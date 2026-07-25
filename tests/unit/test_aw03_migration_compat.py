from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
from types import SimpleNamespace

import pytest

import host_brain.audit.migration_runner as migration_runner_module
from host_brain.audit.migration_compat import (
    PLATFORM_REGISTRY_DRAFT_CHECKSUM,
    PLATFORM_REGISTRY_FINAL_CHECKSUM,
    checksum_bound_schema_replay_postcondition_holds,
    is_exact_checksum_bound_schema_replay_failure,
    is_exact_legacy_inline_schema_failure,
    legacy_inline_schema_postcondition_holds,
)
from host_brain.core.errors import MIGRATION_SCHEMA_DRIFT
from host_brain.audit.migration_guard import SQLiteMigrationVersionGuard
from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.persistence.task_graph_admission_authority import (
    canonical_task_graph_admission_scope,
    register_task_graph_admission_authority,
)


def _asset(filename: str, checksum: str):
    migration, name = filename.removesuffix(".sql").split("_", 1)
    return SimpleNamespace(
        migration_id=int(migration),
        name=name,
        path=Path(filename),
        checksum=checksum,
    )


def _migration_catalog_through(tmp_path: Path, version: int) -> Path:
    source = Path(migration_runner_module.__file__).with_name("migrations")
    target = tmp_path / f"migrations-through-{version:04d}"
    target.mkdir()
    for path in source.glob("*.sql"):
        migration = int(path.name.split("_", 1)[0])
        if migration <= version:
            shutil.copy2(path, target / path.name)
    return target


def _drop_post_0122_task_graph_admission_triggers(
    conn: sqlite3.Connection,
) -> None:
    """Reconstruct a genuine pre-0123 schema after a full-head test seed."""

    for trigger in (
        "trg_task_runtime_graph_admission_authority_insert",
        "trg_task_runtime_node_admission_authority_insert",
        "trg_host_command_journal_admission_authority_insert",
    ):
        conn.execute(f"DROP TRIGGER IF EXISTS {trigger}")


def _insert_task_graph(
    conn: sqlite3.Connection,
    *,
    graph_id: str,
    revision: int,
) -> None:
    conn.execute(
        """
        INSERT INTO task_runtime_graph(
            graph_id, revision, root_work_order_id, planner_ref,
            status, status_version, graph_json, source_kind,
            created_ts_ms, updated_ts_ms
        ) VALUES (?, ?, ?, ?, 'ACTIVE', 1, '{}', 'CANONICAL', 1, 1)
        """,
        (
            graph_id,
            revision,
            f"work-order-{graph_id}-{revision}",
            f"planner://{graph_id}/{revision}",
        ),
    )


def _insert_host_command(
    conn: sqlite3.Connection,
    *,
    command_id: str,
    graph_id: str,
    graph_revision: int,
) -> None:
    digest = f"sha256:{str(graph_revision) * 64}"
    conn.execute(
        """
        INSERT INTO host_command_journal(
            command_id, schema_version, request_id, surface, principal_id,
            session_id, input_ref, input_sha256, route_type, target_id,
            business_scope, operation_fingerprint, root_work_order_id,
            graph_id, graph_revision, assignment_id, command_json,
            command_digest, work_order_json, work_order_digest,
            assignment_json, assignment_digest, graph_json, graph_digest,
            created_at_ms
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                  ?, ?, ?, ?, ?, ?)
        """,
        (
            command_id,
            "test.host-command.v1",
            f"request-{graph_id}-{graph_revision}",
            "test-surface",
            "principal://migration/test",
            f"session://migration/{graph_revision}",
            f"input://migration/{graph_revision}",
            digest,
            "TASK_GRAPH",
            f"target-{graph_revision}",
            f"scope-{graph_id}",
            digest,
            f"work-order-{graph_id}-{graph_revision}",
            graph_id,
            graph_revision,
            f"assignment-{graph_id}",
            "{}",
            digest,
            "{}",
            digest,
            "{}",
            digest,
            "{}",
            digest,
            graph_revision,
        ),
    )


def _governed_outbox_identity(
    *,
    command_id: str,
    aggregate_id: str,
    aggregate_version: int,
    event_type: str,
) -> tuple[str, str]:
    material = "\n".join(
        (
            command_id,
            "TASK_GRAPH",
            aggregate_id,
            str(aggregate_version),
            event_type,
        )
    )
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()
    return f"outbox::governed::{digest}", f"governed:{digest}"


