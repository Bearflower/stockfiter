"""
订单管理器
管理网格订单的挂单、撤销、成交处理
"""
from datetime import datetime
from decimal import Decimal
from typing import Dict, List, Optional
import structlog

from shared.binance_api import BinanceClient
from shared.notification import NotificationClient
from shared.database import DatabaseManager
from .grid_calculator import GridLevel


logger = structlog.get_logger()


class OrderManager:
    """
    订单管理器

    负责管理网格策略的所有订单，包括：
    - 挂单：在网格层级挂限价单
    - 撤单：撤销指定或所有订单
    - 订单跟踪：跟踪订单状态
    - 成交处理：处理订单成交事件
    """

    def __init__(
        self,
        binance_client: BinanceClient,
        db: DatabaseManager,
        notification_client: NotificationClient,
        config: dict
    ):
        """
        初始化订单管理器

        Args:
            binance_client: 币安API客户端
            db: 数据库管理器
            notification_client: 通知服务客户端
            config: 配置字典

        Raises:
            ValueError: 参数验证失败
        """
        if not isinstance(config, dict):
            raise ValueError(f"配置必须是字典类型，实际为 {type(config).__name__}")

        self.binance = binance_client
        self.db = db
        self.notification = notification_client
        self.config = config

        # 订单状态
        self.pending_orders: Dict[int, dict] = {}  # 待成交订单
        self.filled_orders: Dict[int, dict] = {}   # 已成交订单

        # 交易配置
        trading_config = config.get('trading', {})
        self.time_in_force = trading_config.get('time_in_force', 'GTC')

        logger.info(
            "订单管理器初始化",
            pending_orders=len(self.pending_orders),
            filled_orders=len(self.filled_orders)
        )

    async def place_grid_order(
        self,
        symbol: str,
        level: GridLevel
    ) -> Optional[dict]:
        """
        挂网格单

        Args:
            symbol: 交易对
            level: 网格层级

        Returns:
            订单信息字典，失败返回None

        Raises:
            ValueError: 参数验证失败
        """
        # 参数验证
        if not symbol or not isinstance(symbol, str):
            raise ValueError(f"交易对必须是非空字符串，实际为 {symbol}")

        if not isinstance(level, GridLevel):
            raise ValueError(f"level 必须是 GridLevel 类型，实际为 {type(level).__name__}")

        # 跳过HOLD方向
        if level.side == 'HOLD':
            logger.debug(f"跳过HOLD方向的网格层级: {level.price}")
            return None

        try:
            logger.info(
                f"挂网格单: {symbol}",
                side=level.side,
                quantity=float(level.quantity),
                price=float(level.price)
            )

            # 调用币安API下单
            order = await self.binance.place_order(
                symbol=symbol,
                side=level.side,
                quantity=level.quantity,
                price=level.price,
                order_type='LIMIT',
                timeInForce=self.time_in_force
            )

            # 保存到内存
            order_info = {
                'order': order,
                'level': level,
                'symbol': symbol,
                'created_at': datetime.now().isoformat()
            }
            self.pending_orders[order['orderId']] = order_info

            # 保存到数据库
            if self.db:
                order_data = {
                    'order_id': order['orderId'],
                    'symbol': symbol,
                    'side': level.side,
                    'quantity': str(level.quantity),
                    'price': str(level.price),
                    'status': 'NEW',
                    'strategy': 'grid',
                    'created_at': datetime.now()
                }
                # await self.db.insert_order(order_data)

            logger.info(
                f"网格单已挂出: {symbol}",
                order_id=order['orderId'],
                side=level.side,
                quantity=float(level.quantity),
                price=float(level.price)
            )

            return order

        except Exception as e:
            logger.error(
                f"挂网格单失败: {symbol}",
                error=str(e),
                exc_info=True
            )
            return None

    async def cancel_all_orders(self, symbol: Optional[str] = None) -> int:
        """
        撤销所有订单

        Args:
            symbol: 交易对（可选，不指定则撤销所有）

        Returns:
            成功撤销的订单数量
        """
        cancelled_count = 0

        for order_id, order_info in list(self.pending_orders.items()):
            # 如果指定了交易对，只撤销该交易对的订单
            if symbol and order_info['symbol'] != symbol:
                continue

            try:
                # 调用币安API撤单
                await self.binance.cancel_order(
                    symbol=order_info['symbol'],
                    order_id=str(order_id)
                )

                # 从待成交列表移除
                del self.pending_orders[order_id]
                cancelled_count += 1

                logger.info(
                    f"订单已撤销: {order_info['symbol']}",
                    order_id=order_id
                )

            except Exception as e:
                logger.error(
                    f"撤销订单失败: {order_id}",
                    error=str(e),
                    exc_info=True
                )

        logger.info(
            "撤销订单完成",
            cancelled_count=cancelled_count,
            symbol=symbol or "所有交易对"
        )

        return cancelled_count

    async def on_order_filled(self, order: dict) -> Optional[dict]:
        """
        订单成交回调

        Args:
            order: 订单信息字典

        Returns:
            订单信息字典，用于触发反向挂单

        Raises:
            ValueError: 参数验证失败
        """
        if not isinstance(order, dict):
            raise ValueError(f"order 必须是字典类型，实际为 {type(order).__name__}")

        order_id = order.get('orderId')
        if not order_id:
            raise ValueError("订单信息缺少 orderId")

        # 检查是否是待成交订单
        if order_id not in self.pending_orders:
            logger.warning(
                f"未知订单成交: {order_id}",
                order=order
            )
            return None

        # 获取订单信息
        order_info = self.pending_orders[order_id]
        level = order_info['level']
        symbol = order_info['symbol']

        # 移动到已成交列表
        order_info['filled_at'] = datetime.now().isoformat()
        order_info['order'] = order
        self.filled_orders[order_id] = order_info

        # 从待成交列表移除
        del self.pending_orders[order_id]

        # 更新数据库
        if self.db:
            order_data = {
                'order_id': order_id,
                'status': 'FILLED',
                'filled_at': datetime.now()
            }
            # await self.db.update_order(order_data)

        # 发送通知
        if self.notification:
            try:
                await self.notification.send_trade_notification(
                    strategy="grid",
                    symbol=symbol,
                    action=level.side,
                    quantity=float(level.quantity),
                    price=float(level.price),
                    order_id=order_id
                )
            except Exception as e:
                logger.error(
                    f"发送订单成交通知失败: {order_id}",
                    error=str(e)
                )

        logger.info(
            f"网格单成交: {symbol}",
            order_id=order_id,
            side=level.side,
            quantity=float(level.quantity),
            price=float(level.price)
        )

        return order_info

    async def check_orders_status(self, symbol: str) -> List[dict]:
        """
        检查订单状态

        Args:
            symbol: 交易对

        Returns:
            成交的订单列表

        Raises:
            ValueError: 参数验证失败
        """
        if not symbol or not isinstance(symbol, str):
            raise ValueError(f"交易对必须是非空字符串，实际为 {symbol}")

        filled_orders = []

        try:
            # 获取未完成订单
            open_orders = await self.binance.get_open_orders(symbol)
            pending_order_ids = set(o['orderId'] for o in open_orders)

            # 检查是否有订单成交
            for order_id, order_info in list(self.pending_orders.items()):
                if order_info['symbol'] != symbol:
                    continue

                # 如果订单不在未完成列表中，说明已成交或取消
                if order_id not in pending_order_ids:
                    # 查询订单详情
                    try:
                        order_detail = await self.binance.get_order(
                            symbol=symbol,
                            order_id=str(order_id)
                        )

                        # 如果订单已成交
                        if order_detail.get('status') == 'FILLED':
                            filled_info = await self.on_order_filled(order_detail)
                            if filled_info:
                                filled_orders.append(filled_info)

                    except Exception as e:
                        logger.error(
                            f"查询订单详情失败: {order_id}",
                            error=str(e)
                        )

        except Exception as e:
            logger.error(
                f"检查订单状态失败: {symbol}",
                error=str(e),
                exc_info=True
            )

        return filled_orders

    def get_pending_orders(self, symbol: Optional[str] = None) -> List[dict]:
        """
        获取待成交订单

        Args:
            symbol: 交易对（可选）

        Returns:
            待成交订单列表
        """
        if symbol:
            return [o for o in self.pending_orders.values() if o['symbol'] == symbol]
        return list(self.pending_orders.values())

    def get_filled_orders(
        self,
        symbol: Optional[str] = None,
        limit: int = 100
    ) -> List[dict]:
        """
        获取已成交订单

        Args:
            symbol: 交易对（可选）
            limit: 返回数量限制

        Returns:
            已成交订单列表
        """
        orders = list(self.filled_orders.values())

        if symbol:
            orders = [o for o in orders if o['symbol'] == symbol]

        # 按时间倒序排列
        orders.sort(key=lambda x: x.get('filled_at', ''), reverse=True)

        return orders[:limit]

    def get_order_stats(self) -> dict:
        """
        获取订单统计信息

        Returns:
            订单统计字典
        """
        return {
            'pending_count': len(self.pending_orders),
            'filled_count': len(self.filled_orders),
            'total_count': len(self.pending_orders) + len(self.filled_orders)
        }
