"""
OBPC 超跌反弹策略调度器
每天 22:15(北京) 形态扫描（等待K线更新完成），07:30 补全后重扫，08:10 飞书推送
使用明确的 Asia/Shanghai 时区，防止 Docker 容器时区漂移
"""
import os
import sys
import time
import logging
import subprocess
from datetime import datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

import yaml

TZ = ZoneInfo("Asia/Shanghai")

# 可配置参数（通过环境变量覆盖，默认值合理无需配置）
SCHEDULE_SCAN = os.environ.get("SCHEDULE_SCAN", "22:15")
SCHEDULE_RESCAN = os.environ.get("SCHEDULE_RESCAN", "07:30")
SCHEDULE_PUSH = os.environ.get("SCHEDULE_PUSH", "08:10")
SCAN_TIMEOUT = int(os.environ.get("SCAN_TIMEOUT", "3600"))  # 1小时
PUSH_TIMEOUT = int(os.environ.get("PUSH_TIMEOUT", "600"))  # 10分钟
POST_TASK_DELAY = int(os.environ.get("POST_TASK_DELAY", "90"))
LOOP_INTERVAL = int(os.environ.get("LOOP_INTERVAL", "30"))
FRESHNESS_THRESHOLD = int(os.environ.get("FRESHNESS_THRESHOLD", "3600"))  # 1小时

KLINE_WAIT_TIMEOUT = int(os.environ.get("KLINE_WAIT_TIMEOUT", "1800"))
KLINE_WAIT_INTERVAL = int(os.environ.get("KLINE_WAIT_INTERVAL", 60))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("obpc_scheduler")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.trade_calendar import is_trading_day

