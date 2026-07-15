from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from host_brain.contracts.agent_runtime.budget_policy import BUDGET_POLICY_SCHEMA_VERSION
from host_brain.contracts.agent_runtime.task_graph import TaskGraphStatus
from host_brain.orchestration.planning_admission import PlanningAdmissionReceipt
from host_brain.orchestration.planning_execution import (
    PLANNING_EXECUTION_SEARCH_FAILED,
    GovernedPlanningExecutionService,
    PlanningExecutionAuthority,
    PlanningExecutionError,
)
from host_brain.orchestration.planning_graph_compiler import PlanningGraphCompiler
from host_brain.planning_search.contracts import (
    SearchCandidate,
    SearchCounters,
    SearchRequest,
    SearchResult,
    SearchStatus,
    SearchTraceEvent,
)
from natsume_contracts.task import (
    ExecutorAssignment,
    HostCommandSurface,
    TaskExecutor,
    build_host_command,
)


def _candidate(candidate_id: str) -> SearchCandidate:
    return SearchCandidate(
        candidate_id,
        payload={
            "worker_route": "skill",
            "target_id": f"worker-{candidate_id}",
            "objective": f"execute {candidate_id}",
            "acceptance_criteria": [f"{candidate_id} verified"],
            "input_refs": [f"artifact://input/{candidate_id}"],
            "output_contract": {"type": "result", "required": ["receipt_ref"]},
            "retry_policy": {"max_attempts": 2, "backoff_ms": 0},
            "risk_level": 2,
            "capability_requirements": [f"capability-{candidate_id}"],
            "domain": "code",
            "run_mode": "worker",
            "allowed_actions": ["read_files"],
            "forbidden_actions": ["push"],
            "verification_plan": {"required": True},
            "approval_policy": {"required": True},
            "review_policy": {"review_required": False},
            "budget_policy": {
                "schema_version": BUDGET_POLICY_SCHEMA_VERSION,
                "scope": "planning-execution-test",
                "limits": {
                    "token_count": 1000,
                    "tool_call_count": 4,
                    "time_ms": 1000,
                    "concurrency": 1,
                    "retry_count": 1,
                    "model_cost_micros": 1000,
                },
                "on_exceeded": "BLOCK",
            },
            "business_idempotency_scope": "planning-execution-test",
            "approval_ref": "approval://planning/root",
            "review_ref": "review-waiver://planning/root",
        },
    )


def _result(request_id: str, *ids: str) -> SearchResult:
    path = tuple(_candidate(value) for value in ids)
    return SearchResult(
        request_id=request_id,
        status=SearchStatus.SELECTED,
        stop_reason="selected",
        selected_candidate=path[0],
        selected_path=path,
        ranked=(),
        trace=(SearchTraceEvent(1, "service", "search_completed", {}),),
        counters=SearchCounters(),
    )


class _Search:
    def __init__(self, result: SearchResult) -> None:
        self.result = result
        self.requests: list[SearchRequest] = []

    def search(self, request: SearchRequest) -> SearchResult:
        self.requests.append(request)
        return self.result


class _Admission:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def admit(self, compiled, **kwargs):  # noqa: ANN001
        graph = compiled.task_graph
        work_order = compiled.work_order
        self.calls.append({"compiled": compiled, **kwargs})
        command = build_host_command(
            surface=kwargs["surface"],
            request_id=kwargs["command_request_id"],
            session_id=kwargs["session_id"],
            principal_id=kwargs["principal_id"],
            ingress_claim_ref=kwargs.get("ingress_claim_ref"),
            ingress_audit_ref=kwargs.get("ingress_audit_ref"),
            governor_grant_ref=kwargs["governor_grant_ref"],
            input_ref=kwargs["input_ref"],
            input_sha256=kwargs["input_sha256"],
            root_work_order_id=graph.root_work_order_id,
            graph_id=graph.graph_id,
            graph_revision=graph.revision,
            route_type="plan",
            target_id=kwargs["target_id"],
            business_scope=work_order.business_idempotency_scope,
            operation_fingerprint=work_order.operation_fingerprint,
            created_at_ms=graph.created_at,
        )
        return PlanningAdmissionReceipt(
            command=command,
            work_order=work_order,
            assignment=ExecutorAssignment(
                work_order_id=graph.root_work_order_id,
                executor=TaskExecutor.CANONICAL,
                assignment_id=f"runtime-assignment://{graph.root_work_order_id}/canonical",
                assigned_at_ms=graph.created_at,
                is_new_task=True,
                reason_code="RUNTIME_NEW_TASK_CANONICAL",
            ),
            graph=graph,
            created=True,
            replayed=False,
        )


