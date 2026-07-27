"""
数据库管理模块
负责 PostgreSQL 数据库的连接、表结构创建、CRUD 操作封装

V3 变更：klines 表增加 frequency 字段，唯一约束改为 (code, frequency, date)
"""

import os
from typing import Optional, List, Dict
from datetime import datetime

import psycopg2
import pandas as pd

from utils.logger import get_logger

logger = get_logger()

# PostgreSQL BIGINT 范围常量
BIGINT_MAX = 9223372036854775807
BIGINT_MIN = -9223372036854775808


class DatabaseManager:
    """数据库管理类"""

    def __init__(self, connection_string: Optional[str] = None):
        """
        初始化数据库连接

        Args:
            connection_string: PostgreSQL 连接字符串
        """
        self.host = os.getenv('DB_HOST', 'localhost')
        self.port = os.getenv('DB_PORT', '5432')
        self.database = os.getenv('DB_NAME', 'stockfilter')
        self.user = os.getenv('DB_USER', 'stockfilter_user')
        self.password = os.getenv('DB_PASSWORD', 'Stock@2024')
        self.schema = os.getenv('DB_SCHEMA', 'schema_stockfilter')

        if connection_string is None:
            connection_string = (
                f"postgresql://{self.user}:{self.password}"
                f"@{self.host}:{self.port}/{self.database}"
            )

        self.connection_string = connection_string
        self.conn = None
        self._connect()
        self._create_tables()
        self._migrate_add_frequency()

    def _connect(self):
        """建立数据库连接"""
        try:
            self.conn = psycopg2.connect(
                host=self.host,
                port=self.port,
                database=self.database,
                user=self.user,
                password=self.password,
                options=f'-c search_path={self.schema}'
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
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS stocks (
                code VARCHAR(20) PRIMARY KEY,
                name VARCHAR(100),
                symbol VARCHAR(30),
                list_date DATE,
                sector VARCHAR(100),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # 创建 K 线数据表（V3：增加 frequency 字段）
        cursor.execute("""
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
        """)

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
        cursor.execute("""
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
        """)

        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_scan_code ON scan_results(code)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_scan_date ON scan_results(scan_date)"
        )

        # 创建持仓表
        cursor.execute("""
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
        """)

        # 创建推送历史表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS push_history (
                id SERIAL PRIMARY KEY,
                code VARCHAR(20) NOT NULL,
                push_date DATE NOT NULL,
                push_type VARCHAR(50),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(code, push_date)
            )
        """)

        self.conn.commit()
        cursor.close()
        logger.info("数据库表结构创建完成")

    def _migrate_add_frequency(self):
        """
        V3 迁移：为旧版 klines 表添加 frequency 字段
        """
        cursor = self.conn.cursor()
        try:
            cursor.execute("""
                SELECT column_name FROM information_schema.columns
                WHERE table_name = 'klines' AND column_name = 'frequency'
            """)
            if cursor.fetchone() is None:
                logger.info("执行 V3 迁移：为 klines 表添加 frequency 字段...")
                cursor.execute("""
                    ALTER TABLE klines
                    ADD COLUMN IF NOT EXISTS frequency VARCHAR(10) NOT NULL DEFAULT 'd'
                """)
                cursor.execute("""
                    ALTER TABLE klines
                    DROP CONSTRAINT IF EXISTS klines_code_date_key
                """)
                cursor.execute("""
                    ALTER TABLE klines
                    ADD CONSTRAINT klines_code_frequency_date_key
                    UNIQUE(code, frequency, date)
                """)
                self.conn.commit()
                logger.info("V3 迁移完成：klines 表已添加 frequency 字段")
        except Exception as e:
            self.conn.rollback()
            logger.warning(f"V3 迁移跳过（可能已执行）：{e}")
        finally:
            cursor.close()

    def get_stock_list(self, filters: Optional[Dict] = None) -> Optional[pd.DataFrame]:
        """获取股票列表"""
        query = "SELECT code, name, symbol, list_date, sector FROM stocks WHERE 1=1"

        if filters:
            if filters.get('exclude_st'):
                query += " AND name NOT LIKE '%ST%'"
            if filters.get('exclude_beijing'):
                query += " AND code NOT LIKE '8%'"
            if filters.get('min_list_days'):
                days = filters.get('min_list_days')
                query += (
                    f" AND (list_date IS NULL OR "
                    f"list_date <= CURRENT_DATE - INTERVAL '{days} days')"
                )

        df = pd.read_sql_query(query, self.conn)
        return df

    def save_stock_list(self, df: pd.DataFrame) -> None:
        """保存股票列表"""
        if df is None or df.empty:
            return

        cursor = self.conn.cursor()

        for _, row in df.iterrows():
            cursor.execute("""
                INSERT INTO stocks (code, name, symbol, list_date, sector)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (code) DO UPDATE SET
                    name = EXCLUDED.name,
                    symbol = EXCLUDED.symbol,
                    list_date = EXCLUDED.list_date,
                    sector = EXCLUDED.sector,
                    updated_at = CURRENT_TIMESTAMP
            """, (
                row['code'], row['name'], row['symbol'],
                row.get('list_date'), row.get('sector')
            ))

        self.conn.commit()
        cursor.close()
        logger.info(f"保存 {len(df)} 只股票到数据库")

    def get_kline_history(
        self, code: str, days: int = 120, frequency: str = 'd'
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
        df['date'] = pd.to_datetime(df['date'])
        df.sort_values('date', inplace=True)
        df.reset_index(drop=True, inplace=True)
        return df

    def get_latest_kline_date(
        self, code: str, frequency: str = 'd'
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
            if hasattr(result[0], 'strftime'):
                return result[0].strftime('%Y-%m-%d')
            return str(result[0])
        return None

    def save_kline_history(
        self, code: str, df: pd.DataFrame, frequency: str = 'd'
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
            volume = row['volume']
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

            amount = row.get('amount', 0)
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
                    code, frequency,
                    row['date'].strftime('%Y-%m-%d'),
                    row['open'], row['high'], row['low'],
                    row['close'], volume, amount
                )
            )

        self.conn.commit()
        cursor.close()
        logger.debug(f"{code} 保存 {len(df)} 条 K 线数据（{frequency}）")

    def save_scan_result(self, scan_date: str, results: List[Dict]) -> None:
        """保存扫描结果"""
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
                """,
                (
                    scan_date, result.get('code'), result.get('name'),
                    result.get('score'), result.get('surge_date'),
                    result.get('support_level'), result.get('current_close'),
                    result.get('drop_rate'), result.get('min_vol_ratio'),
                    result.get('surge_price'), result.get('surge_volume_ratio'),
                    result.get('surge_pct'), result.get('low_after_surge')
                )
            )

        self.conn.commit()
        cursor.close()
        logger.info(f"保存 {len(results)} 条扫描结果")

    def has_pushed_today(self, code: str, today: Optional[str] = None) -> bool:
        """检查某只股票今日是否已推送"""
        if today is None:
            today = datetime.now().strftime('%Y-%m-%d')
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT COUNT(*) FROM push_history WHERE code = %s AND push_date = %s",
            (code, today)
        )
        result = cursor.fetchone()
        cursor.close()
        return result[0] > 0

    def save_push_history(
        self, code: str, push_date: str, push_type: str = 'daily_scan'
    ) -> None:
        """保存推送历史"""
        cursor = self.conn.cursor()
        cursor.execute(
            """
            INSERT INTO push_history (code, push_date, push_type)
            VALUES (%s, %s, %s)
            ON CONFLICT (code, push_date) DO NOTHING
            """,
            (code, push_date, push_type)
        )
        self.conn.commit()
        cursor.close()

    def insert_push_record(
        self, code: str, push_date: str, push_type: str = 'new_signal'
    ) -> None:
        """插入推送记录（兼容旧接口）"""
        self.save_push_history(code, push_date, push_type)

    def get_last_signal_date(self, code: str) -> Optional[str]:
        """获取某只股票最近一次信号日期"""
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT MAX(scan_date) FROM scan_results WHERE code = %s",
            (code,)
        )
        result = cursor.fetchone()
        cursor.close()
        if result and result[0]:
            return str(result[0])
        return None

    def get_signal_count_this_year(self, code: str, year: Optional[int] = None) -> int:
        """获取某只股票今年的信号次数"""
        if year is None:
            year = datetime.now().year
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT COUNT(*) FROM scan_results WHERE code = %s AND EXTRACT(YEAR FROM scan_date) = %s",
            (code, year)
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
                # 查询全市场当月信号总数
                cursor.execute(
                    "SELECT COUNT(*) FROM scan_results "
                    "WHERE TO_CHAR(scan_date, 'YYYY-MM') = %s",
                    (year_month,)
                )
            else:
                # 查询指定股票当月信号数
                cursor.execute(
                    "SELECT COUNT(*) FROM scan_results "
                    "WHERE code = %s AND TO_CHAR(scan_date, 'YYYY-MM') = %s",
                    (code, year_month)
                )
            result = cursor.fetchone()
            return result[0] if result else 0
        except Exception as e:
            logger.warning(f"查询当月信号数失败（{year_month}）：{e}")
            return 0
        finally:
            cursor.close()

    def get_index_kline(self, index_code: str, days: int = 30) -> Optional[pd.DataFrame]:
        """获取指数K线数据（用于大盘环境过滤）"""
        # 将 000300.SH 转为数据库中的代码格式
        code = index_code.replace('.SH', '').replace('.SZ', '')
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
            df['date'] = pd.to_datetime(df['date'])
            df = df.sort_values('date').reset_index(drop=True)
            return df
        except Exception as e:
            logger.warning(f"获取指数 {index_code} K线失败：{e}")
            return None

    def close(self) -> None:
        """关闭数据库连接"""
        if self.conn:
            self.conn.close()
            logger.info("数据库连接已关闭")