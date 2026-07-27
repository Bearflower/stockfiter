# E大人物画像蒸馏重构 Spec

## Why

当前蒸馏方法学的根本问题在于**将"人物智慧"当作"文章摘要"处理**：

```
输入: 294篇(109发车帖+183微博精选+2文章)
  → [summarizer] 每篇压缩为100字摘要
  → [analyzer] 提取"最反复强调的10个观点"
  → [opinion_extractor] 再压缩为20条通用原则
  → 输出: 20条 market_condition="通用" 的"正确的废话"
```

这种"三次LLM压缩"的流水线，天然过滤掉了所有时效性信息（某次操作、某次市场判断），只留下放之四海而皆准的通用原则。**这不是人物画像，而是教科书摘要。**

E大这个"人物"的智慧由三层构成，需要三种不同的提取和存储策略：

| 层 | 内容 | 来源 | 当前状态 |
|---|------|------|---------|
| 操作记忆 | 什么时间做了什么交易 | 发车帖(109篇) | 已提取但未融入观点库 |
| 判断记忆 | 在不同市场条件下说过什么 | 微博精选(183篇) | 已提取但未融入观点库 |
| 原则记忆 | 跨时间线的投资哲学 | 全部内容 | 观点库仅含此层(20条) |

新的方法学核心：**不要再压缩，直接存"颗粒"**。把每一条操作、每一条判断、每一条原则都作为独立的"知识颗粒"存入观点库，保留时间戳和来源类型，让匹配环节自己选。

## What Changes

### 1. 核心转变：从"三层压缩"到"三层并排"

```
旧: 294篇 → 三层压缩 → 20条通用原则
新: 
  ├─ 发车帖(109篇) → 结构化操作记录 → 操作时间线.jsonl
  ├─ 微博精选(183篇) → 单条市场判断 → 近期判断库.jsonl  
  └─ 全部内容 → analyzer → 核心观点矿脉 → 通用原则(保留现有)
       ↓
  合并 → 观点库.jsonl (每条带 source_type + time)
```

### 2. 具体文件变更

| 文件 | 变更 | 说明 |
|------|------|------|
| **opinion_extractor.py** | **重构** | 不再只用核心观点矿脉+金句库，改为从state.json提取操作+判断+原则，合并为带标签的统一观点库 |
| **knowledge_base.py** | **增强** | 将操作时间线+近期判断库加入系统Prompt，LLM生成报告时知晓E大近期动态 |
| **main.py cmd_update** | **集成** | 在update流程中调用record_extractor生成中间产物，并调用增强版opinion_extractor |
| **opinion_matcher.py** | **简化** | 从三通道独立文件改为读取统一观点库.jsonl，按source_type/时间过滤 |
| **analyzer.py** | **修改prompt** | 明确要求只提取"跨时间的通用原则"，不再试图覆盖所有内容 |
| **config.yaml** | **新增配置** | persona-reconstruction相关参数 |

### 3. 删除/废弃

- 无删除。现有产物（核心观点矿脉.md、金句库.md、观点库.jsonl、操作时间线.jsonl、近期判断库.jsonl）全部保留，只是opinion_extractor.output发生变化。
- 后端产物（观点库.jsonl）的格式将增加 `source_type` 和 `time` 字段。

## Impact

- Affected specs: 覆盖并修正 distill-changying-blog/spec.md 中的方法论
- Affected code: 
  - `scripts/distill/opinion_extractor.py` — 重构
  - `scripts/advisor/knowledge_base.py` — 增强
  - `scripts/distill/main.py` cmd_update — 集成
  - `scripts/advisor/opinion_matcher.py` — 简化
  - `scripts/distill/analyzer.py` — prompt调整
  - `scripts/distill/config.yaml` — 新增配置
- 新增产物: 无（已有文件格式变化）
- 不修改: `docs/blog/` 原始文件

---

## ADDED Requirements

### Requirement: 统一观点库（核心变更）

系统 SHALL 将操作记录、市场判断、通用原则三种知识合并到统一的 `观点库.jsonl`，每条记录带 `source_type` 和 `time` 标签。

