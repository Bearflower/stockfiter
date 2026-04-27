#!/usr/bin/env python3
"""
缓存和并发功能单元测试

测试增强的缓存功能和并发数据获取功能。

版本: v1.0.0
创建时间: 2026-04-27
"""

import unittest
from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import Mock, patch, MagicMock
import time

from core.data.cache import DataCache, CacheStats, cache_result


class TestCacheStats(unittest.TestCase):
    """测试缓存统计"""

    def test_init(self):
        """测试初始化"""
        stats = CacheStats()
        self.assertEqual(stats.hits, 0)
        self.assertEqual(stats.misses, 0)
        self.assertEqual(stats.evictions, 0)

    def test_record_hit(self):
        """测试记录命中"""
        stats = CacheStats()
        stats.record_hit()
        self.assertEqual(stats.hits, 1)

    def test_record_miss(self):
        """测试记录未命中"""
        stats = CacheStats()
        stats.record_miss()
        self.assertEqual(stats.misses, 1)

    def test_hit_rate(self):
        """测试命中率计算"""
        stats = CacheStats()
        stats.hits = 8
        stats.misses = 2
        self.assertEqual(stats.hit_rate, 0.8)

    def test_reset(self):
        """测试重置统计"""
        stats = CacheStats()
        stats.hits = 10
        stats.misses = 5
        stats.reset()
        self.assertEqual(stats.hits, 0)
        self.assertEqual(stats.misses, 0)


class TestDataCache(unittest.TestCase):
    """测试数据缓存"""

    def setUp(self):
        """测试前准备"""
        self.cache = DataCache(maxsize=10, ttl_seconds=60, enable_stats=True)

    def test_init(self):
        """测试初始化"""
        self.assertEqual(self.cache.maxsize, 10)
        self.assertEqual(self.cache.ttl_seconds, 60)
        self.assertTrue(self.cache.enable_stats)
        self.assertIsNotNone(self.cache.stats)

    def test_set_and_get(self):
        """测试设置和获取缓存"""
        data = {'last_price': Decimal('50000'), 'symbol': 'BTCUSDT'}
        self.cache.set('BTCUSDT', data)

        result = self.cache.get('BTCUSDT')
        self.assertIsNotNone(result)
        self.assertEqual(result['symbol'], 'BTCUSDT')
        self.assertEqual(result['last_price'], Decimal('50000'))

    def test_get_nonexistent(self):
        """测试获取不存在的缓存"""
        result = self.cache.get('NOTEXIST')
        self.assertIsNone(result)

    def test_set_all_and_get_all(self):
        """测试批量设置和获取"""
        data = {
            'BTCUSDT': {'last_price': Decimal('50000')},
            'ETHUSDT': {'last_price': Decimal('3000')}
        }
        self.cache.set_all(data)

        result = self.cache.get_all()
        self.assertEqual(len(result), 2)
        self.assertIn('BTCUSDT', result)
        self.assertIn('ETHUSDT', result)

    def test_clear(self):
        """测试清除缓存"""
        self.cache.set('BTCUSDT', {'price': 50000})
        self.cache.clear()

        result = self.cache.get('BTCUSDT')
        self.assertIsNone(result)

    def test_remove(self):
        """测试移除单个缓存"""
        self.cache.set('BTCUSDT', {'price': 50000})
        self.cache.set('ETHUSDT', {'price': 3000})

        self.cache.remove('BTCUSDT')

        self.assertIsNone(self.cache.get('BTCUSDT'))
        self.assertIsNotNone(self.cache.get('ETHUSDT'))

    def test_has_symbol(self):
        """测试检查交易对是否存在"""
        self.cache.set('BTCUSDT', {'price': 50000})

        self.assertTrue(self.cache.has_symbol('BTCUSDT'))
        self.assertFalse(self.cache.has_symbol('ETHUSDT'))

    def test_get_symbols(self):
        """测试获取所有交易对"""
        self.cache.set('BTCUSDT', {'price': 50000})
        self.cache.set('ETHUSDT', {'price': 3000})

        symbols = self.cache.get_symbols()
        self.assertEqual(len(symbols), 2)
        self.assertIn('BTCUSDT', symbols)
        self.assertIn('ETHUSDT', symbols)

    def test_is_valid(self):
        """测试缓存有效性检查"""
        # 空缓存无效
        self.assertFalse(self.cache.is_valid())

        # 设置缓存后有效
        self.cache.set('BTCUSDT', {'price': 50000})
        self.assertTrue(self.cache.is_valid(['BTCUSDT']))

        # 检查不存在的交易对
        self.assertFalse(self.cache.is_valid(['ETHUSDT']))

    def test_get_stats(self):
        """测试获取统计信息"""
        self.cache.set('BTCUSDT', {'price': 50000})
        self.cache.get('BTCUSDT')  # 命中
        self.cache.get('ETHUSDT')  # 未命中

        stats = self.cache.get_stats()
        self.assertIsNotNone(stats)
        self.assertEqual(stats['hits'], 1)
        self.assertEqual(stats['misses'], 1)
        self.assertEqual(stats['cache_size'], 1)

    def test_get_size(self):
        """测试获取缓存大小"""
        self.assertEqual(self.cache.get_size(), 0)

        self.cache.set('BTCUSDT', {'price': 50000})
        self.assertEqual(self.cache.get_size(), 1)

    def test_is_empty(self):
        """测试检查缓存是否为空"""
        self.assertTrue(self.cache.is_empty())

        self.cache.set('BTCUSDT', {'price': 50000})
        self.assertFalse(self.cache.is_empty())


