from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import sqlite3
from typing import Any
import uuid

import pytest

from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.contracts.agent_runtime.budget_policy import (
    BudgetAmount,
    BudgetReservation,
    BudgetReservationStatus,
)
from host_brain.contracts.agent_runtime.task_graph import (
    TaskGraph,
    TaskGraphStatus,
    TaskNode,
    TaskNodeStatus,
)
from host_brain.persistence.task_repository import (
    AttemptStatus,
    DurableTaskStore,
    OutboxMessage,
    TASK_STORE_CAS_CONFLICT,
    TASK_STORE_OWNERSHIP_CONFLICT,
    TaskStoreError,
    build_outbox_payload_ref,
)


NOW = 1_920_000_000_000
OWNER = "runtime-ne01/parallel-store"
BUDGET_ID = "budget-ne01-parallel-store"
FAILURE_REF = "retry-error://worker-registry/transient"


@dataclass
class Clock:
    value: int = NOW

    def __call__(self) -> int:
        return self.value


def _event(
    aggregate_type: str,
    aggregate_id: str,
    aggregate_version: int,
    event_type: str,
    *,
    attempt_id: str | None = None,
    evidence_refs: tuple[str, ...] = (),
    identity: str | None = None,
) -> OutboxMessage:
    stable = identity or uuid.uuid4().hex
    return OutboxMessage.create(
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        aggregate_version=aggregate_version,
        event_type=event_type,
        payload_ref=build_outbox_payload_ref(
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            aggregate_version=aggregate_version,
        ),
        idempotency_key=f"ne01-parallel-store:{stable}",
        outbox_id=f"outbox::ne01-parallel-store:{stable}",
        attempt_id=attempt_id,
        evidence_refs=evidence_refs,
    )


def _graph(graph_id: str) -> TaskGraph:
    return TaskGraph(
        graph_id=graph_id,
        root_work_order_id=f"work-order::{graph_id}",
        revision=1,
        planner_ref="planner://ne01/parallel-store",
        status=TaskGraphStatus.CREATED,
        created_at=NOW,
        nodes=tuple(
            TaskNode(
                node_id=node_id,
                dependencies=(),
                route=f"skill:{node_id}",
                input_refs=(),
                output_contract={},
                retry_policy={"max_attempts": 2, "retry_backoff_ms": 0},
                risk_level=1,
                acceptance_criteria=(),
                status=TaskNodeStatus.READY,
                status_version=0,
            )
            for node_id in ("left", "right")
        ),
    )


def _store(tmp_path: Path, graph_id: str) -> DurableTaskStore:
    db_path = tmp_path / f"{graph_id}.sqlite3"
    migration = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id=f"migration::{graph_id}",
        ts_ms=NOW,
    )
    assert migration.success is True, migration.as_dict()
    store = DurableTaskStore(db_path, clock=Clock())
    store.create_graph(
        _graph(graph_id),
        outbox=_event("TASK_GRAPH", graph_id, 0, "TASK_GRAPH_CREATED"),
    )
    store.transition_graph(
        graph_id,
        1,
        expected_version=0,
        target_status=TaskGraphStatus.ACTIVE,
        outbox=_event("TASK_GRAPH", graph_id, 1, "TASK_GRAPH_ACTIVE"),
    )
    store.create_budget_reservation(
        BudgetReservation(
            budget_id=BUDGET_ID,
            scope=f"graph:{graph_id}@1",
            limits=BudgetAmount(
                token_count=100,
                tool_call_count=10,
                time_ms=60_000,
                retry_count=0,
            ),
            reserved=BudgetAmount(
                token_count=100,
                tool_call_count=10,
                time_ms=60_000,
                retry_count=0,
            ),
            status=BudgetReservationStatus.PENDING,
        ),
        graph_id=graph_id,
        graph_revision=1,
        node_id=None,
        attempt_id=None,
        outbox=_event(
            "BUDGET_RESERVATION",
            BUDGET_ID,
            0,
            "BUDGET_RESERVATION_CREATED",
        ),
    )
    store.transition_budget(
        BUDGET_ID,
        expected_version=0,
        target_status=BudgetReservationStatus.ACTIVE,
        outbox=_event(
            "BUDGET_RESERVATION",
            BUDGET_ID,
            1,
            "BUDGET_RESERVATION_ACTIVE",
        ),
    )
    return store


