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

from scripts.advisor.config import get_config
from scripts.advisor.market_data import fetch_all_valuations
from scripts.advisor.temperature import calculate_market_temperature
from scripts.advisor.position import get_position_advice
from scripts.advisor.etf_recommend import (
    generate_recommendations,
    format_recommendations_text,
)
from scripts.advisor.position_display import (
    load_current_positions,
    format_position_section,
)
from scripts.advisor.opinion_matcher import (
    match_opinions,
    format_opinion_section,
)
from scripts.advisor.advisor_llm import call_llm_analysis

import yaml
import requests
from zoneinfo import ZoneInfo
from datetime import datetime

TZ = ZoneInfo("Asia/Shanghai")
logger = logging.getLogger("manual_scan")

# 加载调度器配置（飞书 webhook、免责声明等）
cwd = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
cfg_path = os.path.join(cwd, "config", "config.yaml")
with open(cfg_path) as f:
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

    # ── 主路径：LLM 综合分析 ──
    llm_analysis = call_llm_analysis(config, temperature)

    if llm_analysis:
        logger.info("使用 LLM 分析结果作为主内容")
        pa = llm_analysis["position_advice"]
        etf_recs_llm = llm_analysis["etf_recommendations"]
        commentary = llm_analysis["market_commentary"]

        # 构建 ETF 操作文本
        etf_lines = ["📈 ETF 操作建议", ""]
        buy_recs = [r for r in etf_recs_llm if r["action"] == "buy"]
        sell_recs = [r for r in etf_recs_llm if r["action"] == "sell"]

        if buy_recs:
            etf_lines.append("🟢 买入建议：")
            for r in buy_recs:
                etf_lines.append(
                    f"  买入 {r['shares']} 份 {r['etf_name']}({r['etf_code']})"
                    f" — {r['reasoning']}"
                )
            etf_lines.append("")

        if sell_recs:
            etf_lines.append("🔴 卖出建议：")
            for r in sell_recs:
                etf_lines.append(
                    f"  卖出 {r['shares']} 份 {r['etf_name']}({r['etf_code']})"
                    f" — {r['reasoning']}"
                )
            etf_lines.append("")

        etf_text = "\n".join(etf_lines)

        # 持仓展示
        positions = load_current_positions()
        if positions:
            position_section = format_position_section(positions, market_data=market_data, config=config)
            etf_text = etf_text + "\n" + position_section

        # LLM 市场解读
        if commentary:
            etf_text += f"\n\n💡 **E大市场解读**\n\n{commentary}"

        # "E大说过"
        etf_recs_for_match: dict = {
            "buy_recommendations": [r for r in etf_recs_llm if r["action"] == "buy"],
            "sell_recommendations": [r for r in etf_recs_llm if r["action"] == "sell"],
        }
        position_for_match = {
            "stock": pa["stock_pct"],
            "bond": pa["bond_pct"],
            "cash": pa["cash_pct"],
            "temperature": pa.get("temperature_label", temperature["label"]),
        }
        matched_opinions = match_opinions(config, temperature, position_for_match, etf_recs_for_match, market_data)
        if matched_opinions:
            opinion_section = format_opinion_section(matched_opinions)
            etf_text = etf_text + "\n" + opinion_section

        stock_str = f"{pa['stock_pct']}%"
        bond_str = f"{pa['bond_pct']}%"
        cash_str = f"{pa['cash_pct']}%"
        head_stock = f"A股 {stock_str}"
        head_bond = f"债券 {bond_str}"
        head_cash = f"现金 {cash_str}"

    else:
        # ── 降级路径：规则引擎 ──
        logger.info("LLM 分析不可用，降级到规则引擎")
        position = get_position_advice(temperature["label"], config)
        etf_recs = generate_recommendations(market_data, config)

        etf_text = format_recommendations_text(etf_recs, show_hold=False)
        positions = load_current_positions()
        if positions:
            position_section = format_position_section(positions, market_data=market_data, config=config)
            etf_text = etf_text + "\n" + position_section

        matched_opinions = match_opinions(config, temperature, position, etf_recs, market_data)
        if matched_opinions:
            opinion_section = format_opinion_section(matched_opinions)
            etf_text = etf_text + "\n" + opinion_section

        stock_str = f"{position['stock']}%"
        bond_str = f"{position['bond']}%"
        cash_str = f"{position['cash']}%"
        head_stock = f"A股 {stock_str}"
        head_bond = f"债券 {bond_str}"
        head_cash = f"现金 {cash_str}"

    lines = [
        "🌡️ E大投资决策日报",
        "",
        f"全市场温度：{temperature['label']}（PE分位均值 {temperature['avg_percentile']:.1f}%）",
        f"建议仓位：{head_stock} / {head_bond} / {head_cash}",
        "",
        etf_text,
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
            resp = requests.post(feishu_webhook, json=payload, timeout=feishu_timeout)
            logger.info("飞书推送结果: %s %s", resp.status_code, resp.text[:200])
        except Exception as e:
            logger.error("飞书推送失败: %s", e)
    else:
        logger.warning("未配置 FEISHU_WEBHOOK，无法推送")


if __name__ == "__main__":
    scan_and_push()