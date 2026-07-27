"""
人物画像可视化辅助模块

基于结构化索引中的操作时间线、近期判断库、观点库数据，生成文本格式的图表。
所有输出为 Markdown 格式，直接可读，纯数据驱动，不调用 LLM。

功能清单:
  - operation_heatmap: 操作频率年度热力图
  - fund_ranking_table: 品种买卖排名表
  - yield_histogram: 收益率分布文本直方图
  - topic_tagcloud: 判断主题标签云
  - generate_all_viz: 一键生成所有可视化图表
  - generate_viz_report: 从蒸馏产物目录一键加载数据并生成可视化报告

Usage:
    from scripts.distill.persona_viz import generate_all_viz, generate_viz_report

    # 方式一：传入内存数据
    report = generate_all_viz(operations, observations, principles)
    print(report)

    # 方式二：从蒸馏产物目录加载
    report = generate_viz_report("docs/distilled")
    print(report)
"""

from __future__ import annotations

import logging
import os
from collections import defaultdict
from typing import Any

from scripts.distill.config import get_config

logger = logging.getLogger(__name__)

# ============================================================
# 默认可视化配置（可通过 config.yaml 中 viz 节覆盖）
# ============================================================

_DEFAULT_VIZ_CONFIG: dict[str, Any] = {
    "fund_rankings": {
        "top_k": 10,                     # 品种买卖排名取 Top K
    },
    "heatmap": {
        "max_bar_len": 30,               # 热力条最大字符数
    },
    "histogram": {
        "max_bar_len": 40,               # 分布图最大字符数
    },
    "tagcloud": {
        "max_bar_len": 50,               # 标签云最大字符数
    },
}

# 收益率区间配置（与 persona_stats 保持一致）
_YIELD_RANGES: list[dict[str, Any]] = [
    {"label": "亏损", "max": 0},
    {"label": "0-20%", "min": 0, "max": 20},
    {"label": "20-50%", "min": 20, "max": 50},
    {"label": "50-100%", "min": 50, "max": 100},
    {"label": ">100%", "min": 100},
]


# ============================================================
# 辅助函数
# ============================================================


def _extract_year(time_str: str | None) -> str | None:
    """从时间字符串中提取年份。

    Args:
        time_str: 时间字符串（如 "2023-05-15" 或 "20230515"）

    Returns:
        str | None: 4位年份字符串，无法提取时返回 None
    """
    if not time_str or not isinstance(time_str, str):
        return None
    candidate = time_str[:4]
    if candidate.isdigit():
        return candidate
    return None


def _normalize_bar(value: int, max_value: int, max_len: int) -> str:
    """将数值归一化为指定长度的方块字符条。

    Args:
        value: 当前数值
        max_value: 所有数值中的最大值
        max_len: 条的最大字符数

    Returns:
        str: 由方块字符组成的字符串，右侧填充空格至 max_len
    """
    if max_value <= 0:
        return " " * max_len
    bar_len = max(1, round(value / max_value * max_len))
    return "█" * bar_len


def _classify_yield(yield_pct: float, ranges: list[dict[str, Any]]) -> str:
    """将收益率归类到指定区间。

    Args:
        yield_pct: 收益率数值
        ranges: 区间配置列表，每条有 label、min（可选）、max（可选）

    Returns:
        str: 区间标签
    """
    for r in ranges:
        r_min = r.get("min", float("-inf"))
        r_max = r.get("max", float("inf"))
        if r_min <= yield_pct < r_max:
            return r["label"]
    return "未知"


# ============================================================
# 8.1 操作频率年度热力图
# ============================================================


