"""
Task 9 Step A: 端到端集成测试公共 fixture。

提供 FakeDB（内存模拟 DatabaseManager）和 pipeline fixture，
测试通过 monkeypatch 将 FakeDB 注入生产代码路径，保证被测代码是真实实现。
"""

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest import mock

import pytest

# ---------------------------------------------------------------------------
# 确保仓库根在 sys.path（PEP 420 命名空间包合并需要）
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
DISTILL_ROOT = REPO_ROOT / "distill_changying"
if str(DISTILL_ROOT) not in sys.path:
    sys.path.insert(0, str(DISTILL_ROOT))


# =====================================================================
# FakeDB：内存模拟 DatabaseManager，足够支撑端到端场景测试
# =====================================================================


class FakeCursor:
    """
    FakeDB 配套的 cursor 替身。

    只实现测试用到的少量 SQL 模式：
      - SELECT status, data_version FROM scan_runs WHERE signal_date=%s AND run_kind='rescan'
      - SELECT status, target_date, last_completed_at, details FROM task_status WHERE task_name=%s
      - SELECT last_completed_at FROM task_status WHERE task_name='backfill'
    """

    def __init__(self, db: "FakeDB"):
        self.db = db
        self._fetched_rows: List[tuple] = []
        self._columns: List[str] = []
        self.description = []  # psycopg2 cursor 需要

    def execute(self, query: str, params=None):
        """解析 SQL 模式并返回 FakeDB 中的数据"""
        params = params or ()
        q = query.strip()

        # --- is_rescan_completed：SELECT status, data_version FROM scan_runs WHERE signal_date=%s AND run_kind='rescan' ---
        if "SELECT status, data_version FROM scan_runs" in q or (
            "FROM scan_runs" in q and "run_kind" in q and "rescan" in q
        ):
            target_date = params[0] if params else None
            self._columns = ["status", "data_version"]
            matches = [
                v for (sd, rk), v in self.db.scan_runs.items()
                if sd == target_date and rk == "rescan"
            ]
            matches.sort(key=lambda x: x.get("started_at"), reverse=True)
            if matches:
                m = matches[0]
                self._fetched_rows = [(m.get("status"), m.get("data_version"))]
            else:
                self._fetched_rows = []
            return

        # --- _query_task_status：SELECT status, target_date, last_completed_at, details FROM task_status WHERE task_name=%s ---
        if (
            "FROM task_status" in q
            and "task_name" in q
            and "last_completed_at" in q
        ):
            task_name = params[0] if params else None
            self._columns = ["status", "target_date", "last_completed_at", "details"]
            row = self.db.task_status.get(task_name)
            if row:
                self._fetched_rows = [tuple(row.get(c) for c in self._columns)]
            else:
                self._fetched_rows = []
            return

        # --- _fetch_backfill_data_version：SELECT last_completed_at FROM task_status WHERE task_name='backfill' ---
        if "last_completed_at" in q and "task_name" in q:
            task_name = params[0] if params else None
            self._columns = ["last_completed_at"]
            row = self.db.task_status.get(task_name)
            if row and row.get("last_completed_at"):
                self._fetched_rows = [(row["last_completed_at"],)]
            else:
                self._fetched_rows = []
            return

        # --- mark_stale_scan_runs_failed 里的 UPDATE，FakeDB 已有独立实现 ---
        if "UPDATE scan_runs" in q:
            self._fetched_rows = []
            return

        # --- 未知 SQL 模式：静默返回空（测试应避免走到未覆盖路径） ---
        self._fetched_rows = []

    def fetchone(self):
        if self._fetched_rows:
            return self._fetched_rows.pop(0)
        return None

    def fetchall(self):
        rows = self._fetched_rows[:]
        self._fetched_rows = []
        return rows

    def close(self):
        pass


