"""
tests/data/test_stock_pool_filters.py

验证数据库回退路径下 get_stock_list 对 global.stock_pool 全部过滤开关的支持，
与固定 CSV 路径经 filter_stock_pool 纯函数得到相同股票集合。

补充：纯 DataFrame 等价性测试（不依赖 PostgreSQL，本地无 DB 也能跑）。
"""

import pandas as pd
import pytest


# ---------------------------------------------------------------------------
# 纯 DataFrame fixture（不依赖 DB）
# ---------------------------------------------------------------------------


@pytest.fixture(scope="function")
def pure_mixed_stock_df() -> pd.DataFrame:
    """
    构造含各板块/类型的纯 DataFrame，覆盖：
      - 920xxx（北交所新代码段，原 filter_stock_pool 会漏掉）
      - 43xxxx（新三板，原 filter_stock_pool 误伤 4xx 全部）
      - 688xxx / 300xxx（科创板/创业板）
      - ST 小写（name 含 "st"，case=False 应排除）
      - list_date 恰好等于 cutoff_date（边界值应保留）
      - "上证 50ETF"（含遗留实现排除的关键词）
    """
    rows = [
        # --- 主板保留 ---
        {"code": "000001", "name": "平安银行", "symbol": "000001", "list_date": "2012-06-15", "sector": "银行"},
        {"code": "600000", "name": "浦发银行", "symbol": "600000", "list_date": "1999-11-10", "sector": "银行"},
        # --- 北交所 920xxx（必须被排除，原 filter_stock_pool 漏掉） ---
        {"code": "920001", "name": "北交所920测试", "symbol": "920001", "list_date": "2024-01-01", "sector": "科技"},
        # --- 北交所 83xxx / 87xxx（必须被排除） ---
        {"code": "830001", "name": "北交所83测试", "symbol": "830001", "list_date": "2015-11-20", "sector": "科技"},
        {"code": "870001", "name": "北交所87测试", "symbol": "870001", "list_date": "2016-01-01", "sector": "科技"},
        # --- 新三板 43xxxx（必须被排除，原 filter_stock_pool 误伤 4xx 全部） ---
        {"code": "430001", "name": "世纪鼎利", "symbol": "430001", "list_date": "2013-12-20", "sector": "通信"},
        # --- 科创板 / 创业板（必须被排除） ---
        {"code": "688001", "name": "华兴源创", "symbol": "688001", "list_date": "2019-07-22", "sector": "半导体"},
        {"code": "300001", "name": "特锐德", "symbol": "300001", "list_date": "2009-10-30", "sector": "电力设备"},
        # --- ST 小写（必须被排除，case=False） ---
        {"code": "000002", "name": "st万科A", "symbol": "000002", "list_date": "2015-01-01", "sector": "地产"},
        # --- 指数（含遗留关键词"上证"，必须被排除） ---
        {"code": "000300", "name": "沪深300", "symbol": "000300", "list_date": None, "sector": "指数"},
        {"code": "510050", "name": "上证50ETF", "symbol": "510050", "list_date": "2012-05-28", "sector": "ETF"},
        # --- 次新股（cutoff 之后） ---
        {"code": "603999", "name": "新华网", "symbol": "603999", "list_date": "2025-05-10", "sector": "传媒"},
        # --- 边界：list_date 恰好等于 cutoff_date（应保留，不是次新股） ---
        {"code": "000003", "name": "老股边界", "symbol": "000003", "list_date": "2025-01-01", "sector": "综合"},
    ]
    return pd.DataFrame(rows)


@pytest.fixture(scope="function")
def pure_filter_config() -> dict:
    """与 daily_scan / config.yaml global.stock_pool 对齐的过滤配置"""
    return {
        "exclude_st": True,
        "exclude_delisting": True,
        "exclude_beijing": True,
        "exclude_star_market": True,
        "exclude_chi_next": True,
        "exclude_new_third_board": True,
        "exclude_index": True,
        "exclude_new_stocks": True,
        "new_stock_cutoff_date": "2025-01-01",
        "main_board_only": True,
    }


