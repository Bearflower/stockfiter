# E大投资决策助手 V2 — ETF 操作推荐 + 交互式问答 Spec

> **变更代号**：e-advisor-etf-v2
> **前置 Spec**：`e-investment-advisor`（已全部完成，基础框架就绪）
> **核心理念变更**：LLM 从"解说员不做决策者"升级为"E大风格的投资顾问"——**可推荐具体 ETF 品种和份数**

## Why

现有 V1 交付物是"市场温度 + 仓位比例"，缺少 E大最核心的价值——**具体到品种和份数的操作建议**。E大的标志性风格是："买入 1 份 沪深300ETF（510300）"、"卖出 1 份 中证500联接"，这套"150 份资产配置 + 估值触发 + 具体 ETF 品种"的体系才是用户真正想要的。同时，交互式问答也需要从"解说不推荐"升级为"有操作感的投资咨询"。

## What Changes

- **MODIFIED** — AI 分析报告：LLM 可推荐具体 ETF 品种和买卖份数（不再限制为"不推荐具体品种"）
- **ADDED** — ETF 操作推荐引擎：基于估值分位，按 E大份数体系自动生成具体买卖建议
- **ADDED** — 150 份资产配置框架：配置层定义可投资 ETF 池、份数、估值触发区间
- **ENHANCED** — 交互式问答：从"解说不推荐"升级为"有操作感的投资咨询"，可追问具体品种
- **MODIFIED** — 飞书推送报告：从纯温度报告升级为"ETF 操作建议 + 温度"日报

## Impact

- Affected specs: `e-investment-advisor`（重构 analyze/chat 核心逻辑）
- Affected code:
  - `distill_changying/scripts/advisor/llm_report.py` — 重写 Prompt，允许推荐 ETF 品种和份数
  - `distill_changying/scripts/advisor/etf_recommend.py` — **新建**，ETF 操作推荐引擎
  - `distill_changying/scripts/advisor/position.py` — 整合 150 份框架
  - `distill_changying/scripts/advisor/bridge.py` — 整合新推荐引擎
  - `config/config.yaml` — 新增 ETF 池配置、份数规则
  - `distill_changying/scripts/advisor/config.yaml` — 同上

---

## ADDED Requirements

### Requirement: 150 份资产配置框架

系统 SHALL 支持 E大经典的"150 份资产配置"框架，将总资产划分为 150 份，每次操作以"份"为单位。

#### Scenario: 份数配置
- **WHEN** 系统启动
- **THEN** 从配置文件读取：
  - 总份数（默认 150）
  - 各 ETF 品种的持仓份数上限
  - 每次操作的份数（如每次买入 1 份、卖出 1 份）
  - 估值区间与操作映射表

#### Scenario: 份数校验
- **WHEN** 生成操作建议
- **THEN** 校验总份数不超 150
- **AND** 校验单品种不超配置上限
- **AND** 若超出，优先建议卖出而非买入

### Requirement: ETF 品种配置

系统 SHALL 支持配置可投资的 ETF 品种池，包含品种名称、代码、关联指数、估值触发区间。

#### Scenario: ETF 品种定义
- **WHEN** 系统启动
- **THEN** 读取配置的 ETF 品种列表，每个品种包含：
  - ETF 代码（如 510300）
  - ETF 名称（如 沪深300ETF）
  - 关联指数代码（如 000300，用于获取估值数据）
  - 买入估值区间（PE 分位区间，如 0%-30%）
  - 卖出估值区间（PE 分位区间，如 70%-100%）
  - 每次操作份数（如 1 份）
  - 持有份数上限（如 10 份）
  - 资产大类（A股/债券/海外/商品等）

#### Scenario: 默认 ETF 池
- **WHEN** 配置未指定 ETF 池
- **THEN** 使用预设 ETF 池：沪深300ETF(510300)、中证500ETF(510500)、创业板ETF(159915)、上证50ETF(510050)、中证红利ETF(515080)、恒生ETF(159920)、纳指ETF(513100)、黄金ETF(518880)、国债ETF(511010)

### Requirement: ETF 操作推荐引擎

系统 SHALL 基于各 ETF 关联指数的估值分位，自动生成具体的买卖操作建议。

#### Scenario: 生成操作建议
- **WHEN** 完成市场估值扫描
- **THEN** 对每个 ETF 品种：
  - 获取其关联指数的 PE 分位
  - 与配置的买卖区间对比
  - 若在买入区间：生成"买入 X 份 YYY（代码）"
  - 若在卖出区间：生成"卖出 X 份 YYY（代码）"
  - 若在持有区间：标注"持有"
- **AND** 按操作优先级排序（买入区 > 卖出区 > 持有区）
- **AND** 同区间内按 PE 分位偏离程度排序（偏离越多优先级越高）

