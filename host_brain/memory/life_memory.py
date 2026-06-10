"""Episode-first memory store for the digital-life memory SSOT.

The first version keeps SQLite as the source of truth and treats every user
turn, perception, scheduler event, and inner stream item as a life episode with
attached evidence. Vector/semantic layers can be derived from this ledger later;
they should not be the primary author of lived memory.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
import time
import uuid
from collections.abc import Mapping, Sequence
from typing import Any

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.memory.life_memory_query import (
    DOMAIN_LIGHTNOVEL_RECOMMENDATION,
    DOMAIN_RECOMMENDATION,
    TEMPORAL_ALL_TIME,
    TEMPORAL_CURRENT,
    TEMPORAL_DATE_PREFIX,
    TEMPORAL_DAY_BEFORE_YESTERDAY,
    TEMPORAL_RECENT_DAYS,
    TEMPORAL_TODAY,
    TEMPORAL_YESTERDAY,
    plan_life_memory_query,
)


TRUTH_CANDIDATE = "CANDIDATE"
TRUTH_VERIFIED = "VERIFIED"
TRUTH_CORRECTED = "CORRECTED"
TRUTH_SUPPRESSED = "SUPPRESSED"
TRUTH_NARRATIVE_ONLY = "NARRATIVE_ONLY"

STATUS_ACTIVE = "ACTIVE"
STATUS_SUPPRESSED = "SUPPRESSED"

OPEN_LOOP_OPEN = "OPEN"
OPEN_LOOP_CLOSED = "CLOSED"
OPEN_LOOP_CANCELLED = "CANCELLED"
OPEN_LOOP_SUPERSEDED = "SUPERSEDED"
OPEN_LOOP_TERMINAL_STATES: frozenset[str] = frozenset({OPEN_LOOP_CLOSED, OPEN_LOOP_CANCELLED, OPEN_LOOP_SUPERSEDED})

EPISODE_CHAT = "chat_episode"
EPISODE_PERCEPTION = "perception_episode"
EPISODE_ACTION = "action_episode"
EPISODE_BACKGROUND = "background_episode"
EPISODE_INNER = "inner_episode"
EPISODE_REFLECTION = "reflection_episode"
EPISODE_SEMANTIC = "semantic_episode"

EVIDENCE_USER_TEXT = "user_text"
EVIDENCE_ASSISTANT_REPLY = "assistant_reply"
EVIDENCE_GATEWAY_MESSAGE = "gateway_message"
EVIDENCE_VISION_OBSERVATION = "vision_observation"
EVIDENCE_AUDIO_ASR = "audio_asr"
EVIDENCE_SCHEDULER_EVENT = "scheduler_event"
EVIDENCE_INNER_THOUGHT = "inner_thought"
EVIDENCE_TOOL_RESULT = "tool_result"
EVIDENCE_REFLECTION = "reflection"
EVIDENCE_ATTACHMENT = "attachment"

RETENTION_HOT = "HOT"
RETENTION_WARM = "WARM"
RETENTION_COLD = "COLD"
RETENTION_GONE = "GONE"

MEMORY_CLASS_AUDIT_ANCHOR = "audit_anchor"
MEMORY_CLASS_CHAT_EXPERIENCE = "chat_experience"
MEMORY_CLASS_COLLECTION_EVENT = "collection_event"
MEMORY_CLASS_COMMITMENT = "commitment"
MEMORY_CLASS_CORRECTION = "correction"
MEMORY_CLASS_FAILURE_LESSON = "failure_lesson"
MEMORY_CLASS_INNER_LIFE = "inner_life"
MEMORY_CLASS_PERCEPTION_EVIDENCE = "perception_evidence"
MEMORY_CLASS_PREFERENCE = "preference"
MEMORY_CLASS_RELATIONSHIP_FEEDBACK = "relationship_feedback"
MEMORY_CLASS_REFLECTION = "reflection"
MEMORY_CLASS_SELF_CHANGE = "self_change"
MEMORY_CLASS_STABLE_FACT = "stable_fact"
MEMORY_CLASS_TOOL_ACTION = "tool_action"
MEMORY_CLASS_TRANSIENT_NOISE = "transient_noise"

RECALL_GRAIN_AUDIT = "audit_only"
RECALL_GRAIN_EPISODE = "episode"
RECALL_GRAIN_COLLECTION = "collection"
RECALL_GRAIN_ENTITY = "entity"
RECALL_GRAIN_SEMANTIC = "semantic"

DECAY_STABLE = "STABLE"
DECAY_SLOW = "SLOW"
DECAY_NORMAL = "NORMAL"
DECAY_FAST = "FAST"
DECAY_EPHEMERAL = "EPHEMERAL"

COLLECTION_RECOMMENDATION = "recommendation_collection"
COLLECTION_SCOPE_DAILY = "daily"
COLLECTION_SCOPE_RECENT = "recent"
COLLECTION_SCOPE_DELIVERY = "delivery"
COLLECTION_SCOPE_ROLLING_PREFIX = "rolling"
COLLECTION_SCOPE_ROLLING_3D = "rolling:3d"
COLLECTION_SCOPE_ROLLING_7D = "rolling:7d"
COLLECTION_SCOPE_ROLLING_30D = "rolling:30d"
COLLECTION_ROLLING_WINDOWS: tuple[tuple[str, int, str], ...] = (
    (COLLECTION_SCOPE_ROLLING_3D, 3, "近 3 天"),
    (COLLECTION_SCOPE_ROLLING_7D, 7, "近 7 天"),
    (COLLECTION_SCOPE_ROLLING_30D, 30, "近 30 天"),
)
COLLECTION_SCOPE_RECENT_WINDOW_DAYS = 7
MS_DAY = 24 * 60 * 60 * 1000

_SYNTHETIC_METADATA_FLAGS: tuple[str, ...] = ("e2e_probe", "synthetic_probe", "test_probe")
_SYNTHETIC_METADATA_MARKERS: tuple[str, ...] = ("real_scene_batch", "scenario_batch", "entrypoint_probe")
_SYNTHETIC_ID_MARKERS: tuple[str, ...] = (
    "surface-memory-recall-e2e",
    "life-memory-real-scene-e2e",
    "life-memory-real-scene-",
    "unit-tool-matrix",
    "pytest",
    "person-test",
    "session-test",
    "-test",
    "unit-",
    "test-",
    "test_",
    "synthetic",
)
_SYNTHETIC_SOURCE_MARKERS: tuple[str, ...] = (
    "real-scene-",
    "surface-memory-recall-e2e",
    "life-memory-real-scene",
    "unit-tool-matrix",
    "pytest",
    "synthetic",
)

_SEMANTIC_PRIORITY_MEMORY_CLASSES: tuple[str, ...] = (
    MEMORY_CLASS_PREFERENCE,
    MEMORY_CLASS_COMMITMENT,
    MEMORY_CLASS_CORRECTION,
    MEMORY_CLASS_RELATIONSHIP_FEEDBACK,
    MEMORY_CLASS_SELF_CHANGE,
    MEMORY_CLASS_STABLE_FACT,
    MEMORY_CLASS_FAILURE_LESSON,
)

ENTITY_KIND_BOOK = "book"
ENTITY_KIND_ITEM = "item"

_LOCAL_MEMORY_TZ = timezone(timedelta(hours=8))

STRONG_EVIDENCE_PREFIXES: tuple[str, ...] = (
    "gateway://message/",
    "qq://message/",
    "wechat://message/",
    "chat://request/",
    "chat://turn/",
    "surface://reply/",
    "skill://exec/",
    "mcp://exec/",
    "action://event/",
    "channel://message/",
    "channel://event/",
    "vision://observation/",
    "perception://event/",
    "scheduler://event/",
    "inner_world://event/",
    "inner_activity://event/",
    "voice://event/",
    "sandbox://exec/",
    "avatar://action/",
    "clawd://action/",
)

_RECALL_FETCH_LIMIT_FACTOR = 8
_RECALL_KEYWORD_LIMIT = 18
_ANAPHORIC_SELECTION_RE = re.compile(
    r"(?:哪(?:本|部|个|一(?:本|部|个))|哪个|哪部|哪本|"
    r"(?:我问你|刚问你|问你).{0,12}哪|"
    r"(?:喜欢|想看|先看|推荐|选|挑).{0,8}哪)",
)
_ANAPHORIC_EVENT_KINDS = frozenset({EPISODE_ACTION, EPISODE_BACKGROUND, EPISODE_PERCEPTION})
_ANAPHORIC_LOW_VALUE_ACTIONS = frozenset(
    {
        "qq_reply_sent",
        "wechat_reply_sent",
        "gateway_reply_sent",
        "channel_message_sent",
        "surface_reply",
        "avatar_action",
        "clawd_action",
    }
)


@dataclass(frozen=True)
class EpisodeRecallPacket:
    """Recall output centered on episodes rather than loose text snippets."""

    query: str
    candidates: list[dict[str, Any]]

    def as_dict(self) -> dict[str, Any]:
        return {
            "packet_kind": "EpisodeRecallPacket",
            "query": self.query,
            "candidate_count": len(self.candidates),
            "candidates": list(self.candidates),
        }


def now_ts_ms() -> int:
    return int(time.time() * 1000)


def _stable_hash(value: str, *, size: int = 32) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="ignore")).hexdigest()[:size]


def _json_dumps(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _json_loads(value: Any, *, fallback: Any) -> Any:
    if value is None:
        return fallback
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return fallback
    return fallback


def _clean_text(value: Any, *, limit: int | None = None) -> str:
    text = str(value or "").strip()
    if limit is not None and limit > 0 and len(text) > limit:
        return text[: max(0, int(limit) - 1)].rstrip() + "..."
    return text


def _recall_like_terms(query: str, *, limit: int = _RECALL_KEYWORD_LIMIT) -> list[str]:
    """Extract broad lexical anchors used only to widen SQLite recall candidates."""

    value = str(query or "").strip().lower()
    if not value:
        return []
    terms: list[str] = []

    def add(term: str) -> None:
        cleaned = term.strip().lower()
        if len(cleaned) < 2 or cleaned in terms:
            return
        terms.append(cleaned)

    for token in re.findall(r"[a-z0-9_]{2,}", value):
        add(token)
    cjk_chunks = re.findall(r"[\u4e00-\u9fff]{2,}", value)
    for chunk in cjk_chunks:
        add(chunk[:12])
    for chunk in cjk_chunks:
        for size in (4, 3, 2):
            for index in range(0, max(0, len(chunk) - size + 1)):
                add(chunk[index : index + size])
                if len(terms) >= limit:
                    return terms
    return terms[: max(1, int(limit))]


def _looks_like_anaphoric_selection_query(query: str) -> bool:
    normalized = _clean_text(query)
    return bool(normalized and _ANAPHORIC_SELECTION_RE.search(normalized))


def _episode_memory_class_from_metadata(metadata: Mapping[str, Any]) -> str:
    policy = metadata.get("memory_policy")
    policy_class = policy.get("memory_class") if isinstance(policy, Mapping) else ""
    return _clean_text(metadata.get("memory_class") or policy_class).lower()


def _episode_recall_target_memory_classes(query_kind: str) -> tuple[str, ...]:
    normalized = _clean_text(query_kind).lower()
    if normalized == "failure_debug":
        return (MEMORY_CLASS_FAILURE_LESSON,)
    if normalized == "correction":
        return (MEMORY_CLASS_CORRECTION,)
    if normalized == "preference_lookup":
        return (MEMORY_CLASS_PREFERENCE, MEMORY_CLASS_RELATIONSHIP_FEEDBACK)
    if normalized == "open_loop":
        return (MEMORY_CLASS_COMMITMENT,)
    if normalized == "self_change":
        return (MEMORY_CLASS_SELF_CHANGE,)
    if normalized == "evidence_review":
        return (MEMORY_CLASS_STABLE_FACT, MEMORY_CLASS_PERCEPTION_EVIDENCE, MEMORY_CLASS_TOOL_ACTION)
    return ()


def _metadata_domain_set(metadata: Mapping[str, Any], *, text: str = "") -> set[str]:
    domains: set[str] = set()
    for key in ("domain", "recommendation_domain", "content_domain", "target_domain", "memory_domain"):
        value = _clean_text(metadata.get(key)).lower()
        if value:
            domains.add(value)
    compact = " ".join(
        _clean_text(part).lower()
        for part in (
            text,
            metadata.get("action_kind"),
            metadata.get("event_source"),
            metadata.get("task_id"),
            metadata.get("primary_ref"),
            metadata.get("evidence_refs"),
        )
    )
    if (
        any(token in compact for token in ("failure", "failed", "timeout", "异常", "报错"))
        or any(token in compact for token in ("失败原因", "任务失败", "调用失败", "执行失败", "状态=failed", "状态 failed"))
    ):
        domains.add("failure")
    if any(token in compact for token in ("lightnovel", "轻小说", "lightnovel.fun")):
        domains.update({"lightnovel_recommendation", "recommendation"})
    elif "recommendation" in compact or "推荐" in compact:
        domains.add("recommendation")
    if any(token in compact for token in ("web", "网页", "网站", "浏览器", "browser", "http://", "https://")):
        domains.update({"web", "content"})
    if any(token in compact for token in ("file", "文件", "文档", "pdf", "epub", "附件")):
        domains.update({"file", "content"})
    return domains


def _looks_like_recorded_failure_candidate(
    *,
    episode_kind: str,
    action_text: str,
    result_text: str,
    text: str,
) -> bool:
    compact = _clean_text(text, limit=1600).lower()
    return (
        result_text in {"FAILED", "ERROR", "DEGRADED", "BLOCKED"}
        or "timeout" in action_text
        or "failure" in action_text
        or "failed" in action_text
        or any(
            token in compact
            for token in (
                "timeout",
                "deepseek_timeout",
                "llm_pool_exhausted",
                "失败原因",
                "任务失败",
                "调用失败",
                "执行失败",
                "连不上",
                "被占用",
                "异常",
                "报错",
            )
        )
        or (episode_kind in {EPISODE_ACTION, EPISODE_BACKGROUND} and "failed" in compact)
    )


def _looks_like_correction_fact_candidate(text: str) -> bool:
    compact = _clean_text(text, limit=1600).lower()
    return any(token in compact for token in ("不对", "没有", "不是", "错了", "纠正", "撤回", "修正", "你说得对"))


def _looks_like_explicit_correction_event(text: str) -> bool:
    compact = _clean_text(text, limit=1600).lower()
    return any(token in compact for token in ("不对", "错了", "纠正", "撤回", "修正", "你说得对", "没有这种", "没有这本", "没有封面有猫"))


def _looks_like_correction_question_echo(text: str) -> bool:
    compact = _clean_text(text, limit=1600).lower()
    if not any(token in compact for token in ("是不是记错", "记错了吗", "有没有记错", "是否记错")):
        return False
    return any(token in compact for token in ("assistant:", "确实", "没错", "明明记得", "封面有猫"))


def _chat_summary_user_text(summary: str) -> str:
    text = _clean_text(summary, limit=1200)
    match = re.search(r"(?:^|\n)\s*User\s*[:：]\s*(.*?)(?:\n\s*Assistant\s*[:：]|\Z)", text, flags=re.DOTALL | re.IGNORECASE)
    if match:
        return _clean_text(match.group(1), limit=500)
    return ""


def _looks_like_semantic_lookup_echo(summary: str) -> bool:
    user_text = _chat_summary_user_text(summary)
    if not user_text:
        return False
    compact = re.sub(r"\s+", "", user_text).lower()
    if _looks_like_explicit_correction_event(user_text):
        return False
    if any(token in compact for token in ("记住", "以后", "不要", "别再", "必须", "应该", "优先", "改成")):
        return False
    return _semantic_statement_is_lookup_question(user_text) or _looks_like_anaphoric_selection_query(user_text)


def _looks_like_preference_candidate(text: str) -> bool:
    compact = _clean_text(text, limit=1600).lower().replace("要不要", "")
    actor_says_user = "user:" in compact or "用户" in compact
    if _semantic_looks_like_recommendation_report(compact):
        return False
    if any(token in compact for token in ("用户偏好", "用户喜欢", "用户不喜欢", "用户要求")):
        return True
    if re.search(r"user:.{0,40}我(?:不)?喜欢", compact):
        return True
    if "assistant:" not in compact and any(token in compact for token in ("不要用", "别用", "不要再", "优先使用", "应该用", "必须用")):
        return True
    if not actor_says_user:
        return False
    return any(
        token in compact
        for token in (
            "以后",
            "明确要求",
            "推荐格式",
            "推荐轻小说时",
            "格式",
            "换行",
            "分段",
            "卡片",
            "模板腔",
            "拟人化发送",
            "主链不要",
            "不要用pro",
            "不用pro",
            "优先使用",
        )
    )


def _looks_like_substantive_evidence_candidate(*, memory_class: str, action_text: str, text: str) -> bool:
    compact = _clean_text(text, limit=2200).lower()
    if _looks_like_absence_or_generic_evidence_answer(compact):
        return False
    if action_text in {"web_research", "browser_research", "file_read", "document_read", "lightnovel_recommendation_deep_review"}:
        return True
    if memory_class in {MEMORY_CLASS_STABLE_FACT, MEMORY_CLASS_PERCEPTION_EVIDENCE}:
        return True
    return any(
        token in compact
        for token in (
            "网页主题",
            "正文内容",
            "报告正文",
            "content_excerpt",
            "body_ref",
            "doc://web",
            "阅读了一个网页",
            "打开详情页",
            "工具结果",
            "截图观察",
            "ocr",
        )
    )


def _looks_like_absence_or_generic_evidence_answer(text: str) -> bool:
    compact = _clean_text(text, limit=1600).lower()
    return any(
        token in compact
        for token in (
            "没有看到",
            "没有拿到",
            "没有对应",
            "没有具体内容",
            "没法直接判断",
            "不能直接判断",
            "一般判断依据",
            "建议再交叉验证",
        )
    )


def _episode_metadata_dict(episode: Mapping[str, Any]) -> dict[str, Any]:
    metadata = _json_loads(episode.get("metadata"), fallback={})
    return dict(metadata) if isinstance(metadata, Mapping) else {}


def _has_any_marker(value: Any, markers: Sequence[str]) -> bool:
    text = _clean_text(value, limit=1200).lower()
    return bool(text) and any(marker in text for marker in markers)


def _is_synthetic_life_record(record: Mapping[str, Any], metadata: Mapping[str, Any] | None = None) -> bool:
    meta = dict(metadata) if isinstance(metadata, Mapping) else _episode_metadata_dict(record)
    if any(bool(meta.get(flag)) for flag in _SYNTHETIC_METADATA_FLAGS):
        return True
    if any(meta.get(marker) not in (None, "", False) for marker in _SYNTHETIC_METADATA_MARKERS):
        return True
    identity_values = (
        record.get("person_id"),
        record.get("session_id"),
        meta.get("person_id"),
        meta.get("session_id"),
        meta.get("conversation_id"),
    )
    if any(_has_any_marker(value, (*_SYNTHETIC_ID_MARKERS, "probe")) for value in identity_values):
        return True
    if _has_any_marker(record.get("thread_id"), _SYNTHETIC_ID_MARKERS):
        return True
    source_values = (
        record.get("primary_ref"),
        record.get("source_ref"),
        record.get("evidence_ref"),
        record.get("request_id"),
        meta.get("source_ref"),
        meta.get("action_id"),
        meta.get("request_id"),
    )
    if any(_has_any_marker(value, _SYNTHETIC_SOURCE_MARKERS) for value in source_values):
        return True
    refs = meta.get("evidence_refs")
    if isinstance(refs, Sequence) and not isinstance(refs, (str, bytes, bytearray)):
        return any(_has_any_marker(ref, _SYNTHETIC_SOURCE_MARKERS) for ref in refs)
    return False


def _allow_synthetic_life_recall_for_scope(*, person_id: str | None, session_id: str | None) -> bool:
    return any(
        _has_any_marker(value, _SYNTHETIC_ID_MARKERS)
        for value in (person_id, session_id)
        if _clean_text(value)
    )


def _session_allows_cross_scope_recall(session_id: str | None) -> bool:
    session = _clean_text(session_id).lower()
    if not session:
        return True
    return any(
        marker in session
        for marker in (
            "scheduler",
            "daily",
            "diary",
            "background",
            "inner",
            "codex",
            "task",
        )
    )


def _record_matches_life_recall_scope(
    record: Mapping[str, Any],
    metadata: Mapping[str, Any] | None,
    *,
    person_id: str | None,
    session_id: str | None,
    allow_synthetic: bool,
) -> bool:
    meta = dict(metadata) if isinstance(metadata, Mapping) else {}
    if _is_synthetic_life_record(record, meta) and not allow_synthetic:
        return False
    target_person = _clean_text(person_id)
    target_session = _clean_text(session_id)
    if not target_person and not target_session:
        return True
    row_person = _clean_text(record.get("person_id") or meta.get("person_id"))
    row_session = _clean_text(record.get("session_id") or meta.get("session_id") or meta.get("conversation_id"))
    aliases = set(_owner_alias_person_ids(target_person)) if target_person else set()
    if target_session and row_session and row_session != target_session:
        same_person_cross_scope = bool(target_person and row_person in aliases and _session_allows_cross_scope_recall(row_session))
        if not same_person_cross_scope:
            return False
    if target_person and row_person and row_person not in aliases:
        same_session = bool(target_session and row_session == target_session)
        if not same_session:
            return False
    return True


def _is_significant_anaphoric_episode(episode: Mapping[str, Any]) -> bool:
    kind = _clean_text(episode.get("episode_kind"))
    if kind not in _ANAPHORIC_EVENT_KINDS:
        return False
    metadata = _episode_metadata_dict(episode)
    if bool(metadata.get("anchor_only")):
        return False
    action = _clean_text(episode.get("action") or metadata.get("action_kind")).lower()
    if action in _ANAPHORIC_LOW_VALUE_ACTIONS:
        return False
    summary = _clean_text(episode.get("summary"))
    if not summary:
        return False
    return True


def _clamp_unit(value: Any, *, default: float = 0.0) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return float(default)
    return max(0.0, min(1.0, numeric))


def _coerce_int(value: Any, *, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _metadata_float(metadata: Mapping[str, Any] | None, key: str, *, default: float = 0.0) -> float:
    if not isinstance(metadata, Mapping):
        return float(default)
    return _clamp_unit(metadata.get(key), default=default)


def _importance_keyword_boost(text: str) -> float:
    normalized = str(text or "").lower()
    if not normalized:
        return 0.0
    boost = 0.0
    if any(token in normalized for token in ("记住", "以后", "不要", "别再", "偏好", "喜欢", "讨厌", "remember", "preference")):
        boost += 0.16
    if any(token in normalized for token in ("承诺", "待办", "继续", "必须", "open loop", "todo")):
        boost += 0.12
    if any(token in normalized for token in ("纠正", "不对", "错了", "失败", "连不上", "被占用", "修复", "原因")):
        boost += 0.10
    if _semantic_is_relationship_feedback(normalized):
        boost += 0.12
    if any(token in normalized for token in ("模型路由", "记忆系统", "能力", "策略", "自我改变", "self_change")):
        boost += 0.12
    return min(0.35, boost)


def _decay_profile_for_episode(
    *,
    episode_kind: str,
    salience: float,
    metadata: Mapping[str, Any] | None,
) -> str:
    explicit = _clean_text((metadata or {}).get("decay_profile_id") if isinstance(metadata, Mapping) else None).upper()
    if explicit in {DECAY_STABLE, DECAY_SLOW, DECAY_NORMAL, DECAY_FAST, DECAY_EPHEMERAL}:
        return explicit
    if _metadata_float(metadata, "promise_weight") > 0.0 or _metadata_float(metadata, "user_importance") >= 0.4:
        return DECAY_STABLE
    if episode_kind in {EPISODE_CHAT, EPISODE_ACTION, EPISODE_PERCEPTION} and salience >= 0.5:
        return DECAY_SLOW
    if episode_kind == EPISODE_INNER:
        return DECAY_FAST
    return DECAY_NORMAL


def _compute_memory_strength(
    *,
    salience: float,
    episode_kind: str,
    summary: str | None,
    metadata: Mapping[str, Any] | None,
    existing_strength: Any = None,
) -> float:
    base = _clamp_unit(existing_strength, default=-1.0)
    if existing_strength is None:
        base = _clamp_unit(salience)
    strength = max(base, _clamp_unit(salience))
    strength += _metadata_float(metadata, "emotional_weight")
    strength += _metadata_float(metadata, "user_importance")
    strength += _metadata_float(metadata, "promise_weight")
    strength += _metadata_float(metadata, "correction_weight")
    strength += _metadata_float(metadata, "semantic_reference_weight")
    strength += _importance_keyword_boost(str(summary or ""))
    if episode_kind in {EPISODE_CHAT, EPISODE_ACTION, EPISODE_PERCEPTION}:
        strength += 0.04
    strength -= _metadata_float(metadata, "duplicate_penalty")
    strength -= _metadata_float(metadata, "triviality_penalty")
    if bool((metadata or {}).get("anchor_only")):
        strength -= 0.10
    return _clamp_unit(strength)


def _retention_state_for_strength(
    strength: float,
    *,
    truth_state: str | None = None,
    explicit: str | None = None,
) -> str:
    normalized = _clean_text(explicit).upper()
    if normalized in {RETENTION_HOT, RETENTION_WARM, RETENTION_COLD, RETENTION_GONE}:
        return normalized
    truth = _clean_text(truth_state).upper()
    if truth in {TRUTH_SUPPRESSED, TRUTH_CORRECTED}:
        return RETENTION_COLD
    value = _clamp_unit(strength)
    if value >= 0.75:
        return RETENTION_HOT
    if value >= 0.35:
        return RETENTION_WARM
    if value >= 0.10:
        return RETENTION_COLD
    return RETENTION_GONE


def _classify_life_memory_policy(
    *,
    episode_kind: str,
    action: str | None,
    result: str | None,
    summary: str | None,
    metadata: Mapping[str, Any] | None,
    evidence_refs: Sequence[Any] | None,
    truth_state: str | None,
) -> dict[str, Any]:
    """Classify an episode for memory management, not just storage.

    Episode is the SSOT for "what happened"; this policy says how that
    experience should be managed later: active recall, semantic distillation,
    sleep replay, and eventual forgetting.
    """

    meta = dict(metadata or {})
    kind = _clean_text(episode_kind) or EPISODE_CHAT
    action_text = _clean_text(action or meta.get("action_kind") or meta.get("task_id")).lower()
    result_text = _clean_text(result or meta.get("status") or meta.get("result_status")).lower()
    source_text = _clean_text(meta.get("event_source") or meta.get("source")).lower()
    summary_text = _clean_text(summary or meta.get("detail") or meta.get("note"), limit=1200)
    compact = re.sub(r"\s+", "", summary_text).lower()
    truth = _clean_text(truth_state).upper()
    refs = _normalize_refs(evidence_refs or meta.get("evidence_refs") or [])
    reason_codes: list[str] = []
    trace_meta = meta.get("trace_stage_meta") if isinstance(meta.get("trace_stage_meta"), Mapping) else {}
    reply_source = _clean_text(meta.get("reply_source") or trace_meta.get("reply_source")).lower()
    lookup_echo = (
        kind == EPISODE_CHAT
        and reply_source != "surface_memory_direct_answer"
        and _looks_like_semantic_lookup_echo(summary_text)
    )

    def has_any(*tokens: str) -> bool:
        return any(token and token in compact for token in tokens)

    memory_class = MEMORY_CLASS_CHAT_EXPERIENCE
    recall_grain = RECALL_GRAIN_EPISODE
    decay_profile = DECAY_NORMAL
    active_recall_default = True
    semantic_candidate = False
    sleep_replay_candidate = False
    failure_like = _semantic_is_failure_lesson(compact, action=action_text) or result_text in {"failed", "error", "degraded", "blocked"}

    if bool(meta.get("anchor_only")) or action_text == "audit_anchor" or "audit_anchor" in action_text:
        memory_class = MEMORY_CLASS_AUDIT_ANCHOR
        recall_grain = RECALL_GRAIN_AUDIT
        decay_profile = DECAY_EPHEMERAL
        active_recall_default = False
        reason_codes.append("audit_anchor_only")
    elif truth in {TRUTH_CORRECTED, TRUTH_SUPPRESSED}:
        memory_class = MEMORY_CLASS_CORRECTION
        recall_grain = RECALL_GRAIN_AUDIT
        decay_profile = DECAY_SLOW
        active_recall_default = False
        semantic_candidate = False
        sleep_replay_candidate = False
        reason_codes.append("corrected_or_suppressed_source")
    elif _looks_like_explicit_correction_event(summary_text):
        memory_class = MEMORY_CLASS_CORRECTION
        recall_grain = RECALL_GRAIN_EPISODE
        decay_profile = DECAY_SLOW
        semantic_candidate = True
        sleep_replay_candidate = True
        reason_codes.append("correction_or_suppression")
    elif not lookup_echo and _semantic_is_relationship_feedback(compact):
        memory_class = MEMORY_CLASS_RELATIONSHIP_FEEDBACK
        recall_grain = RECALL_GRAIN_SEMANTIC
        decay_profile = DECAY_STABLE
        semantic_candidate = True
        sleep_replay_candidate = True
        reason_codes.append("relationship_or_quality_feedback")
    elif kind == EPISODE_INNER or source_text in {"inner_world", "inner_stream", "inner_activity"}:
        memory_class = MEMORY_CLASS_INNER_LIFE
        recall_grain = RECALL_GRAIN_EPISODE
        decay_profile = DECAY_FAST
        semantic_candidate = bool(meta.get("should_distill_to_memory"))
        sleep_replay_candidate = semantic_candidate
        reason_codes.append("inner_life")
    elif kind == EPISODE_REFLECTION or action_text == "memory_compaction":
        memory_class = MEMORY_CLASS_REFLECTION
        recall_grain = RECALL_GRAIN_EPISODE
        decay_profile = DECAY_SLOW
        active_recall_default = True
        reason_codes.append("reflection_or_compaction")
    elif not lookup_echo and failure_like:
        memory_class = MEMORY_CLASS_FAILURE_LESSON
        recall_grain = RECALL_GRAIN_SEMANTIC
        decay_profile = DECAY_SLOW
        semantic_candidate = True
        sleep_replay_candidate = True
        reason_codes.append("failure_lesson")
    elif not lookup_echo and _semantic_is_commitment(compact):
        memory_class = MEMORY_CLASS_COMMITMENT
        recall_grain = RECALL_GRAIN_SEMANTIC
        decay_profile = DECAY_STABLE
        semantic_candidate = True
        sleep_replay_candidate = True
        reason_codes.append("commitment_or_open_loop")
    elif not lookup_echo and _semantic_is_self_change(compact, action=action_text):
        memory_class = MEMORY_CLASS_SELF_CHANGE
        recall_grain = RECALL_GRAIN_SEMANTIC
        decay_profile = DECAY_STABLE
        semantic_candidate = True
        sleep_replay_candidate = True
        reason_codes.append("self_change")
    elif not lookup_echo and _semantic_is_user_preference(compact):
        memory_class = MEMORY_CLASS_PREFERENCE
        recall_grain = RECALL_GRAIN_SEMANTIC
        decay_profile = DECAY_STABLE
        semantic_candidate = True
        sleep_replay_candidate = True
        reason_codes.append("user_preference")
    elif not lookup_echo and _semantic_is_stable_fact(compact):
        memory_class = MEMORY_CLASS_STABLE_FACT
        recall_grain = RECALL_GRAIN_SEMANTIC
        decay_profile = DECAY_STABLE
        semantic_candidate = True
        sleep_replay_candidate = True
        reason_codes.append("stable_fact")
    elif kind == EPISODE_PERCEPTION or source_text in {"vision", "audio", "asr", "perception"}:
        memory_class = MEMORY_CLASS_PERCEPTION_EVIDENCE
        recall_grain = RECALL_GRAIN_EPISODE
        decay_profile = DECAY_SLOW
        reason_codes.append("perception_evidence")
    elif action_text in {"lightnovel_recommendation_delivery", "recommendation_delivery"} or bool(meta.get("selected_recommendations")):
        memory_class = MEMORY_CLASS_COLLECTION_EVENT
        recall_grain = RECALL_GRAIN_COLLECTION
        decay_profile = DECAY_SLOW
        active_recall_default = True
        reason_codes.append("collection_event")
    elif kind in {EPISODE_ACTION, EPISODE_BACKGROUND} or source_text in {"skill", "tool", "browser", "mcp", "sandbox", "scheduler", "codex_production"}:
        memory_class = MEMORY_CLASS_TOOL_ACTION
        recall_grain = RECALL_GRAIN_EPISODE
        decay_profile = DECAY_NORMAL
        reason_codes.append("action_or_background")

    if bool(meta.get("triviality_penalty")) or has_any("任务仍在运行", "没有新变化", "健康检查", "heartbeat", "ping"):
        if memory_class not in {
            MEMORY_CLASS_AUDIT_ANCHOR,
            MEMORY_CLASS_PREFERENCE,
            MEMORY_CLASS_COMMITMENT,
            MEMORY_CLASS_CORRECTION,
            MEMORY_CLASS_FAILURE_LESSON,
            MEMORY_CLASS_SELF_CHANGE,
            MEMORY_CLASS_STABLE_FACT,
        }:
            memory_class = MEMORY_CLASS_TRANSIENT_NOISE
            recall_grain = RECALL_GRAIN_AUDIT if not refs else RECALL_GRAIN_EPISODE
            decay_profile = DECAY_EPHEMERAL
            active_recall_default = False
            semantic_candidate = False
            sleep_replay_candidate = False
            reason_codes.append("transient_low_value")

    return {
        "schema": "Natsume.life_memory_policy.v1",
        "memory_class": memory_class,
        "recall_grain": recall_grain,
        "decay_profile_id": decay_profile,
        "active_recall_default": bool(active_recall_default),
        "semantic_candidate": bool(semantic_candidate),
        "sleep_replay_candidate": bool(sleep_replay_candidate),
        "reason_codes": list(dict.fromkeys(reason_codes or ["default_chat_experience"])),
    }


def _with_memory_policy_metadata(
    metadata: Mapping[str, Any] | None,
    *,
    policy: Mapping[str, Any],
    explicit_decay_profile_id: str | None,
) -> dict[str, Any]:
    payload = dict(metadata or {})
    policy_payload = dict(policy)
    existing_policy = payload.get("memory_policy")
    if not isinstance(existing_policy, Mapping):
        existing_policy = {}
    payload["memory_policy"] = {**dict(existing_policy), **policy_payload}
    if _clean_text(policy_payload.get("memory_class")):
        payload["memory_class"] = policy_payload.get("memory_class")
    if _clean_text(policy_payload.get("recall_grain")):
        payload["recall_grain"] = policy_payload.get("recall_grain")
    if not _clean_text(explicit_decay_profile_id) and not _clean_text(payload.get("decay_profile_id")):
        payload["decay_profile_id"] = _clean_text(policy_payload.get("decay_profile_id")) or DECAY_NORMAL
    return payload


def _decay_rate_per_day(decay_profile_id: str | None) -> float:
    profile = _clean_text(decay_profile_id).upper()
    if profile == DECAY_STABLE:
        return 0.001
    if profile == DECAY_SLOW:
        return 0.003
    if profile == DECAY_FAST:
        return 0.025
    if profile == DECAY_EPHEMERAL:
        return 0.080
    return 0.008


def _normalize_refs(evidence_refs: Sequence[Any] | None) -> list[str]:
    refs: list[str] = []
    seen: set[str] = set()
    for item in list(evidence_refs or []):
        ref = str(item or "").strip()
        if not ref or ref in seen:
            continue
        seen.add(ref)
        refs.append(ref)
    return refs


def strong_evidence_refs(evidence_refs: Sequence[Any] | None) -> list[str]:
    refs = _normalize_refs(evidence_refs)
    strong = [
        ref
        for ref in refs
        if any(ref.startswith(prefix) for prefix in STRONG_EVIDENCE_PREFIXES)
    ]
    return sorted(strong)


def derive_episode_identity_key(
    evidence_refs: Sequence[Any] | None,
    *,
    fallback_seed: str | None = None,
) -> str | None:
    strong = strong_evidence_refs(evidence_refs)
    if strong:
        return strong[0]
    seed = _clean_text(fallback_seed)
    return seed or None


def derive_episode_id(
    evidence_refs: Sequence[Any] | None,
    *,
    fallback_seed: str | None = None,
) -> str:
    key = derive_episode_identity_key(evidence_refs, fallback_seed=fallback_seed)
    if key:
        return f"ep_{_stable_hash('life_episode:v1:' + key, size=32)}"
    return f"ep_tmp_{uuid.uuid4().hex}"


def derive_evidence_id(
    *,
    episode_id: str,
    evidence_kind: str,
    source_ref: str | None = None,
    content: str | None = None,
    payload: Mapping[str, Any] | None = None,
) -> str:
    source = _clean_text(source_ref)
    if source:
        key = f"{episode_id}|{evidence_kind}|{source}"
    else:
        payload_digest = _stable_hash(_json_dumps(payload or {}) or "{}", size=24)
        key = f"{episode_id}|{evidence_kind}|{_clean_text(content, limit=300)}|{payload_digest}"
    return f"ev_{_stable_hash('life_evidence:v1:' + key, size=32)}"


def _episode_truth_from_refs(evidence_refs: Sequence[Any] | None) -> str:
    return TRUTH_VERIFIED if strong_evidence_refs(evidence_refs) else TRUTH_CANDIDATE


def _merge_metadata(existing: Any, incoming: Mapping[str, Any] | None) -> dict[str, Any]:
    base = _json_loads(existing, fallback={})
    if not isinstance(base, dict):
        base = {}
    merged = dict(base)
    for key, value in dict(incoming or {}).items():
        if value is not None:
            merged[str(key)] = value
    return merged


class LifeMemoryStore:
    """SQLite-backed life episode store."""

    def __init__(self, *, ledger: SQLiteLedger) -> None:
        self.ledger = ledger

    def upsert_episode(
        self,
        *,
        episode_kind: str,
        evidence_refs: Sequence[Any] | None = None,
        episode_id: str | None = None,
        thread_id: str | None = None,
        person_id: str | None = None,
        session_id: str | None = None,
        actor_id: str | None = None,
        intent: str | None = None,
        action: str | None = None,
        result: str | None = None,
        primary_ref: str | None = None,
        summary: str | None = None,
        salience: float = 0.0,
        memory_strength: float | None = None,
        decay_profile_id: str | None = None,
        retention_state: str | None = None,
        retention_until_ts_ms: int | None = None,
        forget_reason: str | None = None,
        truth_state: str | None = None,
        status: str = STATUS_ACTIVE,
        ts_ms: int | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        refs = _normalize_refs(evidence_refs)
        primary = _clean_text(primary_ref) or (strong_evidence_refs(refs) or refs or [None])[0]
        created = int(ts_ms if ts_ms is not None else now_ts_ms())
        eid = _clean_text(episode_id) or derive_episode_id(
            refs,
            fallback_seed=f"{episode_kind}:{thread_id or session_id or person_id or created}:{primary or summary or ''}",
        )
        truth = _clean_text(truth_state) or _episode_truth_from_refs(refs)
        kind = _clean_text(episode_kind) or EPISODE_CHAT
        metadata_payload = dict(metadata or {})
        if refs:
            metadata_payload.setdefault("evidence_refs", refs)
        summary_text = _clean_text(summary, limit=1200)
        policy = _classify_life_memory_policy(
            episode_kind=kind,
            action=action,
            result=result,
            summary=summary_text,
            metadata=metadata_payload,
            evidence_refs=refs,
            truth_state=truth,
        )
        metadata_payload = _with_memory_policy_metadata(
            metadata_payload,
            policy=policy,
            explicit_decay_profile_id=decay_profile_id,
        )
        base_salience = _clamp_unit(salience)
        decay_metadata = dict(metadata_payload)
        if _clean_text(decay_profile_id):
            decay_metadata["decay_profile_id"] = _clean_text(decay_profile_id)
        decay_profile = _decay_profile_for_episode(
            episode_kind=kind,
            salience=base_salience,
            metadata=decay_metadata,
        )
        computed_strength = _compute_memory_strength(
            salience=base_salience,
            episode_kind=kind,
            summary=summary_text,
            metadata=metadata_payload,
            existing_strength=memory_strength,
        )
        computed_retention = _retention_state_for_strength(
            computed_strength,
            truth_state=truth,
            explicit=retention_state,
        )

        with self.ledger._write_lock:
            with self.ledger._conn:
                existing = self.ledger._conn.execute(
                    "SELECT * FROM life_episode WHERE episode_id = ?",
                    (eid,),
                ).fetchone()
                if existing is None:
                    self.ledger._conn.execute(
                        """
                        INSERT INTO life_episode (
                            episode_id, episode_kind, thread_id, person_id, session_id,
                            actor_id, intent, action, result,
                            primary_ref, summary, salience, base_salience,
                            memory_strength, decay_profile_id, retention_state,
                            last_recalled_ts_ms, recall_count, reinforcement_count,
                            compressed_into_episode_id, retention_until_ts_ms, forget_reason,
                            truth_state, status,
                            created_ts_ms, last_touched_ts_ms, metadata
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            eid,
                            kind,
                            _clean_text(thread_id) or None,
                            _clean_text(person_id) or None,
                            _clean_text(session_id) or None,
                            _clean_text(actor_id) or None,
                            _clean_text(intent) or None,
                            _clean_text(action) or None,
                            _clean_text(result) or None,
                            primary,
                            summary_text,
                            base_salience,
                            base_salience,
                            computed_strength,
                            decay_profile,
                            computed_retention,
                            None,
                            0,
                            0,
                            None,
                            int(retention_until_ts_ms) if retention_until_ts_ms is not None else None,
                            _clean_text(forget_reason) or None,
                            truth,
                            _clean_text(status) or STATUS_ACTIVE,
                            created,
                            created,
                            _json_dumps(metadata_payload or {}),
                        ),
                    )
                else:
                    current = dict(existing)
                    current_truth = _clean_text(current.get("truth_state")) or TRUTH_CANDIDATE
                    if current_truth in {TRUTH_SUPPRESSED, TRUTH_CORRECTED} and truth not in {TRUTH_SUPPRESSED, TRUTH_CORRECTED}:
                        next_truth = current_truth
                    else:
                        next_truth = truth or current_truth
                    next_summary = summary_text or current.get("summary")
                    next_metadata = _merge_metadata(current.get("metadata"), metadata_payload)
                    next_policy = _classify_life_memory_policy(
                        episode_kind=kind,
                        action=_clean_text(action) or current.get("action"),
                        result=_clean_text(result) or current.get("result"),
                        summary=str(next_summary or ""),
                        metadata=next_metadata,
                        evidence_refs=refs or next_metadata.get("evidence_refs") or [],
                        truth_state=next_truth,
                    )
                    next_metadata = _with_memory_policy_metadata(
                        next_metadata,
                        policy=next_policy,
                        explicit_decay_profile_id=decay_profile_id,
                    )
                    next_decay_metadata = dict(next_metadata)
                    if _clean_text(decay_profile_id):
                        next_decay_metadata["decay_profile_id"] = _clean_text(decay_profile_id)
                    next_decay_profile = _decay_profile_for_episode(
                        episode_kind=kind,
                        salience=max(_clamp_unit(current.get("salience")), base_salience),
                        metadata=next_decay_metadata,
                    )
                    next_strength = _compute_memory_strength(
                        salience=max(_clamp_unit(current.get("salience")), base_salience),
                        episode_kind=kind,
                        summary=str(next_summary or ""),
                        metadata=next_metadata,
                        existing_strength=max(
                            _clamp_unit(current.get("memory_strength")),
                            computed_strength,
                        ),
                    )
                    next_retention = _retention_state_for_strength(
                        next_strength,
                        truth_state=next_truth,
                        explicit=retention_state,
                    )
                    self.ledger._conn.execute(
                        """
                        UPDATE life_episode
                        SET
                            episode_kind = COALESCE(NULLIF(?, ''), episode_kind),
                            thread_id = COALESCE(?, thread_id),
                            person_id = COALESCE(?, person_id),
                            session_id = COALESCE(?, session_id),
                            actor_id = COALESCE(?, actor_id),
                            intent = COALESCE(?, intent),
                            action = COALESCE(?, action),
                            result = COALESCE(?, result),
                            primary_ref = COALESCE(?, primary_ref),
                            summary = COALESCE(?, summary),
                            salience = MAX(COALESCE(salience, 0.0), ?),
                            base_salience = MAX(COALESCE(base_salience, 0.0), ?),
                            memory_strength = MAX(COALESCE(memory_strength, 0.0), ?),
                            decay_profile_id = COALESCE(NULLIF(?, ''), decay_profile_id),
                            retention_state = ?,
                            retention_until_ts_ms = COALESCE(?, retention_until_ts_ms),
                            forget_reason = COALESCE(?, forget_reason),
                            truth_state = ?,
                            status = COALESCE(NULLIF(?, ''), status),
                            last_touched_ts_ms = MAX(COALESCE(last_touched_ts_ms, 0), ?),
                            metadata = ?
                        WHERE episode_id = ?
                        """,
                        (
                            kind,
                            _clean_text(thread_id) or None,
                            _clean_text(person_id) or None,
                            _clean_text(session_id) or None,
                            _clean_text(actor_id) or None,
                            _clean_text(intent) or None,
                            _clean_text(action) or None,
                            _clean_text(result) or None,
                            primary,
                            next_summary,
                            base_salience,
                            base_salience,
                            next_strength,
                            next_decay_profile,
                            next_retention,
                            int(retention_until_ts_ms) if retention_until_ts_ms is not None else None,
                            _clean_text(forget_reason) or None,
                            next_truth,
                            _clean_text(status) or STATUS_ACTIVE,
                            created,
                            _json_dumps(next_metadata),
                            eid,
                        ),
                    )
        return {
            "status": "OK",
            "episode_id": eid,
            "episode_kind": kind,
            "truth_state": truth,
            "retention_state": computed_retention,
            "memory_strength": computed_strength,
            "primary_ref": primary,
        }

    def attach_evidence(
        self,
        *,
        episode_id: str,
        evidence_kind: str,
        source_ref: str | None = None,
        request_id: str | None = None,
        content: str | None = None,
        payload: Mapping[str, Any] | None = None,
        trust_level: str = TRUTH_CANDIDATE,
        ts_ms: int | None = None,
        evidence_id: str | None = None,
    ) -> dict[str, Any]:
        eid = _clean_text(episode_id)
        if not eid:
            raise ValueError("episode_id is required")
        kind = _clean_text(evidence_kind) or EVIDENCE_TOOL_RESULT
        source = _clean_text(source_ref) or None
        created = int(ts_ms if ts_ms is not None else now_ts_ms())
        ev_id = _clean_text(evidence_id) or derive_evidence_id(
            episode_id=eid,
            evidence_kind=kind,
            source_ref=source,
            content=content,
            payload=payload,
        )
        with self.ledger._write_lock:
            with self.ledger._conn:
                self.ledger._conn.execute(
                    """
                    INSERT OR IGNORE INTO life_evidence (
                        evidence_id, episode_id, evidence_kind, source_ref, request_id,
                        content, payload, trust_level, created_ts_ms
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        ev_id,
                        eid,
                        kind,
                        source,
                        _clean_text(request_id) or None,
                        _clean_text(content, limit=4000),
                        _json_dumps(dict(payload or {})),
                        _clean_text(trust_level) or TRUTH_CANDIDATE,
                        created,
                    ),
                )
                self.ledger._conn.execute(
                    """
                    UPDATE life_episode
                    SET last_touched_ts_ms = MAX(COALESCE(last_touched_ts_ms, 0), ?)
                    WHERE episode_id = ?
                    """,
                    (created, eid),
                )
        return {
            "status": "OK",
            "episode_id": eid,
            "evidence_id": ev_id,
            "evidence_kind": kind,
            "source_ref": source,
        }

    def upsert_open_loop(
        self,
        *,
        semantic_key: str,
        episode_id: str,
        status: str = OPEN_LOOP_OPEN,
        title: str | None = None,
        detail: str | None = None,
        person_id: str | None = None,
        session_id: str | None = None,
        source_ref: str | None = None,
        close_reason: str | None = None,
        salience: float = 0.0,
        memory_strength: float = 0.0,
        ts_ms: int | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Track the lifecycle of promises/todos derived from lived episodes."""

        key = _clean_text(semantic_key, limit=300)
        eid = _clean_text(episode_id)
        if not key or not eid:
            raise ValueError("semantic_key and episode_id are required")
        state = _normalize_open_loop_status(status)
        created = int(ts_ms if ts_ms is not None else now_ts_ms())
        open_loop_id = f"ol_{_stable_hash('life_open_loop:v1:' + key, size=28)}"
        metadata_payload = dict(metadata or {})
        metadata_payload.setdefault("semantic_key", key)
        metadata_payload.setdefault("status_source", "life_memory_consolidator")
        terminal = state in OPEN_LOOP_TERMINAL_STATES
        with self.ledger._write_lock:
            with self.ledger._conn:
                existing = self.ledger._conn.execute(
                    "SELECT * FROM life_open_loop WHERE semantic_key = ? LIMIT 1",
                    (key,),
                ).fetchone()
                if existing is None:
                    self.ledger._conn.execute(
                        """
                        INSERT INTO life_open_loop (
                            open_loop_id, semantic_key, episode_id, person_id, session_id,
                            title, detail, status, source_ref,
                            opened_ts_ms, updated_ts_ms, closed_ts_ms,
                            close_episode_id, close_reason,
                            salience, memory_strength, metadata
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            open_loop_id,
                            key,
                            eid,
                            _clean_text(person_id) or None,
                            _clean_text(session_id) or None,
                            _clean_text(title, limit=180) or None,
                            _clean_text(detail, limit=900) or None,
                            state,
                            _clean_text(source_ref) or None,
                            created,
                            created,
                            created if terminal else None,
                            eid if terminal else None,
                            _clean_text(close_reason, limit=300) or None,
                            _clamp_unit(salience),
                            _clamp_unit(memory_strength),
                            _json_dumps(metadata_payload),
                        ),
                    )
                else:
                    current = dict(existing)
                    current_status = _normalize_open_loop_status(current.get("status"))
                    next_status = state
                    closed_ts = int(current.get("closed_ts_ms") or 0) or None
                    close_episode_id = _clean_text(current.get("close_episode_id")) or None
                    next_close_reason = _clean_text(current.get("close_reason"), limit=300) or None
                    if terminal:
                        closed_ts = created
                        close_episode_id = eid
                        next_close_reason = _clean_text(close_reason, limit=300) or _open_loop_close_reason_for_status(state)
                    elif current_status in OPEN_LOOP_TERMINAL_STATES and created <= int(current.get("updated_ts_ms") or 0):
                        next_status = current_status
                    else:
                        closed_ts = None
                        close_episode_id = None
                        next_close_reason = None
                    merged_metadata = _merge_metadata(current.get("metadata"), metadata_payload)
                    self.ledger._conn.execute(
                        """
                        UPDATE life_open_loop
                        SET
                            episode_id = COALESCE(NULLIF(?, ''), episode_id),
                            person_id = COALESCE(?, person_id),
                            session_id = COALESCE(?, session_id),
                            title = COALESCE(?, title),
                            detail = COALESCE(?, detail),
                            status = ?,
                            source_ref = COALESCE(?, source_ref),
                            updated_ts_ms = MAX(COALESCE(updated_ts_ms, 0), ?),
                            closed_ts_ms = ?,
                            close_episode_id = ?,
                            close_reason = ?,
                            salience = MAX(COALESCE(salience, 0.0), ?),
                            memory_strength = MAX(COALESCE(memory_strength, 0.0), ?),
                            metadata = ?
                        WHERE semantic_key = ?
                        """,
                        (
                            eid,
                            _clean_text(person_id) or None,
                            _clean_text(session_id) or None,
                            _clean_text(title, limit=180) or None,
                            _clean_text(detail, limit=900) or None,
                            next_status,
                            _clean_text(source_ref) or None,
                            created,
                            closed_ts,
                            close_episode_id,
                            next_close_reason,
                            _clamp_unit(salience),
                            _clamp_unit(memory_strength),
                            _json_dumps(merged_metadata),
                            key,
                        ),
                    )
        return {
            "status": "OK",
            "open_loop_id": open_loop_id,
            "semantic_key": key,
            "open_loop_status": state,
            "episode_id": eid,
        }

    def list_open_loops(
        self,
        *,
        status: str | None = OPEN_LOOP_OPEN,
        person_id: str | None = None,
        session_id: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        state = _clean_text(status).upper()
        if state:
            clauses.append("status = ?")
            params.append(state)
        person = _clean_text(person_id)
        if person:
            clauses.append("person_id = ?")
            params.append(person)
        session = _clean_text(session_id)
        if session:
            clauses.append("session_id = ?")
            params.append(session)
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        rows = self.ledger._conn.execute(
            f"""
            SELECT *
            FROM life_open_loop
            {where}
            ORDER BY memory_strength DESC, updated_ts_ms DESC
            LIMIT ?
            """,
            (*params, max(1, int(limit))),
        ).fetchall()
        return [dict(row) for row in rows]

    def upsert_collection(
        self,
        *,
        collection_kind: str,
        domain: str,
        scope_key: str,
        title: str,
        summary: str,
        person_id: str | None = None,
        session_id: str | None = None,
        collection_id: str | None = None,
        salience: float = 0.35,
        memory_strength: float = 0.42,
        retention_state: str = RETENTION_WARM,
        ts_ms: int | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        kind = _clean_text(collection_kind) or COLLECTION_RECOMMENDATION
        domain_key = _clean_text(domain) or DOMAIN_RECOMMENDATION
        scope = _clean_text(scope_key) or COLLECTION_SCOPE_RECENT
        person = _clean_text(person_id) or None
        session = _clean_text(session_id) or None
        created = int(ts_ms if ts_ms is not None else now_ts_ms())
        cid = _clean_text(collection_id) or _collection_id(
            collection_kind=kind,
            domain=domain_key,
            scope_key=scope,
            person_id=person,
            session_id=session,
        )
        metadata_payload = dict(metadata or {})
        with self.ledger._write_lock:
            with self.ledger._conn:
                existing = self.ledger._conn.execute(
                    "SELECT metadata, item_count, created_ts_ms FROM life_memory_collection WHERE collection_id = ?",
                    (cid,),
                ).fetchone()
                if existing is None:
                    self.ledger._conn.execute(
                        """
                        INSERT INTO life_memory_collection (
                            collection_id, collection_kind, domain, scope_key,
                            person_id, session_id, title, summary, item_count,
                            salience, memory_strength, retention_state,
                            created_ts_ms, updated_ts_ms, metadata
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            cid,
                            kind,
                            domain_key,
                            scope,
                            person,
                            session,
                            _clean_text(title, limit=240),
                            _clean_text(summary, limit=1200),
                            0,
                            _clamp_unit(salience),
                            _clamp_unit(memory_strength),
                            _clean_text(retention_state).upper() or RETENTION_WARM,
                            created,
                            created,
                            _json_dumps(metadata_payload),
                        ),
                    )
                else:
                    merged_meta = _merge_metadata(existing["metadata"], metadata_payload)
                    for list_key in ("source_episode_ids",):
                        old_values = _json_loads(existing["metadata"], fallback={})
                        old_list = old_values.get(list_key) if isinstance(old_values, Mapping) else []
                        new_list = metadata_payload.get(list_key)
                        if isinstance(old_list, Sequence) and not isinstance(old_list, (str, bytes, bytearray)):
                            merged = [str(item) for item in old_list if str(item).strip()]
                        else:
                            merged = []
                        if isinstance(new_list, Sequence) and not isinstance(new_list, (str, bytes, bytearray)):
                            merged.extend(str(item) for item in new_list if str(item).strip())
                        if merged:
                            merged_meta[list_key] = list(dict.fromkeys(merged))
                    self.ledger._conn.execute(
                        """
                        UPDATE life_memory_collection
                        SET
                            title = COALESCE(NULLIF(?, ''), title),
                            summary = COALESCE(NULLIF(?, ''), summary),
                            salience = MAX(COALESCE(salience, 0.0), ?),
                            memory_strength = MAX(COALESCE(memory_strength, 0.0), ?),
                            retention_state = COALESCE(NULLIF(?, ''), retention_state),
                            updated_ts_ms = MAX(COALESCE(updated_ts_ms, 0), ?),
                            metadata = ?
                        WHERE collection_id = ?
                        """,
                        (
                            _clean_text(title, limit=240),
                            _clean_text(summary, limit=1200),
                            _clamp_unit(salience),
                            _clamp_unit(memory_strength),
                            _clean_text(retention_state).upper() or RETENTION_WARM,
                            created,
                            _json_dumps(merged_meta),
                            cid,
                        ),
                    )
        return {"status": "OK", "collection_id": cid, "collection_kind": kind, "domain": domain_key, "scope_key": scope}

    def attach_collection_item(
        self,
        *,
        collection_id: str,
        episode_id: str,
        item_key: str,
        item_title: str | None = None,
        item_summary: str | None = None,
        source_ref: str | None = None,
        evidence_ref: str | None = None,
        position: int = 0,
        ts_ms: int | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        cid = _clean_text(collection_id)
        eid = _clean_text(episode_id)
        key = _clean_text(item_key) or _stable_hash(f"{cid}:{eid}:{position}:{item_title or item_summary or ''}", size=24)
        if not cid or not eid:
            return {"status": "SKIPPED", "reason_code": "EMPTY_COLLECTION_OR_EPISODE"}
        created = int(ts_ms if ts_ms is not None else now_ts_ms())
        item_id = f"coli_{_stable_hash('life_collection_item:v1:' + cid + ':' + key, size=32)}"
        metadata_payload = dict(metadata or {})
        canonical_title = _collection_item_resolved_canonical_title(
            {
                **metadata_payload,
                "title": metadata_payload.get("title") or item_title,
                "item_title": item_title,
            }
        )
        canonical_key = _collection_item_canonical_key(canonical_title or item_title)
        if canonical_title:
            metadata_payload.setdefault("canonical_title", canonical_title)
        if canonical_key:
            metadata_payload.setdefault("canonical_key", canonical_key)
        with self.ledger._write_lock:
            with self.ledger._conn:
                self.ledger._conn.execute(
                    """
                    INSERT INTO life_memory_collection_item (
                        collection_item_id, collection_id, episode_id, item_key,
                        item_title, item_summary, source_ref, evidence_ref,
                        position, created_ts_ms, metadata
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(collection_id, item_key) DO UPDATE SET
                        item_title = COALESCE(NULLIF(excluded.item_title, ''), item_title),
                        item_summary = COALESCE(NULLIF(excluded.item_summary, ''), item_summary),
                        source_ref = COALESCE(NULLIF(excluded.source_ref, ''), source_ref),
                        evidence_ref = COALESCE(NULLIF(excluded.evidence_ref, ''), evidence_ref),
                        position = excluded.position,
                        metadata = excluded.metadata
                    """,
                    (
                        item_id,
                        cid,
                        eid,
                        key,
                        _clean_text(item_title, limit=300),
                        _clean_text(item_summary, limit=900),
                        _clean_text(source_ref) or None,
                        _clean_text(evidence_ref) or None,
                        max(0, int(position or 0)),
                        created,
                        _json_dumps(metadata_payload),
                    ),
                )
                self._prune_collection_items_for_scope_locked(collection_id=cid)
                self._refresh_collection_item_count_locked(collection_id=cid, ts_ms=created)
        entity = self._index_entity_for_collection_item(
            collection_id=cid,
            collection_item_id=item_id,
            episode_id=eid,
            item_key=key,
            item_title=item_title,
            item_summary=item_summary,
            source_ref=source_ref,
            evidence_ref=evidence_ref,
            position=position,
            ts_ms=created,
            metadata=metadata_payload,
        )
        return {
            "status": "OK",
            "collection_id": cid,
            "collection_item_id": item_id,
            "item_key": key,
            "entity_index": entity,
        }

    def upsert_entity(
        self,
        *,
        entity_kind: str,
        canonical_name: str,
        normalized_key: str | None = None,
        domain: str | None = None,
        salience: float = 0.25,
        memory_strength: float = 0.35,
        retention_state: str = RETENTION_WARM,
        ts_ms: int | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        kind = _clean_text(entity_kind) or ENTITY_KIND_ITEM
        name = _clean_text(canonical_name, limit=300)
        if not name:
            return {"status": "SKIPPED", "reason_code": "EMPTY_ENTITY_NAME"}
        domain_key = _clean_text(domain)
        key = _clean_text(normalized_key) or _entity_normalized_key(name)
        if not key:
            return {"status": "SKIPPED", "reason_code": "EMPTY_ENTITY_KEY"}
        created = int(ts_ms if ts_ms is not None else now_ts_ms())
        entity_id = _entity_id(entity_kind=kind, domain=domain_key, normalized_key=key)
        metadata_payload = dict(metadata or {})
        with self.ledger._write_lock:
            with self.ledger._conn:
                existing = self.ledger._conn.execute(
                    "SELECT metadata FROM life_memory_entity WHERE entity_id = ?",
                    (entity_id,),
                ).fetchone()
                if existing is None:
                    self.ledger._conn.execute(
                        """
                        INSERT INTO life_memory_entity (
                            entity_id, entity_kind, canonical_name, normalized_key, domain,
                            first_seen_ts_ms, last_seen_ts_ms, mention_count,
                            salience, memory_strength, retention_state, metadata
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?)
                        """,
                        (
                            entity_id,
                            kind,
                            name,
                            key,
                            domain_key,
                            created,
                            created,
                            _clamp_unit(salience),
                            _clamp_unit(memory_strength),
                            _clean_text(retention_state).upper() or RETENTION_WARM,
                            _json_dumps(metadata_payload),
                        ),
                    )
                else:
                    merged_meta = _merge_metadata(existing["metadata"], metadata_payload)
                    self.ledger._conn.execute(
                        """
                        UPDATE life_memory_entity
                        SET
                            canonical_name = COALESCE(NULLIF(?, ''), canonical_name),
                            last_seen_ts_ms = MAX(COALESCE(last_seen_ts_ms, 0), ?),
                            salience = MAX(COALESCE(salience, 0.0), ?),
                            memory_strength = MAX(COALESCE(memory_strength, 0.0), ?),
                            retention_state = COALESCE(NULLIF(?, ''), retention_state),
                            metadata = ?
                        WHERE entity_id = ?
                        """,
                        (
                            name,
                            created,
                            _clamp_unit(salience),
                            _clamp_unit(memory_strength),
                            _clean_text(retention_state).upper() or RETENTION_WARM,
                            _json_dumps(merged_meta),
                            entity_id,
                        ),
                    )
        return {
            "status": "OK",
            "entity_id": entity_id,
            "entity_kind": kind,
            "canonical_name": name,
            "normalized_key": key,
            "domain": domain_key,
        }

    def attach_entity_mention(
        self,
        *,
        entity_id: str,
        episode_id: str | None = None,
        collection_id: str | None = None,
        collection_item_id: str | None = None,
        source_ref: str | None = None,
        evidence_ref: str | None = None,
        position: int = 0,
        ts_ms: int | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        eid = _clean_text(entity_id)
        if not eid:
            return {"status": "SKIPPED", "reason_code": "EMPTY_ENTITY_ID"}
        created = int(ts_ms if ts_ms is not None else now_ts_ms())
        mention_seed = "life_entity_mention:v1:" + "|".join([
            eid,
            _clean_text(episode_id),
            _clean_text(collection_id),
            _clean_text(collection_item_id),
            _clean_text(source_ref),
        ])
        mention_id = f"ement_{_stable_hash(mention_seed, size=32)}"
        with self.ledger._write_lock:
            with self.ledger._conn:
                self.ledger._conn.execute(
                    """
                    INSERT INTO life_memory_entity_mention (
                        mention_id, entity_id, episode_id, collection_id, collection_item_id,
                        source_ref, evidence_ref, position, created_ts_ms, metadata
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(entity_id, episode_id, collection_id, collection_item_id) DO UPDATE SET
                        source_ref = COALESCE(NULLIF(excluded.source_ref, ''), source_ref),
                        evidence_ref = COALESCE(NULLIF(excluded.evidence_ref, ''), evidence_ref),
                        position = excluded.position,
                        created_ts_ms = MIN(created_ts_ms, excluded.created_ts_ms),
                        metadata = excluded.metadata
                    """,
                    (
                        mention_id,
                        eid,
                        _clean_text(episode_id) or None,
                        _clean_text(collection_id) or None,
                        _clean_text(collection_item_id) or None,
                        _clean_text(source_ref) or None,
                        _clean_text(evidence_ref) or None,
                        max(0, int(position or 0)),
                        created,
                        _json_dumps(dict(metadata or {})),
                    ),
                )
                self._refresh_entity_mention_count_locked(entity_id=eid)
        return {"status": "OK", "entity_id": eid, "mention_id": mention_id}

    def attach_collection_item_review_evidence(
        self,
        *,
        review_episode_id: str,
        reviewed_items: Sequence[Mapping[str, Any]],
        review_ref: str | None = None,
        fact: str | None = None,
        ts_ms: int | None = None,
    ) -> dict[str, Any]:
        review_eid = _clean_text(review_episode_id)
        if not review_eid:
            return {"status": "SKIPPED", "reason_code": "EMPTY_REVIEW_EPISODE_ID"}
        created = int(ts_ms if ts_ms is not None else now_ts_ms())
        evidence_ref = _clean_text(review_ref) or f"life_episode://{review_eid}"
        updated_count = 0
        mention_count = 0
        skipped_count = 0
        for raw_item in list(reviewed_items or []):
            if not isinstance(raw_item, Mapping):
                skipped_count += 1
                continue
            item = dict(raw_item)
            collection_item_id = _clean_text(item.get("collection_item_id"))
            if not collection_item_id:
                skipped_count += 1
                continue
            row = self.ledger._conn.execute(
                "SELECT * FROM life_memory_collection_item WHERE collection_item_id = ?",
                (collection_item_id,),
            ).fetchone()
            if row is None:
                skipped_count += 1
                continue
            row_dict = dict(row)
            review_meta = _collection_item_review_evidence_metadata(
                item,
                review_episode_id=review_eid,
                review_ref=evidence_ref,
                fact=fact,
                ts_ms=created,
            )
            if not review_meta:
                skipped_count += 1
                continue
            collection_id = _clean_text(row_dict.get("collection_id"))
            position = _coerce_int(row_dict.get("position"))
            source_ref = _clean_text(item.get("detail_url") or row_dict.get("source_ref"))
            with self.ledger._write_lock:
                with self.ledger._conn:
                    merged_meta = _merge_metadata(row_dict.get("metadata"), review_meta)
                    self.ledger._conn.execute(
                        """
                        UPDATE life_memory_collection_item
                        SET
                            evidence_ref = COALESCE(NULLIF(?, ''), evidence_ref),
                            metadata = ?
                        WHERE collection_item_id = ?
                        """,
                        (
                            evidence_ref,
                            _json_dumps(merged_meta),
                            collection_item_id,
                        ),
                    )
                    if collection_id:
                        self.ledger._conn.execute(
                            """
                            UPDATE life_memory_collection
                            SET
                                memory_strength = MAX(COALESCE(memory_strength, 0.0), ?),
                                retention_state = CASE
                                    WHEN COALESCE(retention_state, '') = ? THEN ?
                                    ELSE retention_state
                                END,
                                updated_ts_ms = MAX(COALESCE(updated_ts_ms, 0), ?)
                            WHERE collection_id = ?
                            """,
                            (0.58, RETENTION_COLD, RETENTION_WARM, created, collection_id),
                        )
                    mention_rows = self.ledger._conn.execute(
                        """
                        SELECT DISTINCT entity_id
                        FROM life_memory_entity_mention
                        WHERE collection_item_id = ?
                        """,
                        (collection_item_id,),
                    ).fetchall()
            updated_count += 1
            for mention_row in mention_rows:
                entity_id = _clean_text(mention_row["entity_id"])
                if not entity_id:
                    continue
                result = self.attach_entity_mention(
                    entity_id=entity_id,
                    episode_id=review_eid,
                    collection_id=collection_id,
                    collection_item_id=collection_item_id,
                    source_ref=source_ref or evidence_ref,
                    evidence_ref=evidence_ref,
                    position=position,
                    ts_ms=created,
                    metadata={
                        **review_meta,
                        "memory_layer": "life_memory_entity_mention",
                        "mention_kind": "lightnovel_deep_review",
                    },
                )
                if result.get("status") == "OK":
                    mention_count += 1
        return {
            "status": "OK",
            "review_episode_id": review_eid,
            "updated_count": updated_count,
            "mention_count": mention_count,
            "skipped_count": skipped_count,
        }

    def backfill_collection_item_canonical_index(
        self,
        *,
        limit: int = 1000,
        dry_run: bool = True,
        include_non_lightnovel: bool = False,
        ts_ms: int | None = None,
    ) -> dict[str, Any]:
        """Backfill canonical item metadata and canonical entity mentions for collection items."""

        current = int(ts_ms if ts_ms is not None else now_ts_ms())
        rows = self.ledger._conn.execute(
            """
            SELECT
                i.*,
                c.domain AS collection_domain,
                c.scope_key AS collection_scope_key
            FROM life_memory_collection_item i
            JOIN life_memory_collection c ON c.collection_id = i.collection_id
            WHERE COALESCE(c.retention_state, ?) != ?
            ORDER BY i.created_ts_ms ASC, i.position ASC, i.item_title ASC
            LIMIT ?
            """,
            (RETENTION_WARM, RETENTION_GONE, max(1, int(limit))),
        ).fetchall()
        planned: list[dict[str, Any]] = []
        updated_metadata_count = 0
        indexed_count = 0
        for row in rows:
            item = dict(row)
            domain = _clean_text(item.get("collection_domain"))
            if not include_non_lightnovel and domain != DOMAIN_LIGHTNOVEL_RECOMMENDATION:
                continue
            metadata = _json_loads(item.get("metadata"), fallback={})
            if not isinstance(metadata, dict):
                metadata = {}
            item_title = _clean_text(item.get("item_title"), limit=300)
            canonical_title = _collection_item_resolved_canonical_title(
                {**metadata, "item_title": item_title, "title": item_title}
            )
            canonical_key = _collection_item_canonical_key(canonical_title) or _clean_text(metadata.get("canonical_key"))
            if not canonical_title or not canonical_key:
                continue
            normalized_key = f"title:{canonical_key}" if domain == DOMAIN_LIGHTNOVEL_RECOMMENDATION else _entity_key_from_item(
                title=canonical_title,
                source_ref=item.get("source_ref"),
            )
            entity_kind = ENTITY_KIND_BOOK if domain == DOMAIN_LIGHTNOVEL_RECOMMENDATION else ENTITY_KIND_ITEM
            entity_id = _entity_id(entity_kind=entity_kind, domain=domain, normalized_key=normalized_key)
            metadata_changed = (
                _clean_text(metadata.get("canonical_title"), limit=300) != canonical_title
                or _clean_text(metadata.get("canonical_key")) != canonical_key
            )
            delivery_scope = _clean_text(item.get("collection_scope_key")).startswith(f"{COLLECTION_SCOPE_DELIVERY}:")
            mention_exists = False
            if delivery_scope:
                mention_exists = self.ledger._conn.execute(
                    """
                    SELECT 1
                    FROM life_memory_entity_mention
                    WHERE entity_id = ?
                      AND collection_item_id = ?
                    LIMIT 1
                    """,
                    (entity_id, item.get("collection_item_id")),
                ).fetchone() is not None
            if not metadata_changed and (not delivery_scope or mention_exists):
                continue
            payload = {
                "collection_item_id": item.get("collection_item_id"),
                "collection_id": item.get("collection_id"),
                "episode_id": item.get("episode_id"),
                "item_title": item_title,
                "canonical_title": canonical_title,
                "canonical_key": canonical_key,
                "entity_id": entity_id,
                "metadata_changed": metadata_changed,
                "entity_mention_missing": bool(delivery_scope and not mention_exists),
                "scope_key": item.get("collection_scope_key"),
            }
            planned.append(payload)
            if dry_run:
                continue
            merged_metadata = {**metadata, "canonical_title": canonical_title, "canonical_key": canonical_key}
            if metadata_changed:
                with self.ledger._write_lock:
                    with self.ledger._conn:
                        self.ledger._conn.execute(
                            """
                            UPDATE life_memory_collection_item
                            SET metadata = ?
                            WHERE collection_item_id = ?
                            """,
                            (_json_dumps(merged_metadata), item.get("collection_item_id")),
                        )
                updated_metadata_count += 1
            if delivery_scope and not mention_exists:
                result = self._index_entity_for_collection_item(
                    collection_id=str(item.get("collection_id") or ""),
                    collection_item_id=str(item.get("collection_item_id") or ""),
                    episode_id=str(item.get("episode_id") or ""),
                    item_key=str(item.get("item_key") or ""),
                    item_title=item_title,
                    item_summary=item.get("item_summary"),
                    source_ref=item.get("source_ref"),
                    evidence_ref=item.get("evidence_ref"),
                    position=int(item.get("position") or 0),
                    ts_ms=current,
                    metadata=merged_metadata,
                )
                if result.get("status") == "OK":
                    indexed_count += 1
        return {
            "status": "OK",
            "dry_run": bool(dry_run),
            "scanned_count": len(rows),
            "planned_count": len(planned),
            "updated_metadata_count": updated_metadata_count,
            "indexed_count": indexed_count,
            "samples": planned[:10],
        }

    def index_episode_collections(self, *, episode_id: str, ts_ms: int | None = None) -> dict[str, Any]:
        eid = _clean_text(episode_id)
        if not eid:
            return {"status": "SKIPPED", "reason_code": "EMPTY_EPISODE_ID"}
        row = self.ledger._conn.execute("SELECT * FROM life_episode WHERE episode_id = ?", (eid,)).fetchone()
        if row is None:
            return {"status": "SKIPPED", "reason_code": "EPISODE_NOT_FOUND", "episode_id": eid}
        episode = dict(row)
        metadata = _json_loads(episode.get("metadata"), fallback={})
        if not isinstance(metadata, dict):
            metadata = {}
        items = _extract_collection_items_from_metadata(metadata)
        if not items:
            return {"status": "SKIPPED", "reason_code": "NO_COLLECTION_ITEMS", "episode_id": eid}
        created = int(ts_ms if ts_ms is not None else (episode.get("created_ts_ms") or now_ts_ms()))
        action = _clean_text(episode.get("action") or metadata.get("action_kind")).lower()
        if "deep_review" in action or "analysis" in action:
            return {"status": "SKIPPED", "reason_code": "DERIVED_ANALYSIS_NOT_COLLECTION_SOURCE", "episode_id": eid}
        domain = _collection_domain_for_episode(action=action, summary=episode.get("summary"), metadata=metadata)
        kind = COLLECTION_RECOMMENDATION if ("recommendation" in action or "推荐" in str(episode.get("summary") or "")) else "item_collection"
        day_key = _collection_day_key(created)
        domain_label = "轻小说推荐" if domain == DOMAIN_LIGHTNOVEL_RECOMMENDATION else domain.replace("_", " ")
        person = _clean_text(episode.get("person_id")) or None
        session = _clean_text(episode.get("session_id")) or None
        source_ref = _clean_text(episode.get("primary_ref")) or f"life_episode://{eid}"
        collection_specs = [
            (
                COLLECTION_SCOPE_DELIVERY,
                f"{day_key} 本次{domain_label}集合",
                _collection_summary_text(day_key=day_key, domain=domain, items=items, scope_label="本次"),
            ),
            (
                COLLECTION_SCOPE_DAILY,
                f"{day_key} 当天{domain_label}集合",
                _collection_summary_text(day_key=day_key, domain=domain, items=items, scope_label="当天"),
            ),
            (
                COLLECTION_SCOPE_RECENT,
                f"最近{domain_label}集合",
                _collection_summary_text(day_key=day_key, domain=domain, items=items, scope_label="最近"),
            ),
        ]
        for rolling_scope, _days, label in COLLECTION_ROLLING_WINDOWS:
            collection_specs.append(
                (
                    rolling_scope,
                    f"{label}{domain_label}集合",
                    _collection_summary_text(day_key=day_key, domain=domain, items=items, scope_label=label),
                )
            )
        written: list[dict[str, Any]] = []
        for scope, title, summary in collection_specs:
            collection = self.upsert_collection(
                collection_kind=kind,
                domain=domain,
                scope_key=_collection_scope_key(scope=scope, day_key=day_key, episode_id=eid),
                person_id=person,
                session_id=session,
                title=title,
                summary=summary,
                salience=max(0.38, _clamp_unit(episode.get("salience"), default=0.38)),
                memory_strength=max(0.44, _clamp_unit(episode.get("memory_strength"), default=0.44)),
                retention_state=RETENTION_WARM,
                ts_ms=created,
                metadata={
                    "source_episode_ids": [eid],
                    "source_episode_id": eid,
                    "source_ref": source_ref,
                    "domain": domain,
                    "scope": scope,
                    "day_key": day_key,
                    "memory_layer": "life_memory_collection",
                    "exact_episode_id": eid if scope == COLLECTION_SCOPE_DELIVERY else "",
                    "semantic_consolidation": scope != COLLECTION_SCOPE_DELIVERY,
                },
            )
            collection_id = str(collection.get("collection_id") or "")
            for index, item in enumerate(items, start=1):
                self.attach_collection_item(
                    collection_id=collection_id,
                    episode_id=eid,
                    item_key=_collection_item_key(episode_id=eid, item=item, index=index),
                    item_title=_collection_item_title(item),
                    item_summary=_collection_item_summary(item),
                    source_ref=_collection_item_source_ref(item) or source_ref,
                    evidence_ref=source_ref,
                    position=index,
                    ts_ms=created + index,
                    metadata={**dict(item), "source_day": day_key, "source_episode_id": eid},
                )
            written.append(collection)
        return {"status": "OK", "episode_id": eid, "collection_count": len(written), "collections": written}

    def recall_collection_packet(
        self,
        *,
        query: str,
        top_k: int = 6,
        person_id: str | None = None,
        session_id: str | None = None,
        now_ts_ms_value: int | None = None,
    ) -> EpisodeRecallPacket:
        from host_brain.retrieval.recall_fusion_utils import (  # noqa: PLC0415
            _apply_sqlite_ledger_boost,
            _text_similarity_score,
            _truncate_text_for_recall,
        )

        normalized_query = _clean_text(query)
        plan = plan_life_memory_query(normalized_query)
        current = int(now_ts_ms_value if now_ts_ms_value is not None else now_ts_ms())
        scoped_person_id = _clean_text(person_id)
        scoped_session_id = _clean_text(session_id)
        allow_synthetic_recall = _allow_synthetic_life_recall_for_scope(
            person_id=scoped_person_id,
            session_id=scoped_session_id,
        )
        rows = self._fetch_collection_rows_for_plan(
            plan=plan,
            person_id=None if allow_synthetic_recall else scoped_person_id,
            session_id=scoped_session_id,
            now_ts_ms_value=current,
            limit=max(12, int(top_k) * 6),
        )
        scored: list[tuple[float, dict[str, Any]]] = []
        for row in rows:
            collection = dict(row)
            collection_id = _clean_text(collection.get("collection_id"))
            if not collection_id:
                continue
            metadata = _json_loads(collection.get("metadata"), fallback={})
            if not isinstance(metadata, dict):
                metadata = {}
            if (
                allow_synthetic_recall
                and scoped_session_id
                and _clean_text(collection.get("session_id")) != scoped_session_id
            ):
                continue
            synthetic_record = _is_synthetic_life_record(collection, metadata)
            if synthetic_record:
                if not allow_synthetic_recall:
                    continue
            items = self._list_collection_items(collection_id=collection_id, limit=24)
            if not allow_synthetic_recall:
                items = [item for item in items if not _collection_item_is_synthetic(item)]
            source_episode_id_order = [
                _clean_text(item.get("episode_id"))
                for item in items
                if _clean_text(item.get("episode_id"))
            ]
            trusted_episode_ids = _trusted_active_episode_ids(
                self,
                source_episode_id_order,
                allow_synthetic=allow_synthetic_recall,
            )
            if source_episode_id_order:
                trusted_items = [item for item in items if _clean_text(item.get("episode_id")) in trusted_episode_ids]
                items = trusted_items or [
                    item
                    for item in items
                    if _collection_item_has_recallable_anchor(item)
                    and _collection_item_episode_anchor_is_synthetic(self, item)
                ]
            if not items:
                continue
            text_for_score = "\n".join(
                part
                for part in (
                    str(collection.get("title") or ""),
                    str(collection.get("summary") or ""),
                    "\n".join(_collection_item_recall_line(item, idx) for idx, item in enumerate(items, start=1)),
                )
                if part
            )
            base_score = _text_similarity_score(normalized_query, text_for_score) if normalized_query else 0.0
            scope_boost = _collection_scope_boost(collection, plan=plan, now_ts_ms_value=current)
            if normalized_query and base_score <= 0.0 and scope_boost <= 0.0:
                continue
            boosted_score, boost_meta = _apply_sqlite_ledger_boost(
                base_score=max(base_score, 0.04 + scope_boost),
                row_ts_ms=int(collection.get("updated_ts_ms") or collection.get("created_ts_ms") or 0),
                now_ts_ms=current,
                row_person_id=str(collection.get("person_id") or "").strip() or None,
                target_person_id=scoped_person_id,
                row_session_id=str(collection.get("session_id") or "").strip() or None,
                target_session_id=scoped_session_id,
            )
            strength = _clamp_unit(collection.get("memory_strength"), default=_clamp_unit(collection.get("salience"), default=0.4))
            final_score = round(max(0.0, min(1.0, boosted_score + scope_boost + strength * 0.10)), 6)
            item_lines = [_collection_item_recall_line(item, index) for index, item in enumerate(items, start=1)]
            text = _truncate_text_for_recall(
                f"{collection.get('summary') or collection.get('title') or ''}；可比较证据：{'；'.join(item_lines)}",
                limit=1400,
            )
            evidence_refs = [
                _clean_text(item.get("evidence_ref") or item.get("source_ref"))
                for item in items
                if _clean_text(item.get("evidence_ref") or item.get("source_ref"))
            ]
            scored.append(
                (
                    final_score,
                    {
                        "memory_id": collection_id,
                        "text": text,
                        "score": final_score,
                        "metadata": {
                            "store_id": "life_memory_collection",
                            "memory_layer": "life_memory_collection",
                            "collection_id": collection_id,
                            "collection_kind": collection.get("collection_kind"),
                            "domain": collection.get("domain"),
                            "scope_key": collection.get("scope_key"),
                            "person_id": collection.get("person_id"),
                            "session_id": collection.get("session_id"),
                            "title": collection.get("title"),
                            "item_count": len(items),
                            "items": [dict(item) for item in items],
                            "collection_metadata": metadata,
                            "created_ts_ms": int(collection.get("created_ts_ms") or 0),
                            "updated_ts_ms": int(collection.get("updated_ts_ms") or 0),
                            "memory_strength": strength,
                            "retention_state": collection.get("retention_state"),
                            "source_ref": f"life_memory_collection://{collection_id}",
                            "evidence_refs_primary": evidence_refs[0] if evidence_refs else f"life_memory_collection://{collection_id}",
                            "evidence_refs": evidence_refs,
                            "query_plan": plan.as_dict(),
                            "score_boost": {
                                **dict(boost_meta or {}),
                                "scope_boost": round(scope_boost, 6),
                                "strength_boost": round(strength * 0.10, 6),
                            },
                        },
                    },
                )
            )
        candidates = [
            item
            for _score, item in sorted(
                scored,
                key=lambda pair: (
                    -round(pair[0], 2),
                    -int(bool(scoped_session_id and _clean_text(pair[1]["metadata"].get("session_id")) == scoped_session_id)),
                    -int(bool(scoped_person_id and _clean_text(pair[1]["metadata"].get("person_id")) == scoped_person_id)),
                    -int(pair[1]["metadata"].get("updated_ts_ms") or 0),
                    str(pair[1].get("memory_id") or ""),
                ),
            )[: max(1, int(top_k))]
        ]
        return EpisodeRecallPacket(query=normalized_query, candidates=candidates)

    def recall_entity_packet(
        self,
        *,
        query: str,
        top_k: int = 6,
        person_id: str | None = None,
        session_id: str | None = None,
        now_ts_ms_value: int | None = None,
    ) -> EpisodeRecallPacket:
        from host_brain.retrieval.recall_fusion_utils import (  # noqa: PLC0415
            _apply_sqlite_ledger_boost,
            _text_similarity_score,
            _truncate_text_for_recall,
        )

        normalized_query = _clean_text(query)
        plan = plan_life_memory_query(normalized_query)
        current = int(now_ts_ms_value if now_ts_ms_value is not None else now_ts_ms())
        domains = set(str(item or "").strip() for item in getattr(plan, "target_domains", ()) if str(item or "").strip())
        scoped_entity_mention_keys = {
            _clean_text(item)
            for item in _scope_keys_for_collection_plan(plan=plan, now_ts_ms_value=current)
            if _clean_text(item)
        }
        scoped_entity_mention_days = {
            key.split(":", 1)[1]
            for key in scoped_entity_mention_keys
            if key.startswith(f"{COLLECTION_SCOPE_DAILY}:") and len(key.split(":", 1)) == 2
        }
        if DOMAIN_LIGHTNOVEL_RECOMMENDATION in domains:
            domains.add(DOMAIN_RECOMMENDATION)
        if DOMAIN_RECOMMENDATION in domains:
            domains.add(DOMAIN_LIGHTNOVEL_RECOMMENDATION)
        clauses = ["COALESCE(retention_state, ?) != ?"]
        params: list[Any] = [RETENTION_WARM, RETENTION_GONE]
        if domains:
            placeholders = ", ".join("?" for _ in domains)
            clauses.append(f"COALESCE(domain, '') IN ({placeholders})")
            params.extend(sorted(domains))
        rows = self.ledger._conn.execute(
            f"""
            SELECT *
            FROM life_memory_entity
            WHERE {' AND '.join(clauses)}
            ORDER BY last_seen_ts_ms DESC, memory_strength DESC
            LIMIT ?
            """,
            [*params, max(8, int(top_k) * 8)],
        ).fetchall()
        terms = _recall_like_terms(normalized_query)
        scored: list[tuple[float, dict[str, Any]]] = []
        for row in rows:
            entity = dict(row)
            metadata = _json_loads(entity.get("metadata"), fallback={})
            if not isinstance(metadata, dict):
                metadata = {}
            entity_id = _clean_text(entity.get("entity_id"))
            if not entity_id:
                continue
            mention_rows = self._list_entity_mentions(
                entity_id=entity_id,
                person_id=person_id,
                session_id=session_id,
                limit=8,
            )
            if scoped_entity_mention_days:
                mention_rows = [
                    item
                    for item in mention_rows
                    if _entity_mention_matches_daily_scope(item, target_days=scoped_entity_mention_days)
                ]
                if not mention_rows:
                    continue
            text_for_score = " ".join(
                str(part or "")
                for part in (
                    entity.get("canonical_name"),
                    entity.get("normalized_key"),
                    entity.get("domain"),
                    metadata.get("source_ref"),
                    "\n".join(_entity_mention_recall_line(item) for item in mention_rows[:4]),
                )
            )
            base_score = _text_similarity_score(normalized_query, text_for_score) if normalized_query else 0.0
            keyword_hit = any(term and term in text_for_score.lower() for term in terms)
            if normalized_query and base_score <= 0.0 and not keyword_hit:
                continue
            boosted_score, boost_meta = _apply_sqlite_ledger_boost(
                base_score=max(base_score, 0.05 if keyword_hit else 0.02),
                row_ts_ms=int(entity.get("last_seen_ts_ms") or 0),
                now_ts_ms=current,
                row_person_id=None,
                target_person_id=_clean_text(person_id) or None,
                row_session_id=None,
                target_session_id=_clean_text(session_id) or None,
            )
            strength = _clamp_unit(entity.get("memory_strength"), default=_clamp_unit(entity.get("salience"), default=0.35))
            mention_count = int(entity.get("mention_count") or len(mention_rows))
            scoped_mention_count = len(mention_rows)
            display_name = _clean_text(
                (mention_rows[0].get("item_title") if mention_rows else "") or entity.get("canonical_name"),
                limit=160,
            )
            final_score = round(max(0.0, min(1.0, boosted_score + min(0.12, mention_count * 0.015) + strength * 0.08)), 6)
            mention_lines = [_entity_mention_recall_line(item) for item in mention_rows[:5]]
            text = _truncate_text_for_recall(
                f"实体记忆：{display_name or entity.get('canonical_name')}；出现 {scoped_mention_count or mention_count} 次；"
                f"最近证据：{'；'.join(line for line in mention_lines if line)}",
                limit=900,
            )
            evidence_refs = [
                _clean_text(item.get("evidence_ref") or item.get("source_ref"))
                for item in mention_rows
                if _clean_text(item.get("evidence_ref") or item.get("source_ref"))
            ]
            scored.append(
                (
                    final_score,
                    {
                        "memory_id": entity_id,
                        "text": text,
                        "score": final_score,
                        "metadata": {
                            "store_id": "life_memory_entity",
                            "memory_layer": "life_memory_entity",
                            "entity_id": entity_id,
                            "entity_kind": entity.get("entity_kind"),
                            "canonical_name": entity.get("canonical_name"),
                            "normalized_key": entity.get("normalized_key"),
                            "domain": entity.get("domain"),
                            "mention_count": mention_count,
                            "mentions": mention_rows,
                            "retention_state": entity.get("retention_state"),
                            "memory_strength": strength,
                            "created_ts_ms": int(entity.get("first_seen_ts_ms") or 0),
                            "updated_ts_ms": int(entity.get("last_seen_ts_ms") or 0),
                            "source_ref": f"life_memory_entity://{entity_id}",
                            "evidence_refs_primary": evidence_refs[0] if evidence_refs else f"life_memory_entity://{entity_id}",
                            "evidence_refs": evidence_refs,
                            "query_plan": plan.as_dict(),
                            "score_boost": {
                                **dict(boost_meta or {}),
                                "keyword_hit": bool(keyword_hit),
                                "strength_boost": round(strength * 0.08, 6),
                                "mention_count_boost": round(min(0.12, mention_count * 0.015), 6),
                            },
                        },
                    },
                )
            )
        candidates = [
            item
            for _score, item in sorted(
                scored,
                key=lambda pair: (
                    -round(pair[0], 2),
                    -int(pair[1]["metadata"].get("updated_ts_ms") or 0),
                    str(pair[1].get("memory_id") or ""),
                ),
            )[: max(1, int(top_k))]
        ]
        return EpisodeRecallPacket(query=normalized_query, candidates=candidates)

    def link_episode(
        self,
        *,
        source_episode_id: str,
        target_episode_id: str,
        link_kind: str,
        weight: float = 1.0,
        ts_ms: int | None = None,
        metadata: Mapping[str, Any] | None = None,
        link_id: str | None = None,
    ) -> dict[str, Any]:
        source = _clean_text(source_episode_id)
        target = _clean_text(target_episode_id)
        kind = _clean_text(link_kind) or "related"
        if not source or not target:
            raise ValueError("source_episode_id and target_episode_id are required")
        lid = _clean_text(link_id) or f"el_{_stable_hash('life_episode_link:v1:' + source + '|' + target + '|' + kind)}"
        created = int(ts_ms if ts_ms is not None else now_ts_ms())
        with self.ledger._write_lock:
            with self.ledger._conn:
                self.ledger._conn.execute(
                    """
                    INSERT OR IGNORE INTO life_episode_link (
                        link_id, source_episode_id, target_episode_id, link_kind,
                        weight, created_ts_ms, metadata
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        lid,
                        source,
                        target,
                        kind,
                        max(0.0, float(weight or 0.0)),
                        created,
                        _json_dumps(dict(metadata or {})),
                    ),
                )
        return {"status": "OK", "link_id": lid}

    def correct_episode(
        self,
        *,
        episode_id: str,
        correction_kind: str = "correction",
        correction_episode_id: str | None = None,
        reason_code: str | None = None,
        detail: str | None = None,
        suppress: bool = False,
        ts_ms: int | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        eid = _clean_text(episode_id)
        if not eid:
            raise ValueError("episode_id is required")
        created = int(ts_ms if ts_ms is not None else now_ts_ms())
        kind = _clean_text(correction_kind) or ("suppression" if suppress else "correction")
        correction_id = f"corr_{_stable_hash('life_correction:v1:' + eid + '|' + kind + '|' + str(created) + '|' + _clean_text(detail, limit=200))}"
        next_truth = TRUTH_SUPPRESSED if suppress else TRUTH_CORRECTED
        next_status = STATUS_SUPPRESSED if suppress else STATUS_ACTIVE
        with self.ledger._write_lock:
            with self.ledger._conn:
                self.ledger._conn.execute(
                    """
                    INSERT OR IGNORE INTO life_memory_correction (
                        correction_id, episode_id, correction_episode_id, correction_kind,
                        reason_code, detail, created_ts_ms, metadata
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        correction_id,
                        eid,
                        _clean_text(correction_episode_id) or None,
                        kind,
                        _clean_text(reason_code) or None,
                        _clean_text(detail, limit=2000),
                        created,
                        _json_dumps(dict(metadata or {})),
                    ),
                )
                self.ledger._conn.execute(
                    """
                    UPDATE life_episode
                    SET truth_state = ?, status = ?, last_touched_ts_ms = MAX(COALESCE(last_touched_ts_ms, 0), ?)
                    WHERE episode_id = ?
                    """,
                    (next_truth, next_status, created, eid),
                )
        if correction_episode_id:
            self.link_episode(
                source_episode_id=correction_episode_id,
                target_episode_id=eid,
                link_kind="correction",
                weight=1.0,
                ts_ms=created,
            )
        return {
            "status": "OK",
            "correction_id": correction_id,
            "episode_id": eid,
            "truth_state": next_truth,
        }

    def write_chat_episode(
        self,
        *,
        request_id: str,
        session_id: str,
        person_id: str | None,
        user_message: str,
        assistant_reply: str,
        ts_ms: int | None = None,
        intent_id: str | None = None,
        attachments: Sequence[Mapping[str, Any]] | None = None,
        multimodal_observations: Mapping[str, Any] | None = None,
        attachment_summary: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        req = _clean_text(request_id) or f"chat-{uuid.uuid4().hex}"
        created = int(ts_ms if ts_ms is not None else now_ts_ms())
        refs = [
            f"chat://request/{req}",
            f"chat://session/{_clean_text(session_id) or 'default'}",
            f"chat://person/{_clean_text(person_id) or 'default'}",
        ]
        intent = _clean_text(intent_id)
        if intent:
            refs.append(f"chat://intent/{intent}")
        meta = dict(metadata or {}) if isinstance(metadata, Mapping) else {}
        reply_channel = _clean_text(meta.get("reply_channel")).lower()
        gateway_message_id = _clean_text(meta.get("gateway_message_id"))
        if not gateway_message_id and (
            reply_channel in {"gateway", "qq", "wechat"}
            or _clean_text(session_id).lower().startswith(("qq_", "qq:", "wechat_", "wechat:"))
        ):
            gateway_message_id = req
        if gateway_message_id:
            refs.append(f"gateway://message/{gateway_message_id}")
        summary_parts = [
            f"User: {_clean_text(user_message, limit=500)}",
            f"Assistant: {_clean_text(assistant_reply, limit=500)}",
        ]
        perception_summary = _clean_text(attachment_summary, limit=500)
        if perception_summary:
            summary_parts.append(f"Perception: {perception_summary}")
        episode = self.upsert_episode(
            episode_kind=EPISODE_CHAT,
            evidence_refs=refs,
            thread_id=f"chat://session/{_clean_text(session_id) or 'default'}",
            person_id=person_id,
            session_id=session_id,
            actor_id="natsume",
            intent=intent or None,
            action="chat_turn",
            result="assistant_reply",
            primary_ref=f"chat://request/{req}",
            summary="\n".join(part for part in summary_parts if part).strip(),
            salience=_chat_salience(user_message=user_message, assistant_reply=assistant_reply, has_perception=bool(attachments or multimodal_observations)),
            truth_state=TRUTH_VERIFIED,
            ts_ms=created,
            metadata={
                **meta,
                "intent_id": intent or None,
                "memory_layer": "life_episode",
            },
        )
        episode_id = str(episode["episode_id"])
        if _clean_text(user_message):
            self.attach_evidence(
                episode_id=episode_id,
                evidence_kind=EVIDENCE_USER_TEXT,
                source_ref=f"chat://request/{req}#user",
                request_id=req,
                content=user_message,
                payload={"person_id": _clean_text(person_id), "session_id": _clean_text(session_id)},
                trust_level=TRUTH_VERIFIED,
                ts_ms=created,
            )
        if _clean_text(assistant_reply):
            self.attach_evidence(
                episode_id=episode_id,
                evidence_kind=EVIDENCE_ASSISTANT_REPLY,
                source_ref=f"chat://request/{req}#assistant",
                request_id=req,
                content=assistant_reply,
                payload={"person_id": _clean_text(person_id), "session_id": _clean_text(session_id)},
                trust_level=TRUTH_VERIFIED,
                ts_ms=created + 1,
            )
        attachment_rows = [dict(item) for item in list(attachments or []) if isinstance(item, Mapping)]
        observations = dict(multimodal_observations or {})
        if gateway_message_id:
            self.attach_evidence(
                episode_id=episode_id,
                evidence_kind=EVIDENCE_GATEWAY_MESSAGE,
                source_ref=f"gateway://message/{gateway_message_id}",
                request_id=req,
                content=user_message,
                payload={"attachments": attachment_rows, "observations": observations},
                trust_level=TRUTH_VERIFIED,
                ts_ms=created,
            )
        if attachment_rows:
            for idx, attachment in enumerate(attachment_rows):
                source_ref = _clean_text(attachment.get("source_ref")) or _clean_text(attachment.get("url"))
                if not source_ref:
                    source_ref = f"chat://request/{req}#attachment/{idx}"
                kind = EVIDENCE_VISION_OBSERVATION if _attachment_looks_visual(attachment) else EVIDENCE_ATTACHMENT
                self.attach_evidence(
                    episode_id=episode_id,
                    evidence_kind=kind,
                    source_ref=source_ref,
                    request_id=req,
                    content=perception_summary,
                    payload={"attachment": attachment, "observations": observations},
                    trust_level=TRUTH_VERIFIED if observations or perception_summary else TRUTH_CANDIDATE,
                    ts_ms=created + 2 + idx,
                )
        elif observations or perception_summary:
            self.attach_evidence(
                episode_id=episode_id,
                evidence_kind=EVIDENCE_VISION_OBSERVATION,
                source_ref=f"vision://observation/{req}",
                request_id=req,
                content=perception_summary,
                payload={"observations": observations},
                trust_level=TRUTH_VERIFIED if observations else TRUTH_CANDIDATE,
                ts_ms=created + 2,
            )
        return {
            "status": "OK",
            "episode_id": episode_id,
            "episode_kind": EPISODE_CHAT,
            "evidence_refs": refs,
        }

    def write_owner_directive_episode(
        self,
        *,
        request_id: str,
        directive_text: str,
        person_id: str | None = "owner",
        session_id: str | None = "codex_operator",
        directive_kind: str | None = None,
        applied_result: str | None = None,
        ts_ms: int | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Persist owner control-plane directives as lived evidence.

        This is for real owner instructions that arrive outside the normal chat
        surface, such as Codex/operator control threads. It creates an episode
        anchor first; semantic memory is still derived later through the normal
        consolidator and evidence gates.
        """

        text = _clean_text(directive_text, limit=2000)
        if not text:
            raise ValueError("directive_text is required")
        req = _clean_text(request_id) or f"owner-directive-{uuid.uuid4().hex}"
        created = int(ts_ms if ts_ms is not None else now_ts_ms())
        kind = _clean_text(directive_kind) or "owner_directive"
        person = _clean_text(person_id) or "owner"
        session = _clean_text(session_id) or "codex_operator"
        refs = [
            f"chat://request/{req}",
            f"action://event/{req}",
            f"owner://directive/{req}",
            f"chat://session/{session}",
            f"chat://person/{person}",
        ]
        meta = {
            **dict(metadata or {}),
            "event_source": "owner_directive",
            "owner_directive": True,
            "directive_kind": kind,
            "user_importance": max(_metadata_float(metadata, "user_importance", default=0.0), 0.72),
            "memory_layer": "life_episode",
            "evidence_refs": refs,
        }
        result_text = _clean_text(applied_result, limit=800)
        summary_parts = [f"Owner directive: {text}"]
        if result_text:
            summary_parts.append(f"Applied result: {result_text}")
        episode = self.upsert_episode(
            episode_kind=EPISODE_CHAT,
            evidence_refs=refs,
            thread_id=f"owner://thread/{session}",
            person_id=person,
            session_id=session,
            actor_id="owner",
            intent=kind,
            action="owner_directive",
            result="directive_recorded",
            primary_ref=f"owner://directive/{req}",
            summary="\n".join(summary_parts),
            salience=max(_metadata_float(metadata, "salience", default=0.0), 0.82),
            truth_state=TRUTH_VERIFIED,
            ts_ms=created,
            metadata=meta,
        )
        episode_id = str(episode["episode_id"])
        self.attach_evidence(
            episode_id=episode_id,
            evidence_kind=EVIDENCE_USER_TEXT,
            source_ref=f"chat://request/{req}#owner",
            request_id=req,
            content=text,
            payload={"person_id": person, "session_id": session, "directive_kind": kind},
            trust_level=TRUTH_VERIFIED,
            ts_ms=created,
        )
        if result_text:
            self.attach_evidence(
                episode_id=episode_id,
                evidence_kind=EVIDENCE_TOOL_RESULT,
                source_ref=f"action://event/{req}#applied_result",
                request_id=req,
                content=result_text,
                payload={"person_id": person, "session_id": session, "directive_kind": kind},
                trust_level=TRUTH_VERIFIED,
                ts_ms=created + 1,
            )
        return {
            "status": "OK",
            "episode_id": episode_id,
            "episode_kind": EPISODE_CHAT,
            "evidence_refs": refs,
        }

    def write_background_episode(
        self,
        *,
        event_source: str,
        event_id: str | None = None,
        request_id: str | None = None,
        intent_id: str | None = None,
        person_id: str | None = None,
        session_id: str | None = None,
        task_id: str | None = None,
        status: str | None = None,
        reason_code: str | None = None,
        detail: str | None = None,
        ts_ms: int | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        source = _clean_text(event_source).lower() or "background"
        created = int(ts_ms if ts_ms is not None else now_ts_ms())
        event_key = _clean_text(event_id or request_id or (metadata or {}).get("event_id") or "")
        if not event_key:
            event_key = _stable_hash(f"{source}:{task_id or ''}:{created}:{detail or ''}", size=20)
        if source in {"inner_world", "inner_stream", "inner_activity"}:
            primary_ref = f"inner_world://event/{event_key}" if source != "inner_activity" else f"inner_activity://event/{event_key}"
            episode_kind = EPISODE_INNER
            evidence_kind = EVIDENCE_INNER_THOUGHT
        elif source in {"vision", "screen_ocr", "camera", "camera_recognition", "perception"}:
            primary_ref = f"vision://observation/{event_key}" if source != "perception" else f"perception://event/{event_key}"
            episode_kind = EPISODE_PERCEPTION
            evidence_kind = EVIDENCE_VISION_OBSERVATION
        elif source in {"voice", "audio", "asr"}:
            primary_ref = f"voice://event/{event_key}"
            episode_kind = EPISODE_PERCEPTION
            evidence_kind = EVIDENCE_AUDIO_ASR
        elif source == "scheduler":
            primary_ref = f"scheduler://event/{event_key}"
            episode_kind = EPISODE_BACKGROUND
            evidence_kind = EVIDENCE_SCHEDULER_EVENT
        elif source == "skill":
            primary_ref = f"skill://exec/{event_key}"
            episode_kind = EPISODE_ACTION
            evidence_kind = EVIDENCE_TOOL_RESULT
        elif source == "mcp":
            primary_ref = f"mcp://exec/{event_key}"
            episode_kind = EPISODE_ACTION
            evidence_kind = EVIDENCE_TOOL_RESULT
        elif source == "sandbox":
            primary_ref = f"sandbox://exec/{event_key}"
            episode_kind = EPISODE_ACTION
            evidence_kind = EVIDENCE_TOOL_RESULT
        elif source == "avatar":
            primary_ref = f"avatar://action/{event_key}"
            episode_kind = EPISODE_ACTION
            evidence_kind = EVIDENCE_TOOL_RESULT
        elif source == "clawd":
            primary_ref = f"clawd://action/{event_key}"
            episode_kind = EPISODE_ACTION
            evidence_kind = EVIDENCE_TOOL_RESULT
        elif source == "channel":
            primary_ref = f"channel://event/{event_key}"
            episode_kind = EPISODE_ACTION
            evidence_kind = EVIDENCE_GATEWAY_MESSAGE
        elif source in {"action", "gateway", "codex_production", "surface_memory_recall", "tool", "browser"}:
            primary_ref = f"action://event/{event_key}"
            episode_kind = EPISODE_ACTION
            evidence_kind = EVIDENCE_TOOL_RESULT
        else:
            primary_ref = f"{source}://event/{event_key}"
            episode_kind = EPISODE_BACKGROUND
            evidence_kind = EVIDENCE_TOOL_RESULT
        refs = [primary_ref]
        for ref in _normalize_refs((metadata or {}).get("evidence_refs") if isinstance(metadata, Mapping) else None):
            if ref not in refs:
                refs.append(ref)
        if task_id:
            refs.append(f"scheduler://task/{_clean_text(task_id)}")
        if session_id:
            refs.append(f"chat://session/{_clean_text(session_id)}")
        if person_id:
            refs.append(f"chat://person/{_clean_text(person_id)}")
        summary = " ".join(
            part
            for part in (
                source,
                _clean_text(task_id),
                _clean_text(status),
                _clean_text(reason_code),
                _clean_text(detail, limit=800),
            )
            if part
        ).strip()
        metadata_payload = {
            **dict(metadata or {}),
            "event_source": source,
            "task_id": _clean_text(task_id) or None,
            "intent_id": _clean_text(intent_id) or None,
            "memory_layer": "life_episode",
            "evidence_refs": refs,
        }
        episode = self.upsert_episode(
            episode_kind=episode_kind,
            evidence_refs=refs,
            thread_id=f"{source}://thread/{_clean_text(task_id or session_id or person_id or source)}",
            person_id=person_id,
            session_id=session_id,
            actor_id=_clean_text((metadata or {}).get("actor_id") if isinstance(metadata, Mapping) else None) or "natsume",
            intent=_clean_text(intent_id) or None,
            action=_clean_text((metadata or {}).get("action_kind") if isinstance(metadata, Mapping) else None) or _clean_text(task_id) or source,
            result=_clean_text(status) or None,
            primary_ref=primary_ref,
            summary=summary,
            salience=_background_salience(source=source, status=status, detail=detail),
            truth_state=TRUTH_VERIFIED,
            ts_ms=created,
            metadata=metadata_payload,
        )
        episode_id = str(episode["episode_id"])
        self.attach_evidence(
            episode_id=episode_id,
            evidence_kind=evidence_kind,
            source_ref=primary_ref,
            request_id=_clean_text(request_id) or event_key,
            content=detail or summary,
            payload={
                "event_source": source,
                "event_id": event_key,
                "task_id": _clean_text(task_id) or None,
                "status": _clean_text(status) or None,
                "reason_code": _clean_text(reason_code) or None,
                "metadata": dict(metadata or {}),
            },
            trust_level=TRUTH_VERIFIED,
            ts_ms=created,
        )
        collection_result = self.index_episode_collections(episode_id=episode_id, ts_ms=created)
        return {
            "status": "OK",
            "episode_id": episode_id,
            "episode_kind": episode_kind,
            "primary_ref": primary_ref,
            "collection_index": collection_result,
        }

    def recall_episode_packet(
        self,
        *,
        query: str,
        top_k: int = 8,
        person_id: str | None = None,
        session_id: str | None = None,
        include_suppressed: bool = False,
        now_ts_ms_value: int | None = None,
    ) -> EpisodeRecallPacket:
        from host_brain.retrieval.recall_fusion_utils import (  # noqa: PLC0415
            _apply_sqlite_ledger_boost,
            _text_similarity_score,
            _truncate_text_for_recall,
        )

        normalized_query = _clean_text(query)
        query_plan = plan_life_memory_query(normalized_query)
        current = int(now_ts_ms_value if now_ts_ms_value is not None else now_ts_ms())
        limit = max(1, int(top_k)) * _RECALL_FETCH_LIMIT_FACTOR
        anaphoric_query = _looks_like_anaphoric_selection_query(normalized_query)
        scoped_person_id = _clean_text(person_id)
        scoped_session_id = _clean_text(session_id)
        where = "WHERE status != ? AND truth_state NOT IN (?, ?) AND COALESCE(retention_state, ?) != ?"
        params: list[Any] = [STATUS_SUPPRESSED, TRUTH_SUPPRESSED, TRUTH_CORRECTED, RETENTION_COLD, RETENTION_GONE]
        if include_suppressed:
            where = ""
            params = []

        def fetch_rows(extra_where: str = "", extra_params: Sequence[Any] | None = None, *, row_limit: int = limit) -> list[Any]:
            clause = where
            if extra_where:
                clause = f"{where} AND ({extra_where})" if where else f"WHERE {extra_where}"
            return list(
                self.ledger._conn.execute(
                    f"""
                    SELECT *
                    FROM life_episode
                    {clause}
                    ORDER BY last_touched_ts_ms DESC
                    LIMIT ?
                    """,
                    [*params, *list(extra_params or []), max(1, int(row_limit))],
                ).fetchall()
            )

        rows_by_id: dict[str, Any] = {}
        anaphoric_episode_ids: set[str] = set()
        for row in fetch_rows():
            rows_by_id[str(row["episode_id"])] = row
        like_terms = _recall_like_terms(normalized_query)
        if like_terms:
            clauses: list[str] = []
            like_params: list[str] = []
            for term in like_terms:
                pattern = f"%{term}%"
                clauses.append("(COALESCE(summary, '') LIKE ? OR COALESCE(primary_ref, '') LIKE ?)")
                like_params.extend([pattern, pattern])
            for row in fetch_rows(" OR ".join(clauses), like_params, row_limit=limit * 2):
                rows_by_id[str(row["episode_id"])] = row
        target_memory_classes = _episode_recall_target_memory_classes(getattr(query_plan, "query_kind", ""))
        target_domains = {
            _clean_text(domain).lower()
            for domain in getattr(query_plan, "target_domains", ())
            if _clean_text(domain)
        }
        if target_memory_classes:
            placeholders = ", ".join("?" for _ in target_memory_classes)
            class_where = (
                f"json_extract(metadata, '$.memory_class') IN ({placeholders}) "
                f"OR json_extract(metadata, '$.memory_policy.memory_class') IN ({placeholders})"
            )
            for row in fetch_rows(
                class_where,
                [*target_memory_classes, *target_memory_classes],
                row_limit=max(limit * 3, 48),
            ):
                rows_by_id[str(row["episode_id"])] = row
        if target_domains:
            domain_clauses: list[str] = []
            domain_params: list[str] = []
            for domain in sorted(target_domains):
                pattern = f"%{domain}%"
                domain_clauses.append(
                    "(COALESCE(metadata, '') LIKE ? OR COALESCE(summary, '') LIKE ? OR COALESCE(primary_ref, '') LIKE ?)"
                )
                domain_params.extend([pattern, pattern, pattern])
            for row in fetch_rows(" OR ".join(domain_clauses), domain_params, row_limit=max(limit * 3, 48)):
                rows_by_id[str(row["episode_id"])] = row
        if anaphoric_query and (scoped_session_id or scoped_person_id):
            scope_clauses: list[str] = []
            scope_params: list[Any] = []
            if scoped_session_id:
                scope_clauses.append("COALESCE(session_id, '') = ?")
                scope_params.append(scoped_session_id)
            if scoped_person_id:
                scope_clauses.append("COALESCE(person_id, '') = ?")
                scope_params.append(scoped_person_id)
            kind_placeholders = ", ".join("?" for _ in _ANAPHORIC_EVENT_KINDS)
            scope_clause = " OR ".join(scope_clauses)
            extra_where = f"episode_kind IN ({kind_placeholders}) AND ({scope_clause})"
            extra_params = [*_ANAPHORIC_EVENT_KINDS, *scope_params]
            for row in fetch_rows(extra_where, extra_params, row_limit=max(limit * 3, 40)):
                episode_id = str(row["episode_id"])
                if _is_significant_anaphoric_episode(dict(row)):
                    rows_by_id[episode_id] = row
                    anaphoric_episode_ids.add(episode_id)
        rows = list(rows_by_id.values())
        scored: list[tuple[float, dict[str, Any]]] = []
        for row in rows:
            episode = dict(row)
            episode_id = str(episode.get("episode_id") or "").strip()
            if not episode_id:
                continue
            episode_metadata = _json_loads(episode.get("metadata"), fallback={})
            if not isinstance(episode_metadata, dict):
                episode_metadata = {}
            allow_synthetic_recall = _allow_synthetic_life_recall_for_scope(
                person_id=scoped_person_id,
                session_id=scoped_session_id,
            )
            if not _record_matches_life_recall_scope(
                episode,
                episode_metadata,
                person_id=scoped_person_id,
                session_id=scoped_session_id,
                allow_synthetic=allow_synthetic_recall,
            ):
                continue
            evidence_rows = self._list_evidence(episode_id=episode_id)
            correction_rows = self._list_corrections(episode_id=episode_id)
            evidence_text = "\n".join(
                part
                for ev in evidence_rows
                for part in (str(ev.get("content") or "").strip(), str(ev.get("source_ref") or "").strip())
                if part
            )
            text_for_score = "\n".join(
                part
                for part in (
                    str(episode.get("summary") or "").strip(),
                    evidence_text,
                    str(episode.get("primary_ref") or "").strip(),
                )
                if part
            )
            base_score = _text_similarity_score(normalized_query, text_for_score) if normalized_query else 0.0
            is_anaphoric_event_candidate = episode_id in anaphoric_episode_ids
            if normalized_query and base_score <= 0.0 and not is_anaphoric_event_candidate:
                continue
            boosted_score, boost_meta = _apply_sqlite_ledger_boost(
                base_score=max(base_score, 0.03 if is_anaphoric_event_candidate else (0.01 if not normalized_query else base_score)),
                row_ts_ms=int(episode.get("last_touched_ts_ms") or episode.get("created_ts_ms") or 0),
                now_ts_ms=current,
                row_person_id=str(episode.get("person_id") or "").strip() or None,
                target_person_id=scoped_person_id,
                row_session_id=str(episode.get("session_id") or "").strip() or None,
                target_session_id=scoped_session_id,
            )
            salience = _clamp_unit(episode.get("salience"), default=0.0)
            truth_state = str(episode.get("truth_state") or TRUTH_CANDIDATE).strip().upper()
            truth_boost = 0.08 if truth_state == TRUTH_VERIFIED else (-0.25 if truth_state in {TRUTH_CORRECTED, TRUTH_SUPPRESSED} else 0.0)
            memory_class = _episode_memory_class_from_metadata(episode_metadata)
            episode_domains = _metadata_domain_set(episode_metadata, text=text_for_score)
            episode_kind = str(episode.get("episode_kind") or "").strip()
            action_text = _clean_text(episode.get("action") or episode_metadata.get("action_kind")).lower()
            result_text = _clean_text(episode.get("result")).upper()
            contract_boost = 0.0
            query_kind = _clean_text(getattr(query_plan, "query_kind", "")).lower()
            query_wants_relationship_feedback = _preference_query_wants_relationship_feedback(normalized_query)
            failure_record = _looks_like_recorded_failure_candidate(
                episode_kind=episode_kind,
                action_text=action_text,
                result_text=result_text,
                text=text_for_score,
            )
            correction_fact = _looks_like_correction_fact_candidate(text_for_score)
            correction_echo = _looks_like_correction_question_echo(text_for_score)
            preference_fact = _looks_like_preference_candidate(text_for_score)
            lookup_question_echo = _semantic_statement_is_lookup_question(text_for_score)
            generic_evidence_absence = _looks_like_absence_or_generic_evidence_answer(text_for_score)
            substantive_evidence = _looks_like_substantive_evidence_candidate(
                memory_class=memory_class,
                action_text=action_text,
                text=text_for_score,
            )
            if target_memory_classes and memory_class in target_memory_classes:
                if query_kind == "failure_debug":
                    contract_boost += 0.95 if failure_record and episode_kind != EPISODE_CHAT else -0.45
                elif query_kind == "correction":
                    contract_boost += 0.95 if correction_fact and not correction_echo else -0.65
                elif query_kind == "preference_lookup":
                    if (
                        memory_class == MEMORY_CLASS_RELATIONSHIP_FEEDBACK
                        and query_wants_relationship_feedback
                        and _semantic_is_relationship_feedback(text_for_score)
                    ):
                        contract_boost += 0.85
                    else:
                        contract_boost += 0.95 if preference_fact else 0.45
                elif query_kind == "evidence_review":
                    if generic_evidence_absence or memory_class == MEMORY_CLASS_AUDIT_ANCHOR or not substantive_evidence:
                        contract_boost -= 0.65
                    else:
                        contract_boost += 0.75
                elif query_kind == "open_loop":
                    wants_closed = _query_wants_closed_open_loop(normalized_query)
                    commitment_status = _semantic_commitment_status(re.sub(r"\s+", "", text_for_score).lower())
                    if wants_closed:
                        contract_boost += 0.85 if commitment_status in OPEN_LOOP_TERMINAL_STATES else -0.35
                    else:
                        contract_boost += 0.85 if commitment_status == OPEN_LOOP_OPEN else -0.55
                else:
                    contract_boost += 0.65
            if target_domains and episode_domains.intersection(target_domains):
                if query_kind == "preference_lookup" and memory_class != MEMORY_CLASS_PREFERENCE:
                    contract_boost -= 0.20
                elif query_kind == "evidence_review" and (generic_evidence_absence or memory_class == MEMORY_CLASS_AUDIT_ANCHOR or not substantive_evidence):
                    contract_boost -= 0.20
                else:
                    contract_boost += 0.45
            if query_kind == "failure_debug":
                if not failure_record:
                    contract_boost -= 0.60
                if episode_kind == EPISODE_CHAT:
                    contract_boost -= 0.45
            elif query_kind == "correction":
                if correction_fact and not correction_echo:
                    contract_boost += 0.35
                else:
                    contract_boost -= 0.55
                if correction_echo:
                    contract_boost -= 0.70
                if memory_class != MEMORY_CLASS_CORRECTION:
                    contract_boost -= 0.20
            elif query_kind == "preference_lookup":
                if memory_class == MEMORY_CLASS_RELATIONSHIP_FEEDBACK and _semantic_is_relationship_feedback(text_for_score):
                    if not query_wants_relationship_feedback:
                        contract_boost -= 0.75
                    else:
                        contract_boost += 0.35
                elif memory_class == MEMORY_CLASS_PREFERENCE and preference_fact:
                    contract_boost += 0.35
                elif memory_class == MEMORY_CLASS_PREFERENCE:
                    contract_boost -= 0.45
                elif memory_class in {MEMORY_CLASS_COLLECTION_EVENT, MEMORY_CLASS_TOOL_ACTION, MEMORY_CLASS_CHAT_EXPERIENCE}:
                    contract_boost -= 0.65
                if lookup_question_echo:
                    contract_boost -= 0.85
                if not preference_fact and not (
                    memory_class == MEMORY_CLASS_RELATIONSHIP_FEEDBACK
                    and query_wants_relationship_feedback
                    and _semantic_is_relationship_feedback(text_for_score)
                ):
                    contract_boost -= 0.35
            elif query_kind == "evidence_review":
                if memory_class in {MEMORY_CLASS_STABLE_FACT, MEMORY_CLASS_PERCEPTION_EVIDENCE, MEMORY_CLASS_TOOL_ACTION} and substantive_evidence:
                    contract_boost += 0.30
                if memory_class in {MEMORY_CLASS_AUDIT_ANCHOR, MEMORY_CLASS_CHAT_EXPERIENCE} or generic_evidence_absence or not substantive_evidence:
                    contract_boost -= 0.80
                if target_domains and not episode_domains.intersection(target_domains):
                    contract_boost -= 0.35
            elif query_kind == "open_loop":
                if memory_class == MEMORY_CLASS_COMMITMENT:
                    wants_closed = _query_wants_closed_open_loop(normalized_query)
                    commitment_status = _semantic_commitment_status(re.sub(r"\s+", "", text_for_score).lower())
                    if wants_closed:
                        contract_boost += 0.30 if commitment_status in OPEN_LOOP_TERMINAL_STATES else -0.25
                    else:
                        contract_boost += 0.30 if commitment_status == OPEN_LOOP_OPEN else -0.45
                else:
                    contract_boost -= 0.45
            if query_kind in {"failure_debug", "correction", "preference_lookup", "open_loop", "self_change", "evidence_review"}:
                if memory_class == MEMORY_CLASS_CHAT_EXPERIENCE:
                    contract_boost -= 0.35
                if query_kind == "failure_debug" and episode_kind == EPISODE_CHAT:
                    contract_boost -= 0.25
                if normalized_query and normalized_query in text_for_score and "assistant:" in text_for_score.lower():
                    contract_boost -= 0.25
            anaphoric_boost = 0.0
            if anaphoric_query:
                if is_anaphoric_event_candidate:
                    anaphoric_boost += 0.45
                elif episode_kind == EPISODE_CHAT:
                    anaphoric_boost -= 0.45
                elif episode_kind in _ANAPHORIC_EVENT_KINDS:
                    anaphoric_boost += 0.05
            final_score = round(max(0.0, min(1.45, boosted_score + salience * 0.12 + truth_boost + anaphoric_boost + contract_boost)), 6)
            retention_state = str(episode.get("retention_state") or RETENTION_COLD).strip().upper()
            if retention_state == RETENTION_COLD and normalized_query and final_score < 0.18:
                continue
            evidence_refs = [
                str(ev.get("source_ref") or "").strip()
                for ev in evidence_rows
                if str(ev.get("source_ref") or "").strip()
            ]
            item = {
                "memory_id": episode_id,
                "text": _truncate_text_for_recall(str(episode.get("summary") or text_for_score), limit=700),
                "score": final_score,
                "metadata": {
                    "store_id": "life_episode",
                    "memory_layer": "episodic_life_episode",
                    "episode_id": episode_id,
                    "episode_kind": episode.get("episode_kind"),
                    "thread_id": episode.get("thread_id"),
                    "person_id": episode.get("person_id"),
                    "session_id": episode.get("session_id"),
                    "action": episode.get("action"),
                    "result": episode.get("result"),
                    "truth_state": truth_state,
                    "status": episode.get("status"),
                    "memory_class": memory_class,
                    "recall_grain": _clean_text(episode_metadata.get("recall_grain")).lower(),
                    "domains": sorted(episode_domains),
                    "salience": salience,
                    "base_salience": _clamp_unit(episode.get("base_salience"), default=salience),
                    "memory_strength": _clamp_unit(episode.get("memory_strength"), default=salience),
                    "retention_state": retention_state,
                    "decay_profile_id": episode.get("decay_profile_id"),
                    "last_recalled_ts_ms": int(episode.get("last_recalled_ts_ms") or 0),
                    "recall_count": int(episode.get("recall_count") or 0),
                    "reinforcement_count": int(episode.get("reinforcement_count") or 0),
                    "compressed_into_episode_id": episode.get("compressed_into_episode_id"),
                    "retention_until_ts_ms": int(episode.get("retention_until_ts_ms") or 0),
                    "forget_reason": episode.get("forget_reason"),
                    "created_ts_ms": int(episode.get("created_ts_ms") or 0),
                    "last_touched_ts_ms": int(episode.get("last_touched_ts_ms") or 0),
                    "evidence_count": len(evidence_rows),
                    "evidence_refs": evidence_refs,
                    "evidence": evidence_rows,
                    "episode_metadata": episode_metadata,
                    "corrections": correction_rows,
                    "correction_count": len(correction_rows),
                    "source_ref": str(episode.get("primary_ref") or f"life_episode://{episode_id}"),
                    "evidence_refs_primary": evidence_refs[0] if evidence_refs else str(episode.get("primary_ref") or f"life_episode://{episode_id}"),
                    "score_boost": {
                        **dict(boost_meta or {}),
                        "salience_boost": round(salience * 0.12, 6),
                        "truth_boost": truth_boost,
                        "anaphoric_boost": round(anaphoric_boost, 6),
                        "contract_boost": round(contract_boost, 6),
                    },
                },
            }
            scored.append((final_score, item))
        candidates = [
            item
            for _score, item in sorted(
                scored,
                key=lambda pair: (
                    -round(pair[0], 2),
                    -int(pair[1]["metadata"].get("last_touched_ts_ms") or 0),
                    -pair[0],
                    str(pair[1].get("memory_id") or ""),
                ),
            )[: max(1, int(top_k))]
        ]
        if candidates and not include_suppressed:
            for item in candidates:
                self.reinforce_episode_recall(
                    episode_id=str(item.get("memory_id") or ""),
                    ts_ms=current,
                    amount=0.015,
                )
        return EpisodeRecallPacket(query=normalized_query, candidates=candidates)

    def reinforce_episode_recall(
        self,
        *,
        episode_id: str,
        ts_ms: int | None = None,
        amount: float = 0.015,
    ) -> dict[str, Any]:
        eid = _clean_text(episode_id)
        if not eid:
            return {"status": "SKIPPED", "reason_code": "EMPTY_EPISODE_ID"}
        current = int(ts_ms if ts_ms is not None else now_ts_ms())
        with self.ledger._write_lock:
            with self.ledger._conn:
                row = self.ledger._conn.execute(
                    "SELECT memory_strength, truth_state, retention_state FROM life_episode WHERE episode_id = ?",
                    (eid,),
                ).fetchone()
                if row is None:
                    return {"status": "SKIPPED", "reason_code": "EPISODE_NOT_FOUND", "episode_id": eid}
                current_strength = _clamp_unit(row["memory_strength"], default=0.0)
                next_strength = _clamp_unit(current_strength + max(0.0, float(amount or 0.0)))
                next_state = _retention_state_for_strength(
                    next_strength,
                    truth_state=row["truth_state"],
                    explicit=None,
                )
                self.ledger._conn.execute(
                    """
                    UPDATE life_episode
                    SET
                        last_recalled_ts_ms = ?,
                        recall_count = COALESCE(recall_count, 0) + 1,
                        reinforcement_count = COALESCE(reinforcement_count, 0) + 1,
                        memory_strength = ?,
                        retention_state = ?
                    WHERE episode_id = ?
                    """,
                    (current, next_strength, next_state, eid),
                )
        return {"status": "OK", "episode_id": eid, "memory_strength": next_strength, "retention_state": next_state}

    def compact_episodes(
        self,
        *,
        source_episode_ids: Sequence[str],
        summary: str,
        kept_facts: Sequence[str] | None = None,
        dropped_details: Sequence[str] | None = None,
        ts_ms: int | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        source_ids = [
            _clean_text(item)
            for item in list(source_episode_ids or [])
            if _clean_text(item)
        ]
        if not source_ids:
            return {"status": "SKIPPED", "reason_code": "NO_SOURCE_EPISODES"}
        compact_summary = _clean_text(summary, limit=1200)
        if not compact_summary:
            return {"status": "SKIPPED", "reason_code": "EMPTY_COMPACTION_SUMMARY"}
        created = int(ts_ms if ts_ms is not None else now_ts_ms())
        source_hash = _stable_hash("|".join(sorted(source_ids)), size=24)
        primary_ref = f"compaction://episode/{source_hash}"
        episode = self.upsert_episode(
            episode_kind=EPISODE_REFLECTION,
            evidence_refs=[primary_ref],
            episode_id=f"ep_{_stable_hash('life_compaction:v1:' + primary_ref, size=32)}",
            thread_id=f"compaction://thread/{source_hash}",
            actor_id="memory_sleep_replay",
            action="memory_compaction",
            result="summary_episode",
            primary_ref=primary_ref,
            summary=compact_summary,
            salience=0.42,
            memory_strength=0.42,
            decay_profile_id=DECAY_SLOW,
            retention_state=RETENTION_WARM,
            truth_state=TRUTH_NARRATIVE_ONLY,
            ts_ms=created,
            metadata={
                **dict(metadata or {}),
                "source_episode_ids": source_ids,
                "kept_facts": list(kept_facts or []),
                "dropped_details": list(dropped_details or []),
                "memory_layer": "life_episode",
            },
        )
        compressed_id = str(episode["episode_id"])
        self.attach_evidence(
            episode_id=compressed_id,
            evidence_kind=EVIDENCE_REFLECTION,
            source_ref=primary_ref,
            request_id=primary_ref,
            content=compact_summary,
            payload={
                "source_episode_ids": source_ids,
                "kept_facts": list(kept_facts or []),
                "dropped_details": list(dropped_details or []),
            },
            trust_level=TRUTH_NARRATIVE_ONLY,
            ts_ms=created,
        )
        compaction_id = f"cmp_{_stable_hash('life_compaction_row:v1:' + primary_ref, size=32)}"
        with self.ledger._write_lock:
            with self.ledger._conn:
                self.ledger._conn.execute(
                    """
                    INSERT OR IGNORE INTO life_memory_compaction (
                        compaction_id, source_episode_ids, compressed_episode_id,
                        summary, dropped_details, kept_facts, created_ts_ms, metadata
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        compaction_id,
                        _json_dumps(source_ids),
                        compressed_id,
                        compact_summary,
                        _json_dumps(list(dropped_details or [])),
                        _json_dumps(list(kept_facts or [])),
                        created,
                        _json_dumps(dict(metadata or {})),
                    ),
                )
                self.ledger._conn.executemany(
                    """
                    UPDATE life_episode
                    SET
                        compressed_into_episode_id = ?,
                        retention_state = CASE
                            WHEN COALESCE(memory_strength, 0.0) < 0.18 THEN ?
                            ELSE ?
                        END,
                        forget_reason = COALESCE(forget_reason, ?),
                        last_touched_ts_ms = MAX(COALESCE(last_touched_ts_ms, 0), ?)
                    WHERE episode_id = ?
                    """,
                    [
                        (
                            compressed_id,
                            RETENTION_GONE,
                            RETENTION_COLD,
                            "compressed_into_summary",
                            created,
                            source_id,
                        )
                        for source_id in source_ids
                    ],
                )
        for source_id in source_ids:
            try:
                self.link_episode(
                    source_episode_id=source_id,
                    target_episode_id=compressed_id,
                    link_kind="semantic_child",
                    weight=0.7,
                    ts_ms=created,
                    metadata={"compaction_id": compaction_id},
                )
            except Exception:
                pass
        return {
            "status": "OK",
            "compaction_id": compaction_id,
            "compressed_episode_id": compressed_id,
            "source_count": len(source_ids),
        }

    def apply_forgetting_pass(
        self,
        *,
        now_ts_ms_value: int | None = None,
        batch_limit: int = 500,
        cold_after_ms: int = 7 * 24 * 60 * 60 * 1000,
        gone_after_ms: int = 30 * 24 * 60 * 60 * 1000,
    ) -> dict[str, Any]:
        current = int(now_ts_ms_value if now_ts_ms_value is not None else now_ts_ms())
        semantic_referenced_episode_ids = _semantic_referenced_episode_ids(self.ledger._conn)
        rows = self.ledger._conn.execute(
            """
            SELECT *
            FROM life_episode
            WHERE status = ?
              AND truth_state NOT IN (?, ?)
              AND COALESCE(retention_state, ?) != ?
            ORDER BY last_touched_ts_ms ASC
            LIMIT ?
            """,
            (
                STATUS_ACTIVE,
                TRUTH_SUPPRESSED,
                TRUTH_CORRECTED,
                RETENTION_COLD,
                RETENTION_GONE,
                max(1, int(batch_limit)),
            ),
        ).fetchall()
        updates: list[tuple[float, str, str | None, str]] = []
        counts = {RETENTION_HOT: 0, RETENTION_WARM: 0, RETENTION_COLD: 0, RETENTION_GONE: 0}
        semantic_reference_protected = 0
        gone_reason_backfilled = 0
        for row in rows:
            episode = dict(row)
            eid = str(episode.get("episode_id") or "").strip()
            if not eid:
                continue
            last_touched = int(episode.get("last_touched_ts_ms") or episode.get("created_ts_ms") or current)
            age_ms = max(0, current - last_touched)
            strength = _clamp_unit(episode.get("memory_strength"), default=_clamp_unit(episode.get("salience")))
            protected_until = int(episode.get("retention_until_ts_ms") or 0)
            if protected_until and protected_until > current:
                next_state = _retention_state_for_strength(max(strength, 0.40), truth_state=episode.get("truth_state"))
                counts[next_state] += 1
                updates.append((max(strength, 0.40), next_state, None, eid))
                continue
            days = age_ms / float(24 * 60 * 60 * 1000)
            decayed = _clamp_unit(strength - days * _decay_rate_per_day(episode.get("decay_profile_id")))
            summary = str(episode.get("summary") or "")
            decayed += _importance_keyword_boost(summary) * 0.35
            decayed = _clamp_unit(decayed)
            if age_ms >= gone_after_ms and decayed < 0.18:
                next_state = RETENTION_GONE
                reason = "low_strength_decay"
            elif age_ms >= cold_after_ms and decayed < 0.35:
                next_state = RETENTION_COLD
                reason = "decayed_to_cold"
            else:
                next_state = _retention_state_for_strength(decayed, truth_state=episode.get("truth_state"))
                reason = None
            if eid in semantic_referenced_episode_ids and next_state == RETENTION_GONE:
                next_state = RETENTION_COLD
                decayed = max(decayed, 0.18)
                reason = "semantic_reference_protected"
                semantic_reference_protected += 1
            counts[next_state] += 1
            updates.append((decayed, next_state, reason, eid))
        if updates:
            with self.ledger._write_lock:
                with self.ledger._conn:
                    self.ledger._conn.executemany(
                        """
                        UPDATE life_episode
                        SET
                            memory_strength = ?,
                            retention_state = ?,
                            forget_reason = COALESCE(?, forget_reason)
                        WHERE episode_id = ?
                        """,
                        updates,
                    )
        with self.ledger._write_lock:
            with self.ledger._conn:
                cursor = self.ledger._conn.execute(
                    """
                    UPDATE life_episode
                    SET forget_reason = ?
                    WHERE COALESCE(retention_state, '') = ?
                      AND (forget_reason IS NULL OR TRIM(forget_reason) = '')
                    """,
                    ("historical_low_value_retired", RETENTION_GONE),
                )
                gone_reason_backfilled = int(cursor.rowcount or 0)
        return {
            "status": "OK",
            "scanned": len(rows),
            "updated": len(updates),
            "counts": counts,
            "semantic_reference_protected": semantic_reference_protected,
            "gone_reason_backfilled": gone_reason_backfilled,
        }

    def backfill_memory_policy(
        self,
        *,
        batch_limit: int = 5000,
        include_existing: bool = False,
        dry_run: bool = False,
        sample_limit: int = 25,
    ) -> dict[str, Any]:
        """Backfill management policy metadata for historical episodes."""

        rows = self.ledger._conn.execute(
            """
            SELECT
                ep.*,
                GROUP_CONCAT(DISTINCT ev.source_ref) AS evidence_source_refs
            FROM life_episode ep
            LEFT JOIN life_evidence ev ON ev.episode_id = ep.episode_id
            WHERE (? OR ep.metadata IS NULL OR ep.metadata NOT LIKE '%"memory_policy"%')
            GROUP BY ep.episode_id
            ORDER BY ep.created_ts_ms ASC
            LIMIT ?
            """,
            (1 if include_existing else 0, max(1, int(batch_limit))),
        ).fetchall()
        updates: list[tuple[str, str, str]] = []
        changes: list[dict[str, Any]] = []
        skipped_existing = 0
        class_counts: dict[str, int] = {}
        transition_counts: dict[str, int] = {}
        semantic_transition_counts: dict[str, int] = {}
        synthetic_change_count = 0
        for row in rows:
            episode = dict(row)
            episode_id = _clean_text(episode.get("episode_id"))
            if not episode_id:
                continue
            metadata = _json_loads(episode.get("metadata"), fallback={})
            if not isinstance(metadata, dict):
                metadata = {}
            existing_policy = metadata.get("memory_policy")
            if isinstance(existing_policy, Mapping) and existing_policy.get("schema") and not include_existing:
                skipped_existing += 1
                continue
            existing_policy_map = dict(existing_policy) if isinstance(existing_policy, Mapping) else {}
            old_memory_class = _clean_text(
                metadata.get("memory_class") or existing_policy_map.get("memory_class") or "unknown"
            )
            old_recall_grain = _clean_text(
                metadata.get("recall_grain") or existing_policy_map.get("recall_grain") or "unknown"
            )
            old_semantic_candidate = bool(existing_policy_map.get("semantic_candidate"))
            evidence_refs = list(metadata.get("evidence_refs") or [])
            for ref in str(episode.get("evidence_source_refs") or "").split(","):
                cleaned = _clean_text(ref)
                if cleaned:
                    evidence_refs.append(cleaned)
            policy = _classify_life_memory_policy(
                episode_kind=_clean_text(episode.get("episode_kind")),
                action=_clean_text(episode.get("action")),
                result=_clean_text(episode.get("result")),
                summary=_clean_text(episode.get("summary"), limit=1200),
                metadata=metadata,
                evidence_refs=list(dict.fromkeys(evidence_refs)),
                truth_state=_clean_text(episode.get("truth_state")),
            )
            next_metadata = _with_memory_policy_metadata(
                metadata,
                policy=policy,
                explicit_decay_profile_id=None,
            )
            next_decay = _clean_text(next_metadata.get("decay_profile_id")) or _clean_text(episode.get("decay_profile_id")) or DECAY_NORMAL
            memory_class = _clean_text(policy.get("memory_class")) or "unknown"
            class_counts[memory_class] = class_counts.get(memory_class, 0) + 1
            new_recall_grain = _clean_text(policy.get("recall_grain")) or "unknown"
            new_semantic_candidate = bool(policy.get("semantic_candidate"))
            old_decay = _clean_text(episode.get("decay_profile_id")) or DECAY_NORMAL
            metadata_changed = (_json_dumps(next_metadata) or "{}") != (_json_dumps(metadata) or "{}")
            decay_changed = next_decay != old_decay
            if not metadata_changed and not decay_changed:
                continue
            transition_key = f"{old_memory_class or 'unknown'}->{memory_class}"
            transition_counts[transition_key] = transition_counts.get(transition_key, 0) + 1
            semantic_key = f"{str(old_semantic_candidate).lower()}->{str(new_semantic_candidate).lower()}"
            semantic_transition_counts[semantic_key] = semantic_transition_counts.get(semantic_key, 0) + 1
            if _is_synthetic_life_record(episode, metadata):
                synthetic_change_count += 1
            updates.append((_json_dumps(next_metadata) or "{}", next_decay, episode_id))
            if len(changes) < max(0, int(sample_limit)):
                changes.append(
                    {
                        "episode_id": episode_id,
                        "episode_kind": _clean_text(episode.get("episode_kind")),
                        "action": _clean_text(episode.get("action")),
                        "old_memory_class": old_memory_class,
                        "new_memory_class": memory_class,
                        "old_recall_grain": old_recall_grain,
                        "new_recall_grain": new_recall_grain,
                        "old_semantic_candidate": old_semantic_candidate,
                        "new_semantic_candidate": new_semantic_candidate,
                        "old_decay_profile_id": old_decay,
                        "new_decay_profile_id": next_decay,
                        "summary": _clean_text(episode.get("summary"), limit=240),
                    }
                )
        if updates and not dry_run:
            with self.ledger._write_lock:
                with self.ledger._conn:
                    self.ledger._conn.executemany(
                        """
                        UPDATE life_episode
                        SET metadata = ?, decay_profile_id = ?
                        WHERE episode_id = ?
                        """,
                        updates,
                    )
        return {
            "status": "OK",
            "dry_run": bool(dry_run),
            "scanned": len(rows),
            "updated": 0 if dry_run else len(updates),
            "would_update": len(updates),
            "change_count": len(updates),
            "skipped_existing": skipped_existing,
            "class_counts": dict(sorted(class_counts.items())),
            "transition_counts": dict(sorted(transition_counts.items())),
            "semantic_transition_counts": dict(sorted(semantic_transition_counts.items())),
            "synthetic_change_count": synthetic_change_count,
            "changes": changes,
        }

    def _prune_collection_items_for_scope_locked(self, *, collection_id: str) -> None:
        collection = self.ledger._conn.execute(
            "SELECT scope_key FROM life_memory_collection WHERE collection_id = ?",
            (_clean_text(collection_id),),
        ).fetchone()
        if collection is None:
            return
        days = _collection_scope_window_days(collection["scope_key"])
        if days <= 0:
            return
        anchor_row = self.ledger._conn.execute(
            """
            SELECT MAX(created_ts_ms) AS max_ts
            FROM life_memory_collection_item
            WHERE collection_id = ?
            """,
            (_clean_text(collection_id),),
        ).fetchone()
        anchor_ts = int(anchor_row["max_ts"] or 0) if anchor_row is not None else 0
        if anchor_ts <= 0:
            return
        anchor_date = datetime.fromtimestamp(anchor_ts / 1000.0, tz=_LOCAL_MEMORY_TZ).date()
        cutoff_date = anchor_date - timedelta(days=max(0, int(days) - 1))
        cutoff_dt = datetime(cutoff_date.year, cutoff_date.month, cutoff_date.day, tzinfo=_LOCAL_MEMORY_TZ)
        cutoff = int(cutoff_dt.timestamp() * 1000)
        self.ledger._conn.execute(
            """
            DELETE FROM life_memory_collection_item
            WHERE collection_id = ?
              AND created_ts_ms < ?
            """,
            (_clean_text(collection_id), cutoff),
        )

    def _refresh_entity_mention_count_locked(self, *, entity_id: str) -> None:
        eid = _clean_text(entity_id)
        if not eid:
            return
        row = self.ledger._conn.execute(
            """
            SELECT COUNT(1) AS mention_count, MAX(created_ts_ms) AS last_seen_ts_ms
            FROM life_memory_entity_mention
            WHERE entity_id = ?
            """,
            (eid,),
        ).fetchone()
        mention_count = int(row["mention_count"] or 0) if row is not None else 0
        last_seen_ts_ms = int(row["last_seen_ts_ms"] or 0) if row is not None else 0
        self.ledger._conn.execute(
            """
            UPDATE life_memory_entity
            SET
                mention_count = ?,
                last_seen_ts_ms = MAX(COALESCE(last_seen_ts_ms, 0), ?)
            WHERE entity_id = ?
            """,
            (mention_count, last_seen_ts_ms, eid),
        )

    def _index_entity_for_collection_item(
        self,
        *,
        collection_id: str,
        collection_item_id: str,
        episode_id: str,
        item_key: str,
        item_title: str | None,
        item_summary: str | None,
        source_ref: str | None,
        evidence_ref: str | None,
        position: int,
        ts_ms: int,
        metadata: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        collection = self.ledger._conn.execute(
            "SELECT collection_kind, domain, scope_key FROM life_memory_collection WHERE collection_id = ?",
            (_clean_text(collection_id),),
        ).fetchone()
        if collection is None:
            return {"status": "SKIPPED", "reason_code": "COLLECTION_NOT_FOUND"}
        scope_key = _clean_text(collection["scope_key"])
        if not scope_key.startswith(f"{COLLECTION_SCOPE_DELIVERY}:"):
            return {"status": "SKIPPED", "reason_code": "ENTITY_INDEX_ONLY_DELIVERY_ITEMS"}
        item_payload = dict(metadata or {})
        title = _clean_text(item_title, limit=300) or _collection_item_title(item_payload)
        if not title:
            return {"status": "SKIPPED", "reason_code": "EMPTY_ENTITY_ITEM_TITLE"}
        domain = _clean_text(collection["domain"])
        source = _clean_text(source_ref) or _collection_item_source_ref(item_payload)
        canonical_title = _collection_item_resolved_canonical_title({"title": title, "item_title": title, **item_payload})
        canonical_key = _collection_item_canonical_key(canonical_title) or _clean_text(item_payload.get("canonical_key"))
        if canonical_title:
            item_payload["canonical_title"] = canonical_title
        if canonical_key:
            item_payload["canonical_key"] = canonical_key
        if domain == DOMAIN_LIGHTNOVEL_RECOMMENDATION and canonical_key:
            normalized_key = f"title:{canonical_key}"
        else:
            normalized_key = _entity_key_from_item(title=title, source_ref=source)
        entity_kind = ENTITY_KIND_BOOK if domain == DOMAIN_LIGHTNOVEL_RECOMMENDATION else ENTITY_KIND_ITEM
        entity = self.upsert_entity(
            entity_kind=entity_kind,
            canonical_name=canonical_title or title,
            normalized_key=normalized_key,
            domain=domain,
            salience=0.36,
            memory_strength=0.42,
            retention_state=RETENTION_WARM,
            ts_ms=ts_ms,
            metadata={
                "source_ref": source,
                "first_item_key": _clean_text(item_key),
                "domain": domain,
                "canonical_title": canonical_title or title,
                "canonical_key": canonical_key,
                "source_collection_scope": scope_key,
                "memory_layer": "life_memory_entity",
            },
        )
        if entity.get("status") != "OK":
            return entity
        mention = self.attach_entity_mention(
            entity_id=str(entity["entity_id"]),
            episode_id=episode_id,
            collection_id=collection_id,
            collection_item_id=collection_item_id,
            source_ref=source,
            evidence_ref=evidence_ref or source,
            position=position,
            ts_ms=ts_ms,
            metadata={
                **item_payload,
                "item_key": _clean_text(item_key),
                "item_title": title,
                "item_summary": _clean_text(item_summary, limit=900),
                "collection_scope_key": scope_key,
                "memory_layer": "life_memory_entity_mention",
            },
        )
        return {**entity, "mention": mention}

    def _refresh_collection_item_count_locked(self, *, collection_id: str, ts_ms: int) -> None:
        self.ledger._conn.execute(
            """
            UPDATE life_memory_collection
            SET
                item_count = (
                    SELECT COUNT(1)
                    FROM life_memory_collection_item
                    WHERE collection_id = ?
                ),
                updated_ts_ms = MAX(COALESCE(updated_ts_ms, 0), ?)
            WHERE collection_id = ?
            """,
            (collection_id, int(ts_ms), collection_id),
        )
        collection = self.ledger._conn.execute(
            "SELECT * FROM life_memory_collection WHERE collection_id = ?",
            (collection_id,),
        ).fetchone()
        if collection is None:
            return
        items = self.ledger._conn.execute(
            """
            SELECT *
            FROM life_memory_collection_item
            WHERE collection_id = ?
            ORDER BY created_ts_ms ASC, position ASC, item_title ASC
            """,
            (collection_id,),
        ).fetchall()
        summary = _collection_summary_text_for_collection(
            collection=dict(collection),
            items=[dict(item) for item in items],
        )
        if summary:
            self.ledger._conn.execute(
                """
                UPDATE life_memory_collection
                SET summary = ?, updated_ts_ms = MAX(COALESCE(updated_ts_ms, 0), ?)
                WHERE collection_id = ?
                """,
                (summary, int(ts_ms), collection_id),
            )

    def _fetch_collection_rows_for_plan(
        self,
        *,
        plan: Any,
        person_id: str | None,
        session_id: str | None,
        now_ts_ms_value: int,
        limit: int,
    ) -> list[Any]:
        base_clauses = ["COALESCE(retention_state, ?) != ?"]
        base_params: list[Any] = [RETENTION_WARM, RETENTION_GONE]
        domains = set(str(item or "").strip() for item in getattr(plan, "target_domains", ()) if str(item or "").strip())
        if DOMAIN_LIGHTNOVEL_RECOMMENDATION in domains:
            domains.add(DOMAIN_RECOMMENDATION)
        if DOMAIN_RECOMMENDATION in domains:
            domains.add(DOMAIN_LIGHTNOVEL_RECOMMENDATION)
        if domains:
            placeholders = ", ".join("?" for _ in domains)
            base_clauses.append(f"COALESCE(domain, '') IN ({placeholders})")
            base_params.extend(sorted(domains))
        scope_keys = _scope_keys_for_collection_plan(plan=plan, now_ts_ms_value=now_ts_ms_value)
        if scope_keys:
            exact_count = _plan_requires_exact_collection_count(plan)
            dated_daily_keys = [
                key
                for key in scope_keys
                if _clean_text(key).startswith(f"{COLLECTION_SCOPE_DAILY}:")
            ]
            if exact_count and dated_daily_keys:
                daily_placeholders = ", ".join("?" for _ in dated_daily_keys)
                day_ranges: list[tuple[int, int]] = []
                for key in dated_daily_keys:
                    day_key = _clean_text(key).split(":", 1)[1]
                    try:
                        day_dt = datetime.strptime(day_key, "%Y-%m-%d").replace(tzinfo=_LOCAL_MEMORY_TZ)
                    except ValueError:
                        continue
                    start_ms = int(day_dt.timestamp() * 1000)
                    end_ms = int((day_dt + timedelta(days=1)).timestamp() * 1000)
                    day_ranges.append((start_ms, end_ms))
                if day_ranges:
                    range_clauses: list[str] = []
                    range_params: list[Any] = []
                    for start_ms, end_ms in day_ranges:
                        range_clauses.append("(COALESCE(scope_key, '') LIKE ? AND COALESCE(created_ts_ms, 0) >= ? AND COALESCE(created_ts_ms, 0) < ?)")
                        range_params.extend([f"{COLLECTION_SCOPE_DELIVERY}:%", start_ms, end_ms])
                    base_clauses.append(
                        f"(COALESCE(scope_key, '') IN ({daily_placeholders}) OR {' OR '.join(range_clauses)})"
                    )
                    base_params.extend([*dated_daily_keys, *range_params])
                else:
                    placeholders = ", ".join("?" for _ in scope_keys)
                    base_clauses.append(f"COALESCE(scope_key, '') IN ({placeholders})")
                    base_params.extend(scope_keys)
            else:
                placeholders = ", ".join("?" for _ in scope_keys)
                base_clauses.append(f"COALESCE(scope_key, '') IN ({placeholders})")
                base_params.extend(scope_keys)
        identity_clauses: list[str] = []
        identity_params: list[Any] = []
        if session_id:
            if person_id:
                aliases = _owner_alias_person_ids(person_id)
                placeholders = ", ".join("?" for _ in aliases)
                identity_clauses.append(
                    f"(COALESCE(session_id, '') = ? OR COALESCE(session_id, '') = '' OR COALESCE(person_id, '') IN ({placeholders}))"
                )
                identity_params.extend([session_id, *aliases])
            else:
                identity_clauses.append("(COALESCE(session_id, '') = ? OR COALESCE(session_id, '') = '')")
                identity_params.append(session_id)
        elif person_id:
            identity_clauses.append("(COALESCE(person_id, '') = ? OR COALESCE(person_id, '') = '')")
            identity_params.append(person_id)

        def _query(extra_clauses: Sequence[str], extra_params: Sequence[Any]) -> list[Any]:
            clauses = [*base_clauses, *list(extra_clauses or [])]
            params = [*base_params, *list(extra_params or [])]
            return list(
                self.ledger._conn.execute(
                    f"""
                    SELECT *
                    FROM life_memory_collection
                    WHERE {' AND '.join(clauses)}
                    ORDER BY updated_ts_ms DESC, memory_strength DESC
                    LIMIT ?
                    """,
                    [*params, max(1, int(limit))],
                ).fetchall()
            )

        rows = _query(identity_clauses, identity_params)
        if rows or not (session_id or person_id):
            return rows
        owner_aliases = _owner_alias_person_ids(person_id)
        placeholders = ", ".join("?" for _ in owner_aliases)
        return _query(
            [f"COALESCE(person_id, '') IN ({placeholders})"],
            owner_aliases,
        )

    def _list_collection_items(self, *, collection_id: str, limit: int = 24) -> list[dict[str, Any]]:
        rows = self.ledger._conn.execute(
            """
            SELECT *
            FROM life_memory_collection_item
            WHERE collection_id = ?
            ORDER BY created_ts_ms ASC, position ASC, item_title ASC
            LIMIT ?
            """,
            (_clean_text(collection_id), max(1, int(limit))),
        ).fetchall()
        items: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item.pop("id", None)
            metadata = _json_loads(item.get("metadata"), fallback={})
            if isinstance(metadata, dict):
                for key, value in metadata.items():
                    item.setdefault(key, value)
            item["metadata"] = metadata if isinstance(metadata, dict) else {}
            items.append(item)
        return items

    def _list_entity_mentions(
        self,
        *,
        entity_id: str,
        person_id: str | None = None,
        session_id: str | None = None,
        limit: int = 12,
    ) -> list[dict[str, Any]]:
        clauses = ["m.entity_id = ?"]
        params: list[Any] = [_clean_text(entity_id)]
        scoped_session = _clean_text(session_id)
        scoped_person = _clean_text(person_id)
        if scoped_session:
            if scoped_person:
                aliases = _owner_alias_person_ids(scoped_person)
                placeholders = ", ".join("?" for _ in aliases)
                clauses.append(
                    f"(COALESCE(ep.session_id, '') = ? OR COALESCE(ep.session_id, '') = '' OR COALESCE(ep.person_id, '') IN ({placeholders}))"
                )
                params.extend([scoped_session, *aliases])
            else:
                clauses.append("(COALESCE(ep.session_id, '') = ? OR COALESCE(ep.session_id, '') = '')")
                params.append(scoped_session)
        elif scoped_person:
            aliases = _owner_alias_person_ids(scoped_person)
            placeholders = ", ".join("?" for _ in aliases)
            clauses.append(f"(COALESCE(ep.person_id, '') IN ({placeholders}) OR COALESCE(ep.person_id, '') = '')")
            params.extend(aliases)
        rows = self.ledger._conn.execute(
            f"""
            SELECT
                m.*,
                i.item_title,
                i.item_summary,
                i.source_ref AS item_source_ref,
                i.evidence_ref AS item_evidence_ref,
                i.metadata AS item_metadata,
                c.scope_key,
                c.domain AS collection_domain,
                ep.person_id AS episode_person_id,
                ep.session_id AS episode_session_id,
                ep.created_ts_ms AS episode_created_ts_ms
            FROM life_memory_entity_mention m
            LEFT JOIN life_memory_collection_item i ON i.collection_item_id = m.collection_item_id
            LEFT JOIN life_memory_collection c ON c.collection_id = m.collection_id
            LEFT JOIN life_episode ep ON ep.episode_id = m.episode_id
            WHERE {' AND '.join(clauses)}
            ORDER BY m.created_ts_ms DESC, m.position ASC
            LIMIT ?
            """,
            [*params, max(1, int(limit))],
        ).fetchall()
        mentions: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item.pop("id", None)
            metadata = _json_loads(item.get("metadata"), fallback={})
            item_metadata = _json_loads(item.get("item_metadata"), fallback={})
            item["metadata"] = metadata if isinstance(metadata, dict) else {}
            item["item_metadata"] = item_metadata if isinstance(item_metadata, dict) else {}
            mentions.append(item)
        return mentions

    def _list_evidence(self, *, episode_id: str) -> list[dict[str, Any]]:
        rows = self.ledger._conn.execute(
            """
            SELECT *
            FROM life_evidence
            WHERE episode_id = ?
            ORDER BY created_ts_ms ASC
            """,
            (episode_id,),
        ).fetchall()
        items: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item.pop("id", None)
            payload = _json_loads(item.get("payload"), fallback={})
            item["payload"] = payload if isinstance(payload, dict) else {}
            items.append(item)
        return items

    def _list_corrections(self, *, episode_id: str) -> list[dict[str, Any]]:
        rows = self.ledger._conn.execute(
            """
            SELECT *
            FROM life_memory_correction
            WHERE episode_id = ?
            ORDER BY created_ts_ms ASC
            """,
            (episode_id,),
        ).fetchall()
        items: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item.pop("id", None)
            metadata = _json_loads(item.get("metadata"), fallback={})
            item["metadata"] = metadata if isinstance(metadata, dict) else {}
            items.append(item)
        return items


class LifeMemoryConsolidator:
    """Build semantic candidates only from trusted life episodes."""

    def __init__(self, *, store: LifeMemoryStore) -> None:
        self.store = store

    def build_semantic_candidates(self, *, limit: int = 20, verified_only: bool = False) -> list[dict[str, Any]]:
        truth_states = (TRUTH_VERIFIED,) if verified_only else (TRUTH_VERIFIED, TRUTH_NARRATIVE_ONLY)
        placeholders = ", ".join("?" for _ in truth_states)
        rows = self.store.ledger._conn.execute(
            f"""
            SELECT *
            FROM life_episode
            WHERE truth_state IN ({placeholders})
              AND status = ?
              AND COALESCE(retention_state, ?) != ?
            ORDER BY memory_strength DESC, salience DESC, last_touched_ts_ms DESC
            LIMIT ?
            """,
            (*truth_states, STATUS_ACTIVE, RETENTION_COLD, RETENTION_GONE, max(1, int(limit))),
        ).fetchall()
        candidates: list[dict[str, Any]] = []
        for row in rows:
            episode = dict(row)
            metadata = _episode_metadata_dict(episode)
            if _is_synthetic_life_record(episode, metadata):
                continue
            summary = _clean_text(episode.get("summary"), limit=1200)
            if not summary:
                continue
            candidates.append(
                {
                    "episode_id": episode.get("episode_id"),
                    "episode_kind": episode.get("episode_kind"),
                    "action": episode.get("action"),
                    "result": episode.get("result"),
                    "summary": summary,
                    "salience": _clamp_unit(episode.get("salience")),
                    "memory_strength": _clamp_unit(episode.get("memory_strength")),
                    "retention_state": episode.get("retention_state"),
                    "source_ref": episode.get("primary_ref"),
                    "truth_state": episode.get("truth_state"),
                    "person_id": episode.get("person_id"),
                    "session_id": episode.get("session_id"),
                    "metadata": metadata,
                }
            )
        return candidates

    def build_semantic_proposals(self, *, limit: int = 80, max_records: int = 24) -> list[dict[str, Any]]:
        """Extract durable semantic facts from trusted episodes and collections."""

        requested_limit = max(1, int(limit))
        requested_records = max(1, int(max_records))
        general_fetch_limit = min(max(requested_limit * 4, requested_records * 10, 120), 2000)
        priority_fetch_limit = min(max(requested_limit * 12, requested_records * 24, 240), 5000)
        rows = self.store.ledger._conn.execute(
            """
            SELECT *
            FROM life_episode
            WHERE truth_state = ?
              AND status = ?
              AND COALESCE(retention_state, ?) != ?
            ORDER BY memory_strength DESC, salience DESC, last_touched_ts_ms DESC
            LIMIT ?
            """,
            (TRUTH_VERIFIED, STATUS_ACTIVE, RETENTION_COLD, RETENTION_GONE, general_fetch_limit),
        ).fetchall()
        memory_class_like_patterns = [
            pattern
            for item in _SEMANTIC_PRIORITY_MEMORY_CLASSES
            for pattern in (f'%"memory_class": "{item}"%', f'%"memory_class":"{item}"%')
        ]
        priority_rows = self.store.ledger._conn.execute(
            f"""
            SELECT *
            FROM life_episode
            WHERE truth_state = ?
              AND status = ?
              AND COALESCE(retention_state, ?) != ?
              AND (
                metadata LIKE '%"semantic_candidate": true%'
                OR metadata LIKE '%"semantic_candidate":true%'
                OR {" OR ".join("metadata LIKE ?" for _ in memory_class_like_patterns)}
              )
            ORDER BY last_touched_ts_ms DESC, memory_strength DESC, salience DESC
            LIMIT ?
            """,
            (
                TRUTH_VERIFIED,
                STATUS_ACTIVE,
                RETENTION_COLD,
                RETENTION_GONE,
                *memory_class_like_patterns,
                priority_fetch_limit,
            ),
        ).fetchall()
        proposals_by_key: dict[str, dict[str, Any]] = {}
        merged_rows: list[Any] = []
        seen_episode_ids: set[str] = set()
        for row in [*priority_rows, *rows]:
            episode = dict(row)
            metadata = _episode_metadata_dict(episode)
            if _is_synthetic_life_record(episode, metadata):
                continue
            episode_id = _clean_text(episode.get("episode_id"))
            if not episode_id or episode_id in seen_episode_ids:
                continue
            seen_episode_ids.add(episode_id)
            merged_rows.append(row)
        for row in merged_rows:
            episode = dict(row)
            episode_id = _clean_text(episode.get("episode_id"))
            if not episode_id:
                continue
            evidence_rows = self.store._list_evidence(episode_id=episode_id)
            for proposal in _semantic_proposals_from_episode(episode, evidence_rows):
                key = _clean_text(proposal.get("semantic_key"))
                if not key:
                    continue
                existing = proposals_by_key.get(key)
                if existing is None:
                    proposals_by_key[key] = proposal
                    continue
                if _semantic_proposals_are_same_collection_scope(existing, proposal):
                    _merge_collection_semantic_proposal(existing, proposal)
                    continue
                existing["summary"] = _merge_semantic_summaries(existing.get("summary"), proposal.get("summary"))
                existing_sources = list(existing.get("source_episode_ids") or [])
                for source_id in list(proposal.get("source_episode_ids") or []):
                    if source_id not in existing_sources:
                        existing_sources.append(source_id)
                existing["source_episode_ids"] = existing_sources
                existing["confidence"] = max(_clamp_unit(existing.get("confidence")), _clamp_unit(proposal.get("confidence")))
                existing["source_refs"] = list(dict.fromkeys([*list(existing.get("source_refs") or []), *list(proposal.get("source_refs") or [])]))
                existing["tags"] = list(dict.fromkeys([*list(existing.get("tags") or []), *list(proposal.get("tags") or [])]))
                existing["source_collection_ids"] = list(
                    dict.fromkeys(
                        [
                            *list(existing.get("source_collection_ids") or []),
                            *list(proposal.get("source_collection_ids") or []),
                        ]
                    )
                )
        for proposal in self._build_collection_semantic_proposals(limit=limit):
            key = _clean_text(proposal.get("semantic_key"))
            if not key:
                continue
            existing = proposals_by_key.get(key)
            if existing is None:
                proposals_by_key[key] = proposal
                continue
            if _semantic_proposals_are_same_collection_scope(existing, proposal):
                _merge_collection_semantic_proposal(existing, proposal)
                continue
            existing["summary"] = _merge_semantic_summaries(existing.get("summary"), proposal.get("summary"))
            existing["confidence"] = max(_clamp_unit(existing.get("confidence")), _clamp_unit(proposal.get("confidence")))
            existing["source_refs"] = list(dict.fromkeys([*list(existing.get("source_refs") or []), *list(proposal.get("source_refs") or [])]))
            existing["tags"] = list(dict.fromkeys([*list(existing.get("tags") or []), *list(proposal.get("tags") or [])]))
            existing["source_episode_ids"] = list(
                dict.fromkeys([*list(existing.get("source_episode_ids") or []), *list(proposal.get("source_episode_ids") or [])])
            )
            existing["source_collection_ids"] = list(
                dict.fromkeys(
                    [
                        *list(existing.get("source_collection_ids") or []),
                        *list(proposal.get("source_collection_ids") or []),
                    ]
                )
            )
        for proposal in self._build_entity_semantic_proposals(limit=limit):
            key = _clean_text(proposal.get("semantic_key"))
            if not key:
                continue
            existing = proposals_by_key.get(key)
            if existing is None:
                proposals_by_key[key] = proposal
                continue
            existing["summary"] = _merge_semantic_summaries(existing.get("summary"), proposal.get("summary"))
            existing["confidence"] = max(_clamp_unit(existing.get("confidence")), _clamp_unit(proposal.get("confidence")))
            existing["source_refs"] = list(dict.fromkeys([*list(existing.get("source_refs") or []), *list(proposal.get("source_refs") or [])]))
            existing["tags"] = list(dict.fromkeys([*list(existing.get("tags") or []), *list(proposal.get("tags") or [])]))
            existing["source_episode_ids"] = list(
                dict.fromkeys([*list(existing.get("source_episode_ids") or []), *list(proposal.get("source_episode_ids") or [])])
            )
            existing["source_collection_ids"] = list(
                dict.fromkeys(
                    [
                        *list(existing.get("source_collection_ids") or []),
                        *list(proposal.get("source_collection_ids") or []),
                    ]
                )
            )
            existing["source_entity_ids"] = list(
                dict.fromkeys([*list(existing.get("source_entity_ids") or []), *list(proposal.get("source_entity_ids") or [])])
            )
        for proposal in self._build_correction_semantic_proposals(limit=limit):
            key = _clean_text(proposal.get("semantic_key"))
            if not key:
                continue
            existing = proposals_by_key.get(key)
            if existing is None:
                proposals_by_key[key] = proposal
                continue
            existing["summary"] = _merge_semantic_summaries(existing.get("summary"), proposal.get("summary"))
            existing["confidence"] = max(_clamp_unit(existing.get("confidence")), _clamp_unit(proposal.get("confidence")))
            existing["source_refs"] = list(dict.fromkeys([*list(existing.get("source_refs") or []), *list(proposal.get("source_refs") or [])]))
            existing["tags"] = list(dict.fromkeys([*list(existing.get("tags") or []), *list(proposal.get("tags") or [])]))
            existing["source_episode_ids"] = list(
                dict.fromkeys([*list(existing.get("source_episode_ids") or []), *list(proposal.get("source_episode_ids") or [])])
            )
            existing["source_correction_ids"] = list(
                dict.fromkeys([*list(existing.get("source_correction_ids") or []), *list(proposal.get("source_correction_ids") or [])])
            )
        for proposal in self._build_action_pattern_semantic_proposals(limit=limit):
            key = _clean_text(proposal.get("semantic_key"))
            if not key:
                continue
            existing = proposals_by_key.get(key)
            if existing is None:
                proposals_by_key[key] = proposal
                continue
            existing["summary"] = _merge_semantic_summaries(existing.get("summary"), proposal.get("summary"))
            existing["confidence"] = max(_clamp_unit(existing.get("confidence")), _clamp_unit(proposal.get("confidence")))
            existing["source_refs"] = list(dict.fromkeys([*list(existing.get("source_refs") or []), *list(proposal.get("source_refs") or [])]))
            existing["tags"] = list(dict.fromkeys([*list(existing.get("tags") or []), *list(proposal.get("tags") or [])]))
            existing["source_episode_ids"] = list(
                dict.fromkeys([*list(existing.get("source_episode_ids") or []), *list(proposal.get("source_episode_ids") or [])])
            )
            existing["source_collection_ids"] = list(
                dict.fromkeys(
                    [
                        *list(existing.get("source_collection_ids") or []),
                        *list(proposal.get("source_collection_ids") or []),
                    ]
                )
            )
        proposals = sorted(
            proposals_by_key.values(),
            key=lambda item: (
                _semantic_kind_priority(_clean_text(item.get("semantic_kind"))),
                -_clamp_unit(item.get("confidence")),
                str(item.get("summary") or ""),
            ),
        )
        return proposals[: max(1, int(max_records))]

    def semantic_growth_diagnostics(self, *, limit: int = 500) -> dict[str, Any]:
        """Explain why trusted episodes did or did not become semantic memories."""

        rows = self.store.ledger._conn.execute(
            """
            SELECT *
            FROM life_episode
            WHERE truth_state = ?
              AND status = ?
            ORDER BY memory_strength DESC, salience DESC, last_touched_ts_ms DESC
            LIMIT ?
            """,
            (TRUTH_VERIFIED, STATUS_ACTIVE, max(1, int(limit))),
        ).fetchall()
        class_counts: dict[str, int] = {}
        synthetic_counts: dict[str, int] = {}
        candidate_counts: dict[str, int] = {}
        proposal_counts: dict[str, int] = {}
        skip_counts: dict[str, int] = {}
        samples: list[dict[str, Any]] = []
        for row in rows:
            episode = dict(row)
            metadata = _episode_metadata_dict(episode)
            memory_class = _episode_memory_class_from_metadata(metadata) or "unclassified"
            class_counts[memory_class] = class_counts.get(memory_class, 0) + 1
            policy = metadata.get("memory_policy") if isinstance(metadata.get("memory_policy"), Mapping) else {}
            semantic_candidate = bool(policy.get("semantic_candidate")) or memory_class in _SEMANTIC_PRIORITY_MEMORY_CLASSES
            if not semantic_candidate:
                continue
            candidate_counts[memory_class] = candidate_counts.get(memory_class, 0) + 1
            reason = ""
            if _is_synthetic_life_record(episode, metadata):
                reason = "synthetic_source"
                synthetic_counts[memory_class] = synthetic_counts.get(memory_class, 0) + 1
                proposals: list[dict[str, Any]] = []
                texts: list[str] = []
            elif _clean_text(episode.get("retention_state")).upper() == RETENTION_GONE:
                reason = "retention_gone"
                proposals = []
                texts = []
            else:
                evidence_rows = self.store._list_evidence(episode_id=_clean_text(episode.get("episode_id")))
                texts = _semantic_source_texts(episode, evidence_rows)
                proposals = _semantic_proposals_from_episode(episode, evidence_rows)
                if not texts:
                    reason = "no_source_text"
                elif not proposals:
                    reason = _semantic_skip_reason_for_texts(texts, episode=episode)
            if proposals:
                for proposal in proposals:
                    kind = _clean_text(proposal.get("semantic_kind")) or "unknown"
                    proposal_counts[kind] = proposal_counts.get(kind, 0) + 1
                continue
            reason = reason or "no_semantic_rule_match"
            skip_counts[reason] = skip_counts.get(reason, 0) + 1
            if len(samples) < 10:
                samples.append(
                    {
                        "episode_id": _clean_text(episode.get("episode_id")),
                        "memory_class": memory_class,
                        "reason_code": reason,
                        "summary": _clean_text(episode.get("summary"), limit=220),
                        "source_text": _clean_text(texts[0], limit=220) if texts else "",
                    }
                )
        for proposal in [
            *self._build_collection_semantic_proposals(limit=limit),
            *self._build_entity_semantic_proposals(limit=limit),
            *self._build_correction_semantic_proposals(limit=limit),
            *self._build_action_pattern_semantic_proposals(limit=limit),
        ]:
            kind = _clean_text(proposal.get("semantic_kind")) or "unknown"
            proposal_counts[kind] = proposal_counts.get(kind, 0) + 1
        return {
            "status": "OK",
            "scanned_count": len(rows),
            "class_counts": dict(sorted(class_counts.items())),
            "semantic_candidate_counts": dict(sorted(candidate_counts.items())),
            "synthetic_candidate_counts": dict(sorted(synthetic_counts.items())),
            "proposal_counts": dict(sorted(proposal_counts.items())),
            "skip_reason_counts": dict(sorted(skip_counts.items())),
            "skip_samples": samples,
        }

    def semantic_growth_gap_report(self, *, limit: int = 30000, sample_limit: int = 12) -> dict[str, Any]:
        """Report durable-memory gaps without promoting test-only evidence."""

        fetch_limit = max(1, int(limit))
        max_samples = max(1, int(sample_limit))
        rows = self.store.ledger._conn.execute(
            """
            SELECT *
            FROM life_episode
            ORDER BY last_touched_ts_ms DESC, memory_strength DESC, salience DESC
            LIMIT ?
            """,
            (fetch_limit,),
        ).fetchall()

        def increment(counter: dict[str, int], key: str) -> None:
            counter[key] = counter.get(key, 0) + 1

        def increment_nested(counter: dict[str, dict[str, int]], key: str, reason: str) -> None:
            bucket = counter.setdefault(key, {})
            bucket[reason] = bucket.get(reason, 0) + 1

        def add_sample(bucket: list[dict[str, Any]], episode: Mapping[str, Any], *, reason: str, synthetic: bool) -> None:
            if len(bucket) >= max_samples:
                return
            bucket.append(
                {
                    "episode_id": _clean_text(episode.get("episode_id")),
                    "memory_class": _episode_memory_class_from_metadata(_episode_metadata_dict(episode)) or "unclassified",
                    "truth_state": _clean_text(episode.get("truth_state")).upper(),
                    "status": _clean_text(episode.get("status")).upper(),
                    "retention_state": _clean_text(episode.get("retention_state")).upper(),
                    "reason_code": reason,
                    "synthetic": bool(synthetic),
                    "person_id": _clean_text(episode.get("person_id"), limit=80),
                    "session_id": _clean_text(episode.get("session_id"), limit=80),
                    "summary": _clean_text(episode.get("summary"), limit=220),
                    "source_ref": _clean_text(episode.get("primary_ref"), limit=180),
                }
            )

        def inactive_reason(
            *,
            truth_state: str,
            status: str,
            retention_state: str,
            policy_candidate: bool,
        ) -> str:
            if truth_state in {TRUTH_CORRECTED, TRUTH_SUPPRESSED} or status == STATUS_SUPPRESSED:
                return "corrected_or_suppressed_source"
            if truth_state != TRUTH_VERIFIED:
                return f"truth_state_{truth_state.lower() or 'missing'}"
            if status != STATUS_ACTIVE:
                return f"status_{status.lower() or 'missing'}"
            if retention_state == RETENTION_GONE:
                return "retention_gone"
            if not policy_candidate:
                return "not_semantic_candidate"
            return "not_active_candidate"

        class_counts: dict[str, int] = {}
        production_priority_counts: dict[str, int] = {}
        synthetic_priority_counts: dict[str, int] = {}
        production_candidate_counts: dict[str, int] = {}
        synthetic_candidate_counts: dict[str, int] = {}
        production_inactive_priority_counts: dict[str, dict[str, int]] = {}
        synthetic_inactive_priority_counts: dict[str, dict[str, int]] = {}
        suppressed_source_counts: dict[str, int] = {}
        samples: dict[str, list[dict[str, Any]]] = {
            "production_candidates": [],
            "synthetic_candidates": [],
            "production_inactive_priority": [],
            "synthetic_inactive_priority": [],
        }

        priority_classes = set(_SEMANTIC_PRIORITY_MEMORY_CLASSES)
        for row in rows:
            episode = dict(row)
            metadata = _episode_metadata_dict(episode)
            memory_class = _episode_memory_class_from_metadata(metadata) or "unclassified"
            increment(class_counts, memory_class)
            policy = metadata.get("memory_policy") if isinstance(metadata.get("memory_policy"), Mapping) else {}
            policy_candidate = bool(policy.get("semantic_candidate"))
            priority_like = memory_class in priority_classes or policy_candidate
            if not priority_like:
                continue

            synthetic = _is_synthetic_life_record(episode, metadata)
            truth_state = _clean_text(episode.get("truth_state")).upper()
            status = _clean_text(episode.get("status")).upper()
            retention_state = _clean_text(episode.get("retention_state")).upper()
            active_candidate = (
                policy_candidate
                and truth_state == TRUTH_VERIFIED
                and status == STATUS_ACTIVE
                and retention_state != RETENTION_GONE
            )

            if synthetic:
                increment(synthetic_priority_counts, memory_class)
            else:
                increment(production_priority_counts, memory_class)

            if active_candidate:
                if synthetic:
                    increment(synthetic_candidate_counts, memory_class)
                    add_sample(samples["synthetic_candidates"], episode, reason="active_candidate", synthetic=True)
                else:
                    increment(production_candidate_counts, memory_class)
                    add_sample(samples["production_candidates"], episode, reason="active_candidate", synthetic=False)
                continue

            reason = inactive_reason(
                truth_state=truth_state,
                status=status,
                retention_state=retention_state,
                policy_candidate=policy_candidate,
            )
            if reason == "corrected_or_suppressed_source":
                increment(suppressed_source_counts, memory_class)
            if synthetic:
                increment_nested(synthetic_inactive_priority_counts, memory_class, reason)
                add_sample(samples["synthetic_inactive_priority"], episode, reason=reason, synthetic=True)
            else:
                increment_nested(production_inactive_priority_counts, memory_class, reason)
                add_sample(samples["production_inactive_priority"], episode, reason=reason, synthetic=False)

        correction_record_proposals = self._build_correction_semantic_proposals(limit=fetch_limit)
        if correction_record_proposals:
            production_candidate_counts[MEMORY_CLASS_CORRECTION] = production_candidate_counts.get(MEMORY_CLASS_CORRECTION, 0) + len(
                correction_record_proposals
            )

        synthetic_only_candidate_classes = [
            item
            for item in _SEMANTIC_PRIORITY_MEMORY_CLASSES
            if synthetic_candidate_counts.get(item, 0) > 0 and production_candidate_counts.get(item, 0) == 0
        ]
        missing_production_candidate_classes = [
            item for item in _SEMANTIC_PRIORITY_MEMORY_CLASSES if production_candidate_counts.get(item, 0) == 0
        ]
        recommendations: list[str] = []
        if synthetic_only_candidate_classes:
            recommendations.append(
                "production_source_absent: do not distill synthetic-only priority memories; capture the same owner inputs through real gateway/surface episodes first."
            )
        if missing_production_candidate_classes:
            recommendations.append(
                "semantic_growth_sparse: production durable memory is missing one or more priority classes; inspect ingestion before changing distillation prompts."
            )
        if production_inactive_priority_counts:
            recommendations.append(
                "inactive_priority_sources_present: corrected, suppressed, cold, or policy-denied priority episodes exist and should remain audit-only unless new evidence verifies them."
            )

        semantic_memory_count = self.store.ledger._conn.execute(
            "SELECT COUNT(*) FROM semantic_memory WHERE deleted_at IS NULL"
        ).fetchone()[0]
        return {
            "status": "OK",
            "scanned_count": len(rows),
            "semantic_memory_count": int(semantic_memory_count or 0),
            "class_counts": dict(sorted(class_counts.items())),
            "production_priority_counts": dict(sorted(production_priority_counts.items())),
            "synthetic_priority_counts": dict(sorted(synthetic_priority_counts.items())),
            "production_candidate_counts": dict(sorted(production_candidate_counts.items())),
            "synthetic_candidate_counts": dict(sorted(synthetic_candidate_counts.items())),
            "production_inactive_priority_counts": {
                key: dict(sorted(value.items())) for key, value in sorted(production_inactive_priority_counts.items())
            },
            "synthetic_inactive_priority_counts": {
                key: dict(sorted(value.items())) for key, value in sorted(synthetic_inactive_priority_counts.items())
            },
            "suppressed_source_counts": dict(sorted(suppressed_source_counts.items())),
            "synthetic_only_candidate_classes": synthetic_only_candidate_classes,
            "missing_production_candidate_classes": missing_production_candidate_classes,
            "recommendations": recommendations,
            "samples": samples,
        }

    def _build_collection_semantic_proposals(self, *, limit: int = 80) -> list[dict[str, Any]]:
        rows = self.store.ledger._conn.execute(
            """
            SELECT *
            FROM life_memory_collection
            WHERE COALESCE(retention_state, ?) != ?
              AND COALESCE(item_count, 0) > 0
            ORDER BY memory_strength DESC, updated_ts_ms DESC
            LIMIT ?
            """,
            (RETENTION_WARM, RETENTION_GONE, max(1, int(limit))),
        ).fetchall()
        proposals: list[dict[str, Any]] = []
        for row in rows:
            collection = dict(row)
            proposal = _semantic_proposal_from_collection(self.store, collection)
            if proposal:
                proposals.append(proposal)
        return proposals

    def _build_entity_semantic_proposals(self, *, limit: int = 80) -> list[dict[str, Any]]:
        rows = self.store.ledger._conn.execute(
            """
            SELECT *
            FROM life_memory_entity
            WHERE COALESCE(retention_state, ?) != ?
              AND COALESCE(mention_count, 0) > 0
            ORDER BY memory_strength DESC, last_seen_ts_ms DESC
            LIMIT ?
            """,
            (RETENTION_WARM, RETENTION_GONE, max(1, int(limit))),
        ).fetchall()
        proposals: list[dict[str, Any]] = []
        for row in rows:
            entity = dict(row)
            proposal = _semantic_proposal_from_entity(self.store, entity)
            if proposal:
                proposals.append(proposal)
        return proposals

    def _build_correction_semantic_proposals(self, *, limit: int = 80) -> list[dict[str, Any]]:
        if not _sqlite_table_exists(self.store.ledger._conn, "life_memory_correction"):
            return []
        rows = self.store.ledger._conn.execute(
            """
            SELECT
                c.correction_id,
                c.episode_id,
                c.correction_episode_id,
                c.correction_kind,
                c.reason_code,
                c.detail,
                c.created_ts_ms,
                c.metadata,
                source_ep.summary AS source_summary,
                source_ep.primary_ref AS source_primary_ref,
                source_ep.person_id AS source_person_id,
                source_ep.session_id AS source_session_id,
                source_ep.truth_state AS source_truth_state,
                source_ep.status AS source_status,
                source_ep.metadata AS source_metadata,
                corr_ep.summary AS correction_summary,
                corr_ep.primary_ref AS correction_primary_ref,
                corr_ep.person_id AS correction_person_id,
                corr_ep.session_id AS correction_session_id,
                corr_ep.truth_state AS correction_truth_state,
                corr_ep.status AS correction_status,
                corr_ep.retention_state AS correction_retention_state,
                corr_ep.metadata AS correction_metadata
            FROM life_memory_correction c
            LEFT JOIN life_episode source_ep ON source_ep.episode_id = c.episode_id
            LEFT JOIN life_episode corr_ep ON corr_ep.episode_id = c.correction_episode_id
            ORDER BY c.created_ts_ms DESC
            LIMIT ?
            """,
            (max(1, int(limit)),),
        ).fetchall()
        proposals: list[dict[str, Any]] = []
        for row in rows:
            data = dict(row)
            proposal = _semantic_proposal_from_correction_row(data)
            if not proposal:
                continue
            key = _clean_text(proposal.get("semantic_key"))
            if not key:
                continue
            proposals.append(proposal)
        return proposals

    def _build_action_pattern_semantic_proposals(self, *, limit: int = 80) -> list[dict[str, Any]]:
        return _semantic_action_pattern_proposals(self.store, limit=limit)

    def prune_synthetic_semantic_memories(
        self,
        *,
        ts_ms: int | None = None,
        limit: int = 500,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Soft-delete semantic memories that were distilled from test/e2e sources."""

        current = int(ts_ms if ts_ms is not None else now_ts_ms())
        rows = self.store.ledger._conn.execute(
            """
            SELECT *
            FROM semantic_memory
            WHERE deleted_at IS NULL
            ORDER BY ts_ms DESC
            LIMIT ?
            """,
            (max(1, int(limit)),),
        ).fetchall()
        memories = [self.store.ledger._row_to_semantic_memory(row) for row in rows]
        stale_collection_semantic_ids = _stale_collection_semantic_ids(self.store, memories)
        pruned: list[dict[str, Any]] = []
        for memory in memories:
            reason = _semantic_memory_prune_reason(
                self.store,
                memory,
                stale_collection_semantic_ids=stale_collection_semantic_ids,
            )
            if not reason:
                continue
            pruned.append(
                {
                    "semantic_id": _clean_text(memory.get("semantic_id")),
                    "reason_code": reason,
                    "summary": _clean_text(memory.get("summary"), limit=240),
                }
            )
        if pruned and not dry_run:
            ids = [_clean_text(item.get("semantic_id")) for item in pruned if _clean_text(item.get("semantic_id"))]
            placeholders = ", ".join("?" for _ in ids)
            if ids:
                with self.store.ledger._write_lock:
                    with self.store.ledger._conn:
                        self.store.ledger._conn.execute(
                            f"UPDATE semantic_memory SET deleted_at = ? WHERE semantic_id IN ({placeholders})",
                            (current, *ids),
                        )
        return {
            "status": "OK",
            "dry_run": bool(dry_run),
            "scanned_count": len(rows),
            "pruned_count": len(pruned),
            "semantic_ids": [item["semantic_id"] for item in pruned if item.get("semantic_id")],
            "reason_counts": _count_reason_codes(pruned),
            "samples": pruned[:8],
        }

    def distill_semantic_memories(
        self,
        *,
        limit: int = 80,
        max_records: int = 12,
        query_text: str | None = None,
        ts_ms: int | None = None,
        operator_node_id: str = "life_memory_consolidator",
    ) -> dict[str, Any]:
        created = int(ts_ms if ts_ms is not None else now_ts_ms())
        proposals = self.build_semantic_proposals(limit=limit, max_records=max_records)
        if _clean_text(query_text):
            proposals = [
                proposal
                for proposal in proposals
                if _semantic_proposal_matches_query(proposal, query_text=query_text)
            ][: max(1, int(max_records))]
        written: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        open_loop_updates: list[dict[str, Any]] = []
        for proposal in proposals:
            semantic_key = _clean_text(proposal.get("semantic_key"))
            if not semantic_key:
                continue
            source_ids = _semantic_source_memory_ids(proposal)
            if not source_ids:
                continue
            source_hash = hashlib.sha256(f"life_semantic:v1:{semantic_key}".encode("utf-8", errors="ignore")).hexdigest()
            semantic_id = f"sem_life_{_stable_hash('life_semantic_id:v1:' + semantic_key, size=24)}"
            source_tags: list[str] = []
            if list(proposal.get("source_episode_ids") or []):
                source_tags.append("source:life_episode")
            if list(proposal.get("source_collection_ids") or []):
                source_tags.append("source:life_memory_collection")
            if list(proposal.get("source_entity_ids") or []):
                source_tags.append("source:life_memory_entity")
            if list(proposal.get("source_correction_ids") or []):
                source_tags.append("source:life_memory_correction")
            tags = list(
                dict.fromkeys(
                    [
                        *list(proposal.get("tags") or []),
                        *_semantic_taxonomy_tags(_clean_text(proposal.get("semantic_kind"))),
                        *source_tags,
                        "life_consolidated",
                    ]
                )
            )
            if _clean_text(proposal.get("semantic_kind")) == "commitment":
                lifecycle = self._sync_open_loop_from_commitment_proposal(
                    proposal=proposal,
                    semantic_key=semantic_key,
                    tags=tags,
                    ts_ms=created,
                )
                if lifecycle:
                    open_loop_updates.append(lifecycle)
            existing = self.store.ledger.get_semantic_memory_by_source_hash(source_hash)
            if existing is not None:
                existing_source_ids = list(existing.get("source_memory_ids") or [])
                existing_tags = list(existing.get("tags") or [])
                merged_source_ids = list(dict.fromkeys([*existing_source_ids, *source_ids]))
                merged_tags = _normalize_open_loop_status_tags(list(dict.fromkeys([*existing_tags, *tags])), incoming_tags=tags)
                if _clean_text(proposal.get("distill_strategy")) == "life_memory_collection_rule_consolidation":
                    merged_summary = _clean_text(proposal.get("summary"), limit=900)
                elif _semantic_existing_summary_should_be_replaced(
                    existing.get("summary"),
                    proposal.get("summary"),
                    semantic_kind=_clean_text(proposal.get("semantic_kind")),
                ):
                    merged_summary = _clean_text(proposal.get("summary"), limit=900)
                else:
                    merged_summary = _merge_semantic_summaries(existing.get("summary"), proposal.get("summary"))
                merged_confidence = max(_clamp_unit(existing.get("confidence")), _clamp_unit(proposal.get("confidence")))
                changed = (
                    merged_source_ids != existing_source_ids
                    or merged_tags != existing_tags
                    or _clean_text(merged_summary) != _clean_text(existing.get("summary"))
                    or merged_confidence > _clamp_unit(existing.get("confidence"))
                )
                if changed:
                    with self.store.ledger._write_lock:
                        with self.store.ledger._conn:
                            self.store.ledger._conn.execute(
                                """
                                UPDATE semantic_memory
                                SET summary = ?,
                                    source_memory_ids = ?,
                                    confidence = ?,
                                    tags = ?,
                                    ts_ms = ?,
                                    operator_node_id = ?
                                WHERE semantic_id = ?
                                  AND deleted_at IS NULL
                                """,
                                (
                                    _clean_text(merged_summary, limit=900),
                                    _json_dumps(merged_source_ids) or "[]",
                                    merged_confidence,
                                    _json_dumps(merged_tags) or "[]",
                                    created,
                                    _clean_text(operator_node_id) or "life_memory_consolidator",
                                    existing.get("semantic_id"),
                                ),
                            )
                    persisted = self.store.ledger.get_semantic_memory_by_source_hash(source_hash) or existing
                    written.append(
                        {
                            "semantic_id": persisted.get("semantic_id"),
                            "summary": persisted.get("summary"),
                            "source_count": len(merged_source_ids),
                            "tags": persisted.get("tags"),
                            "updated_existing": True,
                        }
                    )
                else:
                    skipped.append(
                        {
                            "semantic_id": existing.get("semantic_id"),
                            "reason_code": "SEMANTIC_MEMORY_EXISTS",
                            "semantic_key": semantic_key,
                        }
                    )
                continue
            record = {
                "semantic_id": semantic_id,
                "summary": _clean_text(proposal.get("summary"), limit=900),
                "source_memory_ids": source_ids,
                "source_hash": source_hash,
                "distill_strategy": _clean_text(proposal.get("distill_strategy")) or "life_episode_rule_consolidation",
                "confidence": _clamp_unit(proposal.get("confidence")),
                "tags": tags,
                "ts_ms": created,
                "operator_node_id": _clean_text(operator_node_id) or "life_memory_consolidator",
            }
            self.store.ledger.log_semantic_memory(record)
            persisted = self.store.ledger.get_semantic_memory_by_source_hash(source_hash)
            if persisted is None:
                skipped.append({"reason_code": "SEMANTIC_MEMORY_WRITE_FAILED", "semantic_key": semantic_key})
                continue
            written.append(
                {
                    "semantic_id": persisted.get("semantic_id"),
                    "summary": persisted.get("summary"),
                    "source_count": len(source_ids),
                    "tags": persisted.get("tags"),
                }
            )
        return {
            "status": "OK",
            "candidate_count": len(proposals),
            "written_count": len(written),
            "skipped_count": len(skipped),
            "written": written,
            "skipped": skipped,
            "open_loop_updates": open_loop_updates,
        }

    def _sync_open_loop_from_commitment_proposal(
        self,
        *,
        proposal: Mapping[str, Any],
        semantic_key: str,
        tags: Sequence[str],
        ts_ms: int,
    ) -> dict[str, Any] | None:
        source_episode_ids = [
            _clean_text(item)
            for item in list(proposal.get("source_episode_ids") or [])
            if _clean_text(item)
        ]
        if not source_episode_ids:
            return None
        episode_id = source_episode_ids[0]
        row = self.store.ledger._conn.execute(
            "SELECT * FROM life_episode WHERE episode_id = ? LIMIT 1",
            (episode_id,),
        ).fetchone()
        episode = dict(row) if row is not None else {}
        status = _clean_text(proposal.get("open_loop_status")).upper() or _open_loop_status_from_tags(tags)
        summary = _clean_text(proposal.get("summary"), limit=900)
        result = self.store.upsert_open_loop(
            semantic_key=semantic_key,
            episode_id=episode_id,
            status=status,
            title=_open_loop_title_from_summary(summary),
            detail=summary,
            person_id=_clean_text(episode.get("person_id")) or None,
            session_id=_clean_text(episode.get("session_id")) or None,
            source_ref=(list(proposal.get("source_refs") or []) or [episode.get("primary_ref") or f"life_episode://{episode_id}"])[0],
            close_reason=_clean_text(proposal.get("open_loop_close_reason")),
            salience=_clamp_unit(episode.get("salience")),
            memory_strength=_clamp_unit(episode.get("memory_strength"), default=_clamp_unit(proposal.get("confidence"))),
            ts_ms=ts_ms,
            metadata={
                "source_episode_ids": source_episode_ids,
                "source_refs": list(proposal.get("source_refs") or []),
                "semantic_summary": summary,
                "semantic_tags": list(tags),
            },
        )
        return {
            "semantic_key": semantic_key,
            "open_loop_id": result.get("open_loop_id"),
            "status": status,
            "episode_id": episode_id,
        }


class MemorySleepReplayTask:
    """Offline replay selector for high-salience trusted episodes."""

    def __init__(self, *, store: LifeMemoryStore) -> None:
        self.store = store

    def select_replay_batch(self, *, limit: int = 12) -> list[dict[str, Any]]:
        fetch_limit = max(1, int(limit)) * 6
        candidates = LifeMemoryConsolidator(store=self.store).build_semantic_candidates(limit=fetch_limit)
        filtered = [item for item in candidates if _should_sleep_replay_candidate(item)]
        return filtered[: max(1, int(limit))]

    def select_compaction_groups(
        self,
        *,
        now_ts_ms_value: int | None = None,
        group_limit: int = 6,
        max_group_size: int = 6,
        min_age_ms: int = 6 * 60 * 60 * 1000,
    ) -> list[list[dict[str, Any]]]:
        """Select medium-strength lived episodes for sleep-time scene compaction."""

        current = int(now_ts_ms_value if now_ts_ms_value is not None else now_ts_ms())
        semantic_referenced_episode_ids = _semantic_referenced_episode_ids(self.store.ledger._conn)
        rows = self.store.ledger._conn.execute(
            """
            SELECT *
            FROM life_episode
            WHERE status = ?
              AND truth_state IN (?, ?)
              AND COALESCE(retention_state, ?) IN (?, ?)
              AND compressed_into_episode_id IS NULL
            ORDER BY created_ts_ms ASC
            LIMIT ?
            """,
            (
                STATUS_ACTIVE,
                TRUTH_VERIFIED,
                TRUTH_NARRATIVE_ONLY,
                RETENTION_WARM,
                RETENTION_WARM,
                RETENTION_COLD,
                max(1, int(group_limit)) * max(2, int(max_group_size)) * 4,
            ),
        ).fetchall()
        buckets: dict[tuple[str, str, str, str], list[dict[str, Any]]] = {}
        for row in rows:
            episode = dict(row)
            if not _should_compact_sleep_candidate(
                episode,
                current_ts_ms=current,
                semantic_referenced_episode_ids=semantic_referenced_episode_ids,
                min_age_ms=min_age_ms,
            ):
                continue
            key = _sleep_compaction_group_key(episode)
            buckets.setdefault(key, []).append(episode)
        groups: list[list[dict[str, Any]]] = []
        for items in buckets.values():
            if len(items) < 2:
                continue
            groups.append(items[: max(2, int(max_group_size))])
        groups.sort(
            key=lambda group: (
                -len(group),
                min(int(item.get("created_ts_ms") or 0) for item in group),
            )
        )
        return groups[: max(0, int(group_limit))]

    def run_once(
        self,
        *,
        now_ts_ms_value: int | None = None,
        replay_limit: int = 12,
        policy_backfill_limit: int = 5000,
        forgetting_batch_limit: int = 500,
        semantic_limit: int = 12,
        compaction_group_limit: int = 6,
        compaction_min_age_ms: int = 6 * 60 * 60 * 1000,
    ) -> dict[str, Any]:
        current = int(now_ts_ms_value if now_ts_ms_value is not None else now_ts_ms())
        policy_backfill = self.store.backfill_memory_policy(batch_limit=policy_backfill_limit)
        forgetting = self.store.apply_forgetting_pass(
            now_ts_ms_value=current,
            batch_limit=forgetting_batch_limit,
        )
        compaction_groups = self.select_compaction_groups(
            now_ts_ms_value=current,
            group_limit=compaction_group_limit,
            min_age_ms=compaction_min_age_ms,
        )
        compactions: list[dict[str, Any]] = []
        for group in compaction_groups:
            payload = _sleep_compaction_payload(group)
            result = self.store.compact_episodes(
                source_episode_ids=[str(item.get("episode_id") or "") for item in group],
                summary=str(payload.get("summary") or ""),
                kept_facts=list(payload.get("kept_facts") or []),
                dropped_details=list(payload.get("dropped_details") or []),
                ts_ms=current + len(compactions),
                metadata={
                    "compaction_source": "memory_sleep_replay",
                    "group_key": payload.get("group_key"),
                },
            )
            if str(result.get("status") or "") == "OK":
                compactions.append(result)
        consolidator = LifeMemoryConsolidator(store=self.store)
        semantic_cleanup_before = consolidator.prune_synthetic_semantic_memories(ts_ms=current)
        semantic_distillation = consolidator.distill_semantic_memories(
            limit=max(replay_limit * 4, semantic_limit * 3, 12),
            max_records=max(1, int(semantic_limit)),
            ts_ms=current,
            operator_node_id="memory_sleep_replay",
        )
        semantic_cleanup_after = consolidator.prune_synthetic_semantic_memories(ts_ms=current + 1)
        semantic_cleanup = _merge_semantic_cleanup_reports(semantic_cleanup_before, semantic_cleanup_after)
        semantic_growth = consolidator.semantic_growth_diagnostics(limit=max(policy_backfill_limit, replay_limit * 8, semantic_limit * 8, 100))
        replay_batch = self.select_replay_batch(limit=replay_limit)
        reflection_count = 0
        for item in replay_batch:
            episode_id = _clean_text(item.get("episode_id"))
            if not episode_id:
                continue
            if _clamp_unit(item.get("memory_strength"), default=_clamp_unit(item.get("salience"))) < 0.70:
                continue
            self.store.attach_evidence(
                episode_id=episode_id,
                evidence_kind=EVIDENCE_REFLECTION,
                source_ref=f"sleep_replay://episode/{episode_id}/{current}",
                request_id=f"sleep_replay::{current}",
                content=f"睡眠回放强化：{_clean_text(item.get('summary'), limit=500)}",
                payload={"replay_reason": "high_memory_strength"},
                trust_level=TRUTH_NARRATIVE_ONLY,
                ts_ms=current + reflection_count,
            )
            reflection_count += 1
        return {
            "status": "OK",
            "policy_backfill": policy_backfill,
            "forgetting": forgetting,
            "compaction": {
                "group_count": len(compactions),
                "episode_count": sum(int(item.get("source_count") or 0) for item in compactions),
                "groups": compactions,
            },
            "semantic_cleanup": semantic_cleanup,
            "semantic_distillation": semantic_distillation,
            "semantic_growth": semantic_growth,
            "replay_selected": len(replay_batch),
            "reflection_count": reflection_count,
        }


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


def _semantic_statement_is_lookup_question(statement: str) -> bool:
    value = _clean_text(statement, limit=700)
    compact = re.sub(r"\s+", "", value).lower()
    if not compact:
        return True
    assertion_markers = (
        "用户要求",
        "用户喜欢",
        "用户不喜欢",
        "用户偏好",
        "用户明确要求",
        "失败原因：",
        "失败原因:",
        "原因：",
        "原因:",
    )
    if any(marker in compact for marker in assertion_markers):
        return False
    question_like = (
        "?" in compact
        or "？" in compact
        or compact.endswith("吗")
        or compact.endswith("呢")
        or any(token in compact for token in ("是什么", "为什么", "为啥", "怎么", "怎样", "如何", "哪本", "哪个", "哪些", "请列出", "列出当前", "验收", "回忆检查"))
    )
    if not question_like:
        return False
    lookup_markers = (
        "还记得",
        "记得我说",
        "记不记得",
        "记了什么",
        "说过什么",
        "请列出",
        "列出当前",
        "最终同会话验收",
        "跨会话验收",
        "回忆检查",
        "验收",
        "推荐格式",
        "失败原因",
        "任务会失败",
        "你说",
        "我说",
        "我问",
        "你喜欢哪本",
        "哪本好看",
    )
    return any(marker in compact for marker in lookup_markers) or compact.endswith(("吗", "呢", "?", "？"))


def _semantic_statement_body(statement: str) -> str:
    value = _clean_text(statement, limit=360).strip("。；; ")
    value = re.sub(r"^(用户明确要求|用户要求|用户偏好|偏好)[：:\s]*", "", value).strip()
    if value.startswith("我喜欢"):
        value = "用户喜欢" + value.removeprefix("我喜欢")
    if value.startswith("我不喜欢"):
        value = "用户不喜欢" + value.removeprefix("我不喜欢")
    if value.startswith("不要") or value.startswith("别"):
        value = "用户要求" + value
    if value.startswith("主链"):
        value = "用户要求" + value
    return value[:360].rstrip("。；; ")


def _semantic_text_is_sensitive_or_noise(text: str) -> bool:
    compact = re.sub(r"\s+", "", str(text or "")).lower()
    if not compact:
        return True
    if _semantic_looks_like_recommendation_report(compact):
        return True
    if _semantic_text_has_secret(text):
        return True
    if any(token in compact for token in ("heartbeat", "health_check", "emotion_decay_tick", "inner_world_numeric_tick", "silence_tick")):
        return True
    if any(token in compact for token in ("网页深读结果", "lightnovel_recommendation_deep_review", "正文内容足够支撑判断")):
        return True
    if "latency_ms" in compact and "prewarm_latency_ms" not in compact:
        return True
    if any(
        token in compact
        for token in (
            "codexproductionjobagent_lightnovel_recommender",
            "lightnovel_recommendation_delivery",
            "lightnovel_gateway_delivery",
            "轻小说推荐报告",
            "我通过qq给",
            "我刚才通过qq给",
            "推荐了3本轻小说",
            "发送了text消息",
            "发送了image消息",
            "发送结果=sent",
            "命中偏好",
            "详情:https://www.lightnovel.fun",
            "详情：http://www.lightnovel.fun",
            "请根据下面的真实片段",
            "_thalamus_meta",
            "surface_production_result_recall",
            "cortex_orchestrator",
            "decision=approve",
        )
    ):
        return True
    return False


def _semantic_text_has_secret(text: str) -> bool:
    compact = re.sub(r"\s+", "", str(text or "")).lower()
    return any(token in compact for token in ("api_key", "apikey", "secret", "cookie", "token=", "authorization:", "密码", "密钥"))


def _semantic_looks_like_recommendation_report(compact: str) -> bool:
    value = str(compact or "")
    return any(
        token in value
        for token in (
            "轻小说推荐报告",
            "推荐候选",
            "推荐列表",
            "命中偏好",
            "lightnovel_recommendation_delivery",
            "lightnovel_gateway_delivery",
            "agent_lightnovel_recommender",
        )
    )


def _semantic_operational_failure_summary(statement: str, *, episode: Mapping[str, Any]) -> dict[str, Any] | None:
    compact = re.sub(r"\s+", "", statement).lower()
    action = _clean_text(episode.get("action")).lower()
    if not _semantic_is_failure_lesson(compact, action=action):
        return None
    status = _semantic_extract_field(statement, "status").upper()
    reason = _semantic_extract_field(statement, "reason")
    detail = _semantic_extract_field(statement, "summary")
    if status and status not in {"FAILED", "ERROR", "DEGRADED", "SUSPENDED", "HEARTBEAT_ALERT", "EDGE_PROBE_ALERT"}:
        return None
    source = _clean_text(episode.get("action") or episode.get("primary_ref") or episode.get("episode_kind"), limit=80)
    match = re.search(r"\b(?:job|task)\s+([A-Za-z0-9_.:-]{3,80})\s+status\s*=", statement, flags=re.IGNORECASE)
    if match:
        source = match.group(1)
    parts: list[str] = []
    if source:
        parts.append(f"{source} 失败")
    else:
        parts.append("后台任务失败")
    if reason:
        parts.append(f"原因 {reason}")
    if detail:
        parts.append(_clean_text(detail, limit=160))
    elif "result_text" not in compact and "轻小说推荐报告" not in statement:
        natural_detail = _clean_text(_semantic_statement_body(statement), limit=180)
        if natural_detail and natural_detail not in parts:
            parts.append(natural_detail)
    body = "；".join(dict.fromkeys(part for part in parts if part))
    return {
        "kind": "failure_lesson",
        "summary": f"故障经验：{body}",
        "tags": ["failure_lesson", "operations"],
        "base_confidence": 0.78,
    }


def _semantic_extract_field(statement: str, field: str) -> str:
    match = re.search(
        rf"(?:^|[；;\n\r\s]){re.escape(field)}\s*[=:：]\s*([^；;\n\r]+)",
        str(statement or ""),
        flags=re.IGNORECASE,
    )
    if not match:
        return ""
    value = _clean_text(match.group(1), limit=220)
    return re.sub(r"^(result_text|artifacts)\s*[=:：].*$", "", value, flags=re.IGNORECASE).strip()


def _semantic_skip_reason_for_texts(texts: Sequence[str], *, episode: Mapping[str, Any]) -> str:
    if not texts:
        return "no_source_text"
    saw_noise = False
    saw_lookup = False
    for text in texts:
        statement = _normalize_semantic_statement(text)
        if not statement:
            continue
        if _semantic_text_has_secret(statement):
            return "sensitive_text"
        if _semantic_operational_failure_summary(statement, episode=episode):
            return "unexpected_failure_proposal_miss"
        if _semantic_text_is_sensitive_or_noise(statement):
            saw_noise = True
            continue
        if _semantic_statement_is_lookup_question(statement):
            saw_lookup = True
            continue
    if saw_lookup:
        return "lookup_question"
    if saw_noise:
        return "noise_or_report"
    return "no_semantic_rule_match"


def _should_sleep_replay_candidate(item: Mapping[str, Any]) -> bool:
    """Keep replay for durable experiences, not transport/debug chatter."""

    metadata = _episode_metadata_dict(item)
    if _is_synthetic_life_record(item, metadata):
        return False
    summary = _clean_text(item.get("summary"), limit=1200)
    if not summary or _semantic_text_is_sensitive_or_noise(summary):
        return False
    if _looks_like_recommendation_followup_chat_noise(summary):
        return False
    action = _clean_text(item.get("action")).lower()
    if action in {
        "lightnovel_recommendation_delivery",
        "lightnovel_recommendation_deep_review",
        "qq_reply_sent",
        "wechat_reply_sent",
        "gateway_reply_sent",
        "channel_message_sent",
    }:
        return False
    compact = re.sub(r"\s+", "", summary).lower()
    if _semantic_is_failure_lesson(compact, action=action):
        return True
    if _semantic_is_commitment(compact) or _semantic_is_self_change(compact, action=action):
        return True
    if _semantic_is_relationship_feedback(compact):
        return True
    if _semantic_is_user_preference(compact) or _semantic_is_stable_fact(compact):
        return True
    kind = _clean_text(item.get("episode_kind"))
    if kind == EPISODE_CHAT:
        return True
    return _clamp_unit(item.get("memory_strength"), default=_clamp_unit(item.get("salience"))) >= 0.88


def _looks_like_recommendation_followup_chat_noise(text: str) -> bool:
    compact = re.sub(r"\s+", "", str(text or "")).lower()
    if not compact:
        return False
    has_selection_question = any(
        token in compact
        for token in (
            "我问你喜欢哪本",
            "这三本书，你喜欢哪本",
            "这三本书你喜欢哪本",
            "你喜欢哪本",
            "哪本好看",
        )
    )
    has_result_followup = any(
        token in compact
        for token in (
            "刚才今日推荐的结果",
            "刚才的推荐结果",
            "今日推荐的结果",
            "今日推荐跑完了吗",
            "今日推荐还没跑完",
            "推荐任务创建",
            "没拿到完成结果",
            "不能把没跑出来",
            "只说刚才那条结果",
        )
    )
    if not has_selection_question and not has_result_followup:
        return False
    durable_markers = ("用户喜欢", "用户要求", "偏好", "记住", "以后", "不要", "纠正", "失败原因", "承诺")
    return not any(marker in compact for marker in durable_markers)


def _semantic_is_user_preference(compact: str) -> bool:
    value = str(compact or "").replace("要不要", "")
    if _semantic_preference_has_unresolved_pronoun(value):
        return False
    if _semantic_looks_like_recommendation_report(value):
        return False
    user_value = value.split("assistant:", 1)[0] if "assistant:" in value else value
    selection_question_markers = (
        "我问你喜欢哪",
        "问你喜欢哪",
        "你喜欢哪本",
        "你喜欢哪部",
        "哪本好看",
        "哪本最好看",
        "哪本比较好",
    )
    durable_preference_markers = (
        "用户喜欢",
        "用户不喜欢",
        "用户要求",
        "用户偏好",
        "明确要求",
        "以后",
        "不要",
        "别",
        "记住",
        "偏好",
    )
    if any(token in user_value for token in selection_question_markers) and not any(
        marker in user_value for marker in durable_preference_markers
    ):
        return False
    actor_says_user = "user:" in user_value or "用户" in value
    if any(token in value for token in ("用户喜欢", "用户不喜欢", "用户要求", "用户偏好")):
        return True
    if re.search(r"user:.{0,40}我(?:不)?喜欢", user_value):
        return True
    if "assistant:" not in value and any(token in value for token in ("我喜欢", "我不喜欢", "记住我喜欢")):
        return True
    if "assistant:" not in value and any(
        token in value
        for token in (
            "以后",
            "推荐格式",
            "推荐轻小说时",
            "格式",
            "换行",
            "分段",
            "卡片",
            "模板腔",
            "拟人化发送",
            "主链不要",
            "不要用pro",
            "不用pro",
            "不要用",
            "别用",
            "不要再",
            "优先使用",
            "应该用",
            "必须用",
        )
    ):
        return True
    if "assistant:" not in value and _semantic_is_feature_toggle_preference(value):
        return True
    if not actor_says_user:
        return False
    preference_tokens = (
        "明确要求",
        "偏好",
        "以后",
        "推荐格式",
        "优先使用",
        "主链不要",
        "不要用pro",
        "不用pro",
        "分段",
        "卡片",
        "拟人化发送",
    )
    return any(token in value for token in preference_tokens) or _semantic_is_feature_toggle_preference(value)


def _semantic_preference_has_unresolved_pronoun(compact: str) -> bool:
    value = str(compact or "")
    if not any(token in value for token in ("这本", "那本", "这部", "那部", "这个", "那个", "它")):
        return False
    if not any(token in value for token in ("喜欢", "不喜欢", "不错", "好看", "对味", "讨厌")):
        return False
    return not any(token in value for token in ("《", "》", "http", "标题", "书名", "作品", "第1", "第2", "第3", "第一", "第二", "第三"))


def _semantic_is_feature_toggle_preference(compact: str) -> bool:
    value = str(compact or "")
    if any(token in value for token in ("待办", "承诺", "openloop", "open_loop", "失败原因")):
        return False
    feature_terms = (
        "拟人化发送",
        "主链",
        "模型池",
        "thalamus",
        "llm",
        "codex",
        "deepseek",
        "gemini",
        "pro",
        "推荐格式",
        "推送格式",
        "内心流",
        "后台任务",
        "调度任务",
        "onebot",
        "qq",
        "召回",
        "长期记忆",
    )
    if not any(term in value for term in feature_terms):
        return False
    return any(
        token in value
        for token in (
            "关掉",
            "关闭",
            "关了",
            "禁用",
            "停用",
            "别开",
            "不要开",
            "打开",
            "开启",
            "启用",
            "优先",
            "不要用",
            "别用",
            "不用",
        )
    )


def _semantic_is_correction_fact(compact: str) -> bool:
    value = str(compact or "")
    if any(
        token in value
        for token in (
            "用户纠正",
            "纠正测试代号",
            "你纠正的是",
            "我撤回",
            "撤回这个判断",
            "记错",
            "不是这样",
            "不对，没有",
            "没有“",
            "没有\"",
            "这点是错的",
        )
    ):
        return True
    return "改成" in value and any(token in value for token in ("纠正", "测试代号", "记错", "错了"))


def _normalize_open_loop_status(value: Any) -> str:
    status = _clean_text(value).upper()
    aliases = {
        "DONE": OPEN_LOOP_CLOSED,
        "FULFILLED": OPEN_LOOP_CLOSED,
        "COMPLETE": OPEN_LOOP_CLOSED,
        "COMPLETED": OPEN_LOOP_CLOSED,
        "RESOLVED": OPEN_LOOP_CLOSED,
        "FIXED": OPEN_LOOP_CLOSED,
        "CANCELED": OPEN_LOOP_CANCELLED,
        "CANCELLED": OPEN_LOOP_CANCELLED,
        "SKIPPED": OPEN_LOOP_CANCELLED,
        "ABANDONED": OPEN_LOOP_CANCELLED,
        "REPLACED": OPEN_LOOP_SUPERSEDED,
    }
    status = aliases.get(status, status)
    if status in {OPEN_LOOP_OPEN, OPEN_LOOP_CLOSED, OPEN_LOOP_CANCELLED, OPEN_LOOP_SUPERSEDED}:
        return status
    return OPEN_LOOP_OPEN


def _open_loop_close_reason_for_status(status: str) -> str:
    state = _normalize_open_loop_status(status)
    if state == OPEN_LOOP_CANCELLED:
        return "用户或系统取消了这个待办"
    if state == OPEN_LOOP_SUPERSEDED:
        return "后续决定替代了这个待办"
    if state == OPEN_LOOP_CLOSED:
        return "后续经历显示这个待办已完成"
    return ""


def _semantic_commitment_status(compact: str) -> str:
    value = str(compact or "")
    if any(token in value for token in ("取消待办", "取消这个待办", "不用做了", "不做了", "先不做", "放弃这个", "关闭且不做")):
        return OPEN_LOOP_CANCELLED
    if any(
        token in value
        for token in (
            "已经完成",
            "已完成",
            "完成了",
            "做完了",
            "已经做完",
            "搞定了",
            "处理完",
            "已处理",
            "解决了",
            "修复完成",
            "修好了",
            "关闭这个待办",
            "关掉这个待办",
            "闭环了",
        )
    ):
        return OPEN_LOOP_CLOSED
    return OPEN_LOOP_OPEN


def _query_wants_closed_open_loop(query: str) -> bool:
    value = str(query or "").lower()
    if not value:
        return False
    if any(token in value for token in ("未完成", "没完成", "还没", "待做", "要继续", "继续做", "open")):
        return False
    return any(
        token in value
        for token in (
            "已完成",
            "完成了",
            "做完",
            "搞定",
            "关闭",
            "closed",
            "done",
            "resolved",
        )
    )


def _preference_query_wants_relationship_feedback(query: str) -> bool:
    compact = re.sub(r"\s+", "", _clean_text(query, limit=360)).lower()
    if not compact:
        return False
    if any(token in compact for token in ("推荐格式", "简介后", "换行", "分段", "卡片格式", "怎么发", "怎样发")):
        return False
    return any(
        token in compact
        for token in (
            "文本质量",
            "不像人话",
            "不像正常人",
            "正常聊天",
            "台词腔",
            "模板腔",
            "说辞",
            "话术",
            "很呆",
            "太呆",
        )
    )


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


def _open_loop_status_from_tags(tags: Sequence[str]) -> str:
    lowered = {str(tag or "").strip().lower() for tag in list(tags or [])}
    if "open_loop:cancelled" in lowered or "open_loop:canceled" in lowered:
        return OPEN_LOOP_CANCELLED
    if "open_loop:superseded" in lowered:
        return OPEN_LOOP_SUPERSEDED
    if "open_loop:closed" in lowered:
        return OPEN_LOOP_CLOSED
    return OPEN_LOOP_OPEN


def _normalize_open_loop_status_tags(tags: Sequence[str], *, incoming_tags: Sequence[str] | None = None) -> list[str]:
    out = list(dict.fromkeys(_clean_text(tag) for tag in list(tags or []) if _clean_text(tag)))
    incoming_status = _open_loop_status_from_tags(list(incoming_tags or []))
    if incoming_status in OPEN_LOOP_TERMINAL_STATES:
        out = [tag for tag in out if tag.lower() != "open_loop:open"]
        terminal_tag = f"open_loop:{incoming_status.lower()}"
        if terminal_tag not in out:
            out.append(terminal_tag)
    elif any(str(tag or "").lower() == "open_loop:open" for tag in list(incoming_tags or [])):
        out = [tag for tag in out if tag.lower() not in {"open_loop:closed", "open_loop:cancelled", "open_loop:superseded"}]
        if "open_loop:open" not in out:
            out.append("open_loop:open")
    return out


def _open_loop_title_from_summary(summary: str) -> str:
    text = _clean_text(summary, limit=260)
    if "：" in text:
        text = text.split("：", 1)[1]
    elif ":" in text:
        text = text.split(":", 1)[1]
    text = re.sub(r"^(用户要求|后续要|下次继续|承诺|待办)[，,：:\s]*", "", text).strip()
    return _clean_text(text, limit=120)


def _semantic_is_commitment(compact: str) -> bool:
    value = str(compact or "")
    if _semantic_is_memory_system_directive(value) and not any(
        token in value
        for token in ("待办", "todo", "openloop", "open_loop", "后续要", "下次继续", "未完成", "还要做", "说好", "答应")
    ):
        return False
    if any(token in value for token in ("承诺", "待办", "todo", "openloop", "open_loop", "后续要", "下次继续", "未完成", "还要做", "说好", "答应")):
        return True
    if _semantic_commitment_status(value) in OPEN_LOOP_TERMINAL_STATES:
        return any(
            token in value
            for token in (
                "记忆",
                "召回",
                "推荐",
                "轻小说",
                "gateway",
                "qq",
                "onebot",
                "模型",
                "路由",
                "任务",
                "修复",
                "实现",
                "处理",
            )
        )
    return False


def _semantic_is_self_change(compact: str, *, action: str) -> bool:
    if action in {"model_route_update", "capability_change"}:
        return True
    if any(token in compact for token in ("模型路由", "记忆系统", "能力被删除", "删除资源管理器", "策略被用户纠正", "自我改变", "主链迁移")):
        return True
    if "改成" in compact and any(
        token in compact
        for token in (
            "模型",
            "路由",
            "主链",
            "能力",
            "资源管理器",
            "codex",
            "模型池",
            "deepseek",
            "gemini",
        )
    ):
        return True
    return _semantic_is_memory_system_directive(compact)


def _semantic_is_memory_system_directive(compact: str) -> bool:
    value = str(compact or "")
    memory_terms = (
        "记忆系统",
        "生命经历",
        "经历锚点",
        "episode",
        "evidence",
        "主动记忆",
        "长期记忆",
        "语义记忆",
        "召回记忆",
        "可召回记忆",
        "遗忘系统",
        "睡眠回放",
        "审计库",
        "进入记忆",
        "做过的事",
        "真实生命",
    )
    if not any(term in value for term in memory_terms):
        return False
    directive_terms = (
        "必须",
        "应该",
        "都要",
        "都应该",
        "不应该",
        "不等于",
        "不能",
        "只能",
        "不要",
        "要留下",
        "要进入",
        "进入记忆",
        "可追溯",
        "唯一",
        "改造成",
        "设计成",
    )
    return any(term in value for term in directive_terms)


def _semantic_is_relationship_feedback(compact: str) -> bool:
    value = str(compact or "").lower()
    if not value:
        return False
    preference_directive = any(
        marker in value
        for marker in (
            "以后",
            "不要",
            "别再",
            "别",
            "必须",
            "应该",
            "优先",
            "格式",
            "换行",
            "分段",
            "卡片",
            "简介后",
            "改成",
            "关了",
            "关掉",
            "关闭",
            "打开",
            "开启",
            "启用",
            "停用",
        )
    )
    hard_feedback_markers = (
        "胡说八道",
        "不像人话",
        "不符合正常聊天",
        "不符合正常人",
        "明显有问题",
        "很呆",
        "太呆",
        "白费功夫",
    )
    if any(marker in value for marker in hard_feedback_markers):
        return True
    softer_quality_markers = (
        "模板腔",
        "台词腔",
        "文本质量",
        "纯负优化",
    )
    if any(marker in value for marker in softer_quality_markers) and not preference_directive:
        return True
    scoped_negative = any(marker in value for marker in ("不行啊", "不对啊", "根本不对", "完全不对", "体验不好"))
    if not scoped_negative:
        return False
    scope_markers = (
        "召回",
        "记忆",
        "回答",
        "回复",
        "聊天",
        "对话",
        "主链",
        "模型",
        "推荐",
        "服务",
        "系统",
        "证据",
        "格式",
    )
    return any(marker in value for marker in scope_markers)


def _semantic_relationship_feedback_body(statement: str, *, episode: Mapping[str, Any]) -> str:
    compact = re.sub(r"\s+", "", _clean_text(statement, limit=700)).lower()
    episode_summary = re.sub(r"\s+", "", _clean_text(episode.get("summary"), limit=700)).lower()
    joined = compact + episode_summary
    if not joined:
        return ""
    if any(token in joined for token in ("不像人话", "正常聊天", "模板腔", "台词腔", "文本质量", "很呆", "太呆")):
        return "用户强烈反馈：文本质量不能生硬得不像人话；回答要像正常聊天，避免模板腔、台词腔和生硬的证据条目话术。"
    if any(token in joined for token in ("胡说八道", "编造", "召回", "证据", "记忆")):
        return "用户强烈反馈：记忆召回和判断必须回到证据，不能把没核实的内容说成事实或胡说八道。"
    if any(token in joined for token in ("白费功夫", "服务没起来", "没起来", "连不上")):
        return "用户反馈：测试或排障前要先确认服务真的启动和连通，避免在不可用状态下继续白费功夫。"
    if any(token in joined for token in ("明显有问题", "不行啊", "不对啊", "体验不好")):
        return "用户反馈：当前效果明显不对时，要先回到证据链和运行状态排查，再继续给结论。"
    body = _semantic_statement_body(statement)
    return _clean_text(body, limit=220)


def _semantic_is_failure_lesson(compact: str, *, action: str) -> bool:
    if "failed" in action or "failure" in action:
        return True
    return any(
        token in compact
        for token in (
            "失败原因",
            "任务失败",
            "调用失败",
            "执行失败",
            "status=failed",
            "status:failed",
            "statusfailed",
            "status=error",
            "status:error",
            "状态=failed",
            "状态failed",
            "连不上",
            "没起来",
            "状态库被占用",
            "被占用",
            "chroma",
            "neo4j",
            "deepseek失败",
            "gateway连不上",
            "服务没起来",
        )
    )


def _semantic_is_stable_fact(compact: str) -> bool:
    value = str(compact or "")
    if any(token in value for token in ("onebot在edge", "onebot在", "qq会话", "conversation_id", "session_id", "在edge", "不在本机")):
        return True
    if any(token in value for token in ("项目事实", "测试事实", "长对话测试事实", "当前测试代号", "测试代号是")):
        return True
    if "海边灯塔" in value and any(token in value for token in ("连续记忆锚点", "不是字面灯塔", "指")):
        return True
    if any(token in value for token in ("app-server预热指标", "appserver预热指标", "预热指标", "prewarm_latency_ms", "prewarm_threads", "prewarm_workers")):
        return True
    return False


def _semantic_confidence(*, episode: Mapping[str, Any], base: float) -> float:
    salience = _clamp_unit(episode.get("salience"), default=0.0)
    strength = _clamp_unit(episode.get("memory_strength"), default=salience)
    return round(_clamp_unit(max(base, 0.45 + strength * 0.35 + salience * 0.15)), 4)


__all__ = [
    "LifeMemoryStore",
    "LifeMemoryConsolidator",
    "MemorySleepReplayTask",
    "EpisodeRecallPacket",
    "derive_episode_id",
    "derive_evidence_id",
    "strong_evidence_refs",
    "TRUTH_CANDIDATE",
    "TRUTH_VERIFIED",
    "TRUTH_CORRECTED",
    "TRUTH_SUPPRESSED",
    "TRUTH_NARRATIVE_ONLY",
    "EPISODE_CHAT",
    "EPISODE_PERCEPTION",
    "EPISODE_ACTION",
    "EPISODE_BACKGROUND",
    "EPISODE_INNER",
    "RETENTION_HOT",
    "RETENTION_WARM",
    "RETENTION_COLD",
    "RETENTION_GONE",
    "COLLECTION_RECOMMENDATION",
    "COLLECTION_SCOPE_DAILY",
    "COLLECTION_SCOPE_RECENT",
    "COLLECTION_SCOPE_DELIVERY",
]
