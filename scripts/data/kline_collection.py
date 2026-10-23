"""K 线采集监督者：进程级隔离 + 数据库断点续采。

设计来源：tasks/plan.md（2026-10-22 实施方案）。

核心约束：
1. 单个 Baostock 请求不可安全取消，因此**不允许**在工作进程内部用线程/线程池
   做"伪超时"；超时能力统一由本监督者提供（进程级超时 → terminate → 宽限期 → kill）。
2. 工作进程一次性、纯串行；超时后整进程销毁，旧 socket/线程由操作系统回收。
3. 断点唯一来源是数据库：每轮启动新进程前重新查询目标日缺口，已落库股票自动跳过。
4. 最终是否放行只由数据库真实覆盖率和既有完整性规则（UpdateResult.is_complete）决定。
5. 所有分片规模、超时、重建次数、截止时间均来自配置，不在代码里写死。
"""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from utils.logger import get_logger

logger = get_logger()

TZ = ZoneInfo("Asia/Shanghai")

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
WORKER_ENTRY = os.path.join(REPO_ROOT, "scripts", "data", "update_kline_daily.py")

# 工作进程退出码契约（Task 1）
EXIT_CHUNK_COMPLETE = 0
"""分片内全部股票已尝试（允许存在单股失败）"""

EXIT_CHUNK_BREAKER = 1
"""分片内被失败率/累计失败熔断提前终止（result JSON 仍会写入）"""

EXIT_WORKER_FATAL = 2
"""配置、数据库或未捕获异常导致分片无法继续（result JSON 可能缺失）"""

CONTRACT_VERSION = 1

# 监督者停止原因（写入 details.stop_reason，供监控和交接使用）
STOP_COVERAGE_REACHED = "coverage_reached"
STOP_NO_MISSING = "no_missing"
STOP_DEADLINE = "deadline"
STOP_RESTART_LIMIT = "restart_limit"
STOP_NO_PROGRESS = "no_progress"
STOP_WORKER_FATAL = "worker_fatal"
STOP_DB_ERROR = "db_error"
STOP_CONFIG_ERROR = "config_error"
STOP_COVERAGE_SHORT = "coverage_short"
STOP_QUARANTINED_EXHAUSTED = "quarantined_exhausted"
"""剩余缺口全部是已隔离的挂死股票，且重试额度已用完"""

# completion_mode（写入 details，不改变 task_status.status 枚举）
MODE_NORMAL = "normal"
MODE_RECOVERED = "recovered_after_timeout"
MODE_FAILED = "failed"


class CollectionConfigError(ValueError):
    """分片采集配置缺失或非法（fail-closed，不使用静默默认值）。"""


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CollectionConfig:
    """分片采集监督者配置（全部来自 global.kline_update）。"""

    worker_chunk_size: int
    worker_timeout_seconds: int
    worker_terminate_grace_seconds: int
    max_session_restarts: int
    recovery_deadline_buffer_seconds: int
    immediate_recovery_enabled: bool
    max_consecutive_no_progress_rounds: int
    max_quarantined_retries: int
    db_query_timeout_seconds: int
    db_connect_timeout_seconds: int
    task_status_write_retries: int
    task_status_write_retry_delay_seconds: int
    task_status_process_terminate_grace_seconds: int
    initial_scan_time: str
    kline_wait_timeout_seconds: int
    min_coverage_rate: float
    request_interval_seconds: float


def _require_positive_int(cfg: dict, key: str) -> int:
    if key not in cfg or cfg[key] is None:
        raise CollectionConfigError(f"缺少必填配置 global.kline_update.{key}")
    value = cfg[key]
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise CollectionConfigError(
            f"配置 global.kline_update.{key} 必须是正整数，实际为 {value!r}"
        )
    return value


def _require_non_negative_int(cfg: dict, key: str) -> int:
    if key not in cfg or cfg[key] is None:
        raise CollectionConfigError(f"缺少必填配置 global.kline_update.{key}")
    value = cfg[key]
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise CollectionConfigError(
            f"配置 global.kline_update.{key} 必须是非负整数，实际为 {value!r}"
        )
    return value


def _require_bool(cfg: dict, key: str) -> bool:
    if key not in cfg or cfg[key] is None:
        raise CollectionConfigError(f"缺少必填配置 global.kline_update.{key}")
    value = cfg[key]
    if not isinstance(value, bool):
        raise CollectionConfigError(
            f"配置 global.kline_update.{key} 必须是布尔值，实际为 {value!r}"
        )
    return value


