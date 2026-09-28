"""
交易日历模块
判断指定日期是否为 A 股交易日。

策略：优先使用 Baostock 交易日历（含法定节假日），
      Baostock 不可用时降级为 weekday 启发式（周一至周五）。
      结果按日期字符串 YYYY-MM-DD 做进程级缓存，避免重复请求。
"""

import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger("trade_calendar")

# 日级缓存：key="YYYY-MM-DD", value=True/False
_cache: dict = {}


def _query_via_baostock(date_str: str) -> Optional[bool]:
    """通过 Baostock 查询指定日期是否为交易日。

    Returns:
        True  - 是交易日
        False - 非交易日
        None  - 查询失败（baostock 不可用）
    """
    try:
        import baostock as bs
    except ImportError:
        logger.warning("baostock 未安装，交易日判断降级为 weekday 启发式")
        return None

    try:
        lg = bs.login()
        if lg.error_code != "0":
            logger.warning("Baostock 登录失败: %s，降级为 weekday 启发式", lg.error_msg)
            return None

        try:
            rs = bs.query_trade_dates(start_date=date_str, end_date=date_str)
            if rs.error_code != "0":
                logger.warning("Baostock 查询交易日失败: %s", rs.error_msg)
                return None

            if rs.next():
                row = rs.get_row_data()
                # row[0] = 日期字符串, row[1] = '1'(交易日) 或 '0'(非交易日)
                return row[1] == "1"
            return False
        finally:
            # 不调用 bs.logout()：Baostock 服务端故障时 logout() 会阻塞等待响应
            # 进程退出或函数返回时 OS 会自动清理网络连接
            pass
    except Exception as e:
        logger.warning("Baostock 查询交易日异常: %s", e)
        return None


def _fallback_weekday(date_str: str) -> bool:
    """降级策略：周一至周五视为交易日（不考虑法定节假日）。"""
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    # weekday(): 0=周一, 6=周日
    return dt.weekday() < 5


def is_trading_day(date=None):
    """判断指定日期是否为 A 股交易日。

    Args:
        date: 日期，支持以下格式
            - str: "YYYY-MM-DD"（默认）
            - datetime: 取其 .date() 部分
            - None: 默认取北京时间当天

    Returns:
        True = 交易日，False = 非交易日
    """
    # 统一转换为日期字符串
    if date is None:
        date_str = datetime.now().strftime("%Y-%m-%d")
    elif isinstance(date, datetime):
        date_str = date.strftime("%Y-%m-%d")
    else:
        date_str = date

    # 命中缓存直接返回
    if date_str in _cache:
        return _cache[date_str]

    # 优先走 Baostock
    result = _query_via_baostock(date_str)
    if result is not None:
        _cache[date_str] = result
        logger.info("交易日判断 [%s] = %s (Baostock)", date_str, "交易日" if result else "非交易日")
        return result

    # 降级：weekday 启发式
    result = _fallback_weekday(date_str)
    _cache[date_str] = result
    logger.info(
        "交易日判断 [%s] = %s (weekday 降级)",
        date_str,
        "交易日" if result else "非交易日",
    )
    return result
