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
    """执行 K 线数据更新（C-1 + R-01：所有阈值从 config.yaml 读取，禁止硬编码）

    AC-2 修复：入口处先查 is_trading_day，非交易日直接跳过不采集、不抛异常，
    保证 main.py --update 路径（不经 scheduler 守卫）不会因非交易日误判 FAIL。
    """
    logger.info("=" * 50)
    logger.info("开始执行 K 线数据更新...")
    logger.info("=" * 50)

    # C-1：加载 config.yaml，所有参数从配置读取，禁止硬编码
    # 2026-10-22：与 scheduler_kline 共用同一加载器（含分片采集配置与历史默认值），
    # 避免 main.py --all 与定时任务出现两套配置口径。
    from scripts.data.update_kline_daily import load_kline_update_config
    config_path = os.path.join(os.path.dirname(__file__), 'config/config.yaml')
    kline_cfg = load_kline_update_config(config_path)

    # AC-2：非交易日守卫——main.py --update 不经 scheduler 守卫，
    # 这里兜底：非交易日跳过采集循环，记 INFO 不 raise，让后续扫描/推送正常执行。
    try:
        from utils.trade_calendar import is_trading_day
        today_str = datetime.now().strftime("%Y-%m-%d")
        today_is_trading = is_trading_day(today_str)
    except Exception:
        today_is_trading = True  # trade_calendar 异常时保守：按交易日处理

    if not today_is_trading:
        logger.info(
            "今日 %s 非交易日（is_trading_day=False），跳过 K 线采集循环，"
            "后续扫描/推送正常执行（AC-2 非交易日守卫）",
            today_str,
        )
        return

    # C-1 + R-01：完整性阈值从同一 config 读取，禁止字面量
    min_success_rate = kline_cfg.get('min_success_rate', 0.9)
    # 兜底值与 config/config.yaml 默认值保持一致（配置管理设计.md §8.4：不得凭空另设数字）
    total_failure_limit = kline_cfg.get('total_failure_limit', 200)
    min_coverage_rate = kline_cfg.get('min_coverage_rate', 0.98)

    # C-1：不再调 updater.main()（后者抛 SystemExit 会被 except Exception 漏掉）
    # 改为调 run_update 业务函数，正常 return UpdateResult，流水线可继续
    from scripts.data.update_kline_daily import run_update as kline_run_update
    # C-1 + H-1：cfg 从 config 读取，统一由 updater.cfg 单点管理
    # 人工入口允许滚动截止窗口（strict_deadline=False），自动调度入口保持严格截止
    result = kline_run_update(cfg=kline_cfg, strict_deadline=False)

    # R-01 + B2：完整性判定（严格对齐第 9.2 节 4 条顺序）
    skipped_ratio_guard = kline_cfg.get('skipped_ratio_guard', 0.95)
    complete = result.is_complete(
        min_success_rate, total_failure_limit, min_coverage_rate,
        target_date=result.target_date,
        skipped_ratio_guard=skipped_ratio_guard,
    )
    td_pct = result.target_date_coverage_rate * 100
    logger.info(
        f"K 线数据更新：success={result.success}, failed={result.failed}, "
        f"skipped={result.skipped}, processed={result.processed_count}/{result.total}"
        f"（{result.coverage_rate * 100:.2f}%）, target_date={result.target_date}, "
        f"target_date_coverage={td_pct:.2f}% → "
        f"{'PASS' if complete else 'FAIL'}"
    )

    # R-01：数据不完整时向上抛异常，阻断 run_all 中的后续扫描与推送
    if not complete:
        raise RuntimeError(
            f"K 线数据完整性 FAIL：处理 {result.processed_count}/{result.total}，"
            f"目标交易日 {result.target_date} 覆盖 "
            f"{td_pct:.2f}%。"
            f"下游扫描与推送已阻断。"
        )


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
    import sys

    logger.info("=" * 60)
    logger.info("开始执行完整流程...")
    logger.info("=" * 60)

    # R-5：run_kline_update 可能抛 RuntimeError（数据完整性 FAIL），
    # 捕获后记 ERROR 日志 + 非零退出码干净退出，让 CI/监控能检测到失败。
    # 不静默吞掉，也不打印 traceback（可读性差）。
    try:
        run_kline_update()
    except RuntimeError as e:
        logger.error(f"完整流程中止：K 线数据完整性 FAIL，后续扫描/推送已跳过。原因：{e}")
        sys.exit(1)

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
        try:
            run_kline_update()
        except RuntimeError as e:
            # R-01：数据完整性 FAIL → 以非零退出码表达失败
            logger.error(f"K 线数据更新完整性 FAIL：{e}")
            import sys
            sys.exit(1)
    elif args.backfill:
        run_backfill()
    elif args.schedule:
        setup_schedule(config)
    else:
        setup_schedule(config)


if __name__ == '__main__':
    main()
