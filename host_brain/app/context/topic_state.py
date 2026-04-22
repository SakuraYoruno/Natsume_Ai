from __future__ import annotations

from dataclasses import dataclass, field
import json
import time
from typing import Any

from host_brain.audit.sqlite_ledger import SQLiteLedger

from .dialogue_state import classify_assistant_act, extract_answered_topics


_DEFAULT_STALE_AFTER_TURNS = 48


@dataclass(frozen=True)
class TopicStateEntry:
    topic_key: str
    last_answer_turn_index: int = -1
    last_answer_request_id: str = ""
    source_assistant_act: str = ""
    confidence: float = 1.0
    stale_after_turn_index: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "topic_key": self.topic_key,
            "last_answer_turn_index": int(self.last_answer_turn_index or -1),
            "last_answer_request_id": self.last_answer_request_id,
            "source_assistant_act": self.source_assistant_act,
            "confidence": round(float(self.confidence or 0.0), 4),
            "stale_after_turn_index": int(self.stale_after_turn_index or 0),
            "metadata": dict(self.metadata or {}),
        }


@dataclass(frozen=True)
class TopicStateBlock:
    entries: tuple[TopicStateEntry, ...] = ()
    active_topics: tuple[str, ...] = ()
    recently_answered_topics: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "entries": [entry.as_dict() for entry in self.entries],
            "active_topics": list(self.active_topics),
            "recently_answered_topics": list(self.recently_answered_topics),
        }

    def get(self, topic_key: str) -> TopicStateEntry | None:
        normalized = str(topic_key or "").strip()
        if not normalized:
            return None
        for entry in self.entries:
            if entry.topic_key == normalized:
                return entry
        return None


