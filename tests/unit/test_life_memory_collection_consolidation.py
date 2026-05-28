from __future__ import annotations

from host_brain.app.observability_persist import persist_life_action_event_memory
from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.memory.life_memory import LifeMemoryConsolidator, LifeMemoryStore
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
        intent_id="codex_lightnovel_recommender",
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
        assert any(
            any(str(source).startswith("life_memory_collection::") for source in item["source_memory_ids"])
            for item in collection_items
        )
        assert any("source:life_memory_collection" in item["tags"] for item in collection_items)
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
