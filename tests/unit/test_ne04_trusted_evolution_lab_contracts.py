from __future__ import annotations

from dataclasses import replace

import pytest

from host_brain.contracts.evolution_lab import (
    CounterfactualPairV2,
    DatasetManifestV1,
    EffectAuditReceiptV1,
    ExperimentSpecV2,
    LabDatasetV1,
    PolicyVariantV1,
    VerificationReceiptV1,
    canonical_evolution_lab_digest,
)
from host_brain.contracts.evolution_lab_trusted import (
    DockerSandboxAttestationV1,
    GoldenExperimentSuiteReceiptV1,
    TrustedEvolutionLabContractError,
    TrustedExperimentReportV2,
    TrustedExperimentRunReceiptV2,
    docker_effect_audit_evidence_digest,
)


IMAGE_REFERENCE = "python@sha256:" + "a" * 64
IMAGE_ID = "b" * 64
ENGINE_IDENTITY = "docker://engine/27.5.1@sha256:" + "c" * 64
RUNTIME_SOURCE_DIGEST = "7" * 64
OUTPUT_SOURCE_DIGEST = "8" * 64


def _spec(kind: str = "research_planning") -> ExperimentSpecV2:
    dataset = LabDatasetV1(
        f"golden-{kind}",
        ({"case_id": f"{kind}-1", "expected": "safe"},),
    )
    return ExperimentSpecV2(
        experiment_id=f"ne04-golden-{kind}",
        workspace_id="workspace-ne04",
        dataset_manifest=DatasetManifestV1(
            workspace_id="workspace-ne04",
            dataset_id=dataset.dataset_id,
            dataset_digest=dataset.dataset_digest,
            schema_digest=canonical_evolution_lab_digest(
                {"fields": ["case_id", "expected"]}
            ),
            record_count=len(dataset.records),
            source_kind="synthetic",
        ),
        seed=41,
        budget={"max_runs": 2, "max_wall_time_ms": 30_000},
        variants=(
            PolicyVariantV1(
                "baseline", "policy.v1", "code.v1", "1" * 64, True
            ),
            PolicyVariantV1(
                "candidate", "policy.v2", "code.v2", "2" * 64, False
            ),
        ),
        verifier_identity=f"verifier://ne04/{kind}",
        verifier_version="1.0.0",
        verifier_digest="3" * 64,
        isolation_profile_digest="4" * 64,
    )


def _inspect(
    *,
    command: tuple[str, ...],
    container_id: str,
    container_name: str,
) -> dict[str, object]:
    return {
        "cap_drop": ["ALL"],
        "command": list(command),
        "container_id": container_id,
        "dataset_mount_readonly": True,
        "exit_code": 0,
        "image_id": IMAGE_ID,
        "image_reference": IMAGE_REFERENCE,
        "memory_limit_bytes": 268_435_456,
        "name": container_name,
        "nano_cpus": 1_000_000_000,
        "network_mode": "none",
        "no_new_privileges": True,
        "output_mount_temporary": True,
        "output_mount_type": "bind",
        "output_source_digest": OUTPUT_SOURCE_DIGEST,
        "pids_limit": 64,
        "readonly_rootfs": True,
        "runtime_mount_readonly": True,
        "runtime_source_digest": RUNTIME_SOURCE_DIGEST,
        "user": "65534:65534",
    }


def _run(
    spec: ExperimentSpecV2,
    variant_id: str,
) -> TrustedExperimentRunReceiptV2:
    execution_id = f"trusted-{spec.experiment_id}-{variant_id}"
    container_id = (
        "5" if variant_id == "baseline" else "6"
    ) * 64
    container_name = f"natsume-{spec.experiment_id}-{variant_id}"
    command = (
        "python",
        "/runtime/golden_worker.py",
        "--kind",
        spec.experiment_id,
        "--variant",
        variant_id,
    )
    output_digest = canonical_evolution_lab_digest(
        {"variant": variant_id, "status": "PASS"}
    )
    inspect = _inspect(
        command=command,
        container_id=container_id,
        container_name=container_name,
    )
    audit = EffectAuditReceiptV1(
        execution_id=execution_id,
        audit_id=f"audit-{execution_id}",
        auditor_identity="docker-inspect://ne04",
        audit_evidence_digest=docker_effect_audit_evidence_digest(
            execution_id=execution_id,
            command=command,
            inspect_evidence=inspect,
            sandbox_profile_digest=spec.isolation_profile_digest,
            dataset_digest=spec.dataset_manifest.dataset_digest,
            output_digest=output_digest,
        ),
        effect_count=0,
    )
    sandbox = DockerSandboxAttestationV1(
        execution_id=execution_id,
        engine_identity=ENGINE_IDENTITY,
        image_reference=IMAGE_REFERENCE,
        image_id=IMAGE_ID,
        container_id=container_id,
        container_name=container_name,
        command=command,
        inspect_evidence=inspect,
        sandbox_profile_digest=spec.isolation_profile_digest,
        runtime_source_digest=RUNTIME_SOURCE_DIGEST,
        output_source_digest=OUTPUT_SOURCE_DIGEST,
        dataset_digest=spec.dataset_manifest.dataset_digest,
        output_digest=output_digest,
        output_mount_type="bind",
        network_mode="none",
        readonly_rootfs=True,
        readonly_dataset=True,
        readonly_runtime=True,
        temporary_output=True,
        no_new_privileges=True,
        cap_drop_all=True,
        non_root_user="65534:65534",
        pids_limit=64,
        memory_limit_bytes=268_435_456,
        nano_cpus=1_000_000_000,
        exit_code=0,
        started_at_ms=100,
        finished_at_ms=120,
        effect_audit=audit,
    )
    verifier = VerificationReceiptV1(
        execution_id=execution_id,
        verifier_identity=spec.verifier_identity,
        verifier_version=spec.verifier_version,
        verifier_digest=spec.verifier_digest,
        status="PASS",
        evidence_digest=canonical_evolution_lab_digest(
            {"execution_id": execution_id, "verified": True}
        ),
        outcome={
            "quality": 0.7 if variant_id == "baseline" else 0.9
        },
        risk={"policy_errors": 0},
        metrics={"pass_rate": 1.0},
    )
    return TrustedExperimentRunReceiptV2(
        run_id=f"run-{execution_id}",
        execution_id=execution_id,
        spec_digest=spec.spec_digest,
        workspace_id=spec.workspace_id,
        dataset_manifest_digest=spec.dataset_manifest.manifest_digest,
        variant_id=variant_id,
        status="PASS",
        model_identity="deterministic-golden-policy://v1",
        usage={"records": 1},
        cost={"microusd": 0},
        latency_ms=20,
        sandbox_attestation=sandbox,
        verification_receipt=verifier,
    )


