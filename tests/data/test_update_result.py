"""
UpdateResult 完整性判定单元测试（R-01 + Task 1）。

自包含、不依赖数据库，仅测试 UpdateResult 的字段、属性和 is_complete 逻辑。
"""
from unittest.mock import patch

from scripts.data.update_kline_daily import UpdateResult


class TestCoverageAndCompleteness:
    """处理覆盖率和目标交易日覆盖率双校验。"""

    # ============ Task 1 Step 1：复现 10 月 16 日熔断问题 ============

    def test_breaker_leaves_most_stocks_unprocessed_and_must_fail(self):
        """熔断后只处理了 1061/2911，覆盖率不足必须 FAIL。"""
        result = UpdateResult(success=1059, failed=2, skipped=0, total=2911)

        assert result.processed_count == 1061
        assert result.coverage_rate == 1061 / 2911
        assert result.is_complete(
            min_success_rate=0.90,
            total_failure_limit=500,
            min_coverage_rate=0.98,
        ) is False

    def test_nearly_complete_market_passes(self):
        """接近全市场处理完成时应 PASS。"""
        result = UpdateResult(success=2909, failed=1, skipped=1, total=2911)

        assert result.is_complete(
            min_success_rate=0.90,
            total_failure_limit=500,
            min_coverage_rate=0.98,
        ) is True

    # ============ 9.2 节 B-2 强制补充：目标交易日覆盖率纳入阻断 ============

    def test_trading_day_all_skipped_with_low_target_coverage_must_fail(self):
        """交易日 target_date 但目标日覆盖极低，即使全部 skipped 也必须 FAIL。

        场景：今天非交易日（skipped_ratio=1.0），但 target_date 是交易日且
        target_date_success 很低（数据库中 target_date 当天数据严重不足）。
        此时第 2 条判定必须在第 3 条 skipped_ratio guard 之前拦截。
        """
        result = UpdateResult(
            success=0,
            failed=0,
            skipped=2911,
            total=2911,
            target_date="2026-10-17",
            target_date_success=100,  # 仅 3.4% 覆盖
        )

        # 模拟 target_date 为交易日
        with patch("utils.trade_calendar.is_trading_day", return_value=True):
            assert result.is_complete(
                min_success_rate=0.90,
                total_failure_limit=500,
                min_coverage_rate=0.98,
                target_date="2026-10-17",
            ) is False

    def test_non_trading_day_all_skipped_must_pass(self):
        """非交易日 target_date + 全部 skipped 应 PASS（休市日 guard）。

        场景：target_date 是非交易日（如周日），全部股票被跳过，
        此时 target_date_coverage_rate 判定被跳过，直接走 skipped_ratio guard。
        """
        result = UpdateResult(
            success=0,
            failed=0,
            skipped=2911,
            total=2911,
            target_date="2026-10-18",
            target_date_success=100,  # 覆盖低也无所谓
        )

        # 模拟 target_date 为非交易日
        with patch("utils.trade_calendar.is_trading_day", return_value=False):
            assert result.is_complete(
                min_success_rate=0.90,
                total_failure_limit=500,
                min_coverage_rate=0.98,
                target_date="2026-10-18",
            ) is True

    # ============ 额外边界测试 ============

    def test_zero_total_returns_zero_coverage(self):
        """total <= 0 时 coverage_rate 和 target_date_coverage_rate 应返回 0.0。"""
        result = UpdateResult(success=0, failed=0, skipped=0, total=0)
        assert result.coverage_rate == 0.0
        assert result.target_date_coverage_rate == 0.0

    def test_zero_total_must_fail(self):
        """total <= 0 时 is_complete 必须直接返回 False。"""
        result = UpdateResult(success=0, failed=0, skipped=0, total=0)
        assert result.is_complete(0.90, 500, 0.98) is False

    def test_target_date_none_skips_target_date_check(self):
        """target_date=None 时必须跳过第 2 条判定（禁止隐式取当天）。

        即使 success_rate 很低，也不应因为 target_date_coverage_rate 而被第 2 条拦截。
        """
        result = UpdateResult(
            success=2900,
            failed=10,  # success_rate = 2900/2910 ≈ 0.9966
            skipped=1,
            total=2911,
            target_date_success=50,  # 覆盖很低，但 target_date=None 时此值被忽略
        )
        # 不传 target_date → 第 2 条跳过，coverage_rate 足够高 → PASS
        assert result.is_complete(
            min_success_rate=0.90,
            total_failure_limit=500,
            min_coverage_rate=0.98,
            target_date=None,
        ) is True