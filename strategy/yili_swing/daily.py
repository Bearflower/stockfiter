"""伊利策略每日前复权数据筛选与飞书通知，不执行历史回测。"""

import json
import os
from pathlib import Path
from typing import Callable

import pandas as pd
import yaml

from .strategy import DEFAULT_CONFIG, YiliSwingStrategy


def load_daily_config(config_path: Path = DEFAULT_CONFIG) -> dict:
    with open(config_path, encoding="utf-8") as stream:
        config = yaml.safe_load(stream) or {}
    daily = ((config.get("strategies") or {}).get("yili_swing") or {}).get("daily")
    required = ("start_date", "schedule_time", "cutoff_time", "state_file", "poll_seconds",
                "retry_seconds", "fetch_timeout_seconds")
    if not isinstance(daily, dict) or any(key not in daily for key in required):
        raise ValueError("伊利每日调度配置缺项")
    pd.Timestamp(daily["start_date"])
    def valid_time(value):
        return (isinstance(value, str) and len(value) == 5 and value[2] == ":"
                and value[:2].isdigit() and value[3:].isdigit()
                and 0 <= int(value[:2]) < 24 and 0 <= int(value[3:]) < 60)

    if (not valid_time(daily["schedule_time"])
            or not valid_time(daily["cutoff_time"])
            or daily["schedule_time"] >= daily["cutoff_time"]):
        raise ValueError("伊利每日调度时间无效")
    if any(daily[key] <= 0 for key in ("poll_seconds", "retry_seconds", "fetch_timeout_seconds")):
        raise ValueError("伊利每日调度间隔或超时无效")
    return daily


def resolve_target_date(now, check_trading_day, nearest_trading_day):
    """盘前仅处理经交易日历确认的前一个已收盘交易日。"""
    today = now.date().isoformat()
    trading, degraded = check_trading_day(today)
    if degraded:
        raise ValueError("交易日历未验证，拒绝伊利日报")
    if not trading:
        return None
    target, degraded = nearest_trading_day(now, inclusive=False)
    if degraded or not target:
        raise ValueError("交易日历未验证目标日期，拒绝伊利日报")
    return target


def _validate_bars(frame: pd.DataFrame, code: str) -> pd.DataFrame:
    required = ("code", "date", "open", "high", "low", "close", "volume")
    if frame.empty or not set(required).issubset(frame.columns):
        raise ValueError("前复权日线缺失必要字段")
    if frame["code"].astype(str).ne(code).any():
        raise ValueError("前复权日线包含其他股票")
    bars = frame.copy()
    bars["date"] = pd.to_datetime(bars["date"], format="%Y-%m-%d", errors="raise")
    for column in ("open", "high", "low", "close", "volume"):
        bars[column] = pd.to_numeric(bars[column], errors="raise")
    if bars[list(required)].isna().any().any():
        raise ValueError("前复权日线包含空值")
    if (bars[["open", "high", "low", "close"]] <= 0).any().any():
        raise ValueError("前复权日线包含非正价格")
    if ((bars["high"] < bars[["open", "close", "low"]].max(axis=1)).any()
            or (bars["low"] > bars[["open", "close"]].min(axis=1)).any()):
        raise ValueError("前复权日线 OHLC 关系无效")
    if bars["date"].duplicated().any() or not bars["date"].is_monotonic_increasing:
        raise ValueError("前复权日线日期必须唯一且递增")
    bars.attrs["adjustment"] = "front"
    return bars


def format_summary(snapshot: dict, code: str) -> str:
    labels = {"buy": "买入", "sell": "卖出", "hold": "无"}
    return (
        f"伊利股份（{code}）波段策略日报｜{snapshot['date']}\n"
        f"原始收盘信号：{labels[snapshot['signal']]}\n"
        f"收盘价：{snapshot['close']:.2f} 元；RSI：{snapshot['rsi']:.2f}；"
        f"MA快线：{snapshot['ma_fast']:.2f}；MA慢线：{snapshot['ma_slow']:.2f}\n"
        "指标信号按下一交易日开盘规则观察；此消息不代表实盘持仓，"
        "也不执行盘中止损。"
    )


def run_daily(target_date: str, fetch: Callable, notifier, state_file: Path,
              dry_run: bool = False) -> dict:
    """查询前复权日线、计算当日信号，成功推送后持久化幂等日期。"""
    target_date = pd.Timestamp(target_date).strftime("%Y-%m-%d")
    state_file = Path(state_file)
    if not dry_run and state_file.exists():
        state = json.loads(state_file.read_text(encoding="utf-8"))
        if state.get("sent_date") == target_date:
            return {"status": "already_sent", "data_date": target_date}

    strategy = YiliSwingStrategy()
    daily = load_daily_config()
    frame = fetch(daily["start_date"], target_date)
    bars = _validate_bars(frame, strategy.params["code"])
    snapshot = strategy.snapshot(strategy.params["code"], {"d": bars})
    if snapshot["date"] != target_date:
        raise ValueError(f"前复权数据日期 {snapshot['date']} 与目标日期 {target_date} 不一致")

    result = {"status": "dry_run" if dry_run else "sent",
              "data_date": target_date, "signal": snapshot["signal"]}
    if dry_run:
        return result
    if not notifier.send_text_message(format_summary(snapshot, strategy.params["code"])):
        raise RuntimeError("伊利波段策略通知失败，保留重试资格")
    state_file.parent.mkdir(parents=True, exist_ok=True)
    temp_file = state_file.with_name(state_file.name + ".tmp")
    temp_file.write_text(json.dumps({"sent_date": target_date}, ensure_ascii=False),
                         encoding="utf-8")
    os.replace(temp_file, state_file)
    return result
