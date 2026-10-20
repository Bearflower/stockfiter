"""
K线数据服务调度器
每天 22:00(北京) K线更新，07:00 历史补全
使用明确的 Asia/Shanghai 时区，防止 Docker 容器时区漂移
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import logging
import subprocess
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Shanghai")

# 可配置参数（通过环境变量覆盖，默认值合理无需配置）
SCHEDULE_KLINE_UPDATE = os.environ.get("SCHEDULE_KLINE_UPDATE", "23:00")   # 2026-10-20 错峰：避开 Baostock 晚间高峰期（原 22:00）
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

# sys.path 注入放到模块级，但其他初始化（DB 建表、全局 import）收进 main()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 交易日历：模块级导入以便单测 monkeypatch 该接缝（该模块自身很轻，Baostock 在其内部延迟导入）
from utils.trade_calendar import nearest_trading_date  # noqa: E402


# ---------------------------------------------------------------------------
# 可被单测直接调用的纯函数（B3.2：将守卫判断抽离）
# ---------------------------------------------------------------------------


def should_skip_kline_update(now: datetime | None = None, today_str: str | None = None) -> bool:
    """判断当前时间是否应跳过 K 线更新（非交易日守卫，纯函数）。

    检查 is_trading_day(now) 返回 False 即跳过。now 为 None 时使用北京时间当前时间。
    这是 2026-10-17 周六事故后新增的前置守卫——非交易日直接跳过整个采集循环，
    避免 success_rate=0 + skipped_ratio 未达 guard 阈值导致的误判 FAIL。

    Args:
        now: datetime（必须带 tzinfo，建议 ZoneInfo("Asia/Shanghai")），None 时取当前北京时间
        today_str: 可选，调用方已算出的 today 字符串，避免重复 strftime

    Returns:
        True  = 应跳过（非交易日）
        False = 不应跳过（交易日）
    """
    from utils.trade_calendar import is_trading_day

    if now is None:
        now = datetime.now(TZ)
    if today_str is None:
        today_str = now.strftime("%Y-%m-%d")

    return not is_trading_day(today_str)


def compute_target_date_for(today: datetime) -> tuple[str | None, bool]:
    """计算目标交易日 = 最近一个 ≤ today 的交易日（薄包装，复用 update_kline_daily 的实现）。

    Returns:
        tuple[str | None, bool]: (target_date, is_degraded)
            target_date 可能为 None（回溯窗口内无交易日，由调用方按 guard 语义处理）
            is_degraded=True 表示 is_trading_day 走了 weekday 启发式降级
    """
    from scripts.data.update_kline_daily import DailyKlineUpdater
    return DailyKlineUpdater._compute_target_date(today.date())


def compute_backfill_target_date(now: datetime) -> tuple[str | None, bool]:
    """计算 07:00 历史补全的目标交易日 = 最近一个「已收盘」的交易日（纯函数）。

    与 22:00 K 线更新分支的区别（R-02 日期错配修复）：
      - 22:00 分支用 compute_target_date_for(now)，语义是「最近一个 ≤ today 的交易日」，
        因为当日收盘后要采集的正是当天 K 线，target_date 应等于当天（today）。
      - 07:00 补全分支发生在次日早晨，当天 K 线尚未生成，能被补全的只能是
        「最近一个已收盘的交易日」——即严格早于 today 的最近交易日。

    为什么必须严格早于 today：
      07:00 时 today 本身若为交易日，用 compute_target_date_for(now) 会把 target_date
      写成当天，而当天数据尚不存在，与 OBPC 07:30 侧取用的「最近已收盘交易日」
      （= 最近一个已收盘交易日，通常是前一交易日）语义不一致。OBPC 的
      task_status_allows_run 要求 target_date 完全相等，错配会导致 07:30 等 1800s
      超时后把 backfill_status 强制成 "not_ready" 并跳过重扫。

    实现直接委托 utils.trade_calendar.nearest_trading_date(now, inclusive=False)，
    与 OBPC 07:30 重扫 / 08:10 推送取用的日期**同一实现、同一回溯窗口**，
    从根上消除「两处用不同来源算同一天」的错配（R-02）。

    为什么不复用 compute_target_date_for(now - 1 天)：那条路径内部走
    DailyKlineUpdater._compute_target_date 的 max_backtrack=7，与 trade_calendar 的
    MAX_LOOKBACK_DAYS=30 不一致——遇到超过 7 天不交易的连续长假（如春节 + 周末）时，
    它可能返回 None，而 OBPC 侧仍能算出假期前的交易日，于是 target_date 又对不上、
    07:30 重扫被 fail-closed 阻断。现在两侧共用同一函数，长假期也不分叉。

    Args:
        now: datetime（带 tzinfo），通常为 07:00 触发时刻

    Returns:
        tuple[str | None, bool]: (target_date, is_degraded)
            target_date 为「最近已收盘交易日」，可能为 None（回溯窗口内无交易日）
            is_degraded=True 表示 is_trading_day 走了 weekday 启发式降级
    """
    return nearest_trading_date(now, inclusive=False)


# ---------------------------------------------------------------------------
# DB 写入辅助
# ---------------------------------------------------------------------------


def _write_task_status(
    task_name: str,
    status: str,
    target_date: str | None = None,
    details: dict | None = None,
) -> None:
    """原子写入 task_status（R-01：携带 target_date 与 details + P1 修复 rollback）。

    P1-3/P1-4：内层 except 必须显式 rollback()，防止新列写入失败让整个事务中毒、
    连基础 INSERT 一起被回滚；日志级别从 debug 升级到 warning 便于运维可见。

    Args:
        task_name: 任务名（kline_update / backfill）
        status: completed / failed
        target_date: 目标交易日字符串 "YYYY-MM-DD"，可为 None
        details: UpdateResult 详情字典，可为 None 或空
    """
    from data.database import DatabaseManager

    db = None
    try:
        db = DatabaseManager()
        cur = db.conn.cursor()
        now2 = datetime.now(TZ)
        # 写入基础列（向后兼容：新列不存在时只写基础列）
        cur.execute(
            "INSERT INTO schema_stockfilter.task_status "
            "(task_name, last_completed_at, status) VALUES (%s, %s, %s) "
            "ON CONFLICT (task_name) DO UPDATE SET last_completed_at=%s, status=%s",
            (task_name, now2, status, now2, status),
        )
        # 尝试写入新列（target_date + details）
        try:
            details_json = json.dumps(details or {}, ensure_ascii=False)
            cur.execute(
                "UPDATE schema_stockfilter.task_status "
                "SET target_date = %s, details = %s "
                "WHERE task_name = %s",
                (target_date, details_json, task_name),
            )
        except Exception as col_err:
            # P1-4：显式回滚，防止新列写入失败让整个事务中毒、
            # 连已经成功执行的基础 INSERT/UPDATE 一起丢失
            try:
                db.conn.rollback()
                # 回滚后重新执行基础列写入（确保至少基础数据落库）
                cur.execute(
                    "INSERT INTO schema_stockfilter.task_status "
                    "(task_name, last_completed_at, status) VALUES (%s, %s, %s) "
                    "ON CONFLICT (task_name) DO UPDATE SET last_completed_at=%s, status=%s",
                    (task_name, now2, status, now2, status),
                )
                db.conn.commit()
            except Exception as rollback_err:
                logger.error(
                    "回滚后重写基础 task_status 仍失败：%s", rollback_err, exc_info=True
                )
                return
            # 日志升级到 WARNING + 中文，让运维默认能看到
            logger.warning(
                "写入 target_date/details 列失败（可能尚未迁移，已回滚并重写基础列）：%s",
                col_err,
            )
            logger.info(
                "task_status 写入（基础列 only）：%s → %s (target_date=%s)",
                task_name, status, target_date,
            )
            return

        db.conn.commit()
        logger.info(
            "task_status 写入：%s → %s (target_date=%s)",
            task_name, status, target_date,
        )
    except Exception as e:
        logger.error("写入 task_status 失败：%s", e, exc_info=True)
    finally:
        if db is not None:
            try:
                db.close()
            except Exception:
                pass


def _ensure_task_status_table() -> None:
    """确保 task_status 表存在（幂等）。"""
    from data.database import DatabaseManager

    db = DatabaseManager()
    try:
        cur = db.conn.cursor()
        cur.execute(
            "CREATE TABLE IF NOT EXISTS schema_stockfilter.task_status "
            "(task_name VARCHAR(50) PRIMARY KEY, last_completed_at TIMESTAMP, status VARCHAR(20))"
        )
        db.conn.commit()
    finally:
        db.close()
    logger.info("task_status 表已就绪")


# ---------------------------------------------------------------------------
# 调度循环（main 化后可安全 import 单测）
# ---------------------------------------------------------------------------


def main() -> None:
    """调度器主循环。

    B3.2：将模块级循环收进 main()，让测试 import 时不会自动进入 while True。
    容器启动命令 `python -u scripts/scheduler_kline.py` 由 __main__ 触发 main()，
    行为与改造前完全一致。
    """
    # 注：is_trading_day 由 should_skip_kline_update 内部延迟导入，main() 不直接引用

    logger.info("K线数据服务调度器启动（当前时间: %s）", datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S"))
    logger.info("定时任务：23:00 K线更新（错峰 Baostock 晚间高峰）/ 07:00 历史补全（均为北京时间）")

    _ensure_task_status_table()

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

            # 非交易日守卫：纯函数调用（B3.2 抽出，便于单测）
            if should_skip_kline_update(now=now, today_str=today):
                logger.info("=== %s 非交易日，跳过 K 线数据更新（23:00 北京）===", today)
                time.sleep(LOOP_INTERVAL)
                continue

            logger.info("=== 执行 K 线数据更新（23:00 北京）===")

            # R13 新增：指数更新前置，独立 try/except，失败记日志但不阻断 K 线更新
            try:
                # capture_output=False + stdout/stderr=None 让日志实时输出到 docker logs
                index_ret = subprocess.run(
                    [sys.executable, "scripts/data/index_update.py"],
                    stdout=None,
                    stderr=None,
                    timeout=600,
                )
                if index_ret.returncode != 0:
                    logger.warning(
                        "指数更新返回非零退出码 %d（详细错误见上方实时日志）",
                        index_ret.returncode,
                    )
                else:
                    logger.info("指数更新完成")
            except subprocess.TimeoutExpired:
                logger.warning("指数更新超时（600s），跳过并继续 K 线更新")
            except Exception as e:
                logger.warning("指数更新异常，不阻断 K 线更新：%s", e)

            # P1-1：先算好 target_date，超时/异常分支也要用（之前漏传了）
            target_date, td_degraded = compute_target_date_for(now)
            logger.info(
                "目标交易日：%s（is_trading_day 可信度：%s）",
                target_date,
                "weekday 降级" if td_degraded else "Baostock 已验证",
            )

            try:
                # 用临时文件接收子进程的 UpdateResult 详情（Baostock 挂起隔离 → 保持 subprocess）
                result_json_path = tempfile.mktemp(
                    prefix=f"kline_result_{today}_", suffix=".json"
                )

                # R13 + R-01：子进程隔离 + --result-json 输出详情
                # capture_output=False + stdout/stderr=None 让日志实时输出到 docker logs
                result = subprocess.run(
                    [
                        sys.executable,
                        "scripts/data/update_kline_daily.py",
                        "--result-json", result_json_path,
                    ],
                    stdout=None,
                    stderr=None,
                    timeout=KLINE_UPDATE_TIMEOUT,
                )

                # 读取子进程输出的 details（读不到时 details={}）
                details: dict = {}
                if os.path.exists(result_json_path):
                    try:
                        with open(result_json_path, 'r', encoding='utf-8') as f:
                            details = json.load(f)
                    except Exception as je:
                        logger.warning("读取 result-json 失败：%s", je)
                    finally:
                        # 清理临时文件
                        try:
                            os.unlink(result_json_path)
                        except Exception:
                            pass

                if result.returncode == 0:
                    logger.info("K 线数据更新完成（完整性 PASS）")
                    _write_task_status("kline_update", "completed", target_date, details)
                else:
                    # R13 + R-01：完整性判定 FAIL（处理覆盖率/目标日覆盖率不足等）
                    logger.error(
                        "K 线数据更新完整性判定 FAIL（退出码 %d，details=%s）",
                        result.returncode, details,
                    )
                    _write_task_status("kline_update", "failed", target_date, details)
            except subprocess.TimeoutExpired:
                logger.error(
                    "K 线数据更新超时（>%ds），视为失败", KLINE_UPDATE_TIMEOUT, exc_info=True
                )
                # P1-2：补传 target_date，与正常失败分支语义一致
                _write_task_status("kline_update", "failed", target_date, {})
            except Exception as e:
                logger.error("K 线数据更新异常：%s", e, exc_info=True)
                # P1-2：补传 target_date
                _write_task_status("kline_update", "failed", target_date, {})
            time.sleep(POST_TASK_DELAY)

        elif hm == SCHEDULE_BACKFILL and last_run_backfill != today:
            last_run_backfill = today
            logger.info("=== 执行历史数据补全（07:00 北京）===")

            # R-02 修复：07:00 补全的目标日 = 最近一个「已收盘」的交易日（严格早于 today），
            # 与 OBPC 07:30 重扫/08:10 推送取用的「最近已收盘交易日」是同源实现；不再用当天
            # （当天 K 线尚未生成），也不用指数表最新日期（指数更新失败时会漂移）。
            target_date, td_deg = compute_backfill_target_date(now)
            logger.info(
                "历史补全目标日（最近已收盘交易日）：%s（is_trading_day 可信度：%s）",
                target_date,
                "weekday 降级" if td_deg else "Baostock 已验证",
            )

            try:
                # 同样不 capture_output，让补全日志实时输出到 docker logs
                result = subprocess.run(
                    [sys.executable, "scripts/data/quick_backfill.py"],
                    stdout=None,
                    stderr=None,
                    timeout=BACKFILL_TIMEOUT,
                )
                if result.returncode == 0:
                    logger.info("历史数据补全完成")
                    _write_task_status("backfill", "completed", target_date, {})
                else:
                    logger.error(
                        "历史数据补全返回非零退出码 %d",
                        result.returncode,
                    )
                    _write_task_status("backfill", "failed", target_date, {})
            except subprocess.TimeoutExpired:
                logger.error("历史数据补全超时（>%ds）", BACKFILL_TIMEOUT, exc_info=True)
                _write_task_status("backfill", "failed", target_date, {})
            except Exception as e:
                logger.error("历史数据补全失败: %s", e, exc_info=True)
                _write_task_status("backfill", "failed", target_date, {})
            time.sleep(POST_TASK_DELAY)

        else:
            time.sleep(LOOP_INTERVAL)


if __name__ == "__main__":
    main()
