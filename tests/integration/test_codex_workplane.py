from __future__ import annotations

from pathlib import Path

from host_brain.agent_runtime.executor import AgentRuntimeExecutor
from host_brain.agent_runtime.artifact_store import AgentArtifactStore
from host_brain.agent_runtime.result_contract import AgentResult
from host_brain.skills.agent_runtime_registry import build_agent_runtime_skill_registry


def test_codex_workplane_runs_from_skill_work_order_and_writes_artifacts(monkeypatch, tmp_path) -> None:
    class CanonicalFixtureExecutor:
        def __init__(self, **dependencies: object) -> None:
            assert dependencies["model_attempt_service"] is model_attempt_service
            assert dependencies["prompt_registry"] is prompt_registry

        def execute(self, order, *, workspace: Path) -> AgentResult:  # noqa: ANN001
            return AgentResult(
                status="EXECUTED",
                work_order_id=order.work_order_id,
                summary="Canonical inspect fixture completed.",
                result_text=f"inspected {workspace.name}",
                review_required=False,
            )

    model_attempt_service = object()
    prompt_registry = object()
    monkeypatch.setattr(
        "host_brain.agent_runtime.executor.AgentExecutor",
        CanonicalFixtureExecutor,
    )
    registry = build_agent_runtime_skill_registry()
    payload = registry.require("agent_inspect").handler(
        {"task": "inspect a lightweight snippet"},
        {"request_id": "req-workplane"},
    )

    result = AgentRuntimeExecutor(
        artifact_store=AgentArtifactStore(tmp_path),
        model_attempt_service=model_attempt_service,
        prompt_registry=prompt_registry,
    ).execute(payload["work_order"])

    assert result["status"] == "EXECUTED"
    assert result["work_order_id"] == payload["work_order"]["work_order_id"]
    assert result["changed_files"] == []
    assert result["verification"]["status"] == "NOT_RUN"
    assert result["review_required"] is False
    assert result["metadata"]["execution_authority"] == "canonical_agent_executor"
    assert Path(result["artifacts_dir"], "work_order.json").exists()
    assert Path(result["artifacts_dir"], "result.json").exists()
