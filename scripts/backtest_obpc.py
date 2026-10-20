#!/usr/bin/env python3
"""
OBPC 超跌反弹策略 V2.0 历史回测脚本

功能说明：
    1. 从 PostgreSQL 数据库获取所有主板股票的历史 K 线数据
    2. 对每只股票采用滑动窗口方式逐日调用 OversoldBounceStrategy.analyze()
    3. 记录所有产生的买入信号，并对每个信号模拟后续交易
    4. 统计回测结果：总信号数、胜率、平均收益、最高/最低收益等
    5. 输出详细统计并保存结果到 backtest_results/backtest_{timestamp}.json

用法：
    python scripts/backtest_obpc.py [--start 2020-01-01] [--end 2026-04-07]
                                    [--index-filter] [--max-stocks N]

设计要点：
    - 读取 config/config.yaml 获取策略参数，不重写策略逻辑
    - 滑动窗口回测：对每只股票从第 120 天开始逐日作为"当前日期"调用策略
    - 信号去重：同一股票 60 个交易日内只取第一个信号
    - 交易成本：佣金 0.025%（双向）、印花税 0.1%（卖出）、滑点 0.1%
    - 大盘过滤（可选）：检查沪深 300 是否在 20 日均线上方
"""

import sys
import os
import json
import argparse
from datetime import datetime
from dataclasses import dataclass, field, asdict, replace
from typing import Dict, List, Optional
from multiprocessing import Pool, cpu_count

# 将项目根目录加入 sys.path，确保能导入项目模块
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 加载环境变量
from dotenv import load_dotenv
load_dotenv()

import pandas as pd
import yaml

from utils.logger import get_logger
from data.database import DatabaseManager
from data.stock_list import filter_stock_list
from strategy.oversold_bounce.strategy import OversoldBounceStrategy
from strategy.oversold_bounce.risk_control import (
    RiskControlConfig,
    RiskController,
    MarketContext,
    MarketRegimeDecision,
    PreloadedMarketContextProvider,
    InMemoryMonthlyCounter,
    cap_by_monthly_quota,
    calculate_relative_strength,
)
from strategy.base import Signal
from collections import defaultdict

logger = get_logger()


# ==================== 配置数据类 ====================

@dataclass
class BacktestConfig:
    """回测配置数据类，封装所有回测相关参数

    所有交易参数默认值为 None，在 __post_init__ 里统一兜底，
    避免硬编码散落在 dataclass 字段上。生产环境通过 load_backtest_config()
    从 config.yaml 完整构造，__post_init__ 兜底仅用于直接实例化场景（如测试）。
    """

    # 回测时间区间
    start_date: str = "2020-01-01"
    end_date: str = "2026-04-07"

    # 滑动窗口参数
    window_size: int = 120
    """滑动窗口大小：策略需要的历史数据天数"""

    step_days: int = 1
    """滑动步长：每隔多少个交易日回测一次"""

    # 信号去重参数
    signal_cooldown_days: int = 60
    """同一股票信号冷却期（交易日），冷却期内不重复产生信号"""

    # 交易模拟参数（H：默认 None，__post_init__ 统一兜底，禁止字段级硬编码）
    trailing_stop_ratio: Optional[float] = None
    """移动止盈回撤比例"""

    hard_stop_loss: Optional[float] = None
    """硬止损比例"""

    stop_loss_ratio: Optional[float] = None
    """止损比例（支撑位 × 该比例）"""

    min_hold_days: int = 5
    """最短持仓天数"""

    max_hold_days: int = 30
    """最长持仓天数"""

    # 交易成本参数（H：默认 None，__post_init__ 统一兜底）
    commission: Optional[float] = None
    """佣金费率（双向）"""

    stamp_tax: Optional[float] = None
    """印花税费率（仅卖出）"""

    slippage: Optional[float] = None
    """滑点费率"""

    # 大盘过滤参数
    index_filter_enabled: bool = False
    """是否启用大盘过滤"""

    index_code: str = "000300.SH"
    """大盘指数代码"""

    index_ma_period: int = 20
    """大盘均线周期"""

    # 风控配置（新增）
    risk_control_config: Optional[RiskControlConfig] = None
    """风控配置对象，从 config.yaml 的策略参数构建"""

    # 股票池过滤配置
    stock_filter: Dict = field(default_factory=dict)
    """股票池过滤配置"""

    # 策略参数
    strategy_params: Dict = field(default_factory=dict)
    """策略参数（从 config.yaml 加载）"""

    # V2.0 预期指标（用于回测结果对比）
    expected_metrics: Dict = field(default_factory=dict)
    """预期指标字典（从 config.yaml 的 expected_metrics 加载）"""

    # 输出配置
    output_dir: str = "backtest_results"
    """结果输出目录"""

    max_stocks: int = 0
    """最大回测股票数（0=不限制，用于调试）"""

    # V2.1 新增：回测方案信息（用于报告 meta）
    scheme: Optional[int] = None
    """回测方案编号（1/2/3/4），None 表示未指定方案"""

    scheme_description: str = ""
    """回测方案描述（用于报告 meta）"""

    # CSV 数据源（可选，用于无 DB 环境下的本地回测）
    csv_data_dir: Optional[str] = None
    """本地 CSV 数据目录（含 stocks_mainboard.csv / klines_mainboard.csv / hs300_index.csv）；None 时走 DB 路径"""

    def __post_init__(self):
        """H：统一兜底交易参数，禁止字段级硬编码。

        当参数为 None 时使用与 config.yaml strategies.oversold_bounce.params 节一致的兜底值，
        保证直接实例化 BacktestConfig（如测试场景）也能正确运行。
        """
        # 兜底值与 config.yaml strategies.oversold_bounce.params 保持一致
        _fallbacks = {
            'trailing_stop_ratio': 0.08,
            'hard_stop_loss': 0.10,
            'stop_loss_ratio': 0.97,
            'commission': 0.00025,
            'stamp_tax': 0.001,
            'slippage': 0.001,
        }
        for _field, _val in _fallbacks.items():
            if getattr(self, _field) is None:
                setattr(self, _field, _val)


# ==================== V2.1 回测方案配置器 ====================

# 方案二、方案三使用的短周期均线斜率计算周期（MA20 斜率）
SLOPE_MA_PERIOD_SHORT = 20
# 方案四使用的长周期均线斜率计算周期（MA60 斜率）
SLOPE_MA_PERIOD_LONG = 60


class SchemeConfigurator:
    """
    V2.1 新增：回测方案配置器

    封装 6 个回测方案的配置覆盖逻辑，根据方案编号修改 RiskControlConfig。
    6 个方案通过配置组合实现，无需 6 套代码。

    方案定义：
        - 方案一：大盘 MACD DIF > 0（method='macd'）
        - 方案二：大盘收盘价 > MA20 且 MA20 斜率 > 0（ma_position + slope_ma20）
        - 方案三：方案一 + 方案二（MACD + MA20 斜率双重确认）
        - 方案四：大盘 MA60 向上（ma_position + slope_ma60，斜率替代位置）
        - 方案五：ma_position 硬过滤 + 市场评分调整（弱势缩小、极弱归零）
        - 方案六：不做大盘硬过滤，仅评分阈值 + 市场评分调整
    """

    # 方案定义表：方案编号 -> {描述, 配置覆盖项}
    SCHEME_DEFINITIONS = {
        1: {
            "description": "方案一：大盘 MACD DIF > 0",
            "config": {
                "index_filter_method": "macd",
                "slope_filter_enabled": False,
                "score_adjustment_enabled": False,
            },
        },
        2: {
            "description": "方案二：大盘收盘价 > MA20 且 MA20 斜率 > 0",
            "config": {
                "index_filter_method": "ma_position",
                "long_ma_filter_enabled": False,
                "slope_filter_enabled": True,
                "slope_ma_period": SLOPE_MA_PERIOD_SHORT,
                "score_adjustment_enabled": False,
            },
        },
        3: {
            "description": "方案三：方案一 + 方案二（MACD + MA20 斜率双重确认）",
            "config": {
                "index_filter_method": "macd",
                "slope_filter_enabled": True,
                "slope_ma_period": SLOPE_MA_PERIOD_SHORT,
                "score_adjustment_enabled": False,
            },
        },
        4: {
            "description": "方案四：大盘 MA60 向上（斜率替代位置）",
            "config": {
                "index_filter_method": "ma_position",
                "long_ma_filter_enabled": False,
                "slope_filter_enabled": True,
                "slope_ma_period": SLOPE_MA_PERIOD_LONG,
                "score_adjustment_enabled": False,
            },
        },
        5: {
            "description": "方案五：ma_position 硬过滤 + 市场评分调整（弱势缩小、极弱归零）",
            "config": {
                "index_filter_method": "ma_position",
                "slope_filter_enabled": False,
                "score_adjustment_enabled": True,
            },
        },
        6: {
            "description": "方案六：不做大盘硬过滤，仅评分阈值 + 市场评分调整",
            "config": {
                "index_filter_enabled": False,
                "slope_filter_enabled": False,
                "score_adjustment_enabled": True,
            },
        },
    }

    @classmethod
    def apply_scheme(
        cls, risk_config: RiskControlConfig, scheme: int
    ) -> tuple:
        """
        应用指定方案的配置覆盖

        使用 dataclasses.replace 创建配置副本，避免修改原始配置。

        Args:
            risk_config: 原始风控配置
            scheme: 方案编号（1/2/3/4）

        Returns:
            tuple: (覆盖后的配置, 方案描述)

        Raises:
            ValueError: 方案编号不支持时抛出
        """
        if scheme not in cls.SCHEME_DEFINITIONS:
            raise ValueError(
                f"不支持的方案编号：{scheme}，支持 1/2/3/4/5/6"
            )

        scheme_def = cls.SCHEME_DEFINITIONS[scheme]
        config_overrides = scheme_def["config"]

        # 创建配置副本，应用覆盖
        new_config = replace(risk_config, **config_overrides)

        return new_config, scheme_def["description"]

    @classmethod
    def is_v21_enabled(cls, risk_config: RiskControlConfig) -> bool:
        """
        判断是否启用了 V2.1 功能

        V2.1 功能包括：MACD 过滤、斜率过滤、评分调整。
        启用任一功能时返回 True。

        Args:
            risk_config: 风控配置

        Returns:
            bool: True 表示启用了 V2.1 功能
        """
        return (
            risk_config.index_filter_method == "macd"
            or risk_config.slope_filter_enabled
            or risk_config.score_adjustment_enabled
        )


# ==================== 交易结果数据类 ====================

@dataclass
class TradeResult:
    """单笔交易结果"""

    code: str
    """股票代码"""

    name: str = ""
    """股票名称"""

    signal_date: str = ""
    """信号日期（回踩确认日）"""

    entry_date: str = ""
    """买入日期（信号次日）"""

    entry_price: float = 0.0
    """买入价格（开盘价 + 滑点）"""

    exit_date: str = ""
    """卖出日期"""

    exit_price: float = 0.0
    """卖出价格"""

    exit_reason: str = ""
    """退出原因：trailing_stop/hard_stop/support_stop/max_hold/min_hold"""

    hold_days: int = 0
    """持仓天数"""

    raw_return: float = 0.0
    """原始收益率（不含成本）"""

    net_return: float = 0.0
    """净收益率（扣除成本）"""

    max_price: float = 0.0
    """持仓期间最高价"""

    support_level: float = 0.0
    """支撑位"""

    stop_loss_price: float = 0.0
    """止损价（支撑位 × stop_loss_ratio）"""

    score: float = 0.0
    """信号评分"""

    # V3 新增：分级市场裁决字段
    regime: str = ""
    """市场裁决状态：strong / weak / extreme_weak / unknown。由 decide_market_regime() 给出。"""

    position_multiplier: float = 1.0
    """仓位乘数，源自 MarketRegimeDecision.position_multiplier。默认 1.0（满仓），
    weak 市场通常为 0.5，extreme_weak 为 0.0。"""


