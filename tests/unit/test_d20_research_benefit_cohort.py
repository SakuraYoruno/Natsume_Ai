from __future__ import annotations

from dataclasses import replace
import hashlib

import pytest

from host_brain.contracts.planning_search.research_benchmark_contracts import (
    ExternalProviderRunAttestationV1,
    PairedResearchRunReceiptV1,
    ResearchModelExecutionReceiptV1,
    ResearchVerifierReceiptV1,
)
from host_brain.contracts.planning_search.research_benefit_cohort import (
    ResearchBenefitCohortError,
    ResearchBenefitCohortEvaluator,
    ResearchBenefitCohortSpecV1,
    ResearchCohortPairedEvidenceV1,
    ResearchCohortWorkloadV1,
    ResearchQualityVerifierReceiptV1,
)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _attestation(
    execution_id: str,
    *,
    response: str,
    provider: str = "openai",
    model: str = "gpt-real",
    cost: int = 100,
    latency: int = 100,
    with_transport: bool = True,
) -> ExternalProviderRunAttestationV1:
    return ExternalProviderRunAttestationV1(
        execution_id=execution_id,
        provider=provider,
        model=model,
        route_digest=_digest(f"route:{execution_id}"),
        response_digest=_digest(response),
        total_tokens=80,
        cost_micros=cost,
        provider_latency_ms=latency - 5,
        total_latency_ms=latency,
        transport_identity_digest=(
            _digest(f"transport:{provider}") if with_transport else None
        ),
        transport_external=True if with_transport else None,
    )


def _model_receipt(
    execution_id: str,
    arm_id: str,
    *,
    response: str,
    cost: int,
    latency: int,
) -> ResearchModelExecutionReceiptV1:
    attestation = _attestation(
        execution_id,
        response=response,
        cost=cost,
        latency=latency,
    )
    return ResearchModelExecutionReceiptV1(
        execution_id=execution_id,
        arm_id=arm_id,
        evidence_class="ATTESTED_EXTERNAL",
        attempt_ids=(f"attempt-{execution_id}",),
        prompt_digest=_digest(f"prompt:{execution_id}"),
        tool_call_count=1,
        total_tokens=attestation.total_tokens,
        cost_micros=attestation.cost_micros,
        attestation_ref=attestation.attestation_ref,
        attestation=attestation,
    )


def _citation_receipt(execution_id: str, validity: float) -> ResearchVerifierReceiptV1:
    return ResearchVerifierReceiptV1(
        execution_id=execution_id,
        citation_count=2,
        cited_span_count=2,
        citation_coverage=1.0,
        citation_validity=validity,
        evidence_digest=_digest(f"citation:{execution_id}:{validity}"),
    )


def _quality_receipt(
    *,
    run_id: str,
    role: str,
    arm_execution_id: str,
    response_digest: str,
    score: float,
    verifier_profile_digest: str,
    rubric_digest: str,
    cost: int = 20,
    latency: int = 20,
    evidence_class: str = "ATTESTED_EXTERNAL",
    severe_policy_errors: int = 0,
    severe_conflict_errors: int = 0,
) -> ResearchQualityVerifierReceiptV1:
    verifier_execution_id = f"verify-{arm_execution_id}"
    attestation = (
        _attestation(
            verifier_execution_id,
            response=f"verifier-result:{arm_execution_id}",
            provider="anthropic",
            model="quality-real",
            cost=cost,
            latency=latency,
        )
        if evidence_class == "ATTESTED_EXTERNAL"
        else None
    )
    verifier_result_digest = (
        attestation.response_digest
        if attestation is not None
        else _digest(f"unattested:{arm_execution_id}")
    )
    return ResearchQualityVerifierReceiptV1(
        run_id=run_id,
        arm_role=role,
        arm_execution_id=arm_execution_id,
        evaluated_response_digest=response_digest,
        verifier_execution_id=verifier_execution_id,
        verifier_attempt_ids=(f"attempt-{verifier_execution_id}",),
        verifier_profile_digest=verifier_profile_digest,
        rubric_digest=rubric_digest,
        verifier_result_digest=verifier_result_digest,
        quality_score=score,
        severe_policy_errors=severe_policy_errors,
        severe_conflict_errors=severe_conflict_errors,
        evidence_class=evidence_class,
        hidden_verifier=True,
        verifier_prompt_digest=_digest(f"verifier-prompt:{arm_execution_id}"),
        attestation_ref=(
            None if attestation is None else attestation.attestation_ref
        ),
        attestation=attestation,
    )


