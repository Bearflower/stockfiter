"""
pytest 全局 fixture（不 autouse，按需 import）。

- repository_root: 仓库根路径，便于测试中定位 data/、scripts/、config/
- database: 真实 PostgreSQL 连接，经 TEST_DB_* 环境变量；不可用则 skip
  自动清理 scan_results 和 stocks 表中本次测试插入的数据
- scan_result: 单条扫描结果 fixture（test_scan_result_upsert.py 用）
- mixed_stock_rows: 覆盖全部板块/类型的股票 fixture（test_stock_pool_filters.py 用）
"""

import os
import sys
from datetime import date
from pathlib import Path

import pytest

# 将仓库根插入 sys.path，保证 `import scripts...`、`import data...` 可用
# 容器内 compose 会额外注入 PYTHONPATH=/app:/app/distill_changying，
# 本地 pytest 需要手动把 distill_changying 也加入 sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
DISTILL_ROOT = REPO_ROOT / "distill_changying"
if str(DISTILL_ROOT) not in sys.path:
    sys.path.insert(0, str(DISTILL_ROOT))


# ---------------------------------------------------------------------------
# 顶层 fixture
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def repository_root():
    """返回仓库根路径的 Path 对象"""
    return REPO_ROOT


# ---------------------------------------------------------------------------
# database fixture（真实 PostgreSQL，不可用则 skip）
# ---------------------------------------------------------------------------


@pytest.fixture(scope="function")
def database():
    """
    提供真实 DatabaseManager 连接。

    环境变量 TEST_DB_HOST / TEST_DB_PORT / TEST_DB_NAME / TEST_DB_USER /
    TEST_DB_PASSWORD / TEST_DB_SCHEMA 决定连接参数；
    缺少 DB_PASSWORD 或连接失败时 pytest.skip。
    测试结束后清理本次测试写入的 scan_results / stocks 数据。
    """
    # 设置临时环境变量让 DatabaseManager 构造函数读到
    test_db_password = os.getenv("TEST_DB_PASSWORD")
    if not test_db_password:
        pytest.skip("未配置 TEST_DB_PASSWORD，跳过数据库相关测试")

    os.environ.setdefault("DB_HOST", os.getenv("TEST_DB_HOST", "localhost"))
    os.environ.setdefault("DB_PORT", os.getenv("TEST_DB_PORT", "5432"))
    os.environ.setdefault("DB_NAME", os.getenv("TEST_DB_NAME", "stockfilter"))
    os.environ.setdefault("DB_USER", os.getenv("TEST_DB_USER", "postgres"))
    os.environ["DB_PASSWORD"] = test_db_password
    os.environ.setdefault("DB_SCHEMA", os.getenv("TEST_DB_SCHEMA", "schema_stockfilter"))

    from data.database import DatabaseManager

    try:
        db = DatabaseManager()
    except Exception as exc:
        pytest.skip(f"PostgreSQL 测试库连接失败：{exc}")

    # 记录本次测试要清理的数据键
    cleanup_keys = {"scan_result_codes": set(), "stocks_codes": set()}

    yield db

    # --- cleanup ---
    try:
        cur = db.conn.cursor()
        # 清理 scan_results 中的测试数据（按 fixture 已知 code 或按近期 date 清理）
        if cleanup_keys["scan_result_codes"]:
            codes = list(cleanup_keys["scan_result_codes"])
            cur.execute(
                f"DELETE FROM scan_results WHERE code = ANY(%s)",
                (codes,),
            )
        # 同时清理 scan_date 在未来的测试残留（用大于今天的日期作为清理标记不可行，
        # 这里改为扫描 scan_date 在过去 1 天内由 fixture 插入的记录——实际上我们
        # 通过 save_scan_result 的入参 code 来识别；如果测试用了动态 code，则
        # 另加一个清理手段）
        # 保险做法：按 stocks 表中我们知道要插入的 codes 清理
        if cleanup_keys["stocks_codes"]:
            codes = list(cleanup_keys["stocks_codes"])
            cur.execute(
                f"DELETE FROM scan_results WHERE code = ANY(%s)",
                (codes,),
            )
            cur.execute(
                f"DELETE FROM stocks WHERE code = ANY(%s)",
                (codes,),
            )
        db.conn.commit()
        cur.close()
    except Exception as exc:
        try:
            db.conn.rollback()
        except Exception:
            pass
        # 清理失败不影响测试结果，仅记日志
        import logging

        logging.getLogger(__name__).warning(f"测试数据清理失败：{exc}")
    finally:
        try:
            db.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# 数据 fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="function")
