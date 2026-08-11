"""Operation-scoped persistence authority for the Bond SQLite fallback."""

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


BOND_FALLBACK_INVALID = "BOND_FALLBACK_INVALID"
BOND_FALLBACK_SCHEMA_NOT_READY = "BOND_FALLBACK_SCHEMA_NOT_READY"
BOND_FALLBACK_CORRUPT = "BOND_FALLBACK_CORRUPT"

BOND_FALLBACK_MIGRATION_ASSET_KEY = "0076#bond_fallback_persistence"
BOND_FALLBACK_MIGRATION_SHA256 = (
    "a089c310961174aa925a5ecbd340094cef6392dc58ed3282ac63a19c06b43839"
)

_ASSET_PATH = (
    Path(__file__).resolve().parents[1]
    / "audit"
    / "migrations"
    / "0076_bond_fallback_persistence.sql"
)
_ALLOWED_DISPOSITIONS = frozenset(
    {"APPLIED", "APPLIED_WITH_COMPAT", "LEGACY_BACKFILL"}
)
_REQUIRED_TABLE_COLUMNS = {
    "bond_events": frozenset(
        {
            "event_id",
            "user_id",
            "session_id",
            "topic_id",
            "capability_hint",
            "evidence_refs",
            "payload",
            "ts_ms",
        }
    ),
    "bond_fallback_event_journal": frozenset(
        {
            "journal_id",
            "operation_ref",
            "event_id",
            "change_kind",
            "request_json",
            "request_digest",
            "before_json",
            "before_digest",
            "result_json",
            "result_digest",
            "created_at_ms",
        }
    ),
}
_REQUIRED_INDEXES = frozenset(
    {
        "idx_bond_user",
        "idx_bond_ts",
        "idx_bond_fallback_journal_event",
        "idx_bond_fallback_journal_created",
    }
)
_REQUIRED_TRIGGERS = frozenset(
    {
        "trg_bond_fallback_journal_no_update",
        "trg_bond_fallback_journal_no_delete",
    }
)


class BondFallbackRepositoryError(RuntimeError):
    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = str(reason_code or BOND_FALLBACK_CORRUPT)
        self.detail = str(detail or "Bond fallback persistence failed")
        super().__init__(f"{self.reason_code}: {self.detail}")


