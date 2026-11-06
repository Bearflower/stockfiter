"""伊利波段研究的交易与数据口径回归测试。"""

import pandas as pd
import pytest

from scripts import backtest_swing as swing


def _bars(prices, dates=None):
    dates = dates or pd.date_range("2026-11-02", periods=len(prices), freq="B")
    return pd.DataFrame({
        "date": pd.to_datetime(dates),
        "open": prices, "high": prices, "low": prices, "close": prices,
    })


def test_round_trip_uses_notional_fees_and_scale_invariant_nav():
    bars = _bars([30.0, 30.0, 30.0])
    buy = pd.Series([True, False, False])
    sell = pd.Series([False, True, False])
    results = [swing.run_backtest(bars, buy, sell, "indicator", .15, .08, capital)
               for capital in (1.0, 10000.0)]
    expected = ((1 - swing.COST["slippage"])
                * (1 - swing.COST["commission"] - swing.COST["stamp_tax"])
                / ((1 + swing.COST["slippage"])
                   * (1 + swing.COST["commission"])))
    for capital, (trades, _, equity) in zip((1.0, 10000.0), results):
        assert equity[-1] / capital == pytest.approx(expected)
        assert trades[0]["ret"] == pytest.approx(expected - 1)


def test_sell_tax_changes_on_effective_date():
    before = _bars([30.0, 30.0, 30.0], ["2023-08-23", "2023-08-24", "2023-08-25"])
    after = _bars([30.0, 30.0, 30.0], ["2023-08-25", "2023-08-28", "2023-08-29"])
    buy = pd.Series([True, False, False])
    sell = pd.Series([False, True, False])
    old = swing.run_backtest(before, buy, sell, "indicator", .15, .08)[2][-1]
    new = swing.run_backtest(after, buy, sell, "indicator", .15, .08)[2][-1]
    assert new > old
    assert new - old == pytest.approx(0.0005, abs=0.00001)


def test_documented_trend_rsi_signal_requires_trend_and_crossing():
    df = pd.DataFrame({
        "rsi_trend": [35.0, 29.0, 28.0, 71.0],
        "ma_trend_fast": [10.0, 10.0, 9.0, 10.0],
        "ma_trend_slow": [9.0, 9.0, 10.0, 9.0],
    })
    buy, sell = swing.gen_signals(df, "trend_rsi")
    assert buy.tolist() == [False, True, False, False]
    assert sell.tolist() == [False, False, False, True]


def test_headered_eight_column_export_loads(tmp_path):
    path = tmp_path / "bars.csv"
    path.write_text(
        "code,date,open,high,low,close,volume,amount\n"
        "600887,2026-11-02,30,31,29,30,100,3000\n",
        encoding="utf-8",
    )
    rows = swing.load_data(str(path))
    assert rows["600887"].iloc[0]["close"] == 30


def test_stop_mode_ignores_indicator_sell_and_gap_take_profit():
    df = pd.DataFrame({
        "date": pd.to_datetime(["2026-11-02", "2026-11-03", "2026-11-04"]),
        "open": [100, 100, 120], "high": [100, 100, 122],
        "low": [100, 100, 119], "close": [100, 100, 120],
    })
    buy = pd.Series([True, False, False])
    sell = pd.Series([False, True, False])
    trades, _, _ = swing.run_backtest(df, buy, sell, "stop", .15, .08)
    assert trades[0]["reason"] == "止盈"
    assert trades[0]["exit"] == 120


def test_final_strategy_is_only_run_for_yili():
    assert "trend_rsi" in swing.strategies_for_stock("600887")
    assert "trend_rsi" not in swing.strategies_for_stock("002539")


def test_main_rejects_unconfirmed_adjustment(monkeypatch, tmp_path):
    path = tmp_path / "bars.csv"
    path.write_text("code,date,open,high,low,close,volume\n"
                    "600887,2026-11-02,30,30,30,30,100\n", encoding="utf-8")
    monkeypatch.setattr(swing, "DATA_FILE", str(path))
    monkeypatch.delenv("SWING_DATA_ADJUSTMENT", raising=False)
    with pytest.raises(ValueError, match="前复权"):
        swing.main()


def test_missing_swing_config_fails_closed(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("section_backtest:\n  swing:\n    commission: 0.00025\n", encoding="utf-8")
    with pytest.raises(ValueError, match="配置"):
        swing._load_swing_config(str(path))


def test_main_reports_latest_documented_signal(monkeypatch, tmp_path, capsys):
    path = tmp_path / "front.csv"
    dates = pd.bdate_range("2026-07-01", periods=70)
    pd.DataFrame({
        "code": ["600887"] * 70, "date": dates.strftime("%Y-%m-%d"),
        "open": [30] * 70, "high": [30] * 70,
        "low": [30] * 70, "close": [30] * 70,
        "volume": [100] * 70,
    }).to_csv(path, index=False)
    monkeypatch.setattr(swing, "DATA_FILE", str(path))
    monkeypatch.setenv("SWING_DATA_ADJUSTMENT", "front")
    swing.main()
    assert "最新收盘信号" in capsys.readouterr().out


def test_main_rejects_insufficient_trend_history(monkeypatch, tmp_path):
    path = tmp_path / "front.csv"
    path.write_text("code,date,open,high,low,close,volume\n"
                    "600887,2026-11-05,30,30,30,30,100\n", encoding="utf-8")
    monkeypatch.setattr(swing, "DATA_FILE", str(path))
    monkeypatch.setenv("SWING_DATA_ADJUSTMENT", "front")
    with pytest.raises(ValueError, match="数据不足"):
        swing.main()
