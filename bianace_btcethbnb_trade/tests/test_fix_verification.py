#!/usr/bin/env python3
"""
综合修复验证测试

验证内容：
1. float/Decimal 类型转换修复
2. 相对导入路径修复
3. 调度器时间配置验证

测试文件：
- core/signal/filter.py
- core/signal/detector.py
- core/data/fetcher.py
- core/data_fetcher.py
- core/signal_detector.py
- scheduler_new.py

运行方式：
    python tests/test_fix_verification.py
"""

import sys
import os
import unittest
from decimal import Decimal
from datetime import datetime
from typing import Dict, Any, List

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestImportPaths(unittest.TestCase):
    """测试导入路径是否正确"""

    def test_01_import_core_data_fetcher(self):
        """测试 core.data_fetcher 兼容性导入"""
        try:
            from core.data_fetcher import MarketDataFetcher, get_data_fetcher
            self.assertIsNotNone(MarketDataFetcher)
            self.assertIsNotNone(get_data_fetcher)
            print("✅ core.data_fetcher 导入成功")
        except ImportError as e:
            self.fail(f"core.data_fetcher 导入失败：{e}")

    def test_02_import_core_signal_detector(self):
        """测试 core.signal_detector 兼容性导入"""
        try:
            from core.signal_detector import SignalDetector, get_signal_detector
            self.assertIsNotNone(SignalDetector)
            self.assertIsNotNone(get_signal_detector)
            print("✅ core.signal_detector 导入成功")
        except ImportError as e:
            self.fail(f"core.signal_detector 导入失败：{e}")

    def test_03_import_core_signal_filter(self):
        """测试 core.signal.filter 导入"""
        try:
            from core.signal.filter import SignalFilter
            self.assertIsNotNone(SignalFilter)
            print("✅ core.signal.filter 导入成功")
        except ImportError as e:
            self.fail(f"core.signal.filter 导入失败：{e}")

    def test_04_import_core_signal_validator(self):
        """测试 core.signal.validator 导入"""
        try:
            from core.signal.validator import SignalValidator
            self.assertIsNotNone(SignalValidator)
            print("✅ core.signal.validator 导入成功")
        except ImportError as e:
            self.fail(f"core.signal.validator 导入失败：{e}")

    def test_05_import_core_data_fetcher_module(self):
        """测试 core.data.fetcher 导入"""
        try:
            from core.data.fetcher import MarketDataFetcher as Fetcher, get_data_fetcher as get_fetcher
            self.assertIsNotNone(Fetcher)
            self.assertIsNotNone(get_fetcher)
            print("✅ core.data.fetcher 导入成功")
        except ImportError as e:
            self.fail(f"core.data.fetcher 导入失败：{e}")

    def test_06_import_core_scoring(self):
        """测试 core.scoring 导入"""
        try:
            from core.scoring import get_scoring_engine, ScoringEngineV612
            self.assertIsNotNone(get_scoring_engine)
            self.assertIsNotNone(ScoringEngineV612)
            print("✅ core.scoring 导入成功")
        except ImportError as e:
            self.fail(f"core.scoring 导入失败：{e}")

    def test_07_import_scheduler_new(self):
        """测试 scheduler_new 导入"""
        try:
            # 注意：不要导入整个模块，因为它会初始化调度器
            # 只验证文件存在
            scheduler_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                'scheduler_new.py'
            )
            self.assertTrue(os.path.exists(scheduler_path), f"文件不存在：{scheduler_path}")
            print("✅ scheduler_new.py 文件存在")
        except Exception as e:
            self.fail(f"scheduler_new 验证失败：{e}")


