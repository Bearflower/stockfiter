import pytest
from src.core.strategy.grid_calculator import GridCalculator, MarketState, GridParameters


def test_grid_calculator_ranging_market():
    """测试震荡市场的网格参数计算"""
    calculator = GridCalculator()
    
    # 测试参数
    current_price = 50000.0
    atr_smooth = 1000.0
    market_state = MarketState.RANGING
    
    params = calculator.calculate(current_price, atr_smooth, market_state)
    
    # 验证结果
    assert isinstance(params, GridParameters)
    assert params.grid_count >= calculator.min_grid_count
    assert params.grid_count <= calculator.max_grid_count
    assert params.upper_price > current_price
    assert params.lower_price < current_price
    assert params.grid_direction == "NEUTRAL"


def test_grid_calculator_uptrend_market():
    """测试上升趋势市场的网格参数计算"""
    calculator = GridCalculator()
    
    # 测试参数
    current_price = 50000.0
    atr_smooth = 1000.0
    market_state = MarketState.UPTREND
    
    params = calculator.calculate(current_price, atr_smooth, market_state)
    
    # 验证结果
    assert isinstance(params, GridParameters)
    assert params.grid_direction == "LONG"
    # 上升趋势应该上边界更宽，下边界更窄
    assert (params.upper_price - current_price) > (current_price - params.lower_price)


def test_grid_calculator_downtrend_market():
    """测试下降趋势市场的网格参数计算"""
    calculator = GridCalculator()
    
    # 测试参数
    current_price = 50000.0
    atr_smooth = 1000.0
    market_state = MarketState.DOWNTREND
    
    params = calculator.calculate(current_price, atr_smooth, market_state)
    
    # 验证结果
    assert isinstance(params, GridParameters)
    assert params.grid_direction == "SHORT"
    # 下降趋势应该下边界更宽，上边界更窄
    assert (current_price - params.lower_price) > (params.upper_price - current_price)


def test_grid_calculator_profit_rate_validation():
    """测试利润率验证和网格数量调整"""
    # 设置较小的网格数量和较大的ATR，确保利润率足够
    calculator = GridCalculator(min_profit_rate=0.01, base_grid_count=10)
    
    current_price = 50000.0
    atr_smooth = 5000.0  # 较大的ATR
    market_state = MarketState.RANGING
    
    params = calculator.calculate(current_price, atr_smooth, market_state)
    
    # 验证利润率满足要求
    assert params.profit_rate >= calculator.min_profit_rate
    # 验证网格数量在合理范围内
    assert params.grid_count >= calculator.min_grid_count
    assert params.grid_count <= calculator.max_grid_count


def test_grid_calculator_adjustment_triggers():
    """测试调整触发条件检查"""
    calculator = GridCalculator()
    
    # 模拟当前参数
    current_price = 50000.0
    atr_smooth = 1000.0
    market_state = MarketState.RANGING
    
    # 计算初始参数
    current_params = calculator.calculate(current_price, atr_smooth, market_state)
    
    # 先设置_last_atr，否则第一次不会触发ATR_CHANGE
    calculator._last_atr = atr_smooth
    
    # 测试ATR变化触发
    triggers = calculator.check_adjustment_triggers(
        current_price, atr_smooth * 1.5, market_state, current_params
    )
    assert any(t.trigger_type == "ATR_CHANGE" for t in triggers)
    
    # 测试价格偏离触发
    triggers = calculator.check_adjustment_triggers(
        current_price * 1.15, atr_smooth, market_state, current_params
    )
    assert any(t.trigger_type == "PRICE_DEVIATION" for t in triggers)


def test_grid_calculator_should_adjust():
    """测试是否应该调整参数的判断"""
    calculator = GridCalculator()
    
    # 模拟当前参数
    current_price = 50000.0
    atr_smooth = 1000.0
    market_state = MarketState.RANGING
    
    # 计算初始参数
    current_params = calculator.calculate(current_price, atr_smooth, market_state)
    
    # 先设置_last_atr
    calculator._last_atr = atr_smooth
    
    # 生成触发条件
    triggers = calculator.check_adjustment_triggers(
        current_price * 1.2, atr_smooth * 1.5, market_state, current_params
    )
    
    # 检查是否应该调整
    should_adjust = calculator.should_adjust(
        triggers, current_price, current_params, atr_smooth
    )
    # 应该返回True，因为有严重的触发条件
    assert should_adjust is True


