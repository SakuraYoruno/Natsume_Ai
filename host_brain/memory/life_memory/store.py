"""LifeMemoryStore — SQLite-backed life episode store."""

from __future__ import annotations

import re
import time
import uuid
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
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

