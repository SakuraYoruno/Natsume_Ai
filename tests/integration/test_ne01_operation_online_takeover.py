from __future__ import annotations

import asyncio
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import threading
from typing import Any

import pytest

from host_brain.contracts.agent_runtime.budget_policy import BudgetAmount
from host_brain.orchestration import GovernedCommandApplication
from host_brain.persistence.host_command_repository import HostCommandRepository
from host_brain.persistence.internal_agent_checkpoint_uow import (
    DurableInternalAgentCheckpointUnitOfWork,
)
from host_brain.persistence.task_repository import (
    DurableTaskStore,
    OutboxMessage,
    TaskStoreError,
)
from host_brain.persistence.worker_projection_repository import (
    WORKER_PROJECTION_SCHEMA_VERSION,
    WorkerProjectionStore,
)
from host_brain.task_runtime import (
    AdmittedExecution,
    GovernedRuntimeError,
    GovernedTaskCoordinator,
)
from tests.integration.test_gr03a_governed_durable_dag import (
    NOW,
    _Acceptance,
    _FailingHeartbeatStore,
    _Publisher,
    _Verifier,
    _contracts as _fixture_contracts,
    _migrated_db,
)


class _Clock:
    def __init__(self) -> None:
        self.value = NOW

    def __call__(self) -> int:
        return self.value

    def advance(self, milliseconds: int) -> None:
        self.value += int(milliseconds)


def _contracts(*args: Any, **kwargs: Any):
    """Use only production-supported TaskNode route kinds in this slice."""

    command, work_order, assignment, graph = _fixture_contracts(*args, **kwargs)
    nodes = tuple(
        replace(node, route=f"chat:{node.node_id}")
        for node in graph.nodes
    )
    return command, work_order, assignment, replace(graph, nodes=nodes)


class _ProductionRouteResolver:
    def __init__(self, worker: Any) -> None:
        self._worker = worker

    def resolve(self, *, executor: str, worker_route: str) -> Any:
        assert executor == "canonical"
        route_kind, separator, route_target = str(worker_route).partition(":")
        assert route_kind in {"chat", "skill", "delegate", "tool"}
        assert separator and route_target
        return self._worker


class _ProjectedWorker:
    def __init__(
        self,
        *,
        projections: WorkerProjectionStore,
        calls: list[str],
        delay_seconds: float = 0.0,
        token_counts: dict[str, int] | None = None,
    ) -> None:
        self._projections = projections
        self._calls = calls
        self._delay_seconds = float(delay_seconds)
        self._token_counts = dict(token_counts or {})

    async def execute_stream(
        self,
        *,
        command,
        execution_context: dict[str, Any],
        **_: Any,
    ):
        node_id = str(execution_context["node_id"])
        attempt_id = str(execution_context["attempt_id"])
        self._calls.append(node_id)
        if self._delay_seconds:
            await asyncio.sleep(self._delay_seconds)
        route_kind, _, route_target = str(
            execution_context.get("worker_route") or ""
        ).partition(":")
        if route_kind not in {"chat", "skill", "delegate"}:
            route_kind = str(
                getattr(command.route_type, "value", command.route_type) or ""
            ).lower()
            route_target = command.target_id
        body = {
            "phase": "COMMIT",
            "done": True,
            "decision": "APPROVE",
            "reason_code": None,
            "command_id": command.command_id,
            "request_id": command.request_id,
            "narrative": f"durable checkpoint for {node_id}",
            "route_type": route_kind,
            "target_id": route_target or command.target_id,
            "graph_id": str(execution_context["graph_id"]),
            "graph_revision": int(execution_context["graph_revision"]),
            "node_id": node_id,
            "attempt_id": attempt_id,
            "node_grant_ref": f"governor-grant://checkpoint/{node_id}",
            "audit_refs": {"checkpoint": f"audit://checkpoint/{attempt_id}"},
            "budget_usage": {
                "schema_version": "Natsume.task_runtime.worker_budget_usage.v1",
                "usage_id": f"worker-usage://{node_id}/online-takeover",
                "measurements": {
                    "token_count": self._token_counts.get(node_id, 10),
                    "retry_count": 0,
                    "model_cost_micros": 25,
                },
                "evidence_refs": [f"model-budget://{node_id}/online-takeover"],
            },
        }
        encoded = json.dumps(
            body,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        digest = "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        projection_id = (
            "worker-projection://online-takeover/"
            f"{digest.removeprefix('sha256:')}"
        )
        self._projections.stage(
            projection_id=projection_id,
            projection={
                **body,
                "projection_id": projection_id,
                "payload_digest": digest,
                "schema_version": WORKER_PROJECTION_SCHEMA_VERSION,
            },
        )
        yield {**body, "receipt_ref": projection_id}


class _CommitFailureStore:
    def __init__(self, delegate: DurableTaskStore, failure: BaseException) -> None:
        self._delegate = delegate
        self._failure = failure
        self.commit_calls = 0

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)

    def commit_operation(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        self.commit_calls += 1
        result = self._delegate.commit_operation(*args, **kwargs)
        raise self._failure


class _SelectedCommitFailureStore(_CommitFailureStore):
    def __init__(
        self,
        delegate: DurableTaskStore,
        failure: BaseException,
        *,
        reservation_id: str,
    ) -> None:
        super().__init__(delegate, failure)
        self._reservation_id = reservation_id
        self._failed = False

    def commit_operation(self, reservation_id: str, **kwargs: Any) -> dict[str, Any]:
        if reservation_id != self._reservation_id or self._failed:
            return self._delegate.commit_operation(reservation_id, **kwargs)
        self.commit_calls += 1
        self._failed = True
        self._delegate.commit_operation(reservation_id, **kwargs)
        raise self._failure


class _TimedCommitFailureStore(_CommitFailureStore):
    def __init__(
        self,
        delegate: DurableTaskStore,
        failure: BaseException,
        *,
        clock: _Clock,
        elapsed_ms: int,
    ) -> None:
        super().__init__(delegate, failure)
        self._clock = clock
        self._elapsed_ms = int(elapsed_ms)

    def commit_operation(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        self._clock.advance(self._elapsed_ms)
        return super().commit_operation(*args, **kwargs)


class _BudgetResponseLossStore:
    def __init__(self, delegate: DurableTaskStore) -> None:
        self._delegate = delegate
        self.failed = False

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)

    def consume_budget_once(self, *args: Any, **kwargs: Any) -> Any:
        result = self._delegate.consume_budget_once(*args, **kwargs)
        if (
            not self.failed
            and str(kwargs.get("charge_kind") or "").startswith("WORKER_USAGE")
        ):
            self.failed = True
            raise TimeoutError("simulated BudgetUsage response loss")
        return result


class _BudgetSettlementDoubleLossStore:
    """Lose the write response and the first exact usage read-back."""

    def __init__(
        self,
        delegate: DurableTaskStore,
        *,
        commit_before_loss: bool,
        clock: _Clock,
        durable_elapsed_ms: int = 7,
        write_failure_type: type[BaseException] = TimeoutError,
        read_failure_type: type[BaseException] | None = TimeoutError,
    ) -> None:
        self._delegate = delegate
        self._commit_before_loss = bool(commit_before_loss)
        self._clock = clock
        self._durable_elapsed_ms = int(durable_elapsed_ms)
        self._write_failure_type = write_failure_type
        self._read_failure_type = read_failure_type
        self._commit_advanced = False
        self.consume_failed = False
        self.read_failed = False

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)

    def commit_operation(self, *args: Any, **kwargs: Any) -> Any:
        if not self._commit_advanced:
            self._commit_advanced = True
            self._clock.advance(self._durable_elapsed_ms)
        return self._delegate.commit_operation(*args, **kwargs)

    def consume_budget_once(self, *args: Any, **kwargs: Any) -> Any:
        if (
            not self.consume_failed
            and str(kwargs.get("charge_kind") or "").startswith("WORKER_USAGE")
        ):
            self.consume_failed = True
            if self._commit_before_loss:
                self._delegate.consume_budget_once(*args, **kwargs)
            raise self._write_failure_type(
                "simulated worker BudgetUsage response loss"
            )
        return self._delegate.consume_budget_once(*args, **kwargs)

    def get_budget_usage(self, *args: Any, **kwargs: Any) -> Any:
        if (
            self.consume_failed
            and not self.read_failed
            and self._read_failure_type is not None
        ):
            self.read_failed = True
            raise self._read_failure_type(
                "simulated first BudgetUsage read-back loss"
            )
        return self._delegate.get_budget_usage(*args, **kwargs)


class _CheckpointBudgetRejectionResponseLossUow:
    """Lose the generic atomic closeout response, then reopen it read-only."""

    def __init__(self, delegate: DurableInternalAgentCheckpointUnitOfWork) -> None:
        self._delegate = delegate
        self.mutation_calls = 0
        self.reopen_calls = 0

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)

    def reject_committed_checkpoint_budget_bundle(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        self.mutation_calls += 1
        self._delegate.reject_committed_checkpoint_budget_bundle(*args, **kwargs)
        raise TimeoutError("simulated atomic budget closeout response loss")

    def reopen_committed_checkpoint_budget_rejection_terminal(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        self.reopen_calls += 1
        return self._delegate.reopen_committed_checkpoint_budget_rejection_terminal(
            *args,
            **kwargs,
        )


class _BarrierAcquireStore:
    def __init__(
        self,
        delegate: DurableTaskStore,
        barrier: threading.Barrier,
    ) -> None:
        self._delegate = delegate
        self._barrier = barrier

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)

    def acquire_lease(self, *args: Any, **kwargs: Any) -> Any:
        self._barrier.wait(timeout=5.0)
        return self._delegate.acquire_lease(*args, **kwargs)


class _LeaseAcquireResponseLossStore:
    """Commit one takeover Lease, then lose only its first response."""

    def __init__(self, delegate: DurableTaskStore) -> None:
        self._delegate = delegate
        self.acquire_calls = 0
        self.response_lost = False

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)

    def acquire_lease(self, *args: Any, **kwargs: Any) -> Any:
        self.acquire_calls += 1
        result = self._delegate.acquire_lease(*args, **kwargs)
        is_takeover = bool(
            kwargs.get("absent_operation_reservation_id")
            or kwargs.get("existing_operation_reservation_id")
        )
        if is_takeover and not self.response_lost:
            self.response_lost = True
            raise TimeoutError("simulated takeover Lease response loss")
        return result


class _StopBeforeAttemptSuccessStore:
    def __init__(self, delegate: DurableTaskStore, failure: BaseException) -> None:
        self._delegate = delegate
        self._failure = failure
        self.stopped = False

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)

    def transition_attempt(self, attempt_id: str, **kwargs: Any) -> dict[str, Any]:
        if kwargs.get("target_status") == "SUCCEEDED" and not self.stopped:
            self.stopped = True
            raise self._failure
        return self._delegate.transition_attempt(attempt_id, **kwargs)


class _ReserveBoundaryStopStore:
    """Stop exactly at the Operation reservation durability boundary."""

    def __init__(
        self,
        delegate: DurableTaskStore,
        failure: BaseException,
        *,
        commit_before_stop: bool,
        reservation_id: str | None = None,
    ) -> None:
        self._delegate = delegate
        self._failure = failure
        self._commit_before_stop = bool(commit_before_stop)
        self._reservation_id = reservation_id
        self.reserve_calls = 0
        self.stopped = False

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)

    def reserve_operation(self, *args: Any, **kwargs: Any) -> Any:
        reservation_id = str(
            kwargs.get("reservation_id")
            or (args[0] if args else "")
        )
        if (
            self.stopped
            or (
                self._reservation_id is not None
                and reservation_id != self._reservation_id
            )
        ):
            return self._delegate.reserve_operation(*args, **kwargs)
        self.reserve_calls += 1
        self.stopped = True
        if self._commit_before_stop:
            self._delegate.reserve_operation(*args, **kwargs)
        raise self._failure


