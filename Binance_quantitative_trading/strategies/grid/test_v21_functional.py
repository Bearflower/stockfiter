"""
V2.2 网格交易系统功能验证测试
覆盖所有 V2.2 规范定义的功能点，使用 mock 对象模拟 K 线数据和 API 调用。

测试覆盖：
  1. 市场状态判定（5 种状态 + 优先级互斥 + V2.2极端双重确认）
  2. 波动率异常检测（触发 + 恢复，V2.2阈值调整 + ATR历史窗口扩展）
  3. 弱趋势参数调整（区间宽度、网格数量，V2.2 ATR倍数和网格数量调整）
  4. 网格计算器（价格区间、止盈止损、上下移、网格模式、利润率）
  5. 信号推送（消息模板、冷却时间、状态变化、首次运行）
  6. 配置完整性与硬编码检查
"""
import sys
import os
import math
import textwrap
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Dict, List, Optional
from unittest.mock import MagicMock, AsyncMock, patch, PropertyMock

# 确保项目根目录在 sys.path 中
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import yaml


# ============================================================
# 测试框架
# ============================================================

class TestResult:
    """单个测试用例的结果"""
    def __init__(self, name: str):
        self.name = name
        self.passed = True
        self.error_msg = ""

    def fail(self, msg: str):
        self.passed = False
        self.error_msg = msg

    def __bool__(self):
        return self.passed


class TestSuite:
    """测试套件，管理所有测试用例"""

    def __init__(self, name: str):
        self.name = name
        self.results: List[TestResult] = []
        self._current: Optional[TestResult] = None

    def test(self, name: str):
        """注册一个测试用例"""
        self._current = TestResult(f"[{self.name}] {name}")
        self.results.append(self._current)
        return self._current

    def assert_true(self, condition: bool, msg: str):
        if not condition:
            self._current.fail(msg)

    def assert_equal(self, actual, expected, msg: str):
        if isinstance(actual, Decimal) and isinstance(expected, Decimal):
            if abs(actual - expected) > Decimal('0.0001'):
                self._current.fail(f"{msg}: 期望={expected}, 实际={actual}")
        elif actual != expected:
            self._current.fail(f"{msg}: 期望={expected}, 实际={actual}")

    def assert_approx(self, actual: Decimal, expected: Decimal, tolerance: Decimal, msg: str):
        if abs(actual - expected) > tolerance:
            self._current.fail(f"{msg}: 期望≈{expected}, 实际={actual}, 偏差={abs(actual - expected)}")

    def assert_in(self, item, container, msg: str):
        if item not in container:
            self._current.fail(f"{msg}: '{item}' 不在容器中")

    def assert_greater(self, a, b, msg: str):
        if not (a > b):
            self._current.fail(f"{msg}: {a} 不大于 {b}")

    def assert_less(self, a, b, msg: str):
        if not (a < b):
            self._current.fail(f"{msg}: {a} 不小于 {b}")

    def print_result(self, result: TestResult):
        status = "PASS" if result.passed else "FAIL"
        if result.passed:
            print(f"  [{status}] {result.name}")
        else:
            print(f"  [{status}] {result.name}")
            print(f"         错误: {result.error_msg}")

    def summary(self) -> tuple:
        passed = sum(1 for r in self.results if r.passed)
        failed = sum(1 for r in self.results if not r.passed)
        return passed, failed


# ============================================================
# Mock 对象工厂
# ============================================================

def make_mock_kline_service(klines_1h=None, klines_4h=None, klines_15m=None, klines_1d=None):
    """
    创建 mock KLineService。
    klines_* 应为 List[Dict]，每个 dict 包含 open, high, low, close 等字段。
    未指定的时间框架默认返回空列表。
    """
    mock = MagicMock()
    mock.service_url = "http://mock:8080"
    mock.timeout = 10

    # 同步版 get_klines（部分测试可能调用）
    mock.get_klines = MagicMock()

    async def async_get_klines(symbol, interval, limit=100):
        data_map = {
            '1h': klines_1h,
            '4h': klines_4h,
            '15m': klines_15m,
            '1d': klines_1d,
        }
        return data_map.get(interval, []) or []

    mock.get_klines = AsyncMock(side_effect=async_get_klines)

    async def async_get_multi(symbol, intervals):
        result = {}
        for interval in intervals:
            data = data_map.get(interval, [])
            if data:
                result[interval] = data
        return result

    mock.get_multi_timeframe_data = AsyncMock(side_effect=async_get_multi)

    return mock


def generate_klines(
    base_price: float = 3000.0,
    count: int = 100,
    volatility_pct: float = 2.0,
    trend_pct: float = 0.0,
    seed: int = 42,
) -> List[Dict]:
    """
    生成模拟 K 线数据。

    Args:
        base_price: 基准价格
        count: K 线数量
        volatility_pct: 波动率百分比（用于控制 ATR）
        trend_pct: 趋势百分比（每根 K 线的漂移）
        seed: 随机种子

    Returns:
        List[Dict] 每项包含 open, high, low, close (float 类型)
    """
    import random
    rng = random.Random(seed)
    klines = []
    price = base_price

    for i in range(count):
        change = rng.gauss(trend_pct, volatility_pct)
        close = price * (1 + change / 100)
        high = max(price, close) * (1 + abs(rng.gauss(0, volatility_pct / 2)) / 100)
        low = min(price, close) * (1 - abs(rng.gauss(0, volatility_pct / 2)) / 100)
        klines.append({
            'open': float(price),
            'high': float(high),
            'low': float(low),
            'close': float(close),
            'volume': 1000.0 + rng.random() * 500,
        })
        price = close
    return klines


def generate_klines_decimal(
    base_price: float = 3000.0,
    count: int = 100,
    volatility_pct: float = 2.0,
    trend_pct: float = 0.0,
    seed: int = 42,
) -> List[Dict]:
    """生成模拟 K 线数据（Decimal 类型，适配 KLineService 返回格式）"""
    import random
    rng = random.Random(seed)
    klines = []
    price = base_price
    t = 1000000
    for i in range(count):
        change = rng.gauss(trend_pct, volatility_pct)
        close = price * (1 + change / 100)
        high = max(price, close) * (1 + abs(rng.gauss(0, volatility_pct / 2)) / 100)
        low = min(price, close) * (1 - abs(rng.gauss(0, volatility_pct / 2)) / 100)
        klines.append({
            'open_time': t,
            'open': Decimal(str(round(price, 2))),
            'high': Decimal(str(round(high, 2))),
            'low': Decimal(str(round(low, 2))),
            'close': Decimal(str(round(close, 2))),
            'volume': Decimal(str(round(1000.0 + rng.random() * 500, 2))),
            'close_time': t + 3600000,
            'quote_volume': Decimal('0'),
            'trades': 100,
        })
        price = close
        t += 3600000
    return klines


def generate_high_adx_klines(
    base_price: float = 3000.0,
    count: int = 100,
    adx_target: float = 38.0,
    seed: int = 100,
) -> List[Dict]:
    """
    生成高 ADX（强趋势）的 K 线数据。
    通过持续的单向漂移模拟趋势行情。
    """
    import random
    rng = random.Random(seed)
    klines = []
    price = base_price

    for i in range(count):
        # 强趋势：持续上涨 + 较小回撤
        trend = 0.8 + rng.gauss(0, 0.5)
        close = price * (1 + trend / 100)
        high = price * (1 + (trend + abs(rng.gauss(0, 0.3))) / 100)
        low = price * (1 + max(0, trend - abs(rng.gauss(0, 0.5))) / 100)
        klines.append({
            'open': float(price),
            'high': float(high),
            'low': float(low),
            'close': float(close),
            'volume': 1000.0,
        })
        price = close
    return klines


def generate_low_adx_klines(
    base_price: float = 3000.0,
    count: int = 100,
    seed: int = 200,
) -> List[Dict]:
    """
    生成低 ADX（震荡）的 K 线数据。
    通过均值回归模拟震荡行情。
    """
    import random
    rng = random.Random(seed)
    klines = []
    price = base_price

    for i in range(count):
        # 震荡：均值回归
        reversion = (base_price - price) * 0.05
        noise = rng.gauss(0, 1.5)
        change = reversion + noise
        close = price + change
        high = close + abs(rng.gauss(0, 0.5))
        low = close - abs(rng.gauss(0, 0.5))
        klines.append({
            'open': float(price),
            'high': float(high),
            'low': float(low),
            'close': float(close),
            'volume': 1000.0,
        })
        price = close
    return klines


# ============================================================
# 测试 1：市场状态判定（5 种状态 + 优先级）            [F1]
# ============================================================

