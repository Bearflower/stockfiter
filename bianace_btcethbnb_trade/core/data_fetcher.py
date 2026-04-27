#!/usr/bin/env python3
"""
数据获取模块兼容性导入

为了保持向后兼容，从 core/data/fetcher.py 重新导出所需的类和函数
"""

from .data.fetcher import MarketDataFetcher, get_data_fetcher

__all__ = ['MarketDataFetcher', 'get_data_fetcher']
