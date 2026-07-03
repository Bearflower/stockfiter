"""
V2.4 网格交易系统全功能点测试
覆盖所有 V2.4 规范定义的功能点，使用 mock 对象模拟 K 线数据和 API 调用。

测试覆盖：
  F1: 市场状态判定（9种状态，含三层预警）
  F2: 优先级测试（价格行为紧急触发 > 15m ADX早期预警 > 1h ADX趋势确认 > 趋势加速 > 极端强趋势 > 普通强趋势 > 波动率异常）
  F3: 趋势加速检测（ADX历史、边界值、重启归零）
  F4: 趋势确认边界（ADX=55触发、54.9不触发、4h无条件）
  F5: 三档冷却时间（alert=1h, normal=6h, tradable=2h）
  F6: 恢复条件（强趋势恢复、波动率异常恢复）
  F7: 配置完整性（V2.4新增参数、版本号2.4.0）
  F8: 通知模板（价格行为紧急触发含"立即终止"、趋势加速含ADX变化量）
  F9: V2.4 三层预警验证（三层优先级、价格变动率计算）
"""
import sys
import os
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Dict, List, Optional
from unittest.mock import MagicMock, AsyncMock

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
            self._current.fail(f"{msg}: 期望~{expected}, 实际={actual}, 偏差={abs(actual - expected)}")

    def assert_in(self, item, container, msg: str):
        if item not in container:
            self._current.fail(f"{msg}: '{item}' 不在容器中")

    def assert_not_in(self, item, container, msg: str):
        if item in container:
            self._current.fail(f"{msg}: '{item}' 不应在容器中")

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

    data_map = {
        '1h': klines_1h,
        '4h': klines_4h,
        '15m': klines_15m,
        '1d': klines_1d,
    }

    async def async_get_klines(symbol, interval, limit=100):
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


def _load_config() -> dict:
    """加载 config.yaml 配置文件"""
    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.yaml')
    with open(config_path, encoding='utf-8') as f:
        return yaml.safe_load(f)


def _create_bot(config: dict = None) -> 'GridSignalBot':
    """创建测试用的 GridSignalBot 实例（使用 mock 依赖）"""
    from strategies.grid.signal_bot import GridSignalBot
    from strategies.grid.grid_calculator import GridCalculator

    if config is None:
        config = _load_config()

    grid_calculator = GridCalculator(config)

    bot = GridSignalBot(
        binance_client=MagicMock(),
        kline_service=make_mock_kline_service(),
        notification_client=MagicMock(),
        grid_calculator=grid_calculator,
        config=config
    )
    return bot


# ============================================================
# F1: 市场状态判定（7种状态）
# ============================================================

def test_f1_market_state_determination():
    """测试9种市场状态判定逻辑（V2.4三层预警架构）"""
    suite = TestSuite("F1-市场状态判定(9种)")

    from strategies.grid.market_state import MarketStateDetector, MarketState

    mock_kline = make_mock_kline_service()
    detector = MarketStateDetector(kline_service=mock_kline)

    _default_v24 = dict(
        adx_15m=Decimal('20'), price_change_1h=Decimal('0'), price_change_15m=Decimal('0')
    )

    # --- F1.1: 价格行为紧急触发：1h变动>=3% ---
    t = suite.test("F1.1-价格行为紧急触发：1h变动=3.5% -> PRICE_EMERGENCY")
    state, conf = detector._determine_state(
        adx_1h=Decimal('30'), adx_4h=Decimal('20'),
        adx_15m=Decimal('30'),
        ema20_1h=Decimal('3100'), ema50_1h=Decimal('3000'),
        ema20_4h=Decimal('3100'), ema50_4h=Decimal('3000'),
        atr_smooth_1h=Decimal('80'),
        price_change_1h=Decimal('0.035'), price_change_15m=Decimal('0')
    )
    suite.assert_equal(state, MarketState.PRICE_EMERGENCY, "1h变动3.5% 应为价格行为紧急触发")
    suite.assert_approx(conf, Decimal('1.0'), Decimal('0.01'), "置信度应为1.0")
    suite.print_result(t)

    # --- F1.2: 15m ADX早期预警：15m ADX>=50 且 1h变动>=1% ---
    t = suite.test("F1.2-15m ADX早期预警：15m ADX=55, 1h变动=1.5% -> EARLY_WARNING_15M")
    state, conf = detector._determine_state(
        adx_1h=Decimal('30'), adx_4h=Decimal('20'),
        adx_15m=Decimal('55'),
        ema20_1h=Decimal('3100'), ema50_1h=Decimal('3000'),
        ema20_4h=Decimal('3100'), ema50_4h=Decimal('3000'),
        atr_smooth_1h=Decimal('80'),
        price_change_1h=Decimal('0.015'), price_change_15m=Decimal('0')
    )
    suite.assert_equal(state, MarketState.EARLY_WARNING_15M, "15m ADX=55 且 1h变动=1.5% 应为早期预警")
    suite.assert_approx(conf, Decimal('0.92'), Decimal('0.01'), "置信度应为0.92")
    suite.print_result(t)

    # --- F1.3: 1h ADX(10)趋势确认：1h ADX >= 55 ---
    t = suite.test("F1.3-1h ADX趋势确认：1h ADX=58 -> TREND_CONFIRMED_1H")
    state, conf = detector._determine_state(
        adx_1h=Decimal('58'), adx_4h=Decimal('35'),
        adx_15m=Decimal('30'),
        ema20_1h=Decimal('3100'), ema50_1h=Decimal('3000'),
        ema20_4h=Decimal('3100'), ema50_4h=Decimal('3000'),
        atr_smooth_1h=Decimal('80'),
        price_change_1h=Decimal('0.005'), price_change_15m=Decimal('0')
    )
    suite.assert_equal(state, MarketState.TREND_CONFIRMED_1H, "1h ADX=58 >= 55 应为趋势确认")
    suite.assert_approx(conf, Decimal('0.95'), Decimal('0.01'), "置信度应为0.95")
    suite.print_result(t)

    # --- F1.4: 趋势急剧增强：2h内1h ADX上升>8点 ---
    t = suite.test("F1.4-趋势急剧增强：ADX从20升至45 -> TREND_ACCELERATING")
    detector2 = MarketStateDetector(kline_service=mock_kline)
    # 填充ADX历史，模拟2h前ADX=20
    detector2._adx_history = [Decimal('20'), Decimal('30'), Decimal('40')]
    state, conf = detector2._determine_state(
        adx_1h=Decimal('45'), adx_4h=Decimal('35'),
        adx_15m=Decimal('30'),
        ema20_1h=Decimal('3100'), ema50_1h=Decimal('3000'),
        ema20_4h=Decimal('3100'), ema50_4h=Decimal('3000'),
        atr_smooth_1h=Decimal('80'),
        price_change_1h=Decimal('0'), price_change_15m=Decimal('0')
    )
    suite.assert_equal(state, MarketState.TREND_ACCELERATING, "ADX上升25点应为趋势急剧增强")
    suite.assert_approx(conf, Decimal('0.9'), Decimal('0.01'), "置信度应为0.9")
    suite.print_result(t)

    # --- F1.5: 极端强趋势：1h ADX >= 40 且 4h ADX >= 30 ---
    t = suite.test("F1.5-极端强趋势：1h ADX=42, 4h ADX=32 -> EXTREME_STRONG_TREND")
    detector3 = MarketStateDetector(kline_service=mock_kline)
    state, conf = detector3._determine_state(
        adx_1h=Decimal('42'), adx_4h=Decimal('32'),
        adx_15m=Decimal('20'),
        ema20_1h=Decimal('3100'), ema50_1h=Decimal('3000'),
        ema20_4h=Decimal('3100'), ema50_4h=Decimal('3000'),
        atr_smooth_1h=Decimal('80'),
        price_change_1h=Decimal('0'), price_change_15m=Decimal('0')
    )
    suite.assert_equal(state, MarketState.EXTREME_STRONG_TREND, "应为极端强趋势")
    suite.assert_approx(conf, Decimal('0.95'), Decimal('0.01'), "置信度应为0.95")
    suite.print_result(t)

    # --- F1.6: 波动率异常 ---
    t = suite.test("F1.6-波动率异常：ATR飙升触发 -> VOLATILITY_ABNORMAL")
    detector4 = MarketStateDetector(kline_service=mock_kline)
    detector4._atr_history = [Decimal('100'), Decimal('105'), Decimal('108'), Decimal('110'), Decimal('112')]
    detector4._atr_abnormal_count = 1
    detector4._is_vol_alarm_active = False
    detector4._atr_peak = Decimal('0')
    detector4._check_volatility_abnormal(Decimal('250'))
    state, conf = detector4._determine_state(
        adx_1h=Decimal('28'), adx_4h=Decimal('22'),
        adx_15m=Decimal('20'),
        ema20_1h=Decimal('3100'), ema50_1h=Decimal('3000'),
        ema20_4h=Decimal('3100'), ema50_4h=Decimal('3000'),
        atr_smooth_1h=Decimal('310'),
        price_change_1h=Decimal('0'), price_change_15m=Decimal('0')
    )
    suite.assert_equal(state, MarketState.VOLATILITY_ABNORMAL, "应为波动率异常")
    suite.assert_approx(conf, Decimal('0.85'), Decimal('0.01'), "置信度应为0.85")
    suite.print_result(t)

    # --- F1.7: 普通强趋势：1h ADX >= 30 且 4h ADX >= 25 且方向一致 ---
    t = suite.test("F1.7-普通强趋势：1h ADX=32, 4h ADX=27, 方向一致 -> NORMAL_STRONG_TREND")
    detector5 = MarketStateDetector(kline_service=mock_kline)
    state, conf = detector5._determine_state(
        adx_1h=Decimal('32'), adx_4h=Decimal('27'),
        adx_15m=Decimal('20'),
        ema20_1h=Decimal('3100'), ema50_1h=Decimal('3000'),
        ema20_4h=Decimal('3100'), ema50_4h=Decimal('3000'),
        atr_smooth_1h=Decimal('80'),
        price_change_1h=Decimal('0'), price_change_15m=Decimal('0')
    )
    suite.assert_equal(state, MarketState.NORMAL_STRONG_TREND, "应为普通强趋势")
    suite.assert_approx(conf, Decimal('0.8'), Decimal('0.01'), "置信度应为0.8")
    suite.print_result(t)

    # --- F1.8: 弱趋势：25 <= 1h ADX < 30 且 4h ADX < 25 ---
    t = suite.test("F1.8-弱趋势：1h ADX=27, 4h ADX=20 -> WEAK_TREND")
    detector6 = MarketStateDetector(kline_service=mock_kline)
    state, conf = detector6._determine_state(
        adx_1h=Decimal('27'), adx_4h=Decimal('20'),
        adx_15m=Decimal('20'),
        ema20_1h=Decimal('3100'), ema50_1h=Decimal('3000'),
        ema20_4h=Decimal('3100'), ema50_4h=Decimal('3000'),
        atr_smooth_1h=Decimal('80'),
        price_change_1h=Decimal('0'), price_change_15m=Decimal('0')
    )
    suite.assert_equal(state, MarketState.WEAK_TREND, "应为弱趋势")
    suite.assert_approx(conf, Decimal('0.7'), Decimal('0.01'), "置信度应为0.7")
    suite.print_result(t)

    # --- F1.9: 震荡：1h ADX < 25 且 4h ADX < 25 ---
    t = suite.test("F1.9-震荡：1h ADX=20, 4h ADX=15 -> OSCILLATION")
    detector7 = MarketStateDetector(kline_service=mock_kline)
    state, conf = detector7._determine_state(
        adx_1h=Decimal('20'), adx_4h=Decimal('15'),
        adx_15m=Decimal('15'),
        ema20_1h=Decimal('3000'), ema50_1h=Decimal('3000'),
        ema20_4h=Decimal('3000'), ema50_4h=Decimal('3000'),
        atr_smooth_1h=Decimal('80'),
        price_change_1h=Decimal('0'), price_change_15m=Decimal('0')
    )
    suite.assert_equal(state, MarketState.OSCILLATION, "应为震荡")
    suite.assert_approx(conf, Decimal('0.5'), Decimal('0.01'), "置信度应为0.5")
    suite.print_result(t)

    # 汇总
    passed, failed = suite.summary()
    print(f"\n  [F1] 市场状态判定(9种): {passed}/{passed+failed} 通过")
    return passed, failed


