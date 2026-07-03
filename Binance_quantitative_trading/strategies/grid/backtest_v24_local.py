#!/usr/bin/env python3
"""V2.4 策略回测 — 基于本地 ETHUSDT K线数据（三层预警架构）"""
import json
import sys
import os
from datetime import datetime, timedelta
from collections import defaultdict
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ============================================================
# 加载本地数据（同源数据：与 V2.3 回测使用相同的数据文件）
# ============================================================
with open("data/ethusdt/ethusdt_1h.json") as f:
    raw_1h = json.load(f)
with open("data/ethusdt/ethusdt_4h.json") as f:
    raw_4h = json.load(f)
with open("data/ethusdt/ethusdt_15m.json") as f:
    raw_15m = json.load(f)

def build_klines(raw):
    """K线格式: [openTime, open, high, low, close, volume, ...]"""
    klines = {}
    for k in raw:
        ts = k[0]
        klines[ts] = {"open": float(k[1]), "high": float(k[2]), "low": float(k[3]), "close": float(k[4]), "volume": float(k[5])}
    return klines

klines_1h = build_klines(raw_1h)
klines_4h = build_klines(raw_4h)
klines_15m = build_klines(raw_15m)

sorted_1h = sorted(klines_1h.keys())
sorted_4h = sorted(klines_4h.keys())
sorted_15m = sorted(klines_15m.keys())

print(f"1h 数据: {len(sorted_1h)} 根, {datetime.fromtimestamp(sorted_1h[0]/1000)} ~ {datetime.fromtimestamp(sorted_1h[-1]/1000)}")
print(f"4h 数据: {len(sorted_4h)} 根, {datetime.fromtimestamp(sorted_4h[0]/1000)} ~ {datetime.fromtimestamp(sorted_4h[-1]/1000)}")
print(f"15m 数据: {len(sorted_15m)} 根, {datetime.fromtimestamp(sorted_15m[0]/1000)} ~ {datetime.fromtimestamp(sorted_15m[-1]/1000)}")

# ============================================================
# 指标计算函数
# ============================================================
def calc_ema(data, period):
    if len(data) < period:
        return None
    multiplier = Decimal('2') / Decimal(str(period + 1))
    ema = Decimal(str(data[0]))
    for i in range(1, len(data)):
        ema = (Decimal(str(data[i])) - ema) * multiplier + ema
    return float(ema)

def calc_atr(highs, lows, closes, period=14):
    if len(highs) < period + 1:
        return None
    trs = []
    for i in range(1, len(highs)):
        h, l, pc = highs[i], lows[i], closes[i-1]
        tr = max(h - l, abs(h - pc), abs(l - pc))
        trs.append(tr)
    return sum(trs[-period:]) / period

def calc_adx(highs, lows, closes, period=14):
    """简化 ADX 计算"""
    if len(highs) < period * 2:
        return None
    trs = []
    plus_dms = []
    minus_dms = []
    for i in range(1, len(highs)):
        h, l, ph, pl = highs[i], lows[i], highs[i-1], lows[i-1]
        tr = max(h - l, abs(h - ph), abs(l - pl))
        trs.append(tr)
        up_move = h - ph
        down_move = pl - l
        plus_dm = up_move if up_move > down_move and up_move > 0 else 0
        minus_dm = down_move if down_move > up_move and down_move > 0 else 0
        plus_dms.append(plus_dm)
        minus_dms.append(minus_dm)

    # Wilder's smoothing
    atr_val = sum(trs[:period]) / period
    plus_di = sum(plus_dms[:period]) / period
    minus_di = sum(minus_dms[:period]) / period

    for i in range(period, len(trs)):
        atr_val = (atr_val * (period - 1) + trs[i]) / period
        plus_di = (plus_di * (period - 1) + plus_dms[i]) / period
        minus_di = (minus_di * (period - 1) + minus_dms[i]) / period

    if atr_val == 0:
        return 0

    plus_di_val = (plus_di / atr_val) * 100
    minus_di_val = (minus_di / atr_val) * 100
    dx = abs(plus_di_val - minus_di_val) / (plus_di_val + minus_di_val) * 100 if (plus_di_val + minus_di_val) > 0 else 0

    dxs = []
    for i in range(period, len(trs)):
        atr_i = sum(trs[i-period:i]) / period
        pdi = sum(plus_dms[i-period:i]) / period
        mdi = sum(minus_dms[i-period:i]) / period
        if atr_i == 0:
            dxs.append(0)
            continue
        pdi_v = (pdi / atr_i) * 100
        mdi_v = (mdi / atr_i) * 100
        if (pdi_v + mdi_v) == 0:
            dxs.append(0)
        else:
            dxs.append(abs(pdi_v - mdi_v) / (pdi_v + mdi_v) * 100)

    if len(dxs) < period:
        return dxs[-1] if dxs else 0

    return sum(dxs[-period:]) / period

