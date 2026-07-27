"""Pure deterministic evaluator for the fixed Research benefit cohort."""

from __future__ import annotations

import math
import random
from typing import Any, Mapping, Sequence

from .research_benefit_cohort_models import (
    RESEARCH_BENEFIT_BOOTSTRAP_ITERATIONS,
    RESEARCH_BENEFIT_COHORT_SIZE,
    RESEARCH_BENEFIT_MAX_CITATION_DECLINE,
    RESEARCH_BENEFIT_MAX_COST_RATIO,
    RESEARCH_BENEFIT_MAX_P95_LATENCY_RATIO,
    RESEARCH_BENEFIT_MIN_CITATION_VALIDITY,
    RESEARCH_BENEFIT_MIN_QUALITY_UPLIFT,
    ResearchBenefitCohortError,
    ResearchBenefitCohortReportV1,
    ResearchBenefitCohortSpecV1,
    ResearchCohortPairedEvidenceV1,
    ResearchQualityVerifierReceiptV1,
    _digest,
    _json_digest,
)
from .research_benchmark_contracts import PairedResearchRunReceiptV1


class ResearchBenefitCohortEvaluator:
    """Evaluate the frozen 24-pair protocol without mutating any authority."""

    def evaluate(
        self,
        *,
        spec: ResearchBenefitCohortSpecV1,
        evidence: Sequence[ResearchCohortPairedEvidenceV1],
    ) -> ResearchBenefitCohortReportV1:
        if not isinstance(spec, ResearchBenefitCohortSpecV1):
            raise ResearchBenefitCohortError(
                "spec must be ResearchBenefitCohortSpecV1"
            )
        if isinstance(evidence, (str, bytes, bytearray)):
            raise ResearchBenefitCohortError("evidence must be a sequence")
        rows = tuple(evidence)
        if len(rows) != RESEARCH_BENEFIT_COHORT_SIZE or any(
            not isinstance(item, ResearchCohortPairedEvidenceV1) for item in rows
        ):
            raise ResearchBenefitCohortError(
                f"Research cohort evaluation requires exactly {RESEARCH_BENEFIT_COHORT_SIZE} pairs"
            )
        by_workload = {row.workload_id: row for row in rows}
        if len(by_workload) != len(rows):
            raise ResearchBenefitCohortError(
                "cohort evidence workload ids are not unique"
            )
        expected = {item.workload_id: item for item in spec.workloads}
        if set(by_workload) != set(expected):
            raise ResearchBenefitCohortError(
                "cohort evidence does not match the preregistered workload set"
            )

        quality_deltas: list[float] = []
        baseline_citations: list[float] = []
        selected_citations: list[float] = []
        baseline_cost = 0
        selected_cost = 0
        baseline_latencies: list[int] = []
        selected_latencies: list[int] = []
        selected_policy_errors = 0
        selected_conflict_errors = 0
        reasons: set[str] = set()
        evidence_payloads: list[Mapping[str, Any]] = []

        for workload in spec.workloads:
            row = by_workload[workload.workload_id]
            if (
                row.run_id != workload.run_id
                or row.terminal_receipt_digest != workload.terminal_receipt_digest
            ):
                raise ResearchBenefitCohortError(
                    "cohort evidence identity drifted from the preregistered workload"
                )
            receipt = row.terminal_receipt
            if not row.owner_randomized_run:
                reasons.add("ARM_ORDER_NOT_LEDGER_RANDOMIZED")
            if not receipt.benefit_eligible:
                reasons.add("PAIRED_MODEL_EVIDENCE_NOT_ELIGIBLE")
            if (
                not row.baseline_quality.benefit_eligible
                or not row.selected_quality.benefit_eligible
            ):
                reasons.add("HIDDEN_VERIFIER_EVIDENCE_NOT_ELIGIBLE")
            if (
                row.baseline_quality.verifier_profile_digest
                != spec.verifier_profile_digest
                or row.selected_quality.verifier_profile_digest
                != spec.verifier_profile_digest
                or row.baseline_quality.rubric_digest != spec.rubric_digest
                or row.selected_quality.rubric_digest != spec.rubric_digest
            ):
                reasons.add("HIDDEN_VERIFIER_PROFILE_DRIFT")

            baseline_model_attestation = receipt.baseline_model.attestation
            selected_model_attestation = receipt.selected_model.attestation
            if baseline_model_attestation is None or selected_model_attestation is None:
                reasons.add("MODEL_ATTESTATION_MISSING")
            else:
                if (
                    baseline_model_attestation.provider
                    != spec.execution_provider.lower()
                    or selected_model_attestation.provider
                    != spec.execution_provider.lower()
                    or baseline_model_attestation.model != spec.execution_model
                    or selected_model_attestation.model != spec.execution_model
                ):
                    reasons.add("EXECUTION_MODEL_PROFILE_DRIFT")
                if (
                    baseline_model_attestation.provider
                    != selected_model_attestation.provider
                    or baseline_model_attestation.model
                    != selected_model_attestation.model
                ):
                    reasons.add("PAIRED_EXECUTION_MODEL_MISMATCH")

            for quality in (row.baseline_quality, row.selected_quality):
                verifier_attestation = quality.attestation
                if verifier_attestation is None:
                    continue
                if (
                    verifier_attestation.provider != spec.verifier_provider.lower()
                    or verifier_attestation.model != spec.verifier_model
                ):
                    reasons.add("VERIFIER_MODEL_PROFILE_DRIFT")

            quality_deltas.append(
                row.selected_quality.quality_score
                - row.baseline_quality.quality_score
            )
            baseline_citations.append(receipt.baseline_verifier.citation_validity)
            selected_citations.append(receipt.selected_verifier.citation_validity)
            baseline_cost += _total_arm_cost(
                receipt=receipt,
                quality=row.baseline_quality,
                role="BASELINE",
            )
            selected_cost += _total_arm_cost(
                receipt=receipt,
                quality=row.selected_quality,
                role="SELECTED",
            )
            baseline_latencies.append(
                _total_arm_latency(
                    receipt=receipt,
                    quality=row.baseline_quality,
                    role="BASELINE",
                )
            )
            selected_latencies.append(
                _total_arm_latency(
                    receipt=receipt,
                    quality=row.selected_quality,
                    role="SELECTED",
                )
            )
            selected_policy_errors += row.selected_quality.severe_policy_errors
            selected_conflict_errors += row.selected_quality.severe_conflict_errors
            evidence_payloads.append(
                {
                    "workload": workload.to_dict(),
                    "terminal_receipt_digest": row.terminal_receipt_digest,
                    "baseline_quality_digest": row.baseline_quality.receipt_digest,
                    "selected_quality_digest": row.selected_quality.receipt_digest,
                    "owner_randomized_run": row.owner_randomized_run,
                }
            )

        mean_uplift = sum(quality_deltas) / len(quality_deltas)
        bootstrap_lower = _paired_bootstrap_lower_bound(
            quality_deltas,
            seed_digest=_json_digest(
                {
                    "spec_digest": spec.spec_digest,
                    "randomization_seed_digest": spec.randomization_seed_digest,
                    "algorithm": "PAIRED_BOOTSTRAP_MEAN_V1",
                }
            ),
        )
        baseline_citation = sum(baseline_citations) / len(baseline_citations)
        selected_citation = sum(selected_citations) / len(selected_citations)
        citation_delta = selected_citation - baseline_citation
        cost_ratio = _ratio(selected_cost, baseline_cost)
        latency_ratio = _ratio(
            _nearest_rank_p95(selected_latencies),
            _nearest_rank_p95(baseline_latencies),
        )

        if mean_uplift < RESEARCH_BENEFIT_MIN_QUALITY_UPLIFT:
            reasons.add("QUALITY_UPLIFT_BELOW_THRESHOLD")
        if bootstrap_lower <= 0.0:
            reasons.add("BOOTSTRAP_LOWER_BOUND_NOT_POSITIVE")
        if selected_citation < RESEARCH_BENEFIT_MIN_CITATION_VALIDITY:
            reasons.add("CITATION_VALIDITY_BELOW_THRESHOLD")
        if citation_delta < -RESEARCH_BENEFIT_MAX_CITATION_DECLINE:
            reasons.add("CITATION_VALIDITY_REGRESSION")
        if selected_policy_errors:
            reasons.add("SEVERE_POLICY_ERROR")
        if selected_conflict_errors:
            reasons.add("SEVERE_CONFLICT_ERROR")
        if cost_ratio > RESEARCH_BENEFIT_MAX_COST_RATIO:
            reasons.add("COST_RATIO_EXCEEDED")
        if latency_ratio > RESEARCH_BENEFIT_MAX_P95_LATENCY_RATIO:
            reasons.add("P95_LATENCY_RATIO_EXCEEDED")

        ordered_reasons = tuple(sorted(reasons))
        eligible = not ordered_reasons
        return ResearchBenefitCohortReportV1(
            cohort_id=spec.cohort_id,
            cohort_version=spec.cohort_version,
            spec_digest=spec.spec_digest,
            evaluated_workload_count=len(rows),
            mean_quality_uplift=_rounded(mean_uplift),
            bootstrap_95_lower_bound=_rounded(bootstrap_lower),
            baseline_mean_citation_validity=_rounded(baseline_citation),
            selected_mean_citation_validity=_rounded(selected_citation),
            citation_validity_delta=_rounded(citation_delta),
            total_cost_ratio=_rounded(cost_ratio),
            p95_latency_ratio=_rounded(latency_ratio),
            selected_severe_policy_errors=selected_policy_errors,
            selected_severe_conflict_errors=selected_conflict_errors,
            reason_codes=ordered_reasons,
            evidence_digest=_json_digest(evidence_payloads),
            benefit_eligible=eligible,
            proposal_eligible=eligible,
        )


