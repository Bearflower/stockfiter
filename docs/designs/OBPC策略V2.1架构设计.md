# OBPC 超跌反弹策略 V2.1 架构设计文档

## 0. 文档信息

| 项目 | 内容 |
|------|------|
| 文档版本 | V1.0 |
| 创建日期 | 2026-06-24 |
| 作者 | 后端架构师 |
| 审核人 | 待定 |
| 最后更新 | 2026-06-24 |
| 文档状态 | 待评审 |
| 关联需求 | [OBPC策略V2.1优化需求.md](./OBPC策略V2.1优化需求.md) |
| 关联代码 | strategy v25 / risk_control V2.0 / config V3.0 |

### 修改记录

| 版本 | 日期 | 修改人 | 修改内容 |
|------|------|--------|---------|
| V1.0 | 2026-06-24 | 后端架构师 | 初始版本，覆盖 FR-1~FR-8 的架构设计与 4 个回测方案 |

### 术语表

| 术语 | 含义 |
|------|------|
| V2.0 三层风控 | 评分阈值过滤 → 大盘趋势过滤 → 单月信号上限 |
| method | 大盘趋势过滤的方法选择（`ma_position` / `macd`） |
| 方案（Scheme） | 4 种市场状态识别的配置组合，通过 `--scheme` 切换 |
| 市场强度 | 个股评分调整依据的市场状态（strong/weak/extreme_weak） |

---

## 1. 概述

### 1.1 背景

V2.0 风控增强后，60 日均线过滤在 2026 年初系统性下跌期命中 0 次，根因是"均线之上但仍在下跌"的状态无法被位置过滤识别。V2.1 引入 MACD 动量过滤、均线斜率判断、评分市场状态调整三项优化，并通过 4 个回测方案对比选优。

### 1.2 设计目标

| 目标 | 说明 |
|------|------|
| 向后兼容 | V2.1 所有功能默认关闭，使用 V2.0 配置时行为完全一致 |
| 配置驱动 | 所有 V2.1 参数通过 config.yaml 配置，代码中无硬编码魔法数字 |
| 模块化扩展 | MACD 计算、斜率计算、评分调整逻辑独立封装，互不耦合 |
| 方案可切换 | 4 个回测方案通过配置组合实现，无需 4 套代码 |
| 不侵入策略层 | 不修改 strategy.py 的 analyze 方法（评分调整除外） |

### 1.3 设计原则

1. **开闭原则**：对扩展开放，对修改关闭。V2.1 通过新增字段和方法扩展，不破坏 V2.0 接口。
2. **单一职责**：MACD 计算、斜率计算、评分调整各自封装为独立方法。
3. **依赖倒置**：RiskController 依赖 MarketContext 抽象数据，不依赖具体计算实现。
4. **可选启用**：所有 V2.1 功能通过开关控制，关闭时走 V2.0 原有路径。

---

## 2. 整体架构

### 2.1 架构分层

```
┌─────────────────────────────────────────────────────────────────┐
│                        调用层                                    │
│   scripts/backtest_obpc.py    scripts/daily_scan.py             │
│   (BacktestEngine)            (scan_daily_signals)              │
└──────────────┬──────────────────────────┬───────────────────────┘
               │                          │
               ▼                          ▼
┌─────────────────────────────────────────────────────────────────┐
│                    风控层（V2.1 扩展核心）                        │
│  strategy/oversold_bounce/risk_control.py                       │
│                                                                  │
│  ┌─────────────────┐  ┌──────────────────┐  ┌────────────────┐ │
│  │ RiskControlConfig│  │ MarketContext     │  │ RiskController │ │
│  │  (V2.1 新增字段) │  │  (V2.1 新增字段)  │  │ (V2.1 新增方法)│ │
│  └─────────────────┘  └──────────────────┘  └────────────────┘ │
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │       MarketContextProvider（抽象接口）                   │   │
│  │  ┌────────────────────┐  ┌────────────────────────────┐ │   │
│  │  │ RealtimeProvider   │  │ PreloadedProvider          │ │   │
│  │  │ (V2.1: MACD/斜率)  │  │ (V2.1: MACD/斜率预计算)    │ │   │
│  │  └────────────────────┘  └────────────────────────────┘ │   │
│  └──────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
               │                          │
               ▼                          ▼
┌─────────────────────────────────────────────────────────────────┐
│                        数据层                                    │
│   data/database.py (get_index_kline)  config/config.yaml        │
└─────────────────────────────────────────────────────────────────┘
```

### 2.2 V2.1 改动范围总览

| 模块 | 文件 | 改动类型 | 改动说明 |
|------|------|---------|---------|
| 风控配置 | risk_control.py | 扩展 | RiskControlConfig 新增 12 个字段 |
| 大盘上下文 | risk_control.py | 扩展 | MarketContext 新增 6 个字段 + 3 个属性 |
| 实时上下文提供者 | risk_control.py | 扩展 | RealtimeMarketContextProvider 新增 MACD/斜率计算 |
| 预加载上下文提供者 | risk_control.py | 扩展 | PreloadedMarketContextProvider 预计算 MACD/斜率列 |
| 风控控制器 | risk_control.py | 扩展 | RiskController 新增 adjust_score 方法，扩展 check_market_trend |
| 回测脚本 | backtest_obpc.py | 扩展 | 新增 --scheme 参数、SchemeConfigurator、报告增强 |
| 每日扫描 | daily_scan.py | 扩展 | 评分调整应用、MACD/斜率过滤日志 |
| 策略层 | strategy.py | 最小改动 | score 方法可选注入 market_context（仅评分调整启用时） |
| 配置文件 | config.yaml | 扩展 | index_filter 段新增参数 + score_adjustment 新段 |

---

## 3. 核心数据结构设计

### 3.1 MarketContext 扩展

**设计思路**：新增字段全部为 `Optional`，不启用对应功能时为 `None`，确保向后兼容。

**现有字段（V2.0，保持不变）**：
- `current_date: str`
- `index_close: float`
- `index_ma20: Optional[float]`
- `index_ma60: Optional[float]`
- `is_weak_market: bool`
- `data_sufficient: bool`

**新增字段（V2.1）**：

| 字段 | 类型 | 默认值 | 说明 | 计算时机 |
|------|------|--------|------|---------|
| `index_dif` | `Optional[float]` | `None` | 大盘 MACD DIF 值 | method='macd' 时计算 |
| `index_dea` | `Optional[float]` | `None` | 大盘 MACD DEA 值 | method='macd' 时计算 |
| `index_hist` | `Optional[float]` | `None` | 大盘 MACD 柱状值（DIF - DEA） | method='macd' 时计算 |
| `ma20_slope` | `Optional[float]` | `None` | 大盘 MA20 斜率 | slope_filter_enabled=true 或 score_adjustment.enabled=true 时计算 |
| `ma60_slope` | `Optional[float]` | `None` | 大盘 MA60 斜率 | slope_filter_enabled=true（slope_ma_period=60）或 score_adjustment.enabled=true 时计算 |
| `market_strength` | `str` | `'unknown'` | 市场强度标签 | score_adjustment.enabled=true 时计算 |

**新增属性（V2.1）**：

| 属性 | 返回类型 | 说明 |
|------|---------|------|
| `macd_bullish` | `bool` | MACD 是否多头（DIF > 0），数据不足时返回 True（放行） |
| `ma20_slope_up` | `bool` | MA20 斜率是否向上（> slope_threshold），数据不足时返回 True |
| `ma60_slope_up` | `bool` | MA60 斜率是否向上（> slope_threshold），数据不足时返回 True |

**market_strength 取值规则**：

| 取值 | 判定条件 | 评分系数 |
|------|---------|---------|
| `'strong'` | index_close >= MA20 且 ma20_slope > 0 | strong_market_multiplier（默认 1.2） |
| `'weak'` | index_close < MA20 或 ma20_slope <= 0 | weak_market_multiplier（默认 0.8） |
| `'extreme_weak'` | index_close < MA60 且 ma60_slope <= 0 | 0（评分归零） |
| `'unknown'` | 数据不足或评分调整未启用 | 1.0（不调整） |

**判定优先级**：`extreme_weak` > `strong` > `weak`（先判极弱，再判强势，否则弱势）。

### 3.2 RiskControlConfig 扩展

**设计思路**：所有新增字段均有默认值，`from_params` 方法对 V2.0 配置字典兼容（缺失键使用默认值）。