# ==================== 配置加载 ====================

def load_backtest_config(
    config_path: str = "config/config.yaml",
    start_date: str = "2020-01-01",
    end_date: str = "2026-04-07",
    index_filter: bool = False,
    max_stocks: int = 0,
) -> BacktestConfig:
    """
    从 config.yaml 加载回测配置

    Args:
        config_path: 配置文件路径
        start_date: 回测起始日期
        end_date: 回测结束日期
        index_filter: 是否启用大盘过滤
        max_stocks: 最大回测股票数（0=不限制）

    Returns:
        BacktestConfig: 回测配置对象
    """
    with open(config_path, "r", encoding="utf-8") as f:
        full_config = yaml.safe_load(f)

    # 提取策略配置
    strategy_cfg = full_config.get("strategies", {}).get("oversold_bounce", {})
    params = strategy_cfg.get("params", {})

    # 提取 V2.0 预期指标配置（与 params 同级）
    expected_metrics = strategy_cfg.get("expected_metrics", {})

    # 提取股票池过滤配置
    stock_pool_cfg = full_config.get("global", {}).get("stock_pool", {})

    # 提取大盘过滤配置
    index_filter_cfg = params.get("index_filter", {})

    # 构建风控配置（新增）
    risk_control_config = RiskControlConfig.from_params(params)

    # 构建回测配置
    config = BacktestConfig(
        start_date=start_date,
        end_date=end_date,
        # 信号去重
        signal_cooldown_days=params.get("signal_cooldown_days", 60),
        # 交易模拟参数
        trailing_stop_ratio=params.get("trailing_stop_ratio", 0.08),
        hard_stop_loss=params.get("hard_stop_loss", 0.10),
        stop_loss_ratio=params.get("stop_loss_ratio", 0.97),
        min_hold_days=params.get("min_hold_days", 5),
        max_hold_days=params.get("max_hold_days", 30),
        # 交易成本
        commission=params.get("commission", 0.00025),
        stamp_tax=params.get("stamp_tax", 0.001),
        slippage=params.get("slippage", 0.001),
        # 大盘过滤
        index_filter_enabled=index_filter or index_filter_cfg.get("enabled", False),
        index_code=index_filter_cfg.get("index_code", "000300.SH"),
        index_ma_period=index_filter_cfg.get("index_ma_period", 20),
        # 风控配置（新增）
        risk_control_config=risk_control_config,
        # 股票池过滤
        stock_filter=stock_pool_cfg,
        # 策略参数
        strategy_params=params,
        # V2.0 预期指标
        expected_metrics=expected_metrics,
        # 调试参数
        max_stocks=max_stocks,
    )

    logger.info(f"回测配置加载完成：{start_date} ~ {end_date}")
    logger.info(f"大盘过滤：{'启用' if config.index_filter_enabled else '关闭'}")
    logger.info(f"信号冷却期：{config.signal_cooldown_days} 个交易日")
    logger.info(
        f"风控配置：大盘趋势过滤={'启用' if risk_control_config.index_filter_enabled else '关闭'}，"
        f"单月上限={risk_control_config.max_signals_per_month}，"
        f"评分阈值过滤={'启用' if risk_control_config.score_filter_enabled else '关闭'}"
    )
    return config


# ==================== 数据加载器 ====================

class BacktestDataLoader:
    """
    回测数据加载器

    支持两种数据源：
        - 默认：DatabaseManager（PostgreSQL），生产路径
        - CSV 分支：传入 csv_dir 后从本地 CSV 读（用于 DB 不可达时的本地回测）

    两种路径返回的 DataFrame 列契约必须完全一致，确保上层回测引擎无感知。
    """

    # CSV 文件名常量：CSV 数据源分支的固定文件名（本地回测专用，非业务参数）
    _CSV_STOCKS_FILE = "stocks_mainboard.csv"
    _CSV_KLINES_FILE = "klines_mainboard.csv"
    _CSV_INDEX_FILE = "hs300_index.csv"

    def __init__(
        self,
        config: BacktestConfig,
        db: Optional[DatabaseManager] = None,
        csv_dir: Optional[str] = None,
    ):
        """
        初始化数据加载器

        Args:
            config: 回测配置
            db: 数据库管理器（DB 路径必填，CSV 路径传 None）
            csv_dir: 本地 CSV 数据目录（CSV 路径必填，DB 路径传 None）

        Raises:
            ValueError: db 和 csv_dir 同时为 None 或同时有值时抛出
        """
        # 数据源互斥校验（二选一，避免歧义）
        if db is None and csv_dir is None and config.csv_data_dir is None:
            raise ValueError(
                "BacktestDataLoader 必须指定 db 或 csv_dir（或 config.csv_data_dir）之一"
            )
        if db is not None and csv_dir is not None:
            raise ValueError("BacktestDataLoader 只能使用一种数据源：db 或 csv_dir，不可同时指定")

        self.db = db
        # 显式传参优先；否则从 config.csv_data_dir 读取（CLI --data-dir 注入到 config）
        self.csv_dir = csv_dir or config.csv_data_dir
        self.config = config
        # K 线缓存：回测区间内所有股票的日线，首次 load_kline_data 时批量加载
        self._klines_cache: Optional[pd.DataFrame] = None

        if self.csv_dir:
            logger.info(f"数据加载器：CSV 模式，目录 = {self.csv_dir}")
        else:
            logger.info("数据加载器：DB 模式")

    # ==================== 股票列表 ====================

    def load_stock_list(self) -> pd.DataFrame:
        """
        加载主板股票列表

        DB 路径：db.get_stock_list() 后复用项目标准 filter_stock_list 过滤
        CSV 路径：读 stocks_mainboard.csv 后同样复用 filter_stock_list 过滤

        Returns:
            pd.DataFrame: 股票列表，包含 code, name 列（过滤后）
        """
        if self.csv_dir is not None:
            df = self._load_stocks_from_csv()
        else:
            df = self.db.get_stock_list()

        if df is None or df.empty:
            logger.error("无股票列表数据")
            return pd.DataFrame()

        # 应用项目标准的股票池过滤（CSV/DB 共用同一过滤管线）
        filtered_df = filter_stock_list(df, self.config.stock_filter)
        logger.info(f"过滤后待回测股票数：{len(filtered_df)} 只")

        # 限制股票数量（调试用）
        if self.config.max_stocks > 0 and len(filtered_df) > self.config.max_stocks:
            filtered_df = filtered_df.head(self.config.max_stocks)
            logger.info(f"调试模式：限制回测股票数为 {self.config.max_stocks} 只")

        return filtered_df

    def _load_stocks_from_csv(self) -> pd.DataFrame:
        """CSV 路径加载股票列表（返回原始 DataFrame，后续统一过 filter_stock_list）"""
        import os
        path = os.path.join(self.csv_dir, self._CSV_STOCKS_FILE)
        if not os.path.exists(path):
            logger.error(f"CSV 股票列表文件不存在：{path}")
            return pd.DataFrame()
        df = pd.read_csv(path, dtype={"code": str})
        logger.info(f"CSV 股票列表：{len(df)} 行，列 = {list(df.columns)}")
        return df

    # ==================== K 线缓存 ====================

    def _ensure_klines_cache(self) -> None:
        """懒加载 K 线缓存：一次性批量拉取回测区间内所有股票的日线

        CSV 路径：读 klines_mainboard.csv 后按 config.start_date/end_date 过滤
        DB 路径：原有 pd.read_sql_query 批量查询

        两种路径都保证缓存 DataFrame 的列契约为 [code, date, open, high, low, close, volume, amount]
        且 date 为 datetime64，排序为 (code, date) ASC。
        """
        if self._klines_cache is not None:
            return

        if self.csv_dir is not None:
            self._ensure_klines_cache_csv()
        else:
            self._ensure_klines_cache_db()

    def _ensure_klines_cache_csv(self) -> None:
        """CSV 路径批量加载 K 线缓存"""
        import os
        path = os.path.join(self.csv_dir, self._CSV_KLINES_FILE)
        if not os.path.exists(path):
            logger.error(f"CSV K 线文件不存在：{path}")
            self._klines_cache = pd.DataFrame()
            return

        logger.info(
            f"CSV 批量加载回测区间 K 线：{self.config.start_date} ~ {self.config.end_date}"
        )
        df = pd.read_csv(path, dtype={"code": str})
        df["date"] = pd.to_datetime(df["date"])

        # 按回测时间区间过滤
        start_ts = pd.to_datetime(self.config.start_date)
        end_ts = pd.to_datetime(self.config.end_date)
        df = df[(df["date"] >= start_ts) & (df["date"] <= end_ts)]

        if df.empty:
            logger.warning(f"CSV K 线按 {self.config.start_date} ~ {self.config.end_date} 过滤后为空")
            self._klines_cache = pd.DataFrame()
            return

        df = df.sort_values(["code", "date"]).reset_index(drop=True)
        self._klines_cache = df
        logger.info(
            f"CSV K 线缓存加载完成：{len(df)} 行，"
            f"{df['code'].nunique()} 只股票，"
            f"日期范围 {df['date'].min().date()} ~ {df['date'].max().date()}"
        )

    def _ensure_klines_cache_db(self) -> None:
        """DB 路径批量加载 K 线缓存（原有逻辑，不变）"""
        query = """
            SELECT code, date, open, high, low, close, volume, amount
            FROM klines
            WHERE frequency = 'd'
              AND date >= %s AND date <= %s
            ORDER BY code, date ASC
        """
        logger.info(
            f"批量加载回测区间内所有 K 线："
            f"{self.config.start_date} ~ {self.config.end_date}，首次查询 DB..."
        )
        try:
            df = pd.read_sql_query(
                query, self.db.conn,
                params=(self.config.start_date, self.config.end_date)
            )
            df["date"] = pd.to_datetime(df["date"])
            df = df.sort_values(["code", "date"]).reset_index(drop=True)
            self._klines_cache = df
            logger.info(
                f"K 线缓存加载完成：{len(df)} 行，"
                f"{df['code'].nunique()} 只股票，"
                f"日期范围 {df['date'].min().date()} ~ {df['date'].max().date()}"
            )
        except Exception as e:
            logger.warning(f"批量加载 K 线缓存失败：{e}，回退到逐股票查询")
            self._klines_cache = pd.DataFrame()

    def load_kline_data(self, code: str) -> Optional[pd.DataFrame]:
        """
        加载单只股票的完整 K 线数据（回测区间内）

        CSV 路径：只读缓存，无逐股票 SQL 回退（CSV 全量加载已包含全部股票）
        DB 路径：优先缓存，缓存不可用时回退到逐股票 SQL 查询

        Returns:
            Optional[pd.DataFrame]: K 线数据，包含 date, open, high, low, close, volume, amount（已去掉 code 列，与 DB 路径一致）
        """
        self._ensure_klines_cache()
        if self._klines_cache is not None and not self._klines_cache.empty:
            df = self._klines_cache[self._klines_cache["code"] == code].copy()
            if df.empty:
                return None
            df = df.drop(columns=["code"]).reset_index(drop=True)
            return df

        # 回退路径：仅 DB 模式下可用的逐股票 SQL 查询
        if self.csv_dir is not None:
            # CSV 模式下缓存为空即意味着该 code 不在数据集中，不再尝试其它路径
            return None

        query = """
            SELECT date, open, high, low, close, volume, amount
            FROM klines
            WHERE code = %s AND frequency = 'd'
              AND date >= %s AND date <= %s
            ORDER BY date ASC
        """
        try:
            df = pd.read_sql_query(
                query, self.db.conn,
                params=(code, self.config.start_date, self.config.end_date)
            )
            if df.empty:
                return None
            df["date"] = pd.to_datetime(df["date"])
            df = df.sort_values("date").reset_index(drop=True)
            return df
        except Exception as e:
            logger.warning(f"加载 {code} K 线数据失败：{e}")
            return None

    # ==================== 指数数据 ====================

    def load_index_data(self) -> Optional[pd.DataFrame]:
        """
        加载大盘指数数据（用于大盘过滤）

        CSV 路径：读 hs300_index.csv，按 start_date/end_date 过滤
        DB 路径：原有 pd.read_sql_query

        Returns:
            Optional[pd.DataFrame]: 指数 K 线数据，包含 date, open, high, low, close, volume, amount（与 DB 路径列契约完全一致）
        """
        if self.csv_dir is not None:
            return self._load_index_from_csv()

        # DB 路径（原有逻辑，不变）
        # 将 000300.SH 转为数据库中的代码格式
        index_code = self.config.index_code.replace(".SH", "").replace(".SZ", "")
        query = """
            SELECT date, open, high, low, close, volume, amount
            FROM klines
            WHERE code = %s AND frequency = 'd'
              AND date >= %s AND date <= %s
            ORDER BY date ASC
        """
        try:
            df = pd.read_sql_query(
                query, self.db.conn,
                params=(index_code, self.config.start_date, self.config.end_date)
            )
            if df.empty:
                logger.warning(f"未找到指数 {self.config.index_code} 的数据，大盘过滤将失效")
                return None
            df["date"] = pd.to_datetime(df["date"])
            df = df.sort_values("date").reset_index(drop=True)
            logger.info(f"加载指数 {self.config.index_code} 数据：{len(df)} 条")
            return df
        except Exception as e:
            logger.warning(f"加载指数数据失败：{e}")
            return None

    def _load_index_from_csv(self) -> Optional[pd.DataFrame]:
        """CSV 路径加载沪深 300 指数数据

        CSV 列：code,frequency,date,open,high,low,close,volume,amount
        DB 路径返回列：date,open,high,low,close,volume,amount
        所以这里读完后去掉 code 和 frequency 列，保持与 DB 路径列契约一致。
        """
        import os
        path = os.path.join(self.csv_dir, self._CSV_INDEX_FILE)
        if not os.path.exists(path):
            logger.error(f"CSV 指数文件不存在：{path}")
            return None

        df = pd.read_csv(path)
        df["date"] = pd.to_datetime(df["date"])

        # 按回测时间区间过滤
        start_ts = pd.to_datetime(self.config.start_date)
        end_ts = pd.to_datetime(self.config.end_date)
        df = df[(df["date"] >= start_ts) & (df["date"] <= end_ts)]

        if df.empty:
            logger.warning(
                f"CSV 指数按 {self.config.start_date} ~ {self.config.end_date} 过滤后为空，"
                f"大盘过滤将失效"
            )
            return None

        # 去掉 CSV 独有的 code / frequency 列，保持与 DB 路径返回的列契约完全一致
        df = df.drop(columns=["code", "frequency"], errors="ignore")
        df = df.sort_values("date").reset_index(drop=True)
        logger.info(f"CSV 指数 {self.config.index_code} 数据：{len(df)} 条")
        return df

    def build_index_ma_filter(self, index_df: Optional[pd.DataFrame]) -> Dict:
        """
        构建大盘均线过滤字典：日期 -> 是否在均线上方

        Args:
            index_df: 指数 K 线数据

        Returns:
            Dict: {日期字符串: bool}，True 表示大盘在均线上方（允许买入）
        """
        if index_df is None or index_df.empty:
            return {}

        df = index_df.copy()
        ma_period = self.config.index_ma_period
        df["ma"] = df["close"].rolling(window=ma_period).mean()
        df["above_ma"] = df["close"] >= df["ma"]

        # 构建日期到过滤结果的映射
        filter_map = {}
        for _, row in df.iterrows():
            date_str = row["date"].strftime("%Y-%m-%d")
            filter_map[date_str] = bool(row["above_ma"])

        above_count = sum(1 for v in filter_map.values() if v)
        logger.info(
            f"大盘过滤构建完成：{len(filter_map)} 个交易日，"
            f"其中 {above_count} 天在 {ma_period} 日均线上方"
        )
        return filter_map

    def build_market_context_provider(
        self, index_df: Optional[pd.DataFrame]
    ) -> Optional[PreloadedMarketContextProvider]:
        """
        构建预加载大盘上下文提供者（用于风控控制器）

        一次性加载全部指数数据，预计算所有日期的上下文，
        构建日期到上下文的映射字典，查询时 O(1)。

        Args:
            index_df: 完整的指数 K 线数据（回测区间内）

        Returns:
            Optional[PreloadedMarketContextProvider]: 大盘上下文提供者，数据为空时返回 None
        """
        if index_df is None or index_df.empty:
            logger.warning("指数数据为空，无法构建大盘上下文提供者")
            return None

        if self.config.risk_control_config is None:
            logger.warning("风控配置为空，无法构建大盘上下文提供者")
            return None

        provider = PreloadedMarketContextProvider(
            index_df, self.config.risk_control_config
        )
        return provider


