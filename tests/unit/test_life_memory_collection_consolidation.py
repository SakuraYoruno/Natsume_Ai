from __future__ import annotations

import hashlib
import json

from host_brain.app.observability_persist import persist_life_action_event_memory
from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.memory.life_memory import LifeMemoryConsolidator, LifeMemoryStore, MemorySleepReplayTask
from scripts.runtime.audit_life_memory_coverage import build_coverage_report


def _cards(prefix: str) -> list[dict[str, object]]:
    return [
        {
            "title": f"{prefix} 轻小说 A",
            "selection_reason": "恋爱日常",
            "intro_snippet": "人物关系比较轻快。",
            "detail_url": f"https://www.lightnovel.fun/cn/detail/{prefix}-a",
        },
        {
            "title": f"{prefix} 轻小说 B",
            "selection_reason": "奇幻身份",
            "intro_snippet": "设定钩子更明显。",
            "detail_url": f"https://www.lightnovel.fun/cn/detail/{prefix}-b",
        },
        {
            "title": f"{prefix} 轻小说 C",
            "selection_reason": "皇女皇帝线",
            "intro_snippet": "路线更直给。",
            "detail_url": f"https://www.lightnovel.fun/cn/detail/{prefix}-c",
        },
    ]


def _write_lightnovel_delivery(store: LifeMemoryStore, *, prefix: str, ts_ms: int) -> None:
    persist_life_action_event_memory(
        event_source="codex_production",
        action_kind="lightnovel_recommendation_delivery",
        action_id=f"job-{prefix}:lightnovel_gateway_delivery",
        actor_id="natsume",
        request_id=f"req-{prefix}",
        intent_id="agent_lightnovel_recommender",
        person_id="owner",
        session_id="qq_dm_owner",
        status="COMPLETED",
        action_summary=f"我给 qq_dm_owner 推荐了 3 本轻小说：{prefix} A；{prefix} B；{prefix} C。",
        ts_ms=ts_ms,
        evidence_refs=[
            f"gateway://message/{prefix}/card/1",
            f"gateway://message/{prefix}/card/2",
            f"gateway://message/{prefix}/card/3",
        ],
        life_memory_store=store,
        episodic_store=None,
        metadata={
            "platform": "qq",
            "conversation_id": "qq_dm_owner",
            "selected_recommendations": _cards(prefix),
        },
    )


def _write_lightnovel_single_delivery(store: LifeMemoryStore, *, action_id: str, title: str, detail_url: str, ts_ms: int) -> None:
    persist_life_action_event_memory(
        event_source="codex_production",
        action_kind="lightnovel_recommendation_delivery",
        action_id=f"{action_id}:lightnovel_gateway_delivery",
        actor_id="natsume",
        request_id=f"req-{action_id}",
        intent_id="agent_lightnovel_recommender",
        person_id="owner",
        session_id="qq_dm_owner",
        status="COMPLETED",
        action_summary=f"我给 qq_dm_owner 推荐了 1 本轻小说：{title}。",
        ts_ms=ts_ms,
        evidence_refs=[f"gateway://message/{action_id}/card/1"],
        life_memory_store=store,
        episodic_store=None,
        metadata={
            "platform": "qq",
            "conversation_id": "qq_dm_owner",
            "selected_recommendations": [
                {
                    "title": title,
                    "selection_reason": "恋爱日常",
                    "intro_snippet": "同一作品的不同章节更新。",
                    "detail_url": detail_url,
                }
            ],
        },
    )


