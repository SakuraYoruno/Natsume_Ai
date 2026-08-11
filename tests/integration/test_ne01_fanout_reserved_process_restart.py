from __future__ import annotations

import asyncio
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
from host_brain.orchestration import GovernedCommandApplication
from host_brain.persistence.host_command_repository import HostCommandRepository
from host_brain.persistence.task_repository import (
    DurableTaskStore,
    Ed25519ReconciliationProofVerifier,
)
from host_brain.persistence.worker_projection_repository import (
    WorkerProjectionStore,
)
from tests.integration.test_ne01_host_signed_unknown_effect_process_restart import (
    _REACQUISITION_OWNER,
    _RECONCILIATION_KEY_ID,
    _RECOVERY_ADVANCE_MS,
    _signature,
    _signed_reacquire_process,
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


_CRASH_EXIT_CODE = 93
_GRAPH_ID = "graph-gr03a"
_LEFT_NODE = "left"
_UNKNOWN_NODE = "right"
_JOIN_NODE = "join"
_SPECS = (
    (_LEFT_NODE, ()),
    (_UNKNOWN_NODE, ()),
    (_JOIN_NODE, (_LEFT_NODE, _UNKNOWN_NODE)),
)


class _ExitUnknownSiblingAfterAcceptedPeer:
    """Keep one RESERVED sibling live until its peer is durably ACCEPTED."""

    def __init__(
        self,
        *,
        delegate: _ProjectedWorker,
        store: DurableTaskStore,
        marker_path: str,
    ) -> None:
        self._delegate = delegate
        self._store = store
        self._marker_path = Path(marker_path)

    async def execute_stream(
        self,
        *,
        execution_context: dict[str, Any],
        **kwargs: Any,
    ):
        node_id = str(execution_context["node_id"])
        if node_id != _UNKNOWN_NODE:
            async for event in self._delegate.execute_stream(
                execution_context=execution_context,
                **kwargs,
            ):
                yield event
            return

        for _ in range(2_000):
            left = self._store.get_node(_GRAPH_ID, 1, _LEFT_NODE)
            if left["status"] == "ACCEPTED":
                break
            await asyncio.sleep(0.005)
        else:
            raise AssertionError("accepted fan-out peer was not observed")

        attempt_id = str(execution_context["attempt_id"])
        with self._store._connect() as conn:
            operation = conn.execute(
                """
                SELECT reservation_id, attempt_id, lease_id, owner_id,
                       fencing_token, status, status_version
                  FROM task_runtime_idempotency_reservation
                 WHERE attempt_id = ?
                """,
                (attempt_id,),
            ).fetchone()
        assert operation is not None
        self._marker_path.write_text(
            json.dumps(
                {
                    "pid": os.getpid(),
                    "phase": "FANOUT_PEER_ACCEPTED_UNKNOWN_RESERVED",
                    "accepted_node": self._store.get_node(
                        _GRAPH_ID,
                        1,
                        _LEFT_NODE,
                    ),
                    "unknown_node": self._store.get_node(
                        _GRAPH_ID,
                        1,
                        _UNKNOWN_NODE,
                    ),
                    "operation": dict(operation),
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        os._exit(_CRASH_EXIT_CODE)


def _crash_fanout_process(db_path: str, marker_path: str) -> None:
    asyncio.run(
        _crash_fanout(
            db_path=Path(db_path),
            marker_path=marker_path,
        )
    )


async def _crash_fanout(*, db_path: Path, marker_path: str) -> None:
    contracts = _contracts(_SPECS)
    clock = _Clock()
    durable = DurableTaskStore(db_path, clock=clock)
    projections = WorkerProjectionStore(db_path, clock=clock)
    delegate = _ProjectedWorker(projections=projections, calls=[])
    worker = _ExitUnknownSiblingAfterAcceptedPeer(
        delegate=delegate,
        store=durable,
        marker_path=marker_path,
    )
    application = GovernedCommandApplication(
        command_repository=HostCommandRepository(db_path, clock=clock),
        coordinator=_coordinator(
            store=durable,
            projections=projections,
            worker=worker,
            owner_id="fanout-process-owner-a",
            lease_ttl_ms=_LEASE_TTL_MS,
            bounded_fanout_enabled=True,
        ),
    )
    await _events(application, contracts)
    raise AssertionError("fan-out process did not exit in RESERVED window")


def _recover_fanout_process(
    db_path: str,
    result_path: str,
    owner_id: str,
    advance_ms: int,
    public_key: bytes | None,
) -> None:
    asyncio.run(
        _recover_fanout(
            db_path=Path(db_path),
            result_path=Path(result_path),
            owner_id=owner_id,
            advance_ms=advance_ms,
            public_key=public_key,
        )
    )


async def _recover_fanout(
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
        recovery_id=f"fanout-process:{owner_id}:{advance_ms}"
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
            bounded_fanout_enabled=True,
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
    snapshot = durable.read_scheduler_snapshot(_GRAPH_ID, 1)
    with durable._connect() as conn:
        operations = [
            dict(row)
            for row in conn.execute(
                """
                SELECT r.reservation_id, r.attempt_id, a.node_id,
                       r.owner_id, r.fencing_token, r.status,
                       r.status_version, r.reconciliation_action,
                       r.reconciliation_outcome
                  FROM task_runtime_idempotency_reservation AS r
                  JOIN task_runtime_attempt AS a
                    ON a.attempt_id = r.attempt_id
                 ORDER BY a.node_id
                """
            ).fetchall()
        ]
        projection_count = int(
            conn.execute(
                "SELECT COUNT(*) FROM task_runtime_worker_projection"
            ).fetchone()[0]
        )
    result_path.write_text(
        json.dumps(
            {
                "pid": os.getpid(),
                "startup": asdict(startup),
                "report": list(report),
                "worker_calls": worker_calls,
                "graph": snapshot["graph_record"],
                "nodes": snapshot["graph_record"]["nodes"],
                "attempts": snapshot["attempts"],
                "active_leases": snapshot["active_leases"],
                "holds": list(
                    durable.list_current_operation_recovery_holds(
                        graph_id=_GRAPH_ID
                    )
                ),
                "operations": operations,
                "projection_count": projection_count,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )


def _spawn_fanout_recovery(
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
        target=_recover_fanout_process,
        args=(
            str(db_path),
            str(result_path),
            owner_id,
            advance_ms,
            public_key,
        ),
        expected_exit_code=0,
    )
    return json.loads(result_path.read_text(encoding="utf-8"))


def _node(records: list[dict[str, Any]], node_id: str) -> dict[str, Any]:
    matches = [record for record in records if record["node_id"] == node_id]
    assert len(matches) == 1
    return matches[0]


def test_fanout_reserved_sibling_requires_signed_recovery_after_process_crash(
    tmp_path: Path,
) -> None:
    db_path = _migrated_db(tmp_path)
    context = multiprocessing.get_context("spawn")
    marker_path = tmp_path / "fanout-crash.json"
    _run_process(
        context,
        target=_crash_fanout_process,
        args=(str(db_path), str(marker_path)),
        expected_exit_code=_CRASH_EXIT_CODE,
    )
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    assert marker["phase"] == "FANOUT_PEER_ACCEPTED_UNKNOWN_RESERVED"
    assert marker["accepted_node"]["status"] == "ACCEPTED"
    assert marker["unknown_node"]["status"] == "RUNNING"
    assert marker["operation"]["status"] == "RESERVED"

    live = _spawn_fanout_recovery(
        context,
        db_path=db_path,
        result_path=tmp_path / "fanout-live.json",
        owner_id="fanout-process-owner-b-live",
        advance_ms=0,
    )
    assert live["worker_calls"] == []
    assert live["report"][0]["status"] == "DEFERRED"
    assert live["report"][0]["reason_code"] == "GOVERNED_HOST_STARTUP_LIVE_LEASE"
    assert _node(live["nodes"], _LEFT_NODE)["status"] == "ACCEPTED"
    assert _node(live["nodes"], _UNKNOWN_NODE)["status"] == "RUNNING"
    assert live["holds"] == []
    assert live["projection_count"] == 1

    expired = _spawn_fanout_recovery(
        context,
        db_path=db_path,
        result_path=tmp_path / "fanout-expired.json",
        owner_id="fanout-process-owner-b-expired",
        advance_ms=_RECOVERY_ADVANCE_MS,
    )
    assert expired["worker_calls"] == []
    assert expired["report"][0]["status"] == "DEFERRED"
    assert expired["report"][0]["reason_code"] == (
        "GOVERNED_OPERATION_RECOVERY_REQUIRED"
    )
    assert _node(expired["nodes"], _LEFT_NODE)["status"] == "ACCEPTED"
    assert len(expired["holds"]) == 1
    recovery_case = dict(expired["holds"][0])
    assert recovery_case["node_id"] == _UNKNOWN_NODE
    assert recovery_case["case_kind"] == "UNKNOWN_SIDE_EFFECT"
    recovery_operation = _node(expired["operations"], _UNKNOWN_NODE)
    assert recovery_operation["status"] == "RECOVERY_REQUIRED"

    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    operation_for_signature = DurableTaskStore(
        db_path,
        clock=_Clock(),
    ).get_operation_reservation(recovery_operation["reservation_id"])
    request = {
        "reservation_id": recovery_operation["reservation_id"],
        "expected_version": recovery_operation["status_version"],
        "reconciler_actor_id": (
            "reconciler://operator/process-restart"
        ),
        "attestation_ref": (
            "proof://operation/process-restart-no-side-effect"
        ),
        "attestation_key_id": _RECONCILIATION_KEY_ID,
        "attestation_signature": _signature(
            private_key,
            operation=operation_for_signature,
        ),
    }
    reacquired_path = tmp_path / "fanout-reacquired.json"
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
    assert reacquired["reconciled"]["replayed"] is False
    assert reacquired["reconciled"]["operation"]["owner_id"] == (
        _REACQUISITION_OWNER
    )

    resumed = _spawn_fanout_recovery(
        context,
        db_path=db_path,
        result_path=tmp_path / "fanout-resumed.json",
        owner_id=_REACQUISITION_OWNER,
        advance_ms=_RECOVERY_ADVANCE_MS,
        public_key=public_key,
    )
    assert resumed["report"] == [
        {
            "graph_id": _GRAPH_ID,
            "graph_revision": 1,
            "reason_code": None,
            "status": "COMPLETED",
        }
    ]
    assert resumed["worker_calls"] == [_UNKNOWN_NODE, _JOIN_NODE]
    assert resumed["graph"]["status"] == "COMPLETED"
    assert _node(resumed["nodes"], _LEFT_NODE)["status"] == "ACCEPTED"
    assert _node(resumed["nodes"], _UNKNOWN_NODE)["status"] == "ACCEPTED"
    assert _node(resumed["nodes"], _JOIN_NODE)["status"] == "ACCEPTED"
    assert resumed["holds"] == []
    assert resumed["projection_count"] == 3
    assert _node(resumed["operations"], _UNKNOWN_NODE)[
        "reconciliation_action"
    ] == "REACQUIRE"

    terminal = _spawn_fanout_recovery(
        context,
        db_path=db_path,
        result_path=tmp_path / "fanout-terminal.json",
        owner_id="fanout-process-owner-d-terminal",
        advance_ms=_RECOVERY_ADVANCE_MS * 2,
        public_key=public_key,
    )
    assert terminal["report"] == []
    assert terminal["worker_calls"] == []
    assert terminal["projection_count"] == 3
