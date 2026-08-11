from __future__ import annotations

from host_brain.agent_runtime.executor import AgentRuntimeExecutor
from host_brain.agent_runtime.artifact_store import AgentArtifactStore
from host_brain.agent_runtime.agent_tools import AGENT_TOOL_GOVERNED_ADMISSION_REQUIRED
from host_brain.skills.agent_runtime_registry import build_agent_runtime_skill_registry


def test_codex_evolutionplane_blocks_direct_ungoverned_execution(monkeypatch, tmp_path) -> None:
    import host_brain.agent_runtime.workspace_policy as workspace_policy_module

    monkeypatch.setattr(workspace_policy_module, "DEFAULT_CODEX_RUNS_ROOT", tmp_path / "workspaces")
    registry = build_agent_runtime_skill_registry()
    payload = registry.require("agent_self_evolve").handler(
        {
            "task": "try an evolution experiment",
            "workspace": {"strategy": "temp_workspace"},
            "confirmed": True,
        },
        {"request_id": "req-evolution"},
    )

    result = AgentRuntimeExecutor(artifact_store=AgentArtifactStore(tmp_path / "runs")).execute(payload["work_order"])

    assert result["status"] == "BLOCKED"
    assert result["reason_code"] == AGENT_TOOL_GOVERNED_ADMISSION_REQUIRED
    assert result["review_required"] is True
    assert result["risk_flags"] == [AGENT_TOOL_GOVERNED_ADMISSION_REQUIRED]
    assert not result.get("artifacts_dir")
