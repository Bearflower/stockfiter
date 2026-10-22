#!/usr/bin/env python3
"""每日 K 线数据更新脚本（分片工作进程 + 监督者）。

2026-10-22 改造（tasks/plan.md）：
  - 彻底移除 ThreadPoolExecutor 单股"伪超时"：Baostock 请求不可安全中断，
    线程池超时后会泄漏挂起线程并污染会话。
  - 每条命令都是纯串行工作进程（--worker）或监督者（默认入口）。
  - 监督者按分片启动独立 OS 进程，进程级超时后 terminate → 宽限期 → kill，
    再从数据库缺口继续；详见 scripts/data/kline_collection.py。
  - 完整性判定仍由 UpdateResult.is_complete 的既有 4 步顺序决定，不放宽 98% 红线。
"""

import os
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd
import yaml

from utils.logger import get_logger
from data.database import DatabaseManager

logger = get_logger()


# ==================== 配置加载 ====================

def load_kline_update_config(config_path: str = 'config/config.yaml') -> dict:
    """加载 config.yaml 中的 global.kline_update 节。

    保留配置文件中的**全部键**（含分片采集配置），再对历史键补齐默认值，
    避免新增配置被过滤后静默丢失。

    Args:
        config_path: 配置文件路径

    Returns:
        dict: kline_update 配置字典
    """
    section: dict = {}
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            full = yaml.safe_load(f) or {}
        section = dict((full.get('global') or {}).get('kline_update') or {})
    except Exception as e:
        logger.warning(f"加载 config/config.yaml 失败，使用默认值：{e}")

    cfg = dict(section)
    # 默认值与原脚本硬编码保持一致（仅历史键，新增分片配置必须显式写入 config.yaml）
    defaults = {
        'days_to_update': 5,
        'failure_rate_threshold': 0.7,
        'failure_window_size': 100,
        'failure_window_min_samples': 50,
        'total_failure_limit': 400,
        'min_success_rate': 0.9,
        'min_coverage_rate': 0.98,
        'request_interval_seconds': 0.05,
        'target_date_max_backtrack': 7,
        'skipped_ratio_guard': 0.95,
    }
    for key, value in defaults.items():
        cfg.setdefault(key, value)
    return cfg


# ==================== UpdateResult ====================