def test_lightnovel_collection_items_index_same_series_title_variants_as_one_entity() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = LifeMemoryStore(ledger=ledger)
        _write_lightnovel_single_delivery(
            store,
            action_id="variant-15",
            title="[丸焦ししゃも]给被人说“培养失败”的前美少女童星（SSR） 第15话 辽君，差不多该配备用钥匙了吧？",
            detail_url="https://www.lightnovel.fun/cn/detail/1144609",
            ts_ms=1_800_000_000_000,
        )
        _write_lightnovel_single_delivery(
            store,
            action_id="variant-16",
            title="[丸焦ししゃも]给被人说“培养失败”的前美少女童星（SSR） 第16话 看来晚上只说“晚安”还不够",
            detail_url="https://www.lightnovel.fun/cn/detail/1144610",
            ts_ms=1_800_086_400_000,
        )

        rows = ledger._conn.execute(
            """
            SELECT entity_id, canonical_name, normalized_key, mention_count
            FROM life_memory_entity
            WHERE domain = 'lightnovel_recommendation'
            """
        ).fetchall()
        item_rows = ledger._conn.execute(
            "SELECT metadata FROM life_memory_collection_item ORDER BY created_ts_ms"
        ).fetchall()
        item_meta = [json.loads(row["metadata"] or "{}") for row in item_rows]

        assert len(rows) == 1
        assert rows[0]["mention_count"] == 2
        assert rows[0]["canonical_name"] == "[丸焦ししゃも]给被人说“培养失败”的前美少女童星（SSR）"
        assert "第15话" not in rows[0]["canonical_name"]
        assert "第16话" not in rows[0]["canonical_name"]
        assert len({meta.get("canonical_key") for meta in item_meta}) == 1
        assert all(meta.get("canonical_title") == rows[0]["canonical_name"] for meta in item_meta)
    finally:
        ledger.close()


def test_backfill_collection_item_canonical_index_repairs_old_rows() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = LifeMemoryStore(ledger=ledger)
        _write_lightnovel_single_delivery(
            store,
            action_id="old-variant-15",
            title="[丸焦ししゃも]给被人说“培养失败”的前美少女童星（SSR） 第15话 辽君，差不多该配备用钥匙了吧？",
            detail_url="https://www.lightnovel.fun/cn/detail/old-15",
            ts_ms=1_800_000_000_000,
        )
        _write_lightnovel_single_delivery(
            store,
            action_id="old-variant-16",
            title="[丸焦ししゃも]给被人说“培养失败”的前美少女童星（SSR） 第16话 看来晚上只说“晚安”还不够",
            detail_url="https://www.lightnovel.fun/cn/detail/old-16",
            ts_ms=1_800_086_400_000,
        )
        with ledger._write_lock:
            with ledger._conn:
                ledger._conn.execute("UPDATE life_memory_collection_item SET metadata = '{}'")
                ledger._conn.execute("DELETE FROM life_memory_entity_mention")
                ledger._conn.execute("DELETE FROM life_memory_entity")

        preview = store.backfill_collection_item_canonical_index(dry_run=True, limit=100)
        applied = store.backfill_collection_item_canonical_index(dry_run=False, limit=100)
        second = store.backfill_collection_item_canonical_index(dry_run=True, limit=100)
        entities = ledger._conn.execute(
            "SELECT canonical_name, normalized_key, mention_count FROM life_memory_entity"
        ).fetchall()
        item_rows = ledger._conn.execute("SELECT metadata FROM life_memory_collection_item").fetchall()
        item_meta = [json.loads(row["metadata"] or "{}") for row in item_rows]

        assert preview["planned_count"] > 0
        assert applied["updated_metadata_count"] == len(item_rows)
        assert applied["indexed_count"] == 2
        assert second["planned_count"] == 0
        assert len(entities) == 1
        assert entities[0]["mention_count"] == 2
        assert entities[0]["canonical_name"] == "[丸焦ししゃも]给被人说“培养失败”的前美少女童星（SSR）"
        assert entities[0]["normalized_key"].startswith("title:")
        assert all(meta.get("canonical_key") for meta in item_meta)
        assert len({meta.get("canonical_key") for meta in item_meta}) == 1
    finally:
        ledger.close()


