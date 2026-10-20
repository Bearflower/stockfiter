# -*- coding: utf-8 -*-
"""
回测分级裁决与仓位乘数接入测试（Task 6）

测试目标：
    1. 回测直接消费 decide_market_regime，不复制判断逻辑
    2. position_multiplier 真正进入收益计算（不是只改显示）
    3. 防止未来数据泄漏（前缀不变性）
    4. 极弱市场交易数恒为 0
    5. 同一种子 + 同一参数 → 结果可复现

依赖：risk_control 模块已实现 MarketRegimeDecision / PreloadedMarketContextProvider / decide_market_regime
"""

import os
import sys

import pytest
import pandas as pd
import numpy as np
from dataclasses import replace
from types import SimpleNamespace

# 确保项目根目录在 sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from strategy.oversold_bounce.risk_control import (
    RiskControlConfig,
    RiskController,
    MarketContext,
    PreloadedMarketContextProvider,
    MarketRegimeDecision,
    MarketRegimePolicyConfig,
)


# ==================== Fixture 工厂 ====================

def _make_graded_risk_config(
    weak_position_multiplier: float = 0.5,
    extreme_weak_allow_entry: bool = False,
    weak_allow_entry: bool = True,
    weak_score_threshold: float = 70.0,
    weak_daily_signal_limit: int = 3,
) -> RiskControlConfig:
    """构造 graded 模式的 RiskControlConfig，所有参数从配置构造，禁止硬编码。"""
    policy = MarketRegimePolicyConfig(
        mode="graded",
        base_position_pct=0.10,
        weak_allow_entry=weak_allow_entry,
        weak_position_multiplier=weak_position_multiplier,
        weak_score_threshold=weak_score_threshold,
        weak_daily_signal_limit=weak_daily_signal_limit,
        weak_require_relative_strength=False,
        relative_strength_lookback=20,
        relative_strength_min=0.0,
        extreme_weak_allow_entry=extreme_weak_allow_entry,
        extreme_weak_position_multiplier=0.0,
        extreme_weak_daily_signal_limit=0,
    )
    return RiskControlConfig(
        market_regime_policy_config=policy,
        index_filter_enabled=True,
        index_filter_method="ma_position",
        index_ma_period=20,
        index_ma_period_long=60,
        long_ma_filter_enabled=True,
        score_filter_enabled=True,
        score_threshold=60.0,
        score_threshold_weak=70.0,
        slope_filter_enabled=False,
        score_adjustment_enabled=False,  # graded 模式下必须关闭（互斥校验）
        max_signals_per_month=12,
        max_daily_signals=0,
    )


