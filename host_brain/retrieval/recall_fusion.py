"""Multi-route recall fusion contract over Chroma candidates.

拆分后的主模块，仅保留：
  - RecallFusionServiceContract 主类（骨架 + recall/degrade/_log_event 核心方法）
  - _normalize_candidate、_default_runtime_config
  - 从子模块 re-export 所有公共符号，保持向后兼容

子模块职责：
  - recall_fusion_utils.py   — 纯工具函数（评分、文本相似度、向量归一化等）
  - recall_fusion_stores.py  — 五路 store 搜索方法（Phase XIII 精简后）
  - recall_fusion_config.py  — 配置加载与路由解析
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
import os
import re
from typing import Any, Mapping

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.errors import (
    CHROMA_ENDPOINT_UNAVAILABLE,
    RETR_RECALL_INPUT_INVALID,
    RETR_RECALL_UNAVAILABLE,
)
from host_brain.core.ids import now_ms
from host_brain.memory.chroma_client import (
    ChromaClientProtocol,
    build_runtime_chroma_client,
)
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
    STORE_LIFE_EPISODE,
    STORE_NEO4J_BOND,
    STORE_EPISODIC_VECTOR,
    STORE_SEMANTIC_MEMORY,
    STORE_WORLD_CANON,
    STORE_ARTIFACT_VAULT,
    search_route as _search_route_impl,
)
from host_brain.retrieval.hybrid_fusion import HybridFusionStrategy  # noqa: F401
from host_brain.retrieval.audit_preview import build_retrieval_items_preview

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

_WARM_EMOTION_TAGS = frozenset({"happy", "calm", "warm", "trust", "affection", "grateful", "love", "joy"})
_FRAGILE_EMOTION_TAGS = frozenset({"sad", "fear", "angry", "anxious", "hurt", "pain", "guilt", "shame"})
_CONTINUITY_HINT_KEYWORDS = ("上次", "之前", "后来", "还记得", "一起", "约定", "说好", "那天", "继续", "remember", "last time", "together", "promise")
_WARM_HINT_KEYWORDS = ("一起", "陪你", "说好", "约定", "开心", "想你", "等你", "抱", "温柔", "高兴", "庆祝")
_ANAPHORIC_SELECTION_RE = re.compile(
    r"(?:哪(?:本|部|个|一(?:本|部|个))|哪个|哪部|哪本|"
    r"(?:我问你|刚问你|问你).{0,12}哪|"
    r"(?:喜欢|想看|先看|推荐|选|挑).{0,8}哪)",
)
_ANAPHORIC_EVENT_KINDS = frozenset({"action_episode", "background_episode", "perception_episode"})
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
_ANAPHORIC_POSITIVE_ANCHORS = (
    "推荐",
    "lightnovel",
    "小说",
    "书",
    "qq",
    "wechat",
    "微信",
    "推送",
    "发送",
    "delivery",
)
_ANAPHORIC_SCHEDULER_NOISE_TERMS = (
    "emotion_decay_tick",
    "inner_world_numeric_tick",
    "silence_tick",
    "heartbeat",
    "health_check",
    "resource_guard",
)
_ANAPHORIC_EVENT_WINDOW_MS = 2 * 60 * 1000
_BOND_STAGE_DEPTH_MAP = {
    "familiar": 0.38,
    "friend": 0.62,
    "close_friend": 0.78,
    "family": 0.86,
    "partner": 0.90,
    "intimate": 0.92,
    "deep": 0.88,
}
_PROTECTIVE_MOOD_BIAS = {
    "critical": 0.95,
    "withdrawn": 0.90,
    "depressed": 0.88,
    "low": 0.80,
    "tired": 0.72,
    "negative": 0.68,
    "sad": 0.66,
}
_CONTINUITY_MOOD_BIAS = {
    "engaged": 0.72,
    "warm": 0.76,
    "positive": 0.74,
    "calm": 0.56,
    "steady": 0.46,
    "tired": 0.26,
    "critical": 0.12,
    "withdrawn": 0.18,
}
_WARM_MOOD_BIAS = {
    "engaged": 0.74,
    "warm": 0.82,
    "positive": 0.78,
    "calm": 0.60,
    "steady": 0.44,
    "tired": 0.28,
    "critical": 0.14,
    "withdrawn": 0.20,
}


class _UnavailableChromaClient:
    def __init__(self, *, reason: str) -> None:
        self.reason = str(reason or CHROMA_ENDPOINT_UNAVAILABLE)

    @property
    def collection_profiles(self) -> Mapping[str, int]:
        return {}

    @property
    def default_collection(self) -> str:
        return "episodic"

    def upsert(self, **_kwargs: Any) -> None:
        raise RuntimeError(self.reason)

    def delete(self, _memory_id: str, **_kwargs: Any) -> None:
        raise RuntimeError(self.reason)

    def get(self, _memory_id: str, **_kwargs: Any) -> None:
        raise RuntimeError(self.reason)

    def search(self, **_kwargs: Any) -> list[dict[str, Any]]:
        raise RuntimeError(self.reason)

    def validate_collection(self, _collection: str, *, vector_dim: int | None = None) -> tuple[bool, str | None]:
        return False, self.reason


def _build_optional_runtime_chroma_client() -> ChromaClientProtocol:
    try:
        return build_runtime_chroma_client()
    except Exception as exc:
        detail = str(exc).strip() or CHROMA_ENDPOINT_UNAVAILABLE
        logger.warning("Chroma unavailable during recall service init; vector routes will degrade: %s", detail)
        return _UnavailableChromaClient(reason=detail)


class RecallFusionServiceContract:
    """Recall candidates from multiple routes and fuse into a stable list."""

    def __init__(
        self,
        *,
        ledger: SQLiteLedger | None = None,
        chroma_client: ChromaClientProtocol | None = None,
        neo4j_client: Any | None = None,
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
        self.chroma = chroma_client or _build_optional_runtime_chroma_client()
        self.neo4j = neo4j_client
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
        person_id: str | None = None,
        session_id: str | None = None,
        now_ts_ms: int | None = None,
    ) -> list[dict[str, Any]]:
        return _search_route_impl(
            self,
            route=route,
            query=query,
            query_embedding=query_embedding,
            render_target_lang=render_target_lang,
            person_id=person_id,
            session_id=session_id,
            now_ts_ms=now_ts_ms,
        )

    # ── 各路 store 方法（委托给 recall_fusion_stores）────────────────────
    def _search_sqlite_ledger(self, *, query: str, top_k: int, person_id: str | None = None, session_id: str | None = None, now_ts_ms: int | None = None) -> list[dict[str, Any]]:
        from host_brain.retrieval.recall_fusion_stores import search_sqlite_ledger
        return search_sqlite_ledger(self, query=query, top_k=top_k, person_id=person_id, session_id=session_id, now_ts_ms=now_ts_ms)

    def _search_life_episode(self, *, query: str, top_k: int, person_id: str | None = None, session_id: str | None = None, now_ts_ms: int | None = None) -> list[dict[str, Any]]:
        from host_brain.retrieval.recall_fusion_stores import search_life_episode
        return search_life_episode(self, query=query, top_k=top_k, person_id=person_id, session_id=session_id, now_ts_ms=now_ts_ms)

    def _search_semantic_memory(self, *, query: str, top_k: int, now_ts_ms: int | None = None) -> list[dict[str, Any]]:
        from host_brain.retrieval.recall_fusion_stores import search_semantic_memory
        return search_semantic_memory(self, query=query, top_k=top_k, now_ts_ms=now_ts_ms)

    def _search_neo4j_bond(self, *, query: str, top_k: int) -> list[dict[str, Any]]:
        from host_brain.retrieval.recall_fusion_stores import search_neo4j_bond
        return search_neo4j_bond(self, query=query, top_k=top_k)

    def _search_world_canon(self, *, query: str, query_embedding: list[float], top_k: int, collection: str | None) -> list[dict[str, Any]]:
        from host_brain.retrieval.recall_fusion_stores import search_world_canon
        return search_world_canon(self, query=query, query_embedding=query_embedding, top_k=top_k, collection=collection)

    def _search_artifact_vault(self, *, query: str, top_k: int) -> list[dict[str, Any]]:
        from host_brain.retrieval.recall_fusion_stores import search_artifact_vault
        return search_artifact_vault(self, query=query, top_k=top_k)

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
        person_id: str | None = None,
        session_id: str | None = None,
        ts_ms: int | None = None,
        emotion_filter: str | None = None,
        mood_state: str | None = None,
        bond_summary: Mapping[str, Any] | None = None,
        motive_state: Mapping[str, Any] | None = None,
        render_target_lang: str | None = None,
        target_store_ids: list[str] | None = None,
        time_lookback_days: int | None = None,
    ) -> RecallFusionResult:
        now_value = int(ts_ms if ts_ms is not None else now_ms())
        normalized_query = str(query or "").strip()
        normalized_vector = _normalize_vector(query_embedding)

        routes = resolve_recall_routes(
            self.runtime_config,
            node_id=self.node_id,
            render_target_lang=render_target_lang,
        )
        routes = _filter_recall_routes(routes=routes, target_store_ids=target_store_ids)
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

        persona_recall_profile = _build_persona_recall_profile(
            mood_state=mood_state,
            bond_summary=bond_summary,
            motive_state=motive_state,
        )

        fused_candidates: dict[str, dict[str, Any]] = {}
        for route in routes:
            _route_start_ms = now_ms()
            try:
                rows = self._search_route(
                    route=route,
                    query=normalized_query,
                    query_embedding=normalized_vector,
                    render_target_lang=render_target_lang,
                    person_id=person_id,
                    session_id=session_id,
                    now_ts_ms=now_value,
                )
            except Exception as exc:
                _route_latency_ms = now_ms() - _route_start_ms
                reason_code = _map_route_exception(exc, store_id=route.store_id)
                route_errors.append({
                    "route_id": route.route_id,
                    "store_id": route.store_id,
                    "collection": route.collection,
                    "render_target_lang_selector": route.render_target_lang_selector,
                    "reason_code": reason_code,
                    "detail": str(exc),
                    "latency_ms": _route_latency_ms,
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
                    "latency_ms": _route_latency_ms,
                })
                continue

            _route_latency_ms = now_ms() - _route_start_ms
            route_rows.append({
                "route_id": route.route_id,
                "store_id": route.store_id,
                "collection": route.collection,
                "render_target_lang_selector": route.render_target_lang_selector,
                "status": STATUS_OK,
                "reason_code": None,
                "detail": "recall route completed",
                "candidate_count": len(rows),
                "latency_ms": _route_latency_ms,
            })

            for rank, raw in enumerate(rows):
                candidate = _normalize_candidate(raw=raw, route=route, rank=rank)
                if not candidate:
                    continue
                candidate = _apply_persona_recall_weight(
                    candidate=candidate,
                    route=route,
                    persona_profile=persona_recall_profile,
                )
                # FIX: 使用 store_id::doc_id 作为 key，防止不同 store 产生相同
                # source_ref/doc_id 时发生跨路由错误合并（原实现仅用 source_ref 或 doc_id）。
                store_id_for_key = str(candidate["metadata"].get("store_id") or route.store_id or "")
                doc_id_for_key = str(candidate["source_ref"] or candidate["doc_id"])
                key = f"{store_id_for_key}::{doc_id_for_key}" if store_id_for_key else doc_id_for_key
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

        if time_lookback_days is not None:
            candidates = [
                candidate
                for candidate in candidates
                if _candidate_within_lookback(candidate, now_ts_ms=now_value, lookback_days=time_lookback_days)
            ]

        candidates = _filter_anaphoric_selection_candidates(
            candidates,
            query=normalized_query,
        )

        # M27-G：情感维度路由（OPT-31）
        # FIX: 原实现仅做列表拼接，两个分组内部排序丢失。
        # 修复后：matched/unmatched 各自按原始分数降序保持稳定，再拼接。
        if emotion_filter is not None and candidates:
            ef = str(emotion_filter or "").strip().lower()
            def _has_emotion_tag(c: dict) -> bool:
                tag = str(c.get("metadata", {}).get("emotion_tag") or "").strip().lower()
                return bool(tag and (tag == ef or tag.startswith(ef)))
            matched = sorted(
                [c for c in candidates if _has_emotion_tag(c)],
                key=_candidate_sort_key,
            )
            unmatched = sorted(
                [c for c in candidates if not _has_emotion_tag(c)],
                key=_candidate_sort_key,
            )
            candidates = matched + unmatched

        if not candidates:
            if route_errors:
                reason_code = route_errors[0]["reason_code"]
                detail = route_errors[0]["detail"]
                return self._degrade(
                    request_id=request_id, intent_id=intent_id, query=normalized_query,
                    routes=routes, candidates=[], route_rows=route_rows, route_errors=route_errors,
                    reason_code=reason_code, detail=detail, ts_ms=now_value,
                )

            output = {
                "request_id": request_id,
                "intent_id": intent_id,
                "query": normalized_query,
                "node_id": self.node_id,
                "status": STATUS_OK,
                "stage": S_RET_RECALL,
                "reason_code": None,
                "degraded": False,
                "routes": [route.as_dict() for route in routes],
                "route_rows": route_rows,
                "route_errors": [],
                "candidate_count": 0,
                "candidates": [],
                "ts_ms": now_value,
            }
            detail = "recall completed with no candidates"
            self._log_event(
                request_id=request_id, intent_id=intent_id, status=STATUS_OK,
                reason_code=None, detail=detail,
                payload={
                    "query_length": len(normalized_query),
                    "route_count": len(routes),
                    "route_error_count": 0,
                    "candidate_count": 0,
                    "items": [],
                    "output_status": STATUS_OK,
                    "latency_ms": sum(r.get("latency_ms", 0) for r in route_rows),
                    "route_latencies": [
                        {"store_id": r["store_id"], "latency_ms": r.get("latency_ms", 0),
                         "candidate_count": r.get("candidate_count", 0), "status": r.get("status", "OK")}
                        for r in route_rows
                    ],
                },
                ts_ms=now_value,
            )
            return RecallFusionResult(
                success=True,
                status=STATUS_OK,
                reason_code=None,
                stage=S_RET_RECALL,
                detail=detail,
                output=output,
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
                "items": build_retrieval_items_preview(candidates),
                "output_status": status,
                "latency_ms": sum(r.get("latency_ms", 0) for r in route_rows),
                "route_latencies": [
                    {"store_id": r["store_id"], "latency_ms": r.get("latency_ms", 0),
                     "candidate_count": r.get("candidate_count", 0), "status": r.get("status", "OK")}
                    for r in route_rows
                ],
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
                "items": build_retrieval_items_preview(candidates),
                "output_status": STATUS_DEGRADED,
                "latency_ms": sum(r.get("latency_ms", 0) for r in route_rows),
                "route_latencies": [
                    {"store_id": r["store_id"], "latency_ms": r.get("latency_ms", 0),
                     "candidate_count": r.get("candidate_count", 0), "status": r.get("status", "DEGRADED")}
                    for r in route_rows
                ],
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


def _filter_anaphoric_selection_candidates(
    candidates: list[dict[str, Any]],
    *,
    query: str,
) -> list[dict[str, Any]]:
    if not candidates or not _looks_like_anaphoric_selection_query(query):
        return candidates
    primary = [
        candidate
        for candidate in candidates
        if _is_significant_anaphoric_life_candidate(candidate)
    ]
    if not primary:
        return candidates
    primary = _select_recent_anaphoric_life_primary(primary)
    kept: list[dict[str, Any]] = []
    seen: set[str] = set()
    for candidate in primary:
        key = str(candidate.get("source_ref") or candidate.get("doc_id") or "").strip()
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        updated = dict(candidate)
        metadata = dict(updated.get("metadata") or {})
        metadata["anaphoric_selection_primary"] = True
        metadata["anaphoric_selection_filter"] = "life_episode_primary"
        updated["metadata"] = metadata
        kept.append(updated)
    relationship = [
        candidate
        for candidate in candidates
        if _candidate_store_id(candidate) in {STORE_NEO4J_BOND, "bond_graph"}
    ]
    for candidate in relationship:
        key = str(candidate.get("source_ref") or candidate.get("doc_id") or "").strip()
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        kept.append(candidate)
    return sorted(kept, key=_candidate_sort_key)


def _select_recent_anaphoric_life_primary(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if len(candidates) <= 1:
        return candidates
    ranked = sorted(candidates, key=_anaphoric_primary_sort_key)
    newest = ranked[0]
    newest_ts = _candidate_life_ts(newest)
    newest_action = _candidate_life_action(newest)
    newest_session = _candidate_life_session(newest)
    if newest_ts <= 0:
        return [newest]
    selected: list[dict[str, Any]] = []
    for candidate in ranked:
        candidate_ts = _candidate_life_ts(candidate)
        if candidate_ts <= 0:
            continue
        if newest_action and _candidate_life_action(candidate) != newest_action:
            continue
        if newest_session and _candidate_life_session(candidate) != newest_session:
            continue
        if newest_ts - candidate_ts > _ANAPHORIC_EVENT_WINDOW_MS:
            continue
        selected.append(candidate)
    return selected or [newest]


def _anaphoric_primary_sort_key(candidate: Mapping[str, Any]) -> tuple[int, int, float, str]:
    return (
        _candidate_anaphoric_action_priority(candidate),
        -_candidate_life_ts(candidate),
        -_coerce_score(candidate.get("score")),
        str(candidate.get("doc_id") or ""),
    )


def _candidate_anaphoric_action_priority(candidate: Mapping[str, Any]) -> int:
    action = _candidate_life_action(candidate)
    if action in {"lightnovel_recommendation_delivery", "recommendation_delivery"} or action.endswith("_recommendation_delivery"):
        return 0
    if action in {"lightnovel_gateway_delivery", "gateway_delivery"} or action.endswith("_gateway_delivery"):
        return 1
    if action in {"lightnovel_recommendation_deep_review", "recommendation_deep_review"} or action.endswith("_deep_review"):
        return 8
    return 4


def _candidate_life_action(candidate: Mapping[str, Any]) -> str:
    metadata = _coerce_mapping(candidate.get("metadata"))
    return str(metadata.get("action") or metadata.get("action_kind") or "").strip().lower()


def _candidate_life_session(candidate: Mapping[str, Any]) -> str:
    metadata = _coerce_mapping(candidate.get("metadata"))
    return str(metadata.get("session_id") or "").strip()


def _candidate_life_ts(candidate: Mapping[str, Any]) -> int:
    metadata = _coerce_mapping(candidate.get("metadata"))
    for key in ("last_touched_ts_ms", "created_ts_ms"):
        try:
            value = int(metadata.get(key) or 0)
        except (TypeError, ValueError):
            value = 0
        if value > 0:
            return value
    score_boost = _coerce_mapping(metadata.get("score_boost"))
    try:
        return max(0, int(score_boost.get("row_ts_ms") or 0))
    except (TypeError, ValueError):
        return 0


def _looks_like_anaphoric_selection_query(query: str) -> bool:
    value = str(query or "").strip()
    return bool(value and _ANAPHORIC_SELECTION_RE.search(value))


def _is_significant_anaphoric_life_candidate(candidate: Mapping[str, Any]) -> bool:
    if _candidate_store_id(candidate) != STORE_LIFE_EPISODE:
        return False
    metadata = _coerce_mapping(candidate.get("metadata"))
    episode_kind = str(metadata.get("episode_kind") or "").strip()
    if episode_kind not in _ANAPHORIC_EVENT_KINDS:
        return False
    action = str(metadata.get("action") or metadata.get("action_kind") or "").strip().lower()
    if action in _ANAPHORIC_LOW_VALUE_ACTIONS:
        return False
    text = _candidate_text(candidate)
    if not text or _looks_like_anaphoric_scheduler_noise(text):
        return False
    return True


def _looks_like_anaphoric_scheduler_noise(text: str) -> bool:
    value = str(text or "").strip().lower()
    if not value:
        return False
    if any(anchor in value for anchor in _ANAPHORIC_POSITIVE_ANCHORS):
        return False
    if "后台任务记忆" in value and "scheduler " in value:
        return True
    return any(term in value for term in _ANAPHORIC_SCHEDULER_NOISE_TERMS)


def _candidate_store_id(candidate: Mapping[str, Any]) -> str:
    metadata = _coerce_mapping(candidate.get("metadata"))
    return str(candidate.get("store_id") or metadata.get("store_id") or "").strip().lower()


def _candidate_text(candidate: Mapping[str, Any]) -> str:
    return str(candidate.get("snippet") or candidate.get("text") or "").strip()


def _clamp_unit_float(value: Any, *, default: float = 0.0) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(1.0, numeric))


def _coerce_mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _extract_drive_score(raw: Mapping[str, Any], *keys: str) -> float:
    drives = _coerce_mapping(raw.get("drives"))
    for key in keys:
        if key in raw:
            return _clamp_unit_float(raw.get(key))
        if key in drives:
            return _clamp_unit_float(drives.get(key))
    return 0.0


def _estimate_bond_depth(bond_summary: Mapping[str, Any]) -> float:
    raw = _coerce_mapping(bond_summary)
    for key in ("bond_depth", "depth_score", "bond_score", "score", "closeness", "intimacy", "trust_score"):
        value = raw.get(key)
        if value in (None, ""):
            continue
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            continue
        if numeric > 1.0:
            numeric = numeric / 100.0
        return max(0.0, min(1.0, numeric))
    stage = str(raw.get("stage") or raw.get("bond_stage") or "").strip().lower()
    return _BOND_STAGE_DEPTH_MAP.get(stage, 0.0)


def _build_persona_recall_profile(
    *,
    mood_state: str | None,
    bond_summary: Mapping[str, Any] | None,
    motive_state: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    normalized_bond = _coerce_mapping(bond_summary)
    normalized_motive = _coerce_mapping(motive_state)
    normalized_mood = str(mood_state or "").strip().lower()
    if not normalized_mood and not normalized_bond and not normalized_motive:
        return None

    bond_depth = _estimate_bond_depth(normalized_bond)
    reach_out_drive = _extract_drive_score(normalized_motive, "reach_out_drive", "reach_out")
    care_drive = _extract_drive_score(normalized_motive, "care_drive", "care")
    continuity_drive = _extract_drive_score(normalized_motive, "continuity_drive", "continuity")
    withdraw_drive = _extract_drive_score(normalized_motive, "withdraw_drive", "withdraw")
    self_protect_drive = _extract_drive_score(normalized_motive, "self_protect_drive", "self_protect")
    dominant_motive = str(normalized_motive.get("dominant_motive") or "").strip().lower()

    protect_bias = max(
        _PROTECTIVE_MOOD_BIAS.get(normalized_mood, 0.22),
        withdraw_drive,
        self_protect_drive,
    )
    continuity_bias = _clamp_unit_float(
        max(
            _CONTINUITY_MOOD_BIAS.get(normalized_mood, 0.36),
            care_drive,
            continuity_drive,
            reach_out_drive * 0.82,
            bond_depth * 0.60,
        ) - withdraw_drive * 0.08
    )
    warm_bias = _clamp_unit_float(
        max(
            _WARM_MOOD_BIAS.get(normalized_mood, 0.34),
            care_drive,
            reach_out_drive * 0.35,
            max(0.0, bond_depth - 0.15),
        ) - self_protect_drive * 0.08
    )

    if dominant_motive in {"self_protect", "withdraw", "withdrawal"}:
        protect_bias = max(protect_bias, 0.75)
        continuity_bias = _clamp_unit_float(continuity_bias - 0.08)
    if dominant_motive in {"continuity", "reach_out", "care"}:
        continuity_bias = max(continuity_bias, 0.70)
        warm_bias = max(warm_bias, 0.62)

    return {
        "mood_state": normalized_mood or "steady",
        "bond_stage": str(normalized_bond.get("stage") or normalized_bond.get("bond_stage") or "").strip(),
        "bond_depth": round(_clamp_unit_float(bond_depth), 4),
        "dominant_motive": dominant_motive,
        "reach_out_drive": round(reach_out_drive, 4),
        "care_drive": round(care_drive, 4),
        "continuity_drive": round(continuity_drive, 4),
        "withdraw_drive": round(withdraw_drive, 4),
        "self_protect_drive": round(self_protect_drive, 4),
        "protect_bias": round(_clamp_unit_float(protect_bias), 4),
        "continuity_bias": round(_clamp_unit_float(continuity_bias), 4),
        "warm_bias": round(_clamp_unit_float(warm_bias), 4),
    }


def _memory_matches_keywords(text: str, keywords: tuple[str, ...]) -> bool:
    if not text:
        return False
    lowered = text.lower()
    return any(keyword.lower() in lowered for keyword in keywords)


def _classify_persona_memory(candidate: Mapping[str, Any]) -> dict[str, Any]:
    metadata = _coerce_mapping(candidate.get("metadata"))
    text_blob = " ".join(
        str(part or "")
        for part in (
            candidate.get("snippet"),
            metadata.get("source_ref"),
            metadata.get("memory_kind"),
            metadata.get("topic"),
        )
        if str(part or "").strip()
    )
    emotion_tag = str(metadata.get("emotion_tag") or "").strip().lower()
    emotion_color_tag = str(metadata.get("emotion_color_tag") or "").strip().lower()
    suppression_flag = _parse_bool(metadata.get("suppression_flag"), default=False)
    distortion_level = _clamp_unit_float(metadata.get("distortion_level"))
    continuity_memory = _memory_matches_keywords(text_blob, _CONTINUITY_HINT_KEYWORDS)
    warm_memory = bool({emotion_tag, emotion_color_tag}.intersection(_WARM_EMOTION_TAGS)) or _memory_matches_keywords(text_blob, _WARM_HINT_KEYWORDS)
    fragile_memory = suppression_flag or distortion_level >= 0.45 or bool({emotion_tag, emotion_color_tag}.intersection(_FRAGILE_EMOTION_TAGS))
    return {
        "continuity_memory": continuity_memory,
        "warm_memory": warm_memory,
        "fragile_memory": fragile_memory,
        "suppression_flag": suppression_flag,
        "distortion_level": distortion_level,
    }


def _apply_persona_recall_weight(
    *,
    candidate: dict[str, Any],
    route: RecallRouteConfig,
    persona_profile: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if route.store_id != STORE_EPISODIC_VECTOR or not persona_profile:
        return candidate

    score = _coerce_score(candidate.get("score"))
    metadata = _coerce_mapping(candidate.get("metadata"))
    memory_flags = _classify_persona_memory(candidate)
    protect_bias = _clamp_unit_float(persona_profile.get("protect_bias"))
    continuity_bias = _clamp_unit_float(persona_profile.get("continuity_bias"))
    warm_bias = _clamp_unit_float(persona_profile.get("warm_bias"))
    bond_depth = _clamp_unit_float(persona_profile.get("bond_depth"))
    reach_out_drive = _clamp_unit_float(persona_profile.get("reach_out_drive"))
    care_drive = _clamp_unit_float(persona_profile.get("care_drive"))
    continuity_drive = _clamp_unit_float(persona_profile.get("continuity_drive"))
    withdraw_drive = _clamp_unit_float(persona_profile.get("withdraw_drive"))
    self_protect_drive = _clamp_unit_float(persona_profile.get("self_protect_drive"))
    shared_history_pull = _clamp_unit_float(
        continuity_drive * 0.55 + care_drive * 0.25 + reach_out_drive * 0.20
    )
    relational_safety = _clamp_unit_float(max(bond_depth * 0.80, warm_bias * 0.75))
    defensive_recoil = _clamp_unit_float(max(protect_bias, self_protect_drive, withdraw_drive))

    multiplier = 1.0
    reason_tags: list[str] = []

    if memory_flags["continuity_memory"]:
        continuity_boost = 0.08 * continuity_bias + 0.03 * bond_depth
        multiplier += continuity_boost
        if continuity_boost > 0.0:
            reason_tags.append("continuity_boost")
        if bond_depth >= 0.90 or shared_history_pull >= 0.70:
            multiplier += 0.05 if shared_history_pull >= 0.70 else 0.04
            reason_tags.append("bond_depth_boost")

    if memory_flags["warm_memory"]:
        warm_boost = 0.06 * warm_bias + 0.02 * max(0.0, continuity_bias - 0.5)
        multiplier += warm_boost
        if warm_boost > 0.0:
            reason_tags.append("warm_memory_boost")

    if memory_flags["continuity_memory"] and memory_flags["warm_memory"]:
        resonance_signal = max(shared_history_pull, relational_safety)
        if resonance_signal >= 0.58:
            resonance_boost = 0.04 * shared_history_pull + 0.03 * max(0.0, relational_safety - 0.55)
            multiplier += resonance_boost
            if resonance_boost > 0.0:
                reason_tags.append("shared_history_resonance")

    if memory_flags["fragile_memory"]:
        fragile_penalty = 0.08 * protect_bias + 0.03 * memory_flags["distortion_level"]
        multiplier -= fragile_penalty
        if fragile_penalty > 0.0:
            reason_tags.append("fragile_memory_guard")

        if defensive_recoil >= 0.72:
            protective_recoil_penalty = 0.07 * (defensive_recoil - 0.72) + 0.05 * max(withdraw_drive, self_protect_drive)
            multiplier -= protective_recoil_penalty
            if protective_recoil_penalty > 0.0:
                reason_tags.append("protective_recoil_penalty")

    if memory_flags["suppression_flag"]:
        multiplier -= 0.18 * protect_bias + 0.08
        reason_tags.append("suppression_penalty")

    if memory_flags["distortion_level"] > 0.0:
        distortion_penalty = min(0.14, memory_flags["distortion_level"] * (0.05 + 0.07 * protect_bias))
        multiplier -= distortion_penalty
        reason_tags.append("distortion_penalty")

    multiplier = max(0.35, min(1.45, multiplier))
    adjusted_score = round(max(0.0, min(1.0, score * multiplier)), 6)
    boost = round(adjusted_score - score, 6)

    updated = dict(candidate)
    updated_metadata = dict(metadata)
    updated_metadata["persona_recall_profile"] = {
        "mood_state": str(persona_profile.get("mood_state") or ""),
        "bond_stage": str(persona_profile.get("bond_stage") or ""),
        "bond_depth": round(bond_depth, 4),
        "dominant_motive": str(persona_profile.get("dominant_motive") or ""),
        "reach_out_drive": round(reach_out_drive, 4),
        "care_drive": round(care_drive, 4),
        "continuity_drive": round(continuity_drive, 4),
        "withdraw_drive": round(withdraw_drive, 4),
        "self_protect_drive": round(self_protect_drive, 4),
        "protect_bias": round(protect_bias, 4),
        "continuity_bias": round(continuity_bias, 4),
        "warm_bias": round(warm_bias, 4),
        "shared_history_pull": round(shared_history_pull, 4),
        "defensive_recoil": round(defensive_recoil, 4),
    }
    updated_metadata["persona_recall_multiplier"] = round(multiplier, 6)
    updated_metadata["persona_recall_boost"] = boost
    updated_metadata["persona_recall_reason_tags"] = sorted(set(reason_tags))
    score_boost = _coerce_mapping(updated_metadata.get("score_boost"))
    score_boost["persona_recall_boost"] = boost
    updated_metadata["score_boost"] = score_boost
    updated["score"] = adjusted_score
    updated["confidence"] = adjusted_score
    updated["metadata"] = updated_metadata
    return updated


def _filter_recall_routes(
    *,
    routes: list[RecallRouteConfig],
    target_store_ids: list[str] | None,
) -> list[RecallRouteConfig]:
    normalized_target_ids = {
        str(store_id or "").strip().lower()
        for store_id in list(target_store_ids or [])
        if str(store_id or "").strip()
    }
    if not normalized_target_ids:
        return list(routes)
    return [
        route
        for route in routes
        if str(route.store_id or "").strip().lower() in normalized_target_ids
    ]


def _extract_candidate_ts_ms(candidate: Mapping[str, Any]) -> int | None:
    candidate_ts_keys = (
        "ts_ms",
        "created_ts_ms",
        "updated_ts_ms",
        "memory_ts_ms",
        "event_ts_ms",
    )
    for scope in (candidate, candidate.get("metadata") or {}):
        if not isinstance(scope, Mapping):
            continue
        for key in candidate_ts_keys:
            value = scope.get(key)
            if value in (None, ""):
                continue
            try:
                return int(value)
            except (TypeError, ValueError):
                continue
    return None


def _candidate_within_lookback(
    candidate: Mapping[str, Any],
    *,
    now_ts_ms: int,
    lookback_days: int,
) -> bool:
    if lookback_days <= 0:
        return True
    candidate_ts_ms = _extract_candidate_ts_ms(candidate)
    if candidate_ts_ms is None:
        return True
    return candidate_ts_ms >= (int(now_ts_ms) - int(lookback_days) * 86_400_000)


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
