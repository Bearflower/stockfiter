"""
OBPC 超跌反弹策略调度器
每天 23:15(北京) 形态扫描（等待23:00 K线更新完成，错峰避开 Baostock 晚间高峰），07:30 补全后重扫，08:10 飞书推送
使用明确的 Asia/Shanghai 时区，防止 Docker 容器时区漂移

2026-10-18 改造：
  - 所有模块级副作用收进 main()，避免测试 `import` 时卡死
  - task_status_allows_run 纯函数，status + target_date + 新鲜度三重校验
  - wait_for_kline_update / wait_for_backfill 按 status / target_date / last_completed_at 判定
  - 23:15 未就绪时记录 blocked_data，绝不启动 daily_scan
  - 07:30 重扫按目标交易日判断（已删除 is_trading_day(today) 提前跳过）
  - 重扫幂等：同数据版本已完成的 rescan 不再重扫
  - 启动扫描前 mark_stale_scan_runs_failed 收敛卡死 running
  - 推送幂等从 DB 读 has_pushed / save_push_history，进程内变量仅作查询优化

2026-10-19 R-02 同源修复：
  - 三处信号日期（23:15 初扫 / 07:30 重扫 / 08:10 推送）统一来自
    utils.trade_calendar.nearest_trading_date，与 scheduler_kline 写 task_status 的
    target_date 同源（不再读指数表最新日期），消除「一处指数表、一处交易日历」的日期错配。
"""
import os
import sys
import time
import logging
import subprocess
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Shanghai")

# 可配置参数（通过环境变量覆盖，默认值合理无需配置）
SCHEDULE_SCAN = os.environ.get("SCHEDULE_SCAN", "23:15")    # 2026-10-20 错峰：与 K 线更新 23:00 保持 15 分钟间隔
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

from utils.trade_calendar import is_trading_day, nearest_trading_date


# =====================================================================
# 纯函数区（供单元测试，不依赖 DB 连接）
# =====================================================================


def task_status_allows_run(row: Optional[Dict[str, Any]], target_date: str, freshness_seconds: int) -> bool:
    """
    任务状态网关：只有 completed + 目标日期匹配 + 新鲜度合格才放行。

    fail-closed 语义：row 缺 target_date 键或 target_date 不匹配 → 一律拒绝。
    （2026-10-18 改造：task_status 表已通过幂等迁移新增 target_date / details 列，
      _query_task_status 改为 SELECT 四列，因此生产环境 row 必含 target_date）

    Args:
        row: task_status 查询结果字典，至少含 status / target_date / last_completed_at
        target_date: 本次扫描要求的数据日期（YYYY-MM-DD）
        freshness_seconds: 允许的最大新鲜度（秒）

    Returns:
        bool: True = 放行扫描
    """
    if not row or row.get("status") != "completed":
        return False
    # fail-closed：target_date 缺失或不匹配均拒绝（Task 2 Step 4）
    row_target_date = row.get("target_date")
    if row_target_date is None or str(row_target_date) != target_date:
        return False
    completed_at = row.get("last_completed_at")
    if completed_at is None:
        return False
    # last_completed_at 无时区列、DB 会话为 UTC，按 UTC 解析（N-1 时间戳约定）
    if completed_at.tzinfo is None:
        completed_at = completed_at.replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - completed_at).total_seconds()
    return 0 <= age < freshness_seconds


def should_rescan_target(
    now_date: str,
    target_date: Optional[str],
    backfill_status: str,
    already_scanned: bool,
) -> bool:
    """
    重扫时机纯函数：只要目标交易日存在、backfill 已完成、同数据版本未重扫过，就放行。

    注意：now_date 保留签名（调用方传入），但判定核心在 target_date，
    这样周六 07:30 可以重扫周五数据，周日不会错误重扫更早日期
    （因为调用方用 nearest_trading_date(now, inclusive=False) 已返回最近一个交易日）。

    Args:
        now_date: 自然日（保留签名，内部 del）
        target_date: backfill 覆盖的目标交易日（None 表示无数据）
        backfill_status: task_status.status，必须 == 'completed'
        already_scanned: 同 signal_date + data_version 下 rescan 是否已完成

    Returns:
        bool: True = 执行重扫
    """
    del now_date
    return (
        bool(target_date)
        and is_trading_day(target_date)
        and backfill_status == "completed"
        and not already_scanned
    )


