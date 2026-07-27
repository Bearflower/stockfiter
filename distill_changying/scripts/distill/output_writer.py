"""
蒸馏产物输出模块

负责将第二层蒸馏（跨文章深度分析）的结果写入文件系统，
包括核心观点矿脉、金句库和产物索引入口。

产物结构：
  {output_dir}/
  ├── blog-index.csv          # 第一层蒸馏产物
  ├── 核心观点矿脉.md          # 跨文章深度分析报告
  ├── 金句库.md                # 按主题分类的金句整理
  └── README.md               # 蒸馏产物索引入口
"""

from __future__ import annotations

import csv
import logging
import os
from typing import Any

from scripts.distill.analyzer import call_llm, load_summaries

logger = logging.getLogger(__name__)


def write_deep_analysis(analysis_text: str, output_dir: str) -> str:
    """将深度分析报告写入「核心观点矿脉.md」。

    Args:
        analysis_text: LLM 生成的完整 Markdown 分析报告
        output_dir: 输出目录绝对路径

    Returns:
        str: 写入的文件绝对路径
    """
    os.makedirs(output_dir, exist_ok=True)
    file_path = os.path.join(output_dir, "核心观点矿脉.md")

    with open(file_path, "w", encoding="utf-8") as f:
        f.write(analysis_text)

    logger.info("深度分析报告已写入: %s (%d 字符)", file_path, len(analysis_text))
    return file_path


def extract_quotes(config: dict[str, Any]) -> str:
    """从博客摘要中提取/生成按主题分类的金句库。

    读取 blog-index.csv 中的摘要数据，调用 LLM 从中提炼出
    具有 E大 风格的、按主题分类的金句表述。

    Args:
        config: 完整配置字典

    Returns:
        str: 按主题分类的金句库 Markdown 文本

    Raises:
        FileNotFoundError: blog-index.csv 不存在
    """
    output_dir = config["paths"]["output_dir"]
    summaries = load_summaries(output_dir)

    logger.info("从 %d 篇摘要中提取金句", len(summaries))

    # 构建摘要列表（精简版，只给 LLM 必要的上下文）
    summary_lines: list[str] = []
    for s in summaries:
        line = f"- 【{s['topic']}】{s['summary']}（关键词：{s['keywords']}）"
        summary_lines.append(line)

    summaries_text = "\n".join(summary_lines)

    prompt = f"""以下是投资博主"ETF拯救世界"(E大)所有文章的摘要列表。请根据这些摘要内容，提炼出具有传播力的金句表达（仿照他的风格）。

要求：
1. 按主题分类输出，每个主题下列出 5-10 条金句风格的观点表述
2. 每条金句应该简洁有力、有洞察力和传播性
3. 确保金句风格与 E大 一贯的表述方式一致（理性、数据驱动、有温度）
4. 输出为结构化 Markdown 格式

【文章摘要列表】
{summaries_text}

请输出完整的金句库 Markdown。"""

    quotes_text = call_llm(config, prompt, task_name="金句库提取")
    return quotes_text


def write_quotes(quotes_text: str, output_dir: str) -> str:
    """将金句库写入「金句库.md」。

    Args:
        quotes_text: 金句库 Markdown 文本
        output_dir: 输出目录绝对路径

    Returns:
        str: 写入的文件绝对路径
    """
    os.makedirs(output_dir, exist_ok=True)
    file_path = os.path.join(output_dir, "金句库.md")

    with open(file_path, "w", encoding="utf-8") as f:
        f.write(quotes_text)

    logger.info("金句库已写入: %s (%d 字符)", file_path, len(quotes_text))
    return file_path


def write_readme(output_dir: str, blog_count: int | None = None) -> str:
    """生成蒸馏产物索引入口「README.md」。

    自动统计 blog-index.csv 中的博文数量（如果未提供）。

    Args:
        output_dir: 输出目录绝对路径
        blog_count: 博文总数，为 None 时自动从 blog-index.csv 统计

    Returns:
        str: 写入的文件绝对路径
    """
    os.makedirs(output_dir, exist_ok=True)

    # 如果未提供博文数量，从 CSV 中自动统计
    if blog_count is None:
        blog_count = _count_blog_entries(output_dir)

    content = f"""# E大博客蒸馏产物

对"ETF拯救世界"（E大）{blog_count} 篇投资博客的 AI 蒸馏结果。

## 产物列表

- [blog-index.csv](./blog-index.csv) — 全部博文摘要索引表
- [核心观点矿脉.md](./核心观点矿脉.md) — 跨文章深度分析报告
- [金句库.md](./金句库.md) — 按主题分类的金句整理

## 蒸馏方法论

本项目采用**两层蒸馏**策略：

1. **第一层：单篇提炼** — 对每篇博文生成摘要、关键词和主题分类
2. **第二层：跨文章分析** — 将所有摘要汇总，调用 LLM 进行深度跨文章分析

更多细节参见 [蒸馏长赢指数.md](../../docs/requirements/蒸馏长赢指数.md)
"""

    file_path = os.path.join(output_dir, "README.md")
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(content)

    logger.info("产物索引入口已写入: %s", file_path)
    return file_path


def _count_blog_entries(output_dir: str) -> int:
    """从 blog-index.csv 统计博文数量。

    Args:
        output_dir: 输出目录绝对路径

    Returns:
        int: 博文条目数，CSV 不存在时返回 0
    """
    csv_path = os.path.join(output_dir, "blog-index.csv")
    if not os.path.isfile(csv_path):
        logger.warning("blog-index.csv 不存在，无法统计博文数量")
        return 0

    try:
        with open(csv_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            count = sum(1 for _ in reader)
        logger.info("统计到 %d 篇博文摘要", count)
        return count
    except Exception as e:
        logger.warning("统计博客数量失败: %s", e)
        return 0