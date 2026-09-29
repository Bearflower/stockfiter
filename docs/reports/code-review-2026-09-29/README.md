# 全项目代码审查报告（2026-09-29）

结论：发现 23 项可行动问题，其中 P1/high 10 项、P2/medium 13 项。主要影响信号风控、投顾输入、数据任务可靠性和回测可信度。此报告没有修改业务代码。

基线：`main` / `d94b75e024bb778386beb0df055e3c8743b84baa`，同时审查工作树中未提交代码。审查方法为 open-code-review-delegate：OCR 负责文件预览与规则解析，由当前代理完成代码审查，没有调用 OCR 外部 LLM。

## 范围与限制

全仓扩展清单共 123 项：reviewed_files=110，skipped_files=13，coverage_rate=89.43%。这只是文件审查覆盖率，不是测试覆盖率，也不表示每条分支已经执行。

OCR workspace preview 原始总项数 339，其中 reviewable 22、excluded 317；22 个可审项逐一登记，已审 9、明确跳过 13。默认排除的两个 Python 诊断脚本另纳入全仓范围。其他排除项和原因保存在 ocr-preview.json。全仓范围含跟踪的 Python、YAML、Shell、Dockerfile、依赖文件，以及本次新增代码、运行映射和 .dockerignore；生成 HTML/视觉报告与文章索引不逐项审查。

阅读了 README.md、docs/README.md 和相关实现/配置。未对全部历史博文、图片、生成图表和文档叙述做内容审计；未连接生产数据库、访问行情服务、调用 LLM、发送飞书、部署容器或执行真实历史回测。数据库迁移与容器问题基于仓库配置及调用链确认，不能据此断言当前服务器的实际状态。

## 验证证据

- 本地隔离复现脚本 `reproduce.py`：使用临时目录、mock 依赖和合成 K 线，13 组结果均通过“缺陷仍存在”的断言；输出见 repro-results.json。修复后应改为正确行为断言，不能把此脚本通过视为项目通过验收。
- Python AST 解析检查与 Ruff F821/F822/F823/E9 检查；Ruff 检出 utils/logger.py 两处未定义 loguru 的类型注解，记录于 ruff.json，未作为主要业务缺陷重复计数。
- 未全仓执行 pytest：仓库中的若干诊断脚本在导入时即访问服务或有写入副作用，没有可直接假定安全的统一测试入口。

可在已安装当前项目依赖的环境复现：

```sh
PYTHONDONTWRITEBYTECODE=1 python3 docs/reports/code-review-2026-09-29/reproduce.py
```

## 问题清单

### R01 · P1 · 价格分位被作为 PE/PB 估值分位输出

位置：[distill_changying/scripts/advisor/market_data.py:520](/Users/yl/vscode/stockfilter_v3/distill_changying/scripts/advisor/market_data.py:520)；类别：bug；证据：代码路径确认。

成分股估值分支将指数收盘价历史分位同时赋给 pe_percentile 和 pb_percentile，后续温度计、ETF 阈值和日报把它们当成估值指标使用。即使盈利或净资产变化，价格分位也不能代表 PE/PB 分位，可能给出方向错误的估值建议。应计算相应估值时间序列；若保留价格指标，必须使用独立字段、标签及适配后的规则。

### R02 · P1 · 月度上限未计入本批待保存信号

位置：[scripts/daily_scan.py:264](/Users/yl/vscode/stockfilter_v3/scripts/daily_scan.py:264)；类别：bug；证据：隔离复现：monthly_limit。

扫描每个候选时都读取同一数据库月计数，直到扫描结束才 save_scan_result，DatabaseMonthlyCounter.increment 也不增加本地计数。因此已有 29 条、月限 30、日限 5 时仍新增 5 条，最终 34 条。应按剩余额度截取最终排序结果，并在保存时防止并发超额。

### R03 · P1 · 早晨重扫使用当天日期，推送却读取昨天文件

