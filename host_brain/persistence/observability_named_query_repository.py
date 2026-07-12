"""Typed execution boundary for registry-validated observability named queries."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import sqlite3
from typing import Any, Protocol


class SQLiteReadCursor(Protocol):
    description: Sequence[Sequence[Any]] | None

    def fetchall(self) -> Sequence[Any]: ...


class SQLiteReadConnection(Protocol):
    """Small connection port required by the named-query repository."""

    def execute(
        self,
        sql: str,
        parameters: Mapping[str, Any],
    ) -> SQLiteReadCursor: ...


@dataclass(frozen=True, slots=True)
class ObservabilityNamedReadQuery:
    """Validated query text and bound parameters loaded from the registry."""

    name: str
    sql: str
    parameters: Mapping[str, Any]

    def __post_init__(self) -> None:
        if not str(self.name or "").strip():
            raise ValueError("named query name is required")
        if not str(self.sql or "").strip():
            raise ValueError("named query SQL is required")


class ObservabilityNamedQueryRepository:
    """Execute one typed named read through an injected SQLite connection."""

    def __init__(self, connection: SQLiteReadConnection) -> None:
        self._connection = connection

    def fetch_all(self, query: ObservabilityNamedReadQuery) -> list[dict[str, Any]]:
        cursor = self._connection.execute(query.sql, dict(query.parameters))
        columns = [str(column[0]) for column in (cursor.description or ())]
        rows: list[dict[str, Any]] = []
        for raw in cursor.fetchall():
            if isinstance(raw, sqlite3.Row):
                rows.append({key: raw[key] for key in raw.keys()})
            elif isinstance(raw, Mapping):
                rows.append(dict(raw))
            elif isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
                rows.append(dict(zip(columns, raw, strict=False)))
            elif columns:  # pragma: no cover - defensive driver compatibility
                rows.append({columns[0]: raw})
            else:  # pragma: no cover - malformed cursor contract
                rows.append({})
        return rows


__all__ = [
    "ObservabilityNamedQueryRepository",
    "ObservabilityNamedReadQuery",
    "SQLiteReadConnection",
    "SQLiteReadCursor",
]