class _SiblingStateBoundaryStopStore:
    """Stop one node before Operation only after a parallel sibling is durable."""

    def __init__(
        self,
        delegate: DurableTaskStore,
        failure: BaseException,
        *,
        reservation_id: str,
        sibling_node_id: str,
        release_on: str,
    ) -> None:
        self._delegate = delegate
        self._failure = failure
        self._reservation_id = reservation_id
        self._sibling_node_id = sibling_node_id
        self._release_on = release_on
        self._sibling_durable = asyncio.Event()
        self._sibling_budget_exceeded = False
        self.stopped = False

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)

    async def reserve_operation(self, *args: Any, **kwargs: Any) -> Any:
        reservation_id = str(
            kwargs.get("reservation_id")
            or (args[0] if args else "")
        )
        if reservation_id != self._reservation_id or self.stopped:
            return self._delegate.reserve_operation(*args, **kwargs)
        await asyncio.wait_for(self._sibling_durable.wait(), timeout=5.0)
        self.stopped = True
        raise self._failure

    def commit_node_acceptance(
        self,
        graph_id: str,
        graph_revision: int,
        node_id: str,
        **kwargs: Any,
    ) -> Any:
        result = self._delegate.commit_node_acceptance(
            graph_id,
            graph_revision,
            node_id,
            **kwargs,
        )
        if (
            self._release_on == "ACCEPTED"
            and node_id == self._sibling_node_id
        ):
            self._sibling_durable.set()
        return result

    def consume_budget_once(self, *args: Any, **kwargs: Any) -> Any:
        result = self._delegate.consume_budget_once(*args, **kwargs)
        if (
            self._release_on == "BUDGET_EXCEEDED"
            and kwargs.get("node_id") == self._sibling_node_id
            and str(kwargs.get("charge_kind") or "").startswith("WORKER_USAGE")
            and result.accepted is False
        ):
            self._sibling_budget_exceeded = True
        return result

    def fail_running_attempt_node_bundle(
        self,
        attempt_id: str,
        **kwargs: Any,
    ) -> Any:
        result = self._delegate.fail_running_attempt_node_bundle(
            attempt_id,
            **kwargs,
        )
        node = result.get("node") if isinstance(result, dict) else None
        if (
            self._release_on == "FAILED"
            and isinstance(node, dict)
            and node.get("node_id") == self._sibling_node_id
            and node.get("status") == "FAILED"
        ):
            self._sibling_durable.set()
        return result

    def transition_node(
        self,
        graph_id: str,
        graph_revision: int,
        node_id: str,
        **kwargs: Any,
    ) -> Any:
        result = self._delegate.transition_node(
            graph_id,
            graph_revision,
            node_id,
            **kwargs,
        )
        if (
            self._release_on == "WAITING_ACCEPTANCE_REVIEW"
            and node_id == self._sibling_node_id
            and result.get("status") == "WAITING_ACCEPTANCE_REVIEW"
        ):
            self._sibling_durable.set()
        if (
            self._release_on == "BUDGET_EXCEEDED"
            and self._sibling_budget_exceeded
            and node_id == self._sibling_node_id
            and result.get("status") == "FAILED"
        ):
            self._sibling_durable.set()
        return result


class _PreReservationTerminalStopStore:
    """Stop after a rejected PRECHARGE but before terminal closeout."""

    def __init__(self, delegate: DurableTaskStore, failure: BaseException) -> None:
        self._delegate = delegate
        self._failure = failure
        self.stopped = False

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)

    def fail_running_attempt_bundle(self, *args: Any, **kwargs: Any) -> Any:
        if not self.stopped:
            self.stopped = True
            raise self._failure
        return self._delegate.fail_running_attempt_bundle(*args, **kwargs)


class _ReservationAwareProjectedWorker(_ProjectedWorker):
    """Record the durable Operation row observed at the worker boundary."""

    def __init__(
        self,
        *,
        projections: WorkerProjectionStore,
        calls: list[str],
        task_store: DurableTaskStore,
        starts: list[dict[str, Any]],
        delay_seconds: float = 0.0,
    ) -> None:
        super().__init__(
            projections=projections,
            calls=calls,
            delay_seconds=delay_seconds,
        )
        self._task_store = task_store
        self._starts = starts

    async def execute_stream(
        self,
        *,
        execution_context: dict[str, Any],
        **kwargs: Any,
    ):
        reservation = self._task_store.get_operation_reservation(
            str(execution_context["reservation_id"])
        )
        self._starts.append(
            {
                "reservation_id": reservation["reservation_id"],
                "reservation_status": reservation["status"],
                "attempt_id": reservation["attempt_id"],
                "owner_id": reservation["owner_id"],
                "fencing_token": reservation["fencing_token"],
            }
        )
        async for event in super().execute_stream(
            execution_context=execution_context,
            **kwargs,
        ):
            yield event


class _SelectedPreparationFailureResolver(_ProductionRouteResolver):
    def __init__(self, worker: Any, *, failed_node_id: str) -> None:
        super().__init__(worker)
        self._failed_route = f"chat:{failed_node_id}"

    def resolve(self, *, executor: str, worker_route: str) -> Any:
        if worker_route == self._failed_route:
            raise RuntimeError("selected sibling preparation failed")
        return super().resolve(executor=executor, worker_route=worker_route)


class _ProcessStopped(BaseException):
    pass


_TERMINAL_LIFECYCLE_OUTBOX_EVENTS = frozenset(
    {
        "TASK_ATTEMPT_SUCCEEDED",
        "TASK_ATTEMPT_FAILED",
        "TASK_ATTEMPT_CANCELLED",
        "TASK_NODE_EXECUTED",
        "TASK_NODE_VERIFYING",
        "TASK_NODE_VERIFIED",
        "TASK_NODE_ACCEPTED",
        "TASK_NODE_FAILED",
        "TASK_NODE_CANCELLED",
        "TASK_GRAPH_COMPLETED",
        "TASK_GRAPH_FAILED",
        "TASK_GRAPH_BLOCKED",
        "TASK_GRAPH_CANCELLED",
        "TASK_LEASE_RELEASED",
        "TASK_LEASE_RELEASED_TERMINAL",
        "TASK_LEASE_RELEASED_RECOVERY",
        "BUDGET_RESERVATION_CONSUMED",
        "BUDGET_RESERVATION_RELEASED",
    }
)


def _terminal_lifecycle_outbox_events(store: DurableTaskStore) -> set[str]:
    with store._connect() as conn:
        persisted = {
            str(row[0])
            for row in conn.execute(
                "SELECT event_type FROM task_runtime_outbox"
            ).fetchall()
        }
    # BUDGET_CONSUMED / BUDGET_EXCEEDED are intentionally not terminal here:
    # the exact worker usage may have committed before its response was lost.
    return persisted & _TERMINAL_LIFECYCLE_OUTBOX_EVENTS


def _operation_commit_outbox_count(store: DurableTaskStore) -> int:
    with store._connect() as conn:
        return int(
            conn.execute(
                "SELECT COUNT(*) FROM task_runtime_outbox "
                "WHERE event_type = 'OPERATION_COMMITTED'"
            ).fetchone()[0]
        )


def _lease_lineage(
    store: DurableTaskStore,
    *,
    attempt_id: str,
) -> list[tuple[str, int, str]]:
    with store._connect() as conn:
        rows = conn.execute(
            "SELECT owner_id, fencing_token, status FROM task_runtime_lease "
            "WHERE attempt_id = ? ORDER BY fencing_token",
            (attempt_id,),
        ).fetchall()
    return [(str(row[0]), int(row[1]), str(row[2])) for row in rows]


