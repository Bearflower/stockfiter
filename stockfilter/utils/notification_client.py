#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
通用通知服务客户端
调用 common_service/notification_service 统一发送飞书通知
"""

import requests
from typing import Optional


class NotificationClient:
    """通用通知服务客户端"""
    
    def __init__(self, base_url: str = "http://43.156.242.184:8766/api/v1"):
        """
        初始化通知客户端
        
        Args:
            base_url: 通知服务地址
        """
        self.base_url = base_url
        self.project = "stockfilter"  # 项目标识
    
    def send_text(self, message: str, level: str = "info") -> bool:
        """
        发送文本消息
        
        Args:
            message: 消息内容
            level: 通知级别 (info, warning, error)
        
        Returns:
            bool: 是否发送成功
        """
        try:
            response = requests.post(
                f"{self.base_url}/send",
                json={
                    "project": self.project,
                    "message": message,
                    "type": "text",
                    "level": level
                },
                timeout=10
            )
            
            if response.status_code == 200:
                result = response.json()
                if result.get('code') == 0:
                    print(f"✅ 通知发送成功：{result['data'].get('msg_id', 'N/A')}")
                    return True
                else:
                    print(f"❌ 通知发送失败：{result.get('message', 'Unknown error')}")
                    return False
            else:
                print(f"❌ HTTP 错误：{response.status_code}")
                return False
                
        except Exception as e:
            print(f"❌ 通知异常：{e}")
            return False
    
    def send_markdown(self, title: str, content: str, level: str = "info") -> bool:
        """
        发送 Markdown 消息
        
        Args:
            title: 消息标题
            content: Markdown 内容
            level: 通知级别 (info, warning, error)
        
        Returns:
            bool: 是否发送成功
        """
        try:
            response = requests.post(
                f"{self.base_url}/send",
                json={
                    "project": self.project,
                    "message": f"{title}\n\n{content}",
                    "type": "markdown",
                    "level": level
                },
                timeout=10
            )
            
            if response.status_code == 200:
                result = response.json()
                if result.get('code') == 0:
                    print(f"✅ 通知发送成功：{result['data'].get('msg_id', 'N/A')}")
                    return True
                else:
                    print(f"❌ 通知发送失败：{result.get('message', 'Unknown error')}")
                    return False
            else:
                print(f"❌ HTTP 错误：{response.status_code}")
                return False
                
        except Exception as e:
            print(f"❌ 通知异常：{e}")
            return False
