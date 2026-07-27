#!/usr/bin/env python3
"""
股票筛选系统 V3 - 主程序（OBPC 策略入口）

3容器分离架构：
  - kline-service:    K线数据更新 + 历史补全 → scripts/scheduler_kline.py
  - strategy-obpc:    OBPC 策略调度          → scripts/scheduler_obpc.py
  - strategy-eadvisor: E大估值策略调度        → distill_changying/scripts/advisor/scheduler.py

本文件保留作为 OBPC 策略的本地开发/手动测试入口。
"""

from __future__ import annotations

import argparse
import os
import time
from datetime import datetime
from typing import Dict

import schedule
import yaml

from utils.logger import setup_logger, get_logger

# 设置日志
setup_logger("config/config.yaml")
logger = get_logger()


def load_config(config_path: str = "config/config.yaml") -> Dict:
    """加载配置文件"""
    if not os.path.exists(config_path):
        logger.warning(f"配置文件不存在: {config_path}，使用默认配置")
        return {}
    with open(config_path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)


def init_strategies(config: Dict) -> None:
    """初始化并注册所有策略"""
    from engine.registry import get_registry
    from strategy.oversold_bounce.strategy import OversoldBounceStrategy

    registry = get_registry()
    registry.clear()

    strategies_config = config.get('strategies', {})

    # 注册超跌反弹策略
    ob_config = strategies_config.get('oversold_bounce', {})
    if ob_config:
        params = ob_config.get('params', {})
        enabled = ob_config.get('enabled', True)
        strategy = OversoldBounceStrategy(params=params)
        registry.register(strategy, enabled=enabled)
        logger.info(f"策略注册: {strategy}")

    logger.info(
        f"策略初始化完成，已注册 {registry.strategy_count} 个策略"
        f"（启用 {registry.enabled_count} 个）"
    )


def run_kline_update() -> None:
    """执行 K 线数据更新"""
    logger.info("=" * 50)
    logger.info("开始执行 K 线数据更新...")
    logger.info("=" * 50)

    try:
        from scripts.data.update_kline_daily import main as update_main
        update_main()
        logger.info("K 线数据更新完成")
    except Exception as e:
        logger.error(f"K 线数据更新失败: {e}")


def run_daily_scan() -> None:
    """执行形态扫描"""
    logger.info("=" * 50)
    logger.info("开始执行形态扫描...")
    logger.info("=" * 50)

    try:
        from scripts.daily_scan import main as scan_main
        scan_main()
        logger.info("形态扫描完成")
    except Exception as e:
        logger.error(f"形态扫描失败: {e}")


def run_feishu_push() -> None:
    """执行飞书推送"""
    logger.info("=" * 50)
    logger.info("开始执行飞书推送...")
    logger.info("=" * 50)

    try:
        from scripts.feishu_push import main as push_main
        push_main()
        logger.info("飞书推送完成")
    except Exception as e:
        logger.error(f"飞书推送失败: {e}")


def run_backfill() -> None:
    """执行历史数据补全"""
    logger.info("=" * 50)
    logger.info("开始执行历史数据补全...")
    logger.info("=" * 50)

    try:
        from scripts.data.quick_backfill import main as backfill_main
        backfill_main()
        logger.info("历史数据补全完成")
    except Exception as e:
        logger.error(f"历史数据补全失败: {e}")


def run_all() -> None:
    """一次性执行完整流程：K线更新 -> 形态扫描 -> 飞书推送"""
    logger.info("=" * 60)
    logger.info("开始执行完整流程...")
    logger.info("=" * 60)

    run_kline_update()
    run_daily_scan()
    run_feishu_push()

    logger.info("完整流程执行完成")


def setup_schedule(config: Dict) -> None:
    """设置定时任务（本地开发用，生产环境使用独立容器调度器）"""
    schedule_config = config.get('schedule', {})

    kline_time = schedule_config.get('kline_update', '14:00')
    scan_time = schedule_config.get('daily_scan', '14:10')
    push_time = schedule_config.get('feishu_push', '00:10')
    backfill_time = schedule_config.get('backfill', '02:00')

    logger.info("进入定时任务模式（本地）...")
    logger.info(f"- 每日 {kline_time} UTC 执行 K 线更新")
    logger.info(f"- 每日 {scan_time} UTC 执行形态扫描")
    logger.info(f"- 每日 {push_time} UTC 执行飞书推送")
    logger.info(f"- 每日 {backfill_time} UTC 执行历史数据补全")

    schedule.every().day.at(kline_time).do(run_kline_update)
    schedule.every().day.at(scan_time).do(run_daily_scan)
    schedule.every().day.at(push_time).do(run_feishu_push)
    schedule.every().day.at(backfill_time).do(run_backfill)

    logger.info("定时任务已设置，等待执行...")

    while True:
        schedule.run_pending()
        time.sleep(60)


def main() -> None:
    """主函数"""
    parser = argparse.ArgumentParser(description='股票筛选系统 V3 — OBPC 策略')
    parser.add_argument('--config', default='config/config.yaml', help='配置文件路径')
    parser.add_argument('--scan', action='store_true', help='执行形态扫描')
    parser.add_argument('--push', action='store_true', help='执行飞书推送')
    parser.add_argument('--update', action='store_true', help='执行 K 线数据更新')
    parser.add_argument('--backfill', action='store_true', help='执行历史数据补全')
    parser.add_argument('--all', action='store_true', help='执行完整流程')
    parser.add_argument('--schedule', action='store_true', help='启动定时任务模式')

    args = parser.parse_args()

    # 加载配置
    config = load_config(args.config)
    setup_logger(args.config)

    # 初始化策略
    init_strategies(config)

    # 执行
    if args.all:
        run_all()
    elif args.scan:
        run_daily_scan()
    elif args.push:
        run_feishu_push()
    elif args.update:
        run_kline_update()
    elif args.backfill:
        run_backfill()
    elif args.schedule:
        setup_schedule(config)
    else:
        setup_schedule(config)


if __name__ == '__main__':
    main()