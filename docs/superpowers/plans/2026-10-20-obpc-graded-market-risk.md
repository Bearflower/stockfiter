# OBPC 分级市场风控与弱市仓位 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 OBPC 的“大盘弱则全部禁止”改造成可配置、可回测、可灰度的分级市场风控：普通弱市允许具备相对强度的高分候选以低建议仓位进入，极弱市场继续硬阻断。

**Architecture:** 在现有 `MarketContext` 与 `RiskController` 之上增加单一 `MarketRegimeDecision`，统一给出准入、评分阈值、建议仓位和每日名额，避免 MA20、MA60、斜率、评分重复惩罚。扫描、回测、持久化和飞书共同消费同一决策；数据库复用 `scan_runs.data_metrics`；默认 `hard_filter` 保持现网行为，`graded` 只有在本地回测和影子验收达标后才能单独启用。

**Tech Stack:** Python 3、pandas、PyYAML、PostgreSQL JSONB、pytest、Docker Compose、飞书 Webhook。

---

## 1. 开发前必须确认的事实

| 项目 | 当前事实 | 实施约束 |
|---|---|---|
| 策略性质 | OBPC 是左侧反转/回踩策略 | 不能用趋势过滤把策略完全改成右侧追涨 |
| 生产硬门槛 | MA20 + MA60 + MA60斜率 > 0.2% | `graded` 模式必须停止重复硬过滤 |
| 交易日通过率 | 三个硬条件同时满足约 37.3% | 需报告过滤前后信号，不得只报最终 0 |
| 极弱定义 | 跌破 MA60 且 MA60 斜率 ≤ 0 | 相对强度不能绕过极弱硬阻断 |
| 现有仓位 | 文档标准单只仓位 10% | 新功能输出建议乘数，不自动下单 |
| 运行记录 | `scan_runs.data_metrics` 已是 JSONB | 不新增数据库列 |
| 回测约束 | 禁止在生产服务器回测 | 数据只可导出，本地执行全部实验 |
| 发布权限 | 开发、Git、部署相互独立 | 本计划不授权提交、推送或部署 |

## 2. 问题、修改点与解决办法

| ID | 问题 | 修改点 | 解决办法 | 主要风险 |
|---|---|---|---|---|
| G-01 | 多个市场规则重复惩罚 | `risk_control.py` | 引入统一 `MarketRegimeDecision` | 新旧模式行为串线 |
| G-02 | 弱市全部候选同时被拒绝 | 配置 + `daily_scan.py` | 弱市改为高分、相对强度、低仓位、低名额 | 放松过度导致接飞刀 |
| G-03 | 极弱市场评分归零与硬过滤语义混乱 | `risk_control.py` | 极弱直接输出明确硬阻断，不再靠乘零间接实现 | 通知状态误判 |
| G-04 | 缺少个股相对大盘强度 | 市场上下文 + 扫描 | 计算 N 日超额收益，弱市作为准入条件 | 数据不足、未来函数 |
| G-05 | 仓位建议没有结构化字段 | 信号 JSON + 推送 | 保存基准仓位、乘数和最终建议比例 | 被误解为自动下单 |
| G-06 | 回测只比较信号，不验证仓位效果 | `backtest_obpc.py` | 按建议仓位计算收益、回撤和暴露 | 指标口径不一致 |
| G-07 | 生产参数容易凭感觉选择 | 配置 + 决策文档 | 先锁验收线，再跑走样本实验 | 过拟合/结果倒推 |
| G-08 | 上线后难区分各类过滤 | `scan_runs.data_metrics` + 监控 | 持久化市场状态和各原因计数 | JSON 字段漂移 |

## 3. 文件变更地图

