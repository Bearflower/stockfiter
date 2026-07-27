# E大人物画像知识系统 Spec

## Why

当前蒸馏系统经过三通道重构，代码和数据层面已完成基础建设，但**人物画像被当作"LLM生成的一篇作文"来设计，而不是"一个可交互的知识系统"来构建**。

### 核心诊断

多智能体审查发现四个关键问题：

| # | 问题 | 严重度 | 说明 |
|---|------|--------|------|
| P0 | **时间戳全为处理日期** | 致命 | 2352条记录的 `time` 字段全部是 `2026-06-23`（提取时间），非博文实际发布日期。导致上层所有按时间分组的分析逻辑（"按年份操作摘要"、"判断演化史"）全部失灵。50条principle记录的time值甚至是字符串 `"principle"`，按时间排序会崩溃。 |
| P0 | **画像=静态文档** | 范式错误 | `persona_builder.py` 将90%的数据截断后，一次性塞给LLM生成一篇3000-5000字的Markdown。这不是"人物画像"，而是基于10%数据的"LLM作文"。无交互、无索引、无增量能力。 |
| P1 | **检索=原始关键词匹配** | 能力不足 | `persona_query.py` 用 `str.lower() in text` 做匹配，无分词、无语义、无向量。停用词表硬编码且包含"市场""投资"等关键投资领域词。 |
| P2 | **缺少可视化维度** | 体验缺失 | 无法直观看到操作频率分布、品种偏好、收益率分布、判断主题分布等量化指标。 |

### 范式转换

```
旧范式：输入 → 三通道JSONL → 一次性LLM → 静态Markdown文档 → 阅读即结束

新范式：输入 → 三通道JSONL → 结构索引 + 向量索引 → 查询API + 可视化层 → 持续使用
                                    ↓
                             增量更新，无需全量重跑
```

### 三层知识回顾

| 层 | 内容 | 当前数据 |
|---|------|---------|
| 操作记忆 | 何时买卖了什么品种、收益率 | 157条（但时间戳全为处理日期） |
| 判断记忆 | 在不同市场下说过什么 | 994条（但时间戳全为处理日期） |
| 原则记忆 | 跨时间的投资哲学 | 50条（principle） |

## What Changes

### 1. 数据层修复（P0 - 必须先做）

**修复时间戳**：从博文文件名和 `blog-index.csv` 中提取实际发布日期，回填到JSONL数据。

当前博文文件名包含日期信息：
- `2015年9-12月"ETF拯救世界"微博精选.md` → 发布日期范围：2015-09
- `2026年1月长赢指数投资计划（一）.md` → 发布日期：2026-01
- `2020【06.01-06.07】"ETF拯救世界"微博精选.md` → 发布日期范围：2020-06

修复方案：
1. 在 `record_extractor` 中新增函数，从文件名提取实际日期
2. 更新 `操作时间线.jsonl` 和 `近期判断库.jsonl` 的 `time` 字段
3. 修复 `观点库.jsonl` 中50条principle记录的 `"principle"` 字符串时间戳
4. 为 `近期判断库.jsonl` 补全缺失的 `source_type` 字段

### 2. 构建知识索引系统（核心变更）

将 `persona_builder.py` 从"静态文档生成器"升级为"知识系统构建器"：

**2.1 结构化索引（新增）**

创建 `persona_index.py`，构建多维可筛选索引：

```
知识索引 = {
  "by_fund": {                   # 按品种索引
    "中证500": [...操作记录...],
    "沪深300": [...操作记录...],
    ...
  },
  "by_action": {                 # 按操作类型索引
    "买入": [...],
    "卖出": [...]
  },
  "by_topic": {                  # 按主题索引
    "估值": [...],
    "仓位管理": [...],
    ...
  },
  "by_year": {                   # 按年份索引
    "2020": [...],
    "2024": [...],
    ...
  }
}
```

**2.2 向量索引（新增）**

为判断库和原则库生成 Embedding 向量，支持语义检索：
- 使用 `text2vec-base-chinese` 或同等级别的轻量Embedding模型
- 每条记录生成一个向量，存储在 `persona_embeddings.npy`
- 支持余弦相似度检索

**2.3 统计摘要模块（新增）**

创建 `persona_stats.py`，生成量化统计：
- 操作频率月度热力图
- 品种买卖分布统计
- 收益率分布统计
- 判断主题分布统计
- 投资原则关联网络（共现分析）

### 3. 检索升级（P0）

