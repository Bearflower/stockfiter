"""
手动触发估值扫描 + LLM分析 + 飞书推送
用于调试和验证，与 scheduler.py 的主循环解耦
"""
import sys
import os
import logging

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.getenv("PYTHONPATH", ""))
sys.path.insert(0, "/app")
sys.path.insert(0, "/app/distill_changying")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

import yaml

from scripts.advisor.config import get_config
from scripts.advisor.market_data import fetch_all_valuations
from scripts.advisor.temperature import calculate_market_temperature
from scripts.advisor.daily_report import (
    build_daily_report,
    push_feishu_message,
)

logger = logging.getLogger("manual_scan")

# 加载调度器配置（config/config.yaml 位于项目根 stockfilter_v3/，向上 4 级定位）
_project_root = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)
with open(os.path.join(_project_root, "config", "config.yaml")) as f:
    cfg = yaml.safe_load(f)

advisor_cfg = cfg.get("distill_changying", {}).get("advisor", {})
schedule_cfg = advisor_cfg.get("schedule", {})
feishu_timeout = schedule_cfg.get("feishu_timeout", 10)

feishu_webhook = os.environ.get(
    "FEISHU_WEBHOOK_EADVISOR",
    cfg.get("global", {}).get("notification", {}).get("feishu_webhook", ""),
)


def scan_and_push():
    """执行单次估值扫描 + LLM分析 + 飞书推送"""
    logger.info("正在获取指数估值数据...")
    config = get_config()
    market_data = fetch_all_valuations(config, force_refresh=True)

    if market_data["valid_count"] == 0:
        logger.warning("无有效估值数据，跳过推送")
        return

    temperature = calculate_market_temperature(market_data["indices"], config)

    # 组装日报（LLM 主路径 + 规则引擎降级 + 观点匹配 + 持仓展示）
    msg = build_daily_report(config, market_data, temperature)
    logger.info("E大日报内容:\n%s", msg)

    push_feishu_message(msg, feishu_webhook, timeout=feishu_timeout)


if __name__ == "__main__":
    scan_and_push()
