#!/usr/bin/env python3
"""用数据库数据 + 修复后的策略验证 600020 OBPC 信号"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
from datetime import datetime

from strategy.oversold_bounce.strategy import OversoldBounceStrategy

# 加载数据库导出的 K 线
df = pd.read_csv("/tmp/600020_db.csv", parse_dates=["date"])
df = df.sort_values("date").reset_index(drop=True)

print("=" * 70)
print("600020 中原高速 OBPC 策略验证（修复后 + 数据库数据）")
print("=" * 70)
print(f"数据: {len(df)} 条, {df['date'].iloc[0].strftime('%Y-%m-%d')} ~ {df['date'].iloc[-1].strftime('%Y-%m-%d')}")
print(f"字段: {list(df.columns)}")
print(f"amount 示例: {df['amount'].head(3).tolist()} (单位: 元)")

# 初始化策略
strategy = OversoldBounceStrategy()
print(f"\n策略: {strategy.name} v{strategy.version}")
print(f"参数: {strategy.params}")

# 执行检测
signal = strategy.analyze("600020", {"d": df})

print("\n" + "=" * 70)
print("检测结果")
print("=" * 70)

if signal is None:
    print("❌ 未匹配到任何完整的 OBPC 形态")
else:
    d = signal.detail
    print("✅ 匹配成功！")
    print(f"  大跌起始:  {d.get('drop_start_date')} (自然日)")
    print(f"  大跌结束:  {d.get('drop_end_date')} (自然日)")
    print(f"  大跌跌幅:  {d.get('drop_change', 0) * 100:.2f}%")
    print(f"  缩量日期:  {d.get('shrink_date')} (自然日)")
    print(f"  放量日期:  {d.get('surge_date')} (自然日)")
    print(f"  放量收盘:  {d.get('surge_close', 0):.2f}")
    print(f"  回踩日期:  {d.get('retrace_date')} (自然日)")
    print(f"  回踩收盘:  {d.get('retrace_close', 0):.2f}")
    print(f"  回踩最低:  {d.get('retrace_low', 0):.2f}")
    print(f"  支撑位:    {d.get('support_level', 0):.2f}")
    print(f"  止损位:    {d.get('support_level', 0) * 0.97:.2f}")
    print(f"  评分:      {signal.score:.2f}")

    # 对比旧版推送
    print(f"\n--- 对比旧版推送 (2026-05-08) ---")
    old_support = 3.89
    old_stop = 3.77
    new_support = d.get('support_level', 0)
    new_stop = new_support * 0.97
    print(f"  支撑位: 旧版 {old_support} vs 新版 {new_support:.2f} -> {'一致' if abs(old_support - new_support) < 0.02 else '有差异'}")
    print(f"  止损位: 旧版 {old_stop} vs 新版 {new_stop:.2f} -> {'一致' if abs(old_stop - new_stop) < 0.02 else '有差异'}")

    retrace_date = d.get('retrace_date')
    if isinstance(retrace_date, pd.Timestamp):
        print(f"  回踩日期: {retrace_date.strftime('%Y-%m-%d')} (旧版 2026-05-07)")

# 额外：尝试用 5月7日截止的数据再检一次
print("\n" + "=" * 70)
print("额外验证：只用 2026-05-07 之前的数据")
print("=" * 70)

df_507 = df[df["date"] <= pd.Timestamp("2026-05-07")].copy()
print(f"数据: {len(df_507)} 条, 截止 {df_507['date'].iloc[-1].strftime('%Y-%m-%d')}")

signal_507 = strategy.analyze("600020", {"d": df_507})
if signal_507:
    d2 = signal_507.detail
    print(f"✅ 5月7日视角也匹配成功！")
    print(f"  支撑位: {d2.get('support_level', 0):.2f} (旧版: 3.89)")
    print(f"  回踩日期: {d2.get('retrace_date')}")
else:
    print("❌ 5月7日视角也不匹配")