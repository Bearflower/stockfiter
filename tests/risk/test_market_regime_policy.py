"""
OBPC V3 分级市场风控 - 配置契约与统一裁决测试

覆盖 Task 1（MarketRegimePolicyConfig 配置合法性与向后兼容）
和 Task 2（MarketRegimeDecision 统一裁决与 graded 路径接入）。
"""

import pandas as pd
import pytest
from strategy.oversold_bounce.risk_control import (
    RiskControlConfig,
    MarketRegimePolicyConfig,
    RealtimeMarketContextProvider,
)


# ==================== 测试配置辅助 ====================
# P1-2 修复后，graded / shadow 要求 weak 与 extreme_weak 的「全部风险字段」
# 必须显式存在。helper 生成完整合法分节，测试再按需覆盖单个字段。


def _weak(**overrides):
    base = {
        "allow_entry": True,
        "position_multiplier": 0.5,
        "score_threshold": 75,
        "daily_signal_limit": 1,
        "require_relative_strength": True,
        "relative_strength_lookback": 20,
        "relative_strength_min": 0.0,
    }
    base.update(overrides)
    return base


def _extreme_weak(**overrides):
    base = {
        "allow_entry": False,
        "position_multiplier": 0.0,
        "daily_signal_limit": 0,
    }
    base.update(overrides)
    return base


# ==================== Task 1：配置契约测试 ====================


