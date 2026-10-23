"""
Task 4 / Task 3-4 集成测试：daily_scan 在分级市场风控下的行为

覆盖：
  - 强市正常通过（graded 模式）
  - 弱市高分 + 相对强度达标 → 弱市乘数进入
  - 弱市相对强度不足被过滤
  - 极弱市场禁止开仓（但仍统计 raw_candidate_count）
  - 弱市 daily_signal_limit 截断
  - hard_filter 模式零回归（JSON 结构含默认分级字段）

mock 范围：
  - DatabaseManager 的所有 DB 查询方法
  - OversoldBounceStrategy 的形态检测方法
  - scan_daily_signals 纯函数调用，不涉及数据库写入或文件 I/O
"""

import numpy as np
import pandas as pd
import pytest
from unittest import mock
from datetime import datetime

# 被测函数
from scripts.daily_scan import scan_daily_signals


# =====================================================================
# 辅助：构造 graded 模式 config dict（与实际 config.yaml 结构对齐）
# =====================================================================


def _make_graded_config(
    weak_score_threshold: float = 75,
    weak_daily_limit: int = 2,
    require_rs: bool = True,
    rs_lookback: int = 5,
    rs_min: float = 0.0,
) -> dict:
    """构造 graded 模式 scan_daily_signals 所需的完整 config dict。"""
    return {
        "global": {
            # 不提供 stock_list_file，让 scan_daily_signals 走 db 路径
        },
        "strategies": {
            "oversold_bounce": {
                "params": {
                    "market_regime_policy": {
                        "mode": "graded",
                        "base_position_pct": 0.10,
                        "weak": {
                            "allow_entry": True,
                            "position_multiplier": 0.5,
                            "score_threshold": weak_score_threshold,
                            "daily_signal_limit": weak_daily_limit,
                            "require_relative_strength": require_rs,
                            # 配置契约：RS 参数定义在 weak 子节下（与 config.yaml 一致）
                            "relative_strength_lookback": rs_lookback,
                            "relative_strength_min": rs_min,
                        },
                        "extreme_weak": {
                            "allow_entry": False,
                            "position_multiplier": 0.0,
                            "daily_signal_limit": 0,
                        },
                    },
                    # 其他必要 params（沿用现有默认值）
                    "stop_loss_ratio": 0.97,
                    "hard_stop_loss": 0.10,
                    "trailing_stop_ratio": 0.08,
                    "signal_cooldown_days": 60,
                    "max_signals_per_year": 2,
                    "kline_history_days": 120,
                    "min_kline_length": 30,
                    "max_signals_per_month": 12,
                    # 注意：不配置 score_adjustment，避免与 graded 互斥
                },
            },
        },
    }


def _make_hard_filter_config() -> dict:
    """构造 hard_filter 模式 config（不带 market_regime_policy 分节）。"""
    return {
        "global": {},
        "strategies": {
            "oversold_bounce": {
                "params": {
                    "stop_loss_ratio": 0.97,
                    "hard_stop_loss": 0.10,
                    "trailing_stop_ratio": 0.08,
                    "signal_cooldown_days": 60,
                    "max_signals_per_year": 2,
                    "kline_history_days": 120,
                    "min_kline_length": 30,
                    "max_signals_per_month": 12,
                    "score_filter": {
                        "enabled": True,
                        "score_threshold": 60,
                    },
                    # 不配置 market_regime_policy → hard_filter 兼容路径
                },
            },
        },
    }


# =====================================================================
# 辅助：构造 mock 返回数据
# =====================================================================


def _make_stock_df(n: int = 3) -> pd.DataFrame:
    """构造简单的股票列表 DataFrame（code + name）。"""
    data = {"code": [f"60000{i}" for i in range(1, n + 1)],
            "name": [f"测试股票{i}" for i in range(1, n + 1)]}
    return pd.DataFrame(data)


