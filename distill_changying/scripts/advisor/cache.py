"""
估值数据缓存模块

基于本地 JSON 文件实现估值数据缓存，避免短时间内重复请求 baostock API。
缓存包含时间戳和 TTL 过期机制，使用原子写入保证数据一致性。

缓存文件 JSON 结构：
{
    "timestamp": "2026-05-22T10:00:00",
    "data": {...}
}
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

# 默认 TTL 小时数，当配置中未指定时使用
_DEFAULT_TTL_HOURS = 6


class ValuationCache:
    """估值数据本地缓存。

    以 JSON 文件形式持久化，支持 TTL 过期检查和原子写入。

    Attributes:
        _cache_path: 缓存文件绝对路径
        _ttl_hours: 缓存有效期（小时）
    """

    def __init__(self, config: dict[str, Any]) -> None:
        """初始化缓存模块。

        从 config["cache"]["path"] 读取缓存文件路径，
        从 config["cache"]["ttl_hours"] 读取 TTL。

        Args:
            config: 完整配置字典（已由 config.get_config() 完成路径解析）
        """
        cache_section = config.get("cache", {})
        self._cache_path: str = cache_section.get("path", "")
        self._ttl_hours: int = cache_section.get("ttl_hours", _DEFAULT_TTL_HOURS)

        if not self._cache_path:
            logger.warning("缓存路径未配置，缓存功能将不可用")

        logger.info(
            "估值缓存已初始化: path=%s, ttl=%d小时",
            self._cache_path,
            self._ttl_hours,
        )

    def get(self) -> dict[str, Any] | None:
        """获取缓存数据。

        检查流程：
        1. 缓存文件不存在 -> 返回 None
        2. JSON 解析失败 -> 返回 None
        3. TTL 已过期 -> 返回 None
        4. 上述检查均通过 -> 返回 data 字段内容

        Returns:
            dict | None: 缓存数据字典，无有效缓存时返回 None
        """
        if not self._cache_path:
            logger.debug("缓存路径为空，跳过读取")
            return None

        if not os.path.isfile(self._cache_path):
            logger.debug("缓存文件不存在: %s", self._cache_path)
            return None

        try:
            with open(self._cache_path, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("缓存文件损坏或无法读取: %s，错误: %s", self._cache_path, e)
            return None

        if not isinstance(raw, dict) or "timestamp" not in raw or "data" not in raw:
            logger.warning("缓存文件格式异常，缺少必要字段: %s", self._cache_path)
            return None

        # 检查 TTL
        try:
            cached_time = datetime.fromisoformat(raw["timestamp"])
        except (ValueError, TypeError):
            logger.warning("缓存时间戳格式异常: %s", raw.get("timestamp"))
            return None

        now = datetime.now(timezone.utc)
        # 确保 cached_time 也有时区信息，若无则视为 UTC
        if cached_time.tzinfo is None:
            cached_time = cached_time.replace(tzinfo=timezone.utc)

        elapsed = now - cached_time
        ttl = timedelta(hours=self._ttl_hours)

        if elapsed > ttl:
            logger.info(
                "缓存已过期: 距今 %.1f 小时（TTL=%d小时）",
                elapsed.total_seconds() / 3600,
                self._ttl_hours,
            )
            return None

        logger.info(
            "缓存命中: 距今 %.1f 小时（TTL=%d小时）",
            elapsed.total_seconds() / 3600,
            self._ttl_hours,
        )
        return raw["data"]

    def set(self, data: dict[str, Any]) -> None:
        """写入缓存数据。

        使用原子写入策略：先写入 .tmp 临时文件，再 os.replace() 到正式文件。
        自动确保父目录存在。

        写入的 JSON 结构：
        {
            "timestamp": "2026-05-22T10:00:00+00:00",
            "data": {用户数据}
        }

        Args:
            data: 待缓存的估值数据字典
        """
        if not self._cache_path:
            logger.warning("缓存路径为空，跳过写入")
            return

        parent_dir = os.path.dirname(self._cache_path)
        os.makedirs(parent_dir, exist_ok=True)

        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "data": data,
        }

        tmp_file = self._cache_path + ".tmp"
        try:
            with open(tmp_file, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            os.replace(tmp_file, self._cache_path)
            logger.info("缓存已写入: %s（%d 个顶层字段）", self._cache_path, len(data))
        except OSError as e:
            logger.error("写入缓存文件失败 %s: %s", self._cache_path, e)
            # 清理临时文件
            if os.path.exists(tmp_file):
                try:
                    os.remove(tmp_file)
                except OSError:
                    pass

    def is_valid(self) -> bool:
        """检查缓存是否存在且未过期。

        Returns:
            bool: 缓存有效返回 True，否则返回 False
        """
        return self.get() is not None

    def clear(self) -> None:
        """清除缓存文件。

        安全删除：仅在文件存在时执行删除操作。
        """
        if not self._cache_path:
            logger.debug("缓存路径为空，跳过清除")
            return

        if os.path.isfile(self._cache_path):
            try:
                os.remove(self._cache_path)
                logger.info("缓存已清除: %s", self._cache_path)
            except OSError as e:
                logger.error("清除缓存文件失败 %s: %s", self._cache_path, e)
        else:
            logger.debug("缓存文件不存在，无需清除: %s", self._cache_path)