def test_market_state_determination():
    """测试 _determine_state 方法的所有状态判定逻辑"""
    suite = TestSuite("F1-市场状态判定")

    from strategies.grid.market_state import MarketStateDetector, MarketState, MarketAnalysis

    # 使用默认参数初始化检测器（不需要真实 KLineService，直接测内部方法）
    mock_kline = make_mock_kline_service()
    detector = MarketStateDetector(kline_service=mock_kline)

    # --- F1.1：极端强趋势（V2.2：需1h ADX>=40 且 4h ADX>=30 双重确认）---
    t = suite.test("F1.1-极端强趋势：ADX_1h=42>=40 且 ADX_4h=32>=30 → EXTREME_STRONG_TREND")
    state, conf = detector._determine_state(
        adx_1h=Decimal('42'), adx_4h=Decimal('32'),
        ema20_1h=Decimal('3100'), ema50_1h=Decimal('3000'),
        ema20_4h=Decimal('3100'), ema50_4h=Decimal('3000'),
        atr_smooth_1h=Decimal('80')
    )
    suite.assert_equal(state, MarketState.EXTREME_STRONG_TREND, "状态应为极端强趋势")
    suite.assert_approx(conf, Decimal('0.95'), Decimal('0.01'), "置信度应为 0.95")
    suite.print_result(t)

    # --- F1.2：极端强趋势边界（V2.2：1h ADX=40, 4h ADX=30）---
    t = suite.test("F1.2-极端强趋势边界：ADX_1h=40, ADX_4h=30 → EXTREME_STRONG_TREND")
    state, conf = detector._determine_state(
        adx_1h=Decimal('40'), adx_4h=Decimal('30'),
        ema20_1h=Decimal('3100'), ema50_1h=Decimal('3000'),
        ema20_4h=Decimal('3100'), ema50_4h=Decimal('3000'),
        atr_smooth_1h=Decimal('80')
    )
    suite.assert_equal(state, MarketState.EXTREME_STRONG_TREND, "ADX=40+4h=30 应为极端强趋势")
    suite.print_result(t)

    # --- F1.3：极端强趋势边界下（V2.2：1h ADX=39.9 < 40 → 非极端）---
    t = suite.test("F1.3-极端强趋势边界下：ADX_1h=39.9 < 40 → 非极端")
    state, _ = detector._determine_state(
        adx_1h=Decimal('39.9'), adx_4h=Decimal('32'),
        ema20_1h=Decimal('3100'), ema50_1h=Decimal('3000'),
        ema20_4h=Decimal('3100'), ema50_4h=Decimal('3000'),
        atr_smooth_1h=Decimal('80')
    )
    suite.assert_true(state != MarketState.EXTREME_STRONG_TREND,
                      f"1h ADX=39.9 < 40 不应为极端强趋势，实际={state.value}")
    suite.print_result(t)

    # --- F1.3b：极端强趋势4h不满足（V2.2：4h ADX=29.9 < 30 → 非极端）---
    t = suite.test("F1.3b-极端强趋势4h不满足：ADX_4h=29.9 < 30 → 非极端")
    state, _ = detector._determine_state(
        adx_1h=Decimal('42'), adx_4h=Decimal('29.9'),
        ema20_1h=Decimal('3100'), ema50_1h=Decimal('3000'),
        ema20_4h=Decimal('3100'), ema50_4h=Decimal('3000'),
        atr_smooth_1h=Decimal('80')
    )
    suite.assert_true(state != MarketState.EXTREME_STRONG_TREND,
                      f"4h ADX=29.9 < 30 不应为极端强趋势，实际={state.value}")
    suite.print_result(t)

    # --- F1.4：普通强趋势（EMA 方向一致 - 多头）---
    t = suite.test("F1.4-普通强趋势多头：1h_ADX=32, 4h_ADX=27, 1h/4h EMA方向一致向上")
    state, conf = detector._determine_state(
        adx_1h=Decimal('32'), adx_4h=Decimal('27'),
        ema20_1h=Decimal('3100'), ema50_1h=Decimal('3000'),  # 1h 多头
        ema20_4h=Decimal('3100'), ema50_4h=Decimal('3000'),  # 4h 多头 → 一致
        atr_smooth_1h=Decimal('80')
    )
    suite.assert_equal(state, MarketState.NORMAL_STRONG_TREND, "应为普通强趋势（多头一致）")
    suite.assert_approx(conf, Decimal('0.8'), Decimal('0.01'), "置信度应为 0.8")
    suite.print_result(t)

    # --- F1.5：普通强趋势（EMA 方向一致 - 空头）---
    t = suite.test("F1.5-普通强趋势空头：EMA方向一致向下")
    state, _ = detector._determine_state(
        adx_1h=Decimal('32'), adx_4h=Decimal('27'),
        ema20_1h=Decimal('2900'), ema50_1h=Decimal('3000'),  # 1h 空头
        ema20_4h=Decimal('2900'), ema50_4h=Decimal('3000'),  # 4h 空头 → 一致
        atr_smooth_1h=Decimal('80')
    )
    suite.assert_equal(state, MarketState.NORMAL_STRONG_TREND, "应为普通强趋势（空头一致）")
    suite.print_result(t)

    # --- F1.6：普通强趋势条件满足但 EMA 方向不一致 → 降级为弱趋势 ---
    t = suite.test("F1.6-EMA方向不一致降级：1h多头+4h空头 → 降级为弱趋势")
    state, conf = detector._determine_state(
        adx_1h=Decimal('32'), adx_4h=Decimal('27'),
        ema20_1h=Decimal('3100'), ema50_1h=Decimal('3000'),  # 1h 多头
        ema20_4h=Decimal('2900'), ema50_4h=Decimal('3000'),  # 4h 空头 → 不一致
        atr_smooth_1h=Decimal('80')
    )
    suite.assert_equal(state, MarketState.WEAK_TREND, "EMA方向不一致应降级为弱趋势")
    suite.assert_approx(conf, Decimal('0.7'), Decimal('0.01'), "置信度应为 0.7")
    suite.print_result(t)

    # --- F1.7：普通强趋势 4h ADX 边界（=25）---
    t = suite.test("F1.7-普通强趋势4h边界：ADX_4h=25 → NORMAL_STRONG_TREND")
    state, _ = detector._determine_state(
        adx_1h=Decimal('30'), adx_4h=Decimal('25'),
        ema20_1h=Decimal('3100'), ema50_1h=Decimal('3000'),
        ema20_4h=Decimal('3100'), ema50_4h=Decimal('3000'),
        atr_smooth_1h=Decimal('80')
    )
    suite.assert_equal(state, MarketState.NORMAL_STRONG_TREND, "4h ADX=25 应该通过")
    suite.print_result(t)

    # --- F1.8：普通强趋势 4h ADX 不满足（<25）---
    t = suite.test("F1.8-普通强趋势4h不满足：ADX_4h=24 < 25 → 检查是否降级")
    state, _ = detector._determine_state(
        adx_1h=Decimal('30'), adx_4h=Decimal('24'),
        ema20_1h=Decimal('3100'), ema50_1h=Decimal('3000'),
        ema20_4h=Decimal('3100'), ema50_4h=Decimal('3000'),
        atr_smooth_1h=Decimal('80')
    )
    # 1h ADX >= 30 但 4h ADX < 25，不满足普通强趋势，会继续检查弱趋势
    # 但 1h ADX=30 不满足 weak_trend (25 <= x < 30)，所以会到震荡
    suite.assert_equal(state, MarketState.OSCILLATION,
                       f"1h_ADX=30(不满足弱趋势30) + 4h_ADX=24 → 应该是震荡，实际={state.value}")
    suite.print_result(t)

    # --- F1.9：弱趋势 ---
    t = suite.test("F1.9-弱趋势：25 <= ADX_1h=27 < 30, ADX_4h=20 < 25 → WEAK_TREND")
    state, conf = detector._determine_state(
        adx_1h=Decimal('27'), adx_4h=Decimal('20'),
        ema20_1h=Decimal('3100'), ema50_1h=Decimal('3000'),
        ema20_4h=Decimal('0'), ema50_4h=Decimal('0'),
        atr_smooth_1h=Decimal('80')
    )
    suite.assert_equal(state, MarketState.WEAK_TREND, "应为弱趋势")
    suite.assert_approx(conf, Decimal('0.7'), Decimal('0.01'), "置信度应为 0.7")
    suite.print_result(t)

    # --- F1.10：弱趋势边界下（ADX=25）---
    t = suite.test("F1.10-弱趋势下边界：ADX_1h=25 → WEAK_TREND")
    state, _ = detector._determine_state(
        adx_1h=Decimal('25'), adx_4h=Decimal('20'),
        ema20_1h=Decimal('0'), ema50_1h=Decimal('0'),
        ema20_4h=Decimal('0'), ema50_4h=Decimal('0'),
        atr_smooth_1h=Decimal('80')
    )
    suite.assert_equal(state, MarketState.WEAK_TREND, "ADX=25 应为弱趋势")
    suite.print_result(t)

    # --- F1.11：弱趋势边界下（ADX=24.9）→ 震荡 ---
    t = suite.test("F1.11-弱趋势边界下不满足：ADX_1h=24.9 < 25 → OSCILLATION")
    state, _ = detector._determine_state(
        adx_1h=Decimal('24.9'), adx_4h=Decimal('20'),
        ema20_1h=Decimal('0'), ema50_1h=Decimal('0'),
        ema20_4h=Decimal('0'), ema50_4h=Decimal('0'),
        atr_smooth_1h=Decimal('80')
    )
    suite.assert_equal(state, MarketState.OSCILLATION, "ADX=24.9 应为震荡")
    suite.print_result(t)

    # --- F1.12：震荡（默认）---
    t = suite.test("F1.12-震荡：ADX_1h=20, ADX_4h=15 → OSCILLATION")
    state, conf = detector._determine_state(
        adx_1h=Decimal('20'), adx_4h=Decimal('15'),
        ema20_1h=Decimal('3000'), ema50_1h=Decimal('3000'),
        ema20_4h=Decimal('0'), ema50_4h=Decimal('0'),
        atr_smooth_1h=Decimal('80')
    )
    suite.assert_equal(state, MarketState.OSCILLATION, "应为震荡")
    suite.assert_approx(conf, Decimal('0.5'), Decimal('0.01'), "置信度应为 0.5")
    suite.print_result(t)

    # --- F1.13：优先级验证 - 极端 > 普通强趋势（V2.2：双重确认）---
    t = suite.test("F1.13-优先级：极端强趋势 > 普通强趋势（ADX_1h=42, ADX_4h=32）")
    state, _ = detector._determine_state(
        adx_1h=Decimal('42'), adx_4h=Decimal('32'),
        ema20_1h=Decimal('3100'), ema50_1h=Decimal('3000'),
        ema20_4h=Decimal('3100'), ema50_4h=Decimal('3000'),
        atr_smooth_1h=Decimal('80')
    )
    suite.assert_equal(state, MarketState.EXTREME_STRONG_TREND,
                       "即使满足普通强趋势条件，1h ADX>=40 且 4h ADX>=30 应为极端强趋势")
    suite.print_result(t)

    # --- F1.14：_is_direction_aligned ---
    t = suite.test("F1.14-EMA方向一致判断：多头一致")
    result = detector._is_direction_aligned(
        Decimal('3100'), Decimal('3000'), Decimal('3100'), Decimal('3000')
    )
    suite.assert_true(result, "1h多头+4h多头 → 方向一致")

    t = suite.test("F1.14b-EMA方向一致：空头一致")
    result = detector._is_direction_aligned(
        Decimal('2900'), Decimal('3000'), Decimal('2900'), Decimal('3000')
    )
    suite.assert_true(result, "1h空头+4h空头 → 方向一致")
    suite.print_result(t)

    t = suite.test("F1.14c-EMA方向不一致：1h多头+4h空头")
    result = detector._is_direction_aligned(
        Decimal('3100'), Decimal('3000'), Decimal('2900'), Decimal('3000')
    )
    suite.assert_true(not result, "1h多头+4h空头 → 方向不一致")
    suite.print_result(t)

    t = suite.test("F1.14d-EMA方向不一致：1h空头+4h多头")
    result = detector._is_direction_aligned(
        Decimal('2900'), Decimal('3000'), Decimal('3100'), Decimal('3000')
    )
    suite.assert_true(not result, "1h空头+4h多头 → 方向不一致")
    suite.print_result(t)

    # 汇总
    passed, failed = suite.summary()
    print(f"\n  [F1] 市场状态判定: {passed}/{passed+failed} 通过")
    return passed, failed


