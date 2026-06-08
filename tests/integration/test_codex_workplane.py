from __future__ import annotations

from pathlib import Path

from host_brain.codex_runtime import CodexRuntimeExecutor
from host_brain.codex_runtime.artifact_store import CodexArtifactStore
from host_brain.skills.codex_runtime_registry import build_codex_runtime_skill_registry


def test_codex_workplane_runs_from_skill_work_order_and_writes_artifacts(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("host_brain.codex_runtime.executor.codex_cli_command", lambda: None)
    registry = build_codex_runtime_skill_registry()
    payload = registry.require("codex_inspect").handler(
        {"task": "inspect a lightweight snippet"},
        {"request_id": "req-workplane"},
    )

    result = CodexRuntimeExecutor(artifact_store=CodexArtifactStore(tmp_path)).execute(payload["work_order"])

    assert result["status"] == "DEGRADED"
    assert result["work_order_id"] == payload["work_order"]["work_order_id"]
    assert result["changed_files"] == []
    assert result["verification"]["status"] == "NOT_RUN"
    assert result["review_required"] is False
    assert Path(result["artifacts_dir"], "work_order.json").exists()
    assert Path(result["artifacts_dir"], "result.json").exists()