def _require_rate(cfg: dict, key: str, *, lower_inclusive: bool = True) -> float:
    """读取有限比例值，拒绝 bool、NaN/Inf 与越界值。"""
    if key not in cfg or cfg[key] is None:
        raise CollectionConfigError(f"缺少必填配置 global.kline_update.{key}")
    value = cfg[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CollectionConfigError(
            f"配置 global.kline_update.{key} 必须是数值，实际为 {value!r}"
        )
    normalized = float(value)
    lower_ok = normalized >= 0.0 if lower_inclusive else normalized > 0.0
    if not math.isfinite(normalized) or not lower_ok or normalized > 1.0:
        interval = "[0, 1]" if lower_inclusive else "(0, 1]"
        raise CollectionConfigError(f"{key} 必须在 {interval} 内，实际为 {value!r}")
    return normalized


_HHMM_RE = re.compile(r"^([01][0-9]|2[0-3]):([0-5][0-9])$")


def _validate_hhmm(value: str, label: str) -> str:
    if not isinstance(value, str) or not _HHMM_RE.match(value):
        raise CollectionConfigError(f"{label} 必须是 HH:MM，实际为 {value!r}")
    return value


def load_collection_config(
    kline_cfg: dict,
    initial_scan_time: str | None = None,
    kline_wait_timeout_seconds: int | None = None,
) -> CollectionConfig:
    """从 global.kline_update 读取分片采集配置。

    缺失任一必填项都直接抛 CollectionConfigError（fail-closed），
    避免静默使用"看起来合理"的默认值掩盖配置漂移。

    Args:
        kline_cfg: config.yaml 的 global.kline_update 字典
        initial_scan_time: OBPC 初扫时点（HH:MM），默认取环境变量 SCHEDULE_SCAN
        kline_wait_timeout_seconds: OBPC 等待 K 线的超时秒数，默认取环境变量 KLINE_WAIT_TIMEOUT
    """
    if not isinstance(kline_cfg, dict) or not kline_cfg:
        raise CollectionConfigError("global.kline_update 配置缺失或不是字典")

    min_coverage_rate = _require_rate(
        kline_cfg, "min_coverage_rate", lower_inclusive=False
    )
    _require_positive_int(kline_cfg, "days_to_update")
    _require_positive_int(kline_cfg, "target_date_max_backtrack")
    failure_window_size = _require_positive_int(kline_cfg, "failure_window_size")
    failure_window_min_samples = _require_positive_int(
        kline_cfg, "failure_window_min_samples"
    )
    if failure_window_min_samples > failure_window_size:
        raise CollectionConfigError(
            "failure_window_min_samples 不得大于 failure_window_size，"
            f"实际为 {failure_window_min_samples} > {failure_window_size}"
        )
    _require_rate(kline_cfg, "failure_rate_threshold")
    _require_rate(kline_cfg, "min_success_rate")
    _require_rate(kline_cfg, "skipped_ratio_guard")

    request_interval = kline_cfg.get("request_interval_seconds")
    if not isinstance(request_interval, (int, float)) or isinstance(request_interval, bool):
        raise CollectionConfigError("缺少或非法配置 global.kline_update.request_interval_seconds")
    if not math.isfinite(float(request_interval)) or float(request_interval) < 0:
        raise CollectionConfigError(
            f"request_interval_seconds 必须 >= 0，实际为 {request_interval!r}"
        )

    initial_scan_time = initial_scan_time or os.environ.get("SCHEDULE_SCAN", "23:15")
    if kline_wait_timeout_seconds is None:
        raw_wait_timeout = os.environ.get("KLINE_WAIT_TIMEOUT", "1800")
        try:
            kline_wait_timeout_seconds = int(raw_wait_timeout)
        except (TypeError, ValueError) as exc:
            raise CollectionConfigError(
                f"KLINE_WAIT_TIMEOUT 必须是正整数，实际为 {raw_wait_timeout!r}"
            ) from exc

    initial_scan_time = _validate_hhmm(initial_scan_time, "initial_scan_time")
    if (
        isinstance(kline_wait_timeout_seconds, bool)
        or not isinstance(kline_wait_timeout_seconds, int)
        or kline_wait_timeout_seconds <= 0
    ):
        raise CollectionConfigError(
            f"kline_wait_timeout_seconds 必须为正数，实际为 {kline_wait_timeout_seconds!r}"
        )

    config = CollectionConfig(
        worker_chunk_size=_require_positive_int(kline_cfg, "worker_chunk_size"),
        worker_timeout_seconds=_require_positive_int(kline_cfg, "worker_timeout_seconds"),
        worker_terminate_grace_seconds=_require_non_negative_int(
            kline_cfg, "worker_terminate_grace_seconds"
        ),
        max_session_restarts=_require_non_negative_int(kline_cfg, "max_session_restarts"),
        recovery_deadline_buffer_seconds=_require_non_negative_int(
            kline_cfg, "recovery_deadline_buffer_seconds"
        ),
        immediate_recovery_enabled=_require_bool(kline_cfg, "immediate_recovery_enabled"),
        max_consecutive_no_progress_rounds=_require_positive_int(
            kline_cfg, "max_consecutive_no_progress_rounds"
        ),
        max_quarantined_retries=_require_non_negative_int(
            kline_cfg, "max_quarantined_retries"
        ),
        db_query_timeout_seconds=_require_positive_int(
            kline_cfg, "db_query_timeout_seconds"
        ),
        db_connect_timeout_seconds=_require_positive_int(
            kline_cfg, "db_connect_timeout_seconds"
        ),
        task_status_write_retries=_require_positive_int(
            kline_cfg, "task_status_write_retries"
        ),
        task_status_write_retry_delay_seconds=_require_non_negative_int(
            kline_cfg, "task_status_write_retry_delay_seconds"
        ),
        task_status_process_terminate_grace_seconds=_require_positive_int(
            kline_cfg, "task_status_process_terminate_grace_seconds"
        ),
        initial_scan_time=initial_scan_time,
        kline_wait_timeout_seconds=int(kline_wait_timeout_seconds),
        min_coverage_rate=min_coverage_rate,
        request_interval_seconds=float(request_interval),
    )

    # 单分片熔断上限必须小于分片大小，否则分片内永远达不到硬上限（配置自相矛盾）
    total_failure_limit = _require_positive_int(kline_cfg, "total_failure_limit")
    if total_failure_limit >= config.worker_chunk_size:
        raise CollectionConfigError(
            "total_failure_limit 必须小于 worker_chunk_size，否则单分片内永远无法触发"
            f"累计失败硬熔断（limit={total_failure_limit}，"
            f"chunk={config.worker_chunk_size}）"
        )

    if failure_window_min_samples > config.worker_chunk_size:
        raise CollectionConfigError(
            "failure_window_min_samples 不得大于 worker_chunk_size，否则单个工作"
            "进程永远无法积累足够样本触发失败率熔断"
            f"（samples={failure_window_min_samples}，chunk={config.worker_chunk_size}）"
        )

    if config.recovery_deadline_buffer_seconds >= config.kline_wait_timeout_seconds:
        raise CollectionConfigError(
            "recovery_deadline_buffer_seconds 必须小于 kline_wait_timeout_seconds，"
            f"否则截止时间早于初扫开始（buffer={config.recovery_deadline_buffer_seconds}，"
            f"wait={config.kline_wait_timeout_seconds}）"
        )

    task_status_budget = (
        config.task_status_write_retries
        * (
            config.db_connect_timeout_seconds
            + config.db_query_timeout_seconds
            + 2 * config.task_status_process_terminate_grace_seconds
        )
        + (config.task_status_write_retries - 1)
        * config.task_status_write_retry_delay_seconds
    )
    if task_status_budget >= config.recovery_deadline_buffer_seconds:
        raise CollectionConfigError(
            "task_status 写入最坏预算必须严格小于 recovery_deadline_buffer_seconds，"
            f"实际为 {task_status_budget}s >= "
            f"{config.recovery_deadline_buffer_seconds}s"
        )
    return config


def compute_collection_deadline(
    now: datetime,
    initial_scan_time: str,
    kline_wait_timeout_seconds: int,
    recovery_deadline_buffer_seconds: int,
    strict: bool = True,
) -> datetime:
    """推导采集整体截止时间。

    collection_deadline = initial_scan_time + KLINE_WAIT_TIMEOUT - buffer

    与 OBPC 的等待窗口同源（同一环境变量），不另写绝对时刻，避免双源漂移。

    截止时间已过时的行为由 strict 区分：
      - strict=True（自动调度入口）：直接返回已过的时刻，监督者立即以
        stop_reason=deadline 结束，避免延迟执行越过 OBPC 等待窗口；
      - strict=False（人工入口 main.py --all）：从当前时刻起给予同样长度的
        可用窗口（wait - buffer），保证人工在任意时刻仍可完成采集。
    """
    hour, minute = (int(part) for part in initial_scan_time.split(":"))
    scan_at = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    deadline = scan_at + timedelta(
        seconds=kline_wait_timeout_seconds - recovery_deadline_buffer_seconds
    )
    if deadline <= now and not strict:
        deadline = now + timedelta(
            seconds=kline_wait_timeout_seconds - recovery_deadline_buffer_seconds
        )
    return deadline


# ---------------------------------------------------------------------------
# 数据库断点（缺口/覆盖）
# ---------------------------------------------------------------------------


class DatabaseGapStore:
    """以数据库为目标日缺口的唯一事实来源。

    只做两类查询：
      - pool(): 股票池（code/symbol）
      - target_date_codes(target_date): 目标日已落库的 code 集合
    写入由工作进程逐股 upsert 完成，本类不写数据。
    """

    def __init__(self, db, pool_df, query_timeout_seconds: int | None = None):
        self._db = db
        self._pool = [
            {"code": str(row["code"]), "symbol": str(row["symbol"])}
            for _, row in pool_df.iterrows()
        ]
        if query_timeout_seconds is not None:
            self._apply_statement_timeout(query_timeout_seconds)

    def _apply_statement_timeout(self, seconds: int) -> None:
        """给监督者自身的查询设置语句超时，避免整体截止时间被慢查询突破。"""
        cur = self._db.conn.cursor()
        try:
            cur.execute("SET statement_timeout = %s", (int(seconds) * 1000,))
            self._db.conn.commit()
        finally:
            cur.close()

    def pool(self) -> list[dict]:
        return list(self._pool)

    def target_date_codes(self, target_date: str) -> set[str]:
        cur = self._db.conn.cursor()
        try:
            cur.execute(
                "SELECT DISTINCT code FROM klines WHERE date = %s AND frequency = 'd'",
                (target_date,),
            )
            return {str(row[0]) for row in cur.fetchall()}
        finally:
            cur.close()


def load_stock_pool(
    config_path: str = "config/config.yaml",
    connect_timeout: int | None = None,
    statement_timeout: int | None = None,
):
    """加载股票池（与 update_kline_daily 原有来源保持一致）。

    Returns:
        pandas.DataFrame: 至少包含 code / symbol 两列
    """
    import pandas as pd
    import yaml

    from data.database import DatabaseManager

    stocks_file = ""
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            full_cfg = yaml.safe_load(f) or {}
        stocks_file = (full_cfg.get("global") or {}).get("stock_list_file", "")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"读取 config.yaml 中的 stock_list_file 失败：{e}")

    if stocks_file and os.path.exists(stocks_file):
        logger.info(f"使用固定股票列表：{stocks_file}")
        return pd.read_csv(stocks_file, dtype={"code": str})

    db = DatabaseManager(
        connect_timeout=connect_timeout,
        statement_timeout=statement_timeout,
    )
    try:
        pool = db.get_stock_list()
    finally:
        db.close()
    if pool is None or getattr(pool, "empty", True):
        return None
    logger.info(f"使用动态股票列表：{len(pool)} 只")
    return pool


