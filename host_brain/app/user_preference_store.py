"""用户偏好事件、画像与推荐上下文的应用层存储适配器。

偏好不是对话模型的瞬时猜测：事件先被规范化并持久化为可追溯事实，画像再可
从这些事件重建。隐私开关异常时一律视为未获同意；推荐上下文只提供给回答，
明确标记为不应反向写入偏好，防止“询问推荐”被学习成用户喜好。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from host_brain.persistence.context_preference_repository import PreferenceRepository

LIGHTNOVEL_DOMAIN = "lightnovel_recommendation"
GENERAL_DOMAIN = "general"
PROFILE_SCHEMA_VERSION = "Natsume.user_preference_profile.v1"
DELIVERY_SCHEMA_VERSION = "Natsume.recommendation_delivery.v1"

_SPACE_RE = re.compile(r"\s+")
_LIGHTNOVEL_QUERY_RE = re.compile(r"(小说|轻小说|书|推荐|详情|详细|内容|简介|介绍|lightnovel|epub|奇幻|后宫|异世界|恋爱|校园)", re.IGNORECASE)
_PREFERENCE_QUERY_RE = re.compile(r"(我喜欢什么|我的偏好|我的喜好|我偏好|喜欢哪些|喜欢哪类|口味|taste|preference)", re.IGNORECASE)
_RECOMMENDATION_CONTEXT_QUERY_RE = re.compile(
    r"("
    r"第\s*(?:[一二三四五六七八九十]|\d{1,2})\s*(?:本|个|项|部|步)?"
    r"|(?:这本|这部|这个|那本|那部|刚才那本|刚刚那本|刚才推荐|刚刚推荐)"
    r"|(?:哪本|先看哪|先读哪|好看吗|好不好看|怎么样|如何|值不值得|推不推荐|值得看)"
    r"|(?:详情|详细内容|详细|内容|简介|介绍|讲什么|说什么)"
    r"|(?:epub|电子书|封装|打包|制作|生成|下载|购买|买下来)"
    r")",
    re.IGNORECASE,
)
_RECOMMENDATION_INDEX_RE = re.compile(r"(?:第\s*)?([一二三四五六七八九十]|\d{1,2})\s*(?:本|个|项|部|步)")
_CN_NUMBERS = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def _now_ms() -> int:
    return int(time.time() * 1000)


def _enabled(
    name: str,
    *,
    default: bool = True,
    invalid: bool | None = None,
) -> bool:
    raw = str(os.environ.get(name) or "").strip().lower()
    if not raw:
        return bool(default)
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return bool(default if invalid is None else invalid)


def _preference_enabled() -> bool:
    """隐私配置格式错误即视为禁用，绝不把异常值解释成用户同意。"""

    return _enabled(
        "HOST_USER_PREFERENCE_ENABLED",
        default=True,
        invalid=False,
    )


def _clean_text(value: Any, *, limit: int = 240) -> str:
    text = _SPACE_RE.sub(" ", str(value or "").strip())
    if len(text) <= max(1, int(limit)):
        return text
    return text[: max(1, int(limit)) - 1].rstrip() + "..."


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _json_loads(value: Any, default: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if not isinstance(value, str) or not value.strip():
        return default
    try:
        return json.loads(value)
    except Exception:
        return default


def _stable_id(prefix: str, *parts: Any) -> str:
    digest = hashlib.sha1("|".join(_clean_text(part, limit=400) for part in parts).encode("utf-8")).hexdigest()[:16]
    return f"{prefix}_{digest}"


def _event_id() -> str:
    return f"prefevt_{uuid.uuid4().hex}"


def _normalize_domain(domain: Any) -> str:
    value = str(domain or "").strip().lower()
    return value or GENERAL_DOMAIN


@dataclass(slots=True)
class UserPreferenceEvent:
    """一条可重建用户画像的原始偏好事实及其最小溯源信息。"""
    person_id: str
    session_id: str
    domain: str
    source_kind: str
    polarity: str
    target_kind: str
    target_id: str
    target_label: str
    attributes: dict[str, Any] = field(default_factory=dict)
    evidence_refs: list[str] = field(default_factory=list)
    confidence: float = 0.0
    created_at_ms: int = field(default_factory=_now_ms)
    event_id: str = field(default_factory=_event_id)
    active: bool = True

    def normalized(self) -> "UserPreferenceEvent":
        """截断并规范字段，同时保留 Acceptance 绑定的原始事件时间。"""
        polarity = str(self.polarity or "").strip().lower()
        if polarity not in {"positive", "negative"}:
            polarity = "positive"
        confidence = max(0.0, min(1.0, float(self.confidence or 0.0)))
        return UserPreferenceEvent(
            event_id=str(self.event_id or _event_id()),
            person_id=_clean_text(self.person_id, limit=120) or "unknown",
            session_id=_clean_text(self.session_id, limit=180),
            domain=_normalize_domain(self.domain),
            source_kind=_clean_text(self.source_kind, limit=80) or "unknown",
            polarity=polarity,
            target_kind=_clean_text(self.target_kind, limit=80) or "trait",
            target_id=_clean_text(self.target_id, limit=220) or _stable_id("target", self.target_label),
            target_label=_clean_text(self.target_label, limit=240),
            attributes=dict(self.attributes or {}),
            evidence_refs=[_clean_text(item, limit=300) for item in list(self.evidence_refs or []) if _clean_text(item)],
            confidence=confidence,
            # A post-Acceptance effect binds the admitted timestamp exactly;
            # zero is a valid epoch value and must not become a replay-time
            # clock read.
            created_at_ms=max(0, int(self.created_at_ms)),
            active=bool(self.active),
        )

    def as_row(self) -> tuple[Any, ...]:
        item = self.normalized()
        return (
            item.event_id,
            item.person_id,
            item.session_id,
            item.domain,
            item.source_kind,
            item.polarity,
            item.target_kind,
            item.target_id,
            item.target_label,
            _json_dumps(item.attributes),
            _json_dumps(item.evidence_refs),
            item.confidence,
            item.created_at_ms,
            1 if item.active else 0,
        )


class UserPreferenceStore:
    """偏好事实的应用入口：通过注入 ledger 访问 canonical repository。

    本类不缓存个人资料，以使进程恢复和多 worker 均从同一 SQLite 事实源读取。
    ``signal_writes_enabled`` 仅暴露隐私判定；调用方仍须通过 Acceptance 等上游
    authority 决定某条文本是否有资格成为学习事件。
    """
    def __init__(self, *, ledger: Any | None = None) -> None:
        if ledger is None:
            raise RuntimeError("USER_PREFERENCE_LEDGER_NOT_INJECTED")
        self.ledger = ledger
        self.repository = PreferenceRepository.from_ledger(self.ledger)

    @property
    def available(self) -> bool:
        return True

    @property
    def signal_writes_enabled(self) -> bool:
        """Expose the production privacy gate used by Acceptance effects."""

        return _preference_enabled()

    def ensure_schema(self) -> None:
        self.repository.assert_schema_ready(
            require_catalog=self.repository.db_path != ":memory:"
        )

    def append_events(self, events: Sequence[UserPreferenceEvent]) -> list[dict[str, Any]]:
        """批量追加规范事件，并由 repository 原子更新可重建画像。"""
        if not _preference_enabled() or not self.available:
            return []
        normalized = [event.normalized() for event in list(events or [])]
        if not normalized:
            return []
        return self.repository.append_events(
            [self._event_to_dict(event) for event in normalized],
            profile_builder=build_profile_from_events,
            updated_at_ms=_now_ms(),
        )

    def append_event(self, event: UserPreferenceEvent) -> dict[str, Any] | None:
        rows = self.append_events([event])
        return rows[0] if rows else None

    def get_profile(self, *, person_id: str, domain: str = LIGHTNOVEL_DOMAIN) -> dict[str, Any] | None:
        if not self.available:
            return None
        return self.repository.get_profile(
            person_id=_clean_text(person_id, limit=120) or "unknown",
            domain=_normalize_domain(domain),
        )

    def get_latest_delivery(
        self,
        *,
        person_id: str | None,
        session_id: str | None = None,
        conversation_id: str | None = None,
        domain: str = LIGHTNOVEL_DOMAIN,
        created_before_ms: int | None = None,
    ) -> dict[str, Any] | None:
        if not self.available:
            return None
        return self.repository.get_latest_delivery(
            domain=_normalize_domain(domain),
            person_id=_clean_text(person_id, limit=120),
            session_id=_clean_text(session_id, limit=180),
            conversation_id=_clean_text(conversation_id, limit=180),
            created_before_ms=created_before_ms,
        )

    def get_feedback_delivery(
        self,
        *,
        person_id: str | None,
        session_id: str | None,
        conversation_id: str | None,
        domain: str,
        admitted_at_ms: int,
    ) -> dict[str, Any] | None:
        if not self.available:
            return None
        return self.repository.get_feedback_delivery(
            person_id=_clean_text(person_id, limit=120) or "unknown",
            session_id=_clean_text(session_id, limit=180),
            conversation_id=_clean_text(conversation_id, limit=180),
            domain=_normalize_domain(domain),
            admitted_at_ms=int(admitted_at_ms),
        )

    def record_delivery(
        self,
        *,
        person_id: str | None,
        session_id: str | None,
        domain: str,
        platform: str,
        conversation_id: str,
        request_id: str,
        job_id: str,
        recommendations_ref: str,
        ranked_recommendations_ref: str,
        items: Sequence[Mapping[str, Any]],
        message_payload: Mapping[str, Any] | None = None,
        created_at_ms: int | None = None,
    ) -> dict[str, Any] | None:
        """记录已展示推荐集合，为后续反馈提供时间和会话范围内的归因证据。"""
        if not _enabled("HOST_RECOMMENDATION_FEEDBACK_ENABLED", default=True) or not self.available:
            return None
        normalized_items = [
            normalize_recommendation_item(item, rank=index)
            for index, item in enumerate(list(items or []), start=1)
            if isinstance(item, Mapping)
        ]
        if not normalized_items:
            return None
        delivery = {
            "delivery_id": _stable_id(
                "recodel",
                person_id,
                session_id,
                conversation_id,
                request_id,
                job_id,
                domain,
            ),
            "person_id": _clean_text(person_id, limit=120) or "default",
            "session_id": _clean_text(session_id, limit=180),
            "domain": _normalize_domain(domain),
            "platform": _clean_text(platform, limit=40),
            "conversation_id": _clean_text(conversation_id, limit=180),
            "request_id": _clean_text(request_id, limit=160),
            "job_id": _clean_text(job_id, limit=160),
            "recommendations_ref": _clean_text(recommendations_ref, limit=400),
            "ranked_recommendations_ref": _clean_text(ranked_recommendations_ref, limit=400),
            "items": normalized_items,
            "message_payload": dict(message_payload or {}),
            "created_at_ms": int(created_at_ms or _now_ms()),
            "active": True,
        }
        return self.repository.append_delivery(delivery)

    def _event_to_dict(self, event: UserPreferenceEvent) -> dict[str, Any]:
        item = event.normalized()
        return {
            "event_id": item.event_id,
            "person_id": item.person_id,
            "session_id": item.session_id,
            "domain": item.domain,
            "source_kind": item.source_kind,
            "polarity": item.polarity,
            "target_kind": item.target_kind,
            "target_id": item.target_id,
            "target_label": item.target_label,
            "attributes": dict(item.attributes or {}),
            "evidence_refs": list(item.evidence_refs or []),
            "confidence": item.confidence,
            "created_at_ms": item.created_at_ms,
            "active": item.active,
        }

    def event_to_dict(self, event: UserPreferenceEvent) -> dict[str, Any]:
        """Return the canonical repository payload for a typed event."""

        if not isinstance(event, UserPreferenceEvent):
            raise TypeError("event must be UserPreferenceEvent")
        return self._event_to_dict(event)

def build_profile_from_events(*, person_id: str, domain: str, events: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """从全部原始事件确定性重建画像，而非就地累加不透明权重。

    正负 trait 与具体推荐项分桶，置信度取最强证据而 evidence_count/最近时间
    保留可解释性。该纯函数可被存储迁移或投影重建复用，故不读取时钟和环境。
    """
    likes: dict[str, dict[str, Any]] = {}
    dislikes: dict[str, dict[str, Any]] = {}
    liked_items: dict[str, dict[str, Any]] = {}
    disliked_items: dict[str, dict[str, Any]] = {}
    evidence_summary: list[dict[str, Any]] = []

    for event in list(events or []):
        polarity = str(event.get("polarity") or "").strip().lower()
        target_kind = str(event.get("target_kind") or "trait").strip().lower()
        target_id = str(event.get("target_id") or "").strip()
        label = _clean_text(event.get("target_label"), limit=160)
        confidence = max(0.0, min(1.0, float(event.get("confidence") or 0.0)))
        if not target_id and label:
            target_id = _stable_id(target_kind, label)
        if not target_id:
            continue
        bucket = liked_items if polarity == "positive" and target_kind == "recommendation_item" else None
        if polarity == "negative" and target_kind == "recommendation_item":
            bucket = disliked_items
        if bucket is None:
            bucket = likes if polarity == "positive" else dislikes
        item = bucket.setdefault(
            target_id,
            {
                "id": target_id,
                "label": label or target_id,
                "confidence": 0.0,
                "evidence_count": 0,
                "latest_at_ms": 0,
                "keywords": [],
                "source_kinds": [],
            },
        )
        item["confidence"] = round(max(float(item.get("confidence") or 0.0), confidence), 4)
        item["evidence_count"] = int(item.get("evidence_count") or 0) + 1
        item["latest_at_ms"] = max(int(item.get("latest_at_ms") or 0), int(event.get("created_at_ms") or 0))
        attrs = dict(event.get("attributes") or {})
        for keyword in list(attrs.get("keywords") or []):
            keyword_text = _clean_text(keyword, limit=60)
            if keyword_text and keyword_text not in item["keywords"]:
                item["keywords"].append(keyword_text)
        source_kind = _clean_text(event.get("source_kind"), limit=80)
        if source_kind and source_kind not in item["source_kinds"]:
            item["source_kinds"].append(source_kind)
        evidence_summary.append(
            {
                "event_id": str(event.get("event_id") or ""),
                "polarity": polarity,
                "target_kind": target_kind,
                "target_label": label or target_id,
                "confidence": confidence,
                "created_at_ms": int(event.get("created_at_ms") or 0),
            }
        )

    def _ordered(bucket: Mapping[str, Mapping[str, Any]], *, limit: int) -> list[dict[str, Any]]:
        return [
            dict(item)
            for item in sorted(
                bucket.values(),
                key=lambda row: (float(row.get("confidence") or 0.0), int(row.get("latest_at_ms") or 0)),
                reverse=True,
            )[:limit]
        ]

    inferred_traits = _ordered(likes, limit=16)
    return {
        "schema_version": PROFILE_SCHEMA_VERSION,
        "person_id": person_id,
        "domain": domain,
        "likes": _ordered(likes, limit=20),
        "dislikes": _ordered(dislikes, limit=20),
        "liked_items": _ordered(liked_items, limit=20),
        "disliked_items": _ordered(disliked_items, limit=20),
        "inferred_traits": inferred_traits,
        "evidence_summary": sorted(evidence_summary, key=lambda row: int(row.get("created_at_ms") or 0), reverse=True)[:30],
    }


def normalize_recommendation_item(item: Mapping[str, Any], *, rank: int) -> dict[str, Any]:
    """收窄推荐供应商数据为可留档、可反馈绑定且不含任意扩展字段的条目。"""
    title = _clean_text(item.get("title") or item.get("title_display") or item.get("canonical_title") or "未命名作品", limit=180)
    detail_url = _clean_text(item.get("detail_url") or item.get("url") or item.get("source_url"), limit=320)
    cover_url = _clean_text(item.get("cover_url") or item.get("cover") or item.get("image_url"), limit=500)
    series_key = _clean_text(item.get("series_key"), limit=220)
    item_id = series_key or _clean_text(item.get("key"), limit=220) or _stable_id("recitem", title, detail_url)
    preference_matches = [dict(match) for match in list(item.get("preference_matches") or []) if isinstance(match, Mapping)]
    traits = []
    for match in preference_matches[:8]:
        label = _clean_text(match.get("label") or match.get("group_id"), limit=120)
        if not label:
            continue
        traits.append(
            {
                "trait_id": _clean_text(match.get("group_id"), limit=120) or _stable_id("trait", label),
                "label": label,
                "keywords": [_clean_text(keyword, limit=60) for keyword in list(match.get("keywords") or []) if _clean_text(keyword)],
                "score": float(match.get("score") or 0.0),
            }
        )
    if not traits:
        bucket = _clean_text(item.get("diversity_bucket"), limit=120)
        if bucket and bucket != "other":
            traits.append({"trait_id": bucket, "label": bucket, "keywords": [bucket], "score": 0.0})
    return {
        "schema_version": DELIVERY_SCHEMA_VERSION,
        "rank": int(item.get("recommendation_rank") or rank),
        "target_id": item_id,
        "title": title,
        "detail_url": detail_url,
        "cover_url": cover_url,
        "series_key": series_key,
        "source_key": _clean_text(item.get("source_key"), limit=120),
        "selection_reason": _clean_text(item.get("selection_reason") or item.get("rationale") or item.get("reason"), limit=220),
        "intro_snippet": _clean_text(item.get("intro_snippet") or item.get("intro") or item.get("description") or item.get("summary"), limit=260),
        "score": float(item.get("recommendation_score") or item.get("recommendation_adjusted_score") or 0.0),
        "traits": traits,
        "message_ids": [_clean_text(value, limit=120) for value in list(item.get("message_ids") or []) if _clean_text(value)],
        "raw": {
            "content_access_state": _clean_text(item.get("content_access_state"), limit=60),
            "dedupe_state": _clean_text(item.get("dedupe_state"), limit=80),
            "ranking_profile_version": _clean_text(item.get("ranking_profile_version"), limit=120),
        },
    }


def _parse_recommendation_reference_index(query: str) -> int | None:
    match = _RECOMMENDATION_INDEX_RE.search(str(query or ""))
    if not match:
        return None
    raw = str(match.group(1) or "").strip()
    if raw.isdigit():
        return max(1, int(raw))
    return _CN_NUMBERS.get(raw)


def _infer_recent_recommendation_reference_index(
    *,
    store: UserPreferenceStore,
    session_id: str,
    conversation_id: str,
    current_query: str,
) -> int | None:
    clean_session = _clean_text(session_id, limit=180)
    clean_conversation = _clean_text(conversation_id, limit=180) or clean_session
    if not clean_session and not clean_conversation:
        return None
    rows: list[dict[str, Any]] = []
    for identity in dict.fromkeys((clean_session, clean_conversation)):
        if not identity:
            continue
        rows.extend(
            store.repository.recent_turns(
                session_id=identity,
                limit=16,
            )
        )
    rows.sort(
        key=lambda row: (int(row.get("ts_ms") or 0), int(row.get("turn_index") or 0)),
        reverse=True,
    )
    current = _clean_text(current_query, limit=500)
    for row in list(rows or []):
        content = _clean_text(row.get("content"), limit=500)
        if not content or content == current:
            continue
        index = _parse_recommendation_reference_index(content)
        if index is not None:
            return index
    return None


def _compact_recommendation_context_item(item: Mapping[str, Any]) -> dict[str, Any]:
    traits = []
    for trait in list(item.get("traits") or [])[:4]:
        if not isinstance(trait, Mapping):
            continue
        label = _clean_text(trait.get("label"), limit=80)
        if label:
            traits.append(label)
    return {
        "rank": int(item.get("rank") or 0),
        "title": _clean_text(item.get("title"), limit=180),
        "detail_url": _clean_text(item.get("detail_url"), limit=320),
        "selection_reason": _clean_text(item.get("selection_reason"), limit=220),
        "intro_snippet": _clean_text(item.get("intro_snippet"), limit=260),
        "traits": traits,
        "score": item.get("score"),
    }


def _build_recent_recommendation_context(
    *,
    store: UserPreferenceStore,
    person_id: str,
    session_id: str | None,
    conversation_id: str | None,
    query_text: str,
) -> dict[str, Any]:
    """为推荐追问构造有限的最近展示上下文，不把它当作偏好写入证据。"""
    query = _clean_text(query_text, limit=500)
    if not query or not _RECOMMENDATION_CONTEXT_QUERY_RE.search(query):
        return {}
    clean_session = _clean_text(session_id, limit=180)
    clean_conversation = _clean_text(conversation_id, limit=180) or clean_session
    delivery = store.get_latest_delivery(
        person_id=person_id,
        session_id=clean_session,
        conversation_id=clean_conversation,
        domain=LIGHTNOVEL_DOMAIN,
    )
    if not delivery:
        return {}
    items = [
        _compact_recommendation_context_item(item)
        for item in list(delivery.get("items") or [])
        if isinstance(item, Mapping)
    ]
    if not items:
        return {}
    reference_index = _parse_recommendation_reference_index(query)
    if reference_index is None:
        reference_index = _infer_recent_recommendation_reference_index(
            store=store,
            session_id=clean_session,
            conversation_id=clean_conversation,
            current_query=query,
        )
    referenced_item = None
    if reference_index is not None:
        referenced_item = next((item for item in items if int(item.get("rank") or 0) == reference_index), None)
        if referenced_item is None and 1 <= reference_index <= len(items):
            referenced_item = items[reference_index - 1]
    return {
        "schema_version": "Natsume.recent_recommendation_context.v1",
        "domain": LIGHTNOVEL_DOMAIN,
        "delivery_id": _clean_text(delivery.get("delivery_id"), limit=160),
        "created_at_ms": int(delivery.get("created_at_ms") or 0),
        "conversation_id": _clean_text(delivery.get("conversation_id"), limit=180),
        "items": items[:8],
        "reference_index": reference_index,
        "referenced_item": referenced_item or {},
        "answer_guidance": {
            "generate_with_model": True,
            "treat_as_followup_question": True,
            "do_not_record_as_preference": True,
            "if_referenced_item_present": "Answer about that exact ranked item from the recent recommendation set.",
            "if_no_referenced_item": "Use the recent recommendation list as context; ask a short clarification only if the reference is still ambiguous.",
        },
    }


def record_recommendation_delivery(
    *,
    person_id: str | None,
    session_id: str | None,
    domain: str,
    platform: str,
    conversation_id: str,
    request_id: str,
    job_id: str,
    recommendations_ref: str,
    ranked_recommendations_ref: str,
    items: Sequence[Mapping[str, Any]],
    message_payload: Mapping[str, Any] | None = None,
    ledger: Any | None = None,
    created_at_ms: int | None = None,
) -> dict[str, Any] | None:
    return UserPreferenceStore(ledger=ledger).record_delivery(
        person_id=person_id,
        session_id=session_id,
        domain=domain,
        platform=platform,
        conversation_id=conversation_id,
        request_id=request_id,
        job_id=job_id,
        recommendations_ref=recommendations_ref,
        ranked_recommendations_ref=ranked_recommendations_ref,
        items=items,
        message_payload=message_payload,
        created_at_ms=created_at_ms,
    )


def build_user_preference_pack(
    *,
    person_id: str | None,
    query_text: str,
    session_id: str | None = None,
    conversation_id: str | None = None,
    ledger: Any | None = None,
) -> dict[str, Any]:
    """按查询相关性向模型提供偏好事实和回答约束。

    无相关证据时返回空包，避免无端注入个人画像；有推荐追问时携带最近可见
    集合，但 guidance 明确要求将其视为上下文而非新偏好。
    """
    if not _preference_enabled():
        return {}
    clean_person = _clean_text(person_id, limit=120) or "default"
    query = _clean_text(query_text, limit=500)
    store = UserPreferenceStore(ledger=ledger)
    recent_recommendation_context = _build_recent_recommendation_context(
        store=store,
        person_id=clean_person,
        session_id=session_id,
        conversation_id=conversation_id,
        query_text=query,
    )
    query_relevant = bool(
        _PREFERENCE_QUERY_RE.search(query)
        or _LIGHTNOVEL_QUERY_RE.search(query)
        or recent_recommendation_context
    )
    domain = LIGHTNOVEL_DOMAIN if (
        _LIGHTNOVEL_QUERY_RE.search(query)
        or _PREFERENCE_QUERY_RE.search(query)
        or recent_recommendation_context
    ) else GENERAL_DOMAIN
    profile = store.get_profile(person_id=clean_person, domain=domain)
    if not profile and domain != LIGHTNOVEL_DOMAIN and query_relevant:
        profile = store.get_profile(person_id=clean_person, domain=LIGHTNOVEL_DOMAIN)
        domain = LIGHTNOVEL_DOMAIN if profile else domain
    if not profile and not query_relevant:
        return {}
    pack = {
        "schema_version": "Natsume.user_preference_pack.v1",
        "person_id": clean_person,
        "domain": domain,
        "query_relevant": query_relevant,
        "has_profile": bool(profile),
        "profile": profile or {},
        "answer_guidance": {
            "generate_with_model": True,
            "do_not_claim_unproven_preferences": True,
            "separate_confirmed_from_inferred": True,
            "if_empty": "Say naturally that there is not enough explicit preference evidence yet; ask one small calibration question or propose a tentative direction.",
        },
    }
    if recent_recommendation_context:
        pack["recent_recommendation_context"] = recent_recommendation_context
    return pack


def build_lightnovel_dynamic_taste_profile(
    base_profile: Mapping[str, Any] | None,
    *,
    person_id: str | None,
    ledger: Any | None = None,
    user_profile: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """在基础轻小说品味配置上叠加已持久化的个人偏好，保留基础配置所有权。"""
    profile = dict(base_profile or {})
    if not _preference_enabled():
        return profile
    clean_person = _clean_text(person_id, limit=120)
    if not clean_person:
        return profile
    resolved_profile = dict(user_profile or {}) if isinstance(user_profile, Mapping) else None
    if resolved_profile is None:
        if ledger is None:
            return profile
        store = UserPreferenceStore(ledger=ledger)
        resolved_profile = store.get_profile(person_id=clean_person, domain=LIGHTNOVEL_DOMAIN)
    if not resolved_profile:
        return profile

    def _coerce_groups(value: Any) -> dict[str, dict[str, Any]]:
        if isinstance(value, Mapping):
            return {
                _clean_text(key, limit=120) or _stable_id("group", index, group): dict(group)
                if isinstance(group, Mapping)
                else {"label": _clean_text(key, limit=80), "weight": 1.0, "keywords": [_clean_text(group, limit=60)]}
                for index, (key, group) in enumerate(value.items())
            }
        groups: dict[str, dict[str, Any]] = {}
        for index, group in enumerate(list(value or []), start=1):
            if isinstance(group, Mapping):
                label = _clean_text(group.get("label") or group.get("group_id") or f"base_group_{index}", limit=80)
                keywords = [_clean_text(keyword, limit=60) for keyword in list(group.get("keywords") or []) if _clean_text(keyword)]
                groups[_clean_text(group.get("group_id"), limit=120) or f"base_group_{index}"] = {
                    **dict(group),
                    "label": label,
                    "keywords": keywords,
                }
                continue
            if isinstance(group, (str, bytes)):
                keywords = [_clean_text(group, limit=60)]
            else:
                keywords = [_clean_text(keyword, limit=60) for keyword in list(group or []) if _clean_text(keyword)]
            keywords = [keyword for keyword in keywords if keyword]
            if keywords:
                groups[f"base_group_{index}"] = {"label": keywords[0], "weight": 1.0, "keywords": keywords}
        return groups

    positive_groups = _coerce_groups(profile.get("positive_keyword_groups"))
    negative_groups = _coerce_groups(profile.get("negative_keyword_groups"))

    def _keywords(entry: Mapping[str, Any]) -> list[str]:
        values = [_clean_text(item, limit=60) for item in list(entry.get("keywords") or []) if _clean_text(item)]
        label = _clean_text(entry.get("label"), limit=80)
        if label and label not in values:
            values.append(label)
        return values[:8]

    for entry in list(resolved_profile.get("likes") or [])[:12]:
        if not isinstance(entry, Mapping):
            continue
        keywords = _keywords(entry)
        if not keywords:
            continue
        confidence = float(entry.get("confidence") or 0.0)
        group_id = "user_like_" + hashlib.sha1(str(entry.get("id") or entry.get("label") or "").encode("utf-8")).hexdigest()[:10]
        positive_groups[group_id] = {
            "label": f"用户偏好：{_clean_text(entry.get('label'), limit=40)}",
            "weight": round(6.0 + confidence * 10.0, 2),
            "keywords": keywords,
        }
    for entry in list(resolved_profile.get("liked_items") or [])[:6]:
        if not isinstance(entry, Mapping):
            continue
        label = _clean_text(entry.get("label"), limit=80)
        if not label:
            continue
        group_id = "user_liked_item_" + hashlib.sha1(str(entry.get("id") or label).encode("utf-8")).hexdigest()[:10]
        positive_groups[group_id] = {
            "label": f"用户刚喜欢过：{label[:32]}",
            "weight": 5.0,
            "keywords": [label],
        }
    for entry in list(resolved_profile.get("dislikes") or [])[:12]:
        if not isinstance(entry, Mapping):
            continue
        keywords = _keywords(entry)
        if not keywords:
            continue
        confidence = float(entry.get("confidence") or 0.0)
        group_id = "user_dislike_" + hashlib.sha1(str(entry.get("id") or entry.get("label") or "").encode("utf-8")).hexdigest()[:10]
        negative_groups[group_id] = {
            "label": f"用户不喜欢：{_clean_text(entry.get('label'), limit=40)}",
            "weight": round(-8.0 - confidence * 14.0, 2),
            "keywords": keywords,
        }

    excluded_item_ids = {
        _clean_text(entry.get("id"), limit=220)
        for entry in list(resolved_profile.get("disliked_items") or [])
        if isinstance(entry, Mapping) and _clean_text(entry.get("id"), limit=220)
    }

    profile["positive_keyword_groups"] = positive_groups
    profile["negative_keyword_groups"] = negative_groups
    profile["excluded_item_ids"] = sorted(excluded_item_ids)
    profile["version"] = f"{profile.get('version') or 'custom'}+userpref-v{resolved_profile.get('version') or 1}"
    profile["user_preference_profile"] = {
        "person_id": clean_person,
        "domain": LIGHTNOVEL_DOMAIN,
        "version": resolved_profile.get("version"),
        "likes_count": len(list(resolved_profile.get("likes") or [])),
        "dislikes_count": len(list(resolved_profile.get("dislikes") or [])),
    }
    return profile


__all__ = [
    "LIGHTNOVEL_DOMAIN",
    "UserPreferenceEvent",
    "UserPreferenceStore",
    "build_lightnovel_dynamic_taste_profile",
    "build_user_preference_pack",
    "record_recommendation_delivery",
]
