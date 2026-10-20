#!/usr/bin/env python3
"""
飞书推送脚本（T+1 日开盘前运行）

2026-10-18 改造：
  - 按 scan_runs 六态分流，不再靠信号文件存在性推断"零信号"
  - build_scan_summary_message 六态全覆盖永不抛异常
  - main() 从 DB 读取 scan_run，completed_with_signals 走卡片，其余走文本
  - send_card_message 的空列表 else 分支文案不再表述为"今日无符合形态"
"""

import json
import os
import sys
from pathlib import Path
from typing import List, Optional

import requests


class FeishuPusher:
    """飞书推送器（直连飞书 webhook）"""

    def __init__(self, webhook_url: Optional[str] = None):
        self.webhook_url = webhook_url or os.getenv("FEISHU_WEBHOOK")

    def send_card_message(self, signals: List) -> bool:
        """
        发送卡片消息。空 signals 时也发送（不含"今日无符合形态"措辞）。

        V3 分级市场风控：根据 signal 的 market_regime 字段分流展示：
          - strong：正标题，标注"强市正常放行"
          - weak：正标题，标注"弱市分级放行"，展示建议仓位和相对强度
          - extreme_weak：防御性处理——标题改为"观察名单"，正文明确说明风险
          - hard_filter / 缺省：保持传统展示
        """
        # ===== 分级裁决字段提取 =====
        # 取第一个 signal 的 market_regime 来决定卡片整体风格
        first_regime = None
        if signals:
            first_regime = signals[0].get("market_regime", "hard_filter")

        # extreme_weak 防御：正常 daily_scan 不会把 extreme_weak 的信号放进来
        # （allow_entry=False），但如果进来了，我们必须把它标为"观察名单"
        # 防止误发送"建议买入"措辞导致用户操作
        is_observation_list = (
            first_regime == "extreme_weak"
        )

        # 根据 regime 决定卡片 header 颜色和标题
        if is_observation_list:
            header_template = "orange"
            header_title = "📋 观察名单"
        elif first_regime == "strong":
            header_template = "green"
            header_title = "📈 新筛选股票提醒（强市正常放行）"
        elif first_regime == "weak":
            header_template = "yellow"
            header_title = "📈 新筛选股票提醒（弱市分级放行）"
        else:
            # hard_filter / unknown / 缺省 → 传统展示
            header_template = "blue"
            header_title = "📈 新筛选股票提醒"

        card = {
            "msg_type": "interactive",
            "card": {
                "config": {"wide_screen_mode": True},
                "header": {
                    "template": header_template,
                    "title": {
                        "content": header_title,
                        "tag": "plain_text",
                    },
                },
                "elements": [],
            },
        }

        if signals:
            first_sig = signals[0]
            stop_loss_ratio = first_sig.get("stop_loss_ratio", 0.97)
            trailing_stop = first_sig.get("trailing_stop_ratio", 0.08)

            # ===== summary 段：按 regime 生成不同措辞 =====
            if is_observation_list:
                # 极弱市场 → 观察名单，禁止"建议买入"措辞
                summary_content = (
                    f"当前市场环境极弱（MA60 向下 + 指数跌破 MA60），禁止开仓。\n"
                    f"共发现 {len(signals)} 个原始形态候选，以下仅供观察参考，**不构成买入建议**。\n"
                    f"⚠️ 风险提示：极弱市场应保持空仓或极小仓位观望。"
                )
            elif first_regime == "weak":
                # 弱市分级放行 → 展示仓位建议
                summary_content = (
                    f"弱市分级放行：共 {len(signals)} 只股票通过相对强度筛选。\n"
                    f"⚠️ 弱市操作建议：建议仓位见个股卡片内标注，严格控制风险。\n"
                    f"支撑位×{stop_loss_ratio}为止损价，移动止盈回撤{trailing_stop:.0%}卖出。以上信息仅供参考，不构成投资建议"
                )
            else:
                # strong / hard_filter / 其他 → 传统展示
                summary_content = (
                    f"共筛选出 {len(signals)} 只股票，建议开盘后择机买入（高开>5%请放弃）\n"
                    f"⚠️ 风险提示：支撑位×{stop_loss_ratio}为止损价，"
                    f"移动止盈回撤{trailing_stop:.0%}卖出。以上信息仅供参考，不构成投资建议"
                )

            summary_element = {
                "tag": "div",
                "text": {
                    "content": summary_content,
                    "tag": "lark_md",
                },
            }
            card["card"]["elements"].append(summary_element)

            for sig in signals:
                surge_pct = sig.get("surge_pct", 0)
                surge_volume_ratio = sig.get("surge_volume_ratio", 0)
                if isinstance(surge_pct, (int, float)):
                    surge_pct_str = f"{surge_pct:.2%}"
                else:
                    surge_pct_str = str(surge_pct)
                if isinstance(surge_volume_ratio, (int, float)):
                    surge_volume_ratio_str = f"{surge_volume_ratio:.1f}"
                else:
                    surge_volume_ratio_str = str(surge_volume_ratio)

                trailing_stop_sig = sig.get("trailing_stop_ratio", 0.08)
                hard_stop = sig.get("hard_stop_loss", 0.10)

                # ===== V3 分级字段展示 =====
                regime = sig.get("market_regime", "hard_filter")
                graded_lines: List[str] = []
                show_trading_advice = True

                # 极弱市场 → 观察名单，不展示交易建议
                if regime == "extreme_weak":
                    show_trading_advice = False
                    graded_lines.append("🔴 极弱市场 · 仅供观察")
                elif regime == "weak":
                    # 弱市：展示建议仓位和相对强度
                    rec_pos = sig.get("recommended_position_pct")
                    if rec_pos is not None:
                        graded_lines.append(f"• 建议仓位：{rec_pos:.2%}")
                    rs = sig.get("relative_strength")
                    if rs is not None:
                        rs_sign = "+" if rs >= 0 else ""
                        graded_lines.append(f"• 相对强度：{rs_sign}{rs:.2%}")
                    graded_lines.append("• 弱市分级放行")
                elif regime == "strong":
                    graded_lines.append("• 强市正常放行")
                # hard_filter / 其他 → 不展示分级字段

                # 组装正文内容
                content_lines = [
                    f"**{sig['name']}** ({sig['code']})",
                    f"📊 形态评分：{sig.get('score', 0):.2f}",
                    "📈 关键指标：",
                    f"• 信号日期：{sig.get('signal_date', '')}",
                    f"• 放量日期：{sig.get('surge_date', '')}",
                    f"• 放量涨幅：{surge_pct_str}",
                    f"• 放量倍数：{surge_volume_ratio_str}倍",
                    f"• 支撑位：{sig['support_level']}元",
                    f"• 止损价：{sig['stop_loss_price']}元",
                    f"• 回踩低点：{sig.get('retrace_low', 0)}元",
                    f"• 大跌幅度：{sig.get('drop_rate', 0):.1%}",
                ]

                # 追加分级字段行
                if graded_lines:
                    content_lines.extend(graded_lines)

                # 交易建议段（极弱市场不展示）
                if show_trading_advice:
                    content_lines.append("💡 交易建议：")
                    content_lines.append("• 建议买入价：今日开盘价")
                    content_lines.append(
                        f"• 移动止盈：从持仓最高价回撤{trailing_stop_sig:.0%}卖出"
                    )
                    content_lines.append(f"• 硬止损：-{hard_stop:.0%}")

                element = {
                    "tag": "div",
                    "text": {
                        "content": "\n".join(content_lines),
                        "tag": "lark_md",
                    },
                }
                card["card"]["elements"].append(element)

            card["card"]["elements"].append({"tag": "hr"})
            card["card"]["elements"].append(
                {
                    "tag": "note",
                    "elements": [
                        {
                            "tag": "plain_text",
                            "content": "风险提示：以上信息仅供参考，不构成投资建议",
                        }
                    ],
                }
            )
        else:
            # 空 signals（可能因 DB 查询不到 run 记录）：不再表述为"今日无符合形态"
            card["card"]["elements"].append(
                {
                    "tag": "div",
                    "text": {
                        "content": "本次推送无可展示的信号，请检查运行记录。",
                        "tag": "plain_text",
                    },
                }
            )

        return self._send_via_webhook(card)

    def _send_via_webhook(self, card: dict) -> bool:
        """直连飞书 webhook 发送"""
        if not self.webhook_url:
            print("未配置 webhook URL")
            return False

        try:
            response = requests.post(self.webhook_url, json=card, timeout=10)
            if response.status_code == 200:
                result = response.json()
                if result.get("StatusCode") == 0 or result.get("code") == 0:
                    print("飞书推送成功")
                    return True
                else:
                    print(f"飞书推送失败：{result}")
                    return False
            else:
                print(f"HTTP 错误：{response.status_code}")
                return False
        except Exception as e:
            print(f"推送异常：{e}")
            return False

    def send_text_message(self, content: str) -> bool:
        """发送文本消息（直连飞书 webhook）"""
        if not self.webhook_url:
            print("未配置 webhook URL")
            return False

        payload = {"msg_type": "text", "content": {"text": content}}

        try:
            response = requests.post(
                self.webhook_url, json=payload, timeout=10
            )
            if response.status_code == 200:
                result = response.json()
                if (
                    result.get("StatusCode") == 0
                    or result.get("code") == 0
                ):
                    print("飞书文本推送成功")
                    return True
                else:
                    print(f"飞书推送失败：{result}")
                    return False
            else:
                print(f"HTTP 错误：{response.status_code}")
                return False
        except Exception as e:
            print(f"推送异常：{e}")
            return False


