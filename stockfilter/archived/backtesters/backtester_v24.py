#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V2.4 回测器：
- 进一步激进的放宽条件以增加信号数量
- 目标：每月 2-3 个信号（每年 24-36 个）
- 特别关注 2019 年后的信号增加
"""

import pandas as pd
import yaml
from typing import Optional, Dict, List
from datetime import datetime


class BacktesterV24:
    """V2.4 回测器（激进放宽条件）"""
    
    def __init__(self, config_path: str = 'config_v21_final.yaml'):
        """初始化回测器"""
        with open(config_path, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)
        
        # V2.4 激进参数设置
        pattern_config = config.get('pattern', {})
        
        # 核心放宽参数
        self.drop_threshold = 0.08  # 跌幅从 12% 放宽到 8%
        self.surge_price_ratio = 0.03  # 放量涨幅从 5% 放宽到 3%
        self.min_volume_ratio = 1.2  # 量比从 1.5 放宽到 1.2
        self.max_volume_ratio = 15.0  # 量比上限从 12 放宽到 15
        self.volume_shrink_ratio = 0.8  # 缩量从 60% 放宽到 80%
        self.shrink_to_surge_days = 60  # 时间窗口从 25 天放宽到 60 天
        
        # 方案 B 参数
        self.flat_days = pattern_config.get('flat_days', 0)
        self.flat_volume_threshold = pattern_config.get('flat_volume_threshold', 0.85)
        self.flat_price_range = pattern_config.get('flat_price_range', 0.08)
        self.use_scheme_b = self.flat_days == 0
        self.post_surge_check_days = pattern_config.get('post_surge_check_days', 5)
        self.post_surge_max_drop = pattern_config.get('post_surge_max_drop', 0.97)
        
        # 交易参数
        trading_config = config.get('trading', {})
        self.trailing_stop_ratio = trading_config.get('trailing_stop_ratio', 0.08)
        self.hard_stop_loss = trading_config.get('hard_stop_loss', 0.10)
        self.min_hold_days = trading_config.get('min_hold_days', 5)
        self.max_hold_days = trading_config.get('max_hold_days', 30)
        self.commission = trading_config.get('commission', 0.00025)
        self.stamp_tax = trading_config.get('stamp_tax', 0.001)
        self.slippage = trading_config.get('slippage', 0.001)
        
        # 流动性过滤（降低要求）
        self.min_avg_volume = 20_000_000  # 从 3000 万降低到 2000 万
        self.volume_check_period = trading_config.get('volume_check_period', 20)
    
    def check_liquidity(self, df, retrace_idx):
        """检查股票流动性"""
        if retrace_idx < self.volume_check_period:
            return False
        
        start_idx = retrace_idx - self.volume_check_period
        end_idx = retrace_idx + 1
        
        recent_df = df.iloc[start_idx:end_idx].copy()
        
        if 'amount' in recent_df.columns:
            avg_amount = recent_df['amount'].mean()
        else:
            recent_amount = recent_df['volume'] * recent_df['close']
            avg_amount = recent_amount.mean()
        
        return avg_amount >= self.min_avg_volume
    
    def check_all_patterns(self, df: pd.DataFrame, code: str, 
                            period_start: str, period_end: str) -> List[Dict]:
        """检测所有符合条件的形态"""
        df = df.copy()
        df['date'] = pd.to_datetime(df['date'])
        df = df.sort_values('date').reset_index(drop=True)
        
        period_start_dt = pd.to_datetime(period_start)
        period_end_dt = pd.to_datetime(period_end)
        
        period_df = df[(df['date'] >= period_start_dt) & (df['date'] <= period_end_dt)].copy()
        
        if len(period_df) < 30:
            return []
        
        all_patterns = []
        search_start_idx = 0
        
        while search_start_idx < len(period_df) - 30:
            temp_df = period_df.iloc[search_start_idx:].copy()
            temp_df = temp_df.reset_index(drop=True)
            
            if len(temp_df) < 30:
                break
            
            pattern = self._detect_pattern_from_start(temp_df, code)
            
            if pattern:
                all_patterns.append(pattern)
                retrace_date = pd.to_datetime(pattern['retrace_date'])
                retrace_idx_in_temp = temp_df[temp_df['date'] == retrace_date].index
                if len(retrace_idx_in_temp) > 0:
                    search_start_idx += retrace_idx_in_temp[0] + 1
                else:
                    search_start_idx += 20
            else:
                break
        
        return all_patterns
    
    def _detect_pattern_from_start(self, period_df: pd.DataFrame, code: str) -> Optional[Dict]:
        """从给定数据的起始位置检测形态"""
        # 1. 检测大跌
        drop_found = False
        drop_start_idx = -1
        drop_end_idx = -1
        
        for i in range(20, len(period_df)):
            window_df = period_df.iloc[i-20:i+1]
            high_price = window_df['high'].max()
            low_price = window_df['low'].min()
            drop = (high_price - low_price) / high_price
            
            if drop >= self.drop_threshold:
                drop_found = True
                drop_start_idx = window_df[window_df['high'] == high_price].index[0]
                drop_end_idx = i
                break
        
        if not drop_found:
            return None
        
        # 2. 检测缩量（V2.4: 在放量日前 60 天内）
        shrink_found = False
        shrink_idx = -1
        
        for i in range(drop_end_idx+1, min(drop_end_idx + self.shrink_to_surge_days, len(period_df))):
            vol_i = period_df['volume'].iloc[i]
            vol_avg_20 = period_df['volume'].iloc[max(0, i-20):i].mean()
            
            if vol_i <= vol_avg_20 * self.volume_shrink_ratio:
                shrink_found = True
                shrink_idx = i
                break
        
        if not shrink_found:
            return None
        
        # 3. 检测放量
        surge_found = False
        surge_idx = -1
        
        for j in range(shrink_idx+1, min(shrink_idx+15, len(period_df))):
            vol_j = period_df['volume'].iloc[j]
            vol_prev = period_df['volume'].iloc[j-1]
            close_j = period_df['close'].iloc[j]
            close_prev = period_df['close'].iloc[j-1]
            
            vol_ratio = vol_j / vol_prev if vol_prev > 0 else 0
            price_change = (close_j - close_prev) / close_prev
            
            if vol_ratio >= self.min_volume_ratio and price_change >= self.surge_price_ratio:
                if not (self.min_volume_ratio <= vol_ratio <= self.max_volume_ratio):
                    continue
                
                if self.flat_days > 0:
                    is_flat = True
                    for k in range(j+1, min(j+1+self.flat_days, len(period_df))):
                        vol_k = period_df['volume'].iloc[k]
                        close_k = period_df['close'].iloc[k]
                        
                        if vol_k >= vol_j * self.flat_volume_threshold:
                            is_flat = False
                            break
                        
                        price_change_k = abs(close_k - close_j) / close_j
                        if price_change_k >= self.flat_price_range:
                            is_flat = False
                            break
                    
                    if not is_flat:
                        continue
                    
                    surge_found = True
                    surge_idx = j
                    break
                
                else:
                    support_level = close_j * self.post_surge_max_drop
                    is_valid = True
                    
                    for k in range(j+1, min(j+1+self.post_surge_check_days, len(period_df))):
                        low_k = period_df['low'].iloc[k]
                        if low_k < support_level:
                            is_valid = False
                            break
                    
                    if is_valid:
                        surge_found = True
                        surge_idx = j
                        break
        
        if not surge_found:
            return None
        
        # 4. 检测回踩确认
        retrace_found = False
        retrace_idx = -1
        
        support_level = period_df['low'].iloc[surge_idx]
        
        for i in range(surge_idx+1, min(surge_idx+10, len(period_df))):
            low_i = period_df['low'].iloc[i]
            close_i = period_df['close'].iloc[i]
            
            if low_i >= support_level * 0.98:
                retrace_found = True
                retrace_idx = i
                break
        
        if not retrace_found:
            return None
        
        if not self.check_liquidity(period_df, retrace_idx):
            return None
        
        return {
            'code': code,
            'drop_start_date': period_df['date'].iloc[drop_start_idx],
            'drop_end_date': period_df['date'].iloc[drop_end_idx],
            'drop_change': (period_df['high'].iloc[drop_start_idx] - period_df['low'].iloc[drop_end_idx]) / period_df['high'].iloc[drop_start_idx],
            'shrink_date': period_df['date'].iloc[shrink_idx],
            'surge_date': period_df['date'].iloc[surge_idx],
            'surge_close': period_df['close'].iloc[surge_idx],
            'retrace_date': period_df['date'].iloc[retrace_idx],
            'retrace_close': period_df['close'].iloc[retrace_idx],
            'retrace_low': period_df['low'].iloc[retrace_idx],
            'support_level': support_level,
            'is_match': True,
            'scheme': 'V2.4',
            'shrink_to_surge_days': self.shrink_to_surge_days,
            'drop_threshold': self.drop_threshold,
            'surge_price_ratio': self.surge_price_ratio,
            'min_volume_ratio': self.min_volume_ratio,
            'volume_shrink_ratio': self.volume_shrink_ratio
        }
    
    def check_pattern_single(self, df: pd.DataFrame, code: str, 
                            period_start: str, period_end: str) -> Optional[Dict]:
        """检测单只股票是否符合形态（只返回第一个）"""
        all_patterns = self.check_all_patterns(df, code, period_start, period_end)
        if all_patterns and len(all_patterns) > 0:
            return all_patterns[0]
        return None
    
    def simulate_trade(self, df: pd.DataFrame, pattern_info: Dict) -> Optional[Dict]:
        """模拟交易"""
        code = pattern_info['code']
        retrace_date = pd.to_datetime(pattern_info['retrace_date'])
        buy_price = None
        sell_price = None
        sell_date = None
        sell_reason = None
        
        df = df.copy()
        df['date'] = pd.to_datetime(df['date'])
        df = df.sort_values('date').reset_index(drop=True)
        
        retrace_idx_list = df[df['date'] == retrace_date].index
        if len(retrace_idx_list) == 0:
            return None
        retrace_idx = retrace_idx_list[0]
        
        if retrace_idx + 1 >= len(df):
            return None
        
        next_day = df.iloc[retrace_idx + 1]
        open_price = next_day['open']
        prev_close = df.iloc[retrace_idx]['close']
        
        open_change = (open_price - prev_close) / prev_close
        
        if open_change >= 0.05:
            return None
        
        if open_change > 0.03:
            buy_price = open_price * (1 + 0.01)
        else:
            buy_price = open_price
        
        buy_date = next_day['date']
        
        high_since_buy = buy_price
        close_since_buy = buy_price
        
        for i in range(retrace_idx + 2, min(retrace_idx + 2 + self.max_hold_days, len(df))):
            row = df.iloc[i]
            current_date = row['date']
            current_close = row['close']
            current_high = row['high']
            current_low = row['low']
            
            hold_days = (current_date - buy_date).days
            
            if current_high > high_since_buy:
                high_since_buy = current_high
            
            trailing_stop_price = high_since_buy * (1 - self.trailing_stop_ratio)
            hard_stop_price = buy_price * (1 - self.hard_stop_loss)
            
            if hold_days >= self.min_hold_days:
                if current_low <= trailing_stop_price:
                    sell_price = trailing_stop_price
                    sell_reason = '移动止盈'
                    sell_date = current_date
                    break
        
            if current_low <= hard_stop_price and hold_days >= self.min_hold_days:
                sell_price = hard_stop_price
                sell_reason = '硬止损'
                sell_date = current_date
                break
            
            if hold_days >= self.max_hold_days:
                sell_price = current_close
                sell_reason = '时间止盈'
                sell_date = current_date
                break
            
            close_since_buy = current_close
        
        if sell_price is None:
            sell_price = close_since_buy
            sell_reason = '未触发卖出'
            sell_date = df.iloc[min(retrace_idx + 2 + self.max_hold_days - 1, len(df)-1)]['date']
        
        gross_return = (sell_price - buy_price) / buy_price
        cost = self.commission * 2 + self.stamp_tax + self.slippage * 2
        net_return = gross_return - cost
        
        return {
            'code': code,
            'buy_date': buy_date,
            'sell_date': sell_date,
            'buy_price': buy_price,
            'sell_price': sell_price,
            'gross_return': gross_return,
            'net_return': net_return,
            'holding_days': (sell_date - buy_date).days,
            'sell_reason': sell_reason
        }
