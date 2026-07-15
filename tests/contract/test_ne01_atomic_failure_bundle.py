from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import uuid

import pytest

from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.contracts.agent_runtime.task_graph import (
    TaskGraph,
    TaskGraphStatus,
    TaskNode,
    TaskNodeStatus,
)
from host_brain.persistence.task_repository import (
    AttemptStatus,
    DurableTaskStore,
    LeaseStatus,
    OutboxMessage,
    TASK_STORE_IDEMPOTENCY_RECOVERY_REQUIRED,
    TASK_STORE_OUTBOX_CONFLICT,
    TASK_STORE_OWNERSHIP_CONFLICT,
    TaskStoreError,
    build_operation_fingerprint,
    build_outbox_payload_ref,
)


NOW = 1_910_000_000_000
GRAPH_ID = "graph-ne01-atomic-failure"
NODE_ID = "node-resolver"
ATTEMPT_ID = "attempt-ne01-atomic-failure"
OWNER_ID = "runtime-ne01/boot-1"
BUSINESS_SCOPE = "host-command:ne01-atomic-failure"
FAILURE_REF = "resolver-error://worker-registry/permanent-unavailable"


@dataclass
class MutableClock:
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
    unique = identity or uuid.uuid4().hex
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
        idempotency_key=f"ne01-atomic-failure:{unique}",
        outbox_id=f"outbox::ne01-atomic-failure:{unique}",
        attempt_id=attempt_id,
        evidence_refs=evidence_refs,
    )


def _graph() -> TaskGraph:
    return TaskGraph(
        graph_id=GRAPH_ID,
        root_work_order_id="work-order-ne01-atomic-failure",
        revision=1,
        planner_ref="planner://ne01/atomic-failure",
        status=TaskGraphStatus.CREATED,
        created_at=NOW,
        nodes=(
            TaskNode(
                node_id=NODE_ID,
                dependencies=(),
                route="worker:resolver-test",
                input_refs=(),
                output_contract={},
                retry_policy={"max_attempts": 1, "retry_backoff_ms": 0},
                risk_level=1,
                acceptance_criteria=(),
                status=TaskNodeStatus.READY,
                status_version=0,
            ),
        ),
    )


def _running_store(tmp_path: Path) -> tuple[DurableTaskStore, dict[str, Any]]:
    db_path = tmp_path / "ne01-atomic-failure.sqlite3"
    migration = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="migration-ne01-atomic-failure",
        ts_ms=NOW,
    )
    assert migration.success is True, migration.as_dict()
    store = DurableTaskStore(db_path, clock=MutableClock())
    graph = _graph()
    store.create_graph(
        graph,
        outbox=_event("TASK_GRAPH", GRAPH_ID, 0, "TASK_GRAPH_CREATED"),
    )
    store.transition_graph(
        GRAPH_ID,
        1,
        expected_version=0,
        target_status=TaskGraphStatus.ACTIVE,
        outbox=_event("TASK_GRAPH", GRAPH_ID, 1, "TASK_GRAPH_ACTIVE"),
    )
    fingerprint = build_operation_fingerprint(
        business_scope=BUSINESS_SCOPE,
        operation="resolve-worker",
        parameters={"node_id": NODE_ID},
    )
    store.create_attempt(
        attempt_id=ATTEMPT_ID,
        graph_id=GRAPH_ID,
        graph_revision=1,
        node_id=NODE_ID,
        attempt_no=1,
        expected_node_version=0,
        operation_scope=BUSINESS_SCOPE,
        operation_fingerprint=fingerprint,
        outbox=_event(
            "TASK_ATTEMPT",
            ATTEMPT_ID,
            0,
            "TASK_ATTEMPT_CREATED",
            attempt_id=ATTEMPT_ID,
        ),
    )
    lease = store.acquire_lease(
        ATTEMPT_ID,
        owner_id=OWNER_ID,
        ttl_ms=60_000,
        expected_attempt_version=0,
        outbox=_event(
            "TASK_ATTEMPT",
            ATTEMPT_ID,
            1,
            "TASK_ATTEMPT_LEASED",
            attempt_id=ATTEMPT_ID,
        ),
    )
    store.transition_node(
        GRAPH_ID,
        1,
        NODE_ID,
        expected_version=1,
        target_status=TaskNodeStatus.RUNNING,
        outbox=_event(
            "TASK_NODE",
            f"{GRAPH_ID}@1/{NODE_ID}",
            2,
            "TASK_NODE_RUNNING",
        ),
    )
    store.transition_attempt(
        ATTEMPT_ID,
        owner_id=OWNER_ID,
        fencing_token=int(lease["fencing_token"]),
        expected_version=1,
        target_status=AttemptStatus.RUNNING,
        outbox=_event(
            "TASK_ATTEMPT",
            ATTEMPT_ID,
            2,
            "TASK_ATTEMPT_RUNNING",
            attempt_id=ATTEMPT_ID,
        ),
    )
    return store, {"lease": lease, "fingerprint": fingerprint}


