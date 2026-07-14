"""Bounded, read-only ledger windows for Life Body observability."""

from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import threading
from typing import Any, Iterator, Mapping

from host_brain.audit.ledger_constants import JSON_FIELDS_BY_TABLE


LIFE_BODY_QUERY_INVALID = "LIFE_BODY_QUERY_INVALID"
LIFE_BODY_QUERY_UNAVAILABLE = "LIFE_BODY_QUERY_UNAVAILABLE"
LIFE_BODY_GENERIC_TABLE_ALLOWLIST = frozenset(
    {
        "memory_ops_log",
        "bond_update_log",
        "scheduler_event_log",
    }
)
LIFE_BODY_EVENT_TABLE_ALLOWLIST = frozenset({"voice_event_log"})


class LifeBodyQueryRepositoryError(RuntimeError):
    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = str(reason_code or LIFE_BODY_QUERY_UNAVAILABLE)
        self.detail = str(detail or "Life Body ledger query failed")
        super().__init__(f"{self.reason_code}: {self.detail}")


def decode_life_body_row(table: str, row: Mapping[str, Any]) -> dict[str, Any]:
    """Preserve SQLiteLedger's JSON decoder contract without its connection."""
    data = dict(row)
    for key in JSON_FIELDS_BY_TABLE.get(str(table), set()):
        raw = data.get(key)
        if isinstance(raw, str) and raw:
            try:
                data[key] = json.loads(raw)
            except json.JSONDecodeError:
                data[key] = raw
    data.pop("id", None)
    return data


