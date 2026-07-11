"""Durable runtime event timeline and replay primitives.

The in-memory Tribe EventBus remains a low-latency notification mechanism.
This module is the recovery truth for TASK-0474: versioned runtime events are
persisted in SQLite with a monotonic cursor, idempotent dedupe key, and a
fail-closed redaction gate before anything becomes replayable.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from typing import Any, Callable, Iterator, Mapping

from host_brain.core.ids import now_ms


RUNTIME_EVENT_SCHEMA_VERSION = "Natsume.runtime.event.v1"

RUNTIME_EVENT_SCHEMA_NOT_READY = "RUNTIME_EVENT_SCHEMA_NOT_READY"
RUNTIME_EVENT_INVALID = "RUNTIME_EVENT_INVALID"
RUNTIME_EVENT_FORBIDDEN_FIELD = "RUNTIME_EVENT_FORBIDDEN_FIELD"
RUNTIME_EVENT_OUTBOX_PUBLISH_FAILED = "RUNTIME_EVENT_OUTBOX_PUBLISH_FAILED"

RUNTIME_EVENT_TABLES = frozenset({"runtime_event_timeline"})
RUNTIME_EVENT_REQUIRED_COLUMNS = {
    "runtime_event_timeline": frozenset(
        {
            "cursor",
            "event_id",
            "schema_version",
            "event_type",
            "source_kind",
            "source_ref",
            "aggregate_type",
            "aggregate_id",
            "aggregate_version",
            "payload_ref",
            "payload_json",
            "payload_digest",
            "evidence_refs_json",
            "dedupe_key",
            "redaction_level",
            "ts_ms",
            "created_ts_ms",
            "published_ts_ms",
        }
    )
}

RUNTIME_EVENT_SOURCE_KINDS = frozenset(
    {
        "TASK_STORE_OUTBOX",
        "TRIBE_EVENT",
        "GRAPH_EVIDENCE",
        "PENTEST",
        "BIO_OPS",
        "REVIEW",
        "VERIFICATION",
        "TASK",
        "SYSTEM",
    }
)

RUNTIME_EVENT_REDACTION_LEVELS = frozenset({"PUBLIC", "INTERNAL_REDACTED"})

_DURABLE_REFERENCE_PATTERN = re.compile(
    r"^[a-z][a-z0-9+.-]{1,31}://"
    r"[A-Za-z0-9][A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]{0,511}$"
)
_SHA256_REF_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_FORBIDDEN_KEY_PATTERN = re.compile(
    r"("
    r"raw[_-]?thought|chain[_-]?of[_-]?thought|reasoning[_-]?trace|"
    r"private[_-]?body|raw[_-]?body|password|secret|cookie|"
    r"api[_-]?key|access[_-]?token|refresh[_-]?token|resume[_-]?token|"
    r"authorization|bearer"
    r")",
    re.IGNORECASE,
)
_FORBIDDEN_VALUE_PATTERNS = (
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/\-=]+", re.IGNORECASE),
    re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}\b"),
    re.compile(r"review_resume::[A-Za-z0-9._~+/\-=]+"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bSet-Cookie\s*:", re.IGNORECASE),
)
_MAX_PAYLOAD_JSON_BYTES = 16_384
_MAX_EVIDENCE_REFS = 64


class RuntimeEventError(RuntimeError):
    """Fail-closed runtime event error with a stable reason code."""

    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = str(reason_code or RUNTIME_EVENT_INVALID)
        self.detail = str(detail or "runtime event operation failed")
        super().__init__(f"{self.reason_code}: {self.detail}")

    def to_dict(self) -> dict[str, str]:
        return {"reason_code": self.reason_code, "detail": self.detail}


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _loads_object(value: Any, *, field_name: str) -> dict[str, Any]:
    try:
        parsed = json.loads(str(value or "{}"))
    except json.JSONDecodeError as exc:
        raise RuntimeEventError(RUNTIME_EVENT_INVALID, f"{field_name} is invalid JSON") from exc
    if not isinstance(parsed, dict):
        raise RuntimeEventError(RUNTIME_EVENT_INVALID, f"{field_name} must be a JSON object")
    return parsed


def _loads_list(value: Any, *, field_name: str) -> list[Any]:
    try:
        parsed = json.loads(str(value or "[]"))
    except json.JSONDecodeError as exc:
        raise RuntimeEventError(RUNTIME_EVENT_INVALID, f"{field_name} is invalid JSON") from exc
    if not isinstance(parsed, list):
        raise RuntimeEventError(RUNTIME_EVENT_INVALID, f"{field_name} must be a JSON list")
    return parsed


def _text(value: Any) -> str:
    return str(value or "").strip()


def _require_text(value: Any, *, field_name: str, max_length: int = 512) -> str:
    text = _text(value)
    if not text:
        raise RuntimeEventError(RUNTIME_EVENT_INVALID, f"{field_name} is required")
    if len(text) > max_length:
        raise RuntimeEventError(RUNTIME_EVENT_INVALID, f"{field_name} exceeds {max_length} chars")
    return text


def _require_durable_ref(value: Any, *, field_name: str) -> str:
    text = _require_text(value, field_name=field_name)
    if not _DURABLE_REFERENCE_PATTERN.fullmatch(text):
        raise RuntimeEventError(RUNTIME_EVENT_INVALID, f"{field_name} must be a durable reference")
    return text


def _normalize_source_kind(value: Any) -> str:
    source_kind = _require_text(value, field_name="source_kind", max_length=64).upper()
    if source_kind not in RUNTIME_EVENT_SOURCE_KINDS:
        raise RuntimeEventError(RUNTIME_EVENT_INVALID, f"unsupported source_kind: {source_kind}")
    return source_kind


def _normalize_redaction_level(value: Any) -> str:
    redaction_level = _text(value).upper() or "PUBLIC"
    if redaction_level not in RUNTIME_EVENT_REDACTION_LEVELS:
        raise RuntimeEventError(
            RUNTIME_EVENT_INVALID,
            f"unsupported redaction_level: {redaction_level}",
        )
    return redaction_level


def _normalize_evidence_refs(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise RuntimeEventError(RUNTIME_EVENT_INVALID, "evidence_refs must be a list")
    refs = tuple(_require_durable_ref(item, field_name="evidence_refs") for item in value)
    if len(refs) > _MAX_EVIDENCE_REFS:
        raise RuntimeEventError(
            RUNTIME_EVENT_INVALID,
            f"evidence_refs exceed {_MAX_EVIDENCE_REFS}",
        )
    return refs


def _scan_forbidden(value: Any, *, path: str) -> None:
    if isinstance(value, Mapping):
        for raw_key, raw_value in value.items():
            key = str(raw_key or "").strip()
            if not key:
                raise RuntimeEventError(RUNTIME_EVENT_INVALID, f"{path} contains an empty key")
            if _FORBIDDEN_KEY_PATTERN.search(key):
                raise RuntimeEventError(
                    RUNTIME_EVENT_FORBIDDEN_FIELD,
                    f"runtime event payload contains forbidden field: {path}.{key}",
                )
            _scan_forbidden(raw_value, path=f"{path}.{key}")
        return
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _scan_forbidden(item, path=f"{path}[{index}]")
        return
    if isinstance(value, bytes):
        raise RuntimeEventError(
            RUNTIME_EVENT_FORBIDDEN_FIELD,
            f"runtime event payload contains raw bytes: {path}",
        )
    if isinstance(value, str):
        for pattern in _FORBIDDEN_VALUE_PATTERNS:
            if pattern.search(value):
                raise RuntimeEventError(
                    RUNTIME_EVENT_FORBIDDEN_FIELD,
                    f"runtime event payload contains forbidden value pattern: {path}",
                )


def _json_safe(value: Any, *, path: str = "payload") -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item, path=f"{path}.{key}") for key, item in value.items()}
    if isinstance(value, tuple):
        return [_json_safe(item, path=f"{path}[]") for item in value]
    if isinstance(value, list):
        return [_json_safe(item, path=f"{path}[]") for item in value]
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    raise RuntimeEventError(
        RUNTIME_EVENT_INVALID,
        f"runtime event payload contains non-JSON value at {path}: {type(value).__name__}",
    )


def sanitize_runtime_event_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Return a JSON-safe payload after fail-closed sensitive-field scanning."""

    if not isinstance(payload, Mapping):
        raise RuntimeEventError(RUNTIME_EVENT_INVALID, "runtime event payload must be a mapping")
    safe_payload = _json_safe(dict(payload))
    _scan_forbidden(safe_payload, path="payload")
    payload_json = _dumps(safe_payload)
    if len(payload_json.encode("utf-8")) > _MAX_PAYLOAD_JSON_BYTES:
        raise RuntimeEventError(
            RUNTIME_EVENT_INVALID,
            f"runtime event payload exceeds {_MAX_PAYLOAD_JSON_BYTES} bytes",
        )
    return safe_payload


