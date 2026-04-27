"""
参数对比器
对比新旧网格参数，判断是否需要推送信号
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional

from src.core.strategy.grid_calculator import GridParameters

logger = logging.getLogger(__name__)


@dataclass
class ParameterChange:
    """参数变化"""
    param_name: str
    old_value: float
    new_value: float
    change_percent: float
    is_significant: bool  # 是否显著变化


class ParameterComparator:
    """参数对比器"""

    def __init__(
        self,
        grid_width_change_threshold: float = 0.05,
        grid_count_change_threshold: float = 0.10,
        atr_change_threshold: float = 0.20,
        profit_rate_warning_threshold: float = 0.012
    ):
        """
        初始化参数对比器

        Args:
            grid_width_change_threshold: 网格宽度变化阈值
            grid_count_change_threshold: 网格数量变化阈值
            atr_change_threshold: ATR 变化阈值
            profit_rate_warning_threshold: 利润率警告阈值
        """
        self.grid_width_change_threshold = grid_width_change_threshold
        self.grid_count_change_threshold = grid_count_change_threshold
        self.atr_change_threshold = atr_change_threshold
        self.profit_rate_warning_threshold = profit_rate_warning_threshold

    def compare(
        self,
        old_params: Optional[GridParameters],
        new_params: GridParameters,
        old_atr: Optional[float],
        new_atr: float
    ) -> List[ParameterChange]:
        """
        对比新旧参数

        Args:
            old_params: 旧网格参数
            new_params: 新网格参数
            old_atr: 旧 ATR 值
            new_atr: 新 ATR 值

        Returns:
            参数变化列表
        """
        changes = []

        # 如果没有旧参数，返回空列表
        if old_params is None:
            logger.info("首次运行，无历史参数对比")
            return changes

        # 1. 对比网格宽度
        old_width = old_params.upper_price - old_params.lower_price
        new_width = new_params.upper_price - new_params.lower_price
        width_change = abs(new_width - old_width) / old_width

        if width_change > self.grid_width_change_threshold:
            changes.append(ParameterChange(
                param_name="grid_width",
                old_value=old_width,
                new_value=new_width,
                change_percent=width_change,
                is_significant=True
            ))
            logger.info(f"网格宽度变化 {width_change*100:.1f}% > {self.grid_width_change_threshold*100}%")

        # 2. 对比网格数量
        count_change = abs(new_params.grid_count - old_params.grid_count) / old_params.grid_count

        if count_change > self.grid_count_change_threshold:
            changes.append(ParameterChange(
                param_name="grid_count",
                old_value=old_params.grid_count,
                new_value=new_params.grid_count,
                change_percent=count_change,
                is_significant=True
            ))
            logger.info(f"网格数量变化 {count_change*100:.1f}% > {self.grid_count_change_threshold*100}%")

        # 3. 对比 ATR
        if old_atr is not None:
            atr_change = abs(new_atr - old_atr) / old_atr

            if atr_change > self.atr_change_threshold:
                changes.append(ParameterChange(
                    param_name="atr",
                    old_value=old_atr,
                    new_value=new_atr,
                    change_percent=atr_change,
                    is_significant=True
                ))
                logger.info(f"ATR 变化 {atr_change*100:.1f}% > {self.atr_change_threshold*100}%")

        # 4. 检查利润率
        if new_params.profit_rate and new_params.profit_rate < self.profit_rate_warning_threshold:
            changes.append(ParameterChange(
                param_name="profit_rate",
                old_value=old_params.profit_rate if old_params.profit_rate else 0,
                new_value=new_params.profit_rate,
                change_percent=0,
                is_significant=True
            ))
            logger.warning(
                f"每格利润率 {new_params.profit_rate*100:.2f}% < "
                f"{self.profit_rate_warning_threshold*100:.1f}%"
            )

        return changes

    def should_notify(
        self,
        changes: List[ParameterChange],
        market_state_changed: bool = False
    ) -> bool:
        """
        判断是否需要推送通知

        Args:
            changes: 参数变化列表
            market_state_changed: 市场状态是否变化

        Returns:
            是否需要推送
        """
        # 如果市场状态变化，必须推送
        if market_state_changed:
            logger.info("市场状态变化，需要推送")
            return True

        # 如果有显著变化，需要推送
        significant_changes = [c for c in changes if c.is_significant]
        if significant_changes:
            logger.info(f"检测到 {len(significant_changes)} 个显著变化，需要推送")
            return True

        logger.info("无显著变化，无需推送")
        return False
