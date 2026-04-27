import baostock as bs

bs.login()
print("查询今天数据 (2026-04-09)...")
rs = bs.query_history_k_data_plus(
    "sh.600519",
    "date,open,high,low,close,volume",
    start_date="2026-04-09",
    end_date="2026-04-09",
    frequency="d",
    adjustflag="3"
)

print(f"Error: {rs.error_code} - {rs.error_msg}")
cnt = 0
while rs.next():
    cnt += 1
    print(rs.get_row_data())

print(f"Count: {cnt}")
bs.logout()