# ==================== 交易模拟器 ====================

class TradeSimulator:
    """
    交易模拟器

    根据策略信号模拟实际交易，包含买入、止损、止盈、移动止盈等逻辑
    """

    def __init__(self, config: BacktestConfig):
        """
        初始化交易模拟器

        Args:
            config: 回测配置
        """
        self.config = config

    def simulate(
        self,
        signal: Signal,
        kline_df: pd.DataFrame,
        stock_name: str = "",
        position_multiplier: float = 1.0,
        regime: str = "",
    ) -> Optional[TradeResult]:
        """
        模拟单笔交易

        交易规则：
            - 买入：信号确认后次日开盘价买入（加滑点）
            - 止损：跌破支撑位 × stop_loss_ratio 止损
            - 移动止盈：从持仓最高价回撤 trailing_stop_ratio 卖出
            - 硬止损：亏损达到 hard_stop_loss 卖出
            - 最短持仓 min_hold_days 天，最长持仓 max_hold_days 天

        Args:
            signal: 策略信号
            kline_df: 完整 K 线数据
            stock_name: 股票名称
            position_multiplier: V3 新增，仓位乘数（0~1.0），
                源自 MarketRegimeDecision.position_multiplier。
                weak 市场通常 0.5（半仓），strong 市场 1.0（满仓）。
                乘数乘以收益贡献，不改变进出价格和成本比例。
            regime: V3 新增，市场裁决状态，用于 TradeResult 输出。

        Returns:
            Optional[TradeResult]: 交易结果，若无法买入则返回 None
        """
        detail = signal.detail
        support_level = detail.get("support_level", 0)
        if support_level <= 0:
            return None

        # 定位信号日在 K 线中的位置
        signal_date = signal.signal_date
        signal_idx = kline_df.index[
            kline_df["date"] == pd.to_datetime(signal_date)
        ].tolist()
        if not signal_idx:
            logger.debug(f"{signal.code} 信号日 {signal_date} 不在 K 线数据中")
            return None
        signal_idx = signal_idx[0]

        # 次日买入
        entry_idx = signal_idx + 1
        if entry_idx >= len(kline_df):
            logger.debug(f"{signal.code} 信号日 {signal_date} 后无次日数据，无法买入")
            return None

        entry_row = kline_df.iloc[entry_idx]
        entry_price = entry_row["open"] * (1 + self.config.slippage)
        if entry_price <= 0:
            return None

        # 计算止损价
        stop_loss_price = support_level * self.config.stop_loss_ratio

        # 遍历持仓期间，检查退出条件
        max_price = entry_price
        exit_idx = -1
        exit_price = 0.0
        exit_reason = ""

        # 持仓区间：从买入日到最长持仓日
        hold_end_idx = min(entry_idx + self.config.max_hold_days, len(kline_df) - 1)

        for i in range(entry_idx, hold_end_idx + 1):
            row = kline_df.iloc[i]
            high = row["high"]
            low = row["low"]
            close = row["close"]

            # 更新持仓最高价
            if high > max_price:
                max_price = high

            hold_days = i - entry_idx

            # 最短持仓期内不卖出（除非触发硬止损）
            if hold_days < self.config.min_hold_days:
                # 最短持仓期内仅检查硬止损
                hard_stop_price = entry_price * (1 - self.config.hard_stop_loss)
                if low <= hard_stop_price:
                    # R07：跳空低开越过止损线 → 按开盘价成交（硬止损价可能高于当日 open）
                    exit_price = min(hard_stop_price, float(row["open"]))
                    exit_idx = i
                    exit_reason = "hard_stop"
                    break
                continue

            # 检查退出条件（优先级从高到低）

            # 1. 硬止损：亏损达到 hard_stop_loss
            hard_stop_price = entry_price * (1 - self.config.hard_stop_loss)
            if low <= hard_stop_price:
                # R07：跳空低开越过止损线 → 按开盘价成交
                exit_price = min(hard_stop_price, float(row["open"]))
                exit_idx = i
                exit_reason = "hard_stop"
                break

            # 2. 支撑位止损：跌破支撑位 × stop_loss_ratio
            if low <= stop_loss_price:
                # R07：跳空低开越过止损线 → 按开盘价成交
                exit_price = min(stop_loss_price, float(row["open"]))
                exit_idx = i
                exit_reason = "support_stop"
                break

            # 3. 移动止盈：从最高价回撤 trailing_stop_ratio
            trailing_stop_price = max_price * (1 - self.config.trailing_stop_ratio)
            if low <= trailing_stop_price:
                # R07：移动止盈线同样受跳空影响
                exit_price = min(trailing_stop_price, float(row["open"]))
                exit_idx = i
                exit_reason = "trailing_stop"
                break

            # 4. 最长持仓到期
            if hold_days >= self.config.max_hold_days:
                exit_idx = i
                exit_price = close
                exit_reason = "max_hold"
                break

        # 如果没有触发任何退出条件，到最后一天收盘卖出
        if exit_idx == -1:
            exit_idx = hold_end_idx
            exit_price = kline_df.iloc[exit_idx]["close"]
            exit_reason = "max_hold"

        # 计算收益率
        # 卖出价格扣除滑点
        actual_exit_price = exit_price * (1 - self.config.slippage)

        # 原始收益率（不含成本）：entry 与 exit 的价格差 / entry
        raw_return = (actual_exit_price - entry_price) / entry_price

        # 净收益率（扣除佣金和印花税）
        # 买入成本：佣金
        buy_cost = entry_price * self.config.commission
        # 卖出成本：佣金 + 印花税
        sell_cost = actual_exit_price * (self.config.commission + self.config.stamp_tax)
        # 净收益 = (卖出价 - 买入价) - 买入佣金 - 卖出佣金 - 印花税
        net_profit = (actual_exit_price - entry_price) - buy_cost - sell_cost
        net_return = net_profit / entry_price

        # V3 新增：position_multiplier 真正进入收益计算
        # 弱半仓 → 收益贡献 = full_return × 0.5（同时缩小成本的相对比例影响）
        # 这里直接对净收益率和原始收益率应用乘数，让半仓的收益/亏损比例都是满仓的一半
        # 这是保守做法：成本按比例缩放（因为仓位更小，实际佣金和印花税也更少）
        if position_multiplier != 1.0 and position_multiplier > 0:
            raw_return = raw_return * position_multiplier
            # 成本也是按比例缩放的（半仓只承担一半成本），所以净收益也是满仓的一半
            net_return = net_return * position_multiplier
        elif position_multiplier <= 0:
            # 零仓位不应走到 simulate，但防御性处理：收益归零
            raw_return = 0.0
            net_return = 0.0

        hold_days = exit_idx - entry_idx

        result = TradeResult(
            code=signal.code,
            name=stock_name,
            signal_date=signal_date,
            entry_date=kline_df.iloc[entry_idx]["date"].strftime("%Y-%m-%d"),
            entry_price=round(entry_price, 4),
            exit_date=kline_df.iloc[exit_idx]["date"].strftime("%Y-%m-%d"),
            exit_price=round(actual_exit_price, 4),
            exit_reason=exit_reason,
            hold_days=hold_days,
            raw_return=round(raw_return, 6),
            net_return=round(net_return, 6),
            max_price=round(max_price, 4),
            support_level=round(support_level, 4),
            stop_loss_price=round(stop_loss_price, 4),
            score=signal.score,
            # V3 新增：分级裁决字段
            regime=regime,
            position_multiplier=position_multiplier,
        )

        return result


