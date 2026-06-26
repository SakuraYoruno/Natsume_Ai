"""Life memory semantic functions.

Proposal generation, pruning, summary building, collection/entity processing,
sleep replay selection, and diagnostic helpers.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any, TYPE_CHECKING

from .constants import *
from .constants import (
    _ANAPHORIC_SELECTION_RE,
    _SEMANTIC_PRIORITY_MEMORY_CLASSES,
    _SYNTHETIC_ID_MARKERS,
    _SYNTHETIC_SOURCE_MARKERS,
)
from .utils import (
    _clean_text,
    _clamp_unit,
    _coerce_int,
    _json_loads,
    _json_dumps,
    _stable_hash,
    _has_any_marker,
    _episode_metadata_dict,
    _is_synthetic_life_record,
    _normalize_refs,
    _metadata_float,
    _semantic_statement_is_lookup_question,
    _semantic_looks_like_recommendation_report,
    _semantic_is_user_preference,
    _semantic_is_commitment,
    _semantic_is_self_change,
    _semantic_is_relationship_feedback,
    _semantic_is_failure_lesson,
    _semantic_is_stable_fact,
    _semantic_is_correction_fact,
    _normalize_open_loop_status,
    _semantic_commitment_status,
    _query_wants_closed_open_loop,
    _preference_query_wants_relationship_feedback,
    _should_sleep_replay_candidate,
    _looks_like_recommendation_followup_chat_noise,
    _open_loop_close_reason_for_status,
    _open_loop_status_from_tags,
    _normalize_open_loop_status_tags,
    _open_loop_title_from_summary,
    _semantic_text_is_sensitive_or_noise,
    _semantic_text_has_secret,
    _semantic_operational_failure_summary,
    _semantic_extract_field,
    _semantic_skip_reason_for_texts,
    _semantic_statement_body,
)

if TYPE_CHECKING:
    from .store import LifeMemoryStore

def _attachment_looks_visual(attachment: Mapping[str, Any]) -> bool:
    type_text = str(attachment.get("type") or attachment.get("mime") or attachment.get("mime_type") or "").strip().lower()
    return any(token in type_text for token in ("image", "photo", "vision", "screenshot", "sticker", "emoji"))


def _chat_salience(*, user_message: str, assistant_reply: str, has_perception: bool) -> float:
    text = f"{user_message}\n{assistant_reply}".lower()
    score = 0.36
    if has_perception:
        score += 0.18
    if any(token in text for token in ("记住", "以后", "别忘", "纠正", "不对", "错了", "remember", "wrong")):
        score += 0.20
    if len(text) > 500:
        score += 0.06
    return _clamp_unit(score)


def _background_salience(*, source: str, status: str | None, detail: str | None) -> float:
    source_key = str(source or "").strip().lower()
    status_key = str(status or "").strip().upper()
    score = 0.30
    if source_key in {"inner_world", "inner_stream", "inner_activity"}:
        score += 0.12
    if source_key in {"vision", "screen_ocr", "camera", "camera_recognition", "perception", "voice", "audio", "asr"}:
        score += 0.14
    if source_key in {"action", "gateway", "channel", "skill", "mcp", "tool", "sandbox", "avatar", "clawd", "codex_production", "browser"}:
        score += 0.08
    if status_key in {"ERROR", "FAILED", "DEGRADED", "SUSPENDED", "DEFERRED", "HEARTBEAT_ALERT", "EDGE_PROBE_ALERT"}:
        score += 0.25
    if status_key in {"COMPLETED", "SUCCESS", "SENT", "EXECUTED"}:
        score += 0.04
    if _clean_text(detail):
        score += 0.08
    return _clamp_unit(score)


def _collection_id(
    *,
    collection_kind: str,
    domain: str,
    scope_key: str,
    person_id: str | None,
    session_id: str | None,
) -> str:
    owner = _clean_text(session_id) or _clean_text(person_id) or "global"
    seed = f"life_collection:v1:{collection_kind}:{domain}:{scope_key}:{owner}"
    return f"col_{_stable_hash(seed, size=32)}"


def _entity_id(*, entity_kind: str, domain: str, normalized_key: str) -> str:
    seed = f"life_entity:v1:{_clean_text(entity_kind)}:{_clean_text(domain)}:{_clean_text(normalized_key)}"
    return f"ent_{_stable_hash(seed, size=32)}"


def _entity_key_from_item(*, title: str, source_ref: str | None) -> str:
    source = _clean_text(source_ref, limit=500).lower()
    if source:
        return f"source:{_stable_hash(source, size=28)}"
    return _entity_normalized_key(title)


def _entity_normalized_key(value: Any) -> str:
    text = _clean_text(value, limit=300).lower()
    text = re.sub(r"https?://", "", text)
    text = re.sub(r"[\s\u3000]+", "", text)
    text = re.sub(r"[《》「」『』【】\[\]（）()<>\"'`，。！？!?,.;:：；、|/\\]+", "", text)
    return text[:180]


def _collection_scope_window_days(scope_key: Any) -> int:
    scope = _clean_text(scope_key).lower()
    if scope == COLLECTION_SCOPE_RECENT:
        return COLLECTION_SCOPE_RECENT_WINDOW_DAYS
    match = re.fullmatch(rf"{COLLECTION_SCOPE_ROLLING_PREFIX}:(\d+)d", scope)
    if not match:
        return 0
    return max(1, int(match.group(1)))


def _collection_scope_key(*, scope: str, day_key: str, episode_id: str) -> str:
    normalized = _clean_text(scope)
    if normalized == COLLECTION_SCOPE_DAILY:
        return f"{COLLECTION_SCOPE_DAILY}:{day_key}"
    if normalized == COLLECTION_SCOPE_DELIVERY:
        return f"{COLLECTION_SCOPE_DELIVERY}:{_clean_text(episode_id)}"
    return normalized or COLLECTION_SCOPE_RECENT


def _collection_day_key(ts_ms: int) -> str:
    return datetime.fromtimestamp(int(ts_ms) / 1000.0, tz=_LOCAL_MEMORY_TZ).strftime("%Y-%m-%d")


def _scope_keys_for_collection_plan(*, plan: Any, now_ts_ms_value: int) -> list[str]:
    scope = str(getattr(plan, "temporal_scope", "") or "").strip()
    lookback_days = _coerce_int(getattr(plan, "time_lookback_days", None))
    today = datetime.fromtimestamp(int(now_ts_ms_value) / 1000.0, tz=_LOCAL_MEMORY_TZ).date()
    if scope == TEMPORAL_CURRENT:
        return []
    if scope in {TEMPORAL_RECENT_DAYS, ""}:
        if lookback_days > 0 and lookback_days <= 3:
            return [COLLECTION_SCOPE_ROLLING_3D, COLLECTION_SCOPE_RECENT]
        if lookback_days > 7:
            return [COLLECTION_SCOPE_ROLLING_30D, COLLECTION_SCOPE_RECENT]
        return [COLLECTION_SCOPE_ROLLING_7D, COLLECTION_SCOPE_RECENT]
    if scope == TEMPORAL_TODAY:
        return [f"{COLLECTION_SCOPE_DAILY}:{today.isoformat()}"]
    if scope == TEMPORAL_YESTERDAY:
        return [f"{COLLECTION_SCOPE_DAILY}:{(today - timedelta(days=1)).isoformat()}"]
    if scope == TEMPORAL_DAY_BEFORE_YESTERDAY:
        return [f"{COLLECTION_SCOPE_DAILY}:{(today - timedelta(days=2)).isoformat()}"]
    if scope.startswith(TEMPORAL_DATE_PREFIX):
        day_key = scope.removeprefix(TEMPORAL_DATE_PREFIX).strip()
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", day_key):
            return [f"{COLLECTION_SCOPE_DAILY}:{day_key}"]
    if scope == TEMPORAL_ALL_TIME:
        return []
    return [COLLECTION_SCOPE_RECENT]


def _plan_requires_exact_collection_count(plan: Any) -> bool:
    requirements = {
        _clean_text(item)
        for item in list(getattr(plan, "validation_requirements", ()) or ())
        if _clean_text(item)
    }
    return "collection_item_exact_count" in requirements and _coerce_int(getattr(plan, "min_collection_items", 0)) > 0


def _extract_collection_items_from_metadata(metadata: Mapping[str, Any]) -> list[dict[str, Any]]:
    containers: list[Mapping[str, Any]] = [metadata]
    for nested_key in ("metadata", "result", "payload"):
        nested = metadata.get(nested_key)
        if isinstance(nested, Mapping):
            containers.append(nested)
    keys = (
        "selected_recommendations",
        "recommendations",
        "selected_items",
        "items",
        "cards",
        "delivered_items",
    )
    items: list[dict[str, Any]] = []
    for container in containers:
        for key in keys:
            raw = container.get(key)
            if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes, bytearray)):
                continue
            for item in raw:
                if not isinstance(item, Mapping):
                    continue
                normalized = dict(item)
                if _collection_item_title(normalized) or _collection_item_source_ref(normalized):
                    items.append(normalized)
            if items:
                return items
    return items


def _collection_domain_for_episode(*, action: str, summary: Any, metadata: Mapping[str, Any]) -> str:
    explicit = _clean_text(
        metadata.get("domain")
        or metadata.get("recommendation_domain")
        or metadata.get("content_domain")
        or metadata.get("domain_event_kind")
    ).lower()
    text = " ".join([action, _clean_text(summary).lower(), _json_dumps(metadata) or ""]).lower()
    if "lightnovel" in explicit or "lightnovel" in text or "轻小说" in text or "lightnovel.fun" in text:
        return DOMAIN_LIGHTNOVEL_RECOMMENDATION
    if "recommendation" in explicit or "recommendation" in text or "推荐" in text:
        return DOMAIN_RECOMMENDATION
    return explicit or "generic_item"


def _owner_alias_person_ids(person_id: str | None) -> list[str]:
    person = _clean_text(person_id)
    aliases = ["", "default"]
    if person:
        aliases.append(person)
    if person in {"owner", "user", "main_user", "master"}:
        aliases.extend(["owner", "user", "main_user", "master"])
    return list(dict.fromkeys(aliases))


def _collection_summary_text(
    *,
    day_key: str,
    domain: str,
    items: Sequence[Mapping[str, Any]],
    scope_label: str,
) -> str:
    label = "轻小说推荐" if domain == DOMAIN_LIGHTNOVEL_RECOMMENDATION else domain.replace("_", " ")
    titles = [_collection_item_title(item) for item in list(items or [])[:6] if _collection_item_title(item)]
    title_text = "、".join(titles)
    suffix = f"：{title_text}" if title_text else ""
    return f"{day_key} {scope_label}{label}集合，记录 {len(list(items or []))} 个条目{suffix}。"


def _collection_summary_text_for_collection(
    *,
    collection: Mapping[str, Any],
    items: Sequence[Mapping[str, Any]],
) -> str:
    domain = _clean_text(collection.get("domain")) or DOMAIN_RECOMMENDATION
    scope_key = _clean_text(collection.get("scope_key")) or COLLECTION_SCOPE_RECENT
    label = "轻小说推荐" if domain == DOMAIN_LIGHTNOVEL_RECOMMENDATION else domain.replace("_", " ")
    if scope_key.startswith(f"{COLLECTION_SCOPE_DAILY}:"):
        day_key = scope_key.split(":", 1)[1]
        prefix = f"{day_key} 当天{label}集合"
    elif scope_key.startswith(f"{COLLECTION_SCOPE_DELIVERY}:"):
        prefix = f"本次{label}集合"
    elif scope_key.startswith(f"{COLLECTION_SCOPE_ROLLING_PREFIX}:"):
        prefix = f"{_rolling_scope_label(scope_key)}{label}集合"
    elif scope_key == COLLECTION_SCOPE_RECENT:
        prefix = f"最近{label}集合"
    else:
        prefix = f"{scope_key} {label}集合"
    item_list = list(items or [])
    titles: list[str] = []
    for item in item_list[:6]:
        title = _collection_item_title_for_summary(item)
        if title:
            titles.append(title)
    title_text = "、".join(titles)
    suffix = f"：{title_text}" if title_text else ""
    return f"{prefix}，记录 {len(item_list)} 个条目{suffix}。"


def _collection_item_title_for_summary(item: Mapping[str, Any]) -> str:
    return _clean_text(item.get("item_title"), limit=160) or _collection_item_title(item)


def _collection_item_key(*, episode_id: str, item: Mapping[str, Any], index: int) -> str:
    stable = _collection_item_source_ref(item) or _collection_item_title(item) or str(index)
    return _stable_hash(f"{episode_id}:{index}:{stable}", size=28)


def _collection_item_title(item: Mapping[str, Any]) -> str:
    for key in ("title", "title_display", "name", "label"):
        value = _clean_text(item.get(key), limit=160)
        if value:
            return value
    return ""


def _collection_item_canonical_title(item: Mapping[str, Any]) -> str:
    text = _clean_text(
        item.get("canonical_title")
        or item.get("series_title")
        or item.get("work_title")
        or item.get("book_title")
        or item.get("item_title")
        or _collection_item_title(item),
        limit=220,
    )
    if not text:
        return ""
    text = re.sub(r"^\d+[.)、]\s*", "", text)
    text = re.sub(r"^\d{3,8}[.．]\s*", "", text)
    text = re.sub(r"^(?:今天|今日|昨天|前天|三天前|四天前|五天前|本次|这次)\s*推荐\s*[A-ZＡ-Ｚ一二三四五六七八九十0-9]*\s*[:：]\s*", "", text)
    text = re.sub(r"^推荐\s*[A-ZＡ-Ｚ一二三四五六七八九十0-9]*\s*[:：]\s*", "", text)
    text = re.sub(r"\s*\(\s*\d{1,2}/\d{1,2}[^)]*\)\s*$", "", text)
    text = re.sub(r"\s*（\s*\d{1,2}/\d{1,2}[^）]*）\s*$", "", text)
    text = re.sub(r"\s+第\s*\d+\s*[话話章].*$", "", text)
    text = re.sub(r"\s+第\s*[一二三四五六七八九十百千]+\s*[话話章].*$", "", text)
    return _clean_text(text, limit=160)


def _collection_item_resolved_canonical_title(item: Mapping[str, Any]) -> str:
    payload = dict(item or {})
    stored = _clean_text(payload.get("canonical_title"), limit=220)
    payload.pop("canonical_title", None)
    payload.pop("canonical_key", None)
    reparsed = _collection_item_canonical_title(payload)
    return reparsed or stored


def _collection_item_canonical_key(value: Any) -> str:
    title = _clean_text(value, limit=220)
    if not title:
        return ""
    return _entity_normalized_key(title)


def _collection_item_summary(item: Mapping[str, Any]) -> str:
    parts: list[str] = []
    for label, keys in (
        ("看点", ("selection_reason", "rationale", "reason", "why", "look_point")),
        ("简介", ("intro_snippet", "intro", "description", "content_excerpt", "body_excerpt", "summary", "abstract")),
        ("题材", ("genre", "category", "topic", "theme", "tags")),
        ("更新", ("update_time", "updated_at", "freshness")),
    ):
        for key in keys:
            value = item.get(key)
            if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
                value = "、".join(str(part) for part in value if str(part).strip())
            text = _clean_text(value, limit=180)
            if text:
                parts.append(f"{label}：{text}")
                break
    source = _collection_item_source_ref(item)
    if source:
        parts.append(f"详情：{source}")
    return "｜".join(parts)


def _collection_item_review_evidence_metadata(
    item: Mapping[str, Any],
    *,
    review_episode_id: str,
    review_ref: str,
    fact: str | None,
    ts_ms: int,
) -> dict[str, Any]:
    status = _clean_text(item.get("review_status"), limit=40).upper()
    observations = _coerce_text_list(item.get("body_observations"))
    score_reasons = _coerce_text_list(item.get("score_reasons"))
    chapter_signal = _clean_text(item.get("chapter_signal"), limit=260)
    content_chars = _coerce_int(item.get("content_char_count"))
    body_excerpt = "；".join(observations[:2]) or chapter_signal
    has_body_evidence = bool(observations or chapter_signal or content_chars >= 240)
    deep_review = {
        "review_episode_id": _clean_text(review_episode_id),
        "review_ref": _clean_text(review_ref),
        "reviewed_ts_ms": int(ts_ms),
        "status": status,
        "detail_url": _clean_text(item.get("detail_url"), limit=300),
        "content_char_count": content_chars,
        "body_quality": _clean_text(item.get("body_quality"), limit=80),
        "body_observations": observations,
        "score": _clamp_unit(item.get("deep_review_score"), default=0.0),
        "score_reasons": score_reasons,
        "chapter_signal": chapter_signal,
    }
    payload: dict[str, Any] = {
        "deep_review": {key: value for key, value in deep_review.items() if value not in (None, "", [], {}, 0)},
        "deep_review_episode_id": _clean_text(review_episode_id),
        "detail_evidence_ref": _clean_text(review_ref),
        "body_ref": _clean_text(review_ref) if has_body_evidence else "",
        "detail_review_status": status,
        "detail_reviewed_ts_ms": int(ts_ms),
        "detail_url": _clean_text(item.get("detail_url"), limit=300),
        "content_char_count": content_chars if content_chars > 0 else 0,
        "body_quality": _clean_text(item.get("body_quality"), limit=80),
        "body_observations": observations,
        "content_excerpt": body_excerpt if has_body_evidence else "",
        "chapter_signal": chapter_signal if has_body_evidence else "",
        "deep_review_score": item.get("deep_review_score"),
        "score_reasons": score_reasons,
    }
    if has_body_evidence and _clean_text(fact):
        payload["deep_review_fact"] = _clean_text(fact, limit=520)
    return {key: value for key, value in payload.items() if value not in (None, "", [], {}, 0)}


def _collection_item_deep_review_summary(item: Mapping[str, Any]) -> str:
    deep = item.get("deep_review")
    if not isinstance(deep, Mapping):
        deep = {}
    observations = _coerce_text_list(item.get("body_observations") or deep.get("body_observations"))
    score_reasons = _coerce_text_list(item.get("score_reasons") or deep.get("score_reasons"))
    chapter_signal = _clean_text(item.get("chapter_signal") or deep.get("chapter_signal"), limit=180)
    bits = [*observations[:2], *score_reasons[:2]]
    if chapter_signal:
        bits.append(chapter_signal)
    return "；".join(_unique_clean_texts(bits)[:4])[:360]


def _entity_mention_has_deep_review(item: Mapping[str, Any]) -> bool:
    metadata = item.get("metadata") if isinstance(item.get("metadata"), Mapping) else {}
    item_metadata = item.get("item_metadata") if isinstance(item.get("item_metadata"), Mapping) else {}
    return bool(
        _clean_text(metadata.get("deep_review_episode_id") or item_metadata.get("deep_review_episode_id"))
        or _clean_text(metadata.get("detail_evidence_ref") or item_metadata.get("detail_evidence_ref"))
        or _collection_item_deep_review_summary({**dict(item_metadata), **dict(metadata)})
    )


def _semantic_entity_domain_label(domain: str) -> str:
    value = _clean_text(domain)
    if value == DOMAIN_LIGHTNOVEL_RECOMMENDATION:
        return "轻小说推荐"
    if value == DOMAIN_RECOMMENDATION:
        return "推荐"
    return value.replace("_", " ") if value else "经历"


def _semantic_entity_owner_id(mentions: Sequence[Mapping[str, Any]]) -> str:
    owners = _unique_clean_texts(
        [
            item.get("episode_person_id")
            for item in mentions
            if _clean_text(item.get("episode_person_id"))
        ]
    )
    return owners[0] if len(owners) == 1 else "global"


def _build_entity_semantic_summary(
    *,
    title: str,
    domain_label: str,
    mentions: Sequence[Mapping[str, Any]],
    deep_mentions: Sequence[Mapping[str, Any]],
) -> str:
    mention_count = len(list(mentions or []))
    collection_days = _unique_clean_texts(
        [
            _collection_day_key(_coerce_int(item.get("episode_created_ts_ms") or item.get("created_ts_ms")))
            for item in mentions
            if _coerce_int(item.get("episode_created_ts_ms") or item.get("created_ts_ms")) > 0
        ]
    )
    scope_count = len(_unique_clean_texts([item.get("collection_id") for item in mentions if item.get("collection_id")]))
    summary_bits = [f"实体经历：{domain_label}里《{title}》出现过 {mention_count} 次"]
    if collection_days:
        summary_bits.append(f"时间线：{', '.join(collection_days[:4])}")
    if scope_count:
        summary_bits.append(f"关联过 {scope_count} 个集合")
    if deep_mentions:
        deep_summary = _clean_text(
            _collection_item_deep_review_summary(
                {
                    **dict((deep_mentions[0].get("item_metadata") if isinstance(deep_mentions[0].get("item_metadata"), Mapping) else {}) or {}),
                    **dict((deep_mentions[0].get("metadata") if isinstance(deep_mentions[0].get("metadata"), Mapping) else {}) or {}),
                }
            ),
            limit=260,
        )
        if deep_summary:
            summary_bits.append(f"已有深读证据：{deep_summary}")
        else:
            summary_bits.append("已有深读证据")
    recent = next((item for item in mentions if _clean_text(item.get("item_summary"))), None)
    if recent is not None:
        summary_bits.append(f"最近线索：{_clean_text(recent.get('item_summary'), limit=220)}")
    return "；".join(summary_bits) + "。"


def _collection_item_source_ref(item: Mapping[str, Any]) -> str:
    for key in ("detail_url", "url", "source_url", "href"):
        value = _clean_text(item.get(key), limit=300)
        if value:
            return value
    return ""


def _collection_item_recall_line(item: Mapping[str, Any], index: int) -> str:
    title = _clean_text(item.get("item_title") or _collection_item_title(item), limit=90)
    if not title:
        title = f"条目{index}"
    summary = _clean_text(item.get("item_summary") or _collection_item_summary(item), limit=260)
    deep_summary = _clean_text(_collection_item_deep_review_summary(item), limit=260)
    source = _clean_text(item.get("source_ref") or _collection_item_source_ref(item), limit=260)
    parts = [f"{index}. {title}"]
    if summary:
        parts.append(summary)
    if deep_summary and deep_summary not in summary:
        parts.append(f"深读：{deep_summary}")
    if source and source not in summary:
        parts.append(f"详情：{source}")
    return "｜".join(parts)


def _entity_mention_recall_line(item: Mapping[str, Any]) -> str:
    title = _clean_text(item.get("item_title") or (item.get("item_metadata") or {}).get("title"), limit=120)
    summary = _clean_text(item.get("item_summary") or (item.get("item_metadata") or {}).get("item_summary"), limit=220)
    deep_summary = _clean_text(
        _collection_item_deep_review_summary({**dict(item.get("item_metadata") or {}), **dict(item.get("metadata") or {})}),
        limit=260,
    )
    scope_key = _clean_text(item.get("scope_key"))
    source = _clean_text(item.get("source_ref") or item.get("item_source_ref"), limit=240)
    when = ""
    ts_ms = _coerce_int(item.get("episode_created_ts_ms") or item.get("created_ts_ms"))
    if ts_ms > 0:
        when = _collection_day_key(ts_ms)
    parts = [part for part in (when, scope_key, title, summary) if part]
    if deep_summary and deep_summary not in summary:
        parts.append(f"深读：{deep_summary}")
    if source:
        parts.append(f"来源：{source}")
    return "｜".join(parts)


def _entity_mention_matches_daily_scope(item: Mapping[str, Any], *, target_days: set[str]) -> bool:
    if not target_days:
        return True
    scope_key = _clean_text(item.get("scope_key"))
    if scope_key.startswith(f"{COLLECTION_SCOPE_DAILY}:"):
        return scope_key.split(":", 1)[1] in target_days
    ts_ms = _coerce_int(item.get("episode_created_ts_ms") or item.get("created_ts_ms"))
    if ts_ms <= 0:
        return False
    return _collection_day_key(ts_ms) in target_days


def _collection_scope_boost(collection: Mapping[str, Any], *, plan: Any, now_ts_ms_value: int) -> float:
    scope_key = _clean_text(collection.get("scope_key"))
    temporal_scope = str(getattr(plan, "temporal_scope", ""))
    target_scope_keys = set(_scope_keys_for_collection_plan(plan=plan, now_ts_ms_value=now_ts_ms_value))
    target_has_rolling = any(key.startswith(f"{COLLECTION_SCOPE_ROLLING_PREFIX}:") for key in target_scope_keys)
    boost = 0.0
    if target_scope_keys and scope_key in target_scope_keys:
        boost += 0.24
    if scope_key.startswith(f"{COLLECTION_SCOPE_ROLLING_PREFIX}:") and scope_key in target_scope_keys:
        boost += 0.06
    if scope_key.startswith(f"{COLLECTION_SCOPE_DELIVERY}:") and temporal_scope == TEMPORAL_CURRENT:
        boost += 0.28
    if scope_key == COLLECTION_SCOPE_RECENT and temporal_scope in {TEMPORAL_CURRENT, TEMPORAL_RECENT_DAYS} and not target_has_rolling:
        boost += 0.18
    if int(collection.get("item_count") or 0) > 0:
        boost += 0.08
    return min(0.38, boost)


def _semantic_source_memory_ids(proposal: Mapping[str, Any]) -> list[str]:
    ids: list[str] = []
    for item in list(proposal.get("source_episode_ids") or []):
        eid = _clean_text(item)
        if eid:
            ids.append(f"life_episode::{eid}")
    for item in list(proposal.get("source_collection_ids") or []):
        cid = _clean_text(item)
        if cid:
            ids.append(f"life_memory_collection::{cid}")
    for item in list(proposal.get("source_entity_ids") or []):
        entity_id = _clean_text(item)
        if entity_id:
            ids.append(f"life_memory_entity::{entity_id}")
    for item in list(proposal.get("source_correction_ids") or []):
        correction_id = _clean_text(item)
        if correction_id:
            ids.append(f"life_memory_correction::{correction_id}")
    return list(dict.fromkeys(ids))


def _semantic_taxonomy_tags(kind: str) -> list[str]:
    value = _clean_text(kind)
    base = [f"semantic_kind:{value or 'unknown'}"]
    if value == "user_preference":
        return [*base, "memory_family:user_model", "semantic_tier:durable_profile", "recall_axis:preference"]
    if value == "commitment":
        return [*base, "memory_family:open_loop", "semantic_tier:active_commitment", "recall_axis:promise"]
    if value == "self_change":
        return [*base, "memory_family:self_model", "semantic_tier:identity_continuity", "recall_axis:self_change"]
    if value == "failure_lesson":
        return [*base, "memory_family:operations", "semantic_tier:durable_lesson", "recall_axis:debugging"]
    if value == "stable_fact":
        return [*base, "memory_family:world_or_user_fact", "semantic_tier:durable_fact", "recall_axis:fact"]
    if value == "correction":
        return [*base, "memory_family:error_correction", "semantic_tier:durable_guardrail", "recall_axis:correction"]
    if value == "relationship_feedback":
        return [*base, "memory_family:relationship_model", "semantic_tier:durable_feedback", "recall_axis:interaction_quality"]
    if value == "experience_collection":
        return [*base, "memory_family:experience_index", "semantic_tier:episodic_summary", "recall_axis:what_happened"]
    if value == "experience_entity":
        return [*base, "memory_family:experience_index", "semantic_tier:entity_summary", "recall_axis:what_object"]
    if value == "action_pattern":
        return [*base, "memory_family:action_model", "semantic_tier:durable_pattern", "recall_axis:what_she_does"]
    return base


def _semantic_kind_priority(kind: str) -> int:
    value = _clean_text(kind)
    priorities = {
        "user_preference": 0,
        "commitment": 1,
        "correction": 2,
        "relationship_feedback": 3,
        "self_change": 4,
        "stable_fact": 5,
        "failure_lesson": 6,
        "action_pattern": 7,
        "experience_collection": 8,
    }
    return priorities.get(value, 6)


_SEMANTIC_QUERY_STOP_TERMS = {
    "用户",
    "记忆",
    "系统",
    "偏好",
    "推荐",
    "昨天",
    "今天",
    "前天",
    "刚才",
    "这个",
    "那个",
    "什么",
    "怎么",
    "哪些",
}


def _semantic_proposal_matches_query(proposal: Mapping[str, Any], *, query_text: Any) -> bool:
    query = _clean_text(query_text, limit=220)
    if not query:
        return True
    haystack = _semantic_proposal_query_haystack(proposal)
    compact_query = re.sub(r"\s+", "", query).lower()
    if not compact_query:
        return True
    if compact_query in haystack:
        return True

    terms = _semantic_query_terms(query)
    if terms:
        matched = [term for term in terms if term in haystack]
        if matched and len(terms) <= 2:
            return True
        if len(matched) >= 2:
            return True
        if any(len(term) >= 4 for term in matched):
            return True

    bigrams = _semantic_query_bigrams(compact_query)
    if not bigrams:
        return False
    matched_bigrams = [item for item in bigrams if item in haystack]
    threshold = max(2, min(4, len(bigrams) // 3))
    return len(matched_bigrams) >= threshold


def _semantic_proposal_query_haystack(proposal: Mapping[str, Any]) -> str:
    values: list[str] = [
        _clean_text(proposal.get("semantic_key"), limit=260),
        _clean_text(proposal.get("semantic_kind"), limit=80),
        _clean_text(proposal.get("summary"), limit=900),
    ]
    values.extend(_clean_text(item, limit=120) for item in list(proposal.get("tags") or []))
    values.extend(_clean_text(item, limit=180) for item in list(proposal.get("source_refs") or []))
    return re.sub(r"\s+", "", " ".join(value for value in values if value)).lower()


def _semantic_query_terms(query: str) -> list[str]:
    raw_terms = re.split(r"[\s,，。；;、|｜/\\:：?？!！()（）\[\]【】<>《》\"']+", query)
    terms: list[str] = []
    for raw in raw_terms:
        term = re.sub(r"\s+", "", raw).lower()
        if len(term) < 2 or term in _SEMANTIC_QUERY_STOP_TERMS:
            continue
        if term not in terms:
            terms.append(term)
    return terms[:12]


def _semantic_query_bigrams(compact_query: str) -> list[str]:
    chunks: list[str] = []
    for index in range(0, max(0, len(compact_query) - 1)):
        chunk = compact_query[index : index + 2]
        if len(chunk) < 2 or chunk in _SEMANTIC_QUERY_STOP_TERMS:
            continue
        if chunk not in chunks:
            chunks.append(chunk)
    return chunks[:32]


def _semantic_memory_source_ids(memory: Mapping[str, Any]) -> list[str]:
    raw = memory.get("source_memory_ids")
    if isinstance(raw, str):
        parsed = _json_loads(raw, fallback=[])
    else:
        parsed = raw
    if not isinstance(parsed, Sequence) or isinstance(parsed, (str, bytes, bytearray)):
        return []
    return [_clean_text(item) for item in parsed if _clean_text(item)]


def _looks_like_synthetic_collection_text(value: Any) -> bool:
    text = _clean_text(value, limit=2000)
    if not text:
        return False
    if _has_any_marker(text, _SYNTHETIC_SOURCE_MARKERS):
        return True
    return bool(re.search(r"(?:今天|昨天|前天|三天前)推荐\s*[A-CＡ-Ｃ]?\s*[:：]", text))


def _collection_item_is_synthetic(item: Mapping[str, Any]) -> bool:
    metadata = _json_loads(item.get("metadata"), fallback={})
    if not isinstance(metadata, Mapping):
        metadata = {}
    if _is_synthetic_life_record(item, metadata):
        return True
    values = (
        item.get("item_title"),
        item.get("item_summary"),
        item.get("source_ref"),
        item.get("evidence_ref"),
        metadata.get("title"),
        metadata.get("detail_url"),
    )
    return any(_looks_like_synthetic_collection_text(value) for value in values)


def _collection_item_has_recallable_anchor(item: Mapping[str, Any]) -> bool:
    if _collection_item_is_synthetic(item):
        return False
    metadata = _json_loads(item.get("metadata"), fallback={})
    if not isinstance(metadata, Mapping):
        metadata = {}
    refs = (
        item.get("evidence_ref"),
        item.get("source_ref"),
        metadata.get("evidence_ref"),
        metadata.get("source_ref"),
        metadata.get("detail_url"),
    )
    return any(_clean_text(ref) and not _has_any_marker(ref, _SYNTHETIC_SOURCE_MARKERS) for ref in refs)


def _collection_item_episode_anchor_is_synthetic(store: LifeMemoryStore, item: Mapping[str, Any]) -> bool:
    metadata = _json_loads(item.get("metadata"), fallback={})
    if not isinstance(metadata, Mapping):
        metadata = {}
    episode_id = _clean_text(item.get("episode_id") or metadata.get("source_episode_id"))
    if not episode_id:
        return False
    row = store.ledger._conn.execute(
        "SELECT * FROM life_episode WHERE episode_id = ? LIMIT 1",
        (episode_id,),
    ).fetchone()
    return bool(row is not None and _is_synthetic_life_record(dict(row)))


def _collection_is_synthetic_for_semantic(store: LifeMemoryStore, collection: Mapping[str, Any]) -> bool:
    metadata = _json_loads(collection.get("metadata"), fallback={})
    if not isinstance(metadata, Mapping):
        metadata = {}
    if _is_synthetic_life_record(collection, metadata):
        return True
    values = (
        collection.get("title"),
        collection.get("summary"),
        metadata.get("source_ref"),
        metadata.get("source_episode_id"),
    )
    if any(_looks_like_synthetic_collection_text(value) for value in values):
        return True
    collection_id = _clean_text(collection.get("collection_id"))
    if not collection_id:
        return False
    for item in store._list_collection_items(collection_id=collection_id, limit=96):
        if _collection_item_is_synthetic(item):
            return True
    return False


def _semantic_memory_should_prune_as_synthetic(store: LifeMemoryStore, memory: Mapping[str, Any]) -> bool:
    if _looks_like_synthetic_collection_text(memory.get("summary")):
        return True
    if _has_any_marker(memory.get("operator_node_id"), _SYNTHETIC_SOURCE_MARKERS):
        return True
    for source_id in _semantic_memory_source_ids(memory):
        if _has_any_marker(source_id, _SYNTHETIC_SOURCE_MARKERS):
            return True
        if source_id.startswith("life_episode::"):
            episode_id = source_id.split("::", 1)[1]
            row = store.ledger._conn.execute("SELECT * FROM life_episode WHERE episode_id = ? LIMIT 1", (episode_id,)).fetchone()
            if row is not None and _is_synthetic_life_record(dict(row)):
                return True
        elif source_id.startswith("life_memory_collection::"):
            collection_id = source_id.split("::", 1)[1]
            row = store.ledger._conn.execute(
                "SELECT * FROM life_memory_collection WHERE collection_id = ? LIMIT 1",
                (collection_id,),
            ).fetchone()
            if row is not None and _collection_is_synthetic_for_semantic(store, dict(row)):
                return True
    return False


def _semantic_memory_prune_reason(
    store: LifeMemoryStore,
    memory: Mapping[str, Any],
    *,
    stale_collection_semantic_ids: set[str],
) -> str:
    semantic_id = _clean_text(memory.get("semantic_id"))
    if semantic_id and semantic_id in stale_collection_semantic_ids:
        return "SEMANTIC_COLLECTION_STALE_VERSION"
    if _semantic_memory_should_prune_as_synthetic(store, memory):
        return "SEMANTIC_SYNTHETIC_SOURCE"
    if _semantic_memory_should_prune_as_unscoped_action_pattern(store, memory):
        return "SEMANTIC_ACTION_PATTERN_UNSCOPED_SOURCE"
    collection_ids = _semantic_memory_collection_ids(memory)
    if not collection_ids:
        return ""
    if _clean_text(memory.get("distill_strategy")) != "life_memory_collection_rule_consolidation":
        return ""
    for collection_id in collection_ids:
        row = store.ledger._conn.execute(
            "SELECT * FROM life_memory_collection WHERE collection_id = ? LIMIT 1",
            (collection_id,),
        ).fetchone()
        if row is None:
            continue
        collection = dict(row)
        scope_key = _clean_text(collection.get("scope_key"))
        if scope_key.startswith(f"{COLLECTION_SCOPE_ROLLING_PREFIX}:"):
            return "SEMANTIC_COLLECTION_ROLLING_WINDOW_INDEX"
        if scope_key == COLLECTION_SCOPE_RECENT:
            source_episode_ids = _collection_source_episode_ids(store, collection_id=collection_id)
            if len(source_episode_ids) <= 1:
                return "SEMANTIC_COLLECTION_SINGLE_DELIVERY_WINDOW"
    return ""


def _semantic_memory_should_prune_as_unscoped_action_pattern(store: LifeMemoryStore, memory: Mapping[str, Any]) -> bool:
    if _clean_text(memory.get("distill_strategy")) != "life_action_pattern_rule_consolidation":
        return False
    episode_source_ids = [
        source_id.split("::", 1)[1]
        for source_id in _semantic_memory_source_ids(memory)
        if source_id.startswith("life_episode::") and _clean_text(source_id.split("::", 1)[1])
    ]
    if not episode_source_ids:
        return True
    placeholders = ", ".join("?" for _ in episode_source_ids)
    rows = store.ledger._conn.execute(
        f"""
        SELECT episode_id, person_id, session_id, action, metadata
        FROM life_episode
        WHERE episode_id IN ({placeholders})
        """,
        tuple(episode_source_ids),
    ).fetchall()
    by_id = {str(row["episode_id"]): dict(row) for row in rows}
    for episode_id in episode_source_ids:
        row = by_id.get(episode_id)
        if row is None:
            return True
        metadata = _episode_metadata_dict(row)
        if _is_synthetic_life_record(row, metadata):
            return True
        if _clean_text(row.get("action")).lower() != "lightnovel_recommendation_deep_review":
            return True
        if not _action_pattern_scope_key(row, metadata=metadata):
            return True
    return False


def _semantic_memory_collection_ids(memory: Mapping[str, Any]) -> list[str]:
    ids: list[str] = []
    for source_id in _semantic_memory_source_ids(memory):
        if source_id.startswith("life_memory_collection::"):
            collection_id = _clean_text(source_id.split("::", 1)[1])
            if collection_id:
                ids.append(collection_id)
    return list(dict.fromkeys(ids))


def _stale_collection_semantic_ids(store: LifeMemoryStore, memories: Sequence[Mapping[str, Any]]) -> set[str]:
    by_collection: dict[str, list[Mapping[str, Any]]] = {}
    for memory in list(memories or []):
        if _clean_text(memory.get("distill_strategy")) != "life_memory_collection_rule_consolidation":
            continue
        identity_keys = _semantic_memory_collection_identity_keys(store, memory)
        for identity_key in identity_keys:
            by_collection.setdefault(identity_key, []).append(memory)
    stale: set[str] = set()
    for items in by_collection.values():
        unique = {
            _clean_text(item.get("semantic_id")): item
            for item in items
            if _clean_text(item.get("semantic_id"))
        }
        if len(unique) <= 1:
            continue
        ordered = sorted(
            unique.values(),
            key=lambda item: (
                _coerce_int(item.get("ts_ms")),
                _clamp_unit(item.get("confidence")),
                len(_clean_text(item.get("summary"), limit=2000)),
            ),
            reverse=True,
        )
        for item in ordered[1:]:
            semantic_id = _clean_text(item.get("semantic_id"))
            if semantic_id:
                stale.add(semantic_id)
    return stale


def _semantic_memory_collection_identity_keys(store: LifeMemoryStore, memory: Mapping[str, Any]) -> list[str]:
    collection_ids = _semantic_memory_collection_ids(memory)
    if not collection_ids:
        return []
    placeholders = ", ".join("?" for _ in collection_ids)
    rows = store.ledger._conn.execute(
        f"""
        SELECT collection_id, collection_kind, domain, scope_key, person_id, session_id
        FROM life_memory_collection
        WHERE collection_id IN ({placeholders})
        """,
        tuple(collection_ids),
    ).fetchall()
    by_id = {str(row["collection_id"]): dict(row) for row in rows}
    keys: list[str] = []
    for collection_id in collection_ids:
        row = by_id.get(collection_id)
        if row is None:
            keys.append(f"collection_id:{collection_id}")
            continue
        owner = _clean_text(row.get("person_id")) or "global"
        keys.append(
            "collection_scope:"
            f"{_clean_text(row.get('collection_kind'))}:"
            f"{_clean_text(row.get('domain'))}:"
            f"{_clean_text(row.get('scope_key'))}:"
            f"{owner}"
        )
    return list(dict.fromkeys(keys))


def _count_reason_codes(items: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in list(items or []):
        reason = _clean_text(item.get("reason_code")) or "UNKNOWN"
        counts[reason] = counts.get(reason, 0) + 1
    return counts


def _merge_semantic_cleanup_reports(*reports: Mapping[str, Any]) -> dict[str, Any]:
    merged_ids: list[str] = []
    merged_samples: list[dict[str, Any]] = []
    reason_counts: dict[str, int] = {}
    scanned_count = 0
    dry_run = False
    passes: list[dict[str, Any]] = []
    for report in reports:
        item = dict(report or {})
        if not item:
            continue
        scanned_count += max(0, _coerce_int(item.get("scanned_count")))
        dry_run = dry_run or bool(item.get("dry_run"))
        for semantic_id in list(item.get("semantic_ids") or []):
            cleaned = _clean_text(semantic_id)
            if cleaned and cleaned not in merged_ids:
                merged_ids.append(cleaned)
        for reason, count in dict(item.get("reason_counts") or {}).items():
            key = _clean_text(reason) or "UNKNOWN"
            reason_counts[key] = reason_counts.get(key, 0) + max(0, _coerce_int(count))
        for sample in list(item.get("samples") or []):
            if isinstance(sample, Mapping):
                merged_samples.append(dict(sample))
        passes.append(
            {
                "scanned_count": max(0, _coerce_int(item.get("scanned_count"))),
                "pruned_count": max(0, _coerce_int(item.get("pruned_count"))),
                "reason_counts": dict(item.get("reason_counts") or {}),
            }
        )
    return {
        "status": "OK",
        "dry_run": dry_run,
        "scanned_count": scanned_count,
        "pruned_count": len(merged_ids),
        "semantic_ids": merged_ids,
        "reason_counts": dict(sorted(reason_counts.items())),
        "samples": merged_samples[:8],
        "passes": passes,
    }


def _collection_source_episode_ids(store: LifeMemoryStore, *, collection_id: str) -> list[str]:
    rows = store._list_collection_items(collection_id=collection_id, limit=256)
    ids = [_clean_text(item.get("episode_id")) for item in rows if _clean_text(item.get("episode_id"))]
    return list(dict.fromkeys(ids))


def _semantic_referenced_episode_ids(conn: Any) -> set[str]:
    try:
        rows = conn.execute(
            "SELECT source_memory_ids FROM semantic_memory WHERE deleted_at IS NULL"
        ).fetchall()
    except Exception:
        return set()
    episode_ids: set[str] = set()
    for row in rows:
        raw = row["source_memory_ids"] if isinstance(row, Mapping) else row[0]
        source_ids = _json_loads(raw, fallback=[])
        if not isinstance(source_ids, Sequence) or isinstance(source_ids, (str, bytes, bytearray)):
            continue
        for source_id in source_ids:
            value = _clean_text(source_id)
            if value.startswith("life_episode::"):
                episode_id = value.split("::", 1)[1]
                if episode_id:
                    episode_ids.add(episode_id)
    return episode_ids


def _trusted_active_episode_ids(
    store: LifeMemoryStore,
    episode_ids: Sequence[Any],
    *,
    allow_synthetic: bool = False,
) -> set[str]:
    ids = list(dict.fromkeys(_clean_text(item) for item in list(episode_ids or []) if _clean_text(item)))
    if not ids:
        return set()
    placeholders = ", ".join("?" for _ in ids)
    rows = store.ledger._conn.execute(
        f"""
        SELECT episode_id, person_id, session_id, thread_id, primary_ref, metadata
        FROM life_episode
        WHERE episode_id IN ({placeholders})
          AND truth_state = ?
          AND status = ?
          AND COALESCE(retention_state, ?) != ?
        """,
        (*ids, TRUTH_VERIFIED, STATUS_ACTIVE, RETENTION_COLD, RETENTION_GONE),
    ).fetchall()
    trusted: set[str] = set()
    for row in rows:
        record = dict(row)
        if _is_synthetic_life_record(record) and not allow_synthetic:
            continue
        episode_id = _clean_text(record.get("episode_id"))
        if episode_id:
            trusted.add(episode_id)
    return trusted


def _semantic_proposal_from_collection(store: LifeMemoryStore, collection: Mapping[str, Any]) -> dict[str, Any] | None:
    collection_id = _clean_text(collection.get("collection_id"))
    if not collection_id:
        return None
    metadata = _json_loads(collection.get("metadata"), fallback={})
    if not isinstance(metadata, dict):
        metadata = {}
    if _is_synthetic_life_record(collection, metadata):
        return None
    domain = _clean_text(collection.get("domain"))
    collection_kind = _clean_text(collection.get("collection_kind"))
    scope_key = _clean_text(collection.get("scope_key"))
    item_count = int(collection.get("item_count") or 0)
    if scope_key.startswith(f"{COLLECTION_SCOPE_DELIVERY}:") or metadata.get("semantic_consolidation") is False:
        return None
    if item_count <= 0:
        return None
    if collection_kind != COLLECTION_RECOMMENDATION and "recommendation" not in collection_kind:
        return None
    items = store._list_collection_items(collection_id=collection_id, limit=32)
    if not items:
        return None
    items = [item for item in items if not _collection_item_is_synthetic(item)]
    if not items:
        return None
    source_episode_id_order = [
        _clean_text(item.get("episode_id"))
        for item in items
        if _clean_text(item.get("episode_id"))
    ]
    trusted_episode_ids = _trusted_active_episode_ids(store, source_episode_id_order)
    if source_episode_id_order:
        trusted_items = [item for item in items if _clean_text(item.get("episode_id")) in trusted_episode_ids]
        items = trusted_items or [
            item
            for item in items
            if _collection_item_has_recallable_anchor(item)
            and _collection_item_episode_anchor_is_synthetic(store, item)
        ]
    if not items:
        return None
    item_count = len(items)
    source_episode_ids = [
        _clean_text(item.get("episode_id"))
        for item in items
        if _clean_text(item.get("episode_id"))
    ]
    distinct_source_episode_ids = list(dict.fromkeys(source_episode_ids))
    if not _collection_scope_should_distill_to_semantic(
        scope_key=scope_key,
        source_episode_count=len(distinct_source_episode_ids),
    ):
        return None
    title_records = _semantic_collection_title_records(items)
    titles = [item["title"] for item in title_records]
    if not titles:
        return None
    source_refs = [
        _clean_text(item.get("evidence_ref") or item.get("source_ref"))
        for item in items
        if _clean_text(item.get("evidence_ref") or item.get("source_ref"))
    ]
    person_id = _clean_text(collection.get("person_id")) or "global"
    domain_label = "轻小说推荐" if domain == DOMAIN_LIGHTNOVEL_RECOMMENDATION else (domain.replace("_", " ") or "推荐")
    scope_label = _semantic_collection_scope_label(scope_key)
    facets = _semantic_collection_facets(items)
    summary = _build_collection_semantic_summary(
        scope_label=scope_label,
        domain_label=domain_label,
        titles=titles,
        facets=facets,
        source_episode_count=len(set(source_episode_ids)),
        pushed_count=item_count,
    )
    semantic_key = f"experience_collection|{person_id}|{collection_kind}|{domain}|{scope_key}"
    confidence = _semantic_collection_confidence(collection=collection, item_count=item_count)
    tags = [
        "experience_collection",
        "recommendation_collection",
        f"domain:{domain or 'generic'}",
        f"collection_scope:{_semantic_collection_scope_kind(scope_key)}",
        "memory_family:experience_index",
        "semantic_tier:episodic_summary",
        "recall_axis:what_happened",
    ]
    tags.extend(_semantic_collection_facet_tags(facets))
    if domain == DOMAIN_LIGHTNOVEL_RECOMMENDATION:
        tags.append("lightnovel_recommendation")
    return {
        "semantic_key": semantic_key,
        "semantic_kind": "experience_collection",
        "summary": summary,
        "confidence": confidence,
        "tags": tags,
        "source_episode_ids": distinct_source_episode_ids,
        "source_collection_ids": [collection_id],
        "source_refs": list(dict.fromkeys([f"life_memory_collection://{collection_id}", *source_refs])),
        "distill_strategy": "life_memory_collection_rule_consolidation",
        "collection_scope_label": scope_label,
        "collection_domain_label": domain_label,
        "collection_item_titles": titles,
        "collection_pushed_count": item_count,
        "collection_unique_count": len(titles),
        "collection_facets": facets,
    }


def _semantic_proposal_from_entity(store: LifeMemoryStore, entity: Mapping[str, Any]) -> dict[str, Any] | None:
    entity_id = _clean_text(entity.get("entity_id"))
    if not entity_id:
        return None
    metadata = _json_loads(entity.get("metadata"), fallback={})
    if not isinstance(metadata, dict):
        metadata = {}
    if _is_synthetic_life_record(entity, metadata):
        return None
    mentions = store._list_entity_mentions(entity_id=entity_id, limit=32)
    if not mentions:
        return None
    trusted_ids = _trusted_active_episode_ids(
        store,
        [_clean_text(item.get("episode_id")) for item in mentions if _clean_text(item.get("episode_id"))],
    )
    mentions = [
        item
        for item in mentions
        if not _clean_text(item.get("episode_id")) or _clean_text(item.get("episode_id")) in trusted_ids
    ]
    if not mentions:
        return None
    deep_mentions = [item for item in mentions if _entity_mention_has_deep_review(item)]
    if len(mentions) < 2 and not deep_mentions:
        return None
    title = _clean_text(entity.get("canonical_name"), limit=220)
    if not title:
        return None
    domain = _clean_text(entity.get("domain"))
    owner_id = _semantic_entity_owner_id(mentions)
    source_episode_ids = _unique_clean_texts([item.get("episode_id") for item in mentions if item.get("episode_id")])
    source_collection_ids = _unique_clean_texts([item.get("collection_id") for item in mentions if item.get("collection_id")])
    source_refs = _unique_clean_texts(
        [
            f"life_memory_entity://{entity_id}",
            *[
                item.get("evidence_ref") or item.get("source_ref") or item.get("item_source_ref")
                for item in mentions
                if item.get("evidence_ref") or item.get("source_ref") or item.get("item_source_ref")
            ],
        ]
    )
    if not source_episode_ids and not source_collection_ids:
        return None
    summary = _build_entity_semantic_summary(
        title=title,
        domain_label=_semantic_entity_domain_label(domain),
        mentions=mentions,
        deep_mentions=deep_mentions,
    )
    if not summary:
        return None
    confidence = _clamp_unit(
        0.58
        + min(0.16, len(mentions) * 0.025)
        + (0.10 if deep_mentions else 0.0)
        + min(0.06, len(source_collection_ids) * 0.02)
    )
    tags = [
        "experience_entity",
        f"domain:{domain or 'generic'}",
        "memory_family:experience_index",
        "semantic_tier:entity_summary",
        "recall_axis:what_object",
    ]
    if domain == DOMAIN_LIGHTNOVEL_RECOMMENDATION:
        tags.extend(["lightnovel_recommendation", "entity_kind:book"])
    if deep_mentions:
        tags.extend(["has_deep_review", "evidence_review"])
    return {
        "semantic_key": f"experience_entity|{owner_id}|{domain}|{entity_id}",
        "semantic_kind": "experience_entity",
        "summary": summary,
        "confidence": confidence,
        "tags": list(dict.fromkeys(tags)),
        "source_episode_ids": source_episode_ids,
        "source_collection_ids": source_collection_ids,
        "source_entity_ids": [entity_id],
        "source_refs": source_refs,
        "distill_strategy": "life_memory_entity_rule_consolidation",
        "entity_id": entity_id,
        "entity_title": title,
        "entity_mention_count": len(mentions),
        "entity_deep_review_count": len(deep_mentions),
    }


def _semantic_action_pattern_proposals(store: LifeMemoryStore, *, limit: int = 80) -> list[dict[str, Any]]:
    rows = store.ledger._conn.execute(
        """
        SELECT *
        FROM life_episode
        WHERE truth_state = ?
          AND status = ?
          AND COALESCE(retention_state, ?) != ?
          AND action = ?
        ORDER BY last_touched_ts_ms DESC, memory_strength DESC, salience DESC
        LIMIT ?
        """,
        (
            TRUTH_VERIFIED,
            STATUS_ACTIVE,
            RETENTION_WARM,
            RETENTION_GONE,
            "lightnovel_recommendation_deep_review",
            max(1, int(limit)),
        ),
    ).fetchall()
    groups: dict[str, dict[str, Any]] = {}
    for row in rows:
        episode = dict(row)
        metadata = _episode_metadata_dict(episode)
        if _is_synthetic_life_record(episode, metadata):
            continue
        result = _clean_text(episode.get("result") or metadata.get("status")).upper()
        if result not in {"OK", "SUCCESS", "COMPLETED", "DEGRADED"}:
            continue
        if not _action_pattern_deep_review_is_meaningful(episode, metadata=metadata, result=result):
            continue
        scope_key = _action_pattern_scope_key(episode, metadata=metadata)
        if not scope_key:
            continue
        group = groups.setdefault(
            scope_key,
            {
                "scope_key": scope_key,
                "count": 0,
                "ok_count": 0,
                "degraded_count": 0,
                "readable_body_count": 0,
                "source_episode_ids": [],
                "source_collection_ids": [],
                "source_refs": [],
                "selected_titles": [],
                "reason_facets": [],
            },
        )
        episode_id = _clean_text(episode.get("episode_id"))
        if not episode_id:
            continue
        group["count"] += 1
        if result == "DEGRADED":
            group["degraded_count"] += 1
        else:
            group["ok_count"] += 1
        if _action_pattern_has_readable_body(metadata):
            group["readable_body_count"] += 1
        group["source_episode_ids"].append(episode_id)
        for source_ref in _action_pattern_source_refs(episode, metadata=metadata):
            if source_ref not in group["source_refs"]:
                group["source_refs"].append(source_ref)
        for collection_id in _coerce_text_list(metadata.get("source_collection_ids")):
            if collection_id not in group["source_collection_ids"]:
                group["source_collection_ids"].append(collection_id)
        title = _action_pattern_selected_title(metadata)
        if title and title not in group["selected_titles"]:
            group["selected_titles"].append(title)
        for facet in _action_pattern_reason_facets(metadata):
            if facet not in group["reason_facets"]:
                group["reason_facets"].append(facet)

    proposals: list[dict[str, Any]] = []
    for group in groups.values():
        if int(group.get("count") or 0) < 2:
            continue
        summary = _build_action_pattern_semantic_summary(group)
        if not summary:
            continue
        count = int(group.get("count") or 0)
        ok_count = int(group.get("ok_count") or 0)
        degraded_count = int(group.get("degraded_count") or 0)
        readable_count = int(group.get("readable_body_count") or 0)
        confidence = _clamp_unit(0.62 + min(0.16, count * 0.025) + (0.06 if ok_count else 0.0) + (0.04 if readable_count else 0.0) + (0.02 if degraded_count else 0.0))
        proposals.append(
            {
                "semantic_key": f"action_pattern|{_clean_text(group.get('scope_key'))}|lightnovel_recommendation|deep_review",
                "semantic_kind": "action_pattern",
                "summary": summary,
                "confidence": confidence,
                "tags": [
                    "action_pattern",
                    "proactive_action",
                    "deep_review",
                    "evidence_review",
                    "domain:lightnovel_recommendation",
                    "lightnovel_recommendation",
                    "recall_axis:what_she_does",
                ],
                "source_episode_ids": _unique_clean_texts(list(group.get("source_episode_ids") or [])),
                "source_collection_ids": _unique_clean_texts(list(group.get("source_collection_ids") or [])),
                "source_refs": _unique_clean_texts(list(group.get("source_refs") or [])),
                "distill_strategy": "life_action_pattern_rule_consolidation",
            }
        )
    proposals.sort(key=lambda item: (-_clamp_unit(item.get("confidence")), _clean_text(item.get("semantic_key"))))
    return proposals


def _action_pattern_deep_review_is_meaningful(
    episode: Mapping[str, Any],
    *,
    metadata: Mapping[str, Any],
    result: str,
) -> bool:
    if _clean_text(result).upper() == "DEGRADED":
        text = re.sub(
            r"\s+",
            "",
            " ".join(
                [
                    _clean_text(episode.get("summary"), limit=700),
                    _clean_text(metadata.get("memory_anchor"), limit=500),
                    _clean_text(metadata.get("fact"), limit=500),
                    _clean_text(metadata.get("reason_code"), limit=120),
                ]
            ),
        ).lower()
        return bool(text and any(token in text for token in ("没拿到可读网页", "没拿到", "fetch_failed", "证据不足", "不能负责任", "不能装作")))
    return bool(_action_pattern_selected_title(metadata) or _action_pattern_reason_facets(metadata) or _action_pattern_has_readable_body(metadata))


def _action_pattern_scope_key(episode: Mapping[str, Any], *, metadata: Mapping[str, Any]) -> str:
    session_id = _clean_text(episode.get("session_id") or metadata.get("session_id"), limit=120)
    person_id = _clean_text(episode.get("person_id") or metadata.get("person_id"), limit=120)
    if not session_id and not person_id:
        return ""
    if session_id == "qq_dm_owner":
        return ""
    if session_id.startswith("qq_dm_"):
        return session_id
    return person_id or session_id


def _action_pattern_source_refs(episode: Mapping[str, Any], *, metadata: Mapping[str, Any]) -> list[str]:
    refs = _unique_clean_texts(
        [
            episode.get("primary_ref"),
            *_coerce_text_list(metadata.get("evidence_refs")),
            *_coerce_text_list(metadata.get("source_anchor_refs")),
        ],
        limit=220,
    )
    return refs[:16]


def _action_pattern_selected_title(metadata: Mapping[str, Any]) -> str:
    selected = metadata.get("selected") if isinstance(metadata.get("selected"), Mapping) else {}
    return _clean_text(selected.get("title") if isinstance(selected, Mapping) else None, limit=90)


def _action_pattern_has_readable_body(metadata: Mapping[str, Any]) -> bool:
    selected = metadata.get("selected") if isinstance(metadata.get("selected"), Mapping) else {}
    items = list(metadata.get("items") or []) if isinstance(metadata.get("items"), Sequence) and not isinstance(metadata.get("items"), (str, bytes, bytearray)) else []
    records = [selected, *[item for item in items if isinstance(item, Mapping)]]
    for record in records:
        if _clean_text(record.get("body_quality")).upper() == "READABLE_BODY":
            return True
        if _coerce_int(record.get("content_char_count")) >= 500:
            return True
        observations = " ".join(_coerce_text_list(record.get("body_observations")))
        if "正文" in observations:
            return True
    return False


def _action_pattern_reason_facets(metadata: Mapping[str, Any]) -> list[str]:
    selected = metadata.get("selected") if isinstance(metadata.get("selected"), Mapping) else {}
    items = list(metadata.get("items") or []) if isinstance(metadata.get("items"), Sequence) and not isinstance(metadata.get("items"), (str, bytes, bytearray)) else []
    raw_parts: list[str] = []
    for record in [selected, *[item for item in items[:6] if isinstance(item, Mapping)]]:
        raw_parts.extend(_coerce_text_list(record.get("body_observations")))
        raw_parts.extend(_coerce_text_list(record.get("score_reasons")))
        raw_parts.append(_clean_text(record.get("selection_reason"), limit=120))
        raw_parts.append(_clean_text(record.get("chapter_signal"), limit=120))
    compact = re.sub(r"\s+", "", "；".join(part for part in raw_parts if part)).lower()
    facets: list[str] = []

    def add(label: str) -> None:
        if label and label not in facets:
            facets.append(label)

    if any(token in compact for token in ("正文开篇", "进入故事", "开篇", "正文标题")):
        add("开篇是不是已经进入故事")
    if any(token in compact for token in ("对白", "人物互动", "互动", "关系")):
        add("对白和人物互动")
    if any(token in compact for token in ("恋爱日常", "亲密感", "日常梗", "成长/照顾线")):
        add("关系推进和日常读感")
    if any(token in compact for token in ("异世界", "皇女", "皇帝", "后宫", "身份钩子", "设定")):
        add("设定钩子是否直给")
    if any(token in compact for token in ("章节线索", "第一章", "第二章", "第16话", "第18话", "序章")):
        add("章节线索是否清楚")
    if any(token in compact for token in ("轻喜剧", "轻快", "读感")):
        add("读起来是否轻快")
    return facets[:6]


def _coerce_text_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        raw = list(value)
    else:
        raw = [value]
    return [_clean_text(item, limit=180) for item in raw if _clean_text(item, limit=180)]


def _build_action_pattern_semantic_summary(group: Mapping[str, Any]) -> str:
    count = int(group.get("count") or 0)
    ok_count = int(group.get("ok_count") or 0)
    degraded_count = int(group.get("degraded_count") or 0)
    readable_count = int(group.get("readable_body_count") or 0)
    if count <= 0:
        return ""
    evidence_label = "详情页或正文" if readable_count else "详情页"
    summary = f"主动行动模式：在轻小说推荐追问里，她会回去打开{evidence_label}再比较哪本更值得看；已记录 {count} 次深读/回看行动"
    stats: list[str] = []
    if ok_count:
        stats.append(f"{ok_count} 次拿到可用详情")
    if readable_count:
        stats.append(f"{readable_count} 次读到正文级证据")
    if degraded_count:
        stats.append(f"{degraded_count} 次因网页或 epub 不可读而降级")
    if stats:
        summary += f"，其中{'，'.join(stats)}"
    facets = _unique_clean_texts(list(group.get("reason_facets") or []), limit=40)
    if facets:
        summary += f"。判断时主要看：{'、'.join(facets[:5])}"
    titles = _unique_clean_texts(list(group.get("selected_titles") or []), limit=80)
    if titles:
        summary += f"。最近实际选中过：{'、'.join(titles[:4])}"
    if degraded_count:
        summary += "。如果网页或 epub 拿不到，她应该直接说明证据不足，不能装作已经读过"
    return _clean_text(summary + "。", limit=900)


def _semantic_proposals_are_same_collection_scope(first: Mapping[str, Any], second: Mapping[str, Any]) -> bool:
    if _clean_text(first.get("distill_strategy")) != "life_memory_collection_rule_consolidation":
        return False
    if _clean_text(second.get("distill_strategy")) != "life_memory_collection_rule_consolidation":
        return False
    return _clean_text(first.get("semantic_key")) == _clean_text(second.get("semantic_key"))


def _merge_collection_semantic_proposal(existing: dict[str, Any], incoming: Mapping[str, Any]) -> None:
    titles = _unique_clean_texts([*list(existing.get("collection_item_titles") or []), *list(incoming.get("collection_item_titles") or [])], limit=80)
    facets = _unique_clean_texts([*list(existing.get("collection_facets") or []), *list(incoming.get("collection_facets") or [])], limit=40)
    source_episode_ids = _unique_clean_texts([*list(existing.get("source_episode_ids") or []), *list(incoming.get("source_episode_ids") or [])])
    source_collection_ids = _unique_clean_texts([*list(existing.get("source_collection_ids") or []), *list(incoming.get("source_collection_ids") or [])])
    source_refs = _unique_clean_texts([*list(existing.get("source_refs") or []), *list(incoming.get("source_refs") or [])])
    tags = _unique_clean_texts([*list(existing.get("tags") or []), *list(incoming.get("tags") or [])])
    scope_label = _clean_text(existing.get("collection_scope_label")) or _clean_text(incoming.get("collection_scope_label"))
    domain_label = _clean_text(existing.get("collection_domain_label")) or _clean_text(incoming.get("collection_domain_label"))
    existing["collection_item_titles"] = titles
    existing["collection_facets"] = facets
    existing["collection_pushed_count"] = max(
        len(titles),
        int(existing.get("collection_pushed_count") or 0) + int(incoming.get("collection_pushed_count") or 0),
    )
    existing["collection_unique_count"] = len(titles)
    existing["source_episode_ids"] = source_episode_ids
    existing["source_collection_ids"] = source_collection_ids
    existing["source_refs"] = source_refs
    existing["tags"] = tags
    existing["confidence"] = max(_clamp_unit(existing.get("confidence")), _clamp_unit(incoming.get("confidence")))
    existing["summary"] = _build_collection_semantic_summary(
        scope_label=scope_label,
        domain_label=domain_label,
        titles=titles,
        facets=facets,
        source_episode_count=len(source_episode_ids),
        pushed_count=int(existing.get("collection_pushed_count") or len(titles)),
    )


def _unique_clean_texts(values: Sequence[Any], *, limit: int | None = None) -> list[str]:
    items: list[str] = []
    seen: set[str] = set()
    for value in list(values or []):
        text = _clean_text(value, limit=limit)
        if not text or text in seen:
            continue
        seen.add(text)
        items.append(text)
    return items


def _semantic_collection_title_records(items: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in list(items or []):
        metadata = item.get("metadata") if isinstance(item.get("metadata"), Mapping) else {}
        title = (
            _collection_item_resolved_canonical_title(item)
            or _clean_text(metadata.get("canonical_title") if isinstance(metadata, Mapping) else "", limit=120)
            or _clean_text(item.get("item_title") or _collection_item_title(item), limit=120)
        )
        key = _collection_item_canonical_key(title) or _clean_text(metadata.get("canonical_key") if isinstance(metadata, Mapping) else "")
        if not title:
            continue
        dedupe_key = key or re.sub(r"\s+", "", title.lower())
        if not dedupe_key or dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        records.append({"title": _clean_text(title, limit=80), "canonical_key": dedupe_key})
    return records


def _build_collection_semantic_summary(
    *,
    scope_label: str,
    domain_label: str,
    titles: Sequence[Any],
    facets: Sequence[Any],
    source_episode_count: int,
    pushed_count: int | None = None,
) -> str:
    cleaned_titles = _unique_clean_texts(titles, limit=80)
    title_part = "、".join(cleaned_titles[:10])
    if len(cleaned_titles) > 10:
        title_part += f"等 {len(cleaned_titles)} 本"
    unique_count = len(cleaned_titles)
    raw_count = max(unique_count, int(pushed_count or unique_count))
    label = f"{_clean_text(scope_label)}{_clean_text(domain_label) or '推荐'}"
    if raw_count > unique_count:
        summary = f"经历集合：{label}发过 {raw_count} 条推荐，去掉重复标题主要是 {unique_count} 本：{title_part}。"
    else:
        summary = f"经历集合：{label}记录了 {unique_count} 本：{title_part}。"
    cleaned_facets = _unique_clean_texts(facets, limit=40)
    if cleaned_facets:
        summary += f" 题材线索：{'、'.join(cleaned_facets[:8])}。"
    if source_episode_count:
        summary += f" 可追溯到 {int(source_episode_count)} 个推荐经历。"
    return _clean_text(summary, limit=900)


def _semantic_collection_facets(items: Sequence[Mapping[str, Any]], *, limit: int = 10) -> list[str]:
    facets: list[str] = []

    def add(value: Any) -> None:
        for part in _split_collection_facet_text(value):
            if part not in facets:
                facets.append(part)
                if len(facets) >= max(1, int(limit)):
                    return

    for item in list(items or []):
        metadata = item.get("metadata") if isinstance(item.get("metadata"), Mapping) else {}
        for key in (
            "selection_reason",
            "rationale",
            "reason",
            "look_point",
            "genre",
            "category",
            "topic",
            "theme",
            "tags",
        ):
            add(item.get(key))
            add(metadata.get(key) if isinstance(metadata, Mapping) else None)
        summary = _clean_text(item.get("item_summary") or _collection_item_summary(item), limit=500)
        for label in ("看点", "题材"):
            for match in re.finditer(rf"(?:^|[｜|；;]){label}\s*[:：]\s*([^｜|；;]+)", summary):
                add(match.group(1))
        if len(facets) >= max(1, int(limit)):
            break
    return facets[: max(1, int(limit))]


def _split_collection_facet_text(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        raw_parts = [str(item or "") for item in value]
    else:
        raw_parts = [str(value or "")]
    out: list[str] = []
    for raw in raw_parts:
        text = _clean_text(raw, limit=260)
        if not text:
            continue
        text = re.sub(r"https?://\S+", " ", text)
        text = re.sub(r"^(?:看点|题材|简介|原因|理由|命中偏好|公开信号较好)\s*[:：]\s*", "", text)
        text = text.replace("｜", "、").replace("|", "、")
        for part in re.split(r"[、，,；;/\n\r]+", text):
            cleaned = _normalize_collection_facet(part)
            if cleaned and cleaned not in out:
                out.append(cleaned)
    return out


def _normalize_collection_facet(value: Any) -> str:
    text = _clean_text(value, limit=80)
    text = re.sub(r"^(?:看点|题材|简介|原因|理由|命中偏好|公开信号较好)\s*[:：]\s*", "", text)
    text = re.sub(r"\s+", "", text)
    text = text.strip("。.!！?？:：；;、,，（）()[]【】")
    if not text or len(text) < 2:
        return ""
    if re.fullmatch(r"\d+(?:\.\d+)?[万wkWK]?(?:浏览|评论|字|章|话|小时|分钟|天|月|年|日前|小时前|分钟前)?", text):
        return ""
    if re.search(r"(?:小时|分钟|日前|小时前|分钟前|更新|评论|浏览|资料完整度|公开信号|详情|http|https)", text, re.IGNORECASE):
        return ""
    if text in _COLLECTION_FACET_STOPWORDS:
        return ""
    if len(text) > 18:
        text = text[:18].rstrip("，,、。")
    return text


_COLLECTION_FACET_STOPWORDS: frozenset[str] = frozenset(
    {
        "资料完整度高",
        "资料完整度较高",
        "热度较高",
        "更新较新",
        "信息完整度比普通列表项更高",
        "题材明确",
        "路线直球但题材明确",
    }
)


def _semantic_collection_facet_tags(facets: Sequence[str]) -> list[str]:
    tags: list[str] = []
    for facet in list(facets or [])[:8]:
        normalized = re.sub(r"\s+", "_", _clean_text(facet, limit=40).lower())
        normalized = normalized.replace("：", "_").replace(":", "_")
        normalized = normalized.strip("_")
        if normalized:
            tags.append(f"theme:{normalized}")
    return tags


def _semantic_collection_scope_label(scope_key: str) -> str:
    value = _clean_text(scope_key)
    if value.startswith(f"{COLLECTION_SCOPE_DELIVERY}:"):
        return "单次"
    if value.startswith(f"{COLLECTION_SCOPE_DAILY}:"):
        return value.split(":", 1)[1] + " 的"
    if value == COLLECTION_SCOPE_RECENT:
        return "最近的"
    if value.startswith(f"{COLLECTION_SCOPE_ROLLING_PREFIX}:"):
        return _rolling_scope_label(value) + "的"
    return f"{value} "


def _semantic_collection_scope_kind(scope_key: str) -> str:
    value = _clean_text(scope_key)
    if value.startswith(f"{COLLECTION_SCOPE_DAILY}:"):
        return "daily"
    if value == COLLECTION_SCOPE_RECENT:
        return "recent"
    if value.startswith(f"{COLLECTION_SCOPE_ROLLING_PREFIX}:"):
        return "rolling"
    if value.startswith(f"{COLLECTION_SCOPE_DELIVERY}:"):
        return "delivery"
    return value or "unknown"


def _collection_scope_should_distill_to_semantic(*, scope_key: str, source_episode_count: int) -> bool:
    value = _clean_text(scope_key)
    if value.startswith(f"{COLLECTION_SCOPE_DAILY}:"):
        return True
    if value == COLLECTION_SCOPE_RECENT:
        return int(source_episode_count) >= 2
    if value.startswith(f"{COLLECTION_SCOPE_ROLLING_PREFIX}:"):
        return False
    return False


def _rolling_scope_label(scope_key: str) -> str:
    days = _collection_scope_window_days(scope_key)
    if days <= 0:
        return "滚动窗口"
    return f"近 {days} 天"


def _semantic_collection_confidence(*, collection: Mapping[str, Any], item_count: int) -> float:
    strength = _clamp_unit(collection.get("memory_strength"), default=_clamp_unit(collection.get("salience"), default=0.4))
    count_bonus = min(0.12, max(0, int(item_count)) * 0.015)
    return round(_clamp_unit(0.58 + strength * 0.24 + count_bonus), 4)


def _should_compact_sleep_candidate(
    episode: Mapping[str, Any],
    *,
    current_ts_ms: int,
    semantic_referenced_episode_ids: set[str],
    min_age_ms: int,
) -> bool:
    episode_id = _clean_text(episode.get("episode_id"))
    if not episode_id or episode_id in semantic_referenced_episode_ids:
        return False
    metadata = _episode_metadata_dict(episode)
    if bool(metadata.get("e2e_probe")) or bool(metadata.get("test_probe")) or bool(metadata.get("anchor_only")):
        return False
    if bool(metadata.get("should_not_compact")):
        return False
    kind = _clean_text(episode.get("episode_kind"))
    if kind in {EPISODE_SEMANTIC, EPISODE_REFLECTION}:
        return False
    truth_state = _clean_text(episode.get("truth_state")).upper()
    if truth_state not in {TRUTH_VERIFIED, TRUTH_NARRATIVE_ONLY}:
        return False
    retention_state = _clean_text(episode.get("retention_state")).upper()
    if retention_state not in {RETENTION_WARM, RETENTION_COLD}:
        return False
    if _clean_text(episode.get("compressed_into_episode_id")):
        return False
    created = int(episode.get("created_ts_ms") or episode.get("last_touched_ts_ms") or current_ts_ms)
    if max(0, int(current_ts_ms) - created) < max(0, int(min_age_ms)):
        return False
    strength = _clamp_unit(episode.get("memory_strength"), default=_clamp_unit(episode.get("salience")))
    if strength < 0.18 or strength >= 0.72:
        return False
    summary = _clean_text(episode.get("summary"), limit=1200)
    action = _clean_text(episode.get("action") or metadata.get("action_kind")).lower()
    if not summary:
        return False
    if _looks_like_recommendation_followup_chat_noise(summary):
        return False
    if _importance_keyword_boost(summary) > 0.0:
        return False
    if action in {
        "lightnovel_recommendation_delivery",
        "lightnovel_recommendation_deep_review",
        "qq_reply_sent",
        "wechat_reply_sent",
        "gateway_reply_sent",
        "channel_message_sent",
        "memory_compaction",
    }:
        return False
    return True


def _sleep_compaction_group_key(episode: Mapping[str, Any]) -> tuple[str, str, str, str]:
    created = int(episode.get("created_ts_ms") or episode.get("last_touched_ts_ms") or 0)
    day_key = _collection_day_key(created) if created > 0 else "unknown-day"
    person_id = _clean_text(episode.get("person_id")) or "global"
    session_id = _clean_text(episode.get("session_id")) or "global"
    kind = _clean_text(episode.get("episode_kind")) or "episode"
    return (person_id, session_id, day_key, kind)


def _sleep_compaction_payload(group: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    items = [dict(item) for item in list(group or []) if _clean_text(item.get("episode_id"))]
    if not items:
        return {"summary": "", "kept_facts": [], "dropped_details": [], "group_key": ""}
    first = min(int(item.get("created_ts_ms") or 0) for item in items)
    day_key = _collection_day_key(first) if first > 0 else "未知日期"
    kind = _clean_text(items[0].get("episode_kind")).replace("_", " ") or "episode"
    kept_facts: list[str] = []
    dropped_details: list[str] = []
    for index, item in enumerate(items, start=1):
        summary = _clean_text(item.get("summary"), limit=180)
        if not summary:
            continue
        kept_facts.append(f"{index}. {summary}")
        if len(summary) > 70:
            dropped_details.append("省略原始长文本细节")
    source_ids = [_clean_text(item.get("episode_id")) for item in items if _clean_text(item.get("episode_id"))]
    group_key = "|".join(str(part) for part in _sleep_compaction_group_key(items[0]))
    summary = (
        f"睡眠回放压缩：{day_key} 的 {len(items)} 条{kind}被整理成一个场景摘要；"
        f"保留主线：{'；'.join(kept_facts[:4])}。"
    )
    if len(kept_facts) > 4:
        summary += f" 另有 {len(kept_facts) - 4} 条低层细节只保留审计锚点。"
    return {
        "summary": summary,
        "kept_facts": kept_facts,
        "dropped_details": list(dict.fromkeys([*dropped_details, "低层重复状态与中间过程不进入主动记忆"])),
        "source_episode_ids": source_ids,
        "group_key": group_key,
    }


def _semantic_proposals_from_episode(
    episode: Mapping[str, Any],
    evidence_rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    metadata = _episode_metadata_dict(episode)
    if _is_synthetic_life_record(episode, metadata):
        return []
    action = _clean_text(episode.get("action") or metadata.get("action_kind")).lower()
    event_source = _clean_text(metadata.get("event_source")).lower()
    if action.startswith("sub_agent:") and not bool(metadata.get("should_distill_to_memory")):
        return []
    if action in {
        "lightnovel_recommendation_delivery",
        "lightnovel_recommendation_deep_review",
        "qq_reply_sent",
        "wechat_reply_sent",
        "gateway_reply_sent",
        "channel_message_sent",
    }:
        return []
    if event_source in {"inner_world", "inner_stream", "inner_activity"} and not bool(metadata.get("should_distill_to_memory")):
        return []
    episode_id = _clean_text(episode.get("episode_id"))
    if not episode_id:
        return []
    source_refs = [
        _clean_text(ev.get("source_ref"))
        for ev in list(evidence_rows or [])
        if _clean_text(ev.get("source_ref"))
    ]
    texts = _semantic_source_texts(episode, evidence_rows)
    proposals: list[dict[str, Any]] = []
    for text in texts:
        semantic = _semantic_summary_for_text(text, episode=episode)
        if not semantic:
            continue
        kind = semantic["kind"]
        summary = semantic["summary"]
        person_id = _clean_text(episode.get("person_id")) or "global"
        if kind in {"failure_lesson", "self_change"}:
            person_id = "global"
        key_text = _clean_text(semantic.get("key_hint")) or re.sub(r"\s+", "", summary.lower())[:220]
        semantic_key = f"{kind}|{person_id}|{key_text[:220]}"
        confidence = _semantic_confidence(episode=episode, base=float(semantic.get("base_confidence") or 0.0))
        proposals.append(
            {
                "semantic_key": semantic_key,
                "semantic_kind": kind,
                "summary": summary,
                "confidence": confidence,
                "tags": list(semantic.get("tags") or []),
                "source_episode_ids": [episode_id],
                "source_refs": list(dict.fromkeys(source_refs)),
                "open_loop_status": semantic.get("open_loop_status"),
                "open_loop_close_reason": semantic.get("open_loop_close_reason"),
            }
        )
    return proposals


def _sqlite_table_exists(conn: Any, table_name: str) -> bool:
    try:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ? LIMIT 1",
            (_clean_text(table_name),),
        ).fetchone()
    except Exception:
        return False
    return row is not None


def _semantic_proposal_from_correction_row(row: Mapping[str, Any]) -> dict[str, Any] | None:
    correction_id = _clean_text(row.get("correction_id"))
    if not correction_id:
        return None
    reason_code = _clean_text(row.get("reason_code"))
    detail = _clean_text(row.get("detail"), limit=700)
    if not reason_code:
        return None

    source_episode = {
        "episode_id": _clean_text(row.get("episode_id")),
        "person_id": _clean_text(row.get("source_person_id")),
        "session_id": _clean_text(row.get("source_session_id")),
        "primary_ref": _clean_text(row.get("source_primary_ref")),
        "summary": _clean_text(row.get("source_summary"), limit=900),
        "metadata": row.get("source_metadata"),
    }
    correction_episode = {
        "episode_id": _clean_text(row.get("correction_episode_id")),
        "person_id": _clean_text(row.get("correction_person_id")),
        "session_id": _clean_text(row.get("correction_session_id")),
        "primary_ref": _clean_text(row.get("correction_primary_ref")),
        "summary": _clean_text(row.get("correction_summary"), limit=900),
        "truth_state": _clean_text(row.get("correction_truth_state")).upper(),
        "status": _clean_text(row.get("correction_status")).upper(),
        "retention_state": _clean_text(row.get("correction_retention_state")).upper(),
        "metadata": row.get("correction_metadata"),
    }
    source_meta = _episode_metadata_dict(source_episode)
    correction_meta = _episode_metadata_dict(correction_episode)
    if _is_synthetic_life_record(source_episode, source_meta):
        return None
    if correction_episode["episode_id"] and _is_synthetic_life_record(correction_episode, correction_meta):
        return None

    correction_policy = correction_meta.get("memory_policy") if isinstance(correction_meta.get("memory_policy"), Mapping) else {}
    correction_memory_class = _episode_memory_class_from_metadata(correction_meta)
    correction_episode_is_candidate = (
        bool(correction_episode["episode_id"])
        and correction_episode["truth_state"] == TRUTH_VERIFIED
        and correction_episode["status"] == STATUS_ACTIVE
        and correction_episode["retention_state"] != RETENTION_GONE
        and (bool(correction_policy.get("semantic_candidate")) or correction_memory_class == MEMORY_CLASS_CORRECTION)
    )
    if correction_episode_is_candidate:
        return None

    body, key_hint, extra_tags, person_scope = _semantic_correction_body_and_key(
        reason_code=reason_code,
        detail=detail,
        source_summary=_clean_text(row.get("source_summary"), limit=900),
    )
    if not body:
        return None
    source_refs = list(
        dict.fromkeys(
            item
            for item in (
                f"life_memory_correction://{correction_id}",
                _clean_text(row.get("correction_primary_ref")),
                _clean_text(row.get("source_primary_ref")),
            )
            if item
        )
    )
    source_episode_ids: list[str] = []
    if correction_episode["episode_id"] and correction_episode["truth_state"] == TRUTH_VERIFIED and correction_episode["status"] == STATUS_ACTIVE:
        source_episode_ids.append(correction_episode["episode_id"])
    person_id = person_scope or _clean_text(row.get("correction_person_id")) or _clean_text(row.get("source_person_id")) or "global"
    semantic_key = f"correction|{person_id}|{key_hint}"
    return {
        "semantic_key": semantic_key,
        "semantic_kind": "correction",
        "summary": f"纠错记忆：{body}",
        "confidence": 0.82,
        "tags": list(dict.fromkeys(["correction", "memory_correction", *extra_tags])),
        "source_episode_ids": source_episode_ids,
        "source_correction_ids": [correction_id],
        "source_refs": source_refs,
        "distill_strategy": "life_memory_correction_rule_consolidation",
    }


def _semantic_correction_body_and_key(
    *,
    reason_code: str,
    detail: str,
    source_summary: str,
) -> tuple[str, str, list[str], str]:
    reason = _clean_text(reason_code, limit=160).upper()
    joined = re.sub(r"\s+", "", " ".join([reason, detail, source_summary])).lower()
    if not joined:
        return "", "", [], ""
    if "bad_lightnovel_deep_review_memory" in joined or "quality_claim_without_detail_evidence" in joined:
        return (
            "轻小说好看或质量判断必须有网页正文、详情页正文或 epub 证据；已抑制缺少详情证据的旧判断。",
            "lightnovel_quality_requires_detail_evidence",
            ["evidence_gate", "lightnovel_recommendation"],
            "global",
        )
    if "正文证据" in joined or "详情证据" in joined or "没有正文" in joined or "缺少详情" in joined:
        return (
            "用户纠正过选书判断缺少正文证据；后续评价哪本好看前，必须先核对网页正文、详情页正文或 epub 证据。",
            "recommendation_quality_needs_body_evidence",
            ["evidence_gate", "recommendation_quality"],
            "global",
        )
    if "封面有猫" in joined or "没有这种书" in joined or "没有这本" in joined:
        return (
            "用户纠正过错误记忆：没有“封面有猫”这本；后续不能把这个说成事实。",
            "false_memory_cat_cover",
            ["false_memory", "visual_claim_gate"],
            "",
        )
    cleaned_detail = _clean_text(detail or reason_code, limit=260).strip("。；; ")
    if not cleaned_detail:
        return "", "", [], ""
    cleaned_detail = re.sub(r"^Suppressed stale [^:：]{1,120}[:：]\s*", "", cleaned_detail, flags=re.IGNORECASE).strip()
    body = f"曾经发生过纠错或抑制：{cleaned_detail}。后续召回时要以纠错后的可信版本为准。"
    key_hint = _stable_hash(re.sub(r"\s+", "", cleaned_detail).lower(), size=18)
    return body, key_hint, ["correction_chain"], ""


def _merge_semantic_summaries(first: Any, second: Any) -> str:
    a = _clean_text(first, limit=900)
    b = _clean_text(second, limit=900)
    if not a:
        return b
    if not b or b == a:
        return a
    if b in a:
        return a
    if a in b:
        return b
    prefix_a, body_a = _split_semantic_summary(a)
    prefix_b, body_b = _split_semantic_summary(b)
    if prefix_a and prefix_a == prefix_b:
        clauses = [*_semantic_summary_clauses(body_a), *_semantic_summary_clauses(body_b)]
        merged = "；".join(list(dict.fromkeys(clause for clause in clauses if clause)))
        return _clean_text(f"{prefix_a}：{merged}", limit=900)
    return _clean_text(f"{a}；{b}", limit=900)


def _semantic_existing_summary_should_be_replaced(existing: Any, incoming: Any, *, semantic_kind: str) -> bool:
    current = _clean_text(existing, limit=900)
    proposal = _clean_text(incoming, limit=900)
    if not current or not proposal:
        return False
    if _semantic_summary_has_replay_artifacts(current) and not _semantic_summary_has_replay_artifacts(proposal):
        return True
    if _clean_text(semantic_kind) == "self_change" and _self_change_summary_has_cross_topic_pollution(current, clean_candidate=proposal):
        return True
    return False


def _semantic_summary_has_replay_artifacts(value: Any) -> bool:
    text = _clean_text(value, limit=900)
    return any(marker in text for marker in ("睡眠回放强化", "Owner directive:", "Applied result:"))


def _self_change_summary_has_cross_topic_pollution(value: Any, *, clean_candidate: Any) -> bool:
    current = re.sub(r"\s+", "", _clean_text(value, limit=900)).lower()
    proposal = re.sub(r"\s+", "", _clean_text(clean_candidate, limit=900)).lower()
    if not current or not proposal:
        return False
    if not _semantic_is_memory_system_directive(current):
        return False
    if not _semantic_is_memory_system_directive(proposal):
        return False
    cross_topic_markers = (
        "thalamus",
        "模型池",
        "deepseek",
        "gemini",
        "不要用pro",
        "不用pro",
        "推荐轻小说",
        "拟人化发送",
        "简介后应该换行",
        "每本简介后",
        "卡片各一段",
        "总览完整一段",
    )
    return any(marker in current for marker in cross_topic_markers) and not any(marker in proposal for marker in cross_topic_markers)


def _split_semantic_summary(value: Any) -> tuple[str, str]:
    text = _clean_text(value, limit=900)
    if "：" in text:
        prefix, body = text.split("：", 1)
        return _clean_text(prefix), _clean_text(body)
    if ":" in text:
        prefix, body = text.split(":", 1)
        return _clean_text(prefix), _clean_text(body)
    return "", text


def _semantic_summary_clauses(value: Any) -> list[str]:
    text = _clean_text(value, limit=900).strip("。；; ")
    if not text:
        return []
    parts = re.split(r"[；;\n\r]+", text)
    return [_clean_text(part.strip("。；; "), limit=220) for part in parts if _clean_text(part)]


def _semantic_source_texts(
    episode: Mapping[str, Any],
    evidence_rows: Sequence[Mapping[str, Any]],
) -> list[str]:
    texts: list[str] = []
    metadata = _episode_metadata_dict(episode)
    memory_class = _episode_memory_class_from_metadata(metadata)
    trace_meta = metadata.get("trace_stage_meta") if isinstance(metadata.get("trace_stage_meta"), Mapping) else {}
    reply_source = _clean_text(metadata.get("reply_source") or (trace_meta or {}).get("reply_source")).lower()
    policy = metadata.get("memory_policy") if isinstance(metadata.get("memory_policy"), Mapping) else {}
    semantic_candidate = bool(policy.get("semantic_candidate")) or memory_class in _SEMANTIC_PRIORITY_MEMORY_CLASSES
    include_verified_assistant_reply = (
        semantic_candidate
        and reply_source == "surface_memory_direct_answer"
        and _surface_memory_direct_reply_has_trusted_provenance(metadata)
    )

    def add(value: Any) -> None:
        text = _clean_text(value, limit=700)
        if not text or text in texts:
            return
        texts.append(text)

    for ev in list(evidence_rows or []):
        kind = _clean_text(ev.get("evidence_kind")).lower()
        content = _clean_text(ev.get("content"), limit=700)
        if not content:
            continue
        if kind in {EVIDENCE_USER_TEXT, EVIDENCE_GATEWAY_MESSAGE, EVIDENCE_SCHEDULER_EVENT, EVIDENCE_TOOL_RESULT}:
            add(content)
        elif kind == EVIDENCE_ASSISTANT_REPLY and include_verified_assistant_reply:
            add(content)
    if texts:
        return texts[:8]
    summary = _clean_text(episode.get("summary"), limit=900)
    if summary:
        for line in re.split(r"[\n\r]+", summary):
            if re.match(r"^\s*Assistant\s*[:：]", line) and not include_verified_assistant_reply:
                continue
            cleaned = _clean_text(re.sub(r"^(User|Assistant|Perception)\s*[:：]\s*", "", line), limit=700)
            add(cleaned)
    return texts[:8]


def _surface_memory_direct_reply_has_trusted_provenance(metadata: Mapping[str, Any]) -> bool:
    trace_meta = metadata.get("trace_stage_meta") if isinstance(metadata.get("trace_stage_meta"), Mapping) else {}
    packet = (
        trace_meta.get("surface_memory_recall_packet")
        if isinstance(trace_meta.get("surface_memory_recall_packet"), Mapping)
        else metadata.get("surface_memory_recall_packet")
    )
    if not isinstance(packet, Mapping) or bool(packet.get("degraded")):
        return False
    evidence_refs = [item for item in list(packet.get("evidence_refs") or []) if _trusted_surface_memory_provenance_ref(item)]
    source_refs = [item for item in list(packet.get("source_refs") or []) if _trusted_surface_memory_provenance_ref(item)]
    rows = packet.get("rows")
    has_rows = isinstance(rows, Sequence) and not isinstance(rows, (str, bytes, bytearray)) and any(
        isinstance(row, Mapping) and (
            _trusted_surface_memory_provenance_ref(row.get("source_ref"))
            or _trusted_surface_memory_provenance_ref(row.get("evidence_ref"))
        )
        for row in rows
    )
    trace = packet.get("trace") if isinstance(packet.get("trace"), Mapping) else {}
    readiness = trace.get("recall_answer_readiness") if isinstance(trace.get("recall_answer_readiness"), Mapping) else {}
    ready = bool(readiness.get("ready_to_answer")) if readiness else True
    response_mode = _clean_text(readiness.get("response_mode")).lower() if readiness else ""
    if response_mode in {"state_memory_missing", "state_recall_validation_failed", "state_recall_coverage_missing", "fetch_or_request_detail_evidence"}:
        return False
    return bool(ready and (evidence_refs or source_refs or has_rows))


def _trusted_surface_memory_provenance_ref(value: Any) -> bool:
    ref = _clean_text(value).lower()
    if not ref:
        return False
    if ref.startswith("semantic://"):
        return False
    if ref.startswith("ledger://semantic_memory/"):
        return False
    return True


def _semantic_summary_for_text(text: str, *, episode: Mapping[str, Any]) -> dict[str, Any] | None:
    statement = _normalize_semantic_statement(text)
    if len(statement) < 4 or _semantic_text_has_secret(statement):
        return None
    if _semantic_statement_is_lookup_question(statement):
        return None
    operational_failure = _semantic_operational_failure_summary(statement, episode=episode)
    if operational_failure:
        return operational_failure
    if _semantic_text_is_sensitive_or_noise(statement):
        return None
    compact = re.sub(r"\s+", "", statement).lower()
    action = _clean_text(episode.get("action")).lower()
    tags: list[str] = []
    kind = ""
    prefix = ""
    base_confidence = 0.60

    if _semantic_is_commitment(compact):
        open_loop_status = _semantic_commitment_status(compact)
        kind = "commitment"
        prefix = "承诺完成" if open_loop_status == OPEN_LOOP_CLOSED else ("承诺取消" if open_loop_status == OPEN_LOOP_CANCELLED else "承诺和待办")
        tags = ["commitment", "open_loop", f"open_loop:{open_loop_status.lower()}"]
        base_confidence = 0.78
    elif _semantic_is_self_change(compact, action=action):
        kind = "self_change"
        prefix = "自我改变"
        tags = ["self_change", "capability_change"]
        base_confidence = 0.76
    elif _semantic_is_relationship_feedback(compact):
        feedback = _semantic_relationship_feedback_body(statement, episode=episode)
        if not feedback:
            return None
        return {
            "kind": "relationship_feedback",
            "summary": f"关系反馈：{feedback}",
            "tags": ["relationship_feedback", "response_quality"],
            "base_confidence": 0.80,
            "key_hint": _semantic_key_hint(kind="relationship_feedback", body=feedback),
        }
    elif _semantic_is_user_preference(compact):
        kind = "user_preference"
        prefix = "用户偏好"
        tags = ["user_preference", "preference"]
        base_confidence = 0.82
    elif _semantic_is_failure_lesson(compact, action=action):
        kind = "failure_lesson"
        prefix = "故障经验"
        tags = ["failure_lesson", "operations"]
        base_confidence = 0.74
    elif _semantic_is_correction_fact(compact):
        kind = "correction"
        prefix = "纠错记忆"
        tags = ["correction", "memory_correction"]
        base_confidence = 0.76
    elif _semantic_is_stable_fact(compact):
        kind = "stable_fact"
        prefix = "稳定事实"
        tags = ["stable_fact"]
        base_confidence = 0.72
    else:
        return None

    body = _semantic_statement_body(statement)
    if not body:
        return None
    return {
        "kind": kind,
        "summary": f"{prefix}：{body}",
        "tags": tags,
        "base_confidence": base_confidence,
        "key_hint": _semantic_key_hint(kind=kind, body=body),
        "open_loop_status": open_loop_status if kind == "commitment" else "",
        "open_loop_close_reason": _semantic_commitment_close_reason(body) if kind == "commitment" else "",
    }


def _semantic_key_hint(*, kind: str, body: str) -> str:
    compact = re.sub(r"\s+", "", _clean_text(body, limit=700)).lower()
    if not compact:
        return ""
    if kind == "user_preference":
        if any(token in compact for token in ("thalamus", "模型池", "deepseek", "codex", "不要用pro", "不用pro", "主链不要")):
            return "llm_routing"
        if any(token in compact for token in ("推荐", "推送")) and any(
            token in compact for token in ("格式", "卡片", "分段", "换行", "模板腔", "总览", "简介", "拟人化发送")
        ):
            return "recommendation_format"
    if kind == "commitment":
        return _semantic_commitment_key_hint(body)
    if kind == "stable_fact" and "onebot" in compact and "edge" in compact:
        return "onebot_edge_location"
    if kind == "stable_fact":
        if "测试代号" in compact:
            return "test_code"
        if "海边灯塔" in compact:
            return "seaside_lighthouse_anchor"
        if "prewarm_latency_ms" in compact or "预热指标" in compact:
            return "app_server_prewarm_metrics"
    if kind == "self_change" and _semantic_is_memory_system_directive(compact):
        return "memory_system_design"
    if kind == "relationship_feedback":
        if any(token in compact for token in ("不像人话", "正常聊天", "模板", "很呆", "文本质量", "台词腔")):
            return "natural_conversation_quality_feedback"
        if any(token in compact for token in ("胡说八道", "编造", "证据", "召回", "记忆")):
            return "memory_recall_quality_feedback"
        if any(token in compact for token in ("服务", "没起来", "白费功夫", "连不上")):
            return "runtime_readiness_feedback"
        return "interaction_quality_feedback"
    if kind == "failure_lesson":
        reason = _semantic_extract_field(body, "reason") or _semantic_extract_field(body, "原因")
        action_match = re.search(r"([A-Za-z0-9_.:-]{3,80})\s*失败", body)
        if reason or action_match:
            return f"{_clean_text(action_match.group(1) if action_match else 'failure').lower()}|{_clean_text(reason).lower()}"
    return ""


def _normalize_semantic_statement(text: str) -> str:
    value = _clean_text(text, limit=700)
    value = re.sub(r"^(User|Assistant|Perception)\s*[:：]\s*", "", value).strip()
    value = re.sub(r"^(记忆线索|长期记忆|召回线索)\s*[:：]\s*", "", value).strip()
    value = re.sub(r"^行动记忆：[^；;]{1,120}[；;]\s*", "", value).strip()
    value = re.sub(r"^(记住|请记住|以后记住)[，,：:\s]*", "", value).strip()
    value = re.sub(r"^记得[，,：:\s]*", "", value).strip()
    value = re.sub(r"^(?:再补一个|补一个|一个)?(?:长对话测试事实|项目事实|测试事实|记忆事实)[，,：:\s]*", "", value).strip()
    return value.strip("。；; \n\t")










def _semantic_commitment_close_reason(body: str) -> str:
    compact = re.sub(r"\s+", "", _clean_text(body, limit=700)).lower()
    status = _semantic_commitment_status(compact)
    return _open_loop_close_reason_for_status(status)


def _semantic_commitment_key_hint(body: str) -> str:
    compact = re.sub(r"\s+", "", _clean_text(body, limit=700)).lower()
    if not compact:
        return ""
    buckets: list[str] = []
    if any(token in compact for token in ("记忆系统", "记忆", "召回", "episode", "evidence", "openloop", "open_loop")):
        buckets.append("memory_system")
    if any(token in compact for token in ("推荐", "轻小说", "网页", "epub", "正文", "书籍")):
        buckets.append("recommendation")
    if any(token in compact for token in ("qq", "onebot", "gateway", "edge", "微信", "推送")):
        buckets.append("gateway")
    if any(token in compact for token in ("模型", "路由", "thalamus", "deepseek", "gemini", "codex", "pro")):
        buckets.append("llm_routing")
    if any(token in compact for token in ("后台", "调度", "scheduler", "任务", "每日")):
        buckets.append("scheduler")
    if any(token in compact for token in ("测试", "实测", "批测", "回归")):
        buckets.append("verification")
    if buckets:
        return "|".join(dict.fromkeys(buckets))
    normalized = re.sub(
        r"(承诺|待办|todo|openloop|open_loop|后续要|下次继续|未完成|已经完成|已完成|完成了|关闭这个待办|取消这个待办|要做|开始处理|开始做|继续)",
        "",
        compact,
    )
    normalized = re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "", normalized)
    return normalized[:80] or _stable_hash(compact, size=18)





def _semantic_confidence(*, episode: Mapping[str, Any], base: float) -> float:
    salience = _clamp_unit(episode.get("salience"), default=0.0)
    strength = _clamp_unit(episode.get("memory_strength"), default=salience)
    return round(_clamp_unit(max(base, 0.45 + strength * 0.35 + salience * 0.15)), 4)
