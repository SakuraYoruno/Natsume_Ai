from __future__ import annotations

from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_ROOT = REPO_ROOT / ".github" / "workflows"


def test_github_workflows_opt_into_node24_action_runtime() -> None:
    workflow_paths = sorted(WORKFLOW_ROOT.glob("*.yml"))
    assert workflow_paths

    for path in workflow_paths:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        env = dict(payload.get("env") or {})
        assert env.get("FORCE_JAVASCRIPT_ACTIONS_TO_NODE24") == "true", path.name
        assert "ACTIONS_ALLOW_USE_UNSECURE_NODE_VERSION" not in env, path.name
