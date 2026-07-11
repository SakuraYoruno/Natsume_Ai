"""Catalog-owned durable Gateway transport primitives for TASK-0488.

The store deliberately contains no runtime schema creation.  Migration 0059
must be applied by the central audit migration runner before construction.
Raw payloads are never included in errors or logs; every read used for
delivery is checked against its persisted SHA-256 digest.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time
from typing import Any, Iterator
import uuid


GATEWAY_TRANSPORT_MIGRATION_ASSET_KEY = "0059#gateway_transport"
# Bound to the UTF-8 text checksum used by SQLiteMigrationRunner.  Updated only
# when the immutable 0059 catalog asset is first introduced.
GATEWAY_TRANSPORT_MIGRATION_SHA256 = "bad91de55866c6e9ffffed8a8d249abf077b146bce6b3705a5f95d11b91a8944"

GATEWAY_TRANSPORT_MAX_INBOX_BODY_BYTES = 1_048_576
GATEWAY_TRANSPORT_MAX_NORMALIZED_PAYLOAD_BYTES = 1_048_576
GATEWAY_TRANSPORT_MAX_OUTBOX_BODY_BYTES = 1_048_576
GATEWAY_TRANSPORT_MAX_SAFE_HEADERS_BYTES = 4_096
GATEWAY_TRANSPORT_MAX_SAFE_HEADER_COUNT = 16
GATEWAY_TRANSPORT_MAX_SAFE_HEADER_VALUE_BYTES = 1_024

_ALLOWED_MIGRATION_DISPOSITIONS = frozenset(
    {"APPLIED", "APPLIED_WITH_COMPAT", "LEGACY_BACKFILL"}
)
_SHA256_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_REASON_CODE_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9_.:-]{0,127}$")
_FORBIDDEN_HEADER_PARTS = frozenset(
    {
        "authorization",
        "cookie",
        "credential",
        "key",
        "secret",
        "signature",
        "token",
        "x-edge-sig",
    }
)
_SAFE_HEADER_ALLOWLIST = frozenset(
    {
        "content-length",
        "content-type",
        "user-agent",
        "x-correlation-id",
        "x-edge-source",
        "x-request-id",
    }
)

# DLQ is intentionally unresolved for ordering.  A later item on the same
# authority/order stream cannot pass it, otherwise replay would create 2 -> 1
# delivery.  Only delivered/acked/cancelled records release the sequence.
_INBOX_TERMINAL = frozenset({"DELIVERED"})
_OUTBOX_TERMINAL = frozenset({"ACKED", "CANCELLED"})

_REQUIRED_SCHEMA: dict[str, frozenset[str]] = {
    "gateway_transport_inbox": frozenset(
        {
            "cursor",
            "inbox_id",
            "surface",
            "platform",
            "principal_id",
            "tenant_id",
            "secure_claim_ref",
            "verification_audit_ref",
            "platform_event_id",
            "idempotency_digest",
            "ingress_ack_ref",
            "raw_body_sha256",
            "raw_body_size_bytes",
            "safe_headers_json",
            "safe_headers_sha256",
            "normalized_payload_json",
            "normalized_payload_digest",
            "ordering_key",
            "source_sequence",
            "status",
            "attempt_count",
            "max_attempts",
            "next_attempt_at_ms",
            "claim_token",
            "claim_owner",
            "claim_expires_at_ms",
            "fencing_token",
            "delivery_receipt_ref",
            "last_reason_code",
            "last_replay_operator_ref",
            "replay_count",
            "created_at_ms",
            "updated_at_ms",
            "delivered_at_ms",
            "dlq_at_ms",
            "status_version",
        }
    ),
    "gateway_transport_outbox": frozenset(
        {
            "cursor",
            "outbox_id",
            "origin_inbox_id",
            "tenant_id",
            "principal_id",
            "destination",
            "idempotency_digest",
            "payload_body",
            "payload_sha256",
            "ordering_key",
            "source_sequence",
            "status",
            "attempt_count",
            "max_attempts",
            "next_attempt_at_ms",
            "claim_token",
            "claim_owner",
            "claim_expires_at_ms",
            "fencing_token",
            "ack_ref",
            "ack_digest",
            "last_reason_code",
            "last_replay_operator_ref",
            "replay_count",
            "created_at_ms",
            "updated_at_ms",
            "acked_at_ms",
            "dlq_at_ms",
            "cancelled_at_ms",
            "status_version",
        }
    ),
    "gateway_transport_attempt": frozenset(
        {
            "attempt_id",
            "direction",
            "message_id",
            "replay_no",
            "attempt_no",
            "claim_token",
            "fencing_token",
            "worker_id",
            "started_at_ms",
            "lease_expires_at_ms",
            "dispatch_started_at_ms",
            "completed_at_ms",
            "outcome",
            "reason_code",
            "receipt_ref",
        }
    ),
}

_REQUIRED_INDEXES = frozenset(
    {
        "uq_gateway_transport_inbox_platform_event",
        "uq_gateway_transport_inbox_active_order",
        "idx_gateway_transport_inbox_ready",
        "idx_gateway_transport_inbox_claim_expiry",
        "idx_gateway_transport_inbox_order_sequence",
        "uq_gateway_transport_outbox_active_order",
        "idx_gateway_transport_outbox_ready",
        "idx_gateway_transport_outbox_claim_expiry",
        "idx_gateway_transport_outbox_order_sequence",
        "idx_gateway_transport_attempt_message",
        "idx_gateway_transport_attempt_outcome",
    }
)

_REQUIRED_PRIMARY_KEYS: dict[str, tuple[str, ...]] = {
    "gateway_transport_inbox": ("cursor",),
    "gateway_transport_outbox": ("cursor",),
    "gateway_transport_attempt": ("attempt_id",),
}

_REQUIRED_UNIQUE_KEYS: dict[str, frozenset[tuple[str, ...]]] = {
    "gateway_transport_inbox": frozenset(
        {
            ("inbox_id",),
            ("secure_claim_ref",),
            ("ingress_ack_ref",),
            ("tenant_id", "principal_id", "surface", "idempotency_digest"),
        }
    ),
    "gateway_transport_outbox": frozenset(
        {
            ("outbox_id",),
            ("tenant_id", "principal_id", "destination", "idempotency_digest"),
        }
    ),
    "gateway_transport_attempt": frozenset(
        {
            ("attempt_id",),
            ("claim_token",),
            ("direction", "message_id", "replay_no", "attempt_no"),
        }
    ),
}

# name -> (table, unique, partial, ordered columns, required WHERE predicate)
_REQUIRED_INDEX_DEFINITIONS: dict[
    str, tuple[str, bool, bool, tuple[str, ...], str | None]
] = {
    "uq_gateway_transport_inbox_platform_event": (
        "gateway_transport_inbox",
        True,
        True,
        ("tenant_id", "principal_id", "platform", "platform_event_id"),
        "platform_event_id IS NOT NULL",
    ),
    "uq_gateway_transport_inbox_active_order": (
        "gateway_transport_inbox",
        True,
        True,
        ("tenant_id", "principal_id", "ordering_key"),
        "status = 'CLAIMED'",
    ),
    "idx_gateway_transport_inbox_ready": (
        "gateway_transport_inbox",
        False,
        False,
        (
            "status",
            "next_attempt_at_ms",
            "tenant_id",
            "principal_id",
            "ordering_key",
            "cursor",
        ),
        None,
    ),
    "idx_gateway_transport_inbox_claim_expiry": (
        "gateway_transport_inbox",
        False,
        False,
        ("status", "claim_expires_at_ms"),
        None,
    ),
    "idx_gateway_transport_inbox_order_sequence": (
        "gateway_transport_inbox",
        False,
        False,
        (
            "tenant_id",
            "principal_id",
            "ordering_key",
            "source_sequence",
            "cursor",
            "status",
        ),
        None,
    ),
    "uq_gateway_transport_outbox_active_order": (
        "gateway_transport_outbox",
        True,
        True,
        ("tenant_id", "principal_id", "ordering_key"),
        "status = 'CLAIMED'",
    ),
    "idx_gateway_transport_outbox_ready": (
        "gateway_transport_outbox",
        False,
        False,
        (
            "status",
            "next_attempt_at_ms",
            "tenant_id",
            "principal_id",
            "ordering_key",
            "cursor",
        ),
        None,
    ),
    "idx_gateway_transport_outbox_claim_expiry": (
        "gateway_transport_outbox",
        False,
        False,
        ("status", "claim_expires_at_ms"),
        None,
    ),
    "idx_gateway_transport_outbox_order_sequence": (
        "gateway_transport_outbox",
        False,
        False,
        (
            "tenant_id",
            "principal_id",
            "ordering_key",
            "source_sequence",
            "cursor",
            "status",
        ),
        None,
    ),
    "idx_gateway_transport_attempt_message": (
        "gateway_transport_attempt",
        False,
        False,
        ("direction", "message_id", "replay_no", "attempt_no"),
        None,
    ),
    "idx_gateway_transport_attempt_outcome": (
        "gateway_transport_attempt",
        False,
        False,
        ("outcome", "lease_expires_at_ms"),
        None,
    ),
}

_REQUIRED_FOREIGN_KEYS = frozenset(
    {
        (
            "gateway_transport_outbox",
            "origin_inbox_id",
            "gateway_transport_inbox",
            "inbox_id",
            "RESTRICT",
        )
    }
)

# SHA-256 of the whitespace-normalized SQL stored in sqlite_master for each
# catalog-owned table.  This binds every CHECK/UNIQUE/FK clause, while the
# explicit PRAGMA checks below provide stable diagnostics for key invariants.
_REQUIRED_TABLE_SQL_SHA256: dict[str, str] = {
    "gateway_transport_inbox": "0af468b3f28ea0e44918db0e0e3019a601153e0f8bf9e684d89f76e44377adbb",
    "gateway_transport_outbox": "ebcedaccb2af8e8f358993c2ec9fc0c9e31f6b476a98c6fd374c42067df50a34",
    "gateway_transport_attempt": "e56a431958a1ef2fc8697c1788b44fbfed032dc4864d5c65286718887af8b779",
}


class GatewayTransportStoreError(RuntimeError):
    """Fail-closed transport error containing only a stable reason code."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = str(reason_code)
        super().__init__(self.reason_code)


