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
    from host_brain.app.dependencies import get_ledger  # noqa: PLC0415

    return get_ledger()


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


def _fetch_feedback_rows(
    *,
    session_id: str | None = None,
    person_id: str | None = None,
    request_ids: list[str] | None = None,
    text_hashes: list[str] | None = None,
    ledger: Any | None = None,
) -> list[dict[str, Any]]:
    cleaned_request_ids = [str(item).strip() for item in list(request_ids or []) if str(item).strip()]
    cleaned_hashes = [str(item).strip() for item in list(text_hashes or []) if str(item).strip()]
    if not cleaned_request_ids and not cleaned_hashes:
        return []

    current_ledger = _get_ledger(ledger)
    repository = ResponseFeedbackRepository.from_ledger(current_ledger)
    if repository is None:
        return []
    resolved_session_id = str(session_id or "").strip()
    resolved_person_id = str(person_id or "").strip()
    return repository.fetch(
        session_id=resolved_session_id,
        person_id=resolved_person_id,
        request_ids=cleaned_request_ids,
        text_hashes=cleaned_hashes,
    )


def _merge_feedback_entry(entries: list[dict[str, Any]]) -> dict[str, Any]:
    negative_scores: list[float] = []
    positive_scores: list[float] = []
    tags: set[str] = set()
    for row in entries:
        try:
            score = float(row.get("feedback_score") or 0.0)
        except (TypeError, ValueError):
            score = 0.0
        if score < 0:
            negative_scores.append(score)
        elif score > 0:
            positive_scores.append(score)
        raw_tags = row.get("feedback_tags")
        if isinstance(raw_tags, str) and raw_tags.strip():
            try:
                parsed_tags = json.loads(raw_tags)
            except Exception:
                parsed_tags = [raw_tags]
        elif isinstance(raw_tags, list):
            parsed_tags = raw_tags
        else:
            parsed_tags = []
        for tag in list(parsed_tags or []):
            normalized = str(tag or "").strip()
            if normalized:
                tags.add(normalized)

    score = 0.0
    if negative_scores:
        score = min(negative_scores)
    elif positive_scores:
        score = max(positive_scores)

    return {
        "feedback_score": score,
        "feedback_tags": sorted(tags),
        "feedback_negative_count": len(negative_scores),
        "feedback_positive_count": len(positive_scores),
        "active": True,
    }


def build_feedback_lookup(
    *,
    session_id: str | None = None,
    person_id: str | None = None,
    request_ids: list[str] | None = None,
    text_hashes: list[str] | None = None,
    ledger: Any | None = None,
) -> dict[str, Any]:
    rows = _fetch_feedback_rows(
        session_id=session_id,
        person_id=person_id,
        request_ids=request_ids,
        text_hashes=text_hashes,
        ledger=ledger,
    )
    by_request_id_raw: dict[str, list[dict[str, Any]]] = {}
    by_text_hash_raw: dict[str, list[dict[str, Any]]] = {}
    total_negative = 0
    total_positive = 0
    for row in rows:
        score = float(row.get("feedback_score") or 0.0)
        if score < 0:
            total_negative += 1
        elif score > 0:
            total_positive += 1
        request_id = str(row.get("target_request_id") or "").strip()
        text_hash = str(row.get("target_text_hash") or "").strip()
        if request_id:
            by_request_id_raw.setdefault(request_id, []).append(row)
        if text_hash:
            by_text_hash_raw.setdefault(text_hash, []).append(row)

    return {
        "by_request_id": {key: _merge_feedback_entry(value) for key, value in by_request_id_raw.items()},
        "by_text_hash": {key: _merge_feedback_entry(value) for key, value in by_text_hash_raw.items()},
        "feedback_negative_count": total_negative,
        "feedback_positive_count": total_positive,
    }


def merge_feedback_summaries(*summaries: Mapping[str, Any] | None) -> dict[str, Any]:
    valid = [dict(item) for item in summaries if isinstance(item, Mapping)]
    if not valid:
        return {}

    negative_scores = [
        float(item.get("feedback_score") or 0.0)
        for item in valid
        if float(item.get("feedback_score") or 0.0) < 0
    ]
    positive_scores = [
        float(item.get("feedback_score") or 0.0)
        for item in valid
        if float(item.get("feedback_score") or 0.0) > 0
    ]
    tags: set[str] = set()
    for item in valid:
        for tag in list(item.get("feedback_tags") or []):
            normalized = str(tag or "").strip()
            if normalized:
                tags.add(normalized)

    score = 0.0
    if negative_scores:
        score = min(negative_scores)
    elif positive_scores:
        score = max(positive_scores)

    return {
        "feedback_score": score,
        "feedback_tags": sorted(tags),
        "feedback_negative_count": sum(int(item.get("feedback_negative_count") or 0) for item in valid),
        "feedback_positive_count": sum(int(item.get("feedback_positive_count") or 0) for item in valid),
    }