class TestDecimalTypeConversion(unittest.TestCase):
    """测试 Decimal 类型转换逻辑"""

    def setUp(self):
        """设置测试数据"""
        from core.signal.filter import SignalFilter
        from core.signal.validator import SignalValidator
        from config.strategy_params import get_params
        
        self.params = get_params()
        self.filter = SignalFilter(self.params)
        self.validator = SignalValidator(self.params)

    def _create_test_data(self, **kwargs) -> Dict[str, Any]:
        """创建测试数据"""
        default_data = {
            'symbol': 'BTCUSDT',
            'last_price': Decimal('50000.00'),
            'price_change_24h': Decimal('0.05'),
            'funding_rate': Decimal('0.0001'),
            'indicators': {
                '1d': {
                    'close': Decimal('50000.00'),
                    'ema21': Decimal('49000.00'),
                    'rsi': Decimal('55.0'),
                    'atr14': Decimal('800.00'),
                },
                '4h': {
                    'close': Decimal('50000.00'),
                    'ema21': Decimal('49500.00'),
                    'rsi': Decimal('52.0'),
                },
                '1h': {
                    'close': Decimal('50000.00'),
                    'ema21': Decimal('49800.00'),
                    'rsi': Decimal('50.0'),
                    'atr14': Decimal('500.00'),
                    'volume': [100.0, 120.0, 110.0],
                },
            },
        }
        default_data.update(kwargs)
        return default_data

    def test_10_trend_direction_long(self):
        """测试趋势方向判断 - 多头"""
        data = self._create_test_data()
        direction = self.filter.determine_trend_direction(data)
        self.assertEqual(direction, 1, "价格 > EMA21 应该返回多头方向")
        print("✅ 趋势方向判断 - 多头 测试通过")

    def test_11_trend_direction_short(self):
        """测试趋势方向判断 - 空头"""
        data = self._create_test_data(
            indicators={
                '1d': {
                    'close': Decimal('48000.00'),
                    'ema21': Decimal('49000.00'),
                }
            }
        )
        direction = self.filter.determine_trend_direction(data)
        self.assertEqual(direction, -1, "价格 < EMA21 应该返回空头方向")
        print("✅ 趋势方向判断 - 空头 测试通过")

    def test_12_trend_direction_neutral(self):
        """测试趋势方向判断 - 中性"""
        data = self._create_test_data(
            indicators={
                '1d': {
                    'close': Decimal('49000.00'),
                    'ema21': Decimal('49000.00'),
                }
            }
        )
        direction = self.filter.determine_trend_direction(data)
        self.assertEqual(direction, 0, "价格 = EMA21 应该返回中性")
        print("✅ 趋势方向判断 - 中性 测试通过")

    def test_13_atr_filter_normal(self):
        """测试 ATR 过滤器 - 正常波动率"""
        # ATR% = 500 / 50000 = 1%，低于 2% 应该被过滤
        data = self._create_test_data(
            last_price=Decimal('50000.00'),
            indicators={
                '1h': {
                    'atr14': Decimal('1200.00'),  # 1200/50000 = 2.4%，在正常范围内
                }
            }
        )
        result = self.filter.check_atr_filter(data)
        self.assertTrue(result, "ATR% 在正常范围内应该通过过滤")
        print("✅ ATR 过滤器 - 正常波动率 测试通过")

    def test_14_atr_filter_too_low(self):
        """测试 ATR 过滤器 - 波动率过低"""
        data = self._create_test_data(
            last_price=Decimal('50000.00'),
            indicators={
                '1h': {
                    'atr14': Decimal('500.00'),  # 500/50000 = 1%，低于 2%
                }
            }
        )
        result = self.filter.check_atr_filter(data)
        self.assertFalse(result, "ATR% 过低应该被过滤")
        print("✅ ATR 过滤器 - 波动率过低 测试通过")

    def test_15_atr_filter_too_high(self):
        """测试 ATR 过滤器 - 波动率过高"""
        data = self._create_test_data(
            last_price=Decimal('50000.00'),
            indicators={
                '1h': {
                    'atr14': Decimal('3000.00'),  # 3000/50000 = 6%，高于 4.5%
                }
            }
        )
        result = self.filter.check_atr_filter(data)
        self.assertFalse(result, "ATR% 过高应该被过滤")
        print("✅ ATR 过滤器 - 波动率过高 测试通过")

    def test_16_atr_filter_with_float_input(self):
        """测试 ATR 过滤器 - 输入为 float 类型"""
        data = self._create_test_data(
            last_price=50000.00,  # float 类型
            indicators={
                '1h': {
                    'atr14': 1200.00,  # float 类型
                }
            }
        )
        result = self.filter.check_atr_filter(data)
        self.assertTrue(result, "输入为 float 类型应该正常转换并处理")
        print("✅ ATR 过滤器 - float 输入转换 测试通过")

    def test_17_atr_filter_with_string_input(self):
        """测试 ATR 过滤器 - 输入为 string 类型"""
        data = self._create_test_data(
            last_price='50000.00',  # string 类型
            indicators={
                '1h': {
                    'atr14': '1200.00',  # string 类型
                }
            }
        )
        result = self.filter.check_atr_filter(data)
        self.assertTrue(result, "输入为 string 类型应该正常转换并处理")
        print("✅ ATR 过滤器 - string 输入转换 测试通过")

    def test_18_validator_prohibited_conditions(self):
        """测试验证器 - 禁止交易情形"""
        # 正常情况
        data = self._create_test_data(
            price_change_24h=Decimal('0.05'),
            funding_rate=Decimal('0.0001')
        )
        is_valid, reason = self.validator.validate_signal(data)
        self.assertTrue(is_valid, "正常情况应该通过验证")
        print("✅ 验证器 - 正常情况 测试通过")

    def test_19_validator_price_surge(self):
        """测试验证器 - 价格暴涨"""
        data = self._create_test_data(
            price_change_24h=Decimal('0.30'),  # 30% 涨幅，超过 25%
        )
        is_valid = self.validator.check_prohibited_conditions(data)
        self.assertFalse(is_valid, "24 小时涨幅超过 25% 应该禁止交易")
        print("✅ 验证器 - 价格暴涨 测试通过")

    def test_20_validator_price_drop(self):
        """测试验证器 - 价格暴跌"""
        data = self._create_test_data(
            price_change_24h=Decimal('-0.25'),  # 25% 跌幅，超过 20%
        )
        is_valid = self.validator.check_prohibited_conditions(data)
        self.assertFalse(is_valid, "24 小时跌幅超过 20% 应该禁止交易")
        print("✅ 验证器 - 价格暴跌 测试通过")

    def test_21_validator_high_funding_rate(self):
        """测试验证器 - 高资金费率"""
        data = self._create_test_data(
            funding_rate=Decimal('0.001'),  # 0.1%，超过 0.08%
        )
        is_valid = self.validator.check_prohibited_conditions(data)
        self.assertFalse(is_valid, "资金费率超过 0.08% 应该禁止交易")
        print("✅ 验证器 - 高资金费率 测试通过")


