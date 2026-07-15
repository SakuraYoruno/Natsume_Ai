from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from host_brain.persistence.task_repository import (
    DurableTaskStore,
    TASK_STORE_CAS_CONFLICT,
    TASK_STORE_IDEMPOTENCY_RECOVERY_REQUIRED,
    TASK_STORE_INVALID,
    TASK_STORE_LEASE_CONFLICT,
    TASK_STORE_OWNERSHIP_CONFLICT,
    TaskStoreError,
)
from host_brain.task_runtime.ports import TaskStorePort
from tests.contract.test_nle_task_store_contract import (
    _create_attempt,
    _create_graph,
    _event,
    _migrated_store,
    _set_time,
)


def _leased_store(
    tmp_path: Path,
    *,
    name: str = "heartbeat.sqlite3",
    lease_ttl_ms: int = 20,
) -> tuple[DurableTaskStore, dict[str, object]]:
    store = _migrated_store(tmp_path, name=name)
    _create_graph(store)
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
    return store, lease


def _reserve(
    store: DurableTaskStore,
    lease: dict[str, object],
    *,
    reservation_id: str,
    ttl_ms: int,
) -> dict[str, object]:
    result = store.reserve_operation(
        reservation_id=reservation_id,
        business_scope="repo:task-0467",
        operation_fingerprint="sha256:operation-1",
        attempt_id="attempt-1",
        lease_id=str(lease["lease_id"]),
        owner_id="worker-a",
        fencing_token=int(lease["fencing_token"]),
        ttl_ms=ttl_ms,
        outbox=_event(
            "OPERATION_RESERVATION",
            reservation_id,
            0,
            "OPERATION_RESERVED",
            attempt_id="attempt-1",
        ),
    )
    assert result.acquired is True
    return result.reservation


def _event_rows(store: DurableTaskStore, event_type: str) -> list[sqlite3.Row]:
    with sqlite3.connect(str(store.db_path)) as conn:
        conn.row_factory = sqlite3.Row
        return list(
            conn.execute(
                "SELECT * FROM task_runtime_outbox WHERE event_type = ? ORDER BY created_ts_ms, outbox_id",
                (event_type,),
            ).fetchall()
        )


def test_ne01_task_store_port_exposes_heartbeat_authority() -> None:
    assert {
        "get_active_lease",
        "heartbeat_lease",
        "renew_operation_reservation",
    } <= set(TaskStorePort.__dict__)


def test_ne01_heartbeat_extends_lease_version_and_attempt_bound_outbox(tmp_path: Path) -> None:
    store, lease = _leased_store(tmp_path)
    assert store.get_active_lease("attempt-1") == lease

    _set_time(store, 105)
    renewed = store.heartbeat_lease(
        str(lease["lease_id"]),
        owner_id="worker-a",
        fencing_token=int(lease["fencing_token"]),
        expected_lease_version=0,
        ttl_ms=50,
        outbox=_event(
            "TASK_LEASE",
            str(lease["lease_id"]),
            1,
            "TASK_LEASE_HEARTBEAT",
            attempt_id="attempt-1",
        ),
    )

    assert renewed["status_version"] == 1
    assert renewed["heartbeat_ts_ms"] == 105
    assert renewed["expires_ts_ms"] == 155
    assert store.get_active_lease("attempt-1") == renewed
    with sqlite3.connect(str(store.db_path)) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM task_runtime_heartbeat WHERE lease_id = ?",
            (lease["lease_id"],),
        ).fetchone() == (2,)
    rows = _event_rows(store, "TASK_LEASE_HEARTBEAT")
    assert len(rows) == 1
    assert rows[0]["aggregate_id"] == lease["lease_id"]
    assert rows[0]["aggregate_version"] == 1
    assert rows[0]["attempt_id"] == "attempt-1"