@dataclass
class UpdateResult:
    """K 线更新结果数据类（R13 + R-01 完整覆盖率改造 + B2 非交易日语义修复）。

    替代原脚本 `update_all_klines()` 的 None 返回值，
    让 scheduler 和下游可精确判定「数据完整性」。

    2026-10-22：分片采集模式下 success = 目标日已落库股票数（数据库事实），
    failed = 本轮尝试过但最终仍缺目标日的股票数，skipped = 从未尝试的缺口。
    """
    success: int = 0
    """目标交易日已有 K 线数据的股票数（分片模式下以数据库为准）"""
    failed: int = 0
    """尝试过但最终仍缺目标日数据的股票数"""
    skipped: int = 0
    """未尝试的股票数（例如被 kill 的分片内尚未处理的部分）"""
    total: int = 0
    """股票池总数（未处理的股票也计入分母）"""
    latest_trade_date: Optional[str] = None
    """数据库中最新交易日期（YYYY-MM-DD），用于完整性校验"""
    target_date: Optional[str] = None
    """本次任务的目标交易日（最近一个 ≤ 当前时间的交易日）"""
    target_date_success: int = 0
    """target_date 当天数据库中已有 K 线数据的股票数"""
    is_trading_day_degraded: Optional[bool] = None
    """is_trading_day 是否走了 weekday 降级推断（Baostock 不可用）。
    None 表示未做推断或结果不确定；True=降级推断，False=Baostock 验证过。
    仅供运维观测与交接记录，判定层不消费。"""
    target_date_inferred_non_trading: bool = False
    """结合 degraded_flag + 库内数据推断出 target_date 大概率非交易日。
    仅供运维观测与交接记录，判定层不消费（判定层严格走 4 步顺序，无此字段短路）。"""
    completion_mode: str = "normal"
    """normal / recovered_after_timeout / failed；写入 details，不改变 status 枚举"""
    details: Optional[dict] = None
    """分片采集结构化 details（写入 task_status.details）"""

    @property
    def processed_count(self) -> int:
        """实际处理过的股票数（success + failed + skipped）。"""
        return self.success + self.failed + self.skipped

    @property
    def coverage_rate(self) -> float:
        """处理覆盖率 = processed_count / total（success+failed+skipped，通常为 1.0）。

        注意：这不是目标日真实覆盖率；真实覆盖率必须读 target_date_coverage_rate，
        details.coverage_rate 也固定指向真实覆盖率。
        """
        if self.total <= 0:
            return 0.0
        return self.processed_count / self.total

    @property
    def target_date_coverage_rate(self) -> float:
        """目标交易日覆盖率 = target_date_success / total；total <= 0 返回 0.0"""
        if self.total <= 0:
            return 0.0
        return self.target_date_success / self.total

    @property
    def total_processed(self) -> int:
        """参与成功/失败计数的股票数（用于计算成功率）。"""
        return self.success + self.failed

    @property
    def success_rate(self) -> float:
        """成功率 = 成功 / (成功 + 失败)；分母为 0 时返回 1.0"""
        if self.total_processed == 0:
            return 1.0
        return self.success / self.total_processed

    @property
    def skipped_ratio(self) -> float:
        """跳过股票占比 = skipped / total；total 为 0 时返回 0"""
        if self.total == 0:
            return 0.0
        return self.skipped / self.total

    def is_complete(
        self,
        min_success_rate: float,
        total_failure_limit: int,
        min_coverage_rate: float,
        target_date: Optional[str] = None,
        skipped_ratio_guard: float = 0.95,
    ) -> bool:
        """判定本次更新是否完整（严格对齐第 9.2 节 4 条顺序）。

        判定顺序（禁止调换，后一条不得被前一条短路绕过）：
            1. total<=0 或 coverage_rate<min_coverage_rate → False（硬红线）
            2. target_date 为交易日且 target_date_coverage_rate<min_coverage_rate → False
            3. 仅当 target_date 非交易日 且 skipped_ratio>=skipped_ratio_guard → True
            4. 其余按 success_rate>=min_success_rate 且 failed<=total_failure_limit
        """
        if self.total <= 0 or self.coverage_rate < min_coverage_rate:
            return False

        if target_date:
            try:
                from utils.trade_calendar import is_trading_day
                is_tr_day = is_trading_day(target_date)
            except Exception:
                is_tr_day = False
            if is_tr_day and self.target_date_coverage_rate < min_coverage_rate:
                return False
            if not is_tr_day and self.skipped_ratio >= skipped_ratio_guard:
                return True

        return (
            self.success_rate >= min_success_rate
            and self.failed <= total_failure_limit
        )


# ==================== 分片工作结果 ====================

@dataclass
class ChunkResult:
    """单个分片工作进程的结果（写入 result JSON，供监督者消费）。"""

    target_date: str
    chunk_index: int
    chunk_size: int
    total: int
    success: int = 0
    failed: int = 0
    skipped: int = 0
    failed_codes: list = field(default_factory=list)
    stop_reason: str = "chunk_complete"
    last_started_code: Optional[str] = None
    target_date_covered: int = 0
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    duration_seconds: float = 0.0

    def to_dict(self) -> dict:
        from scripts.data.kline_collection import CONTRACT_VERSION
        return {
            "contract_version": CONTRACT_VERSION,
            "mode": "chunk",
            "target_date": self.target_date,
            "chunk_index": self.chunk_index,
            "chunk_size": self.chunk_size,
            "pool_size": self.total,
            "success": self.success,
            "failed": self.failed,
            "skipped": self.skipped,
            "processed": self.success + self.failed + self.skipped,
            "failed_codes": self.failed_codes,
            "stop_reason": self.stop_reason,
            "last_started_code": self.last_started_code,
            "target_date_covered": self.target_date_covered,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_seconds": self.duration_seconds,
        }