def _operation_rows_for_attempt(
    store: DurableTaskStore,
    *,
    attempt_id: str,
) -> list[dict[str, Any]]:
    with store._connect() as conn:
        rows = conn.execute(
            "SELECT reservation_id, attempt_id, lease_id, owner_id, "
            "fencing_token, status, status_version, receipt_ref "
            "FROM task_runtime_idempotency_reservation "
            "WHERE attempt_id = ? ORDER BY reservation_id",
            (attempt_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def _outbox_rows_for_attempt(
    store: DurableTaskStore,
    *,
    attempt_id: str,
) -> list[dict[str, Any]]:
    with store._connect() as conn:
        rows = conn.execute(
            "SELECT * FROM task_runtime_outbox "
            "WHERE attempt_id = ? ORDER BY created_ts_ms, outbox_id",
            (attempt_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def _accepted_node_snapshot(
    store: DurableTaskStore,
    projections: WorkerProjectionStore,
    *,
    admitted: AdmittedExecution,
    node_id: str,
    attempt: dict[str, Any],
) -> dict[str, Any]:
    artifact = dict(
        store.get_node_artifact(
            admitted.graph_id,
            admitted.graph_revision,
            node_id,
        )
    )
    receipt_ref = str(artifact["receipt_ref"])
    receipt = projections.load(receipt_ref)
    assert receipt is not None
    return {
        "node": dict(
            store.get_node(
                admitted.graph_id,
                admitted.graph_revision,
                node_id,
            )
        ),
        "attempt": dict(attempt),
        "artifact": artifact,
        "operation": dict(
            store.get_operation_reservation(
                admitted.for_node(node_id).reservation_id
            )
        ),
        "effects": store.list_acceptance_effect_intents(
            graph_id=admitted.graph_id,
            graph_revision=admitted.graph_revision,
            node_id=node_id,
        ),
        "outbox": _outbox_rows_for_attempt(
            store,
            attempt_id=str(attempt["attempt_id"]),
        ),
        "receipt": dict(receipt),
    }


def _budget_usage_for_attempt(
    snapshot: dict[str, Any],
    *,
    attempt_id: str,
    charge_kind: str,
) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in snapshot["budget_usage"]
        if row["attempt_id"] == attempt_id
        and row["charge_kind"] == charge_kind
    ]


class _ContextVerifier(_Verifier):
    def __init__(self) -> None:
        self.contexts: list[dict[str, Any]] = []

    def verify(self, *, execution_context: dict[str, Any], **kwargs: Any):
        self.contexts.append(dict(execution_context))
        return super().verify(execution_context=execution_context, **kwargs)


def _coordinator(
    *,
    store: Any,
    projections: WorkerProjectionStore,
    worker: _ProjectedWorker,
    owner_id: str,
    verifier: Any | None = None,
    lease_ttl_ms: int = 20,
    bounded_fanout_enabled: bool = False,
    atomic_graph_success_enabled: bool = False,
    checkpoint_uow: Any | None = None,
    worker_resolver: Any | None = None,
    acceptance: Any | None = None,
    node_grant_verifier: Any | None = None,
    review_runtime: Any | None = None,
) -> GovernedTaskCoordinator:
    durable_store = store
    while type(durable_store) is not DurableTaskStore:
        durable_store = durable_store._delegate
    return GovernedTaskCoordinator(
        task_store=store,
        internal_agent_checkpoint_uow=(
            checkpoint_uow
            or DurableInternalAgentCheckpointUnitOfWork(
                task_store=durable_store,
                worker_projection_store=projections,
            )
        ),
        outbox_publisher=_Publisher(),
        outbox_factory=OutboxMessage,
        worker_resolver=(
            worker_resolver
            if worker_resolver is not None
            else _ProductionRouteResolver(worker)
        ),
        verifier=verifier or _Verifier(),
        acceptance=acceptance if acceptance is not None else _Acceptance("ACCEPT"),
        projection_reader=projections,
        review_runtime=review_runtime,
        node_grant_verifier=node_grant_verifier,
        acceptance_effects_enabled=atomic_graph_success_enabled,
        owner_id=owner_id,
        lease_ttl_ms=lease_ttl_ms,
        operation_ttl_ms=lease_ttl_ms,
        bounded_fanout_enabled=bounded_fanout_enabled,
        atomic_graph_success_enabled=atomic_graph_success_enabled,
    )


async def _events(app: GovernedCommandApplication, contracts) -> list[dict[str, Any]]:
    return [
        event
        async for event in app.execute_stream(
            command=contracts[0],
            work_order=contracts[1],
            assignment=contracts[2],
            graph=contracts[3],
        )
    ]


@pytest.mark.asyncio
async def test_commit_response_loss_reads_exact_checkpoint_without_worker_replay(
    tmp_path: Path,
) -> None:
    db_path = _migrated_db(tmp_path)
    contracts = _contracts((("root", ()),))
    clock = _Clock()
    projections = WorkerProjectionStore(db_path, clock=clock)
    calls: list[str] = []
    worker = _ProjectedWorker(projections=projections, calls=calls)
    durable = DurableTaskStore(db_path, clock=clock)
    response_loss = _CommitFailureStore(
        durable,
        TimeoutError("simulated COMMIT response loss"),
    )
    app = GovernedCommandApplication(
        command_repository=HostCommandRepository(db_path, clock=clock),
        coordinator=_coordinator(
            store=response_loss,
            projections=projections,
            worker=worker,
            owner_id="online-owner-a",
        ),
    )

    events = await _events(app, contracts)

    assert events[-1]["execution_receipt"]["accepted"] is True
    assert response_loss.commit_calls == 1
    assert calls == ["root"]
    receipt_ref = events[-1]["execution_receipt"]["receipt_ref"]
    durable_projection = projections.load(receipt_ref)
    assert durable_projection is not None
    assert (
        events[-1]["execution_receipt"]["payload_digest"]
        == durable_projection["payload_digest"]
        == "sha256:" + receipt_ref.rsplit("/", 1)[-1]
    )
    with durable._connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM task_runtime_outbox "
            "WHERE event_type = 'OPERATION_COMMITTED'"
        ).fetchone()[0] == 1


@pytest.mark.asyncio
async def test_reopened_runtime_takes_over_same_attempt_from_committed_checkpoint(
    tmp_path: Path,
) -> None:
    db_path = _migrated_db(tmp_path)
    contracts = _contracts((("root", ()),))
    clock = _Clock()
    projections = WorkerProjectionStore(db_path, clock=clock)
    calls: list[str] = []
    worker = _ProjectedWorker(projections=projections, calls=calls)
    first_store = DurableTaskStore(db_path, clock=clock)
    crash_after_budget = _StopBeforeAttemptSuccessStore(
        first_store,
        _ProcessStopped("process stopped after accepted worker BudgetUsage"),
    )
    first_app = GovernedCommandApplication(
        command_repository=HostCommandRepository(db_path, clock=clock),
        coordinator=_coordinator(
            store=crash_after_budget,
            projections=projections,
            worker=worker,
            owner_id="online-owner-a",
        ),
    )

    with pytest.raises(_ProcessStopped):
        await _events(first_app, contracts)

    snapshot = first_store.read_scheduler_snapshot("graph-gr03a", 1)
    original_attempt = snapshot["attempts"][0]
    original_lease = snapshot["active_leases"][0]
    admitted = AdmittedExecution.from_contracts(
        command=contracts[0],
        assignment=contracts[2],
        graph=contracts[3],
    )
    reservation = first_store.get_operation_reservation(
        admitted.reservation_id
    )
    durable_projection = projections.load(reservation["receipt_ref"])
    assert durable_projection is not None
    expected_checkpoint_digest = durable_projection["payload_digest"]
    assert reservation["status"] == "COMMITTED"
    assert original_attempt["status"] == "RUNNING"
    assert calls == ["root"]

    clock.advance(25)
    reopened_store = DurableTaskStore(db_path, clock=clock)
    response_loss_store = _LeaseAcquireResponseLossStore(reopened_store)
    reopened_projections = WorkerProjectionStore(db_path, clock=clock)
    verifier = _ContextVerifier()
    reopened_app = GovernedCommandApplication(
        command_repository=HostCommandRepository(db_path, clock=clock),
        coordinator=_coordinator(
            store=response_loss_store,
            projections=reopened_projections,
            worker=worker,
            owner_id="online-owner-b",
            verifier=verifier,
        ),
    )

    resumed = await _events(reopened_app, contracts)

    assert resumed[-1]["execution_receipt"]["accepted"] is True
    assert response_loss_store.response_lost is True
    assert response_loss_store.acquire_calls == 2
    assert calls == ["root"]
    assert verifier.contexts
    recovery = verifier.contexts[-1]["recovery"]
    assert recovery["worker_replayed"] is False
    assert recovery["takeover_lease_acquired"] is True
    assert recovery["execution_authority_active"] is False
    assert recovery["checkpoint_payload_digest"] == expected_checkpoint_digest
    assert (
        recovery["checkpoint_payload_digest_source"]
        == "receipt_identity_digest"
    )
    assert (
        resumed[-1]["execution_receipt"]["payload_digest"]
        == expected_checkpoint_digest
    )
    assert verifier.contexts[-1]["lease_id"] is None
    assert verifier.contexts[-1]["fencing_token"] is None
    final_snapshot = reopened_store.read_scheduler_snapshot("graph-gr03a", 1)
    assert len(final_snapshot["attempts"]) == 1
    assert final_snapshot["attempts"][0]["attempt_id"] == original_attempt["attempt_id"]
    assert final_snapshot["attempts"][0]["status"] == "SUCCEEDED"
    with reopened_store._connect() as conn:
        leases = conn.execute(
            "SELECT owner_id, fencing_token, status FROM task_runtime_lease "
            "WHERE attempt_id = ? ORDER BY fencing_token",
            (original_attempt["attempt_id"],),
        ).fetchall()
    assert [(row[0], row[1], row[2]) for row in leases] == [
        ("online-owner-a", original_lease["fencing_token"], "EXPIRED"),
        ("online-owner-b", original_lease["fencing_token"] + 1, "RELEASED"),
    ]

    with pytest.raises(TaskStoreError):
        reopened_store.commit_operation(
            reservation["reservation_id"],
            attempt_id=original_attempt["attempt_id"],
            lease_id=original_lease["lease_id"],
            owner_id="online-owner-a",
            fencing_token=original_lease["fencing_token"],
            expected_version=reservation["status_version"],
            receipt_ref=reservation["receipt_ref"],
            outbox=OutboxMessage.create(
                aggregate_type="OPERATION_RESERVATION",
                aggregate_id=reservation["reservation_id"],
                aggregate_version=reservation["status_version"] + 1,
                event_type="OPERATION_COMMITTED",
                payload_ref="taskstore://event/stale-owner-commit",
                attempt_id=original_attempt["attempt_id"],
            ),
        )

    replay = await _events(reopened_app, contracts)
    assert replay[-1]["execution_receipt"]["replayed"] is True
    assert (
        replay[-1]["execution_receipt"]["payload_digest"]
        == expected_checkpoint_digest
    )
    assert calls == ["root"]


@pytest.mark.asyncio
async def test_prebudget_checkpoint_is_settled_once_without_worker_replay(
    tmp_path: Path,
) -> None:
    db_path = _migrated_db(tmp_path)
    contracts = _contracts((("root", ()),))
    clock = _Clock()
    projections = WorkerProjectionStore(db_path, clock=clock)
    calls: list[str] = []
    worker = _ProjectedWorker(projections=projections, calls=calls)
    durable = DurableTaskStore(db_path, clock=clock)
    first_app = GovernedCommandApplication(
        command_repository=HostCommandRepository(db_path, clock=clock),
        coordinator=_coordinator(
            store=_TimedCommitFailureStore(
                durable,
                _ProcessStopped("process stopped before budget settlement"),
                clock=clock,
                elapsed_ms=7,
            ),
            projections=projections,
            worker=worker,
            owner_id="budget-owner-a",
        ),
    )

    with pytest.raises(GovernedRuntimeError):
        await _events(first_app, contracts)
    clock.advance(25)

    reopened = DurableTaskStore(db_path, clock=clock)
    second_app = GovernedCommandApplication(
        command_repository=HostCommandRepository(db_path, clock=clock),
        coordinator=_coordinator(
            store=reopened,
            projections=WorkerProjectionStore(db_path, clock=clock),
            worker=worker,
            owner_id="budget-owner-b",
        ),
    )
    resumed = await _events(second_app, contracts)

    assert resumed[-1]["execution_receipt"]["status"] == "ACCEPTED"
    assert resumed[-1]["execution_receipt"]["accepted"] is True
    assert calls == ["root"]
    snapshot = reopened.read_scheduler_snapshot("graph-gr03a", 1)
    worker_usages = [
        row
        for row in snapshot["budget_usage"]
        if row["charge_kind"].startswith("WORKER_USAGE")
    ]
    assert len(worker_usages) == 1
    assert worker_usages[0]["accepted"] is True
    assert worker_usages[0]["delta"]["time_ms"] == 7
    assert worker_usages[0]["delta"]["token_count"] == 10
    assert snapshot["attempts"][0]["status"] == "SUCCEEDED"

    third_app = GovernedCommandApplication(
        command_repository=HostCommandRepository(db_path, clock=clock),
        coordinator=_coordinator(
            store=DurableTaskStore(db_path, clock=clock),
            projections=WorkerProjectionStore(db_path, clock=clock),
            worker=worker,
            owner_id="budget-owner-c",
        ),
    )
    replay = await _events(third_app, contracts)
    assert replay[-1]["execution_receipt"]["accepted"] is True
    assert replay[-1]["execution_receipt"]["replayed"] is True
    final_snapshot = DurableTaskStore(db_path, clock=clock).read_scheduler_snapshot(
        "graph-gr03a", 1
    )
    assert len(
        [
            row
            for row in final_snapshot["budget_usage"]
            if row["charge_kind"].startswith("WORKER_USAGE")
        ]
    ) == 1
    assert calls == ["root"]


@pytest.mark.asyncio
async def test_prebudget_checkpoint_over_limit_fails_closed_without_replay(
    tmp_path: Path,
) -> None:
    db_path = _migrated_db(tmp_path)
    contracts = _contracts((("root", ()),), token_limit=5)
    clock = _Clock()
    projections = WorkerProjectionStore(db_path, clock=clock)
    calls: list[str] = []
    worker = _ProjectedWorker(projections=projections, calls=calls)
    durable = DurableTaskStore(db_path, clock=clock)
    first_app = GovernedCommandApplication(
        command_repository=HostCommandRepository(db_path, clock=clock),
        coordinator=_coordinator(
            store=_TimedCommitFailureStore(
                durable,
                _ProcessStopped("process stopped before budget settlement"),
                clock=clock,
                elapsed_ms=9,
            ),
            projections=projections,
            worker=worker,
            owner_id="budget-limit-owner-a",
        ),
    )

    with pytest.raises(GovernedRuntimeError):
        await _events(first_app, contracts)
    clock.advance(25)
    reopened = DurableTaskStore(db_path, clock=clock)
    second_app = GovernedCommandApplication(
        command_repository=HostCommandRepository(db_path, clock=clock),
        coordinator=_coordinator(
            store=reopened,
            projections=WorkerProjectionStore(db_path, clock=clock),
            worker=worker,
            owner_id="budget-limit-owner-b",
        ),
    )

    blocked = await _events(second_app, contracts)

    assert blocked[-1]["execution_receipt"]["status"] == "BUDGET_BLOCKED"
    assert blocked[-1]["execution_receipt"]["accepted"] is False
    assert calls == ["root"]
    snapshot = reopened.read_scheduler_snapshot("graph-gr03a", 1)
    worker_usages = [
        row
        for row in snapshot["budget_usage"]
        if row["charge_kind"].startswith("WORKER_USAGE")
    ]
    assert len(worker_usages) == 1
    assert worker_usages[0]["accepted"] is False
    assert worker_usages[0]["delta"]["time_ms"] == 9
    assert "token_count" in worker_usages[0]["exceeded_dimensions"]
    assert reopened.get_graph("graph-gr03a", 1)["status"] == "BLOCKED"


@pytest.mark.asyncio
async def test_prebudget_settlement_response_loss_reuses_exact_usage(
    tmp_path: Path,
) -> None:
    db_path = _migrated_db(tmp_path)
    contracts = _contracts((("root", ()),))
    clock = _Clock()
    projections = WorkerProjectionStore(db_path, clock=clock)
    calls: list[str] = []
    worker = _ProjectedWorker(projections=projections, calls=calls)
    durable = DurableTaskStore(db_path, clock=clock)
    first_app = GovernedCommandApplication(
        command_repository=HostCommandRepository(db_path, clock=clock),
        coordinator=_coordinator(
            store=_TimedCommitFailureStore(
                durable,
                _ProcessStopped("process stopped before budget settlement"),
                clock=clock,
                elapsed_ms=11,
            ),
            projections=projections,
            worker=worker,
            owner_id="budget-loss-owner-a",
        ),
    )
    with pytest.raises(GovernedRuntimeError):
        await _events(first_app, contracts)
    clock.advance(25)

    loss_store = _BudgetResponseLossStore(DurableTaskStore(db_path, clock=clock))
    second_app = GovernedCommandApplication(
        command_repository=HostCommandRepository(db_path, clock=clock),
        coordinator=_coordinator(
            store=loss_store,
            projections=WorkerProjectionStore(db_path, clock=clock),
            worker=worker,
            owner_id="budget-loss-owner-b",
        ),
    )
    resumed = await _events(second_app, contracts)

    assert resumed[-1]["execution_receipt"]["accepted"] is True
    assert loss_store.failed is True
    assert calls == ["root"]
    snapshot = DurableTaskStore(db_path, clock=clock).read_scheduler_snapshot(
        "graph-gr03a", 1
    )
    assert len(
        [
            row
            for row in snapshot["budget_usage"]
            if row["charge_kind"].startswith("WORKER_USAGE")
        ]
    ) == 1


@pytest.mark.parametrize(
    ("commit_before_loss", "token_limit", "expected_terminal", "held_budget"),
    (
        (True, 100, "ACCEPTED", "ACTIVE"),
        (False, 100, "ACCEPTED", "ACTIVE"),
        (True, 5, "BUDGET_BLOCKED", "EXCEEDED"),
    ),
)
@pytest.mark.asyncio
async def test_post_commit_budget_double_loss_holds_without_lifecycle_writes_and_recovers(
    tmp_path: Path,
    commit_before_loss: bool,
    token_limit: int,
    expected_terminal: str,
    held_budget: str,
) -> None:
    db_path = _migrated_db(tmp_path)
    contracts = _contracts((("root", ()),), token_limit=token_limit)
    clock = _Clock()
    projections = WorkerProjectionStore(db_path, clock=clock)
    calls: list[str] = []
    worker = _ProjectedWorker(projections=projections, calls=calls)
    durable = DurableTaskStore(db_path, clock=clock)
    uncertain_store = _BudgetSettlementDoubleLossStore(
        durable,
        commit_before_loss=commit_before_loss,
        clock=clock,
    )
    first_app = GovernedCommandApplication(
        command_repository=HostCommandRepository(db_path, clock=clock),
        coordinator=_coordinator(
            store=uncertain_store,
            projections=projections,
            worker=worker,
            owner_id="double-loss-owner-a",
        ),
    )

    first = await _events(first_app, contracts)
    first_receipt = first[-1]["execution_receipt"]

    assert first_receipt["status"] == "RECOVERY_REQUIRED"
    assert first_receipt["reason_code"] == "GOVERNED_OPERATION_RECOVERY_REQUIRED"
    assert first_receipt["projection"]["budget_settlement_state"] == "UNKNOWN"
    assert first_receipt["projection"]["operation_status"] == "COMMITTED"
    assert uncertain_store.consume_failed is True
    assert uncertain_store.read_failed is True
    assert calls == ["root"]

    admitted = AdmittedExecution.from_contracts(
        command=contracts[0],
        assignment=contracts[2],
        graph=contracts[3],
    )
    held = durable.read_scheduler_snapshot("graph-gr03a", 1)
    operation_before = dict(
        durable.get_operation_reservation(admitted.reservation_id)
    )
    original_attempt = dict(held["attempts"][0])
    original_lease = dict(held["active_leases"][0])
    assert operation_before["status"] == "COMMITTED"
    assert operation_before["receipt_ref"]
    assert held["graph_record"]["status"] == "ACTIVE"
    assert durable.get_node("graph-gr03a", 1, "root")["status"] == "RUNNING"
    assert original_attempt["status"] == "RUNNING"
    assert held["budgets"][0]["status"] == held_budget
    assert len(held["active_leases"]) == 1
    worker_usages = [
        row
        for row in held["budget_usage"]
        if row["charge_kind"].startswith("WORKER_USAGE")
    ]
    assert len(worker_usages) == int(commit_before_loss)
    original_usage = dict(worker_usages[0]) if worker_usages else None
    if original_usage is not None:
        assert original_usage["usage_id"] == "worker-usage://root/online-takeover"
        assert original_usage["attempt_id"] == original_attempt["attempt_id"]
    assert _terminal_lifecycle_outbox_events(durable) == set()
    assert _operation_commit_outbox_count(durable) == 1

    clock.advance(25)
    reopened = DurableTaskStore(db_path, clock=clock)
    second_app = GovernedCommandApplication(
        command_repository=HostCommandRepository(db_path, clock=clock),
        coordinator=_coordinator(
            store=reopened,
            projections=WorkerProjectionStore(db_path, clock=clock),
            worker=worker,
            owner_id="double-loss-owner-b",
        ),
    )
    resumed = await _events(second_app, contracts)

    assert resumed[-1]["execution_receipt"]["status"] == expected_terminal
    assert calls == ["root"]
    final = reopened.read_scheduler_snapshot("graph-gr03a", 1)
    assert len(final["attempts"]) == 1
    assert final["attempts"][0]["attempt_id"] == original_attempt["attempt_id"]
    final_usages = [
        row
        for row in final["budget_usage"]
        if row["charge_kind"].startswith("WORKER_USAGE")
    ]
    assert len(final_usages) == 1
    assert final_usages[0]["usage_id"] == "worker-usage://root/online-takeover"
    assert final_usages[0]["attempt_id"] == original_attempt["attempt_id"]
    if original_usage is not None:
        assert final_usages[0] == original_usage
    operation_after = reopened.get_operation_reservation(admitted.reservation_id)
    assert operation_after == operation_before
    assert operation_after["status"] == "COMMITTED"
    assert operation_after["receipt_ref"] == operation_before["receipt_ref"]
    assert _operation_commit_outbox_count(reopened) == 1
    assert _lease_lineage(
        reopened,
        attempt_id=original_attempt["attempt_id"],
    ) == [
        (
            "double-loss-owner-a",
            original_lease["fencing_token"],
            "EXPIRED",
        ),
        (
            "double-loss-owner-b",
            original_lease["fencing_token"] + 1,
            "RELEASED",
        ),
    ]
    assert final["graph_record"]["status"] == (
        "COMPLETED" if expected_terminal == "ACCEPTED" else "BLOCKED"
    )


@pytest.mark.asyncio
async def test_exact_rejected_checkpoint_closeout_response_loss_reopens_atomically(
    tmp_path: Path,
) -> None:
    db_path = _migrated_db(tmp_path)
    contracts = _contracts((("root", ()),), token_limit=5)
    clock = _Clock()
    projections = WorkerProjectionStore(db_path, clock=clock)
    calls: list[str] = []
    worker = _ProjectedWorker(projections=projections, calls=calls)
    durable = DurableTaskStore(db_path, clock=clock)
    uncertain_store = _BudgetSettlementDoubleLossStore(
        durable,
        commit_before_loss=True,
        clock=clock,
    )
    first_app = GovernedCommandApplication(
        command_repository=HostCommandRepository(db_path, clock=clock),
        coordinator=_coordinator(
            store=uncertain_store,
            projections=projections,
            worker=worker,
            owner_id="atomic-rejection-owner-a",
        ),
    )

    held_events = await _events(first_app, contracts)
    assert held_events[-1]["execution_receipt"]["status"] == "RECOVERY_REQUIRED"
    held = durable.read_scheduler_snapshot("graph-gr03a", 1)
    original_attempt = dict(held["attempts"][0])
    original_lease = dict(held["active_leases"][0])
    original_operation = dict(
        durable.get_operation_reservation(
            AdmittedExecution.from_contracts(
                command=contracts[0],
                assignment=contracts[2],
                graph=contracts[3],
            ).reservation_id
        )
    )
    original_usage = next(
        dict(row)
        for row in held["budget_usage"]
        if row["charge_kind"].startswith("WORKER_USAGE")
    )
    assert original_usage["accepted"] is False
    assert held["budgets"][0]["status"] == "EXCEEDED"
    assert _terminal_lifecycle_outbox_events(durable) == set()

    clock.advance(25)
    reopened_store = DurableTaskStore(db_path, clock=clock)
    reopened_projections = WorkerProjectionStore(db_path, clock=clock)
    checkpoint_uow = _CheckpointBudgetRejectionResponseLossUow(
        DurableInternalAgentCheckpointUnitOfWork(
            task_store=reopened_store,
            worker_projection_store=reopened_projections,
        )
    )
    reopened_app = GovernedCommandApplication(
        command_repository=HostCommandRepository(db_path, clock=clock),
        coordinator=_coordinator(
            store=reopened_store,
            projections=reopened_projections,
            worker=worker,
            owner_id="atomic-rejection-owner-b",
            checkpoint_uow=checkpoint_uow,
        ),
    )

    terminal_events = await _events(reopened_app, contracts)

    assert terminal_events[-1]["execution_receipt"]["status"] == "BUDGET_BLOCKED"
    assert checkpoint_uow.mutation_calls == 1
    assert checkpoint_uow.reopen_calls == 1
    assert calls == ["root"]
    final = reopened_store.read_scheduler_snapshot("graph-gr03a", 1)
    assert final["graph_record"]["status"] == "BLOCKED"
    assert reopened_store.get_node("graph-gr03a", 1, "root")["status"] == "FAILED"
    assert len(final["attempts"]) == 1
    assert final["attempts"][0]["attempt_id"] == original_attempt["attempt_id"]
    assert final["attempts"][0]["status"] == "FAILED"
    assert final["budgets"][0]["status"] == "RELEASED"
    final_usage = next(
        dict(row)
        for row in final["budget_usage"]
        if row["charge_kind"].startswith("WORKER_USAGE")
    )
    assert final_usage == original_usage
    assert reopened_store.get_operation_reservation(
        original_operation["reservation_id"]
    ) == original_operation
    assert _operation_commit_outbox_count(reopened_store) == 1
    assert _lease_lineage(
        reopened_store,
        attempt_id=original_attempt["attempt_id"],
    ) == [
        (
            "atomic-rejection-owner-a",
            original_lease["fencing_token"],
            "EXPIRED",
        ),
        (
            "atomic-rejection-owner-b",
            original_lease["fencing_token"] + 1,
            "RELEASED",
        ),
    ]
    assert _terminal_lifecycle_outbox_events(reopened_store) == {
        "TASK_ATTEMPT_FAILED",
        "TASK_NODE_FAILED",
        "TASK_GRAPH_BLOCKED",
        "TASK_LEASE_RELEASED_TERMINAL",
        "BUDGET_RESERVATION_RELEASED",
    }

    duplicate_events = await _events(reopened_app, contracts)
    duplicate_receipt = duplicate_events[-1]["execution_receipt"]
    assert duplicate_receipt["status"] == "BUDGET_BLOCKED"
    assert duplicate_receipt["reason_code"] == "GOVERNED_BUDGET_EXCEEDED"
    assert duplicate_receipt["accepted"] is False
    assert duplicate_receipt["replayed"] is True
    assert checkpoint_uow.mutation_calls == 1
    assert checkpoint_uow.reopen_calls == 1
    assert calls == ["root"]
    duplicate = reopened_store.read_scheduler_snapshot("graph-gr03a", 1)
    assert duplicate["attempts"] == final["attempts"]
    assert duplicate["budgets"] == final["budgets"]
    assert duplicate["budget_usage"] == final["budget_usage"]
    assert reopened_store.get_operation_reservation(
        original_operation["reservation_id"]
    ) == original_operation
    assert _operation_commit_outbox_count(reopened_store) == 1
    assert _terminal_lifecycle_outbox_events(reopened_store) == {
        "TASK_ATTEMPT_FAILED",
        "TASK_NODE_FAILED",
        "TASK_GRAPH_BLOCKED",
        "TASK_LEASE_RELEASED_TERMINAL",
        "BUDGET_RESERVATION_RELEASED",
    }
    assert reopened_store.list_terminal_reconciliation_inventory_candidates() == ()


@pytest.mark.asyncio
async def test_post_commit_budget_process_stop_propagates_without_terminal_writes(
    tmp_path: Path,
) -> None:
    db_path = _migrated_db(tmp_path)
    contracts = _contracts((("root", ()),), token_limit=100)
    clock = _Clock()
    projections = WorkerProjectionStore(db_path, clock=clock)
    calls: list[str] = []
    worker = _ProjectedWorker(projections=projections, calls=calls)
    durable = DurableTaskStore(db_path, clock=clock)
    stopped_store = _BudgetSettlementDoubleLossStore(
        durable,
        commit_before_loss=True,
        clock=clock,
        write_failure_type=_ProcessStopped,
        read_failure_type=None,
    )
    first_app = GovernedCommandApplication(
        command_repository=HostCommandRepository(db_path, clock=clock),
        coordinator=_coordinator(
            store=stopped_store,
            projections=projections,
            worker=worker,
            owner_id="settlement-stop-owner-a",
        ),
    )

    with pytest.raises(_ProcessStopped):
        await _events(first_app, contracts)

    admitted = AdmittedExecution.from_contracts(
        command=contracts[0],
        assignment=contracts[2],
        graph=contracts[3],
    )
    held = durable.read_scheduler_snapshot("graph-gr03a", 1)
    operation_before = dict(
        durable.get_operation_reservation(admitted.reservation_id)
    )
    original_attempt = dict(held["attempts"][0])
    original_lease = dict(held["active_leases"][0])
    assert operation_before["status"] == "COMMITTED"
    assert held["graph_record"]["status"] == "ACTIVE"
    assert durable.get_node("graph-gr03a", 1, "root")["status"] == "RUNNING"
    assert original_attempt["status"] == "RUNNING"
    assert held["budgets"][0]["status"] == "ACTIVE"
    assert len(held["active_leases"]) == 1
    worker_usages = [
        row
        for row in held["budget_usage"]
        if row["charge_kind"].startswith("WORKER_USAGE")
    ]
    assert len(worker_usages) == 1
    original_usage = dict(worker_usages[0])
    assert original_usage["usage_id"] == "worker-usage://root/online-takeover"
    assert original_usage["attempt_id"] == original_attempt["attempt_id"]
    assert stopped_store.consume_failed is True
    assert stopped_store.read_failed is False
    assert _terminal_lifecycle_outbox_events(durable) == set()
    assert _operation_commit_outbox_count(durable) == 1
    assert calls == ["root"]

    clock.advance(25)
    reopened = DurableTaskStore(db_path, clock=clock)
    response_loss_store = _LeaseAcquireResponseLossStore(reopened)
    resumed = await _events(
        GovernedCommandApplication(
            command_repository=HostCommandRepository(db_path, clock=clock),
            coordinator=_coordinator(
                store=response_loss_store,
                projections=WorkerProjectionStore(db_path, clock=clock),
                worker=worker,
                owner_id="settlement-stop-owner-b",
            ),
        ),
        contracts,
    )
    assert resumed[-1]["execution_receipt"]["status"] == "ACCEPTED"
    final = reopened.read_scheduler_snapshot("graph-gr03a", 1)
    assert len(final["attempts"]) == 1
    assert final["attempts"][0]["attempt_id"] == original_attempt["attempt_id"]
    final_usages = [
        row
        for row in final["budget_usage"]
        if row["charge_kind"].startswith("WORKER_USAGE")
    ]
    assert len(final_usages) == 1
    assert final_usages[0] == original_usage
    assert final_usages[0]["usage_id"] == "worker-usage://root/online-takeover"
    assert final_usages[0]["attempt_id"] == original_attempt["attempt_id"]
    operation_after = reopened.get_operation_reservation(admitted.reservation_id)
    assert operation_after == operation_before
    assert operation_after["status"] == "COMMITTED"
    assert operation_after["receipt_ref"] == operation_before["receipt_ref"]
    assert _operation_commit_outbox_count(reopened) == 1
    assert _lease_lineage(
        reopened,
        attempt_id=original_attempt["attempt_id"],
    ) == [
        (
            "settlement-stop-owner-a",
            original_lease["fencing_token"],
            "EXPIRED",
        ),
        (
            "settlement-stop-owner-b",
            original_lease["fencing_token"] + 1,
            "RELEASED",
        ),
    ]
    assert calls == ["root"]


@pytest.mark.asyncio
async def test_conflicting_worker_usage_blocks_before_takeover_lease(
    tmp_path: Path,
) -> None:
    db_path = _migrated_db(tmp_path)
    contracts = _contracts((("root", ()),))
    clock = _Clock()
    projections = WorkerProjectionStore(db_path, clock=clock)
    calls: list[str] = []
    worker = _ProjectedWorker(projections=projections, calls=calls)
    durable = DurableTaskStore(db_path, clock=clock)
    first_app = GovernedCommandApplication(
        command_repository=HostCommandRepository(db_path, clock=clock),
        coordinator=_coordinator(
            store=_TimedCommitFailureStore(
                durable,
                _ProcessStopped("process stopped before budget settlement"),
                clock=clock,
                elapsed_ms=13,
            ),
            projections=projections,
            worker=worker,
            owner_id="budget-conflict-owner-a",
        ),
    )
    with pytest.raises(GovernedRuntimeError):
        await _events(first_app, contracts)

    snapshot = durable.read_scheduler_snapshot("graph-gr03a", 1)
    budget = snapshot["budgets"][0]
    attempt_id = snapshot["attempts"][0]["attempt_id"]
    wrong_usage_id = "worker-usage://root/conflicting-checkpoint"
    durable.consume_budget_once(
        budget["budget_id"],
        usage_id=wrong_usage_id,
        charge_kind="WORKER_USAGE:COST+RETRY+TIME+TOKEN",
        graph_id="graph-gr03a",
        graph_revision=1,
        node_id="root",
        attempt_id=attempt_id,
        delta=BudgetAmount(
            token_count=1,
            time_ms=13,
            retry_count=0,
            model_cost_micros=1,
        ),
        expected_version=budget["status_version"],
        outbox=OutboxMessage.create(
            aggregate_type="BUDGET_RESERVATION",
            aggregate_id=budget["budget_id"],
            aggregate_version=budget["status_version"] + 1,
            event_type="BUDGET_CONSUMED",
            payload_ref=(
                "taskstore://event/BUDGET_RESERVATION/"
                f"{budget['budget_id']}/{budget['status_version'] + 1}"
            ),
            attempt_id=attempt_id,
            evidence_refs=(wrong_usage_id,),
        ),
    )
    clock.advance(25)

    second_app = GovernedCommandApplication(
        command_repository=HostCommandRepository(db_path, clock=clock),
        coordinator=_coordinator(
            store=DurableTaskStore(db_path, clock=clock),
            projections=WorkerProjectionStore(db_path, clock=clock),
            worker=worker,
            owner_id="budget-conflict-owner-b",
        ),
    )
    blocked = await _events(second_app, contracts)

    assert blocked[-1]["execution_receipt"]["status"] == "RECOVERY_REQUIRED"
    assert blocked[-1]["execution_receipt"]["accepted"] is False
    assert calls == ["root"]
    with durable._connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM task_runtime_lease WHERE owner_id = ?",
            ("budget-conflict-owner-b",),
        ).fetchone()[0] == 0


@pytest.mark.asyncio
async def test_concurrent_prebudget_takeover_has_one_fenced_settlement(
    tmp_path: Path,
) -> None:
    db_path = _migrated_db(tmp_path)
    contracts = _contracts((("root", ()),))
    clock = _Clock()
    projections = WorkerProjectionStore(db_path, clock=clock)
    calls: list[str] = []
    worker = _ProjectedWorker(projections=projections, calls=calls)
    first_app = GovernedCommandApplication(
        command_repository=HostCommandRepository(db_path, clock=clock),
        coordinator=_coordinator(
            store=_TimedCommitFailureStore(
                DurableTaskStore(db_path, clock=clock),
                _ProcessStopped("process stopped before budget settlement"),
                clock=clock,
                elapsed_ms=17,
            ),
            projections=projections,
            worker=worker,
            owner_id="budget-race-owner-a",
        ),
    )
    with pytest.raises(GovernedRuntimeError):
        await _events(first_app, contracts)
    clock.advance(25)

    barrier = threading.Barrier(2)
    apps = [
        GovernedCommandApplication(
            command_repository=HostCommandRepository(db_path, clock=clock),
            coordinator=_coordinator(
                store=_BarrierAcquireStore(
                    DurableTaskStore(db_path, clock=clock),
                    barrier,
                ),
                projections=WorkerProjectionStore(db_path, clock=clock),
                worker=worker,
                owner_id=f"budget-race-owner-{suffix}",
            ),
        )
        for suffix in ("b", "c")
    ]

    async def threaded_events(app: GovernedCommandApplication) -> list[dict[str, Any]]:
        return await asyncio.to_thread(lambda: asyncio.run(_events(app, contracts)))

    outcomes = await asyncio.gather(*(threaded_events(app) for app in apps))
    receipts = [events[-1]["execution_receipt"] for events in outcomes]

    assert sum(receipt["accepted"] is True for receipt in receipts) == 1
    assert sum(receipt["status"] == "RECOVERY_REQUIRED" for receipt in receipts) == 1
    assert calls == ["root"]
    snapshot = DurableTaskStore(db_path, clock=clock).read_scheduler_snapshot(
        "graph-gr03a", 1
    )
    worker_usages = [
        row
        for row in snapshot["budget_usage"]
        if row["charge_kind"].startswith("WORKER_USAGE")
    ]
    assert len(worker_usages) == 1
    assert worker_usages[0]["accepted"] is True
    assert worker_usages[0]["delta"]["time_ms"] == 17


@pytest.mark.asyncio
async def test_multi_node_process_stop_without_budget_checkpoint_stays_recovery_pending(
    tmp_path: Path,
) -> None:
    db_path = _migrated_db(tmp_path)
    contracts = _contracts(
        (("root-a", ()), ("root-b", ())),
        concurrency=2,
    )
    admitted = AdmittedExecution.from_contracts(
        command=contracts[0],
        assignment=contracts[2],
        graph=contracts[3],
    )
    clock = _Clock()
    projections = WorkerProjectionStore(db_path, clock=clock)
    calls: list[str] = []
    worker = _ProjectedWorker(projections=projections, calls=calls)
    durable = DurableTaskStore(db_path, clock=clock)
    first_app = GovernedCommandApplication(
        command_repository=HostCommandRepository(db_path, clock=clock),
        coordinator=_coordinator(
            store=_SelectedCommitFailureStore(
                durable,
                _ProcessStopped("one parallel node stopped after COMMIT"),
                reservation_id=admitted.for_node("root-a").reservation_id,
            ),
            projections=projections,
            worker=worker,
            owner_id="batch-owner-a",
            bounded_fanout_enabled=True,
        ),
    )

    with pytest.raises(GovernedRuntimeError):
        await _events(first_app, contracts)
    calls_before_restart = list(calls)
    clock.advance(25)

    reopened = DurableTaskStore(db_path, clock=clock)
    second_app = GovernedCommandApplication(
        command_repository=HostCommandRepository(db_path, clock=clock),
        coordinator=_coordinator(
            store=reopened,
            projections=WorkerProjectionStore(db_path, clock=clock),
            worker=worker,
            owner_id="batch-owner-b",
            bounded_fanout_enabled=True,
        ),
    )
    pending = await _events(second_app, contracts)

    assert pending[-1]["execution_receipt"]["status"] == "RECOVERY_REQUIRED"
    assert (
        pending[-1]["execution_receipt"]["projection"][
            "online_takeover_state"
        ]
        == "BATCH_RECOVERY_PENDING"
    )
    assert calls == calls_before_restart
    with reopened._connect() as conn:
        takeover_leases = conn.execute(
            "SELECT COUNT(*) FROM task_runtime_lease WHERE owner_id = ?",
            ("batch-owner-b",),
        ).fetchone()[0]
    assert takeover_leases == 0


@pytest.mark.asyncio
async def test_heartbeat_loss_keeps_unknown_side_effect_blocked_for_signed_reconciliation(
    tmp_path: Path,
) -> None:
    db_path = _migrated_db(tmp_path)
    contracts = _contracts((("root", ()),))
    clock = _Clock()
    projections = WorkerProjectionStore(db_path, clock=clock)
    calls: list[str] = []
    worker = _ProjectedWorker(
        projections=projections,
        calls=calls,
        delay_seconds=0.08,
    )
    durable = DurableTaskStore(db_path, clock=clock)
    heartbeat_loss = _FailingHeartbeatStore(durable)
    first_app = GovernedCommandApplication(
        command_repository=HostCommandRepository(db_path, clock=clock),
        coordinator=_coordinator(
            store=heartbeat_loss,
            projections=projections,
            worker=worker,
            owner_id="heartbeat-owner-a",
            lease_ttl_ms=30,
        ),
    )

    first = await _events(first_app, contracts)
    assert first[-1]["execution_receipt"]["status"] == "RECOVERY_REQUIRED"
    assert calls == ["root"]
    clock.advance(31)

    second_app = GovernedCommandApplication(
        command_repository=HostCommandRepository(db_path, clock=clock),
        coordinator=_coordinator(
            store=durable,
            projections=projections,
            worker=worker,
            owner_id="heartbeat-owner-b",
            lease_ttl_ms=30,
        ),
    )
    second = await _events(second_app, contracts)
    projection = second[-1]["execution_receipt"]["projection"]

    assert second[-1]["execution_receipt"]["status"] == "RECOVERY_REQUIRED"
    assert projection["side_effect_state"] == "UNKNOWN"
    assert projection["blocking_side_effect_reversible"] is False
    assert projection["required_signed_reconciliation_actions"] == [
        "COMMIT",
        "RELEASE",
        "REACQUIRE",
    ]
    assert calls == ["root"]


@pytest.mark.asyncio
async def test_interrupted_operation_stays_blocked_without_signed_reacquisition(
    tmp_path: Path,
) -> None:
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
    durable = DurableTaskStore(db_path, clock=clock)
    first_app = GovernedCommandApplication(
        command_repository=HostCommandRepository(db_path, clock=clock),
        coordinator=_coordinator(
            store=_FailingHeartbeatStore(durable),
            projections=projections,
            worker=worker,
            owner_id="expiry-owner-a",
            lease_ttl_ms=30,
        ),
    )

    first = await _events(first_app, contracts)
    assert first[-1]["execution_receipt"]["status"] == "RECOVERY_REQUIRED"
    before = durable.get_operation_reservation(admitted.reservation_id)
    assert before["status"] == "RECOVERY_REQUIRED"
    clock.advance(31)

    second_app = GovernedCommandApplication(
        command_repository=HostCommandRepository(db_path, clock=clock),
        coordinator=_coordinator(
            store=durable,
            projections=projections,
            worker=worker,
            owner_id="expiry-owner-b",
            lease_ttl_ms=30,
        ),
    )
    second = await _events(second_app, contracts)
    projection = second[-1]["execution_receipt"]["projection"]
    recovered = durable.get_operation_reservation(admitted.reservation_id)

    assert second[-1]["execution_receipt"]["status"] == "RECOVERY_REQUIRED"
    assert recovered["status"] == "RECOVERY_REQUIRED"
    assert recovered["status_version"] == before["status_version"]
    assert recovered["recovery_reason_code"] == before["recovery_reason_code"]
    assert projection["online_takeover_state"] == "SIGNED_RECONCILIATION_REQUIRED"
    assert projection["side_effect_state"] == "UNKNOWN"
    assert projection["blocking_side_effect_reversible"] is False
    assert calls == ["root"]
    with durable._connect() as conn:
        new_owner_lease_count = conn.execute(
            "SELECT COUNT(*) FROM task_runtime_lease WHERE owner_id = ?",
            ("expiry-owner-b",),
        ).fetchone()[0]
    assert new_owner_lease_count == 0


@pytest.mark.asyncio
async def test_preoperation_hard_stop_replays_same_attempt_under_higher_fence(
    tmp_path: Path,
) -> None:
    db_path = _migrated_db(tmp_path)
    contracts = _contracts((("root", ()),))
    admitted = AdmittedExecution.from_contracts(
        command=contracts[0],
        assignment=contracts[2],
        graph=contracts[3],
    )
    clock = _Clock()
    projections = WorkerProjectionStore(db_path, clock=clock)
    durable = DurableTaskStore(db_path, clock=clock)
    calls: list[str] = []
    starts: list[dict[str, Any]] = []
    worker = _ReservationAwareProjectedWorker(
        projections=projections,
        calls=calls,
        task_store=durable,
        starts=starts,
    )
    stopped_store = _ReserveBoundaryStopStore(
        durable,
        _ProcessStopped("process stopped before Operation reservation"),
        commit_before_stop=False,
    )
    first_app = GovernedCommandApplication(
        command_repository=HostCommandRepository(db_path, clock=clock),
        coordinator=_coordinator(
            store=stopped_store,
            projections=projections,
            worker=worker,
            owner_id="preoperation-owner-a",
        ),
    )

    with pytest.raises(_ProcessStopped):
        await _events(first_app, contracts)

    held = durable.read_scheduler_snapshot("graph-gr03a", 1)
    original_attempt = dict(held["attempts"][0])
    original_lease = dict(held["active_leases"][0])
    original_precharge = _budget_usage_for_attempt(
        held,
        attempt_id=original_attempt["attempt_id"],
        charge_kind="PRECHARGE",
    )
    assert stopped_store.reserve_calls == 1
    assert held["graph_record"]["status"] == "ACTIVE"
    assert durable.get_node("graph-gr03a", 1, "root")["status"] == "RUNNING"
    assert original_attempt["status"] == "RUNNING"
    assert original_lease["owner_id"] == "preoperation-owner-a"
    assert original_precharge and original_precharge[0]["accepted"] is True
    assert _operation_rows_for_attempt(
        durable,
        attempt_id=original_attempt["attempt_id"],
    ) == []
    assert calls == []
    assert starts == []

    clock.advance(25)
    reopened = DurableTaskStore(db_path, clock=clock)
    response_loss_store = _LeaseAcquireResponseLossStore(reopened)
    resumed = await _events(
        GovernedCommandApplication(
            command_repository=HostCommandRepository(db_path, clock=clock),
            coordinator=_coordinator(
                store=response_loss_store,
                projections=WorkerProjectionStore(db_path, clock=clock),
                worker=worker,
                owner_id="preoperation-owner-b",
            ),
        ),
        contracts,
    )

    assert resumed[-1]["execution_receipt"]["status"] == "ACCEPTED"
    assert resumed[-1]["execution_receipt"]["accepted"] is True
    assert response_loss_store.response_lost is True
    assert response_loss_store.acquire_calls == 2
    final = reopened.read_scheduler_snapshot("graph-gr03a", 1)
    assert len(final["attempts"]) == 1
    assert final["attempts"][0]["attempt_id"] == original_attempt["attempt_id"]
    assert final["attempts"][0]["status"] == "SUCCEEDED"
    assert _budget_usage_for_attempt(
        final,
        attempt_id=original_attempt["attempt_id"],
        charge_kind="PRECHARGE",
    ) == original_precharge
    assert _lease_lineage(
        reopened,
        attempt_id=original_attempt["attempt_id"],
    ) == [
        (
            "preoperation-owner-a",
            original_lease["fencing_token"],
            "EXPIRED",
        ),
        (
            "preoperation-owner-b",
            original_lease["fencing_token"] + 1,
            "RELEASED",
        ),
    ]
    operation_rows = _operation_rows_for_attempt(
        reopened,
        attempt_id=original_attempt["attempt_id"],
    )
    assert len(operation_rows) == 1
    assert operation_rows[0]["reservation_id"] == admitted.reservation_id
    assert operation_rows[0]["status"] == "COMMITTED"
    assert operation_rows[0]["owner_id"] == "preoperation-owner-b"
    assert operation_rows[0]["fencing_token"] == (
        original_lease["fencing_token"] + 1
    )
    assert calls == ["root"]
    assert starts == [
        {
            "reservation_id": admitted.reservation_id,
            "reservation_status": "RESERVED",
            "attempt_id": original_attempt["attempt_id"],
            "owner_id": "preoperation-owner-b",
            "fencing_token": original_lease["fencing_token"] + 1,
        }
    ]


@pytest.mark.asyncio
async def test_reserve_response_loss_uses_operation_recovery_without_worker_replay(
    tmp_path: Path,
) -> None:
    db_path = _migrated_db(tmp_path)
    contracts = _contracts((("root", ()),))
    admitted = AdmittedExecution.from_contracts(
        command=contracts[0],
        assignment=contracts[2],
        graph=contracts[3],
    )
    clock = _Clock()
    projections = WorkerProjectionStore(db_path, clock=clock)
    durable = DurableTaskStore(db_path, clock=clock)
    calls: list[str] = []
    starts: list[dict[str, Any]] = []
    worker = _ReservationAwareProjectedWorker(
        projections=projections,
        calls=calls,
        task_store=durable,
        starts=starts,
    )
    response_loss = _ReserveBoundaryStopStore(
        durable,
        _ProcessStopped("Operation reserve response lost"),
        commit_before_stop=True,
    )
    first_app = GovernedCommandApplication(
        command_repository=HostCommandRepository(db_path, clock=clock),
        coordinator=_coordinator(
            store=response_loss,
            projections=projections,
            worker=worker,
            owner_id="reserve-loss-owner-a",
        ),
    )

    with pytest.raises(_ProcessStopped):
        await _events(first_app, contracts)

    held = durable.read_scheduler_snapshot("graph-gr03a", 1)
    original_attempt = dict(held["attempts"][0])
    original_precharge = _budget_usage_for_attempt(
        held,
        attempt_id=original_attempt["attempt_id"],
        charge_kind="PRECHARGE",
    )
    operation_before = durable.get_operation_reservation(admitted.reservation_id)
    assert response_loss.reserve_calls == 1
    assert operation_before["status"] == "RESERVED"
    assert operation_before["attempt_id"] == original_attempt["attempt_id"]
    assert calls == []
    assert starts == []

    clock.advance(25)
    reopened = DurableTaskStore(db_path, clock=clock)
    pending = await _events(
        GovernedCommandApplication(
            command_repository=HostCommandRepository(db_path, clock=clock),
            coordinator=_coordinator(
                store=reopened,
                projections=WorkerProjectionStore(db_path, clock=clock),
                worker=worker,
                owner_id="reserve-loss-owner-b",
            ),
        ),
        contracts,
    )

    receipt = pending[-1]["execution_receipt"]
    operation_after = reopened.get_operation_reservation(admitted.reservation_id)
    final = reopened.read_scheduler_snapshot("graph-gr03a", 1)
    assert receipt["status"] == "RECOVERY_REQUIRED"
    assert receipt["accepted"] is False
    assert receipt["projection"]["online_takeover_state"] == (
        "SIGNED_RECONCILIATION_REQUIRED"
    )
    assert operation_after["status"] == "RECOVERY_REQUIRED"
    assert operation_after["status_version"] == operation_before["status_version"] + 1
    assert operation_after["attempt_id"] == original_attempt["attempt_id"]
    assert len(final["attempts"]) == 1
    assert final["attempts"][0]["attempt_id"] == original_attempt["attempt_id"]
    assert _budget_usage_for_attempt(
        final,
        attempt_id=original_attempt["attempt_id"],
        charge_kind="PRECHARGE",
    ) == original_precharge
    assert calls == []
    assert starts == []
    with reopened._connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM task_runtime_lease WHERE owner_id = ?",
            ("reserve-loss-owner-b",),
        ).fetchone()[0] == 0