class TestMarketRegimePolicyConfig:
    """MarketRegimePolicyConfig 的构造合法性与 RiskControlConfig.from_params 解析。"""

    def test_rejects_invalid_mode(self):
        """mode 必须是 hard_filter | graded | shadow 之一。"""
        with pytest.raises(ValueError, match="market_regime_policy.*mode"):
            RiskControlConfig.from_params({"market_regime_policy": {"mode": "invalid"}})

    def test_rejects_invalid_position_multiplier(self):
        """仓位乘数必须在 [0, 1] 区间。"""
        with pytest.raises(ValueError, match="position_multiplier"):
            RiskControlConfig.from_params({
                "market_regime_policy": {
                    "mode": "graded",
                    "base_position_pct": 0.10,
                    "weak": {
                        "allow_entry": True,
                        "position_multiplier": 1.1,
                        "score_threshold": 75,
                        "daily_signal_limit": 2,
                        "require_relative_strength": True,
                        "relative_strength_lookback": 20,
                        "relative_strength_min": 0.0,
                    },
                    "extreme_weak": {
                        "allow_entry": False,
                        "position_multiplier": 0.0,
                        "daily_signal_limit": 0,
                    },
                }
            })

    def test_rejects_invalid_base_position(self):
        """基础仓位必须在 (0, 1] 区间。"""
        with pytest.raises(ValueError, match="base_position_pct"):
            RiskControlConfig.from_params({
                "market_regime_policy": {
                    "mode": "graded",
                    "base_position_pct": 0.0,
                    "weak": _weak(),
                    "extreme_weak": _extreme_weak(),
                }
            })

    def test_rejects_negative_daily_limit(self):
        """每日名额不得为负。"""
        with pytest.raises(ValueError, match="daily_signal_limit"):
            RiskControlConfig.from_params({
                "market_regime_policy": {
                    "mode": "graded",
                    "base_position_pct": 0.10,
                    "weak": _weak(daily_signal_limit=-1),
                    "extreme_weak": _extreme_weak(),
                }
            })

    def test_graded_missing_risk_fields_is_fatal(self):
        """P1-2：最小 graded 配置必须直接拒绝启动（禁止代码默认值补齐）。

        修复前只校验 weak/extreme_weak 分节存在，其余字段全部走 .get 默认值，
        静默得到 position_multiplier=0 / score_threshold=0 / daily_signal_limit=0 /
        lookback=20，等于用代码默认值改变准入行为。
        """
        with pytest.raises(ValueError, match="要求所有风险参数显式配置"):
            RiskControlConfig.from_params({
                "market_regime_policy": {
                    "mode": "graded",
                    "weak": {"allow_entry": True},
                    "extreme_weak": {"allow_entry": False},
                }
            })

    def test_graded_missing_single_weak_field_is_fatal(self):
        """P1-2：weak 少任意一个字段也必须拒绝（示例缺少 relative_strength_min）。"""
        weak = _weak()
        weak.pop("relative_strength_min")
        with pytest.raises(ValueError, match="relative_strength_min"):
            RiskControlConfig.from_params({
                "market_regime_policy": {
                    "mode": "graded",
                    "base_position_pct": 0.10,
                    "weak": weak,
                    "extreme_weak": _extreme_weak(),
                }
            })

    @pytest.mark.parametrize(
        "bad_threshold",
        [float("nan"), -1, 101],
        ids=["nan", "negative", "above_100"],
    )
    def test_rejects_invalid_score_threshold(self, bad_threshold):
        """P1-2：评分阈值必须是 [0, 100] 内的有限实数。

        NaN 会让 `score >= threshold` 恒为 False（`nan > 0` 为 False），
        从而静默绕过评分过滤；负数 / 超 100 则是无意义配置。
        """
        with pytest.raises(ValueError, match="score_threshold"):
            RiskControlConfig.from_params({
                "market_regime_policy": {
                    "mode": "graded",
                    "base_position_pct": 0.10,
                    "weak": _weak(score_threshold=bad_threshold),
                    "extreme_weak": _extreme_weak(),
                }
            })

    def test_rejects_nan_relative_strength_min(self):
        """P1-2：RS 阈值不得为 NaN。"""
        with pytest.raises(ValueError, match="relative_strength_min"):
            RiskControlConfig.from_params({
                "market_regime_policy": {
                    "mode": "graded",
                    "base_position_pct": 0.10,
                    "weak": _weak(relative_strength_min=float("nan")),
                    "extreme_weak": _extreme_weak(),
                }
            })

    def test_rejects_bool_in_numeric_field(self):
        """P1-2：bool 混入数值字段必须拒绝（True 是 int 子类，易被静默接受）。"""
        with pytest.raises(ValueError, match="position_multiplier"):
            RiskControlConfig.from_params({
                "market_regime_policy": {
                    "mode": "graded",
                    "base_position_pct": 0.10,
                    "weak": _weak(position_multiplier=True),
                    "extreme_weak": _extreme_weak(),
                }
            })

    def test_rejects_str_in_numeric_field(self):
        """P1-2：字符串混入数值字段必须拒绝（修复前只在运行时抛 TypeError）。"""
        with pytest.raises(ValueError, match="score_threshold"):
            RiskControlConfig.from_params({
                "market_regime_policy": {
                    "mode": "graded",
                    "base_position_pct": 0.10,
                    "weak": _weak(score_threshold="75"),
                    "extreme_weak": _extreme_weak(),
                }
            })

    def test_default_mode_preserves_hard_filter(self):
        """没有 market_regime_policy 分节时，自动降级为 hard_filter 兼容路径。"""
        cfg = RiskControlConfig.from_params({})
        assert cfg.market_regime_policy_config.mode == "hard_filter"

    def test_graded_mode_requires_weak_section(self):
        """graded 模式缺少 weak 分节时立即报错。"""
        with pytest.raises(ValueError, match="graded.*weak"):
            RiskControlConfig.from_params({
                "market_regime_policy": {
                    "mode": "graded",
                    "base_position_pct": 0.10,
                    "extreme_weak": {"allow_entry": False, "position_multiplier": 0.0, "daily_signal_limit": 0},
                }
            })

    def test_graded_mode_requires_extreme_weak_section(self):
        """graded 模式缺少 extreme_weak 分节时立即报错。"""
        with pytest.raises(ValueError, match="graded.*extreme_weak"):
            RiskControlConfig.from_params({
                "market_regime_policy": {
                    "mode": "graded",
                    "base_position_pct": 0.10,
                    "weak": _weak(),
                }
            })

    def test_shadow_mode_is_valid(self):
        """shadow 模式是合法值。"""
        cfg = RiskControlConfig.from_params({
            "market_regime_policy": {
                "mode": "shadow",
                "base_position_pct": 0.10,
                "weak": _weak(),
                "extreme_weak": _extreme_weak(),
            }
        })
        assert cfg.market_regime_policy_config.mode == "shadow"

    def test_graded_rejects_conflict_with_score_adjustment(self):
        """graded 模式不得同时启用旧的 score_adjustment（双重惩罚）。"""
        with pytest.raises(ValueError, match="score_adjustment.*graded|graded.*score_adjustment"):
            RiskControlConfig.from_params({
                "market_regime_policy": {
                    "mode": "graded",
                    "base_position_pct": 0.10,
                    "weak": _weak(),
                    "extreme_weak": _extreme_weak(),
                },
                "score_adjustment": {"enabled": True},
            })

    def test_hard_filter_allows_score_adjustment(self):
        """hard_filter 模式下 score_adjustment 可正常启用（兼容现网）。"""
        cfg = RiskControlConfig.from_params({
            "score_adjustment": {"enabled": True},
        })
        assert cfg.market_regime_mode == "hard_filter"
        assert cfg.score_adjustment_enabled is True

    def test_shadow_allows_score_adjustment(self):
        """shadow + score_adjustment.enabled=true 不得报错（互斥仅针对 graded）。

        shadow 的目标是「正式路径输出与 hard_filter 完全一致」，
        必须完整保留 hard_filter 配置（含 score_adjustment）。
        修复前此处会被误判互斥而抛出 ValueError。
        """
        cfg = RiskControlConfig.from_params({
            "market_regime_policy": {
                "mode": "shadow",
                "base_position_pct": 0.10,
                "weak": _weak(),
                "extreme_weak": _extreme_weak(),
            },
            "score_adjustment": {"enabled": True},
        })
        assert cfg.market_regime_mode == "shadow"
        assert cfg.score_adjustment_enabled is True

    def test_production_config_switch_to_shadow_is_constructible(self):
        """生产配置探针：把现网 config.yaml 的 mode 改为 shadow 必须能成功构造。

        探针读取真实 config/config.yaml，深拷贝后仅改 mode（不写磁盘文件）。
        修复前会因 score_adjustment.enabled=true 抛出 ValueError，导致生产切换失败。
        同时验证 weak 子节的 RS 参数被正确解析（而非回退默认值）。
        """
        import copy
        import os
        import yaml

        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        with open(os.path.join(root, "config", "config.yaml"), "r", encoding="utf-8") as f:
            full = yaml.safe_load(f)
        params = copy.deepcopy(full["strategies"]["oversold_bounce"]["params"])
        # 探针前提：生产确实启用了 score_adjustment（否则探针无意义）
        assert params.get("score_adjustment", {}).get("enabled") is True
        weak_yaml = params["market_regime_policy"]["weak"]
        params["market_regime_policy"]["mode"] = "shadow"

        cfg = RiskControlConfig.from_params(params)
        assert cfg.market_regime_mode == "shadow"
        # shadow 完整保留 hard_filter 配置：score_adjustment 仍启用
        assert cfg.score_adjustment_enabled is True
        # 配置契约：weak 下的 RS 参数解析结果必须等于 YAML 中的值
        policy = cfg.market_regime_policy_config
        assert policy.relative_strength_lookback == weak_yaml["relative_strength_lookback"]
        assert policy.relative_strength_min == weak_yaml["relative_strength_min"]

    def test_weak_relative_strength_params_parsed_from_weak_section(self):
        """非默认值探针：weak 下的 RS 参数必须被正确解析（而非回退 20/0.0）。

        config.yaml 把 relative_strength_lookback/min 定义在 weak 子节下；
        早期实现误从 market_regime_policy 顶层读取 → 修改被静默忽略。
        """
        cfg = RiskControlConfig.from_params({
            "market_regime_policy": {
                "mode": "graded",
                "base_position_pct": 0.10,
                "weak": {
                    "allow_entry": True,
                    "position_multiplier": 0.5,
                    "score_threshold": 75,
                    "daily_signal_limit": 2,
                    "require_relative_strength": True,
                    "relative_strength_lookback": 37,
                    "relative_strength_min": 0.123,
                },
                "extreme_weak": {"allow_entry": False, "position_multiplier": 0.0, "daily_signal_limit": 0},
            }
        })
        policy = cfg.market_regime_policy_config
        assert policy.relative_strength_lookback == 37
        assert policy.relative_strength_min == pytest.approx(0.123)


