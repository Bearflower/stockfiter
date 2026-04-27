"""
仓位验证器
验证网格参数的资金可行性，检查每格最小合约张数
"""

import logging
from dataclasses import dataclass
from typing import Dict, Optional

logger = logging.getLogger(__name__)


@dataclass
class PositionValidationResult:
    """仓位验证结果"""
    is_valid: bool  # 是否可行
    qty_per_grid: float  # 每格合约张数
    min_margin_required: float  # 最小所需保证金
    suggested_margin: Optional[float] = None  # 建议保证金
    suggested_grid_count: Optional[int] = None  # 建议网格数量
    message: str = ""  # 提示消息


class PositionValidator:
    """仓位验证器"""

    def __init__(self, min_qty_per_grid: int = 1):
        """
        初始化仓位验证器

        Args:
            min_qty_per_grid: 每格最小合约张数（币安要求≥1）
        """
        self.min_qty_per_grid = min_qty_per_grid

    def validate(
        self,
        current_price: float,
        grid_count: int,
        leverage: int,
        total_investment: float
    ) -> PositionValidationResult:
        """
        验证仓位可行性

        Args:
            current_price: 当前价格
            grid_count: 网格数量
            leverage: 杠杆倍数
            total_investment: 总投资金额

        Returns:
            验证结果
        """
        # 计算每格名义价值
        total_nominal = total_investment * leverage
        nominal_per_grid = total_nominal / grid_count

        # 计算每格合约张数
        qty_per_grid = nominal_per_grid / current_price

        # 计算最小所需保证金
        min_margin_required = (self.min_qty_per_grid * current_price * grid_count) / leverage

        # 判断是否可行
        is_valid = qty_per_grid >= self.min_qty_per_grid

        # 构建提示消息
        if is_valid:
            message = (
                f"✅ 每格合约数量：{qty_per_grid:.2f} 张（≥{self.min_qty_per_grid} 张）\n"
                f"总投资：{total_investment:.0f} USDT，杠杆：{leverage}x"
            )
            suggested_margin = None
            suggested_grid_count = None
        else:
            # 计算建议保证金
            suggested_margin = min_margin_required * 1.2  # 增加 20% 缓冲

            # 计算建议网格数量
            suggested_grid_count = int(total_nominal / (current_price * self.min_qty_per_grid))
            suggested_grid_count = max(5, suggested_grid_count)  # 最小 5 格

            message = (
                f"⚠️ 当前配置无法满足每格最小 {self.min_qty_per_grid} 张\n"
                f"每格合约数量：{qty_per_grid:.2f} 张\n"
                f"建议：\n"
                f"1. 增加保证金至 {suggested_margin:.0f} USDT，或\n"
                f"2. 减少网格数量至 {suggested_grid_count} 格"
            )

        logger.info(f"仓位验证：{'可行' if is_valid else '不可行'}, 每格 {qty_per_grid:.2f} 张")

        return PositionValidationResult(
            is_valid=is_valid,
            qty_per_grid=qty_per_grid,
            min_margin_required=min_margin_required,
            suggested_margin=suggested_margin,
            suggested_grid_count=suggested_grid_count,
            message=message
        )

    def get_min_margin_for_grid(
        self,
        current_price: float,
        grid_count: int,
        leverage: int
    ) -> float:
        """
        获取指定网格数量的最小所需保证金

        Args:
            current_price: 当前价格
            grid_count: 网格数量
            leverage: 杠杆倍数

        Returns:
            最小所需保证金
        """
        return (self.min_qty_per_grid * current_price * grid_count) / leverage
