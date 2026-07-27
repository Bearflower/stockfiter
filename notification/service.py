"""
统一通知服务
直连飞书 webhook 推送
"""

import os
from typing import Dict, Any, Optional, List
from datetime import datetime

import requests

from utils.logger import get_logger
from strategy.base import Signal

logger = get_logger()


class FeishuNotifier:
    """本地飞书通知器"""

    # 评分颜色阈值（可通过子类覆写或配置传入）
    SCORE_HIGH_THRESHOLD = 80
    SCORE_MEDIUM_THRESHOLD = 70

    def __init__(self, webhook_url: Optional[str] = None):
        """
        初始化飞书通知器

        Args:
            webhook_url: 飞书 webhook URL
        """
        self.webhook_url = webhook_url or os.getenv('FEISHU_WEBHOOK')

        if not self.webhook_url:
            logger.warning("飞书 webhook URL 未配置，推送功能将不生效")

    def send_card_message(self, stock_info: Dict[str, Any]) -> bool:
        """
        发送股票筛选结果卡片消息

        Args:
            stock_info: 股票信息字典

        Returns:
            bool: 是否发送成功
        """
        if not self.webhook_url:
            return False

        try:
            message = self._build_card(stock_info)

            response = requests.post(
                self.webhook_url,
                json=message,
                headers={'Content-Type': 'application/json'},
                timeout=10
            )

            if response.status_code == 200:
                result = response.json()
                if result.get('StatusCode') == 0 or result.get('code') == 0:
                    logger.info(
                        f"飞书推送成功：{stock_info.get('code')} "
                        f"{stock_info.get('name', '')}"
                    )
                    return True
                else:
                    logger.error(f"飞书推送失败：{result}")
                    return False
            else:
                logger.error(f"飞书推送 HTTP 错误：{response.status_code}")
                return False

        except Exception as e:
            logger.error(f"飞书推送异常：{e}")
            return False

    def _build_card(self, stock_info: Dict[str, Any]) -> Dict:
        """构建飞书卡片消息"""
        surge_date = stock_info.get('surge_date', '')
        if isinstance(surge_date, datetime):
            surge_date = surge_date.strftime('%Y-%m-%d')

        score = stock_info.get('score', 0)
        score_color = (
            'red' if score >= self.SCORE_HIGH_THRESHOLD
            else 'orange' if score >= self.SCORE_MEDIUM_THRESHOLD
            else 'blue'
        )

        support_level = stock_info.get('support_level', 0)
        current_close = stock_info.get('current_close', 0)
        stop_loss_ratio = stock_info.get('stop_loss_ratio', 0.97)
        stop_loss_price = support_level * stop_loss_ratio

        content_lines = [
            f"**{stock_info.get('name', '')}** ({stock_info.get('code', '')})",
            "",
            f"形态评分：<font color=\"{score_color}\">{score:.2f}</font>",
            "",
            "关键指标：",
            f"放量日期：{surge_date}",
            f"当前价：{current_close:.2f} 元",
            f"支撑位：{support_level:.2f} 元",
            f"放量涨幅：{stock_info.get('surge_pct', 0):.2%}",
            f"放量倍数：{stock_info.get('surge_volume_ratio', 0):.2f} 倍",
            "",
            "交易建议：",
            f"建议买入价：{current_close:.2f} 元（次日开盘）",
            f"止损价：{stop_loss_price:.2f} 元",
            f"回踩低点：{stock_info.get('low_after_surge', 0):.2f} 元",
        ]
        content = '\n'.join(content_lines)

        message = {
            "msg_type": "interactive",
            "card": {
                "config": {"wide_screen_mode": True},
                "header": {
                    "template": "blue",
                    "title": {
                        "tag": "plain_text",
                        "content": "新筛选股票提醒"
                    }
                },
                "elements": [
                    {
                        "tag": "div",
                        "text": {"tag": "lark_md", "content": content}
                    },
                    {"tag": "hr"},
                    {
                        "tag": "note",
                        "elements": [
                            {
                                "tag": "plain_text",
                                "content": "风险提示：以上信息仅供参考，不构成投资建议"
                            }
                        ]
                    }
                ]
            }
        }

        return message

    def send_text_message(self, content: str) -> bool:
        """发送文本消息"""
        if not self.webhook_url:
            return False

        try:
            payload = {
                "msg_type": "text",
                "content": {"text": content}
            }

            response = requests.post(
                self.webhook_url,
                json=payload,
                timeout=10
            )

            if response.status_code == 200:
                result = response.json()
                if result.get('StatusCode') == 0 or result.get('code') == 0:
                    logger.info("飞书文本消息推送成功")
                    return True
                else:
                    logger.error(f"飞书文本推送失败：{result}")
                    return False
            else:
                logger.error(f"飞书文本推送 HTTP 错误：{response.status_code}")
                return False

        except Exception as e:
            logger.error(f"飞书文本推送异常：{e}")
            return False


class NotificationService:
    """
    统一通知服务
    直连飞书 webhook 推送
    """

    def __init__(
        self,
        config: Optional[Dict] = None,
        feishu_webhook: Optional[str] = None,
    ):
        """
        初始化统一通知服务

        Args:
            config: 完整配置字典（优先使用其中的 notification 节）
            feishu_webhook: 飞书 webhook URL
        """
        if config:
            noti_config = config.get('global', {}).get('notification', {})
            feishu_webhook = noti_config.get(
                'feishu_webhook', feishu_webhook
            )

        self.local_feishu = FeishuNotifier(webhook_url=feishu_webhook)

        logger.info("通知服务初始化完成（直连飞书模式）")

    def send_signal(self, signal: Signal) -> bool:
        """
        发送策略信号通知

        Args:
            signal: 策略信号

        Returns:
            bool: 是否发送成功
        """
        return self.local_feishu.send_card_message(signal.detail)

    def send_text(self, message: str, level: str = "info") -> bool:
        """
        发送文本消息

        Args:
            message: 消息内容
            level: 通知级别

        Returns:
            bool: 是否发送成功
        """
        return self.local_feishu.send_text_message(message)

    def send_markdown(
        self, title: str, content: str, level: str = "info"
    ) -> bool:
        """
        发送 Markdown 消息

        Args:
            title: 消息标题
            content: Markdown 内容
            level: 通知级别

        Returns:
            bool: 是否发送成功
        """
        return self.local_feishu.send_text_message(f"{title}\n\n{content}")