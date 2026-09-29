# stockfilter_v3 全项目代码审查 23 项缺陷修复 — 技术设计文档

| 项目 | 内容 |
|------|------|
| 文档版本 | V1.0 |
| 编写人 | 后端架构师 |
| 需求依据 | [code-review-fix-2026-09-29 PRD](../requirements/code-review-fix-2026-09-29.md) |
| 缺陷清单 | [review.json](../reports/code-review-2026-09-29/review.json)（R01~R23） |
| 审查基线 | `main` / `d94b75e024bb778386beb0df055e3c8743b84baa` |
| 本文档边界 | 仅设计"怎么修"，不含修复代码；实现与上线按开发流程另行执行 |

---

## 1. 设计目标与原则

### 1.1 设计目标

围绕 PRD 第 1.2 节四个目标，本设计的工程目标是：

1. **安全目标**：飞书 webhook 明文明文彻底移除，源码与镜像零凭据；凭证改为运行时注入 + 轮换。
2. **正确性目标**：信号额度（月/日）、扫描日期语义、指数风控新鲜度、投顾估值字段、回测成交/净值模型恢复业务语义。
3. **可靠性目标**：K 线更新管线"如实上报成功/失败"（R13 是 R06 的前置），数据不完整时下游不放行；临时故障可重试、可降级、不静默丢失。
4. **可维护目标**：**所有阈值/评分/调度/风控参数来自 `config.yaml` 或算法计算，零新增硬编码**（项目级强制约束）。

### 1.2 设计原则

| 原则 | 说明 | 落地方式 |
|------|------|---------|
| **P1 复用现有抽象，不新增框架** | 项目已有 `MonthlySignalCounter`（内存/数据库双实现）、`MarketContextProvider`、`RiskController` 三层风控等抽象，缺陷修复应补全这些抽象而非另起炉灶 | R02/R21 复用 `MonthlySignalCounter`；R06 复用 `MarketContextProvider.get_context` |
| **P2 实盘与回测同构** | R02（实盘月限）与 R21（回测月限）是同一业务规则的双路径，必须同批、同算法，否则回测无法验证实盘 | 抽取统一的「按剩余额度截取排序后候选」纯函数，两者共用 |
| **P3 成交/净值单一事实源** | R07/R19/R20 暴露了成交价、净值、回撤三处口径不一致；修复后必须以「实际成交价 + 现金 + 每日持仓市值」生成唯一净值曲线，所有绩效指标从同一曲线导出 | `backtest_swing.py` 引入统一 `equity_curve` |
| **P4 配置即契约** | 每次新增参数必须落到 `config.yaml` 并声明键位，评审时逐键核对，防止硬编码回潮 | 第 3 节逐项列出「配置变更」 |
| **P5 先管线上报、后风控校验** | 数据完整性信号必须先真实（R13），下游新鲜度校验才能生效（R06） | 实施顺序 R13 → R06 |
| **P6 回测修复前绩效不作为证据** | 回测缺陷（R07/R08/R19/R20/R21）修复前，既有收益/胜率/回撤一律作废，仅以「正确行为断言 + 前缀不变性」为验收 | 见第 6 章 |

### 1.3 与既有五层架构的对齐

```
通知层（feishu_push.py / notification/ / daily_report.py）
   ↑
策略层（strategy/oversold_bounce/ + distill_changying/scripts/advisor/）
   ↑
引擎层（engine/ + backtest_*.py + daily_scan.py）
   ↑
数据层（data/database.py + scripts/data/*.py）
   ↑
独立 E大估值（distill_changying/）
```

本设计所有修复均落在既有层内，不跨层新增组件。R10/R04/R05 属部署配置层（`docker-compose.yml`/`.dockerignore`/`monitor.sh`），按项目部署规则处理。

---

## 2. 影响面总览

### 2.1 分层与依赖

依据 PRD 第 3 章重新分层 + 第 3.4 节依赖顺序，本设计定稿如下：

```
P0（立即）：R10（独立，最先）
P1（本周）：R13 → R06         （先如实上报，再加新鲜度校验）
           R02 ↔ R21         （月度额度实盘/回测同批）
           R07 ↔ R19 → R20   （成交/净值模型统一）
           R01 / R03 / R04 / R05 / R08 / R09（各自独立，可并行）
P2（排期）：R11 / R12 / R14 / R15 / R16 / R17 / R18 / R22 / R23
           （独立排期；R21 已随 P1）
```

### 2.2 涉及文件清单（按缺陷）

| 文件 | 涉及缺陷 |
|------|---------|
| `scripts/monitor.sh` | R10 |
| `config/config.yaml` | R10 / R06 / R12 / R13 / R17 / R18 / R08 / D-3 |
| `scripts/daily_scan.py` | R02 / R03 |
| `scripts/scheduler_obpc.py` | R03 |
| `scripts/feishu_push.py` | R03 / R05（若选数据库方案） |
| `distill_changying/scripts/advisor/market_data.py` | R01 |
| `distill_changying/scripts/advisor/temperature.py` | R01 |
| `distill_changying/scripts/advisor/etf_recommend.py` | R01 |
| `distill_changying/scripts/advisor/daily_report.py` | R01 |
| `distill_changying/scripts/advisor/config.yaml` | R01 / R17 / R18 |
| `distill_changying/scripts/advisor/advisor_llm.py` | R17 / R18 |
| `distill_changying/scripts/shared/llm_utils.py` | R14 / D-5 |
| `distill_changying/scripts/distill/merge_positions.py` | R09 |
| `distill_changying/scripts/distill/analyzer.py` | R15 |
| `distill_changying/scripts/distill/batch_processor.py` | R15 / R16 |
| `distill_changying/scripts/distill/state_manager.py` | R16 |
| `scripts/backtest_obpc.py` | R07 / R21 |
| `scripts/backtest_swing.py` | R07 / R19 / R20 |
| `scripts/backtest_cross_section.py` | R08 |
| `scripts/data/update_kline_daily.py` | R11 / R12 / R13 / R23 |
| `scripts/scheduler_kline.py` | R13 / R06（调度接入） |
| `scripts/data/index_update.py` | R06 |
| `strategy/oversold_bounce/risk_control.py` | R06 |
| `data/database.py` | R02 / R22 |
| `main.py` | R23 |
| `docker-compose.yml` | R04 / R05 |
| `.dockerignore` / `Dockerfile.eadvisor` | R04 |

### 2.3 新增/调整配置键位总览

| 配置节 | 键位 | 涉及缺陷 | 状态 |
|--------|------|---------|------|
| `global.notification.feishu_webhook` | 删除明文，改环境变量 | R10 | 删除 |
| `global.kline_update.*`（新增节） | `failure_rate_threshold` / `failure_window_size` / `failure_window_min_samples` / `total_failure_limit` / `min_success_rate` / `request_timeout_seconds` | R12 / R13 | 新增 |
| `global.backfill.*` | 保留（供 quick_backfill 使用） | R13（对照） | 不变 |
| `strategies.oversold_bounce.params.*` | `slippage` 复用于 R07；`max_signals_per_month` / `max_daily_signals` 复用 | R07 / R02 / R21 | 复用 |
| `strategies.oversold_bounce.params.index_filter.*` | 新增 `freshness_strategy` / `freshness_stale_days` | R06 / D-3 | 新增 |
| `strategies.distill_changying.params.*` | 新增 `position_bounds`（stock/bond/cash 上下界） | R17 / R18 | 新增 |
| `distill_changying/scripts/advisor/config.yaml` | `llm_analysis.position_bounds` + `etf shares` 校验开关 | R17 / R18 | 新增 |
| 截面回测配置 | 新增 `section_backtest.*`（训练期/统计窗口） | R08 | 新增 |

