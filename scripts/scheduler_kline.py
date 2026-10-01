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


def _write_task_status(task_name: str, status: str) -> None:
    """原子写入 task_status（R13：成功/失败都写状态，不再静默吞异常）。"""
    db = DatabaseManager()
    try:
        cur = db.conn.cursor()
        now2 = datetime.now(TZ)
        cur.execute(
            "INSERT INTO schema_stockfilter.task_status "
            "(task_name, last_completed_at, status) VALUES (%s, %s, %s) "
            "ON CONFLICT (task_name) DO UPDATE SET last_completed_at=%s, status=%s",
            (task_name, now2, status, now2, status),
        )
        db.conn.commit()
        logger.info("task_status 写入：%s → %s", task_name, status)
    except Exception as e:
        logger.error("写入 task_status 失败：%s", e, exc_info=True)
    finally:
        db.close()


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

        # R13 新增：指数更新前置，独立 try/except，失败记日志但不阻断 K 线更新
        try:
            index_ret = subprocess.run(
                [sys.executable, "scripts/data/index_update.py"],
                capture_output=True,
                text=True,
                timeout=600,
            )
            if index_ret.returncode != 0:
                logger.warning(
                    "指数更新返回非零退出码 %d，stderr 片段：%s",
                    index_ret.returncode,
                    (index_ret.stderr or "")[:300],
                )
            else:
                logger.info("指数更新完成")
        except subprocess.TimeoutExpired:
            logger.warning("指数更新超时（600s），跳过并继续 K 线更新")
        except Exception as e:
            logger.warning("指数更新异常，不阻断 K 线更新：%s", e)

        try:
            # R13：kline_update.py main() 现在返回有意义的退出码
            # 用 capture_output=False + stdout/stderr=None 让子进程日志实时输出到 docker logs
            # 之前 capture_output=True 会缓冲 3 小时卡死期间所有日志，进程退出后才一次性读取
            result = subprocess.run(
                [sys.executable, "scripts/data/update_kline_daily.py"],
                stdout=None,  # 继承父进程 stdout → 实时输出到 docker logs
                stderr=None,  # 继承父进程 stderr
                timeout=KLINE_UPDATE_TIMEOUT,
            )
            if result.returncode == 0:
                logger.info("K 线数据更新完成（完整性 PASS）")
                _write_task_status("kline_update", "completed")
            else:
                # R13：完整性判定失败（success_rate < min_success_rate 或 failed > total_limit）
                # 写 failed 状态，下游 scheduler_obpc.py 应据此阻断扫描
                logger.error(
                    "K 线数据更新完整性判定 FAIL（退出码 %d）",
                    result.returncode,
                )
                _write_task_status("kline_update", "failed")
        except subprocess.TimeoutExpired:
            logger.error(
                "K 线数据更新超时（>%ds），视为失败", KLINE_UPDATE_TIMEOUT, exc_info=True
            )
            _write_task_status("kline_update", "failed")
        except Exception as e:
            logger.error("K 线数据更新异常：%s", e, exc_info=True)
            _write_task_status("kline_update", "failed")
        time.sleep(POST_TASK_DELAY)

    elif hm == SCHEDULE_BACKFILL and last_run_backfill != today:
        last_run_backfill = today
        logger.info("=== 执行历史数据补全（07:00 北京）===")
        try:
            # 同样不 capture_output，让补全日志实时输出到 docker logs
            result = subprocess.run(
                [sys.executable, "scripts/data/quick_backfill.py"],
                stdout=None,  # 继承父进程 stdout → 实时输出到 docker logs
                stderr=None,
                timeout=BACKFILL_TIMEOUT,
            )
            if result.returncode == 0:
                logger.info("历史数据补全完成")
                _write_task_status("backfill", "completed")
            else:
                logger.error(
                    "历史数据补全返回非零退出码 %d",
                    result.returncode,
                )
                _write_task_status("backfill", "failed")
        except subprocess.TimeoutExpired:
            logger.error("历史数据补全超时（>%ds）", BACKFILL_TIMEOUT, exc_info=True)
            _write_task_status("backfill", "failed")
        except Exception as e:
            logger.error("历史数据补全失败: %s", e, exc_info=True)
            _write_task_status("backfill", "failed")
        time.sleep(POST_TASK_DELAY)

    else:
        time.sleep(LOOP_INTERVAL)
