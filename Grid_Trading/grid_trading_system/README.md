# 网格交易系统 (Grid Trading System)

**版本**: 3.0 统一版
**功能**: 支持全自动和信号灯两种运行模式
**平台**: 币安永续合约

---

## 📋 项目简介

本项目是一个统一的网格交易系统，整合了之前的 `adaptive_grid_trading`（全自动模式）和 `grid_signal_bot_v2`（信号灯模式），提供了更加统一、可维护的代码结构。

### 核心特性

- **双模式支持**：全自动网格交易 + 信号灯半自动模式
- **智能市场状态识别**：4种状态（震荡、上升趋势、下降趋势、强趋势暂停）
- **自适应网格参数**：根据市场状态和波动率自动调整
- **完整风险管理**：硬止损、移动止盈、紧急暂停、滑点保护
- **多时间框架**：15m/1h/4h 交叉确认，减少假信号
- **实时通知**：飞书/钉钉/Telegram 报警
- **灵活配置**：YAML 配置 + 环境变量覆盖

---

## 🚀 快速开始

### 1. 安装依赖

```bash
cd grid_trading_system
pip install -r requirements.txt
```

### 2. 配置文件

1. **生成默认配置**：
   ```bash
   python -m src.main --init-config
   ```

2. **编辑配置**：
   - `config/config.yaml` - 主配置文件（模式切换、策略参数等）
   - `config/.env` - 敏感信息（API密钥、webhook等）

### 3. 运行系统

#### 全自动模式
```bash
# 使用配置文件中的模式
python -m src.main

# 强制使用全自动模式
python -m src.main --mode auto
```

#### 信号灯模式
```bash
python -m src.main --mode signal
```

---

## 📁 项目结构

```
grid_trading_system/
├── config/                      # 配置目录
│   ├── config.yaml             # 主配置文件
│   ├── config.yaml.template    # 配置模板
│   ├── .env                    # 环境变量
│   └── .env.template          # 环境变量模板
├── src/                        # 源代码
│   ├── main.py                  # 统一入口
│   ├── core/                    # 核心共享模块
│   │   ├── config/               # 配置管理
│   │   ├── data/                 # 数据层
│   │   ├── strategy/            # 策略层
│   │   ├── monitoring/          # 监控层
│   │   └── utils/               # 工具层
│   ├── execution/               # 执行模块
│   │   ├── auto_mode/           # 全自动模式
│   │   └── signal_mode/         # 信号灯模式
│   └── backtest/                # 回测模块（预留）
├── tests/                       # 测试目录
├── requirements.txt            # 依赖包
├── pyproject.toml             # 项目配置
├── Dockerfile                 # Docker构建
└── docker-compose.yml         # Docker Compose配置
```

### 核心模块说明

| 模块 | 主要功能 | 文件位置 |
|------|---------|----------|
| **市场状态识别** | 4种状态判断、多时间框架确认 | `src/core/strategy/market_analyzer.py` |
| **网格参数计算** | 价格区间、网格数量、利润率验证 | `src/core/strategy/grid_calculator.py` |
| **风险管理** | 硬止损、移动止盈、紧急暂停 | `src/core/strategy/risk_manager.py` |
| **技术指标** | ADX、ATR、EMA 计算 | `src/core/data/indicators.py` |
| **币安API** | 统一REST API封装、WebSocket | `src/core/data/binance_client.py` |
| **K线管理** | 多时间框架数据、双数据源 | `src/core/data/kline_manager.py` |
| **通知服务** | 飞书/钉钉/Telegram 报警 | `src/core/monitoring/notifier.py` |
| **配置管理** | YAML解析、环境变量、验证 | `src/core/config/config_loader.py` |

---

## ⚙️ 配置说明

### 核心配置项

```yaml
# 系统配置
system:
  mode: "auto"  # "auto" 或 "signal"

# 交易所配置
exchange:
  api_key: "${BINANCE_API_KEY}"
  api_secret: "${BINANCE_API_SECRET}"
  testnet: false
  symbol: "BTCUSDT"
  contract_type: "PERPETUAL"

# 策略参数
strategy:
  indicators:
    adx_period: 14
    adx_trend_threshold: 25
    ema_fast: 20
    ema_slow: 50
    atr_period: 14
  grid:
    base_grid_count: 30
    min_grid_count: 5
    max_grid_count: 50
  risk:
    hard_stop_loss: -0.08  # -8%
    trailing_profit_start: 0.15  # 15%
    trailing_profit_retrace: 0.05  # 5%

# 执行配置
execution:
  inspection_interval: 3600  # 秒
  auto_mode:
    # 全自动模式配置
  signal_mode:
    run_minute: 35
    rest_hours: [0, 1, 2, 3, 4, 5]

# 监控配置
monitoring:
  alert:
    enabled: true
    feishu_webhook: "${FEISHU_WEBHOOK}"
```

---

## 🔧 模式说明

### 1. 全自动模式 (`--mode auto`)

- **功能**：自动分析市场、计算网格参数、创建/修改网格、执行交易
- **适用场景**：无人值守的自动化交易
- **特点**：完全自动化，无需人工干预

### 2. 信号灯模式 (`--mode signal`)

- **功能**：分析市场、计算参数、推送操作指令
- **适用场景**：需要人工确认的半自动交易
- **特点**：推送详细的网格参数和操作指令，用户手动在币安网页端执行

---

## 📊 日志和监控

### 日志
- 运行日志：`logs/grid_trading.log`
- 结构化格式：`时间 | 级别 | 模块 | 消息`

### 通知
- 支持飞书、钉钉、Telegram 三种通知渠道
- 通知内容：市场状态变更、网格创建、风险事件、参数调整

---

## 🧪 测试

运行单元测试：

```bash
cd grid_trading_system
python -m pytest tests/ -v
```

测试覆盖：
- 市场状态识别
- 网格参数计算
- 风险管理
- 技术指标计算

---

## 🐳 Docker 部署

### 构建镜像
```bash
docker build -t grid-trading .
```

### 启动容器
```bash
docker-compose up -d
```

### 查看日志
```bash
docker logs -f grid-trading
```

---

## 🔒 安全建议

1. **API密钥**：使用最小权限API密钥，启用IP白名单
2. **环境变量**：敏感信息通过 `.env` 文件管理，不要提交到版本控制
3. **资金管理**：从小资金开始测试，设置合理的硬止损
4. **定期检查**：定期查看系统运行状态和日志

---

## 📚 相关文档

- **需求文档**：`../docs/plans/项目需求迭代文档.md`
- **架构文档**：`../docs/architecture/系统架构设计.md`
- **部署文档**：`../docs/deployment/部署指南.md`
- **策略文档**：`../docs/strategy/` 目录

---

## 🤝 贡献

欢迎提交 Issue 和 Pull Request 来改进本项目。

---

**最后更新**: 2026-04-27
**维护者**: 网格交易系统开发团队