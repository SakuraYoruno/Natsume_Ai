from __future__ import annotations

import asyncio
from dataclasses import replace
import json
from pathlib import Path
import sqlite3

import pytest

from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.contracts.agent_runtime.review_policy import (
    ReviewItem,
    ReviewResumePhase,
    ReviewStatus,
    ReviewType,
)
from host_brain.contracts.evolution import (
    DecisionRecordV1,
    IdentityMigrationPlanV2,
    KnowledgeEntryV2,
)
from host_brain.contracts.evolution_lab import EvolutionProposalV2
from host_brain.evolution.version_activation import (
    EXPRESSION_PREFERENCE_POLICY_KIND,
    EvolutionVersionActivationService,
    PERSONA_POLICY_KIND,
)
from host_brain.evolution.task_feedback import (
    EvolutionFeedbackAcceptance,
)
from host_brain.evolution.knowledge_recaller import (
    KnowledgeOSSemanticRecaller,
)
from host_brain.app.recall.semantic_recall import (
    SemanticHit,
    SemanticRecallResult,
)
from host_brain.persistence.evolution_activation_v2_evidence import (
    EvolutionActivationV2Error,
    feedback_subject_ref,
    feedback_target_kind,
    identity_target_kind,
    knowledge_subject_ref,
    knowledge_target_kind,
    payload_digest,
)
from host_brain.persistence.evolution_activation_v2_repository import (
    SQLiteEvolutionActivationV2Repository,
)
from host_brain.persistence.evolution_lab_trusted_repository import (
    SQLiteTrustedEvolutionLabLedger,
)
from host_brain.persistence.evolution_lab_v2_repository import (
    SQLiteEvolutionLabV2Ledger,
)
from host_brain.persistence.review_repository import ReviewStore
from host_brain.persona.personality_traits import DEFAULT_BASELINE
from host_brain.release.identity_snapshot import (
    IdentityDiffSeverity,
    diff_identity_snapshots,
)
from host_brain.release.identity_activation import (
    IdentityReleaseActivationService,
)
from tests.contract.test_nle_identity_snapshot_contract import (
    _build as _build_identity,
    _identity_root,
)
from tests.integration.test_ne04_trusted_evolution_lab_ledger import (
    _complete_pair,
    _report,
    _spec,
)


WORKSPACE = "workspace-ne04-ledger"


class _Clock:
    def __init__(self, value: int = 2_200_000_000_000) -> None:
        self.value = value

    def __call__(self) -> int:
        self.value += 1
        return self.value


def _runtime(tmp_path: Path):
    db = tmp_path / "ne06-v2.sqlite3"
    migrated = SQLiteMigrationRunner(db_path=db).apply_migrations(
        request_id="ne06-v2-integration",
        ts_ms=2_200_000_000_000,
    )
    assert migrated.success, migrated.as_dict()
    assert migrated.summary["latest_version"] == 152
    clock = _Clock()
    repository = SQLiteEvolutionActivationV2Repository(
        db,
        clock=clock,
    )
    return (
        db,
        clock,
        repository,
        EvolutionVersionActivationService(repository),
        SQLiteEvolutionLabV2Ledger(db),
        SQLiteTrustedEvolutionLabLedger(db, clock=clock),
        ReviewStore(db, clock=clock),
    )


def test_ne06_feedback_baseline_digest_is_scoped_per_subject(
    tmp_path: Path,
) -> None:
    (
        db,
        _clock,
        _repository,
        service,
        _v2,
        _trusted,
        _reviews,
    ) = _runtime(tmp_path)

    first = service.stage_feedback_signal(
        workspace_id=WORKSPACE,
        subject_id="runtime-subject-a",
        source_kind="verification",
        outcome="APPROVED",
        evidence_refs=("task-receipt://shared-evidence",),
    )
    second = service.stage_feedback_signal(
        workspace_id=WORKSPACE,
        subject_id="runtime-subject-b",
        source_kind="verification",
        outcome="APPROVED",
        evidence_refs=("task-receipt://shared-evidence",),
    )

    assert first["status"] == "STAGED"
    assert second["status"] == "STAGED"
    assert first["active_digest"] == second["active_digest"]
    with sqlite3.connect(db) as conn:
        rows = conn.execute(
            """
            SELECT workspace_id, subject_id, policy_kind, version_digest
            FROM evolution_policy_version_v2
            WHERE version_digest = ?
            ORDER BY subject_id
            """,
            (first["active_digest"],),
        ).fetchall()
    assert [row[1] for row in rows] == [
        "runtime-subject-a",
        "runtime-subject-b",
    ]


def _trusted_authority(
    *,
    v2: SQLiteEvolutionLabV2Ledger,
    trusted: SQLiteTrustedEvolutionLabLedger,
    target_kind: str,
    target_version: str,
    suffix: str,
):
    spec = _spec(f"ne06_{suffix}")
    baseline, candidate = _complete_pair(
        v2_ledger=v2,
        trusted_ledger=trusted,
        spec=spec,
    )
    report = replace(
        _report(
            spec=spec,
            kind=f"ne06_{suffix}",
            baseline=baseline,
            candidate=candidate,
        ),
        report_id=f"report-ne06-{suffix}",
    )
    trusted.append_report(report)
    proposal = EvolutionProposalV2(
        proposal_id=f"proposal-ne06-{suffix}",
        report_digest=report.report_digest,
        target_kind=target_kind,
        target_version=target_version,
        rationale="NE-06 可信演化仅生成待 Review 的版本提案。",
    )
    trusted.append_proposal(proposal)
    return report, proposal