> 说明：`global.backfill.failure_rate_threshold` 等属于「补全脚本熔断」语义，与「日更新完整性」语义不同，故 R13 新增独立 `global.kline_update` 节，避免复用造成语义混淆（见 4.1 关键决策）。

---

## 3. 逐项修复设计

> 每项按：现状根因 → 修改方案 → 配置变更 → 数据/DB 变更 → 验证方式。

---

### R10 · 飞书机器人凭据明文入库（P0）

**现状根因**：`scripts/monitor.sh:16` 硬编码完整 webhook URL；`config/config.yaml:35` 的 `global.notification.feishu_webhook` 明文。两者都会进入 Git 历史与镜像层。任何拿到源码/镜像者即可向该群发消息。

**修改方案**：
1. `monitor.sh` 第 16 行改为从环境变量读取：
   ```bash
   WEBHOOK="${FEISHU_WEBHOOK_MONITOR:-}"
   if [ -z "$WEBHOOK" ]; then
     log "❌ 未配置 FEISHU_WEBHOOK_MONITOR 环境变量，监控告警将失效"
   fi
   ```
2. `config.yaml` 删除 `global.notification.feishu_webhook` 明文值，改为占位符 `${FEISHU_WEBHOOK:-}`（或整行删除，由消费方 `os.getenv` 读取）。`scripts/feishu_push.py` 已读 `os.getenv('FEISHU_WEBHOOK')`，`docker-compose.yml:47` 已注入 `FEISHU_WEBHOOK=${FEISHU_WEBHOOK}`，链路已具备，仅需去除明文。
3. `.env.example` 增加 `FEISHU_WEBHOOK` / `FEISHU_WEBHOOK_EADVISOR` / `FEISHU_WEBHOOK_MONITOR` 三项空占位。
4. **凭证轮换**：用户在飞书后台对 OBPC 群机器人「重置签名密钥」；新 webhook 注入 GitHub Secrets 或服务器 `.env`（按 [deployment.md](../../../.trae/rules/deployment.md)）。
5. 历史 git 清理：见 D-1，属安全动作，需用户拍板后执行。

**配置变更**：删除 `config.yaml` 明文 webhook；`.env.example` 增加 3 项环境变量占位。

**数据/DB 变更**：无。

**验证方式**：
- `grep -r "open.feishu.cn/open-apis/bot" scripts/ config/ docker-compose.yml` 结果为空（除 `.env.example` 注释外）。
- 模拟发送：设 `FEISHU_WEBHOOK_MONITOR=新webhook` 运行 `monitor.sh` 启动告警可达。
- 部署文档 `.trae/rules/deployment.md` 的 Secret 清单增加 `FEISHU_WEBHOOK_MONITOR`。

---

### R13 · 数据更新部分/全部失败仍上报成功（P1，R06 前置）

**现状根因**：
- `update_kline_daily.py:74-300` `update_all_klines()` 返回 `None`，只有日志没有退出码语义；失败阈值触发后 `break` 提前结束但无人感知「数据不完整」。
- `update_kline_daily.py:351-355` `main()` 末尾 `os._exit(0)` 无条件退出 0。
- `scheduler_kline.py:66-81` `subprocess.run(check=True)` 只依赖退出码；退出 0 即写 `task_status=completed`，扫描放行。

**修改方案**：
1. 重构 `update_all_klines()` 返回 `UpdateResult` 数据类（字段：`success` / `failed` / `skipped` / `total` / `latest_trade_date`）。
2. 将滑动窗口失败率阈值 `MAX_FAILURE_RATE`（第 114 行）与总失败上限 `TOTAL_TIMEOUT_LIMIT`（第 115 行）从函数内硬编码移入 `config.yaml` 的 `global.kline_update` 节；`min_samples` 判定（第 220 行 `len(recent_results) >= 50`）同步提取为 `failure_window_min_samples`。
3. `main()` 移除 `os._exit(0)`，改为：
   ```python
   result = updater.update_all_klines()
   is_complete = result.failed <= config 允许阈值 and result.success_rate >= config["min_success_rate"]
   sys.exit(0 if is_complete else 1)
   ```
   完整性条件：`success_rate = success / (success + failed) >= min_success_rate` 且 `failed <= total_failure_limit`（具体阈值见配置变更）。移除 `os._exit` 同时顺带解除 R23 的进程中断根因。
4. `scheduler_kline.py` 改为捕获非 0 退出码：失败时不写 `completed`，改写 `status='failed'`（task_status 表已有 `status` 列），并让 `scheduler_obpc.wait_for_kline_update` 可识别非 completed（详见 R06）。

**配置变更**（`config/config.yaml` 新增 `global.kline_update`）：
```yaml
global:
  kline_update:
    request_timeout_seconds: 15      # 单股抓取超时（R12）
    failure_rate_threshold: 0.7      # 滑动窗口失败率熔断阈值
    failure_window_size: 100         # 滑动窗口大小
    failure_window_min_samples: 50   # 熔断判定最小样本数
    total_failure_limit: 500         # 累计失败硬上限
    min_success_rate: 0.9            # 完整性：成功率红线（成功/(成功+失败)）
```

**数据/DB 变更**：`task_status.status` 由仅写 `completed` 扩展为可写 `completed` / `failed`。

**验证方式**：
- 隔离场景（R13 对应 report 证据「代码路径确认」）：mock `BaostockSession.get_kline` 让半数失败，断言 `main()` 退出码为 1；`scheduler_kline` 不写 `completed`。
- 全成功场景：退出码 0，写 `completed`。
- 失败场景下 `scheduler_obpc` 扫描不启动。

---

### R06 · 指数数据未校验新鲜度即参与风控（P1，依赖 R13）

**现状根因**：
- `risk_control.py:510-515` `get_context(date_str)` 直接用 `index_df["close"].iloc[-1]`，未比较最后一条 K 线日期与 `date_str`；即使指数更新失败（连续多日旧数据），旧 MA/MACD 仍被当作当天风控。
- `index_update.py` 是独立脚本，未接入默认调度链（`scheduler_kline.py` 只跑股票更新 + 补全）。

**修改方案**：
1. **调度接入**：`scheduler_kline.py` 在 22:00 股票更新前先调用 `scripts/data/index_update.py`（指数优先于个股，符合 `index_update.py` 头部注释的调度意图）。指数更新失败应记录告警（先走 R13 的如实上报逻辑，或独立小脚本）。
2. **新鲜度校验**：`RealtimeMarketContextProvider.get_context(date_str)` 在取 `iloc[-1]` 前，校验 `index_df["date"].iloc[-1]` 是否等于目标交易日：
   - 相等 → 正常计算返回 `MarketContext`；
   - 不相等（缺失/过期）→ 按 `config` 的 `freshness_strategy`（D-3 待决策）执行：`fail`(显式失败) / `stale_days`(允许最近 N 日) / `skip`(该日不参与开仓)。
