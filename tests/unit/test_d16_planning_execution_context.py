from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from host_brain.agent_runtime.governed_production_payload import (
    FROZEN_NODE_EXECUTOR_PAYLOADS_KEY,
)
from host_brain.contracts.agent_runtime.budget_policy import BUDGET_POLICY_SCHEMA_VERSION
from host_brain.contracts.agent_runtime.contract_errors import ContractViolation
from host_brain.contracts.agent_runtime.task_graph import TaskGraphStatus
from host_brain.orchestration.planning_admission import PlanningAdmissionReceipt
from host_brain.orchestration.planning_execution import (
    PLANNING_EXECUTION_INVALID,
    GovernedPlanningExecutionService,
    PlanningExecutionAuthority,
    PlanningExecutionError,
)
from host_brain.orchestration.planning_graph_compiler import (
    PlanningCompilationContext,
    PlanningGraphCompiler,
)
from host_brain.orchestration.planning_recovery import (
    DurablePlanningFailure,
    GovernedPlanningRecoveryService,
)
from host_brain.contracts.planning_search.contracts import (
    SearchCandidate,
    SearchCounters,
    SearchRequest,
    SearchResult,
    SearchStatus,
    SearchTraceEvent,
    VirtualStateSnapshot,
)
from natsume_contracts.task import (
    ExecutorAssignment,
    HostCommandSurface,
    TaskExecutor,
    build_host_command,
)


def _state(label: str) -> VirtualStateSnapshot:
    return VirtualStateSnapshot(
        domain="code",
        capabilities=("inspect",),
        facts={"label": label},
        constraints={"mode": "shadow"},
        source_refs=(f"snapshot://planning/d16/{label}",),
    )


def _candidate(candidate_id: str) -> SearchCandidate:
    return SearchCandidate(
        candidate_id=candidate_id,
        prior_score=0.8,
        payload={
            "worker_route": "skill",
            "target_id": "agent_worker",
            "objective": f"inspect {candidate_id}",
            "acceptance_criteria": ["structured evidence is returned"],
            "input_refs": [f"artifact://planning/d16/{candidate_id}"],
            "output_contract": {"type": "evidence", "required": ["receipt_ref"]},
            "retry_policy": {"max_attempts": 1, "retry_backoff_ms": 0},
            "risk_level": 1,
            "capability_requirements": ["agent_worker"],
        },
    )


def _result(request_id: str, state: VirtualStateSnapshot, candidate_id: str) -> SearchResult:
    candidate = _candidate(candidate_id)
    return SearchResult(
        request_id=request_id,
        status=SearchStatus.SELECTED,
        stop_reason="selected",
        selected_candidate=candidate,
        selected_path=(candidate,),
        ranked=(),
        trace=(SearchTraceEvent(1, "service", "search_completed", {}),),
        counters=SearchCounters(),
        state_snapshot=state,
    )


def _production_result(
    request_id: str,
    state: VirtualStateSnapshot,
    candidate_id: str,
    *,
    objective: str,
) -> SearchResult:
    base_candidate = _candidate(candidate_id)
    candidate = replace(
        base_candidate,
        payload={
            **base_candidate.payload,
            "target_id": "agent_memory_curator",
            "objective": objective,
            "capability_requirements": ["agent_memory_curator"],
        },
    )
    return SearchResult(
        request_id=request_id,
        status=SearchStatus.SELECTED,
        stop_reason="selected",
        selected_candidate=candidate,
        selected_path=(candidate,),
        ranked=(),
        trace=(SearchTraceEvent(1, "service", "search_completed", {}),),
        counters=SearchCounters(),
        state_snapshot=state,
    )


def _authority() -> PlanningExecutionAuthority:
    return PlanningExecutionAuthority(
        surface=HostCommandSurface.CHAT,
        base_command_request_id="request-d16-context",
        session_id="session-d16-context",
        principal_id="owner",
        input_ref="input://sha256/" + "1" * 64,
        input_sha256="sha256:" + "1" * 64,
        governor_grant_ref="approval://planning/root",
        target_id="planning-search",
    )


