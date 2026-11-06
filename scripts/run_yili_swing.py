#!/usr/bin/env python3
"""从已确认的前复权日线生成伊利波段策略的单日观察结果。"""

import argparse
import json
from pathlib import Path
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import backtest_swing as swing
from strategy.yili_swing.strategy import YiliSwingStrategy


def evaluate_file(data_file: str, adjustment: str, expected_date: str) -> dict:
    """本地重放仓位并计算最新收盘信号；数据日期须由调用方确认。"""
    if adjustment != "front":
        raise ValueError("伊利波段策略只能使用前复权数据")
    strategy = YiliSwingStrategy()
    code = strategy.params["code"]
    bars = swing.load_data(data_file).get(code)
    if bars is None:
        raise ValueError(f"数据文件缺少股票 {code}")
    bars.attrs["adjustment"] = adjustment
    snapshot = strategy.snapshot(code, {"d": bars})
    if snapshot["date"] != expected_date:
        raise ValueError(f"数据日期 {snapshot['date']} 与预期日期 {expected_date} 不一致")

    indicators = strategy.indicators(bars)
    buy, sell = strategy.signals(indicators)
    trades, _, _, position = swing.run_backtest(
        bars, buy, sell, "indicator", swing.STOP_PARAMS["take_profit"],
        strategy.params["stop_loss"], close_at_end=False, return_position=True,
    )
    raw_signal = snapshot["signal"]
    next_action = ("buy" if raw_signal == "buy" and position is None else
                   "sell" if raw_signal == "sell" and position is not None else "none")
    last_execution = trades[-1] if trades and trades[-1]["exit_date"] == snapshot["date"] else None
    return {
        "strategy": strategy.name,
        "code": code,
        "adjustment": adjustment,
        "data_date": snapshot["date"],
        "close": snapshot["close"],
        "rsi": snapshot["rsi"],
        "ma_fast": snapshot["ma_fast"],
        "ma_slow": snapshot["ma_slow"],
        "raw_signal": raw_signal,
        "model_position": "holding" if position is not None else "cash",
        "next_open_action": next_action,
        "entry_date": position["entry_date"] if position is not None else None,
        "stop_price": position["entry"] * (1 - strategy.params["stop_loss"])
        if position is not None else None,
        "closed_trades": len(trades),
        "last_execution": last_execution,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", required=True, help="已确认的前复权日线 CSV")
    parser.add_argument("--adjustment", required=True, choices=("front",))
    parser.add_argument("--expected-date", required=True, help="预期的最新交易日 YYYY-MM-DD")
    args = parser.parse_args()
    result = evaluate_file(args.data, args.adjustment, args.expected_date)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
