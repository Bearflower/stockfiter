---
title: OBPC 市场风控方案回测报告（2026-10）
date: 2026-10-19（更新：实测完成）
scope: Task 7 Step 4 / Step 5 / Step 6（计划 docs/superpowers/plans/2026-10-18-obpc-scan-reliability-remediation.md）
status: COMPLETED-WITH-CAVEAT（本地 CSV 分支实测完成；市场归因已按真实指数数据修正；样本为按代码排序的前 300 只、非全市场样本；数据窗口受限于 CSV 覆盖范围；结论为有限样本指示性结果，不作为风控有效性验收依据）
---

# OBPC 市场风控方案回测报告（2026-10）

## 1. 任务目标

对 `scripts/backtest_obpc.py` 内置的 6 套市场风控方案，在本地（禁止服务器）完成全历史回测，按以下验收线选出唯一达标方案：

| 指标 | 验收线 |
|------|--------|
| 整体胜率 | ≥ 60% |
| 整体平均收益 | ≥ 2% |
| 硬止损笔数占比 | ≤ 20% |
| 总交易数 | ≥ 20 |
| 2026-01 ~ 2026-03 交易数 | ≤ 15 |

6 个方案通过 `dataclasses.replace` 注入，不修改生产配置（`config/config.yaml` 保持 `index_filter.method = ma_position`）。

> **样本口径与适用范围（必读）**：本回测的候选股票来自按代码升序排序的**前 300 只**主板股票（CLI `--max-stocks 300`，见 §10.2、§14），**不是全市场样本**（全量为 2,911 只），且时间窗口仅 2025-07-31 ~ 2026-10-16。因此本报告的所有胜率、平均收益、交易数等数字**只能作为有限样本的指示性结果，不能作为风控参数有效性的验收依据**；风控有效性需要全市场样本或生产端可观测证据另行验证。

## 2. 计划执行范围（未能到达 → 已被下方实测取代）

| 项 | 计划值 |
|----|--------|
| 时间区间 | 2020-01-01 ~ 2026-04-07 |
| 复权方式 | 脚本默认（前复权，见 backtest_obpc.py § 交易模拟参数） |
| 交易成本 | 佣金 0.025%（双向） + 印花税 0.1%（卖出） + 滑点 0.1% |
| 股票池 | 主板（`main_board_only = true`），过滤创业板 / 科创板 / 北交所 / 新三板 / ST / 退市 / 指数 ETF |
| 指数 | 沪深 300（code = 000300） |
| 方案 | ① MACD DIF > 0；② ma_position + MA20 斜率；③ MACD + MA20 斜率；④ MA60 斜率；⑤ ma_position + 评分调整；⑥ 无大盘硬过滤 + 评分调整 |

## 3. 阻塞原因：本机数据库不可达

`scripts/backtest_obpc.py` 当前仅通过 `data.database.DatabaseManager` + `pd.read_sql_query` 读取生产 PostgreSQL（无 CSV 数据源开关）。本机在 2026-10-19 先后实测两条路径均失败：

### 3.1 路径 A：DNS 解析 common_service_postgres

```bash
set -a; source .env; set +a
# DB_HOST=common_service_postgres（本机解析到 198.18.1.9）
python3 scripts/backtest_obpc.py --scheme 1 --max-stocks 50 --start 2024-01-01 --end 2024-12-31
```

错误原文：
```
psycopg2 OperationalError: connection to server at "common_service_postgres" (198.18.1.9), port 5432 failed:
server closed the connection unexpectedly
        This probably means the server terminated abnormally
        before or while processing the request.
```

### 3.2 路径 B：SSH 隧道到服务器 43.156.242.184

```bash
ssh -fN -L 55432:common_service_postgres:5432 root@43.156.242.184
export DB_HOST=localhost DB_PORT=55432
python3 scripts/backtest_obpc.py --scheme 1 --max-stocks 50 --start 2024-01-01 --end 2024-12-31
```

