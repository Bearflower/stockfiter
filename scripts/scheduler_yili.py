#!/usr/bin/env python3
"""伊利波段策略独立日调度器（北京时间）。"""

import logging
from datetime import datetime
import subprocess
import sys
import time
from zoneinfo import ZoneInfo

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from strategy.yili_swing.daily import load_daily_config


TZ = ZoneInfo("Asia/Shanghai")
logger = logging.getLogger("yili_swing_scheduler")


def should_attempt(now: datetime, schedule_time: str, cutoff_time: str, last_attempt,
                   last_done_date: str, retry_seconds: int) -> bool:
    """当天到达配置时点且尚未完成时，按配置间隔重试。"""
    today = now.date().isoformat()
    hm = now.strftime("%H:%M")
    if last_done_date == today or not schedule_time <= hm <= cutoff_time:
        return False
    return last_attempt is None or (now - last_attempt).total_seconds() >= retry_seconds


def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    daily = load_daily_config()
    logger.info("伊利波段独立调度器启动，交易日北京时间 %s 至 %s 扫描",
                daily["schedule_time"], daily["cutoff_time"])
    last_attempt = None
    last_done_date = ""
    while True:
        now = datetime.now(TZ)
        if should_attempt(now, daily["schedule_time"], daily["cutoff_time"], last_attempt,
                          last_done_date, daily["retry_seconds"]):
            today = now.date().isoformat()
            last_attempt = now
            try:
                subprocess.run(
                    [sys.executable, "scripts/daily_yili_swing.py", "--run-date", today],
                    check=True, timeout=daily["fetch_timeout_seconds"],
                )
                last_done_date = today
                logger.info("伊利波段 %s 日报处理完成", today)
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
                logger.error("伊利波段 %s 日报失败，稍后重试：%s", today, exc)
        time.sleep(daily["poll_seconds"])


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("收到中断信号，伊利波段调度器退出")