class DailyKlineUpdater:
    """K 线采集执行器（纯串行，无线程池）。"""

    def __init__(self, days_to_update: int = 5, config: Optional[dict] = None):
        """
        Args:
            days_to_update: 更新最近多少天的数据（保留给历史调用方）
            config: global.kline_update 配置字典，None 时自动加载 config.yaml
        """
        self.days_to_update = days_to_update
        self.cfg = config or load_kline_update_config()
        self.db = None

    # -------------------- 单股抓取（纯串行，无伪超时） --------------------

    @staticmethod
    def fetch_kline(session, symbol: str, days: int = 120):
        """直接调用 Baostock 抓取单股 K 线。

        不允许在这里用线程池或信号做单股超时：Baostock 请求不可安全取消，
        伪超时只会泄漏挂起线程并让会话进入不可复用状态。
        超时统一由外层监督者终止整个工作进程（scripts/data/kline_collection.py）。
        """
        return session.get_kline(symbol, None, days)

    @staticmethod
    def _parse_latest_date(latest_data, today_date):
        """统一解析数据库返回的 latest_kline_date。"""
        from datetime import datetime as dt_class
        if latest_data is None:
            return today_date
        if isinstance(latest_data, dt_class):
            return latest_data.date()
        if isinstance(latest_data, str):
            try:
                return dt_class.strptime(latest_data[:10], '%Y-%m-%d').date()
            except (ValueError, TypeError):
                return today_date
        if hasattr(latest_data, 'date'):
            return latest_data.date()
        return latest_data if hasattr(latest_data, 'day') else today_date

    @staticmethod
    def _latest_to_datetime(latest_data, fallback_date):
        """将数据库 latest_kline_date 统一转为 datetime（凌晨零点）。"""
        from datetime import datetime as dt_class
        if latest_data is None:
            return dt_class.combine(fallback_date, dt_class.min.time())
        if isinstance(latest_data, dt_class):
            return latest_data
        if isinstance(latest_data, str):
            try:
                return dt_class.strptime(latest_data[:10], '%Y-%m-%d')
            except (ValueError, TypeError):
                return dt_class.combine(fallback_date, dt_class.min.time())
        parsed = DailyKlineUpdater._parse_latest_date(latest_data, fallback_date)
        return dt_class.combine(parsed, dt_class.min.time())

    @staticmethod
    def _compute_target_date(today_date, max_backtrack: int = 7):
        """计算目标交易日 = 最近一个 ≤ today_date 的交易日。"""
        from utils.trade_calendar import is_trading_day_with_confidence

        degraded = False
        for offset in range(max_backtrack):
            candidate = today_date - timedelta(days=offset)
            candidate_str = candidate.strftime("%Y-%m-%d")
            is_tr, degraded = is_trading_day_with_confidence(candidate_str)
            if is_tr:
                return candidate_str, degraded

        return None, degraded

    # -------------------- 分片串行采集 --------------------

    def collect(
        self,
        target_date: str,
        stocks: list,
        pool_size: int,
        chunk_index: int = 0,
        progress_writer=None,
    ) -> ChunkResult:
        """串行采集一个分片，逐股落库并校验目标日行。

        Args:
            target_date: 目标交易日 YYYY-MM-DD
            stocks: [{"code": ..., "symbol": ...}, ...]
            pool_size: 股票池总数（用于 result JSON 的覆盖率口径）
            chunk_index: 分片序号（仅用于可观测性）
            progress_writer: 可选的进度回调，签名 (code, processed) -> None

        Returns:
            ChunkResult
        """
        from data.fetcher import BaostockSession

        started = time.monotonic()
        started_at = datetime.now().isoformat(timespec="seconds")
        target_ts = pd.Timestamp(target_date)
        today_date = datetime.now().date()

        cfg = self.cfg
        window_size = cfg['failure_window_size']
        min_samples = cfg['failure_window_min_samples']
        rate_threshold = cfg['failure_rate_threshold']
        total_limit = cfg['total_failure_limit']
        interval = cfg['request_interval_seconds']

        result = ChunkResult(
            target_date=target_date,
            chunk_index=chunk_index,
            chunk_size=len(stocks),
            total=pool_size,
            started_at=started_at,
        )
        recent_results = deque(maxlen=window_size)

        def _mark_failure(code: str, message: str) -> None:
            """记录一次采集失败，供熔断判定与失败明细使用。"""
            result.failed += 1
            result.failed_codes.append(code)
            recent_results.append(1)
            logger.warning(f"{code} {message}")

        self.db = DatabaseManager()
        try:
            with BaostockSession() as session:
                for row in stocks:
                    code = row['code']
                    symbol = row['symbol']
                    if progress_writer is not None:
                        try:
                            progress_writer(code, result.processed_count)
                        except Exception as e:  # noqa: BLE001
                            logger.warning(f"写入进度文件失败：{e}")

                    latest_data = self.db.get_latest_kline_date(code)
                    latest_date_obj = self._parse_latest_date(latest_data, today_date)
                    if latest_date_obj == datetime.strptime(target_date, '%Y-%m-%d').date():
                        result.skipped += 1
                        recent_results.append(0)
                        continue

                    kline_df = None
                    try:
                        kline_df = self.fetch_kline(session, symbol, days=120)
                        if kline_df is None:
                            _mark_failure(code, "更新失败：返回 None")
                        elif len(kline_df) == 0:
                            _mark_failure(code, "更新失败：返回空数据")
                        else:
                            contains_target = bool(
                                (pd.to_datetime(kline_df['date']) == target_ts).any()
                            )
                            try:
                                if latest_data is not None:
                                    latest_dt = self._latest_to_datetime(latest_data, today_date)
                                    new_df = kline_df[
                                        pd.to_datetime(kline_df['date']) > latest_dt
                                    ]
                                else:
                                    new_df = kline_df
                                if len(new_df) > 0:
                                    self.db.save_kline_history(code, new_df)
                            except Exception as e:  # noqa: BLE001
                                _mark_failure(code, f"写入失败：{e}")
                            else:
                                if contains_target:
                                    result.success += 1
                                    recent_results.append(0)
                                else:
                                    # 返回数据不含目标交易日 → 截断/停牌，不能算成功
                                    _mark_failure(
                                        code,
                                        f"返回 {len(kline_df)} 条数据但不含 {target_date}",
                                    )
                    except Exception as e:  # noqa: BLE001
                        _mark_failure(code, f"更新失败：{e}")

                    if len(recent_results) >= min_samples:
                        failure_rate = sum(recent_results) / len(recent_results)
                        if failure_rate > rate_threshold:
                            logger.warning(
                                f"本分片最近 {len(recent_results)} 次请求失败率 "
                                f"{failure_rate:.0%}，超过阈值 {rate_threshold:.0%}，提前终止"
                            )
                            result.stop_reason = "failure_rate"
                            break

                    if result.failed >= total_limit:
                        logger.warning(
                            f"本分片累计失败 {result.failed} 次，达到硬上限 {total_limit}，提前终止"
                        )
                        result.stop_reason = "failure_limit"
                        break

                    time.sleep(interval)
        finally:
            try:
                result.target_date_covered = self._count_target_date(target_date)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"统计目标日覆盖失败：{e}")
            if self.db is not None:
                try:
                    self.db.close()
                except Exception:  # noqa: BLE001
                    pass
            result.finished_at = datetime.now().isoformat(timespec="seconds")
            result.duration_seconds = round(time.monotonic() - started, 3)

        logger.info(
            f"分片 {chunk_index} 完成：成功 {result.success}，失败 {result.failed}，"
            f"跳过 {result.skipped}，目标日覆盖 {result.target_date_covered}，"
            f"停止原因 {result.stop_reason}"
        )
        return result

    def _count_target_date(self, target_date: str) -> int:
        cur = self.db.conn.cursor()
        try:
            cur.execute(
                "SELECT COUNT(DISTINCT code) FROM klines WHERE date = %s AND frequency = 'd'",
                (target_date,),
            )
            row = cur.fetchone()
            return int(row[0]) if row and row[0] is not None else 0
        finally:
            cur.close()

    def _verify_database(self) -> None:
        """验证数据库中的 K 线数据（仅日志）。"""
        df = pd.read_sql_query(
            """
            SELECT
                COUNT(*) as total_records,
                COUNT(DISTINCT code) as stocks_with_data,
                MIN(date) as earliest_date,
                MAX(date) as latest_date
            FROM klines
            """,
            self.db.conn
        )
        logger.info(f"总记录数：{df['total_records'].iloc[0]:,}")
        logger.info(f"有数据的股票：{df['stocks_with_data'].iloc[0]} 只")
        logger.info(f"最早日期：{df['earliest_date'].iloc[0]}")
        logger.info(f"最新日期：{df['latest_date'].iloc[0]}")