**现有字段（V2.0，保持不变）**：
- `index_filter_enabled`、`index_code`、`index_ma_period`、`index_ma_period_long`、`long_ma_filter_enabled`
- `max_signals_per_month`
- `score_filter_enabled`、`score_threshold`、`score_threshold_weak`、`weak_market_condition`

**新增字段（V2.1）**：

| 字段 | 类型 | 默认值 | 配置路径 | 说明 |
|------|------|--------|---------|------|
| `index_filter_method` | `str` | `'ma_position'` | `index_filter.method` | 过滤方法：`'ma_position'` 或 `'macd'` |
| `macd_fast` | `int` | `12` | `index_filter.macd_fast` | MACD 快线周期 |
| `macd_slow` | `int` | `26` | `index_filter.macd_slow` | MACD 慢线周期 |
| `macd_signal` | `int` | `9` | `index_filter.macd_signal` | MACD 信号线周期 |
| `macd_condition` | `str` | `'dif > 0'` | `index_filter.condition` | MACD 过滤条件表达式 |
| `slope_filter_enabled` | `bool` | `False` | `index_filter.slope_filter_enabled` | 斜率过滤开关 |
| `slope_ma_period` | `int` | `60` | `index_filter.slope_ma_period` | 斜率计算的均线周期 |
| `slope_lookback` | `int` | `5` | `index_filter.slope_lookback` | 斜率回看天数 |
| `slope_threshold` | `float` | `0.002` | `index_filter.slope_threshold` | 斜率阈值（0.2%） |
| `score_adjustment_enabled` | `bool` | `False` | `score_adjustment.enabled` | 评分调整开关 |
| `strong_market_multiplier` | `float` | `1.2` | `score_adjustment.strong_market_multiplier` | 强势市场评分系数 |
| `weak_market_multiplier` | `float` | `0.8` | `score_adjustment.weak_market_multiplier` | 弱势市场评分系数 |

**from_params 方法扩展**：

```python
@classmethod
def from_params(cls, params: Dict) -> "RiskControlConfig":
    index_filter = params.get("index_filter", {}) or {}
    score_filter = params.get("score_filter", {}) or {}
    score_adjustment = params.get("score_adjustment", {}) or {}

    return cls(
        # ===== V2.0 原有字段（保持不变） =====
        index_filter_enabled=index_filter.get("enabled", True),
        index_code=index_filter.get("index_code", "000300.SH"),
        index_ma_period=index_filter.get("index_ma_period", 20),
        index_ma_period_long=index_filter.get("index_ma_period_long", 60),
        long_ma_filter_enabled=index_filter.get("long_ma_filter_enabled", True),
        max_signals_per_month=params.get("max_signals_per_month", 12),
        score_filter_enabled=score_filter.get("enabled", True),
        score_threshold=score_filter.get("score_threshold", 60),
        score_threshold_weak=score_filter.get("score_threshold_weak", 75),
        weak_market_condition=score_filter.get("weak_market_condition", "below_ma60"),
        # ===== V2.1 新增字段 =====
        index_filter_method=index_filter.get("method", "ma_position"),
        macd_fast=index_filter.get("macd_fast", 12),
        macd_slow=index_filter.get("macd_slow", 26),
        macd_signal=index_filter.get("macd_signal", 9),
        macd_condition=index_filter.get("condition", "dif > 0"),
        slope_filter_enabled=index_filter.get("slope_filter_enabled", False),
        slope_ma_period=index_filter.get("slope_ma_period", 60),
        slope_lookback=index_filter.get("slope_lookback", 5),
        slope_threshold=index_filter.get("slope_threshold", 0.002),
        score_adjustment_enabled=score_adjustment.get("enabled", False),
        strong_market_multiplier=score_adjustment.get("strong_market_multiplier", 1.2),
        weak_market_multiplier=score_adjustment.get("weak_market_multiplier", 0.8),
    )
```

**关键设计点**：
- V2.0 配置文件不含 `method`、`macd_*`、`slope_*`、`score_adjustment` 键时，全部使用默认值，行为与 V2.0 一致。
- `index_filter_method` 默认 `'ma_position'`，确保 V2.0 的均线位置过滤为默认行为。

---

## 4. MarketContextProvider 扩展设计

### 4.1 RealtimeMarketContextProvider 扩展

**职责**：每日扫描场景下，实时从数据库获取指数数据并计算 MACD、斜率、市场强度。

**新增私有方法**：

#### 4.1.1 `_calculate_macd` 方法

```python
def _calculate_macd(
    self, index_df: pd.DataFrame
) -> tuple[Optional[float], Optional[float], Optional[float]]:
    """
    计算大盘 MACD 指标（DIF、DEA、HIST）

    计算公式：
        EMA_fast = close 的 macd_fast 日指数移动平均
        EMA_slow = close 的 macd_slow 日指数移动平均
        DIF = EMA_fast - EMA_slow
        DEA = DIF 的 macd_signal 日指数移动平均
        HIST = DIF - DEA

    Args:
        index_df: 指数 K 线数据（按日期升序）

    Returns:
        tuple: (dif, dea, hist)，数据不足时返回 (None, None, None)
    """
```

**数据充足性判断**：数据条数 >= `macd_slow + macd_signal`（默认 35 天）才计算，否则返回 None 并输出警告。

#### 4.1.2 `_calculate_slope` 方法

```python
def _calculate_slope(
    self, index_df: pd.DataFrame, ma_period: int
) -> Optional[float]:
    """
    计算指定周期均线的斜率

    计算公式：
        ma = close 的 ma_period 日简单移动平均
        slope = (ma.iloc[-1] - ma.iloc[-slope_lookback]) / ma.iloc[-slope_lookback]

    Args:
        index_df: 指数 K 线数据
        ma_period: 均线周期（20 或 60）

    Returns:
        Optional[float]: 斜率值，数据不足时返回 None
    """
```

**数据充足性判断**：数据条数 >= `ma_period + slope_lookback`（默认 65 天）才计算。

#### 4.1.3 `_determine_market_strength` 方法

```python
def _determine_market_strength(
    self,
    index_close: float,
    index_ma20: Optional[float],
    index_ma60: Optional[float],
    ma20_slope: Optional[float],
    ma60_slope: Optional[float],
) -> str:
    """
    判定市场强度（用于评分调整）

    判定优先级：extreme_weak > strong > weak > unknown

    Args:
        index_close: 当日收盘价
        index_ma20: MA20 值
        index_ma60: MA60 值
        ma20_slope: MA20 斜率
        ma60_slope: MA60 斜率

    Returns:
        str: 'strong' / 'weak' / 'extreme_weak' / 'unknown'
    """
```

**判定逻辑**：
1. 若 `index_ma60` 或 `ma60_slope` 为 None → 无法判定极弱，继续
2. 若 `index_close < index_ma60` 且 `ma60_slope <= 0` → `'extreme_weak'`
3. 若 `index_ma20` 或 `ma20_slope` 为 None → `'unknown'`
4. 若 `index_close >= index_ma20` 且 `ma20_slope > 0` → `'strong'`
5. 否则 → `'weak'`

#### 4.1.4 `get_context` 方法扩展

在现有 `get_context` 方法基础上，根据 config 决定是否计算 MACD、斜率、市场强度：

```python
def get_context(self, date_str: str) -> Optional[MarketContext]:
    # ... 现有逻辑：获取指数数据、计算 MA20/MA60、判断 is_weak_market ...

    # ===== V2.1 新增：MACD 计算 =====
    index_dif = None
    index_dea = None
    index_hist = None
    if self.config.index_filter_method == "macd":
        index_dif, index_dea, index_hist = self._calculate_macd(index_df)

    # ===== V2.1 新增：斜率计算 =====
    ma20_slope = None
    ma60_slope = None
    if self.config.slope_filter_enabled or self.config.score_adjustment_enabled:
        # 斜率过滤启用时，计算 slope_ma_period 对应的斜率
        if self.config.slope_filter_enabled:
            slope_target = self.config.slope_ma_period
            if slope_target == 20:
                ma20_slope = self._calculate_slope(index_df, 20)
            elif slope_target == 60:
                ma60_slope = self._calculate_slope(index_df, 60)
        # 评分调整启用时，需要 MA20 和 MA60 的斜率
        if self.config.score_adjustment_enabled:
            if ma20_slope is None:
                ma20_slope = self._calculate_slope(index_df, 20)
            if ma60_slope is None:
                ma60_slope = self._calculate_slope(index_df, 60)

    # ===== V2.1 新增：市场强度判定 =====
    market_strength = "unknown"
    if self.config.score_adjustment_enabled:
        market_strength = self._determine_market_strength(
            current_close, index_ma20, index_ma60, ma20_slope, ma60_slope
        )

    return MarketContext(
        # ... V2.0 原有字段 ...
        # ===== V2.1 新增字段 =====
        index_dif=index_dif,
        index_dea=index_dea,
        index_hist=index_hist,
        ma20_slope=ma20_slope,
        ma60_slope=ma60_slope,
        market_strength=market_strength,
    )
```

