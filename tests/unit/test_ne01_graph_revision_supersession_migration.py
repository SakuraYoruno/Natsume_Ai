from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest

from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.persistence.host_command_repository import (
    HOST_COMMAND_SCHEMA_NOT_READY,
    HOST_COMMAND_SUPERSESSION_MIGRATION_ASSET_KEY,
    HOST_COMMAND_SUPERSESSION_MIGRATION_SHA256,
    HostCommandRepository,
    HostCommandRepositoryError,
)


TABLE = "task_runtime_graph_revision_supersession"
INDEXES = {
    "uq_task_runtime_graph_supersession_previous",
    "uq_task_runtime_graph_supersession_replacement",
    "uq_task_runtime_graph_supersession_previous_command",
    "uq_task_runtime_graph_supersession_replacement_command",
}
TRIGGERS = {
    "trg_task_runtime_graph_supersession_binding_insert",
    "trg_task_runtime_graph_supersession_immutable_update",
    "trg_task_runtime_graph_supersession_immutable_delete",
}


def test_0100_catalog_checksum_and_append_only_schema_are_exact(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "graph-supersession-schema.sqlite3"
    result = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="ne01-graph-supersession-schema",
        ts_ms=100,
    )
    assert result.success is True, result.as_dict()
    assert result.summary["current_version"] >= 102

    asset_path = (
        Path(__file__).parents[2]
        / "host_brain"
        / "audit"
        / "migrations"
        / "0100_task_runtime_graph_revision_supersession.sql"
    )
    checksum = hashlib.sha256(
        asset_path.read_text(encoding="utf-8-sig").encode("utf-8")
    ).hexdigest()
    assert checksum == HOST_COMMAND_SUPERSESSION_MIGRATION_SHA256

    with sqlite3.connect(db_path) as conn:
        asset = conn.execute(
            """
            SELECT checksum, disposition FROM schema_migration_assets
            WHERE asset_key = ?
            """,
            (HOST_COMMAND_SUPERSESSION_MIGRATION_ASSET_KEY,),
        ).fetchone()
        columns = {
            row[1]: (row[2], row[3], row[5])
            for row in conn.execute(f"PRAGMA table_info('{TABLE}')")
        }
        indexes = {
            row[1] for row in conn.execute(f"PRAGMA index_list('{TABLE}')")
        }
        triggers = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'trigger' AND tbl_name = ?",
                (TABLE,),
            )
        }

    assert asset == (checksum, "APPLIED")
    assert set(columns) == {
        "supersession_id",
        "schema_version",
        "graph_id",
        "previous_revision",
        "replacement_revision",
        "previous_command_id",
        "replacement_command_id",
        "previous_graph_status_version",
        "failure_evidence_json",
        "failure_evidence_sha256",
        "created_ts_ms",
    }
    assert INDEXES <= indexes
    assert triggers == TRIGGERS


def test_host_command_readiness_rejects_same_name_weakened_trigger(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "graph-supersession-trigger-drift.sqlite3"
    result = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="ne01-graph-supersession-trigger-drift",
        ts_ms=100,
    )
    assert result.success is True, result.as_dict()
    HostCommandRepository(db_path).assert_schema_ready()

    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            DROP TRIGGER trg_task_runtime_graph_supersession_binding_insert;
            CREATE TRIGGER trg_task_runtime_graph_supersession_binding_insert
            BEFORE INSERT ON task_runtime_graph_revision_supersession
            BEGIN
                SELECT 1;
            END;
            """
        )

    with pytest.raises(HostCommandRepositoryError) as raised:
        HostCommandRepository(db_path)
    assert raised.value.reason_code == HOST_COMMAND_SCHEMA_NOT_READY