def _failure_event() -> OutboxMessage:
    return _event(
        "TASK_ATTEMPT",
        ATTEMPT_ID,
        3,
        "TASK_ATTEMPT_FAILED",
        attempt_id=ATTEMPT_ID,
        evidence_refs=(FAILURE_REF,),
        identity="attempt-failed-stable",
    )


def _fail(
    store: DurableTaskStore,
    lease: dict[str, Any],
    *,
    graph_target_status: TaskGraphStatus = TaskGraphStatus.FAILED,
    attempt_outbox: OutboxMessage | None = None,
    fencing_token: int | None = None,
) -> dict[str, Any]:
    return store.fail_running_attempt_bundle(
        ATTEMPT_ID,
        owner_id=OWNER_ID,
        fencing_token=(
            int(lease["fencing_token"])
            if fencing_token is None
            else int(fencing_token)
        ),
        expected_attempt_version=2,
        expected_node_version=2,
        expected_graph_version=1,
        error_ref=FAILURE_REF,
        graph_target_status=graph_target_status,
        attempt_outbox=attempt_outbox or _failure_event(),
    )


@pytest.mark.parametrize(
    "graph_target_status",
    (TaskGraphStatus.FAILED, TaskGraphStatus.BLOCKED),
)
def test_terminal_failure_bundle_commits_all_aggregates_and_evidence(
    tmp_path: Path,
    graph_target_status: TaskGraphStatus,
) -> None:
    store, context = _running_store(tmp_path)

    result = _fail(
        store,
        context["lease"],
        graph_target_status=graph_target_status,
    )

    assert result["replayed"] is False
    assert result["attempt"]["status"] == AttemptStatus.FAILED.value
    assert result["attempt"]["error_ref"] == FAILURE_REF
    assert result["node"]["status"] == TaskNodeStatus.FAILED.value
    assert result["node"]["node"]["status"] == TaskNodeStatus.FAILED.value
    assert result["graph"]["status"] == graph_target_status.value
    assert result["lease"]["status"] == LeaseStatus.RELEASED.value
    terminal_ts = result["attempt"]["completed_ts_ms"]
    assert terminal_ts == result["attempt"]["updated_ts_ms"]
    assert terminal_ts == result["node"]["updated_ts_ms"]
    assert terminal_ts == result["graph"]["updated_ts_ms"]
    assert terminal_ts == result["lease"]["released_ts_ms"]

    terminal_events = {
        row["event_type"]: row
        for row in store.list_pending_outbox(limit=100)
        if row["event_type"]
        in {
            "TASK_ATTEMPT_FAILED",
            "TASK_NODE_FAILED",
            f"TASK_GRAPH_{graph_target_status.value}",
            "TASK_LEASE_RELEASED_TERMINAL",
        }
    }
    assert set(terminal_events) == {
        "TASK_ATTEMPT_FAILED",
        "TASK_NODE_FAILED",
        f"TASK_GRAPH_{graph_target_status.value}",
        "TASK_LEASE_RELEASED_TERMINAL",
    }
    assert all(
        row["attempt_id"] == ATTEMPT_ID for row in terminal_events.values()
    )
    assert all(
        tuple(row["evidence_refs"]) == (FAILURE_REF,)
        for row in terminal_events.values()
    )


def test_terminal_failure_bundle_exactly_replays_after_response_loss(
    tmp_path: Path,
) -> None:
    store, context = _running_store(tmp_path)
    attempt_outbox = _failure_event()

    committed = _fail(
        store,
        context["lease"],
        attempt_outbox=attempt_outbox,
    )
    replay_store = DurableTaskStore(store.db_path, clock=MutableClock())
    replayed = _fail(
        replay_store,
        context["lease"],
        attempt_outbox=attempt_outbox,
    )

    assert committed["replayed"] is False
    assert replayed["replayed"] is True
    assert replayed["attempt"] == committed["attempt"]
    assert replayed["node"] == committed["node"]
    assert replayed["graph"] == committed["graph"]
    assert replayed["lease"] == committed["lease"]
    terminal_rows = [
        row
        for row in replay_store.list_pending_outbox(limit=100)
        if row["attempt_id"] == ATTEMPT_ID and FAILURE_REF in row["evidence_refs"]
    ]
    assert len(terminal_rows) == 4


