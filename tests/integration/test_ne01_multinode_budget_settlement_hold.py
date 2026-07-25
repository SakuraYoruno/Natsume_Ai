from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from host_brain.orchestration import GovernedCommandApplication
from host_brain.contracts.agent_runtime.task_graph import (
    TaskGraphCompletionPolicy,
)
from host_brain.persistence.host_command_repository import HostCommandRepository
from host_brain.persistence.task_repository import DurableTaskStore
from host_brain.persistence.task_store_types import (
    TASK_STORE_BUDGET_ALREADY_EXCEEDED,
    TASK_STORE_BUDGET_EXCEEDED,
    TASK_STORE_CAS_CONFLICT,
)
from host_brain.persistence.worker_projection_repository import WorkerProjectionStore
from host_brain.task_runtime import AdmittedExecution
from tests.integration.test_gr03a_governed_durable_dag import (
    _migrated_db,
)
from tests.integration.test_ne01_operation_online_takeover import (
    _Clock,
    _ProjectedWorker,
    _coordinator,
    _contracts,
    _events,
)


class _SelectedBudgetSettlementDoubleLossStore:
    """Commit one worker usage, then lose its response and first exact read."""

    def __init__(
        self,
        delegate: DurableTaskStore,
        *,
        node_id: str,
    ) -> None:
        self._delegate = delegate
        self._node_id = node_id
        self.consume_failed = False
        self.read_failed = False

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)

    def consume_budget_once(self, *args: Any, **kwargs: Any) -> Any:
        if (
            not self.consume_failed
            and str(kwargs.get("node_id") or "") == self._node_id
            and str(kwargs.get("charge_kind") or "").startswith("WORKER_USAGE")
        ):
            self.consume_failed = True
            self._delegate.consume_budget_once(*args, **kwargs)
            raise TimeoutError("simulated worker BudgetUsage response loss")
        return self._delegate.consume_budget_once(*args, **kwargs)

    def get_budget_usage(self, *args: Any, **kwargs: Any) -> Any:
        if self.consume_failed and not self.read_failed:
            self.read_failed = True
            raise TimeoutError("simulated first exact BudgetUsage read-back loss")
        return self._delegate.get_budget_usage(*args, **kwargs)


class _SelectedMissingBudgetUsageStore:
    """Lose the first worker-usage write before it reaches durable storage."""

    def __init__(
        self,
        delegate: DurableTaskStore,
        *,
        node_id: str,
    ) -> None:
        self._delegate = delegate
        self._node_id = node_id
        self.consume_failed = False

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)

    def consume_budget_once(self, *args: Any, **kwargs: Any) -> Any:
        if (
            not self.consume_failed
            and str(kwargs.get("node_id") or "") == self._node_id
            and str(kwargs.get("charge_kind") or "").startswith("WORKER_USAGE")
        ):
            self.consume_failed = True
            raise TimeoutError("simulated pre-persistence worker BudgetUsage loss")
        return self._delegate.consume_budget_once(*args, **kwargs)


class _ParallelCommittedCheckpointDoubleLossStore:
    """Commit every selected Operation before losing both Budget responses."""

    def __init__(
        self,
        delegate: DurableTaskStore,
        *,
        node_ids: set[str],
        consume_order: tuple[str, ...] = (),
        missing_usage_nodes: set[str] | None = None,
    ) -> None:
        self._delegate = delegate
        self._node_ids = frozenset(node_ids)
        self._consume_order = tuple(consume_order)
        self._missing_usage_nodes = frozenset(missing_usage_nodes or set())
        self._committed_nodes: set[str] = set()
        self._all_committed = asyncio.Event()
        self._consumed_events = {
            node_id: asyncio.Event() for node_id in self._consume_order
        }
        self.consume_failed_nodes: set[str] = set()
        self.read_failed_nodes: set[str] = set()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)

    async def commit_operation(
        self,
        reservation_id: str,
        **kwargs: Any,
    ) -> Any:
        result = self._delegate.commit_operation(reservation_id, **kwargs)
        attempt = self._delegate.get_attempt(str(result["attempt_id"]))
        node_id = str(attempt["node_id"])
        if node_id in self._node_ids:
            self._committed_nodes.add(node_id)
            if self._committed_nodes == set(self._node_ids):
                self._all_committed.set()
            await asyncio.wait_for(self._all_committed.wait(), timeout=5.0)
        return result

    async def consume_budget_once(self, *args: Any, **kwargs: Any) -> Any:
        node_id = str(kwargs.get("node_id") or "")
        if node_id in self._consume_order:
            position = self._consume_order.index(node_id)
            if position:
                await asyncio.wait_for(
                    self._consumed_events[
                        self._consume_order[position - 1]
                    ].wait(),
                    timeout=5.0,
                )
        if (
            node_id in self._node_ids
            and node_id not in self.consume_failed_nodes
            and str(kwargs.get("charge_kind") or "").startswith("WORKER_USAGE")
        ):
            self.consume_failed_nodes.add(node_id)
            event = self._consumed_events.get(node_id)
            if node_id in self._missing_usage_nodes:
                if event is not None:
                    event.set()
                raise TimeoutError(
                    f"simulated pre-persistence worker BudgetUsage loss for {node_id}"
                )
            try:
                self._delegate.consume_budget_once(*args, **kwargs)
            except Exception as exc:
                if getattr(exc, "reason_code", None) == TASK_STORE_CAS_CONFLICT:
                    self.consume_failed_nodes.remove(node_id)
                raise
            if event is not None:
                event.set()
            raise TimeoutError(
                f"simulated worker BudgetUsage response loss for {node_id}"
            )
        result = self._delegate.consume_budget_once(*args, **kwargs)
        event = self._consumed_events.get(node_id)
        if event is not None:
            event.set()
        return result

    def get_budget_usage(self, *args: Any, **kwargs: Any) -> Any:
        usage_id = str(
            args[1]
            if len(args) > 1
            else kwargs.get("usage_id") or ""
        )
        for node_id in self._node_ids:
            if (
                node_id in self.consume_failed_nodes
                and node_id not in self.read_failed_nodes
                and f"://{node_id}/" in usage_id
            ):
                self.read_failed_nodes.add(node_id)
                raise TimeoutError(
                    f"simulated first BudgetUsage read loss for {node_id}"
                )
        return self._delegate.get_budget_usage(*args, **kwargs)


