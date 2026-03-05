"""End-to-end retrieval pipeline contract: embed -> multi-recall -> rerank -> evidence pack."""

from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Any, Mapping, Sequence
from urllib.parse import urlparse

from host_brain.app.config import load_host_config
from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.ids import now_ms
from host_brain.memory.chroma_client import ChromaRuntimeConfig, load_chroma_runtime_config
from host_brain.retrieval.embedding import (
    EmbeddingServiceContract,
    build_embedding_provider_registry,
    load_embedding_runtime_config,
)
from host_brain.retrieval.evidence_pack import EvidencePackServiceContract
from host_brain.retrieval.recall_fusion import (
    RecallFusionServiceContract,
    load_recall_runtime_config,
)
from host_brain.retrieval.reranker import (
    RerankerServiceContract,
    build_reranker_provider_registry,
    load_reranker_runtime_config,
)


S_RET_PIPELINE = "S_RET_PIPELINE"
RET_OP_PIPELINE = "PIPELINE"

STATUS_OK = "OK"
STATUS_DEGRADED = "DEGRADED"


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
        top_k: int = 5,
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
    ) -> None:
        self.embedding_service = embedding_service
        self.recall_service = recall_service
        self.reranker_service = reranker_service
        self.evidence_pack_service = evidence_pack_service
        self.ledger = ledger

    def run(
        self,
        *,
        request_id: str,
        query: str,
        intent_id: str | None = None,
        top_k: int | None = None,
        ts_ms: int | None = None,
        # M28-P（OPT-41）：情感上下文透传参数
        emotion_context: str | None = None,
    ) -> RetrievalPipelineResult:
        now_value = int(ts_ms if ts_ms is not None else now_ms())
        normalized_query = str(query or "").strip()

        embedding_result = self.embedding_service.embed(
            request_id=request_id,
            text=normalized_query,
            intent_id=intent_id,
            ts_ms=now_value,
        )

        embedding_vector = list(embedding_result.output.get("embedding_vector") or [])

        recall_result = self.recall_service.recall(
            request_id=request_id,
            query=normalized_query,
            query_embedding=embedding_vector,
            intent_id=intent_id,
            ts_ms=now_value,
        )

        recall_candidates = list(recall_result.output.get("candidates") or [])
        rerank_result = self.reranker_service.rerank(
            request_id=request_id,
            query=normalized_query,
            candidates=recall_candidates,
            intent_id=intent_id,
            top_k=top_k,
            ts_ms=now_value,
            # M28-P（OPT-41）：透传情感上下文给 reranker
            emotion_context=emotion_context,
        )

        stage_reasons = [
            reason
            for reason in (
                embedding_result.reason_code,
                recall_result.reason_code,
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
            "recall": recall_result.output,
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
                "rerank_topk": len(rerank_result.output.get("topk") or []),
                "evidence_refs": len(evidence_pack_result.output.get("evidence_refs") or []),
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

    chroma_client = ChromaHttpSearchClient.from_runtime_config(chroma_cfg_result.config)

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
    )


def _distance_to_score(distance: float) -> float:
    value = max(0.0, float(distance))
    return round(1.0 / (1.0 + value), 6)
