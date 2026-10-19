#!/bin/bash
# ============================================================
# stockfilter_v3 分离架构监控脚本
# ============================================================
# 部署位置：服务器 /root/stockfilter_v3/scripts/monitor.sh
# 运行方式：nohup bash monitor.sh & （宿主机直接运行）
#
# 监控分组：
#   - 所有告警统一发送到 OBPC 飞书群
#
# 告警策略：仅在状态变化时发送，避免重复告警
# ============================================================

# ---- 环境变量加载 ----
# 先 source 服务器 .env（与 docker-compose 共用同一份），再读 env 兜底
ENV_FILE="/root/stockfilter_v3/.env"
if [ -f "$ENV_FILE" ]; then
    set -a
    # shellcheck disable=SC1090
    source "$ENV_FILE"
    set +a
fi

# ---- Webhook 配置 ----
# 所有监控告警统一发到 OBPC 群。优先读专用变量 FEISHU_WEBHOOK_MONITOR，
# 未设置时回退 FEISHU_WEBHOOK（容器推送用的同一群）
WEBHOOK="${FEISHU_WEBHOOK_MONITOR:-${FEISHU_WEBHOOK:-}}"
if [ -z "$WEBHOOK" ]; then
    echo "❌ 未配置 FEISHU_WEBHOOK_MONITOR 或 FEISHU_WEBHOOK 环境变量，监控告警将失效"
fi

# ---- 检查参数 ----
CHECK_INTERVAL=600   # 10 分钟
KLINE_DATA_MIN=100   # K线数据最少股票数，低于此值告警

# ---- 从 config.yaml 读取配置阈值（禁止硬编码） ----
CONFIG_FILE="/root/stockfilter_v3/config/config.yaml"

# OBPC 覆盖率红线（global.kline_update.min_coverage_rate）
OBPC_COVERAGE_THRESHOLD=$(python3 -c "
import yaml, sys
try:
    cfg = yaml.safe_load(open('$CONFIG_FILE')) or {}
    v = (((cfg.get('global') or {}).get('kline_update') or {}).get('min_coverage_rate', 0.98))
    print(float(v))
except Exception:
    print('0.98')  # 兜底：与 config 默认值一致，非硬编码阈值
" 2>/dev/null)
log "覆盖率阈值从 config 读取：${OBPC_COVERAGE_THRESHOLD}（兜底 0.98）"

# scan_runs running 超时秒数（对应 scheduler_obpc.py 的 SCAN_TIMEOUT）
OBPC_RUNNING_TIMEOUT_SECONDS=${SCAN_TIMEOUT:-3600}

# ---- 日志 ----
LOG_DIR="/root/stockfilter_v3/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/monitor.log"

# ---- 状态追踪（防止重复告警） ----
LAST_KLINE_STATUS="ok"
LAST_OBPC_STATUS="ok"
LAST_EADVISOR_STATUS="ok"
LAST_KLINE_DATA_STATUS="ok"
LAST_KLINE_SYNC_STATUS="ok"
# OBPC 新增告警状态追踪
LAST_OBPC_COVERAGE_STATUS="ok"       # 覆盖率告警
LAST_OBPC_TASK_STATUS="ok"           # task_status 状态异常
LAST_OBPC_SCAN_MISSING="ok"          # scan_runs 缺失
LAST_OBPC_SCAN_STALE="ok"            # running 超时
LAST_OBPC_SCAN_BLOCKED="ok"          # failed / blocked_data
LAST_OBPC_PUSH_MISSING="ok"          # 推送记录缺失

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"
}

send_feishu() {
    local webhook="$1"
    local title="$2"
    local content="$3"
    local level="${4:-WARNING}"

    local msg="${title}\n\n${content}\n\n时间：$(date '+%Y-%m-%d %H:%M:%S')\n服务器：43.156.242.184"

    log "📤 [${level}] ${title}"

    curl -s -X POST "$webhook" \
        -H "Content-Type: application/json" \
        -d "{\"msg_type\":\"text\",\"content\":{\"text\":\"🔔 K线同步监控告警\n告警级别：${level}\n\n${content}\n\n时间：$(date '+%Y-%m-%d %H:%M:%S')\n服务器：43.156.242.184\"}}" \
        > /dev/null 2>&1

    if [ $? -eq 0 ]; then
        log "✅ 飞书告警发送成功"
    else
        log "❌ 飞书告警发送失败"
    fi
}