@pytest.mark.asyncio
async def test_concurrent_preoperation_recovery_has_one_higher_fence_winner(
    tmp_path: Path,
) -> None:
    db_path = _migrated_db(tmp_path)
    contracts = _contracts((("root", ()),))
    clock = _Clock()
    projections = WorkerProjectionStore(db_path, clock=clock)
    durable = DurableTaskStore(db_path, clock=clock)
    calls: list[str] = []
    starts: list[dict[str, Any]] = []
    worker = _ReservationAwareProjectedWorker(
        projections=projections,
        calls=calls,
        task_store=durable,
        starts=starts,
        delay_seconds=0.05,
    )
    first_app = GovernedCommandApplication(
        command_repository=HostCommandRepository(db_path, clock=clock),
        coordinator=_coordinator(
            store=_ReserveBoundaryStopStore(
                durable,
                _ProcessStopped("process stopped before concurrent recovery"),
                commit_before_stop=False,
            ),
            projections=projections,
            worker=worker,
            owner_id="preoperation-race-owner-a",
        ),
    )
    with pytest.raises(_ProcessStopped):
        await _events(first_app, contracts)

    held = durable.read_scheduler_snapshot("graph-gr03a", 1)
    original_attempt = dict(held["attempts"][0])
    original_lease = dict(held["active_leases"][0])
    original_precharge = _budget_usage_for_attempt(
        held,
        attempt_id=original_attempt["attempt_id"],
        charge_kind="PRECHARGE",
    )
    clock.advance(25)

    barrier = threading.Barrier(2)
    apps = [
        GovernedCommandApplication(
            command_repository=HostCommandRepository(db_path, clock=clock),
            coordinator=_coordinator(
                store=_BarrierAcquireStore(
                    DurableTaskStore(db_path, clock=clock),
                    barrier,
                ),
                projections=WorkerProjectionStore(db_path, clock=clock),
                worker=worker,
                owner_id=f"preoperation-race-owner-{suffix}",
            ),
        )
        for suffix in ("b", "c")
    ]

    async def threaded_events(app: GovernedCommandApplication) -> list[dict[str, Any]]:
        return await asyncio.to_thread(lambda: asyncio.run(_events(app, contracts)))

    outcomes = await asyncio.gather(
        *(threaded_events(app) for app in apps),
        return_exceptions=True,
    )
    receipts = [
        outcome[-1]["execution_receipt"]
        for outcome in outcomes
        if isinstance(outcome, list)
    ]

    assert sum(receipt["accepted"] is True for receipt in receipts) == 1
    assert len(outcomes) == 2
    assert sum(isinstance(outcome, BaseException) for outcome in outcomes) <= 1
    assert calls == ["root"]
    assert len(starts) == 1

    reopened = DurableTaskStore(db_path, clock=clock)
    final = reopened.read_scheduler_snapshot("graph-gr03a", 1)
    assert len(final["attempts"]) == 1
    assert final["attempts"][0]["attempt_id"] == original_attempt["attempt_id"]
    assert final["attempts"][0]["status"] == "SUCCEEDED"
    assert _budget_usage_for_attempt(
        final,
        attempt_id=original_attempt["attempt_id"],
        charge_kind="PRECHARGE",
    ) == original_precharge
    lineage = _lease_lineage(
        reopened,
        attempt_id=original_attempt["attempt_id"],
    )
    assert len(lineage) == 2
    assert lineage[0] == (
        "preoperation-race-owner-a",
        original_lease["fencing_token"],
        "EXPIRED",
    )
    assert lineage[1][0] in {
        "preoperation-race-owner-b",
        "preoperation-race-owner-c",
    }
    assert lineage[1][1:] == (
        original_lease["fencing_token"] + 1,
        "RELEASED",
    )
    operation_rows = _operation_rows_for_attempt(
        reopened,
        attempt_id=original_attempt["attempt_id"],
    )
    assert len(operation_rows) == 1
    assert operation_rows[0]["owner_id"] == lineage[1][0]
    assert operation_rows[0]["fencing_token"] == lineage[1][1]
    assert starts[0]["reservation_status"] == "RESERVED"
    assert starts[0]["owner_id"] == lineage[1][0]
    assert starts[0]["fencing_token"] == lineage[1][1]


