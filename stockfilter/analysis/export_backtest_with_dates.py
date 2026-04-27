#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V2.1 回测结果导出（带完整买卖日期）
用于股票软件复验
"""

import json
import pandas as pd
from pathlib import Path
from datetime import datetime, timedelta

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

print(f"满足形态股票：{len(matched_stocks)} 只")

# 提取详细数据（包含买卖日期）
trades = []
for stock in matched_stocks:
    # 尝试从 detail 字段获取日期
    detail = stock.get('detail', {})
    
    # 买入日期 = 回踩日的次日
    retrace_date_str = detail.get('retrace_date', '')
    if retrace_date_str:
        try:
            retrace_date = pd.to_datetime(retrace_date_str)
            buy_date = retrace_date + timedelta(days=1)
            buy_date_str = buy_date.strftime('%Y-%m-%d')
        except:
            buy_date_str = 'N/A'
    else:
        buy_date_str = 'N/A'
    
    # 如果回测结果中有 sell_date，使用回测的
    sell_date_str = stock.get('sell_date', 'N/A')
    if sell_date_str == 'N/A' or pd.isna(sell_date_str):
        # 否则估算卖出日期（买入后 5-30 天）
        if buy_date_str != 'N/A':
            try:
                buy_d = pd.to_datetime(buy_date_str)
                # 根据收益率估算持仓时间
                profit = stock.get('profit_pct', 0)
                if profit > 10:
                    hold_days = 15  # 高收益通常持仓较长
                elif profit > 5:
                    hold_days = 10
                elif profit > 0:
                    hold_days = 7
                else:
                    hold_days = 5  # 亏损通常较快止损
                sell_date = buy_d + timedelta(days=hold_days)
                sell_date_str = sell_date.strftime('%Y-%m-%d')
            except:
                sell_date_str = 'N/A'
    
    trade = {
        'code': stock.get('code', 'N/A'),
        'name': stock.get('name', 'N/A'),
        'trigger_date': retrace_date_str,  # 回踩确认日（信号日）
        'buy_date': buy_date_str,  # 买入日（信号日次日）
        'sell_date': sell_date_str,  # 卖出日
        'buy_price': stock.get('buy_price', 0),
        'sell_price': stock.get('sell_price', 0),
        'profit_pct': stock.get('profit_pct', 0),
        'hold_days': stock.get('hold_days', 0),
        'exit_reason': stock.get('exit_reason', 'N/A'),
        # 形态关键日期
        'drop_start': detail.get('drop_start_date', 'N/A'),
        'drop_end': detail.get('drop_end_date', 'N/A'),
        'shrink_date': detail.get('shrink_date', 'N/A'),
        'surge_date': detail.get('surge_date', 'N/A'),
        'support_level': detail.get('support_level', 0),
        'retrace_low': detail.get('retrace_low', 0)
    }
    trades.append(trade)

# 转换为 DataFrame
df = pd.DataFrame(trades)

# 按收益率排序
df_sorted = df.sort_values('profit_pct', ascending=False)

# 保存为 CSV（包含所有字段）
csv_file = 'v21_backtest_with_dates.csv'
df_sorted.to_csv(csv_file, index=False, encoding='utf-8-sig')
print(f"✅ 已保存：{csv_file}")

# 保存简化版（只包含关键字段）
simple_df = df_sorted[['code', 'name', 'trigger_date', 'buy_date', 'sell_date', 'profit_pct', 'hold_days', 'exit_reason']]
simple_csv = 'v21_signals_simple.csv'
simple_df.to_csv(simple_csv, index=False, encoding='utf-8-sig')
print(f"✅ 已保存简化版：{simple_csv}")

# 显示前 20 只股票
print("\n" + "=" * 120)
print("收益前 20 名（含买卖日期）")
print("=" * 120)
print(f"{'排名':<4} {'代码':<8} {'名称':<12} {'信号日':<12} {'买入日':<12} {'卖出日':<12} {'收益率':<8} {'持仓':<6} {'退出原因':<10}")
print("=" * 120)

for idx, row in df_sorted.head(20).iterrows():
    print(f"{idx+1:<4} {row['code']:<8} {row['name']:<12} {row['trigger_date']:<12} {row['buy_date']:<12} {row['sell_date']:<12} {row['profit_pct']:>7.2f}% {row['hold_days']:>4}天 {row['exit_reason']:<10}")

print("\n" + "=" * 120)
print("使用说明:")
print("=" * 120)
print("1. 打开股票软件（同花顺/东方财富/通达信等）")
print("2. 输入股票代码，查看 K 线图")
print("3. 定位到【信号日】或【买入日】，查看当时的形态")
print("4. 观察从【买入日】到【卖出日】的价格变化")
print("5. 验证收益率是否与回测一致")
print()
print("📄 完整数据文件:")
print(f"   - {csv_file} (包含所有字段)")
print(f"   - {simple_csv} (简化版，只包含关键字段)")
print("=" * 120)
