"""
市场语境匹配模块

从统一观点库（观点库.jsonl）按 source_type 加载数据，根据当前市场语境（温度、分位、操作建议），
调用 LLM 选出与当前市场状态最匹配的内容：

按 source_type 分组：
- operation（操作记录）：E大近期的买卖操作
- observation（近期判断）：E大近期对市场的判断
- principle（通用原则）：E大的经典投资理念

匹配优先级：observation > operation > principle
用于在决策日报中生成"E大说过"段落。
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from scripts.shared.llm_utils import build_llm_client, call_v4_pro_json

logger = logging.getLogger(__name__)

# 观点匹配 Prompt（统一输入，在调用前已按优先级 filtering）
MATCH_PROMPT_TEMPLATE = """你是一位熟悉"ETF拯救世界"(E大)投资思想的助手。以下是 E大的观点库中与当前市场最相关的内容：

{all_opinions}

【当前市场状态】
- 全市场温度：{temperature_label}（PE分位均值 {avg_percentile}%）
- 建议仓位：A股 {stock_pct}% / 债券 {bond_pct}% / 现金 {cash_pct}%
- ETF操作方向：{action_summary}
- 高估指数：{overvalued_indices}
- 低估指数：{undervalued_indices}

请从中选出 2-3 条与当前市场状态最相关的内容，按重要度输出 JSON 数组：
{{
  "opinion": "内容原文",
  "relevance_reason": "一句话说明为什么适合当前市场（10-20字）",
  "source": "operation / observation / principle"
}}

