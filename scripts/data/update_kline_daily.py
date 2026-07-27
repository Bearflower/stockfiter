#!/usr/bin/env python3
"""
每日 K 线数据更新脚本
获取当日所有股票的 K 线数据并更新到数据库
使用 BaostockSession 保持登录状态，大幅提升效率
"""

import os
import signal
import sys
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from datetime import datetime, timedelta

import pandas as pd

from utils.logger import get_logger
from data.database import DatabaseManager
from data.fetcher import BaostockSession

logger = get_logger()


class DailyKlineUpdater:
    """每日 K 线数据更新器"""

    def __init__(self, days_to_update: int = 5):
        """
        初始化

        Args:
            days_to_update: 更新最近多少天的数据（防止遗漏）
        """
        self.days_to_update = days_to_update
        self.db = None

        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

    def _signal_handler(self, signum, frame) -> None:
        """处理中断信号"""
        logger.info("\n收到中断信号，正在退出...")
        if self.db:
            self.db.close()
        sys.exit(0)

    def _fetch_with_timeout(
        self, session: BaostockSession, symbol: str, days: int = 120, timeout: int = 30
    ):
        """
        带超时的 K 线获取，防止底层 C 扩展库无限挂起

        Args:
            session: Baostock 会话
            symbol: 股票代码
            days: 获取天数
            timeout: 超时秒数

        Returns:
            K 线 DataFrame，超时或异常时返回 None
        """
        executor = ThreadPoolExecutor(max_workers=1)
        future = executor.submit(session.get_kline, symbol, None, days)
        try:
            return future.result(timeout=timeout)
        except FuturesTimeoutError:
            logger.warning(f"{symbol} 获取超时（>{timeout}秒），跳过")
            # 强制终止线程池，避免后台线程阻塞
            executor.shutdown(wait=False, cancel_futures=True)
            return None
        finally:
            executor.shutdown(wait=False)

    def update_all_klines(self) -> None:
        """更新所有股票的 K 线数据"""
        logger.info("=" * 80)
        logger.info("每日 K 线数据更新开始")
        logger.info("=" * 80)
        logger.info(f"更新日期范围：最近 {self.days_to_update} 天")
        logger.info("=" * 80)

        self.db = DatabaseManager()

        stocks_file = '/app/main_board_stocks.csv'
        if os.path.exists(stocks_file):
            logger.info(f"使用固定股票列表：{stocks_file}")
            stocks_df = pd.read_csv(stocks_file, dtype={'code': str})
            logger.info(f"沪深主板股票总数：{len(stocks_df)} 只")
        else:
            stocks_df = self.db.get_stock_list()
            logger.info(f"使用动态股票列表：{len(stocks_df)} 只")

        total = len(stocks_df)
        logger.info(f"总股票数：{total} 只")

        today = datetime.now()
        start_date = today - timedelta(days=self.days_to_update + 10)

        logger.info(
            f"更新日期：{start_date.strftime('%Y-%m-%d')} "
            f"至 {today.strftime('%Y-%m-%d')}"
        )
        logger.info("=" * 80)

        success = 0
        error = 0
        skip = 0
        consecutive_failures = 0  # 连续失败计数
        MAX_CONSECUTIVE_FAILURES = 50  # 连续失败阈值，超过则停止（Baostock 可能已宕机）

        with BaostockSession() as session:
            logger.info("Baostock 会话已建立，开始批量获取...")

            for idx, row in stocks_df.iterrows():
                code = row['code']
                symbol = row['symbol']

                latest_data = self.db.get_latest_kline_date(code)

                if latest_data:
                    from datetime import datetime as dt

                    if isinstance(latest_data, dt):
                        latest_data_date = latest_data.date()
                    elif isinstance(latest_data, str):
                        try:
                            latest_data_date = dt.strptime(
                                latest_data[:10], '%Y-%m-%d'
                            ).date()
                        except (ValueError, TypeError):
                            latest_data_date = today.date()
                    elif hasattr(latest_data, 'date'):
                        latest_data_date = latest_data.date()
                    else:
                        latest_data_date = latest_data

                    if latest_data_date == today.date():
                        skip += 1
                        if (idx + 1) % 200 == 0:
                            logger.info(
                                f"进度：{idx + 1}/{total} | "
                                f"成功：{success} | 失败：{error} | 跳过：{skip}"
                            )
                        continue

                try:
                    kline_df = self._fetch_with_timeout(session, symbol, days=120, timeout=30)

                    if kline_df is not None and len(kline_df) > 0:
                        if latest_data:
                            from datetime import datetime as dt

                            if isinstance(latest_data, dt):
                                latest_date = latest_data
                            elif isinstance(latest_data, str):
                                try:
                                    latest_date = dt.strptime(
                                        latest_data[:10], '%Y-%m-%d'
                                    )
                                except (ValueError, TypeError):
                                    latest_date = dt.combine(
                                        today.date(), dt.min.time()
                                    )
                            elif hasattr(latest_data, 'date'):
                                latest_date = dt.combine(
                                    latest_data.date(), dt.min.time()
                                )
                            else:
                                try:
                                    if isinstance(latest_data, str):
                                        date_obj = dt.strptime(
                                            latest_data[:10], '%Y-%m-%d'
                                        ).date()
                                        latest_date = dt.combine(
                                            date_obj, dt.min.time()
                                        )
                                    else:
                                        latest_date = dt.combine(
                                            latest_data, dt.min.time()
                                        )
                                except (TypeError, ValueError, AttributeError):
                                    latest_date = dt.combine(
                                        today.date(), dt.min.time()
                                    )

                            kline_df = kline_df[
                                pd.to_datetime(kline_df['date']) > latest_date
                            ]

                        if len(kline_df) > 0:
                            self.db.save_kline_history(code, kline_df)
                            success += 1
                            consecutive_failures = 0  # 成功，重置连续失败计数
                            logger.debug(
                                f"{code} 更新成功：{len(kline_df)} 条新数据"
                            )
                        else:
                            skip += 1
                            logger.debug(f"{code} 无新数据")
                    else:
                        error += 1
                        consecutive_failures += 1
                        logger.warning(f"{code} 更新失败：返回空数据")

                except Exception as e:
                    error += 1
                    consecutive_failures += 1
                    logger.error(f"{code} 更新失败：{e}")

                # 连续失败过多，提前终止（Baostock 可能已宕机）
                if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    logger.warning(
                        f"连续 {consecutive_failures} 只股票获取失败，Baostock 可能已宕机，提前终止"
                    )
                    break

                if (idx + 1) % 200 == 0:
                    logger.info(
                        f"进度：{idx + 1}/{total} | "
                        f"成功：{success} | 失败：{error} | 跳过：{skip}"
                    )

                # 每次请求后短暂休眠，避免触发 Baostock API 限流
                time.sleep(0.05)  # 50ms 间隔，约每秒 20 只股票

        logger.info("=" * 80)
        logger.info("每日 K 线数据更新完成")
        logger.info(f"总计：{total} 只股票")
        logger.info(f"成功更新：{success} 只")
        logger.info(f"失败：{error} 只")
        logger.info(f"跳过（已有最新数据）：{skip} 只")
        logger.info("=" * 80)

        self._verify_database()

        self.db.close()

    def _verify_database(self) -> None:
        """验证数据库中的 K 线数据"""
        df = pd.read_sql_query(
            """
            SELECT
                COUNT(*) as total_records,
                COUNT(DISTINCT code) as stocks_with_data,
                MIN(date) as earliest_date,
                MAX(date) as latest_date
            FROM klines
            """,
            self.db.conn
        )

        logger.info("\n数据库验证结果:")
        logger.info(f"总记录数：{df['total_records'].iloc[0]:,}")
        logger.info(f"有数据的股票：{df['stocks_with_data'].iloc[0]} 只")
        logger.info(f"最早日期：{df['earliest_date'].iloc[0]}")
        logger.info(f"最新日期：{df['latest_date'].iloc[0]}")

        today = datetime.now().date()
        df_today = pd.read_sql_query(
            """
            SELECT COUNT(DISTINCT code) as count
            FROM klines
            WHERE date = %s
            """,
            self.db.conn, params=[today]
        )

        count = df_today['count'].iloc[0]
        if count > 0:
            logger.info(f"今日 ({today}) 已有 {count} 只股票的数据")
        else:
            logger.warning(f"今日 ({today}) 暂无数据")


def main() -> None:
    """主函数"""
    import argparse

    parser = argparse.ArgumentParser(description='每日 K 线数据更新脚本')
    parser.add_argument(
        '--days', type=int, default=5, help='更新最近多少天的数据'
    )

    args = parser.parse_args()

    updater = DailyKlineUpdater(days_to_update=args.days)
    updater.update_all_klines()

    import os
    os._exit(0)


if __name__ == '__main__':
    main()