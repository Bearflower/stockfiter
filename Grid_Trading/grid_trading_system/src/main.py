"""
网格交易系统 - 统一入口文件
支持两种运行模式：auto（全自动）和 signal（信号灯）
提供命令行参数控制、优雅退出处理、系统启动日志和报警
"""

import argparse
import asyncio
import logging
import signal
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

# 添加项目根目录到 Python 路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.core.config.config_loader import ConfigLoader, load_config

logger = logging.getLogger(__name__)


class GridTradingApp:
    """网格交易系统主应用类
    
    功能：
    - 支持 auto（全自动）和 signal（信号灯）两种模式
    - 命令行参数控制（--mode, --config）
    - 优雅退出处理（SIGINT/SIGTERM）
    - 系统启动日志和报警
    """
    
    def __init__(self, config_path: Optional[str] = None, mode: Optional[str] = None):
        """
        初始化应用
        
        Args:
            config_path: 配置文件路径
            mode: 运行模式（auto 或 signal），优先使用命令行参数
        """
        # 加载配置
        self.config: ConfigLoader = load_config(config_path)
        self.config_path = config_path or self.config.config_path
        
        # 确定运行模式：命令行 > 配置文件 > 默认值
        self.mode = mode or self.config.mode
        if self.mode not in ('auto', 'signal'):
            raise ValueError(f"不支持的运行模式：{mode}，请使用 'auto' 或 'signal'")
        
        # 状态
        self._shutdown_event = asyncio.Event()
        self._is_running = False
        
        # 设置日志
        self._setup_logging()
        
        logger.info("=" * 60)
        logger.info("网格交易系统初始化")
        logger.info("=" * 60)
        logger.info(f"配置文件：{self.config_path}")
        logger.info(f"运行模式：{self.mode}")
        logger.info(f"系统版本：{self.config.get('system.version', '1.0.0')}")
    
    def _setup_logging(self) -> None:
        """配置日志系统"""
        log_config = self.config.get_logging_config()
        log_level = log_config.get('level', 'INFO')
        log_file = log_config.get('file')
        log_format = log_config.get(
            'format',
            '%(asctime)s | %(levelname)-8s | %(name)s | %(message)s'
        )
        
        # 配置根日志
        log_handlers = [logging.StreamHandler(sys.stdout)]
        
        # 如果配置了日志文件，添加文件处理器
        if log_file:
            log_path = Path(log_file)
            log_path.parent.mkdir(parents=True, exist_ok=True)
            file_handler = logging.FileHandler(log_path, encoding='utf-8')
            log_handlers.append(file_handler)
        
        # 设置日志格式
        formatter = logging.Formatter(log_format)
        for handler in log_handlers:
            handler.setFormatter(formatter)
        
        # 配置根 logger
        root_logger = logging.getLogger()
        root_logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))
        for handler in root_logger.handlers[:]:
            root_logger.removeHandler(handler)
        for handler in log_handlers:
            root_logger.addHandler(handler)
    
    def _register_signal_handlers(self) -> None:
        """注册系统信号处理（SIGINT/SIGTERM）"""
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, lambda s=sig: asyncio.create_task(self._handle_shutdown(s)))
    
    async def _handle_shutdown(self, sig: signal.Signals) -> None:
        """
        处理系统关闭信号
        
        Args:
            sig: 信号类型
        """
        sig_name = signal.Signals(sig).name
        logger.info(f"收到关闭信号：{sig_name}，开始优雅退出...")
        self._shutdown_event.set()
    
    async def run(self) -> None:
        """启动系统"""
        logger.info("=" * 60)
        logger.info("启动网格交易系统")
        logger.info("=" * 60)
        
        # 打印配置摘要
        self.config.print_config_summary()
        
        # 检查配置有效性
        if not self.config.is_valid():
            errors = self.config.get_validation_errors()
            logger.warning("配置验证存在问题，但继续运行：")
            for error in errors:
                logger.warning(f"  - {error}")
        
        # 注册信号处理
        self._register_signal_handlers()
        
        self._is_running = True
        
        # 发送启动报警
        await self._send_startup_alert()
        
        # 根据模式启动不同执行模块
        try:
            if self.mode == 'auto':
                await self._run_auto_mode()
            elif self.mode == 'signal':
                await self._run_signal_mode()
            else:
                logger.error(f"不支持的运行模式：{self.mode}")
                sys.exit(1)
        except KeyboardInterrupt:
            logger.info("收到键盘中断信号")
        except Exception as e:
            logger.error(f"系统运行异常：{e}", exc_info=True)
            await self._send_error_alert(str(e))
        finally:
            await self._cleanup()
    
    async def _run_auto_mode(self) -> None:
        """运行全自动模式
        
        全自动模式：自动执行网格交易，包括市场分析、网格计算、订单执行等
        """
        logger.info("=" * 60)
        logger.info("全自动模式启动")
        logger.info("=" * 60)
        
        # 延迟导入执行模块
        from src.execution.auto_mode.grid_executor import GridExecutor
        from src.execution.auto_mode.grid_manager import GridManager
        from src.execution.auto_mode.scheduler import TaskScheduler as Scheduler
        from src.core.data.binance_client import BinanceClient
        
        try:
            # 初始化核心组件
            exchange_config = self.config.get_exchange_config()
            strategy_config = self.config.get_strategy_config()
            execution_config = self.config.get_execution_config()
            
            logger.info("初始化全自动模式组件...")
            
            # 初始化币安客户端
            binance_client = BinanceClient(
                api_key=exchange_config.get('api_key'),
                api_secret=exchange_config.get('api_secret'),
                testnet=exchange_config.get('testnet', True),
            )
            
            # 初始化调度器
            scheduler = Scheduler(
                inspection_interval=execution_config.get('inspection_interval', 3600),
                atr_change_threshold=execution_config.get('atr_change_threshold', 0.2),
                min_adjustment_interval=execution_config.get('min_adjustment_interval', 14400),
                max_adjustments_per_day=execution_config.get('max_adjustments_per_day', 6),
            )
            await scheduler.start()
            
            # 初始化网格管理器
            grid_manager = GridManager(self.config)
            
            # 初始化网格执行器
            grid_executor = GridExecutor(client=binance_client)
            
            logger.info("所有组件初始化完成，开始执行...")
            
            # Scheduler 内部已自动启动巡检循环，无需手动注册任务
            
            logger.info("全自动模式已启动，开始自动运行")
            
            # 等待关闭信号
            await self._shutdown_event.wait()
            
        except ImportError as e:
            logger.error(f"全自动模式模块导入失败：{e}")
            logger.error("请确保 src/execution/auto_mode/ 目录下有必要的模块")
            raise
        except Exception as e:
            logger.error(f"全自动模式运行异常：{e}", exc_info=True)
            raise
    
    async def _run_signal_mode(self) -> None:
        """运行信号灯模式
        
        信号灯模式：分析市场生成信号，推送通知，不自动执行交易
        只需要获取K线数据（公开接口），不需要API密钥
        """
        logger.info("=" * 60)
        logger.info("信号灯模式启动")
        logger.info("=" * 60)
        
        # 延迟导入执行模块
        from src.execution.signal_mode.signal_generator import SignalGenerator
        from src.execution.signal_mode.signal_manager import SignalManager
        
        try:
            # 初始化核心组件
            services_config = self.config.get_services_config()
            
            logger.info("初始化信号灯模式组件...")
            
            # 初始化信号生成器
            signal_generator = SignalGenerator(config_loader=self.config)
            
            # 获取通用服务配置
            kline_service_url = self.config.get('services.kline.url', 'http://43.156.242.184:8765/api/v1')
            notification_service_url = self.config.get('services.notification.url', 'http://43.156.242.184:8766/api/v1')
            project_name = self.config.get('services.notification.project', 'grid')
            
            logger.info(f"K线服务：{kline_service_url}")
            logger.info(f"通知服务：{notification_service_url}")
            logger.info(f"项目标识：{project_name}")
            
            # 设置K线数据回调（使用通用K线服务）
            async def kline_callback(symbol: str, interval: str, limit: int = 100):
                import aiohttp
                try:
                    async with aiohttp.ClientSession() as session:
                        async with session.get(
                            f"{kline_service_url}/klines/latest",
                            params={"symbol": symbol, "interval": interval, "limit": limit},
                            timeout=aiohttp.ClientTimeout(total=10)
                        ) as response:
                            if response.status == 200:
                                result = await response.json()
                                if result.get('code') == 0:
                                    klines = result['data']  # data直接就是列表
                                    # 转换为信号生成器需要的格式
                                    return [{
                                        'close_price': float(k['close_price']),
                                        'high_price': float(k['high_price']),
                                        'low_price': float(k['low_price']),
                                        'open_price': float(k['open_price']),
                                        'volume': float(k['volume']),
                                        'atr': float(k['close_price']) * 0.01  # 简单估算ATR
                                    } for k in klines]
                                else:
                                    logger.error(f"K线服务返回错误：{result.get('message')}")
                                    return []
                            else:
                                logger.error(f"K线服务HTTP错误：{response.status}")
                                return []
                except Exception as e:
                    logger.error(f"获取K线数据异常：{e}")
                    return []
            
            signal_generator.set_kline_callback(kline_callback)
            
            # 初始化信号管理器
            db_config = self.config.get_database_config()
            signal_manager = SignalManager(db_config)
            
            logger.info("所有组件初始化完成，开始生成信号...")
            
            # 获取信号灯模式配置
            run_minute = self.config.get('signal.run_minute', 35)
            rest_hours = self.config.get('signal.rest_hours', [0, 1, 2, 3, 4, 5])
            
            logger.info(f"信号灯运行配置：每小时第 {run_minute} 分钟运行")
            logger.info(f"休息时间段：{rest_hours}")
            
            # 定时运行循环
            while self._is_running and not self._shutdown_event.is_set():
                try:
                    # 计算到下一个运行时间的等待秒数
                    now = datetime.now()
                    next_run = now.replace(minute=run_minute, second=0, microsecond=0)
                    
                    # 如果当前时间已过本小时的运行时间，则设置为下一小时
                    if now.minute >= run_minute:
                        next_run = next_run.replace(hour=now.hour + 1)
                    
                    # 如果小时超过23，则设置为第二天0点
                    if next_run.hour >= 24:
                        next_run = next_run.replace(hour=0, day=now.day + 1)
                    
                    # 检查是否在休息时间段内，如果是则跳到下一个非休息时间
                    while next_run.hour in rest_hours:
                        next_run = next_run.replace(hour=next_run.hour + 1)
                        if next_run.hour >= 24:
                            next_run = next_run.replace(hour=0, day=next_run.day + 1)
                    
                    wait_seconds = (next_run - now).total_seconds()
                    
                    if wait_seconds > 0:
                        logger.info(f"下次运行时间：{next_run.strftime('%Y-%m-%d %H:%M:%S')}")
                        logger.info(f"等待 {int(wait_seconds // 60)} 分 {int(wait_seconds % 60)} 秒...")
                        
                        # 使用 wait_for 支持中断
                        try:
                            await asyncio.wait_for(
                                self._shutdown_event.wait(),
                                timeout=wait_seconds
                            )
                            break  # 收到关闭信号
                        except asyncio.TimeoutError:
                            pass  # 等待超时，继续执行
                    
                    # 执行一次信号生成
                    if not self._shutdown_event.is_set():
                        await self._run_signal_once(signal_generator, signal_manager)
                    
                except Exception as e:
                    logger.error(f"信号灯循环运行异常：{e}", exc_info=True)
                    # 等待一段时间后重试
                    await asyncio.sleep(300)
            
        except ImportError as e:
            logger.error(f"信号灯模式模块导入失败：{e}")
            logger.error("请确保 src/execution/signal_mode/ 目录下有必要的模块")
            raise
        except Exception as e:
            logger.error(f"信号灯模式运行异常：{e}", exc_info=True)
            raise
    
    async def _run_signal_once(self, signal_generator: 'SignalGenerator', signal_manager: 'SignalManager') -> None:
        """
        执行一次信号灯分析
        
        Args:
            signal_generator: 信号生成器
            signal_manager: 信号管理器
        """
        logger.info("=" * 60)
        logger.info("开始信号灯分析")
        logger.info("=" * 60)
        
        try:
            # 获取交易对
            symbol = self.config.get('exchange.symbol', 'BTCUSDT')
            
            # 生成信号（SignalGenerator内部已处理通知发送）
            signal_result = await signal_generator.generate_signal(symbol)
            
            if signal_result:
                logger.info(f"生成信号：{signal_result}")
                
                # 保存信号到管理器
                signal_manager.add_signal(signal_result.to_dict())
            else:
                logger.info("无有效信号生成")
            
            logger.info("信号灯分析完成")
            
        except Exception as e:
            logger.error(f"信号灯分析失败：{e}", exc_info=True)
            await self._send_error_alert(f"信号灯分析失败：{e}")
    
    async def _send_startup_alert(self) -> None:
        """发送系统启动报警"""
        alert_config = self.config.get('monitoring.alert', {})
        if not alert_config.get('enabled', False):
            return
        
        try:
            # 延迟导入通知模块
            from src.core.monitoring.notifier import Notifier
            
            notifier = Notifier(alert_config)
            startup_msg = (
                f"系统启动成功\n"
                f"模式：{self.mode}\n"
                f"版本：{self.config.get('system.version', '1.0.0')}\n"
                f"时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            )
            await notifier.send_alert(title="网格交易系统启动", message=startup_msg, level="info")
            logger.info("启动报警已发送")
        except ImportError:
            logger.debug("通知模块未安装，跳过启动报警")
        except Exception as e:
            logger.error(f"发送启动报警失败：{e}")
    
    async def _send_error_alert(self, error_message: str) -> None:
        """
        发送错误报警
        
        Args:
            error_message: 错误信息
        """
        alert_config = self.config.get('monitoring.alert', {})
        if not alert_config.get('enabled', False):
            return
        
        try:
            from src.core.monitoring.notifier import Notifier
            
            notifier = Notifier(alert_config)
            error_msg = (
                f"系统运行异常\n"
                f"模式：{self.mode}\n"
                f"错误：{error_message}\n"
                f"时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            )
            await notifier.send_alert(title="网格交易系统异常", message=error_msg, level="error")
        except Exception as e:
            logger.error(f"发送错误报警失败：{e}")
    
    async def _cleanup(self) -> None:
        """清理资源"""
        logger.info("=" * 60)
        logger.info("系统清理")
        logger.info("=" * 60)
        
        self._is_running = False
        
        # 发送关闭报警
        alert_config = self.config.get('monitoring.alert', {})
        if alert_config.get('enabled', False):
            try:
                from src.core.monitoring.notifier import Notifier
                notifier = Notifier(alert_config)
                shutdown_msg = (
                    f"系统已关闭\n"
                    f"模式：{self.mode}\n"
                    f"时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                )
                await notifier.send_alert(title="网格交易系统关闭", message=shutdown_msg, level="info")
            except Exception as e:
                logger.error(f"发送关闭报警失败：{e}")
        
        logger.info("系统已清理")


def parse_args() -> argparse.Namespace:
    """
    解析命令行参数
    
    Returns:
        解析后的参数
    """
    parser = argparse.ArgumentParser(
        description="网格交易系统 - 支持全自动和信号灯两种模式",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用示例：
  # 使用默认配置启动（默认模式由配置文件决定）
  python -m src.main

  # 指定配置文件启动
  python -m src.main --config /path/to/config.yaml

  # 强制使用全自动模式
  python -m src.main --mode auto

  # 强制使用信号灯模式
  python -m src.main --mode signal

  # 生成默认配置文件
  python -m src.main --init-config
        """
    )
    
    parser.add_argument(
        '--mode', '-m',
        type=str,
        choices=['auto', 'signal'],
        default=None,
        help='运行模式：auto（全自动）或 signal（信号灯），覆盖配置文件中的设置'
    )
    
    parser.add_argument(
        '--config', '-c',
        type=str,
        default=None,
        help='配置文件路径（默认：config/config.yaml 或环境变量 CONFIG_PATH）'
    )
    
    parser.add_argument(
        '--init-config',
        action='store_true',
        help='生成默认配置文件并退出'
    )
    
    return parser.parse_args()


async def async_main() -> None:
    """异步主函数"""
    args = parse_args()
    
    # 处理初始化配置
    if args.init_config:
        from src.core.config.config_loader import create_default_config
        config_path = args.config or 'config/config.yaml'
        if create_default_config(config_path):
            print(f"默认配置文件已生成：{config_path}")
        else:
            print("配置文件生成失败")
        sys.exit(0)
    
    # 创建并运行应用
    app = GridTradingApp(
        config_path=args.config,
        mode=args.mode,
    )
    
    await app.run()


def main() -> None:
    """同步主函数（入口点）"""
    try:
        asyncio.run(async_main())
    except KeyboardInterrupt:
        logger.info("收到键盘中断，系统退出")
    except Exception as e:
        logger.error(f"系统启动失败：{e}", exc_info=True)
        sys.exit(1)


if __name__ == '__main__':
    main()
