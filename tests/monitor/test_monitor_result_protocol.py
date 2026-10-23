"""monitor.sh 查询结果协议与阻断状态收敛的回归测试。"""

from pathlib import Path
import re
import shlex
import subprocess
import tempfile


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


def test_coverage_numerator_uses_same_stock_pool_as_denominator():
    """池外指数 K 线不得计入覆盖分子，分子分母都按 distinct code。"""
    body = _function_body("check_obpc_coverage")
    assert "JOIN schema_stockfilter.stocks" in body
    assert "COUNT(DISTINCT" in body
    assert "pool_count <= 0" in body


def test_task_status_completed_rows_must_match_expected_target_dates():
    """旧 completed 不得掩盖本轮状态写入失败。"""
    body = _function_body("check_obpc_task_status")
    assert "OBPC_TARGET_DATE" in body
    assert "OBPC_RESCAN_DATE" in body
    assert "target_date" in body and "expected_target" in body


def _run_task_status_check(rows: str, *, target: str, rescan: str) -> str:
    """用真实 bash 执行 task_status 检查，docker 与飞书均替换为本地桩。"""
    function_text = "check_obpc_task_status() {\n" + _function_body(
        "check_obpc_task_status"
    ) + "}\n"
    harness = f"""#!/usr/bin/env bash
set -u
WEBHOOK=x
OBPC_TARGET_DATE={shlex.quote(target)}
OBPC_RESCAN_DATE={shlex.quote(rescan)}
LAST_OBPC_TASK_STATUS=ok
LAST_KLINE_RECOVERY_KEY=''
MOCK_ROWS={shlex.quote(rows)}
docker() {{ printf '%s\\n' "$MOCK_ROWS"; }}
send_feishu() {{ printf 'NOTICE|%s|%s|%s\\n' "$2" "$3" "$4"; }}
{function_text}
check_obpc_task_status
"""
    with tempfile.NamedTemporaryFile("w", suffix=".sh", encoding="utf-8") as script:
        script.write(harness)
        script.flush()
        completed = subprocess.run(
            ["bash", script.name], capture_output=True, text=True, check=False
        )
    assert completed.returncode == 0, completed.stderr
    return completed.stdout


def _run_task_status_sequence(rows_sequence: list[str]) -> str:
    """在同一 shell 状态中连续执行多轮，验证去重与恢复状态迁移。"""
    function_text = "check_obpc_task_status() {\n" + _function_body(
        "check_obpc_task_status"
    ) + "}\n"
    calls = []
    for rows in rows_sequence:
        calls.append(f"MOCK_ROWS={shlex.quote(rows)}\ncheck_obpc_task_status")
    harness = f"""#!/usr/bin/env bash
set -u
WEBHOOK=x
OBPC_TARGET_DATE=2026-10-22
OBPC_RESCAN_DATE=2026-10-21
LAST_OBPC_TASK_STATUS=ok
LAST_KLINE_RECOVERY_KEY=''
docker() {{ printf '%s\\n' "$MOCK_ROWS"; }}
send_feishu() {{ printf 'NOTICE|%s|%s|%s\\n' "$2" "$3" "$4"; }}
{function_text}
{chr(10).join(calls)}
"""
    with tempfile.NamedTemporaryFile("w", suffix=".sh", encoding="utf-8") as script:
        script.write(harness)
        script.flush()
        completed = subprocess.run(
            ["bash", script.name], capture_output=True, text=True, check=False
        )
    assert completed.returncode == 0, completed.stderr
    return completed.stdout


def test_task_status_stale_kline_completed_is_anomaly():
    """旧 kline completed 不得掩盖本轮状态写入失败。"""
    rows = "\n".join(
        (
            "kline_update|completed|2026-10-21|normal|-|0|0|-|1.0",
            "backfill|completed|2026-10-21|unknown|-|-|0|-|-",
        )
    )
    output = _run_task_status_check(
        rows, target="2026-10-22", rescan="2026-10-21"
    )
    assert "OBPC 数据任务未完成" in output
    assert "expected_target=2026-10-22" in output


def test_task_status_stale_backfill_completed_is_anomaly():
    """旧 backfill completed 同样必须按重扫目标日 fail-closed。"""
    rows = "\n".join(
        (
            "kline_update|completed|2026-10-22|normal|-|0|0|-|1.0",
            "backfill|completed|2026-10-20|unknown|-|-|0|-|-",
        )
    )
    output = _run_task_status_check(
        rows, target="2026-10-22", rescan="2026-10-21"
    )
    assert "OBPC 数据任务未完成" in output
    assert "expected_target=2026-10-21" in output


def test_task_status_fresh_completed_rows_are_quiet():
    """两个任务目标日都新鲜且 completed 时不发送异常或恢复通知。"""
    rows = "\n".join(
        (
            "kline_update|completed|2026-10-22|normal|-|0|0|-|1.0",
            "backfill|completed|2026-10-21|unknown|-|-|0|-|-",
        )
    )
    output = _run_task_status_check(
        rows, target="2026-10-22", rescan="2026-10-21"
    )
    assert "NOTICE|" not in output


def test_task_status_anomaly_is_deduplicated_then_recovery_is_reported_once():
    """同一异常不重复轰炸，目标日恢复后只发一条恢复通知。"""
    stale = "\n".join(
        (
            "kline_update|completed|2026-10-21|normal|-|0|0|-|1.0",
            "backfill|completed|2026-10-21|unknown|-|-|0|-|-",
        )
    )
    fresh = "\n".join(
        (
            "kline_update|completed|2026-10-22|normal|-|0|0|-|1.0",
            "backfill|completed|2026-10-21|unknown|-|-|0|-|-",
        )
    )
    output = _run_task_status_sequence([stale, stale, fresh, fresh])
    assert output.count("OBPC 数据任务未完成") == 1
    assert output.count("OBPC 数据任务状态正常") == 1


def test_task_status_query_failure_is_fail_closed_and_deduplicated():
    """查询失败哨兵必须告警，重复失败不轰炸，恢复后只通知一次。"""
    failed = "QUERY_FAILED|query|QueryCanceled: statement timeout"
    fresh = "\n".join(
        (
            "kline_update|completed|2026-10-22|normal|-|0|0|-|1.0",
            "backfill|completed|2026-10-21|unknown|-|-|0|-|-",
        )
    )
    output = _run_task_status_sequence([failed, failed, fresh, fresh])
    assert output.count("OBPC 数据任务状态查询失败") == 1
    assert "QUERY_FAILED|query|QueryCanceled" in output
    assert output.count("OBPC 数据任务状态正常") == 1


def test_task_status_missing_row_is_fail_closed():
    """只返回一个任务行属于不完整协议，不能被当作正常。"""
    only_kline = "kline_update|completed|2026-10-22|normal|-|0|0|-|1.0"
    output = _run_task_status_check(
        only_kline, target="2026-10-22", rescan="2026-10-21"
    )
    assert "OBPC 数据任务状态查询失败" in output
    assert "kline_update 行：1 / backfill 行：0" in output