def _context(
    state: VirtualStateSnapshot,
    *,
    approval_ref: str | None = None,
    input_pack: dict[str, Any] | None = None,
) -> PlanningCompilationContext:
    return PlanningCompilationContext(
        domain="code",
        run_mode="inspect",
        plan_objective="Compile a state-bound shadow planning path.",
        allowed_actions=("read_files",),
        forbidden_actions=("write_files",),
        verification_plan={"required": True},
        approval_policy={"required": True},
        review_policy={"review_required": False},
        budget_policy={
            "schema_version": BUDGET_POLICY_SCHEMA_VERSION,
            "scope": "d16-planning-context",
            "limits": {
                "token_count": 1_000,
                "tool_call_count": 4,
                "time_ms": 1_000,
                "concurrency": 1,
                "retry_count": 1,
                "model_cost_micros": 1_000,
            },
            "on_exceeded": "BLOCK",
        },
        business_idempotency_scope="d16-planning-context",
        approval_ref=approval_ref or _authority().governor_grant_ref,
        review_ref="review://planning/d16-context",
        workspace={"strategy": "temp_workspace"},
        input_pack=dict(input_pack or {}),
        input_refs=("input://planning/d16-context",),
        virtual_state=state,
    )


def _production_context(
    state: VirtualStateSnapshot,
    *,
    candidate_id: str,
    objective: str,
    caller_frozen_payloads: dict[str, Any] | None = None,
) -> PlanningCompilationContext:
    input_pack: dict[str, Any] = {
        "approved_candidate_inputs": {
            candidate_id: {
                "route": "skill",
                "target_id": "agent_memory_curator",
                "objective": objective,
                "args": {"artifacts_root": "runtime://planning/d16"},
                "depends_on_candidate_ids": [],
            }
        }
    }
    if caller_frozen_payloads is not None:
        input_pack[FROZEN_NODE_EXECUTOR_PAYLOADS_KEY] = dict(caller_frozen_payloads)
    return _context(
        state,
        input_pack=input_pack,
    )


class _Search:
    def __init__(self, result: SearchResult) -> None:
        self.result = result
        self.requests: list[SearchRequest] = []

    def search(self, request: SearchRequest) -> SearchResult:
        self.requests.append(request)
        return self.result


class _RecordingCompiler(PlanningGraphCompiler):
    def __init__(self) -> None:
        self.contexts: list[PlanningCompilationContext | None] = []

    def compile(self, result: SearchResult, **kwargs: Any):  # noqa: ANN201
        self.contexts.append(kwargs.get("compilation_context"))
        return super().compile(result, **kwargs)


class _Admission:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def admit(self, compiled, **kwargs: Any):  # noqa: ANN001
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
        self.preflight_calls: list[dict[str, Any]] = []

    def preflight_parent_governor_binding(self, **kwargs: Any) -> None:
        self.preflight_calls.append(dict(kwargs))

    async def execute_stream(self, **_kwargs: Any):  # noqa: ANN003
        yield {"phase": "COMMIT"}


def _service(
    search: _Search,
    compiler: PlanningGraphCompiler,
    admission: _Admission,
    application: _Application,
) -> GovernedPlanningExecutionService:
    return GovernedPlanningExecutionService(
        search=search,  # type: ignore[arg-type]
        compiler=compiler,
        admission=admission,  # type: ignore[arg-type]
        command_application=application,
    )


@pytest.mark.asyncio
async def test_initial_execution_forwards_context_and_binds_state_to_admission() -> None:
    state = _state("initial")
    search = _Search(_result("search-d16-initial", state, "inspect"))
    compiler = _RecordingCompiler()
    admission = _Admission()
    application = _Application()
    context = _context(state)

    prepared = await _service(search, compiler, admission, application).prepare_initial(
        SearchRequest("search-d16-initial", initial_state=state, state_snapshot=state),
        authority=_authority(),
        created_at=16,
        compilation_context=context,
    )

    assert compiler.contexts == [context]
    assert prepared.compiled.virtual_state_digest == state.state_digest
    assert prepared.admission.work_order.approval_ref == _authority().governor_grant_ref
    assert admission.calls and application.preflight_calls