| 文件 | 动作 | 职责 |
|---|---|---|
| `strategy/oversold_bounce/risk_control.py` | 修改 | 配置解析、状态裁决、相对强度值对象、兼容旧模式 |
| `config/config.yaml` | 修改 | 增加 `market_regime_policy`，默认 `hard_filter` |
| `scripts/daily_scan.py` | 修改 | 计算个股相对强度、应用统一裁决、写入仓位与指标 |
| `scripts/backtest_obpc.py` | 修改 | 增加分级风控实验、仓位加权指标和逐笔审计输出 |
| `scripts/feishu_push.py` | 修改 | 展示市场状态、相对强度与建议仓位，区分观察名单 |
| `scripts/verify_obpc_pipeline.py` | 修改 | 验证 `data_metrics` 新字段及状态一致性 |
| `scripts/monitor.sh` | 修改 | 增加配置模式、极弱误放行、字段缺失告警 |
| `tests/risk/test_market_regime_policy.py` | 新增 | 市场状态与统一裁决单元测试 |
| `tests/scan/test_daily_scan_graded_policy.py` | 新增 | 扫描集成与相对强度测试 |
| `tests/backtest/test_obpc_graded_policy.py` | 新增 | 回测仓位和未来函数防护测试 |
| `tests/notification/test_feishu_status_message.py` | 修改 | 推送语义测试 |
| `tests/integration/test_obpc_daily_pipeline.py` | 修改 | `scan_runs.data_metrics` 端到端契约 |
| `docs/designs/OBPC策略说明文档.md` | 修改 | 用户可见策略和仓位语义 |
| `docs/designs/配置管理设计.md` | 修改 | 新配置键、校验和回滚方式 |
| `docs/knowledge/decisions/stockfilter_v3-20261020-obpc-graded-market-risk.md` | 新增 | 回测参数和生产启用决策记录 |

## 4. 实施任务

### Task 1：建立配置契约与向后兼容门

**Files:**
- Modify: `strategy/oversold_bounce/risk_control.py`
- Modify: `config/config.yaml`
- Test: `tests/risk/test_market_regime_policy.py`

- [ ] **Step 1：先写配置失败测试**

覆盖以下断言：`mode` 只能是 `hard_filter|graded|shadow`；仓位乘数必须在 `[0, 1]`；基础仓位必须在 `(0, 1]`；每日名额不得为负；相对强度回看周期必须为正整数；`graded` 缺少弱市配置时立即报错。

```python
def test_market_regime_policy_rejects_invalid_position_multiplier():
    params = make_params(position_multiplier=1.1)
    with pytest.raises(ValueError, match="position_multiplier"):
        RiskControlConfig.from_params(params)


def test_default_mode_preserves_hard_filter():
    cfg = RiskControlConfig.from_params({})
    assert cfg.market_regime_mode == "hard_filter"
```

- [ ] **Step 2：运行测试并确认先失败**

Run: `python3 -m pytest tests/risk/test_market_regime_policy.py -q`  
Expected: FAIL，提示新配置字段或校验尚不存在。

- [ ] **Step 3：实现配置对象**

新增 `MarketRegimePolicyConfig`，由 `RiskControlConfig.from_params()` 统一构造。除兼容模式名外不在 Python 中提供风险参数默认值；仓位、阈值、名额和回看周期必须来自配置。旧配置缺少新分节时，`from_params()` 应构造只调用现有逻辑的 `hard_filter` 兼容对象，且不得凭代码默认值启用分级行为。

```python
@dataclass(frozen=True)
class MarketRegimePolicyConfig:
    mode: str
    base_position_pct: float
    weak_allow_entry: bool
    weak_position_multiplier: float
    weak_score_threshold: float
    weak_daily_signal_limit: int
    weak_require_relative_strength: bool
    relative_strength_lookback: int
    relative_strength_min: float
    extreme_weak_allow_entry: bool
```

- [ ] **Step 4：增加兼容配置**

在 `config/config.yaml` 写入完整结构，但保持 `mode: hard_filter`。不得在本任务中切换生产行为。

- [ ] **Step 5：运行配置测试**

Run: `python3 -m pytest tests/risk/test_market_regime_policy.py tests/risk/test_market_policy_config.py -q`  
Expected: PASS，旧配置测试无回归。

- [ ] **Step 6：形成独立提交**

```bash
git add strategy/oversold_bounce/risk_control.py config/config.yaml tests/risk/test_market_regime_policy.py
git commit -m "feat: add graded market policy configuration"
```

### Task 2：实现单一市场状态裁决

**Files:**
- Modify: `strategy/oversold_bounce/risk_control.py`
- Test: `tests/risk/test_market_regime_policy.py`

- [ ] **Step 1：写强、弱、极弱、未知四类失败测试**