def _total_arm_cost(
    *,
    receipt: PairedResearchRunReceiptV1,
    quality: ResearchQualityVerifierReceiptV1,
    role: str,
) -> int:
    model = receipt.baseline_model if role == "BASELINE" else receipt.selected_model
    model_cost = 0 if model.cost_micros is None else model.cost_micros
    verifier_cost = 0 if quality.attestation is None else quality.attestation.cost_micros
    return model_cost + verifier_cost


def _total_arm_latency(
    *,
    receipt: PairedResearchRunReceiptV1,
    quality: ResearchQualityVerifierReceiptV1,
    role: str,
) -> int:
    model = receipt.baseline_model if role == "BASELINE" else receipt.selected_model
    model_latency = (
        0 if model.attestation is None else model.attestation.total_latency_ms
    )
    verifier_latency = (
        0 if quality.attestation is None else quality.attestation.total_latency_ms
    )
    return model_latency + verifier_latency


def _ratio(numerator: int, denominator: int) -> float:
    if denominator == 0:
        return 1.0 if numerator == 0 else math.inf
    return numerator / denominator


def _rounded(value: float) -> float:
    return round(value, 8)


def _nearest_rank_p95(values: Sequence[int]) -> int:
    if not values:
        raise ResearchBenefitCohortError("p95 requires at least one value")
    ordered = sorted(values)
    rank = max(1, math.ceil(0.95 * len(ordered)))
    return ordered[rank - 1]


def _paired_bootstrap_lower_bound(
    deltas: Sequence[float],
    *,
    seed_digest: str,
) -> float:
    if len(deltas) != RESEARCH_BENEFIT_COHORT_SIZE:
        raise ResearchBenefitCohortError(
            "paired bootstrap requires the fixed 24-workload cohort"
        )
    rng = random.Random(int(_digest(seed_digest, "seed_digest"), 16))
    sample_size = len(deltas)
    means = []
    for _ in range(RESEARCH_BENEFIT_BOOTSTRAP_ITERATIONS):
        total = 0.0
        for _ in range(sample_size):
            total += deltas[rng.randrange(sample_size)]
        means.append(total / sample_size)
    means.sort()
    index = max(
        0,
        math.ceil(0.025 * RESEARCH_BENEFIT_BOOTSTRAP_ITERATIONS) - 1,
    )
    return means[index]


__all__ = ["ResearchBenefitCohortEvaluator"]
