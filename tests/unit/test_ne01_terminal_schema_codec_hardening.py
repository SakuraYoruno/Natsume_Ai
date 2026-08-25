from __future__ import annotations

from copy import deepcopy
import sqlite3

import pytest

from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.contracts.agent_runtime.terminal_reconciliation import (
    TERMINAL_RECONCILIATION_CASE_SCHEMA_VERSION,
)
from host_brain.persistence.task_repository import DurableTaskStore
from host_brain.persistence.task_store_types import (
    TASK_STORE_DURABLE_DATA_INVALID,
    TASK_STORE_SCHEMA_NOT_READY,
    TaskStoreError,
)
from host_brain.persistence.task_terminal_reconciliation_codec_validation import (
    terminal_proof_matrix_valid,
    terminal_proof_subject_valid,
)
from host_brain.persistence.task_terminal_reconciliation_common import (
    _canonical_digest,
    _terminal_case_id,
    _terminal_reconciliation_case_record,
)


def _migrated_store(tmp_path, name: str) -> DurableTaskStore:
    db_path = tmp_path / f"{name}.sqlite3"
    result = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id=f"schema-codec:{name}",
        ts_ms=99,
    )
    assert result.success is True, result.as_dict()
    return DurableTaskStore(db_path)


def _case_row() -> dict[str, object]:
    graph = {
        "graph_id": "graph-codec",
        "revision": 1,
        "status": "FAILED",
        "status_version": 2,
    }
    budget = {
        "budget_id": "budget-codec",
        "status": "ACTIVE",
        "status_version": 1,
        "node_id": None,
        "attempt_id": None,
        "updated_ts_ms": 10,
    }
    observation = {
        "graph": graph,
        "budgets": [budget],
        "nodes": [],
        "attempts": [],
        "leases": [],
        "operations": [],
        "reviews": [],
        "effects": [],
        "parallel_decisions": [],
        "success_decisions": [],
        "cancellations": [],
    }
    snapshot = {
        "schema_version": TERMINAL_RECONCILIATION_CASE_SCHEMA_VERSION,
        "case_kind": "FAILED_UNSETTLED_BUDGET",
        "classification": "EXACT_REPAIRABLE",
        "reason_code": "FAILED_GRAPH_BUDGET_UNSETTLED",
        "recommended_action": "RELEASE_BUDGET",
        "graph": graph,
        "selected_budget": budget,
        "selected_node": None,
        "selected_attempt": None,
        "failure_bundle": None,
        "observation": observation,
    }
    snapshot_json, snapshot_sha256 = _canonical_digest(snapshot)
    return {
        "case_id": _terminal_case_id(
            graph_id="graph-codec",
            graph_revision=1,
            case_kind="FAILED_UNSETTLED_BUDGET",
            snapshot_sha256=snapshot_sha256,
        ),
        "schema_version": TERMINAL_RECONCILIATION_CASE_SCHEMA_VERSION,
        "case_kind": "FAILED_UNSETTLED_BUDGET",
        "classification": "EXACT_REPAIRABLE",
        "reason_code": "FAILED_GRAPH_BUDGET_UNSETTLED",
        "graph_id": "graph-codec",
        "graph_revision": 1,
        "graph_status": "FAILED",
        "graph_status_version": 2,
        "budget_id": "budget-codec",
        "budget_status": "ACTIVE",
        "budget_status_version": 1,
        "node_id": None,
        "node_status": None,
        "node_status_version": None,
        "attempt_id": None,
        "attempt_status": None,
        "attempt_status_version": None,
        "recommended_action": "RELEASE_BUDGET",
        "snapshot_json": snapshot_json,
        "snapshot_sha256": snapshot_sha256,
        "created_ts_ms": 10,
    }


