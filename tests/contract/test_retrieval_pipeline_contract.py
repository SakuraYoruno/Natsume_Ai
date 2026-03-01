import json
import unittest

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.errors import RETR_RERANK_UNAVAILABLE
from host_brain.memory.chroma_client import InMemoryChromaClient, embed_text
from host_brain.retrieval.embedding import EmbeddingServiceContract, load_embedding_runtime_config
from host_brain.retrieval.evidence_pack import EvidencePackServiceContract
from host_brain.retrieval.pipeline import (
    RET_OP_PIPELINE,
    S_RET_PIPELINE,
    STATUS_DEGRADED,
    STATUS_OK,
    RetrievalPipelineContract,
)
from host_brain.retrieval.recall_fusion import RecallFusionServiceContract, load_recall_runtime_config
from host_brain.retrieval.reranker import RerankerServiceContract, load_reranker_runtime_config


class TestRetrievalPipelineContract(unittest.TestCase):
    def setUp(self) -> None:
        self.now_ms = 1_800_190_000_000
        self.ledger = SQLiteLedger(db_path=":memory:")
        self.chroma = InMemoryChromaClient(
            collection_profiles={"episodic": 16, "canon": 16},
            default_collection="episodic",
        )
        self._seed_collections()

    def tearDown(self) -> None:
        self.ledger.close()

    def test_pipeline_host_route_outputs_stable_evidence_pack(self) -> None:
        pipeline = self._build_pipeline(node_id="host-01", reranker_available=True)
        result = pipeline.run(
            request_id="req_pipe_001",
            intent_id="intent_pipe_001",
            query="natsume retrieval pipeline",
            top_k=2,
            ts_ms=self.now_ms,
        )

        self.assertTrue(result.success)
        self.assertEqual(STATUS_OK, result.status)
        self.assertEqual(S_RET_PIPELINE, result.stage)
        self.assertIsNone(result.reason_code)

        output = result.output
        self.assertEqual("host_default", output["embedding"]["route_id"])
        self.assertEqual("host_default", output["rerank"]["route_id"])
        self.assertEqual(2, len(output["recall"]["routes"]))
        self.assertEqual(0, len(output["reason_chain"]))
        self.assertEqual(2, len(output["evidence_pack"]["topk"]))
        self.assertFalse(output["evidence_pack"]["degraded"])

        op_kinds = {row["op_kind"] for row in self.ledger.retrieval_event_log}
        self.assertIn(RET_OP_PIPELINE, op_kinds)
        self.assertIn("EMBEDDING", op_kinds)
        self.assertIn("RECALL", op_kinds)
        self.assertIn("RERANK", op_kinds)
        self.assertIn("EVIDENCE_PACK", op_kinds)

    def test_pipeline_gpu_routes_apply_embedding_and_reranker_profiles(self) -> None:
        pipeline = self._build_pipeline(node_id="node-gpu-01", reranker_available=True)
        result = pipeline.run(
            request_id="req_pipe_002",
            intent_id="intent_pipe_002",
            query="natsume retrieval pipeline",
            top_k=2,
            ts_ms=self.now_ms + 1,
        )

        self.assertTrue(result.success)
        self.assertEqual(STATUS_OK, result.status)
        self.assertEqual("gpu_route", result.output["embedding"]["route_id"])
        self.assertEqual("mock_openai", result.output["embedding"]["provider"])
        self.assertEqual("text-embedding-3-large", result.output["embedding"]["embedding_model_id"])
        self.assertEqual("gpu_route", result.output["rerank"]["route_id"])
        self.assertEqual("mock_cross_encoder", result.output["rerank"]["provider"])
        self.assertEqual("bge-reranker-large", result.output["rerank"]["rerank_model_id"])

    def test_pipeline_reranker_unavailable_keeps_topk_and_reason_chain(self) -> None:
        pipeline = self._build_pipeline(node_id="host-01", reranker_available=False)
        result = pipeline.run(
            request_id="req_pipe_003",
            intent_id="intent_pipe_003",
            query="natsume retrieval pipeline",
            top_k=2,
            ts_ms=self.now_ms + 2,
        )

        self.assertFalse(result.success)
        self.assertEqual(STATUS_DEGRADED, result.status)
        self.assertEqual(RETR_RERANK_UNAVAILABLE, result.reason_code)
        self.assertIn(RETR_RERANK_UNAVAILABLE, result.output["reason_chain"])
        self.assertTrue(result.output["evidence_pack"]["degraded"])
        self.assertGreater(len(result.output["evidence_pack"]["topk"]), 0)

    def _build_pipeline(self, *, node_id: str, reranker_available: bool) -> RetrievalPipelineContract:
        embed_env = {
            "RETR_EMBED_ENABLED": "true",
            "RETR_EMBED_DEFAULT_ROUTE": "host_default",
            "RETR_EMBED_ROUTES_JSON": json.dumps(
                {
                    "host_default": {
                        "provider": "builtin_hash",
                        "model_id": "hash-embed-v1",
                        "dim": 16,
                        "timeout_ms": 500,
                        "node_selector": "host-01",
                    },
                    "gpu_route": {
                        "provider": "mock_openai",
                        "model_id": "text-embedding-3-large",
                        "dim": 16,
                        "timeout_ms": 900,
                        "node_selector": "node-gpu-01",
                    },
                },
                ensure_ascii=False,
            ),
        }
        rerank_env = {
            "RETR_RERANK_ENABLED": "true",
            "RETR_RERANK_DEFAULT_ROUTE": "host_default",
            "RETR_RERANK_ROUTES_JSON": json.dumps(
                {
                    "host_default": {
                        "provider": "heuristic",
                        "model_id": "heuristic-rerank-v1",
                        "top_k": 2,
                        "timeout_ms": 500,
                        "node_selector": "host-01",
                    },
                    "gpu_route": {
                        "provider": "mock_cross_encoder",
                        "model_id": "bge-reranker-large",
                        "top_k": 2,
                        "timeout_ms": 900,
                        "node_selector": "node-gpu-01",
                    },
                },
                ensure_ascii=False,
            ),
        }
        recall_env = {
            "RETR_RECALL_ENABLED": "true",
            "RETR_RECALL_DEFAULT_ROUTE": "episodic_route",
            "RETR_RECALL_ROUTES_JSON": json.dumps(
                {
                    "episodic_route": {
                        "collection": "episodic",
                        "top_k": 4,
                        "score_weight": 1.0,
                        "node_selector": "host-01,node-gpu-01",
                    },
                    "canon_route": {
                        "collection": "canon",
                        "top_k": 3,
                        "score_weight": 0.75,
                        "node_selector": "host-01,node-gpu-01",
                    },
                },
                ensure_ascii=False,
            ),
        }

        embed_config = load_embedding_runtime_config(env=embed_env).config
        rerank_config = load_reranker_runtime_config(env=rerank_env).config
        recall_config = load_recall_runtime_config(env=recall_env).config
        assert embed_config is not None
        assert rerank_config is not None
        assert recall_config is not None

        embedding_service = EmbeddingServiceContract(
            ledger=self.ledger,
            runtime_config=embed_config,
            node_id=node_id,
            available=True,
            provider_registry={
                "mock_openai": lambda text: (embed_text(text)[0], "text-embedding-3-large"),
            },
        )
        recall_service = RecallFusionServiceContract(
            ledger=self.ledger,
            chroma_client=self.chroma,
            runtime_config=recall_config,
            node_id=node_id,
            available=True,
        )
        reranker_service = RerankerServiceContract(
            ledger=self.ledger,
            runtime_config=rerank_config,
            node_id=node_id,
            available=reranker_available,
            provider_registry={
                "mock_cross_encoder": lambda _q, candidates: (
                    [round(max(0.0, 1.0 - idx * 0.1), 6) for idx in range(len(candidates))],
                    "bge-reranker-large",
                ),
            },
        )
        evidence_pack_service = EvidencePackServiceContract(ledger=self.ledger)

        return RetrievalPipelineContract(
            embedding_service=embedding_service,
            recall_service=recall_service,
            reranker_service=reranker_service,
            evidence_pack_service=evidence_pack_service,
            ledger=self.ledger,
        )

    def _seed_collections(self) -> None:
        query = "natsume retrieval pipeline"
        emb_query, _ = embed_text(query)

        self.chroma.upsert(
            memory_id="mem_epi_001",
            text="episodic retrieval note alpha",
            embedding=emb_query,
            collection="episodic",
            metadata={"source_ref": "memo://epi/001", "evidence_ref": "evd://epi/001"},
        )
        self.chroma.upsert(
            memory_id="mem_epi_002",
            text="episodic retrieval note beta",
            embedding=emb_query,
            collection="episodic",
            metadata={"source_ref": "memo://epi/002", "evidence_ref": "evd://epi/002"},
        )
        self.chroma.upsert(
            memory_id="mem_can_001",
            text="canon retrieval reference alpha",
            embedding=emb_query,
            collection="canon",
            metadata={"source_ref": "memo://canon/001", "evidence_ref": "evd://canon/001"},
        )


if __name__ == "__main__":
    unittest.main()
