"""隔离故障注入：发布脚本不得把归档或健康失败记成成功。"""

import os
from pathlib import Path
import subprocess

import pytest


REPO = Path(__file__).resolve().parents[2]
RELEASE = REPO / "scripts" / "deploy_release.sh"
REVISION = "a" * 40


@pytest.fixture
def release_env(tmp_path):
    root = tmp_path / "project"
    (root / "scripts").mkdir(parents=True)
    (root / "config").mkdir()
    (root / "docker-compose.yml").write_text("old compose\n")
    (root / "config" / "config.yaml").write_text("old config\n")
    (root / "scripts" / "monitor.sh").write_text("#!/bin/bash\n")
    trace = tmp_path / "trace.log"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    docker = fake_bin / "docker"
    docker.write_text(
        "#!/bin/sh\n"
        "echo docker:$* >> \"$DEPLOY_TRACE\"\n"
        "case \"$1\" in\n"
        "  login|tag) exit 0;;\n"
        "  ps) printf 'stockfilter-kline\\nstockfilter-obpc\\nstockfilter-eadvisor\\n'; exit 0;;\n"
        "  logs) if [ \"$DEPLOY_SCENARIO\" = archive_fail ] && [ \"$3\" = stockfilter-obpc ]; then exit 1; fi; echo '2026-10-26T00:00:00Z old'; exit 0;;\n"
        "  inspect) case \"$3\" in\n"
        "    *State.Health.Status*) if [ \"$DEPLOY_SCENARIO\" = health_fail ]; then echo unhealthy; "
        "elif [ \"$DEPLOY_SCENARIO\" = recheck_fail ] && [ \"$4\" = stockfilter-kline ]; then "
        "if [ -f \"$DEPLOY_HEALTH_SEEN\" ]; then echo unhealthy; else touch \"$DEPLOY_HEALTH_SEEN\"; echo healthy; fi; "
        "else echo healthy; fi;;\n"
        "    *Config.Labels*) echo 'oldid|sha256:oldimage|<no value>';;\n"
        "    *) echo 'sha256:oldimage';;\n"
        "  esac; exit 0;;\n"
        "  compose) exit 0;;\n"
        "esac\n"
        "exit 2\n"
    )
    docker.chmod(0o755)
    stub = tmp_path / "monitor_deploy_stub.py"
    stub.write_text(
        "import os, sys\n"
        "with open(os.environ['DEPLOY_TRACE'], 'a') as out: out.write('monitor:' + sys.argv[1] + '\\n')\n"
    )
    curl = fake_bin / "curl"
    curl.write_text(
        "#!/bin/sh\n"
        "while [ \"$#\" -gt 0 ]; do\n"
        "  if [ \"$1\" = -o ]; then dest=\"$2\"; shift 2; else shift; fi\n"
        "done\n"
        "case \"${dest##*/}\" in\n"
        "  monitor_deploy.py) cp \"$MONITOR_STUB\" \"$dest\";;\n"
        "  docker-compose.yml) cp \"$REPO_ROOT/docker-compose.yml\" \"$dest\";;\n"
        "  config.yaml) cp \"$REPO_ROOT/config/config.yaml\" \"$dest\";;\n"
        "  *) cp \"$REPO_ROOT/scripts/${dest##*/}\" \"$dest\";;\n"
        "esac\n"
    )
    curl.chmod(0o755)
    env = dict(
        os.environ, PATH=f"{fake_bin}:{os.environ['PATH']}",
        STOCKFILTER_DEPLOY_ROOT=str(root), DEPLOY_TRACE=str(trace),
        REPO_ROOT=str(REPO), MONITOR_STUB=str(stub),
        DEPLOY_HEALTH_SEEN=str(tmp_path / "health-seen"),
        DEPLOY_HEALTH_ATTEMPTS="1", DEPLOY_HEALTH_INTERVAL_SECONDS="1",
    )
    return root, trace, env


def run_release(env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(RELEASE), REVISION, "12345"], input="test-token\n",
        text=True, capture_output=True, env=env, timeout=15,
    )


def test_archive_failure_recovers_old_containers_before_any_up(release_env):
    root, trace, env = release_env
    env["DEPLOY_SCENARIO"] = "archive_fail"
    result = run_release(env)
    events = trace.read_text()
    assert result.returncode != 0, result.stdout
    assert "monitor:pause" in events and "monitor:restore" in events
    assert "docker:compose stop" in events and "docker:compose start" in events
    assert "docker:compose up" not in events
    logs = "".join(p.read_text() for p in (root / "deploy_logs").glob("*.log"))
    assert "DEPLOY_SUCCESS" not in logs and "PARTIAL_FAILURE" in logs


def test_health_failure_is_partial_and_restores_monitor(release_env):
    root, trace, env = release_env
    env["DEPLOY_SCENARIO"] = "health_fail"
    result = run_release(env)
    events = trace.read_text()
    logs = "".join(p.read_text() for p in (root / "deploy_logs").glob("*.log"))
    assert result.returncode != 0, result.stdout
    assert "docker:compose up -d" in events
    assert "monitor:activate" not in events
    assert "monitor:restore" in events
    assert "PARTIAL_FAILURE" in logs and "DEPLOY_SUCCESS" not in logs


def test_success_requires_monitor_verify_after_health(release_env):
    root, trace, env = release_env
    env["DEPLOY_SCENARIO"] = "success"
    result = run_release(env)
    events = trace.read_text()
    logs = "".join(p.read_text() for p in (root / "deploy_logs").glob("*.log"))
    assert result.returncode == 0, result.stderr
    assert events.index("docker:compose up -d") < events.index("monitor:activate")
    assert events.index("monitor:activate") < events.index("monitor:verify")
    assert "DEPLOY_SUCCESS" in logs and "PARTIAL_FAILURE" not in logs
    assert len(list((root / "logs" / "container-archive").glob("*/*.log.gz"))) == 3


def test_recheck_catches_first_container_becoming_unhealthy_before_success(release_env):
    root, trace, env = release_env
    env["DEPLOY_SCENARIO"] = "recheck_fail"
    result = run_release(env)
    events = trace.read_text()
    logs = "".join(p.read_text() for p in (root / "deploy_logs").glob("*.log"))
    assert result.returncode != 0, result.stdout
    assert "monitor:verify" in events
    assert "monitor:restore" in events
    assert "PARTIAL_FAILURE" in logs and "DEPLOY_SUCCESS" not in logs
