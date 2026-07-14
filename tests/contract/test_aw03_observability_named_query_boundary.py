from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest

from host_brain.persistence.observability_named_query_repository import (
    ObservabilityNamedQueryRepository,
    ObservabilityNamedReadQuery,
)


ROOT = Path(__file__).resolve().parents[2]


class _Cursor:
    description = (("event_count",), ("status",))

    def fetchall(self) -> list[Any]:
        return [(3, "ok"), {"event_count": 1, "status": "degraded"}]


class _Connection:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def execute(self, sql: str, parameters: dict[str, Any]) -> _Cursor:
        self.calls.append((sql, parameters))
        return _Cursor()


def test_named_query_execution_and_row_projection_live_in_persistence() -> None:
    connection = _Connection()
    query = ObservabilityNamedReadQuery(
        name="event_summary",
        sql="SELECT event_count, status FROM event_summary WHERE ts_ms >= :start",
        parameters={"start": 100},
    )

    rows = ObservabilityNamedQueryRepository(connection).fetch_all(query)

    assert connection.calls == [(query.sql, {"start": 100})]
    assert rows == [
        {"event_count": 3, "status": "ok"},
        {"event_count": 1, "status": "degraded"},
    ]


@pytest.mark.parametrize(
    ("name", "sql"),
    [("", "SELECT 1"), ("missing_sql", "")],
)
def test_named_query_requires_identity_and_query_text(name: str, sql: str) -> None:
    with pytest.raises(ValueError):
        ObservabilityNamedReadQuery(name=name, sql=sql, parameters={})


def test_observability_builder_has_no_sqlite_execution_boundary() -> None:
    path = ROOT / "host_brain" / "observability" / "query_builder.py"
    source = path.read_text(encoding="utf-8-sig")
    tree = ast.parse(source, filename=str(path))

    assert "host_brain.persistence.observability_named_query_repository" in source
    assert not any(
        isinstance(node, (ast.Import, ast.ImportFrom))
        and (
            any(alias.name == "sqlite3" for alias in node.names)
            or getattr(node, "module", None) == "sqlite3"
        )
        for node in ast.walk(tree)
    )
    assert not any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"execute", "executemany", "executescript"}
        for node in ast.walk(tree)
    )
    assert not any(
        isinstance(node, ast.Attribute)
        and node.attr in {"_conn", "_write_lock"}
        for node in ast.walk(tree)
    )
