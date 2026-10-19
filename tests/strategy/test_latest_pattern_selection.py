"""
Task 5 + Task 9.4 N-2：最新形态选择 & N-2 窗口回退测试

覆盖：
1. ``analyze()`` 应返回**最新**完整形态（反向遍历 drop 窗口，避免旧形态遮蔽新形态）
2. ``analyze_candidates()`` 返回全部完整形态，按回踩日新→旧排序
3. N-2 窗口回退：当最新候选落在信号窗口外时，analyze_candidates 可用于选出窗口内候选

同时覆盖：config.yaml 的 index_filter.method 应为 'ma_position'（已在 tests/risk 单独覆盖）。
"""

from datetime import datetime, timedelta
from typing import Dict, List, Optional

import pandas as pd
import pytest

from strategy.oversold_bounce.strategy import OversoldBounceStrategy


# ---------------------------------------------------------------------------
# 策略 fixture（使用默认参数，避免依赖 config.yaml 漂移）
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def strategy() -> OversoldBounceStrategy:
    """构造使用默认参数的 OBPC 策略实例"""
    return OversoldBounceStrategy()


# ---------------------------------------------------------------------------
# 测试数据构造：包含两个独立完整形态的 K 线 DataFrame
# ---------------------------------------------------------------------------


def _build_two_pattern_df() -> pd.DataFrame:
    """
    构造一个包含两个完整 OBPC 形态的日线 DataFrame。

    数据布局（共 130 行，index 0 ~ 129）：
        - 旧形态：drop 窗口 5~25（高点 index5=12.0 → 低点 index25=10.0，跌幅16.7%）
            放量日 index35，回踩确认日 index42，date 列值 "2026-09-01"
        - 新形态：drop 窗口 75~95（高点 index75=14.0 → 低点 index95=11.5，跌幅17.9%）
            放量日 index105，回踩确认日 index112，date 列值 "2026-10-16"

    两个形态之间有足够间隔（index 25~75 约 50 行无完整形态），
    确保策略能分别检测到它们。
    """
    n = 130
    # 日期列必须单调非递减：analyze()/analyze_candidates() 会执行
    # sort_values('date').reset_index(drop=True)，若日期乱序会导致行位置被重排，
    # 使 fixture 中预设的回踩索引（42 / 112）失效。
    #
    # 锚点要求：index 42 → 2026-09-01（旧形态回踩日），
    #           index 112 → 2026-10-16（新形态回踩日）。
    # 42→112 共 70 行、跨度 45 天，无法用每日递增表达，
    # 故 43~112 段用线性映射（允许重复日期，仅需非递减），113 之后恢复每日递增。
    base_date = datetime(2026, 7, 21)          # base + 42 天 = 2026-09-01
    mid_span_days = 45                         # 42 → 112 的日期跨度（2026-09-01 → 2026-10-16）
    dates = []
    for i in range(n):
        if i <= 42:
            offset_days = i
        elif i <= 112:
            offset_days = 42 + round((i - 42) * mid_span_days / 70)
        else:
            offset_days = 42 + mid_span_days + (i - 112)
        dates.append((base_date + timedelta(days=offset_days)).strftime("%Y-%m-%d"))

    # 初始化价格序列：从 11.0 开始，中间被两段"大涨→大跌"打断
    close = [11.0] * n
    volume = [100_000_000] * n   # 基础成交量 1亿，流动性充足
    amount = [1_100_000_000] * n  # 基础成交额 11亿 >> 30M，流动性检查必过
    high = [11.0] * n
    low = [11.0] * n

    # ============ 旧形态 ============
    # drop 窗口 index 5~25：高点 index5=12.0，低点 index25=10.0
    close[5] = 12.0; high[5] = 12.2; low[5] = 11.8
    for i in range(6, 25):
        close[i] = 12.0 - (i - 5) * 0.1  # 逐步下跌
        high[i] = close[i] + 0.1
        low[i] = close[i] - 0.1
    close[25] = 10.0; high[25] = 10.2; low[25] = 9.9

    # drop 后缩量区间 index 26~34：index 28 成交量极小
    for i in range(26, 35):
        close[i] = 10.0
        high[i] = 10.1
        low[i] = 9.9
        volume[i] = 80_000_000  # 比基准小
        amount[i] = 800_000_000
    # 缩量日 index 28：成交量 ≈ 前20日均量的 30%（远小于 volume_shrink_ratio=0.5 的阈值）
    volume[28] = 30_000_000
    amount[28] = 300_000_000

    # 放量日 index 35：volume 暴涨 4×，close 从 10.2 → 10.8（涨幅 ~6%）
    volume[35] = 400_000_000  # 基础 1亿的 4倍 → volume_ratio ≈ 4.0 > min_volume_ratio=1.5 ✓
    close[35] = 10.81          # (10.81 - 10.2) / 10.2 ≈ 6% ✓
    high[35] = 10.95
    low[35] = 10.3
    amount[35] = 4_324_000_000  # 10.81 × 4亿 ≈ 43亿

    # flat_days=1：index 36 走平缩量
    close[36] = 10.80
    high[36] = 10.90
    low[36] = 10.70
    volume[36] = 300_000_000   # 放量日 4亿的 75% < flat_volume_threshold * 4亿 ≈ 3.4亿 ✓
    amount[36] = 3_240_000_000

    # 回踩区间 index 37~47：在支撑位上方（支撑 = 10.0，10.0 × 0.985 = 9.85）
    for i in range(37, 48):
        close[i] = 10.5 - (i - 37) * 0.05
        high[i] = close[i] + 0.1
        low[i] = close[i] - 0.15
        volume[i] = 150_000_000
        amount[i] = 1_500_000_000
    # 最低点在 index 42，low = 9.90 > 9.85 ✓
    close[42] = 10.10; high[42] = 10.20; low[42] = 9.90

    # 旧形态回踩日 index 42 的 date 列已由上方日期构造锚定为 2026-09-01

    # ============ 中间盘整段（index 48~74） ============
    for i in range(48, 75):
        close[i] = 10.5 + (i - 48) * 0.05
        high[i] = close[i] + 0.15
        low[i] = close[i] - 0.10
        volume[i] = 100_000_000
        amount[i] = close[i] * 100_000_000

    # ============ 新形态 ============
    # drop 窗口 index 75~95：高点 index75=14.0，低点 index95=11.5
    close[75] = 14.0; high[75] = 14.3; low[75] = 13.8
    for i in range(76, 95):
        close[i] = 14.0 - (i - 75) * 0.13  # 逐步下跌 ~25 天 × 0.13 ≈ 3.25 到 10.75，调整
        high[i] = close[i] + 0.1
        low[i] = close[i] - 0.1
    close[95] = 11.5; high[95] = 11.7; low[95] = 11.3

    # drop 后缩量区间 index 96~104：index 98 缩量
    for i in range(96, 105):
        close[i] = 11.5
        high[i] = 11.6
        low[i] = 11.4
        volume[i] = 80_000_000
        amount[i] = 920_000_000
    volume[98] = 30_000_000
    amount[98] = 345_000_000

    # 放量日 index 105
    volume[105] = 420_000_000
    close[105] = 12.20
    high[105] = 12.40
    low[105] = 11.80
    amount[105] = 5_124_000_000

    # flat_days=1：index 106
    close[106] = 12.15
    high[106] = 12.25
    low[106] = 12.05
    volume[106] = 350_000_000
    amount[106] = 4_252_500_000

    # 回踩区间 index 107~117
    for i in range(107, 118):
        close[i] = 11.8 - (i - 107) * 0.04
        high[i] = close[i] + 0.08
        low[i] = close[i] - 0.12
        volume[i] = 150_000_000
        amount[i] = close[i] * 150_000_000
    # 最低点 index 112，low = 11.34 > 支撑位 11.5 × 0.985 = 11.3275 ✓
    close[112] = 11.52; high[112] = 11.62; low[112] = 11.34

    # 新形态回踩日 index 112 的 date 列已由上方日期构造锚定为 2026-10-16

    # ============ 尾部 ============
    for i in range(118, n):
        close[i] = 11.6 + (i - 118) * 0.02
        high[i] = close[i] + 0.1
        low[i] = close[i] - 0.05
        volume[i] = 100_000_000
        amount[i] = close[i] * 100_000_000

    df = pd.DataFrame({
        "date": dates,
        "open": close,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
        "amount": amount,
    })
    return df