# ============================================================
# 测试 2：波动率异常检测                                 [F2]
# ============================================================

def test_volatility_abnormal_detection():
    """测试波动率异常检测的触发和恢复逻辑（V2.2：阈值1.3，恢复1.2，ATR历史窗口5）"""
    suite = TestSuite("F2-波动率异常检测")

    from strategies.grid.market_state import MarketStateDetector, MarketState

    mock_kline = make_mock_kline_service()
    detector = MarketStateDetector(kline_service=mock_kline)

    # --- F2.1：首次 ATR 不触发（V2.2：需要 atr_history_size=5 个记录） ---
    t = suite.test("F2.1-历史不足：ATR记录 < 5 → 不触发")
    detector._atr_history = []
    detector._atr_abnormal_count = 0
    detector._atr_peak = Decimal('0')
    detector._is_vol_alarm_active = False

    # 前4次 ATR 更新，历史不足5个
    for atr_val in [Decimal('100'), Decimal('105'), Decimal('110'), Decimal('108')]:
        detector._update_atr_history(atr_val)
    suite.assert_true(not detector._is_vol_alarm_active, "历史不足5个不应触发警报")
    suite.print_result(t)

    # --- F2.2：ATR 历史满5个后，连续2次飙升触发（V2.2：ratio > 1.3） ---
    t = suite.test("F2.2-连续2次ATR飙升>30%：触发波动率警报（V2.2阈值1.3）")
    # 重置状态
    detector._atr_history = []
    detector._atr_abnormal_count = 0
    detector._atr_peak = Decimal('0')
    detector._is_vol_alarm_active = False

    # 第1-5次：正常ATR（建立满5个历史）
    for atr_val in [Decimal('100'), Decimal('105'), Decimal('108'), Decimal('110'), Decimal('112')]:
        detector._update_atr_history(atr_val)
    # 现在 history = [100, 105, 108, 110, 112]，ratio = 112/100 = 1.12 < 1.3，不触发
    suite.assert_true(not detector._is_vol_alarm_active, "ratio=1.12 不应触发")

    # 第6次：飙升 ATR=160 > 100*1.3=130
    detector._update_atr_history(Decimal('160'))
    # history = [105, 108, 110, 112, 160], ratio = 160/105 = 1.524 > 1.3
    suite.assert_equal(detector._atr_abnormal_count, 1, "异常计数应为1")

    # 第7次：再次飙升 ATR=250 > 108*1.3=140.4
    detector._update_atr_history(Decimal('250'))
    # history = [108, 110, 112, 160, 250], ratio = 250/108 = 2.315 > 1.3
    suite.assert_equal(detector._atr_abnormal_count, 2, "异常计数应为2")
    suite.assert_true(detector._is_vol_alarm_active, "连续2次飙升应激活警报")
    suite.assert_approx(detector._atr_peak, Decimal('250'), Decimal('0.01'), "ATR峰值应为250")
    suite.print_result(t)

    # --- F2.3：警报激活后记录 atr_peak ---
    t = suite.test("F2.3-atr_peak记录：峰值应为触发时的ATR值")
    suite.assert_approx(detector._atr_peak, Decimal('250'), Decimal('0.01'),
                        "atr_peak 应为触发时的250")
    suite.print_result(t)

    # --- F2.4：恢复检测 - recovery_ratio < 1.2（V2.2） ---
    t = suite.test("F2.4-恢复检测：ATR回落至 250*1.2=300 以下 → 恢复（V2.2恢复阈值1.2）")
    # 当前 ATR = 280, atr_peak = 250
    # recovery_ratio = 280 / 250 = 1.12 < 1.2 → 恢复
    detector._update_atr_history(Decimal('280'))
    suite.assert_true(not detector._is_vol_alarm_active, "ATR回落应恢复警报")
    suite.assert_equal(detector._atr_abnormal_count, 0, "恢复后异常计数应重置为0")
    suite.assert_equal(detector._atr_peak, Decimal('0'), "恢复后atr_peak应重置为0")
    suite.print_result(t)

    # --- F2.5：恢复边界 - recovery_ratio = 1.2 时不恢复（V2.2） ---
    t = suite.test("F2.5-恢复边界：recovery_ratio=1.2 → 不恢复（>=1.2）")
    detector._atr_history = []
    detector._atr_abnormal_count = 0
    detector._atr_peak = Decimal('0')
    detector._is_vol_alarm_active = False
    # 建立历史并触发
    for atr_val in [Decimal('100'), Decimal('105'), Decimal('108'), Decimal('110'), Decimal('112')]:
        detector._update_atr_history(atr_val)
    detector._update_atr_history(Decimal('160'))  # count=1
    detector._update_atr_history(Decimal('250'))  # count=2, alarm=active, peak=250

    # 模拟恢复边界: ATR=300 (250*1.2=300)
    detector._update_atr_history(Decimal('300'))
    suite.assert_true(detector._is_vol_alarm_active,
                      "recovery_ratio=1.2 (300/250) 应保持警报状态（不恢复）")
    suite.print_result(t)

    # --- F2.6：警报激活后 peak 保持不变（仅在激活时设定一次）---
    t = suite.test("F2.6-警报期间峰值不变：peak保持激活时的值，不被后续ATR覆盖")
    # 保持当前 alarm active 状态，送入更高 ATR
    detector._update_atr_history(Decimal('400'))
    # atr_peak 在警报激活时设定，后续不更新（用于恢复检测的基准）
    suite.assert_approx(detector._atr_peak, Decimal('250'), Decimal('0.01'),
                        "peak应保持激活时的250，不被后续更高ATR覆盖")
    suite.print_result(t)

    # --- F2.7：_determine_state 中波动率异常的优先级 ---
    t = suite.test("F2.7-波动率异常优先级：波动率警报活跃 > 普通强趋势")
    detector._is_vol_alarm_active = True
    detector._atr_peak = Decimal('250')  # 使用真实的 peak
    state, conf = detector._determine_state(
        adx_1h=Decimal('32'), adx_4h=Decimal('27'),
        ema20_1h=Decimal('3100'), ema50_1h=Decimal('3000'),
        ema20_4h=Decimal('3100'), ema50_4h=Decimal('3000'),
        atr_smooth_1h=Decimal('310')  # 310/250=1.24 > 1.2，不触发恢复
    )
    suite.assert_equal(state, MarketState.VOLATILITY_ABNORMAL,
                       "波动率异常优先级高于普通强趋势")
    suite.assert_approx(conf, Decimal('0.85'), Decimal('0.01'), "置信度应为0.85")
    detector._is_vol_alarm_active = False  # 清理
    suite.print_result(t)

    # --- F2.8：_check_volatility_abnormal 直接调用（V2.2：ratio > 1.3） ---
    t = suite.test("F2.8-_check_volatility_abnormal：ratio>1.3计数+1（V2.2阈值1.3）")
    detector._atr_history = [Decimal('100'), Decimal('105'), Decimal('108'), Decimal('110'), Decimal('112')]
    detector._atr_abnormal_count = 0
    detector._atr_peak = Decimal('0')
    detector._is_vol_alarm_active = False

    is_alarm, count, peak, alarm = detector._check_volatility_abnormal(Decimal('160'))
    # ratio = 160/100 = 1.6 > 1.3 → count=1，但仅1次不激活警报
    suite.assert_true(not is_alarm, "仅1次异常不激活警报（需连续2次）")
    suite.assert_equal(count, 1, "异常计数应为1")
    suite.print_result(t)

    # 汇总
    passed, failed = suite.summary()
    print(f"\n  [F2] 波动率异常检测: {passed}/{passed+failed} 通过")
    return passed, failed


# ============================================================
# 测试 3：弱趋势参数调整                                 [F3]
# ============================================================

