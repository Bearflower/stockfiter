"""monitor.sh 的 send_feishu 调用点「实参捕获」行为测试。

背景（2026-10-20 复核 P2）：
    send_feishu 只接受 webhook / title / content / level 四个参数，但脚本里多处
    调用把通知正文拆成两个及以上参数（用 `\\` 续行连接）。shell 会把这些片段按
    顺序绑定到形参上，导致第二段正文被当成 level、真正的 "WARNING" 被丢弃——
    这类语义错位 `bash -n` 检不出来，只有真正执行并捕获 $@ 才能发现。

本测试用真实 bash 执行每个调用点（覆盖 send_feishu 以捕获实参），断言：
    1. 参数个数恰好为 4；
    2. 第 4 个参数是合法告警级别（INFO / WARNING / CRITICAL），或为变量引用
       （由调用方动态决定级别，如 "$level"）。
"""

import os
import re
import shlex
import subprocess
import tempfile

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_MONITOR = os.path.join(_REPO_ROOT, "scripts", "monitor.sh")

_KNOWN_LEVELS = {"INFO", "WARNING", "CRITICAL"}

# 供本次测试真实执行脚本，只需要 bash 环境
_BASH = "bash"


def _load_monitor_text() -> str:
    with open(_MONITOR, "r", encoding="utf-8") as f:
        return f.read()


def _extract_send_feishu_calls(text: str):
    """提取 monitor.sh 中所有 send_feishu 调用命令文本（含 `\\` 续行）。

    跳过函数定义行 `send_feishu() {`（其后紧跟 `(` 而非空格/引号）。
    """
    lines = text.splitlines()
    calls = []
    i = 0
    while i < len(lines):
        stripped = lines[i].lstrip()
        if stripped.startswith("send_feishu ") or stripped.startswith("send_feishu\""):
            buf = [lines[i]]
            while buf[-1].rstrip().endswith("\\"):
                i += 1
                if i >= len(lines):
                    break
                buf.append(lines[i])
            calls.append("\n".join(buf))
        i += 1
    return calls


def _run_capture(call_text: str):
    """在真实 bash 中执行调用命令，捕获 send_feishu 的实参。

    返回 (argc, [arg1, arg2, ...])。
    """
    with tempfile.TemporaryDirectory() as td:
        cap = os.path.join(td, "cap.txt")
        script_path = os.path.join(td, "run.sh")
        header = (
            "#!/usr/bin/env bash\n"
            "set +u\n"
            "CAP=" + shlex.quote(cap) + "\n"
            "CHECK_INTERVAL=600\n"
            "log() { :; }\n"
            "send_feishu() { printf '%s\\n' \"$#\" >> \"$CAP\"; printf '%s\\n' \"$@\" >> \"$CAP\"; }\n"
            "WEBHOOK=\"x\"\n"
        )
        with open(script_path, "w", encoding="utf-8") as f:
            f.write(header + call_text + "\n")
        subprocess.run(
            [_BASH, script_path], capture_output=True, text=True, check=False
        )
        if not os.path.exists(cap):
            return 0, []
        with open(cap, "r", encoding="utf-8") as f:
            content = f.read()
    rows = content.split("\n")
    argc = int(rows[0]) if rows and rows[0].strip() else 0
    return argc, rows[1:1 + argc]


_CALLS = _extract_send_feishu_calls(_load_monitor_text())


def test_extraction_found_all_call_sites():
    """提取到的调用点数量应覆盖脚本内全部 send_feishu 调用（远多于 20 处）。"""
    assert len(_CALLS) >= 30, f"仅提取到 {len(_CALLS)} 个调用点，可能漏提取"


@pytest.mark.parametrize("idx", range(len(_CALLS)))
def test_send_feishu_calls_have_exactly_four_args(idx):
    """每个调用点必须恰好传 4 个参数（webhook/title/content/level）。

    序列化调用点首行，便于定位失败项。
    """
    call_text = _CALLS[idx]
    first_line = call_text.splitlines()[0].strip()
    argc, _args = _run_capture(call_text)
    assert argc == 4, (
        f"参数个数={argc}（应为 4），调用点：{first_line}\n"
        f"正文片段被错当成 level 会导致通知正文截断且告警等级错误。"
    )


@pytest.mark.parametrize("idx", range(len(_CALLS)))
def test_send_feishu_level_is_known_or_variable(idx):
    """第 4 个参数必须是合法告警级别，或明确的变量引用（如 "$level"）。"""
    call_text = _CALLS[idx]
    first_line = call_text.splitlines()[0].strip()
    _argc, args = _run_capture(call_text)
    level = args[3] if len(args) >= 4 else None
    if level in _KNOWN_LEVELS:
        return
    # 允许动态级别：调用点末位是 "$var" / "${var}" 形式的变量引用
    tail_var = re.search(r'"\$\{?[A-Za-z_][A-Za-z0-9_]*\}?"\s*$', call_text.rstrip())
    assert level == "" and tail_var is not None, (
        f"第 4 个参数 level={level!r} 既不是合法级别也不是变量引用，调用点：{first_line}"
    )
