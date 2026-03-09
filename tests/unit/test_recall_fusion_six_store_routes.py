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
    STORE_PERSONA_UTTERANCE,
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


def test_style_canon_prefers_vector_projection_when_available(tmp_path) -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        style_store = StyleCanonStore(style_root=tmp_path / "style", ledger=ledger)
        style_store.upsert(
            canon_id="natsume_tone",
            content="file route fallback content not matching query token",
            tags=["file_fallback_only"],
            proposal_id="prop_style_vec_001",
        )

        chroma = InMemoryChromaClient(
            collection_profiles={"episodic": 16, "canon": 16},
            default_collection="episodic",
        )
        query = "serene empathetic style guidance"
        query_embedding, _ = embed_text(query)
        chroma.upsert(
            memory_id="canon_style::natsume_tone",
            text="vector projection for serene empathetic style guidance",
            embedding=query_embedding,
            collection="canon",
            metadata={
                "canon_kind": "style",
                "canon_id": "natsume_tone",
                "version": 2,
                "hash": "hash_style_v2",
                "source_ref": "canon://style/natsume_tone",
                "evidence_ref": "evd://canon/style/natsume_tone@v2",
            },
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
        result = service.recall(
            request_id="req_style_vec_001",
            query=query,
            query_embedding=query_embedding,
            ts_ms=1_800_310_000_001,
        )

        assert result.success
        assert result.output["candidate_count"] > 0
        top = result.output["candidates"][0]
        assert top["doc_id"] == "natsume_tone"
        assert top["metadata"]["store_id"] == STORE_STYLE_CANON
        assert top["metadata"]["source_ref"] == "canon://style/natsume_tone"
    finally:
        ledger.close()


def test_style_canon_falls_back_to_file_when_vector_unavailable(tmp_path) -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        style_store = StyleCanonStore(style_root=tmp_path / "style", ledger=ledger)
        style_store.upsert(
            canon_id="natsume_tone",
            content="gentle supportive tone for calm style response",
            tags=["gentle", "supportive", "style"],
            proposal_id="prop_style_fb_001",
        )

        chroma = InMemoryChromaClient(
            collection_profiles={"episodic": 16},
            default_collection="episodic",
        )
        query = "gentle supportive tone"
        query_embedding, _ = embed_text(query)

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
        result = service.recall(
            request_id="req_style_fb_001",
            query=query,
            query_embedding=query_embedding,
            ts_ms=1_800_310_000_002,
        )

        assert result.success
        assert result.output["route_errors"] == []
        assert result.output["candidate_count"] > 0
        assert result.output["candidates"][0]["metadata"]["store_id"] == STORE_STYLE_CANON
    finally:
        ledger.close()


def test_persona_utterance_vector_route_returns_dialogue_candidates() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        chroma = InMemoryChromaClient(
            collection_profiles={"episodic": 16, "persona_utterance": 16},
            default_collection="episodic",
        )
        query = "今天有点难受 想聊聊"
        query_embedding, _ = embed_text(query)
        chroma.upsert(
            memory_id="persona_utterance::natsume_ai_ja::ja::abc001",
            text="今日は少しつらいんだね……無理しないで。",
            embedding=query_embedding,
            collection="persona_utterance",
            metadata={
                "canon_id": "natsume_ai_ja",
                "lang": "ja",
                "speaker": "夏目藍",
                "source_ref": "persona_utterance://natsume_ai_ja/ja/abc001",
                "evidence_ref": "evd://persona_utterance/natsume_ai_ja/ja/abc001",
                "file_rel_path": "story/ch01.ast",
                "block_id": "block_01",
                "line_index": 7,
            },
        )

        env = {
            "RETR_RECALL_ENABLED": "true",
            "RETR_RECALL_DEFAULT_ROUTE": "route_persona_utterance",
            "RETR_RECALL_ROUTES_JSON": json.dumps(
                {
                    "route_persona_utterance": {
                        "store_id": STORE_PERSONA_UTTERANCE,
                        "collection": "persona_utterance",
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
            runtime_config=cfg_result.config,
            node_id="host-01",
            available=True,
            use_hybrid=False,
        )
        result = service.recall(
            request_id="req_persona_vec_001",
            query=query,
            query_embedding=query_embedding,
            ts_ms=1_800_310_000_111,
        )

        assert result.success
        assert result.output["candidate_count"] > 0
        top = result.output["candidates"][0]
        assert top["metadata"]["store_id"] == STORE_PERSONA_UTTERANCE
        assert top["metadata"]["lang"] == "ja"
        assert "persona_utterance://" in str(top["source_ref"])
    finally:
        ledger.close()


def test_neo4j_route_failure_falls_back_to_sqlite_bond_without_degrade() -> None:
    class _BrokenNeo4jClient:
        @property
        def events(self):  # pragma: no cover - behavior-only stub
            raise RuntimeError("neo4j connection refused")

    ledger = SQLiteLedger(db_path=":memory:")
    try:
        ledger.log_bond_update(
            {
                "request_id": "req-bond-fallback-001",
                "intent_id": "intent-bond-fallback-001",
                "session_id": "sess-bond-fallback-001",
                "seq": 1,
                "event_id": "bond_evt_fallback_001",
                "topic_id": "bond_topic",
                "capability_hint": "bond_hint",
                "update_kind": "INTERACTION",
                "status": "OK",
                "stage": "S_BOND_UPDATE",
                "evidence_refs": ["evd://bond/bond_evt_fallback_001"],
                "payload": {"summary": "bond fallback memory"},
                "detail": "bond fallback seeded",
                "ts_ms": 1_800_320_000_001,
            }
        )

        chroma = InMemoryChromaClient(
            collection_profiles={"episodic": 16},
            default_collection="episodic",
        )
        emb, _ = embed_text("bond fallback query")
        chroma.upsert(
            memory_id="mem_epi_fallback_001",
            text="episodic backup candidate",
            embedding=emb,
            collection="episodic",
            metadata={"source_ref": "memory://episodic/mem_epi_fallback_001"},
        )

        env = {
            "RETR_RECALL_ENABLED": "true",
            "RETR_RECALL_DEFAULT_ROUTE": "route_episodic",
            "RETR_RECALL_ROUTES_JSON": json.dumps(
                {
                    "route_neo4j_bond": {
                        "store_id": STORE_NEO4J_BOND,
                        "top_k": 4,
                        "score_weight": 1.0,
                        "node_selector": "*",
                    },
                    "route_episodic": {
                        "store_id": STORE_EPISODIC_VECTOR,
                        "collection": "episodic",
                        "top_k": 4,
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
            neo4j_client=_BrokenNeo4jClient(),
            runtime_config=cfg_result.config,
            node_id="host-01",
            available=True,
            use_hybrid=False,
        )
        query = "bond fallback query"
        query_embedding, _ = embed_text(query)
        result = service.recall(
            request_id="req_recall_bond_fallback_001",
            query=query,
            query_embedding=query_embedding,
            ts_ms=1_800_320_000_010,
        )

        assert result.success
        assert result.status == "OK"
        assert result.reason_code is None
        assert result.output["route_errors"] == []
        assert result.output["candidate_count"] > 0
    finally:
        ledger.close()