def _cohort(
    *,
    uplift: float = 0.10,
    selected_citation_validity: float = 0.98,
    selected_model_cost: int = 120,
    selected_model_latency: int = 120,
) -> tuple[ResearchBenefitCohortSpecV1, tuple[ResearchCohortPairedEvidenceV1, ...]]:
    verifier_profile_digest = _digest("quality-profile")
    rubric_digest = _digest("hidden-rubric")
    workloads = []
    evidence = []
    topics = ("architecture", "security", "data")
    for index in range(24):
        workload_id = f"research-{index:02d}"
        run_id = f"run-{index:02d}"
        baseline_execution_id = f"{run_id}-baseline"
        selected_execution_id = f"{run_id}-selected"
        baseline_model = _model_receipt(
            baseline_execution_id,
            f"baseline-arm-{index:02d}",
            response=f"baseline-response-{index:02d}",
            cost=100,
            latency=100,
        )
        selected_model = _model_receipt(
            selected_execution_id,
            f"selected-arm-{index:02d}",
            response=f"selected-response-{index:02d}",
            cost=selected_model_cost,
            latency=selected_model_latency,
        )
        terminal = PairedResearchRunReceiptV1(
            corpus_binding_digest=_digest(f"corpus:{index}"),
            profile_digest=_digest("execution-profile"),
            budget_digest=_digest("execution-budget"),
            baseline_execution_id=baseline_execution_id,
            selected_execution_id=selected_execution_id,
            baseline_model=baseline_model,
            selected_model=selected_model,
            baseline_verifier=_citation_receipt(baseline_execution_id, 0.98),
            selected_verifier=_citation_receipt(
                selected_execution_id,
                selected_citation_validity,
            ),
            arm_order=(
                ("BASELINE", "SELECTED")
                if index % 2 == 0
                else ("SELECTED", "BASELINE")
            ),
            randomized_arm_order=True,
        )
        terminal_digest = _digest_json(terminal.to_dict())
        workloads.append(
            ResearchCohortWorkloadV1(
                workload_id=workload_id,
                run_id=run_id,
                terminal_receipt_digest=terminal_digest,
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
        baseline_quality = _quality_receipt(
            run_id=run_id,
            role="BASELINE",
            arm_execution_id=baseline_execution_id,
            response_digest=baseline_model.attestation.response_digest,
            score=0.70,
            verifier_profile_digest=verifier_profile_digest,
            rubric_digest=rubric_digest,
        )
        selected_quality = _quality_receipt(
            run_id=run_id,
            role="SELECTED",
            arm_execution_id=selected_execution_id,
            response_digest=selected_model.attestation.response_digest,
            score=0.70 + uplift,
            verifier_profile_digest=verifier_profile_digest,
            rubric_digest=rubric_digest,
        )
        evidence.append(
            ResearchCohortPairedEvidenceV1(
                workload_id=workload_id,
                run_id=run_id,
                terminal_receipt_digest=terminal_digest,
                terminal_receipt=terminal,
                baseline_quality=baseline_quality,
                selected_quality=selected_quality,
                owner_randomized_run=True,
            )
        )
    spec = ResearchBenefitCohortSpecV1(
        cohort_id="research-benefit-2026-01",
        cohort_version="v1",
        planning_algorithm_version="planning-search-v1",
        execution_provider="openai",
        execution_model="gpt-real",
        verifier_provider="anthropic",
        verifier_model="quality-real",
        verifier_profile_digest=verifier_profile_digest,
        rubric_digest=rubric_digest,
        randomization_seed_digest=_digest("cohort-seed"),
        workloads=tuple(workloads),
    )
    return spec, tuple(evidence)


def _digest_json(value: object) -> str:
    import json

    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def test_fixed_24_pair_cohort_produces_deterministic_proposal_only_report() -> None:
    spec, evidence = _cohort()

    first = ResearchBenefitCohortEvaluator().evaluate(spec=spec, evidence=evidence)
    second = ResearchBenefitCohortEvaluator().evaluate(
        spec=spec,
        evidence=tuple(reversed(evidence)),
    )

    assert first.to_dict() == second.to_dict()
    assert first.evaluated_workload_count == 24
    assert first.mean_quality_uplift == pytest.approx(0.1)
    assert first.bootstrap_95_lower_bound > 0
    assert first.selected_mean_citation_validity == pytest.approx(0.98)
    assert first.total_cost_ratio < 1.5
    assert first.p95_latency_ratio < 1.75
    assert first.reason_codes == ()
    assert first.benefit_eligible is True
    assert first.proposal_eligible is True
    assert first.production_influence_enabled is False
    assert first.to_dict()["production_influence_enabled"] is False


@pytest.mark.parametrize(
    ("mutation", "reason"),
    (
        (
            lambda rows: tuple(
                replace(
                    row,
                    selected_quality=replace(
                        row.selected_quality,
                        quality_score=0.70,
                    ),
                )
                for row in rows
            ),
            "QUALITY_UPLIFT_BELOW_THRESHOLD",
        ),
        (
            lambda rows: tuple(
                replace(
                    row,
                    selected_quality=replace(
                        row.selected_quality,
                        severe_policy_errors=1 if index == 0 else 0,
                    ),
                )
                for index, row in enumerate(rows)
            ),
            "SEVERE_POLICY_ERROR",
        ),
        (
            lambda rows: tuple(
                replace(row, owner_randomized_run=False)
                if index == 0
                else row
                for index, row in enumerate(rows)
            ),
            "ARM_ORDER_NOT_LEDGER_RANDOMIZED",
        ),
    ),
)
def test_failed_thresholds_are_retained_but_never_eligible(
    mutation,
    reason: str,
) -> None:
    spec, evidence = _cohort()

    report = ResearchBenefitCohortEvaluator().evaluate(
        spec=spec,
        evidence=mutation(evidence),
    )

    assert reason in report.reason_codes
    assert report.benefit_eligible is False
    assert report.proposal_eligible is False


def test_unattested_hidden_verifier_is_not_benefit_evidence() -> None:
    spec, evidence = _cohort()
    first = evidence[0]
    untrusted = _quality_receipt(
        run_id=first.run_id,
        role="SELECTED",
        arm_execution_id=first.terminal_receipt.selected_execution_id,
        response_digest=first.terminal_receipt.selected_model.attestation.response_digest,
        score=0.8,
        verifier_profile_digest=spec.verifier_profile_digest,
        rubric_digest=spec.rubric_digest,
        evidence_class="CANONICAL_ATTEMPT_UNATTESTED",
    )
    mutated = (replace(first, selected_quality=untrusted), *evidence[1:])

    report = ResearchBenefitCohortEvaluator().evaluate(
        spec=spec,
        evidence=mutated,
    )

    assert "HIDDEN_VERIFIER_EVIDENCE_NOT_ELIGIBLE" in report.reason_codes
    assert report.benefit_eligible is False


def test_cost_and_latency_thresholds_include_hidden_verifier_request() -> None:
    spec, evidence = _cohort(
        selected_model_cost=200,
        selected_model_latency=210,
    )

    report = ResearchBenefitCohortEvaluator().evaluate(
        spec=spec,
        evidence=evidence,
    )

    assert "COST_RATIO_EXCEEDED" in report.reason_codes
    assert "P95_LATENCY_RATIO_EXCEEDED" in report.reason_codes


def test_cohort_spec_rejects_incomplete_or_unrepresentative_workloads() -> None:
    spec, _ = _cohort()

    with pytest.raises(ResearchBenefitCohortError, match="exactly 24"):
        replace(spec, workloads=spec.workloads[:-1])
    with pytest.raises(ResearchBenefitCohortError, match="three topic"):
        replace(
            spec,
            workloads=tuple(
                replace(item, topic_category="one-topic")
                for item in spec.workloads
            ),
        )
    with pytest.raises(ResearchBenefitCohortError, match="local-file and Web"):
        replace(
            spec,
            workloads=tuple(
                replace(item, source_kinds=("LOCAL_FILE",))
                for item in spec.workloads
            ),
        )


def test_quality_receipt_rejects_verifier_identity_or_response_drift() -> None:
    spec, evidence = _cohort()
    quality = evidence[0].selected_quality

    with pytest.raises(ResearchBenefitCohortError, match="independent"):
        replace(quality, verifier_execution_id=quality.arm_execution_id)
    with pytest.raises(ResearchBenefitCohortError, match="does not bind"):
        replace(quality, verifier_result_digest=_digest("tampered"))


def test_cohort_evidence_rejects_terminal_or_arm_binding_drift() -> None:
    _, evidence = _cohort()
    first = evidence[0]

    with pytest.raises(ResearchBenefitCohortError, match="digest drift"):
        replace(first, terminal_receipt_digest=_digest("wrong-terminal"))
    with pytest.raises(ResearchBenefitCohortError, match="does not bind"):
        replace(
            first,
            selected_quality=replace(
                first.selected_quality,
                evaluated_response_digest=_digest("other-response"),
            ),
        )
