"""
市场状态识别模块
基于多时间框架的技术指标判断市场状态
"""
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Dict, List, Optional, Tuple
import pandas as pd
import structlog

from shared.indicators import TechnicalIndicators
from shared.kline_service import KLineService


logger = structlog.get_logger()


class MarketState(Enum):
    """
    市场状态枚举（V2.4 三层预警架构）

    优先级: 价格行为紧急触发 > 15m ADX 早期预警 > 1h ADX(10) 趋势确认
           > 趋势急剧增强 > 极端强趋势 > 普通强趋势 > 波动率异常 > 弱趋势 > 震荡

    Attributes:
        PRICE_EMERGENCY: 价格行为紧急触发（第1层，1h变动≥3% 或 15m变动≥1.5%，0延迟）
        EARLY_WARNING_15M: 15m ADX 早期预警（第2层，15m ADX≥50 且 1h变动≥1%，比1h快4倍）
        TREND_CONFIRMED_1H: 1h ADX(10) 趋势确认（第3层，ADX周期从14缩短为10）
        TREND_ACCELERATING: 趋势急剧增强（2h内1h ADX上升 > 8点）
        EXTREME_STRONG_TREND: 极端强趋势（1h ADX≥40 且 4h ADX≥30，方向一致）
        NORMAL_STRONG_TREND: 普通强趋势（1h ADX≥30 且 4h ADX≥25，方向一致）
        VOLATILITY_ABNORMAL: 波动率异常（ATR飙升）
        WEAK_TREND: 弱趋势
        OSCILLATION: 震荡市场
    """
    PRICE_EMERGENCY = "价格行为紧急触发"
    EARLY_WARNING_15M = "15m ADX 早期预警"
    TREND_CONFIRMED_1H = "1h ADX 趋势确认"
    TREND_ACCELERATING = "趋势急剧增强"
    EXTREME_STRONG_TREND = "极端强趋势"
    NORMAL_STRONG_TREND = "普通强趋势"
    VOLATILITY_ABNORMAL = "波动率异常"
    WEAK_TREND = "弱趋势"
    OSCILLATION = "震荡市场"


@dataclass
class MarketAnalysis:
    """
    市场分析结果数据类（V2.4 三层预警架构）

    Attributes:
        state: 市场状态
        trend_strength: 趋势强度系数 k (0-0.5)
        adx_1h: 1小时ADX值
        adx_4h: 4小时ADX值
        adx_15m: 15分钟ADX值（V2.4新增，早期预警用）
        ema20_1h: 1小时EMA20值
        ema50_1h: 1小时EMA50值
        current_price: 当前价格
        atr_smooth: 平滑ATR值
        confidence: 置信度 (0-1)
        price_change_1h: 1小时价格变动率（V2.4新增，价格行为触发用）
        price_change_15m: 15分钟价格变动率（V2.4新增，价格行为触发用）
        ema20_4h: 4小时EMA20值
        ema50_4h: 4小时EMA50值
        atr_2h_ago: 2小时前ATR（波动率异常检测用）
        atr_abnormal_count: 连续异常计数
        atr_peak: 异常峰值ATR（恢复检测用）
        is_volatility_alarm_active: 波动率警报是否活跃
        adx_prev_1h: 上次巡检1h ADX值（趋势急剧增强检测用）
    """
    state: MarketState
    trend_strength: Decimal
    adx_1h: Decimal
    adx_4h: Decimal
    ema20_1h: Decimal
    ema50_1h: Decimal
    current_price: Decimal
    atr_smooth: Decimal
    confidence: Decimal
    adx_15m: Decimal = Decimal('0')
    price_change_1h: Decimal = Decimal('0')
    price_change_15m: Decimal = Decimal('0')
    ema20_4h: Decimal = Decimal('0')
    ema50_4h: Decimal = Decimal('0')
    atr_2h_ago: Decimal = Decimal('0')
    atr_abnormal_count: int = 0
    atr_peak: Decimal = Decimal('0')
    is_volatility_alarm_active: bool = False
    adx_prev_1h: Decimal = Decimal('0')

    def __post_init__(self):
        """参数验证"""
        if not isinstance(self.state, MarketState):
            raise ValueError(f"状态必须是 MarketState 类型，实际为 {type(self.state).__name__}")

        if self.trend_strength < 0 or self.trend_strength > Decimal('0.5'):
            raise ValueError(f"趋势强度必须在 0-0.5 之间，实际为 {self.trend_strength}")


