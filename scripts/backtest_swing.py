#!/usr/bin/env python3
"""
个股波段择时回测研究脚本（基线扫描）

目的：验证"对固定单只股票做波段择时"在 2020-2026 的表现，为后续优化提供基线。
研究对象：伊利股份(600887)、云图控股(002539)，各独立回测。

策略基线（4 个经典技术指标）：
  1. 均线交叉 MA(5/20)    —— 趋势跟随
  2. MACD(12/26/9) 金叉死叉 —— 趋势跟随（更灵敏）
  3. RSI(14) 超买超卖       —— 均值回归
  4. 布林带(20,2) 触轨      —— 均值回归

离场方式（2 种，分别回测对比）：
  - indicator：对侧信号离场（金叉进死叉出）
  - stop：固定止盈 + 固定止损（止盈 15% / 止损 8%）

时序约定（避免前视偏差）：
  - 信号在 T 日收盘产生（仅用 T 日及之前数据）
  - T+1 日开盘执行买入/卖出
  - 止盈止损在持仓期间用当日 high/low 盘中检查

成本（复用 OBPC 口径）：佣金 0.025% 双边 + 印花税 0.1% 卖出 + 滑点 0.1% 双边，
合计约 0.35% / 次完整交易。

数据：本地 CSV（后复权日线，从服务器 PostgreSQL 拉取）。
运行：python scripts/backtest_swing.py
"""

import os
import sys

import numpy as np
import pandas as pd

# ==================== 配置加载（H-1：禁止模块级业务阈值硬编码） ====================
def _load_swing_config(config_path: str = 'config/config.yaml') -> dict:
    """从 config.yaml 读取 section_backtest.swing 节（禁止硬编码）。

    当配置缺失时回退到脚本原默认值，保证向后兼容。

    Args:
        config_path: 配置文件路径

    Returns:
        dict: section_backtest.swing 完整配置字典
    """
    default_cfg = {
        'commission': 0.00025,
        'stamp_tax': 0.001,
        'slippage': 0.001,
        'take_profit': 0.15,
        'stop_loss': 0.08,
        'strategy_params': {
            'ma_cross': {'fast': 5, 'slow': 20},
            'macd': {'fast': 12, 'slow': 26, 'signal': 9},
            'rsi': {'period': 14, 'oversold': 30, 'overbought': 70},
            'boll': {'period': 20, 'num_std': 2.0},
        },
    }
    try:
        import yaml
        with open(config_path, 'r', encoding='utf-8') as f:
            full = yaml.safe_load(f) or {}
        cfg = (full.get('section_backtest') or {}).get('swing') or {}
        # strategy_params 需要深合并，防止只覆盖部分子项时其他子项丢失
        if 'strategy_params' in cfg:
            merged_sp = dict(default_cfg['strategy_params'])
            for name, params in cfg['strategy_params'].items():
                if name in merged_sp:
                    merged_sp[name] = dict(merged_sp[name], **params)
            cfg['strategy_params'] = merged_sp
        default_cfg.update(cfg)
    except Exception:
        # config 加载失败时用默认值继续，不阻断脚本运行
        pass
    return default_cfg


# 加载 swing 配置（启动时一次性读取，后续模块内直接用全局常量）
_sw_cfg = _load_swing_config()

# ==================== 配置（H-1：所有业务阈值从 section_backtest.swing 读取） ====================

# 数据文件路径（默认从环境变量读取，未设置时用本地缓存文件）
DATA_FILE = os.environ.get("SWING_DATA_FILE", "/tmp/obpc_two_stocks_ohlc.csv")

# 目标股票：code -> 名称（研究目标常量，不是业务阈值，保留）
STOCKS = {
    "600887": "伊利股份",
    "002539": "云图控股",
}

# H-1：交易成本（从 config 读，total 由三项推导保持一致，禁止双源）
COST = {
    "commission": _sw_cfg['commission'],   # 佣金（双边）
    "stamp_tax": _sw_cfg['stamp_tax'],      # 印花税（仅卖出）
    "slippage": _sw_cfg['slippage'],        # 滑点（双边）
}
COST["total"] = (
    COST["commission"] * 2
    + COST["stamp_tax"]
    + COST["slippage"] * 2
)

# H-1：各策略的技术指标参数（从 config 读）
STRATEGY_PARAMS = _sw_cfg['strategy_params']

# H-1：固定止盈止损参数（从 config 读）
STOP_PARAMS = {
    "take_profit": _sw_cfg['take_profit'],
    "stop_loss": _sw_cfg['stop_loss'],
}

# 离场方式（纯枚举标签，可保留硬编码，不属于业务阈值）
EXIT_MODES = ["indicator", "stop"]


# ==================== 数据加载 ====================

