from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import base64
from dataclasses import dataclass
import hashlib
import inspect
import json
import math
from pathlib import Path
import sqlite3
from typing import Any
import uuid

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from host_brain.agent_runtime.budget_policy import (
    BudgetAmount,
    BudgetReservation,
    BudgetReservationStatus,
)
import host_brain.agent_runtime.task_store as task_store_module
from host_brain.agent_runtime.job_registry import AgentJobRegistry
from host_brain.agent_runtime.task_graph import (
    TaskGraph,
    TaskGraphStatus,
    TaskNode,
    TaskNodeStatus,
)
from host_brain.agent_runtime.task_store import (
    AttemptStatus,
    DurableTaskStore,
    Ed25519ReconciliationProofVerifier,
    IdempotencyRecoveryAction,
    OutboxMessage,
    TASK_RUNTIME_TABLES,
    TASK_STORE_BUDGET_EXCEEDED,
    TASK_STORE_CANCELLATION_CONFLICT,
    TASK_STORE_CAS_CONFLICT,
    TASK_STORE_DURABLE_DATA_INVALID,
    TASK_STORE_IDEMPOTENCY_RECOVERY_REQUIRED,
    TASK_STORE_INVALID,
    TASK_STORE_LEASE_CONFLICT,
    TASK_STORE_OUTBOX_CONFLICT,
    TASK_STORE_OWNERSHIP_CONFLICT,
    TASK_STORE_RECONCILIATION_PROOF_REQUIRED,
    TASK_STORE_RECONCILIATION_PROOF_INVALID,
    TASK_STORE_RECONCILIATION_AUTHORITY_REQUIRED,
    TASK_STORE_SCHEMA_NOT_READY,
    TaskStoreError,
    build_outbox_payload_ref,
    build_reconciliation_attestation_binding,
    build_operation_fingerprint,
)
from host_brain.audit.migration_runner import SQLiteMigrationRunner


ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = ROOT / "host_brain" / "audit" / "migrations"
TASK_RUNTIME_BASE_MIGRATION = MIGRATIONS / "0052_task_runtime.sql"
TASK_RUNTIME_RECOVERY_MIGRATION = MIGRATIONS / "0054_task_runtime_recovery.sql"


@dataclass
class MutableClock:
    value: int = 100

    def __call__(self) -> int:
        return self.value

    def set(self, value: int) -> None:
        self.value = int(value)


def _set_time(store: DurableTaskStore, value: int) -> None:
    clock = store._clock
    assert isinstance(clock, MutableClock)
    clock.set(value)


def _proof_authority() -> tuple[Ed25519PrivateKey, Ed25519ReconciliationProofVerifier]:
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return private_key, Ed25519ReconciliationProofVerifier({"reconciler-v1": public_key})


def _sign_proof(private_key: Ed25519PrivateKey, **binding: Any) -> str:
    signature = private_key.sign(build_reconciliation_attestation_binding(**binding))
    return "ed25519:" + base64.urlsafe_b64encode(signature).decode("ascii")


def _expected_legacy_receipt_digest(value: str | bytes | None) -> str:
    if value is None:
        payload = b"null:"
    elif isinstance(value, bytes):
        payload = b"blob:" + value
    else:
        payload = b"text:" + value.encode("utf-8", errors="surrogatepass")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _event(
    aggregate_type: str,
    aggregate_id: str,
    aggregate_version: int,
    event_type: str,
    *,
    idempotency_key: str | None = None,
    attempt_id: str | None = None,
) -> OutboxMessage:
    unique = uuid.uuid4().hex
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
        idempotency_key=idempotency_key or f"event:{unique}",
        attempt_id=attempt_id,
    )


def _graph(*, graph_id: str = "graph-1", node_status: TaskNodeStatus = TaskNodeStatus.READY) -> TaskGraph:
    node = TaskNode(
        node_id="node-1",
        dependencies=(),
        route="skill:test",
        input_refs=("artifact://input/1",),
        output_contract={"type": "test-result"},
        retry_policy={"max_attempts": 2},
        risk_level=2,
        acceptance_criteria=("exit code is zero",),
        status=node_status,
        status_version=0,
    )
    graph = TaskGraph(
        graph_id=graph_id,
        root_work_order_id=f"work-order::{graph_id}",
        revision=1,
        planner_ref="planner://test/1",
        status=TaskGraphStatus.CREATED,
        created_at=1_700_000_000_000,
        nodes=(node,),
    )
    graph.validate()
    return graph


def _migrated_store(
    tmp_path: Path,
    *,
    name: str = "task-runtime.sqlite3",
    clock: MutableClock | None = None,
    verifier: Ed25519ReconciliationProofVerifier | None = None,
) -> DurableTaskStore:
    db_path = tmp_path / name
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(TASK_RUNTIME_BASE_MIGRATION.read_text(encoding="utf-8"))
        conn.executescript(TASK_RUNTIME_RECOVERY_MIGRATION.read_text(encoding="utf-8"))
    return DurableTaskStore(
        db_path,
        clock=clock or MutableClock(),
        reconciliation_proof_verifier=verifier,
    )


def _create_graph(store: DurableTaskStore, *, graph_id: str = "graph-1") -> None:
    store.create_graph(
        _graph(graph_id=graph_id),
        outbox=_event("TASK_GRAPH", graph_id, 0, "TASK_GRAPH_CREATED"),
    )


def _create_attempt(
    store: DurableTaskStore,
    *,
    attempt_id: str = "attempt-1",
    operation_scope: str = "repo:task-0467",
    operation_fingerprint: str = "sha256:operation-1",
) -> None:
    store.create_attempt(
        attempt_id=attempt_id,
        graph_id="graph-1",
        graph_revision=1,
        node_id="node-1",
        attempt_no=1,
        expected_node_version=0,
        operation_scope=operation_scope,
        operation_fingerprint=operation_fingerprint,
        outbox=_event("TASK_ATTEMPT", attempt_id, 0, "TASK_ATTEMPT_CREATED", attempt_id=attempt_id),
    )


def _advance_node_to_waiting_acceptance(store: DurableTaskStore) -> None:
    for expected_version, target in enumerate(
        (
            TaskNodeStatus.RUNNING,
            TaskNodeStatus.EXECUTED,
            TaskNodeStatus.VERIFYING,
            TaskNodeStatus.VERIFIED,
            TaskNodeStatus.WAITING_ACCEPTANCE_REVIEW,
        ),
        start=1,
    ):
        store.transition_node(
            "graph-1",
            1,
            "node-1",
            expected_version=expected_version,
            target_status=target,
            outbox=_event(
                "TASK_NODE",
                "graph-1@1/node-1",
                expected_version + 1,
                f"TASK_NODE_{target.value}",
            ),
        )


