#!/usr/bin/env python3
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
cur.execute("SELECT MAX(scan_date) FROM scan_results")
print("最新扫描日期:", cur.fetchone()[0])
cur.execute("SELECT scan_date, COUNT(*) FROM scan_results GROUP BY scan_date ORDER BY scan_date DESC LIMIT 10")
print("最近10次扫描信号数:")
for r in cur.fetchall():
    print(f"  {r[0]}: {r[1]}")
cur.close()
conn.close()