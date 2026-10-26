# K 线采集锁等待热修与跨部署日志留存

## 事故证据（2026-10-23，北京时间）

- 23:00 至 23:15，前 5 个分片正常完成，目标日覆盖达到 1469/2911。
- 23:15 OBPC 开始轮询 `task_status`；随后 5 个分片各耗尽进程超时，新增覆盖均为 0。
- 23:39 主采集以 `stop=deadline` 失败，三次 `task_status` 写入均遇到 30 秒 `statement_timeout`。
- 23:45 OBPC 结束轮询并关闭连接；07:05 历史补全将覆盖提升到 2907 只。初扫以 `blocked_data` 阻断，重扫随后运行。

原因：OBPC 的 psycopg2 连接在轮询 `SELECT` 后保持事务；运行期新建 `DatabaseManager` 又执行 `ALTER TABLE task_status ADD COLUMN IF NOT EXISTS`。读事务所持的表锁使 DDL 等待，排队的 DDL 又阻塞后续查询与状态写入。另有 `ChunkResult.processed_count` 缺失，导致每只股票的进度文件更新失败，超时后不能可靠识别正在处理的代码。

## 修复与验收

- `DatabaseManager` 默认不执行 DDL；K 线服务启动时显式完成一次全库建表/迁移，之后采集、扫描和监控连接均不迁移。纯读监督/等待连接使用 autocommit，避免轮询长期持有表锁。`task_status` 目标列已存在时，启动期也跳过重复 ALTER。
- `ChunkResult.processed_count` 与 result JSON 的 `processed` 保持同一计算口径。
- CI 拉取新镜像后先停止三个旧容器，冻结 Docker 日志，再将 `docker logs --timestamps` 压缩归档到服务器 `logs/container_archive/`；归档失败恢复旧容器并中止部署。新容器的 stdout/stderr 同时写入 `logs/services/{kline,obpc,eadvisor}.log`，各自按大小轮转。`docker logs` 仍可用于查看当前容器。
- 日志代理收到容器停止信号时同时通知调度器与独立进程组的 K 线分片，避免分片脱离退出链。归档目录不自动删除旧文件，需监测宿主机磁盘占用并按人工批准的保留策略清理。
- 持久日志轮转参数由 Compose 环境变量 `PERSISTENT_LOG_MAX_BYTES`（默认 20971520）和 `PERSISTENT_LOG_BACKUP_COUNT`（默认 10）控制；退出宽限期由 `PERSISTENT_LOG_TERMINATE_GRACE_SECONDS`（默认 5）控制。服务器日志目录是卷挂载，容器重建不会删除。既往已经被 Docker 删除的旧容器日志无法恢复。

本地验收：临时隔离 PostgreSQL 下全量 455 passed、1 skipped；跳过的是仅在 Linux `/proc` 可运行的真实进程树信号集成测试，纯函数进程树测试已通过。上线后仍须核对 23:15 后分片继续新增覆盖、`task_status.kline_update` 写入当日状态、初扫只在覆盖达标时运行，以及容器重建前后的日志。继续保持市场风控 `hard_filter`，不改变选股策略。

**文档版本**：V1.0（2026-10-26）
