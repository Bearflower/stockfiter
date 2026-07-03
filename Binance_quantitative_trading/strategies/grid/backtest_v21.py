"""
V2.1 网格交易信号灯回测脚本

使用本地 ETHUSDT K 线数据，对 V2.1 网格交易信号灯系统进行回测验证。
逐小时模拟巡检，统计市场状态分布、状态转换、信号推送、网格参数分布等。

回测逻辑：
1. 加载 1h 和 4h K 线数据
2. 对每个 1h 时间点：
   a. 取最近 100 根 1h K 线计算 ADX, EMA20, EMA50, ATR_smooth
   b. 取最近 100 根 4h K 线计算 ADX, EMA20, EMA50
   c. 调用 _determine_state 判断市场状态
   d. 如果是震荡/弱趋势，计算网格参数
   e. 模拟推送去重逻辑
3. 统计回测结果并输出报告
"""
import sys
import os
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import yaml

# 添加项目根目录到 sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

from shared.indicators import TechnicalIndicators
from strategies.grid.market_state import MarketState, MarketStateDetector
from strategies.grid.grid_calculator import GridCalculator, DynamicGridParams, GridMode


# ============================================================
# 数据加载
# ============================================================

def load_kline_data(filepath: str) -> pd.DataFrame:
    """
    加载 K 线 CSV 数据

    Args:
        filepath: CSV 文件路径

    Returns:
        包含 timestamp, open, high, low, close, volume 列的 DataFrame
    """
    df = pd.read_csv(filepath, parse_dates=['timestamp'])
    # 确保数值列类型正确
    for col in ['open', 'high', 'low', 'close', 'volume']:
        df[col] = pd.to_numeric(df[col], errors='coerce')
    df = df.dropna(subset=['open', 'high', 'low', 'close']).reset_index(drop=True)
    return df


# ============================================================
# 指标计算辅助
# ============================================================

def calculate_indicators_1h(df_1h: pd.DataFrame, atr_period: int = 14,
                            ema_fast: int = 20, ema_slow: int = 50) -> Dict[str, Decimal]:
    """
    计算 1h 时间框架的技术指标

    Args:
        df_1h: 1h K 线数据（至少 100 根）
        atr_period: ATR 周期
        ema_fast: 快速 EMA 周期
        ema_slow: 慢速 EMA 周期

    Returns:
        指标字典，包含 adx, ema_fast, ema_slow, atr, atr_smooth, current_price
    """
    adx_series = TechnicalIndicators.calculate_adx(df_1h, period=atr_period)
    ema_fast_series = TechnicalIndicators.calculate_ema(df_1h, period=ema_fast)
    ema_slow_series = TechnicalIndicators.calculate_ema(df_1h, period=ema_slow)
    atr_series = TechnicalIndicators.calculate_atr(df_1h, period=atr_period)
    # 平滑 ATR（EMA 平滑）
    atr_smooth_series = atr_series.ewm(span=atr_period, adjust=False).mean()

    def _safe_decimal(series) -> Decimal:
        val = series.iloc[-1]
        return Decimal(str(val)) if not pd.isna(val) else Decimal('0')

    return {
        'adx': _safe_decimal(adx_series),
        'ema_fast': _safe_decimal(ema_fast_series),
        'ema_slow': _safe_decimal(ema_slow_series),
        'atr': _safe_decimal(atr_series),
        'atr_smooth': _safe_decimal(atr_smooth_series),
        'current_price': Decimal(str(df_1h['close'].iloc[-1])),
    }


def calculate_indicators_4h(df_4h: pd.DataFrame, atr_period: int = 14,
                            ema_fast: int = 20, ema_slow: int = 50) -> Dict[str, Decimal]:
    """
    计算 4h 时间框架的技术指标

    Args:
        df_4h: 4h K 线数据（至少 50 根）
        atr_period: ATR 周期
        ema_fast: 快速 EMA 周期
        ema_slow: 慢速 EMA 周期

    Returns:
        指标字典，包含 adx, ema_fast, ema_slow
    """
    adx_series = TechnicalIndicators.calculate_adx(df_4h, period=atr_period)
    # 4h 使用 tail 截取方式计算 EMA（与生产代码一致）
    ema_fast_series = df_4h['close'].tail(50).ewm(span=ema_fast, adjust=False).mean()
    ema_slow_series = df_4h['close'].tail(100).ewm(span=ema_slow, adjust=False).mean()

    def _safe_decimal(series) -> Decimal:
        val = series.iloc[-1]
        return Decimal(str(val)) if not pd.isna(val) else Decimal('0')

    return {
        'adx': _safe_decimal(adx_series),
        'ema_fast': _safe_decimal(ema_fast_series),
        'ema_slow': _safe_decimal(ema_slow_series),
    }


