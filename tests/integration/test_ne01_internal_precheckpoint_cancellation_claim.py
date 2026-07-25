from __future__ import annotations

import base64
import hashlib
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from host_brain.agent_runtime.runtime_router import CanonicalRuntimeRouter
from host_brain.app.internal_task_contracts import build_internal_task_contracts
from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.contracts.agent_runtime.budget_policy import (
    BudgetAmount,
    BudgetReservation,
    BudgetReservationStatus,
)
from host_brain.contracts.agent_runtime.graph_cancellation import (
    GRAPH_CANCELLATION_DECISION_SCHEMA_VERSION,
    GRAPH_CANCELLATION_INTENT_SCHEMA_VERSION,
    normalize_graph_cancellation_decision,
    normalize_graph_cancellation_intent,
)
from host_brain.contracts.agent_runtime.internal_precheckpoint_cancellation_claim_result import (
    INTERNAL_PRECHECKPOINT_CANCELLATION_RESULT_REF_PREFIX,
    build_internal_precheckpoint_cancellation_result_ref,
)
from host_brain.contracts.agent_runtime.task_graph import (
    TaskGraphStatus,
    TaskNodeStatus,
)
from host_brain.contracts.agent_runtime.work_order import AgentWorkOrder
from host_brain.persistence.host_command_repository import HostCommandRepository
from host_brain.persistence.host_command_repository_types import (
    HOST_COMMAND_INGRESS_CONFLICT,
    HostCommandRepositoryError,
)
from host_brain.persistence.task_repository import (
    AttemptStatus,
    DurableTaskStore,
    Ed25519ReconciliationProofVerifier,
    OutboxMessage,
    TaskStoreError,
    build_outbox_payload_ref,
    build_reconciliation_attestation_binding,
)
from host_brain.persistence.secure_ingress_repository import SQLiteSecureIngressStore


NOW = 2_010_000_000_000
KEY_ID = "reconciler-internal-cancel-v1"


@dataclass
class _Clock:
    value: int = NOW

    def __call__(self) -> int:
        return self.value


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _event(
    aggregate_type: str,
    aggregate_id: str,
    version: int,
    event_type: str,
    *,
    outbox_id: str,
    evidence_refs: tuple[str, ...] = (),
    attempt_id: str | None = None,
) -> OutboxMessage:
    graph_revision = 1 if aggregate_type == "TASK_GRAPH" else None
    return OutboxMessage.create(
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        graph_revision=graph_revision,
        aggregate_version=version,
        event_type=event_type,
        payload_ref=build_outbox_payload_ref(
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            aggregate_version=version,
            graph_revision=graph_revision,
        ),
        idempotency_key=f"idempotency:{outbox_id}",
        outbox_id=outbox_id,
        evidence_refs=evidence_refs,
        attempt_id=attempt_id,
    )


def _insert_secure_authority(
    db_path: Path,
    *,
    request_id: str,
    principal_id: str,
    body_sha256: str,
    claim_ref: str,
    audit_ref: str,
) -> None:
    with sqlite3.connect(db_path) as conn:
        idempotency_digest = f"sha256:{_sha256('idempotency:' + request_id)}"
        conn.execute(
            """
            INSERT INTO secure_ingress_idempotency_claim(
                principal_id, surface, idempotency_digest, action,
                required_scopes_json, request_id, body_sha256, claim_ref,
                result_ref, claim_status, provisional_expires_at_ms,
                claimed_at_ms, updated_at_ms, expires_at_ms
            ) VALUES (?, 'internal.agent', ?, 'internal:agent:execute',
                      '["internal:agent:execute"]', ?, ?, ?, NULL,
                      'COMMITTED', NULL, ?, ?, ?)
            """,
            (
                principal_id,
                idempotency_digest,
                request_id,
                body_sha256,
                claim_ref,
                NOW,
                NOW,
                NOW + 60_000,
            ),
        )
        conn.execute(
            """
            INSERT INTO secure_ingress_audit(
                audit_ref, request_id, correlation_id, surface, action, method,
                normalized_path, principal_id, tenant_id, key_id,
                credential_kind, credential_ref_digest, requested_scopes_json,
                required_scopes_json, body_sha256, idempotency_digest,
                nonce_digest, transport_key_digest, decision, reason_code,
                stage, claim_ref, rate_snapshot_json, side_effects_executed, ts_ms
            ) VALUES (?, ?, NULL, 'internal.agent', 'internal:agent:execute',
                      'POST', '/internal/codex/work-orders/execute', ?,
                      'tenant-internal', 'key-internal', 'HMAC_SHA256', ?,
                      '["internal:agent:execute"]',
                      '["internal:agent:execute"]', ?, ?, ?, ?, 'ALLOW',
                      'SECURE_INGRESS_ALLOWED', 'COMPLETE', ?, '{}', 0, ?)
            """,
            (
                audit_ref,
                request_id,
                principal_id,
                f"sha256:{_sha256('credential:' + request_id)}",
                body_sha256,
                idempotency_digest,
                f"sha256:{_sha256('nonce:' + request_id)}",
                f"sha256:{_sha256('transport:' + request_id)}",
                claim_ref,
                NOW,
            ),
        )


