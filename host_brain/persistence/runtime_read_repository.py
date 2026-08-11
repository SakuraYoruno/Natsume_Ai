"""Read-only semantic queries used by production runtime orchestration."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager, nullcontext
import json
from pathlib import Path
import sqlite3
from typing import Any
from urllib.parse import quote

from host_brain.audit.ledger_constants import JSON_FIELDS_BY_TABLE


RUNTIME_READ_INVALID = "RUNTIME_READ_INVALID"
RUNTIME_READ_UNAVAILABLE = "RUNTIME_READ_UNAVAILABLE"
RUNTIME_READ_TABLE_NOT_ALLOWED = "RUNTIME_READ_TABLE_NOT_ALLOWED"
_MAX_TS = 9_223_372_036_854_775_807

_LEDGER_TIME_QUERIES = {
    "channel_io_log": (
        "SELECT * FROM channel_io_log "
        "WHERE ts_ms >= ? AND ts_ms <= ? ORDER BY ts_ms DESC LIMIT ?"
    ),
    "intent_log": (
        "SELECT * FROM intent_log "
        "WHERE ts_ms >= ? AND ts_ms <= ? ORDER BY ts_ms DESC LIMIT ?"
    ),
    "memory_ops_log": (
        "SELECT * FROM memory_ops_log "
        "WHERE ts_ms >= ? AND ts_ms <= ? ORDER BY ts_ms DESC LIMIT ?"
    ),
}
APPROVED_LEDGER_TIME_TABLES = frozenset(_LEDGER_TIME_QUERIES)


class RuntimeReadRepositoryError(RuntimeError):
    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = str(reason_code or RUNTIME_READ_UNAVAILABLE)
        self.detail = str(detail or "runtime read failed")
        super().__init__(f"{self.reason_code}: {self.detail}")


class RuntimeReadRepository:
    """Operation-scoped, query-only access to central runtime schemas."""

    def __init__(
        self,
        db_path: str | Path,
        *,
        timeout_ms: int = 3_000,
        _borrowed_connection: sqlite3.Connection | None = None,
        _borrowed_lock: Any | None = None,
        _public_ledger: Any | None = None,
    ) -> None:
        raw_path = str(db_path or "").strip()
        self._public_ledger = _public_ledger
        self._timeout_ms = max(1, int(timeout_ms))
        self._borrowed_connection = _borrowed_connection
        self._borrowed_lock = _borrowed_lock
        if self._public_ledger is not None and not raw_path:
            self.db_path = ""
            return
        if not raw_path or raw_path.lower().startswith("file:"):
            raise RuntimeReadRepositoryError(
                RUNTIME_READ_INVALID,
                "db_path must be a database file path or a bound :memory: ledger",
            )
        if raw_path == ":memory:" and _borrowed_connection is None:
            raise RuntimeReadRepositoryError(
                RUNTIME_READ_UNAVAILABLE,
                "an in-memory runtime reader requires the owning ledger connection",
            )
        self.db_path = (
            raw_path if raw_path == ":memory:" else str(Path(raw_path).expanduser().resolve())
        )
        if self._borrowed_connection is None and not Path(self.db_path).is_file():
            raise RuntimeReadRepositoryError(
                RUNTIME_READ_UNAVAILABLE,
                f"runtime database does not exist: {self.db_path}",
            )

    @classmethod
    def from_ledger(cls, ledger: Any, *, timeout_ms: int = 3_000) -> "RuntimeReadRepository":
        canonical_path = str(
            getattr(ledger, "canonical_db_path", "")
            or getattr(ledger, "db_path", "")
            or ""
        ).strip()
        if not canonical_path:
            return cls("", timeout_ms=timeout_ms, _public_ledger=ledger)
        if canonical_path != ":memory:":
            return cls(canonical_path, timeout_ms=timeout_ms)
        connection = getattr(ledger, "_conn", None)
        if connection is None:
            return cls("", timeout_ms=timeout_ms, _public_ledger=ledger)
        return cls(
            ":memory:",
            timeout_ms=timeout_ms,
            _borrowed_connection=connection,
            _borrowed_lock=getattr(ledger, "_write_lock", None),
        )

    @classmethod
    def from_settings(
        cls,
        settings: Any,
        *,
        timeout_ms: int = 3_000,
    ) -> "RuntimeReadRepository":
        db_path = str(getattr(settings, "canonical_db_path", "") or "").strip()
        return cls(db_path, timeout_ms=timeout_ms)

    def close(self) -> None:
        """No-op: every file read owns and closes its operation connection."""

    def recent_user_turn_contents(
        self,
        *,
        person_id: str,
        session_id: str | None,
        request_id: str | None,
        limit: int,
    ) -> list[str]:
        person_key = _required_text(person_id, "person_id")
        session_key = str(session_id or "").strip()
        request_key = str(request_id or "").strip()
        row_limit = _limit(limit, maximum=500)
        if self._public_ledger is not None:
            rows = self._public_table_rows("conversation_turn")
            boundary_ts_ms: int | None = None
            if session_key and request_key:
                boundary_rows = sorted(
                    (
                        row
                        for row in rows
                        if str(row.get("session_id") or "").strip() == session_key
                        and str(row.get("request_id") or "").strip() == request_key
                        and str(row.get("role") or "").strip() == "user"
                    ),
                    key=lambda row: _safe_int(row.get("turn_index")),
                )
                if boundary_rows:
                    boundary_ts_ms = _safe_int(boundary_rows[0].get("ts_ms"))
            selected = [
                row
                for row in rows
                if str(row.get("person_id") or "").strip() == person_key
                and str(row.get("role") or "").strip() == "user"
                and (
                    boundary_ts_ms is None
                    or boundary_ts_ms <= 0
                    or _safe_int(row.get("ts_ms")) <= boundary_ts_ms
                )
            ]
            selected.sort(
                key=lambda row: _safe_int(row.get("turn_index")),
                reverse=True,
            )
            return [str(row.get("content") or "").strip() for row in selected[:row_limit]]
        with self._read_operation() as conn:
            boundary_ts_ms: int | None = None
            if session_key and request_key:
                boundary = conn.execute(
                    """
                    SELECT ts_ms FROM conversation_turn
                    WHERE session_id = ? AND request_id = ? AND role = ?
                    ORDER BY turn_index ASC LIMIT ?
                    """,
                    (session_key, request_key, "user", 1),
                ).fetchone()
                if boundary is not None:
                    boundary_ts_ms = int(boundary["ts_ms"] or 0)
            if boundary_ts_ms is not None and boundary_ts_ms > 0:
                rows = conn.execute(
                    """
                    SELECT content FROM conversation_turn
                    WHERE person_id = ? AND role = ? AND ts_ms <= ?
                    ORDER BY ts_ms DESC LIMIT ?
                    """,
                    (person_key, "user", boundary_ts_ms, row_limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT content FROM conversation_turn
                    WHERE person_id = ? AND role = ?
                    ORDER BY ts_ms DESC LIMIT ?
                    """,
                    (person_key, "user", row_limit),
                ).fetchall()
        return [str(row["content"] or "").strip() for row in rows]

    def recent_conversation_turns_before_request(
        self,
        *,
        session_id: str,
        request_id: str | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        session_key = _required_text(session_id, "session_id")
        request_key = str(request_id or "").strip()
        row_limit = _limit(limit, maximum=20)
        if self._public_ledger is not None:
            rows = self._public_table_rows("conversation_turn")
            boundary_turn_index: int | None = None
            if request_key:
                boundary_rows = sorted(
                    (
                        row
                        for row in rows
                        if str(row.get("session_id") or "").strip() == session_key
                        and str(row.get("request_id") or "").strip() == request_key
                        and str(row.get("role") or "").strip() == "user"
                    ),
                    key=lambda row: _safe_int(row.get("turn_index")),
                )
                if boundary_rows:
                    boundary_turn_index = _safe_int(
                        boundary_rows[0].get("turn_index")
                    )
            selected = [
                dict(row)
                for row in rows
                if str(row.get("session_id") or "").strip() == session_key
                and (
                    boundary_turn_index is None
                    or _safe_int(row.get("turn_index")) < boundary_turn_index
                )
            ]
            selected.sort(key=lambda row: _safe_int(row.get("ts_ms")), reverse=True)
            return selected[:row_limit]
        with self._read_operation() as conn:
            boundary_turn_index: int | None = None
            if request_key:
                boundary = conn.execute(
                    """
                    SELECT turn_index FROM conversation_turn
                    WHERE session_id = ? AND request_id = ? AND role = ?
                    ORDER BY turn_index ASC LIMIT ?
                    """,
                    (session_key, request_key, "user", 1),
                ).fetchone()
                if boundary is not None:
                    boundary_turn_index = int(boundary["turn_index"] or 0)
            if boundary_turn_index is not None:
                rows = conn.execute(
                    """
                    SELECT request_id, role, content, ts_ms, turn_index, metadata FROM conversation_turn
                    WHERE session_id = ? AND turn_index < ?
                    ORDER BY turn_index DESC, ts_ms DESC LIMIT ?
                    """,
                    (session_key, boundary_turn_index, row_limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT request_id, role, content, ts_ms, turn_index, metadata FROM conversation_turn
                    WHERE session_id = ?
                    ORDER BY turn_index DESC, ts_ms DESC LIMIT ?
                    """,
                    (session_key, row_limit),
                ).fetchall()
        return [dict(row) for row in rows]

    def recent_committed_surface_replies_before_request(
        self,
        *,
        session_id: str,
        request_id: str | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        """Read replies that became Gateway-visible before the conversation writer caught up.

        ``surface.reply_delivery=COMMITTED`` is the durable visibility boundary.  The
        asynchronous conversation projection can trail it briefly, so a fast user
        follow-up must supplement (and later de-duplicate) ``conversation_turn``
        with the immutable accepted node artifact instead of forgetting the reply
        the user has just received.
        """

        session_key = _required_text(session_id, "session_id")
        request_key = str(request_id or "").strip()
        row_limit = _limit(limit, maximum=20)
        if self._public_ledger is not None:
            committed = {
                (
                    str(row.get("graph_id") or "").strip(),
                    _safe_int(row.get("graph_revision")),
                    str(row.get("node_id") or "").strip(),
                ): _safe_int(row.get("committed_ts_ms") or row.get("updated_ts_ms"))
                for row in self._public_table_rows(
                    "task_runtime_acceptance_effect_intent"
                )
                if str(row.get("effect_type") or "").strip()
                == "surface.reply_delivery"
                and str(row.get("status") or "").strip().upper() == "COMMITTED"
            }
            replies: list[dict[str, Any]] = []
            for row in self._public_table_rows("task_runtime_node_artifact"):
                key = (
                    str(row.get("graph_id") or "").strip(),
                    _safe_int(row.get("graph_revision")),
                    str(row.get("node_id") or "").strip(),
                )
                if key not in committed:
                    continue
                payload = _json_mapping(row.get("payload_json"))
                if str(payload.get("decision") or "").strip().upper() != "APPROVE":
                    continue
                artifact_request_id = str(payload.get("request_id") or "").strip()
                if request_key and artifact_request_id == request_key:
                    continue
                if _surface_artifact_session_id(payload) != session_key:
                    continue
                content = str(payload.get("narrative") or "").strip()
                if not content:
                    continue
                replies.append(
                    {
                        "request_id": artifact_request_id,
                        "role": "assistant",
                        "content": content,
                        "ts_ms": committed[key]
                        or _safe_int(row.get("created_ts_ms")),
                        "turn_index": -1,
                        "metadata": "{}",
                    }
                )
            replies.sort(key=lambda row: _safe_int(row.get("ts_ms")), reverse=True)
            return replies[:row_limit]

        with self._read_operation() as conn:
            rows = conn.execute(
                """
                SELECT
                    json_extract(artifact.payload_json, '$.request_id') AS request_id,
                    'assistant' AS role,
                    json_extract(artifact.payload_json, '$.narrative') AS content,
                    COALESCE(effect.committed_ts_ms, effect.updated_ts_ms,
                             artifact.created_ts_ms) AS ts_ms,
                    -1 AS turn_index,
                    '{}' AS metadata
                FROM task_runtime_node_artifact AS artifact
                JOIN task_runtime_acceptance_effect_intent AS effect
                  ON effect.graph_id = artifact.graph_id
                 AND effect.graph_revision = artifact.graph_revision
                 AND effect.node_id = artifact.node_id
                WHERE effect.effect_type = 'surface.reply_delivery'
                  AND effect.status = 'COMMITTED'
                  AND json_valid(artifact.payload_json) = 1
                  AND json_extract(artifact.payload_json, '$.decision') = 'APPROVE'
                  AND json_extract(
                        artifact.payload_json,
                        '$.acceptance_effect_intents[0].payload.payload.affective_turn_plan.session_id'
                      ) = ?
                  AND (? = '' OR json_extract(
                        artifact.payload_json, '$.request_id'
                      ) <> ?)
                  AND length(trim(json_extract(
                        artifact.payload_json, '$.narrative'
                      ))) > 0
                ORDER BY COALESCE(
                    effect.committed_ts_ms,
                    effect.updated_ts_ms,
                    artifact.created_ts_ms
                ) DESC
                LIMIT ?
                """,
                (session_key, request_key, request_key, row_limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def recent_dream_memory(self) -> dict[str, Any] | None:
        if self._public_ledger is not None:
            rows = self._public_rows_by_time(
                "memory_ops_log", start_ts=0, end_ts=_MAX_TS, limit=10_000
            )
            for row in rows:
                evidence = _evidence_text(row.get("evidence_refs"))
                if (
                    str(row.get("op_kind") or "").upper() == "WRITE"
                    and str(row.get("status") or "").upper() == "SUCCESS"
                    and "tag:dream" in evidence
                    and "source:dream_generator" in evidence
                    and "tag:dream_usage_hint" not in evidence
                ):
                    return {
                        "memory_id": row.get("memory_id"),
                        "detail": row.get("detail"),
                        "ts_ms": row.get("ts_ms"),
                    }
            return None
        with self._read_operation() as conn:
            columns = _table_columns(conn, "memory_ops_log")
            query = (
                "SELECT memory_id, detail, ts_ms FROM memory_ops_log "
                if "memory_id" in columns
                else "SELECT NULL AS memory_id, detail, ts_ms FROM memory_ops_log "
            ) + (
                "WHERE op_kind = ? AND status = ? "
                "AND evidence_refs LIKE ? AND evidence_refs LIKE ? "
                "AND evidence_refs NOT LIKE ? ORDER BY ts_ms DESC LIMIT ?"
            )
            row = conn.execute(
                query,
                (
                    "WRITE",
                    "SUCCESS",
                    "%tag:dream%",
                    "%source:dream_generator%",
                    "%tag:dream_usage_hint%",
                    1,
                ),
            ).fetchone()
        return dict(row) if row is not None else None

    def read_approved_ledger_rows_by_time(
        self,
        *,
        table: str,
        start_ts: int,
        end_ts: int,
        limit: int,
    ) -> list[dict[str, Any]]:
        table_name = str(table or "").strip()
        if table_name not in APPROVED_LEDGER_TIME_TABLES:
            raise RuntimeReadRepositoryError(
                RUNTIME_READ_TABLE_NOT_ALLOWED,
                f"runtime ledger table is not approved: {table_name!r}",
            )
        row_limit = _limit(limit, maximum=10_000)
        if self._public_ledger is not None:
            return self._public_rows_by_time(
                table_name,
                start_ts=int(start_ts),
                end_ts=int(end_ts),
                limit=row_limit,
            )
        rows: list[sqlite3.Row]
        with self._read_operation() as conn:
            rows = conn.execute(
                _LEDGER_TIME_QUERIES[table_name],
                (int(start_ts), int(end_ts), row_limit),
            ).fetchall()
        return [_decode_ledger_row(table_name, row) for row in rows]

    def recent_memory_write_topics(
        self,
        *,
        since_ts: int,
        limit: int = 5,
    ) -> list[str]:
        if self._public_ledger is not None:
            rows = self._public_rows_by_time(
                "memory_ops_log",
                start_ts=int(since_ts),
                end_ts=_MAX_TS,
                limit=max(_limit(limit, maximum=100) * 4, 100),
            )
            return [
                str(row.get("detail") or "").strip()
                for row in rows
                if str(row.get("op_kind") or "").upper() == "WRITE"
                and str(row.get("status") or "").upper() == "SUCCESS"
                and str(row.get("detail") or "").strip()
            ][:_limit(limit, maximum=100)]
        with self._read_operation() as conn:
            rows = conn.execute(
                """
                SELECT detail FROM memory_ops_log
                WHERE ts_ms >= ? AND op_kind = ? AND status = ?
                ORDER BY ts_ms DESC LIMIT ?
                """,
                (int(since_ts), "WRITE", "SUCCESS", _limit(limit, maximum=100)),
            ).fetchall()
        return [str(row["detail"] or "").strip() for row in rows if row["detail"]]

    def recent_intent_topics(self, *, limit: int = 5) -> list[str]:
        row_limit = _limit(limit, maximum=100)
        if self._public_ledger is not None:
            rows = self._public_rows_by_time(
                "intent_log", start_ts=0, end_ts=_MAX_TS, limit=max(row_limit * 4, row_limit)
            )
            topics: list[str] = []
            for row in rows:
                topic = str(row.get("topic") or "").strip() or _intent_topic(
                    row.get("normalized_json"), row.get("raw_text")
                )
                if topic and topic not in topics:
                    topics.append(topic)
                if len(topics) >= row_limit:
                    break
            return topics
        with self._read_operation() as conn:
            columns = _table_columns(conn, "intent_log")
            if "topic" in columns:
                rows = conn.execute(
                    """
                    SELECT topic FROM intent_log
                    WHERE topic IS NOT NULL AND trim(topic) != ?
                    ORDER BY ts_ms DESC LIMIT ?
                    """,
                    ("", row_limit),
                ).fetchall()
                return [str(row["topic"]).strip() for row in rows if row["topic"]]
            rows = conn.execute(
                """
                SELECT normalized_json, raw_text FROM intent_log
                ORDER BY ts_ms DESC LIMIT ?
                """,
                (max(row_limit * 4, row_limit),),
            ).fetchall()
        topics: list[str] = []
        for row in rows:
            topic = _intent_topic(row["normalized_json"], row["raw_text"])
            if topic and topic not in topics:
                topics.append(topic)
            if len(topics) >= row_limit:
                break
        return topics

    def last_interaction_ts(self, *, upper_bound_ts: int) -> int | None:
        if self._public_ledger is not None:
            rows = self._public_rows_by_time(
                "intent_log",
                start_ts=1,
                end_ts=int(upper_bound_ts),
                limit=1,
            )
            return _safe_int(rows[0].get("ts_ms")) if rows else None
        with self._read_operation() as conn:
            row = conn.execute(
                """
                SELECT ts_ms FROM intent_log
                WHERE ts_ms > ? AND ts_ms <= ?
                ORDER BY ts_ms DESC LIMIT ?
                """,
                (0, int(upper_bound_ts), 1),
            ).fetchone()
        return int(row["ts_ms"]) if row is not None else None

    def _public_rows_by_time(
        self,
        table: str,
        *,
        start_ts: int,
        end_ts: int,
        limit: int,
    ) -> list[dict[str, Any]]:
        ledger = self._public_ledger
        if ledger is None:
            return []
        row_limit = _limit(limit, maximum=10_000)
        fetch = getattr(ledger, "fetch_by_time_range", None)
        if callable(fetch) and table in APPROVED_LEDGER_TIME_TABLES:
            try:
                values = list(
                    fetch(
                        table,
                        start_ms=int(start_ts),
                        end_ms=int(end_ts),
                        limit=row_limit,
                    )
                    or []
                )
            except Exception:
                values = []
            rows = [dict(row) for row in values if isinstance(row, Mapping)]
            rows.sort(key=lambda row: _safe_int(row.get("ts_ms")), reverse=True)
            return rows[:row_limit]
        rows = self._public_table_rows(table)
        filtered = [
            row
            for row in rows
            if int(start_ts) <= _safe_int(row.get("ts_ms")) <= int(end_ts)
        ]
        filtered.sort(key=lambda row: _safe_int(row.get("ts_ms")), reverse=True)
        return filtered[:row_limit]

    def _public_table_rows(self, table: str) -> list[dict[str, Any]]:
        ledger = self._public_ledger
        if ledger is None:
            return []
        try:
            values = list(getattr(ledger, table, []) or [])
        except Exception:
            return []
        return [dict(row) for row in values if isinstance(row, Mapping)]

    def _connect_readonly(self) -> sqlite3.Connection:
        path = Path(self.db_path).resolve().as_posix()
        uri = f"file:{quote(path, safe='/:')}?mode=ro"
        try:
            conn = sqlite3.connect(
                uri,
                uri=True,
                timeout=self._timeout_ms / 1000.0,
                isolation_level=None,
            )
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA query_only = ON")
            conn.execute(f"PRAGMA busy_timeout = {self._timeout_ms}")
            return conn
        except sqlite3.DatabaseError as exc:
            raise RuntimeReadRepositoryError(
                RUNTIME_READ_UNAVAILABLE, "runtime database cannot be opened read-only"
            ) from exc

    @contextmanager
    def _read_operation(self) -> Iterator[sqlite3.Connection]:
        if self._borrowed_connection is not None:
            lock_scope = (
                self._borrowed_lock
                if self._borrowed_lock is not None
                else nullcontext()
            )
            with lock_scope:
                yield self._borrowed_connection
            return
        conn = self._connect_readonly()
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


def _required_text(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise RuntimeReadRepositoryError(
            RUNTIME_READ_INVALID, f"{field} is required"
        )
    return text


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _json_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return dict(parsed) if isinstance(parsed, Mapping) else {}


def _surface_artifact_session_id(payload: Mapping[str, Any]) -> str:
    intents = payload.get("acceptance_effect_intents")
    if not isinstance(intents, list) or not intents:
        return ""
    first = intents[0] if isinstance(intents[0], Mapping) else {}
    outer_payload = first.get("payload") if isinstance(first, Mapping) else {}
    outer_payload = outer_payload if isinstance(outer_payload, Mapping) else {}
    inner_payload = outer_payload.get("payload")
    inner_payload = inner_payload if isinstance(inner_payload, Mapping) else {}
    plan = inner_payload.get("affective_turn_plan")
    plan = plan if isinstance(plan, Mapping) else {}
    return str(plan.get("session_id") or "").strip()


def _evidence_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        return " ".join(str(item or "") for item in value)
    return str(value or "")


def _limit(value: Any, *, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise RuntimeReadRepositoryError(
            RUNTIME_READ_INVALID, "limit must be an integer"
        ) from exc
    return max(1, min(parsed, int(maximum)))


def _table_columns(conn: sqlite3.Connection, table: str) -> frozenset[str]:
    queries = {
        "intent_log": 'PRAGMA table_info("intent_log")',
        "memory_ops_log": 'PRAGMA table_info("memory_ops_log")',
    }
    if table not in queries:
        raise RuntimeReadRepositoryError(
            RUNTIME_READ_TABLE_NOT_ALLOWED,
            f"runtime schema inspection is not approved: {table!r}",
        )
    return frozenset(
        str(row[1])
        for row in conn.execute(queries[table]).fetchall()
    )


def _decode_ledger_row(table: str, row: sqlite3.Row) -> dict[str, Any]:
    value = dict(row)
    for field in JSON_FIELDS_BY_TABLE.get(table, set()):
        raw = value.get(field)
        if isinstance(raw, str) and raw:
            try:
                value[field] = json.loads(raw)
            except json.JSONDecodeError:
                pass
    return value


def _intent_topic(normalized_json: Any, raw_text: Any) -> str:
    normalized: Mapping[str, Any] = {}
    if isinstance(normalized_json, Mapping):
        normalized = normalized_json
    elif isinstance(normalized_json, str) and normalized_json.strip():
        try:
            decoded = json.loads(normalized_json)
        except json.JSONDecodeError:
            decoded = None
        if isinstance(decoded, Mapping):
            normalized = decoded
    for field in ("topic", "primary_topic", "topic_id"):
        topic = str(normalized.get(field) or "").strip()
        if topic:
            return topic
    return str(raw_text or "").strip()


__all__ = [
    "APPROVED_LEDGER_TIME_TABLES",
    "RUNTIME_READ_INVALID",
    "RUNTIME_READ_TABLE_NOT_ALLOWED",
    "RUNTIME_READ_UNAVAILABLE",
    "RuntimeReadRepository",
    "RuntimeReadRepositoryError",
]
