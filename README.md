# stockfilter_v3 - 股票形态筛选系统 V3

## 项目简介

stockfilter_v3 是基于五层分层架构的 A 股（中国股市）股票形态筛选系统。它是 V2 版本的重构升级版，从"单策略+单体脚本"演进为"多策略+分层架构"的可扩展系统。

### 五层架构

```
通知层 (Notification)     -- 飞书推送（远程优先 + 本地降级）
    ^
策略层 (Strategy)          -- 超跌反弹回踩确认策略 + E大指数估值策略
    ^
引擎层 (Engine)            -- 策略注册 + 多策略调度
    ^
数据层 (Data)              -- PostgreSQL + Baostock
```

### 当前策略

- **超跌反弹回踩确认策略**（oversold_bounce v24）：大跌 -> 缩量 -> 放量 -> 回踩确认 四步检测
- **E大指数估值策略**（distill_changying）：指数估值采集 + ETF 推荐引擎 + 飞书日报推送

## 快速开始

### 环境要求

- Python 3.9+
- PostgreSQL 数据库（可与 V2 共享）
- Docker（推荐部署方式）

### 安装

```bash
# 克隆项目
git clone <repo_url>
cd stockfilter_v3

# 安装依赖
pip install -r requirements.txt

# 复制环境变量模板
cp .env.example .env
# 编辑 .env 填入数据库密码和飞书 Webhook
```

### 配置

编辑 `config/config.yaml`：

```yaml
global:
  stock_pool:        # 股票池过滤规则
  notification:      # 通知配置
  kline_service:     # K 线服务地址

strategies:
  oversold_bounce:   # 策略参数
    params: ...

schedule:            # 定时任务时间
  daily_scan: "14:10"
  feishu_push: "00:10"
```

### 运行

```bash
# 单次扫描
python main.py --scan

# 单次推送
python main.py --push

# K 线更新
python main.py --update

# 完整流程（更新 -> 扫描 -> 推送）
python main.py --all

# 定时任务模式（生产环境）
python main.py --schedule
```

### Docker 部署

项目采用 **3 容器分离架构**，每个策略独立容器运行：

```bash
# 构建所有镜像
docker compose build

# 启动所有容器
docker compose up -d

# 单独构建/更新某个策略容器（无需重启其他容器）
docker compose build stockfilter-kline && docker compose up -d stockfilter-kline
docker compose build stockfilter-obpc && docker compose up -d stockfilter-obpc
docker compose build stockfilter-eadvisor && docker compose up -d stockfilter-eadvisor
```

### 3 容器架构

| 容器 | 镜像 | Dockerfile | 依赖文件 | 职责 |
|------|------|------------|----------|------|
| `stockfilter-kline` | `stockfilter-kline:latest` | `Dockerfile.kline` | `requirements-kline.txt` | K线每日更新 + 历史数据补全 |
| `stockfilter-obpc` | `stockfilter-obpc:latest` | `Dockerfile.obpc` | `requirements-obpc.txt` | 形态扫描 + 飞书推送 |
| `stockfilter-eadvisor` | `stockfilter-eadvisor:latest` | `Dockerfile.eadvisor` | `requirements-eadvisor.txt` | 指数估值采集 + ETF推荐 + 日报推送 |

**定时任务时序依赖**：
```
22:00  容器1: K线更新 -> 写入 task_status 表
22:15  容器2: 等待K线完成 -> 形态扫描 -> 写入 scan_results
23:10  容器3: E大估值扫描 + ETF推荐 + 飞书日报推送（独立运行，不依赖DB）
08:10  容器2: 读取昨日扫描结果 -> 飞书推送
```

## 项目结构

```
stockfilter_v3/
├── main.py                    # 主程序入口
├── config/config.yaml         # 统一配置文件
├── engine/                    # 引擎层：策略注册 + 调度
├── strategy/                  # 策略层：策略基类 + 具体策略
├── data/                      # 数据层：数据库 + K线服务 + 数据源
├── notification/              # 通知层：飞书通知（单文件）
├── scripts/                   # 运维脚本 + 各策略调度器
├── distill_changying/         # E大指数估值策略（独立策略，含 ETF 推荐引擎）
├── docs/                      # 文档
├── utils/                     # 工具（日志等）
├── Dockerfile.kline           # K线服务容器镜像
├── Dockerfile.obpc            # OBPC 策略容器镜像
├── Dockerfile.eadvisor        # E大估值策略容器镜像
├── requirements-kline.txt     # K线服务依赖
├── requirements-obpc.txt      # OBPC 策略依赖
├── requirements-eadvisor.txt  # E大估值策略依赖
└── docker-compose.yml         # 3 容器编排配置
```

完整目录结构请参考 [docs/designs/目录结构设计.md](docs/designs/目录结构设计.md)。

## 与 V2 的关系

| 维度 | V2 | V3 |
|------|-----|-----|
| 架构 | 单体脚本 | 五层分层架构 |
| 策略管理 | 硬编码 | BaseStrategy + 注册机制 |
| 数据频率 | 仅日线 | 日线/周线/月线 |
| 部署 | 与 V2 同一项目 | 独立容器，并行运行 |
| 数据库 | 共享 | 共享（klines 新增 frequency 字段） |

V3 与 V2 共享同一 PostgreSQL 实例的 `schema_stockfilter` schema，通过独立容器隔离运行。

## 文档

完整文档请查阅 [docs/README.md](docs/README.md) 文档总索引。

核心设计文档：
- [技术架构文档](docs/designs/技术架构文档_V3.md)
- [核心接口设计](docs/designs/核心接口设计.md)
- [数据库设计](docs/designs/数据库设计.md)
- [配置管理设计](docs/designs/配置管理设计.md)
- [部署方案](docs/designs/部署方案.md)

## 技术栈

- **语言**：Python 3.9+
- **数据库**：PostgreSQL（K线服务 + OBPC 策略共享）
- **数据源**：Baostock
- **通知**：飞书机器人（远程 common_service + 本地 Webhook）
- **部署**：Docker + Docker Compose（3 容器分离架构）
- **日志**：loguru
- **ETF 推荐**：OpenAI API（E大估值策略）

---

**最后更新**：2026-05-26