# ============================================================
# F2: 优先级测试
# ============================================================

def test_f2_priority():
    """测试市场状态优先级：价格行为紧急触发 > 15m ADX早期预警 > 1h ADX趋势确认 > 趋势加速 > 极端强趋势 > 普通强趋势 > 波动率异常"""
    suite = TestSuite("F2-优先级测试")

    from strategies.grid.market_state import MarketStateDetector, MarketState

    mock_kline = make_mock_kline_service()

    # --- F2.1: 价格行为紧急触发优先于15m ADX早期预警 ---
    # 同时满足价格行为紧急触发和15m ADX早期预警 -> 价格行为紧急触发（优先级最高）
    t = suite.test("F2.1-价格行为紧急触发优先于15m ADX早期预警 -> PRICE_EMERGENCY")
    detector = MarketStateDetector(kline_service=mock_kline)
    state, _ = detector._determine_state(
        adx_1h=Decimal('30'), adx_4h=Decimal('20'),
        adx_15m=Decimal('55'),  # 满足15m ADX>=50
        ema20_1h=Decimal('3100'), ema50_1h=Decimal('3000'),
        ema20_4h=Decimal('3100'), ema50_4h=Decimal('3000'),
        atr_smooth_1h=Decimal('80'),
        price_change_1h=Decimal('0.035'), price_change_15m=Decimal('0.02')  # 满足价格行为触发
    )
    suite.assert_equal(state, MarketState.PRICE_EMERGENCY,
                       "价格行为紧急触发优先于15m ADX早期预警")
    suite.print_result(t)

    # --- F2.2: 15m ADX早期预警优先于1h ADX趋势确认 ---
    t = suite.test("F2.2-15m ADX早期预警优先于1h ADX趋势确认 -> EARLY_WARNING_15M")
    detector2 = MarketStateDetector(kline_service=mock_kline)
    state, _ = detector2._determine_state(
        adx_1h=Decimal('58'), adx_4h=Decimal('35'),  # 满足1h ADX趋势确认
        adx_15m=Decimal('55'),  # 满足15m ADX早期预警
        ema20_1h=Decimal('3100'), ema50_1h=Decimal('3000'),
        ema20_4h=Decimal('3100'), ema50_4h=Decimal('3000'),
        atr_smooth_1h=Decimal('80'),
        price_change_1h=Decimal('0.015'), price_change_15m=Decimal('0')  # 满足早期预警的价格变动
    )
    suite.assert_equal(state, MarketState.EARLY_WARNING_15M,
                       "15m ADX早期预警优先于1h ADX趋势确认")
    suite.print_result(t)

    # --- F2.3: 趋势加速优先于极端强趋势 ---
    t = suite.test("F2.3-趋势加速优先于极端强趋势：ADX=42, ADX上升22点 -> TREND_ACCELERATING")
    detector3 = MarketStateDetector(kline_service=mock_kline)
    detector3._adx_history = [Decimal('20'), Decimal('30'), Decimal('40')]
    state, _ = detector3._determine_state(
        adx_1h=Decimal('42'), adx_4h=Decimal('35'),
        adx_15m=Decimal('20'),
        ema20_1h=Decimal('3100'), ema50_1h=Decimal('3000'),
        ema20_4h=Decimal('3100'), ema50_4h=Decimal('3000'),
        atr_smooth_1h=Decimal('80'),
        price_change_1h=Decimal('0'), price_change_15m=Decimal('0')
    )
    suite.assert_equal(state, MarketState.TREND_ACCELERATING,
                       "ADX上升22点 > 8，应为趋势加速（优先于极端强趋势）")
    suite.print_result(t)

    # --- F2.4: 极端强趋势优先于波动率异常 ---
    t = suite.test("F2.4-极端强趋势优先于波动率异常：ADX=42, 4h=32, 波动率异常活跃 -> EXTREME")
    detector4 = MarketStateDetector(kline_service=mock_kline)
    detector4._is_vol_alarm_active = True
    detector4._atr_peak = Decimal('250')
    state, _ = detector4._determine_state(
        adx_1h=Decimal('42'), adx_4h=Decimal('32'),
        adx_15m=Decimal('20'),
        ema20_1h=Decimal('3100'), ema50_1h=Decimal('3000'),
        ema20_4h=Decimal('3100'), ema50_4h=Decimal('3000'),
        atr_smooth_1h=Decimal('280'),
        price_change_1h=Decimal('0'), price_change_15m=Decimal('0')
    )
    suite.assert_equal(state, MarketState.EXTREME_STRONG_TREND,
                       "极端强趋势优先级高于波动率异常")
    suite.print_result(t)

    # --- F2.5: 普通强趋势优先于波动率异常 ---
    t = suite.test("F2.5-普通强趋势优先于波动率异常：同时满足 -> NORMAL_STRONG_TREND")
    detector5 = MarketStateDetector(kline_service=mock_kline)
    detector5._is_vol_alarm_active = True
    detector5._atr_peak = Decimal('250')
    state, _ = detector5._determine_state(
        adx_1h=Decimal('32'), adx_4h=Decimal('27'),
        adx_15m=Decimal('20'),
        ema20_1h=Decimal('3100'), ema50_1h=Decimal('3000'),
        ema20_4h=Decimal('3100'), ema50_4h=Decimal('3000'),
        atr_smooth_1h=Decimal('280'),
        price_change_1h=Decimal('0'), price_change_15m=Decimal('0')
    )
    suite.assert_equal(state, MarketState.NORMAL_STRONG_TREND,
                       "普通强趋势优先级高于波动率异常")
    suite.print_result(t)

    # --- F2.6: 完整优先级链验证 ---
    t = suite.test("F2.6-完整优先级链：价格行为>15m预警>1h确认>加速>极端>普通强趋势>波动率>弱趋势>震荡")
    all_states = list(MarketState)
    suite.assert_equal(len(all_states), 9, "V2.4应有9种市场状态")
    suite.assert_in(MarketState.PRICE_EMERGENCY, all_states, "应包含价格行为紧急触发")
    suite.assert_in(MarketState.EARLY_WARNING_15M, all_states, "应包含15m ADX早期预警")
    suite.assert_in(MarketState.TREND_CONFIRMED_1H, all_states, "应包含1h ADX趋势确认")
    suite.print_result(t)

    # 汇总
    passed, failed = suite.summary()
    print(f"\n  [F2] 优先级测试: {passed}/{passed+failed} 通过")
    return passed, failed