# =====================================================================
# DB 辅助函数（可能抛异常，由调用方捕获）
# =====================================================================


def _query_task_status(db, task_name: str) -> Optional[Dict[str, Any]]:
    """
    查询 task_status 表指定任务的最新一行，返回字典或 None。

    2026-10-18 改造：task_status 表已通过幂等迁移新增 target_date（DATE）
    和 details（JSONB）两列。SELECT 必须包含这两列，让下游 task_status_allows_run
    的 fail-closed 目标日期校验在生产环境中生效（修复 R-02 断链）。

    失败时返回 None（视为未就绪），不向上抛异常。
    """
    cur = db.conn.cursor()
    try:
        cur.execute(
            """
            SELECT status, target_date, last_completed_at, details
            FROM schema_stockfilter.task_status
            WHERE task_name = %s
            """,
            (task_name,),
        )
        row = cur.fetchone()
        if row is None:
            return None
        columns = [desc[0] for desc in cur.description]
        return dict(zip(columns, row))
    except Exception as e:
        logger.warning("查询 task_status(%s) 失败：%s", task_name, e)
        return None
    finally:
        cur.close()


def _fetch_backfill_completed_at(db) -> Optional[datetime]:
    """取 backfill 任务的 last_completed_at（可能无时区，DB 会话 UTC）"""
    row = _query_task_status(db, "backfill")
    if not row:
        return None
    ts = row.get("last_completed_at")
    if ts is None:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts


def is_rescan_completed(db, target_date: str, current_data_version: Optional[datetime]) -> bool:
    """
    重扫幂等判定（第 9.3.3 节）。

    存在 signal_date=target_date、run_kind='rescan'、
    status ∈ {completed_with_signals, completed_zero, blocked_market}
    且 data_version >= current_data_version 的记录 → True（同数据版本已重扫）。
    failed / blocked_data → False（允许重试）。
    current_data_version 为 None（读不到 backfill 完成时间）时返回 False（保守允许重扫）。
    """
    if not target_date:
        return False
    if current_data_version is None:
        return False

    cur = db.conn.cursor()
    try:
        cur.execute(
            """
            SELECT status, data_version FROM scan_runs
            WHERE signal_date = %s AND run_kind = 'rescan'
            ORDER BY started_at DESC LIMIT 1
            """,
            (target_date,),
        )
        row = cur.fetchone()
        if row is None:
            return False
        status, data_version = row
        # 失败状态允许重试
        if status in ("failed", "blocked_data"):
            return False
        # 成功终态（含 blocked_market）需对比 data_version
        if data_version is None:
            return False
        return data_version >= current_data_version
    except Exception as e:
        logger.warning("is_rescan_completed 查询失败：%s，保守返回 False", e)
        return False
    finally:
        cur.close()


# =====================================================================
# 等待函数（严格按 status + target_date + 新鲜度判定）
# =====================================================================


