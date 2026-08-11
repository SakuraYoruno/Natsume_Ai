"""Durable response-feedback and persona-reply query repository."""

from __future__ import annotations

from contextlib import contextmanager, nullcontext
from pathlib import Path
import sqlite3
from typing import Any, Iterator, Mapping, Sequence


RESPONSE_FEEDBACK_MIGRATION_ASSET_KEY = "0078#response_feedback_persistence"
RESPONSE_FEEDBACK_MIGRATION_SHA256 = (
    "bcbce69394c2e9e6a199526f35c6d577653154ec960a938e993233b4dc0f8105"
)
_ALLOWED_DISPOSITIONS = frozenset(
    {"APPLIED", "APPLIED_WITH_COMPAT", "LEGACY_BACKFILL"}
)
_REQUIRED_FEEDBACK_COLUMNS = frozenset(
    {
        "target_request_id", "session_id", "person_id", "thread_id",
        "target_text_hash", "feedback_score", "feedback_tags",
        "feedback_source", "created_at_ms", "active",
    }
)


class ResponseFeedbackRepository:
    def __init__(
        self,
        db_path: str | Path,
        *,
        borrowed_connection: sqlite3.Connection | None = None,
        borrowed_lock: Any | None = None,
        verify_schema: bool = True,
    ) -> None:
        self.db_path = str(db_path or "").strip()
        self._borrowed_connection = borrowed_connection
        self._borrowed_lock = borrowed_lock
        if verify_schema:
            self.assert_ready(require_catalog=self.db_path != ":memory:")

    @classmethod
    def from_ledger(cls, ledger: Any) -> "ResponseFeedbackRepository | None":
        path = str(
            getattr(ledger, "canonical_db_path", "")
            or getattr(ledger, "db_path", "")
            or getattr(ledger, "_db_path", "")
        ).strip()
        if not path:
            return None
        if path != ":memory:":
            return cls(path)
        connection = getattr(ledger, "_conn", None)
        if connection is None:
            return None
        return cls(
            path,
            borrowed_connection=connection,
            borrowed_lock=getattr(ledger, "_write_lock", None),
        )

    @contextmanager
    def _scope(self, *, write: bool) -> Iterator[sqlite3.Connection]:
        if self._borrowed_connection is not None:
            with (self._borrowed_lock or nullcontext()):
                started = write and not self._borrowed_connection.in_transaction
                try:
                    if started:
                        self._borrowed_connection.execute("BEGIN IMMEDIATE")
                    yield self._borrowed_connection
                    if started:
                        self._borrowed_connection.commit()
                except BaseException:
                    if started:
                        self._borrowed_connection.rollback()
                    raise
            return
        conn = sqlite3.connect(self.db_path, isolation_level=None)
        conn.row_factory = sqlite3.Row
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

    def assert_ready(self, *, require_catalog: bool) -> None:
        with self._scope(write=False) as conn:
            columns = {
                str(row[1])
                for row in conn.execute(
                    "PRAGMA table_info(response_feedback_log)"
                ).fetchall()
            }
            voice_present = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='voice_event_log'"
            ).fetchone()
            if not _REQUIRED_FEEDBACK_COLUMNS.issubset(columns) or voice_present is None:
                raise RuntimeError("RESPONSE_FEEDBACK_SCHEMA_NOT_READY")
            if require_catalog:
                row = conn.execute(
                    """
                    SELECT checksum, disposition FROM schema_migration_assets
                    WHERE asset_key = ?
                    """,
                    (RESPONSE_FEEDBACK_MIGRATION_ASSET_KEY,),
                ).fetchone()
                if (
                    row is None
                    or str(row[0]) != RESPONSE_FEEDBACK_MIGRATION_SHA256
                    or str(row[1]) not in _ALLOWED_DISPOSITIONS
                ):
                    raise RuntimeError("RESPONSE_FEEDBACK_SCHEMA_NOT_READY")

    def latest_persona_reply_rows(
        self,
        *,
        session_id: str,
        limit: int = 64,
    ) -> list[dict[str, Any]]:
        with self._scope(write=False) as conn:
            rows = conn.execute(
                """
                SELECT request_id, session_id, event_type, stage, payload, ts_ms
                FROM voice_event_log
                WHERE session_id = ? AND event_type = 'INPUT_COMPLETED'
                ORDER BY ts_ms DESC LIMIT ?
                """,
                (str(session_id), max(1, min(int(limit), 256))),
            ).fetchall()
        return [dict(row) for row in rows]

    def insert(self, record: Mapping[str, Any]) -> None:
        with self._scope(write=True) as conn:
            conn.execute(
                """
                INSERT INTO response_feedback_log(
                    target_request_id, session_id, person_id, thread_id,
                    target_text_hash, feedback_score, feedback_tags,
                    feedback_source, created_at_ms, active
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.get("target_request_id"), record.get("session_id"),
                    record.get("person_id"), record.get("thread_id"),
                    record.get("target_text_hash"), float(record.get("feedback_score") or 0.0),
                    record.get("feedback_tags"), record.get("feedback_source"),
                    int(record.get("created_at_ms") or 0),
                    1 if record.get("active", True) else 0,
                ),
            )

    def fetch(
        self,
        *,
        session_id: str = "",
        person_id: str = "",
        request_ids: Sequence[str] = (),
        text_hashes: Sequence[str] = (),
    ) -> list[dict[str, Any]]:
        requests = [str(value) for value in request_ids]
        hashes = [str(value) for value in text_hashes]
        where, params = ["active = 1"], []
        if session_id:
            where.append("session_id = ?"); params.append(session_id)
        if person_id:
            where.append("person_id = ?"); params.append(person_id)
        candidates: list[str] = []
        if requests:
            candidates.append("target_request_id IN (" + ",".join("?" for _ in requests) + ")")
            params.extend(requests)
        if hashes:
            candidates.append("target_text_hash IN (" + ",".join("?" for _ in hashes) + ")")
            params.extend(hashes)
        if not candidates:
            return []
        where.append("(" + " OR ".join(candidates) + ")")
        with self._scope(write=False) as conn:
            rows = conn.execute(
                """
                SELECT target_request_id, session_id, person_id, thread_id,
                       target_text_hash, feedback_score, feedback_tags,
                       feedback_source, created_at_ms, active
                FROM response_feedback_log WHERE
                """ + " AND ".join(where) + " ORDER BY created_at_ms DESC",
                tuple(params),
            ).fetchall()
        return [dict(row) for row in rows]


__all__ = [
    "RESPONSE_FEEDBACK_MIGRATION_ASSET_KEY",
    "RESPONSE_FEEDBACK_MIGRATION_SHA256",
    "ResponseFeedbackRepository",
]