3. `get_index_kline`（`database.py:502`）已有按 `days` 取最近 N 条的能力，无需改 SQL；仅在 provider 层做日期比对。

**配置变更**（`strategies.oversold_bounce.params.index_filter` 新增）：
```yaml
index_filter:
  freshness_strategy: "fail"      # fail | stale_days | skip（D-3 用户拍板后定）
  freshness_stale_days: 3         # strategy=stale_days 时允许的最近 N 交易日
```

**数据/DB 变更**：无（复用 `klines` 表指数数据）。

**验证方式**：
- 隔离场景（R06 证据「静态调用链确认」）：指数最后 K 线日期 < 本次交易日时，`get_context` 不返回「当天风控上下文」，按策略报错/降级。
- 指数最新日期 == 交易日时，正常返回。
- 拉取默认调度链（`scheduler_kline` + `scheduler_obpc`）确认指数更新在股票更新前执行。

---

### R02 · 月度上限未计入本批待保存信号（P1，与 R21 同批）

**现状根因**：`daily_scan.py:264-271` 每扫描一个候选都读同一 `DatabaseMonthlyCounter.get_count(year_month)`（读库），直到扫描结束才 `save_scan_result` 一次性入库；`DatabaseMonthlyCounter.increment` 是 no-op。因此本批 N 个候选全部按「月初计数」判上限，N 条全部放行，突破 `max_signals_per_month`。

**修改方案**：
1. 将「月度额度截取」从「逐候选风控时判定」移到「扫描收集完、排序后、保存前」，用纯函数统一实现（与 R21 同构）：
   ```python
   def cap_by_monthly_quota(candidates, used_count, max_per_month, max_daily):
       """排序后按剩余额度截取；返回 (入选, 截断说明)。
       candidates 已按评分降序。"""
       remaining = max(0, max_per_month - used_count)
       selected = candidates[: max_daily if max_daily > 0 else len(candidates)]
       selected = selected[:remaining]  # 日限与月限取更严格者
       return selected
   ```
2. `daily_scan.py` 流程调整：
   - 逐候选风控仅保留「评分阈值 + 大盘趋势」，**移除单月上限逐候选判定**（或保留但改为不依据它截断，仅统计）；
   - 排序后：`selected = cap_by_monthly_quota(signals, monthly_count, max_signals_per_month, max_daily_signals)`；
   - 保存 `selected` 到 DB 与 JSON。
3. 并发安全：`save_scan_result` 前，在 `DatabaseManager` 增加原子扣减方法（可选，见决策 4.2）。生产是单进程调度，串行安全；若需防线，用 PostgreSQL 事务 + `SELECT ... FOR UPDATE` 包裹「读月计数 + 插入」。

**配置变更**：无新增（复用 `max_signals_per_month` / `max_daily_signals`）。

**数据/DB 变更**：可选 — `DatabaseManager` 新增 `reserve_monthly_slots(year_month, n)` 原子方法（`BEGIN; SELECT count FOR UPDATE; 校验; INSERT`）。

**验证方式**：
- 隔离场景 `monthly_limit`：已用 29、月限 30、日限 5 时，本批 5 个候选只保存 1 条，最终月计数 == 30。
- 分支测试：剩余额度 < 候选数（截断）、剩余额度 >= 候选数（全保存）。
- 并发 mock（多线程同时保存）超额不突破。

---

### R21 · 月度名额按股票顺序而非信号日期分配（P2，随 R02 同批）

**现状根因**：`backtest_obpc.py:856-869` `BacktestEngine.run()` 逐只股票遍历，全局共用 `self.monthly_counter`；`_backtest_single_stock` 在风控通过后 `self.monthly_counter.increment(year_month)`（第 1020 行）。先遍历的股票月末信号占满名额，后遍历的股票月初信号被过滤，结果依赖股票列表排序。

**修改方案**：
1. 将「信号产出」与「月度额度分配」解耦：
   - `_backtest_single_stock` 改为只产出**候选信号列表**（含 `signal_date` 与评分，已过评分/大盘风控，但**不判定 monthly_limit、不 increment**）。
   - `run()` 收集全部候选信号后，按 `(signal_date, -score)` 排序，然后用共享 `InMemoryMonthlyCounter` 逐个应用日限 + 月限（复用 R02 的 `cap_by_monthly_quota` 纯函数，或等价的逐日窗口分配），分配额度后才进入 `TradeSimulator.simulate`。
2. 全局月度配额分配：
   ```python
   candidates.sort(key=lambda s: (s.signal_date, -s.score))
   selected = cap_by_monthly_quota(candidates, 0, max_per_month, max_daily_signals)
   ```
   `InMemoryMonthlyCounter.increment` 对入选信号按月份累加，日限按 `signal_date` 分组 Top N。

**配置变更**：无新增（复用 `max_signals_per_month` / `max_daily_signals`）。

**数据/DB 变更**：无。

**验证方式**：
- 隔离场景（R21 证据「代码路径确认」）：构造两只股票 A（月末信号）B（月初信号），断言月限分配不再与股票列表顺序相关；`max_signals_per_month` / `max_daily_signals` 生效口径与 R02 实盘版一致。
- 前缀不变性：交换股票遍历顺序，信号集合不变。

---

### R07 · 止损按不可成交阈值记账（P1，联动 R19/R20）

**现状根因**：`backtest_obpc.py:670-681` 三个止损分支直接以止损线成交（`exit_price = hard_stop_price / stop_loss_price / trailing_stop_price`），未处理「开盘已跳空跌破止损线」——合成数据入场 100、次日开盘 80、最高 85，仍按 90 成交，成交价超过全天最高价。

**修改方案**（`TradeSimulator.simulate` 退出条件重排）：
1. 每个退出日先做「开盘跳空判定」：若 `open < stop_price`（对 hard_stop / support_stop / trailing_stop 分别计算各自止损线），则 `exit_price = open`（不再按止损线）。
2. 无跳空时沿用原逻辑按止损线成交并计入滑点（复用 `config.slippage`，从 `strategies.oversold_bounce.params.slippage` 读入 `BacktestConfig`）。
3. 判定顺序修正后通用假设：**成交价永不高于当日 high、永不低于当日 low（跳空例外允许 open < 前日止损线，此时取 open）**。此不变式写入单元测试。

**配置变更**：`BacktestConfig.slippage` 默认值已含 0.001，但需确认 `backtest_obpc.py` 顶部 `BacktestConfig` 的 `slippage` 字段已从 `config.yaml` 的 `params.slippage` 注入（若无则补齐注入逻辑，禁止数据类裸默认值）。

**数据/DB 变更**：无。

**验证方式**：
- 隔离场景 `gap_stop`：入场 100、次日开盘 80、最高 85 → 成交价 == 80（非 90），且不高于全天最高。
- 无跳空：仍按止损价（含滑点）成交。
- 边界：开盘 == 止损线时路径正确。

---

### R19 · 盘中止损覆盖开盘卖出（P2，联动 R07/R20）

**现状根因**：`backtest_swing.py:178-189` 先检查 `low <= sl_price`（盘中），再检查 `sell_sig.iloc[i-1]`（开盘）。有 T-1 卖出信号时本应先开盘离场，却先被盘中止损接管。入场 100、次日开盘 105、盘中低 80 → 以 92 止损替代本应的 105 开盘。

