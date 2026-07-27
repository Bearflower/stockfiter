# 长赢指数投资博客蒸馏 Spec

## Why

"ETF拯救世界"（E大）从 2015 年到 2026 年发布了约 283 篇博客/微博内容（约 2.4MB），涵盖投资理念、交易操作、问答互动、年度回顾等。这些内容目前以原始 Markdown 文件形式散落在 `docs/blog/` 目录中，难以快速调取和复用。需要按照 `docs/requirements/蒸馏长赢指数.md` 中定义的方法论，将这些海量内容压缩为结构清晰、可快速调用的认知资产。

## What Changes

- 新增 Python 蒸馏工具脚本 `scripts/distill/`，实现对博客文件的批量 AI 提炼
- 为每篇博客注入 frontmatter 元数据（summary、keywords、topic）
- 生成跨文章深度分析报告（核心观点、思维模型、价值观等）
- 输出结构化的蒸馏产物到 `docs/distilled/` 目录
- **不修改**原始 `docs/blog/` 中的文件内容

## Impact

- Affected specs: 无（新建项目）
- Affected code: `scripts/distill/`（新建），`docs/distilled/`（新建输出目录）
- Dependencies: Python 3.x, openai SDK, python-frontmatter, PyYAML

---

## ADDED Requirements

### Requirement: 配置文件驱动

系统 SHALL 通过 `scripts/distill/config.yaml` 配置文件管理所有参数，包括 API 配置、模型选择、速率限制、输入输出路径等。

#### Scenario: 读取配置
- **WHEN** 蒸馏脚本启动
- **THEN** 从 `scripts/distill/config.yaml` 读取所有配置项
- **AND** 如果配置文件不存在，使用内置默认值并输出警告

### Requirement: 单篇博客提炼（第一层蒸馏）

系统 SHALL 为 `docs/blog/` 下的每篇 Markdown 文件生成摘要和关键词，输出到 `docs/distilled/` 目录，保持原始文件不变。

#### Scenario: 为单篇博客生成摘要
- **WHEN** 对一篇博客文件执行提炼
- **THEN** 调用 LLM API 生成：一句话核心观点（≤100字）、3-5个关键词、主题分类
- **AND** 将结果输出为独立的 frontmatter 元数据文件或汇总索引

#### Scenario: 跳过已提炼的文章
- **WHEN** 某篇文章的提炼结果已存在
- **THEN** 默认跳过，不重复消耗 API
- **AND** 可通过 `--force` 参数强制重新提炼

#### Scenario: 长文截断处理
- **WHEN** 博客内容超过模型 token 限制
- **THEN** 自动截断到安全长度后发送给 API，并在日志中记录截断情况

### Requirement: 批量提炼流程

系统 SHALL 支持批量遍历所有博客文件，按配置的速率限制和批次大小调用 API。

#### Scenario: 批量提炼全部博客
- **WHEN** 执行批量提炼命令
- **THEN** 遍历 `docs/blog/` 下所有 `.md` 文件
- **AND** 每批次处理 N 篇后暂停 M 秒（由配置决定）
- **AND** 每篇文章间延迟 D 秒（由配置决定）
- **AND** 显示进度（当前处理数/总数）

#### Scenario: 按目录/年份筛选提炼
- **WHEN** 指定 `--year 2020` 参数
- **THEN** 仅处理文件名包含 "2020" 的文章
- **WHEN** 指定 `--pattern "微博精选"` 参数
- **THEN** 仅处理文件名匹配该模式的文章

### Requirement: 跨文章深度分析（第二层蒸馏）

系统 SHALL 将所有单篇摘要汇总后，进行一次跨文章深度分析，生成博主思想全景报告。

#### Scenario: 生成思想矿脉报告
- **WHEN** 单篇提炼全部完成后执行跨文章分析
- **THEN** 将所有摘要汇总为一个 Prompt 发送给 LLM
- **AND** 生成包含以下内容的报告：
  - 核心命题聚类（5-8个主题）
  - 十大核心观点（含原文例证）
  - 思维模型工具箱（5-10个）
  - 价值观罗盘
  - 演化轨迹（按年份的演变）
  - 影响源推测
  - 矛盾与张力分析

### Requirement: 蒸馏产物输出

系统 SHALL 将蒸馏产物输出为结构化的 Markdown 文件，存放在 `docs/distilled/` 目录下。

#### Scenario: 产物目录结构
- **WHEN** 蒸馏流程完成
- **THEN** `docs/distilled/` 目录下包含：
  - `README.md` — 蒸馏产物索引
  - `blog-index.csv` — 所有博客的摘要索引表
  - `核心观点矿脉.md` — 跨文章深度分析报告
  - `金句库.md` — 提取的金句按主题分类
  - `单篇摘要/` — 每篇博客的提炼卡片（可选，按需生成）

### Requirement: API 错误处理与重试

系统 SHALL 对 LLM API 调用实现错误处理和重试机制。

#### Scenario: API 调用失败重试
- **WHEN** API 调用返回错误（限流、超时等）
- **THEN** 记录错误日志，等待指数退避时间后重试，最多重试 N 次（由配置决定）

#### Scenario: JSON 解析失败处理
- **WHEN** LLM 返回的内容无法解析为期望的 JSON 格式
- **THEN** 记录原始响应到日志，尝试修复常见格式问题后重新解析
- **AND** 如果仍失败，标记该文件为处理失败并继续下一文件

### Requirement: CLI 入口

系统 SHALL 通过 `scripts/distill/main.py` 提供命令行入口，支持以下子命令。

#### Scenario: 运行第一层蒸馏
- **WHEN** 执行 `python scripts/distill/main.py summarize`
- **THEN** 对 `docs/blog/` 下所有博客执行单篇提炼

#### Scenario: 运行第二层蒸馏
- **WHEN** 执行 `python scripts/distill/main.py analyze`
- **THEN** 基于已有摘要执行跨文章深度分析

#### Scenario: 运行完整蒸馏
- **WHEN** 执行 `python scripts/distill/main.py distill`
- **THEN** 依次执行第一层和第二层蒸馏