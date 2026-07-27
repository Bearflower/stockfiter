"""
人物画像文档组装模块

从结构化索引 + 统计摘要 + 金句库 + 核心观点矿脉 组装 E大人物画像文档。
不再调用 LLM 生成，改为纯数据驱动的文档组装。

所有数据均可追溯至原始 JSONL 文件，不包含 LLM 编造的内容。

Usage:
    python -m scripts.distill.persona_builder
"""

from __future__ import annotations

import json
import logging
import os
from collections import defaultdict
from typing import Any

logger = logging.getLogger(__name__)


# ============================================================
# 文件工具函数（保留）
# ============================================================


def _read_file_safe(file_path: str, source_name: str) -> str:
    """安全读取文件内容。

    Args:
        file_path: 文件路径
        source_name: 数据来源名称（用于日志）

    Returns:
        str: 文件内容，读取失败时返回空字符串
    """
    if not os.path.isfile(file_path):
        logger.warning("%s 文件不存在: %s", source_name, file_path)
        return ""
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
        logger.info("已加载 %s: %s (%d 字符)", source_name, file_path, len(content))
        return content
    except Exception as e:
        logger.error("读取 %s 失败: %s", source_name, e)
        return ""


def _load_jsonl(filepath: str) -> list[dict]:
    """安全加载 JSONL 文件。

    Args:
        filepath: JSONL 文件路径

    Returns:
        list[dict]: 记录列表
    """
    if not os.path.isfile(filepath):
        logger.warning("文件不存在: %s", filepath)
        return []
    records: list[dict] = []
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
        logger.info("已加载 %d 条记录从 %s", len(records), os.path.basename(filepath))
    except Exception as e:
        logger.error("加载 JSONL 失败 %s: %s", filepath, e)
    return records


# ============================================================
# 辅助函数：提取基础元信息
# ============================================================


def _extract_year(time_str: str) -> str:
    """从时间字符串中提取年份。

    Args:
        time_str: 时间字符串，如 "2024-03-15" 或 "2024年3月"

    Returns:
        str: 4位年份字符串，无法提取时返回 "unknown"
    """
    if not time_str:
        return "unknown"
    if len(time_str) >= 4 and time_str[:4].isdigit():
        return time_str[:4]
    return "unknown"


def _collect_time_range(data: dict) -> tuple[str, str]:
    """从所有记录中收集时间范围（最早和最晚的年份）。

    Args:
        data: 三通道数据字典

    Returns:
        tuple[str, str]: (最早年份, 最晚年份)，数据为空时返回 ("未知", "未知")
    """
    years: set[str] = set()
    for key in ("operations", "observations", "principles"):
        for rec in data.get(key, []):
            year = _extract_year(rec.get("time", ""))
            if year != "unknown":
                years.add(year)

    if not years:
        return ("未知", "未知")

    sorted_years = sorted(years, key=int)
    return (sorted_years[0], sorted_years[-1])


def _collect_topics(data: dict) -> list[tuple[str, int]]:
    """从观察和原则中统计主题分布，返回按次数降序排列的列表。

    Args:
        data: 三通道数据字典

    Returns:
        list[tuple[str, int]]: [(主题, 出现次数), ...]
    """
    topic_counter: dict[str, int] = defaultdict(int)
    for key in ("observations", "principles"):
        for rec in data.get(key, []):
            topic = rec.get("topic", "").strip()
            if topic:
                topic_counter[topic] += 1
            else:
                topic_counter["未分类"] += 1

    return sorted(topic_counter.items(), key=lambda x: x[1], reverse=True)


# ============================================================
# 文档章节组装函数
# ============================================================