def _make_index_kline(
    days: int = 120,
    trend: str = "strong",  # strong / weak / extreme_weak
    signal_date: str = "2026-10-16",
) -> pd.DataFrame:
    """构造指数 K 线 DataFrame（close + date），供 RealtimeMarketContextProvider 使用。"""
    dates = pd.bdate_range(end=signal_date, periods=days)

    if trend == "strong":
        # 收盘价稳步上升 → 强市（close 上升 → MA20 上升 → MA20_slope > 0 → strong）
        closes = [3500 + i * 5 for i in range(days)]
    elif trend == "weak":
        # 弱市构造：close < MA20 但 close >= MA60，且 MA60_slope > 0
        # 判定逻辑：
        #   extreme_weak = close < MA60 且 MA60_slope <= 0
        #   strong = close >= MA20 且 MA20_slope > 0
        #   其余 = weak
        #
        # 前 70 天大涨（MA60 被拉高且保持上升），后 50 天缓跌
        # → 最后 close 在 MA20 之下但在 MA60 之上，MA60_slope > 0
        closes = [3000 + i * 10 for i in range(min(days, 70))]  # 涨
        if days > 70:
            # 缓跌段：从 3700 跌到 3700 - 2*(days-70)
            closes += [3700 - i * 2 for i in range(1, days - 70 + 1)]
    else:  # extreme_weak
        # 收盘价跌破 MA60 且 MA60_slope 向下
        closes = [3700 + i for i in range(days - 60)]  # 前面涨
        closes += [3700 - i * 3 for i in range(60)]   # 最近 60 天猛跌

    return pd.DataFrame({"date": dates, "close": closes})


def _make_stock_kline(
    n_stock: int = 120,
    performance: str = "strong",  # strong / weak（个股相对指数的表现）
    index_df: pd.DataFrame = None,
    signal_date: str = "2026-10-16",
) -> pd.DataFrame:
    """构造个股 K 线 DataFrame。

    performance:
      - "strong": 个股收盘价涨幅等于指数 → 相对强度 ≈ 0
      - "outperform": 个股涨得比指数多 → 相对强度 > 0
      - "underperform": 个股跌得比指数多 → 相对强度 < 0
      - "strong_when_index_weak": 个股逆势上涨（指数弱时个股强）
    """
    dates = pd.bdate_range(end=signal_date, periods=n_stock)

    # 从 index_df 里取 close 做参考
    if index_df is not None and len(index_df) >= n_stock:
        index_closes = index_df["close"].values[-n_stock:]
    else:
        index_closes = [100.0] * n_stock

    base = 10.0
    if performance == "strong":
        # 跟指数同步变化（首尾比值相同 → relative_strength 约 0）
        ratio = index_closes / index_closes[0]
        closes = base * ratio
    elif performance == "outperform":
        # 始终比指数好——如果指数涨就涨更多，指数跌就跌更少
        # 核心：stock 的总收益 = index 总收益 + 额外 5 个百分点
        index_first = index_closes[0]
        index_last = index_closes[-1]
        index_return = (index_last - index_first) / index_first
        stock_return = index_return + 0.05
        # 线性分布到每日，返回 numpy array 以便后续 pd.DataFrame 构造
        t = np.linspace(0, 1, len(index_closes))
        closes = base * (1 + stock_return * t)
    elif performance == "underperform":
        # 始终比指数差（总收益少 5 个百分点）
        index_first = index_closes[0]
        index_last = index_closes[-1]
        index_return = (index_last - index_first) / index_first
        stock_return = index_return - 0.05
        t = np.linspace(0, 1, len(index_closes))
        closes = base * (1 + stock_return * t)
    elif performance == "strong_when_index_weak":
        # 指数跌但个股涨（独立于指数的上升通道）
        closes = np.array([base + i * 0.05 for i in range(n_stock)])
    else:
        closes = np.array([base] * n_stock)

    return pd.DataFrame({
        "date": dates,
        "open": closes * 0.99,
        "high": closes * 1.02,
        "low": closes * 0.98,
        "close": closes,
        "volume": 1000000,
    })


class _SimpleCandidate:
    """最简形态候选对象——避免 MagicMock 干扰 dict.get() 等普通方法。"""
    pass


def _make_mock_candidate(
    score: float = 80.0,
    retrace_date_str: str = "2026-10-15",
) -> _SimpleCandidate:
    """构造一个最简形态候选对象（有 score 和 detail 属性）。

    detail 是普通 dict，确保 .get() 方法按预期工作，不受 MagicMock 干扰。
    """
    cand = _SimpleCandidate()
    cand.score = score
    cand.signal_date = "2026-10-16"
    cand.detail = {
        "retrace_date": retrace_date_str,
        "surge_date": "2026-10-10",
        "support_level": 10.50,
        "surge_pct": 0.15,
        "surge_volume_ratio": 2.5,
        "drop_rate": -0.03,
        "retrace_low": 10.20,
        "retrace_close": 10.80,
        "shrink_ratio": 0.85,
        "drop_start_date": "2026-10-11",
        "drop_end_date": "2026-10-15",
    }
    return cand


