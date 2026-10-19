# OBPC 扫描可靠性与风控可解释性整改 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 K 线不完整却放行、补全后漏扫、失败被误报为零信号、形态选择错误和结果重复等问题，并在不凭感觉放宽交易风控的前提下恢复可审计、可解释、可验收的每日扫描链路。

**Architecture:** 将流水线拆成“数据就绪 → 原始形态检测 → 风控裁决 → 结果持久化 → 通知”五个具有明确状态的阶段。调度器只消费带目标交易日和成功状态的任务记录；扫描无论结果如何都写入结构化运行记录；通知依据运行状态生成不同文案，不再用“文件不存在”推断“零信号”。策略参数变更通过本地回测与既有业务指标决策，不直接在生产环境试错。

**Tech Stack:** Python 3、PostgreSQL、pandas、PyYAML、pytest、Docker Compose、GitHub Actions、飞书 Webhook。

---

## 1. 背景与生产证据

2026-10-16 的“今日无符合形态的买入信号”不是单一原因造成，而是以下链路叠加：

1. 当晚股票池为 2911 只，K 线更新只成功处理 1059 只后触发熔断，实际覆盖约 36.4%。
2. 完整性算法使用 `success / (success + failed)`，没有把熔断后约 1850 只未处理股票纳入分母，因此显示 99.8% 并错误判定 PASS。
3. OBPC 扫描在个股遍历之前执行沪深300市场过滤；当日 `DIF=-97.67`，直接返回空列表，实际上没有扫描 2911 只个股。
4. 周六早晨补全后，10 月 16 日数据恢复到约 2910 条，但重扫逻辑以“当前自然日是否交易日”为条件，周六直接跳过。
5. 扫描提前返回时没有生成信号文件；推送端把“文件不存在”转换为空列表，最终发送“今日无信号”。
6. 使用完整数据按最近窗口诊断，可以识别 10 个原始形态候选，但当前市场风控及 `score_multiplier_extreme_weak: 0.0` 会将其全部拦截。

因此，当前通知只能说明“最终没有可推送信号”，不能证明“完整扫描后没有形态”。

## 2. 问题、影响与解决方案总表

| ID | 优先级 | 问题 | 业务影响 | 解决方案 | 核心验收 |
|---|---|---|---|---|---|
| R-01 | P1 | 熔断后未处理股票不进入完整率分母 | 少量数据被误判完整并放行 | 增加处理覆盖率和目标交易日覆盖率双校验 | 1059/2911 必须 FAIL；2910/2911 才可 PASS |
| R-02 | P1 | OBPC 只看任务时间，不看 `status` 和目标日期 | 新鲜的失败任务也会放行 | 任务状态增加 `target_date`、详情；等待函数校验状态、日期和新鲜度 | `failed`、过期或日期不匹配均不得扫描 |
| R-03 | P1 | 等待超时返回值被忽略 | 数据未就绪仍开始扫描 | 超时或失败时阻断扫描并记录 `blocked_data` | 超时后 `daily_scan.py` 调用次数为 0 |
| R-04 | P1 | 周末跳过前一交易日补全重扫 | 周五漏数周六补齐后永久漏扫 | 以目标交易日和补全状态判断，不以当前自然日判断 | 周六可重扫周五；不会重扫周四或重复执行 |
| R-05 | P1 | 缺失文件被解释为零信号 | 用户收到错误结论，无法区分失败、风控和真零信号 | 新增 `scan_runs` 状态记录；通知按状态分流 | 缺失/失败发送异常提醒，不能发送“无形态” |
| R-06 | P1 | 大盘预检查在原始形态检测前返回 | 无法知道市场弱时是否存在形态 | 原始检测与风控裁决解耦，先统计候选再裁决 | 通知可显示原始候选数、过滤数和过滤原因 |
| R-07 | P1 | 策略返回最旧的第一个完整形态 | 旧形态可能遮蔽近期有效形态 | 候选从新到旧选择，或完整收集后按回踩日期和评分排序 | 旧、新华形态并存时必须返回最新有效形态 |
| R-08 | P1 | 配置仍为 `macd`，与已采纳 `ma_position` 决策冲突 | 生产行为与文档、预期不一致 | 先恢复已采纳配置；任何进一步放宽必须经本地回测 | 配置、决策、日志三者方法一致 |
| R-09 | P2 | 重扫对 `scan_results` 重复 INSERT | 冷却期、月度和年度额度被重复计数 | 唯一约束 `(scan_date, code)` 加 UPSERT | 同日同股重扫后仍只有一条结果 |
| R-10 | P2 | 数据库股票池回退未执行全部过滤项 | 固定 CSV 缺失时股票池语义漂移 | 集中实现并测试所有股票池过滤规则 | 文件路径和数据库回退得到相同代码集合 |
| R-11 | P2 | 推送去重仅存进程内存 | 容器重启后可能重复推送 | 使用 `push_history`/运行记录做持久化幂等 | 重启后同一信号日不重复发送 |
| R-12 | P2 | E-advisor 归一化后可能再次越界 | 输出不符合配置范围的仓位 | 使用有上下界的总和归一化算法并二次校验 | 任意输入均满足范围且总和等于 100 |
| R-13 | P2 | 部署脚本称清理悬空镜像，却运行 `prune -af` | 删除可用于回滚的旧镜像 | 改为只清理 dangling 镜像或配置保留策略 | 已打标签的回滚镜像不被删除 |
| R-14 | P2 | 数据库密码存在弱默认值 | 环境变量缺失时静默使用固定口令 | 生产启动时要求显式 `DB_PASSWORD` | 未配置密码时生产容器启动失败且提示明确 |

## 3. 范围和边界

### 3.1 本次实施范围

- 修复 R-01 至 R-14。
- 新增单元测试、调度集成测试和只读生产验收脚本。
- 更新 OBPC、配置、数据库和部署相关文档。
- 在本地执行 OBPC 多方案回测，形成配置决策记录。

### 3.2 不在本次范围

- 不在生产服务器运行回测或参数优化。
- 不因为“最近信号少”直接关闭全部风控。
- 不扩大到创业板和科创板；若要扩大股票池，应另立策略研究任务并单独回测。
- 不自动提交、推送或部署；这些是独立授权域。

