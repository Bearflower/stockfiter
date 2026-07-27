"""
超跌反弹回踩确认策略

策略流程：大跌 -> 缩量 -> 放量 -> 回踩确认

将原 Backtester._detect_pattern_from_start 和 check_liquidity 逻辑迁移到此
"""

from typing import Dict, List, Optional
from datetime import datetime

import pandas as pd

from strategy.base import BaseStrategy, Signal, DataRequirement
from utils.logger import get_logger

logger = get_logger()


class OversoldBounceStrategy(BaseStrategy):
    """
    超跌反弹回踩确认策略

    检测流程：
    1. 检测大跌（在回溯窗口内跌幅超过阈值）
    2. 检测缩量（大跌后成交量萎缩）
    3. 检测放量（缩量后出现放量大涨）
    4. 检测回踩确认（放量后回踩不破支撑）
    5. 流动性检查（日均成交额满足最小要求）
    """

    name: str = "oversold_bounce"
    version: str = "v25"
    description: str = "超跌反弹回踩确认策略：大跌->缩量->放量->回踩确认"

    def _get_default_params(self) -> Dict:
        """获取默认策略参数"""
        return {
            # 大跌检测参数
            'drop_threshold': 0.12,
            'drop_window': 20,

            # 缩量检测参数
            'volume_shrink_ratio': 0.6,
            'shrink_to_surge_days': 10,

            # 放量检测参数
            'min_volume_ratio': 1.5,
            'max_volume_ratio': 12.0,
            'surge_price_ratio': 0.05,
            'surge_lookback': 15,

            # 方案A参数：缩量走平
            'flat_days': 1,
            'flat_volume_threshold': 0.85,
            'flat_price_range': 0.08,
            'post_surge_check_days': 5,
            'post_surge_max_drop': 0.97,

            # 回踩确认参数
            'retrace_max_days': 10,
            'support_ratio': 0.985,

            # 流动性参数
            'min_avg_amount': 30_000_000,
            'volume_check_period': 20,

            # 回测参数
            'trailing_stop_ratio': 0.08,
            'hard_stop_loss': 0.10,
            'min_hold_days': 5,
            'max_hold_days': 30,
            'commission': 0.00025,
            'stamp_tax': 0.001,
            'slippage': 0.001,
        }

    def declare_data_requirements(self, codes: List[str]) -> List[DataRequirement]:
        """声明数据需求：每只股票需要120天日K线"""
        return [
            DataRequirement(code=code, frequency='d', lookback=120)
            for code in codes
        ]

    def analyze(
        self, code: str, data: Dict[str, pd.DataFrame]
    ) -> Optional[Signal]:
        """
        分析单只股票，检测是否符合策略形态

        Args:
            code: 股票代码
            data: K 线数据字典，key='d' 对应日线 DataFrame

        Returns:
            Optional[Signal]: 如果匹配则返回信号，否则返回 None
        """
        df = data.get('d')
        if df is None or len(df) < 60:
            logger.debug(f"{code} 数据不足60天，跳过")
            return None

        df = df.copy()
        df['date'] = pd.to_datetime(df['date'])
        df = df.sort_values('date').reset_index(drop=True)

        # 检测所有形态，返回第一个匹配的
        pattern = self._detect_pattern(df, code)
        if pattern is None:
            return None

        # 构建信号
        retrace_date = pattern.get('retrace_date')
        if isinstance(retrace_date, pd.Timestamp):
            retrace_date = retrace_date.strftime('%Y-%m-%d')
        elif isinstance(retrace_date, datetime):
            retrace_date = retrace_date.strftime('%Y-%m-%d')

        signal = Signal(
            code=code,
            signal_type='buy',
            signal_date=str(retrace_date) if retrace_date else "",
            strategy_name=self.name,
            strategy_version=self.version,
            detail={
                'support_level': pattern.get('support_level', 0),
                'surge_date': str(pattern.get('surge_date', '')),
                'surge_close': pattern.get('surge_close', 0),
                'surge_pct': pattern.get('surge_pct', 0),
                'surge_volume_ratio': pattern.get('surge_volume_ratio', 0),
                'drop_rate': pattern.get('drop_rate', 0),
                'retrace_date': str(retrace_date) if retrace_date else "",
                'retrace_close': pattern.get('retrace_close', 0),
                'retrace_low': pattern.get('retrace_low', 0),
                'drop_change': pattern.get('drop_change', 0),
                'drop_start_date': str(pattern.get('drop_start_date', '')),
                'drop_end_date': str(pattern.get('drop_end_date', '')),
                'shrink_date': str(pattern.get('shrink_date', '')),
                'shrink_ratio': pattern.get('shrink_ratio', 0.6),
            },
            backtest_params=self.get_backtest_params(),
        )

        # 计算评分
        signal.score = self.score(signal)

        return signal

    def _detect_pattern(
        self, df: pd.DataFrame, code: str
    ) -> Optional[Dict]:
        """
        检测形态（遍历所有大跌，返回第一个完整走通大跌→缩量→放量→回踩→流动性的形态）

        Args:
            df: K 线数据
            code: 股票代码

        Returns:
            Optional[Dict]: 匹配的形态信息
        """
        p = self.params
        drop_window = p.get('drop_window', 20)

        # 1. 遍历所有大跌窗口，每个都尝试走完整流程
        for i in range(drop_window, len(df)):
            window_df = df.iloc[i - drop_window:i + 1]
            high_price = window_df['high'].max()
            low_price = window_df['low'].min()
            drop = (high_price - low_price) / high_price if high_price > 0 else 0

            if drop < p['drop_threshold']:
                continue

            # 验证最高价在最低价之前（时间顺序），确保是"大跌"而非"大涨"
            high_idx = window_df[window_df['high'] == high_price].index[0]
            low_idx = window_df[window_df['low'] == low_price].index[0]
            if high_idx >= low_idx:
                continue

            drop_start_idx = high_idx
            drop_end_idx = low_idx

            # 2. 从这个大跌开始，尝试检测缩量→放量→回踩→流动性
            result = self._try_complete_pattern(
                df, code, p, drop_start_idx, drop_end_idx
            )
            if result is not None:
                return result

        return None

    def _try_complete_pattern(
        self, df: pd.DataFrame, code: str, p: Dict,
        drop_start_idx: int, drop_end_idx: int
    ) -> Optional[Dict]:
        """
        从给定的大跌位置开始，尝试完成后续放量（往前验证缩量）->走平->回踩->流动性检测

        检测流程：
        1. 在大跌结束后的搜索窗口内寻找放量日
        2. 对每个放量日，往前 shrink_to_surge_days 天内验证是否有缩量日
        3. 验证放量后走平（方案A）
        4. 回踩确认
        5. 流动性检查

        Args:
            df: K 线数据
            code: 股票代码
            p: 策略参数
            drop_start_idx: 大跌起始索引
            drop_end_idx: 大跌结束索引

        Returns:
            Optional[Dict]: 匹配的形态信息，或 None
        """
        shrink_window = p['shrink_to_surge_days']
        surge_lookback = p.get('surge_lookback', 15)
        search_end = min(drop_end_idx + shrink_window + surge_lookback, len(df))

        # 1. 在大跌结束后的搜索窗口内寻找放量日
        for j in range(drop_end_idx + 1, search_end):
            vol_j = df['volume'].iloc[j]
            vol_prev = df['volume'].iloc[j - 1]
            close_j = df['close'].iloc[j]
            close_prev = df['close'].iloc[j - 1]

            vol_ratio = vol_j / vol_prev if vol_prev > 0 else 0
            price_change = (close_j - close_prev) / close_prev if close_prev > 0 else 0

            # 放量日条件：量比和涨幅同时满足
            if not (
                p['min_volume_ratio'] <= vol_ratio <= p['max_volume_ratio']
                and price_change >= p['surge_price_ratio']
            ):
                continue

            # 2. 往前 shrink_window 天内验证是否有缩量日
            shrink_idx = -1
            actual_shrink_ratio = 1.0  # 默认缩量比例
            search_start = max(drop_end_idx + 1, j - shrink_window)
            vol_lookback = p.get('volume_check_period', 20)

            for i in range(search_start, j):
                vol_i = df['volume'].iloc[i]
                vol_avg = df['volume'].iloc[max(0, i - vol_lookback):i].mean()

                if vol_i <= vol_avg * p['volume_shrink_ratio']:
                    shrink_idx = i
                    # 记录实际缩量比例，供评分使用
                    actual_shrink_ratio = vol_i / vol_avg if vol_avg > 0 else 1.0
                    break

            if shrink_idx == -1:
                continue

            # 3. 验证放量后走平（方案A）或观察期（方案B）
            flat_days = p.get('flat_days', 0)
            if flat_days > 0:
                # 方案A: 检查缩量走平
                is_flat = True
                for k in range(j + 1, min(j + 1 + flat_days, len(df))):
                    vol_k = df['volume'].iloc[k]
                    close_k = df['close'].iloc[k]

                    # 走平缩量：严格小于阈值才算缩量
                    if vol_k > vol_j * p['flat_volume_threshold']:
                        is_flat = False
                        break

                    # 走平振幅取当日实体振幅（最高-最低）/收盘
                    price_range_k = (df['high'].iloc[k] - df['low'].iloc[k]) / close_k if close_k > 0 else 0
                    if price_range_k >= p['flat_price_range']:
                        is_flat = False
                        break

                if not is_flat:
                    continue
            else:
                # 方案B: 检查启动后观察期
                support_level_b = close_j * p['post_surge_max_drop']
                is_valid = True

                for k in range(
                    j + 1,
                    min(j + 1 + p['post_surge_check_days'], len(df))
                ):
                    low_k = df['low'].iloc[k]
                    if low_k < support_level_b:
                        is_valid = False
                        break

                if not is_valid:
                    continue

            # 4. 检测回踩确认
            # 支撑位取大跌区间最低点
            support_level = df['low'].iloc[drop_start_idx:drop_end_idx + 1].min()

            # 检查放量后 retrace_max_days 天内的最低点是否在支撑位上方
            retrace_window_end = min(j + 1 + p['retrace_max_days'], len(df))
            retrace_window = df['low'].iloc[j + 1:retrace_window_end]

            if len(retrace_window) == 0:
                continue

            retrace_lowest = retrace_window.min()
            if retrace_lowest < support_level * p['support_ratio']:
                continue

            # 回踩日为窗口内最低点对应的日子
            retrace_idx = retrace_window.idxmin()

            # 5. 流动性检查
            if not self._check_liquidity(df, retrace_idx):
                continue

            # 计算大跌幅度（使用窗口最高价和最低价，与检测逻辑一致）
            drop_high = df['high'].iloc[drop_start_idx]
            drop_low = df['low'].iloc[drop_start_idx:drop_end_idx + 1].min()
            drop_rate = (drop_high - drop_low) / drop_high if drop_high > 0 else 0

            pattern = {
                'code': code,
                'drop_start_date': df['date'].iloc[drop_start_idx],
                'drop_end_date': df['date'].iloc[drop_end_idx],
                'drop_change': drop_rate,
                'drop_rate': drop_rate,
                'shrink_date': df['date'].iloc[shrink_idx],
                'shrink_ratio': actual_shrink_ratio,
                'surge_date': df['date'].iloc[j],
                'surge_close': df['close'].iloc[j],
                'surge_pct': price_change,
                'surge_volume_ratio': vol_ratio,
                'retrace_date': df['date'].iloc[retrace_idx],
                'retrace_close': df['close'].iloc[retrace_idx],
                'retrace_low': df['low'].iloc[retrace_idx],
                'support_level': support_level,
            }

            return pattern

        return None

    def _check_liquidity(self, df: pd.DataFrame, retrace_idx: int) -> bool:
        """
        检查股票流动性

        Args:
            df: K 线数据
            retrace_idx: 回踩确认索引

        Returns:
            bool: 是否满足流动性要求
        """
        period = self.params['volume_check_period']
        if retrace_idx < period:
            return False

        start_idx = retrace_idx - period
        end_idx = retrace_idx + 1

        recent_df = df.iloc[start_idx:end_idx].copy()

        if 'amount' in recent_df.columns:
            avg_amount = recent_df['amount'].mean()
        else:
            recent_amount = recent_df['volume'] * recent_df['close']
            avg_amount = recent_amount.mean()

        return avg_amount >= self.params['min_avg_amount']

    def score(self, signal: Signal) -> float:
        """
        对信号进行评分（0-100）

        五维度加权评分：
        - 跌幅深度（25%）：跌得越深，反弹潜力越大
        - 缩量程度（20%）：缩量越充分，筹码越稳定
        - 放量强度（20%）：放量越强，资金介入越明显
        - 回踩幅度（20%）：回踩越浅，支撑越强
        - 支撑强度（15%）：支撑位离回踩低点越近，支撑越有效
        """
        d = signal.detail
        scores = {}

        # 跌幅深度：12%=60分，20%=100分
        drop_rate = d.get('drop_rate', 0)
        if drop_rate < 0.12:
            scores['drop_depth'] = 0
        elif drop_rate >= 0.20:
            scores['drop_depth'] = 100
        else:
            scores['drop_depth'] = 60 + (drop_rate - 0.12) / 0.08 * 40

        # 缩量程度：0.6=60分，0.3=100分（取实际缩量比例）
        shrink_ratio = d.get('shrink_ratio', 0.6)
        if shrink_ratio >= 0.6:
            scores['shrink_degree'] = 60
        elif shrink_ratio <= 0.3:
            scores['shrink_degree'] = 100
        else:
            scores['shrink_degree'] = 100 - (shrink_ratio - 0.3) / 0.3 * 40

        # 放量强度：1.5倍=60分，3倍=100分
        surge_vol_ratio = d.get('surge_volume_ratio', 0)
        if surge_vol_ratio < 1.5:
            scores['surge_strength'] = 0
        elif surge_vol_ratio >= 3.0:
            scores['surge_strength'] = 100
        else:
            scores['surge_strength'] = 60 + (surge_vol_ratio - 1.5) / 1.5 * 40

        # 回踩幅度：回踩低点越接近支撑位分数越高
        support_level = d.get('support_level', 0)
        retrace_low = d.get('retrace_low', 0)
        if support_level > 0 and retrace_low > 0:
            retrace_ratio = retrace_low / support_level
            # retrace_ratio=1.0（未跌破）=100分，retrace_ratio=0.985（刚好满足）=60分
            if retrace_ratio >= 1.0:
                scores['retrace_depth'] = 100
            elif retrace_ratio <= 0.985:
                scores['retrace_depth'] = 60
            else:
                scores['retrace_depth'] = 60 + (retrace_ratio - 0.985) / 0.015 * 40
        else:
            scores['retrace_depth'] = 60

        # 放量涨幅：5%=60分，8%=100分
        surge_pct = d.get('surge_pct', 0)
        if surge_pct < 0.05:
            scores['surge_pct'] = 0
        elif surge_pct >= 0.08:
            scores['surge_pct'] = 100
        else:
            scores['surge_pct'] = 60 + (surge_pct - 0.05) / 0.03 * 40

        weights = {
            'drop_depth': 0.25,
            'shrink_degree': 0.20,
            'surge_strength': 0.20,
            'retrace_depth': 0.20,
            'surge_pct': 0.15,
        }

        total = sum(scores[k] * weights[k] for k in weights)
        return round(min(100, max(0, total)), 2)

    def get_backtest_params(self) -> Dict:
        """获取回测参数"""
        return {
            'trailing_stop_ratio': self.params['trailing_stop_ratio'],
            'hard_stop_loss': self.params['hard_stop_loss'],
            'min_hold_days': self.params['min_hold_days'],
            'max_hold_days': self.params['max_hold_days'],
            'commission': self.params['commission'],
            'stamp_tax': self.params['stamp_tax'],
            'slippage': self.params['slippage'],
        }