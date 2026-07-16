from __future__ import annotations

import hashlib
import sqlite3
from typing import Any

import pytest

from host_brain.audit.migration_runner import SQLiteMigrationRunner


CASE_TABLE = "task_runtime_terminal_reconciliation_case"
PROOF_TABLE = "task_runtime_terminal_reconciliation_proof"
SUPERSESSION_TABLE = "task_runtime_terminal_reconciliation_supersession"
CASE_SCHEMA = "Natsume.task_runtime.terminal_reconciliation.case.v1"
PROOF_SCHEMA = "Natsume.task_runtime.terminal_reconciliation.proof.v1"
SIGNATURE = "ed25519:" + ("A" * 86) + "=="

TRIGGERS = {
    "trg_task_runtime_terminal_case_binding_insert",
    "trg_task_runtime_terminal_proof_binding_insert",
    "trg_task_runtime_terminal_case_immutable_update",
    "trg_task_runtime_terminal_case_immutable_delete",
    "trg_task_runtime_terminal_proof_immutable_update",
    "trg_task_runtime_terminal_proof_immutable_delete",
    "trg_task_runtime_terminal_supersession_binding_insert",
    "trg_task_runtime_terminal_healthy_closure_origin_insert",
    "trg_task_runtime_terminal_supersession_immutable_update",
    "trg_task_runtime_terminal_supersession_immutable_delete",
}


