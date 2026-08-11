from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from host_brain.contracts.agent_runtime.budget_policy import (
    BudgetAmount,
    BudgetReservation,
    BudgetReservationStatus,
)
from host_brain.contracts.agent_runtime.task_graph import TaskGraphStatus
from host_brain.persistence.task_repository import (
    AttemptStatus,
    DurableTaskStore,
    OutboxMessage,
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
    _seed_raw_graph,
    _event,
    _migrated_store,
    _set_time,
)


def _leased_store(
    tmp_path: Path,
    *,
    name: str = "heartbeat.sqlite3",
    lease_ttl_ms: int = 20,
    lease_outbox: OutboxMessage | None = None,
) -> tuple[DurableTaskStore, dict[str, object]]:
    store = _migrated_store(tmp_path, name=name)
    _seed_raw_graph(store)
    _create_attempt(store)
    lease = store.acquire_lease(
        "attempt-1",
        owner_id="worker-a",
        ttl_ms=lease_ttl_ms,
        expected_attempt_version=0,
        outbox=lease_outbox
        or _event(
            "TASK_ATTEMPT",
            "attempt-1",
            1,
            "TASK_ATTEMPT_LEASED",
            attempt_id="attempt-1",
        ),
    )
    return store, lease


def test_ne01_execution_authority_requires_one_active_graph_budget(
    tmp_path: Path,
) -> None:
    store = _migrated_store(tmp_path, name="execution-budget-required.sqlite3")
    _seed_raw_graph(store)

    with pytest.raises(TaskStoreError) as error:
        _create_attempt(store, ensure_budget=False)

    assert error.value.reason_code == TASK_STORE_CAS_CONFLICT
    snapshot = store.read_scheduler_snapshot("graph-1", 1)
    assert snapshot["attempts"] == []
    assert snapshot["active_leases"] == []


def test_ne01_lease_acquisition_exact_replay_is_read_only(tmp_path: Path) -> None:
    outbox = _event(
        "TASK_ATTEMPT",
        "attempt-1",
        1,
        "TASK_ATTEMPT_LEASED",
        idempotency_key="lease-acquisition-exact-replay",
        attempt_id="attempt-1",
    )
    store, first = _leased_store(tmp_path, lease_ttl_ms=20, lease_outbox=outbox)
    with sqlite3.connect(str(store.db_path)) as conn:
        before = tuple(
            conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in (
                "task_runtime_lease",
                "task_runtime_heartbeat",
                "task_runtime_outbox",
            )
        )

    replayed = store.acquire_lease(
        "attempt-1",
        owner_id="worker-a",
        ttl_ms=20,
        expected_attempt_version=0,
        outbox=outbox,
    )

    assert replayed == first
    with sqlite3.connect(str(store.db_path)) as conn:
        after = tuple(
            conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in (
                "task_runtime_lease",
                "task_runtime_heartbeat",
                "task_runtime_outbox",
            )
        )
    assert after == before


@pytest.mark.parametrize(
    "drift",
    ("ttl", "takeover-mode", "lease-fence", "lease-expiry", "heartbeat-owner"),
)
def test_ne01_lease_acquisition_replay_rejects_binding_drift(
    tmp_path: Path,
    drift: str,
) -> None:
    outbox = _event(
        "TASK_ATTEMPT",
        "attempt-1",
        1,
        "TASK_ATTEMPT_LEASED",
        idempotency_key=f"lease-acquisition-drift:{drift}",
        attempt_id="attempt-1",
    )
    store, lease = _leased_store(
        tmp_path,
        name=f"lease-acquisition-drift-{drift}.sqlite3",
        lease_ttl_ms=20,
        lease_outbox=outbox,
    )
    store.transition_graph(
        "graph-1",
        1,
        expected_version=0,
        target_status=TaskGraphStatus.ACTIVE,
        outbox=_event("TASK_GRAPH", "graph-1", 1, "TASK_GRAPH_ACTIVE"),
    )
    replay_ttl = 20
    absent_reservation_id = None
    with sqlite3.connect(str(store.db_path)) as conn:
        if drift == "ttl":
            replay_ttl = 21
        elif drift == "takeover-mode":
            absent_reservation_id = "reservation-drift"
        elif drift == "lease-fence":
            conn.execute(
                "UPDATE task_runtime_lease SET fencing_token = fencing_token + 1 "
                "WHERE lease_id = ?",
                (str(lease["lease_id"]),),
            )
        elif drift == "lease-expiry":
            conn.execute(
                "UPDATE task_runtime_lease SET expires_ts_ms = expires_ts_ms + 1 "
                "WHERE lease_id = ?",
                (str(lease["lease_id"]),),
            )
        else:
            conn.execute(
                "UPDATE task_runtime_heartbeat SET owner_id = 'worker-tampered' "
                "WHERE lease_id = ?",
                (str(lease["lease_id"]),),
            )
        before_replay = (
            tuple(conn.execute("SELECT * FROM task_runtime_lease ORDER BY lease_id")),
            tuple(
                conn.execute(
                    "SELECT * FROM task_runtime_heartbeat ORDER BY heartbeat_id"
                )
            ),
            tuple(conn.execute("SELECT * FROM task_runtime_outbox ORDER BY outbox_id")),
        )

    with pytest.raises(TaskStoreError) as rejected:
        store.acquire_lease(
            "attempt-1",
            owner_id="worker-a",
            ttl_ms=replay_ttl,
            expected_attempt_version=0,
            absent_operation_reservation_id=absent_reservation_id,
            outbox=outbox,
        )
    assert rejected.value.reason_code == TASK_STORE_CAS_CONFLICT
    with sqlite3.connect(str(store.db_path)) as conn:
        after_replay = (
            tuple(conn.execute("SELECT * FROM task_runtime_lease ORDER BY lease_id")),
            tuple(
                conn.execute(
                    "SELECT * FROM task_runtime_heartbeat ORDER BY heartbeat_id"
                )
            ),
            tuple(conn.execute("SELECT * FROM task_runtime_outbox ORDER BY outbox_id")),
        )
    assert after_replay == before_replay


