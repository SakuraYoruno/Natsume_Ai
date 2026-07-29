"""Durable pre-acceptance text receipts for governed Chat streaming.

The Task Runtime remains the authority for execution and acceptance.  This
store only gives a complete, locally gated assistant sentence an immutable
identity before it is exposed to a live client.  It deliberately does not
write Digital Life state, invoke an Outbox, or claim an external delivery.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from typing import Any

from host_brain.core.ids import now_ms


GOVERNED_CHAT_STREAM_RECEIPT_INVALID = "GOVERNED_CHAT_STREAM_RECEIPT_INVALID"
GOVERNED_CHAT_STREAM_RECEIPT_REPLAY_DRIFT = (
    "GOVERNED_CHAT_STREAM_RECEIPT_REPLAY_DRIFT"
)
GOVERNED_CHAT_STREAM_RECEIPT_SCHEMA_NOT_READY = (
    "GOVERNED_CHAT_STREAM_RECEIPT_SCHEMA_NOT_READY"
)

_TABLE = "task_runtime_governed_chat_stream_receipt"
_SCHEMA_VERSION = "Natsume.task_runtime.governed_chat_stream_receipt.v1"
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_HEX = re.compile(r"^[0-9a-f]{64}$")


class GovernedChatStreamReceiptStoreError(RuntimeError):
    """Fail-closed error with an operator-visible stable code."""

    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = str(reason_code or GOVERNED_CHAT_STREAM_RECEIPT_INVALID)
        self.detail = str(detail or "governed chat stream receipt failed")
        super().__init__(f"{self.reason_code}: {self.detail}")


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
        raise GovernedChatStreamReceiptStoreError(
            GOVERNED_CHAT_STREAM_RECEIPT_INVALID,
            "stream receipt payload must be finite JSON",
        ) from exc


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _required(value: Any, field_name: str, *, maximum: int = 240) -> str:
    text = str(value or "").strip()
    if not text or len(text) > maximum:
        raise GovernedChatStreamReceiptStoreError(
            GOVERNED_CHAT_STREAM_RECEIPT_INVALID,
            f"{field_name} is required and must be at most {maximum} chars",
        )
    return text


def _non_negative_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool):
        raise GovernedChatStreamReceiptStoreError(
            GOVERNED_CHAT_STREAM_RECEIPT_INVALID,
            f"{field_name} must be a non-negative integer",
        )
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise GovernedChatStreamReceiptStoreError(
            GOVERNED_CHAT_STREAM_RECEIPT_INVALID,
            f"{field_name} must be a non-negative integer",
        ) from exc
    if parsed < 0:
        raise GovernedChatStreamReceiptStoreError(
            GOVERNED_CHAT_STREAM_RECEIPT_INVALID,
            f"{field_name} must be a non-negative integer",
        )
    return parsed


class GovernedChatStreamReceiptStore:
    """SQLite writer/replay reader for migration 0149's immutable receipts."""

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
            raise GovernedChatStreamReceiptStoreError(
                GOVERNED_CHAT_STREAM_RECEIPT_INVALID,
                "GovernedChatStreamReceiptStore requires a durable SQLite file path",
            )
        if not callable(clock):
            raise GovernedChatStreamReceiptStoreError(
                GOVERNED_CHAT_STREAM_RECEIPT_INVALID,
                "clock must be callable",
            )
        self.db_path = Path(raw_path).expanduser().resolve()
        self._clock = clock
        self._timeout_ms = max(1, int(timeout_ms))
        if verify_schema:
            self.assert_schema_ready()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            str(self.db_path),
            timeout=self._timeout_ms / 1000.0,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(f"PRAGMA busy_timeout = {self._timeout_ms}")
        return connection

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def assert_schema_ready(self) -> None:
        if not self.db_path.exists():
            raise GovernedChatStreamReceiptStoreError(
                GOVERNED_CHAT_STREAM_RECEIPT_SCHEMA_NOT_READY,
                f"runtime database does not exist: {self.db_path}",
            )
        connection = self._connect()
        try:
            table = connection.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                (_TABLE,),
            ).fetchone()
            if table is None:
                raise GovernedChatStreamReceiptStoreError(
                    GOVERNED_CHAT_STREAM_RECEIPT_SCHEMA_NOT_READY,
                    "governed chat stream receipt migration 0149 is not applied",
                )
            columns = {
                str(row["name"])
                for row in connection.execute(f"PRAGMA table_info({_TABLE})").fetchall()
            }
            expected = {
                "receipt_id",
                "graph_id",
                "graph_revision",
                "node_id",
                "attempt_id",
                "sentence_index",
                "request_id",
                "principal_id",
                "workspace_id",
                "session_id",
                "affective_turn_plan_id",
                "synchronization_id",
                "self_affect_revision",
                "sentence",
                "sentence_digest",
                "node_grant_ref",
                "node_grant_digest",
                "payload_digest",
                "staged_at_ms",
            }
            if columns != expected:
                raise GovernedChatStreamReceiptStoreError(
                    GOVERNED_CHAT_STREAM_RECEIPT_SCHEMA_NOT_READY,
                    "governed chat stream receipt schema columns drift",
                )
            triggers = {
                str(row["name"])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name=?",
                    (_TABLE,),
                ).fetchall()
            }
            required_triggers = {
                "trg_task_runtime_governed_chat_stream_receipt_no_update",
                "trg_task_runtime_governed_chat_stream_receipt_no_delete",
            }
            if not required_triggers.issubset(triggers):
                raise GovernedChatStreamReceiptStoreError(
                    GOVERNED_CHAT_STREAM_RECEIPT_SCHEMA_NOT_READY,
                    "governed chat stream receipt immutability triggers are missing",
                )
        except sqlite3.DatabaseError as exc:
            raise GovernedChatStreamReceiptStoreError(
                GOVERNED_CHAT_STREAM_RECEIPT_SCHEMA_NOT_READY,
                "cannot inspect governed chat stream receipt schema",
            ) from exc
        finally:
            connection.close()

    def stage(self, *, receipt: Mapping[str, Any]) -> dict[str, Any]:
        """Insert/reopen one exact sentence receipt before client visibility."""

        normalized = self._normalize(receipt)
        with self._transaction() as connection:
            try:
                connection.execute(
                    f"""
                    INSERT OR IGNORE INTO {_TABLE}(
                        receipt_id, graph_id, graph_revision, node_id, attempt_id,
                        sentence_index, request_id, principal_id, workspace_id,
                        session_id, affective_turn_plan_id, synchronization_id,
                        self_affect_revision, sentence, sentence_digest,
                        node_grant_ref, node_grant_digest, payload_digest, staged_at_ms
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    tuple(normalized[field] for field in self._columns()),
                )
            except sqlite3.DatabaseError as exc:
                raise GovernedChatStreamReceiptStoreError(
                    GOVERNED_CHAT_STREAM_RECEIPT_INVALID,
                    "cannot stage governed chat stream receipt",
                ) from exc
            row = connection.execute(
                f"""
                SELECT * FROM {_TABLE}
                WHERE graph_id=? AND graph_revision=? AND node_id=?
                  AND attempt_id=? AND sentence_index=?
                """,
                (
                    normalized["graph_id"],
                    normalized["graph_revision"],
                    normalized["node_id"],
                    normalized["attempt_id"],
                    normalized["sentence_index"],
                ),
            ).fetchone()
            if row is None:
                raise GovernedChatStreamReceiptStoreError(
                    GOVERNED_CHAT_STREAM_RECEIPT_INVALID,
                    "stream receipt insert did not return a canonical row",
                )
            durable = dict(row)
            identity_fields = tuple(
                field for field in self._columns() if field != "staged_at_ms"
            )
            if any(durable[field] != normalized[field] for field in identity_fields):
                raise GovernedChatStreamReceiptStoreError(
                    GOVERNED_CHAT_STREAM_RECEIPT_REPLAY_DRIFT,
                    "stream sentence replay differs from its immutable task receipt",
                )
            return durable

    def list_for_attempt(
        self,
        *,
        graph_id: str,
        graph_revision: int,
        node_id: str,
        attempt_id: str,
    ) -> tuple[dict[str, Any], ...]:
        """Read exact staged sentences in their durable visible order."""

        connection = self._connect()
        try:
            rows = connection.execute(
                f"""
                SELECT * FROM {_TABLE}
                WHERE graph_id=? AND graph_revision=? AND node_id=? AND attempt_id=?
                ORDER BY sentence_index ASC
                """,
                (
                    _required(graph_id, "graph_id"),
                    _non_negative_int(graph_revision, "graph_revision"),
                    _required(node_id, "node_id"),
                    _required(attempt_id, "attempt_id"),
                ),
            ).fetchall()
            return tuple(dict(row) for row in rows)
        finally:
            connection.close()

    @staticmethod
    def _columns() -> tuple[str, ...]:
        return (
            "receipt_id",
            "graph_id",
            "graph_revision",
            "node_id",
            "attempt_id",
            "sentence_index",
            "request_id",
            "principal_id",
            "workspace_id",
            "session_id",
            "affective_turn_plan_id",
            "synchronization_id",
            "self_affect_revision",
            "sentence",
            "sentence_digest",
            "node_grant_ref",
            "node_grant_digest",
            "payload_digest",
            "staged_at_ms",
        )

    def _normalize(self, receipt: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(receipt, Mapping):
            raise GovernedChatStreamReceiptStoreError(
                GOVERNED_CHAT_STREAM_RECEIPT_INVALID,
                "stream receipt must be a mapping",
            )
        raw = dict(receipt)
        if raw.get("schema_version") not in (None, _SCHEMA_VERSION):
            raise GovernedChatStreamReceiptStoreError(
                GOVERNED_CHAT_STREAM_RECEIPT_INVALID,
                "stream receipt schema version is invalid",
            )
        graph_revision = _non_negative_int(raw.get("graph_revision"), "graph_revision")
        if graph_revision < 1:
            raise GovernedChatStreamReceiptStoreError(
                GOVERNED_CHAT_STREAM_RECEIPT_INVALID,
                "graph_revision must be positive",
            )
        sentence_index = _non_negative_int(raw.get("sentence_index"), "sentence_index")
        self_affect_revision = _non_negative_int(
            raw.get("self_affect_revision"),
            "self_affect_revision",
        )
        sentence = _required(raw.get("sentence"), "sentence", maximum=2_000)
        sentence_digest = _sha256({"sentence": sentence})
        supplied_sentence_digest = str(raw.get("sentence_digest") or "").strip().lower()
        if supplied_sentence_digest and supplied_sentence_digest != sentence_digest:
            raise GovernedChatStreamReceiptStoreError(
                GOVERNED_CHAT_STREAM_RECEIPT_REPLAY_DRIFT,
                "stream sentence digest differs from its content",
            )
        grant_digest = str(raw.get("node_grant_digest") or "").strip().lower()
        if _SHA256.fullmatch(grant_digest) is None:
            raise GovernedChatStreamReceiptStoreError(
                GOVERNED_CHAT_STREAM_RECEIPT_INVALID,
                "node_grant_digest must be a sha256 reference",
            )
        body = {
            "schema_version": _SCHEMA_VERSION,
            "graph_id": _required(raw.get("graph_id"), "graph_id"),
            "graph_revision": graph_revision,
            "node_id": _required(raw.get("node_id"), "node_id"),
            "attempt_id": _required(raw.get("attempt_id"), "attempt_id"),
            "sentence_index": sentence_index,
            "request_id": _required(raw.get("request_id"), "request_id"),
            "principal_id": _required(raw.get("principal_id"), "principal_id"),
            "workspace_id": _required(raw.get("workspace_id"), "workspace_id"),
            "session_id": _required(raw.get("session_id"), "session_id"),
            "affective_turn_plan_id": _required(
                raw.get("affective_turn_plan_id"),
                "affective_turn_plan_id",
            ),
            "synchronization_id": _required(
                raw.get("synchronization_id"),
                "synchronization_id",
            ),
            "self_affect_revision": self_affect_revision,
            "sentence": sentence,
            "sentence_digest": sentence_digest,
            "node_grant_ref": _required(
                raw.get("node_grant_ref"),
                "node_grant_ref",
                maximum=1_024,
            ),
            "node_grant_digest": grant_digest,
        }
        payload_digest = "sha256:" + _sha256(body)
        receipt_id = (
            "governed-chat-stream://"
            + "/".join(
                (
                    hashlib.sha256(body["graph_id"].encode("utf-8")).hexdigest()[:24],
                    str(body["graph_revision"]),
                    hashlib.sha256(body["node_id"].encode("utf-8")).hexdigest()[:24],
                    hashlib.sha256(body["attempt_id"].encode("utf-8")).hexdigest()[:24],
                    str(sentence_index),
                    payload_digest.removeprefix("sha256:"),
                )
            )
        )
        supplied_receipt_id = str(raw.get("receipt_id") or "").strip()
        if supplied_receipt_id and supplied_receipt_id != receipt_id:
            raise GovernedChatStreamReceiptStoreError(
                GOVERNED_CHAT_STREAM_RECEIPT_REPLAY_DRIFT,
                "stream receipt id differs from its immutable payload",
            )
        supplied_payload_digest = str(raw.get("payload_digest") or "").strip().lower()
        if supplied_payload_digest and supplied_payload_digest != payload_digest:
            raise GovernedChatStreamReceiptStoreError(
                GOVERNED_CHAT_STREAM_RECEIPT_REPLAY_DRIFT,
                "stream receipt digest differs from its immutable payload",
            )
        try:
            staged_at_ms = int(self._clock())
        except (TypeError, ValueError) as exc:
            raise GovernedChatStreamReceiptStoreError(
                GOVERNED_CHAT_STREAM_RECEIPT_INVALID,
                "clock returned an invalid timestamp",
            ) from exc
        if staged_at_ms < 0:
            raise GovernedChatStreamReceiptStoreError(
                GOVERNED_CHAT_STREAM_RECEIPT_INVALID,
                "clock returned a negative timestamp",
            )
        return {
            "receipt_id": receipt_id,
            **body,
            "payload_digest": payload_digest,
            "staged_at_ms": staged_at_ms,
        }


__all__ = [
    "GOVERNED_CHAT_STREAM_RECEIPT_INVALID",
    "GOVERNED_CHAT_STREAM_RECEIPT_REPLAY_DRIFT",
    "GOVERNED_CHAT_STREAM_RECEIPT_SCHEMA_NOT_READY",
    "GovernedChatStreamReceiptStore",
    "GovernedChatStreamReceiptStoreError",
]
