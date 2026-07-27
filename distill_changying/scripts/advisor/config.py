"""
E大投资决策助手配置加载模块

从 config.yaml 加载配置，配置文件不存在或解析失败时使用内置默认值。
自动将 cache.path、knowledge_base.* 等相对路径转换为基于项目根目录的绝对路径。
"""

from __future__ import annotations

import os
import logging
from typing import Any

import yaml

logger = logging.getLogger(__name__)

# 内置默认配置，与 config.yaml 保持同步
_DEFAULT_CONFIG: dict[str, Any] = {
    "api": {
        "provider": "deepseek",
        "api_key_env": "DEEPSEEK_API_KEY",
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-chat",
        "temperature": 0.7,
        "max_tokens": 2000,
        "max_retries": 3,
        "retry_base_delay": 5,
    },
    "valuation": {
        "thresholds": {
            "diamond_pit": 10,
            "undervalued": 30,
            "normal": 70,
            "overvalued": 90,
            "bubble": 100,
        },
        "confidence_thresholds": {
            "high": 0.8,
            "medium": 0.5,
        },
        "position_mapping": {
            "diamond_pit": {"stock": 80, "bond": 10, "cash": 10},
            "undervalued": {"stock": 60, "bond": 25, "cash": 15},
            "normal": {"stock": 40, "bond": 40, "cash": 20},
            "overvalued": {"stock": 20, "bond": 50, "cash": 30},
            "bubble": {"stock": 5, "bond": 35, "cash": 60},
        },
    },
    "indices": [
        {"code": "000001", "name": "上证指数"},
        {"code": "399001", "name": "深证成指"},
        {"code": "000300", "name": "沪深300"},
        {"code": "000905", "name": "中证500"},
        {"code": "399006", "name": "创业板指"},
        {"code": "000016", "name": "上证50"},
    ],
    "cache": {
        "ttl_hours": 6,
        "path": "docs/distilled/logs/valuation_cache.json",
    },
    "knowledge_base": {
        "core_veins": "docs/distilled/核心观点矿脉.md",
        "golden_quotes": "docs/distilled/金句库.md",
    },
    "compression": {
        "max_chars_per_section": 300,
        "max_total_tokens": 3000,
    },
    "chat": {
        "max_history_rounds": 10,
        "summary_trigger_rounds": 8,
    },
    "disclaimer": "以上分析仅供参考，不构成投资建议。投资有风险，决策须谨慎。",
    "opinion_matching": {
        "max_opinions_chars": 8000,
        "temperature": 0.3,
        "max_tokens": 1000,
        "opinions_path": "",
        "fallback_opinions": [
            {
                "opinion": "投资是概率游戏，而非预测游戏。放弃预测涨跌，专注于计算不同情况下的胜率和赔率。",
                "relevance_reason": "当前市场需要理性决策",
            },
            {
                "opinion": "仓位管理是风险控制的核心。不空仓、不满仓，根据估值动态调整。",
                "relevance_reason": "当前估值水平需要调整仓位",
            },
        ],
    },
    "llm_analysis": {
        "enabled": True,
        "model": "deepseek-v4-pro",
        "thinking_mode": True,
        "reasoning_effort": "high",
        "temperature": None,
        "max_tokens": 4000,
        "timeout": 120,
        "fallback_to_rules": True,
    },
}

# 模块级缓存，避免重复加载
_config_cache: dict[str, Any] | None = None
_project_root: str | None = None


def _get_project_root() -> str:
    """获取项目根目录。

    通过 config.py 所在位置向上两级确定：
    scripts/advisor/config.py -> scripts/ -> 项目根目录

    Returns:
        str: 项目根目录的绝对路径
    """
    global _project_root
    if _project_root is not None:
        return _project_root

    # config.py 所在目录为 scripts/advisor/，向上两级即为项目根目录
    config_dir = os.path.dirname(os.path.abspath(__file__))
    _project_root = os.path.dirname(os.path.dirname(config_dir))
    return _project_root


def _resolve_path(config: dict[str, Any], section: str, key: str) -> None:
    """将配置中指定节下的相对路径字段转换为绝对路径。

    Args:
        config: 配置字典（原地修改）
        section: 配置节名称
        key: 路径字段名
    """
    root = _get_project_root()
    value = config.get(section, {}).get(key)
    if isinstance(value, str) and not os.path.isabs(value):
        config[section][key] = os.path.join(root, value)


def _resolve_paths(config: dict[str, Any]) -> dict[str, Any]:
    """将配置中所有需要解析的相对路径转换为绝对路径。

    需要解析的路径字段：
    - cache.path: 缓存文件路径
    - knowledge_base.core_veins: 核心观点矿脉文件路径
    - knowledge_base.golden_quotes: 金句库文件路径

    Args:
        config: 原始配置字典

    Returns:
        dict: 路径已转换为绝对路径的配置字典
    """
    _resolve_path(config, "cache", "path")
    _resolve_path(config, "knowledge_base", "core_veins")
    _resolve_path(config, "knowledge_base", "golden_quotes")
    return config


def _load_yaml_config(config_path: str) -> dict[str, Any] | None:
    """从 YAML 文件加载配置。

    Args:
        config_path: config.yaml 的绝对路径

    Returns:
        dict | None: 加载成功返回配置字典，文件不存在或解析失败返回 None
    """
    if not os.path.isfile(config_path):
        logger.warning("配置文件 %s 不存在，将使用内置默认配置", config_path)
        return None

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)
        if config is None:
            logger.warning("配置文件 %s 内容为空，将使用内置默认配置", config_path)
            return None
        logger.info("成功加载配置文件: %s", config_path)
        return config
    except yaml.YAMLError as e:
        logger.error("解析配置文件 %s 失败: %s，将使用内置默认配置", config_path, e)
        return None


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """深度合并两个字典，override 中的值覆盖 base 中的值。

    Args:
        base: 基础字典（默认配置）
        override: 覆盖字典（用户配置）

    Returns:
        dict: 合并后的新字典
    """
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def get_config(force_reload: bool = False) -> dict[str, Any]:
    """获取配置字典。

    加载策略：
    1. 先读取 scripts/advisor/config.yaml 中的用户配置
    2. 与内置默认配置深度合并（用户配置优先）
    3. 将 cache.path、knowledge_base.* 中的相对路径转换为绝对路径

    Args:
        force_reload: 是否强制重新加载，忽略模块级缓存

    Returns:
        dict: 完整的配置字典，所有路径均为绝对路径
    """
    global _config_cache

    if _config_cache is not None and not force_reload:
        return _config_cache

    config_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "config.yaml"
    )

    user_config = _load_yaml_config(config_path)
    if user_config is not None:
        config = _deep_merge(_DEFAULT_CONFIG, user_config)
    else:
        config = _DEFAULT_CONFIG.copy()

    config = _resolve_paths(config)
    _config_cache = config
    return config


def get_project_root() -> str:
    """获取项目根目录的绝对路径。

    Returns:
        str: 项目根目录绝对路径
    """
    return _get_project_root()