只返回 JSON 数组，不要包含其他任何文字。"""


def _get_fallback_opinions(config: dict[str, Any]) -> list[dict]:
    """从配置中获取备用观点。"""
    fallback = config.get("opinion_matching", {}).get("fallback_opinions", [])
    if fallback:
        return fallback
    return [
        {
            "opinion": "投资是概率游戏，而非预测游戏。放弃预测涨跌，专注于计算不同情况下的胜率和赔率。",
            "relevance_reason": "当前市场需要理性决策",
        },
        {
            "opinion": "仓位管理是风险控制的核心。不空仓、不满仓，根据估值动态调整。",
            "relevance_reason": "当前估值水平需要调整仓位",
        },
    ]


def load_jsonl(filepath: str) -> list[dict]:
    """从 JSONL 文件加载数据。

    Args:
        filepath: JSONL 文件路径

    Returns:
        list[dict]: 数据列表
    """
    if not os.path.isfile(filepath):
        logger.warning("文件不存在: %s", filepath)
        return []

    records = []
    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                logger.warning("跳过无法解析的行: %s", line[:80])

    logger.info("已加载 %d 条记录从 %s", len(records), os.path.basename(filepath))
    return records


def _filter_recent(records: list[dict], months: int = 3) -> list[dict]:
    """筛选最近 N 个月的记录。

    解析每条记录的 time 字段（ISO 时间戳），与当前时间比较，
    只保留 months 个月内的记录。time 字段缺失或解析失败时默认保留（安全降级）。

    Args:
        records: 记录列表
        months: 最近几个月

    Returns:
        list[dict]: 筛选后的记录
    """
    if not records:
        return []

    from datetime import datetime, timezone, timedelta

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=months * 30)  # 近似：1个月≈30天

    filtered: list[dict] = []
    for r in records:
        time_str = r.get("time") or r.get("processed_at")
        if not time_str:
            # 没有时间字段的默认保留（安全降级）
            filtered.append(r)
            continue
        try:
            if isinstance(time_str, str):
                # 尝试解析 ISO 格式，兼容带时区和不带时区
                if time_str.endswith("Z"):
                    time_str = time_str[:-1] + "+00:00"
                record_time = datetime.fromisoformat(time_str)
                # 确保时区一致
                if record_time.tzinfo is None:
                    record_time = record_time.replace(tzinfo=timezone.utc)
            else:
                # 非字符串类型，默认保留
                filtered.append(r)
                continue
        except (ValueError, TypeError):
            # 解析失败，默认保留（安全降级）
            filtered.append(r)
            continue

        if record_time >= cutoff:
            filtered.append(r)
        else:
            # 超时记录标记
            r["timed_out"] = True
            # 仍放入 filtered（降级为通用匹配使用）
            filtered.append(r)

    logger.info("时间窗口过滤: %d -> %d 条（窗口=%d个月）",
                len(records), len(filtered), months)
    return filtered


def _build_action_summary(etf_recs: dict) -> str:
    """从 ETF 推荐结果中提取操作方向摘要。"""
    sells = etf_recs.get("sell_recommendations", [])
    buys = etf_recs.get("buy_recommendations", [])

    if sells and buys:
        return f"卖出{len(sells)}个品种、买入{len(buys)}个品种"
    elif sells:
        return f"卖出{len(sells)}个品种、无买入建议"
    elif buys:
        return f"买入{len(buys)}个品种、无卖出建议"
    else:
        return "无明确操作建议"


def _build_index_summary(market_data: dict, percentile_threshold: float) -> tuple[str, str]:
    """构建高估/低估指数摘要。"""
    indices = market_data.get("indices", [])
    overvalued = []
    undervalued = []

    for idx in indices:
        name = idx.get("name", "")
        pe_pct = idx.get("pe_percentile")
        if pe_pct is None:
            continue
        if pe_pct > percentile_threshold:
            overvalued.append(f"{name}({pe_pct:.0f}%)")
        elif pe_pct < (100 - percentile_threshold):
            undervalued.append(f"{name}({pe_pct:.0f}%)")

    overvalued_str = "、".join(overvalued) if overvalued else "无"
    undervalued_str = "、".join(undervalued) if undervalued else "无"
    return overvalued_str, undervalued_str


def _format_operations_for_prompt(operations: list[dict]) -> str:
    """将操作记录格式化为易读的文本，用于 prompt。"""
    if not operations:
        return "（暂无近期操作记录）"
    lines = []
    for op in operations[:10]:  # 最多取 10 条
        plan = op.get("plan", "")
        action = op.get("action", "")
        fund = op.get("fund", "")
        code = op.get("code", "")
        yield_pct = op.get("yield_pct", 0)
        memo = op.get("memo", "")
        parts = [f"{plan}计划" if plan else ""]
        parts.append(f"{action}{fund}({code})" if code else f"{action}{fund}")
        if yield_pct and yield_pct > 0:
            parts.append(f"收益率{yield_pct}%")
        if memo:
            parts.append(f"- {memo}")
        lines.append(f"- {' '.join(parts)}")
    return "\n".join(lines)


def _format_observations_for_prompt(observations: list[dict]) -> str:
    """将市场判断格式化为易读的文本，用于 prompt。"""
    if not observations:
        return "（暂无近期市场判断）"
    lines = []
    for obs in observations[:15]:  # 最多取 15 条
        opinion = obs.get("opinion", "")
        if opinion:
            lines.append(f"- {opinion}")
    return "\n".join(lines)


def _format_principles_for_prompt(principles: list[dict], max_chars: int = 2000) -> str:
    """将通用原则格式化为易读文本，用于 prompt。

    Args:
        principles: 通用原则记录列表
        max_chars: 最大字符数，超长截断

    Returns:
        str: 格式化的原则文本
    """
    if not principles:
        return "（暂无通用原则）"
    lines = []
    chars = 0
    for p in principles:
        opinion = p.get("opinion", "")
        if not opinion:
            continue
        if chars + len(opinion) > max_chars:
            lines.append("（原则库过长，已截断）")
            break
        lines.append(f"- {opinion}")
        chars += len(opinion)
    return "\n".join(lines)


def match_opinions(
    config: dict[str, Any],
    temperature: dict,
    position: dict,
    etf_recs: dict,
    market_data: dict,
    opinions_path: str | None = None,
) -> list[dict]:
    """根据当前市场语境匹配 E大历史观点，统一观点库按 source_type 过滤。

    按 source_type 分组后按优先级组织输入：
    observation（近期判断）> operation（操作记录）> principle（通用原则）

    Args:
        config: advisor 配置字典
        temperature: 市场温度计算结果
        position: 仓位建议
        etf_recs: ETF 推荐结果
        market_data: 市场估值数据
        opinions_path: 观点库文件路径，默认从 distill 产物目录读取

    Returns:
        list[dict]: 匹配的观点列表，每条包含 opinion, relevance_reason, source
    """
    # 解析观点库路径
    if opinions_path is None:
        opinions_path = _resolve_opinions_path(config)

    # 从统一观点库加载全部记录
    records = load_jsonl(opinions_path)

    # 按 source_type 分组
    operations = [r for r in records if r.get("source_type") == "operation"]
    observations = [r for r in records if r.get("source_type") == "observation"]
    principles = [r for r in records if r.get("source_type") in ("principle", None, "")]

    # 如果全部为空，使用备用
    if not operations and not observations and not principles:
        logger.warning("观点库数据均为空，使用备用观点")
        return _get_fallback_opinions(config)

    action_summary = _build_action_summary(etf_recs)
    threshold = config.get("valuation", {}).get("thresholds", {}).get("normal", 70)
    overvalued_str, undervalued_str = _build_index_summary(market_data, threshold)

    match_config = config.get("opinion_matching", {})
    max_principles_chars = match_config.get("max_opinions_chars", 8000)

    # 按优先级筛选和格式化三组数据
    ops_text = _format_operations_for_prompt(_filter_recent(operations))
    obs_text = _format_observations_for_prompt(_filter_recent(observations))
    principles_text = _format_principles_for_prompt(principles, max_chars=max_principles_chars)

    # 合并为统一输入（按优先级排列：observation > operation > principle）
    all_opinions_parts = []
    if obs_text.strip() and obs_text != "（暂无近期市场判断）":
        all_opinions_parts.append("【近期判断】\n" + obs_text)
    if ops_text.strip() and ops_text != "（暂无近期操作记录）":
        all_opinions_parts.append("【近期操作】\n" + ops_text)
    if principles_text.strip() and principles_text != "（暂无通用原则）":
        all_opinions_parts.append("【通用原则】\n" + principles_text)
    all_opinions = "\n\n".join(all_opinions_parts) if all_opinions_parts else "（暂无相关观点）"

    prompt = MATCH_PROMPT_TEMPLATE.format(
        all_opinions=all_opinions,
        temperature_label=temperature.get("label", "未知"),
        avg_percentile=temperature.get("avg_percentile", 50),
        stock_pct=position.get("stock", 0),
        bond_pct=position.get("bond", 0),
        cash_pct=position.get("cash", 0),
        action_summary=action_summary,
        overvalued_indices=overvalued_str,
        undervalued_indices=undervalued_str,
    )

    try:
        client = build_llm_client(config)
        api_config = config["api"]

        result = call_v4_pro_json(
            client=client,
            model=api_config.get("model", "deepseek-v4-pro"),
            messages=[
                {"role": "system", "content": "你是一位熟悉E大投资思想的助手。请只输出 JSON 数组。"},
                {"role": "user", "content": prompt},
            ],
            reasoning_effort="high",
            max_tokens=match_config.get("max_tokens", 1500),
            timeout=match_config.get("timeout", 60),
        )

        if isinstance(result, list) and len(result) > 0:
            matched = []
            for item in result:
                if isinstance(item, dict) and item.get("opinion"):
                    matched.append({
                        "opinion": item["opinion"],
                        "relevance_reason": item.get("relevance_reason", ""),
                        "source": item.get("source", "principle"),
                    })
            if matched:
                logger.info("成功匹配 %d 条观点", len(matched))
                return matched

    except Exception as e:
        logger.error("观点匹配失败: %s", e)

    logger.warning("观点匹配未返回有效结果，使用备用观点")
    return _get_fallback_opinions(config)


def format_opinion_section(matched_opinions: list[dict]) -> str:
    """将匹配的观点格式化为日报段落。

    根据 source 字段分组展示：
    - operation → "E大近期操作"
    - observation → "E大近期判断"
    - principle → "E大金句"

    Args:
        matched_opinions: 匹配的观点列表

    Returns:
        str: 格式化的 Markdown 段落
    """
    if not matched_opinions:
        return ""

    lines = [
        "",
        "---",
        "💬 **E大说过**",
        "",
    ]

    for i, op in enumerate(matched_opinions, 1):
        opinion = op.get("opinion", "")
        reason = op.get("relevance_reason", "")

        if reason:
            lines.append(f"> {opinion}")
            lines.append(f"> *——{reason}*")
        else:
            lines.append(f"> {opinion}")
        if i < len(matched_opinions):
            lines.append("")

    return "\n".join(lines)


def _resolve_opinions_path(config: dict[str, Any]) -> str:
    """解析观点库文件路径。"""
    explicit_path = config.get("opinion_matching", {}).get("opinions_path", "")
    if explicit_path:
        return explicit_path

    project_root = os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )
    return os.path.join(project_root, "docs", "distilled", "观点库.jsonl")