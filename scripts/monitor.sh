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
CHECK_INTERVAL=600   # 检查间隔秒数（10 分钟）
KLINE_DATA_MIN=100   # K线数据最少股票数，低于此值告警

# ---- OBPC 告警参数（统一配置区，禁止分散硬编码） ----
SCAN_BLOCKED_WINDOW_DAYS=3   # check_obpc_scan_blocked 查最近 N 天的 failed/blocked 记录
SCAN_BLOCKED_LIMIT=5          # check_obpc_scan_blocked LIMIT N 条
# check_obpc_push_missing 推送时段守卫（HHMM，北京时区，08:10 推送）
# 修复：原先只比较小时（08:00-08:09 被误判为已过推送时间），改为精确到分钟
PUSH_CHECK_HHMM=810

# ---- OBPC 目标交易日（P1-4 修复：按时段区分 initial / rescan 语义） ----
# 初扫触发时间（HHMM，北京时区），须与 scheduler_obpc.py 的 SCHEDULE_SCAN（默认 23:15）保持一致
OBPC_INITIAL_SCAN_HHMM=2315

# 每轮检查前解析一次并写入全局，供所有 OBPC 检查复用（禁止 naive weekday 推算）：
#   OBPC_INITIAL_DATE    = nearest_trading_date(now, inclusive=True) 「当日（收盘后）初扫目标」
#   OBPC_RESCAN_DATE     = nearest_trading_date(now, inclusive=False)「上一交易日（盘前重扫/推送目标）」
#   OBPC_TARGET_DATE     = 按时段从上面二者中选出的「当前应监控」目标：
#                            now >= OBPC_INITIAL_SCAN_HHMM → initial（23:15 初扫已触发，监控当天）
#                            否则                          → rescan（盘前时段监控上一交易日）
#   OBPC_TARGET_DEGRADED = 1 表示所选日期由 weekday 降级推算（Baostock 不可用，节假日可能不准）
# 与 scheduler_obpc.py 的 23:15 初扫 / 07:30 重扫 / 08:10 推送分支同源（utils.trade_calendar）。
OBPC_INITIAL_DATE=""
OBPC_RESCAN_DATE=""
OBPC_TARGET_DATE=""
OBPC_TARGET_DEGRADED="0"

# ---- 日志（先初始化 LOG 相关变量，再定义 log 函数，确保 log 调用不报错） ----
LOG_DIR="/root/stockfilter_v3/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/monitor.log"

# 容器内 Python 同时会输出 Loguru 日志；查询结果必须使用独立协议前缀，
# 禁止再按日期或任意竖线 grep，否则时间戳/日志字段会被误当成业务数据。
MONITOR_RESULT_PREFIX="__OBPC_MONITOR_RESULT__"

extract_monitor_results() {
    sed -n "s/^${MONITOR_RESULT_PREFIX}|//p"
}

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"
}

