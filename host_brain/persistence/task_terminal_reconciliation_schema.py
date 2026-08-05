"""Canonical migration-0099 schema attestation.

Object names and column lists are insufficient: a same-named no-op trigger or
an index over the wrong columns must never make terminal reconciliation ready.
The trusted migration asset is installed into an isolated in-memory database,
then its canonical ``sqlite_master`` definitions are compared with the live
database for all three tables, four indexes and every 0099 trigger.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
import sqlite3
from typing import TypeAlias

from host_brain.persistence.task_store_types import (
    TASK_STORE_SCHEMA_NOT_READY,
    TaskStoreError,
)


_TABLES = frozenset(
    {
        "task_runtime_terminal_reconciliation_case",
        "task_runtime_terminal_reconciliation_proof",
        "task_runtime_terminal_reconciliation_supersession",
    }
)
_INDEXES = frozenset(
    {
        "idx_task_runtime_terminal_case_graph",
        "idx_task_runtime_terminal_case_classification",
        "idx_task_runtime_terminal_proof_authority",
        "idx_task_runtime_terminal_supersession_replacement",
    }
)
_TRIGGERS = frozenset(
    {
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
)
_EXPECTED_NAMES = _TABLES | _INDEXES | _TRIGGERS

SchemaIdentity: TypeAlias = tuple[str, str]
SchemaDefinition: TypeAlias = tuple[str, str]


def assert_terminal_reconciliation_schema(conn: sqlite3.Connection) -> None:
    """Fail closed unless terminal objects match a frozen canonical schema."""

    expected = _canonical_migration_schema()
    actual = _read_terminal_schema(conn)
    if actual in (expected, _canonical_legacy_migration_schema()):
        return
    missing = sorted(set(expected) - set(actual))
    unexpected = sorted(set(actual) - set(expected))
    drifted = sorted(
        f"{kind}:{name}"
        for (kind, name), definition in expected.items()
        if actual.get((kind, name)) != definition
    )
    if missing or unexpected or drifted:
        raise TaskStoreError(
            TASK_STORE_SCHEMA_NOT_READY,
            "task runtime terminal reconciliation migration 0099/0153 schema drifted: "
            f"missing={_labels(missing)}, unexpected={_labels(unexpected)}, "
            f"definitions={drifted}",
        )


@lru_cache(maxsize=1)
def _canonical_migration_schema() -> dict[SchemaIdentity, SchemaDefinition]:
    migrations_dir = (
        Path(__file__).resolve().parents[1]
        / "audit"
        / "migrations"
    )
    try:
        migration_sql = (
            migrations_dir / "0099_task_runtime_terminal_reconciliation.sql"
        ).read_text(encoding="utf-8")
        correction_sql = (
            migrations_dir
            / "0153_terminal_prebudget_cancellation_classifier_correction.sql"
        ).read_text(encoding="utf-8")
        with sqlite3.connect(":memory:") as conn:
            conn.executescript(migration_sql)
            conn.executescript(correction_sql)
            expected = _read_terminal_schema(conn)
    except (OSError, sqlite3.DatabaseError) as exc:
        raise TaskStoreError(
            TASK_STORE_SCHEMA_NOT_READY,
            "canonical terminal reconciliation migration asset is unavailable",
        ) from exc
    actual_names = {name for _, name in expected}
    if actual_names != _EXPECTED_NAMES:
        missing = sorted(_EXPECTED_NAMES - actual_names)
        unexpected = sorted(actual_names - _EXPECTED_NAMES)
        raise TaskStoreError(
            TASK_STORE_SCHEMA_NOT_READY,
            "canonical terminal reconciliation migration object set is invalid: "
            f"missing={missing}, unexpected={unexpected}",
        )
    return expected


@lru_cache(maxsize=1)
def _canonical_legacy_migration_schema() -> dict[SchemaIdentity, SchemaDefinition]:
    """Keep old catalog-prefix databases readable without weakening drift checks."""

    migration_path = (
        Path(__file__).resolve().parents[1]
        / "audit"
        / "migrations"
        / "0099_task_runtime_terminal_reconciliation.sql"
    )
    try:
        with sqlite3.connect(":memory:") as conn:
            conn.executescript(migration_path.read_text(encoding="utf-8"))
            expected = _read_terminal_schema(conn)
    except (OSError, sqlite3.DatabaseError) as exc:
        raise TaskStoreError(
            TASK_STORE_SCHEMA_NOT_READY,
            "canonical legacy terminal reconciliation migration is unavailable",
        ) from exc
    return expected


def _read_terminal_schema(
    conn: sqlite3.Connection,
) -> dict[SchemaIdentity, SchemaDefinition]:
    rows = conn.execute(
        """
        SELECT type, name, tbl_name, sql
        FROM sqlite_master
        WHERE (type = 'table'
               AND name LIKE 'task_runtime_terminal_reconciliation_%')
           OR (type = 'index' AND name LIKE 'idx_task_runtime_terminal_%')
           OR (type = 'trigger' AND name LIKE 'trg_task_runtime_terminal_%')
        ORDER BY type, name
        """
    ).fetchall()
    result: dict[SchemaIdentity, SchemaDefinition] = {}
    for row in rows:
        kind = str(row[0])
        name = str(row[1])
        table_name = str(row[2])
        sql = row[3]
        result[(kind, name)] = (
            table_name,
            _normalize_sql(str(sql)) if sql is not None else "",
        )
    return result


def _normalize_sql(value: str) -> str:
    # Both expected and live definitions are emitted by the same SQLite
    # runtime.  Whitespace is non-authoritative; every SQL token remains.
    return " ".join(value.split())


def _labels(values: list[SchemaIdentity]) -> list[str]:
    return [f"{kind}:{name}" for kind, name in values]


__all__ = ["assert_terminal_reconciliation_schema"]
