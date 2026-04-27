#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
每日形态扫描脚本（T 日收盘后运行）

功能：
1. 扫描全市场股票，找出当日完成回踩确认的股票
2. 保存信号到 JSON 文件
3. 供次日飞书推送使用
"""

import pandas as pd
import json
from pathlib import Path
from datetime import datetime, timedelta
import yaml
import sys

from utils.logger import get_logger

# 导入数据库管理器
from data.database import DatabaseManager

# 导入回测器
from core.backtester import Backtester

logger = get_logger()


def load_config(config_file='config/config.yaml'):
    """加载配置文件"""
    with open(config_file, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)


def get_latest_trading_date():
    """获取最近一个交易日（假设今天就是交易日）"""
    today = datetime.now()
    # 如果是周末，返回上周五
    if today.weekday() >= 5:
        today = today - timedelta(days=today.weekday() - 4)
    return today.strftime('%Y-%m-%d')


def scan_daily_signals(config: dict, output_dir='signals'):
    """
    扫描当日信号
    
    Args:
        config: 配置字典
        output_dir: 输出目录
    
    Returns:
        list: 信号列表
    """
    print("=" * 80)
    print("每日形态扫描")
    print("=" * 80)
    
    # 获取最近交易日
    signal_date = get_latest_trading_date()
    print(f"扫描日期：{signal_date}")
    print()
    
    # 从数据库获取过滤后的股票列表
    db = DatabaseManager()
    try:
        # 优先使用固定股票列表
        import os
        stocks_file = '/app/main_board_stocks.csv'
        if os.path.exists(stocks_file):
            stock_list_df = pd.read_csv(stocks_file, dtype={'code': str})
            print(f"从固定列表获取到 {len(stock_list_df)} 只沪深主板股票")
        else:
            # 如果固定列表不存在，使用数据库列表并过滤
            stock_list_df = db.get_stock_list()
            print(f"⚠️  固定列表不存在，从数据库获取到 {len(stock_list_df)} 只股票（包含所有板块）")
    except Exception as e:
        print(f"⚠️  数据库获取失败：{e}")
        return []
    
    # 初始化回测器
    backtester = Backtester(config_path='config/config.yaml', version='v24')
    
    print(f"开始扫描 {len(stock_list_df)} 只股票...")
    print()
    
    signals = []
    
    # 遍历股票列表（从数据库）
    for idx, row in stock_list_df.iterrows():
        code = row['code']
        name = row['name']
        symbol = row['symbol']
        
        # 从数据库获取 K 线数据
        try:
            kline_df = db.get_kline_history(code, days=120)
            
            if kline_df is None or len(kline_df) < 60:
                continue
        except Exception as e:
            logger.debug(f"{code} 获取 K 线失败：{e}")
            continue
        
        # 检测形态（检测所有形态，不只第一个）
        try:
            # 使用最近 N 天的数据检测（N=120，覆盖更长的时间窗口）
            recent_df = kline_df.iloc[-120:].copy()
            
            # 检测所有形态（不只第一个）
            all_patterns = backtester.check_all_patterns(
                recent_df, code, 
                recent_df['date'].min().strftime('%Y-%m-%d'),
                recent_df['date'].max().strftime('%Y-%m-%d')
            )
            
            if all_patterns:
                # 对每个形态进行检查
                for pattern_info in all_patterns:
                    # 检查回踩确认日是否是今天（或最近 1-2 天）
                    retrace_date = pattern_info.get('retrace_date')
                    
                    if retrace_date:
                        retrace_date_str = str(retrace_date)[:10]  # 转换为字符串
                        
                        # 如果回踩日是今天或昨天，加入信号列表
                        if retrace_date_str >= signal_date or \
                           (datetime.strptime(signal_date, '%Y-%m-%d') - datetime.strptime(retrace_date_str[:10], '%Y-%m-%d')).days <= 1:
                            
                            # 计算止损价（支撑位 * 0.97）
                            support_level = pattern_info.get('support_level', 0)
                            stop_loss_price = support_level * 0.97
                            
                            signal = {
                                'code': code,
                                'name': name,
                                'support_level': round(support_level, 2),
                                'stop_loss_price': round(stop_loss_price, 2),
                                'retrace_date': retrace_date_str,
                                'surge_date': str(pattern_info.get('surge_date', ''))[:10],
                                'signal_date': signal_date
                            }
                            
                            signals.append(signal)
                            print(f"✅ {code}: 支撑位 {support_level:.2f}, 止损 {stop_loss_price:.2f}")
                            break  # 每个股票当天只发一个信号
        except Exception as e:
            continue
    
    print()
    print("=" * 80)
    print(f"扫描完成：发现 {len(signals)} 个信号")
    print("=" * 80)
    
    # 关闭数据库连接
    db.close()
    
    # 保存信号
    if signals:
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        output_file = Path(output_dir) / f'signals_{signal_date}.json'
        
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(signals, f, ensure_ascii=False, indent=2)
        
        print(f"\n✅ 信号已保存：{output_file}")
    
    return signals


def main():
    """主函数"""
    print("=" * 80)
    print("股票形态每日扫描系统")
    print("=" * 80)
    
    # 加载配置
    try:
        config = load_config()
        print("\n✅ 配置加载完成")
    except Exception as e:
        print(f"\n❌ 配置加载失败：{e}")
        sys.exit(1)
    
    # 扫描信号
    signals = scan_daily_signals(config)
    
    if not signals:
        print("\n⚠️  今日无信号")
    else:
        print(f"\n✅ 共发现 {len(signals)} 个买入信号")
        
        # 显示信号详情
        print("\n信号列表:")
        print("-" * 80)
        for sig in signals:
            print(f"{sig['code']} - {sig['name']}: 支撑 {sig['support_level']}, 止损 {sig['stop_loss_price']}")


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n⚠️  用户中断扫描")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ 扫描异常：{e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
