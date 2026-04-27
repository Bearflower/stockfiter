#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
飞书推送脚本（已改造为使用通用通知服务）
T+1 日开盘前运行

功能：
1. 读取昨日扫描的信号
2. 通过通用通知服务生成消息
3. 推送到飞书
"""

import json
import sys
from pathlib import Path
from datetime import datetime, timedelta

# 导入改造后的通知模块
sys.path.insert(0, str(Path(__file__).parent))
from output.feishu_v2 import StockNotifier


def load_signals(signal_date: str = None) -> list:
    """
    加载信号数据
    
    Args:
        signal_date: 信号日期（YYYY-MM-DD），默认最近一个交易日
    
    Returns:
        list: 信号列表
    """
    if signal_date is None:
        # 默认获取最近一个交易日的信号
        today = datetime.now()
        
        # 如果是周一，获取上周五的信号
        days_to_subtract = {
            0: 3,  # 周一 -> 上周五 (3 天前)
            6: 2,  # 周六 -> 上周五 (2 天前)
            5: 1,  # 周日 -> 上周五 (1 天前)
        }
        
        days_back = days_to_subtract.get(today.weekday(), 1)
        signal_date = (today - timedelta(days=days_back)).strftime('%Y-%m-%d')
    
    signal_file = Path('signals') / f'signals_{signal_date}.json'
    
    if not signal_file.exists():
        print(f"⚠️  信号文件不存在：{signal_file}")
        return []
    
    with open(signal_file, 'r', encoding='utf-8') as f:
        return json.load(f)


def main():
    """主函数"""
    print("=" * 80)
    print("股票筛选通知系统（通用通知服务）")
    print("=" * 80)
    
    # 加载信号
    signals = load_signals()
    
    if not signals:
        print("\n⚠️  今日无买入信号")
        # 发送无信号通知
        notifier = StockNotifier()
        notifier.send_daily_summary([])
        return
    
    print(f"\n📊 发现 {len(signals)} 个买入信号")
    print()
    
    # 显示信号列表
    for idx, sig in enumerate(signals, 1):
        print(f"{idx}. {sig['code']} - {sig['name']}: 支撑 {sig['support_level']}, 止损 {sig['stop_loss_price']}")
    
    print()
    
    # 检查是否为交互模式
    auto_send = True  # 默认自动发送（用于定时任务）
    if sys.stdin.isatty():
        confirm = input("是否发送通知？(y/n): ")
        auto_send = confirm.lower() == 'y'
    
    if not auto_send:
        print("❌ 取消推送")
        return
    
    # 创建通知器并发送
    notifier = StockNotifier()
    
    print("\n正在发送通知到通用服务...")
    success = notifier.send_daily_summary(signals)
    
    if success:
        print("\n✅ 推送完成！")
        print("\n📋 操作提醒:")
        print("1. 请在 9:15-9:25 查看飞书消息")
        print("2. 观察开盘价，若高开>5% 或涨停请放弃")
        print("3. 买入后立即设置条件单（止损 + 移动止盈）")
    else:
        print("\n❌ 推送失败，请检查通知服务")


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n⚠️  用户中断推送")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ 推送异常：{e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
