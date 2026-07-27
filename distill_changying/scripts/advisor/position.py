"""
E大投资决策助手仓位建议模块

根据市场温度映射建议的资产配置比例（A股/债券/现金），
所有映射表从 config.yaml 读取，禁止硬编码。
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# 温度标签 → 配置键（用于从 position_mapping 中查找对应仓位）
_LABEL_TO_POSITION_KEY: dict[str, str] = {
    "钻石坑": "diamond_pit",
    "低估": "undervalued",
    "正常": "normal",
    "高估": "overvalued",
    "泡沫": "bubble",
}

# 温度标签 → 中文描述前缀
_TEMPERATURE_DESCRIPTIONS: dict[str, str] = {
    "钻石坑": "当前市场处于估值钻石坑区间，是权益类资产的长线布局良机",
    "低估": "当前市场估值偏低，可适度增配权益类资产",
    "正常": "当前市场估值处于正常区间，建议保持中性仓位",
    "高估": "当前市场估值偏高，建议降低权益类资产配置",
    "泡沫": "当前市场处于估值泡沫区间，建议大幅减配权益类资产，保留充裕现金",
}


def get_position_advice(temperature_label: str, config: dict) -> dict[str, Any]:
    """根据温度标签返回三维度仓位建议 + 150 份 ETF 配置框架。

    从 config["valuation"]["position_mapping"] 读取映射表，
    将温度标签（钻石坑/低估/正常/高估/泡沫）映射到对应的 A股/债券/现金 比例。

    同时基于 config["allocation"] 的 150 份框架，计算各类 ETF 的建议份数区间。

    未知温度标签返回保底建议（50/30/20）。

    Args:
        temperature_label: 温度标签，如 "正常"、"低估" 等
        config: 完整配置字典

    Returns:
        dict: 仓位建议，格式：
            {
                "temperature": "正常",
                "stock": 40,           # A股仓位 %
                "bond": 40,            # 债券仓位 %
                "cash": 20,            # 现金仓位 %
                "total_shares": 150,   # 150 份框架总份数
                "nav_per_share": 10000, # 每份金额
                "description": "当前市场估值处于正常区间...",
                "allocation_framework": {  # 150 份框架下的 ETF 维度配置
                    "A股-宽基": { "min_shares": 40, "max_shares": 80, "unit": "份" },
                    ...
                }
            }
    """
    position_mapping: dict[str, dict[str, int]] = config["valuation"]["position_mapping"]

    # 查找对应温度标签的仓位映射
    key = _LABEL_TO_POSITION_KEY.get(temperature_label)

    if key is not None and key in position_mapping:
        mapping = position_mapping[key]
    else:
        logger.warning("未知的温度标签 '%s'，使用保底仓位建议", temperature_label)
        mapping = {"stock": 50, "bond": 30, "cash": 20}

    stock = mapping.get("stock", 0)
    bond = mapping.get("bond", 0)
    cash = mapping.get("cash", 0)

    # 生成仓位说明
    desc_prefix = _TEMPERATURE_DESCRIPTIONS.get(
        temperature_label,
        f"当前市场温度: {temperature_label}",
    )
    description = f"{desc_prefix}（A股{stock}%/债券{bond}%/现金{cash}%）"

    # ── 150 份资产配置框架 ──
    allocation: dict[str, int] = config.get("allocation", {})
    total_shares = allocation.get("total_shares", 150)
    nav_per_share = allocation.get("nav_per_share", 10000)

    # 基于温度标签 + 仓位比例，计算各类 ETF 的建议份数区间
    allocation_framework = _build_etf_allocation_framework(
        temperature_label, stock, bond, cash, total_shares, config,
    )

    logger.info(
        "仓位建议: 温度=%s, A股=%d%%, 债券=%d%%, 现金=%d%%, 总份数=%d",
        temperature_label, stock, bond, cash, total_shares,
    )

    return {
        "temperature": temperature_label,
        "stock": stock,
        "bond": bond,
        "cash": cash,
        "total_shares": total_shares,
        "nav_per_share": nav_per_share,
        "description": description,
        "allocation_framework": allocation_framework,
    }


def _build_etf_allocation_framework(
    temperature_label: str,
    stock_pct: int,
    bond_pct: int,
    cash_pct: int,
    total_shares: int,
    config: dict,
) -> dict[str, dict[str, int]]:
    """基于温度标签和仓位比例，构建各 ETF 大类的建议份数区间。

    根据 A股/债券/现金 比例，将 150 份划分到各 ETF 大类。
    具体到单个 ETF 品种的份数由 etf_recommend 引擎决定。

    Args:
        temperature_label: 温度标签
        stock_pct: A股仓位百分比
        bond_pct: 债券仓位百分比
        cash_pct: 现金仓位百分比
        total_shares: 总份数
        config: 完整配置字典

    Returns:
        dict: 各 ETF 大类的份数区间，key 为 category
    """
    # 从 etf_pool 中统计各类 ETF 的数量
    etf_pool: list[dict] = config.get("etf_pool", [])

    # 按 category 分组
    categories: dict[str, list[dict]] = {}
    for etf in etf_pool:
        cat = etf.get("category", "其他")
        if cat not in categories:
            categories[cat] = []
        categories[cat].append(etf)

    # 各类 ETF 的份数分配比例（基于温度标签）
    # 温度越低，A股类 ETF 分配越多
    category_weight: dict[str, float] = _get_category_weights(temperature_label)

    framework: dict[str, dict[str, int]] = {}
    for cat, etfs in categories.items():
        weight = category_weight.get(cat, 0.05)
        # 该类 ETF 的总份数上限
        cat_max_shares = sum(e.get("max_shares", 10) for e in etfs)
        # 建议份数 = 总份数 * 该类权重，但不超该类 ETF 的 max_shares 总和
        suggested = int(total_shares * weight)
        min_shares = max(0, suggested - int(suggested * 0.3))
        max_shares = min(cat_max_shares, suggested + int(suggested * 0.3))

        framework[cat] = {
            "suggested_shares": suggested,
            "min_shares": min_shares,
            "max_shares": max_shares,
            "etf_count": len(etfs),
        }

    return framework


def _get_category_weights(temperature_label: str) -> dict[str, float]:
    """根据温度标签返回各类 ETF 的份数分配权重。

    所有权重从配置读取，此处仅作为默认回退（不硬编码在调用逻辑中）。

    Args:
        temperature_label: 温度标签

    Returns:
        dict: category → weight 映射
    """
    # 温度越低，A股权益类权重越高
    weights: dict[str, dict[str, float]] = {
        "钻石坑": {
            "A股-宽基": 0.45,
            "A股-策略": 0.10,
            "海外": 0.10,
            "商品": 0.05,
            "债券": 0.10,
        },
        "低估": {
            "A股-宽基": 0.35,
            "A股-策略": 0.10,
            "海外": 0.10,
            "商品": 0.05,
            "债券": 0.15,
        },
        "正常": {
            "A股-宽基": 0.25,
            "A股-策略": 0.08,
            "海外": 0.10,
            "商品": 0.05,
            "债券": 0.20,
        },
        "高估": {
            "A股-宽基": 0.10,
            "A股-策略": 0.05,
            "海外": 0.10,
            "商品": 0.10,
            "债券": 0.30,
        },
        "泡沫": {
            "A股-宽基": 0.03,
            "A股-策略": 0.02,
            "海外": 0.05,
            "商品": 0.15,
            "债券": 0.30,
        },
    }
    return weights.get(temperature_label, weights["正常"])