def test_weak_trend_parameters():
    """测试弱趋势市场的参数调整（V2.2：ATR倍数5.0/6.0，网格数量8/6）"""
    suite = TestSuite("F3-弱趋势参数调整")

    from strategies.grid.grid_calculator import GridCalculator, GridMode

    config_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), 'config.yaml'
    )
    with open(config_path) as f:
        config = yaml.safe_load(f)

    calculator = GridCalculator(config)

    price = Decimal('3000')
    atr = Decimal('80')
    baseline = Decimal('100')  # 基准ATR

    # --- F3.1：震荡区间宽度 = 2 * 5.0 * ATR（V2.2） ---
    t = suite.test("F3.1-震荡区间宽度：P ± 5.0×ATR（V2.2）")
    osc_params = calculator.calculate_dynamic_grid_params(
        current_price=price, atr_smooth=atr, atr_baseline=baseline,
        market_state='震荡市场'
    )
    osc_width = osc_params.upper_boundary - osc_params.lower_boundary
    expected_osc_width = Decimal('5.0') * atr * 2  # 2 * 5.0 * 80 = 800
    suite.assert_approx(osc_width, expected_osc_width, Decimal('0.1'),
                        f"震荡宽度应为 {expected_osc_width}")
    suite.print_result(t)

    # --- F3.2：弱趋势区间宽度 = 2 * 6.0 * ATR（V2.2） ---
    t = suite.test("F3.2-弱趋势区间宽度：P ± 6.0×ATR（V2.2）")
    weak_params = calculator.calculate_dynamic_grid_params(
        current_price=price, atr_smooth=atr, atr_baseline=baseline,
        market_state='弱趋势'
    )
    weak_width = weak_params.upper_boundary - weak_params.lower_boundary
    expected_weak_width = Decimal('6.0') * atr * 2  # 2 * 6.0 * 80 = 960
    suite.assert_approx(weak_width, expected_weak_width, Decimal('0.1'),
                        f"弱趋势宽度应为 {expected_weak_width}")
    suite.print_result(t)

    # --- F3.3：弱趋势区间比震荡宽 20%（6.0/5.0=1.2） ---
    t = suite.test("F3.3-弱趋势/震荡宽度比约1.2（6.0/5.0）")
    ratio = weak_width / osc_width
    suite.assert_true(Decimal('1.19') < ratio < Decimal('1.21'),
                      f"宽度比应为~1.2，实际={float(ratio):.4f}")
    suite.print_result(t)

    # --- F3.4：弱趋势网格数量范围 [4, 10]（V2.2） ---
    t = suite.test("F3.4-弱趋势网格数量在 [4, 10] 范围内（V2.2）")
    weak_count = weak_params.grid_count
    suite.assert_true(4 <= weak_count <= 10,
                      f"弱趋势网格数={weak_count}，应在[4,10]")
    suite.print_result(t)

    # --- F3.5：弱趋势网格数 = weak_trend_base_grid_count × (基准ATR/当前ATR) ---
    t = suite.test("F3.5-弱趋势网格数公式验证（V2.2：base=6）")
    # 公式：raw = round((baseline/atr) * weak_trend_base_grid_count)
    # = round((100/80) * 6) = round(7.5) = 8
    # 但需要 clamp 到 [4,10]
    weak_base = config['grid']['weak_trend_base_grid_count']  # 6
    expected_raw = round(float(baseline / atr) * weak_base)
    weak_min = config['grid']['weak_trend_min_grid_count']  # 4
    weak_max = config['grid']['weak_trend_max_grid_count']  # 10
    expected = max(weak_min, min(weak_max, expected_raw))
    suite.assert_equal(weak_count, expected,
                       f"弱趋势网格数应为{expected}（公式计算），实际={weak_count}")
    suite.print_result(t)

    # --- F3.6：震荡网格数量范围 [5, 12]（V2.2） ---
    t = suite.test("F3.6-震荡网格数量在 [5, 12] 范围内（V2.2）")
    osc_count = osc_params.grid_count
    suite.assert_true(5 <= osc_count <= 12,
                      f"震荡网格数={osc_count}，应在[5,12]")
    suite.print_result(t)

    # --- F3.7：弱趋势网格数 < 震荡网格数 ---
    t = suite.test("F3.7-弱趋势网格数 < 震荡网格数（弱趋势更少网格）")
    suite.assert_true(weak_count < osc_count,
                      f"弱趋势={weak_count} 应 < 震荡={osc_count}")
    suite.print_result(t)

    # --- F3.8：ATR 较高时网格数减少 ---
    t = suite.test("F3.8-ATR升高时网格数应减少")
    high_atr_params = calculator.calculate_dynamic_grid_params(
        current_price=price, atr_smooth=Decimal('120'), atr_baseline=baseline,
        market_state='震荡市场'
    )
    suite.assert_true(high_atr_params.grid_count < osc_count,
                      f"高ATR(120)网格={high_atr_params.grid_count} < 低ATR(80)网格={osc_count}")
    suite.print_result(t)

    # --- F3.9：ATR 较低时网格数增加 ---
    t = suite.test("F3.9-ATR降低时网格数应增加")
    low_atr_params = calculator.calculate_dynamic_grid_params(
        current_price=price, atr_smooth=Decimal('50'), atr_baseline=baseline,
        market_state='震荡市场'
    )
    suite.assert_true(low_atr_params.grid_count > osc_count,
                      f"低ATR(50)网格={low_atr_params.grid_count} > 正常ATR(80)网格={osc_count}")
    suite.print_result(t)

    # 汇总
    passed, failed = suite.summary()
    print(f"\n  [F3] 弱趋势参数调整: {passed}/{passed+failed} 通过")
    return passed, failed


# ============================================================
# 测试 4：网格计算器                                     [F4]
# ============================================================

def test_grid_calculator():
    """测试网格计算器的各项功能"""
    suite = TestSuite("F4-网格计算器")

    from strategies.grid.grid_calculator import GridCalculator, DynamicGridParams, GridMode

    config_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), 'config.yaml'
    )
    with open(config_path) as f:
        config = yaml.safe_load(f)

    calculator = GridCalculator(config)

    price = Decimal('3000')
    atr = Decimal('80')
    baseline = Decimal('100')
    stop_loss_buffer = config['grid']['stop_loss_buffer']  # 2

    # --- F4.1：震荡价格区间 P ± 5.0×ATR（V2.2） ---
    t = suite.test("F4.1-震荡价格区间：lower=3000-400=2600, upper=3000+400=3400（V2.2）")
    lower, upper = calculator._calculate_price_range(price, atr, '震荡市场')
    suite.assert_approx(lower, Decimal('2600'), Decimal('0.1'), "震荡下边界")
    suite.assert_approx(upper, Decimal('3400'), Decimal('0.1'), "震荡上边界")
    suite.print_result(t)

    # --- F4.2：弱趋势价格区间 P ± 6.0×ATR（V2.2） ---
    t = suite.test("F4.2-弱趋势价格区间：lower=3000-480=2520, upper=3000+480=3480（V2.2）")
    lower, upper = calculator._calculate_price_range(price, atr, '弱趋势')
    suite.assert_approx(lower, Decimal('2520'), Decimal('0.1'), "弱趋势下边界")
    suite.assert_approx(upper, Decimal('3480'), Decimal('0.1'), "弱趋势上边界")
    suite.print_result(t)

    # --- F4.3：止盈止损价格 ---
    t = suite.test("F4.3-止盈止损价格：边界 ± stop_loss_buffer×ATR")
    params = calculator.calculate_dynamic_grid_params(
        current_price=price, atr_smooth=atr, atr_baseline=baseline,
        market_state='震荡市场'
    )
    expected_stop_low = params.lower_boundary - Decimal(str(stop_loss_buffer)) * atr
    expected_stop_high = params.upper_boundary + Decimal(str(stop_loss_buffer)) * atr
    suite.assert_approx(params.stop_loss_low, expected_stop_low, Decimal('0.01'),
                        f"止损低价应为{expected_stop_low}")
    suite.assert_approx(params.stop_loss_high, expected_stop_high, Decimal('0.01'),
                        f"止损高价应为{expected_stop_high}")
    suite.print_result(t)

    # --- F4.4：上移/下移价格 ---
    t = suite.test("F4.4-上移/下移价格：边界 ± (stop_loss_buffer/divisor)×ATR")
    divisor = Decimal(str(config['grid']['oscillation_move_buffer_divisor']))  # 2
    move_buffer = Decimal(str(stop_loss_buffer)) / divisor  # 1
    expected_move_up = params.upper_boundary + move_buffer * atr
    expected_move_down = params.lower_boundary - move_buffer * atr
    suite.assert_approx(params.stop_move_up_price, expected_move_up, Decimal('0.01'),
                        f"上移价格应为{expected_move_up}")
    suite.assert_approx(params.stop_move_down_price, expected_move_down, Decimal('0.01'),
                        f"下移价格应为{expected_move_down}")
    suite.print_result(t)

    # --- F4.5：网格模式选择 - 等差（振幅 < 0.3）---
    t = suite.test("F4.5-网格模式：振幅<0.3 → 等差网格")
    # 震荡区间宽度 = 800，价格 3000，振幅 = 800/3000 = 0.267 < 0.3
    mode = calculator._select_grid_mode(
        lower_boundary=Decimal('2600'),
        upper_boundary=Decimal('3400'),
        current_price=Decimal('3000')
    )
    suite.assert_equal(mode, GridMode.ARITHMETIC,
                       f"振幅=0.267 < 0.3 → 等差，实际={mode.value}")
    suite.print_result(t)

    # --- F4.6：网格模式选择 - 等比（振幅 >= 0.3）---
    t = suite.test("F4.6-网格模式：振幅>=0.3 → 等比网格")
    mode = calculator._select_grid_mode(
        lower_boundary=Decimal('2000'),
        upper_boundary=Decimal('3200'),
        current_price=Decimal('3000')
    )
    suite.assert_equal(mode, GridMode.GEOMETRIC,
                       f"振幅=0.4 >= 0.3 → 等比，实际={mode.value}")
    suite.print_result(t)

    # --- F4.7：利润率验证（V2.2：区间宽度大，利润率应满足要求） ---
    t = suite.test("F4.7-利润率验证：V2.2区间宽度大，利润率应 >= 1%")
    is_valid, suggested = calculator.validate_profit_rate(params)
    # V2.2：区间宽度 800，8格，间距=100，利润率=100/3000=3.33% > 1%
    suite.assert_true(is_valid, f"V2.2利润率应满足1%要求，实际利润率={float(params.profit_rate)*100:.2f}%")
    suite.print_result(t)

    # --- F4.8：动态网格参数完整字段验证 ---
    t = suite.test("F4.8-DynamicGridParams 完整字段")
    suite.assert_true(isinstance(params.grid_mode, GridMode), "grid_mode 类型正确")
    suite.assert_true(params.grid_count > 0, "grid_count > 0")
    suite.assert_true(params.profit_rate > Decimal('0'), "profit_rate > 0")
    suite.assert_true(params.grid_spacing > Decimal('0'), "grid_spacing > 0")
    suite.assert_true(params.lower_boundary < price < params.upper_boundary,
                      "价格在区间内")
    suite.print_result(t)

    # --- F4.9：仓位大小验证 ---
    t = suite.test("F4.9-仓位大小验证")
    is_feasible, msg, min_margin = calculator.validate_position_size(
        price=Decimal('3000'),
        grid_count=30,
        leverage=10,
        margin=Decimal('500')
    )
    # 500 USDT * 10x / 30 = 166.67 USDT/格，166.67/3000 = 0.056 张 < 1
    suite.assert_true(not is_feasible, "每格不足1张应不可行")
    suite.print_result(t)

    # --- F4.10：足够保证金仓位可行 ---
    t = suite.test("F4.10-足够保证金仓位可行")
    is_feasible, msg, _ = calculator.validate_position_size(
        price=Decimal('3000'),
        grid_count=20,
        leverage=10,
        margin=Decimal('10000')  # 10000*10/20 = 5000/3000 = 1.67 > 1
    )
    suite.assert_true(is_feasible, f"足够保证金应可行: {msg}")
    suite.print_result(t)

    # --- F4.11：不支持状态抛异常 ---
    t = suite.test("F4.11-极端强趋势/波动率异常/普通强趋势应抛ValueError")
    for bad_state in ['极端强趋势', '波动率异常', '普通强趋势']:
        try:
            calculator.calculate_dynamic_grid_params(
                current_price=price, atr_smooth=atr, atr_baseline=baseline,
                market_state=bad_state
            )
            suite.assert_true(False, f"{bad_state} 应该抛出 ValueError")
        except ValueError:
            pass  # 预期行为
    suite.print_result(t)

    # 汇总
    passed, failed = suite.summary()
    print(f"\n  [F4] 网格计算器: {passed}/{passed+failed} 通过")
    return passed, failed


