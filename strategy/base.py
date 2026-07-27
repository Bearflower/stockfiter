"""
策略基类模块
定义策略的标准接口：DataRequirement、Signal、BaseStrategy
所有策略必须继承 BaseStrategy 并实现抽象方法
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, List, Optional
import pandas as pd


@dataclass
class DataRequirement:
    """
    数据需求声明
    策略通过 declare_data_requirements 声明需要哪些数据
    由调度器/引擎统一获取后注入到 analyze 方法
    """
    code: str
    """股票代码（不带后缀，如 600519）"""
    frequency: str = 'd'
    """K 线频率：d=日线, w=周线, m=月线"""
    lookback: int = 120
    """回溯天数/条数"""


@dataclass
class Signal:
    """
    策略信号
    策略 analyze 方法返回的信号对象
    """
    code: str
    """股票代码"""
    signal_type: str = 'buy'
    """信号类型：buy/sell/hold"""
    signal_date: str = ""
    """信号日期，格式 YYYY-MM-DD"""
    strategy_name: str = ""
    """策略名称"""
    strategy_version: str = ""
    """策略版本"""
    detail: Dict = field(default_factory=dict)
    """信号详情，包含支撑位、放量日期等"""
    score: float = 0.0
    """信号评分（0-100）"""
    backtest_params: Dict = field(default_factory=dict)
    """回测参数（由策略提供，供回测引擎使用）"""

    def to_dict(self) -> Dict:
        """将信号转为字典，方便序列化"""
        return {
            'code': self.code,
            'signal_type': self.signal_type,
            'signal_date': self.signal_date,
            'strategy_name': self.strategy_name,
            'strategy_version': self.strategy_version,
            'detail': self.detail,
            'score': self.score,
            'backtest_params': self.backtest_params,
        }


class BaseStrategy(ABC):
    """
    策略基类
    所有策略必须继承此类并实现抽象方法。

    生命周期：
    1. 初始化 -> 加载 params
    2. declare_data_requirements -> 声明需要哪些股票的数据
    3. analyze(每个code) -> 逐只分析，返回 Signal 或 None
    4. score(可选) -> 对信号评分
    """

    # 子类必须设置的属性
    name: str = ""
    """策略名称，英文标识"""
    version: str = ""
    """策略版本"""
    description: str = ""
    """策略描述"""

    def __init__(self, params: Optional[Dict] = None):
        """
        初始化策略

        Args:
            params: 策略参数字典，覆盖默认参数
        """
        self.params = self._get_default_params()
        if params:
            self.params.update(params)

    @abstractmethod
    def _get_default_params(self) -> Dict:
        """
        获取策略默认参数
        子类实现，返回默认参数字典

        Returns:
            Dict: 默认参数
        """
        ...

    @abstractmethod
    def declare_data_requirements(self, codes: List[str]) -> List[DataRequirement]:
        """
        声明数据需求
        策略告诉引擎需要哪些股票的 K 线数据

        Args:
            codes: 股票代码列表

        Returns:
            List[DataRequirement]: 数据需求列表
        """
        ...

    @abstractmethod
    def analyze(self, code: str, data: Dict[str, pd.DataFrame]) -> Optional[Signal]:
        """
        分析单只股票，返回信号

        Args:
            code: 股票代码
            data: K 线数据字典，key 为频率标识（如 'd', 'w'），value 为 DataFrame

        Returns:
            Optional[Signal]: 如果产生信号则返回 Signal，否则返回 None
        """
        ...

    def score(self, signal: Signal) -> float:
        """
        对信号进行评分（可选重写）
        默认返回 0.0，子类可以重写实现自定义评分逻辑

        Args:
            signal: 信号对象

        Returns:
            float: 评分（0-100）
        """
        return 0.0

    def get_backtest_params(self) -> Dict:
        """
        获取回测参数（可选重写）
        返回策略特定的回测参数，供回测引擎使用

        Returns:
            Dict: 回测参数字典
        """
        return {}

    def __repr__(self) -> str:
        return f"{self.name}(v{self.version})"