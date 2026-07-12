from __future__ import annotations

import pytest

from host_brain.app import chat_retrieval_helpers as helpers
from host_brain.app.agent_runtime_shared import _surface_memory_debug_trace_for_audit
from host_brain.app.observability_persist import persist_life_action_event_memory
from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.memory.life_memory import LifeMemoryStore
from host_brain.retrieval.recall_fusion import RecallFusionServiceContract
from host_brain.retrieval.recall_schemas import RecallRouteConfig, RecallRuntimeConfig
from tests.fakes.chroma_client import FakeChromaClient


class _FakeEpisodicStore:
    def write_memory(self, **_kwargs):  # noqa: ANN003
        class _Result:
            success = True
            reason_code = None
            memory_id = "mem-lightnovel-e2e"
            detail = "ok"

        return _Result()


def _surface_life_episode_recall_service(ledger: SQLiteLedger) -> RecallFusionServiceContract:
    node_id = "surface_main_chain_memory_recall"
    route = RecallRouteConfig(
        route_id="surface_life_episode",
        store_id="life_episode",
        collection="life_episode",
        top_k=6,
        score_weight=1.25,
        node_selector=node_id,
    )
    return RecallFusionServiceContract(
        ledger=ledger,
        chroma_client=FakeChromaClient(),
        runtime_config=RecallRuntimeConfig(
            enabled=True,
            default_route="surface_life_episode",
            routes={"surface_life_episode": route},
        ),
        node_id=node_id,
        use_hybrid=False,
    )


def _recommendation_cards() -> list[dict[str, object]]:
    return [
        {
            "title": "恋爱日常 A",
            "selection_reason": "恋爱日常",
            "intro_snippet": "前美少女童星和青梅竹马的轻松关系。",
            "detail_url": "https://www.lightnovel.fun/cn/detail/1001",
        },
        {
            "title": "与神在河岸",
            "selection_reason": "奇幻身份钩子",
            "intro_snippet": "河岸边遇见神明，偏轻喜剧。",
            "detail_url": "https://www.lightnovel.fun/cn/detail/1002",
        },
        {
            "title": "異世界皇女皇帝",
            "selection_reason": "皇女/皇帝线很直给",
            "intro_snippet": "转移到异世界后，和美少女皇女结婚并卷进帝国路线。",
            "detail_url": "https://www.lightnovel.fun/cn/detail/1003",
        },
    ]