def test_t_nle_06_requires_forward_0054_recovery_migration(tmp_path: Path) -> None:
    assert TASK_RUNTIME_BASE_MIGRATION.is_file()
    assert TASK_RUNTIME_RECOVERY_MIGRATION.is_file()
    assert len(list(MIGRATIONS.glob("0052_task_runtime.sql"))) == 1
    assert "RECOVERY_REQUIRED" not in TASK_RUNTIME_BASE_MIGRATION.read_text(encoding="utf-8")

    db_path = tmp_path / "empty.sqlite3"
    sqlite3.connect(str(db_path)).close()
    with pytest.raises(TaskStoreError) as error:
        DurableTaskStore(db_path)
    assert error.value.reason_code == TASK_STORE_SCHEMA_NOT_READY

    store = _migrated_store(tmp_path, name="migrated.sqlite3")
    with sqlite3.connect(str(store.db_path)) as conn:
        tables = {
            str(row[0])
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        reservation_columns = {
            str(row[1])
            for row in conn.execute(
                "PRAGMA table_info(task_runtime_idempotency_reservation)"
            )
        }
    assert TASK_RUNTIME_TABLES <= tables
    assert {
        "lease_id",
        "owner_id",
        "fencing_token",
        "expires_ts_ms",
        "legacy_receipt_raw",
        "reconciliation_proof_ref",
        "reconciliation_proof_digest",
        "reconciliation_proof_key_id",
        "reconciliation_proof_signature",
        "reconciliation_action",
        "reconciliation_outcome",
        "reconciler_actor_id",
        "reconciliation_recovery_version",
        "recovery_reason_code",
        "recovery_ts_ms",
    } <= reservation_columns
    assert "RECOVERY_REQUIRED" in TASK_RUNTIME_RECOVERY_MIGRATION.read_text(encoding="utf-8")


def test_t_nle_06_0052_to_0054_upgrade_retains_data_and_runner_reapply_is_idempotent(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "legacy-0052.sqlite3"
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(TASK_RUNTIME_BASE_MIGRATION.read_text(encoding="utf-8"))
        conn.execute(
            """
            INSERT INTO task_runtime_idempotency_reservation(
                reservation_id, business_scope, operation_fingerprint, attempt_id,
                status, status_version, receipt_ref, created_ts_ms, updated_ts_ms
            ) VALUES ('legacy-reserved', 'scope-a', 'fingerprint-a', NULL,
                      'RESERVED', 4, NULL, 10, 20)
            """
        )
        conn.execute(
            """
            INSERT INTO task_runtime_idempotency_reservation(
                reservation_id, business_scope, operation_fingerprint, attempt_id,
                status, status_version, receipt_ref, created_ts_ms, updated_ts_ms
            ) VALUES ('legacy-committed', 'scope-b', 'fingerprint-b', NULL,
                      'COMMITTED', 7, 'receipt://legacy/committed', 11, 21)
            """
        )
        legacy_receipts: dict[str, str | bytes | None] = {
            "legacy-opaque": "opaque-receipt-id",
            "legacy-empty": "",
            "legacy-control": "line-one\x00line-two\nsecret-like-control",
            "legacy-oversize": "x" * 70_000,
            "legacy-blob": b"\xff\x00opaque-blob",
            "legacy-null": None,
        }
        for index, (reservation_id, raw_receipt) in enumerate(
            legacy_receipts.items(),
            start=3,
        ):
            conn.execute(
                """
                INSERT INTO task_runtime_idempotency_reservation(
                    reservation_id, business_scope, operation_fingerprint, attempt_id,
                    status, status_version, receipt_ref, created_ts_ms, updated_ts_ms
                ) VALUES (?, ?, ?, NULL, 'COMMITTED', 0, ?, 12, 22)
                """,
                (reservation_id, f"scope-{index}", f"fingerprint-{index}", raw_receipt),
            )
        conn.commit()
        conn.executescript(TASK_RUNTIME_RECOVERY_MIGRATION.read_text(encoding="utf-8"))
        rows = {
            str(row[0]): tuple(row[1:])
            for row in conn.execute(
                """
                SELECT reservation_id, status, status_version, receipt_ref,
                       recovery_reason_code, attempt_id, lease_id, legacy_receipt_raw
                FROM task_runtime_idempotency_reservation
                ORDER BY reservation_id
                """
            )
        }
        migration_outbox = conn.execute(
            """
            SELECT event_type, aggregate_version
            FROM task_runtime_outbox
            WHERE aggregate_id = 'legacy-reserved'
            """
        ).fetchone()
    assert rows["legacy-reserved"] == (
        "RECOVERY_REQUIRED",
        5,
        None,
        "LEGACY_0052_UNBOUND_RESERVATION",
        None,
        None,
        None,
    )
    assert rows["legacy-committed"] == (
        "COMMITTED",
        7,
        "legacy-receipt://0052/rowid-2",
        "LEGACY_0052_COMMITTED",
        None,
        None,
        "receipt://legacy/committed",
    )
    for rowid, (reservation_id, raw_receipt) in enumerate(
        legacy_receipts.items(),
        start=3,
    ):
        assert rows[reservation_id] == (
            "COMMITTED",
            0,
            f"legacy-receipt://0052/rowid-{rowid}",
            "LEGACY_0052_COMMITTED",
            None,
            None,
            raw_receipt,
        )
    assert migration_outbox == ("OPERATION_RECOVERY_REQUIRED", 5)

    migrated_store = DurableTaskStore(db_path, clock=MutableClock(30))
    for rowid, (reservation_id, raw_receipt) in enumerate(
        legacy_receipts.items(),
        start=3,
    ):
        record = migrated_store.get_operation_reservation(reservation_id)
        assert record["receipt_ref"] == f"legacy-receipt://0052/rowid-{rowid}"
        assert record["legacy_receipt_digest"] == _expected_legacy_receipt_digest(
            raw_receipt
        )
        assert record["legacy_receipt_was_null"] is (raw_receipt is None)
        assert "legacy_receipt_raw" not in record

    fixture_dir = tmp_path / "sequential-migrations"
    fixture_dir.mkdir()
    (fixture_dir / "0001_task_runtime.sql").write_text(
        TASK_RUNTIME_BASE_MIGRATION.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (fixture_dir / "0002_task_runtime_recovery.sql").write_text(
        TASK_RUNTIME_RECOVERY_MIGRATION.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    runner_db = tmp_path / "runner.sqlite3"
    runner = SQLiteMigrationRunner(db_path=runner_db, migrations_dir=fixture_dir)
    first = runner.apply_migrations(request_id="task0467-first", ts_ms=100)
    assert first.success, first.as_dict()
    with sqlite3.connect(str(runner_db)) as conn:
        conn.execute(
            """
            INSERT INTO task_runtime_idempotency_reservation(
                reservation_id, business_scope, operation_fingerprint,
                expires_ts_ms, status, status_version, receipt_ref, legacy_receipt_raw,
                recovery_reason_code, recovery_ts_ms, created_ts_ms, updated_ts_ms
            ) VALUES ('post-migration', 'scope-c', 'fingerprint-c', 30,
                      'COMMITTED', 0, 'legacy-receipt://0052/rowid-999',
                      'receipt://post/migration',
                      'LEGACY_0052_COMMITTED', 30, 30, 30)
            """
        )
        conn.commit()
    second = runner.apply_migrations(request_id="task0467-repeat", ts_ms=101)
    assert second.success, second.as_dict()
    assert second.summary["applied_count"] == 0
    assert second.summary["skipped_count"] == 2
    with sqlite3.connect(str(runner_db)) as conn:
        retained = conn.execute(
            "SELECT receipt_ref, legacy_receipt_raw FROM task_runtime_idempotency_reservation WHERE reservation_id = 'post-migration'"
        ).fetchone()
    assert retained == (
        "legacy-receipt://0052/rowid-999",
        "receipt://post/migration",
    )


def test_t_nle_06_ed25519_runtime_is_public_key_only_rotatable_and_dependency_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    key_v1 = Ed25519PrivateKey.generate()
    key_v2 = Ed25519PrivateKey.generate()
    public_keys = {
        key_id: private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        for key_id, private_key in (("reconciler-v1", key_v1), ("reconciler-v2", key_v2))
    }
    verifier = Ed25519ReconciliationProofVerifier(public_keys)
    message = b"task0467-authority-boundary"
    signature_v1 = "ed25519:" + base64.urlsafe_b64encode(
        key_v1.sign(message)
    ).decode("ascii")
    signature_v2 = "ed25519:" + base64.urlsafe_b64encode(
        key_v2.sign(message)
    ).decode("ascii")

    assert not hasattr(verifier, "sign")
    assert all(not hasattr(public_key, "sign") for public_key in verifier._public_keys.values())
    assert verifier.verify(key_id="reconciler-v1", message=message, signature=signature_v1)
    assert verifier.verify(key_id="reconciler-v2", message=message, signature=signature_v2)
    assert not verifier.verify(key_id="unknown-kid", message=message, signature=signature_v1)
    assert not verifier.verify(
        key_id="reconciler-v1",
        message=message + b"-tampered",
        signature=signature_v1,
    )
    production_members = dict(inspect.getmembers(task_store_module))
    assert "Ed25519PrivateKey" not in production_members
    assert not any(name.startswith("sign_") for name in production_members)

    monkeypatch.setattr(task_store_module, "_Ed25519PublicKey", None)
    with pytest.raises(TaskStoreError) as missing_dependency:
        Ed25519ReconciliationProofVerifier({"reconciler-v1": public_keys["reconciler-v1"]})
    assert missing_dependency.value.reason_code == TASK_STORE_RECONCILIATION_AUTHORITY_REQUIRED


def test_t_nle_06_competing_expected_version_cas_has_one_winner_and_one_outbox(tmp_path: Path) -> None:
    store = _migrated_store(tmp_path)
    _create_graph(store)

    def transition(label: str) -> str:
        contender = DurableTaskStore(store.db_path, clock=store._clock)
        try:
            contender.transition_node(
                "graph-1",
                1,
                "node-1",
                expected_version=0,
                target_status=TaskNodeStatus.LEASED,
                outbox=_event(
                    "TASK_NODE",
                    "graph-1@1/node-1",
                    1,
                    "TASK_NODE_LEASED",
                ),
            )
            return "COMMITTED"
        except TaskStoreError as exc:
            return exc.reason_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(transition, ("A", "B")))

    assert results.count("COMMITTED") == 1
    assert results.count(TASK_STORE_CAS_CONFLICT) == 1
    node = store.get_node("graph-1", 1, "node-1")
    assert node["status"] == "LEASED"
    assert node["status_version"] == 1
    assert len(store.list_pending_outbox()) == 2


def test_t_nle_06_wrong_or_duplicate_outbox_rolls_back_state_transition(tmp_path: Path) -> None:
    store = _migrated_store(tmp_path)
    create_event = _event(
        "TASK_GRAPH",
        "graph-1",
        0,
        "TASK_GRAPH_CREATED",
        idempotency_key="shared-outbox-key",
    )
    store.create_graph(_graph(), outbox=create_event)

    with pytest.raises(TaskStoreError) as wrong_version:
        store.transition_node(
            "graph-1",
            1,
            "node-1",
            expected_version=0,
            target_status="LEASED",
            outbox=_event("TASK_NODE", "graph-1@1/node-1", 99, "TASK_NODE_LEASED"),
        )
    assert wrong_version.value.reason_code == TASK_STORE_INVALID
    assert store.get_node("graph-1", 1, "node-1")["status_version"] == 0

    with pytest.raises(TaskStoreError) as duplicate:
        store.transition_node(
            "graph-1",
            1,
            "node-1",
            expected_version=0,
            target_status="LEASED",
            outbox=_event(
                "TASK_NODE",
                "graph-1@1/node-1",
                1,
                "TASK_NODE_LEASED",
                idempotency_key="shared-outbox-key",
            ),
        )
    assert duplicate.value.reason_code == TASK_STORE_OUTBOX_CONFLICT
    node = store.get_node("graph-1", 1, "node-1")
    assert node["status"] == "READY"
    assert node["status_version"] == 0
    assert len(store.list_pending_outbox()) == 1


def test_t_nle_06_outbox_metadata_is_store_timed_durable_bounded_and_atomic(
    tmp_path: Path,
) -> None:
    store = _migrated_store(tmp_path)
    canonical = build_outbox_payload_ref(
        aggregate_type="TASK_GRAPH",
        aggregate_id="graph-1",
        aggregate_version=0,
    )
    invalid_messages = (
        OutboxMessage.create(
            aggregate_type="TASK_GRAPH",
            aggregate_id="graph-1",
            aggregate_version=0,
            event_type="TASK_GRAPH_CREATED",
            payload_ref="taskstore://event/TASK_GRAPH/wrong-graph/0",
        ),
        OutboxMessage.create(
            aggregate_type="TASK_GRAPH",
            aggregate_id="graph-1",
            aggregate_version=0,
            event_type="TASK_GRAPH_CREATED",
            payload_ref=canonical,
            available_ts_ms=9_999_999_999,
        ),
        OutboxMessage.create(
            aggregate_type="TASK_GRAPH",
            aggregate_id="graph-1",
            aggregate_version=0,
            event_type="TASK_GRAPH_CREATED",
            payload_ref=canonical,
            evidence_refs=("raw secret token body",),
        ),
    )
    for message in invalid_messages:
        with pytest.raises(TaskStoreError) as invalid:
            store.create_graph(_graph(), outbox=message)
        assert invalid.value.reason_code == TASK_STORE_INVALID
        with sqlite3.connect(str(store.db_path)) as conn:
            assert conn.execute("SELECT COUNT(*) FROM task_runtime_graph").fetchone() == (0,)
            assert conn.execute("SELECT COUNT(*) FROM task_runtime_outbox").fetchone() == (0,)


def test_t_nle_06_wrong_event_rolls_back_node_attempt_lease_operation_recovery_and_budget(
    tmp_path: Path,
) -> None:
    private_key, verifier = _proof_authority()
    store = _migrated_store(tmp_path, verifier=verifier)
    _create_graph(store)

    with pytest.raises(TaskStoreError) as node_event:
        store.transition_node(
            "graph-1",
            1,
            "node-1",
            expected_version=0,
            target_status=TaskNodeStatus.LEASED,
            outbox=_event(
                "TASK_NODE",
                "graph-1@1/node-1",
                1,
                "TASK_NODE_SUCCEEDED",
            ),
        )
    assert node_event.value.reason_code == TASK_STORE_INVALID
    assert store.get_node("graph-1", 1, "node-1")["status_version"] == 0

    with pytest.raises(TaskStoreError) as attempt_event:
        store.create_attempt(
            attempt_id="attempt-1",
            graph_id="graph-1",
            graph_revision=1,
            node_id="node-1",
            attempt_no=1,
            expected_node_version=0,
            operation_scope="repo:task-0467",
            operation_fingerprint="sha256:operation-1",
            outbox=_event(
                "TASK_ATTEMPT",
                "attempt-1",
                0,
                "TASK_ATTEMPT_SUCCEEDED",
                attempt_id="attempt-1",
            ),
        )
    assert attempt_event.value.reason_code == TASK_STORE_INVALID
    with sqlite3.connect(str(store.db_path)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM task_runtime_attempt").fetchone() == (0,)
    _create_attempt(store)

    with pytest.raises(TaskStoreError) as lease_event:
        store.acquire_lease(
            "attempt-1",
            owner_id="worker-a",
            ttl_ms=20,
            expected_attempt_version=0,
            outbox=_event(
                "TASK_ATTEMPT",
                "attempt-1",
                1,
                "TASK_ATTEMPT_SUCCEEDED",
                attempt_id="attempt-1",
            ),
        )
    assert lease_event.value.reason_code == TASK_STORE_INVALID
    assert store.get_attempt("attempt-1")["status"] == "CREATED"
    with sqlite3.connect(str(store.db_path)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM task_runtime_lease").fetchone() == (0,)

    lease = store.acquire_lease(
        "attempt-1",
        owner_id="worker-a",
        ttl_ms=20,
        expected_attempt_version=0,
        outbox=_event(
            "TASK_ATTEMPT",
            "attempt-1",
            1,
            "TASK_ATTEMPT_LEASED",
            attempt_id="attempt-1",
        ),
    )
    with pytest.raises(TaskStoreError) as heartbeat_event:
        store.heartbeat_lease(
            lease["lease_id"],
            owner_id="worker-a",
            fencing_token=lease["fencing_token"],
            expected_lease_version=0,
            ttl_ms=20,
            outbox=_event(
                "TASK_LEASE",
                lease["lease_id"],
                1,
                "TASK_LEASE_RELEASED",
                attempt_id="attempt-1",
            ),
        )
    assert heartbeat_event.value.reason_code == TASK_STORE_INVALID
    with sqlite3.connect(str(store.db_path)) as conn:
        assert conn.execute(
            "SELECT status_version FROM task_runtime_lease WHERE lease_id = ?",
            (lease["lease_id"],),
        ).fetchone() == (0,)

    with pytest.raises(TaskStoreError) as reserve_event:
        store.reserve_operation(
            reservation_id="reservation-outbox",
            business_scope="repo:task-0467",
            operation_fingerprint="sha256:operation-1",
            attempt_id="attempt-1",
            lease_id=lease["lease_id"],
            owner_id="worker-a",
            fencing_token=lease["fencing_token"],
            ttl_ms=5,
            outbox=_event(
                "OPERATION_RESERVATION",
                "reservation-outbox",
                0,
                "OPERATION_COMMITTED",
                attempt_id="attempt-1",
            ),
        )
    assert reserve_event.value.reason_code == TASK_STORE_INVALID
    with sqlite3.connect(str(store.db_path)) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM task_runtime_idempotency_reservation"
        ).fetchone() == (0,)

    with pytest.raises(TaskStoreError) as reserve_attempt_binding:
        store.reserve_operation(
            reservation_id="reservation-outbox",
            business_scope="repo:task-0467",
            operation_fingerprint="sha256:operation-1",
            attempt_id="attempt-1",
            lease_id=lease["lease_id"],
            owner_id="worker-a",
            fencing_token=lease["fencing_token"],
            ttl_ms=5,
            outbox=_event(
                "OPERATION_RESERVATION",
                "reservation-outbox",
                0,
                "OPERATION_RESERVED",
                attempt_id="attempt-other",
            ),
        )
    assert reserve_attempt_binding.value.reason_code == TASK_STORE_INVALID
    with sqlite3.connect(str(store.db_path)) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM task_runtime_idempotency_reservation"
        ).fetchone() == (0,)

    store.reserve_operation(
        reservation_id="reservation-outbox",
        business_scope="repo:task-0467",
        operation_fingerprint="sha256:operation-1",
        attempt_id="attempt-1",
        lease_id=lease["lease_id"],
        owner_id="worker-a",
        fencing_token=lease["fencing_token"],
        ttl_ms=5,
        outbox=_event(
            "OPERATION_RESERVATION",
            "reservation-outbox",
            0,
            "OPERATION_RESERVED",
            attempt_id="attempt-1",
        ),
    )
    with pytest.raises(TaskStoreError) as commit_event:
        store.commit_operation(
            "reservation-outbox",
            attempt_id="attempt-1",
            lease_id=lease["lease_id"],
            owner_id="worker-a",
            fencing_token=lease["fencing_token"],
            expected_version=0,
            receipt_ref="receipt://outbox/commit",
            outbox=_event(
                "OPERATION_RESERVATION",
                "reservation-outbox",
                1,
                "OPERATION_RELEASED",
                attempt_id="attempt-1",
            ),
        )
    assert commit_event.value.reason_code == TASK_STORE_INVALID
    assert store.get_operation_reservation("reservation-outbox")["status"] == "RESERVED"

    _set_time(store, 106)
    with pytest.raises(TaskStoreError) as expired:
        store.commit_operation(
            "reservation-outbox",
            attempt_id="attempt-1",
            lease_id=lease["lease_id"],
            owner_id="worker-a",
            fencing_token=lease["fencing_token"],
            expected_version=0,
            receipt_ref="receipt://outbox/expired",
            outbox=_event(
                "OPERATION_RESERVATION",
                "reservation-outbox",
                1,
                "OPERATION_COMMITTED",
                attempt_id="attempt-1",
            ),
        )
    assert expired.value.reason_code == TASK_STORE_IDEMPOTENCY_RECOVERY_REQUIRED
    attestation_ref = "reconciliation://outbox/confirmed/1"
    receipt_ref = "receipt://outbox/confirmed"
    signature = _sign_proof(
        private_key,
        reservation_id="reservation-outbox",
        business_scope="repo:task-0467",
        operation_fingerprint="sha256:operation-1",
        recovery_version=1,
        reconciler_actor_id="governor:outbox",
        attestation_ref=attestation_ref,
        recovery_action="COMMIT",
        reconciliation_outcome="SIDE_EFFECT_CONFIRMED",
        receipt_ref=receipt_ref,
    )
    with pytest.raises(TaskStoreError) as recovery_event:
        store.recover_operation_reservation(
            "reservation-outbox",
            action="COMMIT",
            attempt_id="attempt-1",
            expected_version=1,
            reconciler_actor_id="governor:outbox",
            downstream_receipt_ref=receipt_ref,
            attestation_ref=attestation_ref,
            attestation_key_id="reconciler-v1",
            attestation_signature=signature,
            outbox=_event(
                "OPERATION_RESERVATION",
                "reservation-outbox",
                2,
                "OPERATION_RECOVERY_RELEASE",
                attempt_id="attempt-1",
            ),
        )
    assert recovery_event.value.reason_code == TASK_STORE_INVALID
    assert store.get_operation_reservation("reservation-outbox")["status"] == "RECOVERY_REQUIRED"

    budget = BudgetReservation(
        budget_id="budget-outbox",
        scope="work-order:outbox",
        limits=BudgetAmount(token_count=10),
        reserved=BudgetAmount(token_count=10),
        status=BudgetReservationStatus.ACTIVE,
        status_version=0,
    )
    with pytest.raises(TaskStoreError) as budget_create_event:
        store.create_budget_reservation(
            budget,
            outbox=_event(
                "BUDGET_RESERVATION",
                "budget-outbox",
                0,
                "BUDGET_CONSUMED",
            ),
        )
    assert budget_create_event.value.reason_code == TASK_STORE_INVALID
    with sqlite3.connect(str(store.db_path)) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM task_runtime_budget_reservation"
        ).fetchone() == (0,)
    store.create_budget_reservation(
        budget,
        outbox=_event(
            "BUDGET_RESERVATION",
            "budget-outbox",
            0,
            "BUDGET_RESERVATION_CREATED",
        ),
    )
    with pytest.raises(TaskStoreError) as budget_consume_event:
        store.consume_budget(
            "budget-outbox",
            delta=BudgetAmount(token_count=1),
            expected_version=0,
            outbox=_event(
                "BUDGET_RESERVATION",
                "budget-outbox",
                1,
                "BUDGET_EXCEEDED",
            ),
        )
    assert budget_consume_event.value.reason_code == TASK_STORE_INVALID
    assert store.reconcile_budget("budget-outbox")["status_version"] == 0


def test_t_nle_06_lease_competition_restart_recovery_and_fencing(tmp_path: Path) -> None:
    store = _migrated_store(tmp_path)
    _create_graph(store)
    _create_attempt(store)

    with pytest.raises(TaskStoreError) as competing_attempt:
        store.create_attempt(
            attempt_id="attempt-2",
            graph_id="graph-1",
            graph_revision=1,
            node_id="node-1",
            attempt_no=2,
            expected_node_version=0,
            outbox=_event(
                "TASK_ATTEMPT",
                "attempt-2",
                0,
                "TASK_ATTEMPT_CREATED",
                attempt_id="attempt-2",
            ),
        )
    assert competing_attempt.value.reason_code == TASK_STORE_CAS_CONFLICT

    lease_a = store.acquire_lease(
        "attempt-1",
        owner_id="worker-a",
        ttl_ms=10,
        expected_attempt_version=0,
        outbox=_event("TASK_ATTEMPT", "attempt-1", 1, "TASK_ATTEMPT_LEASED", attempt_id="attempt-1"),
    )
    with pytest.raises(TaskStoreError) as competition:
        store.acquire_lease(
            "attempt-1",
            owner_id="worker-b",
            ttl_ms=10,
            expected_attempt_version=1,
            outbox=_event("TASK_ATTEMPT", "attempt-1", 2, "TASK_ATTEMPT_LEASED", attempt_id="attempt-1"),
        )
    assert competition.value.reason_code == TASK_STORE_LEASE_CONFLICT

    running = store.transition_attempt(
        "attempt-1",
        owner_id="worker-a",
        fencing_token=lease_a["fencing_token"],
        expected_version=1,
        target_status=AttemptStatus.RUNNING,
        outbox=_event("TASK_ATTEMPT", "attempt-1", 2, "TASK_ATTEMPT_RUNNING", attempt_id="attempt-1"),
    )
    assert running["status"] == "RUNNING"

    _set_time(store, 111)
    restarted = DurableTaskStore(store.db_path, clock=store._clock)
    recovery = restarted.recover_startup(recovery_id="boot-2")
    assert recovery.recovered_attempt_ids == ("attempt-1",)
    assert recovery.expired_lease_ids == (lease_a["lease_id"],)
    assert restarted.get_attempt("attempt-1")["status"] == "RECOVERABLE"
    assert restarted.get_attempt("attempt-1")["status_version"] == 3

    _set_time(restarted, 112)
    lease_b = restarted.acquire_lease(
        "attempt-1",
        owner_id="worker-b",
        ttl_ms=10,
        expected_attempt_version=3,
        outbox=_event("TASK_ATTEMPT", "attempt-1", 4, "TASK_ATTEMPT_LEASED", attempt_id="attempt-1"),
    )
    assert lease_b["fencing_token"] == lease_a["fencing_token"] + 1

    with pytest.raises(TaskStoreError) as stale_heartbeat:
        restarted.heartbeat_lease(
            lease_a["lease_id"],
            owner_id="worker-a",
            fencing_token=lease_a["fencing_token"],
            expected_lease_version=1,
            ttl_ms=10,
            outbox=_event(
                "TASK_LEASE",
                lease_a["lease_id"],
                2,
                "TASK_LEASE_HEARTBEAT",
                attempt_id="attempt-1",
            ),
        )
    assert stale_heartbeat.value.reason_code == TASK_STORE_OWNERSHIP_CONFLICT

    with pytest.raises(TaskStoreError) as stale_transition:
        restarted.transition_attempt(
            "attempt-1",
            owner_id="worker-a",
            fencing_token=lease_a["fencing_token"],
            expected_version=4,
            target_status=AttemptStatus.RUNNING,
            outbox=_event(
                "TASK_ATTEMPT",
                "attempt-1",
                5,
                "TASK_ATTEMPT_RUNNING",
                attempt_id="attempt-1",
            ),
        )
    assert stale_transition.value.reason_code == TASK_STORE_OWNERSHIP_CONFLICT

    heartbeat_b = restarted.heartbeat_lease(
        lease_b["lease_id"],
        owner_id="worker-b",
        fencing_token=lease_b["fencing_token"],
        expected_lease_version=0,
        ttl_ms=20,
        outbox=_event(
            "TASK_LEASE",
            lease_b["lease_id"],
            1,
            "TASK_LEASE_HEARTBEAT",
            attempt_id="attempt-1",
        ),
    )
    assert heartbeat_b["expires_ts_ms"] == 132
    assert len(restarted.list_recoverable_attempts()) == 0


def test_t_nle_06_direct_expired_lease_takeover_uses_new_fencing_token(tmp_path: Path) -> None:
    store = _migrated_store(tmp_path)
    _create_graph(store)
    _create_attempt(store)
    first = store.acquire_lease(
        "attempt-1",
        owner_id="worker-a",
        ttl_ms=5,
        expected_attempt_version=0,
        outbox=_event(
            "TASK_ATTEMPT", "attempt-1", 1, "TASK_ATTEMPT_LEASED", attempt_id="attempt-1"
        ),
    )
    _set_time(store, 105)
    second = store.acquire_lease(
        "attempt-1",
        owner_id="worker-b",
        ttl_ms=5,
        expected_attempt_version=1,
        outbox=_event(
            "TASK_ATTEMPT", "attempt-1", 2, "TASK_ATTEMPT_LEASED", attempt_id="attempt-1"
        ),
    )
    assert second["fencing_token"] == first["fencing_token"] + 1
    assert store.get_attempt("attempt-1")["owner_id"] == "worker-b"
    assert store.get_attempt("attempt-1")["status_version"] == 2


def test_t_nle_06_trusted_clock_prevents_backdated_lease_renew_and_commit(
    tmp_path: Path,
) -> None:
    clock = MutableClock(100)
    store = _migrated_store(tmp_path, clock=clock)
    _create_graph(store)
    _create_attempt(store)
    lease = store.acquire_lease(
        "attempt-1",
        owner_id="worker-a",
        ttl_ms=5,
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
        reservation_id="reservation-clock",
        business_scope="repo:task-0467",
        operation_fingerprint="sha256:operation-1",
        attempt_id="attempt-1",
        lease_id=lease["lease_id"],
        owner_id="worker-a",
        fencing_token=lease["fencing_token"],
        ttl_ms=100,
        outbox=_event(
            "OPERATION_RESERVATION",
            "reservation-clock",
            0,
            "OPERATION_RESERVED",
            attempt_id="attempt-1",
        ),
    )
    clock.set(106)

    for method_name in (
        "acquire_lease",
        "heartbeat_lease",
        "transition_attempt",
        "release_lease",
        "recover_startup",
        "reserve_operation",
        "commit_operation",
        "renew_operation_reservation",
        "recover_operation_reservation",
    ):
        assert "now_ts_ms" not in inspect.signature(
            getattr(DurableTaskStore, method_name)
        ).parameters

    with pytest.raises(TypeError):
        store.heartbeat_lease(
            lease["lease_id"],
            owner_id="worker-a",
            fencing_token=lease["fencing_token"],
            expected_lease_version=0,
            ttl_ms=5,
            outbox=_event(
                "TASK_LEASE",
                lease["lease_id"],
                1,
                "TASK_LEASE_HEARTBEAT",
                attempt_id="attempt-1",
            ),
            now_ts_ms=100,  # type: ignore[call-arg]
        )
    with pytest.raises(TypeError):
        store.commit_operation(
            "reservation-clock",
            attempt_id="attempt-1",
            lease_id=lease["lease_id"],
            owner_id="worker-a",
            fencing_token=lease["fencing_token"],
            expected_version=0,
            receipt_ref="receipt://clock/backdated",
            outbox=_event(
                "OPERATION_RESERVATION",
                "reservation-clock",
                1,
                "OPERATION_COMMITTED",
                attempt_id="attempt-1",
            ),
            now_ts_ms=100,  # type: ignore[call-arg]
        )

    with pytest.raises(TaskStoreError) as expired_heartbeat:
        store.heartbeat_lease(
            lease["lease_id"],
            owner_id="worker-a",
            fencing_token=lease["fencing_token"],
            expected_lease_version=0,
            ttl_ms=5,
            outbox=_event(
                "TASK_LEASE",
                lease["lease_id"],
                1,
                "TASK_LEASE_HEARTBEAT",
                attempt_id="attempt-1",
            ),
        )
    assert expired_heartbeat.value.reason_code == TASK_STORE_LEASE_CONFLICT
    with pytest.raises(TaskStoreError) as expired_commit:
        store.commit_operation(
            "reservation-clock",
            attempt_id="attempt-1",
            lease_id=lease["lease_id"],
            owner_id="worker-a",
            fencing_token=lease["fencing_token"],
            expected_version=0,
            receipt_ref="receipt://clock/expired",
            outbox=_event(
                "OPERATION_RESERVATION",
                "reservation-clock",
                1,
                "OPERATION_COMMITTED",
                attempt_id="attempt-1",
            ),
        )
    assert expired_commit.value.reason_code == TASK_STORE_IDEMPOTENCY_RECOVERY_REQUIRED
    assert store.get_operation_reservation("reservation-clock")["status"] == "RECOVERY_REQUIRED"


def test_t_nle_06_repeated_business_fingerprint_has_one_reservation(tmp_path: Path) -> None:
    store = _migrated_store(tmp_path)
    fingerprint_a = build_operation_fingerprint(
        business_scope="repo:task-0467",
        operation="write-file",
        parameters={"path": "a.py", "digest": "sha256:abc"},
    )
    fingerprint_b = build_operation_fingerprint(
        business_scope="repo:task-0467",
        operation="write-file",
        parameters={"digest": "sha256:abc", "path": "a.py"},
    )
    assert fingerprint_a == fingerprint_b
    _create_graph(store)
    _create_attempt(store, operation_fingerprint=fingerprint_a)
    lease = store.acquire_lease(
        "attempt-1",
        owner_id="worker-a",
        ttl_ms=100,
        expected_attempt_version=0,
        outbox=_event(
            "TASK_ATTEMPT",
            "attempt-1",
            1,
            "TASK_ATTEMPT_LEASED",
            attempt_id="attempt-1",
        ),
    )

    def reserve(reservation_id: str):
        contender = DurableTaskStore(store.db_path, clock=store._clock)
        return contender.reserve_operation(
            reservation_id=reservation_id,
            business_scope="repo:task-0467",
            operation_fingerprint=fingerprint_a,
            attempt_id="attempt-1",
            lease_id=lease["lease_id"],
            owner_id="worker-a",
            fencing_token=lease["fencing_token"],
            ttl_ms=50,
            outbox=_event(
                "OPERATION_RESERVATION",
                reservation_id,
                0,
                "OPERATION_RESERVED",
                attempt_id="attempt-1",
            ),
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(reserve, ("reservation-1", "reservation-2")))
    assert sorted(result.acquired for result in results) == [False, True]
    winner = next(result for result in results if result.acquired)
    duplicate = next(result for result in results if not result.acquired)
    winner_id = str(winner.reservation["reservation_id"])
    assert duplicate.reservation["reservation_id"] == winner_id
    with pytest.raises(TaskStoreError) as opaque_new_receipt:
        store.commit_operation(
            winner_id,
            attempt_id="attempt-1",
            lease_id=lease["lease_id"],
            owner_id="worker-a",
            fencing_token=lease["fencing_token"],
            expected_version=0,
            receipt_ref="opaque-new-receipt",
            outbox=_event(
                "OPERATION_RESERVATION",
                winner_id,
                1,
                "OPERATION_COMMITTED",
                attempt_id="attempt-1",
            ),
        )
    assert opaque_new_receipt.value.reason_code == TASK_STORE_INVALID
    assert store.get_operation_reservation(winner_id)["status"] == "RESERVED"
    committed = store.commit_operation(
        winner_id,
        attempt_id="attempt-1",
        lease_id=lease["lease_id"],
        owner_id="worker-a",
        fencing_token=lease["fencing_token"],
        expected_version=0,
        receipt_ref="receipt://side-effect/1",
        outbox=_event(
            "OPERATION_RESERVATION",
            winner_id,
            1,
            "OPERATION_COMMITTED",
            attempt_id="attempt-1",
        ),
    )
    assert committed["status"] == "COMMITTED"
    assert committed["receipt_ref"] == "receipt://side-effect/1"
    with sqlite3.connect(str(store.db_path)) as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM task_runtime_idempotency_reservation"
        ).fetchone()[0]
        outbox_count = conn.execute(
            """
            SELECT COUNT(*) FROM task_runtime_outbox
            WHERE aggregate_type = 'OPERATION_RESERVATION'
            """
        ).fetchone()[0]
    assert count == 1
    assert outbox_count == 2


def test_t_nle_06_idempotency_recovery_reacquire_is_proof_gated_and_fenced(
    tmp_path: Path,
) -> None:
    private_key, verifier = _proof_authority()
    store = _migrated_store(tmp_path, verifier=verifier)
    _create_graph(store)
    _create_attempt(store)
    lease_a = store.acquire_lease(
        "attempt-1",
        owner_id="worker-a",
        ttl_ms=5,
        expected_attempt_version=0,
        outbox=_event(
            "TASK_ATTEMPT",
            "attempt-1",
            1,
            "TASK_ATTEMPT_LEASED",
            attempt_id="attempt-1",
        ),
    )
    reserved = store.reserve_operation(
        reservation_id="reservation-1",
        business_scope="repo:task-0467",
        operation_fingerprint="sha256:operation-1",
        attempt_id="attempt-1",
        lease_id=lease_a["lease_id"],
        owner_id="worker-a",
        fencing_token=lease_a["fencing_token"],
        ttl_ms=5,
        outbox=_event(
            "OPERATION_RESERVATION",
            "reservation-1",
            0,
            "OPERATION_RESERVED",
            attempt_id="attempt-1",
        ),
    )
    assert reserved.acquired is True
    assert reserved.reservation["expires_ts_ms"] == 105

    _set_time(store, 106)
    lease_b = store.acquire_lease(
        "attempt-1",
        owner_id="worker-b",
        ttl_ms=20,
        expected_attempt_version=1,
        outbox=_event(
            "TASK_ATTEMPT",
            "attempt-1",
            2,
            "TASK_ATTEMPT_LEASED",
            attempt_id="attempt-1",
        ),
    )
    recovery = store.get_operation_reservation("reservation-1")
    assert recovery["status"] == "RECOVERY_REQUIRED"
    assert recovery["status_version"] == 1
    assert recovery["recovery_reason_code"] == "IDEMPOTENCY_RESERVATION_EXPIRED"

    with pytest.raises(TaskStoreError) as stale_commit:
        store.commit_operation(
            "reservation-1",
            attempt_id="attempt-1",
            lease_id=lease_a["lease_id"],
            owner_id="worker-a",
            fencing_token=lease_a["fencing_token"],
            expected_version=1,
            receipt_ref="receipt://side-effect/stale",
            outbox=_event(
                "OPERATION_RESERVATION",
                "reservation-1",
                2,
                "OPERATION_COMMITTED",
                attempt_id="attempt-1",
            ),
        )
    assert stale_commit.value.reason_code == TASK_STORE_OWNERSHIP_CONFLICT

    with pytest.raises(TaskStoreError) as missing_proof:
        store.recover_operation_reservation(
            "reservation-1",
            action=IdempotencyRecoveryAction.REACQUIRE,
            attempt_id="attempt-1",
            lease_id=lease_b["lease_id"],
            owner_id="worker-b",
            fencing_token=lease_b["fencing_token"],
            expected_version=1,
            reconciler_actor_id="governor:test",
            ttl_ms=10,
            outbox=_event(
                "OPERATION_RESERVATION",
                "reservation-1",
                2,
                "OPERATION_RECOVERY_REACQUIRE",
                attempt_id="attempt-1",
            ),
        )
    assert missing_proof.value.reason_code == TASK_STORE_RECONCILIATION_PROOF_REQUIRED

    proof_ref = "reconciliation://side-effect/absent/1"
    stale_signature = _sign_proof(
        private_key,
        reservation_id="reservation-1",
        business_scope="repo:task-0467",
        operation_fingerprint="sha256:operation-1",
        recovery_version=0,
        reconciler_actor_id="governor:test",
        attestation_ref=proof_ref,
        recovery_action="REACQUIRE",
        reconciliation_outcome="NO_SIDE_EFFECT",
    )
    with pytest.raises(TaskStoreError) as stale_proof:
        store.recover_operation_reservation(
            "reservation-1",
            action="REACQUIRE",
            attempt_id="attempt-1",
            lease_id=lease_b["lease_id"],
            owner_id="worker-b",
            fencing_token=lease_b["fencing_token"],
            expected_version=1,
            reconciler_actor_id="governor:test",
            attestation_ref=proof_ref,
            attestation_key_id="reconciler-v1",
            attestation_signature=stale_signature,
            ttl_ms=10,
            outbox=_event(
                "OPERATION_RESERVATION",
                "reservation-1",
                2,
                "OPERATION_RECOVERY_REACQUIRE",
                attempt_id="attempt-1",
            ),
        )
    assert stale_proof.value.reason_code == TASK_STORE_RECONCILIATION_PROOF_INVALID

    cross_reservation_signature = _sign_proof(
        private_key,
        reservation_id="reservation-other",
        business_scope="repo:task-0467",
        operation_fingerprint="sha256:operation-1",
        recovery_version=1,
        reconciler_actor_id="governor:test",
        attestation_ref=proof_ref,
        recovery_action="REACQUIRE",
        reconciliation_outcome="NO_SIDE_EFFECT",
    )
    with pytest.raises(TaskStoreError) as cross_reservation_proof:
        store.recover_operation_reservation(
            "reservation-1",
            action="REACQUIRE",
            attempt_id="attempt-1",
            lease_id=lease_b["lease_id"],
            owner_id="worker-b",
            fencing_token=lease_b["fencing_token"],
            expected_version=1,
            reconciler_actor_id="governor:test",
            attestation_ref=proof_ref,
            attestation_key_id="reconciler-v1",
            attestation_signature=cross_reservation_signature,
            ttl_ms=10,
            outbox=_event(
                "OPERATION_RESERVATION",
                "reservation-1",
                2,
                "OPERATION_RECOVERY_REACQUIRE",
                attempt_id="attempt-1",
            ),
        )
    assert cross_reservation_proof.value.reason_code == TASK_STORE_RECONCILIATION_PROOF_INVALID

    proof_signature = _sign_proof(
        private_key,
        reservation_id="reservation-1",
        business_scope="repo:task-0467",
        operation_fingerprint="sha256:operation-1",
        recovery_version=1,
        reconciler_actor_id="governor:test",
        attestation_ref=proof_ref,
        recovery_action="REACQUIRE",
        reconciliation_outcome="NO_SIDE_EFFECT",
    )
    release_signature = _sign_proof(
        private_key,
        reservation_id="reservation-1",
        business_scope="repo:task-0467",
        operation_fingerprint="sha256:operation-1",
        recovery_version=1,
        reconciler_actor_id="governor:test",
        attestation_ref=proof_ref,
        recovery_action="RELEASE",
        reconciliation_outcome="NO_SIDE_EFFECT",
    )
    with pytest.raises(TaskStoreError) as release_as_reacquire:
        store.recover_operation_reservation(
            "reservation-1",
            action="REACQUIRE",
            attempt_id="attempt-1",
            lease_id=lease_b["lease_id"],
            owner_id="worker-b",
            fencing_token=lease_b["fencing_token"],
            expected_version=1,
            reconciler_actor_id="governor:test",
            attestation_ref=proof_ref,
            attestation_key_id="reconciler-v1",
            attestation_signature=release_signature,
            ttl_ms=10,
            outbox=_event(
                "OPERATION_RESERVATION",
                "reservation-1",
                2,
                "OPERATION_RECOVERY_REACQUIRE",
                attempt_id="attempt-1",
            ),
        )
    assert release_as_reacquire.value.reason_code == TASK_STORE_RECONCILIATION_PROOF_INVALID
    with pytest.raises(TaskStoreError) as reacquire_as_release:
        store.recover_operation_reservation(
            "reservation-1",
            action="RELEASE",
            attempt_id="attempt-1",
            expected_version=1,
            reconciler_actor_id="governor:test",
            attestation_ref=proof_ref,
            attestation_key_id="reconciler-v1",
            attestation_signature=proof_signature,
            outbox=_event(
                "OPERATION_RESERVATION",
                "reservation-1",
                2,
                "OPERATION_RECOVERY_RELEASE",
                attempt_id="attempt-1",
            ),
        )
    assert reacquire_as_release.value.reason_code == TASK_STORE_RECONCILIATION_PROOF_INVALID

    with pytest.raises(TaskStoreError) as missing_authority:
        DurableTaskStore(
            store.db_path,
            clock=store._clock,
        ).recover_operation_reservation(
            "reservation-1",
            action="REACQUIRE",
            attempt_id="attempt-1",
            lease_id=lease_b["lease_id"],
            owner_id="worker-b",
            fencing_token=lease_b["fencing_token"],
            expected_version=1,
            reconciler_actor_id="governor:test",
            attestation_ref=proof_ref,
            attestation_key_id="reconciler-v1",
            attestation_signature=proof_signature,
            ttl_ms=10,
            outbox=_event(
                "OPERATION_RESERVATION",
                "reservation-1",
                2,
                "OPERATION_RECOVERY_REACQUIRE",
                attempt_id="attempt-1",
            ),
        )
    assert missing_authority.value.reason_code == TASK_STORE_RECONCILIATION_AUTHORITY_REQUIRED

    with pytest.raises(TaskStoreError) as unknown_key:
        store.recover_operation_reservation(
            "reservation-1",
            action="REACQUIRE",
            attempt_id="attempt-1",
            lease_id=lease_b["lease_id"],
            owner_id="worker-b",
            fencing_token=lease_b["fencing_token"],
            expected_version=1,
            reconciler_actor_id="governor:test",
            attestation_ref=proof_ref,
            attestation_key_id="unknown-kid",
            attestation_signature=proof_signature,
            ttl_ms=10,
            outbox=_event(
                "OPERATION_RESERVATION",
                "reservation-1",
                2,
                "OPERATION_RECOVERY_REACQUIRE",
                attempt_id="attempt-1",
            ),
        )
    assert unknown_key.value.reason_code == TASK_STORE_RECONCILIATION_PROOF_INVALID

    tamper_index = len("ed25519:") + 7
    replacement = "A" if proof_signature[tamper_index] != "A" else "B"
    tampered_signature = (
        proof_signature[:tamper_index]
        + replacement
        + proof_signature[tamper_index + 1 :]
    )
    with pytest.raises(TaskStoreError) as tampered:
        store.recover_operation_reservation(
            "reservation-1",
            action="REACQUIRE",
            attempt_id="attempt-1",
            lease_id=lease_b["lease_id"],
            owner_id="worker-b",
            fencing_token=lease_b["fencing_token"],
            expected_version=1,
            reconciler_actor_id="governor:test",
            attestation_ref=proof_ref,
            attestation_key_id="reconciler-v1",
            attestation_signature=tampered_signature,
            ttl_ms=10,
            outbox=_event(
                "OPERATION_RESERVATION",
                "reservation-1",
                2,
                "OPERATION_RECOVERY_REACQUIRE",
                attempt_id="attempt-1",
            ),
        )
    assert tampered.value.reason_code == TASK_STORE_RECONCILIATION_PROOF_INVALID

    reacquired = store.recover_operation_reservation(
        "reservation-1",
        action="REACQUIRE",
        attempt_id="attempt-1",
        lease_id=lease_b["lease_id"],
        owner_id="worker-b",
        fencing_token=lease_b["fencing_token"],
        expected_version=1,
        reconciler_actor_id="governor:test",
        attestation_ref=proof_ref,
        attestation_key_id="reconciler-v1",
        attestation_signature=proof_signature,
        ttl_ms=10,
        outbox=_event(
            "OPERATION_RESERVATION",
            "reservation-1",
            2,
            "OPERATION_RECOVERY_REACQUIRE",
            attempt_id="attempt-1",
        ),
    )
    assert reacquired["status"] == "RESERVED"
    assert reacquired["fencing_token"] == lease_b["fencing_token"]
    assert reacquired["reconciliation_proof_digest"].startswith("sha256:")
    assert reacquired["reconciliation_proof_key_id"] == "reconciler-v1"
    assert reacquired["reconciliation_proof_signature"] == proof_signature
    assert reacquired["reconciler_actor_id"] == "governor:test"
    assert reacquired["reconciliation_recovery_version"] == 1

    with pytest.raises(TaskStoreError) as fenced_after_reacquire:
        store.commit_operation(
            "reservation-1",
            attempt_id="attempt-1",
            lease_id=lease_a["lease_id"],
            owner_id="worker-a",
            fencing_token=lease_a["fencing_token"],
            expected_version=2,
            receipt_ref="receipt://side-effect/stale-2",
            outbox=_event(
                "OPERATION_RESERVATION",
                "reservation-1",
                3,
                "OPERATION_COMMITTED",
                attempt_id="attempt-1",
            ),
        )
    assert fenced_after_reacquire.value.reason_code == TASK_STORE_OWNERSHIP_CONFLICT

    committed = store.commit_operation(
        "reservation-1",
        attempt_id="attempt-1",
        lease_id=lease_b["lease_id"],
        owner_id="worker-b",
        fencing_token=lease_b["fencing_token"],
        expected_version=2,
        receipt_ref="receipt://side-effect/verified-1",
        outbox=_event(
            "OPERATION_RESERVATION",
            "reservation-1",
            3,
            "OPERATION_COMMITTED",
            attempt_id="attempt-1",
        ),
    )
    assert committed["status"] == "COMMITTED"
    assert committed["receipt_ref"] == "receipt://side-effect/verified-1"
    assert store.list_recovery_required_operations() == []


def test_t_nle_06_expired_reservation_commit_persists_recovery_required_first(
    tmp_path: Path,
) -> None:
    store = _migrated_store(tmp_path)
    _create_graph(store)
    _create_attempt(store)
    lease = store.acquire_lease(
        "attempt-1",
        owner_id="worker-a",
        ttl_ms=20,
        expected_attempt_version=0,
        outbox=_event(
            "TASK_ATTEMPT", "attempt-1", 1, "TASK_ATTEMPT_LEASED", attempt_id="attempt-1"
        ),
    )
    store.reserve_operation(
        reservation_id="reservation-expired",
        business_scope="repo:task-0467",
        operation_fingerprint="sha256:operation-1",
        attempt_id="attempt-1",
        lease_id=lease["lease_id"],
        owner_id="worker-a",
        fencing_token=lease["fencing_token"],
        ttl_ms=5,
        outbox=_event(
            "OPERATION_RESERVATION",
            "reservation-expired",
            0,
            "OPERATION_RESERVED",
            attempt_id="attempt-1",
        ),
    )

    _set_time(store, 106)
    with pytest.raises(TaskStoreError) as expired_commit:
        store.commit_operation(
            "reservation-expired",
            attempt_id="attempt-1",
            lease_id=lease["lease_id"],
            owner_id="worker-a",
            fencing_token=lease["fencing_token"],
            expected_version=0,
            receipt_ref="receipt://side-effect/unverified",
            outbox=_event(
                "OPERATION_RESERVATION",
                "reservation-expired",
                1,
                "OPERATION_COMMITTED",
                attempt_id="attempt-1",
            ),
        )
    assert expired_commit.value.reason_code == TASK_STORE_IDEMPOTENCY_RECOVERY_REQUIRED
    recovery = store.get_operation_reservation("reservation-expired")
    assert recovery["status"] == "RECOVERY_REQUIRED"
    assert recovery["status_version"] == 1
    assert recovery["receipt_ref"] is None


def test_t_nle_06_recovery_can_commit_only_with_explicit_downstream_receipt(
    tmp_path: Path,
) -> None:
    private_key, verifier = _proof_authority()
    store = _migrated_store(tmp_path, verifier=verifier)
    _create_graph(store)
    _create_attempt(store)
    lease_a = store.acquire_lease(
        "attempt-1",
        owner_id="worker-a",
        ttl_ms=5,
        expected_attempt_version=0,
        outbox=_event(
            "TASK_ATTEMPT", "attempt-1", 1, "TASK_ATTEMPT_LEASED", attempt_id="attempt-1"
        ),
    )
    store.reserve_operation(
        reservation_id="reservation-receipt",
        business_scope="repo:task-0467",
        operation_fingerprint="sha256:operation-1",
        attempt_id="attempt-1",
        lease_id=lease_a["lease_id"],
        owner_id="worker-a",
        fencing_token=lease_a["fencing_token"],
        ttl_ms=5,
        outbox=_event(
            "OPERATION_RESERVATION",
            "reservation-receipt",
            0,
            "OPERATION_RESERVED",
            attempt_id="attempt-1",
        ),
    )
    _set_time(store, 106)
    lease_b = store.acquire_lease(
        "attempt-1",
        owner_id="worker-b",
        ttl_ms=20,
        expected_attempt_version=1,
        outbox=_event(
            "TASK_ATTEMPT", "attempt-1", 2, "TASK_ATTEMPT_LEASED", attempt_id="attempt-1"
        ),
    )

    with pytest.raises(TaskStoreError) as missing_receipt:
        store.recover_operation_reservation(
            "reservation-receipt",
            action="COMMIT",
            attempt_id="attempt-1",
            lease_id=lease_b["lease_id"],
            owner_id="worker-b",
            fencing_token=lease_b["fencing_token"],
            expected_version=1,
            reconciler_actor_id="governor:test",
            outbox=_event(
                "OPERATION_RESERVATION",
                "reservation-receipt",
                2,
                "OPERATION_RECOVERY_COMMIT",
                attempt_id="attempt-1",
            ),
        )
    assert missing_receipt.value.reason_code == TASK_STORE_INVALID

    attestation_ref = "reconciliation://side-effect/confirmed/receipt-1"
    receipt_ref = "receipt://downstream/verified-1"
    signature = _sign_proof(
        private_key,
        reservation_id="reservation-receipt",
        business_scope="repo:task-0467",
        operation_fingerprint="sha256:operation-1",
        recovery_version=1,
        reconciler_actor_id="governor:test",
        attestation_ref=attestation_ref,
        recovery_action="COMMIT",
        reconciliation_outcome="SIDE_EFFECT_CONFIRMED",
        receipt_ref=receipt_ref,
    )
    with pytest.raises(TaskStoreError) as unsigned_outcome:
        store.recover_operation_reservation(
            "reservation-receipt",
            action="COMMIT",
            attempt_id="attempt-1",
            expected_version=1,
            reconciler_actor_id="governor:test",
            downstream_receipt_ref=receipt_ref,
            outbox=_event(
                "OPERATION_RESERVATION",
                "reservation-receipt",
                2,
                "OPERATION_RECOVERY_COMMIT",
                attempt_id="attempt-1",
            ),
        )
    assert unsigned_outcome.value.reason_code == TASK_STORE_RECONCILIATION_PROOF_REQUIRED
    with pytest.raises(TaskStoreError) as fake_receipt:
        store.recover_operation_reservation(
            "reservation-receipt",
            action="COMMIT",
            attempt_id="attempt-1",
            expected_version=1,
            reconciler_actor_id="governor:test",
            downstream_receipt_ref="receipt://downstream/fake",
            attestation_ref=attestation_ref,
            attestation_key_id="reconciler-v1",
            attestation_signature=signature,
            outbox=_event(
                "OPERATION_RESERVATION",
                "reservation-receipt",
                2,
                "OPERATION_RECOVERY_COMMIT",
                attempt_id="attempt-1",
            ),
        )
    assert fake_receipt.value.reason_code == TASK_STORE_RECONCILIATION_PROOF_INVALID

    _set_time(store, 200)
    committed = store.recover_operation_reservation(
        "reservation-receipt",
        action="COMMIT",
        attempt_id="attempt-1",
        expected_version=1,
        reconciler_actor_id="governor:test",
        downstream_receipt_ref=receipt_ref,
        attestation_ref=attestation_ref,
        attestation_key_id="reconciler-v1",
        attestation_signature=signature,
        outbox=_event(
            "OPERATION_RESERVATION",
            "reservation-receipt",
            2,
            "OPERATION_RECOVERY_COMMIT",
            attempt_id="attempt-1",
        ),
    )
    assert committed["status"] == "COMMITTED"
    assert committed["receipt_ref"] == receipt_ref
    assert committed["reconciliation_outcome"] == "SIDE_EFFECT_CONFIRMED"
    assert committed["reconciler_actor_id"] == "governor:test"
    assert committed["reconciliation_recovery_version"] == 1


@pytest.mark.parametrize(
    ("action", "valid_status", "invalid_statuses"),
    (
        (
            "COMMIT",
            "COMMITTED",
            ("RESERVED", "RECOVERY_REQUIRED", "RELEASED", "CANCELLED"),
        ),
        (
            "RELEASE",
            "RELEASED",
            ("RESERVED", "RECOVERY_REQUIRED", "COMMITTED", "CANCELLED"),
        ),
        (
            "REACQUIRE",
            "RESERVED",
            ("RELEASED", "CANCELLED"),
        ),
    ),
)
def test_t_nle_06_durable_reconciliation_action_status_matrix_rejects_tamper(
    tmp_path: Path,
    action: str,
    valid_status: str,
    invalid_statuses: tuple[str, ...],
) -> None:
    private_key, verifier = _proof_authority()
    store = _migrated_store(
        tmp_path,
        name=f"action-status-{action.lower()}.sqlite3",
        verifier=verifier,
    )
    _create_graph(store)
    _create_attempt(store)
    lease = store.acquire_lease(
        "attempt-1",
        owner_id="worker-a",
        ttl_ms=20,
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
        reservation_id="reservation-matrix",
        business_scope="repo:task-0467",
        operation_fingerprint="sha256:operation-1",
        attempt_id="attempt-1",
        lease_id=lease["lease_id"],
        owner_id="worker-a",
        fencing_token=lease["fencing_token"],
        ttl_ms=5,
        outbox=_event(
            "OPERATION_RESERVATION",
            "reservation-matrix",
            0,
            "OPERATION_RESERVED",
            attempt_id="attempt-1",
        ),
    )
    _set_time(store, 106)
    with pytest.raises(TaskStoreError) as expired:
        store.commit_operation(
            "reservation-matrix",
            attempt_id="attempt-1",
            lease_id=lease["lease_id"],
            owner_id="worker-a",
            fencing_token=lease["fencing_token"],
            expected_version=0,
            receipt_ref="receipt://matrix/untrusted",
            outbox=_event(
                "OPERATION_RESERVATION",
                "reservation-matrix",
                1,
                "OPERATION_COMMITTED",
                attempt_id="attempt-1",
            ),
        )
    assert expired.value.reason_code == TASK_STORE_IDEMPOTENCY_RECOVERY_REQUIRED

    receipt_ref = "receipt://matrix/confirmed" if action == "COMMIT" else None
    outcome = "SIDE_EFFECT_CONFIRMED" if action == "COMMIT" else "NO_SIDE_EFFECT"
    attestation_ref = f"reconciliation://matrix/{action.lower()}/1"
    signature = _sign_proof(
        private_key,
        reservation_id="reservation-matrix",
        business_scope="repo:task-0467",
        operation_fingerprint="sha256:operation-1",
        recovery_version=1,
        reconciler_actor_id="governor:matrix",
        attestation_ref=attestation_ref,
        recovery_action=action,
        reconciliation_outcome=outcome,
        receipt_ref=receipt_ref,
    )
    kwargs: dict[str, Any] = {}
    if action == "REACQUIRE":
        kwargs.update(
            lease_id=lease["lease_id"],
            owner_id="worker-a",
            fencing_token=lease["fencing_token"],
            ttl_ms=10,
        )
    recovered = store.recover_operation_reservation(
        "reservation-matrix",
        action=action,
        attempt_id="attempt-1",
        expected_version=1,
        reconciler_actor_id="governor:matrix",
        downstream_receipt_ref=receipt_ref,
        attestation_ref=attestation_ref,
        attestation_key_id="reconciler-v1",
        attestation_signature=signature,
        outbox=_event(
            "OPERATION_RESERVATION",
            "reservation-matrix",
            2,
            f"OPERATION_RECOVERY_{action}",
            attempt_id="attempt-1",
        ),
        **kwargs,
    )
    assert recovered["status"] == valid_status
    original_receipt = recovered["receipt_ref"]

    for invalid_status in invalid_statuses:
        tampered_receipt = (
            f"receipt://matrix/tampered-{action.lower()}"
            if invalid_status == "COMMITTED"
            else None
        )
        with sqlite3.connect(str(store.db_path)) as conn:
            conn.execute(
                """
                UPDATE task_runtime_idempotency_reservation
                SET status = ?, receipt_ref = ?
                WHERE reservation_id = 'reservation-matrix'
                """,
                (invalid_status, tampered_receipt),
            )
        with pytest.raises(TaskStoreError) as tampered:
            store.get_operation_reservation("reservation-matrix")
        assert tampered.value.reason_code == TASK_STORE_DURABLE_DATA_INVALID
        with sqlite3.connect(str(store.db_path)) as conn:
            conn.execute(
                """
                UPDATE task_runtime_idempotency_reservation
                SET status = ?, receipt_ref = ?
                WHERE reservation_id = 'reservation-matrix'
                """,
                (valid_status, original_receipt),
            )
        assert store.get_operation_reservation("reservation-matrix")["status"] == valid_status


def test_t_nle_06_budget_consumption_and_denied_overage_are_reconcilable(tmp_path: Path) -> None:
    store = _migrated_store(tmp_path)
    reservation = BudgetReservation(
        budget_id="budget-1",
        scope="work-order:wo-1",
        limits=BudgetAmount(token_count=10, tool_call_count=2),
        reserved=BudgetAmount(token_count=10, tool_call_count=2),
        status=BudgetReservationStatus.ACTIVE,
        status_version=0,
    )
    store.create_budget_reservation(
        reservation,
        outbox=_event("BUDGET_RESERVATION", "budget-1", 0, "BUDGET_RESERVATION_CREATED"),
    )
    accepted = store.consume_budget(
        "budget-1",
        delta=BudgetAmount(token_count=4, tool_call_count=1),
        expected_version=0,
        outbox=_event("BUDGET_RESERVATION", "budget-1", 1, "BUDGET_CONSUMED"),
    )
    assert accepted.accepted is True
    assert accepted.reservation["consumed"]["token_count"] == 4

    with pytest.raises(ValueError):
        store.consume_budget(
            "budget-1",
            delta=BudgetAmount(token_count=-1),
            expected_version=1,
            outbox=_event("BUDGET_RESERVATION", "budget-1", 2, "BUDGET_CONSUMED"),
        )
    assert store.reconcile_budget("budget-1")["status_version"] == 1

    exceeded = store.consume_budget(
        "budget-1",
        delta=BudgetAmount(token_count=7),
        expected_version=1,
        outbox=_event("BUDGET_RESERVATION", "budget-1", 2, "BUDGET_EXCEEDED"),
    )
    assert exceeded.accepted is False
    assert exceeded.reason_code == TASK_STORE_BUDGET_EXCEEDED
    assert exceeded.exceeded_dimensions == ("token_count",)
    reconciliation = store.reconcile_budget("budget-1")
    assert reconciliation["status"] == "EXCEEDED"
    assert reconciliation["consumed"]["token_count"] == 4
    assert reconciliation["attempted_consumed"]["token_count"] == 11
    assert reconciliation["balanced"] is True
    assert reconciliation["denied_usage_preserved"] is True

    released = store.transition_budget(
        "budget-1",
        expected_version=2,
        target_status=BudgetReservationStatus.RELEASED,
        outbox=_event("BUDGET_RESERVATION", "budget-1", 3, "BUDGET_RESERVATION_RELEASED"),
    )
    assert released["status"] == "RELEASED"


def test_t_nle_06_cancellation_is_cas_guarded_and_emits_matching_outbox(tmp_path: Path) -> None:
    store = _migrated_store(tmp_path)
    _create_graph(store)
    with pytest.raises(TaskStoreError) as generic_cancel:
        store.transition_node(
            "graph-1",
            1,
            "node-1",
            expected_version=0,
            target_status=TaskNodeStatus.CANCEL_REQUESTED,
            outbox=_event(
                "TASK_NODE",
                "graph-1@1/node-1",
                1,
                "TASK_NODE_CANCEL_REQUESTED",
            ),
        )
    assert generic_cancel.value.reason_code == TASK_STORE_INVALID
    assert store.get_node("graph-1", 1, "node-1")["status"] == "READY"
    _set_time(store, 200)
    cancelled = store.request_cancel_node(
        "graph-1",
        1,
        "node-1",
        expected_version=0,
        outbox=_event("TASK_NODE", "graph-1@1/node-1", 1, "TASK_NODE_CANCEL_REQUESTED"),
    )
    assert cancelled["status"] == "CANCEL_REQUESTED"
    assert cancelled["cancellation_requested_ts_ms"] == 200
    pending = store.list_pending_outbox()
    assert pending[-1]["aggregate_id"] == "graph-1@1/node-1"
    assert pending[-1]["aggregate_version"] == cancelled["status_version"]


@pytest.mark.parametrize(
    ("action", "outcome", "receipt_ref"),
    (
        ("COMMIT", "SIDE_EFFECT_CONFIRMED", "receipt://cancelled/confirmed"),
        ("RELEASE", "NO_SIDE_EFFECT", None),
    ),
)
def test_t_nle_06_cancel_fences_workers_waits_for_signed_reconciliation_then_converges(
    tmp_path: Path,
    action: str,
    outcome: str,
    receipt_ref: str | None,
) -> None:
    private_key, verifier = _proof_authority()
    store = _migrated_store(
        tmp_path,
        name=f"cancel-{action.lower()}.sqlite3",
        verifier=verifier,
    )
    _create_graph(store)
    _create_attempt(store)
    lease = store.acquire_lease(
        "attempt-1",
        owner_id="worker-a",
        ttl_ms=100,
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
        reservation_id="reservation-cancel",
        business_scope="repo:task-0467",
        operation_fingerprint="sha256:operation-1",
        attempt_id="attempt-1",
        lease_id=lease["lease_id"],
        owner_id="worker-a",
        fencing_token=lease["fencing_token"],
        ttl_ms=100,
        outbox=_event(
            "OPERATION_RESERVATION",
            "reservation-cancel",
            0,
            "OPERATION_RESERVED",
            attempt_id="attempt-1",
        ),
    )
    requested = store.request_cancel_node(
        "graph-1",
        1,
        "node-1",
        expected_version=1,
        outbox=_event(
            "TASK_NODE",
            "graph-1@1/node-1",
            2,
            "TASK_NODE_CANCEL_REQUESTED",
        ),
    )
    assert requested["status"] == "CANCEL_REQUESTED"
    assert store.get_attempt("attempt-1")["status"] == "CANCEL_REQUESTED"
    reservation = store.get_operation_reservation("reservation-cancel")
    assert reservation["status"] == "RECOVERY_REQUIRED"
    assert reservation["recovery_reason_code"] == "NODE_CANCEL_REQUESTED"
    with sqlite3.connect(str(store.db_path)) as conn:
        lease_status = conn.execute(
            "SELECT status FROM task_runtime_lease WHERE lease_id = ?",
            (lease["lease_id"],),
        ).fetchone()
    assert lease_status == ("CANCELLED",)

    with pytest.raises(TaskStoreError) as heartbeat_after_cancel:
        store.heartbeat_lease(
            lease["lease_id"],
            owner_id="worker-a",
            fencing_token=lease["fencing_token"],
            expected_lease_version=1,
            ttl_ms=100,
            outbox=_event(
                "TASK_LEASE",
                lease["lease_id"],
                2,
                "TASK_LEASE_HEARTBEAT",
                attempt_id="attempt-1",
            ),
        )
    assert heartbeat_after_cancel.value.reason_code == TASK_STORE_CANCELLATION_CONFLICT
    with pytest.raises(TaskStoreError) as success_after_cancel:
        store.transition_attempt(
            "attempt-1",
            owner_id="worker-a",
            fencing_token=lease["fencing_token"],
            expected_version=2,
            target_status=AttemptStatus.SUCCEEDED,
            outbox=_event(
                "TASK_ATTEMPT",
                "attempt-1",
                3,
                "TASK_ATTEMPT_SUCCEEDED",
                attempt_id="attempt-1",
            ),
        )
    assert success_after_cancel.value.reason_code == TASK_STORE_CANCELLATION_CONFLICT
    with pytest.raises(TaskStoreError) as commit_after_cancel:
        store.commit_operation(
            "reservation-cancel",
            attempt_id="attempt-1",
            lease_id=lease["lease_id"],
            owner_id="worker-a",
            fencing_token=lease["fencing_token"],
            expected_version=1,
            receipt_ref="receipt://cancelled/worker-forged",
            outbox=_event(
                "OPERATION_RESERVATION",
                "reservation-cancel",
                2,
                "OPERATION_COMMITTED",
                attempt_id="attempt-1",
            ),
        )
    assert commit_after_cancel.value.reason_code == TASK_STORE_CANCELLATION_CONFLICT

    waiting = store.recover_startup(recovery_id=f"cancel-wait-{action.lower()}")
    assert waiting.cancelled_attempt_ids == ("attempt-1",)
    assert waiting.cancelled_node_ids == ()
    assert waiting.waiting_reconciliation_node_ids == ("graph-1@1/node-1",)
    assert store.get_node("graph-1", 1, "node-1")["status"] == "CANCEL_REQUESTED"

    attestation_ref = f"reconciliation://cancelled/{action.lower()}/1"
    signature = _sign_proof(
        private_key,
        reservation_id="reservation-cancel",
        business_scope="repo:task-0467",
        operation_fingerprint="sha256:operation-1",
        recovery_version=1,
        reconciler_actor_id="governor:cancel",
        attestation_ref=attestation_ref,
        recovery_action=action,
        reconciliation_outcome=outcome,
        receipt_ref=receipt_ref,
    )
    terminal = store.recover_operation_reservation(
        "reservation-cancel",
        action=action,
        attempt_id="attempt-1",
        expected_version=1,
        reconciler_actor_id="governor:cancel",
        downstream_receipt_ref=receipt_ref,
        attestation_ref=attestation_ref,
        attestation_key_id="reconciler-v1",
        attestation_signature=signature,
        outbox=_event(
            "OPERATION_RESERVATION",
            "reservation-cancel",
            2,
            f"OPERATION_RECOVERY_{action}",
            attempt_id="attempt-1",
        ),
    )
    assert terminal["status"] == ("COMMITTED" if action == "COMMIT" else "RELEASED")
    assert terminal["reconciliation_outcome"] == outcome

    converged = store.recover_startup(recovery_id=f"cancel-finish-{action.lower()}")
    assert converged.cancelled_node_ids == ("graph-1@1/node-1",)
    assert converged.waiting_reconciliation_node_ids == ()
    assert store.get_node("graph-1", 1, "node-1")["status"] == "CANCELLED"


def test_t_nle_06_waiting_approval_uses_frozen_direct_cancel_transition(tmp_path: Path) -> None:
    store = _migrated_store(tmp_path)
    store.create_graph(
        _graph(node_status=TaskNodeStatus.WAITING_APPROVAL),
        outbox=_event("TASK_GRAPH", "graph-1", 0, "TASK_GRAPH_CREATED"),
    )
    cancelled = store.request_cancel_node(
        "graph-1",
        1,
        "node-1",
        expected_version=0,
        outbox=_event(
            "TASK_NODE",
            "graph-1@1/node-1",
            1,
            "TASK_NODE_CANCELLED",
        ),
    )
    assert cancelled["status"] == "CANCELLED"
    assert cancelled["cancellation_requested_ts_ms"] == 100


@pytest.mark.parametrize("terminal_status", (AttemptStatus.SUCCEEDED, AttemptStatus.FAILED))
def test_t_nle_06_direct_cancel_closes_orphan_active_lease_for_terminal_attempt(
    tmp_path: Path,
    terminal_status: AttemptStatus,
) -> None:
    store = _migrated_store(
        tmp_path,
        name=f"terminal-direct-cancel-{terminal_status.value.lower()}.sqlite3",
    )
    _create_graph(store)
    _create_attempt(store)
    lease = store.acquire_lease(
        "attempt-1",
        owner_id="worker-a",
        ttl_ms=100,
        expected_attempt_version=0,
        outbox=_event(
            "TASK_ATTEMPT",
            "attempt-1",
            1,
            "TASK_ATTEMPT_LEASED",
            attempt_id="attempt-1",
        ),
    )
    _advance_node_to_waiting_acceptance(store)
    store.transition_attempt(
        "attempt-1",
        owner_id="worker-a",
        fencing_token=lease["fencing_token"],
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
        fencing_token=lease["fencing_token"],
        expected_version=2,
        target_status=terminal_status,
        outbox=_event(
            "TASK_ATTEMPT",
            "attempt-1",
            3,
            f"TASK_ATTEMPT_{terminal_status.value}",
            attempt_id="attempt-1",
        ),
    )
    with sqlite3.connect(str(store.db_path)) as conn:
        assert conn.execute(
            "SELECT status FROM task_runtime_lease WHERE lease_id = ?",
            (lease["lease_id"],),
        ).fetchone() == ("RELEASED",)
        conn.execute(
            """
            UPDATE task_runtime_lease
            SET status = 'ACTIVE', released_ts_ms = NULL
            WHERE lease_id = ?
            """,
            (lease["lease_id"],),
        )

    cancelled = store.request_cancel_node(
        "graph-1",
        1,
        "node-1",
        expected_version=6,
        outbox=_event(
            "TASK_NODE",
            "graph-1@1/node-1",
            7,
            "TASK_NODE_CANCELLED",
        ),
    )
    assert cancelled["status"] == "CANCELLED"
    with sqlite3.connect(str(store.db_path)) as conn:
        assert conn.execute(
            "SELECT status FROM task_runtime_lease WHERE lease_id = ?",
            (lease["lease_id"],),
        ).fetchone() == ("CANCELLED",)
        assert conn.execute(
            "SELECT COUNT(*) FROM task_runtime_lease WHERE status = 'ACTIVE'"
        ).fetchone() == (0,)


def test_t_nle_06_startup_cleans_terminal_attempt_orphan_lease_and_unresolved_blocks_direct_cancel(
    tmp_path: Path,
) -> None:
    store = _migrated_store(tmp_path, name="terminal-startup-cleanup.sqlite3")
    _create_graph(store)
    _create_attempt(store)
    lease = store.acquire_lease(
        "attempt-1",
        owner_id="worker-a",
        ttl_ms=100,
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
        reservation_id="reservation-terminal",
        business_scope="repo:task-0467",
        operation_fingerprint="sha256:operation-1",
        attempt_id="attempt-1",
        lease_id=lease["lease_id"],
        owner_id="worker-a",
        fencing_token=lease["fencing_token"],
        ttl_ms=100,
        outbox=_event(
            "OPERATION_RESERVATION",
            "reservation-terminal",
            0,
            "OPERATION_RESERVED",
            attempt_id="attempt-1",
        ),
    )
    _advance_node_to_waiting_acceptance(store)
    store.transition_attempt(
        "attempt-1",
        owner_id="worker-a",
        fencing_token=lease["fencing_token"],
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
        fencing_token=lease["fencing_token"],
        expected_version=2,
        target_status=AttemptStatus.SUCCEEDED,
        outbox=_event(
            "TASK_ATTEMPT",
            "attempt-1",
            3,
            "TASK_ATTEMPT_SUCCEEDED",
            attempt_id="attempt-1",
        ),
    )
    with sqlite3.connect(str(store.db_path)) as conn:
        conn.execute(
            """
            UPDATE task_runtime_lease
            SET status = 'ACTIVE', released_ts_ms = NULL
            WHERE lease_id = ?
            """,
            (lease["lease_id"],),
        )
        conn.execute(
            """
            UPDATE task_runtime_idempotency_reservation
            SET status = 'RECOVERY_REQUIRED', status_version = 1,
                recovery_reason_code = 'TERMINAL_SIDE_EFFECT_UNRESOLVED',
                recovery_ts_ms = 100
            WHERE reservation_id = 'reservation-terminal'
            """
        )

    with pytest.raises(TaskStoreError) as unresolved_cancel:
        store.request_cancel_node(
            "graph-1",
            1,
            "node-1",
            expected_version=6,
            outbox=_event(
                "TASK_NODE",
                "graph-1@1/node-1",
                7,
                "TASK_NODE_CANCELLED",
            ),
        )
    assert unresolved_cancel.value.reason_code == TASK_STORE_CANCELLATION_CONFLICT
    assert store.get_node("graph-1", 1, "node-1")["status"] == "WAITING_ACCEPTANCE_REVIEW"
    with sqlite3.connect(str(store.db_path)) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM task_runtime_lease WHERE status = 'ACTIVE'"
        ).fetchone() == (0,)
        conn.execute(
            """
            UPDATE task_runtime_lease
            SET status = 'ACTIVE', released_ts_ms = NULL
            WHERE lease_id = ?
            """,
            (lease["lease_id"],),
        )

    report = store.recover_startup(recovery_id="terminal-orphan-cleanup")
    assert report.released_lease_ids == (lease["lease_id"],)
    assert store.get_operation_reservation("reservation-terminal")["status"] == "RECOVERY_REQUIRED"
    assert store.get_node("graph-1", 1, "node-1")["status"] == "WAITING_ACCEPTANCE_REVIEW"
    with sqlite3.connect(str(store.db_path)) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM task_runtime_lease WHERE status = 'ACTIVE'"
        ).fetchone() == (0,)