def _cancelled_runtime(
    tmp_path: Path,
) -> tuple[
    Path,
    DurableTaskStore,
    HostCommandRepository,
    dict[str, Any],
    Ed25519PrivateKey,
    bytes,
]:
    db_path = tmp_path / "internal-precheckpoint-cancel.sqlite3"
    migrated = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="ne01-internal-precheckpoint-cancel",
        ts_ms=NOW,
    )
    assert migrated.success, migrated.as_dict()
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    verifier = Ed25519ReconciliationProofVerifier({KEY_ID: public_key})
    clock = _Clock()
    task_store = DurableTaskStore(
        db_path,
        clock=clock,
        reconciliation_proof_verifier=verifier,
    )
    request_id = "request-internal-precheckpoint-cancel"
    principal_id = "principal-internal-precheckpoint-cancel"
    body_sha256 = f"sha256:{_sha256('internal-precheckpoint-cancel')}"
    claim_ref = "ingress-claim://internal/precheckpoint-cancel"
    audit_ref = "audit://secure-ingress/internal/precheckpoint-cancel"
    _insert_secure_authority(
        db_path,
        request_id=request_id,
        principal_id=principal_id,
        body_sha256=body_sha256,
        claim_ref=claim_ref,
        audit_ref=audit_ref,
    )
    command, work_order, graph = build_internal_task_contracts(
        work_order=AgentWorkOrder(
            work_order_id="work-order-internal-precheckpoint-cancel",
            domain="code",
            run_mode="inspect",
            objective="cancel before the Agent checkpoint",
            acceptance_criteria=["no external effect"],
        ),
        request_id=request_id,
        session_id="session-internal-precheckpoint-cancel",
        ts_ms=NOW,
        governor_grant_ref="governor-grant://internal/precheckpoint-cancel",
        command_authority={
            "principal_id": principal_id,
            "claim_ref": claim_ref,
            "audit_ref": audit_ref,
            "body_sha256": body_sha256,
        },
        frozen_runtime_context={},
    )
    assignment = CanonicalRuntimeRouter(clock=clock).assign_new_task(
        work_order.work_order_id,
        assigned_at_ms=NOW,
    )
    command_repository = HostCommandRepository(
        db_path,
        clock=clock,
        task_store=task_store,
    )
    command_repository.admit(command, work_order, assignment, graph)
    graph_id = graph.graph_id
    node_id = graph.nodes[0].node_id
    attempt_id = "attempt-internal-precheckpoint-cancel"
    task_store.transition_graph(
        graph_id,
        1,
        expected_version=0,
        target_status=TaskGraphStatus.ACTIVE,
        outbox=_event(
            "TASK_GRAPH",
            graph_id,
            1,
            "TASK_GRAPH_ACTIVE",
            outbox_id="outbox-internal-cancel-graph-active",
        ),
    )
    budget_id = "budget-internal-precheckpoint-cancel"
    task_store.create_budget_reservation(
        BudgetReservation(
            budget_id=budget_id,
            scope=f"graph:{graph_id}@1",
            limits=BudgetAmount(token_count=100),
            reserved=BudgetAmount(token_count=100),
        ),
        graph_id=graph_id,
        graph_revision=1,
        node_id=None,
        attempt_id=None,
        outbox=_event(
            "BUDGET_RESERVATION",
            budget_id,
            0,
            "BUDGET_RESERVATION_CREATED",
            outbox_id="outbox-internal-cancel-budget-create",
        ),
    )
    task_store.transition_budget(
        budget_id,
        expected_version=0,
        target_status=BudgetReservationStatus.ACTIVE,
        outbox=_event(
            "BUDGET_RESERVATION",
            budget_id,
            1,
            "BUDGET_RESERVATION_ACTIVE",
            outbox_id="outbox-internal-cancel-budget-active",
        ),
    )
    task_store.create_attempt(
        attempt_id=attempt_id,
        graph_id=graph_id,
        graph_revision=1,
        node_id=node_id,
        attempt_no=1,
        expected_node_version=0,
        operation_scope=work_order.business_idempotency_scope,
        operation_fingerprint=work_order.operation_fingerprint,
        outbox=_event(
            "TASK_ATTEMPT",
            attempt_id,
            0,
            "TASK_ATTEMPT_CREATED",
            outbox_id="outbox-internal-cancel-attempt-create",
            attempt_id=attempt_id,
        ),
    )
    lease = task_store.acquire_lease(
        attempt_id,
        owner_id="worker-internal-cancel",
        ttl_ms=60_000,
        expected_attempt_version=0,
        outbox=_event(
            "TASK_ATTEMPT",
            attempt_id,
            1,
            "TASK_ATTEMPT_LEASED",
            outbox_id="outbox-internal-cancel-attempt-lease",
            attempt_id=attempt_id,
        ),
    )
    leased_node = task_store.get_node(graph_id, 1, node_id)
    task_store.transition_node(
        graph_id,
        1,
        node_id,
        expected_version=int(leased_node["status_version"]),
        target_status=TaskNodeStatus.RUNNING,
        outbox=_event(
            "TASK_NODE",
            f"{graph_id}@1/{node_id}",
            int(leased_node["status_version"]) + 1,
            "TASK_NODE_RUNNING",
            outbox_id="outbox-internal-cancel-node-running",
        ),
    )
    leased_attempt = task_store.get_attempt(attempt_id)
    task_store.transition_attempt(
        attempt_id,
        owner_id="worker-internal-cancel",
        fencing_token=int(lease["fencing_token"]),
        expected_version=int(leased_attempt["status_version"]),
        target_status=AttemptStatus.RUNNING,
        outbox=_event(
            "TASK_ATTEMPT",
            attempt_id,
            int(leased_attempt["status_version"]) + 1,
            "TASK_ATTEMPT_RUNNING",
            outbox_id="outbox-internal-cancel-attempt-running",
            attempt_id=attempt_id,
        ),
    )
    reservation_id = "reservation-internal-precheckpoint-cancel"
    task_store.reserve_operation(
        reservation_id=reservation_id,
        business_scope=work_order.business_idempotency_scope,
        operation_fingerprint=work_order.operation_fingerprint,
        attempt_id=attempt_id,
        lease_id=str(lease["lease_id"]),
        owner_id="worker-internal-cancel",
        fencing_token=int(lease["fencing_token"]),
        ttl_ms=60_000,
        outbox=_event(
            "OPERATION_RESERVATION",
            reservation_id,
            0,
            "OPERATION_RESERVED",
            outbox_id="outbox-internal-cancel-operation-reserve",
            attempt_id=attempt_id,
        ),
    )
    running_node = task_store.get_node(graph_id, 1, node_id)
    intent_payload = {
        "schema_version": GRAPH_CANCELLATION_INTENT_SCHEMA_VERSION,
        "request_id": "request-cancel-internal-precheckpoint",
        "request_identity": "principal://operator/internal-precheckpoint",
        "source": "OPERATOR",
        "reason_code": "OPERATOR_CANCELLED_TASK",
        "graph_id": graph_id,
        "graph_revision": 1,
        "graph_status_before": "ACTIVE",
        "graph_status_version_before": 1,
        "graph_target_status": "BLOCKED",
        "budget_id": budget_id,
        "budget_status_before": "ACTIVE",
        "budget_status_version_before": 1,
        "node_states_before": {
            node_id: {
                "status": running_node["status"],
                "status_version": running_node["status_version"],
            }
        },
        "node_states_after": {
            node_id: {
                "status": "CANCEL_REQUESTED",
                "status_version": int(running_node["status_version"]) + 1,
            }
        },
        "graph_outbox_id": "outbox-internal-cancel-request",
    }
    intent = normalize_graph_cancellation_intent(intent_payload)
    requested = task_store.request_graph_cancellation(
        graph_id,
        1,
        budget_id=budget_id,
        expected_graph_version=1,
        expected_budget_version=1,
        expected_node_states=intent_payload["node_states_before"],
        intent_ref=intent.intent_ref,
        intent_payload=intent_payload,
        graph_outbox=_event(
            "TASK_GRAPH",
            graph_id,
            2,
            "TASK_GRAPH_CANCELLATION_REQUESTED",
            outbox_id="outbox-internal-cancel-request",
            evidence_refs=(intent.intent_ref,),
        ),
    )
    assert requested["intent"]["status"] == "REQUESTED"

    # RECOVERY_REQUIRED is an unknown-effect boundary and must never be
    # reclassified as a safe pre-checkpoint terminal.
    with pytest.raises(TaskStoreError):
        task_store.resolve_internal_agent_precheckpoint_cancellation_terminal(
            attempt_id
        )

    reservation = task_store.get_operation_reservation(reservation_id)
    assert reservation["status"] == "RECOVERY_REQUIRED"
    proof_ref = "operation-proof://internal/precheckpoint/no-side-effect"
    actor_id = "reconciler://internal/precheckpoint"
    proof_binding = build_reconciliation_attestation_binding(
        reservation_id=reservation_id,
        business_scope=work_order.business_idempotency_scope,
        operation_fingerprint=work_order.operation_fingerprint,
        recovery_version=int(reservation["status_version"]),
        reconciler_actor_id=actor_id,
        attestation_ref=proof_ref,
        recovery_action="RELEASE",
        reconciliation_outcome="NO_SIDE_EFFECT",
    )
    proof_signature = "ed25519:" + base64.urlsafe_b64encode(
        private_key.sign(proof_binding)
    ).decode("ascii")
    task_store.recover_operation_reservation(
        reservation_id,
        action="RELEASE",
        attempt_id=attempt_id,
        expected_version=int(reservation["status_version"]),
        reconciler_actor_id=actor_id,
        attestation_ref=proof_ref,
        attestation_key_id=KEY_ID,
        attestation_signature=proof_signature,
        outbox=_event(
            "OPERATION_RESERVATION",
            reservation_id,
            int(reservation["status_version"]) + 1,
            "OPERATION_RECOVERY_RELEASE",
            outbox_id="outbox-internal-cancel-operation-release",
            attempt_id=attempt_id,
        ),
    )
    advanced = task_store.advance_graph_cancellation(intent.intent_ref)
    assert advanced["ready_to_finalize"] is True
    blocked_graph = task_store.get_graph(graph_id, 1)
    cancelled_node = task_store.get_node(graph_id, 1, node_id)
    budget = task_store.read_scheduler_snapshot(graph_id, 1)["budgets"][0]
    node_states_before = {
        node_id: {
            "status": cancelled_node["status"],
            "status_version": cancelled_node["status_version"],
        }
    }
    node_states_after = {
        node_id: {
            "status": "CANCELLED",
            "status_version": int(cancelled_node["status_version"]) + 1,
        }
    }
    decision_payload = {
        "schema_version": GRAPH_CANCELLATION_DECISION_SCHEMA_VERSION,
        "intent_ref": intent.intent_ref,
        "request_id": intent_payload["request_id"],
        "request_identity": intent_payload["request_identity"],
        "graph_id": graph_id,
        "graph_revision": 1,
        "graph_status_before": "BLOCKED",
        "graph_status_version_before": blocked_graph["status_version"],
        "graph_target_status": "CANCELLED",
        "budget_id": budget_id,
        "budget_status_before": budget["status"],
        "budget_status_version_before": budget["status_version"],
        "budget_target_status": "RELEASED",
        "node_states_before": node_states_before,
        "node_states_after": node_states_after,
        "reconciliation_evidence_refs": list(
            advanced["reconciliation_evidence_refs"]
        ),
        "graph_outbox_id": "outbox-internal-cancel-final",
        "budget_outbox_id": "outbox-internal-cancel-budget-release",
    }
    decision = normalize_graph_cancellation_decision(decision_payload)
    authority_evidence = (
        decision.decision_ref,
        intent.intent_ref,
        intent_payload["request_identity"],
    )
    task_store.finalize_graph_cancellation(
        graph_id,
        1,
        budget_id=budget_id,
        expected_graph_version=int(blocked_graph["status_version"]),
        expected_budget_version=int(budget["status_version"]),
        expected_node_states=node_states_before,
        intent_ref=intent.intent_ref,
        decision_ref=decision.decision_ref,
        decision_payload=decision_payload,
        graph_outbox=_event(
            "TASK_GRAPH",
            graph_id,
            int(blocked_graph["status_version"]) + 1,
            "TASK_GRAPH_CANCELLED",
            outbox_id="outbox-internal-cancel-final",
            evidence_refs=authority_evidence,
        ),
        budget_outbox=_event(
            "BUDGET_RESERVATION",
            budget_id,
            int(budget["status_version"]) + 1,
            "BUDGET_RESERVATION_RELEASED",
            outbox_id="outbox-internal-cancel-budget-release",
            evidence_refs=authority_evidence,
        ),
    )
    return (
        db_path,
        task_store,
        command_repository,
        {
            "command": command,
            "work_order": work_order,
            "attempt_id": attempt_id,
            "lease_id": str(lease["lease_id"]),
            "reservation_id": reservation_id,
            "claim_ref": claim_ref,
        },
        private_key,
        public_key,
    )


