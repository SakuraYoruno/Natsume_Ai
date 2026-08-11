from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from typing import Any

import pytest

from host_brain.contracts.agent_runtime.budget_policy import (
    BUDGET_POLICY_SCHEMA_VERSION,
)
from host_brain.contracts.agent_runtime.contract_errors import ContractViolation
from host_brain.contracts.agent_runtime.task_graph import (
    TaskGraphCompletionPolicy,
    TaskGraphStatus,
    TaskNodeStatus,
    validate_task_graph_admission_state,
)
from host_brain.orchestration.planning_graph_compiler import PlanningGraphCompiler
from host_brain.planning_search.contracts import (
    SearchCandidate,
    SearchCounters,
    SearchResult,
    SearchStatus,
    SearchTraceEvent,
)


def _payload(candidate_id: str, **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "worker_route": "skill",
        "target_id": f"worker-{candidate_id}",
        "objective": f"execute {candidate_id}",
        "acceptance_criteria": [f"{candidate_id} has durable evidence"],
        "input_refs": [f"artifact://inputs/{candidate_id}"],
        "output_contract": {
            "type": "planning_step_result",
            "required": ["evidence_ref"],
        },
        "retry_policy": {"max_attempts": 2, "retry_backoff_ms": 10},
        "risk_level": 2,
        "capability_requirements": [f"capability-{candidate_id}"],
        "domain": "code",
        "run_mode": "worker",
        "allowed_actions": ["read_files"],
        "forbidden_actions": ["push"],
        "verification_plan": {"required": True, "type": "evidence"},
        "approval_policy": {"required": True, "mode": "governed"},
        "review_policy": {"review_required": True, "mode": "governed"},
        "budget_policy": {
            "schema_version": BUDGET_POLICY_SCHEMA_VERSION,
            "scope": "planning-compile-test",
            "limits": {
                "token_count": 4_000,
                "tool_call_count": 8,
                "time_ms": 60_000,
                "concurrency": 1,
                "retry_count": 1,
                "model_cost_micros": 100_000,
            },
            "on_exceeded": "BLOCK",
            "metadata": {"owner": "gr04"},
        },
        "business_idempotency_scope": "planning-compile-test",
        "approval_ref": "approval://gr04/root",
        "review_ref": "review://gr04/root",
        "workspace": {"strategy": "temp_workspace"},
        "input_pack": {"request_ref": "artifact://requests/gr04"},
        "metadata": {"source": "unit-test", "author": "researcher"},
    }
    payload.update(overrides)
    return payload


def _candidate(candidate_id: str, **overrides: Any) -> SearchCandidate:
    return SearchCandidate(candidate_id, payload=_payload(candidate_id, **overrides))


def _result(
    *candidates: SearchCandidate,
    request_id: str = "request-gr04",
    status: SearchStatus = SearchStatus.SELECTED,
    selected_candidate: Any = None,
    ranked: Any = (),
    counters: Any = None,
) -> SearchResult:
    path = tuple(candidates)
    return SearchResult(
        request_id=request_id,
        status=status,
        stop_reason="selected" if status is SearchStatus.SELECTED else "failed",
        selected_candidate=(
            path[0] if selected_candidate is None and path else selected_candidate
        ),
        selected_path=path,
        ranked=ranked,
        trace=(
            SearchTraceEvent(
                sequence=1,
                layer="service",
                event="search_completed",
                details={"selected_path": [item.candidate_id for item in path]},
            ),
        ),
        counters=SearchCounters() if counters is None else counters,
        side_effects_executed=False,
    )


