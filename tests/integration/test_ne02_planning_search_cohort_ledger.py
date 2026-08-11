from __future__ import annotations

import hashlib
from pathlib import Path
import sqlite3

import pytest

from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.contracts.planning_search.research_benchmark_contracts import (
    PairedResearchRunReceiptV1,
    ResearchArmPlanV1,
    ResearchCorpusBindingV1,
    ResearchExecutionBudgetV1,
    ResearchExecutionProfileV1,
)
from host_brain.contracts.planning_search.research_benefit_cohort import (
    ResearchBenefitCohortSpecV1,
    ResearchCohortPairedEvidenceV1,
    ResearchCohortWorkloadV1,
)
from host_brain.persistence.code_benchmark_ledger_repository import (
    SQLiteCodeHistoricalBenchmarkLedger,
)
from host_brain.persistence.planning_search_calibration_repository import (
    SQLitePlanningCalibrationRepository,
)
from host_brain.persistence.planning_search_cohort_authority_resolver import (
    SQLitePlanningSearchCohortAuthorityResolver,
)
from host_brain.persistence.planning_search_cohort_ledger_repository import (
    COHORT_SCHEMA_NOT_READY,
    COHORT_SOURCE_AUTHORITY_REJECTED,
    PLANNING_SEARCH_COHORT_MIGRATION_ASSET_KEY,
    PLANNING_SEARCH_COHORT_MIGRATION_SHA256,
    PlanningSearchCohortLedgerError,
    SQLitePlanningSearchCohortLedger,
)
from host_brain.persistence.research_shadow_ledger_repository import (
    SQLiteResearchShadowLedger,
)
from tests.unit.test_d20_research_benefit_cohort import (
    _citation_receipt,
    _model_receipt,
    _quality_receipt,
)
from tests.unit.test_ne02_code_benefit_cohort import (
    _passing_rows as _code_rows,
    _spec as _code_spec,
)
from tests.unit.test_ne02_pentest_calibration_cohort import (
    _evidence as _pentest_evidence,
    _spec as _pentest_spec,
)


NOW = 2_026_072_800_000


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class _PentestReceiptAuthority:
    def __init__(self) -> None:
        self.digests: dict[str, str] = {}

    def add(self, evidence) -> None:  # noqa: ANN001
        self.digests.update(
            {
                evidence.task_receipt_ref: evidence.task_receipt_digest,
                evidence.authorization_receipt_ref: (
                    evidence.authorization_receipt_digest
                ),
                evidence.verification_receipt_ref: (
                    evidence.verification_receipt_digest
                ),
            }
        )

    def resolve_receipt_digest(
        self,
        *,
        receipt_ref: str,
    ) -> str | None:
        return self.digests.get(receipt_ref)


class _RejectingResolver:
    def resolve_research(self, **_kwargs):  # noqa: ANN003, ANN201
        raise AssertionError("source resolver must not be called")

    def resolve_code(self, **_kwargs):  # noqa: ANN003, ANN201
        raise AssertionError("source resolver must not be called")

    def resolve_pentest(self, **_kwargs):  # noqa: ANN003, ANN201
        raise AssertionError("source resolver must not be called")


def _migrated_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "planning-search-cohorts.sqlite3"
    migrated = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="ne02-cohort-ledger",
        ts_ms=NOW,
    )
    assert migrated.success is True, migrated.as_dict()
    return db_path


