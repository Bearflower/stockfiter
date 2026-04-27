#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
生成 V2.1 vs V2.2 公平对比图表（都使用 baostocks_full 数据）
"""

import matplotlib.pyplot as plt
import matplotlib
matplotlib.use('Agg')

# 设置中文字体
plt.rcParams['font.sans-serif'] = ['Arial Unicode MS', 'SimHei', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False

# 数据（从公平对比结果）
v21_patterns = 338
v22_patterns = 478
v21_stocks = 289
v22_stocks = 413
v21_trades = 318
v22_trades = 448
v21_avg_return = 5.39
v22_avg_return = 4.28
v21_win_rate = 73.58
v22_win_rate = 64.96

# 创建图表
fig, axes = plt.subplots(2, 2, figsize=(16, 12))

# 图 1: 形态数量对比
metrics1 = ['形态数', '覆盖股票', '交易数']
v21_values1 = [v21_patterns, v21_stocks, v21_trades]
v22_values1 = [v22_patterns, v22_stocks, v22_trades]

x1 = range(len(metrics1))
width = 0.35

bars1 = axes[0,0].bar([i - width/2 for i in x1], v21_values1, width, label='V2.1 (10 天窗口)', color='lightcoral', edgecolor='red', linewidth=2)
bars2 = axes[0,0].bar([i + width/2 for i in x1], v22_values1, width, label='V2.2 (25 天窗口)', color='lightblue', edgecolor='blue', linewidth=2)

axes[0,0].set_title('图 1: 检测规模对比', fontsize=14, fontweight='bold')
axes[0,0].set_xlabel('指标', fontsize=12)
axes[0,0].set_ylabel('数量', fontsize=12)
axes[0,0].set_xticks(x1)
axes[0,0].set_xticklabels(metrics1)
axes[0,0].legend(fontsize=10)
axes[0,0].grid(axis='y', alpha=0.3, linestyle='--')

for bar, value in zip(bars1, v21_values1):
    axes[0,0].text(bar.get_x() + bar.get_width()/2., value, str(value),
                  ha='center', va='bottom', fontsize=11, fontweight='bold')
for bar, value in zip(bars2, v22_values1):
    axes[0,0].text(bar.get_x() + bar.get_width()/2., value, str(value),
                  ha='center', va='bottom', fontsize=11, fontweight='bold')

# 图 2: 收益指标对比
metrics2 = ['平均收益 (%)', '胜率 (%)']
v21_values2 = [v21_avg_return, v21_win_rate]
v22_values2 = [v22_avg_return, v22_win_rate]

x2 = range(len(metrics2))

bars3 = axes[0,1].bar([i - width/2 for i in x2], v21_values2, width, label='V2.1', color='lightcoral', edgecolor='red', linewidth=2)
bars4 = axes[0,1].bar([i + width/2 for i in x2], v22_values2, width, label='V2.2', color='lightblue', edgecolor='blue', linewidth=2)

axes[0,1].set_title('图 2: 收益指标对比', fontsize=14, fontweight='bold')
axes[0,1].set_xlabel('指标', fontsize=12)
axes[0,1].set_ylabel('百分比 (%)', fontsize=12)
axes[0,1].set_xticks(x2)
axes[0,1].set_xticklabels(metrics2)
axes[0,1].legend(fontsize=10)
axes[0,1].grid(axis='y', alpha=0.3, linestyle='--')

for bar, value in zip(bars3, v21_values2):
    axes[0,1].text(bar.get_x() + bar.get_width()/2., value, f'{value:.2f}%',
                  ha='center', va='bottom', fontsize=11, fontweight='bold')
for bar, value in zip(bars4, v22_values2):
    axes[0,1].text(bar.get_x() + bar.get_width()/2., value, f'{value:.2f}%',
                  ha='center', va='bottom', fontsize=11, fontweight='bold')

# 图 3: 年度形态分布对比
years = ['2019', '2020', '2021', '2022', '2023', '2025']
v21_yearly = [301, 13, 11, 5, 4, 4]
v22_yearly = [431, 21, 11, 6, 4, 5]

x3 = range(len(years))

bars5 = axes[1,0].bar([i - width/2 for i in x3], v21_yearly, width, label='V2.1', color='lightcoral', edgecolor='red', linewidth=2)
bars6 = axes[1,0].bar([i + width/2 for i in x3], v22_yearly, width, label='V2.2', color='lightblue', edgecolor='blue', linewidth=2)

axes[1,0].set_title('图 3: 年度形态分布对比', fontsize=14, fontweight='bold')
axes[1,0].set_xlabel('年份', fontsize=12)
axes[1,0].set_ylabel('形态数量', fontsize=12)
axes[1,0].set_xticks(x3)
axes[1,0].set_xticklabels(years)
axes[1,0].legend(fontsize=10)
axes[1,0].grid(axis='y', alpha=0.3, linestyle='--')

for bar, value in zip(bars5, v21_yearly):
    axes[1,0].text(bar.get_x() + bar.get_width()/2., value, str(value),
                  ha='center', va='bottom', fontsize=10, fontweight='bold')
for bar, value in zip(bars6, v22_yearly):
    axes[1,0].text(bar.get_x() + bar.get_width()/2., value, str(value),
                  ha='center', va='bottom', fontsize=10, fontweight='bold')

# 图 4: V2.2 相对 V2.1 的增长率
growth_rates = [
    (v22_patterns - v21_patterns) / v21_patterns * 100,
    (v22_stocks - v21_stocks) / v21_stocks * 100,
    (v22_trades - v21_trades) / v21_trades * 100,
]

metrics4 = ['形态数', '覆盖股票', '交易数']
x4 = range(len(metrics4))

colors = ['green' if gr > 0 else 'red' for gr in growth_rates]
bars = axes[1,1].bar(x4, growth_rates, color=colors, edgecolor='black', linewidth=1.2)

axes[1,1].set_title('图 4: V2.2 相对 V2.1 的增长率', fontsize=14, fontweight='bold')
axes[1,1].set_xlabel('指标', fontsize=12)
axes[1,1].set_ylabel('增长率 (%)', fontsize=12)
axes[1,1].axhline(y=0, color='black', linestyle='-', linewidth=0.8)
axes[1,1].set_xticks(x4)
axes[1,1].set_xticklabels(metrics4)
axes[1,1].grid(axis='y', alpha=0.3, linestyle='--')

for bar, gr in zip(bars, growth_rates):
    height = bar.get_height()
    axes[1,1].text(bar.get_x() + bar.get_width()/2., height,
                  f'{gr:.1f}%',
                  ha='center', va='bottom' if height > 0 else 'top',
                  fontsize=12, fontweight='bold')

plt.tight_layout()
output_file = 'backtest_results/v21_vs_v22_fair_comparison.png'
plt.savefig(output_file, dpi=150, bbox_inches='tight')
print(f"公平对比图表已保存到：{output_file}")

# 输出详细对比表
print("\n" + "="*80)
print("V2.1 vs V2.2 公平对比表（均使用 baostocks_full 数据）")
print("="*80)

print(f"\n{'指标':<20} {'V2.1':>12} {'V2.2':>12} {'差异':>12} {'增长率':>12}")
print("-"*80)

data = [
    ('检测股票数', 3317, 3317, 0, 0.0),
    ('满足形态股票数', 289, 413, 124, 42.9),
    ('总形态数', 338, 478, 140, 41.4),
    ('总交易数', 318, 448, 130, 40.9),
    ('平均收益 (%)', 5.39, 4.28, -1.11, -20.6),
    ('胜率 (%)', 73.58, 64.96, -8.62, -11.7),
    ('最高收益 (%)', 62.88, 62.88, 0.0, 0.0),
    ('最低收益 (%)', -8.35, -8.35, 0.0, 0.0),
]

for metric, v21, v22, diff, growth in data:
    print(f"{metric:<20} {v21:>12} {v22:>12} {diff:>+12} {growth:>+11.1f}%")

print("-"*80)

print("\n" + "="*80)
print("关键发现")
print("="*80)
print("""
1. 检测规模提升：
   - 形态数：338 → 478 (+41.4%)
   - 覆盖股票：289 → 413 (+42.9%)
   - 交易数：318 → 448 (+40.9%)

2. 收益质量变化：
   - 平均收益：5.39% → 4.28% (-20.6%)
   - 胜率：73.58% → 64.96% (-11.7%)
   - 说明：放宽窗口后，检测到更多形态，但质量略有下降

3. 年度分布：
   - 2019 年：301 → 431 (+43.2%)，增长最多
   - 2020 年：13 → 21 (+61.5%)，增长率最高
   - 其他年份增长较少

4. 603529 检测：
   - V2.1: ❌ 未检测到
   - V2.2: ❌ 未检测到
   - 原因：时间跨度太大（约 160 天），远超 25 天窗口

5. 策略建议：
   - V2.2 的 25 天窗口是合理的平衡点
   - 虽然胜率略有下降，但覆盖更广、机会更多
   - 总体收益仍然可观（4.28% 平均收益）
   - 603529 属于特殊情况，不建议为了它进一步放宽窗口
""")
