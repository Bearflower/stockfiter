#!/usr/bin/env python3
"""
沪深300指数K线数据每日更新

使用 Baostock 数据源（与个股更新脚本统一），替代之前依赖 akshare 的 import_index_data.py。
Baostock 优势：与个股更新同数据源、无需额外依赖、稳定性高。

调度时机：22:00 K线更新之前先跑一次（指数更新优先于个股）
"""
import baostock as bs
import os, sys, logging
import pandas as pd
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

from data.database import DatabaseManager

# ===== 可配置参数（禁止硬编码到业务逻辑） =====
INDEX_CONFIG = {
    'index_code': os.environ.get('INDEX_CODE', '000300'),  # 数据库存储代码（不带后缀）
    'bs_symbol': os.environ.get('BS_INDEX_SYMBOL', 'sh.000300'),  # Baostock 格式
    'frequency': os.environ.get('INDEX_FREQUENCY', 'd'),  # 日线
    'update_days': int(os.environ.get('INDEX_UPDATE_DAYS', '10')),  # 每次回补天数（覆盖漏更）
}


def update_index():
    """拉取指数 K 线并 upsert 到数据库"""
    bs_symbol = INDEX_CONFIG['bs_symbol']
    index_code = INDEX_CONFIG['index_code']
    days = INDEX_CONFIG['update_days']

    logger.info(f"指数更新开始：{bs_symbol} → 数据库 code={index_code}，回补 {days} 天")

    # Baostock 登录
    lg = bs.login()
    if lg.error_code != '0':
        logger.error(f"Baostock 登录失败：{lg.error_msg}")
        return False

    try:
        end_date = datetime.now().strftime('%Y-%m-%d')
        start_date = (datetime.now() - timedelta(days=days + 5)).strftime('%Y-%m-%d')

        # 查询历史 K 线（指数不带复权 adjustflag）
        rs = bs.query_history_k_data_plus(
            bs_symbol,
            "date,open,high,low,close,volume,amount",
            start_date=start_date,
            end_date=end_date,
            frequency=INDEX_CONFIG['frequency'],
        )

        if rs.error_code != '0':
            logger.error(f"Baostock 查询失败：{rs.error_msg}")
            return False

        data_list = []
        while rs.next():
            data_list.append(rs.get_row_data())

        if not data_list:
            logger.warning("Baostock 返回空数据")
            return False

        df = pd.DataFrame(data_list, columns=rs.fields)
        logger.info(f"Baostock 返回 {len(df)} 条指数数据：{df['date'].iloc[0]} ~ {df['date'].iloc[-1]}")

        # 写入数据库（upsert）
        db = DatabaseManager()
        cursor = db.conn.cursor()
        count = 0
        for _, row in df.iterrows():
            cursor.execute(
                """INSERT INTO klines (code, frequency, date, open, high, low, close, volume, amount)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (code, frequency, date) DO UPDATE SET
                       open=EXCLUDED.open, high=EXCLUDED.high, low=EXCLUDED.low,
                       close=EXCLUDED.close, volume=EXCLUDED.volume, amount=EXCLUDED.amount""",
                (
                    index_code, INDEX_CONFIG['frequency'],
                    str(row['date'])[:10],
                    float(row['open']), float(row['high']), float(row['low']), float(row['close']),
                    int(float(row['volume'])), float(row['amount']),
                )
            )
            count += 1
        db.conn.commit()
        cursor.close()
        db.close()

        # 验证
        db2 = DatabaseManager()
        cur2 = db2.conn.cursor()
        cur2.execute("SELECT COUNT(*), MAX(date) FROM klines WHERE code=%s AND frequency='d'", (index_code,))
        total, latest = cur2.fetchone()
        cur2.close()
        db2.close()

        logger.info(f"✅ 指数更新完成：写入 {count} 条，数据库共 {total} 条，最新 {latest}")
        return True

    except Exception as e:
        logger.error(f"指数更新异常：{e}", exc_info=True)
        return False
    finally:
        bs.logout()


if __name__ == '__main__':
    print("=" * 60)
    print("沪深300指数 K 线每日更新")
    print("=" * 60)
    success = update_index()
    print()
    if success:
        print("✅ 更新完成")
    else:
        print("❌ 更新失败")
        sys.exit(1)
