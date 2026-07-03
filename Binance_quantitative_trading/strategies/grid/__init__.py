"""
网格交易策略
在设定的价格区间内，按照固定的价格间隔挂单买卖的策略
"""

__version__ = "1.0.0"
__strategy_name__ = "grid_trading"

from .strategy import GridStrategy
from .main import main

__all__ = ["GridStrategy", "main"]