# ============================================================
# Mock KLineService（仅用于创建 MarketStateDetector 实例）
# ============================================================

class MockKLineService:
    """模拟 K 线服务，仅用于满足 MarketStateDetector 构造函数要求"""

    async def get_multi_timeframe_data(self, symbol: str, intervals: list) -> dict:
        return {}

    async def get_klines(self, symbol: str, interval: str, limit: int = 100) -> list:
        return []


# ============================================================
# 回测引擎
# ============================================================

@dataclass
class BacktestEvent:
    """回测事件记录"""
    timestamp: datetime
    state: MarketState
    adx_1h: Decimal
    adx_4h: Decimal
    ema20_1h: Decimal
    ema50_1h: Decimal
    ema20_4h: Decimal
    ema50_4h: Decimal
    atr_smooth: Decimal
    current_price: Decimal
    trend_strength: Decimal
    confidence: Decimal
    grid_params: Optional[DynamicGridParams] = None
    should_push: bool = False
    atr_abnormal_count: int = 0
    is_vol_alarm_active: bool = False


class V21BacktestEngine:
    """
    V2.1 信号灯回测引擎

    逐小时模拟巡检，记录市场状态变化、信号推送、网格参数等。
    """

    def __init__(self, config: dict):
        """
        初始化回测引擎

        Args:
            config: 配置字典（从 config.yaml 加载）
        """
        self.config = config
        market_cfg = config.get('market', {})

        # 创建 MarketStateDetector 实例（使用 Mock KLineService）
        mock_kline_service = MockKLineService()
        self.detector = MarketStateDetector(
            kline_service=mock_kline_service,
            adx_extreme_strong=market_cfg.get('adx_extreme_strong', 35),
            adx_normal_strong=market_cfg.get('adx_normal_strong', 30),
            adx_normal_strong_4h=market_cfg.get('adx_normal_strong_4h', 25),
            weak_trend_adx_lower=market_cfg.get('weak_trend_adx_lower', 25),
            weak_trend_adx_upper=market_cfg.get('weak_trend_adx_upper', 30),
            volatility_ratio_threshold=Decimal(str(market_cfg.get('volatility_ratio_threshold', 1.5))),
            volatility_consecutive_count=market_cfg.get('volatility_consecutive_count', 2),
            volatility_recovery_ratio=Decimal(str(market_cfg.get('volatility_recovery_ratio', 1.3))),
            recovery_adx_strong_1h=market_cfg.get('recovery_adx_strong_1h', 30),
            recovery_adx_strong_4h=market_cfg.get('recovery_adx_strong_4h', 30),
            recovery_adx_weak_1h=market_cfg.get('recovery_adx_weak_1h', 25),
            recovery_adx_weak_4h=market_cfg.get('recovery_adx_weak_4h', 25),
            trend_strength_divisor=market_cfg.get('trend_strength_divisor', 30),
            atr_history_size=market_cfg.get('atr_history_size', 3),
            ema_fast_period=market_cfg.get('ema_fast', 20),
            ema_slow_period=market_cfg.get('ema_slow', 50),
            atr_period=market_cfg.get('atr_period', 14),
            emergency_adx_threshold=market_cfg.get('emergency_adx_threshold', 50),
            trend_acceleration_threshold=market_cfg.get('trend_acceleration_threshold', 20),
            adx_history_size=market_cfg.get('adx_history_size', 3),
        )

        # 创建 GridCalculator 实例
        self.grid_calculator = GridCalculator(config)

        # 推送冷却时间（小时）
        self.push_cooldown_hours = config.get('signal_bot', {}).get('push_cooldown_hours', 4)

        # 回测结果
        self.events: List[BacktestEvent] = []
        self.state_changes: List[Dict] = []
        self.volatility_events: List[Dict] = []

        # 推送去重状态
        self.last_push_time: Optional[datetime] = None
        self.last_push_state: Optional[MarketState] = None

        # ATR 基准值（将在回测开始时计算）
        self.atr_baseline: Decimal = Decimal('0')

    def _calculate_baseline_atr(self, df_1h: pd.DataFrame) -> Decimal:
        """
        计算基准 ATR（使用全部 1h 数据的 ATR 均值）

        Args:
            df_1h: 完整的 1h K 线数据

        Returns:
            基准 ATR 值
        """
        atr_period = self.config.get('market', {}).get('atr_period', 14)
        atr_baseline_period = self.config.get('market', {}).get('atr_baseline_period', 30)

        # 使用配置的基准周期（天数），换算为小时数
        atr_baseline_hours = atr_baseline_period * 24

        if len(df_1h) < atr_baseline_hours:
            atr_baseline_hours = len(df_1h)

        atr_series = TechnicalIndicators.calculate_atr(df_1h, period=atr_period)
        baseline_atr = Decimal(str(atr_series.tail(atr_baseline_hours).mean()))

        return baseline_atr

    def _should_push(self, current_time: datetime, current_state: MarketState) -> bool:
        """
        判断是否需要推送（模拟推送去重逻辑）

        规则：
        - 首次运行：一定推送
        - 市场状态变化：立即推送
        - 同状态：检查冷却时间

        Args:
            current_time: 当前时间
            current_state: 当前市场状态

        Returns:
            是否需要推送
        """
        # 首次运行
        if self.last_push_time is None:
            return True

        # 状态变化
        if self.last_push_state != current_state:
            return True

        # 同状态：检查冷却时间
        hours_since = (current_time - self.last_push_time).total_seconds() / 3600
        if hours_since >= self.push_cooldown_hours:
            return True

        return False

    def _record_push(self, current_time: datetime, current_state: MarketState) -> None:
        """记录推送时间"""
        self.last_push_time = current_time
        self.last_push_state = current_state

    def run(self, df_1h: pd.DataFrame, df_4h: pd.DataFrame) -> List[BacktestEvent]:
        """
        执行回测

        Args:
            df_1h: 1h K 线数据
            df_4h: 4h K 线数据

        Returns:
            回测事件列表
        """
        # 计算基准 ATR
        self.atr_baseline = self._calculate_baseline_atr(df_1h)
        print(f"基准 ATR: {float(self.atr_baseline):.2f}")

        # 确保时间列已排序
        df_1h = df_1h.sort_values('timestamp').reset_index(drop=True)
        df_4h = df_4h.sort_values('timestamp').reset_index(drop=True)

        # 构建 4h 时间索引，用于快速查找
        timestamps_4h = df_4h['timestamp'].values

        # 最少需要 100 根 1h K 线才能计算指标
        min_1h_index = 100
        if len(df_1h) < min_1h_index:
            print(f"1h K 线数据不足，至少需要 {min_1h_index} 根，实际 {len(df_1h)} 根")
            return self.events

        total_hours = len(df_1h) - min_1h_index
        print(f"回测总小时数: {total_hours}")

        prev_state = None

        for i in range(min_1h_index, len(df_1h)):
            current_time = df_1h['timestamp'].iloc[i]

            # 取最近 100 根 1h K 线
            window_1h = df_1h.iloc[i - 100:i + 1].copy()

            # 取截至当前时间的 4h K 线（最近 100 根）
            mask_4h = df_4h['timestamp'] <= current_time
            df_4h_available = df_4h[mask_4h].tail(100).copy()

            if len(df_4h_available) < 50:
                # 4h 数据不足，跳过
                continue

            # 计算 1h 指标
            ind_1h = calculate_indicators_1h(
                window_1h,
                atr_period=self.detector.atr_period,
                ema_fast=self.detector.ema_fast_period,
                ema_slow=self.detector.ema_slow_period,
            )

            # 计算 4h 指标
            ind_4h = calculate_indicators_4h(
                df_4h_available,
                atr_period=self.detector.atr_period,
                ema_fast=self.detector.ema_fast_period,
                ema_slow=self.detector.ema_slow_period,
            )

            # 更新 ATR 历史（用于波动率异常检测）
            atr_2h_ago, atr_abnormal_count, atr_peak, is_vol_alarm_active = \
                self.detector._update_atr_history(ind_1h['atr_smooth'])

            # 更新 ADX 历史（用于趋势急剧增强检测）
            self.detector._update_adx_history(ind_1h['adx'])

            # 判断市场状态
            state, confidence = self.detector._determine_state(
                adx_1h=ind_1h['adx'],
                adx_4h=ind_4h['adx'],
                ema20_1h=ind_1h['ema_fast'],
                ema50_1h=ind_1h['ema_slow'],
                ema20_4h=ind_4h['ema_fast'],
                ema50_4h=ind_4h['ema_slow'],
                atr_smooth_1h=ind_1h['atr_smooth'],
            )

            # 计算趋势强度系数
            trend_strength = self.detector._calculate_trend_strength(ind_1h['adx'])

            # 计算网格参数（仅震荡/弱趋势）
            grid_params = None
            if state in [MarketState.OSCILLATION, MarketState.WEAK_TREND]:
                try:
                    grid_params = self.grid_calculator.calculate_dynamic_grid_params(
                        current_price=ind_1h['current_price'],
                        atr_smooth=ind_1h['atr_smooth'],
                        atr_baseline=self.atr_baseline,
                        market_state=state.value,
                        trend_strength=trend_strength,
                    )
                except Exception as e:
                    # 网格参数计算失败（如 ATR 为 0），跳过
                    pass

            # 判断是否推送
            should_push = self._should_push(current_time, state)
            if should_push:
                self._record_push(current_time, state)

            # 记录状态变化
            if prev_state is not None and prev_state != state:
                self.state_changes.append({
                    'timestamp': current_time,
                    'old_state': prev_state,
                    'new_state': state,
                    'adx_1h': ind_1h['adx'],
                    'adx_4h': ind_4h['adx'],
                    'atr_smooth': ind_1h['atr_smooth'],
                    'current_price': ind_1h['current_price'],
                })

            # 记录波动率异常事件
            if state == MarketState.VOLATILITY_ABNORMAL:
                self.volatility_events.append({
                    'timestamp': current_time,
                    'atr_smooth': ind_1h['atr_smooth'],
                    'atr_abnormal_count': atr_abnormal_count,
                    'atr_peak': atr_peak,
                    'current_price': ind_1h['current_price'],
                })

            # 创建事件记录
            event = BacktestEvent(
                timestamp=current_time,
                state=state,
                adx_1h=ind_1h['adx'],
                adx_4h=ind_4h['adx'],
                ema20_1h=ind_1h['ema_fast'],
                ema50_1h=ind_1h['ema_slow'],
                ema20_4h=ind_4h['ema_fast'],
                ema50_4h=ind_4h['ema_slow'],
                atr_smooth=ind_1h['atr_smooth'],
                current_price=ind_1h['current_price'],
                trend_strength=trend_strength,
                confidence=confidence,
                grid_params=grid_params,
                should_push=should_push,
                atr_abnormal_count=atr_abnormal_count,
                is_vol_alarm_active=is_vol_alarm_active,
            )
            self.events.append(event)

            prev_state = state

            # 进度输出
            if (i - min_1h_index) % 500 == 0:
                progress = (i - min_1h_index) / total_hours * 100
                print(f"  回测进度: {progress:.1f}% ({i - min_1h_index}/{total_hours})")

        print(f"  回测进度: 100.0% ({total_hours}/{total_hours})")
        return self.events


