"""Read-only query adapter for persisted Emotion Timeline warmup events."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
import sqlite3
from typing import Iterator


EMOTION_TIMELINE_QUERY_INVALID = "EMOTION_TIMELINE_QUERY_INVALID"
EMOTION_TIMELINE_QUERY_SCHEMA_NOT_READY = "EMOTION_TIMELINE_QUERY_SCHEMA_NOT_READY"
EMOTION_TIMELINE_QUERY_UNAVAILABLE = "EMOTION_TIMELINE_QUERY_UNAVAILABLE"
_REQUIRED_COLUMNS = frozenset({"id", "emotion_tag", "status", "ts_ms"})


class EmotionTimelineQueryRepositoryError(RuntimeError):
    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = str(reason_code or EMOTION_TIMELINE_QUERY_UNAVAILABLE)
        self.detail = str(detail or "emotion timeline query failed")
        super().__init__(f"{self.reason_code}: {self.detail}")


@dataclass(frozen=True, slots=True)
class EmotionTimelineActionRecord:
    emotion_tag: str
    ts_ms: int


class EmotionTimelineQueryRepository:
    """Operation-scoped, query-only access to canonical avatar action history."""

    def __init__(self, canonical_db_path: str | Path, *, timeout_ms: int = 3_000) -> None:
        raw_path = str(canonical_db_path or "").strip()
        if not raw_path or raw_path == ":memory:" or raw_path.lower().startswith("file:"):
            raise EmotionTimelineQueryRepositoryError(
                EMOTION_TIMELINE_QUERY_INVALID,
                "a durable canonical database path is required",
            )
        self.canonical_db_path = Path(raw_path).expanduser().resolve()
        self._timeout_ms = max(1, int(timeout_ms))
        if not self.canonical_db_path.is_file():
            raise EmotionTimelineQueryRepositoryError(
                EMOTION_TIMELINE_QUERY_SCHEMA_NOT_READY,
                f"canonical database does not exist: {self.canonical_db_path}",
            )
        self.assert_schema_ready()

    def _connect_read_only(self) -> sqlite3.Connection:
        uri = self.canonical_db_path.as_uri() + "?mode=ro"
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
            raise EmotionTimelineQueryRepositoryError(
                EMOTION_TIMELINE_QUERY_UNAVAILABLE,
                "canonical database could not be opened read-only",
            ) from exc

    @contextmanager
    def _snapshot(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect_read_only()
        try:
            conn.execute("BEGIN")
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def assert_schema_ready(self) -> None:
        try:
            conn = self._connect_read_only()
            try:
                columns = {
                    str(row[1])
                    for row in conn.execute(
                        'PRAGMA table_info("avatar_action_log")'
                    ).fetchall()
                }
            finally:
                conn.close()
        except EmotionTimelineQueryRepositoryError:
            raise
        except sqlite3.DatabaseError as exc:
            raise EmotionTimelineQueryRepositoryError(
                EMOTION_TIMELINE_QUERY_SCHEMA_NOT_READY,
                "avatar action schema is unavailable",
            ) from exc
        if not _REQUIRED_COLUMNS.issubset(columns):
            raise EmotionTimelineQueryRepositoryError(
                EMOTION_TIMELINE_QUERY_SCHEMA_NOT_READY,
                "avatar_action_log does not expose the required read model",
            )

    def recent_actions(
        self,
        *,
        since_ms: int,
        limit: int = 150,
    ) -> list[EmotionTimelineActionRecord]:
        try:
            normalized_since = int(since_ms)
            normalized_limit = int(limit)
        except (TypeError, ValueError) as exc:
            raise EmotionTimelineQueryRepositoryError(
                EMOTION_TIMELINE_QUERY_INVALID,
                "timeline window and limit must be integers",
            ) from exc
        if normalized_since < 0 or normalized_limit < 1 or normalized_limit > 1_000:
            raise EmotionTimelineQueryRepositoryError(
                EMOTION_TIMELINE_QUERY_INVALID,
                "timeline window or limit is outside the supported range",
            )
        try:
            with self._snapshot() as conn:
                rows = conn.execute(
                    """
                    SELECT emotion_tag, ts_ms
                    FROM (
                        SELECT emotion_tag, ts_ms
                        FROM avatar_action_log
                        WHERE ts_ms >= ?
                          AND emotion_tag IS NOT NULL
                          AND length(trim(emotion_tag)) > 0
                          AND status = 'OK'
                        ORDER BY ts_ms DESC, id DESC
                        LIMIT ?
                    )
                    ORDER BY ts_ms ASC
                    """,
                    (normalized_since, normalized_limit),
                ).fetchall()
        except sqlite3.DatabaseError as exc:
            raise EmotionTimelineQueryRepositoryError(
                EMOTION_TIMELINE_QUERY_UNAVAILABLE,
                "avatar action timeline query failed",
            ) from exc
        return [
            EmotionTimelineActionRecord(
                emotion_tag=str(row["emotion_tag"]),
                ts_ms=int(row["ts_ms"]),
            )
            for row in rows
        ]


__all__ = [
    "EMOTION_TIMELINE_QUERY_INVALID",
    "EMOTION_TIMELINE_QUERY_SCHEMA_NOT_READY",
    "EMOTION_TIMELINE_QUERY_UNAVAILABLE",
    "EmotionTimelineActionRecord",
    "EmotionTimelineQueryRepository",
    "EmotionTimelineQueryRepositoryError",
]