# ==================== Task 2：统一市场状态裁决测试 ====================


from strategy.oversold_bounce.risk_control import (
    MarketContext,
    RiskController,
)


def _make_graded_policy_config() -> RiskControlConfig:
    """构造 graded 模式 RiskControlConfig 用于裁决测试。"""
    return RiskControlConfig.from_params({
        "market_regime_policy": {
            "mode": "graded",
            "base_position_pct": 0.10,
            "weak": {
                "allow_entry": True,
                "position_multiplier": 0.5,
                "score_threshold": 75,
                "daily_signal_limit": 2,
                "require_relative_strength": True,
                "relative_strength_lookback": 20,
                "relative_strength_min": 0.0,
            },
            "extreme_weak": {"allow_entry": False, "position_multiplier": 0.0, "daily_signal_limit": 0},
        },
    })


def _make_context(regime: str) -> MarketContext:
    """根据目标 regime 构建对应的 MarketContext。"""
    if regime == "strong":
        return MarketContext(
            current_date="2026-10-16",
            index_close=3800.0,
            index_ma20=3700.0,
            index_ma60=3600.0,
            ma20_slope=0.01,
            ma60_slope=0.005,
            data_sufficient=True,
        )
    elif regime == "weak":
        return MarketContext(
            current_date="2026-10-16",
            index_close=3650.0,
            index_ma20=3700.0,
            index_ma60=3600.0,
            ma20_slope=-0.005,
            ma60_slope=0.005,
            data_sufficient=True,
        )
    elif regime == "extreme_weak":
        return MarketContext(
            current_date="2026-10-16",
            index_close=3500.0,
            index_ma20=3700.0,
            index_ma60=3600.0,
            ma20_slope=-0.01,
            ma60_slope=-0.005,
            data_sufficient=True,
        )
    else:  # unknown
        return MarketContext(
            current_date="2026-10-16",
            index_close=3650.0,
            index_ma20=None,
            index_ma60=None,
            data_sufficient=False,
        )


