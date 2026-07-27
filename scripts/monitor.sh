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

# ---- Webhook 配置 ----
# 所有监控告警统一发到 OBPC 群
WEBHOOK="https://open.feishu.cn/open-apis/bot/v2/hook/955aced6-5b07-42a6-a714-4c5f4726b003"

# ---- 检查参数 ----
CHECK_INTERVAL=600   # 10 分钟
KLINE_DATA_MIN=100   # K线数据最少股票数，低于此值告警

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

    # E大估值组
    check_container "stockfilter-eadvisor" "$WEBHOOK" "E大估值策略" "LAST_EADVISOR_STATUS"

    log "下次检查：$(date -d "+$CHECK_INTERVAL seconds" '+%H:%M:%S')"
    sleep $CHECK_INTERVAL
done