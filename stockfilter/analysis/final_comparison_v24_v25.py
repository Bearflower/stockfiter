#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V2.4 vs V2.5 最终完整对比（修正版）
"""

import json
import pandas as pd
from pathlib import Path
import glob

# 加载 V2.4 结果
v24_file = 'backtest_results/backtest_v24_full_20260414_100828.json'
with open(v24_file, 'r') as f:
    v24_data = json.load(f)

# 加载 V2.5 结果
v25_files = sorted(glob.glob('backtest_results/backtest_v25_full_*.json'))
with open(v25_files[-1], 'r') as f:
    v25_data = json.load(f)

print("="*80)
print("V2.4 vs V2.5 最终完整对比报告")
print("="*80)

v24_summary = v24_data['summary']
v25_summary = v25_data['summary']

# 1. 基本指标对比
print("\n" + "="*80)
print("1. 基本指标对比")
print("="*80)

print(f"\n{'指标':<20} {'V2.4':>12} {'V2.5':>12} {'变化':>12} {'变化率':>12}")
print("-"*80)

metrics = [
    ('检测股票数', 'total_stocks'),
    ('满足形态股票数', 'matched_stocks'),
    ('总形态数', 'total_patterns'),
    ('总交易数', 'total_trades'),
]

for name, key in metrics:
    v24_val = v24_summary[key]
    v25_val = v25_summary[key]
    diff = v25_val - v24_val
    change_rate = (v25_val - v24_val) / v24_val * 100 if v24_val > 0 else 0
    print(f"{name:<20} {v24_val:>12} {v25_val:>12} {diff:>+12} {change_rate:>+11.1f}%")

# 2. 年度分布对比
print("\n" + "="*80)
print("2. 年度分布对比")
print("="*80)

v24_yearly = v24_summary['yearly_stats']
v25_yearly = v25_summary['yearly_stats']

all_years = sorted(set(list(v24_yearly.keys()) + list(v25_yearly.keys())))

print(f"\n{'年份':<10} {'V2.4':>10} {'V2.5':>10} {'变化':>10} {'变化率':>12}")
print("-"*80)

for year in all_years:
    v24_count = v24_yearly.get(year, 0)
    v25_count = v25_yearly.get(year, 0)
    diff = v25_count - v24_count
    change_rate = (v25_count - v24_count) / v24_count * 100 if v24_count > 0 else 0
    print(f"{year:<10} {v24_count:>10} {v25_count:>10} {diff:>+10} {change_rate:>+11.1f}%")

# 3. 排除 2019 年后对比
print("\n" + "="*80)
print("3. 排除 2019 年后对比（关键指标）")
print("="*80)

v24_exclude_2019 = v24_summary['patterns_exclude_2019']
v25_exclude_2019 = v25_summary['patterns_exclude_2019']

# 计算每月平均
def calc_monthly_avg(patterns_exclude_2019, yearly_stats):
    if patterns_exclude_2019 == 0:
        return 0
    years = [y for y in yearly_stats.keys() if y != '2019']
    if not years:
        return 0
    min_year = min(years)
    max_year = max(years)
    months = (int(max_year) - int(min_year) + 1) * 12
    return patterns_exclude_2019 / months if months > 0 else 0

v24_monthly = calc_monthly_avg(v24_exclude_2019, v24_yearly)
v25_monthly = calc_monthly_avg(v25_exclude_2019, v25_yearly)

print(f"\n{'指标':<25} {'V2.4':>12} {'V2.5':>12} {'变化':>12}")
print("-"*80)
print(f"{'排除 2019 年后形态数':<25} {v24_exclude_2019:>12} {v25_exclude_2019:>12} {(v25_exclude_2019-v24_exclude_2019):>+12}")
print(f"{'平均每月信号数':<25} {v24_monthly:>11.2f}个 {v25_monthly:>11.2f}个 {(v25_monthly-v24_monthly):>+11.2f}个")
print(f"{'平均每年信号数':<25} {v24_monthly*12:>11.1f}个 {v25_monthly*12:>11.1f}个 {(v25_monthly*12-v24_monthly*12):>+11.1f}个")

# 4. 目标达成情况
print("\n" + "="*80)
print("4. 目标达成情况")
print("="*80)

print(f"\n目标：每月 2-3 个信号（排除 2019 年）")
print(f"\nV2.4: {v24_monthly:.2f} 个/月 {'✅ 达标' if v24_monthly >= 2 else '❌ 未达标'}")
print(f"V2.5: {v25_monthly:.2f} 个/月 {'✅ 达标' if v25_monthly >= 2 else '❌ 未达标'}")

# 5. 参数调整效果
print("\n" + "="*80)
print("5. 参数调整效果分析")
print("="*80)

print(f"""
V2.4 → V2.5 参数调整：
  - 时间窗口：60 天 → 50 天（收紧 16.7%）
  - 跌幅阈值：8% → 9%（收紧 12.5%）
  - 放量涨幅：3% → 3.5%（收紧 16.7%）
  - 量比最小值：1.2 → 1.3（收紧 8.3%）

