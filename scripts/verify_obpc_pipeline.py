#!/usr/bin/env python3
"""
OBPC 日流水线只读验收脚本（Task 9 Step B）。

用法：python3 scripts/verify_obpc_pipeline.py --signal-date 2026-10-16
输出：JSON，包含当天股票池规模、K 线覆盖率、task_status、scan_runs、推送记录等。

安全约束：
  - 只执行 SELECT，绝不 UPDATE/INSERT/DELETE 生产库
  - DB 不可用时显式报错退出（sys.exit(1)），绝不伪造数据
  - 覆盖率阈值从 config/config.yaml 读取（global.kline_update.min_coverage_rate）
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

import yaml


def load_config(config_path: str = "config/config.yaml") -> Dict[str, Any]:
    """加载 config.yaml（安全降级：读不到则返回空 dict，后续判定报错）"""
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception as e:
        print(f"[ERROR] 加载配置文件失败：{e}", file=sys.stderr)
        return {}


def _pct(v):
    """百分比格式化"""
    if v is None:
        return None
    return round(float(v) * 100, 2)


def main() -> None:
    parser = argparse.ArgumentParser(description="OBPC 日流水线只读验收脚本")
    parser.add_argument(
        "--signal-date",
        type=str,
        required=True,
        help="目标交易日（YYYY-MM-DD）",
    )
    parser.add_argument(
        "--config",
        type=str,
        default="config/config.yaml",
        help="配置文件路径（默认 config/config.yaml）",
    )
    args = parser.parse_args()

    signal_date = args.signal_date
    output: Dict[str, Any] = {
        "signal_date": signal_date,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }

    # ------------------------------------------------------------------
    # 1. 加载配置：覆盖率阈值（禁止硬编码）
    # ------------------------------------------------------------------
    config = load_config(args.config)
    min_coverage_rate = (
        config.get("global", {})
        .get("kline_update", {})
        .get("min_coverage_rate", None)
    )
    output["coverage_threshold"] = min_coverage_rate

    if min_coverage_rate is None:
        print(
            "[WARN] 配置中未找到 global.kline_update.min_coverage_rate，跳过覆盖率判定",
            file=sys.stderr,
        )

    # ------------------------------------------------------------------
    # 2. 连接数据库（只读：DatabaseManager 构造时会做幂等迁移，这是契约允许的）
    # ------------------------------------------------------------------
    try:
        from data.database import DatabaseManager
    except Exception as e:
        print(f"[ERROR] 导入 data.database 失败：{e}", file=sys.stderr)
        sys.exit(1)

    try:
        db = DatabaseManager()
    except Exception as e:
        print(f"[ERROR] 数据库连接失败：{e}", file=sys.stderr)
        sys.exit(1)

    try:
        # ------------------------------------------------------------------
        # 3. 股票池规模（主板只含 000/001/002/600/601/603/605 前缀）
        # ------------------------------------------------------------------
        stock_pool_cfg = config.get("global", {}).get("stock_pool", {})
        try:
            stock_list_df = db.get_stock_list(stock_pool_cfg)
        except Exception as e:
            print(f"[ERROR] 查询股票池失败：{e}", file=sys.stderr)
            sys.exit(1)

        stock_pool_count = (
            int(len(stock_list_df)) if stock_list_df is not None else 0
        )
        output["stock_pool_count"] = stock_pool_count

        # ------------------------------------------------------------------
        # 4. K 线覆盖率（SELECT：COUNT DISTINCT code FROM klines WHERE date = signal_date）
        # ------------------------------------------------------------------
        kline_covered_count = 0
        coverage_rate = None
        try:
            cur = db.conn.cursor()
            cur.execute(
                """
                SELECT COUNT(DISTINCT code) AS cnt
                FROM klines
                WHERE date = %s AND frequency = 'd'
                """,
                (signal_date,),
            )
            row = cur.fetchone()
            kline_covered_count = int(row[0]) if row and row[0] else 0
            cur.close()
        except Exception as e:
            print(f"[WARN] 查询 K 线覆盖率失败：{e}", file=sys.stderr)

        output["kline_covered_count"] = kline_covered_count
        if stock_pool_count > 0:
            coverage_rate = round(
                kline_covered_count / stock_pool_count, 4
            )
        output["coverage_rate"] = coverage_rate
        output["coverage_rate_pct"] = _pct(coverage_rate)

        # ------------------------------------------------------------------
        # 5. task_status：kline_update / backfill 的最新状态
        # ------------------------------------------------------------------
        task_status: Dict[str, Any] = {}
        for task_name in ("kline_update", "backfill"):
            try:
                cur = db.conn.cursor()
                # R-1 修复：真实库只有 task_name / status / last_completed_at 三列
                cur.execute(
                    """
                    SELECT task_name, status, last_completed_at
                    FROM task_status
                    WHERE task_name = %s
                    """,
                    (task_name,),
                )
                row = cur.fetchone()
                cur.close()
                if row:
                    # row[0]=task_name, row[1]=status, row[2]=last_completed_at
                    # target_date 列真实库不存在，置 None
                    status_val = row[1]
                    target_date_val = None
                    last_completed = (
                        row[2].isoformat() if row[2] else None
                    )
                    task_status[task_name] = {
                        "status": status_val,
                        "target_date": target_date_val,
                        "last_completed_at": last_completed,
                    }
                else:
                    task_status[task_name] = None
            except Exception as e:
                print(
                    f"[WARN] 查询 task_status({task_name}) 失败：{e}",
                    file=sys.stderr,
                )
                task_status[task_name] = None

        output["task_status"] = task_status

        # ------------------------------------------------------------------
        # 6. scan_runs：initial + rescan 最近一次
        # ------------------------------------------------------------------
        scan_status: Dict[str, Any] = {}
        for run_kind in ("initial", "rescan"):
            try:
                run = db.get_scan_run(signal_date, run_kind=None)  # 取最近一次
                scan_status["latest"] = (
                    {
                        "run_kind": run.get("run_kind"),
                        "status": run.get("status"),
                        "stock_pool_count": run.get("stock_pool_count", 0),
                        "scanned_count": run.get("scanned_count", 0),
                        "raw_candidate_count": run.get("raw_candidate_count", 0),
                        "selected_count": run.get("selected_count", 0),
                        "filter_reason": run.get("filter_reason"),
                        "data_version": (
                            run["data_version"].isoformat()
                            if run.get("data_version")
                            else None
                        ),
                        "started_at": (
                            run["started_at"].isoformat()
                            if run.get("started_at")
                            else None
                        ),
                        "completed_at": (
                            run["completed_at"].isoformat()
                            if run.get("completed_at")
                            else None
                        ),
                    }
                    if run
                    else None
                )
            except Exception as e:
                print(
                    f"[WARN] 查询 scan_runs 失败：{e}", file=sys.stderr
                )
                scan_status["latest"] = None

        # 同时按 run_kind 分别查
        for rk in ("initial", "rescan"):
            try:
                run = db.get_scan_run(signal_date, run_kind=rk)
                if run:
                    scan_status[rk] = {
                        "status": run.get("status"),
                        "raw_candidate_count": run.get("raw_candidate_count", 0),
                        "selected_count": run.get("selected_count", 0),
                        "filter_reason": run.get("filter_reason"),
                    }
            except Exception as e:
                print(
                    f"[WARN] 查询 scan_runs({rk}) 失败：{e}", file=sys.stderr
                )

        output["scan_status"] = scan_status

        # ------------------------------------------------------------------
        # 7. push_history：汇总推送记录
        # ------------------------------------------------------------------
        push_recorded: Optional[bool] = None
        try:
            push_recorded = db.has_pushed(
                code="__daily_summary__",
                push_date=signal_date,
                push_type="obpc_summary",
            )
        except Exception as e:
            print(f"[WARN] 查询 push_history 失败：{e}", file=sys.stderr)

        output["push_recorded"] = push_recorded

        # ------------------------------------------------------------------
        # 8. 覆盖率合规性判定
        # ------------------------------------------------------------------
        if min_coverage_rate is not None and coverage_rate is not None:
            output["coverage_passes"] = coverage_rate >= min_coverage_rate
        else:
            output["coverage_passes"] = None

        # ------------------------------------------------------------------
        # 9. 输出 JSON
        # ------------------------------------------------------------------
        print(json.dumps(output, ensure_ascii=False, indent=2, default=str))

    finally:
        try:
            db.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