def runtime_event_payload_digest(payload: Mapping[str, Any]) -> str:
    payload_json = _dumps(sanitize_runtime_event_payload(payload))
    return f"sha256:{hashlib.sha256(payload_json.encode('utf-8')).hexdigest()}"


def build_runtime_event_dedupe_key(
    *,
    source_kind: str,
    source_ref: str,
    event_type: str,
    aggregate_type: str,
    aggregate_id: str,
    aggregate_version: int,
) -> str:
    normalized_version = int(aggregate_version)
    if normalized_version < 0:
        raise RuntimeEventError(RUNTIME_EVENT_INVALID, "aggregate_version must be non-negative")
    canonical = _dumps(
        {
            "aggregate_id": _require_text(aggregate_id, field_name="aggregate_id"),
            "aggregate_type": _require_text(aggregate_type, field_name="aggregate_type"),
            "aggregate_version": normalized_version,
            "event_type": _require_text(event_type, field_name="event_type"),
            "source_kind": _normalize_source_kind(source_kind),
            "source_ref": _require_durable_ref(source_ref, field_name="source_ref"),
        }
    )
    return f"sha256:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"


def _event_id_from_dedupe_key(dedupe_key: str) -> str:
    digest = _require_text(dedupe_key, field_name="dedupe_key").removeprefix("sha256:")
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        digest = hashlib.sha256(dedupe_key.encode("utf-8")).hexdigest()
    return f"runtimeevent://sha256/{digest}"


