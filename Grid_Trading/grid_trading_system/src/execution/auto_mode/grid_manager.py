"""
网格管理器
负责网格的创建、修改、终止等操作
"""

import asyncio
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from src.core.data.binance_client import BinanceClient
from src.core.strategy.grid_calculator import GridParameters
from src.core.strategy.market_analyzer import MarketState

logger = logging.getLogger(__name__)


class GridManager:
    """网格管理器"""
    
    def __init__(
        self,
        client: BinanceClient,
        db_manager=None,  # 暂时可选，后续可接入统一数据库
        symbol: str = 'BTCUSDT'
    ):
        """
        初始化网格管理器
        
        Args:
            client: 币安 REST API 客户端
            db_manager: 数据库管理器（可选）
            symbol: 交易对
        """
        self.client = client
        self.db_manager = db_manager
        self.symbol = symbol
        
        self._current_grid_id: Optional[str] = None
        self._current_params: Optional[GridParameters] = None
        self._grid_history: List[Dict] = []
    
    async def create_grid(
        self,
        params: GridParameters,
        wait_for_completion: bool = True
    ) -> Dict:
        """
        创建网格
        
        Args:
            params: 网格参数
            wait_for_completion: 是否等待创建完成
            
        Returns:
            创建结果
        """
        logger.info(f"开始创建网格：symbol={self.symbol}")
        logger.info(f"网格参数：{params.to_dict()}")
        
        try:
            # 调用币安 API 创建网格
            grid_params = params.to_dict()
            grid_params['symbol'] = self.symbol
            
            result = await self.client.create_grid(grid_params)
            
            if result['success']:
                self._current_grid_id = result['grid_id']
                self._current_params = params
                
                logger.info(f"网格创建成功：grid_id={self._current_grid_id}")
                
                # 记录到数据库
                await self._save_grid_history(
                    grid_id=self._current_grid_id,
                    params=params,
                    state='RUNNING'
                )
                
                # 等待创建完成
                if wait_for_completion:
                    logger.info("等待网格初始化完成...")
                    await asyncio.sleep(3)
                
                return {
                    'success': True,
                    'grid_id': self._current_grid_id,
                    'message': '网格创建成功',
                    'params': params.to_dict()
                }
            else:
                logger.error(f"网格创建失败：{result['message']}")
                return {
                    'success': False,
                    'grid_id': None,
                    'message': result['message'],
                    'params': None
                }
                
        except Exception as e:
            logger.error(f"创建网格异常：{e}", exc_info=True)
            return {
                'success': False,
                'grid_id': None,
                'message': f'创建异常：{str(e)}',
                'params': None
            }
    
    async def switch_grid(
        self,
        new_params: GridParameters,
        wait_for_completion: bool = True
    ) -> Dict:
        """
        切换网格（终止旧网格 + 创建新网格）
        
        Args:
            new_params: 新网格参数
            wait_for_completion: 是否等待完成
            
        Returns:
            切换结果
        """
        logger.info(f"开始切换网格：symbol={self.symbol}")
        
        if not self._current_grid_id:
            logger.warning("当前没有运行中的网格，直接创建新网格")
            return await self.create_grid(new_params, wait_for_completion)
        
        try:
            # 1. 终止旧网格
            logger.info(f"步骤 1: 终止旧网格 {self._current_grid_id}")
            terminate_result = await self.client.terminate_grid(
                self._current_grid_id,
                self.symbol
            )
            
            if not terminate_result['success']:
                logger.error(f"终止旧网格失败：{terminate_result['message']}")
                return {
                    'success': False,
                    'old_grid_profit': 0,
                    'new_grid_id': None,
                    'message': f'终止旧网格失败：{terminate_result["message"]}'
                }
            
            old_profit = terminate_result.get('profit', 0)
            logger.info(f"旧网格终止成功，实现盈亏：{old_profit} USDT")
            
            # 记录旧网格终止
            await self._update_grid_history(
                grid_id=self._current_grid_id,
                state='TERMINATED',
                pnl=old_profit
            )
            
            # 记录参数调整历史
            await self._save_parameter_adjustment(
                grid_id=self._current_grid_id,
                old_params=self._current_params.to_dict() if self._current_params else {},
                new_params=new_params.to_dict(),
                trigger_reason='SWITCH_GRID',
                adjustment_type='SWITCH'
            )
            
            # 2. 等待订单完成
            logger.info("步骤 2: 等待订单完成...")
            await asyncio.sleep(2)
            
            # 3. 创建新网格
            logger.info("步骤 3: 创建新网格")
            create_result = await self.create_grid(new_params, wait_for_completion)
            
            if not create_result['success']:
                logger.error(f"创建新网格失败：{create_result['message']}")
                return {
                    'success': False,
                    'old_grid_profit': old_profit,
                    'new_grid_id': None,
                    'message': f'创建新网格失败：{create_result["message"]}'
                }
            
            new_grid_id = create_result['grid_id']
            logger.info(f"新网格创建成功：grid_id={new_grid_id}")
            
            return {
                'success': True,
                'old_grid_profit': old_profit,
                'new_grid_id': new_grid_id,
                'message': '网格切换成功',
                'params': new_params.to_dict()
            }
            
        except Exception as e:
            logger.error(f"切换网格异常：{e}", exc_info=True)
            return {
                'success': False,
                'old_grid_profit': 0,
                'new_grid_id': None,
                'message': f'切换异常：{str(e)}'
            }
    
    async def terminate_grid(self, wait_for_completion: bool = True) -> Dict:
        """
        终止网格
        
        Args:
            wait_for_completion: 是否等待完成
            
        Returns:
            终止结果
        """
        if not self._current_grid_id:
            logger.warning("当前没有运行中的网格")
            return {
                'success': False,
                'profit': 0,
                'message': '没有运行中的网格'
            }
        
        logger.info(f"终止网格：grid_id={self._current_grid_id}")
        
        try:
            result = await self.client.terminate_grid(
                self._current_grid_id,
                self.symbol
            )
            
            if result['success']:
                profit = result.get('profit', 0)
                logger.info(f"网格终止成功，实现盈亏：{profit} USDT")
                
                # 记录到数据库
                await self._update_grid_history(
                    grid_id=self._current_grid_id,
                    state='TERMINATED',
                    pnl=profit
                )
                
                # 清除当前网格状态
                old_grid_id = self._current_grid_id
                self._current_grid_id = None
                self._current_params = None
                
                if wait_for_completion:
                    logger.info("等待网格终止完成...")
                    await asyncio.sleep(2)
                
                return {
                    'success': True,
                    'profit': profit,
                    'grid_id': old_grid_id,
                    'message': '网格终止成功'
                }
            else:
                logger.error(f"网格终止失败：{result['message']}")
                return {
                    'success': False,
                    'profit': 0,
                    'grid_id': self._current_grid_id,
                    'message': result['message']
                }
                
        except Exception as e:
            logger.error(f"终止网格异常：{e}", exc_info=True)
            return {
                'success': False,
                'profit': 0,
                'grid_id': self._current_grid_id,
                'message': f'终止异常：{str(e)}'
            }
    
    async def get_grid_status(self) -> Dict:
        """
        获取网格状态
        
        Returns:
            网格状态信息
        """
        if not self._current_grid_id:
            return {
                'success': False,
                'message': '没有运行中的网格',
                'data': None
            }
        
        try:
            result = await self.client.get_grid_status(
                self._current_grid_id,
                self.symbol
            )
            
            if result['success']:
                return {
                    'success': True,
                    'message': '查询成功',
                    'data': {
                        'grid_id': self._current_grid_id,
                        'params': self._current_params.to_dict() if self._current_params else None,
                        'exchange_data': result.get('data')
                    }
                }
            else:
                return {
                    'success': False,
                    'message': result['message'],
                    'data': None
                }
                
        except Exception as e:
            logger.error(f"查询网格状态异常：{e}")
            return {
                'success': False,
                'message': f'查询异常：{str(e)}',
                'data': None
            }
    
    async def _save_grid_history(
        self,
        grid_id: str,
        params: GridParameters,
        state: str
    ) -> None:
        """保存网格历史到数据库"""
        try:
            await self.db_manager.insert_grid_history({
                'grid_id': grid_id,
                'symbol': self.symbol,
                'upper_price': params.upper_price,
                'lower_price': params.lower_price,
                'grid_count': params.grid_count,
                'investment': params.total_investment,
                'state': state,
                'market_state': None,
                'created_at': datetime.now(),
                'terminated_at': None,
                'pnl': 0
            })
            logger.debug(f"网格历史已保存：grid_id={grid_id}")
        except Exception as e:
            logger.error(f"保存网格历史失败：{e}")
    
    async def _update_grid_history(
        self,
        grid_id: str,
        state: str,
        pnl: float = 0
    ) -> None:
        """更新网格历史"""
        try:
            # 注意：这里需要实现 update_grid_history 方法
            # 简化处理，插入新记录
            await self.db_manager.insert_grid_history({
                'grid_id': grid_id,
                'symbol': self.symbol,
                'upper_price': 0,
                'lower_price': 0,
                'grid_count': 0,
                'investment': 0,
                'state': state,
                'market_state': None,
                'created_at': datetime.now(),
                'terminated_at': datetime.now() if state == 'TERMINATED' else None,
                'pnl': pnl
            })
            logger.debug(f"网格状态已更新：grid_id={grid_id}, state={state}, pnl={pnl}")
        except Exception as e:
            logger.error(f"更新网格历史失败：{e}")
    
    async def _save_parameter_adjustment(
        self,
        grid_id: str,
        old_params: Dict,
        new_params: Dict,
        trigger_reason: str,
        adjustment_type: str = 'SWITCH'
    ) -> None:
        """保存参数调整历史"""
        try:
            # 记录每个参数的变化
            for param_name in ['upper_price', 'lower_price', 'grid_count']:
                old_value = old_params.get(param_name)
                new_value = new_params.get(param_name)
                
                if old_value != new_value:
                    await self.db_manager.insert_parameter_adjustment({
                        'grid_id': grid_id,
                        'timestamp': datetime.now(),
                        'parameter_name': param_name,
                        'old_value': old_value,
                        'new_value': new_value,
                        'trigger_reason': trigger_reason,
                        'market_state': None,
                        'atr_value': None,
                        'adjustment_type': adjustment_type,
                        'details': f'{param_name}: {old_value} → {new_value}'
                    })
            
            logger.debug(f"参数调整历史已保存：grid_id={grid_id}")
        except Exception as e:
            logger.error(f"保存参数调整历史失败：{e}")
    
    def get_current_grid_id(self) -> Optional[str]:
        """获取当前网格 ID"""
        return self._current_grid_id
    
    def get_current_params(self) -> Optional[GridParameters]:
        """获取当前网格参数"""
        return self._current_params
    
    def has_active_grid(self) -> bool:
        """检查是否有运行中的网格"""
        return self._current_grid_id is not None
    
    async def close(self) -> None:
        """关闭管理器（可选：自动终止网格）"""
        if self.has_active_grid():
            logger.warning("关闭管理器时有运行中的网格，不自动终止")