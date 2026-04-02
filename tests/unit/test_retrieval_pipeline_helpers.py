from __future__ import annotations

import sys
import types
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from host_brain.retrieval.pipeline import (
    ChromaHttpSearchClient,
    RetrievalPipelineContract,
    STATUS_OK,
    S_RET_PIPELINE,
    S_RET_RERANK,
    _normalize_query_plan,
    _score_fast_path_candidate,
    build_retrieval_pipeline_from_env,
)
from host_brain.retrieval.query_planner import QueryPlan
from host_brain.retrieval.reranker_schemas import RerankerResult


class _ConfigResult:
    def __init__(self, *, success: bool, config: object | None, label: str) -> None:
        self.success = success
        self.config = config
        self.label = label

    def as_dict(self) -> dict[str, object]:
        return {
            "success": self.success,
            "config": self.label if self.config is not None else None,
        }


class _PlannerStub:
    def __init__(self, intent: str = "chat") -> None:
        self.intent = intent
        self.calls: list[tuple[str, int]] = []

    def plan(self, query: str, *, turn_index: int = 0) -> QueryPlan:
        self.calls.append((query, turn_index))
        return QueryPlan(
            original_query=query,
            rewritten_queries=["rewrite"],
            intent=self.intent,
            target_stores=["episodic_vector"],
            skip_retrieval=False,
            max_rounds=2,
            time_lookback_days=3,
        )


class TestChromaHttpSearchClient(unittest.TestCase):
    def test_search_maps_chroma_rows_and_invalid_distance(self) -> None:
        class _FakeCollection:
            def query(self, **_: object) -> dict[str, object]:
                return {
                    "ids": [["mem-1", "mem-2"]],
                    "documents": [["alpha", None]],
                    "metadatas": [[{"store_id": "episodic_vector"}, "bad-meta"]],
                    "distances": [["0.25", "oops"]],
                }

        fake_client = SimpleNamespace(get_or_create_collection=lambda name: _FakeCollection())
        fake_chromadb = types.SimpleNamespace(HttpClient=lambda host, port, ssl: fake_client)

        with patch.dict(sys.modules, {"chromadb": fake_chromadb}):
            client = ChromaHttpSearchClient(
                endpoint="https://edge.example:8443",
                default_collection="episodic",
            )
            result = client.search(
                query_embedding=[0.1, 0.2, 0.3],
                top_k=2,
                collection="canon",
            )

        self.assertEqual(2, len(result))
        self.assertEqual("mem-1", result[0]["memory_id"])
        self.assertEqual("alpha", result[0]["text"])
        self.assertEqual({"store_id": "episodic_vector"}, result[0]["metadata"])
        self.assertEqual(round(1.0 / 1.25, 6), result[0]["score"])
        self.assertEqual("", result[1]["text"])
        self.assertEqual({}, result[1]["metadata"])
        self.assertEqual(0.5, result[1]["score"])
        self.assertEqual("canon", result[1]["collection"])


