from __future__ import annotations

import asyncio
import base64
import json
import multiprocessing
import os
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from host_brain.app.accepted_operation_reconciliation_handler import (
    AcceptedOperationReconciliationHandler,
)
from host_brain.persistence.host_command_repository import HostCommandRepository
from host_brain.persistence.task_repository import (
    DurableTaskStore,
    Ed25519ReconciliationProofVerifier,
    OutboxMessage,
    build_reconciliation_attestation_binding,
)
from host_brain.task_runtime.graph_cancellation_continuation_worker import (
    GraphCancellationContinuationWorker,
)
from host_brain.task_runtime.graph_cancellation_operation_reconciliation import (
    GraphCancellationOperationReconciliationService,
)
from host_brain.task_runtime.graph_cancellation_runtime import (
    GraphCancellationRuntime,
)
from host_brain.task_runtime.local_graph_execution_registry import (
    LocalGraphExecutionRegistry,
)
from host_brain.task_runtime.operation_reacquisition import (
    OperationReconciliationCommandService,
)
from host_brain.task_runtime.runtime_outbox import RuntimeOutbox
from tests.integration.test_ne01_graph_cancellation_cross_process_continuation import (
    NOW,
    _Clock,
    _NoopOutboxPublisher,
    _seed_requested_intent,
)
from tests.integration.test_ne01_host_worker_process_restart import _run_process


_KEY_ID = "reconciler-cross-process-v1"
_RESERVATION_ID = "reservation-cross-process-interrupted"
_GRAPH_ID = "graph-cross-process-cancel"
_NODE_ID = "node-1"
_ATTEMPT_ID = "attempt-cross-process-interrupted"
_SEED_EXIT = 91
_MUTATION_EXIT = 92


