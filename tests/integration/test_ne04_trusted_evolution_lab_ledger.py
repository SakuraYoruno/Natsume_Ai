from __future__ import annotations

import json
import sqlite3

import pytest

from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.contracts.evolution_lab import (
    CounterfactualPairV2,
    DatasetManifestV1,
    EffectAuditReceiptV1,
    EvolutionProposalV2,
    ExperimentSpecV2,
    LabDatasetV1,
    PolicyVariantV1,
    VerificationReceiptV1,
    canonical_evolution_lab_digest,
)
from host_brain.contracts.evolution_lab_trusted import (
    DockerSandboxAttestationV1,
    GoldenExperimentSuiteReceiptV1,
    RedactionReceiptV1 as TrustedRedactionReceiptV1,
    TrustedExperimentReportV2,
    TrustedExperimentRunReceiptV2,
    docker_effect_audit_evidence_digest,
)
from host_brain.persistence.evolution_lab_trusted_repository import (
    SQLiteTrustedEvolutionLabLedger,
    TrustedEvolutionLabLedgerError,
    TrustedExecutionClaim,
)
from host_brain.persistence.evolution_lab_v2_repository import (
    SQLiteEvolutionLabV2Ledger,
)


IMAGE_REFERENCE = "python@sha256:" + "a" * 64
IMAGE_ID = "b" * 64
ENGINE_IDENTITY = "docker://engine/27.5.1@sha256:" + "c" * 64
RUNTIME_SOURCE_DIGEST = "7" * 64
OUTPUT_SOURCE_DIGEST = "8" * 64


