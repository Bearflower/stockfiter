# E大投资决策助手 Spec

## Why

已蒸馏出"ETF拯救世界"（E大）的 283 篇博客核心观点、思维模型和金句，但这些是静态认知资产。需要将其转化为一个**可运行的投资决策助手**，每日自动获取市场数据（上证/深证/基金估值），基于 E大的投资框架给出操作建议，让"蒸馏出的他"真正为你工作。

## What Changes

- 新增 `scripts/advisor/` 模块，实现 E大投资决策助手
- 集成 akshare 获取 A股指数估值数据
- 基于 E大的"估值温度计"框架，给出市场温度评估和仓位建议
- 支持 CLI 和交互式两种模式
- 输出结构化的投资建议报告

## Impact

- Affected specs: 无（新建，独立于 `distill-changying-blog`）
- Affected code: `scripts/advisor/`（新建）
- Dependencies: Python 3.x, openai, akshare, pandas, PyYAML

---

## ADDED Requirements

### Requirement: 知识底座加载

系统 SHALL 加载蒸馏产物作为系统提示，使 LLM 的分析输出体现 E大的投资理念和语言风格。

#### Scenario: 加载知识底座
- **WHEN** 决策助手启动
- **THEN** 自动读取 `docs/distilled/核心观点矿脉.md` 和 `docs/distilled/金句库.md`
- **AND** 构建包含 E大投资框架的系统 Prompt

### Requirement: 市场数据获取

系统 SHALL 通过 akshare 获取 A股主要指数的实时估值数据。

#### Scenario: 获取指数估值
- **WHEN** 执行数据获取命令
- **THEN** 获取以下指数的 PE/PB 及历史分位：
  - 上证指数（000001）
  - 深证成指（399001）
  - 沪深300（000300）
  - 中证500（000905）
  - 创业板指（399006）
  - 上证50（000016）
- **AND** 如果数据源不可用（如网络问题），输出明确错误信息而不是崩溃

#### Scenario: 获取行业/板块估值（可选）
- **WHEN** 指定 `--sectors` 参数
- **THEN** 额外获取主要行业指数估值（消费、医药、科技、金融等）

### Requirement: 市场温度评估

系统 SHALL 基于 E大的"估值温度计"框架，将当前市场划分为五个温度区间。

#### Scenario: 计算市场温度
- **WHEN** 获取到各指数 PE 历史分位数据
- **THEN** 按配置的估值温度阈值（从 config.yaml 读取）划分区间：
  - 钻石坑（买入区）：PE 分位 < X%
  - 低估区：PE 分位在 X%-Y%
  - 正常区：PE 分位在 Y%-Z%
  - 高估区：PE 分位在 Z%-W%
  - 泡沫区（卖出区）：PE 分位 > W%
- **AND** 综合各指数给出全市场温度评估

### Requirement: 仓位建议

系统 SHALL 基于市场温度给出参考仓位比例。

#### Scenario: 计算建议仓位
- **WHEN** 市场温度评估完成
- **THEN** 按配置的仓位映射表给出建议的股票仓位比例
- **AND** 建议以 A股仓位/债券仓位/现金仓位 三维度呈现

### Requirement: AI 分析报告

系统 SHALL 将市场数据发送给 LLM，基于 E大知识底座生成结构化的投资分析报告。

#### Scenario: 生成日度分析报告
- **WHEN** 执行分析命令
- **THEN** 将以下信息组合为 Prompt 发送给 LLM：
  - E大知识底座（投资框架、核心观点）
  - 当前各指数 PE/PB 估值数据及历史分位
  - 计算得出的市场温度和仓位建议
- **AND** LLM 以 E大的口吻输出报告，包含：
  - 今日市场概况
  - 估值温度判断（当前处于什么阶段）
  - 仓位建议及理由
  - 操作方向（哪些品种值得关注）
  - 风险提示
  - 一句 E大风格的金句总结

### Requirement: 交互式问答

系统 SHALL 提供交互模式，支持用户追问具体问题。

#### Scenario: 交互式问答
- **WHEN** 以 `--chat` 模式启动
- **THEN** 先展示市场评估报告，然后进入对话循环
- **AND** 支持追问如："恒生现在能买吗？""医药怎么看？""和上个月比有什么变化？"

### Requirement: 数据缓存

系统 SHALL 缓存市场数据，避免短时间内重复请求。

#### Scenario: 数据缓存
- **WHEN** 同一天内多次请求市场数据
- **THEN** 使用缓存数据，不重复调用 akshare API
- **AND** 缓存有效期可通过配置调整（默认缓存到当天收盘后失效）

### Requirement: CLI 入口

系统 SHALL 通过 `scripts/advisor/main.py` 提供命令行入口。

#### Scenario: 快速评估
- **WHEN** 执行 `python scripts/advisor/main.py check`
- **THEN** 获取数据 → 计算温度 → 输出简洁版建议（不需要 LLM）

#### Scenario: 完整分析
- **WHEN** 执行 `python scripts/advisor/main.py analyze`
- **THEN** 获取数据 → 计算温度 → 调用 LLM 生成完整分析报告

#### Scenario: 交互模式
- **WHEN** 执行 `python scripts/advisor/main.py chat`
- **THEN** 进入交互式对话模式