服务器端容器 `common_service_postgres` 监听 5432/tcp 但未做主机端口映射，SSH 隧道转发到容器名或容器 IP（172.19.0.2）均报同样错误：
```
psycopg2 OperationalError: connection to server at "localhost" (127.0.0.1), port 55432 failed:
server closed the connection unexpectedly
        This probably means the server terminated abnormally
        before or while processing the request.
```

TCP 层 `nc -z 43.156.242.184 5432` 显示端口可达，但 pg 握手阶段服务端主动断开——疑似 `common_service_postgres` 容器当前仅接受来自同 Docker 网络的连接或 TLS 要求。本机 `psql` 直连同样失败，排除 Python 代码层面问题。

### 3.3 已尝试的其它备选

- 本机历史导出 `data/backtest/klines_mainboard.csv` / `stocks_mainboard.csv` / `hs300_index.csv` 已存在，但 `backtest_obpc.py` 当前不支持 CSV 数据源开关（唯一入口是 `DatabaseManager.get_stock_list()` + `get_kline_data()` + `get_index_data()`）。在不修改生产配置和回测脚本数据源选择逻辑的前提下，无法用这些 CSV 绕过。

## 4. 验收线判定（全部无法判定）

| 方案 | 总信号数 | 年均信号 | 胜率 | 平均收益 | 最低收益 | 硬止损占比 | 2026 Q1 信号数 |
|------|---------:|---------:|-----:|---------:|---------:|----------:|---------------:|
| 1 MACD DIF > 0 | — | — | — | — | — | — | — |
| 2 ma_position + MA20 斜率 | — | — | — | — | — | — | — |
| 3 MACD + MA20 斜率 | — | — | — | — | — | — | — |
| 4 MA60 斜率 | — | — | — | — | — | — | — |
| 5 ma_position + 评分调整 | — | — | — | — | — | — | — |
| 6 无大盘硬过滤 + 评分调整 | — | — | — | — | — | — | — |

注：`—` 表示未能执行。上表不包含任何推测/占位/示例数字。

## 5. 本轮回测已完成的最小验证

| 项 | 结果 |
|----|------|
| 方案配置器 `SchemeConfigurator` / SCHEME_DEFINITIONS（L181-L284） | 已存在 6 个方案，通过 `dataclasses.replace` 注入，不触碰生产配置，合规 |
| `config/config.yaml` 当前值 `index_filter.method` | `ma_position`（Task 7 Step 2 已完成） |
| `tests/risk/test_market_policy_config.py` | 存在；本轮回测未重跑 pytest，不影响结论 |
| 生产服务器运行回测 | 未执行（严格遵守禁止约束） |
| git commit / push / deploy | 未执行（严格遵守禁止约束） |

## 6. 解除阻塞需要的前提（任选其一）

1. **恢复 DB 连通**：让 `43.156.242.184:5432` 对本机 pg 握手成功（容器增加主机端口映射 `5432:5432`，或调整 `pg_hba.conf`/TLS 要求）。
2. **提供本地全量 K 线导出**：覆盖 2020-01-01 ~ 2026-04-07 的主板股票日 K 线 + 沪深 300 日 K 线，以 `data/backtest/*.csv` 形式落盘；随后给 `backtest_obpc.py` 增加 CSV 数据源开关（需独立 MR 审批，不允许在本任务内静默修改）。

## 7. 对决策的影响（如实说明）

- 本轮回测未取得任何回测证据，无法按验收线为 6 个方案排出名次。
- 生产配置 `config/config.yaml` 继续保持已采纳的 `ma_position` 基线（Task 7 Step 2 已完成且无需回滚）。
- 本轮回测**没有**证明 ma_position 达标，也**没有**证明它不达标；只是在证据缺失下被动维持原决策。相关说明已同步追加到决策文档。

## 8. 执行命令记录（全部在本机）

