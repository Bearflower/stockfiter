"""伊利独立调度器的触发与重试时序。"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from scripts.scheduler_yili import should_attempt
from strategy.yili_swing.daily import resolve_target_date


TZ = ZoneInfo("Asia/Shanghai")


def test_scheduler_waits_for_configured_time_and_retries_failures():
    before = datetime(2026, 11, 6, 8, 19, tzinfo=TZ)
    due = datetime(2026, 11, 6, 8, 20, tzinfo=TZ)
    assert not should_attempt(before, "08:20", "09:20", None, "", 900)
    assert should_attempt(due, "08:20", "09:20", None, "", 900)
    assert not should_attempt(due + timedelta(minutes=1), "08:20", "09:20", due, "", 900)
    assert should_attempt(due + timedelta(minutes=15), "08:20", "09:20", due, "", 900)
    assert not should_attempt(due + timedelta(minutes=15), "08:20", "09:20", due,
                              "2026-11-06", 900)
    assert not should_attempt(datetime(2026, 11, 6, 9, 21, tzinfo=TZ),
                              "08:20", "09:20", due, "", 900)


def test_morning_target_is_prior_verified_trading_day():
    now = datetime(2026, 11, 6, 8, 20, tzinfo=TZ)
    target = resolve_target_date(
        now, lambda _: (True, False),
        lambda _now, inclusive: ("2026-11-05", False),
    )
    assert target == "2026-11-05"
    with pytest.raises(ValueError, match="交易日历"):
        resolve_target_date(
            now, lambda _: (True, True),
            lambda _now, inclusive: ("2026-11-05", False),
        )
    assert resolve_target_date(
        now, lambda _: (False, False),
        lambda _now, inclusive: ("2026-11-05", False),
    ) is None
