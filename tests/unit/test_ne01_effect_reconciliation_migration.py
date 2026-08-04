from __future__ import annotations

import hashlib
from pathlib import Path
import sqlite3

import pytest

from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.persistence.task_store_types import (
    TASK_STORE_DURABLE_DATA_INVALID,
    TaskStoreError,
)
from tests.contract.test_ne01_graph_cancellation_store import (
    NOW,
    _request_snapshot,
    _seed_legacy_retry_pending_effect,
    _stage_effect,
    _store,
)
from tests.support.migration_catalog import migration_catalog_through


_0098_TRIGGERS = (
    "trg_task_effect_reconciliation_case_no_update",
    "trg_task_effect_reconciliation_case_no_delete",
    "trg_task_effect_reconciliation_proof_no_update",
    "trg_task_effect_reconciliation_proof_no_delete",
    "trg_task_effect_failed_evidence_forbidden_insert",
    "trg_task_effect_failed_evidence_forbidden_update",
)


def _remove_0098_physical_receipt(store) -> None:
    """Restore the exact pre-0098 surface while retaining the 0097 fixtures."""

    with sqlite3.connect(store.db_path) as conn:
        conn.execute(
            "DROP TABLE IF EXISTS task_runtime_graph_revision_supersession"
        )
        conn.execute("DELETE FROM schema_migration_assets WHERE migration_id >= 100")
        conn.execute("DELETE FROM schema_migrations WHERE migration_id >= 100")
        conn.execute("DROP TABLE IF EXISTS task_runtime_terminal_reconciliation_proof")
        conn.execute("DROP TABLE IF EXISTS task_runtime_terminal_reconciliation_case")
        conn.execute("DELETE FROM schema_migration_assets WHERE migration_id = 99")
        conn.execute("DELETE FROM schema_migrations WHERE migration_id = 99")
        for trigger in _0098_TRIGGERS:
            conn.execute(f"DROP TRIGGER IF EXISTS {trigger}")
        conn.execute("DROP TABLE IF EXISTS task_runtime_effect_reconciliation_proof")
        conn.execute("DROP TABLE IF EXISTS task_runtime_effect_reconciliation_case")
        conn.execute(
            "DROP INDEX IF EXISTS uq_task_runtime_cancellation_effect_fence_binding"
        )
        conn.execute("DELETE FROM schema_migration_assets WHERE migration_id = 98")
        conn.execute("DELETE FROM schema_migrations WHERE migration_id = 98")
        conn.commit()
    store._effect_reconciliation_schema_ready = False
    store._terminal_reconciliation_schema_ready = False


def _apply_0098(store, *, request_id: str) -> None:
    result = SQLiteMigrationRunner(
        db_path=store.db_path,
        migrations_dir=migration_catalog_through(Path(store.db_path).parent, 102),
    ).apply_migrations(
        request_id=request_id,
        ts_ms=NOW,
    )
    assert result.success, result.as_dict()
    assert result.summary["current_version"] >= 102
    store._effect_reconciliation_schema_ready = False
    store.assert_effect_reconciliation_ready(force_revalidate=True)


def _make_failed_effect(store, *, effect_id: str) -> None:
    _stage_effect(store, effect_id=effect_id, accepted=False)
    with sqlite3.connect(store.db_path) as conn:
        conn.execute(
            """
            UPDATE task_runtime_acceptance_effect_intent
            SET status = 'FAILED', status_version = 2,
                lease_owner = 'legacy-dispatcher', lease_expires_ts_ms = NULL,
                fencing_token = 1, attempt_count = 1,
                reason_code = 'ACCEPTANCE_EFFECT_HANDLER_FAILED',
                last_error = 'legacy downstream outcome is uncertain',
                failed_ts_ms = ?, updated_ts_ms = ?
            WHERE effect_id = ?
            """,
            (NOW, NOW, effect_id),
        )
        conn.commit()


def _set_failed_evidence(store, *, effect_id: str) -> None:
    with sqlite3.connect(store.db_path) as conn:
        conn.execute(
            """
            UPDATE task_runtime_graph_cancellation_effect_fence
            SET observed_status = 'FAILED', observed_status_version = 2,
                resolution_status = 'FAILED_EVIDENCE', resolved_ts_ms = ?
            WHERE effect_id = ?
            """,
            (NOW, effect_id),
        )
        conn.commit()