def _insert_legacy_outbox(
    conn: sqlite3.Connection,
    *,
    outbox_id: str,
    aggregate_type: str,
    aggregate_id: str,
    aggregate_version: int,
    event_type: str,
    attempt_id: str | None = None,
    evidence_refs: tuple[str, ...] = (),
    idempotency_key: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO task_runtime_outbox(
            outbox_id, aggregate_type, aggregate_id, aggregate_version,
            event_type, payload_ref, evidence_refs_json, attempt_id,
            idempotency_key, status, available_ts_ms, created_ts_ms,
            published_ts_ms, status_version
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', 1, 1, NULL, 0)
        """,
        (
            outbox_id,
            aggregate_type,
            aggregate_id,
            aggregate_version,
            event_type,
            (
                f"taskstore://event/{aggregate_type}/{aggregate_id}/"
                f"{aggregate_version}"
            ),
            json.dumps(list(evidence_refs), separators=(",", ":")),
            attempt_id,
            idempotency_key or f"idempotency:{outbox_id}",
        ),
    )


def _insert_revisioned_outbox(
    conn: sqlite3.Connection,
    *,
    outbox_id: str,
    aggregate_type: str,
    aggregate_id: str,
    aggregate_version: int,
    event_type: str,
    graph_revision: int | None,
) -> None:
    payload_aggregate_id = aggregate_id
    if aggregate_type == "TASK_GRAPH" and type(graph_revision) is int:
        payload_aggregate_id = f"{aggregate_id}@{graph_revision}"
    conn.execute(
        """
        INSERT INTO task_runtime_outbox(
            outbox_id, aggregate_type, aggregate_id, graph_revision,
            aggregate_version, event_type, payload_ref, evidence_refs_json,
            attempt_id, idempotency_key, status, available_ts_ms,
            created_ts_ms, published_ts_ms, status_version
        ) VALUES (?, ?, ?, ?, ?, ?, ?, '[]', NULL, ?, 'PENDING', 1, 1, NULL, 0)
        """,
        (
            outbox_id,
            aggregate_type,
            aggregate_id,
            graph_revision,
            aggregate_version,
            event_type,
            (
                f"taskstore://event/{aggregate_type}/{payload_aggregate_id}/"
                f"{aggregate_version}"
            ),
            f"idempotency:{outbox_id}",
        ),
    )


def test_legacy_inline_columns_are_checksum_bound_and_postcondition_checked() -> None:
    asset = _asset(
        "0041_canon_change_log_body.sql",
        "663fc6a8c069e8d7749cc580f347edc18ec89fb685f602f6e614e5c32524b06f",
    )
    failure = sqlite3.OperationalError("duplicate column name: before_body")
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute(
            """
            CREATE TABLE canon_change_log(
                id INTEGER PRIMARY KEY,
                before_body TEXT,
                after_body TEXT
            )
            """
        )
        assert is_exact_legacy_inline_schema_failure(asset, failure) is True
        assert legacy_inline_schema_postcondition_holds(conn, asset) is True

        conn.execute("ALTER TABLE canon_change_log RENAME TO old_canon_change_log")
        conn.execute(
            "CREATE TABLE canon_change_log(id INTEGER PRIMARY KEY, before_body TEXT)"
        )
        assert legacy_inline_schema_postcondition_holds(conn, asset) is False
        assert is_exact_legacy_inline_schema_failure(
            SimpleNamespace(**{**vars(asset), "checksum": "0" * 64}),
            failure,
        ) is False
    finally:
        conn.close()


def test_legacy_inline_ledger_replays_complete_catalog_without_duplicate_ddl(
    tmp_path,
) -> None:
    db_path = tmp_path / "legacy-inline.sqlite3"
    legacy = SQLiteLedger(db_path=db_path)
    legacy.close()

    result = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="aw03-legacy-inline-replay",
        ts_ms=1_800_000_000_000,
    )

    assert result.success is True, result.as_dict()
    with sqlite3.connect(db_path) as conn:
        dispositions = dict(
            conn.execute(
                """
                SELECT asset_key, disposition
                FROM schema_migration_assets
                WHERE asset_key IN (
                    '0041#canon_change_log_body',
                    '0043#canon_proposal_content',
                    '0069#life_episode_retention'
                )
                """
            ).fetchall()
        )
    assert dispositions == {
        "0041#canon_change_log_body": "APPLIED_WITH_COMPAT",
        "0043#canon_proposal_content": "APPLIED_WITH_COMPAT",
        "0069#life_episode_retention": "APPLIED_WITH_COMPAT",
    }


def test_capability_forge_proposal_log_is_migration_owned(tmp_path) -> None:
    db_path = tmp_path / "capability-forge-migrated.sqlite3"
    result = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="aw03-capability-forge-schema",
        ts_ms=1_800_000_000_000,
    )
    assert result.success is True, result.as_dict()
    assert result.summary["current_version"] >= 84

    ledger = SQLiteLedger(db_path=db_path, initialize_schema=False)
    try:
        ledger.log_capability_forge_proposal(
            {
                "proposal_id": "forge-proposal-1",
                "event_type": "CREATED",
                "status": "PENDING_APPROVAL",
                "stage": "SHADOW",
                "payload": {"proposal_id": "forge-proposal-1"},
                "ts_ms": 1,
            }
        )
        ledger.log_capability_forge_proposal(
            {
                "proposal_id": "forge-proposal-1",
                "event_type": "APPROVED",
                "status": "APPROVED",
                "stage": "SHADOW",
                "payload": {
                    "proposal_id": "forge-proposal-1",
                    "status": "APPROVED",
                },
                "ts_ms": 3,
            }
        )
        ledger.log_capability_forge_proposal(
            {
                "proposal_id": "forge-proposal-2",
                "event_type": "CREATED",
                "status": "PENDING_APPROVAL",
                "stage": "SHADOW",
                "payload": {"proposal_id": "forge-proposal-2"},
                "ts_ms": 2,
            }
        )
        rows = ledger.list_capability_forge_proposals(
            proposal_id="forge-proposal-1",
        )
        latest = ledger.get_latest_capability_forge_proposals(limit=2)
    finally:
        ledger.close()

    assert len(rows) == 2
    assert rows[0]["payload"]["proposal_id"] == "forge-proposal-1"
    assert [row["proposal_id"] for row in latest] == [
        "forge-proposal-1",
        "forge-proposal-2",
    ]
    assert latest[0]["payload"]["status"] == "APPROVED"


def test_governed_runtime_migrations_upgrade_an_existing_head_83_database(
    tmp_path,
) -> None:
    db_path = tmp_path / "head-83.sqlite3"
    runner = SQLiteMigrationRunner(db_path=db_path)
    seeded = runner.apply_migrations(
        request_id="aw03-capability-forge-seed",
        ts_ms=1_800_000_000_000,
    )
    assert seeded.success is True, seeded.as_dict()

    with sqlite3.connect(db_path) as conn:
        _drop_post_0122_task_graph_admission_triggers(conn)
        conn.execute("DROP TABLE task_runtime_acceptance_effect_intent")
        conn.execute("DROP TABLE task_runtime_worker_projection")
        conn.execute("DROP TABLE task_runtime_budget_usage")
        conn.execute("DROP TABLE capability_forge_proposal_log")
        conn.execute("DELETE FROM schema_migration_assets WHERE migration_id >= 84")
        conn.execute("DELETE FROM schema_migrations WHERE migration_id >= 84")
        conn.commit()
        assert conn.execute(
            "SELECT MAX(migration_id) FROM schema_migrations"
        ).fetchone() == (83,)

    upgraded = runner.apply_migrations(
        request_id="aw03-capability-forge-upgrade",
        ts_ms=1_800_000_000_001,
    )

    assert upgraded.success is True, upgraded.as_dict()
    assert upgraded.summary["current_version"] >= 102
    assert upgraded.summary["applied_count"] >= 19
    assert "0102_task_runtime_node_artifact" in {
        record.migration_id for record in upgraded.records
    }
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            """
            SELECT name FROM sqlite_master
            WHERE type = 'table' AND name = 'capability_forge_proposal_log'
            """
        ).fetchone() == ("capability_forge_proposal_log",)
        assert conn.execute(
            """
            SELECT name FROM sqlite_master
            WHERE type = 'table' AND name = 'task_runtime_budget_usage'
            """
        ).fetchone() == ("task_runtime_budget_usage",)
        assert conn.execute(
            """
            SELECT name FROM sqlite_master
            WHERE type = 'table' AND name = 'task_runtime_worker_projection'
            """
        ).fetchone() == ("task_runtime_worker_projection",)
        assert conn.execute(
            """
            SELECT name FROM sqlite_master
            WHERE type = 'table' AND name = 'task_runtime_acceptance_effect_intent'
            """
        ).fetchone() == ("task_runtime_acceptance_effect_intent",)
        assert conn.execute(
            """
            SELECT name FROM sqlite_master
            WHERE type = 'table'
                AND name = 'task_runtime_parallel_settlement_decision'
            """
        ).fetchone() == ("task_runtime_parallel_settlement_decision",)
        assert conn.execute(
            """
            SELECT name FROM sqlite_master
            WHERE type = 'table'
                AND name = 'task_runtime_parallel_success_decision'
            """
        ).fetchone() == ("task_runtime_parallel_success_decision",)


def test_0089_receipt_loss_replay_requires_the_complete_frozen_schema(tmp_path) -> None:
    db_path = tmp_path / "0089-receipt-loss.sqlite3"
    runner = SQLiteMigrationRunner(db_path=db_path)
    seeded = runner.apply_migrations(request_id="0089-seed", ts_ms=20)
    assert seeded.success is True, seeded.as_dict()

    with sqlite3.connect(db_path) as conn:
        # Rewind the catalog consistently from 0089. Leaving later receipts
        # behind would create an impossible migration-history gap rather than
        # exercising the checksum-bound 0089 replay compatibility path.
        _drop_post_0122_task_graph_admission_triggers(conn)
        conn.execute("DELETE FROM schema_migration_assets WHERE migration_id >= 89")
        conn.execute("DELETE FROM schema_migrations WHERE migration_id >= 89")
        conn.commit()

    repaired = runner.apply_migrations(request_id="0089-receipt-repair", ts_ms=21)
    assert repaired.success is True, repaired.as_dict()
    repaired_ids = [record.migration_id for record in repaired.records]
    start_0090 = repaired_ids.index(
        "0090_task_runtime_parallel_settlement_decision"
    )
    assert repaired_ids[start_0090 : start_0090 + 11] == [
        "0090_task_runtime_parallel_settlement_decision",
        "0091_task_runtime_parallel_success_decision",
        "0092_task_runtime_graph_cancellation",
        "0093_task_runtime_outbox_claim",
        "0094_task_runtime_graph_cancellation_continuation",
        "0095_task_runtime_revisioned_graph_outbox",
        "0096_task_runtime_review_continuation",
        "0097_node_grant_batch_uniqueness",
        "0098_task_runtime_effect_reconciliation",
        "0099_task_runtime_terminal_reconciliation",
        "0100_task_runtime_graph_revision_supersession",
    ]
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT disposition FROM schema_migration_assets WHERE migration_id = 89"
        ).fetchone() == ("APPLIED_WITH_COMPAT",)

        asset = _asset(
            "0089_task_runtime_dag_scheduling.sql",
            "b322b4d98b0fa05eb0774a91530818c88d17dbd5168f821dacecef67ec3d1663",
        )
        failure = sqlite3.OperationalError(
            "duplicate column name: retry_not_before_ms"
        )
        assert is_exact_checksum_bound_schema_replay_failure(asset, failure) is True
        assert checksum_bound_schema_replay_postcondition_holds(conn, asset) is True

        conn.execute("DROP INDEX idx_task_runtime_node_retry_due")
        assert checksum_bound_schema_replay_postcondition_holds(conn, asset) is False


def test_0093_receipt_loss_replay_requires_claim_indexes_and_guards(tmp_path) -> None:
    db_path = tmp_path / "0093-receipt-loss.sqlite3"
    runner = SQLiteMigrationRunner(db_path=db_path)
    seeded = runner.apply_migrations(request_id="0093-seed", ts_ms=22)
    assert seeded.success is True, seeded.as_dict()

    asset = _asset(
        "0093_task_runtime_outbox_claim.sql",
        "5ac7145decc5bcc9cea9bb8378d9cdf93c0196adcb7b8c3dccecf8cff77b16be",
    )
    failure = sqlite3.OperationalError("duplicate column name: claim_owner")
    assert is_exact_checksum_bound_schema_replay_failure(asset, failure) is True
    with sqlite3.connect(db_path) as conn:
        assert checksum_bound_schema_replay_postcondition_holds(conn, asset) is True
        conn.execute("DROP TRIGGER trg_task_runtime_outbox_claim_update_guard")
        assert checksum_bound_schema_replay_postcondition_holds(conn, asset) is False


def test_0094_receipt_loss_replay_is_idempotent_and_keeps_one_continuation_shape(
    tmp_path,
) -> None:
    db_path = tmp_path / "0094-receipt-loss.sqlite3"
    runner = SQLiteMigrationRunner(db_path=db_path)
    seeded = runner.apply_migrations(request_id="0094-seed", ts_ms=23)
    assert seeded.success is True, seeded.as_dict()

    with sqlite3.connect(db_path) as conn:
        _drop_post_0122_task_graph_admission_triggers(conn)
        conn.execute("DELETE FROM schema_migration_assets WHERE migration_id >= 94")
        conn.execute("DELETE FROM schema_migrations WHERE migration_id >= 94")
        conn.commit()

    replayed = runner.apply_migrations(request_id="0094-receipt-replay", ts_ms=24)

    assert replayed.success is True, replayed.as_dict()
    assert replayed.summary["current_version"] >= 102
    assert "0102_task_runtime_node_artifact" in {
        record.migration_id for record in replayed.records
    }
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            """
            SELECT COUNT(*) FROM sqlite_master
            WHERE type = 'table'
              AND name = 'task_runtime_graph_cancellation_continuation'
            """
        ).fetchone() == (1,)
        index_names = {
            str(row[0])
            for row in conn.execute(
                """
                SELECT name FROM sqlite_master
                WHERE type = 'index'
                  AND tbl_name = 'task_runtime_graph_cancellation_continuation'
                """
            ).fetchall()
        }
    assert {
        "uq_task_runtime_graph_cancellation_continuation_graph",
        "idx_task_runtime_graph_cancellation_continuation_ready",
    } <= index_names


def test_0095_fresh_schema_freezes_revision_column_guards_and_delivery_index(
    tmp_path,
) -> None:
    db_path = tmp_path / "0095-fresh.sqlite3"
    result = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="0095-fresh",
        ts_ms=25,
    )

    assert result.success is True, result.as_dict()
    assert result.summary["current_version"] >= 102
    assert "0102_task_runtime_node_artifact" in {
        record.migration_id for record in result.records
    }
    with sqlite3.connect(db_path) as conn:
        graph_revision = {
            str(row[1]): (str(row[2]), int(row[3]))
            for row in conn.execute(
                "PRAGMA table_info(task_runtime_outbox)"
            ).fetchall()
        }["graph_revision"]
        index_columns = tuple(
            str(row[2])
            for row in conn.execute(
                "PRAGMA index_info(idx_task_runtime_outbox_revision_delivery_order)"
            ).fetchall()
        )
        triggers = {
            str(row[0])
            for row in conn.execute(
                """
                SELECT name FROM sqlite_master
                WHERE type = 'trigger' AND tbl_name = 'task_runtime_outbox'
                """
            ).fetchall()
        }

    assert graph_revision == ("INTEGER", 0)
    assert index_columns == (
        "aggregate_type",
        "aggregate_id",
        "graph_revision",
        "status",
        "aggregate_version",
        "created_ts_ms",
        "outbox_id",
    )
    assert {
        "trg_task_runtime_outbox_graph_revision_insert_guard",
        "trg_task_runtime_outbox_graph_revision_update_guard",
    } <= triggers


def test_0095_receipt_loss_replay_requires_complete_revision_schema(tmp_path) -> None:
    db_path = tmp_path / "0095-receipt-loss.sqlite3"
    seeded = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="0095-receipt-loss",
        ts_ms=25,
    )
    assert seeded.success is True, seeded.as_dict()

    asset_name = "0095_task_runtime_revisioned_graph_outbox.sql"
    migration_path = (
        Path(migration_runner_module.__file__).with_name("migrations")
        / asset_name
    )
    asset = _asset(asset_name, hashlib.sha256(migration_path.read_bytes()).hexdigest())
    failure = sqlite3.OperationalError("duplicate column name: graph_revision")
    assert is_exact_checksum_bound_schema_replay_failure(asset, failure) is True
    with sqlite3.connect(db_path) as conn:
        assert checksum_bound_schema_replay_postcondition_holds(conn, asset) is True
        conn.execute(
            "DROP TRIGGER trg_task_runtime_outbox_graph_revision_update_guard"
        )
        assert checksum_bound_schema_replay_postcondition_holds(conn, asset) is False


def test_0095_receipt_loss_postcondition_keeps_pre_0098_trigger_epoch(
    tmp_path,
) -> None:
    db_path = tmp_path / "0095-pre-0098-receipt-loss.sqlite3"
    seeded = SQLiteMigrationRunner(
        db_path=db_path,
        migrations_dir=_migration_catalog_through(tmp_path, 95),
    ).apply_migrations(request_id="0095-pre-0098", ts_ms=25)
    assert seeded.success is True, seeded.as_dict()
    assert seeded.summary["current_version"] == 95

    asset_name = "0095_task_runtime_revisioned_graph_outbox.sql"
    migration_path = (
        Path(migration_runner_module.__file__).with_name("migrations")
        / asset_name
    )
    asset = _asset(asset_name, hashlib.sha256(migration_path.read_bytes()).hexdigest())
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            """
            SELECT 1 FROM sqlite_master
            WHERE type = 'table'
              AND name = 'task_runtime_effect_reconciliation_case'
            """
        ).fetchone() is None
        assert checksum_bound_schema_replay_postcondition_holds(conn, asset) is True
        conn.execute(
            "DROP TRIGGER trg_task_runtime_outbox_graph_revision_insert_guard"
        )
        assert checksum_bound_schema_replay_postcondition_holds(conn, asset) is False


def test_0095_upgrade_backfills_only_exact_authority_then_unique_graph_fallback(
    tmp_path,
) -> None:
    db_path = tmp_path / "0095-authority-upgrade.sqlite3"
    head_94 = SQLiteMigrationRunner(
        db_path=db_path,
        migrations_dir=_migration_catalog_through(tmp_path, 94),
    ).apply_migrations(request_id="0095-head-94", ts_ms=26)
    assert head_94.success is True, head_94.as_dict()
    assert head_94.summary["current_version"] == 94

    graph_id = "graph-0095-authority"
    with sqlite3.connect(db_path) as conn:
        _insert_task_graph(conn, graph_id=graph_id, revision=1)
        _insert_task_graph(conn, graph_id=graph_id, revision=2)
        for outbox_id, event_type in (
            ("outbox-0090", "TASK_GRAPH_BLOCKED"),
            ("outbox-0091", "TASK_GRAPH_COMPLETED"),
            ("outbox-0092-intent", "TASK_GRAPH_CANCELLATION_REQUESTED"),
            ("outbox-0092-decision", "TASK_GRAPH_CANCELLED"),
        ):
            _insert_legacy_outbox(
                conn,
                outbox_id=outbox_id,
                aggregate_type="TASK_GRAPH",
                aggregate_id=graph_id,
                aggregate_version=2,
                event_type=event_type,
            )

        conn.execute(
            """
            INSERT INTO task_runtime_parallel_settlement_decision(
                decision_ref, schema_version, graph_id, graph_revision,
                graph_version_before, graph_version_after, budget_id,
                budget_version_before, budget_version_after, decision_kind,
                selected_node_id, graph_target_status, budget_target_status,
                payload_json, payload_sha256, graph_outbox_id,
                budget_outbox_id, created_ts_ms
            ) VALUES (
                'taskbatch://sha256/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
                'test.v1', ?, 2, 1, 2, 'budget-0090', 0, 0, 'REVIEW',
                'node-0090', 'BLOCKED', NULL, '{}', ?, 'outbox-0090', NULL, 1
            )
            """,
            (graph_id, f"sha256:{'a' * 64}"),
        )
        conn.execute(
            """
            INSERT INTO task_runtime_parallel_success_decision(
                decision_ref, schema_version, graph_id, graph_revision,
                graph_version_before, graph_version_after, budget_id,
                budget_version_before, budget_version_after, result_node_id,
                result_attempt_id, result_receipt_ref, payload_json,
                payload_sha256, graph_outbox_id, budget_outbox_id, created_ts_ms
            ) VALUES (
                'taskgraph-success://sha256/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb',
                'test.v1', ?, 1, 1, 2, 'budget-0091', 0, 1, 'node-0091',
                'attempt-0091', 'receipt://0091', '{}', ?,
                'outbox-0091', 'budget-outbox-0091', 1
            )
            """,
            (graph_id, f"sha256:{'b' * 64}"),
        )
        conn.execute(
            """
            INSERT INTO task_runtime_graph_cancellation_intent(
                intent_ref, schema_version, request_id, request_identity,
                source, reason_code, graph_id, graph_revision,
                graph_version_before, graph_version_after, budget_id,
                budget_version_at_request, payload_json, payload_sha256,
                graph_outbox_id, status, status_version, requested_ts_ms,
                finalized_ts_ms
            ) VALUES (
                'taskcancel-intent://0095', 'test.v1', 'request-0095',
                'principal://0095', 'OPERATOR', 'TEST', ?, 2, 1, 2,
                NULL, NULL, '{}', ?, 'outbox-0092-intent',
                'FINALIZED', 1, 1, 2
            )
            """,
            (graph_id, f"sha256:{'c' * 64}"),
        )
        conn.execute(
            """
            INSERT INTO task_runtime_graph_cancellation_decision(
                decision_ref, schema_version, intent_ref, graph_id,
                graph_revision, graph_version_before, graph_version_after,
                budget_id, budget_version_before, budget_version_after,
                payload_json, payload_sha256, graph_outbox_id,
                budget_outbox_id, created_ts_ms
            ) VALUES (
                'taskcancel-decision://0095', 'test.v1',
                'taskcancel-intent://0095', ?, 2, 2, 3, NULL, NULL, NULL,
                '{}', ?, 'outbox-0092-decision', NULL, 2
            )
            """,
            (graph_id, f"sha256:{'d' * 64}"),
        )

        attempt_graph_id = "graph-0095-attempt"
        _insert_task_graph(conn, graph_id=attempt_graph_id, revision=1)
        _insert_task_graph(conn, graph_id=attempt_graph_id, revision=2)
        conn.execute(
            """
            INSERT INTO task_runtime_attempt(
                attempt_id, graph_id, graph_revision, node_id, attempt_no,
                status, status_version, created_ts_ms, updated_ts_ms
            ) VALUES ('attempt-0095', ?, 1, 'node-attempt', 1,
                      'SUCCEEDED', 1, 1, 1)
            """,
            (attempt_graph_id,),
        )
        _insert_legacy_outbox(
            conn,
            outbox_id="outbox-attempt",
            aggregate_type="TASK_GRAPH",
            aggregate_id=attempt_graph_id,
            aggregate_version=2,
            event_type="TASK_GRAPH_FAILED",
            attempt_id="attempt-0095",
        )

        unique_graph_id = "graph-0095-unique"
        _insert_task_graph(conn, graph_id=unique_graph_id, revision=7)
        _insert_legacy_outbox(
            conn,
            outbox_id="outbox-unique",
            aggregate_type="TASK_GRAPH",
            aggregate_id=unique_graph_id,
            aggregate_version=0,
            event_type="TASK_GRAPH_CREATED",
        )
        _insert_legacy_outbox(
            conn,
            outbox_id="outbox-budget",
            aggregate_type="BUDGET_RESERVATION",
            aggregate_id="budget-0095",
            aggregate_version=1,
            event_type="BUDGET_RESERVATION_ACTIVE",
        )
        conn.commit()

    upgraded = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="0095-authority-upgrade",
        ts_ms=27,
    )

    assert upgraded.success is True, upgraded.as_dict()
    assert upgraded.summary["current_version"] >= 102
    with sqlite3.connect(db_path) as conn:
        revisions = dict(
            conn.execute(
                """
                SELECT outbox_id, graph_revision FROM task_runtime_outbox
                WHERE outbox_id IN (
                    'outbox-0090', 'outbox-0091', 'outbox-0092-intent',
                    'outbox-0092-decision', 'outbox-attempt', 'outbox-unique',
                    'outbox-budget'
                )
                """
            ).fetchall()
        )
    assert revisions == {
        "outbox-0090": 2,
        "outbox-0091": 1,
        "outbox-0092-intent": 2,
        "outbox-0092-decision": 2,
        "outbox-attempt": 1,
        "outbox-unique": 7,
        "outbox-budget": None,
    }


def test_0095_backfills_created_outboxes_from_exact_host_command_evidence(
    tmp_path,
) -> None:
    db_path = tmp_path / "0095-host-command-evidence.sqlite3"
    head_94 = SQLiteMigrationRunner(
        db_path=db_path,
        migrations_dir=_migration_catalog_through(tmp_path, 94),
    ).apply_migrations(request_id="0095-host-command-head", ts_ms=28)
    assert head_94.success is True, head_94.as_dict()

    graph_id = "graph-0095-command-history"
    with sqlite3.connect(db_path) as conn:
        for revision in (1, 2):
            command_id = f"host-command://{graph_id}/{revision}"
            _insert_task_graph(conn, graph_id=graph_id, revision=revision)
            _insert_host_command(
                conn,
                command_id=command_id,
                graph_id=graph_id,
                graph_revision=revision,
            )
            _insert_legacy_outbox(
                conn,
                outbox_id=f"outbox-created-r{revision}",
                aggregate_type="TASK_GRAPH",
                aggregate_id=graph_id,
                aggregate_version=0,
                event_type="TASK_GRAPH_CREATED",
                evidence_refs=(
                    f"evidence://migration/unrelated/{revision}",
                    command_id,
                ),
            )
        conn.commit()

    upgraded = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="0095-host-command-upgrade",
        ts_ms=29,
    )

    assert upgraded.success is True, upgraded.as_dict()
    with sqlite3.connect(db_path) as conn:
        assert dict(
            conn.execute(
                """
                SELECT outbox_id, graph_revision
                FROM task_runtime_outbox
                WHERE outbox_id IN ('outbox-created-r1', 'outbox-created-r2')
                ORDER BY outbox_id
                """
            ).fetchall()
        ) == {
            "outbox-created-r1": 1,
            "outbox-created-r2": 2,
        }


def test_0095_backfills_governed_transitions_from_exact_command_identity(
    tmp_path,
) -> None:
    db_path = tmp_path / "0095-governed-identity.sqlite3"
    head_94 = SQLiteMigrationRunner(
        db_path=db_path,
        migrations_dir=_migration_catalog_through(tmp_path, 94),
    ).apply_migrations(request_id="0095-governed-head", ts_ms=30)
    assert head_94.success is True, head_94.as_dict()

    graph_id = "graph-0095-governed-history"
    expected: dict[str, int] = {}
    with sqlite3.connect(db_path) as conn:
        for revision in (1, 2):
            command_id = f"host-command://{graph_id}/{revision}"
            _insert_task_graph(conn, graph_id=graph_id, revision=revision)
            _insert_host_command(
                conn,
                command_id=command_id,
                graph_id=graph_id,
                graph_revision=revision,
            )
            outbox_id, idempotency_key = _governed_outbox_identity(
                command_id=command_id,
                aggregate_id=graph_id,
                aggregate_version=1,
                event_type="TASK_GRAPH_ACTIVE",
            )
            _insert_legacy_outbox(
                conn,
                outbox_id=outbox_id,
                aggregate_type="TASK_GRAPH",
                aggregate_id=graph_id,
                aggregate_version=1,
                event_type="TASK_GRAPH_ACTIVE",
                idempotency_key=idempotency_key,
            )
            expected[outbox_id] = revision
        conn.commit()

    upgraded = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="0095-governed-upgrade",
        ts_ms=31,
    )

    assert upgraded.success is True, upgraded.as_dict()
    with sqlite3.connect(db_path) as conn:
        actual = dict(
            conn.execute(
                """
                SELECT outbox_id, graph_revision
                FROM task_runtime_outbox
                WHERE event_type = 'TASK_GRAPH_ACTIVE'
                ORDER BY outbox_id
                """
            ).fetchall()
        )
    assert actual == expected


def test_0095_does_not_treat_command_evidence_as_transition_authority(
    tmp_path,
) -> None:
    db_path = tmp_path / "0095-command-evidence-not-transition.sqlite3"
    head_94 = SQLiteMigrationRunner(
        db_path=db_path,
        migrations_dir=_migration_catalog_through(tmp_path, 94),
    ).apply_migrations(request_id="0095-command-evidence-head", ts_ms=31)
    assert head_94.success is True, head_94.as_dict()

    graph_id = "graph-0095-command-evidence-transition"
    command_id = f"host-command://{graph_id}/1"
    with sqlite3.connect(db_path) as conn:
        for revision in (1, 2):
            _insert_task_graph(conn, graph_id=graph_id, revision=revision)
        _insert_host_command(
            conn,
            command_id=command_id,
            graph_id=graph_id,
            graph_revision=1,
        )
        _insert_legacy_outbox(
            conn,
            outbox_id="outbox-command-evidence-active",
            aggregate_type="TASK_GRAPH",
            aggregate_id=graph_id,
            aggregate_version=1,
            event_type="TASK_GRAPH_ACTIVE",
            evidence_refs=(command_id,),
        )
        conn.commit()

    rejected = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="0095-command-evidence-upgrade",
        ts_ms=32,
    )

    assert rejected.success is False
    with sqlite3.connect(db_path) as conn:
        assert "graph_revision" not in {
            str(row[1])
            for row in conn.execute(
                "PRAGMA table_info(task_runtime_outbox)"
            ).fetchall()
        }


@pytest.mark.parametrize("tampered_field", ("outbox_id", "idempotency_key"))
def test_0095_rejects_tampered_governed_transition_identity(
    tmp_path,
    tampered_field: str,
) -> None:
    db_path = tmp_path / f"0095-governed-tampered-{tampered_field}.sqlite3"
    head_94 = SQLiteMigrationRunner(
        db_path=db_path,
        migrations_dir=_migration_catalog_through(tmp_path, 94),
    ).apply_migrations(request_id="0095-governed-tampered-head", ts_ms=32)
    assert head_94.success is True, head_94.as_dict()

    graph_id = "graph-0095-governed-tampered"
    command_id = f"host-command://{graph_id}/1"
    outbox_id, idempotency_key = _governed_outbox_identity(
        command_id=command_id,
        aggregate_id=graph_id,
        aggregate_version=1,
        event_type="TASK_GRAPH_ACTIVE",
    )
    if tampered_field == "outbox_id":
        outbox_id = f"{outbox_id}-tampered"
    else:
        idempotency_key = f"{idempotency_key}-tampered"

    with sqlite3.connect(db_path) as conn:
        for revision in (1, 2):
            _insert_task_graph(conn, graph_id=graph_id, revision=revision)
        _insert_host_command(
            conn,
            command_id=command_id,
            graph_id=graph_id,
            graph_revision=1,
        )
        _insert_legacy_outbox(
            conn,
            outbox_id=outbox_id,
            aggregate_type="TASK_GRAPH",
            aggregate_id=graph_id,
            aggregate_version=1,
            event_type="TASK_GRAPH_ACTIVE",
            idempotency_key=idempotency_key,
        )
        conn.commit()

    rejected = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="0095-governed-tampered-upgrade",
        ts_ms=33,
    )

    assert rejected.success is False
    with sqlite3.connect(db_path) as conn:
        columns = {
            str(row[1])
            for row in conn.execute(
                "PRAGMA table_info(task_runtime_outbox)"
            ).fetchall()
        }
        assert "graph_revision" not in columns
        assert conn.execute(
            "SELECT 1 FROM schema_migrations WHERE migration_id = 95"
        ).fetchone() is None


def test_0095_ambiguous_multirevision_outbox_rolls_back_the_whole_migration(
    tmp_path,
) -> None:
    db_path = tmp_path / "0095-ambiguous.sqlite3"
    head_94 = SQLiteMigrationRunner(
        db_path=db_path,
        migrations_dir=_migration_catalog_through(tmp_path, 94),
    ).apply_migrations(request_id="0095-ambiguous-head", ts_ms=28)
    assert head_94.success is True, head_94.as_dict()

    with sqlite3.connect(db_path) as conn:
        _insert_task_graph(conn, graph_id="graph-0095-ambiguous", revision=1)
        _insert_task_graph(conn, graph_id="graph-0095-ambiguous", revision=2)
        _insert_legacy_outbox(
            conn,
            outbox_id="outbox-ambiguous",
            aggregate_type="TASK_GRAPH",
            aggregate_id="graph-0095-ambiguous",
            aggregate_version=0,
            event_type="TASK_GRAPH_CREATED",
        )
        conn.commit()

    rejected = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="0095-ambiguous-upgrade",
        ts_ms=29,
    )

    assert rejected.success is False
    assert rejected.records[-1].migration_id == (
        "0095_task_runtime_revisioned_graph_outbox"
    )
    with sqlite3.connect(db_path) as conn:
        columns = {
            str(row[1])
            for row in conn.execute(
                "PRAGMA table_info(task_runtime_outbox)"
            ).fetchall()
        }
        assert "graph_revision" not in columns
        assert conn.execute(
            "SELECT 1 FROM schema_migrations WHERE migration_id = 95"
        ).fetchone() is None
        assert conn.execute(
            "SELECT aggregate_id FROM task_runtime_outbox WHERE outbox_id = ?",
            ("outbox-ambiguous",),
        ).fetchone() == ("graph-0095-ambiguous",)


def test_0095_guards_reject_missing_wrong_and_non_graph_revisions(tmp_path) -> None:
    db_path = tmp_path / "0095-guards.sqlite3"
    seeded = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="0095-guards",
        ts_ms=30,
    )
    assert seeded.success is True, seeded.as_dict()

    with sqlite3.connect(db_path) as conn:
        register_task_graph_admission_authority(conn)
        with canonical_task_graph_admission_scope("graph-0095-guard", 2):
            _insert_task_graph(conn, graph_id="graph-0095-guard", revision=2)
        with pytest.raises(sqlite3.IntegrityError, match="graph revision is invalid"):
            _insert_revisioned_outbox(
                conn,
                outbox_id="outbox-null-revision",
                aggregate_type="TASK_GRAPH",
                aggregate_id="graph-0095-guard",
                graph_revision=None,
                aggregate_version=0,
                event_type="TASK_GRAPH_CREATED",
            )
        with pytest.raises(sqlite3.IntegrityError, match="graph revision is invalid"):
            _insert_revisioned_outbox(
                conn,
                outbox_id="outbox-wrong-revision",
                aggregate_type="TASK_GRAPH",
                aggregate_id="graph-0095-guard",
                graph_revision=1,
                aggregate_version=0,
                event_type="TASK_GRAPH_CREATED",
            )
        with pytest.raises(sqlite3.IntegrityError, match="graph revision is invalid"):
            _insert_revisioned_outbox(
                conn,
                outbox_id="outbox-non-graph-revision",
                aggregate_type="BUDGET_RESERVATION",
                aggregate_id="budget-0095-guard",
                graph_revision=2,
                aggregate_version=0,
                event_type="BUDGET_RESERVATION_ACTIVE",
            )

        _insert_revisioned_outbox(
            conn,
            outbox_id="outbox-valid-revision",
            aggregate_type="TASK_GRAPH",
            aggregate_id="graph-0095-guard",
            graph_revision=2,
            aggregate_version=0,
            event_type="TASK_GRAPH_CREATED",
        )
        with pytest.raises(sqlite3.IntegrityError, match="graph revision is invalid"):
            conn.execute(
                """
                UPDATE task_runtime_outbox SET graph_revision = 1
                WHERE outbox_id = 'outbox-valid-revision'
                """
            )
        assert conn.execute(
            """
            SELECT graph_revision FROM task_runtime_outbox
            WHERE outbox_id = 'outbox-valid-revision'
            """
        ).fetchone() == (2,)


def test_0090_parallel_settlement_decision_upgrades_head_89_without_data_loss(
    tmp_path,
) -> None:
    db_path = tmp_path / "head-89-to-90.sqlite3"
    runner = SQLiteMigrationRunner(db_path=db_path)
    seeded = runner.apply_migrations(request_id="0090-seed", ts_ms=30)
    assert seeded.success is True, seeded.as_dict()

    with sqlite3.connect(db_path) as conn:
        _drop_post_0122_task_graph_admission_triggers(conn)
        conn.execute("DROP TABLE task_runtime_parallel_success_decision")
        conn.execute("DROP TABLE task_runtime_parallel_settlement_decision")
        conn.execute("DELETE FROM schema_migration_assets WHERE migration_id >= 90")
        conn.execute("DELETE FROM schema_migrations WHERE migration_id >= 90")
        conn.execute(
            """
            INSERT INTO task_runtime_graph(
                graph_id, revision, root_work_order_id, planner_ref,
                status, status_version, graph_json, source_kind,
                created_ts_ms, updated_ts_ms
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "graph-head-89",
                1,
                "work-order-head-89",
                "planner://head-89",
                "ACTIVE",
                1,
                "{}",
                "CANONICAL",
                30,
                30,
            ),
        )
        conn.execute(
            """
            INSERT INTO task_runtime_node(
                graph_id, graph_revision, node_id, status, status_version,
                node_json, retry_not_before_ms, created_ts_ms, updated_ts_ms
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "graph-head-89",
                1,
                "node-head-89",
                "RETRY_PENDING",
                1,
                "{}",
                1_234,
                30,
                30,
            ),
        )
        conn.commit()
        assert conn.execute(
            "SELECT MAX(migration_id) FROM schema_migrations"
        ).fetchone() == (89,)

    upgraded = runner.apply_migrations(request_id="0090-upgrade", ts_ms=31)

    assert upgraded.success is True, upgraded.as_dict()
    assert upgraded.summary["current_version"] >= 102
    assert upgraded.summary["applied_count"] >= 13
    upgraded_ids = [record.migration_id for record in upgraded.records]
    start_0090 = upgraded_ids.index(
        "0090_task_runtime_parallel_settlement_decision"
    )
    assert upgraded_ids[start_0090 : start_0090 + 13] == [
        "0090_task_runtime_parallel_settlement_decision",
        "0091_task_runtime_parallel_success_decision",
        "0092_task_runtime_graph_cancellation",
        "0093_task_runtime_outbox_claim",
        "0094_task_runtime_graph_cancellation_continuation",
        "0095_task_runtime_revisioned_graph_outbox",
        "0096_task_runtime_review_continuation",
        "0097_node_grant_batch_uniqueness",
        "0098_task_runtime_effect_reconciliation",
        "0099_task_runtime_terminal_reconciliation",
        "0100_task_runtime_graph_revision_supersession",
        "0101_agent_workspace_authority",
        "0102_task_runtime_node_artifact",
    ]
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            """
            SELECT name FROM sqlite_master
            WHERE type = 'table'
                AND name = 'task_runtime_parallel_settlement_decision'
            """
        ).fetchone() == ("task_runtime_parallel_settlement_decision",)
        assert conn.execute(
            """
            SELECT name FROM sqlite_master
            WHERE type = 'table'
                AND name = 'task_runtime_parallel_success_decision'
            """
        ).fetchone() == ("task_runtime_parallel_success_decision",)
        assert conn.execute(
            """
            SELECT status, retry_not_before_ms
            FROM task_runtime_node
            WHERE graph_id = 'graph-head-89'
                AND graph_revision = 1
                AND node_id = 'node-head-89'
            """
        ).fetchone() == ("RETRY_PENDING", 1_234)


def test_0091_parallel_success_decision_upgrades_head_90_without_data_loss(
    tmp_path,
) -> None:
    db_path = tmp_path / "head-90-to-91.sqlite3"
    runner = SQLiteMigrationRunner(db_path=db_path)
    seeded = runner.apply_migrations(request_id="0091-seed", ts_ms=40)
    assert seeded.success is True, seeded.as_dict()

    with sqlite3.connect(db_path) as conn:
        _drop_post_0122_task_graph_admission_triggers(conn)
        conn.execute("DROP TABLE task_runtime_parallel_success_decision")
        conn.execute("DELETE FROM schema_migration_assets WHERE migration_id >= 91")
        conn.execute("DELETE FROM schema_migrations WHERE migration_id >= 91")
        conn.execute(
            """
            INSERT INTO task_runtime_graph(
                graph_id, revision, root_work_order_id, planner_ref,
                status, status_version, graph_json, source_kind,
                created_ts_ms, updated_ts_ms
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "graph-head-90",
                1,
                "work-order-head-90",
                "planner://head-90",
                "ACTIVE",
                3,
                "{}",
                "CANONICAL",
                40,
                40,
            ),
        )
        conn.commit()
        assert conn.execute(
            "SELECT MAX(migration_id) FROM schema_migrations"
        ).fetchone() == (90,)

    upgraded = runner.apply_migrations(request_id="0091-upgrade", ts_ms=41)

    assert upgraded.success is True, upgraded.as_dict()
    assert upgraded.summary["current_version"] >= 102
    assert upgraded.summary["applied_count"] >= 12
    assert "0102_task_runtime_node_artifact" in {
        record.migration_id for record in upgraded.records
    }
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            """
            SELECT status, status_version FROM task_runtime_graph
            WHERE graph_id = 'graph-head-90' AND revision = 1
            """
        ).fetchone() == ("ACTIVE", 3)
        assert conn.execute(
            """
            SELECT name FROM sqlite_master
            WHERE type = 'table'
                AND name = 'task_runtime_parallel_success_decision'
            """
        ).fetchone() == ("task_runtime_parallel_success_decision",)