@dataclass(frozen=True, slots=True)
class RuntimeEvent:
    event_type: str
    source_kind: str
    source_ref: str
    aggregate_type: str
    aggregate_id: str
    aggregate_version: int
    payload_ref: str
    payload: Mapping[str, Any]
    evidence_refs: tuple[str, ...] = ()
    dedupe_key: str | None = None
    event_id: str | None = None
    redaction_level: str = "PUBLIC"
    schema_version: str = RUNTIME_EVENT_SCHEMA_VERSION
    ts_ms: int = 0

    @classmethod
    def create(
        cls,
        *,
        event_type: str,
        source_kind: str,
        source_ref: str,
        aggregate_type: str,
        aggregate_id: str,
        aggregate_version: int = 0,
        payload_ref: str,
        payload: Mapping[str, Any],
        evidence_refs: tuple[str, ...] = (),
        dedupe_key: str | None = None,
        event_id: str | None = None,
        redaction_level: str = "PUBLIC",
        ts_ms: int = 0,
    ) -> "RuntimeEvent":
        normalized_source_kind = _normalize_source_kind(source_kind)
        normalized_source_ref = _require_durable_ref(source_ref, field_name="source_ref")
        normalized_event_type = _require_text(event_type, field_name="event_type", max_length=128)
        normalized_aggregate_type = _require_text(
            aggregate_type,
            field_name="aggregate_type",
            max_length=128,
        )
        normalized_aggregate_id = _require_text(
            aggregate_id,
            field_name="aggregate_id",
            max_length=512,
        )
        normalized_version = int(aggregate_version)
        if normalized_version < 0:
            raise RuntimeEventError(RUNTIME_EVENT_INVALID, "aggregate_version must be non-negative")
        normalized_payload_ref = _require_durable_ref(payload_ref, field_name="payload_ref")
        normalized_payload = sanitize_runtime_event_payload(payload)
        normalized_refs = _normalize_evidence_refs(evidence_refs)
        normalized_dedupe_key = _text(dedupe_key) or build_runtime_event_dedupe_key(
            source_kind=normalized_source_kind,
            source_ref=normalized_source_ref,
            event_type=normalized_event_type,
            aggregate_type=normalized_aggregate_type,
            aggregate_id=normalized_aggregate_id,
            aggregate_version=normalized_version,
        )
        if not _SHA256_REF_PATTERN.fullmatch(normalized_dedupe_key):
            raise RuntimeEventError(RUNTIME_EVENT_INVALID, "dedupe_key must be sha256:<64 hex>")
        normalized_event_id = _text(event_id) or _event_id_from_dedupe_key(normalized_dedupe_key)
        _require_durable_ref(normalized_event_id, field_name="event_id")
        normalized_ts_ms = int(ts_ms or 0)
        if normalized_ts_ms < 0:
            raise RuntimeEventError(RUNTIME_EVENT_INVALID, "ts_ms must be non-negative")
        return cls(
            event_type=normalized_event_type,
            source_kind=normalized_source_kind,
            source_ref=normalized_source_ref,
            aggregate_type=normalized_aggregate_type,
            aggregate_id=normalized_aggregate_id,
            aggregate_version=normalized_version,
            payload_ref=normalized_payload_ref,
            payload=normalized_payload,
            evidence_refs=normalized_refs,
            dedupe_key=normalized_dedupe_key,
            event_id=normalized_event_id,
            redaction_level=_normalize_redaction_level(redaction_level),
            ts_ms=normalized_ts_ms,
        )

    def validate(self) -> None:
        if self.schema_version != RUNTIME_EVENT_SCHEMA_VERSION:
            raise RuntimeEventError(RUNTIME_EVENT_INVALID, "unsupported runtime event schema_version")
        RuntimeEvent.create(
            event_type=self.event_type,
            source_kind=self.source_kind,
            source_ref=self.source_ref,
            aggregate_type=self.aggregate_type,
            aggregate_id=self.aggregate_id,
            aggregate_version=self.aggregate_version,
            payload_ref=self.payload_ref,
            payload=self.payload,
            evidence_refs=tuple(self.evidence_refs),
            dedupe_key=self.dedupe_key,
            event_id=self.event_id,
            redaction_level=self.redaction_level,
            ts_ms=self.ts_ms,
        )


