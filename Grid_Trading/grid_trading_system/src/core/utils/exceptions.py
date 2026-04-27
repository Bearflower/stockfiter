"""
自定义异常类

异常层次结构：
    GridTradingError
    ├── BinanceAPIError: 币安 API 调用错误
    │   ├── APIError: API 响应错误
    │   ├── NetworkError: 网络连接错误
    │   └── RateLimitError: 频率限制错误
    ├── GridConfigError: 网格配置错误
    ├── InsufficientFundsError: 资金不足错误
    └── OrderExecutionError: 订单执行错误
"""


class GridTradingError(Exception):
    """网格交易基础异常"""
    def __init__(self, message: str, code: int = None):
        self.message = message
        self.code = code
        super().__init__(self.message)


class ExchangeError(GridTradingError):
    """交易所相关异常基类"""
    pass


class APIError(ExchangeError):
    """API 调用错误（币安返回非 200 响应）"""
    pass


class NetworkError(ExchangeError):
    """网络连接错误（超时、连接断开等）"""
    pass


class RateLimitError(ExchangeError):
    """频率限制错误（HTTP 429）"""
    pass


class BinanceAPIError(GridTradingError):
    """币安API异常（兼容旧接口，已迁移至 APIError）"""
    def __init__(self, message: str, code: int = None):
        super().__init__(message)
        self.code = code


class GridConfigError(GridTradingError):
    """网格配置异常"""
    pass


class InsufficientFundsError(GridTradingError):
    """资金不足异常"""
    pass


class OrderExecutionError(GridTradingError):
    """订单执行异常"""
    pass