# ============================================================
# F3: 趋势加速检测
# ============================================================

def test_f3_trend_acceleration():
    """测试趋势加速检测逻辑"""
    suite = TestSuite("F3-趋势加速检测")

    from strategies.grid.market_state import MarketStateDetector, MarketState

    mock_kline = make_mock_kline_service()

    # --- F3.1: ADX历史不足3条时不触发 ---
    t = suite.test("F3.1-ADX历史不足3条：不触发趋势加速")
    detector = MarketStateDetector(kline_service=mock_kline)
    detector._adx_history = [Decimal('20'), Decimal('30')]  # 只有2条
    result = detector._check_trend_acceleration(Decimal('45'))
    suite.assert_true(not result, "历史不足3条不应触发趋势加速")
    suite.print_result(t)

    # --- F3.2: ADX历史为空时不触发 ---
    t = suite.test("F3.2-ADX历史为空：不触发趋势加速")
    detector2 = MarketStateDetector(kline_service=mock_kline)
    detector2._adx_history = []
    result = detector2._check_trend_acceleration(Decimal('45'))
    suite.assert_true(not result, "历史为空不应触发趋势加速")
    suite.print_result(t)

    # --- F3.3: ADX上升恰好8点时触发 ---
    t = suite.test("F3.3-ADX上升恰好8点：不触发（需>8）")
    detector3 = MarketStateDetector(kline_service=mock_kline)
    detector3._adx_history = [Decimal('20'), Decimal('24'), Decimal('26')]
    # 当前ADX=28，最旧=20，上升=8，不大于8
    result = detector3._check_trend_acceleration(Decimal('28'))
    suite.assert_true(not result, "ADX上升恰好8点（不大于8）不应触发")
    suite.print_result(t)

    # --- F3.4: ADX上升8.1点时触发 ---
    t = suite.test("F3.4-ADX上升8.1点：触发趋势加速")
    detector4 = MarketStateDetector(kline_service=mock_kline)
    detector4._adx_history = [Decimal('20'), Decimal('24'), Decimal('26')]
    result = detector4._check_trend_acceleration(Decimal('28.1'))
    suite.assert_true(result, "ADX上升8.1点 > 8 应触发趋势加速")
    suite.print_result(t)

    # --- F3.5: ADX上升7.9点时不触发 ---
    t = suite.test("F3.5-ADX上升7.9点：不触发趋势加速")
    detector5 = MarketStateDetector(kline_service=mock_kline)
    detector5._adx_history = [Decimal('20'), Decimal('24'), Decimal('26')]
    result = detector5._check_trend_acceleration(Decimal('27.9'))
    suite.assert_true(not result, "ADX上升7.9点 < 8 不应触发趋势加速")
    suite.print_result(t)

    # --- F3.6: ADX下降时不触发 ---
    t = suite.test("F3.6-ADX下降：不触发趋势加速")
    detector6 = MarketStateDetector(kline_service=mock_kline)
    detector6._adx_history = [Decimal('50'), Decimal('45'), Decimal('42')]
    result = detector6._check_trend_acceleration(Decimal('40'))
    suite.assert_true(not result, "ADX下降不应触发趋势加速")
    suite.print_result(t)

    # --- F3.7: 程序重启后历史归零 ---
    t = suite.test("F3.7-程序重启后历史归零：新实例ADX历史为空")
    detector7 = MarketStateDetector(kline_service=mock_kline)
    suite.assert_equal(len(detector7._adx_history), 0, "新实例ADX历史应为空")
    # 不触发趋势加速
    result = detector7._check_trend_acceleration(Decimal('55'))
    suite.assert_true(not result, "重启后历史为空不应触发趋势加速")
    suite.print_result(t)

    # --- F3.8: _update_adx_history 正确更新 ---
    t = suite.test("F3.8-_update_adx_history：正确追加和截断")
    detector8 = MarketStateDetector(kline_service=mock_kline, adx_history_size=3)
    # 第1次更新
    prev = detector8._update_adx_history(Decimal('20'))
    suite.assert_equal(prev, Decimal('0'), "首次更新，上一次ADX应为0")
    suite.assert_equal(len(detector8._adx_history), 1, "历史长度应为1")

    # 第2次更新
    prev = detector8._update_adx_history(Decimal('30'))
    suite.assert_equal(prev, Decimal('20'), "上一次ADX应为20")
    suite.assert_equal(len(detector8._adx_history), 2, "历史长度应为2")

    # 第3次更新
    prev = detector8._update_adx_history(Decimal('40'))
    suite.assert_equal(prev, Decimal('30'), "上一次ADX应为30")
    suite.assert_equal(len(detector8._adx_history), 3, "历史长度应为3")

    # 第4次更新（超出窗口，截断最旧的）
    prev = detector8._update_adx_history(Decimal('50'))
    suite.assert_equal(prev, Decimal('40'), "上一次ADX应为40")
    suite.assert_equal(len(detector8._adx_history), 3, "历史长度应保持3")
    suite.assert_equal(detector8._adx_history[0], Decimal('30'), "最旧应为30（20被截断）")
    suite.print_result(t)

    # --- F3.9: adx_history_size 可配置 ---
    t = suite.test("F3.9-adx_history_size可配置：默认3，可自定义")
    detector9 = MarketStateDetector(kline_service=mock_kline, adx_history_size=5)
    suite.assert_equal(detector9.adx_history_size, 5, "adx_history_size应为5")
    # 填充5条
    for adx_val in [Decimal('20'), Decimal('25'), Decimal('30'), Decimal('35'), Decimal('40')]:
        detector8._update_adx_history(adx_val)
    suite.assert_equal(len(detector9._adx_history), 0, "新实例历史应为空")
    suite.print_result(t)

    # 汇总
    passed, failed = suite.summary()
    print(f"\n  [F3] 趋势加速检测: {passed}/{passed+failed} 通过")
    return passed, failed


