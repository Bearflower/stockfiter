#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
K 线数据初始化脚本
获取全市场所有股票的历史 K 线数据并保存到数据库

用途：
1. 首次部署时初始化数据库
2. 重新获取所有股票的历史数据

运行方式：
python3 init_kline_data.py
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path
import signal

from utils.logger import get_logger
from data.database import DatabaseManager
from data.fetcher import get_stock_daily_kline

logger = get_logger()


class KlineInitializer:
    """K 线数据初始化器"""
    
    def __init__(self, start_date: str = None, days: int = 250):
        """
        初始化
        
        Args:
            start_date: 开始日期，格式 YYYY-MM-DD，默认 2025-08-01
            days: 获取多少天的数据，默认 250 天（约 1 年）
        """
        self.start_date = start_date or '2025-08-01'
        self.days = days
        self.db = None
        
        # 信号处理
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
    
    def _signal_handler(self, signum, frame):
        """处理中断信号"""
        logger.info("\n收到中断信号，正在保存进度...")
        if self.db:
            self.db.close()
        sys.exit(0)
    
    def init_all_klines(self, skip_existing: bool = True, min_records: int = 100):
        """
        初始化所有股票的 K 线数据
        
        Args:
            skip_existing: 是否跳过已有数据的股票
            min_records: 已有数据的最小记录数（少于这个数会重新获取）
        """
        logger.info("=" * 80)
        logger.info("K 线数据初始化开始")
        logger.info("=" * 80)
        logger.info(f"开始日期：{self.start_date}")
        logger.info(f"获取天数：{self.days} 天")
        logger.info(f"跳过已有数据：{skip_existing}")
        logger.info(f"最小记录数：{min_records}")
        logger.info("=" * 80)
        
        # 连接数据库
        self.db = DatabaseManager()
        
        # 获取股票列表
        stocks_df = self.db.get_stock_list()
        total = len(stocks_df)
        logger.info(f"获取到 {total} 只股票")
        
        # 统计信息
        success = 0
        error = 0
        skip = 0
        
        # 遍历所有股票
        for idx, row in stocks_df.iterrows():
            code = row['code']
            symbol = row['symbol']
            
            # 检查是否已有数据
            if skip_existing:
                existing = self.db.get_kline_history(code, days=self.days)
                if existing is not None and len(existing) >= min_records:
                    skip += 1
                    if (idx + 1) % 100 == 0:
                        logger.info(f"进度：{idx + 1}/{total} | 成功：{success} | 失败：{error} | 跳过：{skip}")
                    continue
            
            # 获取 K 线数据
            try:
                kline_df = get_stock_daily_kline(
                    symbol=symbol,
                    days=self.days
                )
                
                if kline_df is not None and len(kline_df) > 0:
                    # 保存到数据库
                    self.db.save_kline_history(code, kline_df)
                    success += 1
                    logger.debug(f"{code} 获取成功：{len(kline_df)} 条")
                else:
                    error += 1
                    logger.warning(f"{code} 获取失败：返回空数据")
                
            except Exception as e:
                error += 1
                logger.error(f"{code} 获取失败：{e}")
            
            # 每 100 只股票打印进度
            if (idx + 1) % 100 == 0:
                logger.info(f"进度：{idx + 1}/{total} | 成功：{success} | 失败：{error} | 跳过：{skip}")
            
            # 每 10 只股票暂停一下，避免请求过快
            if (idx + 1) % 10 == 0:
                import time
                time.sleep(0.5)
        
        # 最终统计
        logger.info("=" * 80)
        logger.info("K 线数据初始化完成")
        logger.info(f"总计：{total} 只股票")
        logger.info(f"成功：{success} 只")
        logger.info(f"失败：{error} 只")
        logger.info(f"跳过：{skip} 只")
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


def main():
    """主函数"""
    import argparse
    
    parser = argparse.ArgumentParser(description='K 线数据初始化脚本')
    parser.add_argument('--start-date', type=str, default='2025-08-01',
                        help='开始日期，格式 YYYY-MM-DD')
    parser.add_argument('--days', type=int, default=250,
                        help='获取多少天的数据')
    parser.add_argument('--no-skip', action='store_true',
                        help='不跳过已有数据的股票')
    parser.add_argument('--min-records', type=int, default=100,
                        help='已有数据的最小记录数')
    
    args = parser.parse_args()
    
    initializer = KlineInitializer(
        start_date=args.start_date,
        days=args.days
    )
    
    initializer.init_all_klines(
        skip_existing=not args.no_skip,
        min_records=args.min_records
    )


if __name__ == '__main__':
    main()