def load_data(file_path: str) -> pd.DataFrame:
    """加载本地 CSV，返回按 code 分组的 DataFrame 字典"""
    df = pd.read_csv(
        file_path,
        names=["code", "date", "open", "high", "low", "close", "volume"],
        dtype={"code": str},
    )
    df["date"] = pd.to_datetime(df["date"])
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return {code: g.sort_values("date").reset_index(drop=True)
            for code, g in df.groupby("code")}


# ==================== 技术指标计算 ====================

def calc_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """计算全部技术指标列"""
    close = df["close"]
    # 均线
    df["ma_fast"] = close.rolling(STRATEGY_PARAMS["ma_cross"]["fast"]).mean()
    df["ma_slow"] = close.rolling(STRATEGY_PARAMS["ma_cross"]["slow"]).mean()
    # MACD
    p = STRATEGY_PARAMS["macd"]
    ema_fast = close.ewm(span=p["fast"], adjust=False).mean()
    ema_slow = close.ewm(span=p["slow"], adjust=False).mean()
    df["dif"] = ema_fast - ema_slow
    df["dea"] = df["dif"].ewm(span=p["signal"], adjust=False).mean()
    # RSI（Wilder 平滑）
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta.where(delta < 0, 0.0))
    avg_gain = gain.ewm(alpha=1 / STRATEGY_PARAMS["rsi"]["period"], adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / STRATEGY_PARAMS["rsi"]["period"], adjust=False).mean()
    rs = avg_gain / avg_loss
    df["rsi"] = 100 - 100 / (1 + rs)
    # 布林带
    p = STRATEGY_PARAMS["boll"]
    mid = close.rolling(p["period"]).mean()
    std = close.rolling(p["period"]).std()
    df["boll_up"] = mid + p["num_std"] * std
    df["boll_low"] = mid - p["num_std"] * std
    return df


# ==================== 信号生成 ====================

def gen_signals(df: pd.DataFrame, strategy: str):
    """
    生成买入/卖出信号（布尔序列，信号在当日收盘产生）

    Returns:
        (buy, sell): 两个布尔 Series
    """
    if strategy == "ma_cross":
        buy = (df["ma_fast"] > df["ma_slow"]) & \
              (df["ma_fast"].shift(1) <= df["ma_slow"].shift(1))
        sell = (df["ma_fast"] < df["ma_slow"]) & \
               (df["ma_fast"].shift(1) >= df["ma_slow"].shift(1))
    elif strategy == "macd":
        buy = (df["dif"] > df["dea"]) & (df["dif"].shift(1) <= df["dea"].shift(1))
        sell = (df["dif"] < df["dea"]) & (df["dif"].shift(1) >= df["dea"].shift(1))
    elif strategy == "rsi":
        p = STRATEGY_PARAMS["rsi"]
        buy = (df["rsi"] < p["oversold"]) & (df["rsi"].shift(1) >= p["oversold"])
        sell = (df["rsi"] > p["overbought"]) & (df["rsi"].shift(1) <= p["overbought"])
    elif strategy == "boll":
        buy = (df["close"] < df["boll_low"]) & \
              (df["close"].shift(1) >= df["boll_low"].shift(1))
        sell = (df["close"] > df["boll_up"]) & \
               (df["close"].shift(1) <= df["boll_up"].shift(1))
    else:
        raise ValueError(f"未知策略：{strategy}")
    return buy.fillna(False), sell.fillna(False)


# ==================== 回测引擎 ====================

