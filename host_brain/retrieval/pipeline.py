"""End-to-end retrieval pipeline contract: embed -> multi-recall -> rerank -> evidence pack."""

from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Any, Mapping, Sequence
from urllib.parse import urlparse

from host_brain.app.config import load_host_config
from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.bond.neo4j_client import build_neo4j_bond_client, load_neo4j_runtime_config
from host_brain.core.ids import now_ms
from host_brain.lsp.vault import ArtifactVault
from host_brain.memory.chroma_client import ChromaRuntimeConfig, load_chroma_runtime_config
from host_brain.memory.canon_style_store import StyleCanonStore
from host_brain.memory.canon_world_store import WorldCanonStore
from host_brain.retrieval.embedding import (
    EmbeddingServiceContract,
    build_embedding_provider_registry,
    load_embedding_runtime_config,
)
from host_brain.retrieval.audit_preview import build_retrieval_refs_preview
from host_brain.retrieval.adaptive_prompt_budget import AdaptivePromptBudget
from host_brain.retrieval.agentic_retrieval_loop import AgenticRetrievalLoop
from host_brain.retrieval.evidence_pack import EvidencePackServiceContract
from host_brain.retrieval.query_planner import QueryPlan, QueryPlanner
from host_brain.retrieval.recall_fusion import (
    RecallFusionServiceContract,
    load_recall_runtime_config,
)
from host_brain.retrieval.reranker import (
    RerankerServiceContract,
    build_reranker_provider_registry,
    load_reranker_runtime_config,
    resolve_reranker_route,
)
from host_brain.retrieval.reranker_schemas import RerankerResult
from host_brain.retrieval.semantic_dedup import SemanticDedup


S_RET_PIPELINE = "S_RET_PIPELINE"
RET_OP_PIPELINE = "PIPELINE"
S_RET_RERANK = "S_RET_RERANK"
RET_OP_RERANK = "RERANK"

STATUS_OK = "OK"
STATUS_DEGRADED = "DEGRADED"

_FAST_PATH_RERANK_INTENTS = frozenset({"phatic", "affection"})


@dataclass(frozen=True)
class RetrievalPipelineResult:
    success: bool
    status: str
    reason_code: str | None
    stage: str
    detail: str
    output: dict[str, Any]


class ChromaHttpSearchClient:
    """Minimal Chroma HTTP client adapter exposing `search` for recall fusion."""

    def __init__(self, *, endpoint: str, default_collection: str) -> None:
        parsed = urlparse(str(endpoint).strip() or "http://127.0.0.1:8000")
        self._host = parsed.hostname or "127.0.0.1"
        self._port = int(parsed.port or 8000)
        self._ssl = (parsed.scheme.lower() == "https")
        self._default_collection = str(default_collection or "episodic")
        try:
            import chromadb
        except Exception as exc:  # pragma: no cover - runtime dependency
            raise RuntimeError(f"chromadb package unavailable: {exc}") from exc
        self._client = chromadb.HttpClient(host=self._host, port=self._port, ssl=self._ssl)

    @classmethod
    def from_runtime_config(cls, config: ChromaRuntimeConfig) -> "ChromaHttpSearchClient":
        return cls(endpoint=config.endpoint, default_collection=config.default_collection)

    def search(
        self,
        *,
        query_embedding: list[float],
        top_k: int = 8,
        collection: str | None = None,
    ) -> list[dict[str, Any]]:
        collection_name = str(collection or self._default_collection)
        coll = self._client.get_or_create_collection(name=collection_name)
        result = coll.query(
            query_embeddings=[list(query_embedding)],
            n_results=max(1, int(top_k)),
            include=["documents", "metadatas", "distances"],
        )

        ids = ((result.get("ids") or [[]])[0])
        docs = ((result.get("documents") or [[]])[0])
        metas = ((result.get("metadatas") or [[]])[0])
        distances = ((result.get("distances") or [[]])[0])

        rows: list[dict[str, Any]] = []
        for idx, memory_id in enumerate(ids):
            text = str(docs[idx]) if idx < len(docs) and docs[idx] is not None else ""
            metadata = dict(metas[idx] or {}) if idx < len(metas) and isinstance(metas[idx], Mapping) else {}
            distance = 1.0
            if idx < len(distances) and distances[idx] is not None:
                try:
                    distance = float(distances[idx])
                except (TypeError, ValueError):
                    distance = 1.0
            score = _distance_to_score(distance)
            rows.append(
                {
                    "memory_id": str(memory_id),
                    "text": text,
                    "score": score,
                    "metadata": metadata,
                    "collection": collection_name,
                }
            )
        return rows


