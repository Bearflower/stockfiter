"""
状态管理模块

负责加载和保存处理状态，追踪每篇博客是否已处理。
状态以 JSON 文件持久化，格式为：

{
    "file_identifier": {
        "summary": "核心观点摘要",
        "keywords": ["关键词1", "关键词2"],
        "topic": "主题分类",
        "processed_at": "2026-05-22T10:30:00",
        "operations": [...],       // 可选：发车帖操作记录
        "weibo_opinions": [...],   // 可选：微博精选观点
        "product_opinions": [...]  // 可选：品种观点
    }
}
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


def load_state(state_file: str) -> dict[str, Any]:
    """从 JSON 文件加载处理状态。

    Args:
        state_file: 状态文件绝对路径

    Returns:
        dict: 处理状态字典，文件不存在时返回空字典
    """
    if not os.path.isfile(state_file):
        logger.info("状态文件不存在，将从头开始处理: %s", state_file)
        return {}

    try:
        with open(state_file, "r", encoding="utf-8") as f:
            state = json.load(f)
        logger.info(
            "成功加载状态文件: %s（已处理 %d 篇）",
            state_file,
            len(state),
        )
        return state
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("状态文件 %s 损坏或无法读取: %s，将从头开始处理", state_file, e)
        return {}


def save_state(state_file: str, state: dict[str, Any]) -> None:
    """保存处理状态到 JSON 文件。

    自动确保父目录存在。使用原子写入策略：先写入临时文件，再重命名。

    Args:
        state_file: 状态文件绝对路径
        state: 处理状态字典
    """
    parent_dir = os.path.dirname(state_file)
    os.makedirs(parent_dir, exist_ok=True)

    tmp_file = state_file + ".tmp"
    try:
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        os.replace(tmp_file, state_file)
        logger.debug("状态已保存: %s（共 %d 条记录）", state_file, len(state))
    except OSError as e:
        logger.error("保存状态文件失败 %s: %s", state_file, e)
        # 清理临时文件
        if os.path.exists(tmp_file):
            os.remove(tmp_file)


def is_processed(file_identifier: str, state: dict[str, Any]) -> bool:
    """检查某个文件是否已在状态中标记为已处理。

    Args:
        file_identifier: 文件唯一标识符
        state: 处理状态字典

    Returns:
        bool: 已处理返回 True，否则返回 False
    """
    return file_identifier in state


def mark_processed(
    file_identifier: str,
    result: dict[str, Any],
    state: dict[str, Any],
) -> dict[str, Any]:
    """标记文件已处理，将结果存入状态字典。

    自动添加 processed_at 时间戳（ISO 格式，北京时间）。
    自动保存 result 中的额外字段（如 operations、weibo_opinions、product_opinions）。

    Args:
        file_identifier: 文件唯一标识符
        result: 摘要结果字典，包含 summary、keywords、topic，
                可选的 operations（发车帖）、weibo_opinions（微博精选）、
                product_opinions（品种观点）
        state: 当前处理状态字典（会被原地修改）

    Returns:
        dict: 更新后的状态字典（与入参是同一个对象）
    """
    entry: dict[str, Any] = {
        "summary": result.get("summary", ""),
        "keywords": result.get("keywords", []),
        "topic": result.get("topic", ""),
        "processed_at": datetime.now(timezone.utc).isoformat(),
    }
    # 存储额外字段（发车帖操作记录、微博观点、品种观点等）
    if "operations" in result:
        entry["operations"] = result["operations"]
    if "weibo_opinions" in result:
        entry["weibo_opinions"] = result["weibo_opinions"]
    if "product_opinions" in result:
        entry["product_opinions"] = result["product_opinions"]
    state[file_identifier] = entry
    return state