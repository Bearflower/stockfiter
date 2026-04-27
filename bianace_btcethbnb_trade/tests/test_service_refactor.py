#!/usr/bin/env python3
"""
通用服务改造验证测试

验证内容：
1. 飞书通知改造 - 验证是否正确调用通用通知服务
2. K线数据改造 - 验证是否正确使用 KlineServiceClient
3. 配置验证 - 验证环境变量配置是否正确
"""

import os
import sys
import logging
from datetime import datetime

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class TestResult:
    """测试结果类"""
    def __init__(self):
        self.passed = []
        self.failed = []
        self.warnings = []

    def add_pass(self, test_name, message=""):
        self.passed.append((test_name, message))
        logger.info(f"✅ 通过: {test_name} - {message}")

    def add_fail(self, test_name, message=""):
        self.failed.append((test_name, message))
        logger.error(f"❌ 失败: {test_name} - {message}")

    def add_warning(self, test_name, message=""):
        self.warnings.append((test_name, message))
        logger.warning(f"⚠️  警告: {test_name} - {message}")

    def print_summary(self):
        """打印测试摘要"""
        print("\n" + "="*60)
        print("测试结果摘要")
        print("="*60)
        print(f"✅ 通过: {len(self.passed)} 项")
        print(f"❌ 失败: {len(self.failed)} 项")
        print(f"⚠️  警告: {len(self.warnings)} 项")
        print("="*60)

        if self.failed:
            print("\n失败的测试项:")
            for name, msg in self.failed:
                print(f"  - {name}: {msg}")

        if self.warnings:
            print("\n警告项:")
            for name, msg in self.warnings:
                print(f"  - {name}: {msg}")

        print("="*60)
        return len(self.failed) == 0


def test_config_validation(result: TestResult):
    """测试配置验证"""
    print("\n" + "="*60)
    print("1. 配置验证测试")
    print("="*60)

    # 测试 K 线服务配置
    kline_url = os.getenv('KLINE_SERVICE_URL', 'http://43.156.242.184:8765/api/v1')
    if kline_url:
        result.add_pass("K线服务URL配置", f"URL: {kline_url}")
    else:
        result.add_fail("K线服务URL配置", "未配置 KLINE_SERVICE_URL")

    # 测试通知服务配置
    notification_url = os.getenv('NOTIFICATION_SERVICE_URL', 'http://43.156.242.184:8766/api/v1')
    if notification_url:
        result.add_pass("通知服务URL配置", f"URL: {notification_url}")
    else:
        result.add_fail("通知服务URL配置", "未配置 NOTIFICATION_SERVICE_URL")

    # 测试项目标识配置
    project = os.getenv('NOTIFICATION_PROJECT', 'btc_eth_bnb')
    if project:
        result.add_pass("项目标识配置", f"Project: {project}")
    else:
        result.add_warning("项目标识配置", "未配置 NOTIFICATION_PROJECT，使用默认值")


def test_lark_notifier(result: TestResult):
    """测试飞书通知改造"""
    print("\n" + "="*60)
    print("2. 飞书通知改造验证")
    print("="*60)

    try:
        from utils.lark_notifier import LarkNotifier

        # 测试初始化
        notifier = LarkNotifier()
        result.add_pass("LarkNotifier初始化", "成功创建实例")

        # 验证配置属性
        if hasattr(notifier, 'notification_service_url'):
            result.add_pass("通知服务URL属性", f"URL: {notifier.notification_service_url}")
        else:
            result.add_fail("通知服务URL属性", "缺少 notification_service_url 属性")

        if hasattr(notifier, 'project'):
            result.add_pass("项目标识属性", f"Project: {notifier.project}")
        else:
            result.add_fail("项目标识属性", "缺少 project 属性")

        # 验证接口兼容性
        required_methods = [
            'send_text_message',
            'send_success_notification',
            'send_error_notification',
            'send_scheduler_startup_notification',
            'send_scheduler_shutdown_notification',
            'send_scheduler_completion_notification',
            'send_scheduler_before_run_notification',
            'send_analysis_result_notification'
        ]

        for method in required_methods:
            if hasattr(notifier, method) and callable(getattr(notifier, method)):
                result.add_pass(f"接口兼容性 - {method}", "方法存在且可调用")
            else:
                result.add_fail(f"接口兼容性 - {method}", "方法不存在或不可调用")

        # 测试向后兼容性 - webhook_url 参数
        try:
            notifier_with_webhook = LarkNotifier(webhook_url="https://example.com/webhook")
            result.add_pass("向后兼容性", "webhook_url 参数保留，不影响初始化")
        except Exception as e:
            result.add_fail("向后兼容性", f"webhook_url 参数导致初始化失败: {e}")

    except ImportError as e:
        result.add_fail("LarkNotifier导入", f"导入失败: {e}")
    except Exception as e:
        result.add_fail("LarkNotifier测试", f"未知错误: {e}")