def resolve_candidate_feedback_summary(
    *,
    request_id: str | None,
    text_hash: str | None,
    feedback_lookup: Mapping[str, Any] | None,
) -> dict[str, Any]:
    lookup = dict(feedback_lookup or {})
    by_request_id = dict(lookup.get("by_request_id") or {})
    by_text_hash = dict(lookup.get("by_text_hash") or {})
    request_summary = by_request_id.get(str(request_id or "").strip())
    hash_summary = by_text_hash.get(str(text_hash or "").strip())
    return merge_feedback_summaries(request_summary, hash_summary)


def is_feedback_blocking(summary: Mapping[str, Any] | None, *, threshold: float = -0.7) -> bool:
    if not isinstance(summary, Mapping):
        return False
    try:
        score = float(summary.get("feedback_score") or 0.0)
    except (TypeError, ValueError):
        return False
    return score <= threshold


def annotate_continuity_rows_with_feedback(
    rows: list[dict[str, Any]],
    *,
    session_id: str,
    person_id: str | None = None,
    ledger: Any | None = None,
) -> list[dict[str, Any]]:
    request_ids: list[str] = []
    text_hashes: list[str] = []
    prepared_rows: list[dict[str, Any]] = []

    for raw_row in list(rows or []):
        if not isinstance(raw_row, Mapping):
            continue
        row = dict(raw_row)
        payload = _coerce_payload_dict(row.get("payload"))
        row["payload"] = payload
        request_id = str(row.get("request_id") or "").strip()
        reply_preview = normalize_feedback_text(payload.get("reply_preview"))
        text_hash = hash_feedback_text(reply_preview)
        if request_id:
            request_ids.append(request_id)
        if text_hash:
            text_hashes.append(text_hash)
        prepared_rows.append(row)

    lookup = build_feedback_lookup(
        session_id=session_id,
        person_id=person_id,
        request_ids=request_ids,
        text_hashes=text_hashes,
        ledger=ledger,
    )
    annotated_rows: list[dict[str, Any]] = []
    for row in prepared_rows:
        payload = dict(row.get("payload") or {})
        request_id = str(row.get("request_id") or "").strip()
        reply_preview = normalize_feedback_text(payload.get("reply_preview"))
        feedback_summary = resolve_candidate_feedback_summary(
            request_id=request_id,
            text_hash=hash_feedback_text(reply_preview),
            feedback_lookup=lookup,
        )
        if feedback_summary:
            row["_assistant_feedback"] = feedback_summary
        annotated_rows.append(row)
    return annotated_rows


def get_recent_reply_feedback_summary(
    *,
    session_id: str,
    person_id: str | None = None,
    ledger: Any | None = None,
) -> dict[str, Any]:
    latest = find_latest_persona_reply_target(
        session_id=session_id,
        person_id=person_id,
        ledger=ledger,
    )
    if not latest:
        return {}
    lookup = build_feedback_lookup(
        session_id=session_id,
        person_id=person_id,
        request_ids=[str(latest.get("target_request_id") or "").strip()],
        text_hashes=[str(latest.get("target_text_hash") or "").strip()],
        ledger=ledger,
    )
    return resolve_candidate_feedback_summary(
        request_id=str(latest.get("target_request_id") or "").strip(),
        text_hash=str(latest.get("target_text_hash") or "").strip(),
        feedback_lookup=lookup,
    )


def _coerce_mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _resolve_candidate_request_id(
    item: Mapping[str, Any],
    *,
    request_id_keys: tuple[str, ...],
) -> str:
    item_map = dict(item)
    metadata = _coerce_mapping(item_map.get("metadata"))
    for key in request_id_keys:
        value = str(item_map.get(key) or metadata.get(key) or "").strip()
        if value:
            return value
    return ""


def _resolve_candidate_text_hash(
    item: Mapping[str, Any],
    *,
    text_keys: tuple[str, ...],
) -> str:
    item_map = dict(item)
    metadata = _coerce_mapping(item_map.get("metadata"))
    for key in text_keys:
        text = normalize_feedback_text(item_map.get(key) or metadata.get(key))
        if text:
            return hash_feedback_text(text)
    return ""


def _resolve_candidate_ref(
    item: Mapping[str, Any],
    *,
    ref_keys: tuple[str, ...],
) -> str:
    item_map = dict(item)
    metadata = _coerce_mapping(item_map.get("metadata"))
    for key in ref_keys:
        value = str(item_map.get(key) or metadata.get(key) or "").strip()
        if value:
            return value
    return ""


