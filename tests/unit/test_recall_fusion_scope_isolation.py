from __future__ import annotations

import json

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.retrieval.recall_fusion import (
    STORE_EPISODIC_VECTOR,
    STORE_SQLITE_LEDGER,
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


def test_current_ingress_intent_is_not_recalled_as_its_own_memory() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        for request_id, intent_id, raw_text in (
            ("req-previous", "intent::req-previous", "共同关键词 之前发生的事"),
            ("req-current", "intent::req-current", "共同关键词 当前用户输入"),
        ):
            ledger.log_intent(
                {
                    "request_id": request_id,
                    "intent_id": intent_id,
                    "session_id": "session-a",
                    "raw_text": raw_text,
                    "normalized_json": {"person_id": "subject-a"},
                    "ts_ms": 1_800_500_000_000,
                }
            )

        config_result = load_recall_runtime_config(
            env={
                "RETR_RECALL_ENABLED": "true",
                "RETR_RECALL_DEFAULT_ROUTE": "sqlite_route",
                "RETR_RECALL_ROUTES_JSON": json.dumps(
                    {
                        "sqlite_route": {
                            "store_id": STORE_SQLITE_LEDGER,
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
        service = RecallFusionServiceContract(
            ledger=ledger,
            chroma_client=FakeChromaClient(),
            runtime_config=config_result.config,
            node_id="host-01",
            use_hybrid=False,
        )

        result = service.recall(
            request_id="req-current",
            intent_id="intent::req-current",
            query="共同关键词",
            query_embedding=[0.2] * 16,
            person_id="subject-a",
            session_id="session-a",
            ts_ms=1_800_500_000_100,
        )

        assert result.success
        assert [row["doc_id"] for row in result.output["candidates"]] == [
            "intent::req-previous"
        ]
        assert result.output["self_excluded_candidate_count"] == 1
        assert result.output["route_rows"][0]["self_excluded_candidate_count"] == 1
    finally:
        ledger.close()