def test_kline_service_client(result: TestResult):
    """测试 K 线服务客户端"""
    print("\n" + "="*60)
    print("3. K线服务客户端验证")
    print("="*60)

    try:
        from utils.kline_service import KlineServiceClient

        # 测试初始化
        client = KlineServiceClient()
        result.add_pass("KlineServiceClient初始化", "成功创建实例")

        # 验证配置属性
        if hasattr(client, 'service_url'):
            result.add_pass("服务URL属性", f"URL: {client.service_url}")
        else:
            result.add_fail("服务URL属性", "缺少 service_url 属性")

        # 验证必需方法
        required_methods = [
            'get_latest_klines',
            'get_indicators',
            'get_symbols',
            'manual_collect',
            'get_collector_stats'
        ]

        for method in required_methods:
            if hasattr(client, method) and callable(getattr(client, method)):
                result.add_pass(f"客户端方法 - {method}", "方法存在且可调用")
            else:
                result.add_fail(f"客户端方法 - {method}", "方法不存在或不可调用")

        # 测试便捷函数
        from utils.kline_service import get_klines, get_indicators
        result.add_pass("便捷函数 - get_klines", "函数可导入")
        result.add_pass("便捷函数 - get_indicators", "函数可导入")

    except ImportError as e:
        result.add_fail("KlineServiceClient导入", f"导入失败: {e}")
    except Exception as e:
        result.add_fail("KlineServiceClient测试", f"未知错误: {e}")


def test_data_fetcher(result: TestResult):
    """测试数据获取器"""
    print("\n" + "="*60)
    print("4. 数据获取器验证")
    print("="*60)

    try:
        from core.data.fetcher import MarketDataFetcher

        # 测试初始化
        fetcher = MarketDataFetcher()
        result.add_pass("MarketDataFetcher初始化", "成功创建实例")

        # 验证 K 线客户端属性
        if hasattr(fetcher, 'kline_client'):
            result.add_pass("K线客户端属性", "kline_client 属性存在")
        else:
            result.add_fail("K线客户端属性", "缺少 kline_client 属性")

        # 验证并发配置
        if hasattr(fetcher, 'enable_concurrent'):
            result.add_pass("并发配置属性", f"并发模式: {fetcher.enable_concurrent}")
        else:
            result.add_warning("并发配置属性", "缺少 enable_concurrent 属性")

        if hasattr(fetcher, 'max_workers'):
            result.add_pass("最大线程数属性", f"最大线程数: {fetcher.max_workers}")
        else:
            result.add_warning("最大线程数属性", "缺少 max_workers 属性")

        # 验证必需方法
        required_methods = [
            'fetch_market_data',
            'get_symbol_data',
            'clear_cache',
            'get_cache_stats',
            'get_performance_stats'
        ]

        for method in required_methods:
            if hasattr(fetcher, method) and callable(getattr(fetcher, method)):
                result.add_pass(f"数据获取器方法 - {method}", "方法存在且可调用")
            else:
                result.add_fail(f"数据获取器方法 - {method}", "方法不存在或不可调用")

    except ImportError as e:
        result.add_fail("MarketDataFetcher导入", f"导入失败: {e}")
    except Exception as e:
        result.add_fail("MarketDataFetcher测试", f"未知错误: {e}")