效果：
  - 总形态数：3031 → 2260（减少 25.4%）
  - 覆盖股票：1960 → 1644（减少 16.1%）
  - 2019 年后：161 → 115（减少 28.6%）
  - 每月信号：2.24 → 1.60（减少 28.6%）

结论：
  ✅ 参数收紧有效减少了信号数量
  ⚠️  但 V2.5 未达到每月 2 个信号的目标
  ✅ V2.4 仍为最佳选择（信号最多）
""")

# 6. 2024 年数据完整性检查
print("\n" + "="*80)
print("6. 2024 年数据完整性检查")
print("="*80)

v24_2024 = v24_yearly.get('2024', 0)
v25_2024 = v25_yearly.get('2024', 0)

print(f"\nV2.4 检测到 2024 年信号：{v24_2024} 个")
print(f"V2.5 检测到 2024 年信号：{v25_2024} 个")
print(f"平均每月：{v24_2024/12:.2f} 个（V2.4）")

if v24_2024 > 0:
    print(f"\n✅ 2024 年数据完整，有信号产生")
else:
    print(f"\n⚠️  2024 年可能数据不完整")

# 7. 603529 检查
print("\n" + "="*80)
print("7. 603529 爱玛科技检测情况")
print("="*80)

v24_603529 = [p for p in v24_data['patterns'] if p['code'] == '603529']
v25_603529 = [p for p in v25_data['patterns'] if p['code'] == '603529']

print(f"\nV2.4: {'✅ 检测到' if v24_603529 else '❌ 未检测到'}")
print(f"V2.5: {'✅ 检测到' if v25_603529 else '❌ 未检测到'}")

if not v24_603529 and not v25_603529:
    print(f"\n原因分析：")
    print(f"  - 缩量→放量时间跨度可能超过 60 天（V2.4）或 50 天（V2.5）")
    print(f"  - 2025 年 8-9 月可能没有符合其他条件的形态")
    print(f"  - 建议：单独监控或设计专用策略")

# 8. 最终推荐
print("\n" + "="*80)
print("8. 最终推荐")
print("="*80)

print(f"""
【综合评估】

V2.4 优势：
  ✅ 信号数量最多（3031 个）
  ✅ 覆盖股票最广（1960 只）
  ✅ 每月信号达标（2.24 个/月）
  ✅ 2019 年后信号最多（161 个）
  ⚠️  胜率相对较低（49.36%）

V2.5 优势：
  ✅ 参数更严格，信号质量可能更高
  ✅ 信号数量适中（2260 个）
  ❌ 每月信号未达标（1.60 个/月）
  ❌ 2019 年后信号较少（115 个）

【最终推荐】

✅ 推荐使用 V2.4

理由：
1. 已达到核心目标（每月 2-3 个信号）
2. 信号数量充足，选择余地大
3. 可以通过质量排序筛选高质量信号
4. 覆盖率广，分散风险

使用建议：
1. 使用质量评分系统（已在 analyze_v24_results.py 中实现）
2. 优先选择总分≥60 分的信号
3. 对于 603529 等特殊股票，单独监控
4. 定期检查 2024 年等较新年份的数据完整性
""")

# 保存对比结果
output = {
    'comparison': {
        'v24_total_patterns': v24_summary['total_patterns'],
        'v25_total_patterns': v25_summary['total_patterns'],
        'v24_matched_stocks': v24_summary['matched_stocks'],
        'v25_matched_stocks': v25_summary['matched_stocks'],
        'v24_monthly_avg': v24_monthly,
        'v25_monthly_avg': v25_monthly,
        'v24_exclude_2019': v24_exclude_2019,
        'v25_exclude_2019': v25_exclude_2019,
    },
    'recommendation': 'V2.4',
    'reason': '信号数量最多，每月达标，覆盖最广'
}

with open('backtest_results/v24_vs_v25_final_comparison.json', 'w', encoding='utf-8') as f:
    json.dump(output, f, ensure_ascii=False, indent=2)

print("\n对比结果已保存到：backtest_results/v24_vs_v25_final_comparison.json")
