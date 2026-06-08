from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from host_brain.app.reply_performance import ReplyPerformance, coerce_reply_performance
from host_brain.app.response_form_contract import (
    has_structured_media_payload,
    is_mixed_media_response_form,
    is_pure_media_response_form,
    normalize_response_form,
    response_form_requires_text,
)


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
    performance: ReplyPerformance | Mapping[str, Any] = field(default_factory=ReplyPerformance)
    media_ref: str = ""
    reaction_id: str = ""
    raw_extra: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        kind = _normalize_kind(self.kind)
        text = str(self.text or "").strip()
        response_form = normalize_response_form(str(self.response_form or "text"))
        media_ref = str(self.media_ref or "").strip()
        reaction_id = str(self.reaction_id or "").strip()
        raw_extra = dict(self.raw_extra or {})
        raw_media_ref = str(raw_extra.get("media_ref") or raw_extra.get("media_path") or "").strip()
        has_media_payload = has_structured_media_payload(
            response_form=response_form,
            media_ref=media_ref,
            reaction_id=reaction_id,
            raw_extra=raw_extra,
        )
        if is_pure_media_response_form(response_form):
            has_deliverable_output = has_media_payload
        elif is_mixed_media_response_form(response_form):
            has_deliverable_output = bool(text and has_media_payload)
        elif response_form_requires_text(response_form):
            has_deliverable_output = bool(text)
        else:
            has_deliverable_output = bool(text or media_ref or raw_media_ref)
        is_final = kind == FINAL_REPLY and has_deliverable_output and not bool(self.degraded)
        normalized_kind = kind
        if kind == FINAL_REPLY and not is_final:
            normalized_kind = NO_REPLY
        object.__setattr__(self, "kind", normalized_kind)
        object.__setattr__(self, "text", text)
        object.__setattr__(self, "response_form", response_form)
        object.__setattr__(self, "source", str(self.source or "").strip())
        object.__setattr__(
            self,
            "reason_code",
            str(self.reason_code or ("INVALID_REPLY_ENVELOPE" if normalized_kind == NO_REPLY and kind == FINAL_REPLY else "")).strip(),
        )
        object.__setattr__(self, "media_ref", media_ref)
        object.__setattr__(self, "reaction_id", reaction_id)
        object.__setattr__(self, "sendable", bool(self.sendable and is_final))
        object.__setattr__(self, "persistable", bool(self.persistable and is_final))
        object.__setattr__(self, "cacheable", bool(self.cacheable and is_final))
        object.__setattr__(self, "audit", dict(self.audit or {}))
        object.__setattr__(self, "performance", coerce_reply_performance(self.performance))
        object.__setattr__(self, "raw_extra", raw_extra)

    @classmethod
    def final_reply(
        cls,
        *,
        text: str,
        response_form: str = "text",
        source: str = "full_model",
        cacheable: bool = True,
        audit: Mapping[str, Any] | None = None,
        performance: ReplyPerformance | Mapping[str, Any] | None = None,
        media_ref: str = "",
        reaction_id: str = "",
        raw_extra: Mapping[str, Any] | None = None,
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
            performance=performance or ReplyPerformance(source=source),
            media_ref=media_ref,
            reaction_id=reaction_id,
            raw_extra=dict(raw_extra or {}),
        )

    @classmethod
    def no_reply(
        cls,
        *,
        reason_code: str,
        source: str = "fallback_policy",
        response_form: str = "text",
        audit: Mapping[str, Any] | None = None,
    ) -> "ReplyEnvelope":
        return cls(
            kind=NO_REPLY,
            text="",
            response_form=response_form,
            source=source,
            sendable=False,
            persistable=False,
            cacheable=False,
            degraded=True,
            reason_code=reason_code,
            audit=dict(audit or {}),
            performance=ReplyPerformance(source=source),
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
            "performance": self.performance.as_dict(),
            "media_ref": self.media_ref,
            "reaction_id": self.reaction_id,
            "raw_extra": dict(self.raw_extra or {}),
        }


