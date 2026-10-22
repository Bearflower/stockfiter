"""进程级隔离 + 数据库断点续采监督者测试（tasks/plan.md Task 3/4）。

覆盖：
  - 第 610 只股票超时：旧进程退出，新进程从数据库缺口继续，覆盖率达标；
  - SIGTERM 被忽略时升级 SIGKILL 并确认 PID 消失；
  - 会话重建上限、整体截止时间、连续无进展、数据库故障；
  - 配置缺失 fail-closed 与截止时间推导。

全部使用 fake runner / fake store，不连接 Baostock 和真实数据库。
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timedelta
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest

from scripts.data.kline_collection import (
    CollectionConfigError,
    CollectionOutcome,
    CollectionRound,
    MODE_FAILED,
    MODE_NORMAL,
    MODE_RECOVERED,
    STOP_CONFIG_ERROR,
    STOP_COVERAGE_REACHED,
    STOP_COVERAGE_SHORT,
    STOP_DEADLINE,
    STOP_DB_ERROR,
    STOP_NO_PROGRESS,
    STOP_QUARANTINED_EXHAUSTED,
    STOP_RESTART_LIMIT,
    WorkerOutcome,
    CollectionConfig,
    compute_collection_deadline,
    load_collection_config,
    run_supervised_collection,
)


# ---------------------------------------------------------------------------
# 测试替身
# ---------------------------------------------------------------------------


class FakeStore:
    """以内存集合模拟数据库目标日覆盖，runner 通过 cover() 落库。"""

    def __init__(self, pool_codes, covered=None, fail_on_call_at=None):
        self._pool = [{"code": code, "symbol": f"{code}.SZ"} for code in pool_codes]
        self.covered: set[str] = set(covered or [])
        self.fail_on_call_at = fail_on_call_at
        self.query_calls = 0

    def pool(self):
        return list(self._pool)

    def target_date_codes(self, target_date):
        self.query_calls += 1
        if self.fail_on_call_at is not None and self.query_calls >= self.fail_on_call_at:
            raise RuntimeError("数据库连接中断")
        return set(self.covered)


class FakeRunner:
    """按脚本化行为模拟一次分片工作进程。

    behavior(chunk, call_index) 返回 (newly_covered_codes, timed_out, exit_code,
    last_started_code)。新覆盖的代码由 runner 写入 store。
    """

    def __init__(self, store, behavior):
        self._store = store
        self._behavior = behavior
        self.calls: list[list[dict]] = []
        self.timeouts: list[float] = []

    def run(self, chunk, chunk_index, round_index, timeout_seconds, work_dir,
            target_date, pool_size):
        self.calls.append(list(chunk))
        self.timeouts.append(timeout_seconds)
        call_index = len(self.calls) - 1
        covered, timed_out, exit_code, last_started = self._behavior(chunk, call_index)
        self._store.covered.update(covered)
        return WorkerOutcome(
            round_index=round_index,
            chunk_index=chunk_index,
            chunk_codes=[item["code"] for item in chunk],
            exit_code=exit_code,
            timed_out=timed_out,
            duration_seconds=1.0,
            result={"failed_codes": []} if exit_code == 0 else None,
            progress={"last_started_code": last_started} if last_started else None,
            killed=timed_out,
            kill_signal="SIGKILL" if timed_out else "",
            pid=10000 + call_index,
            pid_gone=True,
        )


def make_config(**overrides) -> CollectionConfig:
    values = {
        "worker_chunk_size": 300,
        "worker_timeout_seconds": 300,
        "worker_terminate_grace_seconds": 10,
        "max_session_restarts": 6,
        "recovery_deadline_buffer_seconds": 300,
        "immediate_recovery_enabled": True,
        "max_consecutive_no_progress_rounds": 2,
        "max_quarantined_retries": 1,
        "db_query_timeout_seconds": 30,
        "db_connect_timeout_seconds": 10,
        "initial_scan_time": "23:15",
        "kline_wait_timeout_seconds": 1800,
        "min_coverage_rate": 0.98,
        "request_interval_seconds": 0.05,
    }
    values.update(overrides)
    return CollectionConfig(**values)


def fixed_now(value: datetime):
    return lambda: value


# ---------------------------------------------------------------------------
# 核心验收场景：第 610 只股票超时后断点续采
# ---------------------------------------------------------------------------


def test_timeout_on_610th_stock_recovers_from_database_gap():
    """第 610 只股票超时：旧进程退出，新进程从剩余缺口继续，覆盖率 >= 98%。"""
    codes = [f"{600000 + i}" for i in range(2911)]
    store = FakeStore(codes)
    hung_code = codes[299]  # 第一个分片内第 300 只（对应生产第 610 只超时场景）

    def behavior(chunk, call_index):
        if call_index == 0:
            # 前 299 只已落库；第 300 只挂死 → 监督者超时杀进程
            return [item["code"] for item in chunk[:299]], True, None, hung_code
        return [item["code"] for item in chunk], False, 0, None

    runner = FakeRunner(store, behavior)
    now = datetime(2026, 10, 22, 23, 0, 0)
    outcome = run_supervised_collection(
        target_date="2026-10-22",
        config=make_config(),
        store=store,
        deadline=now + timedelta(hours=2),
        runner=runner,
        now_fn=fixed_now(now),
    )

    # 1. 最终覆盖率达标且判定为「超时后恢复」
    assert outcome.completion_mode == MODE_RECOVERED
    assert outcome.stop_reason == STOP_COVERAGE_REACHED
    assert outcome.coverage_rate >= 0.98
    assert outcome.covered == 2910

    # 2. 旧进程被终止并记录，卡死股票可定位
    assert outcome.session_restarts == 1
    assert len(outcome.timed_out_chunks) == 1
    assert outcome.timed_out_chunks[0]["last_started_code"] == hung_code
    assert outcome.timed_out_chunks[0]["killed"] is True
    assert outcome.timed_out_chunks[0]["pid_gone"] is True

    # 3. 新分片跳过隔离的挂死股票，从下一个普通缺口继续，已落库股票不重复采集
    assert [c["code"] for c in runner.calls[0]][0] == codes[0]
    assert [c["code"] for c in runner.calls[1]][0] == codes[300]
    total_processed = sum(len(call) for call in runner.calls)
    assert total_processed == 2911, "除挂死股票只在首轮尝试一次外，其余股票不得重复采集"

    # 4. 挂死股票进入失败明细（达到红线后不再重试）
    assert outcome.failed == 1
    assert outcome.quarantined_codes == [hung_code]

    # 5. 工作进程数与分片轮次一致
    assert outcome.worker_processes == len(runner.calls)
    assert len(outcome.rounds) == len(runner.calls)


def test_normal_completion_mode_without_timeout():
    """无超时时 completion_mode=normal。"""
    codes = [f"{600000 + i}" for i in range(10)]
    store = FakeStore(codes)
    runner = FakeRunner(
        store, lambda chunk, idx: ([item["code"] for item in chunk], False, 0, None)
    )
    now = datetime(2026, 10, 22, 23, 0, 0)
    outcome = run_supervised_collection(
        target_date="2026-10-22",
        config=make_config(worker_chunk_size=5),
        store=store,
        deadline=now + timedelta(hours=1),
        runner=runner,
        now_fn=fixed_now(now),
    )

    assert outcome.completion_mode == MODE_NORMAL
    assert outcome.stop_reason == STOP_COVERAGE_REACHED
    assert outcome.covered == 10
    assert outcome.session_restarts == 0
    assert outcome.timed_out_chunks == []


# ---------------------------------------------------------------------------
# 失败路径：不得假装完成
# ---------------------------------------------------------------------------


def test_restart_limit_returns_failed():
    """会话重建超过上限 → stop=restart_limit，completion_mode=failed。"""
    codes = [f"{600000 + i}" for i in range(1000)]
    store = FakeStore(codes)

    def behavior(chunk, call_index):
        return [], True, None, chunk[0]["code"]

    runner = FakeRunner(store, behavior)
    now = datetime(2026, 10, 22, 23, 0, 0)
    outcome = run_supervised_collection(
        target_date="2026-10-22",
        config=make_config(
            max_session_restarts=2,
            max_consecutive_no_progress_rounds=99,
        ),
        store=store,
        deadline=now + timedelta(hours=2),
        runner=runner,
        now_fn=fixed_now(now),
    )

    assert outcome.stop_reason == STOP_RESTART_LIMIT
    assert outcome.completion_mode == MODE_FAILED
    assert outcome.session_restarts == 3, "第 3 次异常退出才超过上限 2"
    assert outcome.covered == 0


def test_deadline_stops_collection():
    """整体截止时间到达后停止，不得继续无限重试。"""
    codes = [f"{600000 + i}" for i in range(1000)]
    store = FakeStore(codes)
    clock = {"now": datetime(2026, 10, 22, 23, 0, 0)}

    def behavior(chunk, call_index):
        clock["now"] = clock["now"] + timedelta(seconds=400)
        return [], True, None, chunk[0]["code"]

    runner = FakeRunner(store, behavior)
    outcome = run_supervised_collection(
        target_date="2026-10-22",
        config=make_config(
            max_session_restarts=99,
            max_consecutive_no_progress_rounds=99,
        ),
        store=store,
        deadline=clock["now"] + timedelta(seconds=500),
        runner=runner,
        now_fn=lambda: clock["now"],
    )

    assert outcome.stop_reason == STOP_DEADLINE
    assert outcome.completion_mode == MODE_FAILED
    assert len(runner.calls) == 2


def test_no_progress_stops_collection():
    """连续无新增落库达到阈值 → stop=no_progress。"""
    codes = [f"{600000 + i}" for i in range(1000)]
    store = FakeStore(codes)
    runner = FakeRunner(store, lambda chunk, idx: ([], False, 0, None))
    now = datetime(2026, 10, 22, 23, 0, 0)
    outcome = run_supervised_collection(
        target_date="2026-10-22",
        config=make_config(max_consecutive_no_progress_rounds=2),
        store=store,
        deadline=now + timedelta(hours=2),
        runner=runner,
        now_fn=fixed_now(now),
    )

    assert outcome.stop_reason == STOP_NO_PROGRESS
    assert outcome.completion_mode == MODE_FAILED
    assert len(runner.calls) == 2


def test_database_error_fails_closed():
    """缺口查询失败 → stop=db_error，不得把未知状态当成功。"""
    codes = [f"{600000 + i}" for i in range(10)]
    store = FakeStore(codes, fail_on_call_at=1)
    runner = FakeRunner(store, lambda chunk, idx: ([], False, 0, None))
    now = datetime(2026, 10, 22, 23, 0, 0)
    outcome = run_supervised_collection(
        target_date="2026-10-22",
        config=make_config(),
        store=store,
        deadline=now + timedelta(hours=1),
        runner=runner,
        now_fn=fixed_now(now),
    )

    assert outcome.stop_reason == STOP_DB_ERROR
    assert outcome.completion_mode == MODE_FAILED
    assert runner.calls == []


def test_coverage_below_threshold_never_claims_success():
    """截止时间前只覆盖不足红线时，completion_mode 必须为 failed。"""
    codes = [f"{600000 + i}" for i in range(1000)]
    store = FakeStore(codes)

    def behavior(chunk, call_index):
        if call_index == 0:
            return [item["code"] for item in chunk], False, 0, None
        return [], False, 0, None

    runner = FakeRunner(store, behavior)
    now = datetime(2026, 10, 22, 23, 0, 0)
    outcome = run_supervised_collection(
        target_date="2026-10-22",
        config=make_config(max_consecutive_no_progress_rounds=1),
        store=store,
        deadline=now + timedelta(hours=1),
        runner=runner,
        now_fn=fixed_now(now),
    )

    assert outcome.completion_mode == MODE_FAILED
    assert outcome.covered == 300


def test_persistent_hang_stock_quarantined_not_blocking_others():
    """永久挂死股票必须被隔离：其余缺口继续采集，挂死股票进入失败明细。

    回归 P1-1：旧实现每轮从第一个缺口开始，同一只挂死股票反复占据队首，
    连续无进展后以低覆盖率停止。
    """
    codes = [f"{600000 + i}" for i in range(100)]
    store = FakeStore(codes)
    hung_code = codes[5]

    def behavior(chunk, call_index):
        chunk_codes = [item["code"] for item in chunk]
        if hung_code in chunk_codes:
            # 该股票永久挂死：只落库它之前的股票，然后超时被杀
            return chunk_codes[: chunk_codes.index(hung_code)], True, None, hung_code
        return chunk_codes, False, 0, None

    runner = FakeRunner(store, behavior)
    now = datetime(2026, 10, 22, 23, 0, 0)
    outcome = run_supervised_collection(
        target_date="2026-10-22",
        config=make_config(
            worker_chunk_size=10,
            min_coverage_rate=1.0,
            max_quarantined_retries=1,
        ),
        store=store,
        deadline=now + timedelta(hours=2),
        runner=runner,
        now_fn=fixed_now(now),
    )

    # 其余 99 只全部采完，挂死股票保留为失败明细
    assert outcome.covered == 99
    assert outcome.failed == 1
    assert outcome.quarantined_codes == [hung_code]
    assert outcome.stop_reason == STOP_QUARANTINED_EXHAUSTED
    assert outcome.completion_mode == MODE_FAILED

    # 挂死股票只被尝试「首轮 1 次 + 额度内重试 1 次」，不会无限循环
    hang_attempts = sum(
        1 for call in runner.calls if hung_code in [c["code"] for c in call]
    )
    assert hang_attempts == 2

    # 旧进程每次都被确认消失，不得残留
    assert all(item["pid_gone"] for item in outcome.timed_out_chunks)
    assert len(outcome.timed_out_chunks) == 2


def test_multiple_persistent_hangs_do_not_trigger_no_progress_before_normal_codes():
    """连续识别多个挂死股票仍属于有效推进，不能阻断后续普通缺口。"""
    codes = [f"{600000 + i}" for i in range(100)]
    store = FakeStore(codes)
    hung_codes = set(codes[:2])

    def behavior(chunk, call_index):
        chunk_codes = [item["code"] for item in chunk]
        for index, code in enumerate(chunk_codes):
            if code in hung_codes:
                return chunk_codes[:index], True, None, code
        return chunk_codes, False, 0, None

    runner = FakeRunner(store, behavior)
    now = datetime(2026, 10, 22, 23, 0, 0)
    outcome = run_supervised_collection(
        target_date="2026-10-22",
        config=make_config(
            worker_chunk_size=10,
            min_coverage_rate=1.0,
            max_quarantined_retries=1,
            max_consecutive_no_progress_rounds=2,
        ),
        store=store,
        deadline=now + timedelta(hours=2),
        runner=runner,
        now_fn=fixed_now(now),
    )

    assert outcome.covered == 98
    assert outcome.failed == 2
    assert outcome.skipped == 0
    assert outcome.quarantined_codes == sorted(hung_codes)
    assert outcome.stop_reason == STOP_QUARANTINED_EXHAUSTED
    assert all(code in store.covered for code in codes[2:])

    attempts = {
        code: sum(
            item["last_started_code"] == code
            for item in outcome.timed_out_chunks
        )
        for code in hung_codes
    }
    assert attempts == {code: 2 for code in hung_codes}


# ---------------------------------------------------------------------------
# 覆盖率口径：details.coverage_rate 必须是目标日真实覆盖率
# ---------------------------------------------------------------------------


def test_real_coverage_rate_persisted_not_overwritten():
    """610/2911 场景：落库 details.coverage_rate 必须是约 0.20955，不能被处理比例覆盖。"""
    from scripts.data.update_kline_daily import UpdateResult, build_task_status_details

    total, covered = 2911, 610
    outcome = CollectionOutcome(
        target_date="2026-10-22",
        total=total,
        covered=covered,
        success=covered,
        failed=0,
        skipped=total - covered,
        completion_mode=MODE_FAILED,
        stop_reason=STOP_COVERAGE_SHORT,
    )
    details = outcome.to_details()

    assert details["coverage_rate"] == pytest.approx(0.20955, abs=1e-6)
    assert details["processed_rate"] == 1.0

    # 调度器落库前只允许补充字段，不得覆盖监督者的真实覆盖率
    result = UpdateResult(
        success=covered,
        failed=0,
        skipped=total - covered,
        total=total,
        target_date="2026-10-22",
        target_date_success=covered,
        completion_mode=MODE_FAILED,
        details=details,
    )
    merged = build_task_status_details(result)

    assert merged["coverage_rate"] == pytest.approx(0.20955, abs=1e-6)
    assert merged["target_date_coverage_rate"] == pytest.approx(0.20955, abs=1e-6)
    assert merged["processed_rate"] == 1.0


# ---------------------------------------------------------------------------
# 进程终止：SIGTERM → 宽限期 → SIGKILL，并确认 PID 消失
# ---------------------------------------------------------------------------


def test_terminate_tree_escalates_to_sigkill_and_pid_gone():
    """工作进程忽略 SIGTERM 时必须升级 SIGKILL，并确认旧 PID 消失。"""
    from scripts.data.kline_collection import SubprocessWorkerRunner

    cfg = make_config(worker_terminate_grace_seconds=1)
    runner = SubprocessWorkerRunner(cfg)
    proc = subprocess.Popen(
        [
            sys.executable, "-c",
            "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
            "print('ready', flush=True); time.sleep(30)",
        ],
        start_new_session=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    try:
        proc.stdout.readline()  # 等待子进程进入忽略 SIGTERM 状态
        used_signal = runner._terminate_tree(proc)

        assert used_signal == "SIGKILL"
        assert proc.poll() is not None, "SIGKILL 后必须回收"
        with pytest.raises(OSError):
            os.kill(proc.pid, 0)
    finally:
        if proc.poll() is None:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except OSError:
                pass
            proc.wait(timeout=5)
        if proc.stdout:
            proc.stdout.close()


# ---------------------------------------------------------------------------
# 配置 fail-closed 与截止时间推导
# ---------------------------------------------------------------------------


def test_collection_config_missing_keys_fails_closed():
    """缺少分片配置必须直接抛错，不得静默默认。"""
    with pytest.raises(CollectionConfigError):
        load_collection_config({"min_coverage_rate": 0.98, "request_interval_seconds": 0.05})


def test_collection_config_invalid_type_fails_closed():
    """布尔值伪装整数、负数、非法 buffer、熔断上限不小于分片都必须拒绝。"""
    base = {
        "worker_chunk_size": 300,
        "worker_timeout_seconds": 300,
        "worker_terminate_grace_seconds": 10,
        "max_session_restarts": 6,
        "recovery_deadline_buffer_seconds": 300,
        "immediate_recovery_enabled": True,
        "max_consecutive_no_progress_rounds": 2,
        "max_quarantined_retries": 1,
        "db_query_timeout_seconds": 30,
        "db_connect_timeout_seconds": 10,
        "total_failure_limit": 250,
        "days_to_update": 5,
        "target_date_max_backtrack": 7,
        "failure_rate_threshold": 0.7,
        "failure_window_size": 100,
        "failure_window_min_samples": 50,
        "min_success_rate": 0.9,
        "skipped_ratio_guard": 0.95,
        "min_coverage_rate": 0.98,
        "request_interval_seconds": 0.05,
    }
    for broken in (
        {"worker_chunk_size": True},
        {"worker_chunk_size": 0},
        {"worker_timeout_seconds": -1},
        {"immediate_recovery_enabled": "yes"},
        {"recovery_deadline_buffer_seconds": 1800},
        {"max_quarantined_retries": -1},
        {"db_query_timeout_seconds": 0},
        {"db_connect_timeout_seconds": 0},
        {"total_failure_limit": 300},
        {"total_failure_limit": 400},
        {"days_to_update": "5"},
        {"days_to_update": 0},
        {"target_date_max_backtrack": 0},
        {"failure_rate_threshold": 2},
        {"failure_window_size": 0},
        {"failure_window_min_samples": 0},
        {"failure_window_min_samples": 101},
        {"failure_window_size": 1000, "failure_window_min_samples": 500},
        {"min_success_rate": -1},
        {"skipped_ratio_guard": 2},
    ):
        cfg = dict(base)
        cfg.update(broken)
        with pytest.raises(CollectionConfigError):
            load_collection_config(cfg)

    # 合法边界：熔断上限严格小于分片大小时必须通过
    assert load_collection_config(dict(base)).worker_chunk_size == 300


def test_collection_config_requires_new_keys():
    """新增的隔离/数据库超时配置缺失时必须 fail-closed。"""
    base = {
        "worker_chunk_size": 300,
        "worker_timeout_seconds": 300,
        "worker_terminate_grace_seconds": 10,
        "max_session_restarts": 6,
        "recovery_deadline_buffer_seconds": 300,
        "immediate_recovery_enabled": True,
        "max_consecutive_no_progress_rounds": 2,
        "total_failure_limit": 250,
        "days_to_update": 5,
        "target_date_max_backtrack": 7,
        "failure_rate_threshold": 0.7,
        "failure_window_size": 100,
        "failure_window_min_samples": 50,
        "min_success_rate": 0.9,
        "skipped_ratio_guard": 0.95,
        "min_coverage_rate": 0.98,
        "request_interval_seconds": 0.05,
    }
    for missing in ("max_quarantined_retries", "db_query_timeout_seconds", "db_connect_timeout_seconds"):
        cfg = dict(base)
        cfg.pop(missing, None)
        with pytest.raises(CollectionConfigError):
            load_collection_config(cfg)


def test_run_update_validates_collection_config_before_loading_stock_pool(monkeypatch):
    """非法分片配置必须在任何股票池数据库访问前 fail-closed。"""
    from scripts.data import kline_collection
    from scripts.data import update_kline_daily as update_module

    cfg = update_module.load_kline_update_config()
    cfg.pop("worker_chunk_size")
    pool_loaded = False

    def fake_load_stock_pool(*args, **kwargs):
        nonlocal pool_loaded
        pool_loaded = True
        return None

    monkeypatch.setattr(kline_collection, "load_stock_pool", fake_load_stock_pool)

    result = update_module.run_update(cfg=cfg)

    assert pool_loaded is False
    assert result.completion_mode == MODE_FAILED
    assert result.details["stop_reason"] == STOP_CONFIG_ERROR


def test_load_stock_pool_passes_database_timeouts(tmp_path, monkeypatch):
    """固定股票池缺失时，数据库回退必须继承监督者连接与语句超时。"""
    import pandas as pd
    from scripts.data import kline_collection

    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "global:\n  stock_list_file: /not/exist/main_board_stocks.csv\n",
        encoding="utf-8",
    )
    captured = {}

    class FakeDatabaseManager:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def get_stock_list(self):
            return pd.DataFrame([{"code": "600000", "symbol": "600000.SH"}])

        def close(self):
            return None

    monkeypatch.setattr("data.database.DatabaseManager", FakeDatabaseManager)

    pool = kline_collection.load_stock_pool(
        str(config_path),
        connect_timeout=10,
        statement_timeout=30,
    )

    assert len(pool) == 1
    assert captured == {"connect_timeout": 10, "statement_timeout": 30}


def test_database_manager_applies_statement_timeout_at_connection_start(monkeypatch):
    """statement_timeout 必须随连接建立生效，覆盖构造期建表与迁移。"""
    from data import database as database_module

    captured = {}

    class FakeConnection:
        pass

    def fake_connect(**kwargs):
        captured.update(kwargs)
        return FakeConnection()

    monkeypatch.setenv("DB_PASSWORD", "test-only-password")
    monkeypatch.setattr(database_module.psycopg2, "connect", fake_connect)
    for method_name in (
        "_create_tables",
        "_migrate_add_frequency",
        "_migrate_task_status_columns",
        "_migrate_create_scan_runs",
        "_migrate_scan_results_unique_index",
    ):
        monkeypatch.setattr(database_module.DatabaseManager, method_name, lambda self: None)

    database_module.DatabaseManager(connect_timeout=10, statement_timeout=30)

    assert captured["connect_timeout"] == 10
    assert "-c search_path=schema_stockfilter" in captured["options"]
    assert "-c statement_timeout=30000" in captured["options"]


def test_database_manager_closes_connection_when_constructor_migration_fails(monkeypatch):
    """构造期建表/迁移超时或异常时必须关闭已建立的连接。"""
    from data import database as database_module

    fake_conn = MagicMock()
    monkeypatch.setenv("DB_PASSWORD", "test-only-password")
    monkeypatch.setattr(database_module.psycopg2, "connect", lambda **kwargs: fake_conn)
    monkeypatch.setattr(
        database_module.DatabaseManager,
        "_create_tables",
        lambda self: (_ for _ in ()).throw(RuntimeError("migration timeout")),
    )

    with pytest.raises(RuntimeError, match="migration timeout"):
        database_module.DatabaseManager(connect_timeout=10, statement_timeout=30)

    fake_conn.close.assert_called_once()


def test_compute_collection_deadline_same_source():
    """截止时间 = 初扫时刻 + KLINE_WAIT_TIMEOUT - 余量（当前 23:40）。"""
    now = datetime(2026, 10, 22, 22, 0, 0)
    deadline = compute_collection_deadline(now, "23:15", 1800, 300)
    assert deadline == datetime(2026, 10, 22, 23, 40, 0)

    # 自动调度入口（strict=True）：截止已过就返回过去的时刻，监督者立即停采
    late_now = datetime(2026, 10, 22, 23, 50, 0)
    strict_deadline = compute_collection_deadline(late_now, "23:15", 1800, 300, strict=True)
    assert strict_deadline == datetime(2026, 10, 22, 23, 40, 0)

    # 人工入口（strict=False）：从当前时刻滚动一个同样的可用窗口
    late_deadline = compute_collection_deadline(late_now, "23:15", 1800, 300, strict=False)
    assert late_deadline == late_now + timedelta(seconds=1500)


def test_worker_timeout_reserves_termination_and_post_query_budget():
    """分片时限必须扣除进程回收与轮后 DB 查询预算。"""
    codes = ["600000"]
    store = FakeStore(codes)
    runner = FakeRunner(store, lambda chunk, idx: ([], True, None, "600000"))
    now = datetime(2026, 10, 22, 23, 0, 0)

    run_supervised_collection(
        target_date="2026-10-22",
        config=make_config(
            worker_chunk_size=1,
            worker_timeout_seconds=300,
            worker_terminate_grace_seconds=10,
            max_quarantined_retries=0,
        ),
        store=store,
        deadline=now + timedelta(seconds=100),
        runner=runner,
        now_fn=fixed_now(now),
    )

    assert runner.timeouts == [50.0]


def test_real_worker_termination_stays_within_absolute_deadline(tmp_path):
    """真实忽略 SIGTERM 的子进程也必须在绝对截止时间附近完成回收。"""
    from scripts.data.kline_collection import SubprocessWorkerRunner

    worker = tmp_path / "ignore_term_worker.py"
    worker.write_text(
        "import signal, time\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "time.sleep(30)\n",
        encoding="utf-8",
    )
    config = make_config(
        worker_chunk_size=1,
        worker_timeout_seconds=30,
        worker_terminate_grace_seconds=1,
        max_quarantined_retries=0,
        min_coverage_rate=1.0,
        db_query_timeout_seconds=1,
    )
    store = FakeStore(["600000"])
    started = time.monotonic()
    deadline = datetime.now(ZoneInfo("Asia/Shanghai")) + timedelta(seconds=3.2)

    outcome = run_supervised_collection(
        target_date="2026-10-22",
        config=config,
        store=store,
        deadline=deadline,
        runner=SubprocessWorkerRunner(config, worker_entry=str(worker)),
    )
    elapsed = time.monotonic() - started

    assert outcome.stop_reason == STOP_DEADLINE
    assert elapsed <= 3.7, f"采集耗时 {elapsed:.3f}s 已突破绝对截止容差"


def test_database_queries_do_not_start_without_deadline_budget():
    """剩余窗口不足一次 DB 查询预算时，不得启动覆盖查询后越过截止。"""
    class SlowStore(FakeStore):
        def target_date_codes(self, target_date):
            self.query_calls += 1
            time.sleep(0.15)
            return set(self.covered)

    store = SlowStore(["600000"])
    started = time.monotonic()
    outcome = run_supervised_collection(
        target_date="2026-10-22",
        config=make_config(db_query_timeout_seconds=1),
        store=store,
        deadline=datetime.now(ZoneInfo("Asia/Shanghai")) + timedelta(seconds=0.05),
    )
    elapsed = time.monotonic() - started

    assert outcome.stop_reason == STOP_DEADLINE
    assert store.query_calls == 0
    assert elapsed < 0.1


# ---------------------------------------------------------------------------
# 工作进程入口契约（run_worker）
# ---------------------------------------------------------------------------


class _FakeUpdater:
    """替代 DailyKlineUpdater：记录入参并模拟进度写入与分片结果。"""

    stop_reason = "chunk_complete"

    def __init__(self, *args, **kwargs):
        self.kwargs = kwargs
        self.collect_args = None

    def collect(self, target_date, stocks, pool_size, chunk_index=0, progress_writer=None):
        from scripts.data.update_kline_daily import ChunkResult

        self.collect_args = {
            "target_date": target_date,
            "stocks": stocks,
            "pool_size": pool_size,
            "chunk_index": chunk_index,
        }
        if progress_writer is not None:
            progress_writer(stocks[-1]["code"], 0)
        return ChunkResult(
            target_date=target_date,
            chunk_index=chunk_index,
            chunk_size=len(stocks),
            total=pool_size,
            success=len(stocks),
            stop_reason=self.stop_reason,
        )


def _worker_args(tmp_path, **overrides):
    from types import SimpleNamespace

    symbols_file = tmp_path / "symbols.json"
    symbols_file.write_text(
        '{"target_date": "2026-10-21", "pool_size": 3, '
        '"stocks": [{"code": "600000", "symbol": "600000.SH"}, '
        '{"code": "000001", "symbol": "000001.SZ"}]}',
        encoding="utf-8",
    )
    base = dict(
        target_date="2026-10-21",
        symbols_file=str(symbols_file),
        result_json=str(tmp_path / "result.json"),
        progress_json=str(tmp_path / "progress.json"),
        pool_size=3,
        chunk_index=4,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_run_worker_writes_progress_and_result(tmp_path, monkeypatch):
    """工作进程必须串行读分片、写进度 last_started_code 与 result JSON，退出码 0。"""
    import json

    from scripts.data import update_kline_daily as u

    fake = _FakeUpdater()
    monkeypatch.setattr(u, "DailyKlineUpdater", lambda *a, **kw: fake)
    exit_code = u.run_worker(_worker_args(tmp_path))

    assert exit_code == 0
    assert fake.collect_args["target_date"] == "2026-10-21"
    assert fake.collect_args["pool_size"] == 3
    assert fake.collect_args["chunk_index"] == 4

    progress = json.loads((tmp_path / "progress.json").read_text(encoding="utf-8"))
    assert progress["last_started_code"] == "000001"

    result = json.loads((tmp_path / "result.json").read_text(encoding="utf-8"))
    assert result["stop_reason"] == "chunk_complete"
    assert result["chunk_size"] == 2


def test_run_worker_breaker_exit_code(tmp_path, monkeypatch):
    """熔断提前终止时退出码必须是 1，且 result JSON 仍写入。"""
    from scripts.data import update_kline_daily as u

    fake = _FakeUpdater()
    fake.stop_reason = "failure_rate"
    monkeypatch.setattr(u, "DailyKlineUpdater", lambda *a, **kw: fake)
    assert u.run_worker(_worker_args(tmp_path)) == 1
    assert (tmp_path / "result.json").exists()


def test_run_worker_missing_symbols_file_returns_fatal(tmp_path):
    """缺少 --symbols-file 必须返回致命退出码 2，不得抛出未捕获异常。"""
    from scripts.data import update_kline_daily as u

    assert u.run_worker(_worker_args(tmp_path, symbols_file=None)) == 2
    assert not (tmp_path / "result.json").exists()


def test_run_worker_rejects_invalid_config_before_database_access(tmp_path, monkeypatch):
    """worker 独立入口重载配置后也必须先强校验，再构造 updater/数据库。"""
    from scripts.data import update_kline_daily as u

    cfg = u.load_kline_update_config()
    cfg["failure_window_size"] = 0
    db_accessed = False

    class ForbiddenDatabaseManager:
        def __init__(self, *args, **kwargs):
            nonlocal db_accessed
            db_accessed = True
            raise AssertionError("invalid config must not access database")

    monkeypatch.setattr(u, "load_kline_update_config", lambda *a, **kw: cfg)
    monkeypatch.setattr(u, "DatabaseManager", ForbiddenDatabaseManager)

    assert u.run_worker(_worker_args(tmp_path)) == 2
    assert db_accessed is False


def test_worker_env_injects_repo_root(monkeypatch):
    """监督者必须显式把仓库根注入 PYTHONPATH，不依赖外部环境。"""
    from scripts.data.kline_collection import REPO_ROOT, SubprocessWorkerRunner

    runner = SubprocessWorkerRunner(make_config())

    monkeypatch.setenv("PYTHONPATH", "/custom/path")
    env = runner._worker_env()
    assert env["PYTHONPATH"] == REPO_ROOT + os.pathsep + "/custom/path"

    monkeypatch.delenv("PYTHONPATH", raising=False)
    assert runner._worker_env()["PYTHONPATH"] == REPO_ROOT


def test_worker_cli_imports_without_external_pythonpath(tmp_path, monkeypatch):
    """真实 CLI 冒烟：仅靠监督者注入的 PYTHONPATH 即可导入包并返回契约退出码 2。"""
    from scripts.data.kline_collection import WORKER_ENTRY, SubprocessWorkerRunner

    monkeypatch.delenv("PYTHONPATH", raising=False)
    bad_symbols = tmp_path / "bad_symbols.json"
    bad_symbols.write_text("{not-json", encoding="utf-8")

    proc = subprocess.run(
        [
            sys.executable, WORKER_ENTRY,
            "--worker",
            "--target-date", "2026-10-21",
            "--symbols-file", str(bad_symbols),
        ],
        capture_output=True,
        text=True,
        env=SubprocessWorkerRunner(make_config())._worker_env(),
        timeout=60,
    )

    assert proc.returncode == 2, proc.stderr
    assert "ModuleNotFoundError" not in proc.stderr
