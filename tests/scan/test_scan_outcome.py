"""
Task 4 / 必修 8：daily_scan.py 扫描结果与状态收敛测试

覆盖：
  - 大盘拦截仍保存 raw_candidate_count、status=blocked_market、selected_count=0
  - 零信号也写 [] 文件
  - 异常收敛为 failed
  - 异常路径下 scan_runs 仍落盘六态之一（通过 persist_scan_outcome 纯函数验证）
"""

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import pytest

from scripts.daily_scan import persist_scan_outcome


# =====================================================================
# persist_scan_outcome 状态机单元测试（不依赖 DB）
# =====================================================================


class TestPersistScanOutcome:
    """验证 scan_runs 六态判定逻辑正确（DB 层由 DatabaseManager 测试）"""

    def test_completed_with_signals(self):
        """selected_count > 0 → completed_with_signals"""
        with mock.patch("scripts.daily_scan.DatabaseManager") as MockDB:
            persist_scan_outcome(
                signal_date="2026-10-16",
                run_kind="initial",
                raw_candidate_count=5,
                selected_count=3,
                trend_filter_reason=None,
                stock_pool_count=2900,
                scanned_count=2890,
                data_version=None,
            )
            upsert = MockDB.return_value.upsert_scan_run  # 契约：persist_scan_outcome 直接实例化 DatabaseManager 后调用 upsert_scan_run
            call_args = upsert.call_args
            assert call_args[1]["status"] == "completed_with_signals"
            assert call_args[1]["raw_candidate_count"] == 5
            assert call_args[1]["selected_count"] == 3
            assert call_args[1]["filter_reason"] is None

    def test_data_metrics_is_forwarded_to_upsert(self):
        """修复 issue 1：persist_scan_outcome 必须把 data_metrics 透传给 upsert_scan_run。

        修复前 data_metrics 未接收、未传参 → scan_runs.data_metrics 永远为 {}，
        验收/监控读不到分级数据。
        """
        with mock.patch("scripts.daily_scan.DatabaseManager") as MockDB:
            dm = {"policy_mode": "graded", "market_regime": "weak"}
            persist_scan_outcome(
                signal_date="2026-10-16",
                run_kind="initial",
                raw_candidate_count=5,
                selected_count=3,
                trend_filter_reason=None,
                stock_pool_count=2900,
                scanned_count=2890,
                data_version=None,
                data_metrics=dm,
            )
            upsert = MockDB.return_value.upsert_scan_run
            call_args = upsert.call_args
            # 核心契约：data_metrics 必须出现在 upsert_scan_run 的实参中
            assert call_args[1]["data_metrics"] == dm

    def test_blocked_market(self):
        """raw_candidate_count > 0 且 selected_count == 0 → blocked_market"""
        with mock.patch("scripts.daily_scan.DatabaseManager") as MockDB:
            persist_scan_outcome(
                signal_date="2026-10-16",
                run_kind="initial",
                raw_candidate_count=10,
                selected_count=0,
                trend_filter_reason="沪深300 DIF <= 0",
                stock_pool_count=2900,
                scanned_count=2890,
                data_version=None,
            )
            upsert = MockDB.return_value.upsert_scan_run
            call_args = upsert.call_args
            assert call_args[1]["status"] == "blocked_market"
            assert call_args[1]["raw_candidate_count"] == 10
            assert call_args[1]["selected_count"] == 0
            assert call_args[1]["filter_reason"] == "沪深300 DIF <= 0"

    def test_forced_failure_reason_writes_failed(self):
        """P2-1：forced_failure_reason 非空 → 一律写 failed。

        修复前配置加载失败/异常路径传 raw=0，被状态机误判为 completed_zero，
        监控只查 failed/blocked_data 时漏报。
        """
        with mock.patch("scripts.daily_scan.DatabaseManager") as MockDB:
            persist_scan_outcome(
                signal_date="2026-10-16",
                run_kind="initial",
                raw_candidate_count=0,
                selected_count=0,
                trend_filter_reason=None,
                stock_pool_count=0,
                scanned_count=0,
                data_version=None,
                forced_failure_reason="config_load_failed: boom",
            )
            upsert = MockDB.return_value.upsert_scan_run
            call_args = upsert.call_args
            assert call_args[1]["status"] == "failed"
            assert call_args[1]["filter_reason"] == "config_load_failed: boom"

    def test_completed_zero_no_raw(self):
        """raw_candidate_count == 0 → completed_zero"""
        with mock.patch("scripts.daily_scan.DatabaseManager") as MockDB:
            persist_scan_outcome(
                signal_date="2026-10-16",
                run_kind="initial",
                raw_candidate_count=0,
                selected_count=0,
                trend_filter_reason=None,
                stock_pool_count=2900,
                scanned_count=2890,
                data_version=None,
            )
            upsert = MockDB.return_value.upsert_scan_run
            call_args = upsert.call_args
            assert call_args[1]["status"] == "completed_zero"

    def test_all_filtered_after_market_pass_is_blocked_market(self):
        """有原始候选但全部被评分/冷却期/额度拦截（非市场风控）→ blocked_market。

        第 9.3 节状态机闭环：raw_candidate_count > 0 且 selected_count == 0
        一律判 blocked_market（用 RISK_CONTROLS_FILTERED_ALL 标识非市场风控原因），
        绝不能记 completed_zero 而发送误导性的"完整扫描后未发现形态"通知。
        """
        from scripts.daily_scan import RISK_CONTROLS_FILTERED_ALL
        with mock.patch("scripts.daily_scan.DatabaseManager") as MockDB:
            persist_scan_outcome(
                signal_date="2026-10-16",
                run_kind="initial",
                raw_candidate_count=2,
                selected_count=0,
                trend_filter_reason=None,
                stock_pool_count=2900,
                scanned_count=2890,
                data_version=None,
            )
            upsert = MockDB.return_value.upsert_scan_run
            call_args = upsert.call_args
            assert call_args[1]["status"] == "blocked_market"
            assert call_args[1]["filter_reason"] == RISK_CONTROLS_FILTERED_ALL

    def test_data_version_passed_through(self):
        """data_version 正常透传到 upsert"""
        ts = datetime(2026, 10, 16, 22, 15, 0, tzinfo=timezone.utc)
        with mock.patch("scripts.daily_scan.DatabaseManager") as MockDB:
            persist_scan_outcome(
                signal_date="2026-10-16",
                run_kind="rescan",
                raw_candidate_count=0,
                selected_count=0,
                trend_filter_reason=None,
                stock_pool_count=2900,
                scanned_count=2890,
                data_version=ts,
            )
            upsert = MockDB.return_value.upsert_scan_run
            call_args = upsert.call_args
            assert call_args[1]["run_kind"] == "rescan"
            assert call_args[1]["data_version"] == ts
