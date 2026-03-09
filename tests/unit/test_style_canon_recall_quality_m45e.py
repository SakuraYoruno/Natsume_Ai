from __future__ import annotations

import json

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.memory.canon_style_store import StyleCanonStore
from host_brain.memory.chroma_client import InMemoryChromaClient, embed_text
from host_brain.retrieval.recall_fusion import (
    STORE_STYLE_CANON,
    RecallFusionServiceContract,
    load_recall_runtime_config,
)


def test_style_canon_recall_quality_baseline(tmp_path) -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        style_store = StyleCanonStore(style_root=tmp_path / "style", ledger=ledger)
        style_store.upsert(
            canon_id="natsume_tone",
            content="gentle restrained empathetic calm response style",
            tags=["gentle", "empathetic", "calm"],
            proposal_id="prop_style_quality_001",
        )
        style_store.upsert(
            canon_id="natsume_boundary",
            content="set clear boundaries avoid overpromise and keep safety first",
            tags=["boundary", "safety", "avoid_overpromise"],
            proposal_id="prop_style_quality_002",
        )
        style_store.upsert(
            canon_id="natsume_humor",
            content="light dry humor with subtle teasing and short cadence",
            tags=["humor", "dry", "subtle"],
            proposal_id="prop_style_quality_003",
        )

        chroma = InMemoryChromaClient(
            collection_profiles={"episodic": 16},
            default_collection="episodic",
        )
        env = {
            "RETR_RECALL_ENABLED": "true",
            "RETR_RECALL_DEFAULT_ROUTE": "route_style_canon",
            "RETR_RECALL_ROUTES_JSON": json.dumps(
                {
                    "route_style_canon": {
                        "store_id": STORE_STYLE_CANON,
                        "collection": "canon",
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
            ledger=ledger,
            chroma_client=chroma,
            style_store=style_store,
            runtime_config=cfg_result.config,
            node_id="host-01",
            available=True,
            use_hybrid=False,
        )

        cases = [
            ("empathetic gentle calm response", "natsume_tone"),
            ("clear boundary avoid overpromise", "natsume_boundary"),
            ("light dry humor subtle teasing", "natsume_humor"),
        ]
        for idx, (query, expected_doc_id) in enumerate(cases, start=1):
            query_embedding, _ = embed_text(query)
            result = service.recall(
                request_id=f"req_style_quality_{idx:03d}",
                query=query,
                query_embedding=query_embedding,
                ts_ms=1_800_410_000_000 + idx,
            )
            assert result.success
            assert result.output["candidate_count"] > 0
            top = result.output["candidates"][0]
            assert top["doc_id"] == expected_doc_id
            assert top["metadata"]["store_id"] == STORE_STYLE_CANON
    finally:
        ledger.close()