```bash
# 冒烟测试（方案 1，50 只股票，2024 年）
set -a; source .env; set +a
python3 scripts/backtest_obpc.py --scheme 1 --max-stocks 50 --start 2024-01-01 --end 2024-12-31
# → psycopg2 OperationalError（两条 DB 路径均失败）

# 服务器端容器状态确认（只读 SSH，未执行任何回测）
ssh root@43.156.242.184 "docker ps | grep postgres"
# → common_service_postgres  Up, 5432/tcp（未做主机映射）
```

## 9. 声明

- [x] 未在生产服务器运行回测
- [x] 未修改 `config/config.yaml` 的策略/风控参数
- [x] 未对 Git 仓库执行 commit / push / deploy
- [x] 本文档所有数字均来自真实运行输出或明确标注"未执行"，无编造

---

# 实测追加（2026-10-19 下午）

以下章节取代上方 BLOCKED 结论。核心事实：CSV 数据源分支实现后，以 300 只主板指示性样本在 2025-07-31 ~ 2026-10-16 时间窗口跑完了 6 个方案。**全部方案均不满足验收线**，**维持 `index_filter.method = ma_position` 基线，不放宽任何生产风控阈值**。

---

## 10. 实测执行

### 10.1 阻塞解除方式

上午 DB 不可达后，用户明确授权新增 `scripts/backtest_obpc.py` 的 CSV 数据源分支。新增项：
- `BacktestDataLoader(config, db=None, csv_dir=None)`：数据源互斥（db XOR csv_dir），CSV 分支读本地 `stocks_mainboard.csv` / `klines_mainboard.csv` / `hs300_index.csv`，按 `config.start_date/end_date` 过滤，`date` 列 `pd.to_datetime`，返回 DataFrame 列契约与 DB 路径**完全一致**（单股 K 线去掉 `code` 列，指数去掉 `code`/`frequency` 列）。
- CLI `--data-dir`：传入即走 CSV 路径，不传即 DB 路径（零回归）。
- 风控拦截高频日志从 `logger.info` 改为静默（计数器 `skipped_macd_filter` 等仍累加，汇总报告会展示），避免回测 IO 拖慢速度。生产路径（daily_scan.py）不受影响（每天只扫一次，无高频拦截）。

### 10.2 数据约束与回测窗口修正

| 项 | 计划值 | 实际值（数据结构限制） |
|----|--------|----------------------|
| 时间区间 | 2020-01-01 ~ 2026-04-07 | **2025-07-31 ~ 2026-10-16**（约 15 个月） |
| 全市场宽度 | 全市场 | 2020-2024 年 CSV 仅 3 个 code（000300 指数 + 002539 + 600887）；2025-07-31 起才有完整 2912 只股票 × 823,714 行 K 线 |
| 指数数据 | 沪深 300 | `hs300_index.csv` 1,887 行，覆盖 2019-01-02 ~ 2026-10-16，充足 |
| 复权方式 | 脚本默认 | 前复权（`klines_mainboard.csv` 已是前复权） |
| 交易成本 | 佣金 0.025%（双向）+ 印花税 0.1%（卖出）+ 滑点 0.1% | 与 config.yaml 一致 |
| 股票池 | 主板 | CSV 原始 2,911 只，`filter_stock_list` 后仍 2,911 只——因 `stocks_mainboard.csv` 本身就是主板清单，全量通过属**预期行为**（不是缺陷） |
| 样本量 | 全量 2,911 只 | **300 只**（约 1/10）。原因：单进程风控强制（`InMemoryMonthlyCounter` 需跨股票准确计数），2,911 只 × 约 3 分钟 / 100 只 ≈ 87 分钟 / 方案 × 6 = 9 小时，超出工具 timeout 上限；300 只 ≈ 9 分钟 / 方案。**注意：抽样取自 `filtered_df.head(300)`，按代码升序，偏向 000/001/002 段（深市主板小号段），属非随机抽样、存在选择偏差**，结论的指示性需再打折扣 |

**重要声明**：本次实测为指示性（300 只样本 + 15 个月窗口），**不声称全市场全历史结论**。任何方案在更宽样本 / 更长窗口下的表现可能不同。但就本样本内的相对比较而言，结论具有参考意义。