**修改方案**（`run_backtest` 卖出段重排）：
1. 优先级顺序改为：**① 隔夜信号开盘离场（若当日有 T-1 卖出信号）→ ② 盘中 hard_stop/support_stop/trailing 检查（按 R07 补开盘跳空）→ ③ 止盈**。
2. 具体：在 `pos is not None` 分支，先判 `if sell_sig.iloc[i - 1]: exit_price = today_open`，否则才进入盘中 low/high 检查。

**配置变更**：无新增（`STOP_PARAMS` 等模块级硬编码见「关键决策 4.4」，本次一并整改）。

**数据/DB 变更**：无。

**验证方式**：
- 隔离场景 `swing_exit_order`：入场 100、次日开盘 105、盘中低 80 → 105 开盘离场（非 92）。

---

### R20 · 波段最大回撤与成交净值不一致（P2，依赖 R07/R19）

**现状根因**：`backtest_swing.py:254-263` `compute_metrics` 中净值曲线对任何 `held=True` 整天用 `close[i]/close[i-1]`，未区分成交价，也未扣交易成本；而 `total_ret` 用 `trades` 的成交价累计。两者不是同一资金曲线。

**修改方案**：
1. `run_backtest` 额外返回**统一净值曲线** `equity`（list[float]，长度 n）：
   - 现金 + 持仓市值逐日计算；买入/卖出按实际成交价（含成本）调仓；未持仓日市值不变。
   - 买入前隔夜跳空、开盘卖出后当日涨跌、交易成本（`commission`/`stamp_tax`/`slippage`）全部在该曲线上自然体现。
2. `compute_metrics` 改为：`nav = pd.Series(equity)/equity[0]`，`max_dd`、`total_ret`、`annual_ret` 全部从 `nav` 导出，删除 `held` close-to-close 回撤逻辑。
3. `total_ret` 与 `max_dd` 由此共享同一资金曲线。

**配置变更**：无新增（成本参数复用 `COST`，但需 headers 一致；见 4.4 硬编码整改）。

**数据/DB 变更**：无。

**验证方式**：
- 隔离场景（R20 证据「代码路径确认」）：`max_dd` 与 `total_ret` 基于同一净值曲线；构造含隔夜跳空 + 开盘卖出的用例，断言买入前跳空与卖出后涨跌、成本均反映在回撤中。

---

### R01 · 价格分位被当作 PE/PB 估值分位（P1，定性「业务语义缺陷」）

**现状根因**：`market_data.py:520-523` 成分股方案把 `_get_index_close_and_percentile` 返回的收盘价分位同时赋给 `pe_percentile` 与 `pb_percentile`，而回退方案（`_fallback_index_kline`，第 429-433 行）用真实 `peTTM`/`pbMRQ` 历史序列算分位。两方案字段语义不一致，下游 `temperature.py`/`etf_recommend.py`/`daily_report.py` 统一把 `pe_percentile` 当估值分位消费。

**修改方案**（按 D-2 决策定稿，默认方案 A）：
1. `market_data.py` 返回结构新增 `price_percentile` 字段，仅承接收盘价分位；**`pe_percentile`/`pb_percentile` 只接受真实估值序列分位**。
2. 成分股方案（主子方案）中：真实 PE/PB 分位若可得，则独立计算（需历史成分股 PE 时间序列，`data_source` 下有 `percentile_lookback_years` 可复用）；若不可得，则 `pe_percentile`/`pb_percentile` 置 `None`，估值判断由下游按「无估值分位」降级为 `hold`/「无数据」展示，不再用价格分位冒充。
3. 回退方案（`_fallback_index_kline`）已是真实估值序列，无需改字段语义，只需在同结构上补齐 `price_percentile=None` 保持一致。
4. 下游消费者：`temperature.py:124` 取 `pe_percentile` 计算温度不变；`etf_recommend.py:141` 取 `pe_percentile` 无则 `hold`（已有该分支）；`daily_report.py` 展示独立 `price_percentile`（若保留）。`SYSTEM_PROMPT`/`ANALYSIS_PROMPT_TEMPLATE` 中的「PE 分位」措辞不变，但数据来源保证真实。

**配置变更**（`distill_changying/scripts/advisor/config.yaml`）：`data_source` 增加 `use_real_valuation_percentile: true`（true=只输出真实估值分位、价格分位独立成 `price_percentile`；false=保留旧近似，但必须独立标注 `price_percentile`，二者不再共享字段）。

**数据/DB 变更**：无。

**验证方式**：
- 单元测试：给定已知收盘价序列，断言 `price_percentile` 与 `pe_percentile`/`pb_percentile` 明确分离，价格分位不再赋值到估值字段。
- 字段语义测试：估值判断只消费 `pe_percentile`；`pe_percentile is None` 时下游走 `hold`/「无数据」。

---

### R03 · 早晨重扫使用当天日期，推送读昨天文件（P1）

**现状根因**：`daily_scan.py:41-46` `get_latest_trading_date()` 只按星期估算；盘前（07:30）重扫也返回「今天」。`feishu_push.py:212` 读 `signals_昨天.json`。重扫写入 `signals_今天.json`，推送读不到，且扫描日期/冷却/月计数错记。

**修改方案**：
1. `daily_scan.scan_daily_signals(config, signal_date=None)` 新增 `signal_date` 入参；`get_latest_trading_date` 仅作兜底，调度器显式传入「本次已完成行情的交易日」。
2. `scheduler_obpc.py` 在调用 `subprocess.run(["python", "scripts/daily_scan.py", "--signal-date", trading_date])` 时传入交易日，由 `DatabaseManager.get_latest_kline_date`（最大行情日期）确定 `trading_date`；`daily_scan.main` 新解析 `--signal-date` 参数。
3. `feishu_push.py` 新增 `--signal-date` 参数，读取 `signals_{signal_date}.json`（默认回退昨日的现有逻辑保留，避免破坏手动运行）。
4. 文件命名、落库、冷却、月计数、推送统一以传入交易日为准。

**配置变更**：无（调度链参数化）。

**数据/DB 变更**：无（`signals_{date}.json` 命名语义不变，只是日期来源统一）。

**验证方式**：
- 隔离场景 `morning_scan_date`：07:30 补全后传入交易日 = 昨日，写入 `signals_昨日.json`，`feishu_push` 同参数读到该文件并推送。
- 盘前/盘后两次调用同一交易日，文件与计数不重复不冲突。

---

### R04 · 投顾知识库未进镜像且无挂载（P1）

**现状根因**：`.dockerignore:14` 排除整个 `distill_changying/docs/`；`docker-compose.yml:84-88` 只挂载 `blog` 与 `distilled/logs`。`.state.json`、观点库、持仓、索引既不在镜像也不来自宿主机。

**修改方案**（与 R05 统一持久化方案，见 4.5）：
1. `.dockerignore` 第 14 行 `distill_changying/docs/` 改为只排除易变/日志子目录，保留核心产物：
   ```
   distill_changying/docs/archify/
   distill_changying/docs/distilled/logs/
   distill_changying/docs/长赢指数投资/_article_*.json
   ```
   使 `.state.json`、`观点库.jsonl`、持仓表、索引等进入镜像（首次初始化携带）。
2. `docker-compose.yml` `strategy-eadvisor` 增加挂载 `./distill_changying/docs/distilled:/app/distill_changying/docs/distilled`（读写），使运行期新产物持久化到宿主机。blog 仍 `:ro`。
3. 运行时产品路径与挂载路径对齐（`config.yaml` 的 `cache.path` 等引用 `docs/distilled/...`）。