```python
@pytest.mark.parametrize(
    "regime,allow,multiplier",
    [
        ("strong", True, 1.0),
        ("weak", True, 0.5),
        ("extreme_weak", False, 0.0),
    ],
)
def test_graded_decision_has_one_authoritative_result(regime, allow, multiplier):
    controller = make_graded_controller(weak_multiplier=0.5)
    decision = controller.decide_market_regime(make_context(regime))
    assert decision.regime == regime
    assert decision.allow_entry is allow
    assert decision.position_multiplier == multiplier
```

另写测试证明：`hard_filter` 仍调用现有 `check_market_trend()`；`graded` 不再因单独跌破 MA20 立即失败；`extreme_weak` 无论相对强度多高都禁止开仓。

- [ ] **Step 2：运行测试并确认先失败**

Run: `python3 -m pytest tests/risk/test_market_regime_policy.py -q`  
Expected: FAIL，缺少 `MarketRegimeDecision` / `decide_market_regime`。

- [ ] **Step 3：实现统一决策对象和函数**

```python
@dataclass(frozen=True)
class MarketRegimeDecision:
    regime: str
    allow_entry: bool
    position_multiplier: float
    score_threshold: float
    daily_signal_limit: int
    require_relative_strength: bool
    reason_code: str
    reason: str
```

决策顺序固定为：数据有效性 → 极弱 → 强势 → 普通弱势。`graded` 路径不得再调用 `_check_ma_position_filter()` 和 `_check_slope_filter()`。

- [ ] **Step 4：明确旧评分调整的互斥关系**

`graded` 模式由 `MarketRegimeDecision` 给出评分阈值和仓位，不再同时应用 `score_adjustment` 的强/弱乘数；配置校验应拒绝会造成双重惩罚的组合，或在日志中明确采用 `graded` 覆盖旧评分调整。优先选择“配置加载时拒绝冲突”。

- [ ] **Step 5：运行风险测试**

Run: `python3 -m pytest tests/risk -q`  
Expected: PASS。

- [ ] **Step 6：形成独立提交**

```bash
git add strategy/oversold_bounce/risk_control.py tests/risk/test_market_regime_policy.py
git commit -m "feat: centralize OBPC market regime decisions"
```

### Task 3：增加无未来函数的个股相对强度

**Files:**
- Modify: `strategy/oversold_bounce/risk_control.py`
- Modify: `scripts/daily_scan.py`
- Test: `tests/risk/test_market_regime_policy.py`
- Test: `tests/scan/test_daily_scan_graded_policy.py`

- [ ] **Step 1：写计算和边界测试**

```python
def test_relative_strength_is_stock_return_minus_index_return():
    result = calculate_relative_strength(
        stock_closes=[100.0, 110.0],
        index_closes=[100.0, 102.0],
        lookback=1,
    )
    assert result == pytest.approx(0.08)


def test_relative_strength_does_not_change_when_future_rows_are_appended():
    before = calculate_for_date(stock_df, index_df, "2026-10-16", 20)
    after = calculate_for_date(stock_df_with_future, index_df_with_future, "2026-10-16", 20)
    assert after == before
```

覆盖停牌、缺少回看日、零价格、指数日期未对齐等场景；缺失时返回结构化不可用原因，不得返回 0 冒充中性。

- [ ] **Step 2：运行测试并确认先失败**

Run: `python3 -m pytest tests/risk/test_market_regime_policy.py tests/scan/test_daily_scan_graded_policy.py -q`  
Expected: FAIL，缺少相对强度函数和扫描字段。

- [ ] **Step 3：扩展市场上下文**

`MarketContext` 增加用于相对强度的指数历史收益字段或只读价格序列接口。实时和预加载 Provider 必须使用同一计算函数，并严格裁剪到 `signal_date`。

- [ ] **Step 4：在原始候选产生后计算个股相对强度**

只有 `weak` 且 `require_relative_strength=true` 时参与准入。`strong` 不增加无必要计算；`extreme_weak` 在相对强度前直接阻断。

- [ ] **Step 5：运行测试**

Run: `python3 -m pytest tests/risk tests/scan/test_daily_scan_graded_policy.py -q`  
Expected: PASS，前缀不变性测试通过。

- [ ] **Step 6：形成独立提交**