@pytest.mark.asyncio
async def test_parallel_preoperation_recovery_preserves_accepted_sibling_snapshot(
    tmp_path: Path,
) -> None:
    db_path = _migrated_db(tmp_path)
    contracts = _contracts(
        (
            ("accepted", ()),
            ("recovering", ()),
            ("join", ("accepted", "recovering")),
        ),
        concurrency=2,
    )
    admitted = AdmittedExecution.from_contracts(
        command=contracts[0],
        assignment=contracts[2],
        graph=contracts[3],
    )
    clock = _Clock()
    projections = WorkerProjectionStore(db_path, clock=clock)
    durable = DurableTaskStore(db_path, clock=clock)
    calls: list[str] = []
    starts: list[dict[str, Any]] = []
    worker = _ReservationAwareProjectedWorker(
        projections=projections,
        calls=calls,
        task_store=durable,
        starts=starts,
    )
    first_app = GovernedCommandApplication(
        command_repository=HostCommandRepository(db_path, clock=clock),
        coordinator=_coordinator(
            store=_SiblingStateBoundaryStopStore(
                durable,
                _ProcessStopped("parallel node stopped before Operation reservation"),
                reservation_id=admitted.for_node("recovering").reservation_id,
                sibling_node_id="accepted",
                release_on="ACCEPTED",
            ),
            projections=projections,
            worker=worker,
            owner_id="preoperation-dag-owner-a",
            bounded_fanout_enabled=True,
        ),
    )

    with pytest.raises(_ProcessStopped):
        await _events(first_app, contracts)

    held = durable.read_scheduler_snapshot("graph-gr03a", 1)
    recovering_node = dict(durable.get_node("graph-gr03a", 1, "recovering"))
    accepted_attempt = next(
        dict(row) for row in held["attempts"] if row["node_id"] == "accepted"
    )
    recovering_attempt = next(
        dict(row) for row in held["attempts"] if row["node_id"] == "recovering"
    )
    accepted_snapshot = _accepted_node_snapshot(
        durable,
        projections,
        admitted=admitted,
        node_id="accepted",
        attempt=accepted_attempt,
    )
    recovering_lease = next(
        dict(row)
        for row in held["active_leases"]
        if row["attempt_id"] == recovering_attempt["attempt_id"]
    )
    recovering_precharge = _budget_usage_for_attempt(
        held,
        attempt_id=recovering_attempt["attempt_id"],
        charge_kind="PRECHARGE",
    )
    assert held["graph_record"]["status"] == "ACTIVE"
    assert accepted_snapshot["node"]["status"] == "ACCEPTED"
    assert accepted_attempt["status"] == "SUCCEEDED"
    assert accepted_snapshot["operation"]["status"] == "COMMITTED"
    assert accepted_snapshot["artifact"]["attempt_id"] == accepted_attempt["attempt_id"]
    assert accepted_snapshot["receipt"]["attempt_id"] == accepted_attempt["attempt_id"]
    assert accepted_snapshot["effects"] == []
    assert recovering_node["status"] == "RUNNING"
    assert recovering_attempt["status"] == "RUNNING"
    assert _operation_rows_for_attempt(
        durable,
        attempt_id=recovering_attempt["attempt_id"],
    ) == []
    assert calls == ["accepted"]
    assert [item["reservation_status"] for item in starts] == ["RESERVED"]

    clock.advance(25)
    reopened = DurableTaskStore(db_path, clock=clock)
    resumed = await _events(
        GovernedCommandApplication(
            command_repository=HostCommandRepository(db_path, clock=clock),
            coordinator=_coordinator(
                store=reopened,
                projections=WorkerProjectionStore(db_path, clock=clock),
                worker=worker,
                owner_id="preoperation-dag-owner-b",
                bounded_fanout_enabled=True,
            ),
        ),
        contracts,
    )

    assert resumed[-1]["execution_receipt"]["status"] == "ACCEPTED"
    final = reopened.read_scheduler_snapshot("graph-gr03a", 1)
    assert final["graph_record"]["status"] == "COMPLETED"
    final_accepted_attempt = next(
        dict(row) for row in final["attempts"] if row["node_id"] == "accepted"
    )
    assert _accepted_node_snapshot(
        reopened,
        WorkerProjectionStore(db_path, clock=clock),
        admitted=admitted,
        node_id="accepted",
        attempt=final_accepted_attempt,
    ) == accepted_snapshot
    final_recovering_attempts = [
        dict(row) for row in final["attempts"] if row["node_id"] == "recovering"
    ]
    assert len(final_recovering_attempts) == 1
    assert final_recovering_attempts[0]["attempt_id"] == (
        recovering_attempt["attempt_id"]
    )
    assert final_recovering_attempts[0]["status"] == "SUCCEEDED"
    assert _budget_usage_for_attempt(
        final,
        attempt_id=recovering_attempt["attempt_id"],
        charge_kind="PRECHARGE",
    ) == recovering_precharge
    assert _lease_lineage(
        reopened,
        attempt_id=recovering_attempt["attempt_id"],
    ) == [
        (
            "preoperation-dag-owner-a",
            recovering_lease["fencing_token"],
            "EXPIRED",
        ),
        (
            "preoperation-dag-owner-b",
            recovering_lease["fencing_token"] + 1,
            "RELEASED",
        ),
    ]
    assert calls == ["accepted", "recovering", "join"]
    assert [item["reservation_status"] for item in starts] == [
        "RESERVED",
        "RESERVED",
        "RESERVED",
    ]


