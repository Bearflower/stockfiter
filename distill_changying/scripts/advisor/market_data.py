"""
E大投资决策助手市场数据获取模块

提供市场数据源的抽象接口和 baostock 实现，支持数据质量校验和缓存降级。

核心组件：
- MarketDataSource: 抽象基类，定义数据源接口
- BaostockDataSource: 通过 baostock 获取 A 股指数 PE/PB 估值（基于成分股 PE 中位数）
- validate_valuation: 数据质量校验函数
- fetch_all_valuations: 数据获取主入口（含缓存与降级）

估值计算方案：
- 主子方案：查询指数成分股个股的 peTTM/pbMRQ，取中位数作为指数 PE/PB
- 成分股来源：baostock 的 query_sz50_stocks / query_hs300_stocks / query_zz500_stocks
- PE/PB 分位：使用指数收盘价在 N 年历史中的分位近似（避免逐股查历史 PE 的耗时）
- 回退方案：当成分股 API 不可用时，回退到指数 K 线查询（原方案）
- 采样策略：按代码字典序取前 N 只成分股（采样数量由 config 控制，默认 50）

支持指数：000001(上证指数), 000016(上证50), 000300(沪深300),
000905(中证500), 399001(深证成指), 399006(创业板指)
"""

from __future__ import annotations

import json
import logging
import os
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)


def _safe_float(value: Any) -> float | None:
    """安全地将值转换为 float，转换失败返回 None。

    Args:
        value: 待转换的值，可能是数字、字符串、None、NaN 等

    Returns:
        float | None: 转换成功返回 float，否则返回 None
    """
    if value is None:
        return None
    try:
        result = float(value)
        import math
        if result != result or math.isinf(result):
            return None
        return result
    except (ValueError, TypeError):
        return None


def _code_to_baostock(code: str) -> str:
    """将纯数字指数代码转换为 baostock 格式（sh./sz. 前缀）。

    转换规则：
    - 首位数字 ≤ 2 且第二位 ≠ 9 → sh.前缀（上海交易所）
    - 首位数字 ≥ 3 → sz.前缀（深圳交易所）
    - 其他情况默认 sh.前缀

    Args:
        code: 纯数字指数代码，如 "000300"

    Returns:
        str: baostock 格式代码，如 "sh.000300"
    """
    if not code or len(code) < 2:
        return f"sh.{code}"

    try:
        first = int(code[0])
        second = int(code[1])
    except ValueError:
        return f"sh.{code}"

    if first <= 2 and second != 9:
        return f"sh.{code}"
    elif first >= 3:
        return f"sz.{code}"
    else:
        return f"sh.{code}"


class MarketDataSource(ABC):
    """市场数据源抽象基类，方便后续切换数据源（如 Wind、Tushare 等）。"""

    @abstractmethod
    def get_index_valuation(self, index_code: str, index_name: str) -> dict | None:
        """获取单个指数的 PE/PB 估值数据。

        Args:
            index_code: 指数代码（如 "000001"）
            index_name: 指数名称（如 "上证指数"）

        Returns:
            dict | None: 成功返回估值数据字典，失败返回 None。
            返回格式：{"name": str, "code": str, "pe": float|None,
                       "pe_percentile": float|None, "pb": float|None,
                       "pb_percentile": float|None}
        """
        ...


