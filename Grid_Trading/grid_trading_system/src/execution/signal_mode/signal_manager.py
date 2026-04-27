"""
信号管理器
管理信号的生命周期：存储、查询、确认、拒绝、过期清理
支持信号状态追踪和历史记录
"""

import logging
from collections import OrderedDict
from datetime import datetime, timedelta
from enum import Enum
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class SignalStatus(Enum):
    """信号状态枚举"""
    PENDING = "pending"        # 待处理（刚生成）
    CONFIRMED = "confirmed"    # 已确认（用户确认执行）
    REJECTED = "rejected"      # 已拒绝（用户拒绝执行）
    EXPIRED = "expired"        # 已过期（超时未处理）
    EXECUTED = "executed"      # 已执行（信号对应的操作已执行）


class SignalRecord:
    """信号记录"""

    def __init__(
        self,
        signal_id: str,
        signal_data: Dict,
        status: SignalStatus = SignalStatus.PENDING,
        created_at: Optional[datetime] = None,
        updated_at: Optional[datetime] = None
    ):
        """
        初始化信号记录

        Args:
            signal_id: 信号唯一标识
            signal_data: 信号数据（来自SignalGenerator）
            status: 信号状态
            created_at: 创建时间
            updated_at: 更新时间
        """
        self.signal_id = signal_id
        self.signal_data = signal_data
        self.status = status
        self.created_at = created_at or datetime.now()
        self.updated_at = updated_at or self.created_at

    def to_dict(self) -> Dict:
        """转换为字典"""
        return {
            'signal_id': self.signal_id,
            'status': self.status.value,
            'signal_data': self.signal_data,
            'created_at': self.created_at.isoformat(),
            'updated_at': self.updated_at.isoformat()
        }


