# OBPC V2.0 差距修复实施计划

## 概述

修复 OBPC 策略 V2.0 参数卡片与当前代码的 11 项差距，涵盖形态计算、止损价、评分逻辑、推送格式、硬编码消除和文档更新。扫描时间（22:15）和推送时间（08:10）保持不变，更新到项目文档中固化。

## 修复清单

### 第一批：策略核心逻辑修复（strategy.py + config.yaml）

#### 修复1：走平振幅计算错误（高严重度）
- **文件**：`strategy/oversold_bounce/strategy.py` 第259-260行
- **当前**：`price_change_k = abs(close_k - close_j) / close_j`（相对放量日收盘价偏离）
- **改为**：`price_range_k = (df['high'].iloc[k] - df['low'].iloc[k]) / close_k`（当日实体振幅）
- **需求**：走平振幅 ≤ 8%，公式为 `(最高-最低)/收盘`

#### 修复2：走平缩量边界条件（低严重度）
- **文件**：`strategy/oversold_bounce/strategy.py` 第255行
- **当前**：`if vol_k >= vol_j * p['flat_volume_threshold']:`（严格小于）
- **改为**：`if vol_k > vol_j * p['flat_volume_threshold']:`（含等于，匹配需求"≤"）

#### 修复3：评分缩量维度取实际值（中严重度）
- **文件**：`strategy/oversold_bounce/strategy.py` 第381行 + `_try_complete_pattern` 方法
- **当前**：`shrink_ratio = self.params.get('volume_shrink_ratio', 0.6)` 取参数固定值
- **改为**：
  1. 在 `_try_complete_pattern` 的 pattern 字典中新增 `shrink_ratio` 字段，保存实际缩量日的缩量比例（`vol_i / vol_avg`）
  2. 在 `analyze` 的 detail 中新增 `shrink_ratio` 字段
  3. `score()` 中改为 `shrink_ratio = d.get('shrink_ratio', 0.6)` 取实际值

#### 修复4：config.yaml 补充 surge_lookback 参数
- **文件**：`config/config.yaml`
- **当前**：strategy.py 默认参数有 `surge_lookback: 15`，但 config.yaml 缺失
- **改为**：在放量检测段新增 `surge_lookback: 15`

### 第二批：扫描逻辑修复（daily_scan.py + config.yaml）

#### 修复5：止损价计算错误（高严重度）
- **文件**：`scripts/daily_scan.py` 第172行 + `config/config.yaml`
- **当前**：`stop_loss_price = support_level * (1 - hard_stop_loss)` = 支撑位×0.9
- **改为**：`stop_loss_price = support_level * stop_loss_ratio`，其中 `stop_loss_ratio` 从配置读取
- **config.yaml 新增**：`stop_loss_ratio: 0.97`（在交易参数段）
- **说明**：V2.0 风险提示明确"支撑位×0.97为止损价"，与硬止损-10%（相对买入价）是两个不同概念

#### 修复6：信号冷却期去除1.5倍系数（中严重度）
- **文件**：`scripts/daily_scan.py` 第159行
- **当前**：`cooldown_threshold = int(signal_cooldown_days * 1.5)` = 90天（含硬编码1.5）
- **改为**：`cooldown_threshold = signal_cooldown_days` = 60天（直接使用配置值）

#### 修复7：信号保存增加形态详情字段
- **文件**：`scripts/daily_scan.py` 第174-186行
- **当前**：缺少 `retrace_low`、`shrink_ratio`、`drop_start_date`、`drop_end_date`
- **改为**：在 entry 字典中新增这些字段，供推送和评分使用

### 第三批：推送格式修复（feishu_push.py）

#### 修复8：风险提示内容完善（中严重度）
- **文件**：`scripts/feishu_push.py` 第74行
- **当前**：`⚠️ 风险提示：以上信息仅供参考，不构成投资建议`
- **改为**：`⚠️ 风险提示：支撑位×0.97为止损价，移动止盈回撤8%卖出。以上信息仅供参考，不构成投资建议`

#### 修复9：推送内容增加信号日期 + 消除硬编码（低严重度）
- **文件**：`scripts/feishu_push.py` 第99-112行
- **当前**：缺少信号日期；移动止盈8%和硬止损10%硬编码
- **改为**：
  1. 增加 `• 信号日期：{sig.get('signal_date', '')}`
  2. 移动止盈和硬止损从信号数据中读取（daily_scan.py 已保存这些参数到信号文件）
  3. 在 daily_scan.py 的 entry 中新增 `trailing_stop_ratio` 和 `hard_stop_loss` 字段

#### 修复10：推送内容增加回踩低点和大跌幅度
- **文件**：`scripts/feishu_push.py`
- **当前**：只显示支撑位、止损价
- **改为**：增加 `• 回踩低点：{retrace_low}元` 和 `• 大跌幅度：{drop_rate}`

### 第四批：文档更新

#### 修复11：创建 OBPC 策略说明文档 + 更新现有文档
- **新建**：`docs/designs/OBPC策略说明文档.md`
  - 策略原理（超跌反弹回踩确认）
  - V2.0 参数卡片完整说明
  - 形态检测5步流程
  - 评分系统5维度说明
  - 风控过滤机制
  - 调度时间说明（22:15扫描、08:10推送，及原因）
  - 推送格式说明
- **更新**：`docs/designs/配置管理设计.md`
  - oversold_bounce 版本 v24 → v25
  - 补充新增参数（stop_loss_ratio、surge_lookback等）
- **更新**：`docs/designs/部署方案.md`
  - 固化调度时间说明：22:15扫描（等K线更新完成）、08:10推送
  - 说明与V2.0参数卡片15:30/9:15的差异原因

## 不修改的项

| 项目 | 原因 |
|------|------|
| 扫描时间22:15 | 用户确认不改，等K线更新完成 |
| 推送时间08:10 | 用户确认不改 |
| 交易执行模块 | 系统定位为信号筛选+推送，交易手动执行 |
| 仓位控制/最大持仓数 | 同上，属于交易执行层面 |
| 高开/涨停自动过滤 | 推送时无开盘价，已在推送中文字提示 |

## 实施流程

按 development-workflow.md 要求，调用多智能体协作：

| 序号 | 环节 | 智能体 | 内容 |
|------|------|--------|------|
| 1 | 编码实现 | python-engineer | 修复1-10（strategy.py + config.yaml + daily_scan.py + feishu_push.py） |
| 2 | 代码检测 | code-specification-inspector | 检查编码规范、硬编码、代码质量 |
| 3 | 强制测试 | api-test-pro | 功能验证（语法检查、逻辑验证） |
| 4 | 代码审查 | TRAE-code-review | 深度审查代码质量 |
| 5 | 文档更新 | code-document-curator | 修复11（创建策略说明文档 + 更新现有文档） |

## 验证步骤

1. **语法检查**：所有修改文件通过 `py_compile`
2. **参数一致性**：strategy.py 默认参数与 config.yaml 一致
3. **硬编码检查**：grep 确认无新增硬编码
4. **部署验证**：构建新镜像，重启容器，确认健康运行
5. **文档检查**：确认文档版本与代码一致
