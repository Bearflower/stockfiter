# stockfilter_v3 - A股形态筛选与量化投顾系统

## 项目简介

stockfilter_v3 是一套面向 A 股市场的**形态筛选 + 量化投顾**系统，采用五层分层架构，从 V2 的"单策略+单体脚本"演进为"多策略+分层架构+LLM增强"的可扩展系统。系统融合传统形态学策略与 E大（ETF拯救世界）估值框架，并通过 DeepSeek V4 Pro 大模型进行市场解读与 ETF 推荐。

### 五层架构

```
通知层 (Notification)     -- 飞书推送（远程优先 + 本地降级）
    ^
策略层 (Strategy)          -- 超跌反弹回踩确认策略 + E大指数估值策略（LLM增强）
    ^
引擎层 (Engine)            -- 策略注册 + 多策略调度
    ^
数据层 (Data)              -- PostgreSQL + Baostock + 远程K线服务
```

### 核心策略

| 策略 | 版本 | 说明 |
|------|------|------|
| **超跌反弹回踩确认策略** | oversold_bounce v25 | 大跌 → 缩量 → 放量 → 回踩确认 四步检测，含 MACD 动量过滤与均线斜率过滤 |
| **E大指数估值策略** | distill_changying v1 | 指数估值温度采集 + LLM 投顾决策 + ETF 推荐引擎 + 飞书日报推送 |

## 快速开始

### 环境要求

- Python 3.9+
- PostgreSQL 数据库
- Docker（推荐部署方式）
- DeepSeek API Key（用于 E大策略 LLM 分析）

### 安装

```bash
git clone https://github.com/Bearflower/stockfiter.git
cd stockfiter
pip install -r requirements.txt

cp .env.example .env
# 编辑 .env 填入数据库密码、飞书 Webhook、DeepSeek API Key
```

### 配置

核心配置文件：`config/config.yaml`

```yaml
global:
  database:             # PostgreSQL 配置
  stock_pool:           # 股票池过滤（剔除 ST/科创/创业/次新等）
  notification:         # 飞书 Webhook 与远程通知服务
  kline_service:        # 远程 K 线服务地址

strategies:
  oversold_bounce:      # 超跌反弹策略参数（v25）
    params:
      drop_threshold: 0.15              # 大跌阈值
      min_volume_ratio: 2.0             # 放量倍数
      max_daily_signals: 5              # 每日信号上限（稀缺性控制）
      max_signals_per_month: 30         # 月度信号上限
      index_filter:                     # 大盘环境过滤
        method: 'macd'                  # MACD 动量过滤
        condition: 'dif > 0'
      score_filter:                     # 评分阈值过滤
        score_threshold: 65

  distill_changying:    # E大估值策略参数
    params:
      indices: [...]                    # 估值跟踪的指数列表
      etf_pool: [...]                   # ETF 品种池
      position_mapping: ...             # 估值温度到仓位映射

schedule:               # 定时任务调度
  kline_update: "14:00"
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

# 历史数据补全
python main.py --backfill

# 完整流程（更新 -> 扫描 -> 推送）
python main.py --all

# 定时任务模式（生产环境）
python main.py --schedule
```

## Docker 部署

项目采用 **3 容器分离架构**，每个策略独立容器运行，互不影响：

```bash
# 构建并启动所有容器
docker compose build
docker compose up -d

# 单独更新某个策略容器
docker compose build stockfilter-obpc && docker compose up -d stockfilter-obpc
```

### 3 容器架构

| 容器 | Dockerfile | 依赖文件 | 职责 |
|------|------------|----------|------|
| `stockfilter-kline` | `Dockerfile.kline` | `requirements-kline.txt` | K 线每日更新 + 历史数据补全 |
| `stockfilter-obpc` | `Dockerfile.obpc` | `requirements-obpc.txt` | 形态扫描 + 飞书推送 |
| `stockfilter-eadvisor` | `Dockerfile.eadvisor` | `requirements-eadvisor.txt` | 指数估值采集 + LLM 投顾决策 + ETF 推荐 + 日报推送 |

### 定时任务时序

```
22:00  容器1 (kline)     : K线更新 -> 写入 task_status 表
22:10  容器2 (obpc)      : 等待K线完成 -> 形态扫描 -> 写入 scan_results
23:10  容器3 (eadvisor)  : E大估值扫描 + LLM分析 + ETF推荐 + 飞书日报推送
08:10  容器2 (obpc)      : 读取昨日扫描结果 -> 飞书推送
```

## 项目结构

