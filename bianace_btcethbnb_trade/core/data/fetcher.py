#!/usr/bin/env python3
"""
行情数据获取模块（增强版）

功能：
1. 从通用K线服务获取行情数据
2. 支持多时间框架（日线、4小时、1小时、15分钟）
3. 数据格式处理和转换
4. 缓存集成
5. 并发数据获取（新增）

数据流：
通用K线服务 → 数据获取（并发） → 指标计算 → 缓存 → 提供给信号检测模块

版本: v2.0.0 (增强版)
更新时间: 2026-04-27
"""

import logging
import traceback
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Dict, Any, List, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
import time
import pandas as pd

from .cache import DataCache
from .indicators import IndicatorCalculator
from utils.kline_service import KlineServiceClient

logger = logging.getLogger(__name__)


class MarketDataFetcher:
    """
    行情数据获取类（增强版）

    负责从K线服务获取数据，处理数据格式，计算技术指标，并管理缓存。
    支持并发获取多个交易对的数据，提高性能。
    """

    def __init__(
        self,
        cache_duration_hours: int = 1,
        max_workers: int = 5,
        enable_concurrent: bool = True
    ):
        """
        初始化数据获取器

        Args:
            cache_duration_hours: 缓存有效期（小时），默认1小时
            max_workers: 并发线程池最大线程数，默认5
            enable_concurrent: 是否启用并发获取，默认True
        """
        self.cache = DataCache(
            maxsize=100,
            ttl_seconds=cache_duration_hours * 3600,
            enable_stats=True
        )
        self.max_workers = max_workers
        self.enable_concurrent = enable_concurrent
        
        # 初始化 K 线服务客户端
        self.kline_client = KlineServiceClient()

        # 性能统计
        self._fetch_count = 0
        self._total_fetch_time = 0.0

        logger.info(
            f"数据获取器初始化完成：缓存时长={cache_duration_hours}小时, "
            f"并发线程数={max_workers}, 并发模式={enable_concurrent}"
        )

    def fetch_market_data(self, symbols: List[str] = None) -> Dict[str, Dict[str, Any]]:
        """
        获取市场行情数据

        Args:
            symbols: 交易对列表，默认 ['BTCUSDT', 'ETHUSDT', 'BNBUSDT']

        Returns:
            行情数据字典 {symbol: data}
        """
        if symbols is None:
            symbols = ['BTCUSDT', 'ETHUSDT', 'BNBUSDT']

        # 检查缓存是否有效
        if self.cache.is_valid(symbols):
            logger.info("使用缓存的行情数据")
            return self.cache.get_all()

        logger.info(f"从通用K线服务获取行情数据：{symbols}")

        # 记录开始时间
        start_time = time.time()

        try:
            # 根据配置选择并发或串行获取
            if self.enable_concurrent and len(symbols) > 1:
                api_data = self._fetch_from_kline_service_concurrent(symbols)
            else:
                api_data = self._fetch_from_kline_service(symbols)

            # 处理数据并计算技术指标
            processed_data = self._process_api_data(api_data)

            # 更新缓存
            self.cache.set_all(processed_data)

            # 记录性能统计
            fetch_time = time.time() - start_time
            self._fetch_count += 1
            self._total_fetch_time += fetch_time

            logger.info(
                f"成功获取 {len(processed_data)} 个交易对的行情数据，"
                f"耗时 {fetch_time:.2f} 秒"
            )

            return processed_data

        except Exception as e:
            logger.error(f"获取行情数据失败：{str(e)}")
            # 如果缓存存在，返回旧缓存
            if self.cache.get_all():
                logger.warning("使用旧的缓存数据")
                return self.cache.get_all()
            raise

    def _fetch_from_kline_service(self, symbols: List[str]) -> Dict[str, Any]:
        """
        从通用K线服务获取数据（串行方式）

        Args:
            symbols: 交易对列表

        Returns:
            API数据字典
        """
        result = {}
        for symbol in symbols:
            try:
                # 获取多个时间框架的K线数据
                klines_data = {}
                for interval in ['1d', '4h', '1h', '15m']:
                    limit = 100 if interval in ['1d', '4h'] else 100
                    klines = self._get_klines_from_service(symbol, interval, limit=limit)
                    if klines:
                        klines_data[interval] = klines

                if klines_data:
                    # 获取最新价格和涨跌幅
                    latest_1h = klines_data.get('1h', [])
                    latest_1d = klines_data.get('1d', [])

                    # 从1h K线获取最新价格
                    last_price = latest_1h[-1].get('close_price', 0) if latest_1h else 0

                    # 计算24小时涨跌幅
                    price_change_percent = 0.0
                    if latest_1d and len(latest_1d) > 0:
                        price_change_percent = latest_1d[-1].get('price_change_percent', 0.0)
                    elif latest_1h and len(latest_1h) >= 24:
                        open_24h = latest_1h[0].get('open_price', last_price)
                        price_change_percent = ((last_price - open_24h) / open_24h * 100) if open_24h > 0 else 0.0

                    result[symbol] = {
                        'klines': klines_data,
                        'symbol': symbol,
                        'lastPrice': str(last_price),
                        'priceChangePercent': str(round(price_change_percent, 2)),
                        'funding_rate': '0'
                    }
                else:
                    logger.warning(f"无法获取 {symbol} 的K线数据")
            except Exception as e:
                logger.error(f"获取 {symbol} 数据失败：{e}")

        return result

    def _fetch_from_kline_service_concurrent(self, symbols: List[str]) -> Dict[str, Any]:
        """
        从通用K线服务获取数据（并发方式）

        Args:
            symbols: 交易对列表

        Returns:
            API数据字典
        """
        result = {}

        def fetch_symbol_data(symbol: str) -> tuple:
            """获取单个交易对的数据"""
            try:
                # 获取多个时间框架的K线数据
                klines_data = {}
                for interval in ['1d', '4h', '1h', '15m']:
                    limit = 100 if interval in ['1d', '4h'] else 100
                    klines = self._get_klines_from_service(symbol, interval, limit=limit)
                    if klines:
                        klines_data[interval] = klines

                if klines_data:
                    # 获取最新价格和涨跌幅
                    latest_1h = klines_data.get('1h', [])
                    latest_1d = klines_data.get('1d', [])

                    # 从1h K线获取最新价格
                    last_price = latest_1h[-1].get('close_price', 0) if latest_1h else 0

                    # 计算24小时涨跌幅
                    price_change_percent = 0.0
                    if latest_1d and len(latest_1d) > 0:
                        price_change_percent = latest_1d[-1].get('price_change_percent', 0.0)
                    elif latest_1h and len(latest_1h) >= 24:
                        open_24h = latest_1h[0].get('open_price', last_price)
                        price_change_percent = ((last_price - open_24h) / open_24h * 100) if open_24h > 0 else 0.0

                    return (symbol, {
                        'klines': klines_data,
                        'symbol': symbol,
                        'lastPrice': str(last_price),
                        'priceChangePercent': str(round(price_change_percent, 2)),
                        'funding_rate': '0'
                    })
                else:
                    logger.warning(f"无法获取 {symbol} 的K线数据")
                    return (symbol, None)
            except Exception as e:
                logger.error(f"获取 {symbol} 数据失败：{e}")
                return (symbol, None)

        # 使用线程池并发获取
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            # 提交所有任务
            future_to_symbol = {
                executor.submit(fetch_symbol_data, symbol): symbol
                for symbol in symbols
            }

            # 收集结果
            for future in as_completed(future_to_symbol):
                symbol = future_to_symbol[future]
                try:
                    symbol_key, data = future.result(timeout=30)
                    if data:
                        result[symbol_key] = data
                except Exception as e:
                    logger.error(f"处理 {symbol} 结果失败：{e}")

        return result

    def _get_klines_from_service(self, symbol: str, interval: str, limit: int = 100) -> Optional[List]:
        """
        从通用K线服务获取K线数据

        Args:
            symbol: 交易对
            interval: 时间间隔
            limit: 获取数量

        Returns:
            K线数据列表
        """
        try:
            # 使用 KlineServiceClient 获取数据
            klines = self.kline_client.get_latest_klines(symbol, interval, limit)
            
            if klines:
                logger.info(f"成功获取 {symbol} {interval} K线数据 {len(klines)} 条")
                return klines
            else:
                logger.warning(f"未获取到 {symbol} {interval} K线数据")
                return []
                
        except Exception as e:
            logger.error(f"获取K线数据异常：{e}")
            return None

    def _process_api_data(self, api_data: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
        """
        处理API数据并计算技术指标

        Args:
            api_data: 原始API数据

        Returns:
            处理后的数据
        """
        logger.info(f"开始处理API数据，包含 {len(api_data)} 个交易对")
        processed = {}

        for symbol, data in api_data.items():
            logger.info(f"========== 开始处理 {symbol} ==========")
            logger.info(f"data类型：{type(data)}")
            logger.info(f"data内容：{data}")

            try:
                # 检查是否有indicators字段（新的API格式）
                if 'indicators' in data:
                    logger.info(f"{symbol} 使用indicators格式")
                    indicators_data = data['indicators']
                    indicators = {}

                    for timeframe in ['1d', '4h', '1h', '15m']:
                        if timeframe in indicators_data:
                            tf_data = indicators_data[timeframe]
                            # 转换为标准格式，只取最后一个值并转换为Decimal
                            prices = tf_data.get('prices', [])
                            logger.info(f"{symbol} {timeframe} prices长度：{len(prices) if prices else 0}")

                            if not prices or len(prices) == 0:
                                logger.warning(f"{symbol} {timeframe} prices为空，跳过")
                                indicators[timeframe] = {}
                                continue

                            try:
                                indicators[timeframe] = {
                                    'close': Decimal(str(prices[-1])),
                                    'close_list': [Decimal(str(p)) for p in prices],
                                    'ema21': Decimal(str(tf_data.get('ema21', [])[-1])) if tf_data.get('ema21') else None,
                                    'ema21_list': [Decimal(str(e)) for e in tf_data.get('ema21', [])],
                                    'rsi': Decimal(str(tf_data.get('rsi', [])[-1])) if tf_data.get('rsi') else None,
                                    'rsi_list': [Decimal(str(r)) for r in tf_data.get('rsi', [])],
                                    'atr14': Decimal(str(tf_data.get('atr14', [])[-1])) if tf_data.get('atr14') else None,
                                    'atr14_list': [Decimal(str(a)) for a in tf_data.get('atr14', [])],
                                }
                                # 添加布林带数据
                                if 'bollinger' in tf_data:
                                    indicators[timeframe]['bollinger'] = tf_data['bollinger']
                                else:
                                    # 如果K线服务没有返回布林带，本地计算
                                    closes = pd.Series([float(p) for p in prices])
                                    if len(closes) >= 20:
                                        bb_middle = closes.rolling(window=20).mean()
                                        bb_std = closes.rolling(window=20).std()
                                        bb_upper = bb_middle + 2 * bb_std
                                        bb_lower = bb_middle - 2 * bb_std
                                        bb_middle = bb_middle.ffill().bfill()
                                        bb_upper = bb_upper.ffill().bfill()
                                        bb_lower = bb_lower.ffill().bfill()
                                        indicators[timeframe]['bollinger'] = {
                                            'upper': [float(u) for u in bb_upper],
                                            'middle': [float(m) for m in bb_middle],
                                            'lower': [float(l) for l in bb_lower]
                                        }
                                        logger.info(f"{symbol} {timeframe} 布林带本地计算成功")
                                    else:
                                        indicators[timeframe]['bollinger'] = {'upper': [], 'middle': [], 'lower': []}
                                        logger.warning(f"{symbol} {timeframe} 数据不足，无法计算布林带")

                                # 添加volume数据
                                volumes = tf_data.get('volumes', [])
                                if volumes and len(volumes) > 0:
                                    indicators[timeframe]['volume'] = [float(v) for v in volumes]
                                    logger.info(f"{symbol} {timeframe} Volume数据添加成功，长度={len(indicators[timeframe]['volume'])}")
                                else:
                                    indicators[timeframe]['volume'] = []
                                    logger.warning(f"{symbol} {timeframe} Volume数据为空")

                                logger.info(f"{symbol} {timeframe} 指标计算成功")
                            except Exception as e:
                                logger.error(f"{symbol} {timeframe} 指标计算失败：{e}")
                                indicators[timeframe] = {}
                else:
                    logger.info(f"{symbol} 使用klines格式")
                    # 新的API格式，klines直接是列表
                    # data结构：{'klines': {'1d': [...], '4h': [...], ...}, 'symbol': 'BTCUSDT'}
                    klines_data = data.get('klines', {})
                    logger.info(f"{symbol} klines_data类型：{type(klines_data)}")
                    logger.info(f"{symbol} klines_data包含的时间框架：{list(klines_data.keys()) if klines_data else '空'}")

                    indicators = {}

                    for timeframe in ['1d', '4h', '1h', '15m']:
                        if timeframe not in klines_data:
                            logger.warning(f"{symbol} {timeframe} 不在klines_data中")
                            indicators[timeframe] = {}
                            continue

                        kline_list = klines_data[timeframe]
                        logger.info(f"{symbol} {timeframe} kline_list类型：{type(kline_list)}")
                        logger.info(f"{symbol} {timeframe} kline_list长度：{len(kline_list) if kline_list else 0}")

                        if not kline_list or len(kline_list) == 0:
                            logger.warning(f"{symbol} {timeframe} K线数据为空")
                            indicators[timeframe] = {}
                            continue

                        # K线服务返回的是字典列表，每个K线是字典格式
                        try:
                            logger.info(f"{symbol} {timeframe} 第一个K线元素：{kline_list[0]}")

                            # 检查数据类型
                            if not isinstance(kline_list[0], dict):
                                logger.error(f"{symbol} {timeframe} K线元素不是字典类型：{type(kline_list[0])}")
                                indicators[timeframe] = {}
                                continue

                            # 检查必需字段
                            required_fields = ['close_price', 'high_price', 'low_price', 'open_price', 'volume']
                            missing_fields = [f for f in required_fields if f not in kline_list[0]]
                            if missing_fields:
                                logger.error(f"{symbol} {timeframe} 缺少必需字段：{missing_fields}")
                                indicators[timeframe] = {}
                                continue

                            kline_dict = {
                                'close': [float(k.get('close_price', 0)) for k in kline_list],
                                'high': [float(k.get('high_price', 0)) for k in kline_list],
                                'low': [float(k.get('low_price', 0)) for k in kline_list],
                                'open': [float(k.get('open_price', 0)) for k in kline_list],
                                'volume': [float(k.get('volume', 0)) for k in kline_list],
                            }

                            logger.info(f"{symbol} {timeframe} kline_dict转换成功，close长度：{len(kline_dict['close'])}")

                            indicators[timeframe] = IndicatorCalculator.calculate_timeframe_indicators(
                                kline_dict,
                                timeframe
                            )
                            logger.info(f"{symbol} {timeframe} 指标计算完成")

                        except Exception as e:
                            error_details = traceback.format_exc()
                            logger.error(f"{symbol} {timeframe} K线数据处理失败：{e}")
                            logger.error(f"详细错误：{error_details}")
                            logger.error(f"kline_list类型：{type(kline_list)}")
                            logger.error(f"kline_list长度：{len(kline_list)}")
                            if len(kline_list) > 0:
                                logger.error(f"第一个元素类型：{type(kline_list[0])}")
                                logger.error(f"第一个元素内容：{kline_list[0]}")
                            indicators[timeframe] = {}

                # 组装处理后的数据
                logger.info(f"{symbol} 开始组装最终数据")

                # 安全获取lastPrice
                last_price_val = data.get('lastPrice', '0')
                if last_price_val is None:
                    logger.warning(f"{symbol} lastPrice为None，使用默认值'0'")
                    last_price_val = '0'
                last_price = Decimal(str(last_price_val))

                # 安全获取priceChangePercent
                price_change_val = data.get('priceChangePercent', '0')
                if price_change_val is None:
                    logger.warning(f"{symbol} priceChangePercent为None，使用默认值'0'")
                    price_change_val = '0'
                try:
                    price_change_24h = Decimal(str(price_change_val)) / Decimal('100')
                except Exception as e:
                    logger.error(f"{symbol} price_change_24h计算失败：{e}, priceChangePercent={price_change_val}")
                    price_change_24h = Decimal('0')

                # 安全获取funding_rate
                funding_rate_val = data.get('funding_rate', '0')
                if funding_rate_val is None:
                    logger.warning(f"{symbol} funding_rate为None，使用默认值'0'")
                    funding_rate_val = '0'
                funding_rate = Decimal(str(funding_rate_val))

                processed[symbol] = {
                    'symbol': symbol,
                    'last_price': last_price,
                    'price_change_24h': price_change_24h,
                    'funding_rate': funding_rate,
                    'indicators': indicators,
                    'timestamp': datetime.now(),
                }

                logger.info(f"{symbol} 处理成功！last_price={last_price}, indicators时间框架数：{len(indicators)}")
                logger.info(f"========== {symbol} 处理完成 ==========\n")

            except Exception as e:
                error_details = traceback.format_exc()
                logger.error(f"[处理失败] {symbol} 处理失败：{str(e)}")
                logger.error(f"详细错误：{error_details}")
                logger.error(f"data类型：{type(data)}")
                logger.error(f"data内容：{data}")
                logger.error(f"========== {symbol} 处理失败 ==========\n")
                continue

        logger.info(f"所有交易对处理完成，成功：{len(processed)}, 失败：{len(api_data) - len(processed)}")
        return processed

    def get_symbol_data(self, symbol: str) -> Optional[Dict[str, Any]]:
        """
        获取单个交易对的行情数据

        Args:
            symbol: 交易对

        Returns:
            行情数据，如果不存在则返回None
        """
        if not self.cache.has_symbol(symbol):
            self.fetch_market_data([symbol])

        return self.cache.get(symbol)

    def clear_cache(self):
        """清除缓存"""
        self.cache.clear()
        logger.info("行情数据缓存已清除")

    def get_cache_stats(self) -> Optional[Dict[str, Any]]:
        """
        获取缓存统计信息

        Returns:
            缓存统计信息字典
        """
        return self.cache.get_stats()

    def get_performance_stats(self) -> Dict[str, Any]:
        """
        获取性能统计信息

        Returns:
            性能统计信息字典
        """
        avg_fetch_time = (
            self._total_fetch_time / self._fetch_count
            if self._fetch_count > 0
            else 0.0
        )

        return {
            'fetch_count': self._fetch_count,
            'total_fetch_time': round(self._total_fetch_time, 2),
            'avg_fetch_time': round(avg_fetch_time, 2),
            'concurrent_enabled': self.enable_concurrent,
            'max_workers': self.max_workers
        }


# 全局实例
_global_fetcher: Optional[MarketDataFetcher] = None


def get_data_fetcher(
    cache_duration_hours: int = 1,
    max_workers: int = 5,
    enable_concurrent: bool = True
) -> MarketDataFetcher:
    """
    获取数据获取器实例（单例模式）

    Args:
        cache_duration_hours: 缓存有效期（小时）
        max_workers: 并发线程池最大线程数
        enable_concurrent: 是否启用并发获取

    Returns:
        数据获取器实例
    """
    global _global_fetcher
    if _global_fetcher is None:
        _global_fetcher = MarketDataFetcher(
            cache_duration_hours=cache_duration_hours,
            max_workers=max_workers,
            enable_concurrent=enable_concurrent
        )
    return _global_fetcher
