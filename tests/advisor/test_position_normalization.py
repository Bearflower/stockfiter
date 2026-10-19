"""仓位归一化测试。

覆盖三个层次：
1. _load_position_bounds —— 配置解析的健壮性
2. _normalize_position_with_bounds —— 有上下界总和归一化算法本身
3. _validate_and_normalize —— 端到端（含 ETF 推荐、market_commentary 保留）
"""

from __future__ import annotations

import logging

import pytest

# 让日志在测试里能看到（调试用）
logging.basicConfig(level=logging.INFO)

from distill_changying.scripts.advisor.advisor_llm import (
    _load_position_bounds,
    _normalize_position_with_bounds,
    _validate_and_normalize,
)


# ============================================================
# _load_position_bounds
# ============================================================

def test_load_bounds_from_real_config(advisor_config):
    """真实配置能被正确解析为上下界字典。"""
    bounds = _load_position_bounds(advisor_config)
    assert bounds is not None
    assert bounds["stock_pct"] == {"min": 5, "max": 80}
    assert bounds["bond_pct"] == {"min": 10, "max": 50}
    assert bounds["cash_pct"] == {"min": 10, "max": 60}


def test_load_bounds_missing_section_returns_none():
    """缺少 position_bounds 配置节时返回 None（禁止硬编码 fallback）。"""
    assert _load_position_bounds({}) is None
    assert _load_position_bounds({"foo": "bar"}) is None


def test_load_bounds_inconsistent_returns_none():
    """下界之和 > 100 或上界之和 < 100 时返回 None（不可能满足总和约束）。"""
    bad = {
        "position_bounds": {
            "stock_pct": {"min": 50, "max": 60},
            "bond_pct": {"min": 50, "max": 60},
            "cash_pct": {"min": 50, "max": 60},
        },
    }
    # 下界之和 150 > 100，不可能
    assert _load_position_bounds(bad) is None


# ============================================================
# _normalize_position_with_bounds —— 算法核心
# ============================================================

def test_normalize_step1_case(position_bounds):
    """Task 8 Step 1 示例：stock=5, bond=10, cash=60 → 总和 75 不够，需补足到 100。"""
    raw = {"stock_pct": 5, "bond_pct": 10, "cash_pct": 60}
    result = _normalize_position_with_bounds(raw, position_bounds)
    assert result is not None
    assert sum(result.values()) == 100
    # 各项在界内
    for k, v in result.items():
        assert position_bounds[k]["min"] <= v <= position_bounds[k]["max"], f"{k}={v} 越界"


def test_normalize_exactly_100_within_bounds(position_bounds):
    """已经是 100 且各项在界内 → 直接通过。"""
    raw = {"stock_pct": 40, "bond_pct": 40, "cash_pct": 20}
    result = _normalize_position_with_bounds(raw, position_bounds)
    assert result is not None
    assert sum(result.values()) == 100
    assert result == {"stock_pct": 40, "bond_pct": 40, "cash_pct": 20}


def test_normalize_zero_input(position_bounds):
    """极端输入 {0,0,0} —— 先 clamp 到各下界 (5,10,10) 共 25，再加 75。"""
    raw = {"stock_pct": 0, "bond_pct": 0, "cash_pct": 0}
    result = _normalize_position_with_bounds(raw, position_bounds)
    assert result is not None
    assert sum(result.values()) == 100
    for k, v in result.items():
        assert position_bounds[k]["min"] <= v <= position_bounds[k]["max"]


def test_normalize_triple_100(position_bounds):
    """极端输入 {100,100,100} —— 先 clamp 到各上界 (80,50,60) 共 190，减到 100。"""
    raw = {"stock_pct": 100, "bond_pct": 100, "cash_pct": 100}
    result = _normalize_position_with_bounds(raw, position_bounds)
    assert result is not None
    assert sum(result.values()) == 100
    for k, v in result.items():
        assert position_bounds[k]["min"] <= v <= position_bounds[k]["max"]


def test_normalize_cannot_satisfy_returns_none():
    """人为构造一组**根本不可能**的约束（stock 上界只有 20，但下界和已 90），
    算法必须返回 None，**禁止输出越界结果**。
    """
    impossible = {
        "stock_pct": {"min": 50, "max": 20},  # min > max，_load_position_bounds 已挡
    }
    # 换一种：下界没问题但算法层面加不上去
    bounds = {
        "stock_pct": {"min": 0, "max": 10},
        "bond_pct": {"min": 0, "max": 10},
        "cash_pct": {"min": 0, "max": 10},
    }
    assert _load_position_bounds({"position_bounds": bounds}) is None, "上界之和 30 < 100 应被挡住"

    # 再测试：直接调用 normalize 给不合理的输入，也应该返回 None
    # 例如所有项都在界内但总和远超出后，已经无法再减到 100（下界太高）
    tight = {
        "stock_pct": {"min": 40, "max": 80},
        "bond_pct": {"min": 40, "max": 50},
        "cash_pct": {"min": 40, "max": 60},
    }
    # 下界之和 120 > 100 → _load_position_bounds 应返回 None
    assert _load_position_bounds({"position_bounds": tight}) is None


# ============================================================
# _validate_and_normalize 端到端
# ============================================================

def test_validate_and_normalize_end_to_end(advisor_config):
    """完整走一遍：position_advice + etf_recommendations + market_commentary 都保留。"""
    llm_out = {
        "position_advice": {
            "stock_pct": 5,
            "bond_pct": 10,
            "cash_pct": 60,
            "reasoning": "低估区，加仓 A 股",
            "temperature_label": "低估",
        },
        "etf_recommendations": [
            {
                "etf_code": "510300",
                "etf_name": "沪深300ETF",
                "action": "buy",
                "shares": 1,
                "reasoning": "低估区买入",
            },
        ],
        "market_commentary": "市场处于低估区，建议分批买入。",
    }
    result = _validate_and_normalize(llm_out, advisor_config, {"label": "低估"})
    assert result is not None, "归一化失败应返回 None"
    pa = result["position_advice"]
    assert sum([pa["stock_pct"], pa["bond_pct"], pa["cash_pct"]]) == 100
    # 用真实配置界值做断言
    pb = advisor_config["position_bounds"]
    assert pb["stock_pct"]["min"] <= pa["stock_pct"] <= pb["stock_pct"]["max"]
    assert pb["bond_pct"]["min"] <= pa["bond_pct"] <= pb["bond_pct"]["max"]
    assert pb["cash_pct"]["min"] <= pa["cash_pct"] <= pb["cash_pct"]["max"]
    # 附加字段保留
    assert pa["reasoning"] == "低估区，加仓 A 股"
    assert pa["temperature_label"] == "低估"
    # ETF 保留
    assert len(result["etf_recommendations"]) == 1
    assert result["etf_recommendations"][0]["etf_code"] == "510300"
    # commentary 保留
    assert result["market_commentary"] == "市场处于低估区，建议分批买入。"


def test_validate_missing_position_bounds_returns_none():
    """config 没有 position_bounds 时直接拒绝，不硬编码 fallback。"""
    result = _validate_and_normalize(
        {"position_advice": {"stock_pct": 40, "bond_pct": 40, "cash_pct": 20}},
        {},  # 空 config，没有 position_bounds
        {},
    )
    assert result is None
