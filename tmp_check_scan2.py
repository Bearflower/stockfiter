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
# 查看scan_results列
cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name='scan_results' ORDER BY ordinal_position")
cols = [r[0] for r in cur.fetchall()]
print("scan_results列:", cols)

# 查看最近信号
cur.execute("SELECT code, name, scan_date, score FROM scan_results WHERE scan_date >= '2026-06-29' ORDER BY scan_date DESC, score DESC LIMIT 20")
print("\n最近扫描信号:")
for r in cur.fetchall():
    print(f"  {r[0]} {r[1]} 扫描日={r[2]} 评分={r[3]:.2f}")

# 查看7月6日后是否有任何信号
cur.execute("SELECT scan_date, COUNT(*) FROM scan_results WHERE scan_date >= '2026-07-06' GROUP BY scan_date ORDER BY scan_date")
print("\n7月6日后扫描结果:")
for r in cur.fetchall():
    print(f"  {r[0]}: {r[1]}")

# 检查7月是否有扫描记录
cur.execute("SELECT scan_date, COUNT(*) FROM scan_results WHERE scan_date >= '2026-07-01' GROUP BY scan_date ORDER BY scan_date")
print("\n7月扫描结果:")
for r in cur.fetchall():
    print(f"  {r[0]}: {r[1]}")
cur.close(); conn.close()