def _approve(
    *,
    reviews: ReviewStore,
    report,
    proposal,
    subject_ref: str,
    subject_version: int,
    evidence_refs: tuple[str, ...],
    suffix: str,
) -> tuple[str, int]:
    review_id = f"review-ne06-{suffix}"
    item = ReviewItem(
        review_id=review_id,
        review_type=ReviewType.PRE_EXECUTION_APPROVAL,
        subject_ref=subject_ref,
        subject_version=subject_version,
        requester="ne06-version-activation",
        reason="批准可信 Lab 版本进入唯一 active projection",
        risk_level=5,
        side_effects=("version-activation",),
        evidence_refs=(
            f"evolution-lab-report://sha256/{report.report_digest}",
            (
                "evolution-proposal://sha256/"
                f"{proposal.proposal_digest}"
            ),
            *evidence_refs,
        ),
        status=ReviewStatus.PENDING,
        expires_at=2_200_100_000_000,
        resume_phase=ReviewResumePhase.PRE_EXECUTION,
        checkpoint_id=f"checkpoint://ne06/{suffix}",
    )
    reviews.create_review(item)
    approved = reviews.approve_review(
        review_id,
        actor_id="operator-ne06",
    )
    return review_id, int(approved["review"]["status_version"])


def _feedback_authority(
    *,
    v2,
    trusted,
    reviews,
    subject_id: str,
    proposal: dict,
    target_version: str,
    target_digest: str,
    subject_version: int,
    suffix: str,
    policy_kind: str = PERSONA_POLICY_KIND,
):
    report, lab_proposal = _trusted_authority(
        v2=v2,
        trusted=trusted,
        target_kind=feedback_target_kind(
            workspace_id=WORKSPACE,
            subject_id=subject_id,
            policy_kind=policy_kind,
        ),
        target_version=target_version,
        suffix=suffix,
    )
    review_id, review_version = _approve(
        reviews=reviews,
        report=report,
        proposal=lab_proposal,
        subject_ref=feedback_subject_ref(
            workspace_id=WORKSPACE,
            subject_id=subject_id,
            policy_kind=policy_kind,
            proposal_digest=proposal["proposal_digest"],
        ),
        subject_version=subject_version,
        evidence_refs=(
            (
                "evolution-feedback-proposal://sha256/"
                f"{proposal['proposal_digest']}"
            ),
            (
                "evolution-policy-version://sha256/"
                f"{target_digest}"
            ),
        ),
        suffix=suffix,
    )
    return report, review_id, review_version


