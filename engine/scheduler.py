"""
调度器
替代 main.py 的硬编码调度，支持多策略顺序执行
根据配置文件中的 schedule 节执行定时任务
"""

import time
from typing import Dict, List, Optional, Callable
from datetime import datetime

from utils.logger import get_logger
from engine.registry import get_registry
from strategy.base import BaseStrategy
from data.database import DatabaseManager
from data.kline_service import KlineService
from notification.service import NotificationService

logger = get_logger()


class Scheduler:
    """
    策略调度器

    功能：
    1. 按配置顺序执行所有已启用策略
    2. 为每个策略加载数据、执行分析、收集信号
    3. 将信号传递给通知服务
    """

    def __init__(self, config: Dict):
        """
        初始化调度器

        Args:
            config: 完整配置字典
        """
        self.config = config
        self.registry = get_registry()
        self.signals: List[Dict] = []

        # 初始化数据库
        self.db = DatabaseManager()

        # 初始化 K 线服务
        kline_config = config.get('global', {}).get('kline_service', {})
        kline_url = kline_config.get(
            'remote_url', 'http://43.156.242.184:8765/api/v1'
        )
        self.kline_service = KlineService(self.db, remote_url=kline_url)

        # 初始化通知服务
        self.notification = NotificationService(config=config)

        logger.info("调度器初始化完成")

    def run_all_strategies(self) -> List:
        """
        按顺序执行所有已启用的策略

        Returns:
            List: 所有策略产生的信号列表
        """
        strategies = self.registry.list_enabled()

        if not strategies:
            logger.warning("没有已启用的策略")
            return []

        logger.info(f"开始执行 {len(strategies)} 个策略")

        all_signals = []

        for strategy in strategies:
            strategy_config = self._get_strategy_config(strategy.name)
            enabled = strategy_config.get('enabled', True)

            if not enabled:
                logger.info(f"策略 [{strategy.name}] 在配置中被禁用，跳过")
                continue

            logger.info(f"--- 执行策略: [{strategy.name}] v{strategy.version} ---")
            signals = self._run_strategy(strategy, strategy_config)
            all_signals.extend(signals)
            logger.info(
                f"策略 [{strategy.name}] 完成，产生 {len(signals)} 个信号"
            )

        self.signals = all_signals
        logger.info(f"所有策略执行完成，共产生 {len(all_signals)} 个信号")

        return all_signals

    def _get_strategy_config(self, name: str) -> Dict:
        """获取某个策略的配置"""
        strategies_config = self.config.get('strategies', {})
        return strategies_config.get(name, {})

    def _run_strategy(
        self, strategy: BaseStrategy, strategy_config: Dict
    ) -> List:
        """
        执行单个策略

        Args:
            strategy: 策略实例
            strategy_config: 策略配置

        Returns:
            List: 信号列表
        """
        # 获取股票列表
        stock_pool_config = self.config.get('global', {}).get('stock_pool', {})
        stocks_df = self.db.get_stock_list(stock_pool_config)

        if stocks_df is None or stocks_df.empty:
            logger.warning("股票列表为空，跳过策略执行")
            return []

        logger.info(f"股票池数量: {len(stocks_df)} 只")

        # 声明数据需求
        codes = stocks_df['code'].tolist()
        requirements = strategy.declare_data_requirements(codes)

        # 批量加载 K 线数据
        stocks = stocks_df[['code', 'symbol']].to_dict('records')
        data_map = self.kline_service.batch_load_klines(
            stocks, days=requirements[0].lookback if requirements else 120
        )

        # 执行策略分析
        signals = []
        strategy_params = strategy_config.get('params', None)
        if strategy_params:
            strategy.params.update(strategy_params)

        for idx, (_, row) in enumerate(stocks_df.iterrows()):
            code = row['code']
            df = data_map.get(code)

            if df is None or len(df) < 60:
                continue

            try:
                signal = strategy.analyze(code, {'d': df})
                if signal:
                    # 计算评分
                    signal.score = strategy.score(signal)
                    # 补充股票名称
                    if 'name' in row:
                        signal.detail['name'] = row['name']
                    signals.append(signal)

                    logger.info(
                        f"发现信号: {code} {row.get('name', '')} "
                        f"评分: {signal.score:.2f}"
                    )
            except Exception as e:
                logger.error(f"{code} 策略分析异常: {e}")

            if (idx + 1) % 500 == 0:
                logger.info(
                    f"策略分析进度: {idx + 1}/{len(stocks_df)}，"
                    f"已发现信号: {len(signals)}"
                )

        # 按评分排序
        signals.sort(key=lambda s: s.score, reverse=True)

        return signals

    def notify_signals(self, signals: Optional[List] = None) -> int:
        """
        发送信号通知

        Args:
            signals: 信号列表，不提供则使用 self.signals

        Returns:
            int: 成功发送通知的数量
        """
        if signals is None:
            signals = self.signals

        if not signals:
            logger.info("没有信号需要通知")
            return 0

        success_count = 0
        for signal in signals:
            try:
                if self.notification.send_signal(signal):
                    success_count += 1
            except Exception as e:
                logger.error(f"发送信号通知失败 [{signal.code}]: {e}")

        logger.info(
            f"信号通知完成: {success_count}/{len(signals)} 成功"
        )
        return success_count

    def send_summary(self, signals: Optional[List] = None):
        """
        发送汇总通知

        Args:
            signals: 信号列表
        """
        if signals is None:
            signals = self.signals

        today = datetime.now().strftime('%Y-%m-%d')

        if not signals:
            self.notification.send_text(
                f"股票形态扫描 ({today})\n\n今日无符合形态的买入信号\n\n继续监控中...",
                level="info"
            )
            return

        lines = [
            f"股票形态筛选信号 ({today})",
            "",
            f"共发现 {len(signals)} 个买入信号:",
            "",
        ]
        for idx, sig in enumerate(signals[:10], 1):
            detail = sig.detail
            lines.append(
                f"{idx}. **{sig.code}** {detail.get('name', '')} "
                f"评分: {sig.score:.2f}"
            )
            lines.append(
                f"   支撑位: {detail.get('support_level', 0):.2f} "
                f"放量日期: {detail.get('surge_date', '')}"
            )

        if len(signals) > 10:
            lines.append(f"\n... 还有 {len(signals) - 10} 个信号")

        content = '\n'.join(lines)

        self.notification.send_markdown(
            title=f"股票形态筛选信号 - {today}",
            content=content,
            level="warning" if len(signals) > 0 else "info"
        )

    def cleanup(self) -> None:
        """清理资源"""
        if self.db:
            self.db.close()
        logger.info("调度器资源已清理")