from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sqlite3

import pytest

from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.persistence.task_repository import DurableTaskStore
from host_brain.persistence.task_store_types import (
    TASK_STORE_CAS_CONFLICT,
    TASK_STORE_INVALID,
    TASK_STORE_LEASE_CONFLICT,
    TASK_STORE_OWNERSHIP_CONFLICT,
    TASK_STORE_SCHEMA_NOT_READY,
    TaskStoreError,
)


ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = ROOT / "host_brain" / "audit" / "migrations"


@dataclass
class _Clock:
    value: int

    def __call__(self) -> int:
        return self.value


def _store(tmp_path: Path, clock: _Clock) -> DurableTaskStore:
    db_path = tmp_path / "task-runtime-outbox.sqlite3"
    migrated = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="ne01-outbox-claim",
        ts_ms=clock.value,
    )
    assert migrated.success is True, migrated.as_dict()
    assert migrated.summary["current_version"] == 93
    return DurableTaskStore(db_path, clock=clock)


def _insert_outbox(
    store: DurableTaskStore,
    *,
    outbox_id: str,
    aggregate_id: str,
    aggregate_version: int,
    created_ts_ms: int,
    available_ts_ms: int = 0,
) -> None:
    aggregate_type = "TASK_GRAPH"
    payload_ref = (
        f"taskstore://event/{aggregate_type}/{aggregate_id}/{aggregate_version}"
    )
    with sqlite3.connect(store.db_path) as conn:
        conn.execute(
            """
            INSERT INTO task_runtime_outbox(
                outbox_id, aggregate_type, aggregate_id, aggregate_version,
                event_type, payload_ref, evidence_refs_json, attempt_id,
                idempotency_key, status, available_ts_ms, created_ts_ms,
                published_ts_ms, status_version
            ) VALUES (?, ?, ?, ?, ?, ?, '[]', NULL, ?, 'PENDING', ?, ?, NULL, 0)
            """,
            (
                outbox_id,
                aggregate_type,
                aggregate_id,
                aggregate_version,
                "TASK_GRAPH_CHANGED",
                payload_ref,
                f"idempotency::{outbox_id}",
                available_ts_ms,
                created_ts_ms,
            ),
        )


def test_claim_is_fenced_cross_process_and_ordered_per_aggregate(tmp_path: Path) -> None:
    clock = _Clock(100)
    store = _store(tmp_path, clock)
    _insert_outbox(
        store,
        outbox_id="outbox-a-v1",
        aggregate_id="graph-a",
        aggregate_version=1,
        created_ts_ms=1,
    )
    _insert_outbox(
        store,
        outbox_id="outbox-a-v2",
        aggregate_id="graph-a",
        aggregate_version=2,
        created_ts_ms=2,
    )
    _insert_outbox(
        store,
        outbox_id="outbox-b-v1",
        aggregate_id="graph-b",
        aggregate_version=1,
        created_ts_ms=3,
    )

    claimed = store.claim_pending_outbox("publisher-1", 50, limit=1)
    second_process = DurableTaskStore(store.db_path, clock=clock)
    parallel_claim = second_process.claim_pending_outbox(
        "publisher-2",
        50,
        limit=10,
    )

    assert [row["outbox_id"] for row in claimed] == ["outbox-a-v1"]
    assert [row["outbox_id"] for row in parallel_claim] == ["outbox-b-v1"]
    claimed.extend(parallel_claim)
    assert all(row["status"] == "PENDING" for row in claimed)
    assert [row["claim_owner"] for row in claimed] == [
        "publisher-1",
        "publisher-2",
    ]
    assert all(row["claim_token"] for row in claimed)
    assert all(row["claim_expires_ts_ms"] == 150 for row in claimed)
    assert all(row["claim_fencing_token"] == 1 for row in claimed)
    assert all(row["attempt_count"] == 1 for row in claimed)
    assert all(row["status_version"] == 1 for row in claimed)

    assert second_process.claim_pending_outbox("publisher-3", 50, limit=10) == []
    inventory = second_process.list_pending_outbox(limit=10)
    assert [row["outbox_id"] for row in inventory] == [
        "outbox-a-v1",
        "outbox-a-v2",
        "outbox-b-v1",
    ]
    assert second_process.inspect_outbox_delivery_backlog() == {
        "total_pending": 3,
        "delivery_ready_count": 0,
        "next_wakeup_ts_ms": 150,
    }

    with pytest.raises(TaskStoreError) as unfenced_publish:
        store.mark_outbox_published(
            claimed[0]["outbox_id"],
            expected_version=claimed[0]["status_version"],
        )
    assert unfenced_publish.value.reason_code == TASK_STORE_INVALID

    completed = store.complete_outbox_claim(
        claimed[0]["outbox_id"],
        "publisher-1",
        claimed[0]["claim_token"],
        claimed[0]["claim_fencing_token"],
        claimed[0]["status_version"],
    )
    assert completed["status"] == "PUBLISHED"
    assert completed["claim_owner"] is None
    assert completed["claim_token"] is None
    assert completed["claim_expires_ts_ms"] is None
    assert completed["status_version"] == 2

    assert second_process.inspect_outbox_delivery_backlog() == {
        "total_pending": 2,
        "delivery_ready_count": 1,
        "next_wakeup_ts_ms": 100,
    }

    next_claim = second_process.claim_pending_outbox("publisher-3", 50, limit=10)
    assert [row["outbox_id"] for row in next_claim] == ["outbox-a-v2"]


