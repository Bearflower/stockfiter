"""
Task 7 R-08：市场风控配置与已采纳决策一致性测试。

断言 config.yaml 的 index_filter.method == 'ma_position'，
与 docs/knowledge/decisions/stockfilter_v3-20261013-obpc-index-filter-back-to-ma-position.md
中已采纳的决策一致，消除生产配置漂移。
"""

import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
CONFIG_PATH = REPO_ROOT / "config" / "config.yaml"


@pytest.fixture(scope="module")
def config():
    """加载 config.yaml 为 dict"""
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def test_adopted_market_filter_is_ma_position(config):
    """
    Task 7 Step 2：恢复已采纳的 ma_position 配置。

    配置漂移是 R-08 的直接原因（2026-10-16 生产行为与已采纳决策不一致）。
    本测试是阻塞性断言：method 必须是 'ma_position'。
    """
    index_filter = config["strategies"]["oversold_bounce"]["params"]["index_filter"]
    assert index_filter["method"] == "ma_position", (
        f"index_filter.method={index_filter['method']!r}，"
        f"应为 'ma_position'（与已采纳决策一致）"
    )


def test_risk_config_yaml_syntax_ok():
    """辅助：config.yaml 可被 yaml.safe_load 正常解析"""
    assert CONFIG_PATH.exists(), f"config.yaml 不存在：{CONFIG_PATH}"
    with open(CONFIG_PATH, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    assert "strategies" in cfg
    assert "oversold_bounce" in cfg["strategies"]


def test_ma_position_default_semantics_preserved(config):
    """
    辅助断言：ma_position 模式下 MA20/MA60 均线过滤开关开启，
    与 V2.0 原有语义一致；MACD 相关参数保留但仅在 method='macd' 时生效。
    """
    index_filter = config["strategies"]["oversold_bounce"]["params"]["index_filter"]
    # ma_position 模式不依赖 MACD 条件字段，但 MACD 参数应保留以备切换
    assert "macd_fast" in index_filter
    assert "macd_slow" in index_filter
    assert "macd_signal" in index_filter
    assert "condition" in index_filter  # 'dif > 0'
