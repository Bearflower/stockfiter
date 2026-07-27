# OBPC 策略 V2.0 参数卡片对齐实施计划

## 概述

将当前 OBPC 策略代码（v24）的形态筛选参数、风控过滤、推送格式全面对齐到用户确认的 V2.0 参数卡片，升级为 v25。

## 现状分析

当前代码与 V2.0 参数卡片存在 **7 项核心差异**：

| # | 差异项 | 当前代码 | V2.0 要求 | 影响 |
|---|--------|---------|----------|------|
| 1 | 大跌阈值 | 8% | 12% | 信号数量偏多 |
| 2 | 缩量比例 | ≤20日均量×0.8 | ≤20日均量×0.6 | 缩量判断过松 |
| 3 | 缩量检测方式 | 大跌后60天内找1天 | 放量日前10天内 | 检测窗口不同 |
| 4 | 放量涨幅 | ≥3% | ≥5% | 信号质量偏低 |
| 5 | 放量量比 | 1.2~15倍 | 1.5~12倍 | 量比范围过宽 |
| 6 | 放量后验证 | 方案B（5天不破0.97） | 方案A（1天缩量走平） | 验证逻辑不同 |
| 7 | 支撑位定义 | 放量日最低价 | 大跌区间最低点 | 支撑位偏高 |
| 8 | 回踩比例 | ×0.98 | ×0.985 | 允许跌破幅度不同 |
| 9 | 流动性 | ≥2000万 | ≥3000万 | 流动性门槛偏低 |
| 10 | 大盘过滤 | 无 | 沪深300≥20日均线 | 缺失风控 |
| 11 | 信号间隔 | 无 | ≥60交易日 | 缺失去重 |
| 12 | 年度限制 | 无 | ≤2次/年 | 缺失频率控制 |
| 13 | 推送格式 | 简略文本 | 详细卡片+交易建议 | 信息不足 |
| 14 | 止损价 | 硬编码0.97 | 配置化（hard_stop_loss） | 硬编码违规 |

## 修改计划

### 文件1：`strategy/oversold_bounce/strategy.py`

**目标**：核心策略逻辑对齐 V2.0

**修改内容**：

1. **默认参数更新**（`_get_default_params`）：
   - `drop_threshold`: 0.08 → 0.12
   - `volume_shrink_ratio`: 0.8 → 0.6
   - `shrink_to_surge_days`: 60 → 10（改为放量日前搜索窗口）
   - `min_volume_ratio`: 1.2 → 1.5
   - `max_volume_ratio`: 15.0 → 12.0
   - `surge_price_ratio`: 0.03 → 0.05
   - `flat_days`: 0 → 1（启用方案A）
   - `flat_volume_threshold`: 0.85（保持不变）
   - `flat_price_range`: 0.08（保持不变）
   - `support_ratio`: 0.98 → 0.985
   - `min_avg_amount`: 20000000 → 30000000
   - 版本号：v24 → v25

2. **支撑位定义修改**（`_try_complete_pattern`）：
   - 当前：`support_level = df['low'].iloc[surge_idx]`（放量日最低价）
   - 改为：`support_level = df['low'].iloc[drop_start_idx:drop_end_idx+1].min()`（大跌区间最低点）

3. **缩量检测方式修改**（`_try_complete_pattern`）：
   - 当前：从大跌结束后往后搜索60天
   - 改为：从放量日往前搜索10天内是否有缩量日
   - 新逻辑：先找放量日，再往前验证缩量

4. **放量后验证修改**：
   - flat_days=1 已启用方案A，逻辑已存在，无需修改代码逻辑
   - 确认方案A参数正确：flat_volume_threshold=0.85, flat_price_range=0.08

5. **信号详情补充**（`analyze`）：
   - detail 中增加 `surge_pct`（放量涨幅）、`surge_volume_ratio`（放量量比）、`drop_rate`（大跌幅度）字段，供推送和评分使用

### 文件2：`config/config.yaml`

**目标**：配置文件与策略代码同步

**修改内容**：

1. `strategies.oversold_bounce.params` 同步更新所有参数值
2. `strategies.oversold_bounce.version` 改为 v25
3. 新增大盘过滤配置：
   ```yaml
   strategies:
     oversold_bounce:
       params:
         # ... 已有参数更新 ...
         # 大盘环境过滤
         index_filter:
           enabled: true
           index_code: "000300.SH"
           index_ma_period: 20
         # 信号间隔控制
         signal_cooldown_days: 60
         max_signals_per_year: 2
   ```

