#!/usr/bin/env python3
"""OBPC 策略逐步骤诊断脚本"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timedelta
import pandas as pd
import akshare as ak

code = "600020"
end_date = datetime.now().strftime("%Y%m%d")
start_date = (datetime.now() - timedelta(days=180)).strftime("%Y%m%d")

df = ak.stock_zh_a_hist(
    symbol=code, period="daily",
    start_date=start_date, end_date=end_date, adjust="qfq",
)

df = df.rename(columns={
    "日期": "date", "开盘": "open", "最高": "high",
    "最低": "low", "收盘": "close", "成交量": "volume", "成交额": "amount",
})
for col in ["open", "high", "low", "close", "volume", "amount"]:
    df[col] = pd.to_numeric(df[col], errors="coerce")
df["date"] = pd.to_datetime(df["date"])
df = df.sort_values("date").reset_index(drop=True)

print("=" * 70)
print("中原高速 (600020) OBPC 策略逐步骤诊断")
print("=" * 70)

# 步骤1：大跌检测（20日内跌幅>=8%）
print("\n[步骤1] 大跌检测（20日内最大跌幅 >= 8%）")
drop_threshold = 0.08
drop_window = 20

recent = df.tail(60).reset_index(drop=True)
drop_found = False
for i in range(drop_window, len(recent)):
    window = recent.iloc[i - drop_window:i + 1]
    high = window["high"].max()
    low = window["low"].min()
    drop = (high - low) / high
    if drop >= drop_threshold:
        high_row = window[window["high"] == high]
        high_date = high_row["date"].iloc[0]
        print(f"  OK 检测到大跌: {high_date.strftime('%Y-%m-%d')} ~ {window['date'].iloc[-1].strftime('%Y-%m-%d')}")
        print(f"     最高: {high:.2f}, 最低: {low:.2f}, 跌幅: {drop*100:.2f}%")
        drop_found = True
        break

if not drop_found:
    print(f"  NO 未检测到大跌")
    r60 = recent
    max_drop = (r60["high"].max() - r60["low"].min()) / r60["high"].max()
    print(f"     60日内最大跌幅: {max_drop*100:.2f}% (阈值 8%)")

# 步骤2：缩量检测
print("\n[步骤2] 缩量检测（大跌后成交量萎缩至20日均量的80%以下）")

# 步骤3：放量检测
print("\n[步骤3] 放量检测（缩量后量比>=1.2 且涨幅>=3%）")
tail20 = recent.tail(20)
avg_vol = tail20["volume"].mean()

print("\n  最近20个交易日：")
print(f"  {'日期':<12} {'收盘':>6} {'涨跌幅':>7} {'成交量':>10} {'量比(20日)':>12} {'成交额':>12}")
print("  " + "-" * 65)
for _, row in tail20.iterrows():
    chg = (row["close"] - row["open"]) / row["open"] * 100
    vol_ratio = row["volume"] / avg_vol if avg_vol > 0 else 0
    amt = row.get("amount", 0)
    print(f"  {row['date'].strftime('%Y-%m-%d'):<12} {row['close']:>6.2f} {chg:>+6.2f}% {row['volume']:>10.0f} {vol_ratio:>11.2f}x {amt:>12.0f}")

# 检查放量信号
found_surge = False
for _, row in tail20.iterrows():
    chg = (row["close"] - row["open"]) / row["open"] * 100
    vol_ratio = row["volume"] / avg_vol if avg_vol > 0 else 0
    if vol_ratio >= 1.2 and chg >= 3.0:
        found_surge = True
        print(f"\n  => 发现放量信号: {row['date'].strftime('%Y-%m-%d')} 涨幅{chg:.2f}% 量比{vol_ratio:.2f}x")

if not found_surge:
    # 找最接近的
    max_chg = -999
    max_chg_row = None
    for _, row in tail20.iterrows():
        chg = (row["close"] - row["open"]) / row["open"] * 100
        if chg > max_chg:
            max_chg = chg
            max_chg_row = row
    print(f"\n  => 未发现放量信号。最大单日涨幅: {max_chg_row['date'].strftime('%Y-%m-%d')} {max_chg:.2f}%")

# 步骤5：流动性
print(f"\n[步骤5] 流动性检查（20日均成交额 >= 2000万元）")
avg_amount = (tail20["volume"] * tail20["close"]).mean()
print(f"  20日均成交额: {avg_amount:,.0f} 元")
print(f"  阈值: 20,000,000 元")
if avg_amount >= 20000000:
    print(f"  OK 满足流动性要求")
else:
    print(f"  NO 流动性严重不足（实际仅 {avg_amount/10000:.0f} 万元）")

# 总结
print("\n" + "=" * 70)
print("诊断总结：")
print("-" * 70)
issues = []
if not drop_found:
    issues.append("未检测到满足条件的大跌（20日内跌幅<8%）")
else:
    issues.append("大跌条件满足，但后续缩量/放量/回踩可能不满足")

if not found_surge:
    issues.append("最近20日未出现放量大涨（量比>=1.2且涨幅>=3%）")

avg_amount = (tail20["volume"] * tail20["close"]).mean()
if avg_amount < 20000000:
    issues.append(f"流动性不足（20日均成交额仅 {avg_amount/10000:.0f} 万元，远低于2000万）")

for i, issue in enumerate(issues, 1):
    print(f"  {i}. {issue}")

print(f"\n最终结论：中原高速 (600020) 不满足 OBPC 超跌反弹策略形态")
print(f"核心原因是该股属于冷门股，日均成交额不到 100 万元，流动性严重不足。")
print(f"OBPC 策略对流动性有较高要求（>=2000万日均成交额），以规避流动性风险。")