# ==================== 业务入口（监督者） ====================

def run_update(
    days: Optional[int] = None,
    cfg: Optional[dict] = None,
    deadline: Optional[datetime] = None,
    strict_deadline: bool = True,
    pool_df=None,
    runner=None,
) -> UpdateResult:
    """业务入口：分片监督采集，正常 return UpdateResult，不抛 SystemExit。

    scheduler_kline 23:00 任务与 main.py --all 都使用本入口，不存在不受监督的
    全量采集旁路。

    Args:
        days: 更新最近多少天的数据。None 时从 config.yaml global.kline_update.days_to_update 读取。
        cfg: 外部注入的 kline_update 配置字典（含分片采集配置）。
        deadline: 整体截止时间。None 时按 initial_scan_time + KLINE_WAIT_TIMEOUT - buffer 推导。
        strict_deadline: 推导出的截止时间已过时是否严格失败。
            自动调度入口传 True（立即失败，不越过 OBPC 等待窗口）；
            人工入口 main.py --all 传 False（从当前时刻滚动一个可用窗口）。
        pool_df: 股票池 DataFrame（测试可注入）。
        runner: 工作进程运行器（测试可注入 fake）。

    Returns:
        UpdateResult: 更新结果（含完整性判定所需字段和 details）。
    """
    from scripts.data.kline_collection import (
        CollectionConfigError,
        DatabaseGapStore,
        compute_collection_deadline,
        load_collection_config,
        load_stock_pool,
        run_supervised_collection,
        STOP_CONFIG_ERROR,
        MODE_FAILED,
    )

    effective_cfg = cfg or load_kline_update_config()
    effective_days = days if days is not None else effective_cfg['days_to_update']
    updater = DailyKlineUpdater(days_to_update=effective_days, config=effective_cfg)

    # 配置必须在任何数据库访问前 fail-closed，避免非法配置下股票池查询或
    # DatabaseManager 构造期迁移无界阻塞。
    try:
        collection_cfg = load_collection_config(effective_cfg)
    except CollectionConfigError as e:
        logger.error(f"分片采集配置非法，拒绝启动采集：{e}")
        return UpdateResult(
            total=0, target_date=None, completion_mode=MODE_FAILED,
            details={"stop_reason": STOP_CONFIG_ERROR, "error": str(e)},
        )

    # 股票池先加载：非交易日守卫与空池都需要 total
    if pool_df is None:
        pool_df = load_stock_pool(
            connect_timeout=collection_cfg.db_connect_timeout_seconds,
            statement_timeout=collection_cfg.db_query_timeout_seconds,
        )
    if pool_df is None or getattr(pool_df, 'empty', True):
        logger.error("股票列表为空：无法执行 K 线采集")
        return UpdateResult(total=0, completion_mode=MODE_FAILED)

    total = len(pool_df)

    today_date = datetime.now().date()
    today_str = today_date.strftime("%Y-%m-%d")

    # 非交易日守卫（AC-2）：跳过采集循环，返回 PASS 语义结果
    from utils.trade_calendar import is_trading_day
    try:
        if not is_trading_day(today_str):
            logger.info(f"今日 {today_str} 非交易日，跳过 K 线采集循环，返回 PASS 语义结果")
            return UpdateResult(
                success=0, failed=0, skipped=total, total=total,
                target_date_inferred_non_trading=True,
                completion_mode="normal",
                details={"collection_mode": "skipped_non_trading_day", "target_date": today_str},
            )
    except Exception as e:  # noqa: BLE001
        logger.warning(f"非交易日守卫 is_trading_day 异常：{e}，保守继续执行采集")

    target_date, is_td_degraded = updater._compute_target_date(
        today_date, max_backtrack=effective_cfg.get('target_date_max_backtrack', 7),
    )
    if target_date is None:
        logger.error(
            f"最多回溯 {effective_cfg.get('target_date_max_backtrack')} 天仍未找到有效交易日，"
            "target_date=None"
        )
        return UpdateResult(
            total=total, target_date=None, completion_mode=MODE_FAILED,
            details={"stop_reason": "target_date_unresolved", "total": total},
        )

    if deadline is None:
        from zoneinfo import ZoneInfo
        deadline = compute_collection_deadline(
            datetime.now(ZoneInfo("Asia/Shanghai")),
            collection_cfg.initial_scan_time,
            collection_cfg.kline_wait_timeout_seconds,
            collection_cfg.recovery_deadline_buffer_seconds,
            strict=strict_deadline,
        )

    db = DatabaseManager(
        connect_timeout=collection_cfg.db_connect_timeout_seconds,
        statement_timeout=collection_cfg.db_query_timeout_seconds,
    )
    try:
        store = DatabaseGapStore(
            db, pool_df,
            query_timeout_seconds=collection_cfg.db_query_timeout_seconds,
        )
        outcome = run_supervised_collection(
            target_date=target_date,
            config=collection_cfg,
            store=store,
            deadline=deadline,
            runner=runner,
        )
        details = outcome.to_details()
        details.update({
            "is_trading_day_degraded": is_td_degraded,
            "target_date_inferred_non_trading": False,
        })
        return UpdateResult(
            success=outcome.success,
            failed=outcome.failed,
            skipped=outcome.skipped,
            total=outcome.total,
            target_date=target_date,
            target_date_success=outcome.covered,
            is_trading_day_degraded=is_td_degraded,
            completion_mode=outcome.completion_mode,
            details=details,
        )
    finally:
        try:
            db.close()
        except Exception:  # noqa: BLE001
            pass