send_feishu() {
    local webhook="$1"
    local title="$2"
    local content="$3"
    local level="${4:-WARNING}"

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

# ---- 状态追踪（防止重复告警） ----
LAST_KLINE_STATUS="ok"
LAST_OBPC_STATUS="ok"
LAST_EADVISOR_STATUS="ok"
LAST_KLINE_DATA_STATUS="ok"
LAST_KLINE_SYNC_STATUS="ok"
# OBPC 新增告警状态追踪
LAST_OBPC_COVERAGE_STATUS="ok"       # 覆盖率告警
LAST_OBPC_TASK_STATUS="ok"           # task_status 状态异常
LAST_KLINE_RECOVERY_KEY=""           # 超时后自动恢复的一次性告警去重键
LAST_OBPC_SCAN_MISSING="ok"          # scan_runs 缺失
LAST_OBPC_SCAN_STALE="ok"            # running 超时
LAST_OBPC_SCAN_BLOCKED="ok"          # failed / blocked_data
LAST_OBPC_PUSH_MISSING="ok"          # 推送记录缺失
# Task 5 新增：分级市场风控监控
LAST_OBPC_GRADED_CONFIG="ok"         # graded/shadow 模式意外启用（生产应为 hard_filter）
LAST_OBPC_EXTREME_WEAK_BLOCK="ok"     # 极弱市场误放行（extreme_weak + selected > 0）

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
# P1-4 修复：OBPC 目标交易日解析（区分 initial / rescan 语义，禁止 naive weekday）
# =====================================================================
# 每轮检查前调用一次，结果写入全局 OBPC_TARGET_DATE / OBPC_TARGET_DEGRADED
# （以及 OBPC_INITIAL_DATE / OBPC_RESCAN_DATE 供分段监控使用）；
# 所有 OBPC 日期相关检查一律复用该值，不再各自按 weekday 推算（节假日会误判）。
# 解析失败（返回空）时置空全局，调用方按「查询失败」fail-closed 处理。
#
# 日期语义（与 scheduler_obpc.py 一致）：
#   - 23:15 初扫 → signal_date = 当天（收盘后时段）→ inclusive=True 取「最近 <= now 的交易日」
#   - 07:30 重扫 / 08:10 推送 → signal_date = 上一交易日（盘前时段）→ inclusive=False 取「严格早于 now 的交易日」
# 因此按当前北京时间相对 OBPC_INITIAL_SCAN_HHMM 的位置选择应监控的目标：
#   已过 23:15 → 监控当天 initial；未到 23:15 → 监控上一交易日 rescan。
resolve_obpc_target_date() {
    # 显式取北京时区当前时间传入容器，避免依赖容器内部 TZ 导致跨零点日期错位
    local now_iso
    now_iso=$(TZ='Asia/Shanghai' date '+%Y-%m-%d %H:%M:%S')

    local out
    out=$(docker exec stockfilter-kline python3 -c "
from datetime import datetime
from utils.trade_calendar import nearest_trading_date
now = datetime.strptime('$now_iso', '%Y-%m-%d %H:%M:%S')
di, deg_i = nearest_trading_date(now, inclusive=True)
dr, deg_r = nearest_trading_date(now, inclusive=False)
print('{}|{}|{}|{}'.format(di or '', 1 if deg_i else 0, dr or '', 1 if deg_r else 0))
" 2>&1 | grep -E '^([0-9]{4}-[0-9]{2}-[0-9]{2})?\|[01]\|([0-9]{4}-[0-9]{2}-[0-9]{2})?\|[01]$' | tail -1)

    if [ -z "$out" ]; then
        OBPC_INITIAL_DATE=""
        OBPC_RESCAN_DATE=""
        OBPC_TARGET_DATE=""
        OBPC_TARGET_DEGRADED="0"
        log "⚠️ OBPC target 交易日解析失败（交易日历无返回），本轮相关检查按查询失败处理"
        return
    fi

    OBPC_INITIAL_DATE=$(echo "$out" | cut -d'|' -f1)
    local initial_deg=$(echo "$out" | cut -d'|' -f2)
    OBPC_RESCAN_DATE=$(echo "$out" | cut -d'|' -f3)
    local rescan_deg=$(echo "$out" | cut -d'|' -f4)

    # 按时段选择目标：已过 23:15 初扫 → 当天 initial；否则 → 上一交易日 rescan
    local now_hm
    now_hm=$((10#$(TZ='Asia/Shanghai' date '+%H%M')))
    if [ "$now_hm" -ge "$OBPC_INITIAL_SCAN_HHMM" ]; then
        OBPC_TARGET_DATE="$OBPC_INITIAL_DATE"
        OBPC_TARGET_DEGRADED="$initial_deg"
        log "OBPC 监控目标=当日初扫 ${OBPC_TARGET_DATE}（已过 ${OBPC_INITIAL_SCAN_HHMM} 初扫时点）"
    else
        OBPC_TARGET_DATE="$OBPC_RESCAN_DATE"
        OBPC_TARGET_DEGRADED="$rescan_deg"
        log "OBPC 监控目标=上一交易日 ${OBPC_TARGET_DATE}（盘前 rescan/push 时段）"
    fi

    if [ -n "$OBPC_TARGET_DATE" ] && [ "$OBPC_TARGET_DEGRADED" = "1" ]; then
        log "⚠️ OBPC target 交易日 ${OBPC_TARGET_DATE} 为 weekday 降级推算（Baostock 不可用），节假日可能不准"
    fi
}

# =====================================================================
# 2026-10-18 新增：OBPC 扫描可靠性告警（Task 9 Step C）
# =====================================================================

# 告警 1：最近交易日 K 线覆盖率低于配置阈值
check_obpc_coverage() {
    # P1-1：target 统一由交易日历解析；解析失败不得当作「覆盖率正常」（fail-closed）
    if [ -z "$OBPC_TARGET_DATE" ]; then
        if [ "$LAST_OBPC_COVERAGE_STATUS" != "query_failed" ]; then
            LAST_OBPC_COVERAGE_STATUS="query_failed"
            send_feishu "$WEBHOOK" "⚠️ OBPC 覆盖率查询失败" \
                "无法确定目标交易日（交易日历解析失败）\n\nfail-closed，请检查 K 线容器与 Baostock 连通性" \
                "WARNING"
        fi
        return
    fi

    local target_date="$OBPC_TARGET_DATE"
    local result=$(docker exec stockfilter-kline python3 -c "
from data.database import DatabaseManager

def emit_monitor_result(*parts):
    print('__OBPC_MONITOR_RESULT__|' + '|'.join(str(part) for part in parts))

target = '$target_date'

db = DatabaseManager()
cur = db.conn.cursor()

# 股票池规模
cur.execute('SELECT COUNT(*) FROM schema_stockfilter.stocks')
pool_count = cur.fetchone()[0] or 0

# 目标日 K 线覆盖
cur.execute(\"SELECT COUNT(DISTINCT code) FROM schema_stockfilter.klines WHERE date = %s AND frequency = 'd'\", (target,))
covered = cur.fetchone()[0] or 0

rate = covered / pool_count if pool_count > 0 else 0
emit_monitor_result(target, covered, pool_count, f'{rate:.4f}')
db.close()
" 2>&1 | extract_monitor_results | tail -1)

    if [ -z "$result" ]; then
        # 查询失败
        if [ "$LAST_OBPC_COVERAGE_STATUS" != "query_failed" ]; then
            LAST_OBPC_COVERAGE_STATUS="query_failed"
            send_feishu "$WEBHOOK" "⚠️ OBPC 覆盖率查询失败" "无法查询 K 线覆盖率\n\n请检查数据库连接" "WARNING"
        fi
        return
    fi

    local result_date=$(echo "$result" | cut -d'|' -f1)
    local covered=$(echo "$result" | cut -d'|' -f2)
    local pool_count=$(echo "$result" | cut -d'|' -f3)
    local rate=$(echo "$result" | cut -d'|' -f4)

    if [ "$result_date" != "$target_date" ] || ! [[ "$covered" =~ ^[0-9]+$ ]] || \
       ! [[ "$pool_count" =~ ^[0-9]+$ ]] || ! [[ "$rate" =~ ^[0-9]+([.][0-9]+)?$ ]]; then
        if [ "$LAST_OBPC_COVERAGE_STATUS" != "query_failed" ]; then
            LAST_OBPC_COVERAGE_STATUS="query_failed"
            send_feishu "$WEBHOOK" "⚠️ OBPC 覆盖率查询结果非法" \
                "查询返回不符合监控协议（fail-closed）：${result}" \
                "WARNING"
        fi
        return
    fi

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
# 2026-10-22 改造：告警携带分片采集 details（completion_mode / stop_reason /
# 会话重建次数 / 超时分片与卡死股票 / 覆盖率），并在「超时后已恢复」时发送一次性 WARNING。
check_obpc_task_status() {
    # 一次查询两个任务；任何异常都输出 QUERY_FAILED 哨兵，shell 侧强校验两行齐全，
    # 避免「第一条成功、第二条异常」时把不完整结果当作正常状态。
    local raw=$(docker exec stockfilter-kline python3 -c "
import json
from data.database import DatabaseManager

try:
    db = DatabaseManager()
    cur = db.conn.cursor()
except Exception as e:
    print(f'QUERY_FAILED|connect|{type(e).__name__}: {str(e)[:160]}')
    raise SystemExit(0)

try:
    # 查 kline_update 和 backfill（details 缺失时按空字典处理，不伪造字段）
    for task in ('kline_update', 'backfill'):
        cur.execute(
            \"SELECT task_name, status, target_date, details FROM schema_stockfilter.task_status WHERE task_name = %s\",
            (task,),
        )
        row = cur.fetchone()
        if not row:
            print(f'{task}|unknown|None|unknown|-|-|0|-|-')
            continue
        details = row[3]
        if isinstance(details, str):
            try:
                details = json.loads(details)
            except Exception:
                details = {}
        if not isinstance(details, dict):
            details = {}
        mode = details.get('completion_mode') or 'unknown'
        stop = details.get('stop_reason') or '-'
        restarts = details.get('session_restarts')
        restarts = '-' if restarts is None else restarts
        chunks = details.get('timed_out_chunks')
        chunks = chunks if isinstance(chunks, list) else []
        hung = '-'
        if chunks:
            last = chunks[-1] if isinstance(chunks[-1], dict) else {}
            hung = last.get('last_started_code') or '-'
        cov = details.get('coverage_rate')
        cov = '-' if cov is None else cov
        print(f'{row[0]}|{row[1]}|{row[2] or \"None\"}|{mode}|{stop}|{restarts}|{len(chunks)}|{hung}|{cov}')
    db.close()
except Exception as e:
    print(f'QUERY_FAILED|query|{type(e).__name__}: {str(e)[:160]}')
" 2>&1)

    local result=$(printf '%s\n' "$raw" | grep -E '^(kline_update|backfill)\|')
    local query_failed_line=$(printf '%s\n' "$raw" | grep -E '^QUERY_FAILED\|' | head -1)
    local has_kline=0
    local has_backfill=0
    printf '%s\n' "$result" | grep -q '^kline_update|' && has_kline=1
    printf '%s\n' "$result" | grep -q '^backfill|' && has_backfill=1

    local anomaly=0
    local msg_parts=""
    local key=""
    local recovery_mode=""
    local recovery_key=""

    # 查询失败或不完整必须 fail-closed：不得把不完整/空结果当作「任务正常」
    if [ -n "$query_failed_line" ] || [ "$has_kline" != "1" ] || [ "$has_backfill" != "1" ]; then
        if [ "$LAST_OBPC_TASK_STATUS" != "query_failed" ]; then
            LAST_OBPC_TASK_STATUS="query_failed"
            local query_fail_body="无法完整查询 task_status（fail-closed）"
            query_fail_body="${query_fail_body}"$'\n'"哨兵：${query_failed_line:-无}"
            query_fail_body="${query_fail_body}"$'\n'"kline_update 行：${has_kline} / backfill 行：${has_backfill}"
            query_fail_body="${query_fail_body}"$'\n\n'"请检查数据库连接与 kline 容器状态"
            send_feishu "$WEBHOOK" "⚠️ OBPC 数据任务状态查询失败" "$query_fail_body" "WARNING"
        fi
        return
    fi

    while IFS='|' read -r task status target_date mode stop restarts timeouts hung coverage; do
        # 超时后自动恢复：按目标日 + 停止原因 + 重建次数去重，只告警一次
        if [ "$task" = "kline_update" ] && [ "$mode" = "recovered_after_timeout" ]; then
            recovery_mode="recovered"
            recovery_key="recovered|${target_date}|${stop}|${restarts}|${timeouts}"
        fi

        if [ "$status" != "completed" ]; then
            anomaly=1
            msg_parts="${msg_parts}• ${task}: status=${status} target_date=${target_date}
  completion_mode=${mode} stop_reason=${stop}
  会话重建=${restarts} 超时分片=${timeouts} 超时股票=${hung}
  覆盖率=${coverage}
"
            key="${key}_${task}_${status}_${mode}_${stop}"
        fi
    done <<< "$result"

    if [ "$recovery_mode" = "recovered" ]; then
        if [ "$LAST_KLINE_RECOVERY_KEY" != "$recovery_key" ]; then
            LAST_KLINE_RECOVERY_KEY="$recovery_key"
            local recovery_body="采集进程发生超时，已终止旧进程并从数据库缺口续采完成。"
            recovery_body="${recovery_body}"$'\n'"目标交易日：$(echo "$recovery_key" | cut -d'|' -f2)"
            recovery_body="${recovery_body}"$'\n'"停止原因：$(echo "$recovery_key" | cut -d'|' -f3)"
            recovery_body="${recovery_body}"$'\n'"会话重建次数：$(echo "$recovery_key" | cut -d'|' -f4)"
            recovery_body="${recovery_body}"$'\n'"超时分片数：$(echo "$recovery_key" | cut -d'|' -f5)"
            recovery_body="${recovery_body}"$'\n\n'"数据已达标，扫描不受影响；请关注 Baostock 稳定性。"
            send_feishu "$WEBHOOK" "⚠️ K线采集超时后已自动恢复" "$recovery_body" "WARNING"
        fi
    else
        LAST_KLINE_RECOVERY_KEY=""
    fi

    if [ "$anomaly" = "1" ]; then
        local state="anomaly${key}"
        if [ "$LAST_OBPC_TASK_STATUS" != "$state" ]; then
            LAST_OBPC_TASK_STATUS="$state"
            local anomaly_body="以下 task_status 状态异常："$'\n'"${msg_parts}"$'\n'"OBPC 扫描依赖 kline_update + backfill 均完成"
            send_feishu "$WEBHOOK" "⚠️ OBPC 数据任务未完成" "$anomaly_body" "WARNING"
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
    # P1-1：target 统一由交易日历解析；解析失败按查询失败处理（fail-closed，不得当作「无缺失」）
    if [ -z "$OBPC_TARGET_DATE" ]; then
        if [ "$LAST_OBPC_SCAN_MISSING" != "target_unresolved" ]; then
            LAST_OBPC_SCAN_MISSING="target_unresolved"
            send_feishu "$WEBHOOK" "⚠️ OBPC 扫描缺失检查无法确定目标交易日" \
                "交易日历解析失败（OBPC_TARGET_DATE 为空），无法判断 scan_runs 是否缺失。\n\nfail-closed：不得当作正常，请检查 K 线容器与 Baostock 连通性" \
                "WARNING"
        fi
        return
    fi

    local target_date="$OBPC_TARGET_DATE"
    local count=$(docker exec stockfilter-kline python3 -c "
from data.database import DatabaseManager

target = '$target_date'

db = DatabaseManager()
cur = db.conn.cursor()
cur.execute('SELECT COUNT(*) FROM schema_stockfilter.scan_runs WHERE signal_date = %s', (target,))
cnt = cur.fetchone()[0] or 0
print(cnt)
db.close()
" 2>&1 | grep -E '^[0-9]+$' | tail -1)

    if [ -z "$count" ]; then
        # P1-5 修复（fail-closed）：数据库/容器查询失败不得静默放过，
        # 否则监控会把「关键检查无法执行」当作无事发生。
        if [ "$LAST_OBPC_SCAN_MISSING" != "query_failed" ]; then
            LAST_OBPC_SCAN_MISSING="query_failed"
            send_feishu "$WEBHOOK" "⚠️ OBPC scan_runs 缺失检查查询失败" \
                "无法从数据库查询 scan_runs 记录数（fail-closed）。\n\n不得当作\"无缺失\"，请检查 K 线容器与数据库连接。" \
                "WARNING"
        fi
        return
    fi

    if [ "$count" = "0" ]; then
        if [ "$LAST_OBPC_SCAN_MISSING" != "missing_${target_date}" ]; then
            LAST_OBPC_SCAN_MISSING="missing_${target_date}"
            local level="CRITICAL"
            local degraded_note=""
            if [ "$OBPC_TARGET_DEGRADED" = "1" ]; then
                # 降级日期节假日可能不准 → 降为 WARNING，避免误报
                level="WARNING"
                degraded_note="\n\n注意：目标交易日由 weekday 降级推算（Baostock 不可用），节假日可能误判。"
            fi
            send_feishu "$WEBHOOK" "🔴 OBPC scan_runs 缺失" \
                "交易日：${target_date}\nscan_runs 记录数：0\n\n调度器可能未启动扫描，或扫描进程崩溃未收敛${degraded_note}" \
                "$level"
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

def emit_monitor_result(*parts):
    print('__OBPC_MONITOR_RESULT__|' + '|'.join(str(part) for part in parts))

db = None
try:
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
    if rows:
        for r in rows:
            emit_monitor_result(r[0], r[1], r[2])
    else:
        # P2-3：查询成功且无卡死记录 → 输出显式 none 标记，供 shell 区分
        # 「无异常（空结果）」与「查询失败」（二者仅凭空字符串无法区分）
        emit_monitor_result('none', 0, 0)
except Exception as e:
    # P2-3 修复（fail-closed）：查询失败输出哨兵，交由 shell 发去重告警
    emit_monitor_result('query_failed', type(e).__name__)
if db is not None:
    try:
        db.close()
    except Exception:
        pass
" 2>&1 | extract_monitor_results)

    local first_field=$(echo "$result" | cut -d'|' -f1)
    if [ -z "$result" ] || [ "$first_field" = "query_failed" ] || [[ "$result" == *"Traceback"* ]]; then
        # P2-3 修复（fail-closed）：数据库/容器故障不得静默放过、不得清除已有告警状态
        if [ "$LAST_OBPC_SCAN_STALE" != "query_failed" ]; then
            LAST_OBPC_SCAN_STALE="query_failed"
            send_feishu "$WEBHOOK" "⚠️ OBPC 扫描卡死检查查询失败" \
                "无法查询 scan_runs 的 running 状态（fail-closed）。\n\n不得当作\"无卡死\"，请检查 K 线容器与数据库连接。" \
                "WARNING"
        fi
        return
    fi

    if [ "$first_field" != "none" ]; then
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

def emit_monitor_result(*parts):
    print('__OBPC_MONITOR_RESULT__|' + '|'.join(str(part) for part in parts))

db = None
try:
    db = DatabaseManager()
    cur = db.conn.cursor()

    # 每个交易日只看 started_at 最新的一次运行。若晚间 initial 曾 blocked_data，
    # 但次晨 rescan 已成功，旧阻断已收敛，不应继续告警。
    cur.execute('''
        WITH ranked_runs AS (
            SELECT signal_date, run_kind, status, filter_reason,
                   ROW_NUMBER() OVER (
                       PARTITION BY signal_date
                       ORDER BY started_at DESC
                   ) AS run_rank
            FROM schema_stockfilter.scan_runs
            WHERE signal_date >= CURRENT_DATE - ${SCAN_BLOCKED_WINDOW_DAYS}
        )
        SELECT signal_date, run_kind, status, filter_reason
        FROM ranked_runs
        WHERE run_rank = 1
          AND status IN ('failed', 'blocked_data')
        ORDER BY signal_date DESC, run_kind DESC
        LIMIT ${SCAN_BLOCKED_LIMIT}
    ''')
    rows = cur.fetchall()
    if rows:
        for r in rows:
            emit_monitor_result(r[0], r[1], r[2], r[3])
    else:
        # P2-3：查询成功且无异常记录 → 显式 none 标记，区分空结果与查询失败
        emit_monitor_result('none', 0, 0, 0)
except Exception as e:
    # P2-3 修复（fail-closed）：查询失败输出哨兵，交由 shell 发去重告警
    emit_monitor_result('query_failed', type(e).__name__)
if db is not None:
    try:
        db.close()
    except Exception:
        pass
" 2>&1 | extract_monitor_results)

    local first_field=$(echo "$result" | cut -d'|' -f1)
    if [ -z "$result" ] || [ "$first_field" = "query_failed" ] || [[ "$result" == *"Traceback"* ]]; then
        # P2-3 修复（fail-closed）：数据库/容器故障不得静默放过、不得清除已有告警状态
        if [ "$LAST_OBPC_SCAN_BLOCKED" != "query_failed" ]; then
            LAST_OBPC_SCAN_BLOCKED="query_failed"
            send_feishu "$WEBHOOK" "⚠️ OBPC 扫描失败检查查询失败" \
                "无法查询 scan_runs 的 failed / blocked_data 状态（fail-closed）。\n\n不得当作\"无异常\"，请检查 K 线容器与数据库连接。" \
                "WARNING"
        fi
        return
    fi

    if [ "$first_field" != "none" ]; then
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
    # P1-4：推送永远针对「上一交易日」的汇总（08:10 盘前推送），因此固定使用
    # OBPC_RESCAN_DATE（inclusive=False），不随初扫时段切换到当天——当天 initial
    # 在盘中尚未推送。解析失败按查询失败处理（fail-closed）。
    if [ -z "$OBPC_RESCAN_DATE" ]; then
        if [ "$LAST_OBPC_PUSH_MISSING" != "target_unresolved" ]; then
            LAST_OBPC_PUSH_MISSING="target_unresolved"
            send_feishu "$WEBHOOK" "⚠️ OBPC 推送缺失检查无法确定目标交易日" \
                "交易日历解析失败（OBPC_RESCAN_DATE 为空），无法判断推送记录是否缺失。\n\nfail-closed：不得当作正常，请检查 K 线容器与 Baostock 连通性" \
                "WARNING"
        fi
        return
    fi

    local target_date="$OBPC_RESCAN_DATE"
    local result=$(docker exec stockfilter-kline python3 -c "
from data.database import DatabaseManager

def emit_monitor_result(*parts):
    print('__OBPC_MONITOR_RESULT__|' + '|'.join(str(part) for part in parts))

target = '$target_date'

db = None
try:
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

    emit_monitor_result(target, scan_status, push_cnt)
except Exception as e:
    # P1-5 修复（fail-closed）：查询失败输出哨兵值，交由 shell 发去重告警
    emit_monitor_result('query_failed', type(e).__name__)
if db is not None:
    try:
        db.close()
    except Exception:
        pass
" 2>&1 | extract_monitor_results | tail -1)

    local first_field=$(echo "$result" | cut -d'|' -f1)
    if [ -z "$result" ] || [ "$first_field" = "query_failed" ]; then
        # P1-5 修复（fail-closed）：数据库/容器故障不得静默放过
        if [ "$LAST_OBPC_PUSH_MISSING" != "query_failed" ]; then
            LAST_OBPC_PUSH_MISSING="query_failed"
            send_feishu "$WEBHOOK" "⚠️ OBPC 推送缺失检查查询失败" \
                "无法查询 scan_runs / push_history（fail-closed）。\n\n不得当作\"已推送\"，请检查 K 线容器与数据库连接。" \
                "WARNING"
        fi
        return
    fi

    local scan_status=$(echo "$result" | cut -d'|' -f2)
    local push_cnt=$(echo "$result" | cut -d'|' -f3)

    # 有 scan_runs 终态但没推送记录 → 告警
    if [ "$scan_status" != "None" ] && [ "$push_cnt" = "0" ]; then
        # 推送时段（08:10 北京）之前不算缺失；精确到分钟，避免 08:00-08:09 提前误报
        local now_hm
        now_hm=$((10#$(TZ='Asia/Shanghai' date '+%H%M')))
        if [ "$now_hm" -ge "$PUSH_CHECK_HHMM" ]; then
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

# =====================================================================
# Task 5 新增：分级市场风控告警
# =====================================================================

# 告警 7：config.yaml 的 market_regime_policy.mode 意外为 graded/shadow
# 生产环境应保持 hard_filter（保守策略），graded/shadow 需人工确认
check_obpc_graded_config() {
    # 从 config.yaml 读取当前 market_regime_policy.mode
    # P1-6 修复（2026-10-20）：YAML 读取异常 / 字段缺失 / mode 非法时，
    # 不得再兜底伪装成 'hard_filter'（会让「配置不可读」被误判为「模式正常」）。
    # 改为输出哨兵值 'config_unreadable' / 'config_invalid_mode:...'，由下方发去重告警。
    local current_mode=$(python3 -c "
import yaml
try:
    cfg = yaml.safe_load(open('$CONFIG_FILE')) or {}
    mrp = (((cfg.get('strategies') or {}).get('oversold_bounce') or {}).get('params') or {}).get('market_regime_policy') or {}
    mode = mrp.get('mode') if isinstance(mrp, dict) else None
    if mode in ('hard_filter', 'graded', 'shadow'):
        print(mode)
    elif mode is None:
        print('config_unreadable')
    else:
        print('config_invalid_mode:' + repr(mode))
except Exception:
    print('config_unreadable')
" 2>/dev/null)

    if [ -z "$current_mode" ]; then
        # 子进程异常（python 未运行 / 输出为空）→ fail-closed 告警
        if [ "$LAST_OBPC_GRADED_CONFIG" != "config_unreadable" ]; then
            LAST_OBPC_GRADED_CONFIG="config_unreadable"
            send_feishu "$WEBHOOK" "⚠️ OBPC 分级模式配置读取失败" \
                "无法读取 ${CONFIG_FILE} 的 market_regime_policy.mode（fail-closed）。\n\n不得当作 hard_filter 正常，请检查配置文件。" \
                "WARNING"
        fi
        return
    fi

    case "$current_mode" in
        hard_filter)
            if [ "$LAST_OBPC_GRADED_CONFIG" != "ok" ] && [ -n "$LAST_OBPC_GRADED_CONFIG" ]; then
                LAST_OBPC_GRADED_CONFIG="ok"
                send_feishu "$WEBHOOK" "✅ OBPC 分级模式配置恢复正常" \
                    "当前 market_regime_policy.mode=${current_mode}\n\n已确认为 hard_filter 保守模式" \
                    "INFO"
            fi
            ;;
        graded|shadow)
            # 分级模式意外启用 → 高优先级告警
            local state="${current_mode}"
            if [ "$LAST_OBPC_GRADED_CONFIG" != "$state" ]; then
                LAST_OBPC_GRADED_CONFIG="$state"
                send_feishu "$WEBHOOK" "🔴 OBPC 分级市场风控模式已启用" \
                    "当前 market_regime_policy.mode=${current_mode}\n\n生产环境建议保持 hard_filter 保守模式。\n若为灰度测试请忽略本告警；若为误操作请立即关闭。" \
                    "CRITICAL"
            fi
            ;;
        *)
            # config_unreadable / config_invalid_mode:... → fail-closed，不伪装 hard_filter
            if [ "$LAST_OBPC_GRADED_CONFIG" != "$current_mode" ]; then
                LAST_OBPC_GRADED_CONFIG="$current_mode"
                send_feishu "$WEBHOOK" "⚠️ OBPC 分级模式配置无法确认" \
                    "无法从 ${CONFIG_FILE} 确定 market_regime_policy.mode（返回：${current_mode}）。\n\nfail-closed：不得当作 hard_filter 正常，请立即检查配置文件。" \
                    "WARNING"
            fi
            ;;
    esac
}

# 告警 8：极弱市场误放行（data_metrics.market_regime=extreme_weak 但 selected_count > 0）
# 这是风控逻辑穿透的关键信号
check_obpc_extreme_weak_leak() {
    # P1-1：target 统一由交易日历解析；解析失败不得当作「无穿透」（fail-closed）
    if [ -z "$OBPC_TARGET_DATE" ]; then
        if [ "$LAST_OBPC_EXTREME_WEAK_BLOCK" != "query_error" ]; then
            LAST_OBPC_EXTREME_WEAK_BLOCK="query_error"
            send_feishu "$WEBHOOK" "🔴 OBPC 极弱穿透监控无法确定目标交易日" \
                "交易日历解析失败（OBPC_TARGET_DATE 为空，fail-closed）。\n\n不得当作\"无穿透\"，请检查 K 线容器与 Baostock 连通性。" \
                "CRITICAL"
        fi
        return
    fi

    local target_date="$OBPC_TARGET_DATE"
    local result=$(docker exec stockfilter-obpc python3 -c "
import json
from data.database import DatabaseManager

def emit_monitor_result(*parts):
    print('__OBPC_MONITOR_RESULT__|' + '|'.join(str(part) for part in parts))

target = '$target_date'

db = None
try:
    db = DatabaseManager()
    cur = db.conn.cursor()
    # P1-4：优先取「非 running」的最新一行。晚间 23:15 初扫刚启动时，当天最新行是
    # running 且 data_metrics 尚未落库；此时不得误判为穿透/缺失（卡死由 scan_stale 兜底）。
    cur.execute('''
        SELECT data_metrics, selected_count, status
        FROM schema_stockfilter.scan_runs
        WHERE signal_date = %s
        ORDER BY (status = 'running') ASC, started_at DESC
        LIMIT 1
    ''', (target,))
    row = cur.fetchone()
except Exception as e:
    # 修复 issue 8（fail-closed）：查询失败不得被当作'没有穿透'
    emit_monitor_result('query_failed', type(e).__name__)
else:
    leak = 0
    metrics_missing = 0
    leak_info = ''
    if row is None:
        # P1-1 修复（fail-closed）：目标交易日无 scan_runs 运行记录时，
        # 不得输出 0|0 被 shell 当作'无穿透'静默放过；置 metrics_missing=1 走缺失告警。
        metrics_missing = 1
        leak_info = f'目标交易日 {target} 无 scan_runs 运行记录'
    else:
        dm = row[0] or {}
        if isinstance(dm, str):
            try:
                dm = json.loads(dm)
            except Exception:
                dm = {}
        sel = row[1] or 0
        status = row[2] or ''
        if status == 'running' and (not isinstance(dm, dict) or not dm):
            # P1-4：晚间初扫刚启动（running 且指标未落库）暂不判定穿透，
            # 避免把「正在运行」误报为穿透/缺失；卡死场景由 check_obpc_scan_stale 兜底。
            leak_info = '初扫进行中 status=running（指标未落库），本轮暂不判定极弱穿透'
        elif not isinstance(dm, dict) or not dm:
            # 指标缺失：无法确认是否穿透 → fail-closed
            metrics_missing = 1
            leak_info = f'data_metrics 缺失或为空 status={status}'
        else:
            mode = dm.get('policy_mode', '') or ''
            # P2 修复（fail-closed）：policy_mode 必须属于已知集合，否则无法确认
            # 走哪条裁决链（hard_filter / graded / shadow）。修复前非法/缺失 mode
            # 会让后续所有分支都不命中，落到 leak=0、metrics_missing=0，被 shell
            # 当作正常并清除既有告警——与验收脚本「非法或缺失 mode 必须 fatal」不一致。
            if mode not in ('hard_filter', 'graded', 'shadow'):
                metrics_missing = 1
                leak_info = f'policy_mode 非法或缺失（{mode!r}）无法确认裁决链 status={status}'
            # graded：顶层 market_regime；shadow：嵌套 shadow_graded_decision.regime
            if mode == 'shadow':
                sdec = dm.get('shadow_graded_decision') or {}
                regime = sdec.get('regime', '') if isinstance(sdec, dict) else ''
                # P2-2 修复：shadow 是否穿透，必须看影子裁决自身是否放行极弱开仓
                # （shadow_graded_decision.allow_entry），不能拿正式 hard_filter
                # 路径的 selected_count 与影子 regime 组合判断——那既可能误报
                # （正式信号为 0 但影子放行），也可能漏报（影子裁决放行但正式为 0）。
                shadow_allow = sdec.get('allow_entry') if isinstance(sdec, dict) else None
            else:
                regime = dm.get('market_regime', '') or ''
                shadow_allow = None
            if mode in ('graded', 'shadow') and not regime:
                metrics_missing = 1
                leak_info = f'policy_mode={mode} 但 regime 缺失 status={status}'
            elif mode == 'shadow':
                if regime == 'extreme_weak':
                    # P2-2 修复：shadow 是否穿透/是否可信，必须看影子裁决自身
                    # 的 allow_entry，不能拿正式 hard_filter 路径的 selected_count
                    # 与影子 regime 组合判断——那既可能误报（正式信号为 0 但影子放行），
                    # 也可能漏报（影子裁决放行但正式为 0）。
                    # 极弱状态下要求 allow_entry 明确为布尔 False，否则 fail-closed：
                    #   True       → 影子放行极弱开仓（穿透）
                    #   其他值/缺失 → 无法证明硬阻断生效（按指标缺失告警）
                    if shadow_allow is True:
                        leak = 1
                        leak_info = (
                            f'影子裁决 regime=extreme_weak 但 allow_entry=True '
                            f'status={status} mode=shadow'
                        )
                    elif shadow_allow is not False:
                        metrics_missing = 1
                        leak_info = (
                            f'影子裁决 regime=extreme_weak 但 allow_entry='
                            f'{shadow_allow!r} 非布尔值 status={status} mode=shadow'
                        )
            elif regime == 'extreme_weak' and sel > 0:
                leak = 1
                leak_info = f'regime=extreme_weak selected_count={sel} status={status} mode={mode}'
    emit_monitor_result(target, leak, metrics_missing, leak_info)

if db is not None:
    db.close()
" 2>&1 | extract_monitor_results | tail -1)

    # 查询为空/失败：fail-closed，不得静默当作无穿透
    local first_field=$(echo "$result" | cut -d'|' -f1)
    if [ -z "$result" ] || [ "$first_field" = "query_failed" ] || [[ "$result" == *"Traceback"* ]]; then
        if [ "$LAST_OBPC_EXTREME_WEAK_BLOCK" != "query_error" ]; then
            LAST_OBPC_EXTREME_WEAK_BLOCK="query_error"
            send_feishu "$WEBHOOK" "🔴 OBPC 极弱穿透监控查询失败" \
                "无法查询 scan_runs.data_metrics（fail-closed）。\n\n返回：${result}\n\n不得当作\"无穿透\"，请检查 OBPC 容器与数据库连接。" \
                "CRITICAL"
        fi
        return
    fi

    local leak_flag=$(echo "$result" | cut -d'|' -f2)
    local metrics_missing=$(echo "$result" | cut -d'|' -f3)
    local leak_info=$(echo "$result" | cut -d'|' -f4)

    if [ "$leak_flag" = "1" ]; then
        # 极弱市场误放行 → 最高优先级
        if [ "$LAST_OBPC_EXTREME_WEAK_BLOCK" != "leak_${target_date}" ]; then
            LAST_OBPC_EXTREME_WEAK_BLOCK="leak_${target_date}"
            send_feishu "$WEBHOOK" "🔴🔴 OBPC 极弱市场风控穿透" \
                "交易日：${target_date}\n${leak_info}\n\n极弱市场（extreme_weak）禁止开仓，但系统产出了 selected_count > 0 的信号。\n这是风控逻辑 BUG，请立即检查 risk_control.py 的 decide_market_regime 和 daily_scan.py 的 allow_entry 分支。" \
                "CRITICAL"
        fi
    elif [ "$metrics_missing" = "1" ]; then
        # 指标/记录缺失：fail-closed，无法确认是否穿透
        if [ "$LAST_OBPC_EXTREME_WEAK_BLOCK" != "missing_${target_date}" ]; then
            LAST_OBPC_EXTREME_WEAK_BLOCK="missing_${target_date}"
            local level="CRITICAL"
            local degraded_note=""
            if [ "$OBPC_TARGET_DEGRADED" = "1" ]; then
                # 降级日期节假日可能不准 → 降为 WARNING，避免误报
                level="WARNING"
                degraded_note="\n\n注意：目标交易日由 weekday 降级推算（Baostock 不可用），节假日可能误判。"
            fi
            send_feishu "$WEBHOOK" "🔴 OBPC 分级指标缺失" \
                "交易日：${target_date}\n${leak_info}\n\nscan_runs 无当日运行记录或 data_metrics/分级 regime 字段缺失，无法确认极弱穿透状态。\nfail-closed 告警（不得当作无穿透），请检查 daily_scan 落库链路与调度执行。${degraded_note}" \
                "$level"
        fi
    else
        if [ "$LAST_OBPC_EXTREME_WEAK_BLOCK" != "ok" ] && [ -n "$LAST_OBPC_EXTREME_WEAK_BLOCK" ]; then
            LAST_OBPC_EXTREME_WEAK_BLOCK="ok"
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
    # P1-1：先解析本轮 OBPC target 交易日（唯一日期语义），供下方各检查复用
    resolve_obpc_target_date
    check_obpc_coverage
    check_obpc_task_status
    check_obpc_scan_missing
    check_obpc_scan_stale
    check_obpc_scan_blocked
    check_obpc_push_missing
    # Task 5 新增：分级市场风控告警
    check_obpc_graded_config
    check_obpc_extreme_weak_leak

    # E大估值组
    check_container "stockfilter-eadvisor" "$WEBHOOK" "E大估值策略" "LAST_EADVISOR_STATUS"

    log "下次检查：$(date -d "+$CHECK_INTERVAL seconds" '+%H:%M:%S')"
    sleep $CHECK_INTERVAL
done
