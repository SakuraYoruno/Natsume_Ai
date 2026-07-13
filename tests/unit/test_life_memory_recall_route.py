from __future__ import annotations

import json

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.errors import CHROMA_ENDPOINT_UNAVAILABLE
from host_brain.memory.chroma_client import embed_text
from host_brain.memory.life_memory import EPISODE_ACTION, EPISODE_BACKGROUND
from host_brain.memory.life_memory.store import LifeMemoryStore
from host_brain.retrieval.recall_fusion import RecallFusionServiceContract, load_recall_runtime_config
from host_brain.retrieval.recall_fusion_stores import STORE_EPISODIC_VECTOR, STORE_LIFE_EPISODE, STORE_LIFE_MEMORY_COLLECTION
from tests.fakes.chroma_client import FakeChromaClient


def test_life_episode_route_returns_episode_packet_candidate() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = LifeMemoryStore(ledger=ledger)
        written = store.write_chat_episode(
            request_id="req-life-route-001",
            session_id="qq_dm_1",
            person_id="user-1",
            user_message="昨晚说过要检查今日推荐任务",
            assistant_reply="我会看 scheduler 事件。",
            ts_ms=1_800_000_000_000,
        )

        cfg_result = load_recall_runtime_config(
            env={
                "RETR_RECALL_ENABLED": "true",
                "RETR_RECALL_DEFAULT_ROUTE": "route_life_episode",
                "RETR_RECALL_ROUTES_JSON": json.dumps(
                    {
                        "route_life_episode": {
                            "store_id": STORE_LIFE_EPISODE,
                            "top_k": 5,
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
            life_memory_store=store,
            runtime_config=cfg_result.config,
            node_id="host-01",
            available=True,
            use_hybrid=False,
        )
        query = "今日 推荐 scheduler"
        query_embedding, _ = embed_text(query)
        result = service.recall(
            request_id="req-life-route-recall",
            query=query,
            query_embedding=query_embedding,
            person_id="user-1",
            session_id="qq_dm_1",
            ts_ms=1_800_000_000_100,
        )

        assert result.success
        assert result.output["candidate_count"] >= 1
        top = result.output["candidates"][0]
        assert top["doc_id"] == written["episode_id"]
        assert top["metadata"]["store_id"] == STORE_LIFE_EPISODE
        assert top["metadata"]["evidence_count"] >= 2
        assert top["metadata"]["truth_state"] == "VERIFIED"
        assert top["evidence_refs"].startswith("chat://request/req-life-route-001")
    finally:
        ledger.close()


def test_anaphoric_selection_prefers_latest_three_item_delivery_over_recent_collection() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = LifeMemoryStore(ledger=ledger)
        latest = store.upsert_episode(
            episode_kind=EPISODE_ACTION,
            evidence_refs=["action://event/latest-delivery"],
            primary_ref="action://event/latest-delivery",
            person_id="user-1",
            session_id="qq_dm_1",
            action="lightnovel_recommendation_delivery",
            summary="我刚才给 qq_dm_1 推荐了 3 本轻小说：1. 新 A；2. 新 B；3. 新 C。",
            salience=0.7,
            retention_state="HOT",
            truth_state="VERIFIED",
            ts_ms=1_800_000_000_000,
            metadata={"action_kind": "lightnovel_recommendation_delivery"},
        )
        old = store.upsert_episode(
            episode_kind=EPISODE_ACTION,
            evidence_refs=["action://event/old-delivery"],
            primary_ref="action://event/old-delivery",
            person_id="user-1",
            session_id="qq_dm_1",
            action="lightnovel_recommendation_delivery",
            summary="较早推荐过旧书。",
            salience=0.5,
            retention_state="WARM",
            truth_state="VERIFIED",
            ts_ms=1_799_999_000_000,
            metadata={"action_kind": "lightnovel_recommendation_delivery"},
        )
        recent = store.upsert_collection(
            collection_kind="recommendation_collection",
            domain="lightnovel_recommendation",
            scope_key="recent",
            title="最近轻小说推荐集合",
            summary="最近轻小说推荐集合，记录 12 个条目。",
            person_id="user-1",
            session_id="qq_dm_1",
            ts_ms=1_800_000_000_050,
        )
        for index in range(1, 13):
            store.attach_collection_item(
                collection_id=recent["collection_id"],
                episode_id=old["episode_id"],
                item_key=f"old-{index}",
                item_title=f"旧书 {index}",
                item_summary="旧集合条目",
                source_ref=f"https://example.test/old/{index}",
                evidence_ref=f"action://event/old-delivery#{index}",
                position=index,
                ts_ms=1_800_000_000_050 + index,
            )

        query = "这三本书，你喜欢哪本？"
        query_embedding, _ = embed_text(query)
        cfg_result = load_recall_runtime_config(
            env={
                "RETR_RECALL_ENABLED": "true",
                "RETR_RECALL_DEFAULT_ROUTE": "route_life_collection",
                "RETR_RECALL_ROUTES_JSON": json.dumps(
                    {
                        "route_life_collection": {
                            "store_id": STORE_LIFE_MEMORY_COLLECTION,
                            "top_k": 5,
                            "score_weight": 1.2,
                            "node_selector": "*",
                        },
                        "route_life_episode": {
                            "store_id": STORE_LIFE_EPISODE,
                            "top_k": 5,
                            "score_weight": 1.0,
                            "node_selector": "*",
                        },
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
            life_memory_store=store,
            runtime_config=cfg_result.config,
            node_id="host-01",
            available=True,
            use_hybrid=False,
        )
        result = service.recall(
            request_id="req-life-route-current-three",
            query=query,
            query_embedding=query_embedding,
            person_id="user-1",
            session_id="qq_dm_1",
            ts_ms=1_800_000_000_500,
            time_lookback_days=1,
        )

        assert result.success
        candidates = result.output["candidates"]
        assert [item["doc_id"] for item in candidates] == [latest["episode_id"]]
        assert "新 A" in candidates[0]["snippet"]
        assert "旧书" not in candidates[0]["snippet"]
    finally:
        ledger.close()


def test_life_episode_route_still_runs_when_chroma_is_down(monkeypatch) -> None:  # noqa: ANN001
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = LifeMemoryStore(ledger=ledger)
        written = store.write_chat_episode(
            request_id="req-life-route-chroma-down",
            session_id="qq_dm_1",
            person_id="user-1",
            user_message="刚才给我推荐了三本轻小说",
            assistant_reply="我推荐了 A、B、C。",
            ts_ms=1_800_000_000_000,
        )
        cfg_result = load_recall_runtime_config(
            env={
                "RETR_RECALL_ENABLED": "true",
                "RETR_RECALL_DEFAULT_ROUTE": "route_life_episode",
                "RETR_RECALL_ROUTES_JSON": json.dumps(
                    {
                        "route_life_episode": {
                            "store_id": STORE_LIFE_EPISODE,
                            "top_k": 5,
                            "score_weight": 1.0,
                            "node_selector": "*",
                        },
                        "route_episodic": {
                            "store_id": STORE_EPISODIC_VECTOR,
                            "collection": "episodic",
                            "top_k": 5,
                            "score_weight": 1.0,
                            "node_selector": "*",
                        },
                    },
                    ensure_ascii=False,
                ),
            }
        )
        assert cfg_result.success, cfg_result.as_dict()
        assert cfg_result.config is not None
        monkeypatch.setattr(
            "host_brain.retrieval.recall_fusion.build_runtime_chroma_client",
            lambda: (_ for _ in ()).throw(RuntimeError("chroma is down")),
        )

        service = RecallFusionServiceContract(
            ledger=ledger,
            life_memory_store=store,
            runtime_config=cfg_result.config,
            node_id="host-01",
            available=True,
            use_hybrid=False,
        )
        query_embedding, _ = embed_text("三本轻小说 哪本")
        result = service.recall(
            request_id="req-life-route-chroma-down-recall",
            query="三本轻小说 哪本",
            query_embedding=query_embedding,
            person_id="user-1",
            session_id="qq_dm_1",
            ts_ms=1_800_000_000_100,
        )

        assert result.output["candidate_count"] >= 1
        assert result.output["candidates"][0]["doc_id"] == written["episode_id"]
        assert result.output["candidates"][0]["metadata"]["store_id"] == STORE_LIFE_EPISODE
        assert result.output["degraded"] is True
        assert any(
            row["store_id"] == STORE_EPISODIC_VECTOR and row["reason_code"] == CHROMA_ENDPOINT_UNAVAILABLE
            for row in result.output["route_rows"]
        )
    finally:
        ledger.close()


def test_anaphoric_selection_fusion_keeps_latest_delivery_not_old_recommendations_or_send_logs() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = LifeMemoryStore(ledger=ledger)
        old = store.upsert_episode(
            episode_kind=EPISODE_ACTION,
            evidence_refs=["action://event/old-job:lightnovel_gateway_delivery"],
            primary_ref="action://event/old-job:lightnovel_gateway_delivery",
            person_id="user-1",
            session_id="qq_dm_1",
            action="lightnovel_recommendation_delivery",
            summary="我昨天通过 qq 给 qq_dm_1 推荐了 3 本轻小说：1. 旧 A；2. 旧 B；3. 旧 C。",
            salience=0.8,
            retention_state="HOT",
            truth_state="VERIFIED",
            ts_ms=1_800_000_000_000,
            metadata={"action_kind": "lightnovel_recommendation_delivery"},
        )
        latest = store.upsert_episode(
            episode_kind=EPISODE_ACTION,
            evidence_refs=["action://event/new-job:lightnovel_gateway_delivery"],
            primary_ref="action://event/new-job:lightnovel_gateway_delivery",
            person_id="user-1",
            session_id="qq_dm_1",
            action="lightnovel_recommendation_delivery",
            summary=(
                "我刚才通过 qq 给 qq_dm_1 推荐了 3 本轻小说："
                "1. 新 A；2. 新 B；3. 新 C。"
            ),
            salience=0.65,
            retention_state="HOT",
            truth_state="VERIFIED",
            ts_ms=1_800_000_600_000,
            metadata={"action_kind": "lightnovel_recommendation_delivery"},
        )
        send_log = store.upsert_episode(
            episode_kind=EPISODE_ACTION,
            evidence_refs=["action://event/send-overview"],
            primary_ref="action://event/send-overview",
            person_id="user-1",
            session_id="qq_dm_1",
            action="qq_reply_sent",
            summary="gateway qq_reply_sent sent；内容「今日轻小说推荐来了，共 3 本。」",
            salience=0.9,
            retention_state="HOT",
            truth_state="VERIFIED",
            ts_ms=1_800_000_600_050,
            metadata={"action_kind": "qq_reply_sent"},
        )
        deep_review = store.upsert_episode(
            episode_kind=EPISODE_ACTION,
            evidence_refs=["action://event/deep-review"],
            primary_ref="action://event/deep-review",
            person_id="user-1",
            session_id="qq_dm_1",
            action="lightnovel_recommendation_deep_review",
            summary="当时更偏向第1本《新 A》，因为开篇更抓人；也对比了另外 1 本。",
            salience=0.95,
            retention_state="HOT",
            truth_state="VERIFIED",
            ts_ms=1_800_000_600_090,
            metadata={"action_kind": "lightnovel_recommendation_deep_review"},
        )

        query = "这三本书，你喜欢哪本？"
        query_embedding, _ = embed_text(query)
        cfg_result = load_recall_runtime_config(
            env={
                "RETR_RECALL_ENABLED": "true",
                "RETR_RECALL_DEFAULT_ROUTE": "route_life_episode",
                "RETR_RECALL_ROUTES_JSON": json.dumps(
                    {
                        "route_life_episode": {
                            "store_id": STORE_LIFE_EPISODE,
                            "top_k": 8,
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
            life_memory_store=store,
            runtime_config=cfg_result.config,
            node_id="host-01",
            available=True,
            use_hybrid=False,
        )
        result = service.recall(
            request_id="req-life-route-latest-delivery",
            query=query,
            query_embedding=query_embedding,
            person_id="user-1",
            session_id="qq_dm_1",
            ts_ms=1_800_000_600_500,
        )

        assert result.success
        candidates = result.output["candidates"]
        assert [item["doc_id"] for item in candidates] == [latest["episode_id"]]
        assert old["episode_id"] not in {item["doc_id"] for item in candidates}
        assert send_log["episode_id"] not in {item["doc_id"] for item in candidates}
        assert deep_review["episode_id"] not in {item["doc_id"] for item in candidates}
        assert "新 A" in candidates[0]["snippet"]
        assert candidates[0]["metadata"]["action"] == "lightnovel_recommendation_delivery"
    finally:
        ledger.close()


def test_deep_review_followup_prefers_review_episode_over_delivery() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = LifeMemoryStore(ledger=ledger)
        delivery = store.upsert_episode(
            episode_kind=EPISODE_ACTION,
            evidence_refs=["action://event/job-1:lightnovel_gateway_delivery"],
            primary_ref="action://event/job-1:lightnovel_gateway_delivery",
            person_id="user-1",
            session_id="qq_dm_1",
            action="lightnovel_recommendation_delivery",
            summary="我刚才通过 qq 给 qq_dm_1 推荐了 3 本轻小说：1. A；2. B；3. C。",
            salience=0.65,
            retention_state="HOT",
            truth_state="VERIFIED",
            ts_ms=1_800_000_000_000,
            metadata={"action_kind": "lightnovel_recommendation_delivery"},
        )
        review = store.upsert_episode(
            episode_kind=EPISODE_ACTION,
            evidence_refs=["action://event/deep-review-1", "https://www.lightnovel.fun/cn/detail/2"],
            primary_ref="action://event/deep-review-1",
            person_id="user-1",
            session_id="qq_dm_1",
            action="lightnovel_recommendation_deep_review",
            summary=(
                "网页深读结果（含正文）：我回去打开详情页和正文比较了这些轻小说，"
                "当前更推荐第2本《B》。原因：正文对白更轻快，人物互动更好接。"
            ),
            salience=0.9,
            retention_state="HOT",
            truth_state="VERIFIED",
            ts_ms=1_800_000_000_500,
            metadata={
                "action_kind": "lightnovel_recommendation_deep_review",
                "deep_review_cache_version": "lightnovel_deep_review:v4",
                "items": [
                    {
                        "index": 2,
                        "title": "B",
                        "detail_url": "https://www.lightnovel.fun/cn/detail/2",
                        "content_char_count": 1200,
                        "body_observations": ["正文对白更轻快，人物互动更好接。"],
                    }
                ],
            },
        )
        store.attach_evidence(
            episode_id=review["episode_id"],
            evidence_kind="tool_result",
            source_ref="https://www.lightnovel.fun/cn/detail/2",
            content="正文对白更轻快，人物互动更好接。",
            trust_level="VERIFIED",
            ts_ms=1_800_000_000_501,
        )

        query = "你刚才翻看正文后更推荐哪本？为什么？"
        query_embedding, _ = embed_text(query)
        cfg_result = load_recall_runtime_config(
            env={
                "RETR_RECALL_ENABLED": "true",
                "RETR_RECALL_DEFAULT_ROUTE": "route_life_episode",
                "RETR_RECALL_ROUTES_JSON": json.dumps(
                    {
                        "route_life_episode": {
                            "store_id": STORE_LIFE_EPISODE,
                            "top_k": 8,
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
            life_memory_store=store,
            runtime_config=cfg_result.config,
            node_id="host-01",
            available=True,
            use_hybrid=False,
        )
        result = service.recall(
            request_id="req-life-route-deep-review-followup",
            query=query,
            query_embedding=query_embedding,
            person_id="user-1",
            session_id="qq_dm_1",
            ts_ms=1_800_000_001_000,
        )

        assert result.success
        candidates = result.output["candidates"]
        assert [item["doc_id"] for item in candidates] == [review["episode_id"]]
        assert delivery["episode_id"] not in {item["doc_id"] for item in candidates}
        assert "网页深读结果" in candidates[0]["snippet"]
        assert "《B》" in candidates[0]["snippet"]
        assert candidates[0]["metadata"]["action"] == "lightnovel_recommendation_deep_review"
        assert "https://www.lightnovel.fun/cn/detail/2" in candidates[0]["metadata"]["evidence_refs"]
    finally:
        ledger.close()


def test_anaphoric_selection_fusion_prefers_life_action_and_drops_self_echo_noise() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = LifeMemoryStore(ledger=ledger)
        recommendation = store.upsert_episode(
            episode_kind=EPISODE_ACTION,
            evidence_refs=["action://event/job-1:lightnovel_gateway_delivery"],
            primary_ref="action://event/job-1:lightnovel_gateway_delivery",
            person_id="user-1",
            session_id="qq_dm_1",
            action="lightnovel_recommendation_delivery",
            summary=(
                "codex_production lightnovel_recommendation_delivery COMPLETED；"
                "我刚才通过 qq 给 qq_dm_1 推荐了 3 本轻小说：1. A；2. 1205.与神在河岸；3. C。"
            ),
            salience=0.65,
            retention_state="HOT",
            truth_state="VERIFIED",
            ts_ms=1_800_000_000_000,
            metadata={"action_kind": "lightnovel_recommendation_delivery"},
        )
        store.upsert_episode(
            episode_kind=EPISODE_BACKGROUND,
            evidence_refs=["scheduler://event/noise-emotion-decay"],
            primary_ref="scheduler://event/noise-emotion-decay",
            person_id="user-1",
            session_id="qq_dm_1",
            action="emotion_decay_tick",
            summary="scheduler emotion_decay_tick OK 后台任务记忆：emotion_decay_tick 状态=OK。",
            salience=0.8,
            retention_state="HOT",
            truth_state="VERIFIED",
            ts_ms=1_800_000_000_050,
        )
        store.write_chat_episode(
            request_id="req-self-echo",
            session_id="qq_dm_1",
            person_id="user-1",
            user_message="我问你喜欢哪本",
            assistant_reply="哈？最近在看一本讲宇宙和人的，挺有意思的。",
            ts_ms=1_800_000_000_100,
        )

        query = "我问你喜欢哪本"
        query_embedding, _ = embed_text(query)
        chroma = FakeChromaClient(collection_profiles={"episodic": len(query_embedding)})
        chroma.upsert(
            memory_id="episodic-self-echo",
            text="当前回合摘要：用户提到「我问你喜欢哪本」；回复「哈？最近在看一本讲宇宙和人的，挺有意思的。」。",
            embedding=query_embedding,
            metadata={"emotion_tag": "NEUTRAL"},
            collection="episodic",
        )
        cfg_result = load_recall_runtime_config(
            env={
                "RETR_RECALL_ENABLED": "true",
                "RETR_RECALL_DEFAULT_ROUTE": "route_life_episode",
                "RETR_RECALL_ROUTES_JSON": json.dumps(
                    {
                        "route_life_episode": {
                            "store_id": STORE_LIFE_EPISODE,
                            "top_k": 5,
                            "score_weight": 1.0,
                            "node_selector": "*",
                        },
                        "route_episodic": {
                            "store_id": STORE_EPISODIC_VECTOR,
                            "collection": "episodic",
                            "top_k": 5,
                            "score_weight": 2.0,
                            "node_selector": "*",
                        },
                    },
                    ensure_ascii=False,
                ),
            }
        )
        assert cfg_result.success, cfg_result.as_dict()
        assert cfg_result.config is not None

        service = RecallFusionServiceContract(
            chroma_client=chroma,
            ledger=ledger,
            life_memory_store=store,
            runtime_config=cfg_result.config,
            node_id="host-01",
            available=True,
            use_hybrid=False,
        )
        result = service.recall(
            request_id="req-life-route-anaphoric",
            query=query,
            query_embedding=query_embedding,
            person_id="user-1",
            session_id="qq_dm_1",
            ts_ms=1_800_000_000_500,
        )

        assert result.success
        candidates = result.output["candidates"]
        assert [item["doc_id"] for item in candidates] == [recommendation["episode_id"]]
        joined = "\n".join(item["snippet"] for item in candidates)
        assert "推荐了 3 本轻小说" in joined
        assert "最近在看一本讲宇宙" not in joined
        assert "emotion_decay_tick" not in joined
        payload = ledger.retrieval_event_log[-1]["payload"]
        preview = json.dumps(payload, ensure_ascii=False)
        assert "episodic-self-echo" not in preview
        assert "emotion_decay_tick" not in preview
    finally:
        ledger.close()