def operation_heatmap(operations: list[dict], years: list[int] | None = None) -> str:
    """生成操作频率年度热力图（Markdown 表格形式）。

    从每条操作记录的 time 字段提取年份，按年统计买入/卖出次数。
    热力条长度按合计值归一化（max=30字符）。
    如果指定 years 参数，仅输出指定年份的数据并按此顺序排列；
    否则从数据中自动提取所有年份，按年份升序排列。

    Args:
        operations: 操作时间线记录列表，每条应包含 time、action 字段
        years: 要显示的年份列表（int），为 None 时自动从数据提取

    Returns:
        str: Markdown 格式的热力图表格；输入为空时返回提示信息
    """
    if not operations:
        logger.warning("operation_heatmap: 操作列表为空")
        return "## 操作频率热力图\n\n暂无操作数据。\n"

    config = get_config()
    max_bar_len = config.get("viz", {}).get("heatmap", {}).get(
        "max_bar_len",
        _DEFAULT_VIZ_CONFIG["heatmap"]["max_bar_len"],
    )

    # 按年份统计买入/卖出
    yearly_buy: dict[str, int] = defaultdict(int)
    yearly_sell: dict[str, int] = defaultdict(int)

    for op in operations:
        time_str = op.get("time", "") or ""
        action = op.get("action", "") or ""
        year = _extract_year(time_str)
        if year is None:
            logger.debug("无法提取年份，跳过: time=%s, action=%s", time_str, action)
            continue

        if "买入" in action:
            yearly_buy[year] += 1
        elif "卖出" in action:
            yearly_sell[year] += 1
        else:
            logger.debug("无法识别操作类型，跳过: action=%s", action)

    if not yearly_buy and not yearly_sell:
        logger.warning("operation_heatmap: 未能从操作记录中提取到有效年份")
        return "## 操作频率热力图\n\n未能从操作记录中提取到有效的年份数据。\n"

    # 确定年份列表
    all_data_years = sorted(set(list(yearly_buy.keys()) + list(yearly_sell.keys())))
    if years is not None:
        # 仅保留指定的年份，转换为字符串比较
        year_str_set = set(str(y) for y in years)
        display_years = [y for y in all_data_years if y in year_str_set]
        if not display_years:
            logger.warning("operation_heatmap: 指定的年份列表在数据中均无匹配")
            return "## 操作频率热力图\n\n指定的年份在数据中无匹配记录。\n"
    else:
        display_years = all_data_years

    # 计算每行的合计值
    rows: list[dict[str, Any]] = []
    max_total = 0
    for year in display_years:
        buy_count = yearly_buy.get(year, 0)
        sell_count = yearly_sell.get(year, 0)
        total = buy_count + sell_count
        if total > max_total:
            max_total = total
        rows.append({
            "year": year,
            "buy": buy_count,
            "sell": sell_count,
            "total": total,
        })

    # 构建 Markdown 表格
    lines: list[str] = [
        "## 操作频率热力图（买入/卖出按年分布）",
        "",
        "| 年份 | 买入 | 卖出 | 合计 | 热力条 |",
        "|------|------|------|------|--------|",
    ]

    for row in rows:
        bar = _normalize_bar(row["total"], max_total, max_bar_len)
        lines.append(
            f"| {row['year']} | {row['buy']:<4} | {row['sell']:<4} | {row['total']:<4} | {bar} |"
        )

    result = "\n".join(lines) + "\n"

    logger.info(
        "operation_heatmap 生成完成: %d 个年份, %d 条操作",
        len(rows), len(operations),
    )
    return result


# ============================================================
# 8.2 品种买卖排名表
# ============================================================