def build_task_status_details(result: UpdateResult) -> dict:
    """构造写入 task_status.details 的字典。

    关键契约：`details.coverage_rate` 固定表示**目标日数据库覆盖率**，
    不得用 UpdateResult.coverage_rate（success+failed+skipped 的处理比例，通常为 1.0）
    覆盖它，否则 610/2911 的失败场景会在监控里显示成 100%。

    处理比例单独放在 `processed_rate`，需要两者都保留。
    """
    details = dict(result.details or {})
    details["completion_mode"] = result.completion_mode
    details["target_date_coverage_rate"] = round(result.target_date_coverage_rate, 6)
    details["processed_rate"] = round(result.coverage_rate, 6)
    if "coverage_rate" not in details:
        details["coverage_rate"] = round(result.target_date_coverage_rate, 6)
    return details


def _result_to_dict(result: UpdateResult) -> dict:
    """将 UpdateResult 序列化为 result JSON（兼容旧消费方 + 新 details）。"""
    data = {
        'success': result.success,
        'failed': result.failed,
        'skipped': result.skipped,
        'total': result.total,
        'processed_count': result.processed_count,
        'coverage_rate': round(result.coverage_rate, 6),
        'target_date': result.target_date,
        'target_date_success': result.target_date_success,
        'target_date_coverage_rate': round(result.target_date_coverage_rate, 6),
        'latest_trade_date': result.latest_trade_date,
        'success_rate': round(result.success_rate, 6),
        'skipped_ratio': round(result.skipped_ratio, 6),
        'is_trading_day_degraded': result.is_trading_day_degraded,
        'target_date_inferred_non_trading': result.target_date_inferred_non_trading,
        'completion_mode': result.completion_mode,
    }
    if result.details:
        data['collection'] = result.details
        # 常用的 details 字段平铺到顶层，避免监控脚本解析嵌套 JSON
        for key in (
            'collection_mode', 'stop_reason', 'worker_processes', 'session_restarts',
            'timed_out_chunks', 'rounds', 'started_at', 'finished_at', 'deadline',
            'duration_seconds', 'covered',
        ):
            if key in result.details:
                data[key] = result.details[key]
    return data


