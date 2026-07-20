from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from typing import Any

import pytest

from host_brain.agent_runtime.runtime_router import CanonicalRuntimeRouter
from host_brain.scheduler.accepted_operation import (
    SCHEDULER_ACCEPTED_OPERATION_SCHEMA,
    SCHEDULER_ACTION_PREVIEW_CONTEXT_SCHEMA,
    SchedulerAcceptedOperationHandler,
    _attempt_id,
    _scheduler_handler_result,
)
from host_brain.scheduler.contracts import (
    SchedulerDefinitionSnapshot,
    SchedulerOccurrence,
    SchedulerOccurrenceSource,
)
from host_brain.scheduler.governed_runtime import _build_contracts
from host_brain.scheduler.worker_registry import (
    SCHEDULER_DURABLE_RESULT_INVALID,
    SchedulerWorkerError,
    SchedulerWorkerRegistry,
)
from host_brain.task_runtime.acceptance_effect_dispatcher import (
    AcceptanceEffectOutcomeUnknown,
)
from natsume_contracts.task import HostCommandSurface


NOW = 1_800_000_000_000


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _result_record(*, payload: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    digest = "sha256:" + hashlib.sha256(
        _canonical_json(result).encode("utf-8")
    ).hexdigest()
    receipt_digest = hashlib.sha256(
        (
            f"{payload['occurrence_id']}\n{payload['command_id']}\n{digest}"
        ).encode("utf-8")
    ).hexdigest()
    return {
        "receipt_ref": f"schedulerresult://sha256/{receipt_digest}",
        "occurrence_id": payload["occurrence_id"],
        "command_id": payload["command_id"],
        "work_order_id": payload["work_order_id"],
        "result_status": result["status"],
        "result": deepcopy(result),
        "result_digest": digest,
        "created_at_ms": NOW,
    }


class _CommandRepository:
    def __init__(self, command_record: dict[str, Any]) -> None:
        self.command_record = command_record

    def get(self, command_id: str) -> dict[str, Any] | None:
        command = self.command_record["command"]
        return self.command_record if command_id == command.command_id else None


class _OccurrenceRepository:
    def __init__(self, occurrence: SchedulerOccurrence) -> None:
        self.occurrence = occurrence

    def get_occurrence(self, occurrence_id: str) -> dict[str, Any] | None:
        return (
            self.occurrence.to_dict()
            if occurrence_id == self.occurrence.occurrence_id
            else None
        )


class _DefinitionRepository:
    def __init__(self, definition: SchedulerDefinitionSnapshot) -> None:
        self.definition = definition

    def get_revision(self, task_id: str, revision: int) -> dict[str, Any] | None:
        if (
            task_id == self.definition.task_id
            and revision == self.definition.definition_revision
        ):
            return self.definition.to_dict()
        return None


class _ResultRepository:
    def __init__(self) -> None:
        self.record: dict[str, Any] | None = None
        self.append_mode = "ok"
        self.append_calls = 0

    def get_by_command(self, command_id: str) -> dict[str, Any] | None:
        if self.record is None or self.record["command_id"] != command_id:
            return None
        return deepcopy(self.record)

    def append(
        self,
        *,
        occurrence_id: str,
        command_id: str,
        work_order_id: str,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        self.append_calls += 1
        payload = {
            "occurrence_id": occurrence_id,
            "command_id": command_id,
            "work_order_id": work_order_id,
        }
        record = _result_record(payload=payload, result=dict(result))
        if self.append_mode == "fail_before_commit":
            raise OSError("database unavailable")
        self.record = record
        if self.append_mode == "lose_response_after_commit":
            raise TimeoutError("commit response lost")
        return deepcopy(record)


class _Bundle:
    def __init__(self) -> None:
        self.occurrence = SchedulerOccurrence(
            occurrence_id="scheduler-occurrence://manual/accepted-operation-one",
            task_id="accepted_operation_task",
            definition_revision=1,
            source=SchedulerOccurrenceSource.MANUAL,
            scheduled_at_ms=NOW,
            payload={"reason": "unit-test"},
            request_id="request-accepted-operation-one",
            principal_id="scheduler-principal",
            claim_ref="secure-ingress-claim://scheduler/accepted-operation-one",
            audit_ref="audit://scheduler/accepted-operation-one",
            body_sha256="sha256:" + "a" * 64,
        )
        self.definition = SchedulerDefinitionSnapshot(
            task_id=self.occurrence.task_id,
            definition_revision=1,
            head_version=1,
            enabled=True,
            definition={
                "task_id": self.occurrence.task_id,
                "intent_template": {
                    "skill_name": "accepted_operation_test",
                    "args": {"marker": "frozen"},
                },
                "worker_key": "intent_dispatch",
                "retry_policy": {"max_attempts": 2, "retry_backoff_ms": 0},
                "risk_level": 3,
                "enabled": True,
            },
        )
        command, work_order, graph = _build_contracts(
            occurrence=self.occurrence,
            definition=self.definition,
            authority={
                "request_id": self.occurrence.request_id,
                "principal_id": self.occurrence.principal_id,
                "claim_ref": self.occurrence.claim_ref,
                "audit_ref": self.occurrence.audit_ref,
                "body_sha256": self.occurrence.body_sha256,
            },
            governor_grant="governor-grant://scheduler/accepted-operation-one",
            received_at_ms=NOW,
            frozen_runtime_context={
                "runtime_context_schema": "natsume.runtime-context.request.v1",
                "scene_identity_v2": {
                    "workspace_id": "workspace-accepted-operation",
                    "scene_revision": 7,
                    "scene_digest": "sha256:" + "b" * 64,
                },
            },
            command_surface=HostCommandSurface.SCHEDULER,
        )
        assignment = CanonicalRuntimeRouter().assign_new_task(
            work_order.work_order_id,
            assigned_at_ms=NOW,
        )
        node = graph.nodes[0]
        attempt_id = _attempt_id(
            graph_id=graph.graph_id,
            graph_revision=graph.revision,
            node_id=node.node_id,
            assignment_id=assignment.assignment_id,
            attempt_no=1,
        )
        canonical = {
            "schema_version": SCHEDULER_ACCEPTED_OPERATION_SCHEMA,
            "occurrence_id": self.occurrence.occurrence_id,
            "definition_ref": work_order.input_pack["definition_ref"],
            "definition_revision": self.definition.definition_revision,
            "worker_key": self.definition.worker_key,
            "operation_fingerprint": work_order.operation_fingerprint,
            "command_id": command.command_id,
            "request_id": command.request_id,
            "work_order_id": work_order.work_order_id,
            "graph_id": graph.graph_id,
            "graph_revision": graph.revision,
            "node_id": node.node_id,
            "attempt_id": attempt_id,
        }
        boundary = {
            "action_kind": "acceptance-effect.scheduler.operation.execute",
            "action_scope": "acceptance-effect://sha256/" + "c" * 64,
            "risk_level": node.risk_level,
            "action_args": deepcopy(canonical),
            "context_pack_digest": "sha256:" + "d" * 64,
            "scene_identity": {
                "workspace_id": "workspace-accepted-operation",
                "scene_revision": 7,
                "scene_digest": "sha256:" + "b" * 64,
            },
            "task_graph_id": graph.graph_id,
            "graph_revision": graph.revision,
            "task_node_id": node.node_id,
            "parent_governor_grant_ref": command.governor_grant_ref,
            "node_grant_ref": "node-grant://accepted-operation/one",
            "node_grant": {
                "grant_ref": "node-grant://accepted-operation/one",
                "binding_digest": "sha256:" + "e" * 64,
            },
            "authority_context": {
                "schema_version": (
                    "Natsume.action_preview."
                    "acceptance_effect_authority_request.v1"
                ),
                "effect_id": "acceptance-effect://sha256/" + "c" * 64,
                "lease_owner_id": "acceptance-effect-owner-one",
                "fencing_token": 1,
            },
        }
        self.payload = {
            **canonical,
            "_action_preview_context": {
                "schema_version": SCHEDULER_ACTION_PREVIEW_CONTEXT_SCHEMA,
                "origin_authority_kind": "ACCEPTANCE_EFFECT",
                "origin_event_id": "action-preview://event/scheduler-operation-one",
                "origin_receipt_ref": (
                    "action-preview-receipt://scheduler-operation-one"
                ),
                "origin_boundary": deepcopy(boundary),
            },
            "_action_preview_terminal_proof": {
                "service": object(),
                "event_id": "action-preview://event/scheduler-operation-one",
                "boundary": deepcopy(boundary),
            },
        }
        self.command_record = {
            "command": command,
            "work_order": work_order,
            "graph": graph,
            "assignment": assignment,
        }
        self.results = _ResultRepository()
        self.calls: list[dict[str, Any]] = []
        self.handler_result: Any = {
            "status": "OK",
            "summary": "accepted Scheduler handler ran",
        }
        self.handler_error: Exception | None = None
        workers = SchedulerWorkerRegistry()

        async def real_handler(**kwargs: Any) -> Any:
            self.calls.append(kwargs)
            if self.handler_error is not None:
                raise self.handler_error
            return deepcopy(self.handler_result)

        workers.register(self.definition.worker_key, real_handler)
        self.operation = SchedulerAcceptedOperationHandler(
            worker_registry=workers,
            definition_repository=_DefinitionRepository(self.definition),
            occurrence_repository=_OccurrenceRepository(self.occurrence),
            command_repository=_CommandRepository(self.command_record),
            result_repository=self.results,
        )


@pytest.mark.asyncio
async def test_accepted_operation_reopens_all_identities_and_persists_once() -> None:
    bundle = _Bundle()

    outcome = await bundle.operation(bundle.payload)

    assert outcome["status"] == "APPLIED"
    assert outcome["replayed"] is False
    assert bundle.results.append_calls == 1
    assert len(bundle.calls) == 1
    call = bundle.calls[0]
    assert call["occurrence"] == bundle.occurrence
    assert call["definition"] == bundle.definition
    context = call["execution_context"]
    assert context["frozen_input_pack"] == bundle.command_record["work_order"].input_pack
    assert context["_action_preview_context"]["schema_version"] == (
        SCHEDULER_ACTION_PREVIEW_CONTEXT_SCHEMA
    )
    assert not {
        "verified_node_grant",
        "node_grant_ref",
        "parent_grant_ref",
        "lease_id",
        "lease_owner_id",
        "fencing_token",
    }.intersection(context)
    assert bundle.results.record is not None
    assert bundle.results.record["result"] == {
        "status": "SUCCEEDED",
        "summary": "accepted Scheduler handler ran",
    }
    assert "_action_preview_context" not in _canonical_json(bundle.results.record)


class _SerializableSchedulerResult:
    def model_dump(self) -> dict[str, Any]:
        return {
            "status": "SKIPPED",
            "reason_code": "no_due_work",
            "summary": "nothing was eligible",
        }


def test_scheduler_handler_result_accepts_explicit_serializable_object() -> None:
    assert _scheduler_handler_result(_SerializableSchedulerResult()) == {
        "status": "SKIPPED",
        "reason_code": "NO_DUE_WORK",
        "summary": "nothing was eligible",
    }


@pytest.mark.parametrize(
    "result",
    [
        None,
        {},
        {"summary": "missing status"},
        {"status": ""},
        {"status": "UNKNOWN"},
        {"status": "SKIPPED"},
        {"status": "SUCCEEDED", "opaque": object()},
    ],
)
@pytest.mark.asyncio
async def test_invalid_scheduler_handler_result_never_becomes_success(
    result: Any,
) -> None:
    bundle = _Bundle()
    bundle.handler_result = result

    with pytest.raises(AcceptanceEffectOutcomeUnknown):
        await bundle.operation(bundle.payload)

    assert len(bundle.calls) == 1
    assert bundle.results.append_calls == 0
    assert bundle.results.record is None


@pytest.mark.asyncio
async def test_exact_immutable_result_replays_without_resolving_real_handler() -> None:
    bundle = _Bundle()
    result = {"status": "SUCCEEDED", "summary": "already durable"}
    bundle.results.record = _result_record(payload=bundle.payload, result=result)

    outcome = await bundle.operation(bundle.payload)

    assert outcome["status"] == "REPLAYED"
    assert outcome["replayed"] is True
    assert bundle.calls == []
    assert bundle.results.append_calls == 0


@pytest.mark.asyncio
async def test_reconcile_replays_exact_result_without_resolving_worker() -> None:
    bundle = _Bundle()
    result = {"status": "SUCCEEDED", "summary": "reconciled durable result"}
    bundle.results.record = _result_record(payload=bundle.payload, result=result)

    outcome = await bundle.operation.reconcile(bundle.payload)

    assert outcome["status"] == "REPLAYED"
    assert outcome["replayed"] is True
    assert bundle.calls == []
    assert bundle.results.append_calls == 0


@pytest.mark.asyncio
async def test_reconcile_absent_result_stays_unknown_and_never_calls_worker() -> None:
    bundle = _Bundle()

    with pytest.raises(AcceptanceEffectOutcomeUnknown):
        await bundle.operation.reconcile(bundle.payload)

    assert bundle.calls == []
    assert bundle.results.append_calls == 0


def _drift_payload(
    payload: dict[str, Any],
    field: str,
    value: Any,
) -> dict[str, Any]:
    drifted = deepcopy(payload)
    drifted[field] = value
    boundary = drifted["_action_preview_context"]["origin_boundary"]
    boundary["action_args"][field] = value
    drifted["_action_preview_terminal_proof"]["boundary"] = deepcopy(boundary)
    if field == "graph_id":
        boundary["task_graph_id"] = value
        drifted["_action_preview_terminal_proof"]["boundary"] = deepcopy(boundary)
    elif field == "graph_revision":
        boundary["graph_revision"] = value
        drifted["_action_preview_terminal_proof"]["boundary"] = deepcopy(boundary)
    elif field == "node_id":
        boundary["task_node_id"] = value
        drifted["_action_preview_terminal_proof"]["boundary"] = deepcopy(boundary)
    return drifted


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("command_id", "host-command://scheduler/missing"),
        ("request_id", "request-drifted"),
        ("occurrence_id", "scheduler-occurrence://manual/missing"),
        ("definition_revision", 2),
        ("definition_ref", "scheduler-definition://accepted_operation_task/2"),
        ("work_order_id", "scheduler-work-drifted"),
        ("graph_id", "graph-scheduler-drifted"),
        ("graph_revision", 2),
        ("node_id", "node-scheduler-drifted"),
        ("attempt_id", "attempt::governed::" + "f" * 64),
        ("worker_key", "other_worker"),
        ("operation_fingerprint", "sha256:" + "f" * 64),
    ],
)
async def test_each_accepted_identity_drift_fails_before_handler(
    field: str,
    value: Any,
) -> None:
    bundle = _Bundle()

    with pytest.raises(SchedulerWorkerError) as raised:
        await bundle.operation(_drift_payload(bundle.payload, field, value))

    assert raised.value.reason_code == SCHEDULER_DURABLE_RESULT_INVALID
    assert bundle.calls == []
    assert bundle.results.append_calls == 0