# =====================================================================
# 状态到通知文案的映射（六态全覆盖，永不抛异常）
# =====================================================================


def build_scan_summary_message(
    signal_date: str,
    run: Optional[dict],
) -> Optional[str]:
    """
    根据 scan_runs 记录构造推送文案。

    六态全覆盖：
      - completed_with_signals → 返回 None（走信号卡片流程）
      - completed_zero → "完整扫描后未发现形态"
      - blocked_market → 按 filter_reason 分流："发现 N 个原始形态候选，被市场风控拦截"
        （市场层面），或"均未通过个股级筛选，并非市场层面的风控拦截"（个股层面）
      - blocked_data → "数据不完整，本次扫描已阻断"
      - failed → "扫描任务失败，请检查日志"
      - running → 异常语义文本，不抛异常
      - run is None → 扫描结果缺失，不抛异常
      - 未知 status → 异常语义文本，不抛异常

    Args:
        signal_date: 目标交易日（YYYY-MM-DD）
        run: scan_runs 行字典或 None

    Returns:
        Optional[str]: 返回 None 表示走信号卡片流程；返回文本表示走文本推送
    """
    header = f"股票形态扫描 {signal_date}\n\n"

    try:
        if run is None:
            return header + "扫描结果缺失，请检查调度与数据任务。"

        status = run.get("status")
        if status is None:
            return header + "扫描结果状态异常（无 status 字段），请检查日志。"

        if status == "completed_with_signals":
            # 有信号 → 让主流程走信号卡片
            return None

        if status == "completed_zero":
            return header + "完整扫描后未发现形态。"

        if status == "blocked_market":
            raw_count = run.get("raw_candidate_count", 0)
            reason = run.get("filter_reason") or "未记录原因"
            # 个股层面（评分/冷却期/年度额度/每日 TopN 截断）筛掉全部候选时，
            # filter_reason 用 RISK_CONTROLS_FILTERED_ALL 标识，不能推成"市场风控拦截"。
            # 延迟 import 该常量避免定义漂移；导入失败时回退为同值字面量。
            try:
                from scripts.daily_scan import (
                    RISK_CONTROLS_FILTERED_ALL as _all_filtered_reason,
                )
            except Exception:
                _all_filtered_reason = "risk_controls_filtered_all_candidates"
            if reason == _all_filtered_reason:
                return (
                    header
                    + f"发现 {raw_count} 个原始形态候选，"
                    + "均未通过个股级筛选（评分/冷却期/年度额度/每日截断），"
                    + "并非市场层面的风控拦截。"
                )
            # 市场层面拦截（来自 risk_controller 的市场硬过滤）
            return (
                header
                + f"发现 {raw_count} 个原始形态候选，但被市场风控拦截：{reason}。"
            )

        if status == "blocked_data":
            return header + "数据不完整，本次扫描已阻断。"

        if status == "failed":
            reason = run.get("filter_reason")
            suffix = f"（{reason}）" if reason else ""
            return header + f"扫描任务失败，请检查日志{suffix}。"

        if status == "running":
            return header + "扫描尚未结束或未收敛，请检查调度。"

        # 未知状态（防御式）
        return header + f"扫描状态异常（{status}），请检查日志。"

    except Exception as e:
        # 彻底防御：永不抛异常
        return header + f"构造推送文案时发生异常（{e}），请检查日志。"