```bash
git add strategy/oversold_bounce/risk_control.py scripts/daily_scan.py tests/risk/test_market_regime_policy.py tests/scan/test_daily_scan_graded_policy.py
git commit -m "feat: gate weak-market signals by relative strength"
```

### Task 4：让扫描输出分级仓位与可解释指标

**Files:**
- Modify: `scripts/daily_scan.py`
- Modify: `tests/scan/test_daily_scan_graded_policy.py`
- Modify: `tests/scan/test_scan_outcome.py`
- Modify: `tests/integration/test_obpc_daily_pipeline.py`

- [ ] **Step 1：写扫描行为测试**

至少覆盖：

1. 强市高分候选按正常建议仓位进入。
2. 弱市高分且相对强度达标候选按弱市乘数进入。
3. 弱市相对强度不足进入过滤计数，不进入买入信号。
4. 极弱市场原始候选保留计数，但最终信号为 0。
5. 弱市每日名额只保留最高分候选。
6. `hard_filter` 与变更前固定夹具输出完全一致。

- [ ] **Step 2：运行测试并确认先失败**

Run: `python3 -m pytest tests/scan tests/integration/test_obpc_daily_pipeline.py -q`  
Expected: FAIL，新字段和分级裁决尚未接入。

- [ ] **Step 3：扩展信号 JSON**

每个正式信号增加：

```python
{
    "market_regime": decision.regime,
    "market_reason_code": decision.reason_code,
    "raw_score": chosen.score,
    "effective_score": effective_score,
    "relative_strength": relative_strength.value,
    "relative_strength_lookback": policy.relative_strength_lookback,
    "base_position_pct": policy.base_position_pct,
    "position_multiplier": decision.position_multiplier,
    "recommended_position_pct": policy.base_position_pct * decision.position_multiplier,
}
```

- [ ] **Step 4：复用 `scan_runs.data_metrics`**

`persist_scan_outcome()` 写入统一键名：`policy_mode`、`market_regime`、`market_reason_code`、`weak_eligible_count`、`relative_strength_pass_count`、`relative_strength_filtered_count`、`extreme_weak_blocked_count`、`position_multiplier`。不修改表结构。

- [ ] **Step 5：修正状态语义**

弱市中存在原始候选但相对强度全部失败，应记录 `blocked_market`，原因使用稳定代码 `relative_strength_filtered_all`；极弱市场使用 `extreme_weak_market`。不得回退为“今日无形态”。

- [ ] **Step 6：运行扫描和集成测试**

Run: `python3 -m pytest tests/scan tests/integration/test_obpc_daily_pipeline.py -q`  
Expected: PASS。

- [ ] **Step 7：形成独立提交**

```bash
git add scripts/daily_scan.py tests/scan tests/integration/test_obpc_daily_pipeline.py
git commit -m "feat: persist graded OBPC risk outcomes"
```

### Task 5：更新飞书通知和只读验收

**Files:**
- Modify: `scripts/feishu_push.py`
- Modify: `scripts/verify_obpc_pipeline.py`
- Modify: `scripts/monitor.sh`
- Modify: `tests/notification/test_feishu_status_message.py`
- Modify: `tests/notification/test_feishu_push_exit_code.py`

- [ ] **Step 1：写通知语义测试**

```python
def test_weak_market_signal_includes_reduced_position():
    message = build_message(make_signal(regime="weak", recommended_position_pct=0.05))
    assert "弱市分级放行" in message
    assert "建议仓位：5.0%" in message


def test_extreme_weak_candidates_are_not_called_buy_signals():
    message = build_status_message(make_run(reason="extreme_weak_market", raw=7, selected=0))
    assert "发现 7 个原始候选" in message
    assert "观察名单" in message
    assert "建议买入" not in message
```

- [ ] **Step 2：运行测试并确认先失败**

Run: `python3 -m pytest tests/notification -q`  
Expected: FAIL，缺少新字段展示。

- [ ] **Step 3：实现通知与验收规则**

正式信号展示建议仓位；观察名单明确“不构成买入信号”；验收脚本校验 `graded` 时必需指标存在且数值自洽；监控脚本在极弱市场 `selected_count > 0` 时产生高优先级告警。

- [ ] **Step 4：运行通知、Shell 和验收测试**

