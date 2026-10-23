# 2026-10-23 K 线夜间采集韧性整改

## 1. 生产证据与问题定义

2026-10-22 夜间主采集在覆盖约 `1682/2911` 后出现工作进程 300 秒超时，随后以
`stop_reason=no_progress` 提前结束。此时距离整体截止时间仍约 13 分钟。失败状态写入又因
PostgreSQL `statement_timeout` 未落库，`task_status.kline_update` 保留为 2026-10-21 的旧记录。
次日 07:00 backfill 将目标日补至红线以上，07:30 重扫正常执行。

只读复核还确认监控的覆盖分子把指数 `000300` 计入，导致显示 `2908/2911`，而当前有效
股票池交集为 `2907/2911`。`blocked_data` 运行又触发了一条没有独立诊断价值的
`data_metrics` 缺失 CRITICAL。

## 2. 范围与非目标

### 目标

1. 任务状态写入不再触发建表、建索引或迁移，只执行有界超时的原子 UPSERT，并对瞬时错误有限重试。
2. 会话/进程级异常退出不再误计为“业务分片无进展”；只要截止时间与重建额度允许，就继续新会话恢复。
3. 覆盖率监控分子与分母使用同一 `stocks` 股票池集合，排除指数等池外 K 线。
4. `blocked_data + selected_count=0` 时不再重复发送极弱市场指标缺失告警；其他未知或矛盾状态仍 fail-closed。

### 非目标

- 不修改 `min_coverage_rate=0.98`、OBPC 策略、评分或市场风控。
- 不切换生产 `hard_filter`。
- 不清理长期无行情股票；只修正本轮监控统计口径。
- 本轮开发阶段不提交、推送或部署。

## 3. 验收条件

- **AC-01 状态写入轻量化：** `_write_task_status()` 不构造 `DatabaseManager`，不执行任何 DDL；一次事务原子写入
  `task_name/status/last_completed_at/target_date/details`。
- **AC-02 状态写入恢复：** 瞬时失败在配置的重试次数和间隔内重连；最终成功返回 `True`，耗尽返回 `False`，
  每次失败连接均 rollback/close，不泄漏连接。调用方必须把最终失败记录为 CRITICAL；监控必须把旧目标日视为异常，
  不能因旧记录仍为 `completed` 而报告正常。
- **AC-03 配置闭环：** `task_status_write_retries`、`task_status_write_retry_delay_seconds` 和
  `task_status_process_terminate_grace_seconds` 显式存在、严格校验，
  生产初值的最坏等待不超过既有 5 分钟恢复余量。
- **AC-04 会话级恢复：** `timed_out=True`、致命退出或无/非法结构化结果的异常退出即使零新增，也不累计内容级
  `consecutive_no_progress`；由 `max_session_restarts` 与绝对截止兜底。
- **AC-05 内容级熔断保留：** 正常退出且连续两轮零新增仍以 `no_progress` 停止，防止对真实无数据分片无限循环。
- **AC-06 覆盖口径一致：** 监控只统计与 `stocks` 表相交的目标日日线；池外指数不得进入分子。
- **AC-07 告警降噪不降级：** 仅当 `status` 精确为 `blocked_data`、`selected_count` 是非 bool 的非负整数且精确为 0、
  `data_metrics` 是数据库 NULL 或合法空 JSON 对象时，才不触发极弱指标缺失告警；坏 JSON、非字典、NULL/负数计数、
  `selected_count>0`、无运行记录、查询失败、非法 mode 等仍 fail-closed。
- **AC-08 零策略回归：** `market_regime_policy.mode` 保持 `hard_filter`；相关目标测试、全量测试、编译、
  `bash -n`、YAML 解析和 `git diff --check` 通过。

## 4. 设计

### 4.1 轻量状态写入

调度器启动时由 `_ensure_task_status_table()` 显式完成一次性 schema 准备：空库直接创建包含
`task_name/last_completed_at/status/target_date/details` 的完整表，旧三列表再执行幂等 `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`。
这一步允许 DDL，但只在调度器启动期运行。运行期 `_write_task_status()` 禁止 DDL，改用轻量连接工厂封装唯一一份
`DB_HOST/DB_PORT/DB_NAME/DB_USER/DB_PASSWORD/DB_SCHEMA`、`search_path`、建连超时和语句超时规则，执行一条
`INSERT ... ON CONFLICT DO UPDATE`。JSON 使用 psycopg2 JSON 适配器，时间戳保持项目既有“写入带时区时间、
TIMESTAMP 列按 UTC 裸值读取”的约定。每次写入在独立子进程执行，失败事务显式回滚并关闭；
父进程对单次 I/O 施加 deadline，超时后 terminate→宽限→kill，不再依赖数据库驱动自行返回。

新增配置：

