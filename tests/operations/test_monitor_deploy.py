"""宿主机监控切换必须精确定位进程，禁止误杀或双实例。"""

from pathlib import Path
import json
import time

import pytest


def fake_process(root: Path, pid: int, argv: list[str], cwd: Path) -> None:
    process = root / str(pid)
    process.mkdir(parents=True)
    (process / "cmdline").write_bytes(b"\0".join(arg.encode() for arg in argv) + b"\0")
    (process / "cwd").symlink_to(cwd, target_is_directory=True)


def test_monitor_identity_requires_exact_script_and_cwd(tmp_path):
    from scripts.monitor_deploy import monitor_pids

    script_dir = tmp_path / "scripts"
    script_dir.mkdir()
    other_dir = tmp_path / "other"
    other_dir.mkdir()
    proc = tmp_path / "proc"
    proc.mkdir()
    fake_process(proc, 100, ["bash", "monitor.sh"], script_dir)
    fake_process(proc, 101, ["bash", "monitor.sh"], other_dir)
    fake_process(proc, 102, ["bash", "monitor.sh.backup"], script_dir)
    fake_process(proc, 103, ["bash", str(script_dir / "monitor.sh")], script_dir)
    assert monitor_pids(script_dir, proc) == [100, 103]


def test_pause_rejects_missing_or_multiple_monitor_instances(tmp_path):
    from scripts.monitor_deploy import pause_monitor

    script_dir = tmp_path / "scripts"
    script_dir.mkdir()
    (script_dir / "monitor.sh").write_text("#!/bin/bash\nexit 0\n")
    proc = tmp_path / "proc"
    proc.mkdir()
    with pytest.raises(RuntimeError, match="恰好一个"):
        pause_monitor(script_dir, tmp_path / "state", proc_root=proc)
    fake_process(proc, 100, ["bash", "monitor.sh"], script_dir)
    fake_process(proc, 101, ["bash", "monitor.sh"], script_dir)
    with pytest.raises(RuntimeError, match="恰好一个"):
        pause_monitor(script_dir, tmp_path / "state", proc_root=proc)


def test_activate_and_restore_never_leave_new_script_as_old_fallback(tmp_path, monkeypatch):
    import scripts.monitor_deploy as deploy

    script_dir = tmp_path / "scripts"
    script_dir.mkdir()
    old_script = script_dir / "monitor.sh"
    old_script.write_text("#!/bin/bash\necho old\n")
    candidate = tmp_path / "new-monitor.sh"
    candidate.write_text("#!/bin/bash\necho new\n")
    state = tmp_path / "state"
    state.mkdir()
    (state / "monitor.sh.old").write_bytes(old_script.read_bytes())
    (state / "monitor-state.json").write_text(json.dumps({"phase": "paused", "old_pid": 123}))
    pids = []
    stopped = []
    readiness_modes = []
    monkeypatch.setattr(deploy, "monitor_pids", lambda *_args: list(pids))
    def start_monitor(*_args, require_ready=True):
        readiness_modes.append(require_ready)
        pids.append(555)
        return 555
    monkeypatch.setattr(deploy, "_start_monitor", start_monitor)
    monkeypatch.setattr(deploy, "_stop_monitor", lambda pid, *_args: stopped.append(pid) or pids.clear())

    deploy.activate_monitor(script_dir, state, candidate)
    assert old_script.read_bytes() == candidate.read_bytes()
    assert pids == [555]
    deploy.restore_monitor(script_dir, state)
    assert readiness_modes == [True, False]
    assert stopped == [555]
    assert old_script.read_text() == "#!/bin/bash\necho old\n"
    assert pids == [555]
    assert json.loads((state / "monitor-state.json").read_text())["phase"] == "restored"


def test_start_monitor_rejects_process_that_dies_after_brief_pid_visibility(tmp_path, monkeypatch):
    import scripts.monitor_deploy as deploy

    script_dir = tmp_path / "scripts"
    script_dir.mkdir()
    (tmp_path / "logs").mkdir()
    started = time.monotonic()

    class ShortLived:
        pid = 777
        returncode = 7

        def poll(self):
            return None if time.monotonic() - started < 0.15 else self.returncode

    monkeypatch.setenv("MONITOR_START_STABLE_SECONDS", "0.4")
    monkeypatch.setattr(deploy.subprocess, "Popen", lambda *_args, **_kwargs: ShortLived())
    identity_checks = iter(([], [777], [777], [777], [777]))
    monkeypatch.setattr(deploy, "monitor_pids", lambda *_args: next(identity_checks, [777]))
    with pytest.raises(RuntimeError, match="启动即退出"):
        deploy._start_monitor(script_dir, tmp_path / "proc")


