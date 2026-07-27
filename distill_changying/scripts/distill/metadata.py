"""
元数据管理模块

负责生成、读取和写入 .meta.yaml 元数据文件。
记录蒸馏过程的统计信息、时间戳和产物路径，
供外部工具（如调度器）追踪知识库的更新状态。
"""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Any

logger = logging.getLogger(__name__)

# 统一时区：北京时间
TZ = ZoneInfo("Asia/Shanghai")

# 日期提取正则：匹配 "20xx年xx月" 或 "20xx-xx" 等格式
_DATE_PATTERN = re.compile(r"20\d{2}[年.-]?\d{0,2}")


def extract_last_blog_date(blog_files: list[str]) -> str:
    """从文件名列表中提取最大的博客日期。

    对每个文件路径用正则提取形如 "2026年05"、"2026-05" 的日期片段，
    归一化为 "YYYY-MM" 格式后返回最大值。

    Args:
        blog_files: 博客文件绝对路径列表

    Returns:
        str: 最大日期，格式为 "YYYY-MM"（如 "2026-05"），
             无法提取时返回空字符串
    """
    best_year = 0
    best_month = 0

    for filepath in blog_files:
        basename = os.path.basename(filepath)
        match = _DATE_PATTERN.search(basename)
        if not match:
            continue

        date_str = match.group(0)
        # 统一标准化为数字
        digits = re.sub(r"[^0-9]", "", date_str)

        year, month = 0, 0
        if len(digits) > 4:
            # "202605" -> year=2026, month=5；"20261" -> year=2026, month=1
            year = int(digits[:4])
            month = int(digits[4:])
        elif len(digits) == 4:
            # "2026" -> year=2026, month=0
            year = int(digits)
            month = 0

        # 更新最大值：先按年份比较，再按月份比较
        if year > best_year or (year == best_year and month > best_month):
            best_year = year
            best_month = month

    if best_year == 0:
        logger.warning("未能从 %d 个文件名中提取出有效日期", len(blog_files))
        return ""

    result = f"{best_year:04d}-{best_month:02d}" if best_month > 0 else f"{best_year:04d}"
    logger.info("提取最大博客日期: %s（共扫描 %d 个文件）", result, len(blog_files))
    return result


def generate_meta_yaml(
    config: dict[str, Any],
    total_files: int,
    summarized_count: int,
    new_count: int,
    last_blog_date: str,
    summarize_elapsed: float,
    analyze_elapsed: float,
) -> str:
    """生成 .meta.yaml 格式的元数据内容。

    Args:
        config: 完整的配置字典
        total_files: 博客文件总数
        summarized_count: 已摘要处理的文件数
        new_count: 本次新增处理的文件数
        last_blog_date: 最新博客日期（如 "2026-05"）
        summarize_elapsed: 第一层蒸馏耗时（秒），无摘要时为 0
        analyze_elapsed: 第二层蒸馏耗时（秒），无分析时为 0

    Returns:
        str: YAML 格式的元数据字符串
    """
    now = datetime.now(TZ).isoformat()
    paths = config["paths"]
    api_config = config["api"]
    pending_count = max(total_files - summarized_count, 0)

    # 计算最近博客文件名
    last_blog_file = ""
    if last_blog_date:
        last_blog_file = f"（详见 blog_dir 目录，日期: {last_blog_date}）"

    lines = [
        "meta:",
        '  version: "1.0"',
        '  generated_by: "scripts/distill/main.py"',
        f"  generated_at: \"{now}\"",
        "",
        "blog_stats:",
        f"  total_files: {total_files}",
        f"  summarized_count: {summarized_count}",
        f"  pending_count: {pending_count}",
        f"  new_since_last_update: {new_count}",
        f'  last_blog_file: "{last_blog_file}"',
        f'  last_blog_date: "{last_blog_date}"',
        "",
        "distillation:",
        f"  last_summarize_time: \"{now if summarize_elapsed > 0 else 'N/A'}\"",
        f"  last_analyze_time: \"{now if analyze_elapsed > 0 else 'N/A'}\"",
        f"  first_layer_elapsed_seconds: {summarize_elapsed:.1f}",
        f"  second_layer_elapsed_seconds: {analyze_elapsed:.1f}",
        "",
        "products:",
        f'  blog_index: "{os.path.relpath(os.path.join(paths["output_dir"], "blog-index.csv"), _get_project_root_from_config(config))}"',
        f'  core_veins: "{os.path.relpath(os.path.join(paths["output_dir"], "核心观点矿脉.md"), _get_project_root_from_config(config))}"',
        f'  golden_quotes: "{os.path.relpath(os.path.join(paths["output_dir"], "金句库.md"), _get_project_root_from_config(config))}"',
        f'  state_file: "{os.path.relpath(paths["state_file"], _get_project_root_from_config(config))}"',
        f'  readme: "{os.path.relpath(os.path.join(paths["output_dir"], "README.md"), _get_project_root_from_config(config))}"',
        "",
        "api_used:",
        f'  model: "{api_config.get("model", "N/A")}"',
        f'  deep_model: "{api_config.get("deep_model", "N/A")}"',
        "",
    ]

    return "\n".join(lines)