def _build_identity_section(data: dict, index: dict | None) -> str:
    """构建人物身份档案章节。

    从数据中提取：
    - 总记录数
    - 时间范围
    - 总操作次数、总原则条数
    - 核心投资领域（从 topic 分布推断）

    Args:
        data: 三通道数据字典
        index: 结构化索引（可为 None）

    Returns:
        str: Markdown 格式的身份档案章节
    """
    if not data:
        return "（数据不可用）"

    operations = data.get("operations", [])
    observations = data.get("observations", [])
    principles = data.get("principles", [])

    # 时间范围
    earliest, latest = _collect_time_range(data)

    # 主题分布（推断核心投资领域）
    topics = _collect_topics(data)
    core_areas = "、".join([t for t, _ in topics[:6]]) if topics else "（暂未识别）"

    # 核心标签（从 topic 提取前5个作为标签）
    tags = ", ".join([f"`{t}`" for t, _ in topics[:6]]) if topics else "`待识别`"

    lines: list[str] = [
        "| 项目 | 内容 |",
        "|------|------|",
        f"| 数据时间跨度 | {earliest}年 — {latest}年 |",
        f"| 总操作记录数 | {len(operations)} 条 |",
        f"| 总市场判断数 | {len(observations)} 条 |",
        f"| 总投资原则数 | {len(principles)} 条 |",
        f"| 核心关注领域 | {core_areas} |",
        f"| 核心标签 | {tags} |",
    ]
    return "\n".join(lines)


def _build_timeline_section(data: dict, index: dict | None) -> str:
    """构建交易操作时间线章节。

    按年份生成 Markdown 表格：
    | 年份 | 买入次数 | 卖出次数 | 主要品种 | 关键操作 |

    Args:
        data: 三通道数据字典
        index: 结构化索引（可为 None）

    Returns:
        str: Markdown 格式的时间线章节
    """
    operations = data.get("operations", [])
    if not operations:
        return "（暂无操作记录）"

    # 按年份分组统计
    yearly_data: dict[str, dict[str, Any]] = {}
    for op in operations:
        year = _extract_year(op.get("time", ""))
        if year == "unknown":
            year = "未知"

        if year not in yearly_data:
            yearly_data[year] = {
                "买入": 0,
                "卖出": 0,
                "品种": set(),
                "关键操作": [],
            }

        action = op.get("action", "")
        if "买入" in action:
            yearly_data[year]["买入"] += 1
        elif "卖出" in action:
            yearly_data[year]["卖出"] += 1

        fund = op.get("fund", "")
        if fund:
            yearly_data[year]["品种"].add(fund)

        # 收集有备注的操作作为关键操作
        memo = op.get("memo", "")
        if memo:
            yearly_data[year]["关键操作"].append(f"{action}{fund}: {memo}")

    # 按年份排序（将 "未知" 放在最后）
    sorted_years = sorted(
        [y for y in yearly_data if y != "未知"],
        key=int,
        reverse=True,
    )
    if "未知" in yearly_data:
        sorted_years.append("未知")

    # 生成表格
    table_lines: list[str] = [
        "| 年份 | 买入次数 | 卖出次数 | 主要品种 | 关键操作 |",
        "|------|---------|---------|---------|---------|",
    ]
    for year in sorted_years:
        yd = yearly_data[year]
        funds = "、".join(sorted(yd["品种"])[:5]) if yd["品种"] else "-"
        key_ops = "；".join(yd["关键操作"][:3]) if yd["关键操作"] else "-"
        table_lines.append(
            f"| {year}年 | {yd['买入']} | {yd['卖出']} | {funds} | {key_ops} |"
        )

    return "\n".join(table_lines)