class BondFallbackRepository:
    """Mutable Bond event projection plus immutable exact-replay journal."""

    def __init__(
        self,
        db_path: str | Path,
        *,
        clock: Callable[[], int] = now_ms,
        timeout_ms: int = 5_000,
        fault_injector: Callable[[str], None] | None = None,
    ) -> None:
        raw_path = str(db_path or "").strip()
        if not raw_path or raw_path.lower().startswith("file:"):
            raise BondFallbackRepositoryError(
                BOND_FALLBACK_INVALID,
                "db_path must be a database file path or :memory:",
            )
        if not callable(clock):
            raise BondFallbackRepositoryError(
                BOND_FALLBACK_INVALID, "clock must be callable"
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
            self._configure(self._memory_connection)
            try:
                self._memory_connection.executescript(_read_verified_asset())
                self._assert_schema(self._memory_connection, require_catalog=False)
            except BaseException:
                self._memory_connection.close()
                self._memory_connection = None
                raise
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
            raise BondFallbackRepositoryError(
                BOND_FALLBACK_SCHEMA_NOT_READY,
                f"Bond fallback database does not exist: {self.db_path}",
            )
        conn = self._connect_file()
        try:
            self._assert_schema(conn, require_catalog=True)
        finally:
            conn.close()

    def write_event(self, event: Mapping[str, Any]) -> dict[str, Any]:
        request = _normalize_event(event)
        request_json = _canonical_json(request)
        request_digest = _sha256(request_json)
        event_id = request["event_id"]
        operation_ref = _operation_ref(event_id, request_digest)
        with self._write_operation() as conn:
            replay = conn.execute(
                """
                SELECT * FROM bond_fallback_event_journal
                WHERE event_id = ? AND request_digest = ?
                """,
                (event_id, request_digest),
            ).fetchone()
            if replay is not None:
                record = self._journal_record(replay)
                if record["request"] != request:
                    raise BondFallbackRepositoryError(
                        BOND_FALLBACK_CORRUPT,
                        "Bond replay digest is bound to another request",
                    )
                return dict(record["result"])

            created_at_ms = self._now()
            current = conn.execute(
                "SELECT * FROM bond_events WHERE event_id = ?", (event_id,)
            ).fetchone()
            before = _row_to_event(current) if current is not None else None
            change_kind = "UPDATE" if before is not None else "INSERT"
            conn.execute(
                """
                INSERT INTO bond_events(
                    event_id, user_id, session_id, topic_id, capability_hint,
                    evidence_refs, payload, ts_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(event_id) DO UPDATE SET
                    user_id = excluded.user_id,
                    session_id = excluded.session_id,
                    topic_id = excluded.topic_id,
                    capability_hint = excluded.capability_hint,
                    evidence_refs = excluded.evidence_refs,
                    payload = excluded.payload,
                    ts_ms = excluded.ts_ms
                """,
                (
                    event_id,
                    request["user_id"],
                    request["session_id"],
                    request["topic_id"],
                    request["capability_hint"],
                    _canonical_json(request["evidence_refs"]),
                    _canonical_json(request["payload"]),
                    request["ts_ms"],
                ),
            )
            self._fault("write_event:after_projection")
            result = json.loads(request_json)
            result_json = _canonical_json(result)
            result_digest = _sha256(result_json)
            before_json = _canonical_json(before) if before is not None else None
            before_digest = _sha256(before_json) if before_json is not None else None
            conn.execute(
                """
                INSERT INTO bond_fallback_event_journal(
                    operation_ref, event_id, change_kind, request_json,
                    request_digest, before_json, before_digest, result_json,
                    result_digest, created_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    operation_ref,
                    event_id,
                    change_kind,
                    request_json,
                    request_digest,
                    before_json,
                    before_digest,
                    result_json,
                    result_digest,
                    created_at_ms,
                ),
            )
            self._fault("write_event:after_journal")
            return result

    def get_event(self, event_id: str) -> dict[str, Any] | None:
        event_key = _required_text(event_id, "event_id", 512)
        with self._read_operation() as conn:
            row = conn.execute(
                "SELECT * FROM bond_events WHERE event_id = ?", (event_key,)
            ).fetchone()
            return _row_to_event(row) if row is not None else None

    def trace_user_events(self, user_id: str) -> list[dict[str, Any]]:
        user_key = _required_text(user_id, "user_id", 512)
        with self._read_operation() as conn:
            rows = conn.execute(
                """
                SELECT * FROM bond_events
                WHERE user_id = ? ORDER BY ts_ms ASC, event_id ASC
                """,
                (user_key,),
            ).fetchall()
            return [_row_to_event(row) for row in rows]

    def list_events(self) -> list[dict[str, Any]]:
        with self._read_operation() as conn:
            rows = conn.execute(
                "SELECT * FROM bond_events ORDER BY ts_ms ASC, event_id ASC"
            ).fetchall()
            return [_row_to_event(row) for row in rows]

    def journal_entries(self, *, event_id: str | None = None) -> list[dict[str, Any]]:
        with self._read_operation() as conn:
            if event_id is None:
                rows = conn.execute(
                    """
                    SELECT * FROM bond_fallback_event_journal
                    ORDER BY journal_id ASC
                    """
                ).fetchall()
            else:
                event_key = _required_text(event_id, "event_id", 512)
                rows = conn.execute(
                    """
                    SELECT * FROM bond_fallback_event_journal
                    WHERE event_id = ? ORDER BY journal_id ASC
                    """,
                    (event_key,),
                ).fetchall()
            return [self._journal_record(row) for row in rows]

    def _journal_record(self, row: sqlite3.Row) -> dict[str, Any]:
        request = _decode_bound_object(row["request_json"], row["request_digest"])
        result = _decode_bound_object(row["result_json"], row["result_digest"])
        event_id = str(row["event_id"])
        request_digest = str(row["request_digest"])
        if (
            request.get("event_id") != event_id
            or result != request
            or str(row["operation_ref"]) != _operation_ref(event_id, request_digest)
        ):
            raise BondFallbackRepositoryError(
                BOND_FALLBACK_CORRUPT, "Bond journal binding drifted"
            )
        try:
            if _normalize_event(request) != request:
                raise ValueError("journal request is not canonical")
        except (BondFallbackRepositoryError, ValueError) as exc:
            raise BondFallbackRepositoryError(
                BOND_FALLBACK_CORRUPT, "Bond journal request is invalid"
            ) from exc
        before = _decode_optional_bound_object(
            row["before_json"], row["before_digest"]
        )
        if before is not None:
            try:
                if _normalize_event(before) != before:
                    raise ValueError("journal before image is not canonical")
            except (BondFallbackRepositoryError, ValueError) as exc:
                raise BondFallbackRepositoryError(
                    BOND_FALLBACK_CORRUPT, "Bond journal before image is invalid"
                ) from exc
        change_kind = str(row["change_kind"])
        if (before is None and change_kind != "INSERT") or (
            before is not None and change_kind != "UPDATE"
        ):
            raise BondFallbackRepositoryError(
                BOND_FALLBACK_CORRUPT, "Bond journal change kind drifted"
            )
        return {
            "operation_ref": str(row["operation_ref"]),
            "event_id": event_id,
            "change_kind": change_kind,
            "request": request,
            "request_digest": request_digest,
            "before": before,
            "result": result,
            "result_digest": str(row["result_digest"]),
            "created_at_ms": int(row["created_at_ms"]),
        }

    def _assert_schema(
        self,
        conn: sqlite3.Connection | None,
        *,
        require_catalog: bool,
    ) -> None:
        if conn is None:
            raise BondFallbackRepositoryError(
                BOND_FALLBACK_SCHEMA_NOT_READY,
                "Bond fallback connection is unavailable",
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
                    (BOND_FALLBACK_MIGRATION_ASSET_KEY,),
                ).fetchone()
        except sqlite3.DatabaseError as exc:
            raise BondFallbackRepositoryError(
                BOND_FALLBACK_SCHEMA_NOT_READY,
                "Bond fallback schema or migration catalog is unavailable",
            ) from exc
        invalid_tables = [
            table
            for table, required in _REQUIRED_TABLE_COLUMNS.items()
            if not required <= columns.get(table, set())
        ]
        catalog_ready = not require_catalog or (
            asset is not None
            and str(asset["checksum"]) == BOND_FALLBACK_MIGRATION_SHA256
            and str(asset["disposition"]) in _ALLOWED_DISPOSITIONS
        )
        if (
            invalid_tables
            or not _REQUIRED_INDEXES <= indexes
            or not _REQUIRED_TRIGGERS <= triggers
            or not catalog_ready
        ):
            raise BondFallbackRepositoryError(
                BOND_FALLBACK_SCHEMA_NOT_READY,
                "0076 Bond fallback schema is missing or checksum-drifted",
            )

    def _connect_file(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            self.db_path,
            timeout=self._timeout_ms / 1000.0,
            isolation_level=None,
        )
        self._configure(conn)
        return conn

    def _configure(self, conn: sqlite3.Connection) -> None:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(f"PRAGMA busy_timeout = {self._timeout_ms}")

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
                    self._fault("write_event:before_commit")
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
            self._fault("write_event:before_commit")
            conn.commit()
        except BaseException:
            if conn.in_transaction:
                conn.rollback()
            raise
        finally:
            conn.close()

    def _fault(self, stage: str) -> None:
        if self._fault_injector is not None:
            self._fault_injector(stage)

    def _now(self) -> int:
        try:
            value = int(self._clock())
        except (TypeError, ValueError) as exc:
            raise BondFallbackRepositoryError(
                BOND_FALLBACK_INVALID, "clock returned an invalid timestamp"
            ) from exc
        if value < 0:
            raise BondFallbackRepositoryError(
                BOND_FALLBACK_INVALID, "clock returned a negative timestamp"
            )
        return value


def _read_verified_asset() -> str:
    try:
        raw = _ASSET_PATH.read_bytes()
    except OSError as exc:
        raise BondFallbackRepositoryError(
            BOND_FALLBACK_SCHEMA_NOT_READY,
            "0076 migration asset is unavailable",
        ) from exc
    if hashlib.sha256(raw).hexdigest() != BOND_FALLBACK_MIGRATION_SHA256:
        raise BondFallbackRepositoryError(
            BOND_FALLBACK_SCHEMA_NOT_READY,
            "0076 migration asset checksum drifted",
        )
    return raw.decode("utf-8")


def _normalize_event(event: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(event, Mapping):
        raise BondFallbackRepositoryError(
            BOND_FALLBACK_INVALID, "Bond event must be a mapping"
        )
    evidence = _string_list(event.get("evidence_refs"), "evidence_refs")
    if not evidence:
        raise BondFallbackRepositoryError(
            BOND_FALLBACK_INVALID, "evidence_refs must not be empty"
        )
    payload = event.get("payload")
    if payload is None:
        payload = {}
    if not isinstance(payload, Mapping):
        raise BondFallbackRepositoryError(
            BOND_FALLBACK_INVALID, "payload must be a mapping"
        )
    try:
        normalized_payload = json.loads(_canonical_json(dict(payload)))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise BondFallbackRepositoryError(
            BOND_FALLBACK_INVALID, "payload must be canonical JSON data"
        ) from exc
    if not isinstance(normalized_payload, dict):
        raise BondFallbackRepositoryError(
            BOND_FALLBACK_INVALID, "payload must encode a JSON object"
        )
    try:
        ts_ms = int(event.get("ts_ms"))
    except (TypeError, ValueError) as exc:
        raise BondFallbackRepositoryError(
            BOND_FALLBACK_INVALID, "ts_ms must be an integer"
        ) from exc
    if ts_ms < 0:
        raise BondFallbackRepositoryError(
            BOND_FALLBACK_INVALID, "ts_ms must be non-negative"
        )
    normalized = {
        "event_id": _required_text(event.get("event_id"), "event_id", 512),
        "user_id": _required_text(event.get("user_id"), "user_id", 512),
        "session_id": _required_text(event.get("session_id"), "session_id", 512),
        "topic_id": _required_text(event.get("topic_id"), "topic_id", 512),
        "capability_hint": _required_text(
            event.get("capability_hint"), "capability_hint", 512
        ),
        "evidence_refs": evidence,
        "payload": normalized_payload,
        "ts_ms": ts_ms,
    }
    if len(_canonical_json(evidence).encode("utf-8")) > 1_048_576 or len(
        _canonical_json(normalized_payload).encode("utf-8")
    ) > 2_097_152:
        raise BondFallbackRepositoryError(
            BOND_FALLBACK_INVALID, "Bond event JSON exceeds the durable limit"
        )
    return normalized


def _row_to_event(row: sqlite3.Row) -> dict[str, Any]:
    evidence = _decode_json(row["evidence_refs"], field="evidence_refs")
    payload = _decode_json(row["payload"], field="payload")
    if not isinstance(evidence, list) or not evidence or not all(
        isinstance(item, str) and item.strip() for item in evidence
    ):
        raise BondFallbackRepositoryError(
            BOND_FALLBACK_CORRUPT, "durable evidence_refs is invalid"
        )
    if not isinstance(payload, dict):
        raise BondFallbackRepositoryError(
            BOND_FALLBACK_CORRUPT, "durable payload is invalid"
        )
    try:
        ts_ms = int(row["ts_ms"])
    except (TypeError, ValueError) as exc:
        raise BondFallbackRepositoryError(
            BOND_FALLBACK_CORRUPT, "durable ts_ms is invalid"
        ) from exc
    if ts_ms < 0:
        raise BondFallbackRepositoryError(
            BOND_FALLBACK_CORRUPT, "durable ts_ms is negative"
        )
    return {
        "event_id": _durable_text(row["event_id"], "event_id", 512),
        "user_id": _durable_text(row["user_id"], "user_id", 512),
        "session_id": _durable_text(row["session_id"], "session_id", 512),
        "topic_id": _durable_text(row["topic_id"], "topic_id", 512),
        "capability_hint": _durable_text(
            row["capability_hint"], "capability_hint", 512
        ),
        "evidence_refs": list(evidence),
        "payload": dict(payload),
        "ts_ms": ts_ms,
    }


def _required_text(value: Any, field: str, limit: int) -> str:
    text = str(value or "").strip()
    if not text or len(text) > limit:
        raise BondFallbackRepositoryError(
            BOND_FALLBACK_INVALID, f"{field} is missing or too long"
        )
    return text


def _durable_text(value: Any, field: str, limit: int) -> str:
    text = str(value or "").strip()
    if not text or len(text) > limit:
        raise BondFallbackRepositoryError(
            BOND_FALLBACK_CORRUPT, f"durable {field} is invalid"
        )
    return text


def _string_list(value: Any, field: str) -> list[str]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise BondFallbackRepositoryError(
            BOND_FALLBACK_INVALID, f"{field} must be a list of strings"
        )
    result: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise BondFallbackRepositoryError(
                BOND_FALLBACK_INVALID, f"{field} must contain only strings"
            )
        text = item.strip()
        if not text or len(text) > 2_048:
            raise BondFallbackRepositoryError(
                BOND_FALLBACK_INVALID, f"{field} contains an invalid reference"
            )
        result.append(text)
    return result


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


def _operation_ref(event_id: str, request_digest: str) -> str:
    identity = f"{event_id}\n{request_digest}"
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    return f"bond-fallback://sha256/{digest}"


def _decode_json(raw: Any, *, field: str) -> Any:
    try:
        return json.loads(str(raw))
    except json.JSONDecodeError as exc:
        raise BondFallbackRepositoryError(
            BOND_FALLBACK_CORRUPT, f"durable {field} JSON is invalid"
        ) from exc


def _decode_bound_object(raw: Any, digest: Any) -> dict[str, Any]:
    value = _decode_json(raw, field="journal")
    if not isinstance(value, dict) or _sha256(_canonical_json(value)) != str(digest):
        raise BondFallbackRepositoryError(
            BOND_FALLBACK_CORRUPT, "durable Bond journal digest drifted"
        )
    return value


def _decode_optional_bound_object(raw: Any, digest: Any) -> dict[str, Any] | None:
    if raw is None and digest is None:
        return None
    if raw is None or digest is None:
        raise BondFallbackRepositoryError(
            BOND_FALLBACK_CORRUPT, "durable Bond before image binding is incomplete"
        )
    value = _decode_json(raw, field="journal before")
    if not isinstance(value, dict) or _sha256(_canonical_json(value)) != str(digest):
        raise BondFallbackRepositoryError(
            BOND_FALLBACK_CORRUPT, "durable Bond journal before image drifted"
        )
    return value


__all__ = [
    "BOND_FALLBACK_CORRUPT",
    "BOND_FALLBACK_INVALID",
    "BOND_FALLBACK_MIGRATION_ASSET_KEY",
    "BOND_FALLBACK_MIGRATION_SHA256",
    "BOND_FALLBACK_SCHEMA_NOT_READY",
    "BondFallbackRepository",
    "BondFallbackRepositoryError",
]