## 4. 目标数据流与状态语义

```text
K线更新
  ├─ completed(target_date, coverage metrics) ──> 原始形态检测
  └─ failed/incomplete ──> scan_runs.blocked_data ──> 异常通知

原始形态检测
  ├─ raw_candidate_count = N
  └─ 风控裁决
       ├─ completed_with_signals ──> 信号持久化 ──> 信号通知
       ├─ completed_zero ─────────> “完整扫描后零候选”通知
       └─ blocked_market ─────────> “发现 N 个原始候选，市场风控拦截”通知
```

`scan_runs.status` 只允许以下值：

| 状态 | 含义 | 可否表述为“完整扫描后零形态” |
|---|---|---|
| `running` | 扫描已开始、尚未结束 | 否 |
| `completed_with_signals` | 完整扫描且有最终信号 | 否，直接推信号 |
| `completed_zero` | 完整扫描，原始候选为零 | 是 |
| `blocked_market` | 完成原始扫描，但最终被市场风控拦截 | 否 |
| `blocked_data` | 数据完整性不足，未执行扫描 | 否 |
| `failed` | 扫描异常 | 否 |

## 5. 文件变更地图

| 文件 | 责任 |
|---|---|
| `scripts/data/update_kline_daily.py` | 计算处理覆盖率和目标日覆盖率，输出可审计的 `UpdateResult` |
| `scripts/scheduler_kline.py` | 写入带目标日期、状态和指标详情的任务状态 |
| `scripts/scheduler_obpc.py` | 严格等待数据、按目标交易日重扫、持久化推送幂等 |
| `scripts/daily_scan.py` | 返回结构化扫描结果，分离原始检测和风控裁决，始终记录运行状态 |
| `scripts/feishu_push.py` | 按扫描状态生成准确通知，不再通过文件存在性推断状态 |
| `strategy/oversold_bounce/strategy.py` | 选择最新有效形态 |
| `strategy/oversold_bounce/risk_control.py` | 保留单一、可解释的市场裁决入口和统计字段 |
| `data/database.py` | 新增 `scan_runs`、任务状态字段、幂等 UPSERT、股票池过滤 |
| `config/config.yaml` | 新增数据覆盖阈值；恢复已采纳市场过滤方法；保留回测配置依据 |
| `main.py` | 一次性完整流程严格传播失败，不在数据不完整时继续扫描 |
| `distill_changying/scripts/advisor/advisor_llm.py` | 修复仓位归一化越界 |
| `.github/workflows/deploy.yml` | 修复镜像清理范围 |
| `docker-compose.yml`、`.env.example` | 移除生产数据库密码弱默认值 |
| `tests/` | 新增各层单元和集成测试 |
| `docs/designs/OBPC策略说明文档.md` | 更新扫描状态、风控顺序和通知语义 |
| `docs/designs/配置管理设计.md` | 记录新增配置和阈值依据 |
| `docs/designs/数据库设计.md` | 记录 `scan_runs` 和唯一约束 |
| `docs/knowledge/decisions/` | 记录最终回测选定的风控方案 |

---

### Task 1: 建立数据完整性失败的回归测试

**Files:**
- Create: `tests/data/test_update_result.py`
- Modify: `scripts/data/update_kline_daily.py:64-128`
- Modify: `config/config.yaml`

- [ ] **Step 1: 写入能复现 10 月 16 日问题的失败测试**

```python
from scripts.data.update_kline_daily import UpdateResult


def test_breaker_leaves_most_stocks_unprocessed_and_must_fail():
    result = UpdateResult(success=1059, failed=2, skipped=0, total=2911)

    assert result.processed_count == 1061
    assert result.coverage_rate == 1061 / 2911
    assert result.is_complete(
        min_success_rate=0.90,
        total_failure_limit=500,
        min_coverage_rate=0.98,
    ) is False


def test_nearly_complete_market_passes():
    result = UpdateResult(success=2909, failed=1, skipped=1, total=2911)

    assert result.is_complete(
        min_success_rate=0.90,
        total_failure_limit=500,
        min_coverage_rate=0.98,
    ) is True
```

- [ ] **Step 2: 运行测试并确认旧实现失败**

Run: `pytest tests/data/test_update_result.py -v`

Expected: FAIL，提示 `UpdateResult` 没有 `processed_count`/`coverage_rate`，或 1059/2911 被错误判定为完整。

- [ ] **Step 3: 实现处理覆盖率校验**

在 `UpdateResult` 中加入：

```python
@property
def processed_count(self) -> int:
    return self.success + self.failed + self.skipped

@property
def coverage_rate(self) -> float:
    if self.total <= 0:
        return 0.0
    return self.processed_count / self.total

def is_complete(
    self,
    min_success_rate: float,
    total_failure_limit: int,
    min_coverage_rate: float,
) -> bool:
    if self.total <= 0 or self.coverage_rate < min_coverage_rate:
        return False
    if self.skipped_ratio >= 0.95:
        return True
    return self.success_rate >= min_success_rate and self.failed <= total_failure_limit
```

配置增加：

```yaml
global:
  kline_update:
    min_coverage_rate: 0.98  # 生产正常值约 2910/2911；允许少量停牌或退市异常，禁止大面积漏处理
```

- [ ] **Step 4: 让所有调用点显式传入配置值**

`run_update()`、`scripts/scheduler_kline.py` 和 `main.py` 都必须从 `global.kline_update.min_coverage_rate` 读取，日志必须输出 `processed_count/total` 和 `coverage_rate`。

- [ ] **Step 5: 运行测试**

Run: `pytest tests/data/test_update_result.py -v`

Expected: 2 passed。

- [ ] **Step 6: 提交本任务**

```bash
git add config/config.yaml scripts/data/update_kline_daily.py tests/data/test_update_result.py
git commit -m "fix: block incomplete kline batches by coverage"
```

**验收标准：**

- 1059 成功、2 失败、总数 2911 时必须 FAIL。
- 处理覆盖率不足 98% 时，无论 `success_rate` 多高都必须 FAIL。
- 日志能够直接看到“处理 1061/2911、覆盖 36.45%、FAIL”。

---

### Task 2: 让任务状态携带目标交易日并严格阻断失败任务