class TestDetectorIntegration(unittest.TestCase):
    """测试 SignalDetector 集成"""

    def test_30_detector_initialization(self):
        """测试 SignalDetector 初始化"""
        try:
            from core.signal.detector import SignalDetector
            from config.strategy_params import get_params
            
            params = get_params()
            detector = SignalDetector(params)
            
            self.assertIsNotNone(detector.params)
            self.assertIsNotNone(detector.data_fetcher)
            self.assertIsNotNone(detector.scoring_engine)
            self.assertIsNotNone(detector.validator)
            self.assertIsNotNone(detector.filter)
            print("✅ SignalDetector 初始化 测试通过")
        except Exception as e:
            self.fail(f"SignalDetector 初始化失败：{e}")

    def test_31_calculate_price_levels_long(self):
        """测试价格水平计算 - 多头"""
        try:
            from core.signal.detector import SignalDetector
            from config.strategy_params import get_params
            from decimal import Decimal
            
            params = get_params()
            detector = SignalDetector(params)
            
            data = {
                'last_price': Decimal('50000.00'),
                'indicators': {
                    '1h': {
                        'atr14': Decimal('1000.00'),
                    }
                }
            }
            
            entry_price, stop_loss, take_profits = detector._calculate_price_levels(
                'BTCUSDT', data, direction=1, grade='A'
            )
            
            self.assertIsNotNone(entry_price, "入场价不应该为空")
            self.assertIsNotNone(stop_loss, "止损价不应该为空")
            self.assertIsInstance(entry_price, Decimal, "入场价应该是 Decimal 类型")
            self.assertIsInstance(stop_loss, Decimal, "止损价应该是 Decimal 类型")
            
            # 多头：止损价应该低于入场价
            self.assertLess(stop_loss, entry_price, "多头止损价应该低于入场价")
            
            # 检查止盈价
            self.assertEqual(len(take_profits), 3, "应该有 3 个止盈级别")
            self.assertIsNotNone(take_profits[0]['price'], "TP1 价格不应该为空")
            self.assertIsNotNone(take_profits[1]['price'], "TP2 价格不应该为空")
            self.assertIsNone(take_profits[2]['price'], "TP3 应该使用移动止损")
            
            print("✅ 价格水平计算 - 多头 测试通过")
        except Exception as e:
            self.fail(f"价格水平计算失败：{e}")

    def test_32_calculate_price_levels_short(self):
        """测试价格水平计算 - 空头"""
        try:
            from core.signal.detector import SignalDetector
            from config.strategy_params import get_params
            from decimal import Decimal
            
            params = get_params()
            detector = SignalDetector(params)
            
            data = {
                'last_price': Decimal('50000.00'),
                'indicators': {
                    '1h': {
                        'atr14': Decimal('1000.00'),
                    }
                }
            }
            
            entry_price, stop_loss, take_profits = detector._calculate_price_levels(
                'BTCUSDT', data, direction=-1, grade='A'
            )
            
            self.assertIsNotNone(entry_price, "入场价不应该为空")
            self.assertIsNotNone(stop_loss, "止损价不应该为空")
            
            # 空头：止损价应该高于入场价
            self.assertGreater(stop_loss, entry_price, "空头止损价应该高于入场价")
            
            # 空头：止盈价应该低于入场价
            self.assertLess(take_profits[0]['price'], entry_price, "空头 TP1 应该低于入场价")
            self.assertLess(take_profits[1]['price'], entry_price, "空头 TP2 应该低于入场价")
            
            print("✅ 价格水平计算 - 空头 测试通过")
        except Exception as e:
            self.fail(f"价格水平计算失败：{e}")

    def test_33_calculate_position_params(self):
        """测试仓位参数计算"""
        try:
            from core.signal.detector import SignalDetector
            from config.strategy_params import get_params
            from decimal import Decimal
            
            params = get_params()
            detector = SignalDetector(params)
            
            entry_price = Decimal('50000.00')
            stop_loss = Decimal('48500.00')  # 3% 止损
            
            position_params = detector._calculate_position_params(
                'BTCUSDT', entry_price, stop_loss, grade='A', direction=1
            )
            
            self.assertIn('notional_value', position_params)
            self.assertIn('margin', position_params)
            self.assertIn('leverage', position_params)
            self.assertIn('quantity', position_params)
            self.assertIn('risk_ratio', position_params)
            
            self.assertEqual(position_params['leverage'], 5, "杠杆应该为 5")
            self.assertIsInstance(position_params['margin'], Decimal, "保证金应该是 Decimal 类型")
            self.assertIsInstance(position_params['quantity'], Decimal, "数量应该是 Decimal 类型")
            
            print("✅ 仓位参数计算 测试通过")
        except Exception as e:
            self.fail(f"仓位参数计算失败：{e}")