# =====================================================================
# 核心：scan_daily_signals 的 mock 框架
# =====================================================================


def _run_scan_with_mocks(
    config: dict,
    index_trend: str,
    stock_performances: list,  # 每只股票的表现标签
    candidate_scores: list,     # 每只股票的形态评分
    signal_date: str = "2026-10-16",
) -> dict:
    """
    带完整 mock 运行 scan_daily_signals，返回其 dict 结果。

    mock 策略：
      - DatabaseManager 的 get_index_kline / get_kline_history / get_stock_list /
        get_last_signal_date / get_signal_count_this_year / get_signal_count_this_month
      - OversoldBounceStrategy 的 analyze_candidates（返回预定义候选列表）
      - persist_scan_outcome / _write_signals_file 不动——scan_daily_signals
        自身不调用它们，由 main() 调用
    """
    n = len(stock_performances)
    stock_df = _make_stock_df(n)
    index_df = _make_index_kline(days=120, trend=index_trend, signal_date=signal_date)

    # 为每只股票预构造 kline_df 和 candidate
    stock_klines = [
        _make_stock_kline(
            n_stock=120,
            performance=perf,
            index_df=index_df,
            signal_date=signal_date,
        )
        for perf in stock_performances
    ]

    candidates = [
        [_make_mock_candidate(score=score)]
        for score in candidate_scores
    ]

    # mock DatabaseManager
    mock_db = mock.MagicMock()
    mock_db.get_stock_list.return_value = stock_df
    mock_db.get_index_kline.return_value = index_df
    mock_db.get_kline_history.side_effect = lambda code, days: (
        stock_klines[int(code[-1]) - 1] if code[-1].isdigit() else stock_klines[0]
    )
    mock_db.get_last_signal_date.return_value = None
    mock_db.get_signal_count_this_year.return_value = 0
    mock_db.get_signal_count_this_month.return_value = 0
    mock_db.save_scan_result.return_value = None
    mock_db.close.return_value = None
    mock_db.conn = mock.MagicMock()
    # _fetch_backfill_data_version 内部用 cur.execute + fetchone
    mock_cursor = mock.MagicMock()
    mock_cursor.fetchone.return_value = None
    mock_db.conn.cursor.return_value = mock_cursor

    # mock OversoldBounceStrategy
    mock_strategy_instance = mock.MagicMock()
    def _analyze_candidates(code, kline_dict):
        idx = int(code[-1]) - 1 if code[-1].isdigit() else 0
        return candidates[idx]
    mock_strategy_instance.analyze_candidates = _analyze_candidates

    with mock.patch("scripts.daily_scan.DatabaseManager", return_value=mock_db), \
         mock.patch("scripts.daily_scan.OversoldBounceStrategy", return_value=mock_strategy_instance):
        result = scan_daily_signals(
            config=config,
            output_dir="/tmp/signals_test",
            signal_date_override=signal_date,
            run_kind="initial",
        )
    return result


# =====================================================================
# 测试用例
# =====================================================================