def _wait_for_task_completed(task_name: str, target_date: str, task_label: str) -> bool:
    """
    轮询 task_status 表等待指定任务完成（公共实现）。

    wait_for_kline_update 和 wait_for_backfill 委托到此函数，避免逐行重复。

    Args:
        task_name: task_status.task_name 值（如 "kline_update" / "backfill"）
        target_date: 本次扫描要求的数据日期（YYYY-MM-DD）
        task_label: 中文标签，用于日志输出（如 "K线更新" / "历史补全"）

    Returns:
        bool: True = 任务就绪；False = 未就绪或超时
    """
    from data.database import DatabaseManager

    db = DatabaseManager(initialize_schema=False, autocommit=True)
    try:
        deadline = datetime.now(TZ) + timedelta(seconds=KLINE_WAIT_TIMEOUT)
        waited = 0
        while datetime.now(TZ) < deadline:
            row = _query_task_status(db, task_name)
            if task_status_allows_run(row, target_date, FRESHNESS_THRESHOLD):
                logger.info(
                    "%s已完成（%s，目标日期=%s）",
                    task_label, row.get("last_completed_at"), target_date,
                )
                return True
            waited += KLINE_WAIT_INTERVAL
            if row:
                logger.info(
                    "等待%s... status=%s target_date=%s 已等 %ds",
                    task_label, row.get("status"), row.get("target_date"), waited,
                )
            else:
                logger.info("等待%s... 无 task_status 记录 已等 %ds", task_label, waited)
            time.sleep(KLINE_WAIT_INTERVAL)

        logger.warning(
            "%s等待超时（%ds），视为未就绪（target_date=%s）",
            task_label, KLINE_WAIT_TIMEOUT, target_date,
        )
        return False
    finally:
        db.close()


def wait_for_kline_update(target_date: str) -> bool:
    """轮询 task_status 表，等待 K 线更新完成。返回 False 表示未就绪或超时。"""
    return _wait_for_task_completed("kline_update", target_date, "K线更新")


def wait_for_backfill(target_date: str) -> bool:
    """轮询 task_status 表，等待历史补全完成。返回 False 表示未就绪或超时。"""
    return _wait_for_task_completed("backfill", target_date, "历史补全")


# =====================================================================
# 运行记录与调度辅助
# =====================================================================


def _resolve_scan_signal_date(signal_date: Optional[str]) -> str:
    """
    兜底：调度器各分支拿不到 signal_date 时，用当天自然日兜底并记录，
    但严禁静默回退。
    """
    if signal_date:
        return signal_date
    return datetime.now(TZ).strftime("%Y-%m-%d")


def record_scan_blocked(signal_date: Optional[str], status: str, reason: str) -> None:
    """
    数据未就绪/失败时记录 blocked_data（或调用方指定 status），
    不启动 daily_scan.py。

    Args:
        signal_date: 目标交易日（None 时用当天自然日兜底）
        status: scan_runs.status（blocked_data / failed / blocked_market 等）
        reason: filter_reason
    """
    from data.database import DatabaseManager

    date = _resolve_scan_signal_date(signal_date)
    try:
        db = DatabaseManager()
        try:
            db.upsert_scan_run(
                signal_date=date,
                run_kind="initial",
                status=status,
                filter_reason=reason,
            )
        finally:
            db.close()
        logger.warning(
            "扫描已阻断：signal_date=%s status=%s reason=%s", date, status, reason
        )
    except Exception as e:
        logger.error("record_scan_blocked 写库失败：%s（不影响阻断语义）", e)


# =====================================================================
# 飞书汇总推送幂等（Task 6 Step 3 / 第 9.4 N-3）
# =====================================================================

PUSH_SUMMARY_CODE = "__daily_summary__"
PUSH_SUMMARY_TYPE = "obpc_summary"


def _get_database_manager():
    """延迟获取 DatabaseManager 实例。

    保持「不在模块导入期建连」的既有约束，同时提供可替换的测试接缝：
    测试通过 monkeypatch 本函数即可注入 FakeDB。
    """
    from data.database import DatabaseManager
    return DatabaseManager()


