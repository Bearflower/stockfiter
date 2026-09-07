#!/usr/bin/env python3
"""
快速补全最近交易日的 K 线数据
用途：补全因 Baostock 故障导致夜间更新提前终止而缺失的数据

修复记录（2026-09-07）：
1. 原逻辑用"日历上的昨天"作为补全目标，周末/节假日会去补全不存在的交易日
   → 改用 baostock 交易日历确定真实交易日，并自动检查最近多个交易日
2. 原逻辑只要返回非空 DataFrame 就算"成功"，但 Baostock 解压错误时
   会返回截断的旧数据（不含目标日期行），导致假成功
   → 只有返回数据包含目标日期行才算真正成功
3. 原逻辑 LIMIT 1000 只处理一批，剩余缺口永不重试
   → 改为循环补全直到无缺失（受失败率保护）
4. 熔断/批量参数从 config.yaml 的 global.backfill 段读取，不硬编码
"""

import argparse
import os
from datetime import datetime, timedelta
from collections import deque

import pandas as pd
import yaml

from data.database import DatabaseManager
from data.fetcher import BaostockSession
from utils.logger import get_logger

logger = get_logger()

# 主板股票代码前缀（与 stocks 表内容一致）
MAIN_BOARD_PREFIXES = (
    "code LIKE '600%%' OR code LIKE '601%%' OR code LIKE '603%%' "
    "OR code LIKE '605%%' OR code LIKE '000%%' OR code LIKE '001%%' "
    "OR code LIKE '002%%'"
)

# 默认参数（config.yaml 缺失或读取失败时兜底使用）
DEFAULT_CONFIG = {
    'days_back': 10,
    'kline_days': 20,
    'batch_size': 1000,
    'max_stocks_per_run': 4000,
    'failure_rate_threshold': 0.7,
    'failure_window_size': 100,
    'failure_window_min_samples': 50,
    'total_failure_limit': 300,
}


def load_backfill_config(config_path: str = 'config/config.yaml') -> dict:
    """从 config.yaml 读取 global.backfill 配置，缺失时使用默认值"""
    cfg = dict(DEFAULT_CONFIG)
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            full = yaml.safe_load(f)
        section = (full or {}).get('global', {}).get('backfill', {})
        for key in DEFAULT_CONFIG:
            if key in section and section[key] is not None:
                cfg[key] = section[key]
    except Exception as e:
        logger.warning(f"读取配置文件 {config_path} 失败，使用默认参数：{e}")
    return cfg


def get_recent_trading_dates(
    session: BaostockSession, days_back: int
) -> list:
    """
    通过 baostock 交易日历获取最近的交易日（不含今天，按日期倒序）

    Args:
        session: 已登录的 Baostock 会话
        days_back: 向前回溯的自然日天数

    Returns:
        交易日字符串列表，如 ['2026-09-04', '2026-09-03']
    """
    end_dt = datetime.now()
    start_dt = end_dt - timedelta(days=days_back)

    dates = session.get_trade_dates(
        start_dt.strftime('%Y-%m-%d'),
        end_dt.strftime('%Y-%m-%d')
    )

    if not dates:
        # 降级：交易日历获取失败时按工作日判断
        logger.warning("交易日历为空，回退为工作日判断")
        dates = [
            (end_dt - timedelta(days=i)).strftime('%Y-%m-%d')
            for i in range(1, days_back + 1)
            if (end_dt - timedelta(days=i)).weekday() < 5
        ]
        return dates

    today_str = end_dt.strftime('%Y-%m-%d')
    dates = [d for d in dates if d != today_str]
    return sorted(dates, reverse=True)


def get_missing_stocks(db: DatabaseManager, target_date: str, limit: int):
    """查询目标日期缺失数据的主板股票"""
    query = f"""
        SELECT code, name, symbol FROM stocks
        WHERE ({MAIN_BOARD_PREFIXES})
        AND code NOT IN (
            SELECT DISTINCT code FROM klines WHERE date = %s
        )
        LIMIT %s
    """
    cur = db.conn.cursor()
    cur.execute(query, (target_date, limit))
    rows = cur.fetchall()
    cur.close()
    return pd.DataFrame(rows, columns=['code', 'name', 'symbol'])


def count_stocks_for_date(db: DatabaseManager, target_date: str) -> int:
    """统计目标日期已有多少只主板股票的数据"""
    query = f"""
        SELECT COUNT(DISTINCT code) FROM klines
        WHERE date = %s AND code IN (
            SELECT code FROM stocks WHERE {MAIN_BOARD_PREFIXES}
        )
    """
    cur = db.conn.cursor()
    cur.execute(query, (target_date,))
    count = cur.fetchone()[0]
    cur.close()
    return count


