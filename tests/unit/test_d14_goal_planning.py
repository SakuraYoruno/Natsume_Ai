from __future__ import annotations

from dataclasses import replace
from typing import Any, Mapping

import pytest

from host_brain.contracts.agent_runtime.budget_policy import BUDGET_POLICY_SCHEMA_VERSION
from host_brain.contracts.agent_runtime.contract_errors import ContractViolation
from host_brain.orchestration.goal_planning import (
    GoalPlanningRequest,
    GoalPlanningService,
    PlanningDomainPorts,
    build_replan_goal_request,
)
from host_brain.orchestration.planning_graph_compiler import (
    PlanningCompilationContext,
    PlanningGraphCompiler,
)
from host_brain.planning_search import (
    MCTSMode,
    PlanningGoal,
    SearchCandidate,
    SearchBudget,
    VirtualStateSnapshot,
)


class _Catalog:
    def expand(
        self,
        *,
        state: Any,
        path: tuple[SearchCandidate, ...],
        depth: int,
        limit: int,
        context: Mapping[str, Any],
    ) -> tuple[SearchCandidate, ...]:
        del state, path, depth, context
        return (SearchCandidate("inspect", prior_score=0.8, payload=_node_payload()),)[:limit]


def _node_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "worker_route": "skill",
        "target_id": "code-inspector",
        "objective": "Inspect the frozen repository state",
        "acceptance_criteria": ["inspection evidence is attached"],
        "input_refs": ["artifact://planning/request"],
        "output_contract": {"type": "inspection", "required": ["evidence_ref"]},
        "retry_policy": {"max_attempts": 1, "retry_backoff_ms": 0},
        "risk_level": 1,
        "capability_requirements": ["read_repository"],
    }
    payload.update(overrides)
    return payload


def _goal() -> PlanningGoal:
    return PlanningGoal(
        goal_id="goal-code-repair",
        domain="code",
        objective="Repair the reported boundary without touching the main workspace",
        success_criteria=("all declared checks pass",),
        frozen_context_refs=("artifact://requests/goal-code-repair",),
    )


def _state(*, issue: str = "null-boundary") -> VirtualStateSnapshot:
    return VirtualStateSnapshot(
        domain="code",
        capabilities=("inspect", "patch", "unit", "integration", "rollback"),
        facts={"issue": issue, "nested": {"paths": ["src/quantity.py"]}},
        constraints={"workspace": "isolated-worktree", "production_influence": 0},
        source_refs=("artifact://snapshots/code-base",),
    )


def _request(*, state: VirtualStateSnapshot | None = None) -> GoalPlanningRequest:
    return GoalPlanningRequest(
        request_id="request-goal-code-repair",
        goal=_goal(),
        state=state or _state(),
        budget=SearchBudget(max_candidates=4, max_expansions=2, max_rollouts=0),
        mcts_mode=MCTSMode.NEVER,
    )


def _context(*, state: VirtualStateSnapshot) -> PlanningCompilationContext:
    return PlanningCompilationContext(
        domain="code",
        run_mode="worker",
        plan_objective="Repair the reported boundary without touching the main workspace",
        allowed_actions=("read_files",),
        forbidden_actions=("push",),
        verification_plan={"required": True, "type": "evidence"},
        approval_policy={"required": True, "mode": "governed"},
        review_policy={"review_required": True, "mode": "governed"},
        budget_policy={
            "schema_version": BUDGET_POLICY_SCHEMA_VERSION,
            "scope": "goal-code-repair",
            "limits": {
                "token_count": 4_000,
                "tool_call_count": 4,
                "time_ms": 60_000,
                "concurrency": 1,
                "retry_count": 0,
                "model_cost_micros": 100_000,
            },
            "on_exceeded": "BLOCK",
        },
        business_idempotency_scope="goal-code-repair",
        approval_ref="approval://goal-code-repair",
        review_ref="review://goal-code-repair",
        workspace={"strategy": "temp_workspace"},
        input_pack={"request_ref": "artifact://planning/request"},
        input_refs=("artifact://planning/context",),
        goal=_goal(),
        virtual_state=state,
    )


