"""Semantic SQLite repository for conversation, topic, and tactic context.

Application compatibility stores delegate here instead of reaching through
``SQLiteLedger._conn``.  Durable production databases use operation-scoped
connections.  A borrowed connection is supported only for legacy in-memory
tests, where opening a second SQLite connection would address a different DB.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
from typing import Any


CONTEXT_SCHEMA_NOT_READY = "CONTEXT_SCHEMA_NOT_READY"
CONTEXT_RECORD_INVALID = "CONTEXT_RECORD_INVALID"
CONTEXT_RECORD_CONFLICT = "CONTEXT_RECORD_CONFLICT"

CONTEXT_MIGRATION_ASSET_KEY = "0070#context_persistence"
CONTEXT_MIGRATION_SHA256 = (
    "faf991332cffab3f9f7307b2101e9c88d6443b5b32972b08a3be946d97a43e67"
)
_ALLOWED_MIGRATION_DISPOSITIONS = frozenset(
    {"APPLIED", "APPLIED_WITH_COMPAT", "LEGACY_BACKFILL"}
)
_TURN_COLUMNS = (
    "turn_id",
    "session_id",
    "person_id",
    "request_id",
    "role",
    "content",
    "ts_ms",
    "turn_index",
    "lang",
    "token_count",
    "corrupted",
    "metadata",
)
_REQUIRED_COLUMNS: dict[str, frozenset[str]] = {
    "conversation_turn": frozenset((*_TURN_COLUMNS, "created_at_ms")),
    "session_topic_state": frozenset(
        {
            "id",
            "session_id",
            "person_id",
            "topic_key",
            "last_answer_turn_index",
            "last_answer_request_id",
            "source_assistant_act",
            "confidence",
            "stale_after_turn_index",
            "metadata",
            "updated_at_ms",
        }
    ),
    "session_tactic_ledger": frozenset(
        {
            "id",
            "session_id",
            "request_id",
            "topic_key",
            "interaction_move",
            "tactic_family",
            "tactic_variant",
            "trajectory_stage",
            "ts_ms",
        }
    ),
}
_REQUIRED_INDEXES = frozenset(
    {
        "idx_conversation_turn_session_ts",
        "idx_conversation_turn_session_turn_idx",
        "idx_conversation_turn_request",
        "idx_conversation_turn_session_request_role_unique",
        "idx_session_topic_state_session",
        "idx_session_topic_state_updated",
        "idx_session_topic_state_request",
        "idx_session_tactic_ledger_session_ts",
        "idx_session_tactic_ledger_topic",
        "idx_session_tactic_ledger_request",
    }
)


class ContextRepositoryError(RuntimeError):
    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = str(reason_code or CONTEXT_RECORD_INVALID)
        self.detail = str(detail or "context persistence failed")
        super().__init__(f"{self.reason_code}: {self.detail}")


def _text(value: Any, field_name: str, *, required: bool = True) -> str:
    normalized = str(value or "").strip()
    if required and not normalized:
        raise ContextRepositoryError(CONTEXT_RECORD_INVALID, f"{field_name} is required")
    return normalized


def _json(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _normalized_json(value: Any) -> Any:
    if value in (None, ""):
        return None
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value))
    except (TypeError, json.JSONDecodeError):
        return str(value)


class ContextRepository:
    """Single writer/UoW for conversation turns and session context ledgers."""

    def __init__(
        self,
        db_path: str | Path,
        *,
        timeout_ms: int = 5_000,
        verify_schema: bool = True,
        _borrowed_connection: sqlite3.Connection | None = None,
        _borrowed_lock: Any | None = None,
    ) -> None:
        raw_path = str(db_path or "").strip()
        if not raw_path:
            raise ContextRepositoryError(CONTEXT_RECORD_INVALID, "database path is required")
        if raw_path == ":memory:" and _borrowed_connection is None:
            raise ContextRepositoryError(
                CONTEXT_RECORD_INVALID,
                "in-memory context repository requires an existing ledger connection",
            )
        self.db_path = raw_path if raw_path == ":memory:" else str(Path(raw_path).resolve())
        self._timeout_ms = max(1, int(timeout_ms))
        self._borrowed_connection = _borrowed_connection
        self._borrowed_lock = _borrowed_lock
        if verify_schema:
            self.assert_schema_ready(require_catalog=raw_path != ":memory:")

    @classmethod
    def from_ledger(cls, ledger: Any, *, verify_schema: bool = True) -> "ContextRepository":
        canonical = str(getattr(ledger, "canonical_db_path", "") or "").strip()
        if not canonical:
            canonical = str(getattr(ledger, "db_path", "") or "").strip()
        if canonical == ":memory:":
            connection = getattr(ledger, "_conn", None)
            lock = getattr(ledger, "_write_lock", None)
            if connection is None or lock is None:
                raise ContextRepositoryError(
                    CONTEXT_RECORD_INVALID,
                    "in-memory ledger does not expose its compatibility connection",
                )
            return cls(
                ":memory:",
                verify_schema=verify_schema,
                _borrowed_connection=connection,
                _borrowed_lock=lock,
            )
        return cls(canonical, verify_schema=verify_schema)

    def _connect(self) -> sqlite3.Connection:
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
    def _read(self) -> Iterator[sqlite3.Connection]:
        if self._borrowed_connection is not None:
            with self._borrowed_lock:
                yield self._borrowed_connection
            return
        conn = self._connect()
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        if self._borrowed_connection is not None:
            with self._borrowed_lock:
                try:
                    self._borrowed_connection.execute("BEGIN IMMEDIATE")
                    yield self._borrowed_connection
                    self._borrowed_connection.commit()
                except BaseException:
                    self._borrowed_connection.rollback()
                    raise
            return
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

    def assert_schema_ready(self, *, require_catalog: bool = True) -> None:
        try:
            with self._read() as conn:
                columns = {
                    table: {
                        str(row[1])
                        for row in conn.execute(f'PRAGMA table_info("{table}")').fetchall()
                    }
                    for table in _REQUIRED_COLUMNS
                }
                indexes = {
                    str(row[0])
                    for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'index'"
                    ).fetchall()
                }
                asset = None
                if require_catalog:
                    asset = conn.execute(
                        """
                        SELECT checksum, disposition FROM schema_migration_assets
                        WHERE asset_key = ?
                        """,
                        (CONTEXT_MIGRATION_ASSET_KEY,),
                    ).fetchone()
        except sqlite3.DatabaseError as exc:
            raise ContextRepositoryError(
                CONTEXT_SCHEMA_NOT_READY,
                "context schema cannot be verified",
            ) from exc
        missing = {
            table: sorted(required - columns.get(table, set()))
            for table, required in _REQUIRED_COLUMNS.items()
            if not required <= columns.get(table, set())
        }
        catalog_invalid = require_catalog and (
            asset is None
            or str(asset["checksum"]) != CONTEXT_MIGRATION_SHA256
            or str(asset["disposition"]) not in _ALLOWED_MIGRATION_DISPOSITIONS
        )
        if missing or not _REQUIRED_INDEXES <= indexes or catalog_invalid:
            raise ContextRepositoryError(
                CONTEXT_SCHEMA_NOT_READY,
                "context migration 0070 is missing, stale, or checksum-drifted",
            )

    # -- Conversation turns -------------------------------------------------

    def append_turn(self, turn: Mapping[str, Any]) -> dict[str, Any]:
        item = dict(turn)
        session_id = _text(item.get("session_id"), "session_id")
        request_id = _text(item.get("request_id"), "request_id")
        role = _text(item.get("role"), "role")
        if role not in {"user", "assistant"}:
            raise ContextRepositoryError(CONTEXT_RECORD_INVALID, "role is invalid")
        with self._write() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO conversation_turn(
                    turn_id, session_id, person_id, request_id, role, content,
                    ts_ms, turn_index, lang, token_count, corrupted, metadata,
                    created_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _text(item.get("turn_id"), "turn_id"),
                    session_id,
                    _text(item.get("person_id"), "person_id"),
                    request_id,
                    role,
                    str(item.get("content") or ""),
                    int(item.get("ts_ms") or 0),
                    int(item.get("turn_index") or 0),
                    _text(item.get("lang") or "zh", "lang"),
                    item.get("token_count"),
                    1 if item.get("corrupted") else 0,
                    _json(item.get("metadata")),
                    int(item.get("ts_ms") or 0),
                ),
            )
            row = conn.execute(
                """
                SELECT turn_id, session_id, person_id, request_id, role,
                       content, ts_ms, turn_index, lang, token_count,
                       corrupted, metadata
                FROM conversation_turn
                WHERE session_id = ? AND request_id = ? AND role = ?
                LIMIT 1
                """,
                (session_id, request_id, role),
            ).fetchone()
            if row is None:
                raise ContextRepositoryError(
                    CONTEXT_RECORD_CONFLICT,
                    "conversation turn was not persisted",
                )
            record = dict(row)
            self._assert_turn_replay(record, item, include_turn_index=True)
            return record

    def append_generated_turn(self, turn: Mapping[str, Any]) -> dict[str, Any]:
        item = dict(turn)
        session_id = _text(item.get("session_id"), "session_id")
        request_id = _text(item.get("request_id"), "request_id")
        role = _text(item.get("role"), "role")
        if role not in {"user", "assistant"}:
            raise ContextRepositoryError(CONTEXT_RECORD_INVALID, "role is invalid")
        with self._write() as conn:
            existing = conn.execute(
                """
                SELECT turn_id, session_id, person_id, request_id, role,
                       content, ts_ms, turn_index, lang, token_count,
                       corrupted, metadata
                FROM conversation_turn
                WHERE session_id = ? AND request_id = ? AND role = ?
                LIMIT 1
                """,
                (session_id, request_id, role),
            ).fetchone()
            if existing is not None:
                record = dict(existing)
                self._assert_turn_replay(record, item, include_turn_index=False)
                return record
            next_row = conn.execute(
                """
                SELECT COALESCE(MAX(turn_index), -1) + 1
                FROM conversation_turn WHERE session_id = ?
                """,
                (session_id,),
            ).fetchone()
            item["turn_index"] = int(next_row[0] if next_row is not None else 0)
            conn.execute(
                """
                INSERT INTO conversation_turn(
                    turn_id, session_id, person_id, request_id, role, content,
                    ts_ms, turn_index, lang, token_count, corrupted, metadata,
                    created_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _text(item.get("turn_id"), "turn_id"),
                    session_id,
                    _text(item.get("person_id"), "person_id"),
                    request_id,
                    role,
                    str(item.get("content") or ""),
                    int(item.get("ts_ms") or 0),
                    int(item["turn_index"]),
                    _text(item.get("lang") or "zh", "lang"),
                    item.get("token_count"),
                    1 if item.get("corrupted") else 0,
                    _json(item.get("metadata")),
                    int(item.get("ts_ms") or 0),
                ),
            )
            row = conn.execute(
                f"SELECT {', '.join(_TURN_COLUMNS)} FROM conversation_turn WHERE turn_id = ?",
                (item["turn_id"],),
            ).fetchone()
            if row is None:
                raise ContextRepositoryError(CONTEXT_RECORD_CONFLICT, "generated turn disappeared")
            return dict(row)

    @staticmethod
    def _assert_turn_replay(
        persisted: Mapping[str, Any],
        requested: Mapping[str, Any],
        *,
        include_turn_index: bool,
    ) -> None:
        expected = {
            "turn_id": str(requested.get("turn_id") or ""),
            "session_id": str(requested.get("session_id") or "").strip(),
            "person_id": str(requested.get("person_id") or "").strip(),
            "request_id": str(requested.get("request_id") or "").strip(),
            "role": str(requested.get("role") or "").strip(),
            "content": str(requested.get("content") or ""),
            "ts_ms": int(requested.get("ts_ms") or 0),
            "lang": str(requested.get("lang") or "zh").strip() or "zh",
            "token_count": requested.get("token_count"),
            "corrupted": 1 if requested.get("corrupted") else 0,
        }
        if include_turn_index:
            expected["turn_index"] = int(requested.get("turn_index") or 0)
        mismatched = [
            key
            for key, value in expected.items()
            if persisted.get(key) != value
        ]
        if _normalized_json(persisted.get("metadata")) != _normalized_json(
            requested.get("metadata")
        ):
            mismatched.append("metadata")
        if mismatched:
            raise ContextRepositoryError(
                CONTEXT_RECORD_CONFLICT,
                "conversation turn replay conflicts with canonical fields: "
                + ", ".join(mismatched),
            )

    def query_recent_turns(
        self,
        *,
        session_id: str,
        limit: int,
        exclude_request_id: str | None = None,
        before_ts_ms: int | None = None,
        before_turn_index: int | None = None,
    ) -> list[dict[str, Any]]:
        normalized = _text(session_id, "session_id", required=False)
        if not normalized:
            return []
        clauses = ["session_id = ?"]
        params: list[Any] = [normalized]
        if str(exclude_request_id or "").strip():
            clauses.append("request_id != ?")
            params.append(str(exclude_request_id).strip())
        if before_ts_ms is not None:
            clauses.append("ts_ms < ?")
            params.append(int(before_ts_ms))
        if before_turn_index is not None:
            clauses.append("turn_index < ?")
            params.append(int(before_turn_index))
        params.append(max(1, int(limit)))
        with self._read() as conn:
            rows = conn.execute(
                f"""
                SELECT {', '.join(_TURN_COLUMNS)} FROM conversation_turn
                WHERE {' AND '.join(clauses)}
                ORDER BY ts_ms DESC, turn_index DESC LIMIT ?
                """,
                tuple(params),
            ).fetchall()
        return [dict(row) for row in reversed(rows)]

    def list_memory_spine_turns(
        self,
        *,
        person_id: str | None,
        session_id: str,
        limit: int,
        include_asr_sessions: bool,
    ) -> list[dict[str, Any]]:
        """Return the canonical, oldest-first turn window eligible for backfill."""

        person = _text(person_id, "person_id", required=False)
        session = _text(session_id, "session_id", required=False)
        clauses = ["role IN ('user', 'assistant')", "COALESCE(content, '') != ''"]
        params: list[Any] = []
        if person:
            clauses.append("(person_id = ? OR COALESCE(person_id, '') = '')")
            params.append(person)
        if session:
            clauses.append("session_id = ?")
            params.append(session)
        elif not include_asr_sessions:
            clauses.extend(
                (
                    "session_id NOT LIKE 'asr_req_%'",
                    "session_id NOT LIKE 'codex_%'",
                    "session_id NOT LIKE '__codex_%'",
                )
            )
        params.append(max(1, min(int(limit or 500), 5_000)))
        with self._read() as conn:
            rows = conn.execute(
                f"""
                SELECT {', '.join(_TURN_COLUMNS)} FROM conversation_turn
                WHERE {' AND '.join(clauses)}
                ORDER BY ts_ms ASC
                LIMIT ?
                """,
                tuple(params),
            ).fetchall()
        return [dict(row) for row in rows]

    def find_turn(
        self,
        *,
        session_id: str,
        request_id: str,
        role: str | None = None,
    ) -> dict[str, Any] | None:
        session = _text(session_id, "session_id", required=False)
        request = _text(request_id, "request_id", required=False)
        if not session or not request:
            return None
        clauses = ["session_id = ?", "request_id = ?"]
        params: list[Any] = [session, request]
        if str(role or "").strip():
            clauses.append("role = ?")
            params.append(str(role).strip())
        with self._read() as conn:
            row = conn.execute(
                f"""
                SELECT {', '.join(_TURN_COLUMNS)} FROM conversation_turn
                WHERE {' AND '.join(clauses)} ORDER BY turn_index ASC LIMIT 1
                """,
                tuple(params),
            ).fetchone()
        return dict(row) if row is not None else None

    def query_turns_before_index(
        self,
        *,
        session_id: str,
        before_turn_index: int,
        limit: int,
        exclude_request_id: str | None = None,
    ) -> list[dict[str, Any]]:
        normalized = _text(session_id, "session_id", required=False)
        if not normalized:
            return []
        clauses = ["session_id = ?", "turn_index < ?"]
        params: list[Any] = [normalized, int(before_turn_index)]
        if str(exclude_request_id or "").strip():
            clauses.append("request_id != ?")
            params.append(str(exclude_request_id).strip())
        params.append(max(1, int(limit)))
        with self._read() as conn:
            rows = conn.execute(
                f"""
                SELECT {', '.join(_TURN_COLUMNS)} FROM conversation_turn
                WHERE {' AND '.join(clauses)}
                ORDER BY turn_index DESC LIMIT ?
                """,
                tuple(params),
            ).fetchall()
        return [dict(row) for row in reversed(rows)]

    def count_turns_before_index(
        self,
        *,
        session_id: str,
        before_turn_index: int,
        exclude_request_id: str | None = None,
    ) -> int:
        clauses = ["session_id = ?", "turn_index < ?"]
        params: list[Any] = [str(session_id or "").strip(), int(before_turn_index)]
        if str(exclude_request_id or "").strip():
            clauses.append("request_id != ?")
            params.append(str(exclude_request_id).strip())
        with self._read() as conn:
            row = conn.execute(
                f"SELECT COUNT(*) FROM conversation_turn WHERE {' AND '.join(clauses)}",
                tuple(params),
            ).fetchone()
        return int(row[0] if row is not None else 0)

    def next_turn_index(self, *, session_id: str) -> int:
        with self._read() as conn:
            row = conn.execute(
                "SELECT COALESCE(MAX(turn_index), -1) + 1 FROM conversation_turn WHERE session_id = ?",
                (str(session_id or "").strip(),),
            ).fetchone()
        return int(row[0] if row is not None else 0)

    def query_recent_assistant_turns(
        self,
        *,
        limit: int,
        lang: str | None = None,
    ) -> list[dict[str, Any]]:
        clauses = ["role = 'assistant'"]
        params: list[Any] = []
        if str(lang or "").strip():
            clauses.append("LOWER(lang) = ?")
            params.append(str(lang).strip().lower())
        params.append(max(1, int(limit)))
        with self._read() as conn:
            rows = conn.execute(
                f"""
                SELECT {', '.join(_TURN_COLUMNS)} FROM conversation_turn
                WHERE {' AND '.join(clauses)}
                ORDER BY ts_ms DESC, turn_index DESC LIMIT ?
                """,
                tuple(params),
            ).fetchall()
        return [dict(row) for row in rows]

    def delete_conversation_session(self, *, session_id: str) -> int:
        with self._write() as conn:
            cursor = conn.execute(
                "DELETE FROM conversation_turn WHERE session_id = ?",
                (str(session_id or "").strip(),),
            )
            return int(cursor.rowcount or 0)

    # -- One shared TopicState/TopicLedger writer ---------------------------

    def upsert_topic_states(self, entries: Sequence[Mapping[str, Any]]) -> tuple[str, ...]:
        items = [dict(entry) for entry in entries]
        if not items:
            return ()
        topics: list[str] = []
        with self._write() as conn:
            for item in items:
                session = _text(item.get("session_id"), "session_id")
                topic = _text(item.get("topic_key"), "topic_key")
                conn.execute(
                    """
                    INSERT INTO session_topic_state(
                        session_id, person_id, topic_key,
                        last_answer_turn_index, last_answer_request_id,
                        source_assistant_act, confidence,
                        stale_after_turn_index, metadata, updated_at_ms
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(session_id, topic_key) DO UPDATE SET
                        person_id = excluded.person_id,
                        last_answer_turn_index = excluded.last_answer_turn_index,
                        last_answer_request_id = excluded.last_answer_request_id,
                        source_assistant_act = excluded.source_assistant_act,
                        confidence = excluded.confidence,
                        stale_after_turn_index = excluded.stale_after_turn_index,
                        metadata = excluded.metadata,
                        updated_at_ms = excluded.updated_at_ms
                    """,
                    (
                        session,
                        str(item.get("person_id") or "").strip() or None,
                        topic,
                        int(item.get("last_answer_turn_index") or 0),
                        str(item.get("last_answer_request_id") or "").strip(),
                        str(item.get("source_assistant_act") or "").strip(),
                        float(item.get("confidence") or 0.0),
                        int(item.get("stale_after_turn_index") or 0),
                        _json(item.get("metadata")),
                        int(item.get("updated_at_ms") or 0),
                    ),
                )
                topics.append(topic)
        return tuple(dict.fromkeys(topics))

    def fetch_topic_states(self, *, session_id: str) -> list[dict[str, Any]]:
        session = str(session_id or "").strip()
        if not session:
            return []
        with self._read() as conn:
            rows = conn.execute(
                """
                SELECT topic_key, last_answer_turn_index,
                       last_answer_request_id, source_assistant_act,
                       confidence, stale_after_turn_index, metadata
                FROM session_topic_state WHERE session_id = ?
                ORDER BY last_answer_turn_index DESC, updated_at_ms DESC
                """,
                (session,),
            ).fetchall()
        return [dict(row) for row in rows]

    def delete_topic_session(self, *, session_id: str) -> int:
        with self._write() as conn:
            cursor = conn.execute(
                "DELETE FROM session_topic_state WHERE session_id = ?",
                (str(session_id or "").strip(),),
            )
            return int(cursor.rowcount or 0)

    # -- Tactic ledger -------------------------------------------------------

    def append_tactic(self, tactic: Mapping[str, Any]) -> None:
        item = dict(tactic)
        with self._write() as conn:
            conn.execute(
                """
                INSERT INTO session_tactic_ledger(
                    session_id, request_id, topic_key, interaction_move,
                    tactic_family, tactic_variant, trajectory_stage, ts_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _text(item.get("session_id"), "session_id"),
                    _text(item.get("request_id"), "request_id"),
                    _text(item.get("topic_key"), "topic_key"),
                    str(item.get("interaction_move") or "").strip(),
                    _text(item.get("tactic_family"), "tactic_family"),
                    str(item.get("tactic_variant") or "").strip(),
                    str(item.get("trajectory_stage") or "").strip(),
                    int(item.get("ts_ms") or 0),
                ),
            )

    def fetch_tactics(self, *, session_id: str, limit: int) -> list[dict[str, Any]]:
        session = str(session_id or "").strip()
        if not session:
            return []
        with self._read() as conn:
            rows = conn.execute(
                """
                SELECT session_id, request_id, topic_key, interaction_move,
                       tactic_family, tactic_variant, trajectory_stage, ts_ms
                FROM session_tactic_ledger WHERE session_id = ?
                ORDER BY ts_ms DESC, id DESC LIMIT ?
                """,
                (session, max(1, int(limit))),
            ).fetchall()
        return [dict(row) for row in rows]

    def delete_tactic_session(self, *, session_id: str) -> int:
        with self._write() as conn:
            cursor = conn.execute(
                "DELETE FROM session_tactic_ledger WHERE session_id = ?",
                (str(session_id or "").strip(),),
            )
            return int(cursor.rowcount or 0)

    def reset_short_term_continuity(self) -> dict[str, int]:
        """Atomically clear every short-lived conversation projection."""

        with self._write() as conn:
            turns = int(conn.execute("DELETE FROM conversation_turn").rowcount or 0)
            topics = int(conn.execute("DELETE FROM session_topic_state").rowcount or 0)
            tactics = int(conn.execute("DELETE FROM session_tactic_ledger").rowcount or 0)
        return {"turns": turns, "topics": topics, "tactics": tactics}

__all__ = [
    "CONTEXT_MIGRATION_ASSET_KEY",
    "CONTEXT_MIGRATION_SHA256",
    "CONTEXT_RECORD_CONFLICT",
    "CONTEXT_RECORD_INVALID",
    "CONTEXT_SCHEMA_NOT_READY",
    "ContextRepository",
    "ContextRepositoryError",
]
