#!/usr/bin/env python3
"""手动执行 CSV 导入（清空表，不跳过）"""

from import_csv_to_db import CsvImporter
import signal
import sys

def signal_handler(signum, frame):
    print('收到中断信号，正在退出...')
    sys.exit(0)

signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

print('=' * 80)
print('开始 CSV 导入（清空表，不跳过）')
print('=' * 80)

importer = CsvImporter(csv_dir='data/backtest/baostocks_full')
importer.import_all_csvs(skip_existing=False, truncate_first=True)
