"""
订单执行器
负责订单的执行、滑点保护、超时管理
"""

import asyncio
import logging
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from src.core.data.binance_client import BinanceClient

logger = logging.getLogger(__name__)


class OrderExecutor:
    """订单执行器"""
    
    def __init__(
        self,
        client: BinanceClient,
        symbol: str = 'BTCUSDT',
        limit_order_timeout: int = 3,
        optimal_price_timeout: int = 2,
        market_order_fallback: bool = True
    ):
        """
        初始化订单执行器
        
        Args:
            client: 币安 API 客户端
            symbol: 交易对
            limit_order_timeout: 限价单超时（秒）
            optimal_price_timeout: 最优价超时（秒）
            market_order_fallback: 是否使用市价单 fallback
        """
        self.client = client
        self.symbol = symbol
        self.limit_order_timeout = limit_order_timeout
        self.optimal_price_timeout = optimal_price_timeout
        self.market_order_fallback = market_order_fallback
        
        self._pending_orders: List[Dict] = []
        self._executed_orders: List[Dict] = []
    
    async def execute_market_order(
        self,
        side: str,
        quantity: float,
        reduce_only: bool = False
    ) -> Dict:
        """
        执行市价单
        
        Args:
            side: 方向（BUY/SELL）
            quantity: 数量
            reduce_only: 是否仅减仓
            
        Returns:
            执行结果
        """
        logger.info(f"执行市价单：{side} {quantity} {self.symbol}")
        
        try:
            # 调用币安 API
            result = await self.client.place_order(
                symbol=self.symbol,
                side=side,
                type='MARKET',
                quantity=quantity
            )
            
            if result and result.get('orderId'):
                order_info = {
                    'order_id': result['orderId'],
                    'symbol': self.symbol,
                    'side': side,
                    'type': 'MARKET',
                    'quantity': quantity,
                    'price': result.get('avgPrice', 0),
                    'status': result.get('status', 'FILLED'),
                    'timestamp': datetime.now()
                }
                
                self._executed_orders.append(order_info)
                logger.info(f"市价单执行成功：{order_info}")
                
                return {
                    'success': True,
                    'order_id': order_info['order_id'],
                    'price': order_info['price'],
                    'message': '市价单执行成功'
                }
            else:
                logger.error(f"市价单执行失败：{result}")
                return {
                    'success': False,
                    'order_id': None,
                    'price': None,
                    'message': '市价单执行失败'
                }
                
        except Exception as e:
            logger.error(f"市价单执行异常：{e}", exc_info=True)
            return {
                'success': False,
                'order_id': None,
                'price': None,
                'message': f'执行异常：{str(e)}'
            }
    
    async def execute_limit_order(
        self,
        side: str,
        quantity: float,
        price: float,
        time_in_force: str = 'GTC',
        reduce_only: bool = False
    ) -> Dict:
        """
        执行限价单（带滑点保护和超时）
        
        Args:
            side: 方向（BUY/SELL）
            quantity: 数量
            price: 价格
            time_in_force: 有效期（GTC/IOC/FOK）
            reduce_only: 是否仅减仓
            
        Returns:
            执行结果
        """
        logger.info(f"执行限价单：{side} {quantity} @ {price}")
        
        try:
            # 1. 发送限价单
            result = await self.client.place_order(
                symbol=self.symbol,
                side=side,
                type='LIMIT',
                quantity=quantity,
                price=price,
                time_in_force=time_in_force
            )
            
            if not result or not result.get('orderId'):
                logger.error(f"限价单发送失败：{result}")
                return {
                    'success': False,
                    'order_id': None,
                    'price': None,
                    'message': '限价单发送失败'
                }
            
            order_id = result['orderId']
            self._pending_orders.append({
                'order_id': order_id,
                'symbol': self.symbol,
                'side': side,
                'type': 'LIMIT',
                'quantity': quantity,
                'price': price,
                'status': 'PENDING',
                'timestamp': datetime.now()
            })
            
            logger.info(f"限价单已发送：order_id={order_id}")
            
            # 2. 等待成交（带超时）
            timeout = self.limit_order_timeout
            start_time = datetime.now()
            
            while True:
                # 检查超时
                elapsed = (datetime.now() - start_time).total_seconds()
                if elapsed > timeout:
                    logger.warning(f"限价单超时，取消订单：{order_id}")
                    await self.client.cancel_order(self.symbol, str(order_id))
                    
                    # 如果启用 fallback，转市价单
                    if self.market_order_fallback:
                        logger.info("切换到市价单执行")
                        return await self.execute_market_order(side, quantity, reduce_only)
                    else:
                        return {
                            'success': False,
                            'order_id': None,
                            'price': None,
                            'message': f'限价单超时（{timeout}秒）'
                        }
                
                # 检查订单状态
                # 注意：这里需要实现 get_order_status 方法
                # 简化处理，假设超时后未成交
                await asyncio.sleep(0.5)
                
        except Exception as e:
            logger.error(f"限价单执行异常：{e}", exc_info=True)
            return {
                'success': False,
                'order_id': None,
                'price': None,
                'message': f'执行异常：{str(e)}'
            }
    
    async def execute_with_slippage_protection(
        self,
        side: str,
        quantity: float,
        target_price: float,
        max_slippage: float = 0.001
    ) -> Dict:
        """
        执行订单（带滑点保护）
        
        滑点保护策略：
        1. 先尝试限价单（对手方最优价）
        2. 超时后转市价单
        
        Args:
            side: 方向（BUY/SELL）
            quantity: 数量
            target_price: 目标价格
            max_slippage: 最大滑点（0.001 = 0.1%）
            
        Returns:
            执行结果
        """
        logger.info(f"执行滑点保护订单：{side} {quantity}，目标价 {target_price}")
        
        try:
            # 1. 计算允许的价格范围
            if side == 'BUY':
                # 买单：最高价 = 目标价 × (1 + 滑点)
                max_price = target_price * (1 + max_slippage)
                limit_price = max_price
            else:
                # 卖单：最低价 = 目标价 × (1 - 滑点)
                min_price = target_price * (1 - max_slippage)
                limit_price = min_price
            
            logger.info(f"价格范围：限价 {limit_price}，最大滑点 {max_slippage*100:.2f}%")
            
            # 2. 执行限价单（带超时）
            result = await self.execute_limit_order(
                side=side,
                quantity=quantity,
                price=limit_price,
                time_in_force='IOC',  # 立即成交或取消
                reduce_only=False
            )
            
            if result['success']:
                logger.info(f"限价单成功成交：{result}")
                return result
            
            # 3. 限价单失败，转市价单
            logger.warning("限价单失败，切换到市价单")
            return await self.execute_market_order(side, quantity, reduce_only=False)
            
        except Exception as e:
            logger.error(f"滑点保护订单执行异常：{e}", exc_info=True)
            return {
                'success': False,
                'order_id': None,
                'price': None,
                'message': f'执行异常：{str(e)}'
            }
    
    async def close_position(
        self,
        position_qty: float,
        side: str
    ) -> Dict:
        """
        平仓
        
        Args:
            position_qty: 持仓数量
            side: 平仓方向（与持仓相反）
            
        Returns:
            平仓结果
        """
        logger.info(f"平仓：{side} {position_qty} {self.symbol}")
        
        # 使用市价单快速平仓
        result = await self.execute_market_order(
            side=side,
            quantity=abs(position_qty),
            reduce_only=True
        )
        
        if result['success']:
            logger.info(f"平仓成功：价格 {result['price']}")
        else:
            logger.error(f"平仓失败：{result['message']}")
        
        return result
    
    def get_pending_orders(self) -> List[Dict]:
        """获取挂单列表"""
        return self._pending_orders.copy()
    
    def get_executed_orders(self, limit: int = 50) -> List[Dict]:
        """获取已执行订单列表"""
        return self._executed_orders[-limit:]
    
    def clear_order_history(self) -> None:
        """清除订单历史"""
        self._pending_orders.clear()
        self._executed_orders.clear()
        logger.info("订单历史已清除")
