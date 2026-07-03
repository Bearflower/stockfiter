#!/usr/bin/env python3
"""下载 ETHUSDT 近3个月多时间频率K线数据（使用python-binance）"""

import asyncio
import json
import os
from datetime import datetime, timedelta
from binance.client import Client
from binance.exceptions import BinanceAPIException


def download_klines_sync(symbol: str, interval: str, start_time: datetime, end_time: datetime) -> list:
    """同步分批下载K线数据"""
    client = Client()
    all_klines = []
    current_start = int(start_time.timestamp() * 1000)
    end_ms = int(end_time.timestamp() * 1000)
    
    while current_start < end_ms:
        try:
            klines = client.futures_klines(
                symbol=symbol,
                interval=interval,
                startTime=current_start,
                endTime=end_ms,
                limit=1000
            )
        except BinanceAPIException as e:
            print(f"  API错误: {e}, 重试...")
            continue
        
        if not klines:
            break
        
        all_klines.extend(klines)
        last_time = klines[-1][0]
        if last_time <= current_start:
            break
        current_start = last_time + 1
        
        print(f"  [{interval}] 已下载 {len(all_klines)} 根, 最新: {datetime.fromtimestamp(last_time/1000)}")
    
    return all_klines


def main():
    output_dir = "data/ethusdt"
    os.makedirs(output_dir, exist_ok=True)
    
    end_time = datetime.now()
    start_time = end_time - timedelta(days=100)
    
    print(f"下载 ETHUSDT K线数据: {start_time.date()} ~ {end_time.date()}")
    
    for interval, desc in [("1h", "1小时"), ("4h", "4小时")]:
        print(f"\n下载 {desc} K线...")
        klines = download_klines_sync("ETHUSDT", interval, start_time, end_time)
        
        filename = f"{output_dir}/ethusdt_{interval}_{start_time.strftime('%Y%m%d')}_{end_time.strftime('%Y%m%d')}.json"
        with open(filename, 'w') as f:
            json.dump(klines, f)
        
        print(f"  已保存 {len(klines)} 根K线到 {filename}")
    
    print(f"\n数据下载完成！文件保存在 {output_dir}/")


if __name__ == "__main__":
    main()