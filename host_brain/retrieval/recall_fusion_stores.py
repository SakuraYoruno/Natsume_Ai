"""五路召回 Store 搜索方法模块（Phase XIII 精简后）。

从 recall_fusion.py 拆分，包含 RecallFusionServiceContract 的各路 store 搜索实现：
  - Route 1: episodic_vector（Chroma 向量检索）
  - Route 2: sqlite_ledger（SQLite 意图日志 + 记忆操作日志）
  - Route 3: semantic_memory（SQLite 长期语义记忆）
  - Route 4: neo4j_bond（Neo4j 关系图 / SQLite bond 日志降级）
  - Route 5: world_canon（世界观典籍向量投影 + 文件降级）
  - Route 6: artifact_vault（制品库 SSOT 接口）

Phase XIII 已废弃的路由（已彻底删除，不再保留向后兼容代码）：
  - persona_utterance → 已从当前主链移除；表层质感只保留 Soul.md 和少量原台词 quote 参考
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Mapping

from host_brain.persistence.life_memory_repository import (
    LifeMemoryRepository,
    SemanticLineageBatch,
)
from host_brain.retrieval.recall_schemas import RecallRouteConfig

logger = logging.getLogger("Natsume.retrieval.recall_fusion")

# ── 常量（与 recall_fusion.py 保持一致）────────────────────────────────────
DEFAULT_RECALL_COLLECTION = "episodic"
DEFAULT_CANON_VECTOR_COLLECTION = "canon"
CANON_VECTOR_FETCH_FACTOR = 5
SQLITE_SESSION_BOOST = 0.25
SQLITE_RECENCY_BOOST_5M = 0.20
SQLITE_RECENCY_BOOST_1H = 0.10
SQLITE_RECENCY_BOOST_1D = 0.05
_SESSION_REF_PREFIX = "chat://session/"

STORE_SQLITE_LEDGER = "sqlite_ledger"
STORE_LIFE_EPISODE = "life_episode"
STORE_LIFE_MEMORY_COLLECTION = "life_memory_collection"
STORE_LIFE_MEMORY_ENTITY = "life_memory_entity"
STORE_NEO4J_BOND = "neo4j_bond"
STORE_KNOWLEDGE_GRAPH = "knowledge_graph"
STORE_EPISODIC_VECTOR = "episodic_vector"
STORE_SEMANTIC_MEMORY = "semantic_memory"
STORE_WORLD_CANON = "world_canon"
STORE_ARTIFACT_VAULT = "artifact_vault"
_SCHEDULER_RECALL_STATUS_ALLOWLIST = {
    "ERROR",
    "FAILED",
    "DEGRADED",
    "SUSPENDED",
    "DEFERRED",
    "HEARTBEAT_ALERT",
    "EDGE_PROBE_ALERT",
}
_SCHEDULER_RECALL_OK_TASK_ALLOWLIST = {
    "daily_recommendation",
    "diary_generate",
    "diary_writer_daily",
    "dream_generator_sleep",
    "lightnovel_recommender_daily",
    "long_term_memory_distillation",
    "distillation_engine_periodic",
    "memory_distillation",
    "emergent_growth_weekly",
    "evolution_cycle_daily",
}


def _scheduler_event_is_recallable(*, task_id: str, status: str, reason_code: str, detail: str) -> bool:
    status_token = str(status or "").strip().upper()
    task_key = str(task_id or "").strip().lower()
    reason = str(reason_code or "").strip().upper()
    if status_token in _SCHEDULER_RECALL_STATUS_ALLOWLIST:
        return True
    if status_token == "OK" and task_key in _SCHEDULER_RECALL_OK_TASK_ALLOWLIST:
        return True
    if reason == "LLM_POOL_EXHAUSTED":
        return True
    return bool(detail and status_token in {"ERROR", "FAILED", "DEGRADED"})


def search_route(
    self: Any,
    *,
    route: RecallRouteConfig,
    query: str,
    query_embedding: list[float],
    person_id: str | None = None,
    session_id: str | None = None,
    now_ts_ms: int | None = None,
) -> list[dict[str, Any]]:
    """按 store_id 分发到对应的搜索实现。"""
    store_id = str(route.store_id or STORE_EPISODIC_VECTOR).strip().lower()
    if store_id == STORE_EPISODIC_VECTOR:
        collection = str(route.collection or DEFAULT_RECALL_COLLECTION)
        return self.chroma.search(
            query_embedding=query_embedding,
            top_k=int(route.top_k),
            collection=collection,
        )
    if store_id == STORE_SQLITE_LEDGER:
        return search_sqlite_ledger(
            self,
            query=query,
            top_k=int(route.top_k),
            person_id=person_id,
            session_id=session_id,
            now_ts_ms=now_ts_ms,
        )
    if store_id == STORE_LIFE_EPISODE:
        return search_life_episode(
            self,
            query=query,
            top_k=int(route.top_k),
            person_id=person_id,
            session_id=session_id,
            now_ts_ms=now_ts_ms,
        )
    if store_id == STORE_LIFE_MEMORY_COLLECTION:
        return search_life_memory_collection(
            self,
            query=query,
            top_k=int(route.top_k),
            person_id=person_id,
            session_id=session_id,
            now_ts_ms=now_ts_ms,
        )
    if store_id == STORE_LIFE_MEMORY_ENTITY:
        return search_life_memory_entity(
            self,
            query=query,
            top_k=int(route.top_k),
            person_id=person_id,
            session_id=session_id,
            now_ts_ms=now_ts_ms,
        )
    if store_id == STORE_SEMANTIC_MEMORY:
        return search_semantic_memory(
            self,
            query=query,
            top_k=int(route.top_k),
            person_id=person_id,
            session_id=session_id,
            now_ts_ms=now_ts_ms,
        )
    if store_id == STORE_NEO4J_BOND:
        return search_neo4j_bond(self, query=query, top_k=int(route.top_k))
    if store_id == STORE_KNOWLEDGE_GRAPH:
        return search_knowledge_graph(
            self, query=query, top_k=int(route.top_k), person_id=person_id
        )
    if store_id == STORE_WORLD_CANON:
        return search_world_canon(
            self,
            query=query,
            query_embedding=query_embedding,
            top_k=int(route.top_k),
            collection=route.collection,
        )
    if store_id == STORE_ARTIFACT_VAULT:
        return search_artifact_vault(self, query=query, top_k=int(route.top_k))
    raise RuntimeError(f"unsupported recall store_id: {store_id}")


def search_life_episode(
    self: Any,
    *,
    query: str,
    top_k: int,
    person_id: str | None = None,
    session_id: str | None = None,
    now_ts_ms: int | None = None,
) -> list[dict[str, Any]]:
    """Route 0: life_episode SSOT recall with evidence and correction state."""
    store = getattr(self, "life_memory_store", None)
    if store is None:
        raise RuntimeError("life episode store unavailable")
    packet = store.recall_episode_packet(
        query=query,
        top_k=top_k,
        person_id=person_id,
        session_id=session_id,
        include_suppressed=False,
        now_ts_ms_value=now_ts_ms,
    )
    return list(packet.candidates)


def search_life_memory_collection(
    self: Any,
    *,
    query: str,
    top_k: int,
    person_id: str | None = None,
    session_id: str | None = None,
    now_ts_ms: int | None = None,
) -> list[dict[str, Any]]:
    """Collection-level recall for repeated lived experiences."""
    store = getattr(self, "life_memory_store", None)
    if store is None:
        raise RuntimeError("life memory collection store unavailable")
    packet = store.recall_collection_packet(
        query=query,
        top_k=top_k,
        person_id=person_id,
        session_id=session_id,
        now_ts_ms_value=now_ts_ms,
    )
    return list(packet.candidates)


def search_life_memory_entity(
    self: Any,
    *,
    query: str,
    top_k: int,
    person_id: str | None = None,
    session_id: str | None = None,
    now_ts_ms: int | None = None,
) -> list[dict[str, Any]]:
    """Entity-level recall for repeated items inside lived collections."""
    store = getattr(self, "life_memory_store", None)
    if store is None:
        raise RuntimeError("life memory entity store unavailable")
    packet = store.recall_entity_packet(
        query=query,
        top_k=top_k,
        person_id=person_id,
        session_id=session_id,
        now_ts_ms_value=now_ts_ms,
    )
    return list(packet.candidates)


def search_sqlite_ledger(
    self: Any,
    *,
    query: str,
    top_k: int,
    person_id: str | None = None,
    session_id: str | None = None,
    now_ts_ms: int | None = None,
) -> list[dict[str, Any]]:
    """Route 2：SQLite 意图日志 + 记忆操作日志 + 后台调度事件检索。"""
    from host_brain.core.ids import now_ms
    from host_brain.retrieval.recall_fusion_utils import (
        _apply_sqlite_ledger_boost,
        _coerce_non_negative_int,
        _extract_person_id_from_refs,
        _normalize_recall_refs,
        _extract_session_id_from_refs,
        _sqlite_candidate_sort_key,
        _text_similarity_score,
        _truncate_text_for_recall,
    )

    if self.ledger is None:
        raise RuntimeError("sqlite ledger unavailable")
    current_ts = int(now_ts_ms if now_ts_ms is not None else now_ms())
    normalized_person_id = str(person_id or "").strip()
    normalized_session_id = str(session_id or "").strip()
    scored: list[tuple[float, dict[str, Any]]] = []

    for row in list(self.ledger.intent_log):
        if not isinstance(row, Mapping):
            continue
        raw_text = str(row.get("raw_text") or "").strip()
        normalized = row.get("normalized_json")
        normalized_text = (
            json.dumps(normalized, ensure_ascii=False, sort_keys=True)
            if isinstance(normalized, Mapping)
            else str(normalized or "")
        )
        text = f"{raw_text}\n{normalized_text}".strip()
        score = _text_similarity_score(query, text)
        if score <= 0.0:
            continue
        row_session_id = str(row.get("session_id") or "").strip() or None
        row_ts = _coerce_non_negative_int(row.get("ts_ms"), default=0)
        boosted_score, boost_meta = _apply_sqlite_ledger_boost(
            base_score=score,
            row_ts_ms=row_ts,
            now_ts_ms=current_ts,
            row_person_id=(str(normalized.get("person_id") or "").strip() if isinstance(normalized, Mapping) else None),
            target_person_id=normalized_person_id,
            row_session_id=row_session_id,
            target_session_id=normalized_session_id,
        )
        request_id = str(row.get("request_id") or "").strip() or None
        intent_id = str(row.get("intent_id") or "").strip() or None
        memory_id = str(intent_id or request_id or f"ledger_{len(scored)}")
        evidence_refs = _normalize_recall_refs(
            row.get("evidence_refs"),
            fallback=(f"intent://{intent_id}" if intent_id else None),
        )
        scored.append((
            boosted_score,
            {
                "memory_id": memory_id,
                "text": _truncate_text_for_recall(text, limit=512),
                "score": boosted_score,
                "metadata": {
                    "store_id": STORE_SQLITE_LEDGER,
                    "request_id": request_id,
                    "intent_id": intent_id,
                    "person_id": (str(normalized.get("person_id") or "").strip() if isinstance(normalized, Mapping) else None),
                    "session_id": row_session_id,
                    "event_id": (str(row.get("event_id") or "").strip() or None),
                    "base_score": score,
                    "score_boost": boost_meta,
                    "evidence_refs": evidence_refs,
                    "source_ref": f"ledger://intent_log/{memory_id}",
                    "evidence_refs_primary": evidence_refs[0] if evidence_refs else f"ledger://intent/{memory_id}",
                },
            },
        ))

    for row in list(self.ledger.memory_ops_log):
        if not isinstance(row, Mapping):
            continue
        op_kind = str(row.get("op_kind") or "").strip().upper()
        if op_kind != "WRITE":
            continue
        status = str(row.get("status") or "").strip().upper()
        if status and status != "SUCCESS":
            continue
        memory_id = str(row.get("memory_id") or "").strip() or f"memop_{len(scored)}"
        request_id = str(row.get("request_id") or "").strip() or None
        intent_id = str(row.get("intent_id") or "").strip() or None
        detail = str(row.get("detail") or "").strip()
        evidence_refs = _normalize_recall_refs(
            row.get("evidence_refs"),
            fallback=(f"memory://episodic/{memory_id}" if memory_id else None),
        )
        row_person_id = _extract_person_id_from_refs(evidence_refs)
        row_session_id = _extract_session_id_from_refs(evidence_refs)
        row_ts = _coerce_non_negative_int(row.get("ts_ms"), default=0)

        # FIX: 补充语义更丰富的字段，改善文本相似度搜索质量。
        # 原实现只拼接 detail+memory_id+evidence_refs，三者均无实质语义。
        # 现在额外加入：intent_id（意图标识）、target_memory_id（目标记忆）、
        # source_memory_ids（来源记忆集合）、decay_profile_id（衰减策略）、
        # visibility（可见性标签）。
        target_memory_id = str(row.get("target_memory_id") or "").strip()
        source_memory_ids_raw = row.get("source_memory_ids")
        if isinstance(source_memory_ids_raw, (list, tuple)):
            source_ids_text = " ".join(str(s) for s in source_memory_ids_raw if s)
        else:
            source_ids_text = str(source_memory_ids_raw or "").strip()
        decay_profile_id = str(row.get("decay_profile_id") or "").strip()
        visibility = str(row.get("visibility") or "").strip()

        text = " ".join(
            part for part in (
                intent_id,
                detail,
                memory_id,
                target_memory_id,
                source_ids_text,
                decay_profile_id,
                visibility,
                " ".join(evidence_refs),
            )
            if part
        ).strip()

        score = _text_similarity_score(query, text)
        if score <= 0.0:
            continue
        boosted_score, boost_meta = _apply_sqlite_ledger_boost(
            base_score=score,
            row_ts_ms=row_ts,
            now_ts_ms=current_ts,
            row_person_id=row_person_id,
            target_person_id=normalized_person_id,
            row_session_id=row_session_id,
            target_session_id=normalized_session_id,
        )
        scored.append((
            boosted_score,
            {
                "memory_id": memory_id,
                "text": _truncate_text_for_recall(text, limit=512),
                "score": boosted_score,
                "metadata": {
                    "store_id": STORE_SQLITE_LEDGER,
                    "request_id": request_id,
                    "intent_id": intent_id,
                    "person_id": row_person_id,
                    "session_id": row_session_id,
                    "op_kind": op_kind,
                    "status": status or None,
                    "target_memory_id": target_memory_id or None,
                    "decay_profile_id": decay_profile_id or None,
                    "visibility": visibility or None,
                    "base_score": score,
                    "score_boost": boost_meta,
                    "evidence_refs": evidence_refs,
                    "source_ref": f"ledger://memory_ops_log/{memory_id}",
                    "evidence_refs_primary": (
                        evidence_refs[0] if evidence_refs else f"memory://episodic/{memory_id}"
                    ),
                },
            },
        ))

    for row in list(getattr(self.ledger, "scheduler_event_log", []) or []):
        if not isinstance(row, Mapping):
            continue
        event_id = str(row.get("event_id") or row.get("id") or "").strip()
        task_id = str(row.get("task_id") or "").strip()
        status = str(row.get("status") or "").strip().upper()
        reason_code = str(row.get("reason_code") or "").strip()
        mode_at_trigger = str(row.get("mode_at_trigger") or "").strip()
        error_detail = str(row.get("error_detail") or "").strip()
        if not _scheduler_event_is_recallable(
            task_id=task_id,
            status=status,
            reason_code=reason_code,
            detail=error_detail,
        ):
            continue
        intent_id = str(row.get("intent_id") or "").strip() or None
        row_ts = _coerce_non_negative_int(row.get("ts_ms"), default=0)
        trigger_ts = _coerce_non_negative_int(row.get("trigger_ts"), default=0)
        memory_id = event_id or f"scheduler_{len(scored)}"
        text = " ".join(
            part
            for part in (
                "后台任务",
                task_id,
                status,
                reason_code,
                mode_at_trigger,
                error_detail,
                intent_id or "",
            )
            if part
        ).strip()
        score = _text_similarity_score(query, text)
        if score <= 0.0:
            continue
        boosted_score, boost_meta = _apply_sqlite_ledger_boost(
            base_score=score,
            row_ts_ms=row_ts or trigger_ts,
            now_ts_ms=current_ts,
            row_person_id=None,
            target_person_id=normalized_person_id,
            row_session_id=None,
            target_session_id=normalized_session_id,
        )
        evidence_refs = [f"scheduler://event/{memory_id}"]
        if task_id:
            evidence_refs.append(f"scheduler://task/{task_id}")
        scored.append((
            boosted_score,
            {
                "memory_id": memory_id,
                "text": _truncate_text_for_recall(text, limit=512),
                "score": boosted_score,
                "metadata": {
                    "store_id": STORE_SQLITE_LEDGER,
                    "request_id": event_id or None,
                    "intent_id": intent_id,
                    "person_id": None,
                    "session_id": None,
                    "task_id": task_id or None,
                    "status": status or None,
                    "reason_code": reason_code or None,
                    "mode_at_trigger": mode_at_trigger or None,
                    "base_score": score,
                    "score_boost": boost_meta,
                    "evidence_refs": evidence_refs,
                    "source_ref": f"ledger://scheduler_event_log/{memory_id}",
                    "evidence_refs_primary": evidence_refs[0],
                },
            },
        ))
    deduped: list[dict[str, Any]] = []
    seen_memory_ids: set[str] = set()
    for _score, item in sorted(
        scored,
        key=lambda pair: _sqlite_candidate_sort_key(score=pair[0], candidate=pair[1]),
    ):
        memory_id = str(item.get("memory_id") or "").strip()
        if memory_id and memory_id in seen_memory_ids:
            continue
        if memory_id:
            seen_memory_ids.add(memory_id)
        deduped.append(item)
        if len(deduped) >= max(1, int(top_k)):
            break
    return deduped


def search_semantic_memory(
    self: Any,
    *,
    query: str,
    top_k: int,
    person_id: str | None = None,
    session_id: str | None = None,
    now_ts_ms: int | None = None,
) -> list[dict[str, Any]]:
    """Route 3：SQLite semantic_memory 长期语义记忆检索。"""
    from host_brain.retrieval.recall_fusion_utils import (
        _coerce_non_negative_int,
        _coerce_score,
        _text_similarity_score,
        _truncate_text_for_recall,
    )

    if self.ledger is None or not hasattr(self.ledger, "list_semantic_memories"):
        raise RuntimeError("semantic memory ledger unavailable")
    try:
        rows, _total = self.ledger.list_semantic_memories(limit=max(1, int(top_k)) * 8, offset=0)
    except Exception as exc:
        raise RuntimeError(f"semantic memory ledger unavailable: {exc}") from exc

    semantic_rows = list(rows or [])
    lineage_source_ids: list[str] = []
    for semantic_row in semantic_rows:
        if not isinstance(semantic_row, Mapping):
            continue
        for source_id in _semantic_source_memory_ids(semantic_row.get("source_memory_ids")):
            if source_id not in lineage_source_ids:
                lineage_source_ids.append(source_id)
    try:
        lineage_repository: LifeMemoryRepository | None = LifeMemoryRepository.from_ledger(self.ledger)
    except Exception:
        logger.debug("semantic lineage repository unavailable", exc_info=True)
        lineage_repository = None
    lineage = _load_semantic_lineage_batch(
        lineage_repository,
        source_memory_ids=lineage_source_ids,
    )

    current_ts = int(now_ts_ms or 0)
    target_person = str(person_id or "").strip()
    target_session = str(session_id or "").strip()
    query_profile = _semantic_query_profile(query)
    scored: list[tuple[float, dict[str, Any]]] = []
    for row in semantic_rows:
        if not isinstance(row, Mapping):
            continue
        semantic_id = str(row.get("semantic_id") or "").strip()
        summary = str(row.get("summary") or "").strip()
        if not semantic_id or not summary:
            continue
        tags_raw = row.get("tags")
        if isinstance(tags_raw, (list, tuple)):
            tags = [str(tag).strip() for tag in tags_raw if str(tag).strip()]
        else:
            tags = [str(tags_raw).strip()] if str(tags_raw or "").strip() else []
        text_for_score = " ".join([summary, *tags])
        lexical_score = _text_similarity_score(query, text_for_score)
        semantic_kind = _semantic_kind_from_tags(tags)
        matched_tags = _semantic_matched_tags(query=query, tags=tags)
        if lexical_score <= 0.0 and not matched_tags:
            continue
        confidence = _coerce_score(row.get("confidence"))
        score = lexical_score
        if confidence > 0.0:
            score = round(min(1.0, score * 0.82 + confidence * 0.18), 6)
        profile_boost = _semantic_profile_boost(
            query=query,
            query_profile=query_profile,
            semantic_kind=semantic_kind,
            summary=summary,
            tags=tags,
        )
        if profile_boost > 0.0 and semantic_kind:
            kind_tag = f"semantic_kind:{semantic_kind}"
            if kind_tag in tags and kind_tag not in matched_tags:
                matched_tags.append(kind_tag)
        tag_match_boost = min(0.15, len(matched_tags) * 0.035)
        row_ts = _coerce_non_negative_int(row.get("ts_ms"), default=0)
        recency_boost = 0.0
        if current_ts > 0 and row_ts > 0:
            age_ms = max(0, current_ts - row_ts)
            if age_ms <= 30 * 24 * 60 * 60 * 1000:
                recency_boost = 0.04
        final_score = round(max(0.0, min(1.0, score + recency_boost + profile_boost + tag_match_boost)), 6)
        source_memory_ids = _semantic_source_memory_ids(row.get("source_memory_ids"))
        provenance = _semantic_memory_source_provenance(
            lineage,
            source_memory_ids=source_memory_ids,
            semantic_id=semantic_id,
        )
        provenance_person_ids = [str(item or "").strip() for item in list(provenance.get("person_ids") or []) if str(item or "").strip()]
        provenance_session_ids = [str(item or "").strip() for item in list(provenance.get("session_ids") or []) if str(item or "").strip()]
        identity_boost = 0.0
        identity_penalty = 0.0
        if target_session and target_session in provenance_session_ids:
            identity_boost += 0.18
        if target_person and target_person in provenance_person_ids:
            identity_boost += 0.36
        elif target_person and provenance_person_ids:
            identity_penalty += 0.22
        final_score = round(max(0.0, min(1.0, final_score + identity_boost - identity_penalty)), 6)
        evidence_refs = provenance.get("evidence_refs") or [f"semantic://{semantic_id}"]
        scored.append(
            (
                final_score,
                {
                    "memory_id": semantic_id,
                    "text": _truncate_text_for_recall(summary, limit=512),
                    "score": final_score,
                    "metadata": {
                        "store_id": STORE_SEMANTIC_MEMORY,
                        "memory_layer": "semantic_long_term",
                        "semantic_id": semantic_id,
                        "distill_strategy": str(row.get("distill_strategy") or "merge_top_k"),
                        "confidence": confidence,
                        "tags": tags,
                        "semantic_kind": semantic_kind,
                        "source_memory_ids": source_memory_ids,
                        "semantic_source_refs": provenance.get("source_refs") or [],
                        "semantic_evidence_refs": provenance.get("evidence_refs") or [],
                        "semantic_episode_ids": provenance.get("episode_ids") or [],
                        "semantic_person_ids": provenance_person_ids,
                        "semantic_session_ids": provenance_session_ids,
                        "semantic_collection_ids": provenance.get("collection_ids") or [],
                        "semantic_entity_ids": provenance.get("entity_ids") or [],
                        "semantic_correction_ids": provenance.get("correction_ids") or [],
                        "semantic_provenance_status": provenance.get("status") or "unresolved",
                        "ts_ms": row_ts,
                        "score_boost": {
                            "query_profile": query_profile,
                            "lexical_score": lexical_score,
                            "confidence_blend_score": score,
                            "recency_boost": recency_boost,
                            "identity_boost": round(identity_boost, 6),
                            "identity_penalty": round(identity_penalty, 6),
                            "semantic_kind_boost": round(profile_boost, 6),
                            "preference_boost": round(profile_boost if query_profile == "preference" else 0.0, 6),
                            "tag_match_boost": round(tag_match_boost, 6),
                            "matched_tags": matched_tags,
                            "semantic_kind": semantic_kind,
                            "row_ts_ms": row_ts,
                            "now_ts_ms": current_ts,
                        },
                        "source_ref": f"ledger://semantic_memory/{semantic_id}",
                        "evidence_refs": evidence_refs,
                        "evidence_refs_primary": evidence_refs[0],
                    },
                },
            )
        )
    return [
        item
        for _score, item in sorted(scored, key=lambda pair: (-pair[0], -int(pair[1]["metadata"].get("ts_ms") or 0), str(pair[1].get("memory_id") or "")))[: max(1, int(top_k))]
    ]


def _semantic_source_memory_ids(raw_value: Any) -> list[str]:
    if isinstance(raw_value, (list, tuple)):
        raw_items = raw_value
    elif isinstance(raw_value, str):
        stripped = raw_value.strip()
        if not stripped:
            raw_items = []
        else:
            try:
                parsed = json.loads(stripped)
            except (TypeError, ValueError):
                parsed = [stripped]
            raw_items = parsed if isinstance(parsed, (list, tuple)) else [parsed]
    else:
        raw_items = []
    ids: list[str] = []
    for item in raw_items:
        cleaned = str(item or "").strip()
        if cleaned and cleaned not in ids:
            ids.append(cleaned)
    return ids


def _semantic_lineage_source_groups(source_memory_ids: list[str]) -> dict[str, list[str]]:
    groups = {
        "episode_ids": [],
        "collection_ids": [],
        "entity_ids": [],
        "correction_ids": [],
    }
    prefixes = (
        ("life_episode::", "episode_ids"),
        ("life_memory_collection::", "collection_ids"),
        ("life_memory_entity::", "entity_ids"),
        ("life_memory_correction::", "correction_ids"),
    )
    for source_id in source_memory_ids:
        cleaned_source = str(source_id or "").strip()
        for prefix, group_key in prefixes:
            if not cleaned_source.startswith(prefix):
                continue
            record_id = cleaned_source[len(prefix) :].strip()
            if record_id and record_id not in groups[group_key]:
                groups[group_key].append(record_id)
            break
    return groups


def _load_semantic_lineage_batch(
    repository: LifeMemoryRepository | None,
    *,
    source_memory_ids: list[str],
) -> SemanticLineageBatch:
    if repository is None:
        return SemanticLineageBatch()
    groups = _semantic_lineage_source_groups(source_memory_ids)
    try:
        return repository.load_semantic_lineage_batch(**groups)
    except Exception:
        logger.debug("semantic lineage batch unavailable", exc_info=True)
        return SemanticLineageBatch()


def _semantic_memory_source_provenance(
    lineage: SemanticLineageBatch,
    *,
    source_memory_ids: list[str],
    semantic_id: str,
) -> dict[str, Any]:
    """Resolve distilled semantic memories from a typed lineage snapshot."""
    del semantic_id  # Retained for the established helper API and diagnostics callers.
    source_refs: list[str] = []
    evidence_refs: list[str] = []
    episode_ids: list[str] = []
    person_ids: list[str] = []
    session_ids: list[str] = []
    collection_ids: list[str] = []
    entity_ids: list[str] = []
    correction_ids: list[str] = []

    def add(target: list[str], value: Any) -> None:
        cleaned = str(value or "").strip()
        if cleaned and cleaned not in target:
            target.append(cleaned)

    for source_id in source_memory_ids:
        cleaned_source = str(source_id or "").strip()
        if not cleaned_source:
            continue
        if cleaned_source.startswith("life_episode::"):
            episode_id = cleaned_source.split("::", 1)[1].strip()
            if episode_id:
                add(episode_ids, episode_id)
                add(source_refs, f"life_episode::{episode_id}")
                _semantic_add_episode_provenance(
                    lineage,
                    episode_id=episode_id,
                    add=add,
                    source_refs=source_refs,
                    evidence_refs=evidence_refs,
                )
            continue
        if cleaned_source.startswith("life_memory_collection::"):
            collection_id = cleaned_source.split("::", 1)[1].strip()
            if collection_id:
                add(collection_ids, collection_id)
                add(source_refs, f"life_memory_collection::{collection_id}")
                _semantic_add_collection_provenance(
                    lineage,
                    collection_id=collection_id,
                    add=add,
                    source_refs=source_refs,
                    evidence_refs=evidence_refs,
                    episode_ids=episode_ids,
                )
            continue
        if cleaned_source.startswith("life_memory_entity::"):
            entity_id = cleaned_source.split("::", 1)[1].strip()
            if entity_id:
                add(entity_ids, entity_id)
                add(source_refs, f"life_memory_entity::{entity_id}")
                _semantic_add_entity_provenance(
                    lineage,
                    entity_id=entity_id,
                    add=add,
                    source_refs=source_refs,
                    evidence_refs=evidence_refs,
                    episode_ids=episode_ids,
                    collection_ids=collection_ids,
                )
            continue
        if cleaned_source.startswith("life_memory_correction::"):
            correction_id = cleaned_source.split("::", 1)[1].strip()
            if correction_id:
                add(correction_ids, correction_id)
                add(source_refs, f"life_memory_correction::{correction_id}")
                _semantic_add_correction_provenance(
                    lineage,
                    correction_id=correction_id,
                    add=add,
                    source_refs=source_refs,
                    evidence_refs=evidence_refs,
                    episode_ids=episode_ids,
                )
            continue
        if "://" in cleaned_source:
            add(source_refs, cleaned_source)
            add(evidence_refs, cleaned_source)

    _semantic_add_episode_identities(
        lineage,
        episode_ids=episode_ids,
        add=add,
        person_ids=person_ids,
        session_ids=session_ids,
    )
    return {
        "status": "resolved" if evidence_refs else "unresolved",
        "source_refs": source_refs,
        "evidence_refs": evidence_refs,
        "episode_ids": episode_ids,
        "person_ids": person_ids,
        "session_ids": session_ids,
        "collection_ids": collection_ids,
        "entity_ids": entity_ids,
        "correction_ids": correction_ids,
    }


def _semantic_add_episode_identities(
    lineage: SemanticLineageBatch,
    *,
    episode_ids: list[str],
    add: Any,
    person_ids: list[str],
    session_ids: list[str],
) -> None:
    for episode_id in episode_ids:
        episode = lineage.episodes_by_id.get(episode_id)
        if episode is None:
            continue
        add(person_ids, episode.get("person_id"))
        add(session_ids, episode.get("session_id"))


def _semantic_add_episode_provenance(
    lineage: SemanticLineageBatch,
    *,
    episode_id: str,
    add: Any,
    source_refs: list[str],
    evidence_refs: list[str],
) -> None:
    episode = lineage.episodes_by_id.get(episode_id)
    if episode is not None:
        add(source_refs, episode.get("primary_ref"))
    for evidence in lineage.evidence_by_episode_id.get(episode_id, ()):
        source_ref = str(evidence.get("source_ref") or "").strip()
        evidence_id = str(evidence.get("evidence_id") or "").strip()
        add(evidence_refs, source_ref or (f"life_evidence://{evidence_id}" if evidence_id else None))


def _semantic_add_collection_provenance(
    lineage: SemanticLineageBatch,
    *,
    collection_id: str,
    add: Any,
    source_refs: list[str],
    evidence_refs: list[str],
    episode_ids: list[str],
) -> None:
    for item in lineage.collection_items_by_collection_id.get(collection_id, ()):
        episode_id = str(item.get("episode_id") or "").strip()
        if episode_id:
            add(episode_ids, episode_id)
            add(source_refs, f"life_episode::{episode_id}")
        add(source_refs, item.get("source_ref"))
        evidence_ref = item.get("evidence_ref") or item.get("source_ref")
        add(evidence_refs, evidence_ref)
        if episode_id and not str(evidence_ref or "").strip():
            _semantic_add_episode_provenance(
                lineage,
                episode_id=episode_id,
                add=add,
                source_refs=source_refs,
                evidence_refs=evidence_refs,
            )


def _semantic_add_entity_provenance(
    lineage: SemanticLineageBatch,
    *,
    entity_id: str,
    add: Any,
    source_refs: list[str],
    evidence_refs: list[str],
    episode_ids: list[str],
    collection_ids: list[str],
) -> None:
    for mention in lineage.entity_mentions_by_entity_id.get(entity_id, ()):
        episode_id = str(mention.get("episode_id") or "").strip()
        collection_id = str(mention.get("collection_id") or "").strip()
        if episode_id:
            add(episode_ids, episode_id)
            add(source_refs, f"life_episode::{episode_id}")
        if collection_id:
            add(collection_ids, collection_id)
            add(source_refs, f"life_memory_collection::{collection_id}")
        add(source_refs, mention.get("source_ref"))
        add(source_refs, mention.get("item_source_ref"))
        evidence_ref = (
            mention.get("evidence_ref")
            or mention.get("item_evidence_ref")
            or mention.get("source_ref")
            or mention.get("item_source_ref")
        )
        add(evidence_refs, evidence_ref)
        if episode_id and not str(evidence_ref or "").strip():
            _semantic_add_episode_provenance(
                lineage,
                episode_id=episode_id,
                add=add,
                source_refs=source_refs,
                evidence_refs=evidence_refs,
            )


def _semantic_add_correction_provenance(
    lineage: SemanticLineageBatch,
    *,
    correction_id: str,
    add: Any,
    source_refs: list[str],
    evidence_refs: list[str],
    episode_ids: list[str],
) -> None:
    correction = lineage.corrections_by_id.get(correction_id)
    if correction is None:
        return
    resolved_correction_id = correction.get("correction_id") or correction_id
    add(evidence_refs, f"life_memory_correction://{resolved_correction_id}")
    for episode_id in (correction.get("correction_episode_id"), correction.get("episode_id")):
        cleaned_episode_id = str(episode_id or "").strip()
        if not cleaned_episode_id:
            continue
        add(episode_ids, cleaned_episode_id)
        add(source_refs, f"life_episode::{cleaned_episode_id}")
        _semantic_add_episode_provenance(
            lineage,
            episode_id=cleaned_episode_id,
            add=add,
            source_refs=source_refs,
            evidence_refs=evidence_refs,
        )


def _semantic_query_profile(query: str) -> str:
    text = str(query or "").strip().lower()
    compact = re.sub(r"\s+", "", text)
    if not compact:
        return "general"
    if _looks_like_llm_routing_preference_query(compact):
        return "llm_routing_preference"
    if _looks_like_preference_semantic_query(query):
        return "preference"
    if any(token in compact for token in ("失败", "故障", "报错", "异常", "卡住", "卡死", "连不上", "没起来", "超时", "timeout", "deepseek", "gateway", "chroma", "neo4j", "状态库")):
        return "failure_debug"
    if any(token in compact for token in ("记忆系统", "模型路由", "主链迁移", "能力", "删除资源管理器", "自我改变", "改了什么", "改成什么", "策略")):
        return "self_change"
    if any(token in compact for token in ("不像人话", "正常聊天", "模板腔", "台词腔", "胡说八道", "很呆", "文本质量", "体验不好", "白费功夫")):
        return "relationship_feedback"
    if any(token in compact for token in ("回去翻", "翻看", "深读", "正文", "详情页", "网页", "epub", "怎么判断", "判断哪本", "哪本好看")):
        if any(token in compact for token in ("轻小说", "推荐", "哪本", "书", "正文", "详情", "网页", "epub")):
            return "action_pattern"
    if any(token in compact for token in ("测试代号", "海边灯塔", "prewarm", "预热指标", "app-server", "appserver")):
        return "stable_fact"
    if "未完成" not in compact and any(token in compact for token in ("完成了什么", "做完了什么", "关闭了", "已完成", "完成的待办", "closed", "done", "resolved")):
        return "commitment_closed"
    if any(token in compact for token in ("承诺", "待办", "openloop", "open_loop", "未完成", "还要做", "后续要", "答应", "说好")):
        return "commitment"
    if any(token in compact for token in ("推荐", "轻小说", "哪几本", "哪些书", "最近", "这几天", "上周", "昨天", "前天", "题材", "恋爱日常", "奇幻", "皇女", "皇帝")):
        return "experience_collection"
    if any(token in compact for token in ("onebot", "edge", "qq会话", "不在本机")):
        return "stable_fact"
    return "general"


def _semantic_kind_from_tags(tags: list[str]) -> str:
    for tag in tags:
        value = str(tag or "").strip()
        if value.startswith("semantic_kind:"):
            return value.split(":", 1)[1].strip()
    lowered = " ".join(str(tag or "").lower() for tag in tags)
    if "user_preference" in lowered or "preference" in lowered:
        return "user_preference"
    if "failure_lesson" in lowered:
        return "failure_lesson"
    if "self_change" in lowered:
        return "self_change"
    if "commitment" in lowered or "open_loop" in lowered:
        return "commitment"
    if "experience_collection" in lowered or "recommendation_collection" in lowered:
        return "experience_collection"
    if "action_pattern" in lowered or "proactive_action" in lowered:
        return "action_pattern"
    if "stable_fact" in lowered:
        return "stable_fact"
    if "correction" in lowered:
        return "correction"
    if "relationship_feedback" in lowered or "response_quality" in lowered:
        return "relationship_feedback"
    return ""


def _semantic_profile_boost(*, query: str, query_profile: str, semantic_kind: str, summary: str, tags: list[str]) -> float:
    profile = str(query_profile or "general")
    kind = str(semantic_kind or "")
    if profile == "preference":
        if kind == "experience_collection" or _looks_like_collection_semantic_row(summary=summary, tags=tags):
            return -0.45
        if kind == "user_preference" or _looks_like_preference_semantic_row(summary=summary, tags=tags):
            return 0.26
    if profile == "llm_routing_preference":
        if kind == "experience_collection" or _looks_like_collection_semantic_row(summary=summary, tags=tags):
            return -0.45
        if kind == "user_preference" and _looks_like_llm_routing_semantic_row(summary=summary, tags=tags):
            return 0.34
        if kind == "user_preference":
            return -0.18
    if profile == "failure_debug":
        if kind == "failure_lesson":
            return 0.18
        if kind == "experience_collection":
            return -0.08
    if profile == "self_change":
        if kind == "self_change":
            return 0.20
        if kind in {"experience_collection", "user_preference"}:
            return -0.06
    if profile in {"commitment", "commitment_closed"}:
        if kind == "commitment":
            status = _open_loop_status_from_tags(tags)
            if profile == "commitment_closed":
                return 0.22 if status in {"closed", "cancelled", "superseded"} else 0.06
            if status in {"closed", "cancelled", "superseded"}:
                return -0.28
            return 0.28
        if kind == "experience_collection":
            return -0.06
    if profile == "experience_collection":
        if kind == "experience_collection":
            return 0.12
        if kind == "user_preference":
            return -0.04
    if profile == "action_pattern":
        if kind == "action_pattern":
            return 0.24
        if kind == "experience_collection":
            return 0.04
        if kind in {"user_preference", "relationship_feedback"}:
            return -0.08
    if profile == "stable_fact":
        if kind == "stable_fact":
            return 0.24
        if kind == "correction":
            return 0.28
        if kind == "user_preference" and _looks_like_preference_semantic_row(summary=summary, tags=tags):
            return 0.08
        if kind == "experience_collection":
            return -0.18
    if profile == "relationship_feedback":
        if kind == "relationship_feedback":
            return 0.20 + _relationship_feedback_subtopic_boost(query=query, summary=summary, tags=tags)
        if kind == "experience_collection":
            return -0.08
    return 0.0


def _relationship_feedback_subtopic_boost(*, query: str, summary: str, tags: list[str]) -> float:
    compact_query = re.sub(r"\s+", "", str(query or "")).lower()
    compact_row = re.sub(r"\s+", "", " ".join([str(summary or ""), *[str(tag or "") for tag in tags]])).lower()
    if not compact_query or not compact_row:
        return 0.0
    natural_markers = ("不像人话", "正常聊天", "模板腔", "台词腔", "文本质量", "很呆", "太呆")
    recall_quality_markers = ("胡说八道", "编造", "召回", "证据", "没核实", "核实", "事实")
    runtime_markers = ("白费功夫", "服务没起来", "没起来", "连不上")
    subtopics = (
        (natural_markers, 0.16),
        (recall_quality_markers, 0.12),
        (runtime_markers, 0.12),
    )
    for markers, positive_boost in subtopics:
        if any(marker in compact_query for marker in markers):
            if any(marker in compact_row for marker in markers):
                return positive_boost
            return -0.10
    return 0.0


def _semantic_matched_tags(*, query: str, tags: list[str]) -> list[str]:
    compact_query = re.sub(r"\s+", "", str(query or "")).lower()
    if not compact_query:
        return []
    matched: list[str] = []
    for tag in tags:
        value = str(tag or "").strip()
        if not value:
            continue
        tag_body = value.split(":", 1)[1] if ":" in value else value
        compact_tag = re.sub(r"[\s_]+", "", tag_body).lower()
        if len(compact_tag) >= 2 and compact_tag in compact_query:
            matched.append(value)
    return list(dict.fromkeys(matched))


def _open_loop_status_from_tags(tags: list[str]) -> str:
    lowered = {str(tag or "").strip().lower() for tag in list(tags or [])}
    if "open_loop:cancelled" in lowered or "open_loop:canceled" in lowered:
        return "cancelled"
    if "open_loop:superseded" in lowered:
        return "superseded"
    if "open_loop:closed" in lowered:
        return "closed"
    if "open_loop:open" in lowered or "open_loop" in lowered or "commitment" in lowered:
        return "open"
    return ""


def _looks_like_preference_semantic_query(query: str) -> bool:
    text = str(query or "").strip()
    if not text:
        return False
    compact = re.sub(r"\s+", "", text).lower()
    if _looks_like_llm_routing_preference_query(compact):
        return True
    return any(token in text for token in ("偏好", "喜欢", "不喜欢", "以后", "不要", "别再", "必须", "记得", "我说", "要求")) and any(
        token in text for token in ("格式", "怎么发", "怎样发", "换行", "模板腔", "分段", "卡片", "推荐", "发送", "回复")
    )


def _looks_like_llm_routing_preference_query(compact_query: str) -> bool:
    value = str(compact_query or "").lower()
    return any(token in value for token in ("thalamus", "llm", "模型池", "主链", "deepseek", "gemini", "codex", "pro")) and any(
        token in value for token in ("应该", "用什么", "用哪个", "不要用", "别用", "不用", "优先", "模型")
    )


def _looks_like_preference_semantic_row(*, summary: str, tags: list[str]) -> bool:
    text = " ".join([str(summary or ""), *[str(tag or "") for tag in tags]])
    return any(token in text for token in ("用户偏好", "user_preference", "preference", "不要", "别再", "必须", "以后"))


def _looks_like_llm_routing_semantic_row(*, summary: str, tags: list[str]) -> bool:
    summary_text = str(summary or "").lower()
    tag_text = " ".join(str(tag or "") for tag in tags).lower()
    combined = f"{summary_text} {tag_text}"
    if any(token in combined for token in ("thalamus", "llm", "模型池", "主链", "deepseek", "gemini", "codex")):
        return True
    return bool(re.search(r"(^|[^a-z0-9])pro([^a-z0-9]|$)", summary_text))


def _looks_like_collection_semantic_row(*, summary: str, tags: list[str]) -> bool:
    text = " ".join([str(summary or ""), *[str(tag or "") for tag in tags]])
    return any(token in text for token in ("经历集合", "experience_collection", "recommendation_collection"))


def search_neo4j_bond(self: Any, *, query: str, top_k: int) -> list[dict[str, Any]]:
    """Route 3：Neo4j 关系图检索，降级到 SQLite bond 日志。"""
    from host_brain.retrieval.recall_fusion_utils import (
        _normalize_recall_refs,
        _text_similarity_score,
        _truncate_text_for_recall,
    )

    records: list[Mapping[str, Any]] = []
    try:
        events_attr = getattr(self.neo4j, "events", None)
        if isinstance(events_attr, list):
            records = [row for row in events_attr if isinstance(row, Mapping)]
    except Exception as exc:
        logger.warning("neo4j recall route unavailable, fallback to sqlite bond log: %s", exc)
        records = []
    if not records and self.ledger is not None:
        records = [row for row in self.ledger.bond_update_log if isinstance(row, Mapping)]
    if not records:
        return []
    scored: list[tuple[float, dict[str, Any]]] = []
    for row in records:
        payload = row.get("payload")
        payload_text = (
            json.dumps(payload, ensure_ascii=False, sort_keys=True)
            if isinstance(payload, Mapping)
            else str(payload or "")
        )
        text = " ".join(
            part for part in (
                str(row.get("topic_id") or "").strip(),
                str(row.get("capability_hint") or "").strip(),
                payload_text,
            )
            if part
        )
        score = _text_similarity_score(query, text)
        if score <= 0.0:
            continue
        event_id = str(row.get("event_id") or "").strip() or f"bond_event_{len(scored)}"
        refs = _normalize_recall_refs(
            row.get("evidence_refs"),
            fallback=f"evd://bond/{event_id}",
        )
        scored.append((
            score,
            {
                "memory_id": event_id,
                "text": _truncate_text_for_recall(text, limit=512),
                "score": score,
                "metadata": {
                    "store_id": STORE_NEO4J_BOND,
                    "event_id": event_id,
                    "topic_id": str(row.get("topic_id") or "").strip() or None,
                    "capability_hint": str(row.get("capability_hint") or "").strip() or None,
                    "evidence_refs": refs,
                    "source_ref": f"neo4j://bond/event/{event_id}",
                    "evidence_refs_primary": refs[0] if refs else f"evd://bond/{event_id}",
                },
            },
        ))
    return [item for _score, item in sorted(scored, key=lambda pair: -pair[0])[: max(1, int(top_k))]]


def search_knowledge_graph(
    self: Any,
    *,
    query: str,
    top_k: int,
    person_id: str | None,
) -> list[dict[str, Any]]:
    """知识图谱路由：失败时降级为空候选，绝不影响主聊天链。"""
    retriever = getattr(self, "knowledge_graph_retriever", None)
    if retriever is None or not str(person_id or "").strip():
        return []
    terms = [item for item in re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z0-9_-]{2,}", str(query or "")) if item]
    hints = ["DISLIKES"] if any(token in str(query or "") for token in ("不喜欢", "讨厌", "不要")) else ["LIKES"]
    try:
        return list(retriever.search(
            person_id=str(person_id), query_entities=terms[:8],
            relation_hints=hints, max_hops=2, limit=max(1, int(top_k)),
        ))
    except Exception as exc:
        logger.warning("知识图谱检索不可用，已降级：%s", exc)
        return []


def search_world_canon(
    self: Any,
    *,
    query: str,
    query_embedding: list[float],
    top_k: int,
    collection: str | None,
) -> list[dict[str, Any]]:
    """Route 5：世界观典籍检索（向量投影优先，文件降级）。"""
    try:
        vector_rows = search_canon_projection_vector(
            self,
            query_embedding=query_embedding,
            top_k=top_k,
            collection=collection,
            canon_kind="world",
            store_id=STORE_WORLD_CANON,
        )
        if vector_rows:
            return vector_rows
    except Exception:
        pass
    return search_world_canon_file(self, query=query, top_k=top_k)


def search_world_canon_file(self: Any, *, query: str, top_k: int) -> list[dict[str, Any]]:
    """世界观典籍文件降级检索。"""
    from host_brain.retrieval.recall_fusion_utils import (
        _text_similarity_score,
        _truncate_text_for_recall,
    )

    if self.world_store is None:
        return []
    docs = getattr(self.world_store, "list_documents", lambda: [])()
    scored: list[tuple[float, dict[str, Any]]] = []
    for doc in docs or []:
        doc_id = str(getattr(doc, "doc_id", "") or "").strip()
        title = str(getattr(doc, "title", "") or "").strip()
        body = str(getattr(doc, "body", "") or "").strip()
        tags = list(getattr(doc, "tags", []) or [])
        text = " ".join(part for part in [title, body, *[str(tag) for tag in tags]] if part)
        score = _text_similarity_score(query, text)
        if score <= 0.0:
            continue
        version = int(getattr(doc, "version", 0) or 0)
        doc_hash = str(getattr(doc, "hash", "") or "").strip() or "unknown"
        source_ref = f"world_canon://{doc_id}@v{version}"
        scored.append((
            score,
            {
                "memory_id": doc_id,
                "text": _truncate_text_for_recall(body or title, limit=512),
                "score": score,
                "metadata": {
                    "store_id": STORE_WORLD_CANON,
                    "doc_id": doc_id,
                    "version": version,
                    "hash": doc_hash,
                    "evidence_refs": [source_ref],
                    "source_ref": source_ref,
                    "evidence_refs_primary": source_ref,
                },
            },
        ))
    return [item for _score, item in sorted(scored, key=lambda pair: -pair[0])[: max(1, int(top_k))]]


def search_canon_projection_vector(
    self: Any,
    *,
    query_embedding: list[float],
    top_k: int,
    collection: str | None,
    canon_kind: str,
    store_id: str,
) -> list[dict[str, Any]]:
    """世界观 canon 向量投影检索。"""
    from host_brain.retrieval.recall_fusion_utils import (
        _coerce_non_negative_int,
        _coerce_score,
        _extract_canon_entity_id,
        _truncate_text_for_recall,
    )

    target_collection = str(collection or DEFAULT_CANON_VECTOR_COLLECTION).strip()
    if not target_collection:
        target_collection = DEFAULT_CANON_VECTOR_COLLECTION
    fetch_k = max(1, int(top_k)) * CANON_VECTOR_FETCH_FACTOR
    rows = self.chroma.search(
        query_embedding=query_embedding,
        top_k=fetch_k,
        collection=target_collection,
    )

    candidates: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        metadata = row.get("metadata")
        normalized_metadata = dict(metadata or {}) if isinstance(metadata, Mapping) else {}
        row_kind = str(normalized_metadata.get("canon_kind") or "").strip().lower()
        if row_kind and row_kind != canon_kind:
            continue
        memory_id = str(row.get("memory_id") or "").strip()
        if not memory_id:
            continue
        score = _coerce_score(row.get("score"))
        if score <= 0.0:
            continue
        version = _coerce_non_negative_int(normalized_metadata.get("version"), default=0)
        content_hash = str(normalized_metadata.get("hash") or "").strip() or "unknown"
        snippet = _truncate_text_for_recall(str(row.get("text") or ""), limit=512)

        doc_id = str(normalized_metadata.get("doc_id") or "").strip()
        if not doc_id:
            doc_id = _extract_canon_entity_id(memory_id, "canon_world") or memory_id
        source_ref = str(normalized_metadata.get("source_ref") or f"world_canon://{doc_id}@v{version}")
        evidence_primary = str(
            normalized_metadata.get("evidence_ref")
            or normalized_metadata.get("evidence_refs_primary")
            or source_ref
        )
        payload: dict[str, Any] = {
            "memory_id": doc_id,
            "text": snippet,
            "score": score,
            "metadata": {
                "store_id": store_id,
                "canon_kind": canon_kind,
                "doc_id": doc_id,
                "version": version,
                "hash": content_hash,
                "vector_collection": target_collection,
                "evidence_refs": [source_ref],
                "source_ref": source_ref,
                "evidence_refs_primary": evidence_primary,
            },
        }
        candidates.append(payload)
    return sorted(candidates, key=lambda row: -float(row["score"]))[: max(1, int(top_k))]


def search_artifact_vault(self: Any, *, query: str, top_k: int) -> list[dict[str, Any]]:
    """Route 6：制品库 SSOT 接口检索。"""
    from host_brain.retrieval.recall_fusion_utils import (
        _normalize_recall_refs,
        _text_similarity_score,
        _truncate_text_for_recall,
    )

    if self.artifact_vault is None:
        return []
    try:
        raw_results = self.artifact_vault.search(query, topk=int(top_k))
    except Exception:
        return []
    candidates: list[dict[str, Any]] = []
    for row in raw_results or []:
        if not isinstance(row, Mapping):
            continue
        manifest_id = str(row.get("manifest_id") or "").strip()
        if not manifest_id:
            continue
        integrity = str(row.get("hash") or row.get("integrity_digest") or "").strip()
        refs = _normalize_recall_refs(
            row.get("evidence_refs"),
            fallback=f"vault://manifest/{manifest_id}",
        )
        text = " ".join(
            part for part in (
                manifest_id,
                str(row.get("intent_id") or "").strip(),
                str(row.get("policy_profile_id") or "").strip(),
                integrity,
                " ".join(refs),
            )
            if part
        )
        score = _text_similarity_score(query, text)
        if score <= 0.0:
            continue
        source_ref = f"vault://manifest/{manifest_id}"
        artifact_id_raw = row.get("artifact_id")
        artifact_id = str(artifact_id_raw).strip() if artifact_id_raw is not None else ""
        candidates.append({
            "memory_id": manifest_id,
            "text": _truncate_text_for_recall(text, limit=512),
            "score": score,
            "metadata": {
                "store_id": STORE_ARTIFACT_VAULT,
                "manifest_id": manifest_id,
                "hash": integrity or None,
                "artifact_id": artifact_id if artifact_id else None,
                "evidence_refs": refs,
                "source_ref": source_ref,
                "evidence_refs_primary": refs[0] if refs else source_ref,
            },
        })
    return sorted(candidates, key=lambda c: -float(c["score"]))[: max(1, int(top_k))]
