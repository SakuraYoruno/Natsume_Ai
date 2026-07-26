from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass
import hashlib
import multiprocessing
from pathlib import Path
from queue import Empty
import sqlite3
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.contracts.agent_runtime.budget_policy import (
    BudgetAmount,
    BudgetPolicy,
    BudgetReservation,
    BudgetReservationStatus,
)
from host_brain.contracts.agent_runtime.graph_cancellation import (
    GRAPH_CANCELLATION_INTENT_SCHEMA_VERSION,
    normalize_graph_cancellation_intent,
)
from host_brain.contracts.agent_runtime.task_graph import (
    TaskGraph,
    TaskGraphStatus,
    TaskNode,
    TaskNodeStatus,
)
from host_brain.contracts.agent_runtime.work_order import WorkOrderV3
from host_brain.persistence.host_command_repository import HostCommandRepository
from host_brain.persistence.task_repository import (
    AttemptStatus,
    DurableTaskStore,
    Ed25519ReconciliationProofVerifier,
    OutboxMessage,
    TASK_STORE_LEASE_CONFLICT,
    TaskStoreError,
    build_outbox_payload_ref,
    build_reconciliation_attestation_binding,
)
from host_brain.task_runtime.graph_cancellation_operation_reconciliation import (
    GraphCancellationOperationReconciliationService,
)
from host_brain.task_runtime.graph_cancellation_continuation_worker import (
    GraphCancellationContinuationWorker,
)
from host_brain.task_runtime.graph_cancellation_runtime import (
    GraphCancellationRuntime,
)
from host_brain.task_runtime.local_graph_execution_registry import (
    LocalGraphExecutionRegistry,
)
from host_brain.task_runtime.runtime_outbox import RuntimeOutbox
from natsume_contracts.task.v1 import (
    ExecutorAssignment,
    HostCommandSurface,
    TaskExecutor,
    build_host_command,
)


NOW = 1_950_000_000_000


@dataclass(frozen=True)
class _Clock:
    value: int

    def __call__(self) -> int:
        return self.value


class _NoopOutboxPublisher:
    def __init__(self) -> None:
        self.calls = 0

    def publish_pending(self, *, limit: int) -> dict[str, int | str]:
        self.calls += 1
        return {"status": "OK", "published_count": 0, "limit": limit}


def _sha256(value: str) -> str:
    return f"sha256:{hashlib.sha256(value.encode('utf-8')).hexdigest()}"


def _event(
    aggregate_id: str,
    aggregate_version: int,
    event_type: str,
    *,
    outbox_id: str,
    evidence_refs: tuple[str, ...] = (),
) -> OutboxMessage:
    return OutboxMessage.create(
        aggregate_type="TASK_GRAPH",
        aggregate_id=aggregate_id,
        graph_revision=1,
        aggregate_version=aggregate_version,
        event_type=event_type,
        payload_ref=build_outbox_payload_ref(
            aggregate_type="TASK_GRAPH",
            aggregate_id=aggregate_id,
            aggregate_version=aggregate_version,
            graph_revision=1,
        ),
        idempotency_key=f"idempotency:{outbox_id}",
        outbox_id=outbox_id,
        evidence_refs=evidence_refs,
    )


def _runtime_event(
    aggregate_type: str,
    aggregate_id: str,
    aggregate_version: int,
    event_type: str,
    *,
    outbox_id: str,
    attempt_id: str | None = None,
) -> OutboxMessage:
    return OutboxMessage.create(
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        aggregate_version=aggregate_version,
        event_type=event_type,
        payload_ref=build_outbox_payload_ref(
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            aggregate_version=aggregate_version,
        ),
        idempotency_key=f"idempotency:{outbox_id}",
        outbox_id=outbox_id,
        attempt_id=attempt_id,
    )


