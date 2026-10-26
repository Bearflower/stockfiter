"""把容器标准输出同步写入宿主机挂载的轮转日志，并保留原退出码。"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import time
import re


def independent_descendant_groups(root_pid: int, proc_root: Path = Path("/proc")) -> set[int]:
    """找出调度器子孙中另建会话的分片进程组。"""
    children_by_parent: dict[int, list[int]] = {}
    groups: dict[int, int] = {}
    if proc_root.is_dir():
        for entry in proc_root.iterdir():
            if not entry.name.isdigit():
                continue
            try:
                stat = (entry / "stat").read_text()
                fields = stat.rsplit(") ", 1)[1].split()
                pid = int(entry.name)
                parent_pid, group_id = int(fields[1]), int(fields[2])
            except (OSError, ValueError, IndexError):
                continue
            children_by_parent.setdefault(parent_pid, []).append(pid)
            groups[pid] = group_id
    else:  # 本地 macOS 测试路径；生产 Linux 使用 /proc，不依赖 procps。
        result = subprocess.run(
            ["ps", "-axo", "pid=,ppid=,pgid="], capture_output=True, text=True,
            timeout=2, check=True,
        )
        for line in result.stdout.splitlines():
            try:
                pid, parent_pid, group_id = map(int, line.split())
            except ValueError:
                continue
            children_by_parent.setdefault(parent_pid, []).append(pid)
            groups[pid] = group_id

    descendants: set[int] = set()
    queue = list(children_by_parent.get(root_pid, []))
    while queue:
        pid = queue.pop()
        if pid in descendants:
            continue
        descendants.add(pid)
        queue.extend(children_by_parent.get(pid, []))
    root_group = os.getpgid(root_pid)
    own_group = os.getpgrp()
    return {
        groups[pid] for pid in descendants
        if pid in groups and groups[pid] not in (root_group, own_group)
    }


def scheduler_child_running(target: str, proc_root: Path = Path("/proc")) -> bool:
    """健康检查只认可 PID 1 的直接调度器子进程。"""
    try:
        child_pids = (proc_root / "1/task/1/children").read_text().split()
    except OSError:
        return False
    expected_arg = target.encode("utf-8")
    for pid in child_pids:
        if not pid.isdigit():
            continue
        try:
            args = (proc_root / pid / "cmdline").read_bytes().split(b"\0")
        except OSError:
            continue
        if expected_arg in args:
            return True
    return False


def run_logged_process(
    service: str,
    command: list[str],
    log_dir: Path,
    max_bytes: int,
    backup_count: int,
    terminate_grace_seconds: float,
) -> int:
    """运行调度器，实时转发并持久化其子进程树输出。"""
    if not service.replace("-", "").replace("_", "").isalnum():
        raise ValueError("service 只允许字母、数字、连字符和下划线")
    if not command or max_bytes <= 0 or backup_count <= 0 or terminate_grace_seconds <= 0:
        raise ValueError("命令、日志大小与保留份数必须有效")

    log_dir.mkdir(parents=True, exist_ok=True)
    revision = os.environ.get("SOURCE_REVISION", "unknown")
    if not (revision == "unknown" or re.fullmatch(r"[0-9a-f]{40}", revision)):
        raise ValueError("SOURCE_REVISION 必须是完整提交 SHA 或 unknown")
    container = os.environ.get("CONTAINER_NAME", f"stockfilter-{service}")
    if not re.fullmatch(r"stockfilter-(?:kline|obpc|eadvisor)", container):
        raise ValueError("CONTAINER_NAME 非法")
    handler = RotatingFileHandler(
        log_dir / f"{service}_scheduler.log",
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("%(message)s"))
    child = None

    def forward_signal(signum, _frame):
        if child is not None and child.poll() is None:
            try:
                # 分片 worker 使用 start_new_session，不能只通知调度器进程组。
                for group_id in independent_descendant_groups(child.pid):
                    os.killpg(group_id, signum)
            except (OSError, subprocess.SubprocessError) as exc:
                print(f"独立工作进程组信号转发失败：{exc}", file=sys.stderr, flush=True)
            try:
                os.killpg(child.pid, signum)
            except ProcessLookupError:
                pass

    previous_handlers = {
        sig: signal.signal(sig, forward_signal)
        for sig in (signal.SIGTERM, signal.SIGINT)
    }
    try:
        child = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            bufsize=0,
        )
        assert child.stdout is not None
        selector = selectors.DefaultSelector()
        selector.register(child.stdout, selectors.EVENT_READ)
        pending = b""
        exit_seen_at = None
        killed_descendants = False

        def persist_line(raw_line: bytes) -> None:
            message = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
            stamp = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
            record = logging.LogRecord(
                service, logging.INFO, __file__, 0,
                f"{stamp} container={container} revision={revision} {message}",
                (), None,
            )
            handler.emit(record)

        try:
            while True:
                for _key, _mask in selector.select(timeout=0.2):
                    chunk = os.read(child.stdout.fileno(), 65536)
                    if not chunk:
                        selector.unregister(child.stdout)
                        break
                    sys.stdout.buffer.write(chunk)
                    sys.stdout.buffer.flush()
                    pending += chunk
                    while b"\n" in pending:
                        line, pending = pending.split(b"\n", 1)
                        persist_line(line)

                if child.poll() is not None and exit_seen_at is None:
                    exit_seen_at = time.monotonic()
                    # 调度器已退出时，仍持有 stdout 管道的子进程不应把容器伪装成健康。
                    try:
                        os.killpg(child.pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                if not selector.get_map():
                    break
                if exit_seen_at is not None and not killed_descendants:
                    if time.monotonic() - exit_seen_at >= terminate_grace_seconds:
                        try:
                            os.killpg(child.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                        killed_descendants = True
                if killed_descendants and time.monotonic() - exit_seen_at >= terminate_grace_seconds + 1:
                    break
        finally:
            selector.close()
            child.stdout.close()
        if pending:
            persist_line(pending)
        return child.wait()
    finally:
        for sig, previous in previous_handlers.items():
            signal.signal(sig, previous)
        handler.close()


def main() -> int:
    if len(sys.argv) == 3 and sys.argv[1] == "--healthcheck":
        return 0 if scheduler_child_running(sys.argv[2]) else 1
    parser = argparse.ArgumentParser(description="容器日志持久化入口")
    parser.add_argument("--service", required=True)
    parser.add_argument("--log-dir", type=Path, required=True)
    parser.add_argument("--max-bytes", type=int, required=True)
    parser.add_argument("--backup-count", type=int, required=True)
    parser.add_argument("--terminate-grace-seconds", type=float, required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    return run_logged_process(
        args.service, command, args.log_dir, args.max_bytes, args.backup_count,
        args.terminate_grace_seconds,
    )


if __name__ == "__main__":
    raise SystemExit(main())
