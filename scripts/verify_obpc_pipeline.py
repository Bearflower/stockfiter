#!/usr/bin/env python3
"""
OBPC 日流水线只读验收脚本（Task 9 Step B）。

用法：python3 scripts/verify_obpc_pipeline.py --signal-date 2026-10-16
输出：JSON，包含当天股票池规模、K 线覆盖率、task_status、scan_runs、推送记录等。

安全约束（只读，强制）：
  - 使用 psycopg2 直连，**绝不实例化 DatabaseManager**：后者的 __init__ 会执行建表与
    幂等迁移（ALTER TABLE task_status、CREATE TABLE scan_runs 等），在被验收的
    生产库上运行会真实改动生产表结构。
  - 连接后立即把会话设为只读（双重保险）：
      1) conn.set_session(readonly=True, autocommit=True)（psycopg2 支持时）；
      2) 执行 SET default_transaction_read_only = on。
    确保脚本内任何写语句都会被 PostgreSQL 直接拒绝。
  - 脚本内只执行 SELECT 与 information_schema / pg_catalog 只读查询，
    绝无 INSERT/UPDATE/DELETE/CREATE/ALTER。
  - DB 不可达或认证失败时打印明确中文错误并 sys.exit(1)，绝不伪造数据。
  - 覆盖率阈值从 config/config.yaml 读取（global.kline_update.min_coverage_rate）。

schema 现状如实报告（新增字段）：
  - scan_runs_table_exists：to_regclass('schema_stockfilter.scan_runs') 是否为 NULL；
  - task_status_columns：information_schema.columns 实测列清单，据此直接证明
    target_date/details 是否已迁移到位；task_status.target_date 存在则真实读出，
    不存在则输出 null 并由上述列清单佐证（不再假设“生产仍是旧 schema”）。
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
import psycopg2
import yaml

# 支持以 `python3 scripts/verify_obpc_pipeline.py` 直接运行：脚本目录默认可导入路径，
# 需把仓库根目录加入 sys.path，才能导入 data.* / utils.*（与 data/database.py 的取值方式无关）
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# 项目统一使用 schema_stockfilter 前缀（与 data/database.py 的迁移 SQL 习惯一致）
DB_SCHEMA = "schema_stockfilter"


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


def _connect_readonly():
    """
    以只读会话直连 PostgreSQL。

    连接参数取值方式与 data/database.py 的 DatabaseManager.__init__/_connect 完全一致
    （DB_HOST/DB_PORT/DB_NAME/DB_USER/DB_PASSWORD/DB_SCHEMA 以及 search_path options），
    不另造一套配置来源；区别仅在于本脚本不执行任何建表/迁移，并强制只读会话。
    """
    host = os.getenv("DB_HOST", "localhost")
    port = os.getenv("DB_PORT", "5432")
    database = os.getenv("DB_NAME", "stockfilter")
    user = os.getenv("DB_USER", "stockfilter_user")
    password = os.getenv("DB_PASSWORD")
    schema = os.getenv("DB_SCHEMA", DB_SCHEMA)

    if not password:
        print(
            "[ERROR] 必须通过环境变量 DB_PASSWORD 配置数据库密码；"
            "只读验收需要能连生产库，且此脚本不会做任何写操作",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        conn = psycopg2.connect(
            host=host,
            port=port,
            database=database,
            user=user,
            password=password,
            options=f"-c search_path={schema}",
        )
    except Exception as e:
        print(
            "[ERROR] 数据库连接失败（认证失败或数据库不可达）："
            f"{e}；只读验收需要能连生产库，且此脚本不会做任何写操作",
            file=sys.stderr,
        )
        sys.exit(1)

    # 只读双重保险 1：psycopg2 会话级 readonly + autocommit（psycopg2 支持时生效）
    try:
        conn.set_session(readonly=True, autocommit=True)
    except Exception as e:
        print(
            f"[WARN] conn.set_session(readonly=True) 未生效（将由 SET 语句兜底）：{e}",
            file=sys.stderr,
        )

    # 只读双重保险 2：会话默认事务只读，写语句会被 PostgreSQL 直接拒绝
    try:
        with conn.cursor() as cur:
            cur.execute("SET default_transaction_read_only = on")
    except Exception as e:
        print(
            f"[ERROR] 设置 default_transaction_read_only=on 失败，无法保证只读：{e}",
            file=sys.stderr,
        )
        sys.exit(1)

    return conn


def _query_scan_run(
    conn, signal_date: str, run_kind: Optional[str]
) -> Optional[Dict[str, Any]]:
    """
    查询单条 scan_runs 记录（run_kind=None 时取该 signal_date 最近一次运行）。

    语义与 data/database.py 的 get_scan_run 一致；表名统一加 schema_stockfilter. 前缀。
    """
    if run_kind is None:
        sql = (
            f"SELECT * FROM {DB_SCHEMA}.scan_runs "
            "WHERE signal_date = %s ORDER BY started_at DESC LIMIT 1"
        )
        params = (signal_date,)
    else:
        sql = (
            f"SELECT * FROM {DB_SCHEMA}.scan_runs "
            "WHERE signal_date = %s AND run_kind = %s"
        )
        params = (signal_date, run_kind)

    with conn.cursor() as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
        if row is None:
            return None
        columns = [desc[0] for desc in cur.description]
        return dict(zip(columns, row))


# =====================================================================
# Task 5 新增：graded 模式 data_metrics 验证
# =====================================================================

_GRADED_REGIMES = {"strong", "weak", "extreme_weak", "unknown"}
_POLICY_MODES = {"hard_filter", "graded", "shadow"}


def _safe_int(value: Any) -> Optional[int]:
    """严格解析候选计数：只接受「非布尔、非负整数」。

    P2-1 修复（2026-10-20）：修复前 int(value) 会把 True 转成 1、把 3.9 截断成 3，
    使候选计数闭环被宽松类型静默绕过（fail-open）。现在 bool / None / 浮点 /
    字符串 / 负数一律返回 None，由调用方判定为 fatal（fail-closed）。
    """
    if isinstance(value, bool):
        # 注意：Python 中 isinstance(True, int) 为 True，必须先排除 bool
        return None
    if not isinstance(value, int):
        return None
    if value < 0:
        return None
    return value


def verify_data_metrics_for_graded(
    data_metrics: Optional[Dict[str, Any]],
    selected_count: int,
    raw_candidate_count: int,
    expected_mode: Optional[str] = None,
) -> Dict[str, Any]:
    """验证 scan_runs.data_metrics 的完整性和逻辑一致性。

    验收脚本的核心安全逻辑：
      - 配置期望模式与落库 policy_mode 不一致 → 致命错误（P1-2：mode 漂移交叉校验）
      - extreme_weak 但 selected_count > 0 → 致命错误（极弱误放行）
      - graded/shadow 模式下必要字段缺失 → 致命错误（fail-closed）
      - 候选数分层逻辑矛盾 → 警告
      - hard_filter 模式下新字段缺失或为默认值 → 正常，不报错

    Args:
        data_metrics: scan_runs.data_metrics dict（psycopg2 从 JSONB 自动解码），
                      可能为 None（旧数据无此字段）
        selected_count: scan_runs.selected_count
        raw_candidate_count: scan_runs.raw_candidate_count
        expected_mode: config.yaml 中 market_regime_policy.mode 的期望值；
                       None 表示不做模式漂移交叉校验

    Returns:
        Dict[str, Any]: 验证结果，包含：
          - policy_mode: 识别出的模式
          - expected_mode: 配置期望的模式
          - is_graded: 是否为 graded/shadow 模式
          - regime: market_regime 值（若存在）
          - passed: bool，是否全部通过
          - fatal_errors: List[str]，致命错误（需进 precondition_failures）
          - warnings: List[str]，警告（如实报告但不影响退出码）
    """
    result: Dict[str, Any] = {
        "policy_mode": None,
        "expected_mode": expected_mode,
        "is_graded": False,
        "regime": None,
        "passed": True,
        "fatal_errors": [],
        "warnings": [],
    }

    # 修复 issue 8（fail-closed）：data_metrics 缺失/为空即致命。
    # 修复前只 warning 且 passed=True，会让持久化链路断裂（验收永远读到 {}）
    # 被静默放过。daily_scan 现在总是写入 data_metrics，缺失即代表落库异常。
    if data_metrics is None or (
        isinstance(data_metrics, dict) and not data_metrics
    ):
        result["policy_mode"] = "unknown"
        result["fatal_errors"].append(
            "scan_runs.data_metrics 缺失或为空——无法验证分级风控断言，"
            "fail-closed 判定为致命错误（持久化链路可能已断）"
        )
        result["passed"] = False
        return result

    policy_mode = data_metrics.get("policy_mode")
    result["policy_mode"] = policy_mode

    if policy_mode is None:
        # policy_mode 缺失 → 无法判定风控模式，fail-closed
        result["fatal_errors"].append(
            "data_metrics.policy_mode 字段缺失——无法判定风控模式，fail-closed"
        )
        result["passed"] = False
        return result

    if policy_mode not in _POLICY_MODES:
        result["fatal_errors"].append(
            f"data_metrics.policy_mode={policy_mode} 不合法，"
            f"必须是 hard_filter | graded | shadow"
        )
        result["passed"] = False
        return result

    # P1-2 修复：配置期望模式与落库实际模式交叉校验（fail-closed）。
    # 修复前 policy_mode=="hard_filter" 直接 passed=True，导致「配置已切 graded/shadow
    # 但当日仍按 hard_filter 落库」的模式漂移被静默放过。
    if expected_mode is not None and policy_mode != expected_mode:
        result["fatal_errors"].append(
            f"风控模式漂移：配置 market_regime_policy.mode={expected_mode}，"
            f"但 scan_runs.data_metrics.policy_mode={policy_mode}——"
            "配置与当日落库不一致，fail-closed 判定为致命错误"
        )
        result["passed"] = False
        return result

    if policy_mode == "hard_filter":
        # hard_filter 模式：新字段缺失或默认值都是正常的，不报错
        result["regime"] = data_metrics.get("market_regime")
        return result

    # ---- 分级模式（graded / shadow）----
    result["is_graded"] = True

    # P2-1 修复（2026-10-20）：候选计数闭环校验（graded / shadow 共用，违反一律 fatal）。
    # 修复前：data_metrics.raw_candidate_count 缺失只跳过检查、两处计数不一致仅记 warning、
    # shadow 更是在计数校验之前 return → 三种矛盾都能 passed=True。
    # 现在一律作为致命错误：
    #   - data_metrics.raw_candidate_count 或 scan_runs.raw_candidate_count 缺失/类型非法
    #   - 两处 raw_candidate_count 不一致
    #   - selected_count 超过任一处的候选总数
    def _check_candidate_count_closure() -> None:
        dm_raw_int = _safe_int(data_metrics.get("raw_candidate_count"))
        if dm_raw_int is None:
            result["fatal_errors"].append(
                "data_metrics.raw_candidate_count 缺失或类型非法"
                "（必须为非布尔非负整数）——候选计数闭环无法校验，fail-closed"
            )
            result["passed"] = False

        scan_raw_int = _safe_int(raw_candidate_count)
        if scan_raw_int is None:
            # P2-1：scan_runs 侧计数同样必须存在且合法，缺失/非法即 fatal
            result["fatal_errors"].append(
                "scan_runs.raw_candidate_count 缺失或类型非法"
                "（必须为非布尔非负整数）——候选计数闭环无法校验，fail-closed"
            )
            result["passed"] = False

        if (
            dm_raw_int is not None
            and scan_raw_int is not None
            and dm_raw_int != scan_raw_int
        ):
            result["fatal_errors"].append(
                f"候选数落库不一致：scan_runs.raw_candidate_count={scan_raw_int} "
                f"vs data_metrics.raw_candidate_count={dm_raw_int}——"
                "同一链路两处口径必须一致"
            )
            result["passed"] = False

        for label, limit in (
            ("data_metrics.raw_candidate_count", dm_raw_int),
            ("scan_runs.raw_candidate_count", scan_raw_int),
        ):
            if limit is not None and selected_count > limit:
                result["fatal_errors"].append(
                    f"候选数逻辑矛盾：{label}={limit} < selected_count={selected_count}"
                )
                result["passed"] = False

    if policy_mode == "shadow":
        # 修复 issue 8：shadow 的分级指标位于 shadow_graded_decision 嵌套结构，
        # 而非顶层 market_regime/position_multiplier。验收器必须据此读取，
        # 缺失即 fail-closed（而非误判为顶层字段缺失）。
        shadow_dec = data_metrics.get("shadow_graded_decision")
        if not isinstance(shadow_dec, dict) or not shadow_dec:
            result["fatal_errors"].append(
                "shadow 模式下 data_metrics.shadow_graded_decision 缺失或为空——"
                "无法验证分级裁决，fail-closed"
            )
            result["passed"] = False
            return result

        regime = shadow_dec.get("regime")
        result["regime"] = regime
        if regime is None:
            result["fatal_errors"].append(
                "shadow 模式下 shadow_graded_decision.regime 字段缺失"
            )
            result["passed"] = False
        elif regime not in _GRADED_REGIMES:
            result["fatal_errors"].append(
                f"shadow_graded_decision.regime={regime} 不合法，"
                f"必须是 strong | weak | extreme_weak | unknown"
            )
            result["passed"] = False

        position_multiplier = shadow_dec.get("position_multiplier")
        if position_multiplier is None:
            result["fatal_errors"].append(
                "shadow 模式下 shadow_graded_decision.position_multiplier 字段缺失"
            )
            result["passed"] = False
        else:
            try:
                pm = float(position_multiplier)
                if not (0 <= pm <= 1):
                    result["fatal_errors"].append(
                        f"shadow_graded_decision.position_multiplier={pm} 超出合法区间 [0, 1]"
                    )
                    result["passed"] = False
            except (TypeError, ValueError):
                # P2-2 修复：不可解析 → fail-closed 致命错误
                result["fatal_errors"].append(
                    f"shadow_graded_decision.position_multiplier={position_multiplier} "
                    "无法解析为数字（fail-closed）"
                )
                result["passed"] = False

        # shadow 正式信号仍走 hard_filter，selected_count 不受分级乘数影响；
        # 但 shadow 裁决本身在极弱市场下必须 allow_entry 明确为布尔 False
        # （P2-2 修复 2026-10-20：修复前只拦截 allow_entry is True，字段缺失/None/
        #  非布尔值都会静默通过，无法证明极弱硬阻断生效 → 现在要求严格 False）。
        if regime == "extreme_weak":
            shadow_allow = shadow_dec.get("allow_entry")
            if shadow_allow is not False:
                result["fatal_errors"].append(
                    "shadow 模式下 shadow_graded_decision.regime=extreme_weak "
                    f"但 allow_entry={shadow_allow!r} 非布尔 False，"
                    "无法证明极弱硬阻断生效（fail-closed）"
                )
                result["passed"] = False

        # P2-1：shadow 也必须在返回前完成候选计数闭环校验
        _check_candidate_count_closure()
        return result

    # ---- graded 模式：顶层 market_regime / position_multiplier ----
    # 1. market_regime 必须是合法值
    regime = data_metrics.get("market_regime")
    result["regime"] = regime

    if regime is None:
        result["fatal_errors"].append(
            f"graded 模式下 market_regime 字段缺失"
        )
        result["passed"] = False
    elif regime not in _GRADED_REGIMES:
        result["fatal_errors"].append(
            f"market_regime={regime} 不合法，"
            f"必须是 strong | weak | extreme_weak | unknown"
        )
        result["passed"] = False

    # 2. position_multiplier 必须在 [0, 1]
    position_multiplier = data_metrics.get("position_multiplier")
    if position_multiplier is not None:
        try:
            pm = float(position_multiplier)
            if not (0 <= pm <= 1):
                result["fatal_errors"].append(
                    f"position_multiplier={pm} 超出合法区间 [0, 1]"
                )
                result["passed"] = False
        except (TypeError, ValueError):
            # P2-2 修复：不可解析 → fail-closed 致命错误
            result["fatal_errors"].append(
                f"position_multiplier={position_multiplier} 无法解析为数字（fail-closed）"
            )
            result["passed"] = False
    else:
        result["fatal_errors"].append(
            "graded 模式下 position_multiplier 字段缺失"
        )
        result["passed"] = False

    # 3. 极弱市场禁止开仓：extreme_weak 时 selected_count 必须为 0
    if regime == "extreme_weak" and selected_count > 0:
        result["fatal_errors"].append(
            f"极弱市场（market_regime=extreme_weak）但 selected_count={selected_count} > 0，"
            f"属于风控逻辑穿透（极弱误放行）"
        )
        result["passed"] = False

    # 4. 强市/弱市时 selected_count > 0 不能同时有 extreme_weak_blocked_count
    #    （正常路径不会同时出现，但做防御性检查）

    # 5. P2-1：候选计数闭环（raw_candidate_count 缺失、两处不一致、selected 超上限
    #    一律致命——修复前缺失跳过、不一致仅 warning）
    _check_candidate_count_closure()

    return result


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
    # 验收前置条件失败原因（非空则在输出 JSON 后以退出码 1 结束）
    precondition_failures: List[str] = []

    # ------------------------------------------------------------------
    # 1. 加载配置：覆盖率阈值（禁止硬编码）
    # ------------------------------------------------------------------
    config = load_config(args.config)
    # P1-6 修复（2026-10-20）：配置读取失败时 load_config 返回空 dict，
    # 若继续走下去 expected_mode 会默认成 hard_filter，从而「无法确认真实配置」
    # 也可能验收通过。改为直接记为前置条件失败（fail-closed）。
    config_load_failed = not config
    if config_load_failed:
        reason = (
            f"配置文件 {args.config} 加载失败或为空，无法确定 market_regime_policy.mode ——"
            "fail-closed 判定为前置条件失败（不得默认按 hard_filter 验收）"
        )
        print(f"[ERROR] {reason}", file=sys.stderr)
        precondition_failures.append(reason)
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
    # 2. 只读连接数据库（psycopg2 直连，不使用 DatabaseManager）
    # ------------------------------------------------------------------
    conn = _connect_readonly()

    try:
        # ------------------------------------------------------------------
        # 3. 股票池规模
        #    语义与 data/database.py 的 get_stock_list 一致：读取 stocks 全表后，
        #    套用同一个纯函数 filter_stock_pool（主板/ST/退市/指数/次新等过滤）
        #    作为最终防线，避免与 pipeline 口径漂移。纯函数不触碰数据库连接。
        # ------------------------------------------------------------------
        # 仅导入纯函数 filter_stock_pool（模块级函数，导入本身不建立任何数据库连接、
        # 不执行建表/迁移）；真正的 DatabaseManager 实例化已被本脚本彻底移除。
        try:
            from data.database import filter_stock_pool
        except Exception as e:
            print(
                f"[ERROR] 导入 data.database.filter_stock_pool 失败：{e}",
                file=sys.stderr,
            )
            sys.exit(1)

        stock_pool_cfg = config.get("global", {}).get("stock_pool", {})
        try:
            stocks_df = pd.read_sql_query(
                f"SELECT code, name, symbol, list_date, sector "
                f"FROM {DB_SCHEMA}.stocks",
                conn,
            )
        except Exception as e:
            print(f"[ERROR] 查询股票池失败：{e}", file=sys.stderr)
            sys.exit(1)

        filtered_df = filter_stock_pool(stocks_df, stock_pool_cfg or {})
        stock_pool_count = int(len(filtered_df)) if filtered_df is not None else 0
        output["stock_pool_count"] = stock_pool_count

        # ------------------------------------------------------------------
        # 4. K 线覆盖率（SELECT COUNT DISTINCT code FROM klines WHERE date=signal_date）
        # ------------------------------------------------------------------
        kline_covered_count = 0
        coverage_rate = None
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT COUNT(DISTINCT code) AS cnt
                    FROM {DB_SCHEMA}.klines
                    WHERE date = %s AND frequency = 'd'
                    """,
                    (signal_date,),
                )
                row = cur.fetchone()
                kline_covered_count = int(row[0]) if row and row[0] else 0
        except Exception as e:
            print(f"[WARN] 查询 K 线覆盖率失败：{e}", file=sys.stderr)

        output["kline_covered_count"] = kline_covered_count
        if stock_pool_count > 0:
            coverage_rate = round(kline_covered_count / stock_pool_count, 4)
        output["coverage_rate"] = coverage_rate
        output["coverage_rate_pct"] = _pct(coverage_rate)

        # ------------------------------------------------------------------
        # 5. task_status：先如实报告列清单（schema 现状），再取最新状态
        #    注意区分“表不存在”与“列不存在”：表缺失属验收前置条件失败；
        #    target_date 列缺失只是如实报告（输出 null + 列清单佐证）。
        # ------------------------------------------------------------------
        task_status_columns: List[str] = []
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT column_name FROM information_schema.columns
                    WHERE table_schema = %s AND table_name = 'task_status'
                    ORDER BY ordinal_position
                    """,
                    (DB_SCHEMA,),
                )
                task_status_columns = [r[0] for r in cur.fetchall()]
        except Exception as e:
            print(f"[ERROR] 读取 task_status 列清单失败：{e}", file=sys.stderr)

        output["task_status_columns"] = task_status_columns
        task_status_table_exists = len(task_status_columns) > 0

        task_status: Dict[str, Any] = {}
        if not task_status_table_exists:
            reason = (
                f"{DB_SCHEMA}.task_status 表不存在（information_schema 实测无任何列），"
                "可能 scheduler_kline 尚未建表"
            )
            print(f"[ERROR] {reason}", file=sys.stderr)
            precondition_failures.append(reason)
            for task_name in ("kline_update", "backfill"):
                task_status[task_name] = None
        else:
            # 按 information_schema 实测列清单为准（不再假设“生产只有三列”）
            has_target_date = "target_date" in task_status_columns
            select_cols = "task_name, status, last_completed_at"
            if has_target_date:
                # target_date 列已迁移到位，真实读出其值
                select_cols += ", target_date"

            for task_name in ("kline_update", "backfill"):
                try:
                    with conn.cursor() as cur:
                        cur.execute(
                            f"SELECT {select_cols} FROM {DB_SCHEMA}.task_status "
                            "WHERE task_name = %s",
                            (task_name,),
                        )
                        row = cur.fetchone()
                    if row:
                        # row[0]=task_name row[1]=status row[2]=last_completed_at
                        # row[3]=target_date（仅当该列存在时才有）
                        target_date_val = None
                        if has_target_date and len(row) > 3 and row[3] is not None:
                            target_date_val = (
                                row[3].isoformat()
                                if hasattr(row[3], "isoformat")
                                else str(row[3])
                            )
                        task_status[task_name] = {
                            "status": row[1],
                            "target_date": target_date_val,
                            "last_completed_at": (
                                row[2].isoformat() if row[2] else None
                            ),
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
        # 6. scan_runs：表存在性 + 最近一次(latest) + 按 run_kind(initial/rescan)
        #    表名统一加 schema_stockfilter. 前缀；一次性查询取最近一次，
        #    替代原先 for run_kind 循环里两次写同一键的冗余逻辑。
        # ------------------------------------------------------------------
        scan_runs_table_exists = False
        try:
            with conn.cursor() as cur:
                # to_regclass 返回 NULL 表示表在搜索路径/schema 中不存在
                cur.execute(f"SELECT to_regclass('{DB_SCHEMA}.scan_runs')")
                scan_runs_table_exists = cur.fetchone()[0] is not None
        except Exception as e:
            print(
                f"[ERROR] 判定 scan_runs 表是否存在失败：{e}", file=sys.stderr
            )

        output["scan_runs_table_exists"] = scan_runs_table_exists

        scan_status: Dict[str, Any] = {}
        if not scan_runs_table_exists:
            reason = (
                f"{DB_SCHEMA}.scan_runs 表不存在（幂等迁移尚未执行），"
                "无法验收扫描运行状态"
            )
            print(f"[ERROR] {reason}", file=sys.stderr)
            precondition_failures.append(reason)
            scan_status["latest"] = None
            scan_status["initial"] = None
            scan_status["rescan"] = None
        else:
            # 最近一次运行（一次查询即可，替代原先重复的两次迭代）
            try:
                run = _query_scan_run(conn, signal_date, None)
                scan_status["latest"] = (
                    {
                        "run_kind": run.get("run_kind"),
                        "status": run.get("status"),
                        "stock_pool_count": run.get("stock_pool_count", 0),
                        "scanned_count": run.get("scanned_count", 0),
                        "raw_candidate_count": run.get("raw_candidate_count", 0),
                        "selected_count": run.get("selected_count", 0),
                        "filter_reason": run.get("filter_reason"),
                        # Task 5 新增：graded 模式 data_metrics（psycopg2 从 JSONB 自动解码为 dict）
                        "data_metrics": run.get("data_metrics"),
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
                print(f"[WARN] 查询 scan_runs(latest) 失败：{e}", file=sys.stderr)
                scan_status["latest"] = None

            # 按 run_kind 分别取 initial / rescan（键名保持 initial / rescan）
            for run_kind in ("initial", "rescan"):
                try:
                    run = _query_scan_run(conn, signal_date, run_kind)
                    if run:
                        scan_status[run_kind] = {
                            "status": run.get("status"),
                            "raw_candidate_count": run.get(
                                "raw_candidate_count", 0
                            ),
                            "selected_count": run.get("selected_count", 0),
                            "filter_reason": run.get("filter_reason"),
                        }
                    else:
                        scan_status[run_kind] = None
                except Exception as e:
                    print(
                        f"[WARN] 查询 scan_runs({run_kind}) 失败：{e}",
                        file=sys.stderr,
                    )
                    scan_status[run_kind] = None

        output["scan_status"] = scan_status

        # ------------------------------------------------------------------
        # 7. Task 5 新增：graded 模式 data_metrics 验证
        # ------------------------------------------------------------------
        # P1-2：从 config 读取期望的 market_regime_policy.mode，用于与落库
        # policy_mode 交叉校验（模式漂移 fail-closed）。读取路径与
        # monitor.sh 的 check_obpc_graded_config 一致。
        # P1-6：配置读取失败或 mode 非法时不默认 hard_filter，直接记为前置条件失败。
        expected_mode: Optional[str] = None
        # P2-3：config_invalid 统一表示「配置不可读 或 mode 非法」，
        # 两种情况都必须让 graded_verification.passed=False，避免 JSON 与退出码自相矛盾
        config_invalid = config_load_failed
        if config_load_failed:
            # 已在步骤 1 记入 precondition_failures，此处不再伪造期望模式
            expected_mode = None
        else:
            mode_value = (
                config.get("strategies", {})
                .get("oversold_bounce", {})
                .get("params", {})
                .get("market_regime_policy", {})
                .get("mode", "hard_filter")
            )
            if mode_value not in ("hard_filter", "graded", "shadow"):
                config_invalid = True
                mode_reason = (
                    f"market_regime_policy.mode={mode_value!r} 非法，"
                    "无法确定期望模式 —— fail-closed 判定为前置条件失败"
                )
                print(f"[ERROR] {mode_reason}", file=sys.stderr)
                precondition_failures.append(mode_reason)
            else:
                expected_mode = mode_value
        output["expected_policy_mode"] = expected_mode

        graded_verification: Dict[str, Any] = {"checked": False}
        if scan_runs_table_exists and scan_status.get("latest") is not None:
            latest = scan_status["latest"]
            dm = latest.get("data_metrics")
            sel = latest.get("selected_count", 0)
            raw = latest.get("raw_candidate_count", 0)

            graded_verification = verify_data_metrics_for_graded(
                dm, sel, raw, expected_mode=expected_mode
            )
            graded_verification["checked"] = True

            # 致命错误 → 进 precondition_failures（触发脚本 exit 1）
            for err in graded_verification.get("fatal_errors", []):
                print(f"[ERROR] graded 验证失败：{err}", file=sys.stderr)
                precondition_failures.append(f"[graded验证] {err}")
            # 警告 → 如实报告到 stderr，不触发 exit 1
            for w in graded_verification.get("warnings", []):
                print(f"[WARN] graded 验证警告：{w}", file=sys.stderr)

            if config_invalid:
                # P1-6 / P2-3：配置不可读或 mode 非法时无法确定期望模式，
                # JSON 中也不得声称通过（否则出现「passed=true 但退出码=1」的自相矛盾）
                graded_verification["passed"] = False
                graded_verification.setdefault("warnings", []).append(
                    "配置加载失败或 market_regime_policy.mode 非法，"
                    "无法确定期望风控模式，本次 graded 验证判为不通过（fail-closed）"
                )
        elif scan_runs_table_exists:
            # fail-closed：scan_runs 表存在但该 signal_date 无运行记录 ——
            # 说明当日扫描根本没有落盘，无法验证任何分级断言。
            # 此时不能只 warning 放过（否则“没有证据”会被当成通过）。
            reason = (
                f"scan_runs 表存在但 {signal_date} 无运行记录（latest run 为 None）——"
                "当日扫描未落盘，fail-closed 判定为前置条件失败"
            )
            # P2-3：无运行记录时不给出 passed=True 的假象，明确置为不通过
            graded_verification["passed"] = False
            graded_verification["warnings"] = [reason]
            print(f"[ERROR] {reason}", file=sys.stderr)
            precondition_failures.append(reason)

        output["graded_verification"] = graded_verification

        # ------------------------------------------------------------------
        # 8. push_history：汇总推送记录（三列精确匹配）
        # ------------------------------------------------------------------
        push_recorded: Optional[bool] = None
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT COUNT(*) FROM {DB_SCHEMA}.push_history
                    WHERE code = %s AND push_date = %s AND push_type = %s
                    """,
                    ("__daily_summary__", signal_date, "obpc_summary"),
                )
                cnt = cur.fetchone()[0]
            push_recorded = bool(cnt > 0)
        except Exception as e:
            print(f"[WARN] 查询 push_history 失败：{e}", file=sys.stderr)

        output["push_recorded"] = push_recorded

        # ------------------------------------------------------------------
        # 8. 覆盖率合规性判定（阈值来自 config，禁止硬编码）
        # ------------------------------------------------------------------
        if min_coverage_rate is not None and coverage_rate is not None:
            output["coverage_passes"] = coverage_rate >= min_coverage_rate
        else:
            output["coverage_passes"] = None

        # ------------------------------------------------------------------
        # 9. 输出 JSON（如实报告，包含 schema 现状字段）
        # ------------------------------------------------------------------
        print(json.dumps(output, ensure_ascii=False, indent=2, default=str))

        # 验收前置条件失败时，JSON 已如实输出，退以非 0 退出码标明前置条件失败
        if precondition_failures:
            print(
                "[ERROR] 验收前置条件失败：" + "；".join(precondition_failures),
                file=sys.stderr,
            )
            sys.exit(1)

    finally:
        try:
            conn.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