@pytest.mark.asyncio
async def test_production_skill_plan_freezes_node_payload_for_initial_and_replan() -> None:
    initial_state = _state("production-initial")
    initial_objective = "curate the approved initial planning input"
    search = _Search(
        _production_result(
            "search-d16-production-initial",
            initial_state,
            "curate-initial",
            objective=initial_objective,
        )
    )
    admission = _Admission()
    application = _Application()
    service = _service(search, PlanningGraphCompiler(), admission, application)
    initial = await service.prepare_initial(
        SearchRequest(
            "search-d16-production-initial",
            initial_state=initial_state,
            state_snapshot=initial_state,
        ),
        authority=_authority(),
        created_at=16,
        compilation_context=_production_context(
            initial_state,
            candidate_id="curate-initial",
            objective=initial_objective,
        ),
    )

    initial_node_id = initial.admission.graph.nodes[0].node_id
    initial_payload = initial.admission.work_order.input_pack[
        FROZEN_NODE_EXECUTOR_PAYLOADS_KEY
    ][initial_node_id]
    assert initial_payload["target_id"] == "agent_memory_curator"
    assert initial_payload["work_order"]["metadata"]["request_id"] == "request-d16-context"
    assert initial_payload["work_order"]["metadata"]["governed_lineage"] == {
        "root_work_order_id": initial.admission.work_order.root_work_order_id,
        "task_graph_id": initial.admission.graph.graph_id,
        "task_node_id": initial_node_id,
        "graph_revision": 1,
        "business_scope": initial.admission.work_order.business_idempotency_scope,
        "operation_fingerprint": initial.admission.work_order.operation_fingerprint,
        "approval_ref": initial.admission.work_order.approval_ref,
        "review_ref": initial.admission.work_order.review_ref,
    }

    replan_state = _state("production-replan")
    replan_objective = "curate the approved replanned input"
    search.result = _production_result(
        "search-d16-production-replan",
        replan_state,
        "curate-replan",
        objective=replan_objective,
    )
    failed = replace(initial.admission.graph, status=TaskGraphStatus.FAILED)
    replanned = await service.prepare_replan(
        SearchRequest(
            "search-d16-production-replan",
            initial_state=replan_state,
            state_snapshot=replan_state,
        ),
        authority=_authority(),
        previous_graph=failed,
        failure_evidence_refs=("evidence://planning/d16-production-failure",),
        created_at=32,
        compilation_context=_production_context(
            replan_state,
            candidate_id="curate-replan",
            objective=replan_objective,
        ),
    )

    replan_node_id = replanned.admission.graph.nodes[0].node_id
    replan_payload = replanned.admission.work_order.input_pack[
        FROZEN_NODE_EXECUTOR_PAYLOADS_KEY
    ][replan_node_id]
    assert replanned.admission.graph.revision == 2
    assert replan_payload["work_order"]["metadata"]["request_id"] == (
        "request-d16-context.graph-revision-2"
    )
    assert replan_payload["work_order"]["metadata"]["governed_lineage"][
        "graph_revision"
    ] == 2
    assert initial_payload["work_order"]["metadata"]["governed_lineage"][
        "graph_revision"
    ] == 1


@pytest.mark.asyncio
async def test_production_skill_plan_rejects_caller_supplied_frozen_payloads() -> None:
    state = _state("production-caller-payload")
    objective = "reject a caller supplied production payload"
    search = _Search(
        _production_result(
            "search-d16-production-caller-payload",
            state,
            "curate-caller-payload",
            objective=objective,
        )
    )
    admission = _Admission()
    application = _Application()

    with pytest.raises(PlanningExecutionError) as raised:
        await _service(search, PlanningGraphCompiler(), admission, application).prepare_initial(
            SearchRequest(
                "search-d16-production-caller-payload",
                initial_state=state,
                state_snapshot=state,
            ),
            authority=_authority(),
            created_at=16,
            compilation_context=_production_context(
                state,
                candidate_id="curate-caller-payload",
                objective=objective,
                caller_frozen_payloads={"node-caller": {"target_id": "agent_memory_curator"}},
            ),
        )

    assert raised.value.reason_code == PLANNING_EXECUTION_INVALID
    assert admission.calls == []
    assert application.preflight_calls == []


@pytest.mark.asyncio
async def test_execution_rejects_context_grant_drift_before_search_or_admission() -> None:
    state = _state("grant-drift")
    search = _Search(_result("search-d16-grant", state, "inspect"))
    admission = _Admission()
    application = _Application()

    with pytest.raises(PlanningExecutionError) as raised:
        await _service(search, PlanningGraphCompiler(), admission, application).prepare_initial(
            SearchRequest("search-d16-grant", initial_state=state, state_snapshot=state),
            authority=_authority(),
            created_at=16,
            compilation_context=_context(
                state,
                approval_ref="approval://different-governor/request",
            ),
        )

    assert raised.value.reason_code == PLANNING_EXECUTION_INVALID
    assert search.requests == []
    assert admission.calls == []
    assert application.preflight_calls == []