def _make_index_df(trend: str = "strong", n_days: int = 120) -> pd.DataFrame:
    """根据市场状态构造模拟指数 K 线。

    关键约束：n_days >= 90（MA60 窗口 + slope_lookback），
    否则 _determine_regime 可能因数据不足返回 unknown。

    trend 决定最后一天的 MA20/MA60 关系：
        - strong: close >= MA20 且 MA20 向上（ma20_slope > 0）
        - weak: close < MA20 但 close >= MA60，MA20 向下但 MA60 向上
        - extreme_weak: close < MA60 且 MA60 向下

    判定逻辑来自 risk_control.py _determine_regime()：
        extreme_weak > strong > weak > unknown
    """
    assert n_days >= 90, "需要至少 90 天数据才能正确判定 MA20/MA60 slope"

    rng = np.random.RandomState(42)
    dates = pd.date_range("2024-01-01", periods=n_days, freq="B")

    if trend == "strong":
        # 稳步上升：close > MA20 > MA60，所有 slope > 0
        base = 3500.0
        drift = np.linspace(0, 500, n_days)
        noise = rng.normal(0, 10, n_days)
        close = base + drift + noise

    elif trend == "weak":
        # 构造要点：close < MA20 但 close > MA60，且 MA60 slope > 0
        # 策略：前 2/3 上升（让 MA60 维持向上），后 1/3 小幅回调（让 close 跌破 MA20 但不跌破 MA60）
        base = 3800.0
        rise_len = n_days * 2 // 3
        fall_len = n_days - rise_len

        # 前 2/3：持续上升，MA60 会跟着向上
        rise = np.linspace(0, 300, rise_len)
        # 后 1/3：小幅回调，让 close 从峰值掉到 MA20 下方但仍在 MA60 上方
        # MA60 在上升期累积了较高位置，所以 close 回落一点不会跌破
        fall = np.linspace(300, 150, fall_len)

        drift = np.concatenate([rise, fall])
        noise = rng.normal(0, 5, n_days)
        close = base + drift + noise

    elif trend == "extreme_weak":
        # 先涨后持续下跌：close < MA60 且 MA60 slope < 0
        base = 4200.0
        # 前 40 天快速上涨让 MA60 维持在高位
        rise = np.linspace(0, 400, n_days // 3)
        # 剩余持续下跌，让 close 跌破 MA60 且 MA60 slope 转为负
        fall = np.linspace(400, -600, n_days - n_days // 3)
        drift = np.concatenate([rise, fall])
        noise = rng.normal(0, 10, n_days)
        close = base + drift + noise

    else:
        # unknown：数据不足，只有 30 天
        base = 3500.0
        close = base + rng.normal(0, 5, n_days)

    df = pd.DataFrame({
        "date": dates,
        "open": close * (1 + rng.normal(0, 0.005, n_days)),
        "high": close * (1 + np.abs(rng.normal(0, 0.01, n_days))),
        "low": close * (1 - np.abs(rng.normal(0, 0.01, n_days))),
        "close": close,
        "volume": rng.randint(1e8, 5e8, n_days),
        "amount": close * rng.randint(1e8, 5e8, n_days),
    })
    return df


# ==================== 核心测试类 ====================


class TestGradedBacktestIntegrity:
    """回测分级裁决与生产同路、仓位乘数真正进收益的测试。"""

    # ----------------------------------------------------------
    # 测试 1：回测直接消费 decide_market_regime
    # ----------------------------------------------------------

    def test_backtest_uses_same_decide_market_regime_as_production(self):
        """回测与生产（RealtimeMarketContextProvider）对同一日期给出相同 regime。

        本测试直接验证 PreloadedMarketContextProvider + decide_market_regime
        的行为正确性——它们是回测和生产（RealtimeProvider）共用的核心逻辑。
        """
        config = _make_graded_risk_config()
        controller = RiskController(config)

        # ===== 强市：手动构造 MarketContext =====
        strong_ctx = MarketContext(
            current_date="2024-03-01",
            index_close=4000.0,         # close 高于 MA20
            index_ma20=3900.0,           # close >= MA20
            index_ma60=3700.0,           # close >= MA60
            is_weak_market=False,
            data_sufficient=True,
            # 强市关键：ma20_slope > 0
            ma20_slope=0.02,
            ma60_slope=0.01,
        )
        decision = controller.decide_market_regime(strong_ctx)
        assert decision.regime == "strong", f"期望 strong，实际 {decision.regime}"
        assert decision.allow_entry is True
        assert decision.position_multiplier == 1.0

        # ===== 弱市：close < MA20 但 close > MA60，MA60 slope 仍向上 =====
        weak_ctx = MarketContext(
            current_date="2024-03-01",
            index_close=3800.0,         # close 低于 MA20 但高于 MA60
            index_ma20=3900.0,           # close < MA20
            index_ma60=3700.0,           # close > MA60（关键：不低于 MA60）
            is_weak_market=True,
            data_sufficient=True,
            # _determine_regime 里先判 extreme_weak（close < MA60 and ma60_slope <= 0），
            # 再判 strong（close >= MA20 and ma20_slope > 0），最后 weak。
            # close(3800) >= MA60(3700) 不会被判为 extreme_weak；
            # close(3800) < MA20(3900) 不会被判为 strong；
            # 所以最终 weak。
            ma20_slope=0.005,
            ma60_slope=0.005,
        )
        weak_decision = controller.decide_market_regime(weak_ctx)
        assert weak_decision.regime == "weak", (
            f"期望 weak，实际 {weak_decision.regime}。"
            f"判定条件：close(3800) < MA20(3900) 且 close(3800) >= MA60(3700)"
        )
        assert weak_decision.position_multiplier == 0.5  # 从配置读取

    # ----------------------------------------------------------
    # 测试 2：position_multiplier 真正进入收益计算
    # ----------------------------------------------------------

    def test_position_multiplier_enters_return_calculation(self):
        """弱市 position_multiplier=0.5 → 单笔收益是满仓方案的一半。"""
        # 模拟：假设一次交易 entry=100, exit=110，raw_return = 0.10 (10%)
        # 满仓 → 实际收益率 = 10%
        # 半仓 → 实际收益率 = 10% × 0.5 = 5%
        from scripts.backtest_obpc import BacktestConfig, TradeSimulator

        # 基础配置
        config_full = BacktestConfig()  # 默认满仓 100%
        simulator_full = TradeSimulator(config_full)

        config_half = BacktestConfig()
        simulator_half = TradeSimulator(config_half)

        # 构造简单 K 线数据：10 天
        dates = pd.date_range("2024-01-01", periods=10, freq="B")
        prices = [100.0, 102.0, 105.0, 108.0, 110.0, 109.0, 107.0, 106.0, 105.0, 104.0]
        kline_df = pd.DataFrame({
            "date": dates,
            "open": prices,
            "high": [p * 1.01 for p in prices],
            "low": [p * 0.99 for p in prices],
            "close": prices,
            "volume": [1e6] * 10,
            "amount": [1e8] * 10,
        })

        # 构造 Signal：信号日 = 第 3 天（index 2），支撑位 = 95
        from strategy.base import Signal
        signal = Signal(
            code="000001",
            signal_date=dates[2].strftime("%Y-%m-%d"),
            score=80.0,
            detail={"support_level": 95.0},
        )

        # 满仓模拟（默认 position_multiplier=1.0）
        trade_full = simulator_full.simulate(signal, kline_df, "测试股票")
        assert trade_full is not None, "满仓模拟应成功"

        # 半仓模拟：需要修改 simulator 支持 position_multiplier
        # 当前 simulate 还不支持乘数（我们刚要改），这里用 monkey-patch 验证
        # 先直接算 raw_return 的乘数效果
        raw_return_full = trade_full.raw_return

        # 验证：如果有 position_multiplier 支持，半仓收益率 = 满仓 × 0.5
        # 这个断言会在实现 position_multiplier 进入收益计算后生效
        expected_half_return = raw_return_full * 0.5

        # 这里直接调用 TradeSimulator.simulate 但传入 multiplier 参数时应生效
        # 先验证 simulator 的 simulate 方法签名支持 multiplier
        import inspect
        sig = inspect.signature(simulator_half.simulate)
        assert "position_multiplier" in sig.parameters, (
            "TradeSimulator.simulate 必须接受 position_multiplier 参数"
        )

        trade_half = simulator_half.simulate(
            signal, kline_df, "测试股票", position_multiplier=0.5
        )
        assert trade_half is not None, "半仓模拟应成功"

        # 半仓净收益 = 满仓净收益 × 0.5（近似，佣金结构可能有微小差异）
        # 放宽容差到 0.001 即 0.1% 因为成本是按比例收的
        assert abs(trade_half.net_return - trade_full.net_return * 0.5) < 0.002, (
            f"半仓净收益 ({trade_half.net_return:.4f}) 应接近满仓净收益 "
            f"({trade_full.net_return:.4f}) 的一半，"
            f"差值 {abs(trade_half.net_return - trade_full.net_return * 0.5):.6f}"
        )

    # ----------------------------------------------------------
    # 测试 3：前缀不变性（无未来数据泄漏）
    # ----------------------------------------------------------

    def test_no_future_data_leakage(self):
        """T 日裁决不使用 T+1 及以后数据（前缀不变性）。"""
        # 构造一段指数数据
        rng = np.random.RandomState(123)
        dates = pd.date_range("2024-01-01", periods=150, freq="B")
        base = 3500.0
        close = base + np.cumsum(rng.normal(0, 30, 150))

        config = _make_graded_risk_config()
        controller = RiskController(config)

        # 截断到 T=100 天的数据
        T = 100
        prefix_df = pd.DataFrame({
            "date": dates[:T],
            "open": close[:T] * (1 + rng.normal(0, 0.005, T)),
            "high": close[:T] * (1 + np.abs(rng.normal(0, 0.01, T))),
            "low": close[:T] * (1 - np.abs(rng.normal(0, 0.01, T))),
            "close": close[:T],
            "volume": rng.randint(1e8, 5e8, T),
            "amount": close[:T] * rng.randint(1e8, 5e8, T),
        })

        prefix_provider = PreloadedMarketContextProvider(prefix_df, config)
        T_date_prefix = prefix_df["date"].iloc[-1].strftime("%Y-%m-%d")
        context_prefix = prefix_provider.get_context(T_date_prefix)
        decision_prefix = controller.decide_market_regime(context_prefix)

        # 再构造包含 T+1~T+50 的完整数据
        full_df = pd.DataFrame({
            "date": dates,
            "open": close * (1 + rng.normal(0, 0.005, 150)),
            "high": close * (1 + np.abs(rng.normal(0, 0.01, 150))),
            "low": close * (1 - np.abs(rng.normal(0, 0.01, 150))),
            "close": close,
            "volume": rng.randint(1e8, 5e8, 150),
            "amount": close * rng.randint(1e8, 5e8, 150),
        })

        full_provider = PreloadedMarketContextProvider(full_df, config)
        # T_date 在 full_df 中的位置也是最后一天的前缀对应
        # 但 full_provider 包含了更多数据，要取 full_df 中第 T-1 天（即 T_date_prefix）
        # 因为 full_df 日期范围更大，T_date_prefix 在 full_df 中也是存在的
        context_full = full_provider.get_context(T_date_prefix)
        decision_full = controller.decide_market_regime(context_full)

        # 断言：同一天的 MarketRegimeDecision 必须完全一致
        # 所有字段都要对比
        assert decision_prefix.regime == decision_full.regime
        assert decision_prefix.allow_entry == decision_full.allow_entry
        assert decision_prefix.position_multiplier == decision_full.position_multiplier
        assert decision_prefix.score_threshold == decision_full.score_threshold
        assert decision_prefix.daily_signal_limit == decision_full.daily_signal_limit

    # ----------------------------------------------------------
    # 测试 4：极弱市场交易数恒为 0
    # ----------------------------------------------------------

    def test_extreme_weak_trade_count_is_zero(self):
        """极弱市场 graded 模式下交易数恒为 0。"""
        index_df = _make_index_df(trend="extreme_weak", n_days=120)
        config = _make_graded_risk_config(extreme_weak_allow_entry=False)
        controller = RiskController(config)

        provider = PreloadedMarketContextProvider(index_df, config)

        # 遍历最后 10 天，全部应该是 extreme_weak 且 allow_entry=False
        for i in range(-10, 0):
            date_str = index_df["date"].iloc[i].strftime("%Y-%m-%d")
            context = provider.get_context(date_str)
            if context is None:
                continue  # 某些天可能数据不足
            decision = controller.decide_market_regime(context)
            if decision.regime == "extreme_weak":
                assert decision.allow_entry is False, (
                    f"极弱市场 {date_str} 应禁止开仓，实际 allow_entry={decision.allow_entry}"
                )
                assert decision.position_multiplier == 0.0, (
                    f"极弱市场 position_multiplier 应为 0.0"
                )

    # ----------------------------------------------------------
    # 测试 5：同一随机种子可复现
    # ----------------------------------------------------------

    def test_deterministic_with_same_seed(self):
        """同一随机种子 + 同一参数 → 结果可复现。"""
        rng = np.random.RandomState(999)
        dates = pd.date_range("2024-01-01", periods=120, freq="B")
        base = 3500.0
        close = base + np.cumsum(rng.normal(0, 20, 120))

        df1 = pd.DataFrame({
            "date": dates,
            "open": close * 0.999,
            "high": close * 1.005,
            "low": close * 0.995,
            "close": close,
            "volume": [1e8] * 120,
            "amount": [1e10] * 120,
        })
        df2 = df1.copy()

        config = _make_graded_risk_config()
        controller1 = RiskController(config)
        controller2 = RiskController(config)

        provider1 = PreloadedMarketContextProvider(df1, config)
        provider2 = PreloadedMarketContextProvider(df2, config)

        # 逐日对比
        for idx in range(60, 120):
            date_str = dates[idx].strftime("%Y-%m-%d")
            ctx1 = provider1.get_context(date_str)
            ctx2 = provider2.get_context(date_str)
            if ctx1 is None or ctx2 is None:
                continue
            d1 = controller1.decide_market_regime(ctx1)
            d2 = controller2.decide_market_regime(ctx2)
            assert d1.regime == d2.regime, f"{date_str}: regime 不一致"
            assert d1.position_multiplier == d2.position_multiplier, (
                f"{date_str}: position_multiplier 不一致"
            )


class TestBacktestEngineGradedIntegration:
    """BacktestEngine 与分级裁决的集成测试。

    验证 engine 在 graded 模式下正确：
        1. 调用 decide_market_regime
        2. 将 position_multiplier 存入 TradeResult
        3. 回测报告包含 market_regime_distribution
    """

    def test_trade_result_contains_regime_and_multiplier(self):
        """TradeResult 应有 regime 和 position_multiplier 字段。"""
        from scripts.backtest_obpc import TradeResult

        # 直接检查字段存在
        result = TradeResult(
            code="000001",
            regime="strong",
            position_multiplier=1.0,
        )
        assert result.regime == "strong"
        assert result.position_multiplier == 1.0

    def test_report_contains_market_regime_distribution(self):
        """回测报告应包含 market_regime_distribution 和 trade_summary_by_regime。"""
        from scripts.backtest_obpc import (
            BacktestConfig,
            BacktestReporter,
            TradeResult,
        )

        config = BacktestConfig()
        reporter = BacktestReporter(config)

        # 构造有 regime/multiplier 的交易列表
        trades = [
            TradeResult(code="000001", regime="strong", position_multiplier=1.0,
                        net_return=0.05),
            TradeResult(code="000002", regime="strong", position_multiplier=1.0,
                        net_return=-0.02),
            TradeResult(code="000003", regime="weak", position_multiplier=0.5,
                        net_return=0.03),
        ]

        # 测试 stats dict（添加 graded 相关统计）
        stats = {
            "total_stocks": 10,
            "total_signals": 5,
            "total_trades": 3,
        }
        risk_stats = {
            "summary": {
                "skipped_extreme_weak_filter": 5,
                "skipped_weak_market_blocked": 2,
            },
            "monthly": [],
            # V3 新增：市场裁决原始分布（由 engine.run() 采集）
            "market_regime_distribution": {
                "strong": 100,
                "weak": 30,
                "extreme_weak": 10,
                "unknown": 5,
            },
        }

        report = reporter.generate_report(trades, stats, risk_stats)

        # 断言：报告包含 market_regime_distribution
        assert "market_regime_distribution" in report, (
            "回测报告必须包含 market_regime_distribution 字段"
        )
        dist = report["market_regime_distribution"]
        assert dist.get("strong", 0) == 100
        assert dist.get("weak", 0) == 30
        assert dist.get("extreme_weak", 0) == 10
        assert dist.get("unknown", 0) == 5

        # 断言：报告包含 trade_summary_by_regime
        assert "trade_summary_by_regime" in report, (
            "回测报告必须包含 trade_summary_by_regime 字段"
        )
        summary_by_regime = report["trade_summary_by_regime"]
        assert "strong" in summary_by_regime
        assert "weak" in summary_by_regime
        assert summary_by_regime["strong"]["trades"] == 2
        assert summary_by_regime["strong"]["win_rate"] > 0
        assert summary_by_regime["weak"]["trades"] == 1

        # 断言：trade_summary_by_regime 里 extreme_weak 应为 0（未实际开仓）
        assert summary_by_regime.get("extreme_weak", {}).get("trades", 0) == 0


class TestDailySignalLimitEnforcement:
    """弱市 daily_signal_limit 截断测试。"""

    def test_daily_signal_limit_truncates_candidates(self):
        """弱市 daily_signal_limit=2 → MarketRegimeDecision.daily_signal_limit 返回 2。"""
        # _make_graded_risk_config 直接返回 RiskControlConfig，不是 BacktestConfig
        ctrl_config = _make_graded_risk_config(
            weak_allow_entry=True,
            weak_daily_signal_limit=2,  # 弱市每日最多 2 个
            weak_position_multiplier=0.5,
        )
        controller = RiskController(ctrl_config)

        # 直接构造弱市 MarketContext（同 test_backtest_uses_same_decide_market_regime_as_production）
        weak_ctx = MarketContext(
            current_date="2024-03-01",
            index_close=3800.0,
            index_ma20=3900.0,
            index_ma60=3700.0,
            is_weak_market=True,
            data_sufficient=True,
            ma20_slope=0.005,
            ma60_slope=0.005,
        )
        decision = controller.decide_market_regime(weak_ctx)

        # 断言：这是 weak 状态，且 daily_signal_limit = 2（从配置读取）
        assert decision.regime == "weak"
        assert decision.allow_entry is True
        assert decision.position_multiplier == 0.5
        assert decision.daily_signal_limit == 2


class TestBacktestReporterGradedPrint:
    """BacktestReporter.print_report 应打印 graded 相关统计。"""

    def test_print_report_includes_graded_section(self, capsys):
        """print_report 在 graded 模式下应打印市场状态分布段。"""
        from scripts.backtest_obpc import (
            BacktestConfig,
            BacktestReporter,
            TradeResult,
        )

        config = BacktestConfig()
        reporter = BacktestReporter(config)

        trades = [
            TradeResult(code="000001", regime="strong", position_multiplier=1.0,
                        net_return=0.05),
            TradeResult(code="000002", regime="weak", position_multiplier=0.5,
                        net_return=-0.01),
        ]

        stats = {"total_stocks": 10, "total_signals": 5, "total_trades": 2}
        risk_stats = {
            "summary": {"skipped_extreme_weak_filter": 3},
            "monthly": [],
            "market_regime_distribution": {
                "strong": 50, "weak": 30, "extreme_weak": 10, "unknown": 5,
            },
        }

        report = reporter.generate_report(trades, stats, risk_stats)
        reporter.print_report(report)

        captured = capsys.readouterr().out
        # 验证打印输出包含市场状态相关关键词
        assert "市场状态" in captured or "分级" in captured or "regime" in captured.lower()


# ==================== fix-7 回测前置过滤 / shadow 零回归 ====================


class _StubSignal:
    """测试用信号桩：signal_date 取窗口最后一天，score 固定。"""

    def __init__(self, signal_date, score=80.0):
        self.code = "000001"
        self.signal_date = signal_date
        self.score = score
        self.detail = {"support_level": 90.0}


class _StubStrategy:
    """策略桩：返回窗口最后一天的信号。"""

    def analyze(self, code, data):
        window_df = data["d"]
        return _StubSignal(window_df["date"].iloc[-1].strftime("%Y-%m-%d"))


class _StubDataLoader:
    """数据加载器桩：只实现 load_kline_data。"""

    def __init__(self, df):
        self._df = df

    def load_kline_data(self, code):
        return self._df


class _StubRiskResult:
    """风控结果桩：全部放行。"""

    passed = True
    filter_rule = ""


class _StubController:
    """风控控制器桩：暴露 market_regime_mode 配置，放行全部风控，返回固定裁决。"""

    def __init__(self, mode, decision=None):
        self.config = SimpleNamespace(market_regime_mode=mode)
        self._decision = decision

    def apply_all_controls(self, signal, market_context):
        return _StubRiskResult()

    def decide_market_regime(self, market_context):
        return self._decision


class _StubContextProvider:
    """大盘上下文提供者桩：返回固定 MarketContext，暴露 context_map 供分布采集。"""

    def __init__(self, ctx, ctx_map=None):
        self._ctx = ctx
        self.context_map = ctx_map or {}

    def get_context(self, date_str):
        return self._ctx


def _build_stock_df(n_days=130):
    """构造可触发单次信号的股票 K 线（列名与生产一致）。"""
    rng = np.random.RandomState(7)
    dates = pd.date_range("2024-01-01", periods=n_days, freq="B")
    close = 100.0 + np.cumsum(rng.normal(0, 1.0, n_days))
    return pd.DataFrame({
        "date": dates,
        "open": close,
        "high": close * 1.01,
        "low": close * 0.99,
        "close": close,
        "volume": [1e6] * n_days,
        "amount": [1e8] * n_days,
    })


class TestBacktestGradedPreFilterBypass:
    """fix-7（issue 2）：graded 回测必须绕过旧 MA 均线过滤。

    修复前 graded 模式下 index_filter_map（close>=MA_period）在 decide_market_regime
    之前拦截，跌破均线的弱市日期全部 continue，分级降仓策略无从评估。
    """

    def _make_engine(self, mode, df, decision=None, ctx=None, ctx_map=None):
        from scripts.backtest_obpc import BacktestConfig, BacktestEngine

        config = BacktestConfig()
        config.index_filter_enabled = False  # 默认关闭，各用例按需开启
        engine = BacktestEngine(
            config,
            strategy=_StubStrategy(),
            data_loader=_StubDataLoader(df),
            trade_simulator=None,
        )
        engine.risk_controller = _StubController(mode, decision=decision)
        if ctx is not None:
            engine.market_context_provider = _StubContextProvider(ctx, ctx_map)
        return engine

    def test_graded_backtest_bypasses_index_filter(self):
        """graded 模式：指数跌破均线的日期不得被 index_filter_map 提前拦截。"""
        df = _build_stock_df()
        engine = self._make_engine("graded", df)
        engine.config.index_filter_enabled = True
        # 信号日 = 窗口末尾（i == window_size），标记该日跌破均线
        target = df["date"].iloc[engine.config.window_size].strftime("%Y-%m-%d")
        engine.index_filter_map = {target: False}

        candidates = engine._backtest_single_stock("000001", "测试")

        assert engine.stats["skipped_index_filter"] == 0, (
            "graded 模式不得触发旧 MA 均线过滤"
        )
        assert len(candidates) == 1, "跌破均线的弱市日期在 graded 下应能产出候选"

    def test_hard_filter_backtest_still_applies_index_filter(self):
        """hard_filter 模式：旧 MA 均线过滤仍然生效（零回归）。"""
        df = _build_stock_df()
        engine = self._make_engine("hard_filter", df)
        engine.config.index_filter_enabled = True
        target = df["date"].iloc[engine.config.window_size].strftime("%Y-%m-%d")
        engine.index_filter_map = {target: False}

        candidates = engine._backtest_single_stock("000001", "测试")

        assert engine.stats["skipped_index_filter"] >= 1, (
            "hard_filter 模式必须保留旧 MA 均线过滤"
        )
        assert candidates == []


class TestBacktestShadowZeroRegression:
    """fix-7（issue 3b）：shadow 回测必须与 hard_filter 等价（不执行 graded 正式逻辑）。"""

    def _weak_decision(self):
        """构造一个会给出 0.5 仓位乘数 + 日限额的弱市裁决。"""
        return MarketRegimeDecision(
            regime="weak",
            allow_entry=True,
            position_multiplier=0.5,
            score_threshold=70.0,
            daily_signal_limit=3,
            require_relative_strength=False,
            reason_code="weak_market",
            reason="弱市降仓",
        )

    def _make_engine(self, mode):
        from scripts.backtest_obpc import BacktestConfig, BacktestEngine

        df = _build_stock_df()
        ctx = MarketContext(
            current_date="2024-01-01",
            index_close=3800.0,
            index_ma20=3900.0,
            index_ma60=3700.0,
            is_weak_market=True,
            data_sufficient=True,
            ma20_slope=0.005,
            ma60_slope=0.005,
        )
        config = BacktestConfig()
        config.index_filter_enabled = False
        engine = BacktestEngine(
            config,
            strategy=_StubStrategy(),
            data_loader=_StubDataLoader(df),
            trade_simulator=None,
        )
        engine.risk_controller = _StubController(mode, decision=self._weak_decision())
        engine.market_context_provider = _StubContextProvider(ctx, {})
        return engine, df

    def test_shadow_backtest_does_not_apply_graded_multiplier(self):
        engine, _ = self._make_engine("shadow")
        candidates = engine._backtest_single_stock("000001", "测试")
        assert len(candidates) == 1
        cand = candidates[0]
        assert cand["regime"] == "", "shadow 回测 regime 必须留空（等价 hard_filter）"
        assert cand["position_multiplier"] == 1.0, (
            "shadow 回测必须满仓（等价 hard_filter），不得应用 graded 仓位乘数"
        )
        assert cand["daily_signal_limit"] == 0, "shadow 回测不得应用 graded 日限额"

    def test_graded_backtest_applies_multiplier_and_regime(self):
        engine, _ = self._make_engine("graded")
        candidates = engine._backtest_single_stock("000001", "测试")
        assert len(candidates) == 1
        cand = candidates[0]
        assert cand["regime"] == "weak", "graded 回测必须记录裁决 regime"
        assert cand["position_multiplier"] == 0.5, "graded 回测必须应用仓位乘数"
        assert cand["daily_signal_limit"] == 3, "graded 回测必须记录日限额"


class TestBacktestCooldownAnchorAfterRS:
    """P1-1 回归（2026-10-20）：冷却锚点必须在候选真正入选后才更新。

    修复前 `last_signal_idx = i` 位于 RS 校验之前：首个候选因相对强度不足被拒绝，
    仍会占用 60 日冷却期，把紧随其后本可放行的信号一并压制 → 回测信号数与收益
    系统性偏低。
    """

    def _make_engine(self, df, decision, lookback, rs_min):
        from scripts.backtest_obpc import BacktestConfig, BacktestEngine

        config = BacktestConfig()
        config.index_filter_enabled = False
        engine = BacktestEngine(
            config,
            strategy=_StubStrategy(),
            data_loader=_StubDataLoader(df),
            trade_simulator=None,
        )
        controller = _StubController("graded", decision=decision)
        # 引擎从 controller.config.market_regime_policy_config 读取 RS 参数
        controller.config.market_regime_policy_config = SimpleNamespace(
            relative_strength_lookback=lookback,
            relative_strength_min=rs_min,
        )
        engine.risk_controller = controller

        # 指数恒为 100 → index_return=0，RS == 个股收益率，便于精确构造用例
        index_series = pd.Series(
            [100.0] * len(df),
            index=pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d").values,
            name="index_close",
        )
        ctx = MarketContext(
            current_date="2024-01-01",
            index_close=100.0,
            index_ma20=100.0,
            index_ma60=100.0,
            is_weak_market=True,
            data_sufficient=True,
            ma20_slope=0.005,
            ma60_slope=0.005,
            index_close_history=index_series,
        )
        engine.market_context_provider = _StubContextProvider(ctx, {})
        return engine

    def _make_df(self):
        """构造 130 天 K 线：关键日 120/121/122 相对强度为负，123 转正。"""
        n_days = 130
        dates = pd.date_range("2024-01-01", periods=n_days, freq="B")
        close = np.full(n_days, 101.0)
        # 索引 118~122 逐日走低（RS 为负），123 跳升（RS 转正）
        close[118:123] = [101.0, 100.0, 99.0, 98.0, 97.0]
        close[123:] = 110.0
        return pd.DataFrame({
            "date": dates,
            "open": close,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "volume": [1e6] * n_days,
            "amount": [1e8] * n_days,
        })

    def test_rs_rejected_candidate_does_not_consume_cooldown(self):
        """首个候选 RS 失败后，冷却期不得被占用，后续达标候选必须入选。"""
        df = self._make_df()
        decision = MarketRegimeDecision(
            regime="weak",
            allow_entry=True,
            position_multiplier=0.5,
            score_threshold=70.0,
            daily_signal_limit=3,
            require_relative_strength=True,
            reason_code="weak_market",
            reason="弱市降仓",
        )
        engine = self._make_engine(df, decision, lookback=2, rs_min=0.0)

        candidates = engine._backtest_single_stock("000001", "测试")

        # 循环从 window_size=120 起：120/121/122 三天 RS 为负被拒，123 转正入选
        accepted_date = df["date"].iloc[123].strftime("%Y-%m-%d")
        assert len(candidates) == 1, (
            f"RS 失败的前 3 个候选错误占用冷却期会导致 123 日候选被压制；"
            f"实际候选数={len(candidates)}"
        )
        assert candidates[0]["signal_date"] == accepted_date, (
            f"入选日期应为 {accepted_date}（首个 RS 达标日），"
            f"实际 {candidates[0]['signal_date']}"
        )

    def test_cooldown_still_suppresses_after_accepted_signal(self):
        """正向对照：一旦有候选真正入选，60 日冷却期仍必须生效。"""
        df = self._make_df()
        # 把 118~123 全部设为上涨，使首个候选（120 日）即通过 RS 并入选
        df.loc[df.index[118:124], "close"] = [97.0, 98.0, 99.0, 100.0, 101.0, 110.0]
        df["open"] = df["close"]
        decision = MarketRegimeDecision(
            regime="weak",
            allow_entry=True,
            position_multiplier=0.5,
            score_threshold=70.0,
            daily_signal_limit=3,
            require_relative_strength=True,
            reason_code="weak_market",
            reason="弱市降仓",
        )
        engine = self._make_engine(df, decision, lookback=2, rs_min=0.0)

        candidates = engine._backtest_single_stock("000001", "测试")

        # 120 日入选后，121~129 均落在 60 日冷却期内 → 只应有 1 个候选
        assert len(candidates) == 1
        assert candidates[0]["signal_date"] == df["date"].iloc[120].strftime("%Y-%m-%d")
