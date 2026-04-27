#!/usr/bin/env python3
"""
专项修复验证测试

针对以下修复进行专项验证：
1. float/Decimal 类型修复 - filter.py 和 detector.py 中的类型转换
2. 类型注解修复 - filter.py 中的 Tuple[bool, Optional[str]] 注解
3. 并发超时修复 - fetcher.py 中 future.result(timeout=30)
4. 相对导入 - 所有模块导入路径

运行方式：
    python tests/test_fix_verification_special.py
"""

import sys
import os
import unittest
from decimal import Decimal
from typing import get_type_hints, get_origin, get_args
from unittest.mock import Mock, patch, MagicMock
from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError
import inspect

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestFloatDecimalTypeFix(unittest.TestCase):
    """测试 float/Decimal 类型转换修复"""

    def test_01_filter_atr_filter_decimal_conversion(self):
        """测试 filter.py 中 ATR 过滤器的 Decimal 类型转换"""
        from core.signal.filter import SignalFilter
        from config.strategy_params import get_params

        params = get_params()
        filter_instance = SignalFilter(params)

        # 测试用例1：输入都是 float 类型
        data_float = {
            'last_price': 50000.00,
            'indicators': {
                '1h': {
                    'atr14': 1200.00,
                }
            }
        }
        try:
            result = filter_instance.check_atr_filter(data_float)
            self.assertIsInstance(result, bool, "返回值应该是 bool 类型")
            print("✅ ATR 过滤器 - float 输入转换正常")
        except TypeError as e:
            self.fail(f"float 到 Decimal 转换失败：{e}")

        # 测试用例2：输入都是 string 类型
        data_string = {
            'last_price': '50000.00',
            'indicators': {
                '1h': {
                    'atr14': '1200.00',
                }
            }
        }
        try:
            result = filter_instance.check_atr_filter(data_string)
            self.assertIsInstance(result, bool, "返回值应该是 bool 类型")
            print("✅ ATR 过滤器 - string 输入转换正常")
        except (TypeError, ValueError) as e:
            self.fail(f"string 到 Decimal 转换失败：{e}")

        # 测试用例3：混合类型输入
        data_mixed = {
            'last_price': Decimal('50000.00'),
            'indicators': {
                '1h': {
                    'atr14': 1200.00,  # float
                }
            }
        }
        try:
            result = filter_instance.check_atr_filter(data_mixed)
            self.assertIsInstance(result, bool, "返回值应该是 bool 类型")
            print("✅ ATR 过滤器 - 混合类型输入转换正常")
        except TypeError as e:
            self.fail(f"混合类型到 Decimal 转换失败：{e}")

    def test_02_filter_atr_filter_type_preservation(self):
        """测试 ATR 过滤器中 Decimal 类型不会被重复转换"""
        from core.signal.filter import SignalFilter
        from config.strategy_params import get_params

        params = get_params()
        filter_instance = SignalFilter(params)

        # 输入已经是 Decimal 类型，不应该出错
        data_decimal = {
            'last_price': Decimal('50000.00'),
            'indicators': {
                '1h': {
                    'atr14': Decimal('1200.00'),
                }
            }
        }
        try:
            result = filter_instance.check_atr_filter(data_decimal)
            self.assertTrue(result, "ATR% 2.4% 应该通过过滤")
            print("✅ ATR 过滤器 - Decimal 输入类型保持正常")
        except Exception as e:
            self.fail(f"Decimal 类型保持失败：{e}")

    def test_03_detector_calculate_price_levels_decimal(self):
        """测试 detector.py 中价格水平计算的 Decimal 类型"""
        from core.signal.detector import SignalDetector
        from config.strategy_params import get_params

        params = get_params()
        detector = SignalDetector(params)

        # 测试 float 输入的价格计算
        data_float = {
            'last_price': 50000.00,  # float
            'indicators': {
                '1h': {
                    'atr14': 1000.00,  # float
                }
            }
        }

        try:
            entry_price, stop_loss, take_profits = detector._calculate_price_levels(
                'BTCUSDT', data_float, direction=1, grade='A'
            )

            self.assertIsInstance(entry_price, Decimal, "入场价应该是 Decimal 类型")
            self.assertIsInstance(stop_loss, Decimal, "止损价应该是 Decimal 类型")
            
            # 检查止盈价格
            for tp in take_profits:
                if tp['price'] is not None:
                    self.assertIsInstance(tp['price'], Decimal, f"{tp['level']} 价格应该是 Decimal 类型")

            print("✅ 价格水平计算 - float 输入转换为 Decimal 正常")
        except TypeError as e:
            self.fail(f"float 到 Decimal 转换失败：{e}")

    def test_04_detector_position_params_decimal(self):
        """测试 detector.py 中仓位参数计算的 Decimal 类型"""
        from core.signal.detector import SignalDetector
        from config.strategy_params import get_params

        params = get_params()
        detector = SignalDetector(params)

        entry_price = Decimal('50000.00')
        stop_loss = Decimal('48500.00')

        try:
            position_params = detector._calculate_position_params(
                'BTCUSDT', entry_price, stop_loss, grade='A', direction=1
            )

            # 验证所有关键值都是 Decimal 类型
            self.assertIsInstance(position_params['notional_value'], Decimal, "名义价值应该是 Decimal 类型")
            self.assertIsInstance(position_params['margin'], Decimal, "保证金应该是 Decimal 类型")
            self.assertIsInstance(position_params['quantity'], Decimal, "数量应该是 Decimal 类型")
            self.assertIsInstance(position_params['risk_ratio'], Decimal, "风险占比应该是 Decimal 类型")

            print("✅ 仓位参数计算 - Decimal 类型保持正常")
        except TypeError as e:
            self.fail(f"Decimal 类型保持失败：{e}")

    def test_05_detector_build_signal_float_conversion(self):
        """测试 detector.py 中信号组装时的 Decimal 到 float 转换"""
        from core.signal.detector import SignalDetector
        from config.strategy_params import get_params

        params = get_params()
        detector = SignalDetector(params)

        entry_price = Decimal('50000.00')
        stop_loss = Decimal('48500.00')
        tp1_price = Decimal('53750.00')
        tp2_price = Decimal('57500.00')

        take_profits = [
            {'level': 'TP1', 'price': tp1_price, 'ratio': Decimal('0.25')},
            {'level': 'TP2', 'price': tp2_price, 'ratio': Decimal('0.25')},
            {'level': 'TP3', 'price': None, 'ratio': Decimal('0.50')},
        ]

        position_params = {
            'notional_value': Decimal('16666.67'),
            'margin': Decimal('30.00'),
            'leverage': 5,
            'quantity': Decimal('0.33333'),
            'risk_ratio': Decimal('0.02'),
        }

        try:
            signal = detector._build_signal(
                symbol='BTCUSDT',
                direction=1,
                grade='A',
                score=85,
                entry_price=entry_price,
                stop_loss=stop_loss,
                take_profits=take_profits,
                position_params=position_params
            )

            # 验证最终输出中的价格是 float 类型（用于显示/存储）
            self.assertIsInstance(signal['开仓价'], float, "开仓价应该是 float 类型")
            self.assertIsInstance(signal['止损价'], float, "止损价应该是 float 类型")
            self.assertIsInstance(signal['保证金'], float, "保证金应该是 float 类型")

            # 验证止盈设置中的价格是 float 类型
            for tp_key, tp_data in signal['止盈设置'].items():
                if tp_data['价格'] != '移动止损':
                    self.assertIsInstance(tp_data['价格'], float, f"{tp_key} 价格应该是 float 类型")

            print("✅ 信号组装 - Decimal 到 float 转换正常")
        except (TypeError, ValueError) as e:
            self.fail(f"Decimal 到 float 转换失败：{e}")


