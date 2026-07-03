"""
分状态冷却时间功能测试（V2.2）

测试推送冷却时间从单一6小时改为分状态的逻辑：
- 强趋势/极端强趋势/波动率异常：6小时（push_cooldown_hours）
- 震荡/弱趋势（可开仓）：2小时（push_cooldown_hours_tradable）

测试场景：
1. 震荡市场冷却2小时 - 超过冷却时间应推送
2. 震荡市场冷却未满 - 未超过冷却时间不应推送
3. 极端强趋势冷却6小时 - 超过冷却时间应推送
4. 极端强趋势冷却未满 - 未超过冷却时间不应推送
5. 弱趋势冷却2小时 - 超过冷却时间应推送
6. 波动率异常冷却6小时 - 未超过冷却时间不应推送
7. 状态变化立即推送 - 从极端强趋势变为震荡，无论冷却时间都应推送
8. 配置完整性 - config.yaml 包含 push_cooldown_hours_tradable 参数
"""
import sys
import os
import unittest
from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock
import yaml

# 将项目根目录加入 sys.path
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, PROJECT_ROOT)

from strategies.grid.signal_bot import GridSignalBot, GridSignal
from strategies.grid.market_state import MarketState, MarketAnalysis


def _make_market_analysis(state: MarketState) -> MarketAnalysis:
    """
    构建测试用的 MarketAnalysis 对象

    Args:
        state: 市场状态

    Returns:
        MarketAnalysis 实例
    """
    return MarketAnalysis(
        state=state,
        trend_strength=Decimal('0.1'),
        adx_1h=Decimal('25'),
        adx_4h=Decimal('20'),
        ema20_1h=Decimal('2500'),
        ema50_1h=Decimal('2480'),
        current_price=Decimal('2500'),
        atr_smooth=Decimal('50'),
        confidence=Decimal('0.7'),
        ema20_4h=Decimal('2490'),
        ema50_4h=Decimal('2470'),
        atr_2h_ago=Decimal('45'),
        atr_abnormal_count=0,
        atr_peak=Decimal('0'),
        is_volatility_alarm_active=False
    )


def _make_signal(symbol: str, state: MarketState, timestamp: datetime = None) -> GridSignal:
    """
    构建测试用的 GridSignal 对象

    Args:
        symbol: 交易对
        state: 市场状态
        timestamp: 信号时间戳，默认为当前时间

    Returns:
        GridSignal 实例
    """
    return GridSignal(
        symbol=symbol,
        market_analysis=_make_market_analysis(state),
        grid_params=None,
        timestamp=timestamp or datetime.now(),
        message="测试消息",
        position_valid=True,
        position_message=""
    )


def _create_bot() -> GridSignalBot:
    """
    创建测试用的 GridSignalBot 实例（使用 mock 依赖）

    Returns:
        GridSignalBot 实例
    """
    config = {
        'symbols': ['ETHUSDT'],
        'trading': {'leverage': 10, 'margin': 500},
        'grid': {'min_grid_count': 5},
        'monitor': {'check_interval': 3600},
        'signal_bot': {
            'push_cooldown_hours': 6,
            'push_cooldown_hours_tradable': 2,
            'conservative_grid_reduce': 10,
            'trigger_thresholds': {'profit_rate_low': 0.012}
        },
        'market': {}
    }

    bot = GridSignalBot(
        binance_client=MagicMock(),
        kline_service=MagicMock(),
        notification_client=MagicMock(),
        grid_calculator=MagicMock(),
        config=config
    )
    return bot


