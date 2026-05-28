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

DECAY_STABLE = "STABLE"
DECAY_SLOW = "SLOW"
DECAY_NORMAL = "NORMAL"
DECAY_FAST = "FAST"
DECAY_EPHEMERAL = "EPHEMERAL"

COLLECTION_RECOMMENDATION = "recommendation_collection"
COLLECTION_SCOPE_DAILY = "daily"
COLLECTION_SCOPE_RECENT = "recent"

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


def _episode_metadata_dict(episode: Mapping[str, Any]) -> dict[str, Any]:
    metadata = _json_loads(episode.get("metadata"), fallback={})
    return dict(metadata) if isinstance(metadata, Mapping) else {}


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
        base_salience = _clamp_unit(salience)
        decay_profile = _decay_profile_for_episode(
            episode_kind=kind,
            salience=base_salience,
            metadata={**metadata_payload, "decay_profile_id": decay_profile_id},
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
                            decay_profile,
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
                        _json_dumps(dict(metadata or {})),
                    ),
                )
                self._refresh_collection_item_count_locked(collection_id=cid, ts_ms=created)
        return {"status": "OK", "collection_id": cid, "collection_item_id": item_id, "item_key": key}

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
        person = _clean_text(episode.get("person_id")) or None
        session = _clean_text(episode.get("session_id")) or None
        source_ref = _clean_text(episode.get("primary_ref")) or f"life_episode://{eid}"
        collection_specs = [
            (
                COLLECTION_SCOPE_DAILY,
                f"{day_key} {domain} 集合",
                _collection_summary_text(day_key=day_key, domain=domain, items=items, scope_label="当天"),
            ),
            (
                COLLECTION_SCOPE_RECENT,
                f"最近 {domain} 集合",
                _collection_summary_text(day_key=day_key, domain=domain, items=items, scope_label="最近"),
            ),
        ]
        written: list[dict[str, Any]] = []
        for scope, title, summary in collection_specs:
            collection = self.upsert_collection(
                collection_kind=kind,
                domain=domain,
                scope_key=f"{scope}:{day_key}" if scope == COLLECTION_SCOPE_DAILY else scope,
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
        rows = self._fetch_collection_rows_for_plan(
            plan=plan,
            person_id=scoped_person_id,
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
            items = self._list_collection_items(collection_id=collection_id, limit=24)
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
                            "item_count": int(collection.get("item_count") or len(items)),
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
        current = int(now_ts_ms_value if now_ts_ms_value is not None else now_ts_ms())
        limit = max(1, int(top_k)) * _RECALL_FETCH_LIMIT_FACTOR
        anaphoric_query = _looks_like_anaphoric_selection_query(normalized_query)
        scoped_person_id = _clean_text(person_id)
        scoped_session_id = _clean_text(session_id)
        where = "WHERE status != ? AND truth_state != ? AND COALESCE(retention_state, ?) != ?"
        params: list[Any] = [STATUS_SUPPRESSED, TRUTH_SUPPRESSED, RETENTION_COLD, RETENTION_GONE]
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
            anaphoric_boost = 0.0
            if anaphoric_query:
                episode_kind = str(episode.get("episode_kind") or "").strip()
                if is_anaphoric_event_candidate:
                    anaphoric_boost += 0.45
                elif episode_kind == EPISODE_CHAT:
                    anaphoric_boost -= 0.45
                elif episode_kind in _ANAPHORIC_EVENT_KINDS:
                    anaphoric_boost += 0.05
            final_score = round(max(0.0, min(1.0, boosted_score + salience * 0.12 + truth_boost + anaphoric_boost)), 6)
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
        return {
            "status": "OK",
            "scanned": len(rows),
            "updated": len(updates),
            "counts": counts,
        }

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

    def _fetch_collection_rows_for_plan(
        self,
        *,
        plan: Any,
        person_id: str | None,
        session_id: str | None,
        now_ts_ms_value: int,
        limit: int,
    ) -> list[Any]:
        clauses = ["COALESCE(retention_state, ?) != ?"]
        params: list[Any] = [RETENTION_WARM, RETENTION_GONE]
        domains = set(str(item or "").strip() for item in getattr(plan, "target_domains", ()) if str(item or "").strip())
        if DOMAIN_LIGHTNOVEL_RECOMMENDATION in domains:
            domains.add(DOMAIN_RECOMMENDATION)
        if DOMAIN_RECOMMENDATION in domains:
            domains.add(DOMAIN_LIGHTNOVEL_RECOMMENDATION)
        if domains:
            placeholders = ", ".join("?" for _ in domains)
            clauses.append(f"COALESCE(domain, '') IN ({placeholders})")
            params.extend(sorted(domains))
        scope_keys = _scope_keys_for_collection_plan(plan=plan, now_ts_ms_value=now_ts_ms_value)
        if scope_keys:
            placeholders = ", ".join("?" for _ in scope_keys)
            clauses.append(f"COALESCE(scope_key, '') IN ({placeholders})")
            params.extend(scope_keys)
        if session_id:
            clauses.append("(COALESCE(session_id, '') = ? OR COALESCE(session_id, '') = '')")
            params.append(session_id)
        elif person_id:
            clauses.append("(COALESCE(person_id, '') = ? OR COALESCE(person_id, '') = '')")
            params.append(person_id)
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
                }
            )
        return candidates

    def build_semantic_proposals(self, *, limit: int = 80, max_records: int = 24) -> list[dict[str, Any]]:
        """Extract durable semantic facts from trusted episodes and collections."""

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
            (TRUTH_VERIFIED, STATUS_ACTIVE, RETENTION_COLD, RETENTION_GONE, max(1, int(limit))),
        ).fetchall()
        proposals_by_key: dict[str, dict[str, Any]] = {}
        for row in rows:
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
            key=lambda item: (-_clamp_unit(item.get("confidence")), str(item.get("summary") or "")),
        )
        return proposals[: max(1, int(max_records))]

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

    def distill_semantic_memories(
        self,
        *,
        limit: int = 80,
        max_records: int = 12,
        ts_ms: int | None = None,
        operator_node_id: str = "life_memory_consolidator",
    ) -> dict[str, Any]:
        created = int(ts_ms if ts_ms is not None else now_ts_ms())
        proposals = self.build_semantic_proposals(limit=limit, max_records=max_records)
        written: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        for proposal in proposals:
            semantic_key = _clean_text(proposal.get("semantic_key"))
            if not semantic_key:
                continue
            source_ids = _semantic_source_memory_ids(proposal)
            if not source_ids:
                continue
            source_hash = hashlib.sha256(f"life_semantic:v1:{semantic_key}".encode("utf-8", errors="ignore")).hexdigest()
            existing = self.store.ledger.get_semantic_memory_by_source_hash(source_hash)
            if existing is not None:
                skipped.append(
                    {
                        "semantic_id": existing.get("semantic_id"),
                        "reason_code": "SEMANTIC_MEMORY_EXISTS",
                        "semantic_key": semantic_key,
                    }
                )
                continue
            semantic_id = f"sem_life_{_stable_hash('life_semantic_id:v1:' + semantic_key, size=24)}"
            source_tags = ["source:life_episode"]
            if list(proposal.get("source_collection_ids") or []):
                source_tags.append("source:life_memory_collection")
            tags = list(dict.fromkeys([*list(proposal.get("tags") or []), *source_tags, "life_consolidated"]))
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

    def run_once(
        self,
        *,
        now_ts_ms_value: int | None = None,
        replay_limit: int = 12,
        forgetting_batch_limit: int = 500,
        semantic_limit: int = 12,
    ) -> dict[str, Any]:
        current = int(now_ts_ms_value if now_ts_ms_value is not None else now_ts_ms())
        forgetting = self.store.apply_forgetting_pass(
            now_ts_ms_value=current,
            batch_limit=forgetting_batch_limit,
        )
        semantic_distillation = LifeMemoryConsolidator(store=self.store).distill_semantic_memories(
            limit=max(replay_limit * 4, semantic_limit * 3, 12),
            max_records=max(1, int(semantic_limit)),
            ts_ms=current,
            operator_node_id="memory_sleep_replay",
        )
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
            "forgetting": forgetting,
            "semantic_distillation": semantic_distillation,
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