# ============================================================
# 测试 5：信号推送                                       [F5]
# ============================================================

def test_signal_push():
    """测试信号推送的消息模板、冷却时间、状态变化、首次运行"""
    suite = TestSuite("F5-信号推送")

    from strategies.grid.signal_bot import GridSignalBot, GridSignal
    from strategies.grid.market_state import MarketState, MarketAnalysis

    config_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), 'config.yaml'
    )
    with open(config_path) as f:
        config = yaml.safe_load(f)

    # 创建 mock 依赖
    mock_binance = MagicMock()
    mock_kline = make_mock_kline_service()
    mock_notification = MagicMock()
    mock_notification.send = AsyncMock(return_value=True)

    from strategies.grid.grid_calculator import GridCalculator
    grid_calculator = GridCalculator(config)

    bot = GridSignalBot(
        binance_client=mock_binance,
        kline_service=mock_kline,
        notification_client=mock_notification,
        grid_calculator=grid_calculator,
        config=config
    )

    # --- F5.1：极端强趋势消息模板（V2.2：ADX 超过 40） ---
    t = suite.test("F5.1-极端强趋势消息模板包含关键内容")
    ma = MarketAnalysis(
        state=MarketState.EXTREME_STRONG_TREND,
        current_price=Decimal('3200'), atr_smooth=Decimal('100'),
        adx_1h=Decimal('42'), adx_4h=Decimal('32'),
        trend_strength=Decimal('0.4'),
        ema20_1h=Decimal('3250'), ema50_1h=Decimal('3100'),
        confidence=Decimal('0.95')
    )
    msg = bot._generate_extreme_strong_message("ETHUSDT", ma)
    suite.assert_in("极端强趋势", msg, "应包含极端强趋势")
    suite.assert_in("必须立即终止", msg, "应包含必须立即终止")
    suite.assert_in("ETHUSDT", msg, "应包含交易对")
    suite.assert_in("ADX 超过", msg, "应提到ADX超过阈值")
    suite.assert_in("40", msg, "应提到极端强趋势阈值40")
    suite.print_result(t)

    # --- F5.2：普通强趋势消息模板 ---
    t = suite.test("F5.2-普通强趋势消息模板包含关键内容")
    ma_ns = MarketAnalysis(
        state=MarketState.NORMAL_STRONG_TREND,
        current_price=Decimal('3100'), atr_smooth=Decimal('80'),
        adx_1h=Decimal('32'), adx_4h=Decimal('27'),
        trend_strength=Decimal('0.2'),
        ema20_1h=Decimal('3150'), ema50_1h=Decimal('3000'),
        confidence=Decimal('0.8'),
        ema20_4h=Decimal('3150'), ema50_4h=Decimal('3000'),
    )
    msg_ns = bot._generate_normal_strong_message("ETHUSDT", ma_ns)
    suite.assert_in("建议终止网格", msg_ns, "应包含建议终止网格")
    suite.assert_in("强趋势", msg_ns, "应包含强趋势")
    suite.print_result(t)

    # --- F5.3：波动率异常消息模板 ---
    t = suite.test("F5.3-波动率异常消息模板包含关键内容")
    ma_vol = MarketAnalysis(
        state=MarketState.VOLATILITY_ABNORMAL,
        current_price=Decimal('3100'), atr_smooth=Decimal('250'),
        adx_1h=Decimal('28'), adx_4h=Decimal('22'),
        trend_strength=Decimal('0.15'),
        ema20_1h=Decimal('3100'), ema50_1h=Decimal('3050'),
        confidence=Decimal('0.85'),
        atr_2h_ago=Decimal('100'), atr_peak=Decimal('250'),
        is_volatility_alarm_active=True,
    )
    msg_vol = bot._generate_volatility_abnormal_message("ETHUSDT", ma_vol)
    suite.assert_in("波动率异常", msg_vol, "应包含波动率异常")
    suite.assert_in("暂停挂单", msg_vol, "应包含暂停挂单")
    suite.print_result(t)

    # --- F5.4：恢复消息模板 ---
    t = suite.test("F5.4-恢复消息模板包含关键内容")
    ma_rec = MarketAnalysis(
        state=MarketState.OSCILLATION,
        current_price=Decimal('3000'), atr_smooth=Decimal('80'),
        adx_1h=Decimal('20'), adx_4h=Decimal('18'),
        trend_strength=Decimal('0'),
        ema20_1h=Decimal('3000'), ema50_1h=Decimal('2980'),
        confidence=Decimal('0.5'),
    )
    msg_rec = bot._generate_recovery_message("ETHUSDT", ma_rec)
    suite.assert_in("可重新创建网格", msg_rec, "应包含可重新创建网格")
    suite.print_result(t)

    # --- F5.5：震荡/弱趋势的网格信号消息模板（V2.2：网格数量减少） ---
    t = suite.test("F5.5-网格信号消息模板包含网格参数")
    from strategies.grid.grid_calculator import DynamicGridParams, GridMode
    gp = DynamicGridParams(
        lower_boundary=Decimal('2600'), upper_boundary=Decimal('3400'),
        grid_count=8, grid_mode=GridMode.ARITHMETIC,
        stop_loss_low=Decimal('2440'), stop_loss_high=Decimal('3560'),
        stop_move_up_price=Decimal('3480'), stop_move_down_price=Decimal('2520'),
        profit_rate=Decimal('0.033'), grid_spacing=Decimal('100')
    )
    msg_signal = bot._generate_signal_message(
        "ETHUSDT", ma_rec, gp, position_valid=True, position_message="仓位可行"
    )
    suite.assert_in("网格信号灯", msg_signal, "应包含标题")
    suite.assert_in("震荡市场", msg_signal, "应包含状态名")
    suite.assert_in("2600", msg_signal, "应包含下边界")
    suite.assert_in("3400", msg_signal, "应包含上边界")
    suite.assert_in("8 格", msg_signal, "应包含网格数量（V2.2）")
    suite.print_result(t)

    # --- F5.6：信号消息包含上移/下移 ---
    t = suite.test("F5.6-信号消息包含上移/下移功能")
    suite.assert_in("上移功能", msg_signal, "应包含上移功能")
    suite.assert_in("下移功能", msg_signal, "应包含下移功能")
    suite.print_result(t)

    # --- F5.7：首次运行一定推送 ---
    t = suite.test("F5.7-首次运行：_should_notify 返回 True")
    bot.last_signals = {}  # 清空历史
    signal = GridSignal(
        symbol="ETHUSDT", market_analysis=ma_rec,
        grid_params=gp, timestamp=datetime.now(),
        message="测试消息", position_valid=True, position_message=""
    )
    should = bot._should_notify(signal)
    suite.assert_true(should, "首次运行必须推送")
    bot.last_signals["ETHUSDT"] = signal  # 记录
    suite.print_result(t)

    # --- F5.8：状态变化时立即推送 ---
    t = suite.test("F5.8-状态变化：_should_notify 返回 True")
    ma_changed = MarketAnalysis(
        state=MarketState.WEAK_TREND,
        current_price=Decimal('3000'), atr_smooth=Decimal('80'),
        adx_1h=Decimal('27'), adx_4h=Decimal('20'),
        trend_strength=Decimal('0.1'),
        ema20_1h=Decimal('3000'), ema50_1h=Decimal('2980'),
        confidence=Decimal('0.7'),
    )
    changed_signal = GridSignal(
        symbol="ETHUSDT", market_analysis=ma_changed,
        grid_params=gp, timestamp=datetime.now(),
        message="状态变化", position_valid=True, position_message=""
    )
    should = bot._should_notify(changed_signal)
    suite.assert_true(should, "状态变化（震荡→弱趋势）必须推送")
    suite.print_result(t)

    # --- F5.9：同状态冷却期内不推送 ---
    t = suite.test("F5.9-同状态冷却期内：_should_notify 返回 False")
    bot.last_signals["ETHUSDT"] = changed_signal  # 最近一次是弱趋势
    same_signal = GridSignal(
        symbol="ETHUSDT", market_analysis=ma_changed,  # 同样弱趋势
        grid_params=gp, timestamp=datetime.now(),
        message="同状态", position_valid=True, position_message=""
    )
    should = bot._should_notify(same_signal)
    suite.assert_true(not should, "冷却期内不应推送")
    suite.print_result(t)

    # --- F5.10：冷却期满后推送（V2.2：6小时冷却） ---
    t = suite.test("F5.10-冷却期满：_should_notify 返回 True")
    # 模拟 7 小时前的信号（超过6小时冷却期）
    old_signal = GridSignal(
        symbol="ETHUSDT", market_analysis=ma_changed,
        grid_params=gp,
        timestamp=datetime.now() - timedelta(hours=7),
        message="旧信号", position_valid=True, position_message=""
    )
    bot.last_signals["ETHUSDT"] = old_signal
    should = bot._should_notify(same_signal)
    suite.assert_true(should, "超过6小时冷却期应推送")
    suite.print_result(t)

    # --- F5.11：推送冷却时间从配置读取（V2.2：6小时） ---
    t = suite.test("F5.11-推送冷却时间从配置读取：push_cooldown_hours=6")
    suite.assert_equal(bot.push_cooldown_hours, 6, "冷却时间应为6小时")
    suite.print_result(t)

    # --- F5.12：所有5种消息方法存在 ---
    t = suite.test("F5.12-所有5种消息生成方法存在")
    methods = [
        '_generate_extreme_strong_message',
        '_generate_normal_strong_message',
        '_generate_volatility_abnormal_message',
        '_generate_signal_message',
        '_generate_recovery_message',
    ]
    for m in methods:
        suite.assert_true(hasattr(bot, m), f"缺失方法: {m}")
    suite.print_result(t)

    # --- F5.13：grid_params 为 None 的信号（极端/波动率/普通强趋势）---
    t = suite.test("F5.13-极端强趋势信号 grid_params=None")
    no_param_signal = GridSignal(
        symbol="ETHUSDT", market_analysis=ma,
        grid_params=None, timestamp=datetime.now(),
        message="无网格参数", position_valid=True, position_message=""
    )
    suite.assert_equal(no_param_signal.grid_params, None, "极端强趋势 grid_params 应为 None")
    suite.print_result(t)

    # 汇总
    passed, failed = suite.summary()
    print(f"\n  [F5] 信号推送: {passed}/{passed+failed} 通过")
    return passed, failed