def fund_ranking_table(operations: list[dict], top_k: int | None = None) -> str:
    """生成品种买卖排名表（Markdown 表格形式）。

    按 fund 字段分组统计买入和卖出次数，计算净买入（买入 - 卖出），
    按总操作次数（买入 + 卖出）降序排列，取 Top K。

    Args:
        operations: 操作时间线记录列表，每条应包含 fund、action 字段
        top_k: 取前 K 个品种，为 None 时从 config.yaml 读取

    Returns:
        str: Markdown 格式的排名表格；输入为空时返回提示信息
    """
    if not operations:
        logger.warning("fund_ranking_table: 操作列表为空")
        return "## 品种买卖排名\n\n暂无操作数据。\n"

    # 从配置读取 top_k
    if top_k is None:
        config = get_config()
        top_k = config.get("viz", {}).get("fund_rankings", {}).get(
            "top_k",
            _DEFAULT_VIZ_CONFIG["fund_rankings"]["top_k"],
        )

    # 按品种统计买入/卖出
    fund_buy: dict[str, int] = defaultdict(int)
    fund_sell: dict[str, int] = defaultdict(int)

    for op in operations:
        fund = op.get("fund", "") or ""
        if not fund:
            continue
        action = op.get("action", "") or ""
        if "买入" in action:
            fund_buy[fund] += 1
        elif "卖出" in action:
            fund_sell[fund] += 1

    if not fund_buy and not fund_sell:
        logger.warning("fund_ranking_table: 未能从操作记录中提取到品种信息")
        return "## 品种买卖排名\n\n未能从操作记录中提取到有效的品种信息。\n"

    # 计算每个品种的总操作次数和净买入
    all_funds = set(list(fund_buy.keys()) + list(fund_sell.keys()))
    fund_stats: list[dict[str, Any]] = []
    for fund in all_funds:
        buy_count = fund_buy.get(fund, 0)
        sell_count = fund_sell.get(fund, 0)
        net = buy_count - sell_count
        total = buy_count + sell_count
        if total == 0:
            continue
        preference = "买入为主" if net > 0 else "卖出为主" if net < 0 else "平衡"
        fund_stats.append({
            "fund": fund,
            "buy": buy_count,
            "sell": sell_count,
            "net": net,
            "total": total,
            "preference": preference,
        })

    # 按总操作次数降序排列，取 top_k
    fund_stats.sort(key=lambda x: x["total"], reverse=True)
    top_funds = fund_stats[:top_k]

    # 构建 Markdown 表格
    lines: list[str] = [
        f"## 品种买卖排名 Top {top_k}",
        "",
        "| 排名 | 品种 | 买入次数 | 卖出次数 | 净买入 | 操作偏好 |",
        "|------|------|---------|---------|--------|---------|",
    ]

    for rank, stat in enumerate(top_funds, start=1):
        net_str = f"+{stat['net']}" if stat['net'] > 0 else str(stat['net'])
        lines.append(
            f"| {rank:<4} | {stat['fund']:<4} | {stat['buy']:<7} | "
            f"{stat['sell']:<7} | {net_str:<6} | {stat['preference']} |"
        )

    result = "\n".join(lines) + "\n"

    logger.info(
        "fund_ranking_table 生成完成: %d 个品种 (Top %d)",
        len(fund_stats), min(top_k, len(fund_stats)),
    )
    return result


# ============================================================
# 8.3 收益率分布文本直方图
# ============================================================