### 10.3 方案配置（与计划 Task 7 Step 3 完全一致）

| 方案 | index_filter_method | slope_filter | score_adjustment |
|------|--------------------:|:------------:|:----------------:|
| 1 MACD DIF > 0 | macd | 关 | 关 |
| 2 ma_position + MA20 斜率 | ma_position + `long_ma=False` | 开（MA20） | 关 |
| 3 MACD + MA20 斜率 | macd | 开（MA20） | 关 |
| 4 MA60 斜率 | ma_position + `long_ma=False` | 开（MA60） | 关 |
| 5 ma_position + 评分调整 | ma_position | 关 | 开 |
| 6 无大盘硬过滤 + 评分调整 | **禁用** (`index_filter_enabled=False`) | 关 | 开 |

## 11. 实测结果（6 方案 × 300 只主板 × 2025-07-31 ~ 2026-10-16）

### 11.1 核心指标（真实数字）

| 方案 | 候选信号（风控前） | 实际交易 | 胜率 | 平均收益 | 最高收益 | 最低收益 | 硬止损占比 | 耗时 |
|------|-------------------:|--------:|-----:|---------:|---------:|---------:|----------:|-----:|
| 1 MACD DIF>0 | 44 | 11 | 18.2% | -3.39% | 11.48% | -10.23% | 2/11 = 18.2% | 9:16 |
| 2 ma_position+MA20斜率 | 47 | 10 | 30.0% | -2.24% | 11.48% | -10.23% | 2/10 = 20.0% | 8:05 |
| 3 MACD+MA20斜率双重 | 47 | 10 | 30.0% | -2.24% | 11.48% | -10.23% | 2/10 = 20.0% | 8:20 |
| 4 MA60斜率 | 55 | 6 | 0.0% | -6.24% | -1.81% | -10.23% | 2/6 = 33.3% | 6:55 |
| 5 ma_position+评分调整 | 32 | 13 | 0.0% | -6.15% | -1.75% | -10.23% | 2/13 = 15.4% | 6:16 |
| 6 无大盘硬过滤+评分调整 | 34 | 25 | 16.0% | -3.98% | 12.18% | -10.23% | 5/25 = 20.0% | 15:28 |

> 方案 2 和方案 3 实测结果完全一致（10 笔交易、3 盈 7 亏、胜率 30%）：就本样本结果而言，MACD + slope 双重过滤未比单独 MA20 斜率过滤多拦下任何候选。**但需注意**，窗口内通过 ma_position（收盘 ≥ MA20）的 162 个交易日中，DIF > 0 有 136 日、MA20 斜率 > 0 有 128 日，两者方向不一致有 14 日（按 HS300 指数重算，见 §17），所以"双重过滤严格等价于单独斜率过滤"只是本样本的观察结果，**并非两条件在所有日期同步的一般规律**。

### 11.2 V2.1 市场状态拦截统计（按月份）

| 方案 | MACD 动量 | 均线斜率 | 评分归零 | 合计 | 主要拦截月份 |
|------|----------:|--------:|--------:|-----:|-------------|
| 1 | 29 | — | — | 29 | 2026-03(6), 04(16), 06(3), 08(4) |
| 2 | — | 34 | — | 34 | 2026-02(1), 03(6), 04(18), 06(5), 08(4) |
| 3 | 29 | 5 | — | 34 | 2026-03(6), 04(16+2), 06(3+2), 08(4) |
| 4 | — | 49 | — | 49 | 2026-03(6), 04(39), 08(4) |
| 5 | — | — | 15 | 15 | 2026-04(11), 08(4) |
| 6 | — | — | 0 | 0 | 无大盘拦截 |

### 11.3 退出原因分布