def _sha(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _migrated_db(tmp_path, name: str) -> tuple[SQLiteMigrationRunner, Any]:
    db_path = tmp_path / name
    runner = SQLiteMigrationRunner(db_path=db_path)
    result = runner.apply_migrations(request_id=f"0099:{name}", ts_ms=99)
    assert result.success is True, result.as_dict()
    assert result.summary["current_version"] >= 101
    assert "0101_agent_workspace_authority" in {
        record.migration_id for record in result.records
    }
    return runner, db_path


def _connect(db_path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _insert_graph(
    conn: sqlite3.Connection,
    *,
    graph_id: str,
    status: str,
    version: int,
) -> None:
    conn.execute(
        """
        INSERT INTO task_runtime_graph(
            graph_id, revision, root_work_order_id, planner_ref,
            status, status_version, graph_json, source_kind,
            created_ts_ms, updated_ts_ms
        ) VALUES (?, 1, ?, ?, ?, ?, '{}', 'CANONICAL', 1, 1)
        """,
        (
            graph_id,
            f"work-order-{graph_id}",
            f"planner://{graph_id}",
            status,
            version,
        ),
    )


def _insert_budget(
    conn: sqlite3.Connection,
    *,
    budget_id: str,
    graph_id: str,
    status: str,
    version: int,
) -> None:
    conn.execute(
        """
        INSERT INTO task_runtime_budget_reservation(
            budget_id, scope, graph_id, graph_revision,
            limits_json, reserved_json, consumed_json,
            attempted_consumed_json, exceeded_dimensions_json,
            status, status_version, created_ts_ms, updated_ts_ms
        ) VALUES (?, ?, ?, 1, '{}', '{}', '{}', '{}', '[]', ?, ?, 1, 1)
        """,
        (budget_id, f"scope:{graph_id}", graph_id, status, version),
    )


def _insert_failed_node_attempt(
    conn: sqlite3.Connection,
    *,
    graph_id: str,
    node_id: str,
    attempt_id: str,
) -> None:
    conn.execute(
        """
        INSERT INTO task_runtime_node(
            graph_id, graph_revision, node_id, status, status_version,
            node_json, created_ts_ms, updated_ts_ms
        ) VALUES (?, 1, ?, 'FAILED', 1, '{}', 1, 1)
        """,
        (graph_id, node_id),
    )
    conn.execute(
        """
        INSERT INTO task_runtime_attempt(
            attempt_id, graph_id, graph_revision, node_id, attempt_no,
            status, status_version, created_ts_ms, completed_ts_ms,
            updated_ts_ms
        ) VALUES (?, ?, 1, ?, 1, 'FAILED', 1, 1, 1, 1)
        """,
        (attempt_id, graph_id, node_id),
    )


def _case_values(
    *,
    case_id: str,
    graph_id: str,
    graph_status: str,
    graph_version: int,
    case_kind: str,
    classification: str,
    recommended_action: str,
    budget_id: str | None = None,
    budget_status: str | None = None,
    budget_version: int | None = None,
    node_id: str | None = None,
    node_status: str | None = None,
    node_version: int | None = None,
    attempt_id: str | None = None,
    attempt_status: str | None = None,
    attempt_version: int | None = None,
) -> tuple[Any, ...]:
    return (
        case_id,
        CASE_SCHEMA,
        case_kind,
        classification,
        f"REASON_{case_id}",
        graph_id,
        1,
        graph_status,
        graph_version,
        budget_id,
        budget_status,
        budget_version,
        node_id,
        node_status,
        node_version,
        attempt_id,
        attempt_status,
        attempt_version,
        recommended_action,
        "{}",
        _sha(case_id),
        1,
    )


def _insert_case(conn: sqlite3.Connection, values: tuple[Any, ...]) -> None:
    conn.execute(
        f"""
        INSERT INTO {CASE_TABLE}(
            case_id, schema_version, case_kind, classification, reason_code,
            graph_id, graph_revision, graph_status, graph_status_version,
            budget_id, budget_status, budget_status_version,
            node_id, node_status, node_status_version,
            attempt_id, attempt_status, attempt_status_version,
            recommended_action, snapshot_json, snapshot_sha256, created_ts_ms
        ) VALUES ({", ".join("?" for _ in values)})
        """,
        values,
    )


def _insert_outbox(
    conn: sqlite3.Connection,
    *,
    outbox_id: str,
    aggregate_type: str,
    aggregate_id: str,
    aggregate_version: int,
    event_type: str,
    graph_revision: int | None = None,
) -> None:
    payload_ref = (
        f"taskstore://event/TASK_GRAPH/{aggregate_id}@{graph_revision}/"
        f"{aggregate_version}"
        if aggregate_type == "TASK_GRAPH"
        else f"taskstore://event/{aggregate_type}/{aggregate_id}/{aggregate_version}"
    )
    conn.execute(
        """
        INSERT INTO task_runtime_outbox(
            outbox_id, aggregate_type, aggregate_id, aggregate_version,
            graph_revision, event_type, payload_ref, evidence_refs_json,
            attempt_id, idempotency_key, status, available_ts_ms,
            created_ts_ms, published_ts_ms, status_version
        ) VALUES (?, ?, ?, ?, ?, ?, ?, '[]', NULL, ?, 'PENDING', 1, 1, NULL, 0)
        """,
        (
            outbox_id,
            aggregate_type,
            aggregate_id,
            aggregate_version,
            graph_revision,
            event_type,
            payload_ref,
            f"idempotency:{outbox_id}",
        ),
    )


def _proof_values(
    *,
    proof_id: str,
    case_id: str,
    authority_kind: str,
    action: str,
    target_graph_status: str | None,
    target_budget_status: str | None,
    graph_outbox_id: str | None,
    budget_outbox_id: str | None,
    signed: bool,
) -> tuple[Any, ...]:
    return (
        proof_id,
        PROOF_SCHEMA,
        case_id,
        _sha(case_id),
        authority_kind,
        action,
        target_graph_status,
        target_budget_status,
        "{}",
        _sha(f"subject:{proof_id}"),
        f"actor:{proof_id}",
        f"attestation://{proof_id}" if signed else None,
        "operator-key-v1" if signed else None,
        SIGNATURE if signed else None,
        1,
        "{}",
        _sha(f"proof:{proof_id}"),
        graph_outbox_id,
        budget_outbox_id,
        1,
    )


def _insert_proof(conn: sqlite3.Connection, values: tuple[Any, ...]) -> None:
    conn.execute(
        f"""
        INSERT INTO {PROOF_TABLE}(
            proof_id, schema_version, case_id, case_snapshot_sha256,
            authority_kind, action, target_graph_status, target_budget_status,
            subject_snapshot_json, subject_snapshot_sha256,
            reconciler_actor_id, attestation_ref, attestation_key_id,
            attestation_signature, issued_ts_ms, proof_binding_json,
            proof_digest, graph_outbox_id, budget_outbox_id, created_ts_ms
        ) VALUES ({", ".join("?" for _ in values)})
        """,
        values,
    )


def test_0099_is_schema_only_and_preserves_existing_authority_rows(tmp_path) -> None:
    runner, db_path = _migrated_db(tmp_path, "0099-schema-only.sqlite3")
    with _connect(db_path) as conn:
        conn.execute(
            "DROP TABLE IF EXISTS task_runtime_graph_revision_supersession"
        )
        conn.execute("DELETE FROM schema_migration_assets WHERE migration_id >= 100")
        conn.execute("DELETE FROM schema_migrations WHERE migration_id >= 100")
        for trigger in TRIGGERS:
            conn.execute(f"DROP TRIGGER {trigger}")
        conn.execute(f"DROP TABLE {SUPERSESSION_TABLE}")
        conn.execute(f"DROP TABLE {PROOF_TABLE}")
        conn.execute(f"DROP TABLE {CASE_TABLE}")
        conn.execute("DELETE FROM schema_migration_assets WHERE migration_id = 99")
        conn.execute("DELETE FROM schema_migrations WHERE migration_id = 99")
        _insert_graph(conn, graph_id="graph-active-0099", status="ACTIVE", version=1)
        _insert_graph(conn, graph_id="graph-failed-0099", status="FAILED", version=2)
        _insert_budget(
            conn,
            budget_id="budget-failed-0099",
            graph_id="graph-failed-0099",
            status="ACTIVE",
            version=1,
        )
        authority_sql_before = {
            name: str(
                conn.execute(
                    "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
                    (name,),
                ).fetchone()[0]
            )
            for name in (
                "task_runtime_graph",
                "task_runtime_budget_reservation",
                "task_runtime_parallel_settlement_decision",
                "task_runtime_graph_cancellation_decision",
            )
        }

    applied = runner.apply_migrations(request_id="0099-schema-only-reapply", ts_ms=100)
    assert applied.success is True, applied.as_dict()
    assert applied.summary["current_version"] >= 101
    assert "0101_agent_workspace_authority" in {
        record.migration_id for record in applied.records
    }

    with _connect(db_path) as conn:
        assert tuple(conn.execute(f"SELECT COUNT(*) FROM {CASE_TABLE}").fetchone()) == (0,)
        assert tuple(conn.execute(f"SELECT COUNT(*) FROM {PROOF_TABLE}").fetchone()) == (0,)
        assert tuple(
            conn.execute(f"SELECT COUNT(*) FROM {SUPERSESSION_TABLE}").fetchone()
        ) == (0,)
        assert tuple(conn.execute(
            "SELECT status, status_version FROM task_runtime_graph "
            "WHERE graph_id = 'graph-active-0099' AND revision = 1"
        ).fetchone()) == ("ACTIVE", 1)
        assert tuple(conn.execute(
            "SELECT status, status_version FROM task_runtime_graph "
            "WHERE graph_id = 'graph-failed-0099' AND revision = 1"
        ).fetchone()) == ("FAILED", 2)
        assert tuple(conn.execute(
            "SELECT status, status_version FROM task_runtime_budget_reservation "
            "WHERE budget_id = 'budget-failed-0099'"
        ).fetchone()) == ("ACTIVE", 1)
        authority_sql_after = {
            name: str(
                conn.execute(
                    "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
                    (name,),
                ).fetchone()[0]
            )
            for name in authority_sql_before
        }
        objects = {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'trigger' "
                "AND name LIKE 'trg_task_runtime_terminal_%'"
            ).fetchall()
        }
    assert authority_sql_after == authority_sql_before
    assert objects == TRIGGERS


def test_0099_case_matrix_exact_binding_and_append_only_guards(tmp_path) -> None:
    _, db_path = _migrated_db(tmp_path, "0099-case-contract.sqlite3")
    with _connect(db_path) as conn:
        _insert_graph(conn, graph_id="graph-case", status="FAILED", version=2)
        _insert_budget(
            conn,
            budget_id="budget-case",
            graph_id="graph-case",
            status="EXCEEDED",
            version=3,
        )
        values = _case_values(
            case_id="case-exact",
            graph_id="graph-case",
            graph_status="FAILED",
            graph_version=2,
            case_kind="FAILED_UNSETTLED_BUDGET",
            classification="EXACT_REPAIRABLE",
            recommended_action="RELEASE_BUDGET",
            budget_id="budget-case",
            budget_status="EXCEEDED",
            budget_version=3,
        )
        _insert_case(conn, values)

        with pytest.raises(sqlite3.IntegrityError):
            _insert_case(
                conn,
                _case_values(
                    case_id="case-wrong-version",
                    graph_id="graph-case",
                    graph_status="FAILED",
                    graph_version=99,
                    case_kind="FAILED_UNSETTLED_BUDGET",
                    classification="EXACT_REPAIRABLE",
                    recommended_action="RELEASE_BUDGET",
                    budget_id="budget-case",
                    budget_status="EXCEEDED",
                    budget_version=3,
                    ),
                )

        _insert_graph(
            conn,
            graph_id="graph-blocked-consumed",
            status="BLOCKED",
            version=1,
        )
        _insert_budget(
            conn,
            budget_id="budget-blocked-consumed",
            graph_id="graph-blocked-consumed",
            status="CONSUMED",
            version=2,
        )
        _insert_failed_node_attempt(
            conn,
            graph_id="graph-blocked-consumed",
            node_id="node-blocked-consumed",
            attempt_id="attempt-blocked-consumed",
        )
        with pytest.raises(sqlite3.IntegrityError):
            _insert_case(
                conn,
                _case_values(
                    case_id="case-blocked-consumed",
                    graph_id="graph-blocked-consumed",
                    graph_status="BLOCKED",
                    graph_version=1,
                    case_kind="BLOCKED_FAILURE_NODE",
                    classification="SIGNED_REPAIR_REQUIRED",
                    recommended_action="FAIL_GRAPH",
                    budget_id="budget-blocked-consumed",
                    budget_status="CONSUMED",
                    budget_version=2,
                    node_id="node-blocked-consumed",
                    node_status="FAILED",
                    node_version=1,
                    attempt_id="attempt-blocked-consumed",
                    attempt_status="FAILED",
                    attempt_version=1,
                ),
            )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                f"UPDATE {CASE_TABLE} SET reason_code = 'TAMPER' "
                "WHERE case_id = 'case-exact'"
            )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(f"DELETE FROM {CASE_TABLE} WHERE case_id = 'case-exact'")
        assert tuple(conn.execute(f"SELECT COUNT(*) FROM {CASE_TABLE}").fetchone()) == (1,)


