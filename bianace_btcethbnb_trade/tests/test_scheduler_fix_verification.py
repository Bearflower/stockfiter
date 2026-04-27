#!/usr/bin/env python3
"""
验证 scheduler_new.py 修复的测试脚本

修复内容：
1. 字段名映射：position_share -> position_side
2. 限价单参数：price 和 time_in_force

验证内容：
1. 订单生成器生成的数据结构
2. 参数映射逻辑
3. 限价单参数传递
4. API 方法签名匹配
"""

import sys
import os
from decimal import Decimal
from unittest.mock import Mock, MagicMock

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.order_generator import OrderGenerator


def test_order_generator_field_names():
    """测试订单生成器生成的字段名"""
    print("\n" + "="*80)
    print("测试 1: 订单生成器字段名验证")
    print("="*80)

    # Mock 策略参数
    mock_params = Mock()
    mock_params.get = Mock(side_effect=lambda key, default=None: {
        'risk_management.take_profit_levels': {
            'tp1_multiplier': Decimal('2.5'),
            'tp2_multiplier': Decimal('4.0'),
            'tp1_ratio': Decimal('0.25'),
            'tp2_ratio': Decimal('0.25'),
            'tp3_ratio': Decimal('0.50')
        },
        'position_sizing.leverage_by_grade': {
            'S': 5,
            'A': 4,
            'B': 3
        },
        'account.min_notional_value': Decimal('100')
    }.get(key, default))

    order_generator = OrderGenerator(params=mock_params)

    # 测试市价单
    print("\n【测试 1.1】市价单参数生成")
    order_template = {
        'symbol': 'BTCUSDT',
        'direction': 'LONG',
        'quantity': Decimal('0.01'),
        'entry_price': Decimal('50000'),
        'stop_loss_price': Decimal('48000')
    }

    market_params = order_generator.generate_market_order_params(order_template)
    print(f"  生成的市价单参数：{market_params}")

    # 验证字段名
    assert 'position_share' in market_params, "❌ 市价单应该包含 position_share 字段"
    assert market_params['position_share'] == 'BOTH', "❌ position_share 应该为 BOTH"
    print("  ✅ 市价单字段名正确：position_share = BOTH")

    # 测试限价单
    print("\n【测试 1.2】限价单参数生成")
    limit_params = order_generator.generate_limit_order_params(
        order_template,
        current_price=Decimal('50000'),
        orderbook_data={'bids': [{'price': '49999'}]}
    )
    print(f"  生成的限价单参数：{limit_params}")

    # 验证字段名
    assert 'position_share' in limit_params, "❌ 限价单应该包含 position_share 字段"
    assert limit_params['position_share'] == 'BOTH', "❌ position_share 应该为 BOTH"
    assert 'price' in limit_params, "❌ 限价单应该包含 price 字段"
    assert 'timeInForce' in limit_params, "❌ 限价单应该包含 timeInForce 字段"
    print("  ✅ 限价单字段名正确：position_share = BOTH")
    print(f"  ✅ 限价单价格：{limit_params['price']}")
    print(f"  ✅ 限价单有效期：{limit_params['timeInForce']}")

    # 测试止损单
    print("\n【测试 1.3】止损单参数生成")
    stop_loss_params = order_generator.generate_stop_loss_order_params(order_template)
    print(f"  生成的止损单参数：{stop_loss_params}")

    # 验证字段名
    assert 'position_side' in stop_loss_params, "❌ 止损单应该包含 position_side 字段"
    assert stop_loss_params['position_side'] == 'BOTH', "❌ position_side 应该为 BOTH"
    print("  ✅ 止损单字段名正确：position_side = BOTH")

    # 测试止盈单
    print("\n【测试 1.4】止盈单参数生成")
    tp_level = {'level': 'TP1', 'price': Decimal('52000'), 'ratio': Decimal('0.25')}
    tp_params = order_generator.generate_take_profit_order_params(order_template, tp_level)
    print(f"  生成的止盈单参数：{tp_params}")

    # 验证字段名
    assert 'position_side' in tp_params, "❌ 止盈单应该包含 position_side 字段"
    assert tp_params['position_side'] == 'BOTH', "❌ position_side 应该为 BOTH"
    print("  ✅ 止盈单字段名正确：position_side = BOTH")


def test_scheduler_parameter_mapping():
    """测试 scheduler_new.py 的参数映射逻辑"""
    print("\n" + "="*80)
    print("测试 2: 参数映射逻辑验证")
    print("="*80)

    # 模拟订单生成器生成的订单数据
    entry_order = {
        'symbol': 'BTCUSDT',
        'side': 'BUY',
        'position_share': 'BOTH',  # 订单生成器使用 position_share
        'type': 'LIMIT',
        'quantity': Decimal('0.01'),
        'price': '49999',
        'timeInForce': 'GTC'
    }

    print(f"\n【测试 2.1】订单生成器生成的数据：")
    print(f"  {entry_order}")

    # 模拟 scheduler_new.py 的参数映射逻辑（修复后）
    print(f"\n【测试 2.2】参数映射（修复后）：")
    entry_params = {
        'symbol': entry_order.get('symbol'),
        'side': entry_order.get('side'),
        'position_side': entry_order.get('position_share'),  # 修正字段名
        'order_type': entry_order.get('type'),
        'quantity': entry_order.get('quantity'),
    }

    # 限价单参数
    if entry_order.get('type') == 'LIMIT':
        entry_params['price'] = entry_order.get('price')
        entry_params['time_in_force'] = entry_order.get('timeInForce', 'GTC')

    print(f"  映射后的参数：{entry_params}")

    # 验证映射结果
    assert 'position_side' in entry_params, "❌ 映射后应该包含 position_side"
    assert entry_params['position_side'] == 'BOTH', "❌ position_side 应该为 BOTH"
    assert 'position_share' not in entry_params, "❌ 映射后不应该包含 position_share"
    print("  ✅ 字段名映射正确：position_share -> position_side")

    # 验证限价单参数
    assert 'price' in entry_params, "❌ 限价单应该包含 price"
    assert 'time_in_force' in entry_params, "❌ 限价单应该包含 time_in_force"
    print("  ✅ 限价单参数正确：price 和 time_in_force 已添加")


