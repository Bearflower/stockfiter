"""
博客蒸馏工具 CLI 入口

提供五个子命令：
  summarize  - 第一层蒸馏：单篇摘要 + 关键词提取
  analyze    - 第二层蒸馏：跨文章深度分析
  distill    - 完整蒸馏：依次执行 summarize + analyze
  update     - 增量更新：检测新文件并执行摘要+分析
  persona    - 生成E大人物画像文档（需先完成 summarize + analyze）
"""

from __future__ import annotations

import argparse
import logging
import sys
from typing import Any

from scripts.distill.config import get_config


def setup_logging(config: dict[str, Any]) -> None:
    """根据配置初始化日志系统。

    Args:
        config: 配置字典
    """
    log_dir = config["paths"].get("log_dir", "docs/distilled/logs")
    # 确保日志目录存在
    import os
    os.makedirs(log_dir, exist_ok=True)

    log_format = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    handlers = [
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(
            os.path.join(log_dir, "distill.log"),
            encoding="utf-8",
        ),
    ]

    logging.basicConfig(
        level=logging.INFO,
        format=log_format,
        handlers=handlers,
    )


def cmd_summarize(args: argparse.Namespace) -> None:
    """执行第一层蒸馏：单篇摘要 + 关键词提取。

    Args:
        args: 解析后的命令行参数
    """
    config = get_config()
    setup_logging(config)
    logger = logging.getLogger(__name__)

    logger.info("开始第一层蒸馏（单篇摘要+关键词）")
    logger.info("配置: model=%s, blog_dir=%s", config["api"]["model"], config["paths"]["blog_dir"])

    if args.year:
        logger.info("筛选年份: %s", args.year)
    if args.pattern:
        logger.info("筛选模式: %s", args.pattern)
    if args.limit:
        logger.info("限制篇数: %d", args.limit)
    if args.force:
        logger.info("强制重新处理已启用")

    # 调用批量处理器执行第一层蒸馏
    from scripts.distill.batch_processor import export_to_csv, run_summarize

    try:
        state = run_summarize(
            config=config,
            year=args.year,
            pattern=args.pattern,
            force=args.force,
            limit=args.limit,
        )
        if state:
            csv_path = export_to_csv(state, config["paths"]["output_dir"])
            logger.info("摘要索引文件已生成: %s", csv_path)
        else:
            logger.warning("未生成任何摘要结果")
    except Exception as e:
        logger.error("第一层蒸馏执行失败: %s", e, exc_info=True)
        raise


