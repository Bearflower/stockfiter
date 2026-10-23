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
    MarketRegimeDecision,
    RealtimeMarketContextProvider,
    DatabaseMonthlyCounter,
    cap_by_monthly_quota,
    apply_candidate_quota_chain,
    calculate_relative_strength,
)

logger = get_logger()

# =====================================================================
# 模块级常量：filter_reason 语义标识
# =====================================================================
# 裁决约定（2026-10-18 B-3 状态机闭环）：
#   - selected_count > 0 → completed_with_signals
#   - raw_candidate_count > 0 且 selected_count == 0 → 一律判 blocked_market
#       * 市场层面拦截：filter_reason = trend_filter_reason
#         （来自 risk_controller.check_market_trend / index_filter /
#          market_context_unavailable）
#       * 个股层面拦截：filter_reason = RISK_CONTROLS_FILTERED_ALL
#         （候选被评分/冷却期/年度额度/每日 TopN 截断全部筛掉，非市场风控）
#   - raw_candidate_count == 0 → completed_zero
#
# 个股层面为何仍用 blocked_market 状态：本次确实完成了原始扫描且有原始候选，
# 若改判 completed_zero 会退回误导性的"完整扫描后未发现形态"（修复 B-3 的旧 bug）。
# 因此通知端（scripts/feishu_push.py 的 build_scan_summary_message）必须按
# filter_reason 区分市场层面与个股层面文案，并以 RISK_CONTROLS_FILTERED_ALL
# 作为个股层面的判别标识，且不得把该内部常量字符串原样展示给用户。
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
# shadow 模式：独立模拟 graded 裁决的辅助函数
# =====================================================================


def _build_stock_close_series(kline_df, signal_date: str) -> Optional["pd.Series"]:
    """按 signal_date 截断个股 K 线，返回以交易日为索引的收盘价 Series。

    前视偏差修复：db.get_kline_history 返回数据库里最新的 N 个交易日，
    当 --signal-date 指定历史日期时，kline_df 末尾会包含 signal_date 之后
    的收盘价 → 必须只保留 date <= signal_date 的记录。

    末日期对齐校验：截断后最后一个日期必须等于 signal_date，否则收盘价窗口
    错位（signal_date 当天数据缺失），继续计算会得到错误结果 → 返回 None
    （fail-closed，由调用方按数据不可用处理）。

    Series 索引为 "YYYY-MM-DD" 交易日字符串，供 calculate_relative_strength
    与指数序列按交易日期 inner join 对齐（而非按位置截取）。
    """
    try:
        kline_cut = kline_df[kline_df["date"].dt.strftime("%Y-%m-%d") <= signal_date]
    except Exception:
        return None
    if kline_cut is None or kline_cut.empty:
        return None
    last_date_str = kline_cut["date"].iloc[-1].strftime("%Y-%m-%d")
    if last_date_str != signal_date:
        return None
    try:
        return pd.Series(
            kline_cut["close"].astype(float).values,
            index=pd.to_datetime(kline_cut["date"]).dt.strftime("%Y-%m-%d").values,
            name="stock_close",
        )
    except Exception:
        return None


