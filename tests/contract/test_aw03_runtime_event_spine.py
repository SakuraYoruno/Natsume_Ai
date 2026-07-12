from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.eventing.runtime_event_projection import RuntimeEventProjectionWorker
from host_brain.integrations.runtime_events import (
    RUNTIME_EVENT_DELIVERY_CLAIM_LOST,
    RUNTIME_EVENT_DELIVERY_MIGRATION_ASSET_KEY,
    RUNTIME_EVENT_DELIVERY_MIGRATION_SHA256,
    RUNTIME_EVENT_SCHEMA_NOT_READY,
    RuntimeEvent,
    RuntimeEventError,
    RuntimeEventStore,
)


ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = ROOT / "host_brain" / "audit" / "migrations"
NOW = 1_900_000_000_000


class MutableClock:
    def __init__(self, value: int = NOW) -> None:
        self.value = int(value)

    def __call__(self) -> int:
        return self.value


def _migrated_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "runtime-event-spine.sqlite3"
    result = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="aw03-runtime-event-spine",
        ts_ms=NOW,
    )
    assert result.success is True, result.as_dict()
    return db_path


def _task_outbox(index: int, *, aggregate_id: str = "graph-aw03-event") -> dict:
    return {
        "outbox_id": f"outbox::aw03-event::{index}",
        "aggregate_type": "TASK_GRAPH",
        "aggregate_id": aggregate_id,
        "aggregate_version": index,
        "event_type": "TASK_GRAPH_CREATED" if index == 1 else "TASK_GRAPH_UPDATED",
        "payload_ref": f"taskstore://event/TASK_GRAPH/{aggregate_id}/{index}",
        "idempotency_key": f"aw03-runtime-event-{aggregate_id}-{index}",
        "evidence_refs": (f"evidence://aw03-runtime-event/{index}",),
        "status": "PENDING",
        "created_ts_ms": NOW + index,
    }


def _record_migration_asset(conn: sqlite3.Connection) -> None:
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


def test_aw03_event_migration_is_catalogued_and_schema_drift_fails_closed(
    tmp_path: Path,
) -> None:
    db_path = _migrated_db(tmp_path)
    assert (
        hashlib.sha256(
            (MIGRATIONS / "0068_runtime_event_delivery.sql").read_bytes()
        ).hexdigest()
        == RUNTIME_EVENT_DELIVERY_MIGRATION_SHA256
    )
    with sqlite3.connect(str(db_path)) as conn:
        row = conn.execute(
            """
            SELECT checksum, disposition FROM schema_migration_assets
            WHERE asset_key = ?
            """,
            (RUNTIME_EVENT_DELIVERY_MIGRATION_ASSET_KEY,),
        ).fetchone()
        assert row == (RUNTIME_EVENT_DELIVERY_MIGRATION_SHA256, "APPLIED")
        conn.execute(
            "UPDATE schema_migration_assets SET checksum = ? WHERE asset_key = ?",
            ("0" * 64, RUNTIME_EVENT_DELIVERY_MIGRATION_ASSET_KEY),
        )

    with pytest.raises(RuntimeEventError) as error:
        RuntimeEventStore(db_path)
    assert error.value.reason_code == RUNTIME_EVENT_SCHEMA_NOT_READY


def test_aw03_task_event_journal_and_projection_intent_commit_as_one_uow(
    tmp_path: Path,
) -> None:
    db_path = _migrated_db(tmp_path)
    store = RuntimeEventStore(db_path, clock=lambda: NOW)

    event = store.append_task_outbox(_task_outbox(1))
    duplicate = store.append_task_outbox(_task_outbox(1))

    assert duplicate["cursor"] == event["cursor"]
    assert store.projection_health() == {
        "projection_name": "task_runtime_latest_v1",
        "pending": 1,
        "claimed": 0,
        "expired": 0,
        "completed": 0,
        "journal_cursor": event["cursor"],
        "projected_cursor": 0,
        "inbox_count": 0,
    }
    with sqlite3.connect(str(db_path)) as conn:
        outbox = conn.execute(
            """
            SELECT event_cursor, event_id, status
            FROM runtime_event_projection_outbox
            """
        ).fetchall()
        assert outbox == [(event["cursor"], event["event_id"], "PENDING")]
        with pytest.raises(sqlite3.IntegrityError, match="RUNTIME_EVENT_JOURNAL_IMMUTABLE"):
            conn.execute(
                "UPDATE runtime_event_timeline SET event_type = 'MUTATED' WHERE cursor = ?",
                (event["cursor"],),
            )