def calc_price_change(klines_sorted, current_idx, klines_dict):
    """计算价格变动率：当前K线 vs 前一根K线"""
    if current_idx < 1:
        return 0
    curr_ts = klines_sorted[current_idx]
    prev_ts = klines_sorted[current_idx - 1]
    curr_close = klines_dict[curr_ts]['close']
    prev_close = klines_dict[prev_ts]['close']
    if prev_close == 0:
        return 0
    return abs(curr_close - prev_close) / prev_close

# ============================================================
# V2.4 参数（与 config.yaml 一致）
# ============================================================
# 三层预警
PRICE_EMERGENCY_1H = 0.03      # 第1层：1h变动≥3%
PRICE_EMERGENCY_15M = 0.015    # 第1层：15m变动≥1.5%
ADX_EARLY_WARNING_15M = 50     # 第2层：15m ADX≥50
PRICE_EARLY_WARNING_1H = 0.01  # 第2层：需1h变动≥1%
EMERGENCY_ADX = 55             # 第3层：1h ADX(10)≥55
TREND_ACCEL_THRESHOLD = 8      # 趋势加速：2h内上升>8
ADX_PERIOD = 10                # V2.4: ADX周期从14缩短为10

# 其他阈值
EXTREME_ADX_1H = 40
EXTREME_ADX_4H = 30
NORMAL_ADX_1H = 30
NORMAL_ADX_4H = 25
WEAK_ADX_LOWER = 25
WEAK_ADX_UPPER = 30
VOL_RATIO = 1.2                # V2.4: 从1.3降至1.2
VOL_RECOVERY = 1.2
ATR_PERIOD = 14
EMA_FAST = 20
EMA_SLOW = 50
RECOVERY_STRONG_1H = 30
RECOVERY_STRONG_4H = 30

# 网格参数
OSCILLATION_BASE_COUNT = 8
OSCILLATION_MIN = 5
OSCILLATION_MAX = 12
OSCILLATION_ATR_MULT = 5
OSCILLATION_STOP_BUFFER = 2
WEAK_BASE_COUNT = 6
WEAK_MIN = 4
WEAK_MAX = 10
WEAK_ATR_MULT = 6
WEAK_STOP_BUFFER = 2.4
WEAK_REDUCTION = 0.8
BASE_ATR = 30

# 冷却时间
COOLDOWN_ALERT_H = 1
COOLDOWN_NORMAL_H = 6
COOLDOWN_TRADABLE_H = 2

# 回测窗口：从第200根开始（留足指标计算数据）
START_IDX = 200

print(f"\n回测窗口: {datetime.fromtimestamp(sorted_1h[START_IDX]/1000)} ~ {datetime.fromtimestamp(sorted_1h[-1]/1000)}")
print(f"有效数据点: {len(sorted_1h) - START_IDX}")

# ============================================================
# 回测主循环
# ============================================================
state_counts = defaultdict(int)
push_log = []
last_push_state = None
last_push_time = None
atr_abnormal_count = 0
atr_peak = None
atr_alarm_active = False
adx_history = []
atr_history = []

atr_smooth_values = []
adx_1h_values = []
adx_4h_values = []
adx_15m_values = []
price_change_1h_values = []
price_change_15m_values = []

# 恢复通知追踪
last_danger_state = None  # 上一个危险状态，用于恢复检测

