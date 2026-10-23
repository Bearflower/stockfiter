# K 线采集进程隔离与断点续采：实施记录

- 方案来源：`tasks/plan.md`、`tasks/todo.md`
- 实施日期：2026-10-22
- 状态：**本地实现与本地验证完成，未 commit / 未 push / 未部署，等待复审**
- 生产配置：保持 `hard_filter` 不变

## 1. 改造范围

| 文件 | 变更 |
|------|------|
| `scripts/data/kline_collection.py` | **新增**：契约常量、`CollectionConfig` 校验、截止时间推导、`DatabaseGapStore`、`SubprocessWorkerRunner`、`run_supervised_collection` |
| `scripts/data/update_kline_daily.py` | 重写：纯串行分片工作进程（`--worker`）+ 监督者业务入口 `run_update`；删除线程池伪超时；`load_kline_update_config` 改为公开共用加载器 |
| `scripts/scheduler_kline.py` | 23:00 分支改为进程内调用共享监督者，删除 3 小时外层 subprocess 超时与 result-json 临时文件；配置非法直接写 `failed/config_error` |
| `main.py` | `--all` 的 K 线步骤改用同一配置加载器与同一 `run_update`，保持返回值/退出码/后续任务语义 |
| `data/fetcher.py` | 删除无调用方的 `update_all_klines()`，消除不受监督的全量采集旁路 |
| `scripts/monitor.sh` | 解析 `task_status.details`；恢复事件一次性告警；失败告警携带 details；查询失败 fail-closed |
| `config/config.yaml` | 删除 `request_timeout_seconds`；新增 10 项分片、隔离与数据库超时配置并写明推导依据 |
| `docker-compose.yml` | kline-service 与 strategy-obpc 同源注入 `SCHEDULE_SCAN` / `KLINE_WAIT_TIMEOUT` |
| `tests/scheduler/test_kline_guard_and_completeness.py` | 熔断测试改为直接测 `collect()`；result-json 契约增加 `completion_mode` 与扁平化字段 |
| `tests/scheduler/test_kline_process_isolation.py` | **新增**：11 个故障注入与 fail-closed 测试 |

## 2. 关键契约

**工作进程退出码**

| 码 | 含义 |
|----|------|
| 0 | 分片内全部股票已尝试（允许单股失败） |
| 1 | 分片内被失败率/累计失败熔断提前终止（result JSON 仍写入） |
| 2 | 配置、数据库或未捕获异常，分片无法继续（result JSON 可能缺失） |

**监督者停止原因（`details.stop_reason`）**：`coverage_reached` / `no_missing` / `deadline` / `restart_limit` / `no_progress` / `worker_fatal` / `db_error` / `config_error` / `coverage_short` / `quarantined_exhausted`

**完成模式（`details.completion_mode`）**：`normal` / `recovered_after_timeout` / `failed`。`task_status.status` 枚举保持不变（`completed` / `failed`）。

**覆盖率口径**：最终以数据库为准。`success = 目标日已落库数`，`failed = 尝试过但最终仍缺目标日`，`skipped = 从未尝试`。`is_complete()` 既有 4 步判定顺序与 `min_coverage_rate=0.98` 红线不变。

**超时定位**：工作进程在请求每只股票前写 `progress JSON.last_started_code`（原子 `os.replace`），监督者超时后读取该文件，从而回答“第几只/哪只股票卡死”。

## 3. 配置初值推导

本地 100 只基准 **BLOCKED**：2026-10-22 两次尝试均在 Baostock 登录阶段失败（`[Errno 54] Connection reset by peer`、`{"login_failed": "网络接收错误。"}`），未取得 p50/p95/max。按用户授权改用生产证据推导：

| 参数 | 初值 | 依据 |
|------|------|------|
| `worker_chunk_size` | 300 | 生产 2909 只夜间任务正常完成；常态单股 < 0.5s 推算单分片 < 150s，覆盖股票池约 10%，超时后能快速重查缺口 |
| `worker_timeout_seconds` | 300 | 约为常态分片耗时的 2 倍；单只挂死最多浪费 300s，已落库股票不重复采集 |
| `worker_terminate_grace_seconds` | 10 | 足够 Python 响应 SIGTERM 关闭 Baostock socket；否则 SIGKILL 由 OS 回收线程/连接 |
| `max_session_restarts` | 6 | 每次超时只损失 1 只挂起股票，6 次约覆盖股票池 0.2% 的离散挂死 |
| `recovery_deadline_buffer_seconds` | 300 | 为 OBPC 初扫等待窗口预留 5 分钟写库与扫描启动 |
| `immediate_recovery_enabled` | true | 主采集失败立即断点恢复，不等次日 07:00 |
| `max_consecutive_no_progress_rounds` | 2 | 连续 2 个分片既无新增落库、也没有新识别并隔离挂死股票，说明 Baostock/数据库整体不可用 |