def test_aw03_migrations_require_an_explicit_immediate_transaction(tmp_path) -> None:
    catalog = tmp_path / "migrations"
    catalog.mkdir()
    (catalog / "0066_untransactional.sql").write_text(
        "CREATE TABLE unsafe_partial(id INTEGER PRIMARY KEY);",
        encoding="utf-8",
    )

    report = SQLiteMigrationVersionGuard(migrations_dir=catalog).build_guard_report(
        request_id="aw03-transaction-policy",
        ts_ms=1,
    )

    assert report.status == "FAILED"
    assert "migrations >= 0066 require explicit BEGIN IMMEDIATE/COMMIT" in report.detail
    result = SQLiteMigrationRunner(
        db_path=tmp_path / "runner-policy.sqlite3",
        migrations_dir=catalog,
    ).apply_migrations(request_id="aw03-runner-transaction-policy", ts_ms=2)
    assert result.success is False
    assert result.reason_code == MIGRATION_SCHEMA_DRIFT
    with sqlite3.connect(tmp_path / "runner-policy.sqlite3") as conn:
        assert conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'unsafe_partial'"
        ).fetchone() is None


def test_frozen_0067_runner_envelope_rolls_back_partial_schema(tmp_path) -> None:
    db_path = tmp_path / "0067-envelope.sqlite3"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE canon_journal(journal_id TEXT PRIMARY KEY)")
        conn.commit()

    result = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="aw03-0067-envelope-fault",
        ts_ms=1_800_000_000_000,
    )

    assert result.success is False
    assert result.records[-1].migration_id == "0067_canon_command_spine"
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'canon_command'"
        ).fetchone() is None
        assert conn.execute(
            "SELECT 1 FROM schema_migrations WHERE migration_id = 67"
        ).fetchone() is None
        assert conn.execute(
            "SELECT 1 FROM schema_migration_assets WHERE asset_key = '0067#canon_command_spine'"
        ).fetchone() is None
        assert conn.execute("SELECT MAX(migration_id) FROM schema_migrations").fetchone() == (66,)