def cmd_analyze(args: argparse.Namespace) -> None:
    """执行第二层蒸馏：跨文章深度分析。

    流程：
    1. 调用 run_analysis 生成深度分析报告
    2. 将分析报告写入「核心观点矿脉.md」
    3. 调用 extract_quotes 生成金句库
    4. 将金句库写入「金句库.md」
    5. 生成产物索引入口「README.md」

    Args:
        args: 解析后的命令行参数
    """
    config = get_config()
    setup_logging(config)
    logger = logging.getLogger(__name__)

    # 第二层蒸馏模块的延迟导入（避免第一层蒸馏依赖不存在的模块）
    from scripts.distill.analyzer import run_analysis  # pyright: ignore[reportMissingImports]
    from scripts.distill.output_writer import (  # pyright: ignore[reportMissingImports]
        extract_quotes,
        write_deep_analysis,
        write_quotes,
        write_readme,
    )

    output_dir = config["paths"]["output_dir"]

    try:
        # 阶段 1：跨文章深度分析
        logger.info("=" * 50)
        logger.info("开始第二层蒸馏（跨文章深度分析）")
        logger.info("配置: deep_model=%s, output_dir=%s",
                    config["api"]["deep_model"], output_dir)
        logger.info("=" * 50)

        analysis_text = run_analysis(config)
        analysis_path = write_deep_analysis(analysis_text, output_dir)
        logger.info("深度分析报告已生成: %s", analysis_path)

        # 阶段 2：金句库提取
        logger.info("-" * 50)
        logger.info("开始金句库提取")
        logger.info("-" * 50)

        quotes_text = extract_quotes(config)
        quotes_path = write_quotes(quotes_text, output_dir)
        logger.info("金句库已生成: %s", quotes_path)

        # 阶段 2.5：先生成中间产物（操作时间线.jsonl 和 近期判断库.jsonl）
        try:
            from scripts.distill.record_extractor import extract_all_and_save
            from scripts.distill.state_manager import load_state
            state = load_state(config["paths"]["state_file"])
            extract_all_and_save(state, output_dir, blog_dir=config["paths"]["blog_dir"])
            logger.info("操作时间线和近期判断库已更新")
        except Exception as e:
            logger.warning("中间产物生成失败（不影响分析结果）: %s", e)

        # 再基于中间产物做观点提取（合并三通道，顺序不能反）
        try:
            from scripts.distill.opinion_extractor import extract_opinions, write_opinions
            opinions = extract_opinions(config, output_dir=output_dir)
            if opinions:
                opinion_path = write_opinions(opinions, output_dir)
                logger.info("观点库已生成: %s（%d 条，含operation/observation/principle三种类型）", opinion_path, len(opinions))
        except Exception as e:
            logger.warning("观点提取失败（不影响分析结果）: %s", e)

        # 阶段 3：生成产物索引入口
        logger.info("-" * 50)
        logger.info("生成产物索引入口")
        logger.info("-" * 50)

        readme_path = write_readme(output_dir)
        logger.info("产物索引入口已生成: %s", readme_path)

        # 自动构建知识索引
        try:
            logger.info("自动构建知识索引...")
            from scripts.distill.persona_index import load_data, build_structured_index, save_index
            data = load_data(output_dir)
            if data.get("operations") or data.get("observations") or data.get("principles"):
                index = build_structured_index(data)
                save_index(index, output_dir)
                logger.info("知识索引已构建")
            else:
                logger.info("数据为空，跳过索引构建")
        except Exception as e:
            logger.warning("知识索引自动构建失败（不影响分析结果）: %s", e)

        logger.info("=" * 50)
        logger.info("第二层蒸馏完成！产物目录: %s", output_dir)
        logger.info("=" * 50)

    except FileNotFoundError as e:
        logger.error("文件未找到: %s", e)
        sys.exit(1)
    except RuntimeError as e:
        logger.error("运行时错误: %s", e)
        sys.exit(1)
    except Exception as e:
        logger.exception("第二层蒸馏过程中发生未预期错误: %s", e)
        sys.exit(1)


def cmd_distill(args: argparse.Namespace) -> None:
    """执行完整蒸馏流程：依次执行 summarize + analyze。

    Args:
        args: 解析后的命令行参数
    """
    config = get_config()
    setup_logging(config)
    logger = logging.getLogger(__name__)

    logger.info("开始完整蒸馏流程（summarize + analyze）")

    # 先执行第一层蒸馏
    logger.info("--- 阶段 1/2: 单篇摘要提取 ---")
    cmd_summarize(args)

    # 再执行第二层蒸馏
    logger.info("--- 阶段 2/2: 跨文章深度分析 ---")
    cmd_analyze(args)

    # 生成 .meta.yaml 元数据文件
    _generate_meta_after_distill(config, logger)


