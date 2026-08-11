"""Operation-scoped persistence adapter for the catalog-owned episodic FTS."""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
from pathlib import Path
import sqlite3
import threading
from typing import Any, Iterator

from host_brain.audit.migration_compat import LEGACY_FTS_CHECKSUM


FULL_TEXT_SCHEMA_NOT_READY = "FULL_TEXT_SCHEMA_NOT_READY"
FULL_TEXT_QUERY_FAILED = "FULL_TEXT_QUERY_FAILED"
FULL_TEXT_MIGRATION_ASSET_KEY = "0021#episodic_fts5"
FULL_TEXT_MIGRATION_FILENAME = "0021_episodic_fts5.sql"


class FullTextRepositoryError(RuntimeError):
    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = str(reason_code or FULL_TEXT_QUERY_FAILED)
        self.detail = str(detail or "full-text persistence failed")
        super().__init__(f"{self.reason_code}: {self.detail}")


class FullTextRepository:
    """Own FTS connections and transactions without creating durable schema."""

    def __init__(
        self,
        db_path: str | Path,
        *,
        timeout_ms: int = 5_000,
        verify_schema: bool = True,
    ) -> None:
        raw = str(db_path or "").strip() or ":memory:"
        self._timeout_ms = max(1, int(timeout_ms))
        self._memory_connection: sqlite3.Connection | None = None
        self._memory_lock = threading.RLock()
        if raw == ":memory:":
            self.db_path = raw
            self._memory_connection = sqlite3.connect(
                ":memory:",
                check_same_thread=False,
                isolation_level=None,
            )
            self._memory_connection.row_factory = sqlite3.Row
            self._initialize_catalog_schema_for_memory_tests()
        else:
            if raw.lower().startswith("file:"):
                raise FullTextRepositoryError(
                    FULL_TEXT_SCHEMA_NOT_READY,
                    "URI databases are not a durable FTS authority",
                )
            self.db_path = str(Path(raw).expanduser().resolve())
        if verify_schema:
            self.assert_schema_ready(require_catalog=raw != ":memory:")

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        if self._memory_connection is not None:
            with self._memory_lock:
                yield self._memory_connection
            return
        conn = sqlite3.connect(
            self.db_path,
            timeout=self._timeout_ms / 1000.0,
            isolation_level=None,
        )
        conn.row_factory = sqlite3.Row
        conn.execute(f"PRAGMA busy_timeout = {self._timeout_ms}")
        try:
            yield conn
        finally:
            conn.close()

    def _initialize_catalog_schema_for_memory_tests(self) -> None:
        asset = (
            Path(__file__).resolve().parents[1]
            / "audit"
            / "migrations"
            / FULL_TEXT_MIGRATION_FILENAME
        )
        sql = asset.read_text(encoding="utf-8-sig")
        if hashlib.sha256(sql.encode("utf-8")).hexdigest() != LEGACY_FTS_CHECKSUM:
            raise FullTextRepositoryError(
                FULL_TEXT_SCHEMA_NOT_READY,
                "0021 migration checksum drifted",
            )
        start = sql.index("CREATE VIRTUAL TABLE")
        end = sql.index(");", start) + 2
        assert self._memory_connection is not None
        self._memory_connection.execute(sql[start:end])

    def assert_schema_ready(self, *, require_catalog: bool = True) -> None:
        try:
            with self._connection() as conn:
                columns = {
                    str(row[1])
                    for row in conn.execute("PRAGMA table_info(episodic_fts)").fetchall()
                }
                asset = None
                if require_catalog:
                    asset = conn.execute(
                        """
                        SELECT checksum, disposition
                        FROM schema_migration_assets WHERE asset_key = ?
                        """,
                        (FULL_TEXT_MIGRATION_ASSET_KEY,),
                    ).fetchone()
        except sqlite3.DatabaseError as exc:
            raise FullTextRepositoryError(
                FULL_TEXT_SCHEMA_NOT_READY,
                "episodic FTS schema cannot be verified",
            ) from exc
        if not {"memory_id", "content"} <= columns or (
            require_catalog
            and (
                asset is None
                or str(asset["checksum"]) != LEGACY_FTS_CHECKSUM
                or str(asset["disposition"])
                not in {"APPLIED", "APPLIED_WITH_COMPAT"}
            )
        ):
            raise FullTextRepositoryError(
                FULL_TEXT_SCHEMA_NOT_READY,
                "catalog migration 0021 is missing or drifted",
            )

    def replace_document(self, *, memory_id: str, content: str) -> None:
        identifier = str(memory_id or "").strip()
        if not identifier:
            raise FullTextRepositoryError(FULL_TEXT_QUERY_FAILED, "memory_id is required")
        with self._connection() as conn:
            try:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute(
                    "DELETE FROM episodic_fts WHERE memory_id = ?",
                    (identifier,),
                )
                conn.execute(
                    "INSERT INTO episodic_fts(memory_id, content) VALUES (?, ?)",
                    (identifier, str(content or "")),
                )
                conn.commit()
            except BaseException:
                conn.rollback()
                raise

    def search(self, query: str, *, limit: int) -> list[dict[str, Any]]:
        with self._connection() as conn:
            try:
                rows = conn.execute(
                    """
                    SELECT memory_id, content, bm25(episodic_fts) AS bm25_score
                    FROM episodic_fts
                    WHERE episodic_fts MATCH ?
                    ORDER BY bm25_score
                    LIMIT ?
                    """,
                    (str(query), max(1, int(limit))),
                ).fetchall()
            except sqlite3.OperationalError as exc:
                raise FullTextRepositoryError(FULL_TEXT_QUERY_FAILED, str(exc)) from exc
        return [dict(row) for row in rows]

    def close(self) -> None:
        with self._memory_lock:
            if self._memory_connection is not None:
                self._memory_connection.close()
                self._memory_connection = None


__all__ = [
    "FULL_TEXT_QUERY_FAILED",
    "FULL_TEXT_SCHEMA_NOT_READY",
    "FullTextRepository",
    "FullTextRepositoryError",
]
