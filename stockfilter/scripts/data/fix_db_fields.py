from data.database import DatabaseManager

db = DatabaseManager()
conn = db.conn
cur = conn.cursor()

print("修改 volume 字段类型...")
cur.execute('ALTER TABLE klines ALTER COLUMN volume TYPE NUMERIC(20,0)')
conn.commit()
print("✅ volume 修改成功")

print("修改 amount 字段类型...")
cur.execute('ALTER TABLE klines ALTER COLUMN amount TYPE NUMERIC(30,2)')
conn.commit()
print("✅ amount 修改成功")

print("\n验证修改结果...")
cur.execute("""
    SELECT column_name, data_type, numeric_precision, numeric_scale 
    FROM information_schema.columns 
    WHERE table_name = 'klines' AND column_name IN ('volume', 'amount')
    ORDER BY column_name
""")
for row in cur.fetchall():
    print(f"{row[0]}: {row[1]}({row[2]},{row[3]})")

print("\n✅ 数据库字段修改成功！")
db.close()