def _collection_day_key(ts_ms: int) -> str:
    return datetime.fromtimestamp(int(ts_ms) / 1000.0, tz=_LOCAL_MEMORY_TZ).strftime("%Y-%m-%d")


def _scope_keys_for_collection_plan(*, plan: Any, now_ts_ms_value: int) -> list[str]:
    scope = str(getattr(plan, "temporal_scope", "") or "").strip()
    today = datetime.fromtimestamp(int(now_ts_ms_value) / 1000.0, tz=_LOCAL_MEMORY_TZ).date()
    if scope in {TEMPORAL_CURRENT, TEMPORAL_RECENT_DAYS, ""}:
        return [COLLECTION_SCOPE_RECENT]
    if scope == TEMPORAL_TODAY:
        return [f"{COLLECTION_SCOPE_DAILY}:{today.isoformat()}"]
    if scope == TEMPORAL_YESTERDAY:
        return [f"{COLLECTION_SCOPE_DAILY}:{(today - timedelta(days=1)).isoformat()}"]
    if scope == TEMPORAL_DAY_BEFORE_YESTERDAY:
        return [f"{COLLECTION_SCOPE_DAILY}:{(today - timedelta(days=2)).isoformat()}"]
    if scope == TEMPORAL_ALL_TIME:
        return []
    return [COLLECTION_SCOPE_RECENT]


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


