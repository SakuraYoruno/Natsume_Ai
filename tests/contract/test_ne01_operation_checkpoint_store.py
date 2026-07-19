from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sqlite3
from threading import Event
from typing import Any

import pytest

from host_brain.persistence.task_repository import (
    TASK_STORE_DURABLE_DATA_INVALID,
    DurableTaskStore,
    TaskStoreError,
)
from tests.contract.test_nle_task_store_contract import (
    MutableClock,
    _create_attempt,
    _seed_raw_graph,
    _event,
    _migrated_store,
)


_RECEIPT_DIGEST = "a" * 64
_RECEIPT_REF = f"worker-projection://operation-checkpoint/{_RECEIPT_DIGEST}"
_DIGEST_REF = f"sha256://execution/{_RECEIPT_DIGEST}"


def _reserved_operation(
    tmp_path: Path,
    *,
    name: str,
    clock: MutableClock | None = None,
    lease_ttl_ms: int = 20,
    operation_ttl_ms: int = 20,
) -> tuple[DurableTaskStore, dict[str, Any]]:
    store = _migrated_store(tmp_path, name=name, clock=clock)
    _seed_raw_graph(store)
    _create_attempt(store)
    lease = store.acquire_lease(
        "attempt-1",
        owner_id="worker-a",
        ttl_ms=lease_ttl_ms,
        expected_attempt_version=0,
        outbox=_event(
            "TASK_ATTEMPT",
            "attempt-1",
            1,
            "TASK_ATTEMPT_LEASED",
            attempt_id="attempt-1",
        ),
    )
    store.reserve_operation(
        reservation_id="reservation-checkpoint",
        business_scope="repo:task-0467",
        operation_fingerprint="sha256:operation-1",
        attempt_id="attempt-1",
        lease_id=str(lease["lease_id"]),
        owner_id="worker-a",
        fencing_token=int(lease["fencing_token"]),
        ttl_ms=operation_ttl_ms,
        outbox=_event(
            "OPERATION_RESERVATION",
            "reservation-checkpoint",
            0,
            "OPERATION_RESERVED",
            attempt_id="attempt-1",
        ),
    )
    return store, lease


def _committed_operation(
    tmp_path: Path,
    *,
    name: str,
) -> tuple[DurableTaskStore, dict[str, Any]]:
    store, lease = _reserved_operation(tmp_path, name=name)
    committed = store.commit_operation(
        "reservation-checkpoint",
        attempt_id="attempt-1",
        lease_id=str(lease["lease_id"]),
        owner_id="worker-a",
        fencing_token=int(lease["fencing_token"]),
        expected_version=0,
        receipt_ref=_RECEIPT_REF,
        outbox=_event(
            "OPERATION_RESERVATION",
            "reservation-checkpoint",
            1,
            "OPERATION_COMMITTED",
            attempt_id="attempt-1",
            evidence_refs=(_RECEIPT_REF, _DIGEST_REF),
        ),
    )
    return store, committed


def test_committed_operation_checkpoint_returns_one_exact_durable_binding(
    tmp_path: Path,
) -> None:
    store, committed = _committed_operation(tmp_path, name="checkpoint-exact.sqlite3")

    checkpoint = store.get_committed_operation_checkpoint(
        "reservation-checkpoint"
    )

    assert checkpoint["reservation"] == committed
    assert checkpoint["attempt"]["attempt_id"] == "attempt-1"
    assert checkpoint["lease"]["lease_id"] == committed["lease_id"]
    assert checkpoint["outbox"]["attempt_id"] == "attempt-1"
    assert checkpoint["outbox"]["evidence_refs"] == [
        _RECEIPT_REF,
        _DIGEST_REF,
    ]
    assert checkpoint["checkpoint_digest_ref"] == _DIGEST_REF


def test_committed_operation_checkpoint_reads_one_sqlite_snapshot(
    tmp_path: Path,
) -> None:
    store, _ = _committed_operation(tmp_path, name="checkpoint-snapshot.sqlite3")
    reservation_read = Event()
    writer_committed = Event()
    original_operation_record = store._operation_record
    observed = False

    def operation_record_with_interleave(row: Any) -> dict[str, Any]:
        nonlocal observed
        record = original_operation_record(row)
        if record["status"] == "COMMITTED" and not observed:
            observed = True
            reservation_read.set()
            # A writer may update its private page, but its COMMIT must remain
            # blocked until this checkpoint releases the read snapshot.
            assert writer_committed.wait(timeout=0.2) is False
        return record

    store._operation_record = operation_record_with_interleave  # type: ignore[method-assign]  # noqa: SLF001

    def tamper_historical_lease() -> None:
        assert reservation_read.wait(timeout=2.0)
        with sqlite3.connect(str(store.db_path), timeout=2.0) as conn:
            conn.execute("PRAGMA busy_timeout = 2000")
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "UPDATE task_runtime_lease SET owner_id = 'worker-forged' WHERE lease_id = ?",
                (store.get_operation_reservation("reservation-checkpoint")["lease_id"],),
            )
            conn.commit()
        writer_committed.set()

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(tamper_historical_lease)
        checkpoint = store.get_committed_operation_checkpoint(
            "reservation-checkpoint"
        )
        future.result(timeout=3.0)

    assert checkpoint["lease"]["owner_id"] == "worker-a"
    with pytest.raises(TaskStoreError) as drifted:
        store.get_committed_operation_checkpoint("reservation-checkpoint")
    assert drifted.value.reason_code == TASK_STORE_DURABLE_DATA_INVALID


