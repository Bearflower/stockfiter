# Checklist

## 配置文件与 CLI

- [x] `scripts/distill/config.yaml` 存在且包含所有必要配置项（api、model、rate_limit、paths）
- [x] `scripts/distill/main.py` 支持 `summarize`、`analyze`、`distill` 三个子命令
- [x] 配置文件不存在时，程序使用默认值并输出警告而不崩溃

## 单篇提炼（第一层蒸馏）

- [x] 调用 LLM API 后能正确返回 summary（≤100字中文）、keywords（3-5个）、topic（主题分类）
- [x] 长文（超过 token 限制）自动截断处理，截断点在日志中记录
- [x] API 调用失败后按指数退避策略重试，最多重试次数由配置决定
- [x] LLM 返回非标准 JSON 时，程序能容错修复并解析，失败时跳过不崩溃
- [x] 已处理文件默认跳过不重复调用 API
- [x] `--force` 参数可强制重新处理已完成的文件

## 批量处理

- [x] 批量处理时显示实时进度（已处理/总数）
- [x] 速率限制（批次大小、延迟时间）按配置生效
- [x] `--year 2020` 筛选参数正确过滤文件
- [x] `--pattern "微博精选"` 筛选参数正确过滤文件
- [x] 中断后重新运行，已处理文件被跳过（断点续跑）

## 跨文章分析（第二层蒸馏）

- [x] 输出报告包含：核心命题聚类、十大核心观点、思维模型工具箱、价值观罗盘、演化轨迹、影响源推测、矛盾与张力分析
- [x] 摘要总量超过上下文窗口时，分批处理并合并结果

## 产物输出

- [x] `docs/distilled/blog-index.csv` 包含所有博客的摘要索引
- [x] `docs/distilled/核心观点矿脉.md` 包含完整的跨文章分析报告
- [x] `docs/distilled/金句库.md` 包含按主题分类的金句
- [x] `docs/distilled/README.md` 作为蒸馏产物的索引入口
- [x] 原始 `docs/blog/` 目录下的文件未被修改

## 整体

- [x] 模块级测试验证通过（file_handler、state_manager、batch_processor、analyzer、output_writer）
- [x] 所有硬编码参数均为 0（全由 config.yaml 或算法决定）