#!/usr/bin/env python3
"""
每日 K 线数据更新脚本
获取当日所有股票的 K 线数据并更新到数据库
使用 BaostockSession 保持登录状态，大幅提升效率
"""

import os
import signal
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd
import yaml

from utils.logger import get_logger
from data.database import DatabaseManager
from data.fetcher import BaostockSession

logger = get_logger()


class BaostockRequestTimeout(RuntimeError):
    """单股请求超时后标记当前 Baostock 会话已不可安全复用。"""

    def __init__(self, symbol: str):
        super().__init__(f"{symbol} 获取超时，当前 Baostock 会话已废弃")
        self.symbol = symbol


# ==================== 配置加载 ====================

def _load_kline_update_config(config_path: str = 'config/config.yaml') -> dict:
    """加载 config.yaml 中的 global.kline_update 节。

    所有日更新熔断/完整性参数必须从配置读取，禁止硬编码。
    当配置缺失时回退到与原脚本一致的默认值，保证向后兼容。

    Args:
        config_path: 配置文件路径

    Returns:
        dict: kline_update 配置字典
    """
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            full = yaml.safe_load(f) or {}
        cfg = (full.get('global') or {}).get('kline_update') or {}
    except Exception as e:
        logger.warning(f"加载 config/config.yaml 失败，使用默认值：{e}")
        cfg = {}

    # 默认值与原脚本硬编码保持一致，确保缺失配置时行为不变
    return {
        # C-1：days_to_update 从 config 读取，禁止硬编码
        'days_to_update': cfg.get('days_to_update', 5),
        'request_timeout_seconds': cfg.get('request_timeout_seconds', 15),
        'failure_rate_threshold': cfg.get('failure_rate_threshold', 0.7),
        'failure_window_size': cfg.get('failure_window_size', 100),
        'failure_window_min_samples': cfg.get('failure_window_min_samples', 50),
        # 兜底值与 config/config.yaml 默认值保持一致（配置管理设计.md §8.4：不得凭空另设数字）
        'total_failure_limit': cfg.get('total_failure_limit', 400),
        'min_success_rate': cfg.get('min_success_rate', 0.9),
        'min_coverage_rate': cfg.get('min_coverage_rate', 0.98),
        # M-2：限流间隔（请求后休眠时间，防止触发 Baostock API 限流）
        'request_interval_seconds': cfg.get('request_interval_seconds', 0.05),
        # P1：目标交易日回溯窗口（替代原硬编码 max_backtrack=7）
        'target_date_max_backtrack': cfg.get('target_date_max_backtrack', 7),
        # B2：休市日 guard 阈值（替代原硬编码 0.95）
        'skipped_ratio_guard': cfg.get('skipped_ratio_guard', 0.95),
    }


# ==================== UpdateResult ====================