def test_0099_healthy_closure_requires_prior_and_signed_noop_proof(tmp_path) -> None:
    _, db_path = _migrated_db(tmp_path, "0099-healthy-closure.sqlite3")
    with _connect(db_path) as conn:
        _insert_graph(conn, graph_id="graph-healthy", status="FAILED", version=2)
        _insert_budget(
            conn,
            budget_id="budget-healthy",
            graph_id="graph-healthy",
            status="RELEASED",
            version=2,
        )
        closure = _case_values(
            case_id="case-healthy-closure",
            graph_id="graph-healthy",
            graph_status="FAILED",
            graph_version=2,
            case_kind="TERMINAL_HEALTHY_CLOSURE",
            classification="SIGNED_REPAIR_REQUIRED",
            recommended_action="NONE",
            budget_id="budget-healthy",
            budget_status="RELEASED",
            budget_version=2,
        )
        with pytest.raises(sqlite3.IntegrityError):
            _insert_case(conn, closure)

        _insert_case(
            conn,
            _case_values(
                case_id="case-prior-anomaly",
                graph_id="graph-healthy",
                graph_status="FAILED",
                graph_version=2,
                case_kind="TERMINAL_STRUCTURAL_ANOMALY",
                classification="QUARANTINED_STRUCTURAL",
                recommended_action="NONE",
            ),
        )
        _insert_case(conn, closure)

        with pytest.raises(sqlite3.IntegrityError):
            _insert_proof(
                conn,
                _proof_values(
                    proof_id="proof-healthy-system",
                    case_id="case-healthy-closure",
                    authority_kind="SYSTEM_DURABLE_EVIDENCE",
                    action="NONE",
                    target_graph_status=None,
                    target_budget_status=None,
                    graph_outbox_id=None,
                    budget_outbox_id=None,
                    signed=False,
                ),
            )
        _insert_proof(
            conn,
            _proof_values(
                proof_id="proof-healthy-signed",
                case_id="case-healthy-closure",
                authority_kind="SIGNED_OPERATOR",
                action="NONE",
                target_graph_status=None,
                target_budget_status=None,
                graph_outbox_id=None,
                budget_outbox_id=None,
                signed=True,
            ),
        )