def _seed_requested_intent(
    tmp_path: Path,
    *,
    reconciliation_private_key: Ed25519PrivateKey | None = None,
    reconcile_worker_interruption: bool = True,
) -> tuple[Path, str]:
    db_path = tmp_path / "graph-cancellation-cross-process.sqlite3"
    migrated = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="ne01-graph-cancellation-cross-process",
        ts_ms=NOW,
    )
    assert migrated.success is True, migrated.as_dict()
    private_key = reconciliation_private_key
    seeded_worker_interruption = private_key is not None
    proof_verifier = None
    if private_key is not None:
        public_key = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        proof_verifier = Ed25519ReconciliationProofVerifier(
            {"reconciler-cross-process-v1": public_key}
        )
    store = DurableTaskStore(
        db_path,
        clock=_Clock(NOW),
        reconciliation_proof_verifier=proof_verifier,
    )
    graph_id = "graph-cross-process-cancel"
    work_order_id = "work-order-cross-process-cancel"
    node_id = "node-1"
    input_ref = "host-input://request-cross-process-cancel"
    operation_fingerprint = _sha256("cross-process-cancellation-operation")
    work_order = WorkOrderV3(
        work_order_id=work_order_id,
        domain="chat",
        run_mode="inspect",
        objective="continue one durably requested Graph cancellation",
        input_pack={"input_ref": input_ref},
        forbidden_actions=("write_files",),
        acceptance_criteria=("cancellation reaches a durable decision",),
        root_work_order_id=work_order_id,
        task_graph_id=graph_id,
        task_node_id=node_id,
        graph_revision=1,
        input_refs=(input_ref,),
        output_contract={"type": "cancellation_receipt"},
        budget_policy=BudgetPolicy(
            scope="graph:graph-cross-process-cancel@1",
            limits=BudgetAmount(
                token_count=1_000,
                tool_call_count=10,
                time_ms=60_000,
                concurrency=1,
                retry_count=1,
                model_cost_micros=100_000,
            ),
        ),
        business_idempotency_scope="graph-cancellation-cross-process",
        operation_fingerprint=operation_fingerprint,
    )
    graph = TaskGraph(
        graph_id=graph_id,
        root_work_order_id=work_order_id,
        revision=1,
        planner_ref="planner://graph-cancellation/cross-process-test",
        status=TaskGraphStatus.CREATED,
        created_at=NOW,
        nodes=(
            TaskNode(
                node_id=node_id,
                dependencies=(),
                route="host_chat",
                input_refs=(input_ref,),
                output_contract={"type": "cancellation_receipt"},
                retry_policy={"max_attempts": 1, "retry_backoff_ms": 0},
                risk_level=1,
                acceptance_criteria=(),
                status=TaskNodeStatus.READY,
                status_version=0,
            ),
        ),
        result_node_id=node_id,
    )
    assignment = ExecutorAssignment(
        work_order_id=work_order_id,
        executor=TaskExecutor.CANONICAL,
        assignment_id=(
            "runtime-assignment://work-order-cross-process-cancel/canonical"
        ),
        assigned_at_ms=NOW,
        is_new_task=True,
        reason_code="RUNTIME_NEW_TASK_CANONICAL",
    )
    command = build_host_command(
        surface=HostCommandSurface.CHAT,
        request_id="request-cross-process-cancel",
        session_id="session-cross-process-cancel",
        principal_id="principal-cross-process-cancel",
        governor_grant_ref="governor-grant://cross-process-cancel/1",
        input_ref=input_ref,
        input_sha256=_sha256("cross-process-cancellation-input"),
        root_work_order_id=work_order_id,
        graph_id=graph_id,
        graph_revision=1,
        route_type="chat",
        target_id=node_id,
        business_scope="graph-cancellation-cross-process",
        operation_fingerprint=operation_fingerprint,
        created_at_ms=NOW,
    )
    admitted = HostCommandRepository(db_path, clock=_Clock(NOW)).admit(
        command,
        work_order,
        assignment,
        graph,
    )
    assert admitted["created"] is True
    store.transition_graph(
        graph_id,
        1,
        expected_version=0,
        target_status=TaskGraphStatus.ACTIVE,
        outbox=_event(
            graph_id,
            1,
            "TASK_GRAPH_ACTIVE",
            outbox_id="outbox-cross-process-active",
        ),
    )
    node_states_before = {
        "node-1": {"status": "READY", "status_version": 0},
    }
    node_states_after = {
        "node-1": {"status": "CANCEL_REQUESTED", "status_version": 1},
    }
    budget_id: str | None = None
    budget_status_before: str | None = None
    budget_status_version_before: int | None = None
    if seeded_worker_interruption:
        budget_id = "budget-cross-process-interrupted"
        store.create_budget_reservation(
            BudgetReservation(
                budget_id=budget_id,
                scope=f"graph:{graph_id}@1",
                limits=BudgetAmount(
                    token_count=1_000,
                    tool_call_count=10,
                    time_ms=60_000,
                    concurrency=1,
                    retry_count=1,
                    model_cost_micros=100_000,
                ),
                reserved=BudgetAmount(
                    token_count=1_000,
                    tool_call_count=10,
                    time_ms=60_000,
                    concurrency=1,
                    retry_count=1,
                    model_cost_micros=100_000,
                ),
            ),
            graph_id=graph_id,
            graph_revision=1,
            node_id=None,
            attempt_id=None,
            outbox=_runtime_event(
                "BUDGET_RESERVATION",
                budget_id,
                0,
                "BUDGET_RESERVATION_CREATED",
                outbox_id="outbox-cross-process-budget-created",
            ),
        )
        store.transition_budget(
            budget_id,
            expected_version=0,
            target_status=BudgetReservationStatus.ACTIVE,
            outbox=_runtime_event(
                "BUDGET_RESERVATION",
                budget_id,
                1,
                "BUDGET_RESERVATION_ACTIVE",
                outbox_id="outbox-cross-process-budget-active",
            ),
        )
        budget_status_before = "ACTIVE"
        budget_status_version_before = 1
        attempt_id = "attempt-cross-process-interrupted"
        store.create_attempt(
            attempt_id=attempt_id,
            graph_id=graph_id,
            graph_revision=1,
            node_id=node_id,
            attempt_no=1,
            expected_node_version=0,
            operation_scope="graph-cancellation-cross-process",
            operation_fingerprint=operation_fingerprint,
            outbox=_runtime_event(
                "TASK_ATTEMPT",
                attempt_id,
                0,
                "TASK_ATTEMPT_CREATED",
                outbox_id="outbox-cross-process-attempt-created",
                attempt_id=attempt_id,
            ),
        )
        lease = store.acquire_lease(
            attempt_id,
            owner_id="worker-cross-process",
            ttl_ms=60_000,
            expected_attempt_version=0,
            outbox=_runtime_event(
                "TASK_ATTEMPT",
                attempt_id,
                1,
                "TASK_ATTEMPT_LEASED",
                outbox_id="outbox-cross-process-attempt-leased",
                attempt_id=attempt_id,
            ),
        )
        store.transition_node(
            graph_id,
            1,
            node_id,
            expected_version=1,
            target_status=TaskNodeStatus.RUNNING,
            outbox=_runtime_event(
                "TASK_NODE",
                f"{graph_id}@1/{node_id}",
                2,
                "TASK_NODE_RUNNING",
                outbox_id="outbox-cross-process-node-running",
            ),
        )
        store.transition_attempt(
            attempt_id,
            owner_id="worker-cross-process",
            fencing_token=int(lease["fencing_token"]),
            expected_version=1,
            target_status=AttemptStatus.RUNNING,
            outbox=_runtime_event(
                "TASK_ATTEMPT",
                attempt_id,
                2,
                "TASK_ATTEMPT_RUNNING",
                outbox_id="outbox-cross-process-attempt-running",
                attempt_id=attempt_id,
            ),
        )
        reserved = store.reserve_operation(
            reservation_id="reservation-cross-process-interrupted",
            business_scope="graph-cancellation-cross-process",
            operation_fingerprint=operation_fingerprint,
            attempt_id=attempt_id,
            lease_id=str(lease["lease_id"]),
            owner_id="worker-cross-process",
            fencing_token=int(lease["fencing_token"]),
            ttl_ms=60_000,
            outbox=_runtime_event(
                "OPERATION_RESERVATION",
                "reservation-cross-process-interrupted",
                0,
                "OPERATION_RESERVED",
                outbox_id="outbox-cross-process-operation-reserved",
                attempt_id=attempt_id,
            ),
        )
        assert reserved.reservation["status"] == "RESERVED"
        interrupted = store.mark_worker_interruption_recovery_required(
            "reservation-cross-process-interrupted",
            attempt_id=attempt_id,
            lease_id=str(lease["lease_id"]),
            owner_id="worker-cross-process",
            fencing_token=int(lease["fencing_token"]),
            expected_version=0,
            reason_code="GOVERNED_WORKER_FAILED",
        )
        assert interrupted["status"] == "RECOVERY_REQUIRED"
        node_states_before = {
            "node-1": {"status": "RUNNING", "status_version": 2},
        }
        node_states_after = {
            "node-1": {"status": "CANCEL_REQUESTED", "status_version": 3},
        }
    payload = {
        "schema_version": GRAPH_CANCELLATION_INTENT_SCHEMA_VERSION,
        "request_id": "request-cross-process-cancel",
        "request_identity": "principal://operator/cross-process-test",
        "source": "OPERATOR",
        "reason_code": "OPERATOR_CANCELLED_TASK",
        "graph_id": graph_id,
        "graph_revision": 1,
        "graph_status_before": "ACTIVE",
        "graph_status_version_before": 1,
        "graph_target_status": "BLOCKED",
        "budget_id": budget_id,
        "budget_status_before": budget_status_before,
        "budget_status_version_before": budget_status_version_before,
        "node_states_before": node_states_before,
        "node_states_after": node_states_after,
        "graph_outbox_id": "outbox-cross-process-cancel-request",
    }
    intent = normalize_graph_cancellation_intent(payload)
    requested = store.request_graph_cancellation(
        graph_id,
        1,
        budget_id=budget_id,
        expected_graph_version=1,
        expected_budget_version=budget_status_version_before,
        expected_node_states=payload["node_states_before"],
        intent_ref=intent.intent_ref,
        intent_payload=payload,
        graph_outbox=_event(
            graph_id,
            2,
            "TASK_GRAPH_CANCELLATION_REQUESTED",
            outbox_id="outbox-cross-process-cancel-request",
            evidence_refs=(intent.intent_ref,),
        ),
    )
    assert requested["intent"]["status"] == "REQUESTED"
    if seeded_worker_interruption and reconcile_worker_interruption:
        assert private_key is not None
        intent_ref = str(requested["intent"]["intent_ref"])
        recovery_version = int(
            store.get_operation_reservation(
                "reservation-cross-process-interrupted"
            )["status_version"]
        )
        actor_id = "reconciler://operator/cross-process-test"
        attestation_ref = "proof://graph-cancellation/cross-process-release"
        proof_binding = build_reconciliation_attestation_binding(
            reservation_id="reservation-cross-process-interrupted",
            business_scope="graph-cancellation-cross-process",
            operation_fingerprint=operation_fingerprint,
            recovery_version=recovery_version,
            reconciler_actor_id=actor_id,
            attestation_ref=attestation_ref,
            recovery_action="RELEASE",
            reconciliation_outcome="NO_SIDE_EFFECT",
            receipt_ref=None,
        )
        signature = "ed25519:" + base64.urlsafe_b64encode(
            private_key.sign(proof_binding)
        ).decode("ascii")
        reconciliation = GraphCancellationOperationReconciliationService(
            task_store=store,
            outbox_factory=OutboxMessage,
            wake_cancellation=lambda: None,
        )
        request = {
            "intent_ref": intent_ref,
            "reservation_id": "reservation-cross-process-interrupted",
            "expected_version": recovery_version,
            "action": "RELEASE",
            "reconciler_actor_id": actor_id,
            "downstream_receipt_ref": None,
            "attestation_ref": attestation_ref,
            "attestation_key_id": "reconciler-cross-process-v1",
            "attestation_signature": signature,
        }
        first = reconciliation.reconcile_graph_cancellation_operation(**request)
        replay = reconciliation.reconcile_graph_cancellation_operation(**request)
        assert first["replayed"] is False
        assert replay["replayed"] is True
        assert first["operation"]["status"] == "RELEASED"
    return db_path, intent.intent_ref


