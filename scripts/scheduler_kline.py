"""
K线数据服务调度器
每天 23:00(北京) K线更新，07:00 历史补全
使用明确的 Asia/Shanghai 时区，防止 Docker 容器时区漂移
"""
from __future__ import annotations

import os
import re
import sys
import time
import logging
import multiprocessing
import subprocess
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Shanghai")

# 可配置参数（通过环境变量覆盖，默认值合理无需配置）
SCHEDULE_KLINE_UPDATE = os.environ.get("SCHEDULE_KLINE_UPDATE", "23:00")   # 2026-10-20 错峰：避开 Baostock 晚间高峰期（原 22:00）
SCHEDULE_BACKFILL = os.environ.get("SCHEDULE_BACKFILL", "07:00")
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


def load_kline_update_cfg() -> dict:
    """读取 global.kline_update 配置（与 update_kline_daily 同一加载器，避免双源）。"""
    from scripts.data.update_kline_daily import load_kline_update_config
    return load_kline_update_config()


# ---------------------------------------------------------------------------
# DB 写入辅助
# ---------------------------------------------------------------------------


def _connect_task_status_db(
    *,
    db_connect_timeout_seconds: int,
    db_query_timeout_seconds: int,
):
    """建立 task_status 专用轻量连接，不执行建表或迁移。"""
    import psycopg2

    password = os.getenv("DB_PASSWORD")
    if not password:
        raise RuntimeError("必须通过环境变量 DB_PASSWORD 配置数据库密码")
    schema = os.getenv("DB_SCHEMA", "schema_stockfilter")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", schema):
        raise RuntimeError(f"DB_SCHEMA 非法：{schema!r}")
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=os.getenv("DB_PORT", "5432"),
        database=os.getenv("DB_NAME", "stockfilter"),
        user=os.getenv("DB_USER", "stockfilter_user"),
        password=password,
        connect_timeout=int(db_connect_timeout_seconds),
        options=(
            f"-c search_path={schema} "
            f"-c statement_timeout={int(db_query_timeout_seconds) * 1000}"
        ),
    )


def _is_retryable_task_status_error(exc: Exception) -> bool:
    """只重试连接/锁/事务瞬时错误，确定性 schema/权限错误立即失败。"""
    import psycopg2

    return isinstance(
        exc,
        (
            psycopg2.OperationalError,
            psycopg2.InterfaceError,
            psycopg2.errors.QueryCanceled,
            psycopg2.errors.LockNotAvailable,
            psycopg2.errors.DeadlockDetected,
            psycopg2.errors.SerializationFailure,
        ),
    )


def _write_task_status_once(
    task_name: str,
    status: str,
    target_date: str | None = None,
    details: dict | None = None,
    *,
    db_connect_timeout_seconds: int,
    db_query_timeout_seconds: int,
) -> tuple[bool, bool, str]:
    """在当前进程执行一次原子 UPSERT，返回成功/可重试/错误摘要。"""
    from psycopg2.extras import Json

    conn = None
    cur = None
    try:
        conn = _connect_task_status_db(
            db_connect_timeout_seconds=db_connect_timeout_seconds,
            db_query_timeout_seconds=db_query_timeout_seconds,
        )
        cur = conn.cursor()
        now2 = datetime.now(TZ)
        cur.execute(
            "INSERT INTO task_status "
            "(task_name, last_completed_at, status, target_date, details) "
            "VALUES (%s, %s, %s, %s, %s) "
            "ON CONFLICT (task_name) DO UPDATE SET "
            "last_completed_at = EXCLUDED.last_completed_at, "
            "status = EXCLUDED.status, target_date = EXCLUDED.target_date, "
            "details = EXCLUDED.details",
            (task_name, now2, status, target_date, Json(details or {})),
        )
        conn.commit()
        return True, False, ""
    except Exception as exc:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        return False, _is_retryable_task_status_error(exc), (
            f"{type(exc).__name__}: {str(exc)[:500]}"
        )
    finally:
        if cur is not None:
            try:
                cur.close()
            except Exception:
                pass
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def _task_status_write_child(send_conn, kwargs: dict) -> None:
    """子进程入口：数据库驱动即使卡死也可由父进程整体终止。"""
    try:
        send_conn.send(_write_task_status_once(**kwargs))
    except BaseException as exc:  # 子进程必须把非预期终止转成父进程可见结果
        try:
            send_conn.send((False, True, f"child_error:{type(exc).__name__}:{exc}"))
        except Exception:
            pass
    finally:
        send_conn.close()


