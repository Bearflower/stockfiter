# 网格操作指南 - 终止和新建网格

## 📋 概述

本文档说明如何在生产环境对现有网格进行终止和新建操作。

## ⚠️ 重要提示

1. **币安不支持直接修改网格参数**
   - 所有参数调整都需要通过 **终止旧网格 + 创建新网格** 实现
   - 会产生交易费用，请谨慎操作

2. **操作风险**
   - 终止网格会立即平仓并实现盈亏
   - 新建网格会重新开仓
   - 价格波动可能导致滑点

## 🎯 操作方式

### 方式一：使用系统自动调整（推荐）

系统已内置自动参数调整功能，会检测以下条件并自动调整：

**触发条件**：
- ATR 波动率变化超过 ±20%
- 市场状态发生变化（震荡↔趋势）
- 价格运行到网格边缘

**限制**：
- 最小间隔：4 小时
- 每日最多：6 次调整

系统会自动调用 `switch_grid()` 方法完成调整。

---

### 方式二：手动操作单个指令

#### 1. 创建网格

```python
from src.data.binance_client import BinanceClient

client = BinanceClient(api_key, api_secret, testnet=False)

grid_params = {
    'symbol': 'BTCUSDT',
    'upper_price': 70000,      # 上边界
    'lower_price': 68000,      # 下边界
    'grid_count': 30,          # 网格数量
    'grid_direction': 'NEUTRAL', # 方向：LONG/SHORT/NEUTRAL
    'total_investment': 1000,  # 投资金额 (USDT)
    'leverage': 10             # 杠杆倍数
}

result = await client.create_grid(grid_params)

if result['success']:
    grid_id = result['grid_id']
    print(f"网格创建成功：{grid_id}")
else:
    print(f"创建失败：{result['message']}")
```

#### 2. 终止网格

```python
# 需要知道当前网格的 grid_id
grid_id = "your_grid_id"

result = await client.terminate_grid(
    grid_id=grid_id,
    symbol='BTCUSDT'
)

if result['success']:
    profit = result['profit']
    print(f"网格终止成功，实现盈亏：{profit:.2f} USDT")
else:
    print(f"终止失败：{result['message']}")
```

#### 3. 切换网格（参数调整）

```python
# 切换网格 = 终止旧网格 + 创建新网格
old_grid_id = "your_grid_id"

new_params = {
    'upper_price': 71000,      # 新上边界
    'lower_price': 67000,      # 新下边界
    'grid_count': 25,          # 新网格数量
    'grid_direction': 'NEUTRAL',
    'total_investment': 1000,
    'leverage': 10
}

result = await client.switch_grid(
    old_grid_id=old_grid_id,
    symbol='BTCUSDT',
    new_params=new_params
)

if result['success']:
    print(f"网格切换成功")
    print(f"新 grid_id: {result['new_grid_id']}")
    print(f"旧网格实现盈亏：{result['old_grid_profit']:.2f} USDT")
```

---

### 方式三：使用管理脚本

#### 查看当前网格状态

```bash
# 查看容器日志中的网格信息
ssh root@43.156.242.184 "docker logs grid-trading-system | grep '网格'"
```

#### 运行演示脚本

```bash
# 进入项目目录
cd /Users/yl/vscode/Grid_Trading/adaptive_grid_trading

# 运行演示脚本（查看操作示例）
python scripts/grid_operations_demo.py
```

---

## 📊 完整操作示例

### 场景：调整现有网格的价格区间

假设当前有一个运行中的网格：
- Grid ID: `grid_12345`
- 当前区间：[68000, 70000]
- 当前价格：69000

现在想调整为：[67000, 71000]（扩大区间）

```python
import asyncio
from src.data.binance_client import BinanceClient

async def adjust_grid():
    client = BinanceClient(api_key, api_secret, testnet=False)
    
    old_grid_id = "grid_12345"
    
    # 新参数
    new_params = {
        'upper_price': 71000,
        'lower_price': 67000,
        'grid_count': 30,
        'grid_direction': 'NEUTRAL',
        'total_investment': 1000,
        'leverage': 10
    }
    
    # 执行切换
    result = await client.switch_grid(
        old_grid_id=old_grid_id,
        symbol='BTCUSDT',
        new_params=new_params
    )
    
    if result['success']:
        print("✅ 网格调整成功")
        print(f"   新 grid_id: {result['new_grid_id']}")
        print(f"   旧网格盈亏：{result['old_grid_profit']:.2f} USDT")
    else:
        print(f"❌ 调整失败：{result['message']}")
    
    await client.close()

asyncio.run(adjust_grid())
```

---

## 🔍 获取当前网格信息

### 方法一：查看日志

```bash
ssh root@43.156.242.184 "docker logs grid-trading-system | grep -E '(grid_id|网格创建|Grid ID)'"
```

### 方法二：查询数据库

```python
from src.data.database import DatabaseManager
import asyncio

async def get_current_grid():
    db = DatabaseManager('data/database.db')
    
    # 获取最近的网格记录
    grids = await db.get_grid_history(symbol='BTCUSDT', limit=1)
    
    if grids:
        grid = grids[0]
        print(f"Grid ID: {grid['grid_id']}")
        print(f"状态：{grid['state']}")
        print(f"上边界：{grid['upper_price']}")
        print(f"下边界：{grid['lower_price']}")
        print(f"网格数量：{grid['grid_count']}")
    
    await db.close()

asyncio.run(get_current_grid())
```

---

## ⚙️ 系统自动调整机制

系统内置的自动调整逻辑：

### 1. 检测触发条件

```python
# src/strategy/grid_calculator.py

# 检测 ATR 变化
if atr_change_percent > 20%:
    触发调整

# 检测市场状态变化
if market_state_changed:
    触发调整

# 检测价格接近边界
if price_distance_to_edge < 0.5 * ATR:
    触发调整
```

### 2. 检查限制条件

```python
# 检查最小间隔（4 小时）
if time_since_last_adjustment < 4 hours:
    跳过调整

# 检查每日上限（6 次）
if adjustments_today >= 6:
    跳过调整
```

### 3. 执行调整

```python
# 调用 switch_grid 方法
result = await client.switch_grid(
    old_grid_id=current_grid_id,
    symbol='BTCUSDT',
    new_params=new_params
)
```

---

## 📝 注意事项

1. **费用成本**
   - 每次终止网格会产生平仓手续费
   - 新建网格会产生开仓手续费
   - 建议通过自动调整机制控制频率

2. **时机选择**
   - 避免在剧烈波动时调整
   - 系统已设置 4 小时间隔保护
   - 每日最多 6 次调整

3. **参数合理性**
   - 确保上边界 > 下边界
   - 网格数量在 20-50 之间
   - 投资金额足够覆盖所有网格

4. **监控日志**
   - 调整后检查新网格是否正常
   - 观察实现盈亏
   - 记录调整原因和效果

---

## 🛠️ 常用命令

```bash
# 查看系统状态
./scripts/check_status.sh

# 查看实时日志
ssh root@43.156.242.184 "docker logs -f grid-trading-system"

# 查看最近网格操作
ssh root@43.156.242.184 "docker logs grid-trading-system | grep -A 5 '网格'"

# 重启系统
ssh root@43.156.242.184 "docker restart grid-trading-system"
```

---

## 📖 相关文档

- [部署报告](DEPLOYMENT_REPORT.md) - 系统运行状态
- [README.md](../README.md) - 项目说明
- [产品需求](../memory-bank/product-requirements.md) - 参数调整逻辑

---

**最后更新**: 2026-03-20
