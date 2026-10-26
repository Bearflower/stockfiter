"""发布工作流不得把纯文档 push 或未通过健康门槛记为成功。"""

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]


def test_nested_markdown_does_not_trigger_push_workflow():
    workflow = yaml.safe_load((ROOT / ".github/workflows/deploy.yml").read_text())
    patterns = workflow.get("on", workflow.get(True))["push"]["paths-ignore"]
    assert "**/*.md" in patterns
    assert "*.md" in patterns


def test_deploys_are_queued_and_build_comparison_uses_full_push_range():
    source = (ROOT / ".github/workflows/deploy.yml").read_text()
    assert "cancel-in-progress: false" in source
    assert "fetch-depth: 0" in source
    assert "github.event.before" in source
    assert "HEAD~1" not in source


def test_release_requires_three_healthy_containers_before_success():
    script = (ROOT / "scripts/deploy_release.sh").read_text()
    assert "stockfilter-kline stockfilter-obpc stockfilter-eadvisor" in script
    assert "State.Health.Status" in script
    assert script.index("State.Health.Status") < script.index("DEPLOY_SUCCESS")
    assert 'monitor_deploy.py" verify' in script
    assert script.index('monitor_deploy.py" verify') < script.index("DEPLOY_SUCCESS")
    assert "PARTIAL_FAILURE" in script
    assert "monitor_deploy.py" in script
    assert 'docker tag "$old_image" "$rollback_tag"' in script
    assert "docker image prune -f" not in script


def test_release_supports_isolated_test_root_without_touching_production():
    script = (ROOT / "scripts/deploy_release.sh").read_text()
    assert "STOCKFILTER_DEPLOY_ROOT" in script
