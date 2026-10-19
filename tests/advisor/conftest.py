"""advisor 模块测试共享 fixture。

加载真实的 distill_changying/scripts/advisor/config.yaml，
让测试使用生产一致的仓位上下界和其他配置，避免测试与生产脱节。

路径隔离（解决 scripts 命名空间冲突）
---------------------------------------
仓库根存在一个 ``scripts/`` 目录（stockfilter 自己的脚本），
``distill_changying/scripts/`` 里又有 ``advisor/``、``distill/``、``shared/`` 三个子包。
两者都没有 ``scripts/__init__.py``，都是 PEP 420 命名空间包，
在同一个 pytest 进程里 ``scripts`` 这个名字只能指向一个物理包。

advisor_llm.py 里写的是 ``from scripts.shared.llm_utils import ...``，
它要求 ``scripts.shared`` 能解析到 ``distill_changying/scripts/shared``；
同时 stockfilter 自己的测试会 ``import scripts.data.update_kline_daily``，
这又要求 ``scripts.data`` 解析到仓库根。

本 conftest 在模块被 pytest 导入的最早期（早于任何 test 文件收集）做两件事：

1. 触发仓库根 ``scripts`` 命名空间包的注册（保持给 ``scripts.data``、
   ``scripts.scheduler_obpc`` 等用）。
2. 手工把 ``scripts.shared`` 别名注册到 ``sys.modules``，让它
   指向 ``distill_changying/scripts/shared`` 目录，
   并把该目录追加到 ``scripts.__path__`` 里，保证后续 ``scripts.shared.*``
   子模块能被正常 ``import``。

这样两边的 ``scripts.*`` 导入路径在同一个 pytest 进程里都能工作。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

# ============= 路径引导（必须在 fixture 定义之前，模块导入即执行） =============

# 仓库根
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# 触发仓库根 scripts 命名空间包注册（scripts/ 没有 __init__.py，纯 PEP 420）
# 必须先这一步，否则 scripts.shared 别名无处挂载
if "scripts" not in sys.modules:
    import scripts  # noqa: F401  —— 让命名空间包进 sys.modules

# 手工注册 scripts.shared 别名包，指向 distill_changying/scripts/shared
_SHARED_DIR = _ROOT / "distill_changying" / "scripts" / "shared"
if "scripts.shared" not in sys.modules and _SHARED_DIR.is_dir():
    _spec = importlib.util.spec_from_file_location(
        "scripts.shared",
        _SHARED_DIR / "__init__.py",
        submodule_search_locations=[str(_SHARED_DIR)],
    )
    _mod = importlib.util.module_from_spec(_spec)
    sys.modules["scripts.shared"] = _mod
    _spec.loader.exec_module(_mod)
    # 同步到父包 attributes，让后续 ``from scripts.shared.X import ...``
    # 的属性查找路径也能命中
    setattr(sys.modules["scripts"], "shared", _mod)
    # 把 shared 目录追加到 scripts.__path__，保证 Python importlib 的
    # 路径搜索也能找到（sys.modules 挂好后这步主要是让 importlib
    # 在首次触发子模块时走正规的路径解析）
    parent_pkg = sys.modules["scripts"]
    try:
        ns_path = getattr(parent_pkg, "__path__", None)
        if ns_path is not None:
            # _NamespacePath（Python 3.9）只有 append 和切片赋值，没有 insert
            if str(_SHARED_DIR) not in list(ns_path):
                ns_path.append(str(_SHARED_DIR))
    except Exception:
        # 极少数环境 __path__ 可能不可写，此时 sys.modules 已足够覆盖
        pass

# ============= 以下是 fixture 定义 =============

import pytest
import yaml


@pytest.fixture(scope="session")
def advisor_config() -> dict:
    """加载真实 advisor 配置的只读 dict。

    作用域 session：同一次 pytest 运行中只加载一次文件。
    如果真实配置文件不存在，会 fallback 到最小可用的 mock 配置（
    仅含 position_bounds），方便在非完整仓库环境下也能跑。
    """
    config_path = Path(__file__).resolve().parents[2] / "distill_changying" / "scripts" / "advisor" / "config.yaml"
    if config_path.is_file():
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        assert "position_bounds" in cfg, (
            "真实 advisor 配置缺少 position_bounds 节，请先在 "
            "distill_changying/scripts/advisor/config.yaml 中补充"
        )
        return cfg

    # fallback：最小可用配置（仅用于非完整仓库环境）
    return {
        "position_bounds": {
            "stock_pct": {"min": 5, "max": 80},
            "bond_pct": {"min": 10, "max": 50},
            "cash_pct": {"min": 10, "max": 60},
        },
        "etf_pool": [],
    }


@pytest.fixture(scope="session")
def position_bounds(advisor_config: dict) -> dict:
    """从 advisor_config 中提取 position_bounds 节，方便直接读取上下界数值。"""
    return advisor_config["position_bounds"]
