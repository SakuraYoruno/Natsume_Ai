from __future__ import annotations

import pytest

from host_brain.contracts.evolution_lab import (
    CounterfactualPairV1,
    EvolutionLabContractError,
    EvolutionProposalV1,
    ExperimentReportV1,
    ExperimentRunReceiptV1,
    ExperimentSpecV1,
)


def _spec() -> ExperimentSpecV1:
    return ExperimentSpecV1(
        experiment_id="research-planning-golden",
        dataset_digest="a" * 64,
        seed=7,
        budget={"max_runs": 4, "max_wall_time_ms": 1000},
        variants=("baseline", "candidate"),
        verifier_ref="artifact://verifier/evolution-lab-v1",
        policy_version="policy-v1",
    )


def _run(spec: ExperimentSpecV1, run_id: str, variant: str) -> ExperimentRunReceiptV1:
    return ExperimentRunReceiptV1(
        run_id=run_id,
        spec_digest=spec.spec_digest,
        variant=variant,
        status="PASS",
        evidence_digest="b" * 64,
        outcome={"quality": 0.8},
        cost={"tokens": 10},
        risk={"policy_errors": 0},
    )


def test_ne04_report_is_replay_bound_and_proposal_only() -> None:
    spec = _spec()
    baseline = _run(spec, "run-baseline", "baseline")
    candidate = _run(spec, "run-candidate", "candidate")
    pair = CounterfactualPairV1(
        baseline_run_id=baseline.run_id,
        alternative_run_id=candidate.run_id,
        outcome_delta={"quality": 0.1},
        evidence_delta={"citation_validity": 0.05},
        cost_delta={"tokens": 2.0},
        risk_delta={"policy_errors": 0.0},
    )
    report = ExperimentReportV1(
        report_id="report-1",
        spec=spec,
        runs=(baseline, candidate),
        comparisons=(pair,),
    )
    proposal = EvolutionProposalV1(
        proposal_id="proposal-1",
        report_digest=report.report_digest,
        target_kind="planning-policy",
        target_version="candidate-v1",
        rationale="Candidate improves verified quality within budget.",
    )

    assert len(report.report_digest) == 64
    assert proposal.production_influence_enabled is False
    assert proposal.review_required is True


def test_ne04_rejects_side_effects_and_unreviewed_promotion() -> None:
    spec = _spec()
    with pytest.raises(EvolutionLabContractError):
        _ = ExperimentRunReceiptV1(
            run_id="run",
            spec_digest=spec.spec_digest,
            variant="candidate",
            status="PASS",
            evidence_digest="b" * 64,
            outcome={},
            cost={},
            risk={},
            production_side_effects=True,
        )
    with pytest.raises(EvolutionLabContractError):
        EvolutionProposalV1(
            proposal_id="proposal",
            report_digest="c" * 64,
            target_kind="skill",
            target_version="v1",
            rationale="bad",
            review_required=False,
        )