def test_cancellation_crossing_operation_hold_is_durable_and_immutable(
    tmp_path: Path,
) -> None:
    db_path, intent_ref = _seed_requested_intent(
        tmp_path,
        reconciliation_private_key=Ed25519PrivateKey.generate(),
        reconcile_worker_interruption=False,
    )
    store = DurableTaskStore(db_path, clock=_Clock(NOW))

    first = store.record_operation_recovery_hold(
        graph_id="graph-cross-process-cancel",
        graph_revision=1,
        node_id="node-1",
        attempt_id="attempt-cross-process-interrupted",
        reservation_id="reservation-cross-process-interrupted",
        budget_id="budget-cross-process-interrupted",
        reason_code="GOVERNED_WORKER_FAILED",
    )
    replay = store.record_operation_recovery_hold(
        graph_id="graph-cross-process-cancel",
        graph_revision=1,
        node_id="node-1",
        attempt_id="attempt-cross-process-interrupted",
        reservation_id="reservation-cross-process-interrupted",
        budget_id="budget-cross-process-interrupted",
        reason_code="GOVERNED_WORKER_FAILED",
    )

    assert first["exact_replay"] is False
    assert replay["exact_replay"] is True
    assert replay["case_ref"] == first["case_ref"]
    assert first["case_kind"] == "CANCELLATION_CROSSING"
    assert (
        first["classification"]
        == "SIGNED_CANCELLATION_RECONCILIATION_REQUIRED"
    )
    assert first["cancellation_intent_ref"] == intent_ref
    assert first["cancellation_status"] == "REQUESTED"
    current = store.list_current_operation_recovery_holds(
        graph_id="graph-cross-process-cancel",
    )
    assert len(current) == 1
    assert current[0]["case_ref"] == first["case_ref"]
    with store._connect() as conn:
        outbox_count = conn.execute(
            """
            SELECT COUNT(*) FROM task_runtime_outbox
            WHERE event_type = 'OPERATION_RECOVERY_HOLD_RECORDED'
            """,
        ).fetchone()[0]
        assert outbox_count == 1
        with pytest.raises(
            sqlite3.IntegrityError,
            match="TASK_OPERATION_RECOVERY_HOLD_IMMUTABLE",
        ):
            conn.execute(
                """
                UPDATE task_runtime_operation_recovery_hold
                SET reason_code = 'TAMPERED'
                WHERE case_ref = ?
                """,
                (first["case_ref"],),
            )