def test_virtual_state_is_deeply_frozen_and_digest_is_input_mutation_safe() -> None:
    source = {"nested": {"paths": ["src/quantity.py"]}}
    snapshot = VirtualStateSnapshot(
        domain="code",
        capabilities=("inspect",),
        facts=source,
        constraints={"mode": "shadow"},
    )
    original_digest = snapshot.state_digest
    source["nested"]["paths"].append("outside.py")

    assert snapshot.identity_dict()["facts"] == {"nested": {"paths": ["src/quantity.py"]}}
    assert snapshot.state_digest == original_digest
    with pytest.raises(TypeError):
        snapshot.facts["nested"] = {}  # type: ignore[index]
    with pytest.raises(TypeError):
        snapshot.facts["nested"]["paths"][0] = "tampered"  # type: ignore[index]


def test_goal_service_carries_snapshot_provenance_without_executing_side_effects() -> None:
    request = _request()
    decision = GoalPlanningService(ports=PlanningDomainPorts(successors=_Catalog())).search(request)

    assert decision.result.selected_candidate is not None
    assert decision.result.selected_candidate.candidate_id == "inspect"
    assert decision.result.side_effects_executed is False
    assert decision.result.state_snapshot is request.state
    assert decision.result.virtual_state_schema == request.state.schema_version
    assert decision.result.virtual_state_digest == request.state.state_digest
    started = decision.result.trace[0]
    assert started.details["virtual_state_digest"] == request.state.state_digest
    assert "facts" not in started.details


def test_context_owned_authority_and_state_digest_bind_immutable_compilation() -> None:
    first_state = _state()
    second_state = _state(issue="serialization-boundary")
    service = GoalPlanningService(ports=PlanningDomainPorts(successors=_Catalog()))
    first = service.search(_request(state=first_state)).result
    second = service.search(_request(state=second_state)).result

    compiler = PlanningGraphCompiler()
    compiled = compiler.compile(first, compilation_context=_context(state=first_state), created_at=12)
    different = compiler.compile(second, compilation_context=_context(state=second_state), created_at=12)

    assert compiled.task_graph.graph_id != different.task_graph.graph_id
    assert compiled.virtual_state_digest == first_state.state_digest
    assert f"/state/sha256:{first_state.state_digest}" in compiled.task_graph.planner_ref
    lineage = compiled.work_order.metadata["planning_lineage"]
    assert lineage["virtual_state"] == {
        "schema": first_state.schema_version,
        "digest": first_state.state_digest,
    }
    assert compiled.work_order.metadata["planning_context"]["goal"]["goal_id"] == "goal-code-repair"

    with pytest.raises(ContractViolation):
        PlanningGraphCompiler().compile(
            first,
            compilation_context=_context(state=second_state),
        )
    with pytest.raises(ContractViolation):
        PlanningGraphCompiler().compile(
            replace(
                first,
                selected_path=(
                    SearchCandidate(
                        "forged",
                        payload=_node_payload(budget_policy={"forged": True}),
                    ),
                ),
            ),
            compilation_context=_context(state=first_state),
        )
    with pytest.raises(ContractViolation):
        replace(compiled, virtual_state_digest="0" * 64).validate()


def test_replan_request_only_freezes_evidence_and_never_performs_admission() -> None:
    original = _request()
    replanned = build_replan_goal_request(
        original,
        failure_evidence_refs=("evidence://attempts/failed-1",),
        accepted_artifact_refs=("artifact://task-result/graph/revisions/1/nodes/ok",),
    )

    assert replanned is not original
    assert replanned.state.state_digest != original.state.state_digest
    assert replanned.context["replan"]["failure_evidence_refs"] == (
        "evidence://attempts/failed-1",
    )
    assert replanned.context["replan"]["accepted_artifact_refs"] == (
        "artifact://task-result/graph/revisions/1/nodes/ok",
    )
