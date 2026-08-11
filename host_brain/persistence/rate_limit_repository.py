"""Operation-scoped SQLite repository for durable rate-limit windows."""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
from pathlib import Path
import sqlite3
import threading
from typing import Iterator


RATE_LIMIT_SCHEMA_NOT_READY = "RATE_LIMIT_SCHEMA_NOT_READY"
RATE_LIMIT_MIGRATION_ASSET_KEY = "0073#rate_limit_persistence"
RATE_LIMIT_MIGRATION_SHA256 = "767194c1f5328984f14100f7b8ce707b0bbfae7d6a84b5b12a60eb92a250d7f2"


class RateLimitRepositoryError(RuntimeError):
    pass


class RateLimitRepository:
    def __init__(self, db_path: str | Path, *, timeout_ms: int = 3_000) -> None:
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
            self._apply_memory_catalog_asset()
        else:
            if raw.lower().startswith("file:"):
                raise RateLimitRepositoryError("URI databases are not supported")
            self.db_path = str(Path(raw).expanduser().resolve())
        self.assert_schema_ready(require_catalog=raw != ":memory:")

    def _apply_memory_catalog_asset(self) -> None:
        asset = (
            Path(__file__).resolve().parents[1]
            / "audit"
            / "migrations"
            / "0073_rate_limit_persistence.sql"
        )
        sql = asset.read_text(encoding="utf-8-sig")
        if hashlib.sha256(sql.encode("utf-8")).hexdigest() != RATE_LIMIT_MIGRATION_SHA256:
            raise RateLimitRepositoryError("0073 migration checksum drifted")
        assert self._memory_connection is not None
        self._memory_connection.executescript(sql)

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

    def assert_schema_ready(self, *, require_catalog: bool) -> None:
        try:
            with self._connection() as conn:
                columns = {
                    str(row[1])
                    for row in conn.execute("PRAGMA table_info(rate_limit_events)")
                }
                indexes = {
                    str(row[0])
                    for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='index'"
                    )
                }
                asset = None
                if require_catalog:
                    asset = conn.execute(
                        """
                        SELECT checksum, disposition FROM schema_migration_assets
                        WHERE asset_key = ?
                        """,
                        (RATE_LIMIT_MIGRATION_ASSET_KEY,),
                    ).fetchone()
        except sqlite3.DatabaseError as exc:
            raise RateLimitRepositoryError(RATE_LIMIT_SCHEMA_NOT_READY) from exc
        if not {"id", "key", "ts_ms"} <= columns or "idx_rate_limit_key_ts" not in indexes:
            raise RateLimitRepositoryError(RATE_LIMIT_SCHEMA_NOT_READY)
        if require_catalog and (
            asset is None
            or str(asset["checksum"]) != RATE_LIMIT_MIGRATION_SHA256
            or str(asset["disposition"]) not in {"APPLIED", "LEGACY_BACKFILL"}
        ):
            raise RateLimitRepositoryError(RATE_LIMIT_SCHEMA_NOT_READY)

    def increment(self, key: str, *, ts_ms: int, cutoff_ms: int) -> int:
        normalized = str(key or "").strip()
        if not normalized:
            raise ValueError("rate-limit key is required")
        with self._connection() as conn:
            try:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute(
                    "DELETE FROM rate_limit_events WHERE key = ? AND ts_ms <= ?",
                    (normalized, int(cutoff_ms)),
                )
                conn.execute(
                    "INSERT INTO rate_limit_events(key, ts_ms) VALUES (?, ?)",
                    (normalized, int(ts_ms)),
                )
                row = conn.execute(
                    "SELECT COUNT(*) FROM rate_limit_events WHERE key = ? AND ts_ms > ?",
                    (normalized, int(cutoff_ms)),
                ).fetchone()
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
        return int(row[0] if row is not None else 1)

    def count(self, key: str, *, cutoff_ms: int) -> int:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM rate_limit_events WHERE key = ? AND ts_ms > ?",
                (str(key), int(cutoff_ms)),
            ).fetchone()
        return int(row[0] if row is not None else 0)

    def reset(self, key: str) -> None:
        with self._connection() as conn:
            try:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute("DELETE FROM rate_limit_events WHERE key = ?", (str(key),))
                conn.commit()
            except BaseException:
                conn.rollback()
                raise

    def oldest_after(self, key: str, *, cutoff_ms: int) -> int | None:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT MIN(ts_ms) FROM rate_limit_events WHERE key = ? AND ts_ms > ?",
                (str(key), int(cutoff_ms)),
            ).fetchone()
        return None if row is None or row[0] is None else int(row[0])

    def close(self) -> None:
        with self._memory_lock:
            if self._memory_connection is not None:
                self._memory_connection.close()
                self._memory_connection = None


__all__ = ["RateLimitRepository", "RateLimitRepositoryError"]
