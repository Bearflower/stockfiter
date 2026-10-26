"""部署前日志归档与跨容器重建的持久日志。"""

import gzip
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest


ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "scripts" / "persistent_log_runner.py"
ARCHIVER = ROOT / "scripts" / "archive_container_logs.sh"


def test_runner_keeps_subprocess_output_and_exit_code_across_restarts(tmp_path):
    command = [
        sys.executable, str(RUNNER), "--service", "kline", "--log-dir", str(tmp_path),
        "--max-bytes", "1024", "--backup-count", "2", "--terminate-grace-seconds", "1", "--",
        sys.executable, "-c", "import sys; print('first'); print('problem', file=sys.stderr); sys.exit(7)",
    ]
    first = subprocess.run(command, capture_output=True, text=True, timeout=10)
    assert first.returncode == 7
    assert "first" in first.stdout
    assert "problem" in first.stdout

    second = subprocess.run(
        command[:-3] + [sys.executable, "-c", "print('second')"],
        capture_output=True, text=True, timeout=10,
    )
    assert second.returncode == 0
    persisted = (tmp_path / "kline.log").read_text()
    assert "first" in persisted and "problem" in persisted and "second" in persisted


def test_runner_rotates_persistent_file_without_losing_stdout(tmp_path):
    result = subprocess.run(
        [
            sys.executable, str(RUNNER), "--service", "obpc", "--log-dir", str(tmp_path),
            "--max-bytes", "40", "--backup-count", "2", "--terminate-grace-seconds", "1", "--",
            sys.executable, "-c", "print('alpha' * 8); print('beta' * 8)",
        ],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0
    assert "alpha" in result.stdout and "beta" in result.stdout
    assert (tmp_path / "obpc.log.1").exists()
    assert "beta" in (tmp_path / "obpc.log").read_text()


def test_runner_exits_when_scheduler_exits_but_descendant_holds_stdout(tmp_path):
    started = time.monotonic()
    result = subprocess.run(
        [
            sys.executable, str(RUNNER), "--service", "kline", "--log-dir", str(tmp_path),
            "--max-bytes", "1024", "--backup-count", "2", "--terminate-grace-seconds", "1", "--",
            sys.executable, "-c",
            "import subprocess; subprocess.Popen(['sleep', '3']); print('scheduler-exit')",
        ],
        capture_output=True, text=True, timeout=5,
    )
    assert result.returncode == 0
    assert time.monotonic() - started < 2


def test_healthcheck_requires_live_direct_scheduler_child(tmp_path):
    from scripts.persistent_log_runner import scheduler_child_running

    children = tmp_path / "1" / "task" / "1"
    children.mkdir(parents=True)
    children.joinpath("children").write_text("345 346")
    for pid, argv in {
        "345": b"python\0-u\0scripts/scheduler_kline.py\0",
        "346": b"sh\0-c\0grep scheduler_kline.py\0",
    }.items():
        proc = tmp_path / pid
        proc.mkdir()
        proc.joinpath("cmdline").write_bytes(argv)
    assert scheduler_child_running("scripts/scheduler_kline.py", tmp_path)
    (tmp_path / "345" / "cmdline").unlink()
    assert not scheduler_child_running("scripts/scheduler_kline.py", tmp_path)


def test_runner_forwards_container_stop_to_scheduler(tmp_path):
    marker = tmp_path / "stopped"
    script = (
        "import pathlib, signal, sys, time\n"
        "def stop(_signal, _frame):\n"
        f"    pathlib.Path({str(marker)!r}).write_text('stopped')\n"
        "    sys.exit(0)\n"
        "signal.signal(signal.SIGTERM, stop)\n"
        "print('ready', flush=True)\n"
        "time.sleep(20)\n"
    )
    runner = subprocess.Popen(
        [
            sys.executable, str(RUNNER), "--service", "obpc", "--log-dir", str(tmp_path),
            "--max-bytes", "1024", "--backup-count", "2",
            "--terminate-grace-seconds", "1", "--", sys.executable, "-c", script,
        ],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        assert runner.stdout.readline().strip() == "ready"
        runner.terminate()
        runner.wait(timeout=5)
        assert marker.read_text() == "stopped"
        assert "ready" in (tmp_path / "obpc.log").read_text()
    finally:
        if runner.poll() is None:
            runner.kill()
            runner.wait(timeout=5)


def test_archive_captures_each_existing_container_before_recreation(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    docker = fake_bin / "docker"
    docker.write_text(
        "#!/bin/sh\n"
        "if [ \"$1\" = ps ]; then printf 'stockfilter-kline\\nstockfilter-obpc\\nstockfilter-eadvisor\\n'; exit 0; fi\n"
        "if [ \"$1\" = inspect ]; then echo id-$4; exit 0; fi\n"
        "if [ \"$1\" = logs ]; then echo old-log-$4; exit 0; fi\n"
        "exit 2\n"
    )
    docker.chmod(0o755)
    env = dict(os.environ, PATH=f"{fake_bin}:{os.environ['PATH']}")
    archive_dir = tmp_path / "archive"
    result = subprocess.run(
        ["bash", str(ARCHIVER), str(archive_dir)], env=env,
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr
    archives = list(archive_dir.glob("*.log.gz"))
    assert len(archives) == 3
    assert all("old-log" in gzip.open(path, "rt").read() for path in archives)


def test_archive_failure_stops_before_publishing_partial_file(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    docker = fake_bin / "docker"
    docker.write_text(
        "#!/bin/sh\n"
        "if [ \"$1\" = ps ]; then printf 'stockfilter-kline\\nstockfilter-obpc\\nstockfilter-eadvisor\\n'; exit 0; fi\n"
        "if [ \"$1\" = inspect ]; then echo id-$4; exit 0; fi\n"
        "if [ \"$1\" = logs ]; then echo partial-line; exit 1; fi\n"
        "exit 2\n"
    )
    docker.chmod(0o755)
    env = dict(os.environ, PATH=f"{fake_bin}:{os.environ['PATH']}")
    archive_dir = tmp_path / "archive"
    result = subprocess.run(
        ["bash", str(ARCHIVER), str(archive_dir)], env=env,
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode != 0
    assert list(archive_dir.glob("*.log.gz")) == []


def test_archive_inspect_failure_is_not_treated_as_missing_container(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    docker = fake_bin / "docker"
    docker.write_text(
        "#!/bin/sh\n"
        "if [ \"$1\" = ps ]; then echo stockfilter-kline; exit 0; fi\n"
        "if [ \"$1\" = inspect ]; then echo 'permission denied' >&2; exit 1; fi\n"
        "exit 2\n"
    )
    docker.chmod(0o755)
    env = dict(os.environ, PATH=f"{fake_bin}:{os.environ['PATH']}")
    result = subprocess.run(
        ["bash", str(ARCHIVER), str(tmp_path / "archive")], env=env,
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode != 0
    assert "inspect" in result.stderr


def test_runner_stops_independent_worker_group_on_sigterm(tmp_path):
    if not Path("/proc").is_dir():
        pytest.skip("真实进程树 SIGTERM 集成测试需要 Linux /proc")
    marker = tmp_path / "worker-stopped"
    worker = (
        "import pathlib, signal, sys, time\n"
        "def stop(_signal, _frame):\n"
        f"    pathlib.Path({str(marker)!r}).write_text('stopped')\n"
        "    sys.exit(0)\n"
        "signal.signal(signal.SIGTERM, stop)\n"
        "print('worker-ready', flush=True)\n"
        "time.sleep(20)\n"
    )
    scheduler = (
        "import subprocess, sys, time\n"
        f"subprocess.Popen([sys.executable, '-c', {worker!r}], start_new_session=True)\n"
        "time.sleep(20)\n"
    )
    runner = subprocess.Popen(
        [
            sys.executable, str(RUNNER), "--service", "kline", "--log-dir", str(tmp_path),
            "--max-bytes", "1024", "--backup-count", "2",
            "--terminate-grace-seconds", "1", "--", sys.executable, "-c", scheduler,
        ],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        assert runner.stdout.readline().strip() == "worker-ready"
        runner.terminate()
        runner.wait(timeout=5)
        assert marker.exists(), runner.stderr.read()
        assert marker.read_text() == "stopped"
    finally:
        if runner.poll() is None:
            runner.kill()
            runner.wait(timeout=5)


def test_independent_worker_group_is_found_in_proc_tree(tmp_path, monkeypatch):
    from scripts.persistent_log_runner import independent_descendant_groups

    for pid, parent, group in ((100, 1, 100), (101, 100, 100), (102, 100, 102)):
        process = tmp_path / str(pid)
        process.mkdir()
        process.joinpath("stat").write_text(
            f"{pid} (python) S {parent} {group} {group} 0 0 0 0 0 0 0 0 0 0"
        )
    monkeypatch.setattr(os, "getpgid", lambda _pid: 100)
    monkeypatch.setattr(os, "getpgrp", lambda: 50)
    assert independent_descendant_groups(100, tmp_path) == {102}