class MarketStateDetector:
    """
    市场状态检测器

    基于多时间框架的技术指标判断市场状态：
    - 使用1小时作为主判断周期
    - 使用4小时作为趋势过滤器
    - 使用15分钟作为边界突破检测

    主要功能：
    - 多时间框架数据获取
    - ADX趋势强度判断
    - EMA方向判断
    - 市场状态综合判定
    - 趋势强度系数计算
    """

    def __init__(
        self,
        kline_service: KLineService,
        adx_extreme_strong: int = 40,
        adx_extreme_strong_4h: int = 30,
        adx_normal_strong: int = 30,
        adx_normal_strong_4h: int = 25,
        weak_trend_adx_lower: int = 25,
        weak_trend_adx_upper: int = 30,
        volatility_ratio_threshold: Decimal = Decimal('1.2'),
        volatility_consecutive_count: int = 2,
        volatility_recovery_ratio: Decimal = Decimal('1.2'),
        recovery_adx_strong_1h: int = 30,   # 强趋势恢复1h ADX阈值
        recovery_adx_strong_4h: int = 30,   # 强趋势恢复4h ADX阈值
        recovery_adx_weak_1h: int = 25,     # 弱趋势恢复1h ADX阈值
        recovery_adx_weak_4h: int = 25,     # 弱趋势恢复4h ADX阈值
        trend_strength_divisor: int = 30,
        atr_history_size: int = 5,
        ema_fast_period: int = 20,
        ema_slow_period: int = 50,
        atr_period: int = 14,
        emergency_adx_threshold: int = 55,
        trend_acceleration_threshold: int = 8,
        adx_history_size: int = 3,
        # V2.4 三层预警架构新增参数
        adx_period: int = 10,                               # ADX周期（从14缩短为10）
        price_emergency_1h: Decimal = Decimal('0.03'),       # 第1层：1h价格变动紧急阈值
        price_emergency_15m: Decimal = Decimal('0.015'),     # 第1层：15m价格变动紧急阈值
        adx_early_warning_15m: int = 50,                     # 第2层：15m ADX 早期预警阈值
        price_early_warning_1h: Decimal = Decimal('0.01'),   # 第2层：早期预警需1h价格变动≥1%
        # 置信度参数（V2.3从配置读取）
        confidence_emergency: Decimal = Decimal('0.99'),
        confidence_trend_accelerating: Decimal = Decimal('0.9'),
        confidence_extreme_strong: Decimal = Decimal('0.95'),
        confidence_volatility_abnormal: Decimal = Decimal('0.85'),
        confidence_normal_strong: Decimal = Decimal('0.8'),
        confidence_weak_trend: Decimal = Decimal('0.7'),
        confidence_oscillation: Decimal = Decimal('0.5'),
        # V2.4 新增置信度
        confidence_price_emergency: Decimal = Decimal('1.0'),
        confidence_early_warning_15m: Decimal = Decimal('0.92'),
        confidence_trend_confirmed_1h: Decimal = Decimal('0.95'),
    ):
        """
        初始化市场状态检测器（V2.4 三层预警架构）

        Args:
            kline_service: K线服务实例
            adx_extreme_strong: 极端强趋势1h ADX阈值（默认40）
            adx_extreme_strong_4h: 极端强趋势4h ADX确认阈值（默认30）
            adx_normal_strong: 普通强趋势1h ADX阈值（默认30）
            adx_normal_strong_4h: 普通强趋势4h ADX确认阈值（默认25）
            weak_trend_adx_lower: 弱趋势ADX下限（默认25）
            weak_trend_adx_upper: 弱趋势ADX上限（默认30）
            volatility_ratio_threshold: 波动率异常比率阈值（默认1.3）
            volatility_consecutive_count: 波动率连续异常次数阈值（默认2）
            volatility_recovery_ratio: 波动率恢复比率阈值（默认1.2）
            recovery_adx_strong_1h: 强趋势恢复1h ADX阈值（默认30）
            recovery_adx_strong_4h: 强趋势恢复4h ADX阈值（默认30）
            recovery_adx_weak_1h: 弱趋势恢复1h ADX阈值（默认25）
            recovery_adx_weak_4h: 弱趋势恢复4h ADX阈值（默认25）
            ema_fast_period: 快速EMA周期（默认20）
            ema_slow_period: 慢速EMA周期（默认50）
            atr_period: ATR周期（默认14）
            emergency_adx_threshold: 紧急极端趋势1h ADX阈值（默认50）
            trend_acceleration_threshold: 趋势急剧增强ADX上升阈值（默认20点）
            adx_history_size: ADX历史记录窗口大小（默认3）
            adx_period: ADX计算周期（V2.4：从14缩短为10，提升反应速度）
            price_emergency_1h: 第1层1h价格变动紧急阈值（默认3%）
            price_emergency_15m: 第1层15m价格变动紧急阈值（默认1.5%）
            adx_early_warning_15m: 第2层15m ADX早期预警阈值（默认50）
            price_early_warning_1h: 第2层早期预警需1h价格变动≥1%

        Raises:
            ValueError: 参数验证失败
        """
        if not kline_service:
            raise ValueError("K线服务不能为空")

        self.kline_service = kline_service
        self.adx_extreme_strong = adx_extreme_strong
        self.adx_extreme_strong_4h = adx_extreme_strong_4h
        self.adx_normal_strong = adx_normal_strong
        self.adx_normal_strong_4h = adx_normal_strong_4h
        self.weak_trend_adx_lower = weak_trend_adx_lower
        self.weak_trend_adx_upper = weak_trend_adx_upper
        self.volatility_ratio_threshold = volatility_ratio_threshold
        self.volatility_consecutive_count = volatility_consecutive_count
        self.volatility_recovery_ratio = volatility_recovery_ratio
        self.recovery_adx_strong_1h = recovery_adx_strong_1h
        self.recovery_adx_strong_4h = recovery_adx_strong_4h
        self.recovery_adx_weak_1h = recovery_adx_weak_1h
        self.recovery_adx_weak_4h = recovery_adx_weak_4h
        self.trend_strength_divisor = trend_strength_divisor
        self.atr_history_size = atr_history_size
        self.ema_fast_period = ema_fast_period
        self.ema_slow_period = ema_slow_period
        self.atr_period = atr_period
        # V2.3 新增参数
        self.emergency_adx_threshold = emergency_adx_threshold
        self.trend_acceleration_threshold = trend_acceleration_threshold
        self.adx_history_size = adx_history_size

        # V2.4 三层预警架构新增参数
        self.adx_period = adx_period
        self.price_emergency_1h = price_emergency_1h
        self.price_emergency_15m = price_emergency_15m
        self.adx_early_warning_15m = adx_early_warning_15m
        self.price_early_warning_1h = price_early_warning_1h

        # 置信度参数（V2.3从配置读取，消除硬编码）
        self.confidence_emergency = confidence_emergency
        self.confidence_trend_accelerating = confidence_trend_accelerating
        self.confidence_extreme_strong = confidence_extreme_strong
        self.confidence_volatility_abnormal = confidence_volatility_abnormal
        self.confidence_normal_strong = confidence_normal_strong
        self.confidence_weak_trend = confidence_weak_trend
        self.confidence_oscillation = confidence_oscillation
        # V2.4 新增置信度
        self.confidence_price_emergency = confidence_price_emergency
        self.confidence_early_warning_15m = confidence_early_warning_15m
        self.confidence_trend_confirmed_1h = confidence_trend_confirmed_1h

        # ATR历史记录（用于波动率异常检测）
        self._atr_history: List[Decimal] = []
        self._atr_abnormal_count: int = 0
        self._atr_peak: Decimal = Decimal('0')
        self._is_vol_alarm_active: bool = False

        # ADX历史记录（用于趋势急剧增强检测，V2.3新增）
        self._adx_history: List[Decimal] = []

        logger.info(
            "市场状态检测器初始化完成（V2.4 三层预警架构）",
            adx_extreme_strong=adx_extreme_strong,
            adx_normal_strong=adx_normal_strong,
            adx_normal_strong_4h=adx_normal_strong_4h,
            weak_trend_adx_lower=weak_trend_adx_lower,
            weak_trend_adx_upper=weak_trend_adx_upper,
            volatility_ratio_threshold=float(volatility_ratio_threshold),
            volatility_consecutive_count=volatility_consecutive_count,
            volatility_recovery_ratio=float(volatility_recovery_ratio),
            recovery_adx_strong_1h=recovery_adx_strong_1h,
            recovery_adx_strong_4h=recovery_adx_strong_4h,
            recovery_adx_weak_1h=recovery_adx_weak_1h,
            recovery_adx_weak_4h=recovery_adx_weak_4h,
            ema_fast=ema_fast_period,
            ema_slow=ema_slow_period,
            atr_period=atr_period,
            emergency_adx_threshold=emergency_adx_threshold,
            trend_acceleration_threshold=trend_acceleration_threshold,
            adx_history_size=adx_history_size,
            adx_period=adx_period,
            price_emergency_1h=float(price_emergency_1h),
            price_emergency_15m=float(price_emergency_15m),
            adx_early_warning_15m=adx_early_warning_15m,
            price_early_warning_1h=float(price_early_warning_1h)
        )

    async def detect_market_state(self, symbol: str) -> MarketAnalysis:
        """
        检测市场状态（V2.4 三层预警架构）

        基于多时间框架指标按优先级判断：
        价格行为紧急触发 > 15m ADX 早期预警 > 1h ADX(10) 趋势确认
        > 趋势急剧增强 > 极端强趋势 > 普通强趋势 > 波动率异常 > 弱趋势 > 震荡

        Args:
            symbol: 交易对

        Returns:
            市场分析结果

        Raises:
            ValueError: 参数验证失败
            Exception: 检测失败
        """
        if not symbol or not symbol.strip():
            raise ValueError("交易对不能为空")

        logger.info(f"开始检测市场状态: {symbol}")

        try:
            # 1. 获取多时间框架数据
            tf_data = await self._get_multi_timeframe_data(symbol)

            # 2. 计算1小时指标（使用缩短后的ADX周期）
            indicators_1h = self._calculate_indicators(tf_data['1h'], adx_period=self.adx_period)
            adx_1h = indicators_1h['adx']
            ema20_1h = indicators_1h['ema_fast']
            ema50_1h = indicators_1h['ema_slow']
            atr_1h = indicators_1h['atr']
            current_price = indicators_1h['current_price']

            # 3. 计算4小时指标（使用 tail 截取方式）
            indicators_4h = self._calculate_indicators(tf_data['4h'], use_tail_ema=True)
            adx_4h = indicators_4h['adx']
            ema20_4h = indicators_4h['ema_fast']
            ema50_4h = indicators_4h['ema_slow']

            # 4. V2.4新增：计算15分钟指标（用于早期预警）
            indicators_15m = self._calculate_indicators(tf_data['15m'])
            adx_15m = indicators_15m['adx']

            # 5. V2.4新增：计算价格变动率
            price_change_1h = self._calculate_price_change(tf_data['1h'])
            price_change_15m = self._calculate_price_change(tf_data['15m'])

            # 6. 计算平滑ATR（EMA平滑）
            atr_smooth_1h = self._calculate_smooth_atr(tf_data['1h'])

            # 7. 更新ATR历史并检测波动率异常
            atr_2h_ago, atr_abnormal_count, atr_peak, is_vol_alarm_active = \
                self._update_atr_history(atr_smooth_1h)

            # 8. 更新ADX历史并检测趋势急剧增强
            adx_prev_1h = self._update_adx_history(adx_1h)

            # 9. 判断市场状态（V2.4三层预警架构）
            state, confidence = self._determine_state(
                adx_1h=adx_1h,
                adx_4h=adx_4h,
                adx_15m=adx_15m,
                ema20_1h=ema20_1h,
                ema50_1h=ema50_1h,
                ema20_4h=ema20_4h,
                ema50_4h=ema50_4h,
                atr_smooth_1h=atr_smooth_1h,
                price_change_1h=price_change_1h,
                price_change_15m=price_change_15m
            )

            # 10. 计算趋势强度系数
            trend_strength = self._calculate_trend_strength(adx_1h)

            # 11. 构建结果
            analysis = MarketAnalysis(
                state=state,
                trend_strength=trend_strength,
                adx_1h=adx_1h,
                adx_4h=adx_4h,
                adx_15m=adx_15m,
                ema20_1h=ema20_1h,
                ema50_1h=ema50_1h,
                current_price=current_price,
                atr_smooth=atr_smooth_1h,
                confidence=confidence,
                price_change_1h=price_change_1h,
                price_change_15m=price_change_15m,
                ema20_4h=ema20_4h,
                ema50_4h=ema50_4h,
                atr_2h_ago=atr_2h_ago,
                atr_abnormal_count=atr_abnormal_count,
                atr_peak=atr_peak,
                is_volatility_alarm_active=is_vol_alarm_active,
                adx_prev_1h=adx_prev_1h
            )

            logger.info(
                f"{symbol} 市场状态检测完成",
                state=state.value,
                trend_strength=float(trend_strength),
                adx_1h=float(adx_1h),
                adx_4h=float(adx_4h),
                adx_15m=float(adx_15m),
                confidence=float(confidence),
                price_change_1h=f"{float(price_change_1h)*100:.2f}%",
                price_change_15m=f"{float(price_change_15m)*100:.2f}%",
                ema20_4h=float(ema20_4h),
                ema50_4h=float(ema50_4h),
                atr_abnormal_count=atr_abnormal_count,
                is_vol_alarm_active=is_vol_alarm_active,
                adx_prev_1h=float(adx_prev_1h)
            )

            return analysis

        except Exception as e:
            logger.error(
                f"检测市场状态失败: {symbol}",
                error=str(e),
                exc_info=True
            )
            raise

    async def _get_multi_timeframe_data(self, symbol: str) -> Dict[str, List[Dict]]:
        """
        获取多时间框架K线数据

        Args:
            symbol: 交易对

        Returns:
            多时间框架数据字典 {'15m': [...], '1h': [...], '4h': [...]}

        Raises:
            ValueError: 数据获取失败
        """
        # 定义需要的时间框架
        intervals = ['15m', '1h', '4h']

        # 获取多时间框架数据
        tf_data = await self.kline_service.get_multi_timeframe_data(
            symbol=symbol,
            intervals=intervals
        )

        # 验证数据完整性
        for interval in intervals:
            if interval not in tf_data or not tf_data[interval]:
                raise ValueError(f"缺少 {interval} 时间框架数据")

        logger.debug(
            "多时间框架数据获取完成",
            symbol=symbol,
            timeframes=list(tf_data.keys())
        )

        return tf_data

    def _calculate_indicators(self, klines: List[Dict], use_tail_ema: bool = False, adx_period: int = None) -> Dict[str, Decimal]:
        """
        计算技术指标（V2.4：支持自定义ADX周期）

        Args:
            klines: K线数据列表
            use_tail_ema: 是否使用尾部截取方式计算EMA（4h专用，减少计算量）
            adx_period: ADX计算周期（默认使用self.adx_period，V2.4新增）

        Returns:
            指标字典

        Raises:
            ValueError: 数据验证失败
        """
        if not klines or len(klines) < 50:
            raise ValueError(f"K线数据不足，至少需要50根K线，实际为 {len(klines) if klines else 0}")

        # 使用传入的adx_period或默认值
        if adx_period is None:
            adx_period = self.adx_period

        # 转换为DataFrame
        df = pd.DataFrame(klines)

        # 确保列名正确
        required_columns = ['open', 'high', 'low', 'close']
        for col in required_columns:
            if col not in df.columns:
                raise ValueError(f"K线数据缺少必需的列: {col}")

        # 计算ADX（使用自定义周期）
        adx_series = TechnicalIndicators.calculate_adx(df, period=adx_period)
        adx = Decimal(str(adx_series.iloc[-1])) if not pd.isna(adx_series.iloc[-1]) else Decimal('0')

        # 计算EMA（4h 使用 tail 截取方式，减少计算量）
        if use_tail_ema:
            ema_fast_series = df['close'].tail(50).ewm(span=self.ema_fast_period, adjust=False).mean()
            ema_slow_series = df['close'].tail(100).ewm(span=self.ema_slow_period, adjust=False).mean()
        else:
            ema_fast_series = TechnicalIndicators.calculate_ema(df, period=self.ema_fast_period)
            ema_slow_series = TechnicalIndicators.calculate_ema(df, period=self.ema_slow_period)

        ema_fast = Decimal(str(ema_fast_series.iloc[-1])) if not pd.isna(ema_fast_series.iloc[-1]) else Decimal('0')
        ema_slow = Decimal(str(ema_slow_series.iloc[-1])) if not pd.isna(ema_slow_series.iloc[-1]) else Decimal('0')

        # 计算ATR
        atr_series = TechnicalIndicators.calculate_atr(df, period=self.atr_period)
        atr = Decimal(str(atr_series.iloc[-1])) if not pd.isna(atr_series.iloc[-1]) else Decimal('0')

        # 获取当前价格
        current_price = Decimal(str(df['close'].iloc[-1]))

        return {
            'adx': adx,
            'ema_fast': ema_fast,
            'ema_slow': ema_slow,
            'atr': atr,
            'current_price': current_price
        }

    def _calculate_smooth_atr(self, klines: List[Dict]) -> Decimal:
        """
        计算平滑ATR

        使用EMA对ATR进行平滑处理

        Args:
            klines: K线数据列表

        Returns:
            平滑ATR值
        """
        if not klines or len(klines) < 50:
            logger.warning(f"K线数据不足，无法计算平滑ATR，返回默认值")
            return Decimal('0')

        # 转换为DataFrame
        df = pd.DataFrame(klines)

        # 计算ATR序列
        atr_series = TechnicalIndicators.calculate_atr(df, period=self.atr_period)

        # 使用EMA平滑ATR
        atr_smooth_series = atr_series.ewm(span=self.atr_period, adjust=False).mean()

        atr_smooth = Decimal(str(atr_smooth_series.iloc[-1])) if not pd.isna(atr_smooth_series.iloc[-1]) else Decimal('0')

        logger.debug(
            "平滑ATR计算完成",
            atr_smooth=float(atr_smooth)
        )

        return atr_smooth

    def _calculate_price_change(self, klines: List[Dict]) -> Decimal:
        """
        计算最近两根K线之间的价格变动率（V2.4新增，价格行为触发用）

        公式：abs(当前收盘价 - 前一根收盘价) / 前一根收盘价

        调用方需确保传入正确时间框架的K线数据：
        - 传入1h K线 → 计算1h价格变动率
        - 传入15m K线 → 计算15m价格变动率

        Args:
            klines: K线数据列表

        Returns:
            价格变动率（Decimal类型，绝对值），数据不足时返回 Decimal('0')
        """
        # 需要至少2根K线才能计算变动率
        if not klines or len(klines) < 2:
            logger.warning(
                f"K线数据不足，无法计算价格变动率",
                data_len=len(klines) if klines else 0
            )
            return Decimal('0')

        # 取最近2根K线，计算变动率
        current_close = Decimal(str(klines[-1]['close']))
        prev_close = Decimal(str(klines[-2]['close']))

        if prev_close <= Decimal('0'):
            logger.warning("前一根K线收盘价为0，无法计算价格变动率")
            return Decimal('0')

        # 计算绝对值变动率
        price_change = abs(current_close - prev_close) / prev_close

        logger.debug(
            "价格变动率计算完成",
            current_close=float(current_close),
            prev_close=float(prev_close),
            price_change=f"{float(price_change)*100:.2f}%"
        )

        return price_change

    def _determine_state(
        self,
        adx_1h: Decimal,
        adx_4h: Decimal,
        adx_15m: Decimal,
        ema20_1h: Decimal,
        ema50_1h: Decimal,
        ema20_4h: Decimal,
        ema50_4h: Decimal,
        atr_smooth_1h: Decimal,
        price_change_1h: Decimal,
        price_change_15m: Decimal
    ) -> Tuple[MarketState, Decimal]:
        """
        判断市场状态（V2.4 三层预警架构，优先级排序）

        优先级: 价格行为紧急触发（第1层）> 15m ADX 早期预警（第2层）> 1h ADX(10) 趋势确认（第3层）
               > 趋势急剧增强 > 极端强趋势 > 普通强趋势 > 波动率异常 > 弱趋势 > 震荡

        Args:
            adx_1h: 1小时ADX（使用adx_period周期计算）
            adx_4h: 4小时ADX
            adx_15m: 15分钟ADX（V2.4新增，早期预警用）
            ema20_1h: 1小时EMA20
            ema50_1h: 1小时EMA50
            ema20_4h: 4小时EMA20
            ema50_4h: 4小时EMA50
            atr_smooth_1h: 1小时平滑ATR
            price_change_1h: 1小时价格变动率（V2.4新增）
            price_change_15m: 15分钟价格变动率（V2.4新增）

        Returns:
            (市场状态, 置信度)
        """
        # 0. 价格行为紧急触发（第1层，最高优先级，0延迟）
        # 条件：1h变动≥3% 或 15m变动≥1.5%
        if price_change_1h >= self.price_emergency_1h or price_change_15m >= self.price_emergency_15m:
            logger.info(
                "检测到价格行为紧急触发（第1层）",
                price_change_1h=f"{float(price_change_1h)*100:.2f}%",
                price_change_15m=f"{float(price_change_15m)*100:.2f}%",
                threshold_1h=f"{float(self.price_emergency_1h)*100:.1f}%",
                threshold_15m=f"{float(self.price_emergency_15m)*100:.1f}%"
            )
            return MarketState.PRICE_EMERGENCY, self.confidence_price_emergency

        # 1. 15m ADX 早期预警（第2层，比1h快4倍）
        # 条件：15m ADX≥50 且 1h变动≥1%
        if adx_15m >= self.adx_early_warning_15m and price_change_1h >= self.price_early_warning_1h:
            logger.info(
                "检测到15m ADX早期预警（第2层）",
                adx_15m=float(adx_15m),
                threshold_15m=float(self.adx_early_warning_15m),
                price_change_1h=f"{float(price_change_1h)*100:.2f}%"
            )
            return MarketState.EARLY_WARNING_15M, self.confidence_early_warning_15m

        # 2. 1h ADX(10) 趋势确认（第3层，ADX周期从14缩短为10）
        # 条件：1h ADX(10) >= emergency_adx_threshold (55)
        if adx_1h >= self.emergency_adx_threshold:
            logger.info(
                "检测到1h ADX(10)趋势确认（第3层）",
                adx_1h=float(adx_1h),
                threshold=float(self.emergency_adx_threshold),
                adx_period=self.adx_period
            )
            return MarketState.TREND_CONFIRMED_1H, self.confidence_trend_confirmed_1h

        # 3. 趋势急剧增强（V2.3保留：2h内1h ADX上升 > trend_acceleration_threshold）
        if self._check_trend_acceleration(adx_1h):
            logger.info(
                "检测到趋势急剧增强",
                adx_1h=float(adx_1h),
                adx_history=[float(a) for a in self._adx_history]
            )
            return MarketState.TREND_ACCELERATING, self.confidence_trend_accelerating

        # 4. 极端强趋势（V2.3保留：需1h+4h双重确认，且方向一致）
        if adx_1h >= self.adx_extreme_strong and adx_4h >= self.adx_extreme_strong_4h:
            if self._is_direction_aligned(ema20_1h, ema50_1h, ema20_4h, ema50_4h):
                logger.info(
                    "检测到极端强趋势（1h+4h确认，方向一致）",
                    adx_1h=float(adx_1h),
                    threshold=float(self.adx_extreme_strong)
                )
                return MarketState.EXTREME_STRONG_TREND, self.confidence_extreme_strong
            else:
                logger.info(
                    "极端强趋势条件满足但方向不一致，降级为普通强趋势检查",
                    adx_1h=float(adx_1h),
                    adx_4h=float(adx_4h)
                )

        # 5. 普通强趋势（V2.3保留：需1h+4h双重确认，且方向一致）
        if adx_1h >= self.adx_normal_strong and adx_4h >= self.adx_normal_strong_4h:
            if self._is_direction_aligned(ema20_1h, ema50_1h, ema20_4h, ema50_4h):
                logger.info(
                    "检测到普通强趋势（1h+4h确认，方向一致）",
                    adx_1h=float(adx_1h),
                    adx_4h=float(adx_4h)
                )
                return MarketState.NORMAL_STRONG_TREND, self.confidence_normal_strong
            else:
                logger.info(
                    "普通强趋势条件满足但方向不一致，降级为弱趋势",
                    adx_1h=float(adx_1h),
                    adx_4h=float(adx_4h)
                )
                return MarketState.WEAK_TREND, self.confidence_weak_trend

        # 6. 波动率异常（V2.3保留）
        is_vol_abnormal, atr_abnormal_count, atr_peak, is_vol_alarm_active = \
            self._check_volatility_abnormal(atr_smooth_1h)
        if is_vol_alarm_active:
            logger.info(
                "检测到波动率异常",
                atr_smooth=float(atr_smooth_1h),
                atr_peak=float(atr_peak),
                abnormal_count=atr_abnormal_count
            )
            return MarketState.VOLATILITY_ABNORMAL, self.confidence_volatility_abnormal

        # 7. 弱趋势（V2.3保留）
        if self.weak_trend_adx_lower <= adx_1h < self.weak_trend_adx_upper and \
                adx_4h < self.adx_normal_strong_4h:
            logger.info(
                "检测到弱趋势",
                adx_1h=float(adx_1h),
                adx_4h=float(adx_4h)
            )
            return MarketState.WEAK_TREND, self.confidence_weak_trend

        # 8. 默认震荡
        logger.info(
            "检测到震荡市场",
            adx_1h=float(adx_1h),
            adx_4h=float(adx_4h)
        )
        return MarketState.OSCILLATION, self.confidence_oscillation

    def _calculate_trend_strength(self, adx_1h: Decimal) -> Decimal:
        """
        计算趋势强度系数 k

        公式：k = min(0.5, max(0, (ADX - weak_trend_adx_lower) / 30))

        Args:
            adx_1h: 1小时ADX值

        Returns:
            趋势强度系数 (0-0.5)
        """
        if adx_1h < Decimal(str(self.weak_trend_adx_lower)):
            return Decimal('0')

        # 计算k值（除数从配置读取）
        k = (adx_1h - Decimal(str(self.weak_trend_adx_lower))) / Decimal(str(self.trend_strength_divisor))

        # 限制在0-0.5之间
        k = max(Decimal('0'), min(Decimal('0.5'), k))

        logger.debug(
            "趋势强度系数计算完成",
            adx=float(adx_1h),
            k=float(k)
        )

        return k

    def _update_adx_history(self, adx_1h: Decimal) -> Decimal:
        """
        更新ADX历史记录并返回上一次巡检的ADX值（V2.3新增）

        维护最近 adx_history_size 次巡检的1h ADX值，用于趋势急剧增强检测。

        Args:
            adx_1h: 当前1小时ADX值

        Returns:
            上一次巡检的1h ADX值（若无历史则返回0）
        """
        # 在追加前记录上一次的值
        adx_prev = self._adx_history[-1] if self._adx_history else Decimal('0')

        # 追加当前ADX到历史列表
        self._adx_history.append(adx_1h)

        # 保持最大长度（从配置读取）
        if len(self._adx_history) > self.adx_history_size:
            self._adx_history.pop(0)

        logger.debug(
            "ADX历史更新完成",
            adx_current=float(adx_1h),
            adx_prev=float(adx_prev),
            history_len=len(self._adx_history)
        )

        return adx_prev

    def _check_trend_acceleration(self, adx_1h: Decimal) -> bool:
        """
        检测趋势是否急剧增强（V2.3新增）

        规则：2h内1h ADX上升 > trend_acceleration_threshold 点
        即当前ADX与历史记录中最旧值的差值超过阈值。

        Args:
            adx_1h: 当前1小时ADX值

        Returns:
            是否检测到趋势急剧增强
        """
        # 需要足够的历史记录才能做比较
        if len(self._adx_history) < self.adx_history_size:
            return False

        # 取历史中最旧的ADX值（列表第一个元素）
        adx_oldest = self._adx_history[0]

        # 计算ADX上升幅度
        acceleration = adx_1h - adx_oldest

        if acceleration > Decimal(str(self.trend_acceleration_threshold)):
            logger.info(
                "检测到趋势急剧增强",
                adx_current=float(adx_1h),
                adx_oldest=float(adx_oldest),
                acceleration=float(acceleration),
                threshold=float(self.trend_acceleration_threshold)
            )
            return True

        return False

    def _update_atr_history(self, atr_smooth_1h: Decimal) -> Tuple[Decimal, int, Decimal, bool]:
        """
        更新ATR历史记录并检测波动率异常

        维护最近5次巡检的ATR值，用于波动率异常检测。

        Args:
            atr_smooth_1h: 当前1小时平滑ATR

        Returns:
            (atr_2h_ago, atr_abnormal_count, atr_peak, is_volatility_alarm_active)
        """
        # 追加当前ATR到历史列表
        self._atr_history.append(atr_smooth_1h)

        # 保持最大长度（从配置读取）
        if len(self._atr_history) > self.atr_history_size:
            self._atr_history.pop(0)

        # 获取历史ATR（列表最旧元素）
        atr_2h_ago = self._atr_history[0] if len(self._atr_history) >= self.atr_history_size else Decimal('0')

        # 检测波动率异常
        is_abnormal, count, peak, is_alarm = self._check_volatility_abnormal(atr_smooth_1h)

        logger.debug(
            "ATR历史更新完成",
            atr_current=float(atr_smooth_1h),
            atr_2h_ago=float(atr_2h_ago),
            history_len=len(self._atr_history),
            abnormal_count=count,
            is_alarm_active=is_alarm
        )

        return atr_2h_ago, count, peak, is_alarm

    def _check_volatility_abnormal(self, current_atr: Decimal) -> Tuple[bool, int, Decimal, bool]:
        """
        检测波动率是否异常

        规则：
        1. 计算 ratio = 当前ATR / 2小时前ATR
        2. 若 ratio > volatility_ratio_threshold -> atr_abnormal_count += 1
        3. 若 ratio <= volatility_ratio_threshold -> atr_abnormal_count = 0
        4. 若 atr_abnormal_count >= volatility_consecutive_count 且未激活 -> 激活警报，记录 atr_peak
        5. 恢复检测：若警报已激活 -> recovery_ratio = 当前ATR / atr_peak
           - 若 recovery_ratio < volatility_recovery_ratio -> 恢复，重置所有状态

        Args:
            current_atr: 当前平滑ATR

        Returns:
            (is_abnormal, atr_abnormal_count, atr_peak, is_volatility_alarm_active)
        """
        # 需要足够的历史记录才能做比较
        if len(self._atr_history) < self.atr_history_size:
            return False, self._atr_abnormal_count, self._atr_peak, self._is_vol_alarm_active

        atr_2h_ago = self._atr_history[0]

        if atr_2h_ago <= Decimal('0'):
            logger.warning("2小时前ATR为0，跳过波动率异常检测")
            return False, self._atr_abnormal_count, self._atr_peak, self._is_vol_alarm_active

        ratio = current_atr / atr_2h_ago

        # 警报未激活时的检测逻辑
        if not self._is_vol_alarm_active:
            if ratio > self.volatility_ratio_threshold:
                self._atr_abnormal_count += 1
                logger.debug(
                    "波动率异常计数增加",
                    ratio=float(ratio),
                    threshold=float(self.volatility_ratio_threshold),
                    count=self._atr_abnormal_count
                )
            else:
                self._atr_abnormal_count = 0
                logger.debug("波动率恢复正常，异常计数重置")

            # 连续异常达到阈值，激活警报
            if self._atr_abnormal_count >= self.volatility_consecutive_count:
                self._is_vol_alarm_active = True
                self._atr_peak = current_atr
                logger.info(
                    "波动率警报已激活",
                    atr_peak=float(self._atr_peak),
                    ratio=float(ratio),
                    abnormal_count=self._atr_abnormal_count
                )
                return True, self._atr_abnormal_count, self._atr_peak, True

        # 警报已激活时的恢复检测
        else:
            if self._atr_peak > Decimal('0'):
                recovery_ratio = current_atr / self._atr_peak
                if recovery_ratio < self.volatility_recovery_ratio:
                    logger.info(
                        "波动率警报已恢复",
                        recovery_ratio=float(recovery_ratio),
                        threshold=float(self.volatility_recovery_ratio),
                        current_atr=float(current_atr),
                        atr_peak=float(self._atr_peak)
                    )
                    # 重置所有状态
                    self._is_vol_alarm_active = False
                    self._atr_abnormal_count = 0
                    self._atr_peak = Decimal('0')
                    return False, 0, Decimal('0'), False
            else:
                logger.warning("atr_peak为0，跳过恢复检测，重置警报状态")
                self._is_vol_alarm_active = False
                self._atr_abnormal_count = 0
                self._atr_peak = Decimal('0')
                return False, 0, Decimal('0'), False

        return self._is_vol_alarm_active, self._atr_abnormal_count, self._atr_peak, self._is_vol_alarm_active

    def _is_direction_aligned(
        self,
        ema20_1h: Decimal,
        ema50_1h: Decimal,
        ema20_4h: Decimal,
        ema50_4h: Decimal
    ) -> bool:
        """
        判断1h和4h的EMA方向是否一致

        方向一致意味着两个时间框架的多空排列相同：
        - 1h 多头 (EMA20 > EMA50) 且 4h 多头 (EMA20 > EMA50)
        - 1h 空头 (EMA20 < EMA50) 且 4h 空头 (EMA20 < EMA50)

        Args:
            ema20_1h: 1小时EMA20
            ema50_1h: 1小时EMA50
            ema20_4h: 4小时EMA20
            ema50_4h: 4小时EMA50

        Returns:
            方向是否一致
        """
        return (ema20_1h > ema50_1h and ema20_4h > ema50_4h) or \
               (ema20_1h < ema50_1h and ema20_4h < ema50_4h)

    async def check_boundary_breakthrough(
        self,
        symbol: str,
        upper_boundary: Decimal,
        lower_boundary: Decimal
    ) -> Tuple[bool, Optional[str]]:
        """
        检查价格是否突破网格边界

        Args:
            symbol: 交易对
            upper_boundary: 上边界价格
            lower_boundary: 下边界价格

        Returns:
            (是否突破, 突破方向 'UP'/'DOWN'/None)

        Raises:
            ValueError: 参数验证失败
        """
        if not symbol or not symbol.strip():
            raise ValueError("交易对不能为空")

        if upper_boundary <= lower_boundary:
            raise ValueError(f"上边界 {upper_boundary} 必须大于下边界 {lower_boundary}")

        try:
            # 获取15分钟K线数据
            klines = await self.kline_service.get_klines(
                symbol=symbol,
                interval='15m',
                limit=10
            )

            if not klines:
                logger.warning(f"{symbol} 无法获取15分钟K线数据")
                return False, None

            # 获取最新价格
            current_price = Decimal(str(klines[-1]['close']))

            # 检查是否突破边界
            if current_price > upper_boundary:
                logger.info(
                    f"{symbol} 价格突破上边界",
                    current_price=float(current_price),
                    upper_boundary=float(upper_boundary)
                )
                return True, 'UP'

            elif current_price < lower_boundary:
                logger.info(
                    f"{symbol} 价格突破下边界",
                    current_price=float(current_price),
                    lower_boundary=float(lower_boundary)
                )
                return True, 'DOWN'

            return False, None

        except Exception as e:
            logger.error(
                f"检查边界突破失败: {symbol}",
                error=str(e),
                exc_info=True
            )
            return False, None
