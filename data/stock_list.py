"""
股票列表管理模块
负责获取全市场股票列表并过滤
"""

from typing import Optional, Dict

import pandas as pd

from utils.logger import get_logger
from data.database import DatabaseManager
from data.data_source import get_stock_list_from_data_source

logger = get_logger()


def get_stock_list_from_akshare(
    max_retries: int = 5, retry_delay: int = 15
) -> pd.DataFrame:
    """
    从数据源获取 A 股股票列表（支持 Baostock 数据源）

    Args:
        max_retries: 最大重试次数（备用时使用）
        retry_delay: 重试间隔（秒）

    Returns:
        DataFrame: 包含 code, name, symbol 列的股票列表
    """
    return get_stock_list_from_data_source()


def filter_stock_list(
    df: pd.DataFrame, config: Optional[Dict] = None
) -> pd.DataFrame:
    """
    过滤股票列表（委托 data.database.filter_stock_pool 统一实现）。

    本函数保留公开签名 (df, config=None) 不变，内部委托给模块级纯函数
    filter_stock_pool，确保 DB 写入路径（sync_stock_list_to_db）与扫描路径
   （daily_scan / backtest_*）使用完全相同的过滤语义。

    Args:
        df: 原始股票列表
        config: 过滤配置（缺省视为空 dict，全部开关按默认值生效）

    Returns:
        DataFrame: 过滤后的股票列表
    """
    from data.database import filter_stock_pool

    if config is None:
        config = {}

    original_count = len(df)
    logger.info("原始股票数量：%d", original_count)

    result = filter_stock_pool(df, config)

    filtered_count = len(result)
    logger.info(
        "过滤后股票数量：%d 只（剔除 %d 只）",
        filtered_count, original_count - filtered_count,
    )

    # 主板分布统计（仅当 main_board_only=True 且有结果时输出）
    if config.get("main_board_only", True) and filtered_count > 0:
        sz_main = len(result[
            result["code"].astype(str).str.startswith(("000", "001", "002"))
        ])
        sh_main = len(result[
            result["code"].astype(str).str.startswith(("600", "601", "603", "605"))
        ])
        logger.info(
            "主板分布：深市主板 %d 只，沪市主板 %d 只", sz_main, sh_main
        )

    return result


def sync_stock_list_to_db(
    db: DatabaseManager, config: Optional[Dict] = None, max_retries: int = 3
) -> pd.DataFrame:
    """
    同步股票列表到数据库

    Args:
        db: 数据库管理器
        config: 过滤配置
        max_retries: 获取股票列表的最大重试次数

    Returns:
        DataFrame: 过滤后的股票列表
    """
    logger.info("开始同步股票列表...")

    stock_df = get_stock_list_from_akshare(
        max_retries=max_retries, retry_delay=20
    )

    filtered_df = filter_stock_list(stock_df, config)

    db.save_stock_list(filtered_df)
    logger.info(f"成功同步 {len(filtered_df)} 只股票到数据库")

    return filtered_df


def get_stock_list_from_db(
    db: DatabaseManager, config: Optional[Dict] = None
) -> pd.DataFrame:
    """
    从数据库获取股票列表

    Args:
        db: 数据库管理器
        config: 过滤配置

    Returns:
        DataFrame: 股票列表
    """
    df = db.get_stock_list(config)
    logger.info(f"从数据库获取到 {len(df)} 只股票")
    return df


def init_stock_list(config: Optional[Dict] = None) -> pd.DataFrame:
    """
    初始化股票列表（获取并同步到数据库）

    Args:
        config: 过滤配置

    Returns:
        DataFrame: 过滤后的股票列表
    """
    db = DatabaseManager()
    try:
        return sync_stock_list_to_db(db, config)
    finally:
        db.close()