class TestGradedScanIntegration:
    """graded 模式下 daily_scan 的分级行为测试。"""

    def test_strong_market_passes_with_full_position(self):
        """强市候选按正常建议仓位进入。"""
        result = _run_scan_with_mocks(
            config=_make_graded_config(
                weak_score_threshold=75,
                weak_daily_limit=5,  # 宽松，不截断
                require_rs=False,    # 强市不要求相对强度
            ),
            index_trend="strong",
            stock_performances=["strong"],
            candidate_scores=[80.0],
        )
        assert result["selected_count"] == 1
        assert result["blocked_market"] is False
        sig = result["signals"][0]
        assert sig["market_regime"] == "strong"
        assert sig["position_multiplier"] == 1.0
        assert sig["recommended_position_pct"] == 0.10
        assert sig["base_position_pct"] == 0.10

    def test_weak_market_with_relative_strength_passes(self):
        """弱市高分 + 相对强度达标 → 弱市乘数进入。"""
        result = _run_scan_with_mocks(
            config=_make_graded_config(
                weak_score_threshold=75,
                weak_daily_limit=5,
                require_rs=True,
                rs_lookback=3,
                rs_min=-1.0,  # 宽松阈值，任何正数都能过
            ),
            index_trend="weak",
            stock_performances=["outperform"],  # 跑赢指数
            candidate_scores=[80.0],
        )
        # 弱市 allow_entry=True 且 score=80 ≥ 75，RS 通过 → 进入
        assert result["selected_count"] == 1
        sig = result["signals"][0]
        assert sig["market_regime"] == "weak"
        assert sig["position_multiplier"] == 0.5
        assert sig["recommended_position_pct"] == 0.05
        assert sig["relative_strength"] is not None
        assert sig["relative_strength"] > 0  # outperform → 正

    def test_weak_market_without_relative_strength_filtered(self):
        """弱市相对强度不足 → 不进入正式信号。"""
        result = _run_scan_with_mocks(
            config=_make_graded_config(
                weak_score_threshold=75,
                weak_daily_limit=5,
                require_rs=True,
                rs_lookback=3,
                rs_min=0.5,  # 高阈值：只有大幅跑赢指数才能过
            ),
            index_trend="weak",
            stock_performances=["strong"],  # 和指数同步 → 相对强度 ≈ 0
            candidate_scores=[80.0],
        )
        # raw_candidate_count > 0 但 selected_count == 0
        assert result["raw_candidate_count"] == 1
        assert result["selected_count"] == 0
        assert result["rs_filtered_count"] == 1
        # relative_strength 不在 selected 里（被过滤了）

    def test_extreme_weak_blocks_all(self):
        """极弱市场 → 正式信号恒为 0，但原始候选仍可审计。"""
        result = _run_scan_with_mocks(
            config=_make_graded_config(
                weak_score_threshold=75,
                weak_daily_limit=5,
            ),
            index_trend="extreme_weak",
            stock_performances=["strong", "strong"],
            candidate_scores=[95.0, 90.0],  # 极高分也没用
        )
        assert result["raw_candidate_count"] == 2
        assert result["selected_count"] == 0
        # trend_filter_reason 应该包含分级裁决的 reason_code
        assert result["trend_filter_reason"] == "extreme_weak_market"

    def test_raw_candidate_details_written_to_data_metrics(self):
        """原始形态候选明细必须写入 data_metrics，供飞书推送展示股票代码。"""
        result = _run_scan_with_mocks(
            config=_make_graded_config(
                weak_score_threshold=75,
                weak_daily_limit=5,
            ),
            index_trend="extreme_weak",
            stock_performances=["strong", "strong"],
            candidate_scores=[95.0, 90.0],
        )

        raw_candidates = result["data_metrics"]["raw_candidates"]
        assert raw_candidates == [
            {"code": "600001", "name": "测试股票1"},
            {"code": "600002", "name": "测试股票2"},
        ]
        # 明细数量必须与 raw_candidate_count 闭环
        assert len(raw_candidates) == result["raw_candidate_count"]

    def test_weak_daily_limit_truncates(self):
        """弱市 daily_signal_limit=1 → 只保留最高分候选。"""
        result = _run_scan_with_mocks(
            config=_make_graded_config(
                weak_score_threshold=75,
                weak_daily_limit=1,  # 每天只允许 1 只
                require_rs=True,
                rs_lookback=3,
                rs_min=-1.0,  # 合法区间内最低阈值，保证全过（配置校验限制 [-1, 1]）
            ),
            index_trend="weak",
            stock_performances=["outperform", "outperform", "outperform"],
            candidate_scores=[85.0, 80.0, 78.0],
        )
        # 3 只全通过评分（都 ≥ 75）和 RS → 先排序后截断
        assert result["selected_count"] == 1
        # 保留的必须是最高分那只
        assert result["signals"][0]["score"] == 85.0
        # data_metrics 里有 daily_limit_truncated
        dm = result["data_metrics"]
        assert dm["graded_daily_limit_truncated"] >= 2  # 3→1，丢弃 2 只

    def test_hard_filter_zero_regression(self):
        """hard_filter 模式与现有行为完全一致（零回归）。"""
        result = _run_scan_with_mocks(
            config=_make_hard_filter_config(),
            index_trend="strong",
            stock_performances=["strong"],
            candidate_scores=[70.0],  # 70 ≥ 60（hard_filter 的 score_threshold）
        )
        # hard_filter 正常放行，JSON 结构含默认分级字段
        assert result["selected_count"] == 1
        sig = result["signals"][0]
        # hard_filter 下 market_regime 用默认值 "hard_filter"
        assert sig["market_regime"] == "hard_filter"
        assert sig["position_multiplier"] == 1.0
        # data_metrics 里 policy_mode = hard_filter
        dm = result["data_metrics"]
        assert dm["policy_mode"] == "hard_filter"

    def test_graded_allow_entry_clears_stale_blocked_state(self):
        """修复 issue 6：graded 放行后不得残留旧 MA20 硬过滤状态。

        弱市（close < MA20）下 check_market_trend 会先写入 trend_filter_reason；
        但 graded 裁决 allow_entry=True 时必须清空它，否则会出现
        selected_count>0 且 blocked_market=True 的矛盾结论。
        """
        result = _run_scan_with_mocks(
            config=_make_graded_config(
                weak_score_threshold=75,
                weak_daily_limit=5,
                require_rs=True,
                rs_lookback=3,
                rs_min=-1.0,   # 宽松阈值，outperform 必过
            ),
            index_trend="weak",
            stock_performances=["outperform"],
            candidate_scores=[80.0],
        )
        assert result["selected_count"] == 1
        # 核心：放行时不得残留旧硬过滤状态
        assert result["blocked_market"] is False
        assert result["trend_filter_reason"] is None
        assert result["signals"][0]["market_regime"] == "weak"

    def test_graded_rs_filtered_all_reason_code(self):
        """修复 issue 6：graded 允许开仓但 RS 拦掉全部候选时，
        filter 原因应为 relative_strength_filtered_all（非 MA20 误归因）。
        """
        result = _run_scan_with_mocks(
            config=_make_graded_config(
                weak_score_threshold=75,
                weak_daily_limit=5,
                require_rs=True,
                rs_lookback=3,
                rs_min=0.5,    # 高阈值：同步指数的个股过不了
            ),
            index_trend="weak",
            stock_performances=["strong"],
            candidate_scores=[80.0],
        )
        assert result["raw_candidate_count"] == 1
        assert result["selected_count"] == 0
        assert result["rs_filtered_count"] == 1
        assert result["trend_filter_reason"] == "relative_strength_filtered_all"