### 文件3：`scripts/daily_scan.py`

**目标**：扫描逻辑增加风控过滤

**修改内容**：

1. **新增大盘环境过滤**（扫描前检查）：
   - 从数据库获取沪深300指数K线
   - 计算当前指数是否 ≥ 20日均线
   - 不满足时跳过扫描，推送"大盘环境不佳，今日不开新仓"

2. **新增信号间隔控制**：
   - 对每个信号，查询 scan_results 表获取该股票最近信号日期
   - 过滤掉60个交易日内重复出现的信号
   - 过滤掉年度信号次数超过2次的股票

3. **止损价计算修改**：
   - 当前：`stop_loss_price = support_level * 0.97`（硬编码）
   - 改为：`stop_loss_price = support_level * (1 - config中的hard_stop_loss)`
   - 从 config.yaml 读取 hard_stop_loss 参数

4. **信号详情补充**：
   - 保存信号时增加 surge_pct、surge_volume_ratio、drop_rate 等字段

### 文件4：`scripts/feishu_push.py`

**目标**：推送格式恢复丰富卡片样式

**修改内容**：

1. **卡片标题**：`股票形态筛选信号` → `📈 新筛选股票提醒`

2. **卡片内容**（每只股票）：
   ```
   **{name}** ({code})

   📊 形态评分：{score}
   📈 关键指标：
   • 放量日期：{surge_date}
   • 放量涨幅：{surge_pct}
   • 放量倍数：{surge_volume_ratio}倍
   • 支撑位：{support_level}元
   • 止损价：{stop_loss_price}元

   💡 交易建议：
   • 建议买入价：今日开盘价
   • 移动止盈：从持仓最高价回撤8%卖出
   • 硬止损：-10%
   ```

3. **顶部风险提示**：
   ```
   共筛选出 N 只股票，建议开盘后择机买入（高开>5%请放弃）
   ⚠️ 风险提示：以上信息仅供参考，不构成投资建议
   ```

4. **推送方式**：
   - 改为直接用飞书 webhook 发送卡片消息（通用通知服务不支持卡片格式）
   - 保留通用通知服务作为文本降级

### 文件5：`data/database.py`

**目标**：新增信号间隔查询方法

**修改内容**：

1. 新增 `get_last_signal_date(code)` 方法：
   - 查询 scan_results 表中该股票最近一次信号日期
   - 用于60交易日冷却期判断

2. 新增 `get_signal_count_this_year(code, year)` 方法：
   - 查询 scan_results 表中该股票今年的信号次数
   - 用于年度2次限制判断

## 不修改的文件

- `strategy/base.py` — 基类接口无需改动
- `notification/service.py` — 已修复 project 名称，无需再改
- `scripts/scheduler_obpc.py` — 调度逻辑无需改动
- `Dockerfile.obpc` — 无需改动
- `docker-compose.yml` — 无需改动

## 假设与决策

1. **缩量检测方式变更**：V2.0 说"放量日前10天内至少1天缩量"，需要调整检测顺序——先找放量日，再往前验证缩量。这与当前"先缩量后放量"的顺序一致，但搜索窗口从"大跌后60天"改为"放量日前10天"。

2. **大盘过滤数据来源**：使用数据库中已有的K线数据（K线服务已采集沪深300），不新增外部数据源依赖。

3. **评分系统**：V2.0 参数卡片未提及评分，但旧版有评分逻辑。本次不实现评分，保持 score=0.0，后续可按需添加。

4. **推送方式**：直接用飞书 webhook 发卡片，不走通用通知服务（因为通用服务只支持文本/markdown，不支持 interactive 卡片）。

5. **信号间隔控制**：基于 scan_results 表而非 push_history 表，因为 scan_results 包含更完整的信号信息。

## 验证步骤

1. **参数验证**：确认 strategy.py 默认参数与 config.yaml 一致
2. **单元测试**：用已知股票数据验证形态检测逻辑
3. **大盘过滤测试**：模拟沪深300在均线上方/下方，验证过滤逻辑
4. **信号间隔测试**：模拟重复信号，验证60天冷却期
5. **推送格式测试**：发送测试卡片到飞书，确认显示效果
6. **部署验证**：构建新镜像，部署到服务器，观察首次扫描结果
