"""
R-02 同源修复单测：utils.trade_calendar.nearest_trading_date（唯一日期语义实现）

覆盖：
  - inclusive=True  / inclusive=False 两种语义
  - 周末、连续长假、回溯窗口内无交易日（fail-closed）
  - 降级标志上报
  - 同源不变量：OBPC 早晨用的日期 == scheduler_kline 补全侧 compute_backfill_target_date 的日期

全部为纯函数级测试：monkeypatch 掉 ``utils.trade_calendar.is_trading_day_with_confidence``，
用构造的假日历驱动，禁止真连 Baostock / DB（也不触碰进程级 _cache，因为被替换后不进入 _resolve_with_confidence）。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import utils.trade_calendar as tc

TZ = ZoneInfo("Asia/Shanghai")


# =====================================================================
# 测试夹具：用集合构造假日历
# =====================================================================


def _patch_calendar(monkeypatch, trading_dates, degraded_dates=frozenset()):
    """把 is_trading_day_with_confidence 替换为按集合判定的假日历。

    trading_dates: 视为交易日的日期集合（"YYYY-MM-DD" 字符串）
    degraded_dates: 在这些日期上标记 degraded=True（模拟 weekday 降级）
    """

    def _fake(date=None):
        if date is None:
            ds = datetime.now().strftime("%Y-%m-%d")
        elif isinstance(date, datetime):
            ds = date.strftime("%Y-%m-%d")
        else:
            ds = str(date)[:10]
        return ds in trading_dates, ds in degraded_dates

    monkeypatch.setattr(tc, "is_trading_day_with_confidence", _fake)


def _weekdays(start: str, end: str) -> set:
    """返回 [start, end] 闭区间内所有周一~周五的日期字符串（忽略法定节假日）。"""
    d = datetime.strptime(start, "%Y-%m-%d").date()
    end_d = datetime.strptime(end, "%Y-%m-%d").date()
    out = set()
    while d <= end_d:
        if d.weekday() < 5:
            out.add(d.strftime("%Y-%m-%d"))
        d += timedelta(days=1)
    return out


# =====================================================================
# inclusive=True：最近一个 ≤ date 的交易日
# =====================================================================


def test_inclusive_true_on_trading_day_returns_same_day(monkeypatch):
    """当天是交易日：inclusive=True 应返回当天（收盘后时段语义）。"""
    _patch_calendar(monkeypatch, {"2026-10-19"})  # 周一
    now = datetime(2026, 10, 19, 22, 0, tzinfo=TZ)
    date_str, degraded = tc.nearest_trading_date(now, inclusive=True)
    assert date_str == "2026-10-19"
    assert degraded is False


def test_inclusive_true_on_non_trading_day_returns_prev(monkeypatch):
    """当天非交易日（周六）：inclusive=True 应回退到前一个交易日（周五）。"""
    _patch_calendar(monkeypatch, {"2026-10-16"})  # 周五
    date_str, degraded = tc.nearest_trading_date("2026-10-17", inclusive=True)  # 周六
    assert date_str == "2026-10-16"
    assert degraded is False


# =====================================================================
# inclusive=False：严格早于 date 的最近交易日（最近一个已收盘交易日）
# =====================================================================


def test_inclusive_false_on_trading_day_returns_previous(monkeypatch):
    """当天是交易日（周二）：inclusive=False 必须严格早于当天 → 周一。"""
    _patch_calendar(monkeypatch, {"2026-10-20", "2026-10-19"})  # 周二、周一
    date_str, degraded = tc.nearest_trading_date("2026-10-20", inclusive=False)
    assert date_str == "2026-10-19"
    assert date_str != "2026-10-20"
    assert degraded is False


def test_inclusive_false_on_saturday_returns_friday(monkeypatch):
    """周六：inclusive=False 回退到周五。"""
    _patch_calendar(monkeypatch, {"2026-10-16"})  # 周五
    date_str, _ = tc.nearest_trading_date("2026-10-17", inclusive=False)  # 周六
    assert date_str == "2026-10-16"


def test_inclusive_false_on_sunday_returns_friday(monkeypatch):
    """周日：inclusive=False 回退到周五。"""
    _patch_calendar(monkeypatch, {"2026-10-16"})  # 周五
    date_str, _ = tc.nearest_trading_date("2026-10-18", inclusive=False)  # 周日
    assert date_str == "2026-10-16"


def test_inclusive_false_across_long_holiday(monkeypatch):
    """连续长假：日历里假期前最后交易日为 2026-04-30，假期后 inclusive=False 应回退到它。

    （该场景回退 > 7 天，超出 kline 侧 _compute_target_date 的 7 天窗口，
      但仍在 trade_calendar 的 MAX_LOOKBACK_DAYS=30 内。）
    """
    _patch_calendar(monkeypatch, {"2026-04-30"})  # 仅假期前最后交易日
    date_str, degraded = tc.nearest_trading_date("2026-05-11", inclusive=False)
    assert date_str == "2026-04-30"
    assert degraded is False


# =====================================================================
# fail-closed：回溯窗口内无交易日
# =====================================================================


def test_no_trading_day_in_window_returns_none(monkeypatch):
    """整个搜索窗口内无交易日：必须返回 (None, ...)，调用方据此 fail-closed。"""
    _patch_calendar(monkeypatch, set())  # 全为非交易日
    date_str, degraded = tc.nearest_trading_date(
        "2026-10-19", inclusive=False, max_lookback_days=5
    )
    assert date_str is None
    assert degraded is False


def test_no_trading_day_in_window_with_degraded_returns_none(monkeypatch):
    """窗口内无交易日且全程走降级：返回 (None, True)，既不猜日期也不隐藏降级。"""
    _patch_calendar(
        monkeypatch,
        set(),
        degraded_dates={"2026-10-19", "2026-10-18", "2026-10-17"},
    )
    date_str, degraded = tc.nearest_trading_date(
        "2026-10-19", inclusive=False, max_lookback_days=3
    )
    assert date_str is None
    assert degraded is True


# =====================================================================
# 降级标志：途中任一被查日期走降级 → degraded=True
# =====================================================================


def test_degraded_flag_true_when_path_date_degraded(monkeypatch):
    """命中交易日之前，途中查到的非交易日走了降级 → degraded=True（可信度不足要上报）。"""
    # 起点前一日（周日 Oct 18）与更早的周六 Oct 17 标记降级，周五 Oct 16 为交易日（未降级）
    _patch_calendar(
        monkeypatch,
        {"2026-10-16"},
        degraded_dates={"2026-10-18", "2026-10-17"},
    )
    date_str, degraded = tc.nearest_trading_date("2026-10-19", inclusive=False)  # 周一
    assert date_str == "2026-10-16"
    assert degraded is True


def test_degraded_flag_true_when_hit_date_degraded(monkeypatch):
    """命中的交易日本身走了降级 → degraded=True。"""
    _patch_calendar(
        monkeypatch,
        {"2026-10-16"},
        degraded_dates={"2026-10-16"},
    )
    date_str, degraded = tc.nearest_trading_date("2026-10-17", inclusive=False)
    assert date_str == "2026-10-16"
    assert degraded is True


# =====================================================================
# 同源不变量：OBPC 早晨用的日期 == scheduler_kline 补全侧 compute_backfill_target_date
# =====================================================================


def test_obpc_morning_date_equals_kline_backfill_target(monkeypatch):
    """同一份假日历下，OBPC 07:30/08:10 用的日期 == kline 07:00 补全 target_date。

    这是 R-02 同源不变量的核心：两侧必须命中同一交易日，否则 task_status_allows_run
    的「target_date 完全相等」校验会 fail-closed 阻断扫描。
    """
    import scripts.scheduler_kline as sk

    # 用统一的周一~周五工作日日历驱动两侧
    _patch_calendar(monkeypatch, _weekdays("2026-09-01", "2026-10-31"))

    # 周一早上：两侧都应得到上周五
    monday = datetime(2026, 10, 19, 7, 0, tzinfo=TZ)
    obpc_date, _ = tc.nearest_trading_date(monday, inclusive=False)
    kline_date, _ = sk.compute_backfill_target_date(monday)
    assert obpc_date == kline_date == "2026-10-16"

    # 周六早上：两侧都应得到周五
    saturday = datetime(2026, 10, 17, 7, 0, tzinfo=TZ)
    obpc_date2, _ = tc.nearest_trading_date(saturday, inclusive=False)
    kline_date2, _ = sk.compute_backfill_target_date(saturday)
    assert obpc_date2 == kline_date2 == "2026-10-16"
