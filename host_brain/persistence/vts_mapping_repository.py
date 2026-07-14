"""Operation-scoped persistence authority for versioned VTS mappings."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3
import threading
from typing import Any

from host_brain.core.ids import now_ms


VTS_MAPPING_SCHEMA_NOT_READY = "VTS_MAPPING_SCHEMA_NOT_READY"
VTS_MAPPING_INVALID = "VTS_MAPPING_INVALID"
VTS_MAPPING_CONFLICT = "VTS_MAPPING_CONFLICT"
VTS_MAPPING_NOT_FOUND = "VTS_MAPPING_NOT_FOUND"

PLATFORM_REGISTRY_MIGRATION_ASSET_KEY = "0072#platform_registry_persistence"
PLATFORM_REGISTRY_MIGRATION_SHA256 = (
    "21dfa7958d43c9f7543ceefe1d4d4601fef1a366d360509a3fb5502e26b782b5"
)

_ASSET_PATH = (
    Path(__file__).resolve().parents[1]
    / "audit"
    / "migrations"
    / "0072_platform_registry_persistence.sql"
)
_ALLOWED_DISPOSITIONS = frozenset(
    {"APPLIED", "APPLIED_WITH_COMPAT", "LEGACY_BACKFILL"}
)
_REQUIRED_TABLE_COLUMNS = {
    "vts_expression_mapping": frozenset(
        {"version", "emotion_tag", "expression_id", "created_ts_ms"}
    ),
    "vts_mapping_change_journal": frozenset(
        {
            "journal_id",
            "operation_ref",
            "version",
            "prev_version",
            "prev_hash",
            "new_hash",
            "changed_by",
            "request_json",
            "request_digest",
            "result_json",
            "result_digest",
            "created_at_ms",
        }
    ),
}
_REQUIRED_INDEXES = frozenset(
    {"idx_vts_mapping_version", "idx_vts_mapping_journal_created"}
)
_REQUIRED_TRIGGERS = frozenset(
    {
        "trg_vts_expression_mapping_no_update",
        "trg_vts_expression_mapping_no_delete",
        "trg_vts_expression_mapping_no_insert_after_journal",
        "trg_vts_mapping_journal_no_update",
        "trg_vts_mapping_journal_no_delete",
    }
)


class VTSMappingRepositoryError(RuntimeError):
    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = str(reason_code or VTS_MAPPING_INVALID)
        self.detail = str(detail or "VTS mapping persistence failed")
        super().__init__(f"{self.reason_code}: {self.detail}")


class VTSMappingRepository:
    """Immutable mapping revisions plus an exact-replay change journal."""

    def __init__(
        self,
        db_path: str | Path,
        *,
        clock: Callable[[], int] = now_ms,
        timeout_ms: int = 5_000,
        fault_injector: Callable[[str], None] | None = None,
    ) -> None:
        raw_path = str(db_path or "").strip()
        if not raw_path:
            raise VTSMappingRepositoryError(VTS_MAPPING_INVALID, "db_path is required")
        if not callable(clock):
            raise VTSMappingRepositoryError(VTS_MAPPING_INVALID, "clock must be callable")
        self.db_path = raw_path
        self._clock = clock
        self._timeout_ms = max(1, int(timeout_ms))
        self._fault_injector = fault_injector
        self._memory_connection: sqlite3.Connection | None = None
        self._memory_lock = threading.RLock()
        if raw_path == ":memory:":
            self._memory_connection = sqlite3.connect(
                ":memory:", check_same_thread=False, isolation_level=None
            )
            self._memory_connection.row_factory = sqlite3.Row
            self._memory_connection.execute("PRAGMA foreign_keys = ON")
            self._initialize_memory_from_asset()
        else:
            self.db_path = str(Path(raw_path).expanduser().resolve())
            self.assert_schema_ready()

    def close(self) -> None:
        connection = self._memory_connection
        self._memory_connection = None
        if connection is not None:
            connection.close()

    def assert_schema_ready(self) -> None:
        if self.db_path == ":memory:":
            self._assert_schema(self._memory_connection, require_catalog=False)
            return
        if not Path(self.db_path).is_file():
            raise VTSMappingRepositoryError(
                VTS_MAPPING_SCHEMA_NOT_READY,
                f"VTS mapping database does not exist: {self.db_path}",
            )
        conn = self._connect_file()
        try:
            self._assert_schema(conn, require_catalog=True)
        finally:
            conn.close()

    def reconcile_unjournaled_versions(self) -> None:
        """Journal pre-0072 mapping revisions without changing their rows."""

        with self._read_operation() as conn:
            rows = conn.execute(
                """
                SELECT version, MIN(created_ts_ms) AS created_ts_ms
                FROM vts_expression_mapping
                GROUP BY version
                ORDER BY created_ts_ms, version
                """
            ).fetchall()
            versions = [str(row["version"]) for row in rows]
        for version in versions:
            with self._read_operation() as conn:
                journal = conn.execute(
                    "SELECT 1 FROM vts_mapping_change_journal WHERE version = ?",
                    (version,),
                ).fetchone()
            if journal is not None:
                continue
            record = self.load_mapping(version=version)
            self.save_mapping(
                record["mapping"],
                version=version,
                changed_by="legacy_backfill",
            )

    def save_mapping(
        self,
        mapping: Mapping[str, str],
        *,
        version: str,
        changed_by: str,
    ) -> dict[str, Any]:
        version_value = _required_text(version, field_name="version", limit=80)
        changed_by_value = _required_text(
            changed_by, field_name="changed_by", limit=240
        )
        mapping_value = _normalize_mapping(mapping)
        request = {
            "changed_by": changed_by_value,
            "mapping": mapping_value,
            "version": version_value,
        }
        request_json = _canonical_json(request)
        request_digest = _sha256(request_json)
        new_hash = _mapping_hash(mapping_value)
        created_at_ms = self._now()
        operation_ref = _operation_ref("vts-mapping", version_value)

        with self._write_operation() as conn:
            existing_journal = conn.execute(
                "SELECT * FROM vts_mapping_change_journal WHERE version = ?",
                (version_value,),
            ).fetchone()
            if existing_journal is not None:
                record = self._journal_record(existing_journal)
                if (
                    record["request_digest"] != request_digest
                    or record["request"] != request
                ):
                    raise VTSMappingRepositoryError(
                        VTS_MAPPING_CONFLICT,
                        "VTS mapping version is already bound to another request",
                    )
                return record

            current_rows = conn.execute(
                """
                SELECT emotion_tag, expression_id
                FROM vts_expression_mapping WHERE version = ?
                ORDER BY emotion_tag
                """,
                (version_value,),
            ).fetchall()
            if current_rows:
                current_mapping = {
                    str(row["emotion_tag"]): str(row["expression_id"])
                    for row in current_rows
                }
                if current_mapping != mapping_value:
                    raise VTSMappingRepositoryError(
                        VTS_MAPPING_CONFLICT,
                        "VTS mapping version rows conflict with the requested mapping",
                    )

            previous = conn.execute(
                """
                SELECT version, new_hash FROM vts_mapping_change_journal
                ORDER BY journal_id DESC LIMIT 1
                """
            ).fetchone()
            prev_version = str(previous["version"]) if previous is not None else None
            prev_hash = str(previous["new_hash"]) if previous is not None else None
            if not current_rows:
                conn.executemany(
                    """
                    INSERT INTO vts_expression_mapping(
                        version, emotion_tag, expression_id, created_ts_ms
                    ) VALUES (?, ?, ?, ?)
                    """,
                    [
                        (version_value, tag, expression_id, created_at_ms)
                        for tag, expression_id in mapping_value.items()
                    ],
                )
            self._inject_fault("after_mapping_write")
            result = {"content_hash": new_hash, "version": version_value}
            result_json = _canonical_json(result)
            result_digest = _sha256(result_json)
            conn.execute(
                """
                INSERT INTO vts_mapping_change_journal(
                    operation_ref, version, prev_version, prev_hash, new_hash,
                    changed_by, request_json, request_digest, result_json,
                    result_digest, created_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    operation_ref,
                    version_value,
                    prev_version,
                    prev_hash,
                    new_hash,
                    changed_by_value,
                    request_json,
                    request_digest,
                    result_json,
                    result_digest,
                    created_at_ms,
                ),
            )
            row = conn.execute(
                "SELECT * FROM vts_mapping_change_journal WHERE operation_ref = ?",
                (operation_ref,),
            ).fetchone()
            if row is None:
                raise VTSMappingRepositoryError(
                    VTS_MAPPING_CONFLICT,
                    "VTS mapping journal could not be read back",
                )
            return self._journal_record(row)

    def load_mapping(self, *, version: str | None = None) -> dict[str, Any]:
        with self._read_operation() as conn:
            version_value = str(version or "").strip()
            if not version_value:
                latest = conn.execute(
                    """
                    SELECT version FROM vts_mapping_change_journal
                    ORDER BY journal_id DESC LIMIT 1
                    """
                ).fetchone()
                if latest is None:
                    raise VTSMappingRepositoryError(
                        VTS_MAPPING_NOT_FOUND, "VTS mapping catalog is empty"
                    )
                version_value = str(latest["version"])
            rows = conn.execute(
                """
                SELECT emotion_tag, expression_id, created_ts_ms
                FROM vts_expression_mapping WHERE version = ?
                ORDER BY emotion_tag
                """,
                (version_value,),
            ).fetchall()
            if not rows:
                raise VTSMappingRepositoryError(
                    VTS_MAPPING_NOT_FOUND,
                    f"VTS mapping version does not exist: {version_value}",
                )
            mapping = {
                str(row["emotion_tag"]): str(row["expression_id"]) for row in rows
            }
            return {
                "version": version_value,
                "mapping": mapping,
                "content_hash": _mapping_hash(mapping),
                "created_ts_ms": int(rows[0]["created_ts_ms"]),
            }

    def list_versions(self) -> list[dict[str, Any]]:
        with self._read_operation() as conn:
            rows = conn.execute(
                """
                SELECT * FROM vts_mapping_change_journal
                ORDER BY journal_id DESC
                """
            ).fetchall()
            return [
                {
                    "version": str(row["version"]),
                    "prev_version": (
                        str(row["prev_version"])
                        if row["prev_version"] is not None
                        else None
                    ),
                    "prev_hash": (
                        str(row["prev_hash"]) if row["prev_hash"] is not None else None
                    ),
                    "new_hash": str(row["new_hash"]),
                    "changed_by": str(row["changed_by"]),
                    "ts_ms": int(row["created_at_ms"]),
                }
                for row in rows
            ]

    def _journal_record(self, row: sqlite3.Row) -> dict[str, Any]:
        request = _decode_canonical(row["request_json"], row["request_digest"])
        result = _decode_canonical(row["result_json"], row["result_digest"])
        version = str(row["version"])
        if str(row["operation_ref"]) != _operation_ref("vts-mapping", version):
            raise VTSMappingRepositoryError(
                VTS_MAPPING_INVALID, "VTS mapping operation reference drifted"
            )
        if result.get("version") != version or result.get("content_hash") != str(
            row["new_hash"]
        ):
            raise VTSMappingRepositoryError(
                VTS_MAPPING_INVALID, "VTS mapping result binding drifted"
            )
        request_mapping = request.get("mapping")
        if (
            request.get("version") != version
            or request.get("changed_by") != str(row["changed_by"])
            or not isinstance(request_mapping, dict)
            or _mapping_hash(request_mapping) != str(row["new_hash"])
        ):
            raise VTSMappingRepositoryError(
                VTS_MAPPING_INVALID, "VTS mapping request binding drifted"
            )
        return {
            "operation_ref": str(row["operation_ref"]),
            "version": version,
            "prev_version": (
                str(row["prev_version"]) if row["prev_version"] is not None else None
            ),
            "prev_hash": (
                str(row["prev_hash"]) if row["prev_hash"] is not None else None
            ),
            "new_hash": str(row["new_hash"]),
            "changed_by": str(row["changed_by"]),
            "request": request,
            "request_digest": str(row["request_digest"]),
            "result": result,
            "result_digest": str(row["result_digest"]),
            "created_at_ms": int(row["created_at_ms"]),
        }

    def _initialize_memory_from_asset(self) -> None:
        connection = self._memory_connection
        if connection is None:
            raise AssertionError("memory connection is missing")
        sql = _read_verified_asset()
        connection.executescript(sql)
        self._assert_schema(connection, require_catalog=False)

    def _assert_schema(
        self,
        conn: sqlite3.Connection | None,
        *,
        require_catalog: bool,
    ) -> None:
        if conn is None:
            raise VTSMappingRepositoryError(
                VTS_MAPPING_SCHEMA_NOT_READY, "VTS mapping connection is unavailable"
            )
        try:
            columns = {
                table: {
                    str(row[1])
                    for row in conn.execute(f'PRAGMA table_info("{table}")').fetchall()
                }
                for table in _REQUIRED_TABLE_COLUMNS
            }
            indexes = {
                str(row[0])
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'index'"
                ).fetchall()
            }
            triggers = {
                str(row[0])
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'trigger'"
                ).fetchall()
            }
            asset = None
            if require_catalog:
                asset = conn.execute(
                    """
                    SELECT checksum, disposition FROM schema_migration_assets
                    WHERE asset_key = ?
                    """,
                    (PLATFORM_REGISTRY_MIGRATION_ASSET_KEY,),
                ).fetchone()
        except sqlite3.DatabaseError as exc:
            raise VTSMappingRepositoryError(
                VTS_MAPPING_SCHEMA_NOT_READY,
                "VTS mapping schema could not be verified",
            ) from exc
        missing_columns = {
            table: sorted(required - columns.get(table, set()))
            for table, required in _REQUIRED_TABLE_COLUMNS.items()
            if not required <= columns.get(table, set())
        }
        catalog_ready = not require_catalog or (
            asset is not None
            and str(asset["checksum"]) == PLATFORM_REGISTRY_MIGRATION_SHA256
            and str(asset["disposition"]) in _ALLOWED_DISPOSITIONS
        )
        if (
            missing_columns
            or not _REQUIRED_INDEXES <= indexes
            or not _REQUIRED_TRIGGERS <= triggers
            or not catalog_ready
        ):
            raise VTSMappingRepositoryError(
                VTS_MAPPING_SCHEMA_NOT_READY,
                "0072 VTS mapping schema is missing or checksum-drifted",
            )

    def _connect_file(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            self.db_path,
            timeout=self._timeout_ms / 1000.0,
            isolation_level=None,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(f"PRAGMA busy_timeout = {self._timeout_ms}")
        return conn

    @contextmanager
    def _read_operation(self) -> Iterator[sqlite3.Connection]:
        if self._memory_connection is not None:
            with self._memory_lock:
                yield self._memory_connection
            return
        conn = self._connect_file()
        try:
            conn.execute("BEGIN")
            yield conn
            conn.commit()
        except BaseException:
            if conn.in_transaction:
                conn.rollback()
            raise
        finally:
            conn.close()

    @contextmanager
    def _write_operation(self) -> Iterator[sqlite3.Connection]:
        if self._memory_connection is not None:
            with self._memory_lock:
                conn = self._memory_connection
                conn.execute("BEGIN IMMEDIATE")
                try:
                    yield conn
                    conn.commit()
                except BaseException:
                    if conn.in_transaction:
                        conn.rollback()
                    raise
            return
        conn = self._connect_file()
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except BaseException:
            if conn.in_transaction:
                conn.rollback()
            raise
        finally:
            conn.close()

    def _now(self) -> int:
        value = int(self._clock())
        if value < 0:
            raise VTSMappingRepositoryError(VTS_MAPPING_INVALID, "clock is negative")
        return value

    def _inject_fault(self, stage: str) -> None:
        if self._fault_injector is not None:
            self._fault_injector(stage)


def _read_verified_asset() -> str:
    raw = _ASSET_PATH.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != PLATFORM_REGISTRY_MIGRATION_SHA256:
        raise VTSMappingRepositoryError(
            VTS_MAPPING_SCHEMA_NOT_READY,
            "0072 migration asset checksum drifted",
        )
    return raw.decode("utf-8")


def _normalize_mapping(mapping: Mapping[str, str]) -> dict[str, str]:
    if not isinstance(mapping, Mapping) or not mapping:
        raise VTSMappingRepositoryError(VTS_MAPPING_INVALID, "mapping must not be empty")
    normalized: dict[str, str] = {}
    for raw_tag, raw_expression in mapping.items():
        if not isinstance(raw_tag, str) or not isinstance(raw_expression, str):
            raise VTSMappingRepositoryError(
                VTS_MAPPING_INVALID, "mapping keys and values must be strings"
            )
        tag = raw_tag.strip().upper()
        expression = raw_expression.strip()
        if not tag or len(tag) > 120 or not expression or len(expression) > 240:
            raise VTSMappingRepositoryError(
                VTS_MAPPING_INVALID, "mapping key or value is invalid"
            )
        if tag in normalized and normalized[tag] != expression:
            raise VTSMappingRepositoryError(
                VTS_MAPPING_INVALID,
                "mapping contains conflicting case-insensitive emotion tags",
            )
        normalized[tag] = expression
    return dict(sorted(normalized.items()))


def _required_text(value: Any, *, field_name: str, limit: int) -> str:
    text = str(value or "").strip()
    if not text or len(text) > limit:
        raise VTSMappingRepositoryError(
            VTS_MAPPING_INVALID, f"{field_name} is missing or too long"
        )
    return text


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(
        dict(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _sha256(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _mapping_hash(mapping: Mapping[str, str]) -> str:
    canonical = _canonical_json(dict(mapping))
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def _operation_ref(namespace: str, identity: str) -> str:
    digest = hashlib.sha256(f"{namespace}\n{identity}".encode("utf-8")).hexdigest()
    return f"{namespace}://sha256/{digest}"


def _decode_canonical(raw: Any, digest: Any) -> dict[str, Any]:
    try:
        value = json.loads(str(raw))
    except json.JSONDecodeError as exc:
        raise VTSMappingRepositoryError(
            VTS_MAPPING_INVALID, "durable VTS mapping JSON is invalid"
        ) from exc
    if not isinstance(value, dict) or _sha256(_canonical_json(value)) != str(digest):
        raise VTSMappingRepositoryError(
            VTS_MAPPING_INVALID, "durable VTS mapping digest drifted"
        )
    return value


__all__ = [
    "PLATFORM_REGISTRY_MIGRATION_ASSET_KEY",
    "PLATFORM_REGISTRY_MIGRATION_SHA256",
    "VTS_MAPPING_CONFLICT",
    "VTS_MAPPING_INVALID",
    "VTS_MAPPING_NOT_FOUND",
    "VTS_MAPPING_SCHEMA_NOT_READY",
    "VTSMappingRepository",
    "VTSMappingRepositoryError",
]
