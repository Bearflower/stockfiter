"""
风险管理器 - 网格交易系统的核心风控模块

负责：
- 硬止损保护（-8%总资金亏损阈值）
- 移动止盈机制（15%启动，5%回撤锁定利润）
- 紧急暂停协议（网格突破、API异常、断网等）
- 滑点保护机制
- 动态仓位调整（波动率平价）
- 回撤监控与仓位限制

文档参考: docs/strategy/风险管理策略.md
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class RiskAction(str, Enum):
    """风险动作枚举"""
    CONTINUE = "continue"                          # 继续运行，风险正常
    TRAILING_PROFIT_ACTIVATED = "trailing_activated"  # 移动止盈已激活
    UPDATE_STOP_PRICE = "update_stop_price"        # 建议更新止盈价格
    TRAILING_PROFIT_TRIGGERED = "trailing_triggered"  # 触发移动止盈
    HARD_STOP_LOSS = "hard_stop_loss"              # 触发硬止损
    EMERGENCY_PAUSE = "emergency_pause"            # 触发紧急暂停
    DRAWDOWN_WARNING = "drawdown_warning"          # 回撤警告
    POSITION_LIMIT_WARNING = "position_limit_warning"  # 仓位超限警告


@dataclass
class RiskResult:
    """风险评估结果"""
    action: RiskAction                    # 建议风险动作
    risk_level: str                       # 风险级别：low/medium/high/critical
    is_safe_to_trade: bool               # 是否可以继续交易
    message: str                         # 状态描述
    
    # 硬止损相关
    hard_stop_triggered: bool = False    # 硬止损是否触发
    current_pnl_percent: float = 0.0     # 当前盈亏百分比
    distance_to_stop: float = 0.0        # 距离止损的百分比
    
    # 移动止盈相关
    trailing_activated: bool = False     # 移动止盈是否已激活
    trailing_triggered: bool = False     # 移动止盈是否触发
    peak_price: Optional[float] = None   # 追踪的最高价
    new_stop_price: Optional[float] = None  # 新的止盈价格
    
    # 紧急暂停相关
    emergency_paused: bool = False       # 是否紧急暂停
    pause_reason: Optional[str] = None   # 暂停原因
    
    # 动态仓位相关
    suggested_margin: Optional[float] = None  # 建议保证金
    position_ratio: Optional[float] = None    # 建议仓位比例
    
    timestamp: datetime = field(default_factory=datetime.now)
    
    def to_dict(self) -> Dict:
        """转换为字典格式"""
        return {
            'action': self.action.value,
            'risk_level': self.risk_level,
            'is_safe_to_trade': self.is_safe_to_trade,
            'message': self.message,
            'hard_stop_triggered': self.hard_stop_triggered,
            'current_pnl_percent': self.current_pnl_percent,
            'distance_to_stop': self.distance_to_stop,
            'trailing_activated': self.trailing_activated,
            'trailing_triggered': self.trailing_triggered,
            'peak_price': self.peak_price,
            'new_stop_price': self.new_stop_price,
            'emergency_paused': self.emergency_paused,
            'pause_reason': self.pause_reason,
            'suggested_margin': self.suggested_margin,
            'position_ratio': self.position_ratio,
            'timestamp': self.timestamp
        }


class RiskManager:
    """风险管理器 - 统一风控中枢
    
    整合硬止损、移动止盈、紧急暂停、滑点保护、动态仓位等风控机制。
    通过 check_conditions() 方法进行综合风险评估，返回 RiskAction。
    """
    
    def __init__(
        self,
        initial_margin: float = 500.0,
        leverage: int = 10,
        # 硬止损参数
        hard_stop_loss_ratio: float = -0.08,
        # 移动止盈参数
        trailing_profit_start: float = 0.15,
        trailing_profit_retrace: float = 0.05,
        # 紧急暂停参数
        max_api_errors: int = 3,
        max_api_delay: float = 3.0,
        max_grid_breakthrough: int = 3,
        breakthrough_window: int = 300,
        # 滑点保护参数
        slippage_tolerance: float = 0.005,
        # 动态仓位参数
        atr_coefficient: float = 1.0,
        base_atr: Optional[float] = None,
        # 回撤与仓位限制
        max_drawdown: float = 0.2,
        max_position_pct: float = 0.3,
    ):
        """
        初始化风险管理器
        
        Args:
            initial_margin: 初始保证金（USDT）
            leverage: 杠杆倍数
            hard_stop_loss_ratio: 硬止损阈值（默认-8%）
            trailing_profit_start: 移动止盈启动阈值（默认15%）
            trailing_profit_retrace: 移动止盈回撤比例（默认5%）
            max_api_errors: 最大API连续错误次数
            max_api_delay: 最大API延迟（秒）
            max_grid_breakthrough: 5分钟内最大网格突破层数
            breakthrough_window: 突破计数时间窗口（秒）
            slippage_tolerance: 滑点容忍度（默认0.5%）
            atr_coefficient: ATR波动率系数
            base_atr: 基准ATR值（用于动态仓位计算）
            max_drawdown: 最大回撤比例（默认20%）
            max_position_pct: 单仓位最大占比（默认30%）
        """
        # 基础配置
        self.initial_margin = initial_margin
        self.leverage = leverage
        self.base_notional = initial_margin * leverage
        
        # 硬止损配置
        self.hard_stop_loss_ratio = hard_stop_loss_ratio
        
        # 移动止盈配置
        self.trailing_profit_start = trailing_profit_start
        self.trailing_profit_retrace = trailing_profit_retrace
        
        # 紧急暂停配置
        self.max_api_errors = max_api_errors
        self.max_api_delay = max_api_delay
        self.max_grid_breakthrough = max_grid_breakthrough
        self.breakthrough_window = breakthrough_window
        
        # 滑点保护配置
        self.slippage_tolerance = slippage_tolerance
        
        # 动态仓位配置
        self.atr_coefficient = atr_coefficient
        self.base_atr = base_atr
        
        # 回撤与仓位限制
        self.max_drawdown = max_drawdown
        self.max_position_pct = max_position_pct
        
        # --- 内部状态 ---
        
        # 移动止盈状态
        self._peak_price: Optional[float] = None
        self._peak_pnl_percent: Optional[float] = None
        self._trailing_active: bool = False
        self._trailing_triggered: bool = False
        self._current_stop_price: Optional[float] = None
        
        # 紧急暂停状态
        self._api_error_count: int = 0
        self._grid_breakthroughs: List[datetime] = []
        self._emergency_paused: bool = False
        self._pause_reason: Optional[str] = None
        
        # 峰值权益（用于回撤计算）
        self._peak_equity: float = initial_margin
    
    def check_conditions(
        self,
        current_pnl_percent: float,
        current_price: float,
        current_equity: float,
        market_data: Optional[Dict] = None,
        system_data: Optional[Dict] = None,
    ) -> RiskResult:
        """
        综合风险评估（主方法）
        
        按优先级依次检查：紧急暂停 -> 硬止损 -> 移动止盈 -> 回撤/仓位 -> 动态仓位
        
        Args:
            current_pnl_percent: 当前总盈亏百分比（已实现+未实现）
            current_price: 当前标记价格
            current_equity: 当前总权益
            market_data: 市场数据（可选），包含：
                - atr_smooth: 平滑ATR值
                - market_state: 市场状态（上升趋势/下降趋势/震荡）
            system_data: 系统数据（可选），包含：
                - api_error: 是否发生API错误
                - api_delay: API延迟时间（秒）
                - grid_breakthrough: 突破网格层数
                - timestamp: 时间戳
        
        Returns:
            RiskResult: 风险评估结果，包含建议动作和详细信息
        """
        market_data = market_data or {}
        system_data = system_data or {}
        
        # 更新峰值权益
        if current_equity > self._peak_equity:
            self._peak_equity = current_equity
        
        # === 1. 检查紧急暂停（最高优先级）===
        self._update_emergency_status(system_data)
        if self._emergency_paused:
            return RiskResult(
                action=RiskAction.EMERGENCY_PAUSE,
                risk_level="critical",
                is_safe_to_trade=False,
                message=f"紧急暂停：{self._pause_reason}",
                emergency_paused=True,
                pause_reason=self._pause_reason,
            )
        
        # === 2. 检查硬止损 ===
        hard_stop_result = self._check_hard_stop(current_pnl_percent)
        if hard_stop_result['triggered']:
            return RiskResult(
                action=RiskAction.HARD_STOP_LOSS,
                risk_level="critical",
                is_safe_to_trade=False,
                message=f"硬止损触发！当前亏损：{current_pnl_percent*100:.2f}%",
                hard_stop_triggered=True,
                current_pnl_percent=current_pnl_percent,
                distance_to_stop=hard_stop_result['distance'],
            )
        
        # === 3. 检查移动止盈 ===
        trailing_result = self._check_trailing_profit(current_pnl_percent, current_price, market_data)
        if trailing_result['triggered']:
            return RiskResult(
                action=RiskAction.TRAILING_PROFIT_TRIGGERED,
                risk_level="medium",
                is_safe_to_trade=True,
                message="移动止盈触发！建议平仓锁定利润",
                trailing_triggered=True,
                peak_price=self._peak_price,
                new_stop_price=trailing_result.get('stop_price'),
                current_pnl_percent=current_pnl_percent,
            )
        
        if trailing_result['activated']:
            return RiskResult(
                action=RiskAction.TRAILING_PROFIT_ACTIVATED,
                risk_level="low",
                is_safe_to_trade=True,
                message=f"移动止盈已激活，峰值价格：{self._peak_price}",
                trailing_activated=True,
                peak_price=self._peak_price,
                new_stop_price=trailing_result.get('stop_price'),
                current_pnl_percent=current_pnl_percent,
            )
        
        if trailing_result.get('update_stop_price'):
            return RiskResult(
                action=RiskAction.UPDATE_STOP_PRICE,
                risk_level="low",
                is_safe_to_trade=True,
                message="建议更新止盈价格",
                trailing_activated=True,
                peak_price=self._peak_price,
                new_stop_price=trailing_result.get('stop_price'),
                current_pnl_percent=current_pnl_percent,
            )
        
        # === 4. 检查回撤与仓位限制 ===
        drawdown_warning = self._check_drawdown(current_equity)
        if drawdown_warning:
            return RiskResult(
                action=RiskAction.DRAWDOWN_WARNING,
                risk_level="high",
                is_safe_to_trade=True,
                message=f"接近最大回撤线！当前回撤：{drawdown_warning*100:.2f}%",
                current_pnl_percent=current_pnl_percent,
            )
        
        # === 5. 动态仓位建议 ===
        sizing_info = self._calculate_dynamic_position(current_price, market_data)
        
        # 综合正常状态
        messages = ["风险状态正常"]
        if sizing_info:
            messages.append(f"建议保证金：{sizing_info.get('suggested_margin', 0):.0f} USDT")
        
        return RiskResult(
            action=RiskAction.CONTINUE,
            risk_level="low",
            is_safe_to_trade=True,
            message="; ".join(messages),
            current_pnl_percent=current_pnl_percent,
            suggested_margin=sizing_info.get('suggested_margin') if sizing_info else None,
            position_ratio=sizing_info.get('position_ratio') if sizing_info else None,
        )
    
    def _check_hard_stop(self, current_pnl_percent: float) -> Dict:
        """
        检查硬止损
        
        当总亏损达到初始保证金的 8% 时触发，强制终止网格并平仓。
        
        Args:
            current_pnl_percent: 当前总盈亏百分比
        
        Returns:
            {
                'triggered': 是否触发,
                'distance': 距离止损的百分比
            }
        """
        distance = current_pnl_percent - self.hard_stop_loss_ratio
        
        if current_pnl_percent <= self.hard_stop_loss_ratio:
            logger.error(
                f"硬止损触发！当前盈亏：{current_pnl_percent*100:.2f}%, "
                f"阈值：{self.hard_stop_loss_ratio*100:.2f}%"
            )
            return {'triggered': True, 'distance': 0.0}
        
        return {'triggered': False, 'distance': distance}
    
    def _check_trailing_profit(
        self,
        current_pnl_percent: float,
        current_price: float,
        market_data: Dict,
    ) -> Dict:
        """
        检查移动止盈状态
        
        工作原理：
        1. 当总盈利 >= 15% 时，激活移动止盈，记录当前价格为峰值
        2. 持续追踪最高价格
        3. 当从峰值回撤 >= 5% 时，触发止盈
        
        Args:
            current_pnl_percent: 当前总盈亏百分比
            current_price: 当前标记价格
            market_data: 市场数据（包含市场状态）
        
        Returns:
            {
                'triggered': 是否触发止盈,
                'activated': 是否新激活,
                'update_stop_price': 是否需要更新止盈价,
                'stop_price': 建议止盈价格
            }
        """
        result = {
            'triggered': False,
            'activated': False,
            'update_stop_price': False,
            'stop_price': None,
        }
        
        # 如果已经触发过，不再处理
        if self._trailing_triggered:
            return result
        
        # 1. 检查是否达到启动线
        if not self._trailing_active and current_pnl_percent >= self.trailing_profit_start:
            self._trailing_active = True
            self._peak_price = current_price
            self._peak_pnl_percent = current_pnl_percent
            result['activated'] = True
            logger.info(
                f"移动止盈激活！盈亏：{current_pnl_percent*100:.2f}%, "
                f"峰值价格：{current_price}"
            )
        
        # 2. 如果已激活，追踪峰值并计算止盈价
        if self._trailing_active:
            market_state = market_data.get('market_state', '震荡')
            
            if market_state == '上升趋势':
                # 上升趋势：追踪最高价
                if self._peak_price is None or current_price > self._peak_price:
                    self._peak_price = current_price
                    self._peak_pnl_percent = current_pnl_percent
                    logger.debug(f"更新上升峰值：{self._peak_price}")
                
                # 计算追踪止盈价 = peak × (1 - 回撤比例)
                stop_price = self._peak_price * (1 - self.trailing_profit_retrace)
                self._current_stop_price = stop_price
                result['stop_price'] = stop_price
                
                # 检查是否触发
                if current_price <= stop_price:
                    self._trailing_triggered = True
                    result['triggered'] = True
                    logger.info(
                        f"移动止盈触发！峰值：{self._peak_price}, "
                        f"当前价：{current_price}, 止盈价：{stop_price}"
                    )
                else:
                    # 需要更新止盈价
                    result['update_stop_price'] = True
            
            elif market_state == '下降趋势':
                # 下降趋势：追踪最低价（做空场景）
                if self._peak_price is None or current_price < self._peak_price:
                    self._peak_price = current_price
                    self._peak_pnl_percent = current_pnl_percent
                    logger.debug(f"更新下降谷值：{self._peak_price}")
                
                # 计算追踪止盈价 = peak × (1 + 回撤比例)
                stop_price = self._peak_price * (1 + self.trailing_profit_retrace)
                self._current_stop_price = stop_price
                result['stop_price'] = stop_price
                
                # 检查是否触发
                if current_price >= stop_price:
                    self._trailing_triggered = True
                    result['triggered'] = True
                    logger.info(
                        f"移动止盈触发（空头）！谷值：{self._peak_price}, "
                        f"当前价：{current_price}, 止盈价：{stop_price}"
                    )
                else:
                    result['update_stop_price'] = True
            else:
                # 震荡市场：使用原有逻辑，基于价格回撤
                if self._peak_price is not None:
                    retrace = (self._peak_price - current_price) / self._peak_price
                    if retrace >= self.trailing_profit_retrace:
                        self._trailing_triggered = True
                        result['triggered'] = True
                        logger.info(
                            f"移动止盈触发！回撤：{retrace*100:.2f}%"
                        )
                    elif current_price > self._peak_price:
                        self._peak_price = current_price
        
        return result
    
    def _update_emergency_status(self, system_data: Dict) -> None:
        """
        更新紧急暂停状态
        
        检测条件：
        - API连续错误 >= max_api_errors
        - API延迟 > max_api_delay
        - 5分钟内网格突破 >= max_grid_breakthrough 层
        
        Args:
            system_data: 系统数据
        """
        if self._emergency_paused:
            return
        
        # 1. 检查API错误
        if system_data.get('api_error'):
            self._api_error_count += 1
            if self._api_error_count >= self.max_api_errors:
                self._trigger_emergency_pause(f"API连续报错 {self._api_error_count} 次")
                return
        
        # 2. 检查API延迟
        api_delay = system_data.get('api_delay')
        if api_delay and api_delay > self.max_api_delay:
            self._trigger_emergency_pause(f"API延迟过大：{api_delay:.1f}秒")
            return
        
        # 3. 检查网格突破
        breakthrough = system_data.get('grid_breakthrough')
        if breakthrough:
            now = system_data.get('timestamp', datetime.now())
            if isinstance(now, (int, float)):
                now = datetime.fromtimestamp(now)
            
            # 记录突破时间
            self._grid_breakthroughs.append(now)
            
            # 清理超出时间窗口的记录
            cutoff = now.timestamp() - self.breakthrough_window
            self._grid_breakthroughs = [
                t for t in self._grid_breakthroughs
                if isinstance(t, (int, float)) and t > cutoff or
                   isinstance(t, datetime) and t.timestamp() > cutoff
            ]
            
            if len(self._grid_breakthroughs) >= self.max_grid_breakthrough:
                self._trigger_emergency_pause(
                    f"{self.breakthrough_window}秒内突破 {len(self._grid_breakthroughs)} 层网格"
                )
                return
    
    def _trigger_emergency_pause(self, reason: str) -> None:
        """
        触发紧急暂停
        
        Args:
            reason: 暂停原因
        """
        if not self._emergency_paused:
            self._emergency_paused = True
            self._pause_reason = reason
            logger.critical(f"紧急暂停触发！原因：{reason}")
            # TODO: 集成飞书通知推送
            # self._send_alert(reason)
    
    def _check_drawdown(self, current_equity: float) -> Optional[float]:
        """
        检查回撤是否接近阈值
        
        Args:
            current_equity: 当前总权益
        
        Returns:
            当前回撤比例，如果接近阈值返回警告，否则返回 None
        """
        if self._peak_equity <= 0:
            return None
        
        drawdown = (self._peak_equity - current_equity) / self._peak_equity
        
        # 回撤超过最大回撤的 80% 时发出警告
        warning_threshold = self.max_drawdown * 0.8
        if drawdown >= warning_threshold:
            return drawdown
        
        return None
    
    def _calculate_dynamic_position(
        self,
        current_price: float,
        market_data: Dict,
    ) -> Optional[Dict]:
        """
        计算动态仓位（基于波动率平价）
        
        核心思想：固定名义本金，根据波动率调整实际开仓保证金，
        确保每单位风险恒定。
        
        公式：动态仓位 = 固定名义本金 / (ATR × 系数)
        
        Args:
            current_price: 当前价格
            market_data: 市场数据（需包含 atr_smooth）
        
        Returns:
            {
                'suggested_margin': 建议保证金,
                'notional': 名义价值,
                'risk_unit': 单位风险,
                'position_ratio': 仓位比例,
                'action': 仓位调整建议
            }
        """
        atr_smooth = market_data.get('atr_smooth')
        base_atr = self.base_atr
        
        if not atr_smooth or not base_atr:
            return None
        
        # 计算 ATR 相对基准的比率
        atr_ratio = atr_smooth / base_atr if base_atr > 0 else 1.0
        
        # 单位风险（名义价值 × 波动率）
        risk_unit = self.base_notional * (atr_smooth / current_price)
        
        # 建议保证金 = 基础名义本金 / 杠杆
        suggested_margin = self.base_notional / self.leverage
        
        # 根据波动率调整仓位
        if atr_ratio < 0.7:
            action = "增加仓位（波动率低）"
            suggested_margin *= 1.2
        elif atr_ratio < 1.3:
            action = "维持仓位（波动率正常）"
        elif atr_ratio < 1.8:
            action = "减少仓位（波动率升高）"
            suggested_margin *= 0.8
        else:
            action = "准备暂停（波动率极高）"
            suggested_margin *= 0.5
        
        position_ratio = suggested_margin / (current_price / self.leverage)
        
        return {
            'suggested_margin': suggested_margin,
            'notional': self.base_notional,
            'risk_unit': risk_unit,
            'position_ratio': position_ratio,
            'atr_ratio': atr_ratio,
            'action': action,
        }
    
    def check_position_limit(self, position_value: float, total_equity: float) -> bool:
        """
        检查单仓位是否超出限制
        
        Args:
            position_value: 当前仓位价值
            total_equity: 总权益
        
        Returns:
            是否超出仓位限制
        """
        if total_equity <= 0:
            return False
        return (position_value / total_equity) >= self.max_position_pct
    
    def check_slippage(self, expected_price: float, actual_price: float) -> float:
        """
        计算并检查滑点
        
        Args:
            expected_price: 预期成交价格
            actual_price: 实际成交价格
        
        Returns:
            滑点比例
        """
        if expected_price <= 0:
            return 0.0
        
        slippage = abs(actual_price - expected_price) / expected_price
        
        if slippage > self.slippage_tolerance:
            logger.warning(
                f"滑点超限！预期：{expected_price}, 实际：{actual_price}, "
                f"滑点：{slippage*100:.3f}%"
            )
        
        return slippage
    
    def get_trailing_info(self) -> Dict:
        """
        获取移动止盈当前状态信息
        
        Returns:
            移动止盈信息字典
        """
        return {
            'active': self._trailing_active,
            'triggered': self._trailing_triggered,
            'peak_price': self._peak_price,
            'peak_pnl_percent': self._peak_pnl_percent,
            'current_stop_price': self._current_stop_price,
        }
    
    def get_emergency_info(self) -> Dict:
        """
        获取紧急暂停状态信息
        
        Returns:
            紧急暂停信息字典
        """
        return {
            'paused': self._emergency_paused,
            'reason': self._pause_reason,
            'api_error_count': self._api_error_count,
            'breakthrough_count': len(self._grid_breakthroughs),
        }
    
    def reset(self) -> None:
        """
        重置所有风险状态
        
        用于重新开始新一轮网格交易时清除历史状态。
        """
        # 重置移动止盈
        self._peak_price = None
        self._peak_pnl_percent = None
        self._trailing_active = False
        self._trailing_triggered = False
        self._current_stop_price = None
        
        # 重置紧急暂停
        self._api_error_count = 0
        self._grid_breakthroughs.clear()
        self._emergency_paused = False
        self._pause_reason = None
        
        logger.info("风险管理状态已重置")
    
    def is_safe_to_trade(self) -> bool:
        """
        快速判断是否可以继续交易
        
        Returns:
            是否可以交易
        """
        return not self._emergency_paused and not self._trailing_triggered
