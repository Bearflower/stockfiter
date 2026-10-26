#!/usr/bin/env bash
# 在 compose 重建容器前，将旧容器的 Docker 日志原样归档到宿主机。
set -euo pipefail

archive_dir="${1:?请提供日志归档目录}"
mkdir -p "$archive_dir"

# 先取一次容器清单；Docker 故障或权限错误必须阻断，不得被误判为无旧容器。
if ! existing_containers="$(docker ps -a --format '{{.Names}}')"; then
    echo "Docker 容器清单查询失败，停止部署" >&2
    exit 1
fi

for container in stockfilter-kline stockfilter-obpc stockfilter-eadvisor; do
    if ! printf '%s\n' "$existing_containers" | grep -Fxq "$container"; then
        continue
    fi
    if ! container_id="$(docker inspect -f '{{.Id}}' "$container")"; then
        echo "docker inspect $container 失败，停止部署" >&2
        exit 1
    fi
    timestamp="$(date -u '+%Y%m%dT%H%M%SZ')"
    archive_path="$archive_dir/${container}-${timestamp}-${container_id}.log.gz"
    if [[ -e "$archive_path" ]]; then
        echo "日志归档已存在：$archive_path" >&2
        exit 1
    fi
    temp_path="$(mktemp "$archive_dir/.${container}.XXXXXX")"
    if ! docker logs --timestamps "$container" 2>&1 | gzip -c > "$temp_path"; then
        rm -f "$temp_path"
        echo "归档 $container 日志失败，停止部署" >&2
        exit 1
    fi
    gzip -t "$temp_path"
    mv "$temp_path" "$archive_path"
    echo "已归档 $container 日志：$archive_path"
done