def test_signed_cancellation_reconciliation_consumes_exact_recovery_hold(
    tmp_path: Path,
) -> None:
    private_key = Ed25519PrivateKey.generate()
    db_path, intent_ref = _seed_requested_intent(
        tmp_path,
        reconciliation_private_key=private_key,
        reconcile_worker_interruption=False,
    )
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    key_id = "reconciler-cross-process-v1"
    store = DurableTaskStore(
        db_path,
        clock=_Clock(NOW),
        reconciliation_proof_verifier=Ed25519ReconciliationProofVerifier(
            {key_id: public_key}
        ),
    )
    hold = store.record_operation_recovery_hold(
        graph_id="graph-cross-process-cancel",
        graph_revision=1,
        node_id="node-1",
        attempt_id="attempt-cross-process-interrupted",
        reservation_id="reservation-cross-process-interrupted",
        budget_id="budget-cross-process-interrupted",
        reason_code="GOVERNED_WORKER_FAILED",
    )
    operation = store.get_operation_reservation(
        "reservation-cross-process-interrupted"
    )
    actor_id = "reconciler://operator/cross-process-hold"
    attestation_ref = "proof://graph-cancellation/cross-process-hold-release"
    binding = build_reconciliation_attestation_binding(
        reservation_id=operation["reservation_id"],
        business_scope=operation["business_scope"],
        operation_fingerprint=operation["operation_fingerprint"],
        recovery_version=operation["status_version"],
        reconciler_actor_id=actor_id,
        attestation_ref=attestation_ref,
        recovery_action="RELEASE",
        reconciliation_outcome="NO_SIDE_EFFECT",
    )
    signature = "ed25519:" + base64.urlsafe_b64encode(
        private_key.sign(binding)
    ).decode("ascii")
    evidence_fields = {
        "request_id": "request-cross-process-hold-release",
        "ingress_audit_ref": "secure-ingress-audit://operation/hold-release",
        "body_sha256": "a" * 64,
        "reservation_id": operation["reservation_id"],
        "expected_version": operation["status_version"],
        "action": "RELEASE",
        "intent_ref": intent_ref,
        "reconciler_actor_id": actor_id,
        "downstream_receipt_ref": None,
        "attestation_ref": attestation_ref,
        "attestation_key_id": key_id,
        "attestation_signature": signature,
    }
    with pytest.raises(
        TaskStoreError,
        match="one exact current recovery case",
    ):
        store.stage_operation_reconciliation_evidence(**evidence_fields)
    with pytest.raises(
        TaskStoreError,
        match="Operation reconciliation evidence fields are invalid",
    ):
        store.stage_operation_reconciliation_evidence(
            **evidence_fields,
            recovery_case_ref=hold["case_ref"],
            recovery_case_subject_sha256="sha256:" + "f" * 63,
        )

    evidence = store.stage_operation_reconciliation_evidence(
        **evidence_fields,
        recovery_case_ref=hold["case_ref"],
        recovery_case_subject_sha256=hold["subject_sha256"],
    )
    assert evidence["exact_replay"] is False
    assert evidence["recovery_case_ref"] == hold["case_ref"]
    assert evidence["recovery_case_subject_sha256"] == hold["subject_sha256"]
    with pytest.raises(
        TaskStoreError,
        match="evidence identity conflict",
    ):
        store.stage_operation_reconciliation_evidence(
            **{
                **evidence_fields,
                "request_id": "request-cross-process-hold-release-duplicate",
            },
            recovery_case_ref=hold["case_ref"],
            recovery_case_subject_sha256=hold["subject_sha256"],
        )

    service = GraphCancellationOperationReconciliationService(
        task_store=store,
        outbox_factory=OutboxMessage,
        wake_cancellation=lambda: None,
    )
    settled = service.reconcile_graph_cancellation_operation(
        intent_ref=intent_ref,
        reservation_id=operation["reservation_id"],
        expected_version=operation["status_version"],
        action="RELEASE",
        reconciler_actor_id=actor_id,
        downstream_receipt_ref=None,
        attestation_ref=attestation_ref,
        attestation_key_id=key_id,
        attestation_signature=signature,
    )
    assert settled["operation"]["status"] == "RELEASED"
    assert store.get_operation_recovery_hold(hold["case_ref"])[
        "current"
    ] is False
    assert store.list_current_operation_recovery_holds(
        graph_id="graph-cross-process-cancel"
    ) == ()

    reopened = DurableTaskStore(
        db_path,
        clock=_Clock(NOW),
        reconciliation_proof_verifier=Ed25519ReconciliationProofVerifier(
            {key_id: public_key}
        ),
    )
    replay = reopened.get_operation_reconciliation_evidence(
        evidence["evidence_ref"]
    )
    assert replay["evidence_digest"] == evidence["evidence_digest"]
    assert replay["recovery_case_ref"] == hold["case_ref"]
    assert reopened.get_operation_recovery_hold(hold["case_ref"])[
        "current"
    ] is False
    with reopened._connect() as conn:
        with pytest.raises(
            sqlite3.IntegrityError,
            match="TASK_OPERATION_RECONCILIATION_EVIDENCE_IMMUTABLE",
        ):
            conn.execute(
                """
                UPDATE task_runtime_operation_reconciliation_evidence
                SET recovery_case_subject_sha256 = ?
                WHERE evidence_ref = ?
                """,
                ("sha256:" + "0" * 64, evidence["evidence_ref"]),
            )


