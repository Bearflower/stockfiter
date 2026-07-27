# 情绪面双因子改造计划

## 概述

将情绪面评分从单一因子（资金费率）改为双因子组合（资金费率 + OI变化率），各占5分，总分10分。资金费率计分门槛从50%下调至30%。与V4.0情绪面优化版规范对齐。

---

## 当前状态分析

### 现有文件结构

| 文件 | 当前职责 |
|------|---------|
| `strategies/new_coin/config.yaml` | 评分配置：funding_rate thresholds: extreme(150)/greed(100)/mild(50)，scores: 10/7/3/0 |
| `strategies/new_coin/scoring_engine.py` | `calculate_sentiment_score(funding_rate)` — 单因子，`score()` 传入 funding_rate |
| `strategies/new_coin/strategy.py` | `_get_funding_rate()` 获取最新费率，`_get_open_interest()` 获取当前OI；`analyze()` 调 `score()` |

### 当前废弃点

- `_get_open_interest()` 已存在，可复用
- `_get_funding_rate()` 已存在，需修改为获取历史费率（上线后前6个结算周期的平均值）
- OI变化率需要3小时前的OI数据，需新增 `_get_open_interest_3h_ago()`

### 数据采集策略调整

根据资料费率分析结果，上线后前6个结算周期（约48h）的资金费率才可靠。但策略只监控48h内的币种，所以改为：取**最近6个结算周期的平均费率**。

---

## 修改计划

### 改动1：config.yaml — 新增OI变化率配置 + 更新费率阈值

**文件**：`strategies/new_coin/config.yaml`

替换 `funding_rate` 节为：

```yaml
  # 情绪面评分配置（双因子：资金费率 + OI变化率，各5分，总分10分）
  sentiment:
    # 资金费率子因子（年化%，0~5分）
    funding_rate:
      thresholds:
        extreme: 100       # >100%: 5分
        greed: 50          # 50%~100%: 3分
        mild: 30           # 30%~50%: 1分
      scores:
        extreme: 5.0
        greed: 3.0
        mild: 1.0
        neutral: 0.0       # <30% 或负费率: 0分

    # OI变化率子因子（3小时%，0~5分）
    oi_change:
      thresholds:
        extreme: 50        # >=50%: 5分
        greed: 30          # 30%~50%: 3分
        mild: 10           # 10%~30%: 1分
      scores:
        extreme: 5.0
        greed: 3.0
        mild: 1.0
        neutral: 0.0       # <10% 或负增长: 0分
      lookback_hours: 3    # OI回溯时长（小时）
```

### 改动2：scoring_engine.py — 改造 `calculate_sentiment_score()`

**文件**：`strategies/new_coin/scoring_engine.py`

1. 将 `calculate_sentiment_score(funding_rate: float)` 改为 `calculate_sentiment_score(funding_rate: float, oi_change_rate: float)`

2. 实现双因子评分逻辑：

```python
def calculate_sentiment_score(self, funding_rate: float, oi_change_rate: float) -> Tuple[float, str]:
    """
    计算情绪面评分（V4.0 情绪面优化版 — 双因子组合）

    双因子：资金费率（0~5分）+ OI变化率（0~5分），总分10分
    
    Args:
        funding_rate: 资金费率（原始费率）
        oi_change_rate: OI变化率（小数，如0.5表示50%增长）
    
    Returns:
        (评分, 原因说明)
    """
    # 计算年化费率
    annualized_rate = funding_rate * 3 * 365 * 100
    
    # 1. 资金费率子因子评分（0~5分）
    fr_config = self.config.get('scoring', {}).get('sentiment', {}).get('funding_rate', {})
    fr_thresholds = fr_config.get('thresholds', {})
    fr_scores = fr_config.get('scores', {})
    
    if annualized_rate > fr_thresholds.get('extreme', 100):
        fr_score = fr_scores.get('extreme', 5.0)
        fr_reason = f"年化费率 {annualized_rate:.1f}% > 100%"
    elif annualized_rate >= fr_thresholds.get('greed', 50):
        fr_score = fr_scores.get('greed', 3.0)
        fr_reason = f"年化费率 {annualized_rate:.1f}% (50%~100%)"
    elif annualized_rate >= fr_thresholds.get('mild', 30):
        fr_score = fr_scores.get('mild', 1.0)
        fr_reason = f"年化费率 {annualized_rate:.1f}% (30%~50%)"
    else:
        fr_score = fr_scores.get('neutral', 0.0)
        fr_reason = f"年化费率 {annualized_rate:.1f}% < 30%"
    
    # 2. OI变化率子因子评分（0~5分）
    oi_config = self.config.get('scoring', {}).get('sentiment', {}).get('oi_change', {})
    oi_thresholds = oi_config.get('thresholds', {})
    oi_scores = oi_config.get('scores', {})
    
    oi_change_pct = oi_change_rate * 100  # 转为百分比
    
    if oi_change_pct >= oi_thresholds.get('extreme', 50):
        oi_score = oi_scores.get('extreme', 5.0)
        oi_reason = f"OI 3h增长 {oi_change_pct:.1f}% >= 50%"
    elif oi_change_pct >= oi_thresholds.get('greed', 30):
        oi_score = oi_scores.get('greed', 3.0)
        oi_reason = f"OI 3h增长 {oi_change_pct:.1f}% (30%~50%)"
    elif oi_change_pct >= oi_thresholds.get('mild', 10):
        oi_score = oi_scores.get('mild', 1.0)
        oi_reason = f"OI 3h增长 {oi_change_pct:.1f}% (10%~30%)"
    else:
        oi_score = oi_scores.get('neutral', 0.0)
        oi_reason = f"OI 3h增长 {oi_change_pct:.1f}% < 10%"
    
    # 合计
    total_score = fr_score + oi_score
    reason = f"费率 {fr_score}/5 ({fr_reason}) + OI变化 {oi_score}/5 ({oi_reason})"
    
    logger.debug("情绪面评分(双因子)", 
                 annualized_rate=annualized_rate, fr_score=fr_score,
                 oi_change_pct=oi_change_pct, oi_score=oi_score,
                 total=total_score)
    
    return total_score, reason
```

