"""
持仓数据合并模块

将两条路径的持仓数据合并为完整的"长赢指数理论持仓总表"：
- 路径 A：OCR 图片识别结果（2018/2019 年快照）
- 路径 B：发车帖解析还原（2019-2026 年操作记录）

输出：
- docs/distilled/长赢指数理论持仓总表.md
- docs/distilled/长赢指数理论持仓总表.csv
"""

from __future__ import annotations

import csv
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


# =============================================================================
# 品种名称归一化映射常量（业务知识，非硬编码参数）
# =============================================================================

NAME_MAP: dict[str, list[str]] = {
    # A股宽基
    "上证50": ["50", "上证50ETF", "50ETF"],
    "沪深300": ["300", "沪深300ETF", "300ETF"],
    "中证500": ["500", "中证500ETF", "500ETF", "建信500", "华夏500", "富国500"],
    "创业板": ["创业板指", "创业板ETF", "创业板指数", "广发创业板"],

    # 行业主题
    "中证红利": ["红利", "红利低波100", "博时红利低波100", "富国中证红利指数增强"],
    "全指医药": ["医药", "全指医药", "华宝医疗A", "华宝医疗C", "医疗C", "中证医疗A",
                 "全球医疗保健", "广发全球医疗"],
    "全指金融": ["金融", "金融地产", "证券公司", "证券保险", "信息技术", "全指信息",
                 "易方达信息产业"],
    "中证环保": ["环保"],
    "中证传媒": ["传媒", "文体娱乐"],
    "养老产业": ["养老", "大摩健康产业", "融通健康产业"],
    "消费": ["消费A", "消费C", "消费行业", "汇添富消费", "富国消费C", "300C",
             "沪深300C", "易方达沪深300"],

    # 海外
    "恒生指数": ["恒生", "恒生科技", "恒生医疗"],
    "中国海外互联": ["海外互联", "海外互联网"],
    "德国DAX": ["德国DAX", "纳斯达克100", "美国标普500"],

    # 债券
    "博时信用债": ["博时债", "信用债"],
    "7-10年国开债": ["国开债", "国债", "7-10国开债", "1-3年国开债", "国开债券"],
    "广发纯债": ["纯债"],
    "兴全转债": ["可转债"],
    "海外收益债": ["中银美元债", "富国全球债A", "富国全球债C", "汇添富美元债"],

    # 商品
    "黄金": ["上海金"],
    "原油": ["石油基金", "华宝油气", "南方原油"],

    # 其他（从 OCR 数据中发现但需要特殊处理）
    "中小盘": ["中小盘"],
    "货币": ["货币"],
    "价值": ["价值"],
    "海外新兴": ["海外新兴"],
    "香港": ["香港"],
    "豆粕": ["豆粕"],
    "A股": ["A股"],           # OCR 中的大类汇总
    "大盘": ["大盘"],         # OCR 中的大类汇总
    "债券": ["债券"],         # OCR 中的大类汇总
}


@dataclass
class OCRPosition:
    """OCR 识别的持仓数据"""
    name: str                    # 原始名称
    ratio: Optional[float]       # 2019 年配置占比（%）
    category: Optional[str] = None     # 大类资产类别
    sub_category: Optional[str] = None    # 具体资产类别
    pnl_rate: Optional[float] = None    # 浮动盈亏率（%）


@dataclass
class PositionRecord:
    """发车解析的持仓记录"""
    plan: str                    # 计划名称（150/S）
    fund_name: str               # 基金名称
    fund_code: str               # 基金代码
    net_position: float          # 净持仓（份）
    total_buy: float             # 累计买入（份）
    total_sell: float            # 累计卖出（份）
    remark: str = ""             # 备注


@dataclass
class MergedPosition:
    """合并后的持仓记录"""
    original_name: str           # 原始品种名称
    normalized_name: str         # 归一化名称
    ocr_ratio: Optional[float]   # 2019 配置占比（%）
    net_position: float = 0.0    # 当前净持仓（份）
    total_buy: float = 0.0       # 累计买入（份）
    total_sell: float = 0.0      # 累计卖出（份）
    source: str = ""             # 数据来源标注


def normalize_name(name: str) -> str:
    """
    将品种名称归一化为标准名称

    Args:
        name: 原始品种名称

    Returns:
        归一化后的标准名称，若无法匹配则返回原始值
    """
    if not name:
        return name

    name_stripped = name.strip()

    # 精确匹配
    for standard_name, aliases in NAME_MAP.items():
        if name_stripped == standard_name or name_stripped in aliases:
            return standard_name

    # 模糊匹配（处理包含关系）
    for standard_name, aliases in NAME_MAP.items():
        for alias in aliases:
            if alias in name_stripped or name_stripped in alias:
                return standard_name

    # 无法归一化，保留原始值并记录警告
    logger.warning(f"无法归一化的品种名称: {name_stripped}，将保留原值")
    return name_stripped


