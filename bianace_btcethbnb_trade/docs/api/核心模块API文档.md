# 核心模块 API 文档

## 目录

1. [仓位计算模块](#仓位计算模块)
2. [风险管理模块](#风险管理模块)
3. [订单生成模块](#订单生成模块)
4. [信号检测模块](#信号检测模块)
5. [应急处理模块](#应急处理模块)
6. [数据缓存模块](#数据缓存模块)
7. [评分引擎模块](#评分引擎模块)

---

## 仓位计算模块

**模块路径**: `core.position_calculator`

### 主要类

#### `PositionCalculator`

仓位计算器类，负责计算交易仓位参数。

**初始化参数**:
- `params`: StrategyParams - 策略参数对象（可选）

**主要方法**:

##### `calculate_position(symbol, entry_price, stop_loss_price, direction, signal_grade='A')`

计算仓位参数（核心功能）。

**参数**:
- `symbol` (str): 交易对
- `entry_price` (Decimal): 开仓价
- `stop_loss_price` (Decimal): 止损价
- `direction` (int): 方向（1=多，-1=空）
- `signal_grade` (str): 信号等级（S/A/B，默认A）

**返回值**:
```python
{
    'symbol': str,                    # 交易对
    'entry_price': Decimal,           # 开仓价
    'stop_loss_price': Decimal,       # 止损价
    'stop_loss_pct': Decimal,         # 止损百分比
    'direction': int,                 # 方向
    'signal_grade': str,              # 信号等级
    'base_notional_value': Decimal,   # 基础名义价值（U）
    'actual_notional_value': Decimal, # 实际名义价值（U）
    'position_coefficient': Decimal,  # 仓位系数
    'quantity': Decimal,              # 合约数量
    'margin': Decimal,                # 保证金（U）
    'leverage': int,                  # 实际使用杠杆
    'risk_amount': Decimal,           # 风险金额（U）
    'risk_ratio': Decimal             # 风险占比（%）
}
```

**示例**:
```python
from core.position_calculator import calculate_position
from decimal import Decimal

position = calculate_position(
    symbol='BTCUSDT',
    entry_price=Decimal('95000'),
    stop_loss_price=Decimal('93000'),
    direction=1,
    signal_grade='A'
)

print(f"保证金: {position['margin']}U")
print(f"杠杆: {position['leverage']}x")
print(f"合约数量: {position['quantity']}")
```

---

## 风险管理模块

**模块路径**: `core.risk_manager`

### 主要类

#### `RiskManager`

风险管理器类，负责止损止盈计算和风险监控。

**主要方法**:

##### `calculate_stop_loss(entry_price, direction, stop_loss_pct)`

计算止损价。

**参数**:
- `entry_price` (Decimal): 开仓价
- `direction` (int): 方向（1=多，-1=空）
- `stop_loss_pct` (Decimal): 止损幅度（百分比）

**返回值**: Decimal - 止损价

**示例**:
```python
from core.risk_manager import calculate_stop_loss
from decimal import Decimal

stop_loss = calculate_stop_loss(
    entry_price=Decimal('95000'),
    direction=1,
    stop_loss_pct=Decimal('0.02')
)
# 输出: 93100.00
```

##### `calculate_take_profit_levels(entry_price, direction, atr14, signal_grade='A')`

计算止盈水平。

**参数**:
- `entry_price` (Decimal): 开仓价
- `direction` (int): 方向（1=多，-1=空）
- `atr14` (Decimal): ATR14值
- `signal_grade` (str): 信号等级（默认A）

**返回值**:
```python
[
    {
        'level': 'TP1',
        'price': Decimal,      # TP1价格
        'ratio': Decimal,      # 平仓比例
        'description': str,    # 描述
        'multiplier': Decimal  # ATR倍数
    },
    # TP2, TP3...
]
```

**示例**:
```python
from core.risk_manager import calculate_take_profit_levels
from decimal import Decimal

tp_levels = calculate_take_profit_levels(
    entry_price=Decimal('95000'),
    direction=1,
    r_value=Decimal('500'),  # ATR14值
    signal_grade='A'
)

for tp in tp_levels:
    print(f"{tp['level']}: {tp['price']} ({tp['description']})")
```

##### `check_margin_ratio(account_equity, used_margin)`

检查保证金率。

**参数**:
- `account_equity` (Decimal): 账户权益
- `used_margin` (Decimal): 占用保证金

**返回值**: `(margin_ratio, risk_level, need_intervention)`
- `margin_ratio` (Decimal): 保证金率
- `risk_level` (str): 风险等级（SAFE/WARNING/EMERGENCY）
- `need_intervention` (bool): 是否需要干预

---

## 订单生成模块

**模块路径**: `core.order_generator`

### 主要函数

#### `generate_order_template(symbol, direction, entry_price, stop_loss_price, signal_grade, position_data)`

生成订单模板。

**参数**:
- `symbol` (str): 交易对
- `direction` (int): 方向（1=多，-1=空）
- `entry_price` (Decimal): 开仓价
- `stop_loss_price` (Decimal): 止损价
- `signal_grade` (str): 信号等级
- `position_data` (dict): 仓位数据

**返回值**:
```python
{
    'symbol': str,
    'direction': str,           # 'LONG' 或 'SHORT'
    'entry_price': Decimal,
    'stop_loss_price': Decimal,
    'take_profit_levels': list,
    'leverage': int,
    'quantity': Decimal,
    'margin': Decimal
}
```

---

## 信号检测模块

**模块路径**: `core.signal.detector`

### 主要类

#### `SignalDetector`

信号检测器类，负责检测交易信号。

**初始化参数**:
- `params`: StrategyParams - 策略参数对象（可选）
- `data_fetcher`: MarketDataFetcher - 数据获取器（可选）

**主要方法**:

##### `detect_signals(symbols=None)`

检测交易信号。

**参数**:
- `symbols` (list): 交易对列表，默认 ['BTCUSDT', 'ETHUSDT', 'BNBUSDT']

**返回值**:
```python
[
    {
        '币种': str,
        '开仓方向': str,      # '多' 或 '空'
        '开仓推荐度': int,    # 评分（v5.5 新增）
        '信号等级': str,      # 'S', 'A', 'B', 'C'
        'score': int,         # 总分（v5.5 新增）
        'score_detail': dict, # 评分明细（v5.5 新增）
        '开仓价': float,      # Decimal 转换为 float
        '强平价': float,      # 或 None
        '止损价': float,
        '止盈设置': dict,
        '保证金': float,
        '实际杠杆': int,
        '风险占比': str,      # 格式化百分比字符串
        '通过检查清单': bool,
        '备注': str,
        'suggested_position_ratio': float, # 建议仓位系数（v5.5 新增）
    }
]
```

##### `_determine_signal_grade(symbol, data, direction)`

判定信号等级（v5.5 新增，使用评分引擎）。

**参数**:
- `symbol` (str): 交易对
- `data` (dict): 行情数据
- `direction` (int): 方向（1=多，-1=空）

**返回值**: `(grade, score)` - 信号等级和评分

**说明**: 使用评分引擎进行多维度评分，返回等级和详细评分。

---

## 信号过滤器模块

**模块路径**: `core.signal.filter`

### 主要类

#### `SignalFilter`

信号过滤器类，负责趋势判断和过滤。

**初始化参数**:
- `params`: StrategyParams - 策略参数对象（可选）

**主要方法**:

##### `determine_trend_direction(data)`

判断趋势方向。

**参数**:
- `data` (dict): 行情数据

**返回值**: 
- `1`: 多头方向
- `-1`: 空头方向
- `0`: 趋势不明

##### `check_adx_filter(data, min_adx=Decimal('20'))`

检查 ADX 趋势强度过滤。

**参数**:
- `data` (dict): 行情数据
- `min_adx` (Decimal): 最小 ADX 值（默认 20）

**返回值**: bool

##### `check_volume_filter(data, signal_grade)`

检查成交量过滤。

**参数**:
- `data` (dict): 行情数据
- `signal_grade` (str): 信号等级

**返回值**: bool

##### `check_atr_filter(data)`

检查 ATR 波动率过滤。

**参数**:
- `data` (dict): 行情数据

**返回值**: bool

**说明**: 计算 ATR%，检查是否在区间内（默认 2.0% ~ 4.5%）。内部自动处理 float/Decimal 类型转换。

##### `apply_all_filters(data, direction, grade)`

应用所有过滤器。

**参数**:
- `data` (dict): 行情数据
- `direction` (int): 趋势方向
- `grade` (str): 信号等级

**返回值**: `(passed, reason)` - 是否通过和失败原因

---

## 应急处理模块

**模块路径**: `core.emergency_handler`

### 主要函数

#### `check_extreme_market(symbol, price_change_percent)`

检查极端市场。

**参数**:
- `symbol` (str): 交易对
- `price_change_percent` (Decimal): 24小时涨跌幅

**返回值**: bool - 是否为极端行情

### 主要类

#### `EmergencyHandler`

应急处理器类。

**主要方法**:

##### `is_trading_allowed()`

检查是否允许交易。

**返回值**: `(allowed, reason)`
- `allowed` (bool): 是否允许
- `reason` (str): 原因

##### `check_daily_loss(daily_loss)`

检查单日亏损。

**参数**:
- `daily_loss` (Decimal): 单日亏损金额

---

## 数据获取模块（增强版）

**模块路径**: `core.data.fetcher`

### 主要类

#### `MarketDataFetcher`

行情数据获取类（增强版），负责从K线服务获取数据，处理数据格式，计算技术指标，并管理缓存。

**初始化参数**:
- `cache_duration_hours` (int): 缓存有效期（小时），默认1小时
- `max_workers` (int): 并发线程池最大线程数，默认5
- `enable_concurrent` (bool): 是否启用并发获取，默认True

**主要方法**:

##### `fetch_market_data(symbols=None)`

获取市场行情数据。

**参数**:
- `symbols` (list): 交易对列表，默认 ['BTCUSDT', 'ETHUSDT', 'BNBUSDT']

**返回值**: 行情数据字典 `{symbol: data}`

**说明**: 支持并发获取多个交易对的数据，提高性能。

##### `get_symbol_data(symbol)`

获取单个交易对的行情数据。

**参数**:
- `symbol` (str): 交易对

**返回值**: 行情数据，如果不存在则返回None

##### `clear_cache()`

清除缓存。

##### `get_cache_stats()`

获取缓存统计信息。

##### `get_performance_stats()`

获取性能统计信息。

**返回值**:
```python
{
    'fetch_count': int,           # 获取次数
    'total_fetch_time': float,    # 总耗时（秒）
    'avg_fetch_time': float,      # 平均耗时（秒）
    'concurrent_enabled': bool,   # 是否启用并发
    'max_workers': int            # 最大线程数
}
```

**示例**:
```python
from core.data.fetcher import get_data_fetcher

# 获取数据获取器实例（单例模式）
fetcher = get_data_fetcher(
    cache_duration_hours=1,
    max_workers=5,
    enable_concurrent=True
)

# 获取行情数据
data = fetcher.fetch_market_data(['BTCUSDT', 'ETHUSDT'])

# 查看性能统计
stats = fetcher.get_performance_stats()
print(f"平均获取耗时: {stats['avg_fetch_time']}秒")
```

**数据流**:
```
通用K线服务 → 数据获取（并发/串行） → 指标计算 → 缓存 → 提供给信号检测模块
```

---

## K 线服务客户端模块

**模块路径**: `utils.kline_service`

### 主要类

#### `KlineServiceClient`

通用 K 线服务客户端，封装对通用 K 线服务的调用。

**初始化参数**:
- `service_url` (str): K 线服务地址，默认使用环境变量 `KLINE_SERVICE_URL`

**主要方法**:

##### `get_latest_klines(symbol, interval, limit=100)`

获取最新 K 线数据。

**参数**:
- `symbol` (str): 交易对，如 BTCUSDT
- `interval` (str): 时间间隔，如 1h, 4h, 1d, 15m
- `limit` (int): 获取数量，默认 100

**返回值**: K 线数据列表，失败返回 None

**示例**:
```python
from utils.kline_service import KlineServiceClient

client = KlineServiceClient()
klines = client.get_latest_klines('BTCUSDT', '1h', limit=100)

# K 线数据格式
# [
#     {
#         'timestamp': '2026-04-27T10:00:00',
#         'open_price': 95000.00,
#         'high_price': 96000.00,
#         'low_price': 94000.00,
#         'close_price': 95500.00,
#         'volume': 1234.56,
#         'price_change_percent': 1.5
#     },
#     ...
# ]
```

##### `get_indicators(symbol, interval, period=100)`

获取技术指标。

**参数**:
- `symbol` (str): 交易对
- `interval` (str): 时间间隔
- `period` (int): 计算周期，默认 100

**返回值**: 技术指标数据，失败返回 None

##### `get_symbols()`

获取支持的币种列表。

**返回值**: 币种列表，包含 symbols 和 intervals

##### `manual_collect(symbol, interval, minutes=5)`

手动触发 K 线采集。

**参数**:
- `symbol` (str): 交易对
- `interval` (str): 时间间隔
- `minutes` (int): 采集最近 N 分钟，默认 5

**返回值**: 采集结果

##### `get_collector_stats()`

获取采集器统计信息。

**返回值**: 统计信息

### 便捷函数

#### `get_klines(symbol, interval, limit=100)`

获取 K 线数据的便捷函数。

**参数**:
- `symbol` (str): 交易对
- `interval` (str): 时间间隔
- `limit` (int): 获取数量

**返回值**: K 线数据列表

#### `get_indicators(symbol, interval, period=100)`

获取技术指标的便捷函数。

**参数**:
- `symbol` (str): 交易对
- `interval` (str): 时间间隔
- `period` (int): 计算周期

**返回值**: 技术指标数据

**示例**:
```python
from utils.kline_service import get_klines, get_indicators

# 使用便捷函数
klines = get_klines('BTCUSDT', '1h', limit=100)
indicators = get_indicators('BTCUSDT', '1h', period=100)
```

---

## 数据缓存模块

**模块路径**: `core.data.cache`

### 主要类

#### `DataCache`

数据缓存管理类。

**初始化参数**:
- `maxsize` (int): 最大缓存条目数（默认100）
- `ttl_seconds` (int): 缓存过期时间（秒，默认300）
- `enable_stats` (bool): 是否启用统计（默认True）

**主要方法**:

##### `set(symbol, data)`

设置缓存数据。

##### `get(symbol)`

获取缓存数据。

##### `has_symbol(symbol)`

检查是否包含指定交易对。

##### `clear()`

清除所有缓存。

##### `get_stats()`

获取缓存统计信息。

**示例**:
```python
from core.data import DataCache

# 创建缓存
cache = DataCache(maxsize=100, ttl_seconds=300)

# 设置数据
cache.set('BTCUSDT', {'price': 95000})

# 获取数据
data = cache.get('BTCUSDT')

# 检查是否存在
if cache.has_symbol('BTCUSDT'):
    print("缓存存在")

# 获取统计
stats = cache.get_stats()
print(f"命中率: {stats['hit_rate']*100:.2f}%")
```

---

## 评分引擎模块

**模块路径**: `core.scoring`

### 主要函数

#### `get_scoring_engine(version='latest')`

获取评分引擎实例。

**参数**:
- `version` (str): 引擎版本（默认'latest'）

**返回值**: ScoringEngine实例

### 主要类

#### `ScoringEngineV612`

评分引擎类（V6.12版本）。

**主要方法**:

##### `score(symbol, data)`

执行评分。

**参数**:
- `symbol` (str): 交易对
- `data` (dict): 市场数据

**返回值**:
```python
{
    'signal_grade': str,      # 信号等级
    'direction': int,         # 方向
    'total_score': Decimal,   # 总分
    'details': dict           # 详细评分
}
```

---

## 配置管理模块

**模块路径**: `config.config_manager`

### 主要类

#### `ConfigManager`

配置管理器类（单例模式）。

**主要方法**:

##### `get(key, default=None)`

获取配置值。

##### `get_decimal(key, default=Decimal('0'))`

获取Decimal类型配置值。

##### `get_bool(key, default=False)`

获取布尔类型配置值。

##### `get_list(key, default=None)`

获取列表类型配置值。

**示例**:
```python
from config.config_manager import get_config_manager

config = get_config_manager()

# 获取配置
total_capital = config.get_decimal('account.total_capital')
symbols = config.get_list('trading.symbols')
```

---

## 注意事项

1. 所有涉及金额的参数都使用 `Decimal` 类型，避免浮点数精度问题
2. 方向参数：1表示多头，-1表示空头
3. 信号等级：S（最高）、A（中等）、B（试仓）、C（最低）
4. 所有模块都支持单例模式，可通过 `get_xxx()` 函数获取全局实例
5. 日志输出统一使用中文
6. **类型安全** (v1.1 更新): 信号处理和价格计算模块已全面添加 Decimal 类型安全转换，确保内部处理一致性
7. **并发支持** (v1.1 更新): 数据获取模块支持并发获取，可配置线程池大小和超时时间

---

## 更新日志

- **2026-04-27**: 更新API文档，新增通用服务集成
  - 新增 K 线服务客户端模块完整 API 文档
  - 更新数据获取模块说明（使用通用 K 线服务）
  - 补充 K 线数据格式说明
  - 添加便捷函数使用示例
- **2026-04-27**: 更新API文档，新增信号过滤器模块、数据获取模块文档
  - 修正模块路径：`core.signal_detector` → `core.signal.detector`
  - 补充信号返回值字段（score、score_detail、suggested_position_ratio 等）
  - 新增信号过滤器完整API文档
  - 新增数据获取模块并发支持说明
  - 添加类型安全和并发支持注意事项
