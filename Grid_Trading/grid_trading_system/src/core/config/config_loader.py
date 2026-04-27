"""
统一配置加载器
整合 YAML 配置解析、环境变量覆盖、配置验证、默认配置、热更新等功能
"""

import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

logger = logging.getLogger(__name__)


class ConfigValidationError(Exception):
    """配置验证异常"""
    pass


class ConfigLoader:
    """统一配置加载器
    
    功能：
    - YAML 配置文件解析
    - 环境变量覆盖（${ENV_VAR} 语法）
    - 配置验证（必填字段、类型检查）
    - 默认配置支持
    - 运行时配置热更新
    """
    
    # 默认配置模板
    DEFAULT_CONFIG = {
        'system': {
            'mode': 'auto',
            'name': 'grid_trading_system',
            'version': '1.0.0',
        },
        'exchange': {
            'api_key': '${BINANCE_API_KEY}',
            'api_secret': '${BINANCE_API_SECRET}',
            'testnet': False,
            'symbol': 'BTCUSDT',
            'contract_type': 'PERPETUAL',
        },
        'strategy': {
            'indicators': {
                'adx_period': 14,
                'adx_trend_threshold': 25,
                'adx_weak_threshold': 20,
                'adx_strong_threshold': 40,
                'ema_fast_period': 20,
                'ema_slow_period': 50,
                'atr_period': 14,
                'atr_smoothing': 14,
            },
            'grid': {
                'base_grid_count': 30,
                'min_grid_count': 20,
                'max_grid_count': 50,
                'base_atr_window': 90,
                'leverage': 10,
                'total_investment': 500,
                'min_profit_rate': 0.01,
            },
            'risk': {
                'hard_stop_loss': -0.08,
                'trailing_profit_start': 0.15,
                'trailing_profit_retrace': 0.5,
                'emergency_break_layers': 3,
                'emergency_break_window': 300,
                'position_coefficient': 0.5,
            },
        },
        'execution': {
            'inspection_interval': 3600,
            'atr_change_threshold': 0.2,
            'parameter_adjustment': {
                'enabled': True,
                'min_interval': 14400,
                'max_adjustments_per_day': 6,
            },
            'slippage_protection': {
                'limit_order_timeout': 3,
                'optimal_price_timeout': 2,
                'market_order_fallback': True,
            },
        },
        # 触发条件配置（与 grid_signal_bot_v2 保持一致）
        'triggers': {
            'grid_width_change': 0.05,
            'grid_count_change': 0.10,
            'atr_change': 0.20,
            'profit_rate_warning': 0.012,
        },
        'services': {
            'kline': {
                'url': 'http://localhost:8001',
                'timeout': 10,
            },
            'notification': {
                'url': 'http://localhost:8002',
                'project': 'grid',
                'timeout': 10,
            },
        },
        'database': {
            'type': 'sqlite',
            'path': 'data/grid_trading.db',
        },
        'monitoring': {
            'logging': {
                'level': 'INFO',
                'file': 'logs/grid_trading.log',
                'max_size': 10485760,
                'backup_count': 5,
                'format': '%(asctime)s | %(levelname)-8s | %(name)s | %(message)s',
            },
            'alert': {
                'enabled': True,
                'alert_on_parameter_adjustment': True,
                'feishu_webhook': '${FEISHU_WEBHOOK}',
                'dingding_webhook': '${DINGDING_WEBHOOK}',
                'telegram_bot_token': '${TELEGRAM_BOT_TOKEN}',
                'telegram_chat_id': '${TELEGRAM_CHAT_ID}',
            },
            'metrics': {
                'enabled': True,
                'report_interval': 3600,
            },
        },
    }
    
    # 必填配置字段列表
    # 信号灯模式不需要API密钥，只需要交易对和策略参数
    REQUIRED_KEYS = [
        'exchange.symbol',
        'strategy.indicators.adx_period',
        'strategy.grid.base_grid_count',
        'strategy.risk.hard_stop_loss',
        'execution.inspection_interval',
    ]
    
    def __init__(self, config_path: Optional[str] = None):
        """
        初始化配置加载器
        
        Args:
            config_path: 配置文件路径，优先使用参数，其次使用环境变量 CONFIG_PATH
        """
        # 确定配置文件路径
        if config_path:
            self.config_path = config_path
        else:
            self.config_path = os.getenv('CONFIG_PATH', str(
                Path(__file__).parent.parent.parent.parent / 'config' / 'config.yaml'
            ))
        
        self._config: Dict[str, Any] = {}
        self._last_modified: Optional[datetime] = None
        self._validation_errors: List[str] = []
    
    def _load_env_file(self, env_file: str) -> Dict[str, str]:
        """
        加载 .env 文件
        
        Args:
            env_file: .env 文件路径
            
        Returns:
            环境变量字典
        """
        env_vars = {}
        env_path = Path(env_file)
        
        if env_path.exists():
            with open(env_path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    # 跳过空行和注释
                    if line and not line.startswith('#'):
                        if '=' in line:
                            key, value = line.split('=', 1)
                            # 移除引号
                            value = value.strip().strip('"').strip("'")
                            env_vars[key.strip()] = value
        
        return env_vars
    
    def _replace_env_vars(self, value: Any, env_vars: Dict[str, str]) -> Any:
        """
        递归替换配置中的环境变量（支持 ${ENV_VAR} 语法）
        
        Args:
            value: 配置值
            env_vars: 环境变量字典
            
        Returns:
            替换后的值
        """
        if isinstance(value, str):
            # 匹配 ${VAR_NAME} 格式
            pattern = r'\$\{([^}]+)\}'
            
            def replace(match):
                var_name = match.group(1)
                return env_vars.get(var_name, match.group(0))
            
            return re.sub(pattern, replace, value)
        
        elif isinstance(value, dict):
            return {k: self._replace_env_vars(v, env_vars) for k, v in value.items()}
        
        elif isinstance(value, list):
            return [self._replace_env_vars(item, env_vars) for item in value]
        
        return value
    
    def _deep_merge(self, base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
        """
        深度合并两个配置字典
        
        Args:
            base: 基础配置
            override: 覆盖配置
            
        Returns:
            合并后的配置
        """
        result = base.copy()
        for key, value in override.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = self._deep_merge(result[key], value)
            else:
                result[key] = value
        return result
    
    def _load_from_yaml(self) -> Dict[str, Any]:
        """
        从 YAML 文件加载配置
        
        Returns:
            配置字典
        """
        config_file = Path(self.config_path)
        
        if not config_file.exists():
            logger.warning(f"配置文件不存在：{self.config_path}，将使用默认配置")
            return {}
        
        # 加载 .env 文件
        env_file = config_file.parent / '.env'
        env_vars = self._load_env_file(str(env_file))
        
        # 合并系统环境变量
        env_vars.update(dict(os.environ))
        
        # 加载 YAML 配置
        try:
            with open(config_file, 'r', encoding='utf-8') as f:
                config = yaml.safe_load(f)
        except yaml.YAMLError as e:
            logger.error(f"YAML 配置文件解析失败：{e}")
            raise ConfigValidationError(f"YAML 解析失败：{e}")
        
        if config is None:
            config = {}
        
        # 替换环境变量
        config = self._replace_env_vars(config, env_vars)
        
        # 记录文件修改时间
        self._last_modified = datetime.fromtimestamp(config_file.stat().st_mtime)
        
        logger.info(f"YAML 配置加载成功：{self.config_path}")
        return config
    
    def _apply_env_overrides(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """
        应用环境变量覆盖配置
        
        Args:
            config: 当前配置
            
        Returns:
            应用覆盖后的配置
        """
        # 币安 API 密钥
        if os.getenv('BINANCE_API_KEY'):
            config.setdefault('exchange', {})['api_key'] = os.getenv('BINANCE_API_KEY')
        if os.getenv('BINANCE_API_SECRET'):
            config.setdefault('exchange', {})['api_secret'] = os.getenv('BINANCE_API_SECRET')
        
        # 系统模式
        if os.getenv('SYSTEM_MODE'):
            config.setdefault('system', {})['mode'] = os.getenv('SYSTEM_MODE')
        
        # 数据库配置
        if os.getenv('DB_HOST'):
            config.setdefault('database', {})['host'] = os.getenv('DB_HOST')
        if os.getenv('DB_PORT'):
            config.setdefault('database', {})['port'] = os.getenv('DB_PORT')
        
        # 服务地址
        if os.getenv('KLINE_SERVICE_URL'):
            config.setdefault('services', {}).setdefault('kline', {})['url'] = os.getenv('KLINE_SERVICE_URL')
        if os.getenv('NOTIFICATION_SERVICE_URL'):
            config.setdefault('services', {}).setdefault('notification', {})['url'] = os.getenv('NOTIFICATION_SERVICE_URL')
        
        return config
    
    def _validate_config(self, config: Dict[str, Any]) -> List[str]:
        """
        验证配置完整性
        
        Args:
            config: 配置字典
            
        Returns:
            错误信息列表
        """
        errors = []
        
        # 检查必填字段
        for key in self.REQUIRED_KEYS:
            value = self._get_nested_value(config, key)
            if value is None or (isinstance(value, str) and not value):
                errors.append(f"缺少必需的配置项：{key}")
        
        # 验证 API 密钥（仅全自动模式需要）
        mode = self._get_nested_value(config, 'system.mode') or 'signal'
        if mode == 'auto':
            api_key = self._get_nested_value(config, 'exchange.api_key')
            if not api_key or api_key == '${BINANCE_API_KEY}':
                errors.append("全自动模式需要配置币安 API 密钥")
            
            api_secret = self._get_nested_value(config, 'exchange.api_secret')
            if not api_secret or api_secret == '${BINANCE_API_SECRET}':
                errors.append("全自动模式需要配置币安 API Secret")
        
        # 验证交易对格式
        symbol = self._get_nested_value(config, 'exchange.symbol')
        if symbol and not symbol.endswith('USDT'):
            errors.append("交易对格式不正确，应为 XXXUSDT")
        
        # 验证策略参数
        grid_config = self._get_nested_value(config, 'strategy.grid') or {}
        min_count = grid_config.get('min_grid_count', 20)
        max_count = grid_config.get('max_grid_count', 50)
        base_count = grid_config.get('base_grid_count', 30)
        
        if min_count < 2:
            errors.append("最小网格数不能小于 2")
        if max_count > 100:
            errors.append("最大网格数不能大于 100")
        if min_count >= max_count:
            errors.append("最小网格数必须小于最大网格数")
        if base_count < min_count or base_count > max_count:
            errors.append(f"基准网格数应在 [{min_count}, {max_count}] 范围内")
        
        # 验证风险参数
        risk_config = self._get_nested_value(config, 'strategy.risk') or {}
        hard_stop_loss = risk_config.get('hard_stop_loss', -0.08)
        if hard_stop_loss >= 0:
            errors.append("硬止损必须为负数")
        if hard_stop_loss < -1:
            errors.append("硬止损不能小于 -100%")
        
        # 验证执行参数
        execution_config = self._get_nested_value(config, 'execution') or {}
        inspection_interval = execution_config.get('inspection_interval', 3600)
        if inspection_interval < 60:
            errors.append("巡检间隔不能小于 60 秒")
        
        # 验证监控配置
        monitoring_config = self._get_nested_value(config, 'monitoring') or {}
        alert_config = monitoring_config.get('alert', {})
        if alert_config.get('enabled', False):
            has_channel = any([
                alert_config.get('feishu_webhook'),
                alert_config.get('dingding_webhook'),
                alert_config.get('telegram_bot_token'),
            ])
            if not has_channel:
                errors.append("启用报警但未配置任何报警渠道")
        
        return errors
    
    def _get_nested_value(self, config: Dict[str, Any], key: str) -> Any:
        """
        获取嵌套字典值
        
        Args:
            config: 配置字典
            key: 点分隔的键路径
            
        Returns:
            配置值
        """
        keys = key.split('.')
        value = config
        for k in keys:
            if isinstance(value, dict) and k in value:
                value = value[k]
            else:
                return None
        return value
    
    def load(self, validate: bool = True) -> 'ConfigLoader':
        """
        加载配置
        
        Args:
            validate: 是否验证配置
            
        Returns:
            self（链式调用）
        """
        try:
            # 从 YAML 加载
            yaml_config = self._load_from_yaml()
            
            # 与默认配置合并
            merged_config = self._deep_merge(self.DEFAULT_CONFIG.copy(), yaml_config)
            
            # 应用环境变量覆盖
            final_config = self._apply_env_overrides(merged_config)
            
            self._config = final_config
            
            # 验证配置
            if validate:
                self._validation_errors = self._validate_config(final_config)
                if self._validation_errors:
                    logger.warning(f"配置验证发现 {len(self._validation_errors)} 个问题：")
                    for error in self._validation_errors:
                        logger.warning(f"  - {error}")
            
            logger.info("配置加载完成")
            return self
            
        except Exception as e:
            logger.error(f"配置加载失败：{e}")
            raise
    
    def get(self, key: str, default: Any = None) -> Any:
        """
        获取配置值（支持点分隔路径）
        
        Args:
            key: 配置键（如 'strategy.grid.base_grid_count'）
            default: 默认值
            
        Returns:
            配置值
        """
        if not self._config:
            self.load()
        
        return self._get_nested_value(self._config, key) or default
    
    def get_all(self) -> Dict[str, Any]:
        """
        获取完整配置
        
        Returns:
            配置字典的深拷贝
        """
        import copy
        return copy.deepcopy(self._config)
    
    def get_exchange_config(self) -> Dict[str, Any]:
        """获取交易所配置"""
        return self.get('exchange', {})
    
    def get_strategy_config(self) -> Dict[str, Any]:
        """获取策略配置"""
        return self.get('strategy', {})
    
    def get_execution_config(self) -> Dict[str, Any]:
        """获取执行配置"""
        return self.get('execution', {})
    
    def get_services_config(self) -> Dict[str, Any]:
        """获取服务配置"""
        return self.get('services', {})
    
    def get_database_config(self) -> Dict[str, Any]:
        """获取数据库配置"""
        return self.get('database', {})
    
    def get_monitoring_config(self) -> Dict[str, Any]:
        """获取监控配置"""
        return self.get('monitoring', {})
    
    def get_trading_config(self) -> Dict[str, Any]:
        """获取交易配置"""
        return self.get('exchange', {})
    
    def get_logging_config(self) -> Dict[str, Any]:
        """获取日志配置"""
        return self.get('monitoring.logging', {})
    
    def is_modified(self) -> bool:
        """
        检查配置文件是否被修改
        
        Returns:
            是否被修改
        """
        config_file = Path(self.config_path)
        if config_file.exists():
            mtime = datetime.fromtimestamp(config_file.stat().st_mtime)
            return mtime > self._last_modified if self._last_modified else True
        return False
    
    def reload_if_needed(self) -> bool:
        """
        如果配置文件被修改则重新加载
        
        Returns:
            是否重新加载
        """
        if self.is_modified():
            logger.info("检测到配置文件修改，重新加载...")
            try:
                self.load()
                logger.info("配置热更新成功")
                return True
            except Exception as e:
                logger.error(f"配置热更新失败：{e}")
                return False
        return False
    
    def get_validation_errors(self) -> List[str]:
        """获取验证错误列表"""
        return self._validation_errors.copy()
    
    def is_valid(self) -> bool:
        """检查配置是否有效"""
        return len(self._validation_errors) == 0
    
    @property
    def mode(self) -> str:
        """获取运行模式：'auto' 或 'signal'"""
        return self.get('system.mode', 'auto')
    
    def print_config_summary(self) -> None:
        """打印配置摘要"""
        logger.info("=" * 60)
        logger.info("配置摘要")
        logger.info("=" * 60)
        
        # 系统信息
        logger.info(f"系统名称：{self.get('system.name')}")
        logger.info(f"系统版本：{self.get('system.version')}")
        logger.info(f"运行模式：{self.mode}")
        
        # 交易所配置
        exchange = self.get_exchange_config()
        if exchange:
            logger.info(f"交易所：{'测试网' if exchange.get('testnet') else '主网'}")
            logger.info(f"交易对：{exchange.get('symbol')}")
        
        # 策略配置
        strategy = self.get_strategy_config()
        if strategy:
            indicators = strategy.get('indicators', {})
            logger.info(f"ADX 周期：{indicators.get('adx_period')}")
            logger.info(f"ATR 周期：{indicators.get('atr_period')}")
            
            grid = strategy.get('grid', {})
            logger.info(f"基准网格数：{grid.get('base_grid_count')}")
            logger.info(f"杠杆倍数：{grid.get('leverage')}")
            
            risk = strategy.get('risk', {})
            logger.info(f"硬止损：{risk.get('hard_stop_loss') * 100}%")
        
        # 执行配置
        execution = self.get_execution_config()
        if execution:
            logger.info(f"巡检间隔：{execution.get('inspection_interval')}秒")
            
            param_adj = execution.get('parameter_adjustment', {})
            if param_adj:
                logger.info(f"参数调整：{'启用' if param_adj.get('enabled') else '禁用'}")
        
        logger.info("=" * 60)


def create_default_config(output_path: str = "config/config.yaml") -> bool:
    """
    创建默认配置文件
    
    Args:
        output_path: 输出路径
        
    Returns:
        是否成功创建
    """
    loader = ConfigLoader()
    default_config = loader.DEFAULT_CONFIG
    
    try:
        output_file = Path(output_path)
        output_file.parent.mkdir(parents=True, exist_ok=True)
        
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write("# 网格交易系统 - 配置文件\n")
            f.write(f"# 创建时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
            yaml.dump(default_config, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
        
        logger.info(f"默认配置文件已创建：{output_path}")
        return True
        
    except Exception as e:
        logger.error(f"创建配置文件失败：{e}")
        return False


def load_config(config_path: Optional[str] = None, validate: bool = True) -> ConfigLoader:
    """
    便捷函数：加载配置
    
    Args:
        config_path: 配置文件路径
        validate: 是否验证配置
        
    Returns:
        ConfigLoader 实例
    """
    loader = ConfigLoader(config_path)
    loader.load(validate=validate)
    return loader