Run: `python3 -m pytest tests/notification -q`  
Expected: PASS。  
Run: `bash -n scripts/monitor.sh`  
Expected: exit 0。  
Run: `python3 -m compileall scripts/feishu_push.py scripts/verify_obpc_pipeline.py`  
Expected: exit 0。

- [ ] **Step 5：形成独立提交**

```bash
git add scripts/feishu_push.py scripts/verify_obpc_pipeline.py scripts/monitor.sh tests/notification
git commit -m "feat: explain graded market risk in OBPC alerts"
```

### Task 6：扩展本地回测并防止结果误读

**Files:**
- Modify: `scripts/backtest_obpc.py`
- Create: `tests/backtest/test_obpc_graded_policy.py`
- Create: `docs/knowledge/decisions/stockfilter_v3-20261020-obpc-graded-market-risk.md`

- [ ] **Step 1：先在决策文档锁定实验矩阵和验收线**

在运行结果出现前写入：数据窗口、训练/验证切分、随机抽样种子或全市场口径、候选仓位乘数集合、相对强度回看周期集合、弱市评分阈值集合、每日名额集合、交易成本和验收指标。禁止先看结果再修改验收线。

- [ ] **Step 2：写回测失败测试**

覆盖：仓位乘数 0.5 时单笔收益贡献为满仓方案的一半；T 日信号只能使用 T 日及以前数据；未来行情追加不改变历史裁决；极弱市场交易数始终为 0；同一参数和随机种子结果可复现。

- [ ] **Step 3：运行测试并确认先失败**

Run: `python3 -m pytest tests/backtest/test_obpc_graded_policy.py -q`  
Expected: FAIL，分级回测尚未接入。

- [ ] **Step 4：接入与生产相同的裁决函数**

回测不得复制一套简化判断；直接消费 `MarketRegimeDecision`。报告至少输出：候选数、正式交易数、弱市交易数、胜率、平均收益、盈亏比、最大回撤、硬止损率、资金暴露、年度分布、过滤原因分布、参数稳定性。

- [ ] **Step 5：运行回测单测和冒烟实验**

Run: `python3 -m pytest tests/backtest/test_obpc_graded_policy.py -q`  
Expected: PASS。  
Run: `python3 scripts/backtest_obpc.py --data-dir data/backtest --scheme graded --start 2025-07-31 --end 2026-10-16 --max-stocks 20`  
Expected: exit 0，报告含仓位加权收益、最大回撤和市场状态分布。

- [ ] **Step 6：执行正式本地实验**

仅在本地使用导出的 CSV。若全量耗时过长，使用固定随机种子分层抽样，禁止继续使用按代码排序的 `head(300)` 作为唯一证据。每套参数必须使用同一股票集合和同一时间切分。

- [ ] **Step 7：按预先验收线作出唯一决策**

若没有方案达标，保持 `hard_filter`，不得为了增加信号强行上线。若方案达标，把唯一获选参数和淘汰理由写入决策文档；生产配置切换留给独立授权任务。

- [ ] **Step 8：形成独立提交**

```bash
git add scripts/backtest_obpc.py tests/backtest docs/knowledge/decisions/stockfilter_v3-20261020-obpc-graded-market-risk.md
git commit -m "test: backtest graded OBPC market risk policy"
```

### Task 7：文档、全量回归和影子模式验收

**Files:**
- Modify: `docs/designs/OBPC策略说明文档.md`
- Modify: `docs/designs/配置管理设计.md`
- Modify: `docs/README.md`
- Modify: all files changed in Task 1-6 if verification finds defects

- [ ] **Step 1：更新用户文档**

明确：`hard_filter`、`shadow`、`graded` 三种模式；弱市建议仓位只是人工执行参考；观察名单不等于买入信号；极弱市场不得放行；配置切换与代码部署是两个授权域。

- [ ] **Step 2：运行针对性测试**

Run: `python3 -m pytest tests/risk tests/scan tests/backtest tests/notification tests/integration -q`  
Expected: 全部 PASS，无意外 SKIP。

- [ ] **Step 3：运行全量测试和静态验证**

Run: `python3 -m pytest tests -rs -q`  
Expected: 0 FAIL；所有 SKIP 均有已知环境原因。  
Run: `python3 -m compileall strategy scripts data`  
Expected: exit 0。  
Run: `git diff --check`  
Expected: exit 0。

