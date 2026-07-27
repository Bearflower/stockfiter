"""
数据源管理模块
仅使用 Baostock 数据源
"""

from typing import Optional

import pandas as pd

from utils.logger import get_logger

logger = get_logger()


class DataSourceManager:
    """数据源管理器，仅使用 Baostock"""

    def __init__(self):
        """初始化数据源管理器"""
        self.baostock_available = True

    def get_stock_list(self) -> Optional[pd.DataFrame]:
        """
        从 Baostock 获取股票列表

        Returns:
            DataFrame: 包含 code, name, symbol 列的股票列表
        """
        logger.info("开始获取股票列表（数据源：Baostock）")

        try:
            df = self._get_from_baostock()
            if df is not None and len(df) > 0:
                logger.info(f"从 Baostock 获取成功：{len(df)} 只股票")
                return df
        except Exception as e:
            logger.error(f"Baostock 获取失败：{e}")
            self.baostock_available = False

        logger.error("Baostock 数据源不可用")
        return None

    def _get_from_baostock(self) -> Optional[pd.DataFrame]:
        """从 Baostock 获取股票列表"""
        try:
            import baostock as bs

            logger.info("正在连接 Baostock 数据源...")

            lg = bs.login()
            if lg.error_code != '0':
                logger.error(f"Baostock 登录失败：{lg.error_msg}")
                return None

            logger.info(f"Baostock 登录成功：{lg.error_msg}")

            query_result = bs.query_stock_basic()

            data_list = []
            while query_result.next():
                row = query_result.get_row_data()
                data_list.append(row)

            bs.logout()

            if query_result.error_code != '0':
                logger.error(
                    f"Baostock 获取股票列表失败：{query_result.error_msg}"
                )
                return None

            if not data_list:
                logger.warning("Baostock 返回空数据")
                return None

            columns = query_result.fields
            stock_df = pd.DataFrame(data_list, columns=columns)

            logger.info(f"Baostock 获取到 {len(stock_df)} 只股票")

            result_df = pd.DataFrame()

            if 'code' in stock_df.columns:
                result_df['code'] = (
                    stock_df['code']
                    .astype(str)
                    .str.replace(r'^[a-z]+\.', '', regex=True)
                )
                result_df = result_df[result_df['code'].str.match(r'^\d{6}$')]
            else:
                logger.warning("Baostock 缺少 code 列")
                return None

            if 'code_name' in stock_df.columns:
                result_df['name'] = stock_df['code_name']
            else:
                logger.warning("Baostock 缺少 code_name 列")
                return None

            if 'ipoDate' in stock_df.columns:
                result_df['list_date'] = stock_df['ipoDate']
            else:
                logger.warning(
                    "Baostock 缺少 ipoDate 列，将无法过滤次新股"
                )

            result_df['symbol'] = result_df['code'].apply(
                lambda x: (
                    f"{x}.SH"
                    if x.startswith('6') or x.startswith('5')
                    else f"{x}.SZ"
                )
            )

            sh_count = len(result_df[result_df['code'].str.startswith('6')])
            sz_main_count = len(
                result_df[result_df['code'].str.startswith('00')]
            )
            sz_chi_count = len(
                result_df[result_df['code'].str.startswith('30')]
            )
            logger.info(
                f"市场分布：沪市{sh_count}只，深市主板{sz_main_count}只，"
                f"创业板{sz_chi_count}只"
            )

            columns = ['code', 'name', 'symbol']
            if 'list_date' in result_df.columns:
                columns.append('list_date')
            return result_df[columns]

        except ImportError:
            logger.warning("未安装 baostock 库")
            self.baostock_available = False
            return None
        except Exception as e:
            logger.error(f"Baostock 获取异常：{e}")
            raise


# 全局数据源管理器实例
_data_source: Optional[DataSourceManager] = None


def get_data_source() -> DataSourceManager:
    """获取全局数据源管理器"""
    global _data_source
    if _data_source is None:
        _data_source = DataSourceManager()
    return _data_source


def get_stock_list_from_data_source() -> pd.DataFrame:
    """
    从 Baostock 数据源获取股票列表

    Returns:
        DataFrame: 股票列表
    """
    source_manager = get_data_source()
    df = source_manager.get_stock_list()

    if df is None:
        raise Exception("Baostock 数据源不可用")

    return df