def _claim_in_process(
    db_path: str,
    worker_id: str,
    now_ts_ms: int,
    lease_ms: int,
    start: Any,
    queue: Any,
) -> None:
    try:
        if not start.wait(10):
            queue.put({"kind": "error", "detail": "start barrier timed out"})
            return
        claimed = DurableTaskStore(
            Path(db_path),
            clock=_Clock(now_ts_ms),
        ).claim_pending_graph_cancellations(worker_id, lease_ms, limit=1)
        queue.put(
            {
                "kind": "ok",
                "claim": (
                    {
                        "intent_ref": claimed[0]["intent_ref"],
                        "claim_owner": claimed[0]["claim_owner"],
                        "claim_token": claimed[0]["claim_token"],
                        "claim_expires_ts_ms": claimed[0]["claim_expires_ts_ms"],
                        "claim_fencing_token": claimed[0]["claim_fencing_token"],
                        "attempt_count": claimed[0]["attempt_count"],
                        "status_version": claimed[0]["status_version"],
                    }
                    if claimed
                    else None
                ),
            }
        )
    except Exception as exc:  # pragma: no cover - surfaced by parent assertion
        queue.put({"kind": "error", "detail": f"{type(exc).__name__}:{exc}"})


def _take_over_and_probe_stale_claim(
    db_path: str,
    intent_ref: str,
    old_claim: dict[str, Any],
    now_ts_ms: int,
    queue: Any,
) -> None:
    try:
        worker_id = "cancellation-worker-takeover"
        store = DurableTaskStore(Path(db_path), clock=_Clock(now_ts_ms))
        claimed = store.claim_pending_graph_cancellations(
            worker_id,
            1_000,
            limit=1,
        )
        if len(claimed) != 1:
            queue.put(
                {
                    "kind": "error",
                    "detail": f"takeover claim count was {len(claimed)}",
                }
            )
            return
        takeover = claimed[0]
        stale_results: dict[str, dict[str, str]] = {}
        mutations = {
            "defer": lambda: store.defer_graph_cancellation(
                intent_ref,
                worker_id,
                old_claim["claim_token"],
                old_claim["claim_fencing_token"],
                100,
                "CANCELLATION_NOT_STABLE:cross-process-test",
                takeover["status_version"],
            ),
            "complete": lambda: store.complete_graph_cancellation(
                intent_ref,
                worker_id,
                old_claim["claim_token"],
                old_claim["claim_fencing_token"],
                takeover["status_version"],
            ),
        }
        for name, mutation in mutations.items():
            try:
                mutation()
            except TaskStoreError as exc:
                stale_results[name] = exc.to_dict()
            else:
                stale_results[name] = {
                    "reason_code": "UNEXPECTED_SUCCESS",
                    "detail": "stale mutation unexpectedly committed",
                }
        current = store.get_graph_cancellation_continuation(intent_ref)
        queue.put(
            {
                "kind": "ok",
                "takeover": {
                    "intent_ref": takeover["intent_ref"],
                    "claim_owner": takeover["claim_owner"],
                    "claim_token": takeover["claim_token"],
                    "claim_expires_ts_ms": takeover["claim_expires_ts_ms"],
                    "claim_fencing_token": takeover["claim_fencing_token"],
                    "attempt_count": takeover["attempt_count"],
                    "status_version": takeover["status_version"],
                },
                "stale_results": stale_results,
                "current": {
                    "claim_owner": current["claim_owner"] if current else None,
                    "claim_token": current["claim_token"] if current else None,
                    "claim_fencing_token": (
                        current["claim_fencing_token"] if current else None
                    ),
                    "status_version": current["status_version"] if current else None,
                },
            }
        )
    except Exception as exc:  # pragma: no cover - surfaced by parent assertion
        queue.put({"kind": "error", "detail": f"{type(exc).__name__}:{exc}"})