@pytest.mark.parametrize("drift", ["extra_field", "selected_not_observed", "schema"])
def test_case_codec_rejects_semantic_snapshot_drift(drift: str) -> None:
    row = _case_row()
    baseline = _terminal_reconciliation_case_record(row)
    snapshot = deepcopy(baseline["snapshot"])
    if drift == "extra_field":
        snapshot["pollution"] = True
    elif drift == "selected_not_observed":
        snapshot["selected_budget"]["updated_ts_ms"] = 999
    else:
        snapshot["schema_version"] = "Natsume.task_runtime.terminal_reconciliation.case.v0"
    snapshot_json, snapshot_sha256 = _canonical_digest(snapshot)
    row["snapshot_json"] = snapshot_json
    row["snapshot_sha256"] = snapshot_sha256
    row["case_id"] = _terminal_case_id(
        graph_id=str(row["graph_id"]),
        graph_revision=int(row["graph_revision"]),
        case_kind=str(row["case_kind"]),
        snapshot_sha256=snapshot_sha256,
    )

    with pytest.raises(TaskStoreError) as rejected:
        _terminal_reconciliation_case_record(row)
    assert rejected.value.reason_code == TASK_STORE_DURABLE_DATA_INVALID


def test_subject_codec_requires_exact_schema_case_and_observation() -> None:
    case = _terminal_reconciliation_case_record(_case_row())
    subject = {
        "schema_version": "Natsume.task_runtime.terminal_reconciliation.subject.v1",
        "case_id": case["case_id"],
        "observation": case["snapshot"]["observation"],
    }
    assert terminal_proof_subject_valid(subject, case=case) is True
    for drifted in (
        subject | {"extra": True},
        subject | {"schema_version": "subject.v0"},
        subject | {"case_id": "different-case"},
        subject | {"observation": {"graph": {}}},
    ):
        assert terminal_proof_subject_valid(drifted, case=case) is False


@pytest.mark.parametrize(
    ("classification", "case_kind", "authority", "action", "targets", "outboxes", "valid"),
    [
        (
            "EXACT_REPAIRABLE",
            "FAILED_UNSETTLED_BUDGET",
            "SYSTEM_DURABLE_EVIDENCE",
            "RELEASE_BUDGET",
            ("FAILED", "RELEASED"),
            (None, "budget-outbox"),
            True,
        ),
        (
            "EXACT_REPAIRABLE",
            "FAILED_UNSETTLED_BUDGET",
            "SIGNED_OPERATOR",
            "RELEASE_BUDGET",
            ("FAILED", "RELEASED"),
            (None, "budget-outbox"),
            True,
        ),
        (
            "SIGNED_REPAIR_REQUIRED",
            "CANCELLED_UNSETTLED_BUDGET",
            "SYSTEM_DURABLE_EVIDENCE",
            "RELEASE_BUDGET",
            ("CANCELLED", "RELEASED"),
            (None, "budget-outbox"),
            False,
        ),
        (
            "SIGNED_REPAIR_REQUIRED",
            "CANCELLED_UNSETTLED_BUDGET",
            "SIGNED_OPERATOR",
            "RELEASE_BUDGET",
            ("CANCELLED", "RELEASED"),
            (None, "budget-outbox"),
            True,
        ),
        (
            "QUARANTINED_STRUCTURAL",
            "TERMINAL_STRUCTURAL_ANOMALY",
            "SIGNED_OPERATOR",
            "NONE",
            (None, None),
            (None, None),
            False,
        ),
        (
            "SIGNED_REPAIR_REQUIRED",
            "TERMINAL_HEALTHY_CLOSURE",
            "SIGNED_OPERATOR",
            "NONE",
            (None, None),
            (None, None),
            True,
        ),
        (
            "SIGNED_REPAIR_REQUIRED",
            "TERMINAL_HEALTHY_CLOSURE",
            "SYSTEM_DURABLE_EVIDENCE",
            "NONE",
            (None, None),
            (None, None),
            False,
        ),
        (
            "EXACT_REPAIRABLE",
            "FAILED_UNSETTLED_BUDGET",
            "SYSTEM_DURABLE_EVIDENCE",
            "RELEASE_BUDGET",
            ("FAILED", "RELEASED"),
            ("graph-outbox", None),
            False,
        ),
        (
            "EXACT_REPAIRABLE",
            "FAILED_UNSETTLED_BUDGET",
            "SYSTEM_DURABLE_EVIDENCE",
            "RELEASE_BUDGET",
            ("CANCELLED", "RELEASED"),
            (None, "budget-outbox"),
            False,
        ),
    ],
)
def test_python_proof_matrix_cannot_be_bypassed_by_sql_trigger_drift(
    classification: str,
    case_kind: str,
    authority: str,
    action: str,
    targets: tuple[str | None, str | None],
    outboxes: tuple[str | None, str | None],
    valid: bool,
) -> None:
    case = {
        "classification": classification,
        "case_kind": case_kind,
        "recommended_action": action,
        "graph_status": "CANCELLED" if "CANCELLED" in case_kind else "FAILED",
    }
    proof = {
        "authority_kind": authority,
        "action": action,
        "target_graph_status": targets[0],
        "target_budget_status": targets[1],
        "graph_outbox_id": outboxes[0],
        "budget_outbox_id": outboxes[1],
    }
    assert terminal_proof_matrix_valid(proof, case=case) is valid


