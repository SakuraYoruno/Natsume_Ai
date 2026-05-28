from __future__ import annotations

from datetime import datetime, timezone, timedelta

from host_brain.app.observability_persist import persist_life_action_event_memory
from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.memory.life_memory import LifeMemoryStore
from host_brain.persona.isolated_surface_continuity import ThreeLayerContinuityContext
from host_brain.persona.surface_memory_recall import (
    build_surface_memory_recall_packet,
    plan_surface_memory_recall,
)
from host_brain.retrieval.recall_fusion import RecallFusionServiceContract
from host_brain.retrieval.recall_schemas import RecallRouteConfig, RecallRuntimeConfig
from tests.fakes.chroma_client import FakeChromaClient


CN_TZ = timezone(timedelta(hours=8))


def _ts(year: int, month: int, day: int, hour: int = 12) -> int:
    return int(datetime(year, month, day, hour, tzinfo=CN_TZ).timestamp() * 1000)


def _cards(prefix: str) -> list[dict[str, object]]:
    return [
        {
            "title": f"{prefix} A",
            "selection_reason": "恋爱日常",
            "intro_snippet": f"{prefix} 第一本文案。",
            "detail_url": f"https://www.lightnovel.fun/cn/detail/{prefix.lower()}-a",
        },
        {
            "title": f"{prefix} B",
            "selection_reason": "奇幻身份",
            "intro_snippet": f"{prefix} 第二本文案。",
            "detail_url": f"https://www.lightnovel.fun/cn/detail/{prefix.lower()}-b",
        },
        {
            "title": f"{prefix} C",
            "selection_reason": "皇女皇帝线",
            "intro_snippet": f"{prefix} 第三本文案。",
            "detail_url": f"https://www.lightnovel.fun/cn/detail/{prefix.lower()}-c",
        },
    ]


def _write_delivery(
    store: LifeMemoryStore,
    *,
    prefix: str,
    ts_ms: int,
    session_id: str = "qq_dm_owner",
    person_id: str = "owner",
) -> str:
    result = persist_life_action_event_memory(
        event_source="codex_production",
        action_kind="lightnovel_recommendation_delivery",
        action_id=f"job-{prefix}:lightnovel_gateway_delivery",
        actor_id="natsume",
        request_id=f"req-{prefix}",
        intent_id="codex_lightnovel_recommender",
        person_id=person_id,
        session_id=session_id,
        status="COMPLETED",
        action_summary=f"我给 {session_id} 推荐了 3 本轻小说：{prefix} A；{prefix} B；{prefix} C。",
        ts_ms=ts_ms,
        evidence_refs=[
            f"gateway://message/{prefix}/overview",
            f"gateway://message/{prefix}/card/1",
            f"gateway://message/{prefix}/card/2",
            f"gateway://message/{prefix}/card/3",
        ],
        life_memory_store=store,
        episodic_store=None,
        metadata={
            "platform": "qq",
            "conversation_id": session_id,
            "selected_recommendations": _cards(prefix),
        },
    )
    return str(result["life_memory"]["episode_id"])


def _collection_service(ledger: SQLiteLedger) -> RecallFusionServiceContract:
    node_id = "surface_main_chain_memory_recall"
    route = RecallRouteConfig(
        route_id="surface_life_collection",
        store_id="life_memory_collection",
        collection="life_memory_collection",
        top_k=5,
        score_weight=1.0,
        node_selector=node_id,
    )
    return RecallFusionServiceContract(
        ledger=ledger,
        chroma_client=FakeChromaClient(),
        runtime_config=RecallRuntimeConfig(
            enabled=True,
            default_route="surface_life_collection",
            routes={"surface_life_collection": route},
        ),
        node_id=node_id,
        use_hybrid=False,
    )


def test_recommendation_delivery_indexes_daily_and_recent_collections() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = LifeMemoryStore(ledger=ledger)
        yesterday_episode = _write_delivery(store, prefix="Yesterday", ts_ms=_ts(2026, 5, 27))
        _write_delivery(store, prefix="Today", ts_ms=_ts(2026, 5, 28))

        assert len(ledger.life_memory_collection) == 3
        assert len(ledger.life_memory_collection_item) == 12
        assert any(row["episode_id"] == yesterday_episode for row in ledger.life_memory_collection_item)
        recent_collection = next(row for row in ledger.life_memory_collection if row["scope_key"] == "recent")
        assert recent_collection["item_count"] == 6
        assert "记录 6 个条目" in recent_collection["summary"]
        assert "Yesterday A" in recent_collection["summary"]
        assert "Today C" in recent_collection["summary"]

        packet = store.recall_collection_packet(
            query="昨天三本呢",
            top_k=2,
            person_id="owner",
            session_id="qq_dm_owner",
            now_ts_ms_value=_ts(2026, 5, 28, 18),
        )

        assert packet.candidates
        top = packet.candidates[0]
        assert top["metadata"]["store_id"] == "life_memory_collection"
        assert top["metadata"]["scope_key"] == "daily:2026-05-27"
        assert "Yesterday A" in top["text"]
        assert "Today A" not in top["text"]
    finally:
        ledger.close()