def test_aw03_projection_claim_is_lease_fenced_and_exactly_once(
    tmp_path: Path,
) -> None:
    clock = MutableClock()
    store = RuntimeEventStore(_migrated_db(tmp_path), clock=clock)
    store.append_task_outbox(_task_outbox(1))

    stale = store.claim_projection_delivery(worker_id="worker-a", lease_ms=10)
    assert stale is not None
    clock.value += 11
    recovered = store.claim_projection_delivery(worker_id="worker-b", lease_ms=10)
    assert recovered is not None
    assert recovered["delivery_id"] == stale["delivery_id"]
    assert recovered["claim_token"] != stale["claim_token"]

    with pytest.raises(RuntimeEventError) as error:
        store.complete_projection_delivery(
            delivery_id=stale["delivery_id"],
            worker_id="worker-a",
            claim_token=stale["claim_token"],
        )
    assert error.value.reason_code == RUNTIME_EVENT_DELIVERY_CLAIM_LOST

    completed = store.complete_projection_delivery(
        delivery_id=recovered["delivery_id"],
        worker_id="worker-b",
        claim_token=recovered["claim_token"],
    )
    assert completed["status"] == "COMPLETED"
    assert completed["applied"] is True
    assert store.projection_health()["inbox_count"] == 1


def test_aw03_projection_crash_rolls_back_inbox_and_recovers_after_lease(
    tmp_path: Path,
) -> None:
    clock = MutableClock()
    db_path = _migrated_db(tmp_path)

    def crash(stage: str) -> None:
        if stage == "after_projection_inbox":
            raise RuntimeError("simulated process crash")

    crashing = RuntimeEventStore(db_path, clock=clock, fault_injector=crash)
    crashing.append_task_outbox(_task_outbox(1))
    claimed = crashing.claim_projection_delivery(worker_id="crashed-worker", lease_ms=10)
    assert claimed is not None
    with pytest.raises(RuntimeError, match="simulated process crash"):
        crashing.complete_projection_delivery(
            delivery_id=claimed["delivery_id"],
            worker_id="crashed-worker",
            claim_token=claimed["claim_token"],
        )
    assert crashing.projection_health()["inbox_count"] == 0

    clock.value += 11
    recovered = RuntimeEventStore(db_path, clock=clock)
    result = RuntimeEventProjectionWorker(
        recovered,
        worker_id="recovery-worker",
        lease_ms=10,
    ).run_once()
    assert result["status"] == "COMPLETED"
    assert result["applied"] is True
    projection = recovered.get_task_projection(
        aggregate_type="TASK_GRAPH",
        aggregate_id="graph-aw03-event",
    )
    assert projection is not None
    assert projection["last_event_cursor"] == result["event_cursor"]
    assert recovered.projection_health()["inbox_count"] == 1