class TestPipelineHelpers(unittest.TestCase):
    def test_score_fast_path_candidate_uses_score_fallback_bonus_and_parse_guard(self) -> None:
        invalid_score = _score_fast_path_candidate(
            row={"score": "bad", "metadata": {"store_id": "neo4j_bond"}},
            ordinal=3,
        )
        episodic_score = _score_fast_path_candidate(
            row={"confidence": None, "score": 0.2, "store_id": "episodic_vector"},
            ordinal=1,
        )

        self.assertEqual(0.077, invalid_score)
        self.assertEqual(0.229, episodic_score)

    def test_normalize_query_plan_handles_instances_mappings_and_default_planner(self) -> None:
        planner = _PlannerStub(intent="affection")
        plan = QueryPlan(original_query="keep", intent="chat")
        self.assertIs(
            plan,
            _normalize_query_plan(
                query_plan=plan,
                query="ignored",
                turn_index=1,
                planner=planner,
            ),
        )

        mapped = _normalize_query_plan(
            query_plan={
                "original_query": "mapped",
                "rewritten_queries": ["one", "", "two"],
                "intent": "",
                "target_stores": ["episodic_vector", "", "neo4j_bond"],
                "skip_retrieval": True,
                "max_rounds": "4",
                "time_lookback_days": "7",
            },
            query="fallback",
            turn_index=2,
            planner=planner,
        )
        defaulted = _normalize_query_plan(
            query_plan=None,
            query="from-planner",
            turn_index=3,
            planner=planner,
        )

        self.assertEqual("mapped", mapped.original_query)
        self.assertEqual(["one", "two"], mapped.rewritten_queries)
        self.assertEqual("affection", mapped.intent)
        self.assertEqual(["episodic_vector", "neo4j_bond"], mapped.target_stores)
        self.assertTrue(mapped.skip_retrieval)
        self.assertEqual(4, mapped.max_rounds)
        self.assertEqual(7, mapped.time_lookback_days)
        self.assertEqual("affection", defaulted.intent)
        self.assertEqual([("fallback", 2), ("from-planner", 3)], planner.calls)

    def test_log_event_falls_back_to_log_error(self) -> None:
        ledger = SimpleNamespace(log_error=MagicMock())
        contract = RetrievalPipelineContract(
            embedding_service=MagicMock(),
            recall_service=MagicMock(),
            reranker_service=MagicMock(),
            evidence_pack_service=MagicMock(),
            ledger=ledger,
        )

        contract._log_event(
            request_id="req-pipe-log",
            intent_id="intent-pipe-log",
            status="DEGRADED",
            reason_code="PIPELINE_DOWN",
            detail="pipeline degraded",
            payload={"ok": False},
            ts_ms=1234,
        )

        ledger.log_error.assert_called_once_with(
            request_id="req-pipe-log",
            stage=S_RET_PIPELINE,
            reason_code="PIPELINE_DOWN",
            detail="pipeline degraded",
        )

    def test_log_fast_path_rerank_noops_without_ledger(self) -> None:
        contract = RetrievalPipelineContract(
            embedding_service=MagicMock(),
            recall_service=MagicMock(),
            reranker_service=MagicMock(),
            evidence_pack_service=MagicMock(),
            ledger=None,
        )
        rerank_result = RerankerResult(
            success=True,
            status=STATUS_OK,
            reason_code=None,
            stage=S_RET_RERANK,
            detail="fast path",
            output={"effective_provider": "fast_path_heuristic"},
        )

        contract._log_fast_path_rerank(
            request_id="req-fast-path",
            intent_id="intent-fast-path",
            rerank_result=rerank_result,
            ts_ms=1234,
        )

    def test_run_records_meta_learning_and_tolerates_meta_learning_failure(self) -> None:
        class _MetaLoop:
            def __init__(self) -> None:
                self.record = MagicMock()

        agentic_result = SimpleNamespace(
            recall_summary={"candidates": [{"memory_id": "m1"}], "reason_code": None},
            evidence_items=[],
            elapsed_ms=18,
            rounds=2,
            as_dict=lambda: {"rounds": 2, "elapsed_ms": 18},
        )
        dedup_result = SimpleNamespace(
            candidates=[{"memory_id": "m1"}],
            removed_count=1,
            original_count=2,
            merge_log=[{"winner": "m1"}],
            as_dict=lambda: {"removed_count": 1, "original_count": 2},
        )
        adaptive_budget = SimpleNamespace(
            compute=lambda *_args, **_kwargs: SimpleNamespace(
                adjustments_applied=["trim"],
                as_dict=lambda: {"adjustments_applied": ["trim"]},
            )
        )
        embedding_service = SimpleNamespace(
            embed=lambda **_kwargs: SimpleNamespace(
                reason_code=None,
                output={"embedding_vector": [0.1, 0.2], "route_id": "default"},
            )
        )
        reranker_service = SimpleNamespace(
            rerank=lambda **_kwargs: SimpleNamespace(
                reason_code=None,
                output={"topk": [{"memory_id": "m1"}]},
            )
        )
        evidence_pack_service = SimpleNamespace(
            build_pack=lambda **_kwargs: SimpleNamespace(
                success=True,
                reason_code="PACK_REASON",
                output={"topk": [{"memory_id": "m1"}], "evidence_refs": [{"memory_id": "m1"}]},
            )
        )

        def _agentic_ctor(**_kwargs: object) -> SimpleNamespace:
            return SimpleNamespace(retrieve=lambda **__kwargs: agentic_result)

        contract = RetrievalPipelineContract(
            embedding_service=embedding_service,
            recall_service=MagicMock(),
            reranker_service=reranker_service,
            evidence_pack_service=evidence_pack_service,
            ledger=None,
            adaptive_budget=adaptive_budget,
            semantic_dedup=SimpleNamespace(dedup=lambda candidates: dedup_result),
        )

        meta_loop = _MetaLoop()
        with patch("host_brain.retrieval.pipeline.AgenticRetrievalLoop", side_effect=_agentic_ctor):
            with patch("host_brain.app.dependencies.get_meta_learning_loop", return_value=meta_loop):
                result = contract.run(
                    request_id="req-meta-ok",
                    query="hello",
                    query_plan=QueryPlan(
                        original_query="hello",
                        intent="chat",
                        target_stores=["episodic_vector"],
                        skip_retrieval=False,
                        max_rounds=1,
                    ),
                    route_target="edge",
                    ts_ms=12345,
                )

            with patch("host_brain.app.dependencies.get_meta_learning_loop", side_effect=RuntimeError("no meta")):
                degraded = contract.run(
                    request_id="req-meta-fail",
                    query="hello",
                    query_plan=QueryPlan(
                        original_query="hello",
                        intent="chat",
                        target_stores=["episodic_vector"],
                        skip_retrieval=False,
                        max_rounds=1,
                    ),
                    route_target="edge",
                    ts_ms=12346,
                )

        self.assertTrue(result.success)
        self.assertEqual("PACK_REASON", result.reason_code)
        self.assertIn("PACK_REASON", result.output["reason_chain"])
        self.assertEqual(4, meta_loop.record.call_count)
        self.assertEqual("query_planner", meta_loop.record.call_args_list[0].args[0])
        self.assertEqual("agentic_retrieval_loop", meta_loop.record.call_args_list[1].args[0])
        self.assertEqual("semantic_dedup", meta_loop.record.call_args_list[2].args[0])
        self.assertEqual("adaptive_prompt_budget", meta_loop.record.call_args_list[3].args[0])
        self.assertTrue(degraded.success)
        self.assertEqual("PACK_REASON", degraded.reason_code)