def _research_cohort(
    *,
    ledger: SQLiteResearchShadowLedger,
) -> tuple[
    ResearchBenefitCohortSpecV1,
    tuple[ResearchCohortPairedEvidenceV1, ...],
]:
    binding = ResearchCorpusBindingV1(
        snapshot_digest=_digest("ne02-research-snapshot"),
        corpus_ref="artifact://research/ne02/frozen-corpus",
        manifest_digest=_digest("ne02-research-manifest"),
        source_ids=("local", "web"),
        source_refs=(
            "artifact://research/ne02/local",
            "https://example.invalid/ne02/frozen",
        ),
        source_digests=(
            _digest("ne02-local-source"),
            _digest("ne02-web-source"),
        ),
        authority_tiers=("primary", "secondary"),
    )
    profile = ResearchExecutionProfileV1(
        prompt_digest=_digest("ne02-research-prompt"),
        profile_digest=_digest("ne02-research-profile"),
        output_schema_digest=_digest("ne02-research-output-schema"),
    )
    budget = ResearchExecutionBudgetV1(
        arm_max_tokens=1_000,
        arm_max_tool_calls=4,
        arm_max_wall_time_ms=30_000,
        pair_max_tokens=2_000,
        pair_max_wall_time_ms=60_000,
    )
    registration_id = ledger.register(binding, profile, budget)
    verifier_profile_digest = _digest("ne02-quality-profile")
    rubric_digest = _digest("ne02-quality-rubric")
    workloads: list[ResearchCohortWorkloadV1] = []
    rows: list[ResearchCohortPairedEvidenceV1] = []
    topics = ("architecture", "security", "data")

    for index in range(24):
        workload_id = f"research-authority-{index:02d}"
        baseline = ResearchArmPlanV1(
            arm_id=f"research-baseline-{index:02d}",
            candidate_ids=(f"baseline-candidate-{index:02d}",),
            source_ids=("local",),
            graph_digest=_digest(f"baseline-graph:{index}"),
            path_digest=_digest(f"baseline-path:{index}"),
        )
        selected = ResearchArmPlanV1(
            arm_id=f"research-selected-{index:02d}",
            candidate_ids=(f"selected-candidate-{index:02d}",),
            source_ids=("local", "web"),
            graph_digest=_digest(f"selected-graph:{index}"),
            path_digest=_digest(f"selected-path:{index}"),
        )
        run_id = ledger.preregister_randomized_run(
            registration_id=registration_id,
            baseline=baseline,
            selected=selected,
            cohort_run_key=f"ne02-research-cohort-{index:02d}",
        )
        run_spec = ledger.get_paired_run_spec(run_id=run_id)
        baseline_execution_id = f"{run_id}-baseline"
        selected_execution_id = f"{run_id}-selected"
        baseline_model = _model_receipt(
            baseline_execution_id,
            baseline.arm_id,
            response=f"baseline-response-{index:02d}",
            cost=100,
            latency=100,
        )
        selected_model = _model_receipt(
            selected_execution_id,
            selected.arm_id,
            response=f"selected-response-{index:02d}",
            cost=120,
            latency=120,
        )
        baseline_verifier = _citation_receipt(
            baseline_execution_id,
            0.98,
        )
        selected_verifier = _citation_receipt(
            selected_execution_id,
            0.98,
        )
        ledger.append_arm_execution(
            run_id=run_id,
            role="BASELINE",
            plan=baseline,
            model=baseline_model,
            verifier=baseline_verifier,
        )
        ledger.append_arm_execution(
            run_id=run_id,
            role="SELECTED",
            plan=selected,
            model=selected_model,
            verifier=selected_verifier,
        )
        terminal = PairedResearchRunReceiptV1(
            corpus_binding_digest=binding.binding_digest,
            profile_digest=profile.profile_digest,
            budget_digest=budget.budget_digest,
            baseline_execution_id=baseline_execution_id,
            selected_execution_id=selected_execution_id,
            baseline_model=baseline_model,
            selected_model=selected_model,
            baseline_verifier=baseline_verifier,
            selected_verifier=selected_verifier,
            arm_order=run_spec.arm_order,
            randomized_arm_order=True,
        )
        receipt_ref = ledger.complete(run_id=run_id, receipt=terminal)
        receipt_digest = receipt_ref.rsplit("/", 1)[1]
        workloads.append(
            ResearchCohortWorkloadV1(
                workload_id=workload_id,
                run_id=run_id,
                terminal_receipt_digest=receipt_digest,
                topic_category=topics[index % len(topics)],
                source_kinds=(
                    ("LOCAL_FILE",)
                    if index % 3 == 0
                    else ("WEB",)
                    if index % 3 == 1
                    else ("LOCAL_FILE", "WEB")
                ),
            )
        )
        rows.append(
            ResearchCohortPairedEvidenceV1(
                workload_id=workload_id,
                run_id=run_id,
                terminal_receipt_digest=receipt_digest,
                terminal_receipt=terminal,
                baseline_quality=_quality_receipt(
                    run_id=run_id,
                    role="BASELINE",
                    arm_execution_id=baseline_execution_id,
                    response_digest=(
                        baseline_model.attestation.response_digest
                    ),
                    score=0.70,
                    verifier_profile_digest=verifier_profile_digest,
                    rubric_digest=rubric_digest,
                ),
                selected_quality=_quality_receipt(
                    run_id=run_id,
                    role="SELECTED",
                    arm_execution_id=selected_execution_id,
                    response_digest=(
                        selected_model.attestation.response_digest
                    ),
                    score=0.80,
                    verifier_profile_digest=verifier_profile_digest,
                    rubric_digest=rubric_digest,
                ),
                owner_randomized_run=True,
            )
        )

    return (
        ResearchBenefitCohortSpecV1(
            cohort_id="ne02-research-authority-24",
            cohort_version="v1",
            planning_algorithm_version="planning-search-v3",
            execution_provider="openai",
            execution_model="gpt-real",
            verifier_provider="anthropic",
            verifier_model="quality-real",
            verifier_profile_digest=verifier_profile_digest,
            rubric_digest=rubric_digest,
            randomization_seed_digest=_digest("ne02-research-seed"),
            workloads=tuple(workloads),
        ),
        tuple(rows),
    )