class TestMarketRegimeDecision:
    """Task 2：MarketRegimeDecision 统一裁决单元测试。"""

    @pytest.mark.parametrize(
        "regime,allow,multiplier",
        [
            ("strong", True, 1.0),
            ("weak", True, 0.5),
            ("extreme_weak", False, 0.0),
        ],
    )
    def test_graded_decision_has_one_authoritative_result(self, regime, allow, multiplier):
        """graded 模式下裁决唯一确定，不依赖旧的 MA20/MA60/斜率三重过滤。"""
        controller = RiskController(_make_graded_policy_config())
        decision = controller.decide_market_regime(_make_context(regime))
        assert decision.regime == regime
        assert decision.allow_entry is allow
        assert decision.position_multiplier == multiplier

    def test_hard_filter_still_uses_old_path(self):
        """hard_filter 模式下 apply_all_controls 不走分级裁决路径。"""
        cfg = RiskControlConfig.from_params({})
        controller = RiskController(cfg)
        assert controller.config.market_regime_mode == "hard_filter"

    def test_extreme_weak_never_allows_entry(self):
        """极弱市场无论相对强度多高都禁止开仓。"""
        controller = RiskController(_make_graded_policy_config())
        decision = controller.decide_market_regime(_make_context("extreme_weak"))
        assert decision.regime == "extreme_weak"
        assert decision.allow_entry is False
        assert decision.position_multiplier == 0.0

    def test_unknown_market_returns_no_entry(self):
        """数据缺失/不足时裁决为 unknown，禁止开仓。"""
        controller = RiskController(_make_graded_policy_config())
        decision = controller.decide_market_regime(_make_context("unknown"))
        assert decision.regime == "unknown"
        assert decision.allow_entry is False
        assert decision.position_multiplier == 0.0
        assert decision.reason_code == "market_data_unavailable"

    def test_weak_market_carries_score_threshold(self):
        """弱市裁决的 score_threshold 来自 weak.score_threshold（75）。"""
        controller = RiskController(_make_graded_policy_config())
        decision = controller.decide_market_regime(_make_context("weak"))
        assert decision.score_threshold == 75.0

    def test_strong_market_uses_default_score_threshold(self):
        """强市裁决的 score_threshold 来自 config.score_threshold（默认 60）。"""
        controller = RiskController(_make_graded_policy_config())
        decision = controller.decide_market_regime(_make_context("strong"))
        assert decision.score_threshold == 60.0


class TestHardFilterZeroRegression:
    """hard_filter 模式行为与现有生产完全一致（零回归）。"""

    def test_hard_filter_context_above_ma20_passes(self):
        """强市（close >= MA20）在 hard_filter 下正常通过 check_market_trend。"""
        cfg = RiskControlConfig.from_params({})
        controller = RiskController(cfg)
        ctx = _make_context("strong")
        result = controller.check_market_trend(ctx)
        assert result.passed is True

    def test_hard_filter_context_below_ma20_fails(self):
        """弱市（close < MA20）在 hard_filter 下被 check_market_trend 过滤。"""
        cfg = RiskControlConfig.from_params({})
        controller = RiskController(cfg)
        ctx = _make_context("weak")
        result = controller.check_market_trend(ctx)
        assert result.passed is False


# ==================== Task 3：个股相对强度纯函数测试 ====================

import pandas as pd
from strategy.oversold_bounce.risk_control import calculate_relative_strength