class _CheckpointNodeGrantService:
    def validate_parent_binding(self, **_kwargs: Any) -> bool:
        return True

    def derive(
        self,
        *,
        parent_grant_ref: str,
        task_graph: Any,
        **_kwargs: Any,
    ) -> SimpleNamespace:
        return SimpleNamespace(
            parent_grant_ref=parent_grant_ref,
            task_graph_id=task_graph.graph_id,
            graph_revision=task_graph.revision,
            grant_refs={
                node.node_id: f"governor-grant://checkpoint/{node.node_id}"
                for node in task_graph.nodes
            },
        )


class _CheckpointNodeGrantVerifier:
    def verify_for_claim(self, **kwargs: Any) -> dict[str, Any]:
        return dict(kwargs)


class _TakeoverOrderStore:
    def __init__(self, delegate: DurableTaskStore) -> None:
        self._delegate = delegate
        self.node_ids: list[str] = []

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)

    def acquire_lease(self, attempt_id: str, **kwargs: Any) -> Any:
        if kwargs.get("existing_operation_reservation_id"):
            attempt = self._delegate.get_attempt(attempt_id)
            self.node_ids.append(str(attempt["node_id"]))
        return self._delegate.acquire_lease(attempt_id, **kwargs)


class _SnapshotWithoutSiblingRejectionStore:
    """Simulate a stale/corrupt read that cannot prove the EXCEEDED winner."""

    def __init__(self, delegate: DurableTaskStore) -> None:
        self._delegate = delegate

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)

    def read_scheduler_snapshot(
        self,
        graph_id: str,
        graph_revision: int,
    ) -> dict[str, Any]:
        snapshot = dict(
            self._delegate.read_scheduler_snapshot(
                graph_id,
                graph_revision,
            )
        )
        snapshot["budget_usage"] = [
            dict(row)
            for row in snapshot["budget_usage"]
            if row.get("accepted") is not False
        ]
        return snapshot


class _SnapshotWithDriftedBudgetWinnerStore:
    """Break the immutable winner/sibling Outbox binding on a read snapshot."""

    def __init__(self, delegate: DurableTaskStore, *, drift: str) -> None:
        self._delegate = delegate
        self._drift = drift

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)

    def read_scheduler_snapshot(
        self,
        graph_id: str,
        graph_revision: int,
    ) -> dict[str, Any]:
        snapshot = dict(
            self._delegate.read_scheduler_snapshot(
                graph_id,
                graph_revision,
            )
        )
        snapshot["budget_usage"] = [
            {
                **dict(row),
                **(
                    (
                        {"outbox_id": "outbox://tampered/budget-winner"}
                        if self._drift == "outbox"
                        else {"node_id": "foreign-root"}
                    )
                    if (
                        row.get("reason_code") == TASK_STORE_BUDGET_EXCEEDED
                    )
                    else {}
                ),
            }
            for row in snapshot["budget_usage"]
        ]
        return snapshot