**数据获取天数调整**：`required_days` 需考虑 MACD 和斜率的数据需求：

```python
# V2.0: max(index_ma_period_long + 10, 30)
# V2.1: 额外考虑 macd_slow + macd_signal 和 slope_ma_period + slope_lookback
required_days = max(
    self.config.index_ma_period_long + 10,
    self.config.macd_slow + self.config.macd_signal + 10,
    self.config.slope_ma_period + self.config.slope_lookback + 10,
    30,
)
```

### 4.2 PreloadedMarketContextProvider 扩展

**职责**：回测场景下，启动时一次性预计算所有日期的 MACD、斜率、市场强度，构建 O(1) 查询字典。

**`_build_context_map` 方法扩展**：

```python
def _build_context_map(self, index_df: pd.DataFrame) -> None:
    # ... 现有逻辑：计算 ma20、ma60 列 ...

    # ===== V2.1 新增：预计算 MACD 列 =====
    if self.config.index_filter_method == "macd":
        df["ema_fast"] = df["close"].ewm(
            span=self.config.macd_fast, adjust=False
        ).mean()
        df["ema_slow"] = df["close"].ewm(
            span=self.config.macd_slow, adjust=False
        ).mean()
        df["dif"] = df["ema_fast"] - df["ema_slow"]
        df["dea"] = df["dif"].ewm(
            span=self.config.macd_signal, adjust=False
        ).mean()
        df["hist"] = df["dif"] - df["dea"]

    # ===== V2.1 新增：预计算斜率列 =====
    if self.config.slope_filter_enabled or self.config.score_adjustment_enabled:
        # MA20 斜率
        df["ma20_slope"] = (
            df["ma20"] - df["ma20"].shift(self.config.slope_lookback)
        ) / df["ma20"].shift(self.config.slope_lookback)
        # MA60 斜率
        df["ma60"] = df["close"].rolling(
            window=self.config.index_ma_period_long, min_periods=1
        ).mean()
        df["ma60_slope"] = (
            df["ma60"] - df["ma60"].shift(self.config.slope_lookback)
        ) / df["ma60"].shift(self.config.slope_lookback)

    # 遍历每个交易日构建 MarketContext（扩展字段赋值）
    for idx in range(len(df)):
        # ... 现有逻辑 ...

        # ===== V2.1 新增：提取 MACD 值 =====
        index_dif = None
        index_dea = None
        index_hist = None
        if self.config.index_filter_method == "macd":
            if idx + 1 >= self.config.macd_slow + self.config.macd_signal:
                index_dif = float(df["dif"].iloc[idx])
                index_dea = float(df["dea"].iloc[idx])
                index_hist = float(df["hist"].iloc[idx])

        # ===== V2.1 新增：提取斜率值 =====
        ma20_slope = None
        ma60_slope = None
        if self.config.slope_filter_enabled or self.config.score_adjustment_enabled:
            if idx + 1 >= self.config.index_ma_period + self.config.slope_lookback:
                ma20_slope = float(df["ma20_slope"].iloc[idx]) if pd.notna(df["ma20_slope"].iloc[idx]) else None
            if idx + 1 >= self.config.index_ma_period_long + self.config.slope_lookback:
                ma60_slope = float(df["ma60_slope"].iloc[idx]) if pd.notna(df["ma60_slope"].iloc[idx]) else None

        # ===== V2.1 新增：判定市场强度 =====
        market_strength = "unknown"
        if self.config.score_adjustment_enabled:
            market_strength = self._determine_market_strength(
                current_close, index_ma20, index_ma60, ma20_slope, ma60_slope
            )

        context = MarketContext(
            # ... V2.0 原有字段 ...
            index_dif=index_dif,
            index_dea=index_dea,
            index_hist=index_hist,
            ma20_slope=ma20_slope,
            ma60_slope=ma60_slope,
            market_strength=market_strength,
        )
        self.context_map[date_str] = context
```

**性能说明**：
- MACD 和斜率使用 pandas 向量化计算（`ewm`、`rolling`、`shift`），O(n) 复杂度，对回测耗时影响 < 5%。
- 预计算在回测启动时一次性完成，不影响单股票扫描耗时。

**`_determine_market_strength` 方法**：与 RealtimeMarketContextProvider 共用相同逻辑，建议提取为模块级函数或 mixin，避免代码重复。

---

## 5. RiskController 扩展设计

### 5.1 统计字段扩展

**现有统计字段（V2.0）**：
- `skipped_long_ma_filter`、`skipped_monthly_limit`、`skipped_score_filter`

**新增统计字段（V2.1）**：

| 字段 | 说明 |
|------|------|
| `skipped_macd_filter` | 被 MACD 过滤的信号数 |
| `skipped_slope_filter` | 被斜率过滤的信号数 |
| `skipped_score_adjustment` | 被评分调整归零的信号数（极弱市场） |

**`reset_stats` 方法**同步扩展，重置新增字段。

**`get_stats` 方法**扩展 `summary` 和 `monthly` 结构，新增 V2.1 统计项。

### 5.2 check_market_trend 方法扩展

**设计思路**：根据 `index_filter_method` 分支判断，斜率过滤作为独立增强项叠加。

```python
def check_market_trend(
    self, market_context: MarketContext
) -> RiskControlResult:
    """
    风控2：大盘趋势过滤（V2.1 扩展）

    V2.1 分支逻辑：
        - method='ma_position'：V2.0 原有均线位置过滤
        - method='macd'：V2.1 新增 MACD 动量过滤
        - slope_filter_enabled=true：叠加斜率过滤（与 method 组合）

    Args:
        market_context: 大盘环境上下文

    Returns:
        RiskControlResult: 检查结果
    """
    # 大盘过滤总开关关闭时直接放行
    if not self.config.index_filter_enabled:
        return RiskControlResult(passed=True)

    # ===== 第一步：method 分支判断 =====
    if self.config.index_filter_method == "macd":
        # V2.1 新增：MACD 动量过滤
        result = self._check_macd_filter(market_context)
        if not result.passed:
            return result
    else:
        # V2.0 原有：均线位置过滤（MA20 + MA60）
        result = self._check_ma_position_filter(market_context)
        if not result.passed:
            return result

    # ===== 第二步：斜率过滤（可选增强，与 method 组合） =====
    if self.config.slope_filter_enabled:
        result = self._check_slope_filter(market_context)
        if not result.passed:
            return result

    return RiskControlResult(passed=True)
```

#### 5.2.1 `_check_macd_filter` 方法（V2.1 新增）

```python
def _check_macd_filter(
    self, market_context: MarketContext
) -> RiskControlResult:
    """
    V2.1 新增：MACD 动量过滤

    业务规则：
        - BR-1.2: DIF > 0 时放行（多头区域）
        - BR-1.5: 数据不足时跳过并警告
        - BR-1.7: 命中时输出日志并计入统计

    Args:
        market_context: 大盘环境上下文

    Returns:
        RiskControlResult: 检查结果
    """
    # 数据不足时跳过 MACD 过滤
    if market_context.index_dif is None:
        logger.warning(
            f"指数数据不足 {self.config.macd_slow + self.config.macd_signal} 天，"
            f"跳过 MACD 过滤"
        )
        return RiskControlResult(passed=True)

    # 解析过滤条件（目前支持 'dif > 0'）
    if self.config.macd_condition == "dif > 0":
        if market_context.index_dif <= 0:
            reason = (
                f"大盘 MACD 动量不足（DIF={market_context.index_dif:.4f} <= 0），"
                f"禁止开仓"
            )
            return RiskControlResult(
                passed=False,
                filter_rule="macd_filter",
                filter_reason=reason,
                details={
                    "index_dif": market_context.index_dif,
                    "index_dea": market_context.index_dea,
                    "condition": self.config.macd_condition,
                },
            )

    return RiskControlResult(passed=True)
```

