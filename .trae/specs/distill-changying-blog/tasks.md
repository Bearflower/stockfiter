# Tasks

- [x] Task 1: 创建项目骨架与配置系统
  - [x] 1.1 创建 `scripts/distill/` 目录结构
  - [x] 1.2 编写 `config.yaml` 配置文件，包含 API 参数、模型选择、速率限制、路径配置等
  - [x] 1.3 编写 `config.py` 配置加载模块，支持从 YAML 读取和默认值回退
  - [x] 1.4 编写 `main.py` CLI 入口骨架，支持 `summarize`、`analyze`、`distill` 三个子命令

- [x] Task 2: 实现单篇博客提炼引擎（第一层蒸馏）
  - [x] 2.1 编写 `summarizer.py`，实现调用 LLM API 生成摘要、关键词、主题分类
  - [x] 2.2 实现长文截断处理（超过 token 限制时自动截断）
  - [x] 2.3 实现 API 错误处理与指数退避重试机制
  - [x] 2.4 实现 JSON 响应解析与容错处理
  - [x] 2.5 编写 `file_handler.py`，实现 Markdown 文件的读取、内容提取、文件名安全处理

- [x] Task 3: 实现批量处理与状态管理
  - [x] 3.1 编写 `batch_processor.py`，支持遍历目录、进度显示、速率控制
  - [x] 3.2 支持 `--year` 和 `--pattern` 筛选参数
  - [x] 3.3 编写 `state_manager.py`，记录已处理文件状态，支持断点续跑和 `--force` 参数
  - [x] 3.4 将单篇提炼结果输出为 `docs/distilled/blog-index.csv` 汇总索引表

- [x] Task 4: 实现跨文章深度分析引擎（第二层蒸馏）
  - [x] 4.1 编写 `analyzer.py`，读取所有单篇摘要，按需求文档的 Prompt 模板发送给 LLM
  - [x] 4.2 处理超长 Prompt（摘要总量超过上下文窗口时的分批策略）
  - [x] 4.3 解析 LLM 返回的分析结果，输出为结构化 Markdown

- [x] Task 5: 实现蒸馏产物输出
  - [x] 5.1 编写 `output_writer.py`，将跨文章分析结果写入 `docs/distilled/核心观点矿脉.md`
  - [x] 5.2 生成 `docs/distilled/README.md` 索引文件
  - [x] 5.3 提取金句并生成 `docs/distilled/金句库.md`

- [x] Task 6: 测试与验证
  - [x] 6.1 用少量文件进行模块级测试，验证核心逻辑
  - [x] 6.2 验证 config.yaml 配置项均正确生效（路径解析、默认值回退）
  - [x] 6.3 验证状态管理的断点续跑逻辑（load_state/save_state/mark_processed）

# Task Dependencies

- Task 2 依赖 Task 1
- Task 3 依赖 Task 1、Task 2
- Task 4 依赖 Task 1、Task 3（需要已生成的摘要数据）
- Task 5 依赖 Task 4
- Task 6 依赖 Task 1-5（可并行编写，但运行测试需等前序完成）

# Parallelization

- Task 2.1 和 2.5 可并行开发
- Task 3.1 和 3.3 可并行开发
- Task 5.1、5.2、5.3 可并行开发