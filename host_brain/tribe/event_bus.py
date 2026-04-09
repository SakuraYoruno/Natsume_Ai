from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass
from typing import Any, Deque

from host_brain.core.ids import now_ms


@dataclass(slots=True)
class TribeEvent:
    event_id: str
    event_type: str
    source_agent: str
    target_agent: str | None
    payload: dict[str, Any]
    schema_version: str = "tribe.event.v1"
    ts_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "source_agent": self.source_agent,
            "target_agent": self.target_agent,
            "payload": dict(self.payload),
            "schema_version": self.schema_version,
            "ts_ms": int(self.ts_ms),
        }


class InMemoryTribeEventBus:
    """最小可用事件总线：支持发布与最近事件查询。"""

    def __init__(self, *, max_events: int = 1024) -> None:
        self._max_events = max(1, int(max_events))
        self._events: Deque[TribeEvent] = deque(maxlen=self._max_events)
        self._lock = threading.RLock()

    def publish(self, event: TribeEvent) -> TribeEvent:
        with self._lock:
            self._events.append(event)
        return event

    @staticmethod
    def _payload_value(payload: dict[str, Any], *keys: str) -> str:
        for key in keys:
            value = payload.get(key)
            if value is None:
                continue
            normalized = str(value).strip()
            if normalized:
                return normalized
        return ""

    def recent(
        self,
        *,
        limit: int = 50,
        event_type: str | None = None,
        source_agent: str | None = None,
        target_agent: str | None = None,
        request_id: str | None = None,
        session_id: str | None = None,
        conversation_id: str | None = None,
    ) -> list[TribeEvent]:
        target_type = str(event_type or "").strip().lower()
        target_source_agent = str(source_agent or "").strip().lower()
        target_target_agent = str(target_agent or "").strip().lower()
        target_request_id = str(request_id or "").strip()
        target_session_id = str(session_id or "").strip()
        target_conversation_id = str(conversation_id or "").strip()
        size = max(1, int(limit))
        with self._lock:
            events = list(self._events)
        filtered: list[TribeEvent] = []
        for item in events:
            if target_type and str(item.event_type).strip().lower() != target_type:
                continue
            if target_source_agent and str(item.source_agent).strip().lower() != target_source_agent:
                continue
            if target_target_agent and str(item.target_agent or "").strip().lower() != target_target_agent:
                continue
            payload = item.payload if isinstance(item.payload, dict) else {}
            payload_request_id = self._payload_value(payload, "request_id", "requestId")
            payload_session_id = self._payload_value(payload, "session_id", "sessionId")
            payload_conversation_id = self._payload_value(payload, "conversation_id", "conversationId")
            if target_request_id and payload_request_id != target_request_id:
                continue
            if target_session_id and payload_session_id != target_session_id:
                continue
            if target_conversation_id and payload_conversation_id != target_conversation_id:
                continue
            filtered.append(item)
        return list(reversed(filtered[-size:]))


def new_tribe_event(
    *,
    event_type: str,
    source_agent: str,
    target_agent: str | None = None,
    payload: dict[str, Any] | None = None,
    schema_version: str = "tribe.event.v1",
) -> TribeEvent:
    ts_ms = now_ms()
    event_id = f"tribe_evt_{ts_ms}"
    return TribeEvent(
        event_id=event_id,
        event_type=str(event_type).strip() or "UNKNOWN",
        source_agent=str(source_agent).strip() or "unknown_source",
        target_agent=(str(target_agent).strip() or None) if target_agent is not None else None,
        payload=dict(payload or {}),
        schema_version=str(schema_version).strip() or "tribe.event.v1",
        ts_ms=ts_ms,
    )


__all__ = [
    "InMemoryTribeEventBus",
    "TribeEvent",
    "new_tribe_event",
]