def test_consolidator_dedupes_lightnovel_chapter_variants_in_collection_semantic_summary() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = LifeMemoryStore(ledger=ledger)
        _write_lightnovel_single_delivery(
            store,
            action_id="semantic-variant-15",
            title="[丸焦ししゃも]给被人说“培养失败”的前美少女童星（SSR） 第15话 辽君，差不多该配备用钥匙了吧？",
            detail_url="https://www.lightnovel.fun/cn/detail/semantic-15",
            ts_ms=1_800_000_000_000,
        )
        _write_lightnovel_single_delivery(
            store,
            action_id="semantic-variant-16",
            title="[丸焦ししゃも]给被人说“培养失败”的前美少女童星（SSR） 第16话 看来晚上只说“晚安”还不够",
            detail_url="https://www.lightnovel.fun/cn/detail/semantic-16",
            ts_ms=1_800_001_000_000,
        )

        result = LifeMemoryConsolidator(store=store).distill_semantic_memories(
            limit=20,
            max_records=10,
            ts_ms=1_800_001_000_100,
        )
        items, _ = ledger.list_semantic_memories(limit=20)
        daily = next(
            item
            for item in items
            if item["distill_strategy"] == "life_memory_collection_rule_consolidation"
            and "collection_scope:daily" in item["tags"]
        )

        assert result["written_count"] >= 1
        assert "发过 2 条推荐，去掉重复标题主要是 1 本" in daily["summary"]
        assert "[丸焦ししゃも]给被人说“培养失败”的前美少女童星（SSR）" in daily["summary"]
        assert "第15话" not in daily["summary"]
        assert "第16话" not in daily["summary"]
    finally:
        ledger.close()


def test_consolidator_distills_collection_summary_with_collection_source_ids() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = LifeMemoryStore(ledger=ledger)
        _write_lightnovel_delivery(store, prefix="day1", ts_ms=1_800_000_000_000)
        _write_lightnovel_delivery(store, prefix="day2", ts_ms=1_800_086_400_000)

        result = LifeMemoryConsolidator(store=store).distill_semantic_memories(
            limit=20,
            max_records=10,
            ts_ms=1_800_100_000_000,
        )
        items, total = ledger.list_semantic_memories(limit=20)
        collection_items = [
            item for item in items
            if item["distill_strategy"] == "life_memory_collection_rule_consolidation"
        ]

        assert result["written_count"] >= 1
        assert total >= 1
        assert collection_items
        assert any("经历集合" in item["summary"] for item in collection_items)
        assert any("day1 轻小说 A" in item["summary"] or "day2 轻小说 A" in item["summary"] for item in collection_items)
        assert any("题材线索" in item["summary"] for item in collection_items)
        assert any("恋爱日常" in item["summary"] for item in collection_items)
        assert any("奇幻身份" in item["summary"] for item in collection_items)
        assert any("皇女皇帝线" in item["summary"] for item in collection_items)
        assert not any("近 3 天" in item["summary"] or "近 7 天" in item["summary"] for item in collection_items)
        assert any(
            any(str(source).startswith("life_memory_collection::") for source in item["source_memory_ids"])
            for item in collection_items
        )
        assert any("source:life_memory_collection" in item["tags"] for item in collection_items)
        assert any("memory_family:experience_index" in item["tags"] for item in collection_items)
        assert any("collection_scope:recent" in item["tags"] for item in collection_items)
        assert any("theme:恋爱日常" in item["tags"] for item in collection_items)
        assert any("theme:奇幻身份" in item["tags"] for item in collection_items)
    finally:
        ledger.close()


def test_consolidator_distills_repeated_entity_summary_with_entity_source_ids() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = LifeMemoryStore(ledger=ledger)
        _write_lightnovel_single_delivery(
            store,
            action_id="entity-variant-15",
            title="[丸焦ししゃも]给被人说“培养失败”的前美少女童星（SSR） 第15话 辽君，差不多该配备用钥匙了吧？",
            detail_url="https://www.lightnovel.fun/cn/detail/entity-15",
            ts_ms=1_800_000_000_000,
        )
        _write_lightnovel_single_delivery(
            store,
            action_id="entity-variant-16",
            title="[丸焦ししゃも]给被人说“培养失败”的前美少女童星（SSR） 第16话 看来晚上只说“晚安”还不够",
            detail_url="https://www.lightnovel.fun/cn/detail/entity-16",
            ts_ms=1_800_086_400_000,
        )

        result = LifeMemoryConsolidator(store=store).distill_semantic_memories(
            limit=30,
            max_records=20,
            ts_ms=1_800_100_000_000,
        )
        items, _ = ledger.list_semantic_memories(limit=30)
        entity_items = [
            item
            for item in items
            if item["distill_strategy"] == "life_memory_entity_rule_consolidation"
        ]

        assert result["written_count"] >= 1
        assert entity_items
        assert any("实体经历：" in item["summary"] for item in entity_items)
        assert any("出现过 2 次" in item["summary"] for item in entity_items)
        assert any("[丸焦ししゃも]给被人说“培养失败”的前美少女童星（SSR）" in item["summary"] for item in entity_items)
        assert any(
            any(str(source).startswith("life_memory_entity::") for source in item["source_memory_ids"])
            for item in entity_items
        )
        assert any("source:life_memory_entity" in item["tags"] for item in entity_items)
        assert any("semantic_kind:experience_entity" in item["tags"] for item in entity_items)
    finally:
        ledger.close()


