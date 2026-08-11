from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
import sqlite3
import time

from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.integrations.runtime_events import (
    RuntimeEventStore,
    RuntimeOutboxPublisher,
)
from host_brain.persistence.task_repository import DurableTaskStore
from host_brain.persistence.task_graph_admission_authority import (
    canonical_task_graph_admission_scope,
    register_task_graph_admission_authority,
)


@dataclass
class _Clock:
    value: int

    def __call__(self) -> int:
        return self.value


class _ProjectionWorker:
    def drain(self, *, limit: int) -> dict[str, int | str]:
        return {"status": "OK", "processed_count": 0, "limit": limit}


def _runtime(
    tmp_path: Path,
    *,
    clock: _Clock,
) -> tuple[Path, DurableTaskStore, RuntimeEventStore]:
    db_path = tmp_path / "canonical.sqlite3"
    migrated = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="ne01-outbox-cross-process",
        ts_ms=clock.value,
    )
    assert migrated.success is True, migrated.as_dict()
    return (
        db_path,
        DurableTaskStore(db_path, clock=clock),
        RuntimeEventStore(db_path, clock=clock),
    )


def _insert_outbox(
    db_path: Path,
    *,
    outbox_id: str,
    aggregate_id: str,
    created_ts_ms: int,
) -> None:
    with sqlite3.connect(db_path) as conn:
        register_task_graph_admission_authority(conn)
        with canonical_task_graph_admission_scope(aggregate_id, 1):
            conn.execute(
                """
                INSERT OR IGNORE INTO task_runtime_graph(
                    graph_id, revision, root_work_order_id, planner_ref, status,
                    status_version, graph_json, source_kind, source_ref,
                    legacy_snapshot_json, created_ts_ms, updated_ts_ms
                ) VALUES (?, 1, ?, 'planner://ne01/outbox-test', 'CREATED',
                          0, '{}', 'CANONICAL', ?, NULL, ?, ?)
                """,
                (
                    aggregate_id,
                    f"work-order::{aggregate_id}",
                    f"{aggregate_id}@1",
                    created_ts_ms,
                    created_ts_ms,
                ),
            )
        conn.execute(
            """
            INSERT INTO task_runtime_outbox(
                outbox_id, aggregate_type, aggregate_id, aggregate_version,
                graph_revision, event_type, payload_ref, evidence_refs_json, attempt_id,
                idempotency_key, status, available_ts_ms, created_ts_ms,
                published_ts_ms, status_version
            ) VALUES (?, 'TASK_GRAPH', ?, 1, 1, 'TASK_GRAPH_CHANGED', ?, '[]',
                      NULL, ?, 'PENDING', ?, ?, NULL, 0)
            """,
            (
                outbox_id,
                aggregate_id,
                f"taskstore://event/TASK_GRAPH/{aggregate_id}@1/1",
                f"idempotency::{outbox_id}",
                created_ts_ms,
                created_ts_ms,
            ),
        )


def test_event_append_then_owner_death_is_taken_over_without_duplicate_event(
    tmp_path: Path,
) -> None:
    clock = _Clock(100)
    db_path, first_store, first_events = _runtime(tmp_path, clock=clock)
    _insert_outbox(
        db_path,
        outbox_id="outbox-crash",
        aggregate_id="graph-crash",
        created_ts_ms=100,
    )

    abandoned = first_store.claim_pending_outbox(
        "publisher-dead",
        10,
        limit=1,
    )[0]
    first_event = first_events.append_task_outbox(abandoned)
    # Simulate kill -9 after the idempotent event append and before claim
    # completion.  No release or process-local cleanup is available.
    clock.value = 111

    takeover_store = DurableTaskStore(db_path, clock=clock)
    takeover_events = RuntimeEventStore(db_path, clock=clock)
    publisher = RuntimeOutboxPublisher(
        task_store=takeover_store,
        event_store=takeover_events,
        projection_worker=_ProjectionWorker(),
        worker_id="publisher-takeover",
        claim_lease_ms=10,
    )
    result = publisher.publish_pending(limit=10)

    assert result["published_count"] == 1
    assert result["published"][0]["event_id"] == first_event["event_id"]
    timeline = takeover_events.list_events(
        source_kind="TASK_STORE_OUTBOX",
        limit=100,
    )
    assert [row["event_id"] for row in timeline] == [first_event["event_id"]]
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            """
            SELECT status, claim_owner, claim_token, claim_expires_ts_ms,
                   claim_fencing_token, attempt_count, status_version
            FROM task_runtime_outbox WHERE outbox_id = 'outbox-crash'
            """
        ).fetchone() == ("PUBLISHED", None, None, None, 2, 2, 3)