def test_ne01_lease_acquisition_replay_binds_absent_reservation_identity(
    tmp_path: Path,
) -> None:
    store = _migrated_store(tmp_path, name="lease-reservation-identity.sqlite3")
    _seed_raw_graph(store)
    _create_attempt(store)
    store.transition_graph(
        "graph-1",
        1,
        expected_version=0,
        target_status=TaskGraphStatus.ACTIVE,
        outbox=_event("TASK_GRAPH", "graph-1", 1, "TASK_GRAPH_ACTIVE"),
    )
    outbox = _event(
        "TASK_ATTEMPT",
        "attempt-1",
        1,
        "TASK_ATTEMPT_LEASED",
        idempotency_key="lease-reservation-identity",
        attempt_id="attempt-1",
    )
    store.acquire_lease(
        "attempt-1",
        owner_id="worker-a",
        ttl_ms=20,
        expected_attempt_version=0,
        absent_operation_reservation_id="reservation-a",
        outbox=outbox,
    )
    with sqlite3.connect(str(store.db_path)) as conn:
        before = (
            tuple(conn.execute("SELECT * FROM task_runtime_lease ORDER BY lease_id")),
            tuple(conn.execute("SELECT * FROM task_runtime_heartbeat ORDER BY heartbeat_id")),
            tuple(conn.execute("SELECT * FROM task_runtime_outbox ORDER BY outbox_id")),
        )

    with pytest.raises(TaskStoreError) as rejected:
        store.acquire_lease(
            "attempt-1",
            owner_id="worker-a",
            ttl_ms=20,
            expected_attempt_version=0,
            absent_operation_reservation_id="reservation-b",
            outbox=outbox,
        )

    assert rejected.value.reason_code == TASK_STORE_CAS_CONFLICT
    with sqlite3.connect(str(store.db_path)) as conn:
        after = (
            tuple(conn.execute("SELECT * FROM task_runtime_lease ORDER BY lease_id")),
            tuple(conn.execute("SELECT * FROM task_runtime_heartbeat ORDER BY heartbeat_id")),
            tuple(conn.execute("SELECT * FROM task_runtime_outbox ORDER BY outbox_id")),
        )
    assert after == before


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


def _activate_graph(store: DurableTaskStore) -> None:
    store.transition_graph(
        "graph-1",
        1,
        expected_version=0,
        target_status=TaskGraphStatus.ACTIVE,
        outbox=_event("TASK_GRAPH", "graph-1", 1, "TASK_GRAPH_ACTIVE"),
    )


def test_ne01_task_store_port_exposes_heartbeat_authority() -> None:
    assert {
        "get_lease",
        "get_active_lease",
        "heartbeat_lease",
        "renew_operation_reservation",
    } <= set(TaskStorePort.__dict__)


