"""
统一通知器
支持两种通知方式：直连 webhook 和调用通用通知服务
支持异步发送、频率限制、多种通知模板
"""

import asyncio
import logging
import os
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

import aiohttp

# 通用通知服务配置
NOTIFICATION_SERVICE_URL = os.getenv(
    'NOTIFICATION_SERVICE_URL',
    'http://43.156.242.184:8766/api/v1'
)
NOTIFICATION_PROJECT = 'grid'


class NotificationMode(Enum):
    """通知模式枚举"""
    WEBHOOK = "webhook"      # 直连飞书 webhook
    SERVICE = "service"      # 调用通用通知服务


class UnifiedNotifier:
    """统一通知器
    
    支持两种通知方式：
    1. 直连飞书 webhook：直接调用飞书机器人 webhook
    2. 通用通知服务：通过独立的推送服务中转
    
    特性：
    - 异步发送（基于 aiohttp）
    - 频率限制（冷却时间可配置）
    - 报警历史记录
    - 多种预置通知模板
    """
    
    def __init__(
        self,
        mode: NotificationMode = NotificationMode.SERVICE,
        webhook_url: Optional[str] = None,
        service_url: str = NOTIFICATION_SERVICE_URL,
        project: str = NOTIFICATION_PROJECT,
        enabled: bool = True,
        cooldown: int = 60
    ):
        """
        初始化统一通知器
        
        Args:
            mode: 通知模式（WEBHOOK 或 SERVICE）
            webhook_url: 飞书 webhook URL（直连模式需要）
            service_url: 通用通知服务 URL
            project: 项目标识
            enabled: 是否启用通知
            cooldown: 冷却时间（秒），防止报警轰炸
        """
        self.mode = mode
        self.webhook_url = webhook_url
        self.service_url = service_url.rstrip('/')
        self.project = project
        self.enabled = enabled
        self.cooldown = cooldown
        
        # 频率限制
        self._last_alert_time: Dict[str, datetime] = {}
        self._alert_history: List[Dict] = []
        
        # HTTP 会话（延迟初始化）
        self._session: Optional[aiohttp.ClientSession] = None
        
        self.logger = logging.getLogger(__name__)
    
    async def _get_session(self) -> aiohttp.ClientSession:
        """获取或创建 HTTP 会话"""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session
    
    async def close(self) -> None:
        """关闭 HTTP 会话"""
        if self._session and not self._session.closed:
            await self._session.close()
            self.logger.info("通知器 HTTP 会话已关闭")
    
    def _check_rate_limit(self, key: str) -> bool:
        """
        检查频率限制
        
        Args:
            key: 限制键（通常是 project_level）
            
        Returns:
            是否允许发送
        """
        now = datetime.now()
        if key in self._last_alert_time:
            elapsed = (now - self._last_alert_time[key]).total_seconds()
            if elapsed < self.cooldown:
                return False
        return True
    
    def _record_alert(self, key: str) -> None:
        """
        记录报警时间和历史
        
        Args:
            key: 限制键
        """
        self._last_alert_time[key] = datetime.now()
        self._alert_history.append({
            'key': key,
            'time': datetime.now()
        })
        # 限制历史记录数量
        if len(self._alert_history) > 1000:
            self._alert_history.pop(0)
    
    def _format_message(
        self,
        title: str,
        content: str,
        level: str
    ) -> str:
        """
        格式化消息内容
        
        Args:
            title: 标题
            content: 内容
            level: 消息级别
            
        Returns:
            格式化后的消息字符串
        """
        return (
            f"**{title}**\n\n"
            f"{content}\n\n"
            f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
    
    async def send_message(
        self,
        title: str,
        content: str,
        level: str = "info"
    ) -> bool:
        """
        发送通知消息
        
        Args:
            title: 消息标题
            content: 消息内容
            level: 消息级别（info, warning, error）
            
        Returns:
            是否发送成功
        """
        if not self.enabled:
            self.logger.debug("通知器未启用")
            return False
        
        # 检查频率限制
        rate_key = f"{self.project}_{level}"
        if not self._check_rate_limit(rate_key):
            self.logger.warning(f"通知频率限制：{title[:50]}")
            return False
        
        try:
            if self.mode == NotificationMode.WEBHOOK:
                return await self._send_via_webhook(title, content, level)
            else:
                return await self._send_via_service(title, content, level)
        except Exception as e:
            self.logger.error(f"通知发送异常：{e}")
            return False
    
    async def _send_via_webhook(
        self,
        title: str,
        content: str,
        level: str
    ) -> bool:
        """
        通过直连飞书 webhook 发送通知
        
        Args:
            title: 标题
            content: 内容
            level: 级别
            
        Returns:
            是否发送成功
        """
        if not self.webhook_url:
            self.logger.error("飞书 webhook URL 未配置")
            return False
        
        # 飞书交互卡片格式
        payload = {
            "msg_type": "interactive",
            "card": {
                "header": {
                    "title": {
                        "tag": "plain_text",
                        "content": title
                    }
                },
                "elements": [
                    {
                        "tag": "markdown",
                        "content": content
                    }
                ]
            }
        }
        
        try:
            session = await self._get_session()
            async with session.post(
                self.webhook_url,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.status == 200:
                    self._record_alert(f"{self.project}_{level}")
                    self.logger.info(f"Webhook 通知发送成功：{title[:50]}")
                    return True
                else:
                    self.logger.error(f"Webhook 发送失败：HTTP {resp.status}")
                    return False
        except Exception as e:
            self.logger.error(f"Webhook 发送异常：{e}")
            return False
    
    async def _send_via_service(
        self,
        title: str,
        content: str,
        level: str
    ) -> bool:
        """
        通过通用通知服务发送通知
        
        Args:
            title: 标题
            content: 内容
            level: 级别
            
        Returns:
            是否发送成功
        """
        message = self._format_message(title, content, level)
        
        data = {
            "project": self.project,
            "message": message,
            "type": "markdown",
            "level": level
        }
        
        try:
            session = await self._get_session()
            async with session.post(
                f"{self.service_url}/send",
                json=data,
                timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.status == 200:
                    result = await resp.json()
                    if result.get('code') == 0:
                        self._record_alert(f"{self.project}_{level}")
                        self.logger.info(f"服务通知发送成功：{title[:50]}")
                        return True
                    else:
                        self.logger.error(f"服务通知发送失败：{result}")
                        return False
                else:
                    self.logger.error(f"服务通知 HTTP 错误：{resp.status}")
                    return False
        except Exception as e:
            self.logger.error(f"服务通知发送异常：{e}")
            return False
    
    # ====== 通知模板方法 ======
    
    async def notify_grid_created(
        self,
        grid_id: str,
        upper_price: float,
        lower_price: float,
        grid_count: int,
        investment: float
    ) -> bool:
        """
        通知网格创建成功
        
        Args:
            grid_id: 网格 ID
            upper_price: 上边界价格
            lower_price: 下边界价格
            grid_count: 网格数量
            investment: 投资金额
            
        Returns:
            是否发送成功
        """
        title = "📊 网格创建成功"
        content = (
            f"**网格 ID**: {grid_id}\n"
            f"**价格区间**: {lower_price} - {upper_price}\n"
            f"**网格数量**: {grid_count}\n"
            f"**投资金额**: {investment} USDT"
        )
        return await self.send_message(title, content, level="info")
    
    async def notify_state_change(
        self,
        old_state: str,
        new_state: str,
        price: float,
        adx: float
    ) -> bool:
        """
        通知市场状态变化
        
        Args:
            old_state: 旧状态
            new_state: 新状态
            price: 当前价格
            adx: ADX 值
            
        Returns:
            是否发送成功
        """
        title = "🔄 市场状态变更"
        content = (
            f"**状态变化**: {old_state} → {new_state}\n"
            f"**当前价格**: {price}\n"
            f"**ADX**: {adx:.2f}"
        )
        return await self.send_message(title, content, level="warning")
    
    async def notify_risk_event(
        self,
        event_type: str,
        trigger_price: float,
        trigger_pnl: float,
        action: str
    ) -> bool:
        """
        通知风险事件触发
        
        Args:
            event_type: 事件类型
            trigger_price: 触发价格
            trigger_pnl: 触发盈亏
            action: 执行行动
            
        Returns:
            是否发送成功
        """
        title = "🚨 风险事件触发"
        content = (
            f"**事件类型**: {event_type}\n"
            f"**触发价格**: {trigger_price}\n"
            f"**触发盈亏**: {trigger_pnl:.2%}\n"
            f"**执行行动**: {action}"
        )
        return await self.send_message(title, content, level="error")
    
    async def notify_param_adjust(
        self,
        param_name: str,
        old_value: Any,
        new_value: Any,
        reason: str
    ) -> bool:
        """
        通知参数调整
        
        Args:
            param_name: 参数名称
            old_value: 旧值
            new_value: 新值
            reason: 调整原因
            
        Returns:
            是否发送成功
        """
        title = "⚙️ 参数调整"
        content = (
            f"**参数名称**: {param_name}\n"
            f"**旧值**: {old_value}\n"
            f"**新值**: {new_value}\n"
            f"**调整原因**: {reason}"
        )
        return await self.send_message(title, content, level="warning")
    
    async def notify_error(
        self,
        error_type: str,
        error_message: str,
        details: Optional[str] = None
    ) -> bool:
        """
        通知系统错误
        
        Args:
            error_type: 错误类型
            error_message: 错误消息
            details: 详细信息
            
        Returns:
            是否发送成功
        """
        title = "❌ 系统错误"
        content = f"**错误类型**: {error_type}\n\n**错误消息**: {error_message}"
        if details:
            content += f"\n\n**详细信息**: {details}"
        return await self.send_message(title, content, level="error")
    
    async def notify_grid_terminated(
        self,
        grid_id: str,
        profit: float = 0.0
    ) -> bool:
        """
        通知网格终止
        
        Args:
            grid_id: 网格 ID
            profit: 实现盈亏
            
        Returns:
            是否发送成功
        """
        title = "🛑 网格已终止"
        profit_str = f"+{profit:.2f}" if profit >= 0 else f"{profit:.2f}"
        content = (
            f"**网格 ID**: {grid_id}\n"
            f"**实现盈亏**: {profit_str} USDT"
        )
        return await self.send_message(title, content, level="warning")
    
    # ====== 工具方法 ======
    
    def get_alert_history(self, limit: int = 50) -> List[Dict]:
        """
        获取报警历史
        
        Args:
            limit: 返回数量限制
            
        Returns:
            报警历史列表
        """
        return self._alert_history[-limit:]
    
    def set_enabled(self, enabled: bool) -> None:
        """
        设置启用状态
        
        Args:
            enabled: 是否启用
        """
        self.enabled = enabled
        self.logger.info(f"通知器已{'启用' if enabled else '禁用'}")


# ====== 便捷函数 ======

async def send_notification(
    title: str,
    content: str,
    level: str = "info",
    notifier: Optional[UnifiedNotifier] = None
) -> bool:
    """
    便捷发送通知函数
    
    Args:
        title: 消息标题
        content: 消息内容
        level: 消息级别
        notifier: 通知器实例（None 则使用默认配置）
        
    Returns:
        是否发送成功
    """
    if notifier is None:
        notifier = UnifiedNotifier()
    return await notifier.send_message(title, content, level)
