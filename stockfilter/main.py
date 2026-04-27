#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
股票形态筛选系统 - 主程序
支持定时任务和命令行模式
"""

import argparse
import schedule
import time
from datetime import datetime
from utils.logger import get_logger

logger = get_logger()


def run_kline_update():
    """执行K线数据更新"""
    logger.info("=" * 50)
    logger.info("开始执行K线数据更新...")
    logger.info("=" * 50)
    
    try:
        from scripts.data.update_kline_daily import main as update_main
        update_main()
        logger.info("✅ K线数据更新完成")
    except Exception as e:
        logger.error(f"❌ K线数据更新失败: {e}")


def run_daily_scan():
    """执行形态扫描"""
    logger.info("=" * 50)
    logger.info("开始执行形态扫描...")
    logger.info("=" * 50)
    
    try:
        from scripts.daily_scan import main as scan_main
        scan_main()
        logger.info("✅ 形态扫描完成")
    except Exception as e:
        logger.error(f"❌ 形态扫描失败: {e}")


def run_feishu_push():
    """执行飞书推送"""
    logger.info("=" * 50)
    logger.info("开始执行飞书推送...")
    logger.info("=" * 50)
    
    try:
        from scripts.feishu_push import main as push_main
        push_main()
        logger.info("✅ 飞书推送完成")
    except Exception as e:
        logger.error(f"❌ 飞书推送失败: {e}")


def run_backfill():
    """执行历史数据补全"""
    logger.info("=" * 50)
    logger.info("开始执行历史数据补全...")
    logger.info("=" * 50)
    
    try:
        from scripts.data.quick_backfill import main as backfill_main
        backfill_main()
        logger.info("✅ 历史数据补全完成")
    except Exception as e:
        logger.error(f"❌ 历史数据补全失败: {e}")


def setup_schedule():
    """设置定时任务"""
    logger.info("进入定时任务模式...")
    logger.info("- 每日 14:00 UTC (22:00 北京) 执行 K 线更新 + 形态扫描")
    logger.info("- 每日 00:10 UTC (08:10 北京) 执行飞书推送")
    logger.info("- 每日 02:00 UTC (10:00 北京) 执行历史数据补全（后台任务）")
    
    # 每日 14:00 UTC (22:00 北京) 执行 K 线更新 + 形态扫描
    schedule.every().day.at("14:00").do(run_kline_update)
    schedule.every().day.at("14:10").do(run_daily_scan)
    
    # 每日 00:10 UTC (08:10 北京) 执行飞书推送
    schedule.every().day.at("00:10").do(run_feishu_push)
    
    # 每日 02:00 UTC (10:00 北京) 执行历史数据补全
    schedule.every().day.at("02:00").do(run_backfill)
    
    logger.info("定时任务已设置，等待执行...")
    
    while True:
        schedule.run_pending()
        time.sleep(60)


def main():
    parser = argparse.ArgumentParser(description='股票形态筛选系统')
    parser.add_argument('--scan', action='store_true', help='执行形态扫描')
    parser.add_argument('--push', action='store_true', help='执行飞书推送')
    parser.add_argument('--update', action='store_true', help='执行K线数据更新')
    parser.add_argument('--backfill', action='store_true', help='执行历史数据补全')
    parser.add_argument('--schedule', action='store_true', help='启动定时任务模式')
    
    args = parser.parse_args()
    
    if args.scan:
        run_daily_scan()
    elif args.push:
        run_feishu_push()
    elif args.update:
        run_kline_update()
    elif args.backfill:
        run_backfill()
    elif args.schedule:
        setup_schedule()
    else:
        # 默认启动定时任务模式
        setup_schedule()


if __name__ == '__main__':
    main()