**Files:**
- Create: `tests/scheduler/test_task_gate.py`
- Modify: `data/database.py`
- Modify: `scripts/scheduler_kline.py:51-68`
- Modify: `scripts/scheduler_obpc.py:55-115`
- Modify: `main.py:70-95`
- Modify: `docs/designs/数据库设计.md`

- [ ] **Step 1: 为任务状态网关写失败测试**

```python
from datetime import datetime, timezone

from scripts.scheduler_obpc import task_status_allows_run


def test_failed_task_never_allows_scan():
    row = {
        "status": "failed",
        "target_date": "2026-10-16",
        "last_completed_at": datetime.now(timezone.utc),
    }
    assert task_status_allows_run(row, "2026-10-16", 3600) is False


def test_completed_wrong_date_never_allows_scan():
    row = {
        "status": "completed",
        "target_date": "2026-10-15",
        "last_completed_at": datetime.now(timezone.utc),
    }
    assert task_status_allows_run(row, "2026-10-16", 3600) is False
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `pytest tests/scheduler/test_task_gate.py -v`

Expected: FAIL，函数尚不存在。

- [ ] **Step 3: 扩展任务状态表**

使用幂等迁移增加：

```sql
ALTER TABLE schema_stockfilter.task_status
    ADD COLUMN IF NOT EXISTS target_date DATE,
    ADD COLUMN IF NOT EXISTS details JSONB NOT NULL DEFAULT '{}'::jsonb;
```

`_write_task_status()` 改为接收 `target_date` 和 `details`，其中 `details` 至少保存：

```json
{
  "success": 1059,
  "failed": 2,
  "skipped": 0,
  "total": 2911,
  "processed_count": 1061,
  "coverage_rate": 0.3645
}
```

- [ ] **Step 4: 提取纯函数并严格判断**

```python
def task_status_allows_run(row: dict, target_date: str, freshness_seconds: int) -> bool:
    if not row or row.get("status") != "completed":
        return False
    if str(row.get("target_date")) != target_date:
        return False
    completed_at = row.get("last_completed_at")
    if completed_at is None:
        return False
    if completed_at.tzinfo is None:
        completed_at = completed_at.replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - completed_at).total_seconds()
    return 0 <= age < freshness_seconds
```

- [ ] **Step 5: 阻断等待超时和失败**

22:15 分支必须改为：

```python
target_date = get_signal_target_date()
if not target_date or not wait_for_kline_update(target_date):
    record_scan_blocked(target_date, "blocked_data", "kline_not_ready")
else:
    run_scan(target_date)
```

`main.run_all()` 必须在 `is_complete()` 为 False 时返回非零状态或抛出明确异常，禁止继续调用扫描和推送。

- [ ] **Step 6: 运行测试**

Run: `pytest tests/scheduler/test_task_gate.py tests/data/test_update_result.py -v`

Expected: 全部 PASS。

- [ ] **Step 7: 提交本任务**

```bash
git add data/database.py scripts/scheduler_kline.py scripts/scheduler_obpc.py main.py tests/scheduler/test_task_gate.py docs/designs/数据库设计.md
git commit -m "fix: gate scans on successful target-date data tasks"
```

**验收标准：**

- `failed` 状态即使时间戳刚更新也不能放行。
- `completed` 但目标日期不同不能放行。
- 等待超时后不能启动 `daily_scan.py`。
- `main.py --run-all` 数据不完整时以非零状态结束。

---

### Task 3: 修复周末补全后漏扫

**Files:**
- Create: `tests/scheduler/test_rescan_policy.py`
- Modify: `scripts/scheduler_obpc.py:190-232`

- [ ] **Step 1: 写周六重扫周五的失败测试**

```python
from scripts.scheduler_obpc import should_rescan_target


def test_saturday_may_rescan_friday_after_completed_backfill():
    assert should_rescan_target(
        now_date="2026-10-17",
        target_date="2026-10-16",
        backfill_status="completed",
        already_scanned=False,
    ) is True


def test_same_target_is_idempotent():
    assert should_rescan_target(
        now_date="2026-10-17",
        target_date="2026-10-16",
        backfill_status="completed",
        already_scanned=True,
    ) is False
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `pytest tests/scheduler/test_rescan_policy.py -v`

Expected: FAIL，函数尚不存在。

- [ ] **Step 3: 按目标交易日而非当前自然日判断**

```python
def should_rescan_target(
    now_date: str,
    target_date: str,
    backfill_status: str,
    already_scanned: bool,
) -> bool:
    del now_date
    return (
        bool(target_date)
        and is_trading_day(target_date)
        and backfill_status == "completed"
        and not already_scanned
    )
```

删除 07:30 重扫分支对 `is_trading_day(today)` 的提前跳过；使用 `scan_runs` 判断目标日期是否已经在当前数据版本上完成重扫。

- [ ] **Step 4: 运行测试**

Run: `pytest tests/scheduler/test_rescan_policy.py -v`

Expected: 2 passed。

- [ ] **Step 5: 提交本任务**

```bash
git add scripts/scheduler_obpc.py tests/scheduler/test_rescan_policy.py
git commit -m "fix: rescan completed trading dates on weekends"
```

**验收标准：**

- 周六 07:30 可以重扫周五目标日期。
- 周日不能错误重扫更早日期。
- 同一目标日期和同一数据版本最多成功重扫一次。

---

### Task 4: 引入结构化扫描运行状态并修复通知语义

**Files:**
- Create: `tests/scan/test_scan_outcome.py`
- Create: `tests/notification/test_feishu_status_message.py`
- Modify: `data/database.py`
- Modify: `scripts/daily_scan.py`
- Modify: `scripts/feishu_push.py`
- Modify: `docs/designs/数据库设计.md`

- [ ] **Step 1: 写通知状态测试**

