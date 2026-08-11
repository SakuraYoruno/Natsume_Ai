"""Operation-scoped persistence authority for the MCP tool registry."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3
import threading
from typing import Any

from host_brain.core.ids import now_ms


TOOL_REGISTRY_SCHEMA_NOT_READY = "TOOL_REGISTRY_SCHEMA_NOT_READY"
TOOL_REGISTRY_INVALID = "TOOL_REGISTRY_INVALID"
TOOL_REGISTRY_CONFLICT = "TOOL_REGISTRY_CONFLICT"

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
_VALID_ACCESS_LEVELS = frozenset({"PUBLIC", "INTERNAL", "RESTRICTED"})
_VALID_RISK_LEVELS = frozenset({"LOW", "MEDIUM", "HIGH"})
_REQUIRED_TABLE_COLUMNS = {
    "mcp_tool_registry": frozenset(
        {
            "provider_id",
            "tool_id",
            "name",
            "description",
            "access_level",
            "risk_level",
            "reversible",
            "version",
            "schema_hash",
            "domain_tags",
            "emotion_requirement",
            "updated_ts_ms",
        }
    ),
    "mcp_tool_sync_journal": frozenset(
        {
            "journal_id",
            "operation_ref",
            "provider_id",
            "tool_id",
            "tool_version",
            "change_kind",
            "request_json",
            "request_digest",
            "before_json",
            "after_json",
            "diff_json",
            "result_json",
            "result_digest",
            "created_at_ms",
        }
    ),
}
_REQUIRED_INDEXES = frozenset(
    {
        "idx_mcp_registry_provider",
        "idx_mcp_registry_tool",
        "idx_mcp_tool_sync_provider",
        "idx_mcp_tool_sync_created",
    }
)
_REQUIRED_TRIGGERS = frozenset(
    {
        "trg_mcp_tool_sync_journal_no_update",
        "trg_mcp_tool_sync_journal_no_delete",
    }
)


class ToolRegistryRepositoryError(RuntimeError):
    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = str(reason_code or TOOL_REGISTRY_INVALID)
        self.detail = str(detail or "tool registry persistence failed")
        super().__init__(f"{self.reason_code}: {self.detail}")


class ToolRegistryRepository:
    """Mutable registry projection plus immutable exact-replay revisions."""

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
            raise ToolRegistryRepositoryError(
                TOOL_REGISTRY_INVALID, "db_path is required"
            )
        if not callable(clock):
            raise ToolRegistryRepositoryError(
                TOOL_REGISTRY_INVALID, "clock must be callable"
            )
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
            raise ToolRegistryRepositoryError(
                TOOL_REGISTRY_SCHEMA_NOT_READY,
                f"tool registry database does not exist: {self.db_path}",
            )
        conn = self._connect_file()
        try:
            self._assert_schema(conn, require_catalog=True)
        finally:
            conn.close()

    def register(self, tool: Mapping[str, Any]) -> dict[str, Any]:
        request = _normalize_tool(tool)
        request_json = _canonical_json(request)
        request_digest = _sha256(request_json)
        provider_id = request["provider_id"]
        tool_id = request["tool_id"]
        version = request["version"]
        operation_ref = _operation_ref(provider_id, tool_id, version)

        with self._write_operation() as conn:
            existing = conn.execute(
                """
                SELECT * FROM mcp_tool_sync_journal
                WHERE provider_id = ? AND tool_id = ? AND tool_version = ?
                """,
                (provider_id, tool_id, version),
            ).fetchone()
            if existing is not None:
                record = self._journal_record(existing)
                if (
                    record["request_digest"] != request_digest
                    or record["request"] != request
                ):
                    raise ToolRegistryRepositoryError(
                        TOOL_REGISTRY_CONFLICT,
                        "tool revision is already bound to another request",
                    )
                return dict(record["result"])

            before_row = conn.execute(
                """
                SELECT * FROM mcp_tool_registry
                WHERE provider_id = ? AND tool_id = ?
                """,
                (provider_id, tool_id),
            ).fetchone()
            before = _row_to_tool(before_row) if before_row is not None else None
            created_at_ms = self._now()
            conn.execute(
                """
                INSERT INTO mcp_tool_registry(
                    provider_id, tool_id, name, description, access_level,
                    risk_level, reversible, version, schema_hash, domain_tags,
                    emotion_requirement, updated_ts_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(provider_id, tool_id) DO UPDATE SET
                    name = excluded.name,
                    description = excluded.description,
                    access_level = excluded.access_level,
                    risk_level = excluded.risk_level,
                    reversible = excluded.reversible,
                    version = excluded.version,
                    schema_hash = excluded.schema_hash,
                    domain_tags = excluded.domain_tags,
                    emotion_requirement = excluded.emotion_requirement,
                    updated_ts_ms = excluded.updated_ts_ms
                """,
                (
                    provider_id,
                    tool_id,
                    request["name"],
                    request["description"],
                    request["access_level"],
                    request["risk_level"],
                    int(request["reversible"]),
                    version,
                    request["schema_hash"],
                    _canonical_json(request["domain_tags"]),
                    _canonical_json(request["emotion_requirement"]),
                    created_at_ms,
                ),
            )
            self._inject_fault("after_registry_write")
            change_kind = "UPDATE" if before is not None else "INSERT"
            diff = _compute_diff(before, request)
            result = {
                "provider_id": provider_id,
                "tool_id": tool_id,
                "change_kind": change_kind,
                "before": before,
                "after": request,
                "diff": diff,
                "ts_ms": created_at_ms,
            }
            result_json = _canonical_json(result)
            result_digest = _sha256(result_json)
            conn.execute(
                """
                INSERT INTO mcp_tool_sync_journal(
                    operation_ref, provider_id, tool_id, tool_version,
                    change_kind, request_json, request_digest, before_json,
                    after_json, diff_json, result_json, result_digest,
                    created_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    operation_ref,
                    provider_id,
                    tool_id,
                    version,
                    change_kind,
                    request_json,
                    request_digest,
                    _canonical_json(before) if before is not None else None,
                    _canonical_json(request),
                    _canonical_json(diff),
                    result_json,
                    result_digest,
                    created_at_ms,
                ),
            )
            return result

    def get(self, *, provider_id: str, tool_id: str) -> dict[str, Any] | None:
        provider_value = _required_text(provider_id, "provider_id", 240)
        tool_value = _required_text(tool_id, "tool_id", 240)
        with self._read_operation() as conn:
            row = conn.execute(
                """
                SELECT * FROM mcp_tool_registry
                WHERE provider_id = ? AND tool_id = ?
                """,
                (provider_value, tool_value),
            ).fetchone()
            return _row_to_tool(row) if row is not None else None

    def list_by_provider(self, provider_id: str) -> list[dict[str, Any]]:
        provider_value = _required_text(provider_id, "provider_id", 240)
        with self._read_operation() as conn:
            rows = conn.execute(
                """
                SELECT * FROM mcp_tool_registry
                WHERE provider_id = ? ORDER BY tool_id ASC
                """,
                (provider_value,),
            ).fetchall()
            return [_row_to_tool(row) for row in rows]

    def list_all(self) -> list[dict[str, Any]]:
        with self._read_operation() as conn:
            rows = conn.execute(
                """
                SELECT * FROM mcp_tool_registry
                ORDER BY provider_id ASC, tool_id ASC
                """
            ).fetchall()
            return [_row_to_tool(row) for row in rows]

    def sync_log(self) -> list[dict[str, Any]]:
        with self._read_operation() as conn:
            rows = conn.execute(
                "SELECT * FROM mcp_tool_sync_journal ORDER BY journal_id ASC"
            ).fetchall()
            return [dict(self._journal_record(row)["result"]) for row in rows]

    def _journal_record(self, row: sqlite3.Row) -> dict[str, Any]:
        request = _decode_bound_json(row["request_json"], row["request_digest"])
        result = _decode_bound_json(row["result_json"], row["result_digest"])
        provider_id = str(row["provider_id"])
        tool_id = str(row["tool_id"])
        version = str(row["tool_version"])
        if str(row["operation_ref"]) != _operation_ref(
            provider_id, tool_id, version
        ):
            raise ToolRegistryRepositoryError(
                TOOL_REGISTRY_INVALID, "tool sync operation reference drifted"
            )
        if (
            request.get("provider_id") != provider_id
            or request.get("tool_id") != tool_id
            or request.get("version") != version
            or result.get("provider_id") != provider_id
            or result.get("tool_id") != tool_id
            or result.get("change_kind") != str(row["change_kind"])
            or result.get("after") != request
        ):
            raise ToolRegistryRepositoryError(
                TOOL_REGISTRY_INVALID, "tool sync journal binding drifted"
            )
        before = _decode_unbound_json(row["before_json"], allow_none=True)
        after = _decode_unbound_json(row["after_json"])
        diff = _decode_unbound_json(row["diff_json"])
        if (
            result.get("before") != before
            or result.get("after") != after
            or result.get("diff") != diff
            or result.get("ts_ms") != int(row["created_at_ms"])
        ):
            raise ToolRegistryRepositoryError(
                TOOL_REGISTRY_INVALID, "tool sync projection binding drifted"
            )
        return {
            "operation_ref": str(row["operation_ref"]),
            "request": request,
            "request_digest": str(row["request_digest"]),
            "result": result,
            "result_digest": str(row["result_digest"]),
        }

    def _initialize_memory_from_asset(self) -> None:
        connection = self._memory_connection
        if connection is None:
            raise AssertionError("memory connection is missing")
        connection.executescript(_read_verified_asset())
        self._assert_schema(connection, require_catalog=False)

    def _assert_schema(
        self,
        conn: sqlite3.Connection | None,
        *,
        require_catalog: bool,
    ) -> None:
        if conn is None:
            raise ToolRegistryRepositoryError(
                TOOL_REGISTRY_SCHEMA_NOT_READY,
                "tool registry connection is unavailable",
            )
        try:
            columns = {
                table: {
                    str(row[1])
                    for row in conn.execute(
                        f'PRAGMA table_info("{table}")'
                    ).fetchall()
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
            raise ToolRegistryRepositoryError(
                TOOL_REGISTRY_SCHEMA_NOT_READY,
                "tool registry schema could not be verified",
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
            raise ToolRegistryRepositoryError(
                TOOL_REGISTRY_SCHEMA_NOT_READY,
                "0072 tool registry schema is missing or checksum-drifted",
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
            raise ToolRegistryRepositoryError(
                TOOL_REGISTRY_INVALID, "clock is negative"
            )
        return value

    def _inject_fault(self, stage: str) -> None:
        if self._fault_injector is not None:
            self._fault_injector(stage)


def _read_verified_asset() -> str:
    raw = _ASSET_PATH.read_bytes()
    if hashlib.sha256(raw).hexdigest() != PLATFORM_REGISTRY_MIGRATION_SHA256:
        raise ToolRegistryRepositoryError(
            TOOL_REGISTRY_SCHEMA_NOT_READY,
            "0072 migration asset checksum drifted",
        )
    return raw.decode("utf-8")


def _normalize_tool(tool: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(tool, Mapping):
        raise ToolRegistryRepositoryError(
            TOOL_REGISTRY_INVALID, "tool metadata must be a mapping"
        )
    access_level = _required_text(tool.get("access_level"), "access_level", 32).upper()
    risk_level = _required_text(tool.get("risk_level"), "risk_level", 32).upper()
    if access_level not in _VALID_ACCESS_LEVELS:
        raise ToolRegistryRepositoryError(
            TOOL_REGISTRY_INVALID, f"invalid access_level: {access_level}"
        )
    if risk_level not in _VALID_RISK_LEVELS:
        raise ToolRegistryRepositoryError(
            TOOL_REGISTRY_INVALID, f"invalid risk_level: {risk_level}"
        )
    reversible = tool.get("reversible", False)
    if not isinstance(reversible, bool):
        raise ToolRegistryRepositoryError(
            TOOL_REGISTRY_INVALID, "reversible must be a boolean"
        )
    return {
        "provider_id": _required_text(tool.get("provider_id"), "provider_id", 240),
        "tool_id": _required_text(tool.get("tool_id"), "tool_id", 240),
        "name": _required_text(tool.get("name"), "name", 500),
        "description": _optional_text(tool.get("description"), "description", 8_000),
        "access_level": access_level,
        "risk_level": risk_level,
        "reversible": reversible,
        "version": _required_text(tool.get("version") or "1.0.0", "version", 120),
        "schema_hash": _optional_text(tool.get("schema_hash"), "schema_hash", 500),
        "domain_tags": _text_list(tool.get("domain_tags"), "domain_tags"),
        "emotion_requirement": _text_list(
            tool.get("emotion_requirement"), "emotion_requirement"
        ),
    }


def _row_to_tool(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "provider_id": str(row["provider_id"]),
        "tool_id": str(row["tool_id"]),
        "name": str(row["name"]),
        "description": str(row["description"] or ""),
        "access_level": str(row["access_level"]),
        "risk_level": str(row["risk_level"]),
        "reversible": bool(row["reversible"]),
        "version": str(row["version"] or "1.0.0"),
        "schema_hash": str(row["schema_hash"] or ""),
        "domain_tags": _decode_text_list(row["domain_tags"], "domain_tags"),
        "emotion_requirement": _decode_text_list(
            row["emotion_requirement"], "emotion_requirement"
        ),
    }


def _compute_diff(
    before: Mapping[str, Any] | None,
    after: Mapping[str, Any],
) -> dict[str, Any]:
    if before is None:
        return {"added": list(after.keys()), "changed": {}, "removed": []}
    changed = {
        key: {"before": before.get(key), "after": new_value}
        for key, new_value in after.items()
        if before.get(key) != new_value
    }
    return {
        "added": [],
        "changed": changed,
        "removed": [key for key in before if key not in after],
    }


def _required_text(value: Any, field_name: str, limit: int) -> str:
    text = str(value or "").strip()
    if not text or len(text) > limit:
        raise ToolRegistryRepositoryError(
            TOOL_REGISTRY_INVALID, f"{field_name} is missing or too long"
        )
    return text


def _optional_text(value: Any, field_name: str, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) > limit:
        raise ToolRegistryRepositoryError(
            TOOL_REGISTRY_INVALID, f"{field_name} is too long"
        )
    return text


def _text_list(value: Any, field_name: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ToolRegistryRepositoryError(
            TOOL_REGISTRY_INVALID, f"{field_name} must be a list of strings"
        )
    result: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ToolRegistryRepositoryError(
                TOOL_REGISTRY_INVALID, f"{field_name} must contain only strings"
            )
        text = item.strip()
        if not text or len(text) > 240:
            raise ToolRegistryRepositoryError(
                TOOL_REGISTRY_INVALID, f"{field_name} contains an invalid value"
            )
        result.append(text)
    return result


def _decode_text_list(raw: Any, field_name: str) -> list[str]:
    try:
        value = json.loads(str(raw or "[]"))
    except json.JSONDecodeError as exc:
        raise ToolRegistryRepositoryError(
            TOOL_REGISTRY_INVALID, f"durable {field_name} JSON is invalid"
        ) from exc
    return _text_list(value, field_name)


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _sha256(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _decode_bound_json(raw: Any, digest: Any) -> dict[str, Any]:
    try:
        value = json.loads(str(raw))
    except json.JSONDecodeError as exc:
        raise ToolRegistryRepositoryError(
            TOOL_REGISTRY_INVALID, "durable tool sync JSON is invalid"
        ) from exc
    if not isinstance(value, dict) or _sha256(_canonical_json(value)) != str(digest):
        raise ToolRegistryRepositoryError(
            TOOL_REGISTRY_INVALID, "durable tool sync digest drifted"
        )
    return value


def _decode_unbound_json(raw: Any, *, allow_none: bool = False) -> Any:
    if raw is None and allow_none:
        return None
    try:
        return json.loads(str(raw))
    except json.JSONDecodeError as exc:
        raise ToolRegistryRepositoryError(
            TOOL_REGISTRY_INVALID, "durable tool sync projection JSON is invalid"
        ) from exc


def _operation_ref(provider_id: str, tool_id: str, version: str) -> str:
    identity = f"{provider_id}\n{tool_id}\n{version}"
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    return f"mcp-tool-sync://sha256/{digest}"


__all__ = [
    "PLATFORM_REGISTRY_MIGRATION_ASSET_KEY",
    "PLATFORM_REGISTRY_MIGRATION_SHA256",
    "TOOL_REGISTRY_CONFLICT",
    "TOOL_REGISTRY_INVALID",
    "TOOL_REGISTRY_SCHEMA_NOT_READY",
    "ToolRegistryRepository",
    "ToolRegistryRepositoryError",
]
