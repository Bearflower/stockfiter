"""monitor.sh 查询结果协议与阻断状态收敛的回归测试。"""

from pathlib import Path
import re


REPO_ROOT = Path(__file__).resolve().parents[2]
MONITOR_TEXT = (REPO_ROOT / "scripts" / "monitor.sh").read_text(encoding="utf-8")


def _function_body(name: str) -> str:
    match = re.search(
        rf"^{re.escape(name)}\(\) \{{\n(?P<body>.*?)(?=^\}}\n)",
        MONITOR_TEXT,
        flags=re.MULTILINE | re.DOTALL,
    )
    assert match, f"找不到 shell 函数 {name}"
    return match.group("body")


def test_database_queries_use_explicit_result_protocol():
    """Loguru 时间戳/竖线日志不得被误解析成业务查询结果。"""
    assert 'MONITOR_RESULT_PREFIX="__OBPC_MONITOR_RESULT__"' in MONITOR_TEXT

    for name in (
        "check_obpc_coverage",
        "check_obpc_scan_stale",
        "check_obpc_scan_blocked",
        "check_obpc_push_missing",
        "check_obpc_extreme_weak_leak",
    ):
        body = _function_body(name)
        assert "emit_monitor_result" in body, name
        assert "extract_monitor_results" in body, name
        assert "grep '|'" not in body, name


def test_blocked_scan_only_considers_latest_run_for_each_signal_date():
    """同一交易日后续 rescan 成功后，早先 blocked_data 不应继续报警。"""
    body = _function_body("check_obpc_scan_blocked")
    assert "ROW_NUMBER() OVER" in body
    assert "PARTITION BY signal_date" in body
    assert "run_rank = 1" in body
