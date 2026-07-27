from __future__ import annotations

from dataclasses import replace
import hashlib

import pytest

from host_brain.contracts.planning_search.code_benefit_cohort import (
    CODE_BENEFIT_COHORT_SIZE,
    CodeArmBenefitEvidenceV1,
    CodeBenefitCohortError,
    CodeBenefitCohortEvaluator,
    CodeBenefitCohortSpecV1,
    CodeCohortPairedEvidenceV1,
    CodeCohortWorkloadV1,
)
from host_brain.contracts.planning_search.research_benchmark_contracts import (
    ExternalProviderRunAttestationV1,
)
from host_brain.planning_search import (
    BenchmarkArm,
    BenchmarkArmOutcome,
    DomainBenchmarkReport,
    DomainBenchmarkWorkload,
    SearchBudget,
)
from host_brain.planning_search.code_benchmark_ledger import (
    CodeBenchmarkExecutionBudget,
    CodeBenchmarkRunReceipt,
    CodeBenchmarkRunSpec,
    CodeBenchmarkWorkloadRegistration,
)
from host_brain.planning_search.code_benchmark_models import (
    CODE_HISTORICAL_BENCHMARK_EXECUTOR_TRUST,
    CODE_HISTORICAL_BENCHMARK_MODE,
    CODE_HISTORICAL_BENCHMARK_NETWORK_ISOLATION,
    CodeBenchmarkArmEvidence,
    CodeBenchmarkCleanupReceipt,
    CodeHistoricalBenchmarkRun,
    CodeVerifierResult,
)


COMMIT = "a" * 40
POLICY_DIGEST = "b" * 64
EXECUTION_PROFILE = "c" * 64
HIDDEN_PROFILE = "d" * 64
STATIC_PROFILE = "e" * 64
SEED_DIGEST = "f" * 64


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _arm(index: int, role: str) -> BenchmarkArm:
    candidate = f"{role}-candidate-{index:02d}"
    return BenchmarkArm(
        arm_id=f"{role}-{index:02d}",
        selected_candidate_id=candidate,
        selected_path_candidate_ids=(candidate, f"{role}-verify-{index:02d}"),
        trace_digest=_digest(f"trace:{role}:{index}"),
        counters={"candidates": 2, "rollouts": 1},
        virtual_state_schema="natsume.planning-search.virtual-state.v1",
        virtual_state_digest=_digest(f"state:{index}"),
    )


def _workload(index: int) -> DomainBenchmarkWorkload:
    return DomainBenchmarkWorkload(
        workload_id=f"code-{index:02d}",
        domain="code",
        workload_ref=f"benchmark://code/ne02/{index:02d}",
        baseline_ref=f"benchmark://code/ne02/{index:02d}/baseline",
        verifier_refs=(f"verifier://code/ne02/{index:02d}",),
        isolation={
            "sandbox_mode": CODE_HISTORICAL_BENCHMARK_MODE,
            "source_ref": f"snapshot://code/ne02/{index:02d}",
            "source_commit": COMMIT,
            "path_policy_digest": POLICY_DIGEST,
            "executor_trust": CODE_HISTORICAL_BENCHMARK_EXECUTOR_TRUST,
            "network_isolation": CODE_HISTORICAL_BENCHMARK_NETWORK_ISOLATION,
        },
        budget=SearchBudget(
            max_candidates=4,
            max_expansions=4,
            max_rollouts=2,
        ),
        cohort_key="ne02-code-fixed-20",
    )


def _arm_evidence(
    *,
    index: int,
    arm: BenchmarkArm,
    success: bool,
    diff_digest: str,
) -> CodeBenchmarkArmEvidence:
    cost = {"test_command_count": 1, "verification_wall_time_ms": 10}
    return CodeBenchmarkArmEvidence(
        arm_id=arm.arm_id,
        source_ref=f"snapshot://code/ne02/{index:02d}",
        source_commit=COMMIT,
        path_policy_digest=POLICY_DIGEST,
        changed_file_count=1,
        changed_path_digest=_digest(f"path:{arm.arm_id}"),
        workspace_diff_digest=diff_digest,
        verifier=CodeVerifierResult(
            success=success,
            receipt_ref=f"verifier-receipt://code/{index:02d}/{arm.arm_id}",
            cost=cost,
            evidence_digest=_digest(f"verify:{arm.arm_id}:{success}"),
        ),
    )


