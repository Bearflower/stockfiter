#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V2.1 完整回测报告生成器
包含：触发时间、止盈时机、收益率、持仓天数等完整交易数据
"""

import json
import pandas as pd
from pathlib import Path
from datetime import datetime

# 读取回测结果
result_files = list(Path('backtest_results').glob('backtest_v21_*.json'))
if not result_files:
    print("❌ 未找到回测结果文件")
    exit(1)

latest_file = max(result_files)
print(f"读取回测结果：{latest_file}")

with open(latest_file, 'r', encoding='utf-8') as f:
    data = json.load(f)

# 提取满足形态的股票
matched_stocks = [r for r in data if r.get('is_match', False)]

print("=" * 120)
print("V2.1 完整回测报告")
print("=" * 120)
print(f"检测股票总数：{len(data)}")
print(f"满足形态股票：{len(matched_stocks)}")
print(f"满足比例：{len(matched_stocks)/len(data)*100:.2f}%")
print()

# 提取详细交易数据
trades = []
for stock in matched_stocks:
    trade = {
        'code': stock.get('code', 'N/A'),
        'name': stock.get('name', 'N/A'),
        'buy_date': stock.get('buy_date', 'N/A'),
        'sell_date': stock.get('sell_date', 'N/A'),
        'buy_price': stock.get('buy_price', 0),
        'sell_price': stock.get('sell_price', 0),
        'profit_pct': stock.get('profit_pct', 0),
        'hold_days': stock.get('hold_days', 0),
        'exit_reason': stock.get('exit_reason', 'N/A')
    }
    trades.append(trade)

# 转换为 DataFrame
df = pd.DataFrame(trades)

# 保存为 CSV
csv_file = 'v21_full_backtest_trades.csv'
df.to_csv(csv_file, index=False, encoding='utf-8-sig')
print(f"✅ 已保存交易明细：{csv_file}")
print()

# 统计信息
print("=" * 120)
print("回测统计结果")
print("=" * 120)

# 收益统计
profits = df['profit_pct']
avg_profit = profits.mean()
max_profit = profits.max()
min_profit = profits.min()
median_profit = profits.median()
profitable = (profits > 0).sum()
win_rate = profitable / len(df) * 100

print(f"\n收益统计:")
print(f"  平均收益：{avg_profit:.2f}%")
print(f"  最高收益：{max_profit:.2f}%")
print(f"  最低收益：{min_profit:.2f}%")
print(f"  中位数收益：{median_profit:.2f}%")
print(f"  盈利股票：{profitable}/{len(df)} = {win_rate:.1f}%")

# 持仓天数统计
print(f"\n持仓天数统计:")
print(f"  平均持仓：{df['hold_days'].mean():.1f} 天")
print(f"  最长持仓：{df['hold_days'].max()} 天")
print(f"  最短持仓：{df['hold_days'].min()} 天")

# 退出原因统计
print(f"\n退出原因统计:")
exit_reasons = df['exit_reason'].value_counts()
for reason, count in exit_reasons.items():
    pct = count / len(df) * 100
    print(f"  {reason}: {count} 只 ({pct:.1f}%)")

# 按收益排序
print(f"\n收益前 10 名:")
print("-" * 120)
top10 = df.nlargest(10, 'profit_pct')
for idx, row in top10.iterrows():
    print(f"{idx+1:2d}. {row['code']} | {row['name']:10s} | 买入：{row['buy_date']} | 卖出：{row['sell_date']} | 收益：{row['profit_pct']:6.2f}% | 持仓：{row['hold_days']:2d}天 | 退出：{row['exit_reason']}")

print(f"\n收益后 10 名:")
print("-" * 120)
bottom10 = df.nsmallest(10, 'profit_pct')
for idx, row in bottom10.iterrows():
    print(f"{idx+1:2d}. {row['code']} | {row['name']:10s} | 买入：{row['buy_date']} | 卖出：{row['sell_date']} | 收益：{row['profit_pct']:6.2f}% | 持仓：{row['hold_days']:2d}天 | 退出：{row['exit_reason']}")

# 按月份统计
print(f"\n按买入月份统计:")
print("-" * 120)
df['buy_month'] = pd.to_datetime(df['buy_date']).dt.strftime('%Y-%m')
monthly_stats = df.groupby('buy_month').agg({
    'code': 'count',
    'profit_pct': ['mean', 'median'],
    'hold_days': 'mean'
}).round(2)
print(monthly_stats)

# 保存 Markdown 报告
md_file = 'V21_完整回测报告.md'
with open(md_file, 'w', encoding='utf-8') as f:
    f.write("# V2.1 完整回测报告\n\n")
    f.write(f"**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    f.write(f"**数据源**: {latest_file}\n\n")
    
    f.write("## 📊 总体统计\n\n")
    f.write(f"- 检测股票总数：{len(data)}\n")
    f.write(f"- 满足形态股票：{len(matched_stocks)}\n")
    f.write(f"- 满足比例：{len(matched_stocks)/len(data)*100:.2f}%\n\n")
    
    f.write("## 💰 收益统计\n\n")
    f.write(f"- 平均收益：**{avg_profit:.2f}%**\n")
    f.write(f"- 最高收益：**{max_profit:.2f}%**\n")
    f.write(f"- 最低收益：**{min_profit:.2f}%**\n")
    f.write(f"- 中位数收益：**{median_profit:.2f}%**\n")
    f.write(f"- 胜率：**{win_rate:.1f}%** ({profitable}/{len(df)})\n\n")
    
    f.write("## 📈 持仓统计\n\n")
    f.write(f"- 平均持仓：**{df['hold_days'].mean():.1f} 天**\n")
    f.write(f"- 最长持仓：**{df['hold_days'].max()} 天**\n")
    f.write(f"- 最短持仓：**{df['hold_days'].min()} 天**\n\n")
    
    f.write("## 📅 收益前 10 名\n\n")
    f.write("| 排名 | 代码 | 名称 | 买入日期 | 卖出日期 | 收益率 | 持仓天数 | 退出原因 |\n")
    f.write("|------|------|------|----------|----------|--------|----------|----------|\n")
    for idx, row in top10.iterrows():
        f.write(f"| {idx+1} | {row['code']} | {row['name']} | {row['buy_date']} | {row['sell_date']} | {row['profit_pct']:.2f}% | {row['hold_days']} | {row['exit_reason']} |\n")
    
    f.write("\n## 📉 收益后 10 名\n\n")
    f.write("| 排名 | 代码 | 名称 | 买入日期 | 卖出日期 | 收益率 | 持仓天数 | 退出原因 |\n")
    f.write("|------|------|------|----------|----------|--------|----------|----------|\n")
    for idx, row in bottom10.iterrows():
        f.write(f"| {idx+1} | {row['code']} | {row['name']} | {row['buy_date']} | {row['sell_date']} | {row['profit_pct']:.2f}% | {row['hold_days']} | {row['exit_reason']} |\n")

print(f"\n✅ 已保存 Markdown 报告：{md_file}")
print("\n" + "=" * 120)
print("回测报告生成完成！")
print("=" * 120)