# ---------------------------------------------------------------------------
# 工作进程契约
# ---------------------------------------------------------------------------


@dataclass
class WorkerOutcome:
    """一次分片工作进程的执行结果（监督者内部使用）。"""

    round_index: int
    chunk_index: int
    chunk_codes: list[str]
    exit_code: int | None
    timed_out: bool
    duration_seconds: float
    result: dict | None = None
    progress: dict | None = None
    killed: bool = False
    kill_signal: str = ""
    pid: int | None = None
    pid_gone: bool = True
    error: str = ""

    @property
    def last_started_code(self) -> str | None:
        """超时时正在请求的股票（来自工作进程进度文件，用于定位卡死股票）。"""
        if not self.progress:
            return None
        value = self.progress.get("last_started_code")
        return str(value) if value else None


class SubprocessWorkerRunner:
    """默认工作进程运行器：启动独立 OS 进程，超时后 terminate → 宽限期 → kill。"""

    def __init__(self, config: CollectionConfig, worker_entry: str = WORKER_ENTRY):
        self._config = config
        self._worker_entry = worker_entry

    def run(
        self,
        chunk: list[dict],
        chunk_index: int,
        round_index: int,
        timeout_seconds: float,
        work_dir: str,
        target_date: str,
        pool_size: int,
    ) -> WorkerOutcome:
        symbols_path = os.path.join(work_dir, f"chunk_{round_index}_{chunk_index}.json")
        result_path = os.path.join(work_dir, f"result_{round_index}_{chunk_index}.json")
        progress_path = os.path.join(work_dir, f"progress_{round_index}_{chunk_index}.json")

        with open(symbols_path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "target_date": target_date,
                    "chunk_index": chunk_index,
                    "pool_size": pool_size,
                    "stocks": chunk,
                },
                f,
                ensure_ascii=False,
            )

        cmd = [
            sys.executable,
            self._worker_entry,
            "--worker",
            "--target-date", target_date,
            "--symbols-file", symbols_path,
            "--result-json", result_path,
            "--progress-json", progress_path,
            "--pool-size", str(pool_size),
            "--chunk-index", str(chunk_index),
        ]
        logger.info(
            f"启动分片工作进程：round={round_index} chunk={chunk_index} "
            f"股票数={len(chunk)} 超时={timeout_seconds:.0f}s"
        )
        started = time.monotonic()
        # start_new_session：工作进程成为独立进程组组长，超时后可整组终止，
        # 且不会把 SIGTERM 传播回调度器父进程。
        proc = subprocess.Popen(
            cmd,
            cwd=REPO_ROOT,
            stdout=None,
            stderr=None,
            start_new_session=True,
            env=self._worker_env(),
        )
        timed_out = False
        killed = False
        kill_signal = ""
        try:
            proc.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            timed_out = True
            logger.error(
                f"分片工作进程超时（>{timeout_seconds:.0f}s，pid={proc.pid}），开始 terminate"
            )
            kill_signal = self._terminate_tree(proc)
            killed = True

        # 确认旧 PID 已消失（plan 验收：不得残留工作进程）
        pid_gone = proc.poll() is not None
        if not pid_gone:  # 理论不可达：wait 返回后 poll 必非 None
            try:
                os.kill(proc.pid, 0)
                pid_gone = False
            except OSError:
                pid_gone = True

        duration = time.monotonic() - started
        outcome = WorkerOutcome(
            round_index=round_index,
            chunk_index=chunk_index,
            chunk_codes=[str(item["code"]) for item in chunk],
            exit_code=proc.returncode,
            timed_out=timed_out,
            duration_seconds=round(duration, 3),
            killed=killed,
            kill_signal=kill_signal,
            pid=proc.pid,
            pid_gone=pid_gone,
        )
        outcome.result = _read_json(result_path)
        outcome.progress = _read_json(progress_path)
        if timed_out and not outcome.progress:
            outcome.error = "超时且未读到进度文件"
        return outcome

    def _worker_env(self) -> dict:
        """确保工作进程能导入仓库包（不依赖调用者是否设置 PYTHONPATH）。

        直接执行 scripts/data/*.py 时 sys.path[0] 是脚本目录而非仓库根，
        容器内靠 PYTHONPATH=/app 兜底；监督者显式注入，避免本地/宿主环境失败。
        """
        env = dict(os.environ)
        existing = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = REPO_ROOT + (os.pathsep + existing if existing else "")
        return env

    def _terminate_tree(self, proc: subprocess.Popen) -> str:
        """先 SIGTERM 进程组，宽限期后 SIGKILL，返回实际使用的最后一个信号。"""
        grace = self._config.worker_terminate_grace_seconds
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except OSError:
            try:
                proc.terminate()
            except OSError:
                pass
        try:
            proc.wait(timeout=grace)
            return "SIGTERM"
        except subprocess.TimeoutExpired:
            logger.error(
                f"工作进程在 {grace}s 宽限期内未退出（pid={proc.pid}），执行 SIGKILL"
            )
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except OSError:
            try:
                proc.kill()
            except OSError:
                pass
        try:
            proc.wait(timeout=max(grace, 1))
        except subprocess.TimeoutExpired:
            logger.error(f"SIGKILL 后工作进程仍未回收（pid={proc.pid}）")
        return "SIGKILL"