@pytest.mark.asyncio
async def test_private_preview_requires_v2_and_exact_terminal_cross_check() -> None:
    bundle = _Bundle()
    legacy = deepcopy(bundle.payload)
    legacy["_action_preview_context"]["schema_version"] = (
        "Natsume.scheduler.action_preview_context.v1"
    )
    with pytest.raises(SchedulerWorkerError):
        await bundle.operation(legacy)

    terminal_drift = deepcopy(bundle.payload)
    terminal_drift["_action_preview_terminal_proof"]["event_id"] = (
        "action-preview://event/other"
    )
    with pytest.raises(SchedulerWorkerError):
        await bundle.operation(terminal_drift)

    assert bundle.calls == []


@pytest.mark.asyncio
async def test_handler_exception_is_unknown_and_never_becomes_ordinary_failure() -> None:
    bundle = _Bundle()
    bundle.handler_error = TimeoutError("downstream response lost")

    with pytest.raises(AcceptanceEffectOutcomeUnknown):
        await bundle.operation(bundle.payload)

    assert len(bundle.calls) == 1
    assert bundle.results.record is None


@pytest.mark.asyncio
async def test_append_failure_without_exact_readback_is_unknown() -> None:
    bundle = _Bundle()
    bundle.results.append_mode = "fail_before_commit"

    with pytest.raises(AcceptanceEffectOutcomeUnknown):
        await bundle.operation(bundle.payload)

    assert len(bundle.calls) == 1
    assert bundle.results.append_calls == 1


@pytest.mark.asyncio
async def test_lost_append_response_reopens_exact_result_without_reinvocation() -> None:
    bundle = _Bundle()
    bundle.results.append_mode = "lose_response_after_commit"

    outcome = await bundle.operation(bundle.payload)
    replay = await bundle.operation(bundle.payload)

    assert outcome["status"] == "REPLAYED"
    assert replay["status"] == "REPLAYED"
    assert len(bundle.calls) == 1
    assert bundle.results.append_calls == 1


@pytest.mark.asyncio
async def test_existing_result_identity_drift_is_not_treated_as_exact_replay() -> None:
    bundle = _Bundle()
    result = {"status": "SUCCEEDED", "summary": "wrong owner"}
    bundle.results.record = _result_record(payload=bundle.payload, result=result)
    bundle.results.record["work_order_id"] = "scheduler-work-other"

    with pytest.raises(SchedulerWorkerError):
        await bundle.operation(bundle.payload)

    assert bundle.calls == []