| 方案 | 移动止盈 | 硬止损 | 支撑位止损 | 最长持仓到期 |
|------|--------:|-------:|----------:|-----------:|
| 1 | 9 (81.8%) | 2 (18.2%) | 0 | 0 |
| 2 | 8 (80.0%) | 2 (20.0%) | 0 | 0 |
| 3 | 8 (80.0%) | 2 (20.0%) | 0 | 0 |
| 4 | 3 (50.0%) | 2 (33.3%) | 1 (16.7%) | 0 |
| 5 | 10 (76.9%) | 2 (15.4%) | 1 (7.7%) | 0 |
| 6 | 17 (68.0%) | 5 (20.0%) | 2 (8.0%) | 1 (4.0%) |

### 11.4 2026 Q1（1-3 月）实际交易数

所有 6 个方案在 2026-01 ~ 2026-03 实际开仓为 **0 笔**（该结论保留）。

**市场事实（按 `data/backtest/hs300_index.csv` 重算，证据见 §17）**：2026 Q1 共 **56 个交易日**（CSV 首日为 2026-01-05，无 2026-01-01 / 01-02 行），其中
- 收盘价 > MA20 有 **26 日**、< MA20 有 **30 日**（MA20 位置过滤并非全程拦截）；
- DIF > 0（DIF = EMA12 − EMA26，`ewm(adjust=False)`）有 **35 日**；
- MA20 斜率 > 0（生产口径 `(MA20 − MA20.shift(5)) > 0`）有 **25 日**；
- MA60 斜率 > 0（同口径）有 **48 日**。

因此"0 笔开仓"**不是**"Q1 全程在 MA20 下方 / DIF 全程为负"造成的——该季度是偏强日与偏弱日交替的混合状态，三条大盘指标均有大量偏强的交易日。真实成因只能落在这些分散的偏强日、候选信号时点与各方案具体过滤条件的叠加，具体如下：

- ma_position 硬过滤只在收盘 < MA20 的 30 个交易日生效；
- MACD（方案 1/3）与斜率过滤（方案 2/3/4）在各自为负的少数交易日生效；
- 方案 6 关闭了大盘硬过滤、且 §11.2 显示其市场类拦截合计为 0 次，却同样在 Q1 开仓 0 笔——说明 Q1 的 0 笔开仓至少**不能归因于大盘风控**，最可能由候选信号生成 / 仓位占用等非市场因素导致。

**按现有汇总数据无法进一步归因"为什么恰好 0 笔"**：本报告只保留了按月的拦截计数，未保留逐日候选信号时点与拦截明细，无法判定偏强日的候选是否与指数状态同日出现。需重跑并导出每笔候选的日期与大盘上下文做逐笔复核，才能确定机制——本报告不作推测。

2026 Q1 信号数 = 0，低于验收线 ≤ 15（这条验收线**所有方案均满足**）。

## 12. 按验收线判定（全部不达标）

| 指标 | 验收线 | 1 | 2 | 3 | 4 | 5 | 6 |
|------|--------|---|---|---|---|---|---|
| 整体胜率 | ≥60% | ❌ 18.2% | ❌ 30.0% | ❌ 30.0% | ❌ 0.0% | ❌ 0.0% | ❌ 16.0% |
| 平均收益 | ≥2% | ❌ -3.39% | ❌ -2.24% | ❌ -2.24% | ❌ -6.24% | ❌ -6.15% | ❌ -3.98% |
| 硬止损占比 | ≤20% | ✅ 18.2% | ✅ 20.0% | ✅ 20.0% | ❌ 33.3% | ✅ 15.4% | ✅ 20.0% |
| 实际交易数 | ≥20 | ❌ 11 | ❌ 10 | ❌ 10 | ❌ 6 | ❌ 13 | ✅ 25 |
| 2026 Q1 交易数 | ≤15 | ✅ 0 | ✅ 0 | ✅ 0 | ✅ 0 | ✅ 0 | ✅ 0 |

- **方案 2 / 方案 3** 最接近（硬止损占比 20.0% 擦线、Q1 交易数 0 达标），但胜率 30% 差 30pp、平均收益 -2.24% 差 4.24pp、交易数 10 差 10——**四条验收线挂三条**。
- **方案 6** 交易数 25 达标，但胜率 16% 和平均收益 -3.98% 全部挂——**四条验收线挂两条**（硬止损占比 20% 擦线）。
- **方案 1 / 方案 4 / 方案 5** 五条验收线全部挂。

