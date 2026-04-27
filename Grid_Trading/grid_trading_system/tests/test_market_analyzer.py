import pytest
from src.core.strategy.market_analyzer import MarketAnalyzer, MarketState


def test_market_analyzer_ranging():
    """测试震荡市场状态识别"""
    analyzer = MarketAnalyzer()
    
    # 构建测试数据 - 震荡市场（ADX < 20）
    data_1h = [
        {
            'adx': 15.0,
            'ema_fast': 50000.0,
            'ema_slow': 50100.0
        }
    ]
    
    result = analyzer.analyze(data_1h)
    assert result.state == MarketState.RANGING
    assert result.confidence > 0.0
    assert result.adx == 15.0


def test_market_analyzer_uptrend():
    """测试上升趋势市场状态识别"""
    analyzer = MarketAnalyzer()
    
    # 构建测试数据 - 上升趋势（ADX > 25，EMA快线 > EMA慢线）
    data_1h = [
        {
            'adx': 30.0,
            'ema_fast': 50100.0,
            'ema_slow': 50000.0
        }
    ]
    
    result = analyzer.analyze(data_1h)
    assert result.state == MarketState.UPTREND
    assert result.confidence > 0.0
    assert result.adx == 30.0


def test_market_analyzer_downtrend():
    """测试下降趋势市场状态识别"""
    analyzer = MarketAnalyzer()
    
    # 构建测试数据 - 下降趋势（ADX > 25，EMA快线 < EMA慢线）
    data_1h = [
        {
            'adx': 30.0,
            'ema_fast': 49900.0,
            'ema_slow': 50000.0
        }
    ]
    
    result = analyzer.analyze(data_1h)
    assert result.state == MarketState.DOWNTREND
    assert result.confidence > 0.0
    assert result.adx == 30.0


def test_market_analyzer_strong_trend():
    """测试强趋势暂停状态识别"""
    analyzer = MarketAnalyzer()
    
    # 构建测试数据 - 强趋势（ADX >= 40）
    data_1h = [
        {
            'adx': 45.0,
            'ema_fast': 50100.0,
            'ema_slow': 50000.0
        }
    ]
    
    result = analyzer.analyze(data_1h)
    assert result.state == MarketState.STRONG_TREND
    assert result.confidence > 0.0
    assert result.adx == 45.0


def test_market_analyzer_multi_timeframe_confirm():
    """测试多时间框架确认功能"""
    analyzer = MarketAnalyzer(require_multi_timeframe_confirm=True)
    
    # 构建1H和4H测试数据
    data_1h = [
        {
            'adx': 30.0,
            'ema_fast': 50100.0,
            'ema_slow': 50000.0
        }
    ]
    
    data_4h = [
        {
            'adx': 35.0,
            'ema_fast': 50200.0,
            'ema_slow': 50000.0
        }
    ]
    
    result = analyzer.analyze(data_1h, data_4h)
    assert result.state == MarketState.UPTREND
    # 多时间框架确认应该提高置信度
    assert result.confidence > 0.7


def test_market_analyzer_trend_strength_calculation():
    """测试趋势强度计算"""
    analyzer = MarketAnalyzer()
    
    # 构建测试数据
    data_1h = [
        {
            'adx': 35.0,
            'ema_fast': 50100.0,
            'ema_slow': 50000.0
        }
    ]
    
    result = analyzer.analyze(data_1h)
    # 趋势强度计算公式：(ADX - 25) / 30，限制在0-0.5
    expected_strength = min(0.5, max(0.0, (35 - 25) / 30))
    assert abs(result.trend_strength - expected_strength) < 0.001


def test_market_analyzer_empty_data():
    """测试空数据情况"""
    analyzer = MarketAnalyzer()
    
    with pytest.raises(ValueError):
        analyzer.analyze([])


def test_market_analyzer_state_changes():
    """测试状态变化历史记录"""
    analyzer = MarketAnalyzer()
    
    # 第一次分析 - 震荡
    data_ranging = [{'adx': 15.0, 'ema_fast': 50000.0, 'ema_slow': 50100.0}]
    analyzer.analyze(data_ranging)
    
    # 第二次分析 - 上升趋势
    data_uptrend = [{'adx': 30.0, 'ema_fast': 50100.0, 'ema_slow': 50000.0}]
    analyzer.analyze(data_uptrend)
    
    # 检查状态变化历史
    changes = analyzer.get_state_changes()
    assert len(changes) == 1
    assert changes[0]['from_state'] == 'ranging'
    assert changes[0]['to_state'] == 'uptrend'


def test_market_analyzer_helper_methods():
    """测试辅助方法"""
    analyzer = MarketAnalyzer()
    
    # 测试上升趋势
    data_uptrend = [{'adx': 30.0, 'ema_fast': 50100.0, 'ema_slow': 50000.0}]
    analyzer.analyze(data_uptrend)
    assert analyzer.is_trending() is True
    assert analyzer.is_ranging() is False
    assert analyzer.is_strong_trend() is False
    assert analyzer.get_current_state() == MarketState.UPTREND
    
    # 测试震荡
    data_ranging = [{'adx': 15.0, 'ema_fast': 50000.0, 'ema_slow': 50100.0}]
    analyzer.analyze(data_ranging)
    assert analyzer.is_trending() is False
    assert analyzer.is_ranging() is True
    assert analyzer.is_strong_trend() is False
    assert analyzer.get_current_state() == MarketState.RANGING
    
    # 测试强趋势
    data_strong = [{'adx': 45.0, 'ema_fast': 50100.0, 'ema_slow': 50000.0}]
    analyzer.analyze(data_strong)
    assert analyzer.is_trending() is False
    assert analyzer.is_ranging() is False
    assert analyzer.is_strong_trend() is True
    assert analyzer.get_current_state() == MarketState.STRONG_TREND


def test_market_analyzer_clear_history():
    """测试清除历史记录"""
    analyzer = MarketAnalyzer()
    
    # 分析数据
    data = [{'adx': 30.0, 'ema_fast': 50100.0, 'ema_slow': 50000.0}]
    analyzer.analyze(data)
    assert analyzer.get_current_state() is not None
    
    # 清除历史
    analyzer.clear_history()
    assert analyzer.get_current_state() is None
    assert len(analyzer.get_state_changes()) == 0