@pytest.mark.parametrize(
    "expected_absent_id",
    ("reservation-before-takeover", "reservation-alias-for-same-business"),
)
def test_ne01_expired_takeover_atomically_refuses_an_operation_that_appeared(
    tmp_path: Path,
    expected_absent_id: str,
) -> None:
    store, lease = _leased_store(tmp_path, lease_ttl_ms=10)
    _reserve(store, lease, reservation_id="reservation-before-takeover", ttl_ms=10)
    before_attempt = store.get_attempt("attempt-1")
    before_lease = store.get_lease(str(lease["lease_id"]))
    before_expiry_events = len(_event_rows(store, "TASK_LEASE_EXPIRED"))

    _set_time(store, 111)
    with pytest.raises(TaskStoreError) as blocked:
        store.acquire_lease(
            "attempt-1",
            owner_id="worker-b",
            ttl_ms=50,
            expected_attempt_version=int(before_attempt["status_version"]),
            absent_operation_reservation_id=expected_absent_id,
            outbox=_event(
                "TASK_ATTEMPT",
                "attempt-1",
                int(before_attempt["status_version"]) + 1,
                "TASK_ATTEMPT_LEASED",
                attempt_id="attempt-1",
            ),
        )

    assert blocked.value.reason_code == TASK_STORE_IDEMPOTENCY_RECOVERY_REQUIRED
    assert store.get_attempt("attempt-1") == before_attempt
    assert store.get_lease(str(lease["lease_id"])) == before_lease
    assert len(_event_rows(store, "TASK_LEASE_EXPIRED")) == before_expiry_events
    with sqlite3.connect(str(store.db_path)) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM task_runtime_lease WHERE attempt_id = ?",
            ("attempt-1",),
        ).fetchone() == (1,)


def test_ne01_graph_terminal_transition_refuses_unsettled_operation_authority(
    tmp_path: Path,
) -> None:
    store, lease = _leased_store(tmp_path, lease_ttl_ms=100)
    _activate_graph(store)
    running = store.transition_attempt(
        "attempt-1",
        owner_id="worker-a",
        fencing_token=int(lease["fencing_token"]),
        expected_version=1,
        target_status=AttemptStatus.RUNNING,
        outbox=_event(
            "TASK_ATTEMPT",
            "attempt-1",
            2,
            "TASK_ATTEMPT_RUNNING",
            attempt_id="attempt-1",
        ),
    )
    _reserve(store, lease, reservation_id="reservation-terminal-fence", ttl_ms=50)
    store.transition_attempt(
        "attempt-1",
        owner_id="worker-a",
        fencing_token=int(lease["fencing_token"]),
        expected_version=int(running["status_version"]),
        target_status=AttemptStatus.SUCCEEDED,
        outbox=_event(
            "TASK_ATTEMPT",
            "attempt-1",
            int(running["status_version"]) + 1,
            "TASK_ATTEMPT_SUCCEEDED",
            attempt_id="attempt-1",
        ),
    )
    current_lease = store.get_lease(str(lease["lease_id"]))
    if current_lease["status"] == "ACTIVE":
        store.release_lease(
            str(lease["lease_id"]),
            owner_id="worker-a",
            fencing_token=int(lease["fencing_token"]),
            expected_lease_version=int(current_lease["status_version"]),
            outbox=_event(
                "TASK_LEASE",
                str(lease["lease_id"]),
                int(current_lease["status_version"]) + 1,
                "TASK_LEASE_RELEASED",
                attempt_id="attempt-1",
            ),
        )
    before = store.get_graph("graph-1", 1)

    with pytest.raises(TaskStoreError) as blocked:
        store.transition_graph(
            "graph-1",
            1,
            expected_version=int(before["status_version"]),
            target_status=TaskGraphStatus.FAILED,
            outbox=_event(
                "TASK_GRAPH",
                "graph-1",
                int(before["status_version"]) + 1,
                "TASK_GRAPH_FAILED",
            ),
        )

    assert blocked.value.reason_code == TASK_STORE_CAS_CONFLICT
    assert store.get_graph("graph-1", 1) == before
    assert store.get_operation_reservation("reservation-terminal-fence")["status"] == "RESERVED"


def test_ne01_terminal_graph_atomically_fences_later_attempt_authority(
    tmp_path: Path,
) -> None:
    store = _migrated_store(tmp_path, name="terminal-wins.sqlite3")
    _seed_raw_graph(store)
    _activate_graph(store)
    store.transition_graph(
        "graph-1",
        1,
        expected_version=1,
        target_status=TaskGraphStatus.FAILED,
        outbox=_event("TASK_GRAPH", "graph-1", 2, "TASK_GRAPH_FAILED"),
    )

    with pytest.raises(TaskStoreError) as blocked:
        _create_attempt(store)

    assert blocked.value.reason_code == TASK_STORE_CAS_CONFLICT
    assert store.get_graph("graph-1", 1)["status"] == "FAILED"
    with sqlite3.connect(str(store.db_path)) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM task_runtime_attempt"
        ).fetchone() == (0,)


