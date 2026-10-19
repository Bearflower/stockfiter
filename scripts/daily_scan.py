#!/usr/bin/env python3
"""
每日形态扫描脚本（T 日收盘后运行）

2026-10-18 改造：
  - 分离原始形态检测与风控裁决：先遍历所有股票统计 raw_candidate_count，
    再由 apply_all_controls 裁决，避免大盘弱时只扫 1 只就提前返回
  - 新增 --run-kind {initial,rescan}，scan_runs 用 (signal_date, run_kind) 唯一
  - try/except/finally 保证每次运行最终落盘为六态之一
  - 零信号也写 signals_<date>.json（内容 []），文件只承载信号，真相以 scan_runs 为准
  - 固定 CSV 路径也经 data.database.filter_stock_pool 过滤，与数据库回退路径一致
  - raw_candidate_count = 风控裁决前的形态候选数（用于 blocked_market 通知展示）
  - 形态候选：优先用 strategy.analyze_candidates，不存在则回退 analyze
  - data_version 取 backfill task_status 的 last_completed_at（读不到则 None）
"""

import argparse
import json
import os
import sys
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd
import yaml

from utils.logger import get_logger
from data.database import DatabaseManager, filter_stock_pool
from strategy.oversold_bounce.strategy import OversoldBounceStrategy
from strategy.oversold_bounce.risk_control import (
    RiskControlConfig,
    RiskController,
    MarketContext,
    RealtimeMarketContextProvider,
    DatabaseMonthlyCounter,
    cap_by_monthly_quota,
)

logger = get_logger()

# =====================================================================
# 模块级常量：filter_reason 语义标识
# =====================================================================
# 当 scan_daily_signals 返回 raw_candidate_count > 0 且 selected_count == 0
# 且 blocked_market=False 时，说明所有候选被评分/冷却/年度额度/截断等
# 非市场风控拦截。这种情况必须判为 blocked_market 状态（不是 completed_zero），
# 否则会发送误导性的"完整扫描后未发现形态"通知（修复 B-3 状态机闭环）
RISK_CONTROLS_FILTERED_ALL = "risk_controls_filtered_all_candidates"