#### Scenario: 操作约束
- **WHEN** 生成操作建议
- **THEN** 同一品种不能同时出现买入和卖出建议
- **AND** 若该品种已达持有上限，不生成买入建议
- **AND** 若该品种持有为 0，不生成卖出建议
- **AND** 每次操作总量不超过配置的份数上限

### Requirement: AI 分析报告（重写）

系统 SHALL 将估值数据和 ETF 操作建议发送给 LLM，基于 E大知识底座生成含操作建议的投资分析报告。LLM 作为 E大风格顾问，可以推荐具体品种和份数。

#### Scenario: 生成日度报告
- **WHEN** 执行 `analyze` 命令或每日定时扫描
- **THEN** Prompt 中包含：
  - E大知识底座（二次压缩版）
  - 各指数估值数据（PE/PB/分位）
  - 算法生成的 ETF 操作建议（买入/卖出清单）
  - 市场温度和仓位建议
  - 角色设定："你是 ETF 拯救世界的投资顾问，基于以下数据给出操作建议。你可以推荐具体的 ETF 品种和买卖份数，但必须基于我已提供的估值数据，不可编造数据。"
- **AND** LLM 输出包含：
  - 今日市场概况
  - 估值温度判断
  - **ETF 操作建议**（具体品种、方向、份数、理由）
  - 仓位调整方向
  - 风险提示
  - E大风格金句
  - 免责声明

#### Scenario: LLM 调用失败降级
- **WHEN** DeepSeek API 调用失败
- **THEN** 降级为纯算法报告（温度评估 + ETF 操作清单 + 免责声明），不含 AI 分析

### Requirement: 飞书日报推送（升级）

系统 SHALL 每日推送到飞书的报告从"纯温度报告"升级为"ETF 操作建议 + 温度"日报。

#### Scenario: 日报内容
- **WHEN** 每日 06:15 UTC（14:15 北京时间）推送
- **THEN** 飞书消息包含：
  - 全市场温度
  - **本日 ETF 操作建议**（买入/卖出清单，含品种、份数、理由）
  - PE 分位速查表
  - 免责声明
- **AND** 若 LLM 可用，使用 LLM 生成的 E大风格报告
- **AND** 若 LLM 不可用，使用纯算法操作清单

### Requirement: 交互式问答（增强）

系统 SHALL 提供交互模式，支持用户追问具体 ETF 品种。问答允许推荐具体品种和份数。

#### Scenario: ETF 专项追问
- **WHEN** 用户问"沪深300现在能买吗？""医药ETF怎么看？"
- **THEN** LLM 基于当前估值数据 + E大知识底座回答
- **AND** 可给出具体品种建议和份数参考
- **AND** 如果用户问的是配置外的品种，提示当前无该品种估值数据，建议关注已配置品种

#### Scenario: 操作复盘
- **WHEN** 用户问"上次推荐的买入了吗？""现在仓位怎么样了？"
- **THEN** LLM 结合上次推荐和当前估值回答
- **AND** 若系统记录了历史推荐，引用历史数据

---

## MODIFIED Requirements

### Requirement: AI 分析报告（原）

**变更**：LLM 的职责从"解说员不做决策者"改为"E大风格的投资顾问"。

- **BREAKING**：`llm_report.py` 的 System Prompt 中移除"不推荐具体品种，不预测涨跌，不发明数值"的约束
- **BREAKING**：新增约束"基于提供的估值数据推荐具体 ETF 品种和买卖份数，不可编造估值数据"
- 报告结构新增"ETF 操作建议"段落

### Requirement: 仓位建议（原）

**变更**：整合 150 份资产配置框架。

- 仓位建议从"A股/债券/现金 百分比"升级为"ETF 品种维度的份数建议"
- `position.py` 输出增加"各 ETF 品种建议份数"维度

### Requirement: 交互式问答（原）

**变更**：LLM 约束从"解说员不决策"改为"可推荐品种和份数"。

- chat.py 的 System Prompt 更新角色设定
- 支持 ETF 品种相关的追问

## REMOVED Requirements

无移除的需求。

## 配置结构设计

```yaml
strategies:
  distill_changying:
    params:
      # 150 份资产配置
      allocation:
        total_shares: 150          # 总份数
        nav_per_share: 10000       # 每份金额（元）
      
      # ETF 品种池
      etf_pool:
        - code: "510300"
          name: "沪深300ETF"
          index: "000300"          # 关联指数
          category: "A股-宽基"
          buy_zone:                # 买入估值区间
            pe_percentile_max: 30  # PE分位 < 30% 时买入
          sell_zone:               # 卖出估值区间
            pe_percentile_min: 70  # PE分位 > 70% 时卖出
          shares_per_trade: 1      # 每次操作份数
          max_shares: 10           # 持有份数上限
        # ... 更多 ETF 品种
```