def _populate_source_authorities(
    db_path: Path,
) -> tuple[
    SQLitePlanningSearchCohortAuthorityResolver,
    ResearchBenefitCohortSpecV1,
    tuple[ResearchCohortPairedEvidenceV1, ...],
    object,
    tuple,
    object,
    tuple,
    _PentestReceiptAuthority,
]:
    research_ledger = SQLiteResearchShadowLedger(
        db_path,
        clock=lambda: NOW + 1,
    )
    research_spec, research_rows = _research_cohort(
        ledger=research_ledger
    )

    code_ledger = SQLiteCodeHistoricalBenchmarkLedger(
        db_path,
        clock=lambda: NOW + 2,
    )
    code_rows = _code_rows()
    code_spec = _code_spec(code_rows)
    for row in code_rows:
        receipt = row.terminal_receipt
        code_ledger.register_workload(receipt.spec.registration)
        code_ledger.preregister_run(receipt.spec)
        code_ledger.record_completed_run(receipt)

    calibration = SQLitePlanningCalibrationRepository(
        db_path,
        clock=lambda: NOW + 3,
    )
    pentest_spec = _pentest_spec()
    pentest_rows = tuple(
        _pentest_evidence(workload)
        for workload in pentest_spec.workloads
    )
    pentest_receipts = _PentestReceiptAuthority()
    for row in pentest_rows:
        calibration.record_prediction(row.prediction)
        calibration.reconcile_outcome(row.outcome)
        pentest_receipts.add(row)

    resolver = SQLitePlanningSearchCohortAuthorityResolver(
        research_ledger=research_ledger,
        code_ledger=code_ledger,
        calibration_repository=calibration,
        pentest_receipt_authority=pentest_receipts,
    )
    return (
        resolver,
        research_spec,
        research_rows,
        code_spec,
        code_rows,
        pentest_spec,
        pentest_rows,
        pentest_receipts,
    )