# ==================== 回测引擎 ====================

class BacktestEngine:
    """
    回测引擎

    对每只股票采用滑动窗口方式逐日调用策略分析，收集信号并模拟交易
    """

    def __init__(
        self,
        config: BacktestConfig,
        strategy: OversoldBounceStrategy,
        data_loader: BacktestDataLoader,
        trade_simulator: TradeSimulator,
    ):
        """
        初始化回测引擎

        Args:
            config: 回测配置
            strategy: 策略实例
            data_loader: 数据加载器
            trade_simulator: 交易模拟器
        """
        self.config = config
        self.strategy = strategy
        self.data_loader = data_loader
        self.trade_simulator = trade_simulator

        # 大盘过滤字典（保留兼容旧逻辑）
        self.index_filter_map: Dict[str, bool] = {}

        # 风控相关成员（新增）
        self.risk_controller: Optional[RiskController] = None
        """风控控制器，启用风控时由 run 方法初始化"""

        self.market_context_provider: Optional[PreloadedMarketContextProvider] = None
        """大盘上下文提供者，启用风控时由 run 方法初始化"""

        self.monthly_counter: Optional[InMemoryMonthlyCounter] = None
        """单月信号计数器，启用风控时由 run 方法初始化"""

        # V3 新增：分级市场裁决相关
        self._market_regime_distribution: Dict[str, int] = defaultdict(int)
        """回测期间每个交易日 MarketRegimeDecision 的分布统计。
        key: regime 字符串（strong/weak/extreme_weak/unknown），value: 出现天数。
        注意：按日期去重，一天的裁决只记一次（用 PreloadedProvider 逐日取上下文）。"""

        self._graded_decisions_cache: Dict[str, MarketRegimeDecision] = {}
        """信号日期 → MarketRegimeDecision 的缓存，避免同一日期重复调用 decide_market_regime。"""

        # 市场状态分布与股票无关（只依赖 PreloadedProvider 的日期序列），
        # 首次计算后置位，避免每只股票结束时重复遍历全部日期（O(股票数 × 天数)）
        self._market_regime_distribution_computed: bool = False

        # 统计计数器
        self.stats = {
            "total_stocks": 0,
            "skipped_no_data": 0,
            "skipped_short_data": 0,
            "total_signals": 0,
            "skipped_cooldown": 0,
            "skipped_index_filter": 0,
            # V2.0 风控统计
            "skipped_long_ma_filter": 0,    # 60日均线过滤
            "skipped_monthly_limit": 0,     # 单月上限过滤
            "skipped_score_filter": 0,      # 评分阈值过滤
            # V2.1 新增风控统计
            "skipped_macd_filter": 0,           # MACD 动量过滤
            "skipped_slope_filter": 0,          # 均线斜率过滤
            "skipped_score_adjustment": 0,      # 评分调整归零
            "total_trades": 0,
            "failed_trades": 0,
        }

    def run(self) -> List[TradeResult]:
        """
        执行完整回测

        Returns:
            List[TradeResult]: 所有交易结果列表
        """
        logger.info("=" * 70)
        logger.info("开始执行 OBPC V2.1 历史回测")
        logger.info("=" * 70)

        # 加载股票列表
        stock_df = self.data_loader.load_stock_list()
        if stock_df.empty:
            logger.error("无可用股票，回测终止")
            return []

        self.stats["total_stocks"] = len(stock_df)

        # 初始化风控控制器（新增）
        if self.config.risk_control_config is not None:
            self.risk_controller = RiskController(self.config.risk_control_config)
            self.risk_controller.reset_stats()
            self.monthly_counter = InMemoryMonthlyCounter()
            logger.info("风控控制器已初始化（三层风控：评分阈值 + 大盘趋势 + 单月上限）")

        # 加载大盘过滤数据
        if self.config.index_filter_enabled:
            index_df = self.data_loader.load_index_data()
            if index_df is None or index_df.empty:
                logger.warning(
                    "大盘指数数据不存在，自动关闭大盘过滤"
                )
                self.config.index_filter_enabled = False
            else:
                self.index_filter_map = self.data_loader.build_index_ma_filter(index_df)
                if not self.index_filter_map:
                    logger.warning("大盘过滤字典为空，自动关闭大盘过滤")
                    self.config.index_filter_enabled = False

                # 构建大盘上下文提供者（用于风控控制器）
                if self.risk_controller is not None:
                    self.market_context_provider = (
                        self.data_loader.build_market_context_provider(index_df)
                    )
                    if self.market_context_provider is None:
                        logger.warning("大盘上下文提供者构建失败，风控的大盘趋势过滤将失效")

        # V3（P2-5 修复）：市场状态分布只依赖指数日期序列、与个股无关，
        # 因此在 run() 初始化市场上下文后独立计算一次；不得再依赖某只股票走完整流程。
        self._compute_market_regime_distribution()

        # 阶段一：逐股票收集候选信号（只做前两层风控，不传 monthly_count）
        all_candidates: List[Dict] = []

        for idx, row in stock_df.iterrows():
            code = row["code"]
            name = row.get("name", code)

            if (idx + 1) % 100 == 0:
                logger.info(
                    f"回测进度：{idx + 1}/{len(stock_df)}，"
                    f"已收集 {len(all_candidates)} 个候选信号"
                )

            candidates = self._backtest_single_stock(code, name)
            all_candidates.extend(candidates)

        # 阶段二：全局排序 + 按月分组 + 统一月度配额（R21 修复）
        # 原逻辑在逐股票遍历中 increment monthly counter，导致结果依赖股票遍历顺序
        # 改后：全部候选按 (signal_date, -score) 排序，同日期内评分降序、跨日期升序
        #       再按月份分组，每月内用 cap_by_monthly_quota 统一分配名额
        all_candidates.sort(key=lambda c: (c['signal_date'], -c['score']))

        # V3 新增：每日名额截断（弱市 daily_signal_limit 限制每日最多通过的信号数）
        # 在月度配额之前执行，因为弱市的每日名额更严格
        all_candidates = self._apply_daily_signal_limit(all_candidates)

        # 从风控配置读取配额参数（风控未启用时跳过月度配额，全部候选入选）
        max_per_month = 0
        max_daily = 0
        if self.risk_controller is not None and self.config.risk_control_config is not None:
            max_per_month = self.config.risk_control_config.max_signals_per_month
            max_daily = self.config.risk_control_config.max_daily_signals

        # 按月份分组
        by_month: Dict[str, List[Dict]] = defaultdict(list)
        for c in all_candidates:
            by_month[c['signal_date'][:7]].append(c)

        # 每月内统一配额分配 + 入选候选模拟交易
        all_trades: List[TradeResult] = []
        for ym in sorted(by_month.keys()):
            month_cands = by_month[ym]
            if self.risk_controller is not None:
                # 风控启用：用 cap_by_monthly_quota 统一截断（复用实盘同名纯函数）
                selected = cap_by_monthly_quota(month_cands, 0, max_per_month, max_daily)
                # 截断数量计入 skipped_monthly_limit 统计
                self.stats["skipped_monthly_limit"] += len(month_cands) - len(selected)
            else:
                # 风控未启用：全部候选入选
                selected = month_cands

            for cand in selected:
                # 只有入选的才 increment monthly counter
                if self.monthly_counter is not None:
                    self.monthly_counter.increment(ym)

                # 模拟交易（V3：传入分级裁决字段）
                trade = self.trade_simulator.simulate(
                    cand['signal'],
                    cand['df'],
                    cand['name'],
                    position_multiplier=cand.get('position_multiplier', 1.0),
                    regime=cand.get('regime', ''),
                )
                if trade is None:
                    self.stats["failed_trades"] += 1
                    continue

                all_trades.append(trade)
                # V3：日志中附加仓位乘数和 regime
                regime_tag = f"[{trade.regime}]" if trade.regime else ""
                mult_tag = (f"×{trade.position_multiplier}") if (
                    trade.position_multiplier != 1.0
                ) else ""
                logger.info(
                    f"{cand['code']} {cand['name']} {cand['signal_date']} {regime_tag}{mult_tag}，"
                    f"买入 {trade.entry_date}@{trade.entry_price}，"
                    f"卖出 {trade.exit_date}@{trade.exit_price}，"
                    f"收益 {trade.net_return*100:.2f}% ({trade.exit_reason})"
                )

        self.stats["total_trades"] = len(all_trades)
        logger.info("=" * 70)
        logger.info("回测完成")
        logger.info(f"总股票数：{self.stats['total_stocks']}")
        logger.info(f"无数据跳过：{self.stats['skipped_no_data']}")
        logger.info(f"数据不足跳过：{self.stats['skipped_short_data']}")
        logger.info(f"总信号数：{self.stats['total_signals']}")
        logger.info(f"冷却期跳过：{self.stats['skipped_cooldown']}")
        logger.info(f"大盘过滤跳过：{self.stats['skipped_index_filter']}")
        logger.info(f"60日均线过滤：{self.stats['skipped_long_ma_filter']}")
        logger.info(f"候选信号数：{len(all_candidates)}（前两层风控通过）")
        logger.info(f"单月上限截断：{self.stats['skipped_monthly_limit']}")
        logger.info(f"评分阈值过滤：{self.stats['skipped_score_filter']}")
        # V2.1 新增统计日志（仅在 V2.1 功能启用时输出）
        risk_cfg = self.config.risk_control_config
        if risk_cfg is not None and SchemeConfigurator.is_v21_enabled(risk_cfg):
            logger.info(f"MACD过滤：{self.stats['skipped_macd_filter']}")
            logger.info(f"斜率过滤：{self.stats['skipped_slope_filter']}")
            logger.info(f"评分归零：{self.stats['skipped_score_adjustment']}")
        # V3 新增：分级市场裁决统计
        if self.risk_controller is not None and self.risk_controller.config.market_regime_mode == "graded":
            dist = dict(self._market_regime_distribution)
            logger.info(f"市场状态分布：strong={dist.get('strong', 0)}, weak={dist.get('weak', 0)}, "
                        f"extreme_weak={dist.get('extreme_weak', 0)}, unknown={dist.get('unknown', 0)}")
            # 极弱市场交易数断言
            extreme_weak_trades = sum(1 for t in all_trades if t.regime == "extreme_weak")
            logger.info(f"极弱市场实际交易数：{extreme_weak_trades}（应恒为 0）")
        logger.info(f"总交易数：{self.stats['total_trades']}")
        logger.info("=" * 70)

        return all_trades

    def _backtest_single_stock(self, code: str, name: str) -> List[Dict]:
        """
        对单只股票执行滑动窗口回测（R21：只产出候选信号，不做月度配额、不 increment）

        R21 改造：
            原逻辑在逐股票遍历中 increment monthly counter，导致先遍历的股票月末信号
            占满名额、后遍历的股票月初信号被过滤，结果依赖股票列表排序。
            改后：本方法只做「同股票冷却期 + 前两层风控（评分阈值+大盘趋势）」，
            返回候选信号列表（dict），月度配额统一在 run() 里按全局排序后分配。

        Args:
            code: 股票代码
            name: 股票名称

        Returns:
            List[Dict]: 候选信号列表，每个 dict 含 signal/signal_date/score/code/name/df
        """
        # 加载完整 K 线数据
        df = self.data_loader.load_kline_data(code)
        if df is None or df.empty:
            self.stats["skipped_no_data"] += 1
            return []

        # 数据长度检查
        if len(df) < self.config.window_size:
            self.stats["skipped_short_data"] += 1
            return []

        candidates: List[Dict] = []
        # 记录最近一次信号日期，用于冷却期判断（同股票内冷却期，保留在本方法）
        last_signal_idx = -self.config.signal_cooldown_days

        # 滑动窗口：从第 window_size 天开始，逐日作为"当前日期"
        for i in range(self.config.window_size, len(df), self.config.step_days):
            # 信号冷却期检查（同股票内冷却期，保留）
            if i - last_signal_idx < self.config.signal_cooldown_days:
                continue

            # 截取窗口数据：只取最近 window_size 天，保持每次调用数据量固定
            window_start = max(0, i - self.config.window_size + 1)
            window_df = df.iloc[window_start:i + 1].copy()

            # 大盘过滤检查
            # V3（修复 issue 2）：graded 模式下旧 MA 均线过滤必须让位于
            # decide_market_regime 权威裁决——否则跌破均线的弱市日期会被此处提前
            # 拦截，graded 的分级降仓策略永远得不到评估机会。
            # shadow 模式回测须与 hard_filter 等价（零回归），因此仍保留旧过滤。
            current_date = window_df["date"].iloc[-1].strftime("%Y-%m-%d")
            _regime_mode = (
                self.risk_controller.config.market_regime_mode
                if self.risk_controller is not None
                else "hard_filter"
            )
            if (
                _regime_mode != "graded"
                and self.config.index_filter_enabled
                and self.index_filter_map
            ):
                if not self.index_filter_map.get(current_date, False):
                    self.stats["skipped_index_filter"] += 1
                    continue

            # 调用策略分析
            try:
                signal = self.strategy.analyze(code, {"d": window_df})
            except Exception as e:
                logger.debug(f"{code} 策略分析异常：{e}")
                continue

            if signal is None:
                continue

            # 校验信号日期是否在当前窗口末尾附近（防止策略返回历史信号）
            signal_date = signal.signal_date
            if not signal_date:
                continue

            # 检查信号日期是否为当前窗口的最后一天
            window_last_date = window_df["date"].iloc[-1].strftime("%Y-%m-%d")
            if signal_date != window_last_date:
                continue

            self.stats["total_signals"] += 1

            # ===== 前两层风控检查（R21：不传 monthly_count，让 apply_all_controls 跳过第三层）=====
            # 原逻辑在此处传 monthly_count 拦截单月上限，导致结果依赖股票遍历顺序
            # 改后：月度配额统一在 run() 全局排序后分配，此处只执行评分阈值 + 大盘趋势
            if self.risk_controller is not None:
                # 获取当日大盘上下文
                market_context = None
                if self.market_context_provider is not None:
                    market_context = self.market_context_provider.get_context(signal_date)

                # 若大盘上下文为 None，构造默认上下文（非弱势市场，数据不足）
                if market_context is None:
                    market_context = MarketContext(
                        current_date=signal_date,
                        index_close=0.0,
                        index_ma20=None,
                        index_ma60=None,
                        is_weak_market=False,
                        data_sufficient=False,
                    )

                # R21：不传 monthly_count，apply_all_controls 只执行评分阈值 + 大盘趋势
                # 第三层（单月上限）由 run() 在 cap_by_monthly_quota 里统一分配
                risk_result = self.risk_controller.apply_all_controls(
                    signal, market_context
                )

                if not risk_result.passed:
                    # 更新回测引擎统计计数器
                    if risk_result.filter_rule == "long_ma_filter":
                        self.stats["skipped_long_ma_filter"] += 1
                    # R21：monthly_limit 不再在本层拦截，移除此统计
                    elif risk_result.filter_rule == "score_threshold":
                        self.stats["skipped_score_filter"] += 1
                    # V2.1 新增过滤规则
                    elif risk_result.filter_rule == "macd_filter":
                        self.stats["skipped_macd_filter"] += 1
                    elif risk_result.filter_rule == "slope_filter":
                        self.stats["skipped_slope_filter"] += 1
                    elif risk_result.filter_rule == "score_adjustment":
                        self.stats["skipped_score_adjustment"] += 1

                    # 注意：风控拦截的逐条日志已移除（原 logger.info），避免回测高频 IO 拖慢速度
                    # 过滤规则命中统计已在上方累加到 stats（skipped_macd_filter 等），
                    # 回测结束后的汇总报告会展示各规则的拦截笔数，无需逐条记录
                    continue

            # ===== V3 新增：分级市场裁决 =====
            # 仅在 graded 模式下，风控通过后（apply_all_controls 已放行）额外调用
            # decide_market_regime 获取 position_multiplier / regime / daily_signal_limit，
            # 存入 candidates 供 run() 模拟交易时使用。
            #
            # hard_filter / shadow：position_multiplier 默认 1.0，regime 留空，
            # 模拟交易走满仓路径（shadow 回测须与 hard_filter 等价，零回归）。
            regime = ""
            position_multiplier = 1.0
            daily_signal_limit = 0  # 0 = 不限制

            controller = self.risk_controller
            ctx_provider = self.market_context_provider
            if (
                controller is not None
                and ctx_provider is not None
                and controller.config.market_regime_mode == "graded"
            ):
                # 查缓存：同一日期多只股票会重复触发，缓存避免 O(N_stocks) 次重复裁决
                if signal_date in self._graded_decisions_cache:
                    decision = self._graded_decisions_cache[signal_date]
                else:
                    mkt_ctx = ctx_provider.get_context(signal_date)
                    if mkt_ctx is None:
                        # 大盘数据缺失：标记为 unknown（理论上 apply_all_controls 在 graded 下
                        # 会提前拦截 market_data_unavailable，此处仅做防御）
                        continue
                    decision = controller.decide_market_regime(mkt_ctx)
                    self._graded_decisions_cache[signal_date] = decision

                # 防御性：allow_entry=False 时 apply_all_controls 本应已拦截；
                # 此处再做一次保险性检查，确保极弱市场不会产生交易
                if not decision.allow_entry:
                    continue

                # ===== V3：graded 模式相对强度过滤 =====
                # 与 daily_scan.py 的 graded 路径对齐：require_relative_strength=True 时
                # 计算个股 RS 并按 relative_strength_min 阈值过滤。
                # 数据源：
                #   - 个股：df（完整 K 线）截断到 signal_date 后取 tail(lookback+1)
                #   - 指数：mkt_ctx.index_close_history（PreloadedProvider 已截断）
                if decision.require_relative_strength:
                    mrp = controller.config.market_regime_policy_config
                    rs_lookback = mrp.relative_strength_lookback
                    rs_min = mrp.relative_strength_min

                    # 个股：截断到 signal_date 防前视，构造带交易日索引的 Series
                    kline_cut = df[
                        df["date"].dt.strftime("%Y-%m-%d") <= signal_date
                    ]
                    if kline_cut.empty:
                        continue
                    # 序列末日期对齐校验
                    if kline_cut["date"].iloc[-1].strftime("%Y-%m-%d") != signal_date:
                        continue

                    stock_closes = pd.Series(
                        kline_cut["close"].astype(float).values,
                        index=pd.to_datetime(kline_cut["date"]).dt.strftime(
                            "%Y-%m-%d"
                        ).values,
                        name="stock_close",
                    )

                    # 指数：mkt_ctx 来自 PreloadedProvider，index_close_history
                    # 索引为交易日，已自动截断到 signal_date；按日期对齐由函数完成
                    if mkt_ctx.index_close_history is None:
                        continue
                    index_closes = mkt_ctx.index_close_history

                    rs_value = calculate_relative_strength(
                        stock_closes, index_closes, rs_lookback
                    )
                    if rs_value is None or rs_value < rs_min:
                        continue

                regime = decision.regime
                position_multiplier = decision.position_multiplier
                daily_signal_limit = decision.daily_signal_limit

            # P1-1 修复（2026-10-20）：冷却锚点只在候选通过全部准入检查并真正入选后更新。
            # 修复前 last_signal_idx = i 位于 RS 校验之前，候选随后因 RS/分级裁决
            # continue 时仍会占用 60 日冷却期，错误抑制后续本可放行的有效信号
            # （回测信号数与收益系统性偏低）。
            last_signal_idx = i

            # R21+V3：候选信号收集，附加分级裁决字段
            candidates.append({
                'signal': signal,
                'signal_date': signal_date,
                'score': signal.score,
                'code': code,
                'name': name,
                'df': df,
                # V3 分级裁决字段
                'regime': regime,
                'position_multiplier': position_multiplier,
                'daily_signal_limit': daily_signal_limit,
            })

        # ===== V3：market_regime_distribution 已在 run() 中独立计算（P2-5）=====
        return candidates

    def _compute_market_regime_distribution(self) -> None:
        """V3 新增：采集完整 market_regime_distribution（P2-5 修复：与个股解耦）。

        遍历 PreloadedProvider 的全部日期，不管有没有信号都统计一次 regime，
        因此分布反映的是回测期间每天的裁决结果，而不是"只有产生信号的那些天"。

        修复前该统计写在 `_backtest_single_stock()` 尾部：只要股票池里所有股票
        都无数据 / 数据不足窗口而提前 return，分布就永远算不出来（报告市场归因为空）。
        现在改为在 run() 初始化市场上下文后独立调用一次（幂等，已算过直接返回）。
        """
        if self._market_regime_distribution_computed:
            return
        if (
            self.risk_controller is None
            or self.market_context_provider is None
            or self.risk_controller.config.market_regime_mode != "graded"
        ):
            return
        ctx_map = getattr(self.market_context_provider, "context_map", {})
        if not ctx_map:
            # 无法枚举日期（Provider 未预载上下文）→ 保持未计算状态，不改写分布
            return
        self._market_regime_distribution.clear()
        for d_str, ctx in ctx_map.items():
            # 优先复用缓存里的决策（可能该日期已有信号触发），避免重复计算
            if d_str in self._graded_decisions_cache:
                dec = self._graded_decisions_cache[d_str]
            else:
                dec = self.risk_controller.decide_market_regime(ctx)
                self._graded_decisions_cache[d_str] = dec
            self._market_regime_distribution[dec.regime] += 1
        self._market_regime_distribution_computed = True

    def get_stats(self) -> Dict:
        """获取回测统计信息"""
        return self.stats.copy()

    def get_market_regime_distribution(self) -> Dict[str, int]:
        """V3 新增：获取回测期间完整的市场状态分布。

        以 PreloadedProvider context_map 的全部日期为基础，逐日调用
        decide_market_regime 统计 regime，按日期去重（一天一条裁决）。
        graded/shadow 模式下返回真实分布；hard_filter 模式返回空 dict。
        """
        return dict(self._market_regime_distribution)

    def _apply_daily_signal_limit(self, candidates: List[Dict]) -> List[Dict]:
        """V3 新增：按 daily_signal_limit 截断每日候选信号。

        graded/shadow 模式下，弱市/极弱市可能配置了每日最多放行的信号数。
        此方法在月度配额之前，按日期+regime 分组后截断。
        hard_filter 模式下 daily_signal_limit 恒为 0，截断逻辑自动跳过。

        Args:
            candidates: 已按 (signal_date, -score) 全局排序的候选列表

        Returns:
            List[Dict]: 截断后的候选列表（同全局排序）
        """
        if not candidates:
            return candidates

        # 非 graded 模式（hard_filter/shadow）：daily_signal_limit 恒为 0，无需截断
        if (
            self.risk_controller is None
            or self.risk_controller.config.market_regime_mode != "graded"
        ):
            return candidates

        # 按 signal_date 分组（全局已排序，同日期内也已按 score 降序）
        by_date: Dict[str, List[Dict]] = defaultdict(list)
        for c in candidates:
            by_date[c['signal_date']].append(c)

        truncated: List[Dict] = []
        # _stats_skipped 用于日志，暂不暴露：由各 regime 的 daily limit 控制即可
        for date_str in sorted(by_date.keys()):
            day_cands = by_date[date_str]

            # decision.daily_signal_limit 来自 MarketRegimeDecision
            # strong/unknown 通常为 0（不限制），weak/extreme_weak 可能有值
            limit = day_cands[0].get('daily_signal_limit', 0) if day_cands else 0

            if limit <= 0 or len(day_cands) <= limit:
                truncated.extend(day_cands)
            else:
                # 只取前 limit 个（按 score 降序已排好）
                truncated.extend(day_cands[:limit])

        return truncated


