#!/usr/bin/env python3
"""检查回踩日期与扫描日期的关系"""
import psycopg2, os
from datetime import datetime, timedelta

conn = psycopg2.connect(
    host=os.environ.get("DB_HOST","10.3.0.12"),
    port=int(os.environ.get("DB_PORT","5432")),
    dbname=os.environ.get("DB_NAME","stockfilter"),
    user=os.environ.get("DB_USER","stockfilter_user"),
    password=os.environ.get("DB_PASSWORD","Stock@2024"),
    options="-c search_path=schema_stockfilter"
)
cur = conn.cursor()

# 查看scan_results中的retrace_date
cur.execute("SELECT code, name, scan_date, retrace_date, score FROM scan_results WHERE scan_date >= '2026-06-29' ORDER BY scan_date DESC, score DESC")
print("最近扫描信号详情:")
for r in cur.fetchall():
    print(f"  {r[0]} {r[1]} 扫描日={r[2]} 回踩日={r[3]} 评分={r[4]:.2f}")

# 检查7月最近几天的K线日期分布
cur.execute("SELECT date, COUNT(*) FROM klines WHERE date >= '2026-07-13' AND frequency='d' GROUP BY date ORDER BY date")
print("\n7月13日后K线数据分布:")
for r in cur.fetchall():
    print(f"  {r[0]}: {r[1]}")

# 检查7月6日(周一)后是否有任何信号
cur.execute("SELECT scan_date, COUNT(*) FROM scan_results WHERE scan_date >= '2026-07-06' GROUP BY scan_date ORDER BY scan_date")
print("\n7月6日后扫描结果:")
for r in cur.fetchall():
    print(f"  {r[0]}: {r[1]}")

cur.close(); conn.close()