"""
市场状态分析器
根据 ADX、EMA 等指标判断市场状态（震荡/上升趋势/下降趋势/强趋势暂停）
支持多时间框架确认、趋势强度计算、状态历史追踪

数据接口：
- 支持 List[Dict] 格式（K 线数据字典列表）
- 支持 pandas DataFrame 格式（兼容 adaptive_grid_trading）
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional, Tuple, Union

import pandas as pd

logger = logging.getLogger(__name__)


class MarketState(Enum):
    """市场状态枚举"""
    RANGING = "ranging"          # 震荡
    UPTREND = "uptrend"          # 上升趋势
    DOWNTREND = "downtrend"      # 下降趋势
    STRONG_TREND = "strong_trend"  # 强趋势暂停


@dataclass
class MarketStateResult:
    """市场状态判断结果"""
    state: MarketState
    confidence: float  # 置信度 (0-1)
    adx: float
    adx_4h: Optional[float]
    ema_fast: float
    ema_slow: float
    ema_fast_4h: Optional[float]
    ema_slow_4h: Optional[float]
    trend_strength: float  # 趋势强度系数 (0-0.5)
    timestamp: datetime

    def to_dict(self) -> Dict:
        """转换为字典"""
        return {
            'state': self.state.value,
            'confidence': self.confidence,
            'adx': self.adx,
            'adx_4h': self.adx_4h,
            'ema_fast': self.ema_fast,
            'ema_slow': self.ema_slow,
            'ema_fast_4h': self.ema_fast_4h,
            'ema_slow_4h': self.ema_slow_4h,
            'trend_strength': self.trend_strength,
            'timestamp': self.timestamp
        }


class MarketAnalyzer:
    """
    市场状态分析器

    功能：
    - 根据 ADX 和 EMA 指标判断市场状态（震荡/上升/下降/强趋势暂停）
    - 支持多时间框架确认（1H + 4H）
    - 计算趋势强度系数
    - 追踪状态变化历史
    - 支持 pandas DataFrame 和 List[Dict] 两种输入格式
    """

    def __init__(
        self,
        adx_period: int = 14,
        adx_weak_threshold: float = 20,
        adx_trend_threshold: float = 25,
        adx_strong_threshold: float = 40,
        ema_fast_period: int = 20,
        ema_slow_period: int = 50,
        require_multi_timeframe_confirm: bool = True
    ):
        """
        初始化市场状态分析器

        Args:
            adx_period: ADX 周期
            adx_weak_threshold: ADX 弱趋势阈值（默认 20）
            adx_trend_threshold: ADX 趋势确认阈值（默认 25）
            adx_strong_threshold: ADX 强趋势暂停阈值（默认 40）
            ema_fast_period: 快线 EMA 周期
            ema_slow_period: 慢线 EMA 周期
            require_multi_timeframe_confirm: 是否需要多时间框架确认
        """
        self.adx_period = adx_period
        self.adx_weak_threshold = adx_weak_threshold
        self.adx_trend_threshold = adx_trend_threshold
        self.adx_strong_threshold = adx_strong_threshold
        self.ema_fast_period = ema_fast_period
        self.ema_slow_period = ema_slow_period
        self.require_multi_timeframe_confirm = require_multi_timeframe_confirm

        self._prev_state: Optional[MarketState] = None
        self._state_history: List[Tuple[datetime, MarketState]] = []

    def analyze(
        self,
        data_1h: Union[List[Dict], pd.DataFrame],
        data_4h: Optional[Union[List[Dict], pd.DataFrame]] = None
    ) -> MarketStateResult:
        """
        分析市场状态（统一入口，支持多种数据格式）

        Args:
            data_1h: 1H K 线数据（List[Dict] 或 pandas DataFrame）
            data_4h: 4H K 线数据（可选，用于多时间框架确认）

        Returns:
            市场状态判断结果

        Raises:
            ValueError: 当输入数据为空时
        """
        # 统一转换为内部使用的字典列表格式
        klines_1h = self._normalize_input(data_1h)
        klines_4h = self._normalize_input(data_4h) if data_4h is not None else None

        if not klines_1h:
            raise ValueError("K 线数据不能为空")

        # 获取 1H 指标（最后一条 K 线）
        last_kline_1h = klines_1h[-1]
        adx_1h = last_kline_1h.get('adx', 0)
        ema_fast_1h = last_kline_1h.get('ema_fast', 0)
        ema_slow_1h = last_kline_1h.get('ema_slow', 0)

        # 获取 4H 指标（如果提供）
        adx_4h = None
        ema_fast_4h = None
        ema_slow_4h = None

        if klines_4h and len(klines_4h) > 0:
            last_kline_4h = klines_4h[-1]
            adx_4h = last_kline_4h.get('adx')
            ema_fast_4h = last_kline_4h.get('ema_fast')
            ema_slow_4h = last_kline_4h.get('ema_slow')

        return self._analyze_from_indicators(
            adx_1h=adx_1h, ema_fast_1h=ema_fast_1h, ema_slow_1h=ema_slow_1h,
            adx_4h=adx_4h, ema_fast_4h=ema_fast_4h, ema_slow_4h=ema_slow_4h
        )

    def _normalize_input(self, data: Union[List[Dict], pd.DataFrame]) -> List[Dict]:
        """
        标准化输入数据为 List[Dict] 格式

        Args:
            data: 输入数据（List[Dict] 或 pandas DataFrame）

        Returns:
            字典列表格式
        """
        if isinstance(data, pd.DataFrame):
            # DataFrame 转 List[Dict]
            if data.empty:
                return []
            return data.to_dict('records')
        return data

    def _analyze_from_indicators(
        self,
        adx_1h: float,
        ema_fast_1h: float,
        ema_slow_1h: float,
        adx_4h: Optional[float] = None,
        ema_fast_4h: Optional[float] = None,
        ema_slow_4h: Optional[float] = None
    ) -> MarketStateResult:
        """
        从指标值分析市场状态（内部核心逻辑）

        Args:
            adx_1h: 1H ADX 值
            ema_fast_1h: 1H 快线 EMA
            ema_slow_1h: 1H 慢线 EMA
            adx_4h: 4H ADX 值（可选）
            ema_fast_4h: 4H 快线 EMA（可选）
            ema_slow_4h: 4H 慢线 EMA（可选）

        Returns:
            市场状态判断结果
        """
        # 判断 1H 状态
        state_1h = self._judge_state(adx_1h, ema_fast_1h, ema_slow_1h)

        # 判断 4H 状态（如果提供）
        state_4h = None
        if adx_4h is not None:
            state_4h = self._judge_state(adx_4h, ema_fast_4h, ema_slow_4h)

        # 多时间框架确认（传入 ADX 值用于判断趋势强度）
        final_state = self._confirm_state(state_1h, state_4h, adx_1h, adx_4h)

        # 计算置信度
        confidence = self._calculate_confidence(adx_1h, adx_4h, state_1h, state_4h)

        # 计算趋势强度系数
        trend_strength = self._calculate_trend_strength(adx_1h)

        # 创建结果
        timestamp = datetime.now()
        result = MarketStateResult(
            state=final_state,
            confidence=confidence,
            adx=adx_1h,
            adx_4h=adx_4h,
            ema_fast=ema_fast_1h,
            ema_slow=ema_slow_1h,
            ema_fast_4h=ema_fast_4h,
            ema_slow_4h=ema_slow_4h,
            trend_strength=trend_strength,
            timestamp=timestamp
        )

        # 更新历史
        self._prev_state = final_state
        self._state_history.append((timestamp, final_state))

        # 保持历史记录在合理范围内
        if len(self._state_history) > 100:
            self._state_history.pop(0)

        logger.info(
            f"市场状态判断：{final_state.value} "
            f"(置信度：{confidence:.2f}, ADX: {adx_1h:.2f})"
        )

        return result

    def _judge_state(
        self,
        adx: float,
        ema_fast: float,
        ema_slow: float
    ) -> MarketState:
        """
        判断市场状态（单时间框架）

        Args:
            adx: ADX 值
            ema_fast: 快线 EMA
            ema_slow: 慢线 EMA

        Returns:
            市场状态
        """
        # 强趋势暂停：ADX ≥ 40
        if adx >= self.adx_strong_threshold:
            return MarketState.STRONG_TREND

        # 震荡：ADX < 20
        if adx < self.adx_weak_threshold:
            return MarketState.RANGING

        # 趋势：ADX ≥ 25
        if adx >= self.adx_trend_threshold:
            if ema_fast > ema_slow:
                return MarketState.UPTREND
            else:
                return MarketState.DOWNTREND

        # 弱趋势区：20 ≤ ADX < 25，保持前一状态
        if self._prev_state is None:
            return MarketState.RANGING
        return self._prev_state

    def _confirm_state(
        self,
        state_1h: MarketState,
        state_4h: Optional[MarketState],
        adx_1h: float = 0,
        adx_4h: Optional[float] = None
    ) -> MarketState:
        """
        多时间框架确认（优化版）

        优化逻辑：
        1. 强趋势暂停优先级最高
        2. 1H 强趋势（ADX ≥ 30）+ 4H 震荡 → 保持 1H 趋势（降低置信度）
        3. 1H 弱趋势（25 ≤ ADX < 30）+ 4H 震荡 → 降级为震荡
        4. 4H 强趋势（ADX ≥ 30）作为过滤器

        Args:
            state_1h: 1H 状态
            state_4h: 4H 状态（可选）
            adx_1h: 1H ADX 值
            adx_4h: 4H ADX 值（可选）

        Returns:
            最终确认的状态
        """
        # 强趋势暂停优先级最高
        if state_1h == MarketState.STRONG_TREND:
            return state_1h

        if not self.require_multi_timeframe_confirm:
            return state_1h

        if state_4h is None:
            # 没有 4H 数据，使用 1H 状态
            return state_1h

        # 如果 4H 是强趋势，则暂停
        if state_4h == MarketState.STRONG_TREND:
            logger.warning(f"4H 强趋势暂停：4H={state_4h.value}")
            return MarketState.STRONG_TREND

        # 状态一致，确认
        if state_1h == state_4h:
            logger.debug(f"多时间框架确认：1H={state_1h.value}, 4H={state_4h.value}")
            return state_1h

        # 状态不一致，灵活处理
        # 1H 趋势 + 4H 震荡
        if state_1h in [MarketState.UPTREND, MarketState.DOWNTREND] and \
           state_4h == MarketState.RANGING:

            # 如果 1H 是强趋势（ADX ≥ 30），保持趋势状态
            if adx_1h >= 30:
                logger.info(
                    f"1H 强趋势（ADX={adx_1h:.2f}），即使 4H 震荡也保持趋势："
                    f"1H={state_1h.value}, 4H={state_4h.value}"
                )
                return state_1h

            # 1H 弱趋势，降级为震荡
            logger.warning(
                f"1H 弱趋势（ADX={adx_1h:.2f}），4H 震荡，降级为震荡："
                f"1H={state_1h.value}, 4H={state_4h.value}"
            )
            return MarketState.RANGING

        # 其他情况使用 1H 状态
        logger.warning(
            f"多时间框架状态不一致，使用 1H 状态："
            f"1H={state_1h.value}, 4H={state_4h.value}"
        )
        return state_1h

    def _calculate_confidence(
        self,
        adx_1h: float,
        adx_4h: Optional[float],
        state_1h: MarketState,
        state_4h: Optional[MarketState]
    ) -> float:
        """
        计算置信度

        Args:
            adx_1h: 1H ADX
            adx_4h: 4H ADX
            state_1h: 1H 状态
            state_4h: 4H 状态

        Returns:
            置信度 (0-1)
        """
        confidence = 0.5  # 基础置信度

        # 根据 ADX 调整
        if adx_1h >= self.adx_strong_threshold:
            confidence += 0.3  # 强趋势
        elif adx_1h >= self.adx_trend_threshold:
            confidence += 0.2  # 趋势
        elif adx_1h >= self.adx_weak_threshold:
            confidence += 0.1  # 中等趋势
        else:
            confidence -= 0.1  # 震荡

        # 多时间框架确认加分
        if state_4h is not None and state_1h == state_4h:
            confidence += 0.2
        elif state_4h is not None and state_1h != state_4h:
            confidence -= 0.1

        # 限制在 0-1 范围内
        return max(0.0, min(1.0, confidence))

    def _calculate_trend_strength(self, adx: float) -> float:
        """
        计算趋势强度系数

        根据需求文档：k_trend = min(0.5, max(0, (ADX - 25) / 30))

        Args:
            adx: ADX 值

        Returns:
            趋势强度系数 (0-0.5)
        """
        k_trend = (adx - 25) / 30
        return max(0.0, min(0.5, k_trend))

    def get_state_changes(self, limit: int = 10) -> List[Dict]:
        """
        获取状态变化历史

        Args:
            limit: 返回数量限制

        Returns:
            状态变化列表
        """
        if len(self._state_history) < 2:
            return []

        changes = []
        prev_state = None

        for timestamp, state in self._state_history:
            if prev_state is not None and state != prev_state:
                changes.append({
                    'timestamp': timestamp,
                    'from_state': prev_state.value,
                    'to_state': state.value
                })
            prev_state = state

        return changes[-limit:]

    def get_current_state(self) -> Optional[MarketState]:
        """获取当前状态"""
        return self._prev_state

    def is_trending(self) -> bool:
        """判断是否处于趋势状态"""
        if self._prev_state is None:
            return False
        return self._prev_state in [MarketState.UPTREND, MarketState.DOWNTREND]

    def is_ranging(self) -> bool:
        """判断是否处于震荡状态"""
        if self._prev_state is None:
            return False
        return self._prev_state == MarketState.RANGING

    def is_strong_trend(self) -> bool:
        """判断是否处于强趋势暂停状态"""
        if self._prev_state is None:
            return False
        return self._prev_state == MarketState.STRONG_TREND

    def clear_history(self) -> None:
        """清除状态历史"""
        self._prev_state = None
        self._state_history.clear()
        logger.info("市场状态历史已清除")