def test_gr04_compilation_is_deterministic_linear_and_admission_ready() -> None:
    result = _result(_candidate("collect"), _candidate("synthesize"))
    payload_before = [deepcopy(candidate.payload) for candidate in result.selected_path]
    compiler = PlanningGraphCompiler()

    first = compiler.compile(result, created_at=123)
    second = compiler.compile(result, created_at=123)

    assert first.to_dict() == second.to_dict()
    assert [candidate.payload for candidate in result.selected_path] == payload_before
    validate_task_graph_admission_state(first.task_graph)
    graph = first.task_graph
    assert graph.revision == 1
    assert graph.status is TaskGraphStatus.CREATED
    assert graph.planner_ref.endswith(f"sha256:{result.trace_digest}")
    assert len({node.node_id for node in graph.nodes}) == 2
    assert graph.nodes[0].dependencies == ()
    assert graph.nodes[0].status is TaskNodeStatus.READY
    assert graph.nodes[1].dependencies == (graph.nodes[0].node_id,)
    assert graph.nodes[1].status is TaskNodeStatus.BLOCKED
    assert graph.result_node_id == graph.nodes[1].node_id
    assert graph.nodes[0].route == "skill:worker-collect"
    assert graph.nodes[0].retry_policy == {
        "max_attempts": 2,
        "retry_backoff_ms": 10,
    }
    assert any(
        ref.endswith(f"/revisions/1/nodes/{graph.nodes[0].node_id}")
        for ref in graph.nodes[1].input_refs
    )

    order = first.work_order
    assert order.task_graph_id == graph.graph_id
    assert order.task_node_id == graph.nodes[0].node_id
    assert order.root_work_order_id == graph.root_work_order_id == order.work_order_id
    assert order.graph_revision == 1
    assert order.budget_policy is not None
    assert order.budget_policy.limits.time_ms == 60_000
    assert order.approval_ref == "approval://gr04/root"
    assert order.review_ref == "review://gr04/root"
    assert order.operation_fingerprint.startswith("sha256:")
    assert len(order.operation_fingerprint) == 71


def test_gr04_compiler_rejects_legacy_retry_backoff_alias() -> None:
    candidate = _candidate(
        "legacy-retry",
        retry_policy={"max_attempts": 2, "backoff_ms": 10},
    )

    with pytest.raises(ContractViolation) as error:
        PlanningGraphCompiler().compile(_result(candidate))

    assert "unsupported fields: backoff_ms" in error.value.detail


def test_gr04_compiler_rejects_caller_supplied_task_result_authority() -> None:
    candidate = _candidate(
        "forged-result-ref",
        input_refs=[
            "artifact://task-result/other-graph/revisions/9/nodes/other-node"
        ],
    )

    with pytest.raises(ContractViolation) as error:
        PlanningGraphCompiler().compile(_result(candidate))

    assert "compiler-owned task-result refs" in error.value.detail


def test_gr04_explicit_candidate_dependencies_compile_parallel_dag() -> None:
    result = _result(
        _candidate("collect", depends_on_candidate_ids=[]),
        _candidate("inspect", depends_on_candidate_ids=[]),
        _candidate(
            "synthesize",
            depends_on_candidate_ids=["collect", "inspect"],
        ),
    )

    compiled = PlanningGraphCompiler().compile(result, created_at=123)
    graph = compiled.task_graph

    assert [node.status for node in graph.nodes[:2]] == [
        TaskNodeStatus.READY,
        TaskNodeStatus.READY,
    ]
    assert graph.nodes[0].dependencies == ()
    assert graph.nodes[1].dependencies == ()
    assert graph.nodes[2].dependencies == (
        graph.nodes[0].node_id,
        graph.nodes[1].node_id,
    )
    assert graph.nodes[2].status is TaskNodeStatus.BLOCKED
    assert graph.result_node_id == graph.nodes[2].node_id
    bindings = compiled.work_order.metadata["planning_lineage"]["node_bindings"]
    assert bindings[2]["depends_on_candidate_ids"] == ["collect", "inspect"]
    assert bindings[2]["depends_on_node_ids"] == list(graph.nodes[2].dependencies)


def test_gr04_parallel_terminal_candidates_use_selected_path_result_authority() -> None:
    compiled = PlanningGraphCompiler().compile(
        _result(
            _candidate("left", depends_on_candidate_ids=[]),
            _candidate("right", depends_on_candidate_ids=[]),
        ),
        created_at=123,
        completion_policy=TaskGraphCompletionPolicy.ATOMIC_GRAPH_SUCCESS_V1,
    )

    assert [node.node_id for node in compiled.task_graph.nodes] != []
    assert compiled.task_graph.result_node_id == compiled.task_graph.nodes[-1].node_id
    assert (
        compiled.task_graph.completion_policy
        is TaskGraphCompletionPolicy.ATOMIC_GRAPH_SUCCESS_V1
    )


