from __future__ import annotations

import asyncio
import base64
from dataclasses import asdict
import json
import multiprocessing
import os
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from host_brain.agent_runtime.runtime_router import CanonicalRuntimeRouter
from host_brain.app.governed_task_adapter import GovernedHostRuntime
from host_brain.contracts.agent_runtime.operation_reconciliation import (
    build_reconciliation_attestation_binding,
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
from host_brain.task_runtime.operation_reacquisition import (
    OperationReacquisitionService,
)
from tests.integration.test_ne01_host_worker_process_restart import (
    _LEASE_TTL_MS,
    _lease_lineage,
    _operation_rows,
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


_RESERVED_EXIT_CODE = 94
_RECOVERY_ADVANCE_MS = 25
_REACQUISITION_OWNER = "process-owner-c-signed-reacquisition"
_RECONCILIATION_ACTOR = "reconciler://operator/process-restart"
_RECONCILIATION_KEY_ID = "process-restart-reacquisition-v1"
_ATTESTATION_REF = "proof://operation/process-restart-no-side-effect"


class _ExitAfterOperationReservationStore:
    """Hard-exit after RESERVED commits and before a worker can start."""

    def __init__(self, delegate: DurableTaskStore, marker_path: str) -> None:
        self._delegate = delegate
        self._marker_path = Path(marker_path)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)

    def reserve_operation(self, **kwargs: Any) -> Any:
        result = self._delegate.reserve_operation(**kwargs)
        operation = self._delegate.get_operation_reservation(
            str(kwargs["reservation_id"])
        )
        self._marker_path.write_text(
            json.dumps(
                {
                    "phase": "OPERATION_RESERVED",
                    "pid": os.getpid(),
                    "reservation_id": operation["reservation_id"],
                    "attempt_id": operation["attempt_id"],
                    "lease_id": operation["lease_id"],
                    "owner_id": operation["owner_id"],
                    "fencing_token": operation["fencing_token"],
                    "status": operation["status"],
                    "status_version": operation["status_version"],
                    "acquired": bool(result.acquired),
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        os._exit(_RESERVED_EXIT_CODE)


def _crash_after_reserved_process(db_path: str, marker_path: str) -> None:
    asyncio.run(
        _crash_after_reserved(
            db_path=Path(db_path),
            marker_path=marker_path,
        )
    )


async def _crash_after_reserved(*, db_path: Path, marker_path: str) -> None:
    contracts = _contracts((("root", ()),))
    clock = _Clock()
    durable = DurableTaskStore(db_path, clock=clock)
    projections = WorkerProjectionStore(db_path, clock=clock)
    worker_calls: list[str] = []
    worker = _ProjectedWorker(projections=projections, calls=worker_calls)
    application = GovernedCommandApplication(
        command_repository=HostCommandRepository(db_path, clock=clock),
        coordinator=_coordinator(
            store=_ExitAfterOperationReservationStore(durable, marker_path),
            projections=projections,
            worker=worker,
            owner_id="process-owner-a-reserved",
            lease_ttl_ms=_LEASE_TTL_MS,
        ),
    )
    await _events(application, contracts)
    raise AssertionError("process must exit immediately after Operation reservation")


def _host_recovery_process(
    db_path: str,
    result_path: str,
    owner_id: str,
    advance_ms: int,
    public_key: bytes | None,
) -> None:
    asyncio.run(
        _host_recovery(
            db_path=Path(db_path),
            result_path=Path(result_path),
            owner_id=owner_id,
            advance_ms=advance_ms,
            public_key=public_key,
        )
    )


async def _host_recovery(
    *,
    db_path: Path,
    result_path: Path,
    owner_id: str,
    advance_ms: int,
    public_key: bytes | None,
) -> None:
    clock = _Clock()
    clock.advance(advance_ms)
    verifier = (
        Ed25519ReconciliationProofVerifier(
            {_RECONCILIATION_KEY_ID: public_key}
        )
        if public_key is not None
        else None
    )
    durable = DurableTaskStore(
        db_path,
        clock=clock,
        reconciliation_proof_verifier=verifier,
    )
    startup = durable.recover_startup(
        recovery_id=f"process-restart:{owner_id}:{advance_ms}"
    )
    commands = HostCommandRepository(db_path, clock=clock, task_store=durable)
    projections = WorkerProjectionStore(db_path, clock=clock)
    worker_calls: list[str] = []
    worker = _ProjectedWorker(projections=projections, calls=worker_calls)
    application = GovernedCommandApplication(
        command_repository=commands,
        coordinator=_coordinator(
            store=durable,
            projections=projections,
            worker=worker,
            owner_id=owner_id,
            lease_ttl_ms=_LEASE_TTL_MS,
        ),
    )
    runtime = GovernedHostRuntime(
        application=application,
        runtime_router=CanonicalRuntimeRouter(),
        command_repository=commands,
        task_store=durable,
        supported_routes=frozenset({"chat"}),
        survivor_interval_ms=60_000,
    )
    await runtime.activate()
    report = await runtime.recover_startup_graphs()
    await runtime.aclose()

    contracts = _contracts((("root", ()),))
    admitted = AdmittedExecution.from_contracts(
        command=contracts[0],
        assignment=contracts[2],
        graph=contracts[3],
    )
    operation = durable.get_operation_reservation(admitted.reservation_id)
    snapshot = durable.read_scheduler_snapshot(
        admitted.graph_id,
        admitted.graph_revision,
    )
    holds = durable.list_current_operation_recovery_holds(
        graph_id=admitted.graph_id
    )
    with durable._connect() as conn:
        projection_count = int(
            conn.execute(
                "SELECT COUNT(*) FROM task_runtime_worker_projection"
            ).fetchone()[0]
        )
    Path(result_path).write_text(
        json.dumps(
            {
                "pid": os.getpid(),
                "startup": asdict(startup),
                "report": list(report),
                "worker_calls": worker_calls,
                "operation": operation,
                "attempts": snapshot["attempts"],
                "active_leases": snapshot["active_leases"],
                "holds": list(holds),
                "projection_count": projection_count,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )


def _signed_reacquire_process(
    db_path: str,
    result_path: str,
    public_key: bytes,
    request: dict[str, Any],
    recovery_case: dict[str, Any],
) -> None:
    clock = _Clock()
    clock.advance(_RECOVERY_ADVANCE_MS)
    verifier = Ed25519ReconciliationProofVerifier(
        {_RECONCILIATION_KEY_ID: public_key}
    )
    store = DurableTaskStore(
        Path(db_path),
        clock=clock,
        reconciliation_proof_verifier=verifier,
    )
    evidence = store.stage_operation_reconciliation_evidence(
        request_id="request-process-restart-reacquisition",
        ingress_audit_ref="secure-ingress-audit://operation/process-restart",
        body_sha256="6" * 64,
        intent_ref=None,
        action="REACQUIRE",
        downstream_receipt_ref=None,
        recovery_case_ref=str(recovery_case["case_ref"]),
        recovery_case_subject_sha256=str(recovery_case["subject_sha256"]),
        **request,
    )
    service = OperationReacquisitionService(
        task_store=store,
        outbox_factory=OutboxMessage,
        proof_verifier=verifier,
        lease_ttl_ms=_LEASE_TTL_MS,
    )
    service.bind_execution_owner(_REACQUISITION_OWNER)
    reconciled = service.reconcile_operation_reacquisition(**request)
    scheduler_snapshot = store.read_scheduler_snapshot("graph-gr03a", 1)
    Path(result_path).write_text(
        json.dumps(
            {
                "pid": os.getpid(),
                "evidence_ref": evidence["evidence_ref"],
                "evidence_exact_replay": evidence["exact_replay"],
                "reconciled": reconciled,
                "operation_execution_fences": scheduler_snapshot[
                    "operation_execution_fences"
                ],
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )


def _spawn_host_recovery(
    context: multiprocessing.context.SpawnContext,
    *,
    db_path: Path,
    result_path: Path,
    owner_id: str,
    advance_ms: int,
    public_key: bytes | None = None,
) -> dict[str, Any]:
    _run_process(
        context,
        target=_host_recovery_process,
        args=(str(db_path), str(result_path), owner_id, advance_ms, public_key),
        expected_exit_code=0,
    )
    return json.loads(result_path.read_text(encoding="utf-8"))


def _signature(
    private_key: Ed25519PrivateKey,
    *,
    operation: dict[str, Any],
) -> str:
    binding = build_reconciliation_attestation_binding(
        reservation_id=str(operation["reservation_id"]),
        business_scope=str(operation["business_scope"]),
        operation_fingerprint=str(operation["operation_fingerprint"]),
        recovery_version=int(operation["status_version"]),
        reconciler_actor_id=_RECONCILIATION_ACTOR,
        attestation_ref=_ATTESTATION_REF,
        recovery_action="REACQUIRE",
        reconciliation_outcome="NO_SIDE_EFFECT",
    )
    return "ed25519:" + base64.urlsafe_b64encode(
        private_key.sign(binding)
    ).decode("ascii")


def test_host_process_restart_requires_signed_no_side_effect_before_rerun(
    tmp_path: Path,
) -> None:
    db_path = _migrated_db(tmp_path)
    marker_path = tmp_path / "reserved-crash.json"
    context = multiprocessing.get_context("spawn")
    _run_process(
        context,
        target=_crash_after_reserved_process,
        args=(str(db_path), str(marker_path)),
        expected_exit_code=_RESERVED_EXIT_CODE,
    )
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    assert marker["phase"] == "OPERATION_RESERVED"
    assert marker["status"] == "RESERVED"
    assert marker["status_version"] == 0
    assert marker["acquired"] is True

    before = DurableTaskStore(db_path, clock=_Clock())
    original_snapshot = before.read_scheduler_snapshot("graph-gr03a", 1)
    original_attempt = dict(original_snapshot["attempts"][0])
    original_lease = dict(original_snapshot["active_leases"][0])
    assert original_attempt["status"] == "RUNNING"
    assert original_lease["owner_id"] == "process-owner-a-reserved"
    assert before.get_operation_reservation(marker["reservation_id"])["status"] == (
        "RESERVED"
    )

    live = _spawn_host_recovery(
        context,
        db_path=db_path,
        result_path=tmp_path / "live-lease-restart.json",
        owner_id="process-owner-b-live",
        advance_ms=0,
    )
    assert live["report"] == [
        {
            "graph_id": "graph-gr03a",
            "graph_revision": 1,
            "reason_code": "GOVERNED_HOST_STARTUP_LIVE_LEASE",
            "status": "DEFERRED",
        }
    ]
    assert live["worker_calls"] == []
    assert live["projection_count"] == 0
    assert live["operation"]["status"] == "RESERVED"
    assert live["holds"] == []

    expired = _spawn_host_recovery(
        context,
        db_path=db_path,
        result_path=tmp_path / "expired-lease-restart.json",
        owner_id="process-owner-b-expired",
        advance_ms=_RECOVERY_ADVANCE_MS,
    )
    assert expired["report"] == [
        {
            "graph_id": "graph-gr03a",
            "graph_revision": 1,
            "reason_code": "GOVERNED_OPERATION_RECOVERY_REQUIRED",
            "status": "DEFERRED",
        }
    ]
    assert expired["worker_calls"] == []
    assert expired["projection_count"] == 0
    assert expired["operation"]["status"] == "RECOVERY_REQUIRED"
    assert expired["operation"]["recovery_reason_code"] == (
        "IDEMPOTENCY_RESERVATION_EXPIRED"
    )
    assert len(expired["holds"]) == 1
    recovery_case = dict(expired["holds"][0])
    assert recovery_case["current"] is True
    assert recovery_case["case_kind"] == "UNKNOWN_SIDE_EFFECT"
    assert recovery_case["reservation_id"] == marker["reservation_id"]
    assert expired["startup"]["recovered_attempt_ids"] == [
        original_attempt["attempt_id"]
    ]
    assert expired["startup"]["recovery_required_reservation_ids"] == [
        marker["reservation_id"]
    ]

    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    recovery_operation = dict(expired["operation"])
    request = {
        "reservation_id": recovery_operation["reservation_id"],
        "expected_version": recovery_operation["status_version"],
        "reconciler_actor_id": _RECONCILIATION_ACTOR,
        "attestation_ref": _ATTESTATION_REF,
        "attestation_key_id": _RECONCILIATION_KEY_ID,
        "attestation_signature": _signature(
            private_key,
            operation=recovery_operation,
        ),
    }
    reacquired_path = tmp_path / "signed-reacquisition.json"
    _run_process(
        context,
        target=_signed_reacquire_process,
        args=(
            str(db_path),
            str(reacquired_path),
            public_key,
            request,
            recovery_case,
        ),
        expected_exit_code=0,
    )
    reacquired = json.loads(reacquired_path.read_text(encoding="utf-8"))
    assert reacquired["evidence_exact_replay"] is False
    assert reacquired["reconciled"]["status"] == "RECONCILED"
    assert reacquired["reconciled"]["replayed"] is False
    assert reacquired["reconciled"]["attempt_id"] == original_attempt["attempt_id"]
    assert reacquired["reconciled"]["operation"]["status"] == "RESERVED"
    assert reacquired["reconciled"]["operation"]["owner_id"] == (
        _REACQUISITION_OWNER
    )
    assert reacquired["reconciled"]["operation"]["fencing_token"] == (
        original_lease["fencing_token"] + 1
    )
    assert reacquired["operation_execution_fences"] == [
        {
            "reservation_id": marker["reservation_id"],
            "attempt_id": original_attempt["attempt_id"],
            "lease_id": reacquired["reconciled"]["operation"]["lease_id"],
            "owner_id": _REACQUISITION_OWNER,
            "fencing_token": original_lease["fencing_token"] + 1,
            "status": "RESERVED",
            "status_version": reacquired["reconciled"]["operation"][
                "status_version"
            ],
            "signed_reacquisition_verified": True,
        }
    ]
    operation_fence_text = json.dumps(
        reacquired["operation_execution_fences"],
        sort_keys=True,
    )
    assert request["attestation_signature"] not in operation_fence_text
    for forbidden_field in (
        "business_scope",
        "operation_fingerprint",
        "receipt_ref",
        "reconciler_actor_id",
        "reconciliation_proof_ref",
        "reconciliation_proof_digest",
        "reconciliation_proof_key_id",
        "reconciliation_proof_signature",
    ):
        assert forbidden_field not in operation_fence_text

    resumed = _spawn_host_recovery(
        context,
        db_path=db_path,
        result_path=tmp_path / "signed-host-restart.json",
        owner_id=_REACQUISITION_OWNER,
        advance_ms=_RECOVERY_ADVANCE_MS,
        public_key=public_key,
    )
    assert resumed["report"] == [
        {
            "graph_id": "graph-gr03a",
            "graph_revision": 1,
            "reason_code": None,
            "status": "COMPLETED",
        }
    ]
    assert resumed["worker_calls"] == ["root"]
    assert resumed["projection_count"] == 1
    assert resumed["operation"]["status"] == "COMMITTED"
    assert resumed["operation"]["reconciliation_action"] == "REACQUIRE"
    assert resumed["operation"]["reconciliation_outcome"] == "NO_SIDE_EFFECT"
    assert resumed["holds"] == []

    settled_clock = _Clock()
    settled_clock.advance(_RECOVERY_ADVANCE_MS)
    settled = DurableTaskStore(
        db_path,
        clock=settled_clock,
        reconciliation_proof_verifier=Ed25519ReconciliationProofVerifier(
            {_RECONCILIATION_KEY_ID: public_key}
        ),
    )
    final_snapshot = settled.read_scheduler_snapshot("graph-gr03a", 1)
    assert final_snapshot["graph_record"]["status"] == "COMPLETED"
    assert len(final_snapshot["attempts"]) == 1
    assert final_snapshot["attempts"][0]["attempt_id"] == (
        original_attempt["attempt_id"]
    )
    assert final_snapshot["attempts"][0]["status"] == "SUCCEEDED"
    assert settled.get_node("graph-gr03a", 1, "root")["status"] == "ACCEPTED"
    assert _lease_lineage(
        settled,
        attempt_id=original_attempt["attempt_id"],
    ) == [
        (
            "process-owner-a-reserved",
            original_lease["fencing_token"],
            "EXPIRED",
        ),
        (
            _REACQUISITION_OWNER,
            original_lease["fencing_token"] + 1,
            "RELEASED",
        ),
    ]
    operations = _operation_rows(
        settled,
        attempt_id=original_attempt["attempt_id"],
    )
    assert len(operations) == 1
    assert operations[0]["status"] == "COMMITTED"
    assert operations[0]["owner_id"] == _REACQUISITION_OWNER
    precharges = [
        usage
        for usage in final_snapshot["budget_usage"]
        if usage["charge_kind"] == "PRECHARGE"
    ]
    assert len(precharges) == 2
    assert len({usage["usage_id"] for usage in precharges}) == 2

    with settled._connect() as conn:
        admission = conn.execute(
            """
            SELECT command_json, work_order_json, assignment_json, graph_json
            FROM host_command_journal
            """
        ).fetchone()
        evidence_signature = conn.execute(
            """
            SELECT attestation_signature
            FROM task_runtime_operation_reconciliation_evidence
            WHERE evidence_ref = ?
            """,
            (reacquired["evidence_ref"],),
        ).fetchone()[0]
    admission_text = "\n".join(str(value) for value in admission)
    assert request["attestation_signature"] not in admission_text
    assert evidence_signature == request["attestation_signature"]

    replay = _spawn_host_recovery(
        context,
        db_path=db_path,
        result_path=tmp_path / "terminal-replay.json",
        owner_id="process-owner-d-terminal-replay",
        advance_ms=_RECOVERY_ADVANCE_MS * 2,
        public_key=public_key,
    )
    assert replay["report"] == []
    assert replay["worker_calls"] == []
    assert replay["projection_count"] == 1