def _spec(kind: str) -> ExperimentSpecV2:
    dataset = LabDatasetV1(
        f"golden-{kind}",
        ({"case_id": f"{kind}-1", "expected": "safe"},),
    )
    return ExperimentSpecV2(
        experiment_id=f"ne04-golden-{kind}",
        workspace_id="workspace-ne04-ledger",
        dataset_manifest=DatasetManifestV1(
            workspace_id="workspace-ne04-ledger",
            dataset_id=dataset.dataset_id,
            dataset_digest=dataset.dataset_digest,
            schema_digest=canonical_evolution_lab_digest(
                {"fields": ["case_id", "expected"]}
            ),
            record_count=1,
            source_kind="synthetic",
        ),
        seed=73,
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


def _receipt(
    spec: ExperimentSpecV2,
    claim: TrustedExecutionClaim,
    *,
    score: float,
) -> TrustedExperimentRunReceiptV2:
    command = (
        "python",
        "/runtime/golden_worker.py",
        "--experiment",
        spec.experiment_id,
        "--variant",
        claim.variant_id,
    )
    container_id = canonical_evolution_lab_digest(
        {"container_name": claim.container_name}
    )
    output_digest = canonical_evolution_lab_digest(
        {"variant": claim.variant_id, "score": score}
    )
    inspect = {
        "cap_drop": ["ALL"],
        "command": list(command),
        "container_id": container_id,
        "dataset_mount_readonly": True,
        "exit_code": 0,
        "image_id": IMAGE_ID,
        "image_reference": IMAGE_REFERENCE,
        "memory_limit_bytes": 268_435_456,
        "name": claim.container_name,
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
    audit = EffectAuditReceiptV1(
        execution_id=claim.execution_id,
        audit_id=f"audit-{claim.execution_id}",
        auditor_identity="docker-inspect://ne04",
        audit_evidence_digest=docker_effect_audit_evidence_digest(
            execution_id=claim.execution_id,
            command=command,
            inspect_evidence=inspect,
            sandbox_profile_digest=spec.isolation_profile_digest,
            dataset_digest=spec.dataset_manifest.dataset_digest,
            output_digest=output_digest,
        ),
        effect_count=0,
    )
    sandbox = DockerSandboxAttestationV1(
        execution_id=claim.execution_id,
        engine_identity=ENGINE_IDENTITY,
        image_reference=IMAGE_REFERENCE,
        image_id=IMAGE_ID,
        container_id=container_id,
        container_name=claim.container_name,
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
        execution_id=claim.execution_id,
        verifier_identity=spec.verifier_identity,
        verifier_version=spec.verifier_version,
        verifier_digest=spec.verifier_digest,
        status="PASS",
        evidence_digest=canonical_evolution_lab_digest(
            {"execution_id": claim.execution_id, "score": score}
        ),
        outcome={"quality": score},
        risk={"policy_errors": 0},
        metrics={"pass_rate": 1.0},
    )
    return TrustedExperimentRunReceiptV2(
        run_id=f"run-{claim.execution_id}",
        execution_id=claim.execution_id,
        spec_digest=spec.spec_digest,
        workspace_id=spec.workspace_id,
        dataset_manifest_digest=spec.dataset_manifest.manifest_digest,
        variant_id=claim.variant_id,
        status="PASS",
        model_identity="deterministic-golden-policy://v1",
        usage={"records": 1},
        cost={"microusd": 0},
        latency_ms=20,
        sandbox_attestation=sandbox,
        verification_receipt=verifier,
    )


def _prepare(tmp_path, *, clock=lambda: 100):
    db_path = tmp_path / "trusted-evolution-lab.sqlite3"
    migrated = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="ne04-trusted-ledger",
        ts_ms=1,
    )
    assert migrated.success is True
    assert migrated.summary["latest_version"] == 139
    return (
        db_path,
        SQLiteEvolutionLabV2Ledger(db_path),
        SQLiteTrustedEvolutionLabLedger(db_path, clock=clock),
    )


def _complete_pair(
    *,
    v2_ledger: SQLiteEvolutionLabV2Ledger,
    trusted_ledger: SQLiteTrustedEvolutionLabLedger,
    spec: ExperimentSpecV2,
) -> tuple[TrustedExperimentRunReceiptV2, TrustedExperimentRunReceiptV2]:
    v2_ledger.register_spec_v2(spec)
    receipts: list[TrustedExperimentRunReceiptV2] = []
    for variant_id, score in (("baseline", 0.7), ("candidate", 0.9)):
        claim = trusted_ledger.reserve_execution(
            spec=spec,
            variant_id=variant_id,
            owner_id=f"worker-{variant_id}",
        )
        assert claim.execution_authorized is True
        receipt = _receipt(spec, claim, score=score)
        receipts.append(
            trusted_ledger.complete_execution(
                claim=claim,
                receipt=receipt,
            )
        )
        replay = trusted_ledger.reserve_execution(
            spec=spec,
            variant_id=variant_id,
            owner_id="different-worker",
        )
        assert replay.execution_authorized is False
        assert replay.replayed is True
        assert replay.receipt == receipt
    return receipts[0], receipts[1]


def _report(
    *,
    spec: ExperimentSpecV2,
    kind: str,
    baseline: TrustedExperimentRunReceiptV2,
    candidate: TrustedExperimentRunReceiptV2,
) -> TrustedExperimentReportV2:
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


def test_ne04_trusted_ledger_fences_duplicates_and_replays_completed_receipt(
    tmp_path,
) -> None:
    db_path, v2_ledger, trusted_ledger = _prepare(tmp_path)
    spec = _spec("research_planning")
    v2_ledger.register_spec_v2(spec)

    claim = trusted_ledger.reserve_execution(
        spec=spec,
        variant_id="baseline",
        owner_id="worker-one",
    )
    duplicate = trusted_ledger.reserve_execution(
        spec=spec,
        variant_id="baseline",
        owner_id="worker-two",
    )

    assert claim.execution_authorized is True
    assert duplicate.execution_authorized is False
    assert duplicate.owner_id == "worker-one"
    receipt = _receipt(spec, claim, score=0.7)
    assert trusted_ledger.complete_execution(
        claim=claim, receipt=receipt
    ) == receipt
    replay = trusted_ledger.reserve_execution(
        spec=spec,
        variant_id="baseline",
        owner_id="worker-three",
    )
    assert replay.receipt == receipt

    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM evolution_lab_trusted_operation_head"
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM evolution_lab_trusted_operation_event"
        ).fetchone()[0] == 2
        assert conn.execute(
            "SELECT COUNT(*) FROM evolution_lab_trusted_run_receipt"
        ).fetchone()[0] == 1