@pytest.mark.asyncio
async def test_execution_rejects_context_state_drift_before_admission() -> None:
    result_state = _state("result")
    context_state = _state("context-drift")
    search = _Search(_result("search-d16-state", result_state, "inspect"))
    admission = _Admission()
    application = _Application()

    with pytest.raises(ContractViolation, match="virtual state differs from compilation context"):
        await _service(search, PlanningGraphCompiler(), admission, application).prepare_initial(
            SearchRequest(
                "search-d16-state",
                initial_state=result_state,
                state_snapshot=result_state,
            ),
            authority=_authority(),
            created_at=16,
            compilation_context=_context(context_state),
        )

    assert len(search.requests) == 1
    assert admission.calls == []
    assert application.preflight_calls == []


@pytest.mark.asyncio
async def test_replan_forwards_the_new_context_and_preserves_revision_lineage() -> None:
    initial_state = _state("initial")
    replan_state = _state("replan")
    search = _Search(_result("search-d16-initial", initial_state, "inspect"))
    compiler = _RecordingCompiler()
    admission = _Admission()
    application = _Application()
    service = _service(search, compiler, admission, application)
    initial = await service.prepare_initial(
        SearchRequest(
            "search-d16-initial",
            initial_state=initial_state,
            state_snapshot=initial_state,
        ),
        authority=_authority(),
        created_at=16,
        compilation_context=_context(initial_state),
    )
    failed = replace(initial.admission.graph, status=TaskGraphStatus.FAILED)
    replan_context = _context(replan_state)
    search.result = _result("search-d16-replan", replan_state, "recover")

    replanned = await service.prepare_replan(
        SearchRequest(
            "search-d16-replan",
            initial_state=replan_state,
            state_snapshot=replan_state,
        ),
        authority=_authority(),
        previous_graph=failed,
        failure_evidence_refs=("evidence://planning/d16-failure",),
        created_at=32,
        compilation_context=replan_context,
    )

    assert compiler.contexts == [_context(initial_state), replan_context]
    assert replanned.admission.graph.revision == failed.revision + 1
    assert replanned.compiled.virtual_state_digest == replan_state.state_digest


class _ForwardingSentinel(RuntimeError):
    pass


class _RecoveryExecutionSpy(GovernedPlanningExecutionService):
    def __init__(self) -> None:
        self.kwargs: dict[str, Any] | None = None

    async def prepare_replan(self, *_args: Any, **kwargs: Any):  # noqa: ANN003
        self.kwargs = dict(kwargs)
        raise _ForwardingSentinel()


class _UnusedTaskStore:
    def read_scheduler_snapshot(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise AssertionError("the injected durable failure source must be used")


class _FailureSource:
    def __init__(self, failure: DurablePlanningFailure) -> None:
        self.failure = failure

    async def load(self, **_kwargs: Any) -> DurablePlanningFailure:
        return self.failure


@pytest.mark.asyncio
async def test_recovery_forwards_compilation_context_to_the_explicit_replan() -> None:
    state = _state("recovery")
    context = _context(state)
    previous = PlanningGraphCompiler().compile(
        _result("search-d16-recovery", state, "inspect"),
        created_at=16,
        compilation_context=context,
    ).task_graph
    failed = replace(previous, status=TaskGraphStatus.FAILED)
    failure = DurablePlanningFailure(
        previous_graph=failed,
        graph_status_version=1,
        failure_evidence_refs=("evidence://planning/d16-failure",),
    )
    execution = _RecoveryExecutionSpy()
    service = GovernedPlanningRecoveryService(
        execution_service=execution,
        task_store=_UnusedTaskStore(),
    )
    service._failures = _FailureSource(failure)  # type: ignore[assignment]

    with pytest.raises(_ForwardingSentinel):
        await service.prepare_failure_replan(
            SearchRequest("search-d16-recovery", initial_state=state, state_snapshot=state),
            authority=_authority(),
            graph_id=failed.graph_id,
            previous_revision=failed.revision,
            created_at=32,
            compilation_context=context,
        )

    assert execution.kwargs is not None
    assert execution.kwargs["compilation_context"] is context