# ---------------------------------------------------------------------------
# DB 依赖测试（database fixture 不可用时自动 skip）
# ---------------------------------------------------------------------------


def test_database_fallback_applies_all_mainboard_filters(
    database, mixed_stock_rows
):
    """
    覆盖全部过滤开关，确认最终只剩主板两只。

    预期：mixed_stock_rows 中经全项过滤 + main_board_only 后只剩
          {'000001'（深市主板）, '600000'（沪市主板）}。
    """
    # 用 insert_stocks 写入测试数据（INSERT ... ON CONFLICT (code) DO UPDATE）
    database.insert_stocks(mixed_stock_rows)

    filters = {
        "exclude_st": True,
        "exclude_delisting": True,
        "exclude_beijing": True,
        "exclude_star_market": True,
        "exclude_chi_next": True,
        "exclude_new_third_board": True,
        "exclude_index": True,
        "exclude_new_stocks": True,
        "new_stock_cutoff_date": "2025-01-01",
        "main_board_only": True,
    }

    result = database.get_stock_list(filters)

    assert result is not None, "get_stock_list 不应返回 None"
    codes = set(result["code"].astype(str))
    assert codes == {
        "000001",
        "600000",
    }, f"过滤后应仅剩 000001/600000，实际：{codes}"


def test_filter_stock_pool_pure_function_matches_sql_result(
    database, mixed_stock_rows
):
    """
    纯函数 filter_stock_pool 的结果应与 get_stock_list（SQL + 纯函数双重过滤）
    一致，确保 CSV 路径和数据库回退路径得到相同股票集合。
    """
    from data.database import filter_stock_pool

    database.insert_stocks(mixed_stock_rows)

    filters = {
        "exclude_st": True,
        "exclude_delisting": True,
        "exclude_beijing": True,
        "exclude_star_market": True,
        "exclude_chi_next": True,
        "exclude_new_third_board": True,
        "exclude_index": True,
        "exclude_new_stocks": True,
        "new_stock_cutoff_date": "2025-01-01",
        "main_board_only": True,
    }

    # 直接从数据库取全量，再用纯函数过滤
    all_df = database.get_stock_list(filters=None)
    pure_result = filter_stock_pool(all_df, filters)

    # 与 get_stock_list（SQL 已过滤 + 纯函数再过滤）的结果对比
    sql_result = database.get_stock_list(filters)

    assert set(pure_result["code"].astype(str)) == set(
        sql_result["code"].astype(str)
    )


# ---------------------------------------------------------------------------
# 纯 DataFrame 等价性测试（不依赖 DB，本地也能跑）
# ---------------------------------------------------------------------------


def test_filter_stock_list_delegates_to_filter_stock_pool_identical_result(
    pure_mixed_stock_df, pure_filter_config
):
    """
    等价性证明：filter_stock_list（委托路径）与 filter_stock_pool（直接调用）
    对同一份 DataFrame 必须得到完全相同的 code 集合。

    此测试确保 sync_stock_list_to_db 写入 DB 的股票集合 =
    daily_scan.py 从 CSV / DB 扫描时过滤出的股票集合 =
    backtest_obpc.py / backtest_accumulation.py 过滤出的股票集合。
    """
    from data.database import filter_stock_pool
    from data.stock_list import filter_stock_list

    result_a = filter_stock_pool(pure_mixed_stock_df, pure_filter_config)
    result_b = filter_stock_list(pure_mixed_stock_df.copy(), pure_filter_config)

    codes_a = set(result_a["code"].astype(str))
    codes_b = set(result_b["code"].astype(str))

    assert codes_a == codes_b, (
        f"filter_stock_pool codes={codes_a} 与 filter_stock_list codes={codes_b} 不一致"
    )


