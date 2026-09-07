"""
K线数据服务调度器
每天 22:00(北京) K线更新，07:00 历史补全
使用明确的 Asia/Shanghai 时区，防止 Docker 容器时区漂移
"""
import os
import sys
import time
import logging
import subprocess
from datetime import datetime
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Shanghai")

# 可配置参数（通过环境变量覆盖，默认值合理无需配置）
SCHEDULE_KLINE_UPDATE = os.environ.get("SCHEDULE_KLINE_UPDATE", "22:00")
SCHEDULE_BACKFILL = os.environ.get("SCHEDULE_BACKFILL", "07:00")
KLINE_UPDATE_TIMEOUT = int(os.environ.get("KLINE_UPDATE_TIMEOUT", "10800"))  # 3小时
BACKFILL_TIMEOUT = int(os.environ.get("BACKFILL_TIMEOUT", "7200"))  # 2小时
POST_TASK_DELAY = int(os.environ.get("POST_TASK_DELAY", "90"))
LOOP_INTERVAL = int(os.environ.get("LOOP_INTERVAL", "30"))
HEARTBEAT_INTERVAL = int(os.environ.get("HEARTBEAT_INTERVAL", "5"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("kline_scheduler")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logger.info("K线数据服务调度器启动（当前时间: %s）", datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S"))
logger.info("定时任务：22:00 K线更新 / 07:00 历史补全（均为北京时间）")

# 确保 task_status 表存在
from data.database import DatabaseManager

db = DatabaseManager()
cur = db.conn.cursor()
cur.execute(
    "CREATE TABLE IF NOT EXISTS schema_stockfilter.task_status "
    "(task_name VARCHAR(50) PRIMARY KEY, last_completed_at TIMESTAMP, status VARCHAR(20))"
)
db.conn.commit()
db.close()
logger.info("task_status 表已就绪")

# 当天已触发过的任务（防止同一分钟多次触发）
last_run_kline: str = ""
last_run_backfill: str = ""

while True:
    now = datetime.now(TZ)
    today = now.strftime("%Y-%m-%d")
    hm = now.strftime("%H:%M")

    # 每 HEARTBEAT_INTERVAL 分钟输出一次心跳日志，方便监控
    if now.minute % HEARTBEAT_INTERVAL == 0 and now.second < 30:
        logger.debug("心跳: %s，等待定时触发...", hm)

    if hm == SCHEDULE_KLINE_UPDATE and last_run_kline != today:
        last_run_kline = today
        logger.info("=== 执行 K 线数据更新（22:00 北京）===")
        try:
            subprocess.run(
                [sys.executable, "scripts/data/update_kline_daily.py"],
                check=True,
                timeout=KLINE_UPDATE_TIMEOUT,
            )
            # 写入任务完成标记
            db = DatabaseManager()
            try:
                cur = db.conn.cursor()
                now2 = datetime.now(TZ)
                cur.execute(
                    "INSERT INTO schema_stockfilter.task_status "
                    "(task_name, last_completed_at, status) VALUES (%s, %s, %s) "
                    "ON CONFLICT (task_name) DO UPDATE SET last_completed_at=%s, status=%s",
                    ("kline_update", now2, "completed", now2, "completed"),
                )
                db.conn.commit()
                logger.info("K 线数据更新完成，已写入 task_status")
            finally:
                db.close()
        except Exception as e:
            logger.error("K 线数据更新失败: %s", e, exc_info=True)
        time.sleep(POST_TASK_DELAY)

    elif hm == SCHEDULE_BACKFILL and last_run_backfill != today:
        last_run_backfill = today
        logger.info("=== 执行历史数据补全（07:00 北京）===")
        try:
            subprocess.run(
                [sys.executable, "scripts/data/quick_backfill.py"],
                check=True,
                timeout=BACKFILL_TIMEOUT,
            )
            logger.info("历史数据补全完成")
            # 写入任务完成标记
            db = DatabaseManager()
            try:
                cur = db.conn.cursor()
                now2 = datetime.now(TZ)
                cur.execute(
                    "INSERT INTO schema_stockfilter.task_status "
                    "(task_name, last_completed_at, status) VALUES (%s, %s, %s) "
                    "ON CONFLICT (task_name) DO UPDATE SET last_completed_at=%s, status=%s",
                    ("backfill", now2, "completed", now2, "completed"),
                )
                db.conn.commit()
                logger.info("历史数据补全完成，已写入 task_status")
            finally:
                db.close()
        except Exception as e:
            logger.error("历史数据补全失败: %s", e, exc_info=True)
        time.sleep(POST_TASK_DELAY)

    else:
        time.sleep(LOOP_INTERVAL)