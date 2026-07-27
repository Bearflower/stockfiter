"""
E大投资决策助手市场温度评估模块

基于 E大的"估值温度计"框架，将 PE 历史分位映射到五个温度区间：
钻石坑 -> 低估 -> 正常 -> 高估 -> 泡沫

所有阈值从 config.yaml 读取，禁止硬编码。
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# 温度标签与配置键的映射
_LABEL_TO_KEY: dict[str, str] = {
    "钻石坑": "diamond_pit",
    "低估": "undervalued",
    "正常": "normal",
    "高估": "overvalued",
    "泡沫": "bubble",
}

# 温度标签的固定顺序（用于边界判断）
_TEMPERATURE_ORDER: list[tuple[str, str]] = [
    ("钻石坑", "diamond_pit"),
    ("低估", "undervalued"),
    ("正常", "normal"),
    ("高估", "overvalued"),
    ("泡沫", "bubble"),
]


def get_temperature_label(pe_percentile: float, config: dict) -> str:
    """根据 PE 分位返回温度标签。

    从 config["valuation"]["thresholds"] 读取阈值区间：
    - 钻石坑: pe_percentile < diamond_pit
    - 低估:   diamond_pit <= pe_percentile < undervalued
    - 正常:   undervalued <= pe_percentile < normal
    - 高估:   normal <= pe_percentile < overvalued
    - 泡沫:   pe_percentile >= overvalued

    Args:
        pe_percentile: PE 历史分位，范围 [0.0, 100.0]，越接近 0 越低估
        config: 完整配置字典

    Returns:
        str: 温度标签（"钻石坑"/"低估"/"正常"/"高估"/"泡沫"）

    Raises:
        KeyError: 配置缺少 valuation.thresholds 或缺少某个阈值键
    """
    thresholds: dict[str, float] = config["valuation"]["thresholds"]

    # 从低到高逐个判断，找到第一个不满足"小于阈值"的区间即为当前温度
    for label, key in _TEMPERATURE_ORDER:
        threshold = thresholds[key]
        if pe_percentile < threshold:
            return label

    # 兜底（理论上 pe_percentile 不会 ≥ bubble 阈值，但做防御性处理）
    return "泡沫"


def calculate_market_temperature(
    indices_data: list[dict],
    config: dict,
) -> dict[str, Any]:
    """计算全市场温度评估。

    处理逻辑：
    1. 只计算 valid=True 的指数
    2. 取各指数 PE 分位的等权平均
    3. 判断平均分位对应的温度区间
    4. 返回结果包含：整体温度标签、各指数详情、有效指数数量、置信度

    Args:
        indices_data: fetch_all_valuations 返回的指数列表，每项含：
                      name/code/pe/pe_percentile/valid
        config: 完整配置字典

    Returns:
        dict: 市场温度评估结果，格式：
            {
                "label": "正常",
                "avg_percentile": 45.2,
                "confidence": "高（6/6 指数有效）",
                "details": [
                    {"name": "上证指数", "code": "000001", "pe": 12.5,
                     "percentile": 25.3, "label": "低估"},
                    ...
                ]
            }
    """
    thresholds: dict[str, float] = config["valuation"]["thresholds"]

    # 统计有效指数数
    total_count = len(indices_data)
    valid_indices = [d for d in indices_data if d.get("valid")]

    if not valid_indices:
        logger.warning("没有有效的指数数据，无法计算市场温度")
        return {
            "label": "未知",
            "avg_percentile": 0.0,
            "confidence": f"无（0/{total_count} 指数有效）",
            "details": [],
        }

    valid_count = len(valid_indices)

    # 收集每个有效指数的 PE 分位并计算温度标签
    details: list[dict] = []
    percentile_sum = 0.0
    percentile_count = 0

    for d in valid_indices:
        name = d.get("name", d.get("code", "未知"))
        code = d.get("code", "")
        pe = d.get("pe")
        percentile = d.get("pe_percentile")

        if percentile is not None:
            label = get_temperature_label(percentile, config)
            percentile_sum += percentile
            percentile_count += 1
        else:
            label = "无数据"

        details.append({
            "name": name,
            "code": code,
            "pe": pe,
            "percentile": percentile,
            "label": label,
        })

    if percentile_count == 0:
        logger.warning("所有有效指数的 PE 分位数据均缺失")
        return {
            "label": "未知",
            "avg_percentile": 0.0,
            "confidence": f"无分位数据（{valid_count}/{total_count} 指数有效但无分位）",
            "details": details,
        }

    avg_percentile = round(percentile_sum / percentile_count, 2)
    overall_label = get_temperature_label(avg_percentile, config)

    # 置信度评估：基于有效指数占比
    confidence_config = config["valuation"].get("confidence_thresholds", {})
    high_threshold = confidence_config.get("high", 0.8)
    medium_threshold = confidence_config.get("medium", 0.5)

    ratio = valid_count / total_count if total_count > 0 else 0
    if ratio >= high_threshold:
        confidence_level = "高"
    elif ratio >= medium_threshold:
        confidence_level = "中"
    else:
        confidence_level = "低"
    confidence = f"{confidence_level}（{valid_count}/{total_count} 指数有效）"

    logger.info(
        "全市场温度: %s（平均 PE 分位 %.2f%%），置信度: %s",
        overall_label, avg_percentile, confidence,
    )

    return {
        "label": overall_label,
        "avg_percentile": avg_percentile,
        "confidence": confidence,
        "details": details,
    }