#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
查看 V2.2 回测结果
"""

import json

with open('backtest_results/backtest_v22_20260413_223608.json', 'r') as f:
    data = json.load(f)

print('='*80)
print('V2.2 回测结果（baostocks_full 完整数据）')
print('='*80)
print(f'检测股票数：{data["summary"]["total_stocks"]}')
print(f'满足形态股票数：{data["summary"]["matched_stocks"]}')
print(f'总形态数：{data["summary"]["total_patterns"]}')
print(f'总交易数：{data["summary"]["total_trades"]}')
print(f'平均收益：{data["summary"]["avg_return"]:.2f}%')
print(f'胜率：{data["summary"]["win_rate"]:.2f}%')
print(f'最高收益：{data["summary"]["max_return"]:.2f}%')
print(f'最低收益：{data["summary"]["min_return"]:.2f}%')

# 查找 603529
print('\n' + '='*80)
print('603529 爱玛科技检测结果：')
print('='*80)

patterns_603529 = [p for p in data['patterns'] if p['code'] == '603529']

if patterns_603529:
    print(f'✅ 检测到 {len(patterns_603529)} 个形态：')
    for i, p in enumerate(patterns_603529, 1):
        print(f'\n形态 {i}:')
        print(f'  下跌：{p["drop_start_date"]} 到 {p["drop_end_date"]} ({p["drop_change"]*100:.2f}%)')
        print(f'  缩量：{p["shrink_date"]}')
        print(f'  放量：{p["surge_date"]} ({p["surge_close"]})')
        print(f'  回踩：{p["retrace_date"]} ({p["retrace_close"]})')
        print(f'  缩量到放量天数：{p["shrink_to_surge_days"]}')
else:
    print('❌ 未检测到形态')

# 显示一些示例
print('\n' + '='*80)
print('形态示例（前 10 个）：')
print('='*80)
for i, p in enumerate(data['patterns'][:10], 1):
    print(f'{i}. {p["code"]}: {p["surge_date"]} (缩量→放量：{p["shrink_to_surge_days"]}天)')
