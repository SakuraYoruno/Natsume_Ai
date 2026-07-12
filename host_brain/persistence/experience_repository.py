"""Semantic persistence boundary for conversation experience and memory context.

Durable databases are opened per operation and must prove migration ``0070``
through the migration asset catalog.  Borrowing a ledger connection is limited
to legacy ``:memory:`` tests because a second SQLite connection would address a
different database.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from typing import Any


EXPERIENCE_SCHEMA_NOT_READY = "EXPERIENCE_SCHEMA_NOT_READY"
EXPERIENCE_RECORD_INVALID = "EXPERIENCE_RECORD_INVALID"
EXPERIENCE_REPLAY_CONFLICT = "EXPERIENCE_REPLAY_CONFLICT"
EXPERIENCE_DURABLE_DATA_INVALID = "EXPERIENCE_DURABLE_DATA_INVALID"

CONTEXT_MIGRATION_ASSET_KEY = "0070#context_persistence"
CONTEXT_MIGRATION_SHA256 = (
    "faf991332cffab3f9f7307b2101e9c88d6443b5b32972b08a3be946d97a43e67"
)
_ALLOWED_MIGRATION_DISPOSITIONS = frozenset(
    {"APPLIED", "APPLIED_WITH_COMPAT", "LEGACY_BACKFILL"}
)
_MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "audit"
    / "migrations"
    / "0070_context_persistence.sql"
)

_REQUIRED_COLUMNS: dict[str, frozenset[str]] = {
    "conversation_belief_state": frozenset(
        {"session_id", "person_id", "version", "belief_json", "updated_at_ms"}
    ),
    "conversation_experience_event": frozenset(
        {
            "event_id",
            "request_id",
            "session_id",
            "person_id",
            "user_text",
            "selected_intent",
            "response_policy",
            "understanding_json",
            "belief_before_json",
            "belief_after_json",
            "assistant_reply",
            "outcome_signal",
            "created_at_ms",
            "updated_at_ms",
            "active",
        }
    ),
    "conversation_correction_event": frozenset(
        {
            "correction_id",
            "event_id",
            "request_id",
            "session_id",
            "person_id",
            "raw_text",
            "wrong_inference_json",
            "correct_reference_json",
            "repair_lesson",
            "confidence",
            "created_at_ms",
            "active",
        }
    ),
    "conversation_experience_lesson": frozenset(
        {
            "lesson_id",
            "person_id",
            "domain",
            "pattern",
            "lesson",
            "source_event_ids_json",
            "success_count",
            "failure_count",
            "confidence",
            "updated_at_ms",
            "active",
        }
    ),
    "memory_sanitize_audit": frozenset(
        {"id", "person_id", "summary_json", "created_at_ms"}
    ),
}
_REQUIRED_INDEXES = frozenset(
    {
        "idx_conversation_experience_event_session",
        "idx_conversation_correction_session",
        "idx_conversation_lesson_domain",
    }
)
_JSON_FIELDS = frozenset(
    {
        "understanding_json",
        "belief_before_json",
        "belief_after_json",
        "wrong_inference_json",
        "correct_reference_json",
    }
)
_UNSUPPORTED_PROACTIVE_ACTION_RE = re.compile(
    r"(盯着屏幕|一直等你|一直守着|寸步不离|收拾了一遍|把周围收拾|刚把周围收拾)"
)


class ExperienceRepositoryError(RuntimeError):
    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = str(reason_code or EXPERIENCE_RECORD_INVALID)
        self.detail = str(detail or "experience persistence failed")
        super().__init__(f"{self.reason_code}: {self.detail}")


def _text(value: Any, field_name: str = "value", *, required: bool = False) -> str:
    normalized = str(value or "").strip()
    if required and not normalized:
        raise ExperienceRepositoryError(
            EXPERIENCE_RECORD_INVALID, f"{field_name} is required"
        )
    return normalized


def _json(value: Any) -> str:
    return json.dumps(
        value if value is not None else {},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _loads(value: Any, default: Any) -> Any:
    if value in (None, ""):
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value))
    except (TypeError, json.JSONDecodeError):
        return default


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _normalized_json(value: Any) -> Any:
    if value in (None, ""):
        return None
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value))
    except (TypeError, json.JSONDecodeError):
        return str(value)


def _row_dict(row: Any) -> dict[str, Any]:
    return dict(row) if row is not None else {}


def _compact_text(value: Any, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    return text[: max(0, int(limit))]


class ExperienceRepository:
    """Operation-scoped UoW for experience, belief, and memory sanitation."""

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
            raise ExperienceRepositoryError(
                EXPERIENCE_RECORD_INVALID, "database path is required"
            )
        if raw_path == ":memory:" and _borrowed_connection is None:
            raise ExperienceRepositoryError(
                EXPERIENCE_RECORD_INVALID,
                "in-memory experience repository requires an existing ledger connection",
            )
        self.db_path = raw_path if raw_path == ":memory:" else str(Path(raw_path).resolve())
        self._timeout_ms = max(1, int(timeout_ms))
        self._borrowed_connection = _borrowed_connection
        self._borrowed_lock = _borrowed_lock
        if raw_path == ":memory:":
            self._prepare_borrowed_schema()
        if verify_schema:
            self.assert_schema_ready(require_catalog=raw_path != ":memory:")

    @classmethod
    def from_ledger(
        cls, ledger: Any, *, verify_schema: bool = True
    ) -> "ExperienceRepository":
        canonical = str(getattr(ledger, "canonical_db_path", "") or "").strip()
        if not canonical:
            canonical = str(getattr(ledger, "db_path", "") or "").strip()
        if canonical == ":memory:":
            connection = getattr(ledger, "_conn", None)
            lock = getattr(ledger, "_write_lock", None)
            if connection is None or lock is None:
                raise ExperienceRepositoryError(
                    EXPERIENCE_RECORD_INVALID,
                    "in-memory ledger does not expose its compatibility connection",
                )
            return cls(
                ":memory:",
                verify_schema=verify_schema,
                _borrowed_connection=connection,
                _borrowed_lock=lock,
            )
        return cls(canonical, verify_schema=verify_schema)

    def _prepare_borrowed_schema(self) -> None:
        conn = self._borrowed_connection
        if conn is None:
            return
        with self._borrowed_lock:
            ready = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name='memory_sanitize_audit' LIMIT 1"
            ).fetchone()
            if ready:
                return
            if conn.in_transaction:
                raise ExperienceRepositoryError(
                    EXPERIENCE_SCHEMA_NOT_READY,
                    "borrowed test storage cannot bootstrap 0070 inside an active transaction",
                )
            try:
                migration = _MIGRATION_PATH.read_bytes()
            except OSError as exc:
                raise ExperienceRepositoryError(
                    EXPERIENCE_SCHEMA_NOT_READY,
                    "context migration 0070 cannot be read",
                ) from exc
            if hashlib.sha256(migration).hexdigest() != CONTEXT_MIGRATION_SHA256:
                raise ExperienceRepositoryError(
                    EXPERIENCE_SCHEMA_NOT_READY,
                    "context migration 0070 checksum drifted",
                )
            try:
                conn.executescript(migration.decode("utf-8"))
            except sqlite3.DatabaseError as exc:
                conn.rollback()
                raise ExperienceRepositoryError(
                    EXPERIENCE_SCHEMA_NOT_READY,
                    "context migration 0070 cannot initialize borrowed test storage",
                ) from exc

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
        asset = None
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
                        "SELECT name FROM sqlite_master WHERE type='index'"
                    ).fetchall()
                }
                if require_catalog:
                    asset = conn.execute(
                        "SELECT checksum, disposition FROM schema_migration_assets "
                        "WHERE asset_key=?",
                        (CONTEXT_MIGRATION_ASSET_KEY,),
                    ).fetchone()
        except sqlite3.DatabaseError as exc:
            raise ExperienceRepositoryError(
                EXPERIENCE_SCHEMA_NOT_READY, "experience schema cannot be verified"
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
            raise ExperienceRepositoryError(
                EXPERIENCE_SCHEMA_NOT_READY,
                "context migration 0070 is missing, stale, or checksum-drifted",
            )

    def assert_runtime_ready(self) -> None:
        """Revalidate this repository using its configured durability mode."""

        self.assert_schema_ready(require_catalog=self._borrowed_connection is None)

    # -- Belief and experience --------------------------------------------

    def get_belief(
        self, *, session_id: str, person_id: str, include_updated_at: bool = False
    ) -> dict[str, Any]:
        sid = _text(session_id)
        if not sid:
            return {}
        with self._read() as conn:
            row = conn.execute(
                "SELECT belief_json, updated_at_ms FROM conversation_belief_state "
                "WHERE session_id=? AND person_id=? LIMIT 1",
                (sid, _text(person_id) or "default"),
            ).fetchone()
        if row is None:
            return {}
        belief = _mapping(_loads(row["belief_json"], {}))
        if include_updated_at:
            belief["_updated_at_ms"] = int(row["updated_at_ms"] or 0)
        return belief

    def record_inference(
        self,
        *,
        event: Mapping[str, Any],
        belief_after: Mapping[str, Any],
        correction: Mapping[str, Any] | None = None,
        lessons: Sequence[Mapping[str, Any]] = (),
    ) -> dict[str, Any]:
        item = _mapping(event)
        event_id = _text(item.get("event_id"), "event_id", required=True)
        session_id = _text(item.get("session_id"), "session_id", required=True)
        person_id = _text(item.get("person_id")) or "default"
        now = int(item.get("created_at_ms") or 0)
        if now <= 0:
            raise ExperienceRepositoryError(
                EXPERIENCE_RECORD_INVALID, "created_at_ms must be positive"
            )
        normalized_belief = _mapping(belief_after)
        normalized_belief.setdefault("scope", "session")
        normalized_belief.setdefault("ttl_ms", 30 * 60 * 1000)
        normalized_belief.setdefault(
            "expires_at_ms",
            now + int(normalized_belief.get("ttl_ms") or 30 * 60 * 1000),
        )
        normalized_belief.setdefault("evidence_turn_ids", [])
        normalized_belief.setdefault("source_turn_id", _text(item.get("request_id")))
        if session_id.startswith("asr_req_"):
            normalized_belief["can_drive_reply"] = False
        else:
            normalized_belief.setdefault("can_drive_reply", True)

        with self._write() as conn:
            existing = conn.execute(
                "SELECT * FROM conversation_experience_event WHERE event_id=? LIMIT 1",
                (event_id,),
            ).fetchone()
            if existing is not None:
                self._assert_event_replay(existing, item)
                self._assert_replayed_dependents(
                    conn, correction=correction, lessons=lessons
                )
                return {"event_id": event_id, "replayed": True}
            conn.execute(
                """
                INSERT INTO conversation_experience_event(
                    event_id, request_id, session_id, person_id, user_text,
                    selected_intent, response_policy, understanding_json,
                    belief_before_json, belief_after_json, assistant_reply,
                    outcome_signal, created_at_ms, updated_at_ms, active
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                """,
                (
                    event_id,
                    _text(item.get("request_id")),
                    session_id,
                    person_id,
                    _text(item.get("user_text")),
                    _text(item.get("selected_intent")),
                    _text(item.get("response_policy")),
                    _json(_mapping(item.get("understanding"))),
                    _json(_mapping(item.get("belief_before"))),
                    _json(_mapping(item.get("belief_after"))),
                    _text(item.get("assistant_reply")),
                    _text(item.get("outcome_signal")) or "unknown",
                    now,
                    int(item.get("updated_at_ms") or now),
                ),
            )
            current = conn.execute(
                "SELECT version FROM conversation_belief_state "
                "WHERE session_id=? AND person_id=? LIMIT 1",
                (session_id, person_id),
            ).fetchone()
            version = int(current["version"] or 0) + 1 if current else 1
            conn.execute(
                """
                INSERT INTO conversation_belief_state(
                    session_id, person_id, version, belief_json, updated_at_ms
                ) VALUES(?, ?, ?, ?, ?)
                ON CONFLICT(session_id, person_id) DO UPDATE SET
                    version=excluded.version,
                    belief_json=excluded.belief_json,
                    updated_at_ms=excluded.updated_at_ms
                """,
                (session_id, person_id, version, _json(normalized_belief), now),
            )
            if correction:
                self._insert_correction(conn, _mapping(correction))
            for lesson in lessons:
                self._upsert_lesson(conn, _mapping(lesson))
        return {"event_id": event_id, "replayed": False}

    def _assert_event_replay(self, row: Mapping[str, Any], event: Mapping[str, Any]) -> None:
        expected = {
            "event_id": _text(event.get("event_id")),
            "request_id": _text(event.get("request_id")),
            "session_id": _text(event.get("session_id")),
            "person_id": _text(event.get("person_id")) or "default",
            "user_text": _text(event.get("user_text")),
            "selected_intent": _text(event.get("selected_intent")),
            "response_policy": _text(event.get("response_policy")),
            "understanding_json": _mapping(event.get("understanding")),
            "belief_before_json": _mapping(event.get("belief_before")),
            "belief_after_json": _mapping(event.get("belief_after")),
            "assistant_reply": _text(event.get("assistant_reply")),
            "outcome_signal": _text(event.get("outcome_signal")) or "unknown",
        }
        for key, value in expected.items():
            actual = row[key]
            if key in _JSON_FIELDS:
                actual = _normalized_json(actual)
            if key == "assistant_reply" and not value:
                continue
            if key == "outcome_signal" and value in {"", "unknown"}:
                continue
            if actual != value:
                raise ExperienceRepositoryError(
                    EXPERIENCE_REPLAY_CONFLICT,
                    f"event_id {expected['event_id']} replay changed {key}",
                )

    def _assert_replayed_dependents(
        self,
        conn: sqlite3.Connection,
        *,
        correction: Mapping[str, Any] | None,
        lessons: Sequence[Mapping[str, Any]],
    ) -> None:
        if correction:
            item = _mapping(correction)
            row = conn.execute(
                "SELECT * FROM conversation_correction_event WHERE correction_id=?",
                (_text(item.get("correction_id")),),
            ).fetchone()
            if row is None:
                raise ExperienceRepositoryError(
                    EXPERIENCE_DURABLE_DATA_INVALID,
                    "replayed event is missing its correction",
                )
            self._assert_correction_replay(row, item)
        for lesson in lessons:
            self._assert_lesson_replay(conn, _mapping(lesson))

    def update_assistant_reply(
        self,
        *,
        request_id: str,
        session_id: str,
        person_id: str,
        assistant_reply: str,
        outcome_signal: str,
        updated_at_ms: int,
    ) -> dict[str, Any]:
        req = _text(request_id)
        sid = _text(session_id)
        pid = _text(person_id) or "default"
        with self._write() as conn:
            if req:
                if sid:
                    rows = conn.execute(
                        "SELECT * FROM conversation_experience_event "
                        "WHERE request_id=? AND session_id=? AND person_id=? "
                        "AND active=1 ORDER BY created_at_ms DESC",
                        (req, sid, pid),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT * FROM conversation_experience_event "
                        "WHERE request_id=? AND active=1 ORDER BY created_at_ms DESC",
                        (req,),
                    ).fetchall()
            else:
                rows = []
            if not rows and sid:
                row = conn.execute(
                    "SELECT * FROM conversation_experience_event "
                    "WHERE session_id=? AND person_id=? AND active=1 "
                    "ORDER BY created_at_ms DESC LIMIT 1",
                    (sid, pid),
                ).fetchone()
                if row is None:
                    row = conn.execute(
                        "SELECT * FROM conversation_experience_event "
                        "WHERE person_id=? AND active=1 ORDER BY created_at_ms DESC LIMIT 1",
                        (pid,),
                    ).fetchone()
                if row is None:
                    row = conn.execute(
                        "SELECT * FROM conversation_experience_event "
                        "WHERE active=1 ORDER BY created_at_ms DESC LIMIT 1"
                    ).fetchone()
                rows = [row] if row is not None else []
            if not rows:
                return {"updated": 0, "replayed": False}
            replayed = True
            for row in rows:
                current_reply = str(row["assistant_reply"] or "")
                current_outcome = str(row["outcome_signal"] or "unknown")
                if current_reply == assistant_reply and current_outcome == outcome_signal:
                    continue
                if current_reply and current_reply != assistant_reply:
                    raise ExperienceRepositoryError(
                        EXPERIENCE_REPLAY_CONFLICT,
                        f"event_id {row['event_id']} assistant reply changed",
                    )
                if (
                    current_outcome not in {"", "unknown", "replied"}
                    and current_outcome != outcome_signal
                ):
                    raise ExperienceRepositoryError(
                        EXPERIENCE_REPLAY_CONFLICT,
                        f"event_id {row['event_id']} outcome changed",
                    )
                replayed = False
                conn.execute(
                    "UPDATE conversation_experience_event SET assistant_reply=?, "
                    "outcome_signal=?, updated_at_ms=? WHERE event_id=?",
                    (
                        assistant_reply,
                        outcome_signal,
                        int(updated_at_ms),
                        row["event_id"],
                    ),
                )
            return {"updated": len(rows), "replayed": replayed}

    def recall_lessons(self, *, person_id: str) -> list[dict[str, Any]]:
        pid = _text(person_id) or "default"
        with self._read() as conn:
            rows = conn.execute(
                "SELECT lesson_id, person_id, domain, pattern, lesson, "
                "source_event_ids_json, success_count, failure_count, confidence, "
                "updated_at_ms FROM conversation_experience_lesson "
                "WHERE person_id=? AND active=1 ORDER BY updated_at_ms DESC LIMIT 80",
                (pid,),
            ).fetchall()
            if not rows:
                rows = conn.execute(
                    "SELECT lesson_id, person_id, domain, pattern, lesson, "
                    "source_event_ids_json, success_count, failure_count, confidence, "
                    "updated_at_ms FROM conversation_experience_lesson "
                    "WHERE active=1 ORDER BY updated_at_ms DESC LIMIT 80"
                ).fetchall()
        return [_row_dict(row) for row in rows]

    def record_outcome_feedback(
        self,
        *,
        session_id: str,
        person_id: str,
        request_id: str,
        signal: str,
        updated_at_ms: int,
    ) -> dict[str, Any]:
        sid = _text(session_id, "session_id", required=True)
        pid = _text(person_id) or "default"
        with self._write() as conn:
            row = conn.execute(
                "SELECT * FROM conversation_experience_event "
                "WHERE session_id=? AND person_id=? AND active=1 "
                "ORDER BY created_at_ms DESC LIMIT 1",
                (sid, pid),
            ).fetchone()
            if row is None:
                row = conn.execute(
                    "SELECT * FROM conversation_experience_event "
                    "WHERE person_id=? AND active=1 ORDER BY created_at_ms DESC LIMIT 1",
                    (pid,),
                ).fetchone()
            if row is None:
                row = conn.execute(
                    "SELECT * FROM conversation_experience_event "
                    "WHERE active=1 ORDER BY created_at_ms DESC LIMIT 1"
                ).fetchone()
            if row is None:
                return {"ok": False, "reason": "NO_PREVIOUS_EVENT", "signal": signal}
            event_id = str(row["event_id"])
            previous_signal = str(row["outcome_signal"] or "unknown")
            if previous_signal == signal:
                return {
                    "ok": True,
                    "signal": signal,
                    "event_id": event_id,
                    "request_id": request_id,
                    "lesson_updates": [],
                    "replayed": True,
                }
            if previous_signal not in {"unknown", "replied", ""}:
                raise ExperienceRepositoryError(
                    EXPERIENCE_REPLAY_CONFLICT,
                    f"event_id {event_id} outcome changed from {previous_signal} to {signal}",
                )
            conn.execute(
                "UPDATE conversation_experience_event SET outcome_signal=?, "
                "updated_at_ms=? WHERE event_id=?",
                (signal, int(updated_at_ms), event_id),
            )
            understanding = _mapping(_loads(row["understanding_json"], {}))
            lesson_ids = [
                _text(item.get("lesson_id"))
                for item in understanding.get("experience_lessons", [])
                if isinstance(item, Mapping) and _text(item.get("lesson_id"))
            ]
            if not lesson_ids:
                found = conn.execute(
                    "SELECT lesson_id FROM conversation_experience_lesson "
                    "WHERE source_event_ids_json LIKE ? AND active=1 LIMIT 20",
                    (f"%{event_id}%",),
                ).fetchall()
                lesson_ids.extend(str(item["lesson_id"]) for item in found)
            updates: list[dict[str, Any]] = []
            for lesson_id in dict.fromkeys(lesson_ids):
                lesson_row = conn.execute(
                    "SELECT success_count, failure_count, confidence "
                    "FROM conversation_experience_lesson "
                    "WHERE lesson_id=? AND active=1 LIMIT 1",
                    (lesson_id,),
                ).fetchone()
                if lesson_row is None:
                    continue
                success_count = int(lesson_row["success_count"] or 0)
                failure_count = int(lesson_row["failure_count"] or 0)
                confidence = float(lesson_row["confidence"] or 0.0)
                if signal == "success":
                    success_count += 1
                    confidence = min(1.0, confidence + 0.04)
                else:
                    failure_count += 1
                    confidence = max(0.05, confidence - 0.05)
                confidence = round(confidence, 4)
                conn.execute(
                    "UPDATE conversation_experience_lesson SET success_count=?, "
                    "failure_count=?, confidence=?, updated_at_ms=? WHERE lesson_id=?",
                    (
                        success_count,
                        failure_count,
                        confidence,
                        int(updated_at_ms),
                        lesson_id,
                    ),
                )
                updates.append(
                    {
                        "lesson_id": lesson_id,
                        "success_count": success_count,
                        "failure_count": failure_count,
                        "confidence": confidence,
                    }
                )
            return {
                "ok": True,
                "signal": signal,
                "event_id": event_id,
                "request_id": request_id,
                "lesson_updates": updates,
                "replayed": False,
            }

    def record_correction(
        self,
        *,
        correction: Mapping[str, Any],
        lessons: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        item = _mapping(correction)
        correction_id = _text(
            item.get("correction_id"), "correction_id", required=True
        )
        with self._write() as conn:
            row = conn.execute(
                "SELECT * FROM conversation_correction_event WHERE correction_id=?",
                (correction_id,),
            ).fetchone()
            if row is not None:
                self._assert_correction_replay(row, item)
                for lesson in lessons:
                    self._assert_lesson_replay(conn, _mapping(lesson))
                return {"correction_id": correction_id, "replayed": True}
            self._insert_correction(conn, item)
            for lesson in lessons:
                self._upsert_lesson(conn, _mapping(lesson))
        return {"correction_id": correction_id, "replayed": False}

    def _insert_correction(
        self, conn: sqlite3.Connection, correction: Mapping[str, Any]
    ) -> None:
        item = _mapping(correction)
        correction_id = _text(
            item.get("correction_id"), "correction_id", required=True
        )
        conn.execute(
            """
            INSERT INTO conversation_correction_event(
                correction_id, event_id, request_id, session_id, person_id,
                raw_text, wrong_inference_json, correct_reference_json,
                repair_lesson, confidence, created_at_ms, active
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
            """,
            (
                correction_id,
                _text(item.get("event_id")),
                _text(item.get("request_id")),
                _text(item.get("session_id"), "session_id", required=True),
                _text(item.get("person_id")) or "default",
                _text(item.get("raw_text")),
                _json(_mapping(item.get("wrong_inference"))),
                _json(_mapping(item.get("correct_reference"))),
                _text(item.get("repair_lesson")),
                round(float(item.get("confidence") or 0.0), 4),
                int(item.get("created_at_ms") or 0),
            ),
        )

    def _assert_correction_replay(
        self, row: Mapping[str, Any], correction: Mapping[str, Any]
    ) -> None:
        expected = {
            "correction_id": _text(correction.get("correction_id")),
            "event_id": _text(correction.get("event_id")),
            "request_id": _text(correction.get("request_id")),
            "session_id": _text(correction.get("session_id")),
            "person_id": _text(correction.get("person_id")) or "default",
            "raw_text": _text(correction.get("raw_text")),
            "wrong_inference_json": _mapping(correction.get("wrong_inference")),
            "correct_reference_json": _mapping(correction.get("correct_reference")),
            "repair_lesson": _text(correction.get("repair_lesson")),
            "confidence": round(float(correction.get("confidence") or 0.0), 4),
        }
        for key, value in expected.items():
            actual = row[key]
            if key in _JSON_FIELDS:
                actual = _normalized_json(actual)
            elif key == "confidence":
                actual = round(float(actual or 0.0), 4)
            if actual != value:
                raise ExperienceRepositoryError(
                    EXPERIENCE_REPLAY_CONFLICT,
                    f"correction_id {expected['correction_id']} replay changed {key}",
                )

    def _upsert_lesson(self, conn: sqlite3.Connection, lesson: Mapping[str, Any]) -> None:
        item = _mapping(lesson)
        lesson_id = _text(item.get("lesson_id"), "lesson_id", required=True)
        person_id = _text(item.get("person_id")) or "default"
        domain = _text(item.get("domain")) or "general"
        pattern = _text(item.get("pattern")) or "conversation_repair"
        lesson_text = _text(item.get("lesson"))
        source_event_id = _text(item.get("source_event_id"))
        confidence = round(float(item.get("confidence") or 0.0), 4)
        current = conn.execute(
            "SELECT * FROM conversation_experience_lesson WHERE lesson_id=? LIMIT 1",
            (lesson_id,),
        ).fetchone()
        if current is None:
            sources = [source_event_id] if source_event_id else []
            conn.execute(
                """
                INSERT INTO conversation_experience_lesson(
                    lesson_id, person_id, domain, pattern, lesson,
                    source_event_ids_json, success_count, failure_count,
                    confidence, updated_at_ms, active
                ) VALUES(?, ?, ?, ?, ?, ?, 0, 1, ?, ?, 1)
                """,
                (
                    lesson_id,
                    person_id,
                    domain,
                    pattern,
                    lesson_text,
                    _json(sources),
                    confidence,
                    int(item.get("updated_at_ms") or 0),
                ),
            )
            return
        sources = [str(value) for value in _loads(current["source_event_ids_json"], [])]
        if source_event_id and source_event_id in sources:
            self._assert_lesson_values(current, item)
            return
        if source_event_id:
            sources.append(source_event_id)
        conn.execute(
            "UPDATE conversation_experience_lesson SET person_id=?, domain=?, "
            "pattern=?, lesson=?, source_event_ids_json=?, failure_count=?, "
            "confidence=?, updated_at_ms=?, active=1 WHERE lesson_id=?",
            (
                person_id,
                domain,
                pattern,
                lesson_text,
                _json(sources[-20:]),
                int(current["failure_count"] or 0) + 1,
                max(confidence, float(current["confidence"] or 0.0)),
                int(item.get("updated_at_ms") or 0),
                lesson_id,
            ),
        )

    def _assert_lesson_replay(
        self, conn: sqlite3.Connection, lesson: Mapping[str, Any]
    ) -> None:
        item = _mapping(lesson)
        row = conn.execute(
            "SELECT * FROM conversation_experience_lesson WHERE lesson_id=? LIMIT 1",
            (_text(item.get("lesson_id")),),
        ).fetchone()
        if row is None:
            raise ExperienceRepositoryError(
                EXPERIENCE_DURABLE_DATA_INVALID,
                "replayed correction is missing its lesson",
            )
        source_event_id = _text(item.get("source_event_id"))
        sources = [str(value) for value in _loads(row["source_event_ids_json"], [])]
        if source_event_id and source_event_id not in sources:
            raise ExperienceRepositoryError(
                EXPERIENCE_DURABLE_DATA_INVALID,
                "replayed correction lesson lost its source event",
            )
        self._assert_lesson_values(row, item)

    def _assert_lesson_values(
        self, row: Mapping[str, Any], lesson: Mapping[str, Any]
    ) -> None:
        expected = {
            "person_id": _text(lesson.get("person_id")) or "default",
            "domain": _text(lesson.get("domain")) or "general",
            "pattern": _text(lesson.get("pattern")) or "conversation_repair",
            "lesson": _text(lesson.get("lesson")),
        }
        for key, value in expected.items():
            if str(row[key] or "") != value:
                raise ExperienceRepositoryError(
                    EXPERIENCE_REPLAY_CONFLICT,
                    f"lesson_id {row['lesson_id']} replay changed {key}",
                )

    # -- Memory context read model ----------------------------------------

    def existing_conversation_turn_life_keys(
        self,
        turn_keys: Sequence[tuple[str, str]],
    ) -> frozenset[tuple[str, str]]:
        """Resolve conversation turns already projected into the Life stream."""

        normalized = tuple(
            dict.fromkeys(
                (
                    _text(request_id),
                    _text(role).lower(),
                )
                for request_id, role in turn_keys
                if _text(request_id) and _text(role)
            )
        )
        if not normalized:
            return frozenset()
        found: set[tuple[str, str]] = set()
        with self._read() as conn:
            if not self._table_exists(conn, "life_experience_event"):
                return frozenset()
            for start in range(0, len(normalized), 200):
                chunk = normalized[start : start + 200]
                predicates = " OR ".join(
                    "(json_extract(evidence_json, '$.request_id') = ? "
                    "AND json_extract(evidence_json, '$.role') = ?)"
                    for _ in chunk
                )
                params = tuple(value for key in chunk for value in key)
                try:
                    rows = conn.execute(
                        "SELECT evidence_json FROM life_experience_event "
                        "WHERE source_kind LIKE 'conversation_turn.%' AND ("
                        + predicates
                        + ")",
                        params,
                    ).fetchall()
                except sqlite3.DatabaseError:
                    # Compatibility fallback for old SQLite builds or malformed
                    # historical JSON mirrors the legacy boundary's exact match.
                    for request_id, role in chunk:
                        needle_request = f'\"request_id\":\"{request_id}\"'
                        needle_role = f'\"role\":\"{role}\"'
                        row = conn.execute(
                            "SELECT 1 FROM life_experience_event "
                            "WHERE source_kind LIKE 'conversation_turn.%' "
                            "AND REPLACE(evidence_json, ' ', '') LIKE ? "
                            "AND REPLACE(evidence_json, ' ', '') LIKE ? LIMIT 1",
                            (f"%{needle_request}%", f"%{needle_role}%"),
                        ).fetchone()
                        if row is not None:
                            found.add((request_id, role))
                    continue
                for row in rows:
                    evidence = _mapping(_loads(row["evidence_json"], {}))
                    key = (
                        _text(evidence.get("request_id")),
                        _text(evidence.get("role")).lower(),
                    )
                    if key in chunk:
                        found.add(key)
        return frozenset(found)

    def has_conversation_turn_life_event(
        self,
        *,
        request_id: str,
        role: str,
    ) -> bool:
        key = (_text(request_id), _text(role).lower())
        if not all(key):
            return False
        return key in self.existing_conversation_turn_life_keys((key,))

    def count_conversation_turn_memory_spine_gaps(
        self,
        *,
        person_id: str | None,
        session_id: str,
        limit: int,
        include_asr_sessions: bool,
    ) -> int:
        """Count recent canonical turns not yet represented in the Life stream."""

        person = _text(person_id)
        session = _text(session_id)
        capped = max(1, min(int(limit or 2_000), 10_000))
        clauses = ["t.role IN ('user', 'assistant')", "COALESCE(t.content, '') != ''"]
        params: list[Any] = []
        if person:
            clauses.append("(t.person_id = ? OR COALESCE(t.person_id, '') = '')")
            params.append(person)
        if session:
            clauses.append("t.session_id = ?")
            params.append(session)
        elif not include_asr_sessions:
            clauses.extend(
                (
                    "t.session_id NOT LIKE 'asr_req_%'",
                    "t.session_id NOT LIKE 'codex_%'",
                    "t.session_id NOT LIKE '__codex_%'",
                )
            )
        with self._read() as conn:
            if not self._table_exists(conn, "conversation_turn") or not self._table_exists(
                conn, "life_experience_event"
            ):
                return 0
            row = conn.execute(
                "SELECT COUNT(*) FROM ("
                "SELECT t.request_id, t.role FROM conversation_turn t WHERE "
                + " AND ".join(clauses)
                + " ORDER BY t.ts_ms DESC LIMIT ?) t WHERE NOT EXISTS ("
                "SELECT 1 FROM life_experience_event e "
                "WHERE e.source_kind LIKE 'conversation_turn.%' "
                "AND json_extract(e.evidence_json, '$.request_id') = t.request_id "
                "AND json_extract(e.evidence_json, '$.role') = t.role)",
                (*params, capped),
            ).fetchone()
        return int(row[0] or 0) if row is not None else 0

    @staticmethod
    def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
        return bool(
            conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
                (name,),
            ).fetchone()
        )

    def load_memory_context_snapshot(
        self,
        *,
        person_id: str,
        session_id: str,
        request_id: str,
        current_user_message: str,
        now_ms: int,
        limit: int,
    ) -> dict[str, Any]:
        pid = _text(person_id) or "default"
        sid = _text(session_id)
        rid = _text(request_id)
        row_limit = max(1, min(int(limit or 8), 50))
        with self._read() as conn:
            boundary = self._current_request_boundary(
                conn, person_id=pid, session_id=sid, request_id=rid
            )
            visible = self._recent_memory_events(
                conn,
                person_id=pid,
                session_id=sid,
                visibility="visible",
                limit=row_limit,
                before_ms=boundary or None,
            )
            if not boundary:
                target = _compact_text(current_user_message, 500)
                for event in reversed(visible):
                    if str(event.get("role") or "") != "user":
                        continue
                    if _compact_text(
                        event.get("content") or event.get("content_summary"), 500
                    ) == target:
                        boundary = int(event.get("created_at_ms") or 0)
                        break
            if boundary:
                visible = [
                    event
                    for event in visible
                    if int(event.get("created_at_ms") or 0) <= boundary
                ]
            private_events = self._recent_memory_events(
                conn,
                person_id=pid,
                session_id=sid,
                visibility="private",
                limit=5,
            )
            recent_turns = self._recent_turns(
                conn, session_id=sid, limit=row_limit, before_ms=boundary or None
            )
            proactive_candidates = self._recent_turns(
                conn, session_id=sid, limit=12, before_ms=boundary or None
            )
            proactive_turn = next(
                (
                    turn
                    for turn in reversed(proactive_candidates)
                    if turn.get("role") == "assistant"
                    and _mapping(turn.get("metadata")).get("source_kind")
                    == "life_proactive"
                ),
                {},
            )
            pending = self._pending_proactive_episode(conn, pid, int(now_ms))
            belief = self._belief_snapshot(conn, sid, pid)
            preferences = self._preference_profiles(conn, pid)
            deliveries = self._recommendation_deliveries(conn, pid, sid)
            semantic = self._semantic_memories(conn)
            lessons = self._experience_lesson_rows(conn, pid)
            life_state = self._digital_life_state(conn, pid)
            try:
                from host_brain.app.conversation_event_resolver import (  # noqa: PLC0415
                    resolve_conversation_event_action,
                )

                resolution = resolve_conversation_event_action(
                    ledger=conn,
                    person_id=pid,
                    session_id=sid,
                    current_user_message=current_user_message,
                    now_ms=int(now_ms),
                    before_ms=boundary or None,
                )
            except Exception as exc:
                resolution = {
                    "schema_version": "Natsume.conversation_event_resolution.v1",
                    "status": "UNRESOLVED",
                    "reason_code": "CONVERSATION_EVENT_RESOLVER_ERROR",
                    "error": str(exc)[:200],
                }
        return {
            "current_boundary_ms": boundary,
            "recent_visible_events": visible,
            "private_events": private_events,
            "recent_turns": recent_turns,
            "proactive_turn": proactive_turn,
            "pending_proactive_episode": pending,
            "conversation_event_resolution": resolution,
            "belief": belief,
            "preference_profiles": preferences,
            "recommendation_deliveries": deliveries,
            "semantic_memories": semantic,
            "lessons": lessons,
            "digital_life_state": life_state,
        }

    def _current_request_boundary(
        self,
        conn: sqlite3.Connection,
        *,
        person_id: str,
        session_id: str,
        request_id: str,
    ) -> int:
        if not request_id or not self._table_exists(conn, "memory_event"):
            return 0
        clauses = [
            "person_id=?",
            "role='user'",
            "active=1",
            "json_extract(refs_json, '$.request_id')=?",
        ]
        params: list[Any] = [person_id, request_id]
        if session_id:
            clauses.append("session_id=?")
            params.append(session_id)
        try:
            row = conn.execute(
                "SELECT created_at_ms FROM memory_event WHERE "
                + " AND ".join(clauses)
                + " ORDER BY created_at_ms ASC, id ASC LIMIT 1",
                params,
            ).fetchone()
        except sqlite3.DatabaseError:
            return 0
        return int(row["created_at_ms"] or 0) if row else 0

    def _recent_memory_events(
        self,
        conn: sqlite3.Connection,
        *,
        person_id: str,
        session_id: str,
        visibility: str,
        limit: int,
        before_ms: int | None = None,
    ) -> list[dict[str, Any]]:
        if not self._table_exists(conn, "memory_event"):
            return []
        clauses = ["person_id=?", "active=1"]
        params: list[Any] = [person_id]
        if session_id:
            clauses.append("session_id=?")
            params.append(session_id)
        if visibility:
            clauses.append("visibility=?")
            params.append(visibility)
        if before_ms:
            clauses.append("created_at_ms <= ?")
            params.append(int(before_ms))
        params.append(max(1, min(int(limit), 50)))
        rows = conn.execute(
            "SELECT event_id, session_id, conversation_id, source_kind, "
            "event_kind, visibility, role, content, content_summary, refs_json, "
            "projection_status_json, created_at_ms FROM memory_event WHERE "
            + " AND ".join(clauses)
            + " ORDER BY created_at_ms DESC, id DESC LIMIT ?",
            params,
        ).fetchall()
        events = []
        for row in rows:
            item = _row_dict(row)
            item["refs"] = _loads(item.pop("refs_json", "{}"), {})
            item["projection_status"] = _loads(
                item.pop("projection_status_json", "{}"), {}
            )
            events.append(item)
        return list(reversed(events))

    def _recent_turns(
        self,
        conn: sqlite3.Connection,
        *,
        session_id: str,
        limit: int,
        before_ms: int | None,
    ) -> list[dict[str, Any]]:
        if not session_id or not self._table_exists(conn, "conversation_turn"):
            return []
        clauses = ["session_id=?"]
        params: list[Any] = [session_id]
        if before_ms:
            clauses.append("ts_ms <= ?")
            params.append(int(before_ms))
        params.append(max(1, int(limit)))
        rows = conn.execute(
            "SELECT turn_id, request_id, role, content, ts_ms, turn_index, metadata "
            "FROM conversation_turn WHERE "
            + " AND ".join(clauses)
            + " ORDER BY ts_ms DESC LIMIT ?",
            params,
        ).fetchall()
        result = []
        for row in reversed(rows):
            item = _row_dict(row)
            item["metadata"] = _mapping(_loads(item.get("metadata"), {}))
            result.append(item)
        return result

    def _pending_proactive_episode(
        self, conn: sqlite3.Connection, person_id: str, now_ms: int
    ) -> dict[str, Any]:
        if not self._table_exists(conn, "proactive_interaction_episode"):
            return {}
        row = conn.execute(
            "SELECT episode_id, source_action_id, source_outbox_id, motive, "
            "message_text, expected_response_kind, sent_at_ms, metadata_json "
            "FROM proactive_interaction_episode WHERE person_id=? AND outcome='pending' "
            "ORDER BY sent_at_ms DESC LIMIT 1",
            (person_id,),
        ).fetchone()
        if row is None:
            return {}
        sent_at = int(row["sent_at_ms"] or 0)
        if sent_at and now_ms - sent_at > 3_600_000:
            return {}
        item = _row_dict(row)
        item["sent_at_ms"] = sent_at
        item["metadata"] = _mapping(_loads(item.pop("metadata_json", "{}"), {}))
        return item

    def _belief_snapshot(
        self, conn: sqlite3.Connection, session_id: str, person_id: str
    ) -> dict[str, Any]:
        if not session_id or not self._table_exists(conn, "conversation_belief_state"):
            return {}
        row = conn.execute(
            "SELECT belief_json, updated_at_ms FROM conversation_belief_state "
            "WHERE session_id=? AND person_id=? LIMIT 1",
            (session_id, person_id),
        ).fetchone()
        if row is None:
            return {}
        belief = _mapping(_loads(row["belief_json"], {}))
        belief["_updated_at_ms"] = int(row["updated_at_ms"] or 0)
        return belief

    def _preference_profiles(
        self, conn: sqlite3.Connection, person_id: str
    ) -> list[dict[str, Any]]:
        if not self._table_exists(conn, "user_preference_profile"):
            return []
        rows = conn.execute(
            "SELECT domain, profile_json, updated_at_ms FROM user_preference_profile "
            "WHERE person_id=? ORDER BY updated_at_ms DESC LIMIT 5",
            (person_id,),
        ).fetchall()
        return [
            {
                "domain": row["domain"],
                "profile": _loads(row["profile_json"], {}),
                "updated_at_ms": row["updated_at_ms"],
            }
            for row in rows
        ]

    def _recommendation_deliveries(
        self, conn: sqlite3.Connection, person_id: str, session_id: str
    ) -> list[dict[str, Any]]:
        if not self._table_exists(conn, "recommendation_delivery_log"):
            return []
        rows = conn.execute(
            "SELECT delivery_id, domain, items_json, created_at_ms "
            "FROM recommendation_delivery_log WHERE active=1 AND "
            "(person_id=? OR (? != '' AND session_id=?) OR "
            "(? != '' AND conversation_id=?)) "
            "ORDER BY created_at_ms DESC LIMIT 3",
            (person_id, session_id, session_id, session_id, session_id),
        ).fetchall()
        return [
            {
                "delivery_id": row["delivery_id"],
                "domain": row["domain"],
                "items": _loads(row["items_json"], []),
                "created_at_ms": row["created_at_ms"],
            }
            for row in rows
        ]

    def _semantic_memories(self, conn: sqlite3.Connection) -> list[dict[str, Any]]:
        if not self._table_exists(conn, "semantic_memory"):
            return []
        rows = conn.execute(
            "SELECT semantic_id, summary, confidence, tags, ts_ms "
            "FROM semantic_memory WHERE deleted_at IS NULL "
            "ORDER BY ts_ms DESC LIMIT 8"
        ).fetchall()
        return [
            {
                "semantic_id": row["semantic_id"],
                "summary": row["summary"],
                "confidence": row["confidence"],
                "tags": _loads(row["tags"], []),
                "ts_ms": row["ts_ms"],
            }
            for row in rows
        ]

    def _experience_lesson_rows(
        self, conn: sqlite3.Connection, person_id: str
    ) -> list[dict[str, Any]]:
        rows = conn.execute(
            "SELECT lesson_id, domain, pattern, lesson, success_count, "
            "failure_count, confidence, updated_at_ms "
            "FROM conversation_experience_lesson WHERE person_id=? AND active=1 "
            "ORDER BY updated_at_ms DESC LIMIT 8",
            (person_id,),
        ).fetchall()
        return [_row_dict(row) for row in rows]

    def _digital_life_state(
        self, conn: sqlite3.Connection, person_id: str
    ) -> dict[str, Any]:
        if not self._table_exists(conn, "digital_life_state"):
            return {}
        row = conn.execute(
            "SELECT state_json FROM digital_life_state WHERE person_id=? LIMIT 1",
            (person_id,),
        ).fetchone()
        return _mapping(_loads(row["state_json"], {})) if row else {}

    # -- Memory health and sanitation -------------------------------------

    def memory_health(
        self, *, person_id: str, now_ms: int, belief_ttl_ms: int
    ) -> dict[str, int]:
        pid = _text(person_id) or "default"
        with self._read() as conn:
            def count_if(table: str, sql: str, params: Sequence[Any] = ()) -> int:
                if not self._table_exists(conn, table):
                    return 0
                return int(conn.execute(sql, tuple(params)).fetchone()[0] or 0)

            return {
                "bad_unresolved_preferences": count_if(
                    "semantic_memory",
                    "SELECT COUNT(*) FROM semantic_memory WHERE deleted_at IS NULL "
                    "AND (summary LIKE '%这本%喜欢%' OR summary LIKE '%那本%喜欢%' "
                    "OR summary LIKE '%这个%不错%' OR summary LIKE '%那个%不错%')",
                ),
                "active_failed_lessons": count_if(
                    "conversation_experience_lesson",
                    "SELECT COUNT(*) FROM conversation_experience_lesson "
                    "WHERE active=1 AND failure_count > success_count AND confidence < 0.85",
                ),
                "cross_session_belief_leaks": count_if(
                    "conversation_belief_state",
                    "SELECT COUNT(*) FROM conversation_belief_state WHERE person_id=? "
                    "AND json_extract(belief_json, '$.can_drive_reply') IS NOT 0 "
                    "AND json_extract(belief_json, '$.scope') != 'session'",
                    (pid,),
                ),
                "stale_active_topics": count_if(
                    "conversation_belief_state",
                    "SELECT COUNT(*) FROM conversation_belief_state WHERE person_id=? "
                    "AND json_extract(belief_json, '$.can_drive_reply') IS NOT 0 "
                    "AND COALESCE(json_extract(belief_json, '$.expires_at_ms'), "
                    "updated_at_ms + ?) < ?",
                    (pid, int(belief_ttl_ms), int(now_ms)),
                ),
                "proactive_turn_write_failures": count_if(
                    "proactive_life_outbox",
                    "SELECT COUNT(*) FROM proactive_life_outbox p WHERE p.status='SENT' "
                    "AND NOT EXISTS (SELECT 1 FROM conversation_turn t "
                    "WHERE t.request_id=p.outbox_id AND t.role='assistant')",
                ),
                "conversation_turn_spine_gaps": 0,
                "semantic_memory_without_spine_source": count_if(
                    "semantic_memory",
                    "SELECT COUNT(*) FROM semantic_memory WHERE deleted_at IS NULL "
                    "AND source_memory_ids NOT LIKE '%lifeexp_%' "
                    "AND source_memory_ids NOT LIKE '%turn::%' "
                    "AND source_memory_ids NOT LIKE '%conversation_turn%' "
                    "AND source_memory_ids NOT LIKE '%life_episode::%' "
                    "AND source_memory_ids NOT LIKE '%life_memory_collection::%'",
                ),
            }

    def sanitize_memory(
        self,
        *,
        person_id: str,
        now_ms: int,
        dry_run: bool,
    ) -> dict[str, Any]:
        pid = _text(person_id) or "default"
        summary: dict[str, Any] = {
            "belief_deleted": 0,
            "lessons_disabled": 0,
            "semantic_soft_deleted": 0,
            "life_memory_soft_deleted": 0,
            "proactive_outbox_marked": 0,
            "proactive_turns_backfilled": 0,
            "reason_counts": {},
            "dry_run": bool(dry_run),
        }

        def bump(reason: str, count: int = 1) -> None:
            if count:
                reasons = summary["reason_counts"]
                reasons[reason] = int(reasons.get(reason, 0)) + int(count)

        manager = self._read() if dry_run else self._write()
        with manager as conn:
            if self._table_exists(conn, "conversation_belief_state"):
                rows = conn.execute(
                    "SELECT session_id, person_id, belief_json, updated_at_ms "
                    "FROM conversation_belief_state WHERE person_id=?",
                    (pid,),
                ).fetchall()
                for row in rows:
                    belief = _mapping(_loads(row["belief_json"], {}))
                    sid = str(row["session_id"] or "")
                    reasons = []
                    if sid.startswith("codex_") or sid.startswith("asr_req_"):
                        reasons.append("TEST_OR_ASR_SESSION_BELIEF")
                    if not self._belief_can_drive_reply(
                        belief, now_ms=int(now_ms)
                    ):
                        reasons.append("STALE_OR_LOW_CONFIDENCE_BELIEF")
                    if not reasons:
                        continue
                    belief.update(
                        {
                            "can_drive_reply": False,
                            "suppressed_reason": ",".join(sorted(set(reasons))),
                            "suppressed_at_ms": int(now_ms),
                        }
                    )
                    summary["belief_deleted"] += 1
                    bump("belief:" + reasons[0])
                    if not dry_run:
                        conn.execute(
                            "UPDATE conversation_belief_state SET belief_json=?, "
                            "updated_at_ms=? WHERE session_id=? AND person_id=?",
                            (
                                _json(belief),
                                int(now_ms),
                                row["session_id"],
                                row["person_id"],
                            ),
                        )

            failed_where = (
                "active=1 AND failure_count > success_count AND confidence < 0.85"
            )
            if dry_run:
                count = int(
                    conn.execute(
                        "SELECT COUNT(*) FROM conversation_experience_lesson WHERE "
                        + failed_where
                    ).fetchone()[0]
                    or 0
                )
            else:
                count = int(
                    conn.execute(
                        "UPDATE conversation_experience_lesson SET active=0 WHERE "
                        + failed_where
                    ).rowcount
                    or 0
                )
            summary["lessons_disabled"] += count
            bump("lesson:failed_more_than_success", count)

            semantic_where = (
                "deleted_at IS NULL AND (summary LIKE '%这本%喜欢%' "
                "OR summary LIKE '%那本%喜欢%' OR summary LIKE '%这个%不错%' "
                "OR summary LIKE '%那个%不错%' "
                "OR summary LIKE '稳定事实：skill | %FAILED%' "
                "OR summary LIKE '%测试代号%')"
            )
            if self._table_exists(conn, "semantic_memory"):
                if dry_run:
                    count = int(
                        conn.execute(
                            "SELECT COUNT(*) FROM semantic_memory WHERE " + semantic_where
                        ).fetchone()[0]
                        or 0
                    )
                else:
                    count = int(
                        conn.execute(
                            "UPDATE semantic_memory SET deleted_at=? WHERE "
                            + semantic_where,
                            (int(now_ms),),
                        ).rowcount
                        or 0
                    )
                summary["semantic_soft_deleted"] += count
                bump("semantic:dirty_or_unresolved", count)

            if self._table_exists(conn, "life_memory_collection"):
                collection_where = (
                    "retention_state NOT LIKE 'suppressed%' AND "
                    "(session_id LIKE 'codex_%' OR title LIKE '%测试%')"
                )
                if dry_run:
                    count = int(
                        conn.execute(
                            "SELECT COUNT(*) FROM life_memory_collection WHERE "
                            + collection_where
                        ).fetchone()[0]
                        or 0
                    )
                else:
                    count = int(
                        conn.execute(
                            "UPDATE life_memory_collection SET "
                            "retention_state='suppressed_memory_sanitize', "
                            "updated_ts_ms=? WHERE "
                            + collection_where,
                            (int(now_ms),),
                        ).rowcount
                        or 0
                    )
                summary["life_memory_soft_deleted"] += count
                bump("life_memory:test_collection", count)

            if self._table_exists(conn, "proactive_life_outbox"):
                rows = conn.execute(
                    "SELECT outbox_id, message_json, gate_json "
                    "FROM proactive_life_outbox ORDER BY created_at_ms DESC LIMIT 500"
                ).fetchall()
                marked = 0
                for row in rows:
                    message = _mapping(_loads(row["message_json"], {}))
                    content = _compact_text(message.get("content"), 500)
                    if not content or not _UNSUPPORTED_PROACTIVE_ACTION_RE.search(content):
                        continue
                    gate = _mapping(_loads(row["gate_json"], {}))
                    gate["content_quality"] = {
                        "status": "REJECTED_AFTER_AUDIT",
                        "reason_code": "UNSUPPORTED_PROACTIVE_ACTION",
                    }
                    marked += 1
                    if not dry_run:
                        conn.execute(
                            "UPDATE proactive_life_outbox SET gate_json=? WHERE outbox_id=?",
                            (_json(gate), row["outbox_id"]),
                        )
                summary["proactive_outbox_marked"] = marked
                bump("proactive:unsupported_action", marked)
                backfilled = self._backfill_proactive_turns(
                    conn,
                    person_id=pid,
                    now_ms=int(now_ms),
                    dry_run=bool(dry_run),
                )
                summary["proactive_turns_backfilled"] = backfilled
                bump("proactive:turn_backfilled", backfilled)

            if not dry_run:
                conn.execute(
                    "INSERT INTO memory_sanitize_audit(person_id, summary_json, created_at_ms) "
                    "VALUES(?, ?, ?)",
                    (pid, _json(summary), int(now_ms)),
                )
        return summary

    @staticmethod
    def _belief_can_drive_reply(
        belief: Mapping[str, Any], *, now_ms: int
    ) -> bool:
        item = _mapping(belief)
        if item.get("can_drive_reply") is False:
            return False
        try:
            expires_at = int(item.get("expires_at_ms") or 0)
        except (TypeError, ValueError):
            expires_at = 0
        if expires_at and int(now_ms) > expires_at:
            return False
        selected = _compact_text(item.get("selected_intent"), 80)
        confidence = float(item.get("confidence") or 0.0)
        return not (selected == "open_chat" and confidence < 0.55)

    def _backfill_proactive_turns(
        self,
        conn: sqlite3.Connection,
        *,
        person_id: str,
        now_ms: int,
        dry_run: bool,
    ) -> int:
        if not self._table_exists(conn, "conversation_turn"):
            return 0
        rows = conn.execute(
            "SELECT outbox_id, person_id, source_kind, motive, pressure, target_json, "
            "message_json, result_json, created_at_ms FROM proactive_life_outbox p "
            "WHERE p.person_id=? AND p.status='SENT' AND NOT EXISTS "
            "(SELECT 1 FROM conversation_turn t WHERE t.request_id=p.outbox_id "
            "AND t.role='assistant') ORDER BY p.created_at_ms ASC LIMIT 500",
            (person_id,),
        ).fetchall()
        backfilled = 0
        next_indexes: dict[str, int] = {}
        for row in rows:
            target = _mapping(_loads(row["target_json"], {}))
            message = _mapping(_loads(row["message_json"], {}))
            result = _mapping(_loads(row["result_json"], {}))
            content = _compact_text(
                message.get("content")
                or message.get("text")
                or message.get("narrative")
                or result.get("content")
                or result.get("text"),
                2000,
            )
            conversation_id = _compact_text(
                target.get("conversation_id")
                or target.get("reply_target")
                or target.get("target_id")
                or target.get("id"),
                160,
            )
            platform = _compact_text(
                target.get("platform")
                or target.get("reply_channel")
                or target.get("kind"),
                80,
            )
            session_id = _compact_text(target.get("session_id"), 180)
            if not session_id and conversation_id:
                if conversation_id.startswith(("qq_", "wx_", "telegram_", "discord_")):
                    session_id = conversation_id
                elif platform == "qq":
                    session_id = f"qq_dm_{conversation_id}"
                else:
                    session_id = conversation_id
            if not content or not session_id:
                continue
            if session_id not in next_indexes:
                row_index = conn.execute(
                    "SELECT COALESCE(MAX(turn_index), 0) + 1 FROM conversation_turn "
                    "WHERE session_id=?",
                    (session_id,),
                ).fetchone()
                next_indexes[session_id] = int(row_index[0] or 1)
            turn_index = next_indexes[session_id]
            next_indexes[session_id] += 1
            metadata = {
                "source_kind": "life_proactive",
                "backfilled": True,
                "life_source_kind": row["source_kind"],
                "motive": row["motive"],
                "pressure": float(row["pressure"] or 0.0),
                "proactive_outbox_id": row["outbox_id"],
                "reply_channel": platform,
                "reply_target": conversation_id,
            }
            backfilled += 1
            if not dry_run:
                conn.execute(
                    "INSERT OR IGNORE INTO conversation_turn(turn_id, session_id, "
                    "person_id, request_id, role, content, ts_ms, turn_index, lang, "
                    "token_count, corrupted, metadata, created_at_ms) "
                    "VALUES(?, ?, ?, ?, 'assistant', ?, ?, ?, 'zh', ?, 0, ?, ?)",
                    (
                        f"turn::life_proactive_backfill::{row['outbox_id']}",
                        session_id,
                        row["person_id"] or person_id,
                        row["outbox_id"],
                        content,
                        int(row["created_at_ms"] or now_ms),
                        turn_index,
                        max(1, len(content) // 2),
                        _json(metadata),
                        now_ms,
                    ),
                )
        return backfilled


__all__ = [
    "CONTEXT_MIGRATION_ASSET_KEY",
    "CONTEXT_MIGRATION_SHA256",
    "EXPERIENCE_DURABLE_DATA_INVALID",
    "EXPERIENCE_RECORD_INVALID",
    "EXPERIENCE_REPLAY_CONFLICT",
    "EXPERIENCE_SCHEMA_NOT_READY",
    "ExperienceRepository",
    "ExperienceRepositoryError",
]