def yield_histogram(operations: list[dict]) -> str:
    """生成收益率分布文本直方图（Markdown 表格形式）。

    从每条操作记录的 yield_pct 字段读取收益率，按区间统计分布。
    分布图按数量归一化（max=40字符）。
    同时计算平均收益率和最高收益率品种。

    Args:
        operations: 操作时间线记录列表，每条应包含 yield_pct、fund 字段

    Returns:
        str: Markdown 格式的收益率分布图表；输入为空时返回提示信息
    """
    if not operations:
        logger.warning("yield_histogram: 操作列表为空")
        return "## 收益率分布\n\n暂无操作数据。\n"

    config = get_config()
    max_bar_len = config.get("viz", {}).get("histogram", {}).get(
        "max_bar_len",
        _DEFAULT_VIZ_CONFIG["histogram"]["max_bar_len"],
    )

    # 按区间统计
    range_counts: dict[str, int] = defaultdict(int)
    total_yield = 0.0
    yield_count = 0
    max_yield = float("-inf")
    max_yield_fund = ""

    for op in operations:
        yield_pct = op.get("yield_pct", 0)
        if yield_pct is None or yield_pct == "":
            yield_pct = 0

        try:
            yield_val = float(yield_pct)
        except (ValueError, TypeError):
            logger.debug("无法解析 yield_pct: %s, 归入未知", yield_pct)
            range_counts["未知"] += 1
            continue

        if yield_val == 0.0:
            range_counts["未知"] += 1
            continue

        label = _classify_yield(yield_val, _YIELD_RANGES)
        range_counts[label] += 1

        total_yield += yield_val
        yield_count += 1

        if yield_val > max_yield:
            max_yield = yield_val
            max_yield_fund = op.get("fund", "") or ""

    # 按预定义区间顺序输出
    ordered_ranges: list[tuple[str, int]] = []
    for r in _YIELD_RANGES:
        label = r["label"]
        count = range_counts.get(label, 0)
        if count > 0:
            ordered_ranges.append((label, count))
    if "未知" in range_counts:
        ordered_ranges.append(("未知", range_counts["未知"]))

    if not ordered_ranges:
        logger.warning("yield_histogram: 未能提取到有效的收益率数据")
        return "## 收益率分布\n\n未能从操作记录中提取到有效的收益率数据。\n"

    # 计算最大值用于归一化
    max_count = max(c for _, c in ordered_ranges)

    # 构建 Markdown 表格
    lines: list[str] = [
        "## 收益率分布",
        "",
        "| 区间 | 数量 | 分布图 |",
        "|------|------|--------|",
    ]

    for label, count in ordered_ranges:
        bar = _normalize_bar(count, max_count, max_bar_len)
        lines.append(f"| {label:<6} | {count:<4} | {bar} |")

    # 平均收益率
    avg_yield = round(total_yield / yield_count, 1) if yield_count > 0 else 0.0
    lines.append("")
    lines.append(f"平均收益率: {avg_yield}%")

    # 最高收益率品种
    if max_yield_fund and max_yield > float("-inf"):
        lines.append(f"最高收益率: {max_yield_fund}({int(max_yield)}%)")

    result = "\n".join(lines) + "\n"

    logger.info(
        "yield_histogram 生成完成: %d 个区间, 平均收益率 %.1f%%",
        len(ordered_ranges), avg_yield,
    )
    return result


# ============================================================
# 8.4 判断主题标签云
# ============================================================


def topic_tagcloud(records: list[dict]) -> str:
    """生成判断主题标签云（Markdown 格式）。

    从每条记录的 topic 字段统计各主题的出现次数，按次数降序排列。
    标签大小按出现次数归一化（max=50字符）。

    Args:
        records: 近期判断库或观点库记录列表，每条应包含 topic 字段

    Returns:
        str: Markdown 格式的标签云文本；输入为空时返回提示信息
    """
    if not records:
        logger.warning("topic_tagcloud: 记录列表为空")
        return "## 判断主题分布\n\n暂无主题数据。\n"

    config = get_config()
    max_bar_len = config.get("viz", {}).get("tagcloud", {}).get(
        "max_bar_len",
        _DEFAULT_VIZ_CONFIG["tagcloud"]["max_bar_len"],
    )

    # 按主题统计
    topic_counts: dict[str, int] = defaultdict(int)
    for record in records:
        topic = record.get("topic", "") or ""
        if not topic:
            topic = "未分类"
        topic_counts[topic] += 1

    if not topic_counts:
        logger.warning("topic_tagcloud: 未提取到任何主题信息")
        return "## 判断主题分布\n\n未能从记录中提取到有效的主题信息。\n"

    # 按次数降序排列
    sorted_topics = sorted(topic_counts.keys(), key=lambda t: topic_counts[t], reverse=True)
    max_count = max(topic_counts.values())

    # 构建标签云
    lines: list[str] = [
        "## 判断主题分布",
        "",
    ]

    for topic in sorted_topics:
        count = topic_counts[topic]
        bar = _normalize_bar(count, max_count, max_bar_len)
        lines.append(f"> {topic} [{count}] {bar}")

    result = "\n".join(lines) + "\n"

    logger.info(
        "topic_tagcloud 生成完成: %d 个主题, %d 条记录",
        len(sorted_topics), len(records),
    )
    return result