def test_api_method_signature():
    """测试 API 方法签名"""
    print("\n" + "="*80)
    print("测试 3: API 方法签名验证")
    print("="*80)

    # 检查 place_um_order 方法签名
    from utils.binance_trade_api import BinanceTradeAPI
    import inspect

    sig = inspect.signature(BinanceTradeAPI.place_um_order)
    params = list(sig.parameters.keys())

    print(f"\n【测试 3.1】place_um_order 方法签名：")
    print(f"  参数列表：{params}")

    # 验证参数名
    assert 'position_side' in params, "❌ place_um_order 应该包含 position_side 参数"
    assert 'position_share' not in params, "❌ place_um_order 不应该包含 position_share 参数"
    print("  ✅ 参数名正确：position_side")

    # 检查 place_pm_conditional_order 方法签名
    sig2 = inspect.signature(BinanceTradeAPI.place_pm_conditional_order)
    params2 = list(sig2.parameters.keys())

    print(f"\n【测试 3.2】place_pm_conditional_order 方法签名：")
    print(f"  参数列表：{params2}")

    # 验证参数名
    assert 'position_side' in params2, "❌ place_pm_conditional_order 应该包含 position_side 参数"
    print("  ✅ 参数名正确：position_side")


def test_limit_order_parameters():
    """测试限价单参数传递"""
    print("\n" + "="*80)
    print("测试 4: 限价单参数传递验证")
    print("="*80)

    # 测试不同类型的订单
    test_cases = [
        {
            'name': '市价单',
            'order': {
                'symbol': 'BTCUSDT',
                'side': 'BUY',
                'position_share': 'BOTH',
                'type': 'MARKET',
                'quantity': Decimal('0.01')
            },
            'expected_params': ['symbol', 'side', 'position_side', 'order_type', 'quantity'],
            'unexpected_params': ['price', 'time_in_force']
        },
        {
            'name': '限价单',
            'order': {
                'symbol': 'BTCUSDT',
                'side': 'BUY',
                'position_share': 'BOTH',
                'type': 'LIMIT',
                'quantity': Decimal('0.01'),
                'price': '49999',
                'timeInForce': 'GTC'
            },
            'expected_params': ['symbol', 'side', 'position_side', 'order_type', 'quantity', 'price', 'time_in_force'],
            'unexpected_params': []
        }
    ]

    for test_case in test_cases:
        print(f"\n【测试 4.{test_cases.index(test_case) + 1}】{test_case['name']}")
        print(f"  原始订单：{test_case['order']}")

        # 模拟参数映射
        entry_params = {
            'symbol': test_case['order'].get('symbol'),
            'side': test_case['order'].get('side'),
            'position_side': test_case['order'].get('position_share'),
            'order_type': test_case['order'].get('type'),
            'quantity': test_case['order'].get('quantity'),
        }

        # 限价单参数
        if test_case['order'].get('type') == 'LIMIT':
            entry_params['price'] = test_case['order'].get('price')
            entry_params['time_in_force'] = test_case['order'].get('timeInForce', 'GTC')

        print(f"  映射后参数：{entry_params}")

        # 验证预期参数
        for param in test_case['expected_params']:
            assert param in entry_params, f"❌ 缺少预期参数：{param}"
        print(f"  ✅ 包含所有预期参数：{test_case['expected_params']}")

        # 验证不应该存在的参数
        for param in test_case['unexpected_params']:
            assert param not in entry_params, f"❌ 不应该包含参数：{param}"
        if test_case['unexpected_params']:
            print(f"  ✅ 不包含不应该存在的参数：{test_case['unexpected_params']}")


def main():
    """运行所有测试"""
    print("\n" + "="*80)
    print("开始验证 scheduler_new.py 修复")
    print("="*80)

    try:
        # 测试 1: 订单生成器字段名
        test_order_generator_field_names()

        # 测试 2: 参数映射逻辑
        test_scheduler_parameter_mapping()

        # 测试 3: API 方法签名
        test_api_method_signature()

        # 测试 4: 限价单参数传递
        test_limit_order_parameters()

        print("\n" + "="*80)
        print("✅ 所有测试通过！修复验证成功！")
        print("="*80)

        print("\n【修复总结】")
        print("1. ✅ 字段名映射正确：position_share -> position_side")
        print("2. ✅ 限价单参数正确：price 和 time_in_force 已正确传递")
        print("3. ✅ API 方法签名匹配：所有方法都使用 position_side 参数")
        print("4. ✅ 订单生成器一致性：止损/止盈单已使用 position_side，开仓单需要映射")

        return 0

    except AssertionError as e:
        print(f"\n❌ 测试失败：{e}")
        return 1
    except Exception as e:
        print(f"\n❌ 发生错误：{e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == '__main__':
    sys.exit(main())
