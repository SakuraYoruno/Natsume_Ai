from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.persistence.worker_projection_repository import (
    WORKER_PROJECTION_SCHEMA_NOT_READY,
    WorkerProjectionStore,
    WorkerProjectionStoreError,
)


MIGRATIONS = Path("host_brain/audit/migrations")


def test_0114_preserves_historical_projection_and_expands_tool_route(tmp_path: Path) -> None:
    db_path = tmp_path / "tool-projection-upgrade.sqlite3"
    with sqlite3.connect(db_path) as conn:
        conn.executescript((MIGRATIONS / "0086_task_runtime_worker_projection.sql").read_text("utf-8"))
        conn.execute(
            """
            INSERT INTO task_runtime_worker_projection(
                projection_id, schema_version, payload_digest, command_id,
                request_id, graph_id, graph_revision, node_id, attempt_id,
                route_type, target_id, decision, projection_json, created_ts_ms
            ) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, 'chat', ?, 'APPROVE', ?, 100)
            """,
            (
                "worker-projection://historical/chat",
                "Natsume.task_runtime.worker_projection.v1",
                "sha256:" + "1" * 64,
                "host-command://chat/historical",
                "request-historical",
                "graph-historical",
                "node-historical",
                "attempt-historical",
                "surface_reply",
                "{}",
            ),
        )
        conn.executescript((MIGRATIONS / "0114_task_runtime_tool_worker_projection.sql").read_text("utf-8"))
        historical = conn.execute(
            "SELECT projection_id, route_type FROM task_runtime_worker_projection"
        ).fetchall()
        assert historical == [("worker-projection://historical/chat", "chat")]
        conn.execute(
            """
            INSERT INTO task_runtime_worker_projection(
                projection_id, schema_version, payload_digest, command_id,
                request_id, graph_id, graph_revision, node_id, attempt_id,
                route_type, target_id, decision, projection_json, created_ts_ms
            ) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, 'tool', ?, 'APPROVE', ?, 101)
            """,
            (
                "worker-projection://tool/new",
                "Natsume.task_runtime.worker_projection.v1",
                "sha256:" + "2" * 64,
                "host-command://tool/new",
                "request-tool-new",
                "graph-tool-new",
                "node-tool-new",
                "attempt-tool-new",
                "read_status",
                "{}",
            ),
        )
    WorkerProjectionStore(db_path).assert_schema_ready()


def test_current_catalog_preserves_0114_worker_projection_readiness(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "tool-projection-current.sqlite3"
    result = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="ne01-tool-projection-through-current-head"
    )
    assert result.success is True, result.as_dict()
    assert result.summary["current_version"] == 120
    WorkerProjectionStore(db_path).assert_schema_ready()


def test_worker_projection_readiness_rejects_weakened_tool_guard(tmp_path: Path) -> None:
    db_path = tmp_path / "tool-projection-weakened.sqlite3"
    result = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="ne01-tool-projection-weakened"
    )
    assert result.success is True, result.as_dict()
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA writable_schema = ON")
        sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='task_runtime_worker_projection'"
        ).fetchone()[0]
        conn.execute(
            "UPDATE sqlite_master SET sql = ? WHERE type='table' AND name='task_runtime_worker_projection'",
            (sql.replace("'chat', 'skill', 'delegate', 'tool'", "'chat', 'skill', 'delegate', 'tool', 'other'"),),
        )
        conn.execute("PRAGMA writable_schema = OFF")
    with pytest.raises(WorkerProjectionStoreError) as raised:
        WorkerProjectionStore(db_path)
    assert raised.value.reason_code == WORKER_PROJECTION_SCHEMA_NOT_READY
