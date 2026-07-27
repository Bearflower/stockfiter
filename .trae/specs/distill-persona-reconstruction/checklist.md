# Checklist

## Task 1: opinion_extractor 重构

- [ ] 1.1 `_load_state_json` 能正确加载 state.json 并过滤无效条目
- [ ] 1.2 `_extract_operations_from_state` 能提取所有发车帖的 operations 字段
- [ ] 1.3 `_extract_observations_from_state` 能提取所有微博精选的 weibo_opinions 字段
- [ ] 1.4 `_extract_principles_from_state` 能调用 LLM 从核心观点矿脉提取原则
- [ ] 1.5 `merge_opinions` 能正确合并三种类型并去重
- [ ] 1.6 观点库.jsonl 每条记录包含 `source_type`（operation/observation/principle）和 `time` 字段
- [ ] 1.7 观点库.jsonl 条目数显著增加（从 20 条到至少 100 条以上）
- [ ] 1.8 旧格式观点库（无 source_type）加载时自动标记为 principle

## Task 2: knowledge_base 增强

- [ ] 2.1 `load_recent_operations` 能正确读取操作时间线.jsonl 并按配置限条数
- [ ] 2.2 `load_recent_observations` 能正确读取近期判断库.jsonl 并按配置限条数
- [ ] 2.3 系统 Prompt 包含【E大近期操作动态】和【E大近期市场判断】段落
- [ ] 2.4 config.yaml 新增 `persona` 配置段，包含 max_observations_in_prompt、max_operations_in_prompt、recent_months

## Task 3: analyzer prompt 调整

- [ ] 3.1 `build_analysis_prompt` 的 prompt 文本新增聚焦原则的说明段
- [ ] 3.2 prompt 明确告知 LLM 操作记录和市场判断已由独立通道处理

## Task 4: main.py update 集成

- [ ] 4.1 cmd_update 在"跨文章深度分析"后自动调用 record_extractor.extract_all_and_save
- [ ] 4.2 cmd_update 在 record_extractor 后调用增强版 opinion_extractor.extract_opinions
- [ ] 4.3 增量处理正确：已处理文件不重复提取

## Task 5: opinion_matcher 简化

- [ ] 5.1 `match_opinions` 从统一观点库.jsonl 加载全部记录
- [ ] 5.2 匹配优先级正确: observation > operation > principle
- [ ] 5.3 移除三通道独立文件加载逻辑（操作时间线.jsonl、近期判断库.jsonl 不再单独加载）
- [ ] 5.4 Prompt 模板简化为单一输入源
- [ ] 5.5 source_type 缺失时默认为 principle（向下兼容）

## Task 6: 端到端验证

- [ ] 6.1 `python scripts/distill/main.py update` 执行无报错
- [ ] 6.2 观点库.jsonl 包含 operation、observation、principle 三种 source_type
- [ ] 6.3 观点库.jsonl 条目数从 20 条增加到至少 100 条
- [ ] 6.4 知识底座生成的系统 Prompt 包含近期操作和判断段落
- [ ] 6.5 opinion_matcher 返回的条目正确携带 source_type 字段
- [ ] 6.6 日报"E大说过"段落同时包含近期操作、近期判断和通用原则

## 整体质量

- [ ] 所有新配置参数从 config.yaml 读取，无硬编码
- [ ] 旧格式观点库.jsonl 可被自动识别和兼容
- [ ] 增量蒸馏流程完整：summarize → record_extract → opinion_extract