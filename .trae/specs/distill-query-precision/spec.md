# 检索精度提升 Spec

## Why
回测验证发现：三个不同问题（"中证500怎么操作"、"红利怎么看"、"高估值风险"）的检索结果返回了相同的5条记录（全是2015年9月）。操作记录检索返回的 opinion 字段为空。检索区分度不足导致人物画像系统只能推断大方向，无法精确匹配具体品种判断。

## What Changes
- 操作记录补全 opinion 字段：从 plan/action/fund/code 自动拼接，使操作时间线记录可被 BM25 检索
- 检索增加按 source_type 过滤：分别检索 operation/observation/principle，避免混合返回
- 检索增加按主题（topic）过滤：利用结构化索引的 by_topic 维度做精确匹配
- 向量模型升级：尝试下载 sentence-transformers 模型，失败时保留 TF-IDF 降级
- 混合排序优化：BM25 权重降低、向量权重提高，增加品种匹配加分

## Impact
- Affected specs: distill-persona-knowledge-system
- Affected code: `scripts/advisor/persona_query.py`, `scripts/distill/persona_index.py`
- 不修改数据文件，仅改进检索和排序逻辑

## ADDED Requirements

### Requirement: 操作记录自动生成 opinion 字段
系统在加载操作时间线.jsonl 时，SHALL 自动从 plan/action/fund/code 字段拼接 opinion 文本，格式为 `{plan}计划 {action}{fund}({code})`，使操作记录可被 BM25 全文检索。

#### Scenario: 操作记录可被检索
- **WHEN** 用户查询 "中证500"
- **THEN** 操作记录中包含 "中证500" 的记录被检索并返回，opinion 字段非空

### Requirement: 按 source_type 分层检索
检索方法 SHALL 支持按 source_type（operation/observation/principle）分别检索，查询时返回三种类型的结果分开标注，而非混合返回。

#### Scenario: 分层检索
- **WHEN** 用户查询 "中证500估值"
- **THEN** 返回结果按 source_type 分组，operation 返回操作记录，observation 返回市场判断，principle 返回通用原则

### Requirement: 按 topic 主题过滤检索
系统 SHALL 利用结构化索引的 by_topic 维度，在检索时支持按主题过滤。当查询关键词匹配到已知主题时，优先返回该主题下的记录。

#### Scenario: 主题过滤
- **WHEN** 用户查询包含 "红利" 关键词
- **THEN** 系统识别 "红利" 主题，优先返回 topic 为 "红利" 或包含 "红利" 关键词的记录，再返回其他匹配记录

### Requirement: 向量模型升级
系统 SHALL 尝试下载 sentence-transformers 的 paraphrase-multilingual-MiniLM-L12-v2 模型。下载成功时使用该模型生成 384 维向量；下载失败时降级为 TF-IDF 512 维。系统 SHALL 设置 30 秒下载超时，避免无限等待。

#### Scenario: 模型下载成功
- **WHEN** 网络可达 HuggingFace
- **THEN** 使用 sentence-transformers 模型生成向量，检索精度提升

#### Scenario: 模型下载失败（超时）
- **WHEN** 30 秒内无法下载模型
- **THEN** 降级为 TF-IDF，系统正常运行，日志记录降级原因

### Requirement: 混合排序优化
混合排序算法 SHALL 调整权重分配：BM25 权重 0.2、向量相似度 0.5、品种匹配加分 0.3。当查询结果中的品种名与查询关键词匹配时，额外加分。

#### Scenario: 品种匹配加分
- **WHEN** 用户查询 "中证500"
- **THEN** 记录中 keywords 或 opinion 包含 "中证500" 的记录获得额外品种匹配加分，排序靠前

## MODIFIED Requirements

### Requirement: 向量模型降级逻辑（修改自 persona_index.py）
**原逻辑**：sentence-transformers 加载失败 → 捕获 ImportError → 降级 sklearn → 降级 None
**新逻辑**：sentence-transformers 加载失败 → 捕获 Exception（含 ConnectionError/Timeout）→ 降级 sklearn → 降级 None；增加 30 秒超时

### Requirement: BM25 检索器初始化（修改自 persona_query.py）
**原逻辑**：三个 BM25 检索器从合并后的全量数据构建，无区分
**新逻辑**：每个 BM25 检索器独立构建，source_type 明确区分；操作记录检索器使用自动生成的 opinion 文本