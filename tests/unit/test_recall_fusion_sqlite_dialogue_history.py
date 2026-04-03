from __future__ import annotations

import json

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.memory.chroma_client import embed_text
from host_brain.retrieval.recall_fusion import (
    STORE_SQLITE_LEDGER,
    RecallFusionServiceContract,
    load_recall_runtime_config,
)
from tests.fakes.chroma_client import FakeChromaClient


def test_sqlite_ledger_route_recalls_dialogue_history_from_memory_ops() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        ledger.log_memory_op(
            {
                "request_id": "req_chat_hist_001",
                "intent_id": "intent_chat_hist_001",
                "op_kind": "WRITE",
                "memory_id": "mem_chat_hist_001",
                "target_memory_id": None,
                "source_memory_ids": None,
                "evidence_refs": ["chat://request/req_chat_hist_001"],
                "status": "SUCCESS",
                "reason_code": None,
                "detail": (
                    "memory written: User: I miss madara today. "
                    "Natsume: We can revisit those warm memories together."
                ),
                "decay_profile_id": "SLOW",
                "visibility": "internal",
                "ts_ms": 1_800_360_000_000,
            }
        )

        env = {
            "RETR_RECALL_ENABLED": "true",
            "RETR_RECALL_DEFAULT_ROUTE": "route_sqlite_ledger",
            "RETR_RECALL_ROUTES_JSON": json.dumps(
                {
                    "route_sqlite_ledger": {
                        "store_id": STORE_SQLITE_LEDGER,
                        "top_k": 3,
                        "score_weight": 1.0,
                        "node_selector": "*",
                    }
                },
                ensure_ascii=False,
            ),
        }
        cfg_result = load_recall_runtime_config(env=env)
        assert cfg_result.success, cfg_result.as_dict()
        assert cfg_result.config is not None

        service = RecallFusionServiceContract(
            chroma_client=FakeChromaClient(),
            ledger=ledger,
            runtime_config=cfg_result.config,
            node_id="host-01",
            available=True,
            use_hybrid=False,
        )

        query = "madara warm memories"
        query_embedding, _ = embed_text(query)
        result = service.recall(
            request_id="req_recall_sqlite_hist_001",
            query=query,
            query_embedding=query_embedding,
            ts_ms=1_800_360_000_100,
        )

        assert result.success
        assert result.output["candidate_count"] > 0
        top = result.output["candidates"][0]
        assert top["doc_id"] == "mem_chat_hist_001"
        assert top["metadata"]["store_id"] == STORE_SQLITE_LEDGER
        assert top["source_ref"] == "ledger://memory_ops_log/mem_chat_hist_001"
        assert top["evidence_refs"] == "chat://request/req_chat_hist_001"
        assert "madara" in top["snippet"].lower()
    finally:
        ledger.close()
