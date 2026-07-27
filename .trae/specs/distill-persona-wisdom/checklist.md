# 验收清单

## 阶段一：代码Bug修复

- [ ] Task 1: opinion_matcher._filter_recent 按时间窗口过滤
  - [ ] 1.1 `_filter_recent` 解析 ISO 时间戳并与当前时间比较
  - [ ] 1.2 `recent_months` 从 config.yaml 读取，无硬编码
  - [ ] 1.3 超时记录标记为 `timed_out: true`，降级为通用匹配
  - [ ] 1.4 time 字段缺失或解析失败时默认保留（安全降级）
- [ ] Task 2: main.py 调用顺序
  - [ ] 2.1 cmd_update: record_extractor → opinion_extractor（正确顺序）
  - [ ] 2.2 cmd_analyze: 同样修复顺序
  - [ ] 2.3 opinion_extractor 能正确读取刚生成的中间产物

## 阶段二：数据再生

- [ ] Task 3: 强制重新摘要
  - [ ] 3.1 `summarize --force` 无报错
  - [ ] 3.2 state.json 中有记录的 operations 字段非空
  - [ ] 3.3 state.json 中有记录的 weibo_opinions 字段非空
- [ ] Task 4: 分析与产物生成
  - [ ] 4.1 `analyze` 无报错
  - [ ] 4.2 操作时间线.jsonl ≥ 100条
  - [ ] 4.3 近期判断库.jsonl ≥ 500条
  - [ ] 4.4 观点库.jsonl ≥ 500条，全部包含 `source_type` 和 `time` 字段
  - [ ] 4.5 source_type 分布包含 operation、observation、principle 三种

## 阶段三：人物画像构建

- [ ] Task 5: persona 子命令 + 画像生成
  - [ ] 5.1 `main.py persona` 子命令注册成功
  - [ ] 5.2 `persona_builder.py` 能加载三通道数据
  - [ ] 5.3 LLM prompt 设计合理，输出6个章节
  - [ ] 5.4 `E大人物画像.md` 生成成功且内容完整
  - [ ] 5.5 文档包含：身份档案、操作时间线、判断演化史、体系图谱、哲学体系、语言风格

## 阶段四：智慧查询接口

- [ ] Task 6: persona_query.py
  - [ ] 6.1 按topic检索观点库功能正常
  - [ ] 6.2 按品种检索操作记录功能正常
  - [ ] 6.3 按关键词检索判断库功能正常
  - [ ] 6.4 `query_persona` 返回结构包含 answer / sources / confidence
  - [ ] 6.5 answer 风格模拟E大的口语化表达
  - [ ] 6.6 CLI入口可运行：`python3 -m scripts.advisor.persona_query "问题"`

## 阶段五：日报告警管线

- [ ] Task 7: 日报管线
  - [ ] 7.1 advisor 目录配置正确，输出目录存在
  - [ ] 7.2 knowledge_base 的近期段落非空
  - [ ] 7.3 日报成功生成到指定目录
  - [ ] 7.4 日报"E大说过"段落包含：operation / observation / principle 三类来源

## 阶段六：端到端验证

- [ ] Task 8: 完整流程
  - [ ] 8.1 数据层：三通道 JSONL 文件非空且格式正确
  - [ ] 8.2 画像层：E大人物画像.md 可读、直观、包含6个章节
  - [ ] 8.3 查询层：query_persona 对"当前市场怎么看"类问题返回有意义的回答
  - [ ] 8.4 日报层：日报成功生成且包含E大智慧段落
  - [ ] 8.5 增量兼容：新增博客后 update 正确执行，不重复处理旧数据

## 整体质量

- [ ] 所有配置参数从 config.yaml 读取，无硬编码
- [ ] 强制重新摘要后旧数据被正确覆盖
- [ ] 增量更新机制不受影响
- [ ] 不修改原始博客文件（docs/blog/ 只读）
- [ ] 不破坏现有 distill 全量处理流程