def _runtime_event_record(row: sqlite3.Row) -> dict[str, Any]:
    record = dict(row)
    payload = _loads_object(record.pop("payload_json"), field_name="runtime_event_timeline.payload_json")
    evidence_refs = _loads_list(
        record.pop("evidence_refs_json"),
        field_name="runtime_event_timeline.evidence_refs_json",
    )
    record["payload"] = sanitize_runtime_event_payload(payload)
    record["evidence_refs"] = _normalize_evidence_refs(evidence_refs)
    event = RuntimeEvent.create(
        event_type=record["event_type"],
        source_kind=record["source_kind"],
        source_ref=record["source_ref"],
        aggregate_type=record["aggregate_type"],
        aggregate_id=record["aggregate_id"],
        aggregate_version=int(record["aggregate_version"]),
        payload_ref=record["payload_ref"],
        payload=record["payload"],
        evidence_refs=record["evidence_refs"],
        dedupe_key=record["dedupe_key"],
        event_id=record["event_id"],
        redaction_level=record["redaction_level"],
        ts_ms=int(record["ts_ms"]),
    )
    expected_digest = runtime_event_payload_digest(event.payload)
    if record["payload_digest"] != expected_digest:
        raise RuntimeEventError(
            RUNTIME_EVENT_INVALID,
            "runtime event payload_digest does not match payload_json",
        )
    record["evidence_refs"] = list(record["evidence_refs"])
    return record