def test_ne04_expired_execution_enters_reconciliation_and_never_blindly_replays(
    tmp_path,
) -> None:
    now = [100]
    _, v2_ledger, trusted_ledger = _prepare(
        tmp_path,
        clock=lambda: now[0],
    )
    spec = _spec("proactive")
    v2_ledger.register_spec_v2(spec)
    claim = trusted_ledger.reserve_execution(
        spec=spec,
        variant_id="candidate",
        owner_id="worker-one",
        lease_ms=10,
    )
    now[0] = 111

    recovered = trusted_ledger.reserve_execution(
        spec=spec,
        variant_id="candidate",
        owner_id="recovery-worker",
    )

    assert claim.execution_authorized is True
    assert recovered.status == "RECONCILIATION_REQUIRED"
    assert recovered.execution_authorized is False
    assert recovered.fencing_token == claim.fencing_token + 1
    with pytest.raises(
        TrustedEvolutionLabLedgerError,
        match="stale or drifted",
    ):
        trusted_ledger.complete_execution(
            claim=claim,
            receipt=_receipt(spec, claim, score=0.9),
        )


def test_ne04_trusted_ledger_is_fail_closed_and_tamper_evident(
    tmp_path,
) -> None:
    db_path, v2_ledger, trusted_ledger = _prepare(tmp_path)
    spec = _spec("memory_write")
    v2_ledger.register_spec_v2(spec)
    claim = trusted_ledger.reserve_execution(
        spec=spec,
        variant_id="baseline",
        owner_id="worker",
    )

    with sqlite3.connect(db_path) as conn:
        with pytest.raises(sqlite3.OperationalError, match="no such function"):
            conn.execute(
                """
                INSERT INTO evolution_lab_trusted_operation_event
                (event_id, operation_id, event_sequence, event_type,
                 payload_json, payload_digest, previous_event_digest,
                 event_digest, created_at_ms)
                VALUES ('forged', ?, 2, 'FAILED', '{}', ?, ?, ?, 101)
                """,
                (claim.operation_id, "0" * 64, "1" * 64, "2" * 64),
            )

    with trusted_ledger._connection() as conn:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            conn.execute(
                """
                UPDATE evolution_lab_trusted_operation_event
                SET payload_json = '{"tampered":true}'
                WHERE operation_id = ?
                """,
                (claim.operation_id,),
            )

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT payload_json FROM evolution_lab_trusted_operation_event
            WHERE operation_id = ?
            """,
            (claim.operation_id,),
        ).fetchone()
        assert json.loads(row[0])["status"] == "RUNNING"


def test_ne04_trusted_ledger_rolls_back_receipt_and_event_on_head_failure(
    tmp_path,
) -> None:
    db_path, v2_ledger, trusted_ledger = _prepare(tmp_path)
    spec = _spec("research_planning")
    v2_ledger.register_spec_v2(spec)
    claim = trusted_ledger.reserve_execution(
        spec=spec,
        variant_id="baseline",
        owner_id="worker",
    )
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TRIGGER test_abort_trusted_completion
            BEFORE UPDATE ON evolution_lab_trusted_operation_head
            WHEN NEW.status = 'COMPLETED'
            BEGIN SELECT RAISE(ABORT, 'injected trusted completion failure'); END
            """
        )

    with pytest.raises(
        sqlite3.IntegrityError,
        match="injected trusted completion failure",
    ):
        trusted_ledger.complete_execution(
            claim=claim,
            receipt=_receipt(spec, claim, score=0.7),
        )

    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM evolution_lab_trusted_run_receipt"
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM evolution_lab_trusted_operation_event"
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT status FROM evolution_lab_trusted_operation_head"
        ).fetchone()[0] == "RUNNING"


