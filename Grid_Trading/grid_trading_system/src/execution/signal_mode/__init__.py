"""
信号模式 - 半自动信号灯交易
提供买卖信号建议，由人工确认执行

核心模块：
- SignalGenerator: 信号生成器，负责市场分析、参数计算和信号生成
- SignalManager: 信号管理器，负责信号生命周期管理
- ParameterComparator: 参数对比器，对比新旧网格参数变化
- PositionValidator: 仓位验证器，验证网格参数的资金可行性
"""

from src.execution.signal_mode.signal_generator import Signal, SignalGenerator
from src.execution.signal_mode.signal_manager import SignalManager, SignalStatus, SignalRecord
from src.execution.signal_mode.parameter_comparator import ParameterComparator, ParameterChange
from src.execution.signal_mode.position_validator import PositionValidator, PositionValidationResult

__all__ = [
    # 信号生成器
    'Signal',
    'SignalGenerator',
    # 信号管理器
    'SignalManager',
    'SignalStatus',
    'SignalRecord',
    # 参数对比器
    'ParameterComparator',
    'ParameterChange',
    # 仓位验证器
    'PositionValidator',
    'PositionValidationResult',
]
