"""LedgerFetchMixin：SQLiteLedger 通用查询、写入与编解码方法集合。"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from typing import Any

from host_brain.audit.ledger_constants import (
    _ALLOWED_TABLES,
    JSON_FIELDS_BY_TABLE,
    WRITE_RETRY_DELAY_S,
    WRITE_RETRY_MAX,
)
from host_brain.core.errors import AUDIT_TABLE_NAME_INVALID

logger = logging.getLogger(__name__)


class LedgerFetchMixin:
    """通用 fetch/write/decode 方法 Mixin，需要混入 SQLiteLedger 才能访问 self._conn 等属性。"""

    _conn: sqlite3.Connection
    _column_cache: dict[str, set[str]]
    _column_cache_lock: threading.Lock
    _table_name_cache: set[str] | None
    _write_lock: threading.Lock

    def _time_column(self, table: str) -> str | None:
        """Return the canonical public timestamp used by a durable table.

        Newer immutable journals use ``created_at_ms`` or ``created_ts_ms``
        instead of the legacy ``ts_ms`` convention.  Treating those tables as
        timeless makes a bounded projection read the oldest rows first and can
        silently omit recent facts once the row cap is reached.
        """

        for column in (
            "ts_ms",
            "created_at_ms",
            "created_ts_ms",
            "updated_ts_ms",
            "committed_at_ms",
        ):
            if self._table_has_column(table, column):
                return column
        return None

    def _insert(self, table: str, payload: dict[str, Any]) -> None:
        keys = list(payload.keys())
        if not keys:
            return

        serialized = [self._encode_value(table, key, payload[key]) for key in keys]
        placeholders = ", ".join("?" for _ in keys)
        columns = ", ".join(keys)
        sql = f"INSERT INTO {table} ({columns}) VALUES ({placeholders})"

        last_exc: Exception | None = None
        for attempt in range(WRITE_RETRY_MAX):
            try:
                with self._write_lock:
                    with self._conn:
                        self._conn.execute(sql, serialized)
                return
            except sqlite3.OperationalError as exc:
                if "database is locked" in str(exc).lower() and attempt < WRITE_RETRY_MAX - 1:
                    last_exc = exc
                    time.sleep(WRITE_RETRY_DELAY_S * (attempt + 1))
                    continue
                raise
        if last_exc is not None:
            raise last_exc

    def _insert_many_atomic(
        self,
        table: str,
        payloads: list[dict[str, Any]],
    ) -> None:
        """Append a batch in one SQLite transaction or append none of it."""

        prepared: list[tuple[str, list[Any]]] = []
        for payload in payloads:
            keys = list(payload)
            if not keys:
                raise ValueError("atomic audit batch contains an empty payload")
            serialized = [
                self._encode_value(table, key, payload[key]) for key in keys
            ]
            placeholders = ", ".join("?" for _ in keys)
            columns = ", ".join(keys)
            prepared.append(
                (
                    f"INSERT INTO {table} ({columns}) VALUES ({placeholders})",
                    serialized,
                )
            )
        if not prepared:
            return
        last_exc: Exception | None = None
        for attempt in range(WRITE_RETRY_MAX):
            try:
                with self._write_lock:
                    with self._conn:
                        for sql, serialized in prepared:
                            self._conn.execute(sql, serialized)
                return
            except sqlite3.OperationalError as exc:
                if (
                    "database is locked" in str(exc).lower()
                    and attempt < WRITE_RETRY_MAX - 1
                ):
                    last_exc = exc
                    time.sleep(WRITE_RETRY_DELAY_S * (attempt + 1))
                    continue
                raise
        if last_exc is not None:
            raise last_exc

    def _insert_governor_batch_once(
        self,
        payloads: list[dict[str, Any]],
    ) -> bool:
        """Atomically append one deterministic node-grant batch once.

        ``NodeBoundGrantService`` performs an optimistic replay read before
        minting credentials.  That read cannot serialize two processes.  This
        writer transaction therefore acquires ``BEGIN IMMEDIATE`` first and
        rechecks the deterministic request/stage/intent identity before any
        row is inserted.  ``False`` tells the losing process to reload the
        winner's exact durable batch.
        """

        if not payloads:
            raise ValueError("node-grant governor batch must not be empty")
        request_ids = {str(row.get("request_id") or "").strip() for row in payloads}
        stages = {str(row.get("stage") or "").strip() for row in payloads}
        intent_ids = [str(row.get("intent_id") or "").strip() for row in payloads]
        if (
            len(request_ids) != 1
            or "" in request_ids
            or len(stages) != 1
            or "" in stages
            or any(not intent_id for intent_id in intent_ids)
            or len(set(intent_ids)) != len(intent_ids)
        ):
            raise ValueError(
                "node-grant governor batch requires one request/stage and unique intents"
            )

        prepared: list[tuple[str, list[Any]]] = []
        for payload in payloads:
            keys = list(payload)
            serialized = [
                self._encode_value("governor_log", key, payload[key]) for key in keys
            ]
            placeholders = ", ".join("?" for _ in keys)
            columns = ", ".join(keys)
            prepared.append(
                (
                    f"INSERT INTO governor_log ({columns}) VALUES ({placeholders})",
                    serialized,
                )
            )

        request_id = next(iter(request_ids))
        stage = next(iter(stages))
        guard_placeholders = ", ".join("?" for _ in intent_ids)
        guard_sql = (
            "SELECT intent_id FROM governor_log "
            "WHERE request_id = ? AND stage = ? "
            f"AND intent_id IN ({guard_placeholders}) LIMIT 1"
        )
        guard_params = [request_id, stage, *intent_ids]
        last_exc: Exception | None = None
        for attempt in range(WRITE_RETRY_MAX):
            try:
                with self._write_lock:
                    if self._conn.in_transaction:
                        raise RuntimeError(
                            "node-grant batch append requires an independent transaction"
                        )
                    try:
                        self._conn.execute("BEGIN IMMEDIATE")
                        existing = self._conn.execute(
                            guard_sql,
                            guard_params,
                        ).fetchone()
                        if existing is not None:
                            self._conn.rollback()
                            return False
                        for sql, serialized in prepared:
                            self._conn.execute(sql, serialized)
                        self._conn.commit()
                        return True
                    except BaseException:
                        if self._conn.in_transaction:
                            self._conn.rollback()
                        raise
            except sqlite3.OperationalError as exc:
                if (
                    "database is locked" in str(exc).lower()
                    and attempt < WRITE_RETRY_MAX - 1
                ):
                    last_exc = exc
                    time.sleep(WRITE_RETRY_DELAY_S * (attempt + 1))
                    continue
                raise
        if last_exc is not None:
            raise last_exc
        raise RuntimeError("node-grant batch append exhausted without a result")

    def fetch_by_request_id(self, table: str, request_id: str) -> list[dict[str, Any]]:
        """按 request_id 直接过滤查询（SQL 级，BUG-BE-002 修复）。"""
        if table not in _ALLOWED_TABLES:
            raise ValueError(
                f"Invalid table name: {table!r} (reason_code={AUDIT_TABLE_NAME_INVALID})"
            )
        rows = self._conn.execute(
            f"SELECT * FROM {table} WHERE request_id = ? ORDER BY id ASC",
            (str(request_id),),
        ).fetchall()
        return [self._decode_row(table, row) for row in rows]

    def fetch_by_time_range(
        self,
        table: str,
        *,
        start_ms: int | None = None,
        end_ms: int | None = None,
        limit: int = 100,
        offset: int = 0,
        descending: bool = False,
    ) -> list[dict[str, Any]]:
        """Fetch rows from a time window with deterministic pagination.

        ``descending=True`` is used by bounded projections that need the most
        recent rows rather than the oldest rows in a large retention window.
        A stable row identity is always included after the timestamp so rows
        sharing one millisecond cannot move between pages nondeterministically.
        """
        if table not in _ALLOWED_TABLES:
            logger.warning(
                "Rejected fetch_by_time_range for invalid table name: %s (reason_code=%s)",
                table,
                AUDIT_TABLE_NAME_INVALID,
            )
            raise ValueError(
                f"Invalid table name: {table!r} (reason_code={AUDIT_TABLE_NAME_INVALID})"
            )

        limit = max(1, min(limit, 10_000))
        offset = max(0, offset)

        time_column = self._time_column(table)
        has_id = self._table_has_column(table, "id")
        direction = "DESC" if bool(descending) else "ASC"

        def _order_clause() -> str:
            identity_column = "id" if has_id else "rowid"
            if time_column:
                if identity_column == time_column:
                    return f"{time_column} {direction}"
                return (
                    f"{time_column} {direction}, "
                    f"{identity_column} {direction}"
                )
            return f"{identity_column} {direction}"

        if time_column and (start_ms is not None or end_ms is not None):
            conditions: list[str] = []
            params: list[Any] = []
            if start_ms is not None:
                conditions.append(f"{time_column} >= ?")
                params.append(int(start_ms))
            if end_ms is not None:
                conditions.append(f"{time_column} <= ?")
                params.append(int(end_ms))
            where_clause = " AND ".join(conditions)
            order = _order_clause()
            sql = f"SELECT * FROM {table} WHERE {where_clause} ORDER BY {order} LIMIT ? OFFSET ?"
            params.extend([limit, offset])
            rows = self._conn.execute(sql, params).fetchall()
        else:
            order = _order_clause()
            sql = f"SELECT * FROM {table} ORDER BY {order} LIMIT ? OFFSET ?"
            rows = self._conn.execute(sql, [limit, offset]).fetchall()

        return [self._decode_row(table, row) for row in rows]

    def count_by_time_range(
        self,
        table: str,
        *,
        start_ms: int | None = None,
        end_ms: int | None = None,
    ) -> int:
        """Count rows in a table by optional ts_ms window."""
        if table not in _ALLOWED_TABLES:
            raise ValueError(
                f"Invalid table name: {table!r} (reason_code={AUDIT_TABLE_NAME_INVALID})"
            )
        time_column = self._time_column(table)
        if start_ms is None and end_ms is None:
            row = self._conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
        elif time_column is None:
            return 0
        else:
            conditions: list[str] = []
            params: list[Any] = []
            if start_ms is not None:
                conditions.append(f"{time_column} >= ?")
                params.append(int(start_ms))
            if end_ms is not None:
                conditions.append(f"{time_column} <= ?")
                params.append(int(end_ms))
            where_clause = " AND ".join(conditions)
            row = self._conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE {where_clause}", params
            ).fetchone()
        return int(row[0]) if row else 0

    def _fetch_all(self, table: str) -> list[dict[str, Any]]:
        if table not in _ALLOWED_TABLES:
            logger.warning(
                "Rejected _fetch_all for invalid table name: %s (reason_code=%s)",
                table,
                AUDIT_TABLE_NAME_INVALID,
            )
            raise ValueError(
                f"Invalid table name: {table!r} (reason_code={AUDIT_TABLE_NAME_INVALID})"
            )
        if self._table_has_column(table, "id"):
            rows = self._conn.execute(f"SELECT * FROM {table} ORDER BY id ASC").fetchall()
        elif self._table_has_column(table, "doc_id"):
            rows = self._conn.execute(f"SELECT * FROM {table} ORDER BY doc_id ASC").fetchall()
        else:
            rows = self._conn.execute(f"SELECT * FROM {table}").fetchall()
        return [self._decode_row(table, row) for row in rows]

    def _fetch_by_request(self, table: str, request_id: str) -> list[dict[str, Any]]:
        if table not in _ALLOWED_TABLES:
            raise ValueError(
                f"Invalid table name: {table!r} (reason_code={AUDIT_TABLE_NAME_INVALID})"
            )
        rows = self._conn.execute(
            f"SELECT * FROM {table} WHERE request_id = ? ORDER BY id ASC",
            (request_id,),
        ).fetchall()
        return [self._decode_row(table, row) for row in rows]

    def _encode_value(self, table: str, key: str, value: Any) -> Any:
        if key in JSON_FIELDS_BY_TABLE.get(table, set()):
            if value is None:
                return None
            return json.dumps(value, ensure_ascii=False, sort_keys=True)
        return value

    def _decode_row(self, table: str, row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        for key in JSON_FIELDS_BY_TABLE.get(table, set()):
            raw = data.get(key)
            if isinstance(raw, str) and raw:
                try:
                    data[key] = json.loads(raw)
                except json.JSONDecodeError:
                    data[key] = raw
        data.pop("id", None)
        return data

    def _table_has_column(self, table: str, column: str) -> bool:
        cached = self._column_cache.get(table)
        if cached is None:
            with self._column_cache_lock:
                cached = self._column_cache.get(table)
                if cached is None:
                    rows = self._conn.execute(f"PRAGMA table_info({table})").fetchall()
                    cached = {str(row["name"]) for row in rows}
                    self._column_cache[table] = cached
        return cached is not None and column in cached

    def _get_table_names(self) -> set[str]:
        cached = self._table_name_cache
        if cached is None:
            with self._column_cache_lock:
                cached = self._table_name_cache
                if cached is None:
                    rows = self._conn.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    ).fetchall()
                    cached = {
                        str(row["name"]) if isinstance(row, sqlite3.Row) else str(row[0])
                        for row in rows
                    }
                    self._table_name_cache = cached
        return set(cached)

    def table_exists(self, table: str) -> bool:
        return str(table or "").strip() in self._get_table_names()

    def can_fetch_table(self, table: str) -> bool:
        normalized = str(table or "").strip()
        return bool(normalized) and normalized in _ALLOWED_TABLES and self.table_exists(normalized)

    def resolve_fetch_table(self, *candidates: str) -> str | None:
        for candidate in candidates:
            normalized = str(candidate or "").strip()
            if self.can_fetch_table(normalized):
                return normalized
        return None

    def _invalidate_schema_cache(self, table: str | None = None) -> None:
        """清空表结构缓存（M25-C：迁移后调用，确保下次查询重新读取 PRAGMA）。"""
        with self._column_cache_lock:
            self._table_name_cache = None
            if table is not None:
                self._column_cache.pop(table, None)
            else:
                self._column_cache.clear()
