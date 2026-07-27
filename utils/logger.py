"""
日志管理模块
使用 loguru 提供统一的日志输出，支持控制台和文件双通道
"""

import sys
import os as _os
from loguru import logger
import yaml


def setup_logger(config_path: str = "config/config.yaml") -> 'loguru.Logger':
    """从配置文件加载日志配置并初始化 loguru"""
    log_config = {}

    if _os.path.exists(config_path):
        with open(config_path, "r", encoding="utf-8") as f:
            full_config = yaml.safe_load(f)
            log_config = full_config.get("global", {}).get("logging", {})

    log_level = log_config.get("level", "INFO")
    log_file = log_config.get("file", "logs/stockfilter_v3.log")
    max_bytes = log_config.get("max_bytes", 10 * 1024 * 1024)
    backup_count = log_config.get("backup_count", 5)

    logger.remove()

    logger.add(
        sys.stdout,
        level=log_level,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
        colorize=True
    )

    log_dir = _os.path.dirname(log_file)
    if log_dir and not _os.path.exists(log_dir):
        _os.makedirs(log_dir, exist_ok=True)

    logger.add(
        log_file,
        level=log_level,
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}",
        rotation=max_bytes,
        retention=f"{backup_count} days",
        compression="zip",
        encoding="utf-8"
    )

    return logger


def get_logger() -> 'loguru.Logger':
    """获取全局 logger 实例"""
    return logger