def test_gr04_parallel_terminal_candidates_default_to_atomic_result_authority() -> None:
    compiled = PlanningGraphCompiler().compile(
        _result(
            _candidate("left", depends_on_candidate_ids=[]),
            _candidate("right", depends_on_candidate_ids=[]),
        ),
        created_at=123,
    )

    assert (
        compiled.task_graph.completion_policy
        is TaskGraphCompletionPolicy.ATOMIC_GRAPH_SUCCESS_V1
    )
    assert compiled.task_graph.result_node_id == compiled.task_graph.nodes[-1].node_id


@pytest.mark.parametrize(
    "dependencies",
    [
        ["missing"],
        ["second"],
        ["first"],
        ["first", "first"],
    ],
)
def test_gr04_explicit_dependencies_fail_closed_when_not_a_topological_order(
    dependencies: list[str],
) -> None:
    if dependencies == ["first"]:
        candidates = (
            _candidate("first", depends_on_candidate_ids=["first"]),
            _candidate("second", depends_on_candidate_ids=[]),
        )
    elif dependencies == ["first", "first"]:
        candidates = (
            _candidate("first", depends_on_candidate_ids=[]),
            _candidate("second", depends_on_candidate_ids=dependencies),
        )
    else:
        candidates = (
            _candidate("first", depends_on_candidate_ids=dependencies),
            _candidate("second", depends_on_candidate_ids=[]),
        )

    with pytest.raises(ContractViolation) as error:
        PlanningGraphCompiler().compile(_result(*candidates))

    assert "dependency candidate" in error.value.detail


@pytest.mark.parametrize(
    "sensitive_key",
    [
        "secret",
        "access_token",
        "credential",
        "raw_auth",
        "clientSecret",
        "api_key",
        "privateKey",
        "authHeader",
        "dbPassword",
    ],
)
def test_gr04_recursively_rejects_sensitive_payload_keys(sensitive_key: str) -> None:
    candidate = _candidate(
        "unsafe",
        metadata={"outer": [{"inner": {sensitive_key: "must-not-compile"}}]},
    )

    with pytest.raises(ContractViolation) as error:
        PlanningGraphCompiler().compile(_result(candidate))

    assert "sensitive key is forbidden" in error.value.detail
    assert sensitive_key in error.value.detail


@pytest.mark.parametrize("status", [SearchStatus.EMPTY, SearchStatus.FAILED])
def test_gr04_rejects_non_selected_results(status: SearchStatus) -> None:
    with pytest.raises(ContractViolation) as error:
        PlanningGraphCompiler().compile(_result(_candidate("unused"), status=status))

    assert "only a SELECTED" in error.value.detail


def test_gr04_rejects_empty_selected_path_and_incomplete_production_payload() -> None:
    with pytest.raises(ContractViolation) as empty_error:
        PlanningGraphCompiler().compile(_result())
    assert "empty selected_path" in empty_error.value.detail

    incomplete = _candidate("incomplete")
    payload = dict(incomplete.payload)
    payload.pop("target_id")
    with pytest.raises(ContractViolation) as field_error:
        PlanningGraphCompiler().compile(
            _result(SearchCandidate("incomplete", payload=payload))
        )
    assert "target_id" in field_error.value.detail


def test_gr04_initial_lineage_changes_when_selected_plan_semantics_change() -> None:
    compiler = PlanningGraphCompiler()
    first = compiler.compile(_result(_candidate("same")), created_at=10)
    changed = compiler.compile(
        _result(_candidate("same", objective="different immutable plan")),
        created_at=10,
    )

    assert changed.task_graph.graph_id != first.task_graph.graph_id
    assert changed.task_graph.root_work_order_id != first.task_graph.root_work_order_id


def test_gr04_frozen_product_cannot_be_changed_through_typed_projections() -> None:
    compiled = PlanningGraphCompiler().compile(_result(_candidate("frozen")))
    mutable_projection = compiled.work_order
    mutable_projection.metadata["planning_lineage"]["trace_digest"] = "tampered"

    compiled.validate()
    assert compiled.work_order.metadata["planning_lineage"]["trace_digest"] != "tampered"