#### 5.2.2 `_check_ma_position_filter` 方法（V2.0 逻辑抽取）

将现有 `check_market_trend` 的均线位置过滤逻辑抽取为独立方法，保持原有行为不变：

```python
def _check_ma_position_filter(
    self, market_context: MarketContext
) -> RiskControlResult:
    """
    V2.0 原有：均线位置过滤（MA20 + MA60）

    逻辑与 V2.0 check_market_trend 完全一致，确保向后兼容。
    """
    # 检查 MA20
    # 检查 MA60（long_ma_filter_enabled 控制开关）
    # ... V2.0 原有逻辑 ...
```

#### 5.2.3 `_check_slope_filter` 方法（V2.1 新增）

```python
def _check_slope_filter(
    self, market_context: MarketContext
) -> RiskControlResult:
    """
    V2.1 新增：均线斜率过滤

    业务规则：
        - BR-2.3: slope > slope_threshold 时放行，否则过滤
        - BR-2.4: 斜率可应用于 MA20 或 MA60（由 slope_ma_period 决定）
        - BR-2.6: 数据不足时跳过并警告
        - BR-2.7: 命中时输出日志并计入统计

    Args:
        market_context: 大盘环境上下文

    Returns:
        RiskControlResult: 检查结果
    """
    # 根据 slope_ma_period 选择对应的斜率值
    if self.config.slope_ma_period == 20:
        slope = market_context.ma20_slope
        ma_name = "MA20"
    elif self.config.slope_ma_period == 60:
        slope = market_context.ma60_slope
        ma_name = "MA60"
    else:
        logger.warning(
            f"不支持的 slope_ma_period={self.config.slope_ma_period}，跳过斜率过滤"
        )
        return RiskControlResult(passed=True)

    # 数据不足时跳过
    if slope is None:
        logger.warning(
            f"指数数据不足 {self.config.slope_ma_period + self.config.slope_lookback} 天，"
            f"跳过斜率过滤"
        )
        return RiskControlResult(passed=True)

    # 斜率判断
    if slope <= self.config.slope_threshold:
        reason = (
            f"大盘均线斜率向下（{ma_name} slope={slope:.4f} <= "
            f"阈值 {self.config.slope_threshold}），禁止开仓"
        )
        return RiskControlResult(
            passed=False,
            filter_rule="slope_filter",
            filter_reason=reason,
            details={
                "slope": slope,
                "slope_ma_period": self.config.slope_ma_period,
                "slope_threshold": self.config.slope_threshold,
            },
        )

    return RiskControlResult(passed=True)
```

### 5.3 adjust_score_by_market_state 方法（V2.1 新增）

**设计思路**：作为评分阈值过滤的前置处理，调整后的评分用于阈值判断。

```python
def adjust_score_by_market_state(
    self, original_score: float, market_context: MarketContext
) -> tuple[float, float, str]:
    """
    V2.1 新增：根据大盘市场状态调整个股评分

    业务规则：
        - BR-3.2: 调整系数通过配置控制
        - BR-3.6: adjusted_score = original_score × multiplier，上限 100 下限 0
        - BR-3.8: 极弱市场评分归零
        - BR-3.9: 日志记录原始评分、调整系数、调整后评分

    Args:
        original_score: 个股形态评分（0-100）
        market_context: 大盘环境上下文

    Returns:
        tuple: (adjusted_score, multiplier, market_strength)
            - adjusted_score: 调整后评分（0-100）
            - multiplier: 调整系数（1.2/0.8/0/1.0）
            - market_strength: 市场强度标签
    """
    # 评分调整未启用时，原样返回
    if not self.config.score_adjustment_enabled:
        return original_score, 1.0, "unknown"

    strength = market_context.market_strength

    # 根据市场强度选择系数
    if strength == "extreme_weak":
        multiplier = 0.0
        logger.info(
            f"大盘极弱（跌破 MA60 且 MA60 向下），评分归零："
            f"原始评分={original_score:.2f}"
        )
    elif strength == "strong":
        multiplier = self.config.strong_market_multiplier
    elif strength == "weak":
        multiplier = self.config.weak_market_multiplier
    else:
        # unknown 或数据不足，不调整
        multiplier = 1.0

    # 计算调整后评分（上限 100，下限 0）
    adjusted_score = round(min(100, max(0, original_score * multiplier)), 2)

    # 系数不为 1.0 时输出调试日志
    if multiplier != 1.0:
        logger.info(
            f"评分调整：原始={original_score:.2f}，"
            f"系数={multiplier}（{strength}），"
            f"调整后={adjusted_score:.2f}"
        )

    return adjusted_score, multiplier, strength
```

### 5.4 apply_all_controls 方法扩展

**设计思路**：在评分阈值过滤前插入评分调整步骤。

```python
def apply_all_controls(
    self,
    signal,
    market_context: MarketContext,
    monthly_count: int,
) -> RiskControlResult:
    """
    应用全部风控检查（V2.1 扩展）

    V2.1 执行顺序：
        0. 评分调整（V2.1 新增，可选）→ 修改 signal.score
        1. 动态评分阈值过滤（使用调整后的评分）
        2. 大盘趋势过滤（method 分支 + 斜率过滤）
        3. 单月信号数量上限检查

    Args:
        signal: 信号对象（score 属性会被评分调整修改）
        market_context: 当日大盘环境上下文
        monthly_count: 当月已产出信号数

    Returns:
        RiskControlResult: 风控检查结果
    """
    # ===== V2.1 新增：评分调整（前置） =====
    if self.config.score_adjustment_enabled:
        original_score = signal.score
        adjusted_score, multiplier, strength = self.adjust_score_by_market_state(
            original_score, market_context
        )
        # 更新信号评分（后续阈值判断使用调整后的评分）
        signal.score = adjusted_score

        # 极弱市场评分归零，直接计入统计并过滤
        if strength == "extreme_weak" and adjusted_score == 0:
            self._record_filter_stats(signal.signal_date, "score_adjustment")
            return RiskControlResult(
                passed=False,
                filter_rule="score_adjustment",
                filter_reason="大盘极弱（跌破 MA60 且 MA60 向下），评分归零",
                details={
                    "original_score": original_score,
                    "adjusted_score": 0,
                    "multiplier": 0,
                    "market_strength": strength,
                },
            )

    # ===== 风控1：动态评分阈值过滤（使用调整后的评分） =====
    result = self.check_score_threshold(signal.score, market_context)
    if not result.passed:
        self._record_filter_stats(signal.signal_date, "score_filter")
        return result

    # ===== 风控2：大盘趋势过滤（V2.1 扩展：method 分支 + 斜率） =====
    result = self.check_market_trend(market_context)
    if not result.passed:
        # 根据子规则记录统计
        if result.filter_rule == "macd_filter":
            self._record_filter_stats(signal.signal_date, "macd_filter")
        elif result.filter_rule == "slope_filter":
            self._record_filter_stats(signal.signal_date, "slope_filter")
        else:
            self._record_filter_stats(signal.signal_date, "long_ma_filter")
        return result

    # ===== 风控3：单月信号数量上限检查 =====
    result = self.check_monthly_limit(signal.signal_date, monthly_count)
    if not result.passed:
        self._record_filter_stats(signal.signal_date, "monthly_limit")
        return result

    return result
```

**关键设计点**：
- 评分调整直接修改 `signal.score`，确保后续阈值判断使用调整后的值。
- 极弱市场评分归零时，直接返回过滤结果，不再走后续风控（性能优化）。
- `filter_rule` 新增 `'macd_filter'`、`'slope_filter'`、`'score_adjustment'` 三种取值。

### 5.5 _record_filter_stats 方法扩展

**统计类型映射扩展**：

| filter_type | stats_key | 说明 |
|-------------|-----------|------|
| `long_ma_filter` | `skipped_long_ma_filter` | V2.0 均线位置过滤 |
| `monthly_limit` | `skipped_monthly_limit` | V2.0 单月上限 |
| `score_filter` | `skipped_score_filter` | V2.0 评分阈值 |
| `macd_filter` | `skipped_macd_filter` | V2.1 MACD 过滤 |
| `slope_filter` | `skipped_slope_filter` | V2.1 斜率过滤 |
| `score_adjustment` | `skipped_score_adjustment` | V2.1 评分归零 |

`monthly_filter_stats` 字典的月份结构同步扩展，新增 `macd_filter`、`slope_filter`、`score_adjustment` 键。

### 5.6 get_stats 方法扩展