```python
from scripts.feishu_push import build_scan_summary_message


def test_missing_run_is_not_reported_as_zero_signal():
    message = build_scan_summary_message("2026-10-16", None)
    assert "扫描结果缺失" in message
    assert "无符合形态" not in message


def test_market_block_reports_raw_candidates():
    run = {
        "status": "blocked_market",
        "raw_candidate_count": 10,
        "selected_count": 0,
        "filter_reason": "沪深300 DIF <= 0",
    }
    message = build_scan_summary_message("2026-10-16", run)
    assert "10 个原始形态候选" in message
    assert "市场风控拦截" in message


def test_completed_zero_is_the_only_true_zero_message():
    run = {
        "status": "completed_zero",
        "raw_candidate_count": 0,
        "selected_count": 0,
    }
    message = build_scan_summary_message("2026-10-16", run)
    assert "完整扫描后未发现形态" in message
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `pytest tests/notification/test_feishu_status_message.py -v`

Expected: FAIL，消息构造函数尚不存在。

- [ ] **Step 3: 新增 `scan_runs` 表**

```sql
CREATE TABLE IF NOT EXISTS schema_stockfilter.scan_runs (
    id BIGSERIAL PRIMARY KEY,
    signal_date DATE NOT NULL,
    run_kind VARCHAR(20) NOT NULL,
    status VARCHAR(32) NOT NULL,
    stock_pool_count INTEGER NOT NULL DEFAULT 0,
    scanned_count INTEGER NOT NULL DEFAULT 0,
    raw_candidate_count INTEGER NOT NULL DEFAULT 0,
    selected_count INTEGER NOT NULL DEFAULT 0,
    filter_reason TEXT,
    data_metrics JSONB NOT NULL DEFAULT '{}'::jsonb,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    UNIQUE (signal_date, run_kind)
);
```

- [ ] **Step 4: 让扫描始终终结为明确状态**

`daily_scan.py` 必须用 `try/except/finally` 确保每次运行最终写入六种状态之一。零信号也必须写出 `signals/signals_<date>.json`，内容为 `[]`；文件只承载信号，运行真相以 `scan_runs` 为准。

- [ ] **Step 5: 移除“大盘不佳即提前返回”**

大盘上下文可在扫描前计算，但不能在个股检测前返回。流程必须是：

```python
raw_candidates = detect_raw_candidates(stock_list_df, strategy, db, signal_date)
decision = apply_risk_controls(raw_candidates, market_context, risk_controller)
outcome = persist_scan_outcome(signal_date, raw_candidates, decision)
return outcome
```

当市场硬过滤失败时，`raw_candidate_count` 仍须保存，`selected_count=0`，状态为 `blocked_market`。

- [ ] **Step 6: 按状态构造通知**

```python
def build_scan_summary_message(signal_date: str, run: dict | None) -> str:
    if run is None:
        return f"股票形态扫描 {signal_date}\n\n扫描结果缺失，请检查调度与数据任务。"
    status = run["status"]
    if status == "completed_zero":
        return f"股票形态扫描 {signal_date}\n\n完整扫描后未发现形态。"
    if status == "blocked_market":
        return (
            f"股票形态扫描 {signal_date}\n\n"
            f"发现 {run['raw_candidate_count']} 个原始形态候选，"
            f"但被市场风控拦截：{run.get('filter_reason') or '未记录原因'}。"
        )
    if status == "blocked_data":
        return f"股票形态扫描 {signal_date}\n\n数据不完整，本次扫描已阻断。"
    if status == "failed":
        return f"股票形态扫描 {signal_date}\n\n扫描任务失败，请检查日志。"
    raise ValueError(f"状态 {status} 应由信号卡片流程处理")
```

- [ ] **Step 7: 运行测试**

Run: `pytest tests/scan/test_scan_outcome.py tests/notification/test_feishu_status_message.py -v`

Expected: 全部 PASS。

- [ ] **Step 8: 提交本任务**

```bash
git add data/database.py scripts/daily_scan.py scripts/feishu_push.py tests/scan tests/notification docs/designs/数据库设计.md
git commit -m "feat: persist scan outcomes and send truthful notifications"
```

**验收标准：**

- 只有 `completed_zero` 可以发送“完整扫描后未发现形态”。
- 数据不足、扫描失败、运行记录缺失必须发送异常语义。
- `blocked_market` 必须展示原始候选数、最终信号数、指数名称和过滤原因。
- 10 月 16 日重放应展示“10 个原始候选，市场风控拦截”，而不是“无符合形态”。

---

### Task 5: 修复最新形态选择和扫描结果幂等性

**Files:**
- Create: `tests/strategy/test_latest_pattern_selection.py`
- Create: `tests/data/test_scan_result_upsert.py`
- Modify: `strategy/oversold_bounce/strategy.py:150-343`
- Modify: `data/database.py:117-143,368-395`

- [ ] **Step 1: 写“旧形态不能遮蔽新形态”的失败测试**

构造包含两个完整形态的数据集，旧形态回踩日为 `2026-09-01`，新形态回踩日为 `2026-10-16`：

```python
def test_analyze_returns_most_recent_complete_pattern(strategy, two_pattern_df):
    # 真实签名（strategy/oversold_bounce/strategy.py:85-147）：
    # analyze(code, {'d': df}) -> Optional[Signal]，回踩日位于 signal.detail['retrace_date']
    signal = strategy.analyze("TEST", {"d": two_pattern_df})
    assert signal is not None
    assert str(signal.detail["retrace_date"])[:10] == "2026-10-16"
```

- [ ] **Step 2: 运行测试并确认旧实现失败**

Run: `pytest tests/strategy/test_latest_pattern_selection.py -v`

Expected: FAIL，旧实现返回 `2026-09-01`。

- [ ] **Step 3: 从新到旧评估候选**

将：

```python
for i in range(drop_window, len(df)):
```

改为：

```python
for i in range(len(df) - 1, drop_window - 1, -1):
```

并保留所有时间顺序、成交量和流动性校验不变。

- [ ] **Step 4: 为扫描结果幂等写测试**

```python
def test_same_stock_and_date_is_upserted(database, scan_result):
    database.save_scan_result("2026-10-16", [scan_result])
    changed = {**scan_result, "score": 91.5}
    database.save_scan_result("2026-10-16", [changed])

    rows = database.fetch_scan_results("2026-10-16", scan_result["code"])
    assert len(rows) == 1
    assert float(rows[0]["score"]) == 91.5
