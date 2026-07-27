"""
导入沪深300指数K线数据到数据库

用于大盘环境过滤：OBPC策略需要沪深300指数数据来判断大盘环境
数据源：akshare
目标表：klines（code='000300', frequency='d'）
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime
from data.database import DatabaseManager
from utils.logger import get_logger

logger = get_logger()


def import_index_data(index_code: str = '000300', start_date: str = '2019-01-01'):
    """
    从 akshare 导入指数K线数据到数据库

    Args:
        index_code: 指数代码（不带后缀），如 000300
        start_date: 起始日期
    """
    try:
        import akshare as ak
    except ImportError:
        logger.error("akshare 未安装，请先安装：pip install akshare")
        return

    logger.info(f"开始导入指数 {index_code} 数据（从 {start_date}）")

    # 从 akshare 获取沪深300指数日线数据
    try:
        # akshare 的指数代码格式：sh000300
        ak_code = f'sh{index_code}' if index_code.startswith('000') else f'sz{index_code}'
        df = ak.stock_zh_index_daily(symbol=ak_code)
        logger.info(f"从 akshare 获取到 {len(df)} 条指数数据")
    except Exception as e:
        logger.error(f"akshare 获取指数数据失败：{e}")
        return

    if df is None or df.empty:
        logger.error("获取到的指数数据为空")
        return

    # 筛选日期范围
    df['date'] = df['date'].astype(str)
    df = df[df['date'] >= start_date]
    logger.info(f"筛选 {start_date} 后的数据：{len(df)} 条")

    # 连接数据库
    db = DatabaseManager()

    # 逐条插入（使用 upsert 逻辑）
    cursor = db.conn.cursor()
    inserted = 0
    updated = 0

    for _, row in df.iterrows():
        try:
            # 使用 INSERT ... ON CONFLICT 实现 upsert
            cursor.execute(
                """
                INSERT INTO klines (
                    code, frequency, date, open, high, low, close,
                    volume, amount
                ) VALUES (
                    %s, 'd', %s, %s, %s, %s, %s, %s, %s
                )
                ON CONFLICT (code, frequency, date)
                DO UPDATE SET
                    open = EXCLUDED.open,
                    high = EXCLUDED.high,
                    low = EXCLUDED.low,
                    close = EXCLUDED.close,
                    volume = EXCLUDED.volume,
                    amount = EXCLUDED.amount
                """,
                (
                    index_code,
                    str(row['date'])[:10],
                    float(row.get('open', 0)),
                    float(row.get('high', 0)),
                    float(row.get('low', 0)),
                    float(row.get('close', 0)),
                    int(row.get('volume', 0)),
                    float(row.get('amount', 0)),
                )
            )
            inserted += 1
        except Exception as e:
            db.conn.rollback()
            logger.debug(f"插入 {row['date']} 数据失败：{e}")

    db.conn.commit()
    cursor.close()
    db.close()

    logger.info(f"导入完成：共处理 {inserted} 条记录")

    # 验证导入结果
    db2 = DatabaseManager()
    cursor2 = db2.conn.cursor()
    cursor2.execute(
        "SELECT COUNT(*), MIN(date), MAX(date) FROM klines WHERE code = %s AND frequency = 'd'",
        (index_code,)
    )
    count, min_date, max_date = cursor2.fetchone()
    cursor2.close()
    db2.close()

    logger.info(f"数据库验证：指数 {index_code} 共 {count} 条记录，范围 {min_date} ~ {max_date}")


if __name__ == '__main__':
    print("=" * 60)
    print("导入沪深300指数K线数据")
    print("=" * 60)

    import_index_data('000300', '2019-01-01')

    print("\n导入完成。大盘环境过滤功能现在可以正常使用。")