```
stockfilter_v3/
├── main.py                       # 主程序入口（命令行参数：--scan/--push/--update/--schedule 等）
├── config/
│   └── config.yaml              # 统一配置文件（数据库/策略/调度）
├── engine/                      # 引擎层
│   ├── registry.py              # 策略注册表
│   └── scheduler.py             # 定时调度器
├── strategy/                    # 策略层
│   ├── base.py                  # 策略基类（DataRequirement/Signal/BaseStrategy）
│   └── oversold_bounce/         # 超跌反弹策略实现
├── data/                        # 数据层
│   ├── database.py              # PostgreSQL 数据库封装
│   ├── data_source.py           # Baostock 数据源（股票列表）
│   ├── fetcher.py               # 数据获取器
│   ├── kline_service.py         # 远程 K 线服务客户端
│   └── stock_list.py            # 股票池管理
├── notification/                # 通知层（飞书机器人）
├── scripts/                     # 运维脚本与各策略调度器
├── distill_changying/           # E大指数估值策略（独立策略模块）
│   ├── scripts/
│   │   ├── advisor/             # 投顾 LLM 决策模块
│   │   │   ├── advisor_llm.py   # DeepSeek V4 Pro 调用与仓位/ETF推荐
│   │   │   ├── opinion_matcher.py # E大观点库匹配
│   │   │   └── scheduler.py     # 策略调度器
│   │   ├── distill/             # 博客蒸馏 CLI（summarize/analyze/distill/persona）
│   │   └── shared/
│   │       └── llm_utils.py     # LLM 工具（思考模式/JSON 解析）
│   └── docs/                    # 蒸馏产物（观点库/人物画像/博客原文）
├── docs/                        # 项目文档
├── utils/                       # 工具模块（日志等）
├── Dockerfile.kline             # K 线服务镜像
├── Dockerfile.obpc              # OBPC 策略镜像
├── Dockerfile.eadvisor          # E大估值策略镜像
├── requirements-kline.txt
├── requirements-obpc.txt
├── requirements-eadvisor.txt
└── docker-compose.yml           # 3 容器编排配置
```

## 核心模块说明

### 1. 超跌反弹回踩确认策略（oversold_bounce v25）

四步形态检测：
1. **大跌检测**：跌幅超过 `drop_threshold`（默认 15%）
2. **缩量检测**：放量日前 `shrink_to_surge_days` 内出现缩量（量比 < 0.5）
3. **放量检测**：放量日量比介于 `min_volume_ratio` ~ `max_volume_ratio`
4. **回踩确认**：放量后 `retrace_max_days` 内回踩不破支撑

风控机制：
- 大盘环境过滤（MACD DIF > 0 且 60 日均线斜率向上）
- 评分市场状态调整（强势放大、弱势缩小、极弱归零）
- 每日信号 Top N 稀缺性控制（默认 ≤5 个，评分 ≥65）
- 月度信号上限（默认 30 个）

### 2. E大指数估值策略（distill_changying）

基于 E大（ETF拯救世界）估值框架：
- **估值温度采集**：跟踪上证、深证、沪深300、中证500、创业板指等指数 PE 历史分位
- **仓位映射**：钻石坑/低估/正常/高估/泡沫 → 股债现金比例
- **LLM 投顾决策**：调用 DeepSeek V4 Pro（思考模式）输出结构化 JSON（仓位比例、ETF 建议）+ 自然语言市场解读
- **观点库匹配**：从蒸馏的 E大历史观点中匹配当前市场语境
- **ETF 推荐**：基于估值温度从 ETF 品种池生成买卖建议
- **规则引擎降级**：LLM 失败时自动降级到规则引擎

### 3. LLM 集成模块

- **模型**：DeepSeek V4 Pro（必须使用，旧模型将于 2026/07/24 弃用）
- **调用方式**：`call_v4_pro_json()` 启用思考模式（reasoning_effort: high）
- **输出格式**：结构化 JSON（仓位比例、ETF 建议）+ 自然语言市场解读
- **降级机制**：LLM 失败时降级到规则引擎
- **知识底座**：E大 283 篇博客蒸馏产物（观点库、人物画像、持仓数据）

## 与 V2 的关系

| 维度 | V2 | V3 |
|------|-----|-----|
| 架构 | 单体脚本 | 五层分层架构 |
| 策略管理 | 硬编码 | BaseStrategy + 注册机制 |
| 数据频率 | 仅日线 | 日线/周线/月线 |
| 部署 | 与 V2 同一项目 | 独立容器，并行运行 |
| 数据库 | 共享 | 共享（klines 新增 frequency 字段） |
| LLM 增强 | 无 | DeepSeek V4 Pro 投顾决策 |

V3 与 V2 共享同一 PostgreSQL 实例的 `schema_stockfilter` schema，通过独立容器隔离运行。

## 技术栈

- **语言**：Python 3.9+
- **数据库**：PostgreSQL（K 线服务 + OBPC 策略共享）
- **数据源**：Baostock
- **LLM**：DeepSeek V4 Pro（思考模式 + JSON 输出）
- **通知**：飞书机器人（远程 common_service + 本地 Webhook）
- **部署**：Docker + Docker Compose（3 容器分离架构）
- **日志**：loguru

## 文档

完整文档请查阅 [docs/README.md](docs/README.md) 文档总索引。

核心设计文档：
- [技术架构文档](docs/designs/技术架构文档_V3.md)
- [核心接口设计](docs/designs/核心接口设计.md)
- [数据库设计](docs/designs/数据库设计.md)
- [配置管理设计](docs/designs/配置管理设计.md)
- [部署方案](docs/designs/部署方案.md)

## 开发规范

项目遵循严格的开发流程与编码规范，详见：
- [.trae/rules/development-workflow.md](.trae/rules/development-workflow.md) - 开发流程规范
- [.trae/rules/coding-standards.md](.trae/rules/coding-standards.md) - 编码标准
- [.trae/rules/deployment.md](.trae/rules/deployment.md) - 部署规则（含防幻觉机制）
- [.trae/rules/git-workflow.md](.trae/rules/git-workflow.md) - Git 工作流

---

**最后更新**：2026-08-19
