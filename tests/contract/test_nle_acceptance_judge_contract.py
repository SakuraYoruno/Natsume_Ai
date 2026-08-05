from __future__ import annotations

from pathlib import Path
from typing import Any

from host_brain.contracts.agent_runtime.acceptance_judge import (
    ACCEPTANCE_CRITERION_FAILED,
    AcceptanceCriterionStatus,
    AcceptanceDecision,
    AcceptanceJudgement,
    judge_acceptance_result,
)
from host_brain.agent_runtime.artifact_store import AgentArtifactStore
from host_brain.agent_runtime.executor import AgentRuntimeExecutor
from host_brain.agent_runtime.result_contract import AgentResult


def _executed_result(*, returncode: int = 0) -> dict[str, Any]:
    return {
        "status": "EXECUTED",
        "work_order_id": "wo-accept",
        "summary": "Agent WorkOrder executed.",
        "result_text": "exit code is zero",
        "verification": {
            "status": "PASS",
            "evidence_refs": ["verification://verify-accept/check/1/pass"],
        },
        "metadata": {"returncode": returncode},
    }


def test_t_nle_09_each_acceptance_criterion_has_verdict_and_evidence_ref() -> None:
    judgement = judge_acceptance_result(
        _executed_result(returncode=0),
        work_order_id="wo-accept",
        acceptance_criteria=("exit code is zero", "Agent WorkOrder executed."),
        task_graph_id="graph-1",
        task_node_id="node-1",
    )

    restored = AcceptanceJudgement.from_mapping(judgement.to_dict())

    assert restored.decision is AcceptanceDecision.ACCEPT
    assert [item.verdict for item in restored.criteria] == [
        AcceptanceCriterionStatus.PASS,
        AcceptanceCriterionStatus.PASS,
    ]
    assert all(item.evidence_refs for item in restored.criteria)
    assert all(ref.startswith(("acceptance://", "verification://")) for ref in restored.evidence_refs)


def test_t_nle_09_deterministic_fail_cannot_be_overridden_by_critic_accept() -> None:
    judgement = judge_acceptance_result(
        _executed_result(returncode=1),
        work_order_id="wo-accept",
        acceptance_criteria=("exit code is zero",),
        critic_candidate={"decision": "ACCEPT", "reason": "LLM thinks it is fine"},
        task_graph_id="graph-1",
        task_node_id="node-1",
    )

    assert judgement.decision is AcceptanceDecision.FAIL
    assert judgement.reason_code == ACCEPTANCE_CRITERION_FAILED
    assert judgement.criteria[0].verdict is AcceptanceCriterionStatus.FAIL


def test_t_nle_09_running_execution_never_accepts_matching_output() -> None:
    payload = _executed_result(returncode=0)
    payload["status"] = "RUNNING"
    judgement = judge_acceptance_result(
        payload,
        work_order_id="wo-accept",
        acceptance_criteria=("exit code is zero",),
        critic_candidate={"decision": "ACCEPT"},
        task_graph_id="graph-1",
        task_node_id="node-1",
    )

    assert judgement.decision is AcceptanceDecision.FAIL
    assert judgement.criteria[0].verdict is AcceptanceCriterionStatus.FAIL


def test_t_nle_09_executor_attaches_acceptance_judgement(monkeypatch, tmp_path: Path) -> None:
    class FakeAgentExecutor:
        def __init__(self, **_ports) -> None:  # noqa: ANN003
            pass

        def execute(self, order, *, workspace: Path, **_kwargs) -> AgentResult:  # noqa: ANN003
            return AgentResult(
                status="EXECUTED",
                work_order_id=order.work_order_id,
                summary="Agent WorkOrder executed.",
                result_text="exit code is zero",
                review_required=False,
                metadata={"returncode": 0},
            )

    monkeypatch.setattr("host_brain.agent_runtime.executor.AgentExecutor", FakeAgentExecutor)

    result = AgentRuntimeExecutor(
        artifact_store=AgentArtifactStore(tmp_path / "runs"),
        model_attempt_service=object(),
        prompt_registry=object(),
    ).execute(
        {
            "work_order_id": "wo-accept",
            "domain": "code",
            "run_mode": "inspect",
            "objective": "inspect something",
            "workspace": {"strategy": "temp_workspace", "runs_root": str(tmp_path / "workspaces")},
            "allowed_actions": ["read_files", "generate_report"],
            "forbidden_actions": ["write_files", "modify_secret_files"],
            "acceptance_criteria": ["exit code is zero"],
            "review_policy": {"review_required": False},
        }
    )

    assert result["status"] == "EXECUTED"
    assert result["acceptance"]["decision"] == "ACCEPT"
    assert result["acceptance"]["criteria"][0]["verdict"] == "PASS"
    assert result["acceptance"]["criteria"][0]["evidence_refs"]