```python
def get_stats(self) -> Dict:
    """
    获取风控命中统计（V2.1 扩展）

    Returns:
        Dict: 包含 summary 和 monthly 两部分
    """
    summary = {
        # V2.0 原有
        "skipped_long_ma_filter": self.stats["skipped_long_ma_filter"],
        "skipped_monthly_limit": self.stats["skipped_monthly_limit"],
        "skipped_score_filter": self.stats["skipped_score_filter"],
        # V2.1 新增
        "skipped_macd_filter": self.stats.get("skipped_macd_filter", 0),
        "skipped_slope_filter": self.stats.get("skipped_slope_filter", 0),
        "skipped_score_adjustment": self.stats.get("skipped_score_adjustment", 0),
        "total_filtered": sum(self.stats.values()),
    }

    monthly = []
    for year_month in sorted(self.monthly_filter_stats.keys()):
        month_stats = self.monthly_filter_stats[year_month]
        monthly.append({
            "year_month": year_month,
            # V2.0 原有
            "long_ma_filter": month_stats.get("long_ma_filter", 0),
            "monthly_limit": month_stats.get("monthly_limit", 0),
            "score_filter": month_stats.get("score_filter", 0),
            # V2.1 新增
            "macd_filter": month_stats.get("macd_filter", 0),
            "slope_filter": month_stats.get("slope_filter", 0),
            "score_adjustment": month_stats.get("score_adjustment", 0),
            "total_filtered": sum(month_stats.values()),
        })

    return {"summary": summary, "monthly": monthly}
```

---

## 6. 回测脚本扩展设计

### 6.1 --scheme 参数机制

**设计思路**：新增 `--scheme` 命令行参数，通过 `SchemeConfigurator` 覆盖配置，无需 4 套代码。

#### 6.1.1 命令行参数新增

```python
parser.add_argument(
    "--scheme", type=int, choices=[1, 2, 3, 4], default=None,
    help="回测方案编号（1=MACD, 2=MA20位置+斜率, 3=MACD+斜率, 4=MA60斜率）"
)
```

#### 6.1.2 SchemeConfigurator 类设计

```python
class SchemeConfigurator:
    """
    V2.1 新增：回测方案配置器

    封装 4 个回测方案的配置覆盖逻辑，根据方案编号修改 RiskControlConfig。
    4 个方案通过配置组合实现，无需 4 套代码。
    """

    # 方案定义表
    SCHEME_DEFINITIONS = {
        1: {
            "description": "方案一：大盘 MACD DIF > 0",
            "config": {
                "index_filter_method": "macd",
                "slope_filter_enabled": False,
                "score_adjustment_enabled": False,
            },
        },
        2: {
            "description": "方案二：大盘收盘价 > MA20 且 MA20 斜率 > 0",
            "config": {
                "index_filter_method": "ma_position",
                "long_ma_filter_enabled": False,
                "slope_filter_enabled": True,
                "slope_ma_period": 20,
                "score_adjustment_enabled": False,
            },
        },
        3: {
            "description": "方案三：方案一 + 方案二（MACD + MA20 斜率双重确认）",
            "config": {
                "index_filter_method": "macd",
                "slope_filter_enabled": True,
                "slope_ma_period": 20,
                "score_adjustment_enabled": False,
            },
        },
        4: {
            "description": "方案四：大盘 MA60 向上（斜率替代位置）",
            "config": {
                "index_filter_method": "ma_position",
                "long_ma_filter_enabled": False,
                "slope_filter_enabled": True,
                "slope_ma_period": 60,
                "score_adjustment_enabled": False,
            },
        },
    }

    @classmethod
    def apply_scheme(
        cls, risk_config: RiskControlConfig, scheme: int
    ) -> tuple[RiskControlConfig, str]:
        """
        应用指定方案的配置覆盖

        Args:
            risk_config: 原始风控配置
            scheme: 方案编号（1/2/3/4）

        Returns:
            tuple: (覆盖后的配置, 方案描述)
        """
        if scheme not in cls.SCHEME_DEFINITIONS:
            raise ValueError(f"不支持的方案编号：{scheme}，支持 1/2/3/4")

        scheme_def = cls.SCHEME_DEFINITIONS[scheme]
        config_overrides = scheme_def["config"]

        # 创建配置副本，应用覆盖
        new_config = replace(risk_config, **config_overrides)

        return new_config, scheme_def["description"]
```

**关键设计点**：
- 使用 `dataclasses.replace` 创建配置副本，避免修改原始配置。
- 方案定义集中管理，便于维护和扩展。
- 方案 2 和方案 4 设置 `long_ma_filter_enabled=False`，因为它们用斜率替代 MA60 位置过滤。

#### 6.1.3 main 函数集成

```python
def main():
    # ... 解析参数 ...

    # 加载回测配置
    config = load_backtest_config(...)

    # V2.1 新增：应用回测方案
    scheme_description = ""
    if args.scheme is not None:
        config.risk_control_config, scheme_description = (
            SchemeConfigurator.apply_scheme(
                config.risk_control_config, args.scheme
            )
        )
        logger.info(f"启用回测方案 {args.scheme}：{scheme_description}")

    # V2.1 新增：判断是否启用 V2.1 功能
    v21_enabled = (
        config.risk_control_config.index_filter_method == "macd"
        or config.risk_control_config.slope_filter_enabled
        or config.risk_control_config.score_adjustment_enabled
    )

    # 判断是否启用风控（V2.1 扩展判断条件）
    risk_enabled = (
        config.risk_control_config is not None
        and (
            config.risk_control_config.index_filter_enabled
            or config.risk_control_config.score_filter_enabled
            or config.risk_control_config.long_ma_filter_enabled
            or v21_enabled
        )
    )

    # ... 后续回测逻辑 ...
```

### 6.2 BacktestEngine 扩展

**改动点**：`_backtest_single_stock` 方法中，风控检查逻辑已由 `RiskController.apply_all_controls` 统一处理，无需额外修改。

**统计字段扩展**：

```python
self.stats = {
    # ... V2.0 原有 ...
    # V2.1 新增
    "skipped_macd_filter": 0,
    "skipped_slope_filter": 0,
    "skipped_score_adjustment": 0,
}
```

**风控命中统计映射扩展**：

```python
# 在 _backtest_single_stock 方法中，风控过滤后的统计映射
if risk_result.filter_rule == "long_ma_filter":
    self.stats["skipped_long_ma_filter"] += 1
elif risk_result.filter_rule == "monthly_limit":
    self.stats["skipped_monthly_limit"] += 1
elif risk_result.filter_rule == "score_threshold":
    self.stats["skipped_score_filter"] += 1
# V2.1 新增
elif risk_result.filter_rule == "macd_filter":
    self.stats["skipped_macd_filter"] += 1
elif risk_result.filter_rule == "slope_filter":
    self.stats["skipped_slope_filter"] += 1
elif risk_result.filter_rule == "score_adjustment":
    self.stats["skipped_score_adjustment"] += 1
```

### 6.3 BacktestReporter 扩展

#### 6.3.1 meta 字段扩展

```python
meta = {
    # ... V2.0 原有 ...
    # V2.1 新增
    "scheme": scheme_number,              # 方案编号（None 表示未指定）
    "scheme_description": scheme_description,  # 方案描述
    "index_filter_method": config.risk_control_config.index_filter_method,
    "slope_filter_enabled": config.risk_control_config.slope_filter_enabled,
    "score_adjustment_enabled": config.risk_control_config.score_adjustment_enabled,
    "v21_enabled": v21_enabled,           # V2.1 功能是否启用
}
```

**实现方式**：`BacktestReporter` 构造函数新增 `scheme` 和 `scheme_description` 参数，或通过 config 传递。

#### 6.3.2 报告新增"V2.1 市场状态过滤统计"段

在 `print_report` 方法中，风控命中统计段后新增：

