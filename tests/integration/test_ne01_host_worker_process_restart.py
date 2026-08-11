from __future__ import annotations

import asyncio
import json
import multiprocessing
import os
from pathlib import Path
from typing import Any

import pytest

from host_brain.agent_runtime.runtime_router import CanonicalRuntimeRouter
from host_brain.app.governed_task_adapter import GovernedHostRuntime
from host_brain.orchestration import GovernedCommandApplication
from host_brain.persistence.host_command_repository import HostCommandRepository
from host_brain.persistence.task_repository import DurableTaskStore
from host_brain.persistence.worker_projection_repository import (
    WorkerProjectionStore,
)
from host_brain.task_runtime import AdmittedExecution
from tests.integration.test_gr03a_governed_durable_dag import (
    NOW,
    _migrated_db,
)
from tests.integration.test_ne01_operation_online_takeover import (
    _Clock,
    _ProjectedWorker,
    _ProductionRouteResolver,
    _coordinator,
    _contracts,
    _events,
)


_PREOPERATION_EXIT_CODE = 91
_CHECKPOINT_EXIT_CODE = 92
_MULTINODE_PREOPERATION_EXIT_CODE = 93
_LEASE_TTL_MS = 20
_RECOVERY_ADVANCE_MS = 25


class _ExitBeforeOperationResolver:
    """Terminate after durable claim/precharge but before Operation reserve."""

    def __init__(self, marker_path: str) -> None:
        self._marker_path = Path(marker_path)

    def resolve(self, *, executor: str, worker_route: str) -> Any:
        assert executor == "canonical"
        assert worker_route == "chat:root"
        self._marker_path.write_text(
            json.dumps(
                {
                    "phase": "PREOPERATION",
                    "pid": os.getpid(),
                    "worker_route": worker_route,
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        os._exit(_PREOPERATION_EXIT_CODE)


class _ExitSelectedBeforeOperationResolver:
    """Run predecessors normally, then terminate at one selected node."""

    def __init__(
        self,
        *,
        delegate: Any,
        marker_path: str,
        selected_route: str,
    ) -> None:
        self._delegate = delegate
        self._marker_path = Path(marker_path)
        self._selected_route = str(selected_route)

    def resolve(self, *, executor: str, worker_route: str) -> Any:
        if worker_route != self._selected_route:
            return self._delegate.resolve(
                executor=executor,
                worker_route=worker_route,
            )
        self._marker_path.write_text(
            json.dumps(
                {
                    "phase": "MULTINODE_PREOPERATION",
                    "pid": os.getpid(),
                    "worker_route": worker_route,
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        os._exit(_MULTINODE_PREOPERATION_EXIT_CODE)


class _ExitBeforeAttemptSuccessStore:
    """Terminate after Operation/Budget commit but before Attempt success."""

    def __init__(
        self,
        delegate: DurableTaskStore,
        marker_path: str,
        reservation_id: str,
    ) -> None:
        self._delegate = delegate
        self._marker_path = Path(marker_path)
        self._reservation_id = str(reservation_id)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)

    def transition_attempt(
        self,
        attempt_id: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        if kwargs.get("target_status") == "SUCCEEDED":
            reservation = self._delegate.get_operation_reservation(
                self._reservation_id
            )
            self._marker_path.write_text(
                json.dumps(
                    {
                        "phase": "COMMITTED_CHECKPOINT",
                        "pid": os.getpid(),
                        "attempt_id": attempt_id,
                        "reservation_status": reservation["status"],
                        "receipt_ref": reservation["receipt_ref"],
                    },
                    sort_keys=True,
                ),
                encoding="utf-8",
            )
            os._exit(_CHECKPOINT_EXIT_CODE)
        return self._delegate.transition_attempt(attempt_id, **kwargs)


def _crash_runtime_process(
    db_path: str,
    marker_path: str,
    crash_phase: str,
) -> None:
    asyncio.run(
        _crash_runtime(
            db_path=Path(db_path),
            marker_path=marker_path,
            crash_phase=crash_phase,
        )
    )


async def _crash_runtime(
    *,
    db_path: Path,
    marker_path: str,
    crash_phase: str,
) -> None:
    specs = (
        (("root", ()), ("child", ("root",)))
        if crash_phase == "MULTINODE_PREOPERATION"
        else (("root", ()),)
    )
    contracts = _contracts(specs)
    admitted = AdmittedExecution.from_contracts(
        command=contracts[0],
        assignment=contracts[2],
        graph=contracts[3],
    )
    clock = _Clock()
    durable = DurableTaskStore(db_path, clock=clock)
    projections = WorkerProjectionStore(db_path, clock=clock)
    worker = _ProjectedWorker(projections=projections, calls=[])
    task_store: Any = durable
    resolver: Any = _ProductionRouteResolver(worker)
    exit_code = 99
    if crash_phase == "PREOPERATION":
        resolver = _ExitBeforeOperationResolver(marker_path)
        exit_code = _PREOPERATION_EXIT_CODE
    elif crash_phase == "MULTINODE_PREOPERATION":
        resolver = _ExitSelectedBeforeOperationResolver(
            delegate=resolver,
            marker_path=marker_path,
            selected_route="chat:child",
        )
        exit_code = _MULTINODE_PREOPERATION_EXIT_CODE
    elif crash_phase == "COMMITTED_CHECKPOINT":
        task_store = _ExitBeforeAttemptSuccessStore(
            durable,
            marker_path,
            admitted.reservation_id,
        )
        exit_code = _CHECKPOINT_EXIT_CODE
    else:  # pragma: no cover - target contract guard
        raise ValueError(f"unsupported crash phase: {crash_phase}")
    application = GovernedCommandApplication(
        command_repository=HostCommandRepository(db_path, clock=clock),
        coordinator=_coordinator(
            store=task_store,
            projections=projections,
            worker=worker,
            worker_resolver=resolver,
            owner_id=f"process-owner-a-{crash_phase.lower()}",
            lease_ttl_ms=_LEASE_TTL_MS,
            bounded_fanout_enabled=(len(specs) > 1),
        ),
    )
    await _events(application, contracts)
    os._exit(exit_code)


def _recover_runtime_process(
    db_path: str,
    result_path: str,
    owner_id: str,
) -> None:
    asyncio.run(
        _recover_runtime(
            db_path=Path(db_path),
            result_path=Path(result_path),
            owner_id=owner_id,
        )
    )


async def _recover_runtime(
    *,
    db_path: Path,
    result_path: Path,
    owner_id: str,
) -> None:
    clock = _Clock()
    clock.advance(_RECOVERY_ADVANCE_MS)
    durable = DurableTaskStore(db_path, clock=clock)
    commands = HostCommandRepository(db_path, clock=clock)
    projections = WorkerProjectionStore(db_path, clock=clock)
    worker_calls: list[str] = []
    worker = _ProjectedWorker(
        projections=projections,
        calls=worker_calls,
    )
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
    result_path.write_text(
        json.dumps(
            {
                "pid": os.getpid(),
                "owner_id": owner_id,
                "report": list(report),
                "worker_calls": worker_calls,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )


def _run_process(
    context: multiprocessing.context.SpawnContext,
    *,
    target: Any,
    args: tuple[Any, ...],
    expected_exit_code: int,
) -> None:
    process = context.Process(target=target, args=args)
    process.start()
    process.join(timeout=30)
    if process.is_alive():
        process.terminate()
        process.join(timeout=5)
        pytest.fail("restart integration child process did not terminate")
    assert process.exitcode == expected_exit_code


def _operation_rows(
    store: DurableTaskStore,
    *,
    attempt_id: str,
) -> list[dict[str, Any]]:
    with store._connect() as conn:
        return [
            dict(row)
            for row in conn.execute(
                """
                SELECT reservation_id, attempt_id, lease_id, owner_id,
                       fencing_token, status, status_version, receipt_ref
                  FROM task_runtime_idempotency_reservation
                 WHERE attempt_id = ?
                 ORDER BY reservation_id
                """,
                (attempt_id,),
            ).fetchall()
        ]


def _lease_lineage(
    store: DurableTaskStore,
    *,
    attempt_id: str,
) -> list[tuple[str, int, str]]:
    with store._connect() as conn:
        return [
            (str(row[0]), int(row[1]), str(row[2]))
            for row in conn.execute(
                """
                SELECT owner_id, fencing_token, status
                  FROM task_runtime_lease
                 WHERE attempt_id = ?
                 ORDER BY fencing_token
                """,
                (attempt_id,),
            ).fetchall()
        ]


def _spawn_recovery(
    context: multiprocessing.context.SpawnContext,
    *,
    db_path: Path,
    result_path: Path,
    owner_id: str,
) -> dict[str, Any]:
    _run_process(
        context,
        target=_recover_runtime_process,
        args=(str(db_path), str(result_path), owner_id),
        expected_exit_code=0,
    )
    return json.loads(result_path.read_text(encoding="utf-8"))


def test_host_restart_takes_over_preoperation_attempt_in_new_process(
    tmp_path: Path,
) -> None:
    db_path = _migrated_db(tmp_path)
    marker_path = tmp_path / "preoperation-crash.json"
    context = multiprocessing.get_context("spawn")
    _run_process(
        context,
        target=_crash_runtime_process,
        args=(str(db_path), str(marker_path), "PREOPERATION"),
        expected_exit_code=_PREOPERATION_EXIT_CODE,
    )
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    assert marker["phase"] == "PREOPERATION"

    before_clock = _Clock()
    before = DurableTaskStore(db_path, clock=before_clock)
    held = before.read_scheduler_snapshot("graph-gr03a", 1)
    assert held["graph_record"]["status"] == "ACTIVE"
    assert len(held["attempts"]) == 1
    original_attempt = dict(held["attempts"][0])
    original_lease = dict(held["active_leases"][0])
    assert original_attempt["status"] == "RUNNING"
    assert before.get_node("graph-gr03a", 1, "root")["status"] == "RUNNING"
    assert _operation_rows(
        before,
        attempt_id=original_attempt["attempt_id"],
    ) == []

    recovered = _spawn_recovery(
        context,
        db_path=db_path,
        result_path=tmp_path / "preoperation-recovered.json",
        owner_id="process-owner-b-preoperation",
    )
    assert recovered["report"] == [
        {
            "graph_id": "graph-gr03a",
            "graph_revision": 1,
            "reason_code": None,
            "status": "COMPLETED",
        }
    ]
    assert recovered["worker_calls"] == ["root"]
    assert recovered["pid"] != marker["pid"]

    final_clock = _Clock()
    final_clock.advance(_RECOVERY_ADVANCE_MS)
    final = DurableTaskStore(db_path, clock=final_clock)
    settled = final.read_scheduler_snapshot("graph-gr03a", 1)
    assert settled["graph_record"]["status"] == "COMPLETED"
    assert len(settled["attempts"]) == 1
    assert settled["attempts"][0]["attempt_id"] == original_attempt["attempt_id"]
    assert settled["attempts"][0]["status"] == "SUCCEEDED"
    assert final.get_node("graph-gr03a", 1, "root")["status"] == "ACCEPTED"
    assert _lease_lineage(
        final,
        attempt_id=original_attempt["attempt_id"],
    ) == [
        (
            str(original_lease["owner_id"]),
            int(original_lease["fencing_token"]),
            "EXPIRED",
        ),
        (
            "process-owner-b-preoperation",
            int(original_lease["fencing_token"]) + 1,
            "RELEASED",
        ),
    ]
    operation_rows = _operation_rows(
        final,
        attempt_id=original_attempt["attempt_id"],
    )
    assert len(operation_rows) == 1
    assert operation_rows[0]["status"] == "COMMITTED"
    assert operation_rows[0]["owner_id"] == "process-owner-b-preoperation"

    replay = _spawn_recovery(
        context,
        db_path=db_path,
        result_path=tmp_path / "preoperation-replay.json",
        owner_id="process-owner-c-preoperation",
    )
    assert replay["report"] == []
    assert replay["worker_calls"] == []


def test_host_restart_resumes_committed_checkpoint_without_worker_replay(
    tmp_path: Path,
) -> None:
    db_path = _migrated_db(tmp_path)
    marker_path = tmp_path / "checkpoint-crash.json"
    context = multiprocessing.get_context("spawn")
    _run_process(
        context,
        target=_crash_runtime_process,
        args=(str(db_path), str(marker_path), "COMMITTED_CHECKPOINT"),
        expected_exit_code=_CHECKPOINT_EXIT_CODE,
    )
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    assert marker["phase"] == "COMMITTED_CHECKPOINT"
    assert marker["reservation_status"] == "COMMITTED"

    before_clock = _Clock()
    before = DurableTaskStore(db_path, clock=before_clock)
    held = before.read_scheduler_snapshot("graph-gr03a", 1)
    assert held["graph_record"]["status"] == "ACTIVE"
    assert len(held["attempts"]) == 1
    original_attempt = dict(held["attempts"][0])
    original_lease = dict(held["active_leases"][0])
    assert original_attempt["status"] == "RUNNING"
    operation_rows = _operation_rows(
        before,
        attempt_id=original_attempt["attempt_id"],
    )
    assert len(operation_rows) == 1
    assert operation_rows[0]["status"] == "COMMITTED"
    assert operation_rows[0]["receipt_ref"] == marker["receipt_ref"]

    recovered = _spawn_recovery(
        context,
        db_path=db_path,
        result_path=tmp_path / "checkpoint-recovered.json",
        owner_id="process-owner-b-checkpoint",
    )
    assert recovered["report"] == [
        {
            "graph_id": "graph-gr03a",
            "graph_revision": 1,
            "reason_code": None,
            "status": "COMPLETED",
        }
    ]
    assert recovered["worker_calls"] == []
    assert recovered["pid"] != marker["pid"]

    final_clock = _Clock()
    final_clock.advance(_RECOVERY_ADVANCE_MS)
    final = DurableTaskStore(db_path, clock=final_clock)
    settled = final.read_scheduler_snapshot("graph-gr03a", 1)
    assert settled["graph_record"]["status"] == "COMPLETED"
    assert len(settled["attempts"]) == 1
    assert settled["attempts"][0]["attempt_id"] == original_attempt["attempt_id"]
    assert settled["attempts"][0]["status"] == "SUCCEEDED"
    assert final.get_node("graph-gr03a", 1, "root")["status"] == "ACCEPTED"
    assert _lease_lineage(
        final,
        attempt_id=original_attempt["attempt_id"],
    ) == [
        (
            str(original_lease["owner_id"]),
            int(original_lease["fencing_token"]),
            "EXPIRED",
        ),
        (
            "process-owner-b-checkpoint",
            int(original_lease["fencing_token"]) + 1,
            "RELEASED",
        ),
    ]
    final_operation = _operation_rows(
        final,
        attempt_id=original_attempt["attempt_id"],
    )
    assert len(final_operation) == 1
    assert final_operation[0]["status"] == "COMMITTED"
    assert final_operation[0]["receipt_ref"] == marker["receipt_ref"]
    assert final.get_node_verification_decision(
        "graph-gr03a",
        1,
        "root",
    )["verification_result"]["status"] == "PASS"
    assert final.get_node_acceptance_decision(
        "graph-gr03a",
        1,
        "root",
    )["judgement"]["decision"] == "ACCEPT"

    replay = _spawn_recovery(
        context,
        db_path=db_path,
        result_path=tmp_path / "checkpoint-replay.json",
        owner_id="process-owner-c-checkpoint",
    )
    assert replay["report"] == []
    assert replay["worker_calls"] == []


def test_host_restart_preserves_accepted_predecessor_and_recovers_child(
    tmp_path: Path,
) -> None:
    db_path = _migrated_db(tmp_path)
    marker_path = tmp_path / "multinode-preoperation-crash.json"
    context = multiprocessing.get_context("spawn")
    _run_process(
        context,
        target=_crash_runtime_process,
        args=(str(db_path), str(marker_path), "MULTINODE_PREOPERATION"),
        expected_exit_code=_MULTINODE_PREOPERATION_EXIT_CODE,
    )
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    assert marker["phase"] == "MULTINODE_PREOPERATION"
    assert marker["worker_route"] == "chat:child"

    before_clock = _Clock()
    before = DurableTaskStore(db_path, clock=before_clock)
    held = before.read_scheduler_snapshot("graph-gr03a", 1)
    attempts_by_node = {
        str(attempt["node_id"]): dict(attempt)
        for attempt in held["attempts"]
    }
    assert held["graph_record"]["status"] == "ACTIVE"
    assert before.get_node("graph-gr03a", 1, "root")["status"] == "ACCEPTED"
    assert before.get_node("graph-gr03a", 1, "child")["status"] == "RUNNING"
    assert set(attempts_by_node) == {"root", "child"}
    assert attempts_by_node["root"]["status"] == "SUCCEEDED"
    assert attempts_by_node["child"]["status"] == "RUNNING"
    root_artifact = dict(
        before.get_node_artifact("graph-gr03a", 1, "root")
    )
    child_lease = next(
        dict(lease)
        for lease in held["active_leases"]
        if lease["attempt_id"] == attempts_by_node["child"]["attempt_id"]
    )
    assert _operation_rows(
        before,
        attempt_id=attempts_by_node["child"]["attempt_id"],
    ) == []

    recovered = _spawn_recovery(
        context,
        db_path=db_path,
        result_path=tmp_path / "multinode-recovered.json",
        owner_id="process-owner-b-multinode",
    )
    assert recovered["report"] == [
        {
            "graph_id": "graph-gr03a",
            "graph_revision": 1,
            "reason_code": None,
            "status": "COMPLETED",
        }
    ]
    assert recovered["worker_calls"] == ["child"]
    assert recovered["pid"] != marker["pid"]

    final_clock = _Clock()
    final_clock.advance(_RECOVERY_ADVANCE_MS)
    final = DurableTaskStore(db_path, clock=final_clock)
    settled = final.read_scheduler_snapshot("graph-gr03a", 1)
    final_attempts = {
        str(attempt["node_id"]): dict(attempt)
        for attempt in settled["attempts"]
    }
    assert settled["graph_record"]["status"] == "COMPLETED"
    assert set(final_attempts) == {"root", "child"}
    assert final_attempts["root"]["attempt_id"] == (
        attempts_by_node["root"]["attempt_id"]
    )
    assert final_attempts["child"]["attempt_id"] == (
        attempts_by_node["child"]["attempt_id"]
    )
    assert final_attempts["root"]["status"] == "SUCCEEDED"
    assert final_attempts["child"]["status"] == "SUCCEEDED"
    assert final.get_node("graph-gr03a", 1, "root")["status"] == "ACCEPTED"
    assert final.get_node("graph-gr03a", 1, "child")["status"] == "ACCEPTED"
    assert dict(final.get_node_artifact("graph-gr03a", 1, "root")) == (
        root_artifact
    )
    assert _lease_lineage(
        final,
        attempt_id=attempts_by_node["child"]["attempt_id"],
    ) == [
        (
            str(child_lease["owner_id"]),
            int(child_lease["fencing_token"]),
            "EXPIRED",
        ),
        (
            "process-owner-b-multinode",
            int(child_lease["fencing_token"]) + 1,
            "RELEASED",
        ),
    ]
    child_operation = _operation_rows(
        final,
        attempt_id=attempts_by_node["child"]["attempt_id"],
    )
    assert len(child_operation) == 1
    assert child_operation[0]["status"] == "COMMITTED"
    assert child_operation[0]["owner_id"] == "process-owner-b-multinode"

    replay = _spawn_recovery(
        context,
        db_path=db_path,
        result_path=tmp_path / "multinode-replay.json",
        owner_id="process-owner-c-multinode",
    )
    assert replay["report"] == []
    assert replay["worker_calls"] == []
