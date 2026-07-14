"""Durable, idempotent public projections emitted by governed workers."""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from collections.abc import Callable, Iterator, Mapping
from typing import Any

from host_brain.core.ids import now_ms


WORKER_PROJECTION_SCHEMA_VERSION = "Natsume.task_runtime.worker_projection.v1"
WORKER_PROJECTION_INVALID = "WORKER_PROJECTION_INVALID"
WORKER_PROJECTION_NOT_FOUND = "WORKER_PROJECTION_NOT_FOUND"
WORKER_PROJECTION_REPLAY_DRIFT = "WORKER_PROJECTION_REPLAY_DRIFT"
WORKER_PROJECTION_SCHEMA_NOT_READY = "WORKER_PROJECTION_SCHEMA_NOT_READY"

_TABLE = "task_runtime_worker_projection"
_DURABLE_REF = re.compile(
    r"^[a-z][a-z0-9+.-]{1,31}://[A-Za-z0-9][A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]{0,1023}$"
)
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_SENSITIVE_NON_TOKEN = re.compile(
    r"(?:^|_)(?:api_?key|authorization|bearer|cookie|credential|password|"
    r"private_?key|raw_?auth|secret|signature)(?:$|_)",
    re.IGNORECASE,
)
_TOKEN_FIELD = re.compile(r"(?:^|_)tokens?(?:$|_)", re.IGNORECASE)
_SAFE_TOKEN_METADATA = re.compile(
    r"^(?:"
    r"token_(?:count|budget|limit|usage)|"
    r"(?:prompt|completion|input|output|total|cached|reasoning|actual|estimated|budget|context)_tokens?|"
    r"(?:max|min)(?:_context)?_tokens|"
    r"(?:enforce_)?context_token_budget|context_token_estimator|token_budget_tokens|"
    r"(?:first_token|time_to_first_token|stream_after_first_token)(?:_latency)?_ms"
    r")$",
    re.IGNORECASE,
)
_BODY_FIELDS = (
    "phase",
    "done",
    "decision",
    "reason_code",
    "command_id",
    "request_id",
    "narrative",
    "route_type",
    "target_id",
    "graph_id",
    "graph_revision",
    "node_id",
    "attempt_id",
    "node_grant_ref",
    "audit_refs",
)
_OPTIONAL_BODY_FIELDS = (
    "acceptance_effect_intents",
    "budget_usage",
)


class WorkerProjectionStoreError(RuntimeError):
    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = str(reason_code or WORKER_PROJECTION_INVALID)
        self.detail = str(detail or "worker projection store failed")
        super().__init__(f"{self.reason_code}: {self.detail}")


