"""MemorySleepReplayTask — offline replay and compaction for sleep-time memory processing."""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from typing import Any

from .constants import *
from .utils import (
    now_ts_ms,
    _clean_text,
    _episode_metadata_dict,
    _is_synthetic_life_record,
    _should_sleep_replay_candidate,
    _json_loads,
    _clamp_unit,
)
from .store import LifeMemoryStore
from .consolidator import LifeMemoryConsolidator
from . import semantic as _semantic_mod
from .semantic import (  # noqa: PLC0415
    _merge_semantic_cleanup_reports,
    _semantic_referenced_episode_ids,
    _should_compact_sleep_candidate,
    _sleep_compaction_group_key,
    _sleep_compaction_payload,
)

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
        semantic_referenced_episode_ids = _semantic_referenced_episode_ids(
            self.store.persistence
        )
        rows = self.store.persistence.list_episode_records(
            truth_states=(TRUTH_VERIFIED, TRUTH_NARRATIVE_ONLY),
            statuses=(STATUS_ACTIVE,),
            retention_states=(RETENTION_WARM, RETENTION_COLD),
            uncompressed_only=True,
            order="created",
            limit=max(1, int(group_limit)) * max(2, int(max_group_size)) * 4,
        )
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