# ============================================================
# 报告生成
# ============================================================

def generate_report(events: List[BacktestEvent], state_changes: List[Dict],
                    volatility_events: List[Dict], config: dict,
                    start_time: str, end_time: str) -> str:
    """
    生成回测报告

    Args:
        events: 回测事件列表
        state_changes: 状态变化记录
        volatility_events: 波动率异常事件
        config: 配置字典
        start_time: 回测起始时间
        end_time: 回测结束时间

    Returns:
        格式化的回测报告文本
    """
    if not events:
        return "无回测数据"

    total_hours = len(events)
    push_cooldown_hours = config.get('signal_bot', {}).get('push_cooldown_hours', 4)

    # ---- 一、市场状态分布 ----
    state_counter = Counter(e.state for e in events)
    state_order = [
        MarketState.EMERGENCY_EXTREME_TREND,
        MarketState.TREND_ACCELERATING,
        MarketState.EXTREME_STRONG_TREND,
        MarketState.NORMAL_STRONG_TREND,
        MarketState.VOLATILITY_ABNORMAL,
        MarketState.WEAK_TREND,
        MarketState.OSCILLATION,
    ]

    state_dist_lines = []
    for s in state_order:
        count = state_counter.get(s, 0)
        pct = count / total_hours * 100
        state_dist_lines.append(f"| {s.value:<14} | {count:>6} | {pct:>5.1f}% |")

    # ---- 二、状态转换统计 ----
    transition_counter = Counter()
    for sc in state_changes:
        key = f"{sc['old_state'].value} -> {sc['new_state'].value}"
        transition_counter[key] += 1

    transition_lines = []
    for trans, count in transition_counter.most_common(30):
        transition_lines.append(f"| {trans:<40} | {count:>4} |")

    # ---- 三、信号推送统计 ----
    push_count = sum(1 for e in events if e.should_push)
    push_by_state = Counter()
    for e in events:
        if e.should_push:
            push_by_state[e.state] += 1

    push_lines = []
    for s in state_order:
        count = push_by_state.get(s, 0)
        push_lines.append(f"| {s.value:<14} | {count:>4} |")

    # ---- 四、网格参数分布 ----
    grid_events = [e for e in events if e.grid_params is not None]
    grid_by_state = defaultdict(list)
    for e in grid_events:
        grid_by_state[e.state].append(e.grid_params)

    grid_dist_lines = []
    for s in [MarketState.OSCILLATION, MarketState.WEAK_TREND]:
        params_list = grid_by_state.get(s, [])
        if not params_list:
            grid_dist_lines.append(f"| {s.value:<14} | 无数据   | -        | -         | -          |")
            continue

        grid_counts = [p.grid_count for p in params_list]
        profit_rates = [float(p.profit_rate) * 100 for p in params_list]
        widths = [float(p.upper_boundary - p.lower_boundary) for p in params_list]

        avg_count = np.mean(grid_counts)
        avg_profit = np.mean(profit_rates)
        avg_width = np.mean(widths)
        min_profit = np.min(profit_rates)
        max_profit = np.max(profit_rates)

        grid_dist_lines.append(
            f"| {s.value:<14} | {avg_count:>6.1f}   | {avg_width:>7.0f}   | {avg_profit:>5.2f}%    | {min_profit:.2f}%-{max_profit:.2f}% |"
        )

    # ---- 五、关键事件时间线（状态变化） ----
    event_lines = []
    for sc in state_changes[:50]:  # 最多显示 50 条
        ts = sc['timestamp'].strftime('%Y-%m-%d %H:%M')
        old_s = sc['old_state'].value
        new_s = sc['new_state'].value
        adx1 = float(sc['adx_1h'])
        adx4 = float(sc['adx_4h'])
        atr = float(sc['atr_smooth'])
        price = float(sc['current_price'])
        event_lines.append(
            f"| {ts:<18} | {old_s:<14} | {new_s:<14} | {adx1:>5.1f}  | {adx4:>5.1f}  | {atr:>7.2f}  | {price:>8.2f} |"
        )

    # ---- 六、波动率异常事件 ----
    vol_lines = []
    for ve in volatility_events[:30]:  # 最多显示 30 条
        ts = ve['timestamp'].strftime('%Y-%m-%d %H:%M')
        atr = float(ve['atr_smooth'])
        count = ve['atr_abnormal_count']
        peak = float(ve['atr_peak'])
        price = float(ve['current_price'])
        vol_lines.append(
            f"| {ts:<18} | {atr:>8.2f}   | {count:>3}       | {peak:>8.2f}   | {price:>8.2f}     |"
        )

    # ---- 组装报告 ----
    report = f"""
================================================================================
  V2.1 网格交易信号灯回测报告
================================================================================
回测区间: {start_time} ~ {end_time}
交易对: ETHUSDT
巡检频率: 1小时
总巡检次数: {total_hours}
推送冷却时间: {push_cooldown_hours} 小时

一、市场状态分布
--------------------------------------------------------------------------------
| 状态           | 小时数 | 占比   |
|----------------|--------|--------|
{chr(10).join(state_dist_lines)}

二、状态转换统计
--------------------------------------------------------------------------------
| 转换                                    | 次数 |
|------------------------------------------|------|
{chr(10).join(transition_lines) if transition_lines else '| 无状态转换 | - |'}

三、信号推送统计
--------------------------------------------------------------------------------
总推送次数: {push_count}
推送频率: 每 {total_hours / max(push_count, 1):.1f} 小时推送一次

| 状态           | 推送次数 |
|----------------|----------|
{chr(10).join(push_lines)}

四、网格参数分布
--------------------------------------------------------------------------------
| 状态           | 平均网格数 | 平均区间宽度 | 平均利润率 | 利润率范围     |
|----------------|-----------|-------------|-----------|---------------|
{chr(10).join(grid_dist_lines)}

五、关键事件时间线（状态变化，最多显示 50 条）
--------------------------------------------------------------------------------
| 时间               | 旧状态         | 新状态         | ADX(1h) | ADX(4h) | ATR     | 价格      |
|--------------------|----------------|----------------|---------|---------|---------|-----------|
{chr(10).join(event_lines) if event_lines else '| 无状态变化事件 | - | - | - | - | - | - |'}

六、波动率异常事件（最多显示 30 条）
--------------------------------------------------------------------------------
| 时间               | ATR(平滑)   | 连续异常次数 | ATR峰值   | 价格       |
|--------------------|-------------|-------------|-----------|------------|
{chr(10).join(vol_lines) if vol_lines else '| 无波动率异常事件 | - | - | - | - |'}

================================================================================
  回测完成
================================================================================
"""
    return report


