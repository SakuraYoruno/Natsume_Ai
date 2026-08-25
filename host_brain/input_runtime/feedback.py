from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from typing import Any, Mapping

from host_brain.agent_runtime.skill_ids import AGENT_RUNTIME_SKILL_IDS
from host_brain.persistence.response_feedback_repository import (
    ResponseFeedbackRepository,
)


_NEGATIVE_TOKEN_RULES: tuple[tuple[re.Pattern[str], tuple[str, ...]], ...] = (
    (re.compile(r"AI\s*味|ai\s*味|太ai|像ai", re.IGNORECASE), ("ai_tone",)),
    (re.compile(r"模板|模板化|太模板"), ("template_like",)),
    (re.compile(r"不像|不符人设|不像她|不像旧角色"), ("out_of_persona",)),
    (re.compile(r"逻辑不对|逻辑怪|不是这个意思|莫名其妙"), ("logic_break",)),
    (re.compile(r"别这么说|我不喜欢这样|别这样说"), ("too_helpful", "out_of_persona")),
)
_POSITIVE_TOKEN_RULES: tuple[tuple[re.Pattern[str], tuple[str, ...]], ...] = (
    (re.compile(r"这次对了|这样就对|这句像|这个可以"), ("in_persona",)),
)
_SPACE_RE = re.compile(r"\s+")
_CHAT_REPLY_STAGE_PREFIX = "surface_reply"
_NON_PERSONA_SUB_AGENTS: frozenset[str] = frozenset(
    {
        "open_app",
        "translate_agent",
        "intent_agent",
        "retrieval_agent",
        "judge_agent",
        *AGENT_RUNTIME_SKILL_IDS,
    }
)


@dataclass(slots=True)
class ResponseFeedbackRecord:
    target_request_id: str
    session_id: str
    person_id: str
    thread_id: str | None
    target_text_hash: str
    feedback_score: float
    feedback_tags: list[str]
    feedback_source: str
    created_at_ms: int
    active: bool = True


def _get_ledger(ledger: Any | None = None) -> Any:
    if ledger is not None:
        return ledger
    raise RuntimeError("feedback ledger must be supplied explicitly")


def _coerce_payload_dict(raw_payload: Any) -> dict[str, Any]:
    if isinstance(raw_payload, Mapping):
        return dict(raw_payload)
    if isinstance(raw_payload, str):
        try:
            parsed = json.loads(raw_payload)
        except Exception:
            return {}
        if isinstance(parsed, Mapping):
            return dict(parsed)
    return {}


def normalize_feedback_text(text: Any) -> str:
    normalized = _SPACE_RE.sub(" ", str(text or "").strip())
    return normalized


def hash_feedback_text(text: Any) -> str:
    normalized = normalize_feedback_text(text)
    if not normalized:
        return ""
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()


def detect_explicit_response_feedback(user_message: str) -> dict[str, Any] | None:
    normalized = normalize_feedback_text(user_message)
    if not normalized:
        return None

    tags: set[str] = set()
    negative = False
    positive = False

    for pattern, rule_tags in _NEGATIVE_TOKEN_RULES:
        if pattern.search(normalized):
            tags.update(rule_tags)
            negative = True

    if not negative:
        for pattern, rule_tags in _POSITIVE_TOKEN_RULES:
            if pattern.search(normalized):
                tags.update(rule_tags)
                positive = True

    if negative:
        return {
            "feedback_score": -1.0,
            "feedback_tags": sorted(tags) or ["out_of_persona"],
            "feedback_source": "user_explicit",
            "feedback_kind": "negative",
        }
    if positive:
        return {
            "feedback_score": 1.0,
            "feedback_tags": sorted(tags) or ["in_persona"],
            "feedback_source": "user_positive",
            "feedback_kind": "positive",
        }
    return None


