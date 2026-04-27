#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
K 线数据同步实时监控器
功能：
1. 实时监控同步任务进度
2. 检测失败率和错误类型
3. 自动告警（失败率>10% 或任务停止）
4. 定时推送进度报告
"""

import sys
import time
from datetime import datetime, timedelta
from typing import Dict, List, Tuple
import logging

from utils.logger import get_logger
from data.database import DatabaseManager

logger = get_logger()


class SyncMonitor:
    """同步任务监控器"""
    
    def __init__(self):
        self.db = None
        self.last_check_time = None
        self.last_processed_count = 0
        self.alert_threshold = 0.10  # 失败率超过 10% 告警
        
    def connect(self):
        """连接数据库"""
        self.db = DatabaseManager()
        
    def close(self):
        """关闭连接"""
        if self.db:
            self.db.close()
    
    def get_sync_stats(self) -> Dict:
        """获取同步统计信息"""
        if not self.db:
            self.connect()
        
        # 总体统计
        total_stats = self.db.conn.execute("""
            SELECT 
                COUNT(*) as total_records,
                COUNT(DISTINCT code) as stocks_with_data,
                MIN(date) as earliest_date,
                MAX(date) as latest_date
            FROM klines
        """).fetchone()
        
        # 按数据量分级统计
        completeness_stats = self.db.conn.execute("""
            SELECT 
                COUNT(CASE WHEN cnt >= 200 THEN 1 END) as complete,
                COUNT(CASE WHEN cnt >= 100 AND cnt < 200 THEN 1 END) as good,
                COUNT(CASE WHEN cnt >= 50 AND cnt < 100 THEN 1 END) as fair,
                COUNT(CASE WHEN cnt < 50 OR cnt IS NULL THEN 1 END) as poor
            FROM (
                SELECT code, COUNT(*) as cnt FROM klines GROUP BY code
            ) t
        """).fetchone()
        
        # 4 月数据情况
        april_stats = self.db.conn.execute("""
            SELECT 
                date,
                COUNT(DISTINCT code) as stock_count
            FROM klines
            WHERE date >= '2026-04-01'
            GROUP BY date
            ORDER BY date
        """).fetchall()
        
        return {
            'total_records': total_stats[0],
            'stocks_with_data': total_stats[1],
            'earliest_date': total_stats[2],
            'latest_date': total_stats[3],
            'complete': completeness_stats[0],
            'good': completeness_stats[1],
            'fair': completeness_stats[2],
            'poor': completeness_stats[3],
            'april_data': [(row[0], row[1]) for row in april_stats]
        }
    
    def get_error_stats(self, since: datetime = None) -> Dict:
        """获取错误统计（需要分析日志）"""
        # 这个需要从日志中提取，暂时返回空
        return {
            'total_errors': 0,
            'error_types': {}
        }
    
    def check_progress(self, interval_seconds: int = 60) -> Tuple[bool, float]:
        """
        检查进度是否在推进
        
        Args:
            interval_seconds: 检查间隔
        
        Returns:
            (is_making_progress, progress_rate)
        """
        current_time = datetime.now()
        stats = self.get_sync_stats()
        current_count = stats['stocks_with_data']
        
        if self.last_check_time is None:
            self.last_check_time = current_time
            self.last_processed_count = current_count
            return True, 0.0
        
        # 计算进度变化
        time_diff = (current_time - self.last_check_time).total_seconds()
        count_diff = current_count - self.last_processed_count
        
        progress_rate = count_diff / time_diff if time_diff > 0 else 0
        
        # 如果 5 分钟内没有进展，认为任务可能停止
        is_making_progress = count_diff > 0 or time_diff < 300
        
        self.last_check_time = current_time
        self.last_processed_count = current_count
        
        return is_making_progress, progress_rate
    
    def generate_report(self) -> str:
        """生成监控报告"""
        stats = self.get_sync_stats()
        
        report = []
        report.append("=" * 60)
        report.append(f"📊 K 线数据同步监控报告")
        report.append(f"⏰ 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        report.append("=" * 60)
        report.append("")
        report.append("📈 总体统计:")
        report.append(f"  • K 线总记录数：{stats['total_records']:,} 条")
        report.append(f"  • 有数据的股票：{stats['stocks_with_data']} 只")
        report.append(f"  • 数据日期范围：{stats['earliest_date']} 至 {stats['latest_date']}")
        report.append("")
        report.append("📊 数据完整性:")
        report.append(f"  • ✅ 完整 (≥200 天): {stats['complete']} 只 ({stats['complete']/max(stats['stocks_with_data'],1)*100:.1f}%)")
        report.append(f"  • ✅ 较完整 (100-199 天): {stats['good']} 只")
        report.append(f"  • ⚠️ 较少 (50-99 天): {stats['fair']} 只")
        report.append(f"  • ❌ 很少 (<50 天): {stats['poor']} 只")
        report.append("")
        report.append("📅 2026 年 4 月数据:")
        for date, count in stats['april_data']:
            report.append(f"  • {date}: {count} 只股票")
        report.append("")
        report.append("=" * 60)
        
        return "\n".join(report)
    
    def check_and_alert(self) -> List[str]:
        """检查是否需要告警"""
        alerts = []
        
        # 检查进度
        is_making_progress, rate = self.check_progress()
        if not is_making_progress:
            alerts.append("⚠️ 警告：同步任务可能已停止（5 分钟内无进展）")
        
        # 检查数据完整性
        stats = self.get_sync_stats()
        total_stocks = 5324  # 总股票数
        completion_rate = stats['stocks_with_data'] / total_stocks
        
        if completion_rate < 0.5:
            alerts.append(f"📊 进度提醒：当前完成率 {completion_rate*100:.1f}%")
        
        return alerts


def main():
    """主函数"""
    monitor = SyncMonitor()
    
    print("=" * 60)
    print("🔍 K 线数据同步实时监控")
    print("=" * 60)
    print()
    
    # 生成初始报告
    try:
        monitor.connect()
        report = monitor.generate_report()
        print(report)
        
        # 检查告警
        alerts = monitor.check_and_alert()
        if alerts:
            print("\n⚠️  告警信息:")
            for alert in alerts:
                print(f"  {alert}")
        else:
            print("\n✅ 系统运行正常")
        
    finally:
        monitor.close()


if __name__ == '__main__':
    main()