位置：[scripts/daily_scan.py:41](/Users/yl/vscode/stockfilter_v3/scripts/daily_scan.py:41)；类别：bug；证据：隔离复现：morning_scan_date；跨文件调用链确认。

scheduler_obpc 在 07:30 补全后调用同一 daily_scan，而这里只按星期计算日期，盘前也返回今天。feishu_push 则读取 signals_昨天.json；重扫新增结果写到今天文件，无法进入当晨推送，且扫描日期、冷却与月计数被错记。应由调度器传入本次已完成行情的交易日，让扫描、落库、文件和推送共享日期。

### R04 · P1 · 投顾知识库被排除出镜像且没有挂载产物目录

位置：[docker-compose.yml:84](/Users/yl/vscode/stockfilter_v3/docker-compose.yml:84)；类别：bug；证据：构建上下文与挂载配置确认；未运行 Docker 部署。

当按仓库 Dockerfile 构建新镜像时，.dockerignore:14 排除了整个 distill_changying/docs；此处只挂载 blog 和 distilled/logs，核心 .state.json、观点库、持仓及索引既未进入镜像也未从宿主机提供。投顾首次运行会缺失既有知识，重新生成的产物也随容器重建丢失。应持久化并初始化完整产物目录，验证干净部署与重建后的读取。

### R05 · P1 · OBPC 信号目录没有持久化

位置：[docker-compose.yml:57](/Users/yl/vscode/stockfilter_v3/docker-compose.yml:57)；类别：bug；证据：代码路径确认。

扫描写入 /app/signals，推送也从该目录读文件，但这里只挂载 /app/output。晚间扫描后、早间推送前重建容器会丢失信号文件；数据库已有记录还会参与去重/冷却，不能假定重扫能够恢复同一批信号。应挂载实际 signals 目录，或让推送从持久数据库重建待推送批次。

### R06 · P1 · 历史指数数据未校验新鲜度即参与当天风控

位置：[strategy/oversold_bounce/risk_control.py:515](/Users/yl/vscode/stockfilter_v3/strategy/oversold_bounce/risk_control.py:515)；类别：bug；证据：静态调用链确认；未断言生产库当前已过期。

get_context(date_str) 直接用最后一条指数收盘价，未比较其实际日期与本次交易日，随后却把 current_date 设为 date_str。指数更新失败或停止时，旧 MA/MACD 仍被视作当天风控数据；仓库默认调度链只调用股票更新和补全，独立 index_update.py 未接入。应调度指数更新并按交易日校验新鲜度，缺失时显式失败或采用已配置的降级策略。

### R07 · P1 · 止损按不可成交的阈值记账

位置：[scripts/backtest_obpc.py:670](/Users/yl/vscode/stockfilter_v3/scripts/backtest_obpc.py:670)；类别：bug；证据：隔离复现：gap_stop；仅合成 K 线，未运行历史回测。

日线低价触发止损后直接采用止损线作为成交价，未处理开盘已经跳空跌破止损线的情况。合成数据入场 100、次日开盘 80、最高 85，仍以 90 卖出，成交价超过全天最高价，低估损失并抬高收益。应先判断开盘缺口，再处理盘中触发及滑点；backtest_swing 同类逻辑也需统一检查。

### R08 · P1 · 截面回测使用未来波动率校准过去信号

位置：[scripts/backtest_cross_section.py:99](/Users/yl/vscode/stockfilter_v3/scripts/backtest_cross_section.py:99)；类别：bug；证据：隔离复现：future_volatility。

vol.median() 对整个回测区间计算中位数，再用于所有历史时点评分。追加未来行情即可改变已有日期的分数和仓位，导致未来信息泄漏，T+1 执行也不能消除此偏差。应使用截至决策时点可获得的滚动/扩展统计，或独立训练期常量，并增加前缀不变性检查。

### R09 · P1 · 持仓别名合并了不同资产