class TestBuildRetrievalPipelineFromEnv(unittest.TestCase):
    def test_raises_for_each_invalid_runtime_config_stage(self) -> None:
        success = lambda label: _ConfigResult(success=True, config=object(), label=label)
        failure = lambda label: _ConfigResult(success=False, config=None, label=label)
        cases = {
            "embedding": "embedding runtime config invalid",
            "recall": "recall runtime config invalid",
            "rerank": "reranker runtime config invalid",
            "chroma": "chroma runtime config invalid",
        }

        for stage, expected in cases.items():
            with self.subTest(stage=stage):
                with patch.multiple(
                    "host_brain.retrieval.pipeline",
                    load_embedding_runtime_config=(
                        lambda env=None, **_kwargs: failure("embedding")
                        if stage == "embedding"
                        else success("embedding")
                    ),
                    load_recall_runtime_config=(
                        lambda env=None, **_kwargs: failure("recall")
                        if stage == "recall"
                        else success("recall")
                    ),
                    load_reranker_runtime_config=(
                        lambda env=None, **_kwargs: failure("rerank")
                        if stage == "rerank"
                        else success("rerank")
                    ),
                    load_chroma_runtime_config=(
                        lambda env=None, **_kwargs: failure("chroma")
                        if stage == "chroma"
                        else success("chroma")
                    ),
                ):
                    with self.assertRaisesRegex(RuntimeError, expected):
                        build_retrieval_pipeline_from_env(env={})

    def test_build_swallows_neo4j_client_failure_and_merges_local_overrides(self) -> None:
        embedding_registry_calls: list[dict[str, object]] = []
        style_store = MagicMock()
        world_store = MagicMock()
        artifact_vault = MagicMock()

        def _embedding_registry(*, env: dict[str, object], openai_api_key: str) -> dict[str, str]:
            embedding_registry_calls.append(
                {
                    "env_marker": env.get("MERGED_MARKER"),
                    "openai_api_key": openai_api_key,
                }
            )
            return {"provider": "embed"}

        def _recall_service_factory(**kwargs: object) -> SimpleNamespace:
            return SimpleNamespace(**kwargs)

        with patch.multiple(
            "host_brain.retrieval.pipeline",
            load_embedding_runtime_config=lambda env=None, **_kwargs: _ConfigResult(
                success=True,
                config="embed-config",
                label="embedding",
            ),
            load_recall_runtime_config=lambda env=None, **_kwargs: _ConfigResult(
                success=True,
                config="recall-config",
                label="recall",
            ),
            load_reranker_runtime_config=lambda env=None, **_kwargs: _ConfigResult(
                success=True,
                config="rerank-config",
                label="rerank",
            ),
            load_chroma_runtime_config=lambda env=None, **_kwargs: _ConfigResult(
                success=True,
                config="chroma-config",
                label="chroma",
            ),
            load_host_config=lambda **_kwargs: SimpleNamespace(
                success=True,
                config=SimpleNamespace(openai_api_key=""),
            ),
            build_embedding_provider_registry=_embedding_registry,
            build_reranker_provider_registry=lambda **_kwargs: {"provider": "rerank"},
            load_neo4j_runtime_config=lambda env=None, **_kwargs: _ConfigResult(
                success=True,
                config=SimpleNamespace(enabled=True),
                label="neo4j",
            ),
            build_neo4j_bond_client=MagicMock(side_effect=RuntimeError("neo4j unavailable")),
            EmbeddingServiceContract=lambda **kwargs: SimpleNamespace(**kwargs),
            RecallFusionServiceContract=_recall_service_factory,
            RerankerServiceContract=lambda **kwargs: SimpleNamespace(**kwargs),
            EvidencePackServiceContract=lambda **kwargs: SimpleNamespace(**kwargs),
            QueryPlanner=lambda: "query-planner",
            AdaptivePromptBudget=lambda: "adaptive-budget",
            SemanticDedup=lambda: "semantic-dedup",
        ):
            with patch(
                "host_brain.retrieval.pipeline.ChromaHttpSearchClient.from_runtime_config",
                return_value="chroma-client",
            ):
                with patch(
                    "host_brain.retrieval.pipeline.StyleCanonStore",
                    return_value=style_store,
                ) as style_ctor:
                    with patch(
                        "host_brain.retrieval.pipeline.WorldCanonStore",
                        return_value=world_store,
                    ) as world_ctor:
                        with patch(
                            "host_brain.retrieval.pipeline.ArtifactVault",
                            return_value=artifact_vault,
                        ) as vault_ctor:
                            result = build_retrieval_pipeline_from_env(
                                env={"MERGED_MARKER": "env"},
                                local_overrides={
                                    "MERGED_MARKER": "override",
                                    "OPENAI_API_KEY": "override-key",
                                    "Natsume_STYLE_CANON_ROOT": "D:/style",
                                    "Natsume_WORLD_CANON_ROOT": "D:/world",
                                    "Natsume_VAULT_ROOT": "D:/vault",
                                },
                                node_id="host-neo4j",
                                ledger="ledger",
                            )

        self.assertEqual(
            [{"env_marker": "override", "openai_api_key": "override-key"}],
            embedding_registry_calls,
        )
        self.assertEqual({"provider": "embed"}, result.embedding_service.provider_registry)
        self.assertEqual("chroma-client", result.recall_service.chroma_client)
        self.assertIsNone(result.recall_service.neo4j_client)
        self.assertEqual(style_store, result.recall_service.style_store)
        self.assertEqual(world_store, result.recall_service.world_store)
        self.assertEqual(artifact_vault, result.recall_service.artifact_vault)
        self.assertEqual("query-planner", result.query_planner)
        self.assertEqual("adaptive-budget", result.adaptive_budget)
        self.assertEqual("semantic-dedup", result.semantic_dedup)
        style_ctor.assert_called_once_with(style_root="D:/style", ledger="ledger")
        world_ctor.assert_called_once_with(world_root="D:/world", ledger="ledger")
        vault_ctor.assert_called_once_with(root_dir="D:/vault", ledger="ledger")


if __name__ == "__main__":
    unittest.main()
