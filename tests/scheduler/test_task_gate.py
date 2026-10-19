"""
Task 2 Step 1：task_status_allows_run 失败测试

验证：failed 不放行；completed 但日期不同不放行。
"""

from datetime import datetime, timezone

from scripts.scheduler_obpc import task_status_allows_run


def test_failed_task_never_allows_scan():
    """status='failed' 即使时间戳刚更新也不能放行"""
    row = {
        "status": "failed",
        "target_date": "2026-10-16",
        "last_completed_at": datetime.now(timezone.utc),
    }
    assert task_status_allows_run(row, "2026-10-16", 3600) is False


def test_completed_wrong_date_never_allows_scan():
    """status='completed' 但目标日期不同也不能放行"""
    row = {
        "status": "completed",
        "target_date": "2026-10-15",
        "last_completed_at": datetime.now(timezone.utc),
    }
    assert task_status_allows_run(row, "2026-10-16", 3600) is False


def test_completed_correct_date_allows_scan():
    """status='completed'、日期正确、新鲜度内 → 放行"""
    row = {
        "status": "completed",
        "target_date": "2026-10-16",
        "last_completed_at": datetime.now(timezone.utc),
    }
    assert task_status_allows_run(row, "2026-10-16", 3600) is True


def test_expired_task_not_allowed():
    """completed 但过期超过 freshness_seconds 不放行"""
    row = {
        "status": "completed",
        "target_date": "2026-10-16",
        "last_completed_at": datetime.now(timezone.utc) - __import__("datetime").timedelta(seconds=7200),
    }
    assert task_status_allows_run(row, "2026-10-16", 3600) is False


def test_none_row_not_allowed():
    """row=None 不放行"""
    assert task_status_allows_run(None, "2026-10-16", 3600) is False


def test_no_completed_at_not_allowed():
    """completed 但没有 last_completed_at 不放行"""
    row = {
        "status": "completed",
        "target_date": "2026-10-16",
        "last_completed_at": None,
    }
    assert task_status_allows_run(row, "2026-10-16", 3600) is False


def test_completed_at_naive_is_parsed_as_utc():
    """无时区 last_completed_at 按 UTC 解析（N-1 约定）"""
    # 2 小时前的无时区时间戳
    two_hours_ago = datetime.now(timezone.utc) - __import__("datetime").timedelta(seconds=7200)
    naive = two_hours_ago.replace(tzinfo=None)
    row = {
        "status": "completed",
        "target_date": "2026-10-16",
        "last_completed_at": naive,
    }
    assert task_status_allows_run(row, "2026-10-16", 3600) is False