def test_ne01_lease_heartbeat_precedes_reservation_renewal_and_caps_expiry(
    tmp_path: Path,
) -> None:
    store, lease = _leased_store(tmp_path, lease_ttl_ms=20)
    reservation = _reserve(store, lease, reservation_id="reservation-renew", ttl_ms=10)
    assert reservation["expires_ts_ms"] == 110

    _set_time(store, 105)
    renewed_lease = store.heartbeat_lease(
        str(lease["lease_id"]),
        owner_id="worker-a",
        fencing_token=int(lease["fencing_token"]),
        expected_lease_version=0,
        ttl_ms=50,
        outbox=_event(
            "TASK_LEASE",
            str(lease["lease_id"]),
            1,
            "TASK_LEASE_HEARTBEAT",
            attempt_id="attempt-1",
        ),
    )
    renewed_reservation = store.renew_operation_reservation(
        "reservation-renew",
        attempt_id="attempt-1",
        lease_id=str(lease["lease_id"]),
        owner_id="worker-a",
        fencing_token=int(lease["fencing_token"]),
        expected_version=0,
        ttl_ms=100,
        outbox=_event(
            "OPERATION_RESERVATION",
            "reservation-renew",
            1,
            "OPERATION_RENEWED",
            attempt_id="attempt-1",
        ),
    )

    assert renewed_lease["expires_ts_ms"] == 155
    assert renewed_reservation["status_version"] == 1
    assert renewed_reservation["expires_ts_ms"] == renewed_lease["expires_ts_ms"]
    assert renewed_reservation["expires_ts_ms"] > lease["expires_ts_ms"]
    rows = _event_rows(store, "OPERATION_RENEWED")
    assert len(rows) == 1
    assert rows[0]["attempt_id"] == "attempt-1"
    assert rows[0]["aggregate_id"] == "reservation-renew"
    assert rows[0]["aggregate_version"] == 1


def test_ne01_heartbeat_and_reservation_renewal_exact_replay_after_response_loss(
    tmp_path: Path,
) -> None:
    store, lease = _leased_store(tmp_path, lease_ttl_ms=100)
    _reserve(store, lease, reservation_id="reservation-replay", ttl_ms=20)
    _set_time(store, 105)
    lease_outbox = _event(
        "TASK_LEASE",
        str(lease["lease_id"]),
        1,
        "TASK_LEASE_HEARTBEAT",
        attempt_id="attempt-1",
    )
    first_lease = store.heartbeat_lease(
        str(lease["lease_id"]),
        owner_id="worker-a",
        fencing_token=int(lease["fencing_token"]),
        expected_lease_version=0,
        ttl_ms=100,
        outbox=lease_outbox,
    )
    replayed_lease = store.heartbeat_lease(
        str(lease["lease_id"]),
        owner_id="worker-a",
        fencing_token=int(lease["fencing_token"]),
        expected_lease_version=0,
        ttl_ms=100,
        outbox=lease_outbox,
    )
    assert replayed_lease == first_lease
    assert len(_event_rows(store, "TASK_LEASE_HEARTBEAT")) == 1

    reservation_outbox = _event(
        "OPERATION_RESERVATION",
        "reservation-replay",
        1,
        "OPERATION_RENEWED",
        attempt_id="attempt-1",
    )
    first_reservation = store.renew_operation_reservation(
        "reservation-replay",
        attempt_id="attempt-1",
        lease_id=str(lease["lease_id"]),
        owner_id="worker-a",
        fencing_token=int(lease["fencing_token"]),
        expected_version=0,
        ttl_ms=20,
        outbox=reservation_outbox,
    )
    replayed_reservation = store.renew_operation_reservation(
        "reservation-replay",
        attempt_id="attempt-1",
        lease_id=str(lease["lease_id"]),
        owner_id="worker-a",
        fencing_token=int(lease["fencing_token"]),
        expected_version=0,
        ttl_ms=20,
        outbox=reservation_outbox,
    )
    assert replayed_reservation == first_reservation
    assert len(_event_rows(store, "OPERATION_RENEWED")) == 1

    with pytest.raises(TaskStoreError) as lease_ttl_drift:
        store.heartbeat_lease(
            str(lease["lease_id"]),
            owner_id="worker-a",
            fencing_token=int(lease["fencing_token"]),
            expected_lease_version=0,
            ttl_ms=99,
            outbox=lease_outbox,
        )
    assert lease_ttl_drift.value.reason_code == TASK_STORE_CAS_CONFLICT

    with pytest.raises(TaskStoreError) as reservation_ttl_drift:
        store.renew_operation_reservation(
            "reservation-replay",
            attempt_id="attempt-1",
            lease_id=str(lease["lease_id"]),
            owner_id="worker-a",
            fencing_token=int(lease["fencing_token"]),
            expected_version=0,
            ttl_ms=21,
            outbox=reservation_outbox,
        )
    assert reservation_ttl_drift.value.reason_code == TASK_STORE_CAS_CONFLICT


