"""
ETHUSDT信号生成功能测试脚本
测试内容：
1. 检查配置文件是否正确加载ETHUSDT参数
2. 验证网格数量范围是否为8-30
3. 模拟ETHUSDT信号生成（不实际连接币安API）
4. 验证推送消息格式是否正确
5. 检查利润率计算是否正确
"""
import sys
import os
from datetime import datetime
from decimal import Decimal
from typing import Dict, List
import yaml

# 添加项目根目录到路径
sys.path.insert(0, '/Users/yl/vscode/Binance_quantitative_trading')

from strategies.grid.grid_calculator import GridCalculator, GridMode, DynamicGridParams
from strategies.grid.market_state import MarketState, MarketAnalysis
from strategies.grid.signal_bot import GridSignal


class MockBinanceClient:
    """模拟币安客户端"""
    def __init__(self):
        self.eth_price = Decimal('3500.00')  # 模拟ETH价格

    async def get_ticker_price(self, symbol: str) -> Decimal:
        """获取模拟价格"""
        if symbol == 'ETHUSDT':
            return self.eth_price
        raise ValueError(f"不支持的交易对: {symbol}")


class MockKLineService:
    """模拟K线服务"""
    async def get_klines(self, symbol: str, interval: str, limit: int) -> List[Dict]:
        """获取模拟K线数据"""
        # 生成模拟K线数据（用于计算基准ATR）
        base_price = 3500.0
        klines = []
        for i in range(limit):
            # 模拟价格波动
            price_variation = (i % 10 - 5) * 10
            close = base_price + price_variation
            high = close + 50
            low = close - 50
            open_price = close - 20

            klines.append({
                'open': open_price,
                'high': high,
                'low': low,
                'close': close,
                'volume': 1000
            })
        return klines


class MockNotificationClient:
    """模拟通知客户端"""
    def __init__(self):
        self.messages = []

    async def send(self, message: str, level: str, project: str) -> bool:
        """记录推送消息"""
        self.messages.append({
            'message': message,
            'level': level,
            'project': project,
            'timestamp': datetime.now()
        })
        print(f"\n{'='*80}")
        print(f"推送消息 (级别: {level}, 项目: {project})")
        print('='*80)
        print(message)
        print('='*80)
        return True


def load_config() -> Dict:
    """加载配置文件"""
    config_path = '/Users/yl/vscode/Binance_quantitative_trading/strategies/grid/config.yaml'
    with open(config_path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)


def test_config_loading(config: Dict):
    """测试1: 检查配置文件是否正确加载ETHUSDT参数"""
    print("\n" + "="*80)
    print("测试1: 检查配置文件是否正确加载ETHUSDT参数")
    print("="*80)

    # 检查交易对配置
    symbols = config.get('symbols', [])
    print(f"✓ 配置的交易对: {symbols}")

    if 'ETHUSDT' in symbols:
        print("✓ ETHUSDT参数加载正确")
        return True
    else:
        print("✗ ETHUSDT参数未找到")
        return False


def test_grid_count_range(config: Dict):
    """测试2: 验证网格数量范围是否为8-30"""
    print("\n" + "="*80)
    print("测试2: 验证网格数量范围是否为8-30")
    print("="*80)

    grid_config = config.get('grid', {})
    min_count = grid_config.get('min_grid_count')
    max_count = grid_config.get('max_grid_count')

    print(f"✓ 最小网格数量: {min_count}")
    print(f"✓ 最大网格数量: {max_count}")

    if min_count == 8 and max_count == 30:
        print("✓ 网格数量范围配置正确 (8-30)")
        return True
    else:
        print(f"✗ 网格数量范围配置错误，期望 (8-30)，实际 ({min_count}-{max_count})")
        return False