def _collection_item_key(*, episode_id: str, item: Mapping[str, Any], index: int) -> str:
    stable = _collection_item_source_ref(item) or _collection_item_title(item) or str(index)
    return _stable_hash(f"{episode_id}:{index}:{stable}", size=28)


def _collection_item_title(item: Mapping[str, Any]) -> str:
    for key in ("title", "title_display", "name", "label"):
        value = _clean_text(item.get(key), limit=160)
        if value:
            return value
    return ""


def _collection_item_summary(item: Mapping[str, Any]) -> str:
    parts: list[str] = []
    for label, keys in (
        ("看点", ("selection_reason", "rationale", "reason", "why", "look_point")),
        ("简介", ("intro_snippet", "intro", "description", "summary", "abstract")),
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
    source = _clean_text(item.get("source_ref") or _collection_item_source_ref(item), limit=260)
    parts = [f"{index}. {title}"]
    if summary:
        parts.append(summary)
    if source and source not in summary:
        parts.append(f"详情：{source}")
    return "｜".join(parts)


def _collection_scope_boost(collection: Mapping[str, Any], *, plan: Any, now_ts_ms_value: int) -> float:
    scope_key = _clean_text(collection.get("scope_key"))
    target_scope_keys = set(_scope_keys_for_collection_plan(plan=plan, now_ts_ms_value=now_ts_ms_value))
    boost = 0.0
    if target_scope_keys and scope_key in target_scope_keys:
        boost += 0.24
    if scope_key == COLLECTION_SCOPE_RECENT and str(getattr(plan, "temporal_scope", "")) in {TEMPORAL_CURRENT, TEMPORAL_RECENT_DAYS}:
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
    return list(dict.fromkeys(ids))


def _semantic_proposal_from_collection(store: LifeMemoryStore, collection: Mapping[str, Any]) -> dict[str, Any] | None:
    collection_id = _clean_text(collection.get("collection_id"))
    if not collection_id:
        return None
    metadata = _json_loads(collection.get("metadata"), fallback={})
    if not isinstance(metadata, dict):
        metadata = {}
    if bool(metadata.get("e2e_probe")) or bool(metadata.get("test_probe")):
        return None
    domain = _clean_text(collection.get("domain"))
    collection_kind = _clean_text(collection.get("collection_kind"))
    scope_key = _clean_text(collection.get("scope_key"))
    item_count = int(collection.get("item_count") or 0)
    if item_count <= 0:
        return None
    if collection_kind != COLLECTION_RECOMMENDATION and "recommendation" not in collection_kind:
        return None
    items = store._list_collection_items(collection_id=collection_id, limit=32)
    if not items:
        return None
    titles = [
        _clean_text(item.get("item_title") or _collection_item_title(item), limit=80)
        for item in items
        if _clean_text(item.get("item_title") or _collection_item_title(item))
    ]
    if not titles:
        return None
    source_episode_ids = [
        _clean_text(item.get("episode_id"))
        for item in items
        if _clean_text(item.get("episode_id"))
    ]
    source_refs = [
        _clean_text(item.get("evidence_ref") or item.get("source_ref"))
        for item in items
        if _clean_text(item.get("evidence_ref") or item.get("source_ref"))
    ]
    person_id = _clean_text(collection.get("person_id")) or "global"
    domain_label = "轻小说推荐" if domain == DOMAIN_LIGHTNOVEL_RECOMMENDATION else (domain.replace("_", " ") or "推荐")
    scope_label = _semantic_collection_scope_label(scope_key)
    title_part = "、".join(titles[:10])
    if len(titles) > 10:
        title_part += f"等 {len(titles)} 个条目"
    summary = f"经历集合：{scope_label}{domain_label}记录了 {item_count} 个条目：{title_part}。"
    if source_episode_ids:
        summary += f" 可追溯到 {len(set(source_episode_ids))} 个推荐经历。"
    semantic_key = (
        "experience_collection|"
        f"{person_id}|{domain}|{scope_key}|"
        f"{_stable_hash('|'.join(titles[:24]), size=24)}"
    )
    confidence = _semantic_collection_confidence(collection=collection, item_count=item_count)
    tags = [
        "experience_collection",
        "recommendation_collection",
        f"domain:{domain or 'generic'}",
    ]
    if domain == DOMAIN_LIGHTNOVEL_RECOMMENDATION:
        tags.append("lightnovel_recommendation")
    return {
        "semantic_key": semantic_key,
        "semantic_kind": "experience_collection",
        "summary": summary,
        "confidence": confidence,
        "tags": tags,
        "source_episode_ids": list(dict.fromkeys(source_episode_ids)),
        "source_collection_ids": [collection_id],
        "source_refs": list(dict.fromkeys([f"life_memory_collection://{collection_id}", *source_refs])),
        "distill_strategy": "life_memory_collection_rule_consolidation",
    }


def _semantic_collection_scope_label(scope_key: str) -> str:
    value = _clean_text(scope_key)
    if value.startswith(f"{COLLECTION_SCOPE_DAILY}:"):
        return value.split(":", 1)[1] + " 的"
    if value == COLLECTION_SCOPE_RECENT:
        return "最近的"
    return f"{value} "


def _semantic_collection_confidence(*, collection: Mapping[str, Any], item_count: int) -> float:
    strength = _clamp_unit(collection.get("memory_strength"), default=_clamp_unit(collection.get("salience"), default=0.4))
    count_bonus = min(0.12, max(0, int(item_count)) * 0.015)
    return round(_clamp_unit(0.58 + strength * 0.24 + count_bonus), 4)


def _semantic_proposals_from_episode(
    episode: Mapping[str, Any],
    evidence_rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    metadata = _episode_metadata_dict(episode)
    if bool(metadata.get("e2e_probe")) or bool(metadata.get("test_probe")):
        return []
    action = _clean_text(episode.get("action") or metadata.get("action_kind")).lower()
    event_source = _clean_text(metadata.get("event_source")).lower()
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
        key_text = re.sub(r"\s+", "", summary.lower())
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
            }
        )
    return proposals


