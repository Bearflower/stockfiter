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
        """
        card = {
            "msg_type": "interactive",
            "card": {
                "config": {"wide_screen_mode": True},
                "header": {
                    "template": "blue",
                    "title": {
                        "content": "📈 新筛选股票提醒",
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

            summary_element = {
                "tag": "div",
                "text": {
                    "content": (
                        f"共筛选出 {len(signals)} 只股票，建议开盘后择机买入（高开>5%请放弃）\n"
                        f"⚠️ 风险提示：支撑位×{stop_loss_ratio}为止损价，"
                        f"移动止盈回撤{trailing_stop:.0%}卖出。以上信息仅供参考，不构成投资建议"
                    ),
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

                trailing_stop = sig.get("trailing_stop_ratio", 0.08)
                hard_stop = sig.get("hard_stop_loss", 0.10)

                element = {
                    "tag": "div",
                    "text": {
                        "content": (
                            f"**{sig['name']}** ({sig['code']})\n"
                            f"📊 形态评分：{sig.get('score', 0):.2f}\n"
                            f"📈 关键指标：\n"
                            f"• 信号日期：{sig.get('signal_date', '')}\n"
                            f"• 放量日期：{sig.get('surge_date', '')}\n"
                            f"• 放量涨幅：{surge_pct_str}\n"
                            f"• 放量倍数：{surge_volume_ratio_str}倍\n"
                            f"• 支撑位：{sig['support_level']}元\n"
                            f"• 止损价：{sig['stop_loss_price']}元\n"
                            f"• 回踩低点：{sig.get('retrace_low', 0)}元\n"
                            f"• 大跌幅度：{sig.get('drop_rate', 0):.1%}\n"
                            f"💡 交易建议：\n"
                            f"• 建议买入价：今日开盘价\n"
                            f"• 移动止盈：从持仓最高价回撤{trailing_stop:.0%}卖出\n"
                            f"• 硬止损：-{hard_stop:.0%}"
                        ),
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
      - blocked_market → "发现 N 个原始形态候选，被市场风控拦截"
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
        print(f"\n发现 {len(signals)} 个买入信号（走卡片）")
        for idx, sig in enumerate(signals, 1):
            print(
                f"{idx}. {sig['code']} - {sig['name']}: "
                f"支撑 {sig['support_level']}, 止损 {sig['stop_loss_price']}"
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
        print("\n推送失败，请检查配置")


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