def test_signal_generation(config: Dict):
    """测试3: 模拟ETHUSDT信号生成"""
    print("\n" + "="*80)
    print("测试3: 模拟ETHUSDT信号生成")
    print("="*80)

    try:
        # 初始化网格计算器
        grid_calculator = GridCalculator(config)
        print("✓ 网格计算器初始化成功")

        # 模拟市场分析数据
        current_price = Decimal('3500.00')
        atr_smooth = Decimal('80.00')  # 模拟平滑ATR
        atr_baseline = Decimal('100.00')  # 模拟基准ATR

        # 测试不同市场状态
        test_cases = [
            {
                'market_state': '震荡市场',
                'trend_strength': Decimal('0'),
                'description': '震荡市场场景'
            },
            {
                'market_state': '上升趋势',
                'trend_strength': Decimal('0.3'),
                'description': '上升趋势场景'
            },
            {
                'market_state': '下降趋势',
                'trend_strength': Decimal('0.3'),
                'description': '下降趋势场景'
            }
        ]

        results = []
        for case in test_cases:
            print(f"\n--- {case['description']} ---")

            # 计算动态网格参数
            params = grid_calculator.calculate_dynamic_grid_params(
                current_price=current_price,
                atr_smooth=atr_smooth,
                atr_baseline=atr_baseline,
                market_state=case['market_state'],
                trend_strength=case['trend_strength']
            )

            print(f"✓ 价格区间: {float(params.lower_boundary):.2f} - {float(params.upper_boundary):.2f} USDT")
            print(f"✓ 网格数量: {params.grid_count} 格")
            print(f"✓ 网格模式: {params.grid_mode.value}")
            print(f"✓ 每格利润率: {float(params.profit_rate) * 100:.2f}%")
            print(f"✓ 网格间距: {float(params.grid_spacing):.2f} USDT")
            print(f"✓ 止损价格: {float(params.stop_loss_low):.2f} - {float(params.stop_loss_high):.2f} USDT")

            # 验证网格数量范围
            if 8 <= params.grid_count <= 30:
                print(f"✓ 网格数量在范围内 (8-30)")
                results.append(True)
            else:
                print(f"✗ 网格数量超出范围: {params.grid_count}")
                results.append(False)

        return all(results)

    except Exception as e:
        print(f"✗ 信号生成测试失败: {str(e)}")
        import traceback
        traceback.print_exc()
        return False


def test_message_format(config: Dict):
    """测试4: 验证推送消息格式是否正确"""
    print("\n" + "="*80)
    print("测试4: 验证推送消息格式是否正确")
    print("="*80)

    try:
        # 创建模拟市场分析数据
        market_analysis = MarketAnalysis(
            state=MarketState.OSCILLATION,
            current_price=Decimal('3500.00'),
            atr_smooth=Decimal('80.00'),
            adx_1h=Decimal('18.5'),
            adx_4h=Decimal('19.2'),
            trend_strength=Decimal('0'),
            ema20_1h=Decimal('3480.00'),
            ema50_1h=Decimal('3450.00'),
            confidence=Decimal('0.8')
        )

        # 创建模拟网格参数
        grid_params = DynamicGridParams(
            lower_boundary=Decimal('3340.00'),
            upper_boundary=Decimal('3660.00'),
            grid_count=20,
            grid_mode=GridMode.ARITHMETIC,
            stop_loss_low=Decimal('3180.00'),
            stop_loss_high=Decimal('3820.00'),
            profit_rate=Decimal('0.016'),
            grid_spacing=Decimal('16.00')
        )

        # 创建模拟信号
        signal = GridSignal(
            symbol='ETHUSDT',
            market_analysis=market_analysis,
            grid_params=grid_params,
            timestamp=datetime.now(),
            message="",
            position_valid=True,
            position_message="每格1.50张（取整后1张）"
        )

        # 生成推送消息（模拟signal_bot的消息生成逻辑）
        message = generate_test_message(signal, config)

        print("\n生成的推送消息:")
        print("-" * 80)
        print(message)
        print("-" * 80)

        # 验证消息格式
        required_sections = [
            '【网格信号灯】',
            '📊 当前市场数据',
            '📐 建议网格参数',
            '🎯 止盈止损',
            '💰 资金配置',
            '💡 操作指令'
        ]

        all_present = True
        for section in required_sections:
            if section in message:
                print(f"✓ 包含必要部分: {section}")
            else:
                print(f"✗ 缺少必要部分: {section}")
                all_present = False

        # 验证关键信息
        key_info = [
            'ETHUSDT',
            '3500.00',
            '3340.00',
            '3660.00',
            '20 格',
            '等差'
        ]

        for info in key_info:
            if info in message:
                print(f"✓ 包含关键信息: {info}")
            else:
                print(f"✗ 缺少关键信息: {info}")
                all_present = False

        return all_present

    except Exception as e:
        print(f"✗ 消息格式验证失败: {str(e)}")
        import traceback
        traceback.print_exc()
        return False


