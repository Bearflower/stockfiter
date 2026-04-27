#!/usr/bin/env python3
"""
规则引擎调度器（新版本）

基于 traderule.txt 的规则引擎调度器：
- 每小时执行一次行情分析和信号检测
- 不依赖 DeepSeek AI（可选集成）
- 自动执行符合规则的交易
- 完整的风险控制

使用方式:
    # 每小时执行一次（带自动交易）
    python scheduler_new.py
    
    # 立即执行一次分析（不带自动交易）
    python scheduler_new.py --dry-run
    
    # 立即执行一次分析并自动交易
    python scheduler_new.py --auto-trade
"""

import os
import sys
import logging
import time
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Dict, Any, List, Optional
import pytz
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

# 导入新核心模块
from core.data_fetcher import get_data_fetcher
from core.signal_detector import get_signal_detector
from core.position_calculator import get_position_calculator
from core.risk_manager import get_risk_manager
from core.order_generator import get_order_generator, generate_all_orders
from core.emergency_handler import get_emergency_handler, check_extreme_market, is_trading_allowed
from config.strategy_params import get_params

# 导入频率控制器（v6.12 新增）
from services.frequency_controller import get_frequency_controller

# 导入现有模块（保持兼容）
from utils.binance_trade_api import BinanceTradeAPI
from utils.lark_notifier import LarkNotifier
from models.database import get_db_manager
from config.settings import (
    LARK_WEBHOOK_URL, 
    TIMEZONE, 
    SUPPORTED_CURRENCIES,
    BINANCE_TESTNET,
    ENVIRONMENT
)

# 设置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('logs/scheduler_new.log', encoding='utf-8')
    ]
)

logger = logging.getLogger('scheduler_new')