@pytest.mark.asyncio
async def test_accepted_precharge_recovery_stops_when_sibling_exceeds_shared_budget(
    tmp_path: Path,
) -> None:
    db_path = _migrated_db(tmp_path)
    contracts = _contracts(
        (
            ("recovering", ()),
            ("budget-sibling", ()),
            ("join", ("recovering", "budget-sibling")),
        ),
        concurrency=2,
        token_limit=100,
    )
    admitted = AdmittedExecution.from_contracts(
        command=contracts[0],
        assignment=contracts[2],
        graph=contracts[3],
    )
    clock = _Clock()
    durable = DurableTaskStore(db_path, clock=clock)
    projections = WorkerProjectionStore(db_path, clock=clock)
    calls: list[str] = []
    worker = _ProjectedWorker(
        projections=projections,
        calls=calls,
        token_counts={"budget-sibling": 101},
    )
    stopped_store = _SiblingStateBoundaryStopStore(
        durable,
        _ProcessStopped("candidate stopped after sibling exceeded shared Budget"),
        reservation_id=admitted.for_node("recovering").reservation_id,
        sibling_node_id="budget-sibling",
        release_on="BUDGET_EXCEEDED",
    )

    with pytest.raises(_ProcessStopped):
        await _events(
            GovernedCommandApplication(
                command_repository=HostCommandRepository(db_path, clock=clock),
                coordinator=_coordinator(
                    store=stopped_store,
                    projections=projections,
                    worker=worker,
                    owner_id="preoperation-budget-owner-a",
                    bounded_fanout_enabled=True,
                ),
            ),
            contracts,
        )

    held = durable.read_scheduler_snapshot(admitted.graph_id, admitted.graph_revision)
    recovering_attempt = next(
        dict(row) for row in held["attempts"] if row["node_id"] == "recovering"
    )
    sibling_attempt = next(
        dict(row)
        for row in held["attempts"]
        if row["node_id"] == "budget-sibling"
    )
    recovering_precharge = _budget_usage_for_attempt(
        held,
        attempt_id=recovering_attempt["attempt_id"],
        charge_kind="PRECHARGE",
    )
    sibling_usage = [
        dict(row)
        for row in held["budget_usage"]
        if row["attempt_id"] == sibling_attempt["attempt_id"]
        and row["charge_kind"].startswith("WORKER_USAGE")
    ]
    sibling_operation = dict(
        durable.get_operation_reservation(
            admitted.for_node("budget-sibling").reservation_id
        )
    )
    committed_operation_outbox = _operation_commit_outbox_count(durable)

    assert held["graph_record"]["status"] == "ACTIVE"
    assert held["budgets"][0]["status"] == "EXCEEDED"
    assert durable.get_node(admitted.graph_id, 1, "recovering")["status"] == "RUNNING"
    assert recovering_attempt["status"] == "RUNNING"
    assert len(recovering_precharge) == 1
    assert recovering_precharge[0]["accepted"] is True
    assert _operation_rows_for_attempt(
        durable,
        attempt_id=recovering_attempt["attempt_id"],
    ) == []
    assert durable.get_node(admitted.graph_id, 1, "budget-sibling")["status"] == "FAILED"
    assert sibling_attempt["status"] == "FAILED"
    assert len(sibling_usage) == 1
    assert sibling_usage[0]["accepted"] is False
    assert sibling_operation["status"] == "COMMITTED"
    assert calls == ["budget-sibling"]

    clock.advance(25)
    reopened = DurableTaskStore(db_path, clock=clock)
    resumed = await _events(
        GovernedCommandApplication(
            command_repository=HostCommandRepository(db_path, clock=clock),
            coordinator=_coordinator(
                store=reopened,
                projections=WorkerProjectionStore(db_path, clock=clock),
                worker=worker,
                owner_id="preoperation-budget-owner-b",
                bounded_fanout_enabled=True,
            ),
        ),
        contracts,
    )

    receipt = resumed[-1]["execution_receipt"]
    final = reopened.read_scheduler_snapshot(admitted.graph_id, admitted.graph_revision)
    final_recovering_attempt = next(
        dict(row) for row in final["attempts"] if row["node_id"] == "recovering"
    )
    assert receipt["status"] == "BUDGET_BLOCKED"
    assert receipt["reason_code"] == "GOVERNED_BUDGET_EXCEEDED"
    assert final["graph_record"]["status"] == "BLOCKED"
    assert final["budgets"][0]["status"] == "RELEASED"
    assert reopened.get_node(admitted.graph_id, 1, "recovering")["status"] == "FAILED"
    assert final_recovering_attempt["attempt_id"] == recovering_attempt["attempt_id"]
    assert final_recovering_attempt["status"] == "FAILED"
    assert _budget_usage_for_attempt(
        final,
        attempt_id=recovering_attempt["attempt_id"],
        charge_kind="PRECHARGE",
    ) == recovering_precharge
    assert _operation_rows_for_attempt(
        reopened,
        attempt_id=recovering_attempt["attempt_id"],
    ) == []
    assert _operation_commit_outbox_count(reopened) == committed_operation_outbox
    assert calls == ["budget-sibling"]


