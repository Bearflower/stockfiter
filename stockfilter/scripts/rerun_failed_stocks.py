#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
重新同步失败的股票和今天的数据
"""

from data.database import DatabaseManager
from data.fetcher import get_stock_daily_kline
from utils.logger import get_logger
import pandas as pd

logger = get_logger()

db = DatabaseManager()

print("=" * 80)
print("重新同步失败股票和今天数据")
print("=" * 80)

# 1. 读取固定股票列表
stocks_df = pd.read_csv('/app/main_board_stocks.csv', dtype={'code': str})
print(f"\n沪深主板股票总数：{len(stocks_df)} 只")

# 2. 找出无数据或数据不足的股票
print("\n查找需要重新同步的股票...")
failed_stocks = []

for idx, row in stocks_df.iterrows():
    code = row['code']
    symbol = row['symbol']
    
    # 检查已有数据
    existing = db.get_kline_history(code, days=250)
    if existing is None or len(existing) < 100:
        failed_stocks.append((code, symbol))

print(f"需要重新同步的股票：{len(failed_stocks)} 只")

# 3. 重新同步
success = 0
error = 0

for i, (code, symbol) in enumerate(failed_stocks):
    try:
        logger.info(f"[{i+1}/{len(failed_stocks)}] 同步 {code} ({symbol})...")
        
        # 获取 K 线数据
        kline_df = get_stock_daily_kline(symbol=symbol, days=250)
        
        if kline_df is not None and len(kline_df) > 0:
            # 保存到数据库
            db.save_kline_history(code, kline_df)
            success += 1
            logger.info(f"✅ {code} 同步成功：{len(kline_df)} 条")
        else:
            error += 1
            logger.warning(f"❌ {code} 同步失败：返回空数据")
            
    except Exception as e:
        error += 1
        logger.error(f"❌ {code} 同步失败：{e}")
    
    # 每 10 只暂停一下
    if (i + 1) % 10 == 0:
        import time
        time.sleep(0.5)

print("\n" + "=" * 80)
print(f"重跑完成")
print(f"重跑总数：{len(failed_stocks)} 只")
print(f"成功：{success} 只")
print(f"失败：{error} 只")
print(f"成功率：{success/len(failed_stocks)*100:.1f}%")
print("=" * 80)

db.close()
