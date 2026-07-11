from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
import sqlite3
from unittest.mock import patch

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.memory.canon_world_store import WorldCanonStore
from host_brain.memory.long_term_distiller import LongTermMemoryDistiller


def _install_canon_apply_schema(db_path: Path) -> None:
    migration = (
        Path(__file__).resolve().parents[2]
        / "host_brain"
        / "audit"
        / "migrations"
        / "0053_canon_apply.sql"
    )
    sql = migration.read_text(encoding="utf-8-sig")
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                migration_id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                checksum TEXT NOT NULL,
                applied_ts_ms INTEGER NOT NULL
            )
            """
        )
        conn.executescript(sql)
        conn.execute(
            "INSERT INTO schema_migrations VALUES (53, 'canon_apply', ?, 1000)",
            (hashlib.sha256(sql.encode("utf-8")).hexdigest(),),
        )
        conn.commit()
    finally:
        conn.close()


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
    proposal = store.submit_proposal(
        proposal_id="prop-approve-stored",
        target_doc_id="wc-stored-approve",
        proposed_title="待审核长期记忆",
        proposed_tags=["long_term_distill"],
        proposed_body="候选长期记忆事实，等待审核。",
        proposer_node_id="unit-test",
        summary="approval can use stored body",
        ts_ms=1000,
    )

    from host_brain.governor.tokens import CanonApprovalTokenIssuer
    from host_brain.memory.canon_apply_service import CanonApplyService

    key = Ed25519PrivateKey.generate()
    _install_canon_apply_schema((tmp_path / "audit.sqlite3").resolve())
    issuer = CanonApprovalTokenIssuer(
        (tmp_path / "audit.sqlite3").resolve(),
        authority_key_id="unit-key",
        private_key=key,
        reviewer_id="unit-owner",
    )
    binding = {
        "operation": "APPLY",
        "subject_type": "CANON_PROPOSAL",
        "subject_id": proposal["proposal_id"],
        "subject_version": proposal["proposed_version"],
        "subject_hash": proposal["proposed_hash"],
        "target_doc_id": proposal["target_id"],
        "base_version": proposal["base_version"],
        "actor_id": "unit-owner",
        "session_id": "unit-session",
        "trace_id": "unit-trace",
        "evidence_refs": ("audit://unit/review",),
    }
    token = issuer.issue_approved_token(**binding)
    service = CanonApplyService(
        world_root=(tmp_path / "world").resolve(),
        ledger=ledger,
        trusted_approval_keys={"unit-key": key.public_key()},
    )

    cr._canon_store = store
    cr._ledger_provider = lambda: ledger
    try:
        with patch("host_brain.app.canon_routes._get_canon_apply_service", return_value=service):
            response = asyncio.run(
                cr.approve_proposal(
                    "prop-approve-stored",
                    cr.CanonProposalApproveRequest(
                        apply_id="unit-apply",
                        subject_version=binding["subject_version"],
                        subject_hash=binding["subject_hash"],
                        target_doc_id=binding["target_doc_id"],
                        base_version=binding["base_version"],
                        approval_token_ref=token,
                        actor_id=binding["actor_id"],
                        session_id=binding["session_id"],
                        trace_id=binding["trace_id"],
                        evidence_refs=list(binding["evidence_refs"]),
                    ),
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