**配置变更**：`.dockerignore`、`docker-compose.yml`。

**数据/DB 变更**：无结构化 DB 变更；持久化对象是文件（`.state.json`、观点库、持仓、索引）。

**验证方式**：
- 空目录首次部署：eadvisor 能读既有知识（`.state.json`/观点库/持仓）。
- 重建容器后：新生成产物仍在且内容一致。
- 模拟「空库 → 启动 → 生成 → 重建 → 再启动」读写通过。

---

### R05 · OBPC 信号目录未持久化（P1）

**现状根因**：`docker-compose.yml:57-61` obpc 只挂载 `/app/output`、`/app/data:ro`、`/app/logs`、`config`，未挂载 `/app/signals`。晚间扫描写 `/app/signals/signals_{date}.json`，早间推送从同目录读，重建容器丢失；且 DB 已有记录参与去重/冷却，无法靠重扫恢复同一批。

**修改方案**（D-6 二选一，默认方案 A「挂载 signals 目录」，成本最低、不破坏现有去重/冷却）：
1. `docker-compose.yml` `strategy-obpc` volumes 增加 `./signals:/app/signals`（宿主机 gitignore 该目录或纳入数据目录约定）。
2. 若后续选方案 B（数据库重建待推送批次）：`feishu_push.py` 增加从 `scan_results` 按 `scan_date` 重建待推送列表的路径，替代读 JSON 文件；本设计默认 **A**。

**配置变更**：`docker-compose.yml` 增加 `./signals:/app/signals`。

**数据/DB 变更**：无（方案 A）；方案 B 需 `feishu_push` 读 `scan_results`。

**验证方式**：
- 晚间扫描后、早间推送前重建容器，信号文件仍存在且可推送。
- （若走 B）推送内容与扫描一致，去重/冷却正确。

---

### R08 · 截面回测使用未来波动率（P1）

**现状根因**：`backtest_cross_section.py:99-100` `vol_med = vol.median()` 对整个区间取中位数，再用于全部历史时点评分。追加未来行情会改变既有日期的分数与仓位。

**修改方案**：
1. 改为「截至决策时点可得」的滚动/扩展统计：`vol_med` 用 `vol.expanding().median()`（前序样本扩展窗，不含未来），或独立训练期常量（训练期入 `config.yaml`）。
2. 增加前缀不变性检查：`calc_temperature` 对前 N 日与前 N+K 日分别计算，前 N 日 `score_vol` 序列完全一致。
3. 其余模块级硬编码（`REBALANCE_DAYS`/`TOP_K`/`LOOKBACKS`/`COST`/`MA_PERIOD`/`SLOPE_LOOKBACK`/`MACD`/`VOL_PERIOD`）提取到 `config.yaml` 的 `section_backtest` 节（见 4.4 硬编码整改）。

**配置变更**（`config/config.yaml` 新增 `section_backtest`）：
```yaml
section_backtest:
  use_training_window: false       # false=扩展窗滚动统计；true=固定训练期
  training_window_days: 500        # use_training_window=true 时的训练期
  rebalance_days: 20
  top_k: 20
  lookbacks: [20, 60]
  cost: 0.0035
  ma_period: 20
  slope_lookback: 5
  macd: [12, 26, 9]
  vol_period: 20
```

**数据/DB 变更**：无。

**验证方式**：
- 隔离场景 `future_volatility`：追加未来行情后既有日期分数/仓位不变。
- 前缀不变性：前 N 日与前 N+K 日两次回测，前 N 日结果完全一致。

---

### R09 · 持仓别名合并不同资产（P1）

**现状根因**：`merge_positions.py:59` `NAME_MAP["德国DAX"]` 混入「纳斯达克100」「美国标普500」；第 48 行「全指金融」混入「信息技术」。`normalize_name` 用于持仓合并（买卖/净份数），不同资产被混算。

**修改方案**：
1. `NAME_MAP` 只保留真实同资产别名：移除「纳斯达克100」「美国标普500」出 `德国DAX`；移除「信息技术」「全指信息」「易方达信息产业」出 `全指金融`；为它们各自建立独立标准名（如「纳斯达克100」「标普500」「信息技术」）。
2. 新增 `CATEGORY_MAP`（独立字段，仅展示分组用），不复用 `normalize_name` 做资产类别；`normalize_name` 只用于同资产买卖合并。
3. 受污染持仓重新生成：重跑 `merge_positions.py`，校验 `品种别名映射.json` 与持仓总表 CSV/MD。

**配置变更**：`merge_positions.py` 常量调整；`distill_changying/docs/distilled/品种别名映射.json` 更新。

**数据/DB 变更**：受影响持仓文件重新生成（`长赢指数理论持仓总表.md/.csv`）。

**验证方式**：
- 隔离场景 `asset_alias`：纳斯达克100、标普500 不再并入 DAX；信息技术不再并入全指金融；净份数不混算。
- 重新生成的持仓文件校验通过。

---

### R11 · 重试分支无法处理数据库日期字符串（P2）

**现状根因**：`update_kline_daily.py:272-280` 重试分支拿 `get_latest_kline_date`（返回 `str`）直接 `datetime.combine(latest_data, dt.min.time())`，触发 TypeError；首次更新分支（第 129-141 行）已显式解析字符串但重试分支未同步。

**修改方案**：抽一个共享辅助函数 `_parse_latest_date(latest_data, today)`（输入统一 tolerates `datetime`/`str`/`date`），首次与重试分支都调用它。重试分支不再 `datetime.combine(str, ...)`。

**配置变更**：无。

**数据/DB 变更**：无。

**验证方式**：
- 隔离场景 `retry`：首次失败后重试成功，有历史数据股票能补写（抓取 2 次、保存 >= 1 批，无 TypeError）。

---

### R12 · 请求超时后工作线程仍在运行（P2）

**现状根因**：`update_kline_daily.py:65-72` `executor.shutdown(wait=False, cancel_futures=True)` 无法取消已运行的 `get_kline`；外层循环继续创建线程、复用被占用的 Baostock 会话，连续超时累积线程。

**修改方案**：
1. 单股超时改为「可中断连接超时」或「隔离到可终止工作进程」；本设计优先最小侵入方案：**禁止复用正被旧请求占用的会话** —— 超时后创建新 `BaostockSession` 再继续，旧请求结束前不归还会话。
2. 超时阈值 `timeout` 从 `config.yaml` 的 `global.kline_update.request_timeout_seconds` 读取（`_fetch_with_timeout` 增加默认参数 + CLI 透传）。
3. `_fetch_with_timeout` 不再 `cancel_futures` 假取消，改为记录「孤儿线程」并隔离会话。

**配置变更**：`global.kline_update.request_timeout_seconds`（见 R13 配置节）。

**数据/DB 变更**：无。

**验证方式**：
- 隔离场景 `timeout_workers`：连续两次超时后并发工作线程数不随超时次数增长（不出现多个并发工作线程交错访问同一连接）。

---

### R14 · JSON 数组代码块被截成首对象（P2）

**现状根因**：`llm_utils.py:157-163` 直接 `json.loads` 失败后先 `_extract_json_braces`（大括号匹配）再尝试去 Markdown 围栏。对 ` ```json [...]``` ` 多对象数组，括号提取先命中内部的第一个 `{...}`，成功返回首 dict，其余静默丢失。

