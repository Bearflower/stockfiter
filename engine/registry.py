"""
策略注册表
管理所有已注册的策略，支持按名称查找、列出、启用/禁用
"""

from typing import Dict, List, Optional
from strategy.base import BaseStrategy
from utils.logger import get_logger

logger = get_logger()


class StrategyRegistry:
    """
    策略注册表（单例模式）
    全局唯一的策略注册中心，负责策略的注册、查找和管理
    """

    _instance: Optional['StrategyRegistry'] = None

    def __new__(cls) -> 'StrategyRegistry':
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._strategies: Dict[str, BaseStrategy] = {}
            cls._instance._disabled: Dict[str, bool] = {}
        return cls._instance

    def register(self, strategy: BaseStrategy, enabled: bool = True) -> None:
        """
        注册策略

        Args:
            strategy: 策略实例
            enabled: 是否默认启用
        """
        key = strategy.name
        if key in self._strategies:
            logger.warning(f"策略 [{key}] 已存在，将被覆盖")
        self._strategies[key] = strategy
        self._disabled[key] = not enabled
        logger.info(f"策略注册成功：[{key}] v{strategy.version}（{'启用' if enabled else '禁用'}）")

    def unregister(self, name: str) -> None:
        """
        注销策略

        Args:
            name: 策略名称
        """
        self._strategies.pop(name, None)
        self._disabled.pop(name, None)
        logger.info(f"策略已注销：[{name}]")

    def get(self, name: str) -> Optional[BaseStrategy]:
        """
        按名称获取策略

        Args:
            name: 策略名称

        Returns:
            Optional[BaseStrategy]: 策略实例或 None
        """
        return self._strategies.get(name)

    def list_all(self) -> List[BaseStrategy]:
        """列出所有已注册的策略"""
        return list(self._strategies.values())

    def list_enabled(self) -> List[BaseStrategy]:
        """列出所有已启用的策略"""
        return [
            s for name, s in self._strategies.items()
            if not self._disabled.get(name, True)
        ]

    def is_enabled(self, name: str) -> bool:
        """
        判断策略是否启用

        Args:
            name: 策略名称

        Returns:
            bool: 是否启用
        """
        return not self._disabled.get(name, True)

    def enable(self, name: str) -> None:
        """启用策略"""
        if name in self._strategies:
            self._disabled[name] = False
            logger.info(f"策略已启用：[{name}]")

    def disable(self, name: str) -> None:
        """禁用策略"""
        if name in self._strategies:
            self._disabled[name] = True
            logger.info(f"策略已禁用：[{name}]")

    def clear(self) -> None:
        """清空注册表"""
        self._strategies.clear()
        self._disabled.clear()
        logger.info("策略注册表已清空")

    @property
    def strategy_count(self) -> int:
        """已注册策略数量"""
        return len(self._strategies)

    @property
    def enabled_count(self) -> int:
        """已启用策略数量"""
        return len(self.list_enabled())


# 全局注册表单例
_registry = StrategyRegistry()


def get_registry() -> 'StrategyRegistry':
    """获取全局策略注册表"""
    return _registry