class RetrievalPipelineContract:
    """Single retrieval chain outputting stable evidence pack and stage traces."""

    def __init__(
        self,
        *,
        embedding_service: EmbeddingServiceContract,
        recall_service: RecallFusionServiceContract,
        reranker_service: RerankerServiceContract,
        evidence_pack_service: EvidencePackServiceContract,
        ledger: SQLiteLedger | None = None,
        query_planner: QueryPlanner | None = None,
        adaptive_budget: AdaptivePromptBudget | None = None,
        semantic_dedup: SemanticDedup | None = None,
    ) -> None:
        self.embedding_service = embedding_service
        self.recall_service = recall_service
        self.reranker_service = reranker_service
        self.evidence_pack_service = evidence_pack_service
        self.ledger = ledger
        self.query_planner = query_planner or QueryPlanner()
        self.adaptive_budget = adaptive_budget or AdaptivePromptBudget()
        self.semantic_dedup = semantic_dedup or SemanticDedup()

    def run(
        self,
        *,
        request_id: str,
        query: str,
        intent_id: str | None = None,
        person_id: str | None = None,
        session_id: str | None = None,
        top_k: int | None = None,
        ts_ms: int | None = None,
        # M28-P（OPT-41）：情感上下文透传参数
        emotion_context: str | None = None,
        render_target_lang: str | None = None,
        turn_index: int = 0,
        query_plan: QueryPlan | Mapping[str, Any] | None = None,
        route_target: str = "",
    ) -> RetrievalPipelineResult:
        now_value = int(ts_ms if ts_ms is not None else now_ms())
        normalized_query = str(query or "").strip()
        resolved_query_plan = _normalize_query_plan(
            query_plan=query_plan,
            query=normalized_query,
            turn_index=turn_index,
            planner=self.query_planner,
        )

        embedding_result = self.embedding_service.embed(
            request_id=request_id,
            text=normalized_query,
            intent_id=intent_id,
            ts_ms=now_value,
        )

        embedding_vector = list(embedding_result.output.get("embedding_vector") or [])
        agentic_loop = AgenticRetrievalLoop(
            recall_fusion=self.recall_service,
            planner=self.query_planner,
            embedding_service=self.embedding_service,
        )
        agentic_result = agentic_loop.retrieve(
            query=normalized_query,
            query_embedding=embedding_vector,
            request_id=request_id,
            person_id=person_id,
            session_id=session_id,
            turn_index=turn_index,
            intent_id=intent_id,
            emotion_filter=emotion_context,
            render_target_lang=render_target_lang,
            ts_ms=now_value,
            plan=resolved_query_plan,
        )
        recall_output = dict(agentic_result.recall_summary or {})
        recall_candidates = list(recall_output.get("candidates") or agentic_result.evidence_items or [])
        dedup_result = self.semantic_dedup.dedup(recall_candidates)
        dedup_candidates = list(dedup_result.candidates)
        if _should_use_fast_path_rerank(
            intent=resolved_query_plan.intent,
            candidates=dedup_candidates,
        ):
            rerank_result = _build_fast_path_rerank_result(
                reranker_service=self.reranker_service,
                request_id=request_id,
                query=normalized_query,
                candidates=dedup_candidates,
                intent_id=intent_id,
                top_k=top_k,
                ts_ms=now_value,
            )
            self._log_fast_path_rerank(
                request_id=request_id,
                intent_id=intent_id,
                rerank_result=rerank_result,
                ts_ms=now_value,
            )
        else:
            rerank_result = self.reranker_service.rerank(
                request_id=request_id,
                query=normalized_query,
                candidates=dedup_candidates,
                intent_id=intent_id,
                top_k=top_k,
                ts_ms=now_value,
                # M28-P（OPT-41）：透传情感上下文给 reranker
                emotion_context=emotion_context,
            )
        adaptive_budget = self.adaptive_budget.compute(
            resolved_query_plan.intent,
            route_target=route_target,
        )

        stage_reasons = [
            reason
            for reason in (
                embedding_result.reason_code,
                recall_output.get("reason_code"),
                rerank_result.reason_code,
            )
            if reason is not None
        ]
        degraded = len(stage_reasons) > 0
        root_reason = stage_reasons[0] if stage_reasons else None

        evidence_pack_result = self.evidence_pack_service.build_pack(
            request_id=request_id,
            intent_id=intent_id,
            query=normalized_query,
            rerank_output=rerank_result.output,
            degraded=degraded,
            reason_code=root_reason,
            ts_ms=now_value,
        )

        reason_chain = list(stage_reasons)
        if evidence_pack_result.reason_code and evidence_pack_result.reason_code not in reason_chain:
            reason_chain.append(evidence_pack_result.reason_code)

        try:
            from host_brain.app.dependencies import get_meta_learning_loop  # noqa: PLC0415

            meta_learning = get_meta_learning_loop()
            meta_learning.record(
                "query_planner",
                success=not resolved_query_plan.skip_retrieval,
                skipped=resolved_query_plan.skip_retrieval,
                latency_ms=0.0,
                custom={"skip_rate": 1.0 if resolved_query_plan.skip_retrieval else 0.0},
            )
            meta_learning.record(
                "agentic_retrieval_loop",
                success=not bool(recall_output.get("reason_code")),
                latency_ms=float(agentic_result.elapsed_ms),
                custom={"rounds": float(agentic_result.rounds)},
            )
            meta_learning.record(
                "semantic_dedup",
                success=True,
                latency_ms=0.0,
                custom={
                    "dedup_rate": (
                        float(dedup_result.removed_count) / float(max(1, dedup_result.original_count))
                    ),
                },
            )
            meta_learning.record(
                "adaptive_prompt_budget",
                success=True,
                latency_ms=0.0,
                custom={"adjusted_slots": float(len(adaptive_budget.adjustments_applied))},
            )
        except Exception:
            pass

        status = STATUS_OK if (not degraded and evidence_pack_result.success) else STATUS_DEGRADED
        reason_code = evidence_pack_result.reason_code or root_reason
        output = {
            "request_id": request_id,
            "intent_id": intent_id,
            "query": normalized_query,
            "status": status,
            "stage": S_RET_PIPELINE,
            "reason_code": reason_code,
            "reason_chain": reason_chain,
            "degraded": (status == STATUS_DEGRADED),
            "fallback_embedding_used": False,
            "fallback_embedding_model_id": None,
            "embedding": embedding_result.output,
            "query_plan": resolved_query_plan.as_dict(),
            "agentic_meta": {
                **agentic_result.as_dict(),
                "turn_index": int(turn_index),
                "dedup": dedup_result.as_dict(),
            },
            "adaptive_budget": adaptive_budget.as_dict(),
            "recall": {
                **recall_output,
                "candidate_count": len(dedup_candidates),
                "candidates": dedup_candidates,
                "dedup_removed_count": int(dedup_result.removed_count),
                "dedup_merge_log_count": len(dedup_result.merge_log),
            },
            "rerank": rerank_result.output,
            "evidence_pack": evidence_pack_result.output,
            "ts_ms": now_value,
        }

        detail = "retrieval pipeline completed"
        if status == STATUS_DEGRADED:
            detail = "retrieval pipeline completed with degradation"

        self._log_event(
            request_id=request_id,
            intent_id=intent_id,
            status=status,
            reason_code=reason_code,
            detail=detail,
            payload={
                "query_length": len(normalized_query),
                "fallback_embedding_used": False,
                "recall_candidates": len(recall_candidates),
                "recall_candidates_deduped": len(dedup_candidates),
                "rerank_topk": len(rerank_result.output.get("topk") or []),
                "evidence_refs": len(evidence_pack_result.output.get("evidence_refs") or []),
                "selected_refs": build_retrieval_refs_preview(evidence_pack_result.output.get("evidence_refs") or []),
                "query_plan": resolved_query_plan.as_dict(),
                "agentic_meta": {
                    **agentic_result.as_dict(),
                    "turn_index": int(turn_index),
                    "dedup": dedup_result.as_dict(),
                },
                "adaptive_budget": adaptive_budget.as_dict(),
                "output_status": status,
            },
            ts_ms=now_value,
        )
        return RetrievalPipelineResult(
            success=(status == STATUS_OK),
            status=status,
            reason_code=reason_code,
            stage=S_RET_PIPELINE,
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
            self.ledger.log_retrieval_event(
                {
                    "request_id": request_id,
                    "intent_id": intent_id,
                    "op_kind": RET_OP_PIPELINE,
                    "status": status,
                    "reason_code": reason_code,
                    "stage": S_RET_PIPELINE,
                    "payload": payload,
                    "detail": detail,
                    "ts_ms": ts_ms,
                }
            )
            return

        if self.ledger is not None and hasattr(self.ledger, "log_error") and reason_code is not None:
            self.ledger.log_error(
                request_id=request_id,
                stage=S_RET_PIPELINE,
                reason_code=reason_code,
                detail=detail,
            )

    def _log_fast_path_rerank(
        self,
        *,
        request_id: str,
        intent_id: str | None,
        rerank_result: RerankerResult,
        ts_ms: int,
    ) -> None:
        if self.ledger is None or not hasattr(self.ledger, "log_retrieval_event"):
            return
        self.ledger.log_retrieval_event(
            {
                "request_id": request_id,
                "intent_id": intent_id,
                "op_kind": RET_OP_RERANK,
                "status": rerank_result.status,
                "reason_code": rerank_result.reason_code,
                "stage": S_RET_RERANK,
                "payload": dict(rerank_result.output or {}),
                "detail": str(rerank_result.detail or "rerank completed via fast-path heuristic"),
                "ts_ms": ts_ms,
            }
        )


def _should_use_fast_path_rerank(*, intent: str, candidates: list[dict[str, Any]]) -> bool:
    return str(intent or "").strip().lower() in _FAST_PATH_RERANK_INTENTS and len(candidates) <= 8


def _build_fast_path_rerank_result(
    *,
    reranker_service: RerankerServiceContract,
    request_id: str,
    query: str,
    candidates: list[dict[str, Any]],
    intent_id: str | None,
    top_k: int | None,
    ts_ms: int,
) -> RerankerResult:
    route = resolve_reranker_route(reranker_service.runtime_config, node_id=reranker_service.node_id)
    normalized_candidates = [dict(candidate) for candidate in list(candidates or []) if isinstance(candidate, Mapping)]
    requested_top_k = route.top_k if top_k is None else int(top_k)
    topk_value = max(1, min(requested_top_k, len(normalized_candidates)))
    scored_candidates: list[dict[str, Any]] = []
    for ordinal, candidate in enumerate(normalized_candidates):
        row = dict(candidate)
        score = _score_fast_path_candidate(row=row, ordinal=ordinal)
        row["score"] = score
        row["confidence"] = score
        scored_candidates.append(row)
    scored_candidates.sort(
        key=lambda item: (
            -float(item.get("score") or 0.0),
            str(item.get("source_ref") or item.get("doc_id") or ""),
        )
    )
    topk = scored_candidates[:topk_value]
    output = {
        "candidate_count": len(normalized_candidates),
        "effective_provider": "fast_path_heuristic",
        "expected_top_k": topk_value,
        "fallback_reason_code": "FAST_PATH_HEURISTIC",
        "fallback_used": True,
        "latency_ms": 0,
        "node_selector": route.node_selector,
        "output_status": STATUS_OK,
        "provider": route.provider,
        "query_length": len(str(query or "")),
        "route_id": route.route_id,
        "topk": topk,
        "topk_count": len(topk),
        "warm_retry_used": False,
        "rerank_model_id": route.model_id,
        "request_id": request_id,
        "intent_id": intent_id,
        "ts_ms": ts_ms,
    }
    return RerankerResult(
        success=True,
        status=STATUS_OK,
        reason_code=None,
        stage=S_RET_RERANK,
        detail="rerank completed via fast-path heuristic",
        output=output,
    )


def _score_fast_path_candidate(*, row: Mapping[str, Any], ordinal: int) -> float:
    metadata_raw = row.get("metadata")
    metadata = dict(metadata_raw) if isinstance(metadata_raw, Mapping) else {}
    base_score = row.get("confidence")
    if base_score in (None, ""):
        base_score = row.get("score")
    try:
        score = float(base_score or 0.0)
    except (TypeError, ValueError):
        score = 0.0
    store_id = str(metadata.get("store_id") or row.get("store_id") or "").strip().lower()
    # Phase XIII: 只有 5 个有效 store（style_canon/persona_utterance 已废弃）
    if store_id == "neo4j_bond":
        score += 0.08
    elif store_id == "episodic_vector":
        score += 0.03
    score -= min(ordinal, 5) * 0.001
    return round(score, 6)


def _normalize_query_plan(
    *,
    query_plan: QueryPlan | Mapping[str, Any] | None,
    query: str,
    turn_index: int,
    planner: QueryPlanner,
) -> QueryPlan:
    if isinstance(query_plan, QueryPlan):
        return query_plan
    if isinstance(query_plan, Mapping):
        data = dict(query_plan)
        return QueryPlan(
            original_query=str(data.get("original_query") or query),
            rewritten_queries=[
                str(item).strip()
                for item in list(data.get("rewritten_queries") or [])
                if str(item).strip()
            ],
            intent=str(data.get("intent") or planner.plan(query, turn_index=turn_index).intent),
            target_stores=[
                str(item).strip()
                for item in list(data.get("target_stores") or [])
                if str(item).strip()
            ],
            skip_retrieval=bool(data.get("skip_retrieval")),
            max_rounds=max(0, int(data.get("max_rounds") or 1)),
            time_lookback_days=(
                int(data["time_lookback_days"])
                if data.get("time_lookback_days") not in (None, "")
                else None
            ),
        )
    return planner.plan(query, turn_index=turn_index)


def build_retrieval_pipeline_from_env(
    *,
    env: Mapping[str, Any] | None = None,
    local_overrides: Mapping[str, Any] | None = None,
    ledger: SQLiteLedger | None = None,
    node_id: str | None = None,
    key_profiles: Sequence[Mapping[str, Any]] | None = None,
    secret_store: Mapping[str, Any] | None = None,
    current_epoch: int | None = None,
) -> RetrievalPipelineContract:
    merged: dict[str, Any] = dict(env or os.environ)
    if local_overrides:
        merged.update(dict(local_overrides))

    embedding_cfg_result = load_embedding_runtime_config(env=merged)
    if not embedding_cfg_result.success or embedding_cfg_result.config is None:
        raise RuntimeError(f"embedding runtime config invalid: {embedding_cfg_result.as_dict()}")

    recall_cfg_result = load_recall_runtime_config(env=merged)
    if not recall_cfg_result.success or recall_cfg_result.config is None:
        raise RuntimeError(f"recall runtime config invalid: {recall_cfg_result.as_dict()}")

    rerank_cfg_result = load_reranker_runtime_config(env=merged)
    if not rerank_cfg_result.success or rerank_cfg_result.config is None:
        raise RuntimeError(f"reranker runtime config invalid: {rerank_cfg_result.as_dict()}")

    chroma_cfg_result = load_chroma_runtime_config(env=merged)
    if not chroma_cfg_result.success or chroma_cfg_result.config is None:
        raise RuntimeError(f"chroma runtime config invalid: {chroma_cfg_result.as_dict()}")

    host_cfg_result = load_host_config(
        env=merged,
        key_profiles=key_profiles,
        secret_store=secret_store,
        node_id=(str(node_id or "host.default") if node_id else "host.default"),
        current_epoch=current_epoch,
    )
    resolved_openai_key = ""
    if host_cfg_result.success and host_cfg_result.config is not None:
        resolved_openai_key = str(host_cfg_result.config.openai_api_key or "").strip()
    if not resolved_openai_key:
        resolved_openai_key = str(merged.get("OPENAI_API_KEY") or "").strip()

    embedding_registry = build_embedding_provider_registry(
        env=merged,
        openai_api_key=resolved_openai_key,
    )
    reranker_registry = build_reranker_provider_registry(env=merged)
    neo4j_cfg_result = load_neo4j_runtime_config(env=merged)
    neo4j_client = None
    if (
        neo4j_cfg_result.success
        and neo4j_cfg_result.config is not None
        and neo4j_cfg_result.config.enabled
    ):
        try:
            neo4j_client = build_neo4j_bond_client(
                config=neo4j_cfg_result.config,
                secret_store=secret_store,
            )
        except Exception:
            neo4j_client = None

    chroma_client = ChromaHttpSearchClient.from_runtime_config(chroma_cfg_result.config)
    style_root = str(merged.get("Natsume_STYLE_CANON_ROOT") or "host_brain/canon/style").strip()
    world_root = str(merged.get("Natsume_WORLD_CANON_ROOT") or "host_brain/canon/world").strip()
    vault_root = str(merged.get("Natsume_VAULT_ROOT") or "host_brain/data/vault").strip()

    style_store = StyleCanonStore(style_root=style_root, ledger=ledger)
    world_store = WorldCanonStore(world_root=world_root, ledger=ledger)
    artifact_vault = ArtifactVault(root_dir=vault_root, ledger=ledger)

    embedding_service = EmbeddingServiceContract(
        ledger=ledger,
        runtime_config=embedding_cfg_result.config,
        provider_registry=embedding_registry,
        node_id=node_id,
        available=True,
    )
    recall_service = RecallFusionServiceContract(
        ledger=ledger,
        chroma_client=chroma_client,
        neo4j_client=neo4j_client,
        style_store=style_store,
        world_store=world_store,
        artifact_vault=artifact_vault,
        runtime_config=recall_cfg_result.config,
        node_id=node_id,
        available=True,
    )
    reranker_service = RerankerServiceContract(
        ledger=ledger,
        runtime_config=rerank_cfg_result.config,
        provider_registry=reranker_registry,
        node_id=node_id,
        available=True,
    )
    evidence_pack_service = EvidencePackServiceContract(ledger=ledger)

    return RetrievalPipelineContract(
        embedding_service=embedding_service,
        recall_service=recall_service,
        reranker_service=reranker_service,
        evidence_pack_service=evidence_pack_service,
        ledger=ledger,
        query_planner=QueryPlanner(),
        adaptive_budget=AdaptivePromptBudget(),
        semantic_dedup=SemanticDedup(),
    )


def _distance_to_score(distance: float) -> float:
    value = max(0.0, float(distance))
    return round(1.0 / (1.0 + value), 6)