def test_expired_claim_takeover_increments_fence_and_rejects_stale_owner(
    tmp_path: Path,
) -> None:
    clock = _Clock(200)
    store = _store(tmp_path, clock)
    _insert_outbox(
        store,
        outbox_id="outbox-takeover",
        aggregate_id="graph-takeover",
        aggregate_version=1,
        created_ts_ms=1,
    )
    first = store.claim_pending_outbox("publisher-old", 10)[0]

    clock.value = 211
    takeover_store = DurableTaskStore(store.db_path, clock=clock)
    second = takeover_store.claim_pending_outbox("publisher-new", 10)[0]
    assert second["outbox_id"] == first["outbox_id"]
    assert second["claim_token"] != first["claim_token"]
    assert second["claim_fencing_token"] == first["claim_fencing_token"] + 1
    assert second["attempt_count"] == first["attempt_count"] + 1
    assert second["status_version"] == first["status_version"] + 1

    with pytest.raises(TaskStoreError) as stale_version:
        store.complete_outbox_claim(
            first["outbox_id"],
            "publisher-old",
            first["claim_token"],
            first["claim_fencing_token"],
            first["status_version"],
        )
    assert stale_version.value.reason_code == TASK_STORE_CAS_CONFLICT

    with pytest.raises(TaskStoreError) as stale_owner:
        store.complete_outbox_claim(
            first["outbox_id"],
            "publisher-old",
            first["claim_token"],
            first["claim_fencing_token"],
            second["status_version"],
        )
    assert stale_owner.value.reason_code in {
        TASK_STORE_OWNERSHIP_CONFLICT,
        TASK_STORE_LEASE_CONFLICT,
    }

    completed = takeover_store.complete_outbox_claim(
        second["outbox_id"],
        "publisher-new",
        second["claim_token"],
        second["claim_fencing_token"],
        second["status_version"],
    )
    assert completed["status"] == "PUBLISHED"


def test_release_backoff_blocks_the_same_aggregate_until_deadline(tmp_path: Path) -> None:
    clock = _Clock(500)
    store = _store(tmp_path, clock)
    _insert_outbox(
        store,
        outbox_id="outbox-backoff-v1",
        aggregate_id="graph-backoff",
        aggregate_version=1,
        created_ts_ms=1,
    )
    _insert_outbox(
        store,
        outbox_id="outbox-backoff-v2",
        aggregate_id="graph-backoff",
        aggregate_version=2,
        created_ts_ms=2,
    )
    first = store.claim_pending_outbox("publisher-1", 100, limit=10)
    assert [row["outbox_id"] for row in first] == ["outbox-backoff-v1"]

    released = store.release_outbox_claim(
        first[0]["outbox_id"],
        "publisher-1",
        first[0]["claim_token"],
        first[0]["claim_fencing_token"],
        retry_delay_ms=50,
        last_error="broker unavailable",
        expected_version=first[0]["status_version"],
    )
    assert released["available_ts_ms"] == 550
    assert released["last_error"] == "broker unavailable"
    assert released["claim_owner"] is None
    assert released["claim_token"] is None
    assert released["claim_expires_ts_ms"] is None

    clock.value = 549
    assert store.claim_pending_outbox("publisher-2", 100, limit=10) == []
    assert [
        row["outbox_id"] for row in store.list_pending_outbox(limit=10)
    ] == ["outbox-backoff-v1", "outbox-backoff-v2"]
    assert store.inspect_outbox_delivery_backlog() == {
        "total_pending": 2,
        "delivery_ready_count": 0,
        "next_wakeup_ts_ms": 550,
    }

    clock.value = 550
    retried = store.claim_pending_outbox("publisher-2", 100, limit=10)
    assert [row["outbox_id"] for row in retried] == ["outbox-backoff-v1"]
    assert retried[0]["claim_fencing_token"] == 2
    assert retried[0]["attempt_count"] == 2
    assert retried[0]["last_error"] is None
    assert all(row["outbox_id"] != "outbox-backoff-v2" for row in retried)


