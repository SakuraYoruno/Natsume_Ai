"""Pure deterministic evaluator for the fixed Code benefit cohort."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from .code_benefit_cohort_models import (
    CODE_BENEFIT_COHORT_SIZE,
    CODE_BENEFIT_MIN_COST_REDUCTION,
    CODE_BENEFIT_MIN_PASS_RATE_GAIN,
    CODE_BENEFIT_NON_INFERIOR_MARGIN,
    CodeBenefitCohortError,
    CodeBenefitCohortReportV1,
    CodeBenefitCohortSpecV1,
    CodeCohortPairedEvidenceV1,
    _json_digest,
)


class CodeBenefitCohortEvaluator:
    """Evaluate 20 fixed paired Code tasks without mutating any authority."""

    def evaluate(
        self,
        *,
        spec: CodeBenefitCohortSpecV1,
        evidence: Sequence[CodeCohortPairedEvidenceV1],
    ) -> CodeBenefitCohortReportV1:
        if not isinstance(spec, CodeBenefitCohortSpecV1):
            raise CodeBenefitCohortError(
                "spec must be CodeBenefitCohortSpecV1"
            )
        if isinstance(evidence, (str, bytes, bytearray)):
            raise CodeBenefitCohortError("evidence must be a sequence")
        rows = tuple(evidence)
        if len(rows) != CODE_BENEFIT_COHORT_SIZE or any(
            not isinstance(item, CodeCohortPairedEvidenceV1) for item in rows
        ):
            raise CodeBenefitCohortError(
                f"Code cohort evaluation requires exactly {CODE_BENEFIT_COHORT_SIZE} pairs"
            )
        by_workload = {row.workload_id: row for row in rows}
        if len(by_workload) != len(rows):
            raise CodeBenefitCohortError(
                "Code cohort evidence workload ids are not unique"
            )
        expected = {item.workload_id: item for item in spec.workloads}
        if set(by_workload) != set(expected):
            raise CodeBenefitCohortError(
                "Code evidence does not match the preregistered workload set"
            )

        baseline_passes = 0
        selected_passes = 0
        baseline_hidden = 0
        selected_hidden = 0
        baseline_static = 0
        selected_static = 0
        baseline_risk = 0.0
        selected_risk = 0.0
        baseline_cost = 0
        selected_cost = 0
        reasons: set[str] = set()
        evidence_payloads: list[Mapping[str, Any]] = []

        for workload in spec.workloads:
            row = by_workload[workload.workload_id]
            receipt = row.terminal_receipt
            registration = receipt.spec.registration
            if (
                row.run_id != workload.run_id
                or row.terminal_receipt_digest
                != workload.terminal_receipt_digest
                or registration.source_commit != workload.source_commit
                or registration.path_policy_digest
                != workload.path_policy_digest
            ):
                raise CodeBenefitCohortError(
                    "Code evidence identity drifted from preregistration"
                )
            if registration.execution_budget.budget_digest != spec.execution_budget_digest:
                reasons.add("EXECUTION_BUDGET_DRIFT")
            if not row.owner_randomized_run:
                reasons.add("ARM_ORDER_NOT_LEDGER_RANDOMIZED")
            if not row.independent_fresh_worktrees:
                reasons.add("ARMS_NOT_INDEPENDENT_FRESH_WORKTREES")

            baseline = row.baseline
            selected = row.selected
            if not baseline.benefit_eligible or not selected.benefit_eligible:
                reasons.add("MODEL_EVIDENCE_NOT_ELIGIBLE")
            if (
                baseline.execution_profile_digest != spec.execution_profile_digest
                or selected.execution_profile_digest != spec.execution_profile_digest
            ):
                reasons.add("EXECUTION_PROFILE_DRIFT")
            if (
                baseline.hidden_verifier_profile_digest
                != spec.hidden_verifier_profile_digest
                or selected.hidden_verifier_profile_digest
                != spec.hidden_verifier_profile_digest
            ):
                reasons.add("HIDDEN_VERIFIER_PROFILE_DRIFT")
            if (
                baseline.static_verifier_profile_digest
                != spec.static_verifier_profile_digest
                or selected.static_verifier_profile_digest
                != spec.static_verifier_profile_digest
            ):
                reasons.add("STATIC_VERIFIER_PROFILE_DRIFT")
            if baseline.attestation is not None and selected.attestation is not None:
                if (
                    baseline.attestation.provider != spec.execution_provider
                    or selected.attestation.provider != spec.execution_provider
                    or baseline.attestation.model != spec.execution_model
                    or selected.attestation.model != spec.execution_model
                ):
                    reasons.add("EXECUTION_MODEL_PROFILE_DRIFT")
                if (
                    baseline.attestation.provider != selected.attestation.provider
                    or baseline.attestation.model != selected.attestation.model
                ):
                    reasons.add("PAIRED_EXECUTION_MODEL_MISMATCH")

            baseline_outcome = receipt.run.report.baseline_outcome
            selected_outcome = receipt.run.report.selected_outcome
            if baseline_outcome is None or selected_outcome is None:
                raise CodeBenefitCohortError(
                    "completed Code receipt lacks paired outcomes"
                )
            baseline_passes += int(baseline_outcome.success is True)
            selected_passes += int(selected_outcome.success is True)
            baseline_hidden += int(baseline.hidden_test_passed)
            selected_hidden += int(selected.hidden_test_passed)
            baseline_static += int(baseline.static_check_passed)
            selected_static += int(selected.static_check_passed)
            baseline_risk += baseline.diff_risk_score
            selected_risk += selected.diff_risk_score
            baseline_cost += baseline.total_cost_micros
            selected_cost += selected.total_cost_micros
            evidence_payloads.append(
                {
                    "workload": workload.to_dict(),
                    "terminal_receipt_digest": row.terminal_receipt_digest,
                    "baseline_evidence_digest": baseline.receipt_digest,
                    "selected_evidence_digest": selected.receipt_digest,
                    "owner_randomized_run": row.owner_randomized_run,
                    "independent_fresh_worktrees": row.independent_fresh_worktrees,
                }
            )

        size = float(CODE_BENEFIT_COHORT_SIZE)
        baseline_rate = baseline_passes / size
        selected_rate = selected_passes / size
        pass_gain = selected_rate - baseline_rate
        baseline_hidden_rate = baseline_hidden / size
        selected_hidden_rate = selected_hidden / size
        baseline_static_rate = baseline_static / size
        selected_static_rate = selected_static / size
        baseline_mean_risk = baseline_risk / size
        selected_mean_risk = selected_risk / size
        cost_reduction = (
            None
            if baseline_cost == 0
            else (baseline_cost - selected_cost) / baseline_cost
        )

        pass_gain_gate = (
            pass_gain + 1e-12 >= CODE_BENEFIT_MIN_PASS_RATE_GAIN
        )
        efficiency_gate = (
            selected_rate + 1e-12
            >= baseline_rate - CODE_BENEFIT_NON_INFERIOR_MARGIN
            and cost_reduction is not None
            and cost_reduction + 1e-12 >= CODE_BENEFIT_MIN_COST_REDUCTION
        )
        if not pass_gain_gate and not efficiency_gate:
            reasons.add("PASS_RATE_OR_COST_BENEFIT_NOT_MET")
        if (
            not pass_gain_gate
            and selected_rate + 1e-12
            >= baseline_rate - CODE_BENEFIT_NON_INFERIOR_MARGIN
            and cost_reduction is None
        ):
            reasons.add("COST_REDUCTION_BASELINE_ZERO")
        if selected_hidden_rate < baseline_hidden_rate:
            reasons.add("HIDDEN_TEST_REGRESSION")
        if selected_static_rate < baseline_static_rate:
            reasons.add("STATIC_CHECK_REGRESSION")
        if selected_mean_risk > baseline_mean_risk:
            reasons.add("DIFF_RISK_REGRESSION")

        ordered_reasons = tuple(sorted(reasons))
        eligible = not ordered_reasons
        return CodeBenefitCohortReportV1(
            cohort_id=spec.cohort_id,
            cohort_version=spec.cohort_version,
            spec_digest=spec.spec_digest,
            evaluated_workload_count=len(rows),
            baseline_pass_rate=_rounded(baseline_rate),
            selected_pass_rate=_rounded(selected_rate),
            pass_rate_gain=_rounded(pass_gain),
            baseline_hidden_test_rate=_rounded(baseline_hidden_rate),
            selected_hidden_test_rate=_rounded(selected_hidden_rate),
            baseline_static_check_rate=_rounded(baseline_static_rate),
            selected_static_check_rate=_rounded(selected_static_rate),
            baseline_mean_diff_risk=_rounded(baseline_mean_risk),
            selected_mean_diff_risk=_rounded(selected_mean_risk),
            baseline_total_cost_micros=baseline_cost,
            selected_total_cost_micros=selected_cost,
            cost_reduction_rate=(
                None if cost_reduction is None else _rounded(cost_reduction)
            ),
            reason_codes=ordered_reasons,
            evidence_digest=_json_digest(evidence_payloads),
            benefit_eligible=eligible,
            proposal_eligible=eligible,
        )


def _rounded(value: float) -> float:
    return round(value, 8)


__all__ = ["CodeBenefitCohortEvaluator"]
