#!/usr/bin/env python3
"""
每日形态扫描脚本（T 日收盘后运行）

功能：
1. 扫描全市场股票，找出当日完成回踩确认的股票
2. 保存信号到 JSON 文件
3. 供次日飞书推送使用
"""

import json
import os
import sys
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List

import pandas as pd
import yaml

from utils.logger import get_logger
from data.database import DatabaseManager
from strategy.oversold_bounce.strategy import OversoldBounceStrategy
from strategy.oversold_bounce.risk_control import (
    RiskControlConfig,
    RiskController,
    MarketContext,
    RealtimeMarketContextProvider,
    DatabaseMonthlyCounter,
    cap_by_monthly_quota,
)

logger = get_logger()


def load_config(config_file: str = 'config/config.yaml') -> Dict:
    """加载配置文件"""
    with open(config_file, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)


def get_latest_trading_date() -> str:
    """获取最近一个交易日（假设今天就是交易日）"""
    today = datetime.now()
    if today.weekday() >= 5:
        today = today - timedelta(days=today.weekday() - 4)
    return today.strftime('%Y-%m-%d')


def scan_daily_signals(config: Dict, output_dir: str = 'signals', signal_date_override: str = None) -> List:
    """
    扫描当日信号

    Args:
        config: 配置字典
        output_dir: 输出目录
        signal_date_override: 强制指定信号日期（R03：早晨重扫场景用当天日期而非 get_latest_trading_date）

    Returns:
        list: 信号列表
    """
    print("=" * 80)
    print("每日形态扫描")
    print("=" * 80)

    # R03：优先使用调用方显式指定的信号日期（早晨重扫场景）
    # 否则才按 get_latest_trading_date() 推断最近交易日
    if signal_date_override:
        signal_date = signal_date_override
    else:
        signal_date = get_latest_trading_date()
    print(f"扫描日期：{signal_date}")
    print()

    # 从数据库获取过滤后的股票列表
    db = DatabaseManager()

    try:
        # 从配置读取股票列表文件路径，文件不存在时回退到数据库
        stocks_file = config.get('global', {}).get('stock_list_file', '')
        if stocks_file and os.path.exists(stocks_file):
            stock_list_df = pd.read_csv(stocks_file, dtype={'code': str})
            print(f"从固定列表获取到 {len(stock_list_df)} 只沪深主板股票")
        else:
            stock_pool_config = config.get('global', {}).get('stock_pool', {})
            stock_list_df = db.get_stock_list(stock_pool_config)
            print(
                f"从数据库获取到 {len(stock_list_df)} 只股票"
                if stock_list_df is not None
                else "数据库获取股票列表失败"
            )
    except Exception as e:
        logger.error(f"数据库获取失败：{e}")
        return []

    if stock_list_df is None or stock_list_df.empty:
        print("股票列表为空")
        return []

    # 初始化策略
    strategies_config = config.get('strategies', {})
    ob_config = strategies_config.get('oversold_bounce', {})
    params = ob_config.get('params', {})
    strategy = OversoldBounceStrategy(params=params)

    # 构建风控控制器（三层风控：评分阈值 + 大盘趋势 + 单月上限）
    risk_config = RiskControlConfig.from_params(params)
    risk_controller = RiskController(risk_config)
    market_context_provider = RealtimeMarketContextProvider(db, risk_config)
    monthly_counter = DatabaseMonthlyCounter(db)

    # 大盘环境预检查（扫描前获取当日大盘上下文）
    # 若当日大盘即被风控过滤，可提前返回空列表，避免无谓遍历
    market_context = None
    if risk_config.index_filter_enabled:
        try:
            market_context = market_context_provider.get_context(signal_date)
        except Exception as e:
            print(f"警告：大盘环境检查失败（{e}），继续扫描")
            market_context = None

        if market_context is not None:
            # 仅检查大盘趋势过滤（风控1），提前拦截
            trend_result = risk_controller.check_market_trend(market_context)
            if not trend_result.passed:
                print(f"大盘环境不佳，今日不开新仓")
                print(f"  原因：{trend_result.filter_reason}")
                db.close()
                return []
            else:
                print(f"大盘环境正常（沪深300 {market_context.index_close:.2f}）")
                if market_context.is_weak_market:
                    print(f"  注意：当前为弱势市场（沪深300 < MA60），评分阈值将上调至 {risk_config.score_threshold_weak}")

                # V2.1 新增：输出 MACD 动量过滤信息
                if risk_config.index_filter_method == 'macd':
                    if market_context.index_dif is not None:
                        macd_status = "多头" if market_context.macd_bullish else "空头"
                        logger.info(
                            f"  V2.1 MACD 动量：DIF={market_context.index_dif:.4f}，"
                            f"DEA={market_context.index_dea:.4f}，状态={macd_status}"
                        )
                    else:
                        logger.info(f"  V2.1 MACD 动量：数据不足，无法计算")

                # V2.1 新增：输出均线斜率过滤信息
                if risk_config.slope_filter_enabled:
                    slope_period = risk_config.slope_ma_period
                    if slope_period == 20:
                        slope_val = market_context.ma20_slope
                        slope_up = market_context.ma20_slope_up
                    else:
                        slope_val = market_context.ma60_slope
                        slope_up = market_context.ma60_slope_up

                    if slope_val is not None:
                        slope_status = "向上" if slope_up else "向下"
                        logger.info(
                            f"  V2.1 均线斜率：MA{slope_period} 斜率={slope_val:.6f}，"
                            f"状态={slope_status}（阈值 {risk_config.slope_threshold}）"
                        )
                    else:
                        logger.info(f"  V2.1 均线斜率：MA{slope_period} 数据不足，无法计算")

                # V2.1 新增：输出评分调整信息
                if risk_config.score_adjustment_enabled:
                    strength = market_context.market_strength
                    strength_names = {
                        'strong': '强势',
                        'weak': '弱势',
                        'extreme_weak': '极弱',
                        'unknown': '未知',
                    }
                    strength_name = strength_names.get(strength, strength)
                    # 获取对应的评分调整系数
                    if strength == 'extreme_weak':
                        multiplier = risk_config.score_multiplier_extreme_weak
                    elif strength == 'strong':
                        multiplier = risk_config.score_multiplier_strong
                    elif strength == 'weak':
                        multiplier = risk_config.score_multiplier_weak
                    else:
                        multiplier = 1.0
                    logger.info(
                        f"  V2.1 评分调整：市场强度={strength_name}，"
                        f"评分系数={multiplier}"
                    )
        else:
            # M-1：freshness_strategy='skip' → 风控数据不可用就不开新仓
            # 原代码只跳过大盘过滤继续扫描，实际变成"放行所有"，偏离设计意图
            # 设计文档 D-3 定稿：skip = 该日判定"风控数据不可用"并跳过相关开仓
            if risk_config.freshness_strategy == 'skip':
                print(f"警告：freshness_strategy=skip 且指数数据不可用，当日停止开仓")
                logger.warning(
                    "[M-1] 指数数据过期/缺失，freshness_strategy=skip 触发，"
                    "当日所有开仓跳过。signal_date=%s", signal_date
                )
                db.close()
                return []
            print(f"警告：获取大盘上下文失败，跳过大盘过滤")
        print()
    else:
        # 大盘过滤关闭时，构造默认上下文（非弱势市场，使用默认评分阈值）
        market_context = None

    print(f"开始扫描 {len(stock_list_df)} 只股票...")
    print()

    # 读取信号间隔控制参数
    signal_cooldown_days = params.get('signal_cooldown_days', 60)
    max_signals_per_year = params.get('max_signals_per_year', 2)

    # 读取K线数据获取参数（避免硬编码）
    kline_history_days = params.get('kline_history_days', 120)
    min_kline_length = params.get('min_kline_length', 60)

    signals = []

    for idx, row in stock_list_df.iterrows():
        code = row['code']
        name = row['name']

        try:
            kline_df = db.get_kline_history(code, days=kline_history_days)

            if kline_df is None or len(kline_df) < min_kline_length:
                continue
        except Exception as e:
            logger.debug(f"{code} 获取 K 线失败：{e}")
            continue

        try:
            signal = strategy.analyze(code, {'d': kline_df})

            if signal:
                retrace_date = signal.detail.get('retrace_date', '')

                if retrace_date:
                    retrace_date_str = str(retrace_date)[:10]

                    if (
                        retrace_date_str >= signal_date
                        or (
                            datetime.strptime(signal_date, '%Y-%m-%d')
                            - datetime.strptime(retrace_date_str[:10], '%Y-%m-%d')
                        ).days <= 1
                    ):
                        # 信号间隔控制：检查冷却期
                        last_signal_date = db.get_last_signal_date(code)
                        if last_signal_date:
                            days_since_last = (
                                datetime.strptime(signal_date, '%Y-%m-%d')
                                - datetime.strptime(str(last_signal_date)[:10], '%Y-%m-%d')
                            ).days
                            cooldown_threshold = signal_cooldown_days
                            if days_since_last < cooldown_threshold:
                                print(f"{code} {name}: 跳过 - 距上次信号仅 {days_since_last} 天（冷却期 {cooldown_threshold} 天）")
                                continue

                        # 信号间隔控制：检查年度信号次数
                        signal_count = db.get_signal_count_this_year(code)
                        if signal_count >= max_signals_per_year:
                            print(f"{code} {name}: 跳过 - 今年已触发 {signal_count} 次信号（上限 {max_signals_per_year} 次）")
                            continue

                        # ===== 三层风控检查 =====
                        # 执行顺序：评分阈值 → 大盘趋势 → 单月上限
                        # 大盘趋势过滤已在扫描前预检查完成，这里主要检查评分阈值和单月上限
                        # 若 market_context 为 None（大盘过滤关闭或获取失败），构造默认上下文
                        if market_context is None:
                            effective_context = MarketContext(
                                current_date=signal_date,
                                index_close=0.0,
                                index_ma20=None,
                                index_ma60=None,
                                is_weak_market=False,
                                data_sufficient=False,
                            )
                        else:
                            effective_context = market_context

                        # R02：逐候选风控不再拦截月度额度，apply_all_controls 只执行前两层
                        # monthly_count 统一在 Top N 排序后通过 cap_by_monthly_quota 应用
                        risk_result = risk_controller.apply_all_controls(
                            signal, effective_context  # monthly_count 默认 None
                        )

                        if not risk_result.passed:
                            # 输出风控命中日志（统一格式，使用 logger 替代 print）
                            # V2.1 新增：根据过滤规则类型输出额外的上下文信息
                            v21_extra = ""
                            filter_rule = risk_result.filter_rule
                            if filter_rule == 'macd_filter' and effective_context.index_dif is not None:
                                v21_extra = (
                                    f" dif={effective_context.index_dif:.4f}"
                                    f" dea={effective_context.index_dea:.4f}"
                                )
                            elif filter_rule == 'slope_filter':
                                slope_period = risk_config.slope_ma_period
                                if slope_period == 20:
                                    slope_val = effective_context.ma20_slope
                                else:
                                    slope_val = effective_context.ma60_slope
                                if slope_val is not None:
                                    v21_extra = f" ma{slope_period}_slope={slope_val:.6f}"
                            elif filter_rule == 'score_adjustment':
                                v21_extra = f" strength={effective_context.market_strength}"

                            logger.info(
                                f"[风控过滤] code={code} name={name} "
                                f"rule={filter_rule} "
                                f"reason={risk_result.filter_reason} "
                                f"score={signal.score:.2f} "
                                f"signal_date={signal_date}"
                                f"{v21_extra}"
                            )
                            continue

                        support_level = signal.detail.get('support_level', 0)
                        stop_loss_ratio = params.get('stop_loss_ratio', 0.97)
                        stop_loss_price = support_level * stop_loss_ratio

                        entry = {
                            'code': code,
                            'name': name,
                            'support_level': round(support_level, 2),
                            'stop_loss_price': round(stop_loss_price, 2),
                            'retrace_date': retrace_date_str,
                            'surge_date': str(signal.detail.get('surge_date', ''))[:10],
                            'signal_date': signal_date,
                            'score': signal.score,
                            'surge_pct': signal.detail.get('surge_pct', 0),
                            'surge_volume_ratio': signal.detail.get('surge_volume_ratio', 0),
                            'drop_rate': signal.detail.get('drop_rate', 0),
                            'retrace_low': signal.detail.get('retrace_low', 0),
                            'retrace_close': signal.detail.get('retrace_close', 0),
                            'shrink_ratio': signal.detail.get('shrink_ratio', 0),
                            'drop_start_date': str(signal.detail.get('drop_start_date', ''))[:10],
                            'drop_end_date': str(signal.detail.get('drop_end_date', ''))[:10],
                            'trailing_stop_ratio': params.get('trailing_stop_ratio', 0.08),
                            'hard_stop_loss': params.get('hard_stop_loss', 0.10),
                            'stop_loss_ratio': params.get('stop_loss_ratio', 0.97),
                        }

                        signals.append(entry)
                        print(
                            f"{code}: 支撑位 {support_level:.2f}, "
                            f"止损 {stop_loss_price:.2f}, 评分 {signal.score:.2f}"
                        )
        except Exception as e:
            logger.exception(f"{code} {name} 处理异常：{e}")
            continue

    print()
    print("=" * 80)
    print(f"扫描完成：发现 {len(signals)} 个信号")
    print("=" * 80)

    # ===== Top N 稀缺性控制 + R02 月度额度统一分配 =====
    # 先按评分降序排序，确保后续按质量优先选入
    if len(signals) > 1:
        signals.sort(key=lambda x: x.get('score', 0), reverse=True)

    # R02：月度额度在逐候选风控中不再拦截（见 apply_all_controls 调用处），
    # 此处统一按「剩余额度」截断，保证已用 + 本批 ≤ max_signals_per_month
    year_month = signal_date[:7]
    monthly_count = monthly_counter.get_count(year_month)
    max_daily_signals = params.get('max_daily_signals', 0)
    selected = cap_by_monthly_quota(
        signals,
        used_count=monthly_count,
        max_per_month=risk_config.max_signals_per_month,
        max_daily=max_daily_signals,
    )

    # 输出截断日志（保留原 Top N 段的可读性输出）
    dropped_count = len(signals) - len(selected)
    if dropped_count > 0:
        dropped_scores = [
            f"{s['code']}({s.get('score', 0):.1f})"
            for s in signals[len(selected):len(selected) + 5]
        ]
        dropped_scores_str = "、".join(dropped_scores)
        if dropped_count > 5:
            dropped_scores_str += f" 等{dropped_count}只"

        # 区分日限还是月限导致的截断（或两者叠加）
        reasons = []
        if max_daily_signals > 0 and len(selected) > max_daily_signals:
            reasons.append(f"日限 {max_daily_signals}")
        remaining = risk_config.max_signals_per_month - monthly_count
        if remaining < len(signals) and len(selected) == remaining:
            reasons.append(f"月限剩余 {remaining}（已用 {monthly_count}/{risk_config.max_signals_per_month}）")
        reason_str = " + ".join(reasons) if reasons else "未知"

        logger.info(
            f"额度截断：入选 {len(selected)} 只，丢弃 {dropped_count} 只（{reason_str}）。"
            f"丢弃前几名：{dropped_scores_str}"
        )
        print(
            f"额度截断：入选 {len(selected)} 只，丢弃 {dropped_count} 只"
            f"（{reason_str}）"
        )

    signals = selected

    # 保存扫描结果到数据库（用于冷却期和年度限制检查）
    if signals:
        scan_results_data = [
            {
                'code': str(sig['code']),
                'name': str(sig['name']),
                'score': float(sig.get('score', 0)),
                'surge_date': str(sig.get('surge_date', '')) if sig.get('surge_date') else None,
                'support_level': float(sig.get('support_level', 0)),
                'current_close': float(sig.get('retrace_close', 0)),
                'drop_rate': float(sig.get('drop_rate', 0)),
                'min_vol_ratio': float(sig.get('surge_volume_ratio', 0)),
                'surge_price': 0.0,
                'surge_volume_ratio': float(sig.get('surge_volume_ratio', 0)),
                'surge_pct': float(sig.get('surge_pct', 0)),
                'low_after_surge': float(sig.get('retrace_low', 0)),
            }
            for sig in signals
        ]
        try:
            db.save_scan_result(signal_date, scan_results_data)
            print(f"已保存 {len(scan_results_data)} 条扫描结果到数据库")
        except Exception as e:
            logger.error(f"保存扫描结果到数据库失败：{e}")

    db.close()

    # 保存信号
    if signals:
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        output_file = Path(output_dir) / f'signals_{signal_date}.json'

        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(signals, f, ensure_ascii=False, indent=2)

        print(f"\n信号已保存：{output_file}")

    return signals


def main() -> None:
    """主函数"""
    print("=" * 80)
    print("股票形态每日扫描系统 V3")
    print("=" * 80)

    # R03：命令行可显式指定信号日期（早晨重扫场景用当天日期）
    import argparse
    parser = argparse.ArgumentParser(description='OBPC 每日形态扫描')
    parser.add_argument('--signal-date', type=str, default=None,
                        help='强制指定信号日期（格式 YYYY-MM-DD），不指定时自动推断最近交易日')
    args = parser.parse_args()

    try:
        config = load_config()
        print("\n配置加载完成")
    except Exception as e:
        print(f"\n配置加载失败：{e}")
        sys.exit(1)

    signals = scan_daily_signals(config, signal_date_override=args.signal_date)

    if not signals:
        print("\n今日无信号")
    else:
        print(f"\n共发现 {len(signals)} 个买入信号")

        print("\n信号列表:")
        print("-" * 80)
        for sig in signals:
            print(
                f"{sig['code']} - {sig['name']}: "
                f"支撑 {sig['support_level']}, 止损 {sig['stop_loss_price']}"
            )


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n用户中断扫描")
        sys.exit(1)
    except Exception as e:
        print(f"\n扫描异常：{e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)