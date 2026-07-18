from __future__ import annotations

from pathlib import Path
import sqlite3

import pytest

from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.evolution import FeedbackProposalV1, IdentityMigrationPlanV1, KnowledgeEntryV1
from host_brain.evolution import build_evolution_governance_composition
from host_brain.persistence.evolution_governance_ledger import (
    EvolutionGovernanceLedgerError,
    SQLiteEvolutionGovernanceLedger,
)


ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = ROOT / "host_brain" / "audit" / "migrations"


def _ledger(tmp_path: Path) -> tuple[SQLiteEvolutionGovernanceLedger, Path]:
    db = tmp_path / "evolution.sqlite3"
    result = SQLiteMigrationRunner(db_path=db, migrations_dir=MIGRATIONS).apply_migrations(
        request_id="test-ne06-evolution", ts_ms=2_100_000_010_000
    )
    assert result.success, result.as_dict()
    return SQLiteEvolutionGovernanceLedger(db, clock=lambda: 2_100_000_010_100), db


def test_ne06_feedback_is_proposal_only_until_lab_review_then_rolls_back(tmp_path: Path) -> None:
    ledger, _ = _ledger(tmp_path)
    proposal = FeedbackProposalV1(
        proposal_id="feedback://ne06/style-1",
        source_kind="task_outcome",
        trace_digest="a" * 64,
        outcome_digest="b" * 64,
        confidence=0.85,
        target_version="persona-v2",
        change={"style": "concise"},
        rollback_ref="identity://rollback/persona-v1",
    )
    ledger.register_feedback(proposal)
    assert [event["event"] for event in ledger.list_events(subject_kind="feedback", subject_id=proposal.proposal_id)] == ["STAGED"]
    applied = ledger.apply_feedback(
        proposal_id=proposal.proposal_id,
        lab_report_ref="lab://report/feedback-1",
        review_ref="review://feedback/1",
    )
    assert applied["event"] == "APPLIED"
    with pytest.raises(EvolutionGovernanceLedgerError, match="REPLAY_DRIFT"):
        ledger.apply_feedback(
            proposal_id=proposal.proposal_id,
            lab_report_ref="lab://report/other",
            review_ref="review://feedback/1",
        )
    rolled_back = ledger.rollback_feedback(
        proposal_id=proposal.proposal_id,
        rollback_ref=proposal.rollback_ref,
        lab_report_ref="lab://report/feedback-rollback-1",
        review_ref="review://feedback/rollback-1",
    )
    assert rolled_back["event"] == "ROLLED_BACK"


def test_ne06_knowledge_keeps_competing_refs_and_switches_read_projection(tmp_path: Path) -> None:
    ledger, _ = _ledger(tmp_path)
    first = KnowledgeEntryV1(
        namespace="project-knowledge://workspace-a",
        entry_id="knowledge://workspace-a/fact-1",
        workspace_id="workspace-a",
        content_digest="c" * 64,
        provenance_ref="task-receipt://task-1",
        version=1,
        validity="ACTIVE",
        confidence=0.9,
        conflict_group="release-policy",
    )
    second = KnowledgeEntryV1(
        namespace="project-knowledge://workspace-a",
        entry_id="knowledge://workspace-a/fact-2",
        workspace_id="workspace-a",
        content_digest="d" * 64,
        provenance_ref="task-receipt://task-2",
        version=1,
        validity="ACTIVE",
        confidence=0.4,
        conflict_group="release-policy",
    )
    ledger.register_knowledge(first)
    ledger.register_knowledge(second)
    assert ledger.active_knowledge(namespace=first.namespace, workspace_id=first.workspace_id) == []
    ledger.activate_knowledge(entry_id=first.entry_id, lab_report_ref="lab://report/knowledge-1", review_ref="review://knowledge/1")
    ledger.activate_knowledge(entry_id=second.entry_id, lab_report_ref="lab://report/knowledge-2", review_ref="review://knowledge/2")
    active = ledger.active_knowledge(namespace=first.namespace, workspace_id=first.workspace_id)
    assert {item["entry_id"] for item in active} == {first.entry_id, second.entry_id}
    ledger.deprecate_knowledge(entry_id=second.entry_id, review_ref="review://knowledge/deprecate-2")
    active_after = ledger.active_knowledge(namespace=first.namespace, workspace_id=first.workspace_id)
    assert [item["entry_id"] for item in active_after] == [first.entry_id]


def test_ne06_identity_activation_and_exact_rollback_are_durable(tmp_path: Path) -> None:
    ledger, db = _ledger(tmp_path)
    plan = IdentityMigrationPlanV1(
        migration_id="identity://migration/ne06-1",
        previous_snapshot_digest="e" * 64,
        proposed_snapshot_digest="f" * 64,
        diff_digest="1" * 64,
        regression_report_ref="artifact://identity/regression-1",
        review_ref="review://identity/migration-1",
        rollback_snapshot_digest="e" * 64,
        activation_version=2,
    )
    ledger.register_identity(plan)
    ledger.activate_identity(
        migration_id=plan.migration_id,
        regression_report_ref=plan.regression_report_ref,
        review_ref=plan.review_ref,
    )
    rollback = ledger.rollback_identity(
        migration_id=plan.migration_id,
        regression_report_ref="artifact://identity/rollback-regression-1",
        review_ref="review://identity/rollback-1",
    )
    assert rollback["event"] == "ROLLED_BACK"
    assert [event["event"] for event in ledger.list_events(subject_kind="identity", subject_id=plan.migration_id)] == [
        "PROPOSED",
        "ACTIVE",
        "ROLLED_BACK",
    ]

    no_change = IdentityMigrationPlanV1(
        migration_id="identity://migration/ne06-no-change",
        previous_snapshot_digest="e" * 64,
        proposed_snapshot_digest="e" * 64,
        diff_digest="2" * 64,
        regression_report_ref="artifact://identity/regression-no-change",
        review_ref="review://identity/no-change",
        rollback_snapshot_digest="e" * 64,
        activation_version=3,
        no_change_release=True,
    )
    ledger.register_identity(no_change)

    with sqlite3.connect(db) as conn:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            conn.execute(
                "UPDATE evolution_identity_plan SET plan_json = plan_json WHERE migration_id = ?",
                (plan.migration_id,),
            )


def test_ne06_production_composition_fails_closed_without_0110(tmp_path: Path) -> None:
    disabled = build_evolution_governance_composition(db_path=tmp_path / "missing.sqlite3")
    assert disabled.enabled is False

    db = tmp_path / "ready.sqlite3"
    result = SQLiteMigrationRunner(db_path=db, migrations_dir=MIGRATIONS).apply_migrations(
        request_id="test-ne06-composition", ts_ms=2_100_000_011_000
    )
    assert result.success, result.as_dict()
    ready = build_evolution_governance_composition(db_path=db)
    assert ready.enabled is True
    assert ready.ledger is not None
    ready.close()
