from __future__ import annotations

from pathlib import Path
import sqlite3

import pytest

from host_brain.contracts.agent_runtime.task_graph import (
    TaskGraph,
    TaskGraphStatus,
    TaskNode,
    TaskNodeStatus,
)
from host_brain.persistence.task_repository import (
    DurableTaskStore,
    OutboxMessage,
    build_outbox_payload_ref,
)
from host_brain.integrations.runtime_events import (
    RUNTIME_EVENT_DELIVERY_MIGRATION_ASSET_KEY,
    RUNTIME_EVENT_DELIVERY_MIGRATION_SHA256,
    RUNTIME_EVENT_FORBIDDEN_FIELD,
    RUNTIME_EVENT_SCHEMA_NOT_READY,
    RuntimeEvent,
    RuntimeEventError,
    RuntimeEventStore,
    RuntimeOutboxPublisher,
)
from host_brain.tribe.event_bus import TribeEvent


ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = ROOT / "host_brain" / "audit" / "migrations"
TASK_RUNTIME_BASE_MIGRATION = MIGRATIONS / "0052_task_runtime.sql"
TASK_RUNTIME_RECOVERY_MIGRATION = MIGRATIONS / "0054_task_runtime_recovery.sql"
RUNTIME_EVENT_MIGRATION = MIGRATIONS / "0056_runtime_events.sql"
RUNTIME_EVENT_DELIVERY_MIGRATION = MIGRATIONS / "0068_runtime_event_delivery.sql"


class MutableClock:
    def __init__(self, value: int = 1_700_000_000_000) -> None:
        self.value = int(value)

    def __call__(self) -> int:
        current = self.value
        self.value += 1
        return current


def _migrated_runtime_event_store(tmp_path: Path, *, clock: MutableClock | None = None) -> RuntimeEventStore:
    db_path = tmp_path / "runtime-events.sqlite3"
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(RUNTIME_EVENT_MIGRATION.read_text(encoding="utf-8"))
        conn.executescript(RUNTIME_EVENT_DELIVERY_MIGRATION.read_text(encoding="utf-8"))
        _record_delivery_migration(conn)
    return RuntimeEventStore(db_path, clock=clock or MutableClock())


def _migrated_combined_store(tmp_path: Path, *, clock: MutableClock | None = None) -> tuple[DurableTaskStore, RuntimeEventStore]:
    db_path = tmp_path / "combined-runtime.sqlite3"
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(TASK_RUNTIME_BASE_MIGRATION.read_text(encoding="utf-8"))
        conn.executescript(TASK_RUNTIME_RECOVERY_MIGRATION.read_text(encoding="utf-8"))
        conn.executescript(RUNTIME_EVENT_MIGRATION.read_text(encoding="utf-8"))
        conn.executescript(RUNTIME_EVENT_DELIVERY_MIGRATION.read_text(encoding="utf-8"))
        _record_delivery_migration(conn)
    shared_clock = clock or MutableClock()
    return DurableTaskStore(db_path, clock=shared_clock), RuntimeEventStore(db_path, clock=shared_clock)


