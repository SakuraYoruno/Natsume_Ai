from __future__ import annotations

import asyncio
import json
import multiprocessing
import os
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from host_brain.app.accepted_operation_reconciliation_handler import (
    AcceptedOperationReconciliationHandler,
)
from host_brain.orchestration import GovernedCommandApplication
from host_brain.persistence.host_command_repository import HostCommandRepository
from host_brain.persistence.task_repository import (
    DurableTaskStore,
    Ed25519ReconciliationProofVerifier,
    OutboxMessage,
)
from host_brain.persistence.worker_projection_repository import (
    WorkerProjectionStore,
)
from host_brain.task_runtime import AdmittedExecution
from host_brain.task_runtime.operation_checkpoint_reconciliation import (
    OperationCheckpointReconciliationService,
)
from host_brain.task_runtime.operation_failure_reconciliation import (
    OperationFailureReconciliationService,
)
from host_brain.task_runtime.operation_reacquisition import (
    OperationReconciliationCommandService,
)
from tests.integration.test_gr03a_governed_durable_dag import (
    _FailingHeartbeatStore,
)
from tests.integration.test_ne01_host_signed_unknown_effect_process_restart import (
    _RECONCILIATION_KEY_ID,
    _spawn_host_recovery,
)
from tests.integration.test_ne01_host_worker_process_restart import (
    _LEASE_TTL_MS,
    _run_process,
)
from tests.integration.test_ne01_operation_online_takeover import (
    _Clock,
    _ProjectedWorker,
    _contracts,
    _coordinator,
    _events,
    _migrated_db,
)
from tests.integration.test_ne01_signed_checkpoint_reconciliation import (
    _TargetContinuation,
    _signature as _checkpoint_signature,
)
from tests.integration.test_ne01_signed_operation_failure import (
    _release_signature,
)
from tests.integration.test_ne01_signed_operation_reacquisition import (
    _FailOnceWorker,
)


_CHECKPOINT_INTERRUPTED_EXIT = 95
_CHECKPOINT_MUTATED_EXIT = 96
_FAILURE_INTERRUPTED_EXIT = 97
_FAILURE_MUTATED_EXIT = 98
_ADVANCE_MS = _LEASE_TTL_MS + 5
_CHECKPOINT_ACTOR = "reconciler://operator/process-checkpoint"
_CHECKPOINT_ATTESTATION = "proof://operation/process-checkpoint"
_FAILURE_ACTOR = "reconciler://operator/process-failure"
_FAILURE_ATTESTATION = "proof://operation/process-failure"