class InboxAcceptanceStatus(str, Enum):
    NEW = "NEW"
    DUPLICATE_SAME = "DUPLICATE_SAME"
    CONFLICT = "CONFLICT"


@dataclass(frozen=True)
class InboxAcceptance:
    status: InboxAcceptanceStatus
    inbox_id: str | None
    ack_ref: str | None
    reason_code: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "inbox_id": self.inbox_id,
            "ack_ref": self.ack_ref,
            "reason_code": self.reason_code,
        }


def _now_ms() -> int:
    return int(time.time() * 1000)


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise GatewayTransportStoreError("GATEWAY_TRANSPORT_PAYLOAD_INVALID") from exc


def _json_object(value: Mapping[str, Any] | None) -> tuple[str, dict[str, Any]]:
    normalized = dict(value or {})
    return _canonical_json(normalized), normalized


def _digest_bytes(value: bytes) -> str:
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


def _digest_text(value: str) -> str:
    return _digest_bytes(value.encode("utf-8"))


def _idempotency_digest(value: str) -> str:
    normalized = _required_text(value, "GATEWAY_TRANSPORT_IDEMPOTENCY_REQUIRED", 2048)
    if _SHA256_PATTERN.fullmatch(normalized):
        return normalized
    return _digest_text(normalized)


def _required_text(value: Any, reason_code: str, max_length: int = 512) -> str:
    normalized = str(value or "").strip()
    if not normalized or len(normalized) > max_length:
        raise GatewayTransportStoreError(reason_code)
    return normalized


def _optional_text(value: Any, reason_code: str, max_length: int = 1024) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    if not normalized:
        return None
    if len(normalized) > max_length:
        raise GatewayTransportStoreError(reason_code)
    return normalized


def _reason_code(value: Any) -> str:
    normalized = str(value or "").strip().upper()
    if not _REASON_CODE_PATTERN.fullmatch(normalized):
        raise GatewayTransportStoreError("GATEWAY_TRANSPORT_REASON_CODE_INVALID")
    return normalized


def _safe_headers(value: Mapping[str, Any] | None) -> str:
    safe: dict[str, str] = {}
    supplied = dict(value or {})
    if len(supplied) > GATEWAY_TRANSPORT_MAX_SAFE_HEADER_COUNT:
        raise GatewayTransportStoreError("GATEWAY_TRANSPORT_SAFE_HEADERS_TOO_LARGE")
    for raw_name, raw_value in supplied.items():
        name = str(raw_name or "").strip().lower()
        if not name or len(name) > 128:
            raise GatewayTransportStoreError("GATEWAY_TRANSPORT_SAFE_HEADERS_INVALID")
        if any(part in name for part in _FORBIDDEN_HEADER_PARTS):
            raise GatewayTransportStoreError("GATEWAY_TRANSPORT_SECRET_HEADER_FORBIDDEN")
        if name not in _SAFE_HEADER_ALLOWLIST:
            raise GatewayTransportStoreError("GATEWAY_TRANSPORT_SAFE_HEADER_NOT_ALLOWED")
        rendered = str(raw_value or "")
        if len(rendered.encode("utf-8")) > GATEWAY_TRANSPORT_MAX_SAFE_HEADER_VALUE_BYTES:
            raise GatewayTransportStoreError("GATEWAY_TRANSPORT_SAFE_HEADERS_TOO_LARGE")
        safe[name] = rendered
    serialized = _canonical_json(safe)
    if len(serialized.encode("utf-8")) > GATEWAY_TRANSPORT_MAX_SAFE_HEADERS_BYTES:
        raise GatewayTransportStoreError("GATEWAY_TRANSPORT_SAFE_HEADERS_TOO_LARGE")
    return serialized


def _payload_bytes(value: bytes | bytearray | memoryview | str | Mapping[str, Any]) -> bytes:
    if isinstance(value, bytes):
        result = value
    elif isinstance(value, (bytearray, memoryview)):
        result = bytes(value)
    elif isinstance(value, str):
        result = value.encode("utf-8")
    elif isinstance(value, Mapping):
        result = _canonical_json(dict(value)).encode("utf-8")
    else:
        raise GatewayTransportStoreError("GATEWAY_TRANSPORT_PAYLOAD_INVALID")
    if not result:
        raise GatewayTransportStoreError("GATEWAY_TRANSPORT_PAYLOAD_EMPTY")
    if len(result) > GATEWAY_TRANSPORT_MAX_OUTBOX_BODY_BYTES:
        raise GatewayTransportStoreError("GATEWAY_TRANSPORT_PAYLOAD_TOO_LARGE")
    return result