def test_frozen_0071_runner_envelope_rolls_back_partial_index(tmp_path) -> None:
    db_path = tmp_path / "0071-envelope.sqlite3"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE life_runtime_uow(
                uow_id TEXT PRIMARY KEY,
                aggregate_id TEXT,
                created_at_ms INTEGER
            )
            """
        )
        conn.commit()

    result = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="aw03-0071-envelope-fault",
        ts_ms=1_800_000_000_000,
    )

    assert result.success is False
    assert result.records[-1].migration_id == "0071_life_runtime_uow"
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            """
            SELECT 1 FROM sqlite_master
            WHERE type = 'index' AND name = 'idx_life_runtime_uow_aggregate'
            """
        ).fetchone() is None
        assert conn.execute(
            "SELECT 1 FROM schema_migrations WHERE migration_id = 71"
        ).fetchone() is None
        assert conn.execute(
            "SELECT 1 FROM schema_migration_assets WHERE asset_key = '0071#life_runtime_uow'"
        ).fetchone() is None
        assert conn.execute("SELECT MAX(migration_id) FROM schema_migrations").fetchone() == (70,)


def test_platform_registry_draft_is_repaired_once_then_strictly_validated(
    tmp_path,
) -> None:
    db_path = tmp_path / "platform-registry-draft.sqlite3"
    runner = SQLiteMigrationRunner(db_path=db_path)
    seeded = runner.apply_migrations(request_id="aw03-seed-final", ts_ms=10)
    assert seeded.success is True, seeded.as_dict()

    with sqlite3.connect(db_path) as conn:
        # Rewind to the pre-repair catalog. Later AW-03 migrations must be
        # replayed after 0075 instead of leaving an impossible history gap.
        _drop_post_0122_task_graph_admission_triggers(conn)
        conn.execute("DELETE FROM schema_migration_assets WHERE migration_id >= 75")
        conn.execute("DELETE FROM schema_migrations WHERE migration_id >= 75")
        conn.execute(
            "UPDATE schema_migrations SET checksum = ? WHERE migration_id = 72",
            (PLATFORM_REGISTRY_DRAFT_CHECKSUM,),
        )
        conn.execute(
            "UPDATE schema_migration_assets SET checksum = ? WHERE migration_id = 72",
            (PLATFORM_REGISTRY_DRAFT_CHECKSUM,),
        )
        conn.execute(
            "DROP TRIGGER IF EXISTS trg_vts_expression_mapping_no_insert_after_journal"
        )
        conn.commit()

    repaired = runner.apply_migrations(request_id="aw03-repair-draft", ts_ms=11)
    assert repaired.success is True, repaired.as_dict()
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT checksum FROM schema_migrations WHERE migration_id = 72"
        ).fetchone() == (PLATFORM_REGISTRY_FINAL_CHECKSUM,)
        assert conn.execute(
            "SELECT checksum FROM schema_migration_assets WHERE migration_id = 72"
        ).fetchone() == (PLATFORM_REGISTRY_FINAL_CHECKSUM,)
        assert conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'trigger' AND name = ?",
            ("trg_vts_expression_mapping_no_insert_after_journal",),
        ).fetchone() == ("trg_vts_expression_mapping_no_insert_after_journal",)

    strict_replay = runner.apply_migrations(request_id="aw03-strict-replay", ts_ms=12)
    assert strict_replay.success is True, strict_replay.as_dict()

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE schema_migrations SET checksum = ? WHERE migration_id = 72",
            ("f" * 64,),
        )
        conn.commit()
    drift = runner.apply_migrations(request_id="aw03-reject-drift", ts_ms=13)
    assert drift.success is False
    assert drift.reason_code == MIGRATION_SCHEMA_DRIFT


def test_platform_registry_strict_postcondition_rolls_back_wash_and_receipt(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "platform-registry-postcondition.sqlite3"
    runner = SQLiteMigrationRunner(db_path=db_path)
    seeded = runner.apply_migrations(request_id="aw03-seed-postcondition", ts_ms=14)
    assert seeded.success is True, seeded.as_dict()
    with sqlite3.connect(db_path) as conn:
        _drop_post_0122_task_graph_admission_triggers(conn)
        conn.execute("DELETE FROM schema_migration_assets WHERE migration_id >= 75")
        conn.execute("DELETE FROM schema_migrations WHERE migration_id >= 75")
        conn.execute(
            "UPDATE schema_migrations SET checksum = ? WHERE migration_id = 72",
            (PLATFORM_REGISTRY_DRAFT_CHECKSUM,),
        )
        conn.execute(
            "UPDATE schema_migration_assets SET checksum = ? WHERE migration_id = 72",
            (PLATFORM_REGISTRY_DRAFT_CHECKSUM,),
        )
        conn.execute(
            "DROP TRIGGER IF EXISTS trg_vts_expression_mapping_no_insert_after_journal"
        )
        conn.commit()

    real_postcondition = (
        migration_runner_module.platform_registry_schema_postcondition_holds
    )
    calls = 0

    def fail_strict_postcondition(conn, *, allow_missing_repair_trigger):  # noqa: ANN001, ANN201
        nonlocal calls
        calls += 1
        valid = real_postcondition(
            conn,
            allow_missing_repair_trigger=allow_missing_repair_trigger,
        )
        return False if calls == 3 else valid

    monkeypatch.setattr(
        migration_runner_module,
        "platform_registry_schema_postcondition_holds",
        fail_strict_postcondition,
    )
    rejected = runner.apply_migrations(
        request_id="aw03-fail-strict-postcondition",
        ts_ms=15,
    )

    assert rejected.success is False
    assert rejected.reason_code == "MIGRATION_EXEC_FAILED"
    assert rejected.records[-1].migration_id == "0075_platform_registry_catalog_repair"
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT checksum FROM schema_migrations WHERE migration_id = 72"
        ).fetchone() == (PLATFORM_REGISTRY_DRAFT_CHECKSUM,)
        assert conn.execute(
            "SELECT checksum FROM schema_migration_assets WHERE migration_id = 72"
        ).fetchone() == (PLATFORM_REGISTRY_DRAFT_CHECKSUM,)
        assert conn.execute(
            "SELECT 1 FROM schema_migrations WHERE migration_id = 75"
        ).fetchone() is None
        assert conn.execute(
            "SELECT 1 FROM schema_migration_assets WHERE migration_id = 75"
        ).fetchone() is None
        assert conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'trigger' AND name = ?",
            ("trg_vts_expression_mapping_no_insert_after_journal",),
        ).fetchone() is None


@pytest.mark.parametrize(
    "damage_sql",
    [
        "DROP TABLE mcp_tool_sync_journal",
        "ALTER TABLE mcp_tool_registry DROP COLUMN description",
        "DROP INDEX idx_mcp_registry_tool",
        "DROP TRIGGER trg_mcp_tool_sync_journal_no_delete",
    ],
    ids=["table", "column", "index", "trigger"],
)
def test_platform_registry_draft_repair_rejects_incomplete_physical_schema(
    tmp_path,
    damage_sql: str,
) -> None:
    db_path = tmp_path / "platform-registry-incomplete.sqlite3"
    runner = SQLiteMigrationRunner(db_path=db_path)
    seeded = runner.apply_migrations(request_id="aw03-seed-incomplete", ts_ms=20)
    assert seeded.success is True, seeded.as_dict()

    with sqlite3.connect(db_path) as conn:
        _drop_post_0122_task_graph_admission_triggers(conn)
        conn.execute("DELETE FROM schema_migration_assets WHERE migration_id >= 75")
        conn.execute("DELETE FROM schema_migrations WHERE migration_id >= 75")
        conn.execute(
            "UPDATE schema_migrations SET checksum = ? WHERE migration_id = 72",
            (PLATFORM_REGISTRY_DRAFT_CHECKSUM,),
        )
        conn.execute(
            "UPDATE schema_migration_assets SET checksum = ? WHERE migration_id = 72",
            (PLATFORM_REGISTRY_DRAFT_CHECKSUM,),
        )
        conn.execute(
            "DROP TRIGGER IF EXISTS trg_vts_expression_mapping_no_insert_after_journal"
        )
        conn.execute(damage_sql)
        conn.commit()

    rejected = runner.apply_migrations(
        request_id="aw03-reject-incomplete",
        ts_ms=21,
    )

    assert rejected.success is False
    assert rejected.reason_code == MIGRATION_SCHEMA_DRIFT
    assert "checksum-bound postcondition" in rejected.detail
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT checksum FROM schema_migrations WHERE migration_id = 72"
        ).fetchone() == (PLATFORM_REGISTRY_DRAFT_CHECKSUM,)
        assert conn.execute(
            "SELECT 1 FROM schema_migrations WHERE migration_id = 75"
        ).fetchone() is None


def test_0097_node_grant_uniqueness_fails_closed_on_historical_duplicates(
    tmp_path,
) -> None:
    db_path = tmp_path / "0097-node-grant-duplicates.sqlite3"
    head_96 = SQLiteMigrationRunner(
        db_path=db_path,
        migrations_dir=_migration_catalog_through(tmp_path, 96),
    ).apply_migrations(request_id="0097-head-96", ts_ms=50)
    assert head_96.success is True, head_96.as_dict()
    assert head_96.summary["current_version"] == 96

    with sqlite3.connect(db_path) as conn:
        for ts_ms in (51, 52):
            conn.execute(
                """
                INSERT INTO governor_log(
                    request_id, intent_id, decision, reason_code,
                    rule_trace, tokens, snapshot_refs, stage, ts_ms
                ) VALUES (?, ?, 'APPROVE', 'TEST', '[]', '{}', '{}', ?, ?)
                """,
                (
                    "request-0097-duplicate",
                    "intent-0097-duplicate",
                    "S_TASK_GRAPH_NODE_GRANT",
                    ts_ms,
                ),
            )
        conn.commit()

    rejected = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="0097-reject-duplicates",
        ts_ms=53,
    )

    assert rejected.success is False
    assert rejected.reason_code == "MIGRATION_EXEC_FAILED"
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT MAX(migration_id) FROM schema_migrations"
        ).fetchone() == (96,)
        assert conn.execute(
            """
            SELECT 1 FROM sqlite_master
            WHERE type = 'index'
              AND name = 'uq_governor_node_grant_request_intent'
            """
        ).fetchone() is None


def test_0097_fresh_schema_fences_node_grant_identity_and_duplicates(tmp_path) -> None:
    db_path = tmp_path / "0097-node-grant-fresh.sqlite3"
    result = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="0097-fresh",
        ts_ms=60,
    )
    assert result.success is True, result.as_dict()
    assert result.summary["current_version"] >= 102

    with sqlite3.connect(db_path) as conn:
        objects = {
            (str(row[0]), str(row[1]))
            for row in conn.execute(
                """
                SELECT type, name FROM sqlite_master
                WHERE name IN (
                    'uq_governor_node_grant_request_intent',
                    'trg_governor_node_grant_identity_insert',
                    'trg_governor_node_grant_identity_update'
                )
                """
            ).fetchall()
        }
        assert objects == {
            ("index", "uq_governor_node_grant_request_intent"),
            ("trigger", "trg_governor_node_grant_identity_insert"),
            ("trigger", "trg_governor_node_grant_identity_update"),
        }
        conn.execute(
            """
            INSERT INTO governor_log(
                request_id, intent_id, decision, reason_code,
                rule_trace, tokens, snapshot_refs, stage, ts_ms
            ) VALUES ('request-0097', 'intent-0097', 'APPROVE', 'TEST',
                      '[]', '{}', '{}', 'S_TASK_GRAPH_NODE_GRANT', 61)
            """
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO governor_log(
                    request_id, intent_id, decision, reason_code,
                    rule_trace, tokens, snapshot_refs, stage, ts_ms
                ) VALUES ('request-0097', 'intent-0097', 'APPROVE', 'TEST',
                          '[]', '{}', '{}', 'S_TASK_GRAPH_NODE_GRANT', 62)
                """
            )
