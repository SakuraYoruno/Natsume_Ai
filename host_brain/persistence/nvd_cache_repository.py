"""Catalog-owned, operation-scoped SQLite repository for the NVD cache."""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import threading
from typing import Any


NVD_CACHE_INVALID = "NVD_CACHE_INVALID"
NVD_CACHE_SCHEMA_NOT_READY = "NVD_CACHE_SCHEMA_NOT_READY"
NVD_CACHE_PERSISTENCE_FAILED = "NVD_CACHE_PERSISTENCE_FAILED"
NVD_CACHE_MIGRATION_ASSET_KEY = "0077#nvd_cache_persistence"
NVD_CACHE_MIGRATION_SHA256 = (
    "1ed411277f60a1e3db3320b2463204915aa078cf0ac8e125452dd1c4f44600e0"
)

_ALLOWED_MIGRATION_DISPOSITIONS = frozenset(
    {"APPLIED", "APPLIED_WITH_COMPAT", "LEGACY_BACKFILL"}
)
_REQUIRED_COLUMNS = {
    "cve_cache": frozenset(
        {
            "cve_id",
            "cpe",
            "vendor",
            "product",
            "cvss_score",
            "cvss_vector",
            "cvss_severity",
            "cwe",
            "description",
            "exploit_maturity",
            "attack_chain",
            "patch",
            "published_date",
            "last_modified",
            "raw_json",
            "synced_at",
        }
    ),
    "sync_state": frozenset({"key", "value"}),
}
_REQUIRED_INDEXES = frozenset(
    {
        "idx_cve_vendor",
        "idx_cve_product",
        "idx_cve_score",
        "idx_cve_modified",
        "idx_cve_cpe",
        "idx_cve_synced",
    }
)
_REQUIRED_PRIMARY_KEYS = {
    "cve_cache": ("cve_id",),
    "sync_state": ("key",),
}
_REQUIRED_INDEX_COLUMNS = {
    "idx_cve_vendor": ("vendor",),
    "idx_cve_product": ("product",),
    "idx_cve_score": ("cvss_score",),
    "idx_cve_modified": ("last_modified",),
    "idx_cve_cpe": ("cpe",),
    "idx_cve_synced": ("synced_at",),
}


class NvdCacheRepositoryError(RuntimeError):
    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = str(reason_code or NVD_CACHE_PERSISTENCE_FAILED)
        self.detail = str(detail or "NVD cache persistence failed")
        super().__init__(f"{self.reason_code}: {self.detail}")


def _limit(value: int) -> int:
    try:
        normalized = int(value)
    except (TypeError, ValueError) as exc:
        raise NvdCacheRepositoryError(NVD_CACHE_INVALID, "limit must be an integer") from exc
    if normalized < 1 or normalized > 10_000:
        raise NvdCacheRepositoryError(NVD_CACHE_INVALID, "limit is outside 1..10000")
    return normalized


def _text(value: Any, *, max_length: int = 1_000_000) -> str:
    normalized = str(value or "")
    if len(normalized) > max_length:
        raise NvdCacheRepositoryError(NVD_CACHE_INVALID, "cache field is too large")
    return normalized