# ---------------------------------------------------------------------------
# Task 5 核心测试：analyze 返回最新完整形态
# ---------------------------------------------------------------------------


class TestAnalyzeLatestPattern:
    """验证 analyze() 选择**最新**完整形态（L7 正向→反向遍历修复）"""

    def test_analyze_returns_most_recent_complete_pattern(self, strategy):
        """旧形态（2026-09-01）和新形态（2026-10-16）并存时，analyze 必须返回新形态"""
        df = _build_two_pattern_df()
        signal = strategy.analyze("TEST", {"d": df})

        assert signal is not None, "策略应检测到至少一个完整形态"
        retrace_date = str(signal.detail["retrace_date"])
        # 取 YYYY-MM-DD 前缀比较
        assert retrace_date[:10] == "2026-10-16", (
            f"预期最新形态回踩日 2026-10-16，实际 {retrace_date}"
        )

    def test_analyze_candidates_lists_both_patterns_newest_first(self, strategy):
        """analyze_candidates 应返回两个完整形态，按回踩日新→旧排序"""
        df = _build_two_pattern_df()
        candidates = strategy.analyze_candidates("TEST", {"d": df})

        assert len(candidates) >= 2, (
            f"analyze_candidates 应返回 ≥2 个候选，实际 {len(candidates)}"
        )

        retrace_dates = [str(c.detail["retrace_date"])[:10] for c in candidates]
        # 新→旧排序
        assert retrace_dates[0] == "2026-10-16", f"第一个候选应为最新形态，实际 {retrace_dates[0]}"
        assert "2026-09-01" in retrace_dates, f"应包含旧形态 2026-09-01，实际 {retrace_dates}"

        # 严格降序
        for i in range(len(retrace_dates) - 1):
            assert retrace_dates[i] >= retrace_dates[i + 1], (
                f"候选未按回踩日降序排列：{retrace_dates}"
            )

    def test_analyze_candidates_max_limit_respected(self, strategy):
        """max_candidates 参数应限制返回数量"""
        df = _build_two_pattern_df()
        candidates = strategy.analyze_candidates("TEST", {"d": df}, max_candidates=1)

        assert len(candidates) == 1, (
            f"max_candidates=1 应返回恰好 1 个，实际 {len(candidates)}"
        )
        # 返回的应该是最新那个
        assert str(candidates[0].detail["retrace_date"])[:10] == "2026-10-16"

    def test_analyze_equals_first_candidate(self, strategy):
        """analyze() 应等价于 analyze_candidates(max_candidates=1) 的第一个"""
        df = _build_two_pattern_df()
        signal_single = strategy.analyze("TEST", {"d": df})
        candidates = strategy.analyze_candidates("TEST", {"d": df}, max_candidates=1)

        assert signal_single is not None
        assert len(candidates) == 1
        assert str(signal_single.detail["retrace_date"])[:10] == \
               str(candidates[0].detail["retrace_date"])[:10]