**修改方案**（`parse_json_response` 重排）：
1. 在 `_extract_json_braces` 之前，先检测并剥离 Markdown 代码块围栏（` ```json ... ``` ` 或 ` ``` ... ``` `），再进入 JSON 解析链。
2. `_extract_json_braces` 增加对顶层 `[`/`]` 的处理（数组提取），保证 `[]` 解析为 list 而非截成 dict。
3. 保持 dict 与 list 两种顶层类型兼容，避免反向影响既有 dict 解析。

**配置变更**：无。

**数据/DB 变更**：无。

**验证方式**：
- 隔离场景 `fenced_json_array`：` ```json [{...},{...}] ``` ` 完整解析为含全部对象的 list。

---

### R15 · 摘要 CSV 回读丢失文件名与年份（P2）

**现状根因**：`batch_processor.py:230` 用 `utf-8-sig` 写，`analyzer.py:46` 用 `utf-8` 读，首列表头带 BOM 成 `\ufefffile`，`row.get("file")` 空、`year` 空。另导出端把 `identifier`（哈希）当 `file`。

**修改方案**：
1. 统一编码：`analyzer.py` 回读改 `encoding="utf-8-sig"`（或 `utf-8` 读后 strip BOM）。
2. `batch_processor.py:export_to_csv` 导出真实来源名（`get_blog_identifier` 保留 `original_name` 字段），CSV 增加 `file` 存原始文件名、`id` 存哈希标识；`analyzer.load_summaries` 优先读 `file`，回退读 `id`。
3. 历史 CSV 兼容：`load_summaries` 对无 `file` 列的老文件回退 `row.get("id")`。

**配置变更**：无。

**数据/DB 变更**：`blog-index.csv` 表头增加 `file` 列（兼容旧列）。

**验证方式**：
- 隔离场景 `csv_bom`：回读后 `file`/`year` 非空且值正确；来源与年度分析正确。

---

### R16 · 摘要失败也被永久标记已处理（P2）

**现状根因**：`batch_processor.py:174-185` `summarize_with_retry` 失败返回占位摘要、异常分支也生成占位，但都无条件 `mark_processed`。后续增量只查 identifier 是否存在，短暂 API 故障变永久知识缺失。

**修改方案**：
1. 区分成功/失败：`summarize_with_retry` 返回是否成功的标记（或抛特定异常）；失败时不调 `mark_processed`。
2. `state_manager.mark_processed` 增加 `status` 字段（`ok`/`failed`），失败项保留重试资格；`export_to_csv` 不导出 `status != ok` 的记录。
3. `is_processed` 语义改为「已成功处理」（`status==ok`），失败不视为已处理。

**配置变更**：无。

**数据/DB 变更**：`state` JSON 增加 `status` 字段。

**验证方式**：
- 隔离场景 `failed_summary_not_retried`：两轮运行均调用摘要服务（首轮失败不永久跳过）；失败项不被标记已处理、不导出为有效产物。

---

### R17 · 仓位归一化重新突破边界（P2）

**现状根因**：`advisor_llm.py:318-326` 先逐项 clamp 到边界，再按总和缩放，缩放后不再校验边界；模型返回 80/10/60 → 54/7/39，债券(7)低于下限(10)，仍输出。

**修改方案**：
1. 采用「同时满足总和与各项上下界」的分配：把 stock/bond/cash 映射到可行凸区间后求解，或分两个层次 —— 先确定总和残差如何在「尚有富余的项」间分配，任何一步都保证落在 `[lower, upper]` 内。
2. 边界从 `config.yaml` 读取（`llm_analysis.position_bounds`），替换第 308-316 行硬编码 `5-80/10-50/10-60`。
3. 无法满足总和=100 与上下界时，按 D-4 决策处理：默认「拒绝该建议，降级规则引擎」。

**配置变更**（`llm_analysis` 新增）：
```yaml
llm_analysis:
  position_bounds:
    stock: {min: 5, max: 80}
    bond:  {min: 10, max: 50}
    cash:  {min: 10, max: 60}
  on_unsatisfiable: "reject"    # reject | hold | clamp
```

**数据/DB 变更**：无。

**验证方式**：
- 隔离场景 `llm_validation`：80/10/60 归一化后不出现任一项低于其下限；无法满足时输出明确失败/回退。

---

### R18 · 买卖份数校验允许负数（P2）

**现状根因**：`advisor_llm.py:361-368` 只限制 `shares > max_per_trade` 的上限，未校验正整数/类型；`buy` + `shares=-2` 原样输出，非数值还可能在比较时抛错。

**修改方案**：
1. 先校验 `action in ("buy","sell","hold")`；`buy/sell` 时 `shares` 必须为正整数且 `1 <= shares <= max_per_trade`；非法转 `hold` 或明确失败。
2. `hold` 强制 `shares = 0`。
3. `max_per_trade` 从 `config` 的 `shares_per_trade` 读取（已存在）。

**配置变更**：无新增（复用 `shares_per_trade`）。

**数据/DB 变更**：无。

**验证方式**：
- `shares=-2` 或非数值被拒绝，不原样进入输出。

---

### R22 · 旧表迁移前先建依赖新字段的索引（P2）

**现状根因**：`database.py:27-51` `__init__` 先 `_create_tables()`（含第 113-115 行 `CREATE INDEX ... idx_klines_frequency ON klines(frequency)`），再 `_migrate_add_frequency()`。旧表缺 `frequency` 时索引先失败，迁移永远不执行。

**修改方案**：
1. 调整顺序为 `_connect → _migrate_add_frequency → _create_tables`。迁移先补列、改约束，再建表/建索引。
2. `_create_tables` 内的 `idx_klines_frequency` 依赖 `frequency` 已存在（迁移已保证）。
3. 两种初始化路径（旧表 + 空库）都覆盖测试；迁移保持幂等（`ADD COLUMN IF NOT EXISTS` 已具备）。

**配置变更**：无。

**数据/DB 变更**：`klines` 迁移顺序调整；不新增列。

**验证方式**：
- mock/测试库：旧表缺 `frequency` 时迁移幂等补列后索引创建成功；空库初始化正常；两条路径均覆盖。

---

### R23 · 主入口调用子 CLI 导致 --all 中断（P2）

**现状根因**：`main.py:72-73` `run_kline_update` 调 `updater.main()`；后者重新解析 `sys.argv`，只接受 `--days`，遇 `--all/--update` 报错退出；子 `main` 末尾 `os._exit(0)` 终止整个进程。

**修改方案**：
1. `update_kline_daily.py` 拆分业务函数与 CLI：抽 `run_update(days) -> UpdateResult`（可正常 return），`main()` 只剩 argparse + 调 `run_update`；删除 `os._exit`（与 R13 合并处理）。
2. `main.py` `run_kline_update` 改调 `run_update(...)`（传入 `config` 的 `days`），不再调 `updater.main()`。

**配置变更**：无（days 可读 `config`）。

**数据/DB 变更**：无。

**验证方式**：
- `python main.py --all` 依次完成 更新 → 扫描 → 推送，不因参数错误或 `os._exit` 中断。

---

## 4. 关键设计决策与取舍

### 4.1 R13 独立 `global.kline_update` 节（不复用 `global.backfill`）

