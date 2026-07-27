#!/usr/bin/env python3
"""
复盘：验证 600020 中原高速在 2026-05-07 的 OBPC 信号
逐步骤打印诊断信息，对比旧版推送结论
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timedelta
import pandas as pd
import akshare as ak

code = "600020"

# 用 5月7日 为截止日重新获取数据
end_date = "2026-05-07"
end_fmt = "20260507"
start_fmt = "20251101"

print("=" * 70)
print(f"复盘：{code} 中原高速 OBPC 策略回溯（截止 {end_date}）")
print("=" * 70)

# 获取数据
df = ak.stock_zh_a_hist(
    symbol=code, period="daily",
    start_date=start_fmt, end_date=end_fmt, adjust="qfq",
)
df = df.rename(columns={
    "日期": "date", "开盘": "open", "最高": "high",
    "最低": "low", "收盘": "close", "成交量": "volume", "成交额": "amount",
})
for col in ["open", "high", "low", "close", "volume", "amount"]:
    df[col] = pd.to_numeric(df[col], errors="coerce")
df["date"] = pd.to_datetime(df["date"])
df = df.sort_values("date").reset_index(drop=True)

print(f"数据: {len(df)} 条, {df['date'].iloc[0].strftime('%Y-%m-%d')} ~ {df['date'].iloc[-1].strftime('%Y-%m-%d')}")
print(f"字段: {list(df.columns)}")
print(f"amount 示例: {df['amount'].head(3).tolist()}")

# 策略参数（和 config.yaml 一致）
DROP_THRESHOLD = 0.08
DROP_WINDOW = 20
VOLUME_SHRINK_RATIO = 0.8
SHRINK_TO_SURGE_DAYS = 60
MIN_VOLUME_RATIO = 1.2
MAX_VOLUME_RATIO = 15.0
SURGE_PRICE_RATIO = 0.03
FLAT_DAYS = 0
POST_SURGE_CHECK_DAYS = 5
POST_SURGE_MAX_DROP = 0.97
RETRACE_MAX_DAYS = 10
SUPPORT_RATIO = 0.98
MIN_AVG_AMOUNT = 20_000_000
VOLUME_CHECK_PERIOD = 20

# ====== 步骤1：大跌检测 ======
print("\n" + "-" * 50)
print("[步骤1] 大跌检测")
drop_found, drop_start_idx, drop_end_idx = False, -1, -1
for i in range(DROP_WINDOW, len(df)):
    window = df.iloc[i - DROP_WINDOW:i + 1]
    high = window["high"].max()
    low = window["low"].min()
    drop = (high - low) / high
    if drop >= DROP_THRESHOLD:
        drop_found = True
        drop_start_idx = window[window["high"] == high].index[0]
        drop_end_idx = i
        print(f"  OK 大跌: {df['date'].iloc[drop_start_idx].strftime('%Y-%m-%d')} ~ "
              f"{df['date'].iloc[drop_end_idx].strftime('%Y-%m-%d')}")
        print(f"     最高 {high:.2f} -> 最低 {low:.2f}, 跌幅 {drop*100:.2f}%")
        print(f"     大跌终点 index={drop_end_idx}, date={df['date'].iloc[drop_end_idx].strftime('%Y-%m-%d')}")
        break
if not drop_found:
    print("  FAIL - 无大跌")
    sys.exit(0)

# ====== 步骤2：缩量检测 ======
print("\n" + "-" * 50)
print("[步骤2] 缩量检测（大跌后 60 日内找量缩至 20 日均量 80% 以下）")
shrink_found, shrink_idx = False, -1
search_end = min(drop_end_idx + SHRINK_TO_SURGE_DAYS, len(df))
print(f"  搜索范围: index {drop_end_idx+1}~{search_end-1} "
      f"({df['date'].iloc[drop_end_idx+1].strftime('%Y-%m-%d')} ~ "
      f"{df['date'].iloc[search_end-1].strftime('%Y-%m-%d')})")

for i in range(drop_end_idx + 1, search_end):
    vol_i = df["volume"].iloc[i]
    vol_avg_20 = df["volume"].iloc[max(0, i - 20):i].mean()
    ratio = vol_i / vol_avg_20 if vol_avg_20 > 0 else 0
    if vol_i <= vol_avg_20 * VOLUME_SHRINK_RATIO:
        shrink_found = True
        shrink_idx = i
        print(f"  OK 缩量: {df['date'].iloc[i].strftime('%Y-%m-%d')} "
              f"量={vol_i:.0f}, 20日均={vol_avg_20:.0f}, 比率={ratio:.2f}")
        break

if not shrink_found:
    # 打印最接近缩量的几天
    print("  FAIL - 未找到缩量。最近 5 天的量比:")
    for i in range(search_end - 5, search_end):
        if i < 0:
            continue
        vol_i = df["volume"].iloc[i]
        vol_avg_20 = df["volume"].iloc[max(0, i - 20):i].mean()
        ratio = vol_i / vol_avg_20 if vol_avg_20 > 0 else 0
        print(f"    {df['date'].iloc[i].strftime('%Y-%m-%d')}: "
              f"量={vol_i:.0f}, 20日均={vol_avg_20:.0f}, 比率={ratio:.2f}")
    sys.exit(0)

# ====== 步骤3：放量检测 ======
print("\n" + "-" * 50)
print("[步骤3] 放量检测（缩量后 15 日内找量比 >=1.2 且涨幅 >=3% 的放量大涨）")
surge_found, surge_idx = False, -1
for j in range(shrink_idx + 1, min(shrink_idx + 15, len(df))):
    vol_j = df["volume"].iloc[j]
    vol_prev = df["volume"].iloc[j - 1]
    close_j = df["close"].iloc[j]
    close_prev = df["close"].iloc[j - 1]
    vol_ratio = vol_j / vol_prev if vol_prev > 0 else 0
    price_change = (close_j - close_prev) / close_prev

    if not (vol_ratio >= MIN_VOLUME_RATIO and price_change >= SURGE_PRICE_RATIO):
        continue
    if not (MIN_VOLUME_RATIO <= vol_ratio <= MAX_VOLUME_RATIO):
        continue

    # 方案B：检查启动后 5 日内不跌破 0.97 * 收盘价
    support_level_b = close_j * POST_SURGE_MAX_DROP
    is_valid = True
    for k in range(j + 1, min(j + 1 + POST_SURGE_CHECK_DAYS, len(df))):
        low_k = df["low"].iloc[k]
        if low_k < support_level_b:
            is_valid = False
            break
    if not is_valid:
        continue

    surge_found = True
    surge_idx = j
    print(f"  OK 放量: {df['date'].iloc[j].strftime('%Y-%m-%d')} "
          f"量比={vol_ratio:.2f}x, 涨幅={price_change*100:.2f}%, "
          f"收盘={close_j:.2f}")
    print(f"     方案B 支撑水位={support_level_b:.2f}, 5日内检查通过")
    break

if not surge_found:
    print("  FAIL - 未找到放量信号")
    sys.exit(0)

# ====== 步骤4：回踩确认 ======
print("\n" + "-" * 50)
print("[步骤4] 回踩确认（放量后 10 日内低点 >= 支撑位 × 0.98）")
support_level = df["low"].iloc[surge_idx]
print(f"  支撑位 = 放量日最低价 = {support_level:.2f}")
print(f"  回踩门槛 = {support_level:.2f} × {SUPPORT_RATIO} = {support_level * SUPPORT_RATIO:.2f}")

retrace_found, retrace_idx = False, -1
search_retrace_end = min(surge_idx + RETRACE_MAX_DAYS, len(df))
print(f"  搜索范围: index {surge_idx+1}~{search_retrace_end-1} "
      f"({df['date'].iloc[surge_idx+1].strftime('%Y-%m-%d')} ~ "
      f"{df['date'].iloc[search_retrace_end-1].strftime('%Y-%m-%d')})")

for i in range(surge_idx + 1, search_retrace_end):
    low_i = df["low"].iloc[i]
    close_i = df["close"].iloc[i]
    ok = low_i >= support_level * SUPPORT_RATIO
    print(f"    {df['date'].iloc[i].strftime('%Y-%m-%d')}: "
          f"low={low_i:.2f}, close={close_i:.2f}, "
          f"low >= {support_level * SUPPORT_RATIO:.2f}? {'OK' if ok else 'NO'}")
    if ok:
        retrace_found = True
        retrace_idx = i
        print(f"  OK 回踩确认: {df['date'].iloc[i].strftime('%Y-%m-%d')} low={low_i:.2f}")
        break

if not retrace_found:
    print("  FAIL - 未找到回踩确认")
    sys.exit(0)

# ====== 步骤5：流动性 ======
print("\n" + "-" * 50)
print("[步骤5] 流动性检查")
period = VOLUME_CHECK_PERIOD
start_i = retrace_idx - period
end_i = retrace_idx
recent = df.iloc[start_i:end_i].copy()

# 用 amount 字段
if "amount" in recent.columns:
    avg_amount = recent["amount"].mean()
    print(f"  使用 amount 字段")
else:
    avg_amount = (recent["volume"] * recent["close"]).mean()
    print(f"  使用 volume*close 计算")

print(f"  回踩 index={retrace_idx}, 日期={df['date'].iloc[retrace_idx].strftime('%Y-%m-%d')}")
print(f"  20日范围: {df['date'].iloc[start_i].strftime('%Y-%m-%d')} ~ {df['date'].iloc[end_i-1].strftime('%Y-%m-%d')}")
print(f"  20日均成交额: {avg_amount:,.0f} 元")
print(f"  阈值: {MIN_AVG_AMOUNT:,} 元")
print(f"  {'OK 满足' if avg_amount >= MIN_AVG_AMOUNT else 'NO 不满足'}")

# ====== 总结 ======
print("\n" + "=" * 70)
print("总结")
print("=" * 70)
print(f"  {'✅' if drop_found else '❌'} 步骤1 大跌: {'通过' if drop_found else '失败'}")
print(f"  {'✅' if shrink_found else '❌'} 步骤2 缩量: {'通过' if shrink_found else '失败'}")
print(f"  {'✅' if surge_found else '❌'} 步骤3 放量: {'通过' if surge_found else '失败'}")
print(f"  {'✅' if retrace_found else '❌'} 步骤4 回踩: {'通过' if retrace_found else '失败'}")
print(f"  {'✅' if avg_amount >= MIN_AVG_AMOUNT else '❌'} 步骤5 流动性: "
      f"{'通过' if avg_amount >= MIN_AVG_AMOUNT else '失败'}")

signal_match = all([drop_found, shrink_found, surge_found, retrace_found, avg_amount >= MIN_AVG_AMOUNT])
print(f"\n  最终结论: {'✅ 匹配 OBPC 策略' if signal_match else '❌ 不匹配'}")

if signal_match:
    print(f"  支撑位: {support_level:.2f}（对比旧版推送: 3.89）")
    print(f"  止损位: {support_level * 0.97:.2f}（对比旧版推送: 3.77）")
    print(f"  回踩日期: {df['date'].iloc[retrace_idx].strftime('%Y-%m-%d')}（对比旧版推送: 2026-05-07）")