def cmd_update(args: argparse.Namespace) -> None:
    """执行增量知识库更新：检测新文件、摘要 + 分析、生成 .meta.yaml。

    与 distill 全量处理不同，update 只处理新增的博客文件，
    并仅在有新文件或强制分析时才执行跨文章深度分析。

    Args:
        args: 解析后的命令行参数
    """
    import time

    from scripts.distill.file_handler import get_blog_identifier, list_blog_files
    from scripts.distill.state_manager import is_processed, load_state
    from scripts.distill.batch_processor import export_to_csv, run_summarize
    from scripts.distill.output_writer import (
        extract_quotes,
        write_deep_analysis,
        write_quotes,
        write_readme,
    )
    from scripts.distill.metadata import (
        extract_last_blog_date,
        generate_meta_yaml,
        write_meta_yaml,
    )

    config = get_config()
    setup_logging(config)
    logger = logging.getLogger(__name__)

    meta_path = config["meta"]["meta_file"]
    paths = config["paths"]

    logger.info("开始增量知识库更新")
    logger.info("配置: blog_dir=%s, meta_file=%s", paths["blog_dir"], meta_path)

    # 1. 获取全部博客文件列表
    all_files = list_blog_files(paths["blog_dir"])
    logger.info("全部博客文件: %d 篇", len(all_files))

    # 2. 加载处理状态
    state = load_state(paths["state_file"])
    summarized_count = len(state)
    logger.info("已处理文件: %d 篇", summarized_count)

    # 3. 比对得出新文件列表（文件标识符不在 state 中的）
    new_files: list[str] = []
    for f in all_files:
        identifier = get_blog_identifier(f)
        if not is_processed(identifier, state):
            new_files.append(f)

    new_count = len(new_files)
    logger.info("新增文件: %d 篇", new_count)

    # 4. 如果没有新文件且不强制分析，仅刷新 .meta.yaml
    if new_count == 0 and not args.force_analyze:
        logger.info("知识库已是最新，无需更新")

        # 生成 .meta.yaml（无耗时数据）
        last_blog_date = extract_last_blog_date(all_files)
        meta_content = generate_meta_yaml(
            config=config,
            total_files=len(all_files),
            summarized_count=summarized_count,
            new_count=0,
            last_blog_date=last_blog_date,
            summarize_elapsed=0,
            analyze_elapsed=0,
        )
        meta_result_path = write_meta_yaml(meta_path, meta_content)
        logger.info("元数据文件已刷新: %s", meta_result_path)
        return

    summarize_elapsed = 0.0
    analyze_elapsed = 0.0

    # 5. 如果有新文件，执行摘要
    if new_count > 0:
        logger.info("=" * 50)
        logger.info("开始处理 %d 篇新增文件", new_count)
        logger.info("=" * 50)

        t0 = time.time()
        try:
            run_summarize(config=config, files=new_files)
        except Exception as e:
            logger.error("新增文件摘要处理失败: %s", e, exc_info=True)
            raise
        summarize_elapsed = time.time() - t0

        # 更新 blog-index.csv
        updated_state = load_state(paths["state_file"])
        csv_path = export_to_csv(updated_state, paths["output_dir"])
        logger.info("摘要索引文件已更新: %s", csv_path)
        logger.info("第一层蒸馏耗时: %.1f 秒", summarize_elapsed)

    # 6. 如果有新文件或强制分析，执行跨文章分析
    if new_count > 0 or args.force_analyze:
        from scripts.distill.analyzer import run_analysis  # pyright: ignore[reportMissingImports]

        logger.info("=" * 50)
        logger.info("开始跨文章深度分析（新文件数=%d, 强制=%s）", new_count, args.force_analyze)
        logger.info("=" * 50)

        t0 = time.time()
        try:
            analysis_text = run_analysis(config)
            write_deep_analysis(analysis_text, paths["output_dir"])

            quotes_text = extract_quotes(config)
            write_quotes(quotes_text, paths["output_dir"])

            # 先生成中间产物（操作时间线.jsonl 和 近期判断库.jsonl）
            try:
                from scripts.distill.record_extractor import extract_all_and_save
                from scripts.distill.state_manager import load_state
                updated_state = load_state(paths["state_file"])
                extract_all_and_save(updated_state, paths["output_dir"], blog_dir=paths["blog_dir"])
                logger.info("操作时间线和近期判断库已更新")
            except Exception as e:
                logger.warning("中间产物生成失败（不影响分析结果）: %s", e)

            # 再基于中间产物做观点提取（合并三通道，顺序不能反）
            try:
                from scripts.distill.opinion_extractor import extract_opinions, write_opinions
                opinions = extract_opinions(config)
                if opinions:
                    write_opinions(opinions, paths["output_dir"])
                logger.info("观点库已更新（%d 条，含operation/observation/principle三种类型）", len(opinions))
            except Exception as e:
                logger.warning("观点提取失败（不影响分析结果）: %s", e)
        except Exception as e:
            logger.error("跨文章分析失败: %s", e, exc_info=True)
            raise
        analyze_elapsed = time.time() - t0
        logger.info("第二层蒸馏耗时: %.1f 秒", analyze_elapsed)
    else:
        logger.info("无新增文件且未启用强制分析，跳过跨文章分析")

    # 7. 生成产物索引入口
    write_readme(paths["output_dir"], blog_count=len(all_files))

    # 自动构建知识索引
    try:
        logger.info("自动构建知识索引...")
        from scripts.distill.persona_index import load_data, build_structured_index, save_index
        data = load_data(paths["output_dir"])
        if data.get("operations") or data.get("observations") or data.get("principles"):
            index = build_structured_index(data)
            save_index(index, paths["output_dir"])
            logger.info("知识索引已构建")
        else:
            logger.info("数据为空，跳过索引构建")
    except Exception as e:
        logger.warning("知识索引自动构建失败（不影响更新结果）: %s", e)

    # 8. 生成 .meta.yaml 元数据文件
    last_blog_date = extract_last_blog_date(all_files)
    updated_state = load_state(paths["state_file"])
    meta_content = generate_meta_yaml(
        config=config,
        total_files=len(all_files),
        summarized_count=len(updated_state),
        new_count=new_count,
        last_blog_date=last_blog_date,
        summarize_elapsed=summarize_elapsed,
        analyze_elapsed=analyze_elapsed,
    )
    meta_result_path = write_meta_yaml(meta_path, meta_content)
    logger.info("元数据文件已生成: %s", meta_result_path)

    logger.info("=" * 50)
    logger.info(
        "增量更新完成！新增 %d 篇，已处理 %d/%d 篇，"
        "摘要耗时 %.1f 秒，分析耗时 %.1f 秒",
        new_count,
        len(updated_state),
        len(all_files),
        summarize_elapsed,
        analyze_elapsed,
    )
    logger.info("=" * 50)