def test_ne06_feedback_apply_rollback_and_new_version_switch_real_projection(
    tmp_path: Path,
) -> None:
    (
        db,
        _clock,
        repository,
        service,
        v2,
        trusted,
        reviews,
    ) = _runtime(tmp_path)
    subject = "natsume_ai"
    staged = service.stage_persona_feedback(
        workspace_id=WORKSPACE,
        subject_id=subject,
        source_kind="stage_promotion",
        current_traits=DEFAULT_BASELINE,
        candidate_delta={"warmth": 0.01},
    )
    proposal = dict(staged["proposal"])
    target_digest = payload_digest(proposal["change"])
    report, review_id, review_version = _feedback_authority(
        v2=v2,
        trusted=trusted,
        reviews=reviews,
        subject_id=subject,
        proposal=proposal,
        target_version=proposal["target_version"],
        target_digest=target_digest,
        subject_version=2,
        suffix="feedback-apply",
    )
    applied = service.apply_feedback(
        workspace_id=WORKSPACE,
        subject_id=subject,
        proposal_id=proposal["proposal_id"],
        lab_report_digest=report.report_digest,
        review_id=review_id,
        review_status_version=review_version,
        expected_generation=1,
        idempotency_key="ne06-feedback-apply",
    )
    replay = service.apply_feedback(
        workspace_id=WORKSPACE,
        subject_id=subject,
        proposal_id=proposal["proposal_id"],
        lab_report_digest=report.report_digest,
        review_id=review_id,
        review_status_version=review_version,
        expected_generation=1,
        idempotency_key="ne06-feedback-apply",
    )
    assert replay.receipt_digest == applied.receipt_digest
    assert service.resolve_persona_traits(
        workspace_id=WORKSPACE,
        subject_id=subject,
    )["warmth"] == pytest.approx(DEFAULT_BASELINE["warmth"] + 0.01)

    rollback_version = str(staged["active_version"])
    rollback_digest = str(staged["active_digest"])
    rollback_report, rollback_review, rollback_review_version = (
        _feedback_authority(
            v2=v2,
            trusted=trusted,
            reviews=reviews,
            subject_id=subject,
            proposal=proposal,
            target_version=rollback_version,
            target_digest=rollback_digest,
            subject_version=3,
            suffix="feedback-rollback",
        )
    )
    rolled_back = service.rollback_feedback(
        workspace_id=WORKSPACE,
        subject_id=subject,
        proposal_id=proposal["proposal_id"],
        lab_report_digest=rollback_report.report_digest,
        review_id=rollback_review,
        review_status_version=rollback_review_version,
        expected_generation=2,
        idempotency_key="ne06-feedback-rollback",
    )
    assert rolled_back.event == "ROLLED_BACK"
    assert service.resolve_persona_traits(
        workspace_id=WORKSPACE,
        subject_id=subject,
    ) == dict(DEFAULT_BASELINE)

    next_stage = service.stage_persona_feedback(
        workspace_id=WORKSPACE,
        subject_id=subject,
        source_kind="milestone",
        current_traits=DEFAULT_BASELINE,
        candidate_delta={"curiosity": 0.02},
    )
    next_proposal = dict(next_stage["proposal"])
    next_digest = payload_digest(next_proposal["change"])
    next_report, next_review, next_review_version = (
        _feedback_authority(
            v2=v2,
            trusted=trusted,
            reviews=reviews,
            subject_id=subject,
            proposal=next_proposal,
            target_version=next_proposal["target_version"],
            target_digest=next_digest,
            subject_version=4,
            suffix="feedback-next",
        )
    )
    next_receipt = service.apply_feedback(
        workspace_id=WORKSPACE,
        subject_id=subject,
        proposal_id=next_proposal["proposal_id"],
        lab_report_digest=next_report.report_digest,
        review_id=next_review,
        review_status_version=next_review_version,
        expected_generation=3,
        idempotency_key="ne06-feedback-next",
    )
    assert next_receipt.event == "APPLIED"
    assert service.resolve_persona_traits(
        workspace_id=WORKSPACE,
        subject_id=subject,
    )["curiosity"] == pytest.approx(
        DEFAULT_BASELINE["curiosity"] + 0.02
    )
    with sqlite3.connect(db) as conn:
        assert conn.execute(
            """
            SELECT COUNT(*)
            FROM evolution_transition_receipt_v1
            WHERE subject_kind = 'FEEDBACK_POLICY'
            """
        ).fetchone()[0] == 4

    before = repository.active_policy(
        workspace_id=WORKSPACE,
        subject_id=subject,
        policy_kind=PERSONA_POLICY_KIND,
    )
    with pytest.raises(
        EvolutionActivationV2Error,
        match="LAB_REPORT_REJECTED",
    ):
        service.rollback_feedback(
            workspace_id=WORKSPACE,
            subject_id=subject,
            proposal_id=next_proposal["proposal_id"],
            lab_report_digest="f" * 64,
            review_id="review-forged",
            review_status_version=1,
            expected_generation=4,
            idempotency_key="ne06-feedback-forged",
        )
    assert repository.active_policy(
        workspace_id=WORKSPACE,
        subject_id=subject,
        policy_kind=PERSONA_POLICY_KIND,
    ) == before
    with sqlite3.connect(db) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM evolution_failure_evidence_v1"
        ).fetchone()[0] == 1


def test_ne06_explicit_response_feedback_stages_expression_policy_only(
    tmp_path: Path,
) -> None:
    (
        db,
        _clock,
        repository,
        service,
        v2,
        trusted,
        reviews,
    ) = _runtime(tmp_path)
    first = service.stage_expression_feedback(
        workspace_id=WORKSPACE,
        subject_id="person-alice",
        target_request_id="request-private-123",
        target_text_hash="a" * 40,
        feedback_score=-1.0,
        feedback_tags=("template_like", "ai_tone"),
        feedback_source="user_explicit",
    )
    replay = service.stage_expression_feedback(
        workspace_id=WORKSPACE,
        subject_id="person-alice",
        target_request_id="request-private-123",
        target_text_hash="a" * 40,
        feedback_score=-1.0,
        feedback_tags=("ai_tone", "template_like"),
        feedback_source="user_explicit",
    )

    assert first["status"] == "STAGED"
    assert replay["proposal"]["proposal_id"] == (
        first["proposal"]["proposal_id"]
    )
    assert first["proposal"]["applied"] is False
    assert (
        first["policy_kind"]
        == EXPRESSION_PREFERENCE_POLICY_KIND
    )
    active = repository.active_policy(
        workspace_id=WORKSPACE,
        subject_id="person-alice",
        policy_kind=EXPRESSION_PREFERENCE_POLICY_KIND,
    )
    assert active is not None
    assert active["payload"]["negative_feedback_count"] == 0
    assert active["payload"]["tag_scores"] == {}
    with sqlite3.connect(db) as conn:
        assert conn.execute(
            """
            SELECT COUNT(*)
            FROM evolution_feedback_proposal_v2
            WHERE policy_kind = 'EXPRESSION_PREFERENCES'
            """
        ).fetchone()[0] == 1
        durable = "\n".join(
            str(value)
            for row in conn.execute(
                """
                SELECT proposal_json, trace_digest
                FROM evolution_feedback_proposal_v2
                WHERE policy_kind = 'EXPRESSION_PREFERENCES'
                """
            ).fetchall()
            for value in row
        )
    assert "request-private-123" not in durable
    proposal = dict(first["proposal"])
    target_digest = payload_digest(proposal["change"])
    report, review_id, review_version = _feedback_authority(
        v2=v2,
        trusted=trusted,
        reviews=reviews,
        subject_id="person-alice",
        proposal=proposal,
        target_version=proposal["target_version"],
        target_digest=target_digest,
        subject_version=2,
        suffix="expression-feedback",
        policy_kind=EXPRESSION_PREFERENCE_POLICY_KIND,
    )
    service.apply_feedback(
        workspace_id=WORKSPACE,
        subject_id="person-alice",
        proposal_id=proposal["proposal_id"],
        lab_report_digest=report.report_digest,
        review_id=review_id,
        review_status_version=review_version,
        expected_generation=1,
        idempotency_key="apply-expression-feedback",
    )
    assert service.resolve_expression_preferences(
        workspace_id=WORKSPACE,
        subject_id="person-alice",
    )["tag_scores"] == {
        "ai_tone": -1,
        "template_like": -1,
    }


