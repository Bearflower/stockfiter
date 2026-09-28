#!/usr/bin/env python3
"""
截面策略回测研究脚本（择时 × 截面）

研究问题：验证"择时 + 截面"策略在 2020-2026 的表现，回答三个问题：
  1. 纯择时（温度计控制仓位）能否跑赢等权满仓、降低回撤？
  2. 纯截面（动量/反转选 Top K）能否跑出超额？
  3. 择时 + 截面叠加是 1+1>2 还是相互抵消？

6 组配置（因子正交拆解）：
  B0    = 无择时 + 无截面（等权全池买入持有）
  T     = 温度计4档仓位 + 等权全池
  C-mom = 满仓 + 动量 Top K
  C-rev = 满仓 + 反转 Top K
  TC-mom= 温度计 + 动量 Top K
  TC-rev= 温度计 + 反转 Top K

时序约定（避免前视偏差）：调仓日 T 收盘算信号 → T+1 生效，持有到下一调仓日。

数据：本地 /tmp/full_history/（2911 只前复权）+ /tmp/index_000300_fq.csv（指数前复权）。
运行：python scripts/backtest_cross_section.py
"""

import os
import sys

import numpy as np
import pandas as pd

# ==================== 配置 ====================
DATA_DIR = "/tmp/full_history"
INDEX_FILE = "/tmp/index_000300_fq.csv"

REBALANCE_DAYS = 20       # 调仓周期（交易日）
TOP_K = 20                # 截面持仓数量
LOOKBACKS = [20, 60]      # 因子回看期（动量/反转各跑两档）
COST = 0.0035             # 单次完整交易成本（约0.35%，复用 OBPC 口径）

# 温度计参数
MA_PERIOD = 20            # 趋势均线周期
SLOPE_LOOKBACK = 5        # 斜率回看
MACD = (12, 26, 9)        # MACD 参数
VOL_PERIOD = 20           # 波动率计算周期


# ==================== 数据加载 ====================

def load_panel():
    """加载 2911 只股票的 close 面板（index=date, columns=code）"""
    files = sorted(f for f in os.listdir(DATA_DIR) if f.endswith(".csv"))
    closes = {}
    for f in files:
        code = f.replace(".csv", "")
        df = pd.read_csv(
            os.path.join(DATA_DIR, f), usecols=["date", "close"]
        )
        df["date"] = pd.to_datetime(df["date"])
        closes[code] = df.set_index("date")["close"]
    panel = pd.DataFrame(closes).sort_index()
    # 前复权数据，填充停牌日（用前收盘价，即停牌日收益为 0）
    panel = panel.ffill()
    return panel


def load_index():
    """加载指数数据，返回含 close 的 DataFrame"""
    df = pd.read_csv(INDEX_FILE)
    df["date"] = pd.to_datetime(df["date"])
    return df.set_index("date").sort_index()


# ==================== 择时温度计 ====================

def calc_temperature(index_df, panel):
    """
    计算市场温度计（0-100 分）→ 映射到 4 档仓位

    4 个维度各 0-25 分：
      趋势：指数 MA20 斜率
      动量：指数 MACD DIF
      波动：指数历史波动率
      宽度：全市场上涨家数占比
    """
    close = index_df["close"]
    n = len(close)

    # 维度1：趋势（MA20 斜率，>0.002 满分，<-0.002 零分，线性）
    ma20 = close.rolling(MA_PERIOD).mean()
    slope = (ma20 - ma20.shift(SLOPE_LOOKBACK)) / ma20.shift(SLOPE_LOOKBACK)
    score_trend = ((slope + 0.002) / 0.004).clip(0, 1) * 25

    # 维度2：动量（MACD DIF，>0 满分，<0 零分，二值）
    ema_fast = close.ewm(span=MACD[0], adjust=False).mean()
    ema_slow = close.ewm(span=MACD[1], adjust=False).mean()
    dif = ema_fast - ema_slow
    score_momentum = (dif > 0).astype(float) * 25

    # 维度3：波动（历史波动率越低分越高）
    vol = close.pct_change().rolling(VOL_PERIOD).std()
    vol_med = vol.median()
    # 波动率低于中位数满分，高于中位数2倍零分，线性
    score_vol = (2 - (vol / vol_med)).clip(0, 1) * 25

    # 维度4：宽度（上涨家数占比，>0.6 满分，<0.4 零分，线性）
    up_ratio = (panel.diff() > 0).sum(axis=1) / panel.notna().sum(axis=1)
    score_breadth = ((up_ratio - 0.4) / 0.2).clip(0, 1) * 25

    total = score_trend + score_momentum + score_vol + score_breadth

    # 映射到 4 档仓位
    def to_position(score):
        return np.where(score <= 25, 0.0,
               np.where(score <= 50, 0.3,
               np.where(score <= 75, 0.6, 1.0)))

    pos = pd.Series(to_position(total), index=total.index)
    return total, pos


# ==================== 截面因子 ====================

def calc_factor(panel, lookback):
    """计算动量/反转因子（过去 N 日涨跌幅），返回 DataFrame"""
    return panel / panel.shift(lookback) - 1


# ==================== 组合回测 ====================

