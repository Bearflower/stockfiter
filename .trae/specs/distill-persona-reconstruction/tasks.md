# Tasks

- [ ] Task 1: 重构 opinion_extractor.py — 输出统一观点库
  - [ ] 1.1 新增 `_load_state_json(path)` 从 state.json 读取已处理条目
  - [ ] 1.2 新增 `_extract_operations_from_state(state)` 提取所有 operation 类型记录
  - [ ] 1.3 新增 `_extract_observations_from_state(state)` 提取所有 observation 类型记录
  - [ ] 1.4 新增 `_extract_principles_from_state(config)` 从核心观点矿脉提取 principle 类型记录
  - [ ] 1.5 新增 `merge_opinions(operations, observations, principles)` 合并为统一列表
  - [ ] 1.6 修改 `extract_opinions()` 使用三通道合并逻辑
  - [ ] 1.7 观点库每条记录增加 `source_type`（operation/observation/principle）和 `time` 字段
  - [ ] 1.8 向下兼容：旧格式观点库加载时自动标记为 principle

- [ ] Task 2: 增强 knowledge_base.py — 加载三通道数据
  - [ ] 2.1 新增 `load_recent_operations(config)` 从操作时间线.jsonl 读取最近 N 条
  - [ ] 2.2 新增 `load_recent_observations(config)` 从近期判断库.jsonl 读取最近 N 条
  - [ ] 2.3 修改 `_assemble_prompt` 在系统 Prompt 中新增"近期操作动态"和"近期市场判断"段落
  - [ ] 2.4 在 config.yaml 中新增 `persona` 配置段

- [ ] Task 3: 修改 analyzer.py — 聚焦原则提取
  - [ ] 3.1 修改 `build_analysis_prompt` 的 prompt 文本，限定分析范围为"跨时间的通用原则"
  - [ ] 3.2 在 prompt 开头增加说明段，告知 LLM 操作记录和市场判断已由独立通道处理

- [ ] Task 4: 集成 main.py cmd_update — 完整流程
  - [ ] 4.1 在 cmd_update 的"步骤6 跨文章深度分析"后，新增 record_extractor 调用
  - [ ] 4.2 在 record_extractor 后，新增增强版 opinion_extractor 调用
  - [ ] 4.3 确保增量处理不重复提取（state.json 已有增量机制）

- [ ] Task 5: 简化 opinion_matcher.py — 改用统一观点库
  - [ ] 5.1 修改 `match_opinions` 从单文件（观点库.jsonl）加载全部记录
  - [ ] 5.2 改为按 source_type 分组后按优先级匹配
  - [ ] 5.3 移除三通道独立文件加载逻辑
  - [ ] 5.4 简化 Prompt 模板，不再需要三段式输入
  - [ ] 5.5 向下兼容：source_type 缺失时默认为 principle

- [ ] Task 6: 端到端验证 — 执行 update 并验证产物
  - [ ] 6.1 执行 `python scripts/distill/main.py update` 观察完整流程
  - [ ] 6.2 验证 观点库.jsonl 包含 operation/observation/principle 三种类型
  - [ ] 6.3 验证 观点库.jsonl 条目数从 20 条增加到至少 100 条
  - [ ] 6.4 验证 知识底座系统 Prompt 包含近期动态段落
  - [ ] 6.5 验证 opinion_matcher 返回的条目包含 source_type 字段
  - [ ] 6.6 验证日报的"E大说过"段落包含近期操作和判断

# Task Dependencies

- Task 2 依赖 Task 1（需要统一观点库的 source_type 字段）
- Task 4 依赖 Task 1（需要增强版 opinion_extractor）
- Task 5 依赖 Task 1（需要统一观点库格式）
- Task 6 依赖 Task 1-5（端到端验证）

# Parallelization

- Task 1（opinion_extractor）和 Task 3（analyzer prompt）可并行开发
- Task 2（knowledge_base）和 Task 5（opinion_matcher）可并行开发
- Task 4（main.py 集成）依赖 Task 1 完成后可独立进行