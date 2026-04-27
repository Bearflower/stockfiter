#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
生成沪深两市主板股票列表
排除：创业板、科创板、北交所、ST 股票、次新股、新三板
"""

from data.database import DatabaseManager
import pandas as pd
from datetime import datetime

db = DatabaseManager()

# 获取所有股票
df = db.get_stock_list()
print(f"原始股票总数：{len(df)} 只")

# 1. 过滤代码规则
# 沪深两市主板：000xxx, 001xxx, 002xxx (深市), 600xxx, 601xxx, 603xxx, 605xxx (沪市)
# 排除创业板：300xxx, 301xxx
# 排除科创板：688xxx, 689xxx
# 排除北交所：920xxx, 83xxxx, 87xxxx
# 排除新三板：43xxxx, 83xxxx

def is_main_board(code):
    """判断是否为主板股票"""
    # 深市主板
    if code.startswith(('000', '001', '002')):
        return True
    # 沪市主板
    if code.startswith(('600', '601', '603', '605')):
        return True
    return False

# 2. 过滤 ST 股票
def is_st(name):
    """判断是否为 ST 股票"""
    if 'ST' in str(name).upper():
        return True
    return False

# 3. 过滤次新股（2025 年后上市）
def is_new_stock(list_date):
    """判断是否为次新股"""
    if pd.isna(list_date):
        return False
    try:
        if isinstance(list_date, str):
            list_date = datetime.strptime(list_date, '%Y-%m-%d')
        # 2025 年 1 月 1 日后上市的算次新股
        cutoff_date = datetime(2025, 1, 1)
        return list_date >= cutoff_date
    except:
        return False

# 应用过滤
filtered_df = df.copy()

# 确保 code 列是字符串类型
filtered_df['code'] = filtered_df['code'].astype(str)

# 过滤代码
filtered_df = filtered_df[filtered_df['code'].apply(is_main_board)]
print(f"主板股票（代码过滤后）：{len(filtered_df)} 只")

# 过滤 ST
filtered_df = filtered_df[~filtered_df['name'].apply(is_st)]
print(f"非 ST 股票：{len(filtered_df)} 只")

# 过滤次新股
filtered_df = filtered_df[~filtered_df['list_date'].apply(is_new_stock)]
print(f"非次新股：{len(filtered_df)} 只")

# 保存结果
filtered_df.to_csv('/app/main_board_stocks.csv', index=False, encoding='utf-8-sig')
print(f"\n✅ 沪深主板股票列表已保存：/app/main_board_stocks.csv")
print(f"最终股票数量：{len(filtered_df)} 只")

# 显示前 20 只
print("\n前 20 只股票:")
print(filtered_df.head(20).to_string())

# 统计分布
print("\n深市主板 (000/001/002):", len(filtered_df[filtered_df['code'].str.startswith(('000', '001', '002'))]))
print("沪市主板 (600/601/603/605):", len(filtered_df[filtered_df['code'].str.startswith(('600', '601', '603', '605'))]))

db.close()