def run_backtest(df, buy_sig, sell_sig, exit_mode, tp, sl, init_capital: float = 1.0):
    """
    事件驱动回测，返回交易列表、日持仓标记序列和统一净值曲线（R20）

    时序：T 日收盘信号 → T+1 日开盘执行；止盈止损盘中检查

    R20 改造：
        - 新增 equity 列表：每根 K 线收盘时记录总资产 = 现金 + 持仓市值
        - 买入/卖出按实际成交价扣减成本，保证净值曲线真实反映交易结果
        - 返回三元组 (trades, held, equity)

    Args:
        df: OHLC K 线 DataFrame
        buy_sig: 买入信号布尔序列（T 日收盘产生）
        sell_sig: 卖出信号布尔序列
        exit_mode: 离场方式 'indicator' 或 'stop'
        tp: 固定止盈比例（exit_mode='stop' 时生效）
        sl: 固定止损比例
        init_capital: R20 新增：初始资金（以 close[0] 为基准的相对值，默认 1.0）

    Returns:
        tuple: (trades, held, equity)
            trades: 交易记录列表
            held: 每日是否持仓的布尔列表（用于报告）
            equity: R20 新增：统一净值曲线列表（长度=len(df)）
    """
    trades = []
    pos = None  # {"entry": float, "entry_date": str, "idx": int, "shares": float}
    held = [False] * len(df)
    # R20：统一净值曲线——每根 K 线收盘后的总资产
    equity = [init_capital] * len(df)

    # R20：现金账户和持仓数量（用全部资金满仓）
    cash = init_capital
    shares = 0.0

    for i in range(1, len(df)):
        today_open = float(df["open"].iloc[i])
        today_close = float(df["close"].iloc[i])

        if pos is not None:
            held[i] = True

        # ---- 卖出处理 ----
        if pos is not None:
            tp_price = pos["entry"] * (1 + tp)
            sl_price = pos["entry"] * (1 - sl)
            exit_price = None
            reason = None

            # R19：卖出优先级重构（原：止损→止盈→指标离场；新：指标离场→止损→止盈）
            # 指标离场是 T-1 收盘卖出信号 → 今日开盘执行，开盘价即第一笔成交价
            # 应优先于盘中止损/止盈（开盘离场后当日盘中已不在仓内）
            if sell_sig.iloc[i - 1]:
                exit_price = today_open
                reason = "指标离场"
            # 盘中止损（同一天同时触发时按止损价）
            elif float(df["low"].iloc[i]) <= sl_price:
                # R07：跳空低开越过止损线 → 按开盘价成交（与 backtest_obpc.py 一致）
                exit_price = min(sl_price, today_open)
                reason = "止损"
            # 固定止盈（仅 stop 模式）
            elif exit_mode == "stop" and float(df["high"].iloc[i]) >= tp_price:
                exit_price = tp_price
                reason = "止盈"

            if exit_price is not None:
                # R20：卖出时的实际成交价（扣除滑点）
                actual_exit = exit_price * (1 - COST["slippage"])
                # 卖出成本：佣金 + 印花税
                sell_cost = actual_exit * (COST["commission"] + COST["stamp_tax"])
                # 更新现金账户
                cash += actual_exit * pos["shares"] - sell_cost
                shares = 0.0

                net = exit_price / pos["entry"] - 1 - COST["total"]
                trades.append({
                    "entry_date": pos["entry_date"],
                    "exit_date": df["date"].iloc[i].strftime("%Y-%m-%d"),
                    "entry": pos["entry"],
                    "exit": exit_price,
                    "ret": net,
                    "reason": reason,
                    "hold_days": i - pos["idx"],
                })
                held[i] = True  # 出场当日仍算持仓（按开盘至出场价）
                pos = None

        # ---- 买入处理 ----
        if pos is None and buy_sig.iloc[i - 1]:
            # R20：买入时用全部现金（扣除买入成本后满仓）
            # 买入价 = 开盘价 + 滑点
            actual_entry = today_open * (1 + COST["slippage"])
            buy_cost = actual_entry * COST["commission"]
            # 满仓买入（扣除成本后）
            shares = (cash - buy_cost) / actual_entry
            cash = 0.0

            pos = {
                "entry": actual_entry,
                "entry_date": df["date"].iloc[i].strftime("%Y-%m-%d"),
                "idx": i,
                "shares": shares,
            }
            held[i] = True

        # R20：每日收盘后记录总资产 = 现金 + 持仓市值
        equity[i] = cash + shares * today_close

    # R20：期末未平仓，按最后收盘价平仓
    if pos is not None:
        i = len(df) - 1
        close_price = float(df["close"].iloc[i])
        actual_exit = close_price * (1 - COST["slippage"])
        sell_cost = actual_exit * (COST["commission"] + COST["stamp_tax"])
        cash += actual_exit * pos["shares"] - sell_cost
        shares = 0.0
        # 更新 equity 为平仓后的现金
        equity[i] = cash

        net = close_price / pos["entry"] - 1 - COST["total"]
        trades.append({
            "entry_date": pos["entry_date"],
            "exit_date": df["date"].iloc[i].strftime("%Y-%m-%d"),
            "entry": pos["entry"],
            "exit": close_price,
            "ret": net,
            "reason": "期末平仓",
            "hold_days": i - pos["idx"],
        })

    return trades, held, equity


# ==================== 绩效统计 ====================