def test_0099_proof_authority_action_matrix_and_append_only_guards(tmp_path) -> None:
    _, db_path = _migrated_db(tmp_path, "0099-proof-contract.sqlite3")
    with _connect(db_path) as conn:
        # Exact FAILED case: automatic system proof and signed manual proof are
        # both valid authorities, but one immutable proof wins per case.
        _insert_graph(conn, graph_id="graph-release-system", status="FAILED", version=2)
        _insert_budget(
            conn,
            budget_id="budget-release-system",
            graph_id="graph-release-system",
            status="ACTIVE",
            version=1,
        )
        _insert_case(
            conn,
            _case_values(
                case_id="case-release-system",
                graph_id="graph-release-system",
                graph_status="FAILED",
                graph_version=2,
                case_kind="FAILED_UNSETTLED_BUDGET",
                classification="EXACT_REPAIRABLE",
                recommended_action="RELEASE_BUDGET",
                budget_id="budget-release-system",
                budget_status="ACTIVE",
                budget_version=1,
            ),
        )
        _insert_outbox(
            conn,
            outbox_id="outbox-budget-release-system",
            aggregate_type="BUDGET_RESERVATION",
            aggregate_id="budget-release-system",
            aggregate_version=2,
            event_type="BUDGET_RESERVATION_RELEASED",
        )
        _insert_proof(
            conn,
            _proof_values(
                proof_id="proof-release-system",
                case_id="case-release-system",
                authority_kind="SYSTEM_DURABLE_EVIDENCE",
                action="RELEASE_BUDGET",
                target_graph_status="FAILED",
                target_budget_status="RELEASED",
                graph_outbox_id=None,
                budget_outbox_id="outbox-budget-release-system",
                signed=False,
            ),
        )

        _insert_graph(conn, graph_id="graph-release-signed", status="FAILED", version=2)
        _insert_budget(
            conn,
            budget_id="budget-release-signed",
            graph_id="graph-release-signed",
            status="EXCEEDED",
            version=3,
        )
        _insert_case(
            conn,
            _case_values(
                case_id="case-release-signed",
                graph_id="graph-release-signed",
                graph_status="FAILED",
                graph_version=2,
                case_kind="FAILED_UNSETTLED_BUDGET",
                classification="EXACT_REPAIRABLE",
                recommended_action="RELEASE_BUDGET",
                budget_id="budget-release-signed",
                budget_status="EXCEEDED",
                budget_version=3,
            ),
        )
        _insert_outbox(
            conn,
            outbox_id="outbox-budget-release-signed",
            aggregate_type="BUDGET_RESERVATION",
            aggregate_id="budget-release-signed",
            aggregate_version=4,
            event_type="BUDGET_RESERVATION_RELEASED",
        )
        signed_values = _proof_values(
            proof_id="proof-release-signed",
            case_id="case-release-signed",
            authority_kind="SIGNED_OPERATOR",
            action="RELEASE_BUDGET",
            target_graph_status="FAILED",
            target_budget_status="RELEASED",
            graph_outbox_id=None,
            budget_outbox_id="outbox-budget-release-signed",
            signed=True,
        )
        malformed = list(signed_values)
        malformed[13] = "ed25519:not-valid"
        with pytest.raises(sqlite3.IntegrityError):
            _insert_proof(conn, tuple(malformed))
        _insert_proof(conn, signed_values)

        # Signed BLOCKED failure may fail Graph only when no unsettled Budget.
        _insert_graph(conn, graph_id="graph-fail", status="BLOCKED", version=4)
        _insert_failed_node_attempt(
            conn,
            graph_id="graph-fail",
            node_id="node-fail",
            attempt_id="attempt-fail",
        )
        _insert_case(
            conn,
            _case_values(
                case_id="case-fail",
                graph_id="graph-fail",
                graph_status="BLOCKED",
                graph_version=4,
                case_kind="BLOCKED_FAILURE_NODE",
                classification="SIGNED_REPAIR_REQUIRED",
                recommended_action="FAIL_GRAPH",
                node_id="node-fail",
                node_status="FAILED",
                node_version=1,
                attempt_id="attempt-fail",
                attempt_status="FAILED",
                attempt_version=1,
            ),
        )
        _insert_outbox(
            conn,
            outbox_id="outbox-graph-fail",
            aggregate_type="TASK_GRAPH",
            aggregate_id="graph-fail",
            aggregate_version=5,
            graph_revision=1,
            event_type="TASK_GRAPH_FAILED",
        )
        with pytest.raises(sqlite3.IntegrityError):
            _insert_proof(
                conn,
                _proof_values(
                    proof_id="proof-fail-system-invalid",
                    case_id="case-fail",
                    authority_kind="SYSTEM_DURABLE_EVIDENCE",
                    action="FAIL_GRAPH",
                    target_graph_status="FAILED",
                    target_budget_status=None,
                    graph_outbox_id="outbox-graph-fail",
                    budget_outbox_id=None,
                    signed=False,
                ),
            )
        _insert_proof(
            conn,
            _proof_values(
                proof_id="proof-fail",
                case_id="case-fail",
                authority_kind="SIGNED_OPERATOR",
                action="FAIL_GRAPH",
                target_graph_status="FAILED",
                target_budget_status=None,
                graph_outbox_id="outbox-graph-fail",
                budget_outbox_id=None,
                signed=True,
            ),
        )

        # An unsettled BLOCKED budget requires the combined mutation and both
        # exact Outboxes; it cannot be silently dropped by FAIL_GRAPH alone.
        _insert_graph(conn, graph_id="graph-both", status="BLOCKED", version=2)
        _insert_budget(
            conn,
            budget_id="budget-both",
            graph_id="graph-both",
            status="EXCEEDED",
            version=3,
        )
        _insert_failed_node_attempt(
            conn,
            graph_id="graph-both",
            node_id="node-both",
            attempt_id="attempt-both",
        )
        _insert_case(
            conn,
            _case_values(
                case_id="case-both",
                graph_id="graph-both",
                graph_status="BLOCKED",
                graph_version=2,
                case_kind="BLOCKED_FAILURE_NODE",
                classification="SIGNED_REPAIR_REQUIRED",
                recommended_action="FAIL_GRAPH_AND_RELEASE_BUDGET",
                budget_id="budget-both",
                budget_status="EXCEEDED",
                budget_version=3,
                node_id="node-both",
                node_status="FAILED",
                node_version=1,
                attempt_id="attempt-both",
                attempt_status="FAILED",
                attempt_version=1,
            ),
        )
        _insert_outbox(
            conn,
            outbox_id="outbox-graph-both",
            aggregate_type="TASK_GRAPH",
            aggregate_id="graph-both",
            aggregate_version=3,
            graph_revision=1,
            event_type="TASK_GRAPH_FAILED",
        )
        _insert_outbox(
            conn,
            outbox_id="outbox-budget-both",
            aggregate_type="BUDGET_RESERVATION",
            aggregate_id="budget-both",
            aggregate_version=4,
            event_type="BUDGET_RESERVATION_RELEASED",
        )
        _insert_proof(
            conn,
            _proof_values(
                proof_id="proof-both",
                case_id="case-both",
                authority_kind="SIGNED_OPERATOR",
                action="FAIL_GRAPH_AND_RELEASE_BUDGET",
                target_graph_status="FAILED",
                target_budget_status="RELEASED",
                graph_outbox_id="outbox-graph-both",
                budget_outbox_id="outbox-budget-both",
                signed=True,
            ),
        )

        # A CANCELLED terminal is preserved while only its unsettled Budget is
        # released; this path always requires signed operator authority.
        _insert_graph(conn, graph_id="graph-cancelled", status="CANCELLED", version=5)
        _insert_budget(
            conn,
            budget_id="budget-cancelled",
            graph_id="graph-cancelled",
            status="EXCEEDED",
            version=6,
        )
        _insert_case(
            conn,
            _case_values(
                case_id="case-cancelled",
                graph_id="graph-cancelled",
                graph_status="CANCELLED",
                graph_version=5,
                case_kind="CANCELLED_UNSETTLED_BUDGET",
                classification="SIGNED_REPAIR_REQUIRED",
                recommended_action="RELEASE_BUDGET",
                budget_id="budget-cancelled",
                budget_status="EXCEEDED",
                budget_version=6,
            ),
        )
        _insert_outbox(
            conn,
            outbox_id="outbox-budget-cancelled",
            aggregate_type="BUDGET_RESERVATION",
            aggregate_id="budget-cancelled",
            aggregate_version=7,
            event_type="BUDGET_RESERVATION_RELEASED",
        )
        _insert_proof(
            conn,
            _proof_values(
                proof_id="proof-cancelled",
                case_id="case-cancelled",
                authority_kind="SIGNED_OPERATOR",
                action="RELEASE_BUDGET",
                target_graph_status="CANCELLED",
                target_budget_status="RELEASED",
                graph_outbox_id=None,
                budget_outbox_id="outbox-budget-cancelled",
                signed=True,
            ),
        )

        # Structural quarantine is immutable evidence and can never be proved
        # directly, even with a no-op authority shape.
        _insert_graph(conn, graph_id="graph-none", status="COMPLETED", version=7)
        _insert_case(
            conn,
            _case_values(
                case_id="case-none",
                graph_id="graph-none",
                graph_status="COMPLETED",
                graph_version=7,
                case_kind="COMPLETED_INCONSISTENT",
                classification="QUARANTINED_STRUCTURAL",
                recommended_action="NONE",
            ),
        )
        with pytest.raises(sqlite3.IntegrityError):
            _insert_proof(
                conn,
                _proof_values(
                    proof_id="proof-none",
                    case_id="case-none",
                    authority_kind="SYSTEM_DURABLE_EVIDENCE",
                    action="NONE",
                    target_graph_status=None,
                    target_budget_status=None,
                    graph_outbox_id=None,
                    budget_outbox_id=None,
                    signed=False,
                ),
            )

        _insert_graph(conn, graph_id="graph-anomaly", status="FAILED", version=9)
        _insert_case(
            conn,
            _case_values(
                case_id="case-anomaly",
                graph_id="graph-anomaly",
                graph_status="FAILED",
                graph_version=9,
                case_kind="TERMINAL_STRUCTURAL_ANOMALY",
                classification="QUARANTINED_STRUCTURAL",
                recommended_action="NONE",
            ),
        )

        mismatch = list(
            _proof_values(
                proof_id="proof-mismatch",
                case_id="case-none",
                authority_kind="SYSTEM_DURABLE_EVIDENCE",
                action="RELEASE_BUDGET",
                target_graph_status="FAILED",
                target_budget_status="RELEASED",
                graph_outbox_id=None,
                budget_outbox_id="outbox-budget-release-system",
                signed=False,
            )
        )
        with pytest.raises(sqlite3.IntegrityError):
            _insert_proof(conn, tuple(mismatch))

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                f"UPDATE {PROOF_TABLE} SET reconciler_actor_id = 'tamper' "
                "WHERE proof_id = 'proof-release-system'"
            )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                f"DELETE FROM {PROOF_TABLE} WHERE proof_id = 'proof-release-system'"
            )
        assert tuple(conn.execute(f"SELECT COUNT(*) FROM {PROOF_TABLE}").fetchone()) == (5,)
