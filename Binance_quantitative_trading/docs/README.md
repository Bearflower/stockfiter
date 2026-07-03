# Binance 量化交易系统 - 文档索引

> **版本**: v6.20
> **更新日期**: 2026-06-24
> **维护者**: 开发团队

---

## 目录

- [架构设计](#架构设计)
- [部署运维](#部署运维)
- [需求文档](#需求文档)
- [迁移方案](#迁移方案)
- [设计文档](#设计文档)
- [测试报告](#测试报告)
- [Dashboard 文档](#dashboard-文档)
- [StratTuneAI 调优系统](#strattuneai-调优系统)

---

## 架构设计

### 系统架构

- [系统架构设计](architecture/系统架构设计.md) - 整体系统架构、模块划分、技术选型
- [数据库设计](architecture/数据库设计.md) - 数据库表结构、索引设计、数据关系
- [API接口设计](architecture/API接口设计.md) - RESTful API 设计、接口规范

### 策略设计

- [新币做空策略回测框架设计](architecture/新币做空策略回测框架设计.md) - 回测框架架构设计

### AI 调优系统

- [StratTuneAI 架构设计](architecture/StratTuneAI架构设计.md) - 多策略AI调优系统架构设计

---

## 部署运维

### 部署指南

- [统一交易系统部署指南](deployment/统一交易系统部署指南.md) - 完整部署流程、环境配置
- [部署指南](deployment/部署指南.md) - 基础部署说明
- [Docker容器编排](deployment/Docker容器编排.md) - Docker 部署方案

### 配置管理

- [环境变量配置](deployment/环境变量配置.md) - 环境变量说明、配置方法
- [代码同步检查机制更新指南](deployment/代码同步检查机制更新指南.md) - 代码同步检查流程

---

## 需求文档

### BTC/ETH 策略

- [项目需求迭代文档](requirements/btc_eth/项目需求迭代文档.md) - 完整需求迭代历史
- [v6.16 需求](requirements/btc_eth/v6.16.md) - v6.16 版本需求
- [v6.16.1 需求](requirements/btc_eth/v6.16.1.md) - v6.16.1 版本需求
- [v6.16.2 需求](requirements/btc_eth/v6.16.2.md) - v6.16.2 版本需求
- [v6.16.3 需求](requirements/btc_eth/v6.16.3.md) - v6.16.3 版本需求
- [v6.16.4 需求](requirements/btc_eth/v6.16.4.md) - v6.16.4 版本需求
- [v6.16.7 需求](requirements/btc_eth/v6.16.7.md) - v6.16.7 版本需求
- [v6.16.8 完整方案](requirements/btc_eth/v6.16.8 完整方案.md) - v6.16.8 版本完整方案
- [v6.16.10 最终交易策略规范](requirements/btc_eth/v6.16.10 最终交易策略规范（全仓自动化版 · 500U 阶段一专用）.md) - v6.16.10 版本需求规范

### 网格交易策略

- [网格交易 v2.0](requirements/grid/网格交易v2.0.md) - 网格交易策略 v2.0 需求
- [网格交易 v2.0 补充](requirements/grid/网格交易v2.0补充.md) - 补充需求说明
- [网格交易 V2.1](requirements/grid/Grid_Trading_V2.1.md) - V2.1 双重时间框架 + 波动率异常检测
- [网格交易 V2.2](requirements/grid/Grid_Trading_V2.2.md) - V2.2 参数优化升级（最新版）
- [网格信号灯 V2.0 实施方案](requirements/grid/网格信号灯 V2.0 实施方案（ETHUSDT 专版）.md) - ETHUSDT 专版实施方案
- [网格策略迭代记录](requirements/grid/网格策略迭代记录.md) - 迭代历史记录

### 新币做空策略

- [新币做空 4.0](requirements/new_coin/新币做空4.0.md) - 新币做空策略 v4.0 需求
- [新币做空策略 V4.0 完整版](requirements/new_coin/新币做空策略 V4.0 完整版.md) - 完整版需求文档（内容已更新至 V4.1，含信号质量优化与限价单改造）
- [新币做空策略 V4.0 情绪面优化版](requirements/new_coin/新币做空策略 V4.0 （情绪面优化版）.md) - 情绪面优化版本
- [V4.0 最终版问题解释](requirements/new_coin/V4.0 最终版问题解释.md) - 问题说明和解决方案

### HRS 混合反转策略

- [HRS V1.9.1 完整文档](requirements/HRS/混合反转策略（HRS）V1.9.md) - 最新版完整策略文档（含23+功能点修复）
- [HRS V1.8 文档](requirements/HRS/混合反转策略（HRS）V1.8.md) - V1.8 版本文档（新增状态持久化）
- [HRS V1.1 文档](requirements/HRS/混合反转策略（HRS）V1.1.md) - V1.1 版本文档
- [HRS 澄清记录](requirements/HRS/澄清01.md) - 做多止损、K线预热、形态镜像等澄清

### 报表需求

- [日报需求说明](requirements/daily_report/需求说明.md) - 日报功能需求
- [周报需求说明](requirements/weekly_report/需求说明.md) - 周报功能需求

### StratTuneAI 调优系统

- [PRD-多策略AI调优系统](requirements/StratTuneAI/PRD-多策略AI调优系统.md) - 产品需求文档，系统功能、数据模型、审批流程
- [多策略AI调优系统技术路线](requirements/StratTuneAI/多策略AI调优系统技术路线.md) - 技术实现路线与里程碑

---

## 迁移方案

- [迁移方案总览](migration/README.md) - 迁移方案索引
- [BTC_ETH 策略迁移方案](migration/BTC_ETH策略迁移方案.md) - BTC/ETH 策略迁移详细方案
- [BTC_ETH 策略迁移完成报告](migration/BTC_ETH策略迁移完成报告.md) - 迁移完成总结报告
- [新币做空策略迁移方案](migration/新币做空策略迁移方案.md) - 新币做空策略迁移方案
- [网格交易策略迁移方案](migration/网格交易策略迁移方案.md) - 网格交易策略迁移方案

---

## 设计文档

### Dashboard 设计

- [Dashboard 架构设计](design/dashboard_architecture.md) - Dashboard 系统架构、技术选型、API 设计
- [Dashboard UI 设计](design/dashboard_ui_design.md) - UI 设计规范、交互设计、视觉设计

---

## 测试报告

### BTC/ETH 策略测试

- [所有版本对比报告](reports/btc_eth/all_versions_comparison_report.md) - 所有版本性能对比
- [v6.16 对比报告](reports/btc_eth/v6.16_comparison_report.md) - v6.16 版本对比
- [v6.16.1 对比报告](reports/btc_eth/v6.16.1_comparison_report.md) - v6.16.1 版本对比
- [v6.16.4 vs v6.16.3 对比](reports/btc_eth/v6164_vs_v6163_comparison.md) - v6.16.4 与 v6.16.3 对比
- [部署日志](reports/btc_eth/deployment_log.md) - 部署过程记录

### 新币做空策略测试

- [风控机制 P0 问题修复总结](reports/new_coin/风控机制P0问题修复总结.md) - 风控问题修复报告

### 其他测试报告

- [BTCUSDT 交易错误修复报告](reports/BTCUSDT交易错误修复报告_20260515.md) - 交易错误修复记录
- [BTCUSDT 配置优化建议](reports/BTCUSDT配置优化建议_20260515.md) - 配置优化建议
- [ETHUSDT 信号生成功能测试报告](reports/ETHUSDT信号生成功能测试报告.md) - 信号生成测试
- [v6.16.7 止损止盈单修复测试报告](reports/v6.16.7_止损止盈单修复测试报告.md) - 止损止盈单修复测试
- [精度验证报告](reports/精度验证报告_20260515.md) - 精度验证测试
- [评分计算逻辑检查报告](reports/评分计算逻辑检查报告_20260515.md) - 评分逻辑检查
- [风控机制审查报告](reports/风控机制审查报告_20260509.md) - 风控机制审查

---

## Dashboard 文档

### 核心文档

- [Dashboard README](../dashboard/README.md) - Dashboard 使用说明、快速启动、API 接口
- [Dashboard 架构设计](design/dashboard_architecture.md) - 系统架构、技术选型、API 设计
- [Dashboard UI 设计](design/dashboard_ui_design.md) - UI 设计规范、交互设计、视觉设计

### 功能模块

Dashboard 是一个轻量级的交易数据可视化看板，主要功能包括:

- **数据展示**: 总览数据、策略详情、币种明细、趋势图表
- **实时更新**: 支持日报/周报/月报数据实时更新
- **可视化**: 使用 ECharts 进行金融级可视化
- **易部署**: 载量级架构，易于部署和维护
- **安全可控**: IP 白名单控制，API 限流保护

### 技术栈

| 层次 | 技术选型 | 说明 |
|------|---------|------|
| **前端** | HTML + ECharts + 原生JS | 轻量级，无需打包工具 |
| **后端** | FastAPI + Uvicorn | 高性能异步框架 |
| **数据源** | 复用现有采集器 | 避免重复开发 |
| **缓存** | 内存缓存（TTL） | 轻量级，日报5分钟/周报30分钟/月报2小时 |
| **部署** | Nginx + systemd | 标准部署方式 |

---

## StratTuneAI 调优系统

### 核心文档

- [PRD-多策略AI调优系统](requirements/StratTuneAI/PRD-多策略AI调优系统.md) - 完整产品需求文档，包含功能需求、参数白名单、审批流程、异常处理
- [StratTuneAI 架构设计](architecture/StratTuneAI架构设计.md) - 系统架构设计，五层闭环架构（采集层/记忆层/决策层/审批层/执行层）
- [多策略AI调优系统技术路线](requirements/StratTuneAI/多策略AI调优系统技术路线.md) - 技术实现路线与里程碑计划

### 系统概述

StratTuneAI 是一个 AI 驱动的多策略参数自动调优系统，核心目标：

- **自动化分析**：每周自动采集各策略的周度表现数据，生成标准化健康报告
- **AI 辅助决策**：利用 DeepSeek-v4-pro 大模型分析报告，结合历史调优记忆，生成参数调整建议
- **人工审批在环**：所有 AI 建议必须经人工确认后方可生效，杜绝 AI 幻觉导致的灾难性参数
- **知识沉淀**：每次调优过程结构化记录，形成可追溯的策略进化日志
- **安全兜底**：自动回滚机制保护策略在极端情况下的安全

### 第一期覆盖策略

| 策略 | 策略ID | 适配器 |
|------|--------|--------|
| MTPCS趋势策略 | btc_eth | mtpcs_adapter |
| 新币做空策略 | new_coin | new_coin_adapter |

### 技术栈

| 层次 | 技术选型 | 说明 |
|------|---------|------|
| **后端语言** | Python 3.10+ | 与主项目一致 |
| **定时调度** | APScheduler 3.x | 每周日 23:55 触发 |
| **AI 接口** | DeepSeek-v4-pro (deepseek-v4-pro)，启用思考模式 (thinking_mode: enabled, reasoning_effort: high) | 通过 OpenAI SDK 调用 |
| **数据库** | 复用现有 PostgreSQL | trading schema，新建 strategy_memory 表 |
| **通知** | 复用飞书通知服务 | 新增调优专用 Webhook |
| **容器化** | Docker | 独立 Dockerfile，与主系统解耦 |

---

## 文档维护说明

### 文档更新原则

1. **同步更新**: 代码变更时，同步更新相关文档
2. **版本记录**: 重要变更记录版本号和更新日期
3. **结构一致**: 保持文档结构清晰、格式统一
4. **中文编写**: 所有文档使用中文编写

### 文档分类

- **架构设计**: 系统架构、技术选型、设计方案
- **部署运维**: 部署流程、配置管理、运维指南
- **需求文档**: 功能需求、迭代记录、方案设计
- **迁移方案**: 系统迁移、数据迁移、方案对比
- **设计文档**: UI 设计、交互设计、视觉设计
- **测试报告**: 测试结果、性能对比、问题修复

---

**最后更新**: 2026-06-25
**维护者**: 开发团队