class LifeBodyQueryRepository:
    """Operation-scoped snapshots over a fixed observability table allowlist."""

    def __init__(
        self,
        canonical_db_path: str | Path,
        *,
        timeout_ms: int = 3_000,
        _borrowed_connection: sqlite3.Connection | None = None,
        _borrowed_lock: Any | None = None,
    ) -> None:
        raw_path = str(canonical_db_path or "").strip()
        if not raw_path or raw_path.lower().startswith("file:"):
            raise LifeBodyQueryRepositoryError(
                LIFE_BODY_QUERY_INVALID,
                "canonical database path is required",
            )
        if raw_path == ":memory:" and _borrowed_connection is None:
            raise LifeBodyQueryRepositoryError(
                LIFE_BODY_QUERY_INVALID,
                "in-memory queries require an adapter-owned borrowed connection",
            )
        self.canonical_db_path: str | Path = (
            raw_path if raw_path == ":memory:" else Path(raw_path).expanduser().resolve()
        )
        self._timeout_ms = max(1, int(timeout_ms))
        self._borrowed_connection = _borrowed_connection
        self._borrowed_lock = _borrowed_lock or threading.RLock()
        if isinstance(self.canonical_db_path, Path) and not self.canonical_db_path.is_file():
            raise LifeBodyQueryRepositoryError(
                LIFE_BODY_QUERY_UNAVAILABLE,
                f"canonical database does not exist: {self.canonical_db_path}",
            )

    @classmethod
    def from_ledger(cls, ledger: Any) -> LifeBodyQueryRepository:
        canonical_path = str(getattr(ledger, "canonical_db_path", "") or "").strip()
        if not canonical_path:
            raise LifeBodyQueryRepositoryError(
                LIFE_BODY_QUERY_INVALID,
                "ledger does not expose canonical_db_path",
            )
        if canonical_path != ":memory:":
            return cls(canonical_path)
        connection = getattr(ledger, "_conn", None)
        if connection is None:
            raise LifeBodyQueryRepositoryError(
                LIFE_BODY_QUERY_INVALID,
                "in-memory ledger does not expose a compatibility connection",
            )
        return cls(
            canonical_path,
            _borrowed_connection=connection,
            _borrowed_lock=getattr(ledger, "_write_lock", None) or threading.RLock(),
        )

    def _connect_read_only(self) -> sqlite3.Connection:
        assert isinstance(self.canonical_db_path, Path)
        try:
            conn = sqlite3.connect(
                self.canonical_db_path.as_uri() + "?mode=ro",
                uri=True,
                timeout=self._timeout_ms / 1000.0,
                isolation_level=None,
            )
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA query_only = ON")
            conn.execute(f"PRAGMA busy_timeout = {self._timeout_ms}")
            return conn
        except sqlite3.DatabaseError as exc:
            raise LifeBodyQueryRepositoryError(
                LIFE_BODY_QUERY_UNAVAILABLE,
                "canonical ledger could not be opened read-only",
            ) from exc

    @contextmanager
    def _snapshot(self) -> Iterator[sqlite3.Connection]:
        if self._borrowed_connection is not None:
            with self._borrowed_lock:
                started = not self._borrowed_connection.in_transaction
                if started:
                    self._borrowed_connection.execute("BEGIN")
                try:
                    yield self._borrowed_connection
                finally:
                    if started and self._borrowed_connection.in_transaction:
                        self._borrowed_connection.rollback()
            return
        conn = self._connect_read_only()
        try:
            conn.execute("BEGIN")
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def _limit(value: int) -> int:
        try:
            return max(1, min(int(value), 10_000))
        except (TypeError, ValueError) as exc:
            raise LifeBodyQueryRepositoryError(
                LIFE_BODY_QUERY_INVALID,
                "query limit must be an integer",
            ) from exc

    @staticmethod
    def _window(start_ms: int, end_ms: int) -> tuple[int, int]:
        try:
            start = int(start_ms)
            end = int(end_ms)
        except (TypeError, ValueError) as exc:
            raise LifeBodyQueryRepositoryError(
                LIFE_BODY_QUERY_INVALID,
                "query window must use integer timestamps",
            ) from exc
        if start < 0 or end < start:
            raise LifeBodyQueryRepositoryError(
                LIFE_BODY_QUERY_INVALID,
                "query window is invalid",
            )
        return start, end

    @staticmethod
    def _table_has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
        rows = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
        return str(column) in {str(row[1]) for row in rows}

    def read_generic_window(
        self,
        table: str,
        *,
        start_ms: int,
        end_ms: int,
        limit: int,
    ) -> list[dict[str, Any]]:
        resolved = str(table or "").strip()
        if resolved not in LIFE_BODY_GENERIC_TABLE_ALLOWLIST:
            raise LifeBodyQueryRepositoryError(
                LIFE_BODY_QUERY_INVALID,
                f"table is not allowed for Life Body queries: {resolved}",
            )
        start, end = self._window(start_ms, end_ms)
        row_limit = self._limit(limit)
        try:
            with self._snapshot() as conn:
                if self._table_has_column(conn, resolved, "ts_ms"):
                    rows = conn.execute(
                        f"""
                        SELECT * FROM "{resolved}"
                        WHERE ts_ms >= ? AND ts_ms <= ?
                        ORDER BY ts_ms DESC
                        LIMIT ?
                        """,
                        (start, end, row_limit),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        f'SELECT * FROM "{resolved}" ORDER BY rowid DESC LIMIT ?',
                        (row_limit,),
                    ).fetchall()
        except sqlite3.DatabaseError as exc:
            raise LifeBodyQueryRepositoryError(
                LIFE_BODY_QUERY_UNAVAILABLE,
                f"Life Body query failed for {resolved}",
            ) from exc
        return [dict(row) for row in rows]

    def read_event_type_window(
        self,
        table: str,
        *,
        start_ms: int,
        end_ms: int,
        exact_event_type: str,
        like_event_type: str,
        scan_limit: int,
    ) -> list[dict[str, Any]]:
        resolved = str(table or "").strip()
        if resolved not in LIFE_BODY_EVENT_TABLE_ALLOWLIST:
            raise LifeBodyQueryRepositoryError(
                LIFE_BODY_QUERY_INVALID,
                f"table is not allowed for event queries: {resolved}",
            )
        start, end = self._window(start_ms, end_ms)
        row_limit = self._limit(scan_limit)
        try:
            with self._snapshot() as conn:
                rows = conn.execute(
                    f"""
                    SELECT * FROM "{resolved}"
                    WHERE ts_ms >= ? AND ts_ms <= ?
                      AND (event_type = ? OR event_type LIKE ?)
                    ORDER BY ts_ms DESC
                    LIMIT ?
                    """,
                    (
                        start,
                        end,
                        str(exact_event_type),
                        str(like_event_type),
                        row_limit,
                    ),
                ).fetchall()
        except sqlite3.DatabaseError as exc:
            raise LifeBodyQueryRepositoryError(
                LIFE_BODY_QUERY_UNAVAILABLE,
                f"Life Body event query failed for {resolved}",
            ) from exc
        return [dict(row) for row in rows]


__all__ = [
    "LIFE_BODY_EVENT_TABLE_ALLOWLIST",
    "LIFE_BODY_GENERIC_TABLE_ALLOWLIST",
    "LIFE_BODY_QUERY_INVALID",
    "LIFE_BODY_QUERY_UNAVAILABLE",
    "LifeBodyQueryRepository",
    "LifeBodyQueryRepositoryError",
    "decode_life_body_row",
]