def _finalize_intent_without_runtime_mutation(
    store,
    *,
    intent_ref: str,
    include_decision: bool,
) -> None:
    with sqlite3.connect(store.db_path) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        intent = conn.execute(
            "SELECT * FROM task_runtime_graph_cancellation_intent WHERE intent_ref = ?",
            (intent_ref,),
        ).fetchone()
        assert intent is not None
        conn.execute(
            """
            UPDATE task_runtime_graph_cancellation_intent
            SET status = 'FINALIZED', status_version = 1, finalized_ts_ms = ?
            WHERE intent_ref = ?
            """,
            (NOW, intent_ref),
        )
        if include_decision:
            payload_json = "{}"
            payload_sha256 = "sha256:" + hashlib.sha256(
                payload_json.encode("utf-8")
            ).hexdigest()
            conn.execute(
                """
                INSERT INTO task_runtime_graph_cancellation_decision(
                    decision_ref, schema_version, intent_ref, graph_id,
                    graph_revision, graph_version_before, graph_version_after,
                    budget_id, budget_version_before, budget_version_after,
                    payload_json, payload_sha256, graph_outbox_id,
                    budget_outbox_id, created_ts_ms
                ) VALUES (?, 'test.graph-cancellation-decision.v1', ?, ?, ?,
                          2, 3, ?, ?, ?, ?, ?, ?, NULL, ?)
                """,
                (
                    f"cancellation-decision://legacy/{intent_ref.rsplit('/', 1)[-1]}",
                    intent_ref,
                    intent["graph_id"],
                    intent["graph_revision"],
                    intent["budget_id"],
                    intent["budget_version_at_request"],
                    intent["budget_version_at_request"],
                    payload_json,
                    payload_sha256,
                    intent["graph_outbox_id"],
                    NOW,
                ),
            )
        conn.commit()