@pytest.mark.parametrize(
    ("case_name", "sql", "parameters"),
    (
        (
            "missing-outbox",
            "DELETE FROM task_runtime_outbox WHERE event_type = 'OPERATION_COMMITTED'",
            (),
        ),
        (
            "multiple-outbox",
            """
            INSERT INTO task_runtime_outbox(
                outbox_id, aggregate_type, aggregate_id, aggregate_version,
                event_type, payload_ref, evidence_refs_json, attempt_id,
                idempotency_key, status, available_ts_ms, created_ts_ms,
                published_ts_ms, status_version
            )
            SELECT
                'outbox::forged-duplicate', aggregate_type, aggregate_id,
                aggregate_version, event_type, payload_ref, evidence_refs_json,
                attempt_id, 'operation-commit-forged-duplicate', status,
                available_ts_ms, created_ts_ms, published_ts_ms, status_version
            FROM task_runtime_outbox
            WHERE event_type = 'OPERATION_COMMITTED'
            """,
            (),
        ),
        (
            "attempt-scope-drift",
            "UPDATE task_runtime_attempt SET operation_scope = ? WHERE attempt_id = 'attempt-1'",
            ("repo:wrong",),
        ),
        (
            "lease-missing",
            "DELETE FROM task_runtime_lease WHERE lease_id = ?",
            ("__LEASE_ID__",),
        ),
        (
            "lease-owner-drift",
            "UPDATE task_runtime_lease SET owner_id = ? WHERE lease_id = ?",
            ("worker-forged", "__LEASE_ID__"),
        ),
        (
            "lease-fence-drift",
            "UPDATE task_runtime_lease SET fencing_token = fencing_token + 1 WHERE lease_id = ?",
            ("__LEASE_ID__",),
        ),
        (
            "outbox-attempt-drift",
            "UPDATE task_runtime_outbox SET attempt_id = NULL WHERE event_type = 'OPERATION_COMMITTED'",
            (),
        ),
        (
            "outbox-version-drift",
            "UPDATE task_runtime_outbox SET aggregate_version = aggregate_version + 1 WHERE event_type = 'OPERATION_COMMITTED'",
            (),
        ),
        (
            "receipt-evidence-drift",
            "UPDATE task_runtime_outbox SET evidence_refs_json = ? WHERE event_type = 'OPERATION_COMMITTED'",
            (
                json.dumps(
                    [
                        "worker-projection://operation-checkpoint/"
                        + "b" * 64,
                        _DIGEST_REF,
                    ]
                ),
            ),
        ),
        (
            "outbox-evidence-drift",
            "UPDATE task_runtime_outbox SET evidence_refs_json = ? WHERE event_type = 'OPERATION_COMMITTED'",
            (json.dumps([_RECEIPT_REF, "sha256://execution/short"]),),
        ),
    ),
)
def test_committed_operation_checkpoint_rejects_missing_or_cross_bound_history(
    tmp_path: Path,
    case_name: str,
    sql: str,
    parameters: tuple[Any, ...],
) -> None:
    store, committed = _committed_operation(
        tmp_path,
        name=f"checkpoint-{case_name}.sqlite3",
    )
    bound_parameters = tuple(
        committed["lease_id"] if value == "__LEASE_ID__" else value
        for value in parameters
    )
    with sqlite3.connect(str(store.db_path)) as conn:
        conn.execute(sql, bound_parameters)

    with pytest.raises(TaskStoreError) as error:
        store.get_committed_operation_checkpoint("reservation-checkpoint")
    assert error.value.reason_code == TASK_STORE_DURABLE_DATA_INVALID


def test_reconcile_operation_reservation_state_marks_expiry_once(
    tmp_path: Path,
) -> None:
    clock = MutableClock(100)
    store, _ = _reserved_operation(
        tmp_path,
        name="checkpoint-expiry.sqlite3",
        clock=clock,
        lease_ttl_ms=20,
        operation_ttl_ms=5,
    )
    clock.set(106)

    first = store.reconcile_operation_reservation_state(
        "reservation-checkpoint"
    )
    second = store.reconcile_operation_reservation_state(
        "reservation-checkpoint"
    )

    assert first["status"] == "RECOVERY_REQUIRED"
    assert first["status_version"] == 1
    assert first["recovery_reason_code"] == "IDEMPOTENCY_RESERVATION_EXPIRED"
    assert second == first
    with sqlite3.connect(str(store.db_path)) as conn:
        count = conn.execute(
            """
            SELECT COUNT(*) FROM task_runtime_outbox
            WHERE aggregate_type = 'OPERATION_RESERVATION'
              AND aggregate_id = 'reservation-checkpoint'
              AND event_type = 'OPERATION_RECOVERY_REQUIRED'
            """
        ).fetchone()
    assert count == (1,)


def test_reconcile_operation_reservation_state_keeps_live_reservation_unchanged(
    tmp_path: Path,
) -> None:
    store, _ = _reserved_operation(tmp_path, name="checkpoint-live.sqlite3")

    reservation = store.reconcile_operation_reservation_state(
        "reservation-checkpoint"
    )

    assert reservation["status"] == "RESERVED"
    assert reservation["status_version"] == 0
    with sqlite3.connect(str(store.db_path)) as conn:
        count = conn.execute(
            """
            SELECT COUNT(*) FROM task_runtime_outbox
            WHERE event_type = 'OPERATION_RECOVERY_REQUIRED'
            """
        ).fetchone()
    assert count == (0,)
