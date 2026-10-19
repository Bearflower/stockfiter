"""
交易日历模块
判断指定日期是否为 A 股交易日。

策略：优先使用 Baostock 交易日历（含法定节假日），
      Baostock 不可用时降级为 weekday 启发式（周一至周五）。
      结果按日期字符串 YYYY-MM-DD 做进程级缓存，避免重复请求。

R-2 修复：缓存改存 (result, degraded_flag) 元组，
新增 is_trading_day_with_confidence 函数让调用方可靠获知是否走了降级。
is_trading_day 保持原返回类型 bool 不变（向后兼容）。

R-02 修复：新增 nearest_trading_date，作为流水线「信号日期 / target_date」的
唯一日期语义实现，供 scheduler_kline 与 scheduler_obpc 两个调度器共用，消除
「一处靠指数表、一处靠交易日历」的日期错配。
"""

import logging
from datetime import datetime, timedelta
from typing import Optional, Tuple

logger = logging.getLogger("trade_calendar")

# 日级缓存：key="YYYY-MM-DD", value=(result: bool, degraded: bool)
#   degraded=False → Baostock 已确认
#   degraded=True  → weekday 启发式降级（可信度不足）
_cache: dict = {}

# 回溯搜索上限（天数）。这只是「回溯搜索上限」这一技术边界，不是可调业务参数：
# 它仅用于兜住「连续长假 + 周末」的最坏情况，保证 nearest_trading_date 在有限步内收敛；
# 默认 30 天足以覆盖 A 股最长假期组合（国庆 + 中秋等），远超通常实际需要的 1-3 步。
MAX_LOOKBACK_DAYS = 30


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


def nearest_trading_date(
    date=None,
    inclusive: bool = True,
    max_lookback_days: int = MAX_LOOKBACK_DAYS,
) -> Tuple[Optional[str], bool]:
    """最近交易日（流水线「信号日期 / target_date」的唯一日期语义实现）。

    两个调度器共用本函数，保证同一交易日不会出现两处不同来源：
      - scheduler_kline：22:00 K 线更新与 07:00 补全写 task_status.target_date
      - scheduler_obpc：22:15 初扫 / 07:30 重扫 / 08:10 推送取 signal_date

    inclusive 决定「是否包含起点日」，对应流水线的两个时段：
      - inclusive=True  → 最近一个「≤ date」的交易日（收盘后时段用：当天数据已生成）。
        对应 scheduler_kline 22:00 K 线更新的 target_date、OBPC 22:15 初扫。
      - inclusive=False → 严格早于 date 的最近交易日（盘前时段用：当天数据尚未生成，
        等价于「最近一个已收盘交易日」）。对应 scheduler_kline 07:00 补全、
        OBPC 07:30 重扫 / 08:10 推送。

    实现：从起点日逐日回退，用 is_trading_day_with_confidence 判定，命中即返回。
      任一被查日期走了 weekday 降级 → degraded=True（可信度不足要能上报给运维）。
      超出 max_lookback_days 仍无交易日 → 返回 (None, degraded)（fail-closed，绝不猜日期）。

    不变量（必须与 kline 侧写 task_status 的 target_date 语义一致）：
      OBPC 重扫/推送使用的 signal_date 与 scheduler_kline 写入的 target_date 必须同源，
      否则 task_status_allows_run 的「target_date 完全相等」校验会 fail-closed 阻断扫描。

    Args:
        date: 起点日期，格式同 is_trading_day（str / datetime / date / None=北京时间当天）
        inclusive: True=含起点日；False=严格早于起点日
        max_lookback_days: 回溯搜索上限（天数），默认模块常量 MAX_LOOKBACK_DAYS

    Returns:
        tuple[str | None, bool]: (日期字符串 或 None, is_degraded)
            日期为 None 表示回溯窗口内无交易日（调用方必须 fail-closed 处理）
    """
    # 归一化起点日（datetime.date / str(date)[:10] 均形如 "YYYY-MM-DD"）
    if date is None:
        base_date = datetime.now().date()
    elif isinstance(date, datetime):
        base_date = date.date()
    else:
        base_date = datetime.strptime(str(date)[:10], "%Y-%m-%d").date()

    # inclusive=False 时起点日必须严格早于 date，因此从 date-1 开始回退
    start_date = base_date if inclusive else base_date - timedelta(days=1)

    degraded = False
    for offset in range(max_lookback_days):
        candidate_str = (start_date - timedelta(days=offset)).strftime("%Y-%m-%d")
        is_td, is_deg = is_trading_day_with_confidence(candidate_str)
        # 任一被查日期走了降级都累积上报（可信度不足）
        degraded = degraded or is_deg
        if is_td:
            return candidate_str, degraded

    # 回溯窗口内无交易日：fail-closed，返回 None 交由调用方按 guard 语义处理
    logger.warning(
        "回溯 %d 天内未找到交易日（起点 %s，inclusive=%s）",
        max_lookback_days, base_date, inclusive,
    )
    return None, degraded