# 获取容器状态，返回 "running-healthy" / "running-unhealthy" / "stopped" / "missing"
get_container_status() {
    local name="$1"
    local inspect=$(docker inspect "$name" 2>/dev/null)
    if [ -z "$inspect" ]; then
        echo "missing"
        return
    fi
    local state=$(echo "$inspect" | grep -o '"Status": "[^"]*"' | head -1 | cut -d'"' -f4)
    if [ "$state" != "running" ]; then
        echo "stopped"
        return
    fi
    local healthy=$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}no-check{{end}}' "$name" 2>/dev/null)
    if [ "$healthy" = "healthy" ]; then
        echo "running-healthy"
    else
        echo "running-unhealthy"
    fi
}

# 检查容器健康（仅在状态变化时告警）
check_container() {
    local name="$1"
    local webhook="$2"
    local label="$3"
    local last_status_ref="$4"

    local status=$(get_container_status "$name")
    local last_status="${!last_status_ref}"

    if [ "$status" != "$last_status" ]; then
        eval "$last_status_ref='$status'"
        case "$status" in
            "missing")
                send_feishu "$webhook" "🔴 ${label} 容器消失" "容器 ${name} 不存在\n\n请立即检查服务器！" "CRITICAL"
                ;;
            "stopped")
                send_feishu "$webhook" "🔴 ${label} 容器已停止" "容器 ${name} 已停止运行\n\n请立即检查服务器！" "CRITICAL"
                ;;
            "running-unhealthy")
                send_feishu "$webhook" "⚠️ ${label} 容器健康检查失败" "容器 ${name} 正在运行但健康检查未通过\n\n请检查容器日志" "WARNING"
                ;;
            "running-healthy")
                if [ "$last_status" != "ok" ] && [ "$last_status" != "" ]; then
                    send_feishu "$webhook" "✅ ${label} 容器已恢复" "容器 ${name} 已恢复正常运行" "INFO"
                fi
                ;;
        esac
    fi
}