def _is_persona_reply_row(*, row: Mapping[str, Any], payload: Mapping[str, Any]) -> bool:
    if str(row.get("event_type") or "").strip().upper() != "INPUT_COMPLETED":
        return False
    if not str(row.get("stage") or "").strip().lower().startswith(_CHAT_REPLY_STAGE_PREFIX):
        return False
    source = str(payload.get("source") or "").strip().upper()
    if source not in {"GATEWAY", "CHAT_API"}:
        return False
    sub_agent_id = str(payload.get("sub_agent_id") or "").strip().lower()
    if sub_agent_id and sub_agent_id in _NON_PERSONA_SUB_AGENTS:
        return False
    return bool(normalize_feedback_text(payload.get("reply_preview")))


def find_latest_persona_reply_target(
    *,
    session_id: str,
    person_id: str | None = None,
    ledger: Any | None = None,
) -> dict[str, Any] | None:
    resolved_session_id = str(session_id or "").strip()
    if not resolved_session_id:
        return None

    resolved_person_id = str(person_id or "").strip()
    current_ledger = _get_ledger(ledger)
    repository = ResponseFeedbackRepository.from_ledger(current_ledger)
    if repository is None:
        return None

    rows = repository.latest_persona_reply_rows(session_id=resolved_session_id)
    for raw_row in list(rows or []):
        row = dict(raw_row)
        payload = _coerce_payload_dict(row.get("payload"))
        if resolved_person_id and str(payload.get("person_id") or "").strip() != resolved_person_id:
            continue
        if not _is_persona_reply_row(row=row, payload=payload):
            continue
        reply_preview = normalize_feedback_text(payload.get("reply_preview"))
        if not reply_preview:
            continue
        return {
            "target_request_id": str(row.get("request_id") or "").strip(),
            "session_id": resolved_session_id,
            "person_id": resolved_person_id,
            "thread_id": str(payload.get("thread_id") or "").strip() or None,
            "target_text": reply_preview,
            "target_text_hash": hash_feedback_text(reply_preview),
            "created_at_ms": int(row.get("ts_ms") or 0),
            "payload": payload,
        }
    return None


def insert_response_feedback_record(
    record: ResponseFeedbackRecord,
    *,
    ledger: Any | None = None,
) -> None:
    current_ledger = _get_ledger(ledger)
    repository = ResponseFeedbackRepository.from_ledger(current_ledger)
    if repository is None:
        return
    repository.insert(
        {
            "target_request_id": record.target_request_id,
            "session_id": record.session_id,
            "person_id": record.person_id,
            "thread_id": record.thread_id,
            "target_text_hash": record.target_text_hash,
            "feedback_score": float(record.feedback_score),
            "feedback_tags": json.dumps(
                list(record.feedback_tags or []),
                ensure_ascii=False,
            ),
            "feedback_source": record.feedback_source,
            "created_at_ms": int(record.created_at_ms or time.time() * 1000),
            "active": record.active,
        }
    )


def record_feedback_for_latest_persona_reply(
    *,
    session_id: str,
    person_id: str | None,
    user_message: str,
    ledger: Any | None = None,
    ts_ms: int | None = None,
) -> dict[str, Any] | None:
    detected = detect_explicit_response_feedback(user_message)
    if not detected:
        return None

    latest = find_latest_persona_reply_target(
        session_id=session_id,
        person_id=person_id,
        ledger=ledger,
    )
    if not latest:
        return None

    record = ResponseFeedbackRecord(
        target_request_id=str(latest.get("target_request_id") or "").strip(),
        session_id=str(latest.get("session_id") or "").strip(),
        person_id=str(latest.get("person_id") or "").strip(),
        thread_id=str(latest.get("thread_id") or "").strip() or None,
        target_text_hash=str(latest.get("target_text_hash") or "").strip(),
        feedback_score=float(detected.get("feedback_score") or 0.0),
        feedback_tags=list(detected.get("feedback_tags") or []),
        feedback_source=str(detected.get("feedback_source") or "user_explicit"),
        created_at_ms=int(ts_ms or time.time() * 1000),
        active=True,
    )
    insert_response_feedback_record(record, ledger=ledger)
    return {
        "target_request_id": record.target_request_id,
        "target_text_hash": record.target_text_hash,
        "feedback_score": record.feedback_score,
        "feedback_tags": list(record.feedback_tags),
        "feedback_source": record.feedback_source,
        "feedback_kind": str(detected.get("feedback_kind") or ""),
    }
