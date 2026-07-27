"""
人物画像统计指标模块

对操作时间线、近期判断库、观点库等蒸馏产物进行量化统计分析，
生成人物画像所依赖的各项统计指标。

所有统计数据均为纯数据驱动，不调用 LLM。

Usage:
    from scripts.distill.persona_stats import compute_all_stats

    stats = compute_all_stats(operations, observations, principles)
    print(generate_stats_report(stats))
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any

logger = logging.getLogger(__name__)

# ============================================================
# 默认统计配置（可通过 config.yaml 中 stats 节覆盖）
# ============================================================

_DEFAULT_STATS_CONFIG: dict[str, Any] = {
    "fund_rankings": {
        "top_k": 10,                     # 品种买卖排名取 Top K
    },
    "yield_distribution": {
        "ranges": [                      # 收益率区间划分
            {"label": "亏损", "max": 0},
            {"label": "0-20%", "min": 0, "max": 20},
            {"label": "20-50%", "min": 20, "max": 50},
            {"label": "50-100%", "min": 50, "max": 100},
            {"label": ">100%", "min": 100},
        ],
    },
    "topic_distribution": {
        "min_pct": 0.0,                  # 最低显示百分比（过滤低频主题）
    },
    "principle_cooccurrence": {
        "top_k_keywords": 10,            # 取 Top N 个关键词分析共现
    },
}

# 收益率区间配置，供 yield_distribution 使用
_YIELD_RANGES: list[dict[str, Any]] = [
    {"label": "亏损", "max": 0},
    {"label": "0-20%", "min": 0, "max": 20},
    {"label": "20-50%", "min": 20, "max": 50},
    {"label": "50-100%", "min": 50, "max": 100},
    {"label": ">100%", "min": 100},
]


# ============================================================
# 4.1 按年份统计操作频率
# ============================================================


def operation_frequency_by_year(operations: list[dict]) -> dict[str, dict[str, int]]:
    """按年份统计操作频率（买入/卖出/合计）。

    从每条操作记录的 time 字段提取前4位作为年份，
    从 action 字段判断操作类型（包含"买入" → 买入，包含"卖出" → 卖出）。
    无法提取年份的记录归入 "unknown"。

    Args:
        operations: 操作记录列表，每条应包含 time、action 字段

    Returns:
        dict: 按年份分组的统计结果，格式如:
            {
                "2023": {"买入": 5, "卖出": 3, "合计": 8},
                "unknown": {"买入": 0, "卖出": 1, "合计": 1},
            }
    """
    # 按年份和操作类型分别计数
    yearly_buy: dict[str, int] = defaultdict(int)
    yearly_sell: dict[str, int] = defaultdict(int)

    for op in operations:
        time_str = op.get("time", "") or ""
        action = op.get("action", "") or ""

        # 提取年份：取 time 前4位，必须是数字
        year = "unknown"
        if len(time_str) >= 4 and time_str[:4].isdigit():
            year = time_str[:4]
        else:
            logger.debug("无法提取年份，归入 unknown: time=%s, action=%s", time_str, action)

        # 判断操作类型
        if "买入" in action:
            yearly_buy[year] += 1
        elif "卖出" in action:
            yearly_sell[year] += 1
        else:
            logger.debug("无法识别操作类型，忽略: action=%s", action)

    # 合并结果
    all_years = sorted(set(list(yearly_buy.keys()) + list(yearly_sell.keys())))
    # 将 unknown 放在最后
    if "unknown" in all_years:
        all_years.remove("unknown")
        all_years.append("unknown")

    result: dict[str, dict[str, int]] = {}
    for year in all_years:
        buy_count = yearly_buy.get(year, 0)
        sell_count = yearly_sell.get(year, 0)
        result[year] = {
            "买入": buy_count,
            "卖出": sell_count,
            "合计": buy_count + sell_count,
        }

    logger.info("操作频率统计完成: %d 个年份, %d 条操作", len(result), len(operations))
    return result


# ============================================================
# 4.2 品种买卖排名
# ============================================================


def fund_rankings(operations: list[dict], top_k: int = 10) -> dict[str, dict[str, int]]:
    """品种买卖排名统计。

    按 fund 字段分组，分别统计买入次数、卖出次数和净买入（买入 - 卖出）。
    结果按次数降序排列，取 top_k。

    Args:
        operations: 操作记录列表，每条应包含 fund、action 字段
        top_k: 取前 K 个品种，默认 10

    Returns:
        dict: 包含"买入"、"卖出"、"净买入"三组排名，格式如:
            {
                "买入": {"中证500": 15, "沪深300": 10, ...},
                "卖出": {"中证500": 8, "沪深300": 5, ...},
                "净买入": {"中证500": 7, "沪深300": 5, ...},
            }
    """
    # 按品种和操作类型计数
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

    # 所有出现过的品种
    all_funds = set(list(fund_buy.keys()) + list(fund_sell.keys()))

    # 买入排名（降序）
    buy_sorted = sorted(all_funds, key=lambda f: fund_buy.get(f, 0), reverse=True)
    buy_rank = {f: fund_buy.get(f, 0) for f in buy_sorted[:top_k] if fund_buy.get(f, 0) > 0}

    # 卖出排名（降序）
    sell_sorted = sorted(all_funds, key=lambda f: fund_sell.get(f, 0), reverse=True)
    sell_rank = {f: fund_sell.get(f, 0) for f in sell_sorted[:top_k] if fund_sell.get(f, 0) > 0}

    # 净买入排名（降序），取 top_k
    net: dict[str, int] = {}
    for f in all_funds:
        net_val = fund_buy.get(f, 0) - fund_sell.get(f, 0)
        if net_val != 0:
            net[f] = net_val
    net_sorted = sorted(net.keys(), key=lambda f: net[f], reverse=True)
    net_rank = {f: net[f] for f in net_sorted[:top_k]}

    result = {
        "买入": buy_rank,
        "卖出": sell_rank,
        "净买入": net_rank,
    }

    logger.info(
        "品种买卖排名统计完成: 买入 %d 品种, 卖出 %d 品种, 净买入 %d 品种",
        len(buy_rank), len(sell_rank), len(net_rank),
    )
    return result


# ============================================================
# 4.3 收益率分布统计
# ============================================================


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


def yield_distribution(operations: list[dict]) -> dict[str, Any]:
    """收益率分布统计。

    从每条操作记录的 yield_pct 字段读取收益率，按区间统计分布。
    yield_pct 为 0 或字段不存在的记录归入"未知"。

    Args:
        operations: 操作记录列表，每条应包含 yield_pct、fund 字段

    Returns:
        dict: 包含区间分布、最高收益率品种、平均收益率的字典，格式如:
            {
                "range_distribution": {"0-20%": 15, "20-50%": 8, ...},
                "max_yield": {"fund": "中证500", "yield_pct": 120},
                "avg_yield": 15.5,
            }
    """
    range_counts: dict[str, int] = defaultdict(int)
    total_yield = 0.0
    yield_count = 0
    max_yield = float("-inf")
    max_yield_fund = ""

    for op in operations:
        yield_pct = op.get("yield_pct", 0)
        # yield_pct 可能为 None 或空字符串
        if yield_pct is None or yield_pct == "":
            yield_pct = 0

        try:
            yield_val = float(yield_pct)
        except (ValueError, TypeError):
            logger.debug("无法解析 yield_pct: %s, 归入未知", yield_pct)
            range_counts["未知"] += 1
            continue

        # yield_pct = 0 且没有明确收益率信息 → 未知
        if yield_val == 0.0:
            range_counts["未知"] += 1
            continue

        # 归类到区间
        label = _classify_yield(yield_val, _YIELD_RANGES)
        range_counts[label] += 1

        # 统计平均值
        total_yield += yield_val
        yield_count += 1

        # 记录最大值
        if yield_val > max_yield:
            max_yield = yield_val
            max_yield_fund = op.get("fund", "") or ""

    # 计算平均收益率
    avg_yield = round(total_yield / yield_count, 1) if yield_count > 0 else 0.0

    # 转换为普通 dict 确保序列化友好
    range_distribution = dict(range_counts)
    # 按预定义区间顺序输出
    ordered_distribution: dict[str, int] = {}
    for r in _YIELD_RANGES:
        label = r["label"]
        if label in range_distribution:
            ordered_distribution[label] = range_distribution[label]
    # "未知"放在最后
    if "未知" in range_distribution:
        ordered_distribution["未知"] = range_distribution["未知"]

    result: dict[str, Any] = {
        "range_distribution": ordered_distribution,
        "max_yield": {"fund": max_yield_fund, "yield_pct": int(max_yield) if max_yield > float("-inf") else 0},
        "avg_yield": avg_yield,
    }

    logger.info(
        "收益率分布统计完成: %d 个区间, 平均收益率 %.1f%%, 最高 %.1f%%(%s)",
        len(ordered_distribution), avg_yield, max_yield if max_yield > float("-inf") else 0, max_yield_fund,
    )
    return result


# ============================================================
# 4.4 主题分布统计
# ============================================================


def topic_distribution(observations_or_principles: list[dict]) -> dict[str, dict[str, float]]:
    """判断/原则的主题分布统计。

    从每条记录的 topic 字段统计各主题的条数和百分比。
    结果按条数降序排列，百分比保留 1 位小数。

    Args:
        observations_or_principles: 近期判断库或观点库记录列表，每条应包含 topic 字段

    Returns:
        dict: 按 topic 分组的统计结果，格式如:
            {
                "估值判断": {"count": 200, "pct": 20.1},
                "仓位管理": {"count": 150, "pct": 15.1},
            }
    """
    topic_counts: dict[str, int] = defaultdict(int)
    total = 0

    for record in observations_or_principles:
        topic = record.get("topic", "") or ""
        if not topic:
            topic = "未分类"
        topic_counts[topic] += 1
        total += 1

    if total == 0:
        logger.warning("主题分布统计: 输入记录为空")
        return {}

    # 按 count 降序排列
    sorted_topics = sorted(topic_counts.keys(), key=lambda t: topic_counts[t], reverse=True)

    result: dict[str, dict[str, float]] = {}
    for topic in sorted_topics:
        count = topic_counts[topic]
        pct = round(count / total * 100, 1)
        result[topic] = {"count": count, "pct": pct}

    logger.info("主题分布统计完成: %d 个主题, 共 %d 条记录", len(result), total)
    return result


# ============================================================
# 4.5 原则关键词共现分析
# ============================================================


def _extract_keywords_from_text(text: str) -> list[str]:
    """从文本中提取关键词（简单分词方案）。

    优先使用 jieba 分词（可选依赖），不可用时使用简单的
    中文字符二元组分词作为降级方案。

    Args:
        text: 输入文本

    Returns:
        list[str]: 提取的关键词列表（去重）
    """
    if not text or not text.strip():
        return []

    try:
        import jieba
        # 使用 jieba 分词，过滤单字和过短词
        words = [
            w.strip()
            for w in jieba.cut(text)
            if len(w.strip()) >= 2 and not w.strip().isdigit()
        ]
        # 去重但保持顺序
        seen: set[str] = set()
        result: list[str] = []
        for w in words:
            if w not in seen:
                seen.add(w)
                result.append(w)
        return result
    except ImportError:
        logger.debug("jieba 不可用，使用简单分词降级方案")
        pass

    # 降级方案：基于常见标点符号拆分，提取长度 >= 2 的片段
    import re
    segments = re.split(r"[，。！？、；：""''（）\[\]【】\s,\.!?;:\"\'()\[\]\{\}]", text)
    words = [s.strip() for s in segments if len(s.strip()) >= 2]
    # 去重
    seen_set: set[str] = set()
    deduped: list[str] = []
    for w in words:
        if w not in seen_set:
            seen_set.add(w)
            deduped.append(w)
    return deduped


def principle_cooccurrence(principles: list[dict]) -> list[dict[str, Any]]:
    """原则关键词共现分析。

    对每条原则记录，从 keywords 字段提取关键词列表。
    如果某条记录没有 keywords 字段，用 jieba 分词从 opinion 字段提取。
    统计两两关键词出现在同一条记录中的次数。
    只分析出现频次最高的 Top N 个关键词。

    Args:
        principles: 观点库中 source_type 为 principle 的记录列表

    Returns:
        list[dict]: 共现关系列表，按关键词出现频次降序排列，格式如:
            [
                {
                    "keyword": "估值",
                    "cooccur": {"仓位": 15, "买入": 10, "周期": 8},
                    "count": 30,
                },
                ...
            ]
    """
    if not principles:
        logger.warning("关键词共现分析: 输入原则列表为空")
        return []

    # 第一步：提取每条记录的关键词列表
    record_keywords: list[list[str]] = []
    for p in principles:
        keywords = p.get("keywords", [])
        # 如果 keywords 是字符串，拆分为列表
        if isinstance(keywords, str):
            kw_list = [kw.strip() for kw in keywords.split(",") if kw.strip()]
        elif isinstance(keywords, list):
            kw_list = [str(kw).strip() for kw in keywords if str(kw).strip()]
        else:
            kw_list = []

        # 如果 keywords 为空，从 opinion 字段提取
        if not kw_list:
            opinion = p.get("opinion", "") or ""
            kw_list = _extract_keywords_from_text(opinion)

        record_keywords.append(kw_list)

    # 第二步：统计每个关键词的出现次数
    keyword_count: dict[str, int] = defaultdict(int)
    for kw_list in record_keywords:
        for kw in kw_list:
            keyword_count[kw] += 1

    if not keyword_count:
        logger.warning("关键词共现分析: 未提取到任何关键词")
        return []

    # 第三步：取 Top N 关键词
    top_k = _DEFAULT_STATS_CONFIG["principle_cooccurrence"]["top_k_keywords"]
    top_keywords = sorted(keyword_count.keys(), key=lambda k: keyword_count[k], reverse=True)[:top_k]
    top_keyword_set = set(top_keywords)

    # 第四步：统计共现次数（只统计 Top 关键词之间的共现）
    cooccur_count: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for kw_list in record_keywords:
        # 只保留属于 Top 的关键词
        filtered = [kw for kw in kw_list if kw in top_keyword_set]
        # 去重（同一条记录内同一关键词只计一次）
        unique_kws = list(dict.fromkeys(filtered))
        for i in range(len(unique_kws)):
            for j in range(i + 1, len(unique_kws)):
                kw_a, kw_b = unique_kws[i], unique_kws[j]
                cooccur_count[kw_a][kw_b] += 1
                cooccur_count[kw_b][kw_a] += 1

    # 第五步：组装结果，按 keyword count 降序排列
    result: list[dict[str, Any]] = []
    for kw in top_keywords:
        cooccur_dict = dict(cooccur_count.get(kw, {}))
        # 对共现对象按次数降序排列
        sorted_cooccur = dict(
            sorted(cooccur_dict.items(), key=lambda x: x[1], reverse=True)
        )
        result.append({
            "keyword": kw,
            "cooccur": sorted_cooccur,
            "count": keyword_count[kw],
        })

    logger.info(
        "关键词共现分析完成: %d 个关键词, %d 条原则记录",
        len(result), len(principles),
    )
    return result


# ============================================================
# 4.6 生成统计摘要报告
# ============================================================


def generate_stats_report(stats: dict[str, Any]) -> str:
    """生成人类可读的统计摘要文本。

    将 4.1-4.5 所有统计结果合并为纯文本 + Markdown 格式的摘要报告。
    全部数据驱动，不含 LLM 编造内容。

    Args:
        stats: compute_all_stats 返回的完整统计字典，包含以下键:
            - operation_frequency_by_year
            - fund_rankings
            - yield_distribution
            - topic_distribution
            - principle_cooccurrence

    Returns:
        str: Markdown 格式的统计摘要文本
    """
    sections: list[str] = ["# 人物画像统计报告\n"]

    # --- 4.1 操作频率分布 ---
    yearly_stats = stats.get("operation_frequency_by_year", {})
    if yearly_stats:
        sections.append("## 操作频率分布\n")
        for year in sorted(yearly_stats.keys()):
            data = yearly_stats[year]
            sections.append(
                f"- {year}年：买入{data['买入']}次，卖出{data['卖出']}次，合计{data['合计']}次"
            )
        sections.append("")

    # --- 4.2 品种买卖排名 ---
    fund_data = stats.get("fund_rankings", {})
    if fund_data:
        sections.append("## 品种买卖排名\n")
        for action_type in ["买入", "卖出", "净买入"]:
            rank = fund_data.get(action_type, {})
            if rank:
                items = [f"{fund}({count}次)" for fund, count in rank.items()]
                sections.append(f"{action_type}最多的品种：{'、'.join(items)}")
        sections.append("")

    # --- 4.3 收益率分布 ---
    yield_data = stats.get("yield_distribution", {})
    if yield_data:
        sections.append("## 收益率分布\n")
        range_dist = yield_data.get("range_distribution", {})
        if range_dist:
            parts = [f"{label}：{count}笔" for label, count in range_dist.items()]
            sections.append("- 区间分布：" + "、".join(parts))

        max_yield = yield_data.get("max_yield", {})
        if max_yield and max_yield.get("yield_pct", 0) > 0:
            sections.append(
                f"- 最高收益率品种：{max_yield['fund']}（{max_yield['yield_pct']}%）"
            )

        avg_yield = yield_data.get("avg_yield", 0)
        sections.append(f"- 平均收益率：{avg_yield}%")
        sections.append("")

    # --- 4.4 主题分布 ---
    topic_data = stats.get("topic_distribution", {})
    if topic_data:
        sections.append("## 主题分布\n")
        for topic, data in topic_data.items():
            sections.append(f"- {topic}：{data['count']}条（{data['pct']}%）")
        sections.append("")

    # --- 4.5 关键词共现 ---
    cooccur_data = stats.get("principle_cooccurrence", [])
    if cooccur_data:
        sections.append("## 关键词共现分析\n")
        for item in cooccur_data:
            kw = item["keyword"]
            count = item["count"]
            cooccur = item.get("cooccur", {})
            if cooccur:
                co_parts = [f"{ckw}({cc}次)" for ckw, cc in list(cooccur.items())[:5]]
                sections.append(f"- {kw}（出现{count}次）共现：{'、'.join(co_parts)}")
            else:
                sections.append(f"- {kw}（出现{count}次）")
        sections.append("")

    report = "\n".join(sections)
    logger.info("统计摘要报告生成完成: %d 字符", len(report))
    return report


# ============================================================
# 4.7 一键计算所有统计指标
# ============================================================


def compute_all_stats(
    operations: list[dict],
    observations: list[dict],
    principles: list[dict],
    top_k_funds: int | None = None,
) -> dict[str, Any]:
    """一键计算所有统计指标。

    依次调用 operation_frequency_by_year、fund_rankings、
    yield_distribution、topic_distribution、principle_cooccurrence，
    将结果合并为统一字典。

    Args:
        operations: 操作时间线记录列表
        observations: 近期判断库记录列表
        principles: 观点库中 source_type 为 principle 的记录列表
        top_k_funds: fund_rankings 的 top_k 参数，默认从配置读取

    Returns:
        dict: 所有统计结果的合并字典，包含以下键:
            - operation_frequency_by_year
            - fund_rankings
            - yield_distribution
            - topic_distribution
            - principle_cooccurrence

    Raises:
        ValueError: 输入参数类型错误
    """
    if not isinstance(operations, list):
        raise ValueError(f"operations 必须是 list，收到 {type(operations)}")
    if not isinstance(observations, list):
        raise ValueError(f"observations 必须是 list，收到 {type(observations)}")
    if not isinstance(principles, list):
        raise ValueError(f"principles 必须是 list，收到 {type(principles)}")

    logger.info(
        "开始计算全部统计指标: operations=%d条, observations=%d条, principles=%d条",
        len(operations), len(observations), len(principles),
    )

    # 4.1 操作频率分布
    logger.info("正在计算操作频率分布...")
    freq = operation_frequency_by_year(operations)

    # 4.2 品种买卖排名
    logger.info("正在计算品种买卖排名...")
    if top_k_funds is None:
        top_k_funds = _DEFAULT_STATS_CONFIG["fund_rankings"]["top_k"]
    rankings = fund_rankings(operations, top_k=top_k_funds)

    # 4.3 收益率分布
    logger.info("正在计算收益率分布...")
    yield_dist = yield_distribution(operations)

    # 4.4 主题分布
    logger.info("正在计算主题分布...")
    all_records = observations + principles
    topic_dist = topic_distribution(all_records)

    # 4.5 关键词共现分析
    logger.info("正在计算关键词共现...")
    cooccur = principle_cooccurrence(principles)

    result: dict[str, Any] = {
        "operation_frequency_by_year": freq,
        "fund_rankings": rankings,
        "yield_distribution": yield_dist,
        "topic_distribution": topic_dist,
        "principle_cooccurrence": cooccur,
    }

    logger.info("全部统计指标计算完成")
    return result