def _report(
    spec: ExperimentSpecV2,
    kind: str,
) -> TrustedExperimentReportV2:
    baseline = _run(spec, "baseline")
    candidate = _run(spec, "candidate")
    return TrustedExperimentReportV2(
        report_id=f"report-{kind}",
        experiment_kind=kind,
        spec=spec,
        runs=(baseline, candidate),
        comparisons=(
            CounterfactualPairV2(
                baseline_run_id=baseline.run_id,
                alternative_run_id=candidate.run_id,
                outcome_delta={"quality": 0.2},
                evidence_delta={"pass_rate": 0.0},
                cost_delta={"microusd": 0.0},
                latency_delta={"milliseconds": 0.0},
                risk_delta={"policy_errors": 0.0},
                verification_delta={"pass_rate": 0.0},
            ),
        ),
    )


def test_ne04_trusted_contract_binds_concrete_docker_and_remains_proposal_only() -> None:
    spec = _spec()
    report = _report(spec, "research_planning")
    run = report.runs[0]

    reopened = TrustedExperimentRunReceiptV2.from_dict(run.to_dict())

    assert reopened == run
    assert run.benefit_eligible is True
    assert run.sandbox_attestation.command_digest
    assert run.sandbox_attestation.inspect_digest
    assert report.promotion_eligible is True
    assert report.to_dict()["production_influence_enabled"] is False


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        ("image_reference", "python:3.12", "pinned"),
        ("network_mode", "bridge", "network mode"),
        ("readonly_rootfs", False, "readonly_rootfs"),
        ("no_new_privileges", False, "no_new_privileges"),
    ),
)
def test_ne04_trusted_contract_rejects_weak_sandbox(
    field: str,
    value: object,
    message: str,
) -> None:
    sandbox = _run(_spec(), "baseline").sandbox_attestation

    with pytest.raises(TrustedEvolutionLabContractError, match=message):
        replace(sandbox, **{field: value})


def test_ne04_trusted_contract_rejects_self_attested_inspect_or_effect_audit() -> None:
    sandbox = _run(_spec(), "baseline").sandbox_attestation
    drifted_inspect = dict(sandbox.inspect_evidence)
    drifted_inspect["network_mode"] = "bridge"

    with pytest.raises(
        TrustedEvolutionLabContractError,
        match="inspect evidence",
    ):
        replace(sandbox, inspect_evidence=drifted_inspect)

    drifted_audit = replace(
        sandbox.effect_audit,
        audit_evidence_digest="9" * 64,
    )
    with pytest.raises(
        TrustedEvolutionLabContractError,
        match="effect audit evidence",
    ):
        replace(sandbox, effect_audit=drifted_audit)


def test_ne04_golden_suite_requires_three_reports_and_six_independent_arms() -> None:
    reports = {
        kind: _report(_spec(kind), kind)
        for kind in ("research_planning", "proactive", "memory_write")
    }
    suite = GoldenExperimentSuiteReceiptV1(
        suite_id="ne04-golden-suite",
        commit_sha="d" * 40,
        migration_head="0139#evolution_lab_trusted_redaction_repair",
        engine_identity=ENGINE_IDENTITY,
        image_reference=IMAGE_REFERENCE,
        report_digests={
            kind: report.report_digest for kind, report in reports.items()
        },
        run_receipt_digests=tuple(
            run.receipt_digest
            for report in reports.values()
            for run in report.runs
        ),
        created_at_ms=200,
    )

    assert suite.status == "PASS"
    assert suite.to_dict()["production_influence_enabled"] is False

    with pytest.raises(
        TrustedEvolutionLabContractError,
        match="exactly three",
    ):
        replace(
            suite,
            report_digests={
                "research_planning": reports[
                    "research_planning"
                ].report_digest
            },
        )