@dataclass
class UpdateResult:
    """K 线更新结果数据类（R13 + R-01 完整覆盖率改造 + B2 非交易日语义修复）。

    替代原脚本 `update_all_klines()` 的 None 返回值，
    让 scheduler 和下游可精确判定「数据完整性」。
    """
    success: int = 0
    """成功更新的股票数（有新数据写入）"""
    failed: int = 0
    """失败的股票数（超时/异常）"""
    skipped: int = 0
    """跳过的股票数（已有最新数据或无新数据）"""
    total: int = 0
    """股票池总数（熔断后未遍历的股票也计入分母）"""
    latest_trade_date: Optional[str] = None
    """数据库中最新交易日期（YYYY-MM-DD），用于完整性校验"""
    target_date: Optional[str] = None
    """本次任务的目标交易日（最近一个 ≤ 当前时间的交易日）"""
    target_date_success: int = 0
    """target_date 当天数据库中已有/本次写入 K 线数据的股票数"""
    is_trading_day_degraded: Optional[bool] = None
    """is_trading_day 是否走了 weekday 降级推断（Baostock 不可用）。
    None 表示未做推断或结果不确定；True=降级推断，False=Baostock 验证过。
    仅供运维观测与交接记录，判定层不消费。"""
    target_date_inferred_non_trading: bool = False
    """结合 degraded_flag + 库内数据推断出 target_date 大概率非交易日。
    仅供运维观测与交接记录，判定层不消费（判定层严格走 4 步顺序，无此字段短路）。"""

    @property
    def processed_count(self) -> int:
        """实际处理过的股票数（success + failed + skipped）。

        熔断后未遍历的股票不计入此值，但其影响体现在 coverage_rate 的分母中。
        """
        return self.success + self.failed + self.skipped

    @property
    def coverage_rate(self) -> float:
        """处理覆盖率 = processed_count / total；total <= 0 返回 0.0"""
        if self.total <= 0:
            return 0.0
        return self.processed_count / self.total

    @property
    def target_date_coverage_rate(self) -> float:
        """目标交易日覆盖率 = target_date_success / total；total <= 0 返回 0.0"""
        if self.total <= 0:
            return 0.0
        return self.target_date_success / self.total

    @property
    def total_processed(self) -> int:
        """参与成功/失败计数的股票数（用于计算成功率）。

        注意：此属性与 processed_count 语义不同——processed_count 含 skipped，
        而 total_processed 供 success_rate 使用，**不得合并**。
        """
        return self.success + self.failed

    @property
    def success_rate(self) -> float:
        """成功率 = 成功 / (成功 + 失败)；分母为 0 时返回 1.0"""
        if self.total_processed == 0:
            return 1.0
        return self.success / self.total_processed

    @property
    def skipped_ratio(self) -> float:
        """跳过股票占比 = skipped / total；total 为 0 时返回 0"""
        if self.total == 0:
            return 0.0
        return self.skipped / self.total

    def is_complete(
        self,
        min_success_rate: float,
        total_failure_limit: int,
        min_coverage_rate: float,
        target_date: Optional[str] = None,
        skipped_ratio_guard: float = 0.95,
    ) -> bool:
        """判定本次更新是否完整（严格对齐第 9.2 节 4 条顺序）。

        判定顺序（禁止调换，后一条不得被前一条短路绕过）：
            1. total<=0 或 coverage_rate<min_coverage_rate → False（硬红线）
            2. target_date 为交易日且 target_date_coverage_rate<min_coverage_rate → False
               （必须在休市日 guard 之前，防止 skipped_ratio guard 短路绕过）
            3. 仅当 target_date 非交易日 且 skipped_ratio>=skipped_ratio_guard → True（休市日 guard）
            4. 其余按 success_rate>=min_success_rate 且 failed<=total_failure_limit

        禁止硬编码阈值：skipped_ratio_guard 必须由调用方从 config 传入。

        Args:
            min_success_rate: 成功率红线（0.0 - 1.0）
            total_failure_limit: 累计失败硬上限
            min_coverage_rate: 处理覆盖率红线（同时用于 target_date 覆盖率）
            target_date: 本次任务的目标交易日（YYYY-MM-DD 字符串）
            skipped_ratio_guard: 休市日 guard 阈值（从 config.yaml 读取，禁止硬编码 0.95）

        Returns:
            bool: True=数据完整，下游可放行；False=应阻断后续扫描
        """
        # 第 1 条：硬红线 —— total<=0 或 coverage 不足必须 FAIL
        if self.total <= 0 or self.coverage_rate < min_coverage_rate:
            return False

        # 第 2 条：target_date 为交易日 → target_date_coverage_rate 硬红线
        # （必须在 skipped_ratio guard 之前，禁止被其短路绕过）
        if target_date:
            try:
                from utils.trade_calendar import is_trading_day
                is_tr_day = is_trading_day(target_date)
            except Exception:
                is_tr_day = False
            if is_tr_day and self.target_date_coverage_rate < min_coverage_rate:
                return False

        # 第 3 条：仅当 target_date 非交易日，允许 skipped_ratio guard 放行
        # （交易日绝不能被 skipped_ratio 短路放行，必须走第 2/4 条）
        if target_date:
            try:
                from utils.trade_calendar import is_trading_day
                is_tr_day = is_trading_day(target_date)
            except Exception:
                is_tr_day = False
            # 非交易日 + skipped_ratio >= skipped_ratio_guard → PASS
            if not is_tr_day and self.skipped_ratio >= skipped_ratio_guard:
                return True

        # 第 4 条：正常交易日成功率 + 失败数
        return (
            self.success_rate >= min_success_rate
            and self.failed <= total_failure_limit
        )