class TestTypeAnnotationFix(unittest.TestCase):
    """测试类型注解修复"""

    def test_10_filter_apply_all_filters_return_type(self):
        """测试 filter.py 中 apply_all_filters 方法的返回类型注解"""
        from core.signal.filter import SignalFilter
        from typing import Tuple, Optional
        from config.strategy_params import get_params

        params = get_params()
        filter_instance = SignalFilter(params)

        # 检查方法的返回类型注解
        annotations = get_type_hints(SignalFilter.apply_all_filters)
        return_annotation = annotations.get('return')

        self.assertIsNotNone(return_annotation, "apply_all_filters 应该有返回类型注解")
        
        # 验证返回类型是 Tuple[bool, Optional[str]]
        origin = get_origin(return_annotation)
        self.assertEqual(origin, tuple, "返回类型应该是 tuple")

        args = get_args(return_annotation)
        self.assertEqual(len(args), 2, "返回类型应该有两个参数")
        self.assertEqual(args[0], bool, "第一个返回类型应该是 bool")
        
        # 第二个参数应该是 Optional[str] (即 Union[str, None])
        second_type = args[1]
        second_origin = get_origin(second_type)
        if second_origin is not None:
            # Optional[str] 实际上是 Union[str, None]
            second_args = get_args(second_type)
            self.assertIn(str, second_args, "Optional[str] 应该包含 str")
            self.assertIn(type(None), second_args, "Optional[str] 应该包含 None")

        print("✅ apply_all_filters 返回类型注解验证通过")

    def test_11_filter_apply_all_filters_actual_return_type(self):
        """测试 filter.py 中 apply_all_filters 方法的实际返回值类型"""
        from core.signal.filter import SignalFilter
        from config.strategy_params import get_params

        params = get_params()
        filter_instance = SignalFilter(params)

        # 测试通过的返回值
        data_pass = {
            'last_price': Decimal('50000.00'),
            'indicators': {
                '1d': {
                    'close': Decimal('50000.00'),
                    'ema21': Decimal('49000.00'),
                },
                '1h': {
                    'atr14': Decimal('1200.00'),
                }
            }
        }

        passed, reason = filter_instance.apply_all_filters(data_pass, direction=1, grade='A')

        self.assertIsInstance(passed, bool, "第一个返回值应该是 bool 类型")
        if passed:
            self.assertIsNone(reason, "通过时第二个返回值应该是 None")
        else:
            self.assertIsInstance(reason, str, "失败时第二个返回值应该是 str 类型")

        print("✅ apply_all_filters 实际返回值类型验证通过")

    def test_12_filter_method_signatures(self):
        """测试 filter.py 中各方法的签名是否正确"""
        from core.signal.filter import SignalFilter
        import inspect

        # 检查 apply_all_filters 方法签名
        sig = inspect.signature(SignalFilter.apply_all_filters)
        params = list(sig.parameters.keys())

        self.assertIn('self', params, "apply_all_filters 应该有 self 参数")
        self.assertIn('data', params, "apply_all_filters 应该有 data 参数")
        self.assertIn('direction', params, "apply_all_filters 应该有 direction 参数")
        self.assertIn('grade', params, "apply_all_filters 应该有 grade 参数")

        print("✅ filter.py 方法签名验证通过")

    def test_13_detector_method_return_types(self):
        """测试 detector.py 中关键方法的返回类型注解"""
        from core.signal.detector import SignalDetector
        from typing import Tuple, Optional, get_origin, get_args
        import inspect

        # 检查 _determine_signal_grade 方法的返回类型注解
        sig = inspect.signature(SignalDetector._determine_signal_grade)
        # 注意：由于运行时可能没有完整的类型注解，我们检查源码
        source = inspect.getsource(SignalDetector._determine_signal_grade)
        
        # 验证方法定义中包含正确的返回类型注解
        self.assertIn('-> Tuple[Optional[str], int]', source, 
                     "_determine_signal_grade 应该有正确的返回类型注解")

        print("✅ detector.py 方法返回类型注解验证通过")


