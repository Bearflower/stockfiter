"""
通用工具函数
"""
from typing import Any
import asyncio
from datetime import datetime


def format_timestamp(ts: int) -> str:
    """
    格式化时间戳为可读字符串

    Args:
        ts: 毫秒时间戳

    Returns:
        格式化时间字符串
    """
    return datetime.fromtimestamp(ts / 1000).strftime("%Y-%m-%d %H:%M:%S")


async def retry_with_backoff(func, max_retries: int = 3, base_delay: float = 1.0):
    """
    带退避的重试机制

    Args:
        func: 要执行的异步函数
        max_retries: 最大重试次数
        base_delay: 基础延迟时间（秒）

    Returns:
        函数执行结果
    """
    for attempt in range(max_retries):
        try:
            return await func()
        except Exception as e:
            if attempt == max_retries - 1:
                raise
            delay = base_delay * (2 ** attempt)
            print(f"第 {attempt + 1} 次尝试失败，{delay:.1f}秒后重试: {e}")
            await asyncio.sleep(delay)
