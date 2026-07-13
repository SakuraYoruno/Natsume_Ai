"""Versioned, migration-first schema runner for the standalone Edge store."""

from __future__ import annotations

from pathlib import Path
import sqlite3


MIGRATIONS_ROOT = Path(__file__).with_name("migrations")


def apply_edge_gateway_migrations(db_path: str | Path) -> tuple[str, ...]:
    path = Path(str(db_path)).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    applied: list[str] = []
    connection = sqlite3.connect(str(path), isolation_level=None)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        for migration in sorted(MIGRATIONS_ROOT.glob("[0-9][0-9][0-9][0-9]_*.sql")):
            connection.executescript(migration.read_text(encoding="utf-8-sig"))
            applied.append(migration.name)
    finally:
        connection.close()
    return tuple(applied)


__all__ = ["MIGRATIONS_ROOT", "apply_edge_gateway_migrations"]
