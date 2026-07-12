"""Operation-scoped read repository for LLM token and pool projections."""

from __future__ import annotations

from contextlib import contextmanager, nullcontext
from pathlib import Path
import sqlite3
from typing import Any, Iterator


class TokenUsageQueryRepository:
    def __init__(
        self,
        db_path: str | Path,
        *,
        borrowed_connection: sqlite3.Connection | None = None,
        borrowed_lock: Any | None = None,
    ) -> None:
        self.db_path = str(db_path or "").strip()
        self._borrowed_connection = borrowed_connection
        self._borrowed_lock = borrowed_lock

    @classmethod
    def from_source(
        cls,
        *,
        ledger: Any | None,
        db_path: str | Path,
    ) -> "TokenUsageQueryRepository":
        resolved = str(
            db_path
            or getattr(ledger, "canonical_db_path", "")
            or getattr(ledger, "db_path", "")
        ).strip()
        if resolved != ":memory:":
            return cls(resolved)
        return cls(
            resolved,
            borrowed_connection=getattr(ledger, "_conn", None),
            borrowed_lock=getattr(ledger, "_write_lock", None),
        )

    @contextmanager
    def _scope(self) -> Iterator[sqlite3.Connection]:
        if self._borrowed_connection is not None:
            lock_scope = self._borrowed_lock or nullcontext()
            with lock_scope:
                yield self._borrowed_connection
            return
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def read_window(
        self,
        *,
        start_ms: int,
        end_ms: int,
        limit: int,
    ) -> dict[str, Any]:
        with self._scope() as conn:
            has_pool = self._table_exists(conn, "llm_pool_call_log")
            has_tokens = self._table_exists(conn, "llm_token_usage_log")
            return {
                "has_pool": has_pool,
                "has_tokens": has_tokens,
                "token_rows": self._query_token_rows(conn, start_ms, end_ms)
                if has_tokens else [],
                "token_hotspots": self._query_token_hotspots(
                    conn, start_ms, end_ms, limit
                ) if has_tokens else [],
                "pool_daily": self._query_pool_daily(conn, start_ms, end_ms)
                if has_pool else [],
                "pool_hotspots": self._query_pool_hotspots(
                    conn, start_ms, end_ms, limit
                ) if has_pool else [],
            }

    @staticmethod
    def _query_token_rows(
        conn: sqlite3.Connection, start_ms: int, end_ms: int
    ) -> list[dict[str, Any]]:
        rows = conn.execute(
            """
            SELECT date(ts_ms / 1000, 'unixepoch', 'localtime') AS day,
                   COUNT(1) AS calls, SUM(prompt_tokens) AS prompt_tokens,
                   SUM(completion_tokens) AS completion_tokens,
                   SUM(total_tokens) AS total_tokens,
                   SUM(billable_prompt_tokens) AS billable_prompt_tokens,
                   SUM(billable_completion_tokens) AS billable_completion_tokens,
                   SUM(billable_total_tokens) AS billable_total_tokens,
                   SUM(cached) AS cached_calls,
                   SUM(pool_exhausted) AS pool_exhausted_calls,
                   SUM(attempt_count) AS attempts,
                   SUM(billable_attempt_count) AS billable_attempts
            FROM llm_token_usage_log WHERE ts_ms >= ? AND ts_ms <= ?
            GROUP BY day ORDER BY day DESC
            """,
            (int(start_ms), int(end_ms)),
        ).fetchall()
        return [TokenUsageQueryRepository._row(row) for row in rows]

    @staticmethod
    def _query_token_hotspots(
        conn: sqlite3.Connection, start_ms: int, end_ms: int, limit: int
    ) -> list[dict[str, Any]]:
        rows = conn.execute(
            """
            SELECT scene_type, cognitive_tier, provider, model_id,
                   COUNT(1) AS calls, SUM(prompt_tokens) AS prompt_tokens,
                   SUM(completion_tokens) AS completion_tokens,
                   SUM(total_tokens) AS total_tokens,
                   SUM(billable_total_tokens) AS billable_total_tokens,
                   SUM(cached) AS cached_calls,
                   SUM(pool_exhausted) AS pool_exhausted_calls,
                   SUM(attempt_count) AS attempts,
                   SUM(billable_attempt_count) AS billable_attempts
            FROM llm_token_usage_log WHERE ts_ms >= ? AND ts_ms <= ?
            GROUP BY scene_type, cognitive_tier, provider, model_id
            ORDER BY billable_total_tokens DESC, calls DESC LIMIT ?
            """,
            (int(start_ms), int(end_ms), int(limit)),
        ).fetchall()
        return [TokenUsageQueryRepository._row(row) for row in rows]

    @staticmethod
    def _query_pool_daily(
        conn: sqlite3.Connection, start_ms: int, end_ms: int
    ) -> list[dict[str, Any]]:
        rows = conn.execute(
            """
            SELECT date(ts_ms / 1000, 'unixepoch', 'localtime') AS day,
                   COUNT(1) AS calls, COUNT(DISTINCT request_id) AS requests,
                   SUM(attempt_count) AS attempts, SUM(cached) AS cached_calls,
                   SUM(pool_exhausted) AS pool_exhausted_calls,
                   SUM(CASE WHEN cached = 0 AND pool_exhausted = 0 THEN 1 ELSE 0 END)
                       AS noncached_success_calls
            FROM llm_pool_call_log WHERE ts_ms >= ? AND ts_ms <= ?
            GROUP BY day ORDER BY day DESC
            """,
            (int(start_ms), int(end_ms)),
        ).fetchall()
        return [TokenUsageQueryRepository._row(row) for row in rows]

    @staticmethod
    def _query_pool_hotspots(
        conn: sqlite3.Connection, start_ms: int, end_ms: int, limit: int
    ) -> list[dict[str, Any]]:
        rows = conn.execute(
            """
            SELECT scene_type, cognitive_tier, final_provider AS provider,
                   final_model AS model_id, COUNT(1) AS calls,
                   COUNT(DISTINCT request_id) AS requests,
                   SUM(attempt_count) AS attempts, SUM(cached) AS cached_calls,
                   SUM(pool_exhausted) AS pool_exhausted_calls,
                   SUM(CASE WHEN cached = 0 AND pool_exhausted = 0 THEN 1 ELSE 0 END)
                       AS noncached_success_calls
            FROM llm_pool_call_log WHERE ts_ms >= ? AND ts_ms <= ?
            GROUP BY scene_type, cognitive_tier, final_provider, final_model
            ORDER BY calls DESC LIMIT ?
            """,
            (int(start_ms), int(end_ms), int(limit)),
        ).fetchall()
        return [TokenUsageQueryRepository._row(row) for row in rows]

    @staticmethod
    def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
        return conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (str(table),),
        ).fetchone() is not None

    @staticmethod
    def _row(row: sqlite3.Row) -> dict[str, Any]:
        return {key: (0 if row[key] is None else row[key]) for key in row.keys()}


__all__ = ["TokenUsageQueryRepository"]
