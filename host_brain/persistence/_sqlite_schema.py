"""Shared, read-only SQLite schema inspection helpers.

This module intentionally owns no connection or transaction lifecycle. Repository
modules retain those boundaries and use these helpers only to inspect existing
schema metadata.
"""

from __future__ import annotations

import sqlite3


def columns(conn: sqlite3.Connection, table: str) -> dict[str, sqlite3.Row]:
    rows = conn.execute(f"PRAGMA table_info({_pragma_literal(table)})").fetchall()
    return {str(row["name"]): row for row in rows}


def schema_sql(conn: sqlite3.Connection, object_type: str, name: str) -> str:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = ? AND name = ?",
        (object_type, name),
    ).fetchone()
    return "" if row is None or row["sql"] is None else str(row["sql"])


def schema_has_fragments(sql: str, fragments: frozenset[str]) -> bool:
    normalized = normalize_sql(sql)
    return bool(normalized) and all(fragment in normalized for fragment in fragments)


def named_index_matches(
    conn: sqlite3.Connection,
    table: str,
    name: str,
    expected_columns: tuple[str, ...],
) -> bool:
    rows = conn.execute(f"PRAGMA index_list({_pragma_literal(table)})").fetchall()
    row = next((item for item in rows if str(item["name"]) == name), None)
    if row is None or int(row["unique"]) != 0:
        return False
    return index_columns(conn, name) == expected_columns


def index_columns(conn: sqlite3.Connection, name: str) -> tuple[str, ...]:
    rows = conn.execute(f"PRAGMA index_info({_pragma_literal(name)})").fetchall()
    return tuple(str(row["name"]) for row in sorted(rows, key=lambda row: int(row["seqno"])))


def normalize_sql(value: str) -> str:
    return "".join(str(value or "").upper().split())


def _pragma_literal(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"
