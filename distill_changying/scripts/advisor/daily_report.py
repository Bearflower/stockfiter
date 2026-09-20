"""
E大投资决策日报组装模块

统一封装「LLM 主路径 + 规则引擎降级」的日报生成与飞书推送逻辑，
供 scheduler.py（每日定时推送）和 manual_scan.py（手动触发）复用，
消除两处重复的日报组装与推送代码。

数据流：
  市场估值数据 → 温度计算
    ├─ LLM 主路径：call_llm_analysis → 仓位 + ETF + 市场解读
    │     失败时降级到规则引擎（get_position_advice + generate_recommendations）
    ├─ 观点匹配：match_opinions → 「E大说过」段
    ├─ 持仓展示：load_current_positions + format_position_section
    └─ 组装日报文本 → 飞书推送
"""

from __future__ import annotations

import logging
from typing import Any

import requests

from scripts.advisor.advisor_llm import call_llm_analysis
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

logger = logging.getLogger(__name__)


def _format_llm_etf_text(etf_recs: list[dict]) -> str:
    """将 LLM 返回的 ETF 推荐列表格式化为操作建议文本。

    LLM 的 etf_recommendations 是 list[dict]，每条含
    etf_code/etf_name/action/shares/reasoning，与规则引擎的 dict 结构不同。
    """
    lines = ["📈 ETF 操作建议", ""]
    buy_recs = [r for r in etf_recs if r.get("action") == "buy"]
    sell_recs = [r for r in etf_recs if r.get("action") == "sell"]

    if buy_recs:
        lines.append("🟢 买入建议：")
        for r in buy_recs:
            lines.append(
                f"  买入 {r.get('shares', 0)} 份 {r.get('etf_name', '')}"
                f"({r.get('etf_code', '')}) — {r.get('reasoning', '')}"
            )
        lines.append("")

    if sell_recs:
        lines.append("🔴 卖出建议：")
        for r in sell_recs:
            lines.append(
                f"  卖出 {r.get('shares', 0)} 份 {r.get('etf_name', '')}"
                f"({r.get('etf_code', '')}) — {r.get('reasoning', '')}"
            )
        lines.append("")

    return "\n".join(lines)


def _to_match_etf_recs(etf_recs: Any) -> dict[str, list[dict]]:
    """将 ETF 推荐结果统一转为 opinion_matcher 期望的结构。

    LLM 返回 list[dict]（含 action 字段），规则引擎返回
    dict（含 recommendations 列表）。两者都需归一化为
    {"buy_recommendations": [...], "sell_recommendations": [...]}，
    否则 match_opinions 的 _build_action_summary 会读不到操作方向。
    """
    if isinstance(etf_recs, dict):
        recs = etf_recs.get("recommendations", [])
    elif isinstance(etf_recs, list):
        recs = etf_recs
    else:
        recs = []

    return {
        "buy_recommendations": [r for r in recs if r.get("action") == "buy"],
        "sell_recommendations": [r for r in recs if r.get("action") == "sell"],
    }


def build_daily_report(
    config: dict[str, Any],
    market_data: dict[str, Any],
    temperature: dict[str, Any],
) -> str:
    """组装完整日报文本。

    优先走 LLM 主路径（call_llm_analysis 产出仓位 + ETF + 市场解读），
    LLM 失败时降级到规则引擎（get_position_advice + generate_recommendations）。
    两路最后统一追加持仓展示、观点匹配、免责声明。

    Args:
        config: advisor 配置字典（scripts.advisor.config.get_config 的返回值）
        market_data: fetch_all_valuations 的返回值
        temperature: calculate_market_temperature 的返回值

    Returns:
        str: 完整日报 Markdown 文本
    """
    disclaimer = config.get("disclaimer", "以上分析仅供参考，不构成投资建议。")

    # ── 主路径：LLM 综合分析 ──
    llm_analysis = call_llm_analysis(config, temperature)

    if llm_analysis:
        logger.info("使用 LLM 分析结果作为日报主内容")
        pa = llm_analysis["position_advice"]
        etf_recs_llm = llm_analysis["etf_recommendations"]
        commentary = llm_analysis["market_commentary"]

        etf_text = _format_llm_etf_text(etf_recs_llm)

        # 持仓展示
        positions = load_current_positions()
        if positions:
            etf_text = etf_text + "\n" + format_position_section(
                positions, market_data=market_data, config=config
            )

        # LLM 市场解读
        if commentary:
            etf_text += f"\n\n💡 **E大市场解读**\n\n{commentary}"

        # 观点匹配输入（LLM 结构转 match_opinions 期望结构）
        match_etf_recs = _to_match_etf_recs(etf_recs_llm)
        position_for_match = {
            "stock": pa["stock_pct"],
            "bond": pa["bond_pct"],
            "cash": pa["cash_pct"],
            "temperature": pa.get("temperature_label", temperature["label"]),
        }
        head_stock = f"A股 {pa['stock_pct']}%"
        head_bond = f"债券 {pa['bond_pct']}%"
        head_cash = f"现金 {pa['cash_pct']}%"
    else:
        logger.info("LLM 分析不可用，降级到规则引擎")
        position = get_position_advice(temperature["label"], config)
        etf_recs = generate_recommendations(market_data, config)

        etf_text = format_recommendations_text(etf_recs, show_hold=False)

        positions = load_current_positions()
        if positions:
            etf_text = etf_text + "\n" + format_position_section(
                positions, market_data=market_data, config=config
            )

        match_etf_recs = _to_match_etf_recs(etf_recs)
        position_for_match = position
        head_stock = f"A股 {position['stock']}%"
        head_bond = f"债券 {position['bond']}%"
        head_cash = f"现金 {position['cash']}%"

    # 观点匹配（两路共用，失败降级为空段不影响主日报）
    opinion_section = ""
    try:
        matched = match_opinions(
            config, temperature, position_for_match, match_etf_recs, market_data
        )
        opinion_section = format_opinion_section(matched)
    except Exception as e:
        logger.warning("E大观点匹配失败，跳过「E大视角」段: %s", e)

    lines = [
        "🌡️ E大投资决策日报",
        "",
        f"全市场温度：{temperature['label']}（PE分位均值 {temperature['avg_percentile']:.1f}%）",
        f"建议仓位：{head_stock} / {head_bond} / {head_cash}",
        "",
        etf_text,
    ]
    if opinion_section:
        lines.append(opinion_section)
    lines.extend([
        "",
        "---",
        disclaimer,
    ])
    return "\n".join(lines)


def push_feishu_message(
    msg: str,
    webhook: str,
    timeout: int = 10,
) -> None:
    """将日报文本推送到飞书（交互式卡片）。

    Args:
        msg: 日报 Markdown 文本
        webhook: 飞书机器人 Webhook 地址，为空时跳过推送
        timeout: 推送超时秒数
    """
    if not webhook:
        logger.warning("未配置 FEISHU_WEBHOOK，无法推送")
        return

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
        resp = requests.post(webhook, json=payload, timeout=timeout)
        logger.info("飞书推送结果: %s %s", resp.status_code, resp.text[:100])
    except Exception as e:
        logger.error("飞书推送失败: %s", e)