@pytest.mark.asyncio
async def test_daily_lightnovel_delivery_is_recalled_deep_reviewed_and_traceable(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv("HOST_LIGHTNOVEL_DEEP_REVIEW_CACHE_ENABLED", "0")
    ledger = SQLiteLedger(db_path=":memory:", check_same_thread=False)
    try:
        life_store = LifeMemoryStore(ledger=ledger)
        episodic_store = _FakeEpisodicStore()
        cards = _recommendation_cards()
        delivery = persist_life_action_event_memory(
            event_source="codex_production",
            action_kind="lightnovel_recommendation_delivery",
            action_id="job-e2e:lightnovel_gateway_delivery",
            actor_id="natsume",
            request_id="req-lightnovel-delivery",
            intent_id="agent_lightnovel_recommender",
            person_id="owner",
            session_id="qq_dm_owner",
            status="COMPLETED",
            action_summary=(
                "我刚才通过 qq 给 qq_dm_owner 推荐了 3 本轻小说："
                "1. 恋爱日常 A；2. 与神在河岸；3. 異世界皇女皇帝。"
            ),
            ts_ms=1_800_000_000_000,
            evidence_refs=[
                "gateway://message/job-e2e/lightnovel/overview",
                "gateway://message/job-e2e/lightnovel/card/1",
                "gateway://message/job-e2e/lightnovel/card/2",
                "gateway://message/job-e2e/lightnovel/card/3",
            ],
            life_memory_store=life_store,
            episodic_store=episodic_store,
            metadata={
                "platform": "qq",
                "conversation_id": "qq_dm_owner",
                "selected_recommendations": cards,
            },
        )

        assert delivery["life_memory"]["status"] == "OK"
        delivery_episode_id = delivery["life_memory"]["episode_id"]
        delivery_rows = [row for row in ledger.life_episode if row["episode_id"] == delivery_episode_id]
        assert delivery_rows and delivery_rows[0]["action"] == "lightnovel_recommendation_delivery"
        delivery_meta = delivery_rows[0]["metadata"]
        assert delivery_meta["selected_recommendations"][2]["title"] == "異世界皇女皇帝"
        assert any(
            row["episode_id"] == delivery_episode_id
            and row["source_ref"] == "action://event/job-e2e:lightnovel_gateway_delivery"
            for row in ledger.life_evidence
        )

        captured_cards: list[dict[str, object]] = []

        def fake_review_cards(review_cards, *, timeout_s):  # noqa: ANN001
            captured_cards.extend(dict(item) for item in review_cards)
            return [
                    {
                        **dict(review_cards[0]),
                        "review_status": "OK",
                        "body_quality": "READABLE_BODY",
                        "content_char_count": 1200,
                        "body_observations": ["读到的正文偏恋爱日常，核心在人物关系和亲密感推进"],
                        "deep_review_score": 25.0,
                        "score_reasons": ["恋爱日常信号明确", "读到了正文内容"],
                    },
                    {
                        **dict(review_cards[1]),
                        "review_status": "OK",
                        "body_quality": "READABLE_BODY",
                        "content_char_count": 1400,
                        "body_observations": ["正文主要靠对话和日常梗推进，轻喜剧感比较明显"],
                        "deep_review_score": 29.0,
                        "score_reasons": ["奇幻身份钩子明确", "有正文观察"],
                    },
                {
                        **dict(review_cards[2]),
                        "review_status": "OK",
                        "body_quality": "READABLE_BODY",
                        "content_char_count": 2200,
                        "body_observations": [
                            "正文/简介有异世界皇女、皇帝或后宫路线，世界观钩子更直给",
                            "对白密度高，读感更偏轻快互动",
                    ],
                    "chapter_signal": "序章；第一章",
                    "deep_review_score": 42.0,
                    "score_reasons": ["奇幻身份钩子明确", "正文体量充足"],
                },
            ]

        monkeypatch.setattr(
            helpers,
            "_build_surface_memory_recall_service",
            lambda: _surface_life_episode_recall_service(ledger),
        )
        monkeypatch.setattr(helpers, "_embed_surface_memory_query", lambda query: ([0.2] * 16, "test-embedding"))
        monkeypatch.setattr(
            "host_brain.agent_runtime.lightnovel_deep_reviewer._life_memory_store_provider",
            lambda: life_store,
        )
        monkeypatch.setattr("host_brain.app.dependencies.get_episodic_store", lambda: episodic_store)
        monkeypatch.setattr("host_brain.agent_runtime.lightnovel_deep_reviewer._review_cards", fake_review_cards)

        retrieval_summary, evidence_items = await helpers._run_retrieval(
            request_id="req-qq-followup",
            message="这三本书，你喜欢哪本？",
            person_id="owner",
            session_id="qq_dm_owner",
            ts_ms=1_800_000_000_500,
            emotion_tag="NEUTRAL",
            render_target_lang="zh",
            intent_draft={"route": "chat"},
        )

        packet = retrieval_summary["surface_memory_recall_packet"]
        fact = packet["facts"][0]
        assert evidence_items == []
        assert retrieval_summary["status"] == "OK"
        assert retrieval_summary["query_plan"]["reason"] == "anaphoric_selection_recall"
        assert "life_episode" in retrieval_summary["target_stores"]
        assert "surface_life_episode" in packet["route_ids"]
        assert packet["rows"][0]["store_id"] == "life_episode"
        assert fact.startswith("网页深读结果（含正文）")
        expected_review_outcomes = (
            ("第2本", "与神在河岸", "河岸边遇见神明"),
            ("第3本", "異世界皇女皇帝", "美少女皇女"),
        )
        assert any(
            f"当前更推荐{rank}" in fact and title in fact and detail in fact
            for rank, title, detail in expected_review_outcomes
        )
        assert "正文内容足够支撑判断" not in fact
        assert "正文有" not in fact
        assert "对白" in fact
        captured_urls = [item["detail_url"] for item in captured_cards]
        assert "https://www.lightnovel.fun/cn/detail/1002" in captured_urls
        assert any(
            url in captured_urls
            for url in (
                "https://www.lightnovel.fun/detail/1145001",
                "https://www.lightnovel.fun/cn/detail/1003",
            )
        )

        deep_trace = packet["trace"]["lightnovel_deep_review"]
        assert deep_trace["status"] == "OK"
        assert deep_trace["persist_status"] == "OK"
        assert deep_trace["persisted_episode_id"]
        assert deep_trace["evidence_ref_count"] == 3

        deep_rows = [
            row for row in ledger.life_episode
            if row["action"] == "lightnovel_recommendation_deep_review"
        ]
        assert deep_rows
        assert deep_rows[0]["person_id"] == "owner"
        assert deep_rows[0]["session_id"] == "qq_dm_owner"
        assert any(rank in deep_rows[0]["summary"] for rank, _title, _detail in expected_review_outcomes)

        debug_trace = _surface_memory_debug_trace_for_audit(
            retrieval_summary=retrieval_summary,
            retrieval_evidence_items=evidence_items,
            trace_stage_meta={
                "conversation_history": {
                    "turns_injected": 6,
                    "fetched_turns": 8,
                    "older_turn_count": 12,
                    "summary_present": True,
                }
            },
            delegated_agents=[
                {
                    "agent_id": "retrieval_agent",
                    "success": True,
                    "latency_ms": 31,
                    "audit_refs": {
                        "agent_context": {
                            "conversation_turns": 3,
                            "memory_fact_count": 4,
                            "evidence_item_count": 8,
                            "trace": {"context_profile": "retrieval"},
                        }
                    },
                }
            ],
        )
        assert debug_trace["history_window"]["turns_injected"] == 6
        assert debug_trace["episode_recall_packet"]["life_episode_row_count"] >= 1
        assert debug_trace["episode_recall_packet"]["rows"][0]["store_id"] == "life_episode"
        assert debug_trace["evidence_pack"]["item_count"] >= 1
        assert debug_trace["sub_agent_envelope"]["agents"][0]["context_profile"] == "retrieval"
    finally:
        ledger.close()
