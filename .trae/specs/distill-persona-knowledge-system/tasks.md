# Tasks

## 阶段一：数据层修复（P0 - 必须先做，其他所有任务依赖此阶段）

- [ ] Task 1: 修复三通道JSONL数据的时间戳
  - [ ] 1.1 在 `record_extractor.py` 中新增 `_extract_blog_date_from_filename(filename)` 函数，从博文文件名解析实际发布日期
  - [ ] 1.2 支持多种文件名格式：`2015年9-12月...`、`2020【06.01-06.07】...`、`2026年1月长赢...`、`2019-03-07 【文字发车】` 等
  - [ ] 1.3 更新操作时间线.jsonl的time字段为实际日期
  - [ ] 1.4 更新近期判断库.jsonl的time字段为实际日期
  - [ ] 1.5 修复观点库.jsonl中50条principle记录的 `time="principle"` 问题
  - [ ] 1.6 为近期判断库.jsonl补全缺失的 `source_type: "observation"` 字段
  - [ ] 1.7 验证：时间戳修复后，操作时间线按年份分布合理（至少覆盖5个不同年份）

## 阶段二：知识索引系统

- [ ] Task 2: 创建 persona_index.py 结构化索引模块
  - [ ] 2.1 实现 `build_structured_index(data)` 构建多维索引（by_fund/by_action/by_topic/by_year）
  - [ ] 2.2 实现 `save_index(index, output_dir)` 将索引保存为JSON
  - [ ] 2.3 实现 `load_index(index_dir)` 加载已保存的索引
  - [ ] 2.4 实现增量更新：`update_index_incremental(index, new_records)`

- [ ] Task 3: 创建 persona_index.py 向量索引模块
  - [ ] 3.1 选择合适的Embedding模型（优先使用已有依赖，其次考虑sentence-transformers）
  - [ ] 3.2 实现 `build_embeddings(records)` 为判断库和原则库记录生成向量
  - [ ] 3.3 实现 `cosine_similarity(query_vec, embeddings)` 余弦相似度检索
  - [ ] 3.4 实现 `save_embeddings(embeddings, filepath)` 和 `load_embeddings(filepath)`
  - [ ] 3.5 实现增量追加：`append_embeddings(embeddings, new_vectors)`

- [ ] Task 4: 创建 persona_stats.py 量化统计模块
  - [ ] 4.1 实现 `operation_frequency_by_year(operations)` 按年份操作频率统计
  - [ ] 4.2 实现 `fund_rankings(operations, top_k=10)` 品种买卖排名
  - [ ] 4.3 实现 `yield_distribution(operations)` 收益率分布统计
  - [ ] 4.4 实现 `topic_distribution(observations)` 判断主题分布统计
  - [ ] 4.5 实现 `principle_cooccurrence(principles)` 原则关键词共现分析
  - [ ] 4.6 实现 `generate_stats_report(stats)` 生成统计摘要文本（用于画像文档）

## 阶段三：检索升级

- [ ] Task 5: 重构 persona_query.py 检索机制
  - [ ] 5.1 引入 jieba 分词，改造 `_extract_keywords` 方法
  - [ ] 5.2 移除停用词表中的"市场""投资""基金""指数"等有信息量的投资领域词
  - [ ] 5.3 实现 BM25 检索器（或使用已有库）
  - [ ] 5.4 集成 Embedding 向量检索（调用 person_index 模块）
  - [ ] 5.5 实现混合排序：BM25得分*0.3 + 向量相似度*0.7 + 时间衰减因子
  - [ ] 5.6 验证：同一问题，新检索结果的相关性明显高于旧版

## 阶段四：画像导出降级

- [ ] Task 6: 重构 persona_builder.py 为知识系统组装器
  - [ ] 6.1 移除 LLM prompt 直接生成逻辑
  - [ ] 6.2 改为从结构化索引 + 统计摘要 + 金句库 + 核心观点矿脉组装文档
  - [ ] 6.3 操作时间线章节改为数据驱动的 Markdown 表格
  - [ ] 6.4 统计图表章节改为数字表格 + 文字描述
  - [ ] 6.5 保留金句库和核心观点矿脉作为参考附录
  - [ ] 6.6 验证：生成的文档中所有数据均可追溯至原始数据

## 阶段五：主流程集成

- [ ] Task 7: main.py 新增 index 子命令 + 集成完整流程
  - [ ] 7.1 在 main.py 中新增 cmd_index 函数
  - [ ] 7.2 注册 index 子命令（支持 `--incremental` 参数）
  - [ ] 7.3 在 cmd_analyze 和 cmd_update 末尾自动调用索引构建
  - [ ] 7.4 在 config.yaml 中新增索引、检索相关配置

- [ ] Task 8: 新增 persona_viz.py 可视化辅助模块（P1）
  - [ ] 8.1 实现操作频率年度热力图（Markdown表格形式）
  - [ ] 8.2 实现品种买卖排名表
  - [ ] 8.3 实现收益率分布文本直方图
  - [ ] 8.4 实现判断主题标签云（Markdown格式）

- [ ] Task 9: 端到端验证
  - [ ] 9.1 执行时间戳修复 → 验证操作时间线覆盖至少5个年份
  - [ ] 9.2 执行 index 构建 → 验证结构化索引和向量索引非空
  - [ ] 9.3 执行 stats → 验证统计摘要合理
  - [ ] 9.4 执行 persona → 验证导出的文档可读、数据准确
  - [ ] 9.5 执行 query "当前市场怎么看" → 验证返回有意义的语义检索结果
  - [ ] 9.6 增量验证：新增一个博客文件后执行 update + index --incremental → 验证旧数据不被重复处理

## Task Dependencies

- Task 2, 3, 4 依赖 Task 1（时间戳修复后才能构建正确的索引）
- Task 5 依赖 Task 3（需要向量索引能力）
- Task 6 依赖 Task 2, 4（需要结构化索引和统计摘要）
- Task 7 依赖 Task 2, 3, 4（需要索引模块就绪）
- Task 8 依赖 Task 2（需要结构化索引作为数据源）
- Task 9 依赖 Task 1-8（端到端验证）

## Parallelization

- Task 2（结构化索引）和 Task 3（向量索引）可并行开发（不同文件，无代码依赖）
- Task 2 和 Task 4（统计摘要）可并行（依赖相同数据源但逻辑独立）
- Task 6（画像导出）和 Task 5（检索升级）可并行（不共享代码）
- Task 7（主流程集成）需在 Task 2, 3, 4 完成后进行
- Task 8（可视化）可与 Task 5, 6 并行