def test_grid_calculator_extreme_situation():
    """测试极端情况检测"""
    calculator = GridCalculator()
    
    # 模拟当前参数
    current_price = 50000.0
    atr_smooth = 1000.0
    market_state = MarketState.RANGING
    
    # 计算初始参数
    current_params = calculator.calculate(current_price, atr_smooth, market_state)
    
    # 先设置_last_atr
    calculator._last_atr = atr_smooth
    
    # 测试价格突破上边界10%以上
    extreme_price = current_params.upper_price * 1.11
    triggers = calculator.check_adjustment_triggers(
        extreme_price, atr_smooth, market_state, current_params
    )
    
    should_adjust = calculator.should_adjust(
        triggers, extreme_price, current_params, atr_smooth
    )
    # 极端情况应该立即调整
    assert should_adjust is True


def test_grid_calculator_grid_type_determination():
    """测试网格类型（等差/等比）的确定"""
    calculator = GridCalculator()
    
    # 测试小振幅（使用等差网格）
    current_price = 50000.0
    atr_smooth = 500.0  # 小ATR，振幅小
    market_state = MarketState.RANGING
    
    params1 = calculator.calculate(current_price, atr_smooth, market_state)
    assert params1.grid_type == "arithmetic"
    
    # 测试大振幅（使用等比网格）
    current_price = 50000.0
    atr_smooth = 5000.0  # 大ATR，振幅大
    market_state = MarketState.RANGING
    
    params2 = calculator.calculate(current_price, atr_smooth, market_state)
    assert params2.grid_type == "geometric"
    # 等比网格应该有geometric_ratio参数
    assert params2.geometric_ratio is not None


def test_grid_calculator_config_override():
    """测试通过配置覆盖默认参数"""
    # 通过配置覆盖默认参数
    config = {
        'base_grid_count': 20,
        'min_grid_count': 2,
        'max_grid_count': 100,
        'min_profit_rate': 0.02,
        'leverage': 20
    }
    
    calculator = GridCalculator(config=config)
    
    # 验证配置是否生效
    assert calculator.base_grid_count == 20
    assert calculator.min_grid_count == 2
    assert calculator.max_grid_count == 100
    assert calculator.min_profit_rate == 0.02
    assert calculator.leverage == 20


def test_grid_calculator_stop_prices():
    """测试停止价格计算"""
    calculator = GridCalculator()
    
    current_price = 50000.0
    atr_smooth = 1000.0
    
    # 上升趋势应该有停止上移价格
    params_uptrend = calculator.calculate(
        current_price, atr_smooth, MarketState.UPTREND, trend_strength=0.3
    )
    assert params_uptrend.stop_upper_price is not None
    assert params_uptrend.stop_lower_price is None
    
    # 下降趋势应该有停止下移价格
    params_downtrend = calculator.calculate(
        current_price, atr_smooth, MarketState.DOWNTREND, trend_strength=0.3
    )
    assert params_downtrend.stop_lower_price is not None
    assert params_downtrend.stop_upper_price is None
    
    # 震荡市场不应该有停止价格
    params_ranging = calculator.calculate(
        current_price, atr_smooth, MarketState.RANGING
    )
    assert params_ranging.stop_upper_price is None
    assert params_ranging.stop_lower_price is None


def test_grid_calculator_terminate_prices():
    """测试终止价格计算"""
    calculator = GridCalculator()
    
    current_price = 50000.0
    atr_smooth = 1000.0
    market_state = MarketState.RANGING
    
    params = calculator.calculate(current_price, atr_smooth, market_state)
    
    # 验证终止价格
    assert params.terminate_upper_price is not None
    assert params.terminate_lower_price is not None
    assert params.terminate_upper_price > params.upper_price
    assert params.terminate_lower_price < params.lower_price
