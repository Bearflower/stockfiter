#!/usr/bin/env python3
"""
OBPC 7月信号回测脚本（本地执行）
从CSV文件读取信号数据和K线数据，模拟交易，计算收益率
"""
import pandas as pd
import os
from datetime import datetime

# 配置
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
CONFIG = {
    'trailing_stop_ratio': 0.08,
    'hard_stop_loss': 0.10,
    'stop_loss_ratio': 0.97,
    'min_hold_days': 5,
    'max_hold_days': 30,
    'commission': 0.00025,
    'stamp_tax': 0.001,
    'slippage': 0.001,
}

# 加载数据
df_signals = pd.read_csv(os.path.join(DATA_DIR, 'july_signals.csv'))
df_klines = pd.read_csv(os.path.join(DATA_DIR, 'july_klines.csv'), parse_dates=['date'])
df_index = pd.read_csv(os.path.join(DATA_DIR, 'july_index.csv'), parse_dates=['date'])

# 打印日期范围
print(f"K线数据日期范围：{df_klines['date'].min()} ~ {df_klines['date'].max()}")
print(f"指数数据日期范围：{df_index['date'].min()} ~ {df_index['date'].max()}")

results = []
for _, sig in df_signals.iterrows():
    code = sig['code']
    name = sig['name']
    signal_date = sig['scan_date']
    support_level = sig['support_level']
    entry_price_ref = sig['current_close']

    # 获取该股票的K线数据
    kline = df_klines[df_klines['code'] == code].sort_values('date').reset_index(drop=True)
    if len(kline) == 0:
        print(f"  {code} {name} 无K线数据，跳过")
        continue

    # 找到信号日在K线中的位置
    sig_date = pd.to_datetime(signal_date)
    sig_idx = kline.index[kline['date'] == sig_date].tolist()
    if not sig_idx:
        print(f"  {code} {name} 信号日 {signal_date} 不在K线数据中，跳过")
        continue
    sig_idx = sig_idx[0]

    # 次日买入
    entry_idx = sig_idx + 1
    if entry_idx >= len(kline):
        print(f"  {code} {name} 信号日后无数据，跳过")
        continue

    entry_row = kline.iloc[entry_idx]
    entry_price = entry_row['open'] * (1 + CONFIG['slippage'])
    entry_date = entry_row['date'].strftime('%Y-%m-%d')

    # 止损价
    stop_loss_price = support_level * CONFIG['stop_loss_ratio']

    # 模拟持仓
    max_price = entry_price
    exit_idx = -1
    exit_price = 0.0
    exit_reason = ''

    hold_end_idx = min(entry_idx + CONFIG['max_hold_days'], len(kline) - 1)

    for i in range(entry_idx, hold_end_idx + 1):
        row = kline.iloc[i]
        high = row['high']
        low = row['low']
        close = row['close']
        hold_days = i - entry_idx

        # 更新最高价
        if high > max_price:
            max_price = high

        # 最短持仓期内只检查硬止损
        if hold_days < CONFIG['min_hold_days']:
            hard_stop_price = entry_price * (1 - CONFIG['hard_stop_loss'])
            if low <= hard_stop_price:
                exit_idx = i
                exit_price = hard_stop_price
                exit_reason = 'hard_stop'
                break
            continue

        # 1. 硬止损
        hard_stop_price = entry_price * (1 - CONFIG['hard_stop_loss'])
        if low <= hard_stop_price:
            exit_idx = i
            exit_price = hard_stop_price
            exit_reason = 'hard_stop'
            break

        # 2. 支撑位止损
        if low <= stop_loss_price:
            exit_idx = i
            exit_price = stop_loss_price
            exit_reason = 'support_stop'
            break

        # 3. 移动止盈
        trailing_stop_price = max_price * (1 - CONFIG['trailing_stop_ratio'])
        if low <= trailing_stop_price:
            exit_idx = i
            exit_price = trailing_stop_price
            exit_reason = 'trailing_stop'
            break

        # 4. 最长持仓到期
        if hold_days >= CONFIG['max_hold_days']:
            exit_idx = i
            exit_price = close
            exit_reason = 'max_hold'
            break

    if exit_idx == -1:
        exit_idx = hold_end_idx
        exit_price = kline.iloc[exit_idx]['close']
        exit_reason = 'max_hold'

    # 计算收益率
    actual_exit_price = exit_price * (1 - CONFIG['slippage'])
    raw_return = (actual_exit_price - entry_price) / entry_price
    buy_cost = entry_price * CONFIG['commission']
    sell_cost = actual_exit_price * (CONFIG['commission'] + CONFIG['stamp_tax'])
    net_profit = (actual_exit_price - entry_price) - buy_cost - sell_cost
    net_return = net_profit / entry_price

    hold_days = exit_idx - entry_idx
    exit_date_str = kline.iloc[exit_idx]['date'].strftime('%Y-%m-%d')

    results.append({
        'code': code,
        'name': name,
        'signal_date': signal_date,
        'entry_date': entry_date,
        'entry_price': round(entry_price, 4),
        'exit_date': exit_date_str,
        'exit_price': round(actual_exit_price, 4),
        'exit_reason': exit_reason,
        'hold_days': hold_days,
        'raw_return': round(raw_return * 100, 2),
        'net_return': round(net_return * 100, 2),
        'max_price': round(max_price, 4),
        'support_level': round(support_level, 4),
        'stop_loss_price': round(stop_loss_price, 4),
    })