def test_recent_days_collection_recalls_items_across_multiple_delivery_episodes() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = LifeMemoryStore(ledger=ledger)
        _write_delivery(store, prefix="DayBefore", ts_ms=_ts(2026, 5, 26))
        _write_delivery(store, prefix="Yesterday", ts_ms=_ts(2026, 5, 27))
        _write_delivery(store, prefix="Today", ts_ms=_ts(2026, 5, 28))

        packet = store.recall_collection_packet(
            query="这几天推荐过哪些轻小说",
            top_k=1,
            person_id="owner",
            session_id="qq_dm_owner",
            now_ts_ms_value=_ts(2026, 5, 28, 18),
        )

        assert packet.candidates
        top = packet.candidates[0]
        assert top["metadata"]["scope_key"] == "recent"
        assert top["metadata"]["item_count"] == 9
        assert "最近轻小说推荐集合，记录 9 个条目" in top["text"]
        assert "DayBefore A" in top["text"]
        assert "Yesterday B" in top["text"]
        assert "Today C" in top["text"]
    finally:
        ledger.close()


def test_collection_recall_falls_back_to_default_owner_scope_when_session_alias_misses() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = LifeMemoryStore(ledger=ledger)
        _write_delivery(
            store,
            prefix="RealQQ",
            ts_ms=_ts(2026, 5, 28),
            session_id="qq_dm_3414578517",
            person_id="default",
        )

        packet = store.recall_collection_packet(
            query="昨天三本呢",
            top_k=2,
            person_id="owner",
            session_id="qq_dm_owner_alias",
            now_ts_ms_value=_ts(2026, 5, 29, 8),
        )

        assert packet.candidates
        top = packet.candidates[0]
        assert top["metadata"]["scope_key"] == "daily:2026-05-28"
        assert top["metadata"]["item_count"] == 3
        assert "RealQQ A" in top["text"]
    finally:
        ledger.close()


def test_surface_plan_routes_date_and_collection_queries_to_collection_first() -> None:
    plan = plan_surface_memory_recall(
        ThreeLayerContinuityContext(
            current_user_message="昨天三本呢？",
            task_mode_hint="daily_chat",
        )
    )

    assert plan.should_recall
    assert plan.reason == "collection_memory_recall"
    assert plan.query_kind == "date_lookup"
    assert plan.temporal_scope == "yesterday"
    assert plan.target_store_ids[0] == "life_memory_collection"
    assert "collection_items_present" in plan.validation_requirements


def test_surface_plan_routes_many_book_listing_to_recent_collection() -> None:
    plan = plan_surface_memory_recall(
        ThreeLayerContinuityContext(
            current_user_message="这十几本里有哪些？",
            task_mode_hint="daily_chat",
        )
    )

    assert plan.should_recall
    assert plan.reason == "collection_memory_recall"
    assert plan.query_kind == "collection_summary"
    assert plan.temporal_scope == "recent_days"
    assert plan.target_store_ids[0] == "life_memory_collection"


def test_recall_fusion_collection_route_builds_surface_packet_with_items() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = LifeMemoryStore(ledger=ledger)
        _write_delivery(store, prefix="Yesterday", ts_ms=_ts(2026, 5, 27))
        result = _collection_service(ledger).recall(
            request_id="req-recall-yesterday",
            query="昨天三本呢？",
            query_embedding=[0.1] * 16,
            person_id="owner",
            session_id="qq_dm_owner",
            ts_ms=_ts(2026, 5, 28, 18),
            target_store_ids=["life_memory_collection"],
        )

        packet = build_surface_memory_recall_packet(result, query="昨天三本呢？", max_facts=4, max_chars_per_fact=900)
        joined = "\n".join(packet.facts)

        assert packet.route_ids == ("surface_life_collection",)
        assert packet.rows[0].store_id == "life_memory_collection"
        assert "集合记忆" in joined
        assert "Yesterday A" in joined
        assert "可比较证据" in joined
        assert packet.evidence_refs
    finally:
        ledger.close()


def test_surface_packet_keeps_many_recent_collection_items() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = LifeMemoryStore(ledger=ledger)
        _write_delivery(store, prefix="DayBefore", ts_ms=_ts(2026, 5, 26))
        _write_delivery(store, prefix="Yesterday", ts_ms=_ts(2026, 5, 27))
        _write_delivery(store, prefix="Today", ts_ms=_ts(2026, 5, 28))
        result = _collection_service(ledger).recall(
            request_id="req-recall-recent",
            query="这几天推荐过哪些轻小说？",
            query_embedding=[0.1] * 16,
            person_id="owner",
            session_id="qq_dm_owner",
            ts_ms=_ts(2026, 5, 28, 18),
            target_store_ids=["life_memory_collection"],
        )

        packet = build_surface_memory_recall_packet(
            result,
            query="这几天推荐过哪些轻小说？",
            max_facts=4,
            max_chars_per_fact=900,
            temporal_scope="recent_days",
            validation_requirements=("scope_match", "collection_items_present", "evidence_refs_present"),
        )
        joined = "\n".join(packet.facts)

        assert packet.reason_code is None
        assert "最近轻小说推荐集合，记录 9 个条目" in joined
        assert "DayBefore A" in joined
        assert "Yesterday C" in joined
        assert "Today C" in joined
        assert "详情：" not in joined
        trace_items = list((packet.trace or {}).get("selected_collection_items") or [])
        assert len(trace_items) == 9
        assert any(item["title"] == "DayBefore A" and item["detail_url"].endswith("daybefore-a") for item in trace_items)
    finally:
        ledger.close()
