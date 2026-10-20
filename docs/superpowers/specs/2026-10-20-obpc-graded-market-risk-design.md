# OBPC 分级市场风控与弱市仓位设计

**状态：** 已确认设计方向，等待开发实施  
**日期：** 2026-10-20  
**适用策略：** `oversold_bounce`（OBPC）

## 1. 背景与结论

OBPC 是“大跌 → 缩量 → 放量 → 回踩”的反转策略，候选信号天然更容易集中在指数回落或修复初期。现行生产配置却要求沪深300同时满足：站上 MA20、站上 MA60、MA60 五日斜率大于 0.2%，并继续叠加弱市评分阈值和极弱市场评分归零。按本地沪深300数据（2025-07-31 至 2026-10-16）计算，三个硬条件同时通过的交易日仅 37.3%，意味着 62.7% 的交易日在个股判断前已被整体禁止开仓。

本次设计不删除市场风控，而是把“弱市一律禁止”改为“按市场状态决定是否允许、建议仓位、每日名额和评分门槛”。极弱市场仍硬阻断；普通弱市允许具备个股相对强度的高分候选以较低建议仓位进入观察/推送；强市保持正常行为。

## 2. 目标与非目标

### 2.1 目标

1. 保留 OBPC 的左侧反转属性，避免 MA20、MA60、斜率、评分重复惩罚。
2. 市场状态只计算一次，输出统一的 `MarketRegimeDecision`，供扫描、回测和通知共同消费。
3. 弱市候选必须同时满足更高质量门槛和个股相对强度门槛，且降低建议仓位及每日数量。
4. 极弱市场继续阻断真实买入信号，但保留原始候选和观察名单可观测性。
5. 所有阈值来自配置和本地回测决策，不在代码中写死生产风险参数。
6. 上线前保持 `hard_filter` 默认行为，只有回测和影子验证达标后才能切换 `graded`。

### 2.2 非目标

- 不修改 OBPC 四段形态识别算法。
- 不扩大创业板、科创板股票池。
- 不自动下单；仓位字段只是推送和人工执行建议。
- 不在生产服务器运行回测或参数优化。
- 不因单日 7 个候选直接确定长期参数。

## 3. 方案比较

| 方案 | 优点 | 缺点 | 结论 |
|---|---|---|---|
| 完全关闭大盘过滤 | 最大限度保留左侧信号 | 有限样本中无过滤方案胜率 16%、平均收益 -3.98%，下跌暴露过高 | 不采用 |
| 维持现有硬过滤 | 风险最低、实现不变 | 三个硬条件仅 37.3% 交易日同时通过，改变反转策略属性 | 仅保留为兼容/回滚模式 |
| 分级风控 + 弱市降仓 + 相对强度 | 兼顾左侧机会与系统性风险，行为可解释、可回测、可灰度 | 需要扩展上下文、回测和通知 | **采用** |

## 4. 市场状态与裁决模型

新增不可变结果对象：

```python
@dataclass(frozen=True)
class MarketRegimeDecision:
    regime: str                  # strong | weak | extreme_weak | unknown
    allow_entry: bool
    position_multiplier: float  # 仅建议仓位系数
    score_threshold: float
    daily_signal_limit: int
    require_relative_strength: bool
    reason_code: str
    reason: str
```

市场状态沿用现有定义，避免同时维护两套口径：

| 状态 | 判定 | 行为 |
|---|---|---|
| `strong` | 收盘价 ≥ MA20 且 MA20 五日斜率 > 0 | 放行；正常建议仓位；正常每日名额 |
| `weak` | 非 `strong` 且非 `extreme_weak` | 不因 MA20/MA60位置直接硬拦截；要求高分和个股相对强度；降低建议仓位和每日名额 |
| `extreme_weak` | 收盘价 < MA60 且 MA60 五日斜率 ≤ 0 | 硬阻断买入信号；仍记录原始候选与观察名单 |
| `unknown` | 指数数据不足或不新鲜 | 沿用 `freshness_strategy`；不得静默按强市处理 |

`graded` 模式下禁止再次执行 MA20、MA60和斜率三重硬过滤。市场状态已包含这些信息，重复执行会造成双重惩罚。`hard_filter` 模式保持现有路径，作为回滚开关。

## 5. 个股相对强度

相对强度只用于 `weak` 状态的候选资格，不用于 `extreme_weak` 绕过：

```text
stock_return_N = stock_close_t / stock_close_t-N - 1
index_return_N = index_close_t / index_close_t-N - 1
relative_strength_N = stock_return_N - index_return_N
```

至少支持一个可配置回看周期；第一轮实验同时测试 20 日和 60 日，不在设计阶段指定唯一生产阈值。数据不足时不得假定相对强度通过，应记录 `relative_strength_unavailable` 并按配置决定阻断或仅进入观察名单。

