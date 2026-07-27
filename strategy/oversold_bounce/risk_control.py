"""
OBPC 超跌反弹策略 - 公共风控模块

本模块封装三层风控判定逻辑，是回测与每日扫描共用的唯一风控入口。
通过依赖注入接收 MarketContextProvider 和 MonthlySignalCounter，
确保判定逻辑一致而数据来源可差异化。

三层风控执行顺序（任一环节过滤即终止后续）：
    1. 动态评分阈值过滤（前置，避免低分信号占用单月名额）
    2. 大盘趋势过滤（20日 + 60日均线）
    3. 单月信号数量上限检查

设计原则：
    - 公共逻辑抽取：避免在 daily_scan.py 和 backtest_obpc.py 中重复实现
    - 依赖倒置：风控控制器依赖抽象接口，不依赖具体数据来源
    - 单一职责：风控模块只负责"是否放行"的判定
    - 配置驱动：所有业务阈值从配置读取，代码中不出现魔法数字
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field, replace
from typing import Dict, Optional, Tuple

import pandas as pd

from utils.logger import get_logger

logger = get_logger()


# ==================== V2.1 公共函数 ====================

def determine_market_strength(
    index_close: float,
    index_ma20: Optional[float],
    index_ma60: Optional[float],
    ma20_slope: Optional[float],
    ma60_slope: Optional[float],
) -> str:
    """
    判定市场强度（V2.1 新增，供评分调整使用）

    判定优先级：extreme_weak > strong > weak > unknown

    业务规则：
        - BR-3.3: 强势市场 = 收盘价 >= MA20 且 MA20 斜率 > 0
        - BR-3.4: 弱势市场 = 收盘价 < MA20 或 MA20 斜率 <= 0
        - BR-3.5: 极弱市场 = 收盘价 < MA60 且 MA60 斜率 <= 0

    Args:
        index_close: 当日收盘价
        index_ma20: MA20 值（数据不足时为 None）
        index_ma60: MA60 值（数据不足时为 None）
        ma20_slope: MA20 斜率（数据不足时为 None）
        ma60_slope: MA60 斜率（数据不足时为 None）

    Returns:
        str: 'strong' / 'weak' / 'extreme_weak' / 'unknown'
    """
    # 第一步：判定极弱市场（跌破 MA60 且 MA60 向下）
    if index_ma60 is not None and ma60_slope is not None:
        if index_close < index_ma60 and ma60_slope <= 0:
            return "extreme_weak"

    # 第二步：判定强势市场（收盘价 >= MA20 且 MA20 向上）
    if index_ma20 is None or ma20_slope is None:
        # MA20 数据不足，无法判定强弱
        return "unknown"

    if index_close >= index_ma20 and ma20_slope > 0:
        return "strong"

    # 第三步：其余情况视为弱势市场
    return "weak"


# ==================== 数据类定义 ====================

@dataclass
class RiskControlConfig:
    """
    风控配置数据类

    从 config.yaml 的 strategies.oversold_bounce.params 构建。
    所有参数均有默认值，旧配置文件可正常运行（向后兼容）。

    V2.1 新增字段默认关闭相关功能，确保使用 V2.0 配置时行为完全一致。
    """

    # ===== 风控1：大盘趋势过滤（V2.0 原有） =====
    index_filter_enabled: bool = True
    """大盘过滤总开关"""

    index_code: str = "000300.SH"
    """大盘指数代码"""

    index_ma_period: int = 20
    """短周期均线天数（原有）"""

    index_ma_period_long: int = 60
    """长周期均线天数（新增）"""

    long_ma_filter_enabled: bool = True
    """60日均线过滤开关（新增）"""

    # ===== 风控2：单月信号上限 =====
    max_signals_per_month: int = 12
    """单月信号数上限（新增）"""

    # ===== 风控3：动态评分阈值 =====
    score_filter_enabled: bool = True
    """评分过滤总开关（新增）"""

    score_threshold: float = 60
    """默认评分阈值（非弱势市场的基础阈值），确保只有高质量信号进入单月名额竞争（新增）"""

    score_threshold_weak: float = 75
    """弱势市场评分阈值（新增）"""

    weak_market_condition: str = "below_ma60"
    """弱势市场判定条件（新增），目前仅支持 below_ma60"""

    # ===== V2.1 新增：大盘趋势过滤扩展 =====
    index_filter_method: str = "ma_position"
    """大盘过滤方法（V2.1 新增）：'ma_position'（V2.0 均线位置）或 'macd'（V2.1 动量）"""

    macd_fast: int = 12
    """MACD 快线周期（V2.1 新增），method='macd' 时生效"""

    macd_slow: int = 26
    """MACD 慢线周期（V2.1 新增），method='macd' 时生效"""

    macd_signal: int = 9
    """MACD 信号线周期（V2.1 新增），method='macd' 时生效"""

    macd_condition: str = "dif > 0"
    """MACD 过滤条件表达式（V2.1 新增），目前支持 'dif > 0'"""

    # ===== V2.1 新增：均线斜率过滤 =====
    slope_filter_enabled: bool = False
    """斜率过滤开关（V2.1 新增，默认关闭）"""

    slope_ma_period: int = 60
    """斜率计算的均线周期（V2.1 新增），取值 20 或 60"""

    slope_lookback: int = 5
    """斜率回看天数（V2.1 新增），当前均线 vs N 天前均线"""

    slope_threshold: float = 0.002
    """斜率阈值（V2.1 新增，0.2%），大于该值才视为均线向上"""

    min_index_data_days: int = 30
    """最小指数数据获取天数（V2.1 新增），确保数据量足够计算指标"""

    # ===== V2.1 新增：评分市场状态调整 =====
    score_adjustment_enabled: bool = False
    """评分调整开关（V2.1 新增，默认关闭）"""

    score_multiplier_strong: float = 1.2
    """强势市场评分放大系数（V2.1 新增）"""

    score_multiplier_weak: float = 0.8
    """弱势市场评分缩小系数（V2.1 新增）"""

    score_multiplier_extreme_weak: float = 0.0
    """极弱市场评分系数（V2.1 新增，默认 0.0 即评分归零）"""

    score_max_limit: float = 100.0
    """评分上限（V2.1 新增），调整后评分不超过此值"""

    score_min_limit: float = 0.0
    """评分下限（V2.1 新增），调整后评分不低于此值"""

    def __post_init__(self) -> None:
        """
        配置合法性校验（V2.1 新增，fail-fast）

        在对象创建后立即校验关键配置项的合法性，
        避免运行时静默放行不支持的配置值导致风控失效。

        Raises:
            ValueError: 当配置值不合法时抛出
        """
        # 校验大盘过滤方法
        if self.index_filter_method not in ("ma_position", "macd"):
            raise ValueError(
                f"不支持的 index_filter_method={self.index_filter_method}，"
                f"仅支持 'ma_position' 或 'macd'"
            )

        # 校验斜率均线周期
        if self.slope_ma_period not in (20, 60):
            raise ValueError(
                f"不支持的 slope_ma_period={self.slope_ma_period}，"
                f"仅支持 20 或 60"
            )

        # 校验 MACD 过滤条件
        if self.macd_condition != "dif > 0":
            raise ValueError(
                f"不支持的 macd_condition={self.macd_condition}，"
                f"目前仅支持 'dif > 0'"
            )

    @classmethod
    def from_params(cls, params: Dict) -> "RiskControlConfig":
        """
        从策略参数字典构建风控配置

        V2.1 扩展：兼容 V2.0 配置文件，缺失的 V2.1 键使用默认值，
        确保使用 V2.0 配置时行为完全一致（向后兼容）。

        Args:
            params: config.yaml 中 strategies.oversold_bounce.params 字典

        Returns:
            RiskControlConfig: 风控配置对象
        """
        index_filter = params.get("index_filter", {}) or {}
        score_filter = params.get("score_filter", {}) or {}
        score_adjustment = params.get("score_adjustment", {}) or {}

        return cls(
            # ===== V2.0 原有字段（保持不变） =====
            index_filter_enabled=index_filter.get("enabled", True),
            index_code=index_filter.get("index_code", "000300.SH"),
            index_ma_period=index_filter.get("index_ma_period", 20),
            index_ma_period_long=index_filter.get("index_ma_period_long", 60),
            long_ma_filter_enabled=index_filter.get("long_ma_filter_enabled", True),
            max_signals_per_month=params.get("max_signals_per_month", 12),
            score_filter_enabled=score_filter.get("enabled", True),
            score_threshold=score_filter.get("score_threshold", 60),
            score_threshold_weak=score_filter.get("score_threshold_weak", 75),
            weak_market_condition=score_filter.get("weak_market_condition", "below_ma60"),
            # ===== V2.1 新增：大盘趋势过滤扩展 =====
            index_filter_method=index_filter.get("method", "ma_position"),
            macd_fast=index_filter.get("macd_fast", 12),
            macd_slow=index_filter.get("macd_slow", 26),
            macd_signal=index_filter.get("macd_signal", 9),
            macd_condition=index_filter.get("condition", "dif > 0"),
            # ===== V2.1 新增：均线斜率过滤 =====
            slope_filter_enabled=index_filter.get("slope_filter_enabled", False),
            slope_ma_period=index_filter.get("slope_ma_period", 60),
            slope_lookback=index_filter.get("slope_lookback", 5),
            slope_threshold=index_filter.get("slope_threshold", 0.002),
            min_index_data_days=index_filter.get("min_index_data_days", 30),
            # ===== V2.1 新增：评分市场状态调整 =====
            score_adjustment_enabled=score_adjustment.get("enabled", False),
            score_multiplier_strong=score_adjustment.get(
                "score_multiplier_strong", 1.2
            ),
            score_multiplier_weak=score_adjustment.get(
                "score_multiplier_weak", 0.8
            ),
            score_multiplier_extreme_weak=score_adjustment.get(
                "score_multiplier_extreme_weak", 0.0
            ),
            score_max_limit=score_adjustment.get("score_max_limit", 100.0),
            score_min_limit=score_adjustment.get("score_min_limit", 0.0),
        )


@dataclass
class MarketContext:
    """
    大盘环境上下文

    封装某一交易日的大盘环境信息，供风控控制器使用。
    由 MarketContextProvider 提供。

    V2.1 新增字段均为 Optional，不启用对应功能时为 None，确保向后兼容。
    """

    current_date: str
    """当前交易日，格式 YYYY-MM-DD"""

    index_close: float
    """大盘指数收盘价"""

    index_ma20: Optional[float] = None
    """大盘20日均线值，数据不足时为 None"""

    index_ma60: Optional[float] = None
    """大盘60日均线值，数据不足时为 None"""

    is_weak_market: bool = False
    """是否弱势市场（沪深300 < MA60）"""

    data_sufficient: bool = True
    """大盘数据是否充足（用于决定是否跳过60日均线过滤）"""

    # ===== V2.1 新增字段 =====
    index_dif: Optional[float] = None
    """大盘 MACD DIF 值（V2.1 新增），method='macd' 时计算"""

    index_dea: Optional[float] = None
    """大盘 MACD DEA 值（V2.1 新增），method='macd' 时计算"""

    index_hist: Optional[float] = None
    """大盘 MACD 柱状值（V2.1 新增，DIF - DEA）"""

    ma20_slope: Optional[float] = None
    """大盘 MA20 斜率（V2.1 新增），slope_filter 或 score_adjustment 启用时计算"""

    ma60_slope: Optional[float] = None
    """大盘 MA60 斜率（V2.1 新增），slope_filter 或 score_adjustment 启用时计算"""

    market_strength: str = "unknown"
    """市场强度标签（V2.1 新增）：strong/weak/extreme_weak/unknown"""

    @property
    def above_ma20(self) -> bool:
        """大盘是否在20日均线上方"""
        if self.index_ma20 is None:
            # 数据不足时放行
            return True
        return self.index_close >= self.index_ma20

    @property
    def above_ma60(self) -> bool:
        """大盘是否在60日均线上方"""
        if self.index_ma60 is None:
            # 数据不足时放行
            return True
        return self.index_close >= self.index_ma60

    @property
    def macd_bullish(self) -> bool:
        """
        MACD 是否多头（DIF > 0）

        V2.1 新增属性：数据不足时返回 True（放行），避免数据不足导致误过滤。
        """
        if self.index_dif is None:
            return True
        return self.index_dif > 0

    @property
    def ma20_slope_up(self) -> bool:
        """
        MA20 斜率是否向上（> 0）

        V2.1 新增属性：数据不足时返回 True（放行）。
        注意：此处仅判断斜率是否大于 0，具体阈值由 RiskController 的配置决定。
        """
        if self.ma20_slope is None:
            return True
        return self.ma20_slope > 0

    @property
    def ma60_slope_up(self) -> bool:
        """
        MA60 斜率是否向上（> 0）

        V2.1 新增属性：数据不足时返回 True（放行）。
        """
        if self.ma60_slope is None:
            return True
        return self.ma60_slope > 0


@dataclass
class RiskControlResult:
    """
    风控检查结果

    封装三层风控的最终判定结果和详细信息。
    """

    passed: bool
    """是否通过全部风控检查"""

    filter_rule: str = ""
    """被过滤的规则名（passed=True 时为空）：
    - 'long_ma_filter'：大盘趋势过滤（V2.0 均线位置）
    - 'monthly_limit'：单月信号上限
    - 'score_threshold'：评分阈值过滤
    - 'macd_filter'：MACD 动量过滤（V2.1 新增）
    - 'slope_filter'：均线斜率过滤（V2.1 新增）
    - 'score_adjustment'：评分调整归零（V2.1 新增，极弱市场）
    """

    filter_reason: str = ""
    """过滤原因的中文描述（用于日志输出）"""

    details: Dict = field(default_factory=dict)
    """详细信息（如当前阈值、当月计数、大盘环境等）"""

    effective_threshold: Optional[float] = None
    """当前生效的评分阈值（无论是否被过滤，都记录用于调试）"""


# ==================== 抽象接口定义 ====================

class MarketContextProvider(ABC):
    """
    大盘环境上下文提供者抽象基类

    职责：提供指定交易日的大盘环境上下文。
    两种实现：
        - RealtimeMarketContextProvider：每日扫描实时查询数据库
        - PreloadedMarketContextProvider：回测预加载指数数据构建字典
    """

    @abstractmethod
    def get_context(self, date_str: str) -> Optional[MarketContext]:
        """
        获取指定日期的大盘环境上下文

        Args:
            date_str: 日期字符串，格式 YYYY-MM-DD

        Returns:
            Optional[MarketContext]: 大盘上下文，日期不存在时返回 None
        """
        raise NotImplementedError


class MonthlySignalCounter(ABC):
    """
    单月信号计数器抽象基类

    职责：提供指定月份的已产出信号数，并在新信号产出时增加计数。
    两种实现：
        - DatabaseMonthlyCounter：每日扫描查询数据库 scan_results 表
        - InMemoryMonthlyCounter：回测在内存中累计计数
    """

    @abstractmethod
    def get_count(self, year_month: str) -> int:
        """
        获取指定月份的已产出信号数

        Args:
            year_month: 月份字符串，格式 YYYY-MM

        Returns:
            int: 信号数
        """
        raise NotImplementedError

    @abstractmethod
    def increment(self, year_month: str) -> None:
        """
        增加指定月份的信号计数

        在信号通过全部风控检查并确认产出后调用。

        Args:
            year_month: 月份字符串，格式 YYYY-MM
        """
        raise NotImplementedError


# ==================== MarketContextProvider 实现 ====================

class RealtimeMarketContextProvider(MarketContextProvider):
    """
    实时大盘环境上下文提供者

    用于每日扫描场景：每次调用都从数据库获取最新指数数据并计算均线。
    适用于单次扫描只关心"当日"大盘环境的场景。

    V2.1 扩展：根据 config 决定是否计算 MACD、均线斜率、市场强度。
    """

    def __init__(self, db, config: RiskControlConfig):
        """
        初始化实时大盘上下文提供者

        Args:
            db: 数据库管理器（DatabaseManager 实例）
            config: 风控配置
        """
        self.db = db
        self.config = config

    def get_context(self, date_str: str) -> Optional[MarketContext]:
        """
        实时获取大盘上下文

        实现要点：
            1. 从数据库获取指数 K 线（取最近满足所有指标计算需求的天数）
            2. 计算 MA20 和 MA60
            3. 数据不足60天时，index_ma60=None，data_sufficient=False
            4. 判断 is_weak_market
            5. V2.1 新增：根据 config 计算 MACD、斜率、市场强度

        Args:
            date_str: 日期字符串，格式 YYYY-MM-DD

        Returns:
            Optional[MarketContext]: 大盘上下文，获取失败时返回 None
        """
        # 计算需要获取的指数数据天数：需满足长周期均线、MACD、斜率的所有需求
        required_days = max(
            self.config.index_ma_period_long + 10,
            self.config.macd_slow + self.config.macd_signal + 10,
            self.config.slope_ma_period + self.config.slope_lookback + 10,
            self.config.min_index_data_days,
        )

        try:
            index_df = self.db.get_index_kline(
                self.config.index_code, days=required_days
            )
        except Exception as e:
            logger.warning(f"获取指数 {self.config.index_code} 数据失败：{e}")
            return None

        if index_df is None or index_df.empty:
            logger.warning(f"指数 {self.config.index_code} 数据为空，跳过大盘过滤")
            return None

        # 取最新一条作为当日收盘价
        current_close = float(index_df["close"].iloc[-1])

        # 计算短周期均线（MA20）
        index_ma20 = self._calculate_ma(
            index_df, self.config.index_ma_period
        )

        # 计算长周期均线（MA60）
        index_ma60 = self._calculate_ma(
            index_df, self.config.index_ma_period_long
        )

        # 数据是否充足：数据条数 >= 长周期均线天数
        data_sufficient = len(index_df) >= self.config.index_ma_period_long
        if not data_sufficient:
            logger.warning(
                f"指数数据不足 {self.config.index_ma_period_long} 天"
                f"（实际 {len(index_df)} 天），跳过60日均线过滤"
            )

        # 判断弱势市场：沪深300 < MA60
        is_weak_market = False
        if index_ma60 is not None and current_close < index_ma60:
            is_weak_market = True

        # ===== V2.1 新增：MACD 计算 =====
        index_dif = None
        index_dea = None
        index_hist = None
        if self.config.index_filter_method == "macd":
            index_dif, index_dea, index_hist = self._calculate_macd(index_df)

        # ===== V2.1 新增：斜率计算 =====
        ma20_slope = None
        ma60_slope = None
        if self.config.slope_filter_enabled or self.config.score_adjustment_enabled:
            # 斜率过滤启用时，计算 slope_ma_period 对应的斜率
            if self.config.slope_filter_enabled:
                if self.config.slope_ma_period == 20:
                    ma20_slope = self._calculate_slope(
                        index_df, self.config.index_ma_period
                    )
                elif self.config.slope_ma_period == 60:
                    ma60_slope = self._calculate_slope(
                        index_df, self.config.index_ma_period_long
                    )
            # 评分调整启用时，需要 MA20 和 MA60 的斜率
            if self.config.score_adjustment_enabled:
                if ma20_slope is None:
                    ma20_slope = self._calculate_slope(
                        index_df, self.config.index_ma_period
                    )
                if ma60_slope is None:
                    ma60_slope = self._calculate_slope(
                        index_df, self.config.index_ma_period_long
                    )

        # ===== V2.1 新增：市场强度判定 =====
        market_strength = "unknown"
        if self.config.score_adjustment_enabled:
            market_strength = determine_market_strength(
                current_close, index_ma20, index_ma60, ma20_slope, ma60_slope
            )

        return MarketContext(
            current_date=date_str,
            index_close=current_close,
            index_ma20=index_ma20,
            index_ma60=index_ma60,
            is_weak_market=is_weak_market,
            data_sufficient=data_sufficient,
            # V2.1 新增字段
            index_dif=index_dif,
            index_dea=index_dea,
            index_hist=index_hist,
            ma20_slope=ma20_slope,
            ma60_slope=ma60_slope,
            market_strength=market_strength,
        )

    def _calculate_ma(
        self, index_df: pd.DataFrame, period: int
    ) -> Optional[float]:
        """
        计算指定周期的均线值

        Args:
            index_df: 指数 K 线数据
            period: 均线周期

        Returns:
            Optional[float]: 均线值，数据不足时返回 None
        """
        if index_df is None or len(index_df) < period:
            return None
        return float(index_df["close"].iloc[-period:].mean())

    def _calculate_macd(
        self, index_df: pd.DataFrame
    ) -> Tuple[Optional[float], Optional[float], Optional[float]]:
        """
        计算大盘 MACD 指标（DIF、DEA、HIST）（V2.1 新增）

        计算公式：
            EMA_fast = close 的 macd_fast 日指数移动平均
            EMA_slow = close 的 macd_slow 日指数移动平均
            DIF = EMA_fast - EMA_slow
            DEA = DIF 的 macd_signal 日指数移动平均
            HIST = DIF - DEA

        业务规则：
            - BR-1.5: 数据不足 macd_slow + macd_signal 天时返回 None 并警告

        Args:
            index_df: 指数 K 线数据（按日期升序）

        Returns:
            Tuple: (dif, dea, hist)，数据不足时返回 (None, None, None)
        """
        required_len = self.config.macd_slow + self.config.macd_signal
        if index_df is None or len(index_df) < required_len:
            logger.warning(
                f"指数数据不足 {required_len} 天"
                f"（macd_slow={self.config.macd_slow} + macd_signal={self.config.macd_signal}），"
                f"跳过 MACD 计算"
            )
            return None, None, None

        close_series = index_df["close"]
        # 计算快慢 EMA
        ema_fast = close_series.ewm(
            span=self.config.macd_fast, adjust=False
        ).mean()
        ema_slow = close_series.ewm(
            span=self.config.macd_slow, adjust=False
        ).mean()
        # 计算 DIF 和 DEA
        dif = ema_fast - ema_slow
        dea = dif.ewm(
            span=self.config.macd_signal, adjust=False
        ).mean()
        # 计算柱状值
        hist = dif - dea

        return float(dif.iloc[-1]), float(dea.iloc[-1]), float(hist.iloc[-1])

    def _calculate_slope(
        self, index_df: pd.DataFrame, ma_period: int
    ) -> Optional[float]:
        """
        计算指定周期均线的斜率（V2.1 新增）

        计算公式：
            ma = close 的 ma_period 日简单移动平均
            slope = (ma.iloc[-1] - ma.iloc[-slope_lookback - 1]) / ma.iloc[-slope_lookback - 1]

        业务规则：
            - BR-2.6: 数据不足 ma_period + slope_lookback 天时返回 None 并警告

        Args:
            index_df: 指数 K 线数据
            ma_period: 均线周期（20 或 60）

        Returns:
            Optional[float]: 斜率值，数据不足时返回 None
        """
        required_len = ma_period + self.config.slope_lookback
        if index_df is None or len(index_df) < required_len:
            logger.warning(
                f"指数数据不足 {required_len} 天"
                f"（ma_period={ma_period} + slope_lookback={self.config.slope_lookback}），"
                f"跳过斜率计算"
            )
            return None

        # 计算均线
        ma = index_df["close"].rolling(window=ma_period).mean()
        # 取当前均线值和 slope_lookback 天前的均线值
        ma_current = ma.iloc[-1]
        ma_past = ma.iloc[-self.config.slope_lookback - 1]

        if pd.isna(ma_current) or pd.isna(ma_past) or ma_past == 0:
            return None

        slope = (ma_current - ma_past) / ma_past
        return float(slope)


class PreloadedMarketContextProvider(MarketContextProvider):
    """
    预加载大盘环境上下文提供者

    用于回测场景：启动时一次性加载全部指数数据，预计算所有日期的上下文，
    构建日期到上下文的映射字典，查询时 O(1)。

    V2.1 扩展：启动时根据 config 预计算 MACD 列、斜率列、市场强度，
    使用 pandas 向量化计算，O(n) 复杂度，对回测耗时影响 < 5%。
    """

    def __init__(
        self,
        index_df: pd.DataFrame,
        config: RiskControlConfig,
    ):
        """
        初始化预加载大盘上下文提供者

        Args:
            index_df: 完整的指数 K 线数据（回测区间内）
            config: 风控配置
        """
        self.config = config
        self.context_map: Dict[str, MarketContext] = {}
        self._build_context_map(index_df)

    def _build_context_map(self, index_df: pd.DataFrame) -> None:
        """
        预计算所有日期的大盘上下文

        实现要点：
            1. 计算 MA20 和 MA60 列
            2. V2.1 新增：根据 config 预计算 MACD 列（dif/dea/hist）
            3. V2.1 新增：根据 config 预计算斜率列（ma20_slope/ma60_slope）
            4. 遍历每个交易日，构建 MarketContext（含 V2.1 字段）
            5. 存入 context_map 字典

        Args:
            index_df: 完整的指数 K 线数据
        """
        if index_df is None or index_df.empty:
            logger.warning("指数数据为空，无法构建大盘上下文映射")
            return

        df = index_df.copy()
        # 计算短周期均线列
        df["ma20"] = df["close"].rolling(
            window=self.config.index_ma_period, min_periods=1
        ).mean()
        # 计算长周期均线列
        df["ma60"] = df["close"].rolling(
            window=self.config.index_ma_period_long, min_periods=1
        ).mean()

        # ===== V2.1 新增：预计算 MACD 列 =====
        if self.config.index_filter_method == "macd":
            df["ema_fast"] = df["close"].ewm(
                span=self.config.macd_fast, adjust=False
            ).mean()
            df["ema_slow"] = df["close"].ewm(
                span=self.config.macd_slow, adjust=False
            ).mean()
            df["dif"] = df["ema_fast"] - df["ema_slow"]
            df["dea"] = df["dif"].ewm(
                span=self.config.macd_signal, adjust=False
            ).mean()
            df["hist"] = df["dif"] - df["dea"]

        # ===== V2.1 新增：预计算斜率列 =====
        if self.config.slope_filter_enabled or self.config.score_adjustment_enabled:
            # MA20 斜率：(当前 MA20 - N 天前 MA20) / N 天前 MA20
            df["ma20_slope"] = (
                df["ma20"] - df["ma20"].shift(self.config.slope_lookback)
            ) / df["ma20"].shift(self.config.slope_lookback)
            # MA60 斜率
            df["ma60_slope"] = (
                df["ma60"] - df["ma60"].shift(self.config.slope_lookback)
            ) / df["ma60"].shift(self.config.slope_lookback)

        # MACD 数据充足的最小索引（macd_slow + macd_signal - 1）
        macd_min_idx = self.config.macd_slow + self.config.macd_signal - 1
        # MA20 斜率数据充足的最小索引
        ma20_slope_min_idx = (
            self.config.index_ma_period + self.config.slope_lookback - 1
        )
        # MA60 斜率数据充足的最小索引
        ma60_slope_min_idx = (
            self.config.index_ma_period_long + self.config.slope_lookback - 1
        )

        for idx in range(len(df)):
            row = df.iloc[idx]
            date_str = row["date"].strftime("%Y-%m-%d") if hasattr(
                row["date"], "strftime"
            ) else str(row["date"])[:10]

            current_close = float(row["close"])

            # 短周期均线：数据不足时为 None
            index_ma20 = None
            if idx + 1 >= self.config.index_ma_period:
                index_ma20 = float(row["ma20"])

            # 长周期均线：数据不足时为 None
            index_ma60 = None
            data_sufficient = (idx + 1) >= self.config.index_ma_period_long
            if data_sufficient:
                index_ma60 = float(row["ma60"])

            # 判断弱势市场：沪深300 < MA60
            is_weak_market = False
            if index_ma60 is not None and current_close < index_ma60:
                is_weak_market = True

            # ===== V2.1 新增：提取 MACD 值 =====
            index_dif = None
            index_dea = None
            index_hist = None
            if self.config.index_filter_method == "macd":
                if idx >= macd_min_idx and pd.notna(row["dif"]):
                    index_dif = float(row["dif"])
                    index_dea = float(row["dea"])
                    index_hist = float(row["hist"])

            # ===== V2.1 新增：提取斜率值 =====
            ma20_slope = None
            ma60_slope = None
            if self.config.slope_filter_enabled or self.config.score_adjustment_enabled:
                if idx >= ma20_slope_min_idx and pd.notna(row["ma20_slope"]):
                    ma20_slope = float(row["ma20_slope"])
                if idx >= ma60_slope_min_idx and pd.notna(row["ma60_slope"]):
                    ma60_slope = float(row["ma60_slope"])

            # ===== V2.1 新增：判定市场强度 =====
            market_strength = "unknown"
            if self.config.score_adjustment_enabled:
                market_strength = determine_market_strength(
                    current_close, index_ma20, index_ma60,
                    ma20_slope, ma60_slope,
                )

            context = MarketContext(
                current_date=date_str,
                index_close=current_close,
                index_ma20=index_ma20,
                index_ma60=index_ma60,
                is_weak_market=is_weak_market,
                data_sufficient=data_sufficient,
                # V2.1 新增字段
                index_dif=index_dif,
                index_dea=index_dea,
                index_hist=index_hist,
                ma20_slope=ma20_slope,
                ma60_slope=ma60_slope,
                market_strength=market_strength,
            )
            self.context_map[date_str] = context

        logger.info(
            f"大盘上下文预构建完成：{len(self.context_map)} 个交易日，"
            f"其中弱势市场 {sum(1 for c in self.context_map.values() if c.is_weak_market)} 天"
        )

    def get_context(self, date_str: str) -> Optional[MarketContext]:
        """
        从预构建的字典中获取上下文

        Args:
            date_str: 日期字符串，格式 YYYY-MM-DD

        Returns:
            Optional[MarketContext]: 大盘上下文，日期不存在时返回 None
        """
        return self.context_map.get(date_str)


# ==================== MonthlySignalCounter 实现 ====================

class DatabaseMonthlyCounter(MonthlySignalCounter):
    """
    基于数据库的单月信号计数器

    用于每日扫描场景：通过查询 scan_results 表统计当月已推送信号数。
    信号产出后由 daily_scan.py 的 save_scan_result 写入数据库，
    下次扫描时自动计入计数。
    """

    def __init__(self, db):
        """
        初始化数据库单月计数器

        Args:
            db: 数据库管理器（DatabaseManager 实例）
        """
        self.db = db

    def get_count(self, year_month: str) -> int:
        """
        查询数据库获取当月信号数

        Args:
            year_month: 月份字符串，格式 YYYY-MM

        Returns:
            int: 当月信号数
        """
        try:
            return self.db.get_signal_count_this_month(year_month)
        except Exception as e:
            logger.warning(f"查询当月信号数失败（{year_month}）：{e}")
            return 0

    def increment(self, year_month: str) -> None:
        """
        数据库实现下，计数由 save_scan_result 自动完成，无需主动 increment

        此方法为空实现（no-op），保留接口一致性。

        Args:
            year_month: 月份字符串，格式 YYYY-MM
        """
        # 数据库实现下，信号保存即计数，无需主动 increment
        pass


class InMemoryMonthlyCounter(MonthlySignalCounter):
    """
    基于内存的单月信号计数器

    用于回测场景：在内存中维护月份到计数的字典，
    信号产出后主动 increment，确保回测时间序列内计数准确。
    """

    def __init__(self):
        """初始化内存单月计数器"""
        self.counts: Dict[str, int] = {}

    def get_count(self, year_month: str) -> int:
        """
        获取指定月份的已产出信号数

        Args:
            year_month: 月份字符串，格式 YYYY-MM

        Returns:
            int: 信号数
        """
        return self.counts.get(year_month, 0)

    def increment(self, year_month: str) -> None:
        """
        增加指定月份的信号计数

        Args:
            year_month: 月份字符串，格式 YYYY-MM
        """
        self.counts[year_month] = self.counts.get(year_month, 0) + 1


# ==================== 风控控制器 ====================

class RiskController:
    """
    风控控制器

    封装三层风控判定逻辑，是回测与每日扫描共用的唯一风控入口。
    通过依赖注入接收 MarketContextProvider 和 MonthlySignalCounter，
    确保判定逻辑一致而数据来源可差异化。

    三层风控执行顺序（任一环节过滤即终止后续）：
        0. 评分调整（V2.1 新增，可选）→ 修改 signal.score
        1. 动态评分阈值过滤（前置，避免低分信号占用单月名额）
        2. 大盘趋势过滤（V2.1 扩展：method 分支 + 斜率叠加）
        3. 单月信号数量上限检查

    V2.1 扩展：
        - 大盘趋势过滤支持 MACD 动量过滤（method='macd'）和均线斜率过滤
        - 评分阈值过滤前新增评分调整步骤（极弱市场评分归零）
        - 统计字段新增 MACD/斜率/评分调整的命中次数
    """

    def __init__(self, config: RiskControlConfig):
        """
        初始化风控控制器

        Args:
            config: 风控配置
        """
        self.config = config

        # 风控命中统计计数器（用于回测报告）
        self.stats = {
            # V2.0 原有
            "skipped_long_ma_filter": 0,    # 被60日均线过滤的信号数
            "skipped_monthly_limit": 0,     # 被单月上限过滤的信号数
            "skipped_score_filter": 0,      # 被评分阈值过滤的信号数
            # V2.1 新增
            "skipped_macd_filter": 0,           # 被 MACD 过滤的信号数
            "skipped_slope_filter": 0,          # 被斜率过滤的信号数
            "skipped_score_adjustment": 0,      # 被评分调整归零的信号数
        }

        # 按月份统计风控命中（用于回测报告的时间分布分析）
        self.monthly_filter_stats: Dict[str, Dict[str, int]] = {}

    def apply_all_controls(
        self,
        signal,
        market_context: MarketContext,
        monthly_count: int,
    ) -> RiskControlResult:
        """
        应用全部四层风控检查（串联执行）（V2.1 扩展）

        V2.1 执行顺序：
            0. 评分调整（V2.1 新增，可选）→ 修改 signal.score
            1. 动态评分阈值过滤（使用调整后的评分）
            2. 大盘趋势过滤（method 分支 + 斜率过滤）
            3. 单月信号数量上限检查

        任一环节过滤即终止后续检查。

        调整说明：
            - 评分调整前置，极弱市场评分归零后直接过滤，不再走后续风控
            - 评分阈值前置，确保低分信号先被过滤，不会占用单月名额

        Args:
            signal: 策略产出的信号对象（需有 signal_date 和 score 属性）
                V2.1 注意：score_adjustment 启用时，signal.score 会被修改
            market_context: 当日大盘环境上下文
            monthly_count: 当月已产出的信号数（调用前由计数器提供）

        Returns:
            RiskControlResult: 风控检查结果
        """
        # ===== V2.1 新增：评分调整（前置） =====
        # 极弱市场评分归零，直接过滤；其他市场按系数调整评分
        if self.config.score_adjustment_enabled:
            original_score = signal.score
            adjusted_score, multiplier, strength = (
                self.adjust_score_by_market_state(
                    original_score, market_context
                )
            )
            # 更新信号评分（后续阈值判断使用调整后的评分）
            signal.score = adjusted_score

            # 极弱市场评分归零，直接计入统计并过滤
            if strength == "extreme_weak" and adjusted_score <= self.config.score_min_limit:
                self._record_filter_stats(
                    signal.signal_date, "score_adjustment"
                )
                return RiskControlResult(
                    passed=False,
                    filter_rule="score_adjustment",
                    filter_reason="大盘极弱（跌破 MA60 且 MA60 向下），评分归零",
                    details={
                        "original_score": original_score,
                        "adjusted_score": adjusted_score,
                        "multiplier": multiplier,
                        "market_strength": strength,
                    },
                )

        # 风控1：动态评分阈值过滤（前置，避免低分信号占用单月名额）
        result = self.check_score_threshold(signal.score, market_context)
        if not result.passed:
            self._record_filter_stats(
                signal.signal_date, "score_filter"
            )
            return result

        # 风控2：大盘趋势过滤（V2.1 扩展：method 分支 + 斜率）
        result = self.check_market_trend(market_context)
        if not result.passed:
            # 根据子规则记录统计
            if result.filter_rule == "macd_filter":
                self._record_filter_stats(
                    signal.signal_date, "macd_filter"
                )
            elif result.filter_rule == "slope_filter":
                self._record_filter_stats(
                    signal.signal_date, "slope_filter"
                )
            else:
                self._record_filter_stats(
                    signal.signal_date, "long_ma_filter"
                )
            return result

        # 风控3：单月信号数量上限检查
        result = self.check_monthly_limit(signal.signal_date, monthly_count)
        if not result.passed:
            self._record_filter_stats(
                signal.signal_date, "monthly_limit"
            )
            return result

        return result

    def check_market_trend(
        self, market_context: MarketContext
    ) -> RiskControlResult:
        """
        风控2：大盘趋势过滤（V2.1 扩展）

        V2.1 分支逻辑：
            - method='ma_position'：V2.0 原有均线位置过滤（MA20 + MA60）
            - method='macd'：V2.1 新增 MACD 动量过滤（DIF > 0）
            - slope_filter_enabled=true：叠加斜率过滤（与 method 组合）

        业务规则：
            - BR-1.6: MACD 过滤与均线位置过滤互斥，method 只能取一个值
            - BR-2.5: 斜率过滤可与任意 method 组合使用

        Args:
            market_context: 大盘环境上下文

        Returns:
            RiskControlResult: 检查结果
        """
        # 大盘过滤总开关关闭时直接放行
        if not self.config.index_filter_enabled:
            return RiskControlResult(passed=True)

        # ===== 第一步：method 分支判断 =====
        if self.config.index_filter_method == "macd":
            # V2.1 新增：MACD 动量过滤
            result = self._check_macd_filter(market_context)
            if not result.passed:
                return result
        else:
            # V2.0 原有：均线位置过滤（MA20 + MA60）
            result = self._check_ma_position_filter(market_context)
            if not result.passed:
                return result

        # ===== 第二步：斜率过滤（可选增强，与 method 组合） =====
        if self.config.slope_filter_enabled:
            result = self._check_slope_filter(market_context)
            if not result.passed:
                return result

        return RiskControlResult(passed=True)

    def _check_macd_filter(
        self, market_context: MarketContext
    ) -> RiskControlResult:
        """
        V2.1 新增：MACD 动量过滤

        业务规则：
            - BR-1.2: DIF > 0 时放行（多头区域）
            - BR-1.4: 过滤条件通过 macd_condition 配置，目前支持 'dif > 0'
            - BR-1.5: 数据不足时跳过并警告
            - BR-1.7: 命中时输出日志并计入统计

        Args:
            market_context: 大盘环境上下文

        Returns:
            RiskControlResult: 检查结果
        """
        # 数据不足时跳过 MACD 过滤
        if market_context.index_dif is None:
            logger.warning(
                f"指数数据不足 {self.config.macd_slow + self.config.macd_signal} 天，"
                f"跳过 MACD 过滤"
            )
            return RiskControlResult(passed=True)

        # 解析过滤条件（目前支持 'dif > 0'）
        if self.config.macd_condition == "dif > 0":
            if market_context.index_dif <= 0:
                reason = (
                    f"大盘 MACD 动量不足（DIF={market_context.index_dif:.4f} <= 0），"
                    f"禁止开仓"
                )
                return RiskControlResult(
                    passed=False,
                    filter_rule="macd_filter",
                    filter_reason=reason,
                    details={
                        "index_dif": market_context.index_dif,
                        "index_dea": market_context.index_dea,
                        "condition": self.config.macd_condition,
                    },
                )
        else:
            # 不支持的 macd_condition 输出警告（配置合法性已由 __post_init__ 兜底校验）
            logger.warning(
                f"不支持的 macd_condition={self.config.macd_condition}，"
                f"跳过 MACD 过滤"
            )

        return RiskControlResult(passed=True)

    def _check_ma_position_filter(
        self, market_context: MarketContext
    ) -> RiskControlResult:
        """
        V2.0 原有：均线位置过滤（MA20 + MA60）

        逻辑与 V2.0 check_market_trend 完全一致，确保向后兼容。

        业务规则：
            - BR-1.2: index_close >= MA20 且 index_close >= MA60 时放行
            - BR-1.3: 任一条件不满足时过滤
            - BR-1.4: 60日均线过滤可独立开关
            - BR-1.5: 数据不足60天时跳过60日均线过滤并警告

        Args:
            market_context: 大盘环境上下文

        Returns:
            RiskControlResult: 检查结果
        """
        # 检查短周期均线（MA20）
        if not market_context.above_ma20:
            ma20_str = (
                f"{market_context.index_ma20:.2f}"
                if market_context.index_ma20 is not None
                else "None"
            )
            reason = (
                f"大盘环境不佳（20日均线过滤："
                f"沪深300 {market_context.index_close:.2f} < MA20 {ma20_str}）"
            )
            return RiskControlResult(
                passed=False,
                filter_rule="long_ma_filter",
                filter_reason=reason,
                details={
                    "index_close": market_context.index_close,
                    "index_ma20": market_context.index_ma20,
                    "filter_sub_rule": "below_ma20",
                },
            )

        # 60日均线过滤开关关闭时放行
        if not self.config.long_ma_filter_enabled:
            return RiskControlResult(passed=True)

        # 数据不足60天时跳过60日均线过滤并警告
        if not market_context.data_sufficient:
            logger.warning(
                "指数数据不足60天，跳过60日均线过滤"
            )
            return RiskControlResult(passed=True)

        # 检查长周期均线（MA60）
        if not market_context.above_ma60:
            ma60_str = (
                f"{market_context.index_ma60:.2f}"
                if market_context.index_ma60 is not None
                else "None"
            )
            reason = (
                f"大盘环境不佳（60日均线过滤："
                f"沪深300 {market_context.index_close:.2f} < MA60 {ma60_str}）"
            )
            return RiskControlResult(
                passed=False,
                filter_rule="long_ma_filter",
                filter_reason=reason,
                details={
                    "index_close": market_context.index_close,
                    "index_ma60": market_context.index_ma60,
                    "filter_sub_rule": "below_ma60",
                },
            )

        return RiskControlResult(passed=True)

    def _check_slope_filter(
        self, market_context: MarketContext
    ) -> RiskControlResult:
        """
        V2.1 新增：均线斜率过滤

        业务规则：
            - BR-2.3: slope > slope_threshold 时放行，否则过滤
            - BR-2.4: 斜率可应用于 MA20 或 MA60（由 slope_ma_period 决定）
            - BR-2.6: 数据不足时跳过并警告
            - BR-2.7: 命中时输出日志并计入统计

        Args:
            market_context: 大盘环境上下文

        Returns:
            RiskControlResult: 检查结果
        """
        # 根据 slope_ma_period 选择对应的斜率值
        if self.config.slope_ma_period == 20:
            slope = market_context.ma20_slope
            ma_name = "MA20"
        elif self.config.slope_ma_period == 60:
            slope = market_context.ma60_slope
            ma_name = "MA60"
        else:
            logger.warning(
                f"不支持的 slope_ma_period={self.config.slope_ma_period}，"
                f"跳过斜率过滤"
            )
            return RiskControlResult(passed=True)

        # 数据不足时跳过
        if slope is None:
            logger.warning(
                f"指数数据不足 {self.config.slope_ma_period + self.config.slope_lookback} 天，"
                f"跳过斜率过滤"
            )
            return RiskControlResult(passed=True)

        # 斜率判断：大于阈值放行，否则过滤
        if slope <= self.config.slope_threshold:
            reason = (
                f"大盘均线斜率向下（{ma_name} slope={slope:.4f} <= "
                f"阈值 {self.config.slope_threshold}），禁止开仓"
            )
            return RiskControlResult(
                passed=False,
                filter_rule="slope_filter",
                filter_reason=reason,
                details={
                    "slope": slope,
                    "slope_ma_period": self.config.slope_ma_period,
                    "slope_threshold": self.config.slope_threshold,
                },
            )

        return RiskControlResult(passed=True)

    def adjust_score_by_market_state(
        self, original_score: float, market_context: MarketContext
    ) -> Tuple[float, float, str]:
        """
        V2.1 新增：根据大盘市场状态调整个股评分

        业务规则：
            - BR-3.2: 调整系数通过配置控制
            - BR-3.6: adjusted_score = original_score × multiplier，上限 100 下限 0
            - BR-3.8: 极弱市场评分归零
            - BR-3.9: 日志记录原始评分、调整系数、调整后评分

        Args:
            original_score: 个股形态评分（0-100）
            market_context: 大盘环境上下文

        Returns:
            Tuple: (adjusted_score, multiplier, market_strength)
                - adjusted_score: 调整后评分（0-100）
                - multiplier: 调整系数（strong/weak/extreme_weak 对应配置值，unknown 为 1.0）
                - market_strength: 市场强度标签
        """
        # 评分调整未启用时，原样返回
        if not self.config.score_adjustment_enabled:
            return original_score, 1.0, "unknown"

        strength = market_context.market_strength

        # 根据市场强度选择系数
        if strength == "extreme_weak":
            multiplier = self.config.score_multiplier_extreme_weak
            logger.info(
                f"大盘极弱（跌破 MA60 且 MA60 向下），评分按极弱市场系数调整："
                f"原始评分={original_score:.2f}，系数={multiplier}"
            )
        elif strength == "strong":
            multiplier = self.config.score_multiplier_strong
        elif strength == "weak":
            multiplier = self.config.score_multiplier_weak
        else:
            # unknown 或数据不足，不调整
            multiplier = 1.0

        # 计算调整后评分（上限和下限由配置决定）
        adjusted_score = round(
            min(
                self.config.score_max_limit,
                max(self.config.score_min_limit, original_score * multiplier),
            ),
            2,
        )

        # 系数不为 1.0 时输出调试日志
        if multiplier != 1.0:
            logger.info(
                f"评分调整：原始={original_score:.2f}，"
                f"系数={multiplier}（{strength}），"
                f"调整后={adjusted_score:.2f}"
            )

        return adjusted_score, multiplier, strength

    def check_monthly_limit(
        self, signal_date: str, monthly_count: int
    ) -> RiskControlResult:
        """
        风控3：单月信号数量上限检查

        业务规则：
            - BR-2.1: 上限由 max_signals_per_month 配置，默认12
            - BR-2.2: 以 signal_date 所在自然月为统计单位
            - BR-2.3: 达到上限后过滤并记录日志

        Args:
            signal_date: 信号日期（回踩确认日），格式 YYYY-MM-DD
            monthly_count: 当月已产出信号数

        Returns:
            RiskControlResult: 检查结果
        """
        # 提取信号日期所在月份
        year_month = signal_date[:7] if signal_date else ""

        # 当月信号数已达上限时过滤
        if monthly_count >= self.config.max_signals_per_month:
            reason = (
                f"本月信号数已达上限 {self.config.max_signals_per_month} 个，"
                f"停止产生新信号"
            )
            return RiskControlResult(
                passed=False,
                filter_rule="monthly_limit",
                filter_reason=reason,
                details={
                    "year_month": year_month,
                    "current_count": monthly_count,
                    "max_limit": self.config.max_signals_per_month,
                },
            )

        return RiskControlResult(passed=True)

    def check_score_threshold(
        self, score: float, market_context: MarketContext
    ) -> RiskControlResult:
        """
        风控1：动态评分阈值过滤

        业务规则：
            - BR-3.1: 默认阈值 score_threshold，0 表示不过滤
            - BR-3.2: 弱势市场阈值 score_threshold_weak
            - BR-3.3: 弱势市场判定：沪深300 < MA60
            - BR-3.4: 评分 < 当前生效阈值时过滤

        Args:
            score: 信号评分
            market_context: 大盘环境上下文（用于判断是否弱势市场）

        Returns:
            RiskControlResult: 检查结果
        """
        # 评分过滤总开关关闭时直接放行
        if not self.config.score_filter_enabled:
            return RiskControlResult(passed=True)

        # 获取当前生效的评分阈值
        effective_threshold = self.get_effective_threshold(market_context)

        # 阈值为0表示不过滤
        if effective_threshold <= 0:
            return RiskControlResult(
                passed=True,
                effective_threshold=effective_threshold,
            )

        # 评分低于阈值时过滤
        if score < effective_threshold:
            reason = (
                f"信号评分 {score:.2f} 低于阈值 {effective_threshold}，过滤"
            )
            return RiskControlResult(
                passed=False,
                filter_rule="score_threshold",
                filter_reason=reason,
                details={
                    "effective_threshold": effective_threshold,
                    "is_weak_market": market_context.is_weak_market,
                    "score": score,
                },
                effective_threshold=effective_threshold,
            )

        return RiskControlResult(
            passed=True,
            effective_threshold=effective_threshold,
        )

    def is_weak_market(self, market_context: MarketContext) -> bool:
        """
        判断是否弱势市场

        根据 weak_market_condition 配置判定：
            - 'below_ma60': 沪深300收盘价 < MA60

        Args:
            market_context: 大盘环境上下文

        Returns:
            bool: True 表示弱势市场
        """
        # 直接使用上下文中已计算的弱势市场标记
        # 该标记在 Provider 构建时根据 weak_market_condition 计算
        return market_context.is_weak_market

    def get_effective_threshold(
        self, market_context: MarketContext
    ) -> float:
        """
        获取当前生效的评分阈值

        弱势市场返回 score_threshold_weak，否则返回 score_threshold

        Args:
            market_context: 大盘环境上下文

        Returns:
            float: 当前生效的评分阈值
        """
        if self.is_weak_market(market_context):
            return self.config.score_threshold_weak
        return self.config.score_threshold

    def get_stats(self) -> Dict:
        """
        获取风控命中统计（用于回测报告）（V2.1 扩展）

        Returns:
            Dict: 风控命中统计字典，包含汇总和按月份分布
                - summary: 各过滤规则的命中次数汇总
                - monthly: 按月份分布的命中次数
        """
        # 汇总统计
        summary = {
            # V2.0 原有
            "skipped_long_ma_filter": self.stats["skipped_long_ma_filter"],
            "skipped_monthly_limit": self.stats["skipped_monthly_limit"],
            "skipped_score_filter": self.stats["skipped_score_filter"],
            # V2.1 新增
            "skipped_macd_filter": self.stats.get("skipped_macd_filter", 0),
            "skipped_slope_filter": self.stats.get("skipped_slope_filter", 0),
            "skipped_score_adjustment": self.stats.get(
                "skipped_score_adjustment", 0
            ),
            "total_filtered": sum(self.stats.values()),
        }

        # 按月份统计
        monthly = []
        for year_month in sorted(self.monthly_filter_stats.keys()):
            month_stats = self.monthly_filter_stats[year_month]
            monthly.append({
                "year_month": year_month,
                # V2.0 原有
                "long_ma_filter": month_stats.get("long_ma_filter", 0),
                "monthly_limit": month_stats.get("monthly_limit", 0),
                "score_filter": month_stats.get("score_filter", 0),
                # V2.1 新增
                "macd_filter": month_stats.get("macd_filter", 0),
                "slope_filter": month_stats.get("slope_filter", 0),
                "score_adjustment": month_stats.get("score_adjustment", 0),
                "total_filtered": sum(month_stats.values()),
            })

        return {
            "summary": summary,
            "monthly": monthly,
        }

    def reset_stats(self) -> None:
        """重置统计计数器（回测开始前调用）（V2.1 扩展）"""
        self.stats = {
            # V2.0 原有
            "skipped_long_ma_filter": 0,
            "skipped_monthly_limit": 0,
            "skipped_score_filter": 0,
            # V2.1 新增
            "skipped_macd_filter": 0,
            "skipped_slope_filter": 0,
            "skipped_score_adjustment": 0,
        }
        self.monthly_filter_stats = {}

    def _record_filter_stats(
        self, signal_date: str, filter_type: str
    ) -> None:
        """
        记录风控命中统计（V2.1 扩展）

        Args:
            signal_date: 信号日期，格式 YYYY-MM-DD
            filter_type: 过滤类型，支持：
                - V2.0: long_ma_filter / monthly_limit / score_filter
                - V2.1: macd_filter / slope_filter / score_adjustment
        """
        # 汇总计数器累加
        stats_key = f"skipped_{filter_type}"
        if stats_key in self.stats:
            self.stats[stats_key] += 1

        # 按月份统计
        year_month = signal_date[:7] if signal_date else "unknown"
        if year_month not in self.monthly_filter_stats:
            self.monthly_filter_stats[year_month] = {
                # V2.0 原有
                "long_ma_filter": 0,
                "monthly_limit": 0,
                "score_filter": 0,
                # V2.1 新增
                "macd_filter": 0,
                "slope_filter": 0,
                "score_adjustment": 0,
            }
        self.monthly_filter_stats[year_month][filter_type] += 1