class SQLiteGatewayTransportStore:
    """SQLite durable transport store with lease fencing and replay-safe DLQs."""

    def __init__(
        self,
        db_path: str | Path,
        *,
        clock: Callable[[], int] = _now_ms,
        timeout_ms: int = 5_000,
        verify_schema: bool = True,
    ) -> None:
        raw_path = str(db_path or "").strip()
        if not raw_path or raw_path == ":memory:" or raw_path.lower().startswith("file:"):
            raise GatewayTransportStoreError("GATEWAY_TRANSPORT_DURABLE_PATH_REQUIRED")
        self.db_path = Path(raw_path)
        self._clock = clock
        self._timeout_ms = max(1, int(timeout_ms))
        if verify_schema:
            self.assert_schema_ready()

    def _now(self) -> int:
        value = int(self._clock())
        if value < 0:
            raise GatewayTransportStoreError("GATEWAY_TRANSPORT_CLOCK_INVALID")
        return value

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

    def assert_schema_ready(self) -> None:
        if not self.db_path.is_file():
            raise GatewayTransportStoreError("GATEWAY_TRANSPORT_SCHEMA_UNAVAILABLE")
        conn = self._connect()
        try:
            try:
                asset = conn.execute(
                    """
                    SELECT checksum, disposition
                    FROM schema_migration_assets
                    WHERE asset_key = ?
                    """,
                    (GATEWAY_TRANSPORT_MIGRATION_ASSET_KEY,),
                ).fetchone()
            except sqlite3.Error as exc:
                raise GatewayTransportStoreError(
                    "GATEWAY_TRANSPORT_MIGRATION_DRIFT"
                ) from exc
            if (
                asset is None
                or str(asset["checksum"]) != GATEWAY_TRANSPORT_MIGRATION_SHA256
                or str(asset["disposition"]) not in _ALLOWED_MIGRATION_DISPOSITIONS
            ):
                raise GatewayTransportStoreError("GATEWAY_TRANSPORT_MIGRATION_DRIFT")

            for table, required_columns in _REQUIRED_SCHEMA.items():
                rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
                columns = {str(row["name"]) for row in rows}
                if not required_columns.issubset(columns):
                    raise GatewayTransportStoreError("GATEWAY_TRANSPORT_SCHEMA_DRIFT")

                primary_key = tuple(
                    str(row["name"])
                    for row in sorted(
                        (row for row in rows if int(row["pk"]) > 0),
                        key=lambda item: int(item["pk"]),
                    )
                )
                if primary_key != _REQUIRED_PRIMARY_KEYS[table]:
                    raise GatewayTransportStoreError("GATEWAY_TRANSPORT_SCHEMA_DRIFT")

                table_sql = self._table_sql(conn, table)
                table_sql_digest = hashlib.sha256(table_sql.encode("utf-8")).hexdigest()
                if table_sql_digest != _REQUIRED_TABLE_SQL_SHA256[table]:
                    raise GatewayTransportStoreError("GATEWAY_TRANSPORT_SCHEMA_DRIFT")

                unique_keys: set[tuple[str, ...]] = set()
                for index in conn.execute(f"PRAGMA index_list({table})").fetchall():
                    if int(index["unique"]) != 1 or int(index["partial"]) != 0:
                        continue
                    index_name = str(index["name"])
                    index_columns = tuple(
                        str(column["name"])
                        for column in sorted(
                            conn.execute(f"PRAGMA index_info({index_name})").fetchall(),
                            key=lambda item: int(item["seqno"]),
                        )
                    )
                    unique_keys.add(index_columns)
                if not _REQUIRED_UNIQUE_KEYS[table].issubset(unique_keys):
                    raise GatewayTransportStoreError("GATEWAY_TRANSPORT_SCHEMA_DRIFT")

            index_rows = {
                str(row["name"]): row
                for table in _REQUIRED_SCHEMA
                for row in conn.execute(f"PRAGMA index_list({table})").fetchall()
            }
            indexes = set(index_rows)
            if not _REQUIRED_INDEXES.issubset(indexes):
                raise GatewayTransportStoreError("GATEWAY_TRANSPORT_SCHEMA_DRIFT")

            for index_name, definition in _REQUIRED_INDEX_DEFINITIONS.items():
                expected_table, expected_unique, expected_partial, expected_columns, predicate = (
                    definition
                )
                index = index_rows.get(index_name)
                if (
                    index is None
                    or bool(int(index["unique"])) is not expected_unique
                    or bool(int(index["partial"])) is not expected_partial
                ):
                    raise GatewayTransportStoreError("GATEWAY_TRANSPORT_SCHEMA_DRIFT")
                catalog = conn.execute(
                    "SELECT tbl_name, sql FROM sqlite_master WHERE type = 'index' AND name = ?",
                    (index_name,),
                ).fetchone()
                if catalog is None or str(catalog["tbl_name"]) != expected_table:
                    raise GatewayTransportStoreError("GATEWAY_TRANSPORT_SCHEMA_DRIFT")
                columns = tuple(
                    str(row["name"])
                    for row in sorted(
                        conn.execute(f"PRAGMA index_info({index_name})").fetchall(),
                        key=lambda item: int(item["seqno"]),
                    )
                )
                if columns != expected_columns:
                    raise GatewayTransportStoreError("GATEWAY_TRANSPORT_SCHEMA_DRIFT")
                index_sql = "".join(str(catalog["sql"] or "").upper().split())
                if predicate is None:
                    if "WHERE" in index_sql:
                        raise GatewayTransportStoreError("GATEWAY_TRANSPORT_SCHEMA_DRIFT")
                elif f"WHERE{''.join(predicate.upper().split())}" not in index_sql:
                    raise GatewayTransportStoreError("GATEWAY_TRANSPORT_SCHEMA_DRIFT")

            foreign_keys = {
                (
                    table,
                    str(row["from"]),
                    str(row["table"]),
                    str(row["to"]),
                    str(row["on_delete"]).upper(),
                )
                for table in _REQUIRED_SCHEMA
                for row in conn.execute(f"PRAGMA foreign_key_list({table})").fetchall()
            }
            if not _REQUIRED_FOREIGN_KEYS.issubset(foreign_keys):
                raise GatewayTransportStoreError("GATEWAY_TRANSPORT_SCHEMA_DRIFT")

            inbox_sql = self._table_sql(conn, "gateway_transport_inbox")
            outbox_sql = self._table_sql(conn, "gateway_transport_outbox")
            attempt_sql = self._table_sql(conn, "gateway_transport_attempt")
            if not all(
                status in inbox_sql
                for status in ("READY", "CLAIMED", "RETRY_WAIT", "DELIVERED", "DLQ")
            ):
                raise GatewayTransportStoreError("GATEWAY_TRANSPORT_SCHEMA_DRIFT")
            if not all(
                status in outbox_sql
                for status in (
                    "READY",
                    "CLAIMED",
                    "RETRY_WAIT",
                    "ACKED",
                    "DLQ",
                    "CANCELLED",
                )
            ):
                raise GatewayTransportStoreError("GATEWAY_TRANSPORT_SCHEMA_DRIFT")
            if not all(
                outcome in attempt_sql
                for outcome in (
                    "CLAIMED",
                    "DISPATCHING",
                    "DELIVERED",
                    "ACKED",
                    "LEASE_EXPIRED",
                )
            ):
                raise GatewayTransportStoreError("GATEWAY_TRANSPORT_SCHEMA_DRIFT")
        finally:
            conn.close()

    @staticmethod
    def _table_sql(conn: sqlite3.Connection, table: str) -> str:
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table,),
        ).fetchone()
        return " ".join(str(row["sql"] if row is not None else "").upper().split())

    # -- durable ingress ---------------------------------------------------------

    def accept_inbox(
        self,
        *,
        inbox_id: str,
        surface: str,
        platform: str,
        principal_id: str,
        tenant_id: str,
        idempotency_key: str,
        raw_body: bytes | bytearray | memoryview,
        safe_headers: Mapping[str, Any] | None = None,
        normalized_payload: Mapping[str, Any] | None = None,
        secure_claim_ref: str | None = None,
        verification_audit_ref: str | None = None,
        platform_event_id: str | None = None,
        ordering_key: str | None = None,
        source_sequence: int | None = None,
        max_attempts: int = 8,
    ) -> InboxAcceptance:
        normalized_inbox_id = _required_text(
            inbox_id, "GATEWAY_TRANSPORT_INBOX_ID_REQUIRED"
        )
        normalized_surface = _required_text(
            surface, "GATEWAY_TRANSPORT_SURFACE_REQUIRED"
        )
        normalized_platform = _required_text(
            platform, "GATEWAY_TRANSPORT_PLATFORM_REQUIRED"
        )
        normalized_principal = _required_text(
            principal_id, "GATEWAY_TRANSPORT_PRINCIPAL_REQUIRED"
        )
        normalized_tenant = _required_text(
            tenant_id, "GATEWAY_TRANSPORT_TENANT_REQUIRED"
        )
        normalized_claim = _optional_text(
            secure_claim_ref, "GATEWAY_TRANSPORT_CLAIM_REF_INVALID"
        )
        normalized_audit = _optional_text(
            verification_audit_ref, "GATEWAY_TRANSPORT_AUDIT_REF_INVALID"
        )
        normalized_event = _optional_text(
            platform_event_id, "GATEWAY_TRANSPORT_PLATFORM_EVENT_INVALID"
        )
        normalized_order = _required_text(
            ordering_key or f"{normalized_platform}:{normalized_principal}",
            "GATEWAY_TRANSPORT_ORDERING_KEY_REQUIRED",
        )
        sequence = None if source_sequence is None else int(source_sequence)
        if sequence is not None and sequence < 0:
            raise GatewayTransportStoreError("GATEWAY_TRANSPORT_SEQUENCE_INVALID")
        attempts = int(max_attempts)
        if attempts < 1:
            raise GatewayTransportStoreError("GATEWAY_TRANSPORT_MAX_ATTEMPTS_INVALID")
        body = bytes(raw_body)
        if not body:
            raise GatewayTransportStoreError("GATEWAY_TRANSPORT_PAYLOAD_EMPTY")
        if len(body) > GATEWAY_TRANSPORT_MAX_INBOX_BODY_BYTES:
            raise GatewayTransportStoreError("GATEWAY_TRANSPORT_PAYLOAD_TOO_LARGE")
        body_digest = _digest_bytes(body)
        body_size = len(body)
        headers_json = _safe_headers(safe_headers)
        headers_digest = _digest_text(headers_json)
        payload_json, _payload = _json_object(normalized_payload)
        if (
            len(payload_json.encode("utf-8"))
            > GATEWAY_TRANSPORT_MAX_NORMALIZED_PAYLOAD_BYTES
        ):
            raise GatewayTransportStoreError("GATEWAY_TRANSPORT_PAYLOAD_TOO_LARGE")
        payload_digest = _digest_text(payload_json)
        idem_digest = _idempotency_digest(idempotency_key)
        ack_ref = f"gateway-inbox-ack://{normalized_inbox_id}"
        now = self._now()

        with self._transaction() as conn:
            existing = self._find_existing_inbox(
                conn,
                inbox_id=normalized_inbox_id,
                principal_id=normalized_principal,
                tenant_id=normalized_tenant,
                surface=normalized_surface,
                idempotency_digest=idem_digest,
                platform=normalized_platform,
                platform_event_id=normalized_event,
                secure_claim_ref=normalized_claim,
            )
            if existing is not None:
                same = (
                    str(existing["raw_body_sha256"]) == body_digest
                    and str(existing["normalized_payload_digest"]) == payload_digest
                    and str(existing["principal_id"]) == normalized_principal
                    and str(existing["surface"]) == normalized_surface
                    and str(existing["platform"]) == normalized_platform
                    and str(existing["tenant_id"]) == normalized_tenant
                    and str(existing["idempotency_digest"]) == idem_digest
                    and str(existing["ordering_key"]) == normalized_order
                    and (
                        int(existing["source_sequence"])
                        if existing["source_sequence"] is not None
                        else None
                    )
                    == sequence
                    and (
                        normalized_event is None
                        or str(existing["platform_event_id"] or "") == normalized_event
                    )
                    and (
                        normalized_claim is None
                        or str(existing["secure_claim_ref"] or "") == normalized_claim
                    )
                )
                return InboxAcceptance(
                    InboxAcceptanceStatus.DUPLICATE_SAME if same else InboxAcceptanceStatus.CONFLICT,
                    str(existing["inbox_id"]),
                    str(existing["ingress_ack_ref"]),
                    None if same else "GATEWAY_TRANSPORT_IDEMPOTENCY_CONFLICT",
                )
            try:
                conn.execute(
                    """
                    INSERT INTO gateway_transport_inbox(
                        inbox_id, surface, platform, principal_id, tenant_id,
                        secure_claim_ref, verification_audit_ref, platform_event_id,
                        idempotency_digest, ingress_ack_ref, raw_body_sha256,
                        raw_body_size_bytes, safe_headers_json, safe_headers_sha256,
                        normalized_payload_json, normalized_payload_digest,
                        ordering_key, source_sequence, status, attempt_count,
                        max_attempts, next_attempt_at_ms, claim_token,
                        claim_owner, claim_expires_at_ms, fencing_token,
                        delivery_receipt_ref, last_reason_code,
                        last_replay_operator_ref, replay_count, created_at_ms,
                        updated_at_ms, delivered_at_ms, dlq_at_ms, status_version
                    ) VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        'READY', 0, ?, ?, NULL, NULL, NULL, 0, NULL, NULL, NULL,
                        0, ?, ?, NULL, NULL, 0
                    )
                    """,
                    (
                        normalized_inbox_id,
                        normalized_surface,
                        normalized_platform,
                        normalized_principal,
                        normalized_tenant,
                        normalized_claim,
                        normalized_audit,
                        normalized_event,
                        idem_digest,
                        ack_ref,
                        body_digest,
                        body_size,
                        headers_json,
                        headers_digest,
                        payload_json,
                        payload_digest,
                        normalized_order,
                        sequence,
                        attempts,
                        now,
                        now,
                        now,
                    ),
                )
            except sqlite3.IntegrityError:
                return InboxAcceptance(
                    InboxAcceptanceStatus.CONFLICT,
                    None,
                    None,
                    "GATEWAY_TRANSPORT_IDEMPOTENCY_CONFLICT",
                )
        return InboxAcceptance(InboxAcceptanceStatus.NEW, normalized_inbox_id, ack_ref)

    @staticmethod
    def _find_existing_inbox(
        conn: sqlite3.Connection,
        *,
        inbox_id: str,
        principal_id: str,
        tenant_id: str,
        surface: str,
        idempotency_digest: str,
        platform: str,
        platform_event_id: str | None,
        secure_claim_ref: str | None,
    ) -> sqlite3.Row | None:
        rows: list[sqlite3.Row] = []
        for query, params in (
            (
                "SELECT * FROM gateway_transport_inbox "
                "WHERE tenant_id = ? AND principal_id = ? AND surface = ? "
                "AND idempotency_digest = ?",
                (tenant_id, principal_id, surface, idempotency_digest),
            ),
            (
                "SELECT * FROM gateway_transport_inbox WHERE inbox_id = ?",
                (inbox_id,),
            ),
        ):
            row = conn.execute(query, params).fetchone()
            if row is not None:
                rows.append(row)
        if platform_event_id is not None:
            row = conn.execute(
                "SELECT * FROM gateway_transport_inbox "
                "WHERE tenant_id = ? AND principal_id = ? AND platform = ? "
                "AND platform_event_id = ?",
                (tenant_id, principal_id, platform, platform_event_id),
            ).fetchone()
            if row is not None:
                rows.append(row)
        if secure_claim_ref is not None:
            row = conn.execute(
                "SELECT * FROM gateway_transport_inbox WHERE secure_claim_ref = ?",
                (secure_claim_ref,),
            ).fetchone()
            if row is not None:
                rows.append(row)
        if not rows:
            return None
        first_id = str(rows[0]["inbox_id"])
        if any(str(row["inbox_id"]) != first_id for row in rows[1:]):
            raise GatewayTransportStoreError("GATEWAY_TRANSPORT_IDENTITY_CONFLICT")
        return rows[0]

    def claim_inbox(self, *, worker_id: str, lease_ms: int = 30_000) -> dict[str, Any] | None:
        return self._claim(direction="INBOX", worker_id=worker_id, lease_ms=lease_ms)

    def mark_inbox_dispatch_started(
        self,
        *,
        inbox_id: str,
        claim_token: str,
        fencing_token: int,
    ) -> dict[str, Any]:
        return self.mark_dispatch_started(
            direction="INBOX",
            message_id=inbox_id,
            claim_token=claim_token,
            fencing_token=fencing_token,
        )

    def complete_inbox(
        self,
        *,
        inbox_id: str,
        claim_token: str,
        fencing_token: int,
        receipt_ref: str,
        outbox_messages: Sequence[Mapping[str, Any]] = (),
    ) -> dict[str, Any]:
        """Complete delivery and atomically enqueue children under inbox authority.

        Child ``tenant_id`` / ``principal_id`` values are optional assertions,
        never authority inputs; conflicting assertions fail the transaction.
        """

        message_id = _required_text(inbox_id, "GATEWAY_TRANSPORT_INBOX_ID_REQUIRED")
        token = _required_text(claim_token, "GATEWAY_TRANSPORT_CLAIM_TOKEN_REQUIRED")
        receipt = _required_text(receipt_ref, "GATEWAY_TRANSPORT_RECEIPT_REQUIRED", 2048)
        fence = int(fencing_token)
        now = self._now()
        payload_error = False
        outbox_results: list[dict[str, Any]] = []
        with self._transaction() as conn:
            self._recover_expired_claims(conn, direction="INBOX", now=now)
            row = self._require_message(conn, "INBOX", message_id)
            if str(row["status"]) == "DELIVERED":
                if str(row["delivery_receipt_ref"]) != receipt:
                    raise GatewayTransportStoreError("GATEWAY_TRANSPORT_RECEIPT_CONFLICT")
                result = self._inbox_record(row)
                result["outbox"] = []
                return result
            self._require_fence(row, token=token, fence=fence, now=now)
            if not self._inbox_payload_valid(row):
                self._move_claim_to_dlq(
                    conn,
                    direction="INBOX",
                    row=row,
                    now=now,
                    reason_code="GATEWAY_TRANSPORT_PAYLOAD_DIGEST_MISMATCH",
                )
                payload_error = True
            else:
                derived_tenant = str(row["tenant_id"])
                derived_principal = str(row["principal_id"])
                for message in outbox_messages:
                    requested_tenant = message.get("tenant_id")
                    requested_principal = message.get("principal_id")
                    if requested_tenant is not None and _required_text(
                        requested_tenant,
                        "GATEWAY_TRANSPORT_TENANT_REQUIRED",
                    ) != derived_tenant:
                        raise GatewayTransportStoreError(
                            "GATEWAY_TRANSPORT_AUTHORITY_CONFLICT"
                        )
                    if requested_principal is not None and _required_text(
                        requested_principal,
                        "GATEWAY_TRANSPORT_PRINCIPAL_REQUIRED",
                    ) != derived_principal:
                        raise GatewayTransportStoreError(
                            "GATEWAY_TRANSPORT_AUTHORITY_CONFLICT"
                        )
                    outbox_results.append(
                        self._enqueue_outbox_conn(
                            conn,
                            now=now,
                            tenant_id=derived_tenant,
                            principal_id=derived_principal,
                            origin_inbox_id=message.get("origin_inbox_id") or message_id,
                            outbox_id=message.get("outbox_id"),
                            destination=message.get("destination"),
                            idempotency_key=message.get("idempotency_key"),
                            payload=message.get("payload"),
                            ordering_key=message.get("ordering_key"),
                            source_sequence=message.get("source_sequence"),
                            max_attempts=message.get("max_attempts", 8),
                        )
                    )
                conn.execute(
                    """
                    UPDATE gateway_transport_inbox
                    SET status = 'DELIVERED', delivery_receipt_ref = ?,
                        delivered_at_ms = ?, updated_at_ms = ?,
                        claim_token = NULL, claim_owner = NULL,
                        claim_expires_at_ms = NULL, last_reason_code = NULL,
                        status_version = status_version + 1
                    WHERE inbox_id = ? AND status = 'CLAIMED'
                      AND claim_token = ? AND fencing_token = ?
                    """,
                    (receipt, now, now, message_id, token, fence),
                )
                self._finish_attempt(
                    conn,
                    claim_token=token,
                    outcome="DELIVERED",
                    now=now,
                    reason_code=None,
                    receipt_ref=receipt,
                )
                row = self._require_message(conn, "INBOX", message_id)
                result = self._inbox_record(row)
                result["outbox"] = outbox_results
        if payload_error:
            raise GatewayTransportStoreError(
                "GATEWAY_TRANSPORT_PAYLOAD_DIGEST_MISMATCH"
            )
        return result

    def retry_inbox(
        self,
        *,
        inbox_id: str,
        claim_token: str,
        fencing_token: int,
        reason_code: str,
        base_delay_ms: int = 1_000,
        max_delay_ms: int = 60_000,
    ) -> dict[str, Any]:
        return self._retry(
            direction="INBOX",
            message_id=inbox_id,
            claim_token=claim_token,
            fencing_token=fencing_token,
            reason_code=reason_code,
            base_delay_ms=base_delay_ms,
            max_delay_ms=max_delay_ms,
        )

    def replay_inbox(self, *, inbox_id: str, operator_ref: str) -> dict[str, Any]:
        return self._replay(direction="INBOX", message_id=inbox_id, operator_ref=operator_ref)

    # -- durable egress ----------------------------------------------------------

    def enqueue_outbox(
        self,
        *,
        outbox_id: str,
        destination: str,
        tenant_id: str,
        principal_id: str,
        idempotency_key: str,
        payload: bytes | bytearray | memoryview | str | Mapping[str, Any],
        origin_inbox_id: str | None = None,
        ordering_key: str | None = None,
        source_sequence: int | None = None,
        max_attempts: int = 8,
    ) -> dict[str, Any]:
        """Standalone enqueue requiring an explicit tenant/principal authority."""

        now = self._now()
        with self._transaction() as conn:
            return self._enqueue_outbox_conn(
                conn,
                now=now,
                outbox_id=outbox_id,
                destination=destination,
                tenant_id=tenant_id,
                principal_id=principal_id,
                idempotency_key=idempotency_key,
                payload=payload,
                origin_inbox_id=origin_inbox_id,
                ordering_key=ordering_key,
                source_sequence=source_sequence,
                max_attempts=max_attempts,
            )

    def _enqueue_outbox_conn(
        self,
        conn: sqlite3.Connection,
        *,
        now: int,
        outbox_id: Any,
        destination: Any,
        tenant_id: Any,
        principal_id: Any,
        idempotency_key: Any,
        payload: Any,
        origin_inbox_id: Any = None,
        ordering_key: Any = None,
        source_sequence: Any = None,
        max_attempts: Any = 8,
    ) -> dict[str, Any]:
        message_id = _required_text(outbox_id, "GATEWAY_TRANSPORT_OUTBOX_ID_REQUIRED")
        target = _required_text(destination, "GATEWAY_TRANSPORT_DESTINATION_REQUIRED")
        tenant = _required_text(tenant_id, "GATEWAY_TRANSPORT_TENANT_REQUIRED")
        principal = _required_text(
            principal_id, "GATEWAY_TRANSPORT_PRINCIPAL_REQUIRED"
        )
        idem_digest = _idempotency_digest(str(idempotency_key or ""))
        body = _payload_bytes(payload)
        body_digest = _digest_bytes(body)
        origin = _optional_text(origin_inbox_id, "GATEWAY_TRANSPORT_ORIGIN_INVALID")
        order = _required_text(
            ordering_key or target, "GATEWAY_TRANSPORT_ORDERING_KEY_REQUIRED"
        )
        sequence = None if source_sequence is None else int(source_sequence)
        if sequence is not None and sequence < 0:
            raise GatewayTransportStoreError("GATEWAY_TRANSPORT_SEQUENCE_INVALID")
        attempts = int(max_attempts)
        if attempts < 1:
            raise GatewayTransportStoreError("GATEWAY_TRANSPORT_MAX_ATTEMPTS_INVALID")

        rows = conn.execute(
            """
            SELECT * FROM gateway_transport_outbox
            WHERE (
                tenant_id = ? AND principal_id = ?
                AND destination = ? AND idempotency_digest = ?
            ) OR outbox_id = ?
            ORDER BY cursor
            """,
            (tenant, principal, target, idem_digest, message_id),
        ).fetchall()
        if rows:
            by_id = [row for row in rows if str(row["outbox_id"]) == message_id]
            by_idempotency = [
                row
                for row in rows
                if str(row["tenant_id"]) == tenant
                and str(row["principal_id"]) == principal
                and str(row["destination"]) == target
                and str(row["idempotency_digest"]) == idem_digest
            ]
            if (
                by_id
                and by_idempotency
                and str(by_id[0]["outbox_id"])
                != str(by_idempotency[0]["outbox_id"])
            ):
                raise GatewayTransportStoreError("GATEWAY_TRANSPORT_IDENTITY_CONFLICT")
            existing = by_id[0] if by_id else by_idempotency[0]
            same_immutable = (
                str(existing["tenant_id"]) == tenant
                and str(existing["principal_id"]) == principal
                and str(existing["destination"]) == target
                and str(existing["idempotency_digest"]) == idem_digest
                and str(existing["payload_sha256"]) == body_digest
                and (
                    str(existing["origin_inbox_id"])
                    if existing["origin_inbox_id"] is not None
                    else None
                )
                == origin
                and str(existing["ordering_key"]) == order
                and (
                    int(existing["source_sequence"])
                    if existing["source_sequence"] is not None
                    else None
                )
                == sequence
                and int(existing["max_attempts"]) == attempts
            )
            if not same_immutable:
                raise GatewayTransportStoreError("GATEWAY_TRANSPORT_IDEMPOTENCY_CONFLICT")
            result = self._outbox_record(existing)
            result["acceptance_status"] = "DUPLICATE_SAME"
            return result
        if origin is not None:
            origin_row = conn.execute(
                """
                SELECT tenant_id, principal_id
                FROM gateway_transport_inbox
                WHERE inbox_id = ?
                """,
                (origin,),
            ).fetchone()
            if origin_row is None:
                raise GatewayTransportStoreError(
                    "GATEWAY_TRANSPORT_MESSAGE_NOT_FOUND"
                )
            if (
                str(origin_row["tenant_id"]) != tenant
                or str(origin_row["principal_id"]) != principal
            ):
                raise GatewayTransportStoreError(
                    "GATEWAY_TRANSPORT_AUTHORITY_CONFLICT"
                )
        try:
            conn.execute(
                """
                INSERT INTO gateway_transport_outbox(
                    outbox_id, origin_inbox_id, tenant_id, principal_id,
                    destination, idempotency_digest,
                    payload_body, payload_sha256, ordering_key, source_sequence,
                    status, attempt_count, max_attempts, next_attempt_at_ms,
                    claim_token, claim_owner, claim_expires_at_ms, fencing_token,
                    ack_ref, ack_digest, last_reason_code,
                    last_replay_operator_ref, replay_count, created_at_ms,
                    updated_at_ms, acked_at_ms, dlq_at_ms, cancelled_at_ms,
                    status_version
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'READY', 0, ?, ?, NULL, NULL,
                    NULL, 0, NULL, NULL, NULL, NULL, 0, ?, ?, NULL, NULL, NULL, 0
                )
                """,
                (
                    message_id,
                    origin,
                    tenant,
                    principal,
                    target,
                    idem_digest,
                    sqlite3.Binary(body),
                    body_digest,
                    order,
                    sequence,
                    attempts,
                    now,
                    now,
                    now,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise GatewayTransportStoreError(
                "GATEWAY_TRANSPORT_IDEMPOTENCY_CONFLICT"
            ) from exc
        row = self._require_message(conn, "OUTBOX", message_id)
        result = self._outbox_record(row)
        result["acceptance_status"] = "NEW"
        return result

    def claim_outbox(self, *, worker_id: str, lease_ms: int = 30_000) -> dict[str, Any] | None:
        return self._claim(direction="OUTBOX", worker_id=worker_id, lease_ms=lease_ms)

    def mark_outbox_dispatch_started(
        self,
        *,
        outbox_id: str,
        claim_token: str,
        fencing_token: int,
    ) -> dict[str, Any]:
        return self.mark_dispatch_started(
            direction="OUTBOX",
            message_id=outbox_id,
            claim_token=claim_token,
            fencing_token=fencing_token,
        )

    def ack_outbox(
        self,
        *,
        outbox_id: str,
        claim_token: str,
        fencing_token: int,
        ack_ref: str,
        ack_digest: str | None = None,
    ) -> dict[str, Any]:
        message_id = _required_text(outbox_id, "GATEWAY_TRANSPORT_OUTBOX_ID_REQUIRED")
        token = _required_text(claim_token, "GATEWAY_TRANSPORT_CLAIM_TOKEN_REQUIRED")
        fence = int(fencing_token)
        receipt = _required_text(ack_ref, "GATEWAY_TRANSPORT_ACK_REQUIRED", 2048)
        digest = _optional_text(ack_digest, "GATEWAY_TRANSPORT_ACK_DIGEST_INVALID")
        if digest is not None and not _SHA256_PATTERN.fullmatch(digest):
            raise GatewayTransportStoreError("GATEWAY_TRANSPORT_ACK_DIGEST_INVALID")
        now = self._now()
        payload_error = False
        with self._transaction() as conn:
            self._recover_expired_claims(conn, direction="OUTBOX", now=now)
            row = self._require_message(conn, "OUTBOX", message_id)
            if str(row["status"]) == "ACKED":
                if str(row["ack_ref"]) != receipt or (digest is not None and row["ack_digest"] not in (None, digest)):
                    raise GatewayTransportStoreError("GATEWAY_TRANSPORT_ACK_CONFLICT")
                return self._outbox_record(row)
            self._require_fence(row, token=token, fence=fence, now=now)
            if not self._outbox_payload_valid(row):
                self._move_claim_to_dlq(
                    conn,
                    direction="OUTBOX",
                    row=row,
                    now=now,
                    reason_code="GATEWAY_TRANSPORT_PAYLOAD_DIGEST_MISMATCH",
                )
                payload_error = True
            else:
                conn.execute(
                    """
                    UPDATE gateway_transport_outbox
                    SET status = 'ACKED', ack_ref = ?, ack_digest = ?,
                        acked_at_ms = ?, updated_at_ms = ?, claim_token = NULL,
                        claim_owner = NULL, claim_expires_at_ms = NULL,
                        last_reason_code = NULL, status_version = status_version + 1
                    WHERE outbox_id = ? AND status = 'CLAIMED'
                      AND claim_token = ? AND fencing_token = ?
                    """,
                    (receipt, digest, now, now, message_id, token, fence),
                )
                self._finish_attempt(
                    conn,
                    claim_token=token,
                    outcome="ACKED",
                    now=now,
                    reason_code=None,
                    receipt_ref=receipt,
                )
                row = self._require_message(conn, "OUTBOX", message_id)
                result = self._outbox_record(row)
        if payload_error:
            raise GatewayTransportStoreError(
                "GATEWAY_TRANSPORT_PAYLOAD_DIGEST_MISMATCH"
            )
        return result

    def retry_outbox(
        self,
        *,
        outbox_id: str,
        claim_token: str,
        fencing_token: int,
        reason_code: str,
        base_delay_ms: int = 1_000,
        max_delay_ms: int = 60_000,
    ) -> dict[str, Any]:
        return self._retry(
            direction="OUTBOX",
            message_id=outbox_id,
            claim_token=claim_token,
            fencing_token=fencing_token,
            reason_code=reason_code,
            base_delay_ms=base_delay_ms,
            max_delay_ms=max_delay_ms,
        )

    def replay_outbox(self, *, outbox_id: str, operator_ref: str) -> dict[str, Any]:
        return self._replay(direction="OUTBOX", message_id=outbox_id, operator_ref=operator_ref)

    # -- shared claim/retry/replay machinery ------------------------------------

    def mark_dispatch_started(
        self,
        *,
        direction: str,
        message_id: str,
        claim_token: str,
        fencing_token: int,
    ) -> dict[str, Any]:
        normalized_direction = str(direction or "").strip().upper()
        self._direction_meta(normalized_direction)
        normalized_id = _required_text(
            message_id,
            "GATEWAY_TRANSPORT_INBOX_ID_REQUIRED"
            if normalized_direction == "INBOX"
            else "GATEWAY_TRANSPORT_OUTBOX_ID_REQUIRED",
        )
        token = _required_text(claim_token, "GATEWAY_TRANSPORT_CLAIM_TOKEN_REQUIRED")
        fence = int(fencing_token)
        now = self._now()
        payload_error = False
        with self._transaction() as conn:
            self._recover_expired_claims(
                conn,
                direction=normalized_direction,
                now=now,
            )
            row = self._require_message(conn, normalized_direction, normalized_id)
            self._require_fence(row, token=token, fence=fence, now=now)
            payload_valid = (
                self._inbox_payload_valid(row)
                if normalized_direction == "INBOX"
                else self._outbox_payload_valid(row)
            )
            if not payload_valid:
                self._move_claim_to_dlq(
                    conn,
                    direction=normalized_direction,
                    row=row,
                    now=now,
                    reason_code="GATEWAY_TRANSPORT_PAYLOAD_DIGEST_MISMATCH",
                )
                payload_error = True
            else:
                attempt = conn.execute(
                    """
                    SELECT direction, message_id, fencing_token,
                           dispatch_started_at_ms, outcome
                    FROM gateway_transport_attempt
                    WHERE claim_token = ?
                    """,
                    (token,),
                ).fetchone()
                if (
                    attempt is None
                    or str(attempt["direction"]) != normalized_direction
                    or str(attempt["message_id"]) != normalized_id
                    or int(attempt["fencing_token"]) != fence
                ):
                    raise GatewayTransportStoreError(
                        "GATEWAY_TRANSPORT_ATTEMPT_CONFLICT"
                    )
                outcome = str(attempt["outcome"])
                if outcome == "CLAIMED":
                    cursor = conn.execute(
                        """
                        UPDATE gateway_transport_attempt
                        SET dispatch_started_at_ms = ?, outcome = 'DISPATCHING'
                        WHERE claim_token = ? AND fencing_token = ?
                          AND outcome = 'CLAIMED'
                        """,
                        (now, token, fence),
                    )
                    if cursor.rowcount != 1:
                        raise GatewayTransportStoreError(
                            "GATEWAY_TRANSPORT_ATTEMPT_CONFLICT"
                        )
                    dispatch_started_at_ms = now
                elif outcome == "DISPATCHING" and attempt["dispatch_started_at_ms"] is not None:
                    dispatch_started_at_ms = int(attempt["dispatch_started_at_ms"])
                else:
                    raise GatewayTransportStoreError(
                        "GATEWAY_TRANSPORT_ATTEMPT_CONFLICT"
                    )
                row = self._require_message(conn, normalized_direction, normalized_id)
                result = (
                    self._inbox_record(row)
                    if normalized_direction == "INBOX"
                    else self._outbox_record(row)
                )
                result["dispatch_started_at_ms"] = dispatch_started_at_ms
        if payload_error:
            raise GatewayTransportStoreError(
                "GATEWAY_TRANSPORT_PAYLOAD_DIGEST_MISMATCH"
            )
        return result

    def _claim(self, *, direction: str, worker_id: str, lease_ms: int) -> dict[str, Any] | None:
        worker = _required_text(worker_id, "GATEWAY_TRANSPORT_WORKER_REQUIRED")
        lease = int(lease_ms)
        if lease < 1:
            raise GatewayTransportStoreError("GATEWAY_TRANSPORT_LEASE_INVALID")
        now = self._now()
        error: str | None = None
        result: dict[str, Any] | None = None
        table, id_column, terminal = self._direction_meta(direction)
        with self._transaction() as conn:
            self._recover_expired_claims(conn, direction=direction, now=now)
            placeholders = ",".join("?" for _ in terminal)
            row = conn.execute(
                f"""
                SELECT candidate.*
                FROM {table} AS candidate
                WHERE candidate.status IN ('READY', 'RETRY_WAIT')
                  AND candidate.next_attempt_at_ms <= ?
                  AND NOT EXISTS (
                      SELECT 1 FROM {table} AS active
                      WHERE active.tenant_id = candidate.tenant_id
                        AND active.principal_id = candidate.principal_id
                        AND active.ordering_key = candidate.ordering_key
                        AND active.status = 'CLAIMED'
                  )
                  AND NOT EXISTS (
                      SELECT 1 FROM {table} AS earlier
                      WHERE earlier.tenant_id = candidate.tenant_id
                        AND earlier.principal_id = candidate.principal_id
                        AND earlier.ordering_key = candidate.ordering_key
                        AND earlier.status NOT IN ({placeholders})
                        AND (
                            (
                                candidate.source_sequence IS NOT NULL
                                AND earlier.source_sequence IS NOT NULL
                                AND (
                                    earlier.source_sequence < candidate.source_sequence
                                    OR (
                                        earlier.source_sequence = candidate.source_sequence
                                        AND earlier.cursor < candidate.cursor
                                    )
                                )
                            )
                            OR (
                                (
                                    candidate.source_sequence IS NULL
                                    OR earlier.source_sequence IS NULL
                                )
                                AND earlier.cursor < candidate.cursor
                            )
                        )
                  )
                ORDER BY candidate.cursor ASC
                LIMIT 1
                """,
                (now, *sorted(terminal)),
            ).fetchone()
            if row is None:
                return None
            payload_valid = (
                self._inbox_payload_valid(row)
                if direction == "INBOX"
                else self._outbox_payload_valid(row)
            )
            if not payload_valid:
                conn.execute(
                    f"""
                    UPDATE {table}
                    SET status = 'DLQ', dlq_at_ms = ?, updated_at_ms = ?,
                        last_reason_code = ?, status_version = status_version + 1
                    WHERE {id_column} = ? AND status IN ('READY', 'RETRY_WAIT')
                    """,
                    (
                        now,
                        now,
                        "GATEWAY_TRANSPORT_PAYLOAD_DIGEST_MISMATCH",
                        str(row[id_column]),
                    ),
                )
                error = "GATEWAY_TRANSPORT_PAYLOAD_DIGEST_MISMATCH"
            else:
                claim_token = f"gateway-transport-claim://{uuid.uuid4().hex}"
                claim_expires = now + lease
                attempt_no = int(row["attempt_count"]) + 1
                fence = int(row["fencing_token"]) + 1
                cursor = conn.execute(
                    f"""
                    UPDATE {table}
                    SET status = 'CLAIMED', attempt_count = ?, claim_token = ?,
                        claim_owner = ?, claim_expires_at_ms = ?,
                        fencing_token = ?, updated_at_ms = ?,
                        status_version = status_version + 1
                    WHERE {id_column} = ? AND status_version = ?
                      AND status IN ('READY', 'RETRY_WAIT')
                    """,
                    (
                        attempt_no,
                        claim_token,
                        worker,
                        claim_expires,
                        fence,
                        now,
                        str(row[id_column]),
                        int(row["status_version"]),
                    ),
                )
                if cursor.rowcount != 1:
                    raise GatewayTransportStoreError("GATEWAY_TRANSPORT_CLAIM_CONFLICT")
                conn.execute(
                    """
                    INSERT INTO gateway_transport_attempt(
                        attempt_id, direction, message_id, replay_no, attempt_no,
                        claim_token, fencing_token, worker_id, started_at_ms,
                        lease_expires_at_ms, dispatch_started_at_ms,
                        completed_at_ms, outcome,
                        reason_code, receipt_ref
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, 'CLAIMED', NULL, NULL)
                    """,
                    (
                        f"gateway-transport-attempt://{uuid.uuid4().hex}",
                        direction,
                        str(row[id_column]),
                        int(row["replay_count"]),
                        attempt_no,
                        claim_token,
                        fence,
                        worker,
                        now,
                        claim_expires,
                    ),
                )
                claimed = self._require_message(conn, direction, str(row[id_column]))
                result = (
                    self._inbox_record(claimed, include_payload=True)
                    if direction == "INBOX"
                    else self._outbox_record(claimed, include_payload=True)
                )
        if error is not None:
            raise GatewayTransportStoreError(error)
        return result

    def _retry(
        self,
        *,
        direction: str,
        message_id: str,
        claim_token: str,
        fencing_token: int,
        reason_code: str,
        base_delay_ms: int,
        max_delay_ms: int,
    ) -> dict[str, Any]:
        _table, id_column, _terminal = self._direction_meta(direction)
        normalized_id = _required_text(
            message_id,
            "GATEWAY_TRANSPORT_INBOX_ID_REQUIRED"
            if direction == "INBOX"
            else "GATEWAY_TRANSPORT_OUTBOX_ID_REQUIRED",
        )
        token = _required_text(claim_token, "GATEWAY_TRANSPORT_CLAIM_TOKEN_REQUIRED")
        fence = int(fencing_token)
        requested_reason = _reason_code(reason_code)
        base = int(base_delay_ms)
        cap = int(max_delay_ms)
        if base < 1 or cap < base:
            raise GatewayTransportStoreError("GATEWAY_TRANSPORT_BACKOFF_INVALID")
        now = self._now()
        payload_error = False
        with self._transaction() as conn:
            self._recover_expired_claims(conn, direction=direction, now=now)
            row = self._require_message(conn, direction, normalized_id)
            self._require_fence(row, token=token, fence=fence, now=now)
            attempt = conn.execute(
                """
                SELECT direction, message_id, fencing_token,
                       dispatch_started_at_ms, outcome
                FROM gateway_transport_attempt
                WHERE claim_token = ?
                """,
                (token,),
            ).fetchone()
            if (
                attempt is None
                or str(attempt["direction"]) != direction
                or str(attempt["message_id"]) != normalized_id
                or int(attempt["fencing_token"]) != fence
            ):
                raise GatewayTransportStoreError(
                    "GATEWAY_TRANSPORT_ATTEMPT_CONFLICT"
                )
            delivery_uncertain = (
                str(attempt["outcome"]) == "DISPATCHING"
                or attempt["dispatch_started_at_ms"] is not None
            )
            payload_valid = (
                self._inbox_payload_valid(row)
                if direction == "INBOX"
                else self._outbox_payload_valid(row)
            )
            if not payload_valid:
                self._move_claim_to_dlq(
                    conn,
                    direction=direction,
                    row=row,
                    now=now,
                    reason_code="GATEWAY_TRANSPORT_PAYLOAD_DIGEST_MISMATCH",
                )
                payload_error = True
            else:
                exhausted = int(row["attempt_count"]) >= int(row["max_attempts"])
                next_status = "DLQ" if delivery_uncertain or exhausted else "RETRY_WAIT"
                effective_reason = (
                    "GATEWAY_TRANSPORT_DELIVERY_UNCERTAIN"
                    if delivery_uncertain
                    else requested_reason
                )
                shift = min(30, max(0, int(row["attempt_count"]) - 1))
                delay = min(cap, base * (2**shift))
                next_at = now if delivery_uncertain or exhausted else now + delay
                table, id_column, _terminal = self._direction_meta(direction)
                conn.execute(
                    f"""
                    UPDATE {table}
                    SET status = ?, next_attempt_at_ms = ?,
                        claim_token = NULL, claim_owner = NULL,
                        claim_expires_at_ms = NULL, last_reason_code = ?,
                        dlq_at_ms = CASE WHEN ? = 'DLQ' THEN ? ELSE NULL END,
                        updated_at_ms = ?, status_version = status_version + 1
                    WHERE {id_column} = ? AND status = 'CLAIMED'
                      AND claim_token = ? AND fencing_token = ?
                    """,
                    (
                        next_status,
                        next_at,
                        effective_reason,
                        next_status,
                        now,
                        now,
                        normalized_id,
                        token,
                        fence,
                    ),
                )
                self._finish_attempt(
                    conn,
                    claim_token=token,
                    outcome=next_status,
                    now=now,
                    reason_code=effective_reason,
                    receipt_ref=None,
                )
                row = self._require_message(conn, direction, normalized_id)
                result = (
                    self._inbox_record(row)
                    if direction == "INBOX"
                    else self._outbox_record(row)
                )
        if payload_error:
            raise GatewayTransportStoreError(
                "GATEWAY_TRANSPORT_PAYLOAD_DIGEST_MISMATCH"
            )
        return result

    def _replay(self, *, direction: str, message_id: str, operator_ref: str) -> dict[str, Any]:
        normalized_id = _required_text(
            message_id,
            "GATEWAY_TRANSPORT_INBOX_ID_REQUIRED"
            if direction == "INBOX"
            else "GATEWAY_TRANSPORT_OUTBOX_ID_REQUIRED",
        )
        operator = _required_text(
            operator_ref, "GATEWAY_TRANSPORT_REPLAY_OPERATOR_REQUIRED", 2048
        )
        now = self._now()
        table, id_column, _terminal = self._direction_meta(direction)
        with self._transaction() as conn:
            row = self._require_message(conn, direction, normalized_id)
            if str(row["status"]) != "DLQ":
                raise GatewayTransportStoreError("GATEWAY_TRANSPORT_REPLAY_NOT_DLQ")
            if str(row["last_reason_code"] or "").strip().upper() == (
                "GATEWAY_TRANSPORT_DELIVERY_UNCERTAIN"
            ):
                # A started external side effect may already have succeeded.
                # A plain operator label is not receipt reconciliation and
                # must never authorize a potentially duplicate delivery.
                raise GatewayTransportStoreError(
                    "GATEWAY_TRANSPORT_REPLAY_REQUIRES_RECONCILIATION"
                )
            payload_valid = (
                self._inbox_payload_valid(row)
                if direction == "INBOX"
                else self._outbox_payload_valid(row)
            )
            if not payload_valid:
                raise GatewayTransportStoreError(
                    "GATEWAY_TRANSPORT_PAYLOAD_DIGEST_MISMATCH"
                )
            conn.execute(
                f"""
                UPDATE {table}
                SET status = 'READY', attempt_count = 0,
                    next_attempt_at_ms = ?, claim_token = NULL,
                    claim_owner = NULL, claim_expires_at_ms = NULL,
                    last_reason_code = NULL, last_replay_operator_ref = ?,
                    replay_count = replay_count + 1, dlq_at_ms = NULL,
                    updated_at_ms = ?, status_version = status_version + 1
                WHERE {id_column} = ? AND status = 'DLQ'
                """,
                (now, operator, now, normalized_id),
            )
            row = self._require_message(conn, direction, normalized_id)
            return (
                self._inbox_record(row)
                if direction == "INBOX"
                else self._outbox_record(row)
            )

    def _recover_expired_claims(
        self,
        conn: sqlite3.Connection,
        *,
        direction: str,
        now: int,
    ) -> None:
        table, id_column, _terminal = self._direction_meta(direction)
        rows = conn.execute(
            f"""
            SELECT * FROM {table}
            WHERE status = 'CLAIMED' AND claim_expires_at_ms <= ?
            ORDER BY cursor
            """,
            (now,),
        ).fetchall()
        for row in rows:
            attempt = conn.execute(
                """
                SELECT outcome, dispatch_started_at_ms
                FROM gateway_transport_attempt
                WHERE claim_token = ?
                """,
                (str(row["claim_token"]),),
            ).fetchone()
            if attempt is None:
                raise GatewayTransportStoreError(
                    "GATEWAY_TRANSPORT_ATTEMPT_CONFLICT"
                )
            delivery_uncertain = (
                str(attempt["outcome"]) == "DISPATCHING"
                or attempt["dispatch_started_at_ms"] is not None
            )
            exhausted = int(row["attempt_count"]) >= int(row["max_attempts"])
            next_status = "DLQ" if delivery_uncertain or exhausted else "RETRY_WAIT"
            if delivery_uncertain:
                reason = "GATEWAY_TRANSPORT_DELIVERY_UNCERTAIN"
            elif exhausted:
                reason = "GATEWAY_TRANSPORT_LEASE_EXPIRED_MAX_ATTEMPTS"
            else:
                reason = "GATEWAY_TRANSPORT_LEASE_EXPIRED"
            conn.execute(
                f"""
                UPDATE {table}
                SET status = ?, next_attempt_at_ms = ?, claim_token = NULL,
                    claim_owner = NULL, claim_expires_at_ms = NULL,
                    last_reason_code = ?,
                    dlq_at_ms = CASE WHEN ? = 'DLQ' THEN ? ELSE NULL END,
                    updated_at_ms = ?, status_version = status_version + 1
                WHERE {id_column} = ? AND status = 'CLAIMED'
                  AND claim_token = ? AND fencing_token = ?
                """,
                (
                    next_status,
                    now,
                    reason,
                    next_status,
                    now,
                    now,
                    str(row[id_column]),
                    str(row["claim_token"]),
                    int(row["fencing_token"]),
                ),
            )
            self._finish_attempt(
                conn,
                claim_token=str(row["claim_token"]),
                outcome="DLQ" if next_status == "DLQ" else "LEASE_EXPIRED",
                now=now,
                reason_code=reason,
                receipt_ref=None,
            )

    @staticmethod
    def _require_fence(row: sqlite3.Row, *, token: str, fence: int, now: int) -> None:
        if (
            str(row["status"]) != "CLAIMED"
            or str(row["claim_token"] or "") != token
            or int(row["fencing_token"]) != int(fence)
            or row["claim_expires_at_ms"] is None
            or int(row["claim_expires_at_ms"]) <= now
        ):
            raise GatewayTransportStoreError("GATEWAY_TRANSPORT_STALE_FENCE")

    def _move_claim_to_dlq(
        self,
        conn: sqlite3.Connection,
        *,
        direction: str,
        row: sqlite3.Row,
        now: int,
        reason_code: str,
    ) -> None:
        table, id_column, _terminal = self._direction_meta(direction)
        conn.execute(
            f"""
            UPDATE {table}
            SET status = 'DLQ', dlq_at_ms = ?, updated_at_ms = ?,
                claim_token = NULL, claim_owner = NULL,
                claim_expires_at_ms = NULL, last_reason_code = ?,
                status_version = status_version + 1
            WHERE {id_column} = ? AND status = 'CLAIMED'
              AND claim_token = ? AND fencing_token = ?
            """,
            (
                now,
                now,
                reason_code,
                str(row[id_column]),
                str(row["claim_token"]),
                int(row["fencing_token"]),
            ),
        )
        self._finish_attempt(
            conn,
            claim_token=str(row["claim_token"]),
            outcome="DLQ",
            now=now,
            reason_code=reason_code,
            receipt_ref=None,
        )

    @staticmethod
    def _finish_attempt(
        conn: sqlite3.Connection,
        *,
        claim_token: str,
        outcome: str,
        now: int,
        reason_code: str | None,
        receipt_ref: str | None,
    ) -> None:
        cursor = conn.execute(
            """
            UPDATE gateway_transport_attempt
            SET completed_at_ms = ?, outcome = ?, reason_code = ?, receipt_ref = ?
            WHERE claim_token = ? AND outcome IN ('CLAIMED', 'DISPATCHING')
            """,
            (now, outcome, reason_code, receipt_ref, claim_token),
        )
        if cursor.rowcount != 1:
            raise GatewayTransportStoreError("GATEWAY_TRANSPORT_ATTEMPT_CONFLICT")

    @staticmethod
    def _direction_meta(direction: str) -> tuple[str, str, frozenset[str]]:
        if direction == "INBOX":
            return "gateway_transport_inbox", "inbox_id", _INBOX_TERMINAL
        if direction == "OUTBOX":
            return "gateway_transport_outbox", "outbox_id", _OUTBOX_TERMINAL
        raise GatewayTransportStoreError("GATEWAY_TRANSPORT_DIRECTION_INVALID")

    @classmethod
    def _require_message(
        cls,
        conn: sqlite3.Connection,
        direction: str,
        message_id: str,
    ) -> sqlite3.Row:
        table, id_column, _terminal = cls._direction_meta(direction)
        row = conn.execute(
            f"SELECT * FROM {table} WHERE {id_column} = ?", (message_id,)
        ).fetchone()
        if row is None:
            raise GatewayTransportStoreError("GATEWAY_TRANSPORT_MESSAGE_NOT_FOUND")
        return row

    @staticmethod
    def _inbox_payload_valid(row: sqlite3.Row) -> bool:
        try:
            raw_body_size = int(row["raw_body_size_bytes"])
            raw_body_digest = str(row["raw_body_sha256"])
            normalized_json = str(row["normalized_payload_json"])
            normalized = json.loads(normalized_json)
            safe_headers_json = str(row["safe_headers_json"])
            safe_headers = json.loads(safe_headers_json)
            return (
                isinstance(normalized, dict)
                and isinstance(safe_headers, dict)
                and 0 < raw_body_size <= GATEWAY_TRANSPORT_MAX_INBOX_BODY_BYTES
                and _SHA256_PATTERN.fullmatch(raw_body_digest) is not None
                and len(normalized_json.encode("utf-8"))
                <= GATEWAY_TRANSPORT_MAX_NORMALIZED_PAYLOAD_BYTES
                and len(safe_headers_json.encode("utf-8"))
                <= GATEWAY_TRANSPORT_MAX_SAFE_HEADERS_BYTES
                and _digest_text(_canonical_json(normalized))
                == str(row["normalized_payload_digest"])
                and _safe_headers(safe_headers) == safe_headers_json
                and _digest_text(safe_headers_json)
                == str(row["safe_headers_sha256"])
            )
        except (TypeError, ValueError, json.JSONDecodeError, GatewayTransportStoreError):
            return False

    @staticmethod
    def _outbox_payload_valid(row: sqlite3.Row) -> bool:
        try:
            payload = bytes(row["payload_body"])
            return (
                0 < len(payload) <= GATEWAY_TRANSPORT_MAX_OUTBOX_BODY_BYTES
                and _digest_bytes(payload) == str(row["payload_sha256"])
            )
        except (TypeError, ValueError):
            return False

    # -- safe reads --------------------------------------------------------------

    def transport_counts(self) -> dict[str, int]:
        """Return a payload-free drain snapshot from one read transaction.

        DLQ records are deliberately excluded from ``*_pending``: they require
        reconciliation instead of automatic draining and are reported through
        ``*_dlq`` / ``reconciliation_required``.
        """

        conn = self._connect()
        try:
            conn.execute("BEGIN")
            row = conn.execute(
                """
                SELECT
                    SUM(CASE WHEN direction = 'INBOX'
                                  AND status IN ('READY', 'RETRY_WAIT', 'CLAIMED')
                             THEN 1 ELSE 0 END) AS inbox_pending,
                    SUM(CASE WHEN direction = 'OUTBOX'
                                  AND status IN ('READY', 'RETRY_WAIT', 'CLAIMED')
                             THEN 1 ELSE 0 END) AS outbox_pending,
                    SUM(CASE WHEN direction = 'INBOX' AND status = 'CLAIMED'
                             THEN 1 ELSE 0 END) AS inbox_claimed,
                    SUM(CASE WHEN direction = 'OUTBOX' AND status = 'CLAIMED'
                             THEN 1 ELSE 0 END) AS outbox_claimed,
                    SUM(CASE WHEN direction = 'INBOX' AND status = 'DLQ'
                             THEN 1 ELSE 0 END) AS inbox_dlq,
                    SUM(CASE WHEN direction = 'OUTBOX' AND status = 'DLQ'
                             THEN 1 ELSE 0 END) AS outbox_dlq
                FROM (
                    SELECT 'INBOX' AS direction, status
                    FROM gateway_transport_inbox
                    UNION ALL
                    SELECT 'OUTBOX' AS direction, status
                    FROM gateway_transport_outbox
                ) AS transport_state
                """
            ).fetchone()
            conn.commit()
            counts = {
                key: int(row[key] or 0)
                for key in (
                    "inbox_pending",
                    "outbox_pending",
                    "inbox_claimed",
                    "outbox_claimed",
                    "inbox_dlq",
                    "outbox_dlq",
                )
            }
            counts["claimed"] = counts["inbox_claimed"] + counts["outbox_claimed"]
            counts["reconciliation_required"] = (
                counts["inbox_dlq"] + counts["outbox_dlq"]
            )
            return counts
        finally:
            conn.close()

    def get_inbox(self, *, inbox_id: str) -> dict[str, Any]:
        message_id = _required_text(inbox_id, "GATEWAY_TRANSPORT_INBOX_ID_REQUIRED")
        conn = self._connect()
        try:
            row = self._require_message(conn, "INBOX", message_id)
            if not self._inbox_payload_valid(row):
                raise GatewayTransportStoreError(
                    "GATEWAY_TRANSPORT_PAYLOAD_DIGEST_MISMATCH"
                )
            return self._inbox_record(row)
        finally:
            conn.close()

    def get_outbox(self, *, outbox_id: str) -> dict[str, Any]:
        message_id = _required_text(outbox_id, "GATEWAY_TRANSPORT_OUTBOX_ID_REQUIRED")
        conn = self._connect()
        try:
            row = self._require_message(conn, "OUTBOX", message_id)
            if not self._outbox_payload_valid(row):
                raise GatewayTransportStoreError(
                    "GATEWAY_TRANSPORT_PAYLOAD_DIGEST_MISMATCH"
                )
            return self._outbox_record(row)
        finally:
            conn.close()

    def list_dlq(self, *, direction: str, limit: int = 100) -> list[dict[str, Any]]:
        normalized = str(direction or "").strip().upper()
        table, _id_column, _terminal = self._direction_meta(normalized)
        size = min(1_000, max(1, int(limit)))
        conn = self._connect()
        try:
            rows = conn.execute(
                f"SELECT * FROM {table} WHERE status = 'DLQ' ORDER BY dlq_at_ms, cursor LIMIT ?",
                (size,),
            ).fetchall()
            return [
                self._inbox_record(row)
                if normalized == "INBOX"
                else self._outbox_record(row)
                for row in rows
            ]
        finally:
            conn.close()

    @staticmethod
    def _common_record(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "cursor": int(row["cursor"]),
            "status": str(row["status"]),
            "state": str(row["status"]),
            "attempt_count": int(row["attempt_count"]),
            "max_attempts": int(row["max_attempts"]),
            "next_attempt_at_ms": int(row["next_attempt_at_ms"]),
            "claim_token": str(row["claim_token"]) if row["claim_token"] else None,
            "claim_owner": str(row["claim_owner"]) if row["claim_owner"] else None,
            "claim_expires_at_ms": (
                int(row["claim_expires_at_ms"])
                if row["claim_expires_at_ms"] is not None
                else None
            ),
            "fencing_token": int(row["fencing_token"]),
            "last_reason_code": (
                str(row["last_reason_code"]) if row["last_reason_code"] else None
            ),
            "last_replay_operator_ref": (
                str(row["last_replay_operator_ref"])
                if row["last_replay_operator_ref"]
                else None
            ),
            "replay_count": int(row["replay_count"]),
            "created_at_ms": int(row["created_at_ms"]),
            "updated_at_ms": int(row["updated_at_ms"]),
            "status_version": int(row["status_version"]),
        }

    @classmethod
    def _inbox_record(cls, row: sqlite3.Row, *, include_payload: bool = False) -> dict[str, Any]:
        result = {
            **cls._common_record(row),
            "inbox_id": str(row["inbox_id"]),
            "surface": str(row["surface"]),
            "platform": str(row["platform"]),
            "principal_id": str(row["principal_id"]),
            "tenant_id": str(row["tenant_id"]),
            "secure_claim_ref": (
                str(row["secure_claim_ref"]) if row["secure_claim_ref"] else None
            ),
            "verification_audit_ref": (
                str(row["verification_audit_ref"])
                if row["verification_audit_ref"]
                else None
            ),
            "platform_event_id": (
                str(row["platform_event_id"]) if row["platform_event_id"] else None
            ),
            "idempotency_digest": str(row["idempotency_digest"]),
            "ack_ref": str(row["ingress_ack_ref"]),
            "raw_body_sha256": str(row["raw_body_sha256"]),
            "raw_body_size_bytes": int(row["raw_body_size_bytes"]),
            "safe_headers_sha256": str(row["safe_headers_sha256"]),
            "normalized_payload_digest": str(row["normalized_payload_digest"]),
            "ordering_key": str(row["ordering_key"]),
            "source_sequence": (
                int(row["source_sequence"]) if row["source_sequence"] is not None else None
            ),
            "delivery_receipt_ref": (
                str(row["delivery_receipt_ref"])
                if row["delivery_receipt_ref"]
                else None
            ),
            "delivered_at_ms": (
                int(row["delivered_at_ms"]) if row["delivered_at_ms"] is not None else None
            ),
            "dlq_at_ms": int(row["dlq_at_ms"]) if row["dlq_at_ms"] is not None else None,
        }
        if include_payload:
            result["safe_headers"] = json.loads(str(row["safe_headers_json"]))
            result["normalized_payload"] = json.loads(
                str(row["normalized_payload_json"])
            )
        return result

    @classmethod
    def _outbox_record(cls, row: sqlite3.Row, *, include_payload: bool = False) -> dict[str, Any]:
        result = {
            **cls._common_record(row),
            "outbox_id": str(row["outbox_id"]),
            "origin_inbox_id": (
                str(row["origin_inbox_id"]) if row["origin_inbox_id"] else None
            ),
            "tenant_id": str(row["tenant_id"]),
            "principal_id": str(row["principal_id"]),
            "destination": str(row["destination"]),
            "idempotency_digest": str(row["idempotency_digest"]),
            "payload_sha256": str(row["payload_sha256"]),
            "ordering_key": str(row["ordering_key"]),
            "source_sequence": (
                int(row["source_sequence"]) if row["source_sequence"] is not None else None
            ),
            "ack_ref": str(row["ack_ref"]) if row["ack_ref"] else None,
            "ack_digest": str(row["ack_digest"]) if row["ack_digest"] else None,
            "acked_at_ms": int(row["acked_at_ms"]) if row["acked_at_ms"] is not None else None,
            "dlq_at_ms": int(row["dlq_at_ms"]) if row["dlq_at_ms"] is not None else None,
            "cancelled_at_ms": (
                int(row["cancelled_at_ms"]) if row["cancelled_at_ms"] is not None else None
            ),
        }
        if include_payload:
            result["payload"] = bytes(row["payload_body"])
        return result


# Concise public alias for composition roots.
GatewayTransportStore = SQLiteGatewayTransportStore


__all__ = [
    "GATEWAY_TRANSPORT_MIGRATION_ASSET_KEY",
    "GATEWAY_TRANSPORT_MIGRATION_SHA256",
    "GatewayTransportStore",
    "GatewayTransportStoreError",
    "InboxAcceptance",
    "InboxAcceptanceStatus",
    "SQLiteGatewayTransportStore",
]
