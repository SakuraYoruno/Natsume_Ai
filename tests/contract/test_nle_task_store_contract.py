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

from host_brain.contracts.agent_runtime.budget_policy import (
    BudgetAmount,
    BudgetReservation,
    BudgetReservationStatus,
)
from host_brain.contracts.agent_runtime.acceptance_judge import (
    ACCEPTANCE_EVIDENCE_REQUIRED,
    ACCEPTANCE_REQUIRED_NOT_ACCEPTED,
    AcceptanceJudgement,
    judge_acceptance_result,
)
import host_brain.persistence.task_repository as task_store_module
from host_brain.persistence import (
    reconciliation_proof_verifier as reconciliation_verifier_module,
)
from host_brain.contracts.agent_runtime.task_graph import (
    TaskGraph,
    TaskGraphStatus,
    TaskNode,
    TaskNodeStatus,
)
from host_brain.persistence.task_repository import (
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
from tests.task_runtime_authority_fixtures import seed_raw_task_graph_for_test
from host_brain.contracts.agent_runtime.verification_contract import (
    VERIFICATION_EVIDENCE_REQUIRED,
    VERIFICATION_REQUIRED_NOT_PASSED,
    VerificationResult,
    VerificationStatus,
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
    graph_revision: int | None = None,
    attempt_id: str | None = None,
    evidence_refs: tuple[str, ...] = (),
) -> OutboxMessage:
    unique = uuid.uuid4().hex
    normalized_graph_revision = (
        int(graph_revision or 1) if aggregate_type == "TASK_GRAPH" else None
    )
    return OutboxMessage.create(
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        aggregate_version=aggregate_version,
        event_type=event_type,
        graph_revision=normalized_graph_revision,
        payload_ref=build_outbox_payload_ref(
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            aggregate_version=aggregate_version,
            graph_revision=normalized_graph_revision,
        ),
        idempotency_key=idempotency_key or f"event:{unique}",
        attempt_id=attempt_id,
        evidence_refs=evidence_refs,
    )


def _graph(
    *,
    graph_id: str = "graph-1",
    revision: int = 1,
    node_status: TaskNodeStatus = TaskNodeStatus.READY,
) -> TaskGraph:
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
        revision=revision,
        planner_ref=f"planner://test/{revision}",
        status=TaskGraphStatus.CREATED,
        created_at=1_700_000_000_000,
        nodes=(node,),
    )
    graph.validate()
    return graph


