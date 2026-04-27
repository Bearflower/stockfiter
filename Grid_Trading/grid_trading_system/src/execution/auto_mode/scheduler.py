"""
任务调度器
负责定时巡检、事件触发、任务优先级管理
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class TaskScheduler:
    """任务调度器"""
    
    def __init__(
        self,
        inspection_interval: int = 3600,
        atr_change_threshold: float = 0.2,
        min_adjustment_interval: int = 14400,
        max_adjustments_per_day: int = 6
    ):
        """
        初始化任务调度器
        
        Args:
            inspection_interval: 巡检间隔（秒）
            atr_change_threshold: ATR 变化触发阈值
            min_adjustment_interval: 最小调整间隔（秒）
            max_adjustments_per_day: 每日最大调整次数
        """
        self.inspection_interval = inspection_interval
        self.atr_change_threshold = atr_change_threshold
        self.min_adjustment_interval = min_adjustment_interval
        self.max_adjustments_per_day = max_adjustments_per_day
        
        self._running = False
        self._tasks: List[asyncio.Task] = []
        self._last_inspection_time: Optional[datetime] = None
        self._last_adjustment_time: Optional[datetime] = None
        self._adjustment_count_today: int = 0
        self._last_reset_date: Optional[datetime] = None
        
        # 事件回调
        self._event_callbacks: Dict[str, List[Callable]] = {}
    
    async def start(self) -> None:
        """启动调度器"""
        if self._running:
            logger.warning("调度器已在运行中")
            return
        
        logger.info("启动任务调度器")
        self._running = True
        
        # 启动巡检任务
        inspection_task = asyncio.create_task(self._inspection_loop())
        self._tasks.append(inspection_task)
        
        # 启动事件监听任务
        event_task = asyncio.create_task(self._event_monitoring_loop())
        self._tasks.append(event_task)
        
        logger.info("任务调度器已启动")
    
    async def stop(self) -> None:
        """停止调度器"""
        if not self._running:
            return
        
        logger.info("停止任务调度器")
        self._running = False
        
        # 取消所有任务
        for task in self._tasks:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        
        self._tasks.clear()
        logger.info("任务调度器已停止")
    
    async def _inspection_loop(self) -> None:
        """巡检循环"""
        logger.info(f"启动巡检循环，间隔：{self.inspection_interval}秒")
        
        while self._running:
            try:
                # 重置每日计数
                self._reset_daily_count()
                
                # 执行巡检
                await self._run_inspection()
                
                # 等待下次巡检
                await asyncio.sleep(self.inspection_interval)
                
            except asyncio.CancelledError:
                logger.info("巡检循环已取消")
                break
            except Exception as e:
                logger.error(f"巡检循环异常：{e}", exc_info=True)
                await asyncio.sleep(60)  # 异常后等待 1 分钟
    
    async def _run_inspection(self) -> None:
        """执行巡检任务"""
        logger.info("开始执行巡检任务")
        
        self._last_inspection_time = datetime.now()
        
        # 触发巡检事件
        await self._trigger_event('inspection', {
            'timestamp': self._last_inspection_time,
            'type': 'scheduled'
        })
        
        logger.info("巡检任务执行完成")
    
    async def _event_monitoring_loop(self) -> None:
        """事件监控循环"""
        logger.info("启动事件监控循环")
        
        while self._running:
            try:
                # 持续监听事件（这里简化处理，实际应该监听消息队列或事件总线）
                await asyncio.sleep(1)
                
            except asyncio.CancelledError:
                logger.info("事件监控循环已取消")
                break
            except Exception as e:
                logger.error(f"事件监控循环异常：{e}", exc_info=True)
    
    async def trigger_parameter_adjustment(
        self,
        trigger_type: str,
        trigger_data: Dict[str, Any]
    ) -> bool:
        """
        触发参数调整
        
        Args:
            trigger_type: 触发类型（ATR_CHANGE, STATE_CHANGE, EDGE_APPROACH, etc.）
            trigger_data: 触发数据
            
        Returns:
            是否允许调整
        """
        # 检查是否启用调整
        if not self._is_adjustment_enabled():
            logger.info("参数调整已禁用")
            return False
        
        # 检查最小间隔
        if not self._check_adjustment_interval():
            logger.info("未到调整时间间隔")
            return False
        
        # 检查每日次数限制
        if not self._check_daily_limit():
            logger.info(f"已达到每日调整上限 ({self.max_adjustments_per_day}次)")
            return False
        
        # 触发调整事件
        await self._trigger_event('parameter_adjustment', {
            'trigger_type': trigger_type,
            'trigger_data': trigger_data,
            'timestamp': datetime.now()
        })
        
        # 记录调整
        self._record_adjustment()
        
        return True
    
    def _is_adjustment_enabled(self) -> bool:
        """检查是否启用调整"""
        return True  # 简化处理，默认启用
    
    def _check_adjustment_interval(self) -> bool:
        """检查调整间隔"""
        if self._last_adjustment_time is None:
            return True
        
        elapsed = (datetime.now() - self._last_adjustment_time).total_seconds()
        return elapsed >= self.min_adjustment_interval
    
    def _check_daily_limit(self) -> bool:
        """检查每日限制"""
        return self._adjustment_count_today < self.max_adjustments_per_day
    
    def _record_adjustment(self) -> None:
        """记录调整"""
        self._last_adjustment_time = datetime.now()
        self._adjustment_count_today += 1
        logger.info(f"记录参数调整，今日累计：{self._adjustment_count_today}次")
    
    def _reset_daily_count(self) -> None:
        """重置每日计数"""
        today = datetime.now().date()
        
        if self._last_reset_date is None or today > self._last_reset_date.date():
            self._adjustment_count_today = 0
            self._last_reset_date = datetime.now()
            logger.info("重置每日调整计数")
    
    def register_event_callback(
        self,
        event_type: str,
        callback: Callable
    ) -> None:
        """
        注册事件回调
        
        Args:
            event_type: 事件类型
            callback: 回调函数
        """
        if event_type not in self._event_callbacks:
            self._event_callbacks[event_type] = []
        
        self._event_callbacks[event_type].append(callback)
        logger.info(f"注册事件回调：{event_type}")
    
    async def _trigger_event(
        self,
        event_type: str,
        data: Dict[str, Any]
    ) -> None:
        """
        触发事件
        
        Args:
            event_type: 事件类型
            data: 事件数据
        """
        if event_type not in self._event_callbacks:
            return
        
        logger.info(f"触发事件：{event_type}")
        
        for callback in self._event_callbacks[event_type]:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(data)
                else:
                    callback(data)
            except Exception as e:
                logger.error(f"事件回调执行失败：{e}", exc_info=True)
    
    def get_status(self) -> Dict:
        """获取调度器状态"""
        return {
            'running': self._running,
            'last_inspection': self._last_inspection_time,
            'last_adjustment': self._last_adjustment_time,
            'adjustments_today': self._adjustment_count_today,
            'max_adjustments_per_day': self.max_adjustments_per_day,
            'inspection_interval': self.inspection_interval,
            'min_adjustment_interval': self.min_adjustment_interval
        }
    
    def can_adjust_now(self) -> bool:
        """检查是否可以立即调整"""
        return (
            self._is_adjustment_enabled() and
            self._check_adjustment_interval() and
            self._check_daily_limit()
        )
    
    def get_next_adjustment_time(self) -> Optional[datetime]:
        """获取下次可调整时间"""
        if self._last_adjustment_time is None:
            return datetime.now()
        
        next_time = self._last_adjustment_time + timedelta(seconds=self.min_adjustment_interval)
        return next_time if self._check_daily_limit() else None


# 别名：InspectionScheduler = TaskScheduler
# 为了兼容 main.py 中的导入
InspectionScheduler = TaskScheduler