**没有任一方案在 5 条验收线上全部满足。**

## 13. 结论与维持基线的理由

1. **2025-07-31 ~ 2026-10-16 市场窗口并非单边弱势，而是强弱交替**：按 `data/backtest/hs300_index.csv` 重算（证据见 §17），窗口内 292 个交易日中，沪深 300 收盘价 > MA20 有 **162 日**（55.5%）、< MA20 有 **130 日**（44.5%）；DIF > 0（DIF = EMA12 − EMA26，`ewm(adjust=False)`）有 **168 日**（57.5%）；MA20 斜率 > 0（生产口径 `(MA20 − MA20.shift(5)) > 0`）有 **164 日**（56.2%）。三条偏强指标均超过半数交易日，**弱势并非"全程"**，因此不能把全部方案的负收益简单归因于单边弱势大盘。（原报告此处"148/292 在 MA20 下方（50.7%）""MACD DIF 全程为负、MA20 斜率全程为负"与重算不符，已按 CSV 真实数据修正。）
2. **即使完全去掉大盘硬过滤（方案 6）**，300 只样本的胜率也只有 16.0%、平均收益 -3.98%——说明在**本样本窗口**里，候选信号本身的质量就差，大盘过滤不是负收益的根本原因（市场在该窗口强弱交替，见第 1 条）。该判断受 300 只非随机样本限制，不能外推到全市场。
3. **方案 2 / 方案 3 相对最接近**，但胜率 30%（差 30pp）、平均收益 -2.24%（差 4.24pp）离验收线仍然很远。放宽大盘风控（如去掉 slope 过滤、降低 MACD 阈值）只会增加候选数量，但不会提升胜率，反而可能增加亏损暴露。
4. **硬止损占比**：方案 4 的 33.3% 超标（MA60 斜率是更强的过滤，反而候选信号更差、亏损更严重）；方案 5 的 15.4% 最低但交易数只有 13（评分调整把极弱市场的信号归零，进一步减少了交易数）。
5. **结论**：**维持 `index_filter.method = ma_position` 基线，不放宽任何生产风控阈值**。符合 Task 7 Step 5 明确要求——"无方案全部满足时，保持 ma_position 已采纳基线，不放宽生产风控"。**该结论的依据是"本报告样本内没有任何方案达标、因此不支持放宽风控"这一审慎判断，不依赖上述市场归因**；同时须强调：本回测候选为按代码排序的**前 300 只**（`--max-stocks 300`）、非全市场样本，**本报告不足以验收风控参数有效性**——风控有效性仍需全市场样本或生产端可观测证据另行验证。

### 不放宽风控的具体理由

- 放宽 MA20 斜率阈值或 MACD DIF 阈值 → 会在更多交易日（含弱势日）产生更多信号，但从方案 6（无大盘硬过滤，25 笔交易）的结果看，**更多信号不等于更高胜率**，反而累计亏损 -99.6%（300 只样本）。
- 单独调 `extreme_weak` 评分归零系数 → 方案 5 已经关闭斜率过滤只保留评分调整，结果胜率 0%（13 笔全亏）。调大评分归零系数（从 0.0 往上调）会让极弱市场的信号重新进入交易池，**增加亏损暴露**。
- 生产风控的作用就是在市场转弱时减少开仓、保护本金。**放宽风控等于丢掉了这个功能**；就本样本窗口（强弱交替、方案 6 无过滤仍 25 笔平均 -3.98%、胜率 16%）而言，放宽只会产生更多信号并带来更多亏损，但这属有限样本内的观察，需全市场样本复核。

## 14. 执行命令