class DailyKlineUpdater:
    """每日 K 线数据更新器"""

    def __init__(self, days_to_update: int = 5, config: Optional[dict] = None):
        """
        初始化

        Args:
            days_to_update: 更新最近多少天的数据（防止遗漏）
            config: global.kline_update 配置字典（R13 新增），None 时自动加载 config.yaml
        """
        self.days_to_update = days_to_update
        # R13 新增：从配置读取所有熔断/完整性参数，禁止硬编码
        self.cfg = config or _load_kline_update_config()
        self.db = None

        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

    def _signal_handler(self, signum, frame) -> None:
        """处理中断信号"""
        logger.info("\n收到中断信号，正在退出...")
        if self.db:
            self.db.close()
        # 同 main()：避免 concurrent.futures 的 atexit 等待 Baostock 挂起线程而卡住退出
        os._exit(0)

    def _fetch_with_timeout(
        self, session: BaostockSession, symbol: str, days: int = 120,
        timeout: Optional[int] = None,
    ):
        """
        带超时的 K 线获取，防止底层 C 扩展库无限挂起

        Args:
            session: Baostock 会话
            symbol: 股票代码
            days: 获取天数
            timeout: 超时秒数（R13 新增：默认从 config 读取 request_timeout_seconds）

        Returns:
            K 线 DataFrame，超时或异常时返回 None
        """
        if timeout is None:
            timeout = self.cfg.get('request_timeout_seconds', 15)

        executor = ThreadPoolExecutor(max_workers=1)
        future = executor.submit(session.get_kline, symbol, None, days)
        try:
            return future.result(timeout=timeout)
        except FuturesTimeoutError:
            logger.error(
                f"{symbol} 获取超时（>{timeout}秒）；底层线程无法安全中断，"
                "立即废弃当前会话并终止本轮，等待回补任务接管"
            )
            raise BaostockRequestTimeout(symbol)
        finally:
            executor.shutdown(wait=False)

    @staticmethod
    def _parse_latest_date(latest_data, today_date):
        """
        统一解析数据库返回的 latest_kline_date（R11 + R13 共用）。

        数据库可能返回 datetime、date、str 三种类型；
        本函数消除重复解析代码，让首次更新与重试分支统一处理。

        Args:
            latest_data: 数据库返回的最新日期（可能是 datetime/date/str）
            today_date: datetime.now().date()，解析失败时的兜底值

        Returns:
            date: 解析后的 Python date 对象
        """
        from datetime import datetime as dt_class
        if latest_data is None:
            return today_date
        if isinstance(latest_data, dt_class):
            return latest_data.date()
        if isinstance(latest_data, str):
            try:
                return dt_class.strptime(latest_data[:10], '%Y-%m-%d').date()
            except (ValueError, TypeError):
                return today_date
        if hasattr(latest_data, 'date'):
            return latest_data.date()
        # date 对象或其他不可识别类型
        return latest_data if hasattr(latest_data, 'day') else today_date

    # -------------------- 内部辅助：统一 latest_date 到 datetime --------------------

    @staticmethod
    def _latest_to_datetime(latest_data, fallback_date):
        """
        将数据库 latest_kline_date 统一转为 datetime（凌晨零点）。

        与 _parse_latest_date 类似，但返回的是 datetime 而非 date，
        用于和 kline_df['date'] 做 pd.to_datetime 比较。

        Args:
            latest_data: 数据库返回的最新日期
            fallback_date: 兜底 date

        Returns:
            datetime: 统一后的 datetime（凌晨 00:00:00）
        """
        from datetime import datetime as dt_class
        if latest_data is None:
            return dt_class.combine(fallback_date, dt_class.min.time())
        if isinstance(latest_data, dt_class):
            return latest_data
        if isinstance(latest_data, str):
            try:
                return dt_class.strptime(latest_data[:10], '%Y-%m-%d')
            except (ValueError, TypeError):
                return dt_class.combine(fallback_date, dt_class.min.time())
        parsed = DailyKlineUpdater._parse_latest_date(latest_data, fallback_date)
        return dt_class.combine(parsed, dt_class.min.time())

    @staticmethod
    def _compute_target_date(today_date, max_backtrack: int = 7):
        """计算目标交易日 = 最近一个 ≤ today_date 的交易日。

        从 today_date 往回逐日检查，直到命中交易日。
        R-2 修复：用 is_trading_day_with_confidence 可靠获取 degraded_flag，
        不再靠 try/except（is_trading_day 永不抛异常，原 degraded 恒为 False）。

        Returns:
            tuple[str | None, bool]: (目标交易日, degraded_flag)
                degraded_flag=True  → weekday 启发式降级（Baostock 不可用）
                degraded_flag=False → Baostock 已确认
        """
        from utils.trade_calendar import is_trading_day_with_confidence

        degraded = False
        for offset in range(max_backtrack):
            candidate = today_date - timedelta(days=offset)
            candidate_str = candidate.strftime("%Y-%m-%d")
            is_tr, degraded = is_trading_day_with_confidence(candidate_str)
            if is_tr:
                return candidate_str, degraded

        # 全回溯无结果：返回 None + 最后一次的 degraded 状态
        return None, degraded

    def update_all_klines(self) -> UpdateResult:
        """更新所有股票的 K 线数据（R13：返回 UpdateResult，禁止硬编码）。"""
        # 容错：绕过 __init__ 的实例（如 reproduce.py 用 __new__ 构造）
        # 自动加载 config，保证行为一致
        if not hasattr(self, 'cfg') or self.cfg is None:
            self.cfg = _load_kline_update_config()

        logger.info("=" * 80)
        logger.info("每日 K 线数据更新开始")
        logger.info("=" * 80)
        logger.info(f"更新日期范围：最近 {self.days_to_update} 天")
        logger.info(
            f"熔断配置：窗口大小 {self.cfg['failure_window_size']}，"
            f"熔断阈值 {self.cfg['failure_rate_threshold']:.0%}，"
            f"最小样本 {self.cfg['failure_window_min_samples']}，"
            f"累计失败硬上限 {self.cfg['total_failure_limit']}"
        )
        logger.info("=" * 80)

        self.db = DatabaseManager()

        # C-4：从 config.yaml 读取股票列表文件路径，禁止硬编码
        # 与 daily_scan.py 的读取方式保持一致（global.stock_list_file）
        stocks_file = ''
        try:
            with open('config/config.yaml', 'r', encoding='utf-8') as f:
                _full_cfg = yaml.safe_load(f) or {}
            stocks_file = (_full_cfg.get('global') or {}).get('stock_list_file', '')
        except Exception:
            logger.warning("加载 config.yaml 读取 stock_list_file 失败，使用数据库动态列表")
            stocks_file = ''

        if stocks_file and os.path.exists(stocks_file):
            logger.info(f"使用固定股票列表：{stocks_file}")
            stocks_df = pd.read_csv(stocks_file, dtype={'code': str})
            logger.info(f"沪深主板股票总数：{len(stocks_df)} 只")
        else:
            stocks_df = self.db.get_stock_list()
            if stocks_df is None or getattr(stocks_df, 'empty', False):
                logger.error("股票列表为空：get_stock_list 返回 None 或空 DataFrame")
                # P1：直接返回空结果，total=0 → is_complete 硬红线自动 FAIL，
                # 不会让判定层误放行空股票池
                self.db.close()
                return UpdateResult(total=0)
            logger.info(f"使用动态股票列表：{len(stocks_df)} 只")

        total = len(stocks_df)
        logger.info(f"总股票数：{total} 只")

        # 非交易日守卫（AC-2 红线修复：调用方没守卫时这里兜底）。
        # 在 stocks_df 加载完拿到 total 后检查，保证返回的 PASS 语义 UpdateResult
        # 有正确的 total 让 skipped_ratio 和 coverage_rate 满足 is_complete 红线。
        # scheduler_kline 已有守卫（should_skip_kline_update），不会进入此路径；
        # main.py run_kline_update 未来也会加守卫；这里是双保险兜底。
        from utils.trade_calendar import is_trading_day
        today_str = datetime.now().strftime("%Y-%m-%d")
        try:
            if not is_trading_day(today_str):
                logger.info(
                    f"今日 {today_str} 非交易日（is_trading_day=False），"
                    f"跳过 K 线采集循环，直接返回 PASS 语义结果"
                )
                self.db.close()
                return UpdateResult(
                    success=0,
                    failed=0,
                    skipped=total,
                    total=total,
                    target_date_inferred_non_trading=True,
                )
        except Exception as e:
            logger.warning(f"非交易日守卫 is_trading_day 异常：{e}，保守继续执行采集")

        today = datetime.now()
        start_date = today - timedelta(days=self.days_to_update + 10)
        today_date = today.date()

        # 计算目标交易日 = 最近一个 ≤ 今天的交易日（R-01 第二支柱 + B2 语义修复）
        td_max_backtrack = self.cfg.get('target_date_max_backtrack', 7)
        target_date, is_td_degraded = self._compute_target_date(
            today_date, max_backtrack=td_max_backtrack,
        )
        if target_date is None:
            logger.warning(
                f"最多回溯 {td_max_backtrack} 天仍未找到有效交易日，"
                f"target_date=None（trade_calendar 可能完全不可用）"
            )
        logger.info(
            f"目标交易日：{target_date}（今日 {today_date}，"
            f"is_trading_day 可信度：{'weekday 降级' if is_td_degraded else 'Baostock 已验证'}）"
        )

        # B2：推断 target_date 是否大概率非交易日
        # 规则：is_trading_day 走了 weekday 降级 且 库内 target_date 无数据 → 视为非交易日
        target_date_inferred_non_trading = False
        if target_date and is_td_degraded:
            try:
                df_check = pd.read_sql_query(
                    "SELECT COUNT(*) as cnt FROM klines WHERE date = %s",
                    self.db.conn, params=[target_date],
                )
                cnt = int(df_check['cnt'].iloc[0]) if not df_check.empty else 0
                if cnt == 0:
                    logger.info(
                        f"B2 推断：target_date={target_date} 无库内数据 且 "
                        f"is_trading_day 已降级 → 视作非交易日"
                    )
                    target_date_inferred_non_trading = True
            except Exception as e:
                logger.warning(f"B2 推断库内数据失败：{e}，保守起见不设置 inferred_non_trading")

        logger.info(
            f"更新日期：{start_date.strftime('%Y-%m-%d')} "
            f"至 {today.strftime('%Y-%m-%d')}"
        )
        logger.info("=" * 80)

        # R13：从配置读取所有熔断/完整性参数，禁止硬编码
        window_size = self.cfg['failure_window_size']
        rate_threshold = self.cfg['failure_rate_threshold']
        min_samples = self.cfg['failure_window_min_samples']
        total_limit = self.cfg['total_failure_limit']

        success = 0
        failed = 0
        skipped = 0
        # 收集失败股票，用于后续重试
        failed_stocks = []  # (code, symbol) 列表
        session_unhealthy = False
        # 滑动窗口失败率检测：窗口大小和阈值从配置读取
        recent_results = deque(maxlen=window_size)  # 1=失败, 0=成功

        with BaostockSession() as session:
            logger.info("Baostock 会话已建立，开始批量获取...")

            for idx, row in stocks_df.iterrows():
                code = row['code']
                symbol = row['symbol']

                latest_data = self.db.get_latest_kline_date(code)
                latest_date_obj = self._parse_latest_date(latest_data, today_date)

                if latest_date_obj == today_date:
                    skipped += 1
                    if (idx + 1) % 200 == 0:
                        logger.info(
                            f"进度：{idx + 1}/{total} | "
                            f"成功：{success} | 失败：{failed} | 跳过：{skipped}"
                        )
                    continue

                try:
                    # R13：不传 timeout，让 _fetch_with_timeout 走 cfg 默认值
                    kline_df = self._fetch_with_timeout(session, symbol, days=120)

                    if kline_df is not None and len(kline_df) > 0:
                        # 只保留最新日期之后的新数据
                        if latest_data is not None:
                            latest_dt = self._latest_to_datetime(latest_data, today_date)
                            kline_df = kline_df[
                                pd.to_datetime(kline_df['date']) > latest_dt
                            ]

                        if len(kline_df) > 0:
                            self.db.save_kline_history(code, kline_df)
                            success += 1
                            recent_results.append(0)  # 成功
                            logger.debug(
                                f"{code} 更新成功：{len(kline_df)} 条新数据"
                            )
                        else:
                            skipped += 1
                            recent_results.append(0)  # 无新数据不算失败
                            logger.debug(f"{code} 无新数据")
                    else:
                        failed += 1
                        recent_results.append(1)  # 失败
                        failed_stocks.append((code, symbol))
                        logger.warning(f"{code} 更新失败：返回空数据")

                except BaostockRequestTimeout as e:
                    failed += 1
                    recent_results.append(1)
                    session_unhealthy = True
                    logger.error(f"{e}；禁止继续复用损坏会话")
                    break
                except Exception as e:
                    failed += 1
                    recent_results.append(1)  # 异常=失败
                    failed_stocks.append((code, symbol))
                    logger.error(f"{code} 更新失败：{e}")

                # 滑动窗口失败率检测（配置驱动）
                if len(recent_results) >= min_samples:
                    failure_rate = sum(recent_results) / len(recent_results)
                    if failure_rate > rate_threshold:
                        logger.warning(
                            f"最近 {len(recent_results)} 次请求失败率 {failure_rate:.0%}，"
                            f"超过阈值 {rate_threshold:.0%}，Baostock 疑似故障，提前终止"
                        )
                        break

                # 累计失败硬上限（配置驱动）
                if failed >= total_limit:
                    logger.warning(
                        f"累计失败 {failed} 次，达到硬上限 {total_limit}，提前终止"
                    )
                    break

                if (idx + 1) % 200 == 0:
                    failure_rate_str = ""
                    if len(recent_results) > 0:
                        fr = sum(recent_results) / len(recent_results)
                        failure_rate_str = f" | 窗口失败率：{fr:.0%}"
                    logger.info(
                        f"进度：{idx + 1}/{total} | "
                        f"成功：{success} | 失败：{failed} | 跳过：{skipped}"
                        f"{failure_rate_str}"
                    )

                # M-2：每次请求后短暂休眠，限流间隔从 config 读取（默认 0.05s，必须与 config.yaml global.kline_update.request_interval_seconds 同步）
                time.sleep(self.cfg['request_interval_seconds'])

        # ================================================================
        # 方案 B：熔断后补抓未遍历股票（2026-10-20 新增）
        # 主循环被 break 时（滑动窗口熔断 或 累计失败硬上限），
        # 补抓未遍历的剩余股票。补抓阶段不检查熔断条件（已熔断过一次），
        # 单次尝试，成功计入 success 提升覆盖率；失败只记日志，让次日 backfill 兜底。
        # 配合方案 A 调高 total_failure_limit 到 400 双重保险
        # ================================================================
        normal_complete = (idx >= total - 1)
        if not session_unhealthy and not normal_complete and total > 0:
            remaining_count = total - 1 - idx
            if remaining_count > 0:
                logger.warning(
                    f"主循环在 {idx + 1}/{total} 处被熔断，补抓剩余 {remaining_count} 只未遍历股票..."
                )
                catchup_success = 0
                catchup_fail = 0
                catchup_skip = 0
                # 补抓用新的 Baostock 会话（原会话可能因熔断时的解压错误处于不稳定状态）
                with BaostockSession() as catchup_session:
                    for cidx in range(idx + 1, total):
                        crow = stocks_df.iloc[cidx]
                        code = crow['code']
                        symbol = crow['symbol']

                        latest_data = self.db.get_latest_kline_date(code)
                        latest_date_obj = self._parse_latest_date(latest_data, today_date)

                        if latest_date_obj == today_date:
                            catchup_skip += 1
                            skipped += 1
                            continue

                        try:
                            kline_df = self._fetch_with_timeout(
                                catchup_session, symbol, days=120
                            )

                            if kline_df is not None and len(kline_df) > 0:
                                if latest_data is not None:
                                    latest_dt = self._latest_to_datetime(latest_data, today_date)
                                    kline_df = kline_df[
                                        pd.to_datetime(kline_df['date']) > latest_dt
                                    ]

                                if len(kline_df) > 0:
                                    self.db.save_kline_history(code, kline_df)
                                    catchup_success += 1
                                    success += 1
                                else:
                                    catchup_skip += 1
                                    skipped += 1
                            else:
                                catchup_fail += 1
                                failed += 1

                        except BaostockRequestTimeout as e:
                            catchup_fail += 1
                            failed += 1
                            session_unhealthy = True
                            logger.error(f"补抓阶段 {e}；终止补抓并废弃会话")
                            break
                        except Exception as e:
                            catchup_fail += 1
                            failed += 1
                            logger.debug(f"补抓 {code} 异常：{e}")

                        # 限流间隔从 config 读取，与主循环一致（禁止硬编码）
                        time.sleep(self.cfg['request_interval_seconds'])

                logger.info(
                    f"补抓结果：成功 {catchup_success}，失败 {catchup_fail}，跳过 {catchup_skip}"
                )

        logger.info("=" * 80)
        logger.info("每日 K 线数据更新完成")
        logger.info(f"成功更新：{success} 只")
        logger.info(f"失败：{failed} 只")
        logger.info(f"跳过（已有最新数据）：{skipped} 只")
        logger.info("=" * 80)

        # 对失败的股票进行重试（Baostock 间歇性故障，重试一次通常能恢复）
        if failed_stocks and not session_unhealthy:
            logger.info(f"开始重试 {len(failed_stocks)} 只失败股票...")
            retry_success = 0
            retry_fail = 0
            # 重试使用新的 Baostock 会话（原会话可能因故障处于不稳定状态）
            with BaostockSession() as retry_session:
                for code, symbol in failed_stocks:
                    try:
                        # R13：同样不传 timeout
                        kline_df = self._fetch_with_timeout(
                            retry_session, symbol, days=120
                        )
                        if kline_df is not None and len(kline_df) > 0:
                            latest_data = self.db.get_latest_kline_date(code)
                            latest_dt = self._latest_to_datetime(latest_data, today_date)
                            kline_df = kline_df[
                                pd.to_datetime(kline_df['date']) > latest_dt
                            ]
                            if len(kline_df) > 0:
                                self.db.save_kline_history(code, kline_df)
                                retry_success += 1
                                success += 1
                                failed -= 1
                                logger.info(f"重试成功：{code}")
                                continue
                        retry_fail += 1
                    except BaostockRequestTimeout as e:
                        retry_fail += 1
                        failed += 1
                        session_unhealthy = True
                        logger.error(f"重试阶段 {e}；终止重试并废弃会话")
                        break
                    except Exception as e:
                        retry_fail += 1
                        logger.debug(f"重试失败：{code} - {e}")
                    # M-2：重试循环同样使用配置驱动的限流间隔
                    time.sleep(self.cfg['request_interval_seconds'])

            logger.info(f"重试结果：成功 {retry_success} 只，失败 {retry_fail} 只")
            logger.info(f"最终统计：成功更新 {success} 只，失败 {failed} 只")

        # R13：采集数据库最新交易日，用于完整性校验
        latest_trade_date = None
        try:
            df_latest = pd.read_sql_query(
                "SELECT MAX(date) as latest_date FROM klines", self.db.conn
            )
            if not df_latest.empty and df_latest['latest_date'].iloc[0] is not None:
                latest_trade_date = str(df_latest['latest_date'].iloc[0])[:10]
        except Exception as e:
            logger.warning(f"查询最新交易日失败：{e}")

        # R-01：统计 target_date 当天数据库中有数据的股票数（复用本地 DB 查询，非远端请求）
        target_date_success = 0
        try:
            df_td = pd.read_sql_query(
                "SELECT COUNT(DISTINCT code) as cnt FROM klines WHERE date = %s",
                self.db.conn,
                params=[target_date],
            )
            if not df_td.empty:
                target_date_success = int(df_td['cnt'].iloc[0])
        except Exception as e:
            logger.warning(f"查询目标交易日 {target_date} 覆盖数失败：{e}")

        coverage_pct = (target_date_success / total * 100) if total > 0 else 0
        logger.info(
            f"目标交易日 {target_date} 覆盖：{target_date_success}/{total}（{coverage_pct:.2f}%）"
        )

        self._verify_database()
        self.db.close()

        # R13 + R-01 + B2：返回 UpdateResult，携带完整覆盖率指标 + 非交易日推断标记
        return UpdateResult(
            success=success,
            failed=failed,
            skipped=skipped,
            total=total,
            latest_trade_date=latest_trade_date,
            target_date=target_date,
            target_date_success=target_date_success,
            is_trading_day_degraded=is_td_degraded,
            target_date_inferred_non_trading=target_date_inferred_non_trading,
        )

    def _verify_database(self) -> None:
        """验证数据库中的 K 线数据"""
        df = pd.read_sql_query(
            """
            SELECT
                COUNT(*) as total_records,
                COUNT(DISTINCT code) as stocks_with_data,
                MIN(date) as earliest_date,
                MAX(date) as latest_date
            FROM klines
            """,
            self.db.conn
        )

        logger.info("\n数据库验证结果:")
        logger.info(f"总记录数：{df['total_records'].iloc[0]:,}")
        logger.info(f"有数据的股票：{df['stocks_with_data'].iloc[0]} 只")
        logger.info(f"最早日期：{df['earliest_date'].iloc[0]}")
        logger.info(f"最新日期：{df['latest_date'].iloc[0]}")

        today = datetime.now().date()
        df_today = pd.read_sql_query(
            """
            SELECT COUNT(DISTINCT code) as count
            FROM klines
            WHERE date = %s
            """,
            self.db.conn, params=[today]
        )

        count = df_today['count'].iloc[0]
        if count > 0:
            logger.info(f"今日 ({today}) 已有 {count} 只股票的数据")
        else:
            logger.warning(f"今日 ({today}) 暂无数据")


