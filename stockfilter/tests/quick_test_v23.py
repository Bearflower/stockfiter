#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V2.3 快速测试（前100只股票）
- 测试放宽条件后的信号增加情况
- 估计全量结果
"""

import pandas as pd
import json
from pathlib import Path
from datetime import datetime
from typing import List, Dict
import os

from backtester_v23 import BacktesterV23


def load_first_n_stocks(data_dir: str = 'data/backtest/baostocks_full', n: int = 100) -> List[Dict]:
    """加载前N只股票数据"""
    stock_data = []
    
    if not os.path.exists(data_dir):
        print(f"数据目录不存在：{data_dir}")
        return stock_data
    
    files = sorted([f for f in os.listdir(data_dir) if f.endswith('_data.csv')])
    
    for i, file in enumerate(files[:n]):
        code = file.replace('_data.csv', '')
        file_path = os.path.join(data_dir, file)
        
        try:
            df = pd.read_csv(file_path)
            df['date'] = pd.to_datetime(df['date'])
            df = df.sort_values('date').reset_index(drop=True)
            
            stock_data.append({
                'code': code,
                'data': df
            })
            print(f"加载 {code} 数据，共 {len(df)} 条")
        except Exception as e:
            print(f"加载 {code} 数据失败：{e}")
    
    return stock_data


def quick_test_v23(n_stocks: int = 100):
    """快速测试 V2.3"""
    print("="*80)
    print(f"V2.3 快速测试（前 {n_stocks} 只股票）")
    print("="*80)
    print("V2.3 参数设置：")
    print("  - 缩量到放量时间窗口：40 天（从 25 天放宽）")
    print("  - 跌幅阈值：10%（从 12% 放宽）")
    print("  - 放量涨幅：4%（从 5% 放宽）")
    print("  - 量比最小值：1.3（从 1.5 放宽）")
    print("  - 缩量比率：70%（从 60% 放宽）")
    print("目标：每月 2-3 个信号（每年 24-36 个）")
    print("="*80)
    
    # 加载数据
    stock_data = load_first_n_stocks('data/backtest/baostocks_full', n_stocks)
    print(f"共加载 {len(stock_data)} 只股票数据")
    
    if not stock_data:
        print("没有加载到任何股票数据")
        return
    
    # 创建回测器
    backtester = BacktesterV23('config_v21_final.yaml')
    
    # 回测结果
    all_patterns = []
    all_trades = []
    
    # 遍历股票
    for stock in stock_data:
        code = stock['code']
        df = stock['data']
        
        # 检测所有形态
        patterns = backtester.check_all_patterns(df, code, '2019-01-01', '2026-04-07')
        
        if patterns:
            print(f"{code}: 检测到 {len(patterns)} 个形态")
            all_patterns.extend(patterns)
            
            # 对每个形态模拟交易
            for pattern in patterns:
                trade = backtester.simulate_trade(df, pattern)
                if trade:
                    all_trades.append(trade)
    
    # 统计结果
    print("\n" + "="*80)
    print("快速测试结果统计")
    print("="*80)
    
    total_stocks = len(stock_data)
    matched_stocks = len(set(p['code'] for p in all_patterns))
    total_patterns = len(all_patterns)
    total_trades = len(all_trades)
    
    print(f"测试股票数：{total_stocks}")
    print(f"满足形态股票数：{matched_stocks}")
    print(f"总形态数：{total_patterns}")
    print(f"总交易数：{total_trades}")
    
    # 按年份统计
    yearly_stats = {}
    for pattern in all_patterns:
        retrace_date = pattern['retrace_date']
        if hasattr(retrace_date, 'year'):
            year = str(retrace_date.year)
        else:
            year = str(retrace_date)[:4]
        yearly_stats[year] = yearly_stats.get(year, 0) + 1
    
    print("\n按年份统计：")
    for year in sorted(yearly_stats.keys()):
        print(f"{year}年：{yearly_stats[year]} 个形态")
    
    # 排除 2019 年后的统计
    def get_year_from_date(date_obj):
        if hasattr(date_obj, 'year'):
            return str(date_obj.year)
        else:
            return str(date_obj)[:4]
    
    patterns_exclude_2019 = [p for p in all_patterns if get_year_from_date(p['retrace_date']) != '2019']
    print(f"\n排除 2019 年后形态数：{len(patterns_exclude_2019)}")
    
    # 计算每月平均信号数（排除 2019）
    if patterns_exclude_2019:
        dates = [p['retrace_date'] for p in patterns_exclude_2019]
        min_date = min(dates)
        max_date = max(dates)
        
        min_dt = pd.to_datetime(min_date)
        max_dt = pd.to_datetime(max_date)
        months_diff = (max_dt.year - min_dt.year) * 12 + (max_dt.month - min_dt.month) + 1
        
        avg_per_month = len(patterns_exclude_2019) / months_diff
        print(f"\n数据时间范围：{min_date} 到 {max_date}")
        print(f"月份数：{months_diff} 个月")
        print(f"平均每月信号数：{avg_per_month:.2f} 个/月")
        print(f"平均每年信号数：{avg_per_month * 12:.2f} 个/年")
        
        # 估算全量结果（3317只股票）
        scale_factor = 3317 / total_stocks
        estimated_total_patterns = total_patterns * scale_factor
        estimated_exclude_2019 = len(patterns_exclude_2019) * scale_factor
        estimated_avg_monthly = avg_per_month * scale_factor
        
        print(f"\n基于 {total_stocks} 只样本估算全量结果（3317只股票）：")
        print(f"  估算总形态数：{estimated_total_patterns:.0f} 个")
        print(f"  估算排除2019后形态数：{estimated_exclude_2019:.0f} 个")
        print(f"  估算平均每月信号数：{estimated_avg_monthly:.2f} 个/月")
        print(f"  估算平均每年信号数：{estimated_avg_monthly * 12:.2f} 个/年")
        
        # 检查是否达到目标
        if estimated_avg_monthly >= 2:
            print(f"\n✅ 估算能达到目标：每月 {estimated_avg_monthly:.2f} 个信号（目标：2-3 个）")
        else:
            print(f"\n⚠️  估算未达到目标：每月 {estimated_avg_monthly:.2f} 个信号（目标：2-3 个）")
    
    # 与 V2.2 对比
    print("\n" + "="*80)
    print("与 V2.2 对比（基于样本估算）")
    print("="*80)
    
    # V2.2 全量结果已知：总478个，排除2019后47个
    v22_total = 478
    v22_exclude_2019 = 47
    
    if total_patterns > 0:
        # 估算 V2.3 全量结果
        v23_total_est = total_patterns * (3317 / total_stocks)
        v23_exclude_2019_est = len(patterns_exclude_2019) * (3317 / total_stocks)
        
        print(f"V2.2 全量结果：")
        print(f"  总形态数：{v22_total} 个")
        print(f"  排除2019后：{v22_exclude_2019} 个")
        
        print(f"\nV2.3 估算全量结果：")
        print(f"  总形态数：{v23_total_est:.0f} 个（增长 {(v23_total_est/v22_total-1)*100:.1f}%）")
        print(f"  排除2019后：{v23_exclude_2019_est:.0f} 个（增长 {(v23_exclude_2019_est/v22_exclude_2019-1)*100:.1f}%）")
    
    # 保存结果
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    output_dir = Path('backtest_results')
    output_dir.mkdir(exist_ok=True)
    
    json_file = output_dir / f'quick_test_v23_{timestamp}.json'
    with open(json_file, 'w', encoding='utf-8') as f:
        json.dump({
            'n_stocks': n_stocks,
            'patterns': all_patterns,
            'trades': all_trades,
            'summary': {
                'total_stocks': total_stocks,
                'matched_stocks': matched_stocks,
                'total_patterns': total_patterns,
                'total_trades': total_trades,
                'patterns_exclude_2019': len(patterns_exclude_2019),
                'yearly_stats': yearly_stats
            }
        }, f, ensure_ascii=False, default=str)
    print(f"\n结果保存到：{json_file}")


if __name__ == '__main__':
    quick_test_v23(n_stocks=300)