def _node_states(store: DurableTaskStore, graph_id: str) -> dict[str, dict[str, Any]]:
    return {
        node_id: {
            "status": (record := store.get_node(graph_id, 1, node_id))["status"],
            "status_version": record["status_version"],
        }
        for node_id in ("left", "right")
    }


def _transition_node(
    store: DurableTaskStore,
    graph_id: str,
    node_id: str,
    target: TaskNodeStatus,
) -> dict[str, Any]:
    current = store.get_node(graph_id, 1, node_id)
    return store.transition_node(
        graph_id,
        1,
        node_id,
        expected_version=int(current["status_version"]),
        target_status=target,
        outbox=_event(
            "TASK_NODE",
            f"{graph_id}@1/{node_id}",
            int(current["status_version"]) + 1,
            f"TASK_NODE_{target.value}",
        ),
    )


def _running_attempt(
    store: DurableTaskStore,
    graph_id: str,
    *,
    attempt_id: str,
) -> dict[str, Any]:
    store.create_attempt(
        attempt_id=attempt_id,
        graph_id=graph_id,
        graph_revision=1,
        node_id="left",
        attempt_no=1,
        expected_node_version=0,
        operation_scope=f"graph:{graph_id}@1/left",
        operation_fingerprint=f"sha256:{'1' * 64}",
        outbox=_event(
            "TASK_ATTEMPT",
            attempt_id,
            0,
            "TASK_ATTEMPT_CREATED",
            attempt_id=attempt_id,
        ),
    )
    lease = store.acquire_lease(
        attempt_id,
        owner_id=OWNER,
        ttl_ms=60_000,
        expected_attempt_version=0,
        outbox=_event(
            "TASK_ATTEMPT",
            attempt_id,
            1,
            "TASK_ATTEMPT_LEASED",
            attempt_id=attempt_id,
        ),
    )
    _transition_node(store, graph_id, "left", TaskNodeStatus.RUNNING)
    store.transition_attempt(
        attempt_id,
        owner_id=OWNER,
        fencing_token=int(lease["fencing_token"]),
        expected_version=1,
        target_status=AttemptStatus.RUNNING,
        outbox=_event(
            "TASK_ATTEMPT",
            attempt_id,
            2,
            "TASK_ATTEMPT_RUNNING",
            attempt_id=attempt_id,
        ),
    )
    return lease


def _retry_usage_id(attempt_id: str, *, node_only: bool) -> str:
    material = f"{BUDGET_ID}\n{attempt_id}\nretry"
    if node_only:
        material += "\nnode-only"
    return "budget-usage://" + hashlib.sha256(material.encode("utf-8")).hexdigest()


def _retry_outbox(attempt_id: str, *, node_only: bool) -> OutboxMessage:
    usage_id = _retry_usage_id(attempt_id, node_only=node_only)
    return _event(
        "TASK_ATTEMPT",
        attempt_id,
        3,
        "TASK_ATTEMPT_FAILED",
        attempt_id=attempt_id,
        evidence_refs=(FAILURE_REF, usage_id),
        identity=f"retry:{attempt_id}:{'node' if node_only else 'graph'}",
    )


def test_rejected_retry_node_outcome_keeps_graph_active_and_exactly_replays(
    tmp_path: Path,
) -> None:
    graph_id = "graph-retry-node-only"
    attempt_id = "attempt-retry-node-only"
    store = _store(tmp_path, graph_id)
    lease = _running_attempt(store, graph_id, attempt_id=attempt_id)
    outbox = _retry_outbox(attempt_id, node_only=True)

    committed = store.schedule_retry_node_outcome(
        attempt_id,
        budget_id=BUDGET_ID,
        owner_id=OWNER,
        fencing_token=int(lease["fencing_token"]),
        expected_attempt_version=2,
        expected_graph_version=1,
        error_ref=FAILURE_REF,
        outbox=outbox,
    )
    replayed = DurableTaskStore(store.db_path, clock=Clock()).schedule_retry_node_outcome(
        attempt_id,
        budget_id=BUDGET_ID,
        owner_id=OWNER,
        fencing_token=int(lease["fencing_token"]),
        expected_attempt_version=2,
        expected_graph_version=1,
        error_ref=FAILURE_REF,
        outbox=outbox,
    )

    assert committed["accepted"] is False
    assert committed["replayed"] is False
    assert replayed["replayed"] is True
    assert committed["graph"]["status"] == TaskGraphStatus.ACTIVE.value
    assert committed["graph"]["status_version"] == 1
    assert committed["node"]["status"] == TaskNodeStatus.FAILED.value
    assert committed["budget"]["status"] == BudgetReservationStatus.EXCEEDED.value
    graph_events = [
        row
        for row in store.list_pending_outbox(limit=100)
        if row["aggregate_type"] == "TASK_GRAPH" and row["attempt_id"] == attempt_id
    ]
    assert graph_events == []


