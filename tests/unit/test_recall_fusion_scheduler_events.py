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


def test_sqlite_ledger_route_recalls_scheduler_events() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        ledger.log_scheduler_event(
            {
                "event_id": "sched_evt_daily_reco_001",
                "task_id": "daily_recommendation",
                "trigger_ts": 1_800_000_000_000,
                "intent_id": "intent_daily_reco_001",
                "status": "ERROR",
                "reason_code": "LLM_POOL_EXHAUSTED",
                "mode_at_trigger": "normal",
                "error_detail": "light novel recommendation background job could not reach model pool",
                "ts_ms": 1_800_000_000_010,
            }
        )

        cfg_result = load_recall_runtime_config(
            env={
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
        )
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

        query = "daily recommendation model pool"
        query_embedding, _ = embed_text(query)
        result = service.recall(
            request_id="req_recall_scheduler_001",
            query=query,
            query_embedding=query_embedding,
            ts_ms=1_800_000_000_100,
        )

        assert result.success
        top = result.output["candidates"][0]
        assert top["doc_id"] == "sched_evt_daily_reco_001"
        assert top["metadata"]["store_id"] == STORE_SQLITE_LEDGER
        assert top["source_ref"] == "ledger://scheduler_event_log/sched_evt_daily_reco_001"
        assert top["metadata"]["task_id"] == "daily_recommendation"
    finally:
        ledger.close()