def _build_evolution_section(data: dict, index: dict | None) -> str:
    """构建市场判断演化史章节。

    按年份列出判断数量，引用具体判断原文（每条用引文格式）。

    Args:
        data: 三通道数据字典
        index: 结构化索引（可为 None）

    Returns:
        str: Markdown 格式的演化史章节
    """
    observations = data.get("observations", [])
    if not observations:
        return "（暂无市场判断）"

    # 按年份分组
    by_year: dict[str, list[dict]] = defaultdict(list)
    for obs in observations:
        year = _extract_year(obs.get("time", ""))
        by_year[year].append(obs)

    # 按年份倒序排列
    sorted_years = sorted(
        [y for y in by_year if y != "unknown"],
        key=int,
        reverse=True,
    )
    if "unknown" in by_year:
        sorted_years.append("unknown")

    sections: list[str] = []
    for year in sorted_years:
        records = by_year[year]
        year_label = f"{year}年" if year != "unknown" else "未知年份"
        sections.append(f"### {year_label}（共 {len(records)} 条判断）\n")

        # 取最多10条代表性判断
        for rec in records[:10]:
            opinion = rec.get("opinion", "").strip()
            if opinion:
                sections.append(f"> {opinion}\n")

        if len(records) > 10:
            sections.append(f"> （共 {len(records)} 条，仅展示前10条）\n")

    return "\n".join(sections)


def _build_philosophy_section(data: dict, index: dict | None) -> str:
    """构建投资哲学体系章节。

    从 principles 中提取核心原则，按 topic 分组展示。

    Args:
        data: 三通道数据字典
        index: 结构化索引（可为 None）

    Returns:
        str: Markdown 格式的哲学体系章节
    """
    principles = data.get("principles", [])
    if not principles:
        return "（暂无投资原则）"

    # 按 topic 分组
    by_topic: dict[str, list[dict]] = defaultdict(list)
    for p in principles:
        topic = p.get("topic", "").strip() or "未分类"
        by_topic[topic].append(p)

    # 按主题内记录数降序排列
    sorted_topics = sorted(by_topic.keys(), key=lambda t: len(by_topic[t]), reverse=True)

    sections: list[str] = []
    for topic in sorted_topics:
        records = by_topic[topic]
        sections.append(f"### {topic}（{len(records)} 条原则）\n")

        for rec in records[:8]:
            opinion = rec.get("opinion", "").strip()
            if opinion:
                sections.append(f"- {opinion}")

        sections.append("")

        if len(records) > 8:
            sections.append(f"> 该主题共有 {len(records)} 条原则，仅展示前8条。\n")

    return "\n".join(sections)


def _build_style_section(quotes_text: str) -> str:
    """构建语言风格参考章节。

    直接从金句库提取代表性金句，展示语言风格特征。

    Args:
        quotes_text: 金句库 Markdown 文本

    Returns:
        str: Markdown 格式的语言风格章节
    """
    if not quotes_text or not quotes_text.strip():
        return "（暂无金句库）"

    # 从金句库文本中提取前30条金句（按行分割，过滤空行和标题行）
    lines = quotes_text.strip().split("\n")
    quote_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        # 保留以 - 或 > 开头的金句行，跳过标题行
        if stripped.startswith("-") or stripped.startswith(">"):
            quote_lines.append(stripped)
        # 也保留纯文本的行（可能是简短金句）
        elif stripped and not stripped.startswith("#") and len(stripped) < 200:
            if "ETF" in stripped or "投资" in stripped or "市场" in stripped or "仓位" in stripped:
                quote_lines.append(stripped)

    if not quote_lines:
        # 如果无法解析，直接展示全文前30行
        quote_lines = [l for l in lines if l.strip()][:30]

    sections: list[str] = [
        "以下为 E 大的代表性语言风格样本（摘自金句库）：\n"
    ]
    for q in quote_lines[:20]:
        sections.append(f"- {q}")

    if len(quote_lines) > 20:
        sections.append(f"\n> 金句库共 {len(lines)} 行，仅展示前20条。")

    return "\n".join(sections)


