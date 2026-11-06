#!/usr/bin/env python3
"""在本机导出伊利股份前复权日线，供波段研究脚本使用。"""

import argparse
from datetime import date
from pathlib import Path

import pandas as pd


FIELDS = "date,open,high,low,close,volume,amount"


def fetch_front_history(bs, start_date: str, end_date: str) -> pd.DataFrame:
    """只查询 Baostock，不写生产数据库。"""
    result = bs.query_history_k_data_plus(
        "sh.600887", FIELDS, start_date=start_date, end_date=end_date,
        frequency="d", adjustflag="2",
    )
    if result.error_code != "0":
        raise RuntimeError(f"前复权行情获取失败：{result.error_msg}")
    rows = []
    while result.next():
        rows.append(result.get_row_data())
    if not rows:
        raise ValueError("前复权行情为空，未导出文件")
    frame = pd.DataFrame(rows, columns=result.fields)
    frame.insert(0, "code", "600887")
    for field in ("open", "high", "low", "close", "volume", "amount"):
        frame[field] = pd.to_numeric(frame[field], errors="raise")
    return frame.sort_values("date").reset_index(drop=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="本机导出伊利股份前复权日线")
    parser.add_argument("--start", default="2020-01-01")
    parser.add_argument("--end", default=date.today().isoformat())
    parser.add_argument("--output", default="data/backtest/yili_front.csv")
    args = parser.parse_args()

    import baostock as bs

    login = bs.login()
    if login.error_code != "0":
        raise RuntimeError(f"Baostock 登录失败：{login.error_msg}")
    frame = fetch_front_history(bs, args.start, args.end)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(output, index=False)
    print(f"已导出 {len(frame)} 条前复权日线：{output}，截至 {frame['date'].iloc[-1]}")


if __name__ == "__main__":
    main()
