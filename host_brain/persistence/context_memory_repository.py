"""SQLite repository and UoWs for MemoryEvent and its turn projection."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager, nullcontext
import json
from pathlib import Path
import sqlite3
from typing import Any


MEMORY_EVENT_SCHEMA_NOT_READY = "MEMORY_EVENT_SCHEMA_NOT_READY"
MEMORY_EVENT_INVALID = "MEMORY_EVENT_INVALID"
MEMORY_EVENT_CONFLICT = "MEMORY_EVENT_CONFLICT"
MEMORY_EVENT_MIGRATION_ASSET_KEY = "0070#context_persistence"
MEMORY_EVENT_MIGRATION_SHA256 = (
    "faf991332cffab3f9f7307b2101e9c88d6443b5b32972b08a3be946d97a43e67"
)
_ALLOWED_DISPOSITIONS = frozenset(
    {"APPLIED", "APPLIED_WITH_COMPAT", "LEGACY_BACKFILL"}
)
_TURN_COLUMNS = (
    "turn_id", "session_id", "person_id", "request_id", "role", "content",
    "ts_ms", "turn_index", "lang", "token_count", "corrupted", "metadata",
)
_EVENT_COLUMNS = (
    "event_id", "person_id", "session_id", "conversation_id", "source_kind",
    "event_kind", "visibility", "role", "content", "content_summary",
    "causality_json", "refs_json", "salience", "valence", "uncertainty",
    "confidence", "projection_status_json", "created_at_ms", "active",
)
_REQUIRED_COLUMNS = {
    "memory_event": frozenset({"id", *_EVENT_COLUMNS}),
    "conversation_turn": frozenset({*_TURN_COLUMNS, "created_at_ms"}),
}
_REQUIRED_INDEXES = frozenset(
    {
        "idx_memory_event_id", "idx_memory_event_person_ts",
        "idx_memory_event_session_ts", "idx_memory_event_visibility_ts",
        "idx_memory_event_kind_ts", "idx_conversation_turn_session_ts",
        "idx_conversation_turn_session_turn_idx", "idx_conversation_turn_request",
        "idx_conversation_turn_session_request_role_unique",
    }
)
MEMORY_REBUILD_CLEAR_TABLES: tuple[str, ...] = (
    "memory_event", "conversation_turn", "life_experience_event",
    "proactive_interaction_episode", "proactive_life_outbox",
    "conversation_belief_state", "semantic_memory", "life_memory_entity_mention",
    "life_memory_collection_item", "life_memory_correction",
    "life_memory_compaction", "life_open_loop", "life_episode_link",
    "life_evidence", "life_memory_entity", "life_memory_collection", "life_episode",
    "user_preference_event", "user_preference_profile",
    "recommendation_delivery_log", "digital_life_state",
    "digital_life_state_event", "dialogue_life_influence_log",
    "embodiment_dispatch_log", "work_environment_event", "organ_runtime_state",
    "organ_runtime_event", "organ_coupling_event", "life_state_synthesis_log",
)


class MemoryEventRepositoryError(RuntimeError):
    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = str(reason_code or MEMORY_EVENT_INVALID)
        self.detail = str(detail or "memory event persistence failed")
        super().__init__(f"{self.reason_code}: {self.detail}")


def _required(value: Any, field_name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise MemoryEventRepositoryError(MEMORY_EVENT_INVALID, f"{field_name} is required")
    return normalized


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _loads(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value or ""))
    except (TypeError, json.JSONDecodeError):
        return None


class MemoryEventRepository:
    def __init__(
        self,
        db_path: str | Path,
        *,
        timeout_ms: int = 5_000,
        verify_schema: bool = True,
        _borrowed_connection: sqlite3.Connection | None = None,
        _borrowed_lock: Any | None = None,
    ) -> None:
        raw = str(db_path or "").strip()
        if not raw or (raw == ":memory:" and _borrowed_connection is None):
            raise MemoryEventRepositoryError(MEMORY_EVENT_INVALID, "valid database is required")
        self.db_path = raw if raw == ":memory:" else str(Path(raw).resolve())
        self._timeout_ms = max(1, int(timeout_ms))
        self._borrowed_connection = _borrowed_connection
        self._borrowed_lock = _borrowed_lock
        if verify_schema:
            self.assert_schema_ready(require_catalog=raw != ":memory:")

    @classmethod
    def from_ledger(cls, ledger: Any) -> "MemoryEventRepository":
        if isinstance(ledger, sqlite3.Connection):
            database = ledger.execute("PRAGMA database_list").fetchone()
            database_file = str(database[2] or "") if database is not None else ""
            if database_file:
                return cls(database_file)
            return cls(
                ":memory:",
                _borrowed_connection=ledger,
                _borrowed_lock=None,
            )
        path = str(
            getattr(ledger, "canonical_db_path", "")
            or getattr(ledger, "db_path", "")
        ).strip()
        if path and path != ":memory:":
            return cls(path)
        connection = getattr(ledger, "_conn", None)
        path = path or (":memory:" if connection is not None else "")
        lock = getattr(ledger, "_write_lock", None)
        if connection is None:
            raise MemoryEventRepositoryError(
                MEMORY_EVENT_INVALID, "in-memory ledger compatibility binding is unavailable"
            )
        return cls(
            path,
            _borrowed_connection=connection,
            _borrowed_lock=lock,
        )

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
    def _scope(self, *, write: bool) -> Iterator[sqlite3.Connection]:
        if self._borrowed_connection is not None:
            with (self._borrowed_lock or nullcontext()):
                try:
                    if write:
                        self._borrowed_connection.execute("BEGIN IMMEDIATE")
                    yield self._borrowed_connection
                    if write:
                        self._borrowed_connection.commit()
                except BaseException:
                    if write:
                        self._borrowed_connection.rollback()
                    raise
            return
        conn = self._connect()
        try:
            if write:
                conn.execute("BEGIN IMMEDIATE")
            yield conn
            if write:
                conn.commit()
        except BaseException:
            if write:
                conn.rollback()
            raise
        finally:
            conn.close()

    def assert_schema_ready(self, *, require_catalog: bool = True) -> None:
        try:
            with self._scope(write=False) as conn:
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
                asset = None if not require_catalog else conn.execute(
                    """
                    SELECT checksum, disposition FROM schema_migration_assets
                    WHERE asset_key = ?
                    """,
                    (MEMORY_EVENT_MIGRATION_ASSET_KEY,),
                ).fetchone()
        except sqlite3.DatabaseError as exc:
            raise MemoryEventRepositoryError(
                MEMORY_EVENT_SCHEMA_NOT_READY, "memory event schema cannot be verified"
            ) from exc
        missing = {
            table: sorted(required - columns.get(table, set()))
            for table, required in _REQUIRED_COLUMNS.items()
            if not required <= columns.get(table, set())
        }
        bad_catalog = require_catalog and (
            asset is None
            or str(asset["checksum"]) != MEMORY_EVENT_MIGRATION_SHA256
            or str(asset["disposition"]) not in _ALLOWED_DISPOSITIONS
        )
        if missing or not _REQUIRED_INDEXES <= indexes or bad_catalog:
            raise MemoryEventRepositoryError(
                MEMORY_EVENT_SCHEMA_NOT_READY,
                "memory event schema migration is missing, stale, or checksum-drifted",
            )

    def append_event(self, event: Mapping[str, Any]) -> dict[str, Any]:
        item = dict(event)
        event_id = _required(item.get("event_id"), "event_id")
        values = self._event_values(item)
        with self._scope(write=True) as conn:
            try:
                conn.execute(
                    """
                    INSERT INTO memory_event(
                        event_id, person_id, session_id, conversation_id,
                        source_kind, event_kind, visibility, role, content,
                        content_summary, causality_json, refs_json, salience,
                        valence, uncertainty, confidence, projection_status_json,
                        created_at_ms, active
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    values,
                )
            except sqlite3.IntegrityError as exc:
                raise MemoryEventRepositoryError(
                    MEMORY_EVENT_CONFLICT, f"memory event identity conflict: {event_id}"
                ) from exc
            return dict(
                conn.execute(
                    f"SELECT {', '.join(_EVENT_COLUMNS)} FROM memory_event WHERE event_id = ?",
                    (event_id,),
                ).fetchone()
            )

    @staticmethod
    def _event_values(item: Mapping[str, Any]) -> tuple[Any, ...]:
        return (
            _required(item.get("event_id"), "event_id"),
            _required(item.get("person_id") or "default", "person_id"),
            str(item.get("session_id") or ""), str(item.get("conversation_id") or ""),
            _required(item.get("source_kind"), "source_kind"),
            _required(item.get("event_kind"), "event_kind"),
            _required(item.get("visibility"), "visibility"), str(item.get("role") or ""),
            str(item.get("content") or ""), str(item.get("content_summary") or ""),
            _dumps(dict(item.get("causality") or {})),
            _dumps(dict(item.get("refs") or {})), float(item.get("salience") or 0.0),
            float(item.get("valence") or 0.0), float(item.get("uncertainty") or 0.0),
            float(item.get("confidence") or 0.0),
            _dumps(dict(item.get("projection_status") or {})),
            int(item.get("created_at_ms") or 0), 1 if item.get("active", True) else 0,
        )

    def append_visible_turn(
        self,
        *,
        event: Mapping[str, Any],
        turn: Mapping[str, Any],
    ) -> dict[str, Any]:
        memory, projected = dict(event), dict(turn)
        session = _required(memory.get("session_id"), "session_id")
        role = _required(memory.get("role"), "role")
        refs = dict(memory.get("refs") or {})
        request = _required(refs.get("request_id"), "refs.request_id")
        event_id = _required(memory.get("event_id"), "event_id")
        with self._scope(write=True) as conn:
            rows = conn.execute(
                """
                SELECT event_id, refs_json, projection_status_json FROM memory_event
                WHERE session_id = ? AND role = ? AND visibility = 'visible'
                ORDER BY created_at_ms DESC, id DESC LIMIT 200
                """,
                (session, role),
            ).fetchall()
            for row in rows:
                candidate_refs = _loads(row["refs_json"])
                if isinstance(candidate_refs, dict) and candidate_refs.get("request_id") == request:
                    return {
                        "status": "DUPLICATE", "memory_event_id": str(row["event_id"]),
                        "turn": {"turn_id": candidate_refs.get("turn_id")},
                        "projection_status": _loads(row["projection_status_json"]) or {},
                    }
            values = list(self._event_values({**memory, "visibility": "visible"}))
            values[16] = "{}"
            conn.execute(
                """
                INSERT INTO memory_event(
                    event_id, person_id, session_id, conversation_id, source_kind,
                    event_kind, visibility, role, content, content_summary,
                    causality_json, refs_json, salience, valence, uncertainty,
                    confidence, projection_status_json, created_at_ms, active
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                tuple(values),
            )
            existing = conn.execute(
                f"""
                SELECT {', '.join(_TURN_COLUMNS)} FROM conversation_turn
                WHERE session_id = ? AND request_id = ? AND role = ? LIMIT 1
                """,
                (session, request, role),
            ).fetchone()
            if existing is None:
                next_row = conn.execute(
                    "SELECT COALESCE(MAX(turn_index), -1) + 1 FROM conversation_turn WHERE session_id = ?",
                    (session,),
                ).fetchone()
                turn_index = int(next_row[0] if next_row is not None else 0)
                metadata = dict(projected.get("metadata") or {})
                metadata["memory_event_id"] = event_id
                conn.execute(
                    """
                    INSERT INTO conversation_turn(
                        turn_id, session_id, person_id, request_id, role, content,
                        ts_ms, turn_index, lang, token_count, corrupted, metadata,
                        created_at_ms
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        _required(projected.get("turn_id"), "turn_id"), session,
                        _required(projected.get("person_id") or "default", "person_id"),
                        request, role, str(projected.get("content") or ""),
                        int(projected.get("ts_ms") or 0), turn_index,
                        str(projected.get("lang") or "zh"), projected.get("token_count"),
                        1 if projected.get("corrupted") else 0, _dumps(metadata),
                        int(projected.get("ts_ms") or 0),
                    ),
                )
                turn_id, turn_status = str(projected["turn_id"]), "OK"
            else:
                record = dict(existing)
                self._assert_turn_binding(record, projected)
                metadata_value = _loads(record.get("metadata"))
                metadata = dict(metadata_value) if isinstance(metadata_value, dict) else {}
                bound = str(metadata.get("memory_event_id") or "")
                if bound and bound != event_id:
                    raise MemoryEventRepositoryError(
                        MEMORY_EVENT_CONFLICT,
                        "visible turn is already bound to a different memory event",
                    )
                metadata["memory_event_id"] = event_id
                conn.execute(
                    "UPDATE conversation_turn SET metadata = ? WHERE turn_id = ?",
                    (_dumps(metadata), record["turn_id"]),
                )
                turn_id = str(record["turn_id"])
                turn_index = int(record["turn_index"] or 0)
                turn_status = "EXISTS"
            refs["turn_id"] = turn_id
            projection = {"conversation_turn": {"status": "OK", "turn_id": turn_id}}
            conn.execute(
                "UPDATE memory_event SET refs_json = ?, projection_status_json = ? WHERE event_id = ?",
                (_dumps(refs), _dumps(projection), event_id),
            )
            return {
                "status": "OK", "memory_event_id": event_id,
                "turn": {
                    "status": turn_status, "turn_id": turn_id,
                    "turn_index": turn_index, "memory_event_id": event_id,
                },
                "projection_status": projection,
            }

    def project_turn(self, *, memory_event_id: str, turn: Mapping[str, Any]) -> dict[str, Any]:
        item = dict(turn)
        session = _required(item.get("session_id"), "session_id")
        request = _required(item.get("request_id"), "request_id")
        role = _required(item.get("role"), "role")
        with self._scope(write=True) as conn:
            existing = conn.execute(
                f"""
                SELECT {', '.join(_TURN_COLUMNS)} FROM conversation_turn
                WHERE session_id = ? AND request_id = ? AND role = ? LIMIT 1
                """,
                (session, request, role),
            ).fetchone()
            if existing is not None:
                record = dict(existing)
                self._assert_turn_binding(record, item)
                metadata_value = _loads(record.get("metadata"))
                metadata = dict(metadata_value) if isinstance(metadata_value, dict) else {}
                bound = str(metadata.get("memory_event_id") or "")
                if bound and bound != memory_event_id:
                    raise MemoryEventRepositoryError(
                        MEMORY_EVENT_CONFLICT, "turn has a different memory event binding"
                    )
                metadata["memory_event_id"] = memory_event_id
                conn.execute(
                    "UPDATE conversation_turn SET metadata = ? WHERE turn_id = ?",
                    (_dumps(metadata), record["turn_id"]),
                )
                return {
                    "status": "EXISTS", "turn_id": str(record["turn_id"]),
                    "turn_index": int(record["turn_index"] or 0),
                    "memory_event_id": memory_event_id,
                }
            next_row = conn.execute(
                "SELECT COALESCE(MAX(turn_index), -1) + 1 FROM conversation_turn WHERE session_id = ?",
                (session,),
            ).fetchone()
            turn_index = int(next_row[0] if next_row is not None else 0)
            conn.execute(
                """
                INSERT INTO conversation_turn(
                    turn_id, session_id, person_id, request_id, role, content,
                    ts_ms, turn_index, lang, token_count, corrupted, metadata,
                    created_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _required(item.get("turn_id"), "turn_id"), session,
                    _required(item.get("person_id") or "default", "person_id"), request,
                    role, str(item.get("content") or ""), int(item.get("ts_ms") or 0),
                    turn_index, str(item.get("lang") or "zh"), item.get("token_count"),
                    1 if item.get("corrupted") else 0,
                    _dumps(dict(item.get("metadata") or {})), int(item.get("ts_ms") or 0),
                ),
            )
            return {
                "status": "OK", "turn_id": str(item["turn_id"]),
                "turn_index": turn_index, "memory_event_id": memory_event_id,
            }

    @staticmethod
    def _assert_turn_binding(
        persisted: Mapping[str, Any],
        requested: Mapping[str, Any],
    ) -> None:
        expected = {
            "person_id": str(requested.get("person_id") or "default").strip(),
            "content": str(requested.get("content") or ""),
            "ts_ms": int(requested.get("ts_ms") or 0),
            "lang": str(requested.get("lang") or "zh"),
            "token_count": requested.get("token_count"),
            "corrupted": 1 if requested.get("corrupted") else 0,
        }
        mismatched = [
            key for key, value in expected.items() if persisted.get(key) != value
        ]
        if mismatched:
            raise MemoryEventRepositoryError(
                MEMORY_EVENT_CONFLICT,
                "visible turn conflicts with canonical fields: "
                + ", ".join(mismatched),
            )

    def get_event(self, *, event_id: str, active_only: bool = False) -> dict[str, Any] | None:
        clause = "event_id = ? AND active = 1" if active_only else "event_id = ?"
        with self._scope(write=False) as conn:
            row = conn.execute(
                f"SELECT {', '.join(_EVENT_COLUMNS)} FROM memory_event WHERE {clause} LIMIT 1",
                (str(event_id or "").strip(),),
            ).fetchone()
        return dict(row) if row is not None else None

    def find_visible_event(
        self, *, session_id: str, request_id: str, role: str
    ) -> dict[str, Any] | None:
        request = str(request_id or "").strip()
        if not request:
            return None
        with self._scope(write=False) as conn:
            rows = conn.execute(
                """
                SELECT event_id, refs_json, projection_status_json FROM memory_event
                WHERE session_id = ? AND role = ? AND visibility = 'visible'
                ORDER BY created_at_ms DESC, id DESC LIMIT 200
                """,
                (str(session_id or "").strip(), str(role or "").strip()),
            ).fetchall()
        for row in rows:
            refs = _loads(row["refs_json"])
            if isinstance(refs, dict) and refs.get("request_id") == request:
                return dict(row)
        return None

    def merge_refs(self, *, event_id: str, refs: Mapping[str, Any]) -> None:
        self._merge_json(event_id=event_id, column="refs_json", patch=refs)

    def merge_projection_status(
        self, *, event_id: str, projection_status: Mapping[str, Any]
    ) -> None:
        self._merge_json(
            event_id=event_id,
            column="projection_status_json",
            patch=projection_status,
        )

    def _merge_json(
        self, *, event_id: str, column: str, patch: Mapping[str, Any]
    ) -> None:
        if column not in {"refs_json", "projection_status_json"}:
            raise MemoryEventRepositoryError(MEMORY_EVENT_INVALID, "invalid JSON column")
        with self._scope(write=True) as conn:
            row = conn.execute(
                f'SELECT "{column}" FROM memory_event WHERE event_id = ? LIMIT 1',
                (str(event_id or "").strip(),),
            ).fetchone()
            if row is None:
                return
            value = _loads(row[0])
            merged = dict(value) if isinstance(value, dict) else {}
            merged.update({key: item for key, item in dict(patch or {}).items() if item not in (None, "")})
            conn.execute(
                f'UPDATE memory_event SET "{column}" = ? WHERE event_id = ?',
                (_dumps(merged), str(event_id or "").strip()),
            )

    def list_events(
        self,
        *,
        person_id: str,
        session_id: str = "",
        conversation_id: str = "",
        visibility: str = "",
        created_before_ms: int | None = None,
        match_any_identity: bool = False,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """List events with strict or resolver-style identity matching."""

        person = str(person_id or "").strip()
        session = str(session_id or "").strip()
        conversation = str(conversation_id or "").strip()
        params: list[Any] = []
        if match_any_identity:
            identities = []
            for column, value in (
                ("person_id", person),
                ("session_id", session),
                ("conversation_id", conversation),
            ):
                if value:
                    identities.append(f"{column} = ?")
                    params.append(value)
            if not identities:
                return []
            clauses = ["active = 1", "(" + " OR ".join(identities) + ")"]
        else:
            clauses = ["person_id = ?", "active = 1"]
            params.append(person)
            if session:
                clauses.append("session_id = ?")
                params.append(session)
            if conversation:
                clauses.append("conversation_id = ?")
                params.append(conversation)
        if visibility:
            clauses.append("visibility = ?")
            params.append(str(visibility).strip())
        if created_before_ms is not None and int(created_before_ms) > 0:
            clauses.append("created_at_ms <= ?")
            params.append(int(created_before_ms))
        params.append(max(1, min(int(limit or 50), 200)))
        with self._scope(write=False) as conn:
            rows = conn.execute(
                f"""
                SELECT {', '.join(_EVENT_COLUMNS)} FROM memory_event
                WHERE {' AND '.join(clauses)}
                ORDER BY created_at_ms DESC, id DESC LIMIT ?
                """,
                tuple(params),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_visible_reply_context(
        self,
        *,
        person_id: str,
        session_id: str = "",
        request_id: str = "",
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Read visible events at or before the current request boundary."""

        person = str(person_id or "").strip()
        session = str(session_id or "").strip()
        request = str(request_id or "").strip()
        if not person:
            return []
        with self._scope(write=False) as conn:
            boundary = 0
            if request:
                row = conn.execute(
                    """
                    SELECT created_at_ms FROM memory_event
                    WHERE active = 1 AND visibility = 'visible' AND role = 'user'
                      AND person_id = ? AND (? = '' OR session_id = ?)
                      AND json_extract(refs_json, '$.request_id') = ?
                    ORDER BY created_at_ms ASC LIMIT 1
                    """,
                    (person, session, session, request),
                ).fetchone()
                boundary = int(row[0] or 0) if row is not None else 0
            clauses = [
                "active = 1",
                "visibility = 'visible'",
                "person_id = ?",
                "(? = '' OR session_id = ?)",
            ]
            params: list[Any] = [person, session, session]
            if boundary:
                clauses.append("created_at_ms <= ?")
                params.append(boundary)
            params.append(max(1, min(int(limit), 200)))
            rows = conn.execute(
                f"""
                SELECT {', '.join(_EVENT_COLUMNS)} FROM memory_event
                WHERE {' AND '.join(clauses)}
                ORDER BY created_at_ms DESC, id DESC LIMIT ?
                """,
                tuple(params),
            ).fetchall()
        return [dict(row) for row in reversed(rows)]

    def health(self, *, person_id: str) -> dict[str, int]:
        with self._scope(write=False) as conn:
            present = {
                str(row[0])
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
            memory = conn.execute(
                """
                SELECT COUNT(*) FROM memory_event WHERE person_id = ? AND active = 1
                  AND (projection_status_json LIKE '%FAILED%'
                       OR projection_status_json LIKE '%MISSING%')
                """,
                (person_id,),
            ).fetchone()[0]
            visible = conn.execute(
                """
                SELECT COUNT(*) FROM memory_event e
                WHERE e.person_id = ? AND e.active = 1 AND e.visibility = 'visible'
                  AND e.role IN ('user', 'assistant')
                  AND COALESCE(json_extract(e.projection_status_json, '$.conversation_turn.status'), '')
                      NOT IN ('OK', 'COVERED_BY_USER_MESSAGE')
                  AND NOT EXISTS (
                    SELECT 1 FROM conversation_turn t
                    WHERE json_extract(t.metadata, '$.memory_event_id') = e.event_id
                       OR (t.session_id = e.session_id AND t.role = e.role
                           AND t.request_id = json_extract(e.refs_json, '$.request_id'))
                  )
                """,
                (person_id,),
            ).fetchone()[0] if "conversation_turn" in present else 0
            proactive = conn.execute(
                """
                SELECT COUNT(*) FROM memory_event e
                WHERE e.person_id = ? AND e.active = 1
                  AND e.event_kind = 'life_proactive_sent'
                  AND NOT EXISTS (
                    SELECT 1 FROM proactive_interaction_episode p
                    WHERE p.episode_id = json_extract(e.refs_json, '$.proactive_episode_id')
                       OR p.source_outbox_id = json_extract(e.refs_json, '$.proactive_outbox_id')
                  )
                """,
                (person_id,),
            ).fetchone()[0] if "proactive_interaction_episode" in present else 0
            private = conn.execute(
                """
                SELECT COUNT(*) FROM memory_event e
                WHERE e.person_id = ? AND e.active = 1 AND e.visibility = 'private'
                  AND NOT EXISTS (
                    SELECT 1 FROM life_experience_event l
                    WHERE json_extract(l.evidence_json, '$.memory_event_id') = e.event_id
                  )
                """,
                (person_id,),
            ).fetchone()[0] if "life_experience_event" in present else 0
            orphan = conn.execute(
                """
                SELECT COUNT(*) FROM conversation_turn WHERE person_id = ?
                  AND COALESCE(json_extract(metadata, '$.memory_event_id'), '') = ''
                """,
                (person_id,),
            ).fetchone()[0] if "conversation_turn" in present else 0
        return {
            "memory_event_projection_gaps": int(memory or 0),
            "visible_turn_projection_gaps": int(visible or 0),
            "proactive_episode_projection_gaps": int(proactive or 0),
            "private_life_event_projection_gaps": int(private or 0),
            "orphan_projection_rows": int(orphan or 0),
        }

    def reset(self) -> dict[str, int]:
        cleared: dict[str, int] = {}
        with self._scope(write=True) as conn:
            conn.execute("PRAGMA defer_foreign_keys = ON")
            present = {
                str(row[0])
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
            for table in MEMORY_REBUILD_CLEAR_TABLES:
                if table in present:
                    cursor = conn.execute(f'DELETE FROM "{table}"')
                    cleared[table] = int(cursor.rowcount or 0)
        return cleared


__all__ = [
    "MEMORY_EVENT_CONFLICT", "MEMORY_EVENT_INVALID",
    "MEMORY_EVENT_MIGRATION_ASSET_KEY", "MEMORY_EVENT_MIGRATION_SHA256",
    "MEMORY_EVENT_SCHEMA_NOT_READY", "MEMORY_REBUILD_CLEAR_TABLES",
    "MemoryEventRepository", "MemoryEventRepositoryError",
]
