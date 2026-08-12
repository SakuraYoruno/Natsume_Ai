"""LifeMemoryStore — SQLite-backed life episode store."""

from __future__ import annotations

import re
import time
import uuid
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.persistence.life_memory_repository import LifeMemoryRepository
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

from .constants import *
from .constants import (
    _ANAPHORIC_EVENT_KINDS,
    _LOCAL_MEMORY_TZ,
    _RECALL_FETCH_LIMIT_FACTOR,
)
from .models import EpisodeRecallPacket
from .utils import (
    now_ts_ms,
    _clean_text,
    _clamp_unit,
    _coerce_int,
    _stable_hash,
    _json_loads,
    _json_dumps,
    _recall_like_terms,
    _looks_like_anaphoric_selection_query,
    _episode_memory_class_from_metadata,
    _episode_recall_target_memory_classes,
    _metadata_domain_set,
    _looks_like_recorded_failure_candidate,
    _looks_like_correction_fact_candidate,
    _looks_like_explicit_correction_event,
    _looks_like_correction_question_echo,
    _chat_summary_user_text,
    _looks_like_semantic_lookup_echo,
    _looks_like_preference_candidate,
    _looks_like_substantive_evidence_candidate,
    _looks_like_absence_or_generic_evidence_answer,
    _episode_metadata_dict,
    _has_any_marker,
    _is_synthetic_life_record,
    _allow_synthetic_life_recall_for_scope,
    _session_allows_cross_scope_recall,
    _record_matches_life_recall_scope,
    _is_significant_anaphoric_episode,
    _metadata_float,
    _importance_keyword_boost,
    _decay_profile_for_episode,
    _compute_memory_strength,
    _retention_state_for_strength,
    _classify_life_memory_policy,
    _with_memory_policy_metadata,
    _decay_rate_per_day,
    _normalize_open_loop_status,
    _normalize_refs,
    _open_loop_close_reason_for_status,
    _owner_alias_person_ids,
    strong_evidence_refs,
    derive_episode_id,
    derive_evidence_id,
    derive_episode_identity_key,
    _episode_truth_from_refs,
    _merge_metadata,
    _semantic_statement_is_lookup_question,
    _semantic_is_relationship_feedback,
    _semantic_commitment_status,
    _preference_query_wants_relationship_feedback,
    _query_wants_closed_open_loop,
)

from .semantic import (  # noqa: E402
    _attachment_looks_visual,
    _collection_day_key,
    _collection_domain_for_episode,
    _collection_id,
    _collection_item_canonical_key,
    _collection_item_episode_anchor_is_synthetic,
    _collection_item_has_recallable_anchor,
    _collection_item_is_synthetic,
    _collection_item_key,
    _collection_item_recall_line,
    _collection_item_resolved_canonical_title,
    _collection_item_review_evidence_metadata,
    _collection_item_source_ref,
    _collection_item_summary,
    _collection_item_title,
    _collection_scope_boost,
    _collection_scope_key,
    _collection_scope_window_days,
    _collection_summary_text,
    _collection_summary_text_for_collection,
    _entity_id,
    _entity_key_from_item,
    _entity_mention_matches_daily_scope,
    _entity_mention_recall_line,
    _entity_normalized_key,
    _plan_requires_exact_collection_count,
    _scope_keys_for_collection_plan,
    _semantic_referenced_episode_ids,
    _trusted_active_episode_ids,
)


LIFE_MEMORY_DIRECT_CHAT_WRITE_FORBIDDEN = "LIFE_MEMORY_DIRECT_CHAT_WRITE_FORBIDDEN"


def _background_salience(*, source: str, status: str | None, detail: str | None) -> float:
    """Compute a baseline salience score for a background episode.

    Higher scores for inner-world events (thought/feeling), perception
    events (vision/voice), and error/failure statuses.
    """
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


def _chat_salience(*, user_message: str, assistant_reply: str, has_perception: bool) -> float:
    """Compute salience for a chat episode."""
    text = f"{user_message}\n{assistant_reply}".lower()
    score = 0.36
    if has_perception:
        score += 0.18
    if any(token in text for token in ("记住", "以后", "别忘", "纠正", "不对", "错了", "remember", "wrong")):
        score += 0.20
    if len(text) > 500:
        score += 0.06
    return _clamp_unit(score)