# ==================== 统计报告生成器 ====================

class BacktestReporter:
    """
    回测报告生成器

    统计交易结果，生成并输出回测报告
    """

    def __init__(self, config: BacktestConfig):
        """
        初始化报告生成器

        Args:
            config: 回测配置
        """
        self.config = config

    def generate_report(
        self,
        trades: List[TradeResult],
        stats: Dict,
        risk_control_stats: Optional[Dict] = None,
    ) -> Dict:
        """
        生成回测报告

        Args:
            trades: 交易结果列表
            stats: 回测引擎统计信息
            risk_control_stats: 风控命中统计（新增，可选）
                {
                    'summary': {...},
                    'monthly': [...]
                }

        Returns:
            Dict: 回测报告字典
        """
        # 计算回测年限
        start_dt = datetime.strptime(self.config.start_date, "%Y-%m-%d")
        end_dt = datetime.strptime(self.config.end_date, "%Y-%m-%d")
        years = (end_dt - start_dt).days / 365.25

        # 判断 V2.1 是否启用（用于报告 meta 标识）
        risk_cfg = self.config.risk_control_config
        v21_enabled = (
            risk_cfg is not None
            and SchemeConfigurator.is_v21_enabled(risk_cfg)
        )

        # 基础 meta 信息（无论是否有交易都应包含）
        meta = {
            "strategy": "oversold_bounce",
            "version": "v25",
            "backtest_start": self.config.start_date,
            "backtest_end": self.config.end_date,
            "backtest_years": round(years, 2),
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "index_filter": self.config.index_filter_enabled,
            # V2.1 新增：方案与功能开关信息
            "scheme": self.config.scheme,
            "scheme_description": self.config.scheme_description,
            "index_filter_method": (
                risk_cfg.index_filter_method if risk_cfg else "ma_position"
            ),
            "slope_filter_enabled": (
                risk_cfg.slope_filter_enabled if risk_cfg else False
            ),
            "score_adjustment_enabled": (
                risk_cfg.score_adjustment_enabled if risk_cfg else False
            ),
            "v21_enabled": v21_enabled,
        }

        if not trades:
            # P2（修复零交易报告）：即便无交易，也必须输出 V3 分级市场字段，
            # 否则 graded 回测在弱市/极弱市出现零交易时，报告缺少
            # market_regime_distribution / trade_summary_by_regime / 极弱市场交易数，
            # 无法证明“零交易来自极弱市场拦截”而非回测链路故障。
            zero_trade_dist = (
                risk_control_stats.get("market_regime_distribution", {})
                if risk_control_stats is not None
                else {}
            )
            return {
                "meta": meta,
                "summary": {
                    "total_stocks": stats.get("total_stocks", 0),
                    "total_signals": stats.get("total_signals", 0),
                    "total_trades": 0,
                    "win_count": 0,
                    "loss_count": 0,
                    "win_rate": 0,
                    "avg_return": 0,
                    "total_return": 0,
                    "max_return": 0,
                    "min_return": 0,
                    "avg_hold_days": 0,
                    "signals_per_year": 0,
                    "message": "无交易记录",
                },
                "exit_reasons": {},
                "yearly_stats": [],
                "stats": stats,
                # V3 新增：分级市场裁决报告字段（零交易时 trade_summary_by_regime
                # 以空 trades 计算，返回各 regime 全 0 的结构）
                "market_regime_distribution": zero_trade_dist,
                "trade_summary_by_regime": self._calc_trade_summary_by_regime([]),
                "extreme_weak_trade_count": 0,
                "trades": [],
                "risk_control_stats": risk_control_stats or {
                    "summary": {
                        # V2.0 原有
                        "skipped_long_ma_filter": 0,
                        "skipped_monthly_limit": 0,
                        "skipped_score_filter": 0,
                        # V2.1 新增
                        "skipped_macd_filter": 0,
                        "skipped_slope_filter": 0,
                        "skipped_score_adjustment": 0,
                        "total_filtered": 0,
                    },
                    "monthly": [],
                },
            }

        # 计算统计指标
        returns = [t.net_return for t in trades]
        win_trades = [t for t in trades if t.net_return > 0]
        loss_trades = [t for t in trades if t.net_return <= 0]

        total_return = sum(returns)
        avg_return = sum(returns) / len(returns)
        win_rate = len(win_trades) / len(trades) if trades else 0
        max_return = max(returns)
        min_return = min(returns)

        # 退出原因统计
        exit_reasons = {}
        for t in trades:
            exit_reasons[t.exit_reason] = exit_reasons.get(t.exit_reason, 0) + 1

        # 按年份统计
        yearly_stats = self._calc_yearly_stats(trades)

        # V3 新增：按 market regime 汇总交易结果
        trade_summary_by_regime = self._calc_trade_summary_by_regime(trades)

        # V3 新增：从 risk_control_stats 中提取 market_regime_distribution
        # run() 方法调用时会把 engine.get_market_regime_distribution() 注入到 risk_control_stats
        market_regime_distribution = {}
        extreme_weak_trade_count = 0
        if risk_control_stats is not None:
            market_regime_distribution = risk_control_stats.get(
                "market_regime_distribution", {}
            )
            extreme_weak_trade_count = sum(
                1 for t in trades if t.regime == "extreme_weak"
            )
        # 兜底：从 trades 里也计算一遍 trade_summary_by_regime
        # （当直接构造 reporter 而不走 BacktestEngine.run() 时，
        # trade_summary_by_regime 靠 trades 本身就能算出来；
        # market_regime_distribution 可能为空，此时保持 {}）

        report = {
            "meta": meta,
            "summary": {
                "total_stocks": stats.get("total_stocks", 0),
                "total_signals": stats.get("total_signals", 0),
                "total_trades": len(trades),
                "win_count": len(win_trades),
                "loss_count": len(loss_trades),
                "win_rate": round(win_rate * 100, 2),
                "avg_return": round(avg_return * 100, 2),
                "total_return": round(total_return * 100, 2),
                "max_return": round(max_return * 100, 2),
                "min_return": round(min_return * 100, 2),
                "avg_hold_days": round(sum(t.hold_days for t in trades) / len(trades), 1),
                "signals_per_year": round(len(trades) / years, 2) if years > 0 else 0,
            },
            "exit_reasons": exit_reasons,
            "yearly_stats": yearly_stats,
            "stats": stats,
            "risk_control_stats": risk_control_stats or {
                "summary": {
                    # V2.0 原有
                    "skipped_long_ma_filter": 0,
                    "skipped_monthly_limit": 0,
                    "skipped_score_filter": 0,
                    # V2.1 新增
                    "skipped_macd_filter": 0,
                    "skipped_slope_filter": 0,
                    "skipped_score_adjustment": 0,
                    "total_filtered": 0,
                },
                "monthly": [],
            },
            # V3 新增：分级市场裁决报告字段
            "market_regime_distribution": market_regime_distribution,
            "trade_summary_by_regime": trade_summary_by_regime,
            "extreme_weak_trade_count": extreme_weak_trade_count,
            "trades": [asdict(t) for t in trades],
        }

        return report

    def _calc_trade_summary_by_regime(self, trades: List[TradeResult]) -> Dict[str, Dict]:
        """V3 新增：按 market regime 汇总交易结果。

        返回一个 dict，key 为 regime 字符串（strong / weak / extreme_weak / unknown），
        value 为该 regime 下的交易汇总：trades 数 / win_rate / avg_return / total_return。
        没有交易的 regime 也会被包含（trades=0，其他字段为 0）。
        """
        regimes_of_interest = ("strong", "weak", "extreme_weak", "unknown")
        by_regime: Dict[str, List[TradeResult]] = defaultdict(list)

        for t in trades:
            # regime 为空字符串（非 graded 模式）时归为 "unknown"
            r = t.regime or "unknown"
            by_regime[r].append(t)

        summary: Dict[str, Dict] = {}
        for regime in regimes_of_interest:
            rt = by_regime.get(regime, [])
            if rt:
                returns = [t.net_return for t in rt]
                wins = sum(1 for r in returns if r > 0)
                summary[regime] = {
                    "trades": len(rt),
                    "win_count": wins,
                    "win_rate": round(wins / len(rt) * 100, 2),
                    "avg_return": round(sum(returns) / len(rt) * 100, 2),
                    "total_return": round(sum(returns) * 100, 2),
                }
            else:
                summary[regime] = {
                    "trades": 0,
                    "win_count": 0,
                    "win_rate": 0.0,
                    "avg_return": 0.0,
                    "total_return": 0.0,
                }

        return summary

    def _calc_yearly_stats(self, trades: List[TradeResult]) -> List[Dict]:
        """
        按年份统计交易结果

        Args:
            trades: 交易结果列表

        Returns:
            List[Dict]: 年度统计列表
        """
        yearly: Dict[str, List[TradeResult]] = {}
        for t in trades:
            year = t.entry_date[:4] if t.entry_date else "unknown"
            yearly.setdefault(year, []).append(t)

        result = []
        for year in sorted(yearly.keys()):
            year_trades = yearly[year]
            returns = [t.net_return for t in year_trades]
            win_count = sum(1 for r in returns if r > 0)
            result.append({
                "year": year,
                "trade_count": len(year_trades),
                "win_count": win_count,
                "win_rate": round(win_count / len(year_trades) * 100, 2),
                "avg_return": round(sum(returns) / len(returns) * 100, 2),
                "total_return": round(sum(returns) * 100, 2),
            })

        return result

    def print_report(self, report: Dict) -> None:
        """
        打印回测报告到控制台

        Args:
            report: 回测报告字典
        """
        print()
        print("=" * 70)
        print("OBPC 超跌反弹策略 V2.1 历史回测报告")
        print("=" * 70)

        meta = report.get("meta", {})
        print(f"\n回测区间：{meta.get('backtest_start')} ~ {meta.get('backtest_end')}")
        print(f"回测年限：{meta.get('backtest_years')} 年")
        print(f"大盘过滤：{'启用' if meta.get('index_filter') else '关闭'}")
        print(f"生成时间：{meta.get('generated_at')}")

        # V2.1 新增：方案与功能开关信息展示
        if meta.get("v21_enabled"):
            print("\n" + "-" * 70)
            print("V2.1 优化方案配置")
            print("-" * 70)
            if meta.get("scheme"):
                print(f"  回测方案：       方案{meta['scheme']}")
                print(f"  方案描述：       {meta.get('scheme_description', '')}")
            print(f"  大盘过滤方法：   {meta.get('index_filter_method', 'ma_position')}")
            print(f"  斜率过滤：       {'启用' if meta.get('slope_filter_enabled') else '关闭'}")
            print(f"  评分调整：       {'启用' if meta.get('score_adjustment_enabled') else '关闭'}")

        summary = report.get("summary", {})
        print("\n" + "-" * 70)
        print("核心指标")
        print("-" * 70)
        print(f"  总股票数：       {summary.get('total_stocks', 0)}")
        print(f"  总信号数：       {summary.get('total_signals', 0)}")
        print(f"  总交易数：       {summary.get('total_trades', 0)}")
        print(f"  盈利交易数：     {summary.get('win_count', 0)}")
        print(f"  亏损交易数：     {summary.get('loss_count', 0)}")
        print(f"  胜率：           {summary.get('win_rate', 0)}%")
        print(f"  平均收益：       {summary.get('avg_return', 0)}%")
        print(f"  累计收益：       {summary.get('total_return', 0)}%")
        print(f"  最高收益：       {summary.get('max_return', 0)}%")
        print(f"  最低收益：       {summary.get('min_return', 0)}%")
        print(f"  平均持仓天数：   {summary.get('avg_hold_days', 0)}")
        print(f"  年均信号数：     {summary.get('signals_per_year', 0)}")

        exit_reasons = report.get("exit_reasons", {})
        print("\n" + "-" * 70)
        print("退出原因分布")
        print("-" * 70)
        reason_names = {
            "trailing_stop": "移动止盈",
            "hard_stop": "硬止损",
            "support_stop": "支撑位止损",
            "max_hold": "最长持仓到期",
            "min_hold": "最短持仓",
        }
        for reason, count in sorted(exit_reasons.items(), key=lambda x: -x[1]):
            name = reason_names.get(reason, reason)
            pct = count / summary.get("total_trades", 1) * 100
            print(f"  {name:<12} {count:>4} 次 ({pct:.1f}%)")

        yearly_stats = report.get("yearly_stats", [])
        if yearly_stats:
            print("\n" + "-" * 70)
            print("年度统计")
            print("-" * 70)
            print(f"  {'年份':<6} {'交易数':>6} {'盈利数':>6} {'胜率':>8} {'平均收益':>10} {'累计收益':>10}")
            for y in yearly_stats:
                print(
                    f"  {y['year']:<6} {y['trade_count']:>6} {y['win_count']:>6} "
                    f"{y['win_rate']:>7.2f}% {y['avg_return']:>9.2f}% {y['total_return']:>9.2f}%"
                )

        # 风控命中统计（新增）
        risk_control_stats = report.get("risk_control_stats", {})
        rc_summary = risk_control_stats.get("summary", {})
        rc_monthly = risk_control_stats.get("monthly", [])
        if rc_summary and rc_summary.get("total_filtered", 0) > 0:
            print("\n" + "-" * 70)
            print("风控命中统计")
            print("-" * 70)
            print(f"  60日均线过滤：   {rc_summary.get('skipped_long_ma_filter', 0)} 次")
            print(f"  单月上限过滤：   {rc_summary.get('skipped_monthly_limit', 0)} 次")
            print(f"  评分阈值过滤：   {rc_summary.get('skipped_score_filter', 0)} 次")
            print(f"  总过滤信号数：   {rc_summary.get('total_filtered', 0)} 次")

            if rc_monthly:
                print("\n  按月份分布：")
                print(f"  {'月份':<10} {'60日均线':>8} {'单月上限':>8} {'评分阈值':>8} {'合计':>8}")
                for m in rc_monthly:
                    if m.get("total_filtered", 0) > 0:
                        print(
                            f"  {m['year_month']:<10} "
                            f"{m.get('long_ma_filter', 0):>8} "
                            f"{m.get('monthly_limit', 0):>8} "
                            f"{m.get('score_filter', 0):>8} "
                            f"{m.get('total_filtered', 0):>8}"
                        )

        # V2.1 新增：市场状态过滤统计段（仅 V2.1 启用且有命中时显示）
        v21_macd = rc_summary.get("skipped_macd_filter", 0)
        v21_slope = rc_summary.get("skipped_slope_filter", 0)
        v21_score_adj = rc_summary.get("skipped_score_adjustment", 0)
        v21_total = v21_macd + v21_slope + v21_score_adj
        if meta.get("v21_enabled") and v21_total > 0:
            print("\n" + "-" * 70)
            print("V2.1 市场状态过滤统计")
            print("-" * 70)
            print(f"  MACD 动量过滤：  {v21_macd} 次")
            print(f"  均线斜率过滤：   {v21_slope} 次")
            print(f"  评分归零过滤：   {v21_score_adj} 次")
            print(f"  V2.1 总过滤数：  {v21_total} 次")

            # 按月份分布（仅展示 V2.1 相关列）
            v21_monthly = [
                m for m in rc_monthly
                if m.get("macd_filter", 0) + m.get("slope_filter", 0)
                + m.get("score_adjustment", 0) > 0
            ]
            if v21_monthly:
                print("\n  按月份分布：")
                print(
                    f"  {'月份':<10} {'MACD过滤':>10} {'斜率过滤':>10} "
                    f"{'评分归零':>10} {'合计':>8}"
                )
                for m in v21_monthly:
                    month_total = (
                        m.get("macd_filter", 0)
                        + m.get("slope_filter", 0)
                        + m.get("score_adjustment", 0)
                    )
                    print(
                        f"  {m['year_month']:<10} "
                        f"{m.get('macd_filter', 0):>10} "
                        f"{m.get('slope_filter', 0):>10} "
                        f"{m.get('score_adjustment', 0):>10} "
                        f"{month_total:>8}"
                    )

        # V3 新增：分级市场裁决统计段（graded/shadow 模式下总是显示）
        market_regime_dist = report.get("market_regime_distribution", {})
        trade_summary = report.get("trade_summary_by_regime", {})
        extreme_weak_count = report.get("extreme_weak_trade_count", -1)

        if market_regime_dist or trade_summary:
            # 只有有数据时才打印（非 graded 模式下两者都为空，不打印）
            print("\n" + "-" * 70)
            print("V3 分级市场裁决统计")
            print("-" * 70)

            # 市场状态分布（按日期去重）
            if market_regime_dist:
                print("\n  市场状态天数分布：")
                total_days = sum(market_regime_dist.values())
                label_map = {
                    "strong": "强势",
                    "weak": "偏弱",
                    "extreme_weak": "极弱",
                    "unknown": "数据缺失",
                }
                for regime in ("strong", "weak", "extreme_weak", "unknown"):
                    days = market_regime_dist.get(regime, 0)
                    if days > 0:
                        pct = days / total_days * 100 if total_days > 0 else 0
                        label = label_map.get(regime, regime)
                        print(f"  {label}（{regime:<14}） {days:>5} 天 ({pct:>5.1f}%)")

            # 按 regime 汇总交易结果
            if trade_summary and summary.get("total_trades", 0) > 0:
                print("\n  按市场状态交易结果：")
                print(
                    f"  {'状态':<10} {'交易数':>6} {'胜率':>8} "
                    f"{'平均收益':>10} {'累计收益':>10}"
                )
                label_map2 = {
                    "strong": "强势",
                    "weak": "偏弱",
                    "extreme_weak": "极弱",
                    "unknown": "其他",
                }
                for regime in ("strong", "weak", "extreme_weak", "unknown"):
                    rs = trade_summary.get(regime, {})
                    trades_in_regime = rs.get("trades", 0)
                    if trades_in_regime > 0 or regime in ("extreme_weak",):
                        label = label_map2.get(regime, regime)
                        print(
                            f"  {label:<10} {trades_in_regime:>6} "
                            f"{rs.get('win_rate', 0):>7.1f}% "
                            f"{rs.get('avg_return', 0):>9.2f}% "
                            f"{rs.get('total_return', 0):>9.2f}%"
                        )

            # 极弱市场交易数断言
            if extreme_weak_count >= 0:
                marker = "OK" if extreme_weak_count == 0 else "⚠ 违规！"
                print(f"\n  极弱市场实际交易数：{extreme_weak_count}（应为 0）{marker}")

        # V2.0 预期对比
        print("\n" + "-" * 70)
        print("V2.0 预期指标对比")
        print("-" * 70)
        # 从配置读取预期指标，避免硬编码
        expected_cfg = self.config.expected_metrics
        expected = {
            "总信号数": (expected_cfg.get("total_trades", 0), summary.get("total_trades", 0)),
            "年均信号": (expected_cfg.get("signals_per_year", 0), summary.get("signals_per_year", 0)),
            "平均收益": (expected_cfg.get("avg_return", 0), summary.get("avg_return", 0)),
            "胜率": (expected_cfg.get("win_rate", 0), summary.get("win_rate", 0)),
            "最高收益": (expected_cfg.get("max_return", 0), summary.get("max_return", 0)),
            "最低收益": (expected_cfg.get("min_return", 0), summary.get("min_return", 0)),
        }
        print(f"  {'指标':<10} {'预期':>10} {'实际':>10} {'差异':>10}")
        for name, (exp, actual) in expected.items():
            diff = actual - exp
            print(f"  {name:<10} {exp:>10} {actual:>10} {diff:>+10}")

        print("\n" + "=" * 70)

    def save_report(self, report: Dict) -> str:
        """
        保存回测报告到 JSON 文件

        文件名规则：
            - 指定方案编号：backtest_scheme{N}_{timestamp}.json
            - 未指定方案：  backtest_{timestamp}.json（保持 V2.0 原有逻辑）

        Args:
            report: 回测报告字典

        Returns:
            str: 保存的文件路径
        """
        output_dir = self.config.output_dir
        os.makedirs(output_dir, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        # V2.1 新增：方案编号存在时，文件名包含方案编号
        scheme = report.get("meta", {}).get("scheme")
        if scheme:
            filename = f"backtest_scheme{scheme}_{timestamp}.json"
        else:
            filename = f"backtest_{timestamp}.json"
        filepath = os.path.join(output_dir, filename)

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)

        logger.info(f"回测报告已保存：{filepath}")
        return filepath


