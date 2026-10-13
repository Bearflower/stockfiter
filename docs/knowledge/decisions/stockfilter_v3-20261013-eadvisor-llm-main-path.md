---
id: stockfilter_v3-20261013-eadvisor-llm-main-path
类型: decision
项目: stockfilter_v3
标题: eadvisor 日报改用 LLM 主路径、规则引擎降级为 fallback
日期: 2026-09-16
标签: [eadvisor, 架构决策, LLM]
任务标识: eadvisor_LLM决策链路接入每日调度器_2026-09-16.md
状态: 已采纳
审查: 已审查
---

# eadvisor 日报改用 LLM 主路径、规则引擎降级为 fallback

## 背景
此前每日调度器 `scheduler.py` 只走规则引擎（`get_position_advice` + `generate_recommendations`），LLM 唯一用途是日报末尾「E大说过」观点匹配（装饰层）；`call_llm_analysis`（让 LLM 做仓位+ETF+解读）只被 `manual_scan.py` 调用，未接进每日管线。

## 备选方案
- A. 维持规则引擎为主，LLM 仅作观点匹配装饰层。
- B. LLM 主路径（`call_llm_analysis` 产出仓位+ETF+解读），规则引擎降级为 fallback。

## 决策
采用 B。新建 `scripts/advisor/daily_report.py` 统一封装「LLM 主路径 + 规则引擎降级 + 观点匹配 + 持仓展示 + 飞书推送」，供 scheduler.py / manual_scan.py 复用；`scheduler.run_scan_and_push` 改为 `build_daily_report()` + `push_feishu_message()`；`advisor_llm._validate_and_normalize` 增加 etf_code 白名单校验（丢弃 `etf_pool` 外的幻觉 ETF）。

## 理由
让 LLM 真正产出仓位 + ETF + 解读（推理+生成是规则引擎不可替代的）；同时消除 scheduler 与 manual_scan 两处重复的日报组装与推送代码。

## 后果
- 眼下收益：日报从规则引擎文本升级为 LLM 综合分析；消除重复代码；幻觉 ETF 被白名单拦截。
- 技术债/待验证（当时）：① 端到端 LLM 主路径未实跑（离线 mock 走降级路径）；② 部署方式待确认（git 中 Dockerfile.eadvisor 已删，但 docker-compose.yml 仍引用）；③ `call_llm_analysis` 的 `etf_recs` 纸面参数、`scheduler.py` sys.path 错误路径（非本次引入）。

## 适用范围与修订
distill_changying/scripts/advisor/{daily_report.py 新建, scheduler.py, manual_scan.py, advisor_llm.py}。
**后续进展**：遗留① 端到端 LLM 主路径于 2026-09-17 带 DEEPSEEK_API_KEY 实跑通过（见 eadvisor_增量蒸馏自动接线_2026-09-17.md「测试结果」）；遗留② 部署方式于 2026-09-17 确认为可构建并上线（同文件「部署确认」）。本决策至今有效、无替代条目。

## 关联与证据
- 本地补充来源: [handoff](../../handoffs/eadvisor_LLM决策链路接入每日调度器_2026-09-16.md)（不随 Git 同步）
- 持久证据: distill_changying/scripts/advisor/daily_report.py（新建）、scheduler.py；后续验证见 docs/handoffs/eadvisor_增量蒸馏自动接线_2026-09-17.md
- 必要证据摘要: py_compile 4 文件通过；5 项离线功能测试通过（含丢弃幻觉 ETF 999999）；端到端遗留于 9/17 实跑通过
- 相关: 无