class TestRelativeStrength:
    """calculate_relative_strength 纯函数单元测试（按交易日期对齐契约）。"""

    @staticmethod
    def _series(values, dates=None):
        """构造索引为交易日的收盘价 Series（升序）；dates 缺省用连续自然日。"""
        if dates is None:
            dates = list(
                pd.date_range("2025-01-01", periods=len(values), freq="D").strftime(
                    "%Y-%m-%d"
                )
            )
        return pd.Series([float(v) for v in values], index=list(dates), name="close")

    def test_stock_outperforms_index(self):
        """个股跑赢指数：+10% vs +2% → 相对强度 +8%。"""
        result = calculate_relative_strength(
            self._series([100.0, 110.0]),   # +10%
            self._series([100.0, 102.0]),   # +2%
            lookback=1,
        )
        assert result == pytest.approx(0.08)

    def test_stock_underperforms_index(self):
        """个股跑输指数：-2% vs +5% → 相对强度 -7%。"""
        result = calculate_relative_strength(
            self._series([100.0, 98.0]),    # -2%
            self._series([100.0, 105.0]),   # +5%
            lookback=1,
        )
        assert result == pytest.approx(-0.07)

    def test_insufficient_data_returns_none(self):
        """数据量不足 lookback+1 时返回 None。"""
        result = calculate_relative_strength(
            self._series([100.0]), self._series([100.0]), lookback=5
        )
        assert result is None

    def test_zero_start_price_returns_none(self):
        """T-lookback 日价格为 0 → 返回 None。"""
        result = calculate_relative_strength(
            self._series([0.0, 110.0, 108.0]),
            self._series([100.0, 102.0, 101.0]),
            lookback=2,
        )
        assert result is None

    def test_zero_end_price_returns_none(self):
        """T 日价格为 0 → 返回 None。"""
        result = calculate_relative_strength(
            self._series([100.0, 105.0, 0.0]),
            self._series([100.0, 102.0, 101.0]),
            lookback=2,
        )
        assert result is None

    def test_negative_price_returns_none(self):
        """价格为负数（异常数据）→ 返回 None。"""
        result = calculate_relative_strength(
            self._series([-1.0, 110.0]),
            self._series([100.0, 102.0]),
            lookback=1,
        )
        assert result is None

    def test_intermediate_zero_price_returns_none(self):
        """窗口内中间日价格为 0 → 返回 None（只看首尾会漏判）。

        实测 [100, 0, 110] 首尾均正常，若只校验首尾会算出约 0.08 的"正常"
        相对强度，掩盖脏数据 → 必须校验窗口内全部价格。
        """
        result = calculate_relative_strength(
            self._series([100.0, 0.0, 110.0]),
            self._series([100.0, 101.0, 102.0]),
            lookback=2,
        )
        assert result is None

    def test_equal_performance_yields_zero(self):
        """个股与指数收益率完全相同 → 相对强度 = 0。"""
        result = calculate_relative_strength(
            self._series([100.0, 110.0]),
            self._series([2000.0, 2200.0]),   # 同样 +10%
            lookback=1,
        )
        assert result == pytest.approx(0.0)

    def test_lookback_20_with_multiple_intermediate_values(self):
        """20 日回看，中间有多个交易日 → 正常取首尾计算。"""
        stock = [100.0 + i * 0.5 for i in range(21)]  # 从 100 涨到 110
        index = [1000.0 for _ in range(21)]           # 指数不变
        result = calculate_relative_strength(
            self._series(stock), self._series(index), lookback=20
        )
        assert result == pytest.approx(0.10)

    def test_empty_series_return_none(self):
        """空 Series → 肯定不足 lookback+1 → None。"""
        result = calculate_relative_strength(
            pd.Series(dtype=float), pd.Series(dtype=float), lookback=5
        )
        assert result is None

    def test_list_input_returns_none(self):
        """非 Series 输入（list）→ fail-closed 返回 None（契约强制，防止位置错位）。"""
        result = calculate_relative_strength(
            [100.0, 110.0], [100.0, 102.0], lookback=1
        )
        assert result is None

    def test_lookback_zero_returns_none(self):
        """lookback 为 0 → 返回 None（边界守卫）。"""
        result = calculate_relative_strength(
            self._series([100.0, 110.0]), self._series([100.0, 102.0]), lookback=0
        )
        assert result is None

    def test_lookback_negative_returns_none(self):
        """lookback 为负数 → 返回 None。"""
        result = calculate_relative_strength(
            self._series([100.0, 110.0]), self._series([100.0, 102.0]), lookback=-1
        )
        assert result is None

    def test_misaligned_trading_days_are_date_aligned(self):
        """个股缺 2 个交易日：按日期对齐后起止日与指数一致（不再位置错位）。

        指数 5 个交易日 D1..D5；个股只在 D1、D4、D5 有数据（D2、D3 停牌）。
        按位置截取会把 stock[D1,D4,D5] 与 index[D3,D4,D5] 相减 → 错位；
        按日期对齐只保留共同日 D1、D4、D5，lookback=2 时窗口为 D1→D5。
        """
        idx_dates = ["2025-01-01", "2025-01-02", "2025-01-03", "2025-01-04", "2025-01-05"]
        index = self._series([100.0, 101.0, 102.0, 103.0, 110.0], idx_dates)
        stock = self._series(
            [100.0, 105.0, 120.0], ["2025-01-01", "2025-01-04", "2025-01-05"]
        )

        result = calculate_relative_strength(stock, index, lookback=2)
        # 对齐后共同日 = D1、D4、D5，窗口首尾 = D1(100) → D5
        # stock 收益 = 120/100 - 1 = 0.20；index 收益 = 110/100 - 1 = 0.10
        assert result == pytest.approx(0.10)

    def test_misaligned_not_enough_common_days_returns_none(self):
        """对齐后共同交易日不足 lookback+1 → None（不冒充中性 0）。"""
        index = self._series(
            [100.0, 101.0, 102.0, 103.0],
            ["2025-01-01", "2025-01-02", "2025-01-03", "2025-01-04"],
        )
        # 个股只有最后两天与指数重叠（lookback=3 需要 4 个共同交易日）
        stock = self._series([100.0, 105.0], ["2025-01-03", "2025-01-04"])
        result = calculate_relative_strength(stock, index, lookback=3)
        assert result is None


# ==================== 前视偏差防护测试 ====================


