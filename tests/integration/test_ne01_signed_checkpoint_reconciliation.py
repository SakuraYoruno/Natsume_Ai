from __future__ import annotations

import base64
import sqlite3
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from host_brain.app.accepted_operation_reconciliation_handler import (
    AcceptedOperationReconciliationHandler,
)
from host_brain.contracts.agent_runtime.operation_reconciliation import (
    build_reconciliation_attestation_binding,
)
from host_brain.orchestration import GovernedCommandApplication
from host_brain.persistence.host_command_repository import HostCommandRepository
from host_brain.persistence.task_repository import (
    DurableTaskStore,
    Ed25519ReconciliationProofVerifier,
    OutboxMessage,
    TaskStoreError,
)
from host_brain.persistence.worker_projection_repository import (
    WorkerProjectionStore,
)
from host_brain.task_runtime import AdmittedExecution
from host_brain.task_runtime.operation_checkpoint_reconciliation import (
    OperationCheckpointReconciliationError,
    OperationCheckpointReconciliationService,
)
from host_brain.task_runtime.operation_reacquisition import (
    OperationReconciliationCommandService,
)
from tests.integration.test_gr03a_governed_durable_dag import (
    _FailingHeartbeatStore,
)
from tests.integration.test_ne01_operation_online_takeover import (
    _Clock,
    _ProjectedWorker,
    _contracts,
    _coordinator,
    _events,
    _migrated_db,
)