将 `persona_query.py` 从关键词匹配升级为混合检索：

```
用户问题 → 关键词提取(jieba) → BM25检索（候选池A）
         → Embedding向量化    → 向量余弦相似度（候选池B）
                                 ↓
                           混合排序(加权)
                                 ↓
                           Top-K结果 → LLM综合生成回答
```

具体变更：
1. 引入 `jieba` 中文分词替代标点分割
2. 移除停用词表中的"市场""投资""基金""指数"等有信息量的投资领域词
3. 新增 Embedding 向量检索（内存级，使用 numpy 实现余弦相似度）
4. 混合排序：BM25得分(weight=0.3) + 向量相似度(weight=0.7)
5. 新增时间衰减因子：近期记录在排序中加权

### 4. 人物画像静态文档保留（降级为"导出"功能）

保留 `main.py persona` 子命令，但将其降级为"知识系统导出"功能：
- 不再一次性生成6个章节的LLM作文
- 改为从结构化索引 + 统计摘要自动组装文档
- 时间线章节改为真实数据驱动的表格/列表
- 统计图表章节改为数据表格（文本友好）

### 5. 可视化辅助（P1 - 可选）

新增 `persona_viz.py`，基于结构化索引生成可视化：
- 操作频率年度热力图（ASCII或Markdown表格格式）
- 品种买卖排名表
- 收益率分布直方图（文本格式）
- 判断主题词云（Markdown标签格式）

### 6. 增量更新机制

知识索引和向量索引支持增量更新：
- 新博客文件进入 → `summarize` → `record_extractor` → 更新索引
- 无需重新处理全部数据
- 向量索引支持增量追加

### 文件变更清单

| 文件 | 变更 | 说明 |
|------|------|------|
| `scripts/distill/record_extractor.py` | **修复** | 新增从文件名提取实际日期的函数，修复time字段 |
| `docs/distilled/操作时间线.jsonl` | **数据修复** | time字段从处理日期改为实际操作日期 |
| `docs/distilled/近期判断库.jsonl` | **数据修复** | time字段修复 + 补全source_type |
| `docs/distilled/观点库.jsonl` | **数据修复** | 修复50条principle的time="principle"问题 |
| `scripts/distill/persona_index.py` | **新增** | 结构化索引 + 向量索引构建 |
| `scripts/distill/persona_stats.py` | **新增** | 量化统计分析模块 |
| `scripts/distill/persona_viz.py` | **新增** | 可视化辅助模块（文本格式图表） |
| `scripts/advisor/persona_query.py` | **重构** | 从关键词匹配升级为混合检索（BM25+向量） |
| `scripts/distill/persona_builder.py` | **重构** | 从LLM作文降级为知识系统组装器 |
| `scripts/distill/config.yaml` | **新增参数** | 索引、检索、Embedding相关配置 |
| `scripts/distill/main.py` | **新增子命令** | `index` 构建/更新知识索引 |

### 不修改
- `docs/blog/` 原始博客文件
- `scripts/distill/summarizer.py`（已正确实现）
- `scripts/distill/state_manager.py`（已正确实现）
- `scripts/distill/opinion_extractor.py`（已正确实现）
- `scripts/distill/analyzer.py`（已正确实现）
- `scripts/advisor/opinion_matcher.py`（已正确实现，时间窗口过滤逻辑有效但受时间戳bug影响）
- `scripts/advisor/knowledge_base.py`（已正确实现）

## Impact

- Affected specs: 在 `distill-persona-reconstruction` 和 `distill-persona-wisdom` 基础上做根本性范式转换
- Affected code:
  - `scripts/distill/record_extractor.py` — 修复时间戳
  - `scripts/distill/persona_builder.py` — 重构
  - `scripts/advisor/persona_query.py` — 重构
  - `scripts/distill/persona_index.py` — 新增
  - `scripts/distill/persona_stats.py` — 新增
  - `scripts/distill/persona_viz.py` — 新增
  - `scripts/distill/main.py` — 新增子命令
  - `scripts/distill/config.yaml` — 新增配置
- Affected data:
  - `操作时间线.jsonl` — time字段修复
  - `近期判断库.jsonl` — time字段修复 + source_type补全
  - `观点库.jsonl` — 修复principle时间戳
  - `persona_embeddings.npy` — 新增（向量文件）
- New files:
  - `scripts/distill/persona_index.py`
  - `scripts/distill/persona_stats.py`
  - `scripts/distill/persona_viz.py`