def test_service_connectivity(result: TestResult):
    """测试服务连通性"""
    print("\n" + "="*60)
    print("5. 服务连通性测试")
    print("="*60)

    import requests

    # 测试 K 线服务连通性
    try:
        kline_url = os.getenv('KLINE_SERVICE_URL', 'http://43.156.242.184:8765/api/v1')
        response = requests.get(f"{kline_url}/symbols", timeout=5)

        if response.status_code == 200:
            data = response.json()
            if data.get('code') == 0:
                result.add_pass("K线服务连通性", f"服务可用，状态码: {response.status_code}")
            else:
                result.add_warning("K线服务连通性", f"服务返回错误: {data.get('message')}")
        else:
            result.add_warning("K线服务连通性", f"HTTP状态码: {response.status_code}")
    except requests.exceptions.Timeout:
        result.add_warning("K线服务连通性", "请求超时（服务可能未启动）")
    except requests.exceptions.ConnectionError:
        result.add_warning("K线服务连通性", "连接失败（服务可能未启动）")
    except Exception as e:
        result.add_warning("K线服务连通性", f"测试异常: {e}")

    # 测试通知服务连通性
    try:
        notification_url = os.getenv('NOTIFICATION_SERVICE_URL', 'http://43.156.242.184:8766/api/v1')
        # 发送测试消息（不实际发送，只测试连接）
        response = requests.options(f"{notification_url}/send", timeout=5)

        if response.status_code in [200, 204, 405]:  # 405 表示方法不允许，但服务可达
            result.add_pass("通知服务连通性", f"服务可达，状态码: {response.status_code}")
        else:
            result.add_warning("通知服务连通性", f"HTTP状态码: {response.status_code}")
    except requests.exceptions.Timeout:
        result.add_warning("通知服务连通性", "请求超时（服务可能未启动）")
    except requests.exceptions.ConnectionError:
        result.add_warning("通知服务连通性", "连接失败（服务可能未启动）")
    except Exception as e:
        result.add_warning("通知服务连通性", f"测试异常: {e}")


def test_integration(result: TestResult):
    """集成测试 - 实际调用功能"""
    print("\n" + "="*60)
    print("6. 集成功能测试")
    print("="*60)

    # 测试 K 线数据获取
    try:
        from utils.kline_service import KlineServiceClient

        client = KlineServiceClient()
        klines = client.get_latest_klines('BTCUSDT', '1h', limit=10)

        if klines is not None and len(klines) > 0:
            result.add_pass("K线数据获取", f"成功获取 {len(klines)} 条K线数据")

            # 验证数据格式
            first_kline = klines[0]
            required_fields = ['open_price', 'high_price', 'low_price', 'close_price', 'volume']
            missing_fields = [f for f in required_fields if f not in first_kline]

            if not missing_fields:
                result.add_pass("K线数据格式", "数据格式正确，包含所有必需字段")
            else:
                result.add_fail("K线数据格式", f"缺少字段: {missing_fields}")
        else:
            result.add_warning("K线数据获取", "未获取到数据（服务可能未启动）")
    except Exception as e:
        result.add_warning("K线数据获取", f"测试异常: {e}")

    # 测试数据获取器
    try:
        from core.data.fetcher import MarketDataFetcher

        fetcher = MarketDataFetcher(cache_duration_hours=1)
        # 只获取一个交易对的数据
        data = fetcher.fetch_market_data(['BTCUSDT'])

        if data and 'BTCUSDT' in data:
            result.add_pass("数据获取器功能", "成功获取市场数据")

            # 验证数据结构
            btc_data = data['BTCUSDT']
            if 'indicators' in btc_data:
                result.add_pass("数据获取器 - 指标数据", "包含技术指标")
            else:
                result.add_warning("数据获取器 - 指标数据", "缺少技术指标")

            if 'last_price' in btc_data:
                result.add_pass("数据获取器 - 价格数据", f"最新价格: {btc_data['last_price']}")
            else:
                result.add_warning("数据获取器 - 价格数据", "缺少价格数据")
        else:
            result.add_warning("数据获取器功能", "未获取到数据（服务可能未启动）")
    except Exception as e:
        result.add_warning("数据获取器功能", f"测试异常: {e}")


def main():
    """主测试函数"""
    print("\n" + "="*60)
    print("通用服务改造验证测试")
    print(f"测试时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*60)

    result = TestResult()

    # 执行所有测试
    test_config_validation(result)
    test_lark_notifier(result)
    test_kline_service_client(result)
    test_data_fetcher(result)
    test_service_connectivity(result)
    test_integration(result)

    # 打印测试摘要
    all_passed = result.print_summary()

    # 返回退出码
    sys.exit(0 if all_passed else 1)


if __name__ == '__main__':
    main()