class SignalManager:
    """
    信号管理器

    功能：
    - 添加新信号
    - 查询待处理信号
    - 确认/拒绝信号
    - 过期旧信号清理
    - 信号状态追踪
    """

    def __init__(
        self,
        max_pending_signals: int = 50,
        signal_ttl_hours: int = 24
    ):
        """
        初始化信号管理器

        Args:
            max_pending_signals: 最大待处理信号数量
            signal_ttl_hours: 信号存活时间（小时）
        """
        self.max_pending_signals = max_pending_signals
        self.signal_ttl_hours = signal_ttl_hours

        # 信号存储（按信号ID索引）
        self._signals: Dict[str, SignalRecord] = OrderedDict()

        # 按交易对索引的信号列表
        self._symbol_signals: Dict[str, List[str]] = {}

        # 信号计数器
        self._counter = 0

        logger.info("✅ 信号管理器初始化完成")

    def add_signal(self, signal_data: Dict) -> str:
        """
        添加新信号

        Args:
            signal_data: 信号数据（Signal.to_dict()的结果）

        Returns:
            信号唯一标识
        """
        # 生成信号ID
        self._counter += 1
        symbol = signal_data.get('symbol', 'UNKNOWN')
        signal_id = f"SIG-{symbol}-{self._counter:05d}-{datetime.now().strftime('%Y%m%d%H%M%S')}"

        # 创建信号记录
        record = SignalRecord(
            signal_id=signal_id,
            signal_data=signal_data
        )

        # 存储信号
        self._signals[signal_id] = record

        # 按交易对索引
        if symbol not in self._symbol_signals:
            self._symbol_signals[symbol] = []
        self._symbol_signals[symbol].append(signal_id)

        # 清理过期的待处理信号（保持最大数量）
        self._cleanup_excess_signals()

        logger.info(f"✅ 新信号已添加：{signal_id} ({symbol})")
        return signal_id

    def get_signal(self, signal_id: str) -> Optional[SignalRecord]:
        """
        获取信号记录

        Args:
            signal_id: 信号ID

        Returns:
            信号记录，如果不存在返回None
        """
        return self._signals.get(signal_id)

    def get_pending_signals(self, symbol: Optional[str] = None) -> List[SignalRecord]:
        """
        获取待处理信号列表

        Args:
            symbol: 交易对过滤（可选）

        Returns:
            待处理信号列表
        """
        result = []

        if symbol:
            # 按交易对过滤
            signal_ids = self._symbol_signals.get(symbol, [])
            for sid in signal_ids:
                record = self._signals.get(sid)
                if record and record.status == SignalStatus.PENDING:
                    result.append(record)
        else:
            # 获取所有待处理信号
            for record in self._signals.values():
                if record.status == SignalStatus.PENDING:
                    result.append(record)

        # 按创建时间倒序排列
        result.sort(key=lambda r: r.created_at, reverse=True)
        return result

    def get_signals_by_status(
        self,
        status: SignalStatus,
        symbol: Optional[str] = None
    ) -> List[SignalRecord]:
        """
        按状态获取信号

        Args:
            status: 信号状态
            symbol: 交易对过滤（可选）

        Returns:
            信号记录列表
        """
        result = []

        for record in self._signals.values():
            if record.status != status:
                continue
            if symbol and record.signal_data.get('symbol') != symbol:
                continue
            result.append(record)

        result.sort(key=lambda r: r.created_at, reverse=True)
        return result

    def confirm_signal(self, signal_id: str, executor_notes: str = "") -> bool:
        """
        确认信号（用户确认执行）

        Args:
            signal_id: 信号ID
            executor_notes: 执行人备注

        Returns:
            是否确认成功
        """
        record = self._signals.get(signal_id)
        if not record:
            logger.warning(f"信号不存在：{signal_id}")
            return False

        if record.status != SignalStatus.PENDING:
            logger.warning(f"信号状态不是待处理，无法确认：{signal_id} (当前状态：{record.status.value})")
            return False

        record.status = SignalStatus.CONFIRMED
        record.updated_at = datetime.now()
        record.signal_data['executor_notes'] = executor_notes

        logger.info(f"✅ 信号已确认：{signal_id}")
        return True

    def reject_signal(self, signal_id: str, reason: str = "") -> bool:
        """
        拒绝信号

        Args:
            signal_id: 信号ID
            reason: 拒绝原因

        Returns:
            是否拒绝成功
        """
        record = self._signals.get(signal_id)
        if not record:
            logger.warning(f"信号不存在：{signal_id}")
            return False

        if record.status != SignalStatus.PENDING:
            logger.warning(f"信号状态不是待处理，无法拒绝：{signal_id}")
            return False

        record.status = SignalStatus.REJECTED
        record.updated_at = datetime.now()
        record.signal_data['reject_reason'] = reason

        logger.info(f"❌ 信号已拒绝：{signal_id} (原因：{reason})")
        return True

    def mark_executed(self, signal_id: str, execution_result: Dict = None) -> bool:
        """
        标记信号已执行

        Args:
            signal_id: 信号ID
            execution_result: 执行结果

        Returns:
            是否标记成功
        """
        record = self._signals.get(signal_id)
        if not record:
            logger.warning(f"信号不存在：{signal_id}")
            return False

        if record.status != SignalStatus.CONFIRMED:
            logger.warning(f"信号状态不是已确认，无法标记执行：{signal_id}")
            return False

        record.status = SignalStatus.EXECUTED
        record.updated_at = datetime.now()
        if execution_result:
            record.signal_data['execution_result'] = execution_result

        logger.info(f"✅ 信号已标记执行：{signal_id}")
        return True

    def expire_old_signals(self, max_age_hours: Optional[int] = None) -> int:
        """
        过期旧信号

        Args:
            max_age_hours: 最大保留时长（小时），默认使用初始化配置

        Returns:
            过期信号数量
        """
        if max_age_hours is None:
            max_age_hours = self.signal_ttl_hours

        cutoff_time = datetime.now() - timedelta(hours=max_age_hours)
        expired_count = 0

        # 找出所有过期的待处理信号
        expired_ids = []
        for signal_id, record in self._signals.items():
            if record.status == SignalStatus.PENDING and record.created_at < cutoff_time:
                expired_ids.append(signal_id)

        # 标记为过期
        for signal_id in expired_ids:
            record = self._signals[signal_id]
            record.status = SignalStatus.EXPIRED
            record.updated_at = datetime.now()
            expired_count += 1
            logger.info(f"⏰ 信号已过期：{signal_id}")

        return expired_count

    def cleanup_expired_signals(self, max_age_hours: Optional[int] = None) -> int:
        """
        清理过期信号（从内存中移除）

        Args:
            max_age_hours: 最大保留时长（小时）

        Returns:
            清理的信号数量
        """
        if max_age_hours is None:
            max_age_hours = self.signal_ttl_hours * 2  # 过期信号再保留一倍时间

        cutoff_time = datetime.now() - timedelta(hours=max_age_hours)
        cleanup_count = 0

        # 找出需要清理的信号
        to_remove = []
        for signal_id, record in self._signals.items():
            if record.updated_at < cutoff_time:
                to_remove.append(signal_id)

        # 从存储中移除
        for signal_id in to_remove:
            record = self._signals.pop(signal_id)
            symbol = record.signal_data.get('symbol', 'UNKNOWN')

            # 从交易对索引中移除
            if symbol in self._symbol_signals:
                self._symbol_signals[symbol] = [
                    sid for sid in self._symbol_signals[symbol] if sid != signal_id
                ]

            cleanup_count += 1

        if cleanup_count > 0:
            logger.info(f"🧹 已清理 {cleanup_count} 个过期信号")

        return cleanup_count

    def get_statistics(self) -> Dict:
        """
        获取信号统计信息

        Returns:
            统计信息字典
        """
        stats = {
            'total_signals': len(self._signals),
            'by_status': {},
            'by_symbol': {}
        }

        # 按状态统计
        for status in SignalStatus:
            count = sum(1 for r in self._signals.values() if r.status == status)
            stats['by_status'][status.value] = count

        # 按交易对统计
        for symbol, signal_ids in self._symbol_signals.items():
            stats['by_symbol'][symbol] = len(signal_ids)

        return stats

    def _cleanup_excess_signals(self) -> None:
        """
        清理超出最大数量的待处理信号
        """
        pending_signals = self.get_pending_signals()
        if len(pending_signals) > self.max_pending_signals:
            # 过期最旧的信号
            excess = len(pending_signals) - self.max_pending_signals
            for signal in pending_signals[-excess:]:
                signal.status = SignalStatus.EXPIRED
                signal.updated_at = datetime.now()
                logger.info(f"⏰ 信号因超出最大数量已过期：{signal.signal_id}")

    def clear_all(self) -> None:
        """清空所有信号（仅用于测试）"""
        self._signals.clear()
        self._symbol_signals.clear()
        self._counter = 0
        logger.info("🧹 所有信号已清空")
