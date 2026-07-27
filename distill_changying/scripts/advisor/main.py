"""
E大投资决策助手 CLI 入口（策略名称：distill_changying）

提供三个子命令：
  check    - 快速评估：获取数据 → 温度计算 → 仓位建议（不调用 LLM）
  analyze  - 完整分析：check + 知识底座 + LLM 报告
  chat     - 交互模式
"""

from __future__ import annotations
import argparse
import sys
import logging
from scripts.advisor.config import get_config
from scripts.advisor.market_data import fetch_all_valuations
from scripts.advisor.temperature import calculate_market_temperature
from scripts.advisor.position import get_position_advice
from scripts.advisor.etf_recommend import generate_recommendations, format_recommendations_text
from scripts.advisor.position_display import load_current_positions, format_position_section
from scripts.advisor.llm_report import generate_report
from scripts.advisor.chat import run_chat_session


def setup_logging(config: dict) -> None:
    """根据配置初始化日志系统。

    Args:
        config: 配置字典
    """
    log_format = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    handlers = [
        logging.StreamHandler(sys.stdout),
    ]

    logging.basicConfig(
        level=logging.INFO,
        format=log_format,
        handlers=handlers,
    )


def _build_details_lookup(temperature: dict) -> dict[str, dict]:
    """从温度评估结果构建「代码→详情」的查找表。

    用于表格输出时快速获取每个指数的温度标签。

    Args:
        temperature: calculate_market_temperature 的返回值

    Returns:
        dict: key 为指数代码，value 为 details 中的条目
    """
    lookup: dict[str, dict] = {}
    for detail in temperature.get("details", []):
        code = detail.get("code", "")
        if code:
            lookup[code] = detail
    return lookup


def _format_value(value: float | None, suffix: str = "", decimals: int = 1) -> str:
    """格式化数值为表格显示的字符串，None 时返回占位符。

    Args:
        value: 待格式化的数值，None 表示数据缺失
        suffix: 数值后的后缀（如 "%"）
        decimals: 小数位数

    Returns:
        str: 格式化后的字符串
    """
    if value is None:
        return "--"
    return f"{value:.{decimals}f}{suffix}"


def _pad_center(text: str, width: int) -> str:
    """将文本居中填充到指定宽度，正确处理中文字符。

    中文字符在终端中占 2 个英文字符宽度。

    Args:
        text: 待填充的文本
        width: 目标显示宽度（按英文字符计）

    Returns:
        str: 居中填充后的字符串
    """
    # 计算文本的实际显示宽度（中文占 2，英文/数字/符号占 1）
    display_width = sum(2 if '\u4e00' <= c <= '\u9fff' or '\u3000' <= c <= '\u303f' or '\uff00' <= c <= '\uffef' else 1 for c in text)
    if display_width >= width:
        return text
    left_pad = (width - display_width) // 2
    right_pad = width - display_width - left_pad
    return " " * left_pad + text + " " * right_pad


