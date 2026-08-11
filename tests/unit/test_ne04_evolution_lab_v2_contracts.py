from __future__ import annotations

import pytest

from host_brain.evolution_lab.contracts import (
    CounterfactualPairV2,
    DatasetManifestV1,
    EffectAuditReceiptV1,
    EvolutionLabContractError,
    EvolutionProposalV2,
    ExperimentReportV2,
    ExperimentRunReceiptV2,
    ExperimentSpecV2,
    LabDatasetV1,
    PolicyVariantV1,
    SandboxReceiptV1,
    VerificationReceiptV1,
    canonical_evolution_lab_digest,
)


def _dataset() -> LabDatasetV1:
    return LabDatasetV1(
        dataset_id="synthetic-research-v2",
        records=({"prompt": "compare sources", "expected_kind": "citation"},),
    )


def _spec() -> ExperimentSpecV2:
    dataset = _dataset()
    manifest = DatasetManifestV1(
        workspace_id="workspace-lab",
        dataset_id=dataset.dataset_id,
        dataset_digest=dataset.dataset_digest,
        schema_digest=canonical_evolution_lab_digest({"fields": ["prompt", "expected_kind"]}),
        record_count=len(dataset.records),
        source_kind="synthetic",
    )
    return ExperimentSpecV2(
        experiment_id="research-planning-v2",
        workspace_id="workspace-lab",
        dataset_manifest=manifest,
        seed=7,
        budget={"max_runs": 2, "max_wall_time_ms": 1000},
        variants=(
            PolicyVariantV1(
                variant_id="baseline",
                policy_version="policy.v1",
                code_version="code.baseline",
                config_digest="1" * 64,
                is_baseline=True,
            ),
            PolicyVariantV1(
                variant_id="candidate",
                policy_version="policy.v2",
                code_version="code.candidate",
                config_digest="2" * 64,
                is_baseline=False,
            ),
        ),
        verifier_identity="verifier://evolution-lab-v2",
        verifier_version="v2.0.0",
        verifier_digest="3" * 64,
        isolation_profile_digest="4" * 64,
    )


def _run(spec: ExperimentSpecV2, variant_id: str) -> ExperimentRunReceiptV2:
    execution_id = f"execution-{variant_id}"
    output = {"answer": variant_id, "quality": 0.8}
    effect_audit = EffectAuditReceiptV1(
        execution_id=execution_id,
        audit_id=f"audit-{variant_id}",
        auditor_identity="sandbox-audit://v2",
        audit_evidence_digest="5" * 64,
        effect_count=0,
    )
    sandbox = SandboxReceiptV1(
        execution_id=execution_id,
        sandbox_id=f"sandbox-{variant_id}",
        sandbox_profile_digest=spec.isolation_profile_digest,
        dataset_digest=spec.dataset_manifest.dataset_digest,
        output_digest=canonical_evolution_lab_digest({"output": output}),
        network_disabled=True,
        readonly_dataset=True,
        temporary_output=True,
        effect_audit=effect_audit,
    )
    verification = VerificationReceiptV1(
        execution_id=execution_id,
        verifier_identity=spec.verifier_identity,
        verifier_version=spec.verifier_version,
        verifier_digest=spec.verifier_digest,
        status="PASS",
        evidence_digest=("6" if variant_id == "baseline" else "7") * 64,
        outcome={"quality": 0.7 if variant_id == "baseline" else 0.8},
        risk={"policy_errors": 0},
        metrics={"citation_validity": 1.0},
    )
    return ExperimentRunReceiptV2(
        run_id=f"run-{variant_id}",
        execution_id=execution_id,
        spec_digest=spec.spec_digest,
        workspace_id=spec.workspace_id,
        dataset_manifest_digest=spec.dataset_manifest.manifest_digest,
        variant_id=variant_id,
        status="PASS",
        model_identity="structural-test-model",
        usage={"tokens": 10},
        cost={"microusd": 20},
        latency_ms=12,
        sandbox_receipt=sandbox,
        verification_receipt=verification,
    )


def test_ne04_v2_binds_dataset_sandbox_verifier_and_keeps_proposal_only() -> None:
    spec = _spec()
    baseline = _run(spec, "baseline")
    candidate = _run(spec, "candidate")
    pair = CounterfactualPairV2(
        baseline_run_id=baseline.run_id,
        alternative_run_id=candidate.run_id,
        outcome_delta={"quality": 0.1},
        evidence_delta={"citation_validity": 0.0},
        cost_delta={"microusd": 0.0},
        latency_delta={"p95_ms": 0.0},
        risk_delta={"policy_errors": 0.0},
        verification_delta={"pass_rate": 0.0},
    )
    report = ExperimentReportV2(
        report_id="report-v2",
        spec=spec,
        runs=(baseline, candidate),
        comparisons=(pair,),
    )
    proposal = EvolutionProposalV2(
        proposal_id="proposal-v2",
        report_digest=report.report_digest,
        target_kind="planning-policy",
        target_version="candidate.v2",
        rationale="only a Review may evaluate the structural Lab evidence",
    )

    assert baseline.production_side_effects is False
    assert baseline.benefit_eligible is False
    assert "output" not in baseline.to_dict()
    assert report.promotion_eligible is False
    assert proposal.production_influence_enabled is False


def test_ne04_v2_rejects_missing_verifier_evidence_and_weak_sandbox() -> None:
    spec = _spec()
    with pytest.raises(EvolutionLabContractError, match="evidence_digest"):
        VerificationReceiptV1(
            execution_id="execution",
            verifier_identity=spec.verifier_identity,
            verifier_version=spec.verifier_version,
            verifier_digest=spec.verifier_digest,
            status="PASS",
            evidence_digest="",
            outcome={},
            risk={},
            metrics={},
        )

    audit = EffectAuditReceiptV1(
        execution_id="execution",
        audit_id="audit",
        auditor_identity="sandbox-audit://v2",
        audit_evidence_digest="8" * 64,
        effect_count=0,
    )
    with pytest.raises(EvolutionLabContractError, match="network disabled"):
        SandboxReceiptV1(
            execution_id="execution",
            sandbox_id="sandbox",
            sandbox_profile_digest=spec.isolation_profile_digest,
            dataset_digest=spec.dataset_manifest.dataset_digest,
            output_digest="9" * 64,
            network_disabled=False,
            readonly_dataset=True,
            temporary_output=True,
            effect_audit=audit,
        )


def test_ne04_v2_rejects_direct_promotion_flags() -> None:
    spec = _spec()
    with pytest.raises(EvolutionLabContractError, match="exactly one baseline"):
        ExperimentSpecV2(
            experiment_id="bad-baseline",
            workspace_id=spec.workspace_id,
            dataset_manifest=spec.dataset_manifest,
            seed=spec.seed,
            budget=spec.budget,
            variants=(
                PolicyVariantV1("one", "p", "c", "a" * 64, False),
                PolicyVariantV1("two", "p", "c", "b" * 64, False),
            ),
            verifier_identity=spec.verifier_identity,
            verifier_version=spec.verifier_version,
            verifier_digest=spec.verifier_digest,
            isolation_profile_digest=spec.isolation_profile_digest,
        )

    with pytest.raises(EvolutionLabContractError, match="comparisons are invalid"):
        ExperimentReportV2(
            report_id="missing-counterfactual",
            spec=spec,
            runs=(_run(spec, "baseline"), _run(spec, "candidate")),
            comparisons=(),
        )
