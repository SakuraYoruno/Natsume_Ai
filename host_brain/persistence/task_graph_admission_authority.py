"""Connection-local SQLite authority for canonical TaskGraph admission.

The database trigger introduced by migration 0124 calls a SQLite function that
is registered only by canonical Task Runtime repositories.  Registration alone
does not authorize a write: the current execution context must also hold the
exact graph/revision scope entered by :class:`HostCommandRepository`.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
import re
import sqlite3


TASK_GRAPH_ADMISSION_AUTHORITY_FUNCTION = (
    "natsume_task_graph_admission_authorized"
)
TASK_GRAPH_ADMISSION_AUTHORITY_MIGRATION_ID = 124
TASK_GRAPH_ADMISSION_AUTHORITY_MIGRATION_NAME = (
    "task_graph_admission_authority"
)
_GRAPH_TRIGGER = "trg_task_runtime_graph_admission_authority_insert"
_NODE_TRIGGER = "trg_task_runtime_node_admission_authority_insert"
_COMMAND_TRIGGER = "trg_host_command_journal_admission_authority_insert"
_AUTHORIZED_IDENTITIES: ContextVar[tuple[tuple[str, int], ...]] = ContextVar(
    "task_graph_admission_authorized_identities",
    default=(),
)

_TRIGGER_SQL = {
    _GRAPH_TRIGGER: """
        CREATE TRIGGER trg_task_runtime_graph_admission_authority_insert
        BEFORE INSERT ON task_runtime_graph
        BEGIN
            SELECT CASE
                WHEN NEW.source_kind <> 'CANONICAL' THEN
                    RAISE(ABORT, 'TASK_GRAPH_LEGACY_INSERT_RETIRED')
                WHEN natsume_task_graph_admission_authorized(
                    NEW.graph_id,
                    NEW.revision
                ) <> 1 THEN
                    RAISE(ABORT, 'TASK_GRAPH_ADMISSION_AUTHORITY_REQUIRED')
            END;
        END
    """,
    _NODE_TRIGGER: """
        CREATE TRIGGER trg_task_runtime_node_admission_authority_insert
        BEFORE INSERT ON task_runtime_node
        BEGIN
            SELECT CASE
                WHEN natsume_task_graph_admission_authorized(
                    NEW.graph_id,
                    NEW.graph_revision
                ) <> 1 THEN
                    RAISE(ABORT, 'TASK_NODE_ADMISSION_AUTHORITY_REQUIRED')
                WHEN NOT EXISTS (
                    SELECT 1
                    FROM task_runtime_graph AS graph
                    WHERE graph.graph_id = NEW.graph_id
                      AND graph.revision = NEW.graph_revision
                      AND graph.source_kind = 'CANONICAL'
                ) THEN
                    RAISE(ABORT, 'TASK_NODE_CANONICAL_GRAPH_REQUIRED')
                WHEN EXISTS (
                    SELECT 1
                    FROM host_command_journal AS admission
                    WHERE admission.graph_id = NEW.graph_id
                      AND admission.graph_revision = NEW.graph_revision
                ) THEN
                    RAISE(ABORT, 'TASK_NODE_ADMISSION_ALREADY_CLOSED')
            END;
        END
    """,
    _COMMAND_TRIGGER: """
        CREATE TRIGGER trg_host_command_journal_admission_authority_insert
        BEFORE INSERT ON host_command_journal
        BEGIN
            SELECT CASE
                WHEN natsume_task_graph_admission_authorized(
                    NEW.graph_id,
                    NEW.graph_revision
                ) <> 1 THEN
                    RAISE(ABORT, 'HOST_COMMAND_ADMISSION_AUTHORITY_REQUIRED')
                WHEN NOT EXISTS (
                    SELECT 1
                    FROM task_runtime_graph AS graph
                    WHERE graph.graph_id = NEW.graph_id
                      AND graph.revision = NEW.graph_revision
                      AND graph.source_kind = 'CANONICAL'
                      AND graph.root_work_order_id = NEW.root_work_order_id
                ) THEN
                    RAISE(ABORT, 'HOST_COMMAND_CANONICAL_GRAPH_REQUIRED')
            END;
        END
    """,
}


class TaskGraphAdmissionAuthoritySchemaError(RuntimeError):
    """Raised when migration 0124 or any admission trigger drifted."""


def _normalize_identity(
    graph_id: object,
    graph_revision: object,
) -> tuple[str, int] | None:
    graph = str(graph_id or "").strip()
    if not graph:
        return None
    try:
        revision = int(graph_revision)
    except (TypeError, ValueError):
        return None
    if revision < 1:
        return None
    return graph, revision


def _is_authorized(graph_id: object, graph_revision: object) -> int:
    identity = _normalize_identity(graph_id, graph_revision)
    return int(
        identity is not None
        and bool(_AUTHORIZED_IDENTITIES.get())
        and _AUTHORIZED_IDENTITIES.get()[-1] == identity
    )


def register_task_graph_admission_authority(
    conn: sqlite3.Connection,
) -> None:
    """Register the fail-closed trigger callback on one SQLite connection."""

    if not isinstance(conn, sqlite3.Connection):
        raise TypeError("task graph admission authority requires sqlite3.Connection")
    conn.create_function(
        TASK_GRAPH_ADMISSION_AUTHORITY_FUNCTION,
        2,
        _is_authorized,
        deterministic=False,
    )


@contextmanager
def canonical_task_graph_admission_scope(
    graph_id: object,
    graph_revision: object,
) -> Iterator[None]:
    """Authorize one exact graph revision for the current execution context."""

    identity = _normalize_identity(graph_id, graph_revision)
    if identity is None:
        raise ValueError("canonical TaskGraph admission identity is invalid")
    previous = _AUTHORIZED_IDENTITIES.get()
    token = _AUTHORIZED_IDENTITIES.set((*previous, identity))
    try:
        yield
    finally:
        _AUTHORIZED_IDENTITIES.reset(token)


def assert_task_graph_admission_authority_schema(
    conn: sqlite3.Connection,
) -> None:
    """Verify migration identity and the exact trigger responsibility."""

    migration = conn.execute(
        """
        SELECT name
        FROM schema_migrations
        WHERE migration_id = ?
        """,
        (TASK_GRAPH_ADMISSION_AUTHORITY_MIGRATION_ID,),
    ).fetchone()
    if (
        migration is None
        or str(migration[0])
        != TASK_GRAPH_ADMISSION_AUTHORITY_MIGRATION_NAME
    ):
        raise TaskGraphAdmissionAuthoritySchemaError(
            "task graph admission authority migration 0124 is missing"
        )

    triggers = {
        str(row[0]): str(row[1] or "")
        for row in conn.execute(
            """
            SELECT name, sql
            FROM sqlite_master
            WHERE type = 'trigger'
              AND name IN (?, ?, ?)
            """,
            (_GRAPH_TRIGGER, _NODE_TRIGGER, _COMMAND_TRIGGER),
        ).fetchall()
    }
    if set(triggers) != set(_TRIGGER_SQL):
        raise TaskGraphAdmissionAuthoritySchemaError(
            "task graph admission authority trigger set drifted"
        )
    for name, expected_sql in _TRIGGER_SQL.items():
        normalized = re.sub(r"\s+", "", triggers[name]).lower()
        expected = re.sub(r"\s+", "", expected_sql).lower()
        if normalized != expected:
            raise TaskGraphAdmissionAuthoritySchemaError(
                f"task graph admission authority trigger SQL drifted: {name}"
            )


__all__ = [
    "TASK_GRAPH_ADMISSION_AUTHORITY_FUNCTION",
    "TASK_GRAPH_ADMISSION_AUTHORITY_MIGRATION_ID",
    "TASK_GRAPH_ADMISSION_AUTHORITY_MIGRATION_NAME",
    "TaskGraphAdmissionAuthoritySchemaError",
    "assert_task_graph_admission_authority_schema",
    "canonical_task_graph_admission_scope",
    "register_task_graph_admission_authority",
]