for i in range(START_IDX, len(sorted_1h)):
    ts = sorted_1h[i]
    k = klines_1h[ts]
    price = k['close']
    dt = datetime.fromtimestamp(ts / 1000)

    # --- 计算1h指标 ---
    close_1h = [klines_1h[t]['close'] for t in sorted_1h[max(0, i-100):i+1]]
    high_1h = [klines_1h[t]['high'] for t in sorted_1h[max(0, i-100):i+1]]
    low_1h = [klines_1h[t]['low'] for t in sorted_1h[max(0, i-100):i+1]]

    # --- 找到最近的4h K线 ---
    four_h_ts = None
    for t in sorted_4h:
        if t <= ts:
            four_h_ts = t
        else:
            break
    if four_h_ts is None:
        continue

    four_h_idx = sorted_4h.index(four_h_ts)
    close_4h = [klines_4h[t]['close'] for t in sorted_4h[max(0, four_h_idx-100):four_h_idx+1]]
    high_4h = [klines_4h[t]['high'] for t in sorted_4h[max(0, four_h_idx-100):four_h_idx+1]]
    low_4h = [klines_4h[t]['low'] for t in sorted_4h[max(0, four_h_idx-100):four_h_idx+1]]

    # --- 找到最近的15m K线 ---
    fifteen_m_ts = None
    for t in sorted_15m:
        if t <= ts:
            fifteen_m_ts = t
        else:
            break
    if fifteen_m_ts is None:
        continue

    fifteen_m_idx = sorted_15m.index(fifteen_m_ts)
    close_15m = [klines_15m[t]['close'] for t in sorted_15m[max(0, fifteen_m_idx-100):fifteen_m_idx+1]]
    high_15m = [klines_15m[t]['high'] for t in sorted_15m[max(0, fifteen_m_idx-100):fifteen_m_idx+1]]
    low_15m = [klines_15m[t]['low'] for t in sorted_15m[max(0, fifteen_m_idx-100):fifteen_m_idx+1]]

    # --- 计算EMA ---
    ema20_1h = calc_ema(close_1h, EMA_FAST)
    ema50_1h = calc_ema(close_1h, EMA_SLOW)
    ema20_4h = calc_ema(close_4h, EMA_FAST) if len(close_4h) >= EMA_FAST else None
    ema50_4h = calc_ema(close_4h, EMA_SLOW) if len(close_4h) >= EMA_SLOW else None

    # --- 计算ADX（V2.4: 使用ADX_PERIOD=10） ---
    adx_1h = calc_adx(high_1h, low_1h, close_1h, ADX_PERIOD)
    adx_4h = calc_adx(high_4h, low_4h, close_4h, ADX_PERIOD) if len(high_4h) >= ADX_PERIOD * 2 else None
    adx_15m = calc_adx(high_15m, low_15m, close_15m, ADX_PERIOD) if len(high_15m) >= ADX_PERIOD * 2 else None

    # --- 计算ATR_smooth ---
    atr_1h = calc_atr(high_1h, low_1h, close_1h, ATR_PERIOD)
    if atr_1h is None:
        continue
    atr_smooth_values.append(atr_1h)
    if len(atr_smooth_values) > ATR_PERIOD:
        atr_smooth = calc_ema(atr_smooth_values[-ATR_PERIOD:], ATR_PERIOD)
    else:
        atr_smooth = atr_1h

    # --- V2.4新增：计算价格变动率 ---
    price_change_1h = calc_price_change(sorted_1h, i, klines_1h)
    price_change_15m = calc_price_change(sorted_15m, fifteen_m_idx, klines_15m)

    if adx_1h is None or adx_4h is None or adx_15m is None or ema20_1h is None or ema50_1h is None or ema20_4h is None or ema50_4h is None:
        continue

    adx_1h_values.append(adx_1h)
    adx_4h_values.append(adx_4h)
    adx_15m_values.append(adx_15m)
    price_change_1h_values.append(price_change_1h)
    price_change_15m_values.append(price_change_15m)

    # --- 方向一致检查 ---
    direction_up = ema20_1h > ema50_1h and ema20_4h > ema50_4h
    direction_down = ema20_1h < ema50_1h and ema20_4h < ema50_4h
    direction_aligned = direction_up or direction_down

    # --- 趋势加速检测 ---
    adx_history.append(adx_1h)
    if len(adx_history) > 3:
        adx_history = adx_history[-3:]

    trend_accelerating = False
    if len(adx_history) >= 3:
        adx_old = adx_history[0]
        adx_rise = adx_1h - adx_old
        trend_accelerating = adx_rise > TREND_ACCEL_THRESHOLD

    # --- 波动率异常检测 ---
    atr_history.append(atr_smooth)
    if len(atr_history) > 5:
        atr_history = atr_history[-5:]

    vol_abnormal = False
    if len(atr_history) >= 5:
        atr_4h_ago = atr_history[0]
        if atr_4h_ago > 0:
            atr_ratio = atr_smooth / atr_4h_ago
            if atr_ratio > VOL_RATIO:
                atr_abnormal_count += 1
            else:
                atr_abnormal_count = 0

            if atr_abnormal_count >= 2 and not atr_alarm_active:
                vol_abnormal = True
                atr_alarm_active = True
                atr_peak = atr_smooth

    if atr_alarm_active and atr_peak and atr_peak > 0:
        if atr_smooth / atr_peak < VOL_RECOVERY:
            atr_alarm_active = False
            atr_abnormal_count = 0

    # ============================================================
    # V2.4 三层预警架构：按优先级判定市场状态
    # ============================================================
    state = None
    confidence = 0
    danger_state = False  # 是否为危险状态

    # 第1层：价格行为紧急触发（V2.4新增，最高优先级，0延迟）
    if price_change_1h >= PRICE_EMERGENCY_1H or price_change_15m >= PRICE_EMERGENCY_15M:
        state = "价格行为紧急触发"
        confidence = 1.0
        danger_state = True
    # 第2层：15m ADX早期预警（V2.4新增）
    elif adx_15m >= ADX_EARLY_WARNING_15M and price_change_1h >= PRICE_EARLY_WARNING_1H:
        state = "15m ADX早期预警"
        confidence = 0.92
        danger_state = True
    # 第3层：1h ADX(10)趋势确认（V2.4新增，ADX周期从14缩短为10）
    elif adx_1h >= EMERGENCY_ADX:
        state = "1h ADX趋势确认"
        confidence = 0.95
        danger_state = True
    # 趋势急剧增强
    elif trend_accelerating:
        state = "趋势急剧增强"
        confidence = 0.9
        danger_state = True
    # 极端强趋势
    elif adx_1h >= EXTREME_ADX_1H and adx_4h >= EXTREME_ADX_4H and direction_aligned:
        state = "极端强趋势"
        confidence = 0.85
        danger_state = True
    # 普通强趋势
    elif adx_1h >= NORMAL_ADX_1H and adx_4h >= NORMAL_ADX_4H and direction_aligned:
        state = "普通强趋势"
        confidence = 0.8
        danger_state = True
    # 波动率异常
    elif atr_alarm_active:
        state = "波动率异常"
        confidence = 0.75
        danger_state = True
    # 弱趋势
    elif WEAK_ADX_LOWER <= adx_1h < WEAK_ADX_UPPER and adx_4h < NORMAL_ADX_4H:
        state = "弱趋势"
        confidence = 0.7
    # 震荡
    elif adx_1h < WEAK_ADX_LOWER and adx_4h < WEAK_ADX_LOWER:
        state = "震荡市场"
        confidence = 0.5
    else:
        state = "震荡市场"
        confidence = 0.5

    state_counts[state] += 1

    # V2.4恢复检测：从危险状态恢复到可交易状态
    dangerous_states = {"价格行为紧急触发", "15m ADX早期预警", "1h ADX趋势确认", "趋势急剧增强", "极端强趋势", "普通强趋势", "波动率异常"}
    is_recovery = False
    if last_danger_state in dangerous_states and state in {"弱趋势", "震荡市场"}:
        is_recovery = True

    if danger_state:
        last_danger_state = state
    elif state in {"弱趋势", "震荡市场"}:
        last_danger_state = state

    # --- 推送逻辑 ---
    if is_recovery:
        push_log.append({"time": dt, "state": state, "trigger": "恢复通知", "1h_adx": round(adx_1h, 1), "4h_adx": round(adx_4h, 1), "15m_adx": round(adx_15m, 1), "price": round(price, 2), "pc_1h": f"{price_change_1h*100:.2f}%", "pc_15m": f"{price_change_15m*100:.2f}%"})
        last_push_state = state
        last_push_time = dt
    elif state != last_push_state:
        push_log.append({"time": dt, "state": state, "trigger": "状态变化", "1h_adx": round(adx_1h, 1), "4h_adx": round(adx_4h, 1), "15m_adx": round(adx_15m, 1), "price": round(price, 2), "pc_1h": f"{price_change_1h*100:.2f}%", "pc_15m": f"{price_change_15m*100:.2f}%"})
        last_push_state = state
        last_push_time = dt
    else:
        if last_push_time:
            hours_since = (dt - last_push_time).total_seconds() / 3600
            if state in ["价格行为紧急触发", "15m ADX早期预警", "1h ADX趋势确认", "趋势急剧增强", "极端强趋势"]:
                cooldown = COOLDOWN_ALERT_H
            elif state in ["普通强趋势", "波动率异常"]:
                cooldown = COOLDOWN_NORMAL_H
            else:
                cooldown = COOLDOWN_TRADABLE_H

            if hours_since >= cooldown:
                push_log.append({"time": dt, "state": state, "trigger": f"冷却期满 ({hours_since:.1f}h)", "1h_adx": round(adx_1h, 1), "4h_adx": round(adx_4h, 1), "15m_adx": round(adx_15m, 1), "price": round(price, 2), "pc_1h": f"{price_change_1h*100:.2f}%", "pc_15m": f"{price_change_15m*100:.2f}%"})
                last_push_time = dt