def parse_ocr_markdown(ocr_path: Path) -> dict[str, OCRPosition]:
    """
    解析 OCR 识别结果 Markdown 文件，提取 2019 年配置数据

    Args:
        ocr_path: OCR 结果文件路径

    Returns:
        以品种名称为键的 OCR 持仓字典
    """
    logger.info(f"正在解析 OCR 文件: {ocr_path}")

    if not ocr_path.exists():
        raise FileNotFoundError(f"OCR 文件不存在: {ocr_path}")

    content = ocr_path.read_text(encoding="utf-8")
    positions: dict[str, OCRPosition] = {}

    # 提取 2019 年 2 月三层配置图的 JSON 数据
    pattern_2019 = r'```json\s*(\{[\s\S]*?"品种"\s*:\s*\[[\s\S]*?\][\s\S]*?)\s*```'
    match_2019 = re.search(pattern_2019, content)

    if match_2019:
        try:
            data = json.loads(match_2019.group(1))
            for item in data.get("品种", []):
                name = item.get("名称", "")
                ratio_str = item.get("占比", "0%")

                # 解析占比字符串（如 "6.92%"）
                ratio = None
                if ratio_str.endswith("%"):
                    try:
                        ratio = float(ratio_str[:-1])
                    except ValueError:
                        logger.warning(f"无法解析占比: {ratio_str}")

                positions[name] = OCRPosition(
                    name=name,
                    ratio=ratio
                )
        except json.JSONDecodeError as e:
            logger.error(f"解析 2019 配置 JSON 失败: {e}")

    logger.info(f"从 OCR 文件解析出 {len(positions)} 个品种")
    return positions


def parse_position_csv(csv_path: Path) -> dict[str, list[PositionRecord]]:
    """
    解析发车帖还原的 CSV 文件

    Args:
        csv_path: CSV 文件路径

    Returns:
        以计划名称为键的持仓记录列表字典
    """
    logger.info(f"正在解析持仓 CSV 文件: {csv_path}")

    if not csv_path.exists():
        raise FileNotFoundError(f"CSV 文件不存在: {csv_path}")

    records_by_plan: dict[str, list[PositionRecord]] = {}

    with open(csv_path, "r", encoding="utf-8-sig") as f:  # 使用 utf-8-sig 自动去除 BOM 头
        reader = csv.DictReader(f)

        for row in reader:
            try:
                record = PositionRecord(
                    plan=row["计划"].strip(),
                    fund_name=row["基金名称"].strip(),
                    fund_code=row["基金代码"].strip(),
                    net_position=float(row["净持仓(份)"] or 0),
                    total_buy=float(row["累计买入(份)"] or 0),
                    total_sell=float(row["累计卖出(份)"] or 0),
                    remark=row.get("备注", "").strip()
                )

                if record.plan not in records_by_plan:
                    records_by_plan[record.plan] = []

                records_by_plan[record.plan].append(record)

            except (KeyError, ValueError) as e:
                logger.warning(f"解析行失败: {row}, 错误: {e}")

    total_records = sum(len(records) for records in records_by_plan.values())
    logger.info(f"从 CSV 文件解析出 {total_records} 条记录，涉及 {len(records_by_plan)} 个计划")

    return records_by_plan


