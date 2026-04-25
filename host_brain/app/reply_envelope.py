from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


FINAL_REPLY = "final_reply"
NO_REPLY = "no_reply"
TRANSPORT_ACK = "transport_ack"
TASK_STATUS = "task_status"
ALLOWED_REPLY_KINDS = frozenset({FINAL_REPLY, NO_REPLY, TRANSPORT_ACK, TASK_STATUS})


def _normalize_kind(raw: str | None) -> str:
    kind = str(raw or "").strip().lower()
    return kind if kind in ALLOWED_REPLY_KINDS else NO_REPLY


@dataclass(frozen=True)
class ReplyEnvelope:
    """Single outbound contract for user-visible chat output.

    Only `kind=final_reply` may be sent as a platform reply, persisted as an
    assistant turn, or remembered in the surface cache. ACKs, task status, and
    degraded failures must stay out of persona memory.
    """

    kind: str = NO_REPLY
    text: str = ""
    response_form: str = "text"
    source: str = ""
    sendable: bool = False
    persistable: bool = False
    cacheable: bool = False
    degraded: bool = False
    reason_code: str = ""
    audit: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        kind = _normalize_kind(self.kind)
        text = str(self.text or "").strip()
        is_final = kind == FINAL_REPLY and bool(text) and not bool(self.degraded)
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "text", text)
        object.__setattr__(self, "response_form", str(self.response_form or "text").strip() or "text")
        object.__setattr__(self, "source", str(self.source or "").strip())
        object.__setattr__(self, "reason_code", str(self.reason_code or "").strip())
        object.__setattr__(self, "sendable", bool(self.sendable and is_final))
        object.__setattr__(self, "persistable", bool(self.persistable and is_final))
        object.__setattr__(self, "cacheable", bool(self.cacheable and is_final))
        object.__setattr__(self, "audit", dict(self.audit or {}))

    @classmethod
    def final_reply(
        cls,
        *,
        text: str,
        response_form: str = "text",
        source: str = "full_model",
        cacheable: bool = True,
        audit: Mapping[str, Any] | None = None,
    ) -> "ReplyEnvelope":
        return cls(
            kind=FINAL_REPLY,
            text=text,
            response_form=response_form,
            source=source,
            sendable=True,
            persistable=True,
            cacheable=cacheable,
            degraded=False,
            audit=dict(audit or {}),
        )

    @classmethod
    def no_reply(
        cls,
        *,
        reason_code: str,
        source: str = "fallback_policy",
        audit: Mapping[str, Any] | None = None,
    ) -> "ReplyEnvelope":
        return cls(
            kind=NO_REPLY,
            text="",
            source=source,
            sendable=False,
            persistable=False,
            cacheable=False,
            degraded=True,
            reason_code=reason_code,
            audit=dict(audit or {}),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "text": self.text,
            "response_form": self.response_form,
            "source": self.source,
            "sendable": bool(self.sendable),
            "persistable": bool(self.persistable),
            "cacheable": bool(self.cacheable),
            "degraded": bool(self.degraded),
            "reason_code": self.reason_code,
            "audit": dict(self.audit or {}),
        }


def envelope_from_commit(
    *,
    decision: str,
    narrative: str,
    reason_code: str | None = None,
    response_form: str = "text",
    source: str = "",
    degraded: bool = False,
    cacheable: bool = True,
    audit: Mapping[str, Any] | None = None,
) -> ReplyEnvelope:
    normalized_decision = str(decision or "").strip().upper()
    text = str(narrative or "").strip()
    if normalized_decision == "APPROVE" and text and not bool(degraded):
        return ReplyEnvelope.final_reply(
            text=text,
            response_form=response_form,
            source=source or "full_model",
            cacheable=cacheable,
            audit=audit,
        )
    return ReplyEnvelope.no_reply(
        reason_code=str(reason_code or normalized_decision or "NO_REPLY").strip() or "NO_REPLY",
        source=source or "no_reply",
        audit=audit,
    )


def can_send(envelope: ReplyEnvelope | Mapping[str, Any] | None) -> bool:
    payload = envelope.as_dict() if isinstance(envelope, ReplyEnvelope) else dict(envelope or {})
    return (
        str(payload.get("kind") or "").strip().lower() == FINAL_REPLY
        and bool(payload.get("sendable"))
        and bool(str(payload.get("text") or "").strip())
    )


def can_persist(envelope: ReplyEnvelope | Mapping[str, Any] | None) -> bool:
    payload = envelope.as_dict() if isinstance(envelope, ReplyEnvelope) else dict(envelope or {})
    return (
        str(payload.get("kind") or "").strip().lower() == FINAL_REPLY
        and bool(payload.get("persistable"))
        and bool(str(payload.get("text") or "").strip())
    )


def can_cache(envelope: ReplyEnvelope | Mapping[str, Any] | None) -> bool:
    payload = envelope.as_dict() if isinstance(envelope, ReplyEnvelope) else dict(envelope or {})
    return (
        str(payload.get("kind") or "").strip().lower() == FINAL_REPLY
        and bool(payload.get("cacheable"))
        and not bool(payload.get("degraded"))
        and bool(str(payload.get("text") or "").strip())
    )


__all__ = [
    "ALLOWED_REPLY_KINDS",
    "FINAL_REPLY",
    "NO_REPLY",
    "TASK_STATUS",
    "TRANSPORT_ACK",
    "ReplyEnvelope",
    "can_cache",
    "can_persist",
    "can_send",
    "envelope_from_commit",
]