def _terminate_status_process(process, grace_seconds: float) -> bool:
    """有界终止状态写入子进程，返回 PID 是否已回收。"""
    process.terminate()
    process.join(timeout=grace_seconds)
    if process.is_alive():
        process.kill()
        process.join(timeout=grace_seconds)
    return not process.is_alive()


def _run_task_status_attempt(
    *,
    task_name: str,
    status: str,
    target_date: str | None,
    details: dict | None,
    db_connect_timeout_seconds: int,
    db_query_timeout_seconds: int,
    attempt_timeout_seconds: float,
    process_terminate_grace_seconds: float,
) -> tuple[bool, bool, str]:
    """隔离执行一次状态写入；超过单次 deadline 后终止整个子进程。"""
    start_method = (
        "fork" if "fork" in multiprocessing.get_all_start_methods() else "spawn"
    )
    context = multiprocessing.get_context(start_method)
    recv_conn, send_conn = context.Pipe(duplex=False)
    kwargs = {
        "task_name": task_name,
        "status": status,
        "target_date": target_date,
        "details": details,
        "db_connect_timeout_seconds": db_connect_timeout_seconds,
        "db_query_timeout_seconds": db_query_timeout_seconds,
    }
    process = context.Process(
        target=_task_status_write_child,
        args=(send_conn, kwargs),
        name=f"task-status-{task_name}",
    )
    try:
        process.start()
        send_conn.close()
        process.join(timeout=max(0.0, attempt_timeout_seconds))
        if process.is_alive():
            pid_gone = _terminate_status_process(
                process, process_terminate_grace_seconds
            )
            suffix = "" if pid_gone else f" pid_not_reaped={process.pid}"
            return False, pid_gone, f"attempt_timeout{suffix}"
        if recv_conn.poll():
            result = recv_conn.recv()
            if (
                isinstance(result, tuple)
                and len(result) == 3
                and isinstance(result[0], bool)
                and isinstance(result[1], bool)
                and isinstance(result[2], str)
            ):
                return result
        return False, True, f"child_result_missing exit_code={process.exitcode}"
    except Exception as exc:
        # 任意进程/IPC 异常都必须先确认旧 PID 已回收。无法确认时按未回收处理，
        # 禁止重试，避免两个状态写入进程并发操作同一任务行。
        try:
            process_alive = process.is_alive()
        except Exception:
            process_alive = True
        pid_gone = not process_alive
        if process_alive:
            try:
                pid_gone = _terminate_status_process(
                    process, process_terminate_grace_seconds
                )
            except Exception:
                pid_gone = False
        suffix = "" if pid_gone else f" pid_not_reaped={process.pid}"
        return (
            False,
            pid_gone,
            f"process_error:{type(exc).__name__}:{exc}{suffix}",
        )
    finally:
        try:
            send_conn.close()
        except Exception:
            pass
        recv_conn.close()
        try:
            if process.pid is not None and not process.is_alive():
                process.close()
        except Exception:
            pass


def _write_task_status(
    task_name: str,
    status: str,
    target_date: str | None = None,
    details: dict | None = None,
    *,
    db_connect_timeout_seconds: int,
    db_query_timeout_seconds: int,
    task_status_write_retries: int,
    task_status_write_retry_delay_seconds: int,
    task_status_process_terminate_grace_seconds: int,
) -> bool:
    """在独立子进程中原子写入 task_status，并由父进程执行硬 deadline。

    Args:
        task_name: 任务名（kline_update / backfill）
        status: completed / failed
        target_date: 目标交易日字符串 "YYYY-MM-DD"，可为 None
        details: UpdateResult 详情字典，可为 None 或空
    """
    attempt_timeout = db_connect_timeout_seconds + db_query_timeout_seconds
    total_budget = (
        task_status_write_retries
        * (attempt_timeout + 2 * task_status_process_terminate_grace_seconds)
        + (task_status_write_retries - 1)
        * task_status_write_retry_delay_seconds
    )
    deadline = time.monotonic() + total_budget
    for attempt in range(1, task_status_write_retries + 1):
        remaining = deadline - time.monotonic()
        if remaining <= 2 * task_status_process_terminate_grace_seconds:
            logger.critical(
                "task_status 总写入预算耗尽：task=%s target_date=%s attempt=%d/%d",
                task_name, target_date, attempt, task_status_write_retries,
            )
            return False
        success, retryable, error = _run_task_status_attempt(
            task_name=task_name,
            status=status,
            target_date=target_date,
            details=details,
            db_connect_timeout_seconds=db_connect_timeout_seconds,
            db_query_timeout_seconds=db_query_timeout_seconds,
            attempt_timeout_seconds=min(
                attempt_timeout,
                remaining - 2 * task_status_process_terminate_grace_seconds,
            ),
            process_terminate_grace_seconds=(
                task_status_process_terminate_grace_seconds
            ),
        )
        if success:
            logger.info(
                "task_status 写入：%s → %s (target_date=%s, attempt=%d)",
                task_name, status, target_date, attempt,
            )
            return True
        if not retryable or attempt >= task_status_write_retries:
            logger.critical(
                "task_status 最终写入失败：task=%s status=%s target_date=%s "
                "completion_mode=%s attempt=%d/%d error=%s",
                task_name,
                status,
                target_date,
                (details or {}).get("completion_mode", "unknown"),
                attempt,
                task_status_write_retries,
                error,
            )
            return False
        logger.warning(
            "task_status 瞬时写入失败，准备重连：task=%s target_date=%s "
            "attempt=%d/%d error=%s",
            task_name, target_date, attempt, task_status_write_retries, error,
        )
        sleep_seconds = min(
            task_status_write_retry_delay_seconds,
            max(0.0, deadline - time.monotonic()),
        )
        if sleep_seconds > 0:
            time.sleep(sleep_seconds)
    return False


