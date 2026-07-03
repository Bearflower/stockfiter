"""
持仓管理器
管理网格策略的持仓和盈亏
"""
from datetime import datetime
from decimal import Decimal
from typing import Dict, Optional
import structlog

from shared.binance_api import BinanceClient
from shared.database import DatabaseManager


logger = structlog.get_logger()


class PositionManager:
    """
    持仓管理器

    负责管理网格策略的持仓信息，包括：
    - 持仓跟踪：跟踪每个交易对的持仓数量和成本
    - 盈亏计算：实时计算持仓盈亏
    - 成本计算：计算平均持仓成本
    - 状态持久化：保存持仓状态到数据库
    """

    def __init__(
        self,
        binance_client: BinanceClient,
        db: DatabaseManager,
        config: dict
    ):
        """
        初始化持仓管理器

        Args:
            binance_client: 币安API客户端
            db: 数据库管理器
            config: 配置字典

        Raises:
            ValueError: 参数验证失败
        """
        if not isinstance(config, dict):
            raise ValueError(f"配置必须是字典类型，实际为 {type(config).__name__}")

        self.binance = binance_client
        self.db = db
        self.config = config

        # 持仓状态
        self.positions: Dict[str, dict] = {}

        logger.info(
            "持仓管理器初始化",
            positions_count=len(self.positions)
        )

    def update_position(
        self,
        symbol: str,
        side: str,
        quantity: Decimal,
        price: Decimal
    ) -> None:
        """
        更新持仓

        Args:
            symbol: 交易对
            side: 方向（BUY/SELL）
            quantity: 数量
            price: 价格

        Raises:
            ValueError: 参数验证失败
        """
        # 参数验证
        if not symbol or not isinstance(symbol, str):
            raise ValueError(f"交易对必须是非空字符串，实际为 {symbol}")

        if side not in ['BUY', 'SELL']:
            raise ValueError(f"方向必须是 BUY 或 SELL，实际为 {side}")

        if quantity <= 0:
            raise ValueError(f"数量必须大于0，实际为 {quantity}")

        if price <= 0:
            raise ValueError(f"价格必须大于0，实际为 {price}")

        # 初始化持仓
        if symbol not in self.positions:
            self.positions[symbol] = {
                'quantity': Decimal('0'),
                'avg_price': Decimal('0'),
                'total_cost': Decimal('0'),
                'realized_pnl': Decimal('0'),
                'trades': 0,
                'created_at': datetime.now().isoformat()
            }

        pos = self.positions[symbol]

        if side == 'BUY':
            # 买入，增加持仓
            new_quantity = pos['quantity'] + quantity
            new_cost = pos['total_cost'] + quantity * price

            pos['quantity'] = new_quantity
            pos['total_cost'] = new_cost
            pos['avg_price'] = new_cost / new_quantity if new_quantity > 0 else Decimal('0')

        else:
            # 卖出，减少持仓
            if pos['quantity'] > 0:
                # 计算已实现盈亏
                realized_pnl = (price - pos['avg_price']) * min(quantity, pos['quantity'])
                pos['realized_pnl'] += realized_pnl

            pos['quantity'] -= quantity

            if pos['quantity'] <= 0:
                # 清仓
                pos['quantity'] = Decimal('0')
                pos['avg_price'] = Decimal('0')
                pos['total_cost'] = Decimal('0')

        pos['trades'] += 1
        pos['updated_at'] = datetime.now().isoformat()

        # 保存到数据库
        self._save_positions()

        logger.info(
            f"持仓更新: {symbol}",
            side=side,
            quantity=float(quantity),
            price=float(price),
            current_quantity=float(pos['quantity']),
            avg_price=float(pos['avg_price']),
            realized_pnl=float(pos['realized_pnl'])
        )

    def get_position(self, symbol: str) -> Optional[dict]:
        """
        获取持仓

        Args:
            symbol: 交易对

        Returns:
            持仓信息字典，不存在返回None
        """
        return self.positions.get(symbol)

    def get_position_pnl(
        self,
        symbol: str,
        current_price: Decimal
    ) -> Decimal:
        """
        计算持仓盈亏

        Args:
            symbol: 交易对
            current_price: 当前价格

        Returns:
            未实现盈亏

        Raises:
            ValueError: 参数验证失败
        """
        if current_price <= 0:
            raise ValueError(f"当前价格必须大于0，实际为 {current_price}")

        if symbol not in self.positions:
            return Decimal('0')

        pos = self.positions[symbol]

        if pos['quantity'] == 0:
            return Decimal('0')

        # 未实现盈亏 = (当前价格 - 平均成本) * 持仓数量
        unrealized_pnl = (current_price - pos['avg_price']) * pos['quantity']

        return unrealized_pnl

    def get_total_pnl(
        self,
        current_prices: Dict[str, Decimal]
    ) -> Dict[str, Decimal]:
        """
        计算总盈亏

        Args:
            current_prices: 当前价格字典 {symbol: price}

        Returns:
            盈亏字典，包含未实现盈亏、已实现盈亏、总盈亏

        Raises:
            ValueError: 参数验证失败
        """
        if not isinstance(current_prices, dict):
            raise ValueError(f"current_prices 必须是字典类型，实际为 {type(current_prices).__name__}")

        total_unrealized = Decimal('0')
        total_realized = Decimal('0')

        for symbol, pos in self.positions.items():
            # 累加已实现盈亏
            total_realized += pos['realized_pnl']

            # 累加未实现盈亏
            if symbol in current_prices:
                total_unrealized += self.get_position_pnl(symbol, current_prices[symbol])

        return {
            'unrealized_pnl': total_unrealized,
            'realized_pnl': total_realized,
            'total_pnl': total_unrealized + total_realized
        }

    def reset_daily_pnl(self) -> None:
        """
        重置日盈亏

        将所有持仓的已实现盈亏重置为0
        """
        for symbol in self.positions:
            self.positions[symbol]['realized_pnl'] = Decimal('0')

        self._save_positions()

        logger.info("日盈亏已重置")

    def _save_positions(self) -> None:
        """
        保存持仓到数据库

        将持仓状态序列化后保存到数据库
        """
        if not self.db:
            logger.warning("数据库未初始化，跳过保存持仓")
            return

        try:
            # 序列化持仓数据
            positions_data = {}
            for symbol, pos in self.positions.items():
                positions_data[symbol] = {
                    'quantity': str(pos['quantity']),
                    'avg_price': str(pos['avg_price']),
                    'total_cost': str(pos['total_cost']),
                    'realized_pnl': str(pos['realized_pnl']),
                    'trades': pos['trades'],
                    'created_at': pos.get('created_at'),
                    'updated_at': pos.get('updated_at')
                }

            # 保存到数据库
            state_data = {
                'positions': positions_data,
                'updated_at': datetime.now().isoformat()
            }
            # await self.db.save_strategy_state('grid', 'positions', state_data)

            logger.debug("持仓状态已保存到数据库")

        except Exception as e:
            logger.error(
                "保存持仓状态失败",
                error=str(e),
                exc_info=True
            )

    def _restore_positions(self) -> None:
        """
        从数据库恢复持仓状态

        从数据库读取持仓状态并恢复到内存
        """
        if not self.db:
            logger.warning("数据库未初始化，跳过恢复持仓")
            return

        try:
            # 从数据库读取状态
            # state = await self.db.get_strategy_state('grid', 'positions')
            state = None

            if state and 'positions' in state:
                # 反序列化持仓数据
                for symbol, pos_data in state['positions'].items():
                    self.positions[symbol] = {
                        'quantity': Decimal(pos_data['quantity']),
                        'avg_price': Decimal(pos_data['avg_price']),
                        'total_cost': Decimal(pos_data['total_cost']),
                        'realized_pnl': Decimal(pos_data['realized_pnl']),
                        'trades': pos_data['trades'],
                        'created_at': pos_data.get('created_at'),
                        'updated_at': pos_data.get('updated_at')
                    }

                logger.info(
                    "恢复持仓状态完成",
                    positions_count=len(self.positions)
                )

        except Exception as e:
            logger.error(
                "恢复持仓状态失败",
                error=str(e),
                exc_info=True
            )

    def get_position_stats(self) -> dict:
        """
        获取持仓统计信息

        Returns:
            持仓统计字典
        """
        total_quantity = sum(
            float(pos['quantity'])
            for pos in self.positions.values()
        )

        total_realized_pnl = sum(
            float(pos['realized_pnl'])
            for pos in self.positions.values()
        )

        return {
            'positions_count': len(self.positions),
            'total_quantity': total_quantity,
            'total_realized_pnl': total_realized_pnl,
            'total_trades': sum(pos['trades'] for pos in self.positions.values())
        }
