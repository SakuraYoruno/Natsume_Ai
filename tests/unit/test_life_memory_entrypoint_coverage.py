from __future__ import annotations

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.memory.life_memory import LifeMemoryConsolidator, LifeMemoryStore
from scripts.runtime.audit_life_memory_coverage import build_coverage_report
from scripts.runtime.run_life_memory_entrypoint_scenario_batch import (
    REQUIRED_ENTRYPOINTS,
    run_entrypoint_scenario_batch,
)


def test_entrypoint_scenario_batch_covers_all_required_life_memory_anchors() -> None:
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
