from __future__ import annotations

from datetime import datetime, timedelta, timezone

from host_brain.app.observability_persist import persist_life_action_event_memory
from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.memory.life_memory import LifeMemoryConsolidator, LifeMemoryStore
from host_brain.persona.isolated_surface_continuity import ThreeLayerContinuityContext
from host_brain.persona.surface_memory_recall import plan_surface_memory_recall, recall_surface_memory_for_context
from host_brain.retrieval.recall_fusion import RecallFusionServiceContract
from host_brain.retrieval.recall_schemas import RecallRouteConfig, RecallRuntimeConfig
from tests.fakes.chroma_client import FakeChromaClient


CN_TZ = timezone(timedelta(hours=8))


def _ts(day: int, hour: int = 12) -> int:
    return int(datetime(2026, 5, day, hour, tzinfo=CN_TZ).timestamp() * 1000)


def _cards(day: int) -> list[dict[str, object]]:
    return [
        {
            "title": f"5月{day}日推荐 A",
            "selection_reason": "恋爱日常",
            "intro_snippet": "人物关系推进比较轻。",
            "body_excerpt": "正文片段：开场围绕日常关系推进，人物互动轻，适合作为轻松向选择。",
            "detail_url": f"https://www.lightnovel.fun/cn/detail/{day}01",
        },
        {
            "title": f"5月{day}日推荐 B",
            "selection_reason": "奇幻身份钩子",
            "intro_snippet": "设定比较新鲜。",
            "body_excerpt": "正文片段：主角身份钩子很快抛出，奇幻设定比普通日常更有新鲜感。",
            "detail_url": f"https://www.lightnovel.fun/cn/detail/{day}02",
        },
        {
            "title": f"5月{day}日推荐 C",
            "selection_reason": "皇女皇帝线",
            "intro_snippet": "路线更直给。",
            "body_excerpt": "正文片段：皇女和皇帝线索直接进入冲突，路线清晰，推进更干脆。",
            "detail_url": f"https://www.lightnovel.fun/cn/detail/{day}03",
        },
    ]


def _seed_recommendation(store: LifeMemoryStore, *, day: int) -> None:
    persist_life_action_event_memory(
        event_source="codex_production",
        action_kind="lightnovel_recommendation_delivery",
        action_id=f"job-202605{day}:lightnovel_gateway_delivery",
        actor_id="natsume",
        request_id=f"req-rec-{day}",
        intent_id="agent_lightnovel_recommender",
        person_id="owner",
        session_id="qq_dm_owner",
        status="COMPLETED",
        action_summary=f"我在 5 月 {day} 日给 qq_dm_owner 推荐了 3 本轻小说。",
        ts_ms=_ts(day),
        evidence_refs=[f"gateway://message/202605{day}/card/{index}" for index in range(1, 4)],
        life_memory_store=store,
        episodic_store=None,
        metadata={
            "platform": "qq",
            "conversation_id": "qq_dm_owner",
            "selected_recommendations": _cards(day),
        },
    )


def _service(ledger: SQLiteLedger) -> RecallFusionServiceContract:
    node_id = "surface_main_chain_memory_recall"
    routes = {
        "surface_life_collection": RecallRouteConfig(
            route_id="surface_life_collection",
            store_id="life_memory_collection",
            collection="life_memory_collection",
            top_k=5,
            score_weight=1.25,
            node_selector=node_id,
        ),
        "surface_life_episode": RecallRouteConfig(
            route_id="surface_life_episode",
            store_id="life_episode",
            collection="life_episode",
            top_k=8,
            score_weight=1.0,
            node_selector=node_id,
        ),
        "surface_semantic_memory": RecallRouteConfig(
            route_id="surface_semantic_memory",
            store_id="semantic_memory",
            collection="semantic_memory",
            top_k=5,
            score_weight=1.15,
            node_selector=node_id,
        ),
    }
    return RecallFusionServiceContract(
        ledger=ledger,
        chroma_client=FakeChromaClient(),
        runtime_config=RecallRuntimeConfig(
            enabled=True,
            default_route="surface_life_collection",
            routes=routes,
        ),
        node_id=node_id,
        use_hybrid=False,
    )


