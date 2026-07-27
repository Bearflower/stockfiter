"""
持仓展示模块

读取蒸馏出的"长赢指数理论持仓总表.csv"，生成日报中"继续持有"表格。
只展示净持仓 > 0 的品种，按份数降序排列。
"""

from __future__ import annotations

import csv
import logging
import os

logger = logging.getLogger(__name__)

# 模块级缓存：项目根目录
_project_root: str | None = None


def _get_project_root() -> str:
    """获取项目根目录（distill_changying/）。

    position_display.py 位于 scripts/advisor/ 下，向上两级即为项目根目录。

    Returns:
        str: 项目根目录的绝对路径
    """
    global _project_root
    if _project_root is not None:
        return _project_root

    config_dir = os.path.dirname(os.path.abspath(__file__))
    _project_root = os.path.dirname(os.path.dirname(config_dir))
    return _project_root


def _resolve_csv_path(csv_path: str | None = None) -> str:
    """解析 CSV 文件路径，优先使用传入路径，否则从项目根目录计算。

    Args:
        csv_path: 显式指定的 CSV 路径，None 则使用默认路径

    Returns:
        str: CSV 文件的绝对路径
    """
    if csv_path is not None:
        if os.path.isabs(csv_path):
            return csv_path
        return os.path.join(_get_project_root(), csv_path)

    # 默认路径：项目根目录下的 docs/distilled/长赢指数理论持仓总表.csv
    return os.path.join(
        _get_project_root(), "docs", "distilled", "长赢指数理论持仓总表.csv"
    )