class RuntimeEventStore:
    """SQLite-backed durable event timeline with monotonic cursor replay."""

    def __init__(
        self,
        db_path: str | Path,
        *,
        clock: Callable[[], int] = now_ms,
        timeout_ms: int = 5_000,
        verify_schema: bool = True,
    ) -> None:
        raw_path = str(db_path or "").strip()
        if not raw_path or raw_path == ":memory:" or raw_path.lower().startswith("file:"):
            raise RuntimeEventError(
                RUNTIME_EVENT_INVALID,
                "RuntimeEventStore requires a durable sqlite file path",
            )
        self.db_path = Path(raw_path)
        self._clock = clock
        self._timeout_ms = max(1, int(timeout_ms))
        if verify_schema:
            self.assert_schema_ready()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            str(self.db_path),
            timeout=self._timeout_ms / 1000.0,
            isolation_level=None,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(f"PRAGMA busy_timeout = {self._timeout_ms}")
        return conn

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _now(self) -> int:
        return int(self._clock())

    def assert_schema_ready(self) -> None:
        if not self.db_path.exists():
            raise RuntimeEventError(
                RUNTIME_EVENT_SCHEMA_NOT_READY,
                f"runtime event database does not exist: {self.db_path}",
            )
        conn = self._connect()
        try:
            rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
            present = {str(row[0]) for row in rows}
            missing_tables = sorted(RUNTIME_EVENT_TABLES - present)
            if missing_tables:
                raise RuntimeEventError(
                    RUNTIME_EVENT_SCHEMA_NOT_READY,
                    f"runtime event migration 0056 is not applied; missing tables: {missing_tables}",
                )
            missing_columns: dict[str, list[str]] = {}
            for table, required in RUNTIME_EVENT_REQUIRED_COLUMNS.items():
                columns = {
                    str(column[1])
                    for column in conn.execute(f"PRAGMA table_info({table})").fetchall()
                }
                missing = sorted(required - columns)
                if missing:
                    missing_columns[table] = missing
            if missing_columns:
                raise RuntimeEventError(
                    RUNTIME_EVENT_SCHEMA_NOT_READY,
                    f"runtime event schema is stale; missing columns: {missing_columns}",
                )
        finally:
            conn.close()

    def append_event(self, event: RuntimeEvent | Mapping[str, Any]) -> dict[str, Any]:
        if isinstance(event, RuntimeEvent):
            item = RuntimeEvent.create(
                event_type=event.event_type,
                source_kind=event.source_kind,
                source_ref=event.source_ref,
                aggregate_type=event.aggregate_type,
                aggregate_id=event.aggregate_id,
                aggregate_version=event.aggregate_version,
                payload_ref=event.payload_ref,
                payload=event.payload,
                evidence_refs=tuple(event.evidence_refs),
                dedupe_key=event.dedupe_key,
                event_id=event.event_id,
                redaction_level=event.redaction_level,
                ts_ms=event.ts_ms,
            )
        else:
            item = RuntimeEvent.create(**dict(event))
        payload = sanitize_runtime_event_payload(item.payload)
        payload_json = _dumps(payload)
        payload_digest = f"sha256:{hashlib.sha256(payload_json.encode('utf-8')).hexdigest()}"
        ts_ms = int(item.ts_ms or self._now())
        created_ts_ms = self._now()
        with self._transaction() as conn:
            try:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO runtime_event_timeline(
                        event_id, schema_version, event_type, source_kind,
                        source_ref, aggregate_type, aggregate_id,
                        aggregate_version, payload_ref, payload_json,
                        payload_digest, evidence_refs_json, dedupe_key,
                        redaction_level, ts_ms, created_ts_ms, published_ts_ms
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        item.event_id,
                        RUNTIME_EVENT_SCHEMA_VERSION,
                        item.event_type,
                        item.source_kind,
                        item.source_ref,
                        item.aggregate_type,
                        item.aggregate_id,
                        int(item.aggregate_version),
                        item.payload_ref,
                        payload_json,
                        payload_digest,
                        _dumps(list(item.evidence_refs)),
                        item.dedupe_key,
                        item.redaction_level,
                        ts_ms,
                        created_ts_ms,
                        created_ts_ms,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise RuntimeEventError(
                    RUNTIME_EVENT_INVALID,
                    f"runtime event identity conflict: {item.event_id}",
                ) from exc
            row = conn.execute(
                "SELECT * FROM runtime_event_timeline WHERE dedupe_key = ?",
                (item.dedupe_key,),
            ).fetchone()
            if row is None:
                raise RuntimeEventError(
                    RUNTIME_EVENT_INVALID,
                    "runtime event insert failed without a retrievable dedupe row",
                )
            return _runtime_event_record(row)

    def list_events(
        self,
        *,
        after_cursor: int = 0,
        limit: int = 100,
        event_type: str | None = None,
        source_kind: str | None = None,
    ) -> list[dict[str, Any]]:
        after = max(0, int(after_cursor))
        size = min(1000, max(1, int(limit)))
        clauses = ["cursor > ?"]
        params: list[Any] = [after]
        if _text(event_type):
            clauses.append("event_type = ?")
            params.append(_text(event_type))
        if _text(source_kind):
            clauses.append("source_kind = ?")
            params.append(_normalize_source_kind(source_kind))
        params.append(size)
        conn = self._connect()
        try:
            rows = conn.execute(
                f"""
                SELECT * FROM runtime_event_timeline
                WHERE {' AND '.join(clauses)}
                ORDER BY cursor ASC
                LIMIT ?
                """,
                tuple(params),
            ).fetchall()
            return [_runtime_event_record(row) for row in rows]
        finally:
            conn.close()

    def append_task_outbox(self, outbox: Mapping[str, Any]) -> dict[str, Any]:
        row = dict(outbox)
        outbox_id = _require_text(row.get("outbox_id"), field_name="outbox_id")
        aggregate_type = _require_text(row.get("aggregate_type"), field_name="aggregate_type")
        aggregate_id = _require_text(row.get("aggregate_id"), field_name="aggregate_id")
        aggregate_version = int(row.get("aggregate_version"))
        event_type = _require_text(row.get("event_type"), field_name="event_type")
        payload_ref = _require_durable_ref(row.get("payload_ref"), field_name="payload_ref")
        evidence_refs = _normalize_evidence_refs(row.get("evidence_refs") or ())
        source_ref = f"taskstore://outbox/{outbox_id}"
        idempotency_key = _require_text(row.get("idempotency_key"), field_name="idempotency_key")
        idempotency_digest = f"sha256:{hashlib.sha256(idempotency_key.encode('utf-8')).hexdigest()}"
        payload: dict[str, Any] = {
            "outbox_id": outbox_id,
            "aggregate_type": aggregate_type,
            "aggregate_id": aggregate_id,
            "aggregate_version": aggregate_version,
            "event_type": event_type,
            "payload_ref": payload_ref,
            "evidence_refs": list(evidence_refs),
            "idempotency_digest": idempotency_digest,
            "status": _text(row.get("status")) or "PENDING",
        }
        if _text(row.get("attempt_id")):
            payload["attempt_ref"] = f"taskstore://attempt/{_text(row.get('attempt_id'))}"
        return self.append_event(
            RuntimeEvent.create(
                event_type=event_type,
                source_kind="TASK_STORE_OUTBOX",
                source_ref=source_ref,
                aggregate_type=aggregate_type,
                aggregate_id=aggregate_id,
                aggregate_version=aggregate_version,
                payload_ref=payload_ref,
                payload=payload,
                evidence_refs=evidence_refs,
                dedupe_key=build_runtime_event_dedupe_key(
                    source_kind="TASK_STORE_OUTBOX",
                    source_ref=source_ref,
                    event_type=event_type,
                    aggregate_type=aggregate_type,
                    aggregate_id=aggregate_id,
                    aggregate_version=aggregate_version,
                ),
                ts_ms=int(row.get("created_ts_ms") or self._now()),
            )
        )

    def append_reference_event(
        self,
        *,
        source_kind: str,
        source_ref: str,
        event_type: str,
        aggregate_type: str,
        aggregate_id: str,
        aggregate_version: int = 0,
        payload_ref: str,
        attributes: Mapping[str, Any] | None = None,
        evidence_refs: tuple[str, ...] = (),
        ts_ms: int = 0,
    ) -> dict[str, Any]:
        payload = {
            "source_ref": _require_durable_ref(source_ref, field_name="source_ref"),
            "payload_ref": _require_durable_ref(payload_ref, field_name="payload_ref"),
            "attributes": dict(attributes or {}),
        }
        return self.append_event(
            RuntimeEvent.create(
                event_type=event_type,
                source_kind=source_kind,
                source_ref=source_ref,
                aggregate_type=aggregate_type,
                aggregate_id=aggregate_id,
                aggregate_version=aggregate_version,
                payload_ref=payload_ref,
                payload=payload,
                evidence_refs=evidence_refs,
                ts_ms=ts_ms,
            )
        )

    def append_tribe_event(self, tribe_event: Any) -> dict[str, Any]:
        data = tribe_event.to_dict() if hasattr(tribe_event, "to_dict") else dict(tribe_event)
        event_id = _require_text(data.get("event_id"), field_name="tribe.event_id")
        event_type = _require_text(data.get("event_type"), field_name="tribe.event_type")
        payload = data.get("payload") if isinstance(data.get("payload"), Mapping) else {}
        source_ref = f"tribe://event/{event_id}"
        payload_field_names = sorted(str(key) for key in payload.keys())
        forbidden_fields = [name for name in payload_field_names if _FORBIDDEN_KEY_PATTERN.search(name)]
        if forbidden_fields:
            raise RuntimeEventError(
                RUNTIME_EVENT_FORBIDDEN_FIELD,
                f"tribe event payload declares forbidden fields: {forbidden_fields}",
            )
        public_payload = {
            "tribe_event_id": event_id,
            "schema_version": _text(data.get("schema_version")) or "tribe.event.v1",
            "source_agent": _require_text(data.get("source_agent"), field_name="tribe.source_agent"),
            "target_agent": _text(data.get("target_agent")) or None,
            "payload_field_names": payload_field_names,
        }
        return self.append_event(
            RuntimeEvent.create(
                event_type=event_type,
                source_kind="TRIBE_EVENT",
                source_ref=source_ref,
                aggregate_type="tribe_event",
                aggregate_id=event_id,
                aggregate_version=0,
                payload_ref=source_ref,
                payload=public_payload,
                ts_ms=int(data.get("ts_ms") or self._now()),
            )
        )


@dataclass(slots=True)
class RuntimeOutboxPublisher:
    """Publish TaskStore outbox rows into the runtime event timeline.

    The sequence is intentionally append-then-mark:
    1. Write an idempotent runtime event.
    2. Mark the TaskStore outbox row PUBLISHED only after the event exists.

    If step 2 fails after step 1 succeeds, a later retry reuses the same
    dedupe key and then marks the still-pending outbox row, preserving
    at-least-once delivery without duplicate client-side state changes.
    """

    task_store: Any
    event_store: RuntimeEventStore
    published: list[dict[str, Any]] = field(default_factory=list)

    def publish_pending(self, *, limit: int = 100) -> dict[str, Any]:
        pending = list(self.task_store.list_pending_outbox(limit=limit))
        published: list[dict[str, Any]] = []
        for row in pending:
            try:
                event_record = self.event_store.append_task_outbox(row)
                marked = self.task_store.mark_outbox_published(
                    str(row["outbox_id"]),
                    expected_version=int(row["status_version"]),
                )
            except Exception as exc:
                raise RuntimeEventError(
                    RUNTIME_EVENT_OUTBOX_PUBLISH_FAILED,
                    f"failed to publish outbox {row.get('outbox_id')}: {exc}",
                ) from exc
            published.append(
                {
                    "outbox_id": str(row["outbox_id"]),
                    "cursor": int(event_record["cursor"]),
                    "event_id": str(event_record["event_id"]),
                    "dedupe_key": str(event_record["dedupe_key"]),
                    "outbox_status": str(marked["status"]),
                    "outbox_status_version": int(marked["status_version"]),
                }
            )
        self.published.extend(published)
        return {
            "status": "OK",
            "pending_scanned": len(pending),
            "published_count": len(published),
            "published": published,
        }


__all__ = [
    "RUNTIME_EVENT_FORBIDDEN_FIELD",
    "RUNTIME_EVENT_INVALID",
    "RUNTIME_EVENT_OUTBOX_PUBLISH_FAILED",
    "RUNTIME_EVENT_SCHEMA_NOT_READY",
    "RUNTIME_EVENT_SCHEMA_VERSION",
    "RUNTIME_EVENT_SOURCE_KINDS",
    "RUNTIME_EVENT_TABLES",
    "RuntimeEvent",
    "RuntimeEventError",
    "RuntimeEventStore",
    "RuntimeOutboxPublisher",
    "build_runtime_event_dedupe_key",
    "runtime_event_payload_digest",
    "sanitize_runtime_event_payload",
]
