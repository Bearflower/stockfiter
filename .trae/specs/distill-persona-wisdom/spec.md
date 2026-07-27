# E大人物画像与智慧可用化 Spec

## Why

当前蒸馏系统的核心问题是：**代码已重构为三通道体系，但数据从未重新生成，实际产出仍停留在"教科书摘要"级。**

更重要的是，现有设计把"人物画像"等同于"数据管线"——将E大的知识拆成三堆JSONL文件后就结束了，没有人能"直观看到E大是谁"，也没有人能"真正使用E大的智慧"。

```
当前状态：
  代码层 ✅ 三通道重构完成
  数据层 ❌ 操作时间线.jsonl空 / 近期判断库.jsonl空 / 观点库仅20条旧格式
  画像层 ❌ 无直观的"人物画像"表达
  使用层 ❌ 无查询接口 / 日报管线断裂

目标状态：
  代码层 ✅ 修复2个bug + 强制重新提取
  数据层 ✅ 操作时间线100+条 / 判断库500+条 / 观点库500-3000条（带source_type）
  画像层 ✅ E大人物画像.md（结构化、可视化的人物知识全景）
  使用层 ✅ 查询接口 + 日报管线打通 + 智能匹配可验证
```

### 三层知识回顾

| 层 | 内容 | 来源 | 当前数据状态 |
|---|------|------|------------|
| 操作记忆 | 何时买卖了什么品种、收益率 | 发车帖(109篇) | 操作时间线.jsonl 为空 |
| 判断记忆 | 在不同市场下说过什么 | 微博精选(183篇) | 近期判断库.jsonl 为空 |
| 原则记忆 | 跨时间的投资哲学 | 全部内容 | 观点库仅20条通用原则 |

现有的 `distill-persona-reconstruction` spec 正确设计了技术架构，但**从未被真正执行过**。本 spec 在其基础上完成"最后一公里"：让数据落地、让人物画像直观化、让智慧可用。

## What Changes

### 核心转变：从"数据管道"到"人物画像"

```
旧视角：输入 → 三通道提取 → 三堆JSONL文件 → （等下游使用）
新视角：输入 → 提取知识颗粒 → 构建人物画像 → 智慧接口层 → 对外服务

人物画像 ≠ 数据文件
人物画像 = 结构化知识体系 + 直观表达 + 可查询接口
```

### 1. 修复代码Bug（2个）

| Bug | 文件 | 问题 | 修复方案 |
|-----|------|------|---------|
| B1 | `opinion_matcher.py` | `_filter_recent` 未实现时间窗口过滤，仅取前20条 | 按 `recent_months` 配置做 datetime 解析过滤 |
| B2 | `main.py` cmd_update | opinion_extractor 在 record_extractor 之前调用（顺序反了） | 先 record_extractor 生成中间产物，后 opinion_extractor 合并观点 |

### 2. 数据再生（核心变更）

通过强制重新摘要，用新三通道Prompt重新处理全部294篇博客：

| 产出 | 预期规模 | 格式 | 来源 |
|------|---------|------|------|
| state.json（更新） | 294条，含operations/weibo_opinions字段 | JSON | 强制重新摘要 |
| 操作时间线.jsonl | 100-500条 | JSONL（每行一个操作记录） | record_extractor从state提取 |
| 近期判断库.jsonl | 500-3000条 | JSONL（每行一个观点条目） | record_extractor从state提取 |
| 观点库.jsonl（新） | 500-3000条，含source_type+time | JSONL（三通道合并） | opinion_extractor合并 |

### 3. 构建人物画像（新增）

创建 `E大人物画像.md`，将三层知识整合为结构化、直观的人物全景知识文档。这不是一篇"分析报告"，而是一个**可导航的知识图谱式文档**：

```
# E大人物画像

## 1. 人物身份档案
   - 博主身份：ETF拯救世界
   - 核心领域：指数基金投资 / 资产配置
   - 活跃时间：201X-至今
   - 核心标签：逆向投资 / 150份框架 / 估值驱动

## 2. 交易操作时间线（可视化）
   - 按年份/月份分组的操作摘要
   - 重要操作事件标注（如：2018年底加仓、2021年初减仓）
   - 操作频率和规律分析

## 3. 市场判断演化史
   - 按时间轴排列的重要市场判断
   - 不同市场阶段（牛市/熊市/震荡市）的核心判断
   - 判断准确度和一致性评估

## 4. 投资体系图谱
   - 核心框架：150份资产配置
   - 估值方法：五区间温度计
   - 操作纪律：买卖规则体系
   - 品种选择：ETF品种偏好

## 5. 投资哲学体系
   - 十大核心原则（来自核心观点矿脉）
   - 原则之间的逻辑关联
   - 原则在不同市场条件下的应用

## 6. 语言风格样本
   - 标志性表达方式
   - 常用比喻和类比
   - 口语化风格特征
```

此文档由 **LLM生成**（一次调用），输入为全部三通道数据，输出为结构化人物画像。

### 4. 智慧查询接口（新增）

创建 `scripts/advisor/persona_query.py`，提供问题查询能力：

```python
# 示例接口
def query_persona(question: str, market_condition: str = "") -> dict:
    """
    查询E大对某个问题的"可能回答"
    
    流程：
    1. 从观点库检索相关观点（按source_type和topic过滤）
    2. 从操作时间线检索相关操作（按品种/时间过滤）
    3. 从近期判断库检索相关判断（按时间倒排）
    4. LLM综合生成"E大会怎么回答"
    
    Returns: {
        "answer": "...",        # 模拟E大口吻的回答
        "sources": [...],       # 引用的具体来源
        "confidence": 0.0-1.0   # 依据充分度
    }
    """
```

### 5. 日报告警管线打通