def test_two_publishers_share_one_durable_claim_without_false_failure(
    tmp_path: Path,
) -> None:
    clock = _Clock(500)
    db_path, _seed_store, _seed_events = _runtime(tmp_path, clock=clock)
    _insert_outbox(
        db_path,
        outbox_id="outbox-race",
        aggregate_id="graph-race",
        created_ts_ms=500,
    )

    def publish(worker_id: str) -> dict[str, object]:
        return RuntimeOutboxPublisher(
            task_store=DurableTaskStore(db_path, clock=clock),
            event_store=RuntimeEventStore(db_path, clock=clock),
            projection_worker=_ProjectionWorker(),
            worker_id=worker_id,
            claim_lease_ms=1_000,
        ).publish_pending(limit=1)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(publish, ("publisher-a", "publisher-b")))

    assert sorted(int(row["published_count"]) for row in results) == [0, 1]
    timeline = RuntimeEventStore(db_path, clock=clock).list_events(
        source_kind="TASK_STORE_OUTBOX",
        limit=100,
    )
    assert len(timeline) == 1
    assert timeline[0]["aggregate_id"] == "graph-race@1"
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            """
            SELECT status, attempt_count, claim_fencing_token
            FROM task_runtime_outbox WHERE outbox_id = 'outbox-race'
            """
        ).fetchone() == ("PUBLISHED", 1, 1)


def test_publisher_startup_retries_foreign_claim_after_expiry_without_command(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "canonical.sqlite3"
    migrated = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="ne01-outbox-startup-liveness"
    )
    assert migrated.success is True, migrated.as_dict()
    _insert_outbox(
        db_path,
        outbox_id="outbox-startup-retry",
        aggregate_id="graph-startup-retry",
        created_ts_ms=int(time.time() * 1_000),
    )
    abandoned = DurableTaskStore(db_path).claim_pending_outbox(
        "publisher-old-process",
        150,
        limit=1,
    )
    assert len(abandoned) == 1

    publisher_events = RuntimeEventStore(db_path)
    publisher = RuntimeOutboxPublisher(
        task_store=DurableTaskStore(db_path),
        event_store=publisher_events,
        projection_worker=_ProjectionWorker(),
        worker_id="publisher-new-process",
        claim_lease_ms=1_000,
        background_batch_limit=10,
        max_poll_interval_ms=20,
    )
    startup_result = publisher.publish_pending(limit=10)
    try:
        assert startup_result["published_count"] == 0
        assert startup_result["backlog"]["total_pending"] == 1
        assert startup_result["backlog"]["delivery_ready_count"] == 0
        publisher.start()
        assert publisher.background_running

        deadline = time.monotonic() + 3
        timeline: list[dict[str, object]] = []
        while time.monotonic() < deadline:
            timeline = publisher_events.list_events(
                source_kind="TASK_STORE_OUTBOX",
                limit=100,
            )
            if timeline:
                break
            time.sleep(0.02)

        assert len(timeline) == 1
        assert timeline[0]["aggregate_id"] == "graph-startup-retry@1"
    finally:
        publisher.close()
    assert not publisher.background_running
