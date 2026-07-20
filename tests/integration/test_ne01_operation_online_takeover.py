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
        worker_resolver=_ProductionRouteResolver(worker),
        verifier=verifier or _Verifier(),
        acceptance=_Acceptance("ACCEPT"),
        projection_reader=projections,
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
    reopened_projections = WorkerProjectionStore(db_path, clock=clock)
    verifier = _ContextVerifier()
    reopened_app = GovernedCommandApplication(
        command_repository=HostCommandRepository(db_path, clock=clock),
        coordinator=_coordinator(
            store=reopened_store,
            projections=reopened_projections,
            worker=worker,
            owner_id="online-owner-b",
            verifier=verifier,
        ),
    )

    resumed = await _events(reopened_app, contracts)

    assert resumed[-1]["execution_receipt"]["accepted"] is True
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
    resumed = await _events(
        GovernedCommandApplication(
            command_repository=HostCommandRepository(db_path, clock=clock),
            coordinator=_coordinator(
                store=reopened,
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
async def test_multi_node_committed_checkpoint_returns_batch_recovery_pending(
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
async def test_reserved_operation_expires_online_without_restart_or_worker_replay(
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
    assert before["status"] == "RESERVED"
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
    assert recovered["status_version"] == before["status_version"] + 1
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