# ===== 输出结果 =====
df_results = pd.DataFrame(results)

print("\n" + "=" * 80)
print("OBPC 7月信号回测结果")
print("=" * 80)

# 核心指标
total = len(df_results)
win = len(df_results[df_results['net_return'] > 0])
loss = len(df_results[df_results['net_return'] <= 0])
avg_return = df_results['net_return'].mean()
total_return = df_results['net_return'].sum()
max_return = df_results['net_return'].max()
min_return = df_results['net_return'].min()
avg_hold = df_results['hold_days'].mean()

print(f"\n--- 核心指标 ---")
print(f"  总信号数：       {total}")
print(f"  盈利交易数：     {win}")
print(f"  亏损交易数：     {loss}")
print(f"  胜率：           {win/total*100:.2f}%")
print(f"  平均收益：       {avg_return:.2f}%")
print(f"  累计收益（每信号等权）：{total_return:.2f}%")
print(f"  最高收益：       {max_return:.2f}%")
print(f"  最低收益：       {min_return:.2f}%")
print(f"  平均持仓天数：   {avg_hold:.1f}")
print(f"  盈利/亏损比：    {df_results[df_results['net_return']>0]['net_return'].mean():.2f}% / {df_results[df_results['net_return']<=0]['net_return'].mean():.2f}%")

# 退出原因分布
print(f"\n--- 退出原因分布 ---")
for reason, count in df_results['exit_reason'].value_counts().items():
    reason_names = {'trailing_stop': '移动止盈', 'hard_stop': '硬止损', 'support_stop': '支撑位止损', 'max_hold': '最长持仓到期'}
    print(f"  {reason_names.get(reason, reason):<10} {count:>3} 次 ({count/total*100:.1f}%)")

# 按信号日期分组
print(f"\n--- 按信号日期统计 ---")
df_results['signal_date'] = pd.to_datetime(df_results['signal_date'])
for date, group in df_results.groupby('signal_date'):
    date_str = date.strftime('%Y-%m-%d')
    g_win = len(group[group['net_return'] > 0])
    g_total = len(group)
    g_avg = group['net_return'].mean()
    print(f"  {date_str}: {g_total}个信号, 胜率{g_win/g_total*100:.0f}%, 平均收益{g_avg:.2f}%")

# 详细的交易列表
print(f"\n--- 详细交易列表 ---")
print(f"{'日期':<11} {'代码':<8} {'名称':<10} {'买入价':>8} {'卖出价':>8} {'收益%':>7} {'持仓':>4} {'退出原因':<12}")
print("-" * 75)
for _, r in df_results.iterrows():
    reason_names = {'trailing_stop': '移动止盈', 'hard_stop': '硬止损', 'support_stop': '支撑位止损', 'max_hold': '最长持仓到期'}
    marker = '✅' if r['net_return'] > 0 else '❌'
    print(f"{r['signal_date']:<11} {r['code']:<8} {r['name']:<10} {r['entry_price']:>8.2f} {r['exit_price']:>8.2f} {r['net_return']:>+6.2f}% {r['hold_days']:>4} {reason_names.get(r['exit_reason'], r['exit_reason']):<12}")

# 保存结果
output_path = os.path.join(DATA_DIR, 'july_backtest_results.csv')
df_results.to_csv(output_path, index=False)
print(f"\n结果已保存到：{output_path}")

# 按持有期统计
print(f"\n--- 按持仓天数收益率分布 ---")
for days in [5, 10, 15, 20, 30]:
    sub = df_results[df_results['hold_days'] <= days]
    if len(sub) > 0:
        print(f"  ≤{days}天: {len(sub)}笔, 胜率{len(sub[sub['net_return']>0])/len(sub)*100:.1f}%, 平均收益{sub['net_return'].mean():.2f}%")