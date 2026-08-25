from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from host_brain.agent_runtime.runtime_router import CanonicalRuntimeRouter
from host_brain.contracts.agent_runtime.budget_policy import BUDGET_POLICY_SCHEMA_VERSION
from host_brain.orchestration.planning_admission import (
    PLANNING_ADMISSION_AUTHORITY_MISMATCH,
    PLANNING_ADMISSION_DURABLE_DRIFT,
    GovernedPlanningAdmissionService,
    PlanningAdmissionError,
)
from host_brain.orchestration.planning_graph_compiler import PlanningGraphCompiler
from host_brain.contracts.planning_search.contracts import (
    SearchCandidate,
    SearchCounters,
    SearchResult,
    SearchStatus,
    SearchTraceEvent,
)
from natsume_contracts.task import HostCommandSurface


GRANT = "governor-decision://sha256/" + "a" * 64


def _compiled():
    candidate = SearchCandidate(
        "step",
        payload={
            "worker_route": "skill",
            "target_id": "code-check",
            "objective": "verify code",
            "acceptance_criteria": ["verification evidence exists"],
            "input_refs": ["artifact://requests/gr04-admission"],
            "output_contract": {"type": "verification", "required": ["evidence_ref"]},
            "retry_policy": {"max_attempts": 1},
            "risk_level": 1,
            "capability_requirements": ["code_read"],
            "domain": "code",
            "run_mode": "worker",
            "allowed_actions": ["read_files"],
            "forbidden_actions": ["push", "replan"],
            "verification_plan": {"required": True},
            "approval_policy": {"required": True},
            "review_policy": {"review_required": False, "waiver": "parent_grant"},
            "budget_policy": {
                "schema_version": BUDGET_POLICY_SCHEMA_VERSION,
                "scope": "gr04-admission",
                "limits": {
                    "token_count": 1000,
                    "tool_call_count": 2,
                    "time_ms": 10000,
                    "concurrency": 1,
                    "retry_count": 1,
                    "model_cost_micros": 10000,
                },
                "on_exceeded": "BLOCK",
                "metadata": {"policy_version": "test.v1"},
            },
            "business_idempotency_scope": "gr04-admission",
            "approval_ref": GRANT,
            "review_ref": GRANT,
        },
    )
    result = SearchResult(
        request_id="search-gr04-admission",
        status=SearchStatus.SELECTED,
        stop_reason="selected",
        selected_candidate=candidate,
        selected_path=(candidate,),
        ranked=(),
        trace=(SearchTraceEvent(1, "service", "search_completed", {"selected": "step"}),),
        counters=SearchCounters(),
    )
    return PlanningGraphCompiler().compile(result, created_at=1234)


class _Repository:
    def __init__(self) -> None:
        self.record: dict[str, Any] | None = None
        self.calls = 0

    def admit(self, command, work_order, assignment, graph):
        self.calls += 1
        candidate = {
            "command": command,
            "work_order": work_order,
            "assignment": assignment,
            "graph": graph,
            "created": self.record is None,
            "replayed": self.record is not None,
        }
        if self.record is None:
            self.record = candidate
        return {**self.record, "created": candidate["created"], "replayed": candidate["replayed"]}


def _service(repository: Any) -> GovernedPlanningAdmissionService:
    return GovernedPlanningAdmissionService(
        command_repository=repository,
        runtime_router=CanonicalRuntimeRouter(clock=lambda: 9999),
    )


@pytest.mark.asyncio
async def test_gr04_compiled_plan_admits_exact_deterministic_revision_and_replays() -> None:
    repository = _Repository()
    service = _service(repository)
    kwargs = {
        "surface": HostCommandSurface.CHAT,
        "command_request_id": "request-gr04-admission",
        "session_id": "session-gr04-admission",
        "principal_id": "principal-gr04-admission",
        "input_ref": "input://sha256/" + "b" * 64,
        "input_sha256": "sha256:" + "b" * 64,
        "governor_grant_ref": GRANT,
        "target_id": "plan-gr04-admission",
    }

    first = await service.admit(_compiled(), **kwargs)
    replay = await service.admit(_compiled(), **kwargs)

    assert first.created is True and first.replayed is False
    assert replay.created is False and replay.replayed is True
    assert repository.calls == 2
    assert replay.command.graph_id == replay.graph.graph_id
    assert replay.command.graph_revision == replay.graph.revision == 1
    assert replay.command.operation_fingerprint == replay.work_order.operation_fingerprint
    assert replay.assignment.assigned_at_ms == replay.graph.created_at == 1234


@pytest.mark.asyncio
async def test_gr04_plan_admission_rejects_parent_grant_drift_before_write() -> None:
    repository = _Repository()
    with pytest.raises(PlanningAdmissionError) as error:
        await _service(repository).admit(
            _compiled(),
            surface=HostCommandSurface.CHAT,
            command_request_id="request-gr04-bad-grant",
            session_id="session-gr04",
            principal_id="principal-gr04",
            input_ref="input://sha256/" + "c" * 64,
            input_sha256="sha256:" + "c" * 64,
            governor_grant_ref="governor-decision://sha256/" + "d" * 64,
            target_id="plan-gr04",
        )

    assert error.value.reason_code == PLANNING_ADMISSION_AUTHORITY_MISMATCH
    assert repository.calls == 0


@pytest.mark.asyncio
async def test_gr04_plan_admission_rejects_durable_projection_drift() -> None:
    class DriftRepository(_Repository):
        def admit(self, command, work_order, assignment, graph):
            record = super().admit(command, work_order, assignment, graph)
            record["graph"] = _compiled().task_graph
            record["graph"] = replace(record["graph"], created_at=777)
            return record

    with pytest.raises(PlanningAdmissionError) as error:
        await _service(DriftRepository()).admit(
            _compiled(),
            surface=HostCommandSurface.CHAT,
            command_request_id="request-gr04-drift",
            session_id="session-gr04",
            principal_id="principal-gr04",
            input_ref="input://sha256/" + "e" * 64,
            input_sha256="sha256:" + "e" * 64,
            governor_grant_ref=GRANT,
            target_id="plan-gr04",
        )

    assert error.value.reason_code == PLANNING_ADMISSION_DURABLE_DRIFT


@pytest.mark.asyncio
async def test_gr04_plan_admission_forbids_legacy_plan_payload() -> None:
    with pytest.raises(PlanningAdmissionError, match="CompiledPlanningGraph"):
        await _service(_Repository()).admit(  # type: ignore[arg-type]
            {"planned_steps": [{"route": "skill"}]},
            surface=HostCommandSurface.CHAT,
            command_request_id="request-gr04-legacy",
            session_id="session-gr04",
            principal_id="principal-gr04",
            input_ref="input://sha256/" + "f" * 64,
            input_sha256="sha256:" + "f" * 64,
            governor_grant_ref=GRANT,
            target_id="plan-gr04",
        )