def test_legacy_rejected_retry_still_blocks_graph(tmp_path: Path) -> None:
    graph_id = "graph-retry-legacy"
    attempt_id = "attempt-retry-legacy"
    store = _store(tmp_path, graph_id)
    lease = _running_attempt(store, graph_id, attempt_id=attempt_id)

    result = store.schedule_retry(
        attempt_id,
        budget_id=BUDGET_ID,
        owner_id=OWNER,
        fencing_token=int(lease["fencing_token"]),
        expected_attempt_version=2,
        error_ref=FAILURE_REF,
        outbox=_retry_outbox(attempt_id, node_only=False),
    )

    assert result["accepted"] is False
    assert result["graph"]["status"] == TaskGraphStatus.BLOCKED.value
    assert any(
        row["event_type"] == "TASK_GRAPH_BLOCKED" and row["attempt_id"] == attempt_id
        for row in store.list_pending_outbox(limit=100)
    )


def test_parallel_failure_settlement_releases_budget_and_exactly_replays(
    tmp_path: Path,
) -> None:
    graph_id = "graph-parallel-failure-settlement"
    store = _store(tmp_path, graph_id)
    for target in (
        TaskNodeStatus.LEASED,
        TaskNodeStatus.RUNNING,
        TaskNodeStatus.FAILED,
    ):
        _transition_node(store, graph_id, "left", target)
    states = _node_states(store, graph_id)
    graph_outbox = _event(
        "TASK_GRAPH",
        graph_id,
        2,
        "TASK_GRAPH_FAILED",
        identity="parallel-failure-graph",
    )
    budget_outbox = _event(
        "BUDGET_RESERVATION",
        BUDGET_ID,
        2,
        "BUDGET_RESERVATION_RELEASED",
        identity="parallel-failure-budget",
    )

    committed = store.settle_parallel_graph_budget(
        graph_id,
        1,
        budget_id=BUDGET_ID,
        expected_graph_version=1,
        expected_budget_version=1,
        expected_node_states=states,
        graph_target_status=TaskGraphStatus.FAILED,
        budget_target_status=BudgetReservationStatus.RELEASED,
        graph_outbox=graph_outbox,
        budget_outbox=budget_outbox,
    )
    replayed = DurableTaskStore(store.db_path, clock=Clock()).settle_parallel_graph_budget(
        graph_id,
        1,
        budget_id=BUDGET_ID,
        expected_graph_version=1,
        expected_budget_version=1,
        expected_node_states=states,
        graph_target_status=TaskGraphStatus.FAILED,
        budget_target_status=BudgetReservationStatus.RELEASED,
        graph_outbox=graph_outbox,
        budget_outbox=budget_outbox,
    )

    assert committed["replayed"] is False
    assert replayed["replayed"] is True
    assert committed["graph"]["status"] == TaskGraphStatus.FAILED.value
    assert committed["budget"]["status"] == BudgetReservationStatus.RELEASED.value