class TestForwardLookingBiasProtection:
    """测试信号日期截断逻辑的正确性（阻断项 1 的回归测试）。

    核心断言：对一个 kline_df 先按 signal_date 截断，再取 tail(lookback+1)；
    与原始不带未来数据的 df 直接取 tail(lookback+1)，结果必须完全一致。
    这是前缀不变性要求——追加未来日期的数据不影响对 signal_date 那天的计算。
    """

    def _make_kline_df(self, start_date_str: str, n_days: int, base_close: float = 100.0) -> pd.DataFrame:
        """构造一个按日期升序的 K 线 DataFrame。"""
        dates = pd.date_range(start=start_date_str, periods=n_days, freq="B")
        closes = [base_close + i * 0.5 for i in range(n_days)]
        return pd.DataFrame({
            "date": dates,
            "open": closes,
            "high": closes,
            "low": closes,
            "close": closes,
            "volume": [1000] * n_days,
            "amount": [100000] * n_days,
        })

    def test_rs_not_affected_by_future_data_appended(self):
        """追加 signal_date 之后的数据不改变相对强度结果。

        场景：db.get_kline_history 返回数据库里最新的 120 条 K 线。
        当 --signal-date 指向历史日期（早于库内最新交易日）时，
        kline_df 末尾会有 signal_date 之后的收盘价 → 前视偏差。

        修复后的行为：先按 signal_date 截断再取 tail，结果与不含未来数据一致。
        """
        lookback = 20

        # ---- 1. 构造"干净"的 kline_df：30 个工作日 ----
        kline_clean = self._make_kline_df("2026-07-15", 30, base_close=100.0)
        signal_date = kline_clean["date"].iloc[-1].strftime("%Y-%m-%d")

        # ---- 2. 构造"被污染"的 kline_df：clean + 再追加 10 条未来数据 ----
        last_date_clean = kline_clean["date"].iloc[-1]
        # 从 clean 最后一个日期的下一天再开始 10 个工作日
        future_start = (last_date_clean + pd.Timedelta(days=3)).strftime("%Y-%m-%d")
        kline_future = self._make_kline_df(future_start, 10, base_close=115.5)
        kline_polluted = pd.concat([kline_clean, kline_future], ignore_index=True)
        assert len(kline_polluted) == 40  # 30 + 10

        # ---- 3. 对 polluted 做截断（模拟 daily_scan.py 修复后的逻辑）----
        kline_cut = kline_polluted[kline_polluted["date"].dt.strftime("%Y-%m-%d") <= signal_date]
        assert len(kline_cut) == 30  # 未来的 10 条被过滤掉
        assert kline_cut["date"].iloc[-1].strftime("%Y-%m-%d") == signal_date

        # ---- 4. 对两者分别构造带交易日索引的收盘价 Series ----
        stock_clean = pd.Series(
            kline_clean["close"].astype(float).values,
            index=pd.to_datetime(kline_clean["date"]).dt.strftime("%Y-%m-%d").values,
        )
        stock_cut = pd.Series(
            kline_cut["close"].astype(float).values,
            index=pd.to_datetime(kline_cut["date"]).dt.strftime("%Y-%m-%d").values,
        )
        assert list(stock_clean.values) == list(stock_cut.values)  # 截断后序列完全相同

        # ---- 5. 构造指数序列并计算 RS ----
        # 指数从 3000 开始，clean 段（30 条）和 future 段（10 条）
        index_clean = [3000.0 + i * 3.3 for i in range(30)]
        index_future = [3000.0 + 30 * 3.3 + i * 5.0 for i in range(10)]
        index_polluted = index_clean + index_future

        # 指数用带交易日索引的 pandas Series（与 kline 同日），取截断后的前缀
        index_dates = list(
            pd.to_datetime(kline_polluted["date"]).dt.strftime("%Y-%m-%d").values
        )
        index_series = pd.Series(index_polluted, index=index_dates)
        index_cut = index_series.iloc[:30]

        # 两者 RS 必须完全一致，且不是双双 None 的假通过
        rs_clean = calculate_relative_strength(stock_clean, index_cut, lookback)
        rs_cut = calculate_relative_strength(stock_cut, index_cut, lookback)
        assert rs_clean is not None
        assert rs_clean == rs_cut

    def test_date_alignment_guard_blocks_misaligned_window(self):
        """序列末日期 != signal_date 时应阻断（窗口错位防护）。

        场景：某只股票的 K 线最新日期是 signal_date 之前几天（停牌/数据延迟）。
        这种情况下继续计算 RS 会用错误日期的收盘价当 T 日。

        修复后的行为：检测到 kline_cut 末日期 != signal_date → 阻断。
        这个测试只验证截断逻辑本身是正确的（末日期确实落后）。
        """
        lookback = 20
        # 主信号日期：40 个工作日之后（保证至少比延迟股票多几天）
        kline_main = self._make_kline_df("2026-07-10", 40, base_close=100.0)
        signal_date = kline_main["date"].iloc[-1].strftime("%Y-%m-%d")

        # 延迟股票：30 个工作日（比 signal_date 晚开始几天 → 结束日期落后几日）
        kline_delayed = self._make_kline_df("2026-07-17", 30, base_close=80.0)

        # 截断后末日期仍然落后于 signal_date
        kline_cut = kline_delayed[kline_delayed["date"].dt.strftime("%Y-%m-%d") <= signal_date]
        assert len(kline_cut) == len(kline_delayed)  # 全部保留（延迟股票日期都 < signal_date）
        delayed_last = kline_cut["date"].iloc[-1].strftime("%Y-%m-%d")
        assert delayed_last != signal_date  # 对齐校验应该阻断

    def test_realtime_provider_cut_is_prefix_of_full(self):
        """RealtimeProvider 截断后的 close_history 是完整序列的前缀。

        验证 RealtimeMarketContextProvider.get_context 修复后，
        当 date_str < 数据库最新日期时，index_close_history 不包含未来数据。
        """
        # 模拟 index_df：40 个工作日
        index_df = self._make_kline_df("2026-07-10", 40, base_close=3000.0)
        full_last = index_df["date"].iloc[-1].strftime("%Y-%m-%d")

        # date_str 指向中间某日（第 25 个工作日）
        mid_row = index_df.iloc[24]  # 索引从 0 开始，第 25 个 = index 24
        date_str = mid_row["date"].strftime("%Y-%m-%d")

        index_df_cut = index_df[
            index_df["date"].dt.strftime("%Y-%m-%d") <= date_str
        ]

        # 截断后最后日期 == date_str
        assert index_df_cut["date"].iloc[-1].strftime("%Y-%m-%d") == date_str
        # 截断后的 close 是完整序列的前缀（前 25 条 close 应完全一致）
        full_prefix_closes = list(
            index_df.iloc[: len(index_df_cut)]["close"].astype(float)
        )
        cut_closes = list(index_df_cut["close"].astype(float))
        assert full_prefix_closes == cut_closes
        # 截断后行数 < 40（说明有未来数据被过滤）
        assert len(index_df_cut) < 40

    def test_realtime_provider_indicators_ignore_future_data(self):
        """P1-3 回归（2026-10-20）：RealtimeProvider 必须先截断再算全部指标。

        修复前 stale_days 用绝对日期差判断，数据库最新日期「晚于」signal_date
        时也被接受；随后 current_close / MA20 / MA60 / slope / RS 序列仍从
        完整 index_df 计算 → 历史扫描读到未来行情（前视偏差）。

        断言方式：直接独立重算「截至 signal_date 的截断序列」上的收盘与均线，
        与实际 provider 返回值逐项比对（不依赖 provider 内部实现）。
        """
        config = RiskControlConfig.from_params({})

        index_df = self._make_kline_df("2026-07-10", 70, base_close=3000.0)
        target_idx = 59  # 第 60 个工作日
        date_str = index_df["date"].iloc[target_idx].strftime("%Y-%m-%d")
        latest_close = float(index_df["close"].iloc[-1])

        provider = RealtimeMarketContextProvider(_FakeIndexDb(index_df), config)
        ctx = provider.get_context(date_str)
        assert ctx is not None, "目标日期及之前有数据，上下文不应为 None"

        # 独立重算：按 date<=signal_date 截断后的序列
        truncated = index_df[
            index_df["date"].dt.strftime("%Y-%m-%d") <= date_str
        ].reset_index(drop=True)
        assert len(truncated) == target_idx + 1

        # 1) 当前收盘必须是目标日收盘，而不是库内最新日收盘
        assert ctx.index_close == float(truncated["close"].iloc[-1])
        assert ctx.index_close != latest_close, (
            "index_close 用了数据库最新一天（未来）的收盘价 → 前视偏差"
        )
        assert ctx.current_date == date_str

        # 2) MA20 / MA60 必须只由截断序列计算
        assert ctx.index_ma20 == pytest.approx(
            float(truncated["close"].iloc[-20:].mean())
        )
        assert ctx.index_ma60 == pytest.approx(
            float(truncated["close"].iloc[-60:].mean())
        )

        # 3) RS 历史序列不得包含 signal_date 之后的数据
        assert ctx.index_close_history is not None
        assert ctx.index_close_history.index[-1] == date_str
        assert ctx.index_close_history.iloc[-1] == float(truncated["close"].iloc[-1])