def coerce_reply_envelope(raw: ReplyEnvelope | Mapping[str, Any] | None) -> ReplyEnvelope:
    if isinstance(raw, ReplyEnvelope):
        return raw
    payload = dict(raw or {}) if isinstance(raw, Mapping) else {}
    return ReplyEnvelope(
        kind=str(payload.get("kind") or NO_REPLY),
        text=str(payload.get("text") or ""),
        response_form=str(payload.get("response_form") or "text"),
        source=str(payload.get("source") or ""),
        sendable=bool(payload.get("sendable") or False),
        persistable=bool(payload.get("persistable") or False),
        cacheable=bool(payload.get("cacheable") or False),
        degraded=bool(payload.get("degraded") or False),
        reason_code=str(payload.get("reason_code") or ""),
        audit=dict(payload.get("audit") or {}),
        performance=payload.get("performance") or {},
        media_ref=str(payload.get("media_ref") or ""),
        reaction_id=str(payload.get("reaction_id") or ""),
        raw_extra=dict(payload.get("raw_extra") or {}),
    )


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
    performance: ReplyPerformance | Mapping[str, Any] | None = None,
    media_ref: str = "",
    reaction_id: str = "",
    raw_extra: Mapping[str, Any] | None = None,
) -> ReplyEnvelope:
    normalized_decision = str(decision or "").strip().upper()
    text = str(narrative or "").strip()
    raw_extra_payload = dict(raw_extra or {})
    effective_response_form = normalize_response_form(str(response_form or "text"))
    has_media_payload = has_structured_media_payload(
        response_form=effective_response_form,
        media_ref=str(media_ref or "").strip(),
        reaction_id=str(reaction_id or "").strip(),
        raw_extra=raw_extra_payload,
    )
    raw_media_ref = str(raw_extra_payload.get("media_ref") or raw_extra_payload.get("media_path") or "").strip()
    if text and not has_media_payload and (
        is_pure_media_response_form(effective_response_form) or is_mixed_media_response_form(effective_response_form)
    ):
        raw_extra_payload.setdefault("response_form_repaired_from", effective_response_form)
        effective_response_form = "text"
    if normalized_decision == "APPROVE" and (text or str(media_ref or "").strip() or raw_media_ref) and not bool(degraded):
        return ReplyEnvelope.final_reply(
            text=text,
            response_form=effective_response_form,
            source=source or "full_model",
            cacheable=cacheable,
            audit=audit,
            performance=performance,
            media_ref=media_ref,
            reaction_id=reaction_id,
            raw_extra=raw_extra_payload,
        )
    return ReplyEnvelope.no_reply(
        reason_code=str(reason_code or normalized_decision or "NO_REPLY").strip() or "NO_REPLY",
        source=source or "no_reply",
        response_form=effective_response_form,
        audit=audit,
    )


def can_send(envelope: ReplyEnvelope | Mapping[str, Any] | None) -> bool:
    payload = coerce_reply_envelope(envelope).as_dict()
    return (
        str(payload.get("kind") or "").strip().lower() == FINAL_REPLY
        and bool(payload.get("sendable"))
        and _has_deliverable_output(payload)
    )


def can_persist(envelope: ReplyEnvelope | Mapping[str, Any] | None) -> bool:
    payload = coerce_reply_envelope(envelope).as_dict()
    return (
        str(payload.get("kind") or "").strip().lower() == FINAL_REPLY
        and bool(payload.get("persistable"))
        and _has_deliverable_output(payload)
    )


def can_cache(envelope: ReplyEnvelope | Mapping[str, Any] | None) -> bool:
    payload = coerce_reply_envelope(envelope).as_dict()
    return (
        str(payload.get("kind") or "").strip().lower() == FINAL_REPLY
        and bool(payload.get("cacheable"))
        and not bool(payload.get("degraded"))
        and _has_deliverable_output(payload)
    )


def _has_deliverable_output(payload: Mapping[str, Any]) -> bool:
    raw_extra = dict(payload.get("raw_extra") or {}) if isinstance(payload.get("raw_extra"), Mapping) else {}
    response_form = normalize_response_form(str(payload.get("response_form") or "text"))
    text = str(payload.get("text") or "").strip()
    media_ref = str(payload.get("media_ref") or "").strip()
    reaction_id = str(payload.get("reaction_id") or "").strip()
    has_media_payload = has_structured_media_payload(
        response_form=response_form,
        media_ref=media_ref,
        reaction_id=reaction_id,
        raw_extra=raw_extra,
    )
    if is_pure_media_response_form(response_form):
        return has_media_payload
    if is_mixed_media_response_form(response_form):
        return bool(text and has_media_payload)
    if response_form_requires_text(response_form):
        return bool(text)
    return bool(text or media_ref or str(raw_extra.get("media_ref") or raw_extra.get("media_path") or "").strip())


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
    "coerce_reply_envelope",
    "envelope_from_commit",
]