def _reported(queue: Any, *, count: int) -> list[dict[str, Any]]:
    try:
        return [queue.get(timeout=10) for _ in range(count)]
    except Empty as exc:  # pragma: no cover - diagnostic failure
        raise AssertionError("child process did not report cancellation result") from exc


def test_cross_process_cancellation_claim_takeover_fences_stale_mutations(
    tmp_path: Path,
) -> None:
    db_path, intent_ref = _seed_requested_intent(tmp_path)
    context = multiprocessing.get_context("spawn")
    start = context.Event()
    race_queue = context.Queue()
    lease_ms = 100
    racers = [
        context.Process(
            target=_claim_in_process,
            args=(
                str(db_path),
                worker_id,
                NOW,
                lease_ms,
                start,
                race_queue,
            ),
        )
        for worker_id in ("cancellation-worker-a", "cancellation-worker-b")
    ]
    for process in racers:
        process.start()
    start.set()
    for process in racers:
        process.join(20)
        assert process.exitcode == 0
    race_results = _reported(race_queue, count=len(racers))

    assert all(result["kind"] == "ok" for result in race_results), race_results
    claims = [result["claim"] for result in race_results if result["claim"]]
    assert len(claims) == 1
    old_claim = claims[0]
    assert old_claim["intent_ref"] == intent_ref
    assert old_claim["claim_fencing_token"] == 1
    assert old_claim["attempt_count"] == 1
    assert old_claim["status_version"] == 1
    assert old_claim["claim_expires_ts_ms"] == NOW + lease_ms

    takeover_queue = context.Queue()
    takeover_process = context.Process(
        target=_take_over_and_probe_stale_claim,
        args=(
            str(db_path),
            intent_ref,
            old_claim,
            NOW + lease_ms,
            takeover_queue,
        ),
    )
    takeover_process.start()
    takeover_process.join(20)
    assert takeover_process.exitcode == 0
    takeover_result = _reported(takeover_queue, count=1)[0]

    assert takeover_result["kind"] == "ok", takeover_result
    takeover = takeover_result["takeover"]
    assert takeover["claim_owner"] == "cancellation-worker-takeover"
    assert takeover["claim_token"] != old_claim["claim_token"]
    assert takeover["claim_fencing_token"] == 2
    assert takeover["attempt_count"] == 2
    assert takeover["status_version"] == 2
    assert takeover_result["stale_results"]["defer"]["reason_code"] == (
        TASK_STORE_LEASE_CONFLICT
    )
    assert takeover_result["stale_results"]["complete"]["reason_code"] == (
        TASK_STORE_LEASE_CONFLICT
    )
    assert "fence is stale" in takeover_result["stale_results"]["defer"]["detail"]
    assert "fence is stale" in takeover_result["stale_results"]["complete"]["detail"]
    assert takeover_result["current"] == {
        "claim_owner": takeover["claim_owner"],
        "claim_token": takeover["claim_token"],
        "claim_fencing_token": takeover["claim_fencing_token"],
        "status_version": takeover["status_version"],
    }