所有计算只能使用信号日及以前的数据。信号于 T 日收盘后生成，交易假设从 T+1 开始，防止未来信息泄漏。

## 6. 仓位与信号语义

现有文档的标准单只仓位为总资金 10%。新字段使用乘数而不是直接金额：

```text
recommended_position_pct = base_position_pct × position_multiplier
```

`base_position_pct`、各市场状态的 `position_multiplier` 和 `daily_signal_limit` 全部由配置提供。Python 不得为这些风险参数提供隐式默认值；缺少新配置时只能进入调用现有规则的 `hard_filter` 兼容路径。新的弱市数值只能由回测决策文档写入配置。

信号 JSON 和飞书卡片新增：市场状态、原始评分、最终评分、相对强度、建议仓位系数、建议仓位比例、风控原因。观察名单不得使用“买入信号”措辞。

## 7. 配置设计

在 `strategies.oversold_bounce.params` 下新增：

```yaml
market_regime_policy:
  mode: hard_filter
  base_position_pct: 0.10
  weak:
    allow_entry: false
    position_multiplier: 0.0
    score_threshold: 75
    daily_signal_limit: 0
    require_relative_strength: true
    relative_strength_lookback: 20
    relative_strength_min: 0.0
  extreme_weak:
    allow_entry: false
    position_multiplier: 0.0
    daily_signal_limit: 0
```

初始提交必须以 `mode: hard_filter` 保持兼容。上面的弱市参数是结构示例，不是获准上线的策略值。开发完成后通过实验矩阵选择参数，再以独立配置变更和独立部署授权切换 `mode: graded`。

## 8. 数据与可观测性

不新增数据库列。复用 `scan_runs.data_metrics` JSONB 保存：

- `market_regime`
- `market_reason_code`
- `raw_candidate_count`
- `weak_eligible_count`
- `relative_strength_pass_count`
- `selected_count`
- `position_multiplier`
- 各过滤原因计数

这避免无必要的数据库迁移，同时让验收脚本和监控脚本能够区分“极弱硬阻断”“弱市相对强度不足”“额度截断”和“真实零候选”。

## 9. 回测与决策

开发智能体必须在本地执行，禁止在生产服务器执行。实验至少包含：

1. 当前 `hard_filter` 基线。
2. 完全无市场过滤基线。
3. `graded`：弱市不同仓位乘数。
4. `graded`：20 日与 60 日相对强度。
5. `graded`：弱市评分阈值和每日名额组合。

必须采用时间序列走样本或滚动窗口，不能只在同一数据窗口挑最优参数。除胜率、平均收益外，必须报告最大回撤、盈亏比、交易数、弱市交易数、极弱市误放行数、年度稳定性和相对基线增量。

生产启用 `graded` 的最低条件：

- 极弱市场买入信号数为 0。
- 弱市交易数足以评估，不接受少量交易制造的虚高胜率。
- 样本外平均收益为正。
- 样本外最大回撤不劣于 `hard_filter` 超过事先配置的容忍值。
- 相比无过滤基线，硬止损率和最大回撤显著改善。
- 参数在相邻取值下结果不发生灾难性翻转。

具体数值阈值必须在回测任务开始前写入实验配置/决策文档，不能看完结果后倒推验收线。

## 10. 失败与降级处理

- 指数数据缺失/过期：遵循现有 `freshness_strategy`，记录明确状态。
- 个股相对强度数据不足：不得自动放行；进入观察名单或阻断。
- 非法仓位乘数、评分阈值或每日名额：配置加载立即失败，不使用静默默认值。
- `graded` 模式异常：不得自动退化为无过滤；应阻断当次买入信号并告警。
- 推送失败：保持现有非零退出码和幂等机制。

## 11. 上线与回滚

1. 代码上线时保持 `mode: hard_filter`，验证零回归。
2. 本地完成全市场或代表性随机样本走样本回测。
3. 影子运行至少覆盖若干强、弱、极弱市场日，只记录不改变正式信号。
4. 形成参数决策记录，经用户明确授权后切换 `graded`。
5. 发现异常时仅需把 `mode` 切回 `hard_filter`，无需回滚数据库。

## 12. 审查重点

- 是否把生产参数硬编码进 Python。
- 是否在 `graded` 模式重复执行 MA20/MA60/斜率硬过滤。
- 是否允许相对强度绕过 `extreme_weak`。
- 是否存在使用 T+1 或未来数据计算 T 日信号。
- 回测是否真正按仓位乘数计算收益和回撤，而不只是改变显示字段。
- 通知是否把观察名单误称为买入信号。
- `hard_filter` 默认模式是否与当前生产行为一致。
