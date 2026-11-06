"""伊利独立波段策略与研究回测的口径一致性。"""

import math

import pandas as pd
import pytest

from scripts import backtest_swing as swing
from scripts.run_yili_swing import evaluate_file
from strategy.yili_swing.strategy import YiliSwingStrategy


def _front_bars(oscillate=True):
    dates = pd.bdate_range("2025-01-02", periods=200)
    prices = [20 + i * 0.03 + (2 * math.sin(i / 5) if oscillate else 0)
              for i in range(len(dates))]
    bars = pd.DataFrame({
        "date": dates,
        "open": prices, "high": [x + 0.1 for x in prices],
        "low": [x - 0.1 for x in prices], "close": prices,
        "volume": [1000] * len(dates),
    })
    bars.attrs["adjustment"] = "front"
    return bars


def test_independent_strategy_uses_same_indicators_and_signals_as_backtest():
    strategy = YiliSwingStrategy()
    bars = _front_bars()
    research = swing.calc_indicators(bars.copy())
    actual = strategy.indicators(bars)
    for column in ("rsi_trend", "ma_trend_fast", "ma_trend_slow"):
        pd.testing.assert_series_equal(actual[column], research[column])
    buy, sell = strategy.signals(actual)
    expected_buy, expected_sell = swing.gen_signals(research, "trend_rsi")
    pd.testing.assert_series_equal(buy, expected_buy)
    pd.testing.assert_series_equal(sell, expected_sell)
    assert int(buy.sum()) > 0


def test_strategy_rejects_unverified_adjustment():
    strategy = YiliSwingStrategy()
    bars = _front_bars()
    bars.attrs.clear()
    with pytest.raises(ValueError, match="前复权"):
        strategy.analyze("600887", {"d": bars})


def test_strategy_only_analyzes_configured_stock_and_latest_bar():
    strategy = YiliSwingStrategy()
    bars = _front_bars()
    assert strategy.analyze("002539", {"d": bars}) is None
    snapshot = strategy.snapshot("600887", {"d": bars})
    assert snapshot["date"] == bars["date"].iloc[-1].strftime("%Y-%m-%d")
    assert snapshot["rsi"] == pytest.approx(
        strategy.indicators(bars)["rsi_trend"].iloc[-1])


def test_replay_can_keep_end_position_open_without_forced_exit():
    bars = pd.DataFrame({
        "date": pd.to_datetime(["2026-11-02", "2026-11-03"]),
        "open": [30.0, 30.0], "high": [30.0, 30.0],
        "low": [30.0, 30.0], "close": [30.0, 30.0],
    })
    buy = pd.Series([True, False])
    sell = pd.Series([False, False])
    trades, held, equity, position = swing.run_backtest(
        bars, buy, sell, "indicator", swing.STOP_PARAMS["take_profit"],
        swing.STOP_PARAMS["stop_loss"], close_at_end=False,
        return_position=True,
    )
    assert trades == []
    assert held[-1]
    assert position["entry_date"] == "2026-11-03"
    assert equity[-1] > 0


def test_standalone_runner_reports_replayed_position_and_pending_action(tmp_path):
    bars = _front_bars(oscillate=False)
    bars.insert(0, "code", "600887")
    path = tmp_path / "front.csv"
    bars.to_csv(path, index=False)
    expected_date = bars["date"].iloc[-1].strftime("%Y-%m-%d")
    result = evaluate_file(str(path), "front", expected_date)
    assert result["code"] == "600887"
    assert result["data_date"] == expected_date
    assert result["raw_signal"] == "hold"
    assert result["model_position"] == "cash"
    assert result["next_open_action"] == "none"
    assert result["closed_trades"] == 0


def test_standalone_runner_rejects_wrong_data_date(tmp_path):
    bars = _front_bars()
    bars.insert(0, "code", "600887")
    path = tmp_path / "front.csv"
    bars.to_csv(path, index=False)
    with pytest.raises(ValueError, match="日期"):
        evaluate_file(str(path), "front", "2026-11-06")
