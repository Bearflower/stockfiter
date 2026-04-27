#!/usr/bin/env python3
"""
快速补全指定日期的 K 线数据
用途：补全缺失的历史数据（如昨天）
"""

import os
import sys
from datetime import datetime, timedelta
import pandas as pd

# 设置数据库连接
os.environ['DB_HOST'] = '10.3.0.12'
os.environ['DB_PORT'] = '5432'
os.environ['DB_NAME'] = 'stockfilter'
os.environ['DB_USER'] = 'stockfilter_user'
os.environ['DB_PASSWORD'] = 'Stock@2024'
os.environ['DB_SCHEMA'] = 'schema_stockfilter'

from data.database import DatabaseManager
from data.fetcher import get_stock_daily_kline

def fetch_missing_for_date(target_date: str, max_stocks: int = 1000):
    """
    获取指定日期缺失数据的股票
    
    Args:
        target_date: 目标日期 (YYYY-MM-DD)
        max_stocks: 最多处理多少只股票
    """
    print("=" * 80)
    print(f"补全 {target_date} 的 K 线数据")
    print("=" * 80)
    
    db = DatabaseManager()
    
    # 1. 获取主板股票列表
    query = """
        SELECT code, name, symbol FROM stocks 
        WHERE (code LIKE '600%' OR code LIKE '601%' OR code LIKE '603%' OR code LIKE '605%' 
               OR code LIKE '000%' OR code LIKE '001%' OR code LIKE '002%')
        AND code NOT IN (
            SELECT DISTINCT code FROM klines WHERE date = %s
        )
        LIMIT %s
    """
    
    missing_df = pd.read_sql_query(query, db.conn, params=(target_date, max_stocks))
    print(f"\n📊 {target_date} 缺失数据的主板股票数：{len(missing_df)} 只")
    
    if len(missing_df) == 0:
        print("✅ 所有股票数据已完整！")
        db.close()
        return
    
    # 2. 批量获取数据
    success_count = 0
    error_count = 0
    total = len(missing_df)
    
    print(f"\n开始获取数据...")
    
    for idx, row in missing_df.iterrows():
        code = row['code']
        symbol = row['symbol']
        name = row['name']
        
        try:
            # 获取单只股票数据 (包含目标日期在内的最近 20 天)
            target_dt = datetime.strptime(target_date, '%Y-%m-%d')
            df = get_stock_daily_kline(symbol, days=20)
            
            if df is not None and len(df) > 0:
                # 保存到数据库
                db.save_kline_history(code, df)
                success_count += 1
                
                # 检查是否包含目标日期
                target_exists = df[df['date'] == target_dt]
                if len(target_exists) > 0:
                    print(f"[{idx+1}/{total}] ✅ {code} - {name}: 成功获取 {len(df)} 天数据 (包含 {target_date})")
                else:
                    print(f"[{idx+1}/{total}] ⚠️  {code} - {name}: 获取 {len(df)} 天数据 (但缺少 {target_date})")
            else:
                error_count += 1
                print(f"[{idx+1}/{total}] ❌ {code} - {name}: 获取失败")
            
            # 每 50 只股票打印进度
            if (idx + 1) % 50 == 0:
                print(f"\n进度：{idx+1}/{total}, 成功：{success_count}, 失败：{error_count}\n")
                
        except Exception as e:
            error_count += 1
            print(f"[{idx+1}/{total}] ❌ {code} - {name}: 异常 {e}")
    
    print("\n" + "=" * 80)
    print("补全完成")
    print("=" * 80)
    print(f"总计：{total} 只")
    print(f"成功：{success_count} 只 ({success_count/total*100:.1f}%)")
    print(f"失败：{error_count} 只 ({error_count/total*100:.1f}%)")
    
    db.close()
    
    # 3. 验证结果
    print("\n验证结果...")
    db2 = DatabaseManager()
    query = """
        SELECT COUNT(DISTINCT code) FROM klines 
        WHERE date = %s AND code IN (
            SELECT code FROM stocks 
            WHERE code LIKE '600%' OR code LIKE '601%' OR code LIKE '603%' OR code LIKE '605%' 
                  OR code LIKE '000%' OR code LIKE '001%' OR code LIKE '002%'
        )
    """
    cur = db2.conn.cursor()
    cur.execute(query, (target_date,))
    count = cur.fetchone()[0]
    print(f"✅ {target_date} 现在有 {count} 只主板股票的数据")
    db2.close()

if __name__ == '__main__':
    # 默认补全昨天
    if len(sys.argv) > 1:
        target_date = sys.argv[1]
    else:
        # 获取昨天日期
        yesterday = datetime.now() - timedelta(days=1)
        # 如果是周一，获取上周五
        if yesterday.weekday() == 0:
            yesterday = yesterday - timedelta(days=2)
        target_date = yesterday.strftime('%Y-%m-%d')
    
    max_stocks = int(sys.argv[2]) if len(sys.argv) > 2 else 1000
    
    fetch_missing_for_date(target_date, max_stocks)
