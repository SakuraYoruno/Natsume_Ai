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


def _build_sqlite_only_service(ledger: SQLiteLedger) -> RecallFusionServiceContract:
    env = {
        "RETR_RECALL_ENABLED": "true",
        "RETR_RECALL_DEFAULT_ROUTE": "route_sqlite_ledger",
        "RETR_RECALL_ROUTES_JSON": json.dumps(
            {
                "route_sqlite_ledger": {
                    "store_id": STORE_SQLITE_LEDGER,
                    "top_k": 5,
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
    return RecallFusionServiceContract(
        chroma_client=FakeChromaClient(),
        ledger=ledger,
        runtime_config=cfg_result.config,
        node_id="host-01",
        available=True,
        use_hybrid=False,
    )


def test_sqlite_route_prefers_same_session_on_equal_text_score() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        common_detail = "memory written: natsume memory anchor phrase"
        ledger.log_memory_op(
            {
                "request_id": "req_sqlite_boost_001",
                "intent_id": "intent_sqlite_boost_001",
                "op_kind": "WRITE",
                "memory_id": "mem_other",
                "target_memory_id": None,
                "source_memory_ids": None,
                "evidence_refs": ["chat://session/session-other"],
                "status": "SUCCESS",
                "reason_code": None,
                "detail": common_detail,
                "decay_profile_id": "SLOW",
                "visibility": "internal",
                "ts_ms": 1_800_400_000_000,
            }
        )
        ledger.log_memory_op(
            {
                "request_id": "req_sqlite_boost_002",
                "intent_id": "intent_sqlite_boost_002",
                "op_kind": "WRITE",
                "memory_id": "mem_target",
                "target_memory_id": None,
                "source_memory_ids": None,
                "evidence_refs": ["chat://session/session-target"],
                "status": "SUCCESS",
                "reason_code": None,
                "detail": common_detail,
                "decay_profile_id": "SLOW",
                "visibility": "internal",
                "ts_ms": 1_800_400_000_000,
            }
        )

        service = _build_sqlite_only_service(ledger)
        query = "natsume memory anchor phrase"
        query_embedding, _ = embed_text(query)
        result = service.recall(
            request_id="req_sqlite_recall_boost_001",
            query=query,
            query_embedding=query_embedding,
            session_id="session-target",
            ts_ms=1_800_400_000_100,
        )

        assert result.success
        top = result.output["candidates"][0]
        assert top["doc_id"] == "mem_target"
        assert float(top["metadata"]["score_boost"]["session_boost"]) > 0.0
    finally:
        ledger.close()


def test_sqlite_route_prefers_recent_records_without_session_hint() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        common_detail = "memory written: natsume memory anchor phrase"
        ledger.log_memory_op(
            {
                "request_id": "req_sqlite_recency_001",
                "intent_id": "intent_sqlite_recency_001",
                "op_kind": "WRITE",
                "memory_id": "mem_old",
                "target_memory_id": None,
                "source_memory_ids": None,
                "evidence_refs": ["chat://session/session-old"],
                "status": "SUCCESS",
                "reason_code": None,
                "detail": common_detail,
                "decay_profile_id": "SLOW",
                "visibility": "internal",
                "ts_ms": 1_800_300_000_000,
            }
        )
        ledger.log_memory_op(
            {
                "request_id": "req_sqlite_recency_002",
                "intent_id": "intent_sqlite_recency_002",
                "op_kind": "WRITE",
                "memory_id": "mem_recent",
                "target_memory_id": None,
                "source_memory_ids": None,
                "evidence_refs": ["chat://session/session-recent"],
                "status": "SUCCESS",
                "reason_code": None,
                "detail": common_detail,
                "decay_profile_id": "SLOW",
                "visibility": "internal",
                "ts_ms": 1_800_400_299_000,
            }
        )

        service = _build_sqlite_only_service(ledger)
        query = "natsume memory anchor phrase"
        query_embedding, _ = embed_text(query)
        result = service.recall(
            request_id="req_sqlite_recall_recency_001",
            query=query,
            query_embedding=query_embedding,
            ts_ms=1_800_400_300_000,
        )

        assert result.success
        top = result.output["candidates"][0]
        assert top["doc_id"] == "mem_recent"
        assert float(top["metadata"]["score_boost"]["recency_boost"]) > 0.0
    finally:
        ledger.close()
