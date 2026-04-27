#!/usr/bin/env python3
"""
信号检测核心模块

功能：
1. 检测交易信号
2. 判定信号等级
3. 计算价格水平
4. 计算仓位参数
5. 组装信号字典
"""

import logging
from datetime import datetime
from decimal import Decimal
from typing import Dict, Any, List, Optional, Tuple
from config.strategy_params import StrategyParams, get_params
from ..data import MarketDataFetcher, get_data_fetcher
from ..scoring import get_scoring_engine
from .validator import SignalValidator
from .filter import SignalFilter

logger = logging.getLogger(__name__)


class SignalDetector:
    """信号检测类"""

    def __init__(self, params: StrategyParams = None, data_fetcher: MarketDataFetcher = None):
        """
        初始化信号检测器

        Args:
            params: 策略参数
            data_fetcher: 数据获取器
        """
        self.params = params or get_params()
        self.data_fetcher = data_fetcher or get_data_fetcher()
        self.scoring_engine = get_scoring_engine()  # v5.5: 新增评分引擎

        # 初始化验证器和过滤器
        self.validator = SignalValidator(self.params)
        self.filter = SignalFilter(self.params)

    def detect_signals(self, symbols: List[str] = None) -> List[Dict[str, Any]]:
        """
        检测所有交易对的交易信号

        Args:
            symbols: 交易对列表，默认 ['BTCUSDT', 'ETHUSDT', 'BNBUSDT']

        Returns:
            信号列表
        """
        if symbols is None:
            symbols = ['BTCUSDT', 'ETHUSDT', 'BNBUSDT']

        logger.info(f"开始检测 {len(symbols)} 个交易对的交易信号")

        # 获取行情数据
        market_data = self.data_fetcher.fetch_market_data(symbols)

        signals = []
        for symbol in symbols:
            try:
                data = market_data.get(symbol)
                if not data:
                    logger.warning(f"无法获取 {symbol} 的行情数据，跳过")
                    continue

                # 检测单个交易对的信号
                signal = self._detect_single_signal(symbol, data)

                if signal:
                    signals.append(signal)

            except Exception as e:
                logger.error(f"检测 {symbol} 信号失败：{str(e)}")
                continue

        logger.info(f"检测到 {len(signals)} 个有效信号")
        return signals

    def _detect_single_signal(self, symbol: str, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        检测单个交易对的信号

        Args:
            symbol: 交易对
            data: 行情数据

        Returns:
            信号字典，如果没有有效信号则返回 None
        """
        logger.info(f"{'='*60}")
        logger.info(f"{symbol}: 开始信号检测")
        logger.info(f"{symbol}: last_price={data.get('last_price')}")

        # 1. 验证信号
        is_valid, reason = self.validator.validate_signal(data)
        logger.info(f"{symbol}: 验证结果={is_valid}, 原因={reason}")
        if not is_valid:
            logger.info(f"{symbol}: 信号验证失败：{reason}")
            return None

        # 2. 趋势过滤和方向判断
        direction = self.filter.determine_trend_direction(data)
        logger.info(f"{symbol}: 趋势方向 direction={direction}")

        if direction == 0:
            # 趋势不明朗
            logger.info(f"{symbol}: 趋势不明朗，无信号")
            return None

        # 3. 信号等级判定
        grade, score = self._determine_signal_grade(symbol, data, direction)
        logger.info(f"{symbol}: 信号等级 grade={grade}, score={score}")

        if grade is None:
            logger.info(f"{symbol}: 未达到有效信号等级")
            return None

        # 4. 应用过滤器
        passed, reason = self.filter.apply_all_filters(data, direction, grade)
        if not passed:
            logger.info(f"{symbol}: 过滤器未通过：{reason}")
            return None

        # 5. 计算入场价、止损价、止盈价
        logger.info(f"{symbol}: 开始计算价格水平...")
        entry_price, stop_loss, take_profits = self._calculate_price_levels(
            symbol, data, direction, grade
        )
        logger.info(f"{symbol}: entry_price={entry_price}, stop_loss={stop_loss}, take_profits={take_profits}")

        if entry_price is None:
            logger.warning(f"{symbol}: 无法计算价格水平")
            return None

        # 6. 计算仓位参数
        logger.info(f"{symbol}: 开始计算仓位参数...")
        position_params = self._calculate_position_params(
            symbol, entry_price, stop_loss, grade, direction
        )
        logger.info(f"{symbol}: position_params={position_params}")

        # 7. 组装信号
        logger.info(f"{symbol}: 开始组装信号...")
        signal = self._build_signal(
            symbol=symbol,
            direction=direction,
            grade=grade,
            score=score,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profits=take_profits,
            position_params=position_params
        )

        return signal

    def _determine_signal_grade(self, symbol: str, data: Dict[str, Any], direction: int) -> Tuple[Optional[str], int]:
        """
        判定信号等级（v5.5: 使用新的评分系统）

        Args:
            symbol: 交易对
            data: 行情数据
            direction: 方向（1=多，-1=空）

        Returns:
            (信号等级，推荐度分数)，如果没有有效信号则返回 (None, 0)
        """
        try:
            # v5.5: 使用新的评分系统
            score_result = self.scoring_engine.score(symbol, data)

            if score_result['grade'] is None:
                # 分数低于 C 级阈值，过滤
                logger.info(f"{symbol}: 评分 {score_result['score']} 分，低于 C 级阈值，过滤")
                # 输出详细的评分维度
                if 'breakdown' in score_result:
                    breakdown = score_result['breakdown']
                    logger.info(f"{symbol}: 评分详情 - trend_strength={breakdown.get('trend_strength', 0):.1f}, trend_consistency={breakdown.get('trend_consistency', 0):.1f}, pattern={breakdown.get('pattern', 0):.1f}, volume={breakdown.get('volume', 0):.1f}, momentum={breakdown.get('momentum', 0):.1f}, risk={breakdown.get('risk', 0):.1f}")
                return None, 0

            # 返回等级和分数
            logger.info(f"{symbol}: 新评分系统结果 - {score_result['grade']}级，{score_result['score']}分")
            # 输出详细的评分维度
            if 'breakdown' in score_result:
                breakdown = score_result['breakdown']
                logger.info(f"{symbol}: 评分详情 - trend_strength={breakdown.get('trend_strength', 0):.1f}, trend_consistency={breakdown.get('trend_consistency', 0):.1f}, pattern={breakdown.get('pattern', 0):.1f}, volume={breakdown.get('volume', 0):.1f}, momentum={breakdown.get('momentum', 0):.1f}, risk={breakdown.get('risk', 0):.1f}")
            return score_result['grade'], score_result['score']

        except Exception as e:
            logger.error(f"{symbol}: 新评分系统失败：{e}，使用旧逻辑")
            # 回退到旧逻辑
            return self._legacy_score(symbol, data, direction)

    def _legacy_score(self, symbol: str, data: Dict[str, Any], direction: int) -> Tuple[Optional[str], int]:
        """
        旧的评分逻辑（回退方案）

        Args:
            symbol: 交易对
            data: 行情数据
            direction: 方向

        Returns:
            (信号等级，推荐度分数)
        """
        indicators = data.get('indicators', {})

        # 检查多时间框架共振
        has_daily = '1d' in indicators
        has_4h = '4h' in indicators
        has_1h = '1h' in indicators

        if not (has_daily and has_4h and has_1h):
            logger.warning(f"{symbol}: 时间框架数据不完整")
            return None, 0

        # 简化版 S 级判定：多时间框架共振
        if has_daily and has_4h and has_1h:
            # S 级信号
            return 'S', 85

        # A 级信号
        if has_4h and has_1h:
            return 'A', 75

        # 无有效信号
        return None, 0

    def _calculate_price_levels(self, symbol: str, data: Dict[str, Any],
                                direction: int, grade: str) -> Tuple[Optional[Decimal], Optional[Decimal], List[Dict]]:
        """
        计算入场价、止损价、止盈价（第三、五章）

        Args:
            symbol: 交易对
            data: 行情数据
            direction: 方向
            grade: 信号等级

        Returns:
            (入场价，止损价，止盈价列表)
        """
        indicators = data.get('indicators', {})
        current_price = data.get('last_price')

        if current_price is None:
            return None, None, []

        # 确保 entry_price 是 Decimal 类型
        if isinstance(current_price, Decimal):
            entry_price = current_price
        else:
            entry_price = Decimal(str(current_price))

        # 计算止损幅度（基于 ATR）- v6.13.3 优化
        atr = indicators.get('1h', {}).get('atr14')
        if atr:
            # v6.13.3: 使用 ATR * 1.5 作为止损基准，更科学地反映市场波动
            # 修复类型错误：将 float 转换为 Decimal
            atr_decimal = Decimal(str(atr)) if not isinstance(atr, Decimal) else atr
            stop_loss_pct = (atr_decimal * Decimal('1.5')) / entry_price
            # 限制止损幅度在合理范围内（v6.13.3: 2%-4%）
            # 确保 min_stop 和 max_stop 是 Decimal 类型
            min_stop_raw = self.params.get('position_sizing.min_stop_loss_pct', '0.02')
            max_stop_raw = self.params.get('position_sizing.max_stop_loss_pct', '0.04')
            min_stop = Decimal(str(min_stop_raw)) if not isinstance(min_stop_raw, Decimal) else min_stop_raw
            max_stop = Decimal(str(max_stop_raw)) if not isinstance(max_stop_raw, Decimal) else max_stop_raw
            stop_loss_pct = max(min_stop, min(stop_loss_pct, max_stop))
        else:
            stop_loss_pct = Decimal('0.03')  # v6.13.3: 默认 3%

        # 计算止损价
        if direction == 1:  # 多头
            stop_loss = entry_price * (1 - stop_loss_pct)
        else:  # 空头
            stop_loss = entry_price * (1 + stop_loss_pct)

        # 计算止盈价（基于 R 值）
        r_value = abs(entry_price - stop_loss)

        tp_config = self.params.get('risk_management.take_profit_levels', {})
        # 确保所有值都是 Decimal 类型
        tp1_mult_raw = tp_config.get('tp1_multiplier', '2.5')
        tp2_mult_raw = tp_config.get('tp2_multiplier', '4.0')
        tp1_mult = Decimal(str(tp1_mult_raw)) if not isinstance(tp1_mult_raw, Decimal) else tp1_mult_raw
        tp2_mult = Decimal(str(tp2_mult_raw)) if not isinstance(tp2_mult_raw, Decimal) else tp2_mult_raw

        take_profits = []

        if direction == 1:  # 多头
            tp1_price = entry_price + r_value * tp1_mult
            tp2_price = entry_price + r_value * tp2_mult
        else:  # 空头
            tp1_price = entry_price - r_value * tp1_mult
            tp2_price = entry_price - r_value * tp2_mult

        # 确保 ratio 值也是 Decimal 类型
        tp1_ratio_raw = tp_config.get('tp1_ratio', '0.25')
        tp2_ratio_raw = tp_config.get('tp2_ratio', '0.25')
        tp3_ratio_raw = tp_config.get('tp3_ratio', '0.50')
        tp1_ratio = Decimal(str(tp1_ratio_raw)) if not isinstance(tp1_ratio_raw, Decimal) else tp1_ratio_raw
        tp2_ratio = Decimal(str(tp2_ratio_raw)) if not isinstance(tp2_ratio_raw, Decimal) else tp2_ratio_raw
        tp3_ratio = Decimal(str(tp3_ratio_raw)) if not isinstance(tp3_ratio_raw, Decimal) else tp3_ratio_raw

        take_profits = [
            {'level': 'TP1', 'price': tp1_price, 'ratio': tp1_ratio},
            {'level': 'TP2', 'price': tp2_price, 'ratio': tp2_ratio},
            {'level': 'TP3', 'price': None, 'ratio': tp3_ratio},  # TP3 使用移动止损
        ]

        return entry_price, stop_loss, take_profits

    def _calculate_position_params(self, symbol: str, entry_price: Decimal,
                                   stop_loss: Decimal, grade: str, direction: int) -> Dict[str, Any]:
        """
        计算仓位参数（第四章）

        Args:
            symbol: 交易对
            entry_price: 入场价
            stop_loss: 止损价
            grade: 信号等级
            direction: 方向

        Returns:
            仓位参数字典
        """
        # 计算止损百分比
        stop_loss_pct = abs(entry_price - stop_loss) / entry_price

        # 名义价值 = 风险金额 / 止损百分比
        # 确保 risk_amount 是 Decimal 类型
        risk_amount_raw = self.params.get('position_sizing.risk_amount', '10')
        risk_amount = Decimal(str(risk_amount_raw)) if not isinstance(risk_amount_raw, Decimal) else risk_amount_raw
        notional_value = risk_amount / stop_loss_pct

        # 统一使用固定杠杆倍数（方案 4：保持分级仓位，统一杠杆）
        # 所有等级都使用 5 倍杠杆，通过仓位比例控制风险
        fixed_leverage = Decimal('5')

        # 保证金 = 名义价值 / 杠杆
        margin = notional_value / fixed_leverage

        # 限制保证金不超过单仓上限
        max_margin_raw = self.params.get('account.single_position_margin', '30')
        max_margin = Decimal(str(max_margin_raw)) if not isinstance(max_margin_raw, Decimal) else max_margin_raw
        margin = min(margin, max_margin)

        # 合约数量 = 名义价值 / 入场价
        quantity = notional_value / entry_price

        # 确保 total_capital 是 Decimal 类型
        total_capital_raw = self.params.get('account.total_capital', '500')
        total_capital = Decimal(str(total_capital_raw)) if not isinstance(total_capital_raw, Decimal) else total_capital_raw

        return {
            'notional_value': notional_value,
            'margin': margin,
            'leverage': int(fixed_leverage),
            'quantity': quantity,
            'risk_ratio': risk_amount / total_capital,
        }

    def _build_signal(self, symbol: str, direction: int, grade: str, score: int,
                     entry_price: Decimal, stop_loss: Decimal,
                     take_profits: List[Dict], position_params: Dict[str, Any]) -> Dict[str, Any]:
        """
        组装信号字典（符合第三章 JSON 示例格式，v5.5 新增评分字段）

        Args:
            symbol: 交易对
            direction: 方向
            grade: 信号等级
            score: 推荐度
            entry_price: 入场价
            stop_loss: 止损价
            take_profits: 止盈价列表
            position_params: 仓位参数

        Returns:
            信号字典
        """
        # 方向转换
        direction_str = "多" if direction == 1 else "空"

        # 止盈设置格式化
        tp_settings = {}
        for tp in take_profits:
            tp_key = tp['level']
            price_val = tp['price']
            # 确保价格是 float
            price_float = float(price_val) if price_val is not None else '移动止损'
            # 确保 ratio 是 Decimal，然后转换为 float 用于格式化
            ratio_val = tp['ratio']
            ratio_float = float(ratio_val) if isinstance(ratio_val, Decimal) else ratio_val
            tp_settings[tp_key] = {
                '价格': price_float,
                '仓位比例': f"{ratio_float * 100:.0f}%"
            }

        # v5.5: 获取评分引擎的评分结果（如果可用）
        score_detail = {}
        position_ratio = 0.0
        try:
            # 尝试从评分引擎获取详细评分
            if hasattr(self, 'scoring_engine'):
                # 注意：这里简化处理，实际应该在调用 _determine_signal_grade 时保存评分结果
                position_ratio = self.scoring_engine.calculate_position_ratio(score, grade)
        except Exception as e:
            logger.warning(f"获取评分详情失败：{e}")

        # 确保 risk_ratio 是 float 用于格式化
        risk_ratio_val = position_params['risk_ratio']
        risk_ratio_float = float(risk_ratio_val) if isinstance(risk_ratio_val, Decimal) else risk_ratio_val

        signal = {
            '币种': symbol,
            '开仓方向': direction_str,
            '开仓推荐度': score,  # 保留用于向后兼容
            '信号等级': grade,
            '开仓价': float(entry_price),
            '强平价': None,  # TODO: 计算强平价
            '止损价': float(stop_loss),
            '止盈设置': tp_settings,
            '保证金': float(position_params['margin']),
            '实际杠杆': position_params['leverage'],
            '风险占比': f"{risk_ratio_float * 100:.1f}%",
            '通过检查清单': True,
            '备注': f"符合 500U 阶段一交易规则（{grade}级信号）",
            # v5.5 新增字段
            'score': score,  # 总分
            'score_detail': score_detail,  # 得分明细（后续完善）
            'suggested_position_ratio': position_ratio,  # 建议仓位系数
        }

        return signal


# 全局实例
_global_detector: Optional[SignalDetector] = None


def get_signal_detector(params: StrategyParams = None) -> SignalDetector:
    """获取信号检测器实例（单例模式）"""
    global _global_detector
    if _global_detector is None:
        _global_detector = SignalDetector(params)
    return _global_detector