```python
# V2.1 市场状态过滤统计
v21_stats = {
    "macd_filter": rc_summary.get("skipped_macd_filter", 0),
    "slope_filter": rc_summary.get("skipped_slope_filter", 0),
    "score_adjustment": rc_summary.get("skipped_score_adjustment", 0),
}
v21_total = sum(v21_stats.values())

if v21_total > 0:
    print("\n" + "-" * 70)
    print("V2.1 市场状态过滤统计")
    print("-" * 70)
    print(f"  MACD 过滤：       {v21_stats['macd_filter']} 次")
    print(f"  斜率过滤：         {v21_stats['slope_filter']} 次")
    print(f"  评分归零：         {v21_stats['score_adjustment']} 次")
    print(f"  V2.1 总过滤：      {v21_total} 次")

    # 按月份分布
    if rc_monthly:
        print("\n  按月份分布：")
        print(f"  {'月份':<10} {'MACD':>8} {'斜率':>8} {'评分归零':>10} {'合计':>8}")
        for m in rc_monthly:
            v21_month_total = (
                m.get("macd_filter", 0)
                + m.get("slope_filter", 0)
                + m.get("score_adjustment", 0)
            )
            if v21_month_total > 0:
                print(
                    f"  {m['year_month']:<10} "
                    f"{m.get('macd_filter', 0):>8} "
                    f"{m.get('slope_filter', 0):>8} "
                    f"{m.get('score_adjustment', 0):>10} "
                    f"{v21_month_total:>8}"
                )
```

#### 6.3.3 报告文件名包含方案编号

```python
def save_report(self, report: Dict) -> str:
    output_dir = self.config.output_dir
    os.makedirs(output_dir, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # V2.1 新增：文件名包含方案编号
    scheme = report.get("meta", {}).get("scheme")
    if scheme is not None:
        filename = f"backtest_scheme{scheme}_{timestamp}.json"
    else:
        filename = f"backtest_{timestamp}.json"

    filepath = os.path.join(output_dir, filename)
    # ... 保存逻辑 ...
```

### 6.4 BacktestDataLoader 扩展

`build_market_context_provider` 方法无需修改，因为 `PreloadedMarketContextProvider` 的 V2.1 计算逻辑在构造函数中根据 config 自动决定是否计算 MACD/斜率。

---

## 7. 每日扫描脚本扩展设计

### 7.1 scan_daily_signals 函数扩展

**改动点**：

1. **大盘预检查扩展**：扫描前的 `check_market_trend` 调用已支持 V2.1 的 method 分支和斜率过滤，无需额外修改。

2. **评分调整应用**：风控检查由 `apply_all_controls` 统一处理，评分调整在内部自动应用。

3. **日志增强**：大盘预检查时输出 V2.1 过滤方法的日志。

```python
# 大盘环境预检查（V2.1 扩展日志）
if risk_config.index_filter_enabled:
    market_context = market_context_provider.get_context(signal_date)

    if market_context is not None:
        # V2.1 新增：输出过滤方法信息
        if risk_config.index_filter_method == "macd":
            print(f"大盘过滤方法：MACD（DIF={market_context.index_dif}）")
        if risk_config.slope_filter_enabled:
            print(f"斜率过滤：启用（MA{risk_config.slope_ma_period}）")
        if risk_config.score_adjustment_enabled:
            print(f"评分调整：启用（市场强度={market_context.market_strength}）")

        # 大盘趋势过滤预检查
        trend_result = risk_controller.check_market_trend(market_context)
        if not trend_result.passed:
            print(f"大盘环境不佳，今日不开新仓")
            print(f"  原因：{trend_result.filter_reason}")
            db.close()
            return []
```

**关键设计点**：
- 每日扫描的风控逻辑完全复用 `RiskController`，无需在 daily_scan.py 中重复实现 V2.1 过滤。
- 评分调整在 `apply_all_controls` 内部自动应用，daily_scan.py 无需感知。

---

## 8. 配置设计

### 8.1 config.yaml 完整配置结构

```yaml
strategies:
  oversold_bounce:
    enabled: true
    version: "v25"
    params:
      # ... V2.0 原有参数保持不变 ...

      # ===== 大盘环境过滤（V2.1 扩展） =====
      index_filter:
        # ----- V2.0 原有 -----
        enabled: true                          # 大盘过滤总开关
        index_code: "000300.SH"                # 指数代码
        index_ma_period: 20                    # 短周期均线天数
        index_ma_period_long: 60               # 长周期均线天数
        long_ma_filter_enabled: true           # 60日均线过滤开关

        # ----- V2.1 新增：MACD 动量过滤 -----
        method: 'ma_position'                  # 过滤方法：'ma_position' 或 'macd'
        macd_fast: 12                          # MACD 快线周期
        macd_slow: 26                          # MACD 慢线周期
        macd_signal: 9                         # MACD 信号线周期
        condition: 'dif > 0'                   # MACD 过滤条件

        # ----- V2.1 新增：均线斜率过滤 -----
        slope_filter_enabled: false            # 斜率过滤开关（默认关闭）
        slope_ma_period: 60                    # 斜率计算的均线周期（20 或 60）
        slope_lookback: 5                      # 斜率回看天数
        slope_threshold: 0.002                 # 斜率阈值（0.2%）

      # ===== 评分市场状态调整（V2.1 新增） =====
      score_adjustment:
        enabled: false                         # 评分调整开关（默认关闭）
        strong_market_multiplier: 1.2          # 强势市场评分放大系数
        weak_market_multiplier: 0.8            # 弱势市场评分缩小系数

      # ... V2.0 原有 score_filter、max_signals_per_month 等保持不变 ...
```

### 8.2 默认值与向后兼容

| 配置项 | 默认值 | V2.0 配置缺失时行为 |
|--------|--------|-------------------|
| `index_filter.method` | `'ma_position'` | 使用 V2.0 均线位置过滤 |
| `index_filter.macd_fast` | `12` | 不生效（method 非 macd） |
| `index_filter.macd_slow` | `26` | 不生效 |
| `index_filter.macd_signal` | `9` | 不生效 |
| `index_filter.condition` | `'dif > 0'` | 不生效 |
| `index_filter.slope_filter_enabled` | `false` | 不启用斜率过滤 |
| `index_filter.slope_ma_period` | `60` | 不生效 |
| `index_filter.slope_lookback` | `5` | 不生效 |
| `index_filter.slope_threshold` | `0.002` | 不生效 |
| `score_adjustment.enabled` | `false` | 不启用评分调整 |
| `score_adjustment.strong_market_multiplier` | `1.2` | 不生效 |
| `score_adjustment.weak_market_multiplier` | `0.8` | 不生效 |

**向后兼容验证**：使用 V2.0 配置文件（不含任何 V2.1 新增参数）运行回测，所有 V2.1 字段使用默认值，`method='ma_position'`、`slope_filter_enabled=false`、`score_adjustment.enabled=false`，行为与 V2.0 完全一致。

---

## 9. 4 个回测方案设计

### 9.1 方案配置矩阵

| 方案 | method | slope_filter_enabled | slope_ma_period | long_ma_filter_enabled | score_adjustment.enabled | 过滤条件描述 |
|------|--------|---------------------|-----------------|----------------------|------------------------|------------|
| 方案一 | `macd` | `false` | - | - | `false` | DIF > 0 |
| 方案二 | `ma_position` | `true` | `20` | `false` | `false` | 收盘价 >= MA20 且 MA20 斜率 > 阈值 |
| 方案三 | `macd` | `true` | `20` | - | `false` | DIF > 0 且 MA20 斜率 > 阈值 |
| 方案四 | `ma_position` | `true` | `60` | `false` | `false` | 收盘价 >= MA20 且 MA60 斜率 > 阈值 |

### 9.2 方案执行流程

```
用户执行: python scripts/backtest_obpc.py --scheme 1
                    │
                    ▼
        ┌───────────────────────┐
        │ 1. 加载 config.yaml   │
        │    构建 RiskControlConfig │
        └───────────┬───────────┘
                    │
                    ▼
        ┌───────────────────────┐
        │ 2. SchemeConfigurator │
        │    .apply_scheme(1)   │
        │    覆盖配置：          │
        │    method='macd'      │
        │    slope_filter=false │
        └───────────┬───────────┘
                    │
                    ▼
        ┌───────────────────────┐
        │ 3. v21_enabled=true   │
        │    risk_enabled=true  │
        │    workers=1（单进程） │
        └───────────┬───────────┘
                    │
                    ▼
        ┌───────────────────────┐
        │ 4. PreloadedProvider  │
        │    预计算 MACD 列     │
        │    （method='macd'）  │
        └───────────┬───────────┘
                    │
                    ▼
        ┌───────────────────────┐
        │ 5. BacktestEngine.run │
        │    每个信号调用        │
        │    apply_all_controls │
        │    → check_market_trend│
        │      → _check_macd_filter│
        └───────────┬───────────┘
                    │
                    ▼
        ┌───────────────────────┐
        │ 6. BacktestReporter   │
        │    meta.scheme=1      │
        │    文件名含 scheme1    │
        │    报告含 V2.1 统计段  │
        └───────────────────────┘
```

