import requests
import json
import os
import logging
from config.settings import LOG_DIR
from typing import Optional, Dict, Any


class LarkNotifier:
    """
    飞书通知类，用于向飞书群聊发送消息
    
    改造说明：
    - 保持原有接口不变，确保向后兼容
    - 内部实现改为调用通用通知服务
    - 通用服务地址：http://43.156.242.184:8766/api/v1
    """
    
    def __init__(self, webhook_url=None):
        """
        初始化飞书通知器
        
        Args:
            webhook_url (str): 飞书机器人的 webhook URL（已废弃，保留参数以兼容旧代码）
        """
        # 通用通知服务配置
        self.notification_service_url = os.getenv(
            'NOTIFICATION_SERVICE_URL', 
            'http://43.156.242.184:8766/api/v1'
        )
        # 项目标识
        self.project = os.getenv('NOTIFICATION_PROJECT', 'btc_eth_bnb')
        # 请求超时时间（秒）
        self.timeout = 10
        
        # 配置日志
        self.logger = logging.getLogger('lark_notifier')
        self.logger.info(f"飞书通知器初始化完成，项目标识：{self.project}")
        
    def send_text_message(self, content, level='info'):
        """
        发送文本消息到飞书（通过通用通知服务）
        
        Args:
            content (str): 消息内容
            level (str): 通知级别（info/warning/error），默认info
            
        Returns:
            dict: API 响应结果
        """
        try:
            # 构建请求参数
            payload = {
                "project": self.project,
                "message": content,
                "type": "text",
                "level": level
            }
            
            # 调用通用通知服务
            response = requests.post(
                f"{self.notification_service_url}/send",
                json=payload,
                timeout=self.timeout
            )
            
            response.raise_for_status()
            result = response.json()
            
            # 检查响应结果
            if result.get('code') == 0:
                self.logger.info(f"飞书消息发送成功：{result.get('data', {}).get('msg_id', 'N/A')}")
                return {"status": "success", "message": "Message sent", "data": result.get('data')}
            else:
                error_msg = result.get('message', 'Unknown error')
                self.logger.error(f"飞书消息发送失败：{error_msg}")
                return {"status": "error", "message": error_msg}
                
        except requests.exceptions.Timeout:
            error_msg = "发送飞书消息超时"
            self.logger.error(error_msg)
            return {"status": "error", "message": error_msg}
        except requests.exceptions.RequestException as e:
            error_msg = f"发送飞书消息网络异常：{str(e)}"
            self.logger.error(error_msg)
            return {"status": "error", "message": error_msg}
        except Exception as e:
            error_msg = f"发送飞书消息失败：{str(e)}"
            self.logger.error(error_msg)
            return {"status": "error", "message": error_msg}
    
    def send_success_notification(self, currency, report_path, screenshot_path):
        """
        发送任务成功完成的通知
        
        Args:
            currency (str): 交易对
            report_path (str): 报告文件路径
            screenshot_path (str): 截图文件路径
        """
        message = f"""
✅ 币安期货分析任务已完成

交易对: {currency}
执行时间: {self._get_current_time()}
报告文件: {report_path}
截图文件: {screenshot_path}

请查看分析结果并采取相应行动。
        """
        return self.send_text_message(message, level='info')
    
    def send_error_notification(self, currency, error_message):
        """
        发送任务错误通知
        
        Args:
            currency (str): 交易对
            error_message (str): 错误信息
        """
        message = f"""
❌ 币安期货分析任务执行失败

交易对: {currency}
执行时间: {self._get_current_time()}
错误信息: {error_message}

请检查系统状态和日志文件。
        """
        return self.send_text_message(message, level='error')
    
    def send_scheduler_startup_notification(self, next_run_time, timezone):
        """
        发送调度器启动通知
        
        Args:
            next_run_time (str): 下次执行时间
            timezone (str): 时区
        """
        message = f"🚀 币安期货分析调度器已启动，下次执行时间: {next_run_time} ({timezone})"
        return self.send_text_message(message, level='info')
    
    def send_scheduler_shutdown_notification(self, timezone):
        """
        发送调度器关闭通知
        
        Args:
            timezone (str): 时区
        """
        message = f"🛑 币安期货分析调度器已停止，当前时间: {self._get_current_time()} ({timezone})"
        return self.send_text_message(message, level='warning')
    
    def send_scheduler_completion_notification(self, next_run_time, timezone):
        """
        发送定时任务完成通知
        
        Args:
            next_run_time (str): 下次执行时间
            timezone (str): 时区
        """
        message = f"✅ 定时任务已执行完成，下次执行时间: {next_run_time} ({timezone})"
        return self.send_text_message(message, level='info')
    
    def send_scheduler_before_run_notification(self, currencies, timezone):
        """
        发送任务即将执行的通知
        
        Args:
            currencies (list): 交易对列表
            timezone (str): 时区
        """
        message = f"🔄 币安期货分析任务即将开始，交易对: {', '.join(currencies)}，当前时间: {self._get_current_time()} ({timezone})"
        return self.send_text_message(message, level='info')
    
    def send_analysis_result_notification(self, currencies, report_content):
        """
        发送分析结果通知，包括分析币种、分析结论和决策
        优先提取第四章"交易操作总结"的内容进行推送
        
        Args:
            currencies (list): 交易对列表
            report_content (str): 分析报告内容
        """
        # 提取分析时间
        analysis_time = self._get_current_time()
        lines = report_content.split('\n')
        
        # 尝试从报告中提取分析时间
        for line in lines:
            if '分析时间' in line or '时间' in line:
                parts = line.split('：')
                if len(parts) > 1:
                    analysis_time = parts[1].strip()
                    break
        
        # 提取分析币种
        analysis_currencies = ', '.join(currencies)
        for line in lines:
            if '分析币种' in line or '币种' in line:
                parts = line.split('：')
                if len(parts) > 1:
                    analysis_currencies = parts[1].strip()
                    break
        
        # 优先提取核心分析内容
        trading_summary = self._extract_trading_summary(report_content)
        
        # 如果找到了核心分析内容，使用新格式；否则使用旧格式
        if trading_summary:
            message = f"""
✅ 交易分析核心结论（500U 阶段一）

**当前账户（500U 阶段一）**：
- 总资金：500U
- 单仓保证金：30U
- 最大持仓：2 个
- 备用金：≥400U

**分析币种:** {analysis_currencies}
**分析时间:** {analysis_time}

{trading_summary}

详细报告已生成，请查看完整分析内容。
            """
        else:
            # 备用方案：提取第三章节："三、最终开仓建议与风险管理"的结论部分
            analysis_summary = ""
            in_section = False
            for i, line in enumerate(lines):
                if '三、最终开仓建议与风险管理' in line or '最终开仓建议与风险管理' in line or '### 第三章' in line:
                    in_section = True
                    continue
                if in_section:
                    if line.strip().startswith('第四章') or line.strip().startswith('四、') or line.strip().startswith('4.'):
                        break
                    analysis_summary += line + '\n'
            
            if not analysis_summary:
                analysis_summary = "分析已完成，请查看详细报告获取具体结论和决策建议。"
            
            message = f"""
📊 交易分析结果

**分析币种:** {analysis_currencies}
**分析时间:** {analysis_time}

**分析结论与决策:**
{analysis_summary}

详细报告已生成，请查看完整分析内容。
            """
        
        return self.send_text_message(message, level='info')
    
    def _extract_trading_summary(self, report_content):
        """
        提取核心交易观点用于飞书推送
        
        按优先级提取以下内容：
        1. "最终交易建议汇总与交易日志"部分
        2. "最终决策"部分
        3. 4.1"核心观点提炼"章节
        4. 第四章"交易操作总结"
        5. 第三章"最终开仓建议与风险管理"
        
        Args:
            report_content (str): 分析报告内容
            
        Returns:
            str: 提取的核心观点内容，如果未找到则返回 None
        """
        lines = report_content.split('\n')
        
        # 首先尝试查找最终建议/决策/总结部分（最高优先级）
        # 支持的标题格式：
        # - 最终交易建议汇总与交易日志
        # - 最终交易决策与日志生成
        # - 最终开仓建议与交易日志
        # - 综合结论与交易日志
        summary_lines = []
        in_final_summary = False
        found_final_summary = False
        
        for i, line in enumerate(lines):
            # 检查是否是最终建议/决策/开仓建议/综合结论的标题
            if ('最终交易建议汇总' in line or 
                '最终交易决策' in line or 
                '最终开仓建议' in line or
                '综合结论与交易日志' in line) and ('**' in line or '###' in line):
                in_final_summary = True
                found_final_summary = True
                continue
            
            # 如果已经在最终建议部分内
            if in_final_summary:
                # 检查是否进入 JSON 部分（遇到 ```json）
                if line.strip().startswith('```json') or line.strip() == '```':
                    break
                
                # 收集最终建议的内容
                if line.strip():
                    summary_lines.append(line)
        
        # 如果找到了最终建议，返回提取的内容
        if found_final_summary and summary_lines:
            # 移除末尾的多余空行
            while summary_lines and not summary_lines[-1].strip():
                summary_lines.pop()
            return '\n'.join(summary_lines)
        
        # 其次尝试查找"最终决策"部分（AI 实际生成的格式）
        summary_lines = []
        in_final_decision = False
        found_final_decision = False
        
        for i, line in enumerate(lines):
            # 检查是否是"最终决策"的标题
            if '最终决策' in line and ('**' in line or '###' in line):
                in_final_decision = True
                found_final_decision = True
                continue
            
            # 如果已经在最终决策部分内
            if in_final_decision:
                # 检查是否进入下一节（遇到新的加粗标题或###标题）
                if (line.strip().startswith('**') and '**:' in line) or line.strip().startswith('###'):
                    break
                
                # 收集最终决策的内容
                if line.strip():
                    summary_lines.append(line)
        
        # 如果找到了最终决策，返回提取的内容
        if found_final_decision and summary_lines:
            # 移除末尾的多余空行
            while summary_lines and not summary_lines[-1].strip():
                summary_lines.pop()
            return '\n'.join(summary_lines)
        
        # 如果未找到"最终决策"，则尝试查找 4.1"核心观点提炼"
        summary_lines = []
        in_section_4_1 = False
        found_section_4_1 = False
        
        for i, line in enumerate(lines):
            # 检查是否是 4.1 的标题（支持多种格式）
            if ('4.1' in line or '4.1.' in line) and '核心观点' in line:
                in_section_4_1 = True
                found_section_4_1 = True
                continue
            
            # 如果已经在 4.1 部分内
            if in_section_4_1:
                # 检查是否进入下一节（遇到 4.2 或其他小标题）
                if line.strip().startswith('4.2') or (line.strip().startswith('####') and line.strip() != ''):
                    break
                
                # 收集 4.1 的内容（保留非空行）
                if line.strip():
                    summary_lines.append(line)
        
        # 如果找到了 4.1，返回提取的内容
        if found_section_4_1 and summary_lines:
            # 移除末尾的多余空行
            while summary_lines and not summary_lines[-1].strip():
                summary_lines.pop()
            return '\n'.join(summary_lines)
        
        # 如果仍未找到，则尝试查找第四章"交易操作总结"
        summary_lines = []
        in_chapter4 = False
        found_chapter4_header = False
        
        for i, line in enumerate(lines):
            # 检查是否是第四章的标题
            if ('第四章' in line or '4. 交易操作总结' in line or '四、交易操作总结' in line) and '交易操作总结' in line:
                in_chapter4 = True
                found_chapter4_header = True
                continue
            
            # 如果已经在第四章内
            if in_chapter4:
                # 检查是否进入下一章
                if line.strip().startswith('第五章') or line.strip().startswith('五、') or line.strip().startswith('5.'):
                    break
                
                # 收集第四章的内容
                if line.strip():  # 跳过空行
                    summary_lines.append(line)
        
        # 如果找到了第四章，返回提取的内容
        if found_chapter4_header and summary_lines:
            return '\n'.join(summary_lines)
        
        # 如果仍未找到，则尝试查找第三章"最终开仓建议与风险管理"
        summary_lines = []
        in_chapter3 = False
        found_chapter3_header = False
        
        for i, line in enumerate(lines):
            # 检查是否是第三章的标题
            if ('第三章' in line or '3.' in line or '三、' in line) and ('最终开仓建议' in line or '开仓建议' in line or '风险管理' in line):
                in_chapter3 = True
                found_chapter3_header = True
                continue
            
            # 如果已经在第三章内
            if in_chapter3:
                # 检查是否进入下一章
                if line.strip().startswith('第四章') or line.strip().startswith('四、') or line.strip().startswith('4.'):
                    break
                
                # 收集第三章的内容（跳过空行）
                if line.strip():
                    summary_lines.append(line)
        
        # 如果找到了第三章，返回提取的内容
        if found_chapter3_header and summary_lines:
            return '\n'.join(summary_lines)
        
        return None
    
    def _get_current_time(self):
        """
        获取当前时间字符串
        
        Returns:
            str: 格式化的时间字符串
        """
        from datetime import datetime
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def notify_completion(currency, report_path, screenshot_path):
    """
    通知任务完成的便捷函数
    
    Args:
        currency (str): 交易对
        report_path (str): 报告文件路径
        screenshot_path (str): 截图文件路径
    """
    notifier = LarkNotifier()
    return notifier.send_success_notification(currency, report_path, screenshot_path)


def notify_error(currency, error_message):
    """
    通知任务错误的便捷函数
    
    Args:
        currency (str): 交易对
        error_message (str): 错误信息
    """
    notifier = LarkNotifier()
    return notifier.send_error_notification(currency, error_message)