def _build_appendix(data: dict, index: dict | None) -> str:
    """构建附录章节。

    包含数据来源说明、统计信息、品种排名等。

    Args:
        data: 三通道数据字典
        index: 结构化索引（可为 None）

    Returns:
        str: Markdown 格式的附录章节
    """
    parts: list[str] = ["### 数据来源说明\n"]

    if data:
        operations = data.get("operations", [])
        observations = data.get("observations", [])
        principles = data.get("principles", [])

        parts.append(f"- **操作时间线**：{len(operations)} 条记录（来自 操作时间线.jsonl）")
        parts.append(f"- **近期判断库**：{len(observations)} 条记录（来自 近期判断库.jsonl）")
        parts.append(f"- **观点库（原则）**：{len(principles)} 条记录（来自 观点库.jsonl）")
        parts.append("")

        # 品种统计
        fund_counter: dict[str, int] = defaultdict(int)
        op_types: dict[str, int] = defaultdict(int)
        for op in operations:
            fund = op.get("fund", "").strip()
            if fund:
                fund_counter[fund] += 1
            action = op.get("action", "").strip()
            if action:
                op_types[action] += 1

        if fund_counter:
            parts.append("### 品种操作次数排名\n")
            sorted_funds = sorted(fund_counter.items(), key=lambda x: x[1], reverse=True)
            parts.append("| 排名 | 品种 | 操作次数 |")
            parts.append("|------|------|---------|")
            for rank, (fund, count) in enumerate(sorted_funds[:15], 1):
                parts.append(f"| {rank} | {fund} | {count} |")
            parts.append("")

    if index:
        # 索引维度统计
        parts.append("### 结构化索引维度\n")
        parts.append(f"- **by_fund**（按品种）：{len(index.get('by_fund', {}))} 个品种")
        parts.append(f"- **by_action**（按操作类型）：{len(index.get('by_action', {}))} 种类型")
        parts.append(f"- **by_topic**（按主题）：{len(index.get('by_topic', {}))} 个主题")
        parts.append(f"- **by_year**（按年份）：{len(index.get('by_year', {}))} 个年份")
        parts.append("")
    else:
        parts.append("### 结构化索引\n")
        parts.append("（结构化索引不可用）\n")

    return "\n".join(parts)


# ============================================================
# 主构建函数
# ============================================================