# ============================================================
# F4: 趋势确认边界
# ============================================================

def test_f4_trend_confirmed_boundary():
    """测试1h ADX趋势确认的边界条件（V2.4）"""
    suite = TestSuite("F4-趋势确认边界")

    from strategies.grid.market_state import MarketStateDetector, MarketState

    mock_kline = make_mock_kline_service()

    # --- F4.1: 1h ADX=55 时触发 ---
    t = suite.test("F4.1-1h ADX=55：触发趋势确认")
    detector = MarketStateDetector(kline_service=mock_kline)
    state, _ = detector._determine_state(
        adx_1h=Decimal('55'), adx_4h=Decimal('35'),
        adx_15m=Decimal('20'),
        ema20_1h=Decimal('3100'), ema50_1h=Decimal('3000'),
        ema20_4h=Decimal('3100'), ema50_4h=Decimal('3000'),
        atr_smooth_1h=Decimal('80'),
        price_change_1h=Decimal('0'), price_change_15m=Decimal('0')
    )
    suite.assert_equal(state, MarketState.TREND_CONFIRMED_1H,
                       "1h ADX=55 >= 55 应触发趋势确认")
    suite.print_result(t)

    # --- F4.2: 1h ADX=54.9 时不触发 ---
    t = suite.test("F4.2-1h ADX=54.9：不触发趋势确认")
    detector2 = MarketStateDetector(kline_service=mock_kline)
    state, _ = detector2._determine_state(
        adx_1h=Decimal('54.9'), adx_4h=Decimal('35'),
        adx_15m=Decimal('20'),
        ema20_1h=Decimal('3100'), ema50_1h=Decimal('3000'),
        ema20_4h=Decimal('3100'), ema50_4h=Decimal('3000'),
        atr_smooth_1h=Decimal('80'),
        price_change_1h=Decimal('0'), price_change_15m=Decimal('0')
    )
    suite.assert_true(state != MarketState.TREND_CONFIRMED_1H,
                      f"1h ADX=54.9 < 55 不应为趋势确认，实际={state.value}")
    suite.print_result(t)

    # --- F4.3: 1h ADX=55 且 4h ADX=10（4h不确认）仍触发（无条件）---
    t = suite.test("F4.3-1h ADX=55, 4h ADX=10：仍触发趋势确认（无条件）")
    detector3 = MarketStateDetector(kline_service=mock_kline)
    state, _ = detector3._determine_state(
        adx_1h=Decimal('55'), adx_4h=Decimal('10'),
        adx_15m=Decimal('20'),
        ema20_1h=Decimal('3100'), ema50_1h=Decimal('3000'),
        ema20_4h=Decimal('3100'), ema50_4h=Decimal('3000'),
        atr_smooth_1h=Decimal('80'),
        price_change_1h=Decimal('0'), price_change_15m=Decimal('0')
    )
    suite.assert_equal(state, MarketState.TREND_CONFIRMED_1H,
                       "1h ADX=55 无条件触发趋势确认，4h ADX=10不影响")
    suite.print_result(t)

    # --- F4.4: 1h ADX=60（远超阈值）仍触发 ---
    t = suite.test("F4.4-1h ADX=60：触发趋势确认")
    detector4 = MarketStateDetector(kline_service=mock_kline)
    state, _ = detector4._determine_state(
        adx_1h=Decimal('60'), adx_4h=Decimal('5'),
        adx_15m=Decimal('20'),
        ema20_1h=Decimal('3100'), ema50_1h=Decimal('3000'),
        ema20_4h=Decimal('3100'), ema50_4h=Decimal('3000'),
        atr_smooth_1h=Decimal('80'),
        price_change_1h=Decimal('0'), price_change_15m=Decimal('0')
    )
    suite.assert_equal(state, MarketState.TREND_CONFIRMED_1H,
                       "1h ADX=60 应触发趋势确认")
    suite.print_result(t)

    # --- F4.5: emergency_adx_threshold 可配置 ---
    t = suite.test("F4.5-emergency_adx_threshold可配置：默认55，可自定义")
    detector5 = MarketStateDetector(kline_service=mock_kline, emergency_adx_threshold=45)
    suite.assert_equal(detector5.emergency_adx_threshold, 45, "阈值应为45")
    state, _ = detector5._determine_state(
        adx_1h=Decimal('46'), adx_4h=Decimal('10'),
        adx_15m=Decimal('20'),
        ema20_1h=Decimal('3100'), ema50_1h=Decimal('3000'),
        ema20_4h=Decimal('3100'), ema50_4h=Decimal('3000'),
        atr_smooth_1h=Decimal('80'),
        price_change_1h=Decimal('0'), price_change_15m=Decimal('0')
    )
    suite.assert_equal(state, MarketState.TREND_CONFIRMED_1H,
                       "自定义阈值45，ADX=46应触发趋势确认")
    suite.print_result(t)

    # 汇总
    passed, failed = suite.summary()
    print(f"\n  [F4] 趋势确认边界: {passed}/{passed+failed} 通过")
    return passed, failed


# ============================================================
# F5: 三档冷却时间
# ============================================================

