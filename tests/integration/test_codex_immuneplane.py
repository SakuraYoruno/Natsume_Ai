from __future__ import annotations

from pathlib import Path

from host_brain.agent_runtime import AgentRuntimeExecutor
from host_brain.agent_runtime.artifact_store import AgentArtifactStore
from host_brain.agent_runtime.immune import build_immune_proposal_from_report


def test_codex_immuneplane_builds_and_executes_self_heal_work_order(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("host_brain.agent_runtime.executor.codex_cli_command", lambda: None)
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

    result = AgentRuntimeExecutor(artifact_store=AgentArtifactStore(tmp_path)).execute(work_order)

    assert result["status"] == "DEGRADED"
    assert result["review_required"] is True
    assert result["risk_flags"] == ["codex_cli_unavailable"]
    assert Path(result["artifacts_dir"], "work_order.json").exists()
