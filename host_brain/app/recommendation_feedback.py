"""从用户显式偏好或推荐反馈导出可审计的学习候选事件。

本模块只做保守的文本归因与候选构造，不决定是否持久化。它优先把反馈绑定到
同会话、同时间窗的已投递推荐；绑定不唯一时降级为“推荐集合”而非猜测具体
书目。这样长期偏好学习不会因提问、模糊代词或旧推荐串线而产生虚假事实。
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from typing import Any, Mapping

from host_brain.app.user_preference_store import (
    LIGHTNOVEL_DOMAIN,
    UserPreferenceEvent,
    UserPreferenceStore,
)

_SPACE_RE = re.compile(r"\s+")
_QUESTION_RE = re.compile(
    r"(我喜欢什么|我的偏好|我的喜好|我的轻小说偏好|喜欢哪些|喜欢哪类|喜欢哪种|喜欢什么类型|偏好都有哪些|口味是什么)"
)
_PREFERENCE_QUERY_RE = re.compile(
    r"("
    r"(?:你)?(?:知道|记得|还记得).{0,16}(?:我|我的).{0,12}(?:喜欢|偏好|喜好|口味)"
    r"|(?:我|我的).{0,12}(?:喜欢|偏好|喜好|口味).{0,24}(?:是什么|是啥|有哪些|哪种|哪类|哪一类|什么类型|什么|吗|嘛|呢)"
    r"|(?:你觉得|你认为).{0,12}我.{0,12}(?:喜欢|偏好|喜好|口味)"
    r")",
    re.IGNORECASE,
)
_LIGHTNOVEL_DOMAIN_RE = re.compile(
    r"(小说|轻小说|书|推荐|lightnovel|奇幻|后宫|异世界|恋爱|校园|第[一二三四五六七八九十\d]+本|这本|那本|上一本|下一本)",
    re.IGNORECASE,
)
_POSITIVE_FEEDBACK_RE = re.compile(r"(喜欢|不错|可以|挺好|好看|还行|对胃口|合口味|继续|就这种|这类|这个方向|有意思|想看)")
_NEGATIVE_FEEDBACK_RE = re.compile(r"(不喜欢|没意思|不好|别推|不要推|别再推|换一个|换本|讨厌|不想看|不对胃口)")
_RECOMMENDATION_QUESTION_RE = re.compile(
    r"("
    r"(?:好看|不错|可以|有意思|值得|推荐|能看).{0,8}(?:吗|嘛|么|不|呢|\?|？)"
    r"|(?:怎么样|如何|值不值得|好不好看|推不推荐|先看哪本|哪本)"
    r"|(?:第\s*[一二三四五六七八九十]|\d{1,2})\s*(?:本|个|项|部|步)?.{0,12}(?:吗|嘛|么|\?|？|怎么样|如何|好不好看|值不值得)"
    r")"
)
_INDEX_RE = re.compile(r"第\s*([一二三四五六七八九十]|\d{1,2})\s*(?:本|个|项|部)")
_GENERIC_ITEM_RE = re.compile(r"(这本|这个|这部|刚才那本|刚刚那本|那本|这种|这类)")
_EXPLICIT_NEGATIVE_RE = re.compile(r"(?:我|本人|用户)?(?:不喜欢|讨厌|不想要|不希望|不要|别再)(?P<value>[^，。！？!?]{2,80})")
_EXPLICIT_POSITIVE_RE = re.compile(r"(?:我|本人|用户)?(?:喜欢|偏好|更喜欢|爱看|想看|倾向于)(?P<value>[^，。！？!?]{2,80})")

_CN_NUMBERS = {
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}
_TITLE_PUNCT_RE = re.compile(r"[\s《》「」『』【】\[\]（）()~～?？!！,，.。:：;；·・、/_\\-]+")
_TITLE_TRAILING_VOLUME_RE = re.compile(r"\s+(?:第?\d+(?:卷|册|部|章)?|[上下])(?:\s.*)?$")


def _clean(value: Any, *, limit: int = 240) -> str:
    text = _SPACE_RE.sub(" ", str(value or "").strip())
    if len(text) <= max(1, int(limit)):
        return text
    return text[: max(1, int(limit)) - 1].rstrip() + "..."


def _compact_title_text(value: Any) -> str:
    return _TITLE_PUNCT_RE.sub("", str(value or "").strip().lower())


def _title_match_candidates(value: Any) -> list[str]:
    raw = _clean(value, limit=180)
    if not raw:
        return []
    parts = [raw]
    for sep in ("[", "【", "(", "（"):
        if sep in raw:
            parts.append(raw.split(sep, 1)[0])
    parts.append(_TITLE_TRAILING_VOLUME_RE.sub("", raw).strip())
    candidates: list[str] = []
    seen: set[str] = set()
    for part in parts:
        compact = _compact_title_text(part)
        if len(compact) >= 6 and compact not in seen:
            seen.add(compact)
            candidates.append(compact)
    return candidates


def _domain_for_text(text: str) -> str:
    return LIGHTNOVEL_DOMAIN if _LIGHTNOVEL_DOMAIN_RE.search(text) else "general"


def _looks_like_preference_query(text: str) -> bool:
    normalized = _clean(text, limit=500)
    if not normalized:
        return False
    if _QUESTION_RE.search(normalized):
        return True
    return bool(_PREFERENCE_QUERY_RE.search(normalized))


def _looks_like_recommendation_question(text: str) -> bool:
    normalized = _clean(text, limit=500)
    if not normalized:
        return False
    if _looks_like_preference_query(normalized):
        return True
    return bool(_RECOMMENDATION_QUESTION_RE.search(normalized))


def _confidence(value: float) -> float:
    return max(0.0, min(1.0, float(value or 0.0)))


def _delivery_sha256(delivery: Mapping[str, Any]) -> str:
    material = json.dumps(
        dict(delivery),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(material).hexdigest()


def _parse_index(text: str) -> int | None:
    match = _INDEX_RE.search(text)
    if not match:
        return None
    raw = str(match.group(1) or "").strip()
    if raw.isdigit():
        return max(1, int(raw))
    return _CN_NUMBERS.get(raw)


def _event(
    *,
    person_id: str,
    session_id: str,
    domain: str,
    source_kind: str,
    polarity: str,
    target_kind: str,
    target_id: str,
    target_label: str,
    confidence: float,
    attributes: Mapping[str, Any] | None = None,
    evidence_refs: list[str] | None = None,
    ts_ms: int | None = None,
) -> UserPreferenceEvent:
    return UserPreferenceEvent(
        person_id=person_id,
        session_id=session_id,
        domain=domain,
        source_kind=source_kind,
        polarity=polarity,
        target_kind=target_kind,
        target_id=target_id,
        target_label=target_label,
        confidence=_confidence(confidence),
        attributes=dict(attributes or {}),
        evidence_refs=list(evidence_refs or []),
        created_at_ms=(int(time.time() * 1000) if ts_ms is None else int(ts_ms)),
    )


def _explicit_preference_events(
    *,
    text: str,
    person_id: str,
    session_id: str,
    ts_ms: int | None,
) -> list[UserPreferenceEvent]:
    """提取明确表达的喜欢/排斥，但将偏好询问排除在学习数据之外。"""
    if not text or _looks_like_preference_query(text):
        return []
    events: list[UserPreferenceEvent] = []
    for polarity, pattern, confidence in (
        ("negative", _EXPLICIT_NEGATIVE_RE, 0.85),
        ("positive", _EXPLICIT_POSITIVE_RE, 0.9),
    ):
        match = pattern.search(text)
        if not match:
            continue
        value = _clean(match.group("value"), limit=120).strip(" ：:，,。")
        if len(value) < 2 or value in {"这本", "这个", "这种", "什么"}:
            continue
        domain = _domain_for_text(f"{text} {value}")
        events.append(
            _event(
                person_id=person_id,
                session_id=session_id,
                domain=domain,
                source_kind="explicit_preference",
                polarity=polarity,
                target_kind="trait",
                target_id=f"trait:{value}",
                target_label=value,
                confidence=confidence,
                attributes={"value": value, "keywords": [value], "raw_text": text},
                evidence_refs=[f"input://session/{session_id}/preference"],
                ts_ms=ts_ms,
            )
        )
        break
    return events


def _message_ids(payload: Mapping[str, Any]) -> set[str]:
    values = {
        _clean(payload.get("reply_to_msg_id"), limit=120),
        _clean(payload.get("msg_id"), limit=120),
    }
    raw_extra = payload.get("raw_extra")
    if isinstance(raw_extra, Mapping):
        values.add(_clean(raw_extra.get("reply_to_msg_id"), limit=120))
        values.add(_clean(raw_extra.get("msg_id"), limit=120))
    return {value for value in values if value}


def _item_matches_title(item: Mapping[str, Any], text: str) -> bool:
    normalized = _compact_title_text(text)
    for key in ("title", "target_label"):
        for title in _title_match_candidates(item.get(key)):
            if title in normalized:
                return True
    return False


def _bind_recommendation_item(
    *,
    delivery: Mapping[str, Any],
    text: str,
    payload: Mapping[str, Any],
) -> tuple[dict[str, Any] | None, str]:
    """以消息 ID、序号、标题等强度递减的证据绑定最近推荐项。

    多个标题命中或缺少可见交集时返回空项，让调用者记录集合级反馈；不凭
    “这本/这个”猜测具体作品，是防止用户长期画像被错误污染的关键分支。
    """
    items = [dict(item) for item in list(delivery.get("items") or []) if isinstance(item, Mapping)]
    if not items:
        return None, "NO_DELIVERY_ITEMS"

    incoming_ids = _message_ids(payload)
    if incoming_ids:
        for item in items:
            item_ids = {_clean(value, limit=120) for value in list(item.get("message_ids") or []) if _clean(value)}
            if item_ids.intersection(incoming_ids):
                return item, "MATCH_REPLY_MESSAGE_ID"

    index = _parse_index(text)
    if index is not None:
        for item in items:
            if int(item.get("rank") or 0) == index:
                return item, "MATCH_INDEX"
        if 1 <= index <= len(items):
            return items[index - 1], "MATCH_INDEX_POSITION"

    title_matches = [item for item in items if _item_matches_title(item, text)]
    if len(title_matches) == 1:
        return title_matches[0], "MATCH_TITLE"

    if len(items) == 1 and _GENERIC_ITEM_RE.search(text):
        return items[0], "MATCH_SINGLE_VISIBLE"

    return None, "AMBIGUOUS_RECENT_SET"


def _recommendation_feedback_events(
    *,
    text: str,
    person_id: str,
    session_id: str,
    payload: Mapping[str, Any],
    store: UserPreferenceStore,
    ts_ms: int | None,
) -> list[UserPreferenceEvent]:
    """把非提问式正负反馈关联到时间上可见的推荐投递。

    仅在已有 delivery 且有事件时间时学习，避免无上下文文本被错误归因。每个
    候选携带 delivery 摘要和 evidence refs，供后续持久化层核验其确实来自
    同一已展示推荐，而非调用期的易变检索结果。
    """
    if not text or _looks_like_preference_query(text):
        return []
    if _looks_like_recommendation_question(text):
        return []
    positive = bool(_POSITIVE_FEEDBACK_RE.search(text))
    negative = bool(_NEGATIVE_FEEDBACK_RE.search(text))
    if not positive and not negative:
        return []

    conversation_id = _clean(payload.get("conversation_id"), limit=180)
    if ts_ms is None:
        return []
    delivery = store.get_feedback_delivery(
        person_id=person_id,
        session_id=session_id,
        conversation_id=conversation_id,
        domain=LIGHTNOVEL_DOMAIN,
        admitted_at_ms=int(ts_ms),
    )
    if not delivery:
        return []

    polarity = "negative" if negative else "positive"
    item, bind_reason = _bind_recommendation_item(delivery=delivery, text=text, payload=payload)
    confidence = 0.95 if item is not None else 0.55
    source_kind = "recommendation_feedback"
    delivery_id = _clean(delivery.get("delivery_id"), limit=160)
    delivery_digest = _delivery_sha256(delivery)
    evidence_refs = [
        f"recommendation_delivery://{delivery_id}",
        f"input://session/{session_id}/recommendation_feedback",
    ]
    if item is None:
        return [
            _event(
                person_id=person_id,
                session_id=session_id,
                domain=LIGHTNOVEL_DOMAIN,
                source_kind=source_kind,
                polarity=polarity,
                target_kind="recommendation_set",
                target_id=delivery_id or "recent_lightnovel_recommendations",
                target_label="最近一次轻小说推荐集合",
                confidence=confidence,
                attributes={
                    "bind_reason": bind_reason,
                    "delivery_id": delivery_id,
                    "delivery_sha256": delivery_digest,
                    "raw_text": text,
                    "selected_titles": [_clean(row.get("title"), limit=120) for row in list(delivery.get("items") or [])],
                },
                evidence_refs=evidence_refs,
                ts_ms=ts_ms,
            )
        ]

    events = [
        _event(
            person_id=person_id,
            session_id=session_id,
            domain=LIGHTNOVEL_DOMAIN,
            source_kind=source_kind,
            polarity=polarity,
            target_kind="recommendation_item",
            target_id=_clean(item.get("target_id"), limit=220),
            target_label=_clean(item.get("title"), limit=160),
            confidence=confidence,
            attributes={
                "bind_reason": bind_reason,
                "delivery_id": delivery_id,
                "delivery_sha256": delivery_digest,
                "rank": item.get("rank"),
                "detail_url": item.get("detail_url"),
                "selection_reason": item.get("selection_reason"),
                "intro_snippet": item.get("intro_snippet"),
                "raw_text": text,
            },
            evidence_refs=evidence_refs,
            ts_ms=ts_ms,
        )
    ]
    trait_confidence = 0.65 if polarity == "positive" else 0.72
    for trait in list(item.get("traits") or [])[:6]:
        if not isinstance(trait, Mapping):
            continue
        label = _clean(trait.get("label"), limit=120)
        if not label:
            continue
        events.append(
            _event(
                person_id=person_id,
                session_id=session_id,
                domain=LIGHTNOVEL_DOMAIN,
                source_kind="recommendation_feedback_trait",
                polarity=polarity,
                target_kind="trait",
                target_id=_clean(trait.get("trait_id"), limit=140) or f"trait:{label}",
                target_label=label,
                confidence=trait_confidence,
                attributes={
                    "delivery_id": delivery_id,
                    "delivery_sha256": delivery_digest,
                    "keywords": [_clean(keyword, limit=60) for keyword in list(trait.get("keywords") or []) if _clean(keyword)],
                    "score": trait.get("score"),
                    "source_item": item.get("title"),
                    "raw_text": text,
                },
                evidence_refs=evidence_refs,
                ts_ms=ts_ms,
            )
        )
    return events


def record_user_preference_signal_if_present(
    *,
    session_id: str,
    person_id: str | None,
    user_message: str,
    payload: Mapping[str, Any] | None = None,
    ledger: Any | None = None,
    ts_ms: int | None = None,
) -> dict[str, Any] | None:
    """兼容入口：导出候选后立即写入偏好 store，供非 Acceptance 调用方使用。"""
    store = UserPreferenceStore(ledger=ledger)
    events = derive_user_preference_signal_events(
        session_id=session_id,
        person_id=person_id,
        user_message=user_message,
        payload=dict(payload or {}),
        store=store,
        ts_ms=ts_ms,
    )
    if not events:
        return None
    rows = store.append_events(events)
    if not rows:
        return None
    return {
        "event_count": len(rows),
        "domain": rows[0].get("domain") or "",
        "polarity": rows[0].get("polarity") or "",
        "target_kind": rows[0].get("target_kind") or "",
        "target_label": rows[0].get("target_label") or "",
        "source_kind": rows[0].get("source_kind") or "",
        "events": rows,
    }


def derive_user_preference_signal_events(
    *,
    session_id: str,
    person_id: str | None,
    user_message: str,
    payload: Mapping[str, Any] | None,
    store: UserPreferenceStore,
    ts_ms: int | None,
    operation_id: str = "",
) -> list[UserPreferenceEvent]:
    """无写入地导出候选，并在给定 operation 时绑定稳定事件 ID。

    先处理推荐反馈，再回退到显式偏好，避免同一句话被双重计分。传入
    ``operation_id`` 后，事件 ID 由规范化候选与索引内容寻址生成；重试不会因
    当前时间、数据库顺序或 extractor 重跑而创造新的学习事件。
    """

    text = _clean(user_message, limit=500)
    resolved_person_id = _clean(person_id, limit=120) or "unknown"
    resolved_session_id = _clean(session_id, limit=180)
    if (
        not text
        or not resolved_session_id
        or _looks_like_preference_query(text)
        or _looks_like_recommendation_question(text)
    ):
        return []
    events = _recommendation_feedback_events(
        text=text,
        person_id=resolved_person_id,
        session_id=resolved_session_id,
        payload=dict(payload or {}),
        store=store,
        ts_ms=ts_ms,
    )
    if not events:
        events = _explicit_preference_events(
            text=text,
            person_id=resolved_person_id,
            session_id=resolved_session_id,
            ts_ms=ts_ms,
        )
    operation = str(operation_id or "")
    if not operation:
        return events
    bound: list[UserPreferenceEvent] = []
    for index, event in enumerate(events):
        normalized = event.normalized()
        semantic = {
            "person_id": normalized.person_id,
            "session_id": normalized.session_id,
            "domain": normalized.domain,
            "source_kind": normalized.source_kind,
            "polarity": normalized.polarity,
            "target_kind": normalized.target_kind,
            "target_id": normalized.target_id,
            "target_label": normalized.target_label,
            "attributes": dict(normalized.attributes),
            "evidence_refs": list(normalized.evidence_refs),
            "confidence": normalized.confidence,
            "created_at_ms": normalized.created_at_ms,
            "active": normalized.active,
        }
        digest = hashlib.sha256(
            json.dumps(
                {
                    "operation_id": operation,
                    "candidate_index": index,
                    "event": semantic,
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        ).hexdigest()
        normalized.event_id = f"prefevt_{digest}"
        bound.append(normalized)
    return bound


__all__ = [
    "derive_user_preference_signal_events",
    "record_user_preference_signal_if_present",
]
