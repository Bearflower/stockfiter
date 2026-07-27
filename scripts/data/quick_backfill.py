#!/usr/bin/env python3
"""
快速补全指定日期的 K 线数据
用途：补全缺失的历史数据
使用 BaostockSession 保持登录状态，避免重复 login/logout
"""

import os
import sys
from datetime import datetime, timedelta

import pandas as pd

from data.database import DatabaseManager
from data.fetcher import BaostockSession
from utils.logger import get_logger

logger = get_logger()

# 连续失败阈值
MAX_CONSECUTIVE_FAILURES = 50


def fetch_missing_for_date(target_date: str, max_stocks: int = 1000) -> None:
    """
    获取指定日期缺失数据的股票

    Args:
        target_date: 目标日期 (YYYY-MM-DD)
        max_stocks: 最多处理多少只股票
    """
    logger.info("=" * 80)
    logger.info(f"补全 {target_date} 的 K 线数据")
    logger.info("=" * 80)

    db = DatabaseManager()

    # psycopg2 中 LIKE '600%' 的 % 需要转义为 %%
    query = """
        SELECT code, name, symbol FROM stocks
        WHERE (code LIKE '600%%' OR code LIKE '601%%' OR code LIKE '603%%'
               OR code LIKE '605%%' OR code LIKE '000%%' OR code LIKE '001%%'
               OR code LIKE '002%%')
        AND code NOT IN (
            SELECT DISTINCT code FROM klines WHERE date = %s
        )
        LIMIT %s
    """

    cur = db.conn.cursor()
    cur.execute(query, (target_date, max_stocks))
    rows = cur.fetchall()
    missing_df = pd.DataFrame(rows, columns=['code', 'name', 'symbol'])
    logger.info(f"{target_date} 缺失数据的主板股票数：{len(missing_df)} 只")

    if len(missing_df) == 0:
        logger.info("所有股票数据已完整！")
        db.close()
        return

    success_count = 0
    error_count = 0
    consecutive_failures = 0
    total = len(missing_df)

    logger.info("开始获取数据...")

    target_dt = datetime.strptime(target_date, '%Y-%m-%d')

    with BaostockSession() as session:
        logger.info("Baostock 会话已建立，开始批量获取...")

        for idx, row in missing_df.iterrows():
            code = row['code']
            symbol = row['symbol']
            name = row['name']

            try:
                df = session.get_kline(symbol, days=20)

                if df is not None and len(df) > 0:
                    db.save_kline_history(code, df)
                    success_count += 1
                    consecutive_failures = 0

                    target_exists = df[df['date'] == target_dt]
                    if len(target_exists) > 0:
                        logger.debug(
                            f"[{idx+1}/{total}] {code} - {name}: "
                            f"成功获取 {len(df)} 天数据 (包含 {target_date})"
                        )
                    else:
                        logger.debug(
                            f"[{idx+1}/{total}] {code} - {name}: "
                            f"获取 {len(df)} 天数据 (但缺少 {target_date})"
                        )
                else:
                    error_count += 1
                    consecutive_failures += 1
                    logger.warning(f"[{idx+1}/{total}] {code} - {name}: 获取失败")

            except Exception as e:
                error_count += 1
                consecutive_failures += 1
                logger.error(f"[{idx+1}/{total}] {code} - {name}: 异常 {e}")

            # 连续失败过多，提前终止（Baostock 可能已宕机）
            if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                logger.warning(
                    f"连续 {consecutive_failures} 只股票获取失败，Baostock 可能已宕机，提前终止"
                )
                break

            if (idx + 1) % 50 == 0:
                logger.info(
                    f"进度：{idx+1}/{total}, "
                    f"成功：{success_count}, 失败：{error_count}"
                )

    logger.info("=" * 80)
    logger.info("补全完成")
    logger.info("=" * 80)
    logger.info(f"总计：{total} 只")
    logger.info(f"成功：{success_count} 只")
    logger.info(f"失败：{error_count} 只")

    db.close()

    logger.info("验证结果...")
    db2 = DatabaseManager()
    query = """
        SELECT COUNT(DISTINCT code) FROM klines
        WHERE date = %s AND code IN (
            SELECT code FROM stocks
            WHERE code LIKE '600%%' OR code LIKE '601%%'
                  OR code LIKE '603%%' OR code LIKE '605%%'
                  OR code LIKE '000%%' OR code LIKE '001%%'
                  OR code LIKE '002%%'
        )
    """
    cur = db2.conn.cursor()
    cur.execute(query, (target_date,))
    count = cur.fetchone()[0]
    logger.info(f"{target_date} 现在有 {count} 只主板股票的数据")
    db2.close()


def main() -> None:
    """主函数"""
    if len(sys.argv) > 1:
        target_date = sys.argv[1]
    else:
        yesterday = datetime.now() - timedelta(days=1)
        if yesterday.weekday() == 0:
            yesterday = yesterday - timedelta(days=2)
        target_date = yesterday.strftime('%Y-%m-%d')

    max_stocks = int(sys.argv[2]) if len(sys.argv) > 2 else 1000

    fetch_missing_for_date(target_date, max_stocks)

    import os
    os._exit(0)


if __name__ == '__main__':
    main()