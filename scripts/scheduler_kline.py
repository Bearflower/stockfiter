"""
K线数据服务调度器
每天 22:00(北京) K线更新，10:00 历史补全
使用明确的 Asia/Shanghai 时区，防止 Docker 容器时区漂移
"""
import os
import sys
import time
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Shanghai")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("kline_scheduler")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logger.info("K线数据服务调度器启动（当前时间: %s）", datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S"))
logger.info("定时任务：22:00 K线更新 / 10:00 历史补全（均为北京时间）")

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

    # 每 5 分钟输出一次心跳日志，方便监控
    if now.minute % 5 == 0 and now.second < 30:
        logger.debug("心跳: %s，等待定时触发...", hm)

    if hm == "22:00" and last_run_kline != today:
        last_run_kline = today
        logger.info("=== 执行 K 线数据更新（22:00 北京）===")
        try:
            import subprocess

            subprocess.run(
                [sys.executable, "scripts/data/update_kline_daily.py"],
                check=True,
                timeout=10800,  # 3小时，全量股票更新耗时长
            )
            # 写入任务完成标记
            db = DatabaseManager()
            cur = db.conn.cursor()
            now2 = datetime.now(TZ)
            cur.execute(
                "INSERT INTO schema_stockfilter.task_status "
                "(task_name, last_completed_at, status) VALUES (%s, %s, %s) "
                "ON CONFLICT (task_name) DO UPDATE SET last_completed_at=%s, status=%s",
                ("kline_update", now2, "completed", now2, "completed"),
            )
            db.conn.commit()
            db.close()
            logger.info("K 线数据更新完成，已写入 task_status")
        except Exception as e:
            logger.error("K 线数据更新失败: %s", e, exc_info=True)
        time.sleep(90)

    elif hm == "10:00" and last_run_backfill != today:
        last_run_backfill = today
        logger.info("=== 执行历史数据补全（10:00 北京）===")
        try:
            import subprocess

            subprocess.run(
                [sys.executable, "scripts/data/quick_backfill.py"],
                check=True,
                timeout=7200,
            )
            logger.info("历史数据补全完成")
        except Exception as e:
            logger.error("历史数据补全失败: %s", e, exc_info=True)
        time.sleep(90)

    else:
        time.sleep(30)