def _read_json(path: str) -> dict | None:
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except Exception as e:  # noqa: BLE001
        logger.warning(f"读取 JSON 失败 {path}：{e}")
        return None


def _is_valid_worker_result(outcome: "WorkerOutcome", target_date: str) -> bool:
    """校验工作进程结果契约；非法结果按会话异常处理。"""
    result = outcome.result
    if not isinstance(result, dict):
        return False
    if result.get("contract_version") != CONTRACT_VERSION:
        return False
    if result.get("mode") != "chunk" or result.get("target_date") != target_date:
        return False
    processed = result.get("processed")
    failed_codes = result.get("failed_codes")
    if (
        not isinstance(processed, int)
        or isinstance(processed, bool)
        or not 0 <= processed <= len(outcome.chunk_codes)
        or not isinstance(failed_codes, list)
        or not all(isinstance(code, str) for code in failed_codes)
    ):
        return False
    chunk_codes = set(outcome.chunk_codes)
    processed_prefix = set(outcome.chunk_codes[:processed])
    return (
        result.get("chunk_index") == outcome.chunk_index
        and result.get("chunk_size") == len(outcome.chunk_codes)
        and len(failed_codes) <= processed
        and len(set(failed_codes)) == len(failed_codes)
        and all(
            code in chunk_codes and code in processed_prefix
            for code in failed_codes
        )
    )


