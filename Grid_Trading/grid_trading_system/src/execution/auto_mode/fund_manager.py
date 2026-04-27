"""
资金管理器
负责理财账户赎回和资金划转
"""

import asyncio
import logging
from decimal import Decimal
from typing import Optional, Dict

from src.core.data.binance_client import BinanceClient

logger = logging.getLogger(__name__)


class FundManager:
    """资金管理器"""
    
    def __init__(self, client: BinanceClient):
        self.client = client
    
    async def get_spot_balance(self, asset: str = 'USDT') -> Decimal:
        """
        获取现货账户余额
        
        Args:
            asset: 资产类型
            
        Returns:
            可用余额
        """
        try:
            balance = await self.client.get_spot_balance(asset)
            return Decimal(str(balance)) if balance else Decimal('0')
        except Exception as e:
            logger.error(f"获取现货余额失败：{e}")
            return Decimal('0')
    
    async def get_umfut_balance(self, asset: str = 'USDT') -> Decimal:
        """
        获取合约账户余额
        
        Args:
            asset: 资产类型
            
        Returns:
            可用余额
        """
        try:
            balance = await self.client.get_umfut_balance(asset)
            return Decimal(str(balance))
        except Exception as e:
            logger.error(f"获取合约余额失败：{e}")
            return Decimal('0')
    
    async def redeem_simple_earn(
        self,
        product_id: str = 'USDT',
        asset: str = 'USDT'
    ) -> Optional[Dict]:
        """
        赎回赚币活期产品（暂未实现，需要额外API权限）
        
        Args:
            product_id: 产品 ID
            asset: 资产类型
            
        Returns:
            赎回结果
        """
        logger.warning("赚币赎回功能暂未实现")
        return None
    
    async def transfer_spot_to_umfut(
        self,
        asset: str = 'USDT',
        amount: Optional[Decimal] = None,
        min_reserve: Decimal = Decimal('100')
    ) -> bool:
        """
        现货账户转向合约账户（PM账户模式下不需要划转）
        
        Args:
            asset: 资产类型
            amount: 划转数量（None 表示全部，保留 min_reserve）
            min_reserve: 现货账户保留数量
            
        Returns:
            是否划转成功
        """
        logger.info("PM账户模式使用统一保证金，无需账户间划转")
        return True
    
    async def prepare_grid_funding(
        self,
        required_amount: Decimal,
        asset: str = 'USDT'
    ) -> bool:
        """
        准备网格资金（PM 账户模式）
        
        PM 账户是统一账户，资金共享，不需要划转。
        只需确保现货或合约账户有足够的可用余额即可。
        
        Args:
            required_amount: 需要的资金数量
            asset: 资产类型
            
        Returns:
            是否准备成功
        """
        try:
            logger.info(f"需要准备资金：{required_amount} {asset}")
            
            # 1. 检查合约账户余额
            umfut_balance = await self.get_umfut_balance(asset)
            
            if umfut_balance >= required_amount:
                logger.info(f"合约账户余额充足：{umfut_balance} {asset}")
                return True
            
            # 2. 检查现货账户余额（PM 账户资金共享）
            spot_balance = await self.get_spot_balance(asset)
            
            if spot_balance >= required_amount:
                logger.info(f"现货账户余额充足：{spot_balance} {asset}（PM 账户资金共享，无需划转）")
                return True
            
            # 3. 计算总可用余额
            total_balance = umfut_balance + spot_balance
            logger.info(f"总可用余额：{total_balance} {asset}（合约：{umfut_balance} + 现货：{spot_balance}）")
            
            if total_balance >= required_amount:
                logger.info("资金充足（PM 账户统一保证金模式）")
                return True
            
            # 4. 资金不足，尝试赎回赚币
            shortage = required_amount - total_balance
            logger.info(f"资金缺口：{shortage} {asset}")
            logger.info("尝试赎回赚币产品")
            
            redeem_result = await self.redeem_simple_earn(
                product_id=asset,
                asset=asset
            )
            
            if redeem_result:
                # 等待赎回到账（通常立即到账）
                await asyncio.sleep(2)
                
                # 再次检查余额
                new_spot_balance = await self.get_spot_balance(asset)
                new_total_balance = umfut_balance + new_spot_balance
                
                if new_total_balance >= required_amount:
                    logger.info(f"赎回成功，当前总余额：{new_total_balance} {asset}")
                    return True
            
            logger.warning("资金准备不足")
            return False
            
        except Exception as e:
            logger.error(f"资金准备失败：{e}")
            return False
    
    async def get_total_balance(self, asset: str = 'USDT') -> Dict:
        """
        获取总余额（现货 + 合约 + 赚币）
        
        Args:
            asset: 资产类型
            
        Returns:
            各账户余额
        """
        try:
            spot = await self.get_spot_balance(asset)
            umfut = await self.get_umfut_balance(asset)
            
            # 赚币余额需要查询
            earn_balance = Decimal('0')  # TODO: 查询赚币余额
            
            total = spot + umfut + earn_balance
            
            return {
                'spot': spot,
                'umfut': umfut,
                'earn': earn_balance,
                'total': total
            }
            
        except Exception as e:
            logger.error(f"获取总余额失败：{e}")
            return {
                'spot': Decimal('0'),
                'umfut': Decimal('0'),
                'earn': Decimal('0'),
                'total': Decimal('0')
            }