logger.info("OBPC 策略调度器启动（当前时间: %s）", datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S"))
logger.info("定时任务：22:15 形态扫描 / 07:30 补全后重扫 / 08:10 飞书推送（均为北京时间）")
logger.info("K线等待超时：%ds，轮询间隔：%ds", KLINE_WAIT_TIMEOUT, KLINE_WAIT_INTERVAL)

# 各时段「当天已触发」守卫（值为自然日，防止同一分钟循环重复进入）
last_run_scan: str = ""
last_run_rescan: str = ""
last_run_push: str = ""
# 已推送的信号日期（值为信号日期，跨天保留，防止同一交易日信号被重复推送）；
# 必须与上面按自然日的守卫分离，否则非交易日跳过分支会把信号日期污染为当天，导致重复推送
last_pushed_signal_date: str = ""


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
            # last_completed_at 为无时区列、DB 会话时区为 UTC、实际存 UTC 裸时间戳，
            # 必须按 UTC 解析并与 UTC 比较，否则与 Asia/Shanghai 差 8 小时导致新鲜度判定永远失败
            ts = row[0] if row[0].tzinfo else row[0].replace(tzinfo=timezone.utc)
            elapsed = (datetime.now(timezone.utc) - ts).total_seconds()
            if elapsed < FRESHNESS_THRESHOLD:
                logger.info("K线更新已完成（%d分钟前）", int(elapsed / 60))
                db.close()
                return True
        waited += KLINE_WAIT_INTERVAL
        logger.info("等待K线更新... (%ds / %ds)", waited, KLINE_WAIT_TIMEOUT)
        time.sleep(KLINE_WAIT_INTERVAL)

    db.close()
    logger.warning("K线更新等待超时（%ds），继续执行扫描", KLINE_WAIT_TIMEOUT)
    return False


def wait_for_backfill() -> bool:
    """轮询 task_status 表，等待历史补全完成"""
    from data.database import DatabaseManager

    db = DatabaseManager()
    deadline = datetime.now(TZ) + timedelta(seconds=KLINE_WAIT_TIMEOUT)
    waited = 0

    while datetime.now(TZ) < deadline:
        cur = db.conn.cursor()
        cur.execute(
            "SELECT last_completed_at FROM schema_stockfilter.task_status WHERE task_name=%s",
            ("backfill",),
        )
        row = cur.fetchone()
        if row and row[0]:
            # last_completed_at 为无时区列、DB 会话时区为 UTC、实际存 UTC 裸时间戳，
            # 必须按 UTC 解析并与 UTC 比较，否则与 Asia/Shanghai 差 8 小时导致新鲜度判定永远失败
            ts = row[0] if row[0].tzinfo else row[0].replace(tzinfo=timezone.utc)
            elapsed = (datetime.now(timezone.utc) - ts).total_seconds()
            if elapsed < FRESHNESS_THRESHOLD:
                logger.info("历史补全已完成（%d分钟前）", int(elapsed / 60))
                db.close()
                return True
        waited += KLINE_WAIT_INTERVAL
        logger.info("等待历史补全... (%ds / %ds)", waited, KLINE_WAIT_TIMEOUT)
        time.sleep(KLINE_WAIT_INTERVAL)

    db.close()
    logger.warning("历史补全等待超时（%ds），跳过重扫", KLINE_WAIT_TIMEOUT)
    return False


def _load_index_code(config_path: str = "config/config.yaml") -> str:
    """从配置读取大盘指数代码，禁止硬编码"""
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        return (
            ((cfg.get("strategies") or {}).get("oversold_bounce") or {})
            .get("params", {})
            .get("index_filter", {})
            .get("index_code", "")
        ) or ""
    except Exception as e:
        logger.warning("读取配置 index_code 失败：%s", e)
        return ""


def get_signal_target_date() -> Optional[str]:
    """取最近一个已完成交易日作为重扫/推送的信号日期基准。

    新鲜度校验（risk_control.get_context）要求「指数最新日期 == signal_date」；
    07:30 重扫与 08:10 推送都发生在当天指数（22:00 才生成）之前，
    因此必须锚定「前一交易日（= 数据库中指数最新日期）」，而非当天，
    否则重扫恒为 0 信号、推送恒找不到信号文件。
    """
    from data.database import DatabaseManager

    index_code = _load_index_code()
    if not index_code:
        return None

    db = DatabaseManager()
    try:
        df = db.get_index_kline(index_code, days=1)
        if df is None or df.empty:
            return None
        last = df["date"].iloc[-1]
        return last.strftime("%Y-%m-%d") if hasattr(last, "strftime") else str(last)[:10]
    finally:
        db.close()


while True:
    now = datetime.now(TZ)
    today = now.strftime("%Y-%m-%d")
    yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    hm = now.strftime("%H:%M")

    if hm == SCHEDULE_SCAN and last_run_scan != today:
        # 扫描时段：仅当天是交易日才执行
        if not is_trading_day(today):
            logger.info("=== %s 非交易日，跳过形态扫描 ===", today)
            last_run_scan = today
            time.sleep(LOOP_INTERVAL)
            continue
        last_run_scan = today
        logger.info("=== 等待 K 线更新 ===")
        wait_for_kline_update()
        logger.info("=== 执行形态扫描（22:15 北京）===")
        try:
            subprocess.run(
                [sys.executable, "scripts/daily_scan.py"],
                check=True,
                timeout=SCAN_TIMEOUT,
            )
            logger.info("形态扫描完成")
        except Exception as e:
            logger.error("形态扫描失败: %s", e, exc_info=True)
        time.sleep(POST_TASK_DELAY)

    elif hm == SCHEDULE_RESCAN and last_run_rescan != today:
        # 重扫时段：仅当天是交易日才执行
        if not is_trading_day(today):
            logger.info("=== %s 非交易日，跳过补全后重扫 ===", today)
            last_run_rescan = today
            time.sleep(LOOP_INTERVAL)
            continue
        last_run_rescan = today
        logger.info("=== 等待历史补全完成 ===")
        backfill_ok = wait_for_backfill()
        if backfill_ok:
            # 信号基准日期 = 前一交易日（= 指数最新日期）；当天指数 22:00 才生成，
            # 用当天会导致新鲜度校验（指数最新日期==signal_date）不通过 → 恒 0 信号
            signal_date = get_signal_target_date()
            if not signal_date:
                logger.warning("指数最新日期缺失，无法确定重扫信号日期，跳过重扫")
            else:
                logger.info("=== 执行补全后重扫（07:30 北京，信号日期=%s）===", signal_date)
                try:
                    subprocess.run(
                        [sys.executable, "scripts/daily_scan.py",
                         "--signal-date", signal_date],
                        check=True,
                        timeout=SCAN_TIMEOUT,
                    )
                    logger.info("补全后重扫完成")
                except subprocess.TimeoutExpired:
                    logger.warning("补全后重扫超时（3600s），跳过")
                except Exception as e:
                    logger.error("补全后重扫失败: %s", e, exc_info=True)
        else:
            logger.warning("历史补全未完成，跳过重扫")
        time.sleep(POST_TASK_DELAY)

    elif hm == SCHEDULE_PUSH and last_run_push != today:
        # 按自然日守卫：本时段当天只进入一次，避免同一分钟循环重复判定
        last_run_push = today
        # 推送时段：当天或前一天任一为交易日都放行
        # 覆盖周五 22:15 扫描 → 周六 08:10 推送的场景
        if not is_trading_day(today) and not is_trading_day(yesterday):
            logger.info("=== %s 和 %s 均为非交易日，跳过飞书推送 ===", yesterday, today)
            time.sleep(LOOP_INTERVAL)
            continue

        # 推送的信号日期 = 前一交易日（= 指数最新日期），与重扫保持一致的语义；
        # 当天指数数据 22:00 才生成，用当天会找不到 signals_{当天}.json。
        # 取不到日期时安全跳过，禁止回退「当天」，否则会复现找不到信号文件的问题
        push_date = get_signal_target_date()
        if not push_date:
            logger.warning("指数最新日期缺失，无法确定推送信号日期，跳过飞书推送")
            time.sleep(LOOP_INTERVAL)
            continue

        # 同一交易日的信号只推送一次（如周六已推周五信号，周一不再重复）
        if last_pushed_signal_date == push_date:
            logger.info("信号日期 %s 已推送过，跳过重复推送", push_date)
            time.sleep(LOOP_INTERVAL)
            continue
        last_pushed_signal_date = push_date
        logger.info("=== 执行飞书推送（08:10 北京，信号日期=%s）===", push_date)
        try:
            subprocess.run(
                [sys.executable, "scripts/feishu_push.py",
                 "--signal-date", push_date],  # C-3：显式指定信号日期
                check=True,
                timeout=PUSH_TIMEOUT,
            )
            logger.info("飞书推送完成")
        except Exception as e:
            logger.error("飞书推送失败: %s", e, exc_info=True)
        time.sleep(POST_TASK_DELAY)

    else:
        time.sleep(LOOP_INTERVAL)