# ---------------------------------------------------------------------------
# 监督者
# ---------------------------------------------------------------------------


@dataclass
class CollectionRound:
    """单轮分片执行记录（写入 details.rounds）。"""

    round_index: int
    chunk_index: int
    chunk_size: int
    chunk_start_code: str
    chunk_end_code: str
    timed_out: bool
    exit_code: int | None
    duration_seconds: float
    covered_before: int
    covered_after: int
    newly_covered: int
    last_started_code: str | None = None
    killed: bool = False
    error: str = ""


@dataclass
class CollectionOutcome:
    """监督者最终结果（兼容 UpdateResult 字段 + 可观测 details）。"""

    target_date: str
    total: int
    covered: int
    success: int
    failed: int
    skipped: int
    completion_mode: str
    stop_reason: str
    rounds: list[CollectionRound] = field(default_factory=list)
    timed_out_chunks: list[dict] = field(default_factory=list)
    quarantined_codes: list[str] = field(default_factory=list)
    """超时后延后/隔离的股票（最终仍缺目标日时计入 failed）"""
    worker_processes: int = 0
    session_restarts: int = 0
    started_at: datetime | None = None
    finished_at: datetime | None = None
    deadline: datetime | None = None

    @property
    def coverage_rate(self) -> float:
        return (self.covered / self.total) if self.total > 0 else 0.0

    def to_details(self) -> dict:
        """序列化为 task_status.details（全部 JSON 可序列化）。"""
        return {
            "collection_mode": "chunked",
            "contract_version": CONTRACT_VERSION,
            "completion_mode": self.completion_mode,
            "stop_reason": self.stop_reason,
            "target_date": self.target_date,
            "total": self.total,
            "covered": self.covered,
            "coverage_rate": round(self.coverage_rate, 6),
            "processed_rate": round(
                (self.success + self.failed + self.skipped) / self.total, 6
            ) if self.total > 0 else 0.0,
            "success": self.success,
            "failed": self.failed,
            "skipped": self.skipped,
            "processed_count": self.success + self.failed + self.skipped,
            "quarantined_codes": list(self.quarantined_codes),
            "worker_processes": self.worker_processes,
            "session_restarts": self.session_restarts,
            "timed_out_chunks": list(self.timed_out_chunks),
            "rounds": [
                {
                    "round": r.round_index,
                    "chunk_index": r.chunk_index,
                    "chunk_size": r.chunk_size,
                    "chunk_start_code": r.chunk_start_code,
                    "chunk_end_code": r.chunk_end_code,
                    "timed_out": r.timed_out,
                    "killed": r.killed,
                    "exit_code": r.exit_code,
                    "duration_seconds": r.duration_seconds,
                    "covered_before": r.covered_before,
                    "covered_after": r.covered_after,
                    "newly_covered": r.newly_covered,
                    "last_started_code": r.last_started_code,
                    "error": r.error,
                }
                for r in self.rounds
            ],
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "deadline": self.deadline.isoformat() if self.deadline else None,
            "duration_seconds": round(
                (self.finished_at - self.started_at).total_seconds(), 3
            ) if self.started_at and self.finished_at else None,
        }