@pytest.mark.parametrize(
    ("sibling_mode", "release_on", "held_status", "final_graph", "final_receipt"),
    (
        ("failed", "FAILED", "FAILED", "FAILED", "FAILED"),
        (
            "review",
            "WAITING_ACCEPTANCE_REVIEW",
            "WAITING_ACCEPTANCE_REVIEW",
            "BLOCKED",
            "WAITING_ACCEPTANCE_REVIEW",
        ),
    ),
)
@pytest.mark.asyncio
async def test_terminal_sibling_does_not_preempt_operation_free_candidate_recovery(
    tmp_path: Path,
    sibling_mode: str,
    release_on: str,
    held_status: str,
    final_graph: str,
    final_receipt: str,
) -> None:
    db_path = _migrated_db(tmp_path)
    sibling_node_id = f"{sibling_mode}-sibling"
    contracts = _contracts(
        (
            ("recovering", ()),
            (sibling_node_id, ()),
            ("join", ("recovering", sibling_node_id)),
        ),
        concurrency=2,
    )
    admitted = AdmittedExecution.from_contracts(
        command=contracts[0],
        assignment=contracts[2],
        graph=contracts[3],
    )
    clock = _Clock()
    durable = DurableTaskStore(db_path, clock=clock)
    projections = WorkerProjectionStore(db_path, clock=clock)
    calls: list[str] = []
    worker = _ProjectedWorker(projections=projections, calls=calls)
    resolver = (
        _SelectedPreparationFailureResolver(
            worker,
            failed_node_id=sibling_node_id,
        )
        if sibling_mode == "failed"
        else _ProductionRouteResolver(worker)
    )
    acceptance = _Acceptance(
        "ACCEPT",
        decisions_by_node=(
            {sibling_node_id: "REVIEW"}
            if sibling_mode == "review"
            else None
        ),
    )
    stopped_store = _SiblingStateBoundaryStopStore(
        durable,
        _ProcessStopped(f"candidate stopped beside {sibling_mode} sibling"),
        reservation_id=admitted.for_node("recovering").reservation_id,
        sibling_node_id=sibling_node_id,
        release_on=release_on,
    )

    with pytest.raises(_ProcessStopped):
        await _events(
            GovernedCommandApplication(
                command_repository=HostCommandRepository(db_path, clock=clock),
                coordinator=_coordinator(
                    store=stopped_store,
                    projections=projections,
                    worker=worker,
                    worker_resolver=resolver,
                    acceptance=acceptance,
                    owner_id=f"preoperation-{sibling_mode}-owner-a",
                    bounded_fanout_enabled=True,
                ),
            ),
            contracts,
        )

    held = durable.read_scheduler_snapshot(admitted.graph_id, admitted.graph_revision)
    recovering_attempt = next(
        dict(row) for row in held["attempts"] if row["node_id"] == "recovering"
    )
    sibling_attempt = next(
        dict(row)
        for row in held["attempts"]
        if row["node_id"] == sibling_node_id
    )
    assert held["graph_record"]["status"] == "ACTIVE"
    assert durable.get_node(admitted.graph_id, 1, "recovering")["status"] == "RUNNING"
    assert recovering_attempt["status"] == "RUNNING"
    assert _operation_rows_for_attempt(
        durable,
        attempt_id=recovering_attempt["attempt_id"],
    ) == []
    assert durable.get_node(admitted.graph_id, 1, sibling_node_id)["status"] == held_status
    assert sibling_attempt["status"] == (
        "FAILED" if sibling_mode == "failed" else "SUCCEEDED"
    )
    assert calls == ([] if sibling_mode == "failed" else [sibling_node_id])

    clock.advance(25)
    reopened = DurableTaskStore(db_path, clock=clock)
    resumed = await _events(
        GovernedCommandApplication(
            command_repository=HostCommandRepository(db_path, clock=clock),
            coordinator=_coordinator(
                store=reopened,
                projections=WorkerProjectionStore(db_path, clock=clock),
                worker=worker,
                worker_resolver=resolver,
                acceptance=acceptance,
                owner_id=f"preoperation-{sibling_mode}-owner-b",
                bounded_fanout_enabled=True,
            ),
        ),
        contracts,
    )

    final = reopened.read_scheduler_snapshot(admitted.graph_id, admitted.graph_revision)
    final_recovering_attempt = next(
        dict(row) for row in final["attempts"] if row["node_id"] == "recovering"
    )
    assert resumed[-1]["execution_receipt"]["status"] == final_receipt
    assert final["graph_record"]["status"] == final_graph
    assert reopened.get_node(admitted.graph_id, 1, "recovering")["status"] == "ACCEPTED"
    assert final_recovering_attempt["attempt_id"] == recovering_attempt["attempt_id"]
    assert final_recovering_attempt["status"] == "SUCCEEDED"
    assert len(
        _operation_rows_for_attempt(
            reopened,
            attempt_id=recovering_attempt["attempt_id"],
        )
    ) == 1
    assert calls == (
        ["recovering"]
        if sibling_mode == "failed"
        else [sibling_node_id, "recovering"]
    )
    assert all(call != "join" for call in calls)