def test_ne01_heartbeat_and_reservation_renewal_fail_closed_on_stale_authority(
    tmp_path: Path,
) -> None:
    store, lease = _leased_store(tmp_path, lease_ttl_ms=50)
    _reserve(store, lease, reservation_id="reservation-stale", ttl_ms=30)

    heartbeat_cases = (
        ({"owner_id": "worker-b", "fencing_token": lease["fencing_token"], "expected_lease_version": 0}, TASK_STORE_OWNERSHIP_CONFLICT),
        ({"owner_id": "worker-a", "fencing_token": int(lease["fencing_token"]) + 1, "expected_lease_version": 0}, TASK_STORE_OWNERSHIP_CONFLICT),
        ({"owner_id": "worker-a", "fencing_token": lease["fencing_token"], "expected_lease_version": 1}, TASK_STORE_CAS_CONFLICT),
    )
    for authority, expected_reason in heartbeat_cases:
        with pytest.raises(TaskStoreError) as caught:
            store.heartbeat_lease(
                str(lease["lease_id"]),
                ttl_ms=20,
                outbox=_event(
                    "TASK_LEASE",
                    str(lease["lease_id"]),
                    1,
                    "TASK_LEASE_HEARTBEAT",
                    attempt_id="attempt-1",
                ),
                **authority,
            )
        assert caught.value.reason_code == expected_reason

    renewal_cases = (
        ({"owner_id": "worker-b", "fencing_token": lease["fencing_token"], "expected_version": 0}, TASK_STORE_OWNERSHIP_CONFLICT),
        ({"owner_id": "worker-a", "fencing_token": int(lease["fencing_token"]) + 1, "expected_version": 0}, TASK_STORE_OWNERSHIP_CONFLICT),
        ({"owner_id": "worker-a", "fencing_token": lease["fencing_token"], "expected_version": 1}, TASK_STORE_CAS_CONFLICT),
    )
    for authority, expected_reason in renewal_cases:
        with pytest.raises(TaskStoreError) as caught:
            store.renew_operation_reservation(
                "reservation-stale",
                attempt_id="attempt-1",
                lease_id=str(lease["lease_id"]),
                ttl_ms=20,
                outbox=_event(
                    "OPERATION_RESERVATION",
                    "reservation-stale",
                    1,
                    "OPERATION_RENEWED",
                    attempt_id="attempt-1",
                ),
                **authority,
            )
        assert caught.value.reason_code == expected_reason

    with pytest.raises(TaskStoreError) as wrong_attempt_outbox:
        store.renew_operation_reservation(
            "reservation-stale",
            attempt_id="attempt-1",
            lease_id=str(lease["lease_id"]),
            owner_id="worker-a",
            fencing_token=int(lease["fencing_token"]),
            expected_version=0,
            ttl_ms=20,
            outbox=_event(
                "OPERATION_RESERVATION",
                "reservation-stale",
                1,
                "OPERATION_RENEWED",
                attempt_id="attempt-other",
            ),
        )
    assert wrong_attempt_outbox.value.reason_code == TASK_STORE_INVALID
    assert store.get_active_lease("attempt-1")["status_version"] == 0
    assert store.get_operation_reservation("reservation-stale")["status_version"] == 0
    assert _event_rows(store, "TASK_LEASE_HEARTBEAT") == []
    assert _event_rows(store, "OPERATION_RENEWED") == []

    _set_time(store, 151)
    with pytest.raises(TaskStoreError) as expired_lease:
        store.heartbeat_lease(
            str(lease["lease_id"]),
            owner_id="worker-a",
            fencing_token=int(lease["fencing_token"]),
            expected_lease_version=0,
            ttl_ms=20,
            outbox=_event(
                "TASK_LEASE",
                str(lease["lease_id"]),
                1,
                "TASK_LEASE_HEARTBEAT",
                attempt_id="attempt-1",
            ),
        )
    assert expired_lease.value.reason_code == TASK_STORE_LEASE_CONFLICT


