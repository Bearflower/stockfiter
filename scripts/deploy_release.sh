#!/usr/bin/env bash
# GitHub Actions 远端发布入口；失败时不伪报成功，保留回退证据。
set -Eeuo pipefail

revision="${1:?需要完整提交 SHA}"
run_id="${2:?需要 GitHub run ID}"
[[ "$revision" =~ ^[0-9a-f]{40}$ ]] || { echo "提交 SHA 非法" >&2; exit 2; }
[[ "$run_id" =~ ^[0-9]+$ ]] || { echo "run ID 非法" >&2; exit 2; }
IFS= read -r pull_token || { echo "缺少 GHCR token" >&2; exit 2; }
[[ -n "$pull_token" ]] || { echo "GHCR token 为空" >&2; exit 2; }

root="${STOCKFILTER_DEPLOY_ROOT:-/root/stockfilter_v3}"
[[ "$root" == /* && "$root" != / && -d "$root/scripts" ]] || {
    echo "部署根目录必须是已有项目绝对路径" >&2; exit 2;
}
cd "$root"
mkdir -p deploy_logs logs/container-archive
stage="$(mktemp -d "$root/deploy_logs/.stage-${run_id}.XXXXXX")"
state="$stage/monitor-state"
phase="preparing"
base_url="https://raw.githubusercontent.com/Bearflower/stockfiter/$revision"
export DEPLOY_REVISION="$revision" DEPLOY_RUN_ID="$run_id"

on_exit() {
    local status="$1"
    trap - EXIT INT TERM
    if [[ "$status" -eq 0 ]]; then
        return
    fi
    set +e
    if [[ "$phase" == "stopping" || "$phase" == "stopped" ]]; then
        cp "$stage/docker-compose.yml.old" docker-compose.yml
        cp "$stage/config.yaml.old" config/config.yaml
        docker compose start
    fi
    python3 "$stage/monitor_deploy.py" restore "$root/scripts" "$state"
    printf '[%s] PARTIAL_FAILURE commit=%s phase=%s run=%s old_images=%s\n' \
        "$(date '+%F %T')" "$revision" "$phase" "$run_id" "$stage/old-images.tsv" \
        >> "deploy_logs/$(date '+%Y%m%d').log"
    echo "发布未完成；旧镜像 ID 和配置备份保存在 ${stage}，请检查并人工恢复" >&2
}
trap 'on_exit $?' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

for path in docker-compose.yml config/config.yaml scripts/monitor.sh scripts/monitor_deploy.py \
            scripts/archive_container_logs.py scripts/prune_container_archives.py; do
    curl -fsSL --retry 3 "$base_url/$path" -o "$stage/$(basename "$path")"
done
bash -n "$stage/monitor.sh"
python3 "$stage/monitor_deploy.py" validate "$root/scripts" "$state" "$stage/monitor.sh"
cp docker-compose.yml "$stage/docker-compose.yml.old"
cp config/config.yaml "$stage/config.yaml.old"
docker compose -f "$stage/docker-compose.yml" --project-directory "$root" config --quiet

printf '%s' "$pull_token" | docker login ghcr.io -u bearflower --password-stdin
unset pull_token
docker compose -f "$stage/docker-compose.yml" --project-directory "$root" pull

# 当前生产至少应有三个容器；缺失时不做破坏性重建。
: > "$stage/old-images.tsv"
for container in stockfilter-kline stockfilter-obpc stockfilter-eadvisor; do
    old_image="$(docker inspect -f '{{.Image}}' "$container")"
    rollback_tag="${container}:rollback-${run_id}"
    docker tag "$old_image" "$rollback_tag"
    printf '%s\t%s\t%s\n' "$container" "$old_image" "$rollback_tag" >> "$stage/old-images.tsv"
done

python3 "$stage/monitor_deploy.py" pause "$root/scripts" "$state"
phase="stopping"
docker compose stop
phase="stopped"
python3 "$stage/archive_container_logs.py" "$root/logs/container-archive"

cp "$stage/docker-compose.yml" docker-compose.yml
cp "$stage/config.yaml" config/config.yaml
phase="updating"
docker compose up -d --remove-orphans --force-recreate
phase="up"

health_attempts="${DEPLOY_HEALTH_ATTEMPTS:-24}"
health_interval="${DEPLOY_HEALTH_INTERVAL_SECONDS:-5}"
[[ "$health_attempts" =~ ^[1-9][0-9]*$ && "$health_interval" =~ ^[1-9][0-9]*$ ]] || {
    echo "健康检查参数非法" >&2; exit 2;
}
for container in stockfilter-kline stockfilter-obpc stockfilter-eadvisor; do
    healthy=0
    for ((attempt=1; attempt<=health_attempts; attempt++)); do
        status="$(docker inspect -f '{{.State.Health.Status}}' "$container" 2>/dev/null || true)"
        if [[ "$status" == "healthy" ]]; then
            healthy=1
            break
        fi
        sleep "$health_interval"
    done
    [[ "$healthy" == 1 ]] || { echo "${container} 未通过健康检查（${status}）" >&2; exit 1; }
done

python3 "$stage/monitor_deploy.py" activate "$root/scripts" "$state" "$stage/monitor.sh"
python3 "$stage/monitor_deploy.py" verify "$root/scripts" "$state"
# 逐个等待健康可能耗时；最先通过的容器可能在此期间再次失效。
for container in stockfilter-kline stockfilter-obpc stockfilter-eadvisor; do
    status="$(docker inspect -f '{{.State.Health.Status}}' "$container" 2>/dev/null || true)"
    [[ "$status" == healthy ]] || { echo "发布成功前复查失败：${container} 状态=${status}" >&2; exit 1; }
done
# 监控切换和三个容器健康检查均完成后才能宣告成功。
printf '[%s] DEPLOY_SUCCESS commit=%s run=%s\n' "$(date '+%F %T')" "$revision" "$run_id" \
    >> "deploy_logs/$(date '+%Y%m%d').log"

# 后置清理失败不改变已经完成的服务发布结果，但留明显日志。
python3 "$stage/prune_container_archives.py" "$root/logs/container-archive" \
    "${CONTAINER_ARCHIVE_RETENTION_DAYS:-90}" "${CONTAINER_ARCHIVE_MIN_RELEASES:-2}" \
    || echo "WARNING: 归档保留清理失败，请人工检查" >&2
# 旧镜像必须保留以便 PARTIAL_FAILURE 时人工回退；不能在发布末尾 prune。
echo "DEPLOY_SUCCESS commit=$revision run=$run_id"
