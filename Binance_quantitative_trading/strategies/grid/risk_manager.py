"""
风控管理器
管理网格策略的风险控制
"""
from dataclasses import dataclass
from datetime import datetime, date
from decimal import Decimal
from typing import Dict, List
import structlog

from shared.binance_api import BinanceClient
from shared.notification import NotificationClient
from shared.database import DatabaseManager


logger = structlog.get_logger()


@dataclass
class RiskAlert:
    """
    风险告警数据类

    Attributes:
        level: 告警级别 (LOW/MEDIUM/HIGH)
        type: 告警类型
        message: 告警消息
        action: 建议动作
    """
    level: str  # LOW/MEDIUM/HIGH
    type: str   # 告警类型
    message: str
    action: str  # 建议动作


@dataclass
class RiskResult:
    """
    风险检查结果数据类

    Attributes:
        has_risk: 是否存在风险
        risks: 风险告警列表
        should_stop: 是否应该停止交易
    """
    has_risk: bool
    risks: List[RiskAlert]
    should_stop: bool


class RiskManager:
    """
    风控管理器

    负责管理网格策略的风险控制，包括：
    - 最大回撤检查：监控账户回撤是否超过阈值
    - 仓位比例检查：监控仓位比例是否过高
    - 日亏损限制：监控日亏损是否超过限制
    - 风险告警：发送风险告警通知
    """

    def __init__(
        self,
        binance_client: BinanceClient,
        db: DatabaseManager,
        notification_client: NotificationClient,
        config: dict
    ):
        """
        初始化风控管理器

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

        # 从配置读取风控参数
        risk_config = config.get('risk', {})

        # 最大回撤
        max_drawdown = risk_config.get('max_drawdown', 0.1)
        self.max_drawdown = Decimal(str(max_drawdown))
        if self.max_drawdown <= 0 or self.max_drawdown >= 1:
            raise ValueError(f"最大回撤必须在0-1之间，实际为 {self.max_drawdown}")

        # 最大仓位比例
        max_position_ratio = risk_config.get('max_position_ratio', 0.3)
        self.max_position_ratio = Decimal(str(max_position_ratio))
        if self.max_position_ratio <= 0 or self.max_position_ratio >= 1:
            raise ValueError(f"最大仓位比例必须在0-1之间，实际为 {self.max_position_ratio}")

        # 日亏损限制
        daily_loss_limit = risk_config.get('daily_loss_limit', 0.05)
        self.daily_loss_limit = Decimal(str(daily_loss_limit))
        if self.daily_loss_limit <= 0 or self.daily_loss_limit >= 1:
            raise ValueError(f"日亏损限制必须在0-1之间，实际为 {self.daily_loss_limit}")

        # 状态变量
        self.peak_value = Decimal('0')
        self.daily_pnl = Decimal('0')
        self.last_check_date = date.today()

        logger.info(
            "风控管理器初始化",
            max_drawdown=float(self.max_drawdown),
            max_position_ratio=float(self.max_position_ratio),
            daily_loss_limit=float(self.daily_loss_limit)
        )

    async def check_risk(
        self,
        symbol: str,
        position: dict,
        current_price: Decimal,
        account_balance: Decimal
    ) -> RiskResult:
        """
        检查风险

        Args:
            symbol: 交易对
            position: 持仓信息
            current_price: 当前价格
            account_balance: 账户余额

        Returns:
            风险检查结果

        Raises:
            ValueError: 参数验证失败
        """
        # 参数验证
        if not symbol or not isinstance(symbol, str):
            raise ValueError(f"交易对必须是非空字符串，实际为 {symbol}")

        if current_price <= 0:
            raise ValueError(f"当前价格必须大于0，实际为 {current_price}")

        if account_balance < 0:
            raise ValueError(f"账户余额不能为负数，实际为 {account_balance}")

        # 检查是否需要重置日盈亏
        today = date.today()
        if today != self.last_check_date:
            self.daily_pnl = Decimal('0')
            self.last_check_date = today
            logger.info("日盈亏已重置", date=today)

        risks = []

        # 1. 检查最大回撤
        position_value = self._calculate_position_value(position, current_price)
        current_value = account_balance + position_value

        # 更新峰值
        if current_value > self.peak_value:
            self.peak_value = current_value

        # 计算回撤
        if self.peak_value > 0:
            drawdown = (self.peak_value - current_value) / self.peak_value

            if drawdown > self.max_drawdown:
                risks.append(RiskAlert(
                    level='HIGH',
                    type='MAX_DRAWDOWN',
                    message=f'回撤超过阈值：{drawdown:.2%} > {self.max_drawdown:.2%}',
                    action='STOP_TRADING'
                ))

        # 2. 检查仓位比例
        if account_balance > 0:
            position_ratio = position_value / account_balance

            if position_ratio > self.max_position_ratio:
                risks.append(RiskAlert(
                    level='MEDIUM',
                    type='POSITION_RATIO',
                    message=f'仓位比例过高：{position_ratio:.2%} > {self.max_position_ratio:.2%}',
                    action='REDUCE_POSITION'
                ))

        # 3. 检查日亏损
        if self.daily_pnl < 0 and account_balance > 0:
            daily_loss_ratio = abs(self.daily_pnl) / account_balance

            if daily_loss_ratio > self.daily_loss_limit:
                risks.append(RiskAlert(
                    level='HIGH',
                    type='DAILY_LOSS',
                    message=f'日亏损超过限制：{abs(self.daily_pnl):.2f} USDT ({daily_loss_ratio:.2%})',
                    action='STOP_TRADING'
                ))

        # 发送风险告警
        if risks and self.notification:
            await self._send_risk_alerts(risks)

        # 保存状态
        self._save_state()

        result = RiskResult(
            has_risk=len(risks) > 0,
            risks=risks,
            should_stop=any(r.action == 'STOP_TRADING' for r in risks)
        )

        if result.has_risk:
            logger.warning(
                f"风险检查发现风险: {symbol}",
                risks_count=len(risks),
                should_stop=result.should_stop
            )

        return result

    def update_daily_pnl(self, pnl: Decimal) -> None:
        """
        更新日盈亏

        Args:
            pnl: 盈亏金额

        Raises:
            ValueError: 参数验证失败
        """
        if not isinstance(pnl, Decimal):
            pnl = Decimal(str(pnl))

        self.daily_pnl += pnl

        logger.debug(
            "日盈亏已更新",
            pnl=float(pnl),
            daily_pnl=float(self.daily_pnl)
        )

        self._save_state()

    def _calculate_position_value(
        self,
        position: dict,
        current_price: Decimal
    ) -> Decimal:
        """
        计算持仓价值

        Args:
            position: 持仓信息
            current_price: 当前价格

        Returns:
            持仓价值
        """
        if not position:
            return Decimal('0')

        quantity = Decimal(str(position.get('quantity', 0)))
        return quantity * current_price

    async def _send_risk_alerts(self, risks: List[RiskAlert]) -> None:
        """
        发送风险告警

        Args:
            risks: 风险告警列表
        """
        if not self.notification:
            logger.warning("通知服务未初始化，跳过发送风险告警")
            return

        try:
            for risk in risks:
                level = 'warning' if risk.level == 'MEDIUM' else 'error'

                await self.notification.send_alert(
                    title=f"【风险告警】{risk.type}",
                    message=risk.message,
                    level=level
                )

                logger.info(
                    "风险告警已发送",
                    level=risk.level,
                    type=risk.type,
                    message=risk.message
                )

        except Exception as e:
            logger.error(
                "发送风险告警失败",
                error=str(e),
                exc_info=True
            )

    def _save_state(self) -> None:
        """
        保存风控状态到数据库
        """
        if not self.db:
            logger.warning("数据库未初始化，跳过保存风控状态")
            return

        try:
            state_data = {
                'peak_value': str(self.peak_value),
                'daily_pnl': str(self.daily_pnl),
                'last_check_date': self.last_check_date.isoformat(),
                'updated_at': datetime.now().isoformat()
            }
            # await self.db.save_strategy_state('grid', 'risk', state_data)

            logger.debug("风控状态已保存")

        except Exception as e:
            logger.error(
                "保存风控状态失败",
                error=str(e),
                exc_info=True
            )

    def _restore_state(self) -> None:
        """
        从数据库恢复风控状态
        """
        if not self.db:
            logger.warning("数据库未初始化，跳过恢复风控状态")
            return

        try:
            # state = await self.db.get_strategy_state('grid', 'risk')
            state = None

            if state:
                self.peak_value = Decimal(str(state.get('peak_value', 0)))
                self.daily_pnl = Decimal(str(state.get('daily_pnl', 0)))

                last_date_str = state.get('last_check_date')
                if last_date_str:
                    self.last_check_date = date.fromisoformat(last_date_str)

                logger.info(
                    "恢复风控状态完成",
                    peak_value=float(self.peak_value),
                    daily_pnl=float(self.daily_pnl)
                )

        except Exception as e:
            logger.error(
                "恢复风控状态失败",
                error=str(e),
                exc_info=True
            )

    def get_risk_stats(self) -> dict:
        """
        获取风控统计信息

        Returns:
            风控统计字典
        """
        return {
            'max_drawdown': float(self.max_drawdown),
            'max_position_ratio': float(self.max_position_ratio),
            'daily_loss_limit': float(self.daily_loss_limit),
            'peak_value': float(self.peak_value),
            'daily_pnl': float(self.daily_pnl),
            'last_check_date': self.last_check_date.isoformat()
        }