@pytest.mark.asyncio
async def test_real_worker_startup_scan_finalizes_requested_intent(
    tmp_path: Path,
) -> None:
    db_path, intent_ref = _seed_requested_intent(tmp_path)
    store = DurableTaskStore(db_path, clock=_Clock(NOW))
    publisher = _NoopOutboxPublisher()
    runtime = GraphCancellationRuntime(
        task_store=store,
        runtime_outbox=RuntimeOutbox(
            publisher=publisher,
            factory=OutboxMessage,
            publish_limit=100,
        ),
        execution_registry=LocalGraphExecutionRegistry(),
        lease_ttl_ms=1_000,
    )
    worker = GraphCancellationContinuationWorker(
        task_store=store,
        command_repository=HostCommandRepository(db_path, clock=_Clock(NOW)),
        cancellation_runtime=runtime,
        worker_id="cancellation-worker-startup-scan",
        claim_lease_ms=1_000,
        retry_delay_ms=100,
        batch_limit=1,
    )

    assert await worker.run_once() == 1

    cancellation = store.get_graph_cancellation_for_graph(
        "graph-cross-process-cancel",
        1,
    )
    assert cancellation is not None
    assert cancellation["intent_ref"] == intent_ref
    assert cancellation["status"] == "FINALIZED"
    decision = store.get_graph_cancellation_decision_for_intent(intent_ref)
    assert decision is not None
    assert decision["intent_ref"] == intent_ref
    assert store.get_graph_cancellation_continuation(intent_ref) is None
    assert store.get_graph("graph-cross-process-cancel", 1)["status"] == "CANCELLED"
    assert store.get_node("graph-cross-process-cancel", 1, "node-1")["status"] == (
        "CANCELLED"
    )
    assert publisher.calls >= 2