def _record_delivery_migration(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migration_assets(
            asset_key TEXT PRIMARY KEY,
            checksum TEXT NOT NULL,
            disposition TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        INSERT OR REPLACE INTO schema_migration_assets(asset_key, checksum, disposition)
        VALUES (?, ?, 'APPLIED')
        """,
        (
            RUNTIME_EVENT_DELIVERY_MIGRATION_ASSET_KEY,
            RUNTIME_EVENT_DELIVERY_MIGRATION_SHA256,
        ),
    )


def _graph(graph_id: str = "graph-runtime-event-1") -> TaskGraph:
    node = TaskNode(
        node_id="node-1",
        dependencies=(),
        route="skill:test",
        input_refs=("artifact://input/runtime-event-1",),
        output_contract={"type": "test-result"},
        retry_policy={"max_attempts": 1},
        risk_level=2,
        acceptance_criteria=("event exists",),
        status=TaskNodeStatus.READY,
        status_version=0,
    )
    graph = TaskGraph(
        graph_id=graph_id,
        root_work_order_id=f"work-order::{graph_id}",
        revision=1,
        planner_ref="planner://runtime-events/test",
        status=TaskGraphStatus.CREATED,
        created_at=1_700_000_000_000,
        nodes=(node,),
    )
    graph.validate()
    return graph


def _outbox(
    *,
    aggregate_type: str = "TASK_GRAPH",
    aggregate_id: str = "graph-runtime-event-1",
    aggregate_version: int = 0,
    event_type: str = "TASK_GRAPH_CREATED",
    idempotency_key: str = "runtime-event-contract-outbox-1",
) -> OutboxMessage:
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
        idempotency_key=idempotency_key,
        evidence_refs=("evidence://runtime-event-contract/graph-created",),
    )


def _runtime_event(index: int = 1) -> RuntimeEvent:
    return RuntimeEvent.create(
        event_type="TASK_GRAPH_CREATED",
        source_kind="TASK",
        source_ref=f"task://graph/runtime-event-{index}",
        aggregate_type="TASK_GRAPH",
        aggregate_id=f"graph-runtime-event-{index}",
        aggregate_version=index,
        payload_ref=f"taskstore://event/TASK_GRAPH/graph-runtime-event-{index}/{index}",
        payload={
            "aggregate_type": "TASK_GRAPH",
            "aggregate_id": f"graph-runtime-event-{index}",
            "aggregate_version": index,
            "payload_ref": f"taskstore://event/TASK_GRAPH/graph-runtime-event-{index}/{index}",
        },
        evidence_refs=(f"evidence://runtime-event/{index}",),
        ts_ms=1_700_000_000_000 + index,
    )


def test_t_nle_12_runtime_event_store_requires_migration(tmp_path: Path) -> None:
    db_path = tmp_path / "missing-schema.sqlite3"
    db_path.write_bytes(b"")

    with pytest.raises(RuntimeEventError) as error:
        RuntimeEventStore(db_path)

    assert error.value.reason_code == RUNTIME_EVENT_SCHEMA_NOT_READY


def test_t_nle_12_cursor_replay_is_ordered_and_deduped(tmp_path: Path) -> None:
    store = _migrated_runtime_event_store(tmp_path)

    first = store.append_event(_runtime_event(1))
    duplicate = store.append_event(_runtime_event(1))
    second = store.append_event(_runtime_event(2))

    assert duplicate["cursor"] == first["cursor"]
    assert first["dedupe_key"] == duplicate["dedupe_key"]
    replay = store.list_events(after_cursor=0, limit=10)
    assert [event["cursor"] for event in replay] == [first["cursor"], second["cursor"]]
    assert [event["aggregate_id"] for event in replay] == [
        "graph-runtime-event-1",
        "graph-runtime-event-2",
    ]
    reconnect = store.list_events(after_cursor=first["cursor"], limit=10)
    assert [event["cursor"] for event in reconnect] == [second["cursor"]]


def test_t_nle_12_runtime_event_rejects_sensitive_payload_fields(tmp_path: Path) -> None:
    store = _migrated_runtime_event_store(tmp_path)

    with pytest.raises(RuntimeEventError) as error:
        store.append_event(
            RuntimeEvent.create(
                event_type="REVIEW_APPROVED",
                source_kind="REVIEW",
                source_ref="review://runtime-event/sensitive",
                aggregate_type="REVIEW",
                aggregate_id="review-sensitive",
                aggregate_version=1,
                payload_ref="review://runtime-event/sensitive/payload",
                payload={"resume_token": "review_resume::secret-token"},
            )
        )

    assert error.value.reason_code == RUNTIME_EVENT_FORBIDDEN_FIELD
    assert store.list_events(after_cursor=0) == []


def test_t_nle_12_task_outbox_publisher_is_at_least_once_without_duplicate_events(tmp_path: Path) -> None:
    task_store, event_store = _migrated_combined_store(tmp_path)
    task_store.create_graph(_graph(), outbox=_outbox())
    assert len(task_store.list_pending_outbox()) == 1

    publisher = RuntimeOutboxPublisher(task_store=task_store, event_store=event_store)
    first_publish = publisher.publish_pending(limit=10)
    retry_publish = publisher.publish_pending(limit=10)
    replay = event_store.list_events(after_cursor=0, limit=10)

    assert first_publish["published_count"] == 1
    assert retry_publish["published_count"] == 0
    assert len(replay) == 1
    assert replay[0]["source_kind"] == "TASK_STORE_OUTBOX"
    assert replay[0]["payload"]["payload_ref"] == "taskstore://event/TASK_GRAPH/graph-runtime-event-1/0"
    assert "idempotency_key" not in replay[0]["payload"]
    assert task_store.list_pending_outbox() == []


def test_t_nle_12_task_outbox_retry_marks_pending_duplicate_without_duplicate_timeline(tmp_path: Path) -> None:
    task_store, event_store = _migrated_combined_store(tmp_path)
    task_store.create_graph(_graph(), outbox=_outbox())
    pending = task_store.list_pending_outbox()
    event_store.append_task_outbox(pending[0])

    publisher = RuntimeOutboxPublisher(task_store=task_store, event_store=event_store)
    published = publisher.publish_pending(limit=10)
    replay = event_store.list_events(after_cursor=0, limit=10)

    assert published["published_count"] == 1
    assert len(replay) == 1
    assert published["published"][0]["cursor"] == replay[0]["cursor"]
    assert task_store.list_pending_outbox() == []


def test_t_nle_12_tribe_adapter_persists_metadata_not_raw_payload(tmp_path: Path) -> None:
    store = _migrated_runtime_event_store(tmp_path)
    event = TribeEvent(
        event_id="tribe-runtime-event-1",
        event_type="BOND_PROGRESS",
        source_agent="somatic",
        target_agent="prefrontal",
        payload={"message": "raw user-like text should not be persisted", "score": 0.5},
        ts_ms=1_700_000_000_100,
    )

    record = store.append_tribe_event(event)

    assert record["source_kind"] == "TRIBE_EVENT"
    assert record["payload"]["payload_field_names"] == ["message", "score"]
    assert "raw user-like text" not in str(record["payload"])


def test_t_nle_12_tribe_adapter_rejects_forbidden_payload_field_names(tmp_path: Path) -> None:
    store = _migrated_runtime_event_store(tmp_path)
    event = TribeEvent(
        event_id="tribe-runtime-event-secret",
        event_type="BOND_PROGRESS",
        source_agent="somatic",
        target_agent=None,
        payload={"secret": "not stored"},
        ts_ms=1_700_000_000_100,
    )

    with pytest.raises(RuntimeEventError) as error:
        store.append_tribe_event(event)

    assert error.value.reason_code == RUNTIME_EVENT_FORBIDDEN_FIELD
    assert store.list_events(after_cursor=0) == []