def run_update(days: Optional[int] = None, cfg: Optional[dict] = None) -> UpdateResult:
    """业务入口：可正常 return UpdateResult，不抛 SystemExit（C-1）。

    供 main.py --all 流水线调用，完整跑完更新逻辑后正常返回，
    不中断后续的扫描/推送步骤。CLI 入口 main() 保留退出码语义，
    二者职责分离。

    Args:
        days: 更新最近多少天的数据。None 时从 config.yaml global.kline_update.days_to_update 读取。
        cfg: H-1：外部注入的 kline_update 配置字典，用于统一加载路径。
             None 时内部自动加载 config.yaml。

    Returns:
        UpdateResult: 更新结果（含完整性判定）。
    """
    # H-1：支持外部 cfg 注入，保证与调用方（main.py）读取同一份配置
    effective_cfg = cfg or _load_kline_update_config()
    # C-1：days 从 config 读取，禁止硬编码
    effective_days = days if days is not None else effective_cfg['days_to_update']

    updater = DailyKlineUpdater(days_to_update=effective_days, config=effective_cfg)
    result = updater.update_all_klines()

    # 完整性判定日志（从 updater.cfg 单点读取，避免双源漂移）
    complete = result.is_complete(
        updater.cfg['min_success_rate'],
        updater.cfg['total_failure_limit'],
        updater.cfg['min_coverage_rate'],
        target_date=result.target_date,
        skipped_ratio_guard=updater.cfg['skipped_ratio_guard'],
    )
    coverage_pct = result.coverage_rate * 100
    td_pct = result.target_date_coverage_rate * 100
    verdict = "PASS（可放行后续扫描）" if complete else "FAIL（建议阻断后续扫描）"
    logger.info(
        f"更新完整性判定：处理 {result.processed_count}/{result.total}、"
        f"覆盖 {coverage_pct:.2f}%、target_date 覆盖 {td_pct:.2f}%、"
        f"success_rate={result.success_rate:.2f}、"
        f"skipped_ratio={result.skipped_ratio:.2f}、"
        f"failed={result.failed} → {verdict}"
    )

    # 业务入口只返回、不 sys.exit，保证调用方（main.py --all）能继续执行后续步骤
    return result