def test_ne06_task_verification_rejection_and_tool_feedback_are_proposals(
    tmp_path: Path,
) -> None:
    db, _clock, _repository, service, *_rest = _runtime(tmp_path)

    class _Acceptance:
        def judge(self, **_kwargs):
            return {
                "decision": "REJECT",
                "reason_code": "USER_REJECTED",
                "evidence_refs": [
                    "task-acceptance://sha256/" + "c" * 64
                ],
            }

    observer = EvolutionFeedbackAcceptance(
        inner=_Acceptance(),
        service_provider=lambda: service,
        scope_provider=lambda: (WORKSPACE, "natsume_ai"),
    )
    result = observer.judge(
        command=type(
            "_Command",
            (),
            {
                "command_id": "command-feedback",
                "route_type": "skill",
            },
        )(),
        work_order=type(
            "_WorkOrder",
            (),
            {
                "work_order_id": "work-order-feedback",
                "domain": "code",
            },
        )(),
        execution_context={
            "graph_id": "graph-feedback",
            "node_id": "node-feedback",
            "attempt_id": "attempt-feedback",
        },
        worker_commit={},
        verification_result={
            "status": "PASS",
            "evidence_refs": [
                "task-verification://sha256/" + "d" * 64
            ],
        },
        evidence_refs=(
            "worker-receipt://sha256/" + "e" * 64,
        ),
    )

    assert result["decision"] == "REJECT"
    with sqlite3.connect(db) as conn:
        sources = {
            json.loads(str(row[0]))["source_kind"]
            for row in conn.execute(
                """
                SELECT proposal_json
                FROM evolution_feedback_proposal_v2
                WHERE policy_kind = 'FEEDBACK_SIGNAL_LEDGER'
                """
            ).fetchall()
        }
        active = conn.execute(
            """
            SELECT payload_json
            FROM evolution_policy_active_projection_v2
            WHERE policy_kind = 'FEEDBACK_SIGNAL_LEDGER'
            """
        ).fetchone()
    assert sources == {
        "verification",
        "task_outcome",
        "tool_reliability",
        "user_rejection",
    }
    assert json.loads(str(active[0]))[
        "accepted_signal_digests"
    ] == []


def _knowledge_entry(
    *,
    key: str,
    version: int,
    summary: str,
    suffix: str,
    validity: str = "VALID",
    conflict_refs: tuple[str, ...] = (),
    supersedes: str = "",
    expires_at_ms: int = 0,
) -> KnowledgeEntryV2:
    return KnowledgeEntryV2(
        namespace=f"project-knowledge://workspace/{WORKSPACE}",
        logical_key=key,
        entry_id=f"knowledge://ne06/{key}/{suffix}",
        workspace_id=WORKSPACE,
        owner_id="natsume_ai",
        content_summary=summary,
        content_digest=payload_digest(summary),
        provenance_ref=f"task-receipt://ne06/{suffix}",
        version=version,
        validity=validity,
        confidence=0.9,
        expires_at_ms=expires_at_ms,
        conflict_refs=conflict_refs,
        decision_refs=(),
        supersedes=supersedes,
    )


def test_ne06_expired_knowledge_cannot_enter_active_projection(
    tmp_path: Path,
) -> None:
    _db, clock, _repository, service, *_rest = _runtime(tmp_path)
    expired = _knowledge_entry(
        key="expired-policy",
        version=1,
        summary="已经过期的项目结论。",
        suffix="expired",
        expires_at_ms=clock.value,
    )
    service.register_knowledge(expired)
    with pytest.raises(
        EvolutionActivationV2Error,
        match="EVOLUTION_KNOWLEDGE_BACKFILL_ENTRY_INVALID",
    ):
        service.backfill_knowledge(
            workspace_id=WORKSPACE,
            entry_id=expired.entry_id,
            migration_ref=(
                "legacy-reference://sha256/" + "9" * 64
            ),
            idempotency_key="expired-knowledge-backfill",
        )


