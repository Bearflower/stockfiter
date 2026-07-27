# stockfilter_v3 文档总索引

## 文档目录结构

```
docs/
├── README.md                         # 本文档：文档总索引
├── designs/                          # 设计文档
│   ├── 技术架构文档_V3.md            # 五层架构设计、数据流、技术决策
│   ├── 目录结构设计.md               # 项目目录结构、模块职责
│   ├── 核心接口设计.md               # 策略/数据/回测/通知/引擎 各层接口
│   ├── 数据库设计.md                 # 表结构、迁移方案、ER 图
│   ├── 配置管理设计.md               # config.yaml 结构、环境变量
│   └── 部署方案.md                   # Docker 部署、与 V2 并行方案
├── requirements/                     # 需求文档（预留）
├── reports/                          # 报告文档（预留）
└── deployment/                       # 部署文档（预留）
```

## 设计文档索引

### 1. [技术架构文档_V3.md](designs/技术架构文档_V3.md)

**阅读对象**：架构师、开发者

**内容概要**：
- 系统概述与 V2->V3 核心变更
- 五层架构设计（引擎层/策略层/数据层/回测层/通知层）
- 每日扫描与回测的数据流设计
- 与 V2 的兼容关系
- 技术决策记录（ADR）
- 非功能需求

### 2. [目录结构设计.md](designs/目录结构设计.md)

**阅读对象**：开发者

**内容概要**：
- 完整目录结构（实际代码）
- 与旧版设计的差异对照表
- 各模块职责说明
- 包导入约定

### 3. [核心接口设计.md](designs/核心接口设计.md)

**阅读对象**：策略开发者、引擎开发者

**内容概要**：
- 策略层接口：BaseStrategy、Signal、DataRequirement、OversoldBounceStrategy
- 数据层接口：KlineService、DatabaseManager
- 回测层接口：BacktestEngine、BacktestReport
- 通知层接口：NotificationService（单文件三合一）
- 引擎层接口：StrategyRegistry、Scheduler
- 接口关系图

### 4. [数据库设计.md](designs/数据库设计.md)

**阅读对象**：DBA、后端开发者

**内容概要**：
- 完整表结构（5 张表）
- klines 表的 V3 变更（新增 frequency 字段）
- 频率字段说明（d/w/m）
- 沿用 V2 的表说明（scan_results/push_history/positions/stocks）
- ER 图
- 数据库连接方式

### 5. [配置管理设计.md](designs/配置管理设计.md)

**阅读对象**：运维、开发者

**内容概要**：
- config.yaml 三节结构（global/strategies/schedule）
- 配置加载方式（yaml.safe_load + 字典传递）
- 环境变量管理（.env.example）
- 配置优先级
- Docker 环境变量注入

### 6. [部署方案.md](designs/部署方案.md)

**阅读对象**：运维

**内容概要**：
- 部署架构（与 V2 并行）
- Dockerfile 和 docker-compose.yml
- 调度错峰方案
- 部署步骤和常用命令
- 监控要点

## 文档管理规范

### 版本管理

- 每个文档末尾标注 **文档版本** 和 **最后更新** 日期
- 版本号格式：V1.0（初版）/ V2.0（根据代码更新后）/ V3.0（重大变更）

### 更新规则

1. 代码变更后，必须同步检查并更新相关设计文档
2. 新增功能/模块时，需要同步更新核心接口设计、目录结构设计、技术架构文档
3. 数据库表结构变更时，必须同步更新数据库设计.md
4. 配置项变更时，必须同步更新配置管理设计.md
5. 部署流程变更时，必须同步更新部署方案.md

### 交叉引用的文档

如果需要了解完整的项目需求，请查看 `requirements/` 目录下的需求文档（如有）。

---

**文档版本**：V1.1（3 容器分离架构更新）
**最后更新**：2026-05-26
