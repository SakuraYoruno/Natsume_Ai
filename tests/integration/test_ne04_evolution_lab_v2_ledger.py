from __future__ import annotations

import sqlite3

import pytest

from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.evolution_lab.contracts import (
    CounterfactualPairV2,
    DatasetManifestV1,
    EffectAuditReceiptV1,
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
from host_brain.persistence.evolution_lab_v2_repository import (
    EvolutionLabV2LedgerError,
    SQLiteEvolutionLabV2Ledger,
)


def _dataset() -> LabDatasetV1:
    return LabDatasetV1("ledger-dataset", ({"query": "one", "expected": "receipt"},))


def _spec(experiment_id: str = "ledger-v2") -> ExperimentSpecV2:
    dataset = _dataset()
    return ExperimentSpecV2(
        experiment_id=experiment_id,
        workspace_id="workspace-ledger",
        dataset_manifest=DatasetManifestV1(
            workspace_id="workspace-ledger",
            dataset_id=dataset.dataset_id,
            dataset_digest=dataset.dataset_digest,
            schema_digest=canonical_evolution_lab_digest({"schema": "ledger-v2"}),
            record_count=len(dataset.records),
            source_kind="synthetic",
        ),
        seed=29,
        budget={"max_runs": 2, "tokens": 100},
        variants=(
            PolicyVariantV1("baseline", "policy.v1", "code.v1", "1" * 64, True),
            PolicyVariantV1("candidate", "policy.v2", "code.v2", "2" * 64, False),
        ),
        verifier_identity="verifier://ledger-v2",
        verifier_version="v2",
        verifier_digest="3" * 64,
        isolation_profile_digest="4" * 64,
    )


def _receipt(spec: ExperimentSpecV2, variant_id: str) -> ExperimentRunReceiptV2:
    execution_id = f"execution-{spec.experiment_id}-{variant_id}"
    output = {"variant": variant_id, "answer": "verified"}
    audit = EffectAuditReceiptV1(
        execution_id=execution_id,
        audit_id=f"audit-{variant_id}",
        auditor_identity="sandbox-audit://ledger",
        audit_evidence_digest=("5" if variant_id == "baseline" else "6") * 64,
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
        effect_audit=audit,
    )
    verification = VerificationReceiptV1(
        execution_id=execution_id,
        verifier_identity=spec.verifier_identity,
        verifier_version=spec.verifier_version,
        verifier_digest=spec.verifier_digest,
        status="PASS",
        evidence_digest=("7" if variant_id == "baseline" else "8") * 64,
        outcome={"quality": 0.7 if variant_id == "baseline" else 0.8},
        risk={"policy_errors": 0},
        metrics={"valid": 1},
    )
    return ExperimentRunReceiptV2(
        run_id=f"run-{spec.experiment_id}-{variant_id}",
        execution_id=execution_id,
        spec_digest=spec.spec_digest,
        workspace_id=spec.workspace_id,
        dataset_manifest_digest=spec.dataset_manifest.manifest_digest,
        variant_id=variant_id,
        status="PASS",
        model_identity="structural-ledger-model",
        usage={"tokens": 8},
        cost={"microusd": 12},
        latency_ms=14,
        sandbox_receipt=sandbox,
        verification_receipt=verification,
    )


def test_ne04_v2_ledger_replays_exact_evidence_and_remains_parallel_to_v1(tmp_path) -> None:
    db_path = tmp_path / "evolution-lab-v2.sqlite3"
    result = SQLiteMigrationRunner(db_path=db_path).apply_migrations(request_id="ne04-v2-ledger")
    assert result.success is True
    assert result.summary["latest_version"] >= 122

    ledger = SQLiteEvolutionLabV2Ledger(db_path)
    spec = _spec()
    assert ledger.register_spec_v2(spec) == spec.spec_digest
    baseline = _receipt(spec, "baseline")
    candidate = _receipt(spec, "candidate")
    assert ledger.append_execution_and_run(baseline) == baseline.run_id
    assert ledger.append_run_v2(candidate) == candidate.run_id
    assert ledger.append_execution_and_run(baseline) == baseline.run_id

    report = ExperimentReportV2(
        report_id="report-ledger-v2",
        spec=spec,
        runs=(baseline, candidate),
        comparisons=(
            CounterfactualPairV2(
                baseline_run_id=baseline.run_id,
                alternative_run_id=candidate.run_id,
                outcome_delta={"quality": 0.1},
                evidence_delta={"valid": 0.0},
                cost_delta={"microusd": 0.0},
                latency_delta={"p95_ms": 0.0},
                risk_delta={"policy_errors": 0.0},
                verification_delta={"pass_rate": 0.0},
            ),
        ),
    )
    assert ledger.append_report_v2(report) == report.report_digest
    proposal = EvolutionProposalV2(
        proposal_id="proposal-ledger-v2",
        report_digest=report.report_digest,
        target_kind="planning-policy",
        target_version="candidate.v2",
        rationale="V2 evidence remains proposal-only until a later Review and activation wave.",
    )
    assert ledger.append_proposal_v2(proposal) == proposal.proposal_id

    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM evolution_lab_v2_run_receipt").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM evolution_lab_run_receipt").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM evolution_lab_v2_execution_evidence").fetchone()[0] == 2


