from __future__ import annotations

from pathlib import Path

from host_brain.agent_runtime.executor import AgentRuntimeExecutor
from host_brain.agent_runtime.artifact_store import AgentArtifactStore
from host_brain.agent_runtime.result_contract import AgentResult
from host_brain.agent_runtime.immune import build_immune_proposal_from_report


def test_codex_immuneplane_builds_and_executes_self_heal_work_order(monkeypatch, tmp_path) -> None:
    class CanonicalFixtureExecutor:
        def __init__(self, **dependencies: object) -> None:
            assert dependencies["model_attempt_service"] is model_attempt_service
            assert dependencies["prompt_registry"] is prompt_registry

        def execute(self, order, *, workspace: Path) -> AgentResult:  # noqa: ANN001
            return AgentResult(
                status="EXECUTED",
                work_order_id=order.work_order_id,
                summary="Canonical self-heal fixture completed.",
                result_text=f"checked {workspace.name}",
                review_required=True,
            )

    model_attempt_service = object()
    prompt_registry = object()
    monkeypatch.setattr(
        "host_brain.agent_runtime.executor.AgentExecutor",
        CanonicalFixtureExecutor,
    )
    proposal = build_immune_proposal_from_report(
        {
            "overall_status": "DEGRADED",
            "reason_code": "SERVICE_UNAVAILABLE_DEPENDENCY",
            "components": {"edge.rpc.tts": {"status": "DOWN"}},
        },
        source="health",
    )

    assert proposal["triggered"] is True
    work_order = proposal["work_order"]
    assert work_order["domain"] == "immune"
    assert work_order["run_mode"] == "self_heal"
    approval_policy = dict(work_order["approval_policy"])
    approval_policy["confirmed"] = True
    work_order["approval_policy"] = approval_policy

    result = AgentRuntimeExecutor(
        artifact_store=AgentArtifactStore(tmp_path),
        model_attempt_service=model_attempt_service,
        prompt_registry=prompt_registry,
    ).execute(work_order)

    assert result["status"] == "EXECUTED"
    assert result["review_required"] is True
    assert result["risk_flags"] == []
    assert result["metadata"]["execution_authority"] == "canonical_agent_executor"
    assert Path(result["artifacts_dir"], "work_order.json").exists()
