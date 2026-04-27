#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
按年份统计 V2.2 回测形态分布
"""

import json
from collections import defaultdict

with open('backtest_results/backtest_v22_20260413_223608.json', 'r') as f:
    data = json.load(f)

print('='*80)
print('V2.2 回测 - 形态按年份统计')
print('='*80)

# 按回踩日期统计年份分布
year_stats = defaultdict(lambda: {'count': 0, 'codes': []})

for pattern in data['patterns']:
    # 提取回踩日期的年份
    retrace_date = pattern['retrace_date']
    year = retrace_date[:4]  # 提取年份，如 "2019"
    
    year_stats[year]['count'] += 1
    year_stats[year]['codes'].append(pattern['code'])

# 按年份排序
sorted_years = sorted(year_stats.keys())

print(f"\n总形态数：{data['summary']['total_patterns']}")
print(f"年份范围：{sorted_years[0]} 到 {sorted_years[-1]}")
print("\n年份分布：")
print('-'*80)

for year in sorted_years:
    count = year_stats[year]['count']
    percentage = (count / data['summary']['total_patterns']) * 100
    bar = '█' * int(percentage / 2)  # 用条形图表示
    print(f"{year}年：{count:4d} 个 ({percentage:5.2f}%) {bar}")

print('-'*80)
print(f"总计：{sum(y['count'] for y in year_stats.values()):4d} 个 (100.00%)")

# 显示每年的前 5 只股票
print("\n" + '='*80)
print("每年形态数量前 5 的股票代码：")
print('='*80)

for year in sorted_years:
    print(f"\n{year}年 ({year_stats[year]['count']}个形态):")
    
    # 统计该年份每只股票的形态数
    code_count = defaultdict(int)
    for pattern in data['patterns']:
        if pattern['retrace_date'][:4] == year:
            code_count[pattern['code']] += 1
    
    # 排序并显示前 5
    sorted_codes = sorted(code_count.items(), key=lambda x: x[1], reverse=True)[:5]
    for i, (code, count) in enumerate(sorted_codes, 1):
        print(f"  {i}. {code}: {count}次")

# 显示每年的收益情况
print("\n" + '='*80)
print("每年收益统计：")
print('='*80)

year_returns = defaultdict(list)
for trade in data['trades']:
    buy_date = trade['buy_date']
    year = buy_date[:4]
    year_returns[year].append(trade['net_return'])

for year in sorted(year_returns.keys()):
    returns = year_returns[year]
    avg_return = sum(returns) / len(returns) * 100
    win_count = sum(1 for r in returns if r > 0)
    win_rate = win_count / len(returns) * 100
    max_return = max(returns) * 100
    min_return = min(returns) * 100
    
    print(f"\n{year}年:")
    print(f"  交易数：{len(returns)}")
    print(f"  平均收益：{avg_return:.2f}%")
    print(f"  胜率：{win_rate:.2f}%")
    print(f"  最高收益：{max_return:.2f}%")
    print(f"  最低收益：{min_return:.2f}%")