def test_f5_three_tier_cooldown():
    """测试三档冷却时间逻辑"""
    suite = TestSuite("F5-三档冷却时间")

    from strategies.grid.signal_bot import GridSignalBot, GridSignal
    from strategies.grid.market_state import MarketState, MarketAnalysis

    config = _load_config()
    bot = _create_bot(config)

    def _make_signal(symbol: str, state: MarketState, timestamp: datetime = None) -> GridSignal:
        """构建测试用的信号"""
        ma = MarketAnalysis(
            state=state,
            trend_strength=Decimal('0.1'),
            adx_1h=Decimal('25'), adx_4h=Decimal('20'),
            ema20_1h=Decimal('2500'), ema50_1h=Decimal('2480'),
            current_price=Decimal('2500'), atr_smooth=Decimal('50'),
            confidence=Decimal('0.7'),
        )
        return GridSignal(
            symbol=symbol, market_analysis=ma, grid_params=None,
            timestamp=timestamp or datetime.now(),
            message="测试消息", position_valid=True, position_message=""
        )

    symbol = 'ETHUSDT'

    # --- F5.1: 趋势确认冷却1小时 ---
    t = suite.test("F5.1-趋势确认冷却1小时：30分钟前推送过，不应推送")
    bot.last_signals = {}
    last = _make_signal(symbol, MarketState.TREND_CONFIRMED_1H,
                        timestamp=datetime.now() - timedelta(minutes=30))
    bot.last_signals[symbol] = last
    current = _make_signal(symbol, MarketState.TREND_CONFIRMED_1H)
    result = bot._should_notify(current)
    suite.assert_true(not result, "趋势确认30分钟前推送过，未满1小时，不应推送")
    suite.print_result(t)

    # --- F5.2: 趋势确认冷却1小时已过 ---
    t = suite.test("F5.2-趋势确认冷却1小时：2小时前推送过，应推送")
    last2 = _make_signal(symbol, MarketState.TREND_CONFIRMED_1H,
                         timestamp=datetime.now() - timedelta(hours=2))
    bot.last_signals[symbol] = last2
    current2 = _make_signal(symbol, MarketState.TREND_CONFIRMED_1H)
    result2 = bot._should_notify(current2)
    suite.assert_true(result2, "趋势确认2小时前推送过，超过1小时冷却，应推送")
    suite.print_result(t)

    # --- F5.3: 趋势急剧增强冷却1小时 ---
    t = suite.test("F5.3-趋势急剧增强冷却1小时：30分钟前推送过，不应推送")
    last3 = _make_signal(symbol, MarketState.TREND_ACCELERATING,
                         timestamp=datetime.now() - timedelta(minutes=30))
    bot.last_signals[symbol] = last3
    current3 = _make_signal(symbol, MarketState.TREND_ACCELERATING)
    result3 = bot._should_notify(current3)
    suite.assert_true(not result3, "趋势急剧增强30分钟前推送过，未满1小时，不应推送")
    suite.print_result(t)

    # --- F5.4: 极端强趋势冷却1小时 ---
    t = suite.test("F5.4-极端强趋势冷却1小时：30分钟前推送过，不应推送")
    last4 = _make_signal(symbol, MarketState.EXTREME_STRONG_TREND,
                         timestamp=datetime.now() - timedelta(minutes=30))
    bot.last_signals[symbol] = last4
    current4 = _make_signal(symbol, MarketState.EXTREME_STRONG_TREND)
    result4 = bot._should_notify(current4)
    suite.assert_true(not result4, "极端强趋势30分钟前推送过，未满1小时，不应推送")
    suite.print_result(t)

    # --- F5.5: 普通强趋势冷却6小时 ---
    t = suite.test("F5.5-普通强趋势冷却6小时：5小时前推送过，不应推送")
    last5 = _make_signal(symbol, MarketState.NORMAL_STRONG_TREND,
                         timestamp=datetime.now() - timedelta(hours=5))
    bot.last_signals[symbol] = last5
    current5 = _make_signal(symbol, MarketState.NORMAL_STRONG_TREND)
    result5 = bot._should_notify(current5)
    suite.assert_true(not result5, "普通强趋势5小时前推送过，未满6小时，不应推送")
    suite.print_result(t)

    # --- F5.6: 波动率异常冷却6小时 ---
    t = suite.test("F5.6-波动率异常冷却6小时：5小时前推送过，不应推送")
    last6 = _make_signal(symbol, MarketState.VOLATILITY_ABNORMAL,
                         timestamp=datetime.now() - timedelta(hours=5))
    bot.last_signals[symbol] = last6
    current6 = _make_signal(symbol, MarketState.VOLATILITY_ABNORMAL)
    result6 = bot._should_notify(current6)
    suite.assert_true(not result6, "波动率异常5小时前推送过，未满6小时，不应推送")
    suite.print_result(t)

    # --- F5.7: 弱趋势冷却2小时 ---
    t = suite.test("F5.7-弱趋势冷却2小时：1小时前推送过，不应推送")
    last7 = _make_signal(symbol, MarketState.WEAK_TREND,
                         timestamp=datetime.now() - timedelta(hours=1))
    bot.last_signals[symbol] = last7
    current7 = _make_signal(symbol, MarketState.WEAK_TREND)
    result7 = bot._should_notify(current7)
    suite.assert_true(not result7, "弱趋势1小时前推送过，未满2小时，不应推送")
    suite.print_result(t)

    # --- F5.8: 震荡冷却2小时 ---
    t = suite.test("F5.8-震荡冷却2小时：1小时前推送过，不应推送")
    last8 = _make_signal(symbol, MarketState.OSCILLATION,
                         timestamp=datetime.now() - timedelta(hours=1))
    bot.last_signals[symbol] = last8
    current8 = _make_signal(symbol, MarketState.OSCILLATION)
    result8 = bot._should_notify(current8)
    suite.assert_true(not result8, "震荡1小时前推送过，未满2小时，不应推送")
    suite.print_result(t)

    # --- F5.9: 冷却时间从配置读取 ---
    t = suite.test("F5.9-冷却时间从配置读取：alert=1h, normal=6h, tradable=2h")
    suite.assert_equal(bot.push_cooldown_hours_alert, 1, "alert冷却应为1小时")
    suite.assert_equal(bot.push_cooldown_hours_normal, 6, "normal冷却应为6小时")
    suite.assert_equal(bot.push_cooldown_hours_tradable, 2, "tradable冷却应为2小时")
    suite.print_result(t)

    # --- F5.10: 状态变化立即推送（不受冷却限制）---
    t = suite.test("F5.10-状态变化立即推送：从趋势确认为震荡")
    last10 = _make_signal(symbol, MarketState.TREND_CONFIRMED_1H,
                          timestamp=datetime.now() - timedelta(minutes=5))
    bot.last_signals[symbol] = last10
    current10 = _make_signal(symbol, MarketState.OSCILLATION)
    result10 = bot._should_notify(current10)
    suite.assert_true(result10, "状态变化应立即推送，不受冷却限制")
    suite.print_result(t)

    # --- F5.11: 首次运行一定推送 ---
    t = suite.test("F5.11-首次运行一定推送")
    bot.last_signals = {}
    current11 = _make_signal(symbol, MarketState.TREND_CONFIRMED_1H)
    result11 = bot._should_notify(current11)
    suite.assert_true(result11, "首次运行一定推送")
    suite.print_result(t)

    # 汇总
    passed, failed = suite.summary()
    print(f"\n  [F5] 三档冷却时间: {passed}/{passed+failed} 通过")
    return passed, failed


# ============================================================
# F6: 恢复条件
# ============================================================

def test_f6_recovery_conditions():
    """测试恢复条件逻辑"""
    suite = TestSuite("F6-恢复条件")

    from strategies.grid.market_state import MarketStateDetector, MarketState

    mock_kline = make_mock_kline_service()

    # --- F6.1: 从强趋势恢复：1h ADX < 30 且 4h ADX < 30 ---
    t = suite.test("F6.1-从强趋势恢复：1h ADX=25, 4h ADX=20 -> 弱趋势/震荡")
    detector = MarketStateDetector(kline_service=mock_kline)
    state, _ = detector._determine_state(
        adx_1h=Decimal('25'), adx_4h=Decimal('20'),
        adx_15m=Decimal('20'),
        ema20_1h=Decimal('3100'), ema50_1h=Decimal('3000'),
        ema20_4h=Decimal('3100'), ema50_4h=Decimal('3000'),
        atr_smooth_1h=Decimal('80'),
        price_change_1h=Decimal('0'), price_change_15m=Decimal('0')
    )
    suite.assert_true(state in [MarketState.WEAK_TREND, MarketState.OSCILLATION],
                      f"ADX回落后应为弱趋势或震荡，实际={state.value}")
    suite.print_result(t)

    # --- F6.2: 从强趋势恢复边界：1h ADX=29.9, 4h ADX=29.9 ---
    t = suite.test("F6.2-强趋势恢复边界：1h ADX=29.9, 4h ADX=29.9 -> 弱趋势")
    detector2 = MarketStateDetector(kline_service=mock_kline)
    state, _ = detector2._determine_state(
        adx_1h=Decimal('29.9'), adx_4h=Decimal('29.9'),
        adx_15m=Decimal('20'),
        ema20_1h=Decimal('3100'), ema50_1h=Decimal('3000'),
        ema20_4h=Decimal('3100'), ema50_4h=Decimal('3000'),
        atr_smooth_1h=Decimal('80'),
        price_change_1h=Decimal('0'), price_change_15m=Decimal('0')
    )
    suite.assert_true(state != MarketState.EXTREME_STRONG_TREND,
                      "1h ADX=29.9 不应为极端强趋势")
    suite.assert_true(state != MarketState.NORMAL_STRONG_TREND,
                      "1h ADX=29.9 < 30 不应为普通强趋势")
    suite.print_result(t)

    # --- F6.3: 从波动率异常恢复：ATR/峰值 < 1.2 ---
    t = suite.test("F6.3-从波动率异常恢复：ATR回落至 peak*1.2 以下")
    detector3 = MarketStateDetector(kline_service=mock_kline)
    # 先激活波动率异常
    detector3._atr_history = [Decimal('100'), Decimal('105'), Decimal('108'), Decimal('110'), Decimal('112')]
    detector3._atr_abnormal_count = 1
    detector3._is_vol_alarm_active = False
    detector3._atr_peak = Decimal('0')
    # 触发第2次异常
    detector3._check_volatility_abnormal(Decimal('250'))
    suite.assert_true(detector3._is_vol_alarm_active, "应已激活波动率异常")
    suite.assert_approx(detector3._atr_peak, Decimal('250'), Decimal('0.01'), "峰值应为250")

    # 模拟ATR回落：ATR=280，280/250=1.12 < 1.2 -> 恢复
    detector3._check_volatility_abnormal(Decimal('280'))
    suite.assert_true(not detector3._is_vol_alarm_active, "ATR回落应恢复波动率异常")
    suite.assert_equal(detector3._atr_abnormal_count, 0, "恢复后异常计数应重置")
    suite.assert_equal(detector3._atr_peak, Decimal('0'), "恢复后峰值应重置")
    suite.print_result(t)

    # --- F6.4: 波动率异常恢复边界：ATR/峰值 = 1.2 不恢复 ---
    t = suite.test("F6.4-波动率异常恢复边界：ATR/峰值=1.2 不恢复")
    detector4 = MarketStateDetector(kline_service=mock_kline)
    # 激活波动率异常
    detector4._atr_history = [Decimal('100'), Decimal('105'), Decimal('108'), Decimal('110'), Decimal('112')]
    detector4._atr_abnormal_count = 1
    detector4._is_vol_alarm_active = False
    detector4._atr_peak = Decimal('0')
    detector4._check_volatility_abnormal(Decimal('250'))
    suite.assert_true(detector4._is_vol_alarm_active, "应已激活波动率异常")

    # ATR=300, 300/250=1.2，不恢复（需要 < 1.2）
    detector4._check_volatility_abnormal(Decimal('300'))
    suite.assert_true(detector4._is_vol_alarm_active,
                      "ATR/peak=1.2 应保持警报（不恢复，需<1.2）")
    suite.print_result(t)

    # --- F6.5: 趋势确认通知包含恢复条件 ---
    t = suite.test("F6.5-趋势确认通知包含恢复条件")
    from strategies.grid.signal_bot import GridSignalBot
    from strategies.grid.market_state import MarketAnalysis

    bot = _create_bot()
    ma = MarketAnalysis(
        state=MarketState.TREND_CONFIRMED_1H,
        trend_strength=Decimal('0.5'),
        adx_1h=Decimal('55'), adx_4h=Decimal('35'),
        adx_15m=Decimal('30'),
        ema20_1h=Decimal('3100'), ema50_1h=Decimal('3000'),
        current_price=Decimal('3200'), atr_smooth=Decimal('100'),
        confidence=Decimal('0.95'),
        price_change_1h=Decimal('0.005'),
    )
    msg = bot._generate_trend_confirmed_1h_message("ETHUSDT", ma)
    suite.assert_in("恢复条件", msg, "应包含恢复条件说明")  # 消息中包含恢复条件
    suite.assert_in(str(bot.market_detector.recovery_adx_strong_1h), msg,
                    "应包含1h恢复阈值")
    suite.print_result(t)

    # 汇总
    passed, failed = suite.summary()
    print(f"\n  [F6] 恢复条件: {passed}/{passed+failed} 通过")
    return passed, failed


