"""
tests/data/test_scan_result_upsert.py

验证 save_scan_result 的 UPSERT 语义：同一 (scan_date, code) 重扫后只剩一条记录，
且 score 等字段为最后一次写入的值。

依赖 scan_results 表的唯一索引 uq_scan_results_date_code
（DatabaseManager.__init__ 中的 _migrate_scan_results_unique_index 自动创建）。
"""

import pytest


def test_same_stock_and_date_is_upserted(database, scan_result):
    """
    同一 scan_date + code 两次 save_scan_result：只保留一条，score 取第二次的 91.5。

    覆盖：
        - 唯一索引生效（无唯一冲突）
        - UPSERT 的 DO UPDATE SET 路径（覆盖本次扫描字段）
    """
    scan_date = "2026-10-16"

    # 第一次写入：score=85.0
    database.save_scan_result(scan_date, [scan_result])

    # 第二次写入：同一 (scan_date, code)，score 覆盖为 91.5
    changed = {**scan_result, "score": 91.5}
    database.save_scan_result(scan_date, [changed])

    # 查询应只剩一条，且 score 为 91.5
    rows = database.fetch_scan_results(scan_date, scan_result["code"])
    assert len(rows) == 1, f"UPSERT 后应只剩 1 条，实际 {len(rows)} 条"
    assert float(rows[0]["score"]) == pytest.approx(
        91.5
    ), f"score 应为 91.5，实际 {rows[0]['score']}"


def test_different_codes_on_same_date_both_saved(database, scan_result):
    """
    同一 scan_date 不同 code 应各存一条（唯一约束是 (scan_date, code)）。
    """
    scan_date = "2026-10-16"
    other = {**scan_result, "code": "600000", "name": "浦发银行"}

    database.save_scan_result(scan_date, [scan_result, other])

    rows = database.fetch_scan_results(scan_date)
    codes = {r["code"] for r in rows}
    assert "000001" in codes
    assert "600000" in codes
    assert len(rows) == 2
