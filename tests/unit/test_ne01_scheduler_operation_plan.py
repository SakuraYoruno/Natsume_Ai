from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any

import pytest

from host_brain.contracts.agent_runtime.work_order import WorkOrderV3
from host_brain.persistence.worker_projection_repository import (
    WORKER_PROJECTION_SCHEMA_VERSION,
    WorkerProjectionStore,
)
from host_brain.scheduler.contracts import (
    SchedulerDefinitionSnapshot,
    SchedulerOccurrence,
    occurrence_work_order_id,
)
from host_brain.scheduler.operation_plan import (
    SCHEDULER_OPERATION_EFFECT_TYPE,
    SCHEDULER_OPERATION_PLAN_DEFINITION_DRIFT,
    SCHEDULER_OPERATION_PLAN_PROJECTION_INVALID,
    SCHEDULER_OPERATION_PLAN_SCHEMA_VERSION,
    SchedulerOperationPlanAcceptance,
    SchedulerOperationPlanExecutor,
    SchedulerOperationPlanVerifier,
    scheduler_definition_ref,
    scheduler_operation_fingerprint,
)
from host_brain.scheduler.worker_registry import SchedulerWorkerRegistry
from host_brain.task_runtime.governed_workers import FrozenNodeInput
from natsume_contracts.task import HostCommandSurface, build_host_command


ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = (
    ROOT / "host_brain/audit/migrations/0086_task_runtime_worker_projection.sql",
    ROOT / "host_brain/audit/migrations/0114_task_runtime_tool_worker_projection.sql",
)


class _SchedulerAuthorityRepository:
    def __init__(self, occurrence: dict[str, Any], definition: dict[str, Any]) -> None:
        self.occurrence = occurrence
        self.definition = definition

    def get_occurrence(self, occurrence_id: str) -> dict[str, Any] | None:
        if occurrence_id != self.occurrence["occurrence_id"]:
            return None
        return deepcopy(self.occurrence)

    def get_revision(self, task_id: str, revision: int) -> dict[str, Any] | None:
        if (
            task_id != self.definition["task_id"]
            or revision != self.definition["definition_revision"]
        ):
            return None
        return deepcopy(self.definition)