def test_aw03_replay_dedupes_and_projection_rebuild_is_deterministic(
    tmp_path: Path,
) -> None:
    store = RuntimeEventStore(_migrated_db(tmp_path), clock=lambda: NOW)
    first = store.append_task_outbox(_task_outbox(1))
    second = store.append_task_outbox(_task_outbox(2))
    worker = RuntimeEventProjectionWorker(store, worker_id="projection-worker")
    assert worker.drain(limit=10)["processed_count"] == 2
    before = store.get_task_projection(
        aggregate_type="TASK_GRAPH",
        aggregate_id="graph-aw03-event",
    )
    assert before is not None
    assert before["last_event_cursor"] == second["cursor"]

    first_delivery = (
        f"runtime-event-delivery://task-runtime-latest-v1/{first['cursor']}"
    )
    store.replay_projection_delivery(
        delivery_id=first_delivery,
        operator_ref="operator://aw03/replay",
    )
    replay = worker.run_once()
    assert replay["applied"] is False
    assert store.projection_health()["inbox_count"] == 2
    assert store.get_task_projection(
        aggregate_type="TASK_GRAPH",
        aggregate_id="graph-aw03-event",
    ) == before

    rebuilt = store.rebuild_task_projection(operator_ref="operator://aw03/rebuild")
    assert rebuilt["requeued_count"] == 2
    assert store.get_task_projection(
        aggregate_type="TASK_GRAPH",
        aggregate_id="graph-aw03-event",
    ) is None
    assert worker.drain(limit=10)["processed_count"] == 2
    after = store.get_task_projection(
        aggregate_type="TASK_GRAPH",
        aggregate_id="graph-aw03-event",
    )
    assert after is not None
    assert after["state_digest"] == before["state_digest"]
    assert after["last_event_cursor"] == before["last_event_cursor"]


def test_aw03_0068_backfills_old_task_journal_without_a_second_writer(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "old-runtime-events.sqlite3"
    payload = {
        "aggregate_id": "graph-old",
        "aggregate_type": "TASK_GRAPH",
        "aggregate_version": 1,
        "event_type": "TASK_GRAPH_CREATED",
        "outbox_id": "outbox::old",
        "payload_ref": "taskstore://event/TASK_GRAPH/graph-old/1",
        "status": "PENDING",
    }
    payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    payload_digest = "sha256:" + hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript((MIGRATIONS / "0056_runtime_events.sql").read_text(encoding="utf-8"))
        conn.execute(
            """
            INSERT INTO runtime_event_timeline(
                event_id, schema_version, event_type, source_kind, source_ref,
                aggregate_type, aggregate_id, aggregate_version, payload_ref,
                payload_json, payload_digest, evidence_refs_json, dedupe_key,
                redaction_level, ts_ms, created_ts_ms, published_ts_ms
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '[]', ?, 'PUBLIC', ?, ?, ?)
            """,
            (
                "runtimeevent://old/task-event",
                "Natsume.runtime.event.v1",
                "TASK_GRAPH_CREATED",
                "TASK_STORE_OUTBOX",
                "taskstore://outbox/old",
                "TASK_GRAPH",
                "graph-old",
                1,
                "taskstore://event/TASK_GRAPH/graph-old/1",
                payload_json,
                payload_digest,
                "sha256:" + "1" * 64,
                NOW,
                NOW,
                NOW,
            ),
        )
        conn.executescript(
            (MIGRATIONS / "0068_runtime_event_delivery.sql").read_text(encoding="utf-8")
        )
        _record_migration_asset(conn)

    store = RuntimeEventStore(db_path, clock=lambda: NOW)
    assert store.projection_health()["pending"] == 1
    result = RuntimeEventProjectionWorker(store, worker_id="backfill-worker").run_once()
    assert result["status"] == "COMPLETED"
    assert store.get_task_projection(
        aggregate_type="TASK_GRAPH",
        aggregate_id="graph-old",
    )["last_event_cursor"] == result["event_cursor"]


def test_aw03_event_slice_has_no_integration_private_connection_or_inline_ddl() -> None:
    integration_source = (
        ROOT / "host_brain" / "integrations" / "runtime_events.py"
    ).read_text(encoding="utf-8")
    worker_source = (
        ROOT / "host_brain" / "eventing" / "runtime_event_projection.py"
    ).read_text(encoding="utf-8")

    assert "sqlite3" not in integration_source
    assert "def _connect(" not in integration_source
    assert "CREATE TABLE" not in integration_source
    assert "asyncio.Queue" not in worker_source
    assert "deque(" not in worker_source