- [ ] **Step 4：验证 `hard_filter` 零回归**

使用固定输入夹具比较变更前后的市场裁决、候选数、最终信号和通知文本。除新增可观测字段外，业务输出必须一致。

- [ ] **Step 5：执行 `shadow` 影子运行**

影子模式同时计算旧硬过滤和新分级决策，但正式信号仍使用旧结果；`scan_runs.data_metrics` 记录两者差异。至少覆盖强、弱、极弱状态，并确认 `shadow` 不改变信号文件与推送。

- [ ] **Step 6：完成审查包**

向审查者提供：变更文件清单、配置差异、测试完整输出、回测命令与结果、参数决策文档、影子差异样本、未解决风险。不得只提供摘要截图。

- [ ] **Step 7：形成文档提交**

```bash
git add docs/designs/OBPC策略说明文档.md docs/designs/配置管理设计.md docs/README.md
git commit -m "docs: document graded OBPC market risk policy"
```

## 5. 验收标准

### 5.1 功能验收

- [ ] 默认 `hard_filter` 与现网行为一致。
- [ ] `graded` 的普通弱市不再因单独跌破 MA20 或 MA60直接全部阻断。
- [ ] 弱市候选必须同时满足配置化评分门槛和相对强度门槛。
- [ ] 极弱市场最终买入信号恒为 0，原始候选仍可审计。
- [ ] 建议仓位比例等于配置基准仓位乘以状态乘数。
- [ ] 弱市每日名额按最终评分排序后截断。
- [ ] 观察名单通知不出现“建议买入”。

### 5.2 数据与可解释性验收

- [ ] 每次扫描记录 `policy_mode`、市场状态、原因代码和各层计数。
- [ ] 原始候选数 ≥ 相对强度通过数 ≥ 最终信号数。
- [ ] `blocked_market` 能区分 `extreme_weak_market` 与 `relative_strength_filtered_all`。
- [ ] 指数或相对强度数据缺失不会静默放行。
- [ ] 验收脚本对字段缺失、极弱误放行和计数矛盾返回非零退出码。

### 5.3 回测验收

- [ ] 回测与生产复用相同市场裁决函数。
- [ ] T 日裁决不使用 T+1 及以后数据，前缀不变性测试通过。
- [ ] 交易成本、仓位乘数和资金暴露真实进入收益/回撤计算。
- [ ] 使用时间序列样本外验证，并报告训练/验证区间。
- [ ] 不再以按代码排序前 300 只作为唯一结论依据。
- [ ] 极弱市场交易数为 0。
- [ ] 未达到预先锁定验收线时保持 `hard_filter`。

### 5.4 发布验收

- [ ] 代码发布与 `graded` 配置启用分离。
- [ ] 首次发布保持 `hard_filter` 或 `shadow`。
- [ ] 切换 `graded` 必须有用户独立授权。
- [ ] 回滚只需切换配置回 `hard_filter`，无需数据库回滚。
- [ ] 生产服务器不执行回测。

## 6. 审查者清单

后续代码审查逐项检查：

1. 是否存在无依据硬编码风险参数。
2. 是否在 `graded` 中重复执行旧硬过滤或评分乘数。
3. 相对强度是否可能使用未来数据或错位交易日。
4. 极弱状态是否存在任何绕过路径。
5. 仓位乘数是否真正进入回测收益，而非只进入通知。
6. `scan_runs.data_metrics` 键名是否稳定且有测试。
7. `hard_filter` 默认模式是否零回归。
8. 回测抽样是否随机、分层、可复现。
9. 验收线是否在结果之前锁定。
10. 开发是否擅自提交、推送、部署或切换生产配置。

## 7. 明确禁止事项

- 禁止直接把弱市仓位写死为 25% 或 50%。
- 禁止只关闭 MA20 后直接上线。
- 禁止让相对强度绕过极弱市场。
- 禁止同时启用分级裁决和旧的多重评分惩罚。
- 禁止以信号数量增加作为成功标准。
- 禁止在生产服务器运行任何回测或参数搜索。
- 禁止没有样本外证据就将 `mode` 改为 `graded`。