def test_ne04_v2_ledger_rolls_back_evidence_if_run_insert_fails(tmp_path) -> None:
    db_path = tmp_path / "evolution-lab-v2-rollback.sqlite3"
    result = SQLiteMigrationRunner(db_path=db_path).apply_migrations(request_id="ne04-v2-rollback")
    assert result.success is True
    ledger = SQLiteEvolutionLabV2Ledger(db_path)
    spec = _spec("ledger-v2-rollback")
    ledger.register_spec_v2(spec)

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TRIGGER test_v2_abort_run_insert
            BEFORE INSERT ON evolution_lab_v2_run_receipt
            BEGIN SELECT RAISE(ABORT, 'injected V2 run failure'); END;
            """
        )

    with pytest.raises(sqlite3.IntegrityError, match="injected V2 run failure"):
        ledger.append_execution_and_run(_receipt(spec, "baseline"))

    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM evolution_lab_v2_execution_evidence").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM evolution_lab_v2_run_receipt").fetchone()[0] == 0


def test_ne04_v2_ledger_refuses_unregistered_or_replayed_drift(tmp_path) -> None:
    db_path = tmp_path / "evolution-lab-v2-drift.sqlite3"
    result = SQLiteMigrationRunner(db_path=db_path).apply_migrations(request_id="ne04-v2-drift")
    assert result.success is True
    ledger = SQLiteEvolutionLabV2Ledger(db_path)
    spec = _spec()
    receipt = _receipt(spec, "baseline")

    with pytest.raises(EvolutionLabV2LedgerError, match="unregistered exact spec"):
        ledger.append_execution_and_run(receipt)
    ledger.register_spec_v2(spec)
    ledger.append_execution_and_run(receipt)
    drifted = ExperimentRunReceiptV2(
        run_id=receipt.run_id,
        execution_id=receipt.execution_id,
        spec_digest=receipt.spec_digest,
        workspace_id=receipt.workspace_id,
        dataset_manifest_digest=receipt.dataset_manifest_digest,
        variant_id=receipt.variant_id,
        status=receipt.status,
        model_identity=receipt.model_identity,
        usage=receipt.usage,
        cost={"microusd": 999},
        latency_ms=receipt.latency_ms,
        sandbox_receipt=receipt.sandbox_receipt,
        verification_receipt=receipt.verification_receipt,
    )
    with pytest.raises(EvolutionLabV2LedgerError, match="run replay drift"):
        ledger.append_execution_and_run(drifted)