# ============================================================
# F7: 配置完整性
# ============================================================

def test_f7_config_completeness():
    """测试V2.4配置完整性"""
    suite = TestSuite("F7-配置完整性")

    config = _load_config()

    # --- F7.1: 版本号为 2.4.0 ---
    t = suite.test("F7.1-版本号为2.4.0")
    version = config.get('strategy', {}).get('version', '')
    suite.assert_equal(version, '2.4.0', f"版本号应为2.4.0，实际={version}")
    suite.print_result(t)

    # --- F7.2: market 配置包含 V2.4 新增参数 ---
    t = suite.test("F7.2-market配置包含V2.4新增参数")
    market = config.get('market', {})
    v24_new_keys = [
        'adx_period',                   # ADX周期（从14缩短为10）
        'price_emergency_1h',           # 第1层：1h价格变动紧急阈值
        'price_emergency_15m',          # 第1层：15m价格变动紧急阈值
        'adx_early_warning_15m',        # 第2层：15m ADX早期预警阈值
        'price_early_warning_1h',       # 第2层：早期预警需1h价格变动≥1%
    ]
    for key in v24_new_keys:
        suite.assert_true(key in market, f"market 缺少 V2.4 新增配置项: {key}")
    suite.print_result(t)

    # --- F7.3: V2.4 新增参数值正确 ---
    t = suite.test("F7.3-V2.4新增参数值正确：adx_period=10, price_emergency_1h=0.03")
    market = config.get('market', {})
    suite.assert_equal(market.get('adx_period'), 10,
                       "adx_period 应为10")
    suite.assert_equal(market.get('price_emergency_1h'), 0.03,
                       "price_emergency_1h 应为0.03")
    suite.assert_equal(market.get('price_emergency_15m'), 0.015,
                       "price_emergency_15m 应为0.015")
    suite.assert_equal(market.get('adx_early_warning_15m'), 50,
                       "adx_early_warning_15m 应为50")
    suite.assert_equal(market.get('price_early_warning_1h'), 0.01,
                       "price_early_warning_1h 应为0.01")
    suite.print_result(t)

    # --- F7.4: signal_bot 配置包含三档冷却参数 ---
    t = suite.test("F7.4-signal_bot配置包含三档冷却参数")
    sb = config.get('signal_bot', {})
    cooldown_keys = [
        'push_cooldown_hours_alert',
        'push_cooldown_hours_normal',
        'push_cooldown_hours_tradable',
    ]
    for key in cooldown_keys:
        suite.assert_true(key in sb, f"signal_bot 缺少冷却配置项: {key}")
    suite.print_result(t)

    # --- F7.5: 三档冷却参数值正确 ---
    t = suite.test("F7.5-三档冷却参数值：alert=1h, normal=6h, tradable=2h")
    sb = config.get('signal_bot', {})
    suite.assert_equal(sb.get('push_cooldown_hours_alert'), 1,
                       "alert冷却应为1小时")
    suite.assert_equal(sb.get('push_cooldown_hours_normal'), 6,
                       "normal冷却应为6小时")
    suite.assert_equal(sb.get('push_cooldown_hours_tradable'), 2,
                       "tradable冷却应为2小时")
    suite.print_result(t)

    # --- F7.6: MarketStateDetector 从配置读取V2.4参数 ---
    t = suite.test("F7.6-MarketStateDetector从配置读取V2.4参数")
    from strategies.grid.market_state import MarketStateDetector
    mock_kline = make_mock_kline_service()
    detector = MarketStateDetector(
        kline_service=mock_kline,
        adx_period=config['market']['adx_period'],
        emergency_adx_threshold=config['market']['emergency_adx_threshold'],
        trend_acceleration_threshold=config['market']['trend_acceleration_threshold'],
        adx_history_size=config['market']['adx_history_size'],
    )
    suite.assert_equal(detector.adx_period, 10, "adx_period应为10")
    suite.assert_equal(detector.emergency_adx_threshold, 55, "emergency阈值应为55")
    suite.assert_equal(detector.trend_acceleration_threshold, 8, "acceleration阈值应为8")
    suite.print_result(t)

    # --- F7.7: GridSignalBot 从配置读取三档冷却 ---
    t = suite.test("F7.7-GridSignalBot从配置读取三档冷却")
    bot = _create_bot(config)
    suite.assert_equal(bot.push_cooldown_hours_alert, 1, "alert冷却应为1")
    suite.assert_equal(bot.push_cooldown_hours_normal, 6, "normal冷却应为6")
    suite.assert_equal(bot.push_cooldown_hours_tradable, 2, "tradable冷却应为2")
    suite.print_result(t)

    # --- F7.8: V2.4置信度参数存在 ---
    t = suite.test("F7.8-V2.4置信度参数存在：price_emergency=1.0, early_warning_15m=0.92, trend_confirmed_1h=0.95")
    confidence = config.get('market', {}).get('confidence', {})
    suite.assert_equal(confidence.get('price_emergency'), 1.0, "price_emergency置信度应为1.0")
    suite.assert_equal(confidence.get('early_warning_15m'), 0.92, "early_warning_15m置信度应为0.92")
    suite.assert_equal(confidence.get('trend_confirmed_1h'), 0.95, "trend_confirmed_1h置信度应为0.95")
    suite.print_result(t)

    # 汇总
    passed, failed = suite.summary()
    print(f"\n  [F7] 配置完整性: {passed}/{passed+failed} 通过")
    return passed, failed


# ============================================================
# F8: 通知模板
# ============================================================

