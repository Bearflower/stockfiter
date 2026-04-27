#!/usr/bin/env python3
"""
信号检测模块兼容性导入

为了保持向后兼容，从 core/signal/detector.py 重新导出所需的类和函数
"""

from .signal.detector import SignalDetector, get_signal_detector

__all__ = ['SignalDetector', 'get_signal_detector']
