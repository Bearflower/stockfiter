#!/usr/bin/env python3
"""
策略参数管理模块

基于 traderule.txt 第三、四、五章的可调参数集中管理
支持热加载、版本管理和参数验证

功能：
1. 信号等级阈值配置（S/A 级的条件）
2. 仓位计算公式参数
3. 止盈止损倍数
4. 风险控制参数
5. 支持 JSON/YAML 配置文件
6. 支持热加载（无需重启系统）
"""

import os
import json
import logging
from datetime import datetime
from decimal import Decimal
from typing import Dict, Any, Optional, List
from pathlib import Path

logger = logging.getLogger(__name__)


class StrategyParams:
    """策略参数管理类"""
    
    def __init__(self, config_file: str = None):
        """
        初始化策略参数
        
        v5.3 说明：已禁用 JSON 配置文件，只使用 Python 中定义的参数。
        
        Args:
            config_file: 已忽略（为向后兼容保留参数）
        """
        self.config_file = config_file or os.getenv('STRATEGY_PARAMS_FILE', 'config/strategy_params.json')
        self.params: Dict[str, Any] = {}
        self.version: str = ""
        self.last_loaded: datetime = None
        
        # 加载默认参数（基于 traderule.txt v5.3）
        self._load_default_params()
        
        # v5.3: 已禁用 JSON 配置加载
        # if os.path.exists(self.config_file):
        #     self.load_from_file()
    
    def _load_default_params(self):
        """加载默认参数（基于 traderule.txt 文档）"""
        self.params = {
            # ========== 第一章：核心交易原则 ==========
            'account': {
                'total_capital': Decimal('500'),  # 总资金
                'single_position_margin': Decimal('15'),  # 单仓保证金（临时调整为 15U，适应小资金账户）
                'max_positions': 2,  # 最大同时持仓数
                'reserve_capital_ratio': Decimal('0.8'),  # 备用金比例（80%）
                'max_total_margin_ratio': Decimal('0.3'),  # 最大总保证金占用比例（30%）
            },
            
            # ========== 第二章：禁止交易情形 ==========
            'prohibited_conditions': {
                'max_24h_price_change': Decimal('0.25'),  # 24 小时涨幅 > 25% 禁止
                'max_24h_price_drop': Decimal('0.20'),  # 24 小时跌幅 > 20% 禁止
                'max_funding_rate': Decimal('0.0008'),  # |资金费率| > 0.08% 禁止
                'max_spread_ratio': Decimal('0.003'),  # 买卖价差 > 0.3% 禁止
                'news_blackout_window': 3600,  # 重大消息前后 1 小时（秒）
            },
            
            # ========== 第三章：信号等级定义 ==========
            'signal_grades': {
                'S': {
                    'min_profit_loss_ratio': Decimal('4.0'),  # 盈亏比 ≥ 1:4
                    'max_leverage': 5,  # 杠杆上限 5 倍
                    'min_recommendation_score': 85,  # 推荐度 ≥ 85 分
                    'require_multi_timeframe': True,  # 需要多时间框架共振
                },
                'A': {
                    'min_profit_loss_ratio': Decimal('3.0'),  # 盈亏比 ≥ 1:3
                    'max_leverage': 4,  # 杠杆上限 4 倍
                    'min_recommendation_score': 70,  # 推荐度 ≥ 70 分
                    'require_multi_timeframe': True,  # 需要多时间框架确认
                },
                'B': {
                    'min_profit_loss_ratio': Decimal('2.5'),  # 盈亏比 ≥ 1:2.5
                    'max_leverage': 3,  # 杠杆上限 3 倍
                    'min_recommendation_score': 50,  # 推荐度 ≥ 50 分
                    'require_multi_timeframe': False,  # 仅 1 小时信号
                },
            },
            
            # 允许交易的信号等级（500U 阶段一仅允许 S/A 级）
            'allowed_signal_grades': ['S', 'A'],
            
            # 趋势过滤器参数
            'trend_filter': {
                'ema21_period': 21,  # EMA 周期
                'trend_slope_threshold': Decimal('0.01'),  # 趋势斜率阈值（1%）
                'support_resistance_tolerance': Decimal('0.01'),  # 支撑/阻力容忍度（1%）
                'high_low_distance_threshold': Decimal('0.03'),  # 距离前高/前低 < 3% 禁止入场
            },
            
            # 信号过滤器参数
            'signal_filters': {
                'min_atr_pct': Decimal('0.003'),  # 最小 ATR 百分比（0.3%）
                'max_atr_pct': Decimal('0.10'),  # 最大 ATR 百分比（10%）
            },
            
            # ========== 第四章：仓位管理 ==========
            'position_sizing': {
                'risk_amount': Decimal('10'),  # 单笔风险金额（总资金的 2% = 10U）
                'min_stop_loss_pct': Decimal('0.02'),  # v6.13.3: 最小止损幅度 2%（从 3% 下调）
                'max_stop_loss_pct': Decimal('0.04'),  # v6.13.3: 最大止损幅度 4%（从 7% 下调）
                'max_position_notional': Decimal('1500'),  # 单品种最大名义价值（3 倍总资金）
                'max_total_notional': Decimal('4000'),  # 所有持仓最大名义价值（8 倍总资金）
                # v5.3 仓位系数机制（核心）
                'position_coefficient': {
                    'S': Decimal('0.5'),  # S 级：50%（高确信度，半仓）
                    'A': Decimal('0.3'),  # A 级：30%（中等确信度，轻仓）
                    'B': Decimal('0.2'),  # B 级：20%（试仓，极轻仓）
                },
            },
            
            # ========== 第五章：风险管理 ==========
            'risk_management': {
                'stop_loss_multiplier': Decimal('1.0'),  # 止损倍数（100% 执行）
                # v5.3 止盈配置（基于 ATR 倍数）
                'take_profit_levels': {
                    'tp1_ratio': Decimal('0.3'),  # TP1 平仓 30%（A/B 级），S 级在代码中特殊处理为 20%
                    'tp1_multiplier': Decimal('4.0'),  # v5.3: TP1 = 4.0×ATR
                    'tp2_ratio': Decimal('0.3'),  # TP2 平仓 30%
                    'tp2_multiplier': Decimal('6.0'),  # v5.3: TP2 = 6.0×ATR
                    'tp3_ratio': Decimal('0.4'),  # TP3 平仓 40%（A/B 级），S 级在代码中特殊处理为 50%
                },
                'margin_ratio_warning': Decimal('1.5'),  # 保证金率预警线（≤1.5 减仓）
                'margin_ratio_emergency': Decimal('1.2'),  # 保证金率紧急线（≤1.2 全平）
                'max_margin_usage': Decimal('0.6'),  # 保证金使用率 > 60% 减仓
                'max_float_loss': Decimal('20'),  # 单笔浮亏 > 20U 强制止损
            },
            
            # 移动止损参数（v5.3 规范）
            'trailing_stop': {
                'enable_after_tp1': True,  # TP1 后移至保本
                'enable_after_tp2': True,  # TP2 后移至 TP1
                'use_sar_or_ema': 'EMA21',  # v5.3: TP3 后用 EMA21 跟踪
            },
            
            # ========== 第七章：应急处理 ==========
            'emergency_handling': {
                'extreme_price_drop': Decimal('0.05'),  # 价格瞬间反向波动 5%
                'emergency_close_ratio': Decimal('0.5'),  # 极端行情平仓 50%
                'emergency_stop_loss': Decimal('0.015'),  # 剩余仓位止损收紧至 1.5%
                'consecutive_losses_limit': 2,  # 连续 2 笔亏损停止交易
                'consecutive_losses_pause_days': 3,  # 停止交易 3 天
                'weekly_loss_limit': Decimal('0.15'),  # 单周亏损 > 15% 停止交易
                'weekly_loss_pause_days': 3,  # 停止交易 3 天
            },
            
            # ========== 第九章：策略优化 ==========
            'strategy_optimization': {
                'min_trades_for_optimization': 30,  # 累计 30 笔交易后可优化参数
                'optimization_sample_size': 10,  # 每次观察后续 10 笔交易
                'adjustable_params': [
                    'position_sizing.min_stop_loss_pct',
                    'position_sizing.max_stop_loss_pct',
                    'risk_management.take_profit_levels.tp1_multiplier',
                    'risk_management.take_profit_levels.tp2_multiplier',
                    'account.single_position_margin',
                ],
                'forbidden_adjustments': [
                    'position_sizing.risk_amount',  # 禁止调整单笔风险金额
                    'risk_management.margin_ratio_warning',
                    'risk_management.margin_ratio_emergency',
                ],
            },
            
            # ========== 第十章：绩效评估标准 ==========
            'performance_metrics': {
                'min_win_rate': Decimal('0.45'),  # 胜率 > 45% 可接受
                'good_win_rate': Decimal('0.55'),  # 胜率 > 55% 优秀
                'min_profit_loss_ratio': Decimal('1.8'),  # 盈亏比 > 1.8 可接受
                'good_profit_loss_ratio': Decimal('2.5'),  # 盈亏比 > 2.5 优秀
                'min_leverage_efficiency': Decimal('0.5'),  # 杠杆效率 > 0.5
                'max_bankruptcy_rate': Decimal('0.15'),  # 爆仓率 < 15%
                'max_drawdown': Decimal('0.25'),  # 最大回撤 < 25%
            },
        }
        
        self.version = "1.0.0"
        self.last_loaded = datetime.now()
        logger.info(f"已加载默认策略参数（版本：{self.version}）")
    
    def load_from_file(self, config_file: str = None):
        """
        从配置文件加载参数 - 已禁用
        
        v5.3 说明：为避免双重配置导致的混乱，现已禁用 JSON 配置文件覆盖机制。
        所有参数统一在 Python 文件中定义，确保配置单一来源。
        
        原因：
        1. JSON 配置会覆盖 Python 配置，导致实际参数与代码不一致
        2. 部署时容易遗漏 JSON 文件，造成配置不同步
        3. 维护两套配置增加复杂度和出错风险
        
        如需修改参数，请直接修改 Python 文件中的 _load_default_params 方法。
        """
        # 已禁用 JSON 配置加载，只使用 Python 中定义的默认参数
        logger.info("使用 Python 默认配置（JSON 配置已禁用 - v5.3）")
        return
        
        # 以下为原始代码（已注释）
        # file_path = config_file or self.config_file
        # if not os.path.exists(file_path):
        #     logger.warning(f"配置文件不存在：{file_path}，使用默认参数")
        #     return
        # try:
        #     with open(file_path, 'r', encoding='utf-8') as f:
        #         file_params = json.load(f)
        #     self._merge_params(file_params)
        #     self.version = file_params.get('version', 'custom')
        #     self.last_loaded = datetime.now()
        #     logger.info(f"已从配置文件加载参数：{file_path}（版本：{self.version}）")
        # except Exception as e:
        #     logger.error(f"加载配置文件失败：{str(e)}，使用默认参数")
    
    def _merge_params(self, new_params: Dict[str, Any], base: Dict[str, Any] = None):
        """
        递归合并参数字典
        
        Args:
            new_params: 新参数
            base: 基础参数（默认使用 self.params）
        """
        if base is None:
            base = self.params
        
        for key, value in new_params.items():
            if key in base:
                if isinstance(value, dict) and isinstance(base[key], dict):
                    # 递归合并子字典
                    self._merge_params(value, base[key])
                else:
                    # 覆盖标量值
                    try:
                        # 尝试转换为 Decimal（如果是数字字符串或数字类型）
                        if isinstance(value, str):
                            # 字符串：尝试转换为 Decimal（支持整数和小数）
                            base[key] = Decimal(value)
                        elif isinstance(value, (int, float)):
                            # 数字类型：转换为 Decimal
                            base[key] = Decimal(str(value))
                        else:
                            # 其他类型（如 bool、list 等）：保持原值
                            base[key] = value
                    except Exception as e:
                        logger.warning(f"参数 {key} 转换失败：{str(e)}，使用原始值")
                        base[key] = value
            else:
                # 添加新参数
                base[key] = value
    
    def save_to_file(self, config_file: str = None):
        """
        保存参数到文件
        
        Args:
            config_file: 配置文件路径（可选）
        """
        file_path = config_file or self.config_file
        
        try:
            # 确保目录存在
            Path(file_path).parent.mkdir(parents=True, exist_ok=True)
            
            # 转换 Decimal 为字符串（JSON 不支持 Decimal）
            def decimal_to_str(obj):
                if isinstance(obj, Decimal):
                    return str(obj)
                elif isinstance(obj, dict):
                    return {k: decimal_to_str(v) for k, v in obj.items()}
                elif isinstance(obj, list):
                    return [decimal_to_str(item) for item in obj]
                else:
                    return obj
            
            save_params = {
                'version': self.version,
                'last_saved': datetime.now().isoformat(),
                **self.params
            }
            
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(decimal_to_str(save_params), f, indent=2, ensure_ascii=False)
            
            logger.info(f"参数已保存到：{file_path}")
            
        except Exception as e:
            logger.error(f"保存参数失败：{str(e)}")
    
    def get(self, key_path: str, default: Any = None) -> Any:
        """
        获取参数值（支持点分隔的路径）
        
        Args:
            key_path: 参数路径，如 'account.total_capital'
            default: 默认值（如果参数不存在）
        
        Returns:
            参数值
        
        Example:
            >>> params.get('account.total_capital')
            Decimal('500')
            >>> params.get('signal_grades.S.max_leverage')
            5
        """
        keys = key_path.split('.')
        value = self.params
        
        try:
            for key in keys:
                value = value[key]
            return value
        except (KeyError, TypeError):
            if default is not None:
                return default
            logger.warning(f"参数不存在：{key_path}")
            return None
    
    def set(self, key_path: str, value: Any):
        """
        设置参数值（支持点分隔的路径）
        
        Args:
            key_path: 参数路径
            value: 参数值
        
        Example:
            >>> params.set('account.single_position_margin', Decimal('50'))
        """
        keys = key_path.split('.')
        current = self.params
        
        try:
            # 导航到父级
            for key in keys[:-1]:
                current = current[key]
            
            # 设置值
            current[keys[-1]] = value
            logger.info(f"参数已更新：{key_path} = {value}")
            
        except (KeyError, TypeError) as e:
            logger.error(f"设置参数失败：{key_path} = {value}, 错误：{str(e)}")
    
    def validate(self) -> List[str]:
        """
        验证参数有效性
        
        Returns:
            错误消息列表（空列表表示验证通过）
        """
        errors = []
        
        # 验证账户参数
        if self.params['account']['total_capital'] <= 0:
            errors.append("总资金必须 > 0")
        if self.params['account']['max_positions'] <= 0:
            errors.append("最大持仓数必须 > 0")
        
        # 验证止损参数
        if self.params['position_sizing']['min_stop_loss_pct'] >= self.params['position_sizing']['max_stop_loss_pct']:
            errors.append("最小止损幅度必须 < 最大止损幅度")
        
        # 验证信号等级参数
        for grade in ['S', 'A', 'B']:
            if grade in self.params['signal_grades']:
                if self.params['signal_grades'][grade]['min_profit_loss_ratio'] <= 0:
                    errors.append(f"{grade}级信号的最小盈亏比必须 > 0")
        
        # 验证绩效评估标准
        if self.params['performance_metrics']['min_win_rate'] >= self.params['performance_metrics']['good_win_rate']:
            errors.append("最低胜率必须 < 优秀胜率")
        
        return errors
    
    def get_version(self) -> str:
        """获取参数版本"""
        return self.version
    
    def get_last_loaded(self) -> datetime:
        """获取最后加载时间"""
        return self.last_loaded
    
    def reload(self):
        """重新加载参数（热加载）- v5.3 只使用 Python 配置"""
        logger.info("重新加载策略参数...")
        self._load_default_params()
        # v5.3: 已禁用 JSON 配置加载
        # if os.path.exists(self.config_file):
        #     self.load_from_file()
        logger.info("策略参数重新加载完成（v5.3 - 只使用 Python 配置）")


# 全局参数实例（单例模式）
_global_params: Optional[StrategyParams] = None


def get_params(config_file: str = None, force_reload: bool = False) -> StrategyParams:
    """
    获取全局策略参数实例（单例模式）
    
    Args:
        config_file: 配置文件路径（可选）
        force_reload: 是否强制重新加载
    
    Returns:
        StrategyParams 实例
    """
    global _global_params
    
    if _global_params is None or force_reload:
        _global_params = StrategyParams(config_file)
    
    return _global_params


# 便捷函数
def get_param(key_path: str, default: Any = None) -> Any:
    """获取参数值的便捷函数"""
    return get_params().get(key_path, default)


def set_param(key_path: str, value: Any):
    """设置参数值的便捷函数"""
    get_params().set(key_path, value)


def validate_params() -> List[str]:
    """验证参数有效性"""
    return get_params().validate()


def reload_params():
    """重新加载参数"""
    get_params().reload()
