#!/usr/bin/env python3
"""
飞书推送脚本（T+1 日开盘前运行）

功能：
1. 读取昨日扫描的信号
2. 生成飞书卡片消息
3. 直连飞书 webhook 推送
"""

import json
import os
import sys
from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Optional

import requests


class FeishuPusher:
    """飞书推送器（直连飞书 webhook）"""

    def __init__(self, webhook_url: Optional[str] = None):
        """
        初始化飞书推送器

        Args:
            webhook_url: 飞书 webhook URL
        """
        self.webhook_url = webhook_url or os.getenv('FEISHU_WEBHOOK')

    def send_card_message(self, signals: List) -> bool:
        """
        发送卡片消息

        Args:
            signals: 信号列表

        Returns:
            bool: 是否发送成功
        """
        card = {
            "msg_type": "interactive",
            "card": {
                "config": {"wide_screen_mode": True},
                "header": {
                    "template": "blue",
                    "title": {
                        "content": "📈 新筛选股票提醒",
                        "tag": "plain_text"
                    }
                },
                "elements": []
            }
        }

        if signals:
            # 从首个信号读取交易参数（所有信号共用同一套参数）
            first_sig = signals[0]
            stop_loss_ratio = first_sig.get('stop_loss_ratio', 0.97)
            trailing_stop = first_sig.get('trailing_stop_ratio', 0.08)

            # 汇总信息
            summary_element = {
                "tag": "div",
                "text": {
                    "content": (
                        f"共筛选出 {len(signals)} 只股票，建议开盘后择机买入（高开>5%请放弃）\n"
                        f"⚠️ 风险提示：支撑位×{stop_loss_ratio}为止损价，移动止盈回撤{trailing_stop:.0%}卖出。以上信息仅供参考，不构成投资建议"
                    ),
                    "tag": "lark_md"
                }
            }
            card["card"]["elements"].append(summary_element)

            # 每只股票一个 div
            for sig in signals:
                surge_pct = sig.get('surge_pct', 0)
                surge_volume_ratio = sig.get('surge_volume_ratio', 0)
                # 格式化百分比
                if isinstance(surge_pct, (int, float)):
                    surge_pct_str = f"{surge_pct:.2%}"
                else:
                    surge_pct_str = str(surge_pct)
                if isinstance(surge_volume_ratio, (int, float)):
                    surge_volume_ratio_str = f"{surge_volume_ratio:.1f}"
                else:
                    surge_volume_ratio_str = str(surge_volume_ratio)

                # 从信号数据读取止盈止损参数，避免硬编码
                trailing_stop = sig.get('trailing_stop_ratio', 0.08)
                hard_stop = sig.get('hard_stop_loss', 0.10)

                element = {
                    "tag": "div",
                    "text": {
                        "content": (
                            f"**{sig['name']}** ({sig['code']})\n"
                            f"\n"
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
                            f"\n"
                            f"💡 交易建议：\n"
                            f"• 建议买入价：今日开盘价\n"
                            f"• 移动止盈：从持仓最高价回撤{trailing_stop:.0%}卖出\n"
                            f"• 硬止损：-{hard_stop:.0%}"
                        ),
                        "tag": "lark_md"
                    }
                }
                card["card"]["elements"].append(element)

            # 末尾分隔线和风险提示
            card["card"]["elements"].append({"tag": "hr"})
            card["card"]["elements"].append({
                "tag": "note",
                "elements": [{
                    "tag": "plain_text",
                    "content": "风险提示：以上信息仅供参考，不构成投资建议"
                }]
            })
        else:
            card["card"]["elements"].append({
                "tag": "div",
                "text": {
                    "content": "今日无符合形态的买入信号\n\n继续监控中...",
                    "tag": "plain_text"
                }
            })

        # 直连飞书 webhook 发送卡片
        return self._send_via_webhook(card)

    def _send_via_webhook(self, card: dict) -> bool:
        """直连飞书 webhook 发送"""
        if not self.webhook_url:
            print("未配置 webhook URL")
            return False

        try:
            response = requests.post(
                self.webhook_url,
                json=card,
                timeout=10
            )

            if response.status_code == 200:
                result = response.json()
                if result.get('StatusCode') == 0 or result.get('code') == 0:
                    print("飞书推送成功（直接调用）")
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
        """发送文本消息，直连飞书 webhook"""
        if not self.webhook_url:
            print("未配置 webhook URL")
            return False

        payload = {
            "msg_type": "text",
            "content": {"text": content}
        }

        try:
            response = requests.post(
                self.webhook_url,
                json=payload,
                timeout=10
            )

            if response.status_code == 200:
                result = response.json()
                if (
                    result.get('StatusCode') == 0
                    or result.get('code') == 0
                ):
                    print("飞书文本推送成功（直接调用）")
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


def load_signals() -> List:
    """加载信号文件"""
    yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')

    signal_file = Path(f'signals/signals_{yesterday}.json')

    if not signal_file.exists():
        print(f"信号文件不存在：{signal_file}")
        return []

    with open(signal_file, 'r', encoding='utf-8') as f:
        return json.load(f)


def main() -> None:
    """主函数"""
    print("=" * 80)
    print("飞书推送系统 V3")
    print("=" * 80)

    FEISHU_WEBHOOK = os.getenv('FEISHU_WEBHOOK', '')
    if not FEISHU_WEBHOOK:
        print("错误：未配置 FEISHU_WEBHOOK 环境变量")
        sys.exit(1)

    signals = load_signals()

    if not signals:
        print("\n今日无买入信号")
        pusher = FeishuPusher(webhook_url=FEISHU_WEBHOOK)
        today_beijing = datetime.now().strftime('%Y-%m-%d')
        pusher.send_text_message(
            f"股票形态扫描 {today_beijing}\n\n"
            "今日无符合形态的买入信号\n\n继续监控中..."
        )
        return

    print(f"\n发现 {len(signals)} 个买入信号")
    print()

    for idx, sig in enumerate(signals, 1):
        print(
            f"{idx}. {sig['code']} - {sig['name']}: "
            f"支撑 {sig['support_level']}, 止损 {sig['stop_loss_price']}"
        )

    print()

    auto_send = True
    if sys.stdin.isatty():
        confirm = input("是否发送飞书推送？(y/n): ")
        auto_send = confirm.lower() == 'y'

    if not auto_send:
        print("取消推送")
        return

    pusher = FeishuPusher(webhook_url=FEISHU_WEBHOOK)

    print("\n正在发送飞书推送...")
    success = pusher.send_card_message(signals)

    if success:
        print("\n推送完成！")
        print("\n操作提醒:")
        print("1. 请在 9:15-9:25 查看飞书消息")
        print("2. 观察开盘价，若高开>5% 或涨停请放弃")
        print("3. 买入后立即设置条件单（止损 + 移动止盈）")
    else:
        print("\n推送失败，请检查配置")


if __name__ == '__main__':
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