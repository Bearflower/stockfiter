#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
增量同步历史 K 线数据（后台任务）
每次同步最近 250 天，避免阻塞主任务

用途：
1. 补全缺失的股票历史数据
2. 只同步最近 250 天（保证形态检测）
3. 可以在后台慢慢运行

运行方式：
python3 sync_kline_history.py [--stocks 100]
"""

import sys
from datetime import datetime, timedelta
import signal
import argparse

from utils.logger import get_logger
from data.database import DatabaseManager
from data.fetcher import get_stock_daily_kline

logger = get_logger()


class HistorySync:
    """历史数据同步器"""
    
    def __init__(self, days: int = 250):
        """
        初始化
        
        Args:
            days: 同步最近多少天的数据，默认 250 天（约 1 年）
        """
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
    
    def sync_missing_stocks(self, batch_size: int = 100):
        """
        同步缺失数据的股票
        
        Args:
            batch_size: 每批处理多少只股票
        """
        logger.info("=" * 80)
        logger.info("历史 K 线数据同步开始（后台任务）")
        logger.info("=" * 80)
        logger.info(f"同步天数：最近 {self.days} 天")
        logger.info(f"批次大小：{batch_size} 只/批")
        logger.info("=" * 80)
        
        # 连接数据库
        self.db = DatabaseManager()
        
        # 获取股票列表（使用固定的沪深主板股票列表）
        import pandas as pd
        import os
        
        # 优先使用固定列表，如果不存在则动态生成
        stocks_file = '/app/main_board_stocks.csv'
        if os.path.exists(stocks_file):
            logger.info(f"使用固定股票列表：{stocks_file}")
            # 确保 code 列读取为字符串
            stocks_df = pd.read_csv(stocks_file, dtype={'code': str})
            logger.info(f"沪深主板股票总数：{len(stocks_df)} 只")
        else:
            stocks_df = self.db.get_stock_list()
            logger.info(f"使用动态股票列表：{len(stocks_df)} 只")
        
        total = len(stocks_df)
        logger.info(f"总股票数：{total} 只")
        
        # 找出数据不足的股票（少于 200 天数据）
        missing_stocks = []
        
        for idx, row in stocks_df.iterrows():
            code = row['code']
            symbol = row['symbol']
            
            # 检查已有数据
            existing = self.db.get_kline_history(code, days=self.days)
            if existing is None or len(existing) < 200:  # 少于 200 天数据
                missing_stocks.append((code, symbol))
        
        logger.info(f"需要同步的股票：{len(missing_stocks)} 只")
        logger.info("=" * 80)
        
        if not missing_stocks:
            logger.info("✅ 所有股票数据完整，无需同步")
            self.db.close()
            return
        
        # 分批处理
        total_batches = (len(missing_stocks) + batch_size - 1) // batch_size
        
        # 检查进度文件
        import os
        progress_file = '/app/sync_progress.txt'
        start_batch = 0
        
        if os.path.exists(progress_file):
            with open(progress_file, 'r') as f:
                start_batch = int(f.read().strip())
            logger.info(f"检测到进度文件，从第 {start_batch + 1} 批次继续...")
        else:
            logger.info(f"首次同步，从第 1 批次开始...")
        
        for batch_idx in range(start_batch, total_batches):
            start_idx = batch_idx * batch_size
            end_idx = min(start_idx + batch_size, len(missing_stocks))
            batch = missing_stocks[start_idx:end_idx]
            
            logger.info(f"\n处理批次 {batch_idx + 1}/{total_batches} ({start_idx + 1}-{end_idx}/{len(missing_stocks)})")
            
            success = 0
            error = 0
            
            for code, symbol in batch:
                try:
                    # 获取 K 线数据
                    kline_df = get_stock_daily_kline(symbol=symbol, days=self.days)
                    
                    if kline_df is not None and len(kline_df) > 0:
                        # 保存到数据库
                        self.db.save_kline_history(code, kline_df)
                        success += 1
                        logger.debug(f"{code} 同步成功：{len(kline_df)} 条")
                    else:
                        error += 1
                        logger.warning(f"{code} 获取失败：返回空数据")
                    
                except Exception as e:
                    error += 1
                    logger.error(f"{code} 同步失败：{e}")
                
                # 每 10 只股票暂停一下
                if (batch.index((code, symbol)) + 1) % 10 == 0:
                    import time
                    time.sleep(0.3)
            
            logger.info(f"批次 {batch_idx + 1}/{total_batches} 完成：成功 {success} 只，失败 {error} 只")
            
            # 保存进度
            with open(progress_file, 'w') as f:
                f.write(str(batch_idx + 1))
            
            # 每批之间暂停一下
            if batch_idx < total_batches - 1:
                import time
                time.sleep(2)
        
        # 清除进度文件
        if os.path.exists(progress_file):
            os.remove(progress_file)
            logger.info("已清除进度文件")
        
        # 最终统计
        logger.info("=" * 80)
        logger.info("历史 K 线数据同步完成")
        logger.info(f"应同步：{len(missing_stocks)} 只")
        logger.info("=" * 80)
        
        # 验证数据库
        self._verify_database()
        
        # 重跑失败的股票
        logger.info("\n等待 5 秒后开始重跑失败的股票...")
        import time
        time.sleep(5)
        
        logger.info("=" * 80)
        logger.info("开始重跑失败的股票")
        logger.info("=" * 80)
        
        # 重新初始化数据库连接
        self.db.close()
        self.db = DatabaseManager()
        
        # 找出失败的股票（数据少于 100 天的）
        failed_stocks = []
        
        # 使用固定股票列表，避免包含北交所等非主板股票
        stocks_file = '/app/main_board_stocks.csv'
        if os.path.exists(stocks_file):
            logger.info(f"重跑使用固定股票列表：{stocks_file}")
            stocks_df = pd.read_csv(stocks_file, dtype={'code': str})
        else:
            # 如果固定列表不存在，使用数据库列表并过滤
            stocks_df = self.db.get_stock_list()
            logger.warning(f"固定股票列表不存在，使用数据库列表（{len(stocks_df)} 只）")
        
        for idx, row in stocks_df.iterrows():
            code = row['code']
            symbol = row['symbol']
            
            # 检查已有数据
            existing = self.db.get_kline_history(code, days=self.days)
            if existing is None or len(existing) < 100:  # 少于 100 天数据的视为失败
                failed_stocks.append((code, symbol))
        
        logger.info(f"发现失败股票：{len(failed_stocks)} 只")
        
        if failed_stocks:
            # 重跑失败的股票（每只重试 3 次）
            max_retries = 3
            success = 0
            error = 0
            
            for i, (code, symbol) in enumerate(failed_stocks):
                try:
                    logger.info(f"[{i+1}/{len(failed_stocks)}] 重跑 {code} ({symbol})...")
                    
                    # 获取 K 线数据（带重试）
                    kline_df = None
                    for retry in range(max_retries):
                        try:
                            kline_df = get_stock_daily_kline(symbol=symbol, days=self.days)
                            if kline_df is not None and len(kline_df) > 0:
                                break
                        except Exception as e:
                            if retry < max_retries - 1:
                                logger.warning(f"{code} 第{retry+1}次重试失败：{e}")
                                time.sleep(5)  # 重试前等待 5 秒
                            else:
                                raise
                    
                    if kline_df is not None and len(kline_df) > 0:
                        # 保存到数据库
                        self.db.save_kline_history(code, kline_df)
                        success += 1
                        logger.info(f"✅ {code} 重跑成功：{len(kline_df)} 条")
                    else:
                        error += 1
                        logger.warning(f"❌ {code} 重跑失败：返回空数据")
                    
                except Exception as e:
                    error += 1
                    logger.error(f"❌ {code} 重跑失败：{e}")
                
                # 每 10 只股票暂停一下
                if (i + 1) % 10 == 0:
                    time.sleep(0.5)
            
            # 重跑统计
            logger.info("=" * 80)
            logger.info("重跑完成")
            logger.info(f"重跑总数：{len(failed_stocks)} 只")
            logger.info(f"成功：{success} 只")
            logger.info(f"失败：{error} 只")
            logger.info(f"成功率：{success/len(failed_stocks)*100:.1f}%")
            logger.info("=" * 80)
        
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
    parser = argparse.ArgumentParser(description='历史 K 线数据同步脚本')
    parser.add_argument('--days', type=int, default=250,
                        help='同步最近多少天的数据（默认 250 天）')
    parser.add_argument('--stocks', type=int, default=100,
                        help='每批处理多少只股票（默认 100 只）')
    
    args = parser.parse_args()
    
    sync = HistorySync(days=args.days)
    sync.sync_missing_stocks(batch_size=args.stocks)


if __name__ == '__main__':
    main()