def _run_shadow_graded_simulation(
    risk_controller,
    market_context,
    graded_policy,
    candidate_pool: List[Dict],
    official_selected_count: int,
    trend_filter_reason,
    signal_date,
    used_monthly_count: int = 0,
    max_per_month: int = 0,
    max_daily_signals: int = 0,
) -> Dict:
    """shadow 模式下独立模拟一遍 graded 裁决，采集与 hard_filter 的差异。

    设计原则（2026-10-20 修复 issue 4）：
        - 输入是「原始形态候选池」而非 hard_filter 最终放行的 signals。
          修复前只对 hard_filter 放行的 signals 二次筛选，弱市时 hard_filter
          输出为空 → 无法回答"旧策略挡掉的候选，新策略能否安全放行"。
        - 两条裁决链在同一候选池上独立运行：
            * hard_filter 链：沿用主循环已算出的 risk_result.passed（不做二次运行，
              避免 score_adjustment 被重复施加）
            * graded 链：decide_market_regime + 评分阈值 + 相对强度 + 日限额
        - 纯数据采集，不修改正式 signals 列表（正式信号仍走 hard_filter 路径）

    Args:
        risk_controller: RiskController 实例
        market_context: 当前交易日大盘上下文（可能为 None）
        graded_policy: MarketRegimePolicyConfig（graded 模式配置参数）
        candidate_pool: 原始形态候选池，元素为
            {code, name, score, hard_filter_passed, stock_closes}
        official_selected_count: hard_filter 正式 signals 数量（审计参考）
        trend_filter_reason: hard_filter 的大盘过滤原因（None 表示未被阻断）
        signal_date: 信号日期（用于扩散字段/审计）

    Returns:
        dict: {
            "hard_filter_status": str,        # hard_filter 结论标识
            "hard_filter_passed_count": int,  # 同一候选池上 hard_filter 放行数
            "graded_decision": dict,          # graded 的完整裁决（可机读）
            "graded_signals": list,           # graded 下通过的候选（精简版）
            "graded_rs_filtered_count": int,  # graded 链被 RS 拦掉的候选数
            "diff_summary": str,              # 差异摘要（人读）
        }
    """
    graded_decision_dict = {
        "regime": "unknown",
        "allow_entry": False,
        "reason_code": "market_context_unavailable",
        "reason": "指数数据不可用，无法模拟 graded 裁决",
        "position_multiplier": 0.0,
        "score_threshold": 0.0,
        "daily_signal_limit": 0,
        "require_relative_strength": False,
    }
    graded_signals_out = []
    hard_filter_status = trend_filter_reason or "completed"

    # ---- 第一步：用 graded 裁决入口判定 market regime ----
    graded_decision = None
    if market_context is not None:
        try:
            graded_decision = risk_controller.decide_market_regime(market_context)
            # 填充可序列化的 dict（MarketRegimeDecision 是 frozen dataclass，
            # 需要手动转 dict 才能写 JSON/data_metrics）
            graded_decision_dict = {
                "regime": graded_decision.regime,
                "allow_entry": graded_decision.allow_entry,
                "reason_code": graded_decision.reason_code,
                "reason": graded_decision.reason,
                "position_multiplier": graded_decision.position_multiplier,
                "score_threshold": graded_decision.score_threshold,
                "daily_signal_limit": graded_decision.daily_signal_limit,
                "require_relative_strength": graded_decision.require_relative_strength,
            }
        except Exception as e:
            logger.error("shadow 模拟 graded 裁决失败：%s", e)
            graded_decision_dict["reason"] = f"裁决异常：{e}"

    # ---- 第二步：若 graded 允许开仓，在【原始形态候选池】上跑完整 graded 链 ----
    # 修复 issue 4：不再只对 hard_filter 放行的 signals 二次筛选，而是从原始
    # 候选池出发，因此弱市 hard_filter 输出为空时仍能评估 graded 是否会放行。
    graded_rs_filtered_count = 0
    if graded_decision_dict["allow_entry"]:
        g_threshold = graded_decision_dict["score_threshold"]
        g_limit = graded_decision_dict["daily_signal_limit"]
        g_require_rs = graded_decision_dict["require_relative_strength"]
        rs_lookback = graded_policy.relative_strength_lookback
        rs_min = graded_policy.relative_strength_min

        index_closes = None
        if market_context is not None and market_context.index_close_history is not None:
            index_closes = market_context.index_close_history

        for item in candidate_pool:
            # 评分阈值检查（使用形态原始评分，未被 score_adjustment 改写）
            if g_threshold > 0 and item.get("score", 0) < g_threshold:
                continue

            item_rs: Optional[float] = None
            # 相对强度检查（与 graded 主流程共用 calculate_relative_strength，
            # 输入均为按交易日期索引的 Series）
            if g_require_rs:
                item_rs = calculate_relative_strength(
                    item.get("stock_closes"), index_closes, rs_lookback
                )
                if item_rs is None or item_rs < rs_min:
                    graded_rs_filtered_count += 1
                    continue

            graded_signals_out.append({
                "code": item.get("code"),
                "name": item.get("name"),
                "score": item.get("score"),
                "relative_strength": item_rs,
            })

        # P2-4 修复：配额闭环必须与正式 graded 路径完全一致——月额度 + 日额度 +
        # 分级弱市日限额统一走 apply_candidate_quota_chain。修复前 shadow 只应用
        # 分级 daily_signal_limit，遗漏 max_per_month / max_daily_signals / 已用
        # 月额度，会高估 graded 放行能力、污染影子期对比数据。
        graded_signals_out.sort(key=lambda x: x.get("score", 0), reverse=True)
        graded_signals_out = apply_candidate_quota_chain(
            graded_signals_out,
            used_count=used_monthly_count,
            max_per_month=max_per_month,
            max_daily=max_daily_signals,
            graded_daily_limit=g_limit,
        )

    # ---- 第三步：构造差异摘要（hard_filter 与 graded 在同一候选池上的对比） ----
    hf_pass_count = sum(1 for it in candidate_pool if it.get("hard_filter_passed"))
    # 修复 issue 4：hard_filter 链的放行数取自同一候选池的 risk_result.passed，
    # 而非仅统计正式 signals，保证两条链在同一输入上对比。
    hf_count = hf_pass_count if trend_filter_reason is None else 0
    diff_parts = []
    hf_blocked = trend_filter_reason is not None
    gd_allow = graded_decision_dict["allow_entry"]
    gd_count = len(graded_signals_out)

    if not gd_allow:
        # graded 禁止开仓
        if hf_blocked:
            diff_parts.append(
                f"hard_filter 阻断({trend_filter_reason}) 与 graded 禁止开仓"
                f"({graded_decision_dict['reason_code']}) 结论一致"
            )
        else:
            diff_parts.append(
                f"hard_filter 放行 {hf_count} 只，但 graded 禁止开仓"
                f"({graded_decision_dict['reason_code']})——graded 更保守"
            )
    else:
        # graded 允许开仓
        if hf_blocked:
            diff_parts.append(
                f"hard_filter 阻断({trend_filter_reason})，"
                f"但 graded 放行 {gd_count} 只——graded 更宽松"
            )
        else:
            diff_parts.append(
                f"hard_filter 放行 {hf_count} 只，graded 放行 {gd_count} 只"
                f"——{'一致' if hf_count == gd_count else 'graded 有截断'}"
            )
    if official_selected_count != hf_count:
        diff_parts.append(
            f"（注：hard_filter 正式 signals 经月/日额度截断后为 {official_selected_count} 只）"
        )

    diff_summary = "；".join(diff_parts)
    logger.info("[shadow] graded 模拟：%s", diff_summary)

    return {
        "hard_filter_status": hard_filter_status,
        "hard_filter_passed_count": hf_pass_count,
        "graded_decision": graded_decision_dict,
        "graded_signals": graded_signals_out,
        "graded_rs_filtered_count": graded_rs_filtered_count,
        "diff_summary": diff_summary,
    }


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
    # V3 新增：分级裁决结果（graded/shadow 模式下使用，hard_filter 下为 None）
    market_regime_decision: Optional[MarketRegimeDecision] = None
    # V3 新增：分级模式下的相对强度 policy 快捷引用（避免反复 config 查找）
    _graded_policy = risk_config.market_regime_policy_config

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

    # ===== V3 新增：graded 模式前置分级裁决 =====
    # graded 模式：用 decide_market_regime 替代旧的 trend_filter_reason 判断
    # shadow 模式：主流程仍走 hard_filter（零回归），末尾额外模拟一遍 graded 采集差异
    if risk_config.market_regime_mode == "graded":
        if market_context is not None:
            try:
                market_regime_decision = risk_controller.decide_market_regime(market_context)
                # 打印分级裁决结果
                print(
                    f"分级裁决：regime={market_regime_decision.regime}, "
                    f"multiplier={market_regime_decision.position_multiplier}, "
                    f"score_threshold={market_regime_decision.score_threshold}, "
                    f"daily_limit={market_regime_decision.daily_signal_limit}, "
                    f"require_rs={market_regime_decision.require_relative_strength}, "
                    f"allow_entry={market_regime_decision.allow_entry}"
                )
                # 修复 issue 6：graded 放行时，必须清空早前 check_market_trend
                # 写入的旧硬过滤状态（trend_filter_reason），否则会出现
                # selected_count>0 且 blocked_market=True 的矛盾结论。
                if not market_regime_decision.allow_entry:
                    trend_filter_reason = market_regime_decision.reason_code
                    print(f"分级裁决禁止开仓（reason={market_regime_decision.reason}），继续扫描原始候选")
                else:
                    trend_filter_reason = None
                    print(f"分级裁决放行（regime={market_regime_decision.regime}），"
                          f"已清空旧硬过滤状态")
            except Exception as e:
                logger.error("分级裁决失败：%s", e)
        else:
            # graded/shadow 模式但 market_context 不可用 → unknown，禁止开仓
            logger.warning(
                "graded/shadow 模式下 market_context 不可用，裁决为 unknown，禁止开仓"
            )
            trend_filter_reason = "market_context_unavailable"
            # 构造一个保守的 unknown decision 让后续逐候选循环的 apply_all_controls
            # 知道要阻断（_apply_graded_controls 内部自己会 decide_market_regime，
            # 但在这里存一份可用于最终日志打印）
            market_regime_decision = MarketRegimeDecision(
                regime="unknown",
                allow_entry=False,
                position_multiplier=0.0,
                score_threshold=0.0,
                daily_signal_limit=0,
                require_relative_strength=False,
                reason_code="market_context_unavailable",
                reason="指数数据不可用",
            )

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
    # 原始形态候选明细（code/name），写入 data_metrics 供飞书推送展示
    raw_candidates: List[Dict[str, str]] = []
    signals: List[Dict] = []

    # ===== V3 新增：分级模式下的细粒度计数（用于 data_metrics） =====
    rs_pass_count = 0          # 相对强度通过的候选数
    rs_filtered_count = 0      # 相对强度不足/不可用被阻断的候选数

    # shadow 模式：原始形态候选池（修复 issue 4）——采集所有通过个股层面过滤
    # （冷却期/年度额度）的候选，供末尾在同一输入上对比两条裁决链
    shadow_mode = risk_config.market_regime_mode == "shadow"
    shadow_pool: List[Dict] = []

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
            raw_candidates.append({"code": str(code), "name": str(name)})

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

            # 形态原始评分（apply_all_controls 可能被 score_adjustment 改写；
            # shadow 对比 graded 链时必须使用未改写的原始评分）
            pattern_score = chosen.score
            risk_result = risk_controller.apply_all_controls(chosen, effective_context)

            # shadow 模式：采集原始形态候选池（含被 hard_filter 市场过滤拦截的候选），
            # 供末尾在同一输入上分别跑 hard_filter/graded 两条完整裁决链（修复 issue 4）。
            if shadow_mode:
                shadow_pool.append({
                    "code": code,
                    "name": name,
                    "score": pattern_score,
                    "hard_filter_passed": bool(risk_result.passed),
                    "stock_closes": _build_stock_close_series(kline_df, signal_date),
                })

            if not risk_result.passed:
                logger.info(
                    "[风控过滤] code=%s name=%s rule=%s reason=%s score=%.2f signal_date=%s",
                    code, name, risk_result.filter_rule, risk_result.filter_reason,
                    chosen.score, signal_date,
                )
                continue

            # ---------- V3 新增：分级模式下的相对强度检查 ----------
            relative_strength_value: Optional[float] = None
            # 从 risk_result.details 取出分级裁决附加的信息
            graded_details = risk_result.details or {}
            # graded_mode: 只有 graded 模式走分级的相对强度检查和字段组装；
            # shadow 模式主循环走 hard_filter，graded 字段使用 hard_filter 默认值
            graded_mode = risk_config.market_regime_mode == "graded"
            graded_require_rs = graded_details.get("require_relative_strength", False)
            graded_regime = graded_details.get("market_regime", "hard_filter")
            graded_multiplier = graded_details.get("position_multiplier", 1.0)
            graded_threshold = graded_details.get("score_threshold", risk_config.score_threshold)
            graded_daily_limit = graded_details.get("daily_signal_limit", 0)
            graded_reason_code = graded_details.get("reason_code", "")

            if graded_mode and graded_require_rs:
                lookback = _graded_policy.relative_strength_lookback

                # ---- 前视偏差修复：先按 signal_date 截断 kline_df ----
                # db.get_kline_history 返回数据库里最新的 N 个交易日，
                # 当 --signal-date 指定历史日期时，kline_df 末尾会包含
                # signal_date 之后的收盘价 → 前视偏差。
                # 修复：只保留 date <= signal_date 的记录后再交给 RS 计算。
                try:
                    kline_cut = kline_df[
                        kline_df["date"].dt.strftime("%Y-%m-%d") <= signal_date
                    ]
                except Exception as e:
                    logger.warning("kline_df 按 signal_date 截断失败：%s", e)
                    kline_cut = kline_df

                stock_closes_series: Optional[pd.Series] = None
                if not kline_cut.empty:
                    # ---- 序列末日期对齐校验 ----
                    # 截断后 kline_cut 的最后一个日期必须等于 signal_date，
                    # 否则收盘价窗口错位（signal_date 当天数据缺失），
                    # 继续计算 RS 会得到错误结果。
                    last_date_str = kline_cut["date"].iloc[-1].strftime("%Y-%m-%d")
                    if last_date_str != signal_date:
                        logger.warning(
                            "[相对强度] code=%s kline 末日期 %s != signal_date=%s，"
                            "窗口错位，阻断",
                            code, last_date_str, signal_date,
                        )
                        rs_filtered_count += 1
                        continue

                    # 个股收盘价 Series（索引为交易日，升序，供按日期对齐）
                    try:
                        stock_closes_series = pd.Series(
                            kline_cut["close"].astype(float).values,
                            index=pd.to_datetime(kline_cut["date"]).dt.strftime(
                                "%Y-%m-%d"
                            ).values,
                            name="stock_close",
                        )
                    except Exception:
                        stock_closes_series = None

                # 指数收盘价 Series（MarketContext.index_close_history，索引为交易日）
                index_closes_series: Optional[pd.Series] = None
                if (
                    market_context is not None
                    and market_context.index_close_history is not None
                ):
                    index_closes_series = market_context.index_close_history

                relative_strength_value = calculate_relative_strength(
                    stock_closes_series, index_closes_series, lookback
                )

                if relative_strength_value is None:
                    # 数据不可用 → 默认阻断（生产安全原则，不静默放行）
                    logger.info(
                        "[相对强度] code=%s 数据不可用（stock_points=%d, index_points=%d, lookback=%d），阻断",
                        code,
                        len(stock_closes_series) if stock_closes_series is not None else 0,
                        len(index_closes_series) if index_closes_series is not None else 0,
                        lookback,
                    )
                    rs_filtered_count += 1
                    continue
                elif relative_strength_value < _graded_policy.relative_strength_min:
                    logger.info(
                        "[相对强度] code=%s %.4f < 阈值 %.4f，阻断",
                        code, relative_strength_value, _graded_policy.relative_strength_min,
                    )
                    rs_filtered_count += 1
                    continue
                else:
                    rs_pass_count += 1
                    logger.info(
                        "[相对强度] code=%s %.4f ≥ 阈值 %.4f，通过",
                        code, relative_strength_value, _graded_policy.relative_strength_min,
                    )

            # ---- 通过风控 + 相对强度：组装 entry ----
            support_level = chosen.detail.get("support_level", 0)
            stop_loss_ratio = params.get("stop_loss_ratio", 0.97)
            stop_loss_price = support_level * stop_loss_ratio

            # 分级字段（graded/shadow 模式来自 risk_result.details，hard_filter 模式用默认值保持 JSON 结构一致）
            if graded_mode:
                market_regime_val = graded_regime
                market_reason_code_val = graded_reason_code
                position_multiplier_val = graded_multiplier
                recommended_position_pct_val = round(
                    _graded_policy.base_position_pct * graded_multiplier, 4
                )
                base_position_pct_val = _graded_policy.base_position_pct
                rs_lookback_val = _graded_policy.relative_strength_lookback
            else:
                market_regime_val = "hard_filter"
                market_reason_code_val = None
                position_multiplier_val = 1.0
                recommended_position_pct_val = round(_graded_policy.base_position_pct, 4)
                base_position_pct_val = _graded_policy.base_position_pct
                rs_lookback_val = _graded_policy.relative_strength_lookback

            entry = {
                # ===== 原有字段（保持不变） =====
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
                # ===== V3 新增：分级裁决字段（hard_filter 也用默认值填充） =====
                "market_regime": market_regime_val,
                "market_reason_code": market_reason_code_val,
                "position_multiplier": position_multiplier_val,
                "base_position_pct": base_position_pct_val,
                "recommended_position_pct": recommended_position_pct_val,
                "relative_strength": relative_strength_value,
                "relative_strength_lookback": rs_lookback_val,
            }

            signals.append(entry)
            print(
                f"{code}: 支撑位 {support_level:.2f}, 止损 {stop_loss_price:.2f}, "
                f"评分 {chosen.score:.2f}"
                + (
                    f", RS={relative_strength_value:.4f}"
                    if relative_strength_value is not None
                    else ""
                )
                + (
                    f", 建议仓位 {recommended_position_pct_val:.2%}"
                    if graded_mode
                    else ""
                )
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

    # ===== P2-4 修复：配额闭环统一入口（graded 正式路径与 shadow 模拟复用） =====
    # 月额度 + 日额度 + 分级弱市日限额必须同时生效。graded 模式下先 cap_by_monthly_quota
    # 再按分级裁决的 daily_signal_limit 二次截断；shadow 模式主流程走 hard_filter，
    # graded 日限额由 shadow 模拟跑独立采集（同样复用 apply_candidate_quota_chain）。
    graded_limit = 0
    if risk_config.market_regime_mode == "graded" and market_regime_decision is not None:
        graded_limit = market_regime_decision.daily_signal_limit

    # 中间结果仅用于日志分层统计（与共享函数口径一致）
    monthly_selected = cap_by_monthly_quota(
        signals,
        used_count=monthly_count,
        max_per_month=risk_config.max_signals_per_month,
        max_daily=max_daily_signals,
    )
    selected = apply_candidate_quota_chain(
        signals,
        used_count=monthly_count,
        max_per_month=risk_config.max_signals_per_month,
        max_daily=max_daily_signals,
        graded_daily_limit=graded_limit,
    )

    quota_dropped_count = len(signals) - len(monthly_selected)
    if quota_dropped_count > 0:
        print(f"月度/日额度截断：原 {len(signals)} 只 → 剩余 {len(monthly_selected)} 只（丢弃 {quota_dropped_count} 只）")

    graded_daily_dropped_count = len(monthly_selected) - len(selected)
    if graded_daily_dropped_count > 0:
        print(
            f"分级日限额截断：剩余 {len(monthly_selected)} 只 "
            f"→ {len(selected)} 只（丢弃 {graded_daily_dropped_count} 只，"
            f"弱市日限 = {graded_limit}）"
        )

    signals = selected
    total_dropped_count = quota_dropped_count + graded_daily_dropped_count

    # 修复 issue 6：graded 模式下若市场允许开仓但相对强度把候选全部拦掉，
    # 落库原因应明确为 relative_strength_filtered_all，而非错误归因到 MA20
    # （分级放行时 trend_filter_reason 已清空）或笼统的 risk_controls_filtered_all。
    if (
        risk_config.market_regime_mode == "graded"
        and not signals
        and raw_candidate_count > 0
        and trend_filter_reason is None
        and rs_filtered_count > 0
    ):
        trend_filter_reason = "relative_strength_filtered_all"
        print(f"相对强度过滤全部候选：raw={raw_candidate_count}，rs_filtered={rs_filtered_count}")

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

    # ===== V3 新增：shadow 模式模拟 graded 裁决 =====
    # shadow 模式的主流程已经走 hard_filter（正式 signals 不变），
    # 这里额外独立跑一遍 graded 裁决，采集差异写入 data_metrics 供审计。
    # 核心保证：shadow 模拟跑只写 data_metrics，不改变 signals / signals_*.json / 飞书推送。
    shadow_sim_result: Dict = {}

    if shadow_mode:
        shadow_sim_result = _run_shadow_graded_simulation(
            risk_controller=risk_controller,
            market_context=market_context,
            graded_policy=_graded_policy,
            candidate_pool=shadow_pool,
            official_selected_count=len(signals),
            trend_filter_reason=trend_filter_reason,
            signal_date=signal_date,
            # P2-4：shadow 模拟必须与 graded 正式路径复用同一配额口径
            used_monthly_count=monthly_count,
            max_per_month=risk_config.max_signals_per_month,
            max_daily_signals=max_daily_signals,
        )

    # ===== V3 新增：data_metrics 构建 =====
    # 把 V3 新增的细粒度计数和分级裁决结果打包进 data_metrics dict，
    # 供 persist_scan_outcome 写入 scan_runs 表
    data_metrics: Dict = {}

    if risk_config.market_regime_mode == "graded":
        data_metrics.update({
            "policy_mode": risk_config.market_regime_mode,
            "market_regime": (
                market_regime_decision.regime if market_regime_decision else "unknown"
            ),
            "market_reason_code": (
                market_regime_decision.reason_code if market_regime_decision else None
            ),
            "position_multiplier": (
                market_regime_decision.position_multiplier if market_regime_decision else 1.0
            ),
            # 候选分层计数
            "raw_candidate_count": raw_candidate_count,
            "relative_strength_pass_count": rs_pass_count,
            "relative_strength_filtered_count": rs_filtered_count,
            "graded_daily_limit_truncated": graded_daily_dropped_count,
        })
    elif risk_config.market_regime_mode == "shadow":
        # shadow 模式：主流程 hard_filter 的 data_metrics + graded 模拟跑结果
        data_metrics.update({
            "policy_mode": risk_config.market_regime_mode,
            "raw_candidate_count": raw_candidate_count,
            # ---- shadow 模拟跑结果（同一原始候选池上两条链的对比） ----
            "shadow_hard_filter_status": shadow_sim_result.get("hard_filter_status"),
            "shadow_hard_filter_passed_count": shadow_sim_result.get("hard_filter_passed_count"),
            "shadow_graded_decision": shadow_sim_result.get("graded_decision"),
            "shadow_graded_signals": shadow_sim_result.get("graded_signals"),
            "shadow_graded_rs_filtered_count": shadow_sim_result.get("graded_rs_filtered_count"),
            "shadow_diff_summary": shadow_sim_result.get("diff_summary"),
        })
    else:
        # hard_filter 兼容路径
        data_metrics.update({
            "policy_mode": risk_config.market_regime_mode,
            "raw_candidate_count": raw_candidate_count,
        })

    # 原始形态候选明细：供飞书推送在 blocked_market 场景中展示股票代码
    data_metrics["raw_candidates"] = raw_candidates

    return {
        "signals": signals,
        "stock_pool_count": stock_pool_count,
        "scanned_count": scanned_count,
        "raw_candidate_count": raw_candidate_count,
        "selected_count": len(signals),
        # P2-3 修复：blocked_market 语义与 persist_scan_outcome 的 status 判定对齐——
        # 仅当「有原始候选、但被市场级硬过滤全部拦截」时为 True，
        # 避免 graded 模式下 trend_filter_reason 置位却无候选时误报市场拦截。
        "blocked_market": (
            len(signals) == 0
            and raw_candidate_count > 0
            and trend_filter_reason is not None
        ),
        "trend_filter_reason": trend_filter_reason,
        "risk_context": market_context,
        "data_version": data_version,
        # ===== V3 新增：分级裁决 data_metrics =====
        "market_regime_decision": market_regime_decision,
        "data_metrics": data_metrics,
        "rs_pass_count": rs_pass_count,
        "rs_filtered_count": rs_filtered_count,
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
    trend_filter_reason: Optional[str],
    stock_pool_count: int,
    scanned_count: int,
    data_version: Optional[datetime],
    data_metrics: Optional[Dict] = None,
    forced_failure_reason: Optional[str] = None,
) -> str:
    """
    将本次扫描的结果映射为 scan_runs 六态之一，并调用 db.upsert_scan_run 落盘。

    状态判定（2026-10-18 B-3 修复：严格对齐第 4 节六态语义表）：
      - forced_failure_reason 非空 → failed（P2-1 修复：配置加载失败/异常等
        真正的失败路径必须写 failed，不能因 raw=0 被误判为 completed_zero，
        否则监控只查 failed/blocked_data 时漏报）
      - selected_count > 0 → completed_with_signals（带最终信号）
      - raw_candidate_count > 0 → blocked_market（存在原始候选但全被拦截）
          * 优先用 trend_filter_reason（市场硬过滤原因）
          * 否则用 RISK_CONTROLS_FILTERED_ALL（非市场风控：评分/冷却/额度/截断）
      - raw_candidate_count == 0 → completed_zero（完整扫描，真·零候选）

    旧实现错误：最后一个 else 分支（raw>0 但非市场风控拦）也判为 completed_zero，
    会发送误导性通知"完整扫描后未发现形态"——而实际有原始候选存在。

    Args:
        forced_failure_reason: 非 None 时强制落盘为 failed，并以此作为 filter_reason；
                               用于配置加载失败 / 用户中断 / 未捕获异常等失败收敛路径。
    Returns:
        str: 实际写入的 status
    """
    if forced_failure_reason is not None:
        # P2-1：明确的失败路径一律写 failed（fail-closed，可被监控捕获）
        status = "failed"
        filter_reason = forced_failure_reason
    elif selected_count > 0:
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
                data_metrics=data_metrics,
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
                trend_filter_reason=None,
                stock_pool_count=0, scanned_count=0, data_version=None,
                forced_failure_reason=f"config_load_failed: {e}",
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
            trend_filter_reason=result["trend_filter_reason"],
            stock_pool_count=result["stock_pool_count"],
            scanned_count=result["scanned_count"],
            data_version=result["data_version"],
            data_metrics=result.get("data_metrics"),
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
                trend_filter_reason=None,
                stock_pool_count=0, scanned_count=0, data_version=None,
                forced_failure_reason="user_interrupt",
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
                trend_filter_reason=None,
                stock_pool_count=0, scanned_count=0, data_version=None,
                forced_failure_reason=f"exception: {e}",
            )
        except Exception as e2:
            print(f"scan_runs failed 状态也写失败：{e2}")
        sys.exit(1)


if __name__ == "__main__":
    main()
