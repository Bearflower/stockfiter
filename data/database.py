"""
数据库管理模块
负责 PostgreSQL 数据库的连接、表结构创建、CRUD 操作封装

V3 变更：klines 表增加 frequency 字段，唯一约束改为 (code, frequency, date)
2026-10-18 改造：
  - scan_results 增加 (scan_date, code) 唯一索引，save_scan_result 改为 UPSERT
  - 新增 scan_runs 表记录结构化扫描运行状态
  - task_status 表增加 target_date/details 列
  - 股票池过滤改为参数化 SQL + 纯函数 filter_stock_pool
  - 移除数据库密码硬编码，必须通过 DB_PASSWORD 环境变量注入
"""

import json
import os
from typing import Optional, List, Dict

import psycopg2
import pandas as pd

from utils.logger import get_logger

logger = get_logger()

# PostgreSQL BIGINT 范围常量
BIGINT_MAX = 9223372036854775807
BIGINT_MIN = -9223372036854775808

# ---- 股票池过滤默认常量（config 缺失时的回退值） ----
# 北交所前缀：83xxx/87xxx/920xxx（920 是北交所 2024 年开关的新代码段）
DEFAULT_BEIJING_PREFIXES = ("83", "87", "920")
# 新三板前缀：仅 43xxx（8xx 已被北交所覆盖）
DEFAULT_NEW_THIRD_BOARD_PREFIXES = ("43",)
# 主板前缀：深 000/001/002，沪 600/601/603/605
DEFAULT_MAIN_BOARD_PREFIXES = ("000", "001", "002", "600", "601", "603", "605")
# 指数/ETF 名称关键词（用于 name 列模糊匹配）
DEFAULT_EXCLUDE_INDEX_KEYWORDS = (
    "指数", "ETF", "基金", "B股",
    "上证", "深证", "中证", "全指",
    "等权", "基本", "新兴", "国企", "海外",
)


def filter_stock_pool(df: pd.DataFrame, stock_pool_config: dict) -> pd.DataFrame:
    """
    股票池过滤纯函数（模块级）。

    供固定 CSV 路径和数据库回退路径共用，作为最终防线。
    get_stock_list() 也复用本函数，避免数据库 SQL 与 DataFrame 过滤逻辑漂移。
    data/stock_list.py 的 filter_stock_list() 也委托到此函数，确保 DB 写入路径
    与扫描路径得到相同股票集合。

    Args:
        df: 含 code、name、list_date 列的 DataFrame
        stock_pool_config: global.stock_pool 配置字典，可包含以下可选覆盖键：
            beijing_prefixes、new_third_board_prefixes、main_board_prefixes、
            exclude_index_keywords

    Returns:
        pd.DataFrame: 过滤后的 DataFrame（重置 index）
    """
    if df is None or df.empty:
        return df.copy() if df is not None else pd.DataFrame()

    cfg = stock_pool_config or {}
    result = df.copy()

    # ---- boolean 开关默认值：与原 filter_stock_list (config.get('xxx', True)) 保持一致 ----
    # 这些开关在空 dict 或缺省时全部生效（排除 ST/退市/北交所/科创板/创业板/新三板/指数/次新股，并只保留主板）
    exclude_st = cfg.get("exclude_st", True)
    exclude_delisting = cfg.get("exclude_delisting", True)
    exclude_beijing = cfg.get("exclude_beijing", True)
    exclude_star_market = cfg.get("exclude_star_market", True)
    exclude_chi_next = cfg.get("exclude_chi_next", True)
    exclude_new_third_board = cfg.get("exclude_new_third_board", True)
    exclude_index = cfg.get("exclude_index", True)
    exclude_new_stocks = cfg.get("exclude_new_stocks", True)
    main_board_only = cfg.get("main_board_only", True)

    # --- ST/*ST 排除 ---
    if exclude_st:
        result = result[~result["name"].fillna("").str.contains("ST", case=False, na=False)]

    # --- 退市排除（名称含"退"） ---
    if exclude_delisting:
        result = result[~result["name"].fillna("").str.contains("退", na=False)]

    # --- 北交所排除 ---
    # 从配置读取前缀列表，缺失时用默认值（含 920 开关的北交所代码）
    if exclude_beijing:
        prefixes = tuple(cfg.get("beijing_prefixes", DEFAULT_BEIJING_PREFIXES))
        result = result[~result["code"].astype(str).str.startswith(prefixes)]

    # --- 科创板排除（688/689 开头） ---
    if exclude_star_market:
        result = result[
            ~result["code"].astype(str).str.startswith(("688", "689"))
        ]

    # --- 创业板排除（300/301 开头） ---
    if exclude_chi_next:
        result = result[
            ~result["code"].astype(str).str.startswith(("300", "301"))
        ]

    # --- 新三板排除 ---
    # 从配置读取前缀列表，缺失时用默认值（仅 43xxx）
    if exclude_new_third_board:
        prefixes = tuple(
            cfg.get("new_third_board_prefixes", DEFAULT_NEW_THIRD_BOARD_PREFIXES)
        )
        result = result[~result["code"].astype(str).str.startswith(prefixes)]

    # --- 指数/ETF/基金 排除 ---
    # 同时检查 name（含指数关键词）和 sector（含指数/ETF/基金），双保险
    # 注意：不使用纯代码匹配，因为上证指数代码 000001 与平安银行股票代码重叠
    if exclude_index:
        keywords = cfg.get(
            "exclude_index_keywords", DEFAULT_EXCLUDE_INDEX_KEYWORDS
        )
        name_keywords = "|".join(keywords)
        mask_name = ~result["name"].fillna("").str.contains(name_keywords, na=False)
        if "sector" in result.columns:
            sector_keywords = "指数|ETF|基金"
            mask_sector = ~result["sector"].fillna("").str.contains(
                sector_keywords, na=False
            )
            result = result[mask_name & mask_sector]
        else:
            result = result[mask_name]

    # --- 次新股排除（上市日期晚于 cutoff 日期） ---
    # cutoff 默认值与原 filter_stock_list 保持一致：'2025-01-01'
    if exclude_new_stocks:
        cutoff = cfg.get("new_stock_cutoff_date", "2025-01-01")
        # list_date 列不存在或全为 None 时跳过（避免无意义操作）
        if "list_date" in result.columns and result["list_date"].notna().any():
            # list_date 为 None 的视为老股保留
            mask_old = result["list_date"].isna() | (
                pd.to_datetime(result["list_date"]) <= pd.Timestamp(cutoff)
            )
            result = result[mask_old]

    # --- 仅保留主板 ---
    if main_board_only:
        # 深市主板：000/001/002；沪市主板：600/601/603/605
        prefixes = tuple(cfg.get("main_board_prefixes", DEFAULT_MAIN_BOARD_PREFIXES))
        result = result[result["code"].astype(str).str.startswith(prefixes)]

    result.reset_index(drop=True, inplace=True)
    return result