# ---------------------------------------------------------------------------
# Task 9.4 N-2：窗口回退测试
# ---------------------------------------------------------------------------


class TestN2WindowFallback:
    """
    当最新候选落在外部信号窗口之外时，调用方可从 analyze_candidates 中
    继续向更早候选评估，直到命中窗口内候选或耗尽。

    本测试不直接模拟 daily_scan.py 的窗口逻辑（由它负责），
    只验证 analyze_candidates 提供了正确的新→旧候选序列供调用方使用。
    """

    def test_older_candidate_selected_when_newer_outside_window(self, strategy):
        """
        场景：外部信号窗口只接受 <= 2026-10-10 的回踩日。
        analyze_candidates 返回序列 [2026-10-16, 2026-09-01]，
        调用方跳过第一个（不在窗口内），选中第二个（在窗口内）。
        """
        df = _build_two_pattern_df()
        candidates = strategy.analyze_candidates("TEST", {"d": df})

        # 外部信号窗口（模拟）：回踩日 <= 2026-10-10
        signal_window_cutoff = "2026-10-10"

        # 调用方按新→旧遍历候选，选第一个落在窗口内的
        selected = None
        for c in candidates:
            retrace = str(c.detail["retrace_date"])[:10]
            if retrace <= signal_window_cutoff:
                selected = c
                break

        assert selected is not None, "窗口内应存在至少一个候选（2026-09-01）"
        assert str(selected.detail["retrace_date"])[:10] == "2026-09-01", (
            f"选中的应为旧形态（2026-09-01，落在窗口内），实际 "
            f"{str(selected.detail['retrace_date'])[:10]}"
        )

    def test_analyze_alone_would_return_outside_window_candidate(self, strategy):
        """
        反向验证：如果调用方只用 analyze()（返回最新形态），
        在上面那个窗口约束下会拿到 2026-10-16（落在窗口外），无法使用。
        这正是为什么需要 analyze_candidates + 调用方窗口回退逻辑。
        """
        df = _build_two_pattern_df()
        signal = strategy.analyze("TEST", {"d": df})
        signal_window_cutoff = "2026-10-10"

        retrace = str(signal.detail["retrace_date"])[:10]
        # analyze 返回最新的 2026-10-16，超出了 <= 2026-10-10 的窗口
        assert retrace > signal_window_cutoff, (
            "sanity：analyze 返回的最新形态应在窗口之外，"
            "确保窗口回退逻辑有意义"
        )
