---
id: stockfilter_v3-20261013-obpc-index-filter-back-to-ma-position
类型: decision
项目: stockfilter_v3
标题: OBPC 大盘过滤从 MACD 切回 V2.0 ma_position
日期: 2026-09-18
标签: [obpc, 风控, 配置]
任务标识: obpc链路完整修复_2026-09-18.md
状态: 已采纳
审查: 已审查
---

# OBPC 大盘过滤从 MACD 切回 V2.0 ma_position

## 背景
9/8 部署后确认多个叠加 bug：① task_status 时区 bug；② 指数数据断更（`import_index_data.py` 从未被任何调度器调用，000300 从 8/26 起断更 → MACD DIF 用旧数据算 → 整个 9 月被拦截）；③ MACD DIF 计算正确但市场确实在空（DIF=-31）。需要就大盘过滤方式做决策。

## 备选方案
- A. 保持 V2.1 MACD 动量过滤（`dif > 0`）。
- B. 切回 V2.0 ma_position（均线位置过滤）。
- C. 关闭 index_filter 让信号自由产出（当时未决策）。

## 决策
切回 V2.0 ma_position：`config/config.yaml` 设 `index_filter.enabled: true`、`method: 'ma_position'`、`score_adjustment.enabled: true`（重新开启）。

## 理由
用户选择切回 V2.0 ma_position 并关掉 MACD 动量过滤。DIF 计算逻辑本身正确（标准 pandas ewm，沪深300 EMA12=4584 < EMA26=4616 < MA60=4738 三线空头排列）——并非计算错误，而是市场真实弱势。

## 后果
- 眼下收益：统一为均线位置过滤口径；指数断更修复后大盘风控数据恢复有效。
- 技术债/待观察：市场弱势期间 V2.0 均线过滤同样会拦截（4558 < MA20=4612 < MA60=4738）；要出信号需大盘站上 MA20，或调制过滤条件（更宽松选项均未决策）。

## 适用范围与修订
config/config.yaml（volume 挂载，重启热生效）、scripts/scheduler_obpc.py、scripts/data/index_update.py（新建）；已部署验证 stockfilter-kline / stockfilter-obpc 均 Up healthy。本决策至今有效、无替代条目。

## 关联与证据
- 本地补充来源: [handoff](../../handoffs/obpc链路完整修复_2026-09-18.md)（不随 Git 同步）
- 持久证据: config/config.yaml（index_filter.method=ma_position、score_adjustment.enabled=true）；scripts/data/index_update.py（新建）；两容器 Up healthy
- 必要证据摘要: 切回 V2.0 后大盘弱势下仍拦截（沪深300 4558 < MA20 4612）；V2.1 MACD 当时亦拦截（DIF=-31）
- 相关: [task_status 时区往返错配](../issues/stockfilter_v3-20261013-task-status-tz-roundtrip.md)（同属 OBPC 调度链路问题域）