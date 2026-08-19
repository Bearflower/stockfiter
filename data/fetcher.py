"""
K 线数据获取模块
仅使用 Baostock 数据源
"""

from datetime import datetime, timedelta
from typing import Optional, List, Generator, Tuple

import pandas as pd

from utils.logger import get_logger
from data.database import DatabaseManager

logger = get_logger()


class BaostockSession:
    """
    Baostock 会话管理器
    保持登录状态，避免重复 login/logout 开销
    """
    
    def __init__(self):
        self._bs = None
        self._logged_in = False
    
    def __enter__(self):
        self.login()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        # 不调用 bs.logout()：当 Baostock 服务端宕机/间歇性故障时，
        # logout() 会阻塞等待响应，导致进程无法退出（os._exit 永远无法执行）。
        # 进程退出时 OS 会自动清理网络连接，无需显式 logout。
        return False
    
    def login(self) -> bool:
        """登录 Baostock"""
        if self._logged_in:
            return True
        
        import baostock as bs
        self._bs = bs
        
        lg = bs.login()
        if lg.error_code != '0':
            logger.warning(f"Baostock 登录失败：{lg.error_msg}")
            return False
        
        self._logged_in = True
        return True
    
    def logout(self):
        """登出 Baostock"""
        if self._logged_in and self._bs:
            self._bs.logout()
            self._logged_in = False
    
    def get_kline(
        self, symbol: str, end_date: Optional[str] = None, days: int = 120
    ) -> Optional[pd.DataFrame]:
        """
        获取单只股票 K 线数据（复用登录状态）
        
        Args:
            symbol: 股票代码，如 '600519.SH'
            end_date: 截止日期
            days: 获取天数
            
        Returns:
            K 线 DataFrame
        """
        if not self._logged_in:
            if not self.login():
                return None
        
        try:
            code = symbol.split('.')[0]
            market = symbol.split('.')[1]
            
            bs_market = 'sh' if market == 'SH' else 'sz'
            bs_symbol = f"{bs_market}.{code}"
            
            if end_date is None:
                end_dt = datetime.now()
            else:
                end_dt = datetime.strptime(end_date.replace('-', ''), '%Y%m%d')
            
            start_dt = end_dt - timedelta(days=days + 30)
            start_date = start_dt.strftime('%Y-%m-%d')
            end_date_str = end_dt.strftime('%Y-%m-%d')
            
            rs = self._bs.query_history_k_data_plus(
                bs_symbol,
                "date,open,high,low,close,volume,amount,turn",
                start_date=start_date,
                end_date=end_date_str,
                frequency="d",
                adjustflag="3"
            )
            
            if rs.error_code != '0':
                return None
            
            data_list = []
            while rs.next():
                data_list.append(rs.get_row_data())
            
            if not data_list:
                return None
            
            df = pd.DataFrame(data_list, columns=rs.fields)
            df['date'] = pd.to_datetime(df['date'])
            
            numeric_cols = ['open', 'high', 'low', 'close', 'volume', 'amount']
            for col in numeric_cols:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors='coerce')
            
            df.sort_values('date', inplace=True)
            df.reset_index(drop=True, inplace=True)
            
            if len(df) > days:
                df = df.tail(days)
            
            return df
            
        except Exception as e:
            logger.error(f"获取 {symbol} K 线异常：{e}")
            return None


def batch_get_klines(
    symbols: List[str],
    days: int = 120,
    end_date: Optional[str] = None,
    progress_interval: int = 200
) -> Generator[Tuple[str, Optional[pd.DataFrame]], None, None]:
    """
    批量获取 K 线数据（保持登录状态）
    
    Args:
        symbols: 股票代码列表，如 ['600519.SH', '000001.SZ']
        days: 获取天数
        end_date: 截止日期
        progress_interval: 进度输出间隔
        
    Yields:
        (symbol, DataFrame) 元组
    """
    total = len(symbols)
    
    with BaostockSession() as session:
        for idx, symbol in enumerate(symbols):
            df = session.get_kline(symbol, end_date, days)
            yield symbol, df
            
            if (idx + 1) % progress_interval == 0:
                logger.info(f"批量获取进度：{idx + 1}/{total}")


def get_stock_daily_kline(
    symbol: str, end_date: Optional[str] = None, days: int = 120
) -> Optional[pd.DataFrame]:
    """
    获取单只股票的日 K 线数据（仅使用 Baostock）

    Args:
        symbol: 股票代码，如 '600519.SH'
        end_date: 截止日期，格式 YYYY-MM-DD，默认今天
        days: 获取最近多少天

    Returns:
        DataFrame: K 线数据，包含 date, open, high, low, close, volume, amount
    """
    try:
        logger.debug(f"尝试 Baostock: {symbol}")
        df = _get_from_baostock(symbol, end_date, days)
        if df is not None and len(df) > 0:
            logger.info(f"Baostock: {symbol} 获取到 {len(df)} 条")
            return df
        else:
            logger.warning(f"Baostock: {symbol} 返回空数据")
            return None
    except Exception as e:
        logger.error(f"Baostock 获取 {symbol} 失败：{e}")
        return None


