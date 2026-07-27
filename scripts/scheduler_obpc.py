"""
OBPC 超跌反弹策略调度器
每天 22:15(北京) 形态扫描（等待K线更新完成），08:10 飞书推送
使用明确的 Asia/Shanghai 时区，防止 Docker 容器时区漂移
"""
import os
import sys
import time
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Shanghai")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("obpc_scheduler")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

KLINE_WAIT_TIMEOUT = int(os.environ.get("KLINE_WAIT_TIMEOUT", 1800))
KLINE_WAIT_INTERVAL = int(os.environ.get("KLINE_WAIT_INTERVAL", 60))

logger.info("OBPC 策略调度器启动（当前时间: %s）", datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S"))
logger.info("定时任务：22:15 形态扫描 / 08:10 飞书推送（均为北京时间）")
logger.info("K线等待超时：%ds，轮询间隔：%ds", KLINE_WAIT_TIMEOUT, KLINE_WAIT_INTERVAL)

# 当天已触发过的任务
last_run_scan: str = ""
last_run_push: str = ""


def wait_for_kline_update() -> bool:
    """轮询 task_status 表，等待 K线更新完成"""
    from data.database import DatabaseManager

    db = DatabaseManager()
    deadline = datetime.now(TZ) + timedelta(seconds=KLINE_WAIT_TIMEOUT)
    waited = 0

    while datetime.now(TZ) < deadline:
        cur = db.conn.cursor()
        cur.execute(
            "SELECT last_completed_at FROM schema_stockfilter.task_status WHERE task_name=%s",
            ("kline_update",),
        )
        row = cur.fetchone()
        if row and row[0]:
            # PostgreSQL TIMESTAMP 返回 naive datetime，需补时区信息
            ts = row[0] if row[0].tzinfo else row[0].replace(tzinfo=TZ)
            elapsed = (datetime.now(TZ) - ts).total_seconds()
            if elapsed < 3600:
                logger.info("K线更新已完成（%d分钟前）", int(elapsed / 60))
                db.close()
                return True
        waited += KLINE_WAIT_INTERVAL
        logger.info("等待K线更新... (%ds / %ds)", waited, KLINE_WAIT_TIMEOUT)
        time.sleep(KLINE_WAIT_INTERVAL)

    db.close()
    logger.warning("K线更新等待超时（%ds），继续执行扫描", KLINE_WAIT_TIMEOUT)
    return False


while True:
    now = datetime.now(TZ)
    today = now.strftime("%Y-%m-%d")
    hm = now.strftime("%H:%M")

    if hm == "22:15" and last_run_scan != today:
        last_run_scan = today
        logger.info("=== 等待 K 线更新 ===")
        wait_for_kline_update()
        logger.info("=== 执行形态扫描（22:15 北京）===")
        try:
            import subprocess

            subprocess.run(
                [sys.executable, "scripts/daily_scan.py"],
                check=True,
                timeout=3600,
            )
            logger.info("形态扫描完成")
        except Exception as e:
            logger.error("形态扫描失败: %s", e, exc_info=True)
        time.sleep(90)

    elif hm == "08:10" and last_run_push != today:
        last_run_push = today
        logger.info("=== 执行飞书推送（08:10 北京）===")
        try:
            import subprocess

            subprocess.run(
                [sys.executable, "scripts/feishu_push.py"],
                check=True,
                timeout=600,
            )
            logger.info("飞书推送完成")
        except Exception as e:
            logger.error("飞书推送失败: %s", e, exc_info=True)
        time.sleep(90)

    else:
        time.sleep(30)