```

- [ ] **Step 5: 增加唯一约束和 UPSERT**

部署迁移前先查询重复数据；每个 `(scan_date, code)` 保留最新 `created_at` 的一条，再建立：

```sql
CREATE UNIQUE INDEX IF NOT EXISTS uq_scan_results_date_code
ON schema_stockfilter.scan_results(scan_date, code);
```

写入改为 `ON CONFLICT (scan_date, code) DO UPDATE SET ...`，覆盖评分和本次扫描字段。

- [ ] **Step 6: 运行测试**

Run: `pytest tests/strategy/test_latest_pattern_selection.py tests/data/test_scan_result_upsert.py -v`

Expected: 全部 PASS。

- [ ] **Step 7: 提交本任务**

```bash
git add strategy/oversold_bounce/strategy.py data/database.py tests/strategy/test_latest_pattern_selection.py tests/data/test_scan_result_upsert.py
git commit -m "fix: select latest pattern and upsert scan results"
```

**验收标准：**

- 完整 120 日数据和末尾 60 日窗口对同一股票的近期形态结论一致。
- 同日同股重扫后数据库始终只有一条记录。
- 现有重复数据清理前后总信号日和去重股票数有审计记录。

---

### Task 6: 统一股票池过滤和推送幂等

**Files:**
- Create: `tests/data/test_stock_pool_filters.py`
- Create: `tests/notification/test_push_idempotency.py`
- Modify: `data/database.py:214-231,400-435`
- Modify: `scripts/daily_scan.py:70-99`
- Modify: `scripts/scheduler_obpc.py:243-255`

- [ ] **Step 1: 写股票池一致性测试**

```python
def test_database_fallback_applies_all_mainboard_filters(database, mixed_stock_rows):
    database.insert_stocks(mixed_stock_rows)
    result = database.get_stock_list({
        "exclude_st": True,
        "exclude_delisting": True,
        "exclude_beijing": True,
        "exclude_star_market": True,
        "exclude_chi_next": True,
        "exclude_new_third_board": True,
        "exclude_index": True,
        "exclude_new_stocks": True,
        "new_stock_cutoff_date": "2025-01-01",
        "main_board_only": True,
    })
    assert set(result["code"]) == {"000001", "600000"}
```

- [ ] **Step 2: 实现参数化 SQL 过滤**

不得将配置值直接拼入 SQL。板块前缀、名称规则和上市日期使用固定 SQL 片段与参数列表；固定 CSV 和数据库回退路径都调用同一个 `filter_stock_pool(df, config)` 纯函数作为最终防线。

- [ ] **Step 3: 写持久化推送去重测试**

```python
def test_restart_does_not_push_same_signal_date_twice(database):
    database.save_push_history("__daily_summary__", "2026-10-16", "obpc_summary")
    assert database.has_pushed("__daily_summary__", "2026-10-16", "obpc_summary") is True
```

- [ ] **Step 4: 用数据库替代 `last_pushed_signal_date`**

推送前查询 `push_history`，成功发送后再写入；失败发送不得写入成功记录。进程内变量可保留作为减少查询的优化，但不能作为幂等真相源。

- [ ] **Step 5: 运行测试**

Run: `pytest tests/data/test_stock_pool_filters.py tests/notification/test_push_idempotency.py -v`

Expected: 全部 PASS。

- [ ] **Step 6: 提交本任务**

```bash
git add data/database.py scripts/daily_scan.py scripts/scheduler_obpc.py tests/data/test_stock_pool_filters.py tests/notification/test_push_idempotency.py
git commit -m "fix: unify stock filters and persist push idempotency"
```

**验收标准：**

- 固定 CSV 与数据库回退对同一输入得到相同代码集合。
- 创业板、科创板被排除时，通知和运行记录明确显示扫描范围为主板。
- 容器重启后不会重复推送同一目标交易日摘要。

---

### Task 7: 回测并统一市场风控配置

**Files:**
- Create: `tests/risk/test_market_policy_config.py`
- Modify: `config/config.yaml:121-168`
- Modify: `scripts/backtest_obpc.py`
- Modify: `docs/knowledge/decisions/stockfilter_v3-20261013-obpc-index-filter-back-to-ma-position.md`
- Create: `docs/reports/obpc-risk-policy-backtest-2026-10.md`

- [ ] **Step 1: 写配置与已采纳决策一致性测试**

```python
import yaml