class DatabaseManager:
    """数据库管理类"""

    def __init__(self, connection_string: Optional[str] = None,
                 connect_timeout: Optional[int] = None,
                 statement_timeout: Optional[int] = None):
        """
        初始化数据库连接

        Args:
            connection_string: PostgreSQL 连接字符串
            connect_timeout: 建连超时秒数（None 表示沿用 libpq 默认，
                供 K 线监督者这类有整体截止时间的调用方使用）
            statement_timeout: SQL 语句超时秒数（None 表示沿用 PostgreSQL 默认）。
                通过连接 options 从建连时生效，覆盖构造期建表与迁移。
        """
        self.host = os.getenv("DB_HOST", "localhost")
        self.port = os.getenv("DB_PORT", "5432")
        self.database = os.getenv("DB_NAME", "stockfilter")
        self.user = os.getenv("DB_USER", "stockfilter_user")
        # 2026-10-18 改造：移除弱默认值，必须通过环境变量注入
        self.password = os.getenv("DB_PASSWORD")
        if not self.password:
            raise RuntimeError("必须通过环境变量 DB_PASSWORD 配置数据库密码")
        self.schema = os.getenv("DB_SCHEMA", "schema_stockfilter")

        if connection_string is None:
            connection_string = (
                f"postgresql://{self.user}:{self.password}"
                f"@{self.host}:{self.port}/{self.database}"
            )

        self.connection_string = connection_string
        self.connect_timeout = connect_timeout
        self.statement_timeout = statement_timeout
        self.conn = None
        self._connect()
        try:
            self._create_tables()
            self._migrate_add_frequency()
            # 2026-10-18 新增：幂等迁移链
            self._migrate_task_status_columns()
            self._migrate_create_scan_runs()
            self._migrate_scan_results_unique_index()
        except Exception:
            # 构造期建表/迁移同样受 statement_timeout 约束；一旦失败，必须
            # 主动释放已建立连接，避免半初始化对象遗留连接直到 GC。
            try:
                self.close()
            except Exception as close_error:
                logger.warning(f"数据库构造失败后的连接关闭也失败：{close_error}")
            raise

    def _connect(self):
        """建立数据库连接"""
        try:
            connect_kwargs = {}
            if self.connect_timeout is not None:
                connect_kwargs["connect_timeout"] = int(self.connect_timeout)
            connection_options = [f"-c search_path={self.schema}"]
            if self.statement_timeout is not None:
                connection_options.append(
                    f"-c statement_timeout={int(self.statement_timeout) * 1000}"
                )
            self.conn = psycopg2.connect(
                host=self.host,
                port=self.port,
                database=self.database,
                user=self.user,
                password=self.password,
                options=" ".join(connection_options),
                **connect_kwargs,
            )
            logger.info(
                f"PostgreSQL 数据库连接成功 ({self.host}:{self.port}/{self.database})"
            )
        except Exception as e:
            logger.error(f"PostgreSQL 数据库连接失败：{e}")
            raise

    def _create_tables(self):
        """创建数据库表结构"""
        cursor = self.conn.cursor()

        # 创建股票列表表
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS stocks (
                code VARCHAR(20) PRIMARY KEY,
                name VARCHAR(100),
                symbol VARCHAR(30),
                list_date DATE,
                sector VARCHAR(100),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        # 创建 K 线数据表（V3：增加 frequency 字段）
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS klines (
                id SERIAL PRIMARY KEY,
                code VARCHAR(20) NOT NULL,
                frequency VARCHAR(10) NOT NULL DEFAULT 'd',
                date DATE NOT NULL,
                open NUMERIC(10,2),
                high NUMERIC(10,2),
                low NUMERIC(10,2),
                close NUMERIC(10,2),
                volume BIGINT,
                amount NUMERIC(20,2),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(code, frequency, date)
            )
            """
        )

        # 创建索引
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_klines_code ON klines(code)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_klines_date ON klines(date)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_klines_frequency ON klines(frequency)"
        )

        # 创建扫描结果表
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS scan_results (
                id SERIAL PRIMARY KEY,
                scan_date DATE NOT NULL,
                code VARCHAR(20) NOT NULL,
                name VARCHAR(100),
                score NUMERIC(5,2),
                surge_date DATE,
                support_level NUMERIC(10,2),
                current_close NUMERIC(10,2),
                drop_rate NUMERIC(10,4),
                min_vol_ratio NUMERIC(10,4),
                surge_price NUMERIC(10,2),
                surge_volume_ratio NUMERIC(10,4),
                surge_pct NUMERIC(10,4),
                low_after_surge NUMERIC(10,2),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_scan_code ON scan_results(code)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_scan_date ON scan_results(scan_date)"
        )

        # 创建持仓表
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS positions (
                id SERIAL PRIMARY KEY,
                code VARCHAR(20) NOT NULL,
                name VARCHAR(100),
                entry_date DATE,
                entry_price NUMERIC(10,2),
                position_size NUMERIC(10,2),
                current_price NUMERIC(10,2),
                pnl NUMERIC(10,2),
                pnl_pct NUMERIC(10,4),
                status VARCHAR(20) DEFAULT 'open',
                exit_date DATE,
                exit_price NUMERIC(10,2),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        # 创建推送历史表
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS push_history (
                id SERIAL PRIMARY KEY,
                code VARCHAR(20) NOT NULL,
                push_date DATE NOT NULL,
                push_type VARCHAR(50),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(code, push_date)
            )
            """
        )

        self.conn.commit()
        cursor.close()
        logger.info("数据库表结构创建完成")

    def _migrate_add_frequency(self):
        """V3 迁移：为旧版 klines 表添加 frequency 字段"""
        cursor = self.conn.cursor()
        try:
            cursor.execute(
                """
                SELECT column_name FROM information_schema.columns
                WHERE table_name = 'klines' AND column_name = 'frequency'
                """
            )
            if cursor.fetchone() is None:
                logger.info("执行 V3 迁移：为 klines 表添加 frequency 字段...")
                cursor.execute(
                    """
                    ALTER TABLE klines
                    ADD COLUMN IF NOT EXISTS frequency VARCHAR(10) NOT NULL DEFAULT 'd'
                    """
                )
                cursor.execute(
                    """
                    ALTER TABLE klines
                    DROP CONSTRAINT IF EXISTS klines_code_date_key
                    """
                )
                cursor.execute(
                    """
                    ALTER TABLE klines
                    ADD CONSTRAINT klines_code_frequency_date_key
                    UNIQUE(code, frequency, date)
                    """
                )
                self.conn.commit()
                logger.info("V3 迁移完成：klines 表已添加 frequency 字段")
        except Exception as e:
            self.conn.rollback()
            logger.warning(f"V3 迁移跳过（可能已执行）：{e}")
        finally:
            cursor.close()

    # ================================================================
    # 2026-10-18 新增：幂等迁移链
    # ================================================================

    def _migrate_task_status_columns(self):
        """
        为 task_status 表新增 target_date 和 details 列（幂等）。

        task_status 表由 scheduler_kline.py 创建，此处 ALTER 不依赖其执行顺序。
        新增列统一用 TIMESTAMPTZ 或 JSONB；既有 last_completed_at 保持无时区列，
        按 UTC 解析的既有约定在文档中写明（N-1 时间戳约定）。
        """
        cursor = self.conn.cursor()
        try:
            # 先检查表是否存在（可能还没被 scheduler 创建）
            cursor.execute(
                """
                SELECT EXISTS (
                    SELECT FROM information_schema.tables
                    WHERE table_name = 'task_status'
                )
                """
            )
            table_exists = cursor.fetchone()[0]
            if not table_exists:
                logger.info("task_status 表尚未创建，跳过列扩展（scheduler 首次启动时会自动建表）")
                return

            logger.info("执行迁移：task_status 表新增 target_date / details 列...")
            cursor.execute(
                """
                ALTER TABLE schema_stockfilter.task_status
                    ADD COLUMN IF NOT EXISTS target_date DATE,
                    ADD COLUMN IF NOT EXISTS details JSONB NOT NULL DEFAULT '{}'::jsonb
                """
            )
            self.conn.commit()
            logger.info("迁移完成：task_status 已扩展 target_date / details")
        except Exception as e:
            self.conn.rollback()
            logger.warning(f"task_status 列扩展迁移跳过：{e}")
        finally:
            cursor.close()

    def _migrate_create_scan_runs(self):
        """
        新建 scan_runs 表（幂等），记录结构化扫描运行状态。

        唯一键 (signal_date, run_kind)，同一目标日的初扫和重扫互不覆盖。
        """
        cursor = self.conn.cursor()
        try:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_stockfilter.scan_runs (
                    id BIGSERIAL PRIMARY KEY,
                    signal_date DATE NOT NULL,
                    run_kind VARCHAR(20) NOT NULL,
                    status VARCHAR(32) NOT NULL,
                    stock_pool_count INTEGER NOT NULL DEFAULT 0,
                    scanned_count INTEGER NOT NULL DEFAULT 0,
                    raw_candidate_count INTEGER NOT NULL DEFAULT 0,
                    selected_count INTEGER NOT NULL DEFAULT 0,
                    filter_reason TEXT,
                    data_metrics JSONB NOT NULL DEFAULT '{}'::jsonb,
                    data_version TIMESTAMPTZ,
                    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    completed_at TIMESTAMPTZ,
                    UNIQUE (signal_date, run_kind)
                )
                """
            )
            self.conn.commit()
            logger.info("scan_runs 表已就绪")
        except Exception as e:
            self.conn.rollback()
            logger.warning(f"scan_runs 表创建跳过：{e}")
        finally:
            cursor.close()

    def _migrate_scan_results_unique_index(self):
        """
        清理 scan_results 历史重复并建立唯一索引 uq_scan_results_date_code（幂等）。

        迁移策略：
        1. 查询每个 (scan_date, code) 组，保留最新 created_at 一条，其他标记删除；
        2. 打印清理前后记录数（审计用）；
        3. CREATE UNIQUE INDEX IF NOT EXISTS（已存在则跳过）。
        """
        cursor = self.conn.cursor()
        try:
            # 检查唯一索引是否已存在
            cursor.execute(
                """
                SELECT EXISTS (
                    SELECT FROM pg_indexes
                    WHERE indexname = 'uq_scan_results_date_code'
                )
                """
            )
            idx_exists = cursor.fetchone()[0]
            if idx_exists:
                logger.info("scan_results 唯一索引 uq_scan_results_date_code 已存在，跳过迁移")
                return

            # 清理前计数
            cursor.execute("SELECT COUNT(*) FROM schema_stockfilter.scan_results")
            before_count = cursor.fetchone()[0]

            # 清理重复：每个 (scan_date, code) 保留最新 created_at 一条
            cursor.execute(
                """
                DELETE FROM schema_stockfilter.scan_results
                WHERE id IN (
                    SELECT id FROM (
                        SELECT id, ROW_NUMBER() OVER (
                            PARTITION BY scan_date, code ORDER BY created_at DESC
                        ) AS rn
                        FROM schema_stockfilter.scan_results
                    ) t WHERE t.rn > 1
                )
                """
            )
            deleted = cursor.rowcount

            # 清理后计数
            cursor.execute("SELECT COUNT(*) FROM schema_stockfilter.scan_results")
            after_count = cursor.fetchone()[0]

            if deleted > 0:
                logger.warning(
                    f"scan_results 去重：清理前 {before_count} 条，"
                    f"删除重复 {deleted} 条，剩余 {after_count} 条"
                )
            else:
                logger.info(f"scan_results 去重：无重复记录（{before_count} 条）")

            # 创建唯一索引
            cursor.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS uq_scan_results_date_code
                ON schema_stockfilter.scan_results(scan_date, code)
                """
            )
            self.conn.commit()
            logger.info("scan_results 唯一索引 uq_scan_results_date_code 已创建")
        except Exception as e:
            self.conn.rollback()
            logger.warning(f"scan_results 唯一索引迁移跳过：{e}")
        finally:
            cursor.close()

    # ================================================================
    # 股票列表相关
    # ================================================================

    def get_stock_list(
        self, filters: Optional[Dict] = None
    ) -> Optional[pd.DataFrame]:
        """
        获取股票列表（参数化 SQL 过滤 + filter_stock_pool 最终防线）。

        Args:
            filters: global.stock_pool 配置字典，支持以下键：
                exclude_st、exclude_delisting、exclude_beijing、exclude_star_market、
                exclude_chi_next、exclude_new_third_board、exclude_index、exclude_new_stocks、
                new_stock_cutoff_date、main_board_only

        Returns:
            pd.DataFrame: 过滤后的股票列表，列为 code, name, symbol, list_date, sector
        """
        query = (
            "SELECT code, name, symbol, list_date, sector FROM stocks WHERE 1=1"
        )
        params: List = []

        if filters:
            cfg = filters

            # --- ST 排除 ---
            if cfg.get("exclude_st"):
                query += " AND name NOT LIKE %s"
                params.append("%ST%")

            # --- 退市排除 ---
            if cfg.get("exclude_delisting"):
                query += " AND name NOT LIKE %s"
                params.append("%退%")

            # --- 北交所排除（8 开头） ---
            if cfg.get("exclude_beijing"):
                query += " AND code NOT LIKE %s"
                params.append("8%")

            # --- 科创板排除（688/689） ---
            if cfg.get("exclude_star_market"):
                query += " AND code NOT LIKE %s AND code NOT LIKE %s"
                params.extend(["688%", "689%"])

            # --- 创业板排除（300/301） ---
            if cfg.get("exclude_chi_next"):
                query += " AND code NOT LIKE %s AND code NOT LIKE %s"
                params.extend(["300%", "301%"])

            # --- 新三板排除（4 开头） ---
            if cfg.get("exclude_new_third_board"):
                query += " AND code NOT LIKE %s"
                params.append("4%")

            # --- 指数/ETF/基金排除（name 或 sector 含关键词） ---
            # SQL 层预过滤 + 纯函数 filter_stock_pool 最终防线
            if cfg.get("exclude_index"):
                query += (
                    " AND name NOT LIKE %s AND name NOT LIKE %s "
                    " AND name NOT LIKE %s AND name NOT LIKE %s"
                )
                params.extend(["%指数%", "%ETF%", "%基金%", "%B股%"])
                query += " AND (sector IS NULL OR sector NOT LIKE %s)"
                params.append("%指数%")
                query += " AND (sector IS NULL OR sector NOT LIKE %s)"
                params.append("%ETF%")
                query += " AND (sector IS NULL OR sector NOT LIKE %s)"
                params.append("%基金%")

            # --- 次新股排除（list_date > cutoff） ---
            if cfg.get("exclude_new_stocks") and cfg.get("new_stock_cutoff_date"):
                query += " AND (list_date IS NULL OR list_date <= %s)"
                params.append(cfg["new_stock_cutoff_date"])

            # --- 主板仅保留 ---
            if cfg.get("main_board_only"):
                query += (
                    " AND ("
                    "code LIKE %s OR code LIKE %s OR code LIKE %s OR "
                    "code LIKE %s OR code LIKE %s OR code LIKE %s OR code LIKE %s"
                    ")"
                )
                params.extend(
                    ["000%", "001%", "002%", "600%", "601%", "603%", "605%"]
                )

            # --- min_list_days（参数化替代原 f-string 拼 SQL） ---
            min_days = cfg.get("min_list_days")
            if min_days:
                query += (
                    " AND (list_date IS NULL OR "
                    "list_date <= CURRENT_DATE - INTERVAL %s DAY)"
                )
                params.append(str(min_days))

        try:
            df = pd.read_sql_query(query, self.conn, params=params or None)
        except Exception as e:
            logger.warning(f"数据库股票池查询失败：{e}")
            return None

        # 最终防线：DataFrame 级过滤，确保与固定 CSV 路径语义一致
        if filters:
            df = filter_stock_pool(df, filters)

        return df

    def save_stock_list(self, df: pd.DataFrame) -> None:
        """保存股票列表"""
        if df is None or df.empty:
            return

        cursor = self.conn.cursor()

        for _, row in df.iterrows():
            cursor.execute(
                """
                INSERT INTO stocks (code, name, symbol, list_date, sector)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (code) DO UPDATE SET
                    name = EXCLUDED.name,
                    symbol = EXCLUDED.symbol,
                    list_date = EXCLUDED.list_date,
                    sector = EXCLUDED.sector,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    row["code"],
                    row["name"],
                    row["symbol"],
                    row.get("list_date"),
                    row.get("sector"),
                ),
            )

        self.conn.commit()
        cursor.close()
        logger.info(f"保存 {len(df)} 只股票到数据库")

    def insert_stocks(self, rows: List[Dict]) -> None:
        """
        批量插入/更新股票列表（测试辅助接口）。

        与 save_stock_list 等价但接受 list[dict]，方便 pytest fixture 构造。

        Args:
            rows: 股票列表，每行需含 code, name；可选 symbol, list_date, sector
        """
        if not rows:
            return

        # 构造 DataFrame 复用 save_stock_list
        df = pd.DataFrame(rows)
        self.save_stock_list(df)

    # ================================================================
    # K 线相关
    # ================================================================

    def get_kline_history(
        self, code: str, days: int = 120, frequency: str = "d"
    ) -> Optional[pd.DataFrame]:
        """
        获取 K 线历史数据

        Args:
            code: 股票代码
            days: 获取天数
            frequency: K 线频率（d/w/m），默认日线

        Returns:
            Optional[pd.DataFrame]: K 线数据
        """
        query = """
            SELECT date, open, high, low, close, volume, amount
            FROM klines
            WHERE code = %s AND frequency = %s
            ORDER BY date DESC
            LIMIT %s
        """
        df = pd.read_sql_query(query, self.conn, params=(code, frequency, days))
        if df.empty:
            return None
        df["date"] = pd.to_datetime(df["date"])
        df.sort_values("date", inplace=True)
        df.reset_index(drop=True, inplace=True)
        return df

    def get_latest_kline_date(
        self, code: str, frequency: str = "d"
    ) -> Optional[str]:
        """获取指定股票最新 K 线数据的日期"""
        query = """
            SELECT MAX(date) as latest_date
            FROM klines
            WHERE code = %s AND frequency = %s
        """
        cursor = self.conn.cursor()
        cursor.execute(query, (code, frequency))
        result = cursor.fetchone()
        cursor.close()

        if result and result[0]:
            if hasattr(result[0], "strftime"):
                return result[0].strftime("%Y-%m-%d")
            return str(result[0])
        return None

    def save_kline_history(
        self, code: str, df: pd.DataFrame, frequency: str = "d"
    ) -> None:
        """
        保存 K 线历史数据

        Args:
            code: 股票代码
            df: K 线 DataFrame
            frequency: K 线频率（d/w/m）
        """
        if df is None or df.empty:
            return

        cursor = self.conn.cursor()

        for _, row in df.iterrows():
            volume = row["volume"]
            if pd.isna(volume):
                volume = 0
            else:
                try:
                    volume = int(volume)
                    if volume > BIGINT_MAX:
                        volume = BIGINT_MAX
                    elif volume < BIGINT_MIN:
                        volume = BIGINT_MIN
                except (ValueError, OverflowError):
                    volume = 0

            amount = row.get("amount", 0)
            if pd.isna(amount):
                amount = 0

            cursor.execute(
                """
                INSERT INTO klines
                    (code, frequency, date, open, high, low, close, volume, amount)
                VALUES
                    (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (code, frequency, date) DO UPDATE SET
                    open = EXCLUDED.open,
                    high = EXCLUDED.high,
                    low = EXCLUDED.low,
                    close = EXCLUDED.close,
                    volume = EXCLUDED.volume,
                    amount = EXCLUDED.amount
                """,
                (
                    code,
                    frequency,
                    row["date"].strftime("%Y-%m-%d"),
                    row["open"],
                    row["high"],
                    row["low"],
                    row["close"],
                    volume,
                    amount,
                ),
            )

        self.conn.commit()
        cursor.close()
        logger.debug(f"{code} 保存 {len(df)} 条 K 线数据（{frequency}）")

    # ================================================================
    # 扫描结果相关
    # ================================================================

    def save_scan_result(self, scan_date: str, results: List[Dict]) -> None:
        """
        保存扫描结果（2026-10-18 改造：UPSERT 语义）。

        依赖 scan_results 表的唯一索引 uq_scan_results_date_code
        （由 _migrate_scan_results_unique_index 在初始化时创建）。
        同一 (scan_date, code) 重复写入时覆盖 score 等本次扫描字段。

        Args:
            scan_date: 扫描日期（YYYY-MM-DD）
            results: 扫描结果列表，每项为字典
        """
        if not results:
            return

        cursor = self.conn.cursor()

        for result in results:
            cursor.execute(
                """
                INSERT INTO scan_results (
                    scan_date, code, name, score, surge_date, support_level,
                    current_close, drop_rate, min_vol_ratio, surge_price,
                    surge_volume_ratio, surge_pct, low_after_surge
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                )
                ON CONFLICT (scan_date, code) DO UPDATE SET
                    name = EXCLUDED.name,
                    score = EXCLUDED.score,
                    surge_date = EXCLUDED.surge_date,
                    support_level = EXCLUDED.support_level,
                    current_close = EXCLUDED.current_close,
                    drop_rate = EXCLUDED.drop_rate,
                    min_vol_ratio = EXCLUDED.min_vol_ratio,
                    surge_price = EXCLUDED.surge_price,
                    surge_volume_ratio = EXCLUDED.surge_volume_ratio,
                    surge_pct = EXCLUDED.surge_pct,
                    low_after_surge = EXCLUDED.low_after_surge,
                    created_at = CURRENT_TIMESTAMP
                """,
                (
                    scan_date,
                    result.get("code"),
                    result.get("name"),
                    result.get("score"),
                    result.get("surge_date"),
                    result.get("support_level"),
                    result.get("current_close"),
                    result.get("drop_rate"),
                    result.get("min_vol_ratio"),
                    result.get("surge_price"),
                    result.get("surge_volume_ratio"),
                    result.get("surge_pct"),
                    result.get("low_after_surge"),
                ),
            )

        self.conn.commit()
        cursor.close()
        logger.info(f"UPSERT {len(results)} 条扫描结果（scan_date={scan_date}）")

    def fetch_scan_results(
        self, scan_date: str, code: Optional[str] = None
    ) -> List[Dict]:
        """
        查询扫描结果（测试与验收辅助接口）。

        Args:
            scan_date: 扫描日期（YYYY-MM-DD）
            code: 可选，限定单只股票

        Returns:
            List[Dict]: 每行一个字典，包含 scan_results 表的所有列
        """
        query = (
            "SELECT id, scan_date, code, name, score, surge_date, "
            "support_level, current_close, drop_rate, min_vol_ratio, "
            "surge_price, surge_volume_ratio, surge_pct, low_after_surge, "
            "created_at FROM scan_results WHERE scan_date = %s"
        )
        params: List = [scan_date]
        if code:
            query += " AND code = %s"
            params.append(code)
        query += " ORDER BY code"

        cursor = self.conn.cursor()
        cursor.execute(query, tuple(params))
        columns = [desc[0] for desc in cursor.description]
        rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
        cursor.close()
        return rows

    # ================================================================
    # 推送历史相关
    # ================================================================

    def has_pushed(
        self, code: str, push_date: str, push_type: str = "daily_scan"
    ) -> bool:
        """
        按 code + push_date + push_type 三列查询 push_history，
        判断某条推送是否已执行（持久化幂等）。

        与 has_pushed_today（仅 code + push_date）的区别：
        三列精确匹配支持同一只股票同日不同推送类型的独立判定。

        Args:
            code: 推送对象代码（股票代码或 __daily_summary__ 等特殊标识）
            push_date: 推送日期（YYYY-MM-DD）
            push_type: 推送类型（daily_scan / obpc_summary 等）

        Returns:
            bool: 已推送返回 True
        """
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT COUNT(*) FROM push_history
            WHERE code = %s AND push_date = %s AND push_type = %s
            """,
            (code, push_date, push_type),
        )
        result = cursor.fetchone()
        cursor.close()
        return (result[0] if result else 0) > 0

    def has_pushed_today(self, code: str, today: Optional[str] = None) -> bool:
        """检查某只股票今日是否已推送（仅 code + push_date 匹配，兼容旧接口）"""
        if today is None:
            from datetime import datetime

            today = datetime.now().strftime("%Y-%m-%d")
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT COUNT(*) FROM push_history WHERE code = %s AND push_date = %s",
            (code, today),
        )
        result = cursor.fetchone()
        cursor.close()
        return result[0] > 0

    def save_push_history(
        self, code: str, push_date: str, push_type: str = "daily_scan"
    ) -> None:
        """保存推送历史"""
        cursor = self.conn.cursor()
        cursor.execute(
            """
            INSERT INTO push_history (code, push_date, push_type)
            VALUES (%s, %s, %s)
            ON CONFLICT (code, push_date) DO NOTHING
            """,
            (code, push_date, push_type),
        )
        self.conn.commit()
        cursor.close()

    def insert_push_record(
        self, code: str, push_date: str, push_type: str = "new_signal"
    ) -> None:
        """插入推送记录（兼容旧接口）"""
        self.save_push_history(code, push_date, push_type)

    # ================================================================
    # scan_runs：结构化扫描运行状态
    # ================================================================

    def upsert_scan_run(
        self,
        signal_date: str,
        run_kind: str,
        status: str,
        stock_pool_count: int = 0,
        scanned_count: int = 0,
        raw_candidate_count: int = 0,
        selected_count: int = 0,
        filter_reason: Optional[str] = None,
        data_metrics: Optional[Dict] = None,
        data_version=None,
        completed_at=None,
    ) -> None:
        """
        幂等写入 scan_runs（INSERT ... ON CONFLICT DO UPDATE）。

        同一 (signal_date, run_kind) 重复调用会覆盖 status 等字段，
        初扫 run_kind='initial'、重扫 run_kind='rescan' 互不干扰。

        Args:
            signal_date: 目标交易日（YYYY-MM-DD）
            run_kind: 'initial'（初扫）或 'rescan'（重扫）
            status: 见文档 4 节六种状态
            stock_pool_count: 股票池规模
            scanned_count: 实际扫描股票数
            raw_candidate_count: 原始形态候选数
            selected_count: 最终信号数
            filter_reason: 过滤原因（market 拦截原因 / stale timeout 等）
            data_metrics: 本次运行相关的数据完整性指标（JSON dict）
            data_version: 本次运行依据的 K 线/补全任务的 last_completed_at
            completed_at: 手动指定完成时间；None 时用 NOW()
        """
        from datetime import datetime, timezone

        if run_kind not in ("initial", "rescan"):
            logger.warning(
                f"scan_runs run_kind 超出约定值 'initial'/'rescan'：{run_kind}"
            )

        cursor = self.conn.cursor()
        try:
            data_metrics_json = (
                json.dumps(data_metrics, ensure_ascii=False)
                if data_metrics
                else "{}"
            )

            completed_at_val = completed_at if completed_at is not None else datetime.now(timezone.utc)

            cursor.execute(
                """
                INSERT INTO scan_runs (
                    signal_date, run_kind, status, stock_pool_count,
                    scanned_count, raw_candidate_count, selected_count,
                    filter_reason, data_metrics, data_version, completed_at
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s
                )
                ON CONFLICT (signal_date, run_kind) DO UPDATE SET
                    status = EXCLUDED.status,
                    stock_pool_count = EXCLUDED.stock_pool_count,
                    scanned_count = EXCLUDED.scanned_count,
                    raw_candidate_count = EXCLUDED.raw_candidate_count,
                    selected_count = EXCLUDED.selected_count,
                    filter_reason = EXCLUDED.filter_reason,
                    data_metrics = EXCLUDED.data_metrics,
                    data_version = EXCLUDED.data_version,
                    completed_at = EXCLUDED.completed_at
                """,
                (
                    signal_date,
                    run_kind,
                    status,
                    stock_pool_count,
                    scanned_count,
                    raw_candidate_count,
                    selected_count,
                    filter_reason,
                    data_metrics_json,
                    data_version,
                    completed_at_val,
                ),
            )
            self.conn.commit()
            logger.info(
                f"scan_runs UPSERT：signal_date={signal_date} run_kind={run_kind} "
                f"status={status} raw={raw_candidate_count} selected={selected_count}"
            )
        except Exception as e:
            self.conn.rollback()
            logger.error(f"scan_runs UPSERT 失败：{e}")
            raise
        finally:
            cursor.close()

    def get_scan_run(
        self, signal_date: str, run_kind: Optional[str] = None
    ) -> Optional[Dict]:
        """
        查询 scan_runs 记录。

        Args:
            signal_date: 目标交易日
            run_kind: 可选；None 时返回该 signal_date 最近一次运行

        Returns:
            Optional[Dict]: 行字典（列名与表一致）；无记录返回 None
        """
        cursor = self.conn.cursor()
        try:
            if run_kind is None:
                # 返回该 signal_date 最近的一次运行（按 started_at 降序）
                cursor.execute(
                    """
                    SELECT * FROM scan_runs WHERE signal_date = %s
                    ORDER BY started_at DESC LIMIT 1
                    """,
                    (signal_date,),
                )
            else:
                cursor.execute(
                    """
                    SELECT * FROM scan_runs
                    WHERE signal_date = %s AND run_kind = %s
                    """,
                    (signal_date, run_kind),
                )

            row = cursor.fetchone()
            if row is None:
                return None
            columns = [desc[0] for desc in cursor.description]
            result = dict(zip(columns, row))
            # data_metrics 是 JSONB，psycopg2 会自动解包，但保险起见转一下
            if isinstance(result.get("data_metrics"), str):
                try:
                    result["data_metrics"] = json.loads(result["data_metrics"])
                except (json.JSONDecodeError, TypeError):
                    pass
            return result
        finally:
            cursor.close()

    def get_scan_runs_by_status(
        self, status: str, limit: int = 20
    ) -> List[Dict]:
        """
        按状态查询 scan_runs（辅助监控/重扫判定）。

        Args:
            status: 目标状态
            limit: 返回条数上限

        Returns:
            List[Dict]: 符合条件的运行记录（按 started_at 降序）
        """
        cursor = self.conn.cursor()
        try:
            cursor.execute(
                """
                SELECT * FROM scan_runs WHERE status = %s
                ORDER BY started_at DESC LIMIT %s
                """,
                (status, limit),
            )
            columns = [desc[0] for desc in cursor.description]
            rows = []
            for row in cursor.fetchall():
                item = dict(zip(columns, row))
                if isinstance(item.get("data_metrics"), str):
                    try:
                        item["data_metrics"] = json.loads(item["data_metrics"])
                    except (json.JSONDecodeError, TypeError):
                        pass
                rows.append(item)
            return rows
        finally:
            cursor.close()

    def mark_stale_scan_runs_failed(self, stale_after_seconds: int) -> int:
        """
        将超时未收敛的 running 记录置为 failed（卡死收敛）。

        调度器在启动扫描前调用，防止上一轮被 SIGKILL 后的僵死记录永远卡在 running。
        使用参数化 interval，禁止 SQL 字符串拼接。

        Args:
            stale_after_seconds: 超过多少秒未结束视为超时

        Returns:
            int: 被更新的行数
        """
        cursor = self.conn.cursor()
        try:
            # 构造参数化 interval（安全，不直接拼字符串）
            cursor.execute(
                """
                UPDATE scan_runs
                SET status = 'failed',
                    filter_reason = 'stale_running_timeout',
                    completed_at = NOW()
                WHERE status = 'running'
                  AND started_at < NOW() - (%s::text || ' seconds')::interval
                """,
                (str(stale_after_seconds),),
            )
            affected = cursor.rowcount
            self.conn.commit()
            if affected > 0:
                logger.warning(
                    f"mark_stale_scan_runs_failed：收敛 {affected} 条超时 running 记录"
                )
            else:
                logger.debug("mark_stale_scan_runs_failed：无超时 running 记录")
            return affected
        except Exception as e:
            self.conn.rollback()
            logger.error(f"mark_stale_scan_runs_failed 失败：{e}")
            raise
        finally:
            cursor.close()

    # ================================================================
    # 其它（保持旧接口）
    # ================================================================

    def get_last_signal_date(self, code: str) -> Optional[str]:
        """获取某只股票最近一次信号日期"""
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT MAX(scan_date) FROM scan_results WHERE code = %s",
            (code,),
        )
        result = cursor.fetchone()
        cursor.close()
        if result and result[0]:
            return str(result[0])
        return None

    def get_signal_count_this_year(
        self, code: str, year: Optional[int] = None
    ) -> int:
        """获取某只股票今年的信号次数"""
        if year is None:
            from datetime import datetime

            year = datetime.now().year
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT COUNT(*) FROM scan_results WHERE code = %s AND EXTRACT(YEAR FROM scan_date) = %s",
            (code, year),
        )
        result = cursor.fetchone()
        cursor.close()
        return result[0] if result else 0

    def get_signal_count_this_month(
        self, year_month: str, code: Optional[str] = None
    ) -> int:
        """
        获取指定月份的信号数量（用于单月信号上限检查）

        与 get_signal_count_this_year 的区别：
            - get_signal_count_this_year: 按单股票查询年度信号数
            - get_signal_count_this_month: 可查询全市场或单股票的当月信号数

        Args:
            year_month: 月份字符串，格式 YYYY-MM
            code: 股票代码，为 None 时返回全市场当月信号总数

        Returns:
            int: 当月信号数
        """
        cursor = self.conn.cursor()
        try:
            if code is None:
                cursor.execute(
                    "SELECT COUNT(*) FROM scan_results "
                    "WHERE TO_CHAR(scan_date, 'YYYY-MM') = %s",
                    (year_month,),
                )
            else:
                cursor.execute(
                    "SELECT COUNT(*) FROM scan_results "
                    "WHERE code = %s AND TO_CHAR(scan_date, 'YYYY-MM') = %s",
                    (code, year_month),
                )
            result = cursor.fetchone()
            return result[0] if result else 0
        except Exception as e:
            logger.warning(f"查询当月信号数失败（{year_month}）：{e}")
            return 0
        finally:
            cursor.close()

    def get_index_kline(
        self, index_code: str, days: int = 30
    ) -> Optional[pd.DataFrame]:
        """获取指数K线数据（用于大盘环境过滤）"""
        code = index_code.replace(".SH", "").replace(".SZ", "")
        try:
            query = """
                SELECT date, open, high, low, close, volume, amount
                FROM klines
                WHERE code = %s AND frequency = 'd'
                ORDER BY date DESC
                LIMIT %s
            """
            df = pd.read_sql_query(query, self.conn, params=(code, days))
            if df.empty:
                return None
            df["date"] = pd.to_datetime(df["date"])
            df = df.sort_values("date").reset_index(drop=True)
            return df
        except Exception as e:
            logger.warning(f"获取指数 {index_code} K线失败：{e}")
            return None

    def close(self) -> None:
        """关闭数据库连接"""
        if self.conn:
            self.conn.close()
            logger.info("数据库连接已关闭")
