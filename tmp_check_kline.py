#!/usr/bin/env python3
"""检查数据库K线数据最新的日期"""
import psycopg2, os

conn = psycopg2.connect(
    host=os.environ.get("DB_HOST","10.3.0.12"),
    port=int(os.environ.get("DB_PORT","5432")),
    dbname=os.environ.get("DB_NAME","stockfilter"),
    user=os.environ.get("DB_USER","stockfilter_user"),
    password=os.environ.get("DB_PASSWORD","Stock@2024"),
    options="-c search_path=schema_stockfilter"
)
cur = conn.cursor()

# 1. 最新K线日期
cur.execute("SELECT MAX(date) FROM klines WHERE frequency='d'")
print("最新K线日期:", cur.fetchone()[0])

# 2. 7月数据量
cur.execute("SELECT date, COUNT(*) FROM klines WHERE date >= '2026-07-01' AND frequency='d' GROUP BY date ORDER BY date")
print("\n=== 7月每日K线数据量 ===")
for r in cur.fetchall():
    print(f"  {r[0]}: {r[1]}")

# 3. 为最近一次扫描可用的股票数
cur.execute("SELECT COUNT(*) FROM klines WHERE date >= '2026-06-01' AND frequency='d'")
print(f"\n6月1日以来K线数: {cur.fetchone()[0]}")

cur.close(); conn.close()