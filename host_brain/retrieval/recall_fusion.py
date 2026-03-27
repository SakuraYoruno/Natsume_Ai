"""Multi-route recall fusion contract over Chroma candidates.

拆分后的主模块，仅保留：
  - RecallFusionServiceContract 主类（骨架 + recall/degrade/_log_event 核心方法）
  - _normalize_candidate、_default_runtime_config
  - 从子模块 re-export 所有公共符号，保持向后兼容

子模块职责：
  - recall_fusion_utils.py   — 纯工具函数（评分、文本相似度、向量归一化等）
  - recall_fusion_stores.py  — 七路 store 搜索方法
  - recall_fusion_config.py  — 配置加载与路由解析
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
import os
from typing import Any, Mapping

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.errors import (
    RETR_RECALL_INPUT_INVALID,
    RETR_RECALL_UNAVAILABLE,
)
from host_brain.core.ids import now_ms
from host_brain.memory.chroma_client import InMemoryChromaClient
from host_brain.retrieval.recall_schemas import (
    RecallConfigIssue,
    RecallConfigLoadResult,
    RecallFusionResult,
    RecallRouteConfig,
    RecallRuntimeConfig,
)

# ── 从子模块 re-export（向后兼容）──────────────────────────────────────────
from host_brain.retrieval.recall_fusion_utils import (  # noqa: F401
    _candidate_sort_key,
    _coerce_non_negative_float,
    _coerce_non_negative_int,
    _coerce_score,
    _extract_canon_entity_id,
    _extract_session_id_from_refs,
    _map_route_exception,
    _normalize_recall_refs,
    _normalize_vector,
    _parse_bool,
    _sqlite_candidate_sort_key,
    _text_similarity_score,
    _tokenize_for_similarity,
    _truncate_text_for_recall,
)
from host_brain.retrieval.recall_fusion_config import (  # noqa: F401
    load_recall_runtime_config,
    resolve_recall_routes,
    SUPPORTED_RECALL_STORE_IDS,
    S_RET_RECALL_CONFIG,
)
from host_brain.retrieval.recall_fusion_stores import (
    STORE_SQLITE_LEDGER,
    STORE_NEO4J_BOND,
    STORE_EPISODIC_VECTOR,
    STORE_STYLE_CANON,
    STORE_WORLD_CANON,
    STORE_ARTIFACT_VAULT,
    STORE_PERSONA_UTTERANCE,
    search_route as _search_route_impl,
)
from host_brain.retrieval.hybrid_fusion import HybridFusionStrategy  # noqa: F401

logger = logging.getLogger("Natsume.retrieval.recall_fusion")

S_RET_RECALL = "S_RET_RECALL"
RET_OP_RECALL = "RECALL"

STATUS_OK = "OK"
STATUS_DEGRADED = "DEGRADED"

DEFAULT_RECALL_ROUTE = "default"
DEFAULT_RECALL_COLLECTION = "episodic"
DEFAULT_RECALL_STORE_ID = "episodic_vector"
DEFAULT_RECALL_TOP_K = 8
DEFAULT_RECALL_SCORE_WEIGHT = 1.0
DEFAULT_NODE_SELECTOR = "default"

# 混合检索默认权重（TASK-0202）
DEFAULT_VECTOR_WEIGHT: float = 0.7
DEFAULT_FTS_WEIGHT: float = 0.3


class RecallFusionServiceContract:
    """Recall candidates from multiple routes and fuse into a stable list."""

    def __init__(
        self,
        *,
        ledger: SQLiteLedger | None = None,
        chroma_client: InMemoryChromaClient | None = None,
        neo4j_client: Any | None = None,
        style_store: Any | None = None,
        world_store: Any | None = None,
        artifact_vault: Any | None = None,
        runtime_config: RecallRuntimeConfig | None = None,
        node_id: str | None = None,
        available: bool = True,
        use_hybrid: bool = True,
        vector_weight: float = DEFAULT_VECTOR_WEIGHT,
        fts_weight: float = DEFAULT_FTS_WEIGHT,
        fts_db_path: str | None = None,
    ) -> None:
        self.ledger = ledger
        self.chroma = chroma_client or InMemoryChromaClient()
        self.neo4j = neo4j_client
        self.style_store = style_store
        self.world_store = world_store
        self.artifact_vault = artifact_vault
        self.runtime_config = runtime_config or _default_runtime_config()
        self.node_id = str(node_id).strip() if node_id is not None else None
        self.available = bool(available)
        self.use_hybrid = bool(use_hybrid)
        self.vector_weight = float(vector_weight)
        self.fts_weight = float(fts_weight)
        # 初始化 FTS5 检索器（如果启用混合检索）
        self._fts_searcher: Any | None = None
        if self.use_hybrid:
            try:
                from host_brain.retrieval.full_text_searcher import FullTextSearcher
                self._fts_searcher = FullTextSearcher(db_path=fts_db_path)
            except Exception:
                self._fts_searcher = None

    # ── 路由分发（委托给 recall_fusion_stores）────────────────────────────
    def _search_route(
        self,
        *,
        route: RecallRouteConfig,
        query: str,
        query_embedding: list[float],
        render_target_lang: str | None = None,
        session_id: str | None = None,
        now_ts_ms: int | None = None,
    ) -> list[dict[str, Any]]:
        return _search_route_impl(
            self,
            route=route,
            query=query,
            query_embedding=query_embedding,
            render_target_lang=render_target_lang,
            session_id=session_id,
            now_ts_ms=now_ts_ms,
        )

    # ── 各路 store 方法（委托给 recall_fusion_stores）────────────────────
    def _search_sqlite_ledger(self, *, query: str, top_k: int, session_id: str | None = None, now_ts_ms: int | None = None) -> list[dict[str, Any]]:
        from host_brain.retrieval.recall_fusion_stores import search_sqlite_ledger
        return search_sqlite_ledger(self, query=query, top_k=top_k, session_id=session_id, now_ts_ms=now_ts_ms)

    def _search_neo4j_bond(self, *, query: str, top_k: int) -> list[dict[str, Any]]:
        from host_brain.retrieval.recall_fusion_stores import search_neo4j_bond
        return search_neo4j_bond(self, query=query, top_k=top_k)

    def _search_style_canon(
        self,
        *,
        query: str,
        query_embedding: list[float],
        top_k: int,
        collection: str | None,
        render_target_lang: str | None = None,
    ) -> list[dict[str, Any]]:
        from host_brain.retrieval.recall_fusion_stores import search_style_canon
        return search_style_canon(
            self,
            query=query,
            query_embedding=query_embedding,
            top_k=top_k,
            collection=collection,
            render_target_lang=render_target_lang,
        )

    def _search_world_canon(self, *, query: str, query_embedding: list[float], top_k: int, collection: str | None) -> list[dict[str, Any]]:
        from host_brain.retrieval.recall_fusion_stores import search_world_canon
        return search_world_canon(self, query=query, query_embedding=query_embedding, top_k=top_k, collection=collection)

    def _search_artifact_vault(self, *, query: str, top_k: int) -> list[dict[str, Any]]:
        from host_brain.retrieval.recall_fusion_stores import search_artifact_vault
        return search_artifact_vault(self, query=query, top_k=top_k)

    def _search_persona_utterance(
        self,
        *,
        query_embedding: list[float],
        top_k: int,
        collection: str | None,
        render_target_lang: str | None = None,
    ) -> list[dict[str, Any]]:
        from host_brain.retrieval.recall_fusion_stores import search_persona_utterance
        return search_persona_utterance(
            self,
            query_embedding=query_embedding,
            top_k=top_k,
            collection=collection,
            render_target_lang=render_target_lang,
        )

    def _search_canon_projection_vector(self, *, query_embedding: list[float], top_k: int, collection: str | None, canon_kind: str, store_id: str) -> list[dict[str, Any]]:
        from host_brain.retrieval.recall_fusion_stores import search_canon_projection_vector
        return search_canon_projection_vector(self, query_embedding=query_embedding, top_k=top_k, collection=collection, canon_kind=canon_kind, store_id=store_id)

    # ── 混合检索（委托给 hybrid_fusion）──────────────────────────────────
    def _apply_hybrid_fusion(
        self,
        *,
        vector_candidates: list[dict[str, Any]],
        query: str,
    ) -> list[dict[str, Any]]:
        if self._fts_searcher is None:
            return vector_candidates
        try:
            fts_results = self._fts_searcher.search(query, top_k=DEFAULT_RECALL_TOP_K)
        except Exception:
            return vector_candidates
        if not fts_results:
            return vector_candidates
        try:
            from host_brain.retrieval.hybrid_fusion import HybridFusionStrategy
            strategy = HybridFusionStrategy(
                vector_weight=self.vector_weight,
                fts_weight=self.fts_weight,
            )
        except (ImportError, ValueError):
            return vector_candidates
        return strategy.fuse(vector_candidates=vector_candidates, fts_candidates=fts_results)

    # ── 核心召回入口 ──────────────────────────────────────────────────────
    def recall(
        self,
        *,
        request_id: str,
        query: str,
        query_embedding: list[float] | tuple[float, ...] | Any,
        intent_id: str | None = None,
        session_id: str | None = None,
        ts_ms: int | None = None,
        emotion_filter: str | None = None,
        render_target_lang: str | None = None,
    ) -> RecallFusionResult:
        now_value = int(ts_ms if ts_ms is not None else now_ms())
        normalized_query = str(query or "").strip()
        normalized_vector = _normalize_vector(query_embedding)

        routes = resolve_recall_routes(
            self.runtime_config,
            node_id=self.node_id,
            render_target_lang=render_target_lang,
        )
        route_rows: list[dict[str, Any]] = []
        route_errors: list[dict[str, Any]] = []

        if not normalized_query or not normalized_vector:
            return self._degrade(
                request_id=request_id, intent_id=intent_id, query=normalized_query,
                routes=routes, candidates=[], route_rows=[], route_errors=[],
                reason_code=RETR_RECALL_INPUT_INVALID,
                detail="recall input invalid: query and query_embedding are required",
                ts_ms=now_value,
            )

        if not self.runtime_config.enabled:
            return self._degrade(
                request_id=request_id, intent_id=intent_id, query=normalized_query,
                routes=routes, candidates=[], route_rows=[], route_errors=[],
                reason_code=RETR_RECALL_UNAVAILABLE,
                detail="recall runtime disabled by config",
                ts_ms=now_value,
            )

        if not self.available:
            return self._degrade(
                request_id=request_id, intent_id=intent_id, query=normalized_query,
                routes=routes, candidates=[], route_rows=[], route_errors=[],
                reason_code=RETR_RECALL_UNAVAILABLE,
                detail="recall service unavailable",
                ts_ms=now_value,
            )

        fused_candidates: dict[str, dict[str, Any]] = {}
        for route in routes:
            try:
                rows = self._search_route(
                    route=route,
                    query=normalized_query,
                    query_embedding=normalized_vector,
                    render_target_lang=render_target_lang,
                    session_id=session_id,
                    now_ts_ms=now_value,
                )
            except Exception as exc:
                reason_code = _map_route_exception(exc, store_id=route.store_id)
                route_errors.append({
                    "route_id": route.route_id,
                    "store_id": route.store_id,
                    "collection": route.collection,
                    "render_target_lang_selector": route.render_target_lang_selector,
                    "reason_code": reason_code,
                    "detail": str(exc),
                })
                route_rows.append({
                    "route_id": route.route_id,
                    "store_id": route.store_id,
                    "collection": route.collection,
                    "render_target_lang_selector": route.render_target_lang_selector,
                    "status": STATUS_DEGRADED,
                    "reason_code": reason_code,
                    "detail": str(exc),
                    "candidate_count": 0,
                })
                continue

            route_rows.append({
                "route_id": route.route_id,
                "store_id": route.store_id,
                "collection": route.collection,
                "render_target_lang_selector": route.render_target_lang_selector,
                "status": STATUS_OK,
                "reason_code": None,
                "detail": "recall route completed",
                "candidate_count": len(rows),
            })

            for rank, raw in enumerate(rows):
                candidate = _normalize_candidate(raw=raw, route=route, rank=rank)
                if not candidate:
                    continue
                key = str(candidate["source_ref"] or candidate["doc_id"])
                existing = fused_candidates.get(key)
                if existing is None:
                    fused_candidates[key] = candidate
                    continue
                if float(candidate["score"]) > float(existing["score"]):
                    merged = dict(candidate)
                else:
                    merged = dict(existing)
                merged_routes = sorted(
                    set(existing["metadata"].get("matched_routes", []))
                    .union(set(candidate["metadata"].get("matched_routes", [])))
                )
                merged["metadata"] = dict(merged["metadata"])
                merged["metadata"]["matched_routes"] = merged_routes
                fused_candidates[key] = merged

        candidates = sorted(fused_candidates.values(), key=_candidate_sort_key)

        # 混合检索：叠加 FTS5 全文检索结果（TASK-0202）
        if self.use_hybrid and self._fts_searcher is not None:
            candidates = self._apply_hybrid_fusion(
                vector_candidates=candidates,
                query=normalized_query,
            )

        # M27-G：情感维度路由（OPT-31）
        if emotion_filter is not None and candidates:
            matched = [c for c in candidates if c.get("metadata", {}).get("emotion_tag") == emotion_filter]
            unmatched = [c for c in candidates if c.get("metadata", {}).get("emotion_tag") != emotion_filter]
            candidates = matched + unmatched

        if not candidates:
            reason_code = route_errors[0]["reason_code"] if route_errors else RETR_RECALL_UNAVAILABLE
            detail = (
                route_errors[0]["detail"] if route_errors
                else "no recall candidates from configured routes"
            )
            return self._degrade(
                request_id=request_id, intent_id=intent_id, query=normalized_query,
                routes=routes, candidates=[], route_rows=route_rows, route_errors=route_errors,
                reason_code=reason_code, detail=detail, ts_ms=now_value,
            )

        degraded = len(route_errors) > 0
        reason_code = route_errors[0]["reason_code"] if degraded else None
        status = STATUS_DEGRADED if degraded else STATUS_OK
        output = {
            "request_id": request_id,
            "intent_id": intent_id,
            "query": normalized_query,
            "node_id": self.node_id,
            "status": status,
            "stage": S_RET_RECALL,
            "reason_code": reason_code,
            "degraded": degraded,
            "routes": [route.as_dict() for route in routes],
            "route_rows": route_rows,
            "route_errors": route_errors,
            "candidate_count": len(candidates),
            "candidates": candidates,
            "ts_ms": now_value,
        }
        detail = "multi-route recall fused"
        self._log_event(
            request_id=request_id, intent_id=intent_id, status=status,
            reason_code=reason_code, detail=detail,
            payload={
                "query_length": len(normalized_query),
                "route_count": len(routes),
                "route_error_count": len(route_errors),
                "candidate_count": len(candidates),
                "output_status": status,
            },
            ts_ms=now_value,
        )
        return RecallFusionResult(
            success=not degraded,
            status=status,
            reason_code=reason_code,
            stage=S_RET_RECALL,
            detail=detail if not degraded else "multi-route recall fused with partial degradation",
            output=output,
        )

    def _degrade(
        self,
        *,
        request_id: str,
        intent_id: str | None,
        query: str,
        routes: list[RecallRouteConfig],
        candidates: list[dict[str, Any]],
        route_rows: list[dict[str, Any]],
        route_errors: list[dict[str, Any]],
        reason_code: str,
        detail: str,
        ts_ms: int,
    ) -> RecallFusionResult:
        output = {
            "request_id": request_id,
            "intent_id": intent_id,
            "query": query,
            "node_id": self.node_id,
            "status": STATUS_DEGRADED,
            "stage": S_RET_RECALL,
            "reason_code": reason_code,
            "degraded": True,
            "routes": [route.as_dict() for route in routes],
            "route_rows": route_rows,
            "route_errors": route_errors,
            "candidate_count": len(candidates),
            "candidates": candidates,
            "ts_ms": ts_ms,
        }
        self._log_event(
            request_id=request_id, intent_id=intent_id, status=STATUS_DEGRADED,
            reason_code=reason_code, detail=detail,
            payload={
                "query_length": len(query),
                "route_count": len(routes),
                "route_error_count": len(route_errors),
                "candidate_count": len(candidates),
                "output_status": STATUS_DEGRADED,
            },
            ts_ms=ts_ms,
        )
        return RecallFusionResult(
            success=False,
            status=STATUS_DEGRADED,
            reason_code=reason_code,
            stage=S_RET_RECALL,
            detail=detail,
            output=output,
        )

    def _log_event(
        self,
        *,
        request_id: str,
        intent_id: str | None,
        status: str,
        reason_code: str | None,
        detail: str,
        payload: dict[str, Any],
        ts_ms: int,
    ) -> None:
        if self.ledger is not None and hasattr(self.ledger, "log_retrieval_event"):
            self.ledger.log_retrieval_event({
                "request_id": request_id,
                "intent_id": intent_id,
                "op_kind": RET_OP_RECALL,
                "status": status,
                "reason_code": reason_code,
                "stage": S_RET_RECALL,
                "payload": payload,
                "detail": detail,
                "ts_ms": ts_ms,
            })
            return
        if self.ledger is not None and hasattr(self.ledger, "log_error") and reason_code is not None:
            self.ledger.log_error(
                request_id=request_id,
                stage=S_RET_RECALL,
                reason_code=reason_code,
                detail=detail,
            )


def _normalize_candidate(
    *,
    raw: Mapping[str, Any],
    route: RecallRouteConfig,
    rank: int,
) -> dict[str, Any] | None:
    memory_id = str(raw.get("memory_id") or "").strip()
    if not memory_id:
        return None
    text = str(raw.get("text") or "").strip()
    metadata = dict(raw.get("metadata") or {}) if isinstance(raw.get("metadata"), Mapping) else {}

    base_score = _coerce_score(raw.get("score"))
    weighted_score = round(base_score * float(route.score_weight), 6)
    collection_for_ref = str(route.collection or route.store_id or DEFAULT_RECALL_COLLECTION)
    source_ref = str(metadata.get("source_ref") or f"memory://{collection_for_ref}/{memory_id}")
    evidence_refs_primary = str(
        metadata.get("evidence_refs_primary")
        or metadata.get("evidence_ref")
        or f"evd://{collection_for_ref}/{memory_id}"
    )
    merged_metadata = dict(metadata)
    merged_metadata["store_id"] = route.store_id
    merged_metadata["collection"] = route.collection
    merged_metadata["route_id"] = route.route_id
    merged_metadata["route_score_weight"] = float(route.score_weight)
    merged_metadata["base_score"] = base_score
    merged_metadata["matched_routes"] = [route.route_id]
    merged_metadata["recall_rank"] = int(rank)

    return {
        "doc_id": memory_id,
        "source_ref": source_ref,
        "snippet": text,
        "score": weighted_score,
        "confidence": weighted_score,
        "metadata": merged_metadata,
        "evidence_refs": evidence_refs_primary,
    }


def _default_runtime_config() -> RecallRuntimeConfig:
    route = RecallRouteConfig(
        route_id=DEFAULT_RECALL_ROUTE,
        store_id=STORE_EPISODIC_VECTOR,
        collection=DEFAULT_RECALL_COLLECTION,
        top_k=DEFAULT_RECALL_TOP_K,
        score_weight=DEFAULT_RECALL_SCORE_WEIGHT,
        node_selector=DEFAULT_NODE_SELECTOR,
    )
    return RecallRuntimeConfig(
        enabled=True,
        default_route=DEFAULT_RECALL_ROUTE,
        routes={DEFAULT_RECALL_ROUTE: route},
    )