# 检查 K 线数据量（仅在状态变化时告警）
check_kline_data() {
    local count=$(docker exec stockfilter-kline python3 -c "
from data.database import DatabaseManager
db = DatabaseManager()
import pandas as pd
df = pd.read_sql('SELECT COUNT(DISTINCT code) as cnt FROM schema_stockfilter.klines', db.conn)
print(int(df.iloc[0,0]))
db.close()
" 2>&1 | grep -E '^[0-9]+$' | tail -1)

    if [ -z "$count" ]; then
        # 查询失败
        if [ "$LAST_KLINE_DATA_STATUS" != "query_failed" ]; then
            LAST_KLINE_DATA_STATUS="query_failed"
            send_feishu "$WEBHOOK" "⚠️ K线数据查询失败" "无法从数据库查询 K 线数据\n\n请检查数据库连接" "WARNING"
        fi
        return
    fi

    if [ "$count" -lt "$KLINE_DATA_MIN" ]; then
        if [ "$LAST_KLINE_DATA_STATUS" != "low" ]; then
            LAST_KLINE_DATA_STATUS="low"
            send_feishu "$WEBHOOK" "⚠️ K线数据不足" "当前有数据的股票：${count} 只（阈值：${KLINE_DATA_MIN}）\n\n请检查同步任务是否正常运行" "WARNING"
        fi
    else
        if [ "$LAST_KLINE_DATA_STATUS" = "low" ] || [ "$LAST_KLINE_DATA_STATUS" = "query_failed" ]; then
            LAST_KLINE_DATA_STATUS="ok"
            send_feishu "$WEBHOOK" "✅ K线数据已恢复" "当前有数据的股票：${count} 只\n\n数据同步已恢复正常" "INFO"
        else
            LAST_KLINE_DATA_STATUS="ok"
        fi
    fi
}

# 检查 K 线同步任务是否正常运行（task_status 表）
check_kline_sync() {
    local last_sync=$(docker exec stockfilter-kline python3 -c "
from data.database import DatabaseManager
from datetime import datetime, timedelta
db = DatabaseManager()
cur = db.conn.cursor()
cur.execute(\"SELECT last_completed_at FROM schema_stockfilter.task_status WHERE task_name = 'kline_update'\")
row = cur.fetchone()
if row and row[0]:
    print(row[0].strftime('%Y-%m-%d %H:%M:%S'))
else:
    print('never')
db.close()
" 2>&1 | grep -E '^[0-9]{4}-[0-9]{2}-[0-9]{2}|^never$' | tail -1)

    if [ "$last_sync" = "never" ]; then
        if [ "$LAST_KLINE_SYNC_STATUS" != "never" ]; then
            LAST_KLINE_SYNC_STATUS="never"
            send_feishu "$WEBHOOK" "ℹ️ K线同步尚未执行" "task_status 表中无 kline_update 记录\n\n等待首次定时触发" "INFO"
        fi
    elif [ -z "$last_sync" ]; then
        # 查询失败，静默
        :
    else
        LAST_KLINE_SYNC_STATUS="ok"
    fi
}

# =====================================================================
# 2026-10-18 新增：OBPC 扫描可靠性告警（Task 9 Step C）
# =====================================================================

# 告警 1：最近交易日 K 线覆盖率低于配置阈值
check_obpc_coverage() {
    local result=$(docker exec stockfilter-kline python3 -c "
from data.database import DatabaseManager
from datetime import datetime, timedelta

# 最近一个交易日（简化：用今天或昨天）
today = datetime.now()
if today.weekday() >= 5:
    target = (today - timedelta(days=today.weekday() - 4)).strftime('%Y-%m-%d')
else:
    target = today.strftime('%Y-%m-%d')

db = DatabaseManager()
cur = db.conn.cursor()

# 股票池规模
cur.execute(\"SELECT COUNT(*) FROM schema_stockfilter.stocks\")
pool_count = cur.fetchone()[0] or 0

# 目标日 K 线覆盖
cur.execute(\"SELECT COUNT(DISTINCT code) FROM schema_stockfilter.klines WHERE date = %s AND frequency = 'd'\", (target,))
covered = cur.fetchone()[0] or 0

rate = covered / pool_count if pool_count > 0 else 0
print(f'{target} {covered} {pool_count} {rate:.4f}')
db.close()
" 2>&1 | grep -E '^[0-9]{4}-[0-9]{2}-[0-9]{2}' | tail -1)

    if [ -z "$result" ]; then
        # 查询失败
        if [ "$LAST_OBPC_COVERAGE_STATUS" != "query_failed" ]; then
            LAST_OBPC_COVERAGE_STATUS="query_failed"
            send_feishu "$WEBHOOK" "⚠️ OBPC 覆盖率查询失败" "无法查询 K 线覆盖率\n\n请检查数据库连接" "WARNING"
        fi
        return
    fi

    local target_date=$(echo "$result" | awk '{print $1}')
    local covered=$(echo "$result" | awk '{print $2}')
    local pool_count=$(echo "$result" | awk '{print $3}')
    local rate=$(echo "$result" | awk '{print $4}')

    # 覆盖率低于阈值 → 告警
    local threshold="$OBPC_COVERAGE_THRESHOLD"
    local compare=$(python3 -c "print(1 if float('$rate') < float('$threshold') else 0)" 2>/dev/null || echo "0")

    if [ "$compare" = "1" ]; then
        if [ "$LAST_OBPC_COVERAGE_STATUS" != "low" ]; then
            LAST_OBPC_COVERAGE_STATUS="low"
            local rate_pct=$(python3 -c "print(f'{float(\"$rate\")*100:.2f}%')" 2>/dev/null || echo "$rate")
            local threshold_pct=$(python3 -c "print(f'{float(\"$threshold\")*100:.0f}%')" 2>/dev/null || echo "$threshold")
            send_feishu "$WEBHOOK" "🔴 OBPC 数据覆盖率不足" \
                "交易日：${target_date}\nK 线覆盖：${covered} / ${pool_count}\n覆盖率：${rate_pct}（阈值：${threshold_pct}）\n\n数据未达红线，扫描应被阻断" \
                "CRITICAL"
        fi
    else
        if [ "$LAST_OBPC_COVERAGE_STATUS" = "low" ] || [ "$LAST_OBPC_COVERAGE_STATUS" = "query_failed" ]; then
            LAST_OBPC_COVERAGE_STATUS="ok"
            send_feishu "$WEBHOOK" "✅ OBPC 数据覆盖率恢复" \
                "交易日：${target_date}\nK 线覆盖：${covered} / ${pool_count}\n覆盖率已达阈值" \
                "INFO"
        else
            LAST_OBPC_COVERAGE_STATUS="ok"
        fi
    fi
}

# 告警 2：task_status.status != completed
check_obpc_task_status() {
    local result=$(docker exec stockfilter-kline python3 -c "
from data.database import DatabaseManager
db = DatabaseManager()
cur = db.conn.cursor()

# 查 kline_update 和 backfill
for task in ('kline_update', 'backfill'):
    cur.execute(\"SELECT task_name, status FROM schema_stockfilter.task_status WHERE task_name = %s\", (task,))
    row = cur.fetchone()
    if row:
        print(f'{row[0]}|{row[1]}|None')
    else:
        print(f'{task}|unknown|None')
db.close()
" 2>&1 | grep -E '^(kline_update|backfill)\|')

    local anomaly=0
    local msg_parts=""
    local key=""

    while IFS='|' read -r task status target_date; do
        if [ "$status" != "completed" ]; then
            anomaly=1
            msg_parts="${msg_parts}• ${task}: status=${status} target_date=${target_date}
"
            key="${key}_${task}_${status}"
        fi
    done <<< "$result"

    if [ "$anomaly" = "1" ]; then
        local state="anomaly${key}"
        if [ "$LAST_OBPC_TASK_STATUS" != "$state" ]; then
            LAST_OBPC_TASK_STATUS="$state"
            send_feishu "$WEBHOOK" "⚠️ OBPC 数据任务未完成" \
                "以下 task_status 状态异常：\n${msg_parts}\n\nOBPC 扫描依赖 kline_update + backfill 均完成" \
                "WARNING"
        fi
    else
        if [ "$LAST_OBPC_TASK_STATUS" != "ok" ] && [ -n "$LAST_OBPC_TASK_STATUS" ]; then
            LAST_OBPC_TASK_STATUS="ok"
            send_feishu "$WEBHOOK" "✅ OBPC 数据任务状态正常" \
                "kline_update / backfill 均已完成" \
                "INFO"
        fi
    fi
}

# 告警 3：最近交易日没有 scan_runs 记录
check_obpc_scan_missing() {
    local result=$(docker exec stockfilter-kline python3 -c "
from data.database import DatabaseManager
from datetime import datetime, timedelta

# 最近一个交易日（简化）
today = datetime.now()
if today.weekday() >= 5:
    target = (today - timedelta(days=today.weekday() - 4)).strftime('%Y-%m-%d')
else:
    target = today.strftime('%Y-%m-%d')

db = DatabaseManager()
cur = db.conn.cursor()
cur.execute('SELECT COUNT(*) FROM schema_stockfilter.scan_runs WHERE signal_date = %s', (target,))
cnt = cur.fetchone()[0] or 0
print(f'{target}|{cnt}')
db.close()
" 2>&1 | grep '|' | tail -1)

    if [ -z "$result" ]; then
        return
    fi

    local target_date=$(echo "$result" | cut -d'|' -f1)
    local count=$(echo "$result" | cut -d'|' -f2)

    if [ "$count" = "0" ]; then
        if [ "$LAST_OBPC_SCAN_MISSING" != "missing_${target_date}" ]; then
            LAST_OBPC_SCAN_MISSING="missing_${target_date}"
            send_feishu "$WEBHOOK" "🔴 OBPC scan_runs 缺失" \
                "交易日：${target_date}\nscan_runs 记录数：0\n\n调度器可能未启动扫描，或扫描进程崩溃未收敛" \
                "CRITICAL"
        fi
    else
        if [ "$LAST_OBPC_SCAN_MISSING" != "ok" ] && [ -n "$LAST_OBPC_SCAN_MISSING" ]; then
            LAST_OBPC_SCAN_MISSING="ok"
        fi
    fi
}

# 告警 4：scan_runs 长时间停留在 running（超过 SCAN_TIMEOUT）
check_obpc_scan_stale() {
    local timeout="$OBPC_RUNNING_TIMEOUT_SECONDS"

    local result=$(docker exec stockfilter-kline python3 -c "
from data.database import DatabaseManager
db = DatabaseManager()
cur = db.conn.cursor()
cur.execute(\"\"\"
    SELECT signal_date, run_kind, started_at
    FROM schema_stockfilter.scan_runs
    WHERE status = 'running'
      AND started_at < NOW() - (%s::text || ' seconds')::interval
    ORDER BY started_at ASC
\"\"\", ('$timeout',))
rows = cur.fetchall()
for r in rows:
    print(f'{r[0]}|{r[1]}|{r[2]}')
db.close()
" 2>&1 | grep '|')

    if [ -n "$result" ]; then
        local key=$(echo "$result" | head -1 | cut -d'|' -f1)
        if [ "$LAST_OBPC_SCAN_STALE" != "stale_${key}" ]; then
            LAST_OBPC_SCAN_STALE="stale_${key}"
            send_feishu "$WEBHOOK" "🔴 OBPC 扫描进程卡死" \
                "以下 scan_runs 长时间停留在 running：\n${result}\n\n预计调度器已触发 mark_stale_scan_runs_failed 收敛，请检查" \
                "CRITICAL"
        fi
    else
        if [ "$LAST_OBPC_SCAN_STALE" != "ok" ] && [ -n "$LAST_OBPC_SCAN_STALE" ]; then
            LAST_OBPC_SCAN_STALE="ok"
        fi
    fi
}

# 告警 5：扫描失败（failed）或数据阻断（blocked_data）
check_obpc_scan_blocked() {
    local result=$(docker exec stockfilter-kline python3 -c "
from data.database import DatabaseManager
db = DatabaseManager()
cur = db.conn.cursor()

# 查最近 3 天的 failed / blocked_data 记录
cur.execute('''
    SELECT signal_date, run_kind, status, filter_reason
    FROM schema_stockfilter.scan_runs
    WHERE status IN (\"failed\", \"blocked_data\")
      AND signal_date >= CURRENT_DATE - 3
    ORDER BY signal_date DESC, run_kind DESC
    LIMIT 5
''')
rows = cur.fetchall()
for r in rows:
    print(f'{r[0]}|{r[1]}|{r[2]}|{r[3]}')
db.close()
" 2>&1 | grep '|')

    if [ -n "$result" ]; then
        # 用最新一条的 signal_date 作 key（避免每次内容微变导致重复告警）
        local latest_date=$(echo "$result" | head -1 | cut -d'|' -f1)
        if [ "$LAST_OBPC_SCAN_BLOCKED" != "blocked_${latest_date}" ]; then
            LAST_OBPC_SCAN_BLOCKED="blocked_${latest_date}"
            send_feishu "$WEBHOOK" "🔴 OBPC 扫描失败或数据阻断" \
                "最近 3 天内以下 scan_runs 状态异常：\n${result}\n\n请检查调度器日志与数据完整性" \
                "CRITICAL"
        fi
    else
        if [ "$LAST_OBPC_SCAN_BLOCKED" != "ok" ] && [ -n "$LAST_OBPC_SCAN_BLOCKED" ]; then
            LAST_OBPC_SCAN_BLOCKED="ok"
        fi
    fi
}

# 告警 6：推送记录缺失（scan_runs 有终态但 push_history 无对应汇总推送）
check_obpc_push_missing() {
    local result=$(docker exec stockfilter-kline python3 -c "
from data.database import DatabaseManager
from datetime import datetime, timedelta

# 最近一个交易日（简化）
today = datetime.now()
if today.weekday() >= 5:
    target = (today - timedelta(days=today.weekday() - 4)).strftime('%Y-%m-%d')
else:
    target = today.strftime('%Y-%m-%d')

db = DatabaseManager()
cur = db.conn.cursor()

# 查是否有非 running 的终态 scan_runs
cur.execute('''
    SELECT status FROM schema_stockfilter.scan_runs
    WHERE signal_date = %s AND status IN (
        'completed_with_signals','completed_zero','blocked_market','blocked_data','failed'
    )
    ORDER BY started_at DESC LIMIT 1
''', (target,))
scan_status = None
row = cur.fetchone()
if row:
    scan_status = row[0]

# 查推送记录（obpc_summary 类型的汇总推送）
cur.execute('''
    SELECT COUNT(*) FROM schema_stockfilter.push_history
    WHERE code = '__daily_summary__' AND push_date = %s AND push_type = 'obpc_summary'
''', (target,))
push_cnt = cur.fetchone()[0] or 0

print(f'{target}|{scan_status}|{push_cnt}')
db.close()
" 2>&1 | grep '|' | tail -1)

    if [ -z "$result" ]; then
        return
    fi

    local target_date=$(echo "$result" | cut -d'|' -f1)
    local scan_status=$(echo "$result" | cut -d'|' -f2)
    local push_cnt=$(echo "$result" | cut -d'|' -f3)

    # 有 scan_runs 终态但没推送记录 → 告警
    if [ "$scan_status" != "None" ] && [ "$push_cnt" = "0" ]; then
        # 推送时段（08:10 北京）之前不算缺失
        local now_hour=$(TZ='Asia/Shanghai' date +%H)
        if [ "$now_hour" -ge 8 ]; then
            if [ "$LAST_OBPC_PUSH_MISSING" != "missing_${target_date}" ]; then
                LAST_OBPC_PUSH_MISSING="missing_${target_date}"
                send_feishu "$WEBHOOK" "⚠️ OBPC 汇总推送缺失" \
                    "交易日：${target_date}\nscan_runs 状态：${scan_status}\npush_history 记录：0\n\n已过推送时段，请检查 feishu_push.py 执行情况" \
                    "WARNING"
            fi
        fi
    else
        if [ "$LAST_OBPC_PUSH_MISSING" != "ok" ] && [ -n "$LAST_OBPC_PUSH_MISSING" ]; then
            LAST_OBPC_PUSH_MISSING="ok"
        fi
    fi
}

# ==================== 主程序 ====================
log "=========================================="
log "stockfilter_v3 分离架构监控启动"
log "检查间隔：$((CHECK_INTERVAL / 60)) 分钟"
log "Webhook：${WEBHOOK:0:40}..."
log "=========================================="

# 启动通知
send_feishu "$WEBHOOK" "🔔 监控服务已启动" "stockfilter_v3 监控已就绪（K线 + OBPC + E大估值）\n\n检查间隔：$((CHECK_INTERVAL / 60)) 分钟\n\n仅在状态异常时发送告警" "INFO"

sleep 10

# 初始化状态
LAST_KLINE_STATUS=$(get_container_status "stockfilter-kline")
LAST_OBPC_STATUS=$(get_container_status "stockfilter-obpc")
LAST_EADVISOR_STATUS=$(get_container_status "stockfilter-eadvisor")
log "初始状态: kline=$LAST_KLINE_STATUS obpc=$LAST_OBPC_STATUS eadvisor=$LAST_EADVISOR_STATUS"

# 主循环
while true; do
    log "--- 第 $(date '+%H:%M') 轮检查 ---"

    # OBPC 组
    check_container "stockfilter-kline" "$WEBHOOK" "K线服务" "LAST_KLINE_STATUS"
    check_container "stockfilter-obpc" "$WEBHOOK" "OBPC策略" "LAST_OBPC_STATUS"
    check_kline_data
    check_kline_sync
    # 2026-10-18 新增：OBPC 扫描可靠性告警（Task 9 Step C）
    check_obpc_coverage
    check_obpc_task_status
    check_obpc_scan_missing
    check_obpc_scan_stale
    check_obpc_scan_blocked
    check_obpc_push_missing

    # E大估值组
    check_container "stockfilter-eadvisor" "$WEBHOOK" "E大估值策略" "LAST_EADVISOR_STATUS"

    log "下次检查：$(date -d "+$CHECK_INTERVAL seconds" '+%H:%M:%S')"
    sleep $CHECK_INTERVAL
done