- `task_status_write_retries: 3`：首次失败后最多再尝试两次；
- `task_status_write_retry_delay_seconds: 1`：重试间隔。
- `task_status_process_terminate_grace_seconds: 1`：状态写入子进程 TERM/KILL 各自的回收宽限。

配置加载器增加跨字段预算约束：

```text
retries × (db_connect_timeout_seconds + db_query_timeout_seconds
  + 2 × process_terminate_grace_seconds)
  + (retries - 1) × retry_delay_seconds
  < recovery_deadline_buffer_seconds
```

生产初值最坏约 `3 × (10 + 30 + 2 × 1) + 2 × 1 = 128` 秒，小于既有 300 秒恢复余量。
若 SIGKILL 后 PID 仍未回收，该错误不可重试，禁止并发拉起新写入进程。

可重试错误限定为连接中断、连接不可用、锁等待/语句超时、死锁和序列化冲突；未定义列、权限、数据类型和 JSON
适配等确定性错误立即返回 `False`。最终失败时函数自身记录 CRITICAL，调用方再记录任务名、目标日和原业务结果；
调度器继续存活，但 OBPC 因 target_date 不新鲜而 fail-closed。

### 4.2 无进展状态分类

先按下表计算会话状态：

| 条件 | 分类 | 无进展计数 | 恢复/停止 |
|---|---|---|---|
| `timed_out=True` | 会话异常（最高优先） | 不增加 | 隔离已知股票，增加 restart |
| 旧 PID 未消失 | 不可安全恢复 | 不增加 | 立即 `worker_fatal`，禁止并发拉新进程 |
| `exit_code=2` | 会话致命异常 | 不增加 | 增加 restart |
| result 缺失、非 dict、契约版本/模式/目标日不匹配 | 会话异常 | 不增加 | 增加 restart |
| `exit_code=0/1` 且 result 契约合法 | 内容结果 | 零新增时增加 | 达阈值后 `no_progress` |
| 其他退出码 | 会话异常 | 不增加 | 增加 restart |

合法 result 至少要求 `contract_version`、`mode=chunk`、`target_date` 与本轮一致；`processed` 必须落在分片边界内，
`failed_codes` 必须无重复且全部属于已处理前缀，禁止“处理 0 只却报告失败代码”等自相矛盾结果。
异常退出即使已有部分覆盖，也以数据库覆盖为事实来源；有新增覆盖或新隔离动作会清零内容级无进展计数。

停止优先级为：PID 未回收（禁止任何轮后数据库访问）→ 数据库/截止预算失败 → restart 超限 → 合法内容结果的 no-progress →
`immediate_recovery_enabled=False`。异常会话仍严格受 `max_session_restarts` 与绝对截止控制。

### 4.3 监控口径和降噪

覆盖 SQL 从“目标日全部 K 线”改为 `klines INNER JOIN stocks`，分母和分子都使用 `COUNT(DISTINCT code)`，保证集合一致；
`pool_count <= 0` 视为查询协议错误，不能输出合法 0% 覆盖率。
极弱穿透检查把 `blocked_data` 视为已由覆盖率和扫描阻断检查负责的终态：仅当 `selected_count == 0` 时跳过
空指标告警，但必须满足 AC-07 的严格类型和解析条件；若出现信号或损坏数据仍按状态矛盾 fail-closed。

`check_obpc_task_status` 同时校验目标日：当前 `kline_update` 应匹配 `OBPC_TARGET_DATE`，`backfill` 应匹配
`OBPC_RESCAN_DATE`。状态为 completed 但目标日陈旧仍是异常，从而闭合“状态写入失败留下旧 completed”的监控缺口。

## 5. 测试计划

先写测试并确认 RED，再实现：

1. 状态写入不触碰 `DatabaseManager`、原子 SQL、一次失败后重连成功、确定性错误不重试、耗尽返回 False、连接清理；
   用真实挂起子进程验证父进程 deadline 和 PID 回收。另加可选真实 PostgreSQL 集成用例，
   在随机临时 schema 覆盖旧三列表迁移、真实 JSONB 往返与行锁 timeout 后新连接成功，并用最小数据集执行
   monitor 原始覆盖率 SQL，证明池外指数、重复股票行和非目标频率不会虚增覆盖。
   只允许 `TEST_DB_NAME` 以独立命名片段 `test`/`pytest` 标识隔离库（禁止 `latest` 等子串误命中）；发布前必须补跑。
   配置测试参数化覆盖两个新增键缺失、bool/字符串/负数、`retries < 1`、`delay < 0`，以及总预算恰好等于
   `recovery_deadline_buffer_seconds` 必须拒绝、少 1 秒必须接受。
   每个 completed/failed、kline_update/backfill 调用分支还要验证：返回 False 时 CRITICAL 同时包含 task、target_date
   和原业务结果，不得静默继续。
