from __future__ import annotations

from host_brain.agent_runtime.agent_tools import (
    AGENT_TOOL_GOVERNED_ADMISSION_REQUIRED,
)
from host_brain.agent_runtime.executor import AgentRuntimeExecutor
from host_brain.agent_runtime.artifact_store import AgentArtifactStore
from host_brain.agent_runtime.immune import build_immune_proposal_from_report


def test_codex_immuneplane_blocks_self_heal_before_workspace_resolution(
    monkeypatch,
    tmp_path,
) -> None:
    executor_called = False

    class CanonicalFixtureExecutor:
        def __init__(self, **_dependencies: object) -> None:
            pass

        def execute(self, _order, *, workspace: Path):  # noqa: ANN001, ANN201
            nonlocal executor_called
            executor_called = True
            raise AssertionError(f"executor must not run without WorkspaceGrant: {workspace}")

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

    assert result["status"] == "BLOCKED"
    assert result["reason_code"] == AGENT_TOOL_GOVERNED_ADMISSION_REQUIRED
    assert result["review_required"] is True
    assert result["risk_flags"] == [AGENT_TOOL_GOVERNED_ADMISSION_REQUIRED]
    assert result["metadata"]["execution_authority"] == "proposal_only_agent_executor"
    assert executor_called is False
    assert result["artifacts_dir"] == ""
