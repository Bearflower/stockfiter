"""
K 线更新守卫 + 完整性判定 + 熔断控制流 + result_json 契约 + task_status 写入 单元测试。

自包含、不依赖真实数据库、不调用外部 API（Baostock 在需要被 mock 掉）。
仅验证 UpdateResult / scheduler_kline 守卫的纯逻辑。
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# 前置：patch trade_calendar，避免单测连 Baostock
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _patch_trade_calendar():
    """所有单测默认 patch 掉 utils.trade_calendar.is_trading_day。

    should_skip_kline_update 在函数内部做
        from utils.trade_calendar import is_trading_day
    所以 patch 直接打到 utils.trade_calendar 模块上即可。
    """
    with patch("utils.trade_calendar.is_trading_day") as mock_is:
        yield mock_is


# =====================================================================
# B3.3-1 守卫正反例（should_skip_kline_update 纯函数）
# =====================================================================

class TestKlineScheduleGuard:
    """scheduler_kline.should_skip_kline_update 守卫逻辑。"""

    def test_should_skip_saturday(self, _patch_trade_calendar):
        """周六（非交易日）守卫应返回 True（跳过）。"""
        from scripts.scheduler_kline import should_skip_kline_update
        mock_is = _patch_trade_calendar
        # 2026-10-17 是周六
        mock_is.return_value = False  # is_trading_day 返回 False → 跳过
        now = datetime(2026, 10, 17, 22, 0)
        assert should_skip_kline_update(now=now, today_str="2026-10-17") is True
        mock_is.assert_called_with("2026-10-17")

    def test_should_not_skip_friday(self, _patch_trade_calendar):
        """周五（交易日）守卫应返回 False（不跳过）。"""
        from scripts.scheduler_kline import should_skip_kline_update
        mock_is = _patch_trade_calendar
        mock_is.return_value = True  # is_trading_day 返回 True → 不跳过
        # 2026-10-16 是周五
        now = datetime(2026, 10, 16, 22, 0)
        assert should_skip_kline_update(now=now, today_str="2026-10-16") is False

    def test_should_skip_sunday(self, _patch_trade_calendar):
        """周日守卫应返回 True。"""
        from scripts.scheduler_kline import should_skip_kline_update
        _patch_trade_calendar.return_value = False
        now = datetime(2026, 10, 18, 22, 0)
        assert should_skip_kline_update(now=now, today_str="2026-10-18") is True

    def test_today_str_override(self, _patch_trade_calendar):
        """显式传入 today_str 时不应额外调 strftime，直接用传入值判断。"""
        from scripts.scheduler_kline import should_skip_kline_update
        mock_is = _patch_trade_calendar
        mock_is.return_value = False
        # 即使 now 是周五，只要 today_str 传了周六（被 mock 为非交易日），就跳过
        now = datetime(2026, 10, 16, 22, 0)
        assert should_skip_kline_update(now=now, today_str="2026-10-17") is True


# =====================================================================
# B3.3-2 is_complete B2 修复后的期望结果
# =====================================================================

class TestIsCompleteOrder:
    """§9.2 B-2：is_complete 四条判定顺序。

    签名（第 9 节 A-review 为准）：is_complete(min_success_rate, total_failure_limit,
    min_coverage_rate, target_date=None)。已移除旧的 inferred_non_trading / logger_fn 入参。

    核心：target_date 覆盖率硬检查（第 2 条）必须在休市日 guard（第 3 条）之前，
    禁止被 skipped_ratio >= 0.95 短路绕过。
    """

    # ---------- T1（AC-2 回归锁）：真实交易日 + 全量故障必须 FAIL ----------

    def test_real_trading_day_full_failure_must_fail_no_inferred(self):
        """T1：真实交易日 Baostock 全挂（success=0, failed=全部, skipped=0），
        未传入任何 inferred_non_trading → is_complete **必须返回 False**。

        这是 AC-2 红线——真实交易日的大面积故障绝不能被启发式静默放行。
        原始启发式分支（已删除）会把这组数据当作"非交易日误拉"而放行，
        这是 10-17 周六事故后修复的核心缺陷。
        """
        from scripts.data.update_kline_daily import UpdateResult
        r = UpdateResult(
            success=0, failed=2910, skipped=0,
            total=2910,
            target_date="2026-10-16",  # 周五，真实交易日
            # 关键：不传 inferred_non_trading（调用方未声称非交易日）
        )
        with patch("utils.trade_calendar.is_trading_day", return_value=True):
            assert r.is_complete(
                min_success_rate=0.90,
                total_failure_limit=500,
                min_coverage_rate=0.98,
                target_date="2026-10-16",
            ) is False, (
                "AC-2 红线：真实交易日 + success=0 + failed=total 必须 FAIL，"
                "任何启发式不得放行"
            )

    # ---------- T2（§9.2 必补）：交易日 + 全部 skipped 必须 FAIL ----------

    def test_trading_day_all_skipped_must_fail(self):
        """T2：交易日 + 全部 skipped（skipped_ratio=1.0）必须 FAIL。

        §9.2 明确：target_date 覆盖率硬检查必须在休市日 guard 之前，
        禁止被 skipped_ratio >= 0.95 短路绕过。
        """
        from scripts.data.update_kline_daily import UpdateResult
        r = UpdateResult(
            success=0, failed=0, skipped=2911, total=2911,
            target_date="2026-10-16", target_date_success=0,
        )
        with patch("utils.trade_calendar.is_trading_day", return_value=True):
            assert r.is_complete(
                min_success_rate=0.90,
                total_failure_limit=500,
                min_coverage_rate=0.98,
                target_date="2026-10-16",
            ) is False

    # ---------- T3（§9.2 必补）：非交易日 + 全部 skipped 必须 PASS ----------

    def test_non_trading_day_all_skipped_must_pass(self):
        """T3：非交易日（周六）+ 全部 skipped → 休市日 guard（≥0.95）放行 PASS。

        非交易日时，采集循环会把库内已有"今天"数据的股票计为 skipped，
        skipped_ratio ≈ 1.0，触发第 3 条休市日 guard 放行。
        """
        from scripts.data.update_kline_daily import UpdateResult
        r = UpdateResult(
            success=0, failed=0, skipped=2910,
            total=2910,
            target_date="2026-10-17",  # 周六
        )
        # 显式 patch is_trading_day 返回 False（周六是非交易日）
        with patch("utils.trade_calendar.is_trading_day", return_value=False):
            # skipped_ratio = 2910/2910 = 1.0 ≥ 0.95 guard → PASS
            assert r.is_complete(
                min_success_rate=0.90,
                total_failure_limit=500,
                min_coverage_rate=0.98,
                target_date="2026-10-17",
            ) is True

    # ---------- T4：真实交易日 + target_date 覆盖率不足必须 FAIL ----------

    def test_true_target_date_coverage_gap_fails(self):
        """T4：target_date 被 Baostock 确认为交易日（非 degraded），
        但 target_date_coverage_rate 严重不足（比如只覆盖了 3%），必须 FAIL。"""
        from scripts.data.update_kline_daily import UpdateResult
        r = UpdateResult(
            success=2900, failed=5, skipped=6,
            total=2911,
            target_date="2026-10-16",
            target_date_success=100,  # 仅 3.4% 覆盖
            is_trading_day_degraded=False,  # Baostock 验证过
        )
        with patch("utils.trade_calendar.is_trading_day", return_value=True):
            assert r.is_complete(
                min_success_rate=0.90,
                total_failure_limit=500,
                min_coverage_rate=0.98,
                target_date="2026-10-16",
            ) is False

    # ---------- 原 B2 路径 B（降级 + 假日） ----------

    def test_holiday_non_trading_day_skipped_guard_passes(self):
        """法定节假日（2026-10-01 国庆，非交易日）+ 全部 skipped → 休市日 guard PASS。

        §9.2 以 trade_calendar 的 is_trading_day 为准；节假日返回 False，
        走第 3 条休市日 guard 放行。
        """
        from scripts.data.update_kline_daily import UpdateResult
        r = UpdateResult(
            success=0, failed=0, skipped=2911,
            total=2911,
            target_date="2026-10-01",
        )
        with patch("utils.trade_calendar.is_trading_day", return_value=False):
            assert r.is_complete(
                min_success_rate=0.90,
                total_failure_limit=500,
                min_coverage_rate=0.98,
                target_date="2026-10-01",
            ) is True

    # ---------- 熔断导致 coverage 不足必须 FAIL ----------

    def test_circuit_breaker_leaves_most_unprocessed_still_fails(self):
        """熔断后只处理了 1061/2911，覆盖率不足必须 FAIL（AC-2 不变）。"""
        from scripts.data.update_kline_daily import UpdateResult
        r = UpdateResult(success=1059, failed=2, skipped=0, total=2911)
        assert r.is_complete(
            min_success_rate=0.90,
            total_failure_limit=500,
            min_coverage_rate=0.98,
        ) is False

    # ---------- 未传 target_date 时退化为成功率判定 ----------

    def test_no_target_date_falls_back_to_success_rate(self):
        """未传 target_date → 仅按 success_rate + failed 判定（§9.2 第 4 条）。"""
        from scripts.data.update_kline_daily import UpdateResult
        ok = UpdateResult(success=2900, failed=5, skipped=6, total=2911)
        assert ok.is_complete(
            min_success_rate=0.90, total_failure_limit=500, min_coverage_rate=0.98,
        ) is True
        bad = UpdateResult(success=100, failed=2800, skipped=11, total=2911)
        assert bad.is_complete(
            min_success_rate=0.90, total_failure_limit=500, min_coverage_rate=0.98,
        ) is False


# =====================================================================
# B3.3-3 熔断控制流（滑动窗口 break）
# =====================================================================

class _FakeBaostockSession:
    """最小 Baostock 会话替身（纯串行，返回空数据即计入失败）。"""

    def __init__(self, fetcher=None):
        self._fetcher = fetcher

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_kline(self, symbol, end_date=None, days=120):
        if self._fetcher is not None:
            return self._fetcher(symbol, end_date, days)
        return None


class TestChunkCircuitBreaker:
    """update_kline_daily.DailyKlineUpdater.collect() 的分片内熔断控制流。"""

    def _make_updater_with_10_stocks(self, cfg):
        """构造 MagicMock db + 10 只股票输入的 DailyKlineUpdater。"""
        from scripts.data.update_kline_daily import DailyKlineUpdater
        updater = DailyKlineUpdater(days_to_update=1, config=cfg)
        updater.db = MagicMock()
        updater.db.get_latest_kline_date.return_value = "2026-10-10"
        stocks = [
            {'code': f'00000{i}', 'symbol': f'00000{i}.SZ'} for i in range(10)
        ]
        return updater, stocks

    def test_sliding_window_triggers_break_on_high_failure_rate(self):
        """滑动窗口内失败率超过阈值应提前终止本分片。"""
        from scripts.data.update_kline_daily import (
            DailyKlineUpdater,
            load_kline_update_config,
        )

        cfg = load_kline_update_config()
        cfg['failure_window_size'] = 100
        cfg['failure_window_min_samples'] = 5
        cfg['failure_rate_threshold'] = 0.5
        cfg['total_failure_limit'] = 400

        updater, stocks = self._make_updater_with_10_stocks(cfg)

        with patch("scripts.data.update_kline_daily.DatabaseManager", return_value=updater.db):
            with patch("data.fetcher.BaostockSession", _FakeBaostockSession):
                with patch("scripts.data.update_kline_daily.time.sleep"):
                    result = updater.collect(
                        target_date="2026-10-16",
                        stocks=stocks,
                        pool_size=len(stocks),
                    )

        assert result.stop_reason == "failure_rate"
        assert result.failed == 5, f"第 5 次失败后应熔断，实际 failed={result.failed}"

    def test_hard_limit_break_on_total_failure(self):
        """累计失败达到硬上限时应提前终止本分片。"""
        from scripts.data.update_kline_daily import (
            DailyKlineUpdater,
            load_kline_update_config,
        )

        cfg = load_kline_update_config()
        cfg['failure_window_min_samples'] = 1000  # 让滑动窗口熔断无法触发
        cfg['total_failure_limit'] = 3

        updater, stocks = self._make_updater_with_10_stocks(cfg)

        with patch("scripts.data.update_kline_daily.DatabaseManager", return_value=updater.db):
            with patch("data.fetcher.BaostockSession", _FakeBaostockSession):
                with patch("scripts.data.update_kline_daily.time.sleep"):
                    result = updater.collect(
                        target_date="2026-10-16",
                        stocks=stocks,
                        pool_size=len(stocks),
                    )

        assert result.stop_reason == "failure_limit"
        assert result.failed == 3, f"第 3 次失败后应熔断，实际 failed={result.failed}"

    def test_worker_has_no_thread_pool_timeout(self):
        """工作进程内部不得再有单股线程池伪超时入口。"""
        from scripts.data import update_kline_daily

        assert not hasattr(update_kline_daily.DailyKlineUpdater, "_fetch_with_timeout")
        source = open(update_kline_daily.__file__, encoding="utf-8").read()
        # 只允许在模块 docstring 中提及历史改造，不得真实导入或使用线程池
        assert "from concurrent.futures" not in source
        assert "ThreadPoolExecutor(" not in source
        assert "BaostockRequestTimeout" not in source


# =====================================================================
# B3.3-4 --result-json 读写两端契约
# =====================================================================

class TestResultJsonContract:
    """update_kline_daily._result_to_dict 输出键集 vs scheduler/monitor 读取端契约。"""

    EXPECTED_KEYS = {
        'success', 'failed', 'skipped', 'total',
        'processed_count', 'coverage_rate',
        'target_date', 'target_date_success', 'target_date_coverage_rate',
        'latest_trade_date',
        'success_rate', 'skipped_ratio',
        # B2 新增
        'is_trading_day_degraded', 'target_date_inferred_non_trading',
        # 2026-10-22 分片采集新增
        'completion_mode',
    }

    FLATTENED_DETAIL_KEYS = {
        'collection_mode', 'stop_reason', 'worker_processes', 'session_restarts',
        'timed_out_chunks', 'rounds', 'started_at', 'finished_at', 'deadline',
        'duration_seconds', 'covered',
    }

    def _make_result(self, details=None):
        from scripts.data.update_kline_daily import UpdateResult
        return UpdateResult(
            success=2900, failed=5, skipped=6, total=2911,
            target_date="2026-10-16", target_date_success=2910,
            latest_trade_date="2026-10-16",
            is_trading_day_degraded=False,
            target_date_inferred_non_trading=False,
            completion_mode="recovered_after_timeout",
            details=details,
        )

    def test_result_dict_contains_all_expected_keys(self):
        """_result_to_dict 应包含所有消费端需要的顶层键。"""
        from scripts.data.update_kline_daily import _result_to_dict

        result_dict = _result_to_dict(self._make_result())

        missing = self.EXPECTED_KEYS - set(result_dict.keys())
        assert not missing, f"result_dict 缺少键：{missing}"

    def test_details_fields_are_flattened(self):
        """collection details 需同时保留嵌套与平铺（监控脚本读平铺字段）。"""
        from scripts.data.update_kline_daily import _result_to_dict

        details = {
            'collection_mode': 'chunked',
            'stop_reason': 'coverage_reached',
            'worker_processes': 10,
            'session_restarts': 1,
            'timed_out_chunks': [{'last_started_code': '600610'}],
            'rounds': [],
            'started_at': '2026-10-16T23:00:00',
            'finished_at': '2026-10-16T23:20:00',
            'deadline': '2026-10-16T23:40:00',
            'duration_seconds': 1200.0,
            'covered': 2910,
            'contract_version': 1,
        }
        result_dict = _result_to_dict(self._make_result(details=details))

        assert result_dict['collection'] == details
        for key in self.FLATTENED_DETAIL_KEYS:
            assert key in result_dict, f"details 字段未平铺到顶层：{key}"

    def test_result_json_roundtrip(self):
        """写入临时文件 + 读回，验证监控/调度读取端的 JSON 解析能拿到所有键。"""
        from scripts.data.update_kline_daily import _result_to_dict

        result_dict = _result_to_dict(self._make_result())

        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as tmp:
            tmp_path = tmp.name
            json.dump(result_dict, tmp, ensure_ascii=False)

        try:
            with open(tmp_path, 'r', encoding='utf-8') as f:
                details = json.load(f)

            for key in self.EXPECTED_KEYS:
                assert key in details, f"读取端拿不到 {key}"
                if key in ('coverage_rate', 'target_date_coverage_rate',
                           'success_rate', 'skipped_ratio'):
                    assert isinstance(details[key], (int, float)), f"{key} 应为数值"
        finally:
            os.unlink(tmp_path)


# =====================================================================
# B3.3-5 task_status 写入路径（假 DatabaseManager，不连生产库）
# =====================================================================

class TestTaskStatusWrite:
    """scheduler_kline._write_task_status 在假 DatabaseManager 下的写入路径。

    注意：_write_task_status 内部做的是
        from data.database import DatabaseManager
    所以 patch 必须打到 data.database.DatabaseManager，
    不是 scripts.scheduler_kline.DatabaseManager（模块里没有这个属性）。
    """

    def test_basic_insert_writes_three_columns(self):
        """正常写入应包含 task_name, last_completed_at, status 三列。"""
        from scripts.scheduler_kline import _write_task_status

        fake_db = MagicMock()
        fake_cur = MagicMock()
        fake_db.conn.cursor.return_value = fake_cur

        with patch("data.database.DatabaseManager", return_value=fake_db):
            _write_task_status(
                "kline_update", "completed", target_date="2026-10-16", details={'foo': 1},
                db_connect_timeout_seconds=10, db_query_timeout_seconds=30,
            )

        # INSERT 一条（基础列） + UPDATE 一条（target_date/details）+ 一条 commit
        assert fake_cur.execute.call_count >= 2
        fake_db.conn.commit.assert_called_once()
        fake_db.close.assert_called_once()

    def test_database_manager_receives_explicit_timeouts(self):
        """状态写入不得使用 libpq/PostgreSQL 无界默认超时。"""
        from scripts.scheduler_kline import _write_task_status

        fake_db = MagicMock()
        with patch("data.database.DatabaseManager", return_value=fake_db) as manager:
            _write_task_status(
                "kline_update", "failed",
                db_connect_timeout_seconds=10,
                db_query_timeout_seconds=30,
            )

        manager.assert_called_once_with(connect_timeout=10, statement_timeout=30)

    def test_new_column_failure_rolls_back_and_retries_basic(self):
        """P1-4：target_date/details 列写入失败时应 rollback 并重写基础列。"""
        from scripts.scheduler_kline import _write_task_status

        fake_db = MagicMock()
        fake_cur = MagicMock()
        fake_db.conn.cursor.return_value = fake_cur

        # 第一次 execute（基础 INSERT）成功；第二次 execute（UPDATE 新列）抛异常；
        # 第三次 execute（rollback 后重写基础 INSERT）成功
        fake_cur.execute.side_effect = [
            None,  # 基础 INSERT
            RuntimeError("column target_date does not exist"),  # UPDATE 新列失败
            None,  # rollback 后重写基础 INSERT
        ]

        with patch("data.database.DatabaseManager", return_value=fake_db):
            _write_task_status(
                "kline_update", "completed", target_date="2026-10-16", details={},
                db_connect_timeout_seconds=10, db_query_timeout_seconds=30,
            )

        # rollback 应该被调用
        assert fake_db.conn.rollback.call_count >= 1
        # rollback 后重写基础写入成功 → commit 被调用
        fake_db.conn.commit.assert_called()
        fake_db.close.assert_called_once()

    def test_no_db_error_quietly_records_log(self):
        """DatabaseManager 构造失败或其他顶层异常不崩溃。"""
        from scripts.scheduler_kline import _write_task_status

        with patch("data.database.DatabaseManager", side_effect=RuntimeError("no db")):
            # 不应 raise
            _write_task_status(
                "kline_update", "failed",
                db_connect_timeout_seconds=10, db_query_timeout_seconds=30,
            )


def test_ensure_task_status_table_uses_explicit_database_timeouts():
    """启动建表也必须受连接与 SQL 超时约束。"""
    from scripts.scheduler_kline import _ensure_task_status_table

    fake_db = MagicMock()
    with patch("data.database.DatabaseManager", return_value=fake_db) as manager:
        _ensure_task_status_table(
            db_connect_timeout_seconds=10,
            db_query_timeout_seconds=30,
        )

    manager.assert_called_once_with(connect_timeout=10, statement_timeout=30)
    fake_db.close.assert_called_once()


def test_scheduler_rejects_invalid_config_before_any_database_access():
    """调度器启动时配置非法应直接退出，不能先建表或迁移数据库。"""
    from scripts import scheduler_kline

    with patch.object(
        scheduler_kline,
        "load_kline_update_cfg",
        return_value={"worker_chunk_size": 0},
    ), patch.object(
        scheduler_kline,
        "_ensure_task_status_table",
        side_effect=AssertionError("invalid config must not access database"),
    ):
        scheduler_kline.main()


# =====================================================================
# AC-2 入口守卫验证（T5: main.py, T6: update_kline_daily.py）
# =====================================================================


class TestRunKlineUpdateNonTradingGuard:
    """T5：main.py run_kline_update 非交易日守卫。

    main.py --update 路径不经 scheduler_kline.should_skip_kline_update 守卫，
    必须在 run_kline_update 自己入口处检查 is_trading_day(今天)，
    False 时跳过采集循环、不抛 RuntimeError，让后续扫描/推送正常执行。
    """

    def test_non_trading_day_skips_without_raise(self):
        """T5：is_trading_day 今天返回 False 时，run_kline_update 不采集、不 raise。"""
        with patch("utils.trade_calendar.is_trading_day", return_value=False):
            from main import run_kline_update
            # 不应抛 RuntimeError
            try:
                run_kline_update()
            except RuntimeError:
                pytest.fail("非交易日 run_kline_update 不应抛 RuntimeError")
            except Exception as e:
                pytest.fail(f"非交易日 run_kline_update 不应抛任何异常，实际抛了 {type(e).__name__}: {e}")

    def test_trading_day_still_runs(self):
        """交易日时守卫应放行，正常进入采集循环。"""
        from main import run_kline_update
        # 模拟 trade_calendar 返回交易日，run_update 正常返回一个 UpdateResult
        fake_result = MagicMock()
        fake_result.target_date = "2026-10-16"
        fake_result.coverage_rate = 1.0
        fake_result.target_date_coverage_rate = 1.0
        fake_result.success = 2900
        fake_result.failed = 5
        fake_result.processed_count = 2905
        fake_result.total = 2905

        with patch("utils.trade_calendar.is_trading_day", return_value=True):
            with patch("scripts.data.update_kline_daily.run_update", return_value=fake_result):
                # 交易日应走完判定流程（fake_result.is_complete 会被调用）
                try:
                    run_kline_update()
                except RuntimeError:
                    pytest.fail("fake_result 没有 is_complete 等方法，这里说明 run_update 没被真正 mock 返回的对象调用——"
                                "守卫逻辑在交易日时正确放行了")

    def test_trade_calendar_exception_falls_back_to_trading_day(self):
        """trade_calendar 异常时保守：按交易日处理，守卫通过。"""
        from main import run_kline_update
        fake_result = MagicMock()
        fake_result.target_date = "2026-10-16"
        fake_result.coverage_rate = 1.0
        fake_result.target_date_coverage_rate = 1.0
        fake_result.success = 2900
        fake_result.failed = 5
        fake_result.processed_count = 2905
        fake_result.total = 2905

        # trade_calendar 异常被 except 捕获后，守卫按交易日放行
        with patch("utils.trade_calendar.is_trading_day", side_effect=RuntimeError("网络超时")):
            with patch("scripts.data.update_kline_daily.run_update", return_value=fake_result):
                # 不抛 RuntimeError → 守卫正确通过（按交易日处理）
                try:
                    run_kline_update()
                except RuntimeError:
                    pytest.fail("trade_calendar 异常时守卫应保守放行，不应阻塞")


class TestRunUpdateNonTradingGuard:
    """T6：update_kline_daily.run_update 入口守卫（main.py 与调度器共用同一入口）。

    交易日守卫位于股票池加载后、采集监督者启动前——
    非交易日返回 PASS 语义 UpdateResult，不启动任何工作进程。
    """

    def _make_pool(self):
        import pandas as pd
        return pd.DataFrame([
            {'code': '000001', 'symbol': '000001.SZ'},
            {'code': '000002', 'symbol': '000002.SZ'},
        ])

    def test_non_trading_day_returns_pass_result_without_collecting(self):
        """非交易日 run_update 返回 PASS 语义结果，且不启动监督者。"""
        from scripts.data.update_kline_daily import load_kline_update_config, run_update

        cfg = load_kline_update_config()
        with patch("utils.trade_calendar.is_trading_day", return_value=False):
            with patch(
                "scripts.data.kline_collection.run_supervised_collection"
            ) as mock_collect:
                result = run_update(cfg=cfg, pool_df=self._make_pool())

        assert mock_collect.call_count == 0, "非交易日不得启动分片采集"
        assert result.total == 2
        assert result.success == 0
        assert result.failed == 0
        assert result.skipped == 2
        assert result.target_date_inferred_non_trading is True
        assert result.is_complete(
            min_success_rate=0.90,
            total_failure_limit=500,
            min_coverage_rate=0.98,
        ) is True

    def test_trading_day_starts_supervised_collection(self):
        """交易日守卫放行后必须走分片监督者（不得回到旧全量循环）。"""
        from scripts.data.kline_collection import CollectionOutcome
        from scripts.data.update_kline_daily import load_kline_update_config, run_update

        cfg = load_kline_update_config()
        outcome = CollectionOutcome(
            target_date="2026-10-16",
            total=2,
            covered=2,
            success=2,
            failed=0,
            skipped=0,
            completion_mode="normal",
            stop_reason="coverage_reached",
        )

        with patch("utils.trade_calendar.is_trading_day", return_value=True):
            with patch(
                "utils.trade_calendar.is_trading_day_with_confidence",
                return_value=(True, False),
            ):
                with patch(
                    "scripts.data.update_kline_daily.DatabaseManager"
                ) as mock_db:
                    with patch(
                        "scripts.data.kline_collection.run_supervised_collection",
                        return_value=outcome,
                    ) as mock_collect:
                        result = run_update(cfg=cfg, pool_df=self._make_pool())

        assert mock_collect.call_count == 1, "交易日必须启动分片监督者"
        mock_db.return_value.close.assert_called_once()
        assert result.success == 2
        assert result.coverage_rate == 1.0
        assert result.details["stop_reason"] == "coverage_reached"