def test_ne01_expired_committed_and_recovery_required_reservations_cannot_renew(
    tmp_path: Path,
) -> None:
    expired_store, expired_lease = _leased_store(
        tmp_path,
        name="expired-reservation.sqlite3",
        lease_ttl_ms=50,
    )
    _reserve(expired_store, expired_lease, reservation_id="reservation-expired", ttl_ms=5)
    _set_time(expired_store, 106)
    with pytest.raises(TaskStoreError) as expired:
        expired_store.renew_operation_reservation(
            "reservation-expired",
            attempt_id="attempt-1",
            lease_id=str(expired_lease["lease_id"]),
            owner_id="worker-a",
            fencing_token=int(expired_lease["fencing_token"]),
            expected_version=0,
            ttl_ms=20,
            outbox=_event(
                "OPERATION_RESERVATION",
                "reservation-expired",
                1,
                "OPERATION_RENEWED",
                attempt_id="attempt-1",
            ),
        )
    assert expired.value.reason_code == TASK_STORE_IDEMPOTENCY_RECOVERY_REQUIRED
    recovery_required = expired_store.get_operation_reservation("reservation-expired")
    assert recovery_required["status"] == "RECOVERY_REQUIRED"
    assert recovery_required["status_version"] == 1
    with pytest.raises(TaskStoreError) as recovery_renew:
        expired_store.renew_operation_reservation(
            "reservation-expired",
            attempt_id="attempt-1",
            lease_id=str(expired_lease["lease_id"]),
            owner_id="worker-a",
            fencing_token=int(expired_lease["fencing_token"]),
            expected_version=1,
            ttl_ms=20,
            outbox=_event(
                "OPERATION_RESERVATION",
                "reservation-expired",
                2,
                "OPERATION_RENEWED",
                attempt_id="attempt-1",
            ),
        )
    assert recovery_renew.value.reason_code == TASK_STORE_CAS_CONFLICT

    committed_store, committed_lease = _leased_store(
        tmp_path,
        name="committed-reservation.sqlite3",
        lease_ttl_ms=50,
    )
    _reserve(committed_store, committed_lease, reservation_id="reservation-committed", ttl_ms=30)
    committed = committed_store.commit_operation(
        "reservation-committed",
        attempt_id="attempt-1",
        lease_id=str(committed_lease["lease_id"]),
        owner_id="worker-a",
        fencing_token=int(committed_lease["fencing_token"]),
        expected_version=0,
        receipt_ref="receipt://heartbeat/committed",
        outbox=_event(
            "OPERATION_RESERVATION",
            "reservation-committed",
            1,
            "OPERATION_COMMITTED",
            attempt_id="attempt-1",
        ),
    )
    assert committed["status"] == "COMMITTED"
    with pytest.raises(TaskStoreError) as committed_renew:
        committed_store.renew_operation_reservation(
            "reservation-committed",
            attempt_id="attempt-1",
            lease_id=str(committed_lease["lease_id"]),
            owner_id="worker-a",
            fencing_token=int(committed_lease["fencing_token"]),
            expected_version=1,
            ttl_ms=20,
            outbox=_event(
                "OPERATION_RESERVATION",
                "reservation-committed",
                2,
                "OPERATION_RENEWED",
                attempt_id="attempt-1",
            ),
        )
    assert committed_renew.value.reason_code == TASK_STORE_CAS_CONFLICT
    assert committed_store.get_operation_reservation("reservation-committed") == committed
    assert _event_rows(committed_store, "OPERATION_RENEWED") == []