def _print_check_report(
    market_data: dict,
    temperature: dict,
    position: dict,
    config: dict,
    etf_recommendations: dict | None = None,
    output_path: str | None = None,
) -> None:
    """格式化输出市场温度快报（含 ETF 操作建议）。

    输出内容包括：
    1. 各指数估值一览表（含 valid 和 invalid 的指数）
    2. 全市场温度摘要
    3. 三维度仓位建议
    4. ETF 操作建议
    5. 免责声明

    Args:
        market_data: fetch_all_valuations 的返回值
        temperature: calculate_market_temperature 的返回值
        position: get_position_advice 的返回值
        config: 完整配置字典
        etf_recommendations: generate_recommendations 的返回值（可选）
        output_path: 可选的文件输出路径
    """
    lines: list[str] = []
    sep = "═" * 42
    thin_sep = "─" * 42

    # ── 标题 ──
    lines.append(sep)
    lines.append("E大投资决策助手 · 市场温度快报")
    lines.append(sep)
    lines.append("")

    # ── 各指数估值一览表 ──
    lines.append("📊 各指数估值一览")

    # 确定列宽
    indices = market_data.get("indices", [])
    # 收集所有指数名称，计算最大显示宽度
    name_widths = []
    for idx in indices:
        name = idx.get("name", idx.get("code", "?"))
        w = sum(2 if '\u4e00' <= c <= '\u9fff' or '\u3000' <= c <= '\u303f' or '\uff00' <= c <= '\uffef' else 1 for c in name)
        name_widths.append(w)
    # 表头"指数名称"宽度为 8（4个中文字）
    name_col_width = max(max(name_widths) if name_widths else 4, 8)
    # PE 列固定 8 字符宽（含 1 位小数，如 "  12.5  "）
    pe_col_width = 8
    # PE分位列固定 10 字符宽（如 "  25.3%  "）
    pct_col_width = 10
    # 温度评估列：最宽为 "数据缺失"(4中文=8宽度) 或 "钻石坑"(3中文=6宽度)，取 10 保底
    temp_col_width = 10

    def _draw_row(cells: list[str], left: str, mid: str, right: str, fill: str) -> str:
        """绘制表格行，cells 已包含填充后的内容。"""
        return left + mid.join(cells) + right

    def _draw_sep(left: str, mid: str, right: str, fill: str) -> str:
        """绘制表格分隔线。"""
        cells = [
            fill * name_col_width,
            fill * pe_col_width,
            fill * pct_col_width,
            fill * temp_col_width,
        ]
        return _draw_row(cells, left, mid, right, fill)

    # 表头分隔线
    lines.append(_draw_sep("┌", "┬", "┐", "─"))
    # 表头行
    header_cells = [
        _pad_center("指数名称", name_col_width),
        _pad_center("PE", pe_col_width),
        _pad_center("PE分位", pct_col_width),
        _pad_center("温度评估", temp_col_width),
    ]
    lines.append(_draw_row(header_cells, "│", "│", "│", " "))
    # 表头/数据分隔线
    lines.append(_draw_sep("├", "┼", "┤", "─"))

    # 构建温度详情查找表
    details_lookup = _build_details_lookup(temperature)

    # 数据行
    for idx in indices:
        name = idx.get("name", idx.get("code", "?"))
        code = idx.get("code", "")
        is_valid = idx.get("valid", False)

        if is_valid:
            pe_val = idx.get("pe")
            # 优先使用 temperature details 中的 percentile（可能为 None）
            detail = details_lookup.get(code, {})
            pct_val = detail.get("percentile")
            temp_label = detail.get("label", "无数据")
        else:
            pe_val = None
            pct_val = None
            temp_label = "数据缺失"

        row_cells = [
            _pad_center(name, name_col_width),
            _pad_center(_format_value(pe_val, decimals=1), pe_col_width),
            _pad_center(_format_value(pct_val, suffix="%", decimals=1), pct_col_width),
            _pad_center(temp_label, temp_col_width),
        ]
        lines.append(_draw_row(row_cells, "│", "│", "│", " "))

    # 表尾分隔线
    lines.append(_draw_sep("└", "┴", "┘", "─"))
    lines.append("")

    # ── 全市场温度 ──
    temp_icon_map = {
        "钻石坑": "💎",
        "低估": "🟢",
        "正常": "🟡",
        "高估": "🟠",
        "泡沫": "🔴",
        "未知": "⚪",
    }
    icon = temp_icon_map.get(temperature["label"], "⚪")
    lines.append(
        f"🌡️ 全市场温度：{icon} {temperature['label']}"
        f"（PE 分位均值 {temperature['avg_percentile']:.1f}%）"
    )
    lines.append(f"   置信度：{temperature['confidence']}")
    lines.append("")

    # ── 仓位建议 ──
    lines.append("💰 仓位建议")
    lines.append(
        f"   A股仓位：{position['stock']}%   "
        f"债券仓位：{position['bond']}%   "
        f"现金仓位：{position['cash']}%"
    )
    lines.append(f"   {position['description']}")
    lines.append("")

    # ── ETF 操作建议 ──
    if etf_recommendations and etf_recommendations.get("recommendations"):
        lines.append("📈 ETF 操作建议")
        etf_text = format_recommendations_text(etf_recommendations)
        lines.append(etf_text)
        lines.append("")

    # ── 持仓展示 ──
    try:
        positions = load_current_positions()
        if positions:
            position_section = format_position_section(positions)
            lines.append(position_section)
    except Exception as e:
        logging.getLogger(__name__).debug("加载持仓数据失败（不影响主流程）: %s", e)

    # ── 免责声明 ──
    lines.append(thin_sep)
    disclaimer = config.get("disclaimer", "以上分析仅供参考，不构成投资建议。")
    lines.append(disclaimer)

    # ── 输出 ──
    report = "\n".join(lines)
    print(report)

    if output_path:
        try:
            import os
            output_dir = os.path.dirname(output_path)
            if output_dir:
                os.makedirs(output_dir, exist_ok=True)
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(report + "\n")
            print(f"\n报告已保存至: {output_path}")
        except OSError as e:
            print(f"报告保存失败: {e}")
            logging.getLogger(__name__).warning("报告保存失败: %s", e)