### 9.3 方案对比预期

| 方案 | 过滤严格度 | 预期信号数 | 预期胜率 | 适用场景 |
|------|-----------|-----------|---------|---------|
| 方案一 | 中（单一动量） | 适中 | 较高 | 动量拐点敏感 |
| 方案二 | 中（位置+斜率） | 适中 | 中等 | 均线拐头识别 |
| 方案三 | 高（双重确认） | 最少 | 最高 | 严格过滤，减少误信号 |
| 方案四 | 中（MA60 斜率） | 适中 | 中等 | 长期趋势识别 |

---

## 10. 数据流设计

### 10.1 回测数据流（V2.1 启用时）

```
┌─────────────────────────────────────────────────────────────────────┐
│ 1. 启动阶段                                                          │
│   load_backtest_config()                                             │
│     → RiskControlConfig.from_params()（含 V2.1 字段）                │
│   SchemeConfigurator.apply_scheme(scheme)                            │
│     → 覆盖配置                                                        │
│   BacktestDataLoader.load_index_data()                               │
│     → 获取完整指数 K 线                                               │
│   PreloadedMarketContextProvider(index_df, config)                   │
│     → _build_context_map()                                           │
│       → 计算 ma20、ma60 列                                           │
│       → 计算 dif、dea、hist 列（method='macd' 时）                   │
│       → 计算 ma20_slope、ma60_slope 列（slope_filter 或 adjustment）  │
│       → 判定 market_strength（score_adjustment 时）                  │
│     → context_map[date] = MarketContext（含 V2.1 字段）              │
└─────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│ 2. 单股票回测阶段（_backtest_single_stock）                          │
│   for each window:                                                   │
│     signal = strategy.analyze(code, window_df)                       │
│       → strategy.score(signal)（形态评分，不感知大盘）                │
│     market_context = provider.get_context(signal_date)               │
│       → O(1) 字典查询，返回预计算的 V2.1 上下文                       │
│     risk_result = risk_controller.apply_all_controls(                │
│         signal, market_context, monthly_count)                       │
│       → [V2.1] adjust_score_by_market_state()（可选）                │
│         → 修改 signal.score                                          │
│         → 极弱市场直接过滤                                            │
│       → check_score_threshold()（使用调整后评分）                     │
│       → check_market_trend()                                         │
│         → _check_macd_filter() 或 _check_ma_position_filter()        │
│         → _check_slope_filter()（可选）                              │
│       → check_monthly_limit()                                        │
│     if passed:                                                       │
│       trade = trade_simulator.simulate(signal, df)                   │
└─────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│ 3. 报告阶段                                                          │
│   risk_control_stats = risk_controller.get_stats()                   │
│     → summary 含 V2.1 统计项                                         │
│     → monthly 含 V2.1 按月分布                                       │
│   report = reporter.generate_report(trades, stats, risk_control_stats)│
│     → meta 含 scheme、method、v21_enabled                            │
│   reporter.print_report(report)                                      │
│     → 输出"V2.1 市场状态过滤统计"段                                  │
│   reporter.save_report(report)                                       │
│     → 文件名含 scheme 编号                                           │
└─────────────────────────────────────────────────────────────────────┘
```

### 10.2 每日扫描数据流（V2.1 启用时）

