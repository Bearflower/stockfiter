"""
信号生成器
基于v2版本的GridSignalBot逻辑，适配grid_trading_system的新模块结构
负责市场分析、网格参数计算、参数对比、仓位验证和信号推送
"""

import asyncio
import logging
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from src.core.config.config_loader import ConfigLoader
from src.core.strategy.grid_calculator import GridCalculator, GridParameters
from src.core.strategy.market_analyzer import MarketAnalyzer, MarketState, MarketStateResult
from src.core.monitoring.notifier import UnifiedNotifier
from src.execution.signal_mode.parameter_comparator import ParameterComparator, ParameterChange
from src.execution.signal_mode.position_validator import PositionValidator, PositionValidationResult

logger = logging.getLogger(__name__)


class Signal:
    """信号数据类"""

    def __init__(
        self,
        symbol: str,
        market_state: str,
        current_price: float,
        atr: float,
        adx: float,
        grid_params: Dict,
        position_validation: Dict,
        changes: Optional[List[ParameterChange]] = None,
        timestamp: Optional[datetime] = None
    ):
        """
        初始化信号

        Args:
            symbol: 交易对
            market_state: 市场状态
            current_price: 当前价格
            atr: ATR 值
            adx: ADX 值
            grid_params: 网格参数
            position_validation: 仓位验证结果
            changes: 参数变化列表
            timestamp: 信号时间
        """
        self.symbol = symbol
        self.market_state = market_state
        self.current_price = current_price
        self.atr = atr
        self.adx = adx
        self.grid_params = grid_params
        self.position_validation = position_validation
        self.changes = changes or []
        self.timestamp = timestamp or datetime.now()

    def to_dict(self) -> Dict:
        """转换为字典"""
        return {
            'symbol': self.symbol,
            'market_state': self.market_state,
            'current_price': self.current_price,
            'atr': self.atr,
            'adx': self.adx,
            'grid_params': self.grid_params,
            'position_validation': self.position_validation,
            'changes': [
                {
                    'param_name': c.param_name,
                    'old_value': c.old_value,
                    'new_value': c.new_value,
                    'change_percent': c.change_percent,
                    'is_significant': c.is_significant
                }
                for c in self.changes
            ],
            'timestamp': self.timestamp
        }