def run_backtest(panel, position_series, factor_df, use_section, mode):
    """
    组合回测（择时仓位 × 截面选股）

    Args:
        panel: close 面板
        position_series: 日度目标仓位（0/0.3/0.6/1.0），Series 对齐 date
        factor_df: 因子值 DataFrame（动量/反转），use_section=True 时用于选股
        use_section: 是否启用截面选股（否则等权全池）
        mode: 'momentum' 取因子最大的 Top K；'reversal' 取因子最小的 Top K

    Returns:
        dict: 绩效指标
    """
    # ========== 前视偏差修复 ==========
    # 统一将信号整体向后 shift 一期，避免 T 日收盘信号覆盖 T 日本身收益。
    # 时序约定：T 日收盘算出的因子/仓位 → T+1 生效，持有到下一调仓日。
    position_series = position_series.shift(1).fillna(0.0)
    if factor_df is not None:
        factor_df = factor_df.shift(1)  # 首行 shift 后为 NaN，后续 dropna 自然过滤，选股跳过首日
    # =================================

    returns = panel.pct_change().fillna(0)  # 日度收益率，首日 pct_change 为 NaN 时显式置 0
    n_days = len(panel)
    dates = panel.index

    # 调仓日：每 REBALANCE_DAYS 一个
    rebalance_days = list(range(0, n_days, REBALANCE_DAYS))

    # 持仓：记录每个调仓日到下个调仓日之间的持仓股票集合
    # 简化：逐日计算组合收益
    daily_ret = pd.Series(0.0, index=dates)

    holdings = None  # 当前持仓（等权权重 dict 或 None 表示满仓等权全池）
    target_pos = 0.0

    for i in range(n_days):
        date = dates[i]

        # 调仓日：更新持仓和目标仓位
        if i in rebalance_days:
            target_pos = position_series.iloc[i] if i < len(position_series) else 0.0
            if use_section:
                # 截面选股：取当日因子，选 Top K
                f = factor_df.iloc[i].dropna()
                if len(f) >= TOP_K:
                    if mode == "momentum":
                        selected = f.nlargest(TOP_K).index
                    else:  # reversal
                        selected = f.nsmallest(TOP_K).index
                    holdings = list(selected)
                else:
                    holdings = list(f.index)
            else:
                holdings = None  # 等权全池

        # 当日组合收益（信号已在入口处 shift 一期：用 T-1 日收盘算出的仓位/因子 → T 日生效，
        # 持有到下一调仓日。returns 首日已 fillna(0)，首日收益显式为 0）
        if holdings is None:
            # 等权全池：当日全池等权收益
            day_ret = returns.iloc[i].mean()
        else:
            day_ret = returns.iloc[i][holdings].mean()

        daily_ret.iloc[i] = day_ret * target_pos

    # 扣成本：调仓日扣 COST × 换手率（简化：每次调仓扣 COST × 仓位）
    for i in rebalance_days:
        if i > 0:
            daily_ret.iloc[i] -= COST * position_series.iloc[i]

    # 净值曲线
    nav = (1 + daily_ret).cumprod()
    total_ret = nav.iloc[-1] - 1
    years = n_days / 252
    annual_ret = (1 + total_ret) ** (1 / years) - 1

    # 最大回撤
    peak = nav.cummax()
    max_dd = (nav / peak - 1).min()

    # 月度胜率
    monthly = (1 + daily_ret).resample("ME").prod() - 1
    win_rate = (monthly > 0).mean()

    return {
        "total_ret": total_ret,
        "annual_ret": annual_ret,
        "max_dd": max_dd,
        "win_rate": win_rate,
        "sharpe": daily_ret.mean() / daily_ret.std() * np.sqrt(252),
    }


# ==================== 主流程 ====================

def main():
    print("加载面板数据（2911 只股票）...")
    panel = load_panel()
    print(f"面板：{panel.shape[0]} 交易日 × {panel.shape[1]} 只股票")

    print("加载指数 + 计算温度计...")
    index_df = load_index()
    # 对齐指数和面板的日期
    common_dates = panel.index.intersection(index_df.index)
    panel = panel.loc[common_dates]
    index_df = index_df.loc[common_dates]
    _, position = calc_temperature(index_df, panel)
    position = position.reindex(common_dates).fillna(0.0)

    print("计算因子...")
    factors = {lb: calc_factor(panel, lb) for lb in LOOKBACKS}

    print("\n" + "=" * 90)
    print("截面策略回测结果（2020-01 ~ 2026-09，成本 0.35%/次，调仓每 20 日，Top 20）")
    print("=" * 90)
    print(f"{'配置':<16} {'累计收益':>10} {'年化':>9} {'最大回撤':>9} {'月胜率':>8} {'夏普':>7}")
    print("-" * 90)

    results = {}
    # B0：满仓 + 等权（用 run_backtest 的 use_section=False + position=1）
    pos_full = pd.Series(1.0, index=common_dates)
    results["B0"] = run_backtest(panel, pos_full, None, False, None)

    # T：温度计 + 等权全池
    results["T"] = run_backtest(panel, position, None, False, None)

    for lb in LOOKBACKS:
        f = factors[lb]
        # C-mom / C-rev：满仓 + 截面
        results[f"C-mom({lb})"] = run_backtest(panel, pos_full, f, True, "momentum")
        results[f"C-rev({lb})"] = run_backtest(panel, pos_full, f, True, "reversal")
        # TC-mom / TC-rev：温度计 + 截面
        results[f"TC-mom({lb})"] = run_backtest(panel, position, f, True, "momentum")
        results[f"TC-rev({lb})"] = run_backtest(panel, position, f, True, "reversal")

    for name, r in results.items():
        print(f"{name:<16} {r['total_ret']*100:>+9.1f}% {r['annual_ret']*100:>+8.1f}% "
              f"{r['max_dd']*100:>+8.1f}% {r['win_rate']*100:>7.1f}% {r['sharpe']:>7.2f}")
    print("=" * 90)


if __name__ == "__main__":
    main()
