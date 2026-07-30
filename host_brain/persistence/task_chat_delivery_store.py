"""Durable, exact-replay sentence visibility for governed Chat nodes.

The Task Runtime owns visible pre-acceptance text.  A sentence is first bound
to one immutable graph/node/attempt/lease/grant tuple in this ledger, and only
then may the worker pass it to the SSE caller.  The ledger deliberately does
not write Life, Memory, a channel outbox, or an external device: those remain
Acceptance-owned effects.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any

from host_brain.core.ids import now_ms


TASK_CHAT_DELIVERY_SCHEMA_VERSION = "Natsume.task_runtime.chat_delivery.v1"
TASK_CHAT_DELIVERY_INVALID = "TASK_CHAT_DELIVERY_INVALID"
TASK_CHAT_DELIVERY_SCHEMA_NOT_READY = "TASK_CHAT_DELIVERY_SCHEMA_NOT_READY"
TASK_CHAT_DELIVERY_FENCE_LOST = "TASK_CHAT_DELIVERY_FENCE_LOST"
TASK_CHAT_DELIVERY_REPLAY_DRIFT = "TASK_CHAT_DELIVERY_REPLAY_DRIFT"
TASK_CHAT_DELIVERY_NOT_FOUND = "TASK_CHAT_DELIVERY_NOT_FOUND"

_MIGRATION_ID = 149
_MIGRATION_NAME = "task_runtime_chat_delivery"
_TABLE = "task_runtime_chat_delivery_event"
_EVENT_KINDS = frozenset(
    {
        "PLAN_FROZEN",
        "SENTENCE_STAGED",
        "STREAM_COMPLETED",
        "STREAM_INTERRUPTED",
        "STREAM_CANCELLED",
    }
)
_COLUMNS = (
    "event_id",
    "schema_version",
    "event_kind",
    "graph_id",
    "graph_revision",
    "node_id",
    "attempt_id",
    "lease_id",
    "lease_owner_id",
    "fencing_token",
    "node_grant_ref",
    "request_id",
    "session_id",
    "principal_id",
    "self_id",
    "relationship_subject_id",
    "workspace_id",
    "plan_id",
    "plan_digest",
    "synchronization_id",
    "self_affect_revision",
    "sentence_index",
    "sent_sentence_count",
    "sentence_text",
    "sentence_digest",
    "reason_code",
    "payload_json",
    "payload_digest",
    "created_ts_ms",
)
_TRIGGERS = frozenset(
    {
        "trg_task_runtime_chat_delivery_identity_guard",
        "trg_task_runtime_chat_delivery_no_update",
        "trg_task_runtime_chat_delivery_no_delete",
    }
)


class TaskChatDeliveryStoreError(RuntimeError):
    """Stable failure that never exposes a sentence or SQLite detail."""

    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = str(reason_code or TASK_CHAT_DELIVERY_INVALID)
        self.detail = str(detail or "task chat delivery is invalid")
        super().__init__(f"{self.reason_code}: {self.detail}")


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(
        dict(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _required_text(value: Any, field_name: str, *, maximum: int = 1024) -> str:
    text = str(value or "").strip()
    if not text or len(text) > maximum:
        raise TaskChatDeliveryStoreError(
            TASK_CHAT_DELIVERY_INVALID,
            f"{field_name} is required and bounded",
        )
    return text


def _non_negative_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool):
        raise TaskChatDeliveryStoreError(
            TASK_CHAT_DELIVERY_INVALID, f"{field_name} must be an integer"
        )
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise TaskChatDeliveryStoreError(
            TASK_CHAT_DELIVERY_INVALID, f"{field_name} must be an integer"
        ) from exc
    if parsed < 0:
        raise TaskChatDeliveryStoreError(
            TASK_CHAT_DELIVERY_INVALID, f"{field_name} must be non-negative"
        )
    return parsed


def _hex_digest(value: Any, field_name: str) -> str:
    text = str(value or "").strip().lower()
    if text.startswith("sha256:"):
        text = text.split(":", 1)[1]
    if len(text) != 64 or any(char not in "0123456789abcdef" for char in text):
        raise TaskChatDeliveryStoreError(
            TASK_CHAT_DELIVERY_INVALID, f"{field_name} must be sha256 hex"
        )
    return text


@dataclass(frozen=True, slots=True)
class TaskChatDeliveryContext:
    """Exact authority tuple supplied only by a live governed Chat worker."""

    graph_id: str
    graph_revision: int
    node_id: str
    attempt_id: str
    lease_id: str
    lease_owner_id: str
    fencing_token: int
    node_grant_ref: str
    request_id: str
    session_id: str
    principal_id: str
    self_id: str
    relationship_subject_id: str
    workspace_id: str
    plan_id: str
    plan_digest: str
    synchronization_id: str
    self_affect_revision: int

    def __post_init__(self) -> None:
        for field_name, maximum in (
            ("graph_id", 240),
            ("node_id", 240),
            ("attempt_id", 240),
            ("lease_id", 240),
            ("lease_owner_id", 240),
            ("node_grant_ref", 1024),
            ("request_id", 240),
            ("session_id", 240),
            ("principal_id", 240),
            ("relationship_subject_id", 240),
            ("workspace_id", 240),
            ("plan_id", 512),
            ("synchronization_id", 512),
        ):
            object.__setattr__(
                self, field_name, _required_text(getattr(self, field_name), field_name, maximum=maximum)
            )
        object.__setattr__(
            self, "graph_revision", _non_negative_int(self.graph_revision, "graph_revision")
        )
        if self.graph_revision < 1:
            raise TaskChatDeliveryStoreError(
                TASK_CHAT_DELIVERY_INVALID, "graph_revision must be positive"
            )
        object.__setattr__(
            self, "fencing_token", _non_negative_int(self.fencing_token, "fencing_token")
        )
        if self.fencing_token < 1:
            raise TaskChatDeliveryStoreError(
                TASK_CHAT_DELIVERY_INVALID, "fencing_token must be positive"
            )
        object.__setattr__(
            self,
            "self_affect_revision",
            _non_negative_int(self.self_affect_revision, "self_affect_revision"),
        )
        if str(self.self_id or "").strip() != "natsume_ai":
            raise TaskChatDeliveryStoreError(
                TASK_CHAT_DELIVERY_INVALID,
                "self_id must be natsume_ai",
            )
        object.__setattr__(self, "self_id", "natsume_ai")
        object.__setattr__(self, "plan_digest", _hex_digest(self.plan_digest, "plan_digest"))

    def identity_payload(self) -> dict[str, Any]:
        return {
            "graph_id": self.graph_id,
            "graph_revision": self.graph_revision,
            "node_id": self.node_id,
            "attempt_id": self.attempt_id,
            "lease_id": self.lease_id,
            "lease_owner_id": self.lease_owner_id,
            "fencing_token": self.fencing_token,
            "node_grant_ref": self.node_grant_ref,
            "request_id": self.request_id,
            "session_id": self.session_id,
            "principal_id": self.principal_id,
            "self_id": self.self_id,
            "relationship_subject_id": self.relationship_subject_id,
            "workspace_id": self.workspace_id,
            "plan_id": self.plan_id,
            "plan_digest": self.plan_digest,
            "synchronization_id": self.synchronization_id,
            "self_affect_revision": self.self_affect_revision,
        }


class TaskChatDeliveryStore:
    """SQLite owner for immutable pre-acceptance governed Chat delivery events."""

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
            raise TaskChatDeliveryStoreError(
                TASK_CHAT_DELIVERY_INVALID,
                "task chat delivery requires a file-backed canonical database",
            )
        if not callable(clock):
            raise TaskChatDeliveryStoreError(
                TASK_CHAT_DELIVERY_INVALID, "clock must be callable"
            )
        self.db_path = Path(raw_path).expanduser().resolve()
        self._clock = clock
        self._timeout_ms = max(1, int(timeout_ms))
        if verify_schema:
            self.assert_schema_ready()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            str(self.db_path), timeout=self._timeout_ms / 1000.0, isolation_level=None
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
        try:
            with self._connect() as conn:
                migration = conn.execute(
                    "SELECT name FROM schema_migrations WHERE migration_id = ?",
                    (_MIGRATION_ID,),
                ).fetchone()
                columns = tuple(
                    str(row["name"])
                    for row in conn.execute(f"PRAGMA table_info({_TABLE})").fetchall()
                )
                triggers = frozenset(
                    str(row["name"])
                    for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name=?",
                        (_TABLE,),
                    ).fetchall()
                )
        except sqlite3.Error as exc:
            raise TaskChatDeliveryStoreError(
                TASK_CHAT_DELIVERY_SCHEMA_NOT_READY,
                "task chat delivery schema cannot be inspected",
            ) from exc
        if (
            migration is None
            or str(migration["name"] or "") != _MIGRATION_NAME
            or columns != _COLUMNS
            or triggers != _TRIGGERS
        ):
            raise TaskChatDeliveryStoreError(
                TASK_CHAT_DELIVERY_SCHEMA_NOT_READY,
                "migration 0149 immutable task chat delivery schema is unavailable",
            )

    def stage_plan(self, context: TaskChatDeliveryContext) -> dict[str, Any]:
        return self._append(
            context=context,
            event_kind="PLAN_FROZEN",
            sentence_index=-1,
            sent_sentence_count=0,
            sentence_text="",
            sentence_digest="",
            reason_code="",
            require_live_lease=True,
        )

    def stage_sentence(
        self,
        context: TaskChatDeliveryContext,
        *,
        sentence: str,
        sentence_index: int,
    ) -> dict[str, Any]:
        text = str(sentence or "").strip()
        index = _non_negative_int(sentence_index, "sentence_index")
        if not text or len(text) > 2_000:
            raise TaskChatDeliveryStoreError(
                TASK_CHAT_DELIVERY_INVALID, "sentence must be non-empty and at most 2000 chars"
            )
        return self._append(
            context=context,
            event_kind="SENTENCE_STAGED",
            sentence_index=index,
            sent_sentence_count=0,
            sentence_text=text,
            sentence_digest=_sha256(text),
            reason_code="",
            require_live_lease=True,
        )

    def record_stream_control(
        self,
        context: TaskChatDeliveryContext,
        *,
        event_kind: str,
        sent_sentence_count: int,
        reason_code: str = "",
    ) -> dict[str, Any]:
        kind = str(event_kind or "").strip().upper()
        if kind not in {"STREAM_COMPLETED", "STREAM_INTERRUPTED", "STREAM_CANCELLED"}:
            raise TaskChatDeliveryStoreError(
                TASK_CHAT_DELIVERY_INVALID, "stream control kind is invalid"
            )
        count = _non_negative_int(sent_sentence_count, "sent_sentence_count")
        reason = str(reason_code or "").strip().upper()
        if len(reason) > 160:
            raise TaskChatDeliveryStoreError(
                TASK_CHAT_DELIVERY_INVALID, "reason_code exceeds 160 chars"
            )
        return self._append(
            context=context,
            event_kind=kind,
            sentence_index=-1,
            sent_sentence_count=count,
            sentence_text="",
            sentence_digest="",
            reason_code=reason,
            # The interrupt path may run after the caller has revoked the
            # Lease.  Exact identity is still verified by the insert trigger.
            require_live_lease=False,
        )

    def list_sentences_for_request(
        self,
        *,
        request_id: str,
        session_id: str,
        principal_id: str,
        workspace_id: str,
        after_sentence_id: str = "",
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        request = _required_text(request_id, "request_id", maximum=240)
        session = _required_text(session_id, "session_id", maximum=240)
        principal = _required_text(principal_id, "principal_id", maximum=240)
        workspace = _required_text(workspace_id, "workspace_id", maximum=240)
        cursor = str(after_sentence_id or "").strip()
        bounded_limit = max(1, min(int(limit), 1_000))
        try:
            with self._connect() as conn:
                after_index = -1
                if cursor:
                    cursor_row = conn.execute(
                        f"""
                        SELECT sentence_index
                        FROM {_TABLE}
                        WHERE event_kind = 'SENTENCE_STAGED'
                          AND event_id = ?
                          AND request_id = ?
                          AND session_id = ?
                          AND principal_id = ?
                          AND workspace_id = ?
                        """,
                        (cursor, request, session, principal, workspace),
                    ).fetchone()
                    if cursor_row is None:
                        raise TaskChatDeliveryStoreError(
                            TASK_CHAT_DELIVERY_NOT_FOUND,
                            "replay cursor is not visible to this exact request scope",
                        )
                    after_index = int(cursor_row["sentence_index"])
                rows = conn.execute(
                    f"""
                    SELECT {','.join(_COLUMNS)}
                    FROM {_TABLE}
                    WHERE event_kind = 'SENTENCE_STAGED'
                      AND request_id = ?
                      AND session_id = ?
                      AND principal_id = ?
                      AND workspace_id = ?
                      AND sentence_index > ?
                    ORDER BY sentence_index ASC, event_id ASC
                    LIMIT ?
                    """,
                    (request, session, principal, workspace, after_index, bounded_limit),
                ).fetchall()
        except sqlite3.Error as exc:
            raise TaskChatDeliveryStoreError(
                TASK_CHAT_DELIVERY_SCHEMA_NOT_READY,
                "task chat delivery events could not be replayed",
            ) from exc
        return [self._public_sentence(dict(row), replayed=True) for row in rows]

    def list_attempt_events(
        self,
        context: TaskChatDeliveryContext,
        *,
        limit: int = 1_000,
    ) -> list[dict[str, Any]]:
        bounded_limit = max(1, min(int(limit), 1_000))
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    f"""
                    SELECT {','.join(_COLUMNS)}
                    FROM {_TABLE}
                    WHERE graph_id = ? AND graph_revision = ? AND node_id = ?
                      AND attempt_id = ?
                    ORDER BY CASE event_kind WHEN 'PLAN_FROZEN' THEN 0
                                              WHEN 'SENTENCE_STAGED' THEN 1
                                              ELSE 2 END,
                             sentence_index ASC, created_ts_ms ASC, event_id ASC
                    LIMIT ?
                    """,
                    (
                        context.graph_id,
                        context.graph_revision,
                        context.node_id,
                        context.attempt_id,
                        bounded_limit,
                    ),
                ).fetchall()
        except sqlite3.Error as exc:
            raise TaskChatDeliveryStoreError(
                TASK_CHAT_DELIVERY_SCHEMA_NOT_READY,
                "task chat delivery attempt could not be reopened",
            ) from exc
        return [self._public_event(dict(row), replayed=True) for row in rows]

    def _append(
        self,
        *,
        context: TaskChatDeliveryContext,
        event_kind: str,
        sentence_index: int,
        sent_sentence_count: int,
        sentence_text: str,
        sentence_digest: str,
        reason_code: str,
        require_live_lease: bool,
    ) -> dict[str, Any]:
        if not isinstance(context, TaskChatDeliveryContext):
            raise TaskChatDeliveryStoreError(
                TASK_CHAT_DELIVERY_INVALID, "context must be TaskChatDeliveryContext"
            )
        kind = str(event_kind or "").strip().upper()
        if kind not in _EVENT_KINDS:
            raise TaskChatDeliveryStoreError(
                TASK_CHAT_DELIVERY_INVALID, "event kind is invalid"
            )
        payload = {
            "schema_version": TASK_CHAT_DELIVERY_SCHEMA_VERSION,
            "event_kind": kind,
            **context.identity_payload(),
            "sentence_index": int(sentence_index),
            "sent_sentence_count": int(sent_sentence_count),
            "sentence_text": str(sentence_text or ""),
            "sentence_digest": str(sentence_digest or ""),
            "reason_code": str(reason_code or ""),
        }
        payload_json = _canonical_json(payload)
        payload_digest = _sha256(payload_json)
        event_id = "taskchat://" + _sha256(
            _canonical_json(
                {
                    "graph_id": context.graph_id,
                    "graph_revision": context.graph_revision,
                    "node_id": context.node_id,
                    "attempt_id": context.attempt_id,
                    "event_kind": kind,
                    "sentence_index": int(sentence_index),
                }
            )
        )
        now_value = max(0, int(self._clock()))
        try:
            with self._transaction() as conn:
                existing = conn.execute(
                    f"SELECT {','.join(_COLUMNS)} FROM {_TABLE} WHERE event_id = ?",
                    (event_id,),
                ).fetchone()
                if existing is not None:
                    return self._reopen_exact(
                        dict(existing), expected_payload=payload, event_id=event_id
                    )
                self._assert_context_identity(
                    conn, context=context, require_live_lease=require_live_lease, now_value=now_value
                )
                row = {
                    "event_id": event_id,
                    "schema_version": TASK_CHAT_DELIVERY_SCHEMA_VERSION,
                    "event_kind": kind,
                    **context.identity_payload(),
                    "sentence_index": int(sentence_index),
                    "sent_sentence_count": int(sent_sentence_count),
                    "sentence_text": str(sentence_text or ""),
                    "sentence_digest": str(sentence_digest or ""),
                    "reason_code": str(reason_code or ""),
                    "payload_json": payload_json,
                    "payload_digest": payload_digest,
                    "created_ts_ms": now_value,
                }
                conn.execute(
                    f"INSERT INTO {_TABLE} ({','.join(_COLUMNS)}) "
                    f"VALUES ({','.join('?' for _ in _COLUMNS)})",
                    tuple(row[column] for column in _COLUMNS),
                )
        except TaskChatDeliveryStoreError:
            raise
        except sqlite3.IntegrityError as exc:
            raise TaskChatDeliveryStoreError(
                TASK_CHAT_DELIVERY_FENCE_LOST,
                "task chat delivery could not bind the current attempt fence",
            ) from exc
        except sqlite3.Error as exc:
            raise TaskChatDeliveryStoreError(
                TASK_CHAT_DELIVERY_SCHEMA_NOT_READY,
                "task chat delivery event could not be persisted",
            ) from exc
        return self._public_event(row, replayed=False)

    @staticmethod
    def _assert_context_identity(
        conn: sqlite3.Connection,
        *,
        context: TaskChatDeliveryContext,
        require_live_lease: bool,
        now_value: int,
    ) -> None:
        row = conn.execute(
            """
            SELECT attempt.status AS attempt_status,
                   node.status AS node_status,
                   lease.status AS lease_status,
                   lease.owner_id AS lease_owner_id,
                   lease.fencing_token AS fencing_token,
                   lease.expires_ts_ms AS expires_ts_ms
            FROM task_runtime_attempt AS attempt
            JOIN task_runtime_node AS node
              ON node.graph_id = attempt.graph_id
             AND node.graph_revision = attempt.graph_revision
             AND node.node_id = attempt.node_id
            JOIN task_runtime_lease AS lease
              ON lease.lease_id = ?
             AND lease.attempt_id = attempt.attempt_id
            WHERE attempt.attempt_id = ?
              AND attempt.graph_id = ?
              AND attempt.graph_revision = ?
              AND attempt.node_id = ?
            """,
            (
                context.lease_id,
                context.attempt_id,
                context.graph_id,
                context.graph_revision,
                context.node_id,
            ),
        ).fetchone()
        if row is None:
            raise TaskChatDeliveryStoreError(
                TASK_CHAT_DELIVERY_FENCE_LOST,
                "task chat delivery identity has no exact durable attempt and lease",
            )
        if (
            str(row["lease_owner_id"] or "") != context.lease_owner_id
            or int(row["fencing_token"] or 0) != context.fencing_token
        ):
            raise TaskChatDeliveryStoreError(
                TASK_CHAT_DELIVERY_FENCE_LOST,
                "task chat delivery lease owner or fencing token differs",
            )
        if require_live_lease and (
            str(row["attempt_status"] or "").upper() != "RUNNING"
            or str(row["node_status"] or "").upper() != "RUNNING"
            or str(row["lease_status"] or "").upper() != "ACTIVE"
            or int(row["expires_ts_ms"] or 0) < now_value
        ):
            raise TaskChatDeliveryStoreError(
                TASK_CHAT_DELIVERY_FENCE_LOST,
                "task chat delivery lease is no longer live",
            )

    @staticmethod
    def _reopen_exact(
        row: Mapping[str, Any],
        *,
        expected_payload: Mapping[str, Any],
        event_id: str,
    ) -> dict[str, Any]:
        raw_payload = str(row.get("payload_json") or "")
        try:
            parsed = json.loads(raw_payload)
        except (TypeError, ValueError) as exc:
            raise TaskChatDeliveryStoreError(
                TASK_CHAT_DELIVERY_REPLAY_DRIFT,
                "existing task chat delivery payload is unreadable",
            ) from exc
        if (
            not isinstance(parsed, Mapping)
            or dict(parsed) != dict(expected_payload)
            or str(row.get("payload_digest") or "") != _sha256(_canonical_json(dict(expected_payload)))
        ):
            raise TaskChatDeliveryStoreError(
                TASK_CHAT_DELIVERY_REPLAY_DRIFT,
                "task chat delivery id is already bound to different content",
            )
        return TaskChatDeliveryStore._public_event(dict(row), replayed=True)

    @staticmethod
    def _public_sentence(row: Mapping[str, Any], *, replayed: bool) -> dict[str, Any]:
        return {
            "phase": "SENTENCE",
            "sentence": str(row["sentence_text"]),
            "sentence_index": int(row["sentence_index"]),
            "delivery_id": str(row["event_id"]),
            "delivery_status": "TASK_NODE_SENTENCE_STAGED",
            "replayed": bool(replayed),
            "request_id": str(row["request_id"]),
            "session_id": str(row["session_id"]),
            "principal_id": str(row["principal_id"]),
            "self_id": str(row["self_id"]),
            "relationship_subject_id": str(row["relationship_subject_id"]),
            "workspace_id": str(row["workspace_id"]),
            "graph_id": str(row["graph_id"]),
            "graph_revision": int(row["graph_revision"]),
            "node_id": str(row["node_id"]),
            "attempt_id": str(row["attempt_id"]),
            # These are not bearer credentials.  They are the immutable
            # provenance of the already-staged sentence and let every
            # downstream boundary prove that a stale worker cannot smuggle
            # text into a newer node execution.
            "lease_id": str(row["lease_id"]),
            "lease_owner_id": str(row["lease_owner_id"]),
            "fencing_token": int(row["fencing_token"]),
            "node_grant_ref": str(row["node_grant_ref"]),
            "plan_id": str(row["plan_id"]),
            "plan_digest": str(row["plan_digest"]),
            "synchronization_id": str(row["synchronization_id"]),
            "self_affect_revision": int(row["self_affect_revision"]),
        }

    @classmethod
    def _public_event(cls, row: Mapping[str, Any], *, replayed: bool) -> dict[str, Any]:
        if str(row.get("event_kind") or "") == "SENTENCE_STAGED":
            return cls._public_sentence(row, replayed=replayed)
        return {
            "event_id": str(row["event_id"]),
            "event_kind": str(row["event_kind"]),
            "delivery_status": "TASK_NODE_STREAM_RECORDED",
            "replayed": bool(replayed),
            "request_id": str(row["request_id"]),
            "graph_id": str(row["graph_id"]),
            "graph_revision": int(row["graph_revision"]),
            "node_id": str(row["node_id"]),
            "attempt_id": str(row["attempt_id"]),
            "sent_sentence_count": int(row["sent_sentence_count"]),
            "reason_code": str(row["reason_code"]),
            "plan_id": str(row["plan_id"]),
            "synchronization_id": str(row["synchronization_id"]),
            "self_affect_revision": int(row["self_affect_revision"]),
        }


__all__ = [
    "TASK_CHAT_DELIVERY_FENCE_LOST",
    "TASK_CHAT_DELIVERY_INVALID",
    "TASK_CHAT_DELIVERY_NOT_FOUND",
    "TASK_CHAT_DELIVERY_REPLAY_DRIFT",
    "TASK_CHAT_DELIVERY_SCHEMA_NOT_READY",
    "TASK_CHAT_DELIVERY_SCHEMA_VERSION",
    "TaskChatDeliveryContext",
    "TaskChatDeliveryStore",
    "TaskChatDeliveryStoreError",
]