class TestGradedSignalJSONStructure:
    """graded/shadow 模式下信号 JSON 的分级字段完整性验证。"""

    def test_graded_entry_has_all_v3_fields(self):
        """graded 模式下每个 entry 都包含完整的分级字段集。"""
        result = _run_scan_with_mocks(
            config=_make_graded_config(
                weak_score_threshold=75,
                weak_daily_limit=5,
                require_rs=False,
            ),
            index_trend="strong",
            stock_performances=["strong"],
            candidate_scores=[80.0],
        )
        assert result["selected_count"] >= 1
        sig = result["signals"][0]
        expected_fields = {
            "market_regime",
            "market_reason_code",
            "position_multiplier",
            "base_position_pct",
            "recommended_position_pct",
            "relative_strength",
            "relative_strength_lookback",
        }
        for field in expected_fields:
            assert field in sig, f"信号 entry 缺少分级字段：{field}"

    def test_hard_filter_entry_has_default_v3_fields(self):
        """hard_filter 模式也包含分级字段（保持 JSON 结构一致）。"""
        result = _run_scan_with_mocks(
            config=_make_hard_filter_config(),
            index_trend="strong",
            stock_performances=["strong"],
            candidate_scores=[70.0],
        )
        assert result["selected_count"] >= 1
        sig = result["signals"][0]
        # hard_filter 模式下 market_regime 用默认值填充
        assert sig.get("market_regime") == "hard_filter"
        # position_multiplier 默认为 1.0
        assert sig.get("position_multiplier") == 1.0


# =====================================================================
# shadow 模式专用 helper
# =====================================================================