def _terminal_receipt(
    index: int,
    *,
    baseline_passed: bool,
    selected_passed: bool,
) -> CodeBenchmarkRunReceipt:
    workload = _workload(index)
    registration = CodeBenchmarkWorkloadRegistration(
        workload=workload,
        execution_budget=CodeBenchmarkExecutionBudget(
            max_model_calls=2,
            max_provider_attempts=2,
            max_tool_calls=4,
            max_test_commands=4,
            max_wall_time_ms=60_000,
            max_token_count=20_000,
            max_model_cost_micros=10_000,
        ),
    )
    baseline = _arm(index, "baseline")
    selected = _arm(index, "selected")
    spec = CodeBenchmarkRunSpec(
        run_id=f"code-run-{index:02d}",
        registration=registration,
        baseline=baseline,
        selected=selected,
    )
    baseline_diff = _digest(f"diff:baseline:{index}")
    selected_diff = _digest(f"diff:selected:{index}")
    baseline_evidence = _arm_evidence(
        index=index,
        arm=baseline,
        success=baseline_passed,
        diff_digest=baseline_diff,
    )
    selected_evidence = _arm_evidence(
        index=index,
        arm=selected,
        success=selected_passed,
        diff_digest=selected_diff,
    )
    report = DomainBenchmarkReport(
        workload=workload,
        baseline=baseline,
        selected=selected,
        baseline_outcome=BenchmarkArmOutcome(
            arm_id=baseline.arm_id,
            verifier_receipt_refs=(baseline_evidence.verifier.receipt_ref,),
            success=baseline_passed,
            latency_ms=100,
            cost=baseline_evidence.verifier.cost,
            workspace_diff_digest=baseline_diff,
            executed=True,
        ),
        selected_outcome=BenchmarkArmOutcome(
            arm_id=selected.arm_id,
            verifier_receipt_refs=(selected_evidence.verifier.receipt_ref,),
            success=selected_passed,
            latency_ms=100,
            cost=selected_evidence.verifier.cost,
            workspace_diff_digest=selected_diff,
            executed=True,
        ),
    )
    run = CodeHistoricalBenchmarkRun(
        run_id=spec.run_id,
        report=report,
        baseline_evidence=baseline_evidence,
        selected_evidence=selected_evidence,
        cleanup_receipt=CodeBenchmarkCleanupReceipt(
            run_id=spec.run_id,
            source_before_digest=_digest(f"source:{index}"),
            source_after_digest=_digest(f"source:{index}"),
            private_bare_removed=True,
            worktrees_removed=True,
            run_root_removed=True,
        ),
    )
    return CodeBenchmarkRunReceipt(spec=spec, run=run)


def _benefit_arm(
    *,
    index: int,
    role: str,
    arm_id: str,
    hidden_passed: bool = True,
    static_passed: bool = True,
    risk: float,
    model_cost: int,
    verification_cost: int = 20,
    with_transport: bool = True,
) -> CodeArmBenefitEvidenceV1:
    execution_id = f"model-{role.lower()}-{index:02d}"
    output_digest = _digest(f"output:{execution_id}")
    hidden_digest = _digest(f"hidden:{role}:{index}")
    static_digest = _digest(f"static:{role}:{index}")
    attestation = ExternalProviderRunAttestationV1(
        execution_id=execution_id,
        provider="openai",
        model="code-real",
        route_digest=_digest(f"route:{execution_id}"),
        response_digest=output_digest,
        total_tokens=100,
        cost_micros=model_cost,
        provider_latency_ms=80,
        total_latency_ms=100,
        transport_identity_digest=(
            _digest("transport:openai") if with_transport else None
        ),
        transport_external=True if with_transport else None,
    )
    return CodeArmBenefitEvidenceV1(
        run_id=f"code-run-{index:02d}",
        arm_role=role,
        arm_id=arm_id,
        model_execution_id=execution_id,
        model_attempt_ids=(f"attempt-{execution_id}",),
        model_output_digest=output_digest,
        execution_profile_digest=EXECUTION_PROFILE,
        hidden_verifier_execution_id=f"hidden-{role.lower()}-{index:02d}",
        hidden_verifier_profile_digest=HIDDEN_PROFILE,
        hidden_test_receipt_ref=f"code-hidden-test://sha256/{hidden_digest}",
        hidden_test_receipt_digest=hidden_digest,
        hidden_test_passed=hidden_passed,
        static_verifier_execution_id=f"static-{role.lower()}-{index:02d}",
        static_verifier_profile_digest=STATIC_PROFILE,
        static_check_receipt_ref=f"code-static-check://sha256/{static_digest}",
        static_check_receipt_digest=static_digest,
        static_check_passed=static_passed,
        diff_risk_score=risk,
        verification_cost_micros=verification_cost,
        evidence_class="ATTESTED_EXTERNAL",
        attestation=attestation,
    )


def _row(
    index: int,
    *,
    baseline_passed: bool,
    selected_passed: bool,
    baseline_cost: int = 100,
    selected_cost: int = 100,
    baseline_risk: float = 0.20,
    selected_risk: float = 0.15,
) -> CodeCohortPairedEvidenceV1:
    receipt = _terminal_receipt(
        index,
        baseline_passed=baseline_passed,
        selected_passed=selected_passed,
    )
    digest = _digest_json(receipt.to_dict())
    return CodeCohortPairedEvidenceV1(
        workload_id=f"code-{index:02d}",
        run_id=f"code-run-{index:02d}",
        terminal_receipt_digest=digest,
        terminal_receipt=receipt,
        baseline=_benefit_arm(
            index=index,
            role="BASELINE",
            arm_id=receipt.spec.baseline.arm_id,
            risk=baseline_risk,
            model_cost=baseline_cost,
        ),
        selected=_benefit_arm(
            index=index,
            role="SELECTED",
            arm_id=receipt.spec.selected.arm_id,
            risk=selected_risk,
            model_cost=selected_cost,
        ),
        owner_randomized_run=True,
        independent_fresh_worktrees=True,
    )


