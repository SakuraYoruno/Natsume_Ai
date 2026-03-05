from __future__ import annotations

import json

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.memory.canon_style_store import StyleCanonStore
from host_brain.memory.canon_world_store import WorldCanonStore
from host_brain.memory.chroma_client import InMemoryChromaClient, embed_text
from host_brain.retrieval.recall_fusion import (
    STORE_ARTIFACT_VAULT,
    STORE_EPISODIC_VECTOR,
    STORE_NEO4J_BOND,
    STORE_SQLITE_LEDGER,
    STORE_STYLE_CANON,
    STORE_WORLD_CANON,
    RecallFusionServiceContract,
    load_recall_runtime_config,
)


def test_recall_routes_align_with_devspec_8_store_domains(tmp_path) -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        style_store = StyleCanonStore(style_root=tmp_path / "style", ledger=ledger)
        world_store = WorldCanonStore(world_root=tmp_path / "world", ledger=ledger)

        style_store.upsert(
            canon_id="natsume_tone",
            content="natsume style c-tone minimal response",
            tags=["style", "tone"],
            proposal_id="prop_style_001",
        )
        world_store.upsert_document(
            doc_id="academy_rules",
            title="Natsume Academy Rules",
            tags=["world", "canon"],
            body="academy world canon for natsume setting consistency",
            proposal_id="prop_world_001",
        )

        ledger.log_intent(
            {
                "request_id": "req_led_001",
                "intent_id": "intent_led_001",
                "session_id": "session_led_001",
                "seq": 1,
                "ts_ms": 1_800_300_000_000,
                "nonce": "nonce_led_001",
                "raw_text": "ledger evidence for natsume retrieval",
            }
        )
        ledger.log_bond_update(
            {
                "request_id": "req_bond_001",
                "intent_id": "intent_bond_001",
                "session_id": "session_bond_001",
                "seq": 1,
                "event_id": "bond_evt_001",
                "topic_id": "natsume_bond",
                "capability_hint": "bond_memory",
                "update_kind": "INTERACTION",
                "status": "OK",
                "stage": "S_BOND_UPDATE",
                "evidence_refs": ["evd://bond/bond_evt_001"],
                "payload": {"summary": "natsume bond relationship memory"},
                "detail": "bond seeded",
                "ts_ms": 1_800_300_000_001,
            }
        )
        ledger.log_vault_manifest(
            {
                "request_id": "req_vault_001",
                "manifest_id": "manifest_seed_001",
                "intent_id": "intent_vault_001",
                "integrity_digest": "sha256_seed_001",
                "manifest_path": "runtime/vault/manifests/manifest_seed_001.json",
                "items_count": 1,
                "evidence_refs": ["evd://vault/manifest_seed_001"],
                "policy_profile_id": "vault_default",
                "undo_ref": None,
                "reversible": 0,
                "skill_name": "seed",
                "session_id": "session_vault_001",
                "seq": 1,
                "status": "ACTIVE",
                "ts_ms": 1_800_300_000_002,
            }
        )

        chroma = InMemoryChromaClient(
            collection_profiles={"episodic": 16},
            default_collection="episodic",
        )
        episodic_vector, _ = embed_text("natsume episodic memory evidence")
        chroma.upsert(
            memory_id="mem_epi_001",
            text="natsume episodic memory evidence",
            embedding=episodic_vector,
            collection="episodic",
            metadata={
                "source_ref": "memory://episodic/mem_epi_001",
                "evidence_ref": "evd://episodic/mem_epi_001",
            },
        )

        env = {
            "RETR_RECALL_ENABLED": "true",
            "RETR_RECALL_DEFAULT_ROUTE": "route_episodic",
            "RETR_RECALL_ROUTES_JSON": json.dumps(
                {
                    "route_sqlite_ledger": {
                        "store_id": STORE_SQLITE_LEDGER,
                        "top_k": 3,
                        "score_weight": 1.0,
                        "node_selector": "*",
                    },
                    "route_neo4j_bond": {
                        "store_id": STORE_NEO4J_BOND,
                        "top_k": 3,
                        "score_weight": 1.0,
                        "node_selector": "*",
                    },
                    "route_episodic": {
                        "store_id": STORE_EPISODIC_VECTOR,
                        "collection": "episodic",
                        "top_k": 3,
                        "score_weight": 1.0,
                        "node_selector": "*",
                    },
                    "route_style_canon": {
                        "store_id": STORE_STYLE_CANON,
                        "top_k": 3,
                        "score_weight": 1.0,
                        "node_selector": "*",
                    },
                    "route_world_canon": {
                        "store_id": STORE_WORLD_CANON,
                        "top_k": 3,
                        "score_weight": 1.0,
                        "node_selector": "*",
                    },
                    "route_artifact_vault": {
                        "store_id": STORE_ARTIFACT_VAULT,
                        "top_k": 3,
                        "score_weight": 1.0,
                        "node_selector": "*",
                    },
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
            world_store=world_store,
            runtime_config=cfg_result.config,
            node_id="host-01",
            available=True,
            use_hybrid=False,
        )
        query = "natsume ledger bond style world manifest episodic"
        query_embedding, _ = embed_text(query)
        result = service.recall(
            request_id="req_six_store_001",
            query=query,
            query_embedding=query_embedding,
            ts_ms=1_800_300_000_100,
        )

        assert len(result.output["routes"]) == 6
        route_store_ids = {str(row.get("store_id")) for row in result.output["route_rows"]}
        assert route_store_ids == {
            STORE_SQLITE_LEDGER,
            STORE_NEO4J_BOND,
            STORE_EPISODIC_VECTOR,
            STORE_STYLE_CANON,
            STORE_WORLD_CANON,
            STORE_ARTIFACT_VAULT,
        }
        assert result.output["route_errors"] == []
        assert result.output["candidate_count"] > 0
    finally:
        ledger.close()