def _get_project_root_from_config(config: dict[str, Any]) -> str:
    """从配置推导项目根目录。

    利用 output_dir 的相对位置反推：output_dir 通常是
    "{project_root}/docs/distilled"，向上两级即为项目根。

    Args:
        config: 配置字典

    Returns:
        str: 项目根目录绝对路径
    """
    output_dir = config["paths"]["output_dir"]
    # output_dir 为 docs/distilled 的绝对路径，向上两级为项目根
    return os.path.dirname(os.path.dirname(output_dir))


def write_meta_yaml(meta_path: str, meta_content: str) -> str:
    """将元数据内容写入 .meta.yaml 文件。

    自动确保父目录存在。

    Args:
        meta_path: .meta.yaml 文件的绝对路径
        meta_content: YAML 格式的元数据字符串

    Returns:
        str: 写入的文件绝对路径
    """
    parent_dir = os.path.dirname(meta_path)
    os.makedirs(parent_dir, exist_ok=True)

    with open(meta_path, "w", encoding="utf-8") as f:
        f.write(meta_content)

    logger.info("元数据文件已写入: %s（%d 字符）", meta_path, len(meta_content))
    return meta_path


def read_meta_yaml(meta_path: str) -> dict[str, Any]:
    """安全读取 .meta.yaml 元数据文件。

    文件不存在或解析失败时，返回默认结构，不抛出异常。

    Args:
        meta_path: .meta.yaml 文件的绝对路径

    Returns:
        dict: 元数据字典，文件不存在时返回默认空结构
    """
    if not os.path.isfile(meta_path):
        logger.info("元数据文件不存在: %s，返回默认结构", meta_path)
        return _default_meta()

    try:
        import yaml
        with open(meta_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if data is None:
            logger.warning("元数据文件 %s 内容为空，返回默认结构", meta_path)
            return _default_meta()
        logger.info("成功加载元数据文件: %s", meta_path)
        return data
    except Exception as e:
        logger.warning("读取元数据文件 %s 失败: %s，返回默认结构", meta_path, e)
        return _default_meta()


def _default_meta() -> dict[str, Any]:
    """返回默认的元数据结构（文件不存在时使用）。

    Returns:
        dict: 包含默认值的元数据字典
    """
    return {
        "meta": {
            "version": "1.0",
            "generated_by": "N/A",
            "generated_at": "N/A",
        },
        "blog_stats": {
            "total_files": 0,
            "summarized_count": 0,
            "pending_count": 0,
            "new_since_last_update": 0,
            "last_blog_file": "",
            "last_blog_date": "",
        },
        "distillation": {
            "last_summarize_time": "N/A",
            "last_analyze_time": "N/A",
            "first_layer_elapsed_seconds": 0,
            "second_layer_elapsed_seconds": 0,
        },
        "products": {
            "blog_index": "",
            "core_veins": "",
            "golden_quotes": "",
            "state_file": "",
            "readme": "",
        },
        "api_used": {
            "model": "N/A",
            "deep_model": "N/A",
        },
    }