def test_ne06_active_knowledge_stops_reading_after_expiry(
    tmp_path: Path,
) -> None:
    _db, clock, repository, service, *_rest = _runtime(tmp_path)
    entry = _knowledge_entry(
        key="ttl-policy",
        version=1,
        summary="只在当前发布窗口内有效。",
        suffix="ttl",
        expires_at_ms=clock.value + 1_000,
    )
    service.register_knowledge(entry)
    service.backfill_knowledge(
        workspace_id=WORKSPACE,
        entry_id=entry.entry_id,
        migration_ref="legacy-reference://sha256/" + "7" * 64,
        idempotency_key="ttl-knowledge-backfill",
    )
    assert repository.active_knowledge_page(
        workspace_id=WORKSPACE,
        namespace=entry.namespace,
    )["items"]
    clock.value += 2_000
    assert repository.active_knowledge_page(
        workspace_id=WORKSPACE,
        namespace=entry.namespace,
    )["items"] == []


def test_ne06_knowledge_backfill_shadow_then_enforce_read_cutover(
    tmp_path: Path,
) -> None:
    _db, _clock, repository, service, *_rest = _runtime(tmp_path)
    entry = _knowledge_entry(
        key="alpha",
        version=1,
        summary="alpha active project knowledge",
        suffix="legacy-backfill",
    )
    service.register_knowledge(entry)
    first = service.backfill_knowledge(
        workspace_id=WORKSPACE,
        entry_id=entry.entry_id,
        migration_ref="legacy-reference://sha256/" + "1" * 64,
        idempotency_key="knowledge-backfill-alpha",
    )
    replay = service.backfill_knowledge(
        workspace_id=WORKSPACE,
        entry_id=entry.entry_id,
        migration_ref="legacy-reference://sha256/" + "1" * 64,
        idempotency_key="knowledge-backfill-alpha",
    )
    assert replay.receipt_digest == first.receipt_digest

    class _Legacy:
        async def recall(self, query, **_kwargs):
            return SemanticRecallResult(
                hits=[
                    SemanticHit(
                        doc_id="legacy-alpha",
                        content=f"legacy {query}",
                        score=1.0,
                        metadata={},
                        source="legacy",
                    )
                ],
                query=query,
                top_k=5,
                threshold=0.0,
                total_found=1,
                filtered_count=1,
                degraded=False,
                latency_ms=1,
            )

    mode = ["SHADOW"]
    recaller = KnowledgeOSSemanticRecaller(
        repository=repository,
        legacy_recaller=_Legacy(),
        scope_provider=lambda: (WORKSPACE, "natsume_ai"),
        read_mode_provider=lambda: mode[0],
    )
    with pytest.raises(
        RuntimeError,
        match="KNOWLEDGE_OS_LEGACY_WRITE_RETIRED",
    ):
        recaller.add_mock_document("legacy", "body")
    with pytest.raises(
        RuntimeError,
        match="KNOWLEDGE_OS_LEGACY_WRITE_RETIRED",
    ):
        asyncio.run(recaller.add_document("legacy", "body"))
    with pytest.raises(
        RuntimeError,
        match="KNOWLEDGE_OS_LEGACY_WRITE_RETIRED",
    ):
        asyncio.run(recaller.add_documents([{"id": "legacy"}]))
    shadow = asyncio.run(
        recaller.recall(
            "alpha",
            filter_metadata={
                "workspace_id": WORKSPACE,
                "owner_id": "natsume_ai",
                "knowledge_namespace": entry.namespace,
            },
        )
    )
    assert [hit.doc_id for hit in shadow.hits] == ["legacy-alpha"]
    mode[0] = "ENFORCE"
    enforced = asyncio.run(
        recaller.recall(
            "alpha",
            filter_metadata={
                "workspace_id": WORKSPACE,
                "owner_id": "natsume_ai",
                "knowledge_namespace": entry.namespace,
            },
        )
    )
    assert [hit.doc_id for hit in enforced.hits] == [entry.entry_id]
    assert enforced.hits[0].source == "knowledge_os_v2"
    stats = recaller.public_runtime_stats(
        observed_at_ms=2_200_000_000_100
    )
    assert stats["knowledge_os_read_authority"] == (
        "V2_ACTIVE_PROJECTION"
    )
    assert stats["evolution_activation"][
        "active_knowledge_count"
    ] == 1


def _activate_knowledge(
    *,
    service,
    v2,
    trusted,
    reviews,
    entry: KnowledgeEntryV2,
    expected_generation: int,
    suffix: str,
):
    report, proposal = _trusted_authority(
        v2=v2,
        trusted=trusted,
        target_kind=knowledge_target_kind(entry_id=entry.entry_id),
        target_version=str(entry.version),
        suffix=suffix,
    )
    review_id, review_version = _approve(
        reviews=reviews,
        report=report,
        proposal=proposal,
        subject_ref=knowledge_subject_ref(
            entry_id=entry.entry_id,
            entry_digest=entry.entry_digest,
        ),
        subject_version=entry.version,
        evidence_refs=(
            (
                "evolution-knowledge-entry://sha256/"
                f"{entry.entry_digest}"
            ),
            entry.provenance_ref,
            *entry.decision_refs,
        ),
        suffix=suffix,
    )
    return service.activate_knowledge(
        workspace_id=WORKSPACE,
        entry_id=entry.entry_id,
        lab_report_digest=report.report_digest,
        review_id=review_id,
        review_status_version=review_version,
        expected_generation=expected_generation,
        idempotency_key=f"activate-knowledge-{suffix}",
    )


