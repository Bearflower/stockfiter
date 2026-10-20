"""
tests/data/test_scan_run_data_metrics.py

真实数据库持久化契约测试（修复 issue 1 的端到端验证）。

背景：修复前 persist_scan_outcome 未接收 data_metrics、upsert_scan_run 未传参，
scan_runs.data_metrics 永远为 '{}'，验收脚本 verify_obpc_pipeline.py 和
monitor.sh 的极弱穿透检查永远读不到分级数据。

本文件用真实 PostgreSQL（`database` fixture，未配置 TEST_DB_PASSWORD 时 skip）
验证：
  1. upsert_scan_run(data_metrics=...) 后，get_scan_run 能原样读回该 dict；
  2. data_metrics 为 None（旧调用）时落库为 {}，不报错；
  3. 重复 UPSERT 会覆盖 data_metrics（与 DO UPDATE SET 语义一致）。

安全：测试只写入 tests 专用的 signal_date（2026-10-16）并自行清理该行，
不触碰生产数据。
"""

import pytest


# 专用日期，避免与生产记录（通常为真实交易日）冲突
_TEST_SIGNAL_DATE = "2026-10-16"


def _cleanup(database, signal_date=_TEST_SIGNAL_DATE):
    """删除本测试写入的 scan_runs 行（fixture 不负责 scan_runs 清理）。"""
    try:
        cur = database.conn.cursor()
        cur.execute(
            "DELETE FROM scan_runs WHERE signal_date = %s",
            (signal_date,),
        )
        database.conn.commit()
        cur.close()
    except Exception:
        try:
            database.conn.rollback()
        except Exception:
            pass


def test_data_metrics_persisted_and_read_back(database):
    """upsert_scan_run 写入的 data_metrics 必须能被 get_scan_run 原样读回。"""
    dm = {
        "policy_mode": "graded",
        "market_regime": "weak",
        "position_multiplier": 0.5,
        "daily_signal_limit": 3,
    }
    try:
        database.upsert_scan_run(
            signal_date=_TEST_SIGNAL_DATE,
            run_kind="initial",
            status="completed_with_signals",
            stock_pool_count=2900,
            scanned_count=2890,
            raw_candidate_count=5,
            selected_count=2,
            filter_reason=None,
            data_metrics=dm,
        )

        row = database.get_scan_run(_TEST_SIGNAL_DATE, "initial")
        assert row is not None, "scan_runs 记录应已写入"
        assert row["data_metrics"] == dm, (
            f"data_metrics 应原样持久化，期望 {dm}，实际 {row['data_metrics']}"
        )
        assert row["data_metrics"].get("policy_mode") == "graded"
    finally:
        _cleanup(database)


def test_data_metrics_none_falls_back_to_empty_dict(database):
    """data_metrics=None 时必须落库为 {}，且不抛异常（旧调用兼容）。"""
    try:
        database.upsert_scan_run(
            signal_date=_TEST_SIGNAL_DATE,
            run_kind="rescan",
            status="completed_zero",
            data_metrics=None,
        )
        row = database.get_scan_run(_TEST_SIGNAL_DATE, "rescan")
        assert row is not None
        assert row["data_metrics"] == {}, (
            f"data_metrics=None 应落库为 {{}}，实际 {row['data_metrics']}"
        )
    finally:
        _cleanup(database)


def test_data_metrics_overwritten_on_upsert(database):
    """同 (signal_date, run_kind) 重复 UPSERT：data_metrics 被最新值覆盖。"""
    try:
        database.upsert_scan_run(
            signal_date=_TEST_SIGNAL_DATE,
            run_kind="initial",
            status="blocked_market",
            data_metrics={"policy_mode": "shadow"},
        )
        database.upsert_scan_run(
            signal_date=_TEST_SIGNAL_DATE,
            run_kind="initial",
            status="completed_with_signals",
            data_metrics={"policy_mode": "graded", "market_regime": "strong"},
        )
        row = database.get_scan_run(_TEST_SIGNAL_DATE, "initial")
        assert row is not None
        assert row["data_metrics"] == {
            "policy_mode": "graded",
            "market_regime": "strong",
        }, "重复 UPSERT 应覆盖 data_metrics"
    finally:
        _cleanup(database)
