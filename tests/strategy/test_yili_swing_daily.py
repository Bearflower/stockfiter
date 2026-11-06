"""伊利每日扫描、通知与幂等验证。"""

import pandas as pd
import pytest

from strategy.yili_swing.daily import run_daily


class FakeNotifier:
    def __init__(self, result=True):
        self.result = result
        self.messages = []

    def send_text_message(self, message):
        self.messages.append(message)
        return self.result


def _front_frame(last_date="2026-11-05"):
    dates = pd.bdate_range(end=last_date, periods=90)
    prices = [20 + i * .05 for i in range(len(dates))]
    return pd.DataFrame({
        "code": ["600887"] * len(dates),
        "date": dates.strftime("%Y-%m-%d"),
        "open": prices, "high": [x + .1 for x in prices],
        "low": [x - .1 for x in prices], "close": prices,
        "volume": [1000] * len(dates), "amount": [10000] * len(dates),
    })


def test_daily_pushes_summary_once_without_replaying_trades(tmp_path, monkeypatch):
    import scripts.backtest_swing as backtest

    monkeypatch.setattr(backtest, "run_backtest", lambda *a, **k: pytest.fail("服务器不可回测"))
    notifier = FakeNotifier()
    state = tmp_path / "yili.json"
    calls = []

    def fetch(start, end):
        calls.append((start, end))
        return _front_frame(end)

    first = run_daily("2026-11-05", fetch, notifier, state)
    second = run_daily("2026-11-05", fetch, notifier, state)
    assert first["status"] == "sent"
    assert second["status"] == "already_sent"
    assert len(calls) == 1
    assert len(notifier.messages) == 1
    assert "伊利股份" in notifier.messages[0]
    assert "2026-11-05" in notifier.messages[0]
    assert "原始收盘信号" in notifier.messages[0]
    assert state.exists()


def test_daily_rejects_stale_bars_without_notification(tmp_path):
    notifier = FakeNotifier()
    state = tmp_path / "yili.json"
    with pytest.raises(ValueError, match="日期"):
        run_daily("2026-11-06", lambda *_: _front_frame(), notifier, state)
    assert notifier.messages == []
    assert not state.exists()


def test_daily_push_failure_keeps_date_retryable(tmp_path):
    notifier = FakeNotifier(False)
    state = tmp_path / "yili.json"
    with pytest.raises(RuntimeError, match="通知失败"):
        run_daily("2026-11-05", lambda *_: _front_frame(), notifier, state)
    assert not state.exists()


def test_daily_dry_run_does_not_send_or_write(tmp_path):
    notifier = FakeNotifier()
    state = tmp_path / "yili.json"
    result = run_daily("2026-11-05", lambda *_: _front_frame(), notifier, state,
                       dry_run=True)
    assert result["status"] == "dry_run"
    assert notifier.messages == []
    assert not state.exists()


def test_daily_rejects_undefined_latest_rsi(tmp_path):
    notifier = FakeNotifier()
    bars = _front_frame()
    for column in ("open", "high", "low", "close"):
        bars[column] = 20.0
    with pytest.raises(ValueError, match="指标"):
        run_daily("2026-11-05", lambda *_: bars, notifier, tmp_path / "yili.json")
    assert notifier.messages == []