def merge_position_data(
    ocr_path: Path,
    position_csv_path: Path
) -> dict[str, list[MergedPosition]]:
    """
    合并两条路径的持仓数据

    合并逻辑：
    1. 读取 OCR 的 2019 年配置图数据 → 得到每个品种的占比%
    2. 读取发车解析的 CSV → 得到每个品种的净持仓份数
    3. 做品种名称归一化
    4. 对每个唯一品种：
       - 如果只在 OCR 中出现 → 标注 "仅图片确认(2018/2019)"
       - 如果只在发车解析中出现 → 标注 "仅发车记录(2019-2026)"
       - 如果两边都有 → 标注 "双重确认 ✓"

    Args:
        ocr_path: OCR 图片识别结果文件路径
        position_csv_path: 发车帖解析还原 CSV 文件路径

    Returns:
        以计划名称为键的合并后持仓列表字典
    """
    logger.info("=" * 60)
    logger.info("开始合并持仓数据")
    logger.info("=" * 60)

    # 读取两条路径的数据
    ocr_positions = parse_ocr_markdown(ocr_path)
    csv_records = parse_position_csv(position_csv_path)

    merged_by_plan: dict[str, list[MergedPosition]] = {}

    # 对每个计划分别处理
    for plan_name, records in csv_records.items():
        logger.info(f"正在处理 {plan_name} 计划...")

        # 构建归一化名称 → 持仓记录的映射（同一归一化名称可能有多条记录）
        normalized_records: dict[str, list[PositionRecord]] = {}
        for record in records:
            norm_name = normalize_name(record.fund_name)
            if norm_name not in normalized_records:
                normalized_records[norm_name] = []
            normalized_records[norm_name].append(record)

        # 收集所有出现的品种（去重）
        all_varieties: set[str] = set()

        # 添加 CSV 中的品种
        all_varieties.update(normalized_records.keys())

        # 添加 OCR 中的品种（也需要归一化）
        ocr_normalized: dict[str, OCRPosition] = {}
        for ocr_name, ocr_pos in ocr_positions.items():
            norm_name = normalize_name(ocr_name)
            ocr_normalized[norm_name] = ocr_pos
            all_varieties.add(norm_name)

        # 合并数据
        merged_list: list[MergedPosition] = []
        for variety in sorted(all_varieties):
            # 聚合该品种的所有持仓记录
            csv_record_list = normalized_records.get(variety, [])
            net_pos = sum(r.net_position for r in csv_record_list)
            total_buy = sum(r.total_buy for r in csv_record_list)
            total_sell = sum(r.total_sell for r in csv_record_list)

            # 获取原始名称（取第一条记录的名称）
            original_name = csv_record_list[0].fund_name if csv_record_list else variety

            # 获取 OCR 占比
            ocr_pos = ocr_normalized.get(variety)
            ocr_ratio = ocr_pos.ratio if ocr_pos else None

            # 判断数据来源
            has_ocr = variety in ocr_normalized
            has_csv = len(csv_record_list) > 0

            if has_ocr and has_csv:
                source = "双重确认 ✓"
            elif has_ocr:
                source = "仅图片确认(2018/2019)"
            elif has_csv:
                source = "仅发车记录(2019-2026)"
            else:
                source = "未知"

            merged = MergedPosition(
                original_name=original_name,
                normalized_name=variety,
                ocr_ratio=ocr_ratio,
                net_position=net_pos,
                total_buy=total_buy,
                total_sell=total_sell,
                source=source
            )
            merged_list.append(merged)

        # 按 OCR 占比降序排序（有占比的排前面）
        merged_list.sort(
            key=lambda x: (x.ocr_ratio is None, -(x.ocr_ratio or 0))
        )

        merged_by_plan[plan_name] = merged_list
        logger.info(f"{plan_name} 计划合并完成，共 {len(merged_list)} 个品种")

    return merged_by_plan


def generate_markdown(
    merged_data: dict[str, list[MergedPosition]],
    output_path: Path
) -> None:
    """
   生成 Markdown 格式的持仓总表

    Args:
        merged_data: 合并后的持仓数据
        output_path: 输出文件路径
    """
    logger.info(f"正在生成 Markdown 文件: {output_path}")

    lines: list[str] = []

    # 文档头部
    lines.append("# 长赢指数理论持仓总表")
    lines.append("")
    lines.append("> **数据来源**：路径 A（OCR 图片快照 2018.08 / 2019.02）+ "
                 "路径 B（发车帖解析 2019-2026）")
    lines.append("")

    # 统计摘要（先收集数据用于统计）
    stats: dict[str, dict] = {}
    ocr_only_varieties: set[str] = set()
    csv_only_varieties: set[str] = set()
    dual_confirmed: dict[str, int] = {}

    for plan_name, merged_list in merged_data.items():
        plan_stats = {
            "total": len(merged_list),
            "dual_confirmed": 0,
            "ocr_only": 0,
            "csv_only": 0
        }

        for item in merged_list:
            if "双重确认" in item.source:
                plan_stats["dual_confirmed"] += 1
            elif "仅图片确认" in item.source:
                plan_stats["ocr_only"] += 1
                ocr_only_varieties.add(item.normalized_name)
            elif "仅发车记录" in item.source:
                plan_stats["csv_only"] += 1
                csv_only_varieties.add(item.normalized_name)

        stats[plan_name] = plan_stats
        dual_confirmed[plan_name] = plan_stats["dual_confirmed"]

    # 输出各计划的表格
    for plan_name, merged_list in merged_data.items():
        lines.append(f"## {plan_name} 计划")
        lines.append("")
        lines.append("| 品种 | 归一化名称 | 2019配置占比 | 当前净持仓(份) | "
                     "累计买入 | 累计卖出 | 数据来源 |")
        lines.append("|------|-----------|-------------|---------------|"
                     "---------|---------|---------|")

        for item in merged_list:
            ratio_str = f"~{item.ocr_ratio:.2f}%" if item.ocr_ratio is not None else "-"
            lines.append(
                f"| {item.original_name} | {item.normalized_name} | {ratio_str} | "
                f"{item.net_position:.1f} | {item.total_buy:.1f} | "
                f"{item.total_sell:.1f} | {item.source} |"
            )

        lines.append("")

    # 统计摘要部分
    lines.append("## 统计摘要")
    lines.append("")

    for plan_name, plan_stats in stats.items():
        lines.append(f"- **{plan_name}计划** 确认品种数: {plan_stats['total']} "
                     f"(其中双重确认 {plan_stats['dual_confirmed']})")
        lines.append(f"  - 仅在图片中出现（早期持仓，可能已清仓）: "
                     f"{plan_stats['ocr_only']} 个品种")
        lines.append(f"  - 仅在发车记录中出现（后期新增品种）: "
                     f"{plan_stats['csv_only']} 个品种")

    lines.append("")
    lines.append("-" * 40)
    lines.append("")
    lines.append("**跨计划汇总**：")
    lines.append(f"- 仅在图片中出现的品种: {', '.join(sorted(ocr_only_varieties)) or '无'}")
    lines.append(f"- 仅在发车记录中出现的品种: {', '.join(sorted(csv_only_varieties)) or '无'}")
    lines.append("")
    lines.append("---")
    lines.append(f"*文档自动生成于 {__file__}*")

    # 写入文件
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines), encoding="utf-8")
    logger.info(f"Markdown 文件已生成: {output_path}")


