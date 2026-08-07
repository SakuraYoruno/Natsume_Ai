from __future__ import annotations

import json

from host_brain.retrieval.recall_fusion import (
    STORE_EPISODIC_VECTOR,
    RecallFusionServiceContract,
    _candidate_matches_recall_scope,
    load_recall_runtime_config,
)
from tests.fakes.chroma_client import FakeChromaClient


def _service(*, chroma: FakeChromaClient, use_hybrid: bool = False) -> RecallFusionServiceContract:
    config_result = load_recall_runtime_config(
        env={
            "RETR_RECALL_ENABLED": "true",
            "RETR_RECALL_DEFAULT_ROUTE": "episodic_route",
            "RETR_RECALL_ROUTES_JSON": json.dumps(
                {
                    "episodic_route": {
                        "store_id": STORE_EPISODIC_VECTOR,
                        "collection": "episodic",
                        "top_k": 8,
                        "score_weight": 1.0,
                        "node_selector": "*",
                    }
                }
            ),
        }
    )
    assert config_result.success
    assert config_result.config is not None
    return RecallFusionServiceContract(
        chroma_client=chroma,
        runtime_config=config_result.config,
        node_id="host-01",
        use_hybrid=use_hybrid,
    )


def _upsert(
    chroma: FakeChromaClient,
    memory_id: str,
    *,
    metadata: dict[str, object],
) -> None:
    chroma.upsert(
        memory_id=memory_id,
        text=f"共同关键词 {memory_id}",
        embedding=[0.2] * 16,
        collection="episodic",
        metadata=metadata,
    )


def test_recall_excludes_other_people_and_unscoped_personal_rows() -> None:
    chroma = FakeChromaClient()
    _upsert(chroma, "same_person", metadata={"person_id": "subject-a"})
    _upsert(chroma, "other_person", metadata={"person_id": "subject-b"})
    _upsert(chroma, "same_session", metadata={"session_id": "session-a"})
    _upsert(chroma, "other_session", metadata={"session_id": "session-b"})
    _upsert(chroma, "unscoped_personal", metadata={})
    _upsert(
        chroma,
        "mixed_people",
        metadata={"semantic_person_ids": ["subject-a", "subject-b"]},
    )
    _upsert(chroma, "project_fact", metadata={"scope_type": "project"})

    result = _service(chroma=chroma).recall(
        request_id="req-scope-a",
        query="共同关键词",
        query_embedding=[0.2] * 16,
        person_id="subject-a",
        session_id="session-a",
    )

    assert result.success
    assert {row["doc_id"] for row in result.output["candidates"]} == {
        "same_person",
        "same_session",
        "project_fact",
    }
    assert result.output["scope_excluded_candidate_count"] == 4
    route_row = result.output["route_rows"][0]
    assert route_row["raw_candidate_count"] == 7
    assert route_row["candidate_count"] == 3
    assert route_row["scope_excluded_candidate_count"] == 4


def test_recall_without_target_scope_preserves_legacy_behavior() -> None:
    chroma = FakeChromaClient()
    _upsert(chroma, "subject_a", metadata={"person_id": "subject-a"})
    _upsert(chroma, "subject_b", metadata={"person_id": "subject-b"})
    _upsert(chroma, "unscoped", metadata={})

    result = _service(chroma=chroma).recall(
        request_id="req-unscoped",
        query="共同关键词",
        query_embedding=[0.2] * 16,
    )

    assert result.success
    assert {row["doc_id"] for row in result.output["candidates"]} == {
        "subject_a",
        "subject_b",
        "unscoped",
    }
    assert result.output["scope_excluded_candidate_count"] == 0


def test_hybrid_fts_cannot_reintroduce_an_unscoped_personal_row() -> None:
    class _FTSSearcher:
        def search(self, _query: str, *, top_k: int) -> list[dict[str, object]]:
            assert top_k > 0
            return [
                {
                    "doc_id": "fts_only_other_person",
                    "snippet": "共同关键词 来自未限定全文索引",
                    "fts_score": 1.0,
                }
            ]

    chroma = FakeChromaClient()
    _upsert(chroma, "same_person", metadata={"person_id": "subject-a"})
    service = _service(chroma=chroma, use_hybrid=True)
    service._fts_searcher = _FTSSearcher()

    result = service.recall(
        request_id="req-fts-scope",
        query="共同关键词",
        query_embedding=[0.2] * 16,
        person_id="subject-a",
        session_id="session-a",
    )

    assert result.success
    assert [row["doc_id"] for row in result.output["candidates"]] == ["same_person"]
    assert result.output["scope_excluded_candidate_count"] == 1


def test_explicit_non_personal_stores_remain_recallable_in_person_scope() -> None:
    assert _candidate_matches_recall_scope(
        {"doc_id": "canon", "metadata": {"store_id": "world_canon"}},
        person_id="subject-a",
        session_id="session-a",
    )
    assert _candidate_matches_recall_scope(
        {"doc_id": "artifact", "metadata": {"store_id": "artifact_vault"}},
        person_id="subject-a",
        session_id="session-a",
    )