def _generate_meta_after_distill(config: dict, logger: logging.Logger) -> None:
    """在 distill 命令末尾生成 .meta.yaml 元数据文件。

    Args:
        config: 配置字典
        logger: 日志记录器
    """
    from scripts.distill.file_handler import list_blog_files
    from scripts.distill.state_manager import load_state
    from scripts.distill.metadata import (
        extract_last_blog_date,
        generate_meta_yaml,
        write_meta_yaml,
    )

    meta_path = config["meta"]["meta_file"]
    paths = config["paths"]

    try:
        all_files = list_blog_files(paths["blog_dir"])
        state = load_state(paths["state_file"])

        last_blog_date = extract_last_blog_date(all_files)

        meta_content = generate_meta_yaml(
            config=config,
            total_files=len(all_files),
            summarized_count=len(state),
            new_count=0,
            last_blog_date=last_blog_date,
            summarize_elapsed=0,
            analyze_elapsed=0,
        )
        write_meta_yaml(meta_path, meta_content)
        logger.info("元数据文件已生成: %s", meta_path)
    except Exception as e:
        logger.warning("生成元数据文件失败（非致命错误）: %s", e)


def cmd_persona(args: argparse.Namespace) -> None:
    """生成E大人物画像文档。

    需要先执行 summarize + analyze 生成蒸馏产物。
    产物保存在 docs/distilled/E大人物画像.md。

    Args:
        args: 解析后的命令行参数
    """
    config = get_config()
    setup_logging(config)
    logger = logging.getLogger(__name__)

    from scripts.distill.persona_builder import build_persona, write_persona

    output_dir = config["paths"]["output_dir"]

    logger.info("开始生成E大人物画像")
    logger.info("蒸馏产物目录: %s", output_dir)

    try:
        content = build_persona(config, distill_dir=output_dir)
        if content:
            path = write_persona(content, output_dir)
            logger.info("E大人物画像已生成: %s", path)
            print(f"\n✅ E大人物画像已生成: {path}")
        else:
            logger.error("人物画像生成失败：LLM 返回为空")
            print("\n❌ 人物画像生成失败，请检查日志")
    except Exception as e:
        logger.error("人物画像生成失败: %s", e, exc_info=True)
        print(f"\n❌ 人物画像生成失败: {e}")


def cmd_index(args: argparse.Namespace) -> None:
    """构建/更新知识索引。

    从三通道JSONL数据构建结构化索引+向量索引+统计摘要。

    Args:
        args: 解析后的命令行参数
    """
    config = get_config()
    setup_logging(config)
    logger = logging.getLogger(__name__)

    output_dir = config["paths"]["output_dir"]

    # 1. 加载三通道数据
    logger.info("加载三通道数据...")
    from scripts.distill.persona_index import load_data, build_structured_index, save_index
    data = load_data(output_dir)

    # 2. 构建结构化索引
    logger.info("构建结构化索引...")
    index = build_structured_index(data)
    save_index(index, output_dir)

    # 3. 构建向量索引（除非 --skip-vector）
    if not args.skip_vector:
        logger.info("构建向量索引...")
        try:
            from scripts.distill.persona_index import _get_embedding_model, build_embeddings, save_embeddings, save_embedding_records
            import numpy as np

            model = _get_embedding_model()
            if model is not None:
                # 为observations和principles生成向量
                all_records = data.get("observations", []) + data.get("principles", [])
                if all_records:
                    embeddings = build_embeddings(all_records, model)
                    import os
                    index_dir = os.path.join(output_dir, "persona_index")
                    os.makedirs(index_dir, exist_ok=True)
                    save_embeddings(embeddings, os.path.join(index_dir, "embeddings.npy"))
                    save_embedding_records(all_records, os.path.join(index_dir, "embedding_records.json"))
                    logger.info("向量索引构建完成: %d 条记录", len(all_records))
                else:
                    logger.warning("无observations/principles数据，跳过向量索引")
            else:
                logger.warning("Embedding模型不可用，跳过向量索引")
        except Exception as e:
            logger.warning("向量索引构建失败（不影响结构化索引）: %s", e)

    # 4. 计算统计摘要
    logger.info("计算统计摘要...")
    try:
        from scripts.distill.persona_stats import compute_all_stats, generate_stats_report
        stats = compute_all_stats(
            data.get("operations", []),
            data.get("observations", []),
            data.get("principles", []),
        )
        stats_report = generate_stats_report(stats)
        # 保存统计报告到文件
        import os
        stats_path = os.path.join(output_dir, "persona_index", "stats_report.md")
        os.makedirs(os.path.dirname(stats_path), exist_ok=True)
        with open(stats_path, "w", encoding="utf-8") as f:
            f.write(stats_report)
        logger.info("统计摘要已保存: %s", stats_path)
    except Exception as e:
        logger.warning("统计摘要计算失败: %s", e)

    print(f"\n✅ 知识索引已构建完成")
    print(f"   索引目录: {os.path.join(output_dir, 'persona_index/')}")