def _is_complete_for(result: UpdateResult, cfg: dict) -> bool:
    return result.is_complete(
        cfg['min_success_rate'],
        cfg['total_failure_limit'],
        cfg['min_coverage_rate'],
        target_date=result.target_date,
        skipped_ratio_guard=cfg['skipped_ratio_guard'],
    )


# ==================== 工作进程入口 ====================

def run_worker(args) -> int:
    """工作进程：串行处理一个分片，写 result/progress JSON 后按契约退出。

    退出码：
      0 = 分片内全部股票已尝试（允许单股失败）
      1 = 被失败率/累计失败熔断提前终止（result JSON 仍写入）
      2 = 配置/数据库/未捕获异常（result JSON 可能缺失）
    """
    import json

    from scripts.data.kline_collection import (
        EXIT_CHUNK_BREAKER,
        EXIT_CHUNK_COMPLETE,
        EXIT_WORKER_FATAL,
        load_collection_config,
    )

    try:
        if not args.target_date or not args.symbols_file:
            raise ValueError("--worker 必须同时提供 --target-date 与 --symbols-file")
        with open(args.symbols_file, 'r', encoding='utf-8') as f:
            payload = json.load(f)
        stocks = payload.get('stocks') or []
        pool_size = int(args.pool_size or payload.get('pool_size') or 0)
        if pool_size <= 0:
            raise ValueError("pool_size 必须为正数")

        cfg = load_kline_update_config()
        # worker 是可直接执行的独立入口，且会重新读取配置；必须再次走与监督者
        # 相同的完整校验，防止父进程校验后配置漂移或直接调用 --worker 绕过。
        load_collection_config(cfg)

        def progress_writer(code, processed):
            if not args.progress_json:
                return
            tmp = args.progress_json + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as pf:
                json.dump(
                    {
                        'last_started_code': code,
                        'processed': processed,
                        'updated_at': datetime.now().isoformat(timespec='seconds'),
                    },
                    pf,
                    ensure_ascii=False,
                )
            os.replace(tmp, args.progress_json)

        updater = DailyKlineUpdater(config=cfg)
        chunk_result = updater.collect(
            target_date=args.target_date,
            stocks=stocks,
            pool_size=pool_size,
            chunk_index=int(args.chunk_index or 0),
            progress_writer=progress_writer,
        )

        if args.result_json:
            with open(args.result_json, 'w', encoding='utf-8') as f:
                json.dump(chunk_result.to_dict(), f, ensure_ascii=False, indent=2)

        if chunk_result.stop_reason in ('failure_rate', 'failure_limit'):
            return EXIT_CHUNK_BREAKER
        return EXIT_CHUNK_COMPLETE
    except Exception as e:  # noqa: BLE001
        logger.error(f"工作进程致命错误：{e}", exc_info=True)
        return EXIT_WORKER_FATAL