# ==================== 主函数 ====================

# 多进程工作所需的全局变量（每个子进程初始化时设置）
_WORKER_CONFIG: Optional[BacktestConfig] = None
_WORKER_STRATEGY: Optional[OversoldBounceStrategy] = None
_WORKER_SIMULATOR: Optional[TradeSimulator] = None
_WORKER_INDEX_FILTER: Dict = {}


def _init_worker(
    config: BacktestConfig,
    strategy_params: Dict,
    index_filter_map: Dict,
):
    """
    子进程初始化函数

    在每个子进程启动时创建策略实例等不可序列化的对象
    数据库连接在每只股票处理时创建，避免多进程连接状态问题

    Args:
        config: 回测配置
        strategy_params: 策略参数
        index_filter_map: 大盘过滤字典
    """
    global _WORKER_CONFIG, _WORKER_STRATEGY, _WORKER_SIMULATOR, _WORKER_INDEX_FILTER
    _WORKER_CONFIG = config
    _WORKER_STRATEGY = OversoldBounceStrategy(params=strategy_params)
    _WORKER_SIMULATOR = TradeSimulator(config)
    _WORKER_INDEX_FILTER = index_filter_map


def _process_stock_worker(task: Dict) -> List[Dict]:
    """
    子进程工作函数：处理单只股票的回测

    DB 模式：每只股票创建独立的数据库连接，处理完成后关闭
    CSV 模式：跳过 DB 连接，DataLoader 自动走 CSV 分支

    Args:
        task: 包含 code, name 的任务字典

    Returns:
        List[Dict]: 交易结果字典列表
    """
    global _WORKER_CONFIG, _WORKER_STRATEGY, _WORKER_SIMULATOR, _WORKER_INDEX_FILTER

    code = task["code"]
    name = task.get("name", code)

    # CSV 模式下跳过 DB 连接
    csv_mode = _WORKER_CONFIG is not None and _WORKER_CONFIG.csv_data_dir is not None
    db = None if csv_mode else DatabaseManager()
    try:
        data_loader = BacktestDataLoader(_WORKER_CONFIG, db=db)
        engine = BacktestEngine(
            _WORKER_CONFIG, _WORKER_STRATEGY, data_loader, _WORKER_SIMULATOR
        )
        engine.index_filter_map = _WORKER_INDEX_FILTER

        trades = engine._backtest_single_stock(code, name)
        return [asdict(t) for t in trades]
    except Exception as e:
        logger.error(f"回测 {code} 异常：{e}")
        return []
    finally:
        if db is not None:
            db.close()