---

## ADDED Requirements

### Requirement: 时间戳修复

系统 SHALL 将三通道JSONL数据的 `time` 字段从处理时间戳修复为博文的实际发布日期。

#### Scenario: 从文件名提取日期
- **WHEN** `record_extractor` 提取操作记录和判断记录
- **THEN** 从博客文件名解析实际发布日期范围
- **AND** 将 `time` 字段设置为发布月份的首日（如文件名含"2015年9-12月"则设为 `2015-09-01`）
- **AND** 发车帖文件名含具体日期则使用该日期

#### Scenario: 修复principle时间戳
- **WHEN** 读取观点库中 `time = "principle"` 的记录
- **THEN** 改为使用 `processed_at` 字段值
- **AND** 后续生成时不再使用字符串作为时间戳

### Requirement: 结构化知识索引

系统 SHALL 提供 `persona_index.py` 模块，构建多维可筛选的结构化索引。

#### Scenario: 构建索引
- **WHEN** 执行 `python scripts/distill/main.py index`
- **THEN** 从三通道JSONL数据构建按品种/操作类型/主题/年份分组的索引
- **AND** 按时间倒排排序，确保最新记录优先

#### Scenario: 向量索引
- **WHEN** 构建索引时
- **THEN** 为所有判断库记录和原则库记录生成 Embedding 向量
- **AND** 向量文件保存为 `persona_embeddings.npy`
- **AND** 支持增量追加新记录的向量

### Requirement: 混合检索

系统 SHALL 升级 `persona_query.py` 的检索机制，支持 BM25 + 向量余弦相似度的混合检索。

#### Scenario: 语义检索
- **WHEN** 用户提出自然语言问题
- **THEN** 使用 jieba 分词提取关键词，移除"市场""投资"等停用词
- **AND** 通过 BM25 算法在候选库中检索
- **AND** 同时通过 Embedding 向量做余弦相似度检索
- **AND** 混合排序（BM25权重0.3 + 向量权重0.7 + 时间衰减因子）
- **AND** 返回 Top-K 最相关记录

### Requirement: 量化统计摘要

系统 SHALL 提供 `persona_stats.py` 模块，生成人物画像的量化统计指标。

#### Scenario: 统计生成
- **WHEN** 调用统计模块
- **THEN** 生成以下统计：
  - 按年份的操作频率分布
  - 品种买卖排名 Top 10
  - 收益率区间分布
  - 判断主题分布
  - principle 关键词共现分析

### Requirement: 增量索引更新

系统 SHALL 支持知识索引和向量索引的增量更新。

#### Scenario: 增量更新
- **WHEN** 执行 `python scripts/distill/main.py index --incremental`
- **THEN** 只处理新增/变更的数据
- **AND** 追加新记录的向量到 embeddings 文件
- **AND** 更新结构化索引的分组

## MODIFIED Requirements

### Requirement: persona_builder 降级为导出功能

修改 persona_builder 的行为，从"LLM生成文档"改为"从结构化数据组装文档"。

#### Scenario: 导出人物画像
- **WHEN** 执行 `python scripts/distill/main.py persona`
- **THEN** 不从LLM生成，而是从结构化索引 + 统计摘要组装Markdown文档
- **AND** 操作时间线章节改为数据驱动的表格
- **AND** 统计图表章节改为数字表格
- **AND** 保留LLM生成的金句库和核心观点矿脉作为参考附录

### Requirement: main.py 新增子命令

在 main.py 中新增 `index` 子命令，用于构建和更新知识索引。

#### Scenario: index 子命令
- **WHEN** 执行 `python scripts/distill/main.py index`
- **THEN** 依次执行：
  1. 从三通道JSONL加载数据
  2. 构建结构化索引（by_fund/by_action/by_topic/by_year）
  3. 生成Embedding向量
  4. 计算统计摘要
  5. 保存所有索引文件到 `docs/distilled/persona_index/`
- **AND** `--incremental` 参数启用增量模式

## REMOVED Requirements

### Requirement: 人物画像LLM直接生成

**Reason**: 范式转换。LLM一次性生成6个章节3000-5000字的文档存在严重的事实幻觉风险，且截断了90%的数据，生成的画像不是"画像"而是"基于10%数据的作文"。

**Migration**: 替换为结构化索引 + 统计摘要的数组组装方式。LLM仅在智慧查询接口中用于"模拟E大口吻回答"的生成环节，不用于画像文档的生成。