def _get_from_baostock(
    symbol: str, end_date: Optional[str] = None, days: int = 120
) -> Optional[pd.DataFrame]:
    """从 Baostock 获取 K 线数据"""
    try:
        import baostock as bs

        code = symbol.split('.')[0]
        market = symbol.split('.')[1]

        bs_market = 'sh' if market == 'SH' else 'sz'
        bs_symbol = f"{bs_market}.{code}"

        if end_date is None:
            end_dt = datetime.now()
        else:
            end_dt = datetime.strptime(end_date.replace('-', ''), '%Y%m%d')

        start_dt = end_dt - timedelta(days=days + 30)
        start_date = start_dt.strftime('%Y-%m-%d')
        end_date_str = end_dt.strftime('%Y-%m-%d')

        lg = bs.login()
        if lg.error_code != '0':
            logger.warning(f"Baostock 登录失败：{lg.error_msg}")
            return None

        rs = bs.query_history_k_data_plus(
            bs_symbol,
            "date,open,high,low,close,volume,amount,turn",
            start_date=start_date,
            end_date=end_date_str,
            frequency="d",
            adjustflag="3"
        )

        if rs.error_code != '0':
            bs.logout()
            return None

        data_list = []
        while rs.next():
            row = rs.get_row_data()
            data_list.append(row)

        bs.logout()

        if not data_list:
            return None

        columns = rs.fields
        df = pd.DataFrame(data_list, columns=columns)

        df['date'] = pd.to_datetime(df['date'])
        numeric_cols = ['open', 'high', 'low', 'close', 'volume', 'amount']
        for col in numeric_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce')

        df.sort_values('date', inplace=True)
        df.reset_index(drop=True, inplace=True)

        if len(df) > days:
            df = df.tail(days)

        return df

    except Exception as e:
        logger.error(f"Baostock 获取 {symbol} 异常：{e}")
        raise


def fetch_and_cache_kline(
    db: DatabaseManager, symbol: str, code: str,
    days: int = 120, frequency: str = 'd'
) -> Optional[pd.DataFrame]:
    """
    获取 K 线数据并缓存到数据库

    Args:
        db: 数据库管理器
        symbol: 股票代码（带后缀）
        code: 股票代码（不带后缀）
        days: 获取天数
        frequency: K 线频率（d/w/m）

    Returns:
        DataFrame: K 线数据
    """
    cached_df = db.get_kline_history(code, days, frequency)

    if cached_df is not None and len(cached_df) > 0:
        logger.debug(f"{code} 使用缓存数据：{len(cached_df)} 条")
        return cached_df

    kline_df = get_stock_daily_kline(symbol, days=days)

    if kline_df is None or len(kline_df) == 0:
        logger.warning(f"{code} 无法获取 K 线数据")
        return None

    db.save_kline_history(code, kline_df, frequency)
    logger.debug(f"{code} 已缓存 {len(kline_df)} 条 K 线数据")

    return kline_df


def update_all_klines(
    db: DatabaseManager, stocks: List[dict], days: int = 120
) -> int:
    """
    批量更新所有股票的 K 线数据

    Args:
        db: 数据库管理器
        stocks: 股票列表
        days: 获取天数

    Returns:
        int: 成功更新的股票数量
    """
    success_count = 0
    total = len(stocks)

    for idx, stock in enumerate(stocks):
        code = stock['code']
        symbol = stock['symbol']

        try:
            df = fetch_and_cache_kline(db, symbol, code, days)
            if df is not None and len(df) > 0:
                success_count += 1
        except Exception as e:
            logger.error(f"{code} 更新 K 线失败：{e}")

        if (idx + 1) % 100 == 0:
            logger.info(
                f"K 线更新进度：{idx + 1}/{total}，成功：{success_count}"
            )

    logger.info(f"K 线更新完成：成功 {success_count}/{total}")
    return success_count


def get_kline_for_pattern(
    db: DatabaseManager, symbol: str, code: str, days: int = 120
) -> Optional[pd.DataFrame]:
    """
    获取 K 线数据用于形态检测

    Args:
        db: 数据库管理器
        symbol: 股票代码（带后缀）
        code: 股票代码（不带后缀）
        days: 获取天数

    Returns:
        DataFrame: K 线数据
    """
    return fetch_and_cache_kline(db, symbol, code, days)