def main():
    """主函数：解析命令行参数并执行回测"""
    parser = argparse.ArgumentParser(
        description="OBPC 超跌反弹策略 V2.0 历史回测脚本"
    )
    parser.add_argument(
        "--start", default="2020-01-01",
        help="回测起始日期（默认 2020-01-01）"
    )
    parser.add_argument(
        "--end", default="2026-04-07",
        help="回测结束日期（默认 2026-04-07）"
    )
    parser.add_argument(
        "--index-filter", action="store_true",
        help="启用大盘过滤（沪深300在20日均线上方）"
    )
    parser.add_argument(
        "--max-stocks", type=int, default=0,
        help="最大回测股票数（0=不限制，用于调试）"
    )
    parser.add_argument(
        "--step", type=int, default=1,
        help="滑动窗口步长（默认1=逐日，增大可加速回测但可能遗漏信号）"
    )
    parser.add_argument(
        "--workers", type=int, default=0,
        help="并行进程数（默认0=自动，根据CPU核心数设置）"
    )
    parser.add_argument(
        "--config", default="config/config.yaml",
        help="配置文件路径（默认 config/config.yaml）"
    )
    parser.add_argument(
        "--scheme", type=int, default=None, choices=[1, 2, 3, 4, 5, 6],
        help="V2.1 回测方案编号（1/2/3/4/5/6），不指定则使用 config.yaml 默认配置"
    )
    parser.add_argument(
        "--data-dir", default=None,
        help=(
            "本地 CSV 数据目录（含 stocks_mainboard.csv / klines_mainboard.csv / "
            "hs300_index.csv），传入后走 CSV 数据源，不连接生产 PostgreSQL。"
            "默认不传即走 DB 路径（零回归）。"
        )
    )
    args = parser.parse_args()

    # 加载回测配置
    config = load_backtest_config(
        config_path=args.config,
        start_date=args.start,
        end_date=args.end,
        index_filter=args.index_filter,
        max_stocks=args.max_stocks,
    )
    # 覆盖滑动步长
    config.step_days = args.step

    # CSV 数据源：--data-dir 传入后走本地 CSV，不连 DB
    # 默认不传 = None，保持原 DB 路径零回归
    config.csv_data_dir = args.data_dir
    if args.data_dir is not None:
        logger.info(f"数据源：CSV 模式，目录 = {args.data_dir}（跳过 PostgreSQL 连接）")
    else:
        logger.info("数据源：DB 模式（默认）")

    # V2.1 新增：应用回测方案配置覆盖
    if args.scheme is not None and config.risk_control_config is not None:
        new_risk_config, scheme_desc = SchemeConfigurator.apply_scheme(
            config.risk_control_config, args.scheme
        )
        config.risk_control_config = new_risk_config
        # 同步 BacktestConfig 与 RiskControlConfig 的大盘过滤开关
        # 两者独立定义但语义相同，必须同时修改才生效
        config.index_filter_enabled = new_risk_config.index_filter_enabled
        config.scheme = args.scheme
        config.scheme_description = scheme_desc
        logger.info(f"已应用 V2.1 回测方案：方案{args.scheme} - {scheme_desc}")
        logger.info(
            f"方案配置：method={new_risk_config.index_filter_method}，"
            f"slope_filter={new_risk_config.slope_filter_enabled}，"
            f"score_adjustment={new_risk_config.score_adjustment_enabled}"
        )

    # 判断是否启用风控（新增）
    # 启用风控时强制单进程，确保 InMemoryMonthlyCounter 计数准确
    # V2.1 新增：启用 V2.1 功能时也强制单进程（依赖风控控制器的状态统计）
    risk_cfg = config.risk_control_config
    risk_enabled = (
        risk_cfg is not None
        and (
            risk_cfg.index_filter_enabled
            or risk_cfg.score_filter_enabled
            or risk_cfg.long_ma_filter_enabled
            or SchemeConfigurator.is_v21_enabled(risk_cfg)
        )
    )

    # 确定并行进程数
    if risk_enabled:
        workers = 1
        logger.warning(
            "启用风控规则，回测切换为单进程以确保单月计数准确"
        )
    else:
        workers = args.workers if args.workers > 0 else min(cpu_count(), 4)
    logger.info(f"并行进程数：{workers}")

    # 初始化报告生成器
    reporter = BacktestReporter(config)

    try:
        if risk_enabled:
            # ===== 单进程回测（启用风控时） =====
            # 使用 BacktestEngine.run 方法，确保风控控制器的单月计数器准确
            # CSV 模式下跳过 DB 连接，db=None；DataLoader 自动走 CSV 分支
            csv_mode = config.csv_data_dir is not None
            db = None if csv_mode else DatabaseManager()
            data_loader = BacktestDataLoader(config, db=db)
            strategy = OversoldBounceStrategy(params=config.strategy_params)
            trade_simulator = TradeSimulator(config)
            engine = BacktestEngine(config, strategy, data_loader, trade_simulator)

            trades = engine.run()
            stats = engine.get_stats()

            # 获取风控命中统计
            risk_control_stats = None
            if engine.risk_controller is not None:
                risk_control_stats = engine.risk_controller.get_stats()

            # V3 新增：把 market_regime_distribution 注入到 risk_control_stats 中
            # generate_report 会从 risk_control_stats 里读取这个字段
            market_regime_dist = engine.get_market_regime_distribution()
            if market_regime_dist:
                if risk_control_stats is None:
                    risk_control_stats = {"summary": {}, "monthly": []}
                risk_control_stats["market_regime_distribution"] = market_regime_dist

            if db is not None:
                db.close()

            # 生成并输出报告
            report = reporter.generate_report(trades, stats, risk_control_stats)
            reporter.print_report(report)
            reporter.save_report(report)

        else:
            # ===== 多进程回测（未启用风控时，保持原有逻辑） =====
            # CSV 模式下跳过 DB 连接，DataLoader 自动走 CSV 分支
            csv_mode = config.csv_data_dir is not None
            db = None if csv_mode else DatabaseManager()
            data_loader = BacktestDataLoader(config, db=db)

            # 加载股票列表
            stock_df = data_loader.load_stock_list()
            if stock_df.empty:
                logger.error("无可用股票，回测终止")
                sys.exit(1)

            # 加载大盘过滤数据
            index_filter_map: Dict = {}
            if config.index_filter_enabled:
                index_df = data_loader.load_index_data()
                if index_df is None or index_df.empty:
                    logger.warning("大盘指数数据不存在，自动关闭大盘过滤")
                    config.index_filter_enabled = False
                else:
                    index_filter_map = data_loader.build_index_ma_filter(index_df)
                    if not index_filter_map:
                        logger.warning("大盘过滤字典为空，自动关闭大盘过滤")
                        config.index_filter_enabled = False

            if db is not None:
                db.close()

            # 构建任务列表
            tasks = [
                {"code": row["code"], "name": row.get("name", row["code"])}
                for _, row in stock_df.iterrows()
            ]

            logger.info("=" * 70)
            logger.info("开始执行 OBPC V2.0 历史回测（多进程并行）")
            logger.info(f"股票数：{len(tasks)}，进程数：{workers}")
            logger.info("=" * 70)

            # 多进程并行回测
            all_trade_dicts: List[Dict] = []
            stats = {
                "total_stocks": len(tasks),
                "total_signals": 0,
                "total_trades": 0,
            }

            with Pool(
                processes=workers,
                initializer=_init_worker,
                initargs=(config, config.strategy_params, index_filter_map),
            ) as pool:
                # 分批处理，避免内存占用过高
                batch_size = max(1, len(tasks) // (workers * 4))
                for i, result in enumerate(
                    pool.imap_unordered(_process_stock_worker, tasks, chunksize=batch_size)
                ):
                    all_trade_dicts.extend(result)
                    if (i + 1) % 100 == 0:
                        logger.info(
                            f"回测进度：{i + 1}/{len(tasks)}，"
                            f"已产生 {len(all_trade_dicts)} 笔交易"
                        )

            # 将字典转回 TradeResult 对象（用于报告生成）
            trades = [
                TradeResult(
                    code=t["code"], name=t["name"],
                    signal_date=t["signal_date"],
                    entry_date=t["entry_date"], entry_price=t["entry_price"],
                    exit_date=t["exit_date"], exit_price=t["exit_price"],
                    exit_reason=t["exit_reason"], hold_days=t["hold_days"],
                    raw_return=t["raw_return"], net_return=t["net_return"],
                    max_price=t["max_price"],
                    support_level=t["support_level"],
                    stop_loss_price=t["stop_loss_price"],
                    score=t["score"],
                )
                for t in all_trade_dicts
            ]

            stats["total_signals"] = len(trades)
            stats["total_trades"] = len(trades)

            logger.info("=" * 70)
            logger.info("回测完成")
            logger.info(f"总股票数：{stats['total_stocks']}")
            logger.info(f"总信号数：{stats['total_signals']}")
            logger.info(f"总交易数：{stats['total_trades']}")
            logger.info("=" * 70)

            # 生成并输出报告（未启用风控时 risk_control_stats 为 None）
            report = reporter.generate_report(trades, stats, None)
            reporter.print_report(report)
            reporter.save_report(report)

    except Exception as e:
        logger.error(f"回测执行失败：{e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