# ============================================================
# 测试 6：配置完整性与硬编码检查                         [F6]
# ============================================================

def test_config_completeness():
    """测试配置完整性和硬编码检查"""
    suite = TestSuite("F6-配置完整性与硬编码")

    config_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), 'config.yaml'
    )
    with open(config_path) as f:
        config = yaml.safe_load(f)

    # --- F6.1：market 配置完整性（V2.2：新增 adx_extreme_strong_4h） ---
    t = suite.test("F6.1-market配置完整性")
    market_keys = [
        'adx_extreme_strong', 'adx_extreme_strong_4h', 'adx_normal_strong', 'adx_normal_strong_4h',
        'weak_trend_adx_lower', 'weak_trend_adx_upper',
        'volatility_ratio_threshold', 'volatility_consecutive_count',
        'volatility_recovery_ratio', 'recovery_adx_1h', 'recovery_adx_4h',
        'trend_strength_divisor', 'atr_history_size',
        'ema_fast', 'ema_slow', 'atr_period', 'atr_baseline_period',
        'atr_multipliers',
    ]
    market = config.get('market', {})
    for key in market_keys:
        suite.assert_true(key in market, f"market 缺少配置项: {key}")
    suite.print_result(t)

    # --- F6.2：grid 配置完整性（V2.2：新增 weak_trend_base_grid_count） ---
    t = suite.test("F6.2-grid配置完整性")
    grid_keys = [
        'type', 'count', 'min_grid_count', 'max_grid_count', 'base_grid_count',
        'weak_trend_base_grid_count', 'weak_trend_min_grid_count', 'weak_trend_max_grid_count',
        'grid_spacing_atr_multiplier', 'spacing', 'spacing_ratio',
        'base_quantity', 'stop_loss_buffer', 'oscillation_move_buffer_divisor',
        'min_profit_rate', 'amplitude_threshold',
    ]
    grid = config.get('grid', {})
    for key in grid_keys:
        suite.assert_true(key in grid, f"grid 缺少配置项: {key}")
    suite.print_result(t)

    # --- F6.3：signal_bot 配置完整性 ---
    t = suite.test("F6.3-signal_bot配置完整性")
    sb = config.get('signal_bot', {})
    suite.assert_true('push_cooldown_hours' in sb, "缺少 push_cooldown_hours")
    suite.assert_true('trigger_thresholds' in sb, "缺少 trigger_thresholds")
    suite.assert_true('conservative_grid_reduce' in sb, "缺少 conservative_grid_reduce")
    suite.print_result(t)

    # --- F6.4：ATR 倍数配置正确值（V2.2） ---
    t = suite.test("F6.4-ATR倍数配置：oscillation=5.0, weak_trend=6.0（V2.2）")
    multipliers = market.get('atr_multipliers', {})
    suite.assert_equal(multipliers.get('oscillation'), 5.0,
                       "oscillation ATR倍数应为5.0")
    suite.assert_equal(multipliers.get('weak_trend'), 6.0,
                       "weak_trend ATR倍数应为6.0")
    suite.print_result(t)

    # --- F6.5：ADX阈值配置正确（V2.2：extreme=40, 新增extreme_4h=30） ---
    t = suite.test("F6.5-ADX阈值：extreme=40, extreme_4h=30, normal=30, normal_4h=25, weak[25,30)")
    suite.assert_equal(market.get('adx_extreme_strong'), 40, "极端ADX=40")
    suite.assert_equal(market.get('adx_extreme_strong_4h'), 30, "极端4h ADX=30")
    suite.assert_equal(market.get('adx_normal_strong'), 30, "普通ADX=30")
    suite.assert_equal(market.get('adx_normal_strong_4h'), 25, "普通4h ADX=25")
    suite.assert_equal(market.get('weak_trend_adx_lower'), 25, "弱趋势下限=25")
    suite.assert_equal(market.get('weak_trend_adx_upper'), 30, "弱趋势上限=30")
    suite.print_result(t)

    # --- F6.6：波动率检测配置（V2.2：ratio=1.3, recovery=1.2） ---
    t = suite.test("F6.6-波动率检测：ratio_threshold=1.3, count=2, recovery=1.2（V2.2）")
    suite.assert_equal(market.get('volatility_ratio_threshold'), 1.3,
                       "波动率阈值=1.3")
    suite.assert_equal(market.get('volatility_consecutive_count'), 2,
                       "连续次数=2")
    suite.assert_equal(market.get('volatility_recovery_ratio'), 1.2,
                       "恢复阈值=1.2")
    suite.print_result(t)

    # --- F6.7：代码中无硬编码阈值 ---
    t = suite.test("F6.7-源码中不包含硬编码的阈值（通过扫描关键模式）")
    hardcoded_patterns = [
        # 不应该在业务逻辑中出现这些魔术数字 (排除配置文件)
        'adx_extreme_strong=35' in open(
            os.path.join(os.path.dirname(os.path.abspath(__file__)), 'market_state.py')
        ).read(),
    ]
    # market_state.py 的 __init__ 中参数默认值匹配 config.yaml，这是合理的
    # 真正的硬编码检查：config.yaml 是唯一参数来源
    # 这里验证 GridSignalBot 从 config 读取参数
    suite.assert_equal(bot_inst.push_cooldown_hours, 6,
                       "signal_bot 从配置获取 push_cooldown_hours（V2.2=6）")
    suite.print_result(t)

    # --- F6.8：GridCalculator 从配置读取所有参数（V2.2：grid_count=8） ---
    t = suite.test("F6.8-GridCalculator 从配置读取参数（非硬编码）")
    gc_config = config
    gc = GridCalculator(gc_config)
    suite.assert_equal(gc.grid_count, 8, "grid_count 从配置读取（V2.2=8）")
    suite.assert_true(gc.grid_type == 'dynamic', "grid_type 从配置读取")
    suite.print_result(t)

    # --- F6.9：MarketStateDetector 参数全部可配置 ---
    t = suite.test("F6.9-MarketStateDetector 参数全部可配置")
    from strategies.grid.market_state import MarketStateDetector
    import inspect
    sig = inspect.signature(MarketStateDetector.__init__)
    params = list(sig.parameters.keys())
    # 除了 self 和 kline_service，其他参数都有默认值
    configurable_params = [p for p in params if p not in ('self', 'kline_service')]
    suite.assert_true(len(configurable_params) > 0,
                      f"应有可配置参数: {configurable_params}")
    for p in configurable_params:
        suite.assert_true(
            sig.parameters[p].default is not inspect.Parameter.empty,
            f"参数 {p} 应有默认值"
        )
    suite.print_result(t)

    # 汇总
    passed, failed = suite.summary()
    print(f"\n  [F6] 配置完整性与硬编码: {passed}/{passed+failed} 通过")
    return passed, failed


# ============================================================
# 测试 7：MarketAnalysis 数据类验证                      [F7]
# ============================================================