**取舍**：PRD 提到 R13 完整性阈值可引用 `global.backfill.failure_rate_threshold` 等。但 `backfill` 节语义是「补全脚本熔断」，与「日更新完整性判定」的参数生命周期、告警口径不同（补全按 `max_stocks_per_run` 批处理，日更新按全市场单日）。混用会导致改补全参数无意牵动日更新上线闸门。

**决策**：新增 `global.kline_update` 节，语义独立；`global.backfill` 保留不动。代价是配置稍多，但边界清晰、符合「配置即契约」原则。

### 4.2 R02↔R21 统一月度额度算法

**决策**：抽纯函数 `cap_by_monthly_quota(candidates, used_count, max_per_month, max_daily)`，实盘 `daily_scan.py` 与回测 `backtest_obpc.py` 共用（回测侧用 `InMemoryMonthlyCounter`，实盘侧用 `DatabaseMonthlyCounter` 读库）。

**要点**：
- 「按剩余额度截取」发生在「排序后、保存前」，而非「逐候选风控时」。
- 排序口径统一为「评分降序」（实盘 `daily_scan.py` Top N 与回测同日 Top N 一致）。
- 实盘并发安全：生产是单进程串行调度，天然串行；若未来多实例，则 `DigitalOcean` 用 DB 事务 `SELECT ... FOR UPDATE` 兜底（`reserve_monthly_slots` 可选实现）。

**取舍**：放弃「逐候选 hard-gate」的旧模型，改为「先收集后配额」，符合 PRD 期望「先收集、后截取」。

### 4.3 R07/R19/R20 统一成交/净值模型

**决策**：以 `TradeSimulator` 为 OBPC 成交事实源，`backtest_swing` 为波段成交事实源，二者统一「开盘跳空优先 + 盘中止损含滑点」语义（R07/R19）；绩效统一「现金+成交价+每日持仓市值」净值曲线（R20）。

**落地顺序**：R07（成交价判定）→ R19（波段卖出顺序）→ R20（净值曲线），保证净值建立在正确成交价上。

**取舍**：R20 引入 `equity` 曲线会改动 `compute_metrics` 的返回结构，`print_report` 需同步；但换来收益/回撤口径一致，值得。

### 4.4 回测脚本模块级硬编码整改（顺带消除）

R19/R20 涉及的 `backtest_swing.py` 有模块级硬编码 `COST`/`STOP_PARAMS`/`STRATEGY_PARAMS`/`EXIT_MODES`；R08 的 `backtest_cross_section.py` 有 `REBALANCE_DAYS` 等。这些虽未单列为缺陷，但违反「禁止硬编码」约束，且与本批回测修复强耦合。

**决策**：本批顺手将这些硬编码提取到 `config.yaml`（`section_backtest` 节 + `strategies.oversold_bounce.params` 复用交易成本/止损参数），避免修完 R19/R20 仍留硬编码被后续检测抓出。代价是 `backtest_swing.py` 需增加 config 加载，但符合项目规范。

### 4.5 R04/R05 统一持久化方案选型

**决策**：R04 采用「`.dockerignore` 缩窄 + `docker-compose` 挂载 `distilled` 目录（读写）」；R05 采用「挂载 `./signals:/app/signals`」。二者都是「宿主机目录持久化」模式，与项目 `logs`/`output` 既有挂载惯例一致，落地成本最低，且不引入数据库重建逻辑。

**取舍**：R05 放弃「数据库重建待推送批次」（D-6 方案 B），因为会改动 `feishu_push` 的去重/冷却逻辑，成本与风险更高，而「挂载目录」与项目现状完全一致。

---

## 5. PRD 待决策项技术建议（D-1~D-6）

> 每项给出推荐与理由，供用户拍板；标注实现依赖。

### D-1 · R10 飞书凭据轮换方式

**推荐：A + C 组合（注入环境变量 + 飞书后台轮换 + 更新 secret；暂不清 git 历史，历史清理另立任务评估）**。

- **理由**：立即止血 = 改注入 + 轮换，成本最低、立即生效。git 历史清理（B）需重写历史 + 强制推送，与项目「禁止 force push」规则冲突，且短时间内容易影响协作，建议作为独立安全任务评估，不阻塞本次修复。
- **技术影响**：`monitor.sh`/`config.yaml`/`.env.example`/GitHub Secrets 更新。

### D-2 · R01「价格分位作估值近似」是否接受

**推荐：A（接受近似为独立字段，估值判断改用真实估值分位；真实分位不可得时 `pe_percentile=None` 并降级 hold）**。

- **理由**：数据现实是 `market_data.py` 回退方案已能取真实 `peTTM/pbMRQ` 历史序列（`_fallback_index_kline`），说明真实序列可得；但成分股方案（主子方案）逐成分股查历史 PE 的耗时高（代码注释明示这是为了省时而用价格分位近似）。A 方案把「价格分位」降级为独立展示字段、估值判断走真实分位，既修语义又不过度放大耗时；真实分位不可得时诚实返回 None 并降级，优于用错误数据硬凑。
- **若选 B**：需为 6 个指数全部建立历史成分股 PE 时间序列（数据量与耗时显著上升），需另行评估数据源与缓存。

### D-3 · R06 指数缺失/过期降级策略

**推荐：C（该日判定「风控数据不可用」并跳过相关开仓）+ 配置化**。

- **理由**：C 最安全且语义最简单——数据不新鲜就绝不用旧 MA/MACD 开新仓，符合「宁缺毋滥」的风控边界；比 A（显式失败中断全流程）更稳健（单个数据源故障不至于全线停摆），比 B（用最近 N 日降级）更安全（避免把过期趋势当当天趋势）。三个选项都要入 `config`（`freshness_strategy`/`freshness_stale_days`），C 作为默认值。
- **实现**：`freshness_strategy: "skip"` → `get_context` 返回 `None` + 记 warning；`daily_scan` 已有「market_context 为 None 时跳过大盘过滤」的兜底逻辑，需同步确认该兜底在「数据不可用」场景的语义（改为「风控不可用，跳过开仓」而非「跳过过滤继续开仓」）。

### D-4 · R17 无法满足边界时的兜底

**推荐：A（拒绝该建议并回退规则引擎）**。

- **理由**：项目硬约束「LLM 失败必须有规则引擎兜底」，而规则引擎 `position_mapping` 天然满足总和=100 与边界。LLM 输出不可满足边界时，回退规则引擎既符合硬约束，又保证输出质量；B（hold）/C（保守下限裁剪）会引入「静默改仓」的新语义与风险。推荐 A。

### D-5 · 硬约束「LLM 必须 thinking 模式 reasoning_effort=high」是否有效

**推荐：设 A 为默认（硬约束失效，更新项目记忆/规则，deepseek-chat 标准调用为正确形态），但需一次最小实测佐证**。

- **理由**：代码现状（`llm_utils.py:214-215` 注释、`advisor/config.yaml:8`「deepseek-v4-pro 不存在，已修正」、`llm_analysis.model=deepseek-chat`）已多处明示 `reasoning_effort`/`thinking` 是早期误引入的「V4 Pro 幻觉参数」，deepseek-chat 会静默忽略。当前项目记忆里「必须 deepseek-... V4 Pro + reasoning_effort=high」与代码现状已矛盾（本次审查发现的额外硬约束冲突）。
- **安全落地**：编码前先做一次最小实测（向 deepseek-chat 发带 `reasoning_effort` 的参数，确认是否报错/忽略），用证据裁定：若确为幻觉参数 → 走 A 并更新 `docs/`+项目记忆；若 API 已支持 → 走 B（恢复 thinking 模式并回退模型）。**避免在错误前提上改 LLM 调用链**（PRD 明确要求发射前裁定）。

