# OBPC 初扫等待窗口提前告警修复

日期：2026-11-05（Asia/Shanghai）。基线：main / a65eb98e54c124eb33e91d07b5007cb7fe473dee。

## 需求、范围与根因

处理 2026-11-04 23:17 的两条 CRITICAL 告警：当日无 scan_runs、因此无法确认分级风控指标。只修改监控对“尚无运行记录”的告警期限，不修改策略、风控裁决、扫描状态模型或数据任务；不提交、推送或部署。

只读 SSH 查询生产 stockfilter-obpc 日志得到以下北京时间证据（未执行扫描、回测或数据库写入）：

| 时间 | 事实 |
|---|---|
| 23:15:13 | initial 调度触发，目标为 2026-11-04 |
| 23:17:13 | 正在等待 K 线；task_status 仍为 11-03 的 completed |
| 23:29:13 | 仍在正常等待窗口内 |
| 23:30:13 | 识别当天 K 线已于 23:29:41 完成，启动 daily_scan |
| 23:30:14 | scan_runs 写入 initial / running |
| 23:35:44 | scan_runs 写入 initial / blocked_market，raw=3、selected=0 |
| 23:35:45 | 调度器记录形态扫描完成 |

因此这次是监控提前要求存在运行记录，不能解释为整晚漏扫或真实风控穿透。告警发生时调度器尚在允许的数据等待阶段。

## 设计与验收条件

风险 R2：调整监控通知时机，不改变交易/策略风控行为。只对成功查询明确确认的无记录采用有界宽限，所有实际异常保持原检查。由 Codex 完成实现，并由另一个独立上下文代理复核；不声称执行 Trae 专属模型或跨模型审查。

- AC1：窗口为目标交易日 `SCHEDULE_SCAN <= now < SCHEDULE_SCAN + KLINE_WAIT_TIMEOUT`，均以北京时间计算。默认 23:15—23:45，不另增固定宽限值。
- AC2：两条“无记录”告警在窗口内只记录状态未知的日志，不发恢复通知，不清除历史异常去重状态。
- AC3：到达截止、目标是旧交易日、非法参数或无法解析时不豁免。窗口允许跨零点。
- AC4：查询失败、已有记录但指标缺失、真实穿透均继续告警。
- AC5：极弱监控同一次 SQL 查询返回第五个机器字段 no_run；仅 row is None 时为 1。不能靠中文消息匹配或第二次查询推断，避免并发写入造成错误豁免。
- AC6：监控目标切换时间也由 SCHEDULE_SCAN 派生，严格要求 HH:MM；与 compose 同读宿主机 .env。

固定期限按计划时点计算。实际调度初始化可能晚于计划时点，因此边界附近仍可能保守告警；本修复不将窗口等同于调度器存活证明。容器健康、数据任务失败等其他告警不被屏蔽。生产若单独覆盖容器环境变量，需确保监控宿主机配置同步。

## 实现与验证

- scripts/monitor.sh：共用等待窗口函数；两处分支只豁免无记录；no_run 字段；可配置目标切换。
- tests/monitor/test_monitor_initial_wait.py：提取真实 Bash 函数执行，桩替换时间、Docker 和通知；不触达外部服务。
- tests/monitor/test_monitor_extreme_weak_mode.py：执行真实内联 Python，验证 no_run 仅适用于无行。

先新增回归测试，在原实现观察到 **8 failed / 22 passed**：正常等待与跨午夜窗口仍发出缺失告警，证明测试暴露了原缺陷。修复后相关测试 **154 passed**。

已通过 `bash -n scripts/monitor.sh`、`git diff --check`，以及变更测试文件的 Ruff F821/F822/F823/E9 检查。

业务边界逐项覆盖 AC1—AC6；Shell 测试未生成数值化行/分支覆盖报告，不将场景覆盖宣称为 100% 工具覆盖率。生产只做日志读取，尚未部署修复，不能宣称线上告警已经消失。

## 经验复用与后续

检索 docs/knowledge/INDEX.md：task-status-tz-roundtrip 与 pg-container-vanished-dns 作为排查线索，不直接套用旧根因；此次日志明确证明目标日期守卫正常、数据最终就绪，根因是等待窗口与监控告警期限不一致。其余复权/投顾条目与此次问题无关。

部署需独立授权，按项目发布流程更新宿主机 monitor.sh 并验证实际文件、进程和配置。不要通过手动补写 scan_runs 或关闭 fail-closed 告警处理本事故。

## 最终验证与复审

最终命令：`python3 -m pytest tests/monitor tests/scheduler tests/integration/test_obpc_daily_pipeline.py tests/operations/test_monitor_deploy.py -q`。
结果：**269 passed，1 warning，6.93 秒**。警告为本机 Python 3.9 的 LibreSSL 与 urllib3 v2 兼容性提示，未导致测试失败。

独立上下文复核发现第五字段可能被诊断文本中的管道分隔符挤占；先补测试观察到 **2 failed / 13 passed**，再净化诊断文本中的管道、CR、LF。复审代理独立重跑重点测试 **54 passed**，确认该阻断问题关闭，检查范围内无其他阻断缺陷。这是同模型不同上下文审查，不是不同模型交叉审查。

最终 `bash -n`、Ruff 选定规则、`git diff --check` 均通过。用户原有 `.trae/rules/development-workflow.md` 类型变更及其他未跟踪文件未改动。生产发布尚未执行。

### 本地候选文件 SHA-256

- `scripts/monitor.sh`：`1fc7e8cd7dc4cc3405bb9ea662f2a7fa6787ee9ee49c4a9b48694e4f6f4367a4`
- `tests/monitor/test_monitor_initial_wait.py`：`1dadfe6d674a86f5ae5f15d22a2c32dbdd0df0e01942a671d8042ca75d7f5f4b`
- `tests/monitor/test_monitor_extreme_weak_mode.py`：`f78cb174a46fb159a331f1133cc596e1b95dac7e2d4ac633417db7628832dcc1`

## 后续发布状态（2026-11-05）

用户随后明确授权部署，08:29:58 已完成宿主机热修并通过版本、进程、就绪、首轮检查及时间边界烟测。上文“未部署”描述为本地开发交付时状态，现已由[发布记录](../deployment/2026-11-05-monitor-wait-hotfix.md)更新。发布时额外修复了辅助程序短命子进程识别竞态，测试与回滚证据见该记录。