def generate_csv(
    merged_data: dict[str, list[MergedPosition]],
    output_path: Path
) -> None:
    """
    生成 CSV 格式的持仓总表

    Args:
        merged_data: 合并后的持仓数据
        output_path: 输出文件路径
    """
    logger.info(f"正在生成 CSV 文件: {output_path}")

    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)

        # 表头
        writer.writerow([
            "计划", "品种", "归一化名称", "2019配置占比(%)",
            "当前净持仓(份)", "累计买入(份)", "累计卖出(份)", "数据来源"
        ])

        # 数据行
        for plan_name, merged_list in merged_data.items():
            for item in merged_list:
                writer.writerow([
                    plan_name,
                    item.original_name,
                    item.normalized_name,
                    f"{item.ocr_ratio:.2f}" if item.ocr_ratio is not None else "",
                    f"{item.net_position:.1f}",
                    f"{item.total_buy:.1f}",
                    f"{item.total_sell:.1f}",
                    item.source
                ])

    logger.info(f"CSV 文件已生成: {output_path}")


def load_config(config_path: Path) -> dict:
    """
    加载 YAML 配置文件

    Args:
        config_path: 配置文件路径

    Returns:
        配置字典
    """
    if not config_path.exists():
        logger.warning(f"配置文件不存在: {config_path}，使用默认配置")
        return {}

    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    logger.info(f"已加载配置文件: {config_path}")
    return config or {}


def main() -> None:
    """主函数：执行持仓数据合并流程"""
    # 确定项目根目录和配置路径
    script_dir = Path(__file__).parent
    project_root = script_dir.parent.parent  # distill_changying/
    config_path = script_dir / "config.yaml"

    # 加载配置
    config = load_config(config_path)

    # 从配置或默认值获取路径
    paths_config = config.get("paths", {})
    output_dir_name = paths_config.get("output_dir", "docs/distilled")

    # 构建输入输出路径
    ocr_file = project_root / output_dir_name / "图片OCR识别结果.md"
    position_csv = project_root / output_dir_name / "持仓还原表.csv"
    output_md = project_root / output_dir_name / "长赢指数理论持仓总表.md"
    output_csv = project_root / output_dir_name / "长赢指数理论持仓总表.csv"

    logger.info("=" * 60)
    logger.info("长赢指数持仓数据合并工具")
    logger.info("=" * 60)
    logger.info(f"输入文件:")
    logger.info(f"  - OCR 识别结果: {ocr_file}")
    logger.info(f"  - 发车解析 CSV: {position_csv}")
    logger.info(f"输出文件:")
    logger.info(f"  - Markdown 总表: {output_md}")
    logger.info(f"  - CSV 总表: {output_csv}")
    logger.info("=" * 60)

    try:
        # 执行合并
        merged_data = merge_position_data(ocr_file, position_csv)

        # 生成输出文件
        generate_markdown(merged_data, output_md)
        generate_csv(merged_data, output_csv)

        # 输出统计摘要
        logger.info("=" * 60)
        logger.info("合并完成！统计摘要：")
        for plan_name, merged_list in merged_data.items():
            dual_count = sum(1 for m in merged_list if "双重确认" in m.source)
            logger.info(f"  - {plan_name}计划: {len(merged_list)} 个品种 "
                       f"(双重确认 {dual_count})")
        logger.info("=" * 60)

    except FileNotFoundError as e:
        logger.error(f"文件未找到: {e}")
        raise
    except Exception as e:
        logger.error(f"合并过程出错: {e}")
        raise


if __name__ == "__main__":
    main()
