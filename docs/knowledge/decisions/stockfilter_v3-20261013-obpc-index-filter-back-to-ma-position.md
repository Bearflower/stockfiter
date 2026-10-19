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

---

## 2026-10 回测 BLOCKED 补充（**已被下方实测取代，保留原始历史记录**）

**触发**：Task 7 Step 4 / Step 5，对 6 个市场风控方案做本地全历史回测。

**结果**：BLOCKED。`scripts/backtest_obpc.py` 仅通过 `data.database.DatabaseManager` + `pd.read_sql_query` 从生产 PostgreSQL 取数（无 CSV 数据源开关）；本机两条 DB 路径均 pg 握手失败：

1. `DB_HOST=common_service_postgres`（本机 DNS 解析 198.18.1.9，5432）→ `server closed the connection unexpectedly`
2. SSH 隧道到 `43.156.242.184:5432`（容器 `common_service_postgres` 监听 5432/tcp 但未主机端口映射）→ 同样错误

本机虽有 `data/backtest/klines_mainboard.csv`、`stocks_mainboard.csv`、`hs300_index.csv` 历史导出，但回测脚本当前不读它们；增加 CSV 数据源开关需独立 MR，不能在本次任务内静默修改。

**对本决策的影响**：本轮回测**未能**为 6 个方案提供任何可复核的胜率、平均收益、硬止损占比、2026 Q1 信号数等指标。**既没有证明 ma_position 已达标，也没有证明它不达标**。

**维持的基线**：`config/config.yaml` 继续保持 `index_filter.method = ma_position`（本决策 2026-09-18 已采纳，Task 7 Step 2 已恢复）。**本轮回测不改变生产配置**，也不放宽任何风控阈值——完全遵守 Task 7 Step 5 明确要求："无方案全部满足时，保持 ma_position 已采纳基线，不放宽生产风控，并在报告中记录失败指标（此处失败指标 = 未能执行）"。

**解除阻塞前提**（任选其一）：
1. 恢复 DB 连通（`common_service_postgres` 增加主机端口映射 `5432:5432` 或调整 pg_hba/TLS）；
2. 给 `backtest_obpc.py` 增加 CSV 数据源开关（需独立 MR，审批后方可执行）。

**完整阻塞证据**：见 [OBPC 市场风控方案回测报告（2026-10）](../../reports/obpc-risk-policy-backtest-2026-10.md)。

---

## 2026-10 回测实测（取代上方 BLOCKED 段，2026-10-19 下午）

**触发**：用户授权新增 CSV 数据源分支后，按 Task 7 Step 4/5/6 完成实测。

**实测范围**：300 只主板指示性样本（前 300 只），2025-07-31 ~ 2026-10-16（CSV 数据全市场宽度窗口，仅约 15 个月）。注意：2020-2024 年 CSV 仅 3 个 code，无法覆盖全量；全量 2,912 只单进程需 9 小时，受工具 timeout 限制用 300 只 1/10 抽样。

**验收线 5 条判定结果**（全部 6 方案 × 5 条 = 30 个判定点）：

| 验收线 | 方案1 | 方案2 | 方案3 | 方案4 | 方案5 | 方案6 |
|--------|-------|-------|-------|-------|-------|-------|
| 胜率 ≥ 60% | ❌ 18.2% | ❌ 30.0% | ❌ 30.0% | ❌ 0.0% | ❌ 0.0% | ❌ 16.0% |
| 平均收益 ≥ 2% | ❌ -3.39% | ❌ -2.24% | ❌ -2.24% | ❌ -6.24% | ❌ -6.15% | ❌ -3.98% |
| 硬止损占比 ≤ 20% | ✅ 18.2% | ✅ 20.0% | ✅ 20.0% | ❌ 33.3% | ✅ 15.4% | ✅ 20.0% |
| 交易数 ≥ 20 | ❌ 11 | ❌ 10 | ❌ 10 | ❌ 6 | ❌ 13 | ✅ 25 |
| 2026 Q1 交易 ≤ 15 | ✅ 0 | ✅ 0 | ✅ 0 | ✅ 0 | ✅ 0 | ✅ 0 |

**关键数字**（来自真实运行输出，无编造）：

- 方案 1（MACD DIF>0）：11 笔交易，胜率 18.2%，平均收益 -3.39%，MACD 拦截 29 次
- 方案 2（ma_position+MA20 斜率）：10 笔交易，胜率 30.0%，平均收益 -2.24%，斜率拦截 34 次
- 方案 3（MACD+MA20 斜率双重）：与方案 2 结果完全一致（拦截集在本窗口内等价）
- 方案 4（MA60 斜率）：6 笔交易，胜率 0.0%，硬止损占比 33.3%（超标）
- 方案 5（ma_position+评分调整）：13 笔交易，胜率 0.0%，评分归零 15 次
- 方案 6（无大盘硬过滤）：25 笔交易，胜率 16.0%，平均收益 -3.98%，硬止损 5 次

**对本决策的影响**：
- 本轮回测**未产生"全部满足 5 条验收线"的方案**——最接近的方案 2/3 四条挂三条（胜率差 30pp、平均收益差 4.24pp、交易数差 10）。
- 方案 6（完全去掉大盘硬过滤）交易数 25 达标、硬止损 20.0% 达标，但胜率 16.0% 和平均收益 -3.98% 全部挂——说明**在这个弱势窗口里，放宽大盘风控不等于提升胜率，反而会在弱势期产生更多亏损**。
- 所有 6 个方案在 2026 Q1 实际开仓均为 0（大盘弱势全部被拦截/拒绝），验收线 ≤15 这条所有方案天然满足，但这只是"市场弱势下被动零开仓"，不代表风控参数正确。

**维持的基线**：**维持 `config/config.yaml` 中 `index_filter.method = ma_position`**（2026-09-18 已采纳，Task 7 Step 2 已恢复）。**不放宽任何生产风控阈值**——完全遵守 Task 7 Step 5："无方案全部满足时，保持 ma_position 已采纳基线，不放宽生产风控，并在报告中记录失败指标"。

**完整实测证据**：见 [OBPC 市场风控方案回测报告（2026-10）](../../reports/obpc-risk-policy-backtest-2026-10.md)（status: COMPLETED-WITH-CAVEAT），含 6 方案完整指标表格、拦截统计、退出原因分布、验收线逐条判定。

**对决策原始问题的回答**（Task 7 Step 6 要求的明确化）：

| 问题 | 回答 |
|------|------|
| 用哪个指数 | 沪深 300（`000300.SH`） |
| 哪个条件是硬过滤 | `index_filter.method = ma_position`（收盘价 > MA20），关闭 MACD 动量过滤和斜率过滤 |
| 哪些条件只调整评分 | `score_adjustment.enabled = true`（弱势缩小系数、极弱归零系数），不单独触发拦截 |
| `extreme_weak` 是否归零及其回测依据 | **是，归零（系数 0.0）**。方案 5 已开启评分调整，极弱市场（跌破 MA60 且 MA60 向下）下候选信号评分归零后无法进入交易池；如果调大归零系数让极弱市场信号通过，参考方案 6（完全无大盘硬过滤）的结果：胜率 16.0%、平均收益 -3.98%，**更多信号不等于更高胜率** |
| 股票池是否只含主板 | 生产配置 `main_board_only = true`，应只含主板。本次 CSV 实测中 `filter_stock_list` 后仍 2,911 只——因 `stocks_mainboard.csv` 本身就是主板清单，全量通过属预期，不是缺陷。另：本次为 `head(300)` 非随机抽样（偏向 000/001/002 段），存在选择偏差 |
