#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
每日 K 线数据更新脚本
获取当日所有股票的 K 线数据并更新到数据库

用途：
1. 每日收盘后自动更新当日数据
2. 补充缺失的历史数据

运行方式：
python3 update_kline_daily.py
"""

import sys
from datetime import datetime, timedelta
import signal

from utils.logger import get_logger
from data.database import DatabaseManager
from data.fetcher import get_stock_daily_kline

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
        
        # 信号处理
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
    
    def _signal_handler(self, signum, frame):
        """处理中断信号"""
        logger.info("\n收到中断信号，正在退出...")
        if self.db:
            self.db.close()
        sys.exit(0)
    
    def update_all_klines(self):
        """
        更新所有股票的 K 线数据
        """
        logger.info("=" * 80)
        logger.info("每日 K 线数据更新开始")
        logger.info("=" * 80)
        logger.info(f"更新日期范围：最近 {self.days_to_update} 天")
        logger.info("=" * 80)
        
        # 连接数据库
        self.db = DatabaseManager()
        
        # 获取股票列表（使用固定的沪深主板股票列表）
        import pandas as pd
        import os
        
        # 优先使用固定列表（3026 只沪深主板股票）
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
        
        # 计算日期范围
        today = datetime.now()
        start_date = today - timedelta(days=self.days_to_update + 10)  # 多留几天余量
        
        logger.info(f"更新日期：{start_date.strftime('%Y-%m-%d')} 至 {today.strftime('%Y-%m-%d')}")
        logger.info("=" * 80)
        
        # 统计信息
        success = 0
        error = 0
        skip = 0
        
        # 遍历所有股票
        for idx, row in stocks_df.iterrows():
            code = row['code']
            symbol = row['symbol']
            
            # 检查该股票已有数据的最新日期
            latest_data = self.db.get_latest_kline_date(code)
            
            # 如果最新日期在最近 N 天内，跳过
            if latest_data:
                # 确保类型一致（datetime.date 或 datetime.datetime）
                from datetime import datetime as dt
                if isinstance(latest_data, dt):
                    latest_data_date = latest_data.date()
                elif isinstance(latest_data, str):
                    # 如果是字符串，转换为 datetime
                    try:
                        latest_data_date = dt.strptime(latest_data[:10], '%Y-%m-%d').date()
                    except (ValueError, TypeError):
                        latest_data_date = today.date()
                elif hasattr(latest_data, 'date'):
                    latest_data_date = latest_data.date()
                else:
                    latest_data_date = latest_data
                
                days_diff = (today.date() - latest_data_date).days
                if days_diff < self.days_to_update:
                    skip += 1
                    if (idx + 1) % 200 == 0:
                        logger.info(f"进度：{idx + 1}/{total} | 成功：{success} | 失败：{error} | 跳过：{skip}")
                    continue
            
            # 获取 K 线数据（获取最近 120 天，确保覆盖缺失的日期）
            try:
                kline_df = get_stock_daily_kline(
                    symbol=symbol,
                    days=120
                )
                
                if kline_df is not None and len(kline_df) > 0:
                    # 过滤掉已有数据
                    if latest_data:
                        from datetime import datetime as dt
                        if isinstance(latest_data, dt):
                            latest_date = latest_data
                        elif isinstance(latest_data, str):
                            try:
                                latest_date = dt.strptime(latest_data[:10], '%Y-%m-%d')
                            except (ValueError, TypeError):
                                latest_date = dt.combine(today.date(), dt.min.time())
                        elif hasattr(latest_data, 'date'):
                            latest_date = dt.combine(latest_data.date(), dt.min.time())
                        else:
                            # latest_data 是字符串或其他类型，需要转换
                            try:
                                if isinstance(latest_data, str):
                                    # 字符串转换为 date 对象
                                    date_obj = dt.strptime(latest_data[:10], '%Y-%m-%d').date()
                                    latest_date = dt.combine(date_obj, dt.min.time())
                                else:
                                    # 尝试直接使用
                                    latest_date = dt.combine(latest_data, dt.min.time())
                            except (TypeError, ValueError, AttributeError):
                                latest_date = dt.combine(today.date(), dt.min.time())
                        
                        # 过滤 kline_df 中日期大于 latest_date 的行
                        kline_df = kline_df[pd.to_datetime(kline_df['date']) > latest_date]
                    
                    # 只保存新数据
                    if len(kline_df) > 0:
                        self.db.save_kline_history(code, kline_df)
                        success += 1
                        logger.debug(f"{code} 更新成功：{len(kline_df)} 条新数据")
                    else:
                        skip += 1
                        logger.debug(f"{code} 无新数据")
                else:
                    error += 1
                    logger.warning(f"{code} 更新失败：返回空数据")
                
            except Exception as e:
                error += 1
                logger.error(f"{code} 更新失败：{e}")
            
            # 每 200 只股票打印进度
            if (idx + 1) % 200 == 0:
                logger.info(f"进度：{idx + 1}/{total} | 成功：{success} | 失败：{error} | 跳过：{skip}")
            
            # 每 20 只股票暂停一下，避免请求过快
            if (idx + 1) % 20 == 0:
                import time
                time.sleep(0.3)
        
        # 最终统计
        logger.info("=" * 80)
        logger.info("每日 K 线数据更新完成")
        logger.info(f"总计：{total} 只股票")
        logger.info(f"成功更新：{success} 只")
        logger.info(f"失败：{error} 只")
        logger.info(f"跳过（已有最新数据）：{skip} 只")
        logger.info("=" * 80)
        
        # 验证数据库
        self._verify_database()
        
        self.db.close()
    
    def _verify_database(self):
        """验证数据库中的 K 线数据"""
        import pandas as pd
        
        df = pd.read_sql_query("""
            SELECT 
                COUNT(*) as total_records,
                COUNT(DISTINCT code) as stocks_with_data,
                MIN(date) as earliest_date,
                MAX(date) as latest_date
            FROM klines
        """, self.db.conn)
        
        logger.info("\n数据库验证结果:")
        logger.info(f"总记录数：{df['total_records'].iloc[0]:,}")
        logger.info(f"有数据的股票：{df['stocks_with_data'].iloc[0]} 只")
        logger.info(f"最早日期：{df['earliest_date'].iloc[0]}")
        logger.info(f"最新日期：{df['latest_date'].iloc[0]}")
        
        # 检查今天是否有数据
        today = datetime.now().date()
        df_today = pd.read_sql_query("""
            SELECT COUNT(DISTINCT code) as count
            FROM klines
            WHERE date = %s
        """, self.db.conn, params=[today])
        
        count = df_today['count'].iloc[0]
        if count > 0:
            logger.info(f"✅ 今日 ({today}) 已有 {count} 只股票的数据")
        else:
            logger.warning(f"⚠️  今日 ({today}) 暂无数据")


def main():
    """主函数"""
    import argparse
    
    parser = argparse.ArgumentParser(description='每日 K 线数据更新脚本')
    parser.add_argument('--days', type=int, default=5,
                        help='更新最近多少天的数据')
    
    args = parser.parse_args()
    
    updater = DailyKlineUpdater(days_to_update=args.days)
    updater.update_all_klines()


if __name__ == '__main__':
    main()
