"""
E大估值策略调度器
每天 23:10(北京时间) 估值扫描 + ETF推荐 + 飞书日报推送
完全不依赖 PostgreSQL，仅用 baostock + DeepSeek + 飞书 Webhook
使用明确的 Asia/Shanghai 时区，防止 Docker 容器时区漂移
"""
import os
import sys
import time
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import yaml
import requests

TZ = ZoneInfo("Asia/Shanghai")

sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
)
sys.path.insert(
    0,
    os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "distill_changying"
    ),
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("eadvisor_scheduler")

# 加载配置
with open("config/config.yaml") as f:
    cfg = yaml.safe_load(f)

advisor_config = cfg.get("distill_changying", {}).get("advisor", {})
schedule_config = advisor_config.get("schedule", {})
scan_time = schedule_config.get("scan_time", "23:10")

feishu_webhook = os.environ.get(
    "FEISHU_WEBHOOK_EADVISOR",
    cfg.get("global", {}).get("notification", {}).get("feishu_webhook", ""),
)

logger.info(
    "E大估值策略调度器启动（当前时间: %s）",
    datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S"),
)
logger.info("定时任务：%s 估值扫描 + ETF推荐 + 飞书日报推送（北京时间）", scan_time)
if feishu_webhook:
    logger.info("飞书 Webhook 已配置：%s...", feishu_webhook[:40])
else:
    logger.warning("未配置 FEISHU_WEBHOOK！")

# 当天已触发过
last_run: str = ""


def run_scan_and_push():
    """执行估值扫描并推送到飞书"""
    from scripts.advisor.config import get_config
    from scripts.advisor.market_data import fetch_all_valuations
    from scripts.advisor.temperature import calculate_market_temperature
    from scripts.advisor.position import get_position_advice
    from scripts.advisor.etf_recommend import (
        generate_recommendations,
        format_recommendations_text,
    )

    logger.info("正在获取指数估值数据...")
    config = get_config()
    market_data = fetch_all_valuations(config, force_refresh=True)

    if market_data["valid_count"] == 0:
        logger.warning("无有效估值数据，跳过推送")
        return

    temperature = calculate_market_temperature(market_data["indices"], config)
    position = get_position_advice(temperature["label"], config)
    etf_recs = generate_recommendations(market_data, config)

    lines = [
        "🌡️ E大投资决策日报",
        "",
        f"全市场温度：{temperature['label']}（PE分位均值 {temperature['avg_percentile']:.1f}%）",
        f"建议仓位：A股 {position['stock']}% / 债券 {position['bond']}% / 现金 {position['cash']}%",
        "",
        format_recommendations_text(etf_recs),
        "",
        "---",
        cfg.get("disclaimer", "以上分析仅供参考，不构成投资建议。"),
    ]
    msg = "\n".join(lines)
    logger.info("E大日报内容:\n%s", msg)

    if feishu_webhook:
        payload = {
            "msg_type": "interactive",
            "card": {
                "header": {
                    "title": {"tag": "plain_text", "content": "E大投资决策日报"},
                    "template": "blue",
                },
                "elements": [{"tag": "markdown", "content": msg}],
            },
        }
        try:
            resp = requests.post(feishu_webhook, json=payload, timeout=10)
            logger.info("飞书推送结果: %s %s", resp.status_code, resp.text[:100])
        except Exception as e:
            logger.error("飞书推送失败: %s", e)


logger.info("等待定时触发...")
while True:
    now = datetime.now(TZ)
    today = now.strftime("%Y-%m-%d")
    hm = now.strftime("%H:%M")

    if hm == scan_time and last_run != today:
        last_run = today
        logger.info("=== 执行 E大估值扫描 + 推送（%s 北京）===", scan_time)
        try:
            run_scan_and_push()
        except Exception as e:
            logger.error("E大估值扫描失败: %s", e, exc_info=True)
        time.sleep(90)

    else:
        time.sleep(30)