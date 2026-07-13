from __future__ import annotations

from dataclasses import dataclass, field
import json
import time
from typing import Any

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.persistence.context_repository import ContextRepository


_DEFAULT_STALE_AFTER_TURNS = 48


@dataclass(frozen=True)
class TopicLedgerEntry:
    topic_key: str
    last_answer_turn_index: int = -1
    last_answer_request_id: str = ""
    answer_style: str = ""
    confidence: float = 1.0
    stale_after_turn_index: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "topic_key": self.topic_key,
            "last_answer_turn_index": int(self.last_answer_turn_index or -1),
            "last_answer_request_id": self.last_answer_request_id,
            "answer_style": self.answer_style,
            "confidence": round(float(self.confidence or 0.0), 4),
            "stale_after_turn_index": int(self.stale_after_turn_index or 0),
            "metadata": dict(self.metadata or {}),
        }


@dataclass(frozen=True)
class TopicLedgerBlock:
    entries: tuple[TopicLedgerEntry, ...] = ()
    active_topics: tuple[str, ...] = ()
    recently_answered_topics: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "entries": [entry.as_dict() for entry in self.entries],
            "active_topics": list(self.active_topics),
            "recently_answered_topics": list(self.recently_answered_topics),
        }

    def get(self, topic_key: str) -> TopicLedgerEntry | None:
        normalized = str(topic_key or "").strip()
        if not normalized:
            return None
        for entry in self.entries:
            if entry.topic_key == normalized:
                return entry
        return None


class SessionTopicLedgerStore:
    """Persist session-level answered-topic facts from reply plans."""

    def __init__(self, ledger: SQLiteLedger) -> None:
        self._ledger = ledger
        self._repository = ContextRepository.from_ledger(ledger)

    def record_reply_plan(
        self,
        *,
        session_id: str,
        person_id: str | None,
        request_id: str,
        turn_index: int,
        primary_topic: str,
        answer_style: str,
        confidence: float = 1.0,
        stale_after_turn_gap: int = _DEFAULT_STALE_AFTER_TURNS,
        metadata: dict[str, Any] | None = None,
    ) -> tuple[str, ...]:
        normalized_session = str(session_id or "").strip()
        topic_key = str(primary_topic or "").strip()
        if not normalized_session or not topic_key:
            return ()
        stale_after_turn_index = int(turn_index) + max(1, int(stale_after_turn_gap or _DEFAULT_STALE_AFTER_TURNS))
        updated_at_ms = int(time.time() * 1000)
        return self._repository.upsert_topic_states(
            (
                {
                    "session_id": normalized_session,
                    "person_id": str(person_id or "").strip() or None,
                    "topic_key": topic_key,
                    "last_answer_turn_index": int(turn_index),
                    "last_answer_request_id": str(request_id or "").strip(),
                    "source_assistant_act": str(answer_style or "").strip(),
                    "confidence": float(confidence or 0.0),
                    "stale_after_turn_index": stale_after_turn_index,
                    "metadata": dict(metadata or {}) or None,
                    "updated_at_ms": updated_at_ms,
                },
            )
        )

    def fetch_block(
        self,
        *,
        session_id: str,
        current_turn_index: int | None = None,
    ) -> TopicLedgerBlock:
        normalized_session = str(session_id or "").strip()
        if not normalized_session:
            return TopicLedgerBlock()
        rows = self._repository.fetch_topic_states(session_id=normalized_session)
        entries: list[TopicLedgerEntry] = []
        active_topics: list[str] = []
        recent_topics: list[str] = []
        current_index = int(current_turn_index) if current_turn_index is not None else None
        for row in rows:
            metadata_raw = row.get("metadata")
            metadata: dict[str, Any] = {}
            if metadata_raw:
                try:
                    parsed = json.loads(str(metadata_raw))
                    if isinstance(parsed, dict):
                        metadata = parsed
                except Exception:
                    metadata = {}
            entry = TopicLedgerEntry(
                topic_key=str(row["topic_key"] or "").strip(),
                last_answer_turn_index=int(row["last_answer_turn_index"] or -1),
                last_answer_request_id=str(row["last_answer_request_id"] or "").strip(),
                answer_style=str(row["source_assistant_act"] or "").strip(),
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
        return TopicLedgerBlock(
            entries=tuple(entries),
            active_topics=tuple(active_topics),
            recently_answered_topics=tuple(recent_topics[:8]),
        )

    def delete_by_session(self, *, session_id: str) -> int:
        normalized_session = str(session_id or "").strip()
        if not normalized_session:
            return 0
        return self._repository.delete_topic_session(session_id=normalized_session)


def coerce_topic_ledger_block(raw: Any) -> TopicLedgerBlock:
    payload = dict(raw or {}) if isinstance(raw, dict) else {}
    entries_raw = list(payload.get("entries") or [])
    entries: list[TopicLedgerEntry] = []
    for item in entries_raw:
        if not isinstance(item, dict):
            continue
        topic_key = str(item.get("topic_key") or "").strip()
        if not topic_key:
            continue
        metadata = dict(item.get("metadata") or {}) if isinstance(item.get("metadata"), dict) else {}
        entries.append(
            TopicLedgerEntry(
                topic_key=topic_key,
                last_answer_turn_index=int(item.get("last_answer_turn_index") or -1),
                last_answer_request_id=str(item.get("last_answer_request_id") or "").strip(),
                answer_style=str(item.get("answer_style") or item.get("source_assistant_act") or "").strip(),
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
    return TopicLedgerBlock(
        entries=tuple(entries),
        active_topics=active_topics,
        recently_answered_topics=recent_topics,
    )


__all__ = [
    "SessionTopicLedgerStore",
    "TopicLedgerBlock",
    "TopicLedgerEntry",
    "coerce_topic_ledger_block",
]