位置：[distill_changying/scripts/distill/merge_positions.py:48](/Users/yl/vscode/stockfilter_v3/distill_changying/scripts/distill/merge_positions.py:48)；类别：bug；证据：隔离复现：asset_alias。

NAME_MAP 将纳斯达克100、美国标普500归到德国DAX，另将信息技术归到全指金融。normalize_name 随后被用于持仓合并，不只是展示分组，故不同品种的买卖、净份数被混算，污染持仓和投顾输入。应仅合并同一资产的真实别名，用独立字段表达资产类别，并重新生成受影响的持仓。

### R10 · P1 · 仓库包含完整飞书机器人凭据

位置：[scripts/monitor.sh:16](/Users/yl/vscode/stockfilter_v3/scripts/monitor.sh:16)；类别：security；证据：本地源码确认；未验证远程有效性。

监控脚本把完整飞书 webhook 地址直接写入源码。任何获得源码或镜像的人都能获得该发送入口；是否仍有效未经外部验证。应改为注入环境变量或秘密管理，若仍有效则轮换并处理历史版本中的暴露。报告不复制令牌，也未尝试调用该 webhook。

### R11 · P2 · 重试分支无法处理数据库返回的日期字符串

位置：[scripts/data/update_kline_daily.py:272](/Users/yl/vscode/stockfilter_v3/scripts/data/update_kline_daily.py:272)；类别：bug；证据：隔离复现：retry，抓取 2 次、保存 0 批。

get_latest_kline_date 返回 YYYY-MM-DD 字符串；重试分支却把该字符串交给 datetime.combine，触发 TypeError。结果是首次失败后即使成功取得行情，有历史数据的股票仍无法补写。应像首次更新分支一样显式解析日期并统一返回类型。

### R12 · P2 · 请求超时后工作线程仍在运行

位置：[scripts/data/update_kline_daily.py:65](/Users/yl/vscode/stockfilter_v3/scripts/data/update_kline_daily.py:65)；类别：bug；证据：隔离复现：timeout_workers，连续两次超时后有 2 个并发工作线程。

shutdown(wait=False, cancel_futures=True) 不能取消已经运行的 get_kline。外层循环继续创建线程，并复用尚被旧请求占用的 Baostock 会话；连续超时会累积线程且可能交错访问同一连接。应采用底层可中断的连接超时，或隔离到可终止的工作进程，并在旧请求结束前禁止复用会话。

### R13 · P2 · 数据更新部分或全部失败仍上报成功

位置：[scripts/data/update_kline_daily.py:351](/Users/yl/vscode/stockfilter_v3/scripts/data/update_kline_daily.py:351)；类别：bug；证据：代码路径确认。

update_all_klines 会捕获单股错误、达到失败阈值后提前结束；数据库核验只记日志，main 随后无条件退出 0。scheduler_kline 的 check=True 因而通过并写 task_status=completed，使扫描在数据未完成时继续。应返回成功/失败/跳过和实际行情日期，按可配置的完整性条件决定退出状态与下游是否放行。

### R14 · P2 · JSON 数组代码块被截成第一个对象

位置：[distill_changying/scripts/shared/llm_utils.py:157](/Users/yl/vscode/stockfilter_v3/distill_changying/scripts/shared/llm_utils.py:157)；类别：bug；证据：隔离复现：fenced_json_array。

