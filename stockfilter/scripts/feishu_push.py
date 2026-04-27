#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
飞书推送脚本（T+1 日开盘前运行）

功能：
1. 读取昨日扫描的信号
2. 生成飞书卡片消息
3. 推送到飞书（优先使用通用通知服务，降级为直接调用webhook）
"""

import json
import requests
from pathlib import Path
from datetime import datetime
import sys
import os

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.notification_client import NotificationClient


class FeishuPusher:
    """飞书推送器（支持通用服务和直接调用两种模式）"""
    
    def __init__(self, webhook_url: str = None, use_common_service: bool = True):
        """
        初始化飞书推送器
        
        Args:
            webhook_url: 飞书 webhook URL（降级模式使用）
            use_common_service: 是否使用通用通知服务
        """
        self.webhook_url = webhook_url
        self.use_common_service = use_common_service
        
        if use_common_service:
            self.notification_client = NotificationClient()
    
    def send_card_message(self, signals: list) -> bool:
        """
        发送卡片消息
        
        Args:
            signals: 信号列表
        
        Returns:
            bool: 是否发送成功
        """
        # 构建卡片消息
        card = {
            "msg_type": "interactive",
            "card": {
                "config": {
                    "wide_screen_mode": True
                },
                "header": {
                    "template": "blue",
                    "title": {
                        "content": "📊 股票形态筛选信号",
                        "tag": "plain_text"
                    }
                },
                "elements": []
            }
        }
        
        # 添加信号卡片
        if signals:
            for sig in signals:
                element = {
                    "tag": "div",
                    "text": {
                        "content": f"**{sig['code']} - {sig['name']}**\n"
                                   f"支撑位：{sig['support_level']}\n"
                                   f"止损价：{sig['stop_loss_price']}\n"
                                   f"形态日期：{sig['pattern_date']}",
                        "tag": "lark_md"
                    }
                }
                card["card"]["elements"].append(element)
        else:
            # 无信号
            card["card"]["elements"].append({
                "tag": "div",
                "text": {
                    "content": "今日无符合形态的买入信号\n\n继续监控中...",
                    "tag": "plain_text"
                }
            })
        
        # 优先使用通用通知服务
        if self.use_common_service:
            return self._send_via_common_service(card, signals)
        else:
            return self._send_via_webhook(card)
    
    def _send_via_common_service(self, card: dict, signals: list) -> bool:
        """通过通用通知服务发送"""
        try:
            # 构建消息内容
            level = "warning" if signals else "info"
            
            # 提取卡片内容
            content_lines = []
            for element in card["card"]["elements"]:
                if "text" in element and "content" in element["text"]:
                    content_lines.append(element["text"]["content"])
            
            message = "\n\n".join(content_lines)
            
            # 发送通知
            return self.notification_client.send_markdown(
                title="📊 股票形态筛选信号",
                content=message,
                level=level
            )
            
        except Exception as e:
            print(f"⚠️  通用服务发送失败，降级为直接调用：{e}")
            return self._send_via_webhook(card)
    
    def _send_via_webhook(self, card: dict) -> bool:
        """直接调用飞书 webhook（降级模式）"""
        if not self.webhook_url:
            print("❌ 未配置 webhook URL")
            return False
        
        try:
            response = requests.post(
                self.webhook_url,
                json=card,
                timeout=10
            )
            
            if response.status_code == 200:
                result = response.json()
                if result.get('StatusCode') == 0:
                    print("✅ 飞书推送成功（直接调用）")
                    return True
                else:
                    print(f"❌ 飞书推送失败：{result}")
                    return False
            else:
                print(f"❌ HTTP 错误：{response.status_code}")
                return False
                
        except Exception as e:
            print(f"❌ 推送异常：{e}")
            return False
    
    def send_text_message(self, content: str) -> bool:
        """
        发送文本消息
        
        Args:
            content: 消息内容
        
        Returns:
            bool: 是否发送成功
        """
        # 优先使用通用通知服务
        if self.use_common_service:
            return self.notification_client.send_text(content)
        else:
            # 降级为直接调用
            if not self.webhook_url:
                print("❌ 未配置 webhook URL")
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
                    if result.get('StatusCode') == 0:
                        print("✅ 飞书推送成功（直接调用）")
                        return True
                    else:
                        print(f"❌ 飞书推送失败：{result}")
                        return False
                else:
                    print(f"❌ HTTP 错误：{response.status_code}")
                    return False
                    
            except Exception as e:
                print(f"❌ 推送异常：{e}")
                return False


def load_signals():
    """加载信号文件"""
    # 获取昨天的日期
    from datetime import datetime, timedelta
    yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
    
    # 信号文件路径
    signal_file = Path(f'signals/signals_{yesterday}.json')
    
    if not signal_file.exists():
        print(f"⚠️  信号文件不存在：{signal_file}")
        return []
    
    with open(signal_file, 'r', encoding='utf-8') as f:
        return json.load(f)


def main():
    """主函数"""
    print("=" * 80)
    print("飞书推送系统")
    print("=" * 80)
    
    # 配置（优先使用通用服务，降级为直接调用）
    FEISHU_WEBHOOK = "https://open.feishu.cn/open-apis/bot/v2/hook/955aced6-5b07-42a6-a714-4c5f4726b003"
    
    # 加载信号
    signals = load_signals()
    
    if not signals:
        print("\n⚠️  今日无买入信号")
        # 发送无信号通知
        pusher = FeishuPusher(webhook_url=FEISHU_WEBHOOK, use_common_service=True)
        today_beijing = datetime.now().strftime('%Y-%m-%d')
        pusher.send_text_message(f"📊 {today_beijing} 股票形态扫描\n\n今日无符合形态的买入信号\n\n继续监控中...")
        return
    
    print(f"\n📊 发现 {len(signals)} 个买入信号")
    print()
    
    # 显示信号列表
    for idx, sig in enumerate(signals, 1):
        print(f"{idx}. {sig['code']} - {sig['name']}: 支撑 {sig['support_level']}, 止损 {sig['stop_loss_price']}")
    
    print()
    
    # 检查是否为交互模式（有 stdin 输入）
    auto_send = True  # 默认自动发送（用于定时任务）
    if sys.stdin.isatty():
        # 交互模式，询问用户
        confirm = input("是否发送飞书推送？(y/n): ")
        auto_send = confirm.lower() == 'y'
    
    if not auto_send:
        print("❌ 取消推送")
        return
    
    # 创建推送器并发送（优先使用通用服务）
    pusher = FeishuPusher(webhook_url=FEISHU_WEBHOOK, use_common_service=True)
    
    print("\n正在发送飞书推送...")
    success = pusher.send_card_message(signals)
    
    if success:
        print("\n✅ 推送完成！")
        print("\n📋 操作提醒:")
        print("1. 请在 9:15-9:25 查看飞书消息")
        print("2. 观察开盘价，若高开>5% 或涨停请放弃")
        print("3. 买入后立即设置条件单（止损 + 移动止盈）")
    else:
        print("\n❌ 推送失败，请检查配置")


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n⚠️  用户中断推送")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ 推送异常：{e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