def test_f8_notification_templates():
    """测试V2.4通知模板内容"""
    suite = TestSuite("F8-通知模板")

    from strategies.grid.signal_bot import GridSignalBot
    from strategies.grid.market_state import MarketState, MarketAnalysis

    bot = _create_bot()

    # --- F8.1: 价格行为紧急触发消息包含"立即终止" ---
    t = suite.test("F8.1-价格行为紧急触发消息包含'立即终止'")
    ma_price_emergency = MarketAnalysis(
        state=MarketState.PRICE_EMERGENCY,
        trend_strength=Decimal('0.5'),
        adx_1h=Decimal('30'), adx_4h=Decimal('25'),
        adx_15m=Decimal('40'),
        ema20_1h=Decimal('3100'), ema50_1h=Decimal('3000'),
        current_price=Decimal('3200'), atr_smooth=Decimal('100'),
        confidence=Decimal('1.0'),
        price_change_1h=Decimal('0.035'), price_change_15m=Decimal('0.005'),
    )
    msg = bot._generate_price_emergency_message("ETHUSDT", ma_price_emergency)
    suite.assert_in("立即终止", msg, "价格行为紧急触发消息应包含'立即终止'")
    suite.assert_in("价格行为紧急触发", msg, "应包含'价格行为紧急触发'")
    suite.assert_in("3.50%", msg, "应包含1h价格变动值")
    suite.print_result(t)

    # --- F8.2: 趋势急剧增强消息包含ADX变化量 ---
    t = suite.test("F8.2-趋势急剧增强消息包含ADX变化量")
    ma_accel = MarketAnalysis(
        state=MarketState.TREND_ACCELERATING,
        trend_strength=Decimal('0.4'),
        adx_1h=Decimal('45'), adx_4h=Decimal('35'),
        adx_15m=Decimal('20'),
        ema20_1h=Decimal('3100'), ema50_1h=Decimal('3000'),
        current_price=Decimal('3200'), atr_smooth=Decimal('100'),
        confidence=Decimal('0.9'),
        adx_prev_1h=Decimal('20'),
    )
    msg_accel = bot._generate_trend_accelerating_message("ETHUSDT", ma_accel)
    suite.assert_in("趋势急剧增强", msg_accel, "应包含'趋势急剧增强'")
    suite.assert_in("20.0", msg_accel, "应包含前次ADX值")
    suite.assert_in("45.0", msg_accel, "应包含当前ADX值")
    suite.assert_in("+25.0", msg_accel, "应包含ADX变化量+25.0")
    suite.print_result(t)

    # --- F8.3: 趋势急剧增强消息在无历史时显示0 ---
    t = suite.test("F8.3-趋势急剧增强消息：adx_prev=0时显示0")
    ma_accel_no_prev = MarketAnalysis(
        state=MarketState.TREND_ACCELERATING,
        trend_strength=Decimal('0.4'),
        adx_1h=Decimal('45'), adx_4h=Decimal('35'),
        adx_15m=Decimal('20'),
        ema20_1h=Decimal('3100'), ema50_1h=Decimal('3000'),
        current_price=Decimal('3200'), atr_smooth=Decimal('100'),
        confidence=Decimal('0.9'),
        adx_prev_1h=Decimal('0'),
    )
    msg_no_prev = bot._generate_trend_accelerating_message("ETHUSDT", ma_accel_no_prev)
    suite.assert_in("0.0", msg_no_prev, "应包含前次ADX值0.0")
    suite.print_result(t)

    # --- F8.4: 极端强趋势消息包含"立即终止" ---
    t = suite.test("F8.4-极端强趋势消息包含'立即终止'")
    ma_extreme = MarketAnalysis(
        state=MarketState.EXTREME_STRONG_TREND,
        trend_strength=Decimal('0.4'),
        adx_1h=Decimal('42'), adx_4h=Decimal('32'),
        adx_15m=Decimal('20'),
        ema20_1h=Decimal('3100'), ema50_1h=Decimal('3000'),
        current_price=Decimal('3200'), atr_smooth=Decimal('100'),
        confidence=Decimal('0.95'),
    )
    msg_extreme = bot._generate_extreme_strong_message("ETHUSDT", ma_extreme)
    suite.assert_in("立即终止", msg_extreme, "极端强趋势消息应包含'立即终止'")
    suite.print_result(t)

    # --- F8.5: 普通强趋势消息包含"建议终止" ---
    t = suite.test("F8.5-普通强趋势消息包含'建议终止'")
    ma_normal = MarketAnalysis(
        state=MarketState.NORMAL_STRONG_TREND,
        trend_strength=Decimal('0.2'),
        adx_1h=Decimal('32'), adx_4h=Decimal('27'),
        adx_15m=Decimal('20'),
        ema20_1h=Decimal('3100'), ema50_1h=Decimal('3000'),
        current_price=Decimal('3100'), atr_smooth=Decimal('80'),
        confidence=Decimal('0.8'),
        ema20_4h=Decimal('3100'), ema50_4h=Decimal('3000'),
    )
    msg_normal = bot._generate_normal_strong_message("ETHUSDT", ma_normal)
    suite.assert_in("建议终止", msg_normal, "普通强趋势消息应包含'建议终止'")
    suite.print_result(t)

    # --- F8.6: 波动率异常消息包含"暂停挂单" ---
    t = suite.test("F8.6-波动率异常消息包含'暂停挂单'")
    ma_vol = MarketAnalysis(
        state=MarketState.VOLATILITY_ABNORMAL,
        trend_strength=Decimal('0.15'),
        adx_1h=Decimal('28'), adx_4h=Decimal('22'),
        adx_15m=Decimal('20'),
        ema20_1h=Decimal('3100'), ema50_1h=Decimal('3050'),
        current_price=Decimal('3100'), atr_smooth=Decimal('250'),
        confidence=Decimal('0.85'),
        atr_2h_ago=Decimal('100'), atr_peak=Decimal('250'),
        is_volatility_alarm_active=True,
    )
    msg_vol = bot._generate_volatility_abnormal_message("ETHUSDT", ma_vol)
    suite.assert_in("暂停挂单", msg_vol, "波动率异常消息应包含'暂停挂单'")
    suite.print_result(t)

    # --- F8.7: 所有V2.4消息生成方法存在 ---
    t = suite.test("F8.7-所有V2.4消息生成方法存在")
    v24_methods = [
        '_generate_price_emergency_message',     # V2.4新增（第1层）
        '_generate_early_warning_15m_message',   # V2.4新增（第2层）
        '_generate_trend_confirmed_1h_message',  # V2.4新增（第3层）
        '_generate_trend_accelerating_message',
        '_generate_extreme_strong_message',
        '_generate_normal_strong_message',
        '_generate_volatility_abnormal_message',
        '_generate_signal_message',
        '_generate_recovery_message',
    ]
    for m in v24_methods:
        suite.assert_true(hasattr(bot, m), f"缺失方法: {m}")
    suite.print_result(t)

    # --- F8.8: 15m ADX早期预警消息包含阈值信息 ---
    t = suite.test("F8.8-15m ADX早期预警消息包含阈值信息")
    ma_early = MarketAnalysis(
        state=MarketState.EARLY_WARNING_15M,
        trend_strength=Decimal('0.3'),
        adx_1h=Decimal('30'), adx_4h=Decimal('25'),
        adx_15m=Decimal('55'),
        ema20_1h=Decimal('3100'), ema50_1h=Decimal('3000'),
        current_price=Decimal('3200'), atr_smooth=Decimal('100'),
        confidence=Decimal('0.92'),
        price_change_1h=Decimal('0.015'),
    )
    msg_early = bot._generate_early_warning_15m_message("ETHUSDT", ma_early)
    suite.assert_in("50", msg_early, "应包含15m ADX阈值50")
    suite.assert_in("55.0", msg_early, "应包含15m ADX值55.0")
    suite.print_result(t)

    # --- F8.9: 1h ADX趋势确认消息包含ADX周期信息 ---
    t = suite.test("F8.9-1h ADX趋势确认消息包含ADX(10)信息")
    ma_confirmed = MarketAnalysis(
        state=MarketState.TREND_CONFIRMED_1H,
        trend_strength=Decimal('0.5'),
        adx_1h=Decimal('58'), adx_4h=Decimal('38'),
        adx_15m=Decimal('40'),
        ema20_1h=Decimal('3100'), ema50_1h=Decimal('3000'),
        current_price=Decimal('3200'), atr_smooth=Decimal('100'),
        confidence=Decimal('0.95'),
        price_change_1h=Decimal('0.005'),
    )
    msg_confirmed = bot._generate_trend_confirmed_1h_message("ETHUSDT", ma_confirmed)
    suite.assert_in("ADX(10)", msg_confirmed, "应包含ADX(10)标识")
    suite.assert_in("58.0", msg_confirmed, "应包含1h ADX值58.0")
    suite.print_result(t)

    # 汇总
    passed, failed = suite.summary()
    print(f"\n  [F8] 通知模板: {passed}/{passed+failed} 通过")
    return passed, failed