def _make_shadow_config(
    weak_allow_entry: bool = True,
    weak_score_threshold: float = 75,
    weak_daily_limit: int = 2,
    require_rs: bool = False,
    rs_min: float = 0.0,
) -> dict:
    """构造 shadow 模式 config dict。

    关键：shadow 模式主流程走 hard_filter，但 graded policy 仍会被
    _run_shadow_graded_simulation 使用，所以 weak/extreme_weak 分节
    必须完整提供（与 graded 模式相同）。
    """
    return {
        "global": {},
        "strategies": {
            "oversold_bounce": {
                "params": {
                    "market_regime_policy": {
                        "mode": "shadow",
                        "base_position_pct": 0.10,
                        "weak": {
                            "allow_entry": weak_allow_entry,
                            "position_multiplier": 0.5,
                            "score_threshold": weak_score_threshold,
                            "daily_signal_limit": weak_daily_limit,
                            "require_relative_strength": require_rs,
                            # 配置契约：RS 参数定义在 weak 子节下（与 config.yaml 一致）
                            "relative_strength_lookback": 5,
                            "relative_strength_min": rs_min,
                        },
                        "extreme_weak": {
                            "allow_entry": False,
                            "position_multiplier": 0.0,
                            "daily_signal_limit": 0,
                        },
                    },
                    # hard_filter 路径需要的默认值
                    "stop_loss_ratio": 0.97,
                    "hard_stop_loss": 0.10,
                    "trailing_stop_ratio": 0.08,
                    "signal_cooldown_days": 60,
                    "max_signals_per_year": 2,
                    "kline_history_days": 120,
                    "min_kline_length": 30,
                    "max_signals_per_month": 12,
                    # score_filter：hard_filter 路径需要（shadow 主流程走 hard_filter）
                    "score_filter": {
                        "enabled": True,
                        "score_threshold": 60,
                    },
                    # score_adjustment：仅 graded 模式与其互斥（shadow 保留 hard_filter
                    # 配置故不冲突），本用例为 shadow 主流程 hard_filter 路径提供评分过滤
                },
            },
        },
    }


# =====================================================================
# shadow 模式测试类
# =====================================================================


