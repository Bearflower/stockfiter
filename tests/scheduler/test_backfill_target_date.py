"""
R-02 修复单测：compute_backfill_target_date（07:00 历史补全的目标交易日语义）

缺陷背景（R-02）：
  07:00 补全分支原先用 ``compute_target_date_for(now)``（语义 = 最近一个 ≤ today 的交易日），
  交易日早上会把 ``task_status`` 的 backfill target_date 写成「当天」；
  而 OBPC 07:30 侧用「指数表最新日期」取信号日期（通常是前一交易日）。
  OBPC 的 ``task_status_allows_run`` 要求 target_date 完全相等，两者错配 →
  07:30 等待 1800s 超时 → ``backfill_status`` 被强制成 "not_ready" → 跳过重扫。

修复：
  01) 语义修正：``compute_backfill_target_date`` = 「最近一个已收盘交易日」（严格早于 today）。
  02) 来源统一（根治错配）：直接委托 ``utils.trade_calendar.nearest_trading_date(now, inclusive=False)``，
      与 OBPC 07:30 重扫 / 08:10 推送取用的日期是**同一实现、同一回溯窗口**。
      原先若复用 ``compute_target_date_for(now - 1 天)``，其内部 ``_compute_target_date``
      的 max_backtrack=7 与 trade_calendar 的 30 天窗口不一致，长假期（>7 天不交易）会分叉。

本文件只 monkeypatch ``scripts.scheduler_kline.nearest_trading_date`` 这一个纯函数接缝，
  真实的下游 ``is_trading_day`` 在函数内部，被替换后不会真正执行 —— 因此不连 DB、不连 Baostock。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import scripts.scheduler_kline as sk
import scripts.scheduler_obpc as so

TZ = ZoneInfo("Asia/Shanghai")


def _prev_trading_day(d) -> "datetime.date":
    """朴素等价函数：返回严格早于 d 的最近工作日（仅测试用，不含法定节假日）。

    用于断言「交易日早上的补全目标 == 前一交易日」这一不变量。
    """
    x = d - timedelta(days=1)
    while x.weekday() >= 5:
        x -= timedelta(days=1)
    return x


# =====================================================================
# 用例 1：周一 07:00 → 目标为上周五（严格早于 today）
# =====================================================================


def test_monday_0700_targets_last_friday(monkeypatch):
    """周一 07:00：应以 inclusive=False 委托，拿到最近已收盘交易日 = 上周五，
    而不是把 target_date 写成周一当天。"""
    now = datetime(2026, 10, 19, 7, 0, tzinfo=TZ)  # 2026-10-19 周一
    calls: list[tuple] = []

    def _fake_nearest(date=None, inclusive=True, max_lookback_days=None):
        calls.append((date, inclusive))
        return "2026-10-16", False  # 上周五

    monkeypatch.setattr(sk, "nearest_trading_date", _fake_nearest)

    target_date, degraded = sk.compute_backfill_target_date(now)

    # 返回值被原样透传
    assert target_date == "2026-10-16"
    assert degraded is False
    # 关键断言：起点日就是触发时刻，且必须 inclusive=False（严格早于 today）
    assert len(calls) == 1
    assert calls[0][0] == now
    assert calls[0][1] is False


# =====================================================================
# 用例 2：周六 07:00 → 目标是周五
# =====================================================================


def test_saturday_0700_targets_friday(monkeypatch):
    """周六 07:00：目标就是周五（与重扫/推送信号日期一致）。"""
    now = datetime(2026, 10, 17, 7, 0, tzinfo=TZ)  # 2026-10-17 周六
    calls: list[tuple] = []

    def _fake_nearest(date=None, inclusive=True, max_lookback_days=None):
        calls.append((date, inclusive))
        return "2026-10-16", False  # 周五

    monkeypatch.setattr(sk, "nearest_trading_date", _fake_nearest)

    target_date, degraded = sk.compute_backfill_target_date(now)

    assert target_date == "2026-10-16"
    assert degraded is False
    assert len(calls) == 1
    assert calls[0][0] == now
    assert calls[0][1] is False


# =====================================================================
# 用例 3：与 OBPC 侧的同源不变式（R-02 修复的核心目的）
# =====================================================================


def test_kline_and_obpc_share_same_date_implementation():
    """同源不变式：两个调度器必须引用同一个日期语义实现对象。

    修复前 kline 侧走 DailyKlineUpdater._compute_target_date（max_backtrack=7），
    OBPC 侧读指数表最新日期——两处不同来源必然分叉；
    修复后两侧都委托 utils.trade_calendar.nearest_trading_date。
    这里直接断言「同一个函数对象」，任何一侧改回独立实现都会立刻失败。
    """
    assert so.nearest_trading_date is sk.nearest_trading_date


def test_backfill_target_equals_prev_trading_day(monkeypatch):
    """行为级不变量：交易日早上 07:00 补全目标 == 前一交易日（OBPC 早晨同样取该日）。"""
    now = datetime(2026, 10, 19, 7, 0, tzinfo=TZ)  # 周一交易日早上
    expected = _prev_trading_day(now.date())  # 周一 → 上周五
    captured: dict = {}

    def _fake_nearest(date=None, inclusive=True, max_lookback_days=None):
        captured["date"] = date
        captured["inclusive"] = inclusive
        return expected.strftime("%Y-%m-%d"), False

    monkeypatch.setattr(sk, "nearest_trading_date", _fake_nearest)

    target_date, _ = sk.compute_backfill_target_date(now)

    assert target_date == expected.strftime("%Y-%m-%d")
    # 语义必须是「严格早于起点日」，否则会退回「写成当天」的原缺陷
    assert captured["inclusive"] is False
    assert captured["date"] == now
    # 明确：目标日绝不等于 today
    assert target_date != now.strftime("%Y-%m-%d")


# =====================================================================
# 用例 4：降级标志与 None 透传
# =====================================================================


def test_degraded_flag_passthrough(monkeypatch):
    """降级标志透传：nearest_trading_date 返回的 is_degraded 必须原样返回给调用方，
    供 07:00 分支日志标注「weekday 降级 / Baostock 已验证」。"""
    now = datetime(2026, 10, 19, 7, 0, tzinfo=TZ)
    monkeypatch.setattr(
        sk, "nearest_trading_date", lambda date=None, inclusive=True, max_lookback_days=None: ("2026-10-16", True)
    )

    target_date, degraded = sk.compute_backfill_target_date(now)

    assert target_date == "2026-10-16"
    assert degraded is True


def test_none_target_passthrough(monkeypatch):
    """回溯窗口内无交易日时，target_date 为 None 且同样被原样透传（由调用方按 guard 语义处理）。"""
    now = datetime(2026, 10, 19, 7, 0, tzinfo=TZ)
    monkeypatch.setattr(
        sk, "nearest_trading_date", lambda date=None, inclusive=True, max_lookback_days=None: (None, True)
    )

    target_date, degraded = sk.compute_backfill_target_date(now)

    assert target_date is None
    assert degraded is True