class _CheckpointResponseLossStore:
    def __init__(self, delegate: DurableTaskStore) -> None:
        self._delegate = delegate
        self.response_lost = False

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)

    def reconcile_operation_checkpoint(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        result = self._delegate.reconcile_operation_checkpoint(
            *args,
            **kwargs,
        )
        if not self.response_lost:
            self.response_lost = True
            raise TimeoutError("simulated checkpoint COMMIT response loss")
        return result


class _TargetContinuation:
    def __init__(
        self,
        application: GovernedCommandApplication,
        contracts: tuple[Any, Any, Any, Any],
    ) -> None:
        self._application = application
        self._contracts = contracts
        self.calls = 0

    async def continue_operation_reconciliation(
        self,
        _reconciliation: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        self.calls += 1
        events = await _events(self._application, self._contracts)
        return dict(events[-1])


def _signature(
    private_key: Ed25519PrivateKey,
    *,
    operation: dict[str, Any],
    expected_version: int,
    actor_id: str,
    attestation_ref: str,
    receipt_ref: str,
) -> str:
    binding = build_reconciliation_attestation_binding(
        reservation_id=str(operation["reservation_id"]),
        business_scope=str(operation["business_scope"]),
        operation_fingerprint=str(operation["operation_fingerprint"]),
        recovery_version=expected_version,
        reconciler_actor_id=actor_id,
        attestation_ref=attestation_ref,
        recovery_action="COMMIT",
        reconciliation_outcome="SIDE_EFFECT_CONFIRMED",
        receipt_ref=receipt_ref,
    )
    return "ed25519:" + base64.urlsafe_b64encode(
        private_key.sign(binding)
    ).decode("ascii")


@pytest.mark.asyncio
async def test_signed_checkpoint_commit_resumes_without_worker_reexecution(
    tmp_path: Path,
) -> None:
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    key_id = "checkpoint-reconciliation-integration-v1"
    verifier = Ed25519ReconciliationProofVerifier({key_id: public_key})
    db_path = _migrated_db(tmp_path)
    contracts = _contracts((("root", ()),))
    admitted = AdmittedExecution.from_contracts(
        command=contracts[0],
        assignment=contracts[2],
        graph=contracts[3],
    )
    clock = _Clock()
    projections = WorkerProjectionStore(db_path, clock=clock)
    calls: list[str] = []
    worker = _ProjectedWorker(
        projections=projections,
        calls=calls,
        delay_seconds=0.08,
    )
    durable = DurableTaskStore(
        db_path,
        clock=clock,
        reconciliation_proof_verifier=verifier,
    )
    interrupted_application = GovernedCommandApplication(
        command_repository=HostCommandRepository(db_path, clock=clock),
        coordinator=_coordinator(
            store=_FailingHeartbeatStore(durable),
            projections=projections,
            worker=worker,
            owner_id="checkpoint-owner-a",
            lease_ttl_ms=30,
        ),
    )

    interrupted = await _events(interrupted_application, contracts)

    assert interrupted[-1]["execution_receipt"]["status"] == "RECOVERY_REQUIRED"
    assert calls == ["root"]
    operation = dict(
        durable.get_operation_reservation(admitted.reservation_id)
    )
    assert operation["status"] == "RECOVERY_REQUIRED"
    attempt = durable.get_attempt(str(operation["attempt_id"]))
    with durable._connect() as conn:
        projection_rows = conn.execute(
            """
            SELECT projection_id FROM task_runtime_worker_projection
            WHERE attempt_id = ?
            """,
            (attempt["attempt_id"],),
        ).fetchall()
    assert len(projection_rows) == 1
    projection_ref = str(projection_rows[0]["projection_id"])
    projection = projections.get(projection_ref)
    assert projection["attempt_id"] == attempt["attempt_id"]

    actor_id = "reconciler://operator/checkpoint-integration"
    attestation_ref = "proof://operation/checkpoint-integration"
    expected_version = int(operation["status_version"])
    signature = _signature(
        private_key,
        operation=operation,
        expected_version=expected_version,
        actor_id=actor_id,
        attestation_ref=attestation_ref,
        receipt_ref=projection_ref,
    )
    request = {
        "reservation_id": admitted.reservation_id,
        "expected_version": expected_version,
        "action": "COMMIT",
        "reconciler_actor_id": actor_id,
        "downstream_receipt_ref": projection_ref,
        "attestation_ref": attestation_ref,
        "attestation_key_id": key_id,
        "attestation_signature": signature,
    }
    forged = {
        **request,
        "attestation_signature": _signature(
            Ed25519PrivateKey.generate(),
            operation=operation,
            expected_version=expected_version,
            actor_id=actor_id,
            attestation_ref=attestation_ref,
            receipt_ref=projection_ref,
        ),
    }
    with pytest.raises(TaskStoreError):
        durable.stage_operation_reconciliation_evidence(
            request_id="request-checkpoint-reconciliation-forged",
            ingress_audit_ref="secure-ingress-audit://operation/checkpoint-forged",
            body_sha256="8" * 64,
            intent_ref=None,
            **forged,
        )
    evidence = durable.stage_operation_reconciliation_evidence(
        request_id="request-checkpoint-reconciliation",
        ingress_audit_ref="secure-ingress-audit://operation/checkpoint",
        body_sha256="9" * 64,
        intent_ref=None,
        **request,
    )
    assert evidence["intent_ref"] is None
    assert evidence["downstream_receipt_ref"] == projection_ref
    with durable._connect() as conn:
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute(
                """
                UPDATE task_runtime_worker_projection
                SET decision = 'REJECT'
                WHERE projection_id = ?
                """,
                (projection_ref,),
            )
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute(
                """
                DELETE FROM task_runtime_worker_projection
                WHERE projection_id = ?
                """,
                (projection_ref,),
            )

    service_before_expiry = OperationCheckpointReconciliationService(
        task_store=durable,
        command_repository=HostCommandRepository(db_path, clock=clock),
        projection_reader=projections,
        outbox_factory=OutboxMessage,
        clock_ms=clock,
    )
    with pytest.raises(OperationCheckpointReconciliationError) as live:
        service_before_expiry.reconcile_operation_checkpoint(**request)
    assert live.value.side_effects_may_have_run is False
    assert durable.get_operation_reservation(
        admitted.reservation_id
    )["status"] == "RECOVERY_REQUIRED"

    clock.advance(31)
    response_loss_store = _CheckpointResponseLossStore(durable)
    service = OperationCheckpointReconciliationService(
        task_store=response_loss_store,
        command_repository=HostCommandRepository(db_path, clock=clock),
        projection_reader=WorkerProjectionStore(db_path, clock=clock),
        outbox_factory=OutboxMessage,
        clock_ms=clock,
    )
    resumed_application = GovernedCommandApplication(
        command_repository=HostCommandRepository(db_path, clock=clock),
        coordinator=_coordinator(
            store=durable,
            projections=WorkerProjectionStore(db_path, clock=clock),
            worker=worker,
            owner_id="checkpoint-owner-b",
            lease_ttl_ms=30,
        ),
    )
    target_continuation = _TargetContinuation(
        resumed_application,
        contracts,
    )
    command_service = OperationReconciliationCommandService(
        cancellation_service=object(),  # unreachable for no-intent COMMIT
        reacquisition_service=object(),  # unreachable for no-intent COMMIT
        checkpoint_service=service,
        failure_service=object(),  # unreachable for no-intent COMMIT
    )
    handler = AcceptedOperationReconciliationHandler(
        service=command_service,
        evidence_store=durable,
        continuation_provider=lambda: target_continuation,
    )
    handler_result = await handler(
        {
            "operator_command_id": "task_operation.reconcile",
            "request_id": evidence["request_id"],
            "evidence_ref": evidence["evidence_ref"],
            "evidence_digest": evidence["evidence_digest"],
            "reservation_id": request["reservation_id"],
            "expected_version": request["expected_version"],
            "action": "COMMIT",
            "reconciler_actor_id": actor_id,
        }
    )

    assert handler_result["status"] == "COMMITTED"
    assert handler_result["operation_status"] == "COMMITTED"
    assert handler_result["exact_replay"] is True
    assert handler_result["target_continuation"]["execution_receipt"][
        "status"
    ] == "ACCEPTED"
    assert target_continuation.calls == 1
    assert response_loss_store.response_lost is True
    assert calls == ["root"]
    snapshot = durable.read_scheduler_snapshot(
        admitted.graph_id,
        admitted.graph_revision,
    )
    assert len(snapshot["attempts"]) == 1
    assert snapshot["attempts"][0]["attempt_id"] == attempt["attempt_id"]
    assert snapshot["attempts"][0]["status"] == "SUCCEEDED"
    assert snapshot["graph_record"]["status"] == "COMPLETED"
    final_operation = durable.get_operation_reservation(
        admitted.reservation_id
    )
    assert final_operation["reconciliation_action"] == "COMMIT"
    assert (
        final_operation["reconciliation_outcome"]
        == "SIDE_EFFECT_CONFIRMED"
    )
    assert final_operation["receipt_ref"] == projection_ref
    with durable._connect() as conn:
        outbox_count = conn.execute(
            """
            SELECT COUNT(*) FROM task_runtime_outbox
            WHERE event_type = 'OPERATION_RECOVERY_COMMIT'
              AND aggregate_id = ?
            """,
            (admitted.reservation_id,),
        ).fetchone()[0]
    assert outbox_count == 1

    exact_replay = service.reconcile_operation_checkpoint(**request)
    terminal_replay = await _events(resumed_application, contracts)

    assert exact_replay["replayed"] is True
    assert terminal_replay[-1]["execution_receipt"]["accepted"] is True
    assert calls == ["root"]
