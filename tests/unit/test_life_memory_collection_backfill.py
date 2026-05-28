from __future__ import annotations

from pathlib import Path

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.memory.life_memory import LifeMemoryStore
from scripts.runtime.backfill_life_memory_collections import run_collection_backfill
from scripts.runtime.audit_life_memory_coverage import build_coverage_report


def _cards() -> list[dict[str, object]]:
    return [
        {
            "title": "历史推荐 A",
            "selection_reason": "恋爱日常",
            "intro_snippet": "人物关系轻快。",
            "detail_url": "https://www.lightnovel.fun/cn/detail/history-a",
        },
        {
            "title": "历史推荐 B",
            "selection_reason": "奇幻身份",
            "intro_snippet": "设定钩子明显。",
            "detail_url": "https://www.lightnovel.fun/cn/detail/history-b",
        },
        {
            "title": "历史推荐 C",
            "selection_reason": "皇女皇帝线",
            "intro_snippet": "路线直给。",
            "detail_url": "https://www.lightnovel.fun/cn/detail/history-c",
        },
    ]


def test_collection_backfill_indexes_historical_recommendation_episode(tmp_path: Path) -> None:
    db_path = tmp_path / "audit.sqlite3"
    ledger = SQLiteLedger(db_path=db_path)
    try:
        store = LifeMemoryStore(ledger=ledger)
        episode = store.upsert_episode(
            episode_kind="action_episode",
            evidence_refs=["gateway://message/history/card/1"],
            primary_ref="action://event/history-delivery",
            person_id="owner",
            session_id="qq_dm_owner",
            action="lightnovel_recommendation_delivery",
            result="COMPLETED",
            summary="历史推荐了 3 本轻小说。",
            salience=0.5,
            truth_state="VERIFIED",
            ts_ms=1_800_000_000_000,
            metadata={
                "platform": "qq",
                "conversation_id": "qq_dm_owner",
                "selected_recommendations": _cards(),
            },
        )
        store.attach_evidence(
            episode_id=episode["episode_id"],
            evidence_kind="tool_result",
            source_ref="action://event/history-delivery",
            content="历史推荐了 3 本轻小说。",
            trust_level="VERIFIED",
            ts_ms=1_800_000_000_000,
        )
        assert len(ledger.life_memory_collection_item) == 0
    finally:
        ledger.close()

    dry_run = run_collection_backfill(db_path=db_path, apply=False)
    assert dry_run["planned_count"] == 1
    assert dry_run["collection_item_count"] == 0

    applied = run_collection_backfill(db_path=db_path, apply=True)
    assert applied["planned_count"] == 1
    assert applied["indexed_count"] == 1
    assert applied["collection_item_count"] == 9
    assert applied["backup_path"]

    verify = SQLiteLedger(db_path=db_path)
    try:
        report = build_coverage_report(verify._conn, include_synthetic=True)
        assert report["summary"]["collection_count"] == 3
        assert report["summary"]["collection_item_count"] == 9
        assert report["summary"]["collection_issue_count"] == 0
    finally:
        verify.close()


def test_deep_review_gateway_session_does_not_require_gateway_message_anchor() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = LifeMemoryStore(ledger=ledger)
        store.write_background_episode(
            event_source="surface_memory_recall",
            event_id="deep-review-history",
            request_id="req-deep-review-history",
            person_id="owner",
            session_id="qq_dm_owner",
            task_id="lightnovel_recommendation_deep_review",
            status="OK",
            detail="我打开详情页和正文读过这组轻小说。",
            ts_ms=1_800_000_000_000,
            metadata={
                "action_kind": "lightnovel_recommendation_deep_review",
                "evidence_refs": ["https://www.lightnovel.fun/cn/detail/history-a"],
            },
        )
        report = build_coverage_report(ledger._conn, include_synthetic=True)
        assert "missing_gateway_evidence_anchor" not in report["issue_counts"]
    finally:
        ledger.close()