# ============================================================
# F9: V2.4 三层预警验证（新增测试用例）
# ============================================================

def test_f9_v24_three_tier():
    """测试V2.4三层预警架构"""
    suite = TestSuite("F9-V2.4三层预警验证")

    from strategies.grid.market_state import MarketStateDetector, MarketState
    from strategies.grid.grid_calculator import GridCalculator

    mock_kline = make_mock_kline_service()

    # ============================================================
    # 三层优先级验证
    # ============================================================

    # 测试1：价格行为紧急触发 > 15m ADX早期预警 > 1h ADX趋势确认
    t = suite.test("三层优先级-价格行为紧急触发 > 15m ADX早期预警 > 1h ADX趋势确认")
    detector = MarketStateDetector(kline_service=mock_kline)
    # 同时满足三层条件
    state, conf = detector._determine_state(
        adx_1h=Decimal('58'), adx_4h=Decimal('35'),  # 满足第3层
        adx_15m=Decimal('55'),  # 满足第2层
        ema20_1h=Decimal('3100'), ema50_1h=Decimal('3000'),
        ema20_4h=Decimal('3100'), ema50_4h=Decimal('3000'),
        atr_smooth_1h=Decimal('80'),
        price_change_1h=Decimal('0.035'), price_change_15m=Decimal('0.02')  # 满足第1层
    )
    suite.assert_equal(state, MarketState.PRICE_EMERGENCY,
                       "同时满足三层条件，应返回第1层（价格行为紧急触发）")
    suite.print_result(t)

    # 测试2：15m ADX早期预警 > 1h ADX趋势确认
    t = suite.test("三层优先级-15m ADX早期预警 > 1h ADX趋势确认")
    detector2 = MarketStateDetector(kline_service=mock_kline)
    state, conf = detector2._determine_state(
        adx_1h=Decimal('58'), adx_4h=Decimal('35'),  # 满足第3层
        adx_15m=Decimal('55'),  # 满足第2层
        ema20_1h=Decimal('3100'), ema50_1h=Decimal('3000'),
        ema20_4h=Decimal('3100'), ema50_4h=Decimal('3000'),
        atr_smooth_1h=Decimal('80'),
        price_change_1h=Decimal('0.015'), price_change_15m=Decimal('0')  # 仅满足第2层的价格变动
    )
    suite.assert_equal(state, MarketState.EARLY_WARNING_15M,
                       "同时满足第2层和第3层，应返回第2层（15m ADX早期预警）")
    suite.print_result(t)

    # ============================================================
    # 价格变动率计算验证
    # ============================================================

    # 测试3：_calculate_price_change 方法存在
    t = suite.test("_calculate_price_change方法存在")
    suite.assert_true(hasattr(detector, '_calculate_price_change'),
                      "MarketStateDetector 应有 _calculate_price_change 方法")
    suite.print_result(t)

    # 测试4：价格变动率计算正确
    t = suite.test("价格变动率计算：100→105，变动率=5%")
    klines = [
        {'open': 100, 'high': 102, 'low': 99, 'close': 100},
        {'open': 101, 'high': 106, 'low': 100, 'close': 105},
    ]
    change = detector._calculate_price_change(klines)
    suite.assert_approx(change, Decimal('0.05'), Decimal('0.001'), "100→105变动率应为5%")
    suite.print_result(t)

    # 测试5：价格变动率计算（下跌）
    t = suite.test("价格变动率计算：100→95，变动率=5%（绝对值）")
    klines2 = [
        {'open': 100, 'high': 102, 'low': 99, 'close': 100},
        {'open': 99, 'high': 100, 'low': 94, 'close': 95},
    ]
    change2 = detector._calculate_price_change(klines2)
    suite.assert_approx(change2, Decimal('0.05'), Decimal('0.001'), "100→95变动率绝对值应为5%")
    suite.print_result(t)

    # 测试6：数据不足时返回0
    t = suite.test("价格变动率计算：数据不足（1根K线）返回0")
    change3 = detector._calculate_price_change([{'close': 100}])
    suite.assert_equal(change3, Decimal('0'), "数据不足时应返回0")
    suite.print_result(t)

    # ============================================================
    # 15m价格变动触发验证
    # ============================================================

    # 测试7：15m变动>=1.5%触发价格行为紧急
    t = suite.test("15m变动>=1.5%触发价格行为紧急触发")
    state, _ = detector._determine_state(
        adx_1h=Decimal('30'), adx_4h=Decimal('20'),
        adx_15m=Decimal('30'),
        ema20_1h=Decimal('3100'), ema50_1h=Decimal('3000'),
        ema20_4h=Decimal('3100'), ema50_4h=Decimal('3000'),
        atr_smooth_1h=Decimal('80'),
        price_change_1h=Decimal('0.005'), price_change_15m=Decimal('0.02')  # 15m变动2%
    )
    suite.assert_equal(state, MarketState.PRICE_EMERGENCY,
                       "15m变动2% >= 1.5% 应触发价格行为紧急触发")
    suite.print_result(t)

    # ============================================================
    # 普通强趋势优先于波动率异常（V2.3修复保留验证）
    # ============================================================

    t = suite.test("修复保留-普通强趋势优先于波动率异常：同时满足时返回普通强趋势")
    detector9 = MarketStateDetector(kline_service=mock_kline)
    detector9._atr_history = [Decimal('100'), Decimal('105'), Decimal('108'), Decimal('110'), Decimal('112')]
    detector9._atr_abnormal_count = 1
    detector9._is_vol_alarm_active = False
    detector9._atr_peak = Decimal('0')
    detector9._check_volatility_abnormal(Decimal('250'))
    state, conf = detector9._determine_state(
        adx_1h=Decimal('32'), adx_4h=Decimal('28'),
        adx_15m=Decimal('20'),
        ema20_1h=Decimal('3100'), ema50_1h=Decimal('3000'),
        ema20_4h=Decimal('3100'), ema50_4h=Decimal('3000'),
        atr_smooth_1h=Decimal('280'),
        price_change_1h=Decimal('0'), price_change_15m=Decimal('0')
    )
    suite.assert_equal(state, MarketState.NORMAL_STRONG_TREND,
                       "普通强趋势优先级高于波动率异常，应返回普通强趋势")
    suite.print_result(t)

    # 汇总
    passed, failed = suite.summary()
    print(f"\n  [F9] V2.4三层预警验证: {passed}/{passed+failed} 通过")
    return passed, failed


# ============================================================
# 主入口
# ============================================================

def main():
    """运行所有V2.4测试并汇总结果"""
    print("=" * 70)
    print("  V2.4 网格交易系统全功能点测试")
    print(f"  时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)

    all_passed = 0
    all_failed = 0

    test_modules = [
        ("F1-市场状态判定(9种)", test_f1_market_state_determination),
        ("F2-优先级测试", test_f2_priority),
        ("F3-趋势加速检测", test_f3_trend_acceleration),
        ("F4-趋势确认边界", test_f4_trend_confirmed_boundary),
        ("F5-三档冷却时间", test_f5_three_tier_cooldown),
        ("F6-恢复条件", test_f6_recovery_conditions),
        ("F7-配置完整性", test_f7_config_completeness),
        ("F8-通知模板", test_f8_notification_templates),
        ("F9-V2.4三层预警验证", test_f9_v24_three_tier),
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
    print("  V2.4 测试结果汇总")
    print("=" * 70)
    print(f"  总计: {total} 个用例")
    if total > 0:
        print(f"  通过: {all_passed} ({all_passed/total*100:.1f}%)")
        print(f"  失败: {all_failed} ({all_failed/total*100:.1f}%)")
    print("=" * 70)

    if all_failed == 0:
        print("\n  全部测试通过!")
        return 0
    else:
        print(f"\n  存在 {all_failed} 个失败用例，请检查!")
        return 1


if __name__ == '__main__':
    sys.exit(main())