def _chunk_missing(
    missing_codes: list[str], pool_index: dict[str, dict], chunk_size: int
) -> list[dict]:
    return [pool_index[code] for code in missing_codes[:chunk_size]]


def run_supervised_collection(
    target_date: str,
    config: CollectionConfig,
    store,
    deadline: datetime,
    runner=None,
    now_fn=None,
) -> CollectionOutcome:
    """按分片串行采集目标日缺口，超时换新进程从数据库断点继续。

    Args:
        target_date: 目标交易日 YYYY-MM-DD
        config: CollectionConfig
        store: 需实现 pool() / target_date_codes(target_date)
        deadline: 整体截止时间（超过后返回失败，不悬挂）
        runner: 可注入的工作进程运行器（测试用 fake）
        now_fn: 可注入时钟（测试用）
    """
    now_fn = now_fn or (lambda: datetime.now(TZ))
    runner = runner or SubprocessWorkerRunner(config)

    started_at = now_fn()
    pool = store.pool()
    total = len(pool)
    pool_index = {item["code"]: item for item in pool}
    outcome = CollectionOutcome(
        target_date=target_date,
        total=total,
        covered=0,
        success=0,
        failed=0,
        skipped=0,
        completion_mode=MODE_FAILED,
        stop_reason=STOP_DB_ERROR,
        started_at=started_at,
        deadline=deadline,
    )

    if total <= 0:
        outcome.stop_reason = STOP_DB_ERROR
        outcome.finished_at = now_fn()
        logger.error("股票池为空（total=0），分片采集无法进行")
        return outcome

    work_dir = tempfile.mkdtemp(prefix=f"kline_chunks_{target_date}_")
    timeout_occurred = False
    attempted_codes: set[str] = set()
    # 已发生超时的股票 → 超时次数；用于延后/隔离与单股重试上限
    quarantine_attempts: dict[str, int] = {}
    consecutive_no_progress = 0
    restart_count = 0
    round_index = 0
    latest_covered_codes: set[str] = set()
    has_coverage_snapshot = False

    try:
        while True:
            # 每次覆盖查询最多可消耗 db_query_timeout_seconds；若剩余窗口不足，
            # 不再启动新查询，以免查询本身突破整体绝对截止。
            remaining = (deadline - now_fn()).total_seconds()
            if remaining <= config.db_query_timeout_seconds:
                logger.error(f"剩余时间不足数据库查询预算，停止于整体截止时间 {deadline}")
                outcome.stop_reason = STOP_DEADLINE
                break
            try:
                covered_codes = store.target_date_codes(target_date) & set(pool_index)
                latest_covered_codes = set(covered_codes)
                has_coverage_snapshot = True
            except Exception as e:  # noqa: BLE001
                logger.opt(exception=True).error(f"查询目标日覆盖失败：{e}")
                outcome.stop_reason = STOP_DB_ERROR
                break

            covered = len(covered_codes)
            outcome.covered = covered
            coverage = covered / total
            if coverage >= config.min_coverage_rate:
                outcome.stop_reason = STOP_COVERAGE_REACHED
                break

            missing = [
                code for code in (item["code"] for item in pool)
                if code not in covered_codes
            ]
            if not missing:
                outcome.stop_reason = STOP_NO_MISSING
                break

            remaining = (deadline - now_fn()).total_seconds()
            # 超时后仍需给 SIGTERM 宽限和 SIGKILL 回收各自留足时间；若不预留，
            # worker wait 会吃完整个 remaining，清理阶段必然突破绝对截止。
            termination_budget = (
                config.worker_terminate_grace_seconds
                + max(config.worker_terminate_grace_seconds, 1)
            )
            # worker 返回后还必须留出一次覆盖查询预算，才能基于数据库事实
            # 判断本轮进展；查询预算也属于绝对截止的一部分。
            runnable_seconds = (
                remaining - termination_budget - config.db_query_timeout_seconds
            )
            if runnable_seconds <= 0:
                logger.error(f"已到达采集整体截止时间 {deadline}，停止分片采集")
                outcome.stop_reason = STOP_DEADLINE
                break

            # 优先处理普通缺口；已超时股票延后到普通缺口耗尽后再按额度重试
            selected, is_retry_round = _select_chunk_codes(
                missing,
                quarantine_attempts,
                config.max_quarantined_retries,
                config.worker_chunk_size,
            )
            if not selected:
                logger.error(
                    f"剩余 {len(missing)} 只缺口全部为已隔离的挂死股票，"
                    f"重试额度（{config.max_quarantined_retries}）已用完，停止采集"
                )
                outcome.stop_reason = STOP_QUARANTINED_EXHAUSTED
                break

            chunk = _chunk_missing(selected, pool_index, config.worker_chunk_size)
            chunk_timeout = min(float(config.worker_timeout_seconds), runnable_seconds)
            chunk_index = round_index
            worker_outcome = runner.run(
                chunk=chunk,
                chunk_index=chunk_index,
                round_index=chunk_index,
                timeout_seconds=chunk_timeout,
                work_dir=work_dir,
                target_date=target_date,
                pool_size=total,
            )
            outcome.worker_processes += 1

            valid_result = _is_valid_worker_result(worker_outcome, target_date)
            abnormal_exit = (
                worker_outcome.timed_out
                or not worker_outcome.pid_gone
                or worker_outcome.exit_code not in (
                    EXIT_CHUNK_COMPLETE,
                    EXIT_CHUNK_BREAKER,
                )
                or not valid_result
            )
            attempted_codes.update(
                _attempted_codes(worker_outcome, trust_result=valid_result)
            )

            # 旧 PID 未回收时不得先发起任何轮后数据库访问，否则查询异常会掩盖
            # 更高风险的残留进程，还可能与下一轮产生并发会话。
            if not worker_outcome.pid_gone:
                outcome.rounds.append(
                    CollectionRound(
                        round_index=chunk_index,
                        chunk_index=chunk_index,
                        chunk_size=len(chunk),
                        chunk_start_code=chunk[0]["code"] if chunk else "",
                        chunk_end_code=chunk[-1]["code"] if chunk else "",
                        timed_out=worker_outcome.timed_out,
                        exit_code=worker_outcome.exit_code,
                        duration_seconds=worker_outcome.duration_seconds,
                        covered_before=covered,
                        covered_after=covered,
                        newly_covered=0,
                        last_started_code=worker_outcome.last_started_code,
                        killed=worker_outcome.killed,
                        error=worker_outcome.error or "worker_pid_not_reaped",
                    )
                )
                logger.error(
                    f"旧工作进程 PID {worker_outcome.pid} 未回收，禁止轮后查询和新会话"
                )
                outcome.stop_reason = STOP_WORKER_FATAL
                break

            post_query_skipped = (
                deadline - now_fn()
            ).total_seconds() <= config.db_query_timeout_seconds
            if post_query_skipped:
                # 仍记录 worker 的超时/尝试信息，但不再启动可能越界的查询；
                # 最终统计复用最近一次数据库覆盖快照，保持保守失败语义。
                covered_after = covered
            else:
                try:
                    covered_after_codes = (
                        store.target_date_codes(target_date) & set(pool_index)
                    )
                    latest_covered_codes = set(covered_after_codes)
                    has_coverage_snapshot = True
                    covered_after = len(covered_after_codes)
                except Exception as e:  # noqa: BLE001
                    logger.opt(exception=True).error(f"查询目标日覆盖失败（轮次后）：{e}")
                    outcome.stop_reason = STOP_DB_ERROR
                    break

            newly_covered = max(0, covered_after - covered)
            outcome.covered = covered_after

            collection_round = CollectionRound(
                round_index=chunk_index,
                chunk_index=chunk_index,
                chunk_size=len(chunk),
                chunk_start_code=chunk[0]["code"] if chunk else "",
                chunk_end_code=chunk[-1]["code"] if chunk else "",
                timed_out=worker_outcome.timed_out,
                exit_code=worker_outcome.exit_code,
                duration_seconds=worker_outcome.duration_seconds,
                covered_before=covered,
                covered_after=covered_after,
                newly_covered=newly_covered,
                last_started_code=worker_outcome.last_started_code,
                killed=worker_outcome.killed,
                error=worker_outcome.error,
            )
            outcome.rounds.append(collection_round)

            newly_quarantined = False
            if worker_outcome.timed_out:
                timeout_occurred = True
                outcome.timed_out_chunks.append(
                    {
                        "round": chunk_index,
                        "chunk_index": chunk_index,
                        "chunk_size": len(chunk),
                        "chunk_start_code": collection_round.chunk_start_code,
                        "chunk_end_code": collection_round.chunk_end_code,
                        "last_started_code": worker_outcome.last_started_code,
                        "retry_round": is_retry_round,
                        "newly_covered": newly_covered,
                        "killed": worker_outcome.killed,
                        "kill_signal": worker_outcome.kill_signal,
                        "pid_gone": worker_outcome.pid_gone,
                    }
                )
                if not worker_outcome.pid_gone:
                    logger.error(
                        f"工作进程 PID {worker_outcome.pid} 在 SIGKILL 后仍未消失"
                    )
                hung_code = worker_outcome.last_started_code
                if hung_code and hung_code in pool_index:
                    newly_quarantined = hung_code not in quarantine_attempts
                    attempts = quarantine_attempts.get(hung_code, 0) + 1
                    quarantine_attempts[hung_code] = attempts
                    logger.warning(
                        f"股票 {hung_code} 超时（第 {attempts} 次），加入延后队列；"
                        f"普通缺口处理完后最多再重试 {config.max_quarantined_retries} 次"
                    )

            logger.info(
                f"分片完成：round={chunk_index} chunk={chunk_index} "
                f"覆盖 {covered_after}/{total}"
                f"（本片 +{newly_covered}，{(newly_covered / total * 100) if total else 0.0:+.2f}%）"
                f" 超时={worker_outcome.timed_out} 退出码={worker_outcome.exit_code}"
                f" 耗时={worker_outcome.duration_seconds:.1f}s 延后轮次={is_retry_round}"
            )

            if (
                not abnormal_exit
                and newly_covered <= 0
                and not is_retry_round
                and not newly_quarantined
            ):
                consecutive_no_progress += 1
            elif newly_covered > 0 or newly_quarantined:
                consecutive_no_progress = 0

            # 本轮未能正常完成分片 → 计一次会话重建（下次必须用新进程/新会话）
            if abnormal_exit:
                restart_count += 1
                outcome.session_restarts = restart_count
                if restart_count > config.max_session_restarts:
                    logger.error(
                        f"会话重建次数达到上限 {config.max_session_restarts}，停止恢复"
                    )
                    outcome.stop_reason = STOP_RESTART_LIMIT
                    break

            if consecutive_no_progress >= config.max_consecutive_no_progress_rounds:
                logger.error(
                    f"连续 {consecutive_no_progress} 轮无新增覆盖，停止分片采集"
                )
                outcome.stop_reason = STOP_NO_PROGRESS
                break

            if not config.immediate_recovery_enabled and abnormal_exit:
                logger.error("immediate_recovery_enabled=False，主采集失败后不立即恢复")
                outcome.stop_reason = STOP_WORKER_FATAL
                break

            if post_query_skipped:
                outcome.stop_reason = STOP_DEADLINE
                break

            if (deadline - now_fn()).total_seconds() <= 0:
                outcome.stop_reason = STOP_DEADLINE
                break

            round_index += 1
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)

    # 最终覆盖以数据库为准；失败 = 已尝试过但仍缺目标日，跳过 = 从未尝试
    final_query_budget_available = (
        outcome.stop_reason != STOP_WORKER_FATAL
        and (deadline - now_fn()).total_seconds()
        > config.db_query_timeout_seconds
    )
    if final_query_budget_available:
        try:
            final_codes = store.target_date_codes(target_date) & set(pool_index)
            latest_covered_codes = set(final_codes)
            has_coverage_snapshot = True
        except Exception as e:  # noqa: BLE001
            logger.opt(exception=True).error(f"最终覆盖查询失败：{e}")
            outcome.stop_reason = STOP_DB_ERROR
            final_codes = set()
    else:
        final_codes = set(latest_covered_codes) if has_coverage_snapshot else set()
    covered = len(final_codes)
    failed_codes = {
        code for code in attempted_codes
        if code in pool_index and code not in final_codes
    }
    outcome.covered = covered
    outcome.success = covered
    outcome.failed = len(failed_codes)
    outcome.skipped = max(0, total - covered - len(failed_codes))
    outcome.quarantined_codes = sorted(
        code for code in quarantine_attempts if code not in final_codes
    )
    outcome.finished_at = now_fn()

    coverage_ok = outcome.coverage_rate >= config.min_coverage_rate
    if outcome.stop_reason in (STOP_COVERAGE_REACHED, STOP_NO_MISSING) and coverage_ok:
        outcome.completion_mode = MODE_RECOVERED if timeout_occurred else MODE_NORMAL
    else:
        outcome.completion_mode = MODE_FAILED
        if outcome.stop_reason in (STOP_COVERAGE_REACHED, STOP_NO_MISSING):
            # 覆盖未达红线时不允许声称完成（例如股票池重复 code 导致去重后仍缺口）
            outcome.stop_reason = STOP_COVERAGE_SHORT

    logger.info(
        f"分片采集结束：mode={outcome.completion_mode} stop={outcome.stop_reason} "
        f"覆盖 {outcome.covered}/{outcome.total}（{outcome.coverage_rate * 100:.2f}%）"
        f" 失败={outcome.failed} 跳过={outcome.skipped}"
        f" 工作进程={outcome.worker_processes} 会话重建={outcome.session_restarts}"
        f" 超时分片={len(outcome.timed_out_chunks)}"
        f" 隔离股票={outcome.quarantined_codes}"
    )
    return outcome