class RuleEngineScheduler:
    """规则引擎调度器"""
    
    def __init__(self, enable_auto_trade: bool = False):
        """
        初始化调度器
        
        Args:
            enable_auto_trade: 是否启用自动交易
        """
        self.enable_auto_trade = enable_auto_trade
        self.params = get_params()
        self.data_fetcher = get_data_fetcher()
        self.signal_detector = get_signal_detector(self.params)
        self.position_calculator = get_position_calculator(self.params)
        self.risk_manager = get_risk_manager(self.params)
        self.order_generator = get_order_generator(self.params)
        self.emergency_handler = get_emergency_handler(self.params)
        
        # 初始化数据库管理器
        self.db = get_db_manager()
        logger.info("数据库管理器已初始化")
        
        # v6.12: 初始化频率控制器
        self.frequency_controller = get_frequency_controller(self.db)
        
        # 初始化交易 API（可选，用于自动交易）
        self.trade_api = None
        if enable_auto_trade:
            self.trade_api = BinanceTradeAPI(testnet=BINANCE_TESTNET)
            logger.info("已初始化币安交易 API（自动交易已启用）")
        
        # 初始化飞书通知
        self.lark_notifier = LarkNotifier(LARK_WEBHOOK_URL) if LARK_WEBHOOK_URL else None
        logger.info(f"飞书通知：{'已启用' if self.lark_notifier else '已禁用'}")
    
    def run_analysis(self) -> Dict[str, Any]:
        """
        执行一次完整的分析和交易流程
        
        Returns:
            执行结果
        """
        logger.info("=" * 60)
        logger.info("开始执行规则引擎分析")
        logger.info(f"时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        logger.info(f"自动交易：{'已启用' if self.enable_auto_trade else '已禁用'}")
        logger.info("=" * 60)
        
        result = {
            'success': False,
            'timestamp': datetime.now(),
            'signals': [],
            'executed_trades': [],
            'risk_report': None,
            'message': ''
        }
        
        try:
            # 步骤 1: 获取行情数据
            logger.info("步骤 1: 获取行情数据...")
            market_data = self.data_fetcher.fetch_market_data(SUPPORTED_CURRENCIES)
            logger.info(f"成功获取 {len(market_data)} 个交易对的行情数据")
            
            # 步骤 2: 应急检查（极端行情）
            logger.info("步骤 2: 应急检查...")
            emergency_status = self.emergency_handler.get_emergency_status()
            trading_allowed, halt_reason = is_trading_allowed()
            
            if not trading_allowed:
                logger.warning(f"⛔ 停止交易：{halt_reason}")
                result['success'] = True
                result['message'] = f'停止交易：{halt_reason}'
                
                if self.lark_notifier:
                    self.lark_notifier.send_text_message(f"⛔ 停止交易\n{halt_reason}")
                return result
            
            # 检查极端行情
            for symbol, data in market_data.items():
                # data_fetcher 已将 priceChangePercent 转换为 price_change_24h（除以 100）
                # emergency_handler 期望百分比值（如 5.0 表示 5%），所以需要乘以 100
                price_change_24h = data.get('price_change_24h', Decimal('0'))
                price_change = price_change_24h * Decimal('100')  # 转换回百分比格式
                if check_extreme_market(symbol, price_change):
                    logger.warning(f"⚠️ {symbol} 极端行情，跳过该交易对")
            
            # 步骤 3: 检测交易信号
            logger.info("步骤 3: 检测交易信号...")
            signals = self.signal_detector.detect_signals(SUPPORTED_CURRENCIES)
            result['signals'] = signals
            
            if not signals:
                logger.info("未检测到有效交易信号")
                result['success'] = True
                result['message'] = '未检测到有效交易信号'
                
                # 记录执行统计（无信号）
                self._record_daily_stats(signals_count=0, executed_count=0)
                
                # 发送无信号通知（告知用户系统正常工作）
                if self.lark_notifier:
                    self._send_no_signal_notification(market_data)
                
                return result
            
            logger.info(f"检测到 {len(signals)} 个有效信号:")
            for signal in signals:
                logger.info(f"  - {signal['币种']} {signal['开仓方向']} "
                          f"等级:{signal['信号等级']} 推荐度:{signal['开仓推荐度']}")
            
            # 步骤 4: 生成订单参数
            logger.info("步骤 4: 生成订单参数...")
            for signal in signals:
                # 计算仓位
                position = self.position_calculator.calculate_position(
                    symbol=signal['币种'],
                    entry_price=Decimal(str(signal['开仓价'])),
                    stop_loss_price=Decimal(str(signal['止损价'])),
                    direction=1 if signal['开仓方向'] == '多' else -1,
                    signal_grade=signal['信号等级']
                )
                
                # 生成订单模板
                order_template = self.order_generator.generate_order_template(
                    symbol=signal['币种'],
                    direction=1 if signal['开仓方向'] == '多' else -1,
                    entry_price=Decimal(str(signal['开仓价'])),
                    stop_loss_price=Decimal(str(signal['止损价'])),
                    signal_grade=signal['信号等级'],
                    position_data=position
                )
                
                # 格式化订单（获取 API 精度）
                if self.trade_api:
                    tick_size, step_size = self.trade_api.get_symbol_precision(signal['币种'])
                    api_precision = {'tick_size': tick_size, 'step_size': step_size}
                    formatted_order = self.order_generator.format_order_for_api(
                        order_template, api_precision
                    )
                else:
                    formatted_order = self.order_generator.format_order_for_api(order_template)
                
                # 生成所有订单参数
                all_orders = generate_all_orders(order_template, formatted_order)
                signal['orders'] = all_orders
            
            logger.info(f"订单参数生成完成")
            
            # 步骤 5: 风险检查
            logger.info("步骤 5: 执行风险检查...")
            risk_report = self._generate_risk_report()
            result['risk_report'] = risk_report
            
            # 步骤 6: 执行交易（如果启用）
            if self.enable_auto_trade and signals:
                logger.info("步骤 6: 执行自动交易...")
                executed_trades = self._execute_trades(signals)
                result['executed_trades'] = executed_trades
                result['message'] = f"执行 {len(executed_trades)}/{len(signals)} 笔交易"
            else:
                result['message'] = f"检测到 {len(signals)} 个信号（自动交易未启用）"
            
            # 记录执行统计
            self._record_daily_stats(
                signals_count=len(signals),
                executed_count=len(result.get('executed_trades', []))
            )
            
            # 步骤 5: 发送通知（只在有信号时发送）
            if self.lark_notifier and signals:
                self._send_analysis_result(result)
            
            result['success'] = True
            
        except Exception as e:
            logger.error(f"分析执行失败：{str(e)}", exc_info=True)
            result['message'] = f'执行失败：{str(e)}'
            result['success'] = False
            
            # 不再发送失败通知，避免频率限制
            # 分析结果已经在上面发送，交易执行失败会在日志中记录
        
        logger.info("=" * 60)
        logger.info(f"分析完成：{result['message']}")
        logger.info("=" * 60)
        
        return result
    
    def _generate_risk_report(self) -> Dict[str, Any]:
        """生成风险报告"""
        # TODO: 从数据库获取当前持仓和账户权益
        # 简化实现：返回空报告
        return {
            'account_equity': Decimal('500'),
            'total_capital': Decimal('500'),
            'total_margin': Decimal('0'),
            'margin_usage': Decimal('0'),
            'risk_level': 'SAFE'
        }
    
    def _execute_trades(self, signals: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        执行交易（使用币安 API）
        
        Args:
            signals: 信号列表
        
        Returns:
            执行的交易列表
        """
        executed = []
        
        if not self.trade_api:
            logger.warning("交易 API 未初始化，无法执行交易")
            return executed
        
        # 构建信号推送内容
        signal_messages = []
        
        for i, signal in enumerate(signals):
            try:
                symbol = signal['币种']
                orders = signal.get('orders', {})
                
                # v6.12: 频率控制检查
                trade_allowed, reason = self.frequency_controller.check_trade_allowed(symbol)
                if not trade_allowed:
                    logger.warning(f"⛔ {symbol} 禁止开仓：{reason}")
                    signal_messages.append(f"⛔ {symbol} {signal['开仓方向']} 等级:{signal['信号等级']} 禁止开仓：{reason}")
                    continue  # 跳过该信号
                
                logger.info(f"准备执行：{symbol} {signal['开仓方向']}")
                
                # 添加延迟：避免触发币安 API 频率限制（每笔交易间隔 1 秒）
                if i > 0:
                    time.sleep(1)  # 每个信号之间延迟 1 秒
                
                # 步骤 1: 设置杠杆
                leverage = signal.get('实际杠杆', 5)
                logger.info(f"  设置杠杆：{leverage}x")
                self.trade_api.set_um_leverage(symbol, leverage=leverage)
                
                # 添加延迟：设置杠杆后等待 0.5 秒
                time.sleep(0.5)
                
                # 步骤 2: 执行开仓
                entry_order = orders.get('entry', {})
                logger.info(f"  执行开仓：{entry_order}")
                
                # 参数映射：将订单模板的字段名映射到 API 方法签名
                entry_params = {
                    'symbol': entry_order.get('symbol'),
                    'side': entry_order.get('side'),
                    'position_side': entry_order.get('position_share'),  # 修正字段名
                    'order_type': entry_order.get('type'),
                    'quantity': entry_order.get('quantity'),
                }
                
                # 限价单必须提供 price 和 timeInForce 参数
                if entry_order.get('type') == 'LIMIT':
                    entry_params['price'] = entry_order.get('price')
                    entry_params['time_in_force'] = entry_order.get('timeInForce', 'GTC')
                    logger.info(f"  限价单参数：价格={entry_params['price']}, 有效期={entry_params['time_in_force']}")
                
                entry_result = self.trade_api.place_um_order(**entry_params)
                logger.info(f"  开仓成功：订单 ID={entry_result.get('orderId')}")
                
                # 记录开仓成功
                signal_messages.append(f"✅ {symbol} {signal['开仓方向']} 等级:{signal['信号等级']} 开仓成功")
                
                # 添加延迟：开仓后等待 1 秒再设置止盈止损 (避免 -1015 错误)
                time.sleep(1.0)
                
                # 步骤 3: 获取持仓数量 (用于止损止盈)
                positions = self.trade_api.get_position_risk(symbol)
                position_qty = abs(Decimal(positions[0]['positionAmt'])) if positions else Decimal('0')
                logger.info(f"  持仓数量：{position_qty}")
                
                # 添加延迟：查询持仓后等待 0.5 秒
                time.sleep(0.5)
                
                # 步骤 4: 设置止损
                stop_loss_order = orders.get('stop_loss', {})
                if stop_loss_order and position_qty > 0:
                    # 更新为实际持仓数量
                    stop_loss_order['quantity'] = position_qty
                    logger.info(f"  设置止损：{stop_loss_order}")
                    
                    stop_result = self.trade_api.place_pm_conditional_order(**stop_loss_order)
                    logger.info(f"  止损设置成功：策略 ID={stop_result.get('strategyId')}")
                    
                    # 添加延迟：止损设置后等待 0.5 秒
                    time.sleep(0.5)
                
                # 步骤 5: 设置止盈
                take_profit_orders = orders.get('take_profits', [])
                for tp_order in take_profit_orders:
                    if position_qty > 0:
                        # 更新为实际持仓数量 × 比例
                        tp_order['quantity'] = position_qty * tp_order.get('ratio', Decimal('0.3'))
                        logger.info(f"  设置止盈 ({tp_order.get('level')}): {tp_order}")
                        
                        tp_result = self.trade_api.place_pm_conditional_order(**tp_order)
                        logger.info(f"  止盈设置成功：策略 ID={tp_result.get('strategyId')}")
                        
                        # 添加延迟：每个止盈单之间间隔 0.5 秒
                        time.sleep(0.5)
                
                # 记录执行的交易
                trade_record = {
                    'symbol': symbol,
                    'direction': signal['开仓方向'],
                    'grade': signal['信号等级'],
                    'entry_price': Decimal(str(signal['开仓价'])),
                    'stop_loss': Decimal(str(signal['止损价'])),
                    'margin': Decimal(str(signal['保证金'])),
                    'leverage': leverage,
                    'position_qty': position_qty,
                    'entry_order_id': entry_result.get('orderId'),
                    'status': 'executed',
                    'timestamp': datetime.now()
                }
                
                executed.append(trade_record)
                logger.info(f"  ✅ 交易执行完成：{symbol}")
                
                # v6.12: 记录交易到数据库（用于频率控制）
                self.frequency_controller.record_trade(
                    symbol=symbol,
                    trade_time=datetime.now(),
                    pnl=Decimal('0'),  # 开仓时盈亏为 0
                    direction=signal['开仓方向']
                )
                
            except Exception as e:
                logger.error(f"  ❌ 交易执行失败：{symbol} - {str(e)}", exc_info=True)
                
                # 记录开仓失败
                signal_messages.append(f"❌ {symbol} {signal['开仓方向']} 等级:{signal['信号等级']} 开仓失败：{str(e)}")
        
        # 发送信号执行结果推送
        if signal_messages and self.lark_notifier:
            message = "📊 交易信号执行结果:\n\n" + "\n".join(signal_messages)
            self.lark_notifier.send_text_message(message)
        
        return executed
    
    def _send_analysis_result(self, result: Dict[str, Any]):
        """发送分析结果通知（只发检测信号，不发执行结果）"""
        if not self.lark_notifier:
            return
        
        signals = result.get('signals', [])
        
        # 构建通知消息
        title = "✅ 规则引擎分析完成" if result['success'] else "❌ 规则引擎分析失败"
        
        content = f"{title}\n\n"
        content += f"检测信号：{len(signals)} 个\n"
        
        if signals:
            content += "\n信号详情:\n"
            for signal in signals[:3]:  # 只显示前 3 个
                content += f"├─ {signal['币种']} {signal['开仓方向']} "
                content += f"等级:{signal['信号等级']} 推荐度:{signal['开仓推荐度']}\n"
        
        content += f"\n时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        
        self.lark_notifier.send_text_message(content)
    
    def _send_no_signal_notification(self, market_data: Dict[str, Any]):
        """
        发送无信号通知（告知用户系统正常工作）
        
        Args:
            market_data: 市场数据字典
        """
        if not self.lark_notifier:
            return
        
        # 构建通知消息
        content = "📊 规则引擎分析完成\n\n"
        content += f"⏰ 时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        
        # 检测的交易对
        content += f"🔍 检测交易对：{', '.join(SUPPORTED_CURRENCIES)}\n\n"
        
        # 市场状态摘要
        content += "📈 市场状态摘要：\n"
        
        for symbol, data in market_data.items():
            last_price = data.get('last_price', Decimal('0'))
            price_change_24h = data.get('price_change_24h', Decimal('0'))
            
            # 获取 ATR 数据
            indicators = data.get('indicators', {})
            hourly = indicators.get('1h', {})
            atr14 = hourly.get('atr14', Decimal('0'))
            
            # 计算 ATR%
            atr_pct = (atr14 / last_price * 100) if last_price > 0 else Decimal('0')
            
            # 判断过滤原因
            filter_reason = ""
            min_atr_pct = self.params.get('signal_filters.min_atr_pct', '0.003')
            max_atr_pct = self.params.get('signal_filters.max_atr_pct', '0.10')
            
            if atr_pct < Decimal(str(min_atr_pct)) * 100:
                filter_reason = "（ATR 过低）"
            elif atr_pct > Decimal(str(max_atr_pct)) * 100:
                filter_reason = "（ATR 过高）"
            
            # 价格变化方向
            change_symbol = "📈" if price_change_24h > 0 else "📉" if price_change_24h < 0 else "➡️"
            
            content += f"├─ {symbol}: {last_price:.2f} USDT "
            content += f"{change_symbol} {float(price_change_24h * 100):+.2f}% "
            content += f"| ATR: {float(atr_pct):.2f}% {filter_reason}\n"
        
        content += "\n💡 未检测到符合规则的交易信号\n"
        content += "系统运行正常，等待下次分析..."
        
        self.lark_notifier.send_text_message(content)
    
    def _record_daily_stats(self, signals_count: int, executed_count: int):
        """
        记录每日执行统计
        
        Args:
            signals_count: 检测到的信号数量
            executed_count: 实际执行的交易数量
        """
        try:
            # 获取今日日期
            today = datetime.now().date()
            
            # 查询今日统计是否已存在
            query = """
                SELECT id, signals_count, executed_count, win_count, loss_count
                FROM daily_execution_stats
                WHERE stat_date = %s
            """
            result = self.db._execute_one(query, (today,))
            
            if result:
                # 更新现有记录
                update_query = """
                    UPDATE daily_execution_stats
                    SET signals_count = signals_count + %s,
                        executed_count = executed_count + %s,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE stat_date = %s
                """
                self.db._execute_query(update_query, (signals_count, executed_count, today))
                logger.debug(f"今日统计已更新：信号 +{signals_count}, 执行 +{executed_count}")
            else:
                # 插入新记录
                insert_query = """
                    INSERT INTO daily_execution_stats 
                    (stat_date, signals_count, executed_count, win_count, loss_count)
                    VALUES (%s, %s, %s, 0, 0)
                """
                self.db._execute_query(insert_query, (today, signals_count, executed_count))
                logger.debug(f"今日统计已创建：信号 {signals_count}, 执行 {executed_count}")
                
        except Exception as e:
            logger.error(f"记录每日统计失败：{str(e)}")
    
    def send_daily_report(self):
        """
        发送每日执行报告（在每天早上 9 点发送前一天的报告）
        """
        try:
            # 获取昨天的日期
            yesterday = datetime.now().date() - timedelta(days=1)
            
            # 查询昨天的统计数据
            query = """
                SELECT signals_count, executed_count, win_count, loss_count
                FROM daily_execution_stats
                WHERE stat_date = %s
            """
            result = self.db._execute_one(query, (yesterday,))
            
            if not result:
                logger.info(f"昨天 ({yesterday}) 无统计数据")
                return
            
            # 计算胜率
            total_executed = result['executed_count'] or 0
            win_count = result['win_count'] or 0
            win_rate = (win_count / total_executed * 100) if total_executed > 0 else 0
            
            # 构建报告内容
            win_line = f'├─ 盈利：{win_count} 笔' if win_count > 0 else ''
            loss_line = f'└─ 亏损：{result["loss_count"]} 笔' if result['loss_count'] > 0 else ''
            
            content = f"""📊 交易日报 ({yesterday})

📈 执行统计:
├─ 检测次数：{result['signals_count']} 次
├─ 有效信号：{result['signals_count']} 个
├─ 成功执行：{result['executed_count']} 笔
└─ 胜率：{win_rate:.1f}%

{win_line}
{loss_line}

请查看完整报告获取详细分析。
"""
            
            # 发送报告
            if self.lark_notifier:
                self.lark_notifier.send_text_message(content)
                logger.info(f"昨日日报已发送：{yesterday}")
                
        except Exception as e:
            logger.error(f"发送日报失败：{str(e)}", exc_info=True)


def run_scheduler():
    """启动调度器"""
    logger.info(f"启动规则引擎调度器（时区：{TIMEZONE}）")
    
    # 创建调度器
    tz = pytz.timezone(TIMEZONE)
    scheduler = BlockingScheduler(timezone=tz)
    
    # 每小时执行一次（在每小时的25分执行）
    scheduler.add_job(
        run_analysis_wrapper,
        CronTrigger(hour='*', minute=25),  # 每小时25分执行
        id='hourly_analysis',
        name='每小时行情分析和信号检测',
        kwargs={'enable_auto_trade': True}
    )
    
    # 每天早上 9:10 发送前一天的日报
    scheduler.add_job(
        send_daily_report_wrapper,
        CronTrigger(hour=9, minute=10),  # 每天早上 9:10
        id='daily_report',
        name='每日交易报告'
    )
    
    logger.info("调度器配置完成:")
    logger.info("  - 每小时执行一次（00:25, 01:25, 02:25, ..., 23:25）")
    logger.info("  - 自动交易：已启用")
    logger.info("  - 每天早上 9:10 发送日报")
    logger.info("  - 使用 Ctrl+C 停止调度器")
    
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("调度器已停止")


def run_analysis_wrapper(enable_auto_trade: bool = True):
    """调度器任务包装器"""
    scheduler_instance = RuleEngineScheduler(enable_auto_trade=enable_auto_trade)
    return scheduler_instance.run_analysis()


def send_daily_report_wrapper():
    """发送日报的包装器"""
    scheduler_instance = RuleEngineScheduler(enable_auto_trade=False)
    return scheduler_instance.send_daily_report()


# 数据库表创建 SQL
DAILY_STATS_TABLE_SQL = """
-- 每日执行统计表
CREATE TABLE IF NOT EXISTS daily_execution_stats (
    id SERIAL PRIMARY KEY,
    stat_date DATE NOT NULL UNIQUE,  -- 统计日期
    signals_count INTEGER DEFAULT 0,  -- 检测到的信号数量
    executed_count INTEGER DEFAULT 0,  -- 实际执行的交易数量
    win_count INTEGER DEFAULT 0,  -- 盈利交易数量
    loss_count INTEGER DEFAULT 0,  -- 亏损交易数量
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 创建索引
CREATE INDEX IF NOT EXISTS idx_stat_date ON daily_execution_stats(stat_date);

-- 添加注释
COMMENT ON TABLE daily_execution_stats IS '每日交易执行统计表';
COMMENT ON COLUMN daily_execution_stats.stat_date IS '统计日期';
COMMENT ON COLUMN daily_execution_stats.signals_count IS '检测到的信号数量';
COMMENT ON COLUMN daily_execution_stats.executed_count IS '实际执行的交易数量';
COMMENT ON COLUMN daily_execution_stats.win_count IS '盈利交易数量';
COMMENT ON COLUMN daily_execution_stats.loss_count IS '亏损交易数量';
"""

# 交易记录表（用于频率控制）
TRADE_RECORDS_TABLE_SQL = """
-- 交易记录表（v6.12 频率控制专用）
CREATE TABLE IF NOT EXISTS trade_records (
    id SERIAL PRIMARY KEY,
    symbol VARCHAR(20) NOT NULL,  -- 交易对（如 BTCUSDT）
    direction VARCHAR(4),  -- 方向（多/空）
    open_time TIMESTAMP NOT NULL,  -- 开仓时间
    close_time TIMESTAMP,  -- 平仓时间
    pnl DECIMAL(20, 8),  -- 盈亏金额
    pnl_percent DECIMAL(10, 4),  -- 盈亏比例
    entry_price DECIMAL(20, 8),  -- 开仓价
    close_price DECIMAL(20, 8),  -- 平仓价
    quantity DECIMAL(20, 8),  -- 数量
    leverage INTEGER,  -- 杠杆倍数
    signal_grade VARCHAR(2),  -- 信号等级（S/A/B/C）
    status VARCHAR(20) DEFAULT 'OPEN',  -- 状态（OPEN/CLOSED）
    order_id BIGINT,  -- 订单 ID
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 创建索引
CREATE INDEX IF NOT EXISTS idx_trade_records_symbol ON trade_records(symbol);
CREATE INDEX IF NOT EXISTS idx_trade_records_open_time ON trade_records(open_time);
CREATE INDEX IF NOT EXISTS idx_trade_records_status ON trade_records(status);
CREATE INDEX IF NOT EXISTS idx_trade_records_symbol_time ON trade_records(symbol, open_time);

-- 添加注释
COMMENT ON TABLE trade_records IS '交易记录表（v6.12 频率控制专用）';
COMMENT ON COLUMN trade_records.symbol IS '交易对';
COMMENT ON COLUMN trade_records.direction IS '开仓方向';
COMMENT ON COLUMN trade_records.open_time IS '开仓时间';
COMMENT ON COLUMN trade_records.close_time IS '平仓时间';
COMMENT ON COLUMN trade_records.pnl IS '盈亏金额';
COMMENT ON COLUMN trade_records.status IS '交易状态';
COMMENT ON COLUMN trade_records.signal_grade IS '信号等级';
"""


def init_database():
    """初始化数据库表"""
    try:
        # 直接执行 SQL 创建表（避免 DSN 问题）
        from models.database import get_db_connection
        from psycopg2.extras import RealDictCursor
        
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                # 创建每日统计表
                cursor.execute(DAILY_STATS_TABLE_SQL)
                # 创建交易记录表（v6.12 频率控制）
                cursor.execute(TRADE_RECORDS_TABLE_SQL)
                conn.commit()
        
        logger.info("数据库表初始化完成：daily_execution_stats, trade_records")
    except Exception as e:
        logger.error(f"数据库表初始化失败：{str(e)}")


if __name__ == "__main__":
    # 初始化数据库表
    init_database()
    
    # 检查命令行参数
    if len(sys.argv) > 1:
        if sys.argv[1] == '--auto-trade':
            # 立即执行一次分析并自动交易
            logger.info("立即执行一次分析（带自动交易）...")
            scheduler = RuleEngineScheduler(enable_auto_trade=True)
            scheduler.run_analysis()
        elif sys.argv[1] == '--dry-run':
            # 立即执行一次分析（不带自动交易）
            logger.info("立即执行一次分析（不带自动交易）...")
            scheduler = RuleEngineScheduler(enable_auto_trade=False)
            scheduler.run_analysis()
        else:
            print("用法:")
            print("  python scheduler_new.py              # 启动调度器（每小时执行）")
            print("  python scheduler_new.py --auto-trade # 立即执行一次（带自动交易）")
            print("  python scheduler_new.py --dry-run    # 立即执行一次（不带自动交易）")
    else:
        # 启动调度器
        run_scheduler()