class TestCooldownV22(unittest.TestCase):
    """分状态冷却时间功能测试"""

    def setUp(self):
        """每个测试用例执行前的准备工作"""
        self.bot = _create_bot()
        self.symbol = 'ETHUSDT'

    # ============================================================
    # 测试1：震荡市场冷却2小时 - 3小时前推送过，应推送（>2h）
    # ============================================================
    def test_01_oscillation_cooldown_expired(self):
        """震荡市场冷却2小时已过 - 应推送"""
        # 3小时前推送过震荡状态
        last_signal = _make_signal(
            self.symbol,
            MarketState.OSCILLATION,
            timestamp=datetime.now() - timedelta(hours=3)
        )
        self.bot.last_signals[self.symbol] = last_signal

        # 当前仍为震荡状态
        current_signal = _make_signal(self.symbol, MarketState.OSCILLATION)

        result = self.bot._should_notify(current_signal)
        self.assertTrue(result, "震荡市场3小时前推送过，超过2小时冷却时间，应推送")

    # ============================================================
    # 测试2：震荡市场冷却未满 - 1小时前推送过，不应推送（<2h）
    # ============================================================
    def test_02_oscillation_cooldown_active(self):
        """震荡市场冷却2小时未满 - 不应推送"""
        # 1小时前推送过震荡状态
        last_signal = _make_signal(
            self.symbol,
            MarketState.OSCILLATION,
            timestamp=datetime.now() - timedelta(hours=1)
        )
        self.bot.last_signals[self.symbol] = last_signal

        # 当前仍为震荡状态
        current_signal = _make_signal(self.symbol, MarketState.OSCILLATION)

        result = self.bot._should_notify(current_signal)
        self.assertFalse(result, "震荡市场1小时前推送过，未满2小时冷却时间，不应推送")

    # ============================================================
    # 测试3：极端强趋势冷却6小时 - 7小时前推送过，应推送（>6h）
    # ============================================================
    def test_03_extreme_strong_cooldown_expired(self):
        """极端强趋势冷却6小时已过 - 应推送"""
        # 7小时前推送过极端强趋势状态
        last_signal = _make_signal(
            self.symbol,
            MarketState.EXTREME_STRONG_TREND,
            timestamp=datetime.now() - timedelta(hours=7)
        )
        self.bot.last_signals[self.symbol] = last_signal

        # 当前仍为极端强趋势状态
        current_signal = _make_signal(self.symbol, MarketState.EXTREME_STRONG_TREND)

        result = self.bot._should_notify(current_signal)
        self.assertTrue(result, "极端强趋势7小时前推送过，超过6小时冷却时间，应推送")

    # ============================================================
    # 测试4：极端强趋势冷却未满 - 5小时前推送过，不应推送（<6h）
    # ============================================================
    def test_04_extreme_strong_cooldown_active(self):
        """极端强趋势冷却6小时未满 - 不应推送"""
        # 5小时前推送过极端强趋势状态
        last_signal = _make_signal(
            self.symbol,
            MarketState.EXTREME_STRONG_TREND,
            timestamp=datetime.now() - timedelta(hours=5)
        )
        self.bot.last_signals[self.symbol] = last_signal

        # 当前仍为极端强趋势状态
        current_signal = _make_signal(self.symbol, MarketState.EXTREME_STRONG_TREND)

        result = self.bot._should_notify(current_signal)
        self.assertFalse(result, "极端强趋势5小时前推送过，未满6小时冷却时间，不应推送")

    # ============================================================
    # 测试5：弱趋势冷却2小时 - 3小时前推送过，应推送（>2h）
    # ============================================================
    def test_05_weak_trend_cooldown_expired(self):
        """弱趋势冷却2小时已过 - 应推送"""
        # 3小时前推送过弱趋势状态
        last_signal = _make_signal(
            self.symbol,
            MarketState.WEAK_TREND,
            timestamp=datetime.now() - timedelta(hours=3)
        )
        self.bot.last_signals[self.symbol] = last_signal

        # 当前仍为弱趋势状态
        current_signal = _make_signal(self.symbol, MarketState.WEAK_TREND)

        result = self.bot._should_notify(current_signal)
        self.assertTrue(result, "弱趋势3小时前推送过，超过2小时冷却时间，应推送")

    # ============================================================
    # 测试6：波动率异常冷却6小时 - 5小时前推送过，不应推送（<6h）
    # ============================================================
    def test_06_volatility_abnormal_cooldown_active(self):
        """波动率异常冷却6小时未满 - 不应推送"""
        # 5小时前推送过波动率异常状态
        last_signal = _make_signal(
            self.symbol,
            MarketState.VOLATILITY_ABNORMAL,
            timestamp=datetime.now() - timedelta(hours=5)
        )
        self.bot.last_signals[self.symbol] = last_signal

        # 当前仍为波动率异常状态
        current_signal = _make_signal(self.symbol, MarketState.VOLATILITY_ABNORMAL)

        result = self.bot._should_notify(current_signal)
        self.assertFalse(result, "波动率异常5小时前推送过，未满6小时冷却时间，不应推送")

    # ============================================================
    # 测试7：状态变化立即推送 - 从极端强趋势变为震荡
    # ============================================================
    def test_07_state_change_immediate_notify(self):
        """状态变化时立即推送，不受冷却时间限制"""
        # 5分钟前推送过极端强趋势状态（远未到6小时冷却）
        last_signal = _make_signal(
            self.symbol,
            MarketState.EXTREME_STRONG_TREND,
            timestamp=datetime.now() - timedelta(minutes=5)
        )
        self.bot.last_signals[self.symbol] = last_signal

        # 当前变为震荡状态
        current_signal = _make_signal(self.symbol, MarketState.OSCILLATION)

        result = self.bot._should_notify(current_signal)
        self.assertTrue(result, "从极端强趋势变为震荡，状态变化应立即推送，不受冷却时间限制")

    # ============================================================
    # 测试8：配置完整性 - config.yaml 包含 push_cooldown_hours_tradable 参数
    # ============================================================
    def test_08_config_completeness(self):
        """config.yaml 包含 push_cooldown_hours_tradable 参数"""
        config_path = os.path.join(
            os.path.dirname(__file__), 'config.yaml'
        )

        with open(config_path, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)

        # 验证 signal_bot 节存在
        self.assertIn('signal_bot', config, "config.yaml 必须包含 signal_bot 配置节")

        # 验证 push_cooldown_hours 存在
        self.assertIn(
            'push_cooldown_hours',
            config['signal_bot'],
            "config.yaml 的 signal_bot 必须包含 push_cooldown_hours 参数"
        )

        # 验证 push_cooldown_hours_tradable 存在
        self.assertIn(
            'push_cooldown_hours_tradable',
            config['signal_bot'],
            "config.yaml 的 signal_bot 必须包含 push_cooldown_hours_tradable 参数"
        )

        # 验证参数值合理
        cooldown_hours = config['signal_bot']['push_cooldown_hours']
        cooldown_tradable = config['signal_bot']['push_cooldown_hours_tradable']

        self.assertEqual(cooldown_hours, 6, "push_cooldown_hours 应为 6 小时")
        self.assertEqual(cooldown_tradable, 2, "push_cooldown_hours_tradable 应为 2 小时")

    # ============================================================
    # 补充测试：首次运行一定推送
    # ============================================================
    def test_09_first_run_always_notify(self):
        """首次运行一定推送"""
        # 不设置 last_signals，模拟首次运行
        current_signal = _make_signal(self.symbol, MarketState.OSCILLATION)

        result = self.bot._should_notify(current_signal)
        self.assertTrue(result, "首次运行应一定推送")

    # ============================================================
    # 补充测试：普通强趋势使用6小时冷却
    # ============================================================
    def test_10_normal_strong_trend_uses_6h_cooldown(self):
        """普通强趋势使用6小时冷却时间"""
        # 5小时前推送过普通强趋势
        last_signal = _make_signal(
            self.symbol,
            MarketState.NORMAL_STRONG_TREND,
            timestamp=datetime.now() - timedelta(hours=5)
        )
        self.bot.last_signals[self.symbol] = last_signal

        # 当前仍为普通强趋势
        current_signal = _make_signal(self.symbol, MarketState.NORMAL_STRONG_TREND)

        result = self.bot._should_notify(current_signal)
        self.assertFalse(result, "普通强趋势5小时前推送过，未满6小时冷却时间，不应推送")

    # ============================================================
    # 补充测试：普通强趋势冷却期满应推送
    # ============================================================
    def test_11_normal_strong_trend_cooldown_expired(self):
        """普通强趋势冷却6小时已过 - 应推送"""
        # 7小时前推送过普通强趋势
        last_signal = _make_signal(
            self.symbol,
            MarketState.NORMAL_STRONG_TREND,
            timestamp=datetime.now() - timedelta(hours=7)
        )
        self.bot.last_signals[self.symbol] = last_signal

        # 当前仍为普通强趋势
        current_signal = _make_signal(self.symbol, MarketState.NORMAL_STRONG_TREND)

        result = self.bot._should_notify(current_signal)
        self.assertTrue(result, "普通强趋势7小时前推送过，超过6小时冷却时间，应推送")

    # ============================================================
    # 补充测试：弱趋势冷却未满不应推送
    # ============================================================
    def test_12_weak_trend_cooldown_active(self):
        """弱趋势冷却2小时未满 - 不应推送"""
        # 1小时前推送过弱趋势
        last_signal = _make_signal(
            self.symbol,
            MarketState.WEAK_TREND,
            timestamp=datetime.now() - timedelta(hours=1)
        )
        self.bot.last_signals[self.symbol] = last_signal

        # 当前仍为弱趋势
        current_signal = _make_signal(self.symbol, MarketState.WEAK_TREND)

        result = self.bot._should_notify(current_signal)
        self.assertFalse(result, "弱趋势1小时前推送过，未满2小时冷却时间，不应推送")

    # ============================================================
    # 补充测试：波动率异常冷却期满应推送
    # ============================================================
    def test_13_volatility_abnormal_cooldown_expired(self):
        """波动率异常冷却6小时已过 - 应推送"""
        # 7小时前推送过波动率异常
        last_signal = _make_signal(
            self.symbol,
            MarketState.VOLATILITY_ABNORMAL,
            timestamp=datetime.now() - timedelta(hours=7)
        )
        self.bot.last_signals[self.symbol] = last_signal

        # 当前仍为波动率异常
        current_signal = _make_signal(self.symbol, MarketState.VOLATILITY_ABNORMAL)

        result = self.bot._should_notify(current_signal)
        self.assertTrue(result, "波动率异常7小时前推送过，超过6小时冷却时间，应推送")

    # ============================================================
    # 补充测试：Bot 初始化时正确读取分状态冷却配置
    # ============================================================
    def test_14_bot_reads_cooldown_config(self):
        """Bot 初始化时正确读取分状态冷却配置"""
        self.assertEqual(self.bot.push_cooldown_hours, 6,
                         "push_cooldown_hours 应为 6")
        self.assertEqual(self.bot.push_cooldown_hours_tradable, 2,
                         "push_cooldown_hours_tradable 应为 2")

    # ============================================================
    # 补充测试：状态从震荡变为极端强趋势立即推送
    # ============================================================
    def test_15_state_change_oscillation_to_extreme(self):
        """从震荡变为极端强趋势，状态变化应立即推送"""
        # 30分钟前推送过震荡状态
        last_signal = _make_signal(
            self.symbol,
            MarketState.OSCILLATION,
            timestamp=datetime.now() - timedelta(minutes=30)
        )
        self.bot.last_signals[self.symbol] = last_signal

        # 当前变为极端强趋势
        current_signal = _make_signal(self.symbol, MarketState.EXTREME_STRONG_TREND)

        result = self.bot._should_notify(current_signal)
        self.assertTrue(result, "从震荡变为极端强趋势，状态变化应立即推送")


if __name__ == '__main__':
    # 运行测试并输出详细结果
    unittest.main(verbosity=2)