@pytest.mark.asyncio
async def test_rejected_precharge_orphan_closes_before_shared_budget_shortcut(
    tmp_path: Path,
) -> None:
    db_path = _migrated_db(tmp_path)
    command, work_order, assignment, graph = _contracts((("root", ()),))
    work_order = replace(
        work_order,
        budget_policy=replace(
            work_order.budget_policy,
            limits=replace(
                work_order.budget_policy.limits,
                tool_call_count=0,
            ),
        ),
    )
    graph = replace(
        graph,
        nodes=(replace(graph.nodes[0], route="skill:root"),),
    )
    contracts = (command, work_order, assignment, graph)
    clock = _Clock()
    durable = DurableTaskStore(db_path, clock=clock)
    projections = WorkerProjectionStore(db_path, clock=clock)
    calls: list[str] = []
    stopped = _PreReservationTerminalStopStore(
        durable,
        _ProcessStopped("process stopped before rejected PRECHARGE closeout"),
    )

    with pytest.raises(_ProcessStopped):
        await _events(
            GovernedCommandApplication(
                command_repository=HostCommandRepository(db_path, clock=clock),
                coordinator=_coordinator(
                    store=stopped,
                    projections=projections,
                    worker=_ProjectedWorker(projections=projections, calls=calls),
                    owner_id="precharge-reject-owner-a",
                ),
            ),
            contracts,
        )

    held = durable.read_scheduler_snapshot("graph-gr03a", 1)
    attempt = dict(held["attempts"][0])
    rejected_usage = _budget_usage_for_attempt(
        held,
        attempt_id=attempt["attempt_id"],
        charge_kind="PRECHARGE",
    )
    assert held["graph_record"]["status"] == "ACTIVE"
    assert durable.get_node("graph-gr03a", 1, "root")["status"] == "RUNNING"
    assert attempt["status"] == "RUNNING"
    assert held["budgets"][0]["status"] == "EXCEEDED"
    assert len(rejected_usage) == 1
    assert rejected_usage[0]["accepted"] is False
    assert _operation_rows_for_attempt(durable, attempt_id=attempt["attempt_id"]) == []
    assert calls == []

    clock.advance(25)
    reopened = DurableTaskStore(db_path, clock=clock)
    resumed = await _events(
        GovernedCommandApplication(
            command_repository=HostCommandRepository(db_path, clock=clock),
            coordinator=_coordinator(
                store=reopened,
                projections=WorkerProjectionStore(db_path, clock=clock),
                worker=_ProjectedWorker(projections=projections, calls=calls),
                owner_id="precharge-reject-owner-b",
            ),
        ),
        contracts,
    )

    receipt = resumed[-1]["execution_receipt"]
    final = reopened.read_scheduler_snapshot("graph-gr03a", 1)
    assert receipt["status"] == "BUDGET_BLOCKED"
    assert receipt["reason_code"] == "GOVERNED_BUDGET_EXCEEDED"
    assert final["graph_record"]["status"] == "BLOCKED"
    assert reopened.get_node("graph-gr03a", 1, "root")["status"] == "FAILED"
    assert final["attempts"][0]["status"] == "FAILED"
    assert final["budgets"][0]["status"] == "RELEASED"
    assert _budget_usage_for_attempt(
        final,
        attempt_id=attempt["attempt_id"],
        charge_kind="PRECHARGE",
    ) == rejected_usage
    assert calls == []