class LifeMemoryStore:
    """SQLite-backed life episode store."""

    def __init__(self, *, ledger: SQLiteLedger) -> None:
        self.ledger = ledger
        self.persistence = LifeMemoryRepository.from_ledger(ledger)

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

        def merge_existing(current: Mapping[str, Any]) -> dict[str, Any]:
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
            next_strength = _compute_memory_strength(
                salience=max(_clamp_unit(current.get("salience")), base_salience),
                episode_kind=kind,
                summary=str(next_summary or ""),
                metadata=next_metadata,
                existing_strength=max(_clamp_unit(current.get("memory_strength")), computed_strength),
            )
            return {
                "episode_id": eid,
                "episode_kind": kind,
                "thread_id": _clean_text(thread_id) or None,
                "person_id": _clean_text(person_id) or None,
                "session_id": _clean_text(session_id) or None,
                "actor_id": _clean_text(actor_id) or None,
                "intent": _clean_text(intent) or None,
                "action": _clean_text(action) or None,
                "result": _clean_text(result) or None,
                "primary_ref": primary,
                "summary": next_summary,
                "salience": base_salience,
                "base_salience": base_salience,
                "memory_strength": next_strength,
                "decay_profile_id": _decay_profile_for_episode(
                    episode_kind=kind,
                    salience=max(_clamp_unit(current.get("salience")), base_salience),
                    metadata=next_decay_metadata,
                ),
                "retention_state": _retention_state_for_strength(
                    next_strength, truth_state=next_truth, explicit=retention_state
                ),
                "retention_until_ts_ms": int(retention_until_ts_ms) if retention_until_ts_ms is not None else None,
                "forget_reason": _clean_text(forget_reason) or None,
                "truth_state": next_truth,
                "status": _clean_text(status) or STATUS_ACTIVE,
                "last_touched_ts_ms": created,
                "metadata": _json_dumps(next_metadata),
            }

        self.persistence.upsert_episode_record(
            {
                "episode_id": eid, "episode_kind": kind,
                "thread_id": _clean_text(thread_id) or None,
                "person_id": _clean_text(person_id) or None,
                "session_id": _clean_text(session_id) or None,
                "actor_id": _clean_text(actor_id) or None,
                "intent": _clean_text(intent) or None, "action": _clean_text(action) or None,
                "result": _clean_text(result) or None, "primary_ref": primary,
                "summary": summary_text, "salience": base_salience,
                "base_salience": base_salience, "memory_strength": computed_strength,
                "decay_profile_id": decay_profile, "retention_state": computed_retention,
                "last_recalled_ts_ms": None, "recall_count": 0,
                "reinforcement_count": 0, "compressed_into_episode_id": None,
                "retention_until_ts_ms": int(retention_until_ts_ms) if retention_until_ts_ms is not None else None,
                "forget_reason": _clean_text(forget_reason) or None, "truth_state": truth,
                "status": _clean_text(status) or STATUS_ACTIVE,
                "created_ts_ms": created, "last_touched_ts_ms": created,
                "metadata": _json_dumps(metadata_payload or {}),
            },
            merge_existing=merge_existing,
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
        self.persistence.attach_evidence_record(
            {
                "evidence_id": ev_id,
                "episode_id": eid,
                "evidence_kind": kind,
                "source_ref": source,
                "request_id": _clean_text(request_id) or None,
                "content": _clean_text(content, limit=4000),
                "payload": _json_dumps(dict(payload or {})),
                "trust_level": _clean_text(trust_level) or TRUTH_CANDIDATE,
                "created_ts_ms": created,
            }
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
        def merge_existing(current: Mapping[str, Any]) -> dict[str, Any]:
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
            return {
                "episode_id": eid,
                "person_id": _clean_text(person_id) or None,
                "session_id": _clean_text(session_id) or None,
                "title": _clean_text(title, limit=180) or None,
                "detail": _clean_text(detail, limit=900) or None,
                "status": next_status,
                "source_ref": _clean_text(source_ref) or None,
                "updated_ts_ms": created,
                "closed_ts_ms": closed_ts,
                "close_episode_id": close_episode_id,
                "close_reason": next_close_reason,
                "salience": _clamp_unit(salience),
                "memory_strength": _clamp_unit(memory_strength),
                "metadata": _json_dumps(_merge_metadata(current.get("metadata"), metadata_payload)),
                "semantic_key": key,
            }

        self.persistence.upsert_open_loop_record(
            {
                "open_loop_id": open_loop_id, "semantic_key": key, "episode_id": eid,
                "person_id": _clean_text(person_id) or None,
                "session_id": _clean_text(session_id) or None,
                "title": _clean_text(title, limit=180) or None,
                "detail": _clean_text(detail, limit=900) or None,
                "status": state, "source_ref": _clean_text(source_ref) or None,
                "opened_ts_ms": created, "updated_ts_ms": created,
                "closed_ts_ms": created if terminal else None,
                "close_episode_id": eid if terminal else None,
                "close_reason": _clean_text(close_reason, limit=300) or None,
                "salience": _clamp_unit(salience),
                "memory_strength": _clamp_unit(memory_strength),
                "metadata": _json_dumps(metadata_payload),
            },
            merge_existing=merge_existing,
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
        rows = self.persistence.list_open_loop_records(
            status=_normalize_open_loop_status(status) if status else None,
            person_id=person or None,
            session_id=session or None,
            limit=max(1, int(limit)),
        )
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
        self.persistence.upsert_collection_record(
            {
                "collection_id": cid, "collection_kind": kind, "domain": domain_key,
                "scope_key": scope, "person_id": person, "session_id": session,
                "title": _clean_text(title, limit=240),
                "summary": _clean_text(summary, limit=1200), "item_count": 0,
                "salience": _clamp_unit(salience),
                "memory_strength": _clamp_unit(memory_strength),
                "retention_state": _clean_text(retention_state).upper() or RETENTION_WARM,
                "created_ts_ms": created, "updated_ts_ms": created,
                "metadata": _json_dumps(metadata_payload),
            }
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
        def scope_cutoff(scope_key: Any, anchor_ts: int) -> int | None:
            days = _collection_scope_window_days(scope_key)
            if days <= 0 or anchor_ts <= 0:
                return None
            anchor_date = datetime.fromtimestamp(anchor_ts / 1000.0, tz=_LOCAL_MEMORY_TZ).date()
            cutoff_date = anchor_date - timedelta(days=max(0, int(days) - 1))
            cutoff_dt = datetime(
                cutoff_date.year,
                cutoff_date.month,
                cutoff_date.day,
                tzinfo=_LOCAL_MEMORY_TZ,
            )
            return int(cutoff_dt.timestamp() * 1000)

        self.persistence.attach_collection_item_record(
            {
                "collection_item_id": item_id, "collection_id": cid,
                "episode_id": eid, "item_key": key,
                "item_title": _clean_text(item_title, limit=300),
                "item_summary": _clean_text(item_summary, limit=900),
                "source_ref": _clean_text(source_ref) or None,
                "evidence_ref": _clean_text(evidence_ref) or None,
                "position": max(0, int(position or 0)),
                "created_ts_ms": created, "metadata": _json_dumps(metadata_payload),
            },
            scope_cutoff=scope_cutoff,
            summary_builder=lambda collection, items: _collection_summary_text_for_collection(
                collection=collection, items=items
            ),
        )
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
        self.persistence.upsert_entity_record(
            {
                "entity_id": entity_id, "entity_kind": kind,
                "canonical_name": name, "normalized_key": key, "domain": domain_key,
                "first_seen_ts_ms": created, "last_seen_ts_ms": created,
                "salience": _clamp_unit(salience),
                "memory_strength": _clamp_unit(memory_strength),
                "retention_state": _clean_text(retention_state).upper() or RETENTION_WARM,
                "metadata": _json_dumps(metadata_payload),
            }
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
        self.persistence.attach_entity_mention_record(
            {
                "mention_id": mention_id, "entity_id": eid,
                "episode_id": _clean_text(episode_id) or None,
                "collection_id": _clean_text(collection_id) or None,
                "collection_item_id": _clean_text(collection_item_id) or None,
                "source_ref": _clean_text(source_ref) or None,
                "evidence_ref": _clean_text(evidence_ref) or None,
                "position": max(0, int(position or 0)),
                "created_ts_ms": created, "metadata": _json_dumps(dict(metadata or {})),
            }
        )
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
            reviewed = self.persistence.apply_collection_item_review(
                collection_item_id=collection_item_id,
                evidence_ref=evidence_ref,
                review_metadata=review_meta,
                updated_ts_ms=created,
            )
            if reviewed is None:
                skipped_count += 1
                continue
            collection_id = _clean_text(reviewed.get("collection_id"))
            position = _coerce_int(reviewed.get("position"))
            source_ref = _clean_text(item.get("detail_url") or reviewed.get("source_ref"))
            updated_count += 1
            for entity_value in list(reviewed.get("entity_ids") or []):
                entity_id = _clean_text(entity_value)
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
        rows = self.persistence.list_collection_index_rows(
            limit=max(1, int(limit)),
            include_non_lightnovel=True,
        )
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
                mention_exists = self.persistence.collection_item_has_entity_mention(
                    entity_id=entity_id,
                    collection_item_id=str(item.get("collection_item_id") or ""),
                )
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
                self.persistence.update_collection_item_metadata(
                    collection_item_id=str(item.get("collection_item_id") or ""),
                    metadata_json=_json_dumps(merged_metadata),
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
        row = self.persistence.get_episode(eid)
        if row is None:
            return {"status": "SKIPPED", "reason_code": "EPISODE_NOT_FOUND", "episode_id": eid}
        episode = dict(row)
        metadata = _json_loads(episode.get("metadata"), fallback={})
        if not isinstance(metadata, dict):
            metadata = {}
        from .semantic import _extract_collection_items_from_metadata  # noqa: PLC0415

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
        rows = self.persistence.list_entity_records(
            domains=sorted(domains),
            limit=max(8, int(top_k) * 8),
        )
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
        self.persistence.link_episode_record(
            {
                "link_id": lid, "source_episode_id": source,
                "target_episode_id": target, "link_kind": kind,
                "weight": max(0.0, float(weight or 0.0)),
                "created_ts_ms": created,
                "metadata": _json_dumps(dict(metadata or {})),
            }
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
        self.persistence.record_correction(
            {
                "correction_id": correction_id, "episode_id": eid,
                "correction_episode_id": _clean_text(correction_episode_id) or None,
                "correction_kind": kind,
                "reason_code": _clean_text(reason_code) or None,
                "detail": _clean_text(detail, limit=2000),
                "created_ts_ms": created,
                "metadata": _json_dumps(dict(metadata or {})),
            },
            truth_state=next_truth,
            status=next_status,
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
        """In-memory fixture compatibility only.

        Durable production chat writes are owned by ``LifeMemoryService`` and
        ``LifeMemoryRepository``.  Keeping this fail-closed shim lets the large
        recall/consolidation fixture suite build synthetic in-memory episodes
        without preserving a second production mutation authority.
        """
        if str(getattr(self.ledger, "canonical_db_path", "") or "") != ":memory:":
            raise PermissionError(LIFE_MEMORY_DIRECT_CHAT_WRITE_FORBIDDEN)
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
        like_terms = _recall_like_terms(normalized_query)
        target_memory_classes = _episode_recall_target_memory_classes(getattr(query_plan, "query_kind", ""))
        target_domains = {
            _clean_text(domain).lower()
            for domain in getattr(query_plan, "target_domains", ())
            if _clean_text(domain)
        }
        rows, candidate_anaphoric_ids = self.persistence.recall_episode_candidates(
            include_suppressed=include_suppressed,
            like_terms=like_terms,
            memory_classes=target_memory_classes,
            domains=sorted(target_domains),
            anaphoric_kinds=_ANAPHORIC_EVENT_KINDS,
            anaphoric=anaphoric_query,
            person_id=scoped_person_id or None,
            session_id=scoped_session_id or None,
            limit=limit,
            significant_anaphoric=_is_significant_anaphoric_episode,
        )
        anaphoric_episode_ids = candidate_anaphoric_ids
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
        def calculate(row: Mapping[str, Any]) -> tuple[float, str]:
            previous = _clamp_unit(row.get("memory_strength"), default=0.0)
            last_recalled = int(row.get("last_recalled_ts_ms") or 0)
            recall_count = max(0, int(row.get("recall_count") or 0))
            # 强度越高，剩余提升空间越小；短时间重复召回进一步衰减。
            headroom = max(0.0, 1.0 - previous)
            repetition_decay = 0.2 if last_recalled and current - last_recalled < 5 * 60 * 1000 else 1.0
            historical_decay = 1.0 / (1.0 + min(recall_count, 100) * 0.02)
            increment = max(0.0, float(amount or 0.0)) * headroom * repetition_decay * historical_decay
            strength = _clamp_unit(previous + increment)
            return strength, _retention_state_for_strength(
                strength, truth_state=row.get("truth_state"), explicit=None
            )

        reinforced = self.persistence.reinforce_episode(
            episode_id=eid,
            recalled_at_ms=current,
            calculate=calculate,
        )
        if reinforced is None:
            return {"status": "SKIPPED", "reason_code": "EPISODE_NOT_FOUND", "episode_id": eid}
        next_strength, next_state = reinforced
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
        self.persistence.record_compaction(
            {
                "compaction_id": compaction_id,
                "source_episode_ids": _json_dumps(source_ids),
                "compressed_episode_id": compressed_id,
                "summary": compact_summary,
                "dropped_details": _json_dumps(list(dropped_details or [])),
                "kept_facts": _json_dumps(list(kept_facts or [])),
                "created_ts_ms": created,
                "metadata": _json_dumps(dict(metadata or {})),
            },
            source_episode_ids=source_ids,
            cold_state=RETENTION_COLD,
            gone_state=RETENTION_GONE,
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
        semantic_referenced_episode_ids = _semantic_referenced_episode_ids(self.persistence)
        rows = self.persistence.list_episode_records(
            statuses=(STATUS_ACTIVE,),
            excluded_truth_states=(TRUTH_SUPPRESSED, TRUTH_CORRECTED),
            excluded_retention_states=(RETENTION_GONE,),
            order="oldest",
            limit=max(1, int(batch_limit)),
        )
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
        self.persistence.apply_forgetting_updates(updates)
        gone_reason_backfilled = self.persistence.fill_missing_forget_reasons(
            retention_state=RETENTION_GONE,
            reason="historical_low_value_retired",
        )
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

        rows = self.persistence.list_policy_backfill_records(
            limit=max(1, int(batch_limit)),
            include_existing=include_existing,
        )
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
            self.persistence.update_episode_policy_batch(updates)
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
        collection = self.persistence.get_collection(_clean_text(collection_id))
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

    def _fetch_collection_rows_for_plan(
        self,
        *,
        plan: Any,
        person_id: str | None,
        session_id: str | None,
        now_ts_ms_value: int,
        limit: int,
    ) -> list[Any]:
        domains = set(str(item or "").strip() for item in getattr(plan, "target_domains", ()) if str(item or "").strip())
        if DOMAIN_LIGHTNOVEL_RECOMMENDATION in domains:
            domains.add(DOMAIN_RECOMMENDATION)
        if DOMAIN_RECOMMENDATION in domains:
            domains.add(DOMAIN_LIGHTNOVEL_RECOMMENDATION)
        scope_keys = _scope_keys_for_collection_plan(plan=plan, now_ts_ms_value=now_ts_ms_value)
        day_ranges: list[tuple[int, int]] = []
        if scope_keys and _plan_requires_exact_collection_count(plan):
            for key in scope_keys:
                if not _clean_text(key).startswith(f"{COLLECTION_SCOPE_DAILY}:"):
                    continue
                try:
                    day_dt = datetime.strptime(
                        _clean_text(key).split(":", 1)[1], "%Y-%m-%d"
                    ).replace(tzinfo=_LOCAL_MEMORY_TZ)
                except ValueError:
                    continue
                day_ranges.append(
                    (
                        int(day_dt.timestamp() * 1000),
                        int((day_dt + timedelta(days=1)).timestamp() * 1000),
                    )
                )
        return self.persistence.find_collection_records(
            domains=sorted(domains),
            scope_keys=scope_keys,
            delivery_day_ranges=day_ranges,
            person_id=_clean_text(person_id) or None,
            session_id=_clean_text(session_id) or None,
            owner_aliases=_owner_alias_person_ids(person_id),
            limit=max(1, int(limit)),
        )

    def _list_collection_items(self, *, collection_id: str, limit: int = 24) -> list[dict[str, Any]]:
        rows = self.persistence.list_collection_items(
            collection_id=_clean_text(collection_id),
            limit=max(1, int(limit)),
        )
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
        scoped_session = _clean_text(session_id)
        scoped_person = _clean_text(person_id)
        rows = self.persistence.list_entity_mentions(
            entity_id=_clean_text(entity_id),
            person_id=scoped_person or None,
            session_id=scoped_session or None,
            owner_aliases=_owner_alias_person_ids(scoped_person),
            limit=max(1, int(limit)),
        )
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
        rows = self.persistence.list_evidence(episode_id=episode_id)
        items: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item.pop("id", None)
            payload = _json_loads(item.get("payload"), fallback={})
            item["payload"] = payload if isinstance(payload, dict) else {}
            items.append(item)
        return items

    def _list_corrections(self, *, episode_id: str) -> list[dict[str, Any]]:
        rows = self.persistence.list_corrections(episode_id=episode_id)
        items: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item.pop("id", None)
            metadata = _json_loads(item.get("metadata"), fallback={})
            item["metadata"] = metadata if isinstance(metadata, dict) else {}
            items.append(item)
        return items