截止时间同源公式（禁止双源）：`collection_deadline = initial_scan_time(SCHEDULE_SCAN) + KLINE_WAIT_TIMEOUT - buffer`。默认 23:15 + 1800 - 300 = 23:40。

## 4. 验证证据（2026-10-22 本地）

| 检查 | 命令 | 结果 |
|------|------|------|
| 目标测试 | `python3 -m pytest tests/scheduler/test_kline_process_isolation.py tests/scheduler/test_kline_guard_and_completeness.py -q` | 44 passed |
| 全量回归 | `python3 -m pytest tests/ -q` | **366 passed, 8 skipped**（8 项为需 `TEST_DB_PASSWORD` 的真实 PostgreSQL 用例） |
| 监控测试 | `python3 -m pytest tests/monitor -q` | 82 passed |
| 编译 | `python3 -m compileall -q scripts/data/kline_collection.py scripts/data/update_kline_daily.py scripts/scheduler_kline.py main.py data/fetcher.py` | OK |
| Shell 语法 | `bash -n scripts/monitor.sh` | OK |
| 真实 CLI 冒烟 | 仅注入仓库根 PYTHONPATH 启动 `--worker`（非法 symbols） | exit 2，无 `ModuleNotFoundError` |
| 配置加载 | `load_kline_update_config` → `load_collection_config` | 10 项配置正确解析（chunk=300 / timeout=300 / total_failure_limit=250 / max_quarantined_retries=1 / DB 超时 30/10） |
| Compose 语法 | `python3 -c "yaml.safe_load(...)"` | YAML_OK（本机无 docker compose 插件，未做 `docker compose config`） |

核心故障注入用例 `test_timeout_on_610th_stock_recovers_from_database_gap` 断言：2911 只股票、首个分片第 300 只（`codes[299]`）超时后，旧进程 `killed=True` 且 `pid_gone=True`，第二轮首码跳到 `codes[300]`（跳过隔离股票），最终 `covered=2910`、`coverage_rate≥0.98`、`failed=1`、`completion_mode=recovered_after_timeout`、`session_restarts=1`，全部分片合计只尝试 2911 次（挂死股票仅在首轮尝试一次）。

## 5. 未完成与风险

1. **未部署**：按用户要求未 commit / push / deploy；生产仍为 `hard_filter`。
2. **真实故障注入未执行**：本地故障注入为 FakeRunner 模拟；真实“第 610 只超时”及连续多股超时需在生产观察期用受控方式验证。
3. **配置初值未经实测校准**：基准 BLOCKED，初值来自生产证据；若真实分片耗时显著偏离，观测日志中的 `分片完成 ... 耗时` 与 `timed_out_chunks` 后再调整。
4. **8 个 PostgreSQL 用例跳过**：需 `TEST_DB_PASSWORD` 环境；本次改动未涉及这些表的写入路径变更。
5. **单股失败口径变化**：`fetch_kline` 返回 `None`（Baostock 内部失败）现在会计入分片失败与熔断滑窗；此前该路径既不计成功也不计失败，会低估失败率。

## 6. 复审关注点

- 截止时间是否确实只由 `SCHEDULE_SCAN` + `KLINE_WAIT_TIMEOUT` - buffer 一处推导（`kline_collection.compute_collection_deadline`）。
- `SubprocessWorkerRunner` 的进程组终止与 PID 消失确认是否满足“无残留进程”。
- `run_supervised_collection` 的停止优先级：覆盖率红线 → 无缺口 → 截止时间 → 单轮执行 → 重建上限 → 无进展。
- `monitor.sh` 的恢复告警去重键是否按 `目标日|stop|restarts|timeouts` 隔离。
- `main.py --all` 的返回值/退出码/后续任务语义是否与改造前一致。

## 7. 第二轮复审修复记录（2026-10-22）

复审复现 2 个 P1 阻断问题，本节记录修复内容与回归证据。

### 7.1 P1-1：持续挂死股票阻塞续采