def cmd_check(args: argparse.Namespace) -> None:
    """快速评估：获取数据 → 温度计算 → 仓位建议（不调用 LLM）。

    Args:
        args: 解析后的命令行参数
    """
    config = get_config()
    setup_logging(config)
    logger = logging.getLogger(__name__)

    logger.info("=" * 50)
    logger.info("E大投资决策助手 -- check 模式启动")
    logger.info("=" * 50)

    try:
        # 1. 获取市场数据（含缓存逻辑）
        logger.info("正在获取市场估值数据...")
        market_data = fetch_all_valuations(config, force_refresh=args.no_cache)

        if market_data["valid_count"] == 0:
            print("无法获取有效的市场估值数据，请检查网络或 baostock 状态。")
            return

        # 2. 计算市场温度
        logger.info("正在计算市场温度...")
        temperature = calculate_market_temperature(market_data["indices"], config)

        # 3. 获取仓位建议
        logger.info("正在生成仓位建议...")
        position = get_position_advice(temperature["label"], config)

        # 4. ETF 操作推荐
        logger.info("正在生成 ETF 操作建议...")
        etf_recs = generate_recommendations(market_data, config)

        # 5. 格式化输出
        _print_check_report(
            market_data, temperature, position, config,
            etf_recommendations=etf_recs,
            output_path=args.output,
        )

    except Exception as e:
        logger.error("check 模式执行失败: %s", e, exc_info=True)
        print(f"执行失败: {e}")
        sys.exit(1)


def cmd_analyze(args: argparse.Namespace) -> None:
    """完整分析（含 LLM）：check + 知识底座 + AI 报告。

    流程：
    1. 获取市场估值数据
    2. 计算市场温度
    3. 获取仓位建议
    4. 调用 LLM（含知识底座）生成分析报告
    5. 输出报告（可选的保存到文件）

    Args:
        args: 解析后的命令行参数
    """
    config = get_config()
    setup_logging(config)
    logger = logging.getLogger(__name__)

    logger.info("=" * 50)
    logger.info("E大投资决策助手 -- analyze 模式启动")
    logger.info("=" * 50)

    try:
        # 1. 获取市场数据
        logger.info("正在获取市场估值数据...")
        market_data = fetch_all_valuations(config, force_refresh=args.no_cache)

        if market_data["valid_count"] == 0:
            print("无法获取有效的市场估值数据，请检查网络或 baostock 状态。")
            return

        # 2. 计算市场温度
        logger.info("正在计算市场温度...")
        temperature = calculate_market_temperature(market_data["indices"], config)

        # 3. 获取仓位建议
        logger.info("正在生成仓位建议...")
        position = get_position_advice(temperature["label"], config)

        # 4. ETF 操作推荐
        logger.info("正在生成 ETF 操作建议...")
        etf_recs = generate_recommendations(market_data, config)

        # 5. 生成完整分析报告（含 ETF 建议数据）
        logger.info("正在生成 AI 分析报告...")
        report = generate_report(config, market_data, temperature, position, etf_recs)

        # 6. 输出
        print(report)

        if args.output:
            try:
                import os
                output_dir = os.path.dirname(args.output)
                if output_dir:
                    os.makedirs(output_dir, exist_ok=True)
                with open(args.output, "w", encoding="utf-8") as f:
                    f.write(report)
                print(f"\n报告已保存至: {args.output}")
            except OSError as e:
                print(f"报告保存失败: {e}")

    except Exception as e:
        logger.error("analyze 模式执行失败: %s", e, exc_info=True)
        print(f"执行失败: {e}")
        sys.exit(1)