# =====================================================================
# 主入口
# =====================================================================


def load_signals(signal_date: Optional[str] = None) -> List:
    """
    加载信号文件（主流程不再依赖它推断状态，保留供 completed_with_signals 后
    的卡片渲染使用）。
    """
    from datetime import datetime, timedelta

    target_date = signal_date or (datetime.now() - timedelta(days=1)).strftime(
        "%Y-%m-%d"
    )
    signal_file = Path(f"signals/signals_{target_date}.json")
    if not signal_file.exists():
        return []
    with open(signal_file, "r", encoding="utf-8") as f:
        return json.load(f)


def main() -> None:
    """主函数"""
    import argparse

    parser = argparse.ArgumentParser(description="飞书推送脚本")
    parser.add_argument(
        "--signal-date",
        type=str,
        default=None,
        help="指定信号日期（YYYY-MM-DD），不指定时用昨天",
    )
    args = parser.parse_args()

    print("=" * 80)
    print("飞书推送系统 V4")
    print("=" * 80)

    feishu_webhook = os.getenv("FEISHU_WEBHOOK", "")
    if not feishu_webhook:
        print("错误：未配置 FEISHU_WEBHOOK 环境变量")
        sys.exit(1)

    # 确定信号日期
    signal_date = args.signal_date
    if not signal_date:
        from datetime import datetime, timedelta
        signal_date = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

    print(f"推送日期：{signal_date}")

    # 从 DB 读取该 signal_date 的 scan_run（不指定 run_kind，取最近一次）
    run = None
    try:
        from data.database import DatabaseManager

        db = DatabaseManager()
        try:
            run = db.get_scan_run(signal_date, run_kind=None)
        finally:
            db.close()
    except Exception as e:
        print(f"警告：读取 scan_runs 失败：{e}")

    # 构造推送文案
    summary_text = build_scan_summary_message(signal_date, run)
    print(f"scan_run status={run.get('status') if run else None} → summary_text={'None (走卡片)' if summary_text is None else '文本推送'}")

    pusher = FeishuPusher(webhook_url=feishu_webhook)

    auto_send = True
    if sys.stdin.isatty():
        confirm = input("是否发送飞书推送？(y/n): ")
        auto_send = confirm.lower() == "y"

    if not auto_send:
        print("取消推送")
        return

    if summary_text is None:
        # completed_with_signals → 走信号卡片
        signals = load_signals(signal_date=signal_date)
        if not signals:
            # 扫描状态说有信号，但信号文件缺失或为空：属于异常，不能当作推送成功
            # （否则调度器会误写 push_history，导致该信号日永不重试）
            print(
                f"\n错误：扫描状态为 completed_with_signals（有信号），"
                f"但信号文件缺失/为空（signals/signals_{signal_date}.json）。"
                f"扫描结果与信号文件不一致，本次推送标记为失败以便下次重试。"
            )
            sys.exit(1)
        # 打印分级裁决信息（从第一个 signal 的 market_regime 判断）
        _first_regime = signals[0].get("market_regime", "hard_filter") if signals else "hard_filter"
        _regime_label = {
            "strong": "强市正常放行",
            "weak": "弱市分级放行",
            "extreme_weak": "极弱观察名单",
            "hard_filter": "hard_filter 模式",
        }.get(_first_regime, _first_regime)

        if _first_regime == "extreme_weak":
            print(f"\n当前市场极弱 → 观察名单（禁止开仓）：{len(signals)} 个候选（走卡片）")
        else:
            print(f"\n发现 {len(signals)} 个买入信号（{_regime_label}，走卡片）")

        for idx, sig in enumerate(signals, 1):
            _regime = sig.get("market_regime", "hard_filter")
            _pos = sig.get("recommended_position_pct")
            _pos_str = f", 建议仓位 {_pos:.2%}" if _pos is not None and _regime in ("weak", "strong") else ""
            print(
                f"{idx}. {sig['code']} - {sig['name']}: "
                f"支撑 {sig['support_level']}, 止损 {sig['stop_loss_price']}"
                f"{_pos_str}"
            )

        print("\n正在发送飞书推送...")
        success = pusher.send_card_message(signals)
    else:
        # 非 completed_with_signals → 走文本
        print(f"\n发送文本消息：\n{summary_text}")
        success = pusher.send_text_message(summary_text)

    if success:
        print("\n推送完成！")
    else:
        # 推送失败必须以非零退出码结束：调度器用 check=True 判定成功，
        # 退出码 0 会被误判为已推送并写入 push_history，导致永不重试
        print("\n推送失败，请检查配置")
        sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n用户中断推送")
        sys.exit(1)
    except Exception as e:
        print(f"\n推送异常：{e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