- 旧行为：每轮都从数据库第一个缺口开始，同一只永久挂死股票反复成为队首，连续无进展后以低覆盖率停止（实测 `covered=299/2911`、`stop_reason=no_progress`）。
- 新行为：超时后把 `last_started_code` 写入 `quarantine_attempts`；`_select_chunk_codes()` 始终优先选择普通缺口，普通缺口耗尽后才按 `max_quarantined_retries` 重试隔离股票；额度用完以 `stop_reason=quarantined_exhausted` 停止，挂死股票进入 `quarantined_codes` 与 `failed` 明细。
- 回归：`test_persistent_hang_stock_quarantined_not_blocking_others`（100 只 / chunk=10 / 第 6 只永久挂死）验证其余 99 只全部采完、挂死股票只尝试 2 次、`failed=1`、`stop_reason=quarantined_exhausted`、所有超时进程 `pid_gone=True`。

### 7.2 P1-2：真实覆盖率被处理比例覆盖

- 旧行为：调度器用 `result.coverage_rate`（success+failed+skipped，恒 ≈1.0）覆盖 `details.coverage_rate`，610/2911 会被监控显示成 100%。
- 新行为：`build_task_status_details()` 只补充 `completion_mode` / `target_date_coverage_rate` / `processed_rate`，绝不覆盖监督者的 `details.coverage_rate`；调度日志与 `main.py` 的 PASS/FAIL 均读取 `target_date_coverage_rate`。
- 回归：`test_real_coverage_rate_persisted_not_overwritten` 验证 `details.coverage_rate ≈ 0.209550`、`processed_rate == 1.0`，且合并后仍不被覆盖。

### 7.3 P2 完善项

| 项 | 修复 |
|----|------|
| 超时股票被误记为 skipped | `_attempted_codes()` 依据 `result.processed` 前缀、`failed_codes`，或超时无 result 时按 `last_started_code` 在分片中的位置推导已尝试前缀；`failed = attempted ∩ 未落库`，`skipped` 仅表示从未尝试 |
| 日志格式与异常栈 | `kline_collection.py` 的 Loguru 新增日志使用 f-string 与 `logger.opt(exception=True)`；`scheduler_kline.py` 使用标准库 logging 的参数化日志与 `exc_info=True` |
| `total_failure_limit` 与分片规模矛盾 | config 改为 250，`load_collection_config` 强校验 `< worker_chunk_size` |
| 截止时间严格性 | `compute_collection_deadline(strict=...)`：调度器 `strict=True`（已过即失败），`main.py --all` `strict=False`（滚动窗口） |
| 数据库无超时 | 配置先于数据库访问校验；股票池回退与监督者连接均传 `connect_timeout`，并在 libpq 连接 `options` 中注入 `statement_timeout`，覆盖构造期迁移与后续查询 |
| `monitor.sh` 不完整查询误判 | 单次查询两任务，异常输出 `QUERY_FAILED|connect|...` / `QUERY_FAILED|query|...` 哨兵，并强校验结果同时包含 `kline_update` 与 `backfill` 两行 |

### 7.4 本轮复验证据

| 检查 | 命令 | 结果 |
|------|------|------|
| 聚焦测试 | `python3 -m pytest tests/scheduler/test_kline_process_isolation.py -q` | 19 passed |
| 目标测试 | `python3 -m pytest tests/scheduler/test_kline_process_isolation.py tests/scheduler/test_kline_guard_and_completeness.py -q` | 44 passed |
| 全量回归 | `python3 -m pytest tests/ -q` | **366 passed, 8 skipped** |
| 编译 | `python3 -m compileall -q scripts utils data main.py` | OK |
| Shell 语法 | `bash -n scripts/monitor.sh` | OK |
| 配置加载 | `load_collection_config(load_kline_update_config(...))` | OK（chunk=300 / total_failure_limit=250 / 隔离重试 1 / DB 30+10） |

仍未 commit / push / deploy。Baostock 本地基准仍 BLOCKED（`10002007 网络接收错误 / Socket is not connected`），`chunk=300 / timeout=300s` 继续作为待生产校准的临时初值。

## 8. 第三轮复审修复记录（2026-10-22）

1. **连续多只永久挂死不再误触发无进展停止**：新识别并加入隔离队列属于有效状态推进；只有“无新增覆盖、非隔离重试、且无新隔离股票”才增加无进展计数。回归用 100 只股票、前 2 只连续永久挂死验证其余 98 只全部落库，两只挂死股票各按额度尝试 2 次，最终 `failed=2`、`skipped=0`、`stop_reason=quarantined_exhausted`。
2. **数据库启动期超时闭环**：`run_update()` 在股票池加载前校验 `CollectionConfig`；固定股票池文件缺失时，数据库回退继承连接/语句超时；`DatabaseManager` 从建连时通过 libpq `options` 设置 `statement_timeout`，构造期建表与迁移不再处于无界等待窗口。
3. **新鲜验证**：新增 4 个回归用例先红后绿；目标两文件 `48 passed`；全量 `370 passed, 8 skipped, 3 warnings`。8 个跳过项仍为未配置 `TEST_DB_PASSWORD` 的真实 PostgreSQL 用例。

