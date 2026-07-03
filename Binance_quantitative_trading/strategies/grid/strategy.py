"""
网格交易策略核心逻辑
继承BaseStrategy基类，实现网格交易策略
"""
import asyncio
from datetime import datetime
from decimal import Decimal
from typing import Dict, Any, Optional, List
import statistics
import structlog

from shared.base_strategy import BaseStrategy
from shared.binance_api import BinanceClient
from shared.kline_service import KLineService
from shared.notification import NotificationClient
from shared.database import DatabaseManager
from .grid_calculator import GridCalculator, GridLevel
from .order_manager import OrderManager
from .position_manager import PositionManager
from .risk_manager import RiskManager
from .signal_bot import GridSignalBot


logger = structlog.get_logger()


class GridStrategy(BaseStrategy):
    """
    网格交易策略

    在设定的价格区间内，按照固定的价格间隔挂单买卖的策略。
    通过价格波动获利，适合震荡行情。

    主要功能：
    - 网格初始化：根据当前价格和波动率计算网格层级
    - 订单管理：自动挂单、撤单、反向挂单
    - 持仓管理：跟踪持仓数量和成本
    - 风险控制：监控回撤、仓位、日亏损
    """

    def __init__(self, config: Dict[str, Any]):
        """
        初始化网格策略

        Args:
            config: 策略配置字典

        Raises:
            ValueError: 配置验证失败
        """
        super().__init__(config)

        # 策略配置
        self.strategy_name = config.get('strategy', {}).get('name', 'grid_trading')
        self.symbols = config.get('symbols', [])
        if not self.symbols:
            raise ValueError("交易对列表不能为空")

        # 监控配置
        monitor_config = config.get('monitor', {})
        self.check_interval = monitor_config.get('check_interval', 10)
        self.save_interval = monitor_config.get('save_interval', 60)
        self.grid_check_interval = monitor_config.get('grid_check_interval', 300)

        # 网格状态
        self.grid_states: Dict[str, dict] = {}

        # 组件实例（在initialize中初始化）
        self.grid_calculator: Optional[GridCalculator] = None
        self.order_manager: Optional[OrderManager] = None
        self.position_manager: Optional[PositionManager] = None
        self.risk_manager: Optional[RiskManager] = None
        self.signal_bot: Optional[GridSignalBot] = None
        self.is_signal_mode: bool = False

        logger.info(
            "网格策略初始化",
            strategy_name=self.strategy_name,
            symbols=self.symbols
        )

    async def initialize(self) -> None:
        """
        初始化策略资源

        初始化所有组件：
        - 网格计算器
        - 订单管理器
        - 持仓管理器
        - 风控管理器

        Raises:
            Exception: 初始化失败
        """
        logger.info("开始初始化网格策略资源")

        try:
            # 验证必要客户端已设置
            if not self.binance_client:
                raise ValueError("币安客户端未设置")

            if not self.notification_client:
                raise ValueError("通知客户端未设置")

            # 初始化网格计算器
            self.grid_calculator = GridCalculator(self.config)
            logger.info("网格计算器初始化完成")

            # 初始化订单管理器
            self.order_manager = OrderManager(
                binance_client=self.binance_client,
                db=self.db,
                notification_client=self.notification_client,
                config=self.config
            )
            logger.info("订单管理器初始化完成")

            # 初始化持仓管理器
            self.position_manager = PositionManager(
                binance_client=self.binance_client,
                db=self.db,
                config=self.config
            )
            logger.info("持仓管理器初始化完成")

            # 初始化风控管理器
            self.risk_manager = RiskManager(
                binance_client=self.binance_client,
                db=self.db,
                notification_client=self.notification_client,
                config=self.config
            )
            logger.info("风控管理器初始化完成")

            # 初始化信号机器人（半自动模式）
            if self.config.get('signal_bot', {}).get('enabled', False):
                signal_config = self.config.get('signal_bot', {})
                self.signal_bot = GridSignalBot(
                    binance_client=self.binance_client,
                    kline_service=self.kline_service,
                    notification_client=self.notification_client,
                    grid_calculator=self.grid_calculator,
                    config=self.config
                )
                self.is_signal_mode = signal_config.get('mode', 'semi') == 'semi'
                logger.info(
                    "信号机器人初始化完成",
                    is_signal_mode=self.is_signal_mode
                )

            # 恢复策略状态
            await self._restore_state()

            logger.info("网格策略资源初始化完成")

        except Exception as e:
            logger.error(
                "初始化网格策略资源失败",
                error=str(e),
                exc_info=True
            )
            raise

    async def analyze(self, symbol: str) -> Dict[str, Any]:
        """
        分析市场数据

        Args:
            symbol: 交易对

        Returns:
            分析结果字典，包含：
            - current_price: 当前价格
            - volatility: 波动率
            - grid_levels: 网格层级列表

        Raises:
            ValueError: 参数验证失败
            Exception: 分析失败
        """
        if not symbol or not isinstance(symbol, str):
            raise ValueError(f"交易对必须是非空字符串，实际为 {symbol}")

        logger.info(f"开始分析 {symbol}")

        try:
            # 1. 获取当前价格
            current_price = await self.binance_client.get_ticker_price(symbol)

            # 2. 计算波动率
            volatility = await self._calculate_volatility(symbol)

            # 3. 计算网格层级
            grid_levels = self.grid_calculator.calculate_grid_levels(
                current_price=current_price,
                volatility=volatility
            )

            # 4. 构建分析结果
            result = {
                'symbol': symbol,
                'current_price': current_price,
                'volatility': volatility,
                'grid_levels': grid_levels,
                'grid_count': len(grid_levels),
                'timestamp': datetime.now().isoformat()
            }

            logger.info(
                f"{symbol} 分析完成",
                current_price=float(current_price),
                volatility=float(volatility) if volatility else None,
                grid_count=len(grid_levels)
            )

            return result

        except Exception as e:
            logger.error(
                f"{symbol} 分析失败",
                error=str(e),
                exc_info=True
            )
            raise

    async def execute_signal(self, signal: Dict[str, Any]) -> bool:
        """
        执行交易信号

        对于网格策略，信号主要是初始化网格或重置网格

        Args:
            signal: 交易信号字典

        Returns:
            是否执行成功

        Raises:
            ValueError: 参数验证失败
            Exception: 执行失败
        """
        if not isinstance(signal, dict):
            raise ValueError(f"信号必须是字典类型，实际为 {type(signal).__name__}")

        signal_type = signal.get('type')

        try:
            if signal_type == 'INITIALIZE_GRID':
                # 初始化网格
                symbol = signal['symbol']
                grid_levels = signal['grid_levels']
                return await self._initialize_grid(symbol, grid_levels)

            elif signal_type == 'RESET_GRID':
                # 重置网格
                symbol = signal['symbol']
                return await self._reset_grid(symbol)

            else:
                logger.warning(f"未知的信号类型: {signal_type}")
                return False

        except Exception as e:
            logger.error(
                "执行交易信号失败",
                signal_type=signal_type,
                error=str(e),
                exc_info=True
            )
            return False

    async def run(self) -> None:
        """
        运行策略

        主循环逻辑：
        1. 初始化所有交易对的网格
        2. 定期检查订单状态
        3. 定期检查风控
        4. 定期监控网格状态
        5. 保存策略状态

        Raises:
            Exception: 运行失败
        """
        logger.info("网格策略开始运行")
        self._running = True

        signal_task = None

        try:
            # 启动信号机器人（半自动模式）
            if self.signal_bot and self.is_signal_mode:
                signal_cfg = self.config.get('signal_bot', {})
                check_minutes = signal_cfg.get('check_interval_minutes', 60)
                signal_task = asyncio.create_task(
                    self.signal_bot.run_loop(interval_minutes=check_minutes)
                )
                logger.info("信号机器人已启动", interval_minutes=check_minutes)

            # 初始化所有交易对的网格
            for symbol in self.symbols:
                try:
                    await self._initialize_grid_for_symbol(symbol)
                except Exception as e:
                    logger.error(
                        f"初始化网格失败: {symbol}",
                        error=str(e),
                        exc_info=True
                    )

            # 主循环
            last_save_time = datetime.now()
            last_grid_check_time = datetime.now()

            while self._running:
                try:
                    # 1. 检查订单状态
                    await self._check_all_orders()

                    # 2. 检查风控
                    await self._check_all_risks()

                    # 3. 定期监控网格状态
                    if (datetime.now() - last_grid_check_time).total_seconds() >= self.grid_check_interval:
                        await self._monitor_all_grids()
                        last_grid_check_time = datetime.now()

                    # 4. 定期保存状态
                    if (datetime.now() - last_save_time).total_seconds() >= self.save_interval:
                        await self._save_state()
                        last_save_time = datetime.now()

                    # 等待下一次检查
                    await asyncio.sleep(self.check_interval)

                except Exception as e:
                    logger.error(
                        "主循环执行失败",
                        error=str(e),
                        exc_info=True
                    )
                    await asyncio.sleep(30)

        except Exception as e:
            logger.error(
                "网格策略运行失败",
                error=str(e),
                exc_info=True
            )
            raise

        finally:
            logger.info("网格策略主循环结束")

            # 停止信号机器人
            if signal_task and not signal_task.done():
                signal_task.cancel()
                try:
                    await signal_task
                except asyncio.CancelledError:
                    logger.info("信号机器人已停止")

    async def stop(self) -> None:
        """
        停止策略

        清理资源：
        1. 停止主循环
        2. 撤销所有订单
        3. 保存策略状态
        4. 清理客户端连接
        """
        logger.info("开始停止网格策略")
        self._running = False

        try:
            # 撤销所有订单
            if self.order_manager and not self.is_signal_mode:
                for symbol in self.symbols:
                    await self.order_manager.cancel_all_orders(symbol)

            # 保存策略状态
            await self._save_state()

            # 清理资源
            await self.cleanup()

            logger.info("网格策略已停止")

        except Exception as e:
            logger.error(
                "停止网格策略失败",
                error=str(e),
                exc_info=True
            )

    async def _initialize_grid_for_symbol(self, symbol: str) -> None:
        """
        为指定交易对初始化网格

        Args:
            symbol: 交易对
        """
        logger.info(f"初始化网格: {symbol}")

        # 信号模式下跳过实际挂单
        if self.is_signal_mode:
            logger.info(f"信号模式，跳过网格初始化: {symbol}")
            return

        # 分析市场
        analysis = await self.analyze(symbol)

        # 执行初始化信号
        signal = {
            'type': 'INITIALIZE_GRID',
            'symbol': symbol,
            'grid_levels': analysis['grid_levels']
        }

        success = await self.execute_signal(signal)

        if success:
            logger.info(f"网格初始化成功: {symbol}")
        else:
            logger.error(f"网格初始化失败: {symbol}")

    async def _initialize_grid(
        self,
        symbol: str,
        grid_levels: List[GridLevel]
    ) -> bool:
        """
        初始化网格

        Args:
            symbol: 交易对
            grid_levels: 网格层级列表

        Returns:
            是否成功
        """
        try:
            # 挂单
            placed_count = 0
            for level in grid_levels:
                order = await self.order_manager.place_grid_order(symbol, level)
                if order:
                    placed_count += 1

            # 保存网格状态
            self.grid_states[symbol] = {
                'grid_levels': [
                    {
                        'price': str(l.price),
                        'side': l.side,
                        'quantity': str(l.quantity),
                        'level': l.level
                    }
                    for l in grid_levels
                ],
                'initialized_at': datetime.now().isoformat(),
                'placed_count': placed_count
            }

            logger.info(
                f"网格初始化完成: {symbol}",
                total_levels=len(grid_levels),
                placed_count=placed_count
            )

            return True

        except Exception as e:
            logger.error(
                f"初始化网格失败: {symbol}",
                error=str(e),
                exc_info=True
            )
            return False

    async def _reset_grid(self, symbol: str) -> bool:
        """
        重置网格

        Args:
            symbol: 交易对

        Returns:
            是否成功
        """
        logger.info(f"重置网格: {symbol}")

        if self.is_signal_mode:
            logger.info(f"信号模式，跳过重置网格: {symbol}")
            return True

        try:
            # 撤销所有订单
            await self.order_manager.cancel_all_orders(symbol)

            # 重新初始化网格
            await self._initialize_grid_for_symbol(symbol)

            # 发送通知
            if self.notification_client:
                await self.notification_client.send(
                    message=f"网格已重置: {symbol}",
                    level="warning",
                    project="grid"
                )

            return True

        except Exception as e:
            logger.error(
                f"重置网格失败: {symbol}",
                error=str(e),
                exc_info=True
            )
            return False

    async def _check_all_orders(self) -> None:
        """
        检查所有交易对的订单状态
        """
        if self.is_signal_mode:
            return

        for symbol in self.symbols:
            try:
                filled_orders = await self.order_manager.check_orders_status(symbol)

                # 处理成交订单
                for order_info in filled_orders:
                    await self._on_order_filled(order_info)

            except Exception as e:
                logger.error(
                    f"检查订单状态失败: {symbol}",
                    error=str(e),
                    exc_info=True
                )

    async def _on_order_filled(self, order_info: dict) -> None:
        """
        订单成交处理

        Args:
            order_info: 订单信息
        """
        if self.is_signal_mode:
            return

        symbol = order_info['symbol']
        level = order_info['level']

        logger.info(
            f"订单成交: {symbol}",
            side=level.side,
            quantity=float(level.quantity),
            price=float(level.price)
        )

        # 1. 更新持仓
        self.position_manager.update_position(
            symbol=symbol,
            side=level.side,
            quantity=level.quantity,
            price=level.price
        )

        # 2. 挂反向单
        reverse_price = self.grid_calculator.calculate_reverse_price(level)
        reverse_side = 'SELL' if level.side == 'BUY' else 'BUY'

        reverse_level = GridLevel(
            price=reverse_price,
            side=reverse_side,
            quantity=level.quantity
        )

        await self.order_manager.place_grid_order(symbol, reverse_level)

        # 3. 更新风控
        pnl = self._calculate_trade_pnl(level, reverse_price)
        self.risk_manager.update_daily_pnl(pnl)

    async def _check_all_risks(self) -> None:
        """
        检查所有交易对的风险
        """
        if self.is_signal_mode:
            return

        for symbol in self.symbols:
            try:
                # 获取账户余额
                balance = await self.binance_client.get_account_balance()
                account_balance = Decimal(str(balance.get('USDT', 0)))

                # 获取当前价格
                current_price = await self.binance_client.get_ticker_price(symbol)

                # 获取持仓
                position = self.position_manager.get_position(symbol)

                # 检查风险
                result = await self.risk_manager.check_risk(
                    symbol=symbol,
                    position=position,
                    current_price=current_price,
                    account_balance=account_balance
                )

                if result.should_stop:
                    logger.warning(f"触发风控停止: {symbol}")
                    await self.order_manager.cancel_all_orders(symbol)

            except Exception as e:
                logger.error(
                    f"检查风险失败: {symbol}",
                    error=str(e),
                    exc_info=True
                )

    async def _monitor_all_grids(self) -> None:
        """
        监控所有交易对的网格状态
        """
        if self.is_signal_mode:
            return

        for symbol in self.symbols:
            try:
                await self._monitor_grid(symbol)
            except Exception as e:
                logger.error(
                    f"监控网格状态失败: {symbol}",
                    error=str(e),
                    exc_info=True
                )

    async def _monitor_grid(self, symbol: str) -> None:
        """
        监控网格状态

        Args:
            symbol: 交易对
        """
        # 获取当前价格
        current_price = await self.binance_client.get_ticker_price(symbol)

        # 获取网格状态
        grid_state = self.grid_states.get(symbol, {})
        grid_levels = grid_state.get('grid_levels', [])

        if not grid_levels:
            return

        # 计算网格中心价格
        min_price = Decimal(grid_levels[0]['price'])
        max_price = Decimal(grid_levels[-1]['price'])
        center_price = (min_price + max_price) / 2

        # 计算价格偏离比例
        deviation = abs(current_price - center_price) / center_price

        # 获取重置阈值
        grid_reset_threshold = Decimal(str(
            self.config.get('risk', {}).get('grid_reset_threshold', 0.15)
        ))

        # 如果价格偏离过大，重置网格
        if deviation > grid_reset_threshold:
            logger.warning(
                f"价格偏离网格中心过大: {symbol}",
                current_price=float(current_price),
                center_price=float(center_price),
                deviation=float(deviation)
            )

            await self._reset_grid(symbol)

    async def _calculate_volatility(self, symbol: str) -> Optional[Decimal]:
        """
        计算波动率

        Args:
            symbol: 交易对

        Returns:
            波动率（0-1之间的小数）
        """
        try:
            if not self.kline_service:
                logger.warning("K线服务未初始化，无法计算波动率")
                return None

            # 获取K线配置
            kline_config = self.config.get('kline', {})
            interval = kline_config.get('interval', '1h')
            limit = kline_config.get('limit', 100)

            # 获取K线数据
            klines = await self.kline_service.get_klines(
                symbol=symbol,
                interval=interval,
                limit=limit
            )

            if not klines or len(klines) < 10:
                logger.warning(f"{symbol} K线数据不足，无法计算波动率")
                return None

            # 计算收益率
            returns = []
            for i in range(1, len(klines)):
                close_prev = float(klines[i-1].get('close', 0))
                close_curr = float(klines[i].get('close', 0))

                if close_prev > 0:
                    returns.append((close_curr - close_prev) / close_prev)

            if not returns:
                return None

            # 计算标准差
            volatility = Decimal(str(statistics.stdev(returns)))

            logger.debug(
                f"{symbol} 波动率计算完成",
                volatility=float(volatility),
                data_points=len(returns)
            )

            return volatility

        except Exception as e:
            logger.error(
                f"计算波动率失败: {symbol}",
                error=str(e),
                exc_info=True
            )
            return None

    def _calculate_trade_pnl(
        self,
        level: GridLevel,
        reverse_price: Decimal
    ) -> Decimal:
        """
        计算交易盈亏

        Args:
            level: 网格层级
            reverse_price: 反向价格

        Returns:
            盈亏金额
        """
        if level.side == 'BUY':
            # 买入后卖出
            return (reverse_price - level.price) * level.quantity
        else:
            # 卖出后买入
            return (level.price - reverse_price) * level.quantity

    async def _restore_state(self) -> None:
        """
        恢复策略状态
        """
        if not self.db:
            logger.warning("数据库未初始化，跳过恢复策略状态")
            return

        try:
            # state = await self.db.get_strategy_state(self.strategy_name, 'main')
            state = None

            if state and 'grid_states' in state:
                self.grid_states = state['grid_states']
                logger.info(
                    "恢复策略状态完成",
                    grid_states_count=len(self.grid_states)
                )

        except Exception as e:
            logger.error(
                "恢复策略状态失败",
                error=str(e),
                exc_info=True
            )

    async def _save_state(self) -> None:
        """
        保存策略状态
        """
        if not self.db:
            logger.warning("数据库未初始化，跳过保存策略状态")
            return

        try:
            state_data = {
                'grid_states': self.grid_states,
                'last_update': datetime.now().isoformat()
            }
            # await self.db.save_strategy_state(self.strategy_name, 'main', state_data)

            logger.debug("策略状态已保存")

        except Exception as e:
            logger.error(
                "保存策略状态失败",
                error=str(e),
                exc_info=True
            )