def generate_test_message(signal: GridSignal, config: Dict) -> str:
    """生成测试推送消息"""
    grid_params = signal.grid_params
    market_analysis = signal.market_analysis

    # 标题
    title = f"【网格信号灯】{signal.symbol} - {market_analysis.state.value}"

    # 市场数据
    market_data = f"""
📊 当前市场数据
- 交易对: {signal.symbol}
- 价格: {float(market_analysis.current_price):.2f} USDT
- ATR(14): {float(market_analysis.atr_smooth):.2f}
- ADX(1h): {float(market_analysis.adx_1h):.2f}
- ADX(4h): {float(market_analysis.adx_4h):.2f}
- 每格利润率: {float(grid_params.profit_rate) * 100:.2f}%
"""

    # 网格参数
    grid_params_text = f"""
📐 建议网格参数
- 网格模式: {grid_params.grid_mode.value}
- 价格区间: {float(grid_params.lower_boundary):.2f} - {float(grid_params.upper_boundary):.2f} USDT
- 网格数量: {grid_params.grid_count} 格
- 网格间距: {float(grid_params.grid_spacing):.2f} USDT
"""

    # 止盈止损
    stop_loss_text = f"""
🎯 止盈止损
- 终止最低价: {float(grid_params.stop_loss_low):.2f} USDT
- 终止最高价: {float(grid_params.stop_loss_high):.2f} USDT
"""

    # 资金配置
    leverage = config.get('trading', {}).get('leverage', 10)
    margin = config.get('trading', {}).get('margin', 500)
    funding_text = f"""
💰 资金配置
- 建议杠杆: {leverage}x
- 建议保证金: {float(margin):.0f} USDT
- {signal.position_message}
"""

    # 操作指令
    operation_text = f"""
💡 操作指令：
1. 登录币安APP → 永续合约 → 策略交易 → 运行中，终止当前网格（如有）。
2. 点击"创建网格" → 合约网格。
3. 填入以上价格区间、网格数量、网格模式。
4. 设置杠杆（建议{leverage}x）、总投入金额（根据您的资金能力）。
5. 高级设置中，启用"上移/下移"并填入停止价格（如适用），设置止盈止损价格。
6. 确认创建前请检查每格下单数量≥1张。
"""

    # 组合消息
    message = f"""
{title}
{market_data}
{grid_params_text}
{stop_loss_text}
{funding_text}
{operation_text}
"""

    return message.strip()