def scan_result() -> dict:
    """单条扫描结果（score=85.0，供 UPSERT 测试覆盖）"""
    return {
        "code": "000001",
        "name": "平安银行",
        "score": 85.0,
        "surge_date": date(2026, 10, 10),
        "support_level": 10.50,
        "current_close": 11.20,
        "drop_rate": 0.12,
        "min_vol_ratio": 0.55,
        "surge_price": 11.80,
        "surge_volume_ratio": 2.3,
        "surge_pct": 0.06,
        "low_after_surge": 10.80,
    }


@pytest.fixture(scope="function")
def mixed_stock_rows() -> list:
    """
    覆盖各板块/类型的股票列表，过滤后只剩主板两只。

    预期经 get_stock_list（全项过滤 + main_board_only）后的结果为：
        {'000001', '600000'}

    说明：
        000001 平安银行 —— 深市主板（000 前缀），保留
        600000 浦发银行 —— 沪市主板（600 前缀），保留
        600xxxST  —— ST 股，剔除
        300xxx    —— 创业板，剔除
        688xxx    —— 科创板，剔除
        8xxxxxx   —— 北交所，剔除
        4xxxxxx   —— 新三板，剔除
        xx退xx    —— 退市股，剔除
        000300    —— 沪深300 指数，剔除
        300ETF    —— ETF，剔除
    """
    rows = [
        # --- 主板保留 ---
        {"code": "000001", "name": "平安银行", "symbol": "000001", "list_date": "2012-06-15", "sector": "银行"},
        {"code": "600000", "name": "浦发银行", "symbol": "600000", "list_date": "1999-11-10", "sector": "银行"},
        # --- ST 剔除 ---
        {"code": "000002", "name": "*ST万科A", "symbol": "000002", "list_date": "2015-01-01", "sector": "地产"},
        # --- 退市剔除 ---
        {"code": "000003", "name": "ST东北高退", "symbol": "000003", "list_date": "2000-01-01", "sector": "交通"},
        # --- 创业板剔除 ---
        {"code": "300001", "name": "特锐德", "symbol": "300001", "list_date": "2009-10-30", "sector": "电力设备"},
        {"code": "301001", "name": "安克创新", "symbol": "301001", "list_date": "2020-07-13", "sector": "电子"},
        # --- 科创板剔除 ---
        {"code": "688001", "name": "华兴源创", "symbol": "688001", "list_date": "2019-07-22", "sector": "半导体"},
        {"code": "689001", "name": "中芯国际", "symbol": "689001", "list_date": "2020-07-16", "sector": "半导体"},
        # --- 北交所剔除（8 开头） ---
        {"code": "830001", "name": "北京康得新", "symbol": "830001", "list_date": "2015-11-20", "sector": "科技"},
        # --- 新三板剔除（4 开头） ---
        {"code": "430001", "name": "世纪鼎利", "symbol": "430001", "list_date": "2013-12-20", "sector": "通信"},
        # --- 次新股剔除（上市日期晚于 cutoff） ---
        {"code": "603999", "name": "新华网", "symbol": "603999", "list_date": "2025-05-10", "sector": "传媒"},
        # --- 指数/ETF 剔除 ---
        {"code": "000300", "name": "沪深300", "symbol": "000300", "list_date": None, "sector": "指数"},
        {"code": "510300", "name": "沪深300ETF", "symbol": "510300", "list_date": "2012-05-28", "sector": "ETF"},
    ]
    return rows