class FakeDB:
    """
    DatabaseManager 的轻量替身。

    只实现测试涉及的方法，内部用 dict 存储。所有写操作都是内存级，
    不涉及真实 PostgreSQL。
    """

    def __init__(self):
        # scan_runs: {(signal_date, run_kind): dict}
        self.scan_runs: Dict[tuple, Dict[str, Any]] = {}
        # push_history: {(code, push_date, push_type): bool}
        self.push_history: Dict[tuple, bool] = {}
        # task_status: {task_name: dict}
        self.task_status: Dict[str, Dict[str, Any]] = {}
        # klines: {(code, frequency, date_str): dict}
        self.klines: Dict[tuple, Dict[str, Any]] = {}
        # stocks: {code: dict}
        self.stocks: Dict[str, Dict[str, Any]] = {}
        # scan_results: {(scan_date, code): dict}
        self.scan_results: Dict[tuple, Dict[str, Any]] = {}

        # 记录调用日志，便于断言
        self.upsert_calls: List[Dict[str, Any]] = []

        # conn 对象：cursor() 返回 FakeCursor，模拟 psycopg2 协议
        self.conn = mock.MagicMock()
        self.conn.cursor.return_value = FakeCursor(self)
        # FakeCursor 需要 description 元组（模拟 psycopg2 cursor.description）
        # 由 FakeCursor.execute 设置，这里默认给个空列表
        self.conn.cursor.return_value.description = []

    # --- scan_runs 相关 ------------------------------------------------

    def upsert_scan_run(
        self,
        signal_date: str,
        run_kind: str,
        status: str,
        stock_pool_count: int = 0,
        scanned_count: int = 0,
        raw_candidate_count: int = 0,
        selected_count: int = 0,
        filter_reason: Optional[str] = None,
        data_metrics: Optional[Dict] = None,
        data_version=None,
        completed_at=None,
    ) -> None:
        """幂等写入 scan_runs（内存级模拟）"""
        key = (signal_date, run_kind)
        now = datetime.now(timezone.utc)
        entry = {
            "signal_date": signal_date,
            "run_kind": run_kind,
            "status": status,
            "stock_pool_count": stock_pool_count,
            "scanned_count": scanned_count,
            "raw_candidate_count": raw_candidate_count,
            "selected_count": selected_count,
            "filter_reason": filter_reason,
            "data_metrics": data_metrics or {},
            "data_version": data_version,
            "started_at": self.scan_runs.get(key, {}).get("started_at", now),
            "completed_at": completed_at or now,
        }
        self.scan_runs[key] = entry
        self.upsert_calls.append(entry)

    def get_scan_run(
        self, signal_date: str, run_kind: Optional[str] = None
    ) -> Optional[Dict]:
        """查询 scan_runs（内存级模拟）"""
        if run_kind is None:
            # 返回该 signal_date 最近一次
            matches = [
                v for (sd, _), v in self.scan_runs.items() if sd == signal_date
            ]
            if not matches:
                return None
            # 按 started_at 降序取第一个
            matches.sort(key=lambda x: x.get("started_at"), reverse=True)
            return matches[0].copy()
        key = (signal_date, run_kind)
        return self.scan_runs.get(key, {}).copy() if key in self.scan_runs else None

    def get_scan_runs_by_status(
        self, status: str, limit: int = 20
    ) -> List[Dict]:
        """按状态查询 scan_runs"""
        matches = [v for v in self.scan_runs.values() if v.get("status") == status]
        matches.sort(key=lambda x: x.get("started_at"), reverse=True)
        return [m.copy() for m in matches[:limit]]

    def mark_stale_scan_runs_failed(self, stale_after_seconds: int) -> int:
        """将超时 running 记录置为 failed"""
        from datetime import timedelta

        cutoff = datetime.now(timezone.utc) - timedelta(seconds=stale_after_seconds)
        affected = 0
        for key, entry in self.scan_runs.items():
            if entry.get("status") == "running":
                started = entry.get("started_at")
                if started is not None and started < cutoff:
                    entry["status"] = "failed"
                    entry["filter_reason"] = "stale_running_timeout"
                    entry["completed_at"] = datetime.now(timezone.utc)
                    affected += 1
        return affected

    # --- push_history 相关 --------------------------------------------

    def has_pushed(self, code: str, push_date: str, push_type: str = "daily_scan") -> bool:
        """查询推送幂等标记"""
        return bool(self.push_history.get((code, push_date, push_type)))

    def save_push_history(self, code: str, push_date: str, push_type: str = "daily_scan") -> None:
        """记录推送历史"""
        self.push_history[(code, push_date, push_type)] = True

    def has_pushed_today(self, code: str, today: Optional[str] = None) -> bool:
        """旧接口兼容"""
        from datetime import datetime

        today = today or datetime.now().strftime("%Y-%m-%d")
        return any(
            k[0] == code and k[1] == today for k in self.push_history.keys()
        )

    # --- task_status 相关 ----------------------------------------------

    def set_task_status(self, task_name: str, row: Dict[str, Any]) -> None:
        """测试辅助：设置 task_status 表某任务的最新行"""
        self.task_status[task_name] = row

    # --- stocks / klines 相关 -------------------------------------------

    def get_stock_list(self, filters: Optional[Dict] = None):
        """返回 FakeDB 中的股票列表（DataFrame）"""
        import pandas as pd

        if not self.stocks:
            return None
        rows = list(self.stocks.values())
        df = pd.DataFrame(rows)
        return df if not df.empty else None

    def get_kline_history(self, code: str, days: int = 120, frequency: str = "d"):
        """返回某股票的 K 线历史"""
        import pandas as pd

        matched = [
            v
            for (c, freq, _), v in self.klines.items()
            if c == code and freq == frequency
        ]
        matched.sort(key=lambda x: x["date"])
        if not matched:
            return None
        df = pd.DataFrame(matched)
        df["date"] = pd.to_datetime(df["date"])
        df.sort_values("date", inplace=True)
        df.reset_index(drop=True, inplace=True)
        return df

    def get_index_kline(self, index_code: str, days: int = 30):
        """返回指数 K 线（FakeDB 简化实现）"""
        import pandas as pd

        # FakeDB 中指数数据也存 klines，code 就是 index_code
        matched = [
            v for (c, _, _), v in self.klines.items() if c == index_code
        ]
        matched.sort(key=lambda x: x["date"])
        if not matched:
            return None
        df = pd.DataFrame(matched)
        df["date"] = pd.to_datetime(df["date"])
        df.sort_values("date", inplace=True)
        df.reset_index(drop=True, inplace=True)
        return df

    def get_last_signal_date(self, code: str) -> Optional[str]:
        """获取最近信号日期"""
        key_exists = [k for k in self.scan_results.keys() if k[1] == code]
        if not key_exists:
            return None
        dates = [k[0] for k in key_exists]
        return max(dates)

    def get_signal_count_this_year(self, code: str, year: Optional[int] = None) -> int:
        """年度信号计数"""
        from datetime import datetime

        year = year or datetime.now().year
        count = 0
        for (sd, c), _ in self.scan_results.items():
            if c == code and str(sd).startswith(str(year)):
                count += 1
        return count

    # --- 其他 ----------------------------------------------------------

    def close(self) -> None:
        """关闭连接（FakeDB 内存级，实际无操作）"""
        pass