def test_0098_inventories_requested_failed_evidence_and_replays_receipt_loss(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    effect_id = "effect://migration/legacy-failed-evidence"
    _make_failed_effect(store, effect_id=effect_id)
    requested = _request_snapshot(
        store,
        before_status="READY",
        before_version=0,
        after_status="CANCEL_REQUESTED",
        after_version=1,
    )
    _remove_0098_physical_receipt(store)
    _set_failed_evidence(store, effect_id=effect_id)

    _apply_0098(store, request_id="0098-legacy-failed")
    intent_ref = requested["intent"]["intent_ref"]
    aggregate = store.read_graph_cancellation_effect_reconciliation(
        intent_ref,
        effect_id,
    )
    case = aggregate["case"]
    assert case["case_kind"] == "LEGACY_FAILED_EVIDENCE"
    assert case["classification"] == "REPAIRABLE_REQUESTED"
    assert case["purpose"] == "PENDING_CANCELLATION"
    assert case["snapshot"]["effect_last_error_sha256"].startswith("sha256:")
    assert "legacy downstream outcome is uncertain" not in str(case)

    candidates = store.list_effect_reconciliation_candidates(intent_ref)
    assert len(candidates) == 1
    assert candidates[0]["case"]["case_id"] == case["case_id"]
    assert candidates[0]["proof_exists"] is False
    with pytest.raises(TaskStoreError) as blocked:
        store.assert_effect_reconciliation_production_ready()
    assert blocked.value.reason_code == (
        "TASK_STORE_RECONCILIATION_AUTHORITY_REQUIRED"
    )

    # Simulate SQL commit succeeding while the runner receipt was lost.  The
    # immutable inventory INSERT OR IGNORE must make exact replay idempotent.
    with sqlite3.connect(store.db_path) as conn:
        conn.execute(
            "DROP TABLE IF EXISTS task_runtime_graph_revision_supersession"
        )
        conn.execute("DELETE FROM schema_migration_assets WHERE migration_id >= 100")
        conn.execute("DELETE FROM schema_migrations WHERE migration_id >= 100")
        conn.execute("DELETE FROM schema_migration_assets WHERE migration_id = 99")
        conn.execute("DELETE FROM schema_migrations WHERE migration_id = 99")
        conn.execute("DELETE FROM schema_migration_assets WHERE migration_id = 98")
        conn.execute("DELETE FROM schema_migrations WHERE migration_id = 98")
        conn.commit()
    _apply_0098(store, request_id="0098-receipt-loss-replay")
    assert store.list_effect_reconciliation_cases(limit=10) == (case,)


def test_0098_inventories_retry_pending_cancelled_and_freezes_legacy_rows(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    effect_id = "effect://migration/legacy-retry-pending"
    _stage_effect(store, effect_id=effect_id, accepted=False)
    _seed_legacy_retry_pending_effect(store, effect_id=effect_id)
    requested = _request_snapshot(
        store,
        before_status="READY",
        before_version=0,
        after_status="CANCEL_REQUESTED",
        after_version=1,
    )
    _remove_0098_physical_receipt(store)
    with sqlite3.connect(store.db_path) as conn:
        conn.execute(
            """
            UPDATE task_runtime_graph_cancellation_effect_fence
            SET resolution_status = 'CANCELLED', resolved_ts_ms = ?
            WHERE effect_id = ?
            """,
            (NOW, effect_id),
        )
        conn.commit()

    _apply_0098(store, request_id="0098-legacy-retry")
    case = store.read_graph_cancellation_effect_reconciliation(
        requested["intent"]["intent_ref"],
        effect_id,
    )["case"]
    assert case["case_kind"] == "LEGACY_RETRY_PENDING_CANCELLED"
    assert case["classification"] == "REPAIRABLE_REQUESTED"

    with sqlite3.connect(store.db_path) as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "UPDATE task_runtime_effect_reconciliation_case SET reason_code = 'tamper'"
            )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("DELETE FROM task_runtime_effect_reconciliation_case")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                UPDATE task_runtime_graph_cancellation_effect_fence
                SET resolution_status = 'RECONCILIATION_REQUIRED'
                WHERE effect_id = ?
                """,
                (effect_id,),
            )


@pytest.mark.parametrize(
    ("include_decision", "classification"),
    (
        (True, "QUARANTINED_FINALIZED"),
        (False, "QUARANTINED_STRUCTURAL"),
    ),
)
def test_0098_finalized_inventory_binds_decision_or_quarantines_structure(
    tmp_path: Path,
    include_decision: bool,
    classification: str,
) -> None:
    store = _store(tmp_path)
    effect_id = f"effect://migration/finalized-{include_decision}"
    _make_failed_effect(store, effect_id=effect_id)
    requested = _request_snapshot(
        store,
        before_status="READY",
        before_version=0,
        after_status="CANCEL_REQUESTED",
        after_version=1,
    )
    _remove_0098_physical_receipt(store)
    _set_failed_evidence(store, effect_id=effect_id)
    intent_ref = requested["intent"]["intent_ref"]
    _finalize_intent_without_runtime_mutation(
        store,
        intent_ref=intent_ref,
        include_decision=include_decision,
    )

    _apply_0098(store, request_id=f"0098-finalized-{include_decision}")
    case = store.list_effect_reconciliation_cases(limit=10)[0]
    assert case["classification"] == classification
    assert case["purpose"] == "LEGACY_FINALIZED_CORRECTION"
    if include_decision:
        assert case["legacy_decision_ref"] is not None
        assert case["legacy_decision_payload_sha256"].startswith("sha256:")
        with pytest.raises(TaskStoreError) as blocked:
            store.assert_effect_reconciliation_production_ready()
        assert blocked.value.reason_code == (
            "TASK_STORE_RECONCILIATION_AUTHORITY_REQUIRED"
        )
        with sqlite3.connect(store.db_path) as conn:
            conn.execute(
                """
                UPDATE task_runtime_graph_cancellation_decision
                SET payload_sha256 = ? WHERE intent_ref = ?
                """,
                (f"sha256:{'b' * 64}", intent_ref),
            )
            conn.commit()
        with pytest.raises(TaskStoreError) as drifted:
            store.assert_effect_reconciliation_production_ready()
        assert drifted.value.reason_code == TASK_STORE_DURABLE_DATA_INVALID
    else:
        assert case["legacy_decision_ref"] is None
        with pytest.raises(TaskStoreError) as blocked:
            store.assert_effect_reconciliation_production_ready()
        assert blocked.value.reason_code == TASK_STORE_DURABLE_DATA_INVALID


def test_0098_forward_failed_evidence_insert_and_update_are_forbidden(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    with sqlite3.connect(store.db_path) as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO task_runtime_graph_cancellation_effect_fence(
                    effect_id, intent_ref, observed_status, resolution_status,
                    observed_status_version, created_ts_ms, resolved_ts_ms
                ) VALUES ('effect://future/forbidden', 'intent://future/forbidden',
                          'FAILED', 'FAILED_EVIDENCE', 2, ?, ?)
                """,
                (NOW, NOW),
            )

    effect_id = "effect://migration/forward-update"
    _make_failed_effect(store, effect_id=effect_id)
    _request_snapshot(
        store,
        before_status="READY",
        before_version=0,
        after_status="CANCEL_REQUESTED",
        after_version=1,
    )
    with sqlite3.connect(store.db_path) as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                UPDATE task_runtime_graph_cancellation_effect_fence
                SET resolution_status = 'FAILED_EVIDENCE', resolved_ts_ms = ?
                WHERE effect_id = ?
                """,
                (NOW, effect_id),
            )


def test_production_gate_ignores_normal_virtual_reconciliation_candidate(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    effect_id = "effect://migration/normal-reconciliation"
    _make_failed_effect(store, effect_id=effect_id)
    requested = _request_snapshot(
        store,
        before_status="READY",
        before_version=0,
        after_status="CANCEL_REQUESTED",
        after_version=1,
    )

    # This is a normal runtime RECONCILIATION_REQUIRED fence and therefore a
    # virtual signing candidate, not legacy inventory that may block startup.
    store.assert_effect_reconciliation_production_ready()
    candidates = store.list_effect_reconciliation_candidates(
        requested["intent"]["intent_ref"]
    )
    assert len(candidates) == 1
    assert candidates[0]["case"]["case_kind"] == "RECONCILIATION_REQUIRED"