def _semantic_source_texts(
    episode: Mapping[str, Any],
    evidence_rows: Sequence[Mapping[str, Any]],
) -> list[str]:
    texts: list[str] = []

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
        if kind in {EVIDENCE_USER_TEXT, EVIDENCE_GATEWAY_MESSAGE, EVIDENCE_SCHEDULER_EVENT, EVIDENCE_TOOL_RESULT, EVIDENCE_REFLECTION}:
            add(content)
    if texts:
        return texts[:8]
    summary = _clean_text(episode.get("summary"), limit=900)
    if summary:
        for line in re.split(r"[\n\r]+", summary):
            if re.match(r"^\s*Assistant\s*[:：]", line):
                continue
            cleaned = _clean_text(re.sub(r"^(User|Assistant|Perception)\s*[:：]\s*", "", line), limit=700)
            add(cleaned)
    return texts[:8]


def _semantic_summary_for_text(text: str, *, episode: Mapping[str, Any]) -> dict[str, Any] | None:
    statement = _normalize_semantic_statement(text)
    if len(statement) < 4 or _semantic_text_is_sensitive_or_noise(statement):
        return None
    compact = re.sub(r"\s+", "", statement).lower()
    action = _clean_text(episode.get("action")).lower()
    tags: list[str] = []
    kind = ""
    prefix = ""
    base_confidence = 0.60

    if _semantic_is_user_preference(compact):
        kind = "user_preference"
        prefix = "用户偏好"
        tags = ["user_preference", "preference"]
        base_confidence = 0.82
    elif _semantic_is_commitment(compact):
        kind = "commitment"
        prefix = "承诺和待办"
        tags = ["commitment", "open_loop"]
        base_confidence = 0.78
    elif _semantic_is_self_change(compact, action=action):
        kind = "self_change"
        prefix = "自我改变"
        tags = ["self_change", "capability_change"]
        base_confidence = 0.76
    elif _semantic_is_failure_lesson(compact, action=action):
        kind = "failure_lesson"
        prefix = "故障经验"
        tags = ["failure_lesson", "operations"]
        base_confidence = 0.74
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
    }