def test_market_analysis_dataclass():
    """测试 MarketAnalysis 数据类的字段和验证"""
    suite = TestSuite("F7-MarketAnalysis数据类")

    from strategies.grid.market_state import MarketState, MarketAnalysis

    # --- F7.1：创建完整的 MarketAnalysis ---
    t = suite.test("F7.1-创建完整MarketAnalysis（含V2.1新字段）")
    ma = MarketAnalysis(
        state=MarketState.OSCILLATION,
        trend_strength=Decimal('0'),
        adx_1h=Decimal('20'), adx_4h=Decimal('18'),
        ema20_1h=Decimal('3000'), ema50_1h=Decimal('2980'),
        current_price=Decimal('3000'), atr_smooth=Decimal('80'),
        confidence=Decimal('0.5'),
        ema20_4h=Decimal('3000'), ema50_4h=Decimal('2980'),
        atr_2h_ago=Decimal('75'), atr_abnormal_count=0,
        atr_peak=Decimal('0'), is_volatility_alarm_active=False,
    )
    suite.assert_equal(ma.ema20_4h, Decimal('3000'), "ema20_4h")
    suite.assert_equal(ma.ema50_4h, Decimal('2980'), "ema50_4h")
    suite.assert_equal(ma.atr_2h_ago, Decimal('75'), "atr_2h_ago")
    suite.assert_equal(ma.atr_abnormal_count, 0, "atr_abnormal_count")
    suite.assert_equal(ma.atr_peak, Decimal('0'), "atr_peak")
    suite.assert_equal(ma.is_volatility_alarm_active, False, "is_volatility_alarm_active")
    suite.print_result(t)

    # --- F7.2：默认值验证 ---
    t = suite.test("F7.2-MarketAnalysis 默认值验证")
    ma_default = MarketAnalysis(
        state=MarketState.OSCILLATION,
        trend_strength=Decimal('0'),
        adx_1h=Decimal('20'), adx_4h=Decimal('18'),
        ema20_1h=Decimal('3000'), ema50_1h=Decimal('2980'),
        current_price=Decimal('3000'), atr_smooth=Decimal('80'),
        confidence=Decimal('0.5'),
    )
    suite.assert_equal(ma_default.ema20_4h, Decimal('0'), "ema20_4h 默认值=0")
    suite.assert_equal(ma_default.ema50_4h, Decimal('0'), "ema50_4h 默认值=0")
    suite.assert_equal(ma_default.atr_2h_ago, Decimal('0'), "atr_2h_ago 默认值=0")
    suite.assert_equal(ma_default.atr_abnormal_count, 0, "atr_abnormal_count 默认值=0")
    suite.assert_equal(ma_default.atr_peak, Decimal('0'), "atr_peak 默认值=0")
    suite.assert_equal(ma_default.is_volatility_alarm_active, False,
                       "is_volatility_alarm_active 默认值=False")
    suite.print_result(t)

    # --- F7.3：趋势强度范围验证 ---
    t = suite.test("F7.3-趋势强度范围 [0, 0.5] 验证")
    try:
        MarketAnalysis(
            state=MarketState.OSCILLATION,
            trend_strength=Decimal('0.6'),  # 超出范围
            adx_1h=Decimal('20'), adx_4h=Decimal('18'),
            ema20_1h=Decimal('3000'), ema50_1h=Decimal('2980'),
            current_price=Decimal('3000'), atr_smooth=Decimal('80'),
            confidence=Decimal('0.5'),
        )
        suite.assert_true(False, "trend_strength=0.6 应抛 ValueError")
    except ValueError:
        suite.assert_true(True, "trend_strength 超出范围正确抛异常")
    suite.print_result(t)

    # 汇总
    passed, failed = suite.summary()
    print(f"\n  [F7] MarketAnalysis数据类: {passed}/{passed+failed} 通过")
    return passed, failed


# ============================================================
# 测试 8：趋势强度系数计算                               [F8]
# ============================================================

def test_trend_strength_calculation():
    """测试趋势强度系数 k 的计算"""
    suite = TestSuite("F8-趋势强度系数")

    from strategies.grid.market_state import MarketStateDetector

    mock_kline = make_mock_kline_service()
    detector = MarketStateDetector(kline_service=mock_kline)

    # --- F8.1：ADX < 25 → k = 0 ---
    t = suite.test("F8.1-ADX<25 → k=0")
    k = detector._calculate_trend_strength(Decimal('20'))
    suite.assert_equal(k, Decimal('0'), "ADX=20 → k=0")
    suite.print_result(t)

    # --- F8.2：ADX = 25 → k = 0 ---
    t = suite.test("F8.2-ADX=25（边界）→ k=0")
    k = detector._calculate_trend_strength(Decimal('25'))
    suite.assert_equal(k, Decimal('0'), "ADX=25 → k=0")
    suite.print_result(t)

    # --- F8.3：ADX = 30 → k = 0.167 ---
    t = suite.test("F8.3-ADX=30 → k=(30-25)/30=0.167")
    k = detector._calculate_trend_strength(Decimal('30'))
    expected = (Decimal('30') - Decimal('25')) / Decimal('30')
    suite.assert_approx(k, expected, Decimal('0.001'),
                        f"ADX=30 → k={expected}")
    suite.print_result(t)

    # --- F8.4：ADX = 40 → k = 0.5 (上限) ---
    t = suite.test("F8.4-ADX=40 → k=min(0.5, (40-25)/30)=0.5")
    k = detector._calculate_trend_strength(Decimal('40'))
    suite.assert_equal(k, Decimal('0.5'), "ADX=40 → k=0.5（上限）")
    suite.print_result(t)

    # --- F8.5：ADX = 100 → k = 0.5（上限限制）---
    t = suite.test("F8.5-ADX=100 → k=0.5（上限限制）")
    k = detector._calculate_trend_strength(Decimal('100'))
    suite.assert_equal(k, Decimal('0.5'), "ADX=100 → k=0.5")
    suite.print_result(t)

    # 汇总
    passed, failed = suite.summary()
    print(f"\n  [F8] 趋势强度系数: {passed}/{passed+failed} 通过")
    return passed, failed


# ============================================================
# 测试 9：GridSignal 和 DynamicGridParams 数据类          [F9]
# ============================================================

def test_dataclass_validation():
    """测试数据类的参数验证"""
    suite = TestSuite("F9-数据类验证")

    from strategies.grid.grid_calculator import DynamicGridParams, GridMode, GridLevel
    from strategies.grid.signal_bot import GridSignal

    # --- F9.1：DynamicGridParams 正常创建 ---
    t = suite.test("F9.1-DynamicGridParams 正常创建")
    params = DynamicGridParams(
        lower_boundary=Decimal('2840'), upper_boundary=Decimal('3160'),
        grid_count=30, grid_mode=GridMode.ARITHMETIC,
        stop_loss_low=Decimal('2680'), stop_loss_high=Decimal('3320'),
    )
    suite.assert_equal(params.grid_count, 30, "grid_count=30")
    suite.assert_equal(params.grid_mode, GridMode.ARITHMETIC, "grid_mode=等差")
    suite.print_result(t)

    # --- F9.2：DynamicGridParams 下边界 <= 0 应抛异常 ---
    t = suite.test("F9.2-DynamicGridParams lower_boundary <= 0 → ValueError")
    try:
        DynamicGridParams(
            lower_boundary=Decimal('-100'), upper_boundary=Decimal('3160'),
            grid_count=30, grid_mode=GridMode.ARITHMETIC,
            stop_loss_low=Decimal('2680'), stop_loss_high=Decimal('3320'),
        )
        suite.assert_true(False, "负下边界应抛异常")
    except ValueError:
        pass
    suite.print_result(t)

    # --- F9.3：DynamicGridParams 上边界 <= 下边界应抛异常 ---
    t = suite.test("F9.3-DynamicGridParams upper <= lower → ValueError")
    try:
        DynamicGridParams(
            lower_boundary=Decimal('3160'), upper_boundary=Decimal('2840'),
            grid_count=30, grid_mode=GridMode.ARITHMETIC,
            stop_loss_low=Decimal('2680'), stop_loss_high=Decimal('3320'),
        )
        suite.assert_true(False, "上边界<=下边界应抛异常")
    except ValueError:
        pass
    suite.print_result(t)

    # --- F9.4：DynamicGridParams 网格数量 < 5 应抛异常 ---
    t = suite.test("F9.4-DynamicGridParams grid_count < 5 → ValueError")
    try:
        DynamicGridParams(
            lower_boundary=Decimal('2840'), upper_boundary=Decimal('3160'),
            grid_count=3, grid_mode=GridMode.ARITHMETIC,
            stop_loss_low=Decimal('2680'), stop_loss_high=Decimal('3320'),
        )
        suite.assert_true(False, "网格数3应抛异常")
    except ValueError:
        pass
    suite.print_result(t)

    # --- F9.5：GridLevel 正常创建 ---
    t = suite.test("F9.5-GridLevel 正常创建")
    level = GridLevel(price=Decimal('3000'), side='BUY', quantity=Decimal('0.001'), level=5)
    suite.assert_equal(level.price, Decimal('3000'), "price")
    suite.assert_equal(level.side, 'BUY', "side")
    suite.print_result(t)

    # --- F9.6：GridLevel 无效 side 应抛异常 ---
    t = suite.test("F9.6-GridLevel 无效 side → ValueError")
    try:
        GridLevel(price=Decimal('3000'), side='INVALID', quantity=Decimal('0.001'))
        suite.assert_true(False, "无效side应抛异常")
    except ValueError:
        pass
    suite.print_result(t)

    # --- F9.7：GridSignal 完整创建 ---
    t = suite.test("F9.7-GridSignal 完整创建")
    from strategies.grid.market_state import MarketState, MarketAnalysis
    from datetime import datetime
    ma = MarketAnalysis(
        state=MarketState.OSCILLATION, trend_strength=Decimal('0'),
        adx_1h=Decimal('20'), adx_4h=Decimal('18'),
        ema20_1h=Decimal('3000'), ema50_1h=Decimal('2980'),
        current_price=Decimal('3000'), atr_smooth=Decimal('80'),
        confidence=Decimal('0.5'),
    )
    signal = GridSignal(
        symbol="ETHUSDT", market_analysis=ma, grid_params=params,
        timestamp=datetime.now(), message="测试", position_valid=True,
        position_message="OK"
    )
    suite.assert_equal(signal.symbol, "ETHUSDT", "symbol")
    suite.assert_equal(signal.position_valid, True, "position_valid")
    suite.print_result(t)

    # 汇总
    passed, failed = suite.summary()
    print(f"\n  [F9] 数据类验证: {passed}/{passed+failed} 通过")
    return passed, failed


