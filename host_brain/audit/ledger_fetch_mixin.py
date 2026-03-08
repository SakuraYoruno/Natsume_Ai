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
    _write_lock: threading.Lock

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

    def fetch_by_request_id(self, table: str, request_id: str) -> list[dict[str, Any]]:
        """按 request_id 直接过滤查询（SQL 级，BUG-BE-002 修复）。"""
        _obs_tables = frozenset({
            "llm_node_call_log",
            "graph_run_trace",
        })
        if table not in _ALLOWED_TABLES and table not in _obs_tables:
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
    ) -> list[dict[str, Any]]:
        """Fetch rows from a table by optional ts_ms window with pagination."""
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

        has_ts = self._table_has_column(table, "ts_ms")
        has_id = self._table_has_column(table, "id")

        if has_ts and (start_ms is not None or end_ms is not None):
            conditions: list[str] = []
            params: list[Any] = []
            if start_ms is not None:
                conditions.append("ts_ms >= ?")
                params.append(int(start_ms))
            if end_ms is not None:
                conditions.append("ts_ms <= ?")
                params.append(int(end_ms))
            where_clause = " AND ".join(conditions)
            order = "ts_ms ASC" if has_ts else ("id ASC" if has_id else "rowid ASC")
            sql = f"SELECT * FROM {table} WHERE {where_clause} ORDER BY {order} LIMIT ? OFFSET ?"
            params.extend([limit, offset])
            rows = self._conn.execute(sql, params).fetchall()
        else:
            order = "ts_ms ASC" if has_ts else ("id ASC" if has_id else "rowid ASC")
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
        if start_ms is None and end_ms is None:
            row = self._conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
        else:
            conditions: list[str] = []
            params: list[Any] = []
            if start_ms is not None:
                conditions.append("ts_ms >= ?")
                params.append(int(start_ms))
            if end_ms is not None:
                conditions.append("ts_ms <= ?")
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

    def _invalidate_schema_cache(self, table: str | None = None) -> None:
        """清空表结构缓存（M25-C：迁移后调用，确保下次查询重新读取 PRAGMA）。"""
        with self._column_cache_lock:
            if table is not None:
                self._column_cache.pop(table, None)
            else:
                self._column_cache.clear()
