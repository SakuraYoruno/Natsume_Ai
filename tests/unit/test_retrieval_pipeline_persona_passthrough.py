from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from host_brain.retrieval.agentic_retrieval_loop import AgenticRetrievalResult
from host_brain.retrieval.pipeline import RetrievalPipelineContract
from host_brain.retrieval.query_planner import QueryPlan


def test_pipeline_passes_persona_context_to_agentic_retrieval() -> None:
    captured: dict[str, object] = {}

    mock_embedding = MagicMock()
    mock_embedding.embed.return_value = MagicMock(
        success=True,
        output={"embedding_vector": [0.1, 0.2, 0.3]},
        reason_code=None,
    )

    mock_reranker = MagicMock()
    mock_reranker.rerank.return_value = MagicMock(
        success=True,
        output={"topk": [], "evidence_refs": []},
        reason_code=None,
    )

    mock_evidence_pack = MagicMock()
    mock_evidence_pack.build_pack.return_value = MagicMock(
        success=True,
        output={"topk": [], "evidence_refs": []},
        reason_code=None,
    )

    pipeline = RetrievalPipelineContract(
        embedding_service=mock_embedding,
        recall_service=MagicMock(),
        reranker_service=mock_reranker,
        evidence_pack_service=mock_evidence_pack,
    )
    plan = QueryPlan(
        original_query="测试查询",
        intent="chat",
        target_stores=["episodic_vector"],
        skip_retrieval=False,
        max_rounds=1,
    )
    agentic_result = AgenticRetrievalResult(
        evidence_items=[],
        rounds=1,
        plan=plan,
        elapsed_ms=0.1,
        skipped=False,
        queries_used=["测试查询"],
        recall_summary={
            "status": "OK",
            "reason_code": None,
            "route_errors": [],
            "candidate_count": 0,
            "candidates": [],
        },
    )

    def _agentic_ctor(**_kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(retrieve=lambda **kwargs: captured.update(kwargs) or agentic_result)

    with patch("host_brain.retrieval.pipeline.AgenticRetrievalLoop", side_effect=_agentic_ctor):
        result = pipeline.run(
            request_id="req-persona-pipeline",
            query="测试查询",
            emotion_context="HAPPY",
            mood_state="tired",
            bond_summary={"stage": "FRIEND", "score": 0.66},
            motive_state={
                "dominant_motive": "continuity",
                "drives": {"continuity_drive": 0.82, "care_drive": 0.74},
            },
            query_plan=plan,
        )

    assert result.success is True
    assert captured["emotion_filter"] == "HAPPY"
    assert captured["mood_state"] == "tired"
    assert captured["bond_summary"] == {"stage": "FRIEND", "score": 0.66}
    assert captured["motive_state"] == {
        "dominant_motive": "continuity",
        "drives": {"continuity_drive": 0.82, "care_drive": 0.74},
    }