def test_invalid_stability_config_cannot_spawn_monitor(tmp_path, monkeypatch):
    import scripts.monitor_deploy as deploy

    script_dir = tmp_path / "scripts"
    script_dir.mkdir()
    (tmp_path / "logs").mkdir()
    monkeypatch.setenv("MONITOR_START_STABLE_SECONDS", "nan")
    monkeypatch.setattr(deploy, "monitor_pids", lambda *_args: [])
    calls = []
    monkeypatch.setattr(deploy.subprocess, "Popen", lambda *_args, **_kwargs: calls.append(1))
    with pytest.raises(ValueError, match="MONITOR_START_STABLE_SECONDS"):
        deploy._start_monitor(script_dir, tmp_path / "proc")
    assert calls == []


def test_long_lived_monitor_without_ready_marker_is_not_accepted(tmp_path, monkeypatch):
    import scripts.monitor_deploy as deploy

    script_dir = tmp_path / "scripts"
    script_dir.mkdir()
    (tmp_path / "logs").mkdir()

    class Alive:
        pid = 777
        returncode = None

        def poll(self):
            return None

    monkeypatch.setenv("MONITOR_START_STABLE_SECONDS", "0.05")
    monkeypatch.setenv("MONITOR_READY_TIMEOUT_SECONDS", "0.2")
    monkeypatch.setattr(deploy.subprocess, "Popen", lambda *_args, **_kwargs: Alive())
    identity_checks = iter(([], [777], [777], [777]))
    monkeypatch.setattr(deploy, "monitor_pids", lambda *_args: next(identity_checks, [777]))
    with pytest.raises(RuntimeError, match="就绪"):
        deploy._start_monitor(script_dir, tmp_path / "proc")


def test_verify_requires_ready_marker_for_active_pid(tmp_path, monkeypatch):
    import scripts.monitor_deploy as deploy

    script_dir = tmp_path / "scripts"
    script_dir.mkdir()
    (tmp_path / "logs").mkdir()
    state = tmp_path / "state"
    state.mkdir()
    (state / "monitor-state.json").write_text(json.dumps({"phase": "active", "new_pid": 777}))
    monkeypatch.setattr(deploy, "monitor_pids", lambda *_args: [777])
    with pytest.raises(RuntimeError, match="就绪"):
        deploy.verify_monitor(script_dir, state, tmp_path / "proc")


def test_start_accepts_unique_process_only_after_fresh_ready_marker(tmp_path, monkeypatch):
    import scripts.monitor_deploy as deploy

    script_dir = tmp_path / "scripts"
    script_dir.mkdir()
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "monitor.ready").write_text("old-pid\n")

    class Alive:
        pid = 777

        def poll(self):
            return None

    def start(*_args, **_kwargs):
        assert not (logs / "monitor.ready").exists()
        (logs / "monitor.ready").write_text("777\n")
        return Alive()

    monkeypatch.setenv("MONITOR_START_STABLE_SECONDS", "0.05")
    monkeypatch.setenv("MONITOR_READY_TIMEOUT_SECONDS", "0.5")
    monkeypatch.setattr(deploy.subprocess, "Popen", start)
    identity_checks = iter(([], [777], [777]))
    monkeypatch.setattr(deploy, "monitor_pids", lambda *_args: next(identity_checks, [777]))
    assert deploy._start_monitor(script_dir, tmp_path / "proc") == 777


def test_legacy_monitor_restore_can_be_verified_without_new_ready_protocol(tmp_path, monkeypatch):
    import scripts.monitor_deploy as deploy

    script_dir = tmp_path / "scripts"
    script_dir.mkdir()
    (tmp_path / "logs").mkdir()

    class Alive:
        pid = 777

        def poll(self):
            return None

    monkeypatch.setenv("MONITOR_START_STABLE_SECONDS", "0.05")
    monkeypatch.setattr(deploy.subprocess, "Popen", lambda *_args, **_kwargs: Alive())
    identities = iter(([], [777], [777]))
    monkeypatch.setattr(deploy, "monitor_pids", lambda *_args: next(identities, [777]))
    assert deploy._start_monitor(script_dir, tmp_path / "proc", require_ready=False) == 777