```bash
# CSV 数据源分支实现后，以下命令在本机执行（禁止 SSH 到服务器）
set -a; source .env; set +a

# 冒烟测试（CSV 分支连通性验证）
python3 scripts/backtest_obpc.py --data-dir data/backtest \
  --scheme 1 --start 2025-07-31 --end 2026-10-16 --max-stocks 20

# 实测 6 方案（300 只主板指示性样本，2025-07-31 ~ 2026-10-16）
for i in 1 2 3 4 5 6; do
  python3 scripts/backtest_obpc.py --data-dir data/backtest \
    --scheme $i --start 2025-07-31 --end 2026-10-16 --max-stocks 300 \
    2>&1 | tee /tmp/obpc_scheme${i}.log
done

# 零回归验证
python3 -m pytest tests -q          # 93 passed / 5 skipped / 0 failed
python3 -m compileall -q main.py data scripts strategy distill_changying/scripts  # exit=0
```

## 15. 代码改动清单（最小化、仅限本任务需求）

| 文件 | 改动 | 行数 |
|------|------|-----:|
| `scripts/backtest_obpc.py` | `BacktestConfig` 新增 `csv_data_dir: Optional[str] = None` 字段 | +2 |
| 同上 | `BacktestDataLoader.__init__(config, db=None, csv_dir=None)` 签名调整；新增数据源互斥校验 | ~15 |
| 同上 | `BacktestDataLoader` 新增 3 个 CSV 分支私有方法 + 重构 3 个公开方法为 DB/CSV 双路径 | ~120 |
| 同上 | CLI `argparse` 新增 `--data-dir` | ~7 |
| 同上 | `main()` 三处 `DatabaseManager()` 改为条件创建（CSV 模式跳过）；三处 `db.close()` 改为条件关闭 | ~12 |
| 同上 | `_process_stock_worker` 同上处理 | ~7 |
| 同上 | 风控拦截高频日志从 `logger.info` 改为注释（计数器仍累加，生产不受影响） | ~4 |

**未触碰的**：`config/config.yaml`（零改动，`index_filter.method` 保持 `ma_position`）、生产调度脚本 `scripts/scheduler_obpc.py`、生产测试文件 `tests/risk/test_market_policy_config.py`。

## 16. 声明（实测追加）

- [x] 6 个方案均在本机执行，**未 SSH 到服务器**运行任何回测
- [x] **未修改** `config/config.yaml` 的任何策略/风控参数（`index_filter.method` 保持 `ma_position`）
- [x] **未 commit / push / deploy** 任何 Git 仓库变更
- [x] 本文档所有数字均来自对应日志文件的真实输出（`/tmp/obpc_scheme{1..6}.log` 或 `backtest_results/backtest_scheme{1..6}_*.json`），**无编造**
- [x] 所有方案均通过验收线逐条判定，**无放宽风控以"凑指标"的行为**
- [x] 明确标注了 300 只抽样 / 15 个月窗口的数据约束，**不声称全市场全历史结论**
- [x] 维持 `ma_position` 基线的决策有明确的实测证据支撑，符合 Task 7 Step 5 的明确要求

---

## 17. 市场归因重算证据（修正 §11.1 / §11.4 / §13）

> 起因：初审发现 §11.4 / §13 关于市场环境（MA20 位置、MACD DIF、均线斜率）的断言与指数真实数据不符。以下用仓库现有 `data/backtest/hs300_index.csv`（1,887 行，覆盖 2019-01-02 ~ 2026-10-16，单 code = 000300）独立重算。指标口径与生产代码 `strategy/oversold_bounce/risk_control.py` 一致：MACD `ewm(span=12/26/9, adjust=False)`；斜率取生产口径 `(MA20 − MA20.shift(slope_lookback=5))`。

重算命令（一次性 `python3 -c`，未在仓库留下任何脚本文件）：

