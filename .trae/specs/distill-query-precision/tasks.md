# Tasks

## 阶段一：操作记录 opinion 补全（P0 - 检索基础）

- [x] Task 1: PersonaQuery 加载操作记录时自动生成 opinion 字段
  - [x] 1.1 在 `_load_data` 方法中，对从操作时间线.jsonl 加载的记录，检查是否缺少 opinion 字段
  - [x] 1.2 自动拼接：`{plan}计划 {action}{fund}({code})`，无 code 时省略括号
  - [ ] 1.3 验证：运行 `_search_operations("中证500")`，返回记录的 opinion 字段非空

## 阶段二：检索分层与主题过滤

- [x] Task 2: 实现按 source_type 分层检索
  - [x] 2.1 修改 `_search_opinions` 方法，增加 `source_type` 参数
  - [x] 2.2 当指定 source_type 时，仅从对应子集检索
  - [x] 2.3 新增 `_search_all_layered(question)` 方法，分别检索三种类型并标注返回
  - [ ] 2.4 验证：查询 "中证500"，返回结果按 source_type 分组，每组至少一条

- [x] Task 3: 实现按 topic 主题过滤
  - [x] 3.1 在 `PersonaQuery.__init__` 中加载结构化索引的 by_topic 维度
  - [x] 3.2 新增 `_match_topic(question)` 方法：从查询关键词匹配已知主题
  - [x] 3.3 修改检索方法，当匹配到主题时，优先返回该主题的记录
  - [ ] 3.4 验证：查询 "红利"，返回结果的 topic 中 "红利" 相关记录排在前面

## 阶段三：向量模型升级

- [x] Task 4: 向量模型下载超时 + 降级优化
  - [x] 4.1 在 `_get_embedding_model` 中设置 30 秒超时（使用 threading）
  - [x] 4.2 捕获所有异常（含 ConnectionError、Timeout），不再仅限于 ImportError
  - [x] 4.3 降级到 sklearn TF-IDF 时记录日志说明原因
  - [ ] 4.4 验证：无网络时 30 秒内降级到 TF-IDF，不阻塞；有网络时使用 sentence-transformers

## 阶段四：排序优化

- [x] Task 5: 混合排序增加品种匹配加分
  - [x] 5.1 修改 `_hybrid_sort` 权重：BM25 0.2、向量 0.5、品种匹配 0.3
  - [x] 5.2 实现 `_fund_match_score` 计算品种匹配度
  - [x] 5.3 品种匹配逻辑：检查 keywords 和 opinion 中是否包含查询中的品种名
  - [ ] 5.4 验证：查询 "中证500" 时，中证500相关记录排在非中证500记录前面

## 阶段五：端到端验证

- [x] Task 6: 精度回测对比
  - [x] 6.1 用改进后的系统重新执行三个查询（中证500/红利/高估值风险）
  - [x] 6.2 对比改进前后的检索结果区分度
  - [x] 6.3 验证不同查询返回不同结果（不再全是同一批记录）
  - [x] 6.4 验证操作记录 opinion 非空

## Task Dependencies

- Task 2, 3 依赖 Task 1（需要 opinion 字段补全后才能正确分层）
- Task 5 依赖 Task 2, 3（需要分层和主题过滤就绪后才能优化排序）
- Task 4 可独立并行（不依赖其他任务）
- Task 6 依赖 Task 1-5

## Parallelization

- Task 1（opinion 补全）和 Task 4（向量模型升级）可并行开发
- Task 2（分层检索）和 Task 3（主题过滤）可并行开发
- Task 5（排序优化）依赖 Task 2, 3 完成后进行