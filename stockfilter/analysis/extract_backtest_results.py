#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""提取 V2.1 回测结果的关键信息"""

import json
import pandas as pd

# 读取回测结果
with open('backtest_results.json', 'r') as f:
    data = json.load(f)

# 提取满足形态的股票（is_match=True）
matched_stocks = [stock for stock in data if stock.get('is_match', False)]

print("=" * 120)
print("V2.1 回测结果统计")
print("=" * 120)
print(f"总股票数：{len(data)}")
print(f"满足形态：{len(matched_stocks)} 只")
print(f"满足比例：{len(matched_stocks)/len(data)*100:.2f}%")
print()

# 提取关键字段
if len(matched_stocks) > 0:
    print("=" * 120)
    print("第一个满足形态的股票详细数据:")
    print("=" * 120)
    print(json.dumps(matched_stocks[0], indent=2, ensure_ascii=False))
    
    # 检查有哪些字段
    all_keys = set()
    for stock in matched_stocks[:10]:
        all_keys.update(stock.keys())
    
    print(f"\n所有字段：{sorted(all_keys)}")
    
    # 提取关键信息并保存为 CSV
    print(f"\n提取所有 {len(matched_stocks)} 只股票的关键信息...")
    
    results = []
    for stock in matched_stocks:
        result = {
            'code': stock.get('code', 'N/A'),
            'name': stock.get('name', 'N/A'),
            'trigger_date': stock.get('retrace_date') or stock.get('trigger_date') or stock.get('signal_date', 'N/A'),
            'surge_date': stock.get('surge_date', 'N/A'),
            'exit_date': stock.get('exit_date') or stock.get('sell_date', 'N/A'),
            'return_pct': stock.get('return_pct') or stock.get('total_return') or stock.get('return', 'N/A'),
            'exit_type': stock.get('exit_type') or stock.get('trigger_type', 'N/A'),
            'support_level': stock.get('support_level', 'N/A'),
            'stop_loss_price': stock.get('stop_loss_price', 'N/A'),
            'hold_days': stock.get('hold_days', 'N/A')
        }
        results.append(result)
    
    # 转换为 DataFrame
    df = pd.DataFrame(results)
    
    # 保存为 CSV
    output_file = 'v21_backtest_signals.csv'
    df.to_csv(output_file, index=False, encoding='utf-8-sig')
    print(f"✅ 已保存到：{output_file}")
    
    # 显示前 30 只股票
    print(f"\n前 30 只股票的关键信息:")
    print("=" * 120)
    print(f"{'序号':<4} {'代码':<8} {'名称':<10} {'触发日期':<12} {'退出日期':<12} {'收益率':<10} {'退出类型':<15} {'持仓天数':<8}")
    print("=" * 120)
    
    for i, row in df.head(30).iterrows():
        print(f"{i+1:<4} {row['code']:<8} {str(row['name']):<10} {str(row['trigger_date']):<12} {str(row['exit_date']):<12} {str(row['return_pct']):<10} {str(row['exit_type']):<15} {str(row['hold_days']):<8}")
    
    # 统计信息
    print(f"\n收益率统计:")
    print("=" * 120)
    numeric_returns = pd.to_numeric(df['return_pct'], errors='coerce')
    print(f"平均收益：{numeric_returns.mean():.2f}%")
    print(f"最高收益：{numeric_returns.max():.2f}%")
    print(f"最低收益：{numeric_returns.min():.2f}%")
    print(f"胜率：{(numeric_returns > 0).sum()}/{len(numeric_returns)} = {(numeric_returns > 0).sum()/len(numeric_returns)*100:.2f}%")
    
    # 按退出类型统计
    print(f"\n按退出类型统计:")
    print("=" * 120)
    exit_type_stats = df.groupby('exit_type').size()
    for exit_type, count in exit_type_stats.items():
        print(f"{exit_type}: {count} 只 ({count/len(df)*100:.1f}%)")
