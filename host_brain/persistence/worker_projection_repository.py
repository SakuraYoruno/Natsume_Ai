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
_IMMUTABILITY_TRIGGER_SQL = {
    "trg_task_runtime_worker_projection_no_update": """
        CREATE TRIGGER trg_task_runtime_worker_projection_no_update
        BEFORE UPDATE ON task_runtime_worker_projection
        BEGIN
            SELECT RAISE(ABORT, 'TASK_RUNTIME_WORKER_PROJECTION_IMMUTABLE');
        END
    """,
    "trg_task_runtime_worker_projection_no_delete": """
        CREATE TRIGGER trg_task_runtime_worker_projection_no_delete
        BEFORE DELETE ON task_runtime_worker_projection
        BEGIN
            SELECT RAISE(ABORT, 'TASK_RUNTIME_WORKER_PROJECTION_IMMUTABLE');
        END
    """,
}


class WorkerProjectionStoreError(RuntimeError):
    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = str(reason_code or WORKER_PROJECTION_INVALID)
        self.detail = str(detail or "worker projection store failed")
        super().__init__(f"{self.reason_code}: {self.detail}")


def _normalize_schema_sql(value: Any) -> str:
    return re.sub(r"[\s\"`\[\];]+", "", str(value or "").lower())


def validate_worker_projection_payload(
    projection: Mapping[str, Any],
    *,
    expected_projection_id: str | None = None,
) -> dict[str, Any]:
    """Validate one loaded projection exactly as a persisted Store record.

    ``ExecutionProjectionReaderPort`` implementations are allowed to sit in
    front of the SQLite store.  Consumers therefore must not trust a digest
    string returned by the reader without rebuilding the canonical v1 body.
    This helper returns only the normalized, digest-bound public projection;
    unknown fields and identity drift fail closed.
    """

    if not isinstance(projection, Mapping):
        raise WorkerProjectionStoreError(
            WORKER_PROJECTION_INVALID,
            "worker projection payload must be an object",
        )
    payload = dict(projection)
    projection_id = str(payload.get("projection_id") or "").strip()
    if not _DURABLE_REF.fullmatch(projection_id):
        raise WorkerProjectionStoreError(
            WORKER_PROJECTION_INVALID,
            "projection_id must be a durable reference",
        )
    expected_id = str(expected_projection_id or "").strip()
    if expected_id and projection_id != expected_id:
        raise WorkerProjectionStoreError(
            WORKER_PROJECTION_INVALID,
            "worker projection id differs from its committed receipt",
        )
    if payload.get("schema_version") != WORKER_PROJECTION_SCHEMA_VERSION:
        raise WorkerProjectionStoreError(
            WORKER_PROJECTION_INVALID,
            "worker projection schema version is invalid",
        )
    allowed_fields = {
        *_BODY_FIELDS,
        "projection_id",
        "payload_digest",
        "schema_version",
        *(
            key
            for key in _OPTIONAL_BODY_FIELDS
            if key in payload
        ),
    }
    if set(payload) != allowed_fields:
        raise WorkerProjectionStoreError(
            WORKER_PROJECTION_INVALID,
            "worker projection contains missing or unknown fields",
        )
    _assert_secret_free(payload, path="projection")
    body = _projection_body(payload)
    digest = "sha256:" + hashlib.sha256(
        _canonical_json(body).encode("utf-8")
    ).hexdigest()
    if payload.get("payload_digest") != digest or _SHA256.fullmatch(digest) is None:
        raise WorkerProjectionStoreError(
            WORKER_PROJECTION_INVALID,
            "worker projection payload digest differs",
        )
    normalized = {
        **body,
        "projection_id": projection_id,
        "payload_digest": digest,
        "schema_version": WORKER_PROJECTION_SCHEMA_VERSION,
    }
    _validate_body(normalized)
    return normalized


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
                "SELECT name, sql FROM sqlite_master WHERE type='table' AND name=?",
                (_TABLE,),
            ).fetchone()
            if row is None:
                raise WorkerProjectionStoreError(
                    WORKER_PROJECTION_SCHEMA_NOT_READY,
                    "worker projection migration 0086 is not applied",
                )
            columns = tuple(
                str(item["name"])
                for item in conn.execute(f"PRAGMA table_info({_TABLE})").fetchall()
            )
            expected_columns = (
                "projection_id",
                "schema_version",
                "payload_digest",
                "command_id",
                "request_id",
                "graph_id",
                "graph_revision",
                "node_id",
                "attempt_id",
                "route_type",
                "target_id",
                "decision",
                "projection_json",
                "created_ts_ms",
            )
            create_sql = re.sub(r"[\s\"`\[\]]+", "", str(row["sql"] or "").lower())
            route_guard = "check(route_typein('chat','skill','delegate','tool'))"
            required_indexes = {
                "idx_task_runtime_worker_projection_graph": (
                    "graph_id",
                    "graph_revision",
                    "node_id",
                ),
                "idx_task_runtime_worker_projection_request": (
                    "request_id",
                    "created_ts_ms",
                ),
            }
            indexes = {
                name: tuple(
                    str(item["name"])
                    for item in conn.execute(f"PRAGMA index_info({name})").fetchall()
                )
                for name in required_indexes
            }
            expected_triggers = {
                name: _normalize_schema_sql(sql)
                for name, sql in _IMMUTABILITY_TRIGGER_SQL.items()
            }
            triggers = {
                str(item["name"]): _normalize_schema_sql(item["sql"])
                for item in conn.execute(
                    """
                    SELECT name, sql FROM sqlite_master
                    WHERE type = 'trigger' AND tbl_name = ?
                    """,
                    (_TABLE,),
                ).fetchall()
            }
            if (
                columns != expected_columns
                or route_guard not in create_sql
                or indexes != required_indexes
                or triggers != expected_triggers
            ):
                raise WorkerProjectionStoreError(
                    WORKER_PROJECTION_SCHEMA_NOT_READY,
                    "worker projection migrations 0114/0127 are missing or structurally drifted",
                )
        finally:
            conn.close()

    def stage(self, *, projection_id: str, projection: Mapping[str, Any]) -> dict[str, Any]:
        created = int(self._clock())
        if created < 0:
            raise WorkerProjectionStoreError(
                WORKER_PROJECTION_INVALID,
                "clock returned a negative value",
            )
        with self._transaction() as conn:
            return self._stage_in_transaction(
                conn,
                projection_id=projection_id,
                projection=projection,
                created_ts_ms=created,
            )

    def _stage_in_transaction(
        self,
        conn: sqlite3.Connection,
        *,
        projection_id: str,
        projection: Mapping[str, Any],
        created_ts_ms: int,
    ) -> dict[str, Any]:
        """Stage one projection inside the caller-owned canonical UoW.

        This private hook is the sole physical ``INSERT`` authority for worker
        projections.  It deliberately neither begins nor commits a
        transaction so a composition-owned UoW can atomically bind a recovered
        checkpoint to the Task ledger.
        """

        if not isinstance(conn, sqlite3.Connection) or not conn.in_transaction:
            raise WorkerProjectionStoreError(
                WORKER_PROJECTION_INVALID,
                "worker projection connection hook requires an active transaction",
            )
        database = conn.execute("PRAGMA database_list").fetchone()
        connected_path = "" if database is None else str(database[2] or "")
        if not connected_path or Path(connected_path).resolve() != self.db_path.resolve():
            raise WorkerProjectionStoreError(
                WORKER_PROJECTION_INVALID,
                "worker projection connection differs from the bound canonical database",
            )
        projection_ref = str(projection_id or "").strip()
        if not _DURABLE_REF.fullmatch(projection_ref):
            raise WorkerProjectionStoreError(
                WORKER_PROJECTION_INVALID,
                "projection_id must be a durable reference",
            )
        normalized = validate_worker_projection_payload(
            projection,
            expected_projection_id=projection_ref,
        )
        digest = normalized["payload_digest"]
        encoded = _canonical_json(normalized)
        created = int(created_ts_ms)
        if created < 0:
            raise WorkerProjectionStoreError(
                WORKER_PROJECTION_INVALID,
                "created_ts_ms must be non-negative",
            )
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
        normalized = validate_worker_projection_payload(
            value,
            expected_projection_id=str(row["projection_id"]),
        )
        column_bindings = {
            "schema_version": WORKER_PROJECTION_SCHEMA_VERSION,
            "payload_digest": normalized["payload_digest"],
            "command_id": normalized["command_id"],
            "request_id": normalized["request_id"],
            "graph_id": normalized["graph_id"],
            "graph_revision": normalized["graph_revision"],
            "node_id": normalized["node_id"],
            "attempt_id": normalized["attempt_id"],
            "route_type": normalized["route_type"],
            "target_id": normalized["target_id"],
            "decision": normalized["decision"],
        }
        if any(row[key] != value for key, value in column_bindings.items()):
            raise WorkerProjectionStoreError(
                WORKER_PROJECTION_INVALID,
                "persisted worker projection columns/payload drifted",
            )
        return normalized


def validate_worker_projection_record(
    row: sqlite3.Row | Mapping[str, Any],
) -> dict[str, Any]:
    """Validate a durable projection row for same-transaction consumers."""

    return WorkerProjectionStore._record(row)  # noqa: SLF001 - module-owned validator


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
    if str(value.get("route_type")) not in {"chat", "skill", "delegate", "tool"}:
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
    "validate_worker_projection_payload",
    "validate_worker_projection_record",
    "WORKER_PROJECTION_SCHEMA_VERSION",
    "WorkerProjectionStore",
    "WorkerProjectionStoreError",
]
