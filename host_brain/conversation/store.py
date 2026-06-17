from __future__ import annotations

import json
import uuid
from typing import Any

from host_brain.audit.sqlite_ledger import SQLiteLedger

from .schema import Turn, TurnRole


class TurnStore:
    """SQL-only storage for conversation turns."""

    def __init__(self, ledger: SQLiteLedger) -> None:
        self._ledger = ledger
        self._conn = ledger._conn
        self._write_lock = ledger._write_lock
        self._ledger._ensure_schema()

    def append(self, turn: Turn) -> None:
        metadata_json = None
        if turn.metadata:
            metadata_json = json.dumps(turn.metadata, ensure_ascii=False, sort_keys=True)
        with self._write_lock:
            self._conn.execute(
                """
                INSERT OR IGNORE INTO conversation_turn (
                    turn_id, session_id, person_id, request_id, role, content,
                    ts_ms, turn_index, lang, token_count, corrupted, metadata, created_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    turn.turn_id,
                    turn.session_id,
                    turn.person_id,
                    turn.request_id,
                    turn.role,
                    turn.content,
                    int(turn.ts_ms),
                    int(turn.turn_index),
                    str(turn.lang or "zh"),
                    turn.token_count,
                    1 if turn.corrupted else 0,
                    metadata_json,
                    int(turn.ts_ms),
                ),
            )
            self._conn.commit()

    def append_generated(
        self,
        *,
        session_id: str,
        person_id: str,
        request_id: str,
        role: TurnRole,
        content: str,
        ts_ms: int,
        lang: str = "zh",
        token_count: int | None = None,
        corrupted: bool = False,
        metadata: dict[str, Any] | None = None,
    ) -> Turn:
        with self._write_lock:
            turn = Turn(
                turn_id=f"turn::{uuid.uuid4().hex}",
                session_id=str(session_id or "").strip(),
                person_id=str(person_id or "").strip(),
                request_id=str(request_id or "").strip(),
                role=role,
                content=str(content or ""),
                ts_ms=int(ts_ms),
                turn_index=self._next_turn_index_locked(session_id=str(session_id or "").strip()),
                lang=str(lang or "zh").strip() or "zh",
                token_count=token_count,
                corrupted=bool(corrupted),
                metadata=dict(metadata or {}) or None,
            )
            metadata_json = None
            if turn.metadata:
                metadata_json = json.dumps(turn.metadata, ensure_ascii=False, sort_keys=True)
            self._conn.execute(
                """
                INSERT OR IGNORE INTO conversation_turn (
                    turn_id, session_id, person_id, request_id, role, content,
                    ts_ms, turn_index, lang, token_count, corrupted, metadata, created_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    turn.turn_id,
                    turn.session_id,
                    turn.person_id,
                    turn.request_id,
                    turn.role,
                    turn.content,
                    int(turn.ts_ms),
                    int(turn.turn_index),
                    str(turn.lang or "zh"),
                    turn.token_count,
                    1 if turn.corrupted else 0,
                    metadata_json,
                    int(turn.ts_ms),
                ),
            )
            self._conn.commit()
            return turn

    def query_recent(
        self,
        *,
        session_id: str,
        limit: int,
        exclude_request_id: str | None = None,
        before_ts_ms: int | None = None,
        before_turn_index: int | None = None,
    ) -> list[Turn]:
        session_id = str(session_id or "").strip()
        if not session_id:
            return []
        clauses = ["session_id = ?"]
        params: list[Any] = [session_id]
        if exclude_request_id:
            clauses.append("request_id != ?")
            params.append(str(exclude_request_id).strip())
        if before_ts_ms is not None:
            clauses.append("ts_ms < ?")
            params.append(int(before_ts_ms))
        if before_turn_index is not None:
            clauses.append("turn_index < ?")
            params.append(int(before_turn_index))
        params.append(max(1, int(limit)))
        rows = self._conn.execute(
            f"""
            SELECT turn_id, session_id, person_id, request_id, role, content,
                   ts_ms, turn_index, lang, token_count, corrupted, metadata
            FROM conversation_turn
            WHERE {" AND ".join(clauses)}
            ORDER BY ts_ms DESC, turn_index DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
        return [self._row_to_turn(row) for row in reversed(rows)]

    def find_by_request(
        self,
        *,
        session_id: str,
        request_id: str,
        role: TurnRole | None = None,
    ) -> Turn | None:
        session_id = str(session_id or "").strip()
        request_id = str(request_id or "").strip()
        if not session_id or not request_id:
            return None
        clauses = ["session_id = ?", "request_id = ?"]
        params: list[Any] = [session_id, request_id]
        if role:
            clauses.append("role = ?")
            params.append(str(role))
        row = self._conn.execute(
            f"""
            SELECT turn_id, session_id, person_id, request_id, role, content,
                   ts_ms, turn_index, lang, token_count, corrupted, metadata
            FROM conversation_turn
            WHERE {" AND ".join(clauses)}
            ORDER BY turn_index ASC
            LIMIT 1
            """,
            params,
        ).fetchone()
        return self._row_to_turn(row) if row is not None else None

    def query_before_turn_index(
        self,
        *,
        session_id: str,
        before_turn_index: int,
        limit: int,
        exclude_request_id: str | None = None,
    ) -> list[Turn]:
        session_id = str(session_id or "").strip()
        if not session_id:
            return []
        clauses = ["session_id = ?", "turn_index < ?"]
        params: list[Any] = [session_id, int(before_turn_index)]
        if exclude_request_id:
            clauses.append("request_id != ?")
            params.append(str(exclude_request_id).strip())
        params.append(max(1, int(limit)))
        rows = self._conn.execute(
            f"""
            SELECT turn_id, session_id, person_id, request_id, role, content,
                   ts_ms, turn_index, lang, token_count, corrupted, metadata
            FROM conversation_turn
            WHERE {" AND ".join(clauses)}
            ORDER BY turn_index DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
        return [self._row_to_turn(row) for row in reversed(rows)]

    def count_before_turn_index(
        self,
        *,
        session_id: str,
        before_turn_index: int,
        exclude_request_id: str | None = None,
    ) -> int:
        session_id = str(session_id or "").strip()
        if not session_id:
            return 0
        clauses = ["session_id = ?", "turn_index < ?"]
        params: list[Any] = [session_id, int(before_turn_index)]
        if exclude_request_id:
            clauses.append("request_id != ?")
            params.append(str(exclude_request_id).strip())
        row = self._conn.execute(
            f"""
            SELECT COUNT(1) AS row_count
            FROM conversation_turn
            WHERE {" AND ".join(clauses)}
            """,
            params,
        ).fetchone()
        if row is None:
            return 0
        raw_count = row["row_count"] if "row_count" in row.keys() else 0
        return max(0, int(raw_count or 0))

    def next_turn_index(self, *, session_id: str) -> int:
        with self._write_lock:
            return self._next_turn_index_locked(session_id=str(session_id or "").strip())

    def query_recent_assistant(
        self,
        *,
        limit: int,
        lang: str | None = None,
    ) -> list[Turn]:
        clauses = ["role = ?"]
        params: list[Any] = ["assistant"]
        normalized_lang = str(lang or "").strip().lower()
        if normalized_lang:
            clauses.append("LOWER(lang) = ?")
            params.append(normalized_lang)
        params.append(max(1, int(limit)))
        rows = self._conn.execute(
            f"""
            SELECT turn_id, session_id, person_id, request_id, role, content,
                   ts_ms, turn_index, lang, token_count, corrupted, metadata
            FROM conversation_turn
            WHERE {" AND ".join(clauses)}
            ORDER BY ts_ms DESC, turn_index DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
        return [self._row_to_turn(row) for row in rows]

    def delete_by_session(self, *, session_id: str) -> int:
        with self._write_lock:
            cursor = self._conn.execute(
                "DELETE FROM conversation_turn WHERE session_id = ?",
                (str(session_id or "").strip(),),
            )
            self._conn.commit()
            return int(cursor.rowcount or 0)

    def _next_turn_index_locked(self, *, session_id: str) -> int:
        row = self._conn.execute(
            "SELECT COALESCE(MAX(turn_index), -1) AS max_turn_index FROM conversation_turn WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        raw_max_index = row["max_turn_index"] if row is not None else -1
        max_index = int(raw_max_index if raw_max_index is not None else -1)
        return max_index + 1

    @staticmethod
    def _row_to_turn(row: Any) -> Turn:
        metadata_raw = row["metadata"] if "metadata" in row.keys() else None
        metadata: dict[str, Any] | None = None
        if metadata_raw:
            try:
                parsed = json.loads(str(metadata_raw))
                if isinstance(parsed, dict):
                    metadata = parsed
            except Exception:
                metadata = None
        return Turn(
            turn_id=str(row["turn_id"]),
            session_id=str(row["session_id"]),
            person_id=str(row["person_id"]),
            request_id=str(row["request_id"]),
            role=str(row["role"]),
            content=str(row["content"] or ""),
            ts_ms=int(row["ts_ms"] or 0),
            turn_index=int(row["turn_index"] or 0),
            lang=str(row["lang"] or "zh"),
            token_count=int(row["token_count"]) if row["token_count"] is not None else None,
            corrupted=bool(row["corrupted"]),
            metadata=metadata,
        )