class TestShadowMode:
    """shadow 模式：正式信号不变 + 额外采集 graded 差异。"""

    def test_shadow_preserves_hard_filter_signals(self):
        """shadow 模式正式信号与 hard_filter 完全一致（零回归）。

        场景设计：weak 指数（close < MA20）
          - hard_filter 主流程：MA20 位置过滤拦掉 → 正式 signals 为空
          - shadow 模拟 graded：weak + allow_entry=True → graded 会放行
          - 但正式 signals 必须仍为 hard_filter 的空列表
        """
        result = _run_scan_with_mocks(
            config=_make_shadow_config(
                weak_allow_entry=True,
                weak_score_threshold=75,
                weak_daily_limit=5,
            ),
            index_trend="weak",
            stock_performances=["strong"],
            candidate_scores=[80.0],
        )
        # --- 正式信号：hard_filter 行为 ---
        assert result["selected_count"] == 0, (
            "shadow 模式正式 signals 必须等于 hard_filter 结果；"
            "hard_filter 在 weak 指数（close < MA20）下被 MA20 位置过滤拦掉"
        )
        assert result["blocked_market"] is True
        assert result["trend_filter_reason"] is not None

        # --- graded 裁决：graded 会放行（allow_entry=True） ---
        dm = result["data_metrics"]
        assert dm["policy_mode"] == "shadow"
        assert dm["shadow_hard_filter_status"] is not None
        gd = dm["shadow_graded_decision"]
        assert gd["allow_entry"] is True, (
            "shadow 模拟 graded：weak + allow_entry=True 时应放行"
        )
        assert gd["regime"] == "weak"

        # --- diff_summary 应反映差异 ---
        assert "hard_filter 阻断" in dm["shadow_diff_summary"]
        assert "graded 放行" in dm["shadow_diff_summary"]

    def test_shadow_records_graded_candidates_in_data_metrics(self):
        """shadow 模式 data_metrics 包含完整的 shadow_hard_filter_status /
        shadow_graded_decision / shadow_graded_signals / shadow_diff_summary。"""
        result = _run_scan_with_mocks(
            config=_make_shadow_config(
                weak_allow_entry=True,
                weak_score_threshold=75,
                weak_daily_limit=5,
            ),
            index_trend="weak",
            stock_performances=["outperform"],
            candidate_scores=[90.0],  # 高分候选
        )
        dm = result["data_metrics"]

        # 四个必需字段都存在
        assert "shadow_hard_filter_status" in dm
        assert "shadow_graded_decision" in dm
        assert "shadow_graded_signals" in dm
        assert "shadow_diff_summary" in dm

        # graded_decision 字段结构完整
        gd = dm["shadow_graded_decision"]
        expected_keys = {
            "regime", "allow_entry", "reason_code", "reason",
            "position_multiplier", "score_threshold",
            "daily_signal_limit", "require_relative_strength",
        }
        for key in expected_keys:
            assert key in gd, f"shadow_graded_decision 缺少字段：{key}"

        # graded_signals 字段结构
        assert isinstance(dm["shadow_graded_signals"], list)
        for entry in dm["shadow_graded_signals"]:
            assert {"code", "name", "score"} <= set(entry.keys())

    def test_shadow_does_not_alter_formal_outputs_in_strong_market(self):
        """强市下 shadow 正式信号与 hard_filter 输出一致（双向一致性验证）。

        shadow 在强市下：
          - hard_filter 主流程：MA20/MA60 位置通过 → 候选进入正式 signals
          - shadow 模拟 graded：strong → allow_entry=True → 同样放行
          - 两者结论一致
        """
        # 1. 用 hard_filter config 跑一次
        hf_result = _run_scan_with_mocks(
            config=_make_hard_filter_config(),
            index_trend="strong",
            stock_performances=["strong"],
            candidate_scores=[70.0],
        )

        # 2. 用 shadow config 跑一次
        shadow_result = _run_scan_with_mocks(
            config=_make_shadow_config(
                weak_allow_entry=False,  # 强市下不涉及 weak，随便填
                weak_score_threshold=75,
                weak_daily_limit=5,
            ),
            index_trend="strong",
            stock_performances=["strong"],
            candidate_scores=[70.0],
        )

        # 3. 核心断言：正式 signals 数量和 blocked_market 状态必须完全一致
        assert hf_result["selected_count"] == shadow_result["selected_count"], (
            f"hard_filter selected={hf_result['selected_count']}, "
            f"shadow selected={shadow_result['selected_count']}——正式信号数量应一致"
        )
        assert hf_result["blocked_market"] == shadow_result["blocked_market"]
        assert len(hf_result["signals"]) == len(shadow_result["signals"])

        # 4. shadow 的 data_metrics 中 graded_decision 显示 strong 放行
        dm = shadow_result["data_metrics"]
        gd = dm["shadow_graded_decision"]
        assert gd["regime"] == "strong"
        assert gd["allow_entry"] is True

    def test_shadow_extreme_weak_consistent_block(self):
        """极弱市下 hard_filter 和 graded 都阻断，shadow diff_summary 反映一致。"""
        result = _run_scan_with_mocks(
            config=_make_shadow_config(
                weak_allow_entry=True,
                weak_score_threshold=75,
                weak_daily_limit=5,
            ),
            index_trend="extreme_weak",
            stock_performances=["strong"],
            candidate_scores=[95.0],
        )
        # 正式 signals 为空（hard_filter 被 MA20+MA60 位置双重拦）
        assert result["selected_count"] == 0
        assert result["blocked_market"] is True

        # shadow 模拟 graded：extreme_weak + allow_entry=False → 禁止开仓
        dm = result["data_metrics"]
        gd = dm["shadow_graded_decision"]
        assert gd["regime"] == "extreme_weak"
        assert gd["allow_entry"] is False

        # diff_summary 应说"结论一致"
        assert "结论一致" in dm["shadow_diff_summary"]

    def test_shadow_graded_signals_include_hard_filter_blocked_candidates(self):
        """修复 issue 4：shadow 必须从原始候选池出发对比，而非仅对 hard_filter 放行的信号。

        场景：弱市（hard_filter 被 MA20 拦掉 → 正式 signals 为空），
        但 graded 裁决 allow_entry=True 且评分达标 → graded_signals 必须
        包含该候选（证明"旧策略挡掉的候选，新策略可安全放行"）。
        """
        result = _run_scan_with_mocks(
            config=_make_shadow_config(
                weak_allow_entry=True,
                weak_score_threshold=75,
                weak_daily_limit=5,
                require_rs=False,
            ),
            index_trend="weak",
            stock_performances=["strong"],
            candidate_scores=[80.0],
        )
        # 正式信号仍为 hard_filter 结果（空）
        assert result["selected_count"] == 0
        dm = result["data_metrics"]
        # 修复核心：graded 链在同一候选池上评估出 1 只（而非修复前的 0 只）
        assert dm["shadow_graded_decision"]["allow_entry"] is True
        assert len(dm["shadow_graded_signals"]) == 1
        assert dm["shadow_graded_signals"][0]["code"] == "600001"
        # hard_filter 链在同一候选池上的放行数为 0（弱市市场过滤拦截）
        assert dm["shadow_hard_filter_passed_count"] == 0

    def test_shadow_graded_chain_applies_relative_strength(self):
        """修复 issue 4：shadow 的 graded 链必须真正执行相对强度检查。

        弱市 require_relative_strength=True + 高阈值 → 与指数同步的候选被 RS 拦掉，
        shadow_graded_signals 为空且 graded_rs_filtered_count=1。
        """
        result = _run_scan_with_mocks(
            config=_make_shadow_config(
                weak_allow_entry=True,
                weak_score_threshold=75,
                weak_daily_limit=5,
                require_rs=True,
                rs_min=0.5,
            ),
            index_trend="weak",
            stock_performances=["strong"],  # 与指数同步 → RS≈0 < 0.5
            candidate_scores=[80.0],
        )
        dm = result["data_metrics"]
        assert dm["shadow_graded_decision"]["require_relative_strength"] is True
        assert len(dm["shadow_graded_signals"]) == 0
        assert dm["shadow_graded_rs_filtered_count"] == 1