class TestSchedulerTimeConfig(unittest.TestCase):
    """测试调度器时间配置"""

    def test_40_scheduler_file_exists(self):
        """测试调度器文件存在"""
        scheduler_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            'scheduler_new.py'
        )
        self.assertTrue(os.path.exists(scheduler_path))
        print("✅ scheduler_new.py 文件存在 测试通过")

    def test_41_scheduler_time_config(self):
        """测试调度器时间配置是否正确"""
        scheduler_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            'scheduler_new.py'
        )
        
        with open(scheduler_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # 验证每小时 25 分执行
        self.assertIn("minute=25", content, "应该配置每小时 25 分执行")
        self.assertIn("hour='*'", content, "应该配置每小时执行")
        print("✅ 调度器每小时 25 分执行配置 测试通过")

        # 验证日报 9:10 执行
        self.assertIn("hour=9, minute=10", content, "应该配置每天 9:10 发送日报")
        print("✅ 调度器日报 9:10 执行配置 测试通过")

    def test_42_scheduler_cron_expression(self):
        """测试 Cron 表达式是否正确"""
        try:
            from apscheduler.triggers.cron import CronTrigger
            import pytz
            
            # 测试每小时 25 分
            trigger_hourly = CronTrigger(hour='*', minute=25, timezone=pytz.timezone('Asia/Shanghai'))
            self.assertIsNotNone(trigger_hourly)
            print("✅ 每小时 25 分 Cron 表达式 测试通过")

            # 测试每天 9:10
            trigger_daily = CronTrigger(hour=9, minute=10, timezone=pytz.timezone('Asia/Shanghai'))
            self.assertIsNotNone(trigger_daily)
            print("✅ 每天 9:10 Cron 表达式 测试通过")
            
        except ImportError as e:
            self.skipTest(f"apscheduler 未安装：{e}")


class TestDataFetcherIntegration(unittest.TestCase):
    """测试数据获取模块集成"""

    def test_50_data_fetcher_initialization(self):
        """测试 MarketDataFetcher 初始化"""
        try:
            from core.data.fetcher import MarketDataFetcher
            
            fetcher = MarketDataFetcher(
                cache_duration_hours=1,
                max_workers=5,
                enable_concurrent=True
            )
            
            self.assertIsNotNone(fetcher.cache)
            self.assertEqual(fetcher.max_workers, 5)
            self.assertTrue(fetcher.enable_concurrent)
            print("✅ MarketDataFetcher 初始化 测试通过")
        except Exception as e:
            self.fail(f"MarketDataFetcher 初始化失败：{e}")

    def test_51_data_fetcher_singleton(self):
        """测试单例模式"""
        try:
            from core.data.fetcher import get_data_fetcher
            
            fetcher1 = get_data_fetcher()
            fetcher2 = get_data_fetcher()
            
            self.assertIs(fetcher1, fetcher2, "应该返回同一个实例")
            print("✅ 单例模式 测试通过")
        except Exception as e:
            self.fail(f"单例模式测试失败：{e}")


class TestFilterAllFilters(unittest.TestCase):
    """测试过滤器组合"""

    def test_60_apply_all_filters_pass(self):
        """测试所有过滤器 - 通过"""
        try:
            from core.signal.filter import SignalFilter
            from config.strategy_params import get_params
            from decimal import Decimal
            
            params = get_params()
            filter_instance = SignalFilter(params)
            
            data = {
                'last_price': Decimal('50000.00'),
                'indicators': {
                    '1d': {
                        'close': Decimal('50000.00'),
                        'ema21': Decimal('49000.00'),
                    },
                    '1h': {
                        'atr14': Decimal('1200.00'),  # 2.4% ATR
                    }
                }
            }
            
            passed, reason = filter_instance.apply_all_filters(data, direction=1, grade='A')
            self.assertTrue(passed, "应该通过所有过滤器")
            self.assertIsNone(reason, "失败原因应该为空")
            print("✅ 所有过滤器 - 通过 测试通过")
        except Exception as e:
            self.fail(f"过滤器组合测试失败：{e}")

    def test_61_apply_all_filters_atr_fail(self):
        """测试所有过滤器 - ATR 过滤失败"""
        try:
            from core.signal.filter import SignalFilter
            from config.strategy_params import get_params
            from decimal import Decimal
            
            params = get_params()
            filter_instance = SignalFilter(params)
            
            data = {
                'last_price': Decimal('50000.00'),
                'indicators': {
                    '1d': {
                        'close': Decimal('50000.00'),
                        'ema21': Decimal('49000.00'),
                    },
                    '1h': {
                        'atr14': Decimal('500.00'),  # 1% ATR，过低
                    }
                }
            }
            
            passed, reason = filter_instance.apply_all_filters(data, direction=1, grade='A')
            self.assertFalse(passed, "ATR 过滤失败应该返回 False")
            self.assertIsNotNone(reason, "应该有失败原因")
            print("✅ 所有过滤器 - ATR 过滤失败 测试通过")
        except Exception as e:
            self.fail(f"过滤器组合测试失败：{e}")


def run_tests():
    """运行所有测试"""
    print("=" * 80)
    print("开始执行修复验证测试")
    print("=" * 80)
    print()
    
    # 创建测试套件
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    
    # 添加测试类
    suite.addTests(loader.loadTestsFromTestCase(TestImportPaths))
    suite.addTests(loader.loadTestsFromTestCase(TestDecimalTypeConversion))
    suite.addTests(loader.loadTestsFromTestCase(TestDetectorIntegration))
    suite.addTests(loader.loadTestsFromTestCase(TestSchedulerTimeConfig))
    suite.addTests(loader.loadTestsFromTestCase(TestDataFetcherIntegration))
    suite.addTests(loader.loadTestsFromTestCase(TestFilterAllFilters))
    
    # 运行测试
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    
    # 打印总结
    print()
    print("=" * 80)
    print("测试总结")
    print("=" * 80)
    print(f"总测试数：{result.testsRun}")
    print(f"通过：{result.testsRun - len(result.failures) - len(result.errors)}")
    print(f"失败：{len(result.failures)}")
    print(f"错误：{len(result.errors)}")
    
    if result.failures:
        print("\n失败的测试：")
        for test, traceback in result.failures:
            print(f"  - {test}: {traceback}")
    
    if result.errors:
        print("\n错误的测试：")
        for test, traceback in result.errors:
            print(f"  - {test}: {traceback}")
    
    print("=" * 80)
    
    return result.wasSuccessful()


if __name__ == '__main__':
    success = run_tests()
    sys.exit(0 if success else 1)
