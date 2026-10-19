"""
交易日历模块
判断指定日期是否为 A 股交易日。

策略：优先使用 Baostock 交易日历（含法定节假日），
      Baostock 不可用时降级为 weekday 启发式（周一至周五）。
      结果按日期字符串 YYYY-MM-DD 做进程级缓存，避免重复请求。

R-2 修复：缓存改存 (result, degraded_flag) 元组，
新增 is_trading_day_with_confidence 函数让调用方可靠获知是否走了降级。
is_trading_day 保持原返回类型 bool 不变（向后兼容）。
"""

import logging
from datetime import datetime
from typing import Optional, Tuple

logger = logging.getLogger("trade_calendar")

# 日级缓存：key="YYYY-MM-DD", value=(result: bool, degraded: bool)
#   degraded=False → Baostock 已确认
#   degraded=True  → weekday 启发式降级（可信度不足）
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


def _resolve_with_confidence(date_str: str) -> Tuple[bool, bool]:
    """内部统一解析：返回 (是否交易日, 是否降级)。

    供 is_trading_day 和 is_trading_day_with_confidence 共用。
    命中缓存直接返回，否则查询 Baostock、失败则 weekday 降级。
    """
    # 命中缓存直接返回元组
    if date_str in _cache:
        return _cache[date_str]

    # 优先走 Baostock
    result = _query_via_baostock(date_str)
    if result is not None:
        _cache[date_str] = (result, False)
        logger.info("交易日判断 [%s] = %s (Baostock)", date_str, "交易日" if result else "非交易日")
        return result, False

    # 降级：weekday 启发式
    fallback_result = _fallback_weekday(date_str)
    _cache[date_str] = (fallback_result, True)
    logger.info(
        "交易日判断 [%s] = %s (weekday 降级)",
        date_str,
        "交易日" if fallback_result else "非交易日",
    )
    return fallback_result, True


def is_trading_day(date=None) -> bool:
    """判断指定日期是否为 A 股交易日（R-2 保持原签名不变）。

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

    result, _ = _resolve_with_confidence(date_str)
    return result


def is_trading_day_with_confidence(date=None) -> Tuple[bool, bool]:
    """R-2 新增：判断交易日并同时返回可信度。

    与 is_trading_day 共用同一缓存，不增加额外请求；
    让调用方（如 update_kline_daily._compute_target_date）
    可靠获知是否走了 weekday 降级。

    Args:
        date: 同 is_trading_day

    Returns:
        tuple[bool, bool]: (is_trading_day, is_degraded)
            is_degraded=False → Baostock 已确认，可信度高
            is_degraded=True  → weekday 启发式降级，可信度不足
    """
    # 统一转换为日期字符串
    if date is None:
        date_str = datetime.now().strftime("%Y-%m-%d")
    elif isinstance(date, datetime):
        date_str = date.strftime("%Y-%m-%d")
    else:
        date_str = date

    return _resolve_with_confidence(date_str)