def main() -> None:
    """CLI 入口：解析参数后调 run_update，再按完整性判定设置退出码（C-1）。"""
    import argparse
    import json

    parser = argparse.ArgumentParser(description='每日 K 线数据更新脚本')
    parser.add_argument(
        '--days', type=int, default=None,
        help='更新最近多少天的数据（默认从 config.yaml global.kline_update.days_to_update 读取）'
    )
    parser.add_argument(
        '--result-json', type=str, default=None,
        help='非空时把 UpdateResult 序列化（JSON）写入该路径，供 scheduler 读取',
    )

    args = parser.parse_args()

    result = run_update(days=args.days)

    # CLI 入口保留退出码语义：完整性 PASS 则 exit 0，否则 exit 1
    # 兜底值（必须与 config.yaml 的 global.kline_update 节同步，禁止硬编码独立漂移）
    default_cfg = _load_kline_update_config()
    complete = result.is_complete(
        default_cfg['min_success_rate'],
        default_cfg['total_failure_limit'],
        default_cfg['min_coverage_rate'],
        target_date=result.target_date,
        skipped_ratio_guard=default_cfg['skipped_ratio_guard'],
    )

    # --result-json：供 scheduler_kline 子进程隔离模式读取 details
    if args.result_json:
        try:
            result_dict = {
                'success': result.success,
                'failed': result.failed,
                'skipped': result.skipped,
                'total': result.total,
                'processed_count': result.processed_count,
                'coverage_rate': round(result.coverage_rate, 6),
                'target_date': result.target_date,
                'target_date_success': result.target_date_success,
                'target_date_coverage_rate': round(result.target_date_coverage_rate, 6),
                'latest_trade_date': result.latest_trade_date,
                'success_rate': round(result.success_rate, 6),
                'skipped_ratio': round(result.skipped_ratio, 6),
                # B2：非交易日推断相关（供 scheduler / 下游诊断用）
                'is_trading_day_degraded': result.is_trading_day_degraded,
                'target_date_inferred_non_trading': result.target_date_inferred_non_trading,
            }
            with open(args.result_json, 'w', encoding='utf-8') as f:
                json.dump(result_dict, f, ensure_ascii=False, indent=2)
            logger.info(f"UpdateResult 已写入 {args.result_json}")
        except Exception as e:
            logger.error(f"写入 result-json 失败：{e}")

    # Baostock C 扩展可能残留无法终止的后台线程，concurrent.futures 的 atexit 会阻塞解释器退出，
    # 故用 os._exit 强制退出（与 quick_backfill.py 一致）
    os._exit(0 if complete else 1)


if __name__ == '__main__':
    main()
