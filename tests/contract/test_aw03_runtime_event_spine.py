from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import sqlite3

import pytest

from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.eventing.runtime_event_projection import RuntimeEventProjectionWorker
from host_brain.integrations.runtime_events import (
    RUNTIME_EVENT_DELIVERY_CLAIM_LOST,
    RUNTIME_EVENT_DELIVERY_MIGRATION_ASSET_KEY,
    RUNTIME_EVENT_DELIVERY_MIGRATION_SHA256,
    RUNTIME_EVENT_DURABLE_DATA_INVALID,
    RUNTIME_EVENT_INVALID,
    RUNTIME_EVENT_SCHEMA_NOT_READY,
    RuntimeEvent,
    RuntimeEventError,
    RuntimeEventStore,
)
from host_brain.persistence.runtime_event_repository import (
    RUNTIME_CONTEXT_AUTHORITY_MIGRATION_ASSET_KEY,
    RUNTIME_CONTEXT_AUTHORITY_MIGRATION_SHA256,
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


def _migration_catalog_through(tmp_path: Path, version: int) -> Path:
    target = tmp_path / f"migrations-through-{version:04d}"
    target.mkdir()
    for path in MIGRATIONS.glob("*.sql"):
        if int(path.name.split("_", 1)[0]) <= version:
            shutil.copy2(path, target / path.name)
    return target


def _task_outbox(
    index: int,
    *,
    aggregate_id: str = "graph-aw03-event",
    graph_revision: int = 1,
    outbox_id: str | None = None,
    idempotency_key: str | None = None,
) -> dict:
    return {
        "outbox_id": outbox_id or f"outbox::aw03-event::{index}",
        "aggregate_type": "TASK_GRAPH",
        "aggregate_id": aggregate_id,
        "graph_revision": graph_revision,
        "aggregate_version": index,
        "event_type": "TASK_GRAPH_CREATED" if index == 1 else "TASK_GRAPH_UPDATED",
        "payload_ref": (
            f"taskstore://event/TASK_GRAPH/"
            f"{aggregate_id}@{graph_revision}/{index}"
        ),
        "idempotency_key": (
            idempotency_key or f"aw03-runtime-event-{aggregate_id}-{index}"
        ),
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


def test_ne03_runtime_context_schema_is_owned_by_runtime_event_repository(
    tmp_path: Path,
) -> None:
    db_path = _migrated_db(tmp_path)
    assert (
        hashlib.sha256(
            (MIGRATIONS / "0112_runtime_context_authority.sql").read_bytes()
        ).hexdigest()
        == RUNTIME_CONTEXT_AUTHORITY_MIGRATION_SHA256
    )
    with sqlite3.connect(str(db_path)) as conn:
        row = conn.execute(
            "SELECT checksum, disposition FROM schema_migration_assets "
            "WHERE asset_key = ?",
            (RUNTIME_CONTEXT_AUTHORITY_MIGRATION_ASSET_KEY,),
        ).fetchone()
    assert row == (RUNTIME_CONTEXT_AUTHORITY_MIGRATION_SHA256, "APPLIED")

    RuntimeEventStore(db_path).assert_runtime_context_authority_schema()


@pytest.mark.parametrize(
    ("damage", "detail"),
    (
        ("asset", "checksum drift"),
        ("index", "object missing"),
        ("trigger", "object missing"),
    ),
)
def test_ne03_runtime_context_schema_port_fails_closed_on_drift(
    tmp_path: Path,
    damage: str,
    detail: str,
) -> None:
    db_path = _migrated_db(tmp_path)
    with sqlite3.connect(str(db_path)) as conn:
        if damage == "asset":
            conn.execute(
                "UPDATE schema_migration_assets SET checksum = ? "
                "WHERE asset_key = ?",
                ("0" * 64, RUNTIME_CONTEXT_AUTHORITY_MIGRATION_ASSET_KEY),
            )
        elif damage == "index":
            conn.execute("DROP INDEX ux_runtime_context_aggregate_revision")
        else:
            conn.execute("DROP TRIGGER scene_state_requires_active_host_lease")

    with pytest.raises(RuntimeEventError, match=detail) as rejected:
        RuntimeEventStore(db_path).assert_runtime_context_authority_schema()
    assert rejected.value.reason_code == RUNTIME_EVENT_SCHEMA_NOT_READY


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
        aggregate_id="graph-aw03-event@1",
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
        aggregate_id="graph-aw03-event@1",
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
        aggregate_id="graph-aw03-event@1",
    ) == before

    rebuilt = store.rebuild_task_projection(operator_ref="operator://aw03/rebuild")
    assert rebuilt["requeued_count"] == 2
    assert store.get_task_projection(
        aggregate_type="TASK_GRAPH",
        aggregate_id="graph-aw03-event@1",
    ) is None
    assert worker.drain(limit=10)["processed_count"] == 2
    after = store.get_task_projection(
        aggregate_type="TASK_GRAPH",
        aggregate_id="graph-aw03-event@1",
    )
    assert after is not None
    assert after["state_digest"] == before["state_digest"]
    assert after["last_event_cursor"] == before["last_event_cursor"]


def test_aw03_task_graph_revisions_have_independent_runtime_projections(
    tmp_path: Path,
) -> None:
    store = RuntimeEventStore(_migrated_db(tmp_path), clock=lambda: NOW)
    first = store.append_task_outbox(
        _task_outbox(
            1,
            graph_revision=1,
            outbox_id="outbox::aw03-event::revision-1",
            idempotency_key="aw03-runtime-event-graph-revision-1",
        )
    )
    second = store.append_task_outbox(
        _task_outbox(
            1,
            graph_revision=2,
            outbox_id="outbox::aw03-event::revision-2",
            idempotency_key="aw03-runtime-event-graph-revision-2",
        )
    )
    assert first["aggregate_id"] == "graph-aw03-event@1"
    assert second["aggregate_id"] == "graph-aw03-event@2"

    worker = RuntimeEventProjectionWorker(store, worker_id="revision-projection-worker")
    assert worker.drain(limit=10)["processed_count"] == 2
    revision_one = store.get_task_projection(
        aggregate_type="TASK_GRAPH",
        aggregate_id="graph-aw03-event@1",
    )
    revision_two = store.get_task_projection(
        aggregate_type="TASK_GRAPH",
        aggregate_id="graph-aw03-event@2",
    )
    assert revision_one is not None
    assert revision_two is not None
    assert revision_one["state"]["payload"]["graph_revision"] == 1
    assert revision_two["state"]["payload"]["graph_revision"] == 2


def test_aw03_exact_revision_query_recovers_only_its_legacy_graph_timeline(
    tmp_path: Path,
) -> None:
    db_path = _migrated_db(tmp_path)
    graph_id = "graph-aw03-legacy-revisions"
    with sqlite3.connect(str(db_path)) as conn:
        for revision in (1, 2):
            conn.execute(
                """
                INSERT INTO task_runtime_graph(
                    graph_id, revision, root_work_order_id, planner_ref,
                    status, status_version, graph_json, source_kind,
                    source_ref, legacy_snapshot_json, created_ts_ms,
                    updated_ts_ms
                ) VALUES (?, ?, ?, ?, 'CREATED', 0, '{}', 'CANONICAL',
                          NULL, NULL, ?, ?)
                """,
                (
                    graph_id,
                    revision,
                    f"work-order::{graph_id}",
                    f"planner://aw03/{graph_id}/{revision}",
                    NOW,
                    NOW,
                ),
            )
            conn.execute(
                """
                INSERT INTO task_runtime_outbox(
                    outbox_id, aggregate_type, aggregate_id,
                    graph_revision, aggregate_version, event_type,
                    payload_ref, evidence_refs_json, attempt_id,
                    idempotency_key, status, available_ts_ms,
                    created_ts_ms, published_ts_ms, status_version
                ) VALUES (?, 'TASK_GRAPH', ?, ?, 0, 'TASK_GRAPH_CREATED',
                          ?, '[]', NULL, ?, 'PENDING', ?, ?, NULL, 0)
                """,
                (
                    f"legacy-created-r{revision}",
                    graph_id,
                    revision,
                    (
                        f"taskstore://event/TASK_GRAPH/"
                        f"{graph_id}@{revision}/0"
                    ),
                    f"idempotency:legacy-created-r{revision}",
                    NOW + revision,
                    NOW + revision,
                ),
            )

    store = RuntimeEventStore(db_path, clock=lambda: NOW + 10)
    legacy_events = {}
    for revision in (1, 2):
        legacy_events[revision] = store.append_event(
            RuntimeEvent.create(
                event_type="TASK_GRAPH_CREATED",
                source_kind="TASK_STORE_OUTBOX",
                source_ref=f"taskstore://outbox/legacy-created-r{revision}",
                aggregate_type="TASK_GRAPH",
                # Pre-0095 rows used the unrevisioned graph id here.
                aggregate_id=graph_id,
                aggregate_version=0,
                payload_ref=(
                    f"taskstore://event/TASK_GRAPH/{graph_id}@{revision}/0"
                ),
                payload={"legacy_revision": revision},
                evidence_refs=(f"evidence://aw03/legacy/{revision}",),
                ts_ms=NOW + revision,
            )
        )

    revision_one = store.list_events_for_aggregates(
        aggregate_refs=[("TASK_GRAPH", f"{graph_id}@1")],
        after_cursor=0,
        limit=10,
    )

    assert [event["cursor"] for event in revision_one] == [
        legacy_events[1]["cursor"]
    ]
    assert revision_one[0]["aggregate_id"] == graph_id
    assert legacy_events[2]["cursor"] not in {
        event["cursor"] for event in revision_one
    }


def test_aw03_0095_retry_reuses_preupgrade_outbox_runtime_event(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "runtime-event-pre-0095-retry.sqlite3"
    head_94 = SQLiteMigrationRunner(
        db_path=db_path,
        migrations_dir=_migration_catalog_through(tmp_path, 94),
    ).apply_migrations(request_id="aw03-runtime-event-head-94", ts_ms=NOW)
    assert head_94.success is True, head_94.as_dict()

    graph_id = "graph-aw03-pre-0095-retry"
    outbox_id = "outbox-aw03-pre-0095-retry"
    idempotency_key = "governed:aw03-pre-0095-retry"
    payload_ref = f"taskstore://event/TASK_GRAPH/{graph_id}/0"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO task_runtime_graph(
                graph_id, revision, root_work_order_id, planner_ref,
                status, status_version, graph_json, source_kind,
                source_ref, legacy_snapshot_json, created_ts_ms,
                updated_ts_ms
            ) VALUES (?, 1, ?, ?, 'CREATED', 0, '{}', 'CANONICAL',
                      NULL, NULL, ?, ?)
            """,
            (
                graph_id,
                f"work-order::{graph_id}",
                f"planner://aw03/{graph_id}/1",
                NOW,
                NOW,
            ),
        )
        conn.execute(
            """
            INSERT INTO task_runtime_outbox(
                outbox_id, aggregate_type, aggregate_id,
                aggregate_version, event_type, payload_ref,
                evidence_refs_json, attempt_id, idempotency_key, status,
                available_ts_ms, created_ts_ms, published_ts_ms,
                status_version
            ) VALUES (?, 'TASK_GRAPH', ?, 0, 'TASK_GRAPH_CREATED', ?,
                      '[]', NULL, ?, 'PENDING', ?, ?, NULL, 0)
            """,
            (outbox_id, graph_id, payload_ref, idempotency_key, NOW, NOW),
        )

    idempotency_digest = "sha256:" + hashlib.sha256(
        idempotency_key.encode("utf-8")
    ).hexdigest()
    legacy_payload = {
        "outbox_id": outbox_id,
        "aggregate_type": "TASK_GRAPH",
        "aggregate_id": graph_id,
        "aggregate_version": 0,
        "event_type": "TASK_GRAPH_CREATED",
        "payload_ref": payload_ref,
        "evidence_refs": [],
        "idempotency_digest": idempotency_digest,
        "status": "PENDING",
    }
    preupgrade_store = RuntimeEventStore(db_path, clock=lambda: NOW)
    original = preupgrade_store.append_event(
        RuntimeEvent.create(
            event_type="TASK_GRAPH_CREATED",
            source_kind="TASK_STORE_OUTBOX",
            source_ref=f"taskstore://outbox/{outbox_id}",
            aggregate_type="TASK_GRAPH",
            aggregate_id=graph_id,
            aggregate_version=0,
            payload_ref=payload_ref,
            payload=legacy_payload,
            ts_ms=NOW,
        )
    )

    upgraded = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="aw03-runtime-event-upgrade-0095",
        ts_ms=NOW + 1,
    )
    assert upgraded.success is True, upgraded.as_dict()
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        durable_outbox = dict(
            conn.execute(
                "SELECT * FROM task_runtime_outbox WHERE outbox_id = ?",
                (outbox_id,),
            ).fetchone()
        )
    durable_outbox["evidence_refs"] = ()

    upgraded_store = RuntimeEventStore(db_path, clock=lambda: NOW + 1)
    replayed = upgraded_store.append_task_outbox(durable_outbox)
    retried = upgraded_store.append_task_outbox(durable_outbox)

    assert replayed["cursor"] == retried["cursor"] == original["cursor"]
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            """
            SELECT COUNT(*) FROM runtime_event_timeline
            WHERE source_kind = 'TASK_STORE_OUTBOX' AND source_ref = ?
            """,
            (f"taskstore://outbox/{outbox_id}",),
        ).fetchone() == (1,)
        assert conn.execute(
            """
            SELECT COUNT(*)
            FROM runtime_event_projection_outbox AS delivery
            JOIN runtime_event_timeline AS event
              ON event.cursor = delivery.event_cursor
            WHERE event.source_kind = 'TASK_STORE_OUTBOX'
              AND event.source_ref = ?
            """,
            (f"taskstore://outbox/{outbox_id}",),
        ).fetchone() == (1,)

    drifted = dict(durable_outbox)
    drifted["idempotency_key"] = "governed:tampered"
    with pytest.raises(RuntimeEventError) as drift:
        upgraded_store.append_task_outbox(drifted)
    assert drift.value.reason_code == RUNTIME_EVENT_DURABLE_DATA_INVALID


def test_aw03_zero_created_timestamp_replay_keeps_the_single_source_event(
    tmp_path: Path,
) -> None:
    db_path = _migrated_db(tmp_path)
    outbox = _task_outbox(
        1,
        outbox_id="outbox::aw03-zero-created",
        idempotency_key="aw03-runtime-event-zero-created",
    )
    outbox["created_ts_ms"] = 0

    original = RuntimeEventStore(db_path, clock=lambda: NOW).append_task_outbox(
        outbox
    )
    replayed = RuntimeEventStore(
        db_path,
        clock=lambda: NOW + 10_000,
    ).append_task_outbox(outbox)

    assert replayed["cursor"] == original["cursor"]
    assert replayed["ts_ms"] == original["ts_ms"] == NOW
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            """
            SELECT COUNT(*) FROM runtime_event_timeline
            WHERE source_kind = 'TASK_STORE_OUTBOX'
              AND source_ref = 'taskstore://outbox/outbox::aw03-zero-created'
            """
        ).fetchone() == (1,)


@pytest.mark.parametrize("damage", ("missing_receipt", "spoofed_index"))
def test_aw03_revision_source_fence_readiness_fails_closed_on_partial_shape(
    tmp_path: Path,
    damage: str,
) -> None:
    db_path = _migrated_db(tmp_path)
    with sqlite3.connect(db_path) as conn:
        if damage == "missing_receipt":
            conn.execute(
                "DELETE FROM schema_migration_assets WHERE migration_id = 95"
            )
        else:
            conn.execute("DROP INDEX uq_runtime_event_task_outbox_source")
            conn.execute(
                """
                CREATE INDEX uq_runtime_event_task_outbox_source
                ON runtime_event_timeline(source_ref)
                """
            )
        conn.commit()

    with pytest.raises(RuntimeEventError) as rejected:
        RuntimeEventStore(db_path, clock=lambda: NOW)
    assert rejected.value.reason_code == RUNTIME_EVENT_SCHEMA_NOT_READY


@pytest.mark.parametrize("invalid_revision", (None, 0, True, "not-an-int"))
def test_aw03_task_graph_outbox_requires_an_exact_runtime_revision(
    tmp_path: Path,
    invalid_revision: object,
) -> None:
    store = RuntimeEventStore(_migrated_db(tmp_path), clock=lambda: NOW)
    row = _task_outbox(1)
    row["graph_revision"] = invalid_revision

    with pytest.raises(RuntimeEventError) as exc:
        store.append_task_outbox(row)
    assert exc.value.reason_code == RUNTIME_EVENT_INVALID


def test_aw03_non_graph_outbox_rejects_graph_revision(tmp_path: Path) -> None:
    store = RuntimeEventStore(_migrated_db(tmp_path), clock=lambda: NOW)
    row = _task_outbox(1)
    row.update(
        aggregate_type="TASK_NODE",
        aggregate_id="graph-aw03-event@1/node-1",
        graph_revision=1,
        payload_ref="taskstore://event/TASK_NODE/graph-aw03-event@1/node-1/1",
    )

    with pytest.raises(RuntimeEventError) as exc:
        store.append_task_outbox(row)
    assert exc.value.reason_code == RUNTIME_EVENT_INVALID


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
