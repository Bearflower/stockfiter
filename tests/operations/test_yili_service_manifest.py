"""伊利独立容器编排与发布健康门槛。"""

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]


def test_yili_service_uses_separate_scheduler_and_persistent_state():
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    service = compose["services"]["strategy-yili"]
    assert service["container_name"] == "stockfilter-yili"
    assert "scripts/scheduler_yili.py" in service["command"]
    assert "scripts/scheduler_yili.py" in service["healthcheck"]["test"]
    assert any("./signals/yili_swing:/app/signals/yili_swing" in volume
               for volume in service["volumes"])


def test_release_health_gate_includes_yili_but_legacy_backup_does_not_require_it():
    script = (ROOT / "scripts/deploy_release.sh").read_text(encoding="utf-8")
    legacy = "for container in stockfilter-kline stockfilter-obpc stockfilter-eadvisor; do"
    new = legacy.replace("; do", " stockfilter-yili; do")
    assert script.count(legacy) == 1
    assert script.count(new) == 2
    assert "docker inspect stockfilter-yili >/dev/null 2>&1" in script