def build_persona(config: dict[str, Any], distill_dir: str | None = None) -> str:
    """从结构化索引和统计摘要组装 E 大人物画像文档。

    不再调用 LLM 生成，改为纯数据驱动的文档组装。
    所有数据均可追溯至原始 JSONL 文件。

    Args:
        config: 完整配置字典
        distill_dir: 蒸馏产物目录（可选，默认从 config 推断）

    Returns:
        str: 结构化的人物画像 Markdown 文档
    """
    # 确定蒸馏产物目录
    if distill_dir is None:
        distill_dir = config.get("paths", {}).get("output_dir", "")
        if not distill_dir:
            logger.error("无法确定蒸馏产物目录，请提供 distill_dir 或配置 paths.output_dir")
            return ""

    logger.info("读取蒸馏产物目录: %s", distill_dir)

    # ============================================================
    # 1. 加载结构化索引（如果存在）
    # ============================================================
    index = None
    data = None
    try:
        from scripts.distill.persona_index import load_data, load_index

        index_dir = os.path.join(distill_dir, "persona_index")
        index = load_index(index_dir)
        data = load_data(distill_dir)
        logger.info(
            "结构化索引加载完成: operations=%d, observations=%d, principles=%d",
            len(data.get("operations", [])),
            len(data.get("observations", [])),
            len(data.get("principles", [])),
        )
    except ImportError:
        logger.warning("persona_index 模块不可用，将直接加载原始数据")
    except Exception as e:
        logger.warning("加载结构化索引失败，将直接加载原始数据: %s", e)

    # 如果结构化索引加载失败，直接加载原始 JSONL 数据
    if data is None:
        ops_path = os.path.join(distill_dir, "操作时间线.jsonl")
        obs_path = os.path.join(distill_dir, "近期判断库.jsonl")
        opinions_path = os.path.join(distill_dir, "观点库.jsonl")

        raw_ops = _load_jsonl(ops_path)
        raw_obs = _load_jsonl(obs_path)
        raw_opinions = _load_jsonl(opinions_path)
        raw_principles = [o for o in raw_opinions if o.get("source_type") == "principle"]

        data = {
            "operations": raw_ops,
            "observations": raw_obs,
            "principles": raw_principles,
        }

    # ============================================================
    # 2. 加载统计摘要
    # ============================================================
    stats_report: str = ""
    try:
        from scripts.distill.persona_stats import compute_all_stats, generate_stats_report

        stats = compute_all_stats(
            data["operations"],
            data["observations"],
            data["principles"],
        )
        stats_report = generate_stats_report(stats)
        logger.info("统计摘要生成完成: %d 字符", len(stats_report))
    except ImportError:
        logger.warning("persona_stats 模块不可用，跳过统计摘要")
        stats_report = "（统计摘要模块不可用）"
    except Exception as e:
        logger.warning("生成统计摘要失败: %s", e)
        stats_report = "（统计摘要不可用）"

    # ============================================================
    # 3. 加载金句库和核心观点矿脉
    # ============================================================
    veins_path = os.path.join(distill_dir, "核心观点矿脉.md")
    quotes_path = os.path.join(distill_dir, "金句库.md")
    veins_text = _read_file_safe(veins_path, "核心观点矿脉")
    quotes_text = _read_file_safe(quotes_path, "金句库")

    # ============================================================
    # 4. 组装文档
    # ============================================================
    # 核心观点矿脉插入到投资哲学体系章节之后
    philosophy_section = _build_philosophy_section(data, index)
    if veins_text:
        philosophy_section += "\n\n### 核心观点矿脉\n\n"
        philosophy_section += veins_text

    doc_parts: list[str] = [
        "# E大人物画像\n\n",
        "> **说明**：本文档由知识系统自动组装，数据均来自真实蒸馏产物。\n",
        "> 所有数据均可追溯至原始 JSONL 文件，不包含 LLM 编造的内容。\n\n",
        "---\n\n",
        "## 1. 人物身份档案\n\n",
        _build_identity_section(data, index),
        "\n\n## 2. 交易操作时间线\n\n",
        _build_timeline_section(data, index),
        "\n\n## 3. 市场判断演化史\n\n",
        _build_evolution_section(data, index),
        "\n\n## 4. 统计摘要\n\n",
        stats_report,
        "\n\n## 5. 投资哲学体系\n\n",
        philosophy_section,
        "\n\n## 6. 语言风格参考\n\n",
        _build_style_section(quotes_text),
        "\n\n---\n\n## 附录\n\n",
        _build_appendix(data, index),
    ]

    result = "\n".join(doc_parts)
    logger.info("人物画像组装完成，长度: %d 字符", len(result))
    return result


# ============================================================
# 文件写入函数（保留原样）
# ============================================================


def write_persona(content: str, output_dir: str) -> str:
    """将人物画像写入文件。

    Args:
        content: 人物画像 Markdown 内容
        output_dir: 输出目录

    Returns:
        str: 写入的文件路径
    """
    output_path = os.path.join(output_dir, "E大人物画像.md")
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(content)
    logger.info("人物画像已写入: %s (%d 字符)", output_path, len(content))
    return output_path


# ============================================================
# CLI 入口（保留原样）
# ============================================================


def main() -> None:
    """CLI 入口"""
    import argparse
    from scripts.distill.config import get_config

    parser = argparse.ArgumentParser(description="E大人物画像构建工具")
    parser.add_argument(
        "--distill-dir", type=str, default=None,
        help="蒸馏产物目录（默认从 config 读取 paths.output_dir）",
    )
    parser.add_argument(
        "--output-dir", type=str, default=None,
        help="输出目录（默认与 distill-dir 相同）",
    )
    args = parser.parse_args()

    config = get_config()

    # 设置日志
    from scripts.distill.main import setup_logging
    setup_logging(config)

    distill_dir = args.distill_dir or config["paths"]["output_dir"]
    output_dir = args.output_dir or distill_dir

    content = build_persona(config, distill_dir=distill_dir)
    if content:
        write_persona(content, output_dir)
        print(f"人物画像已生成: {os.path.join(output_dir, 'E大人物画像.md')}")
    else:
        print("人物画像生成失败")


if __name__ == "__main__":
    main()