def filter_badcase_mapping_items(
    items: list[dict[str, Any]],
    *,
    session_id: str,
    person_id: str | None = None,
    ledger: Any | None = None,
    request_id_keys: tuple[str, ...] = ("request_id", "target_request_id"),
    text_keys: tuple[str, ...] = ("content", "text", "snippet"),
    ref_keys: tuple[str, ...] = ("evidence_ref", "source_ref"),
    threshold: float = -0.7,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    prepared: list[dict[str, Any]] = []
    request_ids: list[str] = []
    text_hashes: list[str] = []

    for raw_item in list(items or []):
        if not isinstance(raw_item, Mapping):
            continue
        item = dict(raw_item)
        request_id = _resolve_candidate_request_id(item, request_id_keys=request_id_keys)
        text_hash = _resolve_candidate_text_hash(item, text_keys=text_keys)
        if request_id:
            request_ids.append(request_id)
        if text_hash:
            text_hashes.append(text_hash)
        item["_feedback_request_id"] = request_id
        item["_feedback_text_hash"] = text_hash
        prepared.append(item)

    lookup = build_feedback_lookup(
        session_id=session_id,
        person_id=person_id,
        request_ids=request_ids,
        text_hashes=text_hashes,
        ledger=ledger,
    )
    recent_summary = get_recent_reply_feedback_summary(
        session_id=session_id,
        person_id=person_id,
        ledger=ledger,
    )

    filtered: list[dict[str, Any]] = []
    filtered_refs: list[str] = []
    for item in prepared:
        feedback_summary = resolve_candidate_feedback_summary(
            request_id=str(item.get("_feedback_request_id") or "").strip(),
            text_hash=str(item.get("_feedback_text_hash") or "").strip(),
            feedback_lookup=lookup,
        )
        if feedback_summary:
            item["_feedback_summary"] = feedback_summary
        if is_feedback_blocking(feedback_summary, threshold=threshold):
            ref = _resolve_candidate_ref(item, ref_keys=ref_keys)
            if ref and ref not in filtered_refs:
                filtered_refs.append(ref)
            continue
        item.pop("_feedback_request_id", None)
        item.pop("_feedback_text_hash", None)
        filtered.append(item)

    recent_score = None
    try:
        recent_score = float(recent_summary.get("feedback_score")) if recent_summary else None
    except (TypeError, ValueError):
        recent_score = None

    return filtered, {
        "badcase_filtered_count": max(0, len(prepared) - len(filtered)),
        "badcase_filtered_refs": filtered_refs,
        "feedback_negative_count": int(lookup.get("feedback_negative_count") or 0),
        "feedback_positive_count": int(lookup.get("feedback_positive_count") or 0),
        "recent_reply_feedback_score": recent_score,
        "recent_reply_feedback_tags": list(recent_summary.get("feedback_tags") or []) if recent_summary else [],
    }


def filter_badcase_text_lines(
    lines: list[str],
    *,
    session_id: str,
    person_id: str | None = None,
    ledger: Any | None = None,
    threshold: float = -0.7,
) -> tuple[list[str], dict[str, Any]]:
    normalized_lines = [normalize_feedback_text(line) for line in list(lines or []) if normalize_feedback_text(line)]
    text_hashes = [hash_feedback_text(line) for line in normalized_lines if hash_feedback_text(line)]
    lookup = build_feedback_lookup(
        session_id=session_id,
        person_id=person_id,
        text_hashes=text_hashes,
        ledger=ledger,
    )
    recent_summary = get_recent_reply_feedback_summary(
        session_id=session_id,
        person_id=person_id,
        ledger=ledger,
    )

    filtered: list[str] = []
    filtered_refs: list[str] = []
    for line in normalized_lines:
        text_hash = hash_feedback_text(line)
        feedback_summary = resolve_candidate_feedback_summary(
            request_id="",
            text_hash=text_hash,
            feedback_lookup=lookup,
        )
        if is_feedback_blocking(feedback_summary, threshold=threshold):
            preview = _SPACE_RE.sub(" ", line)[:80]
            if preview and preview not in filtered_refs:
                filtered_refs.append(preview)
            continue
        filtered.append(line)

    recent_score = None
    try:
        recent_score = float(recent_summary.get("feedback_score")) if recent_summary else None
    except (TypeError, ValueError):
        recent_score = None

    return filtered, {
        "badcase_filtered_count": max(0, len(normalized_lines) - len(filtered)),
        "badcase_filtered_refs": filtered_refs,
        "feedback_negative_count": int(lookup.get("feedback_negative_count") or 0),
        "feedback_positive_count": int(lookup.get("feedback_positive_count") or 0),
        "recent_reply_feedback_score": recent_score,
        "recent_reply_feedback_tags": list(recent_summary.get("feedback_tags") or []) if recent_summary else [],
    }