直接 JSON 解析失败后，先提取大括号对象再尝试去掉 Markdown 围栏。对于 ```json 包裹的多对象数组，这一步成功返回首个 dict，其余对象静默丢失；期望 list 的观点提取和匹配也会进入错误分支。应优先解析完整代码块，并保持顶层数组类型。

### R15 · P2 · 摘要 CSV 回读丢失文件名与年份

位置：[distill_changying/scripts/distill/analyzer.py:45](/Users/yl/vscode/stockfilter_v3/distill_changying/scripts/distill/analyzer.py:45)；类别：bug；证据：隔离复现：csv_bom；哈希来源静态确认。

export_to_csv 使用 utf-8-sig 写入，而这里用 utf-8 读取，使首列表头成为带 BOM 的 file；row.get("file") 返回空字符串，year 随之为空，破坏来源和年度分析。修正 BOM 后还需处理导出端把文件名哈希 identifier 当 file 的问题，保存真实来源名及日期。

### R16 · P2 · 摘要失败也被永久标记为已处理

位置：[distill_changying/scripts/distill/batch_processor.py:174](/Users/yl/vscode/stockfilter_v3/distill_changying/scripts/distill/batch_processor.py:174)；类别：bug；证据：隔离复现：failed_summary_not_retried，两轮仅调用一次摘要服务。

summarize_with_retry 重试耗尽返回失败占位摘要，异常分支同样生成占位内容，但这里仍无条件 mark_processed。后续增量任务只检查 identifier 是否存在，短暂 API 故障因此永久变成知识缺失。应将成功与失败状态分开，失败项保留重试资格，不作为有效产物导出。

### R17 · P2 · 仓位归一化重新突破已校验的边界

位置：[distill_changying/scripts/advisor/advisor_llm.py:318](/Users/yl/vscode/stockfilter_v3/distill_changying/scripts/advisor/advisor_llm.py:318)；类别：bug；证据：隔离复现：llm_validation。

先逐项约束范围，再按总和缩放，却未重新校验边界。模型返回股票80/债券10/现金60时，归一化结果为54/7/39，债券低于自身10%的下限，仍作为合格建议输出。应使用同时满足总和与各项上下界的分配算法，无法满足时拒绝结果或回退。

### R18 · P2 · 买卖份数校验允许负数

位置：[distill_changying/scripts/advisor/advisor_llm.py:361](/Users/yl/vscode/stockfilter_v3/distill_changying/scripts/advisor/advisor_llm.py:361)；类别：bug；证据：隔离复现：llm_validation。

买卖建议只限制 shares 的上限，未验证正整数和类型，buy + shares=-2 会原样进入输出；非数值还可能在比较时抛错。应先校验动作和有限整数范围，再按每次份数及持仓余量约束，不合法结果转为明确失败或 hold。

### R19 · P2 · 盘中止损覆盖了已应在开盘执行的卖出

位置：[scripts/backtest_swing.py:178](/Users/yl/vscode/stockfilter_v3/scripts/backtest_swing.py:178)；类别：bug；证据：隔离复现：swing_exit_order。

已有 T-1 卖出信号时本应先在今日开盘离场，但代码先检查全天 low，再考虑开盘卖出。入场100、次日开盘105、盘中最低80时，代码以92止损，代替本应先发生的105开盘成交，改变交易收益及两类退出策略比较。应按时间先执行隔夜信号，再对剩余持仓检查盘中风控。

### R20 · P2 · 波段最大回撤采用了与成交不一致的净值

位置：[scripts/backtest_swing.py:254](/Users/yl/vscode/stockfilter_v3/scripts/backtest_swing.py:254)；类别：bug；证据：代码路径确认。

交易收益按开盘/止损成交计算，但回撤对任何 held=True 的整天都使用收盘对前收盘涨跌，既计入买入前的隔夜跳空，也计入开盘卖出后的涨跌，并未扣交易成本。因而最大回撤与同表的总收益不是同一资金曲线。应从现金、实际成交和每日持仓市值生成统一净值，再计算全部绩效指标。

### R21 · P2 · 月度名额按股票顺序而非信号日期分配

位置：[scripts/backtest_obpc.py:857](/Users/yl/vscode/stockfilter_v3/scripts/backtest_obpc.py:857)；类别：bug；证据：代码路径确认。

引擎逐只股票跑完整历史，所有股票共用一个 monthly_counter。前一股票月末的信号可能先占满名额，导致后一股票月初的信号被过滤，结果依赖股票列表排序且不符合实时扫描。应先生成候选，再按交易日和同日评分排序统一应用月限及日限。

### R22 · P2 · 旧表迁移前先创建依赖新字段的索引

位置：[data/database.py:113](/Users/yl/vscode/stockfilter_v3/data/database.py:113)；类别：bug；证据：代码顺序确认；未连接 PostgreSQL 执行迁移。

DatabaseManager.__init__ 先 _create_tables 再 _migrate_add_frequency。已有 V2 klines 表缺少 frequency 时，CREATE TABLE IF NOT EXISTS 不会加列，这个索引创建先失败，后续迁移永远不会执行。应在依赖新列的索引之前完成幂等迁移，并验证旧模式与空库初始化。

### R23 · P2 · 主入口调用子 CLI 导致 --all 无法执行完整流水线

位置：[main.py:72](/Users/yl/vscode/stockfilter_v3/main.py:72)；类别：bug；证据：代码路径确认。

run_kline_update 直接调用 updater.main；后者重新解析同一 sys.argv，仅接受 --days，遇到主入口 --all/--update 会以参数错误退出。即便绕过重复解析，子 main 末尾 os._exit(0) 仍终止整个进程，后续扫描与推送无法运行。应把业务函数与 CLI 解析分离，主入口调用可正常返回的更新函数。

## 审查清单

完整机器可读结果（包含要求的 path/content/start_line/end_line/category/severity 字段）、文件哈希及 OCR workspace 的 (path, status) 清单见 [review.json](review.json)。以下 reviewed 表示完成静态审查；诊断脚本仅审阅，不实际执行。

| 文件 | 状态 | 跳过原因 |
|---|---|---|
| `.archify_candidate_arch.json` | skipped | 生成的架构/视觉验证产物或文章索引，不属于策略运行逻辑；未逐项验证其展示和内容正确性 |
| `.dockerignore` | reviewed | — |
| `.github/workflows/deploy.yml` | reviewed | — |
| `.gitignore` | reviewed | — |
| `Dockerfile.eadvisor` | reviewed | — |
| `Dockerfile.kline` | reviewed | — |
| `Dockerfile.obpc` | reviewed | — |
| `backtest_july_signals.py` | reviewed | — |
| `config/config.yaml` | reviewed | — |
| `data/__init__.py` | reviewed | — |
| `data/data_source.py` | reviewed | — |
| `data/database.py` | reviewed | — |
| `data/fetcher.py` | reviewed | — |
| `data/kline_service.py` | reviewed | — |
| `data/stock_list.py` | reviewed | — |
| `distill_changying/_final_backtest.py` | reviewed | — |
| `distill_changying/_verify.py` | reviewed | — |
| `distill_changying/docs/distilled/.meta.yaml` | reviewed | — |
| `distill_changying/docs/distilled/品种别名映射.json` | reviewed | — |
| `distill_changying/requirements-advisor.txt` | reviewed | — |
| `distill_changying/scripts/advisor/__init__.py` | reviewed | — |
| `distill_changying/scripts/advisor/advisor_llm.py` | reviewed | — |
| `distill_changying/scripts/advisor/backfill_market_condition.py` | reviewed | — |
| `distill_changying/scripts/advisor/cache.py` | reviewed | — |
| `distill_changying/scripts/advisor/chat.py` | reviewed | — |
| `distill_changying/scripts/advisor/config.py` | reviewed | — |
| `distill_changying/scripts/advisor/config.yaml` | reviewed | — |
| `distill_changying/scripts/advisor/daily_report.py` | reviewed | — |
| `distill_changying/scripts/advisor/etf_recommend.py` | reviewed | — |
| `distill_changying/scripts/advisor/knowledge_base.py` | reviewed | — |
| `distill_changying/scripts/advisor/llm_report.py` | reviewed | — |
| `distill_changying/scripts/advisor/main.py` | reviewed | — |
| `distill_changying/scripts/advisor/manual_scan.py` | reviewed | — |
| `distill_changying/scripts/advisor/market_data.py` | reviewed | — |
| `distill_changying/scripts/advisor/opinion_matcher.py` | reviewed | — |
| `distill_changying/scripts/advisor/persona_query.py` | reviewed | — |
| `distill_changying/scripts/advisor/position.py` | reviewed | — |
| `distill_changying/scripts/advisor/position_display.py` | reviewed | — |
| `distill_changying/scripts/advisor/scheduler.py` | reviewed | — |
| `distill_changying/scripts/advisor/temperature.py` | reviewed | — |
| `distill_changying/scripts/distill/__init__.py` | reviewed | — |
| `distill_changying/scripts/distill/analyzer.py` | reviewed | — |
| `distill_changying/scripts/distill/batch_processor.py` | reviewed | — |
| `distill_changying/scripts/distill/config.py` | reviewed | — |
| `distill_changying/scripts/distill/config.yaml` | reviewed | — |
| `distill_changying/scripts/distill/file_handler.py` | reviewed | — |
| `distill_changying/scripts/distill/image_ocr.py` | reviewed | — |
| `distill_changying/scripts/distill/main.py` | reviewed | — |
| `distill_changying/scripts/distill/merge_positions.py` | reviewed | — |
| `distill_changying/scripts/distill/metadata.py` | reviewed | — |
| `distill_changying/scripts/distill/opinion_extractor.py` | reviewed | — |
| `distill_changying/scripts/distill/output_writer.py` | reviewed | — |
| `distill_changying/scripts/distill/persona_builder.py` | reviewed | — |
| `distill_changying/scripts/distill/persona_index.py` | reviewed | — |
| `distill_changying/scripts/distill/persona_stats.py` | reviewed | — |
| `distill_changying/scripts/distill/persona_viz.py` | reviewed | — |
| `distill_changying/scripts/distill/position_extractor.py` | reviewed | — |
| `distill_changying/scripts/distill/record_extractor.py` | reviewed | — |
| `distill_changying/scripts/distill/state_manager.py` | reviewed | — |
| `distill_changying/scripts/distill/summarizer.py` | reviewed | — |
| `distill_changying/scripts/distill/verify_products.py` | reviewed | — |
| `distill_changying/scripts/shared/__init__.py` | reviewed | — |
| `distill_changying/scripts/shared/llm_utils.py` | reviewed | — |
| `docker-compose.yml` | reviewed | — |
| `docs/archify/eadvisor-business.visual-check.html` | skipped | 生成的架构/视觉验证产物或文章索引，不属于策略运行逻辑；未逐项验证其展示和内容正确性 |
| `docs/archify/eadvisor-business.visual-check.json` | skipped | 生成的架构/视觉验证产物或文章索引，不属于策略运行逻辑；未逐项验证其展示和内容正确性 |
| `docs/archify/eadvisor-container.visual-check.html` | skipped | 生成的架构/视觉验证产物或文章索引，不属于策略运行逻辑；未逐项验证其展示和内容正确性 |
| `docs/archify/eadvisor-container.visual-check.json` | skipped | 生成的架构/视觉验证产物或文章索引，不属于策略运行逻辑；未逐项验证其展示和内容正确性 |
| `docs/archify/eadvisor-dataflow.html` | skipped | 生成的架构/视觉验证产物或文章索引，不属于策略运行逻辑；未逐项验证其展示和内容正确性 |
| `docs/archify/eadvisor-dataflow.json` | skipped | 生成的架构/视觉验证产物或文章索引，不属于策略运行逻辑；未逐项验证其展示和内容正确性 |
| `docs/archify/eadvisor-dataflow.visual-check.html` | skipped | 生成的架构/视觉验证产物或文章索引，不属于策略运行逻辑；未逐项验证其展示和内容正确性 |
| `docs/archify/eadvisor-dataflow.visual-check.json` | skipped | 生成的架构/视觉验证产物或文章索引，不属于策略运行逻辑；未逐项验证其展示和内容正确性 |
| `docs/designs/eadvisor-pipeline.html` | skipped | 生成的架构/视觉验证产物或文章索引，不属于策略运行逻辑；未逐项验证其展示和内容正确性 |
| `docs/designs/eadvisor-pipeline.workflow.json` | skipped | 生成的架构/视觉验证产物或文章索引，不属于策略运行逻辑；未逐项验证其展示和内容正确性 |
| `docs/designs/gen_docs.py` | reviewed | — |
| `docs/长赢指数投资/_article_index.json` | skipped | 生成的架构/视觉验证产物或文章索引，不属于策略运行逻辑；未逐项验证其展示和内容正确性 |
| `docs/长赢指数投资/_article_links.json` | skipped | 生成的架构/视觉验证产物或文章索引，不属于策略运行逻辑；未逐项验证其展示和内容正确性 |
| `engine/__init__.py` | reviewed | — |
| `engine/registry.py` | reviewed | — |
| `engine/scheduler.py` | reviewed | — |
| `main.py` | reviewed | — |
| `notification/__init__.py` | reviewed | — |
| `notification/service.py` | reviewed | — |
| `requirements-eadvisor.txt` | reviewed | — |
| `requirements-kline.txt` | reviewed | — |
| `requirements-obpc.txt` | reviewed | — |
| `requirements.txt` | reviewed | — |
| `scripts/backtest_cross_section.py` | reviewed | — |
| `scripts/backtest_obpc.py` | reviewed | — |
| `scripts/backtest_swing.py` | reviewed | — |
| `scripts/check_stock_obpc.py` | reviewed | — |
| `scripts/compare_sources.py` | reviewed | — |
| `scripts/daily_scan.py` | reviewed | — |
| `scripts/data/backfill_all_history.py` | reviewed | — |
| `scripts/data/backfill_diag.py` | reviewed | — |
| `scripts/data/backfill_history.py` | reviewed | — |
| `scripts/data/baostock_diff_test.py` | reviewed | — |
| `scripts/data/baostock_ping.py` | reviewed | — |
| `scripts/data/baostock_replicate.py` | reviewed | — |
| `scripts/data/index_update.py` | reviewed | — |
| `scripts/data/quick_backfill.py` | reviewed | — |
| `scripts/data/speed_test.py` | reviewed | — |
| `scripts/data/update_kline_daily.py` | reviewed | — |
| `scripts/diagnose_obpc.py` | reviewed | — |
| `scripts/feishu_push.py` | reviewed | — |
| `scripts/import_index_data.py` | reviewed | — |
| `scripts/monitor.sh` | reviewed | — |
| `scripts/review_obpc.py` | reviewed | — |
| `scripts/scheduler_kline.py` | reviewed | — |
| `scripts/scheduler_obpc.py` | reviewed | — |
| `scripts/verify_fix.py` | reviewed | — |
| `strategy/__init__.py` | reviewed | — |
| `strategy/base.py` | reviewed | — |
| `strategy/oversold_bounce/__init__.py` | reviewed | — |
| `strategy/oversold_bounce/risk_control.py` | reviewed | — |
| `strategy/oversold_bounce/strategy.py` | reviewed | — |
| `tmp_check_kline.py` | reviewed | — |
| `tmp_check_retrace.py` | reviewed | — |
| `tmp_check_scan.py` | reviewed | — |
| `tmp_check_scan2.py` | reviewed | — |
| `utils/__init__.py` | reviewed | — |
| `utils/logger.py` | reviewed | — |
| `utils/trade_calendar.py` | reviewed | — |

## 修复顺序建议

先处理凭据暴露、信号额度/日期、投顾数据含义与持久化，再修复行情失败处理、知识库增量与数值校验；在修复未来信息和成交/净值模型之前，不应把现有回测绩效当作策略有效性的验证证据。修复、提交和部署均未在本次审查中执行。