def test_consolidator_distills_single_deep_review_entity_as_long_term_memory() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = LifeMemoryStore(ledger=ledger)
        _write_lightnovel_single_delivery(
            store,
            action_id="entity-deep-review",
            title="与神在河岸",
            detail_url="https://www.lightnovel.fun/cn/detail/entity-deep-river",
            ts_ms=1_800_000_000_000,
        )
        item = ledger.life_memory_collection_item[0]
        review = store.write_background_episode(
            event_source="surface_memory_recall",
            event_id="entity-deep-review-result",
            status="OK",
            detail="我翻过这本轻小说的详情和正文，奇幻身份钩子比较明显。",
            person_id="owner",
            session_id="qq_dm_owner",
            ts_ms=1_800_000_000_100,
            metadata={
                "action_kind": "lightnovel_recommendation_deep_review",
                "items": [
                    {
                        "title": "与神在河岸",
                        "detail_url": "https://www.lightnovel.fun/cn/detail/entity-deep-river",
                        "review_status": "OK",
                        "body_observations": ["正文里异世界、神明和河岸相遇的奇幻身份线很明确"],
                        "content_char_count": 1200,
                    }
                ],
            },
        )
        store.attach_collection_item_review_evidence(
            review_episode_id=review["episode_id"],
            review_ref=review["primary_ref"],
            fact="网页深读结果（含正文）：与神在河岸的奇幻身份线比较明确。",
            reviewed_items=[
                {
                    "collection_item_id": item["collection_item_id"],
                    "detail_url": item["source_ref"],
                    "review_status": "OK",
                    "body_observations": ["正文里异世界、神明和河岸相遇的奇幻身份线很明确"],
                    "content_char_count": 1200,
                }
            ],
            ts_ms=1_800_000_000_110,
        )

        result = LifeMemoryConsolidator(store=store).distill_semantic_memories(
            limit=30,
            max_records=20,
            ts_ms=1_800_000_000_200,
        )
        items, _ = ledger.list_semantic_memories(limit=30)
        entity_item = next(
            item
            for item in items
            if item["distill_strategy"] == "life_memory_entity_rule_consolidation"
            and "与神在河岸" in item["summary"]
        )

        assert result["written_count"] >= 1
        assert "已有深读证据" in entity_item["summary"]
        assert "异世界" in entity_item["summary"]
        assert "神明" in entity_item["summary"]
        assert "has_deep_review" in entity_item["tags"]
        assert any(str(source).startswith("life_memory_entity::") for source in entity_item["source_memory_ids"])
        assert any(str(source).startswith("life_episode::" + review["episode_id"]) for source in entity_item["source_memory_ids"])
    finally:
        ledger.close()


def test_consolidator_skips_single_delivery_recent_and_rolling_semantic_windows() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = LifeMemoryStore(ledger=ledger)
        _write_lightnovel_delivery(store, prefix="solo", ts_ms=1_800_000_000_000)

        result = LifeMemoryConsolidator(store=store).distill_semantic_memories(
            limit=20,
            max_records=10,
            ts_ms=1_800_100_000_000,
        )
        items, total = ledger.list_semantic_memories(limit=20)
        summaries = [item["summary"] for item in items]

        assert result["written_count"] == 1
        assert total == 1
        assert any("2027-01-15 的轻小说推荐记录了 3 本" in item for item in summaries)
        assert not any("最近的轻小说推荐" in item or "近 7 天" in item or "近 30 天" in item for item in summaries)
    finally:
        ledger.close()