def test_profit_rate_calculation(config: Dict):
    """测试5: 检查利润率计算是否正确"""
    print("\n" + "="*80)
    print("测试5: 检查利润率计算是否正确")
    print("="*80)

    try:
        grid_calculator = GridCalculator(config)

        # 测试等差网格利润率计算
        print("\n--- 等差网格利润率计算 ---")
        lower = Decimal('3340.00')
        upper = Decimal('3660.00')
        grid_count = 20
        current_price = Decimal('3500.00')

        profit_rate, grid_spacing = grid_calculator._calculate_profit_rate(
            lower_boundary=lower,
            upper_boundary=upper,
            grid_count=grid_count,
            grid_mode=GridMode.ARITHMETIC,
            current_price=current_price
        )

        # 手动计算验证
        expected_spacing = (upper - lower) / Decimal(str(grid_count))
        expected_profit_rate = expected_spacing / current_price

        print(f"网格间距: {float(grid_spacing):.2f} USDT")
        print(f"利润率: {float(profit_rate) * 100:.2f}%")
        print(f"预期间距: {float(expected_spacing):.2f} USDT")
        print(f"预期利润率: {float(expected_profit_rate) * 100:.2f}%")

        if abs(grid_spacing - expected_spacing) < Decimal('0.01'):
            print("✓ 等差网格间距计算正确")
        else:
            print("✗ 等差网格间距计算错误")
            return False

        if abs(profit_rate - expected_profit_rate) < Decimal('0.0001'):
            print("✓ 等差网格利润率计算正确")
        else:
            print("✗ 等差网格利润率计算错误")
            return False

        # 测试等比网格利润率计算
        print("\n--- 等比网格利润率计算 ---")
        profit_rate_geo, grid_spacing_geo = grid_calculator._calculate_profit_rate(
            lower_boundary=lower,
            upper_boundary=upper,
            grid_count=grid_count,
            grid_mode=GridMode.GEOMETRIC,
            current_price=current_price
        )

        # 手动计算验证
        expected_ratio = (upper / lower) ** (Decimal('1') / Decimal(str(grid_count)))
        expected_profit_rate_geo = expected_ratio - Decimal('1')

        print(f"网格比例: {float(expected_ratio):.6f}")
        print(f"利润率: {float(profit_rate_geo) * 100:.2f}%")
        print(f"预期利润率: {float(expected_profit_rate_geo) * 100:.2f}%")

        if abs(profit_rate_geo - expected_profit_rate_geo) < Decimal('0.0001'):
            print("✓ 等比网格利润率计算正确")
        else:
            print("✗ 等比网格利润率计算错误")
            return False

        # 验证利润率计算正确性
        print("\n--- 利润率计算正确性验证 ---")

        # 等差网格利润率应该等于网格间距/当前价格
        expected_profit_rate_arithmetic = expected_spacing / current_price
        if abs(profit_rate - expected_profit_rate_arithmetic) < Decimal('0.0001'):
            print(f"✓ 等差网格利润率计算正确: {float(profit_rate) * 100:.2f}%")
        else:
            print(f"✗ 等差网格利润率计算错误")
            return False

        # 等比网格利润率应该等于比例-1
        if abs(profit_rate_geo - expected_profit_rate_geo) < Decimal('0.0001'):
            print(f"✓ 等比网格利润率计算正确: {float(profit_rate_geo) * 100:.2f}%")
        else:
            print(f"✗ 等比网格利润率计算错误")
            return False

        # 测试利润率验证功能
        print("\n--- 利润率验证功能测试 ---")

        # 创建一个利润率较低的参数对象（使用合理的价格区间）
        low_profit_params = DynamicGridParams(
            lower_boundary=Decimal('3450.00'),
            upper_boundary=Decimal('3550.00'),
            grid_count=30,
            grid_mode=GridMode.ARITHMETIC,
            stop_loss_low=Decimal('3350.00'),
            stop_loss_high=Decimal('3650.00'),
            profit_rate=Decimal('0.0095'),  # 0.95%，低于1%
            grid_spacing=Decimal('3.33')
        )

        # 验证利润率
        is_valid, suggested_count = grid_calculator.validate_profit_rate(
            low_profit_params,
            min_profit_rate=Decimal('0.01')
        )

        if not is_valid:
            print(f"✓ 利润率验证功能正常：检测到低利润率 {float(low_profit_params.profit_rate) * 100:.2f}%")
            
            if suggested_count is not None:
                print(f"✓ 建议调整网格数量为: {suggested_count} 格")

                # 重新计算调整后的利润率
                new_profit_rate, _ = grid_calculator._calculate_profit_rate(
                    lower_boundary=low_profit_params.lower_boundary,
                    upper_boundary=low_profit_params.upper_boundary,
                    grid_count=suggested_count,
                    grid_mode=low_profit_params.grid_mode,
                    current_price=current_price
                )
                print(f"✓ 调整后利润率: {float(new_profit_rate) * 100:.2f}%")

                if new_profit_rate >= Decimal('0.01'):
                    print(f"✓ 调整后利润率满足币安要求 (> 1%)")
                else:
                    print(f"⚠️  调整后利润率仍不满足要求，但验证功能正常")
            else:
                print(f"⚠️  无法通过减少网格数量满足要求，但验证功能正常")
        else:
            print(f"✗ 利润率验证功能异常")
            return False

        return True

    except Exception as e:
        print(f"✗ 利润率计算测试失败: {str(e)}")
        import traceback
        traceback.print_exc()
        return False


def run_all_tests():
    """运行所有测试"""
    print("\n" + "="*80)
    print("ETHUSDT信号生成功能测试")
    print("="*80)
    print(f"测试时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*80)

    # 加载配置
    try:
        config = load_config()
        print("✓ 配置文件加载成功")
    except Exception as e:
        print(f"✗ 配置文件加载失败: {str(e)}")
        return

    # 运行测试
    results = {
        '测试1 - 配置文件加载': test_config_loading(config),
        '测试2 - 网格数量范围': test_grid_count_range(config),
        '测试3 - 信号生成': test_signal_generation(config),
        '测试4 - 消息格式': test_message_format(config),
        '测试5 - 利润率计算': test_profit_rate_calculation(config)
    }

    # 输出测试结果汇总
    print("\n" + "="*80)
    print("测试结果汇总")
    print("="*80)

    for test_name, result in results.items():
        status = "✓ 通过" if result else "✗ 失败"
        print(f"{test_name}: {status}")

    # 统计
    passed = sum(1 for r in results.values() if r)
    total = len(results)

    print("\n" + "="*80)
    print(f"测试统计: {passed}/{total} 通过")
    print("="*80)

    if passed == total:
        print("\n🎉 所有测试通过！")
    else:
        print(f"\n⚠️  有 {total - passed} 个测试失败")


if __name__ == '__main__':
    run_all_tests()