# ============================================================
# 输出回测结果
# ============================================================
print("\n" + "=" * 70)
print("  V2.4 回测结果（三层预警架构）")
print("=" * 70)
total_hours = sum(state_counts.values())
print(f"\n回测周期: {datetime.fromtimestamp(sorted_1h[START_IDX]/1000)} ~ {datetime.fromtimestamp(sorted_1h[-1]/1000)}")
print(f"有效数据点: {total_hours}")

print(f"\n市场状态分布:")
for st, count in sorted(state_counts.items(), key=lambda x: -x[1]):
    pct = count / total_hours * 100
    bar = "█" * int(pct / 2)
    print(f"  {st:14s} {count:5d} 小时 ({pct:5.1f}%) {bar}")

print(f"\n推送统计:")
print(f"  总推送次数: {len(push_log)}")
print(f"  平均推送间隔: {total_hours / len(push_log):.1f} 小时" if push_log else "  无推送")

push_by_state = defaultdict(int)
for p in push_log:
    push_by_state[p["state"]] += 1
print(f"\n  按状态推送:")
for st, count in sorted(push_by_state.items(), key=lambda x: -x[1]):
    print(f"    {st}: {count} 次")

# 恢复通知统计
recovery_count = sum(1 for p in push_log if p["trigger"] == "恢复通知")
print(f"\n  恢复通知: {recovery_count} 次")