# ============================================================
# 8.5 一键生成所有可视化图表
# ============================================================


def generate_all_viz(
    operations: list[dict],
    observations: list[dict],
    principles: list[dict],
) -> str:
    """一键生成所有可视化图表，组合成 Markdown 文档。

    依次生成：
    1. 操作频率热力图
    2. 品种买卖排名表
    3. 收益率分布直方图
    4. 近期判断主题标签云
    5. 观点库主题标签云

    Args:
        operations: 操作时间线记录列表
        observations: 近期判断库记录列表
        principles: 观点库中 source_type 为 principle 的记录列表

    Returns:
        str: 完整的 Markdown 可视化报告文档
    """
    sections: list[str] = [
        "# 人物画像可视化报告\n",
        "> 本报告由 distill 产物数据驱动生成，不调用 LLM。\n",
    ]

    # 1. 操作频率热力图
    logger.info("正在生成操作频率热力图...")
    sections.append(operation_heatmap(operations))
    sections.append("")

    # 2. 品种买卖排名表
    logger.info("正在生成品种买卖排名表...")
    sections.append(fund_ranking_table(operations))
    sections.append("")

    # 3. 收益率分布直方图
    logger.info("正在生成收益率分布直方图...")
    sections.append(yield_histogram(operations))
    sections.append("")

    # 4. 近期判断主题标签云
    logger.info("正在生成近期判断主题标签云...")
    sections.append("## 近期判断主题标签云\n")
    sections.append(topic_tagcloud(observations).lstrip("#").lstrip())
    sections.append("")

    # 5. 观点库主题标签云
    logger.info("正在生成观点库主题标签云...")
    sections.append("## 观点库主题标签云\n")
    sections.append(topic_tagcloud(principles).lstrip("#").lstrip())

    report = "\n".join(sections)

    logger.info(
        "可视化报告生成完成: %d 字符 (operations=%d, observations=%d, principles=%d)",
        len(report), len(operations), len(observations), len(principles),
    )
    return report


# ============================================================
# 8.6 从蒸馏产物目录生成可视化报告
# ============================================================


def generate_viz_report(distill_dir: str) -> str:
    """从蒸馏产物目录一键加载数据并生成可视化报告。

    加载以下数据文件：
    - {distill_dir}/操作时间线.jsonl   → operations
    - {distill_dir}/近期判断库.jsonl   → observations
    - {distill_dir}/观点库.jsonl       → principles（过滤 source_type=principle）

    Args:
        distill_dir: 蒸馏产物目录路径（绝对或相对路径）

    Returns:
        str: 完整的 Markdown 可视化报告文档

    Raises:
        FileNotFoundError: distill_dir 目录不存在
    """
    # 解析为绝对路径
    distill_dir = os.path.abspath(distill_dir)
    if not os.path.isdir(distill_dir):
        raise FileNotFoundError(f"蒸馏产物目录不存在: {distill_dir}")

    from scripts.advisor.opinion_matcher import load_jsonl

    ops_path = os.path.join(distill_dir, "操作时间线.jsonl")
    obs_path = os.path.join(distill_dir, "近期判断库.jsonl")
    principles_path = os.path.join(distill_dir, "观点库.jsonl")

    logger.info("从 %s 加载蒸馏产物数据", distill_dir)

    operations = load_jsonl(ops_path)
    observations = load_jsonl(obs_path)
    all_opinions = load_jsonl(principles_path)

    # 从观点库中过滤出 source_type=principle 的记录
    principles = [o for o in all_opinions if o.get("source_type") == "principle"]

    logger.info(
        "数据加载完成: operations=%d, observations=%d, principles=%d",
        len(operations), len(observations), len(principles),
    )

    return generate_all_viz(operations, observations, principles)