3. 修改 `score()` 方法签名：新增 `oi_change_rate: float` 参数，调用处改为 `self.calculate_sentiment_score(funding_rate, oi_change_rate)`

4. `ScoringResult.details['sentiment']` 中增加 `funding_rate_score`, `oi_change_score`, `oi_change_rate`, `annualized_rate` 字段

### 改动3：strategy.py — 新增OI变化率获取 + 修改调用

**文件**：`strategies/new_coin/strategy.py`

1. 新增 `_get_open_interest_ahead()` 方法：

```python
async def _get_open_interest_ahead(self, symbol: str, hours_ago: int = 3) -> float:
    """
    获取N小时前的OI数据（通过币安OI历史K线接口）

    GET /fapi/v1/openInterestHist
    params: symbol, period=5m, limit=N_hours*12+1, endTime=...
    
    Args:
        symbol: 交易对
        hours_ago: 回溯小时数
    
    Returns:
        N小时前的OI（美元），如果数据不足返回0
    """
    try:
        from datetime import datetime, timezone, timedelta
        now = datetime.now(timezone.utc)
        end_time = int(now.timestamp() * 1000)
        start_time = int((now - timedelta(hours=hours_ago + 1)).timestamp() * 1000)
        
        data = await self.binance_client._request(
            "GET",
            "/fapi/v1/openInterestHist",
            params={
                'symbol': symbol,
                'period': '5m',
                'startTime': start_time,
                'endTime': end_time,
                'limit': 100
            },
            signed=False
        )
        if data and len(data) > 0:
            # 取最接近hours_ago时刻的OI值（第一个数据点）
            oi_value = float(data[0].get('sumOpenInterest', 
                      data[0].get('sumOpenInterestValue', 0)))
            logger.debug(f"获取{hours_ago}h前OI: {symbol} = {oi_value}")
            return oi_value
        return 0.0
    except Exception as e:
        logger.warning(f"获取{hours_ago}h前OI失败: {symbol}, {e}")
        return 0.0
```

2. 修改 `analyze()` 方法（第195-219行）：

在获取 `oi_usd` 之后，新增OI变化率计算：

```python
# 获取合约数据
oi_usd = await self._get_open_interest(symbol)
total_volume = await self._get_total_volume(symbol)
funding_rate = await self._get_funding_rate(symbol)
current_price = float(klines[-1].get('close', 0))

# 计算OI变化率（获取3小时前的OI）
oi_3h_ago = await self._get_open_interest_ahead(symbol, hours_ago=3)
oi_change_rate = (oi_usd - oi_3h_ago) / oi_3h_ago if oi_3h_ago > 0 else 0.0
```

在 `score()` 调用中添加 `oi_change_rate` 参数：

```python
score_result = self.scoring_engine.score(
    ...,
    funding_rate=funding_rate,
    oi_change_rate=oi_change_rate,  # 新增
    ...
)
```

---

## 改动汇总

| 文件 | 改动类型 | 改动内容 |
|------|---------|---------|
| `config.yaml` | 替换 | `funding_rate` 节 → `sentiment` 双因子配置 |
| `scoring_engine.py` | 修改 | `calculate_sentiment_score()` 签名+逻辑；`score()` 签名；`ScoringResult.details` |
| `strategy.py` | 新增+修改 | `_get_open_interest_ahead()` 方法；`analyze()` 中新增OI变化率计算和参数传递 |

---

## 验证步骤

1. **语法检查**：`python3 -c "import ast; ast.parse(open(f).read())"` 对3个文件
2. **服务器部署（临时诊断模式）**：上传代码 → 重建容器 → 跳过整点对齐 → 立即执行一轮评分 → 查看飞书通知中情绪面评分是否变化
3. **CBRSUSDT模拟验证**：在服务器上用真实数据模拟双因子评分计算
4. **正式部署**：恢复整点对齐，重建容器，等待下一轮正常执行
5. **文档对齐检查**：确认代码改动与V4.0情绪面优化版规范一致

---

## 假设与决策

1. **OI历史数据来源**：使用币安 `GET /fapi/v1/openInterestHist` 接口获取5分钟颗粒度的OI历史
2. **OI变化率窗口**：3小时（配置可调）
3. **数据不足处理**：如果3小时前OI数据不可用（上线不足3小时），oi_change_rate = 0，OI因子得0分，只靠费率因子
4. **资金费率**：继续使用最新一次结算的费率（`limit=1`），而非前6个周期的平均值（策略已限定48h窗口，单一费率足以反映最新情绪）