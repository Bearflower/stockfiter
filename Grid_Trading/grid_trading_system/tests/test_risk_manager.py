import pytest
from src.core.strategy.risk_manager import RiskManager, RiskAction


def test_risk_manager_hard_stop_loss():
    """测试硬止损功能"""
    risk_manager = RiskManager(initial_margin=1000.0, hard_stop_loss_ratio=-0.08)
    
    # 测试正常情况（未触发硬止损）
    result = risk_manager.check_conditions(
        current_pnl_percent=-0.05,  # -5% 亏损
        current_price=50000.0,
        current_equity=950.0
    )
    assert result.action == RiskAction.CONTINUE
    assert result.is_safe_to_trade is True
    assert result.hard_stop_triggered is False
    
    # 测试触发硬止损
    result = risk_manager.check_conditions(
        current_pnl_percent=-0.09,  # -9% 亏损，超过-8%阈值
        current_price=50000.0,
        current_equity=910.0
    )
    assert result.action == RiskAction.HARD_STOP_LOSS
    assert result.is_safe_to_trade is False
    assert result.hard_stop_triggered is True


def test_risk_manager_trailing_profit():
    """测试移动止盈功能"""
    risk_manager = RiskManager(
        trailing_profit_start=0.15,  # 15% 启动
        trailing_profit_retrace=0.05  # 5% 回撤触发
    )
    
    # 测试未达到启动条件
    result = risk_manager.check_conditions(
        current_pnl_percent=0.10,  # 10% 盈利，未达到15%启动线
        current_price=50000.0,
        current_equity=1100.0,
        market_data={'market_state': '上升趋势'}
    )
    assert result.action == RiskAction.CONTINUE
    assert result.trailing_activated is False
    
    # 测试激活移动止盈
    result = risk_manager.check_conditions(
        current_pnl_percent=0.16,  # 16% 盈利，达到启动线
        current_price=52000.0,
        current_equity=1160.0,
        market_data={'market_state': '上升趋势'}
    )
    assert result.action == RiskAction.TRAILING_PROFIT_ACTIVATED
    assert result.trailing_activated is True
    assert result.peak_price == 52000.0
    
    # 测试价格继续上涨，更新止盈价格
    result = risk_manager.check_conditions(
        current_pnl_percent=0.20,  # 20% 盈利
        current_price=53000.0,
        current_equity=1200.0,
        market_data={'market_state': '上升趋势'}
    )
    assert result.action == RiskAction.UPDATE_STOP_PRICE
    assert result.trailing_activated is True
    assert result.peak_price == 53000.0
    # 止盈价格应该是峰值价格的95%（回撤5%）
    expected_stop_price = 53000.0 * (1 - 0.05)
    assert abs(result.new_stop_price - expected_stop_price) < 1.0
    
    # 测试触发移动止盈
    result = risk_manager.check_conditions(
        current_pnl_percent=0.18,  # 18% 盈利
        current_price=50350.0,  # 低于止盈价格
        current_equity=1180.0,
        market_data={'market_state': '上升趋势'}
    )
    assert result.action == RiskAction.TRAILING_PROFIT_TRIGGERED
    assert result.trailing_triggered is True


def test_risk_manager_emergency_pause():
    """测试紧急暂停功能"""
    risk_manager = RiskManager(
        max_api_errors=3,
        max_api_delay=3.0,
        max_grid_breakthrough=3,
        breakthrough_window=300
    )
    
    # 测试API错误触发紧急暂停
    for i in range(3):
        result = risk_manager.check_conditions(
            current_pnl_percent=0.05,
            current_price=50000.0,
            current_equity=1050.0,
            system_data={'api_error': True}
        )
    
    # 第三次错误应该触发紧急暂停
    assert result.action == RiskAction.EMERGENCY_PAUSE
    assert result.is_safe_to_trade is False
    assert result.emergency_paused is True
    assert 'API连续报错' in result.pause_reason


def test_risk_manager_slippage_protection():
    """测试滑点保护功能"""
    risk_manager = RiskManager(slippage_tolerance=0.005)  # 0.5% 滑点容忍度
    
    # 测试正常滑点
    slippage = risk_manager.check_slippage(50000.0, 50200.0)  # 0.4% 滑点
    assert slippage == 0.004  # 0.4%
    
    # 测试超限滑点
    slippage = risk_manager.check_slippage(50000.0, 50300.0)  # 0.6% 滑点
    assert slippage == 0.006  # 0.6%
    # 注意：check_slippage 方法只计算滑点并记录警告，不会返回是否超限