def test_ne06_decision_record_enters_reviewed_project_knowledge(
    tmp_path: Path,
) -> None:
    (
        _db,
        _clock,
        repository,
        service,
        v2,
        trusted,
        reviews,
    ) = _runtime(tmp_path)
    record = DecisionRecordV1(
        decision_id="adr://workspace-ne04-ledger/001",
        decision_key="runtime-authority",
        workspace_id=WORKSPACE,
        owner_id="natsume_ai",
        title="统一演化激活 authority",
        decision_summary="所有长期资产只从 V2 active projection 读取。",
        rationale_summary="避免 V1 ledger 与运行期状态形成双重真相。",
        consequence_summary="任何变更都必须先经过 Lab 和 Review。",
        status="ACCEPTED",
        version=1,
        evidence_refs=(
            "architecture-evidence://sha256/" + "8" * 64,
        ),
    )
    registered = service.register_decision_record(record)
    assert registered == service.register_decision_record(record)
    stored = service.decision_record(record.decision_id)
    assert stored is not None
    assert stored["decision_digest"] == record.decision_digest
    assert stored["knowledge_entry_id"] == registered["entry_id"]

    entry = record.to_knowledge_entry()
    activated = _activate_knowledge(
        service=service,
        v2=v2,
        trusted=trusted,
        reviews=reviews,
        entry=entry,
        expected_generation=0,
        suffix="decision-record",
    )
    assert activated.event == "ACTIVATED"
    page = repository.active_knowledge_page(
        workspace_id=WORKSPACE,
        namespace=entry.namespace,
    )
    assert [item["entry_id"] for item in page["items"]] == [
        entry.entry_id
    ]
    assert page["items"][0]["logical_key"] == (
        "decision:runtime-authority"
    )


