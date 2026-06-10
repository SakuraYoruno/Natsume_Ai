from __future__ import annotations

from datetime import datetime, timezone, timedelta
import json

from host_brain.app.observability_persist import persist_life_action_event_memory
from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.memory.life_memory import LifeMemoryStore
from host_brain.memory.life_memory_query import plan_life_memory_query
from host_brain.persona.isolated_surface_continuity import ThreeLayerContinuityContext
from host_brain.persona.surface_memory_recall import (
    build_surface_memory_recall_packet,
    plan_surface_memory_recall,
    recall_surface_memory_for_context,
)
from host_brain.retrieval.recall_fusion import RecallFusionServiceContract
from host_brain.retrieval.recall_schemas import RecallRouteConfig, RecallRuntimeConfig
from tests.fakes.chroma_client import FakeChromaClient


CN_TZ = timezone(timedelta(hours=8))


def _ts(year: int, month: int, day: int, hour: int = 12) -> int:
    return int(datetime(year, month, day, hour, tzinfo=CN_TZ).timestamp() * 1000)


def _assert_detail_anchor_not_ready(packet) -> None:  # noqa: ANN001
    trace = dict(packet.trace or {})
    readiness = dict(trace.get("recall_answer_readiness") or {})
    strategy = dict(trace.get("recall_answer_strategy") or {})
    detail = dict(readiness.get("detail_evidence") or {})
    assert packet.reason_code == "SURFACE_MEMORY_RECALL_COVERAGE_FAILED"
    assert readiness["status"] == "NOT_READY"
    assert strategy["action"] == "fetch_more_evidence"
    assert detail["basis"] == "detail_anchor"


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
    metadata_extra: dict[str, object] | None = None,
) -> str:
    result = persist_life_action_event_memory(
        event_source="codex_production",
        action_kind="lightnovel_recommendation_delivery",
        action_id=f"job-{prefix}:lightnovel_gateway_delivery",
        actor_id="natsume",
        request_id=f"req-{prefix}",
        intent_id="agent_lightnovel_recommender",
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
            **dict(metadata_extra or {}),
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


def _entity_service(ledger: SQLiteLedger) -> RecallFusionServiceContract:
    node_id = "surface_main_chain_memory_recall"
    route = RecallRouteConfig(
        route_id="surface_life_entity",
        store_id="life_memory_entity",
        collection="life_memory_entity",
        top_k=5,
        score_weight=1.0,
        node_selector=node_id,
    )
    return RecallFusionServiceContract(
        ledger=ledger,
        chroma_client=FakeChromaClient(),
        runtime_config=RecallRuntimeConfig(
            enabled=True,
            default_route="surface_life_entity",
            routes={"surface_life_entity": route},
        ),
        node_id=node_id,
        use_hybrid=False,
    )


def _collection_entity_service(ledger: SQLiteLedger) -> RecallFusionServiceContract:
    node_id = "surface_main_chain_memory_recall"
    routes = {
        "surface_life_collection": RecallRouteConfig(
            route_id="surface_life_collection",
            store_id="life_memory_collection",
            collection="life_memory_collection",
            top_k=5,
            score_weight=1.0,
            node_selector=node_id,
        ),
        "surface_life_entity": RecallRouteConfig(
            route_id="surface_life_entity",
            store_id="life_memory_entity",
            collection="life_memory_entity",
            top_k=5,
            score_weight=1.0,
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


def _collection_episode_service(ledger: SQLiteLedger) -> RecallFusionServiceContract:
    node_id = "surface_main_chain_memory_recall"
    routes = {
        "surface_life_collection": RecallRouteConfig(
            route_id="surface_life_collection",
            store_id="life_memory_collection",
            collection="life_memory_collection",
            top_k=5,
            score_weight=1.0,
            node_selector=node_id,
        ),
        "surface_life_entity": RecallRouteConfig(
            route_id="surface_life_entity",
            store_id="life_memory_entity",
            collection="life_memory_entity",
            top_k=5,
            score_weight=1.0,
            node_selector=node_id,
        ),
        "surface_life_episode": RecallRouteConfig(
            route_id="surface_life_episode",
            store_id="life_episode",
            collection="life_episode",
            top_k=5,
            score_weight=1.0,
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


def test_recommendation_delivery_indexes_delivery_daily_and_recent_collections() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = LifeMemoryStore(ledger=ledger)
        yesterday_episode = _write_delivery(store, prefix="Yesterday", ts_ms=_ts(2026, 5, 27))
        _write_delivery(store, prefix="Today", ts_ms=_ts(2026, 5, 28))

        assert len(ledger.life_memory_collection) == 8
        assert len(ledger.life_memory_collection_item) == 36
        assert len(ledger.life_memory_entity) == 6
        assert len(ledger.life_memory_entity_mention) == 6
        assert any(row["episode_id"] == yesterday_episode for row in ledger.life_memory_collection_item)
        delivery_collection = next(row for row in ledger.life_memory_collection if row["scope_key"] == f"delivery:{yesterday_episode}")
        assert delivery_collection["item_count"] == 3
        assert "本次轻小说推荐集合，记录 3 个条目" in delivery_collection["summary"]
        recent_collection = next(row for row in ledger.life_memory_collection if row["scope_key"] == "recent")
        assert recent_collection["item_count"] == 6
        assert "记录 6 个条目" in recent_collection["summary"]
        assert "Yesterday A" in recent_collection["summary"]
        assert "Today C" in recent_collection["summary"]
        rolling_collection = next(row for row in ledger.life_memory_collection if row["scope_key"] == "rolling:7d")
        assert rolling_collection["item_count"] == 6
        assert "近 7 天轻小说推荐集合" in rolling_collection["summary"]

        packet = store.recall_collection_packet(
            query="昨天推荐过哪些轻小说",
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


def test_current_selection_recalls_exact_delivery_collection_before_recent_collection() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = LifeMemoryStore(ledger=ledger)
        _write_delivery(store, prefix="Old", ts_ms=_ts(2026, 5, 27))
        latest_episode = _write_delivery(store, prefix="Latest", ts_ms=_ts(2026, 5, 28))

        packet = recall_surface_memory_for_context(
            recall_service=_collection_service(ledger),
            context=ThreeLayerContinuityContext(
                current_user_message="这三本书，你喜欢哪本？",
                task_mode_hint="daily_chat",
            ),
            request_id="req-current-three-delivery",
            query_embedding=[0.1] * 16,
            person_id="owner",
            session_id="qq_dm_owner",
            ts_ms=_ts(2026, 5, 28, 18),
        )

        joined = "\n".join(row.fact for row in packet.rows)
        validation = dict(dict(packet.trace or {}).get("validation") or {})
        assert validation["passed"] is True
        _assert_detail_anchor_not_ready(packet)
        assert "Latest A" in joined
        assert "Old A" not in joined
        latest_collection = next(row for row in ledger.life_memory_collection if row["scope_key"] == f"delivery:{latest_episode}")
        assert packet.rows[0].source_ref == f"life_memory_collection://{latest_collection['collection_id']}"
        assert list((packet.trace or {}).get("selected_collection_items") or [])[0]["title"] == "Latest A"
    finally:
        ledger.close()


def test_current_recommendation_listing_plans_collection_recall_not_selection() -> None:
    life_plan = plan_life_memory_query("刚才给我推荐了哪几本书？")
    surface_plan = plan_surface_memory_recall(
        ThreeLayerContinuityContext(
            current_user_message="刚才给我推荐了哪几本书？",
            task_mode_hint="daily_chat",
        )
    )

    assert life_plan.reason == "collection_memory_recall"
    assert life_plan.query_kind == "collection_summary"
    assert life_plan.temporal_scope == "current"
    assert life_plan.primary_memory_layer == "life_memory_collection"
    assert "collection_item_exact_count" in life_plan.validation_requirements
    assert "read_detail_evidence_before_deep_quality_judgment" not in life_plan.answer_obligations
    assert surface_plan.reason == "collection_memory_recall"
    assert surface_plan.query_kind == "collection_summary"


def test_current_listing_recall_does_not_leak_synthetic_or_other_session_episode() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = LifeMemoryStore(ledger=ledger)
        _write_delivery(
            store,
            prefix="SeededE2E",
            ts_ms=_ts(2026, 5, 28, 13),
            session_id="surface-memory-recall-e2e-session",
            person_id="surface-memory-recall-e2e",
            metadata_extra={"e2e_probe": True},
        )
        _write_delivery(
            store,
            prefix="RealQQ",
            ts_ms=_ts(2026, 5, 28, 12),
            session_id="qq_dm_3414578517",
            person_id="default",
        )

        direct_episode = store.recall_episode_packet(
            query="刚才给我推荐了哪几本书？",
            top_k=5,
            person_id="default",
            session_id="qq_dm_3414578517",
            now_ts_ms_value=_ts(2026, 5, 28, 14),
        )
        direct_joined = "\n".join(str(candidate.get("text") or "") for candidate in direct_episode.candidates)
        assert "SeededE2E" not in direct_joined

        packet = recall_surface_memory_for_context(
            recall_service=_collection_episode_service(ledger),
            context=ThreeLayerContinuityContext(
                current_user_message="刚才给我推荐了哪几本书？",
                task_mode_hint="daily_chat",
            ),
            request_id="req-current-listing-cross-session",
            query_embedding=[0.1] * 16,
            person_id="default",
            session_id="qq_dm_3414578517",
            ts_ms=_ts(2026, 5, 28, 14),
        )
        joined = "\n".join(packet.facts)
        trace = dict(packet.trace or {})
        readiness = dict(trace.get("recall_answer_readiness") or {})

        assert packet.reason_code is None
        assert readiness["status"] == "READY"
        assert "RealQQ A" in joined
        assert "RealQQ B" in joined
        assert "RealQQ C" in joined
        assert "SeededE2E" not in joined
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
        assert top["metadata"]["scope_key"] == "rolling:7d"
        assert top["metadata"]["item_count"] == 9
        assert "近 7 天轻小说推荐集合，记录 9 个条目" in top["text"]
        assert "DayBefore A" in top["text"]
        assert "Yesterday B" in top["text"]
        assert "Today C" in top["text"]
    finally:
        ledger.close()


def test_collection_recall_skips_synthetic_probe_collections_even_without_identity_scope() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = LifeMemoryStore(ledger=ledger)
        _write_delivery(
            store,
            prefix="今天推荐",
            ts_ms=_ts(2026, 5, 28),
            session_id="life-memory-real-scene-session",
            person_id="life-memory-real-scene-e2e",
            metadata_extra={"e2e_probe": True, "real_scene_batch": "real-scene-batch"},
        )
        _write_delivery(store, prefix="Real", ts_ms=_ts(2026, 5, 28, 13))

        packet = store.recall_collection_packet(
            query="这几天推荐过哪些轻小说",
            top_k=5,
            now_ts_ms_value=_ts(2026, 5, 28, 18),
        )

        joined = "\n".join(str(candidate.get("text") or "") for candidate in packet.candidates)
        assert packet.candidates
        assert "Real A" in joined
        assert "今天推荐 A" not in joined
        assert all(
            candidate["metadata"].get("person_id") != "life-memory-real-scene-e2e"
            for candidate in packet.candidates
        )
    finally:
        ledger.close()


def test_collection_recall_keeps_real_items_when_review_episode_anchor_is_synthetic() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = LifeMemoryStore(ledger=ledger)
        _write_delivery(
            store,
            prefix="RealYesterday",
            ts_ms=_ts(2026, 5, 27),
            session_id="qq_dm_3414578517",
            person_id="default",
        )
        synthetic = store.write_background_episode(
            event_source="surface_memory_recall",
            event_id="life-memory-real-scene-e2e:deep-review",
            person_id="life-memory-real-scene-e2e",
            session_id="life-memory-real-scene-session",
            task_id="lightnovel_recommendation_deep_review",
            status="OK",
            reason_code="LIGHTNOVEL_DEEP_REVIEW_OK",
            detail="synthetic deep review should not erase real delivery items",
            ts_ms=_ts(2026, 5, 27, 13),
            metadata={"e2e_probe": True},
        )
        synthetic_episode_id = str(synthetic["episode_id"])
        rows = ledger._conn.execute("SELECT * FROM life_memory_collection_item").fetchall()
        assert rows
        with ledger._write_lock:
            with ledger._conn:
                for row in rows:
                    metadata = json.loads(row["metadata"] or "{}")
                    metadata["source_episode_id"] = synthetic_episode_id
                    ledger._conn.execute(
                        """
                        UPDATE life_memory_collection_item
                        SET episode_id = ?, metadata = ?
                        WHERE collection_item_id = ?
                        """,
                        (synthetic_episode_id, json.dumps(metadata, ensure_ascii=False), row["collection_item_id"]),
                    )

        packet = store.recall_collection_packet(
            query="昨天给我推荐了哪几本书？",
            top_k=2,
            person_id="default",
            session_id="qq_dm_3414578517",
            now_ts_ms_value=_ts(2026, 5, 28, 8),
        )

        joined = "\n".join(str(candidate.get("text") or "") for candidate in packet.candidates)
        assert packet.candidates
        assert "RealYesterday A" in joined
        assert "RealYesterday B" in joined
        assert "RealYesterday C" in joined
        assert "life-memory-real-scene-e2e" not in joined
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
    assert "lightnovel_recommendation" in plan.target_domains
    assert "recommendation" in plan.target_domains
    assert "collection_items_present" in plan.validation_requirements
    assert "collection_item_min_count" in plan.validation_requirements
    assert "collection_item_exact_count" in plan.validation_requirements
    assert plan.min_collection_items == 3
    assert plan.recall_contract_id == "collection_date"
    assert plan.primary_memory_layer == "life_memory_collection"


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
    assert "lightnovel_recommendation" in plan.target_domains
    assert "recommendation" in plan.target_domains
    assert "collection_item_min_count" in plan.validation_requirements
    assert plan.min_collection_items == 10
    assert "preserve_item_identity" in plan.answer_obligations


def test_surface_plan_routes_day_before_yesterday_collection_scope() -> None:
    plan = plan_surface_memory_recall(
        ThreeLayerContinuityContext(
            current_user_message="前天三本呢？",
            task_mode_hint="daily_chat",
        )
    )

    assert plan.should_recall
    assert plan.reason == "collection_memory_recall"
    assert plan.query_kind == "date_lookup"
    assert plan.temporal_scope == "day_before_yesterday"
    assert plan.time_lookback_days == 3
    assert plan.target_store_ids[0] == "life_memory_collection"
    assert "lightnovel_recommendation" in plan.target_domains
    assert "recommendation" in plan.target_domains
    assert plan.recall_contract_id == "collection_date"
    assert plan.min_collection_items == 3
    assert "collection_item_exact_count" in plan.validation_requirements


def test_surface_plan_routes_absolute_date_collection_scope() -> None:
    plan = plan_surface_memory_recall(
        ThreeLayerContinuityContext(
            current_user_message="2026-05-27 推荐的三本呢？",
            task_mode_hint="daily_chat",
        )
    )

    assert plan.should_recall
    assert plan.reason == "collection_memory_recall"
    assert plan.query_kind == "date_lookup"
    assert plan.temporal_scope == "date:2026-05-27"
    assert plan.time_lookback_days is None
    assert plan.target_store_ids[0] == "life_memory_collection"
    assert plan.recall_contract_id == "collection_date"
    assert plan.min_collection_items == 3


def test_surface_plan_routes_recent_many_book_selection_to_selection_with_deep_obligation() -> None:
    plan = plan_surface_memory_recall(
        ThreeLayerContinuityContext(
            current_user_message="这几天十几本里哪本最好看？",
            task_mode_hint="daily_chat",
        )
    )

    assert plan.should_recall
    assert plan.reason == "anaphoric_selection_recall"
    assert plan.query_kind == "anaphoric_selection"
    assert plan.temporal_scope == "recent_days"
    assert plan.target_store_ids[0] == "life_memory_collection"
    assert plan.recall_contract_id == "anaphoric_selection"
    assert plan.min_collection_items == 10
    assert "collection_item_min_count" in plan.validation_requirements
    assert "compare_recalled_items_before_answering" in plan.answer_obligations
    assert "read_detail_evidence_before_deep_quality_judgment" in plan.answer_obligations


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
        joined = "\n".join(row.fact for row in packet.rows)

        assert packet.route_ids == ("surface_life_collection",)
        assert packet.rows[0].store_id == "life_memory_collection"
        assert "集合记忆" in joined
        assert "Yesterday A" in joined
        assert "可比较证据" in joined
        assert packet.evidence_refs
    finally:
        ledger.close()


def test_absolute_date_collection_recall_uses_daily_scope() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = LifeMemoryStore(ledger=ledger)
        _write_delivery(store, prefix="DateTarget", ts_ms=_ts(2026, 5, 27))
        _write_delivery(store, prefix="OtherDate", ts_ms=_ts(2026, 5, 28))

        packet = recall_surface_memory_for_context(
            recall_service=_collection_service(ledger),
            context=ThreeLayerContinuityContext(
                current_user_message="2026-05-27 推荐的三本呢？",
                task_mode_hint="daily_chat",
            ),
            request_id="req-absolute-date-collection",
            query_embedding=[0.1] * 16,
            person_id="owner",
            session_id="qq_dm_owner",
            ts_ms=_ts(2026, 5, 29, 8),
        )

        joined = "\n".join(row.fact for row in packet.rows)
        receipt = dict(dict(packet.trace or {}).get("recall_receipt") or {})
        assert packet.reason_code is None
        assert "DateTarget A" in joined
        assert "OtherDate A" not in joined
        assert receipt["expected_scope_key"] == "daily:2026-05-27"
        assert receipt["selected_scope_keys"] == ["daily:2026-05-27"]
        assert receipt["selected_item_count"] == 3
    finally:
        ledger.close()


def test_dated_three_book_recall_can_use_delivery_scope_when_daily_has_multiple_batches() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = LifeMemoryStore(ledger=ledger)
        first_episode = _write_delivery(store, prefix="YesterdayFirst", ts_ms=_ts(2026, 5, 27, 9))
        latest_episode = _write_delivery(store, prefix="YesterdayLatest", ts_ms=_ts(2026, 5, 27, 21))
        daily = next(row for row in ledger.life_memory_collection if row["scope_key"] == "daily:2026-05-27")
        assert daily["item_count"] == 6

        packet = recall_surface_memory_for_context(
            recall_service=_collection_service(ledger),
            context=ThreeLayerContinuityContext(
                current_user_message="昨天三本呢？",
                task_mode_hint="daily_chat",
            ),
            request_id="req-yesterday-three-delivery-scope",
            query_embedding=[0.1] * 16,
            person_id="owner",
            session_id="qq_dm_owner",
            ts_ms=_ts(2026, 5, 28, 8),
        )

        joined = "\n".join(row.fact for row in packet.rows)
        receipt = dict(dict(packet.trace or {}).get("recall_receipt") or {})
        validation = dict(dict(packet.trace or {}).get("validation") or {})
        assert first_episode != latest_episode
        assert packet.reason_code is None
        assert validation["passed"] is True
        assert receipt["expected_scope_key"] == "daily:2026-05-27"
        assert receipt["selected_scope_keys"] == [f"delivery:{latest_episode}"]
        assert receipt["selected_item_count"] == 3
        assert "YesterdayLatest A" in joined
        assert "YesterdayFirst A" not in joined
    finally:
        ledger.close()


def test_dated_collection_quality_followup_uses_latest_delivery_scope() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = LifeMemoryStore(ledger=ledger)
        first_episode = _write_delivery(store, prefix="YesterdayFirst", ts_ms=_ts(2026, 5, 27, 9))
        latest_episode = _write_delivery(store, prefix="YesterdayLatest", ts_ms=_ts(2026, 5, 27, 21))
        daily = next(row for row in ledger.life_memory_collection if row["scope_key"] == "daily:2026-05-27")
        assert daily["item_count"] == 6

        plan = plan_surface_memory_recall(
            ThreeLayerContinuityContext(
                current_user_message="昨天那几本好看吗",
                task_mode_hint="daily_chat",
            )
        )
        packet = recall_surface_memory_for_context(
            recall_service=_collection_service(ledger),
            context=ThreeLayerContinuityContext(
                current_user_message="昨天那几本好看吗",
                task_mode_hint="daily_chat",
            ),
            request_id="req-yesterday-quality-delivery-scope",
            query_embedding=[0.1] * 16,
            person_id="owner",
            session_id="qq_dm_owner",
            ts_ms=_ts(2026, 5, 28, 8),
        )

        joined = "\n".join(row.fact for row in packet.rows)
        receipt = dict(dict(packet.trace or {}).get("recall_receipt") or {})
        assert first_episode != latest_episode
        assert plan.reason == "anaphoric_selection_recall"
        assert plan.min_collection_items == 3
        assert "collection_item_exact_count" in plan.validation_requirements
        _assert_detail_anchor_not_ready(packet)
        assert receipt["selected_scope_keys"] == [f"delivery:{latest_episode}"]
        assert receipt["selected_item_count"] == 3
        assert "YesterdayLatest A" in joined
        assert "YesterdayFirst A" not in joined
    finally:
        ledger.close()


def test_collection_recall_crosses_scheduler_session_for_same_person() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = LifeMemoryStore(ledger=ledger)
        _write_delivery(
            store,
            prefix="SchedulerPush",
            ts_ms=_ts(2026, 5, 27),
            session_id="scheduler_daily_lightnovel",
            person_id="owner",
        )

        packet = store.recall_collection_packet(
            query="昨天三本呢？",
            top_k=2,
            person_id="owner",
            session_id="qq_dm_owner",
            now_ts_ms_value=_ts(2026, 5, 28, 8),
        )

        assert packet.candidates
        top = packet.candidates[0]
        assert top["metadata"]["scope_key"] == "daily:2026-05-27"
        assert top["metadata"]["session_id"] == "scheduler_daily_lightnovel"
        assert "SchedulerPush A" in top["text"]
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
        assert "轻小说推荐集合，记录 9 个条目" in joined
        assert "DayBefore A" in joined
        assert "Yesterday C" in joined
        assert "Today C" in joined
        assert "详情：" not in joined
        trace_items = list((packet.trace or {}).get("selected_collection_items") or [])
        assert len(trace_items) == 9
        assert any(item["title"] == "DayBefore A" and item["detail_url"].endswith("daybefore-a") for item in trace_items)
    finally:
        ledger.close()


def test_rolling_collections_prune_old_items_and_entity_index_dedupes_books() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = LifeMemoryStore(ledger=ledger)
        for day in range(20, 30):
            _write_delivery(store, prefix=f"Day{day}", ts_ms=_ts(2026, 5, day))

        rolling_3d = next(row for row in ledger.life_memory_collection if row["scope_key"] == "rolling:3d")
        rolling_7d = next(row for row in ledger.life_memory_collection if row["scope_key"] == "rolling:7d")
        rolling_30d = next(row for row in ledger.life_memory_collection if row["scope_key"] == "rolling:30d")
        recent = next(row for row in ledger.life_memory_collection if row["scope_key"] == "recent")

        assert rolling_3d["item_count"] == 9
        assert "Day27 A" in rolling_3d["summary"]
        assert "Day26 A" not in rolling_3d["summary"]
        assert rolling_7d["item_count"] == 21
        assert recent["item_count"] == 21
        assert "Day23 A" in rolling_7d["summary"]
        assert "Day22 A" not in rolling_7d["summary"]
        assert rolling_30d["item_count"] == 30

        packet = store.recall_collection_packet(
            query="这几天推荐过哪些轻小说？",
            top_k=1,
            person_id="owner",
            session_id="qq_dm_owner",
            now_ts_ms_value=_ts(2026, 5, 29, 18),
        )
        assert packet.candidates
        top = packet.candidates[0]
        assert top["metadata"]["scope_key"] == "rolling:7d"
        assert top["metadata"]["item_count"] == 21
        assert "Day23 A" in top["text"]
        assert "Day22 A" not in top["text"]

        entity_packet = store.recall_entity_packet(
            query="Day29 A 这本轻小说出现过吗？",
            top_k=2,
            person_id="owner",
            session_id="qq_dm_owner",
            now_ts_ms_value=_ts(2026, 5, 29, 18),
        )
        assert entity_packet.candidates
        entity_top = entity_packet.candidates[0]
        assert entity_top["metadata"]["store_id"] == "life_memory_entity"
        assert entity_top["metadata"]["canonical_name"] == "Day29 A"
        assert entity_top["metadata"]["mention_count"] == 1
        assert "Day29 A" in entity_top["text"]
    finally:
        ledger.close()


def test_recall_fusion_entity_route_returns_book_entity() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = LifeMemoryStore(ledger=ledger)
        _write_delivery(store, prefix="EntityRoute", ts_ms=_ts(2026, 5, 28))

        result = _entity_service(ledger).recall(
            request_id="req-entity-route",
            query="EntityRoute B 这本轻小说出现过吗？",
            query_embedding=[0.1] * 16,
            person_id="owner",
            session_id="qq_dm_owner",
            ts_ms=_ts(2026, 5, 28, 18),
            target_store_ids=["life_memory_entity"],
        )

        assert result.output["status"] == "OK"
        assert result.output["routes"][0]["store_id"] == "life_memory_entity"
        assert result.output["candidates"]
        top = result.output["candidates"][0]
        assert top["metadata"]["store_id"] == "life_memory_entity"
        assert top["metadata"]["canonical_name"] == "EntityRoute B"
        assert "EntityRoute B" in top["snippet"]
    finally:
        ledger.close()


def test_specific_book_followup_keeps_entity_trace_for_deep_review() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = LifeMemoryStore(ledger=ledger)
        _write_delivery(store, prefix="EntityTrace", ts_ms=_ts(2026, 5, 28))

        packet = recall_surface_memory_for_context(
            recall_service=_collection_entity_service(ledger),
            context=ThreeLayerContinuityContext(
                current_user_message="EntityTrace B 这本好看吗？",
                task_mode_hint="daily_chat",
            ),
            request_id="req-specific-book-entity-trace",
            query_embedding=[0.1] * 16,
            person_id="owner",
            session_id="qq_dm_owner",
            ts_ms=_ts(2026, 5, 28, 18),
        )

        assert "surface_life_entity" in packet.route_ids
        trace_items = list((packet.trace or {}).get("selected_entity_items") or [])
        assert trace_items
        assert trace_items[0]["title"] == "EntityTrace B"
        assert trace_items[0]["detail_url"].endswith("entitytrace-b")
        assert any(row.store_id == "life_memory_entity" for row in packet.rows)
    finally:
        ledger.close()


def test_specific_book_followup_uses_named_entity_instead_of_latest_delivery() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = LifeMemoryStore(ledger=ledger)
        _write_delivery(store, prefix="5月27日推荐", ts_ms=_ts(2026, 5, 27))
        _write_delivery(store, prefix="5月28日推荐", ts_ms=_ts(2026, 5, 28))

        plan = plan_surface_memory_recall(
            ThreeLayerContinuityContext(
                current_user_message="5月27日推荐 B 这本好看吗？",
                task_mode_hint="daily_chat",
            )
        )
        packet = recall_surface_memory_for_context(
            recall_service=_collection_entity_service(ledger),
            context=ThreeLayerContinuityContext(
                current_user_message="5月27日推荐 B 这本好看吗？",
                task_mode_hint="daily_chat",
            ),
            request_id="req-specific-book-old-entity",
            query_embedding=[0.1] * 16,
            person_id="owner",
            session_id="qq_dm_owner",
            ts_ms=_ts(2026, 5, 28, 18),
        )

        joined = "\n".join(row.fact for row in packet.rows)
        trace_items = list((packet.trace or {}).get("selected_entity_items") or [])
        assert plan.primary_memory_layer == "life_memory_entity"
        assert plan.time_lookback_days is None
        _assert_detail_anchor_not_ready(packet)
        assert "5月27日推荐 B" in joined
        assert "5月28日推荐 B" not in joined
        assert trace_items and trace_items[0]["title"] == "5月27日推荐 B"
        assert any(row.store_id == "life_memory_entity" for row in packet.rows)
    finally:
        ledger.close()


def test_specific_book_followup_filters_repeated_entity_mentions_by_requested_day() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = LifeMemoryStore(ledger=ledger)
        shared_url = "https://www.lightnovel.fun/cn/detail/shared-river"
        _write_delivery(
            store,
            prefix="昨天推荐",
            ts_ms=_ts(2026, 5, 28),
            metadata_extra={
                "selected_recommendations": [
                    *_cards("昨天推荐")[:1],
                    {
                        "title": "昨天推荐 B：与神在河岸",
                        "selection_reason": "奇幻身份",
                        "intro_snippet": "昨天那组里的河岸与神明。",
                        "detail_url": shared_url,
                    },
                    *_cards("昨天推荐")[2:],
                ]
            },
        )
        _write_delivery(
            store,
            prefix="今天推荐",
            ts_ms=_ts(2026, 5, 29),
            metadata_extra={
                "selected_recommendations": [
                    *_cards("今天推荐")[:1],
                    {
                        "title": "今天推荐 B：与神在河岸",
                        "selection_reason": "奇幻身份",
                        "intro_snippet": "今天那组里的河岸与神明。",
                        "detail_url": shared_url,
                    },
                    *_cards("今天推荐")[2:],
                ]
            },
        )

        packet = recall_surface_memory_for_context(
            recall_service=_collection_entity_service(ledger),
            context=ThreeLayerContinuityContext(
                current_user_message="昨天推荐 B：与神在河岸 这本好看吗？",
                task_mode_hint="daily_chat",
            ),
            request_id="req-specific-book-yesterday-shared-entity",
            query_embedding=[0.1] * 16,
            person_id="owner",
            session_id="qq_dm_owner",
            ts_ms=_ts(2026, 5, 29, 18),
        )

        trace_items = list((packet.trace or {}).get("selected_entity_items") or [])
        joined = "\n".join(row.fact for row in packet.rows)
        _assert_detail_anchor_not_ready(packet)
        assert trace_items
        assert {item.get("title") for item in trace_items} == {"昨天推荐 B：与神在河岸"}
        assert all(str(item.get("scope_key") or "").startswith("delivery:") for item in trace_items)
        assert all("job-昨天推荐" in str(item.get("evidence_ref") or "") for item in trace_items)
        assert "昨天推荐 B：与神在河岸" in joined
        assert "今天推荐 B：与神在河岸" not in joined
    finally:
        ledger.close()
