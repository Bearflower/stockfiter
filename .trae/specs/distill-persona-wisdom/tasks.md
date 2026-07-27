# Tasks

## 阶段一：修复代码Bug

- [ ] Task 1: 修复 opinion_matcher._filter_recent 时间窗口过滤
  - [ ] 1.1 `_filter_recent` 解析每条记录的 time 字段（ISO格式），与当前时间做比较
  - [ ] 1.2 只保留 `recent_months`（默认3个月）内的记录
  - [ ] 1.3 超过时间窗口的记录标记为 `timed_out: true`，降级为通用匹配
  - [ ] 1.4 配置项 `recent_months` 从 config.yaml 读取，不做硬编码
  - [ ] 1.5 处理异常情况：time 字段缺失或解析失败时，默认保留（安全降级）

- [ ] Task 2: 修复 main.py 调用顺序
  - [ ] 2.1 cmd_update 中：先 record_extractor.extract_all_and_save()，后 opinion_extractor.extract_opinions()
  - [ ] 2.2 cmd_analyze 中：同样修复调用顺序
  - [ ] 2.3 确保 opinion_extractor 能正确读取 record_extractor 刚生成的中间产物

## 阶段二：数据再生

- [ ] Task 3: 执行强制重新摘要（LLM密集操作）
  - [ ] 3.1 运行 `python3 scripts/distill/main.py summarize --force`
  - [ ] 3.2 验证 state.json 中新增了 operations 和 weibo_opinions 字段
  - [ ] 3.3 验证新增字段的非空条目数

- [ ] Task 4: 重新执行分析与产物生成
  - [ ] 4.1 运行 `python3 scripts/distill/main.py analyze`（调用顺序已修正）
  - [ ] 4.2 验证 操作时间线.jsonl 非空（预期100-500条）
  - [ ] 4.3 验证 近期判断库.jsonl 非空（预期500-3000条）
  - [ ] 4.4 验证 观点库.jsonl 每条包含 source_type + time 字段
  - [ ] 4.5 验证 source_type 分布：至少包含 operation/observation/principle 三种类型

## 阶段三：人物画像构建

- [ ] Task 5: 新增 persona 子命令 + 人物画像生成
  - [ ] 5.1 在 main.py 中新增 `persona` 子命令
  - [ ] 5.2 编写 `scripts/distill/persona_builder.py`：
    - [ ] 从三通道数据（操作时间线+近期判断库+观点库）加载全部记录
    - [ ] 从核心观点矿脉.md 和金句库.md 加载背景知识
    - [ ] 调用 LLM 生成结构化人物画像文档
  - [ ] 5.3 设计 LLM prompt，要求输出6个章节的人物画像.md
  - [ ] 5.4 输出到 `docs/distilled/E大人物画像.md`
  - [ ] 5.5 在 cmd_analyze 末尾自动调用 persona 构建（可选开关）

## 阶段四：智慧查询接口

- [ ] Task 6: 实现 persona_query.py
  - [ ] 6.1 创建 `scripts/advisor/persona_query.py`
  - [ ] 6.2 实现 `_search_opinions(question, topic_filter="")` 按topic检索观点库
  - [ ] 6.3 实现 `_search_operations(fund_name="")` 按品种检索操作记录
  - [ ] 6.4 实现 `_search_observations(keyword="")` 按关键词检索判断库
  - [ ] 6.5 实现 `query_persona(question, market_condition)` 主接口
  - [ ] 6.6 主流程：检索 → 组装上下文 → LLM生成回答 → 返回结构化结果
  - [ ] 6.7 提供CLI入口：`python3 -m scripts.advisor.persona_query "问题"`

## 阶段五：日报告警管线打通

- [ ] Task 7: 日报管线验证与修复
  - [ ] 7.1 检查 advisor 日报生成代码，确认目录配置正确
  - [ ] 7.2 确认 `knowledge_base._assemble_prompt` 的近期段落非空
  - [ ] 7.3 运行一次日报生成，验证输出文件
  - [ ] 7.4 验证日报中"E大说过"段落包含三类来源

## 阶段六：端到端验证

- [ ] Task 8: 完整流程验证
  - [ ] 8.1 操作时间线.jsonl 有100+条记录
  - [ ] 8.2 近期判断库.jsonl 有500+条记录
  - [ ] 8.3 观点库.jsonl 有500+条记录，全部带 source_type
  - [ ] 8.4 E大人物画像.md 生成成功，包含6个章节
  - [ ] 8.5 `persona_query.py` 查询返回有意义的回答
  - [ ] 8.6 日报成功生成，"E大说过"段落包含三类来源
  - [ ] 8.7 增量更新兼容：新增文件后 update 正确执行

## Task Dependencies

- Task 3 依赖 Task 1, 2（先修bug再重跑数据）
- Task 4 依赖 Task 3（需要新state.json）
- Task 5 依赖 Task 4（需要三通道数据就绪）
- Task 6 依赖 Task 4（需要观点库非空）
- Task 7 依赖 Task 4（需要近期数据就绪）
- Task 8 依赖 Task 1-7（端到端验证）

## Parallelization

- Task 1 和 Task 2 可并行（两个独立bug修复）
- Task 3 是单线程长任务（LLM密集型，需等待API）
- Task 5 和 Task 6 可并行开发（一个写文件、一个写接口）
- Task 7 可与 Task 5, 6 并行（独立验证）