def test_risk_manager_drawdown_warning():
    """测试回撤警告功能"""
    # 设置较低的硬止损阈值，避免触发硬止损
    risk_manager = RiskManager(
        initial_margin=1000.0, 
        max_drawdown=0.2,  # 20% 最大回撤
        hard_stop_loss_ratio=-0.25  # 设置更低的硬止损阈值
    )
    
    # 先设置峰值权益为初始保证金的120%
    risk_manager._peak_equity = 1200.0
    
    # 测试接近最大回撤（17% 回撤，超过80%的最大回撤阈值）
    current_equity = 1200.0 * (1 - 0.17)  # 996.0
    # 计算当前盈亏百分比：(996.0 - 1000.0) / 1000.0 = -0.004 (-0.4% 亏损)
    current_pnl_percent = (current_equity - 1000.0) / 1000.0
    
    result = risk_manager.check_conditions(
        current_pnl_percent=current_pnl_percent,
        current_price=50000.0,
        current_equity=current_equity
    )
    
    # 由于当前权益996.0略低于初始保证金1000.0，但未达到硬止损阈值
    # 回撤达到17%，超过最大回撤20%的80%，应该触发回撤警告
    assert result.action == RiskAction.DRAWDOWN_WARNING
    assert result.risk_level == "high"
    assert result.is_safe_to_trade is True


def test_risk_manager_dynamic_position():
    """测试动态仓位调整功能"""
    risk_manager = RiskManager(
        initial_margin=1000.0,
        leverage=10,
        base_atr=1000.0
    )
    
    # 测试波动率正常情况
    result = risk_manager.check_conditions(
        current_pnl_percent=0.05,
        current_price=50000.0,
        current_equity=1050.0,
        market_data={'atr_smooth': 1000.0}  # 正常波动率
    )
    assert result.action == RiskAction.CONTINUE
    assert result.suggested_margin is not None
    
    # 测试低波动率（建议增加仓位）
    result = risk_manager.check_conditions(
        current_pnl_percent=0.05,
        current_price=50000.0,
        current_equity=1050.0,
        market_data={'atr_smooth': 600.0}  # 低波动率
    )
    assert result.action == RiskAction.CONTINUE
    assert result.suggested_margin is not None
    
    # 测试高波动率（建议减少仓位）
    result = risk_manager.check_conditions(
        current_pnl_percent=0.05,
        current_price=50000.0,
        current_equity=1050.0,
        market_data={'atr_smooth': 1500.0}  # 高波动率
    )
    assert result.action == RiskAction.CONTINUE
    assert result.suggested_margin is not None


def test_risk_manager_position_limit():
    """测试仓位限制检查"""
    risk_manager = RiskManager(max_position_pct=0.3)  # 30% 最大仓位
    
    # 测试未超限
    is_over_limit = risk_manager.check_position_limit(250.0, 1000.0)  # 25% 仓位
    assert is_over_limit is False
    
    # 测试超限
    is_over_limit = risk_manager.check_position_limit(350.0, 1000.0)  # 35% 仓位
    assert is_over_limit is True


def test_risk_manager_reset():
    """测试重置功能"""
    risk_manager = RiskManager()
    
    # 先触发一些状态
    risk_manager.check_conditions(
        current_pnl_percent=0.16,
        current_price=52000.0,
        current_equity=1160.0,
        market_data={'market_state': '上升趋势'}
    )
    
    # 检查状态已设置
    trailing_info = risk_manager.get_trailing_info()
    assert trailing_info['active'] is True
    
    # 重置
    risk_manager.reset()
    
    # 检查状态已重置
    trailing_info = risk_manager.get_trailing_info()
    assert trailing_info['active'] is False
    assert trailing_info['triggered'] is False
    assert trailing_info['peak_price'] is None
    
    emergency_info = risk_manager.get_emergency_info()
    assert emergency_info['paused'] is False
    assert emergency_info['reason'] is None


def test_risk_manager_is_safe_to_trade():
    """测试is_safe_to_trade方法"""
    risk_manager = RiskManager()
    
    # 初始状态应该是安全的
    assert risk_manager.is_safe_to_trade() is True
    
    # 触发移动止盈
    risk_manager.check_conditions(
        current_pnl_percent=0.16,
        current_price=52000.0,
        current_equity=1160.0,
        market_data={'market_state': '上升趋势'}
    )
    # 激活移动止盈但未触发，仍然安全
    assert risk_manager.is_safe_to_trade() is True
    
    # 触发紧急暂停
    for i in range(3):
        risk_manager.check_conditions(
            current_pnl_percent=0.05,
            current_price=50000.0,
            current_equity=1050.0,
            system_data={'api_error': True}
        )
    # 紧急暂停后不安全
    assert risk_manager.is_safe_to_trade() is False
