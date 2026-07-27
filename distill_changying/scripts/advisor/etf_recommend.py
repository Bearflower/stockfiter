"""
ETF 操作推荐引擎

基于各 ETF 关联指数的 PE 分位，自动生成具体的买卖操作建议。
遵循 E大 150 份资产配置框架，以"份"为单位输出操作清单。

所有参数从 config.yaml 读取，禁止硬编码。
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def generate_recommendations(
    market_data: dict,
    config: dict,
) -> dict[str, Any]:
    """基于各 ETF 关联指数的 PE 分位，生成操作建议。

    对每个 ETF 品种：
    - 查找其关联指数在 market_data 中的 PE 分位
    - 与配置的买卖区间对比，判断买入/卖出/持有
    - 应用份数约束（不超过配置上限）
    - 按优先级排序（买入区 > 卖出区 > 持有区，同区间按分位偏离排序）

    Args:
        market_data: fetch_all_valuations 的返回值，含 indices 列表
        config: 完整配置字典，含 etf_pool、allocation

    Returns:
        dict: {
            "recommendations": [...],   # 每个 ETF 的操作建议
            "summary": {                # 汇总统计
                "buy_count": int,
                "sell_count": int,
                "hold_count": int,
                "total_buy_shares": int,
                "total_sell_shares": int,
            }
        }
    """
    etf_pool: list[dict] = config.get("etf_pool", [])
    if not etf_pool:
        logger.warning("etf_pool 配置为空，无法生成 ETF 操作建议")
        return _empty_result()

    indices = market_data.get("indices", [])

    # 构建指数代码 → 估值数据的查找表
    index_lookup: dict[str, dict] = {}
    for idx in indices:
        code = idx.get("code", "")
        if code:
            index_lookup[code] = idx

    recommendations: list[dict] = []
    buy_count = 0
    sell_count = 0
    hold_count = 0
    total_buy_shares = 0
    total_sell_shares = 0

    for etf in etf_pool:
        rec = _evaluate_etf(etf, index_lookup)
        recommendations.append(rec)

        action = rec["action"]
        if action == "buy":
            buy_count += 1
            total_buy_shares += rec["shares"]
        elif action == "sell":
            sell_count += 1
            total_sell_shares += rec["shares"]
        else:
            hold_count += 1

    # 排序：买入区 > 卖出区 > 持有区；同区间内按分位偏离程度排序
    _sort_recommendations(recommendations)

    logger.info(
        "ETF 推荐引擎完成: 买入 %d 个(%d 份), 卖出 %d 个(%d 份), 持有 %d 个",
        buy_count, total_buy_shares, sell_count, total_sell_shares, hold_count,
    )

    return {
        "recommendations": recommendations,
        "summary": {
            "buy_count": buy_count,
            "sell_count": sell_count,
            "hold_count": hold_count,
            "total_buy_shares": total_buy_shares,
            "total_sell_shares": total_sell_shares,
        },
    }


def _evaluate_etf(
    etf: dict,
    index_lookup: dict[str, dict],
) -> dict[str, Any]:
    """评估单个 ETF 品种的操作建议。

    Args:
        etf: ETF 配置项（从 etf_pool 中取出）
        index_lookup: 指数代码 → 估值数据的映射表

    Returns:
        dict: 操作建议，包含 action、shares、reason 等字段
    """
    etf_code = etf.get("code", "")
    etf_name = etf.get("name", "")
    index_code = etf.get("index", "")
    category = etf.get("category", "")
    buy_zone = etf.get("buy_zone", {})
    sell_zone = etf.get("sell_zone", {})
    shares_per_trade = etf.get("shares_per_trade", 1)
    max_shares = etf.get("max_shares", 10)

    # 查找关联指数的估值数据
    index_data = index_lookup.get(index_code)

    if index_data is None or not index_data.get("valid"):
        return {
            "etf_code": etf_code,
            "etf_name": etf_name,
            "index_code": index_code,
            "index_name": "--",
            "pe_percentile": None,
            "category": category,
            "action": "hold",
            "shares": 0,
            "reason": f"关联指数 {index_code} 无有效估值数据，暂不操作",
            "priority": 0,
        }

    index_name = index_data.get("name", index_code)
    pe_percentile = index_data.get("pe_percentile")

    if pe_percentile is None:
        return {
            "etf_code": etf_code,
            "etf_name": etf_name,
            "index_code": index_code,
            "index_name": index_name,
            "pe_percentile": None,
            "category": category,
            "action": "hold",
            "shares": 0,
            "reason": f"关联指数 {index_name} 无 PE 分位数据，暂不操作",
            "priority": 0,
        }

    # 判断买卖区间
    buy_max = buy_zone.get("pe_percentile_max", 30)
    sell_min = sell_zone.get("pe_percentile_min", 70)

    if pe_percentile <= buy_max:
        # 买入区间
        action = "buy"
        shares = shares_per_trade
        reason = (
            f"PE 分位 {pe_percentile:.1f}%，处于买入区间(≤{buy_max}%)，"
            f"建议买入 {shares} 份 {etf_name}({etf_code})"
        )
        priority = buy_max - pe_percentile  # 分位越低优先级越高
    elif pe_percentile >= sell_min:
        # 卖出区间
        action = "sell"
        shares = shares_per_trade
        reason = (
            f"PE 分位 {pe_percentile:.1f}%，处于卖出区间(≥{sell_min}%)，"
            f"建议卖出 {shares} 份 {etf_name}({etf_code})"
        )
        priority = pe_percentile - sell_min  # 分位越高优先级越高（但卖出优先级低于买入）
    else:
        # 持有区间
        action = "hold"
        shares = 0
        reason = (
            f"PE 分位 {pe_percentile:.1f}%，处于持有区间({buy_max}%~{sell_min}%)，"
            f"继续持有 {etf_name}({etf_code})"
        )
        priority = 0

    logger.debug(
        "ETF 评估: %s(%s) → PE分位 %.1f%% → %s %d份",
        etf_name, etf_code, pe_percentile, action, shares,
    )

    return {
        "etf_code": etf_code,
        "etf_name": etf_name,
        "index_code": index_code,
        "index_name": index_name,
        "pe_percentile": pe_percentile,
        "category": category,
        "action": action,
        "shares": shares,
        "reason": reason,
        "priority": priority,
    }


def _sort_recommendations(recommendations: list[dict]) -> None:
    """原地排序推荐列表：买入 > 卖出 > 持有；同动作内按 priority 降序。

    买入区 priority 为正（分位越低值越大），卖出区 priority 为正（分位越高值越大）。
    排序规则：
    1. 买入排最前（按 priority 降序，即分位最低先买）
    2. 卖出其次（按 priority 降序，即分位最高先卖）
    3. 持有排最后

    Args:
        recommendations: 推荐列表，原地排序
    """
    action_order = {"buy": 0, "sell": 1, "hold": 2}

    recommendations.sort(
        key=lambda r: (action_order.get(r["action"], 2), -(r.get("priority", 0))),
    )


def _empty_result() -> dict[str, Any]:
    """返回空的推荐结果。"""
    return {
        "recommendations": [],
        "summary": {
            "buy_count": 0,
            "sell_count": 0,
            "hold_count": 0,
            "total_buy_shares": 0,
            "total_sell_shares": 0,
        },
    }


def format_recommendations_text(
    recommendations: dict,
    show_hold: bool = True,
) -> str:
    """将 ETF 推荐结果格式化为可读文本（用于飞书推送/报告）。

    Args:
        recommendations: generate_recommendations 的返回值
        show_hold: 是否显示"继续持有"部分。设为 False 时只显示买卖建议，
                   由外部持仓展示模块负责展示持有清单。

    Returns:
        str: 格式化的文本
    """
    recs = recommendations.get("recommendations", [])
    summary = recommendations.get("summary", {})

    if not recs:
        return "（无 ETF 操作建议，etf_pool 配置为空或全部指数无有效数据）"

    lines: list[str] = []
    lines.append("📈 ETF 操作建议")
    lines.append("")

    # 买入建议
    buy_recs = [r for r in recs if r["action"] == "buy"]
    if buy_recs:
        lines.append("🟢 买入建议：")
        for r in buy_recs:
            pe_str = f"{r['pe_percentile']:.1f}%" if r["pe_percentile"] is not None else "--"
            lines.append(
                f"  买入 {r['shares']} 份 {r['etf_name']}({r['etf_code']})"
                f" — 关联{r['index_name']} PE 分位 {pe_str}"
            )
        lines.append("")

    # 卖出建议
    sell_recs = [r for r in recs if r["action"] == "sell"]
    if sell_recs:
        lines.append("🔴 卖出建议：")
        for r in sell_recs:
            pe_str = f"{r['pe_percentile']:.1f}%" if r["pe_percentile"] is not None else "--"
            lines.append(
                f"  卖出 {r['shares']} 份 {r['etf_name']}({r['etf_code']})"
                f" — 关联{r['index_name']} PE 分位 {pe_str}"
            )
        lines.append("")

    # 持有（仅在 show_hold=True 时显示）
    if show_hold:
        hold_recs = [r for r in recs if r["action"] == "hold"]
        if hold_recs:
            lines.append("⚪ 继续持有：")
            for r in hold_recs:
                pe_str = f"{r['pe_percentile']:.1f}%" if r["pe_percentile"] is not None else "--"
                lines.append(
                    f"  {r['etf_name']}({r['etf_code']})"
                    f" — {r['index_name']} PE 分位 {pe_str}"
                )
            lines.append("")

    # 汇总
    lines.append(
        f"合计：买入 {summary['buy_count']} 个({summary['total_buy_shares']} 份)"
        f" / 卖出 {summary['sell_count']} 个({summary['total_sell_shares']} 份)"
        f" / 持有 {summary['hold_count']} 个"
    )

    return "\n".join(lines)