修复 `advisor` 的日报生成管线，确保：
- `knowledge_base._assemble_prompt` 正确加载近期动态段落
- 日报的"E大说过"段落同时包含：近期操作 + 近期判断 + 通用原则
- 日报文件正常输出到指定目录

### 文件变更清单

| 文件 | 变更 | 说明 |
|------|------|------|
| `opinion_matcher.py` | **修复** | `_filter_recent` 实现时间窗口过滤 |
| `main.py` cmd_update/cmd_analyze | **修复** | 调用顺序：先 record_extractor 后 opinion_extractor |
| `docs/distilled/E大人物画像.md` | **新增** | LLM生成的structured persona portrait |
| `scripts/advisor/persona_query.py` | **新增** | 智慧查询接口 |
| `scripts/distill/config.yaml` | **新增参数** | persona相关配置 |
| `scripts/advisor/config.yaml` | **新增参数** | 日报和查询相关配置 |
| （现有文件重新生成） | **重跑** | state.json/观点库.jsonl/操作时间线.jsonl/近期判断库.jsonl |

### 不修改

- `docs/blog/` 原始博客文件
- `scripts/distill/summarizer.py`（已正确实现）
- `scripts/distill/state_manager.py`（已正确实现）
- `scripts/distill/record_extractor.py`（已正确实现）
- `scripts/distill/opinion_extractor.py`（已正确实现）
- `scripts/distill/analyzer.py`（已正确实现）
- `scripts/advisor/knowledge_base.py`（已正确实现）

## Impact

- Affected specs: 在 `distill-persona-reconstruction` 基础上扩展完成"最后一公里"
- Affected code:
  - `scripts/advisor/opinion_matcher.py` — 修复_filter_recent
  - `scripts/distill/main.py` — 修复调用顺序
  - `scripts/advisor/persona_query.py` — 新增
- Affected data:
  - `.state.json` — 重新生成（含operations/weibo_opinions）
  - `操作时间线.jsonl` — 从空变为有数据
  - `近期判断库.jsonl` — 从空变为有数据
  - `观点库.jsonl` — 从20条旧格式变为500-3000条新格式
- New files:
  - `docs/distilled/E大人物画像.md`
  - `scripts/advisor/persona_query.py`

---

## ADDED Requirements

### Requirement: 人物画像文档

系统 SHALL 生成结构化 `E大人物画像.md`，以直观、可导航的方式呈现E大的知识全景。

#### Scenario: 生成人物画像
- **WHEN** 执行 `python scripts/distill/main.py persona`
- **THEN** 调用LLM，输入为全部三通道数据（操作时间线+近期判断库+观点库+核心观点矿脉+金句库）
- **AND** 输出结构化文档，包含6个章节：身份档案、操作时间线、判断演化史、体系图谱、哲学体系、语言风格
- **AND** 文档保存在 `docs/distilled/E大人物画像.md`

### Requirement: 智慧查询接口

系统 SHALL 提供 `persona_query.py` 模块，支持通过自然语言问题查询E大的"可能回答"。

#### Scenario: 查询E大观点
- **WHEN** 调用 `persona_query.query_persona(question, market_condition)`
- **THEN** 系统从观点库/操作时间线/近期判断库检索相关知识
- **AND** LLM综合生成模拟E大口吻的回答
- **AND** 返回包含 answer、sources、confidence 的结构化结果

#### Scenario: 在日报中使用
- **WHEN** 生成日报时
- **THEN** 日报的"E大说过"段落同时包含：
  - 近期操作（source_type="operation"，按时间倒排Top 5）
  - 近期判断（source_type="observation"，按时间倒排Top 10）
  - 相关原则（source_type="principle"，按topic匹配）

### Requirement: 数据再生流程

系统 SHALL 通过 `update --force-analyze` 完成完整的数据再生。

#### Scenario: 强制重新处理
- **WHEN** 执行 `python scripts/distill/main.py update --force-analyze`
- **THEN** 依次执行：
  1. 检查现有state.json，确认无operations/weibo_opinions字段
  2. 告知用户需要 `summarize --force` 重新生成
  3. 执行 `summarize --force` 用新三通道Prompt重新处理全部文件
  4. 执行 `analyze` 重新生成核心观点矿脉和金句库
  5. record_extractor 从新state.json提取 操作时间线.jsonl 和 近期判断库.jsonl
  6. opinion_extractor 合并三通道生成新观点库.jsonl（500-3000条，带source_type）

### Requirement: 增量兼容

系统 SHALL 确保增量更新时不会丢失已处理的新数据。

#### Scenario: 增量更新
- **WHEN** 后续有新博客文件加入
- **THEN** update 流程正确识别新文件，只用新prompt处理新文件
- **AND** record_extractor 和 opinion_extractor 在全量数据上重新运行

## MODIFIED Requirements

### Requirement: opinion_matcher 时间过滤（修复）

修改 opinion_matcher 的匹配逻辑，确保 `_filter_recent` 函数按实际时间过滤。

#### Scenario: 按时间窗口过滤
- **WHEN** `match_opinions` 匹配 observation 和 operation 类型
- **THEN** 解析每条记录的 `time` 字段
- **AND** 只保留 `recent_months`（配置项，默认3个月）内的记录
- **AND** 超过时间窗口的自动降级为通用匹配

### Requirement: main.py 调用顺序修复

修改 cmd_update 和 cmd_analyze 中 record_extractor 和 opinion_extractor 的调用顺序。

#### Scenario: 正确调用顺序
- **WHEN** cmd_update 执行到生成产物阶段
- **THEN** 先执行 `record_extractor.extract_all_and_save()` 生成操作时间线+判断库
- **AND** 后执行 `opinion_extractor.extract_opinions()` 从已生成的中间产物合并观点库

## REMOVED Requirements

无删除。