@pytest.mark.asyncio
async def test_restarted_worker_finalizes_signed_interruption_without_replay(
    tmp_path: Path,
) -> None:
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    proof_verifier = Ed25519ReconciliationProofVerifier(
        {"reconciler-cross-process-v1": public_key}
    )
    db_path, intent_ref = _seed_requested_intent(
        tmp_path,
        reconciliation_private_key=private_key,
    )
    store = DurableTaskStore(
        db_path,
        clock=_Clock(NOW),
        reconciliation_proof_verifier=proof_verifier,
    )
    publisher = _NoopOutboxPublisher()
    runtime = GraphCancellationRuntime(
        task_store=store,
        runtime_outbox=RuntimeOutbox(
            publisher=publisher,
            factory=OutboxMessage,
            publish_limit=100,
        ),
        execution_registry=LocalGraphExecutionRegistry(),
        lease_ttl_ms=1_000,
    )
    worker = GraphCancellationContinuationWorker(
        task_store=store,
        command_repository=HostCommandRepository(db_path, clock=_Clock(NOW)),
        cancellation_runtime=runtime,
        worker_id="cancellation-worker-interruption-restart",
        claim_lease_ms=1_000,
        retry_delay_ms=100,
        batch_limit=1,
    )

    assert await worker.run_once() == 1
    assert await worker.run_once() == 0

    operation = store.get_operation_reservation(
        "reservation-cross-process-interrupted"
    )
    assert operation["status"] == "RELEASED"
    assert operation["reconciliation_action"] == "RELEASE"
    assert operation["reconciliation_outcome"] == "NO_SIDE_EFFECT"
    assert store.get_graph(
        "graph-cross-process-cancel",
        1,
    )["status"] == "CANCELLED"
    assert store.get_node(
        "graph-cross-process-cancel",
        1,
        "node-1",
    )["status"] == "CANCELLED"
    assert store.get_attempt(
        "attempt-cross-process-interrupted"
    )["status"] == "CANCELLED"
    assert store.get_graph_cancellation_continuation(intent_ref) is None

    restarted_store = DurableTaskStore(
        db_path,
        clock=_Clock(NOW),
        reconciliation_proof_verifier=proof_verifier,
    )
    restarted_runtime = GraphCancellationRuntime(
        task_store=restarted_store,
        runtime_outbox=RuntimeOutbox(
            publisher=_NoopOutboxPublisher(),
            factory=OutboxMessage,
            publish_limit=100,
        ),
        execution_registry=LocalGraphExecutionRegistry(),
        lease_ttl_ms=1_000,
    )
    restarted_worker = GraphCancellationContinuationWorker(
        task_store=restarted_store,
        command_repository=HostCommandRepository(db_path, clock=_Clock(NOW)),
        cancellation_runtime=restarted_runtime,
        worker_id="cancellation-worker-interruption-second-restart",
        claim_lease_ms=1_000,
        retry_delay_ms=100,
        batch_limit=1,
    )
    assert await restarted_worker.run_once() == 0

    with sqlite3.connect(db_path) as conn:
        recovery_count = conn.execute(
            """
            SELECT COUNT(*) FROM task_runtime_outbox
            WHERE aggregate_type = 'OPERATION_RESERVATION'
              AND aggregate_id = 'reservation-cross-process-interrupted'
              AND event_type = 'OPERATION_RECOVERY_REQUIRED'
            """
        ).fetchone()
        release_count = conn.execute(
            """
            SELECT COUNT(*) FROM task_runtime_outbox
            WHERE aggregate_type = 'OPERATION_RESERVATION'
              AND aggregate_id = 'reservation-cross-process-interrupted'
              AND event_type = 'OPERATION_RECOVERY_RELEASE'
            """
        ).fetchone()
        cancelled_count = conn.execute(
            """
            SELECT COUNT(*) FROM task_runtime_outbox
            WHERE aggregate_type = 'TASK_GRAPH'
              AND aggregate_id = 'graph-cross-process-cancel'
              AND event_type = 'TASK_GRAPH_CANCELLED'
            """
        ).fetchone()
    assert recovery_count == (1,)
    assert release_count == (1,)
    assert cancelled_count == (1,)


@pytest.mark.asyncio
async def test_worker_defers_while_shared_registry_has_nonquiescent_local_executor(
    tmp_path: Path,
) -> None:
    db_path, intent_ref = _seed_requested_intent(tmp_path)
    store = DurableTaskStore(db_path, clock=_Clock(NOW))
    registry = LocalGraphExecutionRegistry()
    publisher = _NoopOutboxPublisher()
    runtime = GraphCancellationRuntime(
        task_store=store,
        runtime_outbox=RuntimeOutbox(
            publisher=publisher,
            factory=OutboxMessage,
            publish_limit=100,
        ),
        execution_registry=registry,
        lease_ttl_ms=100,
    )
    worker = GraphCancellationContinuationWorker(
        task_store=store,
        command_repository=HostCommandRepository(db_path, clock=_Clock(NOW)),
        cancellation_runtime=runtime,
        worker_id="cancellation-worker-shared-registry",
        claim_lease_ms=1_000,
        retry_delay_ms=100,
        batch_limit=1,
    )
    entered = asyncio.Event()
    cancellation_seen = asyncio.Event()
    release = asyncio.Event()

    async def stubborn_local_executor() -> None:
        async with registry.track(
            graph_id="graph-cross-process-cancel",
            graph_revision=1,
        ):
            entered.set()
            while not release.is_set():
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    cancellation_seen.set()

    local_task = asyncio.create_task(
        stubborn_local_executor(),
        name="nonquiescent-local-graph-executor",
    )
    await entered.wait()
    try:
        assert await worker.run_once() == 1

        assert cancellation_seen.is_set()
        assert not local_task.done()
        cancellation = store.get_graph_cancellation_for_graph(
            "graph-cross-process-cancel",
            1,
        )
        assert cancellation is not None
        assert cancellation["status"] == "REQUESTED"
        assert store.get_graph("graph-cross-process-cancel", 1)["status"] == (
            "BLOCKED"
        )
        assert store.get_graph_cancellation_decision_for_intent(intent_ref) is None
        continuation = store.get_graph_cancellation_continuation(intent_ref)
        assert continuation is not None
        assert continuation["claim_owner"] is None
        assert continuation["last_error"] == (
            "GRAPH_CANCELLATION_RECONCILIATION_PENDING"
        )
    finally:
        release.set()
        await local_task
