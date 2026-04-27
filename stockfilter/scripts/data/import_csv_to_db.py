#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CSV 数据导入脚本
将本地 CSV 文件导入到 PostgreSQL 数据库

用途：
1. 快速导入历史 K 线数据到数据库
2. 比在线获取快 10-20 倍

运行方式：
python3 import_csv_to_db.py
"""

import sys
from pathlib import Path
import signal
from datetime import datetime

from utils.logger import get_logger
from data.database import DatabaseManager

logger = get_logger()


class CsvImporter:
    """CSV 数据导入器"""
    
    def __init__(self, csv_dir: str = 'data/backtest/baostocks_full'):
        """
        初始化
        
        Args:
            csv_dir: CSV 文件目录
        """
        self.csv_dir = Path(csv_dir)
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
    
    def import_all_csvs(self, skip_existing: bool = True, truncate_first: bool = False):
        """
        导入所有 CSV 文件到数据库
        
        Args:
            skip_existing: 是否跳过已有数据的股票
            truncate_first: 是否先清空 K 线表
        """
        logger.info("=" * 80)
        logger.info("CSV 数据导入开始")
        logger.info("=" * 80)
        logger.info(f"CSV 目录：{self.csv_dir.absolute()}")
        logger.info(f"跳过已有数据：{skip_existing}")
        logger.info(f"先清空表：{truncate_first}")
        logger.info("=" * 80)
        
        # 如果需要，先清空 K 线表
        if truncate_first:
            logger.info("正在清空 K 线表...")
            try:
                self.db.conn.execute('TRUNCATE TABLE klines RESTART IDENTITY')
                self.db.conn.commit()
                logger.info("✅ K 线表已清空")
            except Exception as e:
                logger.error(f"清空 K 线表失败：{e}")
                return
        
        # 检查目录是否存在
        if not self.csv_dir.exists():
            logger.error(f"CSV 目录不存在：{self.csv_dir}")
            return
        
        # 连接数据库
        self.db = DatabaseManager()
        
        # 获取所有 CSV 文件
        csv_files = list(self.csv_dir.glob('*.csv'))
        total = len(csv_files)
        logger.info(f"找到 {total} 个 CSV 文件")
        
        # 统计信息
        success = 0
        error = 0
        skip = 0
        
        # 遍历所有 CSV 文件
        for idx, csv_file in enumerate(csv_files):
            # 从文件名提取股票代码
            code = csv_file.stem.replace('_data', '')
            
            # 检查是否已有数据
            if skip_existing:
                try:
                    existing = self.db.get_kline_history(code, days=2000)  # 检查所有历史数据
                    if existing is not None and len(existing) > 0:
                        skip += 1
                        if (idx + 1) % 100 == 0:
                            logger.info(f"进度：{idx + 1}/{total} | 成功：{success} | 失败：{error} | 跳过：{skip}")
                        continue
                except Exception as e:
                    # 如果检查失败，继续导入（可能是数据库为空）
                    logger.debug(f"{code} 检查失败：{e}，继续导入")
            
            # 读取 CSV 文件
            try:
                import pandas as pd
                # 尝试多种编码读取 CSV
                df = None
                encoding_used = None
                for encoding in ['utf-8', 'gb18030', 'gbk', 'latin1']:
                    try:
                        df = pd.read_csv(csv_file, encoding=encoding)
                        encoding_used = encoding
                        break
                    except UnicodeDecodeError:
                        continue
                
                if df is None:
                    logger.warning(f"{code} CSV 文件无法读取（编码问题），跳过")
                    skip += 1
                    continue
                
                # 转换日期列
                df['date'] = pd.to_datetime(df['date'])
                
                # 确保数据按日期排序
                df = df.sort_values('date')
                
                # 检查必要列
                required_cols = ['date', 'open', 'high', 'low', 'close', 'volume']
                if not all(col in df.columns for col in required_cols):
                    logger.warning(f"{code} CSV 缺少必要列，跳过")
                    skip += 1
                    continue
                
                # 数据范围检查和转换
                # 检查数值列是否有异常大的值（防止 bigint 溢出）
                numeric_cols = ['open', 'high', 'low', 'close', 'volume']
                has_overflow = False
                for col in numeric_cols:
                    if col in df.columns:
                        # 检查是否有异常值（例如超过 int64 范围）
                        max_val = df[col].max()
                        if pd.notna(max_val) and max_val > 1e15:
                            logger.warning(f"{code} {col} 列有异常大的值：{max_val}，跳过")
                            has_overflow = True
                            break
                
                # 如果有异常值，跳过
                if has_overflow:
                    skip += 1
                    continue
                
                # 添加 amount 列（如果没有）
                if 'amount' not in df.columns:
                    df['amount'] = 0  # 或者用 volume * close 估算
                
                # 确保数值列类型正确
                for col in ['open', 'high', 'low', 'close']:
                    if col in df.columns:
                        df[col] = pd.to_numeric(df[col], errors='coerce')
                
                if 'volume' in df.columns:
                    df['volume'] = pd.to_numeric(df['volume'], errors='coerce').fillna(0).astype(int)
                
                if 'amount' in df.columns:
                    df['amount'] = pd.to_numeric(df['amount'], errors='coerce').fillna(0).astype(float)
                
                # 保存到数据库
                self.db.save_kline_history(code, df)
                success += 1
                logger.debug(f"{code} 导入成功：{len(df)} 条数据 ({encoding_used})")
                
            except Exception as e:
                error += 1
                logger.warning(f"{code} 导入失败：{e}，跳过")
            
            # 每 100 个文件打印进度
            if (idx + 1) % 100 == 0:
                logger.info(f"进度：{idx + 1}/{total} | 成功：{success} | 失败：{error} | 跳过：{skip}")
            
            # 每 50 个文件暂停一下，避免数据库压力过大
            if (idx + 1) % 50 == 0:
                import time
                time.sleep(0.5)
        
        # 最终统计
        logger.info("=" * 80)
        logger.info("CSV 数据导入完成")
        logger.info(f"总计：{total} 个 CSV 文件")
        logger.info(f"成功：{success} 个")
        logger.info(f"失败：{error} 个")
        logger.info(f"跳过：{skip} 个")
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
        
        # 检查最近 7 天的数据量
        df_recent = pd.read_sql_query("""
            SELECT date, COUNT(*) as stock_count
            FROM klines
            WHERE date >= CURRENT_DATE - INTERVAL '7 days'
            GROUP BY date
            ORDER BY date DESC
        """, self.db.conn)
        
        if len(df_recent) > 0:
            logger.info("\n最近 7 天数据量:")
            for _, row in df_recent.iterrows():
                logger.info(f"  {row['date']}: {row['stock_count']} 只股票")


def main():
    """主函数"""
    import argparse
    
    parser = argparse.ArgumentParser(description='CSV 数据导入脚本')
    parser.add_argument('--csv-dir', type=str, default='data/backtest/baostocks_full',
                        help='CSV 文件目录')
    parser.add_argument('--no-skip', action='store_true',
                        help='不跳过已有数据的股票')
    parser.add_argument('--truncate', action='store_true',
                        help='先清空 K 线表')
    
    args = parser.parse_args()
    
    importer = CsvImporter(csv_dir=args.csv_dir)
    importer.import_all_csvs(
        skip_existing=not args.no_skip,
        truncate_first=args.truncate
    )


if __name__ == '__main__':
    main()