def test_parallel_review_settlement_blocks_graph_and_keeps_budget_active(
    tmp_path: Path,
) -> None:
    graph_id = "graph-parallel-review-settlement"
    store = _store(tmp_path, graph_id)
    _transition_node(store, graph_id, "left", TaskNodeStatus.WAITING_APPROVAL)
    states = _node_states(store, graph_id)
    graph_outbox = _event(
        "TASK_GRAPH",
        graph_id,
        2,
        "TASK_GRAPH_BLOCKED",
        identity="parallel-review-graph",
    )

    committed = store.settle_parallel_graph_budget(
        graph_id,
        1,
        budget_id=BUDGET_ID,
        expected_graph_version=1,
        expected_budget_version=1,
        expected_node_states=states,
        graph_target_status=TaskGraphStatus.BLOCKED,
        budget_target_status=None,
        graph_outbox=graph_outbox,
    )
    replayed = store.settle_parallel_graph_budget(
        graph_id,
        1,
        budget_id=BUDGET_ID,
        expected_graph_version=1,
        expected_budget_version=1,
        expected_node_states=states,
        graph_target_status=TaskGraphStatus.BLOCKED,
        budget_target_status=None,
        graph_outbox=graph_outbox,
    )

    assert committed["replayed"] is False
    assert replayed["replayed"] is True
    assert committed["graph"]["status"] == TaskGraphStatus.BLOCKED.value
    assert committed["budget"]["status"] == BudgetReservationStatus.ACTIVE.value
    assert committed["budget"]["status_version"] == 1


def test_parallel_settlement_rejects_stale_node_without_partial_write(
    tmp_path: Path,
) -> None:
    graph_id = "graph-parallel-stale-node"
    store = _store(tmp_path, graph_id)
    _transition_node(store, graph_id, "left", TaskNodeStatus.WAITING_APPROVAL)
    states = _node_states(store, graph_id)
    states["left"]["status_version"] += 1
    before_outbox = len(store.list_pending_outbox(limit=100))

    with pytest.raises(TaskStoreError) as error:
        store.settle_parallel_graph_budget(
            graph_id,
            1,
            budget_id=BUDGET_ID,
            expected_graph_version=1,
            expected_budget_version=1,
            expected_node_states=states,
            graph_target_status=TaskGraphStatus.BLOCKED,
            budget_target_status=None,
            graph_outbox=_event(
                "TASK_GRAPH",
                graph_id,
                2,
                "TASK_GRAPH_BLOCKED",
            ),
        )

    assert error.value.reason_code == TASK_STORE_CAS_CONFLICT
    assert store.get_graph(graph_id, 1)["status"] == TaskGraphStatus.ACTIVE.value
    assert store.reconcile_budget(BUDGET_ID)["status"] == BudgetReservationStatus.ACTIVE.value
    assert len(store.list_pending_outbox(limit=100)) == before_outbox


def test_parallel_settlement_rejects_active_lease_without_partial_write(
    tmp_path: Path,
) -> None:
    graph_id = "graph-parallel-active-lease"
    attempt_id = "attempt-parallel-active-lease"
    store = _store(tmp_path, graph_id)
    _running_attempt(store, graph_id, attempt_id=attempt_id)
    states = _node_states(store, graph_id)
    before_outbox = len(store.list_pending_outbox(limit=100))

    with pytest.raises(TaskStoreError) as error:
        store.settle_parallel_graph_budget(
            graph_id,
            1,
            budget_id=BUDGET_ID,
            expected_graph_version=1,
            expected_budget_version=1,
            expected_node_states=states,
            graph_target_status=TaskGraphStatus.BLOCKED,
            budget_target_status=None,
            graph_outbox=_event(
                "TASK_GRAPH",
                graph_id,
                2,
                "TASK_GRAPH_BLOCKED",
            ),
        )

    assert error.value.reason_code == TASK_STORE_OWNERSHIP_CONFLICT
    assert store.get_graph(graph_id, 1)["status"] == TaskGraphStatus.ACTIVE.value
    assert store.reconcile_budget(BUDGET_ID)["status"] == BudgetReservationStatus.ACTIVE.value
    assert len(store.list_pending_outbox(limit=100)) == before_outbox


def test_parallel_settlement_rejects_terminal_target_without_durable_cause(
    tmp_path: Path,
) -> None:
    graph_id = "graph-parallel-missing-terminal-cause"
    store = _store(tmp_path, graph_id)
    states = _node_states(store, graph_id)
    before_outbox = len(store.list_pending_outbox(limit=100))

    with pytest.raises(TaskStoreError):
        store.settle_parallel_graph_budget(
            graph_id,
            1,
            budget_id=BUDGET_ID,
            expected_graph_version=1,
            expected_budget_version=1,
            expected_node_states=states,
            graph_target_status=TaskGraphStatus.FAILED,
            budget_target_status=BudgetReservationStatus.RELEASED,
            graph_outbox=_event(
                "TASK_GRAPH",
                graph_id,
                2,
                "TASK_GRAPH_FAILED",
            ),
            budget_outbox=_event(
                "BUDGET_RESERVATION",
                BUDGET_ID,
                2,
                "BUDGET_RESERVATION_RELEASED",
            ),
        )

    assert store.get_graph(graph_id, 1)["status"] == TaskGraphStatus.ACTIVE.value
    assert store.reconcile_budget(BUDGET_ID)["status"] == BudgetReservationStatus.ACTIVE.value
    assert len(store.list_pending_outbox(limit=100)) == before_outbox