class _ExitAfterCheckpointMutation:
    """Crash after COMMIT is durable but before the accepted handler returns."""

    def __init__(
        self,
        delegate: OperationCheckpointReconciliationService,
        *,
        marker_path: str,
        marker_context: dict[str, Any],
    ) -> None:
        self._delegate = delegate
        self._marker_path = Path(marker_path)
        self._marker_context = dict(marker_context)

    def reconcile_operation_checkpoint(self, **kwargs: Any) -> Any:
        result = self._delegate.reconcile_operation_checkpoint(**kwargs)
        self._marker_path.write_text(
            json.dumps(
                {
                    **self._marker_context,
                    "pid": os.getpid(),
                    "phase": "CHECKPOINT_OPERATION_COMMITTED",
                    "target_result": result,
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        os._exit(_CHECKPOINT_MUTATED_EXIT)


class _ExitAfterFailureMutation:
    """Crash after RELEASE terminalization but before handler receipt return."""

    def __init__(
        self,
        delegate: OperationFailureReconciliationService,
        *,
        marker_path: str,
        marker_context: dict[str, Any],
    ) -> None:
        self._delegate = delegate
        self._marker_path = Path(marker_path)
        self._marker_context = dict(marker_context)

    def reconcile_operation_failure(self, **kwargs: Any) -> Any:
        result = self._delegate.reconcile_operation_failure(**kwargs)
        self._marker_path.write_text(
            json.dumps(
                {
                    **self._marker_context,
                    "pid": os.getpid(),
                    "phase": "FAILURE_OPERATION_RELEASED",
                    "target_result": result,
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        os._exit(_FAILURE_MUTATED_EXIT)


def _checkpoint_interruption_process(db_path: str, marker_path: str) -> None:
    asyncio.run(
        _checkpoint_interruption(
            db_path=Path(db_path),
            marker_path=Path(marker_path),
        )
    )


async def _checkpoint_interruption(
    *,
    db_path: Path,
    marker_path: Path,
) -> None:
    contracts = _contracts((("root", ()),))
    admitted = AdmittedExecution.from_contracts(
        command=contracts[0],
        assignment=contracts[2],
        graph=contracts[3],
    )
    clock = _Clock()
    durable = DurableTaskStore(db_path, clock=clock)
    projections = WorkerProjectionStore(db_path, clock=clock)
    worker_calls: list[str] = []
    application = GovernedCommandApplication(
        command_repository=HostCommandRepository(db_path, clock=clock),
        coordinator=_coordinator(
            store=_FailingHeartbeatStore(durable),
            projections=projections,
            worker=_ProjectedWorker(
                projections=projections,
                calls=worker_calls,
                delay_seconds=0.08,
            ),
            owner_id="process-checkpoint-owner-a",
            lease_ttl_ms=_LEASE_TTL_MS,
        ),
    )
    events = await _events(application, contracts)
    operation = dict(
        durable.get_operation_reservation(admitted.reservation_id)
    )
    snapshot = durable.read_scheduler_snapshot(
        admitted.graph_id,
        admitted.graph_revision,
    )
    with durable._connect() as conn:
        projection_rows = conn.execute(
            """
            SELECT projection_id
            FROM task_runtime_worker_projection
            WHERE attempt_id = ?
            ORDER BY projection_id
            """,
            (operation["attempt_id"],),
        ).fetchall()
    holds = durable.list_current_operation_recovery_holds(
        graph_id=admitted.graph_id
    )
    marker_path.write_text(
        json.dumps(
            {
                "pid": os.getpid(),
                "phase": "CHECKPOINT_RECOVERY_REQUIRED",
                "operation": operation,
                "attempt": snapshot["attempts"][0],
                "active_lease": snapshot["active_leases"][0],
                "projection_refs": [str(row["projection_id"]) for row in projection_rows],
                "recovery_holds": list(holds),
                "worker_calls": worker_calls,
                "execution_receipt": events[-1]["execution_receipt"],
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    os._exit(_CHECKPOINT_INTERRUPTED_EXIT)


def _failure_interruption_process(db_path: str, marker_path: str) -> None:
    asyncio.run(
        _failure_interruption(
            db_path=Path(db_path),
            marker_path=Path(marker_path),
        )
    )


async def _failure_interruption(
    *,
    db_path: Path,
    marker_path: Path,
) -> None:
    contracts = _contracts((("root", ()),))
    admitted = AdmittedExecution.from_contracts(
        command=contracts[0],
        assignment=contracts[2],
        graph=contracts[3],
    )
    clock = _Clock()
    durable = DurableTaskStore(db_path, clock=clock)
    projections = WorkerProjectionStore(db_path, clock=clock)
    worker_calls: list[str] = []
    worker = _FailOnceWorker(
        _ProjectedWorker(projections=projections, calls=worker_calls),
        worker_calls,
    )
    application = GovernedCommandApplication(
        command_repository=HostCommandRepository(db_path, clock=clock),
        coordinator=_coordinator(
            store=durable,
            projections=projections,
            worker=worker,
            owner_id="process-failure-owner-a",
            lease_ttl_ms=_LEASE_TTL_MS,
        ),
    )
    events = await _events(application, contracts)
    operation = dict(
        durable.get_operation_reservation(admitted.reservation_id)
    )
    snapshot = durable.read_scheduler_snapshot(
        admitted.graph_id,
        admitted.graph_revision,
    )
    holds = durable.list_current_operation_recovery_holds(
        graph_id=admitted.graph_id
    )
    marker_path.write_text(
        json.dumps(
            {
                "pid": os.getpid(),
                "phase": "FAILURE_RECOVERY_REQUIRED",
                "operation": operation,
                "attempt": snapshot["attempts"][0],
                "active_lease": snapshot["active_leases"][0],
                "budget": snapshot["budgets"][0],
                "recovery_holds": list(holds),
                "worker_calls": worker_calls,
                "worker_invocations": worker.invocations,
                "execution_receipt": events[-1]["execution_receipt"],
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    os._exit(_FAILURE_INTERRUPTED_EXIT)


def _signed_terminal_mutation_process(
    db_path: str,
    marker_path: str,
    public_key: bytes,
    request: dict[str, Any],
    recovery_case: dict[str, Any],
) -> None:
    clock = _Clock()
    clock.advance(_ADVANCE_MS)
    verifier = Ed25519ReconciliationProofVerifier(
        {_RECONCILIATION_KEY_ID: public_key}
    )
    store = DurableTaskStore(
        Path(db_path),
        clock=clock,
        reconciliation_proof_verifier=verifier,
    )
    action = str(request["action"])
    evidence = store.stage_operation_reconciliation_evidence(
        request_id=f"request-process-{action.lower()}",
        ingress_audit_ref=f"secure-ingress-audit://operation/process-{action.lower()}",
        body_sha256=("c" if action == "COMMIT" else "d") * 64,
        intent_ref=None,
        recovery_case_ref=str(recovery_case["case_ref"]),
        recovery_case_subject_sha256=str(recovery_case["subject_sha256"]),
        **request,
    )
    effect_payload = {
        "operator_command_id": "task_operation.reconcile",
        "request_id": evidence["request_id"],
        "evidence_ref": evidence["evidence_ref"],
        "evidence_digest": evidence["evidence_digest"],
        "reservation_id": request["reservation_id"],
        "expected_version": request["expected_version"],
        "action": action,
        "reconciler_actor_id": request["reconciler_actor_id"],
        "recovery_case_ref": recovery_case["case_ref"],
        "recovery_case_subject_sha256": recovery_case["subject_sha256"],
    }
    marker_context = {
        "evidence_ref": evidence["evidence_ref"],
        "evidence_digest": evidence["evidence_digest"],
        "effect_payload": effect_payload,
    }
    if action == "COMMIT":
        checkpoint_service = _ExitAfterCheckpointMutation(
            OperationCheckpointReconciliationService(
                task_store=store,
                command_repository=HostCommandRepository(
                    Path(db_path),
                    clock=clock,
                ),
                projection_reader=WorkerProjectionStore(
                    Path(db_path),
                    clock=clock,
                ),
                outbox_factory=OutboxMessage,
                clock_ms=clock,
            ),
            marker_path=marker_path,
            marker_context=marker_context,
        )
        failure_service: Any = object()
    else:
        checkpoint_service = object()
        failure_service = _ExitAfterFailureMutation(
            OperationFailureReconciliationService(
                task_store=store,
                outbox_factory=OutboxMessage,
            ),
            marker_path=marker_path,
            marker_context=marker_context,
        )
    command_service = OperationReconciliationCommandService(
        cancellation_service=object(),
        reacquisition_service=object(),
        checkpoint_service=checkpoint_service,
        failure_service=failure_service,
    )
    handler = AcceptedOperationReconciliationHandler(
        service=command_service,
        evidence_store=store,
        continuation_provider=lambda: (_ for _ in ()).throw(
            AssertionError("fault injection must exit before continuation")
        ),
    )
    asyncio.run(handler(effect_payload))
    raise AssertionError("fault injection must hard-exit after target mutation")


def _replay_terminal_handler_process(
    db_path: str,
    result_path: str,
    public_key: bytes,
    effect_payload: dict[str, Any],
) -> None:
    asyncio.run(
        _replay_terminal_handler(
            db_path=Path(db_path),
            result_path=Path(result_path),
            public_key=public_key,
            effect_payload=effect_payload,
        )
    )


async def _replay_terminal_handler(
    *,
    db_path: Path,
    result_path: Path,
    public_key: bytes,
    effect_payload: dict[str, Any],
) -> None:
    clock = _Clock()
    clock.advance(_ADVANCE_MS)
    verifier = Ed25519ReconciliationProofVerifier(
        {_RECONCILIATION_KEY_ID: public_key}
    )
    store = DurableTaskStore(
        db_path,
        clock=clock,
        reconciliation_proof_verifier=verifier,
    )
    action = str(effect_payload["action"])
    worker_calls: list[str] = []
    continuation: Any
    if action == "COMMIT":
        projections = WorkerProjectionStore(db_path, clock=clock)
        application = GovernedCommandApplication(
            command_repository=HostCommandRepository(db_path, clock=clock),
            coordinator=_coordinator(
                store=store,
                projections=projections,
                worker=_ProjectedWorker(
                    projections=projections,
                    calls=worker_calls,
                ),
                owner_id="process-checkpoint-owner-c",
                lease_ttl_ms=_LEASE_TTL_MS,
            ),
        )
        checkpoint_service: Any = OperationCheckpointReconciliationService(
            task_store=store,
            command_repository=HostCommandRepository(db_path, clock=clock),
            projection_reader=projections,
            outbox_factory=OutboxMessage,
            clock_ms=clock,
        )
        failure_service: Any = object()
        continuation = _TargetContinuation(
            application,
            _contracts((("root", ()),)),
        )
    else:
        checkpoint_service = object()
        failure_service = OperationFailureReconciliationService(
            task_store=store,
            outbox_factory=OutboxMessage,
        )
        continuation = None
    command_service = OperationReconciliationCommandService(
        cancellation_service=object(),
        reacquisition_service=object(),
        checkpoint_service=checkpoint_service,
        failure_service=failure_service,
    )
    handler = AcceptedOperationReconciliationHandler(
        service=command_service,
        evidence_store=store,
        continuation_provider=(
            (lambda: continuation)
            if continuation is not None
            else lambda: (_ for _ in ()).throw(
                AssertionError("terminal failure must not continue the target")
            )
        ),
    )
    result = await handler(effect_payload)
    snapshot = store.read_scheduler_snapshot("graph-gr03a", 1)
    operation = store.get_operation_reservation(
        str(effect_payload["reservation_id"])
    )
    holds = store.list_current_operation_recovery_holds(
        graph_id="graph-gr03a"
    )
    with store._connect() as conn:
        projection_count = int(
            conn.execute(
                "SELECT COUNT(*) FROM task_runtime_worker_projection"
            ).fetchone()[0]
        )
    result_path.write_text(
        json.dumps(
            {
                "pid": os.getpid(),
                "handler_result": result,
                "worker_calls": worker_calls,
                "graph": snapshot["graph_record"],
                "attempts": snapshot["attempts"],
                "active_leases": snapshot["active_leases"],
                "budgets": snapshot["budgets"],
                "operation": operation,
                "current_holds": list(holds),
                "projection_count": projection_count,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )


def _public_key(private_key: Ed25519PrivateKey) -> bytes:
    return private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )


def test_signed_checkpoint_commit_survives_handler_process_crash(
    tmp_path: Path,
) -> None:
    db_path = _migrated_db(tmp_path)
    context = multiprocessing.get_context("spawn")
    interrupted_path = tmp_path / "checkpoint-interrupted.json"
    _run_process(
        context,
        target=_checkpoint_interruption_process,
        args=(str(db_path), str(interrupted_path)),
        expected_exit_code=_CHECKPOINT_INTERRUPTED_EXIT,
    )
    interrupted = json.loads(interrupted_path.read_text(encoding="utf-8"))
    assert interrupted["phase"] == "CHECKPOINT_RECOVERY_REQUIRED"
    assert interrupted["execution_receipt"]["status"] == "RECOVERY_REQUIRED"
    assert interrupted["worker_calls"] == ["root"]
    assert len(interrupted["projection_refs"]) == 1
    assert len(interrupted["recovery_holds"]) == 1

    private_key = Ed25519PrivateKey.generate()
    public_key = _public_key(private_key)
    operation = dict(interrupted["operation"])
    request = {
        "reservation_id": operation["reservation_id"],
        "expected_version": operation["status_version"],
        "action": "COMMIT",
        "reconciler_actor_id": _CHECKPOINT_ACTOR,
        "downstream_receipt_ref": interrupted["projection_refs"][0],
        "attestation_ref": _CHECKPOINT_ATTESTATION,
        "attestation_key_id": _RECONCILIATION_KEY_ID,
        "attestation_signature": _checkpoint_signature(
            private_key,
            operation=operation,
            expected_version=operation["status_version"],
            actor_id=_CHECKPOINT_ACTOR,
            attestation_ref=_CHECKPOINT_ATTESTATION,
            receipt_ref=interrupted["projection_refs"][0],
        ),
    }
    mutation_path = tmp_path / "checkpoint-mutated.json"
    _run_process(
        context,
        target=_signed_terminal_mutation_process,
        args=(
            str(db_path),
            str(mutation_path),
            public_key,
            request,
            interrupted["recovery_holds"][0],
        ),
        expected_exit_code=_CHECKPOINT_MUTATED_EXIT,
    )
    mutation = json.loads(mutation_path.read_text(encoding="utf-8"))
    assert mutation["phase"] == "CHECKPOINT_OPERATION_COMMITTED"
    assert mutation["target_result"]["operation"]["status"] == "COMMITTED"
    assert mutation["target_result"]["replayed"] is False

    crash_clock = _Clock()
    crash_clock.advance(_ADVANCE_MS)
    crash_store = DurableTaskStore(
        db_path,
        clock=crash_clock,
        reconciliation_proof_verifier=Ed25519ReconciliationProofVerifier(
            {_RECONCILIATION_KEY_ID: public_key}
        ),
    )
    assert crash_store.get_operation_reservation(
        operation["reservation_id"]
    )["status"] == "COMMITTED"
    assert crash_store.get_graph("graph-gr03a", 1)["status"] == "ACTIVE"

    replay_path = tmp_path / "checkpoint-handler-replay.json"
    _run_process(
        context,
        target=_replay_terminal_handler_process,
        args=(
            str(db_path),
            str(replay_path),
            public_key,
            mutation["effect_payload"],
        ),
        expected_exit_code=0,
    )
    replay = json.loads(replay_path.read_text(encoding="utf-8"))
    assert replay["handler_result"]["exact_replay"] is True
    assert replay["handler_result"]["operation_status"] == "COMMITTED"
    assert replay["handler_result"]["target_continuation"][
        "execution_receipt"
    ]["status"] == "ACCEPTED"
    assert replay["worker_calls"] == []
    assert replay["graph"]["status"] == "COMPLETED"
    assert replay["attempts"][0]["status"] == "SUCCEEDED"
    assert replay["active_leases"] == []
    assert replay["current_holds"] == []
    assert replay["projection_count"] == 1

    terminal = _spawn_host_recovery(
        context,
        db_path=db_path,
        result_path=tmp_path / "checkpoint-terminal-restart.json",
        owner_id="process-checkpoint-owner-d",
        advance_ms=_ADVANCE_MS * 2,
        public_key=public_key,
    )
    assert terminal["report"] == []
    assert terminal["worker_calls"] == []
    assert terminal["projection_count"] == 1


def test_signed_failure_release_survives_handler_process_crash(
    tmp_path: Path,
) -> None:
    db_path = _migrated_db(tmp_path)
    context = multiprocessing.get_context("spawn")
    interrupted_path = tmp_path / "failure-interrupted.json"
    _run_process(
        context,
        target=_failure_interruption_process,
        args=(str(db_path), str(interrupted_path)),
        expected_exit_code=_FAILURE_INTERRUPTED_EXIT,
    )
    interrupted = json.loads(interrupted_path.read_text(encoding="utf-8"))
    assert interrupted["phase"] == "FAILURE_RECOVERY_REQUIRED"
    assert interrupted["execution_receipt"]["status"] == "RECOVERY_REQUIRED"
    assert interrupted["worker_calls"] == ["root"]
    assert interrupted["worker_invocations"] == 1
    assert len(interrupted["recovery_holds"]) == 1

    private_key = Ed25519PrivateKey.generate()
    public_key = _public_key(private_key)
    operation = dict(interrupted["operation"])
    request = {
        "reservation_id": operation["reservation_id"],
        "expected_version": operation["status_version"],
        "action": "RELEASE",
        "reconciler_actor_id": _FAILURE_ACTOR,
        "downstream_receipt_ref": None,
        "attestation_ref": _FAILURE_ATTESTATION,
        "attestation_key_id": _RECONCILIATION_KEY_ID,
        "attestation_signature": _release_signature(
            private_key,
            operation=operation,
            expected_version=operation["status_version"],
            actor_id=_FAILURE_ACTOR,
            attestation_ref=_FAILURE_ATTESTATION,
        ),
    }
    mutation_path = tmp_path / "failure-mutated.json"
    _run_process(
        context,
        target=_signed_terminal_mutation_process,
        args=(
            str(db_path),
            str(mutation_path),
            public_key,
            request,
            interrupted["recovery_holds"][0],
        ),
        expected_exit_code=_FAILURE_MUTATED_EXIT,
    )
    mutation = json.loads(mutation_path.read_text(encoding="utf-8"))
    assert mutation["phase"] == "FAILURE_OPERATION_RELEASED"
    assert mutation["target_result"]["operation"]["status"] == "RELEASED"
    assert mutation["target_result"]["replayed"] is False
    assert mutation["target_result"]["terminal"]["graph_status"] == "FAILED"

    crash_clock = _Clock()
    crash_clock.advance(_ADVANCE_MS)
    crash_store = DurableTaskStore(
        db_path,
        clock=crash_clock,
        reconciliation_proof_verifier=Ed25519ReconciliationProofVerifier(
            {_RECONCILIATION_KEY_ID: public_key}
        ),
    )
    assert crash_store.get_operation_reservation(
        operation["reservation_id"]
    )["status"] == "RELEASED"
    assert crash_store.get_graph("graph-gr03a", 1)["status"] == "FAILED"

    replay_path = tmp_path / "failure-handler-replay.json"
    _run_process(
        context,
        target=_replay_terminal_handler_process,
        args=(
            str(db_path),
            str(replay_path),
            public_key,
            mutation["effect_payload"],
        ),
        expected_exit_code=0,
    )
    replay = json.loads(replay_path.read_text(encoding="utf-8"))
    assert replay["handler_result"]["exact_replay"] is True
    assert replay["handler_result"]["operation_status"] == "RELEASED"
    assert replay["handler_result"]["target_continuation"] is None
    assert replay["handler_result"]["target_terminal"]["graph_status"] == "FAILED"
    assert replay["worker_calls"] == []
    assert replay["graph"]["status"] == "FAILED"
    assert replay["attempts"][0]["status"] == "FAILED"
    assert replay["active_leases"] == []
    assert replay["budgets"][0]["status"] == "RELEASED"
    assert replay["current_holds"] == []
    assert replay["projection_count"] == 0

    terminal = _spawn_host_recovery(
        context,
        db_path=db_path,
        result_path=tmp_path / "failure-terminal-restart.json",
        owner_id="process-failure-owner-d",
        advance_ms=_ADVANCE_MS * 2,
        public_key=public_key,
    )
    assert terminal["report"] == []
    assert terminal["worker_calls"] == []
    assert terminal["projection_count"] == 0
