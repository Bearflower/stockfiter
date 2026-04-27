#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
公平对比 V2.1 和 V2.2（都使用 baostocks_full 数据）
"""

import json
from collections import defaultdict

# 加载 V2.1 结果
with open('backtest_results/backtest_v21_baostocks_20260413_225540.json', 'r') as f:
    v21_data = json.load(f)

# 加载 V2.2 结果
with open('backtest_results/backtest_v22_20260413_223608.json', 'r') as f:
    v22_data = json.load(f)

print("="*80)
print("V2.1 vs V2.2 公平对比（均使用 baostocks_full 完整数据）")
print("="*80)

# 提取统计数据
v21_summary = v21_data['summary']
v22_summary = v22_data['summary']

print(f"\n{'指标':<25} {'V2.1':>15} {'V2.2':>15} {'差异':>15} {'增长率':>15}")
print("-"*80)

metrics = [
    ('检测股票数', 'total_stocks'),
    ('满足形态股票数', 'matched_stocks'),
    ('总形态数', 'total_patterns'),
    ('总交易数', 'total_trades'),
]

for name, key in metrics:
    v21_val = v21_summary[key]
    v22_val = v22_summary[key]
    diff = v22_val - v21_val
    growth = (v22_val - v21_val) / v21_val * 100 if v21_val > 0 else 0
    print(f"{name:<25} {v21_val:>15} {v22_val:>15} {diff:>+15} {growth:>+14.1f}%")

# 收益指标
print("\n收益指标：")
print("-"*80)

return_metrics = [
    ('平均收益 (%)', 'avg_return'),
    ('胜率 (%)', 'win_rate'),
    ('最高收益 (%)', 'max_return'),
    ('最低收益 (%)', 'min_return'),
]

for name, key in return_metrics:
    v21_val = v21_summary[key]
    v22_val = v22_summary[key]
    diff = v22_val - v21_val
    growth = (v22_val - v21_val) / abs(v21_val) * 100 if v21_val != 0 else 0
    print(f"{name:<25} {v21_val:>14.2f} {v22_val:>14.2f} {diff:>+15.2f} {growth:>+14.1f}%")

# 按年份统计
print("\n" + "="*80)
print("按年份分布对比")
print("="*80)

# V2.1 年份统计
v21_yearly = defaultdict(int)
for p in v21_data['patterns']:
    year = p['retrace_date'][:4]
    v21_yearly[year] += 1

# V2.2 年份统计
v22_yearly = defaultdict(int)
for p in v22_data['patterns']:
    year = p['retrace_date'][:4]
    v22_yearly[year] += 1

all_years = sorted(set(list(v21_yearly.keys()) + list(v22_yearly.keys())))

print(f"\n{'年份':<10} {'V2.1':>10} {'V2.2':>10} {'差异':>10} {'增长率':>15}")
print("-"*80)

for year in all_years:
    v21_count = v21_yearly.get(year, 0)
    v22_count = v22_yearly.get(year, 0)
    diff = v22_count - v21_count
    growth = (v22_count - v21_count) / v21_count * 100 if v21_count > 0 else 0
    print(f"{year:<10} {v21_count:>10} {v22_count:>10} {diff:>+10} {growth:>+14.1f}%")

# 603529 检测情况
print("\n" + "="*80)
print("603529 爱玛科技检测情况")
print("="*80)

v21_603529 = [p for p in v21_data['patterns'] if p['code'] == '603529']
v22_603529 = [p for p in v22_data['patterns'] if p['code'] == '603529']

print(f"\nV2.1: {'✅ 检测到' if v21_603529 else '❌ 未检测到'}")
if v21_603529:
    for p in v21_603529:
        print(f"  - {p['retrace_date']}: {p['surge_date']} 放量")

print(f"V2.2: {'✅ 检测到' if v22_603529 else '❌ 未检测到'}")
if v22_603529:
    for p in v22_603529:
        print(f"  - {p['retrace_date']}: {p['surge_date']} 放量")

# 核心差异总结
print("\n" + "="*80)
print("核心差异总结")
print("="*80)

print(f"""
时间窗口设置：
  V2.1: 缩量→放量 = 10 天
  V2.2: 缩量→放量 = 25 天 (放宽 150%)

检测效果：
  V2.1 检测到 {v21_summary['total_patterns']} 个形态
  V2.2 检测到 {v22_summary['total_patterns']} 个形态
  V2.2 多检测到 {v22_summary['total_patterns'] - v21_summary['total_patterns']} 个形态 (增长 {(v22_summary['total_patterns']-v21_summary['total_patterns'])/v21_summary['total_patterns']*100:.1f}%)

收益表现：
  V2.1 平均收益：{v21_summary['avg_return']:.2f}%
  V2.2 平均收益：{v22_summary['avg_return']:.2f}%
  V2.2 提升：{v22_summary['avg_return'] - v21_summary['avg_return']:.2f}个百分点

  V2.1 胜率：{v21_summary['win_rate']:.2f}%
  V2.2 胜率：{v22_summary['win_rate']:.2f}%
  V2.2 提升：{v22_summary['win_rate'] - v21_summary['win_rate']:.2f}个百分点

覆盖股票：
  V2.1: {v21_summary['matched_stocks']}只股票有形态
  V2.2: {v22_summary['matched_stocks']}只股票有形态
  V2.2 多覆盖：{v22_summary['matched_stocks'] - v21_summary['matched_stocks']}只股票

603529 检测：
  V2.1: {'✅ 检测到' if v21_603529 else '❌ 未检测到'}
  V2.2: {'✅ 检测到' if v22_603529 else '❌ 未检测到'}
  原因：603529 的缩量→放量时间跨度约 160 天，远超 25 天窗口
""")

# 保存对比结果
output = {
    'v21_summary': v21_summary,
    'v22_summary': v22_summary,
    'comparison': {
        'patterns_increase': v22_summary['total_patterns'] - v21_summary['total_patterns'],
        'patterns_growth_rate': (v22_summary['total_patterns'] - v21_summary['total_patterns']) / v21_summary['total_patterns'] * 100,
        'avg_return_improvement': v22_summary['avg_return'] - v21_summary['avg_return'],
        'win_rate_improvement': v22_summary['win_rate'] - v21_summary['win_rate'],
        'stocks_covered_increase': v22_summary['matched_stocks'] - v21_summary['matched_stocks']
    },
    'yearly_comparison': {
        'v21': dict(v21_yearly),
        'v22': dict(v22_yearly)
    },
    '603529_detection': {
        'v21': len(v21_603529),
        'v22': len(v22_603529)
    }
}

import json
with open('backtest_results/v21_vs_v22_fair_comparison.json', 'w', encoding='utf-8') as f:
    json.dump(output, f, ensure_ascii=False, indent=2)

print("\n对比结果已保存到：backtest_results/v21_vs_v22_fair_comparison.json")
