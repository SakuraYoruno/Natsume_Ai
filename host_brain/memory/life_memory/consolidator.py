"""LifeMemoryConsolidator — build semantic candidates from trusted life episodes."""

from __future__ import annotations

import hashlib
import re
import time
from collections.abc import Mapping, Sequence
from typing import Any

from .constants import *
from .constants import _SEMANTIC_PRIORITY_MEMORY_CLASSES  # noqa: F811 (private name not exported via *)
from .utils import (
    _clean_text,
    _clamp_unit,
    _episode_metadata_dict,
    _is_synthetic_life_record,
    _json_loads,
    _json_dumps,
    _stable_hash,
    _normalize_refs,
    _metadata_float,
    now_ts_ms,
    _semantic_statement_is_lookup_question,
)
from .store import LifeMemoryStore
from . import semantic as _semantic_mod
from .semantic import (  # noqa: PLC0415 — explicit private imports from semantic
    _count_reason_codes,
    _merge_collection_semantic_proposal,
    _merge_semantic_summaries,
    _semantic_action_pattern_proposals,
    _semantic_existing_summary_should_be_replaced,
    _semantic_kind_priority,
    _semantic_memory_prune_reason,
    _semantic_proposal_from_collection,
    _semantic_proposal_from_correction_row,
    _semantic_proposal_from_entity,
    _semantic_proposal_matches_query,
    _semantic_proposals_are_same_collection_scope,
    _semantic_proposals_from_episode,
    _semantic_source_memory_ids,
    _semantic_source_texts,
    _semantic_taxonomy_tags,
    _sqlite_table_exists,
    _stale_collection_semantic_ids,
)
from .utils import (  # noqa: PLC0415 — explicit private imports from utils
    _episode_memory_class_from_metadata,
    _normalize_open_loop_status_tags,
    _open_loop_status_from_tags,
    _open_loop_title_from_summary,
    _semantic_skip_reason_for_texts,
)

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


