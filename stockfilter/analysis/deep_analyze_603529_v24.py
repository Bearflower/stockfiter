#!/usr/bin/env python3
"""
深度分析 603529 爱玛科技在 V2.4 下为何没有检测到形态
时间范围：2025-08-25 到 2026-03-30
"""

import pandas as pd
import baostock as bs
from datetime import datetime

def analyze_603529_v24():
    print("=" * 100)
    print("🔍 603529 爱玛科技 V2.4 形态深度分析")
    print("时间范围：2025-08-25 到 2026-03-30")
    print("=" * 100)
    
    # 获取数据
    print("\n1️⃣  获取数据...")
    lg = bs.login()
    rs = bs.query_history_k_data_plus(
        "sh.603529",
        "date,open,high,low,close,volume,amount",
        start_date="2025-08-01",
        end_date="2026-03-30",
        frequency="d",
        adjustflag="2"  # 后复权
    )
    
    data_list = []
    while (rs.error_code == '0') & rs.next():
        data_list.append(rs.get_row_data())
    
    df = pd.DataFrame(data_list, columns=rs.fields)
    bs.logout()
    
    # 数据预处理
    df['date'] = pd.to_datetime(df['date'])
    df = df.sort_values('date')
    
    numeric_cols = ['open', 'high', 'low', 'close', 'volume', 'amount']
    for col in numeric_cols:
        df[col] = pd.to_numeric(df[col], errors='coerce')
    
    print(f"   数据条数：{len(df)}")
    print(f"   日期范围：{df['date'].min().date()} 到 {df['date'].max().date()}")
    print(f"   最新收盘价：{df['close'].iloc[-1]:.2f}")
    
    # V2.4 参数
    print("\n2️⃣  V2.4 参数要求：")
    print(f"   - 跌幅阈值：≥8%")
    print(f"   - 放量涨幅：≥3%")
    print(f"   - 量比要求：1.2-15 倍")
    print(f"   - 缩量要求：≤80% (低于 20 日均量的 80%)")
    print(f"   - 时间窗口：缩量到放量≤60 天")
    print(f"   - 回踩确认：5 天内不破支撑")
    
    # 步骤 1: 检查大跌
    print("\n3️⃣  步骤 1: 检查大跌 (>8%)")
    print("=" * 100)
    df['max_close_30'] = df['close'].rolling(window=30, min_periods=1).max()
    df['drop_from_max'] = (df['max_close_30'] - df['close']) / df['max_close_30']
    
    big_drops = df[df['drop_from_max'] >= 0.08]
    if len(big_drops) > 0:
        print(f"   ✅ 找到 {len(big_drops)} 个交易日跌幅≥8%")
        print(f"   最大跌幅：{df['drop_from_max'].max():.2%} (日期：{df.loc[df['drop_from_max'].idxmax(), 'date'].date()})")
        
        # 显示前 5 次大跌
        print("\n   前 5 次大跌：")
        for idx, row in big_drops.nlargest(5, 'drop_from_max').iterrows():
            print(f"   - {row['date'].date()}: 跌幅 {row['drop_from_max']:.2%}")
    else:
        print(f"   ❌ 没有找到跌幅≥8% 的交易日")
    
    # 步骤 2: 检查放量上涨
    print("\n4️⃣  步骤 2: 检查放量上涨 (涨幅≥3% 且量比≥1.2)")
    print("=" * 100)
    df['avg_volume_20'] = df['volume'].rolling(window=20, min_periods=1).mean()
    df['volume_ratio'] = df['volume'] / df['avg_volume_20']
    df['daily_return'] = df['close'].pct_change()
    
    surges = df[(df['daily_return'] >= 0.03) & (df['volume_ratio'] >= 1.2) & (df['volume_ratio'] <= 15)]
    if len(surges) > 0:
        print(f"   ✅ 找到 {len(surges)} 个放量上涨交易日")
        print(f"   最大单日涨幅：{df['daily_return'].max():.2%} (日期：{df.loc[df['daily_return'].idxmax(), 'date'].date()})")
        
        # 显示最近 5 次
        print("\n   最近 5 次放量上涨：")
        for idx, row in surges.tail(5).iterrows():
            print(f"   - {row['date'].date()}: 涨幅 {row['daily_return']:.2%}, 量比 {row['volume_ratio']:.2f}x")
    else:
        print(f"   ❌ 没有找到放量上涨的交易日")
    
    # 步骤 3: 检查缩量
    print("\n5️⃣  步骤 3: 检查缩量 (成交量≤20 日均量的 80%)")
    print("=" * 100)
    shrink_days = df[df['volume'] <= df['avg_volume_20'] * 0.8]
    if len(shrink_days) > 0:
        print(f"   ✅ 找到 {len(shrink_days)} 个缩量交易日")
        
        # 显示最近 5 次
        print("\n   最近 5 次缩量：")
        for idx, row in shrink_days.tail(5).iterrows():
            vol_ratio = row['volume'] / row['avg_volume_20']
            print(f"   - {row['date'].date()}: 成交量 {vol_ratio:.2%} of 20 日均量")
    else:
        print(f"   ❌ 没有找到缩量的交易日")
    
    # 步骤 4: 检查时间窗口（缩量到放量）
    print("\n6️⃣  步骤 4: 检查时间窗口（缩量→放量，≤60 天）")
    print("=" * 100)
    
    # 寻找缩量后 60 天内有放量上涨的组合
    valid_combinations = []
    for shrink_idx in shrink_days.index:
        shrink_date = shrink_days.loc[shrink_idx, 'date']
        
        # 查找之后 60 天内的放量上涨
        future_surges = surges[(surges['date'] > shrink_date) & 
                               (surges['date'] <= shrink_date + pd.Timedelta(days=60))]
        
        if len(future_surges) > 0:
            for surge_idx in future_surges.index:
                surge_date = surges.loc[surge_idx, 'date']
                days_between = (surge_date - shrink_date).days
                valid_combinations.append({
                    '缩量日期': shrink_date,
                    '放量日期': surge_date,
                    '间隔天数': days_between,
                    '放量涨幅': future_surges.loc[surge_idx, 'daily_return'],
                    '放量量比': future_surges.loc[surge_idx, 'volume_ratio']
                })
    
    if len(valid_combinations) > 0:
        print(f"   ✅ 找到 {len(valid_combinations)} 组有效的 缩量→放量 组合")
        
        # 显示前 5 组
        print("\n   前 5 组组合：")
        for combo in valid_combinations[:5]:
            print(f"   - 缩量：{combo['缩量日期'].date()}, 放量：{combo['放量日期'].date()}, "
                  f"间隔：{combo['间隔天数']}天，涨幅：{combo['放量涨幅']:.2%}, 量比：{combo['放量量比']:.2f}x")
    else:
        print(f"   ❌ 没有找到 60 天内的缩量→放量组合")
    
    # 步骤 5: 检查回踩确认
    print("\n7️⃣  步骤 5: 检查回踩确认")
    print("=" * 100)
    
    if len(valid_combinations) > 0:
        # 检查最后一组组合的回踩情况
        last_combo = valid_combinations[-1]
        surge_date = last_combo['放量日期']
        surge_idx = df[df['date'] == surge_date].index[0]
        surge_close = df.loc[surge_idx, 'close']
        
        print(f"   检查最后一组组合的回踩情况：")
        print(f"   放量日期：{surge_date.date()}")
        print(f"   放量收盘价：{surge_close:.2f}")
        
        # 获取放量后 5 天的数据
        future_5days = df[(df['date'] > surge_date) & (df['date'] <= surge_date + pd.Timedelta(days=10))]
        
        if len(future_5days) > 0:
            min_price = future_5days['low'].min()
            min_price_date = future_5days.loc[future_5days['low'].idxmin(), 'date']
            price_ratio = min_price / surge_close
            
            print(f"   放量后 5 天最低价：{min_price:.2f} (日期：{min_price_date.date()})")
            print(f"   最低价/放量价：{price_ratio:.2%}")
            print(f"   V2.4 要求：≥97%")
            
            if price_ratio >= 0.97:
                print(f"   ✅ 回踩不破支撑，满足 V2.4 要求！")
            else:
                print(f"   ❌ 回踩跌破支撑 ({(1-price_ratio):.2%} < 3%)")
        else:
            print(f"   ⚠️  放量后数据不足，可能还未完成回踩确认")
    
    # 总结
    print("\n" + "=" * 100)
    print("📊 总结")
    print("=" * 100)
    
    issues = []
    if len(big_drops) == 0:
        issues.append("❌ 缺少大跌（≥8%）")
    else:
        print("✅ 有大跌")
    
    if len(surges) == 0:
        issues.append("❌ 缺少放量上涨（≥3% 且量比 1.2-15）")
    else:
        print("✅ 有放量上涨")
    
    if len(shrink_days) == 0:
        issues.append("❌ 缺少缩量（≤80%）")
    else:
        print("✅ 有缩量")
    
    if len(valid_combinations) == 0:
        issues.append("❌ 缩量到放量时间间隔>60 天")
    else:
        print(f"✅ 有缩量→放量组合（共{len(valid_combinations)}组）")
    
    if len(valid_combinations) > 0:
        last_combo = valid_combinations[-1]
        surge_date = last_combo['放量日期']
        surge_idx = df[df['date'] == surge_date].index[0]
        surge_close = df.loc[surge_idx, 'close']
        future_5days = df[(df['date'] > surge_date) & (df['date'] <= surge_date + pd.Timedelta(days=10))]
        
        if len(future_5days) > 0:
            min_price = future_5days['low'].min()
            price_ratio = min_price / surge_close
            
            if price_ratio < 0.97:
                issues.append(f"❌ 回踩跌破支撑 ({(1-price_ratio):.2%} < 3%)")
            else:
                print("✅ 回踩确认完成")
        else:
            issues.append("⚠️  回踩确认未完成（数据不足）")
    
    print("\n" + "=" * 100)
    if issues:
        print("❌ 603529 在指定期间未满足 V2.4 形态，原因：")
        for issue in issues:
            print(f"   {issue}")
    else:
        print("✅ 603529 在指定期间满足 V2.4 形态！")
    
    print("=" * 100)

if __name__ == "__main__":
    analyze_603529_v24()
