#!/usr/bin/env python3
"""伊利波段策略每日前复权行情筛选和飞书摘要；不执行回测。"""

import argparse
from datetime import datetime
import json
from pathlib import Path
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from notification.service import FeishuNotifier
from scripts.fetch_swing_front import fetch_front_history
from strategy.yili_swing.daily import load_daily_config, resolve_target_date, run_daily
from utils.trade_calendar import is_trading_day_with_confidence, nearest_trading_date
from zoneinfo import ZoneInfo


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    date_args = parser.add_mutually_exclusive_group(required=True)
    date_args.add_argument("--date", help="已收盘目标交易日 YYYY-MM-DD，本地核查用")
    date_args.add_argument("--run-date", help="盘前自然日 YYYY-MM-DD，生产调度用")
    parser.add_argument("--dry-run", action="store_true", help="仅计算信号，不推送和记录幂等状态")
    args = parser.parse_args()
    daily = load_daily_config()
    target_date = args.date
    if args.run_date:
        now = datetime.strptime(args.run_date, "%Y-%m-%d").replace(
            tzinfo=ZoneInfo("Asia/Shanghai"))
        target_date = resolve_target_date(
            now, is_trading_day_with_confidence, nearest_trading_date)
        if target_date is None:
            print(json.dumps({"status": "market_closed", "run_date": args.run_date},
                             ensure_ascii=False))
            return

    def fetch(start: str, end: str):
        import baostock as bs

        login = bs.login()
        if login.error_code != "0":
            raise RuntimeError(f"Baostock 登录失败：{login.error_msg}")
        return fetch_front_history(bs, start, end)

    result = run_daily(
        target_date,
        fetch,
        FeishuNotifier(),
        Path(daily["state_file"]),
        dry_run=args.dry_run,
    )
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
