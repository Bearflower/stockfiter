"""
Task 6 Step 3 / 第 9.4 N-3：推送持久化幂等测试

覆盖：
  - 数据库 has_pushed / save_push_history 基本行为（database fixture，无 DB 则 skip）
  - 发送失败不写 push_history（monkeypatch subprocess.run 抛异常）
  - 发送成功才写 push_history
  - DB has_pushed 命中则跳过发送（且不写 history）
  - 进程内守卫命中则直接跳过（不查 DB、不发发送）

被测对象：scripts.scheduler_obpc.push_daily_summary_once
"""

import subprocess
from unittest import mock

import pytest

from scripts.scheduler_obpc import push_daily_summary_once


# ---------------------------------------------------------------------------
# 数据库层幂等（database fixture 可选，无 DB 则 skip）
# ---------------------------------------------------------------------------


def test_db_has_pushed_and_save_roundtrip(database):
    """save 后 has_pushed 返回 True；相同 (code, date, type) 不重复插入"""
    code = "__daily_summary__"
    push_date = "2026-10-16"
    push_type = "obpc_summary"

    assert database.has_pushed(code, push_date, push_type) is False

    database.save_push_history(code, push_date, push_type)
    assert database.has_pushed(code, push_date, push_type) is True

    # 第二次 save 不应报错（UNIQUE ON CONFLICT DO NOTHING）
    database.save_push_history(code, push_date, push_type)
    assert database.has_pushed(code, push_date, push_type) is True


# ---------------------------------------------------------------------------
# 主流程幂等语义（monkeypatch 注入 FakeDB / Fake subprocess）
# ---------------------------------------------------------------------------


def _fake_db_factory(history_records, has_pushed=False):
    """返回可注入 scheduler_obpc._get_database_manager 的工厂函数。

    history_records 用于捕获 save_push_history 调用（发送成功时才应有记录）。
    """

    class FakeDB:
        def close(self):
            pass

        def has_pushed(self, code, push_date, push_type):
            return has_pushed

        def save_push_history(self, code, push_date, push_type):
            history_records.append((code, push_date, push_type))

    return lambda: FakeDB()


def test_send_failure_does_not_write_history(monkeypatch):
    """第 9.4 N-3：发送失败不写 push_history（下次可重试）"""
    history_records = []
    monkeypatch.setattr(
        "scripts.scheduler_obpc._get_database_manager",
        _fake_db_factory(history_records, has_pushed=False),
    )

    def fake_subprocess_run(*args, **kwargs):
        raise subprocess.CalledProcessError(returncode=1, cmd="feishu_push.py")

    monkeypatch.setattr("scripts.scheduler_obpc.subprocess.run", fake_subprocess_run)

    outcome = push_daily_summary_once("2026-10-16", inproc_pushed=False)

    assert outcome == {"skipped": False, "skip_reason": None, "pushed": False}
    assert history_records == [], "发送失败时不应写入 push_history"


def test_send_success_writes_history(monkeypatch):
    """发送成功 → 调用 save_push_history，参数为 (code, date, type)"""
    history_records = []
    monkeypatch.setattr(
        "scripts.scheduler_obpc._get_database_manager",
        _fake_db_factory(history_records, has_pushed=False),
    )
    monkeypatch.setattr(
        "scripts.scheduler_obpc.subprocess.run",
        lambda *args, **kwargs: mock.Mock(returncode=0),
    )

    outcome = push_daily_summary_once("2026-10-16", inproc_pushed=False)

    assert outcome["skipped"] is False
    assert outcome["pushed"] is True
    assert history_records == [("__daily_summary__", "2026-10-16", "obpc_summary")]


def test_second_send_is_skipped_by_db_gate(monkeypatch):
    """DB has_pushed 命中 → 跳过发送，且不写 history"""
    history_records = []
    monkeypatch.setattr(
        "scripts.scheduler_obpc._get_database_manager",
        _fake_db_factory(history_records, has_pushed=True),
    )

    send_calls = {"count": 0}

    def counting_run(*args, **kwargs):
        send_calls["count"] += 1

    monkeypatch.setattr("scripts.scheduler_obpc.subprocess.run", counting_run)

    outcome = push_daily_summary_once("2026-10-16", inproc_pushed=False)

    assert outcome == {"skipped": True, "skip_reason": "db_guard", "pushed": False}
    assert send_calls["count"] == 0, "已推送过时不应执行发送"
    assert history_records == []


def test_inproc_guard_skips_without_db_query(monkeypatch):
    """进程内守卫命中 → 直接跳过，不查 DB、不发送"""
    db_calls = {"count": 0}

    def counting_factory():
        db_calls["count"] += 1
        return _fake_db_factory([], has_pushed=False)()

    monkeypatch.setattr("scripts.scheduler_obpc._get_database_manager", counting_factory)
    monkeypatch.setattr(
        "scripts.scheduler_obpc.subprocess.run",
        lambda *args, **kwargs: pytest.fail("进程内守卫命中时不应发送"),
    )

    outcome = push_daily_summary_once("2026-10-16", inproc_pushed=True)

    assert outcome == {"skipped": True, "skip_reason": "inproc_guard", "pushed": False}
    assert db_calls["count"] == 0, "进程内守卫命中时不应查询 DB"