def compute_metrics(df, trades, held, equity=None):
    """计算回测绩效指标

    R20 改造：统一从 equity 净值曲线导出 total_ret / annual_ret / max_dd，
    删除旧的 close-to-close 持仓涨跌回撤逻辑。

    Args:
        df: K 线 DataFrame（用于计算交易天数和基准）
        trades: 交易记录列表
        held: 每日持仓标记列表（保留用于报告）
        equity: R20 新增：统一净值曲线列表。None 时回退到旧逻辑（兼容旧调用）

    Returns:
        dict: 绩效指标字典
    """
    n = len(df)
    trading_days = n - 1

    # 买入持有基准（含买入成本，期末卖出成本）
    bh_ret = df["close"].iloc[-1] / df["close"].iloc[0] - 1 - COST["total"]

    if not trades:
        return {
            "trades": 0, "win_rate": None, "avg_ret": None,
            "total_ret": 0.0, "annual_ret": 0.0, "max_dd": 0.0,
            "bh_ret": bh_ret, "avg_hold_days": None,
        }

    rets = np.array([t["ret"] for t in trades])

    # R20：统一从 equity 净值曲线导出收益率和回撤
    if equity is not None:
        # 转 pandas Series 以便统一计算
        nav = pd.Series(equity, index=df.index) / equity[0]
        # total_ret / annual_ret 从净值曲线起点终点导出
        total_ret = float(nav.iloc[-1] - 1)
        years = trading_days / 252
        annual_ret = (1 + total_ret) ** (1 / years) - 1 if years > 0 else 0.0
        # max_dd：统一从净值曲线计算
        peak = nav.cummax()
        max_dd = float(((nav - peak) / peak).min())
    else:
        # 回退：旧逻辑（close-to-close 持仓涨跌）
        total_ret = float(np.prod(1 + rets) - 1)
        years = trading_days / 252
        annual_ret = (1 + total_ret) ** (1 / years) - 1 if years > 0 else 0.0
        nav_old = 1.0
        peak = 1.0
        max_dd = 0.0
        for i in range(1, n):
            if held[i]:
                nav_old *= float(df["close"].iloc[i]) / float(df["close"].iloc[i - 1])
            peak = max(peak, nav_old)
            max_dd = min(max_dd, nav_old / peak - 1)

    win_rate = float((rets > 0).sum() / len(rets))
    avg_ret = float(rets.mean())
    avg_hold = float(np.mean([t["hold_days"] for t in trades]))

    return {
        "trades": len(trades),
        "win_rate": win_rate,
        "avg_ret": avg_ret,
        "total_ret": total_ret,
        "annual_ret": annual_ret,
        "max_dd": max_dd,
        "bh_ret": bh_ret,
        "avg_hold_days": avg_hold,
    }


# ==================== 报告输出 ====================

def fmt_pct(x):
    return "  -  " if x is None else f"{x * 100:+.1f}%"


def print_report(results):
    """打印基线对比报告"""
    print("=" * 100)
    print("个股波段择时回测基线对比  (2020-01 ~ 2026-09, 成本已计 0.35%/次)")
    print("=" * 100)

    for stock_code, stock_name in STOCKS.items():
        print(f"\n【{stock_name} {stock_code}】")
        print("-" * 100)
        header = (f"{'策略':<8} {'离场':<9} {'交易数':>5} {'胜率':>8} "
                  f"{'单笔均收益':>10} {'累计收益':>10} {'年化':>9} "
                  f"{'最大回撤':>9} {'持仓天数':>8} {'买持基准':>9}")
        print(header)
        print("-" * 100)

        strategies = list(STRATEGY_PARAMS.keys())
        for strat in strategies:
            for mode in EXIT_MODES:
                r = results[stock_code][strat][mode]
                print(f"{strat:<8} {mode:<9} {r['trades']:>5} "
                      f"{fmt_pct(r['win_rate']):>8} {fmt_pct(r['avg_ret']):>10} "
                      f"{fmt_pct(r['total_ret']):>10} {fmt_pct(r['annual_ret']):>9} "
                      f"{fmt_pct(r['max_dd']):>9} "
                      f"{r['avg_hold_days'] if r['avg_hold_days'] is not None else '-':>8} "
                      f"{fmt_pct(r['bh_ret']):>9}")

        print("-" * 100)
        print(f"买入持有基准（{stock_name}）: {fmt_pct(results[stock_code]['_bh'])}")


def main():
    if not os.path.exists(DATA_FILE):
        print(f"数据文件不存在：{DATA_FILE}")
        print("请先从服务器导出数据，例如：")
        print("  ssh root@server 'docker exec ... psql ...' > /tmp/obpc_two_stocks_ohlc.csv")
        sys.exit(1)

    data = load_data(DATA_FILE)
    results = {}

    for code, name in STOCKS.items():
        if code not in data:
            print(f"警告：数据中缺少 {code}")
            continue
        df = calc_indicators(data[code].copy())
        results[code] = {"_bh": df["close"].iloc[-1] / df["close"].iloc[0] - 1 - COST["total"]}

        for strat in STRATEGY_PARAMS:
            buy, sell = gen_signals(df, strat)
            results[code][strat] = {}
            for mode in EXIT_MODES:
                # R20：run_backtest 返回三元组 (trades, held, equity)
                trades, held, equity = run_backtest(
                    df, buy, sell, mode,
                    STOP_PARAMS["take_profit"], STOP_PARAMS["stop_loss"],
                )
                # R20：compute_metrics 接收 equity，统一从净值曲线导出指标
                results[code][strat][mode] = compute_metrics(df, trades, held, equity)

    print_report(results)


if __name__ == "__main__":
    main()