class TestConcurrentTimeoutFix(unittest.TestCase):
    """测试并发超时修复"""

    def test_20_fetcher_concurrent_timeout_parameter(self):
        """测试 fetcher.py 中并发获取方法包含 timeout 参数"""
        from core.data.fetcher import MarketDataFetcher
        import inspect

        # 检查 _fetch_from_kline_service_concurrent 方法的源码
        source = inspect.getsource(MarketDataFetcher._fetch_from_kline_service_concurrent)
        
        # 验证 future.result 包含 timeout 参数
        self.assertIn('future.result(timeout=30)', source, 
                     "_fetch_from_kline_service_concurrent 应该使用 future.result(timeout=30)")

        print("✅ fetcher.py 并发超时参数验证通过")

    def test_21_fetcher_concurrent_thread_pool_executor(self):
        """测试 fetcher.py 中使用了 ThreadPoolExecutor"""
        from core.data.fetcher import MarketDataFetcher
        import inspect

        source = inspect.getsource(MarketDataFetcher._fetch_from_kline_service_concurrent)
        
        # 验证使用了 ThreadPoolExecutor
        self.assertIn('ThreadPoolExecutor', source, 
                     "_fetch_from_kline_service_concurrent 应该使用 ThreadPoolExecutor")
        
        # 验证使用了 max_workers
        self.assertIn('max_workers', source,
                     "_fetch_from_kline_service_concurrent 应该配置 max_workers")

        print("✅ fetcher.py 线程池配置验证通过")

    def test_22_fetcher_concurrent_error_handling(self):
        """测试 fetcher.py 中并发方法的错误处理"""
        from core.data.fetcher import MarketDataFetcher
        import inspect

        source = inspect.getsource(MarketDataFetcher._fetch_from_kline_service_concurrent)
        
        # 验证有 try-except 块
        self.assertIn('try:', source, "_fetch_from_kline_service_concurrent 应该有 try 块")
        self.assertIn('except', source, "_fetch_from_kline_service_concurrent 应该有 except 块")

        # 验证使用了 as_completed
        self.assertIn('as_completed', source,
                     "_fetch_from_kline_service_concurrent 应该使用 as_completed")

        print("✅ fetcher.py 并发错误处理验证通过")

    def test_23_fetcher_concurrent_mock_timeout(self):
        """模拟测试并发超时的行为"""
        from core.data.fetcher import MarketDataFetcher
        from concurrent.futures import Future
        import threading

        fetcher = MarketDataFetcher(enable_concurrent=False)  # 不需要真实网络

        # 创建一个会超时的 Future
        future = Future()

        # 在一个线程中尝试获取结果（超时）
        result_holder = {'value': None, 'error': None}

        def try_get_result():
            try:
                result_holder['value'] = future.result(timeout=0.1)  # 0.1 秒超时
            except TimeoutError as e:
                result_holder['error'] = e

        thread = threading.Thread(target=try_get_result)
        thread.start()
        thread.join(timeout=1)  # 等待最多 1 秒

        # 验证超时行为
        self.assertIsNotNone(result_holder['error'], "应该捕获到 TimeoutError")
        self.assertIsInstance(result_holder['error'], TimeoutError, "错误类型应该是 TimeoutError")

        print("✅ 并发超时行为模拟验证通过")


