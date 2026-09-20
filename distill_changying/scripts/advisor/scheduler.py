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

# 配置来源说明：eadvisor 有两套配置，职责分工如下，互不重叠——
# 1) config/config.yaml（下方 cfg）：调度层参数（scan_time/feishu_timeout）+ 飞书 webhook（global.notification）
# 2) scripts/advisor/config.yaml（run_scan_and_push 内 get_config()）：业务参数（api/估值/llm/etf_pool 等）
# 调度参数唯一来源是 config/config.yaml，业务参数唯一来源是 scripts/advisor/config.yaml。
# config/config.yaml 位于项目根 stockfilter_v3/，向上 4 级定位，避免相对路径依赖 cwd。
_project_root = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)
with open(os.path.join(_project_root, "config", "config.yaml")) as f:
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
    from scripts.advisor.daily_report import (
        build_daily_report,
        push_feishu_message,
    )

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

    push_feishu_message(
        msg, feishu_webhook, timeout=schedule_config.get("feishu_timeout", 10)
    )


def run_knowledge_base_check():
    """每日检测新博客并执行增量蒸馏 + 观点提炼（失败不影响主日报）。

    在估值扫描与推送完成后串行调用：扫描 docs/blog/ 新增文件，
    若有新文件则触发增量蒸馏（摘要 + 深度分析 + 观点库提炼）。
    无新文件时仅刷新 .meta.yaml，开销极小。
    """
    try:
        from scripts.distill.main import run_update
        result = run_update(force_analyze=False)
        logger.info(
            "知识库增量更新完成: 新增 %d 篇，累计 %d/%d 篇",
            result.get("new_count", 0),
            result.get("summarized_count", 0),
            result.get("total_files", 0),
        )
    except Exception as e:
        logger.warning("知识库增量更新失败（不影响主日报推送）: %s", e)


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
        # 每日增量蒸馏 + 观点提炼（串行，失败不影响主日报）
        run_knowledge_base_check()
        time.sleep(90)

    else:
        time.sleep(30)