def test_consolidator_replaces_collection_semantic_snapshot_instead_of_appending() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = LifeMemoryStore(ledger=ledger)
        _write_lightnovel_delivery(store, prefix="snapshot", ts_ms=1_800_000_000_000)
        consolidator = LifeMemoryConsolidator(store=store)
        proposal = next(
            item
            for item in consolidator.build_semantic_proposals(limit=20, max_records=10)
            if item["distill_strategy"] == "life_memory_collection_rule_consolidation"
        )
        semantic_key = str(proposal["semantic_key"])
        source_hash = hashlib.sha256(f"life_semantic:v1:{semantic_key}".encode("utf-8", errors="ignore")).hexdigest()
        old_summary = str(proposal["summary"]).split(" 题材线索", 1)[0]
        source_ids = [
            *[f"life_episode::{episode_id}" for episode_id in proposal["source_episode_ids"]],
            *[f"life_memory_collection::{collection_id}" for collection_id in proposal["source_collection_ids"]],
        ]
        ledger.log_semantic_memory(
            {
                "semantic_id": "sem_old_collection_snapshot",
                "summary": old_summary,
                "source_memory_ids": source_ids,
                "source_hash": source_hash,
                "distill_strategy": "life_memory_collection_rule_consolidation",
                "confidence": 0.66,
                "tags": ["experience_collection"],
                "ts_ms": 1_800_000_000_050,
                "operator_node_id": "memory_sleep_replay",
            }
        )

        result = consolidator.distill_semantic_memories(
            limit=20,
            max_records=10,
            ts_ms=1_800_000_000_100,
        )
        items, total = ledger.list_semantic_memories(limit=10)
        item = next(row for row in items if row["semantic_id"] == "sem_old_collection_snapshot")

        assert result["written"][0]["updated_existing"] is True
        assert total == 1
        assert "题材线索" in item["summary"]
        assert item["summary"].count("经历集合：") == 1
        assert item["summary"].count("记录了") == 1
        assert "theme:恋爱日常" in item["tags"]
    finally:
        ledger.close()


def test_consolidator_updates_same_day_collection_semantic_instead_of_appending() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = LifeMemoryStore(ledger=ledger)
        _write_lightnovel_delivery(store, prefix="morning", ts_ms=1_800_000_000_000)
        consolidator = LifeMemoryConsolidator(store=store)
        first = consolidator.distill_semantic_memories(
            limit=20,
            max_records=10,
            ts_ms=1_800_000_000_100,
        )

        _write_lightnovel_delivery(store, prefix="night", ts_ms=1_800_001_000_000)
        second = consolidator.distill_semantic_memories(
            limit=20,
            max_records=10,
            ts_ms=1_800_001_000_100,
        )
        items, total = ledger.list_semantic_memories(limit=20)
        daily_items = [
            item
            for item in items
            if item["distill_strategy"] == "life_memory_collection_rule_consolidation"
            and "collection_scope:daily" in item["tags"]
        ]

        assert first["written_count"] == 1
        assert any(item.get("updated_existing") for item in second["written"])
        assert total >= 1
        assert len(daily_items) == 1
        assert daily_items[0]["summary"].count("经历集合：") == 1
        assert daily_items[0]["summary"].count("记录了") == 1
        assert "记录了 6 本" in daily_items[0]["summary"]
        assert "morning 轻小说 A" in daily_items[0]["summary"]
        assert "night 轻小说 A" in daily_items[0]["summary"]
    finally:
        ledger.close()


