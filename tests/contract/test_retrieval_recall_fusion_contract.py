import json
import unittest

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.errors import CHROMA_COLLECTION_MISSING
from host_brain.memory.chroma_client import InMemoryChromaClient, embed_text
from host_brain.retrieval.recall_fusion import (
    RET_OP_RECALL,
    S_RET_RECALL,
    STATUS_DEGRADED,
    STATUS_OK,
    RecallFusionServiceContract,
    load_recall_runtime_config,
)


class TestRetrievalRecallFusionContract(unittest.TestCase):
    def setUp(self) -> None:
        self.now_ms = 1_800_180_000_000
        self.ledger = SQLiteLedger(db_path=":memory:")
        self.chroma = InMemoryChromaClient(
            collection_profiles={"episodic": 16, "canon": 16},
            default_collection="episodic",
        )
        self._seed_collections()

    def tearDown(self) -> None:
        self.ledger.close()

    def test_multi_route_recall_fusion_dedup_is_stable(self) -> None:
        env = {
            "RETR_RECALL_ENABLED": "true",
            "RETR_RECALL_DEFAULT_ROUTE": "episodic_route",
            "RETR_RECALL_ROUTES_JSON": json.dumps(
                {
                    "episodic_route": {
                        "collection": "episodic",
                        "top_k": 3,
                        "score_weight": 1.0,
                        "node_selector": "host-01",
                    },
                    "canon_route": {
                        "collection": "canon",
                        "top_k": 3,
                        "score_weight": 0.8,
                        "node_selector": "host-01",
                    },
                },
                ensure_ascii=False,
            ),
        }
        config_result = load_recall_runtime_config(env=env)
        self.assertTrue(config_result.success, msg=config_result.as_dict())
        assert config_result.config is not None

        svc = RecallFusionServiceContract(
            ledger=self.ledger,
            chroma_client=self.chroma,
            runtime_config=config_result.config,
            node_id="host-01",
            available=True,
        )
        query = "Nastume retrieval fusion"
        query_embedding, _ = embed_text(query)

        result = svc.recall(
            request_id="req_recall_001",
            query=query,
            query_embedding=query_embedding,
            ts_ms=self.now_ms,
        )

        self.assertTrue(result.success)
        self.assertEqual(STATUS_OK, result.status)
        self.assertEqual(S_RET_RECALL, result.stage)
        self.assertIsNone(result.reason_code)
        self.assertEqual(2, len(result.output["routes"]))
        self.assertEqual(0, len(result.output["route_errors"]))
        self.assertGreaterEqual(result.output["candidate_count"], 2)

        shared_rows = [
            row
            for row in result.output["candidates"]
            if row["source_ref"] == "memo://shared"
        ]
        self.assertEqual(1, len(shared_rows), msg=result.output["candidates"])
        shared = shared_rows[0]
        self.assertIn("matched_routes", shared["metadata"])
        self.assertEqual(
            ["canon_route", "episodic_route"],
            sorted(shared["metadata"]["matched_routes"]),
        )

        self.assertEqual(1, len(self.ledger.retrieval_event_log))
        row = self.ledger.retrieval_event_log[0]
        self.assertEqual(RET_OP_RECALL, row["op_kind"])
        self.assertEqual(STATUS_OK, row["status"])
        self.assertEqual(S_RET_RECALL, row["stage"])

    def test_partial_route_failure_keeps_candidates_with_stable_reason(self) -> None:
        env = {
            "RETR_RECALL_ENABLED": "true",
            "RETR_RECALL_DEFAULT_ROUTE": "episodic_route",
            "RETR_RECALL_ROUTES_JSON": json.dumps(
                {
                    "episodic_route": {
                        "collection": "episodic",
                        "top_k": 3,
                        "score_weight": 1.0,
                        "node_selector": "host-01",
                    },
                    "missing_route": {
                        "collection": "canon_missing",
                        "top_k": 3,
                        "score_weight": 0.7,
                        "node_selector": "host-01",
                    },
                },
                ensure_ascii=False,
            ),
        }
        config_result = load_recall_runtime_config(env=env)
        self.assertTrue(config_result.success, msg=config_result.as_dict())
        assert config_result.config is not None

        svc = RecallFusionServiceContract(
            ledger=self.ledger,
            chroma_client=self.chroma,
            runtime_config=config_result.config,
            node_id="host-01",
            available=True,
        )
        query = "Nastume retrieval fusion"
        query_embedding, _ = embed_text(query)

        result = svc.recall(
            request_id="req_recall_002",
            query=query,
            query_embedding=query_embedding,
            ts_ms=self.now_ms + 1,
        )

        self.assertFalse(result.success)
        self.assertEqual(STATUS_DEGRADED, result.status)
        self.assertEqual(CHROMA_COLLECTION_MISSING, result.reason_code)
        self.assertEqual(S_RET_RECALL, result.stage)
        self.assertGreater(result.output["candidate_count"], 0)
        self.assertEqual(1, len(result.output["route_errors"]))
        self.assertEqual(CHROMA_COLLECTION_MISSING, result.output["route_errors"][0]["reason_code"])

        reason_codes = [
            row["reason_code"]
            for row in self.ledger.retrieval_event_log
            if row["op_kind"] == RET_OP_RECALL
        ]
        self.assertIn(CHROMA_COLLECTION_MISSING, reason_codes)

    def _seed_collections(self) -> None:
        query = "Nastume retrieval fusion"
        emb_query, _ = embed_text(query)

        emb_episodic, _ = embed_text("episodic memory around retrieval fusion")
        self.chroma.upsert(
            memory_id="mem_epi_001",
            text="episodic memory around retrieval fusion",
            embedding=emb_episodic,
            collection="episodic",
            metadata={"source_ref": "memo://episodic/001", "evidence_ref": "evd://episodic/001"},
        )

        self.chroma.upsert(
            memory_id="mem_shared",
            text="shared memory from episodic route",
            embedding=emb_query,
            collection="episodic",
            metadata={"source_ref": "memo://shared", "evidence_ref": "evd://shared/episodic"},
        )

        self.chroma.upsert(
            memory_id="mem_shared",
            text="shared memory from canon route",
            embedding=emb_query,
            collection="canon",
            metadata={"source_ref": "memo://shared", "evidence_ref": "evd://shared/canon"},
        )

        emb_canon, _ = embed_text("canon policy retrieval detail")
        self.chroma.upsert(
            memory_id="mem_can_001",
            text="canon policy retrieval detail",
            embedding=emb_canon,
            collection="canon",
            metadata={"source_ref": "memo://canon/001", "evidence_ref": "evd://canon/001"},
        )


if __name__ == "__main__":
    unittest.main()
