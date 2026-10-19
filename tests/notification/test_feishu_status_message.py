"""
Task 4 Step 1 / 9.3.2：build_scan_summary_message 六态覆盖测试

六态必须：永不抛异常；completed_with_signals → None（走卡片）；
其他状态返回文本。
"""

import pytest

from scripts.feishu_push import build_scan_summary_message


# 基础三例（计划 Task 4 Step 1）


def test_missing_run_is_not_reported_as_zero_signal():
    """run=None 不能说"无符合形态"，必须说扫描结果缺失"""
    message = build_scan_summary_message("2026-10-16", None)
    assert message is not None
    assert "扫描结果缺失" in message
    assert "无符合形态" not in message


def test_market_block_reports_raw_candidates():
    """blocked_market 必须展示原始候选数和过滤原因"""
    run = {
        "status": "blocked_market",
        "raw_candidate_count": 10,
        "selected_count": 0,
        "filter_reason": "沪深300 DIF <= 0",
    }
    message = build_scan_summary_message("2026-10-16", run)
    assert message is not None
    assert "10 个原始形态候选" in message
    assert "市场风控拦截" in message
    assert "沪深300 DIF <= 0" in message


def test_completed_zero_is_the_only_true_zero_message():
    """completed_zero 是唯一可以说"完整扫描后未发现形态"的状态"""
    run = {
        "status": "completed_zero",
        "raw_candidate_count": 0,
        "selected_count": 0,
    }
    message = build_scan_summary_message("2026-10-16", run)
    assert message is not None
    assert "完整扫描后未发现形态" in message


# 六态全覆盖


def test_completed_with_signals_returns_none():
    """completed_with_signals → None（主流程走卡片）"""
    run = {
        "status": "completed_with_signals",
        "raw_candidate_count": 5,
        "selected_count": 3,
    }
    assert build_scan_summary_message("2026-10-16", run) is None


def test_running_status_does_not_raise():
    """running → 返回异常语义文本，不抛异常（第 9.3.2 节）"""
    run = {"status": "running"}
    message = build_scan_summary_message("2026-10-16", run)
    assert message is not None
    assert "尚未结束" in message or "未收敛" in message or "调度" in message


def test_blocked_data_status():
    """blocked_data → 数据不完整"""
    run = {"status": "blocked_data"}
    message = build_scan_summary_message("2026-10-16", run)
    assert message is not None
    assert "数据不完整" in message


def test_failed_status_includes_reason_if_present():
    """failed → 扫描任务失败，有 filter_reason 时附带"""
    run = {"status": "failed", "filter_reason": "scan_runs_persist_failed: xxx"}
    message = build_scan_summary_message("2026-10-16", run)
    assert message is not None
    assert "扫描任务失败" in message
    assert "scan_runs_persist_failed" in message


def test_failed_status_no_reason():
    """failed 无 filter_reason 也不崩"""
    run = {"status": "failed"}
    message = build_scan_summary_message("2026-10-16", run)
    assert message is not None
    assert "扫描任务失败" in message


def test_unknown_status_does_not_raise():
    """未知 status → 返回状态异常文本，不抛异常"""
    run = {"status": "some_weird_value_not_in_enum"}
    message = build_scan_summary_message("2026-10-16", run)
    assert message is not None
    assert "状态异常" in message or "some_weird_value_not_in_enum" in message


def test_run_dict_malformed_does_not_raise():
    """run 缺字段/非标准结构也不抛异常"""
    # 直接测试 status 缺键
    message = build_scan_summary_message("2026-10-16", {})
    assert message is not None