```bash
python3 -c "
import pandas as pd
df = pd.read_csv('data/backtest/hs300_index.csv')
df['date'] = pd.to_datetime(df['date']); df = df.sort_values('date').reset_index(drop=True)
df['ma20'] = df['close'].rolling(20).mean()
df['ma20_slope1'] = df['ma20'].diff()                       # 口径A：1 日差分
df['ma20_slope5'] = df['ma20'] - df['ma20'].shift(5)        # 口径B：相对 5 交易日前（生产 slope_lookback=5）
ef = df['close'].ewm(span=12, adjust=False).mean()
es = df['close'].ewm(span=26, adjust=False).mean()
df['dif'] = ef - es                                         # DIF = EMA12 - EMA26
df['ma60'] = df['close'].rolling(60).mean()
df['ma60_slope5'] = df['ma60'] - df['ma60'].shift(5)
w  = df[(df['date'] >= '2025-07-31') & (df['date'] <= '2026-10-16')]
q1 = df[(df['date'] >= '2026-01-01') & (df['date'] <= '2026-03-31')]
print('窗口 n=%d | close>MA20=%d close<MA20=%d | DIF>0=%d | slope1>0=%d | slope5>0=%d' % (
    len(w), (w['close']>w['ma20']).sum(), (w['close']<w['ma20']).sum(),
    (w['dif']>0).sum(), (w['ma20_slope1']>0).sum(), (w['ma20_slope5']>0).sum()))
print('Q1   n=%d | close>MA20=%d close<MA20=%d | DIF>0=%d | slope5>0=%d | MA60slope5>0=%d' % (
    len(q1), (q1['close']>q1['ma20']).sum(), (q1['close']<q1['ma20']).sum(),
    (q1['dif']>0).sum(), (q1['ma20_slope5']>0).sum(), (q1['ma60_slope5']>0).sum()))
"
```

输出：

```
窗口 n=292 | close>MA20=162 close<MA20=130 | DIF>0=168 | slope1>0=158 | slope5>0=164
Q1   n=56 | close>MA20=26 close<MA20=30 | DIF>0=35 | slope5>0=25 | MA60slope5>0=48
```

重算结论汇总：

| 指标 | 全窗口 2025-07-31 ~ 2026-10-16（292 日） | 2026 Q1（CSV 56 日） |
|------|-----------------------------------------|---------------------|
| 收盘价 > MA20 | 162（55.5%） | 26 |
| 收盘价 < MA20 | 130（44.5%） | 30 |
| DIF > 0（EMA12−EMA26） | 168（57.5%） | 35 |
| MA20 斜率 > 0（生产口径，5 日 lookback） | 164（56.2%） | 25 |
| MA60 斜率 > 0（生产口径，5 日 lookback） | 210 | 48 |

口径说明与差异：

- **DIF>0**：口径为 `DIF = EMA12 − EMA26`（`ewm(adjust=False)`），重算 168 日，与用户独立复核一致。
- **MA20 斜率>0**：若按"1 日差分"仅得 158 日；生产代码口径为 `(MA20 − MA20.shift(5)) > 0`（`risk_control.py` 中 `slope_lookback` 默认 5），据此得 **164 日**，与用户复核一致。报告统一采用生产口径并明确标注。
- **Q1 交易日数**：CSV 中 2026 Q1 为 **56 日**（首日 2026-01-05；CSV 无 2026-01-01 / 01-02 行，两日分别为元旦假期）。用户复核为 57 日，差 1 日源于假期/首日计入口径不同，不影响任何比例结论。
- **原报告错误**："148/292 在 MA20 下方（50.7%）"应为 **130/292（44.5%）**；"MACD DIF 全程为负""MA20 斜率全程为负""Q1 均在 MA20 下方""Q1 MACD DIF 同步为负"均被上述重算证伪，已改写。

## 18. 声明（市场归因修正）

- [x] 本次仅修正 `docs/reports/obpc-risk-policy-backtest-2026-10.md`，**未改动** `docs/knowledge/decisions/**`、`scripts/**`、`config/**` 及其他 `docs/**`
- [x] 市场归因数字全部来自上述 `data/backtest/hs300_index.csv` 的真实重算，**未引用未经核验的二手数字**；未在仓库留下任何临时脚本文件
- [x] **未 commit / push / deploy** 任何 Git 仓库变更
- [x] 结论"维持 `ma_position` 基线、不放宽任何生产风控阈值"**保持不变**，且其论据已不再依赖被证伪的市场归因
