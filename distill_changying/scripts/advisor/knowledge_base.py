"""
E大投资决策助手知识底座模块

读取蒸馏产物（核心观点矿脉.md 和金句库.md），二次压缩后构建 LLM 系统 Prompt。
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

# LLM 行为约束 —— 所有 Prompt 中必须包含（V2：允许推荐品种和份数）
_LLM_CONSTRAINT = """【重要行为约束】
你是ETF拯救世界（E大）的投资顾问，可以基于提供的估值数据推荐具体ETF品种和买卖份数。请严格遵守以下规则：
1. 基于我提供的市场数据和估值指标进行分析，不可编造新的估值数值。
2. 可以推荐具体的ETF品种和买卖份数，但必须基于我已提供的ETF操作建议数据。
3. 不预测市场涨跌方向和幅度，只基于估值分位和E大框架给出配置建议。
4. 遵循E大150份资产配置框架，以"份"为单位给出操作建议。
5. 所有分析必须基于E大的投资框架和当前市场数据。
6. 在结论中必须提醒："以上分析仅供参考，不构成投资建议。"""

# 默认截取金句库的长度（字符数）
_DEFAULT_GOLDEN_QUOTES_LENGTH = 1500


def _read_file_safe(file_path: str, source_name: str) -> tuple[str, str | None]:
    """安全读取文件，返回 (内容, 警告信息)。

    文件不存在或读取失败时返回空字符串和警告信息。

    Args:
        file_path: 文件绝对路径
        source_name: 文件来源描述（用于日志和警告信息）

    Returns:
        tuple[str, str | None]: (文件内容, 警告信息或None)
    """
    if not os.path.isfile(file_path):
        warning = f"{source_name}文件不存在: {file_path}"
        logger.warning(warning)
        return "", warning
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
        if not content.strip():
            warning = f"{source_name}文件内容为空: {file_path}"
            logger.warning(warning)
            return "", warning
        logger.info("成功加载%s: %s (%d 字符)", source_name, file_path, len(content))
        return content, None
    except Exception as e:
        warning = f"读取{source_name}文件失败: {file_path}, 错误: {e}"
        logger.error(warning)
        return "", warning


def _compress_markdown(text: str, max_chars_per_section: int) -> str:
    """对 Markdown 文本按章节做二次压缩。

    策略：
    1. 以 "##" 为分隔符拆分为各章节
    2. 每个章节保留：
       - 标题行（完整保留）
       - 首段内容（最多 max_chars_per_section 字符）
    3. 如果章节是表格格式（如十大核心观点等），保留表格
    4. 总长度超过限制时，优先删减后面的章节

    Args:
        text: 原始 Markdown 文本
        max_chars_per_section: 每个章节最多保留字符数

    Returns:
        str: 压缩后的文本
    """
    if not text.strip():
        return ""

    lines = text.split("\n")
    sections: list[str] = []
    current_section_lines: list[str] = []

    for line in lines:
        # 遇到新的二级标题时，保存前一个章节并开启新章节
        if line.startswith("## "):
            if current_section_lines:
                sections.append("\n".join(current_section_lines))
            current_section_lines = [line]
        else:
            current_section_lines.append(line)

    # 保存最后一个章节
    if current_section_lines:
        sections.append("\n".join(current_section_lines))

    # 如果 "## " 拆分没有匹配任何内容（文本无二级标题），整体作为一个章节处理
    if not sections:
        sections = [text]

    # 对每个章节进行压缩
    compressed_sections: list[str] = []
    for section in sections:
        section_lines = section.split("\n")
        if not section_lines:
            continue

        # 标题行完整保留
        title = section_lines[0]
        body_lines = section_lines[1:]

        # 空行过滤开头和结尾
        while body_lines and not body_lines[0].strip():
            body_lines.pop(0)
        while body_lines and not body_lines[-1].strip():
            body_lines.pop()

        if not body_lines:
            # 没有正文的章节只保留标题
            compressed_sections.append(title)
            continue

        # 判断是否为表格格式（第一行有效非空行以 "|" 开头）
        first_content_line = body_lines[0].strip() if body_lines else ""
        is_table = first_content_line.startswith("|")

        if is_table:
            # 表格章节：保留标题 + 完整表格
            table_lines = [title]
            for bl in body_lines:
                if bl.strip().startswith("|") or (
                    bl.strip()
                    and set(bl.strip()).issubset({"|", "-", ":", " "})
                    and "|" in bl.strip()
                ):
                    table_lines.append(bl)
                elif not bl.strip():
                    # 表格结束标志：空行后不再处理
                    break
                else:
                    # 非表格行，表格结束
                    break
            compressed_sections.append("\n".join(table_lines))
        else:
            # 普通章节：保留标题 + 截断后的正文
            body_text = "\n".join(body_lines)
            if len(body_text) > max_chars_per_section:
                # 按字符截断，尽量在自然边界（句号/换行）处截断
                truncated = body_text[:max_chars_per_section]
                # 寻找最后一个合适的截断点
                for delimiter in ["\n\n", "\n", "。", "；", "，"]:
                    last_pos = truncated.rfind(delimiter)
                    if last_pos > max_chars_per_section * 0.6:
                        truncated = truncated[: last_pos + len(delimiter)]
                        break
                compressed_sections.append(f"{title}\n{truncated}")
            else:
                compressed_sections.append(section)

    return "\n\n".join(compressed_sections)