class _ElapsedProjectedWorker(_ProjectedWorker):
    """Advance the injected durable clock before COMMIT observes worker time."""

    def __init__(self, *, clock: _Clock, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._clock = clock

    async def execute_stream(self, **kwargs: Any):
        async for event in super().execute_stream(**kwargs):
            self._clock.advance(17)
            yield event


_FORBIDDEN_HOLD_OUTBOX_EVENTS = frozenset(
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


def _forbidden_hold_outbox_events(store: DurableTaskStore) -> set[str]:
    with store._connect() as conn:
        persisted = {
            str(row[0])
            for row in conn.execute(
                "SELECT event_type FROM task_runtime_outbox"
            ).fetchall()
        }
    # BUDGET_CONSUMED is deliberately allowed: the exact immutable usage row
    # is the write whose response became ambiguous; it is not a lifecycle
    # terminal transition.
    return persisted & _FORBIDDEN_HOLD_OUTBOX_EVENTS


@pytest.mark.parametrize(
    (
        "token_limit",
        "initial_budget_status",
        "usage_accepted",
        "expected_final_status",
        "expected_reason_code",
        "expected_graph_status",
        "expected_budget_status",
        "expected_root_b_status",
        "expected_calls",
        "expected_usage_count",
        "expected_operation_commits",
    ),
    (
        (
            100,
            "ACTIVE",
            True,
            "ACCEPTED",
            None,
            "COMPLETED",
            "CONSUMED",
            "ACCEPTED",
            ["root-a", "root-b"],
            2,
            2,
        ),
        (
            5,
            "EXCEEDED",
            False,
            "BUDGET_BLOCKED",
            "GOVERNED_BUDGET_EXCEEDED",
            "BLOCKED",
            "RELEASED",
            "READY",
            ["root-a"],
            1,
            1,
        ),
    ),
)
@pytest.mark.asyncio
async def test_multinode_post_commit_budget_ambiguity_recovers_without_worker_replay(
    tmp_path: Path,
    token_limit: int,
    initial_budget_status: str,
    usage_accepted: bool,
    expected_final_status: str,
    expected_reason_code: str | None,
    expected_graph_status: str,
    expected_budget_status: str,
    expected_root_b_status: str,
    expected_calls: list[str],
    expected_usage_count: int,
    expected_operation_commits: int,
) -> None:
    db_path = _migrated_db(tmp_path)
    contracts = _contracts(
        (("root-a", ()), ("root-b", ())),
        concurrency=2,
        token_limit=token_limit,
    )
    admitted = AdmittedExecution.from_contracts(
        command=contracts[0],
        assignment=contracts[2],
        graph=contracts[3],
    )
    root_a = admitted.for_node("root-a")
    clock = _Clock()
    projections = WorkerProjectionStore(db_path, clock=clock)
    calls: list[str] = []
    worker = _ProjectedWorker(projections=projections, calls=calls)
    durable = DurableTaskStore(db_path, clock=clock)
    uncertain_store = _SelectedBudgetSettlementDoubleLossStore(
        durable,
        node_id="root-a",
    )
    first_app = GovernedCommandApplication(
        command_repository=HostCommandRepository(db_path, clock=clock),
        coordinator=_coordinator(
            store=uncertain_store,
            projections=projections,
            worker=worker,
            owner_id="batch-budget-owner-a",
            bounded_fanout_enabled=True,
        ),
    )

    first = await _events(first_app, contracts)
    first_receipt = first[-1]["execution_receipt"]

    assert first_receipt["status"] == "RECOVERY_REQUIRED"
    assert first_receipt["reason_code"] == "GOVERNED_OPERATION_RECOVERY_REQUIRED"
    assert first_receipt["projection"]["budget_settlement_state"] == "UNKNOWN"
    assert first_receipt["projection"]["operation_status"] == "COMMITTED"
    assert first_receipt["projection"]["worker_replayed"] is False
    assert uncertain_store.consume_failed is True
    assert uncertain_store.read_failed is True
    assert calls == ["root-a"]

    held = durable.read_scheduler_snapshot("graph-gr03a", 1)
    original_attempt = held["attempts"][0]
    original_lease = held["active_leases"][0]
    original_operation = durable.get_operation_reservation(
        root_a.reservation_id
    )
    worker_usages = [
        row
        for row in held["budget_usage"]
        if row["charge_kind"].startswith("WORKER_USAGE")
    ]

    assert original_operation["status"] == "COMMITTED"
    assert held["graph_record"]["status"] == "ACTIVE"
    assert durable.get_node("graph-gr03a", 1, "root-a")["status"] == "RUNNING"
    assert durable.get_node("graph-gr03a", 1, "root-b")["status"] == "READY"
    assert original_attempt["node_id"] == "root-a"
    assert original_attempt["status"] == "RUNNING"
    assert original_lease["owner_id"] == "batch-budget-owner-a"
    assert held["budgets"][0]["status"] == initial_budget_status
    assert len(worker_usages) == 1
    assert worker_usages[0]["usage_id"] == "worker-usage://root-a/online-takeover"
    assert worker_usages[0]["attempt_id"] == original_attempt["attempt_id"]
    assert worker_usages[0]["accepted"] is usage_accepted
    assert _forbidden_hold_outbox_events(durable) == set()

    clock.advance(25)
    reopened = DurableTaskStore(db_path, clock=clock)
    direct_coordinator = _coordinator(
        store=reopened,
        projections=WorkerProjectionStore(db_path, clock=clock),
        worker=worker,
        owner_id="direct-node-caller",
        bounded_fanout_enabled=True,
    )
    direct = [
        event
        async for event in direct_coordinator._node_execution.execute_stream(  # noqa: SLF001
            admitted=admitted.for_node("root-a"),
            command=contracts[0],
            work_order=contracts[1],
        )
    ]
    direct_receipt = direct[-1]["execution_receipt"]
    assert direct_receipt["status"] == "RECOVERY_REQUIRED"
    assert direct_receipt["projection"]["online_takeover_state"] == (
        "BATCH_RECOVERY_PENDING"
    )
    assert calls == ["root-a"]

    second_app = GovernedCommandApplication(
        command_repository=HostCommandRepository(db_path, clock=clock),
        coordinator=_coordinator(
            store=reopened,
            projections=WorkerProjectionStore(db_path, clock=clock),
            worker=worker,
            owner_id="batch-budget-owner-b",
            bounded_fanout_enabled=True,
        ),
    )

    second = await _events(second_app, contracts)
    second_receipt = second[-1]["execution_receipt"]

    assert second_receipt["status"] == expected_final_status
    assert second_receipt["reason_code"] == expected_reason_code
    assert calls == expected_calls
    assert calls.count("root-a") == 1

    final = reopened.read_scheduler_snapshot("graph-gr03a", 1)
    final_operation = reopened.get_operation_reservation(root_a.reservation_id)
    final_worker_usages = [
        row
        for row in final["budget_usage"]
        if row["charge_kind"].startswith("WORKER_USAGE")
    ]
    with reopened._connect() as conn:
        operation_commits = conn.execute(
            "SELECT COUNT(*) FROM task_runtime_outbox "
            "WHERE event_type = 'OPERATION_COMMITTED'"
        ).fetchone()[0]

    assert len(final["attempts"]) == expected_usage_count
    assert final_operation["status"] == "COMMITTED"
    assert final_operation["receipt_ref"] == original_operation["receipt_ref"]
    assert final["active_leases"] == []
    assert len(final_worker_usages) == expected_usage_count
    assert final["graph_record"]["status"] == expected_graph_status
    assert reopened.get_node("graph-gr03a", 1, "root-a")["status"] in {
        "ACCEPTED",
        "FAILED",
    }
    assert (
        reopened.get_node("graph-gr03a", 1, "root-b")["status"]
        == expected_root_b_status
    )
    assert final["budgets"][0]["status"] == expected_budget_status
    assert operation_commits == expected_operation_commits

    replay = await _events(second_app, contracts)
    assert replay[-1]["execution_receipt"]["status"] == expected_final_status
    assert calls == expected_calls


@pytest.mark.asyncio
async def test_multinode_missing_budget_usage_settles_after_fenced_restart_without_worker_replay(
    tmp_path: Path,
) -> None:
    db_path = _migrated_db(tmp_path)
    contracts = _contracts(
        (("root-a", ()), ("root-b", ())),
        concurrency=2,
        token_limit=100,
    )
    clock = _Clock()
    projections = WorkerProjectionStore(db_path, clock=clock)
    calls: list[str] = []
    worker = _ElapsedProjectedWorker(
        clock=clock,
        projections=projections,
        calls=calls,
    )
    durable = DurableTaskStore(db_path, clock=clock)
    missing_store = _SelectedMissingBudgetUsageStore(
        durable,
        node_id="root-a",
    )
    first_app = GovernedCommandApplication(
        command_repository=HostCommandRepository(db_path, clock=clock),
        coordinator=_coordinator(
            store=missing_store,
            projections=projections,
            worker=worker,
            owner_id="batch-missing-owner-a",
            bounded_fanout_enabled=True,
        ),
    )

    first = await _events(first_app, contracts)

    assert first[-1]["execution_receipt"]["status"] == "RECOVERY_REQUIRED"
    assert missing_store.consume_failed is True
    assert calls == ["root-a"]
    held = durable.read_scheduler_snapshot("graph-gr03a", 1)
    assert [
        row
        for row in held["budget_usage"]
        if row["charge_kind"].startswith("WORKER_USAGE")
    ] == []

    clock.advance(25)
    reopened = DurableTaskStore(db_path, clock=clock)
    second_app = GovernedCommandApplication(
        command_repository=HostCommandRepository(db_path, clock=clock),
        coordinator=_coordinator(
            store=reopened,
            projections=WorkerProjectionStore(db_path, clock=clock),
            worker=worker,
            owner_id="batch-missing-owner-b",
            bounded_fanout_enabled=True,
        ),
    )

    recovered = await _events(second_app, contracts)

    assert recovered[-1]["execution_receipt"]["status"] == "ACCEPTED"
    assert calls == ["root-a", "root-b"]
    snapshot = reopened.read_scheduler_snapshot("graph-gr03a", 1)
    assert snapshot["graph_record"]["status"] == "COMPLETED"
    assert snapshot["budgets"][0]["status"] == "CONSUMED"
    worker_usage = [
        row
        for row in snapshot["budget_usage"]
        if row["charge_kind"].startswith("WORKER_USAGE")
    ]
    assert len(worker_usage) == 2
    assert {row["node_id"] for row in worker_usage} == {"root-a", "root-b"}

    replay = await _events(second_app, contracts)
    assert replay[-1]["execution_receipt"]["status"] == "ACCEPTED"
    assert calls == ["root-a", "root-b"]


@pytest.mark.parametrize(
    (
        "token_limit",
        "expected_terminal_status",
        "expected_graph_status",
        "expected_budget_status",
        "expected_node_statuses",
    ),
    (
        (
            100,
            "ACCEPTED",
            "COMPLETED",
            "CONSUMED",
            {"ACCEPTED"},
        ),
        (
            15,
            "BUDGET_BLOCKED",
            "BLOCKED",
            "RELEASED",
            {"ACCEPTED", "FAILED"},
        ),
    ),
)
@pytest.mark.asyncio
async def test_two_committed_checkpoints_recover_in_deterministic_graph_order(
    tmp_path: Path,
    token_limit: int,
    expected_terminal_status: str,
    expected_graph_status: str,
    expected_budget_status: str,
    expected_node_statuses: set[str],
) -> None:
    db_path = _migrated_db(tmp_path)
    contracts = _contracts(
        (("root-a", ()), ("root-b", ())),
        concurrency=2,
        token_limit=token_limit,
        completion_policy=TaskGraphCompletionPolicy.ATOMIC_GRAPH_SUCCESS_V1,
    )
    contracts = (
        replace(
            contracts[0],
            governor_grant_ref="governor-grant://checkpoint/parent",
        ),
        *contracts[1:],
    )
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
        delay_seconds=0.01,
    )
    durable = DurableTaskStore(db_path, clock=clock)
    uncertain_store = _ParallelCommittedCheckpointDoubleLossStore(
        durable,
        node_ids={"root-a", "root-b"},
        consume_order=("root-a", "root-b"),
    )
    interrupted = GovernedCommandApplication(
        command_repository=HostCommandRepository(db_path, clock=clock),
        coordinator=_coordinator(
            store=uncertain_store,
            projections=projections,
            worker=worker,
            owner_id="batch-two-checkpoints-owner-a",
            bounded_fanout_enabled=True,
            atomic_graph_success_enabled=True,
            node_grant_verifier=_CheckpointNodeGrantVerifier(),
        ),
        node_grant_service=_CheckpointNodeGrantService(),
    )

    first = await _events(interrupted, contracts)

    assert first[-1]["execution_receipt"]["status"] == "RECOVERY_REQUIRED"
    assert set(calls) == {"root-a", "root-b"}
    assert uncertain_store.consume_failed_nodes == {"root-a", "root-b"}
    assert uncertain_store.read_failed_nodes == {"root-a", "root-b"}
    held = durable.read_scheduler_snapshot(
        admitted.graph_id,
        admitted.graph_revision,
    )
    assert held["graph_record"]["status"] == "ACTIVE"
    assert {
        row["node_id"]
        for row in held["attempts"]
        if row["status"] == "RUNNING"
    } == {"root-a", "root-b"}
    assert {
        durable.get_operation_reservation(
            admitted.for_node(node_id).reservation_id
        )["status"]
        for node_id in ("root-a", "root-b")
    } == {"COMMITTED"}
    assert len(
        [
            row
            for row in held["budget_usage"]
            if row["charge_kind"].startswith("WORKER_USAGE")
        ]
    ) == 2
    assert _forbidden_hold_outbox_events(durable) == set()

    clock.advance(25)
    if token_limit == 15:
        missing_sibling_evidence_store = (
            _SnapshotWithoutSiblingRejectionStore(
                DurableTaskStore(db_path, clock=clock)
            )
        )
        fail_closed = GovernedCommandApplication(
            command_repository=HostCommandRepository(db_path, clock=clock),
            coordinator=_coordinator(
                store=missing_sibling_evidence_store,
                projections=WorkerProjectionStore(db_path, clock=clock),
                worker=worker,
                owner_id="batch-two-checkpoints-missing-evidence",
                bounded_fanout_enabled=True,
                atomic_graph_success_enabled=True,
                node_grant_verifier=_CheckpointNodeGrantVerifier(),
            ),
            node_grant_service=_CheckpointNodeGrantService(),
        )

        rejected_recovery = await _events(fail_closed, contracts)

        assert (
            rejected_recovery[-1]["execution_receipt"]["status"]
            == "RECOVERY_REQUIRED"
        )
        with durable._connect() as connection:
            assert (
                connection.execute(
                    """
                    SELECT COUNT(*) FROM task_runtime_lease
                    WHERE owner_id = ?
                    """,
                    ("batch-two-checkpoints-missing-evidence",),
                ).fetchone()[0]
                == 0
            )

    reopened_a = DurableTaskStore(db_path, clock=clock)
    reopened_b = DurableTaskStore(db_path, clock=clock)
    takeover_store_a = _TakeoverOrderStore(reopened_a)
    takeover_store_b = _TakeoverOrderStore(reopened_b)
    resumed_a = GovernedCommandApplication(
        command_repository=HostCommandRepository(db_path, clock=clock),
        coordinator=_coordinator(
            store=takeover_store_a,
            projections=WorkerProjectionStore(db_path, clock=clock),
            worker=worker,
            owner_id="batch-two-checkpoints-owner-b",
            bounded_fanout_enabled=True,
            atomic_graph_success_enabled=True,
            node_grant_verifier=_CheckpointNodeGrantVerifier(),
        ),
        node_grant_service=_CheckpointNodeGrantService(),
    )
    resumed_b = GovernedCommandApplication(
        command_repository=HostCommandRepository(db_path, clock=clock),
        coordinator=_coordinator(
            store=takeover_store_b,
            projections=WorkerProjectionStore(db_path, clock=clock),
            worker=worker,
            owner_id="batch-two-checkpoints-owner-c",
            bounded_fanout_enabled=True,
            atomic_graph_success_enabled=True,
            node_grant_verifier=_CheckpointNodeGrantVerifier(),
        ),
        node_grant_service=_CheckpointNodeGrantService(),
    )

    recovered_a, recovered_b = await asyncio.gather(
        _events(resumed_a, contracts),
        _events(resumed_b, contracts),
    )

    recovered_statuses = {
        recovered_a[-1]["execution_receipt"]["status"],
        recovered_b[-1]["execution_receipt"]["status"],
    }
    assert expected_terminal_status in recovered_statuses
    assert recovered_statuses <= {
        expected_terminal_status,
        "RECOVERY_REQUIRED",
    }
    assert sorted(calls) == ["root-a", "root-b"]
    final = reopened_a.read_scheduler_snapshot(
        admitted.graph_id,
        admitted.graph_revision,
    )
    assert final["graph_record"]["status"] == expected_graph_status
    assert final["budgets"][0]["status"] == expected_budget_status
    assert {
        reopened_a.get_node(
            admitted.graph_id,
            admitted.graph_revision,
            node_id,
        )["status"]
        for node_id in ("root-a", "root-b")
    } == expected_node_statuses
    assert final["active_leases"] == []
    takeover_orders = (
        takeover_store_a.node_ids,
        takeover_store_b.node_ids,
    )
    assert any(order == ["root-a", "root-b"] for order in takeover_orders)
    assert all(not order or order[0] == "root-a" for order in takeover_orders)

    replay = await _events(resumed_a, contracts)
    assert replay[-1]["execution_receipt"]["status"] == expected_terminal_status
    assert sorted(calls) == ["root-a", "root-b"]


@pytest.mark.asyncio
async def test_four_committed_checkpoints_recover_one_shared_budget_decision(
    tmp_path: Path,
) -> None:
    db_path = _migrated_db(tmp_path)
    node_ids = ("root-a", "root-b", "root-c", "root-d")
    contracts = _contracts(
        tuple((node_id, ()) for node_id in node_ids),
        concurrency=4,
        token_limit=25,
        completion_policy=TaskGraphCompletionPolicy.ATOMIC_GRAPH_SUCCESS_V1,
    )
    contracts = (
        replace(
            contracts[0],
            governor_grant_ref="governor-grant://checkpoint/parent",
        ),
        *contracts[1:],
    )
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
        delay_seconds=0.01,
    )
    durable = DurableTaskStore(db_path, clock=clock)
    uncertain = _ParallelCommittedCheckpointDoubleLossStore(
        durable,
        node_ids=set(node_ids),
        consume_order=node_ids,
    )
    first_app = GovernedCommandApplication(
        command_repository=HostCommandRepository(db_path, clock=clock),
        coordinator=_coordinator(
            store=uncertain,
            projections=projections,
            worker=worker,
            owner_id="four-checkpoints-owner-a",
            bounded_fanout_enabled=True,
            atomic_graph_success_enabled=True,
            node_grant_verifier=_CheckpointNodeGrantVerifier(),
        ),
        node_grant_service=_CheckpointNodeGrantService(),
    )

    first = await _events(first_app, contracts)

    assert first[-1]["execution_receipt"]["status"] == "RECOVERY_REQUIRED"
    assert set(calls) == set(node_ids)
    assert uncertain.consume_failed_nodes == set(node_ids)
    assert uncertain.read_failed_nodes == set(node_ids)
    held = durable.read_scheduler_snapshot(
        admitted.graph_id,
        admitted.graph_revision,
    )
    worker_usages = [
        row
        for row in held["budget_usage"]
        if str(row["charge_kind"]).startswith("WORKER_USAGE")
    ]
    assert len(worker_usages) == 4
    assert sum(row["accepted"] is True for row in worker_usages) == 2
    assert sum(
        row["reason_code"] == TASK_STORE_BUDGET_EXCEEDED
        for row in worker_usages
    ) == 1
    assert sum(
        row["reason_code"] == TASK_STORE_BUDGET_ALREADY_EXCEEDED
        for row in worker_usages
    ) == 1

    clock.advance(25)
    reopened = DurableTaskStore(db_path, clock=clock)
    recovered_app = GovernedCommandApplication(
        command_repository=HostCommandRepository(db_path, clock=clock),
        coordinator=_coordinator(
            store=reopened,
            projections=WorkerProjectionStore(db_path, clock=clock),
            worker=worker,
            owner_id="four-checkpoints-owner-b",
            bounded_fanout_enabled=True,
            atomic_graph_success_enabled=True,
            node_grant_verifier=_CheckpointNodeGrantVerifier(),
        ),
        node_grant_service=_CheckpointNodeGrantService(),
    )

    recovered = await _events(recovered_app, contracts)

    assert recovered[-1]["execution_receipt"]["status"] == "BUDGET_BLOCKED"
    assert sorted(calls) == sorted(node_ids)
    final = reopened.read_scheduler_snapshot(
        admitted.graph_id,
        admitted.graph_revision,
    )
    assert final["graph_record"]["status"] == "BLOCKED"
    assert final["budgets"][0]["status"] == "RELEASED"
    assert final["active_leases"] == []
    assert {
        node_id: reopened.get_node(
            admitted.graph_id,
            admitted.graph_revision,
            node_id,
        )["status"]
        for node_id in node_ids
    } == {
        "root-a": "ACCEPTED",
        "root-b": "ACCEPTED",
        "root-c": "FAILED",
        "root-d": "FAILED",
    }

    replay = await _events(recovered_app, contracts)
    assert replay[-1]["execution_receipt"]["status"] == "BUDGET_BLOCKED"
    assert sorted(calls) == sorted(node_ids)


