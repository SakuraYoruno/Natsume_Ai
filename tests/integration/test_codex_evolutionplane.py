from __future__ import annotations

from pathlib import Path

from host_brain.agent_runtime.executor import AgentRuntimeExecutor
from host_brain.agent_runtime.artifact_store import AgentArtifactStore
from host_brain.skills.agent_runtime_registry import build_agent_runtime_skill_registry


def test_codex_evolutionplane_blocks_outside_experiment_branch(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    registry = build_agent_runtime_skill_registry()
    payload = registry.require("agent_self_evolve").handler(
        {"task": "try an evolution experiment", "workspace": {"path": str(workspace)}, "confirmed": True},
        {"request_id": "req-evolution"},
    )

    result = AgentRuntimeExecutor(artifact_store=AgentArtifactStore(tmp_path / "runs")).execute(payload["work_order"])

    assert result["status"] == "BLOCKED"
    assert result["reason_code"] == "EVOLUTION_BRANCH_REQUIRED"
    assert result["review_required"] is True
    assert "evolution_branch_required" in result["risk_flags"]
    assert Path(result["artifacts_dir"], "result.json").exists()