# ============================================================
# 主函数
# ============================================================

def main():
    """主函数：加载配置和数据，执行回测，输出报告"""
    print("=" * 60)
    print("  V2.1 网格交易信号灯回测")
    print("=" * 60)

    # 1. 加载配置
    config_path = os.path.join(PROJECT_ROOT, 'strategies', 'grid', 'config.yaml')
    with open(config_path, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)
    print(f"配置加载完成: {config_path}")

    # 2. 加载 K 线数据
    data_dir = os.path.join(PROJECT_ROOT, 'data', 'klines')
    df_1h = load_kline_data(os.path.join(data_dir, 'ethusdt_1h.csv'))
    df_4h = load_kline_data(os.path.join(data_dir, 'ethusdt_4h.csv'))

    print(f"1h K 线数据: {len(df_1h)} 行, {df_1h['timestamp'].iloc[0]} ~ {df_1h['timestamp'].iloc[-1]}")
    print(f"4h K 线数据: {len(df_4h)} 行, {df_4h['timestamp'].iloc[0]} ~ {df_4h['timestamp'].iloc[-1]}")

    start_time = str(df_1h['timestamp'].iloc[0])
    end_time = str(df_1h['timestamp'].iloc[-1])

    # 3. 创建回测引擎并执行
    engine = V21BacktestEngine(config)
    print("\n开始回测...")
    events = engine.run(df_1h, df_4h)

    # 4. 生成报告
    report = generate_report(
        events=events,
        state_changes=engine.state_changes,
        volatility_events=engine.volatility_events,
        config=config,
        start_time=start_time,
        end_time=end_time,
    )

    # 5. 输出报告
    print(report)

    # 6. 保存报告到文件
    report_dir = os.path.join(PROJECT_ROOT, 'backtest', 'grid', 'reports')
    os.makedirs(report_dir, exist_ok=True)
    report_file = os.path.join(report_dir, f'backtest_v21_{datetime.now().strftime("%Y%m%d_%H%M%S")}.txt')
    with open(report_file, 'w', encoding='utf-8') as f:
        f.write(report)
    print(f"报告已保存到: {report_file}")


if __name__ == '__main__':
    main()