@pytest.mark.asyncio
async def test_missing_checkpoint_after_sibling_budget_exceeded_is_fenced_and_exactly_replayed(
    tmp_path: Path,
) -> None:
    db_path = _migrated_db(tmp_path)
    contracts = _contracts(
        (("root-a", ()), ("root-b", ())),
        concurrency=2,
        token_limit=5,
        completion_policy=TaskGraphCompletionPolicy.ATOMIC_GRAPH_SUCCESS_V1,
    )
    contracts = (
        replace(
            contracts[0],
            governor_grant_ref="governor-grant://checkpoint/parent",
        ),
        *contracts[1:],
    )
    admitted = AdmittedExecution.from_contracts(
        command=contracts[0],
        assignment=contracts[2],
        graph=contracts[3],
    )
    clock = _Clock()
    projections = WorkerProjectionStore(db_path, clock=clock)
    calls: list[str] = []
    worker = _ElapsedProjectedWorker(
        clock=clock,
        projections=projections,
        calls=calls,
        delay_seconds=0.01,
    )
    durable = DurableTaskStore(db_path, clock=clock)
    initial_loss = _ParallelCommittedCheckpointDoubleLossStore(
        durable,
        node_ids={"root-a", "root-b"},
        consume_order=("root-b", "root-a"),
        missing_usage_nodes={"root-a"},
    )
    first_app = GovernedCommandApplication(
        command_repository=HostCommandRepository(db_path, clock=clock),
        coordinator=_coordinator(
            store=initial_loss,
            projections=projections,
            worker=worker,
            owner_id="missing-after-exceeded-owner-a",
            lease_ttl_ms=100,
            bounded_fanout_enabled=True,
            atomic_graph_success_enabled=True,
            node_grant_verifier=_CheckpointNodeGrantVerifier(),
        ),
        node_grant_service=_CheckpointNodeGrantService(),
    )

    first = await _events(first_app, contracts)

    assert first[-1]["execution_receipt"]["status"] == "RECOVERY_REQUIRED"
    assert set(calls) == {"root-a", "root-b"}
    assert initial_loss.consume_failed_nodes == {"root-a", "root-b"}
    assert initial_loss.read_failed_nodes == {"root-a", "root-b"}
    held = durable.read_scheduler_snapshot(
        admitted.graph_id,
        admitted.graph_revision,
    )
    worker_usages = [
        row
        for row in held["budget_usage"]
        if str(row["charge_kind"]).startswith("WORKER_USAGE")
    ]
    assert held["budgets"][0]["status"] == "EXCEEDED"
    assert len(worker_usages) == 1
    assert worker_usages[0]["node_id"] == "root-b"
    assert worker_usages[0]["reason_code"] == TASK_STORE_BUDGET_EXCEEDED

    clock.advance(125)
    second_durable = DurableTaskStore(db_path, clock=clock)
    second_loss = _SelectedBudgetSettlementDoubleLossStore(
        second_durable,
        node_id="root-a",
    )
    second_app = GovernedCommandApplication(
        command_repository=HostCommandRepository(db_path, clock=clock),
        coordinator=_coordinator(
            store=second_loss,
            projections=WorkerProjectionStore(db_path, clock=clock),
            worker=worker,
            owner_id="missing-after-exceeded-owner-b",
            lease_ttl_ms=100,
            bounded_fanout_enabled=True,
            atomic_graph_success_enabled=True,
            node_grant_verifier=_CheckpointNodeGrantVerifier(),
        ),
        node_grant_service=_CheckpointNodeGrantService(),
    )

    second = await _events(second_app, contracts)

    assert second[-1]["execution_receipt"]["status"] == "RECOVERY_REQUIRED"
    assert second_loss.consume_failed is True
    assert second_loss.read_failed is True
    after_delayed_charge = second_durable.read_scheduler_snapshot(
        admitted.graph_id,
        admitted.graph_revision,
    )
    delayed_usage = next(
        row
        for row in after_delayed_charge["budget_usage"]
        if row["node_id"] == "root-a"
        and str(row["charge_kind"]).startswith("WORKER_USAGE")
    )
    winner_usage = next(
        row
        for row in after_delayed_charge["budget_usage"]
        if row["node_id"] == "root-b"
        and str(row["charge_kind"]).startswith("WORKER_USAGE")
    )
    assert delayed_usage["accepted"] is False
    assert delayed_usage["reason_code"] == TASK_STORE_BUDGET_ALREADY_EXCEEDED
    assert (
        delayed_usage["reservation_version_before"]
        == delayed_usage["reservation_version_after"]
        == winner_usage["reservation_version_after"]
    )
    assert delayed_usage["outbox_id"] == winner_usage["outbox_id"]

    clock.advance(125)
    for drift in ("outbox", "node"):
        tampered_owner = f"missing-after-exceeded-tampered-{drift}"
        tampered_store = _SnapshotWithDriftedBudgetWinnerStore(
            DurableTaskStore(db_path, clock=clock),
            drift=drift,
        )
        tampered_app = GovernedCommandApplication(
            command_repository=HostCommandRepository(db_path, clock=clock),
            coordinator=_coordinator(
                store=tampered_store,
                projections=WorkerProjectionStore(db_path, clock=clock),
                worker=worker,
                owner_id=tampered_owner,
                lease_ttl_ms=100,
                bounded_fanout_enabled=True,
                atomic_graph_success_enabled=True,
                node_grant_verifier=_CheckpointNodeGrantVerifier(),
            ),
            node_grant_service=_CheckpointNodeGrantService(),
        )

        tampered = await _events(tampered_app, contracts)

        assert tampered[-1]["execution_receipt"]["status"] == "RECOVERY_REQUIRED"
        with second_durable._connect() as connection:
            assert (
                connection.execute(
                    """
                    SELECT COUNT(*) FROM task_runtime_lease
                    WHERE owner_id = ?
                    """,
                    (tampered_owner,),
                ).fetchone()[0]
                == 0
            )

    reopened = DurableTaskStore(db_path, clock=clock)
    final_app = GovernedCommandApplication(
        command_repository=HostCommandRepository(db_path, clock=clock),
        coordinator=_coordinator(
            store=reopened,
            projections=WorkerProjectionStore(db_path, clock=clock),
            worker=worker,
            owner_id="missing-after-exceeded-owner-c",
            lease_ttl_ms=100,
            bounded_fanout_enabled=True,
            atomic_graph_success_enabled=True,
            node_grant_verifier=_CheckpointNodeGrantVerifier(),
        ),
        node_grant_service=_CheckpointNodeGrantService(),
    )

    recovered = await _events(final_app, contracts)

    assert recovered[-1]["execution_receipt"]["status"] == "BUDGET_BLOCKED"
    assert sorted(calls) == ["root-a", "root-b"]
    final = reopened.read_scheduler_snapshot(
        admitted.graph_id,
        admitted.graph_revision,
    )
    assert final["graph_record"]["status"] == "BLOCKED"
    assert final["budgets"][0]["status"] == "RELEASED"
    assert final["active_leases"] == []
    assert {
        reopened.get_node(
            admitted.graph_id,
            admitted.graph_revision,
            node_id,
        )["status"]
        for node_id in ("root-a", "root-b")
    } == {"FAILED"}

    replay = await _events(final_app, contracts)
    assert replay[-1]["execution_receipt"]["status"] == "BUDGET_BLOCKED"
    assert sorted(calls) == ["root-a", "root-b"]