def backfill_date(
    session: BaostockSession,
    db: DatabaseManager,
    target_date: str,
    cfg: dict,
    budget: int
) -> int:
    """
    补全单个交易日的数据，循环处理直到无缺失或触发保护

    Args:
        session: Baostock 会话
        db: 数据库连接
        target_date: 目标交易日 (YYYY-MM-DD)
        cfg: 补全配置（阈值、批量大小等）
        budget: 本次运行剩余可处理股票配额

    Returns:
        实际处理的股票数量
    """
    target_dt = pd.Timestamp(target_date)
    batch_size = int(cfg['batch_size'])
    kline_days = int(cfg['kline_days'])
    window_size = int(cfg['failure_window_size'])
    min_samples = int(cfg['failure_window_min_samples'])
    rate_threshold = float(cfg['failure_rate_threshold'])
    total_limit = int(cfg['total_failure_limit'])

    before_count = count_stocks_for_date(db, target_date)
    missing_df = get_missing_stocks(db, target_date, batch_size)

    logger.info("=" * 80)
    logger.info(
        f"补全 {target_date} 的 K 线数据：已有 {before_count} 只，"
        f"本批缺失 {len(missing_df)} 只"
    )
    logger.info("=" * 80)

    if len(missing_df) == 0:
        logger.info(f"{target_date} 数据已完整，无需补全")
        return 0

    def abort(reason: str, batch_ok: int, batch_fail: int,
              total_ok: int, total_fail: int, processed: int) -> int:
        """熔断终止时的统一收尾逻辑"""
        logger.warning(reason)
        logger.info(
            f"{target_date} 终止时本批：成功 {batch_ok}，失败 {batch_fail}；"
            f"累计：成功 {total_ok + batch_ok}，失败 {total_fail + batch_fail}"
        )
        after = count_stocks_for_date(db, target_date)
        logger.info(
            f"{target_date} 补全结果：{before_count} 只 → {after} 只，"
            f"新增 {after - before_count} 只"
        )
        return processed

    recent_results = deque(maxlen=window_size)  # 1=失败, 0=成功
    total_success = 0
    total_failure = 0
    processed = 0
    batch_no = 0

    while len(missing_df) > 0:
        batch_no += 1
        batch_success = 0
        batch_failure = 0

        for _, row in missing_df.iterrows():
            if budget - processed <= 0:
                return abort(
                    "本次运行处理配额已用尽，剩余缺口留待下次补全",
                    batch_success, batch_failure,
                    total_success, total_failure, processed
                )

            code = row['code']
            symbol = row['symbol']
            name = row['name']

            try:
                df = session.get_kline(symbol, days=kline_days)

                if df is not None and len(df) > 0:
                    db.save_kline_history(code, df)
                    # 关键校验：返回数据必须真正包含目标日期行，
                    # 否则属于 Baostock 截断/损坏数据，不能算成功
                    if (df['date'] == target_dt).any():
                        batch_success += 1
                        recent_results.append(0)
                    else:
                        batch_failure += 1
                        recent_results.append(1)
                        logger.warning(
                            f"{code} - {name}: 返回 {len(df)} 条数据"
                            f"但不含 {target_date}（Baostock 数据异常）"
                        )
                else:
                    batch_failure += 1
                    recent_results.append(1)
                    logger.warning(f"{code} - {name}: 获取失败（返回空数据）")

            except Exception as e:
                batch_failure += 1
                recent_results.append(1)
                logger.error(f"{code} - {name}: 异常 {e}")

            processed += 1

            # 滑动窗口失败率检测
            if len(recent_results) >= min_samples:
                failure_rate = sum(recent_results) / len(recent_results)
                if failure_rate > rate_threshold:
                    return abort(
                        f"最近 {len(recent_results)} 次请求失败率 "
                        f"{failure_rate:.0%}，Baostock 间歇性故障，提前终止",
                        batch_success, batch_failure,
                        total_success, total_failure, processed
                    )

            # 累计失败硬上限
            if total_failure + batch_failure >= total_limit:
                return abort(
                    f"累计失败 {total_failure + batch_failure} 次，"
                    f"达到上限 {total_limit}，提前终止",
                    batch_success, batch_failure,
                    total_success, total_failure, processed
                )

        total_success += batch_success
        total_failure += batch_failure
        logger.info(
            f"{target_date} 第 {batch_no} 批完成："
            f"本批成功 {batch_success}，失败 {batch_failure}，"
            f"累计成功 {total_success}，累计失败 {total_failure}"
        )

        # 重新查询缺失情况：无进展则停止（剩余股票可能停牌）
        missing_df = get_missing_stocks(db, target_date, batch_size)
        if len(missing_df) > 0 and batch_success == 0:
            logger.warning(
                f"{target_date} 本批无成功补全，剩余 {len(missing_df)} 只"
                f"可能停牌或 Baostock 暂无数据，留待下次补全"
            )
            break

    after = count_stocks_for_date(db, target_date)
    logger.info(
        f"{target_date} 补全结果：{before_count} 只 → {after} 只，"
        f"新增 {after - before_count} 只"
    )
    return processed


def parse_args():
    """解析命令行参数（兼容旧用法：quick_backfill.py 2026-09-04）"""
    parser = argparse.ArgumentParser(description='K 线历史数据补全脚本')
    parser.add_argument(
        'target_date', nargs='?', default=None,
        help='手动指定补全目标日期（YYYY-MM-DD），不传则自动检查最近交易日'
    )
    args = parser.parse_args()

    if args.target_date:
        datetime.strptime(args.target_date, '%Y-%m-%d')  # 格式校验
    return args


def main() -> None:
    """主函数：补全最近交易日缺失的 K 线数据"""
    args = parse_args()
    cfg = load_backfill_config()

    db = DatabaseManager()
    try:
        with BaostockSession() as session:
            if args.target_date:
                trading_dates = [args.target_date]
            else:
                trading_dates = get_recent_trading_dates(
                    session, int(cfg['days_back'])
                )

            logger.info(f"待检查交易日（倒序）：{trading_dates}")

            budget = int(cfg['max_stocks_per_run'])
            for target_date in trading_dates:
                if budget <= 0:
                    logger.warning("本次运行处理配额已用尽，后续日期留待下次补全")
                    break
                budget -= backfill_date(session, db, target_date, cfg, budget)
    finally:
        db.close()

    logger.info("=" * 80)
    logger.info("历史数据补全任务结束")
    logger.info("=" * 80)

    # 强制退出：Baostock C 扩展可能残留无法终止的后台线程
    os._exit(0)


if __name__ == '__main__':
    main()
