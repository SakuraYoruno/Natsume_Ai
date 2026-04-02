from __future__ import annotations

from host_brain.retrieval.orchestrator import build_summary_from_pipeline_result
from host_brain.retrieval.pipeline import RetrievalPipelineResult


def test_build_summary_from_pipeline_result_exposes_embedding_and_rerank_runtime_fields() -> None:
    result = RetrievalPipelineResult(
        success=True,
        status="OK",
        reason_code=None,
        stage="S_RET_PIPELINE",
        detail="ok",
        output={
            "degraded": False,
            "reason_chain": [],
            "query_plan": {},
            "agentic_meta": {},
            "adaptive_budget": {},
            "recall": {"routes": [], "route_rows": [], "route_errors": [], "candidate_count": 2},
            "rerank": {
                "topk": [{"doc_id": "doc-1"}],
                "rerank_model_id": "models/Qwen3-Reranker-0.6B",
                "provider": "qwen3_reranker",
                "effective_provider": "fast_path_heuristic",
                "fallback_used": True,
                "fallback_reason_code": "RETR_RERANK_UNAVAILABLE",
            },
            "evidence_pack": {"evidence_refs": [], "topk": []},
            "embedding": {
                "embedding_model_id": "models/Qwen3-Embedding-0.6B",
                "provider": "sentence_transformers",
                "effective_provider": "hash-embed-v1",
                "fallback_used": True,
                "fallback_reason_code": "RETR_EMBED_UNAVAILABLE",
            },
        },
    )

    summary, _items, _path = build_summary_from_pipeline_result(result)

    assert summary["embedding_model_id"] == "models/Qwen3-Embedding-0.6B"
    assert summary["effective_embedding_provider"] == "hash-embed-v1"
    assert summary["fallback_embedding_used"] is True
    assert summary["rerank_model_id"] == "models/Qwen3-Reranker-0.6B"
    assert summary["effective_rerank_provider"] == "fast_path_heuristic"
    assert summary["fallback_reranker_used"] is True