class SessionTopicStateStore:
    """Persist session-level answered-topic state outside short dialogue windows."""

    def __init__(self, ledger: SQLiteLedger) -> None:
        self._ledger = ledger
        self._conn = ledger._conn
        self._write_lock = ledger._write_lock
        self._ledger._ensure_schema()

    def upsert_answered_topics(
        self,
        *,
        session_id: str,
        person_id: str | None,
        request_id: str,
        turn_index: int,
        assistant_text: str,
        metadata: dict[str, Any] | None = None,
        stale_after_turn_gap: int = _DEFAULT_STALE_AFTER_TURNS,
    ) -> tuple[str, ...]:
        normalized_session = str(session_id or "").strip()
        normalized_text = str(assistant_text or "").strip()
        if not normalized_session or not normalized_text:
            return ()
        topics = sorted(extract_answered_topics(normalized_text))
        if not topics:
            return ()
        assistant_act = classify_assistant_act(normalized_text)
        stale_after_turn_index = int(turn_index) + max(1, int(stale_after_turn_gap or _DEFAULT_STALE_AFTER_TURNS))
        updated_at_ms = int(time.time() * 1000)
        metadata_payload = json.dumps(dict(metadata or {}), ensure_ascii=False, sort_keys=True) if metadata else None
        with self._write_lock:
            for topic_key in topics:
                self._conn.execute(
                    """
                    INSERT INTO session_topic_state (
                        session_id,
                        person_id,
                        topic_key,
                        last_answer_turn_index,
                        last_answer_request_id,
                        source_assistant_act,
                        confidence,
                        stale_after_turn_index,
                        metadata,
                        updated_at_ms
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
                        normalized_session,
                        str(person_id or "").strip() or None,
                        topic_key,
                        int(turn_index),
                        str(request_id or "").strip(),
                        assistant_act,
                        1.0,
                        stale_after_turn_index,
                        metadata_payload,
                        updated_at_ms,
                    ),
                )
            self._conn.commit()
        return tuple(topics)

    def fetch_block(
        self,
        *,
        session_id: str,
        current_turn_index: int | None = None,
    ) -> TopicStateBlock:
        normalized_session = str(session_id or "").strip()
        if not normalized_session:
            return TopicStateBlock()
        rows = self._conn.execute(
            """
            SELECT
                topic_key,
                last_answer_turn_index,
                last_answer_request_id,
                source_assistant_act,
                confidence,
                stale_after_turn_index,
                metadata
            FROM session_topic_state
            WHERE session_id = ?
            ORDER BY last_answer_turn_index DESC, updated_at_ms DESC
            """,
            (normalized_session,),
        ).fetchall()
        entries: list[TopicStateEntry] = []
        active_topics: list[str] = []
        recent_topics: list[str] = []
        current_index = int(current_turn_index) if current_turn_index is not None else None
        for row in rows:
            metadata_raw = row["metadata"] if "metadata" in row.keys() else None
            metadata: dict[str, Any] = {}
            if metadata_raw:
                try:
                    parsed = json.loads(str(metadata_raw))
                    if isinstance(parsed, dict):
                        metadata = parsed
                except Exception:
                    metadata = {}
            entry = TopicStateEntry(
                topic_key=str(row["topic_key"] or "").strip(),
                last_answer_turn_index=int(row["last_answer_turn_index"] or -1),
                last_answer_request_id=str(row["last_answer_request_id"] or "").strip(),
                source_assistant_act=str(row["source_assistant_act"] or "").strip(),
                confidence=float(row["confidence"] or 0.0),
                stale_after_turn_index=int(row["stale_after_turn_index"] or 0),
                metadata=metadata,
            )
            if not entry.topic_key:
                continue
            entries.append(entry)
            recent_topics.append(entry.topic_key)
            if current_index is None or entry.stale_after_turn_index <= 0 or current_index <= entry.stale_after_turn_index:
                active_topics.append(entry.topic_key)
        return TopicStateBlock(
            entries=tuple(entries),
            active_topics=tuple(active_topics),
            recently_answered_topics=tuple(recent_topics[:8]),
        )

    def delete_by_session(self, *, session_id: str) -> int:
        normalized_session = str(session_id or "").strip()
        if not normalized_session:
            return 0
        with self._write_lock:
            cursor = self._conn.execute(
                "DELETE FROM session_topic_state WHERE session_id = ?",
                (normalized_session,),
            )
            self._conn.commit()
            return int(cursor.rowcount or 0)


__all__ = [
    "SessionTopicStateStore",
    "TopicStateBlock",
    "TopicStateEntry",
]


def coerce_topic_state_block(raw: Any) -> TopicStateBlock:
    payload = dict(raw or {}) if isinstance(raw, dict) else {}
    entries_raw = list(payload.get("entries") or [])
    entries: list[TopicStateEntry] = []
    for item in entries_raw:
        if not isinstance(item, dict):
            continue
        topic_key = str(item.get("topic_key") or "").strip()
        if not topic_key:
            continue
        metadata = dict(item.get("metadata") or {}) if isinstance(item.get("metadata"), dict) else {}
        entries.append(
            TopicStateEntry(
                topic_key=topic_key,
                last_answer_turn_index=int(item.get("last_answer_turn_index") or -1),
                last_answer_request_id=str(item.get("last_answer_request_id") or "").strip(),
                source_assistant_act=str(item.get("source_assistant_act") or "").strip(),
                confidence=float(item.get("confidence") or 0.0),
                stale_after_turn_index=int(item.get("stale_after_turn_index") or 0),
                metadata=metadata,
            )
        )
    active_topics = tuple(
        str(item).strip()
        for item in list(payload.get("active_topics") or [])
        if str(item).strip()
    )
    recent_topics = tuple(
        str(item).strip()
        for item in list(payload.get("recently_answered_topics") or [])
        if str(item).strip()
    )
    if not active_topics and entries:
        active_topics = tuple(entry.topic_key for entry in entries)
    if not recent_topics and entries:
        recent_topics = tuple(entry.topic_key for entry in entries[:8])
    return TopicStateBlock(
        entries=tuple(entries),
        active_topics=active_topics,
        recently_answered_topics=recent_topics,
    )


__all__.append("coerce_topic_state_block")