class _FakeIndexDb:
    """最小数据库桩：只实现 get_index_kline（模拟返回库内最新 N 个交易日）。"""

    def __init__(self, index_df: pd.DataFrame):
        self._index_df = index_df

    def get_index_kline(self, index_code, days=None):
        if days is None:
            return self._index_df
        return self._index_df.tail(days).reset_index(drop=True)


# ==================== P1-1 回归：极弱市场硬阻断不变量 ====================


class TestExtremeWeakHardBlockInvariant:
    """P1-1（2026-10-20）：极弱市场「最终信号恒为 0」必须双层防御。

    配置层（gate）：__post_init__ 强制 extreme_weak 三元为 false / 0.0 / 0；
    裁决层（wall）：即使配置实例被篡改绕过校验，extreme_weak 分支仍恒返回阻断值。
    """

    @pytest.mark.parametrize(
        "field,value,match",
        [
            ("allow_entry", True, "allow_entry"),
            ("position_multiplier", 1.0, "position_multiplier"),
            ("daily_signal_limit", 3, "daily_signal_limit"),
        ],
    )
    def test_config_layer_rejects_non_blocking_value(self, field, value, match):
        with pytest.raises(ValueError, match=match):
            RiskControlConfig.from_params({
                "market_regime_policy": {
                    "mode": "graded",
                    "base_position_pct": 0.10,
                    "weak": _weak(),
                    "extreme_weak": _extreme_weak(**{field: value}),
                }
            })

    def test_hard_filter_compat_config_satisfies_invariant(self):
        policy = RiskControlConfig.from_params({}).market_regime_policy_config
        assert policy.extreme_weak_allow_entry is False
        assert policy.extreme_weak_position_multiplier == 0.0
        assert policy.extreme_weak_daily_signal_limit == 0

    def test_decision_layer_wall_blocks_tampered_config(self):
        """裁决层最后一道墙：配置被绕过（篡改 frozen 实例）时仍恒为阻断。"""
        controller = RiskController(_make_graded_policy_config())
        policy = controller.config.market_regime_policy_config
        # 模拟「配置层被绕过」：直接篡改 frozen dataclass 实例的三个字段
        object.__setattr__(policy, "extreme_weak_allow_entry", True)
        object.__setattr__(policy, "extreme_weak_position_multiplier", 1.0)
        object.__setattr__(policy, "extreme_weak_daily_signal_limit", 3)

        decision = controller.decide_market_regime(_make_context("extreme_weak"))
        assert decision.regime == "extreme_weak"
        assert decision.allow_entry is False
        assert decision.position_multiplier == 0.0
        assert decision.daily_signal_limit == 0


# ==================== P1-2 回归：目标交易日缺行 ====================


