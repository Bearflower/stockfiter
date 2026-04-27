"""
指标收集器
收集系统运行指标：交易次数、利润率、运行时长等
"""
from typing import Dict, Any
import time
from dataclasses import dataclass, field


@dataclass
class TradingMetrics:
    """交易指标数据类"""
    total_trades: int = 0
    successful_trades: int = 0
    failed_trades: int = 0
    total_profit: float = 0.0
    start_time: float = field(default_factory=time.time)

    @property
    def success_rate(self) -> float:
        """计算成功率"""
        if self.total_trades == 0:
            return 0.0
        return self.successful_trades / self.total_trades

    @property
    def runtime_hours(self) -> float:
        """计算运行时长（小时）"""
        return (time.time() - self.start_time) / 3600


class MetricsCollector:
    """指标收集器"""

    def __init__(self):
        """初始化指标收集器"""
        self.metrics = TradingMetrics()

    def record_trade(self, success: bool, profit: float = 0.0) -> None:
        """
        记录交易

        Args:
            success: 是否成功
            profit: 利润
        """
        self.metrics.total_trades += 1
        if success:
            self.metrics.successful_trades += 1
        else:
            self.metrics.failed_trades += 1
        self.metrics.total_profit += profit

    def get_metrics(self) -> Dict[str, Any]:
        """获取当前指标"""
        return {
            "total_trades": self.metrics.total_trades,
            "successful_trades": self.metrics.successful_trades,
            "failed_trades": self.metrics.failed_trades,
            "total_profit": self.metrics.total_profit,
            "success_rate": self.metrics.success_rate,
            "runtime_hours": self.metrics.runtime_hours
        }