**最后更新**：2026-10-22（第三轮复审修复）

## 9. 第四轮复审整改记录（2026-10-22）

1. **全量配置统一强校验**：`load_collection_config()` 现覆盖采集窗口、失败率熔断、完整性阈值、分片、隔离、截止余量和数据库超时参数；拒绝 bool 冒充数值、NaN/Inf、非法类型与范围，并强制 `failure_window_min_samples <= failure_window_size`。调度器启动及每轮任务都先校验，再进行建表、指数更新或状态写入。
2. **调度器数据库超时闭环**：`_ensure_task_status_table()` 与 `_write_task_status()` 必须显式接收并传递 connect/statement timeout；`DatabaseManager` 构造期建表或迁移失败时主动关闭已建立连接。
3. **绝对截止包含进程回收预算**：每个 worker 的最大运行时间从剩余窗口扣除 `SIGTERM grace + SIGKILL 回收等待`，不再把完整 remaining 全部分配给子进程。真实忽略 SIGTERM 的子进程回归覆盖了超时、升级 SIGKILL 与截止容差。
4. **文档与异常路径校正**：`SCHEDULE_SCAN` 默认值统一为 23:15；调度器标准库 logging 异常栈改用 `exc_info=True`，不再误用 Loguru 的 `logger.opt()`。
5. **新鲜验证**：目标两文件 `54 passed`；全量 `376 passed, 8 skipped, 3 warnings`；compileall、`bash -n scripts/monitor.sh`、YAML 解析、生产配置强校验与 `git diff --check` 均通过。8 个跳过项仍为未配置 `TEST_DB_PASSWORD` 的真实 PostgreSQL 用例。

仍未 commit / push / deploy；生产策略配置保持 `hard_filter`。

**最后更新**：2026-10-22（第四轮复审整改，等待独立复审）

## 10. 第四轮独立复审追加整改（2026-10-22）

1. **worker 子入口 fail-closed**：`run_worker()` 在构造 `DailyKlineUpdater` 或访问数据库前，对重新加载的配置再次调用 `load_collection_config()`，避免父进程校验后配置漂移或直接执行 `--worker` 绕过强校验。
2. **数据库查询纳入绝对截止**：轮前覆盖查询只在剩余窗口大于完整 DB 查询预算时启动；worker 可运行时间额外预留轮后查询预算；轮后及最终查询无预算时复用最近覆盖快照，保持保守失败而不越界。
3. **失败率熔断可达性**：新增 `failure_window_min_samples <= worker_chunk_size` 强约束，同时保留其不大于 `failure_window_size` 的约束。
4. **交付索引同步**：`docs/README.md` 更新为第四轮记录及当前测试数字。
5. **新鲜验证**：目标测试 `56 passed`；全量 `378 passed, 8 skipped, 3 warnings`。

仍未 commit / push / deploy；生产策略配置保持 `hard_filter`。

### 10.1 独立复审回看结论

结论：**通过**，未发现新的 P1/P2/P3。

- worker 非法配置探针：`exit_code=2`、`db_accessed=False`；
- SlowStore 仅剩 0.05 秒探针：`elapsed=0.001483s`、`query_calls=0`、`stop_reason=deadline`；
- 500 最小样本 / 300 分片配置：抛出 `CollectionConfigError`；
- worker 已写库但轮后无查询预算：复用旧快照并保守失败，`covered=0 / failed=1 / skipped=0`，计数闭合且不误报完成；
- 独立复跑：目标 `56 passed`，全量 `378 passed, 8 skipped, 3 warnings`，静态检查全部通过。

**最后更新**：2026-10-22（第四轮独立复审回看通过）

## 11. 生产观察后韧性跟进（2026-10-23）

2026-10-22 夜间再次出现单分片超时，且异常会话被误计为连续无进展，使主采集在
`1682/2911` 覆盖时提前停止；失败状态写入又被 SQL timeout 取消，留下旧的 `completed`
记录。次日 backfill 成功补至红线，证明保护门生效，但主路径尚未达到可接受稳定性。

本轮跟进实现异常会话/内容无进展解耦、轻量 `task_status` 原子写入与有界重试、旧状态
目标日校验、覆盖集合同口径以及 `blocked_data` 严格降噪。完整验收记录见
`docs/reports/2026-10-23-kline-resilience-followup.md`。本轮不改策略阈值，生产模式仍为 `hard_filter`。