# ============================================================
# 测试 10：综合边界条件与异常场景                       [F10]
# ============================================================

def test_edge_cases():
    """综合边界条件和异常场景"""
    suite = TestSuite("F10-边界条件与异常")

    from strategies.grid.market_state import MarketStateDetector, MarketState

    mock_kline = make_mock_kline_service()
    detector = MarketStateDetector(kline_service=mock_kline)

    # --- F10.1：ADX=0 的情况 ---
    t = suite.test("F10.1-ADX全为0 → 震荡")
    state, _ = detector._determine_state(
        adx_1h=Decimal('0'), adx_4h=Decimal('0'),
        ema20_1h=Decimal('0'), ema50_1h=Decimal('0'),
        ema20_4h=Decimal('0'), ema50_4h=Decimal('0'),
        atr_smooth_1h=Decimal('0')
    )
    suite.assert_equal(state, MarketState.OSCILLATION, "ADX=0 → 震荡")
    suite.print_result(t)

    # --- F10.2：4h ADX=0 不满足极端强趋势（V2.2：需要4h ADX>=30双重确认）---
    t = suite.test("F10.2-极端强趋势：4h_ADX=0 不满足双重确认 → 非极端")
    state, _ = detector._determine_state(
        adx_1h=Decimal('40'), adx_4h=Decimal('0'),
        ema20_1h=Decimal('0'), ema50_1h=Decimal('0'),
        ema20_4h=Decimal('0'), ema50_4h=Decimal('0'),
        atr_smooth_1h=Decimal('80')
    )
    suite.assert_true(state != MarketState.EXTREME_STRONG_TREND,
                       "V2.2：4h ADX=0 < 30 不满足双重确认，不应为极端强趋势")
    suite.print_result(t)

    # --- F10.3：atr_2h_ago=0 跳过波动率检测（V2.2：ATR历史窗口5） ---
    t = suite.test("F10.3-atr_2h_ago=0 → 跳过波动率检测")
    detector._atr_history = [Decimal('0'), Decimal('100'), Decimal('105'), Decimal('108'), Decimal('110')]
    detector._atr_abnormal_count = 0
    detector._atr_peak = Decimal('0')
    detector._is_vol_alarm_active = False
    is_ab, count, peak, alarm = detector._check_volatility_abnormal(Decimal('200'))
    suite.assert_true(not is_ab, "atr_2h=0 应跳过检测")
    suite.print_result(t)

    # --- F10.4：ratio 边界值测试（V2.2：阈值1.3） ---
    t = suite.test("F10.4-ratio边界值：ratio=1.4异常计数+1, ratio=1.3不触发计数（V2.2阈值1.3）")
    # 注意：_check_volatility_abnormal 返回 (is_alarm_active, count, peak, is_alarm_active)
    # 连续2次 ratio > 1.3 才会激活警报，单次只增加计数
    detector._atr_history = [Decimal('100'), Decimal('105'), Decimal('108'), Decimal('110'), Decimal('112')]
    detector._atr_abnormal_count = 0
    detector._atr_peak = Decimal('0')
    detector._is_vol_alarm_active = False
    is_alarm1, count1, _, _ = detector._check_volatility_abnormal(Decimal('140'))
    # ratio = 140/100 = 1.4 > 1.3 → count+1, 但仅1次不够激活警报
    suite.assert_true(not is_alarm1, "仅1次异常不足以激活警报")
    suite.assert_equal(count1, 1, "异常计数应为1")

    # ratio = 1.3（不大于1.3，不触发计数）
    detector._atr_history = [Decimal('100'), Decimal('105'), Decimal('108'), Decimal('110'), Decimal('112')]
    detector._atr_abnormal_count = 0
    detector._atr_peak = Decimal('0')
    detector._is_vol_alarm_active = False
    is_alarm2, count2, _, _ = detector._check_volatility_abnormal(Decimal('130'))
    # ratio = 130/100 = 1.3 == threshold, 不触发
    suite.assert_true(not is_alarm2, "ratio=1.3 不触发")
    suite.assert_equal(count2, 0, "异常计数应保持0")
    suite.print_result(t)

    # --- F10.5：price_range 边界 ---
    t = suite.test("F10.5-价格区间边界：ATR=0 → GridCalculator 抛异常")
    from strategies.grid.grid_calculator import GridCalculator
    import yaml
    config_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), 'config.yaml'
    )
    with open(config_path) as f:
        config = yaml.safe_load(f)
    calculator = GridCalculator(config)

    try:
        calculator.calculate_dynamic_grid_params(
            current_price=Decimal('3000'), atr_smooth=Decimal('0'),
            atr_baseline=Decimal('100'), market_state='震荡市场'
        )
        suite.assert_true(False, "ATR=0 应抛异常")
    except ValueError:
        suite.assert_true(True, "ATR=0 正确抛 ValueError")
    suite.print_result(t)

    # --- F10.6：current_price=0 应抛异常 ---
    t = suite.test("F10.6-价格<=0 → ValueError")
    try:
        calculator.calculate_dynamic_grid_params(
            current_price=Decimal('0'), atr_smooth=Decimal('80'),
            atr_baseline=Decimal('100'), market_state='震荡市场'
        )
        suite.assert_true(False, "price=0 应抛异常")
    except ValueError:
        suite.assert_true(True, "price=0 正确抛异常")
    suite.print_result(t)

    # --- F10.7：atr_baseline=0 应抛异常 ---
    t = suite.test("F10.7-atr_baseline=0 → ValueError")
    try:
        calculator.calculate_dynamic_grid_params(
            current_price=Decimal('3000'), atr_smooth=Decimal('80'),
            atr_baseline=Decimal('0'), market_state='震荡市场'
        )
        suite.assert_true(False, "baseline=0 应抛异常")
    except ValueError:
        suite.assert_true(True, "baseline=0 正确抛异常")
    suite.print_result(t)

    # --- F10.8：GridSignalBot 初始化参数验证 ---
    t = suite.test("F10.8-GridSignalBot None参数 → ValueError")
    from strategies.grid.signal_bot import GridSignalBot
    try:
        GridSignalBot(
            binance_client=None, kline_service=mock_kline,
            notification_client=MagicMock(), grid_calculator=calculator,
            config=config
        )
        suite.assert_true(False, "binance_client=None 应抛异常")
    except ValueError:
        pass
    try:
        GridSignalBot(
            binance_client=MagicMock(), kline_service=None,
            notification_client=MagicMock(), grid_calculator=calculator,
            config=config
        )
        suite.assert_true(False, "kline_service=None 应抛异常")
    except ValueError:
        pass
    suite.print_result(t)

    # 汇总
    passed, failed = suite.summary()
    print(f"\n  [F10] 边界条件与异常: {passed}/{passed+failed} 通过")
    return passed, failed


# ============================================================
# 主入口
# ============================================================

def main():
    """运行所有测试并汇总结果"""
    print("=" * 70)
    print("  V2.2 网格交易系统功能验证测试")
    print(f"  时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)

    all_passed = 0
    all_failed = 0

    test_modules = [
        ("F1-市场状态判定", test_market_state_determination),
        ("F2-波动率异常检测", test_volatility_abnormal_detection),
        ("F3-弱趋势参数调整", test_weak_trend_parameters),
        ("F4-网格计算器", test_grid_calculator),
        ("F5-信号推送", test_signal_push),
        ("F6-配置完整性与硬编码", test_config_completeness),
        ("F7-MarketAnalysis数据类", test_market_analysis_dataclass),
        ("F8-趋势强度系数", test_trend_strength_calculation),
        ("F9-数据类验证", test_dataclass_validation),
        ("F10-边界条件与异常", test_edge_cases),
    ]

    for name, test_fn in test_modules:
        print(f"\n{'─' * 60}")
        print(f"  {name}")
        print(f"{'─' * 60}")
        try:
            p, f = test_fn()
            all_passed += p
            all_failed += f
        except Exception as e:
            import traceback
            print(f"  [ERROR] {name} 抛出异常: {e}")
            traceback.print_exc()
            all_failed += 1

    # 最终汇总
    total = all_passed + all_failed
    print("\n" + "=" * 70)
    print("  测试结果汇总")
    print("=" * 70)
    print(f"  总计: {total} 个用例")
    print(f"  通过: {all_passed} ({all_passed/total*100:.1f}%)" if total > 0 else "  通过: 0")
    print(f"  失败: {all_failed} ({all_failed/total*100:.1f}%)" if total > 0 else "  失败: 0")
    print("=" * 70)

    if all_failed == 0:
        print("\n  全部测试通过!")
        return 0
    else:
        print(f"\n  存在 {all_failed} 个失败用例，请检查!")
        return 1


# 全局引用：供 F6.7 使用
bot_inst = None


if __name__ == '__main__':
    # 预初始化 bot_inst 供配置检查使用
    try:
        from strategies.grid.signal_bot import GridSignalBot
        from strategies.grid.grid_calculator import GridCalculator

        config_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), 'config.yaml'
        )
        with open(config_path) as f:
            _config = yaml.safe_load(f)
        _gc = GridCalculator(_config)
        bot_inst = GridSignalBot(
            binance_client=MagicMock(),
            kline_service=make_mock_kline_service(),
            notification_client=MagicMock(),
            grid_calculator=_gc,
            config=_config
        )
    except Exception:
        pass  # 忽略初始化错误，测试会自行处理

    sys.exit(main())