#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
生成 V2.2 回测年度分布可视化图表
"""

import json
from collections import defaultdict
import matplotlib.pyplot as plt
import matplotlib
matplotlib.use('Agg')  # 不显示图形界面

# 设置中文字体
plt.rcParams['font.sans-serif'] = ['Arial Unicode MS', 'SimHei', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False

with open('backtest_results/backtest_v22_20260413_223608.json', 'r') as f:
    data = json.load(f)

# 统计年份分布
year_stats = defaultdict(int)
for pattern in data['patterns']:
    year = pattern['retrace_date'][:4]
    year_stats[year] += 1

# 统计年份收益
year_returns = defaultdict(list)
for trade in data['trades']:
    year = trade['buy_date'][:4]
    year_returns[year].append(trade['net_return'])

# 创建图表
fig, axes = plt.subplots(2, 1, figsize=(14, 10))

# 图表 1: 形态数量按年份分布
years = sorted(year_stats.keys())
counts = [year_stats[y] for y in years]

colors = plt.cm.Blues([0.3 + 0.7 * (i / len(years)) for i in range(len(years))])
bars = axes[0].bar(years, counts, color=colors, edgecolor='navy', linewidth=1.5)

axes[0].set_title('V2.2 回测 - 形态数量按年份分布 (总计：478 个)', fontsize=14, fontweight='bold')
axes[0].set_xlabel('年份', fontsize=12)
axes[0].set_ylabel('形态数量', fontsize=12)
axes[0].grid(axis='y', alpha=0.3, linestyle='--')

# 在柱子上标注数值
for bar, count in zip(bars, counts):
    height = bar.get_height()
    axes[0].text(bar.get_x() + bar.get_width()/2., height,
                f'{count}',
                ha='center', va='bottom', fontsize=11, fontweight='bold')

# 图表 2: 平均收益按年份分布
year_avg_returns = {}
for year in years:
    if year in year_returns:
        returns = year_returns[year]
        avg_return = sum(returns) / len(returns) * 100
        year_avg_returns[year] = avg_return

years_returns = sorted(year_avg_returns.keys())
avg_returns = [year_avg_returns[y] for y in years_returns]

colors2 = ['green' if r > 0 else 'red' for r in avg_returns]
bars2 = axes[1].bar(years_returns, avg_returns, color=colors2, edgecolor='black', linewidth=1.2)

axes[1].set_title('V2.2 回测 - 平均收益按年份分布', fontsize=14, fontweight='bold')
axes[1].set_xlabel('年份', fontsize=12)
axes[1].set_ylabel('平均收益 (%)', fontsize=12)
axes[1].axhline(y=0, color='black', linestyle='-', linewidth=0.8)
axes[1].grid(axis='y', alpha=0.3, linestyle='--')

# 在柱子上标注数值
for bar, ret in zip(bars2, avg_returns):
    height = bar.get_height()
    axes[1].text(bar.get_x() + bar.get_width()/2., height,
                f'{ret:.2f}%',
                ha='center', va='bottom' if height > 0 else 'top',
                fontsize=11, fontweight='bold')

plt.tight_layout()
output_file = 'backtest_results/v22_yearly_distribution.png'
plt.savefig(output_file, dpi=150, bbox_inches='tight')
print(f"图表已保存到：{output_file}")

# 输出详细统计表
print("\n" + "="*80)
print("V2.2 回测 - 年度详细统计表")
print("="*80)

print(f"\n{'年份':<8} {'形态数':>8} {'交易数':>8} {'平均收益':>10} {'胜率':>10} {'最高收益':>10} {'最低收益':>10}")
print("-"*80)

for year in sorted(years):
    count = year_stats[year]
    if year in year_returns:
        returns = year_returns[year]
        num_trades = len(returns)
        avg_return = sum(returns) / len(returns) * 100
        win_count = sum(1 for r in returns if r > 0)
        win_rate = win_count / len(returns) * 100
        max_return = max(returns) * 100
        min_return = min(returns) * 100
    else:
        num_trades = 0
        avg_return = 0
        win_rate = 0
        max_return = 0
        min_return = 0
    
    print(f"{year:<8} {count:>8} {num_trades:>8} {avg_return:>9.2f}% {win_rate:>9.2f}% {max_return:>9.2f}% {min_return:>9.2f}%")

print("-"*80)
total_count = sum(year_stats.values())
total_trades = sum(len(year_returns[y]) for y in year_returns)
print(f"{'总计':<8} {total_count:>8} {total_trades:>8}")
