"""宿主机 monitor.sh 的身份检查、暂停及原子切换。仅由发布脚本调用。"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time


def _parent_pid(proc_root: Path, pid: int) -> int | None:
    try:
        for line in (proc_root / str(pid) / "status").read_text().splitlines():
            if line.startswith("PPid:"):
                return int(line.split(":", 1)[1].strip())
    except (OSError, ValueError):
        pass
    return None


def monitor_pids(script_dir: Path, proc_root: Path = Path("/proc")) -> list[int]:
    """只认目标 scripts 目录的顶层 bash monitor.sh；忽略其短暂 Bash 子进程。"""
    expected = script_dir.resolve()
    matched = set()
    for entry in proc_root.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            args = [part.decode("utf-8") for part in (entry / "cmdline").read_bytes().split(b"\0") if part]
            cwd = (entry / "cwd").resolve(strict=True)
        except (OSError, UnicodeDecodeError):
            continue
        if len(args) != 2 or Path(args[0]).name != "bash" or cwd != expected:
            continue
        if args[1] in ("monitor.sh", str(expected / "monitor.sh")):
            matched.add(int(entry.name))
    roots = []
    for pid in sorted(matched):
        ancestor = _parent_pid(proc_root, pid)
        for _ in range(64):
            if ancestor in matched:
                break
            if ancestor is None or ancestor <= 1:
                roots.append(pid)
                break
            ancestor = _parent_pid(proc_root, ancestor)
        else:
            roots.append(pid)  # 祖先链异常时保守计为独立实例，避免误判单实例。
    return roots


def _single_pid(script_dir: Path, proc_root: Path) -> int:
    pids = monitor_pids(script_dir, proc_root)
    if len(pids) != 1:
        raise RuntimeError(f"必须恰好一个可验证的 monitor.sh 进程，实际={pids}")
    return pids[0]


def _state_path(state_dir: Path) -> Path:
    return state_dir / "monitor-state.json"


def _read_state(state_dir: Path) -> dict:
    return json.loads(_state_path(state_dir).read_text(encoding="utf-8"))


def _write_state(state_dir: Path, data: dict) -> None:
    _state_path(state_dir).write_text(json.dumps(data, ensure_ascii=False) + "\n", encoding="utf-8")


def _stop_monitor(pid: int, script_dir: Path, proc_root: Path) -> None:
    if pid not in monitor_pids(script_dir, proc_root):
        raise RuntimeError("目标监控 PID 身份已变化，拒绝发送信号")
    os.kill(pid, signal.SIGTERM)
    until = time.monotonic() + 10
    while time.monotonic() < until:
        if pid not in monitor_pids(script_dir, proc_root):
            return
        time.sleep(0.1)
    raise RuntimeError("monitor.sh 未在 10 秒内停止；拒绝强杀")


def _start_monitor(script_dir: Path, proc_root: Path, *, require_ready: bool = True) -> int:
    stable_seconds = float(os.environ.get("MONITOR_START_STABLE_SECONDS", "10"))
    if not math.isfinite(stable_seconds) or not 0 < stable_seconds <= 60:
        raise ValueError("MONITOR_START_STABLE_SECONDS 必须在 (0,60] 秒内")
    ready_timeout = float(os.environ.get("MONITOR_READY_TIMEOUT_SECONDS", "60"))
    if not math.isfinite(ready_timeout) or not 0 < ready_timeout <= 300:
        raise ValueError("MONITOR_READY_TIMEOUT_SECONDS 必须在 (0,300] 秒内")
    if monitor_pids(script_dir, proc_root):
        raise RuntimeError("启动前仍存在 monitor.sh 进程")
    ready_file = script_dir.parent / "logs" / "monitor.ready"
    ready_file.unlink(missing_ok=True)
    with (script_dir.parent / "logs" / "monitor.stdout.log").open("ab") as output:
        child = subprocess.Popen(
            ["bash", "monitor.sh"], cwd=script_dir, stdin=subprocess.DEVNULL,
            stdout=output, stderr=subprocess.STDOUT, start_new_session=True,
        )
    appearance_deadline = time.monotonic() + 3
    ready_deadline = time.monotonic() + ready_timeout
    seen_at = None
    while True:
        if child.poll() is not None:
            raise RuntimeError(f"monitor.sh 启动即退出，exit={child.returncode}")
        pids = monitor_pids(script_dir, proc_root)
        if pids == [child.pid]:
            ready_pid = (
                ready_file.read_text().strip() if require_ready and ready_file.exists()
                else str(child.pid) if not require_ready else ""
            )
            if ready_pid == str(child.pid):
                seen_at = seen_at or time.monotonic()
                if time.monotonic() - seen_at >= stable_seconds:
                    return child.pid
        elif seen_at is not None or len(pids) > 1:
            raise RuntimeError(f"monitor.sh 稳定观察期间进程身份异常：{pids}")
        if seen_at is None and time.monotonic() >= appearance_deadline:
            if pids != [child.pid]:
                raise RuntimeError("monitor.sh 启动后未能确认唯一进程")
        if require_ready and seen_at is None and time.monotonic() >= ready_deadline:
            raise RuntimeError("monitor.sh 启动后未在限时内写入就绪标记")
        time.sleep(0.1)


def validate_candidate(candidate: Path) -> None:
    subprocess.run(["bash", "-n", str(candidate)], check=True, timeout=10)


def pause_monitor(script_dir: Path, state_dir: Path, proc_root: Path = Path("/proc")) -> None:
    """在容器停机前暂停监控；身份不明时直接失败，不动任何进程。"""
    pid = _single_pid(script_dir, proc_root)
    if state_dir.exists():
        raise RuntimeError("发布状态目录已存在，拒绝覆盖")
    state_dir.mkdir(parents=True)
    shutil.copy2(script_dir / "monitor.sh", state_dir / "monitor.sh.old")
    _write_state(state_dir, {"phase": "preparing", "old_pid": pid})
    _stop_monitor(pid, script_dir, proc_root)
    _write_state(state_dir, {"phase": "paused", "old_pid": pid})


def _replace_script(source: Path, target: Path) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=".monitor.", dir=target.parent)
    os.close(descriptor)
    try:
        shutil.copy2(source, temporary)
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def activate_monitor(script_dir: Path, state_dir: Path, candidate: Path,
                     proc_root: Path = Path("/proc")) -> None:
    validate_candidate(candidate)
    state = _read_state(state_dir)
    if state["phase"] != "paused":
        raise RuntimeError("监控不在已暂停状态")
    if monitor_pids(script_dir, proc_root):
        raise RuntimeError("切换前发现监控进程，拒绝形成双实例")
    changed = candidate.read_bytes() != (script_dir / "monitor.sh").read_bytes()
    if changed:
        _replace_script(candidate, script_dir / "monitor.sh")
    pid = _start_monitor(script_dir, proc_root)
    _write_state(state_dir, {**state, "phase": "active", "new_pid": pid, "changed": changed})


def restore_monitor(script_dir: Path, state_dir: Path,
                    proc_root: Path = Path("/proc")) -> None:
    """发布失败时恢复旧脚本与唯一旧监控；新容器可能已启动，发布须另记失败。"""
    if not _state_path(state_dir).exists():
        return
    state = _read_state(state_dir)
    if state["phase"] in ("restored", "complete"):
        return
    pids = monitor_pids(script_dir, proc_root)
    if len(pids) > 1:
        raise RuntimeError("监控进程多于一个，拒绝自动恢复")
    if pids:
        _stop_monitor(pids[0], script_dir, proc_root)
    _replace_script(state_dir / "monitor.sh.old", script_dir / "monitor.sh")
    pid = _start_monitor(script_dir, proc_root, require_ready=False)
    _write_state(state_dir, {**state, "phase": "restored", "restored_pid": pid})


def complete_monitor(state_dir: Path) -> None:
    state = _read_state(state_dir)
    if state["phase"] != "active":
        raise RuntimeError("监控未处于正常运行状态")
    _write_state(state_dir, {**state, "phase": "complete"})


def verify_monitor(script_dir: Path, state_dir: Path,
                   proc_root: Path = Path("/proc")) -> None:
    state = _read_state(state_dir)
    if state["phase"] != "active" or monitor_pids(script_dir, proc_root) != [state["new_pid"]]:
        raise RuntimeError("发布成功前 monitor.sh 不是唯一且存活的新进程")
    ready_file = script_dir.parent / "logs" / "monitor.ready"
    if not ready_file.is_file() or ready_file.read_text().strip() != str(state["new_pid"]):
        raise RuntimeError("发布成功前 monitor.sh 就绪标记缺失或 PID 不匹配")


if __name__ == "__main__":
    if len(sys.argv) not in (4, 5):
        raise SystemExit("用法：monitor_deploy.py validate|pause|activate|verify|restore|complete SCRIPT_DIR STATE_DIR [CANDIDATE]")
    operation, script_dir, state_dir = sys.argv[1:4]
    script_dir_path, state_dir_path = Path(script_dir), Path(state_dir)
    try:
        if operation == "validate" and len(sys.argv) == 5:
            validate_candidate(Path(sys.argv[4]))
        elif operation == "pause":
            pause_monitor(script_dir_path, state_dir_path)
        elif operation == "activate" and len(sys.argv) == 5:
            activate_monitor(script_dir_path, state_dir_path, Path(sys.argv[4]))
        elif operation == "restore":
            restore_monitor(script_dir_path, state_dir_path)
        elif operation == "verify":
            verify_monitor(script_dir_path, state_dir_path)
        elif operation == "complete":
            complete_monitor(state_dir_path)
        else:
            raise ValueError("非法命令或参数")
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"monitor 部署控制失败：{exc}", file=sys.stderr)
        raise SystemExit(1)