def test_fixed_cohorts_reopen_lower_authorities_and_survive_restart(
    tmp_path: Path,
) -> None:
    db_path = _migrated_db(tmp_path)
    (
        resolver,
        research_spec,
        research_rows,
        code_spec,
        code_rows,
        pentest_spec,
        pentest_rows,
        pentest_receipts,
    ) = _populate_source_authorities(db_path)
    owner = SQLitePlanningSearchCohortLedger(
        db_path,
        authority_resolver=resolver,
        clock=lambda: NOW + 10,
    )

    assert owner.register_research(research_spec) == research_spec.spec_digest
    assert owner.register_code(code_spec) == code_spec.spec_digest
    assert owner.register_pentest(pentest_spec) == pentest_spec.spec_digest

    missing_ref = pentest_rows[0].verification_receipt_ref
    pentest_receipts.digests.pop(missing_ref)
    with pytest.raises(PlanningSearchCohortLedgerError) as rejected:
        owner.append_pentest_member(
            spec=pentest_spec,
            evidence=pentest_rows[0],
        )
    assert rejected.value.reason_code == COHORT_SOURCE_AUTHORITY_REJECTED
    pentest_receipts.add(pentest_rows[0])

    research_proofs = tuple(
        owner.append_research_member(spec=research_spec, evidence=row)
        for row in research_rows
    )
    code_proofs = tuple(
        owner.append_code_member(spec=code_spec, evidence=row)
        for row in code_rows
    )
    pentest_proofs = tuple(
        owner.append_pentest_member(spec=pentest_spec, evidence=row)
        for row in pentest_rows
    )
    assert len({proof.proof_digest for proof in research_proofs}) == 24
    assert len({proof.proof_digest for proof in code_proofs}) == 20
    assert len({proof.proof_digest for proof in pentest_proofs}) == 30

    research_report = owner.seal_research(
        spec=research_spec,
        evidence=tuple(reversed(research_rows)),
    )
    code_report = owner.seal_code(
        spec=code_spec,
        evidence=tuple(reversed(code_rows)),
    )
    pentest_report = owner.seal_pentest(
        spec=pentest_spec,
        evidence=tuple(reversed(pentest_rows)),
    )
    assert research_report.benefit_eligible is True
    assert code_report.benefit_eligible is True
    assert pentest_report.calibration_eligible is True

    proposal = owner.append_promotion_proposal(
        target_algorithm_version="planning-search-v4-candidate",
        pentest_report=pentest_report,
        research_report=research_report,
        code_report=code_report,
    )
    assert proposal.proposal_eligible is True
    assert proposal.review_required is True
    assert proposal.activation_authority is False
    assert proposal.production_influence_enabled is False

    restarted = SQLitePlanningSearchCohortLedger(
        db_path,
        authority_resolver=_RejectingResolver(),
        clock=lambda: NOW + 99,
    )
    replayed = restarted.append_research_member(
        spec=research_spec,
        evidence=research_rows[0],
    )
    assert replayed.proof_digest == research_proofs[0].proof_digest
    snapshot = restarted.get_report(
        domain="RESEARCH",
        cohort_id=research_spec.cohort_id,
        cohort_version=research_spec.cohort_version,
    )
    assert snapshot is not None
    assert snapshot.report_digest == research_report.report_digest
    assert restarted.append_promotion_proposal(
        target_algorithm_version="planning-search-v4-candidate",
        pentest_report=pentest_report,
        research_report=research_report,
        code_report=code_report,
    ).proposal_id == proposal.proposal_id

    with sqlite3.connect(db_path) as conn:
        counts = {
            table: int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in (
                "planning_search_cohort_registration",
                "planning_search_cohort_member_evidence",
                "planning_search_cohort_report",
                "planning_search_promotion_evidence",
                "planning_search_promotion_proposal",
            )
        }
        proposal_row = conn.execute(
            """
            SELECT review_required, activation_authority,
                   production_influence_enabled
            FROM planning_search_promotion_proposal
            WHERE proposal_id = ?
            """,
            (proposal.proposal_id,),
        ).fetchone()
    assert counts == {
        "planning_search_cohort_registration": 3,
        "planning_search_cohort_member_evidence": 74,
        "planning_search_cohort_report": 3,
        "planning_search_promotion_evidence": 3,
        "planning_search_promotion_proposal": 1,
    }
    assert proposal_row == (1, 0, 0)


def test_schema_checksum_and_append_only_evidence_fail_closed(
    tmp_path: Path,
) -> None:
    db_path = _migrated_db(tmp_path)
    with sqlite3.connect(db_path) as conn:
        asset = conn.execute(
            """
            SELECT checksum, disposition
            FROM schema_migration_assets
            WHERE asset_key = ?
            """,
            (PLANNING_SEARCH_COHORT_MIGRATION_ASSET_KEY,),
        ).fetchone()
    assert asset == (PLANNING_SEARCH_COHORT_MIGRATION_SHA256, "APPLIED")

    resolver, research_spec, _, *_ = _populate_source_authorities(db_path)
    owner = SQLitePlanningSearchCohortLedger(
        db_path,
        authority_resolver=resolver,
    )
    owner.register_research(research_spec)
    with sqlite3.connect(db_path) as conn:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            conn.execute(
                """
                UPDATE planning_search_cohort_registration
                SET created_at_ms = created_at_ms + 1
                WHERE domain = 'RESEARCH'
                """
            )

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            UPDATE schema_migration_assets
            SET checksum = ?
            WHERE asset_key = ?
            """,
            ("0" * 64, PLANNING_SEARCH_COHORT_MIGRATION_ASSET_KEY),
        )
        conn.commit()
    with pytest.raises(PlanningSearchCohortLedgerError) as drifted:
        SQLitePlanningSearchCohortLedger(
            db_path,
            authority_resolver=resolver,
        )
    assert drifted.value.reason_code == COHORT_SCHEMA_NOT_READY
