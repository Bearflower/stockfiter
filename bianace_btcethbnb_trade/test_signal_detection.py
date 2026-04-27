#!/usr/bin/env python3
"""
手动测试信号检测功能

在服务器上运行，验证修复后的代码是否能正常工作
"""

import sys
import os
import logging

# 设置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# 添加项目路径
sys.path.insert(0, '/root/trading_system')

def test_signal_detection():
    """测试信号检测"""
    logger.info("=" * 60)
    logger.info("开始测试信号检测功能")
    logger.info("=" * 60)

    try:
        # 导入模块
        logger.info("步骤 1: 导入模块...")
        from core.signal import SignalDetector, get_signal_detector
        from core.data import get_data_fetcher
        logger.info("✅ 模块导入成功")

        # 获取实例
        logger.info("步骤 2: 初始化信号检测器...")
        detector = get_signal_detector()
        logger.info("✅ 信号检测器初始化成功")

        # 测试获取数据
        logger.info("步骤 3: 获取行情数据...")
        symbols = ['BTCUSDT', 'ETHUSDT', 'BNBUSDT']
        data_fetcher = get_data_fetcher()
        market_data = data_fetcher.fetch_market_data(symbols)
        logger.info(f"✅ 获取到 {len(market_data)} 个交易对的数据")

        # 测试信号检测
        logger.info("步骤 4: 检测交易信号...")
        signals = detector.detect_signals(symbols)
        logger.info(f"✅ 检测到 {len(signals)} 个有效信号")

        # 打印信号详情
        for signal in signals:
            logger.info(f"\n信号: {signal['币种']} {signal['开仓方向']}")
            logger.info(f"  等级: {signal['信号等级']}")
            logger.info(f"  推荐度: {signal['开仓推荐度']}")
            logger.info(f"  开仓价: {signal['开仓价']}")
            logger.info(f"  止损价: {signal['止损价']}")
            logger.info(f"  止盈设置: {signal['止盈设置']}")
            logger.info(f"  保证金: {signal['保证金']}")
            logger.info(f"  风险占比: {signal['风险占比']}")

        logger.info("\n" + "=" * 60)
        logger.info("✅ 信号检测测试成功！")
        logger.info("=" * 60)
        return True

    except Exception as e:
        logger.error(f"\n❌ 测试失败: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return False


if __name__ == '__main__':
    success = test_signal_detection()
    sys.exit(0 if success else 1)