def test_consolidator_soft_deletes_stale_and_window_collection_semantics() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = LifeMemoryStore(ledger=ledger)
        _write_lightnovel_delivery(store, prefix="day1", ts_ms=1_800_000_000_000)
        _write_lightnovel_delivery(store, prefix="day2", ts_ms=1_800_086_400_000)
        recent = next(row for row in ledger.life_memory_collection if row["scope_key"] == "recent")
        rolling = next(row for row in ledger.life_memory_collection if row["scope_key"] == "rolling:7d")
        ledger.log_semantic_memory(
            {
                "semantic_id": "sem_recent_old",
                "summary": "经历集合：最近的轻小说推荐记录了 3 个条目：day1 轻小说 A、day1 轻小说 B、day1 轻小说 C。",
                "source_memory_ids": [f"life_memory_collection::{recent['collection_id']}"],
                "source_hash": "recent-old",
                "distill_strategy": "life_memory_collection_rule_consolidation",
                "confidence": 0.70,
                "tags": ["experience_collection"],
                "ts_ms": 1_800_000_000_010,
                "operator_node_id": "memory_sleep_replay",
            }
        )
        ledger.log_semantic_memory(
            {
                "semantic_id": "sem_recent_new",
                "summary": "经历集合：最近的轻小说推荐记录了 6 个条目：day1 轻小说 A、day1 轻小说 B、day1 轻小说 C、day2 轻小说 A、day2 轻小说 B、day2 轻小说 C。",
                "source_memory_ids": [f"life_memory_collection::{recent['collection_id']}"],
                "source_hash": "recent-new",
                "distill_strategy": "life_memory_collection_rule_consolidation",
                "confidence": 0.80,
                "tags": ["experience_collection"],
                "ts_ms": 1_800_086_400_010,
                "operator_node_id": "memory_sleep_replay",
            }
        )
        ledger.log_semantic_memory(
            {
                "semantic_id": "sem_rolling_window",
                "summary": "经历集合：近 7 天的轻小说推荐记录了 6 个条目：day1 轻小说 A、day2 轻小说 A。",
                "source_memory_ids": [f"life_memory_collection::{rolling['collection_id']}"],
                "source_hash": "rolling-window",
                "distill_strategy": "life_memory_collection_rule_consolidation",
                "confidence": 0.80,
                "tags": ["experience_collection"],
                "ts_ms": 1_800_086_400_020,
                "operator_node_id": "memory_sleep_replay",
            }
        )

        cleanup = LifeMemoryConsolidator(store=store).prune_synthetic_semantic_memories(
            ts_ms=1_800_100_000_000,
        )
        items, total = ledger.list_semantic_memories(limit=20)
        active_ids = {item["semantic_id"] for item in items}

        assert cleanup["reason_counts"]["SEMANTIC_COLLECTION_STALE_VERSION"] == 1
        assert cleanup["reason_counts"]["SEMANTIC_COLLECTION_ROLLING_WINDOW_INDEX"] == 1
        assert "sem_recent_old" not in active_ids
        assert "sem_rolling_window" not in active_ids
        assert "sem_recent_new" in active_ids
        assert total == 1
    finally:
        ledger.close()


def test_consolidator_soft_deletes_stale_collection_semantic_with_same_scope_identity() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = LifeMemoryStore(ledger=ledger)
        _write_lightnovel_delivery(store, prefix="scope", ts_ms=1_800_000_000_000)
        daily = next(row for row in ledger.life_memory_collection if str(row["scope_key"]).startswith("daily:"))
        shadow = store.upsert_collection(
            collection_id="col_shadow_daily",
            collection_kind=str(daily["collection_kind"]),
            domain=str(daily["domain"]),
            scope_key=str(daily["scope_key"]),
            person_id=str(daily["person_id"]),
            session_id="qq_dm_owner_shadow",
            title="old duplicate daily collection",
            summary="old duplicate daily collection",
            ts_ms=1_800_000_000_010,
        )
        ledger.log_semantic_memory(
            {
                "semantic_id": "sem_daily_scope_old",
                "summary": "经历集合：2027-01-15 的轻小说推荐记录了 3 个条目：old A。",
                "source_memory_ids": [f"life_memory_collection::{shadow['collection_id']}"],
                "source_hash": "daily-scope-old",
                "distill_strategy": "life_memory_collection_rule_consolidation",
                "confidence": 0.70,
                "tags": ["experience_collection", "collection_scope:daily"],
                "ts_ms": 1_800_000_000_010,
                "operator_node_id": "memory_sleep_replay",
            }
        )
        ledger.log_semantic_memory(
            {
                "semantic_id": "sem_daily_scope_new",
                "summary": "经历集合：2027-01-15 的轻小说推荐记录了 3 个条目：scope 轻小说 A。",
                "source_memory_ids": [f"life_memory_collection::{daily['collection_id']}"],
                "source_hash": "daily-scope-new",
                "distill_strategy": "life_memory_collection_rule_consolidation",
                "confidence": 0.82,
                "tags": ["experience_collection", "collection_scope:daily"],
                "ts_ms": 1_800_000_000_020,
                "operator_node_id": "memory_sleep_replay",
            }
        )

        cleanup = LifeMemoryConsolidator(store=store).prune_synthetic_semantic_memories(
            ts_ms=1_800_000_000_100,
        )
        items, total = ledger.list_semantic_memories(limit=20)
        active_ids = {item["semantic_id"] for item in items}

        assert cleanup["reason_counts"]["SEMANTIC_COLLECTION_STALE_VERSION"] == 1
        assert "sem_daily_scope_old" not in active_ids
        assert "sem_daily_scope_new" in active_ids
        assert total == 1
    finally:
        ledger.close()


