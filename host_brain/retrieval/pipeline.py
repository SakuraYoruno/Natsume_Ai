"""End-to-end retrieval pipeline contract: embed -> multi-recall -> rerank -> evidence pack."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.ids import now_ms
from host_brain.memory.chroma_client import EMBEDDING_MODEL_ID, embed_text
from host_brain.retrieval.embedding import EmbeddingServiceContract
from host_brain.retrieval.evidence_pack import EvidencePackServiceContract
from host_brain.retrieval.recall_fusion import RecallFusionServiceContract
from host_brain.retrieval.reranker import RerankerServiceContract


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
        fallback_embedding_used = False
        if not embedding_vector and normalized_query:
            embedding_vector, _ = embed_text(normalized_query)
            fallback_embedding_used = True

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
            "fallback_embedding_used": fallback_embedding_used,
            "fallback_embedding_model_id": (EMBEDDING_MODEL_ID if fallback_embedding_used else None),
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
                "fallback_embedding_used": fallback_embedding_used,
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