#### Scenario: 合并三通道数据
- **WHEN** 执行 `python scripts/distill/main.py update`
- **THEN** 增强版 opinion_extractor 从 state.json 中读取：
  - 所有发车帖的 `operations` 字段 → 标记为 `source_type: "operation"`
  - 所有微博精选的 `weibo_opinions` 字段 → 标记为 `source_type: "observation"`
  - 核心观点矿脉.md → 通过LLM提取通用原则 → 标记为 `source_type: "principle"`
- **AND** 输出到 `观点库.jsonl`，数量不设上限（当前20条→预计100-500条）

#### Scenario: 观点库条目格式
- **WHEN** 观点库.jsonl 被写入
- **THEN** 每条记录的格式为：
  ```json
  {
    "opinion": "150计划卖出一份建信中证500(000478)，收益率76%",
    "source_type": "operation",
    "time": "2026-06-23T00:39:16",
    "topic": "交易操作",
    "market_condition": "高估区",
    "keywords": ["卖出", "止盈", "中证500"]
  }
  ```
  - `source_type` 取值: `"operation"` / `"observation"` / `"principle"`
  - `time` 取值: 操作记录/判断的时间戳，原则类为 `"principle"`（标记为跨时间通用）

### Requirement: 知识底座加载三通道

系统 SHALL 在构建 LLM 系统 Prompt 时，除投资哲学和金句外，新加入"近期操作动态"和"近期市场判断"段落。

#### Scenario: 系统Prompt包含近期动态
- **WHEN** `knowledge_base.load_knowledge_base()` 被调用
- **THEN** 系统 Prompt 在原有结构基础上新增两个段落：
  ```
  【E大近期操作动态】
  按时间倒排展示最近10条操作记录
  （从操作时间线.jsonl读取）
  
  【E大近期市场判断】
  按时间倒排展示最近15条市场判断
  （从近期判断库.jsonl读取）
  ```

### Requirement: analyzer 聚焦原则提取

系统 SHALL 修改 analyzer 的 prompt，明确将分析任务限定为"提取跨时间的通用投资原则"，不再要求覆盖操作记录或市场判断。

#### Scenario: analyzer prompt 调整
- **WHEN** `analyzer.build_analysis_prompt()` 被调用
- **THEN** prompt 中新增说明段：
  ```
  注意：本分析仅关注E大的通用投资原则和哲学理念。
  操作记录和市场判断不在本分析范围内，
  它们已经通过独立的通道提取和存储。
  请聚焦于：哪些投资原则是跨时间不变的？
  ```

### Requirement: update 流程集成

系统 SHALL 在 `main.py cmd_update` 中，确保每次增量处理后都重新生成统一观点库。

#### Scenario: update 流程
- **WHEN** `cmd_update` 执行完「步骤6 跨文章深度分析」后
- **THEN** 自动按顺序执行：
  1. `record_extractor.extract_all_and_save(state, output_dir)` — 更新操作时间线和近期判断库
  2. 增强版 `opinion_extractor.extract_opinions(config)` — 重新生成统一观点库.jsonl
  3. `opinion_extractor.write_opinions(opinions, output_dir)` — 写入文件

### Requirement: opinion_matcher 改用统一观点库

系统 SHALL 简化 opinion_matcher 的匹配逻辑，从"三通道独立文件"改为"统一观点库按 source_type 过滤"。

#### Scenario: 统一匹配
- **WHEN** `match_opinions()` 被调用
- **THEN** 从 `观点库.jsonl` 加载全部记录，按 source_type 分组
- **AND** 按优先级匹配：observation > operation > principle
- **AND** observation 和 operation 按时间倒排，取最近3个月内
- **AND** principle 按 market_condition 匹配

## MODIFIED Requirements

### Requirement: 配置文件新增参数

在 config.yaml 中新增 `persona` 段落：

```yaml
persona:
  max_observations_in_prompt: 15    # 知识底座中最多展示近期判断数
  max_operations_in_prompt: 10      # 知识底座中最多展示近期操作数
  recent_months: 3                  # "近期"的时间窗口（月）
  opinion_output_merged: true       # 是否输出统一观点库（默认开启）
```

## REMOVED Requirements

无删除。旧版 `观点库.jsonl` 格式（无 source_type 字段）将被子集取代。向下兼容：旧格式的条目在加载时自动标记为 `source_type: "principle"`。