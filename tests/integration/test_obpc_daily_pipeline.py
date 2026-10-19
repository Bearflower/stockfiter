"""
Task 9 Step A：OBPC 日流水线端到端集成测试。

4 条场景测试，全部真实驱动生产代码路径：
  场景 1  incomplete_1059_of_2911 —— 覆盖率不足 → blocked_data
  场景 2  2026-10-16_complete         —— 市场风控 → blocked_market
  场景 3  complete_no_pattern          —— 完整扫描零形态 → completed_zero
  场景 4  run_rescan                  —— 重扫幂等

三条消息断言通过真实的 build_scan_summary_message 取文案，不自己拼字符串。
"""

from datetime import datetime, timezone

import pytest


# =====================================================================
# 场景 1：数据不完整阻断扫描（覆盖率不足）
# =====================================================================


def test_incomplete_data_blocks_scan_and_sends_data_warning(pipeline):
    """
    覆盖率不足 → record_scan_blocked("blocked_data", "kline_not_ready")。

    期望：
      - scan_status == "blocked_data"
      - scanned_count == 0
      - 推送文案含「数据不完整」
    """
    result = pipeline.run_incomplete_data(signal_date="2026-10-16")

    assert result["scan_status"] == "blocked_data", (
        f"期望 blocked_data，实际 {result['scan_status']}"
    )
    assert result["scanned_count"] == 0, (
        f"数据阻断时不应有扫描，实际 scanned_count={result['scanned_count']}"
    )
    assert "数据不完整" in result["message"], (
        f"推送文案应含「数据不完整」，实际：{result['message']}"
    )


# =====================================================================
# 场景 2：市场风控拦截（有原始候选但全被风控拦）
# =====================================================================


def test_market_block_preserves_raw_candidate_count(pipeline):
    """
    2026-10-16 完整扫描后，市场风控拦截了全部候选。

    期望：
      - scan_status == "blocked_market"
      - raw_candidate_count == 10
      - selected_count == 0
      - 推送文案含原始候选数与风控原因
    """
    result = pipeline.run_market_block(
        signal_date="2026-10-16",
        raw_candidate_count=10,
        filter_reason="沪深300 DIF <= 0",
    )

    assert result["scan_status"] == "blocked_market", (
        f"期望 blocked_market，实际 {result['scan_status']}"
    )
    assert result["raw_candidate_count"] == 10, (
        f"期望 raw=10，实际 {result['raw_candidate_count']}"
    )
    assert result["selected_count"] == 0, (
        f"期望 selected=0，实际 {result['selected_count']}"
    )
    # 真实推送文案必须含原始候选数和「市场风控拦截」关键词
    assert "10" in result["message"] and "原始形态候选" in result["message"], (
        f"推送文案应展示原始候选数，实际：{result['message']}"
    )
    assert "市场风控拦截" in result["message"], (
        f"推送文案应含「市场风控拦截」，实际：{result['message']}"
    )


# =====================================================================
# 场景 3：完整扫描后零形态（真·零信号，非阻断也非风控）
# =====================================================================


def test_completed_zero_is_truthful(pipeline):
    """
    完整扫描 2911 只主板股票，没有任何形态候选。

    期望：
      - scan_status == "completed_zero"
      - 推送文案含「完整扫描后未发现形态」
    """
    result = pipeline.run_completed_zero(signal_date="2026-10-17")

    assert result["scan_status"] == "completed_zero", (
        f"期望 completed_zero，实际 {result['scan_status']}"
    )
    assert "完整扫描后未发现形态" in result["message"], (
        f"推送文案应含「完整扫描后未发现形态」，实际：{result['message']}"
    )


# =====================================================================
# 场景 4：周六补全后重扫周五 —— 幂等（连续两次只执行一次）
# =====================================================================


def test_saturday_backfill_rescans_friday_once(pipeline):
    """
    07:30 重扫连续触发两次（调度器重启 / 守护进程重复进入），
    同数据版本的 rescan 只执行一次。

    期望：
      - first.executed is True（首次允许执行）
      - second.executed is False（已完成，幂等拦截）
    """
    data_version = datetime(2026, 10, 17, 7, 30, 0, tzinfo=timezone.utc)

    first = pipeline.run_rescan(
        push_date="2026-10-17",
        signal_date="2026-10-16",
        already_completed_status="completed_zero",
        data_version=data_version,
    )

    assert first["first_executed"] is True, (
        "首次重扫应允许执行（无既有 rescan 记录）"
    )
    assert first["second_executed"] is False, (
        f"第二次重扫应被幂等拦截，实际 executed={first['second_executed']}"
    )


# =====================================================================
# 附加阻断用例：failed / blocked_data 允许重试
# =====================================================================


def test_rescan_retry_after_failed_allowed(pipeline):
    """
    rescan 状态为 failed 时，允许再次重扫（重试语义）。
    """
    from scripts.scheduler_obpc import is_rescan_completed

    data_version = datetime(2026, 10, 17, 7, 30, 0, tzinfo=timezone.utc)

    # 先写一条 failed 的 rescan 记录
    pipeline.db.upsert_scan_run(
        signal_date="2026-10-16",
        run_kind="rescan",
        status="failed",
        filter_reason="kline_update_timeout",
        data_version=data_version,
    )

    # is_rescan_completed 应对 failed 返回 False（允许重试）
    completed = is_rescan_completed(pipeline.db, "2026-10-16", data_version)
    assert completed is False, "failed 状态应允许重试重扫"


def test_rescan_retry_after_blocked_data_allowed(pipeline):
    """
    rescan 状态为 blocked_data 时，允许再次重扫（数据补齐后重试）。
    """
    from scripts.scheduler_obpc import is_rescan_completed

    data_version = datetime(2026, 10, 17, 7, 30, 0, tzinfo=timezone.utc)

    pipeline.db.upsert_scan_run(
        signal_date="2026-10-16",
        run_kind="rescan",
        status="blocked_data",
        filter_reason="kline_not_ready",
        data_version=data_version,
    )

    completed = is_rescan_completed(pipeline.db, "2026-10-16", data_version)
    assert completed is False, "blocked_data 状态应允许重试重扫"
