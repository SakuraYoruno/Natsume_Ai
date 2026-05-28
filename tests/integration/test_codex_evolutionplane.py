from __future__ import annotations

from pathlib import Path

from host_brain.codex_runtime import CodexRuntimeExecutor
from host_brain.codex_runtime.artifact_store import CodexArtifactStore
from host_brain.skills.codex_runtime_registry import build_codex_runtime_skill_registry


def test_codex_evolutionplane_blocks_outside_experiment_branch(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("host_brain.codex_runtime.executor.codex_cli_command", lambda: "codex")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    registry = build_codex_runtime_skill_registry()
    payload = registry.require("codex_self_evolve").handler(
        {"task": "try an evolution experiment", "workspace": {"path": str(workspace)}, "confirmed": True},
        {"request_id": "req-evolution"},
    )

    result = CodexRuntimeExecutor(artifact_store=CodexArtifactStore(tmp_path / "runs")).execute(payload["work_order"])

    assert result["status"] == "BLOCKED"
    assert result["reason_code"] == "EVOLUTION_BRANCH_REQUIRED"
    assert result["review_required"] is True
    assert "evolution_branch_required" in result["risk_flags"]
    assert Path(result["artifacts_dir"], "result.json").exists()
