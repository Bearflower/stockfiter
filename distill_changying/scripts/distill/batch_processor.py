"""
批量处理器

编排文件遍历、状态检查、摘要提取、速率控制和结果导出的完整流程。
"""

from __future__ import annotations

import csv
import logging
import os
import time
from typing import Any

from scripts.distill.file_handler import get_blog_identifier, list_blog_files, read_blog
from scripts.distill.state_manager import (
    is_processed,
    load_state,
    mark_processed,
    save_state,
)
from scripts.distill.summarizer import summarize_with_retry

logger = logging.getLogger(__name__)


def run_summarize(
    config: dict[str, Any],
    year: str | None = None,
    pattern: str | None = None,
    force: bool = False,
    limit: int | None = None,
    files: list[str] | None = None,
) -> dict[str, Any]:
    """运行第一层蒸馏：遍历博客文件、调用 LLM 摘要、追踪进度。

    处理流程：
    1. 遍历 blog_dir 获取文件列表（如果 files 参数不为 None，则跳过此步骤）
    2. 加载已有状态，跳过已处理的文件（除非 force=True）
    3. 按速率限制逐篇调用 LLM 摘要
    4. 每处理一篇立即保存状态（防止中断丢失进度）
    5. 返回最终状态字典

    Args:
        config: 完整的配置字典
        year: 按年份筛选（可选，files 参数不为 None 时忽略）
        pattern: 按文件名校验模式筛选（可选，files 参数不为 None 时忽略）
        force: 强制重新处理所有文章，忽略已有状态
        limit: 最多处理篇数（可选，用于调试）
        files: 外部传入的文件列表（可选），不为 None 时跳过 list_blog_files() 步骤

    Returns:
        dict: 最终的处理状态字典
    """
    blog_dir = config["paths"]["blog_dir"]
    state_file = config["paths"]["state_file"]
    rate = config["rate_limit"]

    # 1. 获取文件列表：如果外部传入了 files，则直接使用
    external_files = files is not None
    if not external_files:
        files = list_blog_files(blog_dir, year=year, pattern=pattern)
        if len(files) == 0:
            logger.warning("未找到符合条件的博客文件，处理终止")
            return {}
        logger.info("共找到 %d 篇博客文件待处理", len(files))
    else:
        if len(files) == 0:
            logger.warning("传入的文件列表为空，处理终止")
            return {}
        logger.info("使用外部传入的文件列表，共 %d 篇博客文件", len(files))

    # 2. 加载状态
    state = load_state(state_file)

    # 计算总文件数：外部传入时 total_files = 新文件数 + 已处理数（反映全量）
    if external_files:
        total_files = len(files) + len(state)
    else:
        total_files = len(files)

    # 3. 筛选待处理文件
    pending: list[str] = []
    skipped = 0
    for f in files:
        identifier = get_blog_identifier(f)
        if not force and is_processed(identifier, state):
            skipped += 1
        else:
            pending.append(f)

    logger.info(
        "已跳过 %d 篇（已处理），待处理 %d 篇",
        skipped,
        len(pending),
    )

    if force:
        logger.info("强制重新处理模式已启用，将重新处理所有文章")

    # 4. 按 limit 截断
    if limit is not None and limit > 0:
        if limit < len(pending):
            logger.info("限制处理篇数: %d（共 %d 篇待处理）", limit, len(pending))
            pending = pending[:limit]
        else:
            logger.info("限制篇数 %d >= 待处理篇数 %d，将处理全部", limit, len(pending))

    if not pending:
        logger.info("没有待处理的文章，任务完成")
        return state

    # 5. 逐篇处理
    batch_size = rate.get("batch_size", 5)
    delay_between = rate.get("delay_between_articles", 2)
    batch_pause = rate.get("batch_pause", 10)

    logger.info(
        "开始批量处理: 共 %d 篇，每批 %d 篇，篇间延迟 %ds，批间暂停 %ds",
        len(pending),
        batch_size,
        delay_between,
        batch_pause,
    )

    processed_count = 0
    error_count = 0
    total_pending = len(pending)
    start_time = time.time()

    for idx, filepath in enumerate(pending):
        batch_num = (idx // batch_size) + 1
        basename = os.path.basename(filepath)
        identifier = get_blog_identifier(filepath)
        current_total = skipped + processed_count + error_count
        progress_pct = (current_total / total_files) * 100 if total_files > 0 else 0

        logger.info(
            "[第 %d 批] 正在处理 %d/%d（总进度 %.1f%%）: %s",
            batch_num,
            idx + 1,
            total_pending,
            progress_pct,
            basename,
        )

        try:
            # 读取文章内容
            content = read_blog(filepath)
            if not content:
                logger.warning("文章内容为空，跳过: %s", basename)
                error_count += 1
                state = mark_processed(
                    identifier,
                    {"summary": "[内容为空]", "keywords": [], "topic": "其他"},
                    state,
                )
                save_state(state_file, state)
                continue

            # 调用 LLM 摘要（含重试）
            result = summarize_with_retry(content, config, filename=basename)
            processed_count += 1

            logger.info(
                "已处理 %d/%d (%.1f%%) - 主题: %s, 摘要: %s",
                current_total + 1,
                total_files,
                progress_pct + (1 / total_files * 100),
                result.get("topic", "未知"),
                result.get("summary", "")[:50],
            )

        except Exception as e:
            logger.error("处理文件 %s 时发生异常: %s", basename, e)
            error_count += 1
            result = {
                "summary": f"[处理异常: {str(e)[:50]}]",
                "keywords": [],
                "topic": "其他",
            }

        # 标记已处理并立即保存状态
        state = mark_processed(identifier, result, state)
        save_state(state_file, state)

        # 篇间延迟（最后一批的最后一篇不需要）
        is_last = idx == total_pending - 1
        if not is_last:
            time.sleep(delay_between)

            # 批次间额外暂停
            if (idx + 1) % batch_size == 0:
                logger.info(
                    "第 %d 批完成，暂停 %d 秒...",
                    batch_num,
                    batch_pause,
                )
                time.sleep(batch_pause)

    elapsed = time.time() - start_time
    logger.info(
        "第一层蒸馏完成！处理 %d 篇，成功 %d 篇，失败 %d 篇，"
        "跳过 %d 篇，总耗时 %.1f 秒",
        len(pending),
        processed_count,
        error_count,
        skipped,
        elapsed,
    )

    return state


def export_to_csv(state: dict[str, Any], output_dir: str) -> str:
    """从状态字典导出 blog-index.csv。

    CSV 表头：file, summary, keywords, topic, processed_at

    Args:
        state: 处理状态字典
        output_dir: 输出目录的绝对路径

    Returns:
        str: 导出的 CSV 文件绝对路径
    """
    os.makedirs(output_dir, exist_ok=True)
    csv_path = os.path.join(output_dir, "blog-index.csv")

    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["file", "summary", "keywords", "topic", "processed_at"])

        for file_id, info in state.items():
            keywords_str = ";".join(info.get("keywords", []))
            writer.writerow([
                file_id,
                info.get("summary", ""),
                keywords_str,
                info.get("topic", ""),
                info.get("processed_at", ""),
            ])

    logger.info("CSV 索引已导出: %s（共 %d 条记录）", csv_path, len(state))
    return csv_path