class TestCacheDecorator(unittest.TestCase):
    """测试缓存装饰器"""

    def test_cache_result(self):
        """测试缓存装饰器"""
        # 检查 cachetools 是否可用
        try:
            from cachetools import TTLCache
            cache_available = True
        except ImportError:
            cache_available = False

        call_count = 0

        @cache_result(maxsize=10, ttl_seconds=60)
        def expensive_function(x):
            nonlocal call_count
            call_count += 1
            return x * 2

        # 第一次调用
        result1 = expensive_function(5)
        self.assertEqual(result1, 10)
        self.assertEqual(call_count, 1)

        # 第二次调用（从缓存获取）
        result2 = expensive_function(5)
        self.assertEqual(result2, 10)

        # 如果 cachetools 可用，调用次数应该不变；否则会增加
        if cache_available:
            self.assertEqual(call_count, 1)  # 调用次数未增加
        else:
            self.assertEqual(call_count, 2)  # 无缓存，调用次数增加

        # 不同参数
        result3 = expensive_function(10)
        self.assertEqual(result3, 20)

        # 如果 cachetools 可用，调用次数应该是2；否则是3
        if cache_available:
            self.assertEqual(call_count, 2)  # 新参数需要调用
        else:
            self.assertEqual(call_count, 3)  # 无缓存，每次都调用


class TestConcurrentFetch(unittest.TestCase):
    """测试并发数据获取"""

    @patch('utils.kline_service.KlineServiceClient.get_latest_klines')
    def test_fetch_concurrent(self, mock_get_klines):
        """测试并发获取数据"""
        # Mock K线服务响应
        mock_get_klines.return_value = [
            {
                'close_price': 50000.0,
                'high_price': 51000.0,
                'low_price': 49000.0,
                'open_price': 49500.0,
                'volume': 1000.0
            }
        ]

        from core.data.fetcher import MarketDataFetcher

        # 创建数据获取器（启用并发）
        fetcher = MarketDataFetcher(
            cache_duration_hours=1,
            max_workers=3,
            enable_concurrent=True
        )

        # 获取数据
        symbols = ['BTCUSDT', 'ETHUSDT', 'BNBUSDT']
        data = fetcher.fetch_market_data(symbols)

        # 验证结果
        self.assertIsNotNone(data)
        # 注意：由于Mock的原因，实际数据可能不完整，这里主要测试并发逻辑不报错

    @patch('utils.kline_service.KlineServiceClient.get_latest_klines')
    def test_fetch_serial(self, mock_get_klines):
        """测试串行获取数据"""
        # Mock K线服务响应
        mock_get_klines.return_value = [
            {
                'close_price': 50000.0,
                'high_price': 51000.0,
                'low_price': 49000.0,
                'open_price': 49500.0,
                'volume': 1000.0
            }
        ]

        from core.data.fetcher import MarketDataFetcher

        # 创建数据获取器（禁用并发）
        fetcher = MarketDataFetcher(
            cache_duration_hours=1,
            max_workers=1,
            enable_concurrent=False
        )

        # 获取数据
        symbols = ['BTCUSDT']
        data = fetcher.fetch_market_data(symbols)

        # 验证结果
        self.assertIsNotNone(data)

    def test_performance_stats(self):
        """测试性能统计"""
        from core.data.fetcher import MarketDataFetcher

        fetcher = MarketDataFetcher(enable_concurrent=True)

        # 获取性能统计
        stats = fetcher.get_performance_stats()

        self.assertIsNotNone(stats)
        self.assertEqual(stats['concurrent_enabled'], True)
        self.assertEqual(stats['max_workers'], 5)
        self.assertEqual(stats['fetch_count'], 0)


class TestCacheIntegration(unittest.TestCase):
    """测试缓存集成"""

    def test_cache_with_fetcher(self):
        """测试缓存与数据获取器的集成"""
        from core.data.fetcher import MarketDataFetcher

        fetcher = MarketDataFetcher(
            cache_duration_hours=1,
            enable_concurrent=True
        )

        # 获取缓存统计
        cache_stats = fetcher.get_cache_stats()

        self.assertIsNotNone(cache_stats)
        self.assertEqual(cache_stats['max_size'], 100)
        self.assertEqual(cache_stats['ttl_seconds'], 3600)

    def test_cache_clear(self):
        """测试清除缓存"""
        from core.data.fetcher import MarketDataFetcher

        fetcher = MarketDataFetcher()
        fetcher.cache.set('BTCUSDT', {'price': 50000})

        # 清除缓存
        fetcher.clear_cache()

        # 验证缓存已清除
        self.assertTrue(fetcher.cache.is_empty())


if __name__ == '__main__':
    unittest.main()