def test_0093_readiness_requires_columns_indexes_and_triggers(tmp_path: Path) -> None:
    db_path = tmp_path / "head-54.sqlite3"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            (MIGRATIONS / "0052_task_runtime.sql").read_text(encoding="utf-8")
        )
        conn.executescript(
            (MIGRATIONS / "0054_task_runtime_recovery.sql").read_text(
                encoding="utf-8"
            )
        )
    store = DurableTaskStore(db_path, verify_schema=False)
    with pytest.raises(TaskStoreError) as error:
        store.assert_outbox_delivery_ready(force_revalidate=True)
    assert error.value.reason_code == TASK_STORE_SCHEMA_NOT_READY

    migrated = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="ne01-outbox-upgrade",
        ts_ms=1,
    )
    assert migrated.success is True, migrated.as_dict()
    store.assert_outbox_delivery_ready()

    _insert_outbox(
        store,
        outbox_id="outbox-partial-claim",
        aggregate_id="graph-partial-claim",
        aggregate_version=1,
        created_ts_ms=1,
    )
    with sqlite3.connect(db_path) as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                UPDATE task_runtime_outbox
                SET claim_owner = ?, claim_expires_ts_ms = ?,
                    claim_fencing_token = 1, attempt_count = 1
                WHERE outbox_id = ?
                """,
                ("publisher-partial", 100, "outbox-partial-claim"),
            )

    with sqlite3.connect(db_path) as conn:
        conn.execute("DROP INDEX idx_task_runtime_outbox_delivery_ready")
    with pytest.raises(TaskStoreError) as missing_index:
        store.assert_outbox_delivery_ready(force_revalidate=True)
    assert missing_index.value.reason_code == TASK_STORE_SCHEMA_NOT_READY


def test_delivery_hot_paths_use_constructor_readiness_without_rescanning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _Clock(800)
    store = _store(tmp_path, clock)
    _insert_outbox(
        store,
        outbox_id="outbox-no-rescan",
        aggregate_id="graph-no-rescan",
        aggregate_version=1,
        created_ts_ms=1,
    )

    def _unexpected_full_scan() -> None:
        raise AssertionError("delivery hot path repeated a full readiness scan")

    monkeypatch.setattr(store, "assert_outbox_delivery_ready", _unexpected_full_scan)
    claimed = store.claim_pending_outbox("publisher-no-rescan", 100)[0]
    released = store.release_outbox_claim(
        claimed["outbox_id"],
        "publisher-no-rescan",
        claimed["claim_token"],
        claimed["claim_fencing_token"],
        retry_delay_ms=0,
        last_error="temporary failure",
        expected_version=claimed["status_version"],
    )
    claimed_again = store.claim_pending_outbox("publisher-no-rescan", 100)[0]
    assert claimed_again["status_version"] == released["status_version"] + 1
    completed = store.complete_outbox_claim(
        claimed_again["outbox_id"],
        "publisher-no-rescan",
        claimed_again["claim_token"],
        claimed_again["claim_fencing_token"],
        claimed_again["status_version"],
    )
    assert completed["status"] == "PUBLISHED"


@pytest.mark.parametrize(
    "assignment, values",
    [
        (
            "claim_owner = ?, claim_token = ?, claim_expires_ts_ms = ?, "
            "claim_fencing_token = ?, attempt_count = ?",
            (sqlite3.Binary(b"publisher"), "token", 900, 1, 1),
        ),
        (
            "claim_owner = ?, claim_token = ?, claim_expires_ts_ms = ?, "
            "claim_fencing_token = ?, attempt_count = ?",
            ("publisher", "token", "not-an-integer", 1, 1),
        ),
        (
            "claim_owner = ?, claim_token = ?, claim_expires_ts_ms = ?, "
            "claim_fencing_token = ?, attempt_count = ?",
            ("publisher", "token", 900, 2, 1),
        ),
    ],
)
def test_0093_guards_reject_dynamic_type_and_counter_drift(
    tmp_path: Path,
    assignment: str,
    values: tuple[object, ...],
) -> None:
    clock = _Clock(850)
    store = _store(tmp_path, clock)
    _insert_outbox(
        store,
        outbox_id="outbox-invalid-storage",
        aggregate_id="graph-invalid-storage",
        aggregate_version=1,
        created_ts_ms=1,
    )
    with sqlite3.connect(store.db_path) as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                f"""
                UPDATE task_runtime_outbox
                SET {assignment}
                WHERE outbox_id = ?
                """,
                (*values, "outbox-invalid-storage"),
            )


def test_outbox_codec_rejects_corruption_after_constructor_check(
    tmp_path: Path,
) -> None:
    clock = _Clock(900)
    store = _store(tmp_path, clock)
    _insert_outbox(
        store,
        outbox_id="outbox-codec-corruption",
        aggregate_id="graph-codec-corruption",
        aggregate_version=1,
        created_ts_ms=1,
    )
    with sqlite3.connect(store.db_path) as conn:
        conn.execute("PRAGMA ignore_check_constraints = ON")
        conn.execute("DROP TRIGGER trg_task_runtime_outbox_claim_update_guard")
        conn.execute(
            """
            UPDATE task_runtime_outbox
            SET claim_owner = ?, claim_token = ?, claim_expires_ts_ms = ?,
                claim_fencing_token = 2, attempt_count = 1
            WHERE outbox_id = ?
            """,
            ("publisher", "token", 1_000, "outbox-codec-corruption"),
        )

    with pytest.raises(TaskStoreError) as corrupted:
        store.list_pending_outbox()
    assert corrupted.value.reason_code == "TASK_STORE_DURABLE_DATA_INVALID"
