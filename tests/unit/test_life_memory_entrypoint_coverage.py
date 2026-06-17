from __future__ import annotations

import json

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
        store.attach_evidence(
            episode_id=source["episode_id"],
            evidence_kind="user_text",
            source_ref="chat://request/semantic-audit-source#user",
            content="用户喜欢推荐卡片分段。",
            trust_level="VERIFIED",
            ts_ms=1_800_000_000_001,
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


def test_coverage_report_flags_semantic_source_without_traceable_evidence() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = LifeMemoryStore(ledger=ledger)
        source = store.upsert_episode(
            episode_kind="chat_episode",
            evidence_refs=["chat://request/semantic-audit-no-evidence"],
            primary_ref="chat://request/semantic-audit-no-evidence",
            person_id="user-1",
            session_id="sess-1",
            summary="用户偏好：推荐轻小说时每本一卡片。",
            salience=0.5,
            ts_ms=1_800_000_000_000,
        )
        ledger.log_semantic_memory(
            {
                "semantic_id": "sem_missing_evidence_anchor",
                "summary": "用户偏好：推荐轻小说时每本一卡片。",
                "source_memory_ids": [f"life_episode::{source['episode_id']}"],
                "source_hash": "missing-evidence-anchor",
                "distill_strategy": "life_episode_rule_consolidation",
                "confidence": 0.8,
                "tags": ["semantic_kind:user_preference"],
                "ts_ms": 1_800_000_000_100,
                "operator_node_id": "test",
            }
        )

        report = build_coverage_report(ledger._conn, include_synthetic=True)

        assert report["summary"]["semantic_critical_issue_count"] >= 1
        assert report["summary"]["semantic_provenance_unresolved_count"] == 1
        assert "semantic_life_episode_source_missing_evidence" in report["semantic_issue_counts"]
    finally:
        ledger.close()


def test_coverage_report_flags_semantic_collection_without_item_evidence_refs() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = LifeMemoryStore(ledger=ledger)
        episode = store.upsert_episode(
            episode_kind="action_episode",
            evidence_refs=["action://event/collection-semantic-no-item-ref"],
            primary_ref="action://event/collection-semantic-no-item-ref",
            person_id="owner",
            session_id="qq_dm_owner",
            action="lightnovel_recommendation_delivery",
            result="COMPLETED",
            summary="我给用户推荐了轻小说。",
            salience=0.5,
            ts_ms=1_800_000_000_000,
        )
        store.attach_evidence(
            episode_id=episode["episode_id"],
            evidence_kind="tool_result",
            source_ref="action://event/collection-semantic-no-item-ref",
            content="推荐任务完成。",
            trust_level="VERIFIED",
            ts_ms=1_800_000_000_001,
        )
        collection = store.upsert_collection(
            collection_id="col_semantic_missing_item_refs",
            collection_kind="recommendation_collection",
            domain="lightnovel_recommendation",
            scope_key="daily:2027-01-15",
            person_id="owner",
            session_id="qq_dm_owner",
            title="2027-01-15 当天轻小说集合",
            summary="2027-01-15 当天轻小说集合，记录 1 个条目。",
            ts_ms=1_800_000_000_010,
        )
        store.attach_collection_item(
            collection_id=collection["collection_id"],
            episode_id=episode["episode_id"],
            item_key="book-without-item-ref",
            item_title="没有条目证据的书",
            item_summary="简介。",
            position=1,
            ts_ms=1_800_000_000_011,
        )
        ledger.log_semantic_memory(
            {
                "semantic_id": "sem_collection_missing_item_refs",
                "summary": "经历集合：2027-01-15 的轻小说推荐记录了 1 个条目：没有条目证据的书。",
                "source_memory_ids": [f"life_memory_collection::{collection['collection_id']}"],
                "source_hash": "collection-missing-item-refs",
                "distill_strategy": "life_memory_collection_rule_consolidation",
                "confidence": 0.8,
                "tags": ["experience_collection", "collection_scope:daily"],
                "ts_ms": 1_800_000_000_100,
                "operator_node_id": "test",
            }
        )

        report = build_coverage_report(ledger._conn, include_synthetic=True)

        assert report["summary"]["semantic_critical_issue_count"] >= 1
        assert report["summary"]["semantic_provenance_unresolved_count"] == 1
        assert "semantic_collection_source_missing_evidence_ref" in report["semantic_issue_counts"]
    finally:
        ledger.close()


def test_coverage_report_audits_semantic_entity_sources() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = LifeMemoryStore(ledger=ledger)
        for index, ts_ms in enumerate((1_800_000_000_000, 1_800_086_400_000), start=1):
            store.write_background_episode(
                event_source="codex_production",
                event_id=f"entity-semantic-delivery-{index}",
                task_id="agent_lightnovel_recommender",
                status="COMPLETED",
                detail="给用户推荐了 1 本轻小说。",
                person_id="owner",
                session_id="qq_dm_owner",
                ts_ms=ts_ms,
                metadata={
                    "action_kind": "lightnovel_recommendation_delivery",
                    "selected_recommendations": [
                        {
                            "title": "与神在河岸",
                            "selection_reason": "奇幻身份",
                            "intro_snippet": "神明与河岸的奇幻身份线。",
                            "detail_url": f"https://www.lightnovel.fun/cn/detail/entity-semantic-{index}",
                        }
                    ],
                },
            )
        result = LifeMemoryConsolidator(store=store).distill_semantic_memories(
            limit=20,
            max_records=20,
            ts_ms=1_800_100_000_000,
        )
        items, _ = ledger.list_semantic_memories(limit=20)
        entity_item = next(
            item
            for item in items
            if item["distill_strategy"] == "life_memory_entity_rule_consolidation"
        )

        report = build_coverage_report(ledger._conn, include_synthetic=True)

        assert result["written_count"] >= 1
        assert any(source.startswith("life_memory_entity::") for source in entity_item["source_memory_ids"])
        assert report["summary"]["semantic_issue_count"] == 0
        assert report["summary"]["semantic_provenance_unresolved_count"] == 0
        assert report["semantic_source_class_counts"]["life_memory_entity"] >= 1
    finally:
        ledger.close()


def test_coverage_report_flags_missing_semantic_entity_source() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        ledger.log_semantic_memory(
            {
                "semantic_id": "sem_missing_entity_source",
                "summary": "实体经历：轻小说推荐里《不存在的书》出现过 2 次。",
                "source_memory_ids": ["life_memory_entity::ent_missing"],
                "source_hash": "missing-entity-source",
                "distill_strategy": "life_memory_entity_rule_consolidation",
                "confidence": 0.8,
                "tags": ["experience_entity", "semantic_kind:experience_entity"],
                "ts_ms": 1_800_000_000_100,
                "operator_node_id": "test",
            }
        )

        report = build_coverage_report(ledger._conn, include_synthetic=True)

        assert report["summary"]["semantic_critical_issue_count"] >= 1
        assert report["summary"]["semantic_provenance_unresolved_count"] == 1
        assert "semantic_missing_entity_source" in report["semantic_issue_counts"]
    finally:
        ledger.close()


def test_coverage_report_audits_semantic_correction_sources() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = LifeMemoryStore(ledger=ledger)
        wrong = store.write_chat_episode(
            request_id="req-coverage-correction-wrong",
            session_id="qq_dm_owner",
            person_id="owner",
            user_message="刚才那三本里你选哪本？",
            assistant_reply="我选封面有猫的那本。",
            ts_ms=1_800_000_000_000,
        )
        correction = store.correct_episode(
            episode_id=wrong["episode_id"],
            correction_kind="suppression",
            reason_code="USER_CORRECTED_HALLUCINATION",
            detail="用户指出没有封面有猫这本。",
            suppress=True,
            ts_ms=1_800_000_000_100,
        )
        ledger.log_semantic_memory(
            {
                "semantic_id": "sem_coverage_correction",
                "summary": "纠错记忆：用户纠正过错误记忆：没有“封面有猫”这本；后续不能把这个说成事实。",
                "source_memory_ids": [f"life_memory_correction::{correction['correction_id']}"],
                "source_hash": "coverage-correction-source",
                "distill_strategy": "life_memory_correction_rule_consolidation",
                "confidence": 0.86,
                "tags": ["semantic_kind:correction", "correction"],
                "ts_ms": 1_800_000_000_200,
                "operator_node_id": "test",
            }
        )

        report = build_coverage_report(ledger._conn, include_synthetic=True)

        assert report["summary"]["semantic_issue_count"] == 0
        assert report["summary"]["semantic_provenance_unresolved_count"] == 0
        assert report["semantic_source_class_counts"]["life_memory_correction"] == 1
    finally:
        ledger.close()


def test_coverage_report_audits_retention_and_compaction_health() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = LifeMemoryStore(ledger=ledger)
        old_noise = store.write_background_episode(
            event_source="tool",
            event_id="old-book-read",
            task_id="epub_reading",
            person_id="owner",
            session_id="qq_dm_owner",
            status="COMPLETED",
            detail="为了回答用户追问，读取了一本轻小说正文。",
            ts_ms=1_800_000_000_000,
            metadata={"action_kind": "epub_reading"},
        )
        with ledger._write_lock:
            with ledger._conn:
                ledger._conn.execute(
                    """
                    UPDATE life_episode
                    SET retention_state = 'GONE',
                        compressed_into_episode_id = 'missing_compaction_episode'
                    WHERE episode_id = ?
                    """,
                    (old_noise["episode_id"],),
                )

        report = build_coverage_report(ledger._conn, include_synthetic=True)
        retention_counts = {
            item["retention_state"]: int(item["count"])
            for item in report["retention_state_counts"]
        }

        assert retention_counts["GONE"] == 1
        assert report["summary"]["compressed_episode_count"] == 1
        assert report["issue_counts"]["compressed_target_missing"] == 1
        assert report["issue_counts"]["gone_episode_missing_forget_reason"] == 1
        assert report["summary"]["critical_issue_count"] == 1
        assert report["summary"]["warn_issue_count"] == 1
    finally:
        ledger.close()


def test_coverage_report_tracks_semantic_candidate_backlog() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = LifeMemoryStore(ledger=ledger)
        referenced = store.write_chat_episode(
            request_id="req-semantic-backlog-referenced",
            session_id="sess-1",
            person_id="user-1",
            user_message="以后推荐轻小说时，每本简介后换行。",
            assistant_reply="好，我记住这个格式。",
            ts_ms=1_800_000_000_000,
        )
        unreferenced = store.write_chat_episode(
            request_id="req-semantic-backlog-unreferenced",
            session_id="sess-1",
            person_id="user-1",
            user_message="主链不要用 pro，没有必要。",
            assistant_reply="明白，主链不用 pro。",
            ts_ms=1_800_000_000_100,
        )
        for episode in (referenced, unreferenced):
            row = ledger._conn.execute(
                "SELECT metadata FROM life_episode WHERE episode_id = ?",
                (episode["episode_id"],),
            ).fetchone()
            metadata = json.loads(row["metadata"] or "{}")
            metadata["memory_class"] = "preference"
            metadata["memory_policy"] = {"memory_class": "preference", "semantic_candidate": True}
            ledger._conn.execute(
                "UPDATE life_episode SET metadata = ? WHERE episode_id = ?",
                (json.dumps(metadata, ensure_ascii=False), episode["episode_id"]),
            )
        ledger.log_semantic_memory(
            {
                "semantic_id": "sem_pref_referenced",
                "summary": "用户希望轻小说推荐每本简介后换行。",
                "source_memory_ids": [f"life_episode::{referenced['episode_id']}"],
                "source_hash": "semantic-backlog-referenced",
                "distill_strategy": "life_episode_rule_consolidation",
                "confidence": 0.8,
                "tags": ["semantic_kind:user_preference"],
                "ts_ms": 1_800_000_000_200,
                "operator_node_id": "test",
            }
        )

        report = build_coverage_report(ledger._conn, include_synthetic=True)

        assert report["summary"]["semantic_candidate_episode_count"] == 2
        assert report["summary"]["production_semantic_candidate_episode_count"] == 2
        assert report["summary"]["semantic_referenced_episode_count"] == 1
        assert report["summary"]["production_semantic_unreferenced_candidate_episode_count"] == 1
        assert report["semantic_candidate_counts"] == {"preference": 2}
        assert report["semantic_referenced_candidate_counts"] == {"preference": 1}
        assert report["production_semantic_unreferenced_candidate_counts"] == {"preference": 1}
        assert report["semantic_candidate_unreferenced_samples"][0]["episode_id"] == unreferenced["episode_id"]
    finally:
        ledger.close()


def test_coverage_report_flags_duplicate_collection_semantic_scope() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = LifeMemoryStore(ledger=ledger)
        episode = store.upsert_episode(
            episode_kind="action_episode",
            evidence_refs=["action://event/duplicate-collection-source"],
            primary_ref="action://event/duplicate-collection-source",
            person_id="owner",
            session_id="qq_dm_owner",
            action="lightnovel_recommendation_delivery",
            result="COMPLETED",
            summary="我给用户推荐了轻小说。",
            salience=0.5,
            ts_ms=1_800_000_000_000,
        )
        for index, session_id in enumerate(("qq_dm_a", "qq_dm_b"), start=1):
            collection = store.upsert_collection(
                collection_id=f"col_duplicate_scope_{index}",
                collection_kind="recommendation_collection",
                domain="lightnovel_recommendation",
                scope_key="daily:2027-01-15",
                person_id="owner",
                session_id=session_id,
                title="2027-01-15 当天轻小说集合",
                summary="2027-01-15 当天轻小说集合，记录 1 个条目。",
                ts_ms=1_800_000_000_000 + index,
            )
            store.attach_collection_item(
                collection_id=collection["collection_id"],
                episode_id=episode["episode_id"],
                item_key=f"book-{index}",
                item_title=f"书 {index}",
                item_summary="简介。",
                source_ref=f"action://event/duplicate-collection-source/{index}",
                evidence_ref=f"action://event/duplicate-collection-source/{index}",
                position=1,
                ts_ms=1_800_000_000_000 + index,
            )
            ledger.log_semantic_memory(
                {
                    "semantic_id": f"sem_duplicate_scope_{index}",
                    "summary": f"经历集合：2027-01-15 的轻小说推荐记录了 1 个条目：书 {index}。",
                    "source_memory_ids": [f"life_memory_collection::{collection['collection_id']}"],
                    "source_hash": f"duplicate-scope-{index}",
                    "distill_strategy": "life_memory_collection_rule_consolidation",
                    "confidence": 0.8,
                    "tags": ["experience_collection", "collection_scope:daily"],
                    "ts_ms": 1_800_000_000_100 + index,
                    "operator_node_id": "test",
                }
            )

        report = build_coverage_report(ledger._conn, include_synthetic=True)

        assert report["summary"]["semantic_warn_issue_count"] == 2
        assert report["semantic_issue_counts"]["semantic_duplicate_collection_scope"] == 2
    finally:
        ledger.close()