### D-6 · R05 信号持久化选型

**推荐：A（挂载 `/app/signals` 目录）**。

- **理由**：与项目 `logs`/`output` 既有挂载惯例一致，成本最低，不改动 `feishu_push` 的去重/冷却逻辑，风险最小。B 的收益（重建时从 DB 精确重建）当前无强需求，且改造成本高。

---

## 6. 测试与验收设计

### 6.1 幻觉测试（编码后、功能测试前，逐项 10 清单）

覆盖所有改动文件，重点核查：
1. 新增 import/类/函数真实存在（如 `UpdateResult`、`cap_by_monthly_quota`、`_parse_latest_date`）。
2. 新配置键位在 `config.yaml` 真实存在且键名拼写一致（`global.kline_update.*`、`section_backtest.*`、`position_bounds.*`、`index_filter.freshness_*`）。
3. 环境变量名一致（`FEISHU_WEBHOOK` / `FEISHU_WEBHOOK_MONITOR` / `FEISHU_WEBHOOK_EADVISOR`）。
4. 文件路径真实（`/app/signals`、`distill_changying/docs/distilled`）。
5. `os._exit` 已从 `update_kline_daily.py` / `main.py` 调用链移除后，退出码语义与 `scheduler_kline` 一致。
6. 顺序逻辑无颠倒（R07/R19 退出优先级；R22 迁移顺序 `_migrate → _create_tables`）。
7. 异常路径有兜底（R06 None 处理、R16 失败状态、R17 不可满足回退）。
8. 同步/异步匹配（本批均为同步脚本，无 await 混用）。
9. 无新增硬编码数值（R11/R12/R13/R08/R17 阈值全部读 config）。
10. 每个文件完整无截断。

### 6.2 功能测试（api-test-pro 或等价，本地执行，禁止服务器回测）

- **R02/R21**：`monthly_limit` 复现断言翻转（29→1 保存，月计数 30）；回测前缀不变性（交换股票顺序结果不变）。
- **R03**：`morning_scan_date` 文件与计数一致。
- **R07/R19/R20**：`gap_stop` / `swing_exit_order` / 净值一致性断言。
- **R08**：`future_volatility` 前缀不变性。
- **R09**：`asset_alias` 别名不混算。
- **R11/R12/R13/R23**：`retry` / `timeout_workers` / 失败退出码 / `--all` 全链路。
- **R14/R15/R16**：`fenced_json_array` / `csv_bom` / `failed_summary_not_retried`。
- **R17/R18**：`llm_validation` 边界/负份数拒绝。
- **R22**：旧表 + 空库双路径迁移测试（mock PG）。
- **R01/R06/R10**：字段语义 / 新鲜度 / 明文零检测。

### 6.3 reproduce.py 断言翻转

`docs/reports/code-review-2026-09-29/reproduce.py` 的 13 组隔离复现断言由「缺陷仍存在」翻转为「缺陷已修复」语义，全绿。

### 6.4 覆盖率要求（强制）

- 核心逻辑 100%：R07/R19 的每个退出分支（跳空/无跳空/边界）、R02/R21 的截断两分支、R16 的 ok/failed 分支、R17 的可满足/不可满足分支。
- 边界 100%：空值/None/0/负数/超时。
- 配置路径：每项新增配置至少一个用例覆盖。

### 6.5 前缀不变性测试（回测类专用）

R08/R21 必有「前 N 日 vs 前 N+K 日」或「换遍历顺序」不变性断言，作为回测有效性唯一证据（修复前绩效不作证据）。

---

## 7. 部署影响

| 影响项 | 说明 |
|--------|------|
| **CI/CD 触发** | 全部改动落 `main` 后触发 GitHub Actions 构建三镜像 + SSH 部署（按 [deployment.md](../../../.trae/rules/deployment.md)）。`.dockerignore`/`docker-compose.yml` 改动会影响 eadvisor/obpc 镜像构建上下文与挂载。 |
| **config.yaml** | volume 挂载注入，改配置重启容器即生效（R13/R06/R08/R17/R18 新增键位）。 |
| **docker-compose.yml** | 新增两目录挂载（`./signals`、`./distill_changying/docs/distilled`），需在服务器部署目录创建对应宿主机目录并确认权限。 |
| **凭据轮换** | 飞书后台重置 webhook + 更新 GitHub Secrets / 服务器 `.env`，需人工执行（D-1）。 |
| **星星迁移** | R22 仅改代码顺序，不写数据迁移 SQL；旧库首次启动时自动迁移，需在部署前空库/旧库验证。 |
| **task_status** | `status` 字段开始写 `failed`；`monitor.sh` 的 `check_kline_sync` 需兼容读取 `completed` 判断（保持现状，仅关注 last_completed_at 非空即可，不误报）。 |
| **回测类** | 回测脚本仅本地运行，不进生产容器，无部署风险；但修复前绩效作废，需重新生成回测报告。 |

---

## 8. 风险与回滚

### 8.1 关键风险

| 风险 | 等级 | 缓解 |
|------|------|------|
| R13 退出码契约变更影响调度/监控 | 高 | 同步更新 `scheduler_kline`/`scheduler_obpc`/`monitor.sh` 语义；上线前做失败注入演练。 |
| R06「数据不可用跳过开仓」可能减少信号量 | 中 | 依赖 R13 先修，确保指数缺失是可观测的；D-3 选 C 前向用户确认业务接受度。 |
| R01 估值口径变化导致投顾输出变化 | 中 | 影响面已在 PRD 标注；先发用户确认 D-2，再上线。 |
| R07/R19/R20 回测绩效大幅变化 | 中 | 修复前绩效作废；以正确行为断言 + 前缀不变性为唯一验收。 |
| R21 候选全量生成内存开销 | 中 | 候选信号按股票+日期组织，数量远小于全 K 线；必要时流式分组，先评估再落地。 |
| R04/`.dockerignore` 缩窄导致镜像体积上升 | 低 | 只放开核心产物（`.state.json`/观点库/持仓/索引），排除 `archify`/`logs`/`article_index` 等大文件。 |
| R10 历史 git 未清（若选 C） | 中 | 轮换后旧 webhook 失效，泄露面收敛；历史清理单独立项。 |

### 8.2 回滚策略

- 所有改动均有明确 commit 边界，按 `git revert` 逐项回滚。
- 配置类（R13/R06/R08/R17）回滚 = 还原 `config.yaml` 对应节 + 重启容器，无需重建镜像。
- 代码类（R02/R21/R07/R19/R20/R22/R23）回滚 = 回退对应 commit，走 CI/CD 重新构建（或 `docker compose down && up -d` 回退上一镜像，见 deployment.md 3.1）。
- 部署配置类（R04/R05/R10）回滚 = 还原 `docker-compose.yml`/`.dockerignore`/`monitor.sh`，重启容器；凭据回滚 = 恢复旧 webhook（若未失效）。

---

**文档版本**：V1.0
**最后更新**：2026-09-29