def _dependency_graph(*, graph_id: str = "graph-dependencies") -> TaskGraph:
    graph = TaskGraph(
        graph_id=graph_id,
        root_work_order_id=f"work-order::{graph_id}",
        revision=1,
        planner_ref="planner://test/dependencies",
        status=TaskGraphStatus.CREATED,
        created_at=1_700_000_000_000,
        nodes=(
            TaskNode(
                node_id="root",
                dependencies=(),
                route="skill:test",
                input_refs=(),
                output_contract={},
                retry_policy={"max_attempts": 1},
                risk_level=1,
                acceptance_criteria=(),
                status=TaskNodeStatus.READY,
                status_version=0,
            ),
            TaskNode(
                node_id="dependent",
                dependencies=("root",),
                route="skill:test",
                input_refs=(),
                output_contract={},
                retry_policy={"max_attempts": 1},
                risk_level=1,
                acceptance_criteria=(),
                status=TaskNodeStatus.READY,
                status_version=0,
            ),
        ),
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


def _seed_raw_graph(store: DurableTaskStore, *, graph_id: str = "graph-1") -> None:
    seed_raw_task_graph_for_test(
        store,
        _graph(graph_id=graph_id),
        outbox=_event("TASK_GRAPH", graph_id, 0, "TASK_GRAPH_CREATED"),
    )


def _transition_graph_path(
    store: DurableTaskStore,
    *statuses: TaskGraphStatus,
    graph_id: str = "graph-1",
) -> None:
    for target in statuses:
        graph = store.get_graph(graph_id, 1)
        version = int(graph["status_version"])
        store.transition_graph(
            graph_id,
            1,
            expected_version=version,
            target_status=target,
            outbox=_event(
                "TASK_GRAPH",
                graph_id,
                version + 1,
                f"TASK_GRAPH_{target.value}",
            ),
        )


def _verification_result(
    *,
    status: VerificationStatus = VerificationStatus.PASS,
    graph_id: str = "graph-1",
    node_id: str = "node-1",
    work_order_id: str = "work-order::graph-1",
) -> VerificationResult:
    return VerificationResult(
        verification_id=f"verify-{status.value.lower()}",
        work_order_id=work_order_id,
        required=True,
        status=status,
        task_graph_id=graph_id,
        task_node_id=node_id,
        reason_code=None if status is VerificationStatus.PASS else f"VERIFICATION_{status.value}",
        evidence_refs=(f"verification://verify-{status.value.lower()}/check/1/sha256-{status.value.lower()}",),
        checks=({"status": status.value},),
        started_at_ms=100,
        finished_at_ms=101,
    )


def _acceptance_judgement(*, returncode: int = 0) -> AcceptanceJudgement:
    return judge_acceptance_result(
        {
            "status": "EXECUTED",
            "work_order_id": "work-order::graph-1",
            "summary": "Agent WorkOrder executed.",
            "result_text": "exit code is zero",
            "verification": {
                "status": "PASS",
                "evidence_refs": ["verification://verify-pass/check/1/pass"],
            },
            "metadata": {"returncode": returncode},
        },
        work_order_id="work-order::graph-1",
        acceptance_criteria=("exit code is zero",),
        task_graph_id="graph-1",
        task_node_id="node-1",
    )


def _create_attempt(
    store: DurableTaskStore,
    *,
    attempt_id: str = "attempt-1",
    operation_scope: str = "repo:task-0467",
    operation_fingerprint: str = "sha256:operation-1",
    ensure_budget: bool = True,
) -> None:
    if ensure_budget:
        _ensure_active_graph_budget(store)
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


def _ensure_active_graph_budget(
    store: DurableTaskStore,
    *,
    graph_id: str = "graph-1",
    graph_revision: int = 1,
) -> None:
    budget_id = f"budget::test::{graph_id}@{graph_revision}"
    with sqlite3.connect(str(store.db_path)) as conn:
        rows = conn.execute(
            """
            SELECT budget_id, status
            FROM task_runtime_budget_reservation
            WHERE graph_id = ? AND graph_revision = ?
              AND node_id IS NULL AND attempt_id IS NULL
            ORDER BY budget_id
            """,
            (graph_id, int(graph_revision)),
        ).fetchall()
    if rows:
        assert rows == [(budget_id, "ACTIVE")]
        return
    store.create_budget_reservation(
        BudgetReservation(
            budget_id=budget_id,
            scope=f"graph:{graph_id}@{graph_revision}:test",
            limits=BudgetAmount(token_count=1_000_000),
            reserved=BudgetAmount(token_count=1_000_000),
            status=BudgetReservationStatus.ACTIVE,
        ),
        graph_id=graph_id,
        graph_revision=graph_revision,
        outbox=_event(
            "BUDGET_RESERVATION",
            budget_id,
            0,
            "BUDGET_RESERVATION_CREATED",
        ),
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
        verification = _verification_result() if target is TaskNodeStatus.VERIFIED else None
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
                evidence_refs=verification.evidence_refs if verification else (),
            ),
            verification_result=verification,
        )


def test_gr03a_attempt_claim_requires_every_dependency_to_be_accepted(tmp_path: Path) -> None:
    store = _migrated_store(tmp_path, name="dependency-gate.sqlite3")
    graph = _dependency_graph()
    seed_raw_task_graph_for_test(
        store,
        graph,
        outbox=_event("TASK_GRAPH", graph.graph_id, 0, "TASK_GRAPH_CREATED"),
    )
    _ensure_active_graph_budget(store, graph_id=graph.graph_id)

    with pytest.raises(TaskStoreError) as error:
        store.create_attempt(
            attempt_id="attempt-dependent-before-root",
            graph_id=graph.graph_id,
            graph_revision=graph.revision,
            node_id="dependent",
            attempt_no=1,
            expected_node_version=0,
            operation_scope=None,
            operation_fingerprint=None,
            outbox=_event(
                "TASK_ATTEMPT",
                "attempt-dependent-before-root",
                0,
                "TASK_ATTEMPT_CREATED",
                attempt_id="attempt-dependent-before-root",
            ),
        )

    assert error.value.reason_code == TASK_STORE_INVALID
    assert store.read_scheduler_snapshot(graph.graph_id, graph.revision)["attempts"] == []


