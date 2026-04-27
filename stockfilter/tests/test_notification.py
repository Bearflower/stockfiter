#!/usr/bin/env python3
"""测试股票筛选通知模块"""

from output.feishu_v2 import StockNotifier

def test_stock_notifier():
    """测试股票通知器"""
    print("=" * 80)
    print("测试股票筛选通知模块")
    print("=" * 80)
    
    notifier = StockNotifier()
    
    # 测试 1：发送单个股票信号
    print("\n1. 测试发送单个股票信号...")
    stock_info = {
        'code': '603529',
        'name': '爱玛科技',
        'score': 85.5,
        'surge_date': '2026-04-19',
        'current_close': 45.67,
        'support_level': 44.50,
        'surge_pct': 0.0856,
        'surge_volume_ratio': 3.25,
        'low_after_surge': 44.80
    }
    
    success = notifier.send_stock_signal(stock_info)
    print(f"   结果：{'✅ 成功' if success else '❌ 失败'}")
    
    # 测试 2：发送每日汇总
    print("\n2. 测试发送每日汇总...")
    signals = [
        {'code': '603529', 'name': '爱玛科技', 'support_level': 44.50},
        {'code': '000001', 'name': '平安银行', 'support_level': 12.30},
        {'code': '600000', 'name': '浦发银行', 'support_level': 8.90}
    ]
    
    success = notifier.send_daily_summary(signals)
    print(f"   结果：{'✅ 成功' if success else '❌ 失败'}")
    
    # 测试 3：发送无信号通知
    print("\n3. 测试发送无信号通知...")
    success = notifier.send_daily_summary([])
    print(f"   结果：{'✅ 成功' if success else '❌ 失败'}")
    
    print("\n" + "=" * 80)
    print("测试完成！")
    print("=" * 80)

if __name__ == '__main__':
    test_stock_notifier()
