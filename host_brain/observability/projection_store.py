"""Generic durable kind/key storage for operator query projections."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import sqlite3
import time
import zlib
from collections.abc import Iterable, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator

from natsume_contracts.operator.v1 import (
    OPERATOR_PROJECTION_SCHEMA_VERSION,
    ProjectionDegradedState,
    ProjectionDocument,
)


PROJECTION_SCHEMA_NOT_READY = "OPERATOR_PROJECTION_SCHEMA_NOT_READY"
PROJECTION_DATA_INVALID = "OPERATOR_PROJECTION_DATA_INVALID"
PROJECTION_CURSOR_REGRESSION = "OPERATOR_PROJECTION_CURSOR_REGRESSION"
PROJECTION_CURSOR_CONFLICT = "OPERATOR_PROJECTION_CURSOR_CONFLICT"
PROJECTION_LEASE_FENCED = "OPERATOR_PROJECTION_LEASE_FENCED"


@dataclass(frozen=True, slots=True)
class ProjectionLeaderLease:
    lease_name: str
    owner_id: str
    fencing_token: int
    acquired_ts_ms: int
    lease_expires_ts_ms: int


_BOUND_LEADER_LEASE: ContextVar[ProjectionLeaderLease | None] = ContextVar(
    "operator_projection_leader_lease",
    default=None,
)
_UNCONDITIONAL_CURRENT = object()
_JOURNAL_PAYLOAD_SCHEMA = "Natsume.operator.query_projection.journal_payload.zlib.v1"
_JOURNAL_COMPRESSION_MIN_BYTES = 8 * 1024
_JOURNAL_MAX_UNCOMPRESSED_BYTES = 16 * 1024 * 1024
_JOURNAL_MAX_ENCODED_BYTES = ((_JOURNAL_MAX_UNCOMPRESSED_BYTES + 1024) * 4 // 3) + 4


class QueryProjectionError(RuntimeError):
    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = reason_code
        self.detail = detail
        super().__init__(f"{reason_code}: {detail}")


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest_json(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _encode_journal_payload(
    value: Mapping[str, Any],
    *,
    source_digest: str,
) -> str:
    """Compact large derived snapshots without changing their public digest.

    The materialized document remains ordinary JSON for low-latency Monitor
    reads.  Only the immutable replay journal uses this envelope, represented
    as a JSON array so it cannot collide with a valid object payload.
    """

    raw = canonical_json(value)
    raw_bytes = raw.encode("utf-8")
    if (
        len(raw_bytes) < _JOURNAL_COMPRESSION_MIN_BYTES
        or len(raw_bytes) > _JOURNAL_MAX_UNCOMPRESSED_BYTES
    ):
        return raw
    compressed = zlib.compress(raw_bytes, level=1)
    encoded = canonical_json(
        [
            _JOURNAL_PAYLOAD_SCHEMA,
            len(raw_bytes),
            source_digest,
            base64.b64encode(compressed).decode("ascii"),
        ]
    )
    return encoded if len(encoded.encode("utf-8")) < len(raw_bytes) else raw


def _decode_journal_payload(
    encoded: str,
    *,
    expected_digest: str,
) -> dict[str, Any]:
    """Decode legacy object rows and bounded compressed journal envelopes."""

    try:
        stored = json.loads(encoded)
    except json.JSONDecodeError as exc:
        raise QueryProjectionError(
            PROJECTION_DATA_INVALID,
            "projection journal source_payload_json is invalid",
        ) from exc

    if isinstance(stored, dict):
        payload = stored
    elif (
        isinstance(stored, list)
        and len(stored) == 4
        and stored[0] == _JOURNAL_PAYLOAD_SCHEMA
    ):
        declared_size, envelope_digest, payload_text = stored[1:]
        if (
            type(declared_size) is not int
            or declared_size < 0
            or declared_size > _JOURNAL_MAX_UNCOMPRESSED_BYTES
            or not isinstance(envelope_digest, str)
            or envelope_digest != expected_digest
            or not isinstance(payload_text, str)
            or len(payload_text) > _JOURNAL_MAX_ENCODED_BYTES
        ):
            raise QueryProjectionError(
                PROJECTION_DATA_INVALID,
                "projection journal compressed envelope is invalid",
            )
        try:
            compressed = base64.b64decode(payload_text, validate=True)
            inflater = zlib.decompressobj()
            raw_bytes = inflater.decompress(compressed, declared_size + 1)
            if (
                len(raw_bytes) > declared_size
                or inflater.unconsumed_tail
                or not inflater.eof
                or inflater.unused_data
            ):
                raise ValueError("compressed payload boundary mismatch")
            raw_bytes += inflater.flush()
            if len(raw_bytes) != declared_size:
                raise ValueError("compressed payload size mismatch")
            payload = json.loads(raw_bytes.decode("utf-8"))
        except (
            binascii.Error,
            UnicodeDecodeError,
            ValueError,
            zlib.error,
            json.JSONDecodeError,
        ) as exc:
            raise QueryProjectionError(
                PROJECTION_DATA_INVALID,
                "projection journal compressed payload is invalid",
            ) from exc
        if not isinstance(payload, dict):
            raise QueryProjectionError(
                PROJECTION_DATA_INVALID,
                "projection journal payload must be an object",
            )
    else:
        raise QueryProjectionError(
            PROJECTION_DATA_INVALID,
            "projection journal payload must be an object or supported envelope",
        )

    if digest_json(payload) != expected_digest:
        raise QueryProjectionError(
            PROJECTION_DATA_INVALID,
            "projection journal payload digest mismatch",
        )
    return payload


def public_projection_payload(value: Mapping[str, Any]) -> dict[str, Any]:
    """Apply the fail-closed operator serializer before any durable write."""

    from host_brain.observability.operator_query_gateway import (
        DigestOperatorQueryAuthenticator,
        OperatorQueryGateway,
    )

    sanitized = OperatorQueryGateway(DigestOperatorQueryAuthenticator()).serialize_public(dict(value)).payload
    if not isinstance(sanitized, dict):
        raise QueryProjectionError(PROJECTION_DATA_INVALID, "public projection payload must be an object")
    return sanitized


def _public_degraded_detail(value: str | None) -> str | None:
    if not value:
        return None
    sanitized = public_projection_payload({"detail": str(value)[:1000]}).get("detail")
    return str(sanitized)[:1000] if sanitized is not None else None


class QueryProjectionStore:
    """Migration-first projection repository with no domain-specific branches.

    Public API is intentionally generic: ``get(kind, key)``, ``upsert(...)``,
    ``list(kind)`` and ``rebuild(kind, documents)``.  Device, task, trace and
    session projections use the same durable contract.
    """

    def __init__(
        self,
        db_path: str | Path,
        *,
        clock: Callable[[], int] | None = None,
    ) -> None:
        raw = str(db_path or "").strip()
        if not raw:
            raise ValueError("db_path is required")
        self.db_path = raw
        self._clock = clock or (lambda: int(time.time() * 1000))
        self._assert_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 10000")
        return conn

    def _assert_schema(self) -> None:
        try:
            with self._connect() as conn:
                names = {
                    str(row[0])
                    for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table' AND name LIKE 'operator_query_projection_%'"
                    )
                }
        except sqlite3.Error as exc:
            raise QueryProjectionError(PROJECTION_SCHEMA_NOT_READY, str(exc)) from exc
        required = {
            "operator_query_projection_journal",
            "operator_query_projection_document",
            "operator_query_projection_state",
            "operator_query_projection_leader",
        }
        if not required.issubset(names):
            raise QueryProjectionError(
                PROJECTION_SCHEMA_NOT_READY,
                "apply migration 0082_operator_query_projection.sql",
            )

    @staticmethod
    def _required(value: str, field: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError(f"{field} is required")
        return text

    def acquire_leader_lease(
        self,
        owner_id: str,
        *,
        lease_name: str = "operator-query-projection-daemon",
        lease_ms: int = 30_000,
    ) -> ProjectionLeaderLease | None:
        """Acquire or renew the cross-process daemon lease.

        A takeover always advances the fencing token, including a reacquire by
        the same owner after expiry.  Consequently an old in-flight lease can
        never renew or release the new incarnation.
        """

        owner = self._required(owner_id, "owner_id")
        name = self._required(lease_name, "lease_name")
        duration = max(1, int(lease_ms))
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            now = max(0, int(self._clock()))
            expires = now + duration
            row = conn.execute(
                "SELECT * FROM operator_query_projection_leader WHERE lease_name = ?",
                (name,),
            ).fetchone()
            if row is None:
                conn.execute(
                    """
                    INSERT INTO operator_query_projection_leader(
                        lease_name, owner_id, fencing_token, acquired_ts_ms,
                        lease_expires_ts_ms, updated_ts_ms
                    ) VALUES (?, ?, 1, ?, ?, ?)
                    """,
                    (name, owner, now, expires, now),
                )
                token = 1
                acquired = now
            elif int(row["lease_expires_ts_ms"]) <= now:
                token = int(row["fencing_token"]) + 1
                acquired = now
                conn.execute(
                    """
                    UPDATE operator_query_projection_leader
                    SET owner_id = ?, fencing_token = ?, acquired_ts_ms = ?,
                        lease_expires_ts_ms = ?, updated_ts_ms = ?
                    WHERE lease_name = ?
                    """,
                    (owner, token, acquired, expires, now, name),
                )
            elif str(row["owner_id"]) == owner:
                token = int(row["fencing_token"])
                acquired = int(row["acquired_ts_ms"])
                conn.execute(
                    """
                    UPDATE operator_query_projection_leader
                    SET lease_expires_ts_ms = ?, updated_ts_ms = ?
                    WHERE lease_name = ? AND owner_id = ? AND fencing_token = ?
                    """,
                    (expires, now, name, owner, token),
                )
            else:
                conn.rollback()
                return None
            conn.commit()
        return ProjectionLeaderLease(name, owner, token, acquired, expires)

    def renew_leader_lease(
        self,
        lease: ProjectionLeaderLease,
        *,
        lease_ms: int = 30_000,
    ) -> ProjectionLeaderLease | None:
        """Renew only the current, unexpired owner/fence tuple."""

        duration = max(1, int(lease_ms))
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            now = max(0, int(self._clock()))
            expires = now + duration
            cursor = conn.execute(
                """
                UPDATE operator_query_projection_leader
                SET lease_expires_ts_ms = ?, updated_ts_ms = ?
                WHERE lease_name = ? AND owner_id = ? AND fencing_token = ?
                  AND lease_expires_ts_ms > ?
                """,
                (
                    expires,
                    now,
                    lease.lease_name,
                    lease.owner_id,
                    int(lease.fencing_token),
                    now,
                ),
            )
            if cursor.rowcount != 1:
                conn.rollback()
                return None
            conn.commit()
        return ProjectionLeaderLease(
            lease.lease_name,
            lease.owner_id,
            lease.fencing_token,
            lease.acquired_ts_ms,
            expires,
        )

    def renew_bound_leader_lease(
        self,
        *,
        lease_ms: int = 30_000,
    ) -> ProjectionLeaderLease | None:
        """Renew the lease currently fencing a long-running projection job.

        Most projection callbacks complete inside one lease period.  A trace
        refresh can legitimately traverse hundreds of durable requests, so it
        must periodically prove that it still owns the same fence instead of
        either expiring mid-wave or silently writing without a lease.
        """

        lease = _BOUND_LEADER_LEASE.get()
        if lease is None:
            return None
        renewed = self.renew_leader_lease(lease, lease_ms=lease_ms)
        if renewed is None:
            raise QueryProjectionError(
                PROJECTION_LEASE_FENCED,
                "projection daemon leader fence was lost during refresh",
            )
        _BOUND_LEADER_LEASE.set(renewed)
        return renewed

    def holds_leader_lease(self, lease: ProjectionLeaderLease) -> bool:
        now = max(0, int(self._clock()))
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT 1 FROM operator_query_projection_leader
                WHERE lease_name = ? AND owner_id = ? AND fencing_token = ?
                  AND lease_expires_ts_ms > ?
                """,
                (lease.lease_name, lease.owner_id, int(lease.fencing_token), now),
            ).fetchone()
        return row is not None

    def release_leader_lease(self, lease: ProjectionLeaderLease) -> bool:
        """Expire an exact lease without deleting its monotonic fence row."""

        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            now = max(0, int(self._clock()))
            cursor = conn.execute(
                """
                UPDATE operator_query_projection_leader
                SET lease_expires_ts_ms = ?, updated_ts_ms = ?
                WHERE lease_name = ? AND owner_id = ? AND fencing_token = ?
                  AND lease_expires_ts_ms > ?
                """,
                (
                    now,
                    now,
                    lease.lease_name,
                    lease.owner_id,
                    int(lease.fencing_token),
                    now,
                ),
            )
            changed = cursor.rowcount == 1
            conn.commit()
        return changed

    @contextmanager
    def bind_leader_lease(self, lease: ProjectionLeaderLease) -> Iterator[None]:
        """Fence projection writes performed by one daemon callback."""

        token = _BOUND_LEADER_LEASE.set(lease)
        try:
            yield
        finally:
            _BOUND_LEADER_LEASE.reset(token)

    @staticmethod
    def _assert_bound_lease_in_connection(conn: sqlite3.Connection, *, now: int) -> None:
        lease = _BOUND_LEADER_LEASE.get()
        if lease is None:
            return
        row = conn.execute(
            """
            SELECT 1 FROM operator_query_projection_leader
            WHERE lease_name = ? AND owner_id = ? AND fencing_token = ?
              AND lease_expires_ts_ms > ?
            """,
            (lease.lease_name, lease.owner_id, int(lease.fencing_token), now),
        ).fetchone()
        if row is None:
            raise QueryProjectionError(
                PROJECTION_LEASE_FENCED,
                "projection daemon leader fence is stale",
            )

    def get(self, kind: str, key: str) -> ProjectionDocument | None:
        projection_kind = self._required(kind, "kind")
        document_key = self._required(key, "key")
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM operator_query_projection_document
                WHERE projection_kind = ? AND document_key = ?
                """,
                (projection_kind, document_key),
            ).fetchone()
        if row is None:
            return None
        try:
            payload = json.loads(str(row["payload_json"]))
        except json.JSONDecodeError as exc:
            raise QueryProjectionError(PROJECTION_DATA_INVALID, "payload_json is invalid") from exc
        if not isinstance(payload, dict) or digest_json(payload) != str(row["payload_digest"]):
            raise QueryProjectionError(PROJECTION_DATA_INVALID, "projection payload digest mismatch")
        return ProjectionDocument(
            kind=projection_kind,
            key=document_key,
            payload=payload,
            projection_version=int(row["projection_version"]),
            projection_cursor=int(row["journal_cursor"]),
            source_cursor=int(row["source_cursor"]),
            source_digest=str(row["source_digest"]),
            payload_digest=str(row["payload_digest"]),
            freshness_ts_ms=int(row["freshness_ts_ms"]),
            degraded_state=ProjectionDegradedState(str(row["degraded_state"])),
            degraded_detail=row["degraded_detail"],
        )

    def list(self, kind: str, *, limit: int = 1000) -> list[ProjectionDocument]:
        projection_kind = self._required(kind, "kind")
        with self._connect() as conn:
            keys = conn.execute(
                """
                SELECT document_key FROM operator_query_projection_document
                WHERE projection_kind = ?
                ORDER BY freshness_ts_ms DESC, document_key ASC LIMIT ?
                """,
                (projection_kind, min(10_000, max(1, int(limit)))),
            ).fetchall()
        return [document for row in keys if (document := self.get(projection_kind, str(row[0]))) is not None]

    def journal_text_values(
        self,
        kind: str,
        key: str,
        field: str,
    ) -> frozenset[str]:
        """Return one text field across the complete verified journal history."""

        projection_kind = self._required(kind, "kind")
        document_key = self._required(key, "key")
        field_name = self._required(field, "field")
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT source_payload_json, source_digest
                FROM operator_query_projection_journal
                WHERE projection_kind = ? AND document_key = ?
                ORDER BY cursor ASC
                """,
                (projection_kind, document_key),
            ).fetchall()
        values: set[str] = set()
        for row in rows:
            payload = _decode_journal_payload(
                str(row["source_payload_json"]),
                expected_digest=str(row["source_digest"]),
            )
            value = payload.get(field_name)
            if isinstance(value, str) and value.strip():
                values.add(value.strip())
        return frozenset(values)

    def compare_and_upsert(
        self,
        kind: str,
        key: str,
        payload: Mapping[str, Any],
        *,
        expected_current: ProjectionDocument | None,
        source_cursor: int,
        source_digest: str | None = None,
        source_ref: str = "projection-worker",
        degraded_state: ProjectionDegradedState | str = ProjectionDegradedState.HEALTHY,
        degraded_detail: str | None = None,
        freshness_ts_ms: int | None = None,
    ) -> ProjectionDocument:
        """Atomically compare the current document and commit its successor."""

        return self.upsert(
            kind,
            key,
            payload,
            source_cursor=source_cursor,
            source_digest=source_digest,
            source_ref=source_ref,
            degraded_state=degraded_state,
            degraded_detail=degraded_detail,
            freshness_ts_ms=freshness_ts_ms,
            _expected_current=expected_current,
        )

    def upsert(
        self,
        kind: str,
        key: str,
        payload: Mapping[str, Any],
        *,
        source_cursor: int,
        source_digest: str | None = None,
        source_ref: str = "projection-worker",
        degraded_state: ProjectionDegradedState | str = ProjectionDegradedState.HEALTHY,
        degraded_detail: str | None = None,
        freshness_ts_ms: int | None = None,
        _expected_current: ProjectionDocument | None | object = _UNCONDITIONAL_CURRENT,
    ) -> ProjectionDocument:
        projection_kind = self._required(kind, "kind")
        document_key = self._required(key, "key")
        source_reference = self._required(source_ref, "source_ref")
        body = public_projection_payload(payload)
        payload_digest = digest_json(body)
        # Authority is the public materialized source, never the private input.
        # Callers may supply a digest for compatibility, but no raw/private
        # digest is persisted because it can become a correlation side channel.
        source_hash = digest_json(body)
        state = ProjectionDegradedState(degraded_state)
        public_degraded_detail = _public_degraded_detail(degraded_detail)
        cursor = max(0, int(source_cursor))
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            operation_now = max(0, int(self._clock()))
            freshness = max(
                0,
                int(
                    freshness_ts_ms
                    if freshness_ts_ms is not None
                    else operation_now
                ),
            )
            # The leader fence and journal record when this projection write
            # happened.  ``freshness_ts_ms`` is instead source evidence time;
            # using an old evidence timestamp for the lease comparison would
            # let an already-expired writer pass its fence.
            self._assert_bound_lease_in_connection(conn, now=operation_now)
            current = conn.execute(
                """
                SELECT projection_version, journal_cursor, source_cursor,
                       source_digest, payload_digest
                FROM operator_query_projection_document
                WHERE projection_kind = ? AND document_key = ?
                """,
                (projection_kind, document_key),
            ).fetchone()
            if _expected_current is not _UNCONDITIONAL_CURRENT:
                if _expected_current is None:
                    matches_expected = current is None
                elif isinstance(_expected_current, ProjectionDocument):
                    matches_expected = bool(
                        current is not None
                        and _expected_current.kind == projection_kind
                        and _expected_current.key == document_key
                        and int(current["projection_version"])
                        == int(_expected_current.projection_version)
                        and int(current["journal_cursor"])
                        == int(_expected_current.projection_cursor)
                        and int(current["source_cursor"])
                        == int(_expected_current.source_cursor)
                        and str(current["source_digest"])
                        == str(_expected_current.source_digest)
                        and str(current["payload_digest"])
                        == str(_expected_current.payload_digest)
                    )
                else:
                    raise TypeError(
                        "_expected_current must be ProjectionDocument or None"
                    )
                if not matches_expected:
                    raise QueryProjectionError(
                        PROJECTION_CURSOR_CONFLICT,
                        "projection current changed before atomic transition",
                    )
            if current is not None:
                current_cursor = int(current["source_cursor"])
                current_digest = str(current["source_digest"])
                if cursor < current_cursor:
                    raise QueryProjectionError(
                        PROJECTION_CURSOR_REGRESSION,
                        f"source cursor {cursor} is older than current cursor {current_cursor}",
                    )
                if cursor == current_cursor and source_hash != current_digest:
                    raise QueryProjectionError(
                        PROJECTION_CURSOR_CONFLICT,
                        "the current source cursor is already bound to a different digest",
                    )
            conn.execute(
                """
                INSERT OR IGNORE INTO operator_query_projection_journal(
                    schema_version, projection_kind, document_key, source_cursor,
                    source_digest, source_payload_json, source_ref, observed_ts_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    OPERATOR_PROJECTION_SCHEMA_VERSION,
                    projection_kind,
                    document_key,
                    cursor,
                    source_hash,
                    _encode_journal_payload(body, source_digest=source_hash),
                    source_reference,
                    operation_now,
                ),
            )
            journal = conn.execute(
                """
                SELECT cursor FROM operator_query_projection_journal
                WHERE projection_kind = ? AND document_key = ?
                  AND source_cursor = ? AND source_digest = ?
                """,
                (projection_kind, document_key, cursor, source_hash),
            ).fetchone()
            assert journal is not None
            journal_cursor = int(journal[0])
            unchanged = bool(
                current
                and str(current["source_digest"]) == source_hash
                and str(current["payload_digest"]) == payload_digest
            )
            version = int(current["projection_version"]) if unchanged else int(current["projection_version"] if current else 0) + 1
            conn.execute(
                """
                INSERT INTO operator_query_projection_document(
                    projection_kind, document_key, schema_version, projection_version,
                    journal_cursor, source_cursor, source_digest, payload_json,
                    payload_digest, freshness_ts_ms, degraded_state, degraded_detail,
                    updated_ts_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(projection_kind, document_key) DO UPDATE SET
                    schema_version = excluded.schema_version,
                    projection_version = excluded.projection_version,
                    journal_cursor = excluded.journal_cursor,
                    source_cursor = excluded.source_cursor,
                    source_digest = excluded.source_digest,
                    payload_json = excluded.payload_json,
                    payload_digest = excluded.payload_digest,
                    freshness_ts_ms = excluded.freshness_ts_ms,
                    degraded_state = excluded.degraded_state,
                    degraded_detail = excluded.degraded_detail,
                    updated_ts_ms = excluded.updated_ts_ms
                """,
                (
                    projection_kind,
                    document_key,
                    OPERATOR_PROJECTION_SCHEMA_VERSION,
                    version,
                    journal_cursor,
                    cursor,
                    source_hash,
                    canonical_json(body),
                    payload_digest,
                    freshness,
                    state.value,
                    public_degraded_detail,
                    operation_now,
                ),
            )
            state_digest = digest_json(
                {"key": document_key, "source_cursor": cursor, "source_digest": source_hash}
            )
            conn.execute(
                """
                INSERT INTO operator_query_projection_state(
                    projection_kind, schema_version, projection_version,
                    projection_cursor, source_cursor, source_digest,
                    freshness_ts_ms, degraded_state, degraded_detail,
                    rebuilt_ts_ms, updated_ts_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)
                ON CONFLICT(projection_kind) DO UPDATE SET
                    schema_version = excluded.schema_version,
                    projection_version = operator_query_projection_state.projection_version + excluded.projection_version,
                    projection_cursor = max(operator_query_projection_state.projection_cursor, excluded.projection_cursor),
                    source_cursor = max(operator_query_projection_state.source_cursor, excluded.source_cursor),
                    source_digest = excluded.source_digest,
                    freshness_ts_ms = excluded.freshness_ts_ms,
                    degraded_state = excluded.degraded_state,
                    degraded_detail = excluded.degraded_detail,
                    updated_ts_ms = excluded.updated_ts_ms
                """,
                (
                    projection_kind,
                    OPERATOR_PROJECTION_SCHEMA_VERSION,
                    0 if unchanged else 1,
                    journal_cursor,
                    cursor,
                    state_digest,
                    freshness,
                    state.value,
                    public_degraded_detail,
                    operation_now,
                ),
            )
            conn.commit()
        result = self.get(projection_kind, document_key)
        assert result is not None
        return result

    def mark_degraded(
        self,
        kind: str,
        key: str,
        *,
        state: ProjectionDegradedState | str,
        detail: str,
    ) -> ProjectionDocument | None:
        projection_kind = self._required(kind, "kind")
        document_key = self._required(key, "key")
        degraded = ProjectionDegradedState(state)
        if degraded is ProjectionDegradedState.HEALTHY:
            raise ValueError("mark_degraded requires a degraded state")
        public_detail = _public_degraded_detail(detail)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            now = max(0, int(self._clock()))
            self._assert_bound_lease_in_connection(conn, now=now)
            conn.execute(
                """
                UPDATE operator_query_projection_document
                SET degraded_state = ?, degraded_detail = ?, updated_ts_ms = ?
                WHERE projection_kind = ? AND document_key = ?
                """,
                (degraded.value, public_detail, now, projection_kind, document_key),
            )
            conn.execute(
                """
                UPDATE operator_query_projection_state
                SET degraded_state = ?, degraded_detail = ?, updated_ts_ms = ?
                WHERE projection_kind = ?
                """,
                (degraded.value, public_detail, now, projection_kind),
            )
        return self.get(projection_kind, document_key)

    def state(self, kind: str) -> dict[str, Any] | None:
        projection_kind = self._required(kind, "kind")
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM operator_query_projection_state WHERE projection_kind = ?",
                (projection_kind,),
            ).fetchone()
        return dict(row) if row is not None else None

    def rebuild(
        self,
        kind: str,
        documents: Iterable[tuple[str, Mapping[str, Any], int, str]],
        *,
        source_ref: str = "projection-rebuild",
        _expected_journal_cursor: int | None = None,
    ) -> dict[str, Any]:
        projection_kind = self._required(kind, "kind")
        source_reference = self._required(source_ref, "source_ref")
        prepared: list[tuple[str, dict[str, Any], int, str, str]] = []
        seen_keys: set[str] = set()
        # Materialize and sanitize before opening the write transaction.  A
        # source iterator failure therefore cannot disturb the last-good set.
        for raw_key, payload, source_cursor, _source_digest in documents:
            key = self._required(raw_key, "key")
            if key in seen_keys:
                raise QueryProjectionError(
                    PROJECTION_DATA_INVALID,
                    f"duplicate rebuild document key: {key}",
                )
            seen_keys.add(key)
            body = public_projection_payload(payload)
            source_hash = digest_json(body)
            prepared.append(
                (key, body, max(0, int(source_cursor)), source_hash, digest_json(body))
            )

        with self._connect() as conn:
            # DELETE + replacement + head switch are one SQLite transaction.
            # Concurrent readers retain the old committed snapshot until this
            # transaction commits; an exception rolls the entire shadow build
            # back and preserves the last-good generation.
            conn.execute("BEGIN IMMEDIATE")
            now = max(0, int(self._clock()))
            self._assert_bound_lease_in_connection(conn, now=now)
            if _expected_journal_cursor is not None:
                journal_head = int(
                    conn.execute(
                        """
                        SELECT COALESCE(MAX(cursor), 0)
                        FROM operator_query_projection_journal
                        WHERE projection_kind = ?
                        """,
                        (projection_kind,),
                    ).fetchone()[0]
                )
                if journal_head != int(_expected_journal_cursor):
                    raise QueryProjectionError(
                        PROJECTION_CURSOR_CONFLICT,
                        "projection journal advanced while rebuild snapshot was materialized",
                    )
            current_rows = {
                str(row["document_key"]): row
                for row in conn.execute(
                    """
                    SELECT document_key, projection_version, source_cursor,
                           source_digest, payload_digest
                    FROM operator_query_projection_document
                    WHERE projection_kind = ?
                    """,
                    (projection_kind,),
                ).fetchall()
            }
            current_state = conn.execute(
                """
                SELECT projection_version
                FROM operator_query_projection_state
                WHERE projection_kind = ?
                """,
                (projection_kind,),
            ).fetchone()
            shadow: list[tuple[str, dict[str, Any], int, str, str, int, int]] = []
            for key, body, source_cursor, source_hash, payload_digest in prepared:
                old = current_rows.get(key)
                if old is not None and source_cursor < int(old["source_cursor"]):
                    raise QueryProjectionError(
                        PROJECTION_CURSOR_REGRESSION,
                        f"rebuild source cursor {source_cursor} is older than current cursor {int(old['source_cursor'])}",
                    )
                conn.execute(
                    """
                    INSERT OR IGNORE INTO operator_query_projection_journal(
                        schema_version, projection_kind, document_key, source_cursor,
                        source_digest, source_payload_json, source_ref, observed_ts_ms
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        OPERATOR_PROJECTION_SCHEMA_VERSION,
                        projection_kind,
                        key,
                        source_cursor,
                        source_hash,
                        _encode_journal_payload(body, source_digest=source_hash),
                        source_reference,
                        now,
                    ),
                )
                journal = conn.execute(
                    """
                    SELECT cursor FROM operator_query_projection_journal
                    WHERE projection_kind = ? AND document_key = ?
                      AND source_cursor = ? AND source_digest = ?
                    """,
                    (projection_kind, key, source_cursor, source_hash),
                ).fetchone()
                assert journal is not None
                unchanged = bool(
                    old
                    and int(old["source_cursor"]) == source_cursor
                    and str(old["source_digest"]) == source_hash
                    and str(old["payload_digest"]) == payload_digest
                )
                version = int(old["projection_version"]) if unchanged else int(old["projection_version"] if old else 0) + 1
                shadow.append(
                    (key, body, source_cursor, source_hash, payload_digest, int(journal[0]), version)
                )

            conn.execute(
                "DELETE FROM operator_query_projection_document WHERE projection_kind = ?",
                (projection_kind,),
            )
            conn.execute(
                "DELETE FROM operator_query_projection_state WHERE projection_kind = ?",
                (projection_kind,),
            )
            for key, body, source_cursor, source_hash, payload_digest, journal_cursor, version in shadow:
                conn.execute(
                    """
                    INSERT INTO operator_query_projection_document(
                        projection_kind, document_key, schema_version, projection_version,
                        journal_cursor, source_cursor, source_digest, payload_json,
                        payload_digest, freshness_ts_ms, degraded_state, degraded_detail,
                        updated_ts_ms
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'HEALTHY', NULL, ?)
                    """,
                    (
                        projection_kind,
                        key,
                        OPERATOR_PROJECTION_SCHEMA_VERSION,
                        version,
                        journal_cursor,
                        source_cursor,
                        source_hash,
                        canonical_json(body),
                        payload_digest,
                        now,
                        now,
                    ),
                )

            projection_cursor = max((item[5] for item in shadow), default=0)
            max_source_cursor = max((item[2] for item in shadow), default=0)
            generation_digest = digest_json(
                [
                    {"key": item[0], "source_cursor": item[2], "source_digest": item[3]}
                    for item in sorted(shadow, key=lambda value: value[0])
                ]
            )
            generation_version = int(current_state[0] if current_state else 0) + 1
            conn.execute(
                """
                INSERT INTO operator_query_projection_state(
                    projection_kind, schema_version, projection_version,
                    projection_cursor, source_cursor, source_digest,
                    freshness_ts_ms, degraded_state, degraded_detail,
                    rebuilt_ts_ms, updated_ts_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'HEALTHY', NULL, ?, ?)
                """,
                (
                    projection_kind,
                    OPERATOR_PROJECTION_SCHEMA_VERSION,
                    generation_version,
                    projection_cursor,
                    max_source_cursor,
                    generation_digest,
                    now,
                    now,
                    now,
                ),
            )
            self._before_rebuild_commit(projection_kind, conn)
            conn.commit()
        return {"kind": projection_kind, "rebuilt_count": len(shadow), "rebuilt_ts_ms": now}

    def _before_rebuild_commit(self, kind: str, conn: sqlite3.Connection) -> None:
        """Fault-injection seam; production performs no work before the switch."""

        del kind, conn

    def reconcile(
        self,
        kind: str,
        documents: Iterable[tuple[str, Mapping[str, Any], int, str]],
        *,
        source_ref: str = "projection-reconcile",
    ) -> dict[str, Any]:
        projection_kind = self._required(kind, "kind")
        source: dict[str, tuple[dict[str, Any], int, str]] = {}
        for key, payload, cursor, _digest in documents:
            public_payload = public_projection_payload(payload)
            source[key] = (public_payload, int(cursor), digest_json(public_payload))
        current = {document.key: document for document in self.list(projection_kind, limit=10_000)}
        repaired = 0
        unchanged = 0
        for key, (payload, cursor, source_digest) in source.items():
            existing = current.get(key)
            if (
                existing is not None
                and existing.source_cursor == cursor
                and existing.source_digest == source_digest
                and existing.payload_digest == digest_json(payload)
            ):
                unchanged += 1
                continue
            self.upsert(
                projection_kind,
                key,
                payload,
                source_cursor=cursor,
                source_digest=source_digest,
                source_ref=source_ref,
            )
            repaired += 1
        missing_from_source = sorted(set(current) - set(source))
        for key in missing_from_source:
            self.mark_degraded(
                projection_kind,
                key,
                state=ProjectionDegradedState.DIGEST_MISMATCH,
                detail="projection key is absent from reconciled source",
            )
        return {
            "kind": projection_kind,
            "source_count": len(source),
            "repaired_count": repaired,
            "unchanged_count": unchanged,
            "orphaned_keys": missing_from_source,
        }

    def rebuild_from_journal(
        self,
        kind: str,
        *,
        source_ref: str = "projection-journal-rebuild",
    ) -> dict[str, Any]:
        """Rebuild the latest document per key from the immutable source journal."""

        projection_kind = self._required(kind, "kind")
        with self._connect() as conn:
            rows = conn.execute(
                """
                WITH kind_journal AS (
                    SELECT *
                    FROM operator_query_projection_journal
                    WHERE projection_kind = ?
                )
                SELECT j.*,
                       (SELECT COALESCE(MAX(cursor), 0) FROM kind_journal) AS rebuild_head
                FROM operator_query_projection_journal AS j
                WHERE j.projection_kind = ?
                  AND j.cursor = (
                      SELECT candidate.cursor
                      FROM kind_journal AS candidate
                      WHERE candidate.document_key = j.document_key
                      ORDER BY candidate.source_cursor DESC, candidate.cursor DESC
                      LIMIT 1
                  )
                ORDER BY j.document_key ASC
                """,
                (projection_kind, projection_kind),
            ).fetchall()
        journal_head = int(rows[0]["rebuild_head"]) if rows else 0
        sources: list[tuple[str, Mapping[str, Any], int, str]] = []
        for row in rows:
            payload = _decode_journal_payload(
                str(row["source_payload_json"]),
                expected_digest=str(row["source_digest"]),
            )
            sources.append(
                (
                    str(row["document_key"]),
                    payload,
                    int(row["source_cursor"]),
                    str(row["source_digest"]),
                )
            )
        return self.rebuild(
            projection_kind,
            sources,
            source_ref=source_ref,
            _expected_journal_cursor=journal_head,
        )


__all__ = [
    "PROJECTION_CURSOR_CONFLICT",
    "PROJECTION_CURSOR_REGRESSION",
    "PROJECTION_DATA_INVALID",
    "PROJECTION_LEASE_FENCED",
    "PROJECTION_SCHEMA_NOT_READY",
    "ProjectionLeaderLease",
    "QueryProjectionError",
    "QueryProjectionStore",
    "canonical_json",
    "digest_json",
    "public_projection_payload",
]