print(f"\n最近20次推送:")
for p in push_log[-20:]:
    print(f"  {p['time']} | {p['state']:12s} | 1hADX={p['1h_adx']:5.1f} | 4hADX={p['4h_adx']:5.1f} | 15mADX={p['15m_adx']:5.1f} | 价格={p['price']:.2f} | 1h变动={p['pc_1h']:>7s} | 15m变动={p['pc_15m']:>7s} | {p['trigger']}")

# ADX 统计
print(f"\n1h ADX 统计 (周期={ADX_PERIOD}):")
print(f"  均值: {sum(adx_1h_values)/len(adx_1h_values):.1f}")
print(f"  最大: {max(adx_1h_values):.1f}")
print(f"  最小: {min(adx_1h_values):.1f}")
print(f"4h ADX 统计:")
print(f"  均值: {sum(adx_4h_values)/len(adx_4h_values):.1f}")
print(f"  最大: {max(adx_4h_values):.1f}")
print(f"  最小: {min(adx_4h_values):.1f}")
print(f"15m ADX 统计:")
print(f"  均值: {sum(adx_15m_values)/len(adx_15m_values):.1f}")
print(f"  最大: {max(adx_15m_values):.1f}")
print(f"  最小: {min(adx_15m_values):.1f}")

# 价格变动率统计
print(f"\n价格变动率统计:")
print(f"  1h变动率 均值: {sum(price_change_1h_values)/len(price_change_1h_values)*100:.2f}%")
print(f"  1h变动率 最大: {max(price_change_1h_values)*100:.2f}%")
print(f"  15m变动率 均值: {sum(price_change_15m_values)/len(price_change_15m_values)*100:.2f}%")
print(f"  15m变动率 最大: {max(price_change_15m_values)*100:.2f}%")

# V2.4 三层预警触发统计
print(f"\nV2.4 三层预警触发统计:")
price_emergency_count = state_counts.get("价格行为紧急触发", 0)
early_warning_count = state_counts.get("15m ADX早期预警", 0)
trend_confirmed_count = state_counts.get("1h ADX趋势确认", 0)
print(f"  第1层-价格行为紧急触发: {price_emergency_count} 小时 ({price_emergency_count/total_hours*100:.1f}%)")
print(f"  第2层-15m ADX早期预警: {early_warning_count} 小时 ({early_warning_count/total_hours*100:.1f}%)")
print(f"  第3层-1h ADX趋势确认: {trend_confirmed_count} 小时 ({trend_confirmed_count/total_hours*100:.1f}%)")

print(f"\n回测完成!")