def _digest(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _authority_records() -> tuple[dict[str, Any], dict[str, Any]]:
    occurrence = {
        "occurrence_id": "scheduler-occurrence://manual/runtime-heartbeat/3/request-plan",
        "task_id": "runtime-heartbeat",
        "definition_revision": 3,
        "source": "manual",
        "scheduled_at_ms": 1_750_000_000_000,
        "payload": {"reason": "unit-test"},
        "request_id": "request-scheduler-plan",
        "principal_id": "scheduler-principal",
        "claim_ref": "scheduler-claim://request-scheduler-plan",
        "audit_ref": "scheduler-audit://request-scheduler-plan",
        "body_sha256": "sha256:" + "a" * 64,
    }
    occurrence["payload_digest"] = _digest(occurrence["payload"])
    definition_payload = {
        "task_id": "runtime-heartbeat",
        "worker_key": "host_core",
        "priority": "MEDIUM",
        "risk_level": 2,
        "retry_policy": {"max_attempts": 1},
        "intent_template": {
            "skill_name": "runtime_heartbeat",
            "args": {"source": "scheduler"},
        },
    }
    definition = {
        "task_id": "runtime-heartbeat",
        "definition_revision": 3,
        "head_version": 3,
        "enabled": True,
        "definition": definition_payload,
        "definition_digest": _digest(definition_payload),
    }
    return occurrence, definition


def _contracts(repository: _SchedulerAuthorityRepository) -> tuple[Any, ...]:
    occurrence = SchedulerOccurrence.from_mapping(repository.occurrence)
    definition = SchedulerDefinitionSnapshot.from_mapping(repository.definition)
    definition_ref = scheduler_definition_ref(definition)
    fingerprint = scheduler_operation_fingerprint(
        occurrence=occurrence,
        definition=definition,
        operator=False,
    )
    work_order_id = occurrence_work_order_id(occurrence.occurrence_id)
    graph_id = "graph-scheduler-plan"
    node_id = "node-scheduler-plan"
    business_scope = "scheduler:runtime-heartbeat"
    grant_ref = "governor-grant://scheduler/request-plan"
    work_order = WorkOrderV3(
        work_order_id=work_order_id,
        domain="organ_ops",
        run_mode="worker",
        objective="Prepare the durable Scheduler operation",
        input_pack={
            "occurrence_id": occurrence.occurrence_id,
            "definition_ref": definition_ref,
        },
        allowed_actions=("delegate:host_core",),
        forbidden_actions=("invoke_scheduler_handler_before_acceptance",),
        acceptance_criteria=("Scheduler operation plan is canonical",),
        verification_plan={"required": True, "type": "scheduler_operation_plan"},
        metadata={
            "definition_revision": definition.definition_revision,
            "worker_key": definition.worker_key,
            "command_surface": HostCommandSurface.SCHEDULER.value,
            "planning_lineage": {
                "graph_id": graph_id,
                "root_work_order_id": work_order_id,
                "revision": 1,
                "node_bindings": [
                    {
                        "node_id": node_id,
                        "objective": "Prepare the durable Scheduler operation",
                        "worker_route": "delegate",
                        "target_id": definition.worker_key,
                    }
                ],
            },
        },
        root_work_order_id=work_order_id,
        task_graph_id=graph_id,
        task_node_id=node_id,
        graph_revision=1,
        risk_level=2,
        input_refs=(occurrence.occurrence_id, definition_ref, grant_ref),
        business_idempotency_scope=business_scope,
        operation_fingerprint=fingerprint,
        approval_ref=grant_ref,
    )
    work_order.validate()
    command = build_host_command(
        surface=HostCommandSurface.SCHEDULER,
        request_id=occurrence.request_id,
        session_id="scheduler-session-plan",
        principal_id=occurrence.principal_id,
        ingress_claim_ref=occurrence.claim_ref,
        ingress_audit_ref=occurrence.audit_ref,
        governor_grant_ref=grant_ref,
        input_ref=occurrence.occurrence_id,
        input_sha256=occurrence.body_sha256,
        root_work_order_id=work_order_id,
        graph_id=graph_id,
        graph_revision=1,
        route_type="delegate",
        target_id=definition.worker_key,
        business_scope=business_scope,
        operation_fingerprint=fingerprint,
        created_at_ms=1_750_000_000_001,
    )
    context = {
        "command_id": command.command_id,
        "request_id": command.request_id,
        "principal_id": command.principal_id,
        "work_order_id": work_order.work_order_id,
        "graph_id": graph_id,
        "graph_revision": 1,
        "node_id": node_id,
        "attempt_id": "attempt-scheduler-plan",
        "lease_id": "lease-scheduler-plan",
        "lease_owner_id": "worker-scheduler-plan",
        "fencing_token": 1,
        "worker_route": "delegate:host_core",
        "target_id": "host_core",
        "node_grant_ref": "governor-grant://scheduler/node-plan",
        "business_scope": business_scope,
        "operation_fingerprint": fingerprint,
    }
    frozen = FrozenNodeInput(
        command_id=command.command_id,
        request_id=command.request_id,
        session_id=command.session_id,
        principal_id=command.principal_id,
        work_order_id=work_order.work_order_id,
        root_work_order_id=work_order.root_work_order_id,
        graph_id=graph_id,
        graph_revision=1,
        node_id=node_id,
        attempt_id=context["attempt_id"],
        lease_id=context["lease_id"],
        lease_owner_id=context["lease_owner_id"],
        fencing_token=1,
        node_risk_level=2,
        route="delegate",
        target_id="host_core",
        objective=work_order.objective,
        input_refs=work_order.input_refs,
        _input_pack_json=json.dumps(work_order.input_pack, sort_keys=True),
        _input_artifacts_json="[]",
        node_grant_ref=context["node_grant_ref"],
        parent_grant_ref=grant_ref,
        business_scope=business_scope,
        operation_fingerprint=fingerprint,
    )
    return occurrence, definition, command, work_order, context, frozen


def _projection_store(tmp_path: Path) -> WorkerProjectionStore:
    db_path = tmp_path / "runtime.sqlite3"
    with sqlite3.connect(db_path) as conn:
        for migration in MIGRATIONS:
            conn.executescript(migration.read_text(encoding="utf-8"))
    return WorkerProjectionStore(db_path, clock=lambda: 1_750_000_000_002)


def _stage_projection(
    *,
    store: WorkerProjectionStore,
    command: Any,
    context: dict[str, Any],
    event: dict[str, Any],
) -> dict[str, Any]:
    body = {
        "phase": "COMMIT",
        "done": True,
        "decision": "APPROVE",
        "reason_code": None,
        "command_id": command.command_id,
        "request_id": command.request_id,
        "narrative": event["narrative"],
        "route_type": "delegate",
        "target_id": "host_core",
        "graph_id": command.graph_id,
        "graph_revision": command.graph_revision,
        "node_id": context["node_id"],
        "attempt_id": context["attempt_id"],
        "node_grant_ref": context["node_grant_ref"],
        "audit_refs": {
            **event["audit_refs"],
            "buffered_pre_acceptance_events": 0,
            "node_grant_ref": context["node_grant_ref"],
            "node_grant_digest": "sha256:" + "b" * 64,
        },
        "acceptance_effect_intents": deepcopy(event["acceptance_effect_intents"]),
    }
    projection_id = (
        "worker-projection://graph-scheduler-plan/1/node-scheduler-plan/"
        "attempt-scheduler-plan/plan"
    )
    projection = {
        **body,
        "projection_id": projection_id,
        "payload_digest": _digest(body),
        "schema_version": WORKER_PROJECTION_SCHEMA_VERSION,
    }
    stored = store.stage(projection_id=projection_id, projection=projection)
    return {
        **{
            key: deepcopy(value)
            for key, value in stored.items()
            if key not in {"projection_id", "payload_digest", "schema_version"}
        },
        "receipt_ref": projection_id,
        "audit_refs": {
            **stored["audit_refs"],
            "path": "governed_task_runtime:frozen_node",
            "worker_projection_ref": projection_id,
        },
    }


async def _run_executor(
    repository: _SchedulerAuthorityRepository,
    frozen: FrozenNodeInput,
) -> dict[str, Any]:
    executor = SchedulerOperationPlanExecutor(
        occurrence_repository=repository,
        definition_repository=repository,
    )
    events = [event async for event in executor.execute_stream(frozen)]
    assert len(events) == 1
    return dict(events[0])


@pytest.mark.asyncio
async def test_executor_emits_one_plan_and_never_invokes_registered_handler() -> None:
    occurrence, definition = _authority_records()
    repository = _SchedulerAuthorityRepository(occurrence, definition)
    *_, frozen = _contracts(repository)
    calls = 0

    def handler(**_: Any) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return {"status": "OK"}

    handlers = SchedulerWorkerRegistry()
    handlers.register("host_core", handler)

    event = await _run_executor(repository, frozen)

    assert handlers.worker_keys == ("host_core",)
    assert calls == 0
    assert event["phase"] == "COMMIT"
    assert event["done"] is True
    assert event["decision"] == "APPROVE"
    assert "result" not in event
    assert "receipt_ref" not in event
    assert event["acceptance_effect_intents"] == [
        {
            "effect_type": SCHEDULER_OPERATION_EFFECT_TYPE,
            "commit_scope": "NODE",
            "payload": {
                "schema_version": SCHEDULER_OPERATION_PLAN_SCHEMA_VERSION,
                "occurrence_id": occurrence["occurrence_id"],
                "definition_ref": "scheduler-definition://runtime-heartbeat/3",
                "definition_revision": 3,
                "worker_key": "host_core",
                "operation_fingerprint": frozen.operation_fingerprint,
            },
        }
    ]


@pytest.mark.asyncio
async def test_verifier_reopens_projection_and_rejects_descriptor_tamper(
    tmp_path: Path,
) -> None:
    occurrence, definition = _authority_records()
    repository = _SchedulerAuthorityRepository(occurrence, definition)
    _, _, command, work_order, context, frozen = _contracts(repository)
    event = await _run_executor(repository, frozen)
    event["acceptance_effect_intents"][0]["payload"]["worker_key"] = "forged"
    store = _projection_store(tmp_path)
    commit = _stage_projection(
        store=store,
        command=command,
        context=context,
        event=event,
    )
    verifier = SchedulerOperationPlanVerifier(
        projection_store=store,
        occurrence_repository=repository,
        definition_repository=repository,
    )

    result = verifier.verify(
        command=command,
        work_order=work_order,
        execution_context=context,
        worker_commit=commit,
        evidence_refs=(commit["receipt_ref"],),
    )

    assert result["status"] == "FAIL"
    assert result["reason_code"] == SCHEDULER_OPERATION_PLAN_PROJECTION_INVALID


@pytest.mark.asyncio
async def test_verifier_and_acceptance_reject_definition_revision_drift(
    tmp_path: Path,
) -> None:
    occurrence, definition = _authority_records()
    repository = _SchedulerAuthorityRepository(occurrence, definition)
    _, _, command, work_order, context, frozen = _contracts(repository)
    event = await _run_executor(repository, frozen)
    store = _projection_store(tmp_path)
    commit = _stage_projection(
        store=store,
        command=command,
        context=context,
        event=event,
    )
    verifier = SchedulerOperationPlanVerifier(
        projection_store=store,
        occurrence_repository=repository,
        definition_repository=repository,
    )
    acceptance = SchedulerOperationPlanAcceptance(
        projection_store=store,
        occurrence_repository=repository,
        definition_repository=repository,
    )

    repository.definition["definition"]["intent_template"]["args"]["source"] = (
        "drifted"
    )
    repository.definition["definition_digest"] = _digest(
        repository.definition["definition"]
    )
    verification = verifier.verify(
        command=command,
        work_order=work_order,
        execution_context=context,
        worker_commit=commit,
        evidence_refs=(commit["receipt_ref"],),
    )
    judgement = acceptance.judge(
        command=command,
        work_order=work_order,
        execution_context=context,
        worker_commit=commit,
        verification_result=verification,
        evidence_refs=(commit["receipt_ref"],),
    )

    assert verification["status"] == "FAIL"
    assert verification["reason_code"] == SCHEDULER_OPERATION_PLAN_DEFINITION_DRIFT
    assert judgement["decision"] == "REVIEW"
    assert judgement["reason_code"] == SCHEDULER_OPERATION_PLAN_DEFINITION_DRIFT


@pytest.mark.asyncio
async def test_acceptance_rejects_legacy_domain_result_even_when_projection_is_valid(
    tmp_path: Path,
) -> None:
    occurrence, definition = _authority_records()
    repository = _SchedulerAuthorityRepository(occurrence, definition)
    _, _, command, work_order, context, frozen = _contracts(repository)
    event = await _run_executor(repository, frozen)
    store = _projection_store(tmp_path)
    commit = _stage_projection(
        store=store,
        command=command,
        context=context,
        event=event,
    )
    verifier = SchedulerOperationPlanVerifier(
        projection_store=store,
        occurrence_repository=repository,
        definition_repository=repository,
    )
    acceptance = SchedulerOperationPlanAcceptance(
        projection_store=store,
        occurrence_repository=repository,
        definition_repository=repository,
    )
    verification = verifier.verify(
        command=command,
        work_order=work_order,
        execution_context=context,
        worker_commit=commit,
        evidence_refs=(commit["receipt_ref"],),
    )
    assert verification["status"] == "PASS"

    legacy_commit = {**commit, "result": {"status": "SUCCEEDED"}}
    judgement = acceptance.judge(
        command=command,
        work_order=work_order,
        execution_context=context,
        worker_commit=legacy_commit,
        verification_result=verification,
        evidence_refs=(commit["receipt_ref"],),
    )

    assert judgement["decision"] == "REVIEW"
    assert judgement["reason_code"] == SCHEDULER_OPERATION_PLAN_PROJECTION_INVALID