class _ExitAfterCancellationMutation:
    """Exit after the signed target UoW commits but before handler return."""

    def __init__(
        self,
        delegate: GraphCancellationOperationReconciliationService,
        *,
        marker_path: str,
        marker_context: dict[str, Any],
    ) -> None:
        self._delegate = delegate
        self._marker_path = Path(marker_path)
        self._marker_context = dict(marker_context)

    def reconcile_graph_cancellation_operation(
        self,
        **kwargs: Any,
    ) -> Any:
        result = self._delegate.reconcile_graph_cancellation_operation(**kwargs)
        self._marker_path.write_text(
            json.dumps(
                {
                    **self._marker_context,
                    "pid": os.getpid(),
                    "phase": "CANCELLATION_OPERATION_MUTATED",
                    "target_result": result,
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        os._exit(_MUTATION_EXIT)


def _public_key(private_key: Ed25519PrivateKey) -> bytes:
    return private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )


def _verifier(public_key: bytes) -> Ed25519ReconciliationProofVerifier:
    return Ed25519ReconciliationProofVerifier({_KEY_ID: public_key})


def _seed_cancellation_process(
    case_dir: str,
    private_key_bytes: bytes,
    marker_path: str,
) -> None:
    private_key = Ed25519PrivateKey.from_private_bytes(private_key_bytes)
    db_path, intent_ref = _seed_requested_intent(
        Path(case_dir),
        reconciliation_private_key=private_key,
        reconcile_worker_interruption=False,
    )
    store = DurableTaskStore(
        db_path,
        clock=_Clock(NOW),
        reconciliation_proof_verifier=_verifier(_public_key(private_key)),
    )
    holds = store.list_current_operation_recovery_holds(graph_id=_GRAPH_ID)
    assert len(holds) == 1
    Path(marker_path).write_text(
        json.dumps(
            {
                "pid": os.getpid(),
                "db_path": str(db_path),
                "intent_ref": intent_ref,
                "case_ref": holds[0]["case_ref"],
                "case_subject_sha256": holds[0]["subject_sha256"],
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    os._exit(_SEED_EXIT)


def _mutate_cancellation_process(
    db_path: str,
    marker_path: str,
    private_key_bytes: bytes,
    action: str,
    downstream_receipt_ref: str | None,
) -> None:
    private_key = Ed25519PrivateKey.from_private_bytes(private_key_bytes)
    public_key = _public_key(private_key)
    store = DurableTaskStore(
        Path(db_path),
        clock=_Clock(NOW),
        reconciliation_proof_verifier=_verifier(public_key),
    )
    holds = store.list_current_operation_recovery_holds(graph_id=_GRAPH_ID)
    assert len(holds) == 1
    hold = holds[0]
    operation = store.get_operation_reservation(_RESERVATION_ID)
    intent_ref = str(hold["cancellation_intent_ref"])
    actor_id = f"reconciler://operator/cancellation-{action.lower()}"
    attestation_ref = (
        f"proof://graph-cancellation/process-{action.lower()}"
    )
    outcome = "SIDE_EFFECT_CONFIRMED" if action == "COMMIT" else "NO_SIDE_EFFECT"
    binding = build_reconciliation_attestation_binding(
        reservation_id=operation["reservation_id"],
        business_scope=operation["business_scope"],
        operation_fingerprint=operation["operation_fingerprint"],
        recovery_version=operation["status_version"],
        reconciler_actor_id=actor_id,
        attestation_ref=attestation_ref,
        recovery_action=action,
        reconciliation_outcome=outcome,
        receipt_ref=downstream_receipt_ref,
    )
    signature = "ed25519:" + base64.urlsafe_b64encode(
        private_key.sign(binding)
    ).decode("ascii")
    evidence = store.stage_operation_reconciliation_evidence(
        request_id=f"request-cancellation-process-{action.lower()}",
        ingress_audit_ref=(
            f"secure-ingress-audit://operation/cancellation-{action.lower()}"
        ),
        body_sha256=("a" if action == "COMMIT" else "b") * 64,
        reservation_id=operation["reservation_id"],
        expected_version=operation["status_version"],
        action=action,
        intent_ref=intent_ref,
        reconciler_actor_id=actor_id,
        downstream_receipt_ref=downstream_receipt_ref,
        attestation_ref=attestation_ref,
        attestation_key_id=_KEY_ID,
        attestation_signature=signature,
        recovery_case_ref=hold["case_ref"],
        recovery_case_subject_sha256=hold["subject_sha256"],
    )
    effect_payload = {
        "operator_command_id": "task_operation.reconcile",
        "request_id": evidence["request_id"],
        "evidence_ref": evidence["evidence_ref"],
        "evidence_digest": evidence["evidence_digest"],
        "reservation_id": operation["reservation_id"],
        "expected_version": operation["status_version"],
        "action": action,
        "reconciler_actor_id": actor_id,
        "recovery_case_ref": hold["case_ref"],
        "recovery_case_subject_sha256": hold["subject_sha256"],
    }
    cancellation = GraphCancellationOperationReconciliationService(
        task_store=store,
        outbox_factory=OutboxMessage,
        wake_cancellation=lambda: None,
    )
    service = OperationReconciliationCommandService(
        cancellation_service=_ExitAfterCancellationMutation(
            cancellation,
            marker_path=marker_path,
            marker_context={
                "effect_payload": effect_payload,
                "evidence_ref": evidence["evidence_ref"],
                "intent_ref": intent_ref,
            },
        ),
        reacquisition_service=object(),
        checkpoint_service=object(),
        failure_service=object(),
    )
    handler = AcceptedOperationReconciliationHandler(
        service=service,
        evidence_store=store,
        continuation_provider=lambda: (_ for _ in ()).throw(
            AssertionError("cancellation reconciliation has no graph executor")
        ),
    )
    asyncio.run(handler(effect_payload))
    raise AssertionError("handler process did not exit after target mutation")


def _cancellation_worker(
    *,
    db_path: Path,
    store: DurableTaskStore,
    worker_id: str,
) -> GraphCancellationContinuationWorker:
    runtime = GraphCancellationRuntime(
        task_store=store,
        runtime_outbox=RuntimeOutbox(
            publisher=_NoopOutboxPublisher(),
            factory=OutboxMessage,
            publish_limit=100,
        ),
        execution_registry=LocalGraphExecutionRegistry(),
        lease_ttl_ms=1_000,
    )
    return GraphCancellationContinuationWorker(
        task_store=store,
        command_repository=HostCommandRepository(
            db_path,
            clock=_Clock(NOW),
        ),
        cancellation_runtime=runtime,
        worker_id=worker_id,
        claim_lease_ms=1_000,
        retry_delay_ms=100,
        batch_limit=1,
    )


def _replay_and_finalize_process(
    db_path: str,
    result_path: str,
    public_key: bytes,
    effect_payload: dict[str, Any],
) -> None:
    asyncio.run(
        _replay_and_finalize(
            db_path=Path(db_path),
            result_path=Path(result_path),
            public_key=public_key,
            effect_payload=effect_payload,
        )
    )


async def _replay_and_finalize(
    *,
    db_path: Path,
    result_path: Path,
    public_key: bytes,
    effect_payload: dict[str, Any],
) -> None:
    store = DurableTaskStore(
        db_path,
        clock=_Clock(NOW),
        reconciliation_proof_verifier=_verifier(public_key),
    )
    wake_count = 0

    def wake() -> None:
        nonlocal wake_count
        wake_count += 1

    cancellation = GraphCancellationOperationReconciliationService(
        task_store=store,
        outbox_factory=OutboxMessage,
        wake_cancellation=wake,
    )
    service = OperationReconciliationCommandService(
        cancellation_service=cancellation,
        reacquisition_service=object(),
        checkpoint_service=object(),
        failure_service=object(),
    )
    handler = AcceptedOperationReconciliationHandler(
        service=service,
        evidence_store=store,
        continuation_provider=lambda: (_ for _ in ()).throw(
            AssertionError("cancellation replay must not run a graph worker")
        ),
    )
    cancellation_before = store.get_graph_cancellation_for_graph(
        _GRAPH_ID,
        1,
    )
    assert cancellation_before is not None
    intent_ref = str(cancellation_before["intent_ref"])
    handler_result = await handler(effect_payload)
    worker = _cancellation_worker(
        db_path=db_path,
        store=store,
        worker_id="cancellation-process-replay",
    )
    processed = await worker.run_once()
    replay_processed = await worker.run_once()
    result_path.write_text(
        json.dumps(
            {
                "handler_result": handler_result,
                "wake_count": wake_count,
                "processed": processed,
                "replay_processed": replay_processed,
                "operation": store.get_operation_reservation(_RESERVATION_ID),
                "graph": store.get_graph(_GRAPH_ID, 1),
                "node": store.get_node(_GRAPH_ID, 1, _NODE_ID),
                "attempt": store.get_attempt(_ATTEMPT_ID),
                "cancellation": store.get_graph_cancellation_for_graph(
                    _GRAPH_ID,
                    1,
                ),
                "decision": store.get_graph_cancellation_decision_for_intent(
                    intent_ref
                ),
                "continuation": store.get_graph_cancellation_continuation(
                    intent_ref
                ),
                "current_holds": list(
                    store.list_current_operation_recovery_holds(
                        graph_id=_GRAPH_ID
                    )
                ),
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )


def _terminal_restart_process(
    db_path: str,
    result_path: str,
    public_key: bytes,
) -> None:
    asyncio.run(
        _terminal_restart(
            db_path=Path(db_path),
            result_path=Path(result_path),
            public_key=public_key,
        )
    )


async def _terminal_restart(
    *,
    db_path: Path,
    result_path: Path,
    public_key: bytes,
) -> None:
    store = DurableTaskStore(
        db_path,
        clock=_Clock(NOW),
        reconciliation_proof_verifier=_verifier(public_key),
    )
    worker = _cancellation_worker(
        db_path=db_path,
        store=store,
        worker_id="cancellation-process-terminal",
    )
    result_path.write_text(
        json.dumps(
            {
                "processed": await worker.run_once(),
                "continuations": store.list_pending_graph_cancellations(
                    limit=10
                ),
                "current_holds": list(
                    store.list_current_operation_recovery_holds(
                        graph_id=_GRAPH_ID
                    )
                ),
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )


@pytest.mark.parametrize(
    ("action", "downstream_receipt_ref", "expected_status"),
    (
        (
            "COMMIT",
            "effect-receipt://graph-cancellation/process-confirmed",
            "COMMITTED",
        ),
        ("RELEASE", None, "RELEASED"),
    ),
)
def test_signed_cancellation_action_survives_handler_process_crash(
    tmp_path: Path,
    action: str,
    downstream_receipt_ref: str | None,
    expected_status: str,
) -> None:
    context = multiprocessing.get_context("spawn")
    case_dir = tmp_path / action.lower()
    case_dir.mkdir()
    private_key = Ed25519PrivateKey.generate()
    private_key_bytes = private_key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_key = _public_key(private_key)
    seed_path = case_dir / "seed.json"
    _run_process(
        context,
        target=_seed_cancellation_process,
        args=(str(case_dir), private_key_bytes, str(seed_path)),
        expected_exit_code=_SEED_EXIT,
    )
    seeded = json.loads(seed_path.read_text(encoding="utf-8"))
    db_path = Path(seeded["db_path"])
    assert seeded["case_ref"].startswith(
        "operation-recovery-hold://sha256/"
    )

    mutation_path = case_dir / "mutation.json"
    _run_process(
        context,
        target=_mutate_cancellation_process,
        args=(
            str(db_path),
            str(mutation_path),
            private_key_bytes,
            action,
            downstream_receipt_ref,
        ),
        expected_exit_code=_MUTATION_EXIT,
    )
    mutation = json.loads(mutation_path.read_text(encoding="utf-8"))
    assert mutation["phase"] == "CANCELLATION_OPERATION_MUTATED"
    assert mutation["target_result"]["operation"]["status"] == expected_status
    assert mutation["target_result"]["replayed"] is False

    replay_path = case_dir / "replay.json"
    _run_process(
        context,
        target=_replay_and_finalize_process,
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
    assert replay["handler_result"]["operation_status"] == expected_status
    assert replay["handler_result"]["target_continuation"] is None
    assert replay["handler_result"]["target_terminal"] is None
    assert replay["handler_result"]["recovery_case"]["current"] is False
    assert replay["wake_count"] == 1
    assert replay["processed"] == 1
    assert replay["replay_processed"] == 0
    assert replay["operation"]["status"] == expected_status
    assert replay["graph"]["status"] == "CANCELLED"
    assert replay["node"]["status"] == "CANCELLED"
    assert replay["attempt"]["status"] == "CANCELLED"
    assert replay["cancellation"]["status"] == "FINALIZED"
    assert replay["decision"]["payload"]["graph_target_status"] == "CANCELLED"
    assert replay["continuation"] is None
    assert replay["current_holds"] == []

    terminal_path = case_dir / "terminal.json"
    _run_process(
        context,
        target=_terminal_restart_process,
        args=(str(db_path), str(terminal_path), public_key),
        expected_exit_code=0,
    )
    terminal = json.loads(terminal_path.read_text(encoding="utf-8"))
    assert terminal == {
        "continuations": [],
        "current_holds": [],
        "processed": 0,
    }