def test_t_nle_06_startup_recovery_cancels_created_and_expired_cancel_requested(
    tmp_path: Path,
) -> None:
    created_store = _migrated_store(tmp_path, name="created-cancel.sqlite3")
    _create_graph(created_store)
    _create_attempt(created_store)
    first = created_store.recover_startup(recovery_id="boot-created")
    assert first.cancelled_attempt_ids == ("attempt-1",)
    assert first.recovered_attempt_ids == ()
    assert created_store.get_attempt("attempt-1")["status"] == "CANCELLED"
    outbox_before_repeat = len(created_store.list_pending_outbox())
    repeated = created_store.recover_startup(recovery_id="boot-created")
    assert repeated.cancelled_attempt_ids == ()
    assert len(created_store.list_pending_outbox()) == outbox_before_repeat

    requested_store = _migrated_store(tmp_path, name="requested-cancel.sqlite3")
    _create_graph(requested_store)
    _create_attempt(requested_store)
    lease = requested_store.acquire_lease(
        "attempt-1",
        owner_id="worker-a",
        ttl_ms=5,
        expected_attempt_version=0,
        outbox=_event(
            "TASK_ATTEMPT", "attempt-1", 1, "TASK_ATTEMPT_LEASED", attempt_id="attempt-1"
        ),
    )
    requested_store.transition_attempt(
        "attempt-1",
        owner_id="worker-a",
        fencing_token=lease["fencing_token"],
        expected_version=1,
        target_status=AttemptStatus.CANCEL_REQUESTED,
        outbox=_event(
            "TASK_ATTEMPT",
            "attempt-1",
            2,
            "TASK_ATTEMPT_CANCEL_REQUESTED",
            attempt_id="attempt-1",
        ),
    )
    _set_time(requested_store, 105)
    report = requested_store.recover_startup(
        recovery_id="boot-cancel-requested",
    )
    assert report.expired_lease_ids == ()
    assert report.cancelled_lease_ids == (lease["lease_id"],)
    assert report.cancelled_attempt_ids == ("attempt-1",)
    cancelled = requested_store.get_attempt("attempt-1")
    assert cancelled["status"] == "CANCELLED"
    assert cancelled["completed_ts_ms"] == 105
    event_types = {
        event["event_type"]
        for event in requested_store.list_pending_outbox()
    }
    assert "TASK_LEASE_CANCELLED_RECOVERY" in event_types
    assert "TASK_ATTEMPT_CANCELLED_RECOVERY" in event_types