def test_terminal_failure_bundle_rejects_stale_fence_without_partial_state(
    tmp_path: Path,
) -> None:
    store, context = _running_store(tmp_path)
    before_outbox_count = len(store.list_pending_outbox(limit=100))

    with pytest.raises(TaskStoreError) as error:
        _fail(
            store,
            context["lease"],
            fencing_token=int(context["lease"]["fencing_token"]) + 1,
        )

    assert error.value.reason_code == TASK_STORE_OWNERSHIP_CONFLICT
    snapshot = store.read_scheduler_snapshot(GRAPH_ID, 1)
    assert snapshot["graph_record"]["status"] == TaskGraphStatus.ACTIVE.value
    assert snapshot["graph_record"]["nodes"][0]["status"] == TaskNodeStatus.RUNNING.value
    assert snapshot["attempts"][0]["status"] == AttemptStatus.RUNNING.value
    assert snapshot["active_leases"][0]["status"] == LeaseStatus.ACTIVE.value
    assert len(store.list_pending_outbox(limit=100)) == before_outbox_count


def test_terminal_failure_bundle_rejects_any_existing_operation_reservation(
    tmp_path: Path,
) -> None:
    store, context = _running_store(tmp_path)
    lease = context["lease"]
    store.reserve_operation(
        reservation_id="reservation-ne01-atomic-failure",
        business_scope=BUSINESS_SCOPE,
        operation_fingerprint=context["fingerprint"],
        attempt_id=ATTEMPT_ID,
        lease_id=str(lease["lease_id"]),
        owner_id=OWNER_ID,
        fencing_token=int(lease["fencing_token"]),
        ttl_ms=30_000,
        outbox=_event(
            "OPERATION_RESERVATION",
            "reservation-ne01-atomic-failure",
            0,
            "OPERATION_RESERVED",
            attempt_id=ATTEMPT_ID,
        ),
    )
    before_outbox_count = len(store.list_pending_outbox(limit=100))

    with pytest.raises(TaskStoreError) as error:
        _fail(store, lease)

    assert error.value.reason_code == TASK_STORE_IDEMPOTENCY_RECOVERY_REQUIRED
    assert store.get_attempt(ATTEMPT_ID)["status"] == AttemptStatus.RUNNING.value
    assert store.get_graph(GRAPH_ID, 1)["status"] == TaskGraphStatus.ACTIVE.value
    assert store.get_active_lease(ATTEMPT_ID)["status"] == LeaseStatus.ACTIVE.value
    assert len(store.list_pending_outbox(limit=100)) == before_outbox_count


def test_terminal_failure_bundle_rolls_back_every_aggregate_on_late_outbox_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, context = _running_store(tmp_path)
    before_outbox_count = len(store.list_pending_outbox(limit=100))
    original_insert = store._insert_outbox

    def reject_graph_outbox(
        conn: Any,
        message: OutboxMessage,
        **kwargs: Any,
    ) -> None:
        if message.event_type == "TASK_GRAPH_FAILED":
            raise TaskStoreError(
                TASK_STORE_OUTBOX_CONFLICT,
                "injected late graph outbox conflict",
            )
        original_insert(conn, message, **kwargs)

    monkeypatch.setattr(store, "_insert_outbox", reject_graph_outbox)

    with pytest.raises(TaskStoreError) as error:
        _fail(store, context["lease"])

    assert error.value.reason_code == TASK_STORE_OUTBOX_CONFLICT
    snapshot = store.read_scheduler_snapshot(GRAPH_ID, 1)
    assert snapshot["graph_record"]["status"] == TaskGraphStatus.ACTIVE.value
    assert snapshot["graph_record"]["nodes"][0]["status"] == TaskNodeStatus.RUNNING.value
    assert snapshot["attempts"][0]["status"] == AttemptStatus.RUNNING.value
    assert snapshot["attempts"][0]["error_ref"] is None
    assert snapshot["active_leases"][0]["status"] == LeaseStatus.ACTIVE.value
    assert len(store.list_pending_outbox(limit=100)) == before_outbox_count
