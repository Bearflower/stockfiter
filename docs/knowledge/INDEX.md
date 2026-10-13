# stockfilter_v3 知识库索引

最后巡检: 2026-10-13（冷启动初始化 + 同日 code-document-curator 放下时复核）
条目统计: 问题 3 / 决策 2 / 变更日志累计 0（冷启动仅建 changelog/.gitkeep，日志文件待首次收尾补建）

## 巡检状态汇总
| 状态   | 数量 | 判定规则              |
|--------|------|-----------------------|
| 正常   | 3    | 本次检查未发现需复查项，不代表重新验证全部结论 |
| 待复查 | 2    | 存在证据缺口、相关处理未执行或后续跟踪项等复查提示 |
| 孤立   | 3    | 无其他知识正文条目引用；索引入口不算入链，仅作提示 |

> 冷启动说明：5 条黄金样例的「审查」字段已于 2026-10-13 由 code-document-curator 完成放下时复核（逐条与来源 handoff 1:1 核对）：4 条直接通过；1 条（task-status-tz-roundtrip）先因来源 handoff（K线容器DNS故障排查与恢复_2026-09-17.md 的 2026-10-13 更新段）的遗留项（07:30 重扫恒 0 信号待用户决策、`update_kline_daily.py` 的 `_signal_handler` 仍用 `sys.exit(0)`）未在条目中保留而暂留「待审查」，补入「相关遗留」段后转「已审查」。至此 5 条样例均已「已审查」。「审查」仅表核对来源与表述，与状态互不混用。另按「状态」维度，2 条 issue 因修复未执行/待同步列为「待复查」（见下方清单），与「审查」是两个独立轴。本索引沿 Binance_quantitative_trading 样板结构建立。

## 问题模式库
| ID | 标题 | 标签 | 日期 | 状态 |
|----|------|------|------|------|
| stockfilter_v3-20261013-task-status-tz-roundtrip | task_status 时区往返错配致 wait 永久超时 | bug,调度器,时区 | 2026-10-13 | 已解决 |
| stockfilter_v3-20261013-adjustflag-misread | 复权口径 adjustflag="3" 被误当「后复权」导致回测收益率失真 | bug,数据口径,回测 | 2026-09-21 | 待复查 |
| stockfilter_v3-20261013-pg-container-vanished-dns | common_service_postgres 容器消失致 K 线容器 DNS 解析失败 | bug,部署,运维 | 2026-09-17 | 待复查 |

## 决策记录
| ID | 标题 | 日期 | 状态 | 替代入口 |
|----|------|------|------|----------|
| stockfilter_v3-20261013-obpc-index-filter-back-to-ma-position | OBPC 大盘过滤从 MACD 切回 V2.0 ma_position | 2026-09-18 | 已采纳 | — |
| stockfilter_v3-20261013-eadvisor-llm-main-path | eadvisor 日报改用 LLM 主路径、规则引擎降级为 fallback | 2026-09-16 | 已采纳 | — |

## 待复查清单
- [ ] stockfilter_v3-20261013-adjustflag-misread：生产修复（`data/fetcher.py` adjustflag="3"→"2"）未执行，需先评估对 OBPC 生产扫描指标计算的影响
- [ ] stockfilter_v3-20261013-pg-container-vanished-dns：本地修复版 one_click_deploy.sh 待同步到服务器并验证（MD5 + `bash -n`）；watchdog 长期稳定性待回归
- [x] stockfilter_v3-20261013-task-status-tz-roundtrip「审查」字段复核：2026-10-13 code-document-curator 完成来源 1:1 核对，补入来源两项遗留（07:30 重扫恒 0 信号待用户决策、`_signal_handler` 仍用 `sys.exit(0)`）后转「已审查」
- [ ] stockfilter_v3-20261013-task-status-tz-roundtrip 的相关遗留①（07:30 重扫恒 0 信号，`--signal-date` 与 `freshness_strategy=skip` 语义冲突）待用户决策后修复

## 孤立条目
- stockfilter_v3-20261013-adjustflag-misread（无其他知识正文条目引用）
- stockfilter_v3-20261013-pg-container-vanished-dns
- stockfilter_v3-20261013-eadvisor-llm-main-path

## 关联
- [项目文档入口](../README.md)