def test_ne04_trusted_ledger_rejects_migration_receipt_drift(tmp_path) -> None:
    db_path, _, _ = _prepare(tmp_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            UPDATE schema_migration_assets
            SET checksum = ?
            WHERE asset_key = '0138#evolution_lab_trusted_sandbox'
            """,
            ("0" * 64,),
        )

    with pytest.raises(
        TrustedEvolutionLabLedgerError,
        match="schema is not ready",
    ):
        SQLiteTrustedEvolutionLabLedger(db_path)


def test_ne04_trusted_ledger_persists_three_golden_reports_and_proposal_only(
    tmp_path,
) -> None:
    db_path, v2_ledger, trusted_ledger = _prepare(tmp_path)
    reports: dict[str, TrustedExperimentReportV2] = {}
    run_digests: list[str] = []
    for kind in ("research_planning", "proactive", "memory_write"):
        spec = _spec(kind)
        baseline, candidate = _complete_pair(
            v2_ledger=v2_ledger,
            trusted_ledger=trusted_ledger,
            spec=spec,
        )
        report = _report(
            spec=spec,
            kind=kind,
            baseline=baseline,
            candidate=candidate,
        )
        assert trusted_ledger.append_report(report) == report.report_digest
        assert trusted_ledger.append_report(report) == report.report_digest
        reports[kind] = report
        run_digests.extend(
            (baseline.receipt_digest, candidate.receipt_digest)
        )

    proposal = EvolutionProposalV2(
        proposal_id="proposal-research-planning",
        report_digest=reports["research_planning"].report_digest,
        target_kind="planning-policy",
        target_version="candidate.v2",
        rationale="可信 Lab 仅生成待 Review 的演化提案。",
    )
    assert trusted_ledger.append_proposal(proposal) == proposal.proposal_id
    suite = GoldenExperimentSuiteReceiptV1(
        suite_id="ne04-golden-suite-ledger",
        commit_sha="d" * 40,
        migration_head="0139#evolution_lab_trusted_redaction_repair",
        engine_identity=ENGINE_IDENTITY,
        image_reference=IMAGE_REFERENCE,
        report_digests={
            kind: report.report_digest for kind, report in reports.items()
        },
        run_receipt_digests=tuple(run_digests),
        created_at_ms=200,
    )
    assert trusted_ledger.append_golden_suite(suite) == suite.receipt_digest

    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM evolution_lab_trusted_run_receipt"
        ).fetchone()[0] == 6
        assert conn.execute(
            "SELECT COUNT(*) FROM evolution_lab_trusted_report"
        ).fetchone()[0] == 3
        proposal_flags = conn.execute(
            """
            SELECT review_required, production_influence_enabled
            FROM evolution_lab_trusted_proposal
            """
        ).fetchone()
        assert proposal_flags == (1, 0)
        suite_flags = conn.execute(
            """
            SELECT status, production_side_effects,
                   production_influence_enabled
            FROM evolution_lab_golden_suite_receipt
            """
        ).fetchone()
        assert suite_flags == ("PASS", 0, 0)


def test_ne04_trusted_redaction_uses_distinct_0139_namespace(
    tmp_path,
) -> None:
    db_path, _, trusted_ledger = _prepare(tmp_path)
    receipt = TrustedRedactionReceiptV1(
        redaction_id="trusted-redaction-ne04",
        workspace_id="workspace-ne04-redaction",
        source_digest="1" * 64,
        rules_version="redaction.v2",
        rules_digest="2" * 64,
        output_dataset_digest="3" * 64,
        record_count=4,
        removed_field_count=2,
        created_at_ms=100,
    )

    assert (
        trusted_ledger.append_redaction_receipt(receipt)
        == receipt.receipt_digest
    )
    assert (
        trusted_ledger.append_redaction_receipt(receipt)
        == receipt.receipt_digest
    )
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            """
            SELECT COUNT(*)
            FROM evolution_lab_trusted_redaction_receipt
            """
        ).fetchone()[0] == 1
        columns = {
            str(row[1])
            for row in conn.execute(
                "PRAGMA table_info(evolution_lab_redaction_receipt)"
            )
        }
    assert "receipt_id" in columns
    assert "redaction_id" not in columns