def _record_task_status(
    task_name: str,
    status: str,
    target_date: str | None,
    details: dict | None,
    collection_cfg,
) -> bool:
    """调用方闭环：最终失败必须携带本轮业务结果记录 CRITICAL。"""
    written = _write_task_status(
        task_name,
        status,
        target_date,
        details,
        db_connect_timeout_seconds=collection_cfg.db_connect_timeout_seconds,
        db_query_timeout_seconds=collection_cfg.db_query_timeout_seconds,
        task_status_write_retries=collection_cfg.task_status_write_retries,
        task_status_write_retry_delay_seconds=(
            collection_cfg.task_status_write_retry_delay_seconds
        ),
        task_status_process_terminate_grace_seconds=(
            collection_cfg.task_status_process_terminate_grace_seconds
        ),
    )
    if not written:
        logger.critical(
            "本轮业务已结束但 task_status 未落库：task=%s target_date=%s "
            "business_status=%s completion_mode=%s",
            task_name,
            target_date,
            status,
            (details or {}).get("completion_mode", "unknown"),
        )
    return written


def _ensure_task_status_table(
    *,
    db_connect_timeout_seconds: int,
    db_query_timeout_seconds: int,
) -> None:
    """启动期准备完整 task_status schema；运行期写入不再执行 DDL。"""
    conn = _connect_task_status_db(
        db_connect_timeout_seconds=db_connect_timeout_seconds,
        db_query_timeout_seconds=db_query_timeout_seconds,
    )
    cur = None
    try:
        cur = conn.cursor()
        cur.execute(
            "CREATE TABLE IF NOT EXISTS task_status ("
            "task_name VARCHAR(50) PRIMARY KEY, last_completed_at TIMESTAMP, "
            "status VARCHAR(20), target_date DATE, "
            "details JSONB NOT NULL DEFAULT '{}'::jsonb)"
        )
        cur.execute(
            "ALTER TABLE task_status "
            "ADD COLUMN IF NOT EXISTS target_date DATE, "
            "ADD COLUMN IF NOT EXISTS details JSONB NOT NULL DEFAULT '{}'::jsonb"
        )
        conn.commit()
    finally:
        if cur is not None:
            cur.close()
        conn.close()
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

    # 必须在任何数据库访问前完成全量配置校验；否则配置错误时仅构造
    # DatabaseManager 就可能被建连/迁移卡住，违背 fail-closed 入口约束。
    from scripts.data.kline_collection import (
        compute_collection_deadline,
        load_collection_config,
    )

    try:
        cfg = load_kline_update_cfg()
        collection_cfg = load_collection_config(cfg)
    except Exception as e:
        logger.critical("K 线配置非法，调度器拒绝启动且不访问数据库：%s", e)
        return

    _ensure_task_status_table(
        db_connect_timeout_seconds=collection_cfg.db_connect_timeout_seconds,
        db_query_timeout_seconds=collection_cfg.db_query_timeout_seconds,
    )
    # 全库迁移只在服务启动时执行一次；后续采集、扫描和监控连接默认不做 DDL。
    from data.database import DatabaseManager

    startup_db = DatabaseManager(
        connect_timeout=collection_cfg.db_connect_timeout_seconds,
        statement_timeout=collection_cfg.db_query_timeout_seconds,
        initialize_schema=True,
    )
    startup_db.close()

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

            # 配置文件可能通过挂载在进程运行期间更新；每次任务仍需在指数更新、
            # 状态写入等任何外部动作前重新加载并完整校验。
            try:
                cfg = load_kline_update_cfg()
                collection_cfg = load_collection_config(cfg)
            except Exception as e:
                logger.critical("K 线配置非法，本轮拒绝执行且不访问数据库：%s", e)
                time.sleep(POST_TASK_DELAY)
                continue

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

            # 2026-10-22 改造：23:00 分支在进程内运行监督者，
            # 采集由子进程分片执行（超时 → 终止 → 数据库断点续采）。
            # 不再使用 3 小时外层 subprocess 超时，整体截止时间由统一公式推导：
            #   initial_scan_time + KLINE_WAIT_TIMEOUT - recovery_deadline_buffer_seconds
            try:
                from scripts.data.update_kline_daily import (
                    build_task_status_details,
                    run_update as kline_run_update,
                )

                deadline = compute_collection_deadline(
                    now,
                    collection_cfg.initial_scan_time,
                    collection_cfg.kline_wait_timeout_seconds,
                    collection_cfg.recovery_deadline_buffer_seconds,
                    strict=True,
                )

                logger.info(
                    f"分片采集截止时间：{deadline.strftime('%Y-%m-%d %H:%M:%S')}"
                    f"（初扫 {collection_cfg.initial_scan_time}，"
                    f"等待 {collection_cfg.kline_wait_timeout_seconds}s，"
                    f"预留 {collection_cfg.recovery_deadline_buffer_seconds}s）"
                )

                result = kline_run_update(cfg=cfg, deadline=deadline)

                complete = result.is_complete(
                    cfg['min_success_rate'],
                    cfg['total_failure_limit'],
                    cfg['min_coverage_rate'],
                    target_date=result.target_date,
                    skipped_ratio_guard=cfg['skipped_ratio_guard'],
                )
                # 只补充模式与真实覆盖率口径；不得用处理比例覆盖 details.coverage_rate
                details = build_task_status_details(result)
                effective_target_date = result.target_date or target_date

                if complete:
                    logger.info(
                        f"K 线数据更新完成（完整性 PASS）：mode={result.completion_mode} "
                        f"覆盖 {result.target_date_success}/{result.total}"
                        f"（{result.target_date_coverage_rate * 100:.2f}%）"
                    )
                    _record_task_status(
                        "kline_update", "completed", effective_target_date, details,
                        collection_cfg,
                    )
                else:
                    logger.error(
                        f"K 线数据更新完整性判定 FAIL（mode={result.completion_mode} "
                        f"stop={details.get('stop_reason')} 覆盖 "
                        f"{result.target_date_success}/{result.total}，"
                        f"{result.target_date_coverage_rate * 100:.2f}%）"
                    )
                    _record_task_status(
                        "kline_update", "failed", effective_target_date, details,
                        collection_cfg,
                    )
            except Exception as e:
                logger.error("K 线数据更新异常：%s", e, exc_info=True)
                _record_task_status(
                    "kline_update", "failed", target_date,
                    {
                        "collection_mode": "chunked",
                        "completion_mode": "failed",
                        "stop_reason": "exception",
                        "error": str(e),
                        "target_date": target_date,
                    },
                    collection_cfg,
                )
            time.sleep(POST_TASK_DELAY)

        elif hm == SCHEDULE_BACKFILL and last_run_backfill != today:
            last_run_backfill = today

            try:
                cfg = load_kline_update_cfg()
                collection_cfg = load_collection_config(cfg)
            except Exception as e:
                logger.critical("K 线配置非法，本轮补全拒绝执行且不访问数据库：%s", e)
                time.sleep(POST_TASK_DELAY)
                continue
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
                    _record_task_status(
                        "backfill", "completed", target_date, {}, collection_cfg,
                    )
                else:
                    logger.error(
                        "历史数据补全返回非零退出码 %d",
                        result.returncode,
                    )
                    _record_task_status(
                        "backfill", "failed", target_date, {}, collection_cfg,
                    )
            except subprocess.TimeoutExpired:
                logger.error("历史数据补全超时（>%ds）", BACKFILL_TIMEOUT, exc_info=True)
                _record_task_status(
                    "backfill", "failed", target_date, {}, collection_cfg,
                )
            except Exception as e:
                logger.error("历史数据补全失败: %s", e, exc_info=True)
                _record_task_status(
                    "backfill", "failed", target_date, {}, collection_cfg,
                )
            time.sleep(POST_TASK_DELAY)

        else:
            time.sleep(LOOP_INTERVAL)


if __name__ == "__main__":
    main()