def test_filter_stock_pool_excludes_920_beijing_code(pure_mixed_stock_df, pure_filter_config):
    """920xxx 北交所新代码段必须被排除（原实现只写 startswith('8') 会漏掉 920）"""
    from data.database import filter_stock_pool

    result = filter_stock_pool(pure_mixed_stock_df, pure_filter_config)
    codes = set(result["code"].astype(str))

    assert "920001" not in codes, "920001（北交所新代码段）应被排除"
    assert "830001" not in codes, "830001（北交所）应被排除"
    assert "870001" not in codes, "870001（北交所）应被排除"


def test_filter_stock_pool_excludes_43_new_third_board(pure_mixed_stock_df, pure_filter_config):
    """43xxxx 新三板必须被排除（原实现用 startswith(('4','8')) 误伤 4xx 全部）"""
    from data.database import filter_stock_pool

    result = filter_stock_pool(pure_mixed_stock_df, pure_filter_config)
    codes = set(result["code"].astype(str))

    assert "430001" not in codes, "430001（新三板）应被排除"


def test_filter_stock_pool_excludes_legacy_index_keywords(pure_mixed_stock_df, pure_filter_config):
    """
    遗留实现排除的关键词（上证/深证/中证等）必须被纳入，
    避免"原来被排除、现在被保留"的回归。
    """
    from data.database import filter_stock_pool

    result = filter_stock_pool(pure_mixed_stock_df, pure_filter_config)
    codes = set(result["code"].astype(str))

    assert "510050" not in codes, "上证50ETF 应因含'上证'/ETF 被排除"
    assert "000300" not in codes, "沪深300 指数应被排除"


def test_filter_stock_pool_boundary_list_date_equals_cutoff(pure_mixed_stock_df, pure_filter_config):
    """list_date 恰好等于 cutoff_date 应保留（不是次新股）"""
    from data.database import filter_stock_pool

    result = filter_stock_pool(pure_mixed_stock_df, pure_filter_config)
    codes = set(result["code"].astype(str))

    assert "000003" in codes, "list_date=cutoff_date 的老股应保留"


def test_filter_stock_list_with_none_config_uses_defaults(pure_mixed_stock_df):
    """
    filter_stock_list(config=None) 应按默认值生效，不抛异常。

    这是 backtest_obpc.py / backtest_accumulation.py 调用时的典型路径
    （它们不传 config）。
    """
    from data.stock_list import filter_stock_list

    # main_board_only 默认 True，main_board 只保留 000/001/002/600/601/603/605
    # 纯 DataFrame 里只有 000001 / 600000 / 000003 三个主板候选
    result = filter_stock_list(pure_mixed_stock_df.copy(), config=None)
    codes = set(result["code"].astype(str))

    assert codes == {"000001", "600000", "000003"}, (
        f"默认配置下应保留三只主板，实际：{codes}"
    )


def test_filter_stock_pool_accepts_config_override(pure_mixed_stock_df):
    """
    config 中的前缀/关键词列表可以覆盖默认值，代码使用 cfg.get(key, default) 模式。
    此测试验证覆盖机制生效（单独覆盖 beijing_prefixes 后，920001 不再被排除）。
    """
    from data.database import filter_stock_pool

    # 只测试北交所前缀覆盖效果，关闭其他板块/主板开关让结果可预测
    cfg = {
        "exclude_st": False,
        "exclude_delisting": False,
        "exclude_beijing": True,
        "exclude_star_market": False,
        "exclude_chi_next": False,
        "exclude_new_third_board": False,
        "exclude_index": False,
        "exclude_new_stocks": False,
        "main_board_only": False,
        # 故意不包含 920，验证配置覆盖生效
        "beijing_prefixes": ["83", "87"],
    }
    result = filter_stock_pool(pure_mixed_stock_df, cfg)
    codes = set(result["code"].astype(str))

    # 920001 应该被保留了（因为 beijing_prefixes 覆盖后不含 920）
    assert "920001" in codes, "beijing_prefixes 不含 920 时，920001 应被保留"
    # 但 830001 仍被排除
    assert "830001" not in codes, "beijing_prefixes 含 83，830001 应被排除"
    assert "870001" not in codes, "beijing_prefixes 含 87，870001 应被排除"