@pytest.mark.parametrize(
    "status",
    (VerificationStatus.FAIL, VerificationStatus.ERROR, VerificationStatus.NOT_RUN),
)
def test_t_nle_08_required_verification_non_pass_cannot_reach_verified_or_accepted(
    tmp_path: Path,
    status: VerificationStatus,
) -> None:
    store = _migrated_store(tmp_path)
    seed_raw_task_graph_for_test(
        store,
        _graph(node_status=TaskNodeStatus.VERIFYING),
        outbox=_event("TASK_GRAPH", "graph-1", 0, "TASK_GRAPH_CREATED"),
    )
    verification = _verification_result(status=status)

    with pytest.raises(TaskStoreError) as error:
        store.transition_node(
            "graph-1",
            1,
            "node-1",
            expected_version=0,
            target_status=TaskNodeStatus.VERIFIED,
            outbox=_event(
                "TASK_NODE",
                "graph-1@1/node-1",
                1,
                "TASK_NODE_VERIFIED",
                evidence_refs=verification.evidence_refs,
            ),
            verification_result=verification,
        )

    assert error.value.reason_code == VERIFICATION_REQUIRED_NOT_PASSED
    assert store.get_node("graph-1", 1, "node-1")["status"] == "VERIFYING"
    with sqlite3.connect(str(store.db_path)) as conn:
        accepted_count = conn.execute(
            "SELECT COUNT(*) FROM task_runtime_node WHERE status = 'ACCEPTED'"
        ).fetchone()[0]
    assert accepted_count == 0


def test_t_nle_08_verified_transition_requires_pass_result_and_bound_evidence(tmp_path: Path) -> None:
    store = _migrated_store(tmp_path)
    seed_raw_task_graph_for_test(
        store,
        _graph(node_status=TaskNodeStatus.VERIFYING),
        outbox=_event("TASK_GRAPH", "graph-1", 0, "TASK_GRAPH_CREATED"),
    )
    verification = _verification_result()

    with pytest.raises(TaskStoreError) as missing_evidence:
        store.transition_node(
            "graph-1",
            1,
            "node-1",
            expected_version=0,
            target_status=TaskNodeStatus.VERIFIED,
            outbox=_event("TASK_NODE", "graph-1@1/node-1", 1, "TASK_NODE_VERIFIED"),
            verification_result=verification,
        )
    assert missing_evidence.value.reason_code == VERIFICATION_EVIDENCE_REQUIRED

    verified = store.transition_node(
        "graph-1",
        1,
        "node-1",
        expected_version=0,
        target_status=TaskNodeStatus.VERIFIED,
        outbox=_event(
            "TASK_NODE",
            "graph-1@1/node-1",
            1,
            "TASK_NODE_VERIFIED",
            evidence_refs=verification.evidence_refs,
        ),
        verification_result=verification,
    )

    assert verified["status"] == "VERIFIED"
    verified_events = [
        event
        for event in store.list_pending_outbox()
        if event["event_type"] == "TASK_NODE_VERIFIED"
    ]
    assert verified_events[-1]["evidence_refs"] == list(verification.evidence_refs)


