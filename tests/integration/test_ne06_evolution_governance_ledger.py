from __future__ import annotations

from pathlib import Path
import sqlite3

import pytest

from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.evolution import (
    FeedbackProposalV1,
    IdentityMigrationPlanV1,
    KnowledgeEntryV1,
    build_evolution_governance_composition,
)
from host_brain.persistence.evolution_governance_ledger import (
    EvolutionGovernanceLedgerError,
    SQLiteEvolutionGovernanceLedger,
)


def _ledger(
    tmp_path: Path,
) -> tuple[SQLiteEvolutionGovernanceLedger, Path]:
    db = tmp_path / "evolution-v1-history.sqlite3"
    result = SQLiteMigrationRunner(db_path=db).apply_migrations(
        request_id="test-ne06-v1-read-only",
        ts_ms=2_100_000_010_000,
    )
    assert result.success, result.as_dict()
    assert result.summary["latest_version"] == 142
    return SQLiteEvolutionGovernanceLedger(db), db


def test_ne06_v1_mutation_surface_and_direct_sql_are_read_only(
    tmp_path: Path,
) -> None:
    ledger, db = _ledger(tmp_path)
    feedback = FeedbackProposalV1(
        proposal_id="feedback://ne06/v1-read-only",
        source_kind="task_outcome",
        trace_digest="a" * 64,
        outcome_digest="b" * 64,
        confidence=0.8,
        target_version="persona-v2",
        change={"style": "concise"},
        rollback_ref="identity://rollback/persona-v1",
    )
    knowledge = KnowledgeEntryV1(
        namespace="project-knowledge://workspace-a",
        entry_id="knowledge://workspace-a/v1-read-only",
        workspace_id="workspace-a",
        content_digest="c" * 64,
        provenance_ref="task-receipt://task-1",
        version=1,
        validity="PROPOSED",
        confidence=0.9,
    )
    identity = IdentityMigrationPlanV1(
        migration_id="identity://migration/v1-read-only",
        previous_snapshot_digest="d" * 64,
        proposed_snapshot_digest="e" * 64,
        diff_digest="f" * 64,
        regression_report_ref="artifact://identity/regression",
        review_ref="review://identity/v1-read-only",
        rollback_snapshot_digest="d" * 64,
        activation_version=2,
    )
    for mutation in (
        lambda: ledger.register_feedback(feedback),
        lambda: ledger.register_knowledge(knowledge),
        lambda: ledger.register_identity(identity),
    ):
        with pytest.raises(
            EvolutionGovernanceLedgerError,
            match="EVOLUTION_GOVERNANCE_V1_READ_ONLY",
        ):
            mutation()

    with sqlite3.connect(db) as conn:
        with pytest.raises(
            sqlite3.IntegrityError,
            match="Evolution governance V1 is read-only",
        ):
            conn.execute(
                """
                INSERT INTO evolution_feedback_proposal(
                    proposal_id, proposal_digest, trace_digest,
                    outcome_digest, proposal_json, created_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    "feedback://direct/v1",
                    "1" * 64,
                    "2" * 64,
                    "3" * 64,
                    "{}",
                    1,
                ),
            )


def test_ne06_production_composition_exposes_v2_and_v1_query_only(
    tmp_path: Path,
) -> None:
    disabled = build_evolution_governance_composition(
        db_path=tmp_path / "missing.sqlite3"
    )
    assert disabled.enabled is False

    db = tmp_path / "ready.sqlite3"
    result = SQLiteMigrationRunner(db_path=db).apply_migrations(
        request_id="test-ne06-v2-composition",
        ts_ms=2_100_000_011_000,
    )
    assert result.success, result.as_dict()
    ready = build_evolution_governance_composition(db_path=db)
    assert ready.enabled is True
    assert ready.reason_code == (
        "EVOLUTION_GOVERNANCE_V2_COMPOSITION_ENFORCE"
    )
    assert ready.repository is not None
    assert ready.activation_service is not None
    assert ready.identity_release_service is not None
    assert ready.ledger is not None
    ready.close()