def _digest_json(value) -> str:  # noqa: ANN001
    import json

    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _spec(rows: tuple[CodeCohortPairedEvidenceV1, ...]) -> CodeBenefitCohortSpecV1:
    budget_digest = rows[0].terminal_receipt.spec.registration.execution_budget.budget_digest
    return CodeBenefitCohortSpecV1(
        cohort_id="code-fixed-20",
        cohort_version="v1",
        planning_algorithm_version="planning-search-v1",
        execution_provider="openai",
        execution_model="code-real",
        execution_profile_digest=EXECUTION_PROFILE,
        execution_budget_digest=budget_digest,
        hidden_verifier_profile_digest=HIDDEN_PROFILE,
        static_verifier_profile_digest=STATIC_PROFILE,
        randomization_seed_digest=SEED_DIGEST,
        workloads=tuple(
            CodeCohortWorkloadV1(
                workload_id=row.workload_id,
                run_id=row.run_id,
                terminal_receipt_digest=row.terminal_receipt_digest,
                source_commit=row.terminal_receipt.spec.registration.source_commit,
                path_policy_digest=(
                    row.terminal_receipt.spec.registration.path_policy_digest
                ),
            )
            for row in rows
        ),
    )


def _passing_rows() -> tuple[CodeCohortPairedEvidenceV1, ...]:
    return tuple(
        _row(
            index,
            baseline_passed=index < 10,
            selected_passed=index < 12,
        )
        for index in range(CODE_BENEFIT_COHORT_SIZE)
    )


def test_fixed_code_cohort_accepts_ten_point_pass_rate_gain() -> None:
    rows = _passing_rows()
    report = CodeBenefitCohortEvaluator().evaluate(
        spec=_spec(rows),
        evidence=tuple(reversed(rows)),
    )

    assert report.baseline_pass_rate == 0.5
    assert report.selected_pass_rate == 0.6
    assert report.pass_rate_gain == 0.1
    assert report.reason_codes == ()
    assert report.benefit_eligible is True
    assert report.proposal_eligible is True
    assert report.production_influence_enabled is False


def test_code_cohort_accepts_noninferior_pass_rate_with_cost_reduction() -> None:
    rows = tuple(
        _row(
            index,
            baseline_passed=index < 16,
            selected_passed=index < 15,
            baseline_cost=120,
            selected_cost=80,
        )
        for index in range(CODE_BENEFIT_COHORT_SIZE)
    )
    report = CodeBenefitCohortEvaluator().evaluate(
        spec=_spec(rows),
        evidence=rows,
    )

    assert report.pass_rate_gain == -0.05
    assert report.cost_reduction_rate is not None
    assert report.cost_reduction_rate > 0.15
    assert report.benefit_eligible is True


def test_code_cohort_retains_ineligible_and_regressed_evidence() -> None:
    rows = list(
        _row(
            index,
            baseline_passed=index < 10,
            selected_passed=index < 10,
            baseline_risk=0.20,
            selected_risk=0.20,
        )
        for index in range(CODE_BENEFIT_COHORT_SIZE)
    )
    original = rows[0]
    selected = replace(
        original.selected,
        hidden_test_passed=False,
        static_check_passed=False,
        diff_risk_score=0.30,
        evidence_class="CANONICAL_ATTEMPT_UNATTESTED",
        attestation=None,
    )
    rows[0] = replace(original, selected=selected)
    frozen = tuple(rows)
    report = CodeBenefitCohortEvaluator().evaluate(
        spec=_spec(frozen),
        evidence=frozen,
    )

    assert report.benefit_eligible is False
    assert report.proposal_eligible is False
    assert {
        "MODEL_EVIDENCE_NOT_ELIGIBLE",
        "PASS_RATE_OR_COST_BENEFIT_NOT_MET",
        "HIDDEN_TEST_REGRESSION",
        "STATIC_CHECK_REGRESSION",
        "DIFF_RISK_REGRESSION",
    }.issubset(report.reason_codes)


def test_code_cohort_rejects_size_and_terminal_identity_drift() -> None:
    rows = _passing_rows()
    with pytest.raises(CodeBenefitCohortError, match="exactly 20"):
        replace(_spec(rows), workloads=_spec(rows).workloads[:-1])

    with pytest.raises(CodeBenefitCohortError, match="terminal receipt digest drift"):
        replace(rows[0], terminal_receipt_digest="0" * 64)

    with pytest.raises(CodeBenefitCohortError, match="does not bind its digest"):
        replace(
            rows[0].selected,
            hidden_test_receipt_ref="code-hidden-test://sha256/" + ("0" * 64),
        )

    shared_verifier = replace(
        rows[0].selected,
        hidden_verifier_execution_id=rows[0].baseline.hidden_verifier_execution_id,
    )
    with pytest.raises(CodeBenefitCohortError, match="all be independent"):
        replace(rows[0], selected=shared_verifier)
