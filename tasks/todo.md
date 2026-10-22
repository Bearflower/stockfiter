# K 线采集进程隔离与断点续采任务清单

- [x] Task 1：定义共享监督者、分片工作进程、result JSON、退出码和 task_status.details 契约
- [!] 基准：本地只读采集约 100 只股票，记录 p50/p95/max 与总耗时并推导配置初值
      —— BLOCKED：2026-10-22 两次尝试均在 Baostock 登录阶段被连接重置
      （`[Errno 54] Connection reset by peer` / `网络接收错误`），未取得 p95。
      已按用户授权改为「生产证据推导初值」，推导依据写入 config.yaml 注释与实施记录，
      待故障注入阶段复测校准。
- [x] Task 2：彻底移除 Baostock 线程池超时，实现纯串行、可独立终止的分片工作进程
- [x] Checkpoint A：契约兼容、幂等写入、目标日校验与单元测试通过
- [x] Task 3：实现共享监督者的进程级超时、terminate/kill 和数据库断点续采，覆盖 scheduler 与 main.py --all
- [x] Task 4：主更新失败后立即用新进程恢复，不等待 07:00
- [x] Checkpoint B（本地故障注入）：第 610 只超时后旧进程退出、新进程从缺口续采、覆盖率达 98%+、无残留 PID
      —— 通过 FakeRunner 故障注入验证；真实 Baostock 故障注入待复审后在生产观察期执行
- [x] Checkpoint B2（第二轮复审 P1/P2 修复）：永久挂死股票隔离 + 真实覆盖率持久化
      —— 隔离队列 `quarantined_codes`（普通缺口优先、额度用完 `quarantined_exhausted`）；
         `details.coverage_rate` 固定真实覆盖率、处理比例另存 `processed_rate`；
         超时 failed/skipped 契约、Loguru 日志、`total_failure_limit < chunk` 校验、
         strict 截止、DB 连接/语句超时、monitor 两任务齐全校验全部完成；
         新增两组回归用例，全量 366 passed / 8 skipped，等待复审
- [x] Checkpoint B3（第三轮复审 P1/P2 修复）：连续多股挂死 + 数据库启动期超时
      —— 新隔离动作不计入连续无进展；连续两只永久挂死时其余 98/100 正常完成；
         配置在数据库访问前 fail-closed，股票池回退和构造期迁移继承 connect/statement timeout；
         新增 4 个回归用例，全量 370 passed / 8 skipped，等待独立复审
- [x] Checkpoint B4（第四轮复审整改）：全量配置强校验 + 调度器状态库超时 + 严格截止回收预算
      —— 调度器任何 DB 访问前校验；状态建表/写入显式传 connect/statement timeout；
         构造期迁移失败关闭连接；worker 时限预留 TERM/KILL 两段回收预算；
         目标测试 54 passed，全量 376 passed / 8 skipped，等待独立复审
- [x] Checkpoint B5（第四轮独立复审追加整改）：worker 子入口校验 + DB 查询截止预算 + 熔断可达性
      —— worker 重载配置后再次强校验；轮前/轮后/最终覆盖查询纳入绝对截止；
         failure_window_min_samples 同时受 window 与 chunk 上限约束；
         目标测试 56 passed，全量 378 passed / 8 skipped；独立复审回看通过，无新增 P1/P2/P3
- [x] Task 5：补齐 completion mode、会话重建次数和最终覆盖率监控，关联降噪
- [ ] Task 6：完成本地故障注入、全量回归、hard_filter 部署与五层验证
      —— 本地部分已完成（全量回归 + 静态检查 + compose 语法），部署未授权，等待复审
- [ ] Checkpoint C：连续观察 5 个交易日，确认回补恢复为兜底路径