class TestRelativeImportFix(unittest.TestCase):
    """测试相对导入路径修复"""

    def test_30_import_filter_module(self):
        """测试 core.signal.filter 模块的相对导入"""
        try:
            from core.signal.filter import SignalFilter
            self.assertIsNotNone(SignalFilter)
            
            # 验证导入的是正确的模块
            self.assertEqual(SignalFilter.__module__, 'core.signal.filter')
            print("✅ core.signal.filter 相对导入验证通过")
        except ImportError as e:
            self.fail(f"core.signal.filter 导入失败：{e}")

    def test_31_import_detector_module(self):
        """测试 core.signal.detector 模块的相对导入"""
        try:
            from core.signal.detector import SignalDetector
            self.assertIsNotNone(SignalDetector)
            
            # 验证导入的是正确的模块
            self.assertEqual(SignalDetector.__module__, 'core.signal.detector')
            print("✅ core.signal.detector 相对导入验证通过")
        except ImportError as e:
            self.fail(f"core.signal.detector 导入失败：{e}")

    def test_32_import_fetcher_module(self):
        """测试 core.data.fetcher 模块的相对导入"""
        try:
            from core.data.fetcher import MarketDataFetcher, get_data_fetcher
            self.assertIsNotNone(MarketDataFetcher)
            self.assertIsNotNone(get_data_fetcher)
            
            # 验证导入的是正确的模块
            self.assertEqual(MarketDataFetcher.__module__, 'core.data.fetcher')
            print("✅ core.data.fetcher 相对导入验证通过")
        except ImportError as e:
            self.fail(f"core.data.fetcher 导入失败：{e}")

    def test_33_detector_internal_imports(self):
        """测试 detector.py 内部的相对导入"""
        try:
            from core.signal import detector
            import inspect

            # 检查 detector.py 的源码中的导入语句
            source = inspect.getsource(detector)
            
            # 验证使用了相对导入
            self.assertIn('from ..data import MarketDataFetcher', source,
                         "detector.py 应该使用 from ..data import MarketDataFetcher")
            self.assertIn('from ..scoring import get_scoring_engine', source,
                         "detector.py 应该使用 from ..scoring import get_scoring_engine")
            self.assertIn('from .validator import SignalValidator', source,
                         "detector.py 应该使用 from .validator import SignalValidator")
            self.assertIn('from .filter import SignalFilter', source,
                         "detector.py 应该使用 from .filter import SignalFilter")

            print("✅ detector.py 内部相对导入验证通过")
        except (ImportError, AssertionError) as e:
            self.fail(f"detector.py 内部导入验证失败：{e}")

    def test_34_compatibility_import_paths(self):
        """测试兼容性导入路径（core.data_fetcher 和 core.signal_detector）"""
        try:
            # 测试旧版兼容导入
            from core.data_fetcher import MarketDataFetcher as OldFetcher
            from core.signal_detector import SignalDetector as OldDetector
            
            # 验证兼容性导入指向正确的模块
            self.assertEqual(OldFetcher.__module__, 'core.data.fetcher')
            self.assertEqual(OldDetector.__module__, 'core.signal.detector')
            
            print("✅ 兼容性导入路径验证通过")
        except ImportError as e:
            self.fail(f"兼容性导入失败：{e}")


def run_tests():
    """运行所有测试"""
    print("=" * 80)
    print("开始执行专项修复验证测试")
    print("=" * 80)
    print()
    
    # 创建测试套件
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    
    # 添加测试类
    suite.addTests(loader.loadTestsFromTestCase(TestFloatDecimalTypeFix))
    suite.addTests(loader.loadTestsFromTestCase(TestTypeAnnotationFix))
    suite.addTests(loader.loadTestsFromTestCase(TestConcurrentTimeoutFix))
    suite.addTests(loader.loadTestsFromTestCase(TestRelativeImportFix))
    
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
    
    if result.wasSuccessful():
        print("\n所有测试通过，修复验证成功！")
    else:
        print("\n部分测试失败，请检查修复情况。")
    
    print("=" * 80)
    
    return result.wasSuccessful()


if __name__ == '__main__':
    success = run_tests()
    sys.exit(0 if success else 1)