class WorkerProjectionStore:
    """SQLite projection store shared by writer and terminal replay reader."""

    def __init__(
        self,
        db_path: str | Path,
        *,
        clock: Callable[[], int] = now_ms,
        timeout_ms: int = 5_000,
        verify_schema: bool = True,
    ) -> None:
        raw = str(db_path or "").strip()
        if not raw or raw == ":memory:" or raw.lower().startswith("file:"):
            raise WorkerProjectionStoreError(
                WORKER_PROJECTION_INVALID,
                "WorkerProjectionStore requires a durable sqlite file path",
            )
        self.db_path = Path(raw)
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

    def assert_schema_ready(self) -> None:
        if not self.db_path.exists():
            raise WorkerProjectionStoreError(
                WORKER_PROJECTION_SCHEMA_NOT_READY,
                f"runtime database does not exist: {self.db_path}",
            )
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (_TABLE,),
            ).fetchone()
            if row is None:
                raise WorkerProjectionStoreError(
                    WORKER_PROJECTION_SCHEMA_NOT_READY,
                    "worker projection migration 0086 is not applied",
                )
        finally:
            conn.close()

    def stage(self, *, projection_id: str, projection: Mapping[str, Any]) -> dict[str, Any]:
        projection_ref = str(projection_id or "").strip()
        if not _DURABLE_REF.fullmatch(projection_ref):
            raise WorkerProjectionStoreError(
                WORKER_PROJECTION_INVALID,
                "projection_id must be a durable reference",
            )
        payload = dict(projection or {})
        if payload.get("projection_id") != projection_ref:
            raise WorkerProjectionStoreError(
                WORKER_PROJECTION_INVALID,
                "projection payload id differs from projection_id",
            )
        if payload.get("schema_version") != WORKER_PROJECTION_SCHEMA_VERSION:
            raise WorkerProjectionStoreError(
                WORKER_PROJECTION_INVALID,
                "worker projection schema version is invalid",
            )
        _assert_secret_free(payload, path="projection")
        body = _projection_body(payload)
        digest = "sha256:" + hashlib.sha256(_canonical_json(body).encode("utf-8")).hexdigest()
        if payload.get("payload_digest") != digest or not _SHA256.fullmatch(digest):
            raise WorkerProjectionStoreError(
                WORKER_PROJECTION_INVALID,
                "worker projection payload digest differs",
            )
        normalized = {
            **body,
            "projection_id": projection_ref,
            "payload_digest": digest,
            "schema_version": WORKER_PROJECTION_SCHEMA_VERSION,
        }
        _validate_body(normalized)
        encoded = _canonical_json(normalized)
        created = int(self._clock())
        if created < 0:
            raise WorkerProjectionStoreError(WORKER_PROJECTION_INVALID, "clock returned a negative value")
        with self._transaction() as conn:
            existing = conn.execute(
                f"SELECT * FROM {_TABLE} WHERE projection_id = ?",
                (projection_ref,),
            ).fetchone()
            if existing is not None:
                record = self._record(existing)
                if record != normalized:
                    raise WorkerProjectionStoreError(
                        WORKER_PROJECTION_REPLAY_DRIFT,
                        "worker projection replay differs",
                    )
                return record
            try:
                conn.execute(
                    f"""
                    INSERT INTO {_TABLE}(
                        projection_id, schema_version, payload_digest, command_id,
                        request_id, graph_id, graph_revision, node_id, attempt_id,
                        route_type, target_id, decision, projection_json, created_ts_ms
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        projection_ref,
                        WORKER_PROJECTION_SCHEMA_VERSION,
                        digest,
                        normalized["command_id"],
                        normalized["request_id"],
                        normalized["graph_id"],
                        normalized["graph_revision"],
                        normalized["node_id"],
                        normalized["attempt_id"],
                        normalized["route_type"],
                        normalized["target_id"],
                        normalized["decision"],
                        encoded,
                        created,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise WorkerProjectionStoreError(
                    WORKER_PROJECTION_REPLAY_DRIFT,
                    "attempt already owns a different worker projection",
                ) from exc
            return normalized

    def get(self, projection_id: str) -> dict[str, Any]:
        conn = self._connect()
        try:
            row = conn.execute(
                f"SELECT * FROM {_TABLE} WHERE projection_id = ?",
                (str(projection_id or "").strip(),),
            ).fetchone()
            if row is None:
                raise WorkerProjectionStoreError(
                    WORKER_PROJECTION_NOT_FOUND,
                    f"worker projection not found: {projection_id}",
                )
            return self._record(row)
        finally:
            conn.close()

    def load(self, receipt_ref: str) -> Mapping[str, Any] | None:
        """ExecutionProjectionReaderPort spelling used by the coordinator."""

        try:
            return self.get(receipt_ref)
        except WorkerProjectionStoreError as exc:
            if exc.reason_code == WORKER_PROJECTION_NOT_FOUND:
                return None
            raise

    @staticmethod
    def _record(row: sqlite3.Row) -> dict[str, Any]:
        try:
            value = json.loads(str(row["projection_json"]))
        except (json.JSONDecodeError, TypeError) as exc:
            raise WorkerProjectionStoreError(
                WORKER_PROJECTION_INVALID,
                "persisted worker projection JSON is invalid",
            ) from exc
        if not isinstance(value, dict):
            raise WorkerProjectionStoreError(
                WORKER_PROJECTION_INVALID,
                "persisted worker projection must be an object",
            )
        _validate_body(value)
        body = _projection_body(value)
        expected_digest = "sha256:" + hashlib.sha256(_canonical_json(body).encode("utf-8")).hexdigest()
        if (
            value.get("schema_version") != WORKER_PROJECTION_SCHEMA_VERSION
            or value.get("projection_id") != row["projection_id"]
            or value.get("payload_digest") != row["payload_digest"]
            or value.get("payload_digest") != expected_digest
        ):
            raise WorkerProjectionStoreError(
                WORKER_PROJECTION_INVALID,
                "persisted worker projection columns/payload drifted",
            )
        _assert_secret_free(value, path="projection")
        return value


def _validate_body(value: Mapping[str, Any]) -> None:
    required = (
        "projection_id",
        "payload_digest",
        "command_id",
        "request_id",
        "graph_id",
        "node_id",
        "attempt_id",
        "route_type",
        "target_id",
        "decision",
    )
    if any(not str(value.get(key) or "").strip() for key in required):
        raise WorkerProjectionStoreError(WORKER_PROJECTION_INVALID, "worker projection is incomplete")
    if value.get("phase") != "COMMIT" or value.get("done") is not True:
        raise WorkerProjectionStoreError(WORKER_PROJECTION_INVALID, "projection is not terminal COMMIT")
    if str(value.get("decision")) not in {"APPROVE", "REJECT"}:
        raise WorkerProjectionStoreError(WORKER_PROJECTION_INVALID, "projection decision is invalid")
    if str(value.get("route_type")) not in {"chat", "skill", "delegate"}:
        raise WorkerProjectionStoreError(WORKER_PROJECTION_INVALID, "projection route is invalid")
    try:
        revision = int(value.get("graph_revision"))
    except (TypeError, ValueError) as exc:
        raise WorkerProjectionStoreError(WORKER_PROJECTION_INVALID, "graph_revision is invalid") from exc
    if revision < 1:
        raise WorkerProjectionStoreError(WORKER_PROJECTION_INVALID, "graph_revision is invalid")
    if not isinstance(value.get("audit_refs"), Mapping):
        raise WorkerProjectionStoreError(WORKER_PROJECTION_INVALID, "audit_refs must be an object")
    if "acceptance_effect_intents" in value:
        effects = value.get("acceptance_effect_intents")
        if not isinstance(effects, list) or any(not isinstance(item, Mapping) for item in effects):
            raise WorkerProjectionStoreError(
                WORKER_PROJECTION_INVALID,
                "acceptance_effect_intents must be a list of objects",
            )
    if "budget_usage" in value and not isinstance(value.get("budget_usage"), Mapping):
        raise WorkerProjectionStoreError(
            WORKER_PROJECTION_INVALID,
            "budget_usage must be an object",
        )


def _assert_secret_free(value: Any, *, path: str) -> None:
    if isinstance(value, Mapping):
        for raw_key, child in value.items():
            key = str(raw_key)
            # node_grant_ref is an opaque durable authority reference, not a
            # bearer token.  It is the only explicit authority field allowed.
            if key != "node_grant_ref" and _is_sensitive_key(key):
                raise WorkerProjectionStoreError(
                    WORKER_PROJECTION_INVALID,
                    f"sensitive field is forbidden: {path}.{key}",
                )
            _assert_secret_free(child, path=f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _assert_secret_free(child, path=f"{path}[{index}]")


def _is_sensitive_key(key: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "_", str(key or "").strip().lower()).strip("_")
    if _SENSITIVE_NON_TOKEN.search(normalized):
        return True
    if _TOKEN_FIELD.search(normalized):
        # Token counts, budgets and first-token latency are public execution
        # metadata.  Every other token-shaped key remains fail-closed.
        return _SAFE_TOKEN_METADATA.fullmatch(normalized) is None
    return False


def _canonical_json(value: Mapping[str, Any]) -> str:
    try:
        return json.dumps(
            dict(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise WorkerProjectionStoreError(WORKER_PROJECTION_INVALID, "projection is not JSON-safe") from exc


def _projection_body(value: Mapping[str, Any]) -> dict[str, Any]:
    body = {key: value.get(key) for key in _BODY_FIELDS}
    # v1 projections created before worker-side budget/effect accounting do not
    # contain these optional fields.  Include them in the digest only when they
    # are present so those durable records remain readable.
    for key in _OPTIONAL_BODY_FIELDS:
        if key in value:
            body[key] = value.get(key)
    return body


__all__ = [
    "WORKER_PROJECTION_INVALID",
    "WORKER_PROJECTION_NOT_FOUND",
    "WORKER_PROJECTION_REPLAY_DRIFT",
    "WORKER_PROJECTION_SCHEMA_NOT_READY",
    "WORKER_PROJECTION_SCHEMA_VERSION",
    "WorkerProjectionStore",
    "WorkerProjectionStoreError",
]