def cmd_chat(args: argparse.Namespace) -> None:
    """交互模式：先展示市场报告 → 进入交互式问答。

    流程：
    1. 获取市场估值数据
    2. 计算市场温度
    3. 获取仓位建议
    4. 进入交互式问答循环

    Args:
        args: 解析后的命令行参数
    """
    config = get_config()
    setup_logging(config)
    logger = logging.getLogger(__name__)

    logger.info("=" * 50)
    logger.info("E大投资决策助手 -- chat 模式启动")
    logger.info("=" * 50)

    try:
        # 1. 获取市场数据
        logger.info("正在获取市场估值数据...")
        market_data = fetch_all_valuations(config, force_refresh=args.no_cache)

        if market_data["valid_count"] == 0:
            print("无法获取有效的市场估值数据，请检查网络或 baostock 状态。")
            return

        # 2. 计算市场温度
        temperature = calculate_market_temperature(market_data["indices"], config)

        # 3. 获取仓位建议
        position = get_position_advice(temperature["label"], config)

        # 4. ETF 操作推荐
        etf_recs = generate_recommendations(market_data, config)

        # 5. 进入交互循环
        run_chat_session(config, market_data, temperature, position, etf_recs)

    except Exception as e:
        logger.error("chat 模式执行失败: %s", e, exc_info=True)
        print(f"执行失败: {e}")
        sys.exit(1)


def build_parser() -> argparse.ArgumentParser:
    """构建 argparse 解析器。

    Returns:
        argparse.ArgumentParser: 配置好的参数解析器
    """
    parser = argparse.ArgumentParser(
        description="E大投资决策助手 -- 基于估值温度计的A股分析工具"
    )
    subparsers = parser.add_subparsers(dest="command", help="可用子命令")

    # check 子命令
    check_parser = subparsers.add_parser(
        "check", help="快速评估：纯算法，不调用 LLM"
    )
    check_parser.add_argument(
        "--no-cache", action="store_true", default=False,
        help="强制刷新数据，不使用缓存"
    )
    check_parser.add_argument(
        "--output", type=str, default=None,
        help="保存报告到指定 Markdown 文件"
    )
    check_parser.set_defaults(func=cmd_check)

    # analyze 子命令
    analyze_parser = subparsers.add_parser(
        "analyze", help="完整分析：数据 + 知识底座 + LLM 报告"
    )
    analyze_parser.add_argument(
        "--no-cache", action="store_true", default=False,
        help="强制刷新数据"
    )
    analyze_parser.add_argument(
        "--output", type=str, default=None,
        help="保存报告到文件"
    )
    analyze_parser.set_defaults(func=cmd_analyze)

    # chat 子命令
    chat_parser = subparsers.add_parser(
        "chat", help="交互式问答模式"
    )
    chat_parser.add_argument(
        "--no-cache", action="store_true", default=False,
        help="强制刷新数据"
    )
    chat_parser.set_defaults(func=cmd_chat)

    return parser


def main() -> None:
    """CLI 主入口函数。"""
    parser = build_parser()
    args = parser.parse_args()
    if args.command is None:
        parser.print_help()
        sys.exit(1)
    args.func(args)


if __name__ == "__main__":
    main()