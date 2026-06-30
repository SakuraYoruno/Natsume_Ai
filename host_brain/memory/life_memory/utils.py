"""Life memory utility functions.

Hashing, tokenization, NLP heuristics, scoring, classification,
and semantic classifier functions (moved here to avoid circular imports).
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from collections.abc import Mapping, Sequence
from typing import Any

from .constants import *
from .constants import (
    _ANAPHORIC_SELECTION_RE,
    _ANAPHORIC_EVENT_KINDS,
    _ANAPHORIC_LOW_VALUE_ACTIONS,
    _RECALL_KEYWORD_LIMIT,
    _RECALL_FETCH_LIMIT_FACTOR,
    _SEMANTIC_PRIORITY_MEMORY_CLASSES,
    _SYNTHETIC_METADATA_FLAGS,
    _SYNTHETIC_METADATA_MARKERS,
    _SYNTHETIC_ID_MARKERS,
    _SYNTHETIC_SOURCE_MARKERS,
    STRONG_EVIDENCE_PREFIXES,
)

def now_ts_ms() -> int:
    return int(time.time() * 1000)


def _owner_alias_person_ids(person_id: str | None) -> list[str]:
    person = _clean_text(person_id)
    aliases = ["", "default"]
    if person:
        aliases.append(person)
    if person in {"owner", "user", "main_user", "master"}:
        aliases.extend(["owner", "user", "main_user", "master"])
    return list(dict.fromkeys(aliases))


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



# --- Semantic classifier functions (moved here from semantic section to avoid circular imports) ---

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


def _normalize_semantic_statement(text: str) -> str:
    value = _clean_text(text, limit=700)
    value = re.sub(r"^(User|Assistant|Perception)\s*[:：]\s*", "", value).strip()
    value = re.sub(r"^(记忆线索|长期记忆|召回线索)\s*[:：]\s*", "", value).strip()
    value = re.sub(r"^行动记忆：[^；;]{1,120}[；;]\s*", "", value).strip()
    value = re.sub(r"^(记住|请记住|以后记住)[，,：:\s]*", "", value).strip()
    value = re.sub(r"^记得[，,：:\s]*", "", value).strip()
    value = re.sub(r"^(?:再补一个|补一个|一个)?(?:长对话测试事实|项目事实|测试事实|记忆事实)[，,：:\s]*", "", value).strip()
    return value.strip("。；; \n\t")


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