def _normalize_cve(value: Mapping[str, Any], *, synced_at: str) -> tuple[Any, ...]:
    cve_id = _text(value.get("id") or value.get("cve_id"), max_length=128).strip().upper()
    if not cve_id:
        raise NvdCacheRepositoryError(NVD_CACHE_INVALID, "CVE id is required")
    try:
        score = float(value.get("cvss_score") or 0.0)
    except (TypeError, ValueError) as exc:
        raise NvdCacheRepositoryError(NVD_CACHE_INVALID, "CVSS score is invalid") from exc
    if score < 0.0 or score > 10.0:
        raise NvdCacheRepositoryError(NVD_CACHE_INVALID, "CVSS score is outside 0..10")
    chain = value.get("attack_chain") or []
    if isinstance(chain, str):
        attack_chain = chain
    elif isinstance(chain, Sequence):
        attack_chain = " → ".join(str(item) for item in chain if str(item).strip())
    else:
        raise NvdCacheRepositoryError(NVD_CACHE_INVALID, "attack chain is invalid")
    raw_json = value.get("raw_json") or ""
    if isinstance(raw_json, Mapping):
        raw_json = json.dumps(
            dict(raw_json),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    return (
        cve_id,
        _text(value.get("cpe")),
        _text(value.get("vendor"), max_length=512),
        _text(value.get("product"), max_length=512),
        score,
        _text(value.get("cvss_vector"), max_length=512),
        _text(value.get("cvss_severity"), max_length=64),
        _text(value.get("cwe"), max_length=256),
        _text(value.get("description")),
        _text(value.get("exploit_maturity"), max_length=128),
        _text(attack_chain),
        _text(value.get("patch")),
        _text(value.get("published_date"), max_length=128),
        _text(value.get("last_modified"), max_length=128),
        _text(raw_json),
        _text(synced_at, max_length=128),
    )


class NvdCacheRepository:
    """Owns NVD cache connections and one transaction per mutation operation."""

    def __init__(self, db_path: str | Path, *, timeout_ms: int = 3_000) -> None:
        raw_path = str(db_path or "").strip()
        if not raw_path or raw_path.lower().startswith("file:"):
            raise NvdCacheRepositoryError(
                NVD_CACHE_INVALID,
                "db_path must be a SQLite file path or :memory:",
            )
        self._timeout_ms = max(1, int(timeout_ms))
        self._memory_connection: sqlite3.Connection | None = None
        self._memory_lock = threading.RLock()
        if raw_path == ":memory:":
            self.db_path: str | Path = raw_path
            self._memory_connection = sqlite3.connect(
                raw_path,
                check_same_thread=False,
                isolation_level=None,
            )
            self._configure(self._memory_connection)
            try:
                self._apply_memory_asset()
                self.assert_schema_ready(require_catalog=False)
            except BaseException:
                self._memory_connection.close()
                self._memory_connection = None
                raise
        else:
            self.db_path = Path(raw_path).expanduser().resolve()
            self.assert_schema_ready(require_catalog=True)

    @staticmethod
    def _migration_asset() -> Path:
        return (
            Path(__file__).resolve().parents[1]
            / "audit"
            / "migrations"
            / "0077_nvd_cache_persistence.sql"
        )

    def _apply_memory_asset(self) -> None:
        try:
            sql = self._migration_asset().read_text(encoding="utf-8-sig")
        except OSError as exc:
            raise NvdCacheRepositoryError(
                NVD_CACHE_SCHEMA_NOT_READY,
                "0077 migration asset is unavailable",
            ) from exc
        checksum = hashlib.sha256(sql.encode("utf-8")).hexdigest()
        if checksum != NVD_CACHE_MIGRATION_SHA256:
            raise NvdCacheRepositoryError(
                NVD_CACHE_SCHEMA_NOT_READY,
                "0077 migration checksum drifted",
            )
        assert self._memory_connection is not None
        try:
            self._memory_connection.executescript(sql)
        except sqlite3.DatabaseError as exc:
            raise NvdCacheRepositoryError(
                NVD_CACHE_SCHEMA_NOT_READY,
                "0077 in-memory migration failed",
            ) from exc

    def _configure(self, conn: sqlite3.Connection) -> None:
        conn.row_factory = sqlite3.Row
        conn.execute(f"PRAGMA busy_timeout = {self._timeout_ms}")

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        if self._memory_connection is not None:
            with self._memory_lock:
                yield self._memory_connection
            return
        conn = sqlite3.connect(
            str(self.db_path),
            timeout=self._timeout_ms / 1000.0,
            isolation_level=None,
        )
        self._configure(conn)
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._connection() as conn:
            try:
                conn.execute("BEGIN IMMEDIATE")
                yield conn
                conn.commit()
            except BaseException:
                conn.rollback()
                raise

    @contextmanager
    def _snapshot(self) -> Iterator[sqlite3.Connection]:
        with self._connection() as conn:
            try:
                conn.execute("BEGIN")
                yield conn
                conn.commit()
            except BaseException:
                conn.rollback()
                raise

    def assert_schema_ready(self, *, require_catalog: bool = True) -> None:
        if require_catalog and (
            not isinstance(self.db_path, Path) or not self.db_path.is_file()
        ):
            raise NvdCacheRepositoryError(
                NVD_CACHE_SCHEMA_NOT_READY,
                f"NVD cache database does not exist: {self.db_path}",
            )
        try:
            with self._connection() as conn:
                table_rows = {
                    table: conn.execute(f'PRAGMA table_info("{table}")').fetchall()
                    for table in _REQUIRED_COLUMNS
                }
                columns = {
                    table: {str(row[1]) for row in rows}
                    for table, rows in table_rows.items()
                }
                primary_keys = {
                    table: tuple(
                        str(row[1])
                        for row in sorted(
                            (row for row in rows if int(row[5]) > 0),
                            key=lambda item: int(item[5]),
                        )
                    )
                    for table, rows in table_rows.items()
                }
                indexes = {
                    str(row[0])
                    for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'index'"
                    ).fetchall()
                }
                index_columns = {
                    name: tuple(
                        str(row[2])
                        for row in sorted(
                            conn.execute(f'PRAGMA index_info("{name}")').fetchall(),
                            key=lambda item: int(item[0]),
                        )
                    )
                    for name in _REQUIRED_INDEXES
                }
                asset = None
                if require_catalog:
                    asset = conn.execute(
                        """
                        SELECT checksum, disposition
                        FROM schema_migration_assets
                        WHERE asset_key = ?
                        """,
                        (NVD_CACHE_MIGRATION_ASSET_KEY,),
                    ).fetchone()
        except sqlite3.DatabaseError as exc:
            raise NvdCacheRepositoryError(
                NVD_CACHE_SCHEMA_NOT_READY,
                "NVD cache schema or migration catalog is unavailable",
            ) from exc
        if any(
            not required.issubset(columns[table])
            for table, required in _REQUIRED_COLUMNS.items()
        ) or not _REQUIRED_INDEXES.issubset(indexes) or any(
            primary_keys[table] != required
            for table, required in _REQUIRED_PRIMARY_KEYS.items()
        ) or any(
            index_columns[name] != required
            for name, required in _REQUIRED_INDEX_COLUMNS.items()
        ):
            raise NvdCacheRepositoryError(
                NVD_CACHE_SCHEMA_NOT_READY,
                "NVD cache schema does not match migration 0077",
            )
        if require_catalog and (
            asset is None
            or str(asset["checksum"]) != NVD_CACHE_MIGRATION_SHA256
            or str(asset["disposition"]) not in _ALLOWED_MIGRATION_DISPOSITIONS
        ):
            raise NvdCacheRepositoryError(
                NVD_CACHE_SCHEMA_NOT_READY,
                "NVD cache migration 0077 is not catalog-authorized",
            )

    def is_empty(self) -> bool:
        with self._connection() as conn:
            row = conn.execute("SELECT COUNT(*) AS count FROM cve_cache").fetchone()
        return row is None or int(row["count"]) == 0

    def get_cve(self, cve_id: str) -> dict[str, Any] | None:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM cve_cache WHERE cve_id = ?",
                (str(cve_id or "").strip().upper(),),
            ).fetchone()
        return dict(row) if row is not None else None

    def search_cpe(
        self,
        *,
        vendor: str = "",
        product: str = "",
        min_cvss: float = 0.0,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        conditions: list[str] = []
        parameters: list[Any] = []
        if vendor:
            conditions.append("vendor LIKE ?")
            parameters.append(f"%{vendor}%")
        if product:
            conditions.append("product LIKE ?")
            parameters.append(f"%{product}%")
        if float(min_cvss) > 0:
            conditions.append("cvss_score >= ?")
            parameters.append(float(min_cvss))
        where = " AND ".join(conditions) if conditions else "1 = 1"
        parameters.append(_limit(limit))
        with self._connection() as conn:
            rows = conn.execute(
                f"""
                SELECT cve_id, cpe, vendor, product, cvss_score, cvss_vector,
                       cvss_severity, cwe, description, exploit_maturity,
                       attack_chain, patch, published_date, last_modified
                FROM cve_cache
                WHERE {where}
                ORDER BY cvss_score DESC, cve_id
                LIMIT ?
                """,
                tuple(parameters),
            ).fetchall()
        return [dict(row) for row in rows]

    def search_keyword(
        self,
        *,
        keyword: str,
        min_cvss: float = 0.0,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        conditions = ["description LIKE ?"]
        parameters: list[Any] = [f"%{str(keyword)}%"]
        if float(min_cvss) > 0:
            conditions.append("cvss_score >= ?")
            parameters.append(float(min_cvss))
        parameters.append(_limit(limit))
        with self._connection() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM cve_cache
                WHERE {' AND '.join(conditions)}
                ORDER BY cvss_score DESC, cve_id
                LIMIT ?
                """,
                tuple(parameters),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_sync_state(self, key: str) -> str | None:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT value FROM sync_state WHERE key = ?",
                (str(key),),
            ).fetchone()
        return None if row is None else str(row["value"])

    def stats(self) -> dict[str, Any]:
        with self._snapshot() as conn:
            aggregate = conn.execute(
                "SELECT COUNT(*) AS total, MAX(cvss_score) AS max_cvss FROM cve_cache"
            ).fetchone()
            state_rows = conn.execute("SELECT key, value FROM sync_state").fetchall()
        states = {str(row["key"]): str(row["value"]) for row in state_rows}
        return {
            "total_cached": int(aggregate["total"] if aggregate is not None else 0),
            "max_cvss": float(aggregate["max_cvss"] or 0.0) if aggregate else 0.0,
            "last_sync_ts": states.get("last_sync_ts", "never"),
            "last_mod_end_date": states.get("last_mod_end_date", ""),
            "total_synced": int(states.get("total_synced", "0")),
        }

    def upsert_cve(
        self,
        cve: Mapping[str, Any],
        *,
        synced_at: str | None = None,
    ) -> None:
        timestamp = synced_at or datetime.now(timezone.utc).isoformat()
        row = _normalize_cve(cve, synced_at=timestamp)
        with self._transaction() as conn:
            self._upsert(conn, row)

    def commit_sync(
        self,
        cves: Sequence[Mapping[str, Any]],
        *,
        last_sync_ts: str,
        last_mod_end_date: str,
    ) -> dict[str, int]:
        timestamp = _text(last_sync_ts, max_length=128)
        end_date = _text(last_mod_end_date, max_length=128)
        rows = [_normalize_cve(cve, synced_at=timestamp) for cve in cves]
        with self._transaction() as conn:
            for row in rows:
                self._upsert(conn, row)
            total_row = conn.execute("SELECT COUNT(*) AS count FROM cve_cache").fetchone()
            total = int(total_row["count"] if total_row is not None else 0)
            self._set_state(conn, "last_sync_ts", timestamp)
            self._set_state(conn, "last_mod_end_date", end_date)
            self._set_state(conn, "total_synced", str(total))
        return {"upserted": len(rows), "total_cached": total}

    def prune_synced_before(self, cutoff: str) -> int:
        normalized = _text(cutoff, max_length=128).strip()
        if not normalized:
            raise NvdCacheRepositoryError(NVD_CACHE_INVALID, "prune cutoff is required")
        with self._transaction() as conn:
            cursor = conn.execute(
                "DELETE FROM cve_cache WHERE synced_at < ?",
                (normalized,),
            )
            total_row = conn.execute("SELECT COUNT(*) AS count FROM cve_cache").fetchone()
            total = int(total_row["count"] if total_row is not None else 0)
            self._set_state(conn, "total_synced", str(total))
        return max(0, int(cursor.rowcount))

    @staticmethod
    def _upsert(conn: sqlite3.Connection, row: tuple[Any, ...]) -> None:
        conn.execute(
            """
            INSERT INTO cve_cache(
                cve_id, cpe, vendor, product, cvss_score, cvss_vector,
                cvss_severity, cwe, description, exploit_maturity, attack_chain,
                patch, published_date, last_modified, raw_json, synced_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(cve_id) DO UPDATE SET
                cpe = excluded.cpe,
                vendor = excluded.vendor,
                product = excluded.product,
                cvss_score = excluded.cvss_score,
                cvss_vector = excluded.cvss_vector,
                cvss_severity = excluded.cvss_severity,
                cwe = excluded.cwe,
                description = excluded.description,
                exploit_maturity = excluded.exploit_maturity,
                attack_chain = excluded.attack_chain,
                patch = excluded.patch,
                published_date = excluded.published_date,
                last_modified = excluded.last_modified,
                raw_json = excluded.raw_json,
                synced_at = excluded.synced_at
            """,
            row,
        )

    @staticmethod
    def _set_state(conn: sqlite3.Connection, key: str, value: str) -> None:
        conn.execute(
            """
            INSERT INTO sync_state(key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (str(key), str(value)),
        )

    def close(self) -> None:
        with self._memory_lock:
            if self._memory_connection is not None:
                self._memory_connection.close()
                self._memory_connection = None


__all__ = [
    "NVD_CACHE_INVALID",
    "NVD_CACHE_MIGRATION_ASSET_KEY",
    "NVD_CACHE_MIGRATION_SHA256",
    "NVD_CACHE_PERSISTENCE_FAILED",
    "NVD_CACHE_SCHEMA_NOT_READY",
    "NvdCacheRepository",
    "NvdCacheRepositoryError",
]