def test_sleep_replay_cleans_collection_semantic_migration_duplicate_after_distill() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = LifeMemoryStore(ledger=ledger)
        _write_lightnovel_delivery(store, prefix="migration", ts_ms=1_800_000_000_000)
        daily = next(row for row in ledger.life_memory_collection if str(row["scope_key"]).startswith("daily:"))
        ledger.log_semantic_memory(
            {
                "semantic_id": "sem_old_title_hash_daily",
                "summary": "经历集合：2027-01-15 的轻小说推荐记录了 3 个条目：migration 轻小说 A。",
                "source_memory_ids": [f"life_memory_collection::{daily['collection_id']}"],
                "source_hash": "old-title-hash-daily",
                "distill_strategy": "life_memory_collection_rule_consolidation",
                "confidence": 0.70,
                "tags": ["experience_collection", "collection_scope:daily"],
                "ts_ms": 1_800_000_000_010,
                "operator_node_id": "old_memory_sleep_replay",
            }
        )

        result = MemorySleepReplayTask(store=store).run_once(
            now_ts_ms_value=1_800_000_000_100,
            replay_limit=1,
            forgetting_batch_limit=1,
            semantic_limit=10,
            compaction_group_limit=0,
        )
        items, total = ledger.list_semantic_memories(limit=20)
        active_ids = {item["semantic_id"] for item in items}
        daily_items = [item for item in items if "collection_scope:daily" in item["tags"]]

        assert result["semantic_distillation"]["written_count"] >= 1
        assert result["semantic_cleanup"]["reason_counts"]["SEMANTIC_COLLECTION_STALE_VERSION"] == 1
        assert "sem_old_title_hash_daily" not in active_ids
        assert len(daily_items) == 1
        assert daily_items[0]["summary"].count("经历集合：") == 1
        assert total == 1
    finally:
        ledger.close()


def test_life_memory_coverage_reports_collection_health() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = LifeMemoryStore(ledger=ledger)
        _write_lightnovel_delivery(store, prefix="audit", ts_ms=1_800_000_000_000)
        report = build_coverage_report(ledger._conn, sample_limit=10, include_synthetic=True)

        summary = dict(report["summary"])
        assert summary["collection_count"] >= 2
        assert summary["collection_item_count"] >= 6
        assert summary["collection_issue_count"] == 0
        assert any(row["collection_kind"] == "recommendation_collection" for row in report["collection_by_kind_domain"])

        good_collection_id = ledger.life_memory_collection[0]["collection_id"]
        ledger._conn.execute(
            "UPDATE life_memory_collection SET summary = ? WHERE collection_id = ?",
            ("2026-01-01 当天轻小说推荐集合，记录 999 个条目：坏摘要。", good_collection_id),
        )
        ledger._conn.execute(
            """
            INSERT INTO life_memory_collection (
                collection_id, collection_kind, domain, scope_key, item_count,
                salience, memory_strength, retention_state, created_ts_ms, updated_ts_ms, metadata
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("col_bad", "recommendation_collection", "lightnovel_recommendation", "daily:2026-01-01", 3, 0.1, 0.1, "WARM", 1, 1, "{}"),
        )
        broken = build_coverage_report(ledger._conn, sample_limit=10, include_synthetic=True)

        assert broken["summary"]["collection_issue_count"] >= 1
        assert "empty_collection" in broken["collection_issue_counts"]
        assert "collection_item_count_mismatch" in broken["collection_issue_counts"]
        assert "collection_summary_count_mismatch" in broken["collection_issue_counts"]
    finally:
        ledger.close()