def load_current_positions(csv_path: str | None = None) -> dict[str, list[dict]]:
    """读取"长赢指数理论持仓总表.csv"，返回结构化持仓数据。

    只返回净持仓 > 0 的品种。文件不存在时返回空 dict，不抛异常。

    Args:
        csv_path: CSV 文件路径，None 则使用默认路径

    Returns:
        dict: {
            "150": [
                {"品种": "中证500", "归一化名称": "中证500", "净持仓(份)": 5, "数据来源": "双重确认 ✓"},
                ...
            ],
            "S": [...],
        }
    """
    resolved = _resolve_csv_path(csv_path)

    if not os.path.isfile(resolved):
        logger.info("持仓数据文件不存在，跳过持仓展示: %s", resolved)
        return {}

    positions: dict[str, list[dict]] = {"150": [], "S": []}

    try:
        with open(resolved, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                plan = row.get("计划", "").strip()
                if plan not in ("150", "S"):
                    continue

                # 解析净持仓，过滤 <= 0 或无效值
                raw_net = row.get("当前净持仓(份)", "0").strip()
                try:
                    net_shares = float(raw_net)
                except (ValueError, TypeError):
                    continue

                if net_shares <= 0:
                    continue

                positions[plan].append({
                    "品种": row.get("品种", "").strip(),
                    "归一化名称": row.get("归一化名称", "").strip(),
                    "净持仓(份)": int(net_shares) if net_shares == int(net_shares) else net_shares,
                    "数据来源": row.get("数据来源", "").strip(),
                })

        logger.info(
            "持仓数据加载完成: 150 计划 %d 个品种，S 计划 %d 个品种",
            len(positions["150"]),
            len(positions["S"]),
        )
    except Exception as e:
        logger.error("读取持仓 CSV 失败: %s", e)
        return {}

    return positions


def _build_pe_lookup(
    market_data: dict | None,
    config: dict | None,
) -> dict[str, float | None]:
    """构建 品种名称 → PE分位 的查找表。

    同时支持按 ETF 名称（如"沪深300ETF"）和指数名称（如"沪深300"、"创业板"）匹配，
    使得理论持仓中与 etf_pool 重合的品种（如"沪深300""中证500"）能自动展示 PE 分位。

    Returns:
        dict: { "沪深300": 92.7, "中证500": 96.6, "创业板": 99.8, ... }
    """
    pe_lookup: dict[str, float | None] = {}
    if market_data is None or config is None:
        return pe_lookup

    # 按指数代码建立 PE 查找表
    indices_by_code: dict[str, dict] = {}
    for idx in market_data.get("indices", []):
        code = idx.get("code", "")
        if code and idx.get("valid"):
            indices_by_code[code] = idx

    # 遍历 ETF 品种池，用 ETF 名称和指数名称建立别名查找
    etf_pool = config.get("etf_pool", [])
    for etf in etf_pool:
        index_code = etf.get("index", "")
        idx_data = indices_by_code.get(index_code)
        if idx_data is None:
            continue
        pe_val = idx_data.get("pe_percentile")
        if pe_val is None:
            continue

        # 用 ETF 名称（如"沪深300ETF"）做 key
        etf_name = etf.get("name", "")
        if etf_name:
            pe_lookup[etf_name] = pe_val

        # 用指数名称（如"沪深300"）做 key
        idx_name = idx_data.get("name", "")
        if idx_name:
            pe_lookup[idx_name] = pe_val
            # 去掉"指"后缀（"创业板指"→"创业板"）
            if idx_name.endswith("指"):
                short = idx_name[:-1]
                pe_lookup[short] = pe_val

    return pe_lookup


def format_position_section(
    positions: dict[str, list[dict]],
    max_display: int = 10,
    market_data: dict | None = None,
    config: dict | None = None,
) -> str:
    """将持仓数据格式化为日报中的"继续持有"段落。

    格式示例：
    📋 E大理论持仓（150 计划）
    | 品种 | 净持仓 | PE分位 | 确认 |
    |------|-------|--------|------|
    | 全指医药 | 7 份 | 99.6% | ✓✓ |

    （只显示净持仓 > 0 的品种，按份数降序，最多 max_display 个）
    如果品种数超过 max_display，末尾加 "... 等共 N 个品种"。
    传入 market_data 后可自动为匹配的品种展示 PE 分位。

    Args:
        positions: load_current_positions 的返回值
        max_display: 每个计划最多显示的品种数
        market_data: fetch_all_valuations 的返回值，用于展示 PE 分位
        config: 配置字典，用于获取 ETF 品种池信息

    Returns:
        str: 格式化的持仓展示段落，无数据时返回空字符串
    """
    if not positions:
        return ""

    # 构建 指数名称 → PE分位 查找表
    pe_lookup = _build_pe_lookup(market_data, config)

    lines: list[str] = []

    for plan_name in ("150", "S"):
        items = positions.get(plan_name, [])
        if not items:
            continue

        # 按净持仓份数降序排列
        items_sorted = sorted(items, key=lambda x: x["净持仓(份)"], reverse=True)

        plan_label = "150 计划" if plan_name == "150" else "S 计划"
        lines.append(f"📋 E大理论持仓（{plan_label}）")
        # 有 PE 数据时展示 PE 分位列
        has_pe_data = bool(pe_lookup)
        if has_pe_data:
            lines.append("| 品种 | 净持仓 | PE分位 | 确认 |")
            lines.append("|------|-------|--------|------|")
        else:
            lines.append("| 品种 | 净持仓 | 确认 |")
            lines.append("|------|-------|------|")

        display_items = items_sorted[:max_display]
        for item in display_items:
            name = item["归一化名称"] or item["品种"]
            shares = item["净持仓(份)"]
            shares_str = f"{int(shares)} 份" if isinstance(shares, float) and shares == int(shares) else f"{shares} 份"

            # 数据来源确认标记
            source = item.get("数据来源", "")
            if "双重确认" in source:
                confirm = "✓✓"
            elif "仅图片" in source:
                confirm = "✓"
            elif "仅发车" in source:
                confirm = "✓"
            else:
                confirm = "?"

            # PE 分位：匹配指数名称
            if has_pe_data:
                pe_percentile = pe_lookup.get(name)
                if pe_percentile is not None:
                    pe_str = f"{pe_percentile:.1f}%"
                elif name in pe_lookup:
                    pe_str = "--"  # 指数在列表中但无数据
                else:
                    pe_str = "--"  # 名称不匹配任何指数
                lines.append(f"| {name} | {shares_str} | {pe_str} | {confirm} |")
            else:
                lines.append(f"| {name} | {shares_str} | {confirm} |")

        if len(items_sorted) > max_display:
            if has_pe_data:
                lines.append(f"| ... 等共 {len(items_sorted)} 个品种 | | | |")
            else:
                lines.append(f"| ... 等共 {len(items_sorted)} 个品种 | | |")

        lines.append("")

    return "\n".join(lines)