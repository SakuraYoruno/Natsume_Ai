from __future__ import annotations

import asyncio
import sqlite3

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.memory.canon_world_store import WorldCanonStore
from host_brain.memory.long_term_distiller import LongTermMemoryDistiller


def test_sqlite_ledger_migrates_canon_proposal_content_columns(tmp_path) -> None:
    db_path = tmp_path / "old_canon.sqlite3"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE canon_proposal_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            proposal_id TEXT,
            canon_kind TEXT,
            target_id TEXT,
            base_version INTEGER,
            proposed_version INTEGER,
            proposed_hash TEXT,
            source_intent_id TEXT,
            proposer_node_id TEXT,
            summary TEXT,
            status TEXT,
            ts_ms INTEGER
        )
        """
    )
    conn.commit()
    conn.close()

    ledger = SQLiteLedger(db_path=str(db_path), check_same_thread=False)

    columns = {
        str(row["name"])
        for row in ledger._conn.execute("PRAGMA table_info(canon_proposal_log)").fetchall()
    }
    assert {"proposed_title", "proposed_tags", "proposed_body"} <= columns


def test_world_canon_proposal_persists_reviewable_content(tmp_path) -> None:
    ledger = SQLiteLedger(db_path=str(tmp_path / "audit.sqlite3"), check_same_thread=False)
    store = WorldCanonStore(world_root=str(tmp_path / "world"), ledger=ledger)

    store.submit_proposal(
        proposal_id="prop-content-001",
        target_doc_id="wc-content-001",
        proposed_title="长期记忆蒸馏",
        proposed_tags=["long_term_distill", "reviewable"],
        proposed_body="候选长期记忆事实，等待审核。",
        proposer_node_id="unit-test",
        summary="content should be reviewable",
        ts_ms=1000,
    )

    row = ledger.get_proposal("prop-content-001")

    assert row is not None
    assert row["proposed_title"] == "长期记忆蒸馏"
    assert row["proposed_tags"] == ["long_term_distill", "reviewable"]
    assert row["proposed_body"] == "候选长期记忆事实，等待审核。"


def test_canon_approve_route_uses_stored_proposal_content(tmp_path, monkeypatch) -> None:
    import host_brain.app.canon_routes as cr

    ledger = SQLiteLedger(db_path=str(tmp_path / "audit.sqlite3"), check_same_thread=False)
    store = WorldCanonStore(world_root=str(tmp_path / "world"), ledger=ledger)
    store.submit_proposal(
        proposal_id="prop-approve-stored",
        target_doc_id="wc-stored-approve",
        proposed_title="待审核长期记忆",
        proposed_tags=["long_term_distill"],
        proposed_body="候选长期记忆事实，等待审核。",
        proposer_node_id="unit-test",
        summary="approval can use stored body",
        ts_ms=1000,
    )

    cr._canon_store = store
    cr._ledger_provider = lambda: ledger
    try:
        response = asyncio.run(
            cr.approve_proposal(
                "prop-approve-stored",
                cr.CanonProposalApproveRequest(approver_node_id="unit-test-approver"),
            )
        )
    finally:
        cr._canon_store = None
        cr._ledger_provider = None

    doc = store.get("wc-stored-approve")
    assert response.status == "APPLIED"
    assert response.proposed_title == "待审核长期记忆"
    assert response.proposed_body == "候选长期记忆事实，等待审核。"
    assert doc is not None
    assert doc.title == "待审核长期记忆"


def test_long_term_distiller_backfills_old_pending_proposal_content(tmp_path) -> None:
    ledger = SQLiteLedger(db_path=str(tmp_path / "audit.sqlite3"), check_same_thread=False)
    store = WorldCanonStore(world_root=str(tmp_path / "world"), ledger=ledger)
    ledger.log_semantic_memory(
        {
            "semantic_id": "sem_backfill",
            "summary": "用户低能量时偏好先给一个小步骤。",
            "source_memory_ids": ["audit::mem-1", "audit::mem-2"],
            "source_hash": "hash-backfill",
            "distill_strategy": "merge_top_k",
            "confidence": 0.9,
            "tags": ["long_term_distill"],
            "ts_ms": 1000,
            "operator_node_id": "unit-test",
        }
    )
    ledger.log_canon_proposal(
        {
            "proposal_id": "distill_prop_sem_backfill_abc12345",
            "canon_kind": "world",
            "target_id": "wc-distilled-long-term-memory",
            "base_version": 0,
            "proposed_version": 1,
            "proposed_hash": "hash-proposal",
            "proposer_node_id": "long_term_distiller",
            "summary": "蒸馏主题='用户日常习惯与偏好' semantic_id=sem_backfill source_count=2",
            "status": "pending",
            "ts_ms": 2000,
        }
    )
    distiller = LongTermMemoryDistiller(
        distillation_engine=object(),  # type: ignore[arg-type]
        canon_store=store,
        ledger=ledger,
        topics=["用户日常习惯与偏好"],
    )

    result = distiller.backfill_pending_proposal_content()
    row = ledger.get_proposal("distill_prop_sem_backfill_abc12345")

    assert result["updated"] == 1
    assert row is not None
    assert row["proposed_title"] == "长期记忆蒸馏：用户日常习惯与偏好"
    assert row["proposed_tags"] == ["long_term_distill", "auto_generated", "用户日常习惯与偏好"]
    assert "sem_backfill" in row["proposed_body"]
    assert "用户低能量时偏好先给一个小步骤" in row["proposed_body"]