class TestShadowQuotaChainParity:
    """P2-4（2026-10-20）：shadow 模拟必须与正式 graded 路径复用同一配额链。

    修复前 shadow 只应用分级 daily_signal_limit，遗漏 max_daily_signals /
    max_signals_per_month / 已用月额度，导致影子候选数被高估（正式路径日限为 1 时
    shadow 仍记录 3 个候选），污染影子期对比数据。
    修复后两条链统一走 apply_candidate_quota_chain。
    """

    @staticmethod
    def _candidate_pool(n: int):
        return [
            {
                "code": f"60000{i + 1}",
                "name": f"S{i + 1}",
                "score": 80.0 - i,
                "hard_filter_passed": False,
                "stock_closes": None,
            }
            for i in range(n)
        ]

    def _run(
        self,
        *,
        decision_daily_limit: int,
        n: int,
        used_monthly: int,
        max_per_month: int,
        max_daily: int,
    ) -> dict:
        from types import SimpleNamespace
        from strategy.oversold_bounce.risk_control import MarketRegimeDecision
        from scripts.daily_scan import _run_shadow_graded_simulation

        class _StubController:
            def __init__(self, dec):
                self._dec = dec

            def decide_market_regime(self, _ctx):
                return self._dec

        decision = MarketRegimeDecision(
            regime="weak",
            allow_entry=True,
            position_multiplier=0.5,
            score_threshold=0.0,
            daily_signal_limit=decision_daily_limit,
            require_relative_strength=False,
            reason_code="weak_market",
            reason="弱市",
        )
        graded_policy = SimpleNamespace(
            relative_strength_lookback=5, relative_strength_min=0.0
        )
        return _run_shadow_graded_simulation(
            risk_controller=_StubController(decision),
            market_context=SimpleNamespace(index_close_history=None),
            graded_policy=graded_policy,
            candidate_pool=self._candidate_pool(n),
            official_selected_count=1,
            trend_filter_reason=None,
            signal_date="2026-10-16",
            used_monthly_count=used_monthly,
            max_per_month=max_per_month,
            max_daily_signals=max_daily,
        )

    def test_no_quota_keeps_all_candidates(self):
        """正向对照：无任何额度约束时 3 只候选全部保留。"""
        res = self._run(
            decision_daily_limit=0, n=3, used_monthly=0, max_per_month=12, max_daily=0
        )
        assert len(res["graded_signals"]) == 3

    def test_max_daily_signals_caps_shadow(self):
        """max_daily_signals=1（正式路径日限 1）→ shadow 也必须截断到 1。

        这是验收探针复现场景：修复前 shadow 只按分级日限（3）截断 → 记录 3 只。
        """
        res = self._run(
            decision_daily_limit=3, n=3, used_monthly=0, max_per_month=12, max_daily=1
        )
        assert len(res["graded_signals"]) == 1

    def test_monthly_quota_caps_shadow(self):
        """剩余月额度 = max_per_month - used = 12 - 11 = 1 → shadow 截断到 1。"""
        res = self._run(
            decision_daily_limit=0, n=3, used_monthly=11, max_per_month=12, max_daily=0
        )
        assert len(res["graded_signals"]) == 1

    def test_graded_daily_limit_caps_shadow(self):
        """分级弱市日限额 = 1 → shadow 截断到 1。"""
        res = self._run(
            decision_daily_limit=1, n=3, used_monthly=0, max_per_month=12, max_daily=0
        )
        assert len(res["graded_signals"]) == 1