def test_ne06_knowledge_active_winner_conflict_cursor_and_deprecation(
    tmp_path: Path,
) -> None:
    (
        db,
        _clock,
        repository,
        service,
        v2,
        trusted,
        reviews,
    ) = _runtime(tmp_path)
    first = _knowledge_entry(
        key="release-policy",
        version=1,
        summary="发布必须先通过本地验收。",
        suffix="first",
    )
    service.register_knowledge(first)
    _activate_knowledge(
        service=service,
        v2=v2,
        trusted=trusted,
        reviews=reviews,
        entry=first,
        expected_generation=0,
        suffix="knowledge-first",
    )
    duplicate = _knowledge_entry(
        key="release-policy",
        version=2,
        summary=first.content_summary,
        suffix="duplicate",
        supersedes=first.entry_id,
    )
    with pytest.raises(
        EvolutionActivationV2Error,
        match="DUPLICATE_CONTENT",
    ):
        service.register_knowledge(duplicate)

    competing = _knowledge_entry(
        key="release-policy",
        version=2,
        summary="发布必须通过本地验收并保留回滚证据。",
        suffix="competing",
        validity="CONFLICTED",
        conflict_refs=(first.entry_id,),
    )
    service.register_knowledge(competing)
    _activate_knowledge(
        service=service,
        v2=v2,
        trusted=trusted,
        reviews=reviews,
        entry=competing,
        expected_generation=1,
        suffix="knowledge-competing",
    )
    other = _knowledge_entry(
        key="testing-policy",
        version=1,
        summary="定向测试不能冒充全栈验收。",
        suffix="other",
    )
    service.register_knowledge(other)
    _activate_knowledge(
        service=service,
        v2=v2,
        trusted=trusted,
        reviews=reviews,
        entry=other,
        expected_generation=0,
        suffix="knowledge-other",
    )
    namespace = first.namespace
    first_page = service.active_knowledge_page(
        workspace_id=WORKSPACE,
        namespace=namespace,
        limit=1,
    )
    assert len(first_page["items"]) == 1
    assert first_page["next_cursor"]
    second_page = service.active_knowledge_page(
        workspace_id=WORKSPACE,
        namespace=namespace,
        limit=1,
        cursor=first_page["next_cursor"],
    )
    assert len(second_page["items"]) == 1
    assert {
        first_page["items"][0]["entry_id"],
        second_page["items"][0]["entry_id"],
    } == {competing.entry_id, other.entry_id}
    assert service.active_knowledge_page(
        workspace_id="other-workspace",
        namespace="project-knowledge://workspace/other-workspace",
    )["items"] == []

    report, proposal = _trusted_authority(
        v2=v2,
        trusted=trusted,
        target_kind=knowledge_target_kind(
            entry_id=competing.entry_id
        ),
        target_version=f"deprecate-{competing.version}",
        suffix="knowledge-deprecate",
    )
    review_id, review_version = _approve(
        reviews=reviews,
        report=report,
        proposal=proposal,
        subject_ref=knowledge_subject_ref(
            entry_id=competing.entry_id,
            entry_digest=competing.entry_digest,
        ),
        subject_version=3,
        evidence_refs=(
            (
                "evolution-knowledge-entry://sha256/"
                f"{competing.entry_digest}"
            ),
        ),
        suffix="knowledge-deprecate",
    )
    deprecated = service.deprecate_knowledge(
        workspace_id=WORKSPACE,
        entry_id=competing.entry_id,
        lab_report_digest=report.report_digest,
        review_id=review_id,
        review_status_version=review_version,
        expected_generation=2,
        idempotency_key="deprecate-knowledge-competing",
    )
    assert deprecated.event == "DEPRECATED"
    searched = service.search_active_knowledge(
        workspace_id=WORKSPACE,
        namespace=namespace,
        query="定向测试",
        top_k=5,
        threshold=0.5,
    )
    assert [item["doc_id"] for item in searched] == [other.entry_id]

    with sqlite3.connect(db) as conn:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute(
                """
                INSERT INTO evolution_knowledge_active_projection_v2(
                    workspace_id, namespace, logical_key, entry_id,
                    entry_digest, activation_generation,
                    projection_digest, updated_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    WORKSPACE,
                    namespace,
                    "direct-write",
                    first.entry_id,
                    first.entry_digest,
                    1,
                    "0" * 64,
                    1,
                ),
            )
    assert repository.active_knowledge_page(
        workspace_id=WORKSPACE,
        namespace=namespace,
    )["items"] == [other.to_dict()]


def _identity_authority(
    *,
    v2,
    trusted,
    reviews,
    migration_id: str,
    target_version: str,
    subject_ref: str,
    subject_version: int,
    evidence_refs: tuple[str, ...],
    suffix: str,
):
    report, proposal = _trusted_authority(
        v2=v2,
        trusted=trusted,
        target_kind=identity_target_kind(migration_id=migration_id),
        target_version=target_version,
        suffix=suffix,
    )
    review_id, review_version = _approve(
        reviews=reviews,
        report=report,
        proposal=proposal,
        subject_ref=subject_ref,
        subject_version=subject_version,
        evidence_refs=evidence_refs,
        suffix=suffix,
    )
    return report, review_id, review_version


def test_ne06_identity_release_chain_prepares_reviews_and_activates(
    tmp_path: Path,
) -> None:
    (
        _db,
        _clock,
        _repository,
        service,
        v2,
        trusted,
        reviews,
    ) = _runtime(tmp_path)
    identity_root = tmp_path / "identity-release-root"
    identity_root.mkdir()
    _identity_root(identity_root)
    previous = _build_identity(
        identity_root,
        release="release://natsume/release-chain-v1",
    )
    proposed = _build_identity(
        identity_root,
        release="release://natsume/release-chain-v2",
        previous=previous.release_ref,
        created_at_ms=1_800_000_200_000,
    )
    release = IdentityReleaseActivationService(
        version_activation=service
    )
    release.backfill_previous_release(
        workspace_id=WORKSPACE,
        snapshot=previous,
        activation_version=1,
        idempotency_key="identity-release-chain-baseline",
    )
    preparation = release.prepare_release(
        migration_id="identity-release-chain-v2",
        workspace_id=WORKSPACE,
        previous=previous,
        proposed=proposed,
        activation_version=2,
        regression_checks={
            "persona": True,
            "relationship": True,
            "privacy": True,
        },
    )
    assert preparation.status == "AWAITING_LAB_REVIEW"
    evidence_refs = (
        previous.snapshot_ref,
        proposed.snapshot_ref,
        (
            "identity-regression://sha256/"
            f"{preparation.regression_report_digest}"
        ),
        preparation.diff_ref,
    )
    report, review_id, review_version = _identity_authority(
        v2=v2,
        trusted=trusted,
        reviews=reviews,
        migration_id=preparation.migration_id,
        target_version="2",
        subject_ref=proposed.snapshot_ref,
        subject_version=2,
        evidence_refs=evidence_refs,
        suffix="identity-release-chain",
    )
    ready = release.bind_release_authorities(
        preparation,
        previous=previous,
        proposed=proposed,
        lab_report_digest=report.report_digest,
        review_id=review_id,
        review_status_version=review_version,
    )
    assert ready.status == "READY_FOR_ACTIVATION"
    receipt = release.activate_prepared_release(
        ready,
        expected_generation=1,
        idempotency_key="identity-release-chain-activate",
    )
    assert receipt.event == "ACTIVATED"
    assert service.active_identity(
        workspace_id=WORKSPACE
    )["snapshot"]["snapshot_ref"] == proposed.snapshot_ref


def test_ne06_identity_no_change_major_block_and_exact_rollback(
    tmp_path: Path,
) -> None:
    (
        _db,
        _clock,
        repository,
        service,
        v2,
        trusted,
        reviews,
    ) = _runtime(tmp_path)
    identity_root = tmp_path / "identity-root"
    identity_root.mkdir()
    _identity_root(identity_root)
    previous = _build_identity(
        identity_root,
        release="release://natsume/v1",
    )
    no_change = _build_identity(
        identity_root,
        release="release://natsume/v2",
        previous=previous.release_ref,
        created_at_ms=1_800_000_100_000,
    )
    assert previous.identity_digest == no_change.identity_digest
    service.register_identity_snapshot(
        previous,
        workspace_id=WORKSPACE,
    )
    service.register_identity_snapshot(
        no_change,
        workspace_id=WORKSPACE,
    )
    repository.backfill_identity_projection(
        workspace_id=WORKSPACE,
        snapshot_ref=previous.snapshot_ref,
        activation_version=1,
        migration_ref="evolution-migration://0142/identity-baseline",
        idempotency_key="identity-baseline",
    )
    regression = service.register_identity_regression(
        report_id="identity-regression-no-change",
        workspace_id=WORKSPACE,
        previous_snapshot_ref=previous.snapshot_ref,
        proposed_snapshot_ref=no_change.snapshot_ref,
        checks={
            "persona": True,
            "relationship": True,
            "privacy": True,
        },
    )
    migration_id = "identity-migration-ne06-no-change"
    evidence_refs = (
        previous.snapshot_ref,
        no_change.snapshot_ref,
        (
            "identity-regression://sha256/"
            f"{regression['report_digest']}"
        ),
        regression["diff_ref"],
    )
    report, review_id, review_version = _identity_authority(
        v2=v2,
        trusted=trusted,
        reviews=reviews,
        migration_id=migration_id,
        target_version="2",
        subject_ref=no_change.snapshot_ref,
        subject_version=2,
        evidence_refs=evidence_refs,
        suffix="identity-activate",
    )
    plan = IdentityMigrationPlanV2(
        migration_id=migration_id,
        workspace_id=WORKSPACE,
        previous_snapshot_ref=previous.snapshot_ref,
        proposed_snapshot_ref=no_change.snapshot_ref,
        previous_snapshot_digest=previous.identity_digest.removeprefix(
            "sha256:"
        ),
        proposed_snapshot_digest=no_change.identity_digest.removeprefix(
            "sha256:"
        ),
        diff_digest=regression["diff_digest"],
        lab_report_digest=report.report_digest,
        regression_report_digest=regression["report_digest"],
        review_id=review_id,
        review_status_version=review_version,
        rollback_snapshot_ref=previous.snapshot_ref,
        activation_version=2,
        no_change_release=True,
    )
    service.register_identity_plan(plan)
    activated = service.activate_identity(
        workspace_id=WORKSPACE,
        migration_id=migration_id,
        expected_generation=1,
        idempotency_key="identity-no-change-activate",
    )
    assert activated.event == "ACTIVATED"
    assert service.active_identity(
        workspace_id=WORKSPACE
    )["snapshot"]["snapshot_ref"] == no_change.snapshot_ref

    rollback_report, rollback_review, rollback_review_version = (
        _identity_authority(
            v2=v2,
            trusted=trusted,
            reviews=reviews,
            migration_id=migration_id,
            target_version="rollback-2",
            subject_ref=previous.snapshot_ref,
            subject_version=3,
            evidence_refs=evidence_refs,
            suffix="identity-rollback",
        )
    )
    rolled_back = service.rollback_identity(
        workspace_id=WORKSPACE,
        migration_id=migration_id,
        lab_report_digest=rollback_report.report_digest,
        review_id=rollback_review,
        review_status_version=rollback_review_version,
        expected_generation=2,
        idempotency_key="identity-no-change-rollback",
    )
    assert rolled_back.event == "ROLLED_BACK"
    assert service.active_identity(
        workspace_id=WORKSPACE
    )["snapshot"]["snapshot_ref"] == previous.snapshot_ref

    from host_brain.persona.drift_detector import StyleFingerprint

    major = _build_identity(
        identity_root,
        release="release://natsume/v3",
        previous=previous.release_ref,
        style=StyleFingerprint(
            warmth=0.2,
            curiosity=0.8,
            humor=0.4,
            sensitivity=0.65,
            assertiveness=0.55,
            playfulness=0.45,
            resilience=0.75,
        ),
    )
    assert (
        diff_identity_snapshots(previous, major).severity
        is IdentityDiffSeverity.MAJOR
    )
    service.register_identity_snapshot(major, workspace_id=WORKSPACE)
    blocked_regression = service.register_identity_regression(
        report_id="identity-regression-major-blocked",
        workspace_id=WORKSPACE,
        previous_snapshot_ref=previous.snapshot_ref,
        proposed_snapshot_ref=major.snapshot_ref,
        checks={
            "persona": False,
            "relationship": True,
            "privacy": True,
        },
    )
    blocked_plan = IdentityMigrationPlanV2(
        migration_id="identity-migration-ne06-major",
        workspace_id=WORKSPACE,
        previous_snapshot_ref=previous.snapshot_ref,
        proposed_snapshot_ref=major.snapshot_ref,
        previous_snapshot_digest=previous.identity_digest.removeprefix(
            "sha256:"
        ),
        proposed_snapshot_digest=major.identity_digest.removeprefix(
            "sha256:"
        ),
        diff_digest=blocked_regression["diff_digest"],
        lab_report_digest=report.report_digest,
        regression_report_digest=blocked_regression["report_digest"],
        review_id=review_id,
        review_status_version=review_version,
        rollback_snapshot_ref=previous.snapshot_ref,
        activation_version=3,
        no_change_release=False,
    )
    with pytest.raises(
        EvolutionActivationV2Error,
        match="IDENTITY_PLAN_AUTHORITY_DRIFT",
    ):
        service.register_identity_plan(blocked_plan)
    assert service.active_identity(
        workspace_id=WORKSPACE
    )["snapshot"]["snapshot_ref"] == previous.snapshot_ref
    serialized = json.dumps(
        service.active_identity(workspace_id=WORKSPACE),
        ensure_ascii=False,
    )
    assert "PRIVATE_BODY_MUST_NEVER_ENTER" not in serialized