def test_parallel_review_settlement_rejects_failed_sibling_without_partial_write(
    tmp_path: Path,
) -> None:
    graph_id = "graph-parallel-review-with-failure"
    store = _store(tmp_path, graph_id)
    for target in (
        TaskNodeStatus.LEASED,
        TaskNodeStatus.RUNNING,
        TaskNodeStatus.FAILED,
    ):
        _transition_node(store, graph_id, "left", target)
    _transition_node(store, graph_id, "right", TaskNodeStatus.WAITING_APPROVAL)
    states = _node_states(store, graph_id)
    before_outbox = len(store.list_pending_outbox(limit=100))

    with pytest.raises(TaskStoreError):
        store.settle_parallel_graph_budget(
            graph_id,
            1,
            budget_id=BUDGET_ID,
            expected_graph_version=1,
            expected_budget_version=1,
            expected_node_states=states,
            graph_target_status=TaskGraphStatus.BLOCKED,
            budget_target_status=None,
            graph_outbox=_event(
                "TASK_GRAPH",
                graph_id,
                2,
                "TASK_GRAPH_BLOCKED",
            ),
        )

    assert store.get_graph(graph_id, 1)["status"] == TaskGraphStatus.ACTIVE.value
    assert store.reconcile_budget(BUDGET_ID)["status"] == BudgetReservationStatus.ACTIVE.value
    assert len(store.list_pending_outbox(limit=100)) == before_outbox


def test_parallel_review_settlement_requires_active_budget_without_partial_write(
    tmp_path: Path,
) -> None:
    graph_id = "graph-parallel-review-exceeded-budget"
    store = _store(tmp_path, graph_id)
    _transition_node(store, graph_id, "left", TaskNodeStatus.WAITING_APPROVAL)
    store.consume_budget(
        BUDGET_ID,
        delta=BudgetAmount(token_count=101),
        expected_version=1,
        outbox=_event(
            "BUDGET_RESERVATION",
            BUDGET_ID,
            2,
            "BUDGET_EXCEEDED",
        ),
    )
    states = _node_states(store, graph_id)
    before_outbox = len(store.list_pending_outbox(limit=100))

    with pytest.raises(TaskStoreError):
        store.settle_parallel_graph_budget(
            graph_id,
            1,
            budget_id=BUDGET_ID,
            expected_graph_version=1,
            expected_budget_version=2,
            expected_node_states=states,
            graph_target_status=TaskGraphStatus.BLOCKED,
            budget_target_status=None,
            graph_outbox=_event(
                "TASK_GRAPH",
                graph_id,
                2,
                "TASK_GRAPH_BLOCKED",
            ),
        )

    assert store.get_graph(graph_id, 1)["status"] == TaskGraphStatus.ACTIVE.value
    assert store.reconcile_budget(BUDGET_ID)["status"] == BudgetReservationStatus.EXCEEDED.value
    assert len(store.list_pending_outbox(limit=100)) == before_outbox