# =====================================================================
# pipeline fixture：组装 FakeDB + monkeypatch
# =====================================================================


class Pipeline:
    """
    集成测试管道。

    提供 4 种端到端场景的 run_fixture / run_rescan 方法，
    内部真实调用生产代码（persist_scan_outcome / record_scan_blocked /
    build_scan_summary_message / is_rescan_completed / push_daily_summary_once）。
    """

    def __init__(self, monkeypatch):
        self.db = FakeDB()
        self.monkeypatch = monkeypatch

        # monkeypatch 全局 DatabaseManager（daily_scan.py / scheduler_obpc.py / feishu_push.py 都用到）
        monkeypatch.setattr("data.database.DatabaseManager", lambda: self.db)
        # scheduler_obpc 用的是懒导入函数 _get_database_manager
        monkeypatch.setattr(
            "scripts.scheduler_obpc._get_database_manager",
            lambda: self.db,
        )

        # 场景辅助：注入 FakeDB 到 daily_scan 的模块级引用（如果有的话）
        try:
            import scripts.daily_scan as daily_scan

            monkeypatch.setattr(daily_scan, "DatabaseManager", lambda: self.db)
        except (ImportError, AttributeError):
            pass

    # --- 场景 1：数据不完整阻断扫描 -----------------------------------

    def run_incomplete_data(self, signal_date: str = "2026-10-16") -> Dict[str, Any]:
        """
        覆盖率不足 → record_scan_blocked("blocked_data", "kline_not_ready")。

        返回：{"scan_status", "scanned_count", "message"}。
        """
        from scripts.scheduler_obpc import record_scan_blocked
        from scripts.feishu_push import build_scan_summary_message

        record_scan_blocked(signal_date, "blocked_data", "kline_not_ready")

        run = self.db.get_scan_run(signal_date)
        message = build_scan_summary_message(signal_date, run)

        return {
            "scan_status": run.get("status") if run else None,
            "scanned_count": run.get("scanned_count", 0) if run else 0,
            "message": message or "",
            "run": run,
        }

    # --- 场景 2：市场风控拦截 -----------------------------------------

    def run_market_block(
        self, signal_date: str = "2026-10-16",
        raw_candidate_count: int = 10, filter_reason: str = "沪深300 DIF <= 0",
    ) -> Dict[str, Any]:
        """
        有原始候选但被市场风控拦截 → persist_scan_outcome blocked_market。

        必须真实驱动 persist_scan_outcome 的 blocked_market 分支。
        """
        from scripts.daily_scan import persist_scan_outcome
        from scripts.feishu_push import build_scan_summary_message

        persist_scan_outcome(
            signal_date=signal_date,
            run_kind="initial",
            raw_candidate_count=raw_candidate_count,
            selected_count=0,
            trend_filter_reason=filter_reason,
            stock_pool_count=2911,
            scanned_count=2911,
            data_version=None,
        )

        run = self.db.get_scan_run(signal_date)
        message = build_scan_summary_message(signal_date, run)

        return {
            "scan_status": run.get("status") if run else None,
            "raw_candidate_count": run.get("raw_candidate_count", 0) if run else 0,
            "selected_count": run.get("selected_count", 0) if run else 0,
            "message": message or "",
            "run": run,
        }

    # --- 场景 3：完整扫描后零形态 -------------------------------------

    def run_completed_zero(self, signal_date: str = "2026-10-17") -> Dict[str, Any]:
        """
        完整扫描但原始候选为零 → persist_scan_outcome completed_zero。
        """
        from scripts.daily_scan import persist_scan_outcome
        from scripts.feishu_push import build_scan_summary_message

        persist_scan_outcome(
            signal_date=signal_date,
            run_kind="initial",
            raw_candidate_count=0,
            selected_count=0,
            trend_filter_reason=None,
            stock_pool_count=2911,
            scanned_count=2911,
            data_version=None,
        )

        run = self.db.get_scan_run(signal_date)
        message = build_scan_summary_message(signal_date, run)

        return {
            "scan_status": run.get("status") if run else None,
            "message": message or "",
            "run": run,
        }

    # --- 场景 4：rescan 幂等 ------------------------------------------

    def run_rescan(
        self, push_date: str, signal_date: str,
        already_completed_status: str = "completed_zero",
        data_version=None,
    ) -> Dict[str, Any]:
        """
        模拟「周六补全后重扫周五」场景。连续调用两次：
          - 第一次：is_rescan_completed 返回 False（首次）→ 执行
          - 第二次：is_rescan_completed 返回 True（已重扫）→ 跳过

        注意：这里用 is_rescan_completed 纯函数 + FakeDB 内存状态模拟，
        不是启动真实 scheduler 主循环。
        """
        from scripts.scheduler_obpc import is_rescan_completed

        if data_version is None:
            data_version = datetime(2026, 10, 17, 7, 30, 0, tzinfo=timezone.utc)

        # 第一次：FakeDB 中无 rescan 记录 → 返回 False（允许执行）
        first_allowed = not is_rescan_completed(self.db, signal_date, data_version)

        # 模拟「执行了 rescan 并写完成状态」
        self.db.upsert_scan_run(
            signal_date=signal_date,
            run_kind="rescan",
            status=already_completed_status,
            raw_candidate_count=0,
            selected_count=0,
            data_version=data_version,
        )

        # 第二次：同 data_version → 返回 True（已完成，禁止再执行）
        second_allowed = not is_rescan_completed(self.db, signal_date, data_version)

        return {
            "first_executed": first_allowed,
            "second_executed": second_allowed,
            "signal_date": signal_date,
        }


@pytest.fixture(scope="function")
def pipeline(monkeypatch):
    """
    端到端测试管道 fixture。

    每次测试新建 FakeDB + 独立 monkeypatch，测试间互不污染。
    """
    return Pipeline(monkeypatch)