def test_t_nle_09_accepted_transition_requires_acceptance_judgement_and_evidence(tmp_path: Path) -> None:
    store = _migrated_store(tmp_path)
    seed_raw_task_graph_for_test(
        store,
        _graph(node_status=TaskNodeStatus.WAITING_ACCEPTANCE_REVIEW),
        outbox=_event("TASK_GRAPH", "graph-1", 0, "TASK_GRAPH_CREATED"),
    )
    passing = _acceptance_judgement(returncode=0)
    failing = _acceptance_judgement(returncode=1)

    with pytest.raises(TaskStoreError) as missing_judgement:
        store.transition_node(
            "graph-1",
            1,
            "node-1",
            expected_version=0,
            target_status=TaskNodeStatus.ACCEPTED,
            outbox=_event("TASK_NODE", "graph-1@1/node-1", 1, "TASK_NODE_ACCEPTED"),
        )
    assert missing_judgement.value.reason_code == ACCEPTANCE_REQUIRED_NOT_ACCEPTED

    with pytest.raises(TaskStoreError) as failed_judgement:
        store.transition_node(
            "graph-1",
            1,
            "node-1",
            expected_version=0,
            target_status=TaskNodeStatus.ACCEPTED,
            outbox=_event(
                "TASK_NODE",
                "graph-1@1/node-1",
                1,
                "TASK_NODE_ACCEPTED",
                evidence_refs=failing.evidence_refs,
            ),
            acceptance_judgement=failing,
        )
    assert failed_judgement.value.reason_code == ACCEPTANCE_REQUIRED_NOT_ACCEPTED

    with pytest.raises(TaskStoreError) as missing_evidence:
        store.transition_node(
            "graph-1",
            1,
            "node-1",
            expected_version=0,
            target_status=TaskNodeStatus.ACCEPTED,
            outbox=_event("TASK_NODE", "graph-1@1/node-1", 1, "TASK_NODE_ACCEPTED"),
            acceptance_judgement=passing,
        )
    assert missing_evidence.value.reason_code == ACCEPTANCE_EVIDENCE_REQUIRED

    accepted = store.transition_node(
        "graph-1",
        1,
        "node-1",
        expected_version=0,
        target_status=TaskNodeStatus.ACCEPTED,
        outbox=_event(
            "TASK_NODE",
            "graph-1@1/node-1",
            1,
            "TASK_NODE_ACCEPTED",
            evidence_refs=passing.evidence_refs,
        ),
        acceptance_judgement=passing,
    )

    assert accepted["status"] == "ACCEPTED"


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

    monkeypatch.setattr(reconciliation_verifier_module, "_Ed25519PublicKey", None)
    with pytest.raises(TaskStoreError) as missing_dependency:
        Ed25519ReconciliationProofVerifier({"reconciler-v1": public_keys["reconciler-v1"]})
    assert missing_dependency.value.reason_code == TASK_STORE_RECONCILIATION_AUTHORITY_REQUIRED


def test_t_nle_06_competing_expected_version_cas_has_one_winner_and_one_outbox(tmp_path: Path) -> None:
    store = _migrated_store(tmp_path)
    _seed_raw_graph(store)

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
    seed_raw_task_graph_for_test(store, _graph(), outbox=create_event)

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


def test_t_nle_06_graph_transition_rejects_outbox_from_another_revision(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "task-runtime-revision-boundary.sqlite3"
    migrated = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="task-runtime-revision-boundary",
        ts_ms=100,
    )
    assert migrated.success is True, migrated.as_dict()
    store = DurableTaskStore(
        db_path,
        clock=MutableClock(),
        verify_schema=False,
    )
    graph_id = "graph-revision-boundary"
    for revision in (1, 2):
        seed_raw_task_graph_for_test(
            store,
            _graph(graph_id=graph_id, revision=revision),
            outbox=_event(
                "TASK_GRAPH",
                graph_id,
                0,
                "TASK_GRAPH_CREATED",
                graph_revision=revision,
            ),
        )

    with pytest.raises(TaskStoreError) as wrong_revision:
        store.transition_graph(
            graph_id,
            1,
            expected_version=0,
            target_status=TaskGraphStatus.ACTIVE,
            outbox=_event(
                "TASK_GRAPH",
                graph_id,
                1,
                "TASK_GRAPH_ACTIVE",
                graph_revision=2,
            ),
        )

    assert wrong_revision.value.reason_code == TASK_STORE_INVALID
    assert store.get_graph(graph_id, 1)["status"] == "CREATED"
    assert store.get_graph(graph_id, 1)["status_version"] == 0
    assert store.get_graph(graph_id, 2)["status"] == "CREATED"
    assert len(store.list_pending_outbox()) == 2


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
            graph_revision=1,
            aggregate_version=0,
            event_type="TASK_GRAPH_CREATED",
            payload_ref="taskstore://event/TASK_GRAPH/wrong-graph/0",
        ),
        OutboxMessage.create(
            aggregate_type="TASK_GRAPH",
            aggregate_id="graph-1",
            graph_revision=1,
            aggregate_version=0,
            event_type="TASK_GRAPH_CREATED",
            payload_ref=canonical,
            available_ts_ms=9_999_999_999,
        ),
        OutboxMessage.create(
            aggregate_type="TASK_GRAPH",
            aggregate_id="graph-1",
            graph_revision=1,
            aggregate_version=0,
            event_type="TASK_GRAPH_CREATED",
            payload_ref=canonical,
            evidence_refs=("raw secret token body",),
        ),
    )
    for message in invalid_messages:
        with pytest.raises(TaskStoreError) as invalid:
            seed_raw_task_graph_for_test(store, _graph(), outbox=message)
        assert invalid.value.reason_code == TASK_STORE_INVALID
        with sqlite3.connect(str(store.db_path)) as conn:
            assert conn.execute("SELECT COUNT(*) FROM task_runtime_graph").fetchone() == (0,)
            assert conn.execute("SELECT COUNT(*) FROM task_runtime_outbox").fetchone() == (0,)