def test_readiness_rejects_same_name_noop_trigger(tmp_path) -> None:
    store = _migrated_store(tmp_path, "noop-trigger")
    trigger = "trg_task_runtime_terminal_case_binding_insert"
    with sqlite3.connect(store.db_path) as conn:
        conn.execute(f"DROP TRIGGER {trigger}")
        conn.execute(
            f"CREATE TRIGGER {trigger} BEFORE INSERT ON "
            "task_runtime_terminal_reconciliation_case BEGIN SELECT 1; END"
        )
    with pytest.raises(TaskStoreError) as rejected:
        store.assert_terminal_reconciliation_ready(force_revalidate=True)
    assert rejected.value.reason_code == TASK_STORE_SCHEMA_NOT_READY


def test_readiness_rejects_same_name_wrong_index(tmp_path) -> None:
    store = _migrated_store(tmp_path, "wrong-index")
    index = "idx_task_runtime_terminal_case_graph"
    with sqlite3.connect(store.db_path) as conn:
        conn.execute(f"DROP INDEX {index}")
        conn.execute(
            f"CREATE INDEX {index} ON "
            "task_runtime_terminal_reconciliation_case(reason_code)"
        )
    with pytest.raises(TaskStoreError) as rejected:
        store.assert_terminal_reconciliation_ready(force_revalidate=True)
    assert rejected.value.reason_code == TASK_STORE_SCHEMA_NOT_READY


@pytest.mark.parametrize("drift", ["check", "foreign_key"])
def test_readiness_rejects_table_constraint_drift(tmp_path, drift: str) -> None:
    store = _migrated_store(tmp_path, f"table-{drift}")
    table = (
        "task_runtime_terminal_reconciliation_case"
        if drift == "check"
        else "task_runtime_terminal_reconciliation_supersession"
    )
    with sqlite3.connect(store.db_path) as conn:
        original = str(
            conn.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
                (table,),
            ).fetchone()[0]
        )
        if drift == "check":
            changed = original.replace(
                "CHECK(length(trim(case_id)) BETWEEN 1 AND 512)",
                "CHECK(length(trim(case_id)) >= 0)",
                1,
            )
        else:
            changed = original.replace(
                "REFERENCES task_runtime_terminal_reconciliation_case(case_id)",
                "REFERENCES task_runtime_graph(graph_id)",
                1,
            )
        assert changed != original
        version = int(conn.execute("PRAGMA schema_version").fetchone()[0])
        conn.execute("PRAGMA writable_schema = ON")
        conn.execute(
            "UPDATE sqlite_master SET sql = ? WHERE type = 'table' AND name = ?",
            (changed, table),
        )
        conn.execute("PRAGMA writable_schema = OFF")
        conn.execute(f"PRAGMA schema_version = {version + 1}")
    with pytest.raises(TaskStoreError) as rejected:
        store.assert_terminal_reconciliation_ready(force_revalidate=True)
    assert rejected.value.reason_code == TASK_STORE_SCHEMA_NOT_READY