def load_core_veins(config: dict[str, Any]) -> str:
    """加载并压缩核心观点矿脉文件。

    从 config["knowledge_base"]["core_veins"] 读取文件路径，
    对 7 个章节各压缩到约 config["compression"]["max_chars_per_section"] 字。

    压缩策略：
    - 按 "##" 二级标题分割章节
    - 每个章节保留标题 + 前 N 字内容
    - 总字数控制在约 config["compression"]["max_total_tokens"] * 2 字符

    Args:
        config: 配置字典（由 get_config 返回）

    Returns:
        str: 压缩后的核心观点文本
    """
    file_path = config["knowledge_base"]["core_veins"]
    max_chars = config["compression"]["max_chars_per_section"]

    content, warning = _read_file_safe(file_path, "核心观点矿脉")
    if warning:
        logger.warning("核心观点矿脉加载警告: %s", warning)

    if not content:
        return ""

    # 跳过文件开头的摘要行（第一段引言，即 "好的，作为顶级知识管理专家..."）
    # 找到第一个 "## " 标题的位置
    first_heading_idx = content.find("\n## ")
    if first_heading_idx != -1:
        # 从第一个 ## 标题之前最近的 --- 分隔符后开始
        hr_idx = content.rfind("---", 0, first_heading_idx)
        if hr_idx != -1:
            content = content[hr_idx:].lstrip("-").strip()

    compressed = _compress_markdown(content, max_chars)
    logger.info(
        "核心观点矿脉压缩完成: %d -> %d 字符",
        len(content),
        len(compressed),
    )
    return compressed


def load_golden_quotes(config: dict[str, Any]) -> str:
    """加载金句库文件。

    从 config["knowledge_base"]["golden_quotes"] 读取文件路径，
    截取前约 1500 字内容用于风格注入。

    Args:
        config: 配置字典（由 get_config 返回）

    Returns:
        str: 金句库文本（截断版）
    """
    file_path = config["knowledge_base"]["golden_quotes"]

    content, warning = _read_file_safe(file_path, "金句库")
    if warning:
        logger.warning("金句库加载警告: %s", warning)

    if not content:
        return ""

    # 截取前 N 字符
    max_chars = _DEFAULT_GOLDEN_QUOTES_LENGTH
    if len(content) <= max_chars:
        return content

    # 在自然边界截断
    truncated = content[:max_chars]
    for delimiter in ["\n\n", "\n", "。", "；", "，"]:
        last_pos = truncated.rfind(delimiter)
        if last_pos > max_chars * 0.7:
            truncated = truncated[: last_pos + len(delimiter)]
            break

    logger.info("金句库截断: %d -> %d 字符", len(content), len(truncated))
    return truncated