def load_config(config_file: str = "config/config.yaml") -> Dict:
    """加载配置文件"""
    with open(config_file, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def get_latest_trading_date() -> str:
    """获取最近一个交易日（无 override 时用，保持旧行为兼容）"""
    today = datetime.now()
    if today.weekday() >= 5:
        today = today - timedelta(days=today.weekday() - 4)
    return today.strftime("%Y-%m-%d")


# =====================================================================
# 辅助：取 backfill 的 last_completed_at 作为 data_version
# =====================================================================


def _fetch_backfill_data_version(db) -> Optional[datetime]:
    """
    取 backfill 任务的 last_completed_at（作为 data_version 写入 scan_runs）。
    读不到时返回 None，不向上抛异常。
    """
    cur = db.conn.cursor()
    try:
        cur.execute(
            """
            SELECT last_completed_at FROM schema_stockfilter.task_status
            WHERE task_name = 'backfill'
            """
        )
        row = cur.fetchone()
        if row is None or row[0] is None:
            return None
        ts = row[0]
        # last_completed_at 无时区列、DB 会话为 UTC，按 UTC 解析（N-1 约定）
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return ts
    except Exception as e:
        logger.warning("取 backfill data_version 失败：%s", e)
        return None
    finally:
        cur.close()


# =====================================================================
# 形态检测：优先 analyze_candidates，不存在则回退 analyze
# =====================================================================


def detect_pattern_candidate(strategy, code: str, kline_df) -> Tuple[Optional[object], List[object]]:
    """
    对单只股票检测形态候选。

    返回 (signal, all_candidates)：
      - analyze 路径：signal 为 None 或单个 Signal，all_candidates 含它
      - analyze_candidates 路径：signal 为 None（由扫描器选第一个窗口内候选），
        all_candidates 为从新到旧排序的候选列表
    """
    analyze_candidates = getattr(strategy, "analyze_candidates", None)
    if callable(analyze_candidates):
        try:
            candidates = analyze_candidates(code, {"d": kline_df})
            if candidates is not None:
                return None, list(candidates)
        except Exception as e:
            logger.debug("%s analyze_candidates 调用失败，回退 analyze：%s", code, e)

    signal = strategy.analyze(code, {"d": kline_df})
    return signal, [signal] if signal else []


# =====================================================================
# 扫描主流程
# =====================================================================


def scan_daily_signals(
    config: Dict,
    output_dir: str = "signals",
    signal_date_override: Optional[str] = None,
    run_kind: str = "initial",
) -> Dict:
    """
    扫描当日信号（全流程，已结构化改造）。

    始终返回包含 scan_runs 字段的 Dict；函数自身不写 scan_runs（由调用方 main 的
    try/except/finally 统一落盘），以便测试中可以截断。

    Returns:
        dict: {
            "signals": list（最终入选信号）,
            "stock_pool_count": int,
            "scanned_count": int,
            "raw_candidate_count": int,
            "selected_count": int,
            "blocked_market": bool,      # 是否被市场风控拦截（非股票池为空那种零）
            "trend_filter_reason": Optional[str],
            "risk_context": Optional[MarketContext],
            "data_version": Optional[datetime],
        }
    """
    # R03：优先使用调用方显式指定的信号日期（早晨重扫场景）
    # 否则才按 get_latest_trading_date() 推断最近交易日
    if signal_date_override:
        signal_date = signal_date_override
    else:
        signal_date = get_latest_trading_date()
    print(f"扫描日期：{signal_date}  run_kind={run_kind}")
    print()

    # ---- 股票池（CSV 路径也要经 filter_stock_pool 过滤） ----
    db = DatabaseManager()

    # data_version：本次扫描依据的数据版本（backfill 完成时间）
    data_version = _fetch_backfill_data_version(db)

    stock_pool_count = 0
    stock_list_df = None
    try:
        stocks_file = config.get("global", {}).get("stock_list_file", "")
        if stocks_file and os.path.exists(stocks_file):
            raw_df = pd.read_csv(stocks_file, dtype={"code": str})
            stock_pool_config = config.get("global", {}).get("stock_pool", {})
            # 必修：CSV 路径也经 filter_stock_pool 过滤
            stock_list_df = filter_stock_pool(raw_df, stock_pool_config)
            print(f"从固定列表获取到 {len(stock_list_df)} 只股票（已过滤）")
        else:
            stock_pool_config = config.get("global", {}).get("stock_pool", {})
            stock_list_df = db.get_stock_list(stock_pool_config)
            print(
                f"从数据库获取到 {len(stock_list_df)} 只股票"
                if stock_list_df is not None
                else "数据库获取股票列表失败"
            )
    except Exception as e:
        logger.error("获取股票列表失败：%s", e)
        # 不在此处 return []，让调用方 main 的 try/except 统一写 failed 状态
        db.close()
        raise

    if stock_list_df is None or stock_list_df.empty:
        print("股票列表为空")
        # 股票池为空：视为 blocked_data
        db.close()
        return {
            "signals": [],
            "stock_pool_count": 0,
            "scanned_count": 0,
            "raw_candidate_count": 0,
            "selected_count": 0,
            "blocked_market": False,
            "trend_filter_reason": "stock_pool_empty",
            "risk_context": None,
            "data_version": data_version,
        }

    stock_pool_count = len(stock_list_df)

    # ---- 初始化策略与风控 ----
    strategies_config = config.get("strategies", {})
    ob_config = strategies_config.get("oversold_bounce", {})
    params = ob_config.get("params", {})
    strategy = OversoldBounceStrategy(params=params)

    risk_config = RiskControlConfig.from_params(params)
    risk_controller = RiskController(risk_config)
    market_context_provider = RealtimeMarketContextProvider(db, risk_config)
    monthly_counter = DatabaseMonthlyCounter(db)

    # ---- 大盘上下文（仅用于风控裁决，不得提前 return） ----
    market_context: Optional[MarketContext] = None
    trend_filter_reason: Optional[str] = None
    if risk_config.index_filter_enabled:
        try:
            market_context = market_context_provider.get_context(signal_date)
        except Exception as e:
            print(f"警告：大盘环境检查失败（{e}），继续扫描")
            market_context = None

        if market_context is not None:
            trend_result = risk_controller.check_market_trend(market_context)
            if not trend_result.passed:
                trend_filter_reason = trend_result.filter_reason
                print(f"大盘环境不佳：{trend_filter_reason}")
                print("继续扫描以统计原始候选数（不提前返回）")
            else:
                print(f"大盘环境正常（沪深300 {market_context.index_close:.2f}）")
                if market_context.is_weak_market:
                    print(
                        f"  注意：当前为弱势市场，评分阈值将上调至 {risk_config.score_threshold_weak}"
                    )
        else:
            if risk_config.freshness_strategy == "skip":
                print("警告：freshness_strategy=skip 且指数数据不可用")
                trend_filter_reason = "market_context_unavailable"
                print("继续扫描以统计原始候选数（不提前返回）")
            else:
                print("警告：获取大盘上下文失败，跳过大盘过滤")
    else:
        market_context = None

    # ---- 扫描参数 ----
    signal_cooldown_days = params.get("signal_cooldown_days", 60)
    max_signals_per_year = params.get("max_signals_per_year", 2)
    kline_history_days = params.get("kline_history_days", 120)
    min_kline_length = params.get("min_kline_length", 60)
    # 窗口回退天数（N-2）：retrace_date 距 signal_date 的最大允许偏移日数
    # 回退 1 天 = 覆盖信号日及前一日两个交易日（周末/节假日自然跳过）
    signal_window_fallback_days = params.get("signal_window_fallback_days", 1)

    print(f"开始扫描 {len(stock_list_df)} 只股票...")
    print()

    scanned_count = 0
    raw_candidate_count = 0  # 风控裁决前的形态候选数
    signals: List[Dict] = []

    for idx, row in stock_list_df.iterrows():
        code = row["code"]
        name = row["name"]

        try:
            kline_df = db.get_kline_history(code, days=kline_history_days)
            if kline_df is None or len(kline_df) < min_kline_length:
                continue
        except Exception as e:
            logger.debug(f"{code} 获取 K 线失败：{e}")
            continue

        scanned_count += 1

        # ---- 形态检测 ----
        try:
            # analyze_candidates 路径（从新到旧候选）或 analyze 路径（单候选）
            candidates = []
            try:
                candidates = list(strategy.analyze_candidates(code, {"d": kline_df}) or [])
            except AttributeError:
                signal = strategy.analyze(code, {"d": kline_df})
                if signal:
                    candidates = [signal]
            except Exception as e:
                logger.debug("%s analyze_candidates 调用异常，回退 analyze：%s", code, e)
                signal = strategy.analyze(code, {"d": kline_df})
                if signal:
                    candidates = [signal]

            if not candidates:
                continue

            # ---- 窗口回退（第 9.4 N-2）：从新到旧取第一个 retrace_date 落在信号窗口内的候选 ----
            # analyze_candidates 从新到旧排好序；analyze 只有一个候选直接用
            chosen = None
            for cand in candidates:
                retrace_date = cand.detail.get("retrace_date", "")
                if retrace_date:
                    retrace_date_str = str(retrace_date)[:10]
                    days_offset = (
                        datetime.strptime(signal_date, "%Y-%m-%d")
                        - datetime.strptime(retrace_date_str[:10], "%Y-%m-%d")
                    ).days
                    if retrace_date_str >= signal_date or days_offset <= signal_window_fallback_days:
                        chosen = cand
                        break

            if chosen is None:
                continue

            raw_candidate_count += 1

            # ---- 冷却期 + 年度额度 ----
            retrace_date = chosen.detail.get("retrace_date", "")
            retrace_date_str = str(retrace_date)[:10]

            last_signal_date = db.get_last_signal_date(code)
            if last_signal_date:
                days_since_last = (
                    datetime.strptime(signal_date, "%Y-%m-%d")
                    - datetime.strptime(str(last_signal_date)[:10], "%Y-%m-%d")
                ).days
                if days_since_last < signal_cooldown_days:
                    print(
                        f"{code} {name}: 跳过 - 距上次信号仅 {days_since_last} 天"
                        f"（冷却期 {signal_cooldown_days} 天）"
                    )
                    continue

            signal_count = db.get_signal_count_this_year(code)
            if signal_count >= max_signals_per_year:
                print(
                    f"{code} {name}: 跳过 - 今年已触发 {signal_count} 次信号"
                    f"（上限 {max_signals_per_year} 次）"
                )
                continue

            # ---- 三层风控裁决 ----
            if market_context is None:
                effective_context = MarketContext(
                    current_date=signal_date,
                    index_close=0.0,
                    index_ma20=None,
                    index_ma60=None,
                    is_weak_market=False,
                    data_sufficient=False,
                )
            else:
                effective_context = market_context

            risk_result = risk_controller.apply_all_controls(chosen, effective_context)

            if not risk_result.passed:
                logger.info(
                    "[风控过滤] code=%s name=%s rule=%s reason=%s score=%.2f signal_date=%s",
                    code, name, risk_result.filter_rule, risk_result.filter_reason,
                    chosen.score, signal_date,
                )
                continue

            # ---- 通过风控：组装 entry ----
            support_level = chosen.detail.get("support_level", 0)
            stop_loss_ratio = params.get("stop_loss_ratio", 0.97)
            stop_loss_price = support_level * stop_loss_ratio

            entry = {
                "code": code,
                "name": name,
                "support_level": round(support_level, 2),
                "stop_loss_price": round(stop_loss_price, 2),
                "retrace_date": retrace_date_str,
                "surge_date": str(chosen.detail.get("surge_date", ""))[:10],
                "signal_date": signal_date,
                "score": chosen.score,
                "surge_pct": chosen.detail.get("surge_pct", 0),
                "surge_volume_ratio": chosen.detail.get("surge_volume_ratio", 0),
                "drop_rate": chosen.detail.get("drop_rate", 0),
                "retrace_low": chosen.detail.get("retrace_low", 0),
                "retrace_close": chosen.detail.get("retrace_close", 0),
                "shrink_ratio": chosen.detail.get("shrink_ratio", 0),
                "drop_start_date": str(chosen.detail.get("drop_start_date", ""))[:10],
                "drop_end_date": str(chosen.detail.get("drop_end_date", ""))[:10],
                "trailing_stop_ratio": params.get("trailing_stop_ratio", 0.08),
                "hard_stop_loss": params.get("hard_stop_loss", 0.10),
                "stop_loss_ratio": params.get("stop_loss_ratio", 0.97),
            }

            signals.append(entry)
            print(
                f"{code}: 支撑位 {support_level:.2f}, 止损 {stop_loss_price:.2f}, 评分 {chosen.score:.2f}"
            )
        except Exception as e:
            logger.exception(f"{code} {name} 处理异常：{e}")
            continue

    print()
    print("=" * 80)
    print(f"扫描完成：遍历 {scanned_count} / {stock_pool_count} 只股票")
    print(f"原始形态候选数：{raw_candidate_count}")
    print("=" * 80)

    # ---- Top N + 月度额度截断 ----
    if len(signals) > 1:
        signals.sort(key=lambda x: x.get("score", 0), reverse=True)

    year_month = signal_date[:7]
    monthly_count = monthly_counter.get_count(year_month)
    max_daily_signals = params.get("max_daily_signals", 0)
    selected = cap_by_monthly_quota(
        signals,
        used_count=monthly_count,
        max_per_month=risk_config.max_signals_per_month,
        max_daily=max_daily_signals,
    )

    dropped_count = len(signals) - len(selected)
    if dropped_count > 0:
        print(f"额度截断：入选 {len(selected)} 只，丢弃 {dropped_count} 只")
    signals = selected

    # ---- 保存扫描结果到数据库（用于冷却期等检查） ----
    if signals:
        scan_results_data = [
            {
                "code": str(sig["code"]),
                "name": str(sig["name"]),
                "score": float(sig.get("score", 0)),
                "surge_date": str(sig.get("surge_date", "")) if sig.get("surge_date") else None,
                "support_level": float(sig.get("support_level", 0)),
                "current_close": float(sig.get("retrace_close", 0)),
                "drop_rate": float(sig.get("drop_rate", 0)),
                "min_vol_ratio": float(sig.get("surge_volume_ratio", 0)),
                "surge_price": 0.0,
                "surge_volume_ratio": float(sig.get("surge_volume_ratio", 0)),
                "surge_pct": float(sig.get("surge_pct", 0)),
                "low_after_surge": float(sig.get("retrace_low", 0)),
            }
            for sig in signals
        ]
        try:
            db.save_scan_result(signal_date, scan_results_data)
        except Exception as e:
            logger.error(f"保存扫描结果到数据库失败：{e}")

    db.close()

    return {
        "signals": signals,
        "stock_pool_count": stock_pool_count,
        "scanned_count": scanned_count,
        "raw_candidate_count": raw_candidate_count,
        "selected_count": len(signals),
        "blocked_market": trend_filter_reason is not None,
        "trend_filter_reason": trend_filter_reason,
        "risk_context": market_context,
        "data_version": data_version,
    }


# =====================================================================
# 信号文件写入（零信号也写 []）
# =====================================================================


def _write_signals_file(output_dir: str, signal_date: str, signals: List) -> None:
    """无论 signals 是否为空都写出 JSON 文件（空列表也写，避免推送端误判）"""
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    output_file = Path(output_dir) / f"signals_{signal_date}.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(signals, f, ensure_ascii=False, indent=2)
    print(f"\n信号已保存：{output_file}（{len(signals)} 个）")


# =====================================================================
# persist_scan_outcome：将扫描结果映射为 scan_runs 六态之一并落盘
# =====================================================================


def persist_scan_outcome(
    signal_date: str,
    run_kind: str,
    raw_candidate_count: int,
    selected_count: int,
    blocked_market: bool,
    trend_filter_reason: Optional[str],
    stock_pool_count: int,
    scanned_count: int,
    data_version: Optional[datetime],
) -> str:
    """
    将本次扫描的结果映射为 scan_runs 六态之一，并调用 db.upsert_scan_run 落盘。

    状态判定（2026-10-18 B-3 修复：严格对齐第 4 节六态语义表）：
      - selected_count > 0 → completed_with_signals（带最终信号）
      - raw_candidate_count > 0 → blocked_market（存在原始候选但全被拦截）
          * 优先用 trend_filter_reason（市场硬过滤原因）
          * 否则用 RISK_CONTROLS_FILTERED_ALL（非市场风控：评分/冷却/额度/截断）
      - raw_candidate_count == 0 → completed_zero（完整扫描，真·零候选）

    旧实现错误：最后一个 else 分支（raw>0 但非市场风控拦）也判为 completed_zero，
    会发送误导性通知"完整扫描后未发现形态"——而实际有原始候选存在。

    Returns:
        str: 实际写入的 status
    """
    if selected_count > 0:
        status = "completed_with_signals"
        filter_reason = None
    elif raw_candidate_count > 0:
        # 有原始候选但 selected_count=0：一律判 blocked_market
        # 优先用 trend_filter_reason（市场硬过滤），否则用常量标识非市场风控
        status = "blocked_market"
        if trend_filter_reason:
            filter_reason = trend_filter_reason
        else:
            filter_reason = RISK_CONTROLS_FILTERED_ALL
    else:
        # raw_candidate_count == 0：真·零候选，completed_zero
        status = "completed_zero"
        filter_reason = None

    try:
        db = DatabaseManager()
        try:
            db.upsert_scan_run(
                signal_date=signal_date,
                run_kind=run_kind,
                status=status,
                stock_pool_count=stock_pool_count,
                scanned_count=scanned_count,
                raw_candidate_count=raw_candidate_count,
                selected_count=selected_count,
                filter_reason=filter_reason,
                data_version=data_version,
            )
        finally:
            db.close()
        logger.info(
            "scan_runs 落盘：signal_date=%s run_kind=%s status=%s raw=%d selected=%d",
            signal_date, run_kind, status, raw_candidate_count, selected_count,
        )
    except Exception as e:
        logger.error("scan_runs 落盘失败：%s（标记 failed 重试）", e)
        # 落盘失败时再试一次写 failed
        try:
            db2 = DatabaseManager()
            try:
                db2.upsert_scan_run(
                    signal_date=signal_date,
                    run_kind=run_kind,
                    status="failed",
                    raw_candidate_count=raw_candidate_count,
                    filter_reason=f"scan_runs_persist_failed: {e}",
                )
            finally:
                db2.close()
        except Exception as e2:
            logger.error("scan_runs 双重失败：%s", e2)

    return status


# =====================================================================
# main：try/except/finally 统一保证 scan_runs 落盘为六态
# =====================================================================


def main() -> None:
    """每日形态扫描主入口（带状态收敛）"""
    print("=" * 80)
    print("股票形态每日扫描系统 V4")
    print("=" * 80)

    parser = argparse.ArgumentParser(description="OBPC 每日形态扫描")
    parser.add_argument(
        "--signal-date",
        type=str,
        default=None,
        help="强制指定信号日期（格式 YYYY-MM-DD）",
    )
    parser.add_argument(
        "--run-kind",
        type=str,
        default="initial",
        choices=["initial", "rescan"],
        help="运行类型：initial=初扫, rescan=重扫",
    )
    args = parser.parse_args()

    # 确定 scan_runs 写入所需的 signal_date（finally 里也用）
    final_signal_date = args.signal_date or get_latest_trading_date()
    final_run_kind = args.run_kind

    try:
        try:
            config = load_config()
        except Exception as e:
            print(f"\n配置加载失败：{e}")
            persist_scan_outcome(
                signal_date=final_signal_date, run_kind=final_run_kind,
                raw_candidate_count=0, selected_count=0,
                blocked_market=False, trend_filter_reason=f"config_load_failed: {e}",
                stock_pool_count=0, scanned_count=0, data_version=None,
            )
            sys.exit(1)

        # 先写 running，表明进程已启动（会被 mark_stale_scan_runs_failed 收敛）
        try:
            db_running = DatabaseManager()
            try:
                data_version_init = _fetch_backfill_data_version(db_running)
                db_running.upsert_scan_run(
                    signal_date=final_signal_date,
                    run_kind=final_run_kind,
                    status="running",
                    data_version=data_version_init,
                )
            finally:
                db_running.close()
        except Exception as e:
            logger.warning("写 running 状态失败：%s（不阻断扫描）", e)

        result = scan_daily_signals(
            config=config,
            signal_date_override=final_signal_date,
            run_kind=final_run_kind,
        )

        # 零信号也写出 [] 文件
        _write_signals_file("signals", final_signal_date, result["signals"])

        # 落盘 scan_runs（六态之一）
        persist_scan_outcome(
            signal_date=final_signal_date,
            run_kind=final_run_kind,
            raw_candidate_count=result["raw_candidate_count"],
            selected_count=result["selected_count"],
            blocked_market=result["blocked_market"],
            trend_filter_reason=result["trend_filter_reason"],
            stock_pool_count=result["stock_pool_count"],
            scanned_count=result["scanned_count"],
            data_version=result["data_version"],
        )

        # 控制台输出
        print()
        if result["selected_count"] == 0:
            if result["blocked_market"] and result["raw_candidate_count"] > 0:
                print(
                    f"有 {result['raw_candidate_count']} 个原始形态候选，"
                    f"但被市场风控拦截：{result['trend_filter_reason']}"
                )
            elif result["raw_candidate_count"] == 0:
                print("完整扫描后未发现形态")
            else:
                print(
                    f"有 {result['raw_candidate_count']} 个原始形态候选，"
                    f"但全部被冷却期/额度/评分阈值拦截"
                )
        else:
            print(f"\n共发现 {result['selected_count']} 个买入信号")
            for sig in result["signals"]:
                print(
                    f"  {sig['code']} - {sig['name']}: 支撑 {sig['support_level']}"
                    f", 止损 {sig['stop_loss_price']}"
                )

    except KeyboardInterrupt:
        print("\n\n用户中断扫描")
        # 写 failed 状态
        try:
            persist_scan_outcome(
                signal_date=final_signal_date, run_kind=final_run_kind,
                raw_candidate_count=0, selected_count=0,
                blocked_market=False, trend_filter_reason="user_interrupt",
                stock_pool_count=0, scanned_count=0, data_version=None,
            )
        except Exception:
            pass
        sys.exit(1)

    except Exception as e:
        print(f"\n扫描异常：{e}")
        traceback.print_exc()
        # 异常收敛为 failed
        try:
            persist_scan_outcome(
                signal_date=final_signal_date, run_kind=final_run_kind,
                raw_candidate_count=0, selected_count=0,
                blocked_market=False,
                trend_filter_reason=f"exception: {e}",
                stock_pool_count=0, scanned_count=0, data_version=None,
            )
        except Exception as e2:
            print(f"scan_runs failed 状态也写失败：{e2}")
        sys.exit(1)


if __name__ == "__main__":
    main()