def test_t_nle_06_wrong_event_rolls_back_node_attempt_lease_operation_recovery_and_budget(
    tmp_path: Path,
) -> None:
    private_key, verifier = _proof_authority()
    store = _migrated_store(tmp_path, verifier=verifier)
    _seed_raw_graph(store)
    _ensure_active_graph_budget(store)

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
        ).fetchone() == (1,)
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
    _seed_raw_graph(store)
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
    _seed_raw_graph(store)
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
    _seed_raw_graph(store)
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
    _seed_raw_graph(store)
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
    _seed_raw_graph(store)
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
    _seed_raw_graph(store)
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
    _seed_raw_graph(store)
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
    _seed_raw_graph(store)
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


@pytest.mark.parametrize(
    "terminal_path",
    (
        (TaskGraphStatus.BLOCKED,),
        (TaskGraphStatus.ACTIVE, TaskGraphStatus.COMPLETED),
        (TaskGraphStatus.ACTIVE, TaskGraphStatus.FAILED),
        (TaskGraphStatus.CANCELLED,),
    ),
    ids=("blocked", "completed", "failed", "cancelled"),
)
def test_graph_bound_budget_creation_rejects_non_executable_graph_without_writes(
    tmp_path: Path,
    terminal_path: tuple[TaskGraphStatus, ...],
) -> None:
    store = _migrated_store(tmp_path)
    _seed_raw_graph(store)
    _transition_graph_path(store, *terminal_path)
    terminal_status = terminal_path[-1].value
    budget_id = f"budget-create-{terminal_status.lower()}"
    with sqlite3.connect(str(store.db_path)) as conn:
        before = (
            conn.execute(
                "SELECT COUNT(*) FROM task_runtime_budget_reservation"
            ).fetchone()[0],
            conn.execute("SELECT COUNT(*) FROM task_runtime_outbox").fetchone()[0],
        )

    with pytest.raises(TaskStoreError) as rejected:
        store.create_budget_reservation(
            BudgetReservation(
                budget_id=budget_id,
                scope=f"graph:graph-1@1:{terminal_status.lower()}",
                limits=BudgetAmount(token_count=10),
                reserved=BudgetAmount(token_count=10),
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

    assert rejected.value.reason_code == TASK_STORE_CAS_CONFLICT
    assert store.get_graph("graph-1", 1)["status"] == terminal_status
    with sqlite3.connect(str(store.db_path)) as conn:
        after = (
            conn.execute(
                "SELECT COUNT(*) FROM task_runtime_budget_reservation"
            ).fetchone()[0],
            conn.execute("SELECT COUNT(*) FROM task_runtime_outbox").fetchone()[0],
        )
        assert conn.execute(
            "SELECT COUNT(*) FROM task_runtime_budget_reservation WHERE budget_id = ?",
            (budget_id,),
        ).fetchone() == (0,)
    assert after == before


@pytest.mark.parametrize(
    "terminal_path",
    (
        (TaskGraphStatus.BLOCKED,),
        (TaskGraphStatus.ACTIVE, TaskGraphStatus.COMPLETED),
        (TaskGraphStatus.ACTIVE, TaskGraphStatus.FAILED),
        (TaskGraphStatus.CANCELLED,),
    ),
    ids=("blocked", "completed", "failed", "cancelled"),
)
def test_graph_bound_budget_activation_rejects_non_executable_graph_without_writes(
    tmp_path: Path,
    terminal_path: tuple[TaskGraphStatus, ...],
) -> None:
    store = _migrated_store(tmp_path)
    _seed_raw_graph(store)
    terminal_status = terminal_path[-1].value
    budget_id = f"budget-activate-{terminal_status.lower()}"
    store.create_budget_reservation(
        BudgetReservation(
            budget_id=budget_id,
            scope=f"graph:graph-1@1:{terminal_status.lower()}",
            limits=BudgetAmount(token_count=10),
            reserved=BudgetAmount(token_count=10),
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
    _transition_graph_path(store, *terminal_path)
    with sqlite3.connect(str(store.db_path)) as conn:
        before_budget = conn.execute(
            """
            SELECT status, status_version, updated_ts_ms
            FROM task_runtime_budget_reservation WHERE budget_id = ?
            """,
            (budget_id,),
        ).fetchone()
        before_outboxes = conn.execute(
            "SELECT COUNT(*) FROM task_runtime_outbox"
        ).fetchone()[0]

    with pytest.raises(TaskStoreError) as rejected:
        store.transition_budget(
            budget_id,
            expected_version=0,
            target_status=BudgetReservationStatus.ACTIVE,
            outbox=_event(
                "BUDGET_RESERVATION",
                budget_id,
                1,
                "BUDGET_RESERVATION_ACTIVE",
            ),
        )

    assert rejected.value.reason_code == TASK_STORE_CAS_CONFLICT
    assert store.get_graph("graph-1", 1)["status"] == terminal_status
    with sqlite3.connect(str(store.db_path)) as conn:
        after_budget = conn.execute(
            """
            SELECT status, status_version, updated_ts_ms
            FROM task_runtime_budget_reservation WHERE budget_id = ?
            """,
            (budget_id,),
        ).fetchone()
        after_outboxes = conn.execute(
            "SELECT COUNT(*) FROM task_runtime_outbox"
        ).fetchone()[0]
    assert after_budget == before_budget == ("PENDING", 0, 100)
    assert after_outboxes == before_outboxes


def test_terminal_graph_budget_guard_does_not_block_settlement_or_unbound_budget(
    tmp_path: Path,
) -> None:
    store = _migrated_store(tmp_path)
    _seed_raw_graph(store)
    graph_budget_id = "budget-terminal-settlement"
    store.create_budget_reservation(
        BudgetReservation(
            budget_id=graph_budget_id,
            scope="graph:graph-1@1:settlement",
            limits=BudgetAmount(token_count=10),
            reserved=BudgetAmount(token_count=10),
        ),
        graph_id="graph-1",
        graph_revision=1,
        outbox=_event(
            "BUDGET_RESERVATION",
            graph_budget_id,
            0,
            "BUDGET_RESERVATION_CREATED",
        ),
    )
    store.transition_budget(
        graph_budget_id,
        expected_version=0,
        target_status=BudgetReservationStatus.ACTIVE,
        outbox=_event(
            "BUDGET_RESERVATION",
            graph_budget_id,
            1,
            "BUDGET_RESERVATION_ACTIVE",
        ),
    )
    _transition_graph_path(
        store,
        TaskGraphStatus.ACTIVE,
        TaskGraphStatus.FAILED,
    )

    consumed = store.consume_budget(
        graph_budget_id,
        delta=BudgetAmount(token_count=1),
        expected_version=1,
        outbox=_event(
            "BUDGET_RESERVATION",
            graph_budget_id,
            2,
            "BUDGET_CONSUMED",
        ),
    )
    assert consumed.accepted is True
    released = store.transition_budget(
        graph_budget_id,
        expected_version=2,
        target_status=BudgetReservationStatus.RELEASED,
        outbox=_event(
            "BUDGET_RESERVATION",
            graph_budget_id,
            3,
            "BUDGET_RESERVATION_RELEASED",
        ),
    )
    assert released["status"] == "RELEASED"

    unbound_budget_id = "budget-unbound-after-terminal"
    store.create_budget_reservation(
        BudgetReservation(
            budget_id=unbound_budget_id,
            scope="unbound:test",
            limits=BudgetAmount(token_count=1),
            reserved=BudgetAmount(token_count=1),
        ),
        outbox=_event(
            "BUDGET_RESERVATION",
            unbound_budget_id,
            0,
            "BUDGET_RESERVATION_CREATED",
        ),
    )
    unbound = store.transition_budget(
        unbound_budget_id,
        expected_version=0,
        target_status=BudgetReservationStatus.ACTIVE,
        outbox=_event(
            "BUDGET_RESERVATION",
            unbound_budget_id,
            1,
            "BUDGET_RESERVATION_ACTIVE",
        ),
    )
    assert unbound["status"] == "ACTIVE"


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
    _seed_raw_graph(store)
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


def test_ne01_retry_deadline_invariant_and_backoff_cancellation(tmp_path: Path) -> None:
    db_path = tmp_path / "retry-cancel.sqlite3"
    migrated = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="ne01-retry-cancel",
        ts_ms=100,
    )
    assert migrated.success is True, migrated.as_dict()
    store = DurableTaskStore(db_path, clock=MutableClock(200))
    graph = _graph(node_status=TaskNodeStatus.RETRY_PENDING)
    seed_raw_task_graph_for_test(
        store,
        graph,
        outbox=_event("TASK_GRAPH", graph.graph_id, 0, "TASK_GRAPH_CREATED"),
    )

    with pytest.raises(TaskStoreError) as invalid_deadline:
        store.assert_dag_scheduling_ready()
    assert invalid_deadline.value.reason_code == TASK_STORE_DURABLE_DATA_INVALID

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            UPDATE task_runtime_node
            SET retry_not_before_ms = 500
            WHERE graph_id = ? AND graph_revision = 1 AND node_id = 'node-1'
            """,
            (graph.graph_id,),
        )
        conn.commit()
    store.assert_dag_scheduling_ready()

    cancelled = store.request_cancel_node(
        graph.graph_id,
        graph.revision,
        "node-1",
        expected_version=0,
        outbox=_event(
            "TASK_NODE",
            f"{graph.graph_id}@{graph.revision}/node-1",
            1,
            "TASK_NODE_CANCELLED",
        ),
    )
    assert cancelled["status"] == "CANCELLED"
    assert cancelled["retry_not_before_ms"] is None
    assert cancelled["cancellation_requested_ts_ms"] == 200
    store.assert_dag_scheduling_ready()


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
    _seed_raw_graph(store)
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
    seed_raw_task_graph_for_test(
        store,
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
    _seed_raw_graph(store)
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
    _seed_raw_graph(store)
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
    _seed_raw_graph(created_store)
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
    _seed_raw_graph(requested_store)
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
    _seed_raw_graph(store)
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
        seed_raw_task_graph_for_test(
            store,
            public_v1_graph,
            outbox=_event("TASK_GRAPH", "graph-1", 0, "TASK_GRAPH_CREATED"),
        )
    assert rejected_write.value.reason_code == TASK_STORE_INVALID
    with sqlite3.connect(str(store.db_path)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM task_runtime_graph").fetchone() == (0,)
        assert conn.execute("SELECT COUNT(*) FROM task_runtime_node").fetchone() == (0,)
        assert conn.execute("SELECT COUNT(*) FROM task_runtime_outbox").fetchone() == (0,)

    _seed_raw_graph(store)
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


def test_ne01_optional_result_authority_preserves_legacy_graph_wire_and_store_shape(
    tmp_path: Path,
) -> None:
    graph = _graph(graph_id="graph-legacy-result-authority")
    legacy_payload = graph.to_dict()
    assert "result_node_id" not in legacy_payload

    restored = TaskGraph.from_mapping(legacy_payload)
    assert restored.result_node_id is None
    assert restored.to_dict() == legacy_payload

    store = _migrated_store(tmp_path, name="legacy-result-authority.sqlite3")
    seed_raw_task_graph_for_test(
        store,
        restored,
        outbox=_event(
            "TASK_GRAPH",
            restored.graph_id,
            0,
            "TASK_GRAPH_CREATED",
        ),
    )
    durable = store.get_graph(restored.graph_id, restored.revision)
    assert "result_node_id" not in durable["graph"]
    assert TaskGraph.from_mapping(durable["graph"]).to_dict() == legacy_payload