def push_daily_summary_once(push_date: str, inproc_pushed: bool) -> Dict[str, Any]:
    """执行 08:10 飞书汇总推送，并保证持久化幂等。

    幂等流程（顺序不可颠倒）：
      1. 进程内守卫命中（inproc_pushed）→ 跳过，不查 DB
      2. DB ``has_pushed`` 命中 → 跳过（真相源）
      3. 子进程执行 ``scripts/feishu_push.py``
      4. **仅当推送成功**才写 ``push_history``；失败不写，下次可重试

    Args:
        push_date: 本次推送的信号日期（YYYY-MM-DD）
        inproc_pushed: 进程内变量记录的「已推送过该信号日期」

    Returns:
        Dict[str, Any]: {
            "skipped": bool,        # 是否跳过发送
            "skip_reason": str|None, # "inproc_guard" / "db_guard"
            "pushed": bool,         # 是否实际发送成功
        }
    """
    if inproc_pushed:
        return {"skipped": True, "skip_reason": "inproc_guard", "pushed": False}

    # 1) DB 幂等查询（失败时降级为依赖进程内变量，不阻断推送）
    db_guard_hit = False
    try:
        db_idem = _get_database_manager()
        try:
            db_guard_hit = bool(
                db_idem.has_pushed(PUSH_SUMMARY_CODE, push_date, PUSH_SUMMARY_TYPE)
            )
        finally:
            db_idem.close()
    except Exception as e:
        logger.warning("查询 push_history 幂等失败：%s，依赖进程内变量判定", e)

    if db_guard_hit:
        return {"skipped": True, "skip_reason": "db_guard", "pushed": False}

    # 2) 执行推送（失败/超时都视为未成功）
    push_success = False
    try:
        subprocess.run(
            [sys.executable, "scripts/feishu_push.py", "--signal-date", push_date],
            check=True,
            timeout=PUSH_TIMEOUT,
        )
        push_success = True
        logger.info("飞书推送完成（信号日期=%s）", push_date)
    except subprocess.TimeoutExpired:
        logger.warning("飞书推送超时（%ds）", PUSH_TIMEOUT)
    except Exception as e:
        logger.error("飞书推送失败: %s", e, exc_info=True)

    # 3) 推送成功才写 push_history（第 9.4 N-3）
    if push_success:
        try:
            db_hist = _get_database_manager()
            try:
                db_hist.save_push_history(PUSH_SUMMARY_CODE, push_date, PUSH_SUMMARY_TYPE)
            finally:
                db_hist.close()
            logger.info("已写入 push_history（%s %s）", push_date, PUSH_SUMMARY_TYPE)
        except Exception as e:
            logger.error(
                "推送成功但写 push_history 失败：%s（极小概率重复推送，由监控暴露）", e
            )

    return {"skipped": False, "skip_reason": None, "pushed": push_success}


# =====================================================================
# 调度主循环
# =====================================================================


