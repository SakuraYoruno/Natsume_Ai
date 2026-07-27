from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

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
from host_brain.persistence.worker_projection_repository import WorkerProjectionStore
from host_brain.task_runtime import AdmittedExecution
from host_brain.task_runtime.operation_reacquisition import (
    OperationReacquisitionError,
    OperationReacquisitionService,
)
from tests.integration.test_ne01_operation_online_takeover import (
    _Clock,
    _ProjectedWorker,
    _contracts,
    _coordinator,
    _events,
    _migrated_db,
)


class _FailOnceWorker:
    def __init__(self, delegate: _ProjectedWorker, calls: list[str]) -> None:
        self._delegate = delegate
        self._calls = calls
        self.invocations = 0

    async def execute_stream(
        self,
        *,
        command: Any,
        execution_context: dict[str, Any],
        **kwargs: Any,
    ):
        self.invocations += 1
        if self.invocations == 1:
            self._calls.append(str(execution_context["node_id"]))
            raise RuntimeError("simulated post-reservation worker interruption")
        async for event in self._delegate.execute_stream(
            command=command,
            execution_context=execution_context,
            **kwargs,
        ):
            yield event


class _RecoverResponseLossStore:
    def __init__(self, delegate: DurableTaskStore) -> None:
        self._delegate = delegate
        self.response_lost = False

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)

    def recover_operation_reservation(self, *args: Any, **kwargs: Any):
        result = self._delegate.recover_operation_reservation(*args, **kwargs)
        if not self.response_lost:
            self.response_lost = True
            raise TimeoutError("simulated REACQUIRE response loss")
        return result


def _signature(
    private_key: Ed25519PrivateKey,
    *,
    operation: dict[str, Any],
    expected_version: int,
    actor_id: str,
    attestation_ref: str,
) -> str:
    binding = build_reconciliation_attestation_binding(
        reservation_id=str(operation["reservation_id"]),
        business_scope=str(operation["business_scope"]),
        operation_fingerprint=str(operation["operation_fingerprint"]),
        recovery_version=expected_version,
        reconciler_actor_id=actor_id,
        attestation_ref=attestation_ref,
        recovery_action="REACQUIRE",
        reconciliation_outcome="NO_SIDE_EFFECT",
    )
    return "ed25519:" + base64.urlsafe_b64encode(
        private_key.sign(binding)
    ).decode("ascii")