2. 参数化完整会话真值表；两次“无进度的异常会话”后第三次恢复成功，不得提前 `no_progress`；
   `exit=0 + result 缺失` 必须异常；现有正常合法零进度测试继续证明两轮后停止。
3. 用最小数据集合验证监控覆盖：stocks={A,B}，klines={A,池外指数、A 重复频率/其他日期}，结果必须为 1/2；
   pool=0 和重复 code 进入边界验证。
4. 极弱内联块及 shell harness 覆盖合法 blocked_data 豁免、NULL count、坏 JSON、selected>0、无记录、查询失败、
   非法 mode 和连续两轮去重状态迁移。
   `check_obpc_task_status` 的 shell harness 另覆盖：kline_update completed 但目标日陈旧、backfill completed 但目标日陈旧
   均必须告警；查询失败/缺行仍保持 fail-closed；目标日恢复后才能清除旧异常状态。
5. 目标测试后运行全量 `pytest tests -q -rs`、`compileall`、`bash -n scripts/monitor.sh`、YAML 解析和
   `git diff --check`。

## 6. 发布与回退边界

候选代码通过独立审查与隔离 PostgreSQL 门禁后，已按单独授权提交、推送并部署。上线继续保持 `hard_filter`；若状态写入或
采集恢复出现回归，回退到部署前提交 `e8110c8bf8c19b33135ce9193476af3c51d36cd0`，并依靠既有 07:00 backfill 与
98% fail-closed 门禁保护扫描。

**风险级别：R3**（生产调度恢复、数据库状态一致性、并发锁与 fail-closed 链路）。

## 6.1 生产证据边界

本次容量判断只用于确定保护逻辑，不把单次事故当作稳定性能基准。生产证据来自 2026-10-22 23:27—23:45 告警、
2026-10-23 只读 task_status/scan_runs/klines 查询以及当前配置；原容器于 09:46 重建，完整分片日志已丢失，故具体
Baostock 原始异常仍标记为“会话级卡顿的高概率判断”，不声称已精确定位。p50/p95 基准继续 BLOCKED。

## 7. 实施状态

已完成候选代码与聚焦回归：

- 异常 worker 与内容无进展计数已解耦，PID 未回收时在任何轮后 DB 查询前立即停止。
- `task_status` 运行期已改为轻量原子 UPSERT + 可终止子进程，128 秒最坏预算与
  300 秒余量强约束。
- 监控覆盖率改为 `klines INNER JOIN stocks`，任务状态增加目标日校验，
  `blocked_data` 仅在合法空指标且信号数为整数 0 时抑制重复告警。
- 无数据库环境聚焦回归 `114 passed, 3 skipped`；全量 `430 passed, 11 skipped, 3 warnings`。
- 隔离数据库 `stockfilter_pytest` 专项文件 `10 passed`（含 3 项真实 PostgreSQL 行为测试）；同一环境全量
  `441 passed, 0 failed, 0 skipped, 6 warnings`。四张测试业务表与随机临时 schema 残留均为 `0`，随后已删除
  隔离数据库并关闭本地 `15433` SSH 隧道。
- 独立复审两轮提出的 P1/P2 均已修复，最终只读回看通过。

## 8. Git 与生产部署验收

- 发布提交：`2801b561b9ab461d19ad4f73b971680b5b949b05`；GitHub Actions run 30 的 kline、obpc、
  eadvisor 三个构建 job 与自动部署 job 全部成功。
- 三个生产容器均为新镜像、`healthy`、`RestartCount=0`；部署日志记录同一完整 SHA。
- 容器内 `kline_collection.py`、`scheduler_kline.py`、`monitor.sh` 与本地提交 SHA-256 一致；宿主机与两个相关容器的
  `config.yaml` 摘要一致，实读 `market_regime_policy.mode=hard_filter`，三项 task_status 重试参数为 `3/1/1`。
- 生产 `task_status` 已包含 `task_name/last_completed_at/status/target_date/details(JSONB)`；部署后三个容器新增
  error/exception/fatal/traceback 日志均为 0。
- 发现 CI 只同步 compose/config，不同步宿主机实际运行的 `scripts/monitor.sh`。已备份旧脚本为
  `monitor.sh.bak.20261023-2801b56`，原子替换为当前提交并重启；摘要一致且仅有一个 `bash monitor.sh` 进程。
  后续应将该同步与单实例重启步骤纳入 CI，避免再次依赖手工补部署。

**状态：代码、独立复审、隔离 PostgreSQL、提交推送和生产五层技术验证全部通过；生产保持 `hard_filter`。下一门禁为
2026-10-23 23:00/23:15 首个真实调度窗口及连续 5 个交易日观察。**
