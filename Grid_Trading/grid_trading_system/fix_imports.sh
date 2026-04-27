#!/bin/bash

# 修复 auto_mode 文件中的旧引用

cd /Users/yl/vscode/Grid_Trading/grid_trading_system/src/execution/auto_mode

# fund_manager.py
sed -i '' 's/from src\.core\.utils\.binance_trade_api import BinanceTradeAPI/from src.core.data.binance_client import BinanceClient/g' fund_manager.py
sed -i '' 's/BinanceTradeAPI/BinanceClient/g' fund_manager.py
sed -i '' 's/self\.api\./self.client./g' fund_manager.py

# 继续修复 grid_executor.py 中剩余的 self.api 引用
# 注意：我们使用更精确的模式