def test_parallel_settlement_rejects_unresolved_operation_reservation(
    tmp_path: Path,
) -> None:
    graph_id = "graph-parallel-unresolved-operation"
    attempt_id = "attempt-parallel-unresolved-operation"
    reservation_id = "operation-parallel-unresolved"
    store = _store(tmp_path, graph_id)
    lease = _running_attempt(store, graph_id, attempt_id=attempt_id)
    store.reserve_operation(
        reservation_id=reservation_id,
        business_scope=f"graph:{graph_id}@1/left",
        operation_fingerprint=f"sha256:{'1' * 64}",
        attempt_id=attempt_id,
        lease_id=str(lease["lease_id"]),
        owner_id=OWNER,
        fencing_token=int(lease["fencing_token"]),
        ttl_ms=60_000,
        outbox=_event(
            "OPERATION_RESERVATION",
            reservation_id,
            0,
            "OPERATION_RESERVED",
            attempt_id=attempt_id,
        ),
    )
    store.transition_attempt(
        attempt_id,
        owner_id=OWNER,
        fencing_token=int(lease["fencing_token"]),
        expected_version=2,
        target_status=AttemptStatus.FAILED,
        error_ref="taskerror://parallel-unresolved-operation",
        outbox=_event(
            "TASK_ATTEMPT",
            attempt_id,
            3,
            "TASK_ATTEMPT_FAILED",
            attempt_id=attempt_id,
        ),
    )
    _transition_node(store, graph_id, "left", TaskNodeStatus.FAILED)
    states = _node_states(store, graph_id)
    before_outbox = len(store.list_pending_outbox(limit=100))

    with pytest.raises(TaskStoreError) as error:
        store.settle_parallel_graph_budget(
            graph_id,
            1,
            budget_id=BUDGET_ID,
            expected_graph_version=1,
            expected_budget_version=1,
            expected_node_states=states,
            graph_target_status=TaskGraphStatus.FAILED,
            budget_target_status=BudgetReservationStatus.RELEASED,
            graph_outbox=_event(
                "TASK_GRAPH",
                graph_id,
                2,
                "TASK_GRAPH_FAILED",
            ),
            budget_outbox=_event(
                "BUDGET_RESERVATION",
                BUDGET_ID,
                2,
                "BUDGET_RESERVATION_RELEASED",
            ),
        )

    assert error.value.reason_code == TASK_STORE_OWNERSHIP_CONFLICT
    assert store.get_graph(graph_id, 1)["status"] == TaskGraphStatus.ACTIVE.value
    assert store.reconcile_budget(BUDGET_ID)["status"] == BudgetReservationStatus.ACTIVE.value
    assert len(store.list_pending_outbox(limit=100)) == before_outbox


def test_parallel_settlement_rejects_recoverable_attempt_without_active_lease(
    tmp_path: Path,
) -> None:
    graph_id = "graph-parallel-recoverable-attempt"
    attempt_id = "attempt-parallel-recoverable-attempt"
    store = _store(tmp_path, graph_id)
    lease = _running_attempt(store, graph_id, attempt_id=attempt_id)
    store.transition_attempt(
        attempt_id,
        owner_id=OWNER,
        fencing_token=int(lease["fencing_token"]),
        expected_version=2,
        target_status=AttemptStatus.FAILED,
        error_ref="taskerror://parallel-recoverable-attempt",
        outbox=_event(
            "TASK_ATTEMPT",
            attempt_id,
            3,
            "TASK_ATTEMPT_FAILED",
            attempt_id=attempt_id,
        ),
    )
    _transition_node(store, graph_id, "left", TaskNodeStatus.FAILED)
    with sqlite3.connect(store.db_path) as conn:
        conn.execute(
            "UPDATE task_runtime_attempt SET status = 'RECOVERABLE' WHERE attempt_id = ?",
            (attempt_id,),
        )
    states = _node_states(store, graph_id)
    before_outbox = len(store.list_pending_outbox(limit=100))

    with pytest.raises(TaskStoreError) as error:
        store.settle_parallel_graph_budget(
            graph_id,
            1,
            budget_id=BUDGET_ID,
            expected_graph_version=1,
            expected_budget_version=1,
            expected_node_states=states,
            graph_target_status=TaskGraphStatus.FAILED,
            budget_target_status=BudgetReservationStatus.RELEASED,
            graph_outbox=_event(
                "TASK_GRAPH",
                graph_id,
                2,
                "TASK_GRAPH_FAILED",
            ),
            budget_outbox=_event(
                "BUDGET_RESERVATION",
                BUDGET_ID,
                2,
                "BUDGET_RESERVATION_RELEASED",
            ),
        )

    assert error.value.reason_code == TASK_STORE_OWNERSHIP_CONFLICT
    assert store.get_graph(graph_id, 1)["status"] == TaskGraphStatus.ACTIVE.value
    assert store.reconcile_budget(BUDGET_ID)["status"] == BudgetReservationStatus.ACTIVE.value
    assert len(store.list_pending_outbox(limit=100)) == before_outbox
