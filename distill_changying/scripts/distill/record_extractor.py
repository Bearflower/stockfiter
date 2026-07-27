"""
操作记录与观点提取模块

从 state.json 中读取已提取的结构化数据，生成：
- 操作时间线.jsonl：发车帖中的买卖操作记录
- 近期判断库.jsonl：微博精选中的独立观点/市场判断

作为原始摘要和观点库之间的中间产物，保留被通用观点提取过滤掉的
近期操作和市场判断信息。
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

from scripts.distill.file_handler import get_blog_identifier, list_blog_files

logger = logging.getLogger(__name__)

# 操作记录的默认输出路径
_OPERATIONS_FILE = "操作时间线.jsonl"
# 近期判断的输出路径
_OBSERVATIONS_FILE = "近期判断库.jsonl"


def _extract_blog_date_from_filename(filename: str) -> str:
    """从文件名中提取博文的实际发布日期。

    支持多种文件名格式，按优先级尝试匹配：
    1. YYYY-MM-DD 明确日期（如 "2019-03-07 【文字发车】.md"）
    2. YYYY年M月 或 YYYY年M-D月范围（取范围的首月首日，如 "2015年9-12月..."）
    3. YYYY[-YYYY]【M.D 或 YYYY【MM.DD 括号开头（取第一个年份和括号内月份首日）
    4. YYYY年 年份开头（仅提取年份，默认该年1月1日）

    Args:
        filename: 文件名（含扩展名）

    Returns:
        str: "YYYY-MM-DD" 格式的日期，无法提取时返回空字符串

    Examples:
        >>> _extract_blog_date_from_filename("2019-03-07 【文字发车】.md")
        '2019-03-07'
        >>> _extract_blog_date_from_filename("2026年1月长赢指数投资计划（一）.md")
        '2026-01-01'
        >>> _extract_blog_date_from_filename("2020【06.01-06.07】ETF拯救世界.md")
        '2020-06-01'
        >>> _extract_blog_date_from_filename("2018【9.1-9.23】ETF拯救世界.md")
        '2018-09-01'
        >>> _extract_blog_date_from_filename("2019-2020【12.30-01.05】ETF拯救世界.md")
        '2019-12-01'
        >>> _extract_blog_date_from_filename("2020【1.20-2.2】.md")
        '2020-01-01'
        >>> _extract_blog_date_from_filename("2021【2.15-2.21】.md")
        '2021-02-01'
        >>> _extract_blog_date_from_filename("2015年9-12月ETF拯救世界微博精选.md")
        '2015-09-01'
        >>> _extract_blog_date_from_filename("2019年1月文字发车.md")
        '2019-01-01'
        >>> _extract_blog_date_from_filename("2020年12月ETF计划（三）.md")
        '2020-12-01'
        >>> _extract_blog_date_from_filename("各位长赢的朋友，大家新春快乐！.md")
        ''
        >>> _extract_blog_date_from_filename("第三轮长赢计划三周年业绩回顾 2018-08-03.md")
        '2018-08-03'
        >>> _extract_blog_date_from_filename("长赢指数投资计划2019年上半年回顾 2019-07-10.md")
        '2019-07-10'
        >>> _extract_blog_date_from_filename("各位长赢的朋友，大家新春快乐！ 2019-02-04.md")
        '2019-02-04'
        >>> _extract_blog_date_from_filename("2018【10.08-10.14】ETF拯救世界微博.md")
        '2018-10-01'
        >>> _extract_blog_date_from_filename("2019-2020【12.30-01.05】ETF拯救世界.md")
        '2019-12-01'
    """
    # 模式1：YYYY-MM-DD 明确日期（校验月、日范围）
    m = re.search(r'(\d{4})-(\d{2})-(\d{2})', filename)
    if m:
        year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1 <= month <= 12 and 1 <= day <= 31:
            return f"{year:04d}-{month:02d}-{day:02d}"

    # 模式2：YYYY年M月 或 YYYY年M-D月范围（取范围的首月首日）
    m = re.search(r'(\d{4})年(\d{1,2})(?:[~\-\u2013]\d{1,2})?月', filename)
    if m:
        year, month = int(m.group(1)), int(m.group(2))
        if 1 <= month <= 12:
            return f"{year:04d}-{month:02d}-01"

    # 模式3：YYYY[-YYYY]【M.D 或 YYYY【MM.DD（取第一个年份和括号内月份首日）
    m = re.search(r'(\d{4})(?:[-\u2013]\d{1,4})?【\s*(\d{1,2})\.(\d{1,2})', filename)
    if m:
        year, month = int(m.group(1)), int(m.group(2))
        if 1 <= month <= 12:
            return f"{year:04d}-{month:02d}-01"

    # 模式4：YYYY年（仅提取年份，默认1月）
    m = re.search(r'^(\d{4})年', filename)
    if m:
        return f"{m.group(1)}-01-01"

    return ""


def _build_blog_date_map(blog_dir: str) -> dict[str, str]:
    """扫描博客目录，构建文件标识符到实际发布日期的映射。

    对每个 .md 文件，使用 get_blog_identifier 计算 hash ID，
    再用 _extract_blog_date_from_filename 从文件名提取实际发布日期。

    Args:
        blog_dir: 博客源文件目录的绝对路径

    Returns:
        dict[str, str]: {file_id: "YYYY-MM-DD"} 映射字典，
                        无法提取日期的文件值为空字符串
    """
    date_map: dict[str, str] = {}
    files = list_blog_files(blog_dir)
    for filepath in files:
        file_id = get_blog_identifier(filepath)
        filename = os.path.basename(filepath)
        date_str = _extract_blog_date_from_filename(filename)
        date_map[file_id] = date_str
        if date_str:
            logger.debug("文件 %s -> 日期 %s", filename, date_str)
        else:
            logger.debug("文件 %s -> 无法提取日期，将使用 processed_at 回退", filename)

    logger.info(
        "构建博客日期映射: %d 个文件, %d 个成功提取日期",
        len(files),
        sum(1 for v in date_map.values() if v),
    )
    return date_map


def _extract_operations(
    state: dict[str, Any],
    blog_date_map: dict[str, str],
) -> list[dict]:
    """内部函数：使用预构建的日期映射提取操作记录。

    Args:
        state: 处理状态字典
        blog_date_map: 文件标识符到日期的映射

    Returns:
        list[dict]: 操作记录列表
    """
    operations: list[dict] = []

    for file_id, entry in state.items():
        ops = entry.get("operations")
        if not ops or not isinstance(ops, list):
            continue

        # 优先使用从文件名提取的实际发布日期，无法提取时回退 processed_at
        extracted_date = blog_date_map.get(file_id, "")
        record_time = extracted_date if extracted_date else entry.get("processed_at", "")

        for op in ops:
            if not isinstance(op, dict):
                continue
            op_record = {
                "plan": op.get("plan", "无"),
                "action": op.get("action", ""),
                "fund": op.get("fund", ""),
                "code": op.get("code", ""),
                "yield_pct": op.get("yield_pct", 0),
                "quantity": op.get("quantity", 0),
                "memo": op.get("memo", ""),
                "time": record_time,
                "file_id": file_id,
            }
            operations.append(op_record)

    # 按时间由新到旧排序
    operations.sort(key=lambda x: x.get("time", ""), reverse=True)
    logger.info("从 state 中提取了 %d 条操作记录", len(operations))
    return operations


def _extract_observations(
    state: dict[str, Any],
    blog_date_map: dict[str, str],
) -> list[dict]:
    """内部函数：使用预构建的日期映射提取近期判断。

    Args:
        state: 处理状态字典
        blog_date_map: 文件标识符到日期的映射

    Returns:
        list[dict]: 观点记录列表
    """
    observations: list[dict] = []

    for file_id, entry in state.items():
        opinions = entry.get("weibo_opinions")
        if not opinions or not isinstance(opinions, list):
            continue

        # 优先使用从文件名提取的实际发布日期，无法提取时回退 processed_at
        extracted_date = blog_date_map.get(file_id, "")
        record_time = extracted_date if extracted_date else entry.get("processed_at", "")

        for opinion in opinions:
            if not isinstance(opinion, str) or not opinion.strip():
                continue
            obs_record = {
                "opinion": opinion.strip(),
                "source_type": "observation",
                "topic": entry.get("topic", ""),
                "time": record_time,
                "file_id": file_id,
            }
            observations.append(obs_record)

    # 按时间由新到旧排序
    observations.sort(key=lambda x: x.get("time", ""), reverse=True)
    logger.info("从 state 中提取了 %d 条微博观点", len(observations))
    return observations


def extract_operations_from_state(
    state: dict[str, Any],
    blog_dir: str | None = None,
) -> list[dict]:
    """从 state.json 中提取所有发车帖操作记录。

    Args:
        state: 处理状态字典（已加载的 state.json）
        blog_dir: 博客源文件目录，传入后将从文件名提取实际发布日期

    Returns:
        list[dict]: 操作记录列表，按时间排序（最新的在前）
    """
    blog_date_map = _build_blog_date_map(blog_dir) if blog_dir else {}
    return _extract_operations(state, blog_date_map)


def extract_observations_from_state(
    state: dict[str, Any],
    blog_dir: str | None = None,
) -> list[dict]:
    """从 state.json 中提取所有微博精选的独立观点/市场判断。

    Args:
        state: 处理状态字典
        blog_dir: 博客源文件目录，传入后将从文件名提取实际发布日期

    Returns:
        list[dict]: 观点记录列表
    """
    blog_date_map = _build_blog_date_map(blog_dir) if blog_dir else {}
    return _extract_observations(state, blog_date_map)


def save_operations(
    operations: list[dict],
    output_dir: str,
) -> str:
    """保存操作记录到 JSONL 文件。

    Args:
        operations: 操作记录列表
        output_dir: 输出目录

    Returns:
        str: 输出文件的绝对路径
    """
    output_path = os.path.join(output_dir, _OPERATIONS_FILE)
    os.makedirs(output_dir, exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as f:
        for op in operations:
            f.write(json.dumps(op, ensure_ascii=False) + "\n")

    logger.info("操作时间线已写入: %s（共 %d 条）", output_path, len(operations))
    return output_path


def save_observations(
    observations: list[dict],
    output_dir: str,
) -> str:
    """保存近期判断到 JSONL 文件。

    Args:
        observations: 观点记录列表
        output_dir: 输出目录

    Returns:
        str: 输出文件的绝对路径
    """
    output_path = os.path.join(output_dir, _OBSERVATIONS_FILE)
    os.makedirs(output_dir, exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as f:
        for obs in observations:
            f.write(json.dumps(obs, ensure_ascii=False) + "\n")

    logger.info("近期判断库已写入: %s（共 %d 条）", output_path, len(observations))
    return output_path


def load_operations(output_dir: str) -> list[dict]:
    """从 JSONL 文件加载操作记录。

    Args:
        output_dir: 输出目录

    Returns:
        list[dict]: 操作记录列表
    """
    filepath = os.path.join(output_dir, _OPERATIONS_FILE)
    if not os.path.isfile(filepath):
        logger.warning("操作时间线文件不存在: %s", filepath)
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
                logger.warning("跳过无法解析的操作记录: %s", line[:80])

    logger.info("已加载 %d 条操作记录", len(records))
    return records


def load_observations(output_dir: str) -> list[dict]:
    """从 JSONL 文件加载近期判断。

    Args:
        output_dir: 输出目录

    Returns:
        list[dict]: 观点列表
    """
    filepath = os.path.join(output_dir, _OBSERVATIONS_FILE)
    if not os.path.isfile(filepath):
        logger.warning("近期判断库文件不存在: %s", filepath)
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
                logger.warning("跳过无法解析的近期判断: %s", line[:80])

    logger.info("已加载 %d 条近期判断", len(records))
    return records


def extract_all_and_save(
    state: dict[str, Any],
    output_dir: str,
    blog_dir: str | None = None,
) -> tuple[str, str]:
    """从 state 中提取操作记录和近期判断并保存。

    在增量蒸馏的 update 流程中调用此函数。

    Args:
        state: 处理状态字典
        output_dir: 输出目录
        blog_dir: 博客源文件目录，传入后将使用实际发布日期而非处理时间

    Returns:
        tuple[str, str]: (操作时间线路径, 近期判断库路径)
    """
    # 提前构建日期映射，避免两个提取函数重复扫描目录
    blog_date_map = _build_blog_date_map(blog_dir) if blog_dir else {}

    operations = _extract_operations(state, blog_date_map)
    observations = _extract_observations(state, blog_date_map)

    op_path = save_operations(operations, output_dir)
    obs_path = save_observations(observations, output_dir)

    return op_path, obs_path