class SignalGenerator:
    """
    信号生成器

    整合v2版本GridSignalBot的核心逻辑：
    - 获取K线数据并计算技术指标
    - 分析市场状态
    - 计算网格参数
    - 验证仓位可行性
    - 对比参数变化
    - 生成信号并推送通知
    """

    def __init__(self, config_loader: Optional[ConfigLoader] = None):
        """
        初始化信号生成器

        Args:
            config_loader: 配置加载器（可选，默认使用默认配置）
        """
        logger.info("=" * 60)
        logger.info("🚀 信号生成器初始化")
        logger.info("=" * 60)

        # 加载配置
        self.config = config_loader or ConfigLoader()

        # 初始化市场分析器
        indicators_config = self.config.get('strategy.indicators', {})
        self.market_analyzer = MarketAnalyzer(
            adx_period=indicators_config.get('adx_period', 14),
            adx_weak_threshold=indicators_config.get('adx_weak_threshold', 20),
            adx_trend_threshold=indicators_config.get('adx_trend_threshold', 25),
            adx_strong_threshold=indicators_config.get('adx_strong_threshold', 40),
            ema_fast_period=indicators_config.get('ema_fast_period', 20),
            ema_slow_period=indicators_config.get('ema_slow_period', 50)
        )

        # 初始化网格计算器
        grid_config = self.config.get('strategy.grid', {})
        trading_config = self.config.get('exchange', {})
        self.grid_calculator = GridCalculator(
            base_grid_count=grid_config.get('base_grid_count', 30),
            min_grid_count=grid_config.get('min_grid_count', 5),
            max_grid_count=grid_config.get('max_grid_count', 50),
            min_profit_rate=grid_config.get('min_profit_rate', 0.01),
            leverage=grid_config.get('leverage', 10),
            default_investment=grid_config.get('total_investment', 500)
        )

        # 初始化仓位验证器
        self.position_validator = PositionValidator()

        # 初始化参数对比器
        # 从 triggers 读取配置（与 grid_signal_bot_v2 保持一致）
        triggers_config = self.config.get('triggers', {})
        self.parameter_comparator = ParameterComparator(
            grid_width_change_threshold=triggers_config.get('grid_width_change', 0.05),
            grid_count_change_threshold=triggers_config.get('grid_count_change', 0.10),
            atr_change_threshold=triggers_config.get('atr_change', 0.20),
            profit_rate_warning_threshold=triggers_config.get('profit_rate_warning', 0.012)
        )

        # 初始化通知器
        monitoring_config = self.config.get('monitoring.alert', {})
        services_config = self.config.get('services.notification', {})
        self.notifier = UnifiedNotifier(
            service_url=services_config.get('url', 'http://localhost:8002'),
            project=services_config.get('project', 'grid'),
            enabled=monitoring_config.get('enabled', False),
            cooldown=60
        )

        # 内部状态（保存上次的参数和状态）
        self.current_params: Optional[GridParameters] = None
        self.current_atr: Optional[float] = None
        self.current_market_state: Optional[MarketState] = None

        # K线数据回调函数（需要外部注入）
        self._kline_callback = None

        logger.info("✅ 信号生成器初始化完成")

    def set_kline_callback(self, callback):
        """
        设置K线数据获取回调

        Args:
            callback: 异步回调函数，接受symbol, interval, limit参数，返回K线数据列表
        """
        self._kline_callback = callback

    async def generate_signal(self, symbol: Optional[str] = None) -> Optional[Signal]:
        """
        生成信号（核心方法）

        执行完整的巡检流程：
        1. 获取K线数据
        2. 分析市场状态
        3. 计算网格参数
        4. 验证仓位
        5. 对比参数变化
        6. 判断是否需要推送

        Args:
            symbol: 交易对（默认从配置读取）

        Returns:
            生成的信号，如果无需推送则返回None
        """
        logger.info("\n" + "=" * 60)
        logger.info("🔍 开始生成信号")
        logger.info("=" * 60)

        try:
            # 1. 获取交易对
            if symbol is None:
                symbol = self.config.get('exchange.symbol', 'BTCUSDT')

            # 2. 获取K线数据
            klines_1h, klines_4h = await self._fetch_klines(symbol)
            if not klines_1h:
                logger.error("❌ 获取K线数据失败")
                return None

            # 3. 分析市场状态
            market_result = self.market_analyzer.analyze(klines_1h, klines_4h)
            logger.info(f"市场状态：{market_result.state.value}")
            logger.info(f"ADX: {market_result.adx:.2f}")
            logger.info(f"置信度：{market_result.confidence*100:.1f}%")
            logger.info(f"趋势强度：{market_result.trend_strength:.2f}")

            # 4. 获取当前价格和ATR
            last_kline = klines_1h[-1]
            current_price = last_kline.get('close_price', 0)
            atr = last_kline.get('atr', current_price * 0.01)

            logger.info(f"当前价格：${current_price:,.2f}")
            logger.info(f"ATR: {atr:.2f}")

            # 5. 计算网格参数
            grid_params = self.grid_calculator.calculate(
                current_price=current_price,
                atr_smooth=atr,
                market_state=market_result.state,
                trend_strength=market_result.trend_strength
            )

            logger.info(f"价格区间：[${grid_params.lower_price:,.2f}, ${grid_params.upper_price:,.2f}]")
            logger.info(f"网格数量：{grid_params.grid_count}")
            logger.info(f"每格利润率：{grid_params.profit_rate*100:.2f}%")

            # 6. 验证仓位
            position_result = self.position_validator.validate(
                current_price=current_price,
                grid_count=grid_params.grid_count,
                leverage=grid_params.leverage,
                total_investment=grid_params.total_investment
            )

            logger.info(position_result.message)

            # 7. 对比参数变化
            market_state_changed = (
                self.current_market_state is not None and
                self.current_market_state != market_result.state
            )

            changes = self.parameter_comparator.compare(
                old_params=self.current_params,
                new_params=grid_params,
                old_atr=self.current_atr,
                new_atr=atr
            )

            # 8. 判断是否需要推送
            should_notify = self.parameter_comparator.should_notify(
                changes=changes,
                market_state_changed=market_state_changed
            )

            # 9. 如果需要推送，创建信号
            if should_notify or self.current_params is None:
                signal = Signal(
                    symbol=symbol,
                    market_state=market_result.state.value,
                    current_price=current_price,
                    atr=atr,
                    adx=market_result.adx,
                    grid_params=grid_params.to_dict(),
                    position_validation={
                        'is_valid': position_result.is_valid,
                        'qty_per_grid': position_result.qty_per_grid,
                        'min_margin_required': position_result.min_margin_required,
                        'suggested_margin': position_result.suggested_margin,
                        'suggested_grid_count': position_result.suggested_grid_count,
                        'message': position_result.message
                    },
                    changes=changes
                )

                # 推送通知
                await self._send_notification(signal)

                # 更新内部状态
                self.current_params = grid_params
                self.current_atr = atr
                self.current_market_state = market_result.state

                return signal
            else:
                logger.info("ℹ️  无显著变化，不生成信号")
                return None

        except Exception as e:
            logger.error(f"❌ 信号生成失败：{e}", exc_info=True)
            # 发送错误通知
            await self._send_error_notification("信号生成失败", str(e))
            return None

    async def _fetch_klines(self, symbol: str) -> Tuple[List[Dict], Optional[List[Dict]]]:
        """
        获取K线数据

        Args:
            symbol: 交易对

        Returns:
            (1h K线, 4h K线)
        """
        if self._kline_callback is None:
            logger.warning("未设置K线数据回调，使用模拟数据")
            # 返回模拟数据用于测试
            return self._generate_mock_klines(symbol), None

        # 调用外部K线服务
        klines_1h = await self._kline_callback(symbol=symbol, interval="1h", limit=100)
        klines_4h = await self._kline_callback(symbol=symbol, interval="4h", limit=100)

        return klines_1h, klines_4h

    def _generate_mock_klines(self, symbol: str) -> List[Dict]:
        """
        生成模拟K线数据（用于测试）

        Args:
            symbol: 交易对

        Returns:
            模拟K线数据列表
        """
        import random

        base_price = 60000.0 if 'BTC' in symbol else 3000.0
        klines = []

        for i in range(100):
            price = base_price + random.uniform(-base_price * 0.05, base_price * 0.05)
            klines.append({
                'close_price': price,
                'atr': base_price * 0.01,
                'adx': random.uniform(15, 45),
                'ema_fast': price + random.uniform(-100, 100),
                'ema_slow': price + random.uniform(-200, 200)
            })

        return klines

    async def _send_notification(self, signal: Signal) -> bool:
        """
        发送信号通知

        Args:
            signal: 信号对象

        Returns:
            是否发送成功
        """
        try:
            # 构建通知内容
            grid_params = signal.grid_params
            position = signal.position_validation

            # 价格区间信息
            price_range = f"[${grid_params['lower_price']:,.2f}, ${grid_params['upper_price']:,.2f}]"
            grid_info = (
                f"**交易对**: {signal.symbol}\n"
                f"**市场状态**: {signal.market_state}\n"
                f"**当前价格**: ${signal.current_price:,.2f}\n"
                f"**ATR**: {signal.atr:.2f}\n"
                f"**ADX**: {signal.adx:.2f}\n\n"
                f"**价格区间**: {price_range}\n"
                f"**网格数量**: {grid_params['grid_count']}\n"
                f"**网格方向**: {grid_params['grid_direction']}\n"
                f"**杠杆倍数**: {grid_params['leverage']}x\n"
                f"**投资金额**: {grid_params['total_investment']} USDT\n"
                f"**每格利润率**: {grid_params.get('profit_rate', 0)*100:.2f}%\n\n"
                f"**仓位验证**: {'✅ 可行' if position['is_valid'] else '⚠️ 不可行'}\n"
                f"**每格合约数**: {position['qty_per_grid']:.2f} 张"
            )

            # 如果有建议，添加建议信息
            if position.get('suggested_margin'):
                grid_info += (
                    f"\n\n**建议**:\n"
                    f"- 保证金: {position['suggested_margin']:.0f} USDT\n"
                    f"- 网格数: {position['suggested_grid_count']} 格"
                )

            # 如果有参数变化，添加变化信息
            if signal.changes:
                changes_info = "\n\n**参数变化**:\n"
                for change in signal.changes:
                    changes_info += (
                        f"- {change.param_name}: "
                        f"{change.old_value:.2f} → {change.new_value:.2f} "
                        f"({change.change_percent*100:.1f}%)\n"
                    )
                grid_info += changes_info

            # 发送通知
            success = await self.notifier.send_message(
                title=f"📊 {signal.symbol} 网格信号",
                content=grid_info,
                level="warning" if signal.changes else "info"
            )

            if success:
                logger.info("✅ 信号通知推送成功")
            else:
                logger.error("❌ 信号通知推送失败")

            return success

        except Exception as e:
            logger.error(f"发送通知异常：{e}", exc_info=True)
            return False

    async def _send_error_notification(self, error_type: str, error_message: str) -> bool:
        """
        发送错误通知

        Args:
            error_type: 错误类型
            error_message: 错误消息

        Returns:
            是否发送成功
        """
        try:
            return await self.notifier.notify_error(
                error_type=error_type,
                error_message=error_message
            )
        except Exception as e:
            logger.error(f"发送错误通知异常：{e}")
            return False

    async def run_once(self, symbol: Optional[str] = None) -> Optional[Signal]:
        """
        执行一次信号生成（便捷方法）

        Args:
            symbol: 交易对

        Returns:
            生成的信号
        """
        return await self.generate_signal(symbol)

    async def run_loop(
        self,
        run_minute: int = 35,
        rest_hours: Optional[List[int]] = None,
        symbol: Optional[str] = None
    ) -> None:
        """
        定时运行信号生成

        Args:
            run_minute: 每小时的第几分钟运行
            rest_hours: 休息时间段（小时列表）
            symbol: 交易对
        """
        if rest_hours is None:
            rest_hours = [0, 1, 2, 3, 4, 5]

        logger.info(f"🔄 启动定时运行模式，每小时的 {run_minute} 分运行")
        logger.info(f"😴 休息时间段：{rest_hours[0]:02d}:00 - {rest_hours[-1]+1:02d}:00")

        while True:
            try:
                # 计算到下一个运行时间的等待秒数
                now = datetime.now()
                next_run = now.replace(minute=run_minute, second=0, microsecond=0)

                # 如果当前时间已过本小时的运行时间，则设置为下一小时
                if now.minute >= run_minute:
                    from datetime import timedelta
                    next_run = next_run + timedelta(hours=1)

                # 检查是否在休息时间段内
                while next_run.hour in rest_hours:
                    next_run = next_run.replace(hour=next_run.hour + 1)
                    if next_run.hour >= 24:
                        from datetime import timedelta
                        next_run = next_run.replace(hour=0) + timedelta(days=1)

                wait_seconds = (next_run - now).total_seconds()

                if wait_seconds > 0:
                    logger.info(f"\n⏰ 下次运行时间：{next_run.strftime('%Y-%m-%d %H:%M:%S')}")
                    logger.info(f"⏳ 等待 {int(wait_seconds // 60)} 分 {int(wait_seconds % 60)} 秒...")
                    await asyncio.sleep(wait_seconds)

                # 执行信号生成
                await self.generate_signal(symbol)

            except KeyboardInterrupt:
                logger.info("\n🛑 收到中断信号，停止运行")
                break
            except Exception as e:
                logger.error(f"❌ 循环运行异常：{e}", exc_info=True)
                logger.info("⏳ 等待 5 分钟后重试...")
                await asyncio.sleep(300)

    def get_current_state(self) -> Dict:
        """
        获取当前内部状态

        Returns:
            状态字典
        """
        return {
            'current_params': self.current_params.to_dict() if self.current_params else None,
            'current_atr': self.current_atr,
            'current_market_state': self.current_market_state.value if self.current_market_state else None
        }

    async def close(self) -> None:
        """关闭资源"""
        await self.notifier.close()
        logger.info("✅ 信号生成器资源已释放")
