"""
配置加载模块

从 config.yaml 加载配置，配置文件不存在时使用内置默认值。
自动将相对路径转换为基于项目根目录的绝对路径。
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
        "provider": "openai",
        "api_key_env": "OPENAI_API_KEY",
        "base_url": "",
        "model": "gpt-4o-mini",
        "deep_model": "gpt-4o",
        "temperature": 0.3,
        "max_tokens": 2000,
    },
    "rate_limit": {
        "delay_between_articles": 2,
        "batch_size": 5,
        "batch_pause": 10,
        "max_retries": 3,
        "retry_base_delay": 5,
    },
    "paths": {
        "blog_dir": "docs/blog",
        "output_dir": "docs/distilled",
        "state_file": "docs/distilled/.state.json",
        "log_dir": "docs/distilled/logs",
    },
    "truncation": {
        "max_chars": 6000,
    },
    "deep_analysis": {
        "max_articles_per_batch": 100,
    },
    "opinion_extraction": {
        "max_source_chars": 8000,
        "temperature": 0.2,
        "max_tokens": 4000,
        "topic_tags": [
            "估值判断", "仓位管理", "逆向投资", "风险控制",
            "资产配置", "投资心法", "市场周期", "策略执行",
        ],
        "market_condition_tags": [
            "泡沫高估", "高估区", "正常区", "低估区", "钻石坑",
            "市场恐慌", "市场狂热", "通用",
        ],
    },
    "content_classification": {
        "transaction_keywords": ["长赢.*投资计划", "ETF计划", "文字发车"],
        "weibo_keywords": ["微博精选", "微博"],
    },
    "product_opinion": {
        "enabled": True,
        "target_products": [
            "红利", "中证500", "医药", "消费", "恒生", "科创50",
            "创业板", "信息", "金融", "环保", "证券", "传媒",
            "黄金", "原油", "债券", "可转债", "中概", "恒科",
        ],
        "min_product_opinions": 3,
    },
    "persona": {
        "max_observations_in_prompt": 15,
        "max_operations_in_prompt": 10,
        "recent_months": 3,
        "opinion_output_merged": True,
    },
    "persona_builder": {
        "max_source_chars": 8000,
        "temperature": 0.3,
        "max_tokens": 8192,
    },
    "persona_query": {
        "temperature": 0.4,
        "max_tokens": 4096,
    },
    "position_extraction": {
        "fache_keywords": ["ETF计划", "长赢指数投资计划", "长赢投资计划", "文字发车"],
        "filter_mode": "filename_and_content",
        "fund_code_priority": "场外",
        "output_markdown": "docs/distilled/持仓还原表.md",
        "output_csv": "docs/distilled/持仓还原表.csv",
    },
    "meta": {
        "meta_file": "docs/distilled/.meta.yaml",
    },
    "vision": {
        "api_key_env": "OPENAI_API_KEY",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "temperature": 0.1,
        "max_tokens": 3000,
    },
    "persona_index": {
        "output_subdir": "persona_index",
        "dedup_field": "opinion",
        "structured_index": True,
        "vector_index": True,
        "embedding_model": "paraphrase-multilingual-MiniLM-L12-v2",
        "top_k": 20,
    },
    "stats": {
        "fund_top_k": 10,
        "yield_ranges": ["0-20%", "20-50%", "50-100%", ">100%", "亏损", "未知"],
    },
}

# 模块级缓存，避免重复加载
_config_cache: dict[str, Any] | None = None
_project_root: str | None = None


def _get_project_root() -> str:
    """获取项目根目录。

    通过 config.yaml 所在位置向上两级确定：
    scripts/distill/config.yaml -> 项目根目录

    Returns:
        str: 项目根目录的绝对路径
    """
    global _project_root
    if _project_root is not None:
        return _project_root

    # config.py 所在目录为 scripts/distill/，向上两级即为项目根目录
    config_dir = os.path.dirname(os.path.abspath(__file__))
    _project_root = os.path.dirname(os.path.dirname(config_dir))
    return _project_root


def _resolve_paths(config: dict[str, Any]) -> dict[str, Any]:
    """将配置中 paths 节的相对路径转换为绝对路径。

    Args:
        config: 原始配置字典

    Returns:
        dict: 路径已转换为绝对路径的配置字典
    """
    root = _get_project_root()
    paths = config.get("paths", {})
    for key, value in paths.items():
        if isinstance(value, str) and not os.path.isabs(value):
            paths[key] = os.path.join(root, value)

    # 解析 meta 节中的 meta_file 路径
    meta = config.get("meta", {})
    meta_file = meta.get("meta_file", "")
    if isinstance(meta_file, str) and not os.path.isabs(meta_file):
        meta["meta_file"] = os.path.join(root, meta_file)

    return config


def _load_yaml_config(config_path: str) -> dict[str, Any] | None:
    """从 YAML 文件加载配置。

    Args:
        config_path: config.yaml 的绝对路径

    Returns:
        dict | None: 加载成功返回配置字典，文件不存在返回 None
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


def get_config(config_path: str | None = None, force_reload: bool = False) -> dict[str, Any]:
    """获取配置字典。

    加载策略：
    1. 先读取 config.yaml 中的用户配置
    2. 与内置默认配置深度合并（用户配置优先）
    3. 将 paths 中的相对路径转换为绝对路径

    Args:
        config_path: config.yaml 的路径，默认使用 scripts/distill/config.yaml
        force_reload: 是否强制重新加载，忽略缓存

    Returns:
        dict: 完整的配置字典，所有路径均为绝对路径
    """
    global _config_cache

    if _config_cache is not None and not force_reload:
        return _config_cache

    if config_path is None:
        config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.yaml")

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