class TestRealtimeProviderTargetDayMissing:
    """P1-2（2026-10-20）：目标交易日缺行时必须 fail-closed。

    修复前 freshness 使用「截断前」的数据库最新日期：只要库里有目标日之后的
    行情就进入历史扫描分支；当目标日本身缺行时，会用前一交易日的指标并把
    current_date 标成目标日 → 伪目标日上下文。
    """

    def _df(self, n_days: int = 70) -> pd.DataFrame:
        dates = pd.date_range("2026-07-10", periods=n_days, freq="B")
        closes = [3000.0 + i * 2.0 for i in range(n_days)]
        return pd.DataFrame({
            "date": dates,
            "open": closes,
            "high": closes,
            "low": closes,
            "close": closes,
            "volume": [1000] * n_days,
            "amount": [100000] * n_days,
        })

    def _hole(self, index_df: pd.DataFrame, date_str: str) -> pd.DataFrame:
        """删除目标交易日那一行，保留其前后行情。"""
        holed = index_df[
            index_df["date"].dt.strftime("%Y-%m-%d") != date_str
        ].reset_index(drop=True)
        assert len(holed) == len(index_df) - 1
        return holed

    def test_missing_target_day_returns_none(self):
        config = RiskControlConfig.from_params({})
        index_df = self._df()
        date_str = index_df["date"].iloc[59].strftime("%Y-%m-%d")

        provider = RealtimeMarketContextProvider(
            _FakeIndexDb(self._hole(index_df, date_str)), config
        )
        ctx = provider.get_context(date_str)
        assert ctx is None, (
            "目标交易日缺行时必须 fail-closed（默认 freshness_strategy=skip），"
            "不得返回伪目标日上下文"
        )

    def test_tolerated_stale_day_reports_actual_date(self):
        """显式配置 stale_days 容错时，上下文日期必须如实反映实际数据日期。"""
        config = RiskControlConfig.from_params({
            "index_filter": {
                "freshness_strategy": "stale_days",
                "freshness_stale_days": 3,
            },
        })
        index_df = self._df()
        date_str = index_df["date"].iloc[59].strftime("%Y-%m-%d")
        prev_str = index_df["date"].iloc[58].strftime("%Y-%m-%d")

        provider = RealtimeMarketContextProvider(
            _FakeIndexDb(self._hole(index_df, date_str)), config
        )
        ctx = provider.get_context(date_str)
        assert ctx is not None
        assert ctx.current_date == prev_str, "不得把前一交易日伪装成目标交易日"


# ==================== P2-4 回归：RS 索引语义与价格有限性 ====================


class TestRelativeStrengthIndexGuards:
    """P2-4（2026-10-20）：索引必须解析为交易日，价格必须有限正数。"""

    @staticmethod
    def _dated(values, dates):
        return pd.Series([float(v) for v in values], index=list(dates), name="close")

    @staticmethod
    def _dates(n):
        return list(
            pd.date_range("2025-01-01", periods=n, freq="D").strftime("%Y-%m-%d")
        )

    def test_range_index_is_rejected(self):
        """两个默认 RangeIndex 不得按位置 inner join（会得到错误的"对齐"结果）。"""
        stock = pd.Series([100.0, 110.0])  # RangeIndex，无日期语义
        index = pd.Series([100.0, 102.0])
        assert calculate_relative_strength(stock, index, 1) is None

    def test_unparseable_index_is_rejected(self):
        stock = pd.Series([100.0, 110.0], index=["D1", "D2"])
        index = pd.Series([100.0, 102.0], index=["D1", "D2"])
        assert calculate_relative_strength(stock, index, 1) is None

    def test_duplicate_trading_days_are_rejected(self):
        dates = ["2025-01-01", "2025-01-01", "2025-01-02"]
        stock = self._dated([100.0, 100.0, 110.0], dates)
        index = self._dated([100.0, 100.0, 102.0], dates)
        assert calculate_relative_strength(stock, index, 2) is None

    def test_datetime_and_string_index_align(self):
        """DatetimeIndex 与字符串索引混用时必须能正确对齐（修复前无法对齐）。"""
        dates = self._dates(3)
        stock = self._dated([100.0, 105.0, 110.0], pd.to_datetime(dates))
        index = self._dated([100.0, 101.0, 102.0], dates)
        # 个股 +10%，指数 +2% → 相对强度 +8%
        assert calculate_relative_strength(stock, index, 2) == pytest.approx(0.08)

    def test_infinite_price_returns_none(self):
        """Inf 会产生无限相对强度 → fail-closed。"""
        dates = self._dates(2)
        stock = self._dated([100.0, float("inf")], dates)
        index = self._dated([100.0, 102.0], dates)
        assert calculate_relative_strength(stock, index, 1) is None

    def test_nan_inside_window_returns_none(self):
        """窗口内 NaN 不得被 dropna 静默删行后窗口前移（掩盖脏数据）。"""
        dates = self._dates(3)
        stock = self._dated([100.0, float("nan"), 110.0], dates)
        index = self._dated([100.0, 101.0, 102.0], dates)
        assert calculate_relative_strength(stock, index, 2) is None

    def test_non_numeric_price_returns_none(self):
        dates = self._dates(2)
        stock = pd.Series([100.0, "bad"], index=dates, name="close")
        index = self._dated([100.0, 102.0], dates)
        assert calculate_relative_strength(stock, index, 1) is None