def _normalize_semantic_statement(text: str) -> str:
    value = _clean_text(text, limit=700)
    value = re.sub(r"^(User|Assistant|Perception)\s*[:：]\s*", "", value).strip()
    value = re.sub(r"^(记忆线索|长期记忆|召回线索)\s*[:：]\s*", "", value).strip()
    value = re.sub(r"^行动记忆：[^；;]{1,120}[；;]\s*", "", value).strip()
    value = re.sub(r"^(记住|请记住|以后记住)[，,：:\s]*", "", value).strip()
    return value.strip("。；; \n\t")


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
    if any(token in compact for token in ("api_key", "apikey", "secret", "cookie", "token=", "authorization:", "密码", "密钥")):
        return True
    if any(token in compact for token in ("heartbeat", "health_check", "emotion_decay_tick", "inner_world_numeric_tick", "silence_tick")):
        return True
    if any(token in compact for token in ("网页深读结果", "lightnovel_recommendation_deep_review", "正文内容足够支撑判断")):
        return True
    if any(
        token in compact
        for token in (
            "codexproductionjobcodex_lightnovel_recommender",
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
            "latency_ms",
            "decision=approve",
        )
    ):
        return True
    return False


def _should_sleep_replay_candidate(item: Mapping[str, Any]) -> bool:
    """Keep replay for durable experiences, not transport/debug chatter."""

    summary = _clean_text(item.get("summary"), limit=1200)
    if not summary or _semantic_text_is_sensitive_or_noise(summary):
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
    if _semantic_is_user_preference(compact) or _semantic_is_stable_fact(compact):
        return True
    kind = _clean_text(item.get("episode_kind"))
    if kind == EPISODE_CHAT:
        return True
    return _clamp_unit(item.get("memory_strength"), default=_clamp_unit(item.get("salience"))) >= 0.88


def _semantic_is_user_preference(compact: str) -> bool:
    preference_tokens = (
        "用户喜欢",
        "我喜欢",
        "用户不喜欢",
        "我不喜欢",
        "用户要求",
        "要求",
        "偏好",
        "以后",
        "不要",
        "别再",
        "优先使用",
        "主链不要",
        "不要用pro",
        "不用pro",
        "分段",
        "卡片",
        "拟人化发送",
    )
    return any(token in compact for token in preference_tokens)


def _semantic_is_commitment(compact: str) -> bool:
    return any(token in compact for token in ("承诺", "待办", "todo", "openloop", "后续要", "下次继续", "未完成"))


def _semantic_is_self_change(compact: str, *, action: str) -> bool:
    if action in {"memory_compaction", "model_route_update", "capability_change"}:
        return True
    return any(token in compact for token in ("模型路由", "记忆系统", "能力被删除", "删除资源管理器", "策略被用户纠正", "自我改变", "主链迁移", "改成"))


def _semantic_is_failure_lesson(compact: str, *, action: str) -> bool:
    if "failed" in action or "failure" in action:
        return True
    return any(
        token in compact
        for token in (
            "失败原因",
            "失败",
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
    return any(token in compact for token in ("onebot在edge", "onebot在", "qq会话", "conversation_id", "session_id", "在edge", "不在本机"))


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
]