def load_recent_operations(
    config: dict[str, Any], max_ops: int = 10
) -> list[dict[str, Any]]:
    """从操作时间线.jsonl 读取最近的操作记录。

    文件路径基于 config["knowledge_base"]["core_veins"] 或
    config["knowledge_base"]["golden_quotes"] 的父目录（两者在同一目录下）。

    Args:
        config: 配置字典
        max_ops: 最多返回的记录数

    Returns:
        list[dict]: 操作记录列表，按时间倒排，最多 max_ops 条
    """
    # 从核心观点矿脉或金句库路径推断蒸馏产物目录
    veins_path = config.get("knowledge_base", {}).get("core_veins", "")
    quotes_path = config.get("knowledge_base", {}).get("golden_quotes", "")
    base_dir = os.path.dirname(veins_path or quotes_path)
    if not base_dir:
        logger.warning("无法确定蒸馏产物目录，knowledge_base 配置缺少 core_veins 和 golden_quotes")
        return []

    ops_path = os.path.join(base_dir, "操作时间线.jsonl")
    if not os.path.isfile(ops_path):
        logger.warning("操作时间线文件不存在: %s", ops_path)
        return []

    records: list[dict[str, Any]] = []
    try:
        with open(ops_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError as e:
                        logger.warning("操作时间线 JSON 解析失败，跳过该行: %s", e)
    except Exception as e:
        logger.error("读取操作时间线文件失败: %s, 错误: %s", ops_path, e)
        return []

    if not records:
        logger.info("操作时间线文件为空: %s", ops_path)
        return []

    # 按 time 字段倒排，最新的在前
    records.sort(key=lambda r: r.get("time", ""), reverse=True)
    result = records[:max_ops]
    logger.info("加载操作时间线: %d 条记录（共 %d 条）", len(result), len(records))
    return result


def load_recent_observations(
    config: dict[str, Any], max_obs: int = 15
) -> list[dict[str, Any]]:
    """从近期判断库.jsonl 读取最近的市场判断。

    文件路径基于 config["knowledge_base"]["core_veins"] 或
    config["knowledge_base"]["golden_quotes"] 的父目录（两者在同一目录下）。

    Args:
        config: 配置字典
        max_obs: 最多返回的判断数

    Returns:
        list[dict]: 市场判断列表，按时间倒排，最多 max_obs 条
    """
    veins_path = config.get("knowledge_base", {}).get("core_veins", "")
    quotes_path = config.get("knowledge_base", {}).get("golden_quotes", "")
    base_dir = os.path.dirname(veins_path or quotes_path)
    if not base_dir:
        logger.warning("无法确定蒸馏产物目录，knowledge_base 配置缺少 core_veins 和 golden_quotes")
        return []

    obs_path = os.path.join(base_dir, "近期判断库.jsonl")
    if not os.path.isfile(obs_path):
        logger.warning("近期判断库文件不存在: %s", obs_path)
        return []

    records: list[dict[str, Any]] = []
    try:
        with open(obs_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError as e:
                        logger.warning("近期判断库 JSON 解析失败，跳过该行: %s", e)
    except Exception as e:
        logger.error("读取近期判断库文件失败: %s, 错误: %s", obs_path, e)
        return []

    if not records:
        logger.info("近期判断库文件为空: %s", obs_path)
        return []

    # 按 time 字段倒排，最新的在前
    records.sort(key=lambda r: r.get("time", ""), reverse=True)
    result = records[:max_obs]
    logger.info("加载近期判断库: %d 条记录（共 %d 条）", len(result), len(records))
    return result


def _format_operations_for_prompt(operations: list[dict[str, Any]]) -> str:
    """将操作记录格式化为易读的 Prompt 文本。

    Args:
        operations: 操作记录列表

    Returns:
        str: 格式化后的文本
    """
    lines: list[str] = []
    for op in operations:
        plan = op.get("plan", "")
        action = op.get("action", "")
        fund = op.get("fund", "")
        code = op.get("code", "")
        yield_pct = op.get("yield_pct", "")
        memo = op.get("memo", "")
        lines.append(
            f"- {plan}计划 {action}{fund}({code}) 收益率{yield_pct}% {memo}"
        )
    return "\n".join(lines)


def _format_observations_for_prompt(observations: list[dict[str, Any]]) -> str:
    """将市场判断格式化为易读的 Prompt 文本。

    Args:
        observations: 市场判断列表

    Returns:
        str: 格式化后的文本
    """
    lines: list[str] = []
    for obs in observations:
        opinion = obs.get("opinion", "")
        lines.append(f"- {opinion}")
    return "\n".join(lines)


def build_system_prompt(config: dict[str, Any]) -> str:
    """构建 LLM 系统 Prompt。

    组装核心观点 + 金句库 + LLM 行为约束，构建完整的 system prompt。

    结构：
    1. 角色设定："你是ETF拯救世界(E大)的分析助手..."
    2. 投资哲学（从核心观点矿脉提取）
    3. 估值框架（五区间温度计 + 仓位映射）
    4. 操作纪律
    5. 语言风格（从金句库注入）
    6. 行为约束："你是分析解说员，不是投资决策者..."

    Args:
        config: 配置字典（由 get_config 返回）

    Returns:
        str: 完整的系统 Prompt 文本
    """
    core_veins = load_core_veins(config)
    golden_quotes = load_golden_quotes(config)

    # 构建估值框架描述
    thresholds = config["valuation"]["thresholds"]
    position_mapping = config["valuation"]["position_mapping"]

    valuation_framework = _build_valuation_framework(thresholds, position_mapping)

    # 角色设定（V2：升级为投资顾问，可推荐ETF品种和份数）
    role_setting = (
        "你是ETF拯救世界（E大）的投资顾问，"
        "深谙E大的投资哲学和150份资产配置框架。"
        "你能够基于估值数据推荐具体的ETF品种和买卖份数，"
        "用E大的框架解读市场估值状态并给出可操作的资产配置建议。"
    )

    # 从核心观点矿脉提取投资哲学摘要
    philosophy_summary = _extract_philosophy_summary(core_veins)

    # 操作纪律（V2：增加ETF操作相关纪律）
    discipline = (
        "【操作纪律】\n"
        "1. 投资是概率游戏，不预测市场涨跌，只计算估值概率。\n"
        "2. 资产配置决定90%的收益，通过多品种分散配置应对不确定性。\n"
        "3. 逆向投资是超额收益的核心来源，人弃我取、人取我予。\n"
        "4. 控制回撤比追求收益更重要，熊市不赔钱、牛市跟上指数。\n"
        "5. 建立体系、机械执行，用纪律克服人性弱点。\n"
        "6. 永远保留现金储备，不空仓也不满仓。\n"
        "7. 遵循150份资产配置框架，以'份'为单位操作，不超单品种上限。"
    )

    # 免责声明
    disclaimer = config.get("disclaimer", "以上分析仅供参考，不构成投资建议。")

    # 组装系统 Prompt
    parts: list[str] = [
        role_setting,
        "",
        "---",
        "",
        "【投资哲学】",
        philosophy_summary,
        "",
        "---",
        "",
        "【估值框架】",
        valuation_framework,
        "",
        "---",
        "",
        discipline,
        "",
        "---",
        "",
        "【语言风格参考】",
        "请参考以下E大的经典语录，在分析中融入类似的口语化表达风格，但不要逐字照搬：",
        golden_quotes,
        "",
        "---",
        "",
        _LLM_CONSTRAINT,
        "",
        "---",
        "",
        f"【免责声明】{disclaimer}",
    ]

    system_prompt = "\n".join(parts)
    logger.info("系统 Prompt 构建完成，总长度: %d 字符", len(system_prompt))
    return system_prompt


def _build_valuation_framework(
    thresholds: dict[str, int], position_mapping: dict[str, dict[str, int]]
) -> str:
    """构建估值框架描述文本。

    Args:
        thresholds: 估值阈值配置
        position_mapping: 仓位映射配置

    Returns:
        str: 估值框架描述文本
    """
    lines = [
        "E大的五区间估值温度计体系：",
        "",
        f"| 区间 | PE历史分位 | 定性 | 仓位建议（股/债/现） |",
        f"|------|-----------|------|---------------------|",
        f"| 钻石坑 | < {thresholds['diamond_pit']}% | 极度低估，历史大底 | "
        f"{position_mapping['diamond_pit']['stock']}/{position_mapping['diamond_pit']['bond']}/{position_mapping['diamond_pit']['cash']} |",
        f"| 低估区 | {thresholds['diamond_pit']}%-{thresholds['undervalued']}% | 估值偏低，值得关注 | "
        f"{position_mapping['undervalued']['stock']}/{position_mapping['undervalued']['bond']}/{position_mapping['undervalued']['cash']} |",
        f"| 正常区 | {thresholds['undervalued']}%-{thresholds['normal']}% | 估值合理，持有为主 | "
        f"{position_mapping['normal']['stock']}/{position_mapping['normal']['bond']}/{position_mapping['normal']['cash']} |",
        f"| 高估区 | {thresholds['normal']}%-{thresholds['overvalued']}% | 估值偏高，逐步减仓 | "
        f"{position_mapping['overvalued']['stock']}/{position_mapping['overvalued']['bond']}/{position_mapping['overvalued']['cash']} |",
        f"| 泡沫区 | > {thresholds['overvalued']}% | 极度高估，风险极大 | "
        f"{position_mapping['bubble']['stock']}/{position_mapping['bubble']['bond']}/{position_mapping['bubble']['cash']} |",
        "",
        "核心原则：温度越低，仓位越重；温度越高，仓位越轻。",
    ]
    return "\n".join(lines)


def _extract_philosophy_summary(core_veins: str) -> str:
    """从核心观点矿脉中提取投资哲学摘要。

    如果没有加载到核心观点，返回默认的投资哲学描述。

    Args:
        core_veins: 压缩后的核心观点文本

    Returns:
        str: 投资哲学摘要
    """
    if not core_veins:
        return (
            "E大的投资哲学以价值投资为基础，坚持逆向投资、资产配置和指数投资。\n"
            "核心理念：不预测市场，而是通过估值作为锚，在低估时买入、高估时卖出。\n"
            "强调控制回撤、保持耐心、机械执行策略。"
        )

    # 从核心观点中提取投资哲学相关内容（命题一和十大核心观点的前几条）
    # 找到 "## 1. 核心命题聚类" 和 "## 2. 十大核心观点"
    philosophy_parts: list[str] = []

    # 提取命题一：投资哲学与人性博弈
    prop1_start = core_veins.find("**命题一：投资哲学与人性博弈**")
    if prop1_start != -1:
        prop1_end = core_veins.find("**命题二", prop1_start)
        if prop1_end == -1:
            prop1_end = core_veins.find("## 2.", prop1_start)
        if prop1_end != -1:
            prop1_text = core_veins[prop1_start:prop1_end].strip()
            philosophy_parts.append(prop1_text)

    # 提取十大核心观点中的前 5 条
    table_start = core_veins.find("## 2. 十大核心观点")
    if table_start != -1:
        # 提取表格内容
        table_end = core_veins.find("## 3.", table_start)
        if table_end == -1:
            table_end = len(core_veins)
        table_text = core_veins[table_start:table_end]
        # 如果表格太长，截取一部分
        if len(table_text) > 600:
            # 保留标题 + 前几行
            lines = table_text.split("\n")
            kept = []
            for line in lines:
                kept.append(line)
                if len(kept) >= 10:  # 标题行 + 表头 + ~5条观点
                    break
            table_text = "\n".join(kept)
        philosophy_parts.append(table_text)

    if philosophy_parts:
        return "\n\n".join(philosophy_parts)

    return core_veins[:500]


def load_knowledge_base(config: dict[str, Any]) -> dict[str, Any]:
    """加载知识底座（主入口）。

    返回一个字典，包含所有构建好的 Prompt 片段：
    {
        "system_prompt": str,          # 完整系统 Prompt
        "core_veins_compressed": str,  # 压缩后的核心观点
        "golden_quotes_snippet": str,  # 金句片段
        "loaded": bool,                # 是否成功加载
        "warning": str | None,         # 加载警告信息（文件缺失时）
    }

    Args:
        config: 配置字典（由 get_config 返回）

    Returns:
        dict: 知识底座数据
    """
    result: dict[str, Any] = {
        "system_prompt": "",
        "core_veins_compressed": "",
        "golden_quotes_snippet": "",
        "loaded": False,
        "warning": None,
    }

    warnings: list[str] = []

    # 加载核心观点矿脉
    core_veins = load_core_veins(config)
    result["core_veins_compressed"] = core_veins

    # 加载金句库
    golden_quotes = load_golden_quotes(config)
    result["golden_quotes_snippet"] = golden_quotes

    # 收集警告信息
    if not core_veins:
        warnings.append("核心观点矿脉加载失败")
    if not golden_quotes:
        warnings.append("金句库加载失败")

    if warnings:
        result["warning"] = "; ".join(warnings)
        logger.warning("知识底座加载部分失败: %s", result["warning"])
    else:
        result["loaded"] = True
        logger.info("知识底座加载成功")

    # 构建系统 Prompt
    result["system_prompt"] = _assemble_prompt(
        config=config,
        core_veins=core_veins,
        golden_quotes=golden_quotes,
    )

    return result


def _assemble_prompt(
    config: dict[str, Any],
    core_veins: str,
    golden_quotes: str,
) -> str:
    """内部组装系统 Prompt（避免 load_core_veins/load_golden_quotes 被重复调用）。

    Args:
        config: 配置字典
        core_veins: 已压缩的核心观点文本
        golden_quotes: 已截断的金句库文本

    Returns:
        str: 完整系统 Prompt
    """
    thresholds = config["valuation"]["thresholds"]
    position_mapping = config["valuation"]["position_mapping"]

    valuation_framework = _build_valuation_framework(thresholds, position_mapping)

    role_setting = (
        "你是ETF拯救世界（E大）的投资顾问，"
        "深谙E大的投资哲学和150份资产配置框架。"
        "你能够基于估值数据推荐具体的ETF品种和买卖份数，"
        "用E大的框架解读市场估值状态并给出可操作的资产配置建议。"
    )

    philosophy_summary = _extract_philosophy_summary(core_veins)

    discipline = (
        "【操作纪律】\n"
        "1. 投资是概率游戏，不预测市场涨跌，只计算估值概率。\n"
        "2. 资产配置决定90%的收益，通过多品种分散配置应对不确定性。\n"
        "3. 逆向投资是超额收益的核心来源，人弃我取、人取我予。\n"
        "4. 控制回撤比追求收益更重要，熊市不赔钱、牛市跟上指数。\n"
        "5. 建立体系、机械执行，用纪律克服人性弱点。\n"
        "6. 永远保留现金储备，不空仓也不满仓。\n"
        "7. 遵循150份资产配置框架，以'份'为单位操作，不超单品种上限。"
    )

    disclaimer = config.get("disclaimer", "以上分析仅供参考，不构成投资建议。")

    # 新增：加载近期操作和判断
    recent_ops = load_recent_operations(
        config,
        max_ops=config.get("persona", {}).get("max_operations_in_prompt", 10),
    )
    recent_obs = load_recent_observations(
        config,
        max_obs=config.get("persona", {}).get("max_observations_in_prompt", 15),
    )

    recent_operations_section = ""
    if recent_ops:
        recent_operations_section = (
            "\n\n---\n\n"
            "【E大近期操作动态】\n"
            + _format_operations_for_prompt(recent_ops)
        )

    recent_observations_section = ""
    if recent_obs:
        recent_observations_section = (
            "\n\n---\n\n"
            "【E大近期市场判断】\n"
            + _format_observations_for_prompt(recent_obs)
        )

    golden_quotes_section = ""
    if golden_quotes:
        golden_quotes_section = (
            "\n\n---\n\n"
            "【语言风格参考】\n"
            "请参考以下E大的经典语录，在分析中融入类似的口语化表达风格，但不要逐字照搬：\n"
            + golden_quotes
        )

    parts: list[str] = [
        role_setting,
        "",
        "---",
        "",
        "【投资哲学】",
        philosophy_summary,
        "",
        "---",
        "",
        "【估值框架】",
        valuation_framework,
        "",
        "---",
        "",
        discipline,
        recent_operations_section,
        recent_observations_section,
        golden_quotes_section,
        "",
        "---",
        "",
        _LLM_CONSTRAINT,
        "",
        "---",
        "",
        f"【免责声明】{disclaimer}",
    ]

    return "\n".join(parts)