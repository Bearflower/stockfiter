---
id: stockfilter_v3-20261013-task-status-tz-roundtrip
类型: issue
项目: stockfilter_v3
标题: task_status 时区往返错配致 wait 永久超时
日期: 2026-10-13
标签: [bug, 调度器, 时区]
任务标识: K线容器DNS故障排查与恢复_2026-09-17.md
状态: 已解决
审查: 已审查
---

# task_status 时区往返错配致 wait 永久超时

## 现象
2026-10-13 监控通知「stockfilter-kline 54 个错误」→ 三步排查，确认与 10/10 同型并定位到第二个更严重根因。实测旧逻辑 `elapsed=32498s`（真实仅约 3700s）。后果：**07:30 补全后重扫自 10/9 起从未执行**（每日走 `else` 分支「跳过重扫」），22:15 夜扫每天也白等 1800s。

## 根因
缺陷B（时区往返错配）：`scheduler_kline._write_task_status` 写入 `datetime.now(TZ)`（带 +08:00 的北京时间）到 `TIMESTAMP`（无时区）列，而 DB 会话时区为 `UTC` → PG 转存为 UTC 裸值；`scheduler_obpc.wait_for_kline_update/wait_for_backfill` 读取裸值却按 Shanghai 解析 → `elapsed` 多算 +28800s(8h) → 永远 > 3600 阈值 → **等待必超时**。

## 解决
`scripts/scheduler_obpc.py` 两处 wait 的裸时间戳改按 `timezone.utc` 解析并与 UTC 比较。已提交 f057cde 并 push main，Actions 构建 3 镜像 + 服务器 pull/up，10/13 09:11 三容器重启 healthy。

## 验证
`py_compile` 通过；实测修正后 backfill `elapsed≈3980s`（真实值）；C 段独立审查通过；kline/obpc 容器内文件 MD5 与本地完全一致、启动无错误；10/13 09:11 三容器重启 healthy。

## 适用范围与修订
scripts/scheduler_obpc.py；commit f057cde 已上线。同型 task_status 时区问题在 [obpc链路完整修复_2026-09-18.md](../../handoffs/obpc链路完整修复_2026-09-18.md) 中另记有「kline 写入改用 naive datetime」的早期修复方向；本条目以 f057cde 的读侧 UTC 解析修复为准。无替代条目。

## 相关遗留（同源 handoff 记录，未在本次修复范围内）
- ① 07:30 重扫恒 0 信号（待用户决策的新遗留）：重扫用 `--signal-date=当天`，但 `freshness_strategy=skip` 要求指数数据日期==signal_date，当天指数数据尚不存在 → 重扫恒 0 信号（实测报「指数 000300.SH 最新日期 10/12 != 目标 10/13，跳过全场开仓」）。疑为信号日期语义/配置问题，未擅改。**此遗留直接决定本条「重扫失效」修复后的实际效果，不可据本条「已解决」推断 07:30 重扫链路已完全可用。**
- ② `update_kline_daily.py` 的 `_signal_handler` 仍用 `sys.exit(0)`：SIGTERM 路径同样会卡 atexit（非 3h 超时路径），未在本任务处理。

## 教训
「时区往返」是隐式 bug 温床——写侧带时区、存列无时区、读侧按本地时区解析，三处口径不一致会叠加出固定偏移（本例 8h）。排查「永远超时 / 永远成立」的阈值判断时，先核对时间戳在入库-出库全链路是否同一口径。（AI 归纳，仅供参考）

## 关联
- 本地补充来源: [handoff](../../handoffs/K线容器DNS故障排查与恢复_2026-09-17.md)（不随 Git 同步）
- 持久证据: commit f057cde（scripts/scheduler_obpc.py 两处 wait 解析改 UTC）；服务器核验 kline/obpc 容器内文件 MD5 一致
- 必要证据摘要: 旧逻辑 elapsed=32498s / 修正后≈3980s；后果为 07:30 重扫自 10/9 起从未执行；f057cde 部署后 10/13 09:11 三容器 healthy
- 相关: [大盘过滤切回 V2.0 ma_position](../decisions/stockfilter_v3-20261013-obpc-index-filter-back-to-ma-position.md)（同属 OBPC 调度链路问题域）