```
┌─────────────────────────────────────────────────────────────────────┐
│ 1. 初始化阶段                                                        │
│   RiskControlConfig.from_params()（含 V2.1 字段）                    │
│   RealtimeMarketContextProvider(db, config)                          │
│   RiskController(config)                                             │
└─────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│ 2. 大盘预检查阶段                                                    │
│   market_context = provider.get_context(signal_date)                 │
│     → 实时计算 MA20、MA60                                            │
│     → 实时计算 MACD（method='macd' 时）                              │
│     → 实时计算斜率（slope_filter 或 adjustment 时）                  │
│     → 判定 market_strength（adjustment 时）                          │
│   risk_controller.check_market_trend(market_context)                 │
│     → V2.1 分支判断                                                  │
│   if not passed:                                                     │
│     return []（提前返回，避免无谓遍历）                               │
└─────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│ 3. 单股票扫描阶段                                                    │
│   for each stock:                                                    │
│     signal = strategy.analyze(code, kline_df)                        │
│     risk_result = risk_controller.apply_all_controls(                │
│         signal, market_context, monthly_count)                       │
│       → [V2.1] 评分调整 + 三层风控                                   │
│     if passed:                                                       │
│       signals.append(entry)                                          │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 11. 向后兼容设计

### 11.1 兼容性保障机制

| 机制 | 说明 |
|------|------|
| 字段默认值 | 所有 V2.1 新增字段在 dataclass 中有默认值，V2.0 配置缺失时使用默认值 |
| method 默认值 | `index_filter_method` 默认 `'ma_position'`，走 V2.0 均线位置过滤路径 |
| 开关默认关闭 | `slope_filter_enabled`、`score_adjustment_enabled` 默认 `false` |
| Optional 字段 | MarketContext 新增字段为 `Optional`，不启用时为 `None` |
| 分支兜底 | check_market_trend 的 method 分支，非 'macd' 时走 V2.0 原有逻辑 |

### 11.2 兼容性验证矩阵

| 配置场景 | method | slope_filter | score_adjustment | 行为 |
|---------|--------|-------------|-----------------|------|
| V2.0 配置 | `'ma_position'`（默认） | `false`（默认） | `false`（默认） | 与 V2.0 完全一致 |
| 仅 MACD | `'macd'` | `false` | `false` | MACD 过滤替代均线位置 |
| 仅斜率 | `'ma_position'` | `true` | `false` | 均线位置 + 斜率组合 |
| 仅评分调整 | `'ma_position'` | `false` | `true` | 评分调整 + V2.0 均线过滤 |
| 全部启用 | `'macd'` | `true` | `true` | MACD + 斜率 + 评分调整 |

### 11.3 不修改 strategy.py 的约束

**约束说明**：V2.1 不修改 `OversoldBounceStrategy.analyze` 方法，评分调整在风控层完成。

**实现方式**：
- `strategy.score(signal)` 方法保持不变，计算纯形态评分。
- `RiskController.apply_all_controls` 在评分阈值过滤前调用 `adjust_score_by_market_state`，直接修改 `signal.score`。
- 评分调整逻辑完全在风控层，策略层无感知。

**例外**：若需在 `strategy.score` 方法中注入 market_context（需求文档 FR-3 影响范围提及），可通过可选参数实现，但默认不传入，保持向后兼容。本设计选择在风控层调整评分，避免修改 strategy.py，更符合"不侵入策略层"原则。

---

## 12. 影响范围汇总

### 12.1 文件改动清单

| 文件 | 改动类型 | 改动量 | 说明 |
|------|---------|--------|------|
| `strategy/oversold_bounce/risk_control.py` | 扩展 | 大 | RiskControlConfig +12 字段、MarketContext +6 字段、Provider 扩展、RiskController 扩展 |
| `scripts/backtest_obpc.py` | 扩展 | 中 | 新增 SchemeConfigurator、--scheme 参数、报告增强 |
| `scripts/daily_scan.py` | 扩展 | 小 | 日志增强（风控逻辑复用） |
| `config/config.yaml` | 扩展 | 小 | index_filter 段新增参数 + score_adjustment 新段 |
| `strategy/oversold_bounce/strategy.py` | 不修改 | 无 | 评分调整在风控层完成 |

### 12.2 接口变更清单

| 接口 | 变更类型 | 兼容性 |
|------|---------|--------|
| `RiskControlConfig` | 新增字段 | 向后兼容（有默认值） |
| `RiskControlConfig.from_params` | 扩展 | 向后兼容（缺失键用默认值） |
| `MarketContext` | 新增字段 | 向后兼容（Optional） |
| `MarketContextProvider.get_context` | 返回值扩展 | 向后兼容（新增字段可选） |
| `RiskController.check_market_trend` | 内部分支扩展 | 向后兼容（method 默认 ma_position） |
| `RiskController.apply_all_controls` | 新增评分调整步骤 | 向后兼容（adjustment 默认关闭） |
| `RiskController.get_stats` | 返回值扩展 | 向后兼容（新增统计项默认 0） |
| `BacktestReporter.generate_report` | meta 扩展 | 向后兼容（新增字段可选） |

### 12.3 新增接口清单

| 接口 | 类型 | 说明 |
|------|------|------|
| `RiskController.adjust_score_by_market_state` | 新增方法 | 评分调整 |
| `RiskController._check_macd_filter` | 新增私有方法 | MACD 过滤 |
| `RiskController._check_ma_position_filter` | 新增私有方法 | V2.0 逻辑抽取 |
| `RiskController._check_slope_filter` | 新增私有方法 | 斜率过滤 |
| `RealtimeMarketContextProvider._calculate_macd` | 新增私有方法 | MACD 计算 |
| `RealtimeMarketContextProvider._calculate_slope` | 新增私有方法 | 斜率计算 |
| `RealtimeMarketContextProvider._determine_market_strength` | 新增私有方法 | 市场强度判定 |
| `PreloadedMarketContextProvider._determine_market_strength` | 新增私有方法 | 市场强度判定（建议提取为公共函数） |
| `SchemeConfigurator` | 新增类 | 方案配置器 |
| `SchemeConfigurator.apply_scheme` | 新增类方法 | 应用方案配置 |

---

## 13. 非功能性设计

### 13.1 性能设计

| 场景 | V2.1 影响 | 优化措施 |
|------|----------|---------|
| 回测启动 | MACD/斜率预计算增加启动耗时 | pandas 向量化计算，O(n) 复杂度，增幅 < 5% |
| 单股票回测 | 无影响 | 上下文 O(1) 字典查询，不增加单股票耗时 |
| 回测总耗时 | 增幅 < 20% | V2.1 过滤减少信号数，间接降低交易模拟耗时 |
| 每日扫描 | 增幅 < 10% | 大盘上下文预计算一次，不增加单股票扫描耗时 |
| 内存占用 | 增幅 < 10% | 新增 MACD/斜率列存储，数据量小 |

### 13.2 可维护性设计

| 要求 | 实现方式 |
|------|---------|
| 配置驱动 | 所有 V2.1 参数通过 config.yaml 配置，代码中无硬编码 |
| 模块化 | MACD 计算、斜率计算、评分调整逻辑独立封装为方法 |
| 日志完备 | 所有过滤命中输出日志，包含关键参数值 |
| 统计完备 | 回测报告包含 V2.1 各过滤规则的命中统计和按月分布 |
| 方案可切换 | 4 个方案通过 SchemeConfigurator 配置组合，无需 4 套代码 |

### 13.3 可测试性设计

| 要求 | 实现方式 |
|------|---------|
| 方案可切换 | `--scheme` 参数切换，无需修改配置文件 |
| 结果可对比 | 4 个方案结果独立保存，文件名含方案编号 |
| 统计可观测 | 回测报告展示 V2.1 过滤命中次数和按月分布 |
| 功能可独立测试 | MACD、斜率、评分调整可独立启用/关闭 |

---

## 14. 风险与应对

| 风险 | 影响 | 概率 | 应对措施 |
|------|------|------|---------|
| MACD 过滤过于灵敏，震荡市频繁开关 | 信号数忽多忽少 | 中 | 方案三双重确认缓解；回测对比选优 |
| 斜率阈值 0.2% 不合理 | 过滤效果弱或过强 | 中 | 阈值可配置，回测后调优 |
| 评分调整系数 1.2/0.8 不合理 | 强弱差异过大或过小 | 低 | 系数可配置，回测后调优 |
| 4 个方案均不达标 | V2.1 优化无效 | 低 | 保留 V2.0 回退；调整参数重新回测 |
| V2.1 与 V2.0 行为不一致 | 向后兼容破坏 | 低 | 所有功能默认关闭，V2.0 配置行为一致 |
| 代码重复（市场强度判定） | 维护成本增加 | 中 | 提取为模块级公共函数 |

---

## 15. 实施计划

### 15.1 实施阶段

| 阶段 | 内容 | 涉及文件 | 预计周期 |
|------|------|---------|---------|
| 阶段1 | RiskControlConfig + MarketContext 扩展 | risk_control.py | 0.5 天 |
| 阶段2 | MarketContextProvider 扩展（MACD/斜率计算） | risk_control.py | 1 天 |
| 阶段3 | RiskController 扩展（check_market_trend + adjust_score） | risk_control.py | 1 天 |
| 阶段4 | 回测脚本扩展（SchemeConfigurator + --scheme + 报告） | backtest_obpc.py | 1 天 |
| 阶段5 | 每日扫描扩展（日志增强） | daily_scan.py | 0.5 天 |
| 阶段6 | 配置文件更新 | config.yaml | 0.5 天 |
| 阶段7 | 单元测试 + 4 方案回测验证 | tests/ | 2 天 |
| 阶段8 | 代码审查 + 文档对照 | - | 1 天 |

### 15.2 验证清单

| 验证项 | 验证方法 | 对应 AC |
|--------|---------|---------|
| V2.0 配置兼容 | 使用 V2.0 配置回测，结果与 V2.0 一致 | AC-8.1 |
| MACD 过滤 | 配置 method='macd'，DIF<=0 时无信号 | AC-1.1~1.7 |
| 斜率过滤 | 配置 slope_filter_enabled=true，斜率<=阈值时无信号 | AC-2.1~2.7 |
| 评分调整 | 配置 score_adjustment.enabled=true，极弱市场评分归零 | AC-3.1~3.7 |
| 4 方案切换 | --scheme 1/2/3/4 分别回测，配置正确覆盖 | AC-4.1~4.8 |
| 配置参数化 | grep 确认无硬编码 12/26/9/0.002/1.2/0.8 | AC-5.1~5.3 |
| 命中统计 | 回测报告含 V2.1 统计段 | AC-7.1~7.3 |
| 功能独立开关 | MACD/斜率/评分调整可独立启用 | AC-8.2~8.5 |

---

## 16. 附录

### 16.1 关键代码位置参考

| 模块 | 文件 | 关键位置 |
|------|------|---------|
| 风控配置 | risk_control.py | `RiskControlConfig`（L33-100） |
| 大盘上下文 | risk_control.py | `MarketContext`（L103-144） |
| 实时上下文提供者 | risk_control.py | `RealtimeMarketContextProvider`（L239-339） |
| 预加载上下文提供者 | risk_control.py | `PreloadedMarketContextProvider`（L342-442） |
| 风控控制器 | risk_control.py | `RiskController`（L530-886） |
| 大盘趋势过滤 | risk_control.py | `check_market_trend`（L613-690） |
| 风控串联 | risk_control.py | `apply_all_controls`（L563-611） |
| 回测引擎 | backtest_obpc.py | `BacktestEngine._backtest_single_stock`（L761-897） |
| 回测报告 | backtest_obpc.py | `BacktestReporter`（L906-1206） |
| 每日扫描 | daily_scan.py | `scan_daily_signals`（L49-307） |
| 配置文件 | config.yaml | `strategies.oversold_bounce.params`（L54-116） |

### 16.2 V2.1 新增字段汇总

**RiskControlConfig 新增 12 字段**：
`index_filter_method`、`macd_fast`、`macd_slow`、`macd_signal`、`macd_condition`、`slope_filter_enabled`、`slope_ma_period`、`slope_lookback`、`slope_threshold`、`score_adjustment_enabled`、`strong_market_multiplier`、`weak_market_multiplier`

**MarketContext 新增 6 字段**：
`index_dif`、`index_dea`、`index_hist`、`ma20_slope`、`ma60_slope`、`market_strength`

**RiskController 新增 3 统计字段**：
`skipped_macd_filter`、`skipped_slope_filter`、`skipped_score_adjustment`

**RiskController 新增 4 方法**：
`adjust_score_by_market_state`、`_check_macd_filter`、`_check_ma_position_filter`、`_check_slope_filter`

### 16.3 4 方案配置速查

| 方案 | 命令 | 核心配置 |
|------|------|---------|
| 方案一 | `python scripts/backtest_obpc.py --scheme 1` | method='macd', slope_filter=false |
| 方案二 | `python scripts/backtest_obpc.py --scheme 2` | method='ma_position', slope_filter=true, slope_ma=20, long_ma_filter=false |
| 方案三 | `python scripts/backtest_obpc.py --scheme 3` | method='macd', slope_filter=true, slope_ma=20 |
| 方案四 | `python scripts/backtest_obpc.py --scheme 4` | method='ma_position', slope_filter=true, slope_ma=60, long_ma_filter=false |

---

**文档结束**