def main() -> None:
    """
    OBPC 调度主循环。

    所有模块级副作用（日志输出、task_status 守卫变量）都收在 main() 内，
    避免 `from scripts.scheduler_obpc import task_status_allows_run` 时卡死。
    """
    logger.info("OBPC 策略调度器启动（当前时间: %s）", datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S"))
    logger.info("定时任务：23:15 形态扫描 / 07:30 补全后重扫 / 08:10 飞书推送（均为北京时间，K线更新 23:00 错峰）")
    logger.info("K线等待超时：%ds，轮询间隔：%ds", KLINE_WAIT_TIMEOUT, KLINE_WAIT_INTERVAL)

    # 各时段「当天已触发」守卫（值为自然日，防止同一分钟循环重复进入）
    last_run_scan: str = ""
    last_run_rescan: str = ""
    last_run_push: str = ""
    # 已推送的信号日期（值为信号日期，跨天保留，防止同一交易日信号被重复推送）；
    # 必须与上面按自然日的守卫分离，否则非交易日跳过分支会把信号日期污染为当天，导致重复推送
    last_pushed_signal_date: str = ""

    while True:
        now = datetime.now(TZ)
        today = now.strftime("%Y-%m-%d")
        yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")
        hm = now.strftime("%H:%M")

        # ---------------------------------------------------------------
        # 23:15 初扫分支：严格等待数据，未就绪记录 blocked_data，绝不启动 daily_scan
        # ---------------------------------------------------------------
        if hm == SCHEDULE_SCAN and last_run_scan != today:
            # 先收敛上一轮可能卡死的 running 记录（必修 6）
            try:
                from data.database import DatabaseManager
                db_clean = DatabaseManager()
                try:
                    db_clean.mark_stale_scan_runs_failed(stale_after_seconds=SCAN_TIMEOUT)
                finally:
                    db_clean.close()
            except Exception as e:
                logger.warning("收敛超时 running 记录失败（不阻断本轮扫描）：%s", e)

            # 扫描时段：仅当天是交易日才执行
            if not is_trading_day(today):
                logger.info("=== %s 非交易日，跳过形态扫描 ===", today)
                last_run_scan = today
                time.sleep(LOOP_INTERVAL)
                continue

            last_run_scan = today
            # 23:15 初扫的信号日期必须与 kline 侧 23:00 写入的 task_status.target_date 同源：
            # 上方守卫已确认 today 是交易日，23:00 K 线更新计算的 target_date 就是今天
            # （compute_target_date_for(now) = 最近一个 ≤ today 的交易日 = today），
            # 因此这里直接用 today，不再读指数表（指数表可能因更新失败而滞后）。
            target_date = today
            logger.info("=== 23:15 初扫：target_date=%s ===", target_date)

            if not target_date or not wait_for_kline_update(target_date):
                record_scan_blocked(target_date, "blocked_data", "kline_not_ready")
            else:
                logger.info("=== 执行形态扫描（23:15 北京，target_date=%s）===", target_date)
                try:
                    subprocess.run(
                        [sys.executable, "scripts/daily_scan.py",
                         "--signal-date", target_date,
                         "--run-kind", "initial"],
                        check=True,
                        timeout=SCAN_TIMEOUT,
                    )
                    logger.info("形态扫描完成")
                except subprocess.TimeoutExpired:
                    logger.warning("形态扫描超时（%ds）", SCAN_TIMEOUT)
                except Exception as e:
                    logger.error("形态扫描失败: %s", e, exc_info=True)

            time.sleep(POST_TASK_DELAY)

        # ---------------------------------------------------------------
        # 07:30 重扫分支：已删除 is_trading_day(today) 提前跳过，按目标交易日判断
        # ---------------------------------------------------------------
        elif hm == SCHEDULE_RESCAN and last_run_rescan != today:
            # 先收敛上一轮可能卡死的 running 记录（必修 6）
            try:
                from data.database import DatabaseManager
                db_clean = DatabaseManager()
                try:
                    db_clean.mark_stale_scan_runs_failed(stale_after_seconds=SCAN_TIMEOUT)
                finally:
                    db_clean.close()
            except Exception as e:
                logger.warning("收敛超时 running 记录失败（不阻断本轮重扫）：%s", e)

            last_run_rescan = today
            logger.info("=== 07:30 重扫分支：now_date=%s ===", today)

            # 拿信号基准日期：07:30 是盘前时段，当天 K 线尚未生成，
            # 只能重扫「最近一个已收盘交易日」= nearest_trading_date(now, inclusive=False)，
            # 与 kline 侧 07:00 补全写 task_status 的 target_date 同源（都来自交易日历）。
            signal_date, date_degraded = nearest_trading_date(now, inclusive=False)
            logger.info(
                "07:30 重扫信号日期：%s（交易日历可信度：%s）",
                signal_date,
                "weekday 降级" if date_degraded else "Baostock 已验证",
            )
            if not signal_date:
                logger.warning("交易日历未能确定重扫信号日期（回溯窗口内无交易日），安全跳过重扫")
                time.sleep(LOOP_INTERVAL)
                continue

            # 用 signal_date 作为 backfill 的目标日期来等待（确保补全的是这一天的数据）
            logger.info("=== 等待历史补全完成（target_date=%s）===", signal_date)
            backfill_ok = wait_for_backfill(signal_date)

            # 查询 backfill 任务最新状态供 should_rescan_target 使用
            try:
                from data.database import DatabaseManager
                db_chk = DatabaseManager()
                try:
                    backfill_row = _query_task_status(db_chk, "backfill")
                    backfill_status = (backfill_row or {}).get("status") or "unknown"
                    current_data_version = _fetch_backfill_completed_at(db_chk)
                    already = is_rescan_completed(db_chk, signal_date, current_data_version)
                finally:
                    db_chk.close()
            except Exception as e:
                logger.warning("查询重扫幂等/backfill 状态失败：%s", e)
                backfill_status = "unknown"
                current_data_version = None
                already = False

            # backfill 没完成（wait_for_backfill 返回 False 或 status != completed）都视为未完成
            if not backfill_ok:
                backfill_status = "not_ready"

            if should_rescan_target(
                now_date=today,
                target_date=signal_date,
                backfill_status=backfill_status,
                already_scanned=already,
            ):
                logger.info("=== 执行补全后重扫（07:30 北京，signal_date=%s）===", signal_date)
                try:
                    subprocess.run(
                        [sys.executable, "scripts/daily_scan.py",
                         "--signal-date", signal_date,
                         "--run-kind", "rescan"],
                        check=True,
                        timeout=SCAN_TIMEOUT,
                    )
                    logger.info("补全后重扫完成")
                except subprocess.TimeoutExpired:
                    logger.warning("补全后重扫超时（%ds），跳过", SCAN_TIMEOUT)
                except Exception as e:
                    logger.error("补全后重扫失败: %s", e, exc_info=True)
            else:
                # 日志说明为什么不重扫
                reasons = []
                if not signal_date:
                    reasons.append("signal_date 为空")
                elif not is_trading_day(signal_date):
                    reasons.append(f"{signal_date} 不是交易日")
                elif backfill_status != "completed":
                    reasons.append(f"backfill status={backfill_status}")
                elif already:
                    reasons.append("同数据版本已重扫过")
                logger.info("跳过重扫：%s", "、".join(reasons) if reasons else "未知原因")

            time.sleep(POST_TASK_DELAY)

        # ---------------------------------------------------------------
        # 08:10 推送分支：DB 持久化幂等，进程内变量仅作查询优化
        # ---------------------------------------------------------------
        elif hm == SCHEDULE_PUSH and last_run_push != today:
            # 按自然日守卫：本时段当天只进入一次，避免同一分钟循环重复判定
            last_run_push = today
            # 推送时段：当天或前一天任一为交易日都放行
            # 覆盖周五 23:15 扫描 → 周六 08:10 推送的场景
            if not is_trading_day(today) and not is_trading_day(yesterday):
                logger.info("=== %s 和 %s 均为非交易日，跳过飞书推送 ===", yesterday, today)
                time.sleep(LOOP_INTERVAL)
                continue

            # 推送的信号日期 = 「最近一个已收盘交易日」= nearest_trading_date(now, inclusive=False)，
            # 与 07:30 重扫、kline 侧 07:00 补全保持同一日期语义（同源，保证 signals_{date}.json 找得到）；
            # 当天数据 23:00 K 线更新才完成，用当天会找不到 signals_{当天}.json。
            # 取不到日期时安全跳过，禁止回退「当天」，否则会复现找不到信号文件的问题。
            push_date, push_degraded = nearest_trading_date(now, inclusive=False)
            logger.info(
                "08:10 推送信号日期：%s（交易日历可信度：%s）",
                push_date,
                "weekday 降级" if push_degraded else "Baostock 已验证",
            )
            if not push_date:
                logger.warning("交易日历未能确定推送信号日期（回溯窗口内无交易日），安全跳过飞书推送")
                time.sleep(LOOP_INTERVAL)
                continue

            # 幂等 + 推送统一由 push_daily_summary_once 封装：
            # 进程内守卫 → DB has_pushed → 发送 → 成功才写 push_history
            logger.info("=== 执行飞书推送（08:10 北京，信号日期=%s）===", push_date)
            outcome = push_daily_summary_once(
                push_date, inproc_pushed=(last_pushed_signal_date == push_date)
            )

            if outcome["skipped"]:
                logger.info(
                    "信号日期 %s 已推送过（%s），跳过重复推送",
                    push_date, outcome["skip_reason"],
                )
                time.sleep(LOOP_INTERVAL)
                continue

            if outcome["pushed"]:
                last_pushed_signal_date = push_date

            time.sleep(POST_TASK_DELAY)

        else:
            time.sleep(LOOP_INTERVAL)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("收到中断信号，调度器退出")
        sys.exit(0)
    except Exception as e:
        logger.error("调度器异常退出：%s", e, exc_info=True)
        sys.exit(1)
