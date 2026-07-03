#!/usr/bin/env python3
"""下载 ETHUSDT 15m K线数据用于 V2.4 回测"""
import requests
import json
import time
from datetime import datetime

symbol = 'ETHUSDT'
interval = '15m'
limit = 1000

all_klines = []
end_time = int(time.time() * 1000)

for batch in range(10):
    url = f'https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval={interval}&limit={limit}'
    if all_klines:
        url += f'&endTime={all_klines[0][0] - 1}'
    resp = requests.get(url, timeout=30)
    data = resp.json()
    if not data or not isinstance(data, list):
        break
    all_klines = data + all_klines
    print(f'批次 {batch+1}: {len(data)}根, 累计: {len(all_klines)}根, 起始: {datetime.fromtimestamp(data[0][0]/1000)}')
    if len(data) < limit:
        break
    time.sleep(0.5)

with open('data/ethusdt/ethusdt_15m.json', 'w') as f:
    json.dump(all_klines, f)

print(f'\n保存完成: {len(all_klines)}根15m K线')
print(f'时间范围: {datetime.fromtimestamp(all_klines[0][0]/1000)} ~ {datetime.fromtimestamp(all_klines[-1][0]/1000)}')