# stockfilter_v3 文档总索引

2026-10-26 K 线采集锁等待与日志留存热修记录：[事故证据、修复和上线验收](reports/2026-10-26-kline-lock-and-log-persistence.md)。

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
- 完整表结构（6 张表）
- klines 表的 V3 变更（新增 frequency 字段）
- 频率字段说明（d/w/m）
- 沿用 V2 的表说明（scan_results/push_history/positions/stocks）
- 新增表 scan_runs、task_status 扩展列（2026-10-18 扫描可靠性改造）
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

### 2026-10-22 K 线采集进程隔离与断点续采交付索引

| 类别 | 路径 | 说明 |
|------|------|------|
| **新增** | `scripts/data/kline_collection.py` | 分片监督者：进程级超时 → terminate/宽限期/SIGKILL → PID 确认 → 数据库缺口续采 |
| **新增** | `tests/scheduler/test_kline_process_isolation.py` | 610 只超时恢复、重建上限、截止时间、无进展、DB 异常、配置 fail-closed |
| **重写** | `scripts/data/update_kline_daily.py` | 纯串行 `--worker` 分片进程 + 监督者入口 `run_update`；移除线程池伪超时 |
| **更新** | `scripts/scheduler_kline.py`、`main.py` | 两条全量入口统一走共享监督者 |
| **更新** | `scripts/monitor.sh` | 恢复事件一次性告警、失败告警携带 details、查询失败 fail-closed |
| **更新** | `docs/designs/配置管理设计.md` | §8.1 移除 `request_timeout_seconds`，统一管理 13 项分片/隔离/DB 超时/状态重试配置与推导依据 |
| 实施记录 | `docs/reports/2026-10-22-kline-process-isolation.md` | 契约、配置初值推导、验证证据及第四轮复审整改记录 |
| 计划 | `tasks/plan.md`、`tasks/todo.md` | 实施方案与任务清单（含基准 BLOCKED 说明） |

验收标准（本地）：全量 pytest 378 passed / 8 skipped（需真实 PostgreSQL 环境）、编译与 bash 语法检查通过、故障注入可恢复至 98%+ 且无残留 PID、连续多只永久挂死股票被隔离且不阻断后续普通缺口。

### 2026-10-18 Task 9：OBPC 扫描可靠性整改交付索引

本轮整改引入的新文件与更新点：

| 类别 | 路径 | 说明 |
|------|------|------|
| **新增** | `tests/integration/test_obpc_daily_pipeline.py` | 4 条端到端场景测试（FakeDB + 真实生产代码路径） |
| **新增** | `tests/integration/conftest.py` | FakeDB / FakeCursor / Pipeline fixture |
| **新增** | `scripts/verify_obpc_pipeline.py` | 只读验收脚本（输出 JSON，覆盖率阈值从 config 读取） |
| **扩展** | `scripts/monitor.sh` | 新增 6 类 OBPC 可靠性告警（覆盖率、task_status、scan_runs 缺失/卡死/阻断、推送缺失） |
| **更新** | `docs/designs/OBPC策略说明文档.md` | 新增 scan_runs 六态语义、股票池主板限定、零信号四象限区分 |
| **更新** | `docs/designs/配置管理设计.md` | 新增 global.kline_update 完整性配置节、禁止硬编码约束、scheduler 环境变量 |
| **更新** | `docs/designs/数据库设计.md` | ER 图补入 scan_runs 与 task_status 扩展列 |
| 计划 | `docs/superpowers/plans/2026-10-18-obpc-scan-reliability-remediation.md` | Task 1~9 完整整改计划与验收标准 |

验收标准：全部 pytest 通过、覆盖率告警阈值来自 config、scan_runs 六态不混淆、推送幂等。

### 代码审查与修复（2026-09-29）

本轮对全仓执行了 open-code-review-delegate 代码审查（覆盖率 89.43%），产出 23 项可行动缺陷（P1=10 / P2=13）。P0+P1 批次（含 22 项）已全部修复并通过隔离复现断言翻转验证（16 PASS / 5 SKIP / 0 FAIL）；P2 批次（11 项）排期处理。

| 文档 | 路径 | 说明 |
|------|------|------|
| 代码审查报告 | `reports/code-review-2026-09-29/README.md` | 23 项缺陷清单 + 证据链 + 复现脚本 |
| 修复需求文档（PRD） | `requirements/code-review-fix-2026-09-29.md` | P0/P1/P2 分层 + 逐项验收标准 + 待决策项 |
| 修复技术设计文档 | `designs/code-review-fix-2026-09-29.md` | 逐项修改方案 + 配置变更 + 统一算法设计 |

---

**文档版本**：V1.4（2026-10-22：新增 K 线采集进程隔离与断点续采交付索引；V1.3 为 2026-10-18 OBPC 扫描可靠性整改索引）
**最后更新**：2026-10-22