def build_parser() -> argparse.ArgumentParser:
    """构建命令行参数解析器。

    Returns:
        argparse.ArgumentParser: 配置好的参数解析器
    """
    parser = argparse.ArgumentParser(
        description="博客蒸馏工具 - 通过 LLM API 批量提炼摘要、关键词和深度分析报告",
    )
    subparsers = parser.add_subparsers(dest="command", help="可用子命令")

    # --- summarize 子命令 ---
    summarize_parser = subparsers.add_parser(
        "summarize",
        help="第一层蒸馏：单篇摘要 + 关键词提取",
    )
    summarize_parser.add_argument(
        "--year",
        type=str,
        default=None,
        help="按年份筛选博客文件（如 2020）",
    )
    summarize_parser.add_argument(
        "--pattern",
        type=str,
        default=None,
        help="按文件名模式筛选（支持通配符，如 '*2020*'）",
    )
    summarize_parser.add_argument(
        "--force",
        action="store_true",
        default=False,
        help="强制重新处理所有文章，忽略已有的处理状态",
    )
    summarize_parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="限制处理篇数（用于测试）",
    )
    summarize_parser.set_defaults(func=cmd_summarize)

    # --- analyze 子命令 ---
    analyze_parser = subparsers.add_parser(
        "analyze",
        help="第二层蒸馏：跨文章深度分析",
    )
    analyze_parser.set_defaults(func=cmd_analyze)

    # --- distill 子命令 ---
    distill_parser = subparsers.add_parser(
        "distill",
        help="完整蒸馏：依次执行 summarize + analyze",
    )
    distill_parser.add_argument(
        "--year",
        type=str,
        default=None,
        help="按年份筛选博客文件（如 2020）",
    )
    distill_parser.add_argument(
        "--pattern",
        type=str,
        default=None,
        help="按文件名模式筛选（支持通配符，如 '*2020*'）",
    )
    distill_parser.add_argument(
        "--force",
        action="store_true",
        default=False,
        help="强制重新处理所有文章，忽略已有的处理状态",
    )
    distill_parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="限制处理篇数（用于测试）",
    )
    distill_parser.set_defaults(func=cmd_distill)

    # --- update 子命令 ---
    update_parser = subparsers.add_parser(
        "update",
        help="增量更新：检测新文件并执行摘要+分析（仅处理新增内容）",
    )
    update_parser.add_argument(
        "--force-analyze",
        action="store_true",
        default=False,
        help="即使没有新文件也强制重新执行全量跨文章分析",
    )
    update_parser.set_defaults(func=cmd_update)

    # --- persona 子命令 ---
    persona_parser = subparsers.add_parser(
        "persona",
        help="生成E大人物画像文档（需先完成 summarize + analyze）",
    )
    persona_parser.set_defaults(func=cmd_persona)

    # --- index 子命令 ---
    index_parser = subparsers.add_parser(
        "index",
        help="构建/更新知识索引（结构化索引+向量索引+统计摘要）",
    )
    index_parser.add_argument(
        "--skip-vector",
        action="store_true",
        help="跳过向量索引构建（仅构建结构化索引）",
    )
    index_parser.set_defaults(func=cmd_index)

    return parser


def main() -> None:
    """CLI 主入口函数。"""
    parser = build_parser()
    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(1)

    # 调用对应子命令的处理函数
    args.func(args)


if __name__ == "__main__":
    main()