def test_batch_recall_scenarios_route_to_the_right_memory_layer() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = LifeMemoryStore(ledger=ledger)
        for day in (26, 27, 28):
            _seed_recommendation(store, day=day)
        failure = store.write_background_episode(
            event_source="codex_production",
            event_id="deepseek-failure-1",
            person_id="owner",
            session_id="qq_dm_owner",
            task_id="llm_pool_call",
            status="FAILED",
            reason_code="DEEPSEEK_TIMEOUT",
            detail="DeepSeek 失败原因：供应商超时，后续优先检查网络和 provider 健康状态。",
            ts_ms=_ts(28, 13),
        )
        preference = store.write_chat_episode(
            request_id="req-pref-format",
            session_id="qq_dm_owner",
            person_id="owner",
            user_message="以后推荐轻小说时，每本书的简介后应该换行，别模板腔。",
            assistant_reply="记住了，后面按更自然的卡片格式发。",
            ts_ms=_ts(28, 14),
        )
        wrong = store.write_chat_episode(
            request_id="req-corrected-wrong-cat-cover",
            session_id="qq_dm_owner",
            person_id="owner",
            user_message="刚才那三本里你选哪本？",
            assistant_reply="我选封面有猫的那本。",
            ts_ms=_ts(28, 15),
        )
        correction = store.write_chat_episode(
            request_id="req-corrected-fix-cat-cover",
            session_id="qq_dm_owner",
            person_id="owner",
            user_message="不对，没有封面有猫这本。",
            assistant_reply="你说得对，我把这个判断撤回。",
            ts_ms=_ts(28, 16),
        )
        store.correct_episode(
            episode_id=wrong["episode_id"],
            correction_kind="correction",
            correction_episode_id=correction["episode_id"],
            reason_code="USER_CORRECTED_FALSE_MEMORY",
            detail="用户指出没有封面有猫这本。",
            suppress=False,
            ts_ms=_ts(28, 17),
        )
        LifeMemoryConsolidator(store=store).distill_semantic_memories(
            limit=80,
            max_records=24,
            ts_ms=_ts(28, 17) + 1,
        )

        cases = [
            {
                "message": "这三本书，你喜欢哪本？",
                "expected_reason": "anaphoric_selection_recall",
                "expected_kind": "anaphoric_selection",
                "must_route": "surface_life_collection",
                "must_contain": "5月28日推荐",
                "must_not_contain": "DeepSeek",
            },
            {
                "message": "昨天三本呢？",
                "expected_reason": "collection_memory_recall",
                "expected_kind": "date_lookup",
                "must_route": "surface_life_collection",
                "must_contain": "5月27日推荐",
                "must_not_contain": "5月28日推荐",
            },
            {
                "message": "这几天推荐过哪些轻小说？",
                "expected_reason": "collection_memory_recall",
                "expected_kind": "collection_summary",
                "must_route": "surface_life_collection",
                "must_contain": "5月26日推荐",
                "must_not_contain": "DeepSeek",
            },
            {
                "message": "DeepSeek失败原因是什么？",
                "expected_reason": "failure_debug_recall",
                "expected_kind": "failure_debug",
                "must_route": "surface_life_episode",
                "must_contain": "供应商超时",
                "must_not_contain": "5月27日推荐",
            },
            {
                "message": "还记得我说推荐格式要怎样吗？",
                "expected_reason": "explicit_memory_recall",
                "expected_kind": "preference_lookup",
                "must_route": "surface_semantic_memory",
                "must_contain": "简介后应该换行",
                "must_not_contain": "5月28日推荐",
                "min_coverage": 1.0,
            },
            {
                "message": "你刚才是不是记错了封面猫那本？",
                "expected_reason": "correction_recall",
                "expected_kind": "correction",
                "must_route": "surface_life_episode",
                "must_contain": "没有封面有猫",
                "must_not_contain": "我选封面有猫",
            },
        ]

        for index, case in enumerate(cases):
            plan = plan_surface_memory_recall(
                ThreeLayerContinuityContext(
                    current_user_message=str(case["message"]),
                    task_mode_hint="daily_chat",
                )
            )
            packet = recall_surface_memory_for_context(
                recall_service=_service(ledger),
                context=ThreeLayerContinuityContext(
                    current_user_message=str(case["message"]),
                    task_mode_hint="daily_chat",
                ),
                request_id=f"req-batch-{index}",
                query_embedding=[0.1] * 16,
                person_id="owner",
                session_id="qq_dm_owner",
                ts_ms=_ts(28, 18),
            )
            joined = "\n".join(packet.facts)

            assert plan.reason == case["expected_reason"]
            assert plan.query_kind == case["expected_kind"]
            assert case["must_route"] in packet.route_ids
            assert str(case["must_contain"]) in joined
            assert str(case["must_not_contain"]) not in joined
            assert packet.evidence_refs
            validation = dict(dict(packet.trace or {}).get("validation") or {})
            assert validation, case["message"]
            assert validation["required"] == list(plan.validation_requirements)
            assert validation["passed"] is True, validation
            assert validation["missing"] == []
            need = dict(dict(packet.trace or {}).get("recall_need") or {})
            coverage = dict(dict(packet.trace or {}).get("recall_coverage") or {})
            assert need["recall_contract_id"] == plan.recall_contract_id
            assert need["query_kind"] == plan.query_kind
            assert coverage["passed"] is True
            assert coverage["score"] >= float(case.get("min_coverage", 0.66))

        assert failure["collection_index"]["status"] == "SKIPPED"
        assert preference["episode_id"]
    finally:
        ledger.close()
