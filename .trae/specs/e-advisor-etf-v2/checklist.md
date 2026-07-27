# Checklist

## 配置层

- [ ] `config/config.yaml` 包含 `allocation` 配置段（total_shares、nav_per_share）
- [ ] `config/config.yaml` 包含 `etf_pool` 配置段，默认至少 9 个 ETF 品种
- [ ] 每个 ETF 品种包含：code、name、index、category、buy_zone、sell_zone、shares_per_trade、max_shares
- [ ] `distill_changying/scripts/advisor/config.yaml` 同步包含上述配置
- [ ] `bridge.py` 正确将新配置段透传到 advisor config

## ETF 操作推荐引擎

- [ ] `etf_recommend.py` 存在且可独立运行
- [ ] 能正确从配置读取 ETF 池和份数规则
- [ ] 能基于 PE 分位生成买入/卖出/持有建议
- [ ] 操作约束生效：不超总份数、不超单品种上限
- [ ] 输出按优先级排序（买入区 > 卖出区 > 持有区）
- [ ] 同一品种不会同时出现买入+卖出建议

## 仓位建议重构

- [ ] `position.py` 输出含"各 ETF 品种建议份数"
- [ ] 保留原有的"A股/债券/现金"简化视图
- [ ] 份数分配逻辑正确（从配置读取，无硬编码）

## AI 分析报告重构

- [ ] `llm_report.py` 的 System Prompt 已移除"不推荐具体品种"约束
- [ ] System Prompt 包含 ETF 操作推荐角色设定
- [ ] Prompt 中注入算法生成的 ETF 操作建议数据
- [ ] 报告结构含"ETF 操作建议"段落
- [ ] LLM 失败时降级为纯算法报告

## 桥接层整合

- [ ] `_format_scan_report()` 包含 ETF 操作建议段落
- [ ] `run_advisor_scan()` 流程中正确调用 ETF 推荐引擎

## 交互式问答

- [ ] `chat.py` System Prompt 更新角色设定
- [ ] 对话上下文注入 ETF 池和推荐结果
- [ ] 支持 ETF 品种追问

## 飞书推送

- [ ] 飞书日报包含 ETF 操作建议段落
- [ ] LLM 不可用时降级为纯算法操作清单
- [ ] 日报末尾含免责声明

## 禁止硬编码

- [ ] 所有 ETF 品种数据从配置文件读取
- [ ] 所有估值区间阈值从配置文件读取
- [ ] 所有份数规则从配置文件读取
- [ ] 无业务逻辑中的固定数值

## 边界情况

- [ ] ETF 池为空时，推荐引擎不崩溃
- [ ] 某 ETF 关联指数无估值数据时，该 ETF 跳过
- [ ] 所有 ETF 都不在操作区间时，输出"今日无操作建议"
- [ ] LLM API 不可用时，分析报告降级为纯算法报告

## 整体

- [ ] `check` 子命令正常（纯算法，含 ETF 操作清单）
- [ ] `analyze` 子命令正常（LLM 报告，含 ETF 建议）
- [ ] `chat` 子命令正常（交互式，可追问 ETF）
- [ ] 服务器部署后扫描+推送全链路正常
- [ ] 代码注释与日志使用中文