def test_adopted_market_filter_is_ma_position():
    with open("config/config.yaml", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    index_filter = config["strategies"]["oversold_bounce"]["params"]["index_filter"]
    assert index_filter["method"] == "ma_position"
```

- [ ] **Step 2: 恢复已经采纳的 `ma_position` 配置**

本步骤只消除文档与生产配置漂移，不声称能让 10 月 16 日产生最终信号；当日沪深300低于 MA20，恢复后仍会被拦截。

- [ ] **Step 3: 扩展本地回测方案**

在现有四个方案之外增加两个可比较方案，配置必须通过 `dataclasses.replace` 注入，禁止直接改生产配置：

```python
5: {
    "description": "ma_position 硬过滤 + 弱势评分调整",
    "config": {
        "index_filter_method": "ma_position",
        "slope_filter_enabled": False,
        "score_adjustment_enabled": True,
    },
},
6: {
    "description": "不做市场硬过滤，仅使用评分阈值与评分调整",
    "config": {
        "index_filter_enabled": False,
        "slope_filter_enabled": False,
        "score_adjustment_enabled": True,
    },
},
```

- [ ] **Step 4: 在本地运行全历史回测**

Run: `python scripts/backtest_obpc.py --scheme 1`

依次运行方案 1–6；禁止在生产服务器运行。报告必须至少记录：总信号数、年均信号、胜率、平均收益、最低收益、硬止损占比、按月过滤原因。

- [ ] **Step 5: 按既有业务验收线选择方案**

候选方案必须同时满足现有需求文档中的标准：

- 整体胜率 ≥ 60%。
- 整体平均收益 ≥ 2%。
- 硬止损笔数占比 ≤ 20%。
- 总信号数 ≥ 20。
- 2026 年 1–3 月信号数 ≤ 15。

如果没有方案全部满足，保持 `ma_position` 已采纳基线，不放宽生产风控，并在报告中记录失败指标；不得为了增加信号数单独修改极弱市场乘数。

- [ ] **Step 6: 更新决策记录**

报告必须包含输入数据日期范围、复权方式、交易成本、配置快照、各方案结果和最终选择。决策文档必须明确：

- 用哪个指数；
- 哪个条件是硬过滤；
- 哪些条件只调整评分；
- `extreme_weak` 是否归零及其回测依据；
- 股票池是否只含主板。

- [ ] **Step 7: 运行配置测试**

Run: `pytest tests/risk/test_market_policy_config.py -v`

Expected: PASS。

- [ ] **Step 8: 提交本任务**

```bash
git add config/config.yaml scripts/backtest_obpc.py tests/risk/test_market_policy_config.py docs/knowledge/decisions/stockfilter_v3-20261013-obpc-index-filter-back-to-ma-position.md docs/reports/obpc-risk-policy-backtest-2026-10.md
git commit -m "docs: align OBPC market policy with backtest evidence"
```

**验收标准：**

- 生产配置与有效决策文档完全一致。
- 参数变化都有本地回测报告，不存在无依据硬编码。
- 回测未达标时不得以“最近信号少”为由放宽生产风控。
- 10 月 16 日可解释为“原始候选 10，最终因市场规则为 0”。

---

### Task 8: 修复仓位归一化、镜像清理和数据库密码默认值

**Files:**
- Create: `tests/advisor/test_position_normalization.py`
- Modify: `distill_changying/scripts/advisor/advisor_llm.py:291-326`
- Modify: `.github/workflows/deploy.yml:167-170`
- Modify: `data/database.py:34-45`
- Modify: `docker-compose.yml:19-24,54-59`
- Modify: `.env.example`

- [ ] **Step 1: 写仓位边界失败测试**

```python
from distill_changying.scripts.advisor.advisor_llm import _validate_and_normalize


def test_normalization_keeps_sum_and_bounds(advisor_config):
    result = _validate_and_normalize(
        {"position_advice": {"stock_pct": 5, "bond_pct": 10, "cash_pct": 60}},
        advisor_config,
    )
    position = result["position_advice"]
    assert sum(position.values()) == 100
    assert 5 <= position["stock_pct"] <= 80
    assert 10 <= position["bond_pct"] <= 50
    assert 10 <= position["cash_pct"] <= 60
```

- [ ] **Step 2: 实现有界总和归一化并二次校验**

算法每轮只把剩余差额分配给尚未触及上下界的类别；结束后同时断言总和为 100 且三项均在范围内，无法满足时返回 `None` 并记录错误，禁止输出越界结果。

- [ ] **Step 3: 限定镜像清理范围**

将：

```bash
docker image prune -af || true
```

改为：

```bash
docker image prune -f || true
```

并把日志文字改为“清理悬空镜像”。

- [ ] **Step 4: 移除数据库密码弱默认值**

```python
self.password = os.getenv("DB_PASSWORD")
if not self.password:
    raise RuntimeError("必须通过环境变量 DB_PASSWORD 配置数据库密码")
```

Compose 使用 `${DB_PASSWORD:?DB_PASSWORD must be set}`，`.env.example` 只保留空占位和配置说明，不提供可工作的固定口令。

- [ ] **Step 5: 运行测试和静态验证**

Run: `pytest tests/advisor/test_position_normalization.py -v`

Expected: PASS。

Run: `python -c 'import yaml; yaml.safe_load(open("docker-compose.yml"))'`

Expected: 退出码 0。

Run: `bash -n scripts/monitor.sh`

Expected: 退出码 0。

- [ ] **Step 6: 提交本任务**

```bash
git add distill_changying/scripts/advisor/advisor_llm.py .github/workflows/deploy.yml data/database.py docker-compose.yml .env.example tests/advisor/test_position_normalization.py
git commit -m "fix: harden allocation validation and deployment defaults"
```

**验收标准：**

- 仓位比例总和始终为 100，且各项不越界。
- 部署后仍保留有标签、未被运行容器使用的上一版本镜像。
- 未设置 `DB_PASSWORD` 时启动明确失败；设置后正常连接。

---

### Task 9: 完成端到端验收、监控和文档同步

**Files:**
- Create: `tests/integration/test_obpc_daily_pipeline.py`
- Create: `scripts/verify_obpc_pipeline.py`
- Modify: `scripts/monitor.sh`
- Modify: `docs/designs/OBPC策略说明文档.md`
- Modify: `docs/designs/配置管理设计.md`
- Modify: `docs/designs/数据库设计.md`
- Modify: `docs/README.md`

- [ ] **Step 1: 写四条端到端场景测试**

```python
def test_incomplete_data_blocks_scan_and_sends_data_warning(pipeline):
    result = pipeline.run_fixture("incomplete_1059_of_2911")
    assert result.scan_status == "blocked_data"
    assert result.scanned_count == 0
    assert "数据不完整" in result.message


def test_market_block_preserves_raw_candidate_count(pipeline):
    result = pipeline.run_fixture("2026-10-16_complete")
    assert result.scan_status == "blocked_market"
    assert result.raw_candidate_count == 10
    assert result.selected_count == 0


def test_completed_zero_is_truthful(pipeline):
    result = pipeline.run_fixture("complete_no_pattern")
    assert result.scan_status == "completed_zero"
    assert "完整扫描后未发现形态" in result.message


def test_saturday_backfill_rescans_friday_once(pipeline):
    first = pipeline.run_rescan("2026-10-17", "2026-10-16")
    second = pipeline.run_rescan("2026-10-17", "2026-10-16")
    assert first.executed is True
    assert second.executed is False
```

- [ ] **Step 2: 增加只读验收脚本**

`scripts/verify_obpc_pipeline.py --signal-date YYYY-MM-DD` 输出 JSON，至少包含：

```json
{
  "signal_date": "2026-10-16",
  "stock_pool_count": 2911,
  "kline_covered_count": 2910,
  "coverage_rate": 0.9997,
  "task_status": "completed",
  "scan_status": "blocked_market",
  "scanned_count": 2911,
  "raw_candidate_count": 10,
  "selected_count": 0,
  "filter_reason": "...",
  "push_recorded": true
}
```

脚本只能执行 SELECT，不得更新生产数据库。

- [ ] **Step 3: 扩展监控告警**

`scripts/monitor.sh` 必须告警以下情况：

- 最近交易日覆盖率低于配置阈值；
- `task_status.status != completed`；
- 最近交易日没有 `scan_runs`；
- `scan_runs` 长时间停留在 `running`；
- 扫描失败或数据阻断；
- 推送记录缺失。

- [ ] **Step 4: 运行完整自动化验证**

Run: `pytest -v`

Expected: 全部 PASS，0 failed。

Run: `PYTHONPYCACHEPREFIX=/private/tmp/stockfilter-pycache python3 -m compileall -q main.py data scripts strategy distill_changying/scripts`

Expected: 退出码 0。

Run: `python -c 'from pathlib import Path; import yaml; [yaml.safe_load(p.read_text()) for p in list(Path("config").rglob("*.yaml")) + list(Path(".github").rglob("*.yml"))]'`

Expected: 退出码 0。

Run: `bash -n scripts/monitor.sh`

Expected: 退出码 0。

- [ ] **Step 5: 在隔离环境做 10 月 16 日验收重放**

使用生产数据的本地导出副本运行，禁止在生产服务器回测或参数优化。期望：

```text
stock_pool_count=2911
kline_covered_count>=2853
scan_status=blocked_market
scanned_count=2911
raw_candidate_count=10
selected_count=0
```

其中 `kline_covered_count>=2853` 对应配置的 98% 覆盖红线；实际完整副本预期约 2910。

- [ ] **Step 6: 更新文档**

文档必须明确区分：

- “完整扫描后零形态”；
- “存在形态但被市场风控过滤”；
- “数据不完整未扫描”；
- “任务异常”；
- 股票池只含主板，不覆盖创业板和科创板。

- [ ] **Step 7: 提交本任务**

```bash
git add tests/integration/test_obpc_daily_pipeline.py scripts/verify_obpc_pipeline.py scripts/monitor.sh docs/designs/OBPC策略说明文档.md docs/designs/配置管理设计.md docs/designs/数据库设计.md docs/README.md
git commit -m "test: add OBPC pipeline acceptance coverage"
```

**验收标准：**

- 全部自动化测试通过。
- 10 月 16 日历史重放不再显示“无形态”，而是准确说明原始候选和风控原因。
- 任何数据缺失或任务失败都能在下一次推送前被监控发现。
- 验收脚本只读、可重复运行、输出结构化 JSON。

## 6. 总体验收门槛

以下条件必须全部满足，才可声明整改完成：

### 6.1 数据可靠性

- 处理覆盖率低于 98% 时扫描被阻断。
- 目标交易日 K 线覆盖、任务目标日期、任务成功状态三者一致。
- 熔断后未遍历股票计入未处理数量。
- 周末补齐前一交易日数据后能够重扫一次。

### 6.2 扫描正确性

- 扫描记录包含股票池数、实际扫描数、原始候选数、最终信号数和过滤原因。
- 策略返回最新有效形态，不被旧形态遮蔽。
- 同日同股重扫保持数据库幂等。
- 固定文件与数据库回退股票池过滤结果一致。

### 6.3 通知准确性

- 文件缺失、数据不足和任务失败均不能发送“无符合形态”。
- 市场风控拦截必须说明“原始候选数”和具体规则。
- 只有完整扫描且原始候选为零时才发送“完整扫描后未发现形态”。
- 容器重启后不重复推送同一交易日结果。

### 6.4 风控治理

- 配置和有效决策文档一致。
- 所有策略阈值来自配置或可追溯回测结论。
- 风控放宽方案必须在本地达到既有业务验收线。
- 生产服务器不运行回测或参数优化。

### 6.5 工程质量

- `pytest -v`：0 failed。
- Python compileall、YAML 解析、Shell 语法检查全部通过。
- 数据库迁移幂等，可重复执行。
- 不包含固定生产密码。
- 部署清理不删除带标签的回滚镜像。

## 7. 发布顺序与回滚

### 7.1 推荐发布顺序

1. 先部署数据库向后兼容迁移：`task_status` 新字段、`scan_runs` 表、扫描结果唯一约束。
2. 部署 K 线更新器和 OBPC 调度器，但暂不调整市场策略参数。
3. 观察至少 2 个交易日的数据覆盖率、扫描状态和通知语义。
4. 部署最新形态选择、股票池一致性和推送幂等修复。
5. 本地回测验收后，独立部署已审批的市场风控配置。
6. 最后部署 E-advisor、密码默认值和镜像清理整改。

### 7.2 回滚策略

- 数据库新增表和新增列保留，不做破坏性回滚；旧版本会忽略这些字段。
- 应用镜像保留上一稳定标签，不使用 `docker image prune -af`。
- 风控配置单独版本化，可恢复到已验证的 `ma_position` 基线。
- 若新通知逻辑异常，允许暂时停止摘要推送，但不得回退到把缺失文件解释为零信号。

## 8. 监控观察窗口

上线后连续观察至少 5 个交易日，逐日记录：

| 指标 | 正常范围/期望 |
|---|---|
| 股票池数量 | 与配置股票池一致，变化有原因 |
| K 线处理覆盖率 | ≥ 98% |
| 目标日 K 线覆盖率 | ≥ 98% |
| 扫描股票数 | 等于当日有效股票池数 |
| `scan_runs` 最终状态 | 不得长期停留 `running` |
| 原始候选数/最终信号数 | 均有记录，允许为 0 |
| 推送次数 | 每个信号日期最多 1 次摘要 |
| 错误表述 | 0 次“失败/缺失却称零信号” |

若 5 个交易日内再次出现覆盖率不足、漏扫或错误通知，视为验收失败，必须回到对应任务补充复现测试后再发布。

---

## 9. A-review 修订记录（GA 前，2026-10-18）

复核者：design-review-local（GLM-5.3），独立于 A 段设计。以下三项阻塞项由 A 段（主对话，实际模型 DeepSeek-V4.1-Flash）给出设计决定。**B 段实现以本节为准；本节与正文冲突时以本节为准。**

### 9.1 B-1：Task 5 测试与真实接口对齐

已修正 Task 5 Step 1 片段：真实签名为 `analyze(code: str, data: Dict[str, pd.DataFrame]) -> Optional[Signal]`，回踩日在 `Signal.detail['retrace_date']`（字符串）。见 [strategy.py](file:///Users/yl/vscode/stockfilter_v3/strategy/oversold_bounce/strategy.py#L85-L147)。

### 9.2 B-2：目标交易日覆盖率纳入阻断判定（R-01 第二支柱）

不允许把该指标留给验收脚本观测，必须由 `is_complete` 阻断：

- `UpdateResult` 增加字段 `target_date: Optional[str]`、`target_date_success: int`（`total` 为股票池基数）。
- 新增属性：
  - `processed_count = success + failed + skipped`（熔断后未遍历股票计入分母）。
  - `coverage_rate = processed_count / total`（`total <= 0` 返回 0.0）。
  - `target_date_coverage_rate = target_date_success / total`（`total <= 0` 返回 0.0）。
  - 保留既有 `total_processed = success + failed`（供 `success_rate` 使用）；与 `processed_count` 语义不同，**不得合并**。
- `is_complete(min_success_rate, total_failure_limit, min_coverage_rate, target_date)` 判定顺序：
  1. `total <= 0` 或 `coverage_rate < min_coverage_rate` → `False`。
  2. 若 `target_date` 为交易日（`utils.trade_calendar.is_trading_day`）且 `target_date_coverage_rate < min_coverage_rate` → `False`。**该条必须在休市日 guard 之前，禁止被 `skipped_ratio >= 0.95` 短路绕过。**
  3. 仅当 `target_date` 非交易日时，保留 `skipped_ratio >= 0.95` 的休市日 guard → `True`。
  4. 其余按 `success_rate >= min_success_rate and failed <= total_failure_limit`。
- 阈值只使用既有新键 `global.kline_update.min_coverage_rate`（0.98），处理覆盖率与目标日覆盖率共用，不新增第二个阈值键。
- `target_date_success` 的计算来源：`DailyKlineUpdater.update_all_klines()` 以本次目标交易日统计数据库中该日已有/本次写入的股票数，复用现有 per-stock 循环结果，不新增远端请求。
- 必补测试：**交易日 + 全部 skipped 必须 FAIL**；**非交易日 + 全部 skipped 必须 PASS**。

### 9.3 B-3：scan_runs 状态机闭环

1. **卡死收敛**：`running` 仅表示扫描进程存活。`scheduler_obpc` 在启动任何扫描（22:15 初扫 / 07:30 重扫）之前，先调用 `db.mark_stale_scan_runs_failed(stale_after_seconds=SCAN_TIMEOUT)`，把 `started_at < now - SCAN_TIMEOUT` 且仍为 `running` 的行置为 `failed`，`filter_reason='stale_running_timeout'`。`daily_scan.py` 用 `try/except/finally` 在进程存活时写终态；被 SIGKILL 的进程由下一次调度收敛。`monitor.sh` 对超过阈值的 `running` 行告警。
2. **通知函数六态全覆盖且不抛异常**：签名改为 `build_scan_summary_message(signal_date: str, run: dict | None) -> Optional[str]`。
   - `completed_with_signals` → 返回 `None`（交由信号卡片流程，主流程不再发摘要）。
   - `running` → 返回异常语义文本（“扫描尚未结束或未收敛，请检查调度”），**不抛异常**。
   - 未知状态 → 返回“扫描状态异常（<status>），请检查日志”，**不抛异常**。
   - `None` / `completed_zero` / `blocked_market` / `blocked_data` / `failed` 按正文 Task 4 Step 6 文案。
   - `feishu_push.main()` 只调用该函数，删除“信号文件不存在即无信号”分支；卡片 `send_card_message([])` 的 else 分支文案同样不得表述为“今日无符合形态”。
3. **重扫幂等与数据版本**：`scan_runs` 增加 `data_version TIMESTAMPTZ`（记录本次运行依据的 backfill/kline 任务的 `last_completed_at`）。
   写入统一使用 `INSERT ... ON CONFLICT (signal_date, run_kind) DO UPDATE SET ...`（唯一键 `(signal_date, run_kind)`），`run_kind ∈ {initial, rescan}`，初扫与重扫互不覆盖，同一 `(signal_date, run_kind)` 重复运行幂等更新。
   `already_scanned` 判定：存在同 `signal_date`、`run_kind='rescan'`、`status ∈ {completed_with_signals, completed_zero, blocked_market}` 且 `data_version >= 当前 backfill 完成时间` 的记录 → 该数据版本已重扫，不再重扫；`failed` / `blocked_data` 允许重试。

### 9.4 非阻塞项处理

- **N-1 时间戳**：新增列统一用 `TIMESTAMPTZ`；既有 `task_status.last_completed_at` 保持无时区列 + 按 UTC 解析的既有约定（[scheduler_obpc.py](file:///Users/yl/vscode/stockfilter_v3/scripts/scheduler_obpc.py#L70-L78)），在 `docs/designs/数据库设计.md` 写明该约定并补集成测试。
- **N-2“最新有效形态”定义**：反向遍历取最新完整形态候选；若其 `retrace_date` 落在 `daily_scan.py` 信号窗口之外，继续向更早候选评估，直到命中窗口内候选或耗尽，耗尽则不出信号。Task 5 补该窗口回退测试。
- **N-3 推送原子性**：固定顺序 `has_pushed` 查询 → 发送 → 成功后写 `push_history`；发送失败不写记录（允许下次重试）；发送成功但写库失败只记错误日志并由监控暴露，残余“极小概率重复推送”风险显式记录。
- **N-4 测试环境**：`tests/conftest.py` 提供 `database` fixture，经 `TEST_DB_HOST/PORT/NAME/USER/PASSWORD` 连接本地 PostgreSQL 测试库；不可用时 `pytest.skip`，不得静默通过。Task 9 的 `2026-10-16_complete` 重放使用本地导出副本（`data/backtest/klines_mainboard.csv`、`data/backtest/stocks_mainboard.csv`），禁止连生产库。

**文档版本：** V1.0  
**编制日期：** 2026-10-18  
**依据：** 2026-10-15 至 2026-10-17 生产日志与数据库只读诊断、2026-10-16 完整数据重放、open-code-review-delegate 全仓代码审查。
