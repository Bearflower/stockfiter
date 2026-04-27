#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
生成 V2.1 vs V2.2 对比图表
"""

import matplotlib.pyplot as plt
import matplotlib
matplotlib.use('Agg')

# 设置中文字体
plt.rcParams['font.sans-serif'] = ['Arial Unicode MS', 'SimHei', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False

# 数据
metrics = ['形态数', '交易数', '平均收益 (%)', '胜率 (%)']
v21_values = [5, 5, 0.35, 40.00]
v22_values = [478, 448, 4.28, 64.96]

# 创建图表
fig, axes = plt.subplots(1, 2, figsize=(16, 8))

# 左图：绝对数值对比
x = range(len(metrics))
width = 0.35

bars1 = axes[0].bar([i - width/2 for i in x], v21_values, width, label='V2.1', color='lightcoral', edgecolor='red', linewidth=2)
bars2 = axes[0].bar([i + width/2 for i in x], v22_values, width, label='V2.2', color='lightblue', edgecolor='blue', linewidth=2)

axes[0].set_title('V2.1 vs V2.2 回测指标对比', fontsize=16, fontweight='bold')
axes[0].set_xlabel('指标', fontsize=12)
axes[0].set_ylabel('数值', fontsize=12)
axes[0].set_xticks(x)
axes[0].set_xticklabels(metrics)
axes[0].legend(fontsize=12)
axes[0].grid(axis='y', alpha=0.3, linestyle='--')

# 在柱子上标注数值
for bar, value in zip(bars1, v21_values):
    height = bar.get_height()
    axes[0].text(bar.get_x() + bar.get_width()/2., height,
                f'{value:.2f}' if isinstance(value, float) else str(value),
                ha='center', va='bottom', fontsize=11, fontweight='bold')

for bar, value in zip(bars2, v22_values):
    height = bar.get_height()
    axes[0].text(bar.get_x() + bar.get_width()/2., height,
                f'{value:.2f}' if isinstance(value, float) else str(value),
                ha='center', va='bottom', fontsize=11, fontweight='bold')

# 右图：增长率对比
improvements = [
    (478 - 5) / 5 * 100,  # 形态数增长
    (448 - 5) / 5 * 100,  # 交易数增长
    (4.28 - 0.35) / 0.35 * 100,  # 收益增长
    (64.96 - 40.00) / 40.00 * 100  # 胜率增长
]

colors = ['green' if imp > 0 else 'red' for imp in improvements]
bars = axes[1].bar(metrics, improvements, color=colors, edgecolor='black', linewidth=1.2)

axes[1].set_title('V2.2 相对 V2.1 的增长率', fontsize=16, fontweight='bold')
axes[1].set_xlabel('指标', fontsize=12)
axes[1].set_ylabel('增长率 (%)', fontsize=12)
axes[1].axhline(y=0, color='black', linestyle='-', linewidth=0.8)
axes[1].grid(axis='y', alpha=0.3, linestyle='--')

# 在柱子上标注数值
for bar, imp in zip(bars, improvements):
    height = bar.get_height()
    axes[1].text(bar.get_x() + bar.get_width()/2., height,
                f'{imp:.0f}%',
                ha='center', va='bottom' if height > 0 else 'top',
                fontsize=12, fontweight='bold')

plt.tight_layout()
output_file = 'backtest_results/v21_vs_v22_comparison.png'
plt.savefig(output_file, dpi=150, bbox_inches='tight')
print(f"对比图表已保存到：{output_file}")

# 输出对比表格
print("\n" + "="*80)
print("V2.1 vs V2.2 回测对比表")
print("="*80)

print(f"\n{'指标':<20} {'V2.1':>15} {'V2.2':>15} {'差异':>15} {'增长率':>15}")
print("-"*80)

data = [
    ('检测股票数', 16, 3317, 3301, (3317-16)/16*100),
    ('满足形态股票数', 5, 413, 408, (413-5)/5*100),
    ('总形态数', 5, 478, 473, (478-5)/5*100),
    ('总交易数', 5, 448, 443, (448-5)/5*100),
    ('平均收益 (%)', 0.35, 4.28, 3.93, (4.28-0.35)/0.35*100),
    ('胜率 (%)', 40.00, 64.96, 24.96, (64.96-40.00)/40.00*100),
    ('最高收益 (%)', 10.46, 62.88, 52.42, (62.88-10.46)/10.46*100),
    ('最低收益 (%)', -8.35, -8.35, 0.00, 0),
]

for metric, v21, v22, diff, growth in data:
    print(f"{metric:<20} {v21:>15} {v22:>15} {diff:>+15.0f} {growth:>+14.1f}%")

print("-"*80)

# 输出核心改进
print("\n" + "="*80)
print("核心改进点")
print("="*80)
print("""
1. 时间窗口优化：
   - V2.1: 缩量→放量 = 10 天
   - V2.2: 缩量→放量 = 25 天
   - 提升：150%

2. 数据规模扩大：
   - V2.1: 16 只股票（本地数据）
   - V2.2: 3317 只股票（baostocks_full 完整数据）
   - 提升：20631%

3. 形态检测数量：
   - V2.1: 5 个形态
   - V2.2: 478 个形态
   - 提升：9460%

4. 收益表现：
   - 平均收益：0.35% → 4.28%（提升 1123%）
   - 胜率：40.00% → 64.96%（提升 62.4%）
   - 最高收益：10.46% → 62.88%（提升 501%）

5. 覆盖范围：
   - V2.2 检测到 413 只股票有形态
   - 占总检测数的 12.45%
   - 年度分布：2019 年（431 个）占绝对主导

6. 局限性：
   - 603529 爱玛科技仍无法检测
   - 原因：缩量→放量时间跨度约 160 天，远超 25 天窗口
   - 建议：手动监控或单独设置更宽松参数
""")