def _select_chunk_codes(
    missing: list[str],
    quarantine_attempts: dict[str, int],
    retry_limit: int,
    chunk_size: int,
) -> tuple[list[str], bool]:
    """选择本轮要处理的缺口代码。

    普通缺口优先（保证永久挂死股票不会反复占据队首）；
    普通缺口耗尽后才按额度重试已隔离股票，避免同一只股票阻塞全部后续缺口。

    Returns:
        (codes, is_retry_round)
    """
    normal = [code for code in missing if code not in quarantine_attempts]
    if normal:
        return normal[:chunk_size], False
    retryable = [
        code for code in missing
        if quarantine_attempts.get(code, 0) <= retry_limit
    ]
    if retryable:
        return retryable[:chunk_size], True
    return [], False


def _attempted_codes(
    outcome: WorkerOutcome,
    *,
    trust_result: bool = True,
) -> set[str]:
    """推导本轮工作进程真正发出过请求的股票集合。

    - 有 result：取 processed 前缀，并补上 result.failed_codes；
    - 无 result（超时/被杀）：按 last_started_code 在分片中的位置取已尝试前缀；
    - 无进度文件：返回空集，不臆造。
    """
    if trust_result and outcome.result:
        processed = outcome.result.get("processed")
        if isinstance(processed, int) and not isinstance(processed, bool):
            codes = list(outcome.chunk_codes[: max(0, processed)])
        elif outcome.exit_code == EXIT_CHUNK_COMPLETE:
            codes = list(outcome.chunk_codes)
        else:
            codes = []
        failed = outcome.result.get("failed_codes") or []
        codes.extend(str(code) for code in failed if isinstance(code, str))
        return set(codes)

    last = outcome.last_started_code
    if last and last in outcome.chunk_codes:
        return set(outcome.chunk_codes[: outcome.chunk_codes.index(last) + 1])
    return set()
