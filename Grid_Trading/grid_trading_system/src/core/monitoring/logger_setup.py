"""
日志配置模块
提供结构化日志配置，支持文件和控制台双输出、日志轮转、级别可配置
"""

import logging
import os
import sys
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Dict, Optional


class LoggerSetup:
    """日志配置管理器
    
    特性：
    - 结构化日志格式（时间、级别、模块名、消息）
    - 文件和控制台双输出
    - 日志文件自动轮转（默认10MB）
    - 日志级别可配置
    - 自动创建日志目录
    """
    
    _initialized = False
    _loggers: Dict[str, logging.Logger] = {}
    
    # 日志格式
    LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
    DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
    
    @classmethod
    def initialize(
        cls,
        log_level: str = "INFO",
        log_file: Optional[str] = None,
        max_size_mb: int = 10,
        backup_count: int = 5,
        log_dir: Optional[str] = None
    ) -> None:
        """
        初始化日志系统
        
        Args:
            log_level: 日志级别（DEBUG, INFO, WARNING, ERROR, CRITICAL）
            log_file: 日志文件路径（None 则使用默认路径）
            max_size_mb: 单个日志文件最大大小（MB）
            backup_count: 保留的备份文件数量
            log_dir: 日志目录（None 则使用项目根目录下的 logs/）
        """
        if cls._initialized:
            return
        
        # 确定日志文件路径
        if log_file is None:
            if log_dir is None:
                log_dir = cls._get_default_log_dir()
            os.makedirs(log_dir, exist_ok=True)
            log_file = os.path.join(log_dir, "grid_trading.log")
        
        # 确保日志目录存在
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        
        # 配置根日志记录器
        root_logger = logging.getLogger()
        level = getattr(logging, log_level.upper(), logging.INFO)
        root_logger.setLevel(level)
        
        # 清除现有处理器（避免重复添加）
        root_logger.handlers.clear()
        
        # 创建格式化器
        formatter = logging.Formatter(
            fmt=cls.LOG_FORMAT,
            datefmt=cls.DATE_FORMAT
        )
        
        # 控制台处理器
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(level)
        console_handler.setFormatter(formatter)
        root_logger.addHandler(console_handler)
        
        # 文件处理器（带轮转）
        max_size = max_size_mb * 1024 * 1024  # 转换为字节
        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=max_size,
            backupCount=backup_count,
            encoding='utf-8'
        )
        file_handler.setLevel(level)
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)
        
        cls._initialized = True
        
        # 预定义常用 logger
        cls._loggers = {
            'main': cls.get_logger('main'),
            'trading': cls.get_logger('trading'),
            'strategy': cls.get_logger('strategy'),
            'execution': cls.get_logger('execution'),
            'monitoring': cls.get_logger('monitoring'),
            'risk': cls.get_logger('risk'),
            'data': cls.get_logger('data'),
        }
        
        logging.info(f"日志系统初始化完成：{log_file}, 级别: {log_level}")
    
    @classmethod
    def get_logger(cls, name: str) -> logging.Logger:
        """
        获取指定名称的 logger
        
        Args:
            name: logger 名称
            
        Returns:
            Logger 实例
        """
        if name in cls._loggers:
            return cls._loggers[name]
        return logging.getLogger(name)
    
    @classmethod
    def set_level(cls, level: str, logger_name: Optional[str] = None) -> None:
        """
        设置日志级别
        
        Args:
            level: 日志级别（DEBUG, INFO, WARNING, ERROR, CRITICAL）
            logger_name: logger 名称（None 表示设置所有）
        """
        target_level = getattr(logging, level.upper(), logging.INFO)
        if logger_name:
            logging.getLogger(logger_name).setLevel(target_level)
        else:
            logging.getLogger().setLevel(target_level)
            logging.info(f"全局日志级别已设置为：{level}")
    
    @classmethod
    def _get_default_log_dir(cls) -> str:
        """
        获取默认日志目录
        
        Returns:
            日志目录路径
        """
        return os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
            'logs'
        )
    
    # ====== 结构化日志记录方法 ======
    
    @classmethod
    def log_trade(
        cls,
        symbol: str,
        side: str,
        price: float,
        quantity: float,
        pnl: Optional[float] = None
    ) -> None:
        """
        记录交易日志
        
        Args:
            symbol: 交易对
            side: 方向（BUY/SELL）
            price: 价格
            quantity: 数量
            pnl: 盈亏（可选）
        """
        logger = cls.get_logger('trade')
        if pnl is not None:
            logger.info(
                f"TRADE | {symbol} | {side} | "
                f"price={price} | qty={quantity} | pnl={pnl:.2f}"
            )
        else:
            logger.info(
                f"TRADE | {symbol} | {side} | "
                f"price={price} | qty={quantity}"
            )
    
    @classmethod
    def log_grid_event(
        cls,
        event_type: str,
        grid_id: str,
        details: str
    ) -> None:
        """
        记录网格事件日志
        
        Args:
            event_type: 事件类型
            grid_id: 网格 ID
            details: 详细信息
        """
        logger = cls.get_logger('grid')
        logger.info(f"GRID | {event_type} | {grid_id} | {details}")
    
    @classmethod
    def log_risk_event(
        cls,
        event_type: str,
        trigger_price: float,
        trigger_pnl: float,
        action: str
    ) -> None:
        """
        记录风险事件日志
        
        Args:
            event_type: 事件类型
            trigger_price: 触发价格
            trigger_pnl: 触发盈亏
            action: 执行行动
        """
        logger = cls.get_logger('risk')
        logger.warning(
            f"RISK | {event_type} | price={trigger_price} | "
            f"pnl={trigger_pnl:.2%} | action={action}"
        )
    
    @classmethod
    def log_system_status(
        cls,
        market_state: str,
        price: float,
        atr: float,
        adx: float,
        total_pnl: float
    ) -> None:
        """
        记录系统状态日志
        
        Args:
            market_state: 市场状态
            price: 价格
            atr: ATR 值
            adx: ADX 值
            total_pnl: 总盈亏
        """
        logger = cls.get_logger('status')
        logger.info(
            f"STATUS | state={market_state} | price={price} | "
            f"atr={atr:.2f} | adx={adx:.2f} | pnl={total_pnl:.2%}"
        )


# ====== 便捷函数 ======

def setup_logger(
    name: str = "grid_trading",
    level: str = "INFO",
    log_file: Optional[str] = None,
    max_size_mb: int = 10
) -> logging.Logger:
    """
    便捷函数：快速设置日志
    
    Args:
        name: 日志名称
        level: 日志级别
        log_file: 日志文件路径
        max_size_mb: 单个日志文件最大大小（MB）
        
    Returns:
        Logger 实例
    """
    LoggerSetup.initialize(
        log_level=level,
        log_file=log_file,
        max_size_mb=max_size_mb
    )
    return LoggerSetup.get_logger(name)


def get_logger(name: str) -> logging.Logger:
    """
    便捷函数：获取 logger
    
    Args:
        name: logger 名称
        
    Returns:
        Logger 实例
    """
    return LoggerSetup.get_logger(name)