def test_no_effect_cancellation_closes_claim_restarts_and_rejects_tamper(
    tmp_path: Path,
) -> None:
    (
        db_path,
        task_store,
        command_repository,
        refs,
        _private_key,
        public_key,
    ) = _cancelled_runtime(tmp_path)
    terminal = (
        task_store.resolve_internal_agent_precheckpoint_cancellation_terminal(
            refs["attempt_id"]
        )
    )
    reservation = terminal["operation_reservation"]
    intent = terminal["cancellation_intent"]
    decision = terminal["cancellation_decision"]
    result_ref = build_internal_precheckpoint_cancellation_result_ref(
        command_id=refs["command"].command_id,
        ingress_claim_ref=refs["claim_ref"],
        graph_id=refs["command"].graph_id,
        graph_revision=refs["command"].graph_revision,
        node_id=refs["work_order"].task_node_id,
        attempt_id=refs["attempt_id"],
        lease_id=refs["lease_id"],
        reservation_id=refs["reservation_id"],
        reconciliation_proof_ref=reservation["reconciliation_proof_ref"],
        reconciliation_proof_digest=reservation["reconciliation_proof_digest"],
        cancellation_intent_ref=intent["intent_ref"],
        cancellation_decision_ref=decision["decision_ref"],
    )
    assert result_ref.startswith(
        INTERNAL_PRECHECKPOINT_CANCELLATION_RESULT_REF_PREFIX
    )
    claim_store = SQLiteSecureIngressStore(db_path)
    try:
        claim_store.complete_claim(
            claim_ref=refs["claim_ref"],
            result_ref=result_ref,
        )
        claim_store.complete_claim(
            claim_ref=refs["claim_ref"],
            result_ref=result_ref,
        )
    finally:
        claim_store.close()
    assert (
        command_repository.validate_ingress_claim_result(
            refs["command"].command_id
        )["command"].command_id
        == refs["command"].command_id
    )

    restarted_store = DurableTaskStore(
        db_path,
        clock=_Clock(),
        reconciliation_proof_verifier=Ed25519ReconciliationProofVerifier(
            {KEY_ID: public_key}
        ),
    )
    restarted_commands = HostCommandRepository(
        db_path,
        clock=_Clock(),
        task_store=restarted_store,
    )
    assert (
        restarted_commands.validate_ingress_claim_result(
            refs["command"].command_id
        )["command"].command_id
        == refs["command"].command_id
    )

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            UPDATE task_runtime_idempotency_reservation
            SET reconciliation_proof_signature = ?
            WHERE reservation_id = ?
            """,
            ("ed25519:" + ("A" * 88), refs["reservation_id"]),
        )
    with pytest.raises(TaskStoreError):
        restarted_store.resolve_internal_agent_precheckpoint_cancellation_terminal(
            refs["attempt_id"]
        )
    with pytest.raises(HostCommandRepositoryError) as rejected:
        restarted_commands.validate_ingress_claim_result(
            refs["command"].command_id
        )
    assert rejected.value.reason_code == HOST_COMMAND_INGRESS_CONFLICT