def _calc_median(values: list[float]) -> float:
    """计算列表的中位数。

    Args:
        values: 已排序或未排序的浮点数列表

    Returns:
        float: 中位数值，保留两位小数
    """
    if not values:
        return 0.0
    n = len(values)
    sorted_vals = sorted(values)
    if n % 2 == 1:
        result = sorted_vals[n // 2]
    else:
        result = (sorted_vals[n // 2 - 1] + sorted_vals[n // 2]) / 2
    return round(result, 2)


class BaostockDataSource(MarketDataSource):
    """通过 baostock（证券宝）获取 A 股指数估值数据。

    核心方案：基于成分股 PE/PB 中位数计算指数估值。
    - baostock 的 index K 线 PE/PB 字段全为 0，因此改为查询成分股个股数据
    - 使用 baostock 成分股 API（query_sz50_stocks / query_hs300_stocks / query_zz500_stocks）
    - PE/PB 分位使用指数收盘价在 5 年历史中的分位近似

    回退机制：当成分股方案不适用时，回退到原始指数 K 线查询方案。

    Attributes:
        config: 完整配置字典
    """

    # 指数代码 -> 成分股查询方法标识（用于 _get_index_constituents 路由）
    _CONSTITUENT_SOURCE_MAP: dict[str, str] = {
        "000016": "sz50",       # 上证50
        "000300": "hs300",      # 沪深300
        "000905": "zz500",      # 中证500
    }

    def __init__(self, config: dict):
        """初始化 baostock 数据源。

        Args:
            config: 完整配置字典
        """
        self.config = config

    # ------------------------------------------------------------------
    # 私有方法：成分股方案
    # ------------------------------------------------------------------

    def _get_constituent_sample_size(self) -> int:
        """从配置读取成分股采样数量。

        Returns:
            int: 采样数量，从 config.data_source.constituent_sample_size 读取，默认 50
        """
        return self.config.get("data_source", {}).get("constituent_sample_size", 50)

    def _get_index_constituents(self, index_code: str) -> list[str]:
        """获取指数成分股代码列表（baostock 格式：sh.xxx / sz.xxx）。

        路由规则：
        - 000016 → query_sz50_stocks()
        - 000300 → query_hs300_stocks()
        - 000905 → query_zz500_stocks()
        - 000001 → sz50 ∪ hs300（上证综指近似样本）
        - 399001 → (hs300 ∪ zz500) ∩ 深市个股（深证成指近似样本）
        - 399006 → zz500 ∩ sz.300*（创业板指近似样本）

        Args:
            index_code: 纯数字指数代码

        Returns:
            list[str]: 成分股代码列表，如 ["sh.600000", "sz.000001", ...]
        """
        import baostock as bs

        # 有直接 API 的指数
        if index_code in self._CONSTITUENT_SOURCE_MAP:
            source = self._CONSTITUENT_SOURCE_MAP[index_code]
            if source == "sz50":
                rs = bs.query_sz50_stocks()
            elif source == "hs300":
                rs = bs.query_hs300_stocks()
            elif source == "zz500":
                rs = bs.query_zz500_stocks()
            else:
                return []
            return self._parse_constituent_result(rs)

        # 上证综指：sz50 ∪ hs300
        if index_code == "000001":
            rs1 = bs.query_sz50_stocks()
            rs2 = bs.query_hs300_stocks()
            codes1 = set(self._parse_constituent_result(rs1))
            codes2 = set(self._parse_constituent_result(rs2))
            return list(codes1 | codes2)

        # 深证成指：(hs300 ∪ zz500) ∩ 深市个股
        if index_code == "399001":
            rs1 = bs.query_hs300_stocks()
            rs2 = bs.query_zz500_stocks()
            codes1 = set(self._parse_constituent_result(rs1))
            codes2 = set(self._parse_constituent_result(rs2))
            all_codes = codes1 | codes2
            return [c for c in all_codes if c.startswith("sz.")]

        # 创业板指：zz500 ∩ 创业板个股（sz.300 开头）
        if index_code == "399006":
            rs = bs.query_zz500_stocks()
            all_codes = self._parse_constituent_result(rs)
            return [c for c in all_codes if c.startswith("sz.300")]

        return []

    @staticmethod
    def _parse_constituent_result(rs: Any) -> list[str]:
        """解析 baostock 成分股查询结果，提取代码列表。

        baostock 成分股查询返回每行格式：(update_date, code, code_name)

        Args:
            rs: baostock 查询结果集

        Returns:
            list[str]: 成分股代码列表，如 ["sh.600036", "sz.000858"]
        """
        codes: list[str] = []
        if rs is None or rs.error_code != "0":
            logger.warning("成分股查询失败: %s", rs.error_msg if rs is not None else "返回为空")
            return codes

        while rs.next():
            row = rs.get_row_data()
            if len(row) >= 2 and row[1]:
                codes.append(row[1])
        return codes

    def _get_constituent_pe_pb(
        self, codes: list[str], lookback_days: int = 3
    ) -> tuple[list[float], list[float]]:
        """查询成分股个股的 PE/PB 数据。

        对每只股票查询最近 lookback_days 个交易日的 peTTM/pbMRQ，
        取最新有效值。过滤规则：PE > 0 且 PE < 1000，PB > 0。

        Args:
            codes: 成分股代码列表（baostock 格式 sh.xxx / sz.xxx）
            lookback_days: 向前查询的交易天数（默认 3）

        Returns:
            tuple[list[float], list[float]]: (有效PE列表, 有效PB列表)
        """
        import baostock as bs

        end_date = datetime.now().strftime("%Y-%m-%d")
        # 日期范围取 lookback_days * 3 天以覆盖非交易日
        start_date = (datetime.now() - timedelta(days=lookback_days * 3)).strftime("%Y-%m-%d")

        # 从配置读取 PE/PB 过滤阈值
        ds_config = self.config.get("data_source", {})
        pe_min = ds_config.get("pe_filter_min", 0)
        pe_max = ds_config.get("pe_filter_max", 1000)
        pb_min = ds_config.get("pb_filter_min", 0)

        pe_list: list[float] = []
        pb_list: list[float] = []
        success_count = 0

        for code in codes:
            try:
                rs = bs.query_history_k_data_plus(
                    code,
                    "date,peTTM,pbMRQ",
                    start_date=start_date,
                    end_date=end_date,
                    frequency="d",
                    adjustflag="3",
                )

                if rs.error_code != "0":
                    continue

                latest_pe: float | None = None
                latest_pb: float | None = None

                while rs.next():
                    row = rs.get_row_data()
                    pe_val = _safe_float(row[1]) if len(row) > 1 else None
                    pb_val = _safe_float(row[2]) if len(row) > 2 else None
                    if pe_val is not None:
                        latest_pe = pe_val
                    if pb_val is not None:
                        latest_pb = pb_val

                if latest_pe is not None and pe_min < latest_pe < pe_max:
                    pe_list.append(latest_pe)
                if latest_pb is not None and latest_pb > pb_min:
                    pb_list.append(latest_pb)

                if latest_pe is not None or latest_pb is not None:
                    success_count += 1

            except Exception:
                # 单只股票查询失败不中断整体流程
                continue

        logger.debug(
            "成分股 PE/PB 查询：%d/%d 只有效，PE 样本 %d，PB 样本 %d",
            success_count, len(codes), len(pe_list), len(pb_list),
        )
        return pe_list, pb_list

    def _get_index_close_and_percentile(
        self, index_code: str,
    ) -> tuple[float | None, float | None]:
        """获取指数最新收盘价及其在 5 年历史中的分位。

        一次查询同时获得最新收盘价和完整历史序列，避免重复查询。
        分位值 = (历史收盘价小于当前值的数量 / 历史收盘价总数) * 100。

        Args:
            index_code: 纯数字指数代码

        Returns:
            tuple[float | None, float | None]: (最新收盘价, 分位值0-100)
        """
        import baostock as bs

        bs_code = _code_to_baostock(index_code)
        end_date = datetime.now().strftime("%Y-%m-%d")

        # 从配置读取历史数据年限，默认 5 年
        years = self.config.get("data_source", {}).get("percentile_lookback_years", 5)
        start_date = (datetime.now() - timedelta(days=int(years * 365.25))).strftime("%Y-%m-%d")

        rs = bs.query_history_k_data_plus(
            bs_code,
            "date,close",
            start_date=start_date,
            end_date=end_date,
            frequency="d",
            adjustflag="3",
        )

        if rs.error_code != "0":
            logger.warning("查询 %s 收盘价历史失败: %s", index_code, rs.error_msg)
            return None, None

        close_values: list[float] = []
        while rs.next():
            row = rs.get_row_data()
            val = _safe_float(row[1]) if len(row) > 1 else None
            if val is not None and val > 0:
                close_values.append(val)

        if not close_values:
            return None, None

        latest_close = close_values[-1]

        if len(close_values) < 2:
            return latest_close, None

        count_less = sum(1 for v in close_values if v < latest_close)
        percentile = count_less / len(close_values) * 100

        return latest_close, round(percentile, 2)

    # ------------------------------------------------------------------
    # 回退方案：原始指数 K 线查询
    # ------------------------------------------------------------------

    def _fallback_index_kline(self, index_code: str, index_name: str) -> dict | None:
        """回退方案：直接查询指数 K 线数据中的 PE/PB 字段。

        当成分股方案不可用（如指数无成分股 API）时使用。

        Args:
            index_code: 纯数字指数代码
            index_name: 指数名称

        Returns:
            dict | None: 估值数据，失败返回 None
        """
        import baostock as bs

        bs_code = _code_to_baostock(index_code)
        end_date = datetime.now().strftime("%Y-%m-%d")
        start_date = self.config.get("data_source", {}).get("start_date", "2010-01-01")

        rs = bs.query_history_k_data_plus(
            bs_code,
            "date,peTTM,pbMRQ",
            start_date=start_date,
            end_date=end_date,
            frequency="d",
            adjustflag="3",
        )

        if rs.error_code != "0":
            logger.warning("baostock 查询 %s(%s) 失败: %s", index_name, bs_code, rs.error_msg)
            return None

        data_list: list[list[str]] = []
        while (rs.error_code == "0") and rs.next():
            data_list.append(rs.get_row_data())

        if not data_list:
            logger.warning("baostock 查询 %s(%s) 返回空数据", index_name, bs_code)
            return None

        df = pd.DataFrame(data_list, columns=["date", "peTTM", "pbMRQ"])
        latest = df.iloc[-1]
        pe_val = _safe_float(latest.get("peTTM"))
        pb_val = _safe_float(latest.get("pbMRQ"))

        df["peTTM"] = pd.to_numeric(df["peTTM"], errors="coerce")
        df["pbMRQ"] = pd.to_numeric(df["pbMRQ"], errors="coerce")

        pe_percentile = _calc_series_percentile(df, "peTTM", pe_val)
        pb_percentile = _calc_series_percentile(df, "pbMRQ", pb_val)

        logger.debug(
            "%s(%s): PE=%.2f, PE分位=%.1f%%, PB=%.2f, PB分位=%.1f%% (回退方案, %d 历史点)",
            index_name, index_code,
            pe_val or 0, pe_percentile or 0,
            pb_val or 0, pb_percentile or 0,
            len(df),
        )

        return {
            "name": index_name,
            "code": index_code,
            "pe": pe_val,
            "pb": pb_val,
            "pe_percentile": pe_percentile,
            "pb_percentile": pb_percentile,
        }

    # ------------------------------------------------------------------
    # 公开方法：指数估值主入口
    # ------------------------------------------------------------------

    def get_index_valuation(self, index_code: str, index_name: str) -> dict | None:
        """获取单个指数的 PE/PB 估值数据。

        主子方案：基于成分股 PE/PB 中位数计算指数估值。
        流程：
        1. 获取指数成分股列表
        2. 按代码字典序排序，取前 N 只（采样数量由配置控制）
        3. 查询每只成分股的最近 PE/PB，取中位数
        4. 使用指数收盘价在 5 年历史中的分位近似 PE/PB 估值分位
        5. 若成分股方案失败，回退到原始指数 K 线查询

        Args:
            index_code: 纯数字指数代码（如 "000300"）
            index_name: 指数名称（如 "沪深300"）

        Returns:
            dict | None: 估值数据字典，格式：
                {"name": str, "code": str, "pe": float|None,
                 "pe_percentile": float|None, "pb": float|None,
                 "pb_percentile": float|None}
        """
        import baostock as bs

        # 登录 baostock
        lg = bs.login()
        if lg.error_code != "0":
            logger.error("baostock 登录失败: %s", lg.error_msg)
            return None

        try:
            # 1. 尝试成分股方案
            codes = self._get_index_constituents(index_code)
            if not codes:
                logger.info(
                    "%s(%s): 无成分股 API，使用回退方案（指数 K 线查询）",
                    index_name, index_code,
                )
                return self._fallback_index_kline(index_code, index_name)

            logger.info("%s(%s): 获取到 %d 只成分股", index_name, index_code, len(codes))

            # 2. 采样成分股（按代码字典序排序，取前 N 只）
            sample_size = self._get_constituent_sample_size()
            codes.sort()
            sampled_codes = codes[:sample_size]
            logger.info(
                "%s(%s): 采样 %d/%d 只成分股",
                index_name, index_code, len(sampled_codes), len(codes),
            )

            # 3. 查询成分股 PE/PB
            pe_list, pb_list = self._get_constituent_pe_pb(sampled_codes)

            if not pe_list and not pb_list:
                logger.warning(
                    "%s(%s): 成分股 PE/PB 查询全部失败，使用回退方案",
                    index_name, index_code,
                )
                return self._fallback_index_kline(index_code, index_name)

            # 4. 计算 PE/PB 中位数
            pe_median = _calc_median(pe_list) if pe_list else None
            pb_median = _calc_median(pb_list) if pb_list else None

            # 5. 使用指数收盘价分位近似 PE/PB 估值分位
            _, close_percentile = self._get_index_close_and_percentile(index_code)
            pe_percentile = close_percentile
            pb_percentile = close_percentile

            logger.debug(
                "%s(%s): PE=%.2f (n=%d), PE分位=%.1f%%, PB=%.2f (n=%d), PB分位=%.1f%% (成分股方案)",
                index_name, index_code,
                pe_median or 0, len(pe_list), pe_percentile or 0,
                pb_median or 0, len(pb_list), pb_percentile or 0,
            )

            return {
                "name": index_name,
                "code": index_code,
                "pe": pe_median,
                "pb": pb_median,
                "pe_percentile": pe_percentile,
                "pb_percentile": pb_percentile,
            }

        finally:
            bs.logout()


def _calc_series_percentile(df, col: str, current_val: float) -> float | None:
    """根据历史序列计算当前值的百分位。

    百分位 = 历史值中小于当前值的比例 * 100，范围 0-100。

    Args:
        df: 历史数据 DataFrame
        col: 目标列名
        current_val: 当前值

    Returns:
        float | None: 百分位值 (0.0-100.0，保留两位小数)，无法计算返回 None
    """
    if current_val is None:
        return None
    if col not in df.columns:
        return None

    hist_values = df[col].dropna()
    if len(hist_values) < 2:
        return None

    # 过滤掉非数值
    hist_values = hist_values[hist_values.apply(
        lambda x: isinstance(x, (int, float)) and not (x != x)
    )]
    if len(hist_values) < 2:
        return None

    count_less = (hist_values < current_val).sum()
    percentile = count_less / len(hist_values) * 100
    return round(percentile, 2)


def validate_valuation(data: dict) -> tuple[bool, str]:
    """校验估值数据质量。

    校验规则：
    1. 必须包含 pe 字段且 PE > 0（指数成分股亏损时 PE 可能为负，此时标注无效）
    2. pe_percentile 必须在 [0, 100] 范围内
    3. pb_percentile 如果存在也需在 [0, 100] 范围内
    4. code 字段不能为空

    Args:
        data: 估值数据字典

    Returns:
        tuple[bool, str]: (是否通过校验, 原因描述)
    """
    # 1. 非空检查
    if not data:
        return False, "数据为空"

    code = data.get("code")
    if not code:
        return False, "指数代码缺失"

    # 2. PE 检查
    pe = data.get("pe")
    if pe is None:
        return False, f"{code}: PE 数据缺失"
    if pe <= 0:
        return False, f"{code}: PE = {pe}（非正，成分股可能整体亏损）"

    # 3. PE 分位检查
    pe_pct = data.get("pe_percentile")
    if pe_pct is not None:
        if pe_pct < 0 or pe_pct > 100:
            return False, f"{code}: PE 分位 = {pe_pct}%（超出 [0, 100] 范围）"

    # 4. PB 分位检查
    pb_pct = data.get("pb_percentile")
    if pb_pct is not None:
        if pb_pct < 0 or pb_pct > 100:
            return False, f"{code}: PB 分位 = {pb_pct}%（超出 [0, 100] 范围）"

    return True, f"{data.get('name', code)} 数据校验通过"


def _read_cache(cache_path: str, config: dict) -> dict | None:
    """从本地 JSON 文件读取缓存的估值数据。

    缓存过期检查：当前时间 - 缓存时间戳 > ttl_hours 则视为过期。

    Args:
        cache_path: 缓存文件的绝对路径
        config: 配置字典（用于读取 ttl_hours）

    Returns:
        dict | None: 有效的缓存数据，缓存文件不存在/过期/解析失败返回 None
    """
    if not os.path.isfile(cache_path):
        logger.info("缓存文件不存在: %s", cache_path)
        return None

    try:
        with open(cache_path, "r", encoding="utf-8") as f:
            cache = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("缓存文件读取失败: %s", e)
        return None

    # 验证缓存结构
    if not isinstance(cache, dict) or "timestamp" not in cache or "indices" not in cache:
        logger.warning("缓存文件格式无效，已忽略")
        return None

    # 检查 TTL
    try:
        cache_time = datetime.fromisoformat(cache["timestamp"])
    except (ValueError, TypeError):
        logger.warning("缓存时间戳解析失败，已忽略")
        return None

    ttl_hours = config.get("cache", {}).get("ttl_hours", 6)
    now = datetime.now(timezone.utc)
    if cache_time.tzinfo is None:
        cache_time = cache_time.replace(tzinfo=timezone.utc)

    age = now - cache_time
    if age > timedelta(hours=ttl_hours):
        logger.info("缓存已过期（%s 前，TTL: %s 小时），将刷新数据",
                     _format_timedelta(age), ttl_hours)
        return None

    logger.info("缓存有效（%s 前），共 %d 条指数数据",
                _format_timedelta(age), len(cache.get("indices", [])))
    return cache


def _write_cache(cache_path: str, data: dict) -> None:
    """将估值数据写入本地 JSON 缓存文件。

    自动创建缓存文件所在的目录（如果不存在）。

    Args:
        cache_path: 缓存文件的绝对路径
        data: 待写入的缓存数据
    """
    try:
        cache_dir = os.path.dirname(cache_path)
        if cache_dir:
            os.makedirs(cache_dir, exist_ok=True)
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, default=str)
        logger.info("估值数据已缓存至: %s", cache_path)
    except OSError as e:
        logger.warning("缓存写入失败: %s", e)


def _format_timedelta(td: timedelta) -> str:
    """将 timedelta 格式化为人类可读字符串。

    Args:
        td: 时间差

    Returns:
        str: 格式化后的字符串，如 "3小时25分钟"
    """
    total_minutes = int(td.total_seconds() / 60)
    hours = total_minutes // 60
    minutes = total_minutes % 60
    if hours > 0:
        return f"{hours}小时{minutes}分钟"
    return f"{minutes}分钟"


def fetch_all_valuations(config: dict, force_refresh: bool = False) -> dict:
    """获取所有指数的估值数据（主入口）。

    降级链路：
    1. 检查缓存 -> 有效缓存直接返回（force_refresh=True 时跳过）
    2. 通过 baostock（证券宝）逐个获取实时数据
    3. 对每条数据做质量校验
    4. 写入缓存
    5. 返回结果

    Args:
        config: 完整配置字典
        force_refresh: 是否强制刷新，True 时跳过缓存

    Returns:
        dict: 估值数据聚合结果，格式：
            {
                "timestamp": "2026-05-22T10:00:00+08:00",
                "indices": [
                    {"name": "沪深300", "code": "000300", "pe": 15.2,
                     "pe_percentile": 55.0, "pb": 1.8, "pb_percentile": 40.0,
                     "valid": true, "valid_msg": "沪深300 数据校验通过"},
                    ...
                ],
                "valid_count": 4,
                "total_count": 6,
                "error": ""  # 仅在全部失败时有内容
            }
    """
    indices_config = config.get("indices", [])
    if not indices_config:
        logger.warning("配置中未找到 indices 列表")
        return _empty_result("配置中未定义任何指数")

    # 1. 检查缓存
    cache_path = config.get("cache", {}).get("path", "")
    if cache_path and not force_refresh:
        cached = _read_cache(cache_path, config)
        if cached is not None:
            # 确保缓存数据包含 valid 字段（向后兼容旧缓存格式）
            for idx in cached.get("indices", []):
                if "valid" not in idx:
                    is_valid, msg = validate_valuation(idx)
                    idx["valid"] = is_valid
                    idx["valid_msg"] = msg
            cached["valid_count"] = sum(
                1 for i in cached.get("indices", []) if i.get("valid"))
            cached["total_count"] = len(cached.get("indices", []))
            return cached

    # 2. 通过 baostock 获取实时数据
    logger.info("开始获取 %d 个指数的实时估值数据", len(indices_config))
    data_source = BaostockDataSource(config)

    indices_result: list[dict] = []
    for index_item in indices_config:
        code = index_item.get("code", "")
        name = index_item.get("name", code)
        if not code:
            logger.warning("跳过错失代码的指数项: %s", index_item)
            continue

        logger.debug("获取 %s(%s) 估值数据...", name, code)
        valuation = data_source.get_index_valuation(code, name)

        if valuation is None:
            # 数据源完全失败
            valuation = {
                "name": name,
                "code": code,
                "pe": None,
                "pe_percentile": None,
                "pb": None,
                "pb_percentile": None,
            }

        # 3. 质量校验
        is_valid, msg = validate_valuation(valuation)
        valuation["valid"] = is_valid
        valuation["valid_msg"] = msg
        if not is_valid:
            logger.warning("数据校验未通过: %s", msg)
        else:
            logger.debug("%s 数据获取成功", name)

        indices_result.append(valuation)

    # 4. 汇总结果
    valid_count = sum(1 for i in indices_result if i.get("valid"))
    total_count = len(indices_result)
    now = datetime.now(timezone.utc).astimezone()
    timestamp = now.isoformat()

    error_msg = ""
    if valid_count == 0 and total_count > 0:
        error_msg = "所有指数数据均获取失败或校验未通过"
        logger.error(error_msg)
    elif valid_count < total_count:
        logger.info("%d/%d 个指数数据有效", valid_count, total_count)
    else:
        logger.info("全部 %d 个指数数据获取成功", total_count)

    result = {
        "timestamp": timestamp,
        "indices": indices_result,
        "valid_count": valid_count,
        "total_count": total_count,
        "error": error_msg,
    }

    # 5. 写入缓存
    if cache_path and valid_count > 0:
        _write_cache(cache_path, result)

    return result


def _empty_result(error_msg: str) -> dict:
    """生成空的错误结果。

    Args:
        error_msg: 错误描述

    Returns:
        dict: 空结果字典
    """
    return {
        "timestamp": datetime.now(timezone.utc).astimezone().isoformat(),
        "indices": [],
        "valid_count": 0,
        "total_count": 0,
        "error": error_msg,
    }