@pytest.mark.parametrize("terminal_status", [TaskGraphStatus.FAILED, TaskGraphStatus.BLOCKED])
def test_gr04_failure_replan_is_new_revision_with_fixed_lineage_and_evidence(
    terminal_status: TaskGraphStatus,
) -> None:
    compiler = PlanningGraphCompiler()
    original = compiler.compile(_result(_candidate("first")), created_at=100)
    previous = replace(original.task_graph, status=terminal_status)
    previous_snapshot = previous.to_dict()
    failure_refs = ("evidence://attempts/failed-7", "receipt://attempts/failed-7")
    replan_result = _result(
        _candidate("recover"),
        _candidate("verify"),
        request_id="request-gr04-replan",
    )

    replanned = compiler.replan(
        replan_result,
        previous_graph=previous,
        failure_evidence_refs=failure_refs,
        created_at=200,
    )
    replay = compiler.replan(
        replan_result,
        previous_graph=previous,
        failure_evidence_refs=failure_refs,
        created_at=200,
    )

    assert replanned.to_dict() == replay.to_dict()
    assert replanned.task_graph is not previous
    assert previous.to_dict() == previous_snapshot
    assert replanned.task_graph.graph_id == previous.graph_id
    assert replanned.task_graph.root_work_order_id == previous.root_work_order_id
    assert replanned.task_graph.revision == previous.revision + 1
    assert replanned.work_order.graph_revision == previous.revision + 1
    assert replanned.previous_revision == previous.revision
    assert replanned.failure_evidence_refs == failure_refs
    assert set(failure_refs).issubset(replanned.task_graph.nodes[0].input_refs)
    lineage = replanned.work_order.metadata["planning_lineage"]
    assert lineage["parent_revision"] == previous.revision
    assert lineage["parent_status"] == terminal_status.value
    assert lineage["parent_planner_ref"] == previous.planner_ref
    assert lineage["failure_evidence_refs"] == list(failure_refs)
    assert lineage["failure_evidence_binding"].startswith("sha256:")
    validate_task_graph_admission_state(replanned.task_graph)


def test_gr04_replan_rejects_live_graph_or_missing_failure_evidence() -> None:
    compiler = PlanningGraphCompiler()
    original = compiler.compile(_result(_candidate("first")))
    replanned_result = _result(_candidate("recover"))

    with pytest.raises(ContractViolation) as state_error:
        compiler.replan(
            replanned_result,
            previous_graph=original.task_graph,
            failure_evidence_refs=("evidence://attempts/failed",),
        )
    assert "FAILED or BLOCKED" in state_error.value.detail

    failed = replace(original.task_graph, status=TaskGraphStatus.FAILED)
    with pytest.raises(ContractViolation) as evidence_error:
        compiler.replan(
            replanned_result,
            previous_graph=failed,
            failure_evidence_refs=(),
        )
    assert "failure evidence" in evidence_error.value.detail


class _Tripwire:
    def __init__(self) -> None:
        self.touched = False

    def __getattribute__(self, name: str) -> Any:
        if name not in {"touched", "__dict__", "__class__"}:
            object.__setattr__(self, "touched", True)
            raise AssertionError(f"compiler read unrelated SearchResult field: {name}")
        return object.__getattribute__(self, name)


def test_gr04_compiler_does_not_read_alternatives_or_execute_side_effects() -> None:
    tripwire = _Tripwire()
    candidate = _candidate("selected")
    result = _result(
        candidate,
        selected_candidate=tripwire,
        ranked=(tripwire,),
        counters=tripwire,
    )

    compiled = PlanningGraphCompiler().compile(result)

    assert compiled.selected_candidate_ids == ("selected",)
    assert tripwire.touched is False
    assert result.side_effects_executed is False


def test_gr04_requires_literal_true_simulation_only_marker() -> None:
    unsafe = SearchCandidate(
        "unsafe-marker",
        payload=_payload("unsafe-marker"),
        simulation_only="false",  # type: ignore[arg-type]
    )

    with pytest.raises(ContractViolation) as error:
        PlanningGraphCompiler().compile(_result(unsafe))

    assert "not simulation-only" in error.value.detail