def test_ne01_verifying_node_fences_graph_and_budget_terminal_writes(
    tmp_path: Path,
) -> None:
    store, lease = _leased_store(tmp_path, lease_ttl_ms=100)
    _activate_graph(store)
    budget_id = "budget-verifying-node"
    store.create_budget_reservation(
        BudgetReservation(
            budget_id=budget_id,
            scope="work-order:verifying-node",
            limits=BudgetAmount(token_count=10),
            reserved=BudgetAmount(token_count=10),
            status=BudgetReservationStatus.ACTIVE,
            status_version=0,
        ),
        graph_id="graph-1",
        graph_revision=1,
        outbox=_event(
            "BUDGET_RESERVATION",
            budget_id,
            0,
            "BUDGET_RESERVATION_CREATED",
        ),
    )
    node = store.get_node("graph-1", 1, "node-1")
    node = store.transition_node(
        "graph-1",
        1,
        "node-1",
        expected_version=int(node["status_version"]),
        target_status="RUNNING",
        outbox=_event(
            "TASK_NODE",
            "graph-1@1/node-1",
            int(node["status_version"]) + 1,
            "TASK_NODE_RUNNING",
        ),
    )
    attempt = store.transition_attempt(
        "attempt-1",
        owner_id="worker-a",
        fencing_token=int(lease["fencing_token"]),
        expected_version=1,
        target_status=AttemptStatus.RUNNING,
        outbox=_event(
            "TASK_ATTEMPT",
            "attempt-1",
            2,
            "TASK_ATTEMPT_RUNNING",
            attempt_id="attempt-1",
        ),
    )
    store.transition_attempt(
        "attempt-1",
        owner_id="worker-a",
        fencing_token=int(lease["fencing_token"]),
        expected_version=int(attempt["status_version"]),
        target_status=AttemptStatus.SUCCEEDED,
        outbox=_event(
            "TASK_ATTEMPT",
            "attempt-1",
            int(attempt["status_version"]) + 1,
            "TASK_ATTEMPT_SUCCEEDED",
            attempt_id="attempt-1",
        ),
    )
    for target in ("EXECUTED", "VERIFYING"):
        node = store.transition_node(
            "graph-1",
            1,
            "node-1",
            expected_version=int(node["status_version"]),
            target_status=target,
            outbox=_event(
                "TASK_NODE",
                "graph-1@1/node-1",
                int(node["status_version"]) + 1,
                f"TASK_NODE_{target}",
            ),
        )
    before_graph = store.get_graph("graph-1", 1)
    before_budget = store.reconcile_budget(budget_id)

    with pytest.raises(TaskStoreError) as graph_blocked:
        store.transition_graph(
            "graph-1",
            1,
            expected_version=int(before_graph["status_version"]),
            target_status=TaskGraphStatus.FAILED,
            outbox=_event(
                "TASK_GRAPH",
                "graph-1",
                int(before_graph["status_version"]) + 1,
                "TASK_GRAPH_FAILED",
            ),
        )
    with pytest.raises(TaskStoreError) as budget_blocked:
        store.transition_budget(
            budget_id,
            expected_version=int(before_budget["status_version"]),
            target_status=BudgetReservationStatus.RELEASED,
            outbox=_event(
                "BUDGET_RESERVATION",
                budget_id,
                int(before_budget["status_version"]) + 1,
                "BUDGET_RESERVATION_RELEASED",
            ),
        )

    assert graph_blocked.value.reason_code == TASK_STORE_CAS_CONFLICT
    assert budget_blocked.value.reason_code == TASK_STORE_CAS_CONFLICT
    assert store.get_graph("graph-1", 1) == before_graph
    assert store.reconcile_budget(budget_id) == before_budget
    assert store.get_node("graph-1", 1, "node-1")["status"] == "VERIFYING"


def test_ne01_get_lease_reopens_exact_terminal_row(tmp_path: Path) -> None:
    store, lease = _leased_store(tmp_path)
    with pytest.raises(TaskStoreError):
        store.get_lease("lease-does-not-exist")

    released = store.release_lease(
        str(lease["lease_id"]),
        owner_id="worker-a",
        fencing_token=int(lease["fencing_token"]),
        expected_lease_version=0,
        outbox=_event(
            "TASK_LEASE",
            str(lease["lease_id"]),
            1,
            "TASK_LEASE_RELEASED",
            attempt_id="attempt-1",
        ),
    )

    assert released["status"] == "RELEASED"
    assert store.get_lease(str(lease["lease_id"])) == released
    assert store.get_active_lease("attempt-1") is None


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
