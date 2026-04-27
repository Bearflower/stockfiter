"""
网格订单执行器
通过币安合约 API 自主创建和管理网格订单
"""

import asyncio
import logging
from decimal import Decimal, ROUND_UP
from typing import Dict, List, Optional
from datetime import datetime

from src.core.data.binance_client import BinanceClient
from src.core.strategy.grid_calculator import GridParameters

logger = logging.getLogger(__name__)


class GridOrder:
    """网格订单"""
    
    def __init__(
        self,
        order_id: int,
        symbol: str,
        side: str,  # BUY/SELL
        position_side: str,  # LONG/SHORT
        price: Decimal,
        quantity: Decimal,
        grid_level: int
    ):
        self.order_id = order_id
        self.symbol = symbol
        self.side = side
        self.position_side = position_side
        self.price = price
        self.quantity = quantity
        self.grid_level = grid_level
        self.status = 'PENDING'
        self.create_time = datetime.now()
    
    def __repr__(self):
        return f"GridOrder(id={self.order_id}, {self.side}/{self.position_side}, {self.price}, {self.quantity})"


class GridExecutor:
    """网格订单执行器"""
    
    def __init__(self, client: BinanceClient):
        self.client = client
        self.active_grids: Dict[str, List[GridOrder]] = {}  # grid_id -> orders
        self.grid_params: Dict[str, GridParameters] = {}  # grid_id -> params
    
    async def create_grid_orders(
        self,
        grid_id: str,
        params: GridParameters,
        symbol: str = 'BTCUSDT'
    ) -> bool:
        """
        创建网格订单
        
        Args:
            grid_id: 网格 ID
            params: 网格参数
            symbol: 交易对
            
        Returns:
            是否创建成功
        """
        try:
            logger.info(f"开始创建网格：{grid_id}")
            
            # 1. 设置杠杆
            leverage = params.leverage
            logger.info(f"设置杠杆：{leverage}x")
            await self.client.set_leverage(symbol, leverage)
            
            # 2. 计算网格价格
            upper_price = Decimal(str(params.upper_price))
            lower_price = Decimal(str(params.lower_price))
            grid_count = params.grid_count
            grid_type = params.grid_type if hasattr(params, 'grid_type') else "arithmetic"
            
            # 等比网格 or 等差网格
            if grid_type == "geometric" and hasattr(params, 'grid_prices') and params.grid_prices:
                # 使用等比网格价格
                grid_prices = [Decimal(str(p)) for p in params.grid_prices]
                logger.info(f"创建等比网格：{grid_count}格，比率={params.geometric_ratio:.6f}")
            else:
                # 使用等差网格价格
                price_step = (upper_price - lower_price) / grid_count
                grid_prices = [lower_price + (price_step * i) for i in range(grid_count + 1)]
                logger.info(f"创建等差网格：{grid_count}格，间隔={price_step:.2f}")
            
            # 3. 计算每格投资金额
            total_investment = Decimal(str(params.total_investment))
            investment_per_grid = total_investment / grid_count
            
            # 4. 获取当前价格
            current_price = Decimal(await self.client.get_ticker_price(symbol))
            
            # 5. 创建买单（低于当前价）
            buy_orders = []
            for i in range(grid_count):
                buy_price = grid_prices[i]  # 使用等比或等差价格
                
                if buy_price >= current_price:
                    break
                
                # 计算买单数量
                quantity = investment_per_grid / buy_price
                
                # 格式化数量到 0.001 精度
                quantity = quantity.quantize(Decimal('0.001'), rounding=ROUND_UP)
                
                # 确保数量 >= 0.001（BTC 最小精度）
                if quantity < Decimal('0.001'):
                    logger.warning(f"网格 {i} 数量 {quantity} 小于最小精度 0.001，跳过")
                    continue
                
                # 检查名义价值是否 >= 100 USDT（PM 账户最小限制）
                notional_value = buy_price * quantity
                if notional_value < Decimal('100'):
                    # 自动调整数量，确保名义价值 >= 100
                    min_quantity = Decimal('100') / buy_price
                    logger.info(f"网格 {i} 名义价值 {notional_value:.2f} USDT < 100 USDT，调整为 {min_quantity.quantize(Decimal('0.001'))}")
                    quantity = min_quantity.quantize(Decimal('0.001'), rounding=ROUND_UP)
                
                # 限价买单
                # 注意：PM 账户使用单向持仓模式，必须使用 BOTH
                # BTCUSDT 的 tickSize=0.1，价格必须是 0.1 的整数倍
                order = await self.client.place_limit_order(
                    symbol=symbol,
                    side="BUY",
                    position_side="BOTH",  # PM 账户使用 BOTH
                    quantity=quantity,  # 数量已经是 0.001 精度
                    price=buy_price.quantize(Decimal('0.1')),  # BTC 价格精度为 0.1
                    time_in_force='GTC'
                )
                
                if order.get('code') == 0 or order.get('status') in ['NEW', 'PARTIALLY_FILLED']:
                    grid_order = GridOrder(
                        order_id=order['orderId'],
                        symbol=symbol,
                        side="BUY",
                        position_side="BOTH",
                        price=buy_price,
                        quantity=quantity,
                        grid_level=i
                    )
                    buy_orders.append(grid_order)
                    logger.info(f"买单已创建：{grid_order}")
            
            # 6. 创建卖单（高于当前价）
            sell_orders = []
            for i in range(grid_count, 0, -1):
                sell_price = grid_prices[i]  # 使用等比或等差价格
                
                if sell_price <= current_price:
                    continue
                
                # 计算卖单数量（假设有持仓）
                quantity = investment_per_grid / sell_price
                
                # 格式化数量到 0.001 精度
                quantity = quantity.quantize(Decimal('0.001'), rounding=ROUND_UP)
                
                # 确保数量 >= 0.001（BTC 最小精度）
                if quantity < Decimal('0.001'):
                    logger.warning(f"网格 {i} 数量 {quantity} 小于最小精度 0.001，跳过")
                    continue
                
                # 检查名义价值是否 >= 100 USDT（PM 账户最小限制）
                notional_value = sell_price * quantity
                if notional_value < Decimal('100'):
                    # 自动调整数量，确保名义价值 >= 100
                    min_quantity = Decimal('100') / sell_price
                    logger.info(f"网格 {i} 名义价值 {notional_value:.2f} USDT < 100 USDT，调整为 {min_quantity.quantize(Decimal('0.001'))}")
                    quantity = min_quantity.quantize(Decimal('0.001'), rounding=ROUND_UP)
                
                # 限价卖单
                # 注意：PM 账户使用单向持仓模式，必须使用 BOTH
                # BTCUSDT 的 tickSize=0.1，价格必须是 0.1 的整数倍
                order = await self.client.place_limit_order(
                    symbol=symbol,
                    side="SELL",
                    position_side="BOTH",  # PM 账户使用 BOTH
                    quantity=quantity,  # 数量已经是 0.001 精度
                    price=sell_price.quantize(Decimal('0.1')),  # BTC 价格精度为 0.1
                    time_in_force='GTC'
                )
                
                if order.get('code') == 0 or order.get('status') in ['NEW', 'PARTIALLY_FILLED']:
                    grid_order = GridOrder(
                        order_id=order['orderId'],
                        symbol=symbol,
                        side="SELL",
                        position_side="BOTH",
                        price=sell_price,
                        quantity=quantity,
                        grid_level=i
                    )
                    sell_orders.append(grid_order)
                    logger.info(f"卖单已创建：{grid_order}")
            
            # 7. 保存网格信息
            self.active_grids[grid_id] = buy_orders + sell_orders
            self.grid_params[grid_id] = params
            
            logger.info(f"网格创建完成：{grid_id}, 共 {len(self.active_grids[grid_id])} 个订单")
            return True
            
        except Exception as e:
            logger.error(f"创建网格失败：{e}")
            return False
    
    async def close_grid_orders(self, grid_id: str) -> bool:
        """
        平仓网格订单
        
        Args:
            grid_id: 网格 ID
            
        Returns:
            是否平仓成功
        """
        try:
            if grid_id not in self.active_grids:
                logger.warning(f"网格不存在：{grid_id}")
                return False
            
            orders = self.active_grids[grid_id]
            symbol = orders[0].symbol if orders else 'BTCUSDT'
            
            logger.info(f"开始平仓网格：{grid_id}")
            
            # 1. 撤销所有挂单
            for order in orders:
                if order.status == 'PENDING':
                    try:
                        await self.client.cancel_order(symbol, order.order_id)
                        logger.info(f"撤销订单：{order.order_id}")
                    except Exception as e:
                        logger.error(f"撤销订单失败：{e}")
            
            # 2. 平仓所有持仓
            positions = await self.client.get_umfut_balance()
            for pos in positions:
                if pos['symbol'] == symbol:
                    position_amt = Decimal(pos['positionAmt'])
                    
                    if position_amt != 0:
                        # 反向开单平仓
                        side = "SELL" if position_amt > 0 else "BUY"
                        position_side = pos['positionSide']
                        
                        order = await self.client.place_market_order(
                            symbol=symbol,
                            side=side,
                            position_side=position_side,
                            quantity=abs(position_amt).quantize(Decimal('0.001'))
                        )
                        
                        logger.info(f"平仓订单：{order['orderId']}, 数量：{abs(position_amt)}")
            
            # 3. 清理记录
            del self.active_grids[grid_id]
            del self.grid_params[grid_id]
            
            logger.info(f"网格已平仓：{grid_id}")
            return True
            
        except Exception as e:
            logger.error(f"平仓失败：{e}")
            return False
    
    async def adjust_grid(
        self,
        grid_id: str,
        new_params: GridParameters
    ) -> bool:
        """
        调整网格（先平仓再开新仓）
        
        Args:
            grid_id: 网格 ID
            new_params: 新网格参数
            
        Returns:
            是否调整成功
        """
        try:
            logger.info(f"开始调整网格：{grid_id}")
            
            # 1. 平仓旧网格
            success = await self.close_grid_orders(grid_id)
            if not success:
                logger.error("平仓失败，终止调整")
                return False
            
            # 等待 2 秒确保订单完成
            await asyncio.sleep(2)
            
            # 2. 创建新网格
            symbol = self.grid_params.get(grid_id, GridParameters(
                upper_price=70000,
                lower_price=68000,
                grid_count=30,
                grid_direction='NEUTRAL',
                total_investment=1000,
                leverage=10
            ))
            
            success = await self.create_grid_orders(
                grid_id=grid_id,
                params=new_params,
                symbol='BTCUSDT'
            )
            
            if success:
                logger.info(f"网格调整完成：{grid_id}")
                return True
            else:
                logger.error("创建新网格失败")
                return False
                
        except Exception as e:
            logger.error(f"调整网格失败：{e}")
            return False
    
    async def check_and_replenish_orders(self, grid_id: str, params: GridParameters, symbol: str = 'BTCUSDT') -> bool:
        """
        检查并补充已触发的订单
        
        Args:
            grid_id: 网格 ID
            params: 网格参数
            symbol: 交易对
            
        Returns:
            是否补充成功
        """
        try:
            if grid_id not in self.active_grids:
                logger.warning(f"网格不存在：{grid_id}")
                return False
            
            logger.info(f"\n📋 检查网格订单状态：{grid_id}")
            
            # 1. 获取当前未成交订单
            try:
                open_orders = await self.client.get_open_orders(symbol=symbol)
                open_order_ids = {order['orderId'] for order in open_orders}
            except Exception as e:
                logger.warning(f"获取未成交订单失败：{e}")
                open_order_ids = set()
            
            # 2. 检查哪些订单已触发（不在未成交列表中）
            orders = self.active_grids[grid_id]
            triggered_orders = []
            pending_orders = []
            
            for order in orders:
                if order.order_id in open_order_ids:
                    pending_orders.append(order)
                else:
                    # 检查是否已成交
                    try:
                        order_info = await self.client.get_order(symbol=symbol, order_id=order.order_id)
                        if order_info.get('status') == 'FILLED':
                            triggered_orders.append(order)
                            logger.info(f"✅ 订单已成交：{order.order_id} ({order.side} @ {order.price})")
                        else:
                            pending_orders.append(order)
                    except Exception:
                        # 找不到订单信息，认为已触发
                        triggered_orders.append(order)
                        logger.info(f"✅ 订单已触发：{order.order_id} ({order.side} @ {order.price})")
            
            if not triggered_orders:
                logger.info("✅ 所有订单均未触发，无需补充")
                return True
            
            logger.info(f"📊 已触发订单数：{len(triggered_orders)}, 未触发订单数：{len(pending_orders)}")
            
            # 3. 补充已触发的订单
            replenished = False
            upper_price = Decimal(str(params.upper_price))
            lower_price = Decimal(str(params.lower_price))
            grid_count = params.grid_count
            price_step = (upper_price - lower_price) / grid_count
            total_investment = Decimal(str(params.total_investment))
            investment_per_grid = total_investment / grid_count
            current_price = Decimal(await self.client.get_ticker_price(symbol))
            
            for triggered_order in triggered_orders:
                try:
                    # 重新创建相同价格的订单
                    quantity = investment_per_grid / triggered_order.price
                    quantity = quantity.quantize(Decimal('0.001'), rounding=ROUND_UP)
                    
                    if quantity < Decimal('0.001'):
                        logger.warning(f"数量 {quantity} 小于最小精度，跳过")
                        continue
                    
                    notional_value = triggered_order.price * quantity
                    if notional_value < Decimal('100'):
                        min_quantity = Decimal('100') / triggered_order.price
                        quantity = min_quantity.quantize(Decimal('0.001'), rounding=ROUND_UP)
                    
                    # 根据订单方向创建新订单
                    if triggered_order.side == "BUY":
                        new_order = await self.client.place_limit_order(
                            symbol=symbol,
                            side="BUY",
                            position_side="BOTH",
                            quantity=quantity,
                            price=triggered_order.price.quantize(Decimal('0.1')),
                            time_in_force='GTC'
                        )
                        logger.info(f"🔄 补充买单：{new_order['orderId']} @ {triggered_order.price}")
                    else:  # SELL
                        new_order = await self.client.place_limit_order(
                            symbol=symbol,
                            side="SELL",
                            position_side="BOTH",
                            quantity=quantity,
                            price=triggered_order.price.quantize(Decimal('0.1')),
                            time_in_force='GTC'
                        )
                        logger.info(f"🔄 补充卖单：{new_order['orderId']} @ {triggered_order.price}")
                    
                    if new_order.get('code') == 0 or new_order.get('status') in ['NEW', 'PARTIALLY_FILLED']:
                        # 更新订单记录
                        new_grid_order = GridOrder(
                            order_id=new_order['orderId'],
                            symbol=symbol,
                            side=triggered_order.side,
                            position_side="BOTH",
                            price=triggered_order.price,
                            quantity=quantity,
                            grid_level=triggered_order.grid_level
                        )
                        # 替换旧订单
                        orders.remove(triggered_order)
                        orders.append(new_grid_order)
                        replenished = True
                    
                except Exception as e:
                    logger.error(f"补充订单失败 {triggered_order.order_id}: {e}")
            
            if replenished:
                logger.info(f"✅ 订单补充完成，当前网格共 {len(orders)} 个订单")
                return True
            else:
                logger.info("ℹ️  没有订单需要补充")
                return True
                
        except Exception as e:
            logger.error(f"❌ 检查并补充订单失败：{e}")
            return False
    
    def get_grid_profit(self, grid_id: str) -> Decimal:
        """
        计算网格盈亏
        
        Args:
            grid_id: 网格 ID
            
        Returns:
            盈亏金额（USDT）
        """
        if grid_id not in self.active_grids:
            return Decimal('0')
        
        # TODO: 实现盈亏计算逻辑
        # 需要查询已成交订单和当前持仓
        return Decimal('0')
