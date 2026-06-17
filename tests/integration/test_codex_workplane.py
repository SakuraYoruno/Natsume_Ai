from __future__ import annotations

from pathlib import Path

from host_brain.agent_runtime import AgentRuntimeExecutor
from host_brain.agent_runtime.artifact_store import AgentArtifactStore
from host_brain.skills.agent_runtime_registry import build_agent_runtime_skill_registry


def test_codex_workplane_runs_from_skill_work_order_and_writes_artifacts(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("host_brain.agent_runtime.executor.codex_cli_command", lambda: None)
    registry = build_agent_runtime_skill_registry()
    payload = registry.require("agent_inspect").handler(
        {"task": "inspect a lightweight snippet"},
        {"request_id": "req-workplane"},
    )

    result = AgentRuntimeExecutor(artifact_store=AgentArtifactStore(tmp_path)).execute(payload["work_order"])

    assert result["status"] == "DEGRADED"
    assert result["work_order_id"] == payload["work_order"]["work_order_id"]
    assert result["changed_files"] == []
    assert result["verification"]["status"] == "NOT_RUN"
    assert result["review_required"] is False
    assert Path(result["artifacts_dir"], "work_order.json").exists()
    assert Path(result["artifacts_dir"], "result.json").exists()
