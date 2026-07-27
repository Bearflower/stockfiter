"""
统一 K 线服务
封装 K 线数据获取、缓存、查询的统一入口
支持远程 K 线服务 + 本地 Baostock 降级
"""

from typing import Optional, Dict, List

import requests
import pandas as pd

from utils.logger import get_logger
from data.database import DatabaseManager
from data.fetcher import fetch_and_cache_kline

logger = get_logger()


class KlineService:
    """
    统一 K 线服务
    优先使用远程 K 线服务 API，降级为本地 Baostock 获取
    """

    def __init__(
        self,
        db: DatabaseManager,
        remote_url: Optional[str] = None,
        project: str = "stockfilter"
    ):
        """
        初始化 K 线服务

        Args:
            db: 数据库管理器
            remote_url: 远程 K 线服务地址
            project: 项目标识
        """
        self.db = db
        self.remote_url = remote_url
        self.project = project

        if self.remote_url:
            logger.info(f"K 线服务：远程地址 {self.remote_url}")
        else:
            logger.info("K 线服务：使用本地 Baostock 数据源")

    def get_daily_kline(
        self, code: str, symbol: str, days: int = 120
    ) -> Optional[pd.DataFrame]:
        """
        获取日 K 线数据（先查缓存，再获取）

        Args:
            code: 股票代码（不带后缀）
            symbol: 股票代码（带后缀）
            days: 回溯天数

        Returns:
            Optional[pd.DataFrame]: K 线数据
        """
        # 1. 从本地数据库缓存读取
        cached = self.db.get_kline_history(code, days, 'd')
        if cached is not None and len(cached) > 0:
            logger.debug(f"{code} 从缓存加载 {len(cached)} 条日 K 线")
            return cached

        # 2. 尝试远程 K 线服务
        if self.remote_url:
            df = self._fetch_from_remote(code, 'd', days)
            if df is not None and len(df) > 0:
                self.db.save_kline_history(code, df, 'd')
                return df

        # 3. 降级为本地 Baostock
        logger.debug(f"{code} 降级为本地 Baostock 获取")
        return fetch_and_cache_kline(self.db, symbol, code, days, 'd')

    def _fetch_from_remote(
        self, code: str, frequency: str, days: int
    ) -> Optional[pd.DataFrame]:
        """
        从远程 K 线服务获取数据

        Args:
            code: 股票代码
            frequency: K 线频率
            days: 回溯天数

        Returns:
            Optional[pd.DataFrame]: K 线数据
        """
        try:
            response = requests.post(
                f"{self.remote_url}/kline",
                json={
                    "project": self.project,
                    "code": code,
                    "frequency": frequency,
                    "days": days,
                },
                timeout=30
            )
            if response.status_code == 200:
                result = response.json()
                if result.get('code') == 0 and result.get('data'):
                    data = result['data']
                    if isinstance(data, list) and len(data) > 0:
                        df = pd.DataFrame(data)
                        if 'date' in df.columns:
                            df['date'] = pd.to_datetime(df['date'])
                            df.sort_values('date', inplace=True)
                            df.reset_index(drop=True, inplace=True)
                        return df
            logger.debug(f"远程 K 线服务返回空数据: {code}")
            return None
        except Exception as e:
            logger.warning(f"远程 K 线服务调用失败: {e}")
            return None

    def batch_load_klines(
        self, stocks: List[Dict], days: int = 120
    ) -> Dict[str, pd.DataFrame]:
        """
        批量加载 K 线数据

        Args:
            stocks: 股票列表，每项包含 code, symbol
            days: 回溯天数

        Returns:
            Dict[str, pd.DataFrame]: {code: DataFrame}
        """
        result = {}
        total = len(stocks)

        for idx, stock in enumerate(stocks):
            code = stock['code']
            symbol = stock['symbol']
            try:
                df = self.get_daily_kline(code, symbol, days)
                if df is not None and len(df) > 0:
                    result[code] = df
            except Exception as e:
                logger.error(f"{code} K 线加载失败: {e}")

            if (idx + 1) % 100 == 0:
                logger.info(
                    f"K 线批量加载进度: {idx + 1}/{total}，已加载: {len(result)}"
                )

        logger.info(f"K 线批量加载完成: {len(result)}/{total}")
        return result