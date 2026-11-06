"""伊利股份趋势 RSI 波段信号；仅接受已确认的前复权日线。"""

from pathlib import Path
import math
from typing import Dict, List, Optional

import pandas as pd
import yaml

from strategy.base import BaseStrategy, DataRequirement, Signal


DEFAULT_CONFIG = Path(__file__).resolve().parents[2] / "config" / "config.yaml"


def load_strategy_params(config_path: Path = DEFAULT_CONFIG) -> Dict:
    """读取单一策略参数源；缺项时拒绝计算。"""
    with open(config_path, encoding="utf-8") as stream:
        config = yaml.safe_load(stream) or {}
    params = ((config.get("strategies") or {}).get("yili_swing") or {}).get("params")
    required = ("code", "period", "oversold", "overbought", "fast_ma",
                "slow_ma", "stop_loss")
    if not isinstance(params, dict) or any(key not in params for key in required):
        raise ValueError("伊利波段策略配置缺少必需参数")
    if (not 0 < params["oversold"] < params["overbought"] < 100
            or not 0 < params["fast_ma"] < params["slow_ma"]
            or params["period"] <= 0 or not 0 < params["stop_loss"] < 1):
        raise ValueError("伊利波段策略参数范围无效")
    return dict(params)


def calculate_indicators(df: pd.DataFrame, params: Dict) -> pd.DataFrame:
    """计算与研究回测共用的 Wilder RSI 和均线。"""
    result = df.copy()
    close = result["close"].astype(float)
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1 / params["period"], adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / params["period"], adjust=False).mean()
    result["rsi_trend"] = 100 - 100 / (1 + avg_gain / avg_loss)
    result["ma_trend_fast"] = close.rolling(params["fast_ma"]).mean()
    result["ma_trend_slow"] = close.rolling(params["slow_ma"]).mean()
    return result


def generate_signals(df: pd.DataFrame, params: Dict):
    """仅用当日及之前的收盘指标生成原始信号。"""
    rsi = df["rsi_trend"]
    buy = ((df["ma_trend_fast"] > df["ma_trend_slow"])
           & (rsi < params["oversold"])
           & (rsi.shift(1) >= params["oversold"]))
    sell = ((rsi > params["overbought"])
            & (rsi.shift(1) <= params["overbought"]))
    return buy.fillna(False), sell.fillna(False)


class YiliSwingStrategy(BaseStrategy):
    """固定标的的独立信号策略，不维护真实账户仓位。"""

    name = "yili_swing"
    version = "v1"
    description = "伊利股份前复权日线趋势 RSI 波段策略"

    def _get_default_params(self) -> Dict:
        return load_strategy_params()

    def declare_data_requirements(self, codes: List[str]) -> List[DataRequirement]:
        if self.params["code"] not in codes:
            return []
        return [DataRequirement(code=self.params["code"], frequency="d",
                                lookback=self.params["slow_ma"])]

    def indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        return calculate_indicators(df, self.params)

    def signals(self, indicators: pd.DataFrame):
        return generate_signals(indicators, self.params)

    def snapshot(self, code: str, data: Dict[str, pd.DataFrame]) -> Dict:
        """返回最后一个完整交易日的原始信号与指标。"""
        if code != self.params["code"]:
            raise ValueError("股票代码不是本策略标的")
        df = data.get("d")
        if df is None or df.attrs.get("adjustment") != "front":
            raise ValueError("伊利波段策略需要已确认的前复权日线")
        if len(df) < self.params["slow_ma"]:
            raise ValueError("伊利波段策略数据不足")
        if df["date"].duplicated().any() or not df["date"].is_monotonic_increasing:
            raise ValueError("伊利波段策略日期必须唯一且递增")
        frame = self.indicators(df)
        buy, sell = self.signals(frame)
        last = frame.iloc[-1]
        if not all(math.isfinite(float(last[column])) for column in
                   ("rsi_trend", "ma_trend_fast", "ma_trend_slow", "close")):
            raise ValueError("伊利波段策略最新指标尚未定义")
        return {
            "date": pd.Timestamp(last["date"]).strftime("%Y-%m-%d"),
            "signal": "buy" if bool(buy.iloc[-1]) else "sell" if bool(sell.iloc[-1]) else "hold",
            "rsi": float(last["rsi_trend"]),
            "ma_fast": float(last["ma_trend_fast"]),
            "ma_slow": float(last["ma_trend_slow"]),
            "close": float(last["close"]),
        }

    def analyze(self, code: str, data: Dict[str, pd.DataFrame]) -> Optional[Signal]:
        if code != self.params["code"]:
            return None
        snapshot = self.snapshot(code, data)
        if snapshot["signal"] == "hold":
            return None
        return Signal(
            code=code, signal_type=snapshot["signal"],
            signal_date=snapshot["date"], strategy_name=self.name,
            strategy_version=self.version, detail=snapshot,
            backtest_params={"stop_loss": self.params["stop_loss"]},
        )
