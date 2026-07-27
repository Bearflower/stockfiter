# Tasks

- [ ] Task 1: 配置层 — 新增 ETF 池和 150 份框架配置
  - [ ] 1.1 在 `config/config.yaml` 的 `strategies.distill_changying.params` 下新增 `allocation` 和 `etf_pool` 配置段
  - [ ] 1.2 在 `distill_changying/scripts/advisor/config.yaml` 同步新增相同配置段
  - [ ] 1.3 更新 `bridge.py` 的 `build_advisor_config()` 将新配置段透传到 advisor config

- [ ] Task 2: 新建 ETF 操作推荐引擎 (`etf_recommend.py`)
  - [ ] 2.1 从配置读取 ETF 池、份数规则
  - [ ] 2.2 基于各指数 PE 分位，与 ETF 的买卖区间对比，生成操作建议（买入/卖出/持有）
  - [ ] 2.3 实现操作约束：不超总份数、不超单品种上限、已满不买、已空不卖
  - [ ] 2.4 输出结构化操作清单（JSON），按优先级排序

- [ ] Task 3: 重构 `position.py` — 整合 150 份框架
  - [ ] 3.1 `get_position_advice()` 输出新增"各 ETF 品种建议份数"维度
  - [ ] 3.2 保留原有的"A股/债券/现金 三维度"作为简化视图，新增"ETF 维度份数"作为详细视图
  - [ ] 3.3 份数分配逻辑：基于估值温度计算各类 ETF 的推荐份数

- [ ] Task 4: 重构 `llm_report.py` — 升级 Prompt 允许推荐 ETF
  - [ ] 4.1 移除 System Prompt 中"不推荐具体品种，不预测涨跌"的约束
  - [ ] 4.2 新增角色设定："你是 ETF 拯救世界的投资顾问，可推荐具体 ETF 品种和买卖份数"
  - [ ] 4.3 Prompt 中注入算法生成的 ETF 操作建议数据，LLM 基于此生成 E大风格的报告
  - [ ] 4.4 报告结构新增"ETF 操作建议"段落

- [ ] Task 5: 重构 `bridge.py` — 整合新推荐引擎
  - [ ] 5.1 `_format_scan_report()` 增加 ETF 操作建议段落
  - [ ] 5.2 `run_advisor_scan()` 流程中插入 ETF 推荐引擎调用
  - [ ] 5.3 飞书推送报告格式更新为含 ETF 操作建议

- [ ] Task 6: 升级交互式问答 (`chat.py`)
  - [ ] 6.1 更新 System Prompt 角色设定，允许推荐品种和份数
  - [ ] 6.2 注入 ETF 池信息和推荐引擎结果到对话上下文
  - [ ] 6.3 支持 ETF 品种追问（"XX 能买吗？""我该买什么？"）

- [ ] Task 7: 部署与测试
  - [ ] 7.1 本地测试：验证 ETF 推荐引擎输出正确性
  - [ ] 7.2 本地测试：`check`/`analyze`/`chat` 三个子命令均正常
  - [ ] 7.3 部署到服务器，执行扫描 + 推送全链路测试
  - [ ] 7.4 确认飞书日报包含 ETF 操作建议

# Task Dependencies

- Task 2、Task 3 均依赖 Task 1（需要配置）
- Task 4 依赖 Task 2、Task 3（需要推荐引擎和份数数据）
- Task 5 依赖 Task 2、Task 3、Task 4（整合所有新模块）
- Task 6 依赖 Task 1（需要新配置），可与 Task 2-5 并行
- Task 7 依赖 Task 1-6

# Parallelization

- Task 1.1、1.2 可并行（两个 config.yaml 分别写）
- Task 2 和 Task 3 可并行（推荐引擎 vs 份数框架，互不依赖核心逻辑）
- Task 6 可与 Task 2-5 并行（独立的 chat.py 改造）