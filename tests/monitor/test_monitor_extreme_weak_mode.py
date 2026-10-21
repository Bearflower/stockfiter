"""monitor.sh 极弱穿透内联块的 policy_mode fail-closed 回归测试。

背景（2026-10-20 复核 P2）：
    极弱穿透监控的 data_metrics 非空、但 policy_mode 缺失或非法时，修复前既不进入
    graded/shadow 检查，也不会设置 metrics_missing，最终输出 leak=0 / metrics_missing=0，
    被 shell 当作正常并清除既有告警——与验收脚本「非法或缺失 mode 必须 fatal」不一致。

本测试从 monitor.sh 中提取该内联 python 块，桩掉 DatabaseManager 直接执行，断言：
    - policy_mode 非法/缺失 → metrics_missing=1（fail-closed，不得当作正常）
    - policy_mode=hard_filter + 弱市 → 0|0（正常路径不受影响）
    - policy_mode=hard_filter + extreme_weak 且 selected_count>0 → leak=1
    - shadow 极弱 allow_entry=None → metrics_missing=1
"""

import json
import os
import sys
import types

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_MONITOR = os.path.join(_REPO_ROOT, "scripts", "monitor.sh")


def _extract_extreme_weak_block() -> str:
    with open(_MONITOR, "r", encoding="utf-8") as f:
        lines = f.read().splitlines()
    start = None
    for i, l in enumerate(lines):
        if l.strip() == 'local result=$(docker exec stockfilter-obpc python3 -c "':
            start = i + 1
            break
    assert start is not None, "未找到极弱穿透内联块起点"
    end = None
    for j in range(start, len(lines)):
        if lines[j].lstrip().startswith('" 2>&1'):
            end = j
            break
    assert end is not None, "未找到极弱穿透内联块终点"
    code = "\n".join(lines[start:end])
    code = code.replace("'$target_date'", "'2026-10-16'")
    code = code.replace('\\"', '"')  # 还原 bash 双引号内的 \" 转义
    return code


_BLOCK = _extract_extreme_weak_block()


def _run_block(row):
    """桩掉 DatabaseManager 后执行内联块，返回 (leak, metrics_missing)。"""

    class _FakeCur:
        def execute(self, *a, **k):
            pass

        def fetchone(self):
            return row

    class _FakeConn:
        def cursor(self):
            return _FakeCur()

    class _FakeDB:
        def __init__(self):
            self.conn = _FakeConn()

        def close(self):
            pass

    mod = types.ModuleType("data.database")
    mod.DatabaseManager = lambda: _FakeDB()

    out = []
    code = _BLOCK.replace(
        "emit_monitor_result(target, leak, metrics_missing, leak_info)",
        "out.append(f'{target}|{leak}|{metrics_missing}|{leak_info}')",
    )
    # 桩掉 data.database 后执行，执行完必须还原 sys.modules，避免污染其它用例
    saved = sys.modules.get("data.database")
    sys.modules["data.database"] = mod
    try:
        exec(compile(code, "<inline>", "exec"), {"out": out})
    finally:
        if saved is None:
            sys.modules.pop("data.database", None)
        else:
            sys.modules["data.database"] = saved
    assert out, "内联块未产生输出"
    parts = out[-1].split("|")
    return parts[1], parts[2]


def test_extreme_weak_block_extracted_compiles():
    compile(_BLOCK, "<inline>", "exec")


@pytest.mark.parametrize("mode", ["bogus", ""])
def test_illegal_or_missing_policy_mode_is_fail_closed(mode):
    dm = json.dumps({"market_regime": "weak"})
    if mode:
        dm = json.dumps({"policy_mode": mode, "market_regime": "weak"})
    leak, metrics_missing = _run_block((dm, 0, "completed_zero"))
    assert metrics_missing == "1", "非法/缺失 policy_mode 必须 fail-closed 告警"
    assert leak == "0"


def test_hard_filter_weak_is_normal():
    dm = json.dumps({"policy_mode": "hard_filter", "market_regime": "weak"})
    leak, metrics_missing = _run_block((dm, 0, "completed_zero"))
    assert (leak, metrics_missing) == ("0", "0")


def test_hard_filter_extreme_weak_with_selection_leaks():
    dm = json.dumps({"policy_mode": "hard_filter", "market_regime": "extreme_weak"})
    leak, metrics_missing = _run_block((dm, 2, "completed_with_signals"))
    assert leak == "1"


def test_shadow_extreme_weak_none_allow_entry_is_fail_closed():
    dm = json.dumps(
        {
            "policy_mode": "shadow",
            "shadow_graded_decision": {"regime": "extreme_weak", "allow_entry": None},
        }
    )
    leak, metrics_missing = _run_block((dm, 0, "completed_zero"))
    assert metrics_missing == "1"
    assert leak == "0"


def test_shadow_extreme_weak_false_allow_entry_is_normal():
    dm = json.dumps(
        {
            "policy_mode": "shadow",
            "shadow_graded_decision": {"regime": "extreme_weak", "allow_entry": False},
        }
    )
    leak, metrics_missing = _run_block((dm, 0, "completed_zero"))
    assert (leak, metrics_missing) == ("0", "0")