@pytest.mark.asyncio
async def test_signed_reacquire_restarts_same_attempt_once_under_higher_fence(
    tmp_path: Path,
) -> None:
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    verifier = Ed25519ReconciliationProofVerifier(
        {"reconciler-reacquire-v1": public_key}
    )
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
    successful_worker = _ProjectedWorker(projections=projections, calls=calls)
    worker = _FailOnceWorker(successful_worker, calls)
    first_store = DurableTaskStore(
        db_path,
        clock=clock,
        reconciliation_proof_verifier=verifier,
    )
    first_app = GovernedCommandApplication(
        command_repository=HostCommandRepository(db_path, clock=clock),
        coordinator=_coordinator(
            store=first_store,
            projections=projections,
            worker=worker,
            owner_id="reacquire-owner-a",
            lease_ttl_ms=20,
        ),
    )

    interrupted = await _events(first_app, contracts)

    assert interrupted[-1]["execution_receipt"]["status"] == "RECOVERY_REQUIRED"
    assert worker.invocations == 1
    assert calls == ["root"]
    operation_before = dict(
        first_store.get_operation_reservation(admitted.reservation_id)
    )
    snapshot_before = first_store.read_scheduler_snapshot(
        admitted.graph_id,
        admitted.graph_revision,
    )
    attempt_before = dict(snapshot_before["attempts"][0])
    lease_before = dict(snapshot_before["active_leases"][0])
    assert operation_before["status"] == "RECOVERY_REQUIRED"
    assert operation_before["attempt_id"] == attempt_before["attempt_id"]
    assert operation_before["lease_id"] == lease_before["lease_id"]
    recovery_holds = first_store.list_current_operation_recovery_holds(
        graph_id=admitted.graph_id
    )
    assert len(recovery_holds) == 1
    recovery_case = recovery_holds[0]
    evidence_case = {
        "recovery_case_ref": recovery_case["case_ref"],
        "recovery_case_subject_sha256": recovery_case["subject_sha256"],
    }

    actor_id = "reconciler://operator/reacquire-test"
    attestation_ref = "proof://operation-reacquire/integration"
    signature = _signature(
        private_key,
        operation=operation_before,
        expected_version=operation_before["status_version"],
        actor_id=actor_id,
        attestation_ref=attestation_ref,
    )
    reopened_store = DurableTaskStore(
        db_path,
        clock=clock,
        reconciliation_proof_verifier=verifier,
    )
    response_loss_store = _RecoverResponseLossStore(reopened_store)
    service = OperationReacquisitionService(
        task_store=response_loss_store,
        outbox_factory=OutboxMessage,
        proof_verifier=verifier,
        lease_ttl_ms=20,
    )
    service.bind_execution_owner("reacquire-owner-b")
    request = {
        "reservation_id": admitted.reservation_id,
        "expected_version": operation_before["status_version"],
        "reconciler_actor_id": actor_id,
        "attestation_ref": attestation_ref,
        "attestation_key_id": "reconciler-reacquire-v1",
        "attestation_signature": signature,
    }
    evidence = reopened_store.stage_operation_reconciliation_evidence(
        request_id="request-signed-operation-reacquisition",
        ingress_audit_ref="secure-ingress-audit://operation/reacquisition",
        body_sha256="9" * 64,
        intent_ref=None,
        action="REACQUIRE",
        downstream_receipt_ref=None,
        **evidence_case,
        **request,
    )
    evidence_replay = reopened_store.stage_operation_reconciliation_evidence(
        request_id="request-signed-operation-reacquisition",
        ingress_audit_ref="secure-ingress-audit://operation/reacquisition",
        body_sha256="9" * 64,
        intent_ref=None,
        action="REACQUIRE",
        downstream_receipt_ref=None,
        **evidence_case,
        **request,
    )

    assert evidence["exact_replay"] is False
    assert evidence_replay["exact_replay"] is True
    assert evidence_replay["evidence_ref"] == evidence["evidence_ref"]
    assert reopened_store.get_operation_reconciliation_evidence(
        evidence["evidence_ref"]
    )["attestation_signature"] == signature
    assert reopened_store.get_operation_reservation(
        admitted.reservation_id
    ) == operation_before
    with pytest.raises(TaskStoreError):
        reopened_store.stage_operation_reconciliation_evidence(
            request_id="request-signed-operation-reacquisition",
            ingress_audit_ref="secure-ingress-audit://operation/reacquisition",
            body_sha256="8" * 64,
            intent_ref=None,
            action="REACQUIRE",
            downstream_receipt_ref=None,
            **evidence_case,
            **request,
        )
    with reopened_store._connect() as conn, pytest.raises(Exception):
        conn.execute(
            """
            UPDATE task_runtime_operation_reconciliation_evidence
            SET body_sha256 = ?
            WHERE evidence_ref = ?
            """,
            ("7" * 64, evidence["evidence_ref"]),
        )

    forged_key = Ed25519PrivateKey.generate()
    forged_request = {
        **request,
        "attestation_signature": _signature(
            forged_key,
            operation=operation_before,
            expected_version=operation_before["status_version"],
            actor_id=actor_id,
            attestation_ref=attestation_ref,
        ),
    }
    with pytest.raises(OperationReacquisitionError) as forged:
        service.reconcile_operation_reacquisition(**forged_request)
    assert forged.value.side_effects_may_have_run is False
    assert reopened_store.get_attempt(attempt_before["attempt_id"]) == attempt_before
    assert reopened_store.get_active_lease(attempt_before["attempt_id"]) == lease_before

    with pytest.raises(OperationReacquisitionError) as live:
        service.reconcile_operation_reacquisition(**request)
    assert live.value.side_effects_may_have_run is False
    assert reopened_store.get_attempt(attempt_before["attempt_id"]) == attempt_before
    assert reopened_store.get_active_lease(attempt_before["attempt_id"]) == lease_before

    clock.advance(21)
    reacquired = service.reconcile_operation_reacquisition(**request)

    assert reacquired["status"] == "RECONCILED"
    assert reacquired["replayed"] is True
    assert response_loss_store.response_lost is True
    assert reacquired["attempt_id"] == attempt_before["attempt_id"]
    assert reacquired["operation"]["status"] == "RESERVED"
    assert reacquired["operation"]["owner_id"] == "reacquire-owner-b"
    assert (
        reacquired["operation"]["fencing_token"]
        == lease_before["fencing_token"] + 1
    )

    resumed_store = DurableTaskStore(
        db_path,
        clock=clock,
        reconciliation_proof_verifier=verifier,
    )
    resumed_app = GovernedCommandApplication(
        command_repository=HostCommandRepository(db_path, clock=clock),
        coordinator=_coordinator(
            store=resumed_store,
            projections=WorkerProjectionStore(db_path, clock=clock),
            worker=worker,
            owner_id="reacquire-owner-b",
            lease_ttl_ms=20,
        ),
    )
    resumed = await _events(resumed_app, contracts)

    assert resumed[-1]["execution_receipt"]["status"] == "ACCEPTED"
    assert resumed[-1]["execution_receipt"]["accepted"] is True
    assert worker.invocations == 2
    assert calls == ["root", "root"]
    final = resumed_store.read_scheduler_snapshot(
        admitted.graph_id,
        admitted.graph_revision,
    )
    assert len(final["attempts"]) == 1
    assert final["attempts"][0]["attempt_id"] == attempt_before["attempt_id"]
    assert final["attempts"][0]["status"] == "SUCCEEDED"
    assert final["graph_record"]["status"] == "COMPLETED"
    final_operation = resumed_store.get_operation_reservation(
        admitted.reservation_id
    )
    assert final_operation["status"] == "COMMITTED"
    assert final_operation["reconciliation_action"] == "REACQUIRE"
    assert final_operation["reconciliation_outcome"] == "NO_SIDE_EFFECT"
    precharges = [
        row for row in final["budget_usage"] if row["charge_kind"] == "PRECHARGE"
    ]
    assert len(precharges) == 2
    assert len({row["usage_id"] for row in precharges}) == 2
    assert all(row["attempt_id"] == attempt_before["attempt_id"] for row in precharges)
    with resumed_store._connect() as conn:
        leases = conn.execute(
            """
            SELECT owner_id, fencing_token, status
            FROM task_runtime_lease
            WHERE attempt_id = ?
            ORDER BY fencing_token
            """,
            (attempt_before["attempt_id"],),
        ).fetchall()
        reacquire_outboxes = conn.execute(
            """
            SELECT COUNT(*) FROM task_runtime_outbox
            WHERE event_type = 'OPERATION_RECOVERY_REACQUIRE'
            """
        ).fetchone()[0]
    assert [tuple(row) for row in leases] == [
        ("reacquire-owner-a", lease_before["fencing_token"], "EXPIRED"),
        ("reacquire-owner-b", lease_before["fencing_token"] + 1, "RELEASED"),
    ]
    assert reacquire_outboxes == 1

    replayed = service.reconcile_operation_reacquisition(**request)
    terminal_replay = await _events(resumed_app, contracts)

    assert replayed["replayed"] is True
    assert terminal_replay[-1]["execution_receipt"]["accepted"] is True
    assert worker.invocations == 2
    assert calls == ["root", "root"]
