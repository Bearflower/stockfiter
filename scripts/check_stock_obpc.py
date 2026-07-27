#!/usr/bin/env python3
"""
单只股票 OBPC 策略形态检测脚本

用法: python scripts/check_stock_obpc.py <股票代码或名称>
示例: python scripts/check_stock_obpc.py 600020
     python scripts/check_stock_obpc.py 中原高速
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 必须在这里加载 .env
from dotenv import load_dotenv
load_dotenv()

from datetime import datetime, timedelta
import pandas as pd
import requests
import json

from utils.logger import get_logger
from strategy.oversold_bounce.strategy import OversoldBounceStrategy

logger = get_logger()


def resolve_stock(query: str) -> dict:
    """根据代码或名称解析股票信息"""
    # 常见股票映射
    stock_map = {
        "600020": {"code": "600020", "name": "中原高速", "symbol": "600020.SH"},
        "中原高速": {"code": "600020", "name": "中原高速", "symbol": "600020.SH"},
    }
    if query in stock_map:
        return stock_map[query]

    # 尝试从代码推断
    if query.isdigit() and len(query) == 6:
        market = "SH" if query.startswith("6") else "SZ"
        return {"code": query, "name": query, "symbol": f"{query}.{market}"}

    return None


def fetch_kline_remote(code: str, symbol: str, days: int = 120) -> pd.DataFrame:
    """从远程 K 线服务获取数据"""
    url = "http://43.156.242.184:8765/api/v1/klines/latest"

    # 尝试不同的参数格式
    params_list = [
        {"symbol": symbol, "interval": "1d", "days": days},
        {"code": code, "symbol": symbol, "interval": "1d", "days": days},
        {"code": code, "symbol": symbol, "frequency": "d", "days": days},
    ]

    for params in params_list:
        try:
            resp = requests.get(url, params=params, timeout=15)
            if resp.status_code == 200:
                result = resp.json()
                kline_data = result.get("data", [])
                if kline_data and len(kline_data) > 0:
                    df = pd.DataFrame(kline_data)
                    if "date" in df.columns:
                        df["date"] = pd.to_datetime(df["date"])
                        df.sort_values("date", inplace=True)
                        df.reset_index(drop=True, inplace=True)
                    return df
            elif resp.status_code == 422:
                continue  # 参数不对，尝试下一组
            else:
                logger.debug(f"远程请求失败(参数={params}): HTTP {resp.status_code}")
        except Exception as e:
            logger.debug(f"远程请求异常(参数={params}): {e}")
            continue

    # 尝试 POST
    for body in [
        {"project": "stockfilter", "code": code, "frequency": "d", "days": days},
        {"symbol": symbol, "interval": "1d", "days": days},
    ]:
        try:
            resp = requests.post(url, json=body, timeout=15)
            if resp.status_code == 200:
                result = resp.json()
                kline_data = result.get("data", [])
                if kline_data and len(kline_data) > 0:
                    df = pd.DataFrame(kline_data)
                    if "date" in df.columns:
                        df["date"] = pd.to_datetime(df["date"])
                        df.sort_values("date", inplace=True)
                        df.reset_index(drop=True, inplace=True)
                    return df
        except Exception as e:
            logger.debug(f"POST 请求异常: {e}")

    return None


def fetch_kline_akshare(code: str, symbol: str, days: int = 120) -> pd.DataFrame:
    """从 akshare 获取 K 线数据"""
    try:
        import akshare as ak

        market = "SH" if symbol.endswith(".SH") else "SZ"
        symbol_ak = f"{market.lower()}{code}"

        end_date = datetime.now().strftime("%Y%m%d")
        start_date = (datetime.now() - timedelta(days=days + 30)).strftime("%Y%m%d")

        df = ak.stock_zh_a_hist(
            symbol=code,
            period="daily",
            start_date=start_date,
            end_date=end_date,
            adjust="qfq",
        )

        if df is None or len(df) == 0:
            return None

        df = df.rename(columns={
            "日期": "date",
            "开盘": "open",
            "最高": "high",
            "最低": "low",
            "收盘": "close",
            "成交量": "volume",
            "成交额": "amount",
        })

        for col in ["open", "high", "low", "close", "volume", "amount"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date").reset_index(drop=True)

        if len(df) > days:
            df = df.tail(days)

        return df

    except Exception as e:
        logger.warning(f"akshare 获取异常: {e}")
        return None


def fetch_kline_baostock(code: str, symbol: str, days: int = 120) -> pd.DataFrame:
    """从 Baostock 获取 K 线数据"""
    try:
        import baostock as bs
    except ImportError:
        logger.warning("baostock 未安装")
        return None

    market = "sh" if symbol.endswith(".SH") else "sz"
    bs_symbol = f"{market}.{code}"

    try:
        lg = bs.login()
        if lg.error_code != "0":
            logger.warning(f"Baostock 登录失败: {lg.error_msg}")
            return None

        # 计算起始日期
        from datetime import timedelta
        end_dt = datetime.now()
        start_dt = end_dt - timedelta(days=days + 30)
        start_date = start_dt.strftime("%Y-%m-%d")
        end_date = end_dt.strftime("%Y-%m-%d")

        rs = bs.query_history_k_data_plus(
            bs_symbol,
            "date,open,high,low,close,volume,amount,turn",
            start_date=start_date,
            end_date=end_date,
            frequency="d",
            adjustflag="3",
        )

        if rs.error_code != "0":
            bs.logout()
            logger.warning(f"Baostock 查询失败: {rs.error_msg}")
            return None

        data_list = []
        while rs.next():
            data_list.append(rs.get_row_data())

        bs.logout()

        if not data_list:
            return None

        columns = rs.fields
        df = pd.DataFrame(data_list, columns=columns)

        df["date"] = pd.to_datetime(df["date"])
        numeric_cols = ["open", "high", "low", "close", "volume", "amount"]
        for col in numeric_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        df.sort_values("date", inplace=True)
        df.reset_index(drop=True, inplace=True)

        if len(df) > days:
            df = df.tail(days)

        return df

    except Exception as e:
        logger.error(f"Baostock 获取异常: {e}")
        return None


def fetch_kline(code: str, symbol: str, days: int = 120) -> pd.DataFrame:
    """获取 K 线数据（akshare 优先，远程服务备用，Baostock 降级）"""
    # 1. 优先 akshare
    df = fetch_kline_akshare(code, symbol, days)
    if df is not None and len(df) >= 60:
        logger.info(f"从 akshare 获取到 {len(df)} 条数据")
        return df

    # 2. 远程服务
    df = fetch_kline_remote(code, symbol, days)
    if df is not None and len(df) >= 60:
        logger.info(f"从远程服务获取到 {len(df)} 条数据")
        return df

    # 3. 降级 Baostock
    logger.info("远程服务不可用，降级为 Baostock")
    df = fetch_kline_baostock(code, symbol, days)
    if df is not None and len(df) >= 60:
        logger.info(f"从 Baostock 获取到 {len(df)} 条数据")
        return df

    return None


def analyze_stock(query: str) -> dict:
    """主检测函数"""
    stock = resolve_stock(query)
    if stock is None:
        return {"error": f"无法识别股票: {query}"}

    code = stock["code"]
    name = stock["name"]
    symbol = stock["symbol"]

    print("=" * 70)
    print(f"OBPC 超跌反弹策略形态检测")
    print(f"股票: {code} {name} ({symbol})")
    print(f"检测时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)
    print()

    # 获取 K 线数据
    print("[1/4] 正在获取 K 线数据...")
    df = fetch_kline(code, symbol, days=120)

    if df is None or len(df) < 60:
        print(f"  X 无法获取足够的 K 线数据（需要 ≥60 条，实际: {len(df) if df is not None else 0}）")
        return {"error": "K线数据不足"}

    print(f"  OK 获取到 {len(df)} 条日 K 线数据")
    print(f"     日期范围: {df['date'].iloc[0].strftime('%Y-%m-%d')} ~ {df['date'].iloc[-1].strftime('%Y-%m-%d')}")
    print()

    # 初始化策略
    print("[2/4] 初始化 OBPC 策略...")
    strategy = OversoldBounceStrategy()
    print(f"  策略: {strategy.name} v{strategy.version}")
    print(f"  描述: {strategy.description}")
    print(f"  参数:")
    for k, v in strategy.params.items():
        print(f"    - {k}: {v}")
    print()

    # 执行分析
    print("[3/4] 正在分析形态...")
    signal = strategy.analyze(code, {"d": df})

    if signal is None:
        print("  X 未检测到符合策略的形态")
        print()
        print("[4/4] 检测结论")
        print("-" * 70)
        print("中原高速 (600020) 当前不满足 OBPC 超跌反弹策略形态。")
        print()
        print("可能的原因：")
        print("  1. 近期（20个交易日内）未出现超过 8% 的大跌")
        print("  2. 大跌后未出现明显的缩量信号")
        print("  3. 缩量后未出现放量大涨（量比≥1.2 且涨幅≥3%）")
        print("  4. 放量后未在 10 天内回踩到支撑位")
        print("  5. 20 日日均成交额不足 2000 万元")

        # 附加诊断
        print()
        print("-" * 70)
        print("附加诊断信息：")
        tail = df.tail(30)
        print(f"  最近 30 日最高价: {tail['high'].max():.2f}")
        print(f"  最近 30 日最低价: {tail['low'].min():.2f}")
        print(f"  最近 30 日最大波动: {((tail['high'].max() - tail['low'].min()) / tail['high'].max() * 100):.2f}%")
        print(f"  最近收盘价: {tail['close'].iloc[-1]:.2f}")
        print(f"  最近 20 日均成交额: {(tail['volume'].tail(20) * tail['close'].tail(20)).mean():.0f} 元")
        print(f"  最新成交量: {tail['volume'].iloc[-1]:.0f}")
        print(f"  60 日均量: {df['volume'].tail(60).mean():.0f}")

        # 检查最近几天涨跌
        recent = tail.tail(5)
        for idx, row in recent.iterrows():
            change = row['close'] - row['open']
            pct = change / row['open'] * 100
            print(f"  {row['date'].strftime('%Y-%m-%d')}: 开{row['open']:.2f} 收{row['close']:.2f} ({pct:+.2f}%) 量{row['volume']:.0f}")

        return {"matched": False, "code": code, "name": name}

    # 匹配成功
    detail = signal.detail
    print("  OK 检测到符合策略的形态！")
    print()

    print("[4/4] 检测结论")
    print("-" * 70)
    print(f"  股票:       {code} {name}")
    print(f"  大跌起始:   {detail.get('drop_start_date', 'N/A')}")
    print(f"  大跌跌幅:   {detail.get('drop_change', 0) * 100:.2f}%")
    print(f"  缩量日期:   {detail.get('shrink_date', 'N/A')}")
    print(f"  放量日期:   {detail.get('surge_date', 'N/A')}")
    print(f"  放量收盘:   {detail.get('surge_close', 0):.2f}")
    print(f"  回踩日期:   {detail.get('retrace_date', 'N/A')}")
    print(f"  回踩收盘:   {detail.get('retrace_close', 0):.2f}")
    print(f"  回踩最低:   {detail.get('retrace_low', 0):.2f}")
    print(f"  支撑位:     {detail.get('support_level', 0):.2f}")
    print(f"  止损位:     {detail.get('support_level', 0) * 0.97:.2f} (=支撑位 × 0.97)")
    print(f"  评分:       {signal.score:.2f}")
    print()
    print("  信号识别: ✓ 符合 OBPC 超跌反弹策略形态")
    print(f"  建议: 如果今日开盘价在支撑位 {detail.get('support_level', 0):.2f} 附近，可考虑入场")

    return {
        "matched": True,
        "code": code,
        "name": name,
        "detail": detail,
        "score": signal.score,
    }


if __name__ == "__main__":
    if len(sys.argv) < 2:
        result = analyze_stock("中原高速")
    else:
        result = analyze_stock(sys.argv[1])

    if "error" in result:
        print(f"\n错误: {result['error']}")
        sys.exit(1)