def _flush_std_streams() -> None:
    """退出前刷新标准流，避免 os._exit 丢失最后一行日志。"""
    import sys
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except Exception:  # noqa: BLE001
            pass


def main() -> None:
    """CLI 入口：--worker 时执行分片，否则执行监督者全量采集。"""
    import argparse
    import json

    parser = argparse.ArgumentParser(description='每日 K 线数据更新脚本（分片监督模式）')
    parser.add_argument(
        '--days', type=int, default=None,
        help='更新最近多少天的数据（默认从 config.yaml global.kline_update.days_to_update 读取）'
    )
    parser.add_argument(
        '--result-json', type=str, default=None,
        help='非空时把 UpdateResult 序列化（JSON）写入该路径，供 scheduler 读取',
    )
    # 工作进程参数（由监督者注入）
    parser.add_argument('--worker', action='store_true', help='以分片工作进程模式运行')
    parser.add_argument('--target-date', type=str, default=None, help='目标交易日 YYYY-MM-DD')
    parser.add_argument('--symbols-file', type=str, default=None, help='分片股票列表 JSON')
    parser.add_argument('--progress-json', type=str, default=None, help='单股进度文件路径')
    parser.add_argument('--pool-size', type=int, default=None, help='股票池总数')
    parser.add_argument('--chunk-index', type=int, default=0, help='分片序号')

    args = parser.parse_args()

    if args.worker:
        exit_code = run_worker(args)
        _flush_std_streams()
        os._exit(exit_code)

    result = run_update(days=args.days)

    default_cfg = load_kline_update_config()
    complete = _is_complete_for(result, default_cfg)

    if args.result_json:
        try:
            with open(args.result_json, 'w', encoding='utf-8') as f:
                json.dump(_result_to_dict(result), f, ensure_ascii=False, indent=2)
            logger.info(f"UpdateResult 已写入 {args.result_json}")
        except Exception as e:  # noqa: BLE001
            logger.error(f"写入 result-json 失败：{e}")

    # CLI 入口保留退出码语义：完整性 PASS 则 exit 0，否则 exit 1
    _flush_std_streams()
    os._exit(0 if complete else 1)


if __name__ == '__main__':
    main()
