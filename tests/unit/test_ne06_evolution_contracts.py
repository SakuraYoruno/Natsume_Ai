from __future__ import annotations

import pytest

from host_brain.evolution.contracts import (
    FeedbackProposalV1,
    IdentityMigrationPlanV1,
    KnowledgeEntryV1,
    KnowledgeEvolutionContractError,
)


def test_ne06_feedback_knowledge_and_identity_are_proposal_bound() -> None:
    feedback = FeedbackProposalV1(
        proposal_id="feedback-1",
        source_kind="task_outcome",
        trace_digest="a" * 64,
        outcome_digest="b" * 64,
        confidence=0.8,
        target_version="persona-v2",
        change={"style": "concise"},
        rollback_ref="identity://rollback/v1",
    )
    knowledge = KnowledgeEntryV1(
        namespace="project-knowledge://workspace-a",
        entry_id="entry-1",
        workspace_id="workspace-a",
        content_digest="c" * 64,
        provenance_ref="task-receipt://task-1",
        version=1,
        validity="ACTIVE",
        confidence=0.9,
    )
    migration = IdentityMigrationPlanV1(
        migration_id="migration-1",
        previous_snapshot_digest="d" * 64,
        proposed_snapshot_digest="e" * 64,
        diff_digest="f" * 64,
        regression_report_ref="artifact://identity/regression-1",
        review_ref="review://identity/migration-1",
        rollback_snapshot_digest="d" * 64,
        activation_version=2,
    )

    assert feedback.applied is False
    assert knowledge.namespace.startswith("project-knowledge://")
    assert migration.activation_version == 2


def test_ne06_rejects_unreviewed_feedback_and_cross_namespace_shape() -> None:
    with pytest.raises(KnowledgeEvolutionContractError):
        FeedbackProposalV1(
            proposal_id="feedback-1",
            source_kind="user_reject",
            trace_digest="a" * 64,
            outcome_digest="b" * 64,
            confidence=0.5,
            target_version="persona-v2",
            change={"style": "unsafe"},
            rollback_ref="identity://rollback/v1",
            review_required=False,
        )
    with pytest.raises(KnowledgeEvolutionContractError):
        KnowledgeEntryV1(
            namespace="memory://wrong",
            entry_id="entry",
            workspace_id="workspace-a",
            content_digest="c" * 64,
            provenance_ref="task://1",
            version=1,
            validity="ACTIVE",
            confidence=0.5,
        )
