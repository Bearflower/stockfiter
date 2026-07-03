"""
网格交易策略主入口
负责加载配置、初始化客户端、执行策略逻辑
"""
import asyncio
import os
from datetime import datetime
import yaml
import structlog

from shared.binance_api import BinanceClient
from shared.database import DatabaseManager
from shared.kline_service import KLineService
from shared.notification import NotificationClient
from shared.trade_logger import TradeLogger
from shared.utils import setup_logging
from strategies.grid.strategy import GridStrategy


logger = structlog.get_logger()


async def main():
    """
    主函数
    执行网格交易策略的完整流程
    """
    # 配置日志
    log_level = os.getenv("LOG_LEVEL", "INFO")
    log_format = os.getenv("LOG_FORMAT", "json")
    setup_logging(level=log_level, format=log_format)

    logger.info("网格交易策略启动", timestamp=datetime.now().isoformat())

    strategy = None

    try:
        # 加载配置
        config_path = os.path.join(
            os.path.dirname(__file__),
            "config.yaml"
        )

        with open(config_path, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)

        logger.info("配置加载成功", config_path=config_path)

        # 验证必要的环境变量
        required_env_vars = [
            "BINANCE_API_KEY",
            "BINANCE_API_SECRET",
            "KLINE_SERVICE_URL",
            "NOTIFICATION_SERVICE_URL"
        ]

        missing_vars = [var for var in required_env_vars if not os.getenv(var)]
        if missing_vars:
            raise ValueError(f"缺少必要的环境变量: {', '.join(missing_vars)}")

        # 初始化客户端
        binance_client = BinanceClient(
            api_key=os.getenv("BINANCE_API_KEY"),
            api_secret=os.getenv("BINANCE_API_SECRET"),
            testnet=os.getenv("BINANCE_TESTNET", "false").lower() == "true"
        )

        kline_service = KLineService(
            service_url=os.getenv("KLINE_SERVICE_URL"),
            timeout=int(os.getenv("KLINE_SERVICE_TIMEOUT", "10"))
        )

        notification_client = NotificationClient(
            service_url=os.getenv("NOTIFICATION_SERVICE_URL"),
            timeout=int(os.getenv("NOTIFICATION_SERVICE_TIMEOUT", "10"))
        )

        # 数据库客户端（可选）
        db_client = None
        if os.getenv("DATABASE_HOST"):
            db_client = DatabaseManager(
                host=os.getenv("DATABASE_HOST"),
                port=int(os.getenv("DATABASE_PORT", "5432")),
                database=os.getenv("DATABASE_NAME", "trading"),
                user=os.getenv("DATABASE_USER"),
                password=os.getenv("DATABASE_PASSWORD")
            )
            await db_client.connect()
            logger.info("数据库连接成功")
            
            # 初始化交易记录器（自动记录所有下单到 trading.trade_records）
            trade_logger = TradeLogger(db_client, "网格交易策略")
            await trade_logger.ensure_table_exists()
            binance_client.set_trade_logger(trade_logger)
            logger.info("交易记录器初始化完成", strategy="网格交易策略")

        logger.info("客户端初始化完成")

        # 创建策略实例
        strategy = GridStrategy(config=config)

        # 设置客户端
        await strategy.set_binance_client(binance_client)
        await strategy.set_kline_service(kline_service)
        await strategy.set_notification_client(notification_client)
        if db_client:
            await strategy.set_database(db_client)

        # 初始化策略
        await strategy.initialize()

        # 发送启动通知
        if notification_client:
            await notification_client.send(
                message=f"网格交易策略已启动\n交易对: {', '.join(config.get('symbols', []))}",
                level="info",
                project="grid"
            )

        # 运行策略
        await strategy.run()

    except KeyboardInterrupt:
        logger.info("接收到停止信号，策略即将停止")

    except Exception as e:
        logger.error(
            "策略执行失败",
            error=str(e),
            exc_info=True
        )

        # 发送错误通知
        try:
            if 'notification_client' in locals() and notification_client:
                await notification_client.send_alert(
                    title="网格交易策略执行失败",
                    message=f"错误信息: {str(e)}",
                    level="error"
                )
        except Exception as notify_error:
            logger.error(
                "发送错误通知失败",
                error=str(notify_error)
            )

        raise

    finally:
        # 停止策略
        if strategy:
            try:
                await strategy.stop()
            except Exception as e:
                logger.error(
                    "停止策略失败",
                    error=str(e),
                    exc_info=True
                )

        logger.info("网格交易策略结束", timestamp=datetime.now().isoformat())


if __name__ == "__main__":
    asyncio.run(main())