def test_t_nle_06_durable_json_and_contract_corruption_fail_closed_without_overwrite(
    tmp_path: Path,
) -> None:
    store = _migrated_store(tmp_path)
    _create_graph(store)
    baseline_outbox = len(store.list_pending_outbox())
    with sqlite3.connect(str(store.db_path)) as conn:
        conn.execute(
            "UPDATE task_runtime_graph SET graph_json = ? WHERE graph_id = 'graph-1'",
            ("{broken-json",),
        )
    with pytest.raises(TaskStoreError) as graph_read:
        store.get_graph("graph-1", 1)
    assert graph_read.value.reason_code == TASK_STORE_DURABLE_DATA_INVALID
    with pytest.raises(TaskStoreError) as graph_transition:
        store.transition_graph(
            "graph-1",
            1,
            expected_version=0,
            target_status="ACTIVE",
            outbox=_event("TASK_GRAPH", "graph-1", 1, "TASK_GRAPH_ACTIVE"),
        )
    assert graph_transition.value.reason_code == TASK_STORE_DURABLE_DATA_INVALID
    with sqlite3.connect(str(store.db_path)) as conn:
        graph_row = conn.execute(
            "SELECT status, status_version, graph_json FROM task_runtime_graph WHERE graph_id = 'graph-1'"
        ).fetchone()
    assert graph_row == ("CREATED", 0, "{broken-json")
    assert len(store.list_pending_outbox()) == baseline_outbox

    valid_graph_json = json.dumps(
        _graph().to_dict(),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    duplicate_top_level = valid_graph_json[:-1] + ',"graph_id":"graph-1"}'
    with sqlite3.connect(str(store.db_path)) as conn:
        conn.execute(
            "UPDATE task_runtime_graph SET graph_json = ? WHERE graph_id = 'graph-1'",
            (duplicate_top_level,),
        )
    with pytest.raises(TaskStoreError) as duplicate_graph_key:
        store.get_graph("graph-1", 1)
    assert duplicate_graph_key.value.reason_code == TASK_STORE_DURABLE_DATA_INVALID
    with sqlite3.connect(str(store.db_path)) as conn:
        conn.execute(
            "UPDATE task_runtime_graph SET graph_json = ? WHERE graph_id = 'graph-1'",
            (valid_graph_json,),
        )

    valid_node_json = json.dumps(
        _graph().nodes[0].to_dict(),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    duplicate_nested_key = valid_node_json.replace(
        '"output_contract":{"type":"test-result"}',
        '"output_contract":{"type":"test-result","type":"tampered"}',
    )
    assert duplicate_nested_key != valid_node_json
    with sqlite3.connect(str(store.db_path)) as conn:
        conn.execute(
            "UPDATE task_runtime_node SET node_json = ? WHERE node_id = 'node-1'",
            (duplicate_nested_key,),
        )
    with pytest.raises(TaskStoreError) as duplicate_nested:
        store.get_node("graph-1", 1, "node-1")
    assert duplicate_nested.value.reason_code == TASK_STORE_DURABLE_DATA_INVALID

    with sqlite3.connect(str(store.db_path)) as conn:
        conn.execute(
            "UPDATE task_runtime_node SET node_json = '{}' WHERE node_id = 'node-1'"
        )
    with pytest.raises(TaskStoreError) as node_read:
        store.get_node("graph-1", 1, "node-1")
    assert node_read.value.reason_code == TASK_STORE_DURABLE_DATA_INVALID
    with pytest.raises(TaskStoreError) as node_transition:
        store.transition_node(
            "graph-1",
            1,
            "node-1",
            expected_version=0,
            target_status="LEASED",
            outbox=_event("TASK_NODE", "graph-1@1/node-1", 1, "TASK_NODE_LEASED"),
        )
    assert node_transition.value.reason_code == TASK_STORE_DURABLE_DATA_INVALID
    with sqlite3.connect(str(store.db_path)) as conn:
        node_row = conn.execute(
            "SELECT status, status_version, node_json FROM task_runtime_node WHERE node_id = 'node-1'"
        ).fetchone()
    assert node_row == ("READY", 0, "{}")

    budget = BudgetReservation(
        budget_id="budget-corrupt",
        scope="work-order:corrupt",
        limits=BudgetAmount(token_count=10),
        reserved=BudgetAmount(token_count=10),
        status=BudgetReservationStatus.ACTIVE,
        status_version=0,
    )
    store.create_budget_reservation(
        budget,
        outbox=_event(
            "BUDGET_RESERVATION", "budget-corrupt", 0, "BUDGET_RESERVATION_CREATED"
        ),
    )
    with sqlite3.connect(str(store.db_path)) as conn:
        conn.execute(
            """
            UPDATE task_runtime_budget_reservation
            SET consumed_json = '{}'
            WHERE budget_id = 'budget-corrupt'
            """
        )
    with pytest.raises(TaskStoreError) as budget_read:
        store.reconcile_budget("budget-corrupt")
    assert budget_read.value.reason_code == TASK_STORE_DURABLE_DATA_INVALID
    with pytest.raises(TaskStoreError) as budget_consume:
        store.consume_budget(
            "budget-corrupt",
            delta=BudgetAmount(token_count=1),
            expected_version=0,
            outbox=_event(
                "BUDGET_RESERVATION", "budget-corrupt", 1, "BUDGET_CONSUMED"
            ),
        )
    assert budget_consume.value.reason_code == TASK_STORE_DURABLE_DATA_INVALID
    with sqlite3.connect(str(store.db_path)) as conn:
        budget_row = conn.execute(
            """
            SELECT status_version, consumed_json
            FROM task_runtime_budget_reservation WHERE budget_id = 'budget-corrupt'
            """
        ).fetchone()
    assert budget_row == (0, "{}")


@pytest.mark.parametrize(
    "non_finite",
    (float("nan"), float("inf"), float("-inf")),
    ids=("nan", "positive-infinity", "negative-infinity"),
)
def test_t_nle_06_durable_json_rejects_non_finite_numbers_on_write_and_read(
    tmp_path: Path,
    non_finite: float,
) -> None:
    store = _migrated_store(
        tmp_path,
        name=f"non-finite-{repr(non_finite)}.sqlite3".replace("-", "neg"),
    )
    graph_payload = _graph().to_dict()
    graph_payload["nodes"][0]["output_contract"]["non_finite"] = non_finite
    public_v1_graph = TaskGraph.from_mapping(graph_payload)
    assert not math.isfinite(
        float(public_v1_graph.nodes[0].output_contract["non_finite"])
    )
    with pytest.raises(TaskStoreError) as rejected_write:
        store.create_graph(
            public_v1_graph,
            outbox=_event("TASK_GRAPH", "graph-1", 0, "TASK_GRAPH_CREATED"),
        )
    assert rejected_write.value.reason_code == TASK_STORE_INVALID
    with sqlite3.connect(str(store.db_path)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM task_runtime_graph").fetchone() == (0,)
        assert conn.execute("SELECT COUNT(*) FROM task_runtime_node").fetchone() == (0,)
        assert conn.execute("SELECT COUNT(*) FROM task_runtime_outbox").fetchone() == (0,)

    _create_graph(store)
    corrupt_payload = _graph().to_dict()
    corrupt_payload["nodes"][0]["output_contract"]["non_finite"] = non_finite
    non_standard_json = json.dumps(
        corrupt_payload,
        ensure_ascii=False,
        allow_nan=True,
    )
    with sqlite3.connect(str(store.db_path)) as conn:
        conn.execute(
            "UPDATE task_runtime_graph SET graph_json = ? WHERE graph_id = 'graph-1'",
            (non_standard_json,),
        )
    with pytest.raises(TaskStoreError) as rejected_read:
        store.get_graph("graph-1", 1)
    assert rejected_read.value.reason_code == TASK_STORE_DURABLE_DATA_INVALID


def test_t_nle_06_frozen_v1_public_deserializers_keep_compatibility_fallbacks() -> None:
    graph_payload = _graph().to_dict()
    graph_payload["nodes"] = {"node-1": graph_payload["nodes"][0]}
    assert TaskGraph.from_mapping(graph_payload).nodes == ()

    node_payload = _graph().nodes[0].to_dict()
    node_payload["output_contract"] = []
    assert TaskNode.from_mapping(node_payload).output_contract == {}

    budget_payload = BudgetReservation(
        budget_id="budget-structural",
        scope="work-order:structural",
        limits=BudgetAmount(token_count=1),
        reserved=BudgetAmount(token_count=1),
    ).to_dict()
    budget_payload["consumed"] = []
    assert BudgetReservation.from_mapping(budget_payload).consumed == BudgetAmount()


def test_t_nle_06_legacy_shadow_rejects_rollback_and_same_timestamp_conflict(
    tmp_path: Path,
) -> None:
    store = _migrated_store(tmp_path)
    latest = {
        "job_id": "agent-job::ordered",
        "kind": "test",
        "status": "DONE",
        "work_order": {"work_order_id": "wo-ordered"},
        "result": {"status": "EXECUTED"},
        "created_at_ms": 50,
        "updated_at_ms": 200,
    }
    first = store.mirror_legacy_job(latest)
    baseline_outbox = len(store.list_pending_outbox())

    older = dict(latest, status="RUNNING", updated_at_ms=100)
    with pytest.raises(TaskStoreError) as rollback:
        store.mirror_legacy_job(older)
    assert rollback.value.reason_code == TASK_STORE_CAS_CONFLICT

    idempotent = store.mirror_legacy_job(dict(latest))
    assert idempotent["shadow_version"] == first["shadow_version"]
    assert len(store.list_pending_outbox()) == baseline_outbox

    same_timestamp_conflict = dict(latest, status="FAILED")
    with pytest.raises(TaskStoreError) as conflict:
        store.mirror_legacy_job(same_timestamp_conflict)
    assert conflict.value.reason_code == TASK_STORE_CAS_CONFLICT
    shadow = store.get_legacy_job_shadow("agent-job::ordered")
    assert shadow is not None
    assert shadow["job"]["status"] == "DONE"
    assert shadow["job"]["updated_at_ms"] == 200
    assert shadow["shadow_version"] == first["shadow_version"]
    assert len(store.list_pending_outbox()) == baseline_outbox


def test_t_nle_06_corrupt_legacy_shadow_is_never_downgraded_or_overwritten(
    tmp_path: Path,
) -> None:
    store = _migrated_store(tmp_path)
    legacy_job = {
        "job_id": "agent-job::strict",
        "kind": "test",
        "status": "QUEUED",
        "work_order": {"work_order_id": "wo-strict"},
        "result": None,
        "created_at_ms": 100,
        "updated_at_ms": 100,
    }
    store.mirror_legacy_job(legacy_job)
    with sqlite3.connect(str(store.db_path)) as conn:
        conn.execute(
            """
            UPDATE task_runtime_graph SET legacy_snapshot_json = '{}'
            WHERE source_ref = 'agent-job::strict'
            """
        )
    with pytest.raises(TaskStoreError) as shadow_read:
        store.get_legacy_job_shadow("agent-job::strict")
    assert shadow_read.value.reason_code == TASK_STORE_DURABLE_DATA_INVALID

    updated_job = dict(legacy_job, status="RUNNING", updated_at_ms=101)
    with pytest.raises(TaskStoreError) as shadow_write:
        store.mirror_legacy_job(updated_job)
    assert shadow_write.value.reason_code == TASK_STORE_DURABLE_DATA_INVALID
    with sqlite3.connect(str(store.db_path)) as conn:
        row = conn.execute(
            """
            SELECT status_version, legacy_snapshot_json FROM task_runtime_graph
            WHERE source_ref = 'agent-job::strict'
            """
        ).fetchone()
    assert row == (0, "{}")


def test_t_nle_06_legacy_dual_write_is_shadow_only_redacted_and_reconciled(tmp_path: Path) -> None:
    store = _migrated_store(tmp_path)
    registry = AgentJobRegistry(task_store=store, read_mode="shadow")
    job = registry.create(
        kind="codex_work_order",
        work_order={
            "schema_version": "Natsume.agent.work_order.v2",
            "work_order_id": "wo-legacy",
            "objective": "safe objective",
            "analysis": "private chain of thought must never be persisted",
        },
    )
    assert registry.read_authority == "LEGACY"
    assert registry.get(job["job_id"]) == job
    assert registry.reconcile(job["job_id"])["classification"] == "MATCH"

    updated = registry.update(
        job["job_id"],
        status="COMPLETED",
        result={
            "status": "EXECUTED",
            "reasoning": "another private reasoning body",
            "evidence_refs": ["evidence://1"],
        },
    )
    assert updated is not None
    assert registry.get(job["job_id"])["result"]["reasoning"] == "another private reasoning body"
    report = registry.reconcile(job["job_id"])
    assert report["classification"] == "MATCH"
    assert report["authority"] == "LEGACY"
    assert "work_order_digest" in report["durable_shadow"]
    assert "work_order" not in report["durable_shadow"]

    with sqlite3.connect(str(store.db_path)) as conn:
        persisted = str(
            conn.execute(
                "SELECT legacy_snapshot_json FROM task_runtime_graph WHERE source_ref = ?",
                (job["job_id"],),
            ).fetchone()[0]
        )
    assert "private chain of thought" not in persisted
    assert "private reasoning body" not in persisted
    assert "safe objective" not in persisted
    projection = json.loads(persisted)
    assert projection["result_status"] == "EXECUTED"
    assert projection["result_digest"].startswith("sha256:")


def test_t_nle_06_shadow_failure_never_changes_legacy_return_or_authority() -> None:
    class BrokenShadow:
        def mirror_legacy_job(self, job: dict[str, Any]) -> None:
            raise RuntimeError("shadow database unavailable")

        def reconcile_legacy_job(self, job_id: str, job: dict[str, Any] | None) -> dict[str, Any]:
            raise RuntimeError("shadow read unavailable")

    registry = AgentJobRegistry(task_store=BrokenShadow(), read_mode="shadow")  # type: ignore[arg-type]
    job = registry.create(kind="test", work_order={"work_order_id": "wo-1"})
    updated = registry.update(job["job_id"], status="RUNNING")

    assert updated is not None
    assert updated["status"] == "RUNNING"
    assert registry.get(job["job_id"])["status"] == "RUNNING"
    assert registry.read_authority == "LEGACY"
    report = registry.reconcile(job["job_id"])
    assert report["classification"] == "MIRROR_WRITE_FAILED"
    assert report["match"] is False
    assert report["reason_code"] == "TASK_STORE_SHADOW_WRITE_FAILED"

    with pytest.raises(ValueError):
        AgentJobRegistry(read_mode="durable")  # type: ignore[arg-type]
