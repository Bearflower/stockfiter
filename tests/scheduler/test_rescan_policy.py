"""
Task 3 Step 1：should_rescan_target 重扫时机纯函数测试

Task 9.3.3：同数据版本已成功重扫 → False；backfill 完成时间更新 → True；
failed/blocked_data → 可重试。
"""

from datetime import datetime, timezone, timedelta

import pytest

from scripts.scheduler_obpc import should_rescan_target


@pytest.fixture(autouse=True)
def _patch_is_trading_day(monkeypatch):
    """本文件所有用例 patch 掉 scheduler_obpc 模块级绑定的 is_trading_day。

    scheduler_obpc.py 使用模块级 `from utils.trade_calendar import is_trading_day`，
    因此 patch utils.trade_calendar 对其无效，必须 patch 该模块内的绑定名，
    否则 should_rescan_target 会真实调用日历（依赖 Baostock，非 hermetic 且慢）。
    以工作日启发式模拟交易日历：周一~周五视为交易日。
    """

    def _fake_is_trading_day(date_str):
        dt = datetime.strptime(str(date_str)[:10], "%Y-%m-%d")
        return dt.weekday() < 5

    monkeypatch.setattr(
        "scripts.scheduler_obpc.is_trading_day", _fake_is_trading_day
    )


def test_saturday_may_rescan_friday_after_completed_backfill():
    """周六 07:30 可以重扫周五（backfill 已完成、目标日期是交易日、未重扫过）"""
    assert (
        should_rescan_target(
            now_date="2026-10-17",
            target_date="2026-10-16",
            backfill_status="completed",
            already_scanned=False,
        )
        is True
    )


def test_same_target_is_idempotent():
    """同一目标日期同数据版本已重扫 → 不再重扫"""
    assert (
        should_rescan_target(
            now_date="2026-10-17",
            target_date="2026-10-16",
            backfill_status="completed",
            already_scanned=True,
        )
        is False
    )


def test_no_target_date_no_rescan():
    """target_date 为空 → 不重扫"""
    assert (
        should_rescan_target(
            now_date="2026-10-17",
            target_date=None,
            backfill_status="completed",
            already_scanned=False,
        )
        is False
    )


def test_backfill_not_completed_no_rescan():
    """backfill status != completed → 不重扫"""
    assert (
        should_rescan_target(
            now_date="2026-10-17",
            target_date="2026-10-16",
            backfill_status="running",
            already_scanned=False,
        )
        is False
    )
    assert (
        should_rescan_target(
            now_date="2026-10-17",
            target_date="2026-10-16",
            backfill_status="failed",
            already_scanned=False,
        )
        is False
    )


def test_non_trading_target_no_rescan():
    """目标日期非交易日（比如周日 2026-10-18）→ 不重扫"""
    assert (
        should_rescan_target(
            now_date="2026-10-19",
            target_date="2026-10-18",  # 周日
            backfill_status="completed",
            already_scanned=False,
        )
        is False
    )