class _Application:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def execute_stream(self, **kwargs):  # noqa: ANN003
        self.calls.append(dict(kwargs))
        yield {"phase": "COMMIT", "done": True, "graph_revision": kwargs["graph"].revision}


def _authority() -> PlanningExecutionAuthority:
    return PlanningExecutionAuthority(
        surface=HostCommandSurface.CHAT,
        base_command_request_id="request-plan",
        session_id="session-plan",
        principal_id="owner",
        input_ref="input://sha256/" + "1" * 64,
        input_sha256="sha256:" + "1" * 64,
        governor_grant_ref="approval://planning/root",
        target_id="planning-search",
    )


@pytest.mark.asyncio
async def test_selected_path_is_compiled_admitted_and_executed_without_second_plan() -> None:
    search = _Search(_result("search-initial", "inspect", "verify"))
    admission = _Admission()
    application = _Application()
    service = GovernedPlanningExecutionService(
        search=search,  # type: ignore[arg-type]
        compiler=PlanningGraphCompiler(),
        admission=admission,  # type: ignore[arg-type]
        command_application=application,
    )

    prepared = await service.prepare_initial(
        SearchRequest("search-initial", initial_state={}),
        authority=_authority(),
        created_at=100,
    )
    events = [event async for event in service.execute_stream(prepared)]

    assert prepared.compiled.selected_candidate_ids == ("inspect", "verify")
    assert prepared.admission.graph.revision == 1
    assert admission.calls[0]["command_request_id"] == "request-plan"
    assert application.calls[0]["graph"].to_dict() == prepared.compiled.task_graph.to_dict()
    assert events == [{"phase": "COMMIT", "done": True, "graph_revision": 1}]


@pytest.mark.asyncio
async def test_failed_revision_replan_is_exact_plus_one_and_distinct_command() -> None:
    search = _Search(_result("search-initial", "first"))
    admission = _Admission()
    application = _Application()
    service = GovernedPlanningExecutionService(
        search=search,  # type: ignore[arg-type]
        compiler=PlanningGraphCompiler(),
        admission=admission,  # type: ignore[arg-type]
        command_application=application,
    )
    initial = await service.prepare_initial(
        SearchRequest("search-initial", initial_state={}),
        authority=_authority(),
        created_at=100,
    )
    failed = replace(initial.admission.graph, status=TaskGraphStatus.FAILED)
    failed_snapshot = failed.to_dict()
    search.result = _result("search-replan", "recover", "validate")

    replanned = await service.prepare_replan(
        SearchRequest("search-replan", initial_state={"failed": True}),
        authority=_authority(),
        previous_graph=failed,
        failure_evidence_refs=("evidence://failure/attempt-1",),
        created_at=200,
    )

    assert failed.to_dict() == failed_snapshot
    assert replanned.admission.graph.graph_id == failed.graph_id
    assert replanned.admission.graph.root_work_order_id == failed.root_work_order_id
    assert replanned.admission.graph.revision == failed.revision + 1
    assert admission.calls[-1]["command_request_id"] == "request-plan.graph-revision-2"


@pytest.mark.asyncio
async def test_non_selected_search_never_compiles_or_admits() -> None:
    failed = SearchResult(
        request_id="search-failed",
        status=SearchStatus.EMPTY,
        stop_reason="no_candidates",
        selected_candidate=None,
        selected_path=(),
        ranked=(),
        trace=(),
        counters=SearchCounters(),
    )
    search = _Search(failed)
    admission = _Admission()
    service = GovernedPlanningExecutionService(
        search=search,  # type: ignore[arg-type]
        compiler=PlanningGraphCompiler(),
        admission=admission,  # type: ignore[arg-type]
        command_application=_Application(),
    )

    with pytest.raises(PlanningExecutionError) as raised:
        await service.prepare_initial(
            SearchRequest("search-failed", initial_state={}),
            authority=_authority(),
            created_at=100,
        )

    assert raised.value.reason_code == PLANNING_EXECUTION_SEARCH_FAILED
    assert admission.calls == []
