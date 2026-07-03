"""
网格信号灯模块
半自动信号灯系统，自动分析市场状态并推送网格参数
"""
import asyncio
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Dict, Optional
import structlog

from shared.kline_service import KLineService
from shared.notification import NotificationClient
from shared.binance_api import BinanceClient
from .market_state import MarketStateDetector, MarketState, MarketAnalysis
from .grid_calculator import GridCalculator, DynamicGridParams


logger = structlog.get_logger()


@dataclass
class GridSignal:
    """
    网格信号数据类

    Attributes:
        symbol: 交易对
        market_analysis: 市场分析结果
        grid_params: 动态网格参数
        timestamp: 时间戳
        message: 推送消息
        position_valid: 仓位是否可行
        position_message: 仓位提示信息
    """
    symbol: str
    market_analysis: MarketAnalysis
    grid_params: Optional[DynamicGridParams]
    timestamp: datetime
    message: str
    position_valid: Optional[bool]
    position_message: str


class GridSignalBot:
    """
    网格信号灯机器人

    半自动信号灯系统，自动分析市场状态并推送网格参数到飞书。

    主要功能：
    - 定时巡检市场状态
    - 自动计算最优网格参数
    - 自动检查仓位可行性
    - 通过飞书推送可执行的操作指令
    - 为后续全自动交易打下基础
    """

    def __init__(
        self,
        binance_client: BinanceClient,
        kline_service: KLineService,
        notification_client: NotificationClient,
        grid_calculator: GridCalculator,
        config: Dict
    ):
        """
        初始化网格信号灯机器人

        Args:
            binance_client: 币安客户端
            kline_service: K线服务
            notification_client: 通知客户端
            grid_calculator: 网格计算器
            config: 配置字典

        Raises:
            ValueError: 参数验证失败
        """
        if not binance_client:
            raise ValueError("币安客户端不能为空")

        if not kline_service:
            raise ValueError("K线服务不能为空")

        if not notification_client:
            raise ValueError("通知客户端不能为空")

        if not grid_calculator:
            raise ValueError("网格计算器不能为空")

        if not config:
            raise ValueError("配置不能为空")

        self.binance_client = binance_client
        self.kline_service = kline_service
        self.notification_client = notification_client
        self.grid_calculator = grid_calculator
        self.config = config

        # 初始化市场状态检测器
        self.market_detector = MarketStateDetector(
            kline_service=kline_service,
            adx_extreme_strong=config.get('market', {}).get('adx_extreme_strong', 40),
            adx_extreme_strong_4h=config.get('market', {}).get('adx_extreme_strong_4h', 30),
            adx_normal_strong=config.get('market', {}).get('adx_normal_strong', 30),
            adx_normal_strong_4h=config.get('market', {}).get('adx_normal_strong_4h', 25),
            weak_trend_adx_lower=config.get('market', {}).get('weak_trend_adx_lower', 25),
            weak_trend_adx_upper=config.get('market', {}).get('weak_trend_adx_upper', 30),
            volatility_ratio_threshold=Decimal(str(config.get('market', {}).get('volatility_ratio_threshold', 1.2))),
            volatility_consecutive_count=config.get('market', {}).get('volatility_consecutive_count', 2),
            volatility_recovery_ratio=Decimal(str(config.get('market', {}).get('volatility_recovery_ratio', 1.2))),
            recovery_adx_strong_1h=config.get('market', {}).get('recovery_adx_strong_1h', 30),
            recovery_adx_strong_4h=config.get('market', {}).get('recovery_adx_strong_4h', 30),
            recovery_adx_weak_1h=config.get('market', {}).get('recovery_adx_weak_1h', 25),
            recovery_adx_weak_4h=config.get('market', {}).get('recovery_adx_weak_4h', 25),
            trend_strength_divisor=config.get('market', {}).get('trend_strength_divisor', 30),
            atr_history_size=config.get('market', {}).get('atr_history_size', 5),
            ema_fast_period=config.get('market', {}).get('ema_fast', 20),
            ema_slow_period=config.get('market', {}).get('ema_slow', 50),
            atr_period=config.get('market', {}).get('atr_period', 14),
            # V2.3 新增参数
            emergency_adx_threshold=config.get('market', {}).get('emergency_adx_threshold', 55),
            trend_acceleration_threshold=config.get('market', {}).get('trend_acceleration_threshold', 8),
            adx_history_size=config.get('market', {}).get('adx_history_size', 3),
            # V2.4 三层预警架构新增参数
            adx_period=config.get('market', {}).get('adx_period', 10),
            price_emergency_1h=Decimal(str(config.get('market', {}).get('price_emergency_1h', 0.03))),
            price_emergency_15m=Decimal(str(config.get('market', {}).get('price_emergency_15m', 0.015))),
            adx_early_warning_15m=config.get('market', {}).get('adx_early_warning_15m', 50),
            price_early_warning_1h=Decimal(str(config.get('market', {}).get('price_early_warning_1h', 0.01))),
            # 置信度参数（V2.3从配置读取）
            confidence_emergency=Decimal(str(config.get('market', {}).get('confidence', {}).get('emergency_extreme_trend', 0.99))),
            confidence_trend_accelerating=Decimal(str(config.get('market', {}).get('confidence', {}).get('trend_accelerating', 0.9))),
            confidence_extreme_strong=Decimal(str(config.get('market', {}).get('confidence', {}).get('extreme_strong_trend', 0.95))),
            confidence_volatility_abnormal=Decimal(str(config.get('market', {}).get('confidence', {}).get('volatility_abnormal', 0.85))),
            confidence_normal_strong=Decimal(str(config.get('market', {}).get('confidence', {}).get('normal_strong_trend', 0.8))),
            confidence_weak_trend=Decimal(str(config.get('market', {}).get('confidence', {}).get('weak_trend', 0.7))),
            confidence_oscillation=Decimal(str(config.get('market', {}).get('confidence', {}).get('oscillation', 0.5))),
            # V2.4 新增置信度
            confidence_price_emergency=Decimal(str(config.get('market', {}).get('confidence', {}).get('price_emergency', 1.0))),
            confidence_early_warning_15m=Decimal(str(config.get('market', {}).get('confidence', {}).get('early_warning_15m', 0.92))),
            confidence_trend_confirmed_1h=Decimal(str(config.get('market', {}).get('confidence', {}).get('trend_confirmed_1h', 0.95)))
        )

        # 交易对配置
        self.symbols = config.get('symbols', [])
        if not self.symbols:
            raise ValueError("交易对列表不能为空")

        # 杠杆和保证金配置
        self.default_leverage = config.get('trading', {}).get('leverage', 10)
        self.default_margin = Decimal(str(config.get('trading', {}).get('margin', 500)))

        # 网格数量上下限（用于仓位建议）
        self.min_grid_count = config.get('grid', {}).get('min_grid_count', 5)

        # 巡检配置（从信号灯专用配置读取，单位：分钟）
        self.check_interval_minutes = config.get('signal_bot', {}).get('check_interval_minutes', 60)

        # 推送冷却时间（V2.3三档冷却，从配置文件读取）
        self.push_cooldown_hours_alert = config.get('signal_bot', {}).get('push_cooldown_hours_alert', 1)  # 紧急/趋势加速/极端强趋势
        self.push_cooldown_hours_normal = config.get('signal_bot', {}).get('push_cooldown_hours_normal', 6)  # 普通强趋势/波动率异常
        self.push_cooldown_hours_tradable = config.get('signal_bot', {}).get('push_cooldown_hours_tradable', 2)  # 弱趋势/震荡

        # 利润率低阈值（从配置文件读取，预留后续使用）
        trigger_cfg = config.get('signal_bot', {}).get('trigger_thresholds', {})
        self.profit_rate_low_threshold = Decimal(str(trigger_cfg.get('profit_rate_low', 0.012)))  # TODO: V2.2 利润率恶化提醒

        # 保守方案网格减少步长（从配置文件读取）
        self.conservative_grid_reduce = config.get('signal_bot', {}).get('conservative_grid_reduce', 10)

        # 历史状态记录（用于检测变化）
        self.last_signals: Dict[str, GridSignal] = {}

        logger.info(
            "网格信号灯机器人初始化完成",
            symbols=self.symbols,
            leverage=self.default_leverage,
            margin=float(self.default_margin),
            check_interval_minutes=self.check_interval_minutes,
            push_cooldown_hours_alert=self.push_cooldown_hours_alert,
            push_cooldown_hours_normal=self.push_cooldown_hours_normal,
            push_cooldown_hours_tradable=self.push_cooldown_hours_tradable
        )

    async def run_once(self, symbol: str) -> GridSignal:
        """
        执行一次信号检测（V2.4：按10种市场状态分发，三层预警架构）

        Args:
            symbol: 交易对

        Returns:
            网格信号

        Raises:
            ValueError: 参数验证失败
            Exception: 检测失败
        """
        if not symbol or not symbol.strip():
            raise ValueError("交易对不能为空")

        logger.info(f"开始执行信号检测: {symbol}")

        try:
            # 1. 检测市场状态
            market_analysis = await self.market_detector.detect_market_state(symbol)

            # 2. 根据市场状态分发
            grid_params = None
            position_valid = None  # 非网格状态为 None，表示不适用
            position_message = ""

            state = market_analysis.state

            # V2.4: 检测是否从危险状态恢复到可交易状态
            dangerous_states = {
                MarketState.PRICE_EMERGENCY,
                MarketState.EARLY_WARNING_15M,
                MarketState.TREND_CONFIRMED_1H,
                MarketState.TREND_ACCELERATING,
                MarketState.EXTREME_STRONG_TREND,
                MarketState.NORMAL_STRONG_TREND,
                MarketState.VOLATILITY_ABNORMAL,
            }
            tradable_states = {MarketState.WEAK_TREND, MarketState.OSCILLATION}

            is_recovery = False
            if symbol in self.last_signals:
                prev_state = self.last_signals[symbol].market_analysis.state
                if prev_state in dangerous_states and state in tradable_states:
                    is_recovery = True
                    logger.info(
                        f"{symbol} 从危险状态恢复到可交易状态",
                        prev_state=prev_state.value,
                        new_state=state.value
                    )

            # 恢复通知：从危险状态恢复到可交易状态时推送
            if is_recovery:
                message = self._generate_recovery_message(symbol, market_analysis)

            # 价格行为紧急触发（第1层，V2.4新增，0延迟）
            if state == MarketState.PRICE_EMERGENCY:
                message = self._generate_price_emergency_message(symbol, market_analysis)

            # 15m ADX 早期预警（第2层，V2.4新增，比1h快4倍）
            elif state == MarketState.EARLY_WARNING_15M:
                message = self._generate_early_warning_15m_message(symbol, market_analysis)

            # 1h ADX(10) 趋势确认（第3层，V2.4新增，ADX周期从14缩短为10）
            elif state == MarketState.TREND_CONFIRMED_1H:
                message = self._generate_trend_confirmed_1h_message(symbol, market_analysis)

            # 趋势急剧增强：不计算网格参数，推送"暂停或单向挂单"（V2.3新增）
            elif state == MarketState.TREND_ACCELERATING:
                message = self._generate_trend_accelerating_message(symbol, market_analysis)

            # 极端强趋势：不计算网格参数，推送"必须立即终止"
            elif state == MarketState.EXTREME_STRONG_TREND:
                message = self._generate_extreme_strong_message(symbol, market_analysis)

            # 波动率异常：不计算网格参数，推送"暂停挂单"
            elif state == MarketState.VOLATILITY_ABNORMAL:
                message = self._generate_volatility_abnormal_message(symbol, market_analysis)

            # 普通强趋势：不计算网格参数，推送"建议终止"
            elif state == MarketState.NORMAL_STRONG_TREND:
                message = self._generate_normal_strong_message(symbol, market_analysis)

            # 弱趋势或震荡：计算网格参数，推送网格建议
            elif state in [MarketState.WEAK_TREND, MarketState.OSCILLATION]:
                grid_params = await self._calculate_grid_params(symbol, market_analysis)
                position_valid, position_message, _ = self.grid_calculator.validate_position_size(
                    price=market_analysis.current_price,
                    grid_count=grid_params.grid_count,
                    leverage=self.default_leverage,
                    margin=self.default_margin
                )
                message = self._generate_signal_message(
                    symbol=symbol,
                    market_analysis=market_analysis,
                    grid_params=grid_params,
                    position_valid=position_valid,
                    position_message=position_message
                )

            else:
                # 未知状态，默认震荡处理
                logger.warning(f"{symbol} 未知市场状态: {state}，默认按震荡处理")
                grid_params = await self._calculate_grid_params(symbol, market_analysis)
                message = self._generate_signal_message(
                    symbol=symbol,
                    market_analysis=market_analysis,
                    grid_params=grid_params,
                    position_valid=True,
                    position_message=""
                )

            # 3. 构建信号
            signal = GridSignal(
                symbol=symbol,
                market_analysis=market_analysis,
                grid_params=grid_params,
                timestamp=datetime.now(),
                message=message,
                position_valid=position_valid,
                position_message=position_message
            )

            logger.info(
                f"{symbol} 信号检测完成",
                state=market_analysis.state.value,
                has_grid_params=grid_params is not None
            )

            return signal

        except Exception as e:
            logger.error(
                f"信号检测失败: {symbol}",
                error=str(e),
                exc_info=True
            )
            raise

    async def run_loop(self, interval_minutes: int = None) -> None:
        """
        持续运行信号检测循环

        Args:
            interval_minutes: 巡检间隔（分钟），默认从配置读取

        Raises:
            Exception: 运行失败
        """
        if interval_minutes is None:
            interval_minutes = self.check_interval_minutes

        logger.info(
            "开始运行信号检测循环",
            interval_minutes=interval_minutes
        )

        while True:
            try:
                # 对每个交易对执行检测
                for symbol in self.symbols:
                    try:
                        signal = await self.run_once(symbol)

                        # 检查是否需要推送
                        if self._should_notify(signal):
                            await self._send_notification(signal)
                            self.last_signals[symbol] = signal

                    except Exception as e:
                        logger.error(
                            f"信号检测失败: {symbol}",
                            error=str(e),
                            exc_info=True
                        )

                # 等待下一次巡检
                await asyncio.sleep(interval_minutes * 60)

            except Exception as e:
                logger.error(
                    "信号检测循环失败",
                    error=str(e),
                    exc_info=True
                )
                await asyncio.sleep(60)

    async def _calculate_grid_params(
        self,
        symbol: str,
        market_analysis: MarketAnalysis
    ) -> DynamicGridParams:
        """
        计算动态网格参数

        Args:
            symbol: 交易对
            market_analysis: 市场分析结果

        Returns:
            动态网格参数

        Raises:
            ValueError: 参数验证失败
        """
        # 获取历史K线数据（用于计算基准ATR）
        klines = await self.kline_service.get_klines(
            symbol=symbol,
            interval='1d',
            limit=100
        )

        # 计算基准ATR
        atr_baseline = self.grid_calculator.calculate_baseline_atr(klines)

        # 计算动态网格参数
        params = self.grid_calculator.calculate_dynamic_grid_params(
            current_price=market_analysis.current_price,
            atr_smooth=market_analysis.atr_smooth,
            atr_baseline=atr_baseline,
            market_state=market_analysis.state.value,
            trend_strength=market_analysis.trend_strength
        )

        # 验证利润率
        profit_valid, suggested_count = self.grid_calculator.validate_profit_rate(params)

        if not profit_valid and suggested_count:
            # 重新计算网格参数
            params = self.grid_calculator.calculate_dynamic_grid_params(
                current_price=market_analysis.current_price,
                atr_smooth=market_analysis.atr_smooth,
                atr_baseline=atr_baseline,
                market_state=market_analysis.state.value,
                trend_strength=market_analysis.trend_strength
            )
            params.grid_count = suggested_count

        return params

    def _should_notify(self, signal: GridSignal) -> bool:
        """
        判断是否需要推送通知（V2.3 三档冷却逻辑）

        触发条件：
        - 首次运行：一定推送
        - 市场状态变化：立即推送
        - 同状态：检查冷却时间，超过冷却时间才推送

        冷却时间三档：
        - alert（1小时）：紧急极端趋势/趋势急剧增强/极端强趋势
        - normal（6小时）：普通强趋势/波动率异常
        - tradable（2小时）：弱趋势/震荡

        Args:
            signal: 当前信号

        Returns:
            是否需要推送
        """
        symbol = signal.symbol
        state = signal.market_analysis.state

        # 首次运行：一定推送
        if symbol not in self.last_signals:
            logger.info(f"{symbol} 首次运行，需推送", state=state.value)
            return True

        last = self.last_signals[symbol]
        old_state = last.market_analysis.state

        # 状态变化：立即推送
        if old_state != state:
            logger.info(f"{symbol} 市场状态变化，需推送",
                        old_state=old_state.value, new_state=state.value)
            return True

        # 同状态：根据市场状态选择冷却时间（V2.3三档冷却）
        if state in [MarketState.PRICE_EMERGENCY, MarketState.EARLY_WARNING_15M, MarketState.TREND_CONFIRMED_1H,
             MarketState.TREND_ACCELERATING, MarketState.EXTREME_STRONG_TREND]:
            cooldown_hours = self.push_cooldown_hours_alert  # 1小时
        elif state in [MarketState.NORMAL_STRONG_TREND, MarketState.VOLATILITY_ABNORMAL]:
            cooldown_hours = self.push_cooldown_hours_normal  # 6小时
        else:
            cooldown_hours = self.push_cooldown_hours_tradable  # 2小时（弱趋势/震荡）

        if hasattr(last, 'timestamp') and last.timestamp:
            hours_since = (datetime.now() - last.timestamp).total_seconds() / 3600
            if hours_since < cooldown_hours:
                logger.info(f"{symbol} 冷却中，跳过推送",
                            state=state.value,
                            hours_since=round(hours_since, 1),
                            cooldown=cooldown_hours)
                return False
            # 超过冷却时间：推送
            logger.info(f"{symbol} 冷却期满，推送", state=state.value, hours_since=round(hours_since, 1))
        else:
            logger.info(f"{symbol} 无上次推送记录，推送", state=state.value)
        return True

    async def _send_notification(self, signal: GridSignal) -> bool:
        """
        发送通知到飞书

        Args:
            signal: 网格信号

        Returns:
            是否发送成功
        """
        try:
            success = await self.notification_client.send(
                message=signal.message,
                level="info",
                project="grid"
            )

            if success:
                logger.info(
                    f"{signal.symbol} 通知发送成功",
                    state=signal.market_analysis.state.value
                )
            else:
                logger.error(f"{signal.symbol} 通知发送失败")

            return success

        except Exception as e:
            logger.error(
                f"{signal.symbol} 发送通知失败",
                error=str(e),
                exc_info=True
            )
            return False

    def _generate_signal_message(
        self,
        symbol: str,
        market_analysis: MarketAnalysis,
        grid_params: DynamicGridParams,
        position_valid: bool,
        position_message: str
    ) -> str:
        """
        生成网格信号推送消息

        Args:
            symbol: 交易对
            market_analysis: 市场分析结果
            grid_params: 动态网格参数
            position_valid: 仓位是否可行
            position_message: 仓位提示信息

        Returns:
            推送消息
        """
        # 标题
        title = f"【网格信号灯】{market_analysis.state.value}"

        # 市场数据
        market_data = f"""
📊 当前市场数据
- 价格: {float(market_analysis.current_price):.2f} USDT
- ATR(14): {float(market_analysis.atr_smooth):.2f}
- ADX(1h): {float(market_analysis.adx_1h):.2f}
- ADX(4h): {float(market_analysis.adx_4h):.2f}
- 每格利润率: {float(grid_params.profit_rate) * 100:.2f}%
"""

        # 网格参数
        grid_params_text = f"""
📐 建议网格参数
- 网格模式: {grid_params.grid_mode.value}
- 价格区间: {float(grid_params.lower_boundary):.2f} - {float(grid_params.upper_boundary):.2f} USDT
- 网格数量: {grid_params.grid_count} 格
- 网格间距: {float(grid_params.grid_spacing):.2f} USDT
"""

        # 止盈止损
        stop_loss_text = f"""
🎯 止盈止损
- 终止最低价: {float(grid_params.stop_loss_low):.2f} USDT
- 终止最高价: {float(grid_params.stop_loss_high):.2f} USDT
"""

        # 上移/下移功能
        move_text = ""
        if grid_params.stop_move_up_price:
            move_text += f"""
📈 上移功能（启用）
- 停止上移价格: {float(grid_params.stop_move_up_price):.2f} USDT
"""
        if grid_params.stop_move_down_price:
            move_text += f"""
📉 下移功能（启用）
- 停止下移价格: {float(grid_params.stop_move_down_price):.2f} USDT
"""

        # 资金可行性提醒
        funding_text = ""
        if not position_valid:
            funding_text = f"""
💰 资金可行性提醒
{position_message}

建议：
- 方案1（保守）：减少网格数量至 {max(self.min_grid_count, grid_params.grid_count - self.conservative_grid_reduce)} 格
- 方案2（激进）：增加保证金或提高杠杆（风险较高）
- 请根据您的资金情况在币安创建界面调整参数
"""
        else:
            funding_text = f"""
💰 资金配置
- 建议杠杆: {self.default_leverage}x
- 建议保证金: {float(self.default_margin):.0f} USDT
- {position_message}
"""

        # 操作指令
        operation_text = f"""
💡 操作指令：
1. 登录币安APP → 永续合约 → 策略交易 → 运行中，终止当前 {symbol} 网格（如有）。
2. 点击"创建网格" → 合约网格。
3. 填入以上价格区间、网格数量、网格模式。
4. 设置杠杆（建议{self.default_leverage}x）、总投入金额（根据您的资金能力）。
5. 高级设置中，启用"上移/下移"并填入停止价格（如适用），设置止盈止损价格。
6. 确认创建前请检查每格下单数量≥1张。
"""

        # 组合消息
        message = f"""
{title}
{market_data}
{grid_params_text}
{stop_loss_text}
{move_text}
{funding_text}
{operation_text}
"""

        return message.strip()

    def _generate_trend_accelerating_message(self, symbol: str, market_analysis: MarketAnalysis) -> str:
        """
        生成趋势急剧增强警报消息（V2.3新增）

        当2h内1h ADX上升超过trend_acceleration_threshold时触发。

        Args:
            symbol: 交易对
            market_analysis: 市场分析结果

        Returns:
            推送消息
        """
        adx_current = float(market_analysis.adx_1h)
        adx_prev = float(market_analysis.adx_prev_1h)
        acceleration = adx_current - adx_prev if adx_prev > 0 else 0
        return f"""
⚠️ 【网格信号灯】趋势急剧增强

📊 ADX 在 2 小时内从 {adx_prev:.1f} 升至 {adx_current:.1f} (+{acceleration:.1f})

⚠️ 风险提示
趋势正在加速，即使未达到极端阈值，也建议暂停网格或启用只做单向挂单。

💡 操作：考虑终止网格或取消所有逆势挂单。
""".strip()

    def _generate_extreme_strong_message(self, symbol: str, market_analysis: MarketAnalysis) -> str:
        """
        生成极端强趋势警报消息

        Args:
            symbol: 交易对
            market_analysis: 市场分析结果

        Returns:
            推送消息
        """
        direction = "上升" if market_analysis.ema20_1h > market_analysis.ema50_1h else "下降"
        return f"""
🚨 【网格信号灯】极端强趋势警报 - 必须立即终止

📊 市场状态
- 交易对：{symbol}
- 1h ADX：{float(market_analysis.adx_1h):.1f}（极端强趋势）
- 4h ADX：{float(market_analysis.adx_4h):.1f}
- 价格：{float(market_analysis.current_price):.2f} USDT
- 方向：{direction}

⚠️ 风险提示
ADX 超过 {self.market_detector.adx_extreme_strong}，市场处于极端单边行情。任何逆势网格都会快速亏损。

💡 操作指令：
请立即终止当前 {symbol} 网格，不要犹豫。
等待 1h ADX 回落到 {self.market_detector.recovery_adx_strong_1h} 以下，且 4h ADX < {self.market_detector.recovery_adx_strong_4h} 时再考虑重建。
""".strip()

    def _generate_normal_strong_message(self, symbol: str, market_analysis: MarketAnalysis) -> str:
        """
        生成普通强趋势警报消息

        Args:
            symbol: 交易对
            market_analysis: 市场分析结果

        Returns:
            推送消息
        """
        direction = "上升" if market_analysis.ema20_1h > market_analysis.ema50_1h else "下降"
        return f"""
⚠️ 【网格信号灯】强趋势警报 - 建议终止网格

📊 市场状态
- 交易对：{symbol}
- 1h ADX：{float(market_analysis.adx_1h):.1f}（强趋势）
- 4h ADX：{float(market_analysis.adx_4h):.1f}（确认）
- 价格：{float(market_analysis.current_price):.2f} USDT
- 方向：{direction}（1h/4h EMA 同向确认）

⚠️ 风险提示
当前处于确认的强趋势，中性网格大概率逆势亏损。

💡 操作指令：
建议立即终止当前 {symbol} 网格。等待后续 ADX 回落到 {self.market_detector.recovery_adx_strong_1h} 以下再重建。
""".strip()

    def _generate_volatility_abnormal_message(self, symbol: str, market_analysis: MarketAnalysis) -> str:
        """
        生成波动率异常警报消息

        Args:
            symbol: 交易对
            market_analysis: 市场分析结果

        Returns:
            推送消息
        """
        atr_current = float(market_analysis.atr_smooth)
        atr_2h_ago = float(market_analysis.atr_2h_ago)
        change_pct = ((atr_current / atr_2h_ago) - 1) * 100 if atr_2h_ago > 0 else 0
        return f"""
🌊 【网格信号灯】波动率异常警报 - 暂停挂单

📊 波动率数据
- 交易对：{symbol}
- 当前 ATR(14)：{atr_current:.2f}
- 2小时前 ATR：{atr_2h_ago:.2f}
- 变化率：+{change_pct:.1f}%
- 价格：{float(market_analysis.current_price):.2f} USDT

⚠️ 风险提示
ATR 在 2 小时内飙升 {change_pct:.1f}%，市场可能出现剧烈单边行情。

💡 操作指令：
1. 立即取消当前网格的所有挂单（但不平仓），暂停网格运行。
2. 等待 2 小时后系统重新巡检，若 ATR 回落则推送恢复通知。
3. 若价格已大幅偏离，建议直接终止网格止损。
""".strip()

    def _generate_price_emergency_message(self, symbol: str, market_analysis: MarketAnalysis) -> str:
        """
        生成价格行为紧急触发消息（V2.4新增，第1层预警）

        当1h变动>=3%或15m变动>=1.5%时触发，0延迟。

        Args:
            symbol: 交易对
            market_analysis: 市场分析结果

        Returns:
            推送消息
        """
        pct_1h = float(market_analysis.price_change_1h) * 100
        pct_15m = float(market_analysis.price_change_15m) * 100
        trigger_reason = ""
        if abs(pct_1h) >= 3:
            trigger_reason = f"1h 价格变动 {pct_1h:+.2f}%（超过+/-3%阈值）"
        if abs(pct_15m) >= 1.5:
            trigger_reason += f"；15m 价格变动 {pct_15m:+.2f}%（超过+/-1.5%阈值）"

        return f"""
🚨🚨 【网格信号灯】价格行为紧急触发 - 必须立即终止网格 🚨🚨

📊 触发原因
{trigger_reason}

📊 市场数据
- 价格: {float(market_analysis.current_price):.2f} USDT
- 1h ADX: {float(market_analysis.adx_1h):.1f}
- 15m ADX: {float(market_analysis.adx_15m):.1f}
- 4h ADX: {float(market_analysis.adx_4h):.1f}

⚠️ 风险提示
价格行为直接触发紧急预警（第1层），0延迟响应。市场可能出现极端单边行情，任何网格都会快速亏损。
请 **立即终止** 当前所有网格，不要犹豫。

💡 恢复条件：等待价格变动率回落到正常范围，且ADX指标恢复正常后，系统会推送恢复通知。
""".strip()

    def _generate_early_warning_15m_message(self, symbol: str, market_analysis: MarketAnalysis) -> str:
        """
        生成15m ADX早期预警消息（V2.4新增，第2层预警）

        当15m ADX>=50且1h变动>=1%时触发，比1h ADX快4倍。

        Args:
            symbol: 交易对
            market_analysis: 市场分析结果

        Returns:
            推送消息
        """
        pct_1h = float(market_analysis.price_change_1h) * 100
        return f"""
⚠️⚠️ 【网格信号灯】15m ADX 早期预警 - 建议暂停网格 ⚠️⚠️

📊 预警信号
- 15m ADX: {float(market_analysis.adx_15m):.1f}（超过{self.market_detector.adx_early_warning_15m}阈值，趋势加速中）
- 1h 价格变动: {pct_1h:+.2f}%（超过+/-1%阈值）
- 1h ADX: {float(market_analysis.adx_1h):.1f}
- 价格: {float(market_analysis.current_price):.2f} USDT

⚠️ 风险提示
15分钟级别ADX已触发早期预警（第2层），比1h ADX快4倍。趋势可能正在加速形成。
建议立即暂停网格挂单，等待市场方向明确后再操作。

💡 操作建议：
1. 取消所有逆势挂单
2. 可保留顺势挂单
3. 密切关注后续1h ADX是否确认趋势
""".strip()

    def _generate_trend_confirmed_1h_message(self, symbol: str, market_analysis: MarketAnalysis) -> str:
        """
        生成1h ADX(10)趋势确认消息（V2.4新增，第3层预警）

        当1h ADX(10)>=55时触发，ADX计算周期从14缩短为10。

        Args:
            symbol: 交易对
            market_analysis: 市场分析结果

        Returns:
            推送消息
        """
        direction = "上升" if market_analysis.ema20_1h > market_analysis.ema50_1h else "下降"
        return f"""
🚨 【网格信号灯】1h ADX(10) 趋势确认 - 必须立即终止网格 🚨

📊 市场数据
- 1h ADX(10): {float(market_analysis.adx_1h):.1f}（超过{self.market_detector.emergency_adx_threshold}阈值，趋势已确认）
- 4h ADX: {float(market_analysis.adx_4h):.1f}
- 15m ADX: {float(market_analysis.adx_15m):.1f}
- 价格: {float(market_analysis.current_price):.2f} USDT
- 方向: {direction}
- 1h 价格变动: {float(market_analysis.price_change_1h) * 100:+.2f}%

⚠️ 风险提示
1h ADX(10)已确认强趋势（第3层），ADX周期从14缩短为10，反应速度提升约40%。
市场处于单边行情，任何逆势网格都会快速亏损。

💡 操作指令：
请立即终止当前所有网格。

🔄 恢复条件：
等待 1h ADX 回落到 {self.market_detector.recovery_adx_strong_1h} 以下再考虑重建。""".strip()

    def _generate_recovery_message(self, symbol: str, market_analysis: MarketAnalysis) -> str:
        """
        生成趋势恢复消息

        Args:
            symbol: 交易对
            market_analysis: 市场分析结果

        Returns:
            推送消息
        """
        return f"""
✅ 【网格信号灯】趋势减弱 - 可重新创建网格

📊 市场状态
- 交易对：{symbol}
- 1h ADX：{float(market_analysis.adx_1h):.1f}
- 4h ADX：{float(market_analysis.adx_4h):.1f}
- 价格：{float(market_analysis.current_price):.2f} USDT

市场已从强趋势/波动率异常恢复，可以重新创建网格或恢复挂单。
""".strip()
