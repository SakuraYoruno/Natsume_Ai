from __future__ import annotations

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.memory.life_memory import LifeMemoryConsolidator, LifeMemoryStore
from scripts.runtime.audit_life_memory_coverage import DEFAULT_REQUIRED_ENTRYPOINTS, build_coverage_report
from scripts.runtime.run_life_memory_entrypoint_scenario_batch import (
    REQUIRED_ENTRYPOINTS,
    run_entrypoint_scenario_batch,
)


def test_entrypoint_scenario_batch_covers_all_required_life_memory_anchors() -> None:
    assert DEFAULT_REQUIRED_ENTRYPOINTS == REQUIRED_ENTRYPOINTS
    report = run_entrypoint_scenario_batch()
    counts = {
        item["entrypoint"]: int(item["count"])
        for item in report["entrypoint_counts"]
    }

    assert report["success"] is True
    assert report["summary"]["critical_issue_count"] == 0
    assert report["summary"]["entrypoint_critical_issue_count"] == 0
    for entrypoint in REQUIRED_ENTRYPOINTS:
        assert counts.get(entrypoint, 0) > 0


def test_coverage_report_flags_missing_required_entrypoint() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        report = build_coverage_report(
            ledger._conn,
            include_synthetic=True,
            required_entrypoints=["surface_chat"],
        )

        assert report["summary"]["entrypoint_critical_issue_count"] == 1
        assert report["entrypoint_issue_counts"] == {"missing_required_entrypoint": 1}
        assert report["entrypoint_issues"][0]["entrypoint"] == "surface_chat"
    finally:
        ledger.close()


def test_coverage_report_classifies_surface_memory_recall_action_as_generic_tool_action() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = LifeMemoryStore(ledger=ledger)
        store.write_background_episode(
            event_source="surface_memory_recall",
            event_id="deep-review-1",
            person_id="owner",
            session_id="qq_dm_owner",
            task_id="lightnovel_recommendation_deep_review",
            status="OK",
            reason_code="LIGHTNOVEL_DEEP_REVIEW_OK",
            detail="翻看轻小说详情和正文后形成比较判断。",
            ts_ms=1_800_000_000_000,
            metadata={"action_kind": "lightnovel_recommendation_deep_review"},
        )

        report = build_coverage_report(ledger._conn, include_synthetic=True)
        counts = {item["entrypoint"]: int(item["count"]) for item in report["entrypoint_counts"]}

        assert counts.get("generic_tool_action", 0) == 1
        assert counts.get("unclassified", 0) == 0
    finally:
        ledger.close()


def test_coverage_report_audits_semantic_memory_life_sources() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = LifeMemoryStore(ledger=ledger)
        source = store.upsert_episode(
            episode_kind="chat_episode",
            evidence_refs=["chat://request/semantic-audit-source"],
            primary_ref="chat://request/semantic-audit-source",
            person_id="user-1",
            session_id="sess-1",
            summary="用户喜欢推荐卡片分段。",
            salience=0.5,
            ts_ms=1_800_000_000_000,
        )
        distilled = LifeMemoryConsolidator(store=store).distill_semantic_memories(
            limit=10,
            max_records=5,
            ts_ms=1_800_000_000_100,
        )
        assert distilled["written_count"] == 1

        clean_report = build_coverage_report(ledger._conn, include_synthetic=True)
        assert clean_report["summary"]["semantic_memory_count"] == 1
        assert clean_report["summary"]["semantic_issue_count"] == 0

        store.correct_episode(
            episode_id=source["episode_id"],
            correction_kind="suppression",
            suppress=True,
            detail="source became untrusted",
            ts_ms=1_800_000_000_200,
        )

        dirty_report = build_coverage_report(ledger._conn, include_synthetic=True)
        assert dirty_report["summary"]["semantic_critical_issue_count"] >= 1
        assert "semantic_untrusted_life_episode_source" in dirty_report["semantic_issue_counts"]
    finally:
        ledger.close()
