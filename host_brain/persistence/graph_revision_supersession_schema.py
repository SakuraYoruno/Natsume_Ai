"""Physical schema validator for graph revision supersession authority."""

from __future__ import annotations

import re
import sqlite3


TABLE = "task_runtime_graph_revision_supersession"

_COLUMN_METADATA = {
    "supersession_id": ("TEXT", 1, None, 1),
    "schema_version": ("TEXT", 1, None, 0),
    "graph_id": ("TEXT", 1, None, 0),
    "previous_revision": ("INTEGER", 1, None, 0),
    "replacement_revision": ("INTEGER", 1, None, 0),
    "previous_command_id": ("TEXT", 1, None, 0),
    "replacement_command_id": ("TEXT", 1, None, 0),
    "previous_graph_status_version": ("INTEGER", 1, None, 0),
    "failure_evidence_json": ("TEXT", 1, None, 0),
    "failure_evidence_sha256": ("TEXT", 1, None, 0),
    "created_ts_ms": ("INTEGER", 1, None, 0),
}
_INDEX_COLUMNS = {
    "uq_task_runtime_graph_supersession_previous": (
        "graph_id",
        "previous_revision",
    ),
    "uq_task_runtime_graph_supersession_replacement": (
        "graph_id",
        "replacement_revision",
    ),
    "uq_task_runtime_graph_supersession_previous_command": (
        "previous_command_id",
    ),
    "uq_task_runtime_graph_supersession_replacement_command": (
        "replacement_command_id",
    ),
}
_FOREIGN_KEYS = {
    (
        "task_runtime_graph",
        (("graph_id", "graph_id"), ("previous_revision", "revision")),
        "NO ACTION",
        "RESTRICT",
        "NONE",
    ),
    (
        "task_runtime_graph",
        (("graph_id", "graph_id"), ("replacement_revision", "revision")),
        "NO ACTION",
        "RESTRICT",
        "NONE",
    ),
    (
        "host_command_journal",
        (("previous_command_id", "command_id"),),
        "NO ACTION",
        "RESTRICT",
        "NONE",
    ),
    (
        "host_command_journal",
        (("replacement_command_id", "command_id"),),
        "NO ACTION",
        "RESTRICT",
        "NONE",
    ),
}
_TRIGGER_FRAGMENTS = {
    "trg_task_runtime_graph_supersession_binding_insert": (
        "beforeinsertontask_runtime_graph_revision_supersession",
        "predecessor.status='failed'",
        "predecessor.status_version=new.previous_graph_status_version",
        "replacement.status='created'",
        "replacement.status_version=0",
        "replacement.root_work_order_id=predecessor.root_work_order_id",
        "raise(abort,'graphrevisionsupersessionbindingmismatch')",
    ),
    "trg_task_runtime_graph_supersession_immutable_update": (
        "beforeupdateontask_runtime_graph_revision_supersession",
        "raise(abort,'graphrevisionsupersessionisappend-only')",
    ),
    "trg_task_runtime_graph_supersession_immutable_delete": (
        "beforedeleteontask_runtime_graph_revision_supersession",
        "raise(abort,'graphrevisionsupersessionisappend-only')",
    ),
}
_TABLE_SQL_FRAGMENTS = (
    "schema_version='natsume.task_runtime.graph_revision_supersession.v1'",
    "replacement_revision=previous_revision+1",
    "replacement_command_id<>previous_command_id",
    "json_type(failure_evidence_json)='array'",
    "json_array_length(failure_evidence_json)between1and64",
    "length(failure_evidence_sha256)=71",
    "substr(failure_evidence_sha256,1,7)='sha256:'",
)


class GraphRevisionSupersessionSchemaError(RuntimeError):
    pass


def assert_graph_revision_supersession_schema(conn: sqlite3.Connection) -> None:
    table = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
        (TABLE,),
    ).fetchone()
    if table is None or not str(table[0] or "").strip():
        raise GraphRevisionSupersessionSchemaError(
            "graph revision supersession table is missing"
        )

    columns = {
        str(row[1]): (str(row[2]).upper(), int(row[3]), row[4], int(row[5]))
        for row in conn.execute(f"PRAGMA table_info('{TABLE}')").fetchall()
    }
    if columns != _COLUMN_METADATA:
        raise GraphRevisionSupersessionSchemaError(
            "graph revision supersession column metadata drifted"
        )

    indexes: dict[str, tuple[int, tuple[str, ...]]] = {}
    for row in conn.execute(f"PRAGMA index_list('{TABLE}')").fetchall():
        name = str(row[1])
        if name.startswith("sqlite_autoindex_"):
            continue
        indexes[name] = (
            int(row[2]),
            tuple(
                str(column[2])
                for column in conn.execute(f"PRAGMA index_info('{name}')").fetchall()
            ),
        )
    expected_indexes = {
        name: (1, columns) for name, columns in _INDEX_COLUMNS.items()
    }
    if indexes != expected_indexes:
        raise GraphRevisionSupersessionSchemaError(
            "graph revision supersession unique indexes drifted"
        )

    grouped: dict[int, list[sqlite3.Row]] = {}
    for row in conn.execute(f"PRAGMA foreign_key_list('{TABLE}')").fetchall():
        grouped.setdefault(int(row[0]), []).append(row)
    foreign_keys = {
        (
            str(rows[0][2]),
            tuple(
                (str(row[3]), str(row[4]))
                for row in sorted(rows, key=lambda item: int(item[1]))
            ),
            str(rows[0][5]),
            str(rows[0][6]),
            str(rows[0][7]),
        )
        for rows in grouped.values()
    }
    if foreign_keys != _FOREIGN_KEYS:
        raise GraphRevisionSupersessionSchemaError(
            "graph revision supersession foreign keys drifted"
        )

    triggers = {
        str(row[0]): str(row[1] or "")
        for row in conn.execute(
            "SELECT name, sql FROM sqlite_master WHERE type = 'trigger' AND tbl_name = ?",
            (TABLE,),
        ).fetchall()
    }
    if set(triggers) != set(_TRIGGER_FRAGMENTS):
        raise GraphRevisionSupersessionSchemaError(
            "graph revision supersession trigger set drifted"
        )
    for name, fragments in _TRIGGER_FRAGMENTS.items():
        normalized = _normalize_sql(triggers[name])
        if any(fragment not in normalized for fragment in fragments):
            raise GraphRevisionSupersessionSchemaError(
                f"graph revision supersession trigger SQL drifted: {name}"
            )

    normalized_table = _normalize_sql(str(table[0]))
    if any(fragment not in normalized_table for fragment in _TABLE_SQL_FRAGMENTS):
        raise GraphRevisionSupersessionSchemaError(
            "graph revision supersession table checks drifted"
        )


def _normalize_sql(value: str) -> str:
    return re.sub(r"\s+", "", str(value or "")).lower()


__all__ = [
    "GraphRevisionSupersessionSchemaError",
    "TABLE",
    "assert_graph_revision_supersession_schema",
]
