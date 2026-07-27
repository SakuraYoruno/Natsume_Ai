from __future__ import annotations

import os
from pathlib import Path
import sqlite3

import pytest

from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.evolution_lab.production_composition import (
    build_trusted_evolution_lab_composition,
)
from host_brain.evolution_lab.golden_datasets import build_golden_spec
from host_brain.evolution_lab.trusted_runner import (
    TrustedEvolutionLabPorts,
    TrustedEvolutionLabRunner,
)


pytestmark = pytest.mark.skipif(
    os.getenv("NATSUME_RUN_REAL_DOCKER_TESTS") != "1",
    reason="real digest-pinned Docker integration is opt-in",
)


IMAGE = (
    "python@sha256:"
    "6d43704baacd1bfbe7c295d7f13079d5d8104ed33568873133f8fc69980419df"
)


class _ResponseLossLedger:
    def __init__(self, delegate) -> None:
        self.delegate = delegate
        self.raised = False

    def reserve_execution(self, **kwargs):
        return self.delegate.reserve_execution(**kwargs)

    def mark_reconciliation_required(self, **kwargs):
        return self.delegate.mark_reconciliation_required(**kwargs)

    def complete_execution(self, **kwargs):
        receipt = self.delegate.complete_execution(**kwargs)
        if not self.raised:
            self.raised = True
            raise RuntimeError("simulated response loss after commit")
        return receipt


def test_ne04_runs_three_real_docker_golden_experiments(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "ne04-real-docker.sqlite3"
    runtime_root = tmp_path / "runtime"
    migrated = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="ne04-real-docker-integration",
        ts_ms=1,
    )
    assert migrated.success is True
    assert migrated.summary["latest_version"] >= 139
    composition = build_trusted_evolution_lab_composition(
        db_path=db_path,
        runtime_root=runtime_root,
        image_reference=IMAGE,
    )
    result = composition.golden_experiments.run_suite(
        workspace_id="workspace-ne04-real-docker",
        commit_sha="a" * 40,
        owner_id="pytest-real-docker",
    )

    assert result.receipt.status == "PASS"
    assert set(result.reports) == {
        "research_planning",
        "proactive",
        "memory_write",
    }
    assert len(result.receipt.run_receipt_digests) == 6
    assert all(
        run.sandbox_attestation.image_reference == IMAGE
        for report in result.reports.values()
        for run in report.runs
    )
    assert all(
        run.sandbox_attestation.network_mode == "none"
        and run.sandbox_attestation.readonly_rootfs
        and run.sandbox_attestation.non_root_user == "65534:65534"
        and run.sandbox_attestation.effect_audit.effect_count == 0
        for report in result.reports.values()
        for run in report.runs
    )
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM evolution_lab_trusted_run_receipt"
        ).fetchone()[0] == 6
        assert conn.execute(
            "SELECT COUNT(*) FROM evolution_lab_trusted_report"
        ).fetchone()[0] == 3
        assert conn.execute(
            "SELECT COUNT(*) FROM evolution_lab_trusted_proposal"
        ).fetchone()[0] == 3
        assert conn.execute(
            "SELECT COUNT(*) FROM evolution_lab_golden_suite_receipt"
        ).fetchone()[0] == 1
    assert not runtime_root.exists() or not any(runtime_root.iterdir())


def test_ne04_real_docker_completion_response_loss_reopens_exact_receipt(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "ne04-response-loss.sqlite3"
    runtime_root = tmp_path / "runtime"
    migrated = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="ne04-real-docker-response-loss",
        ts_ms=1,
    )
    assert migrated.success is True
    composition = build_trusted_evolution_lab_composition(
        db_path=db_path,
        runtime_root=runtime_root,
        image_reference=IMAGE,
    )
    ledger = _ResponseLossLedger(composition.evidence_ledger)
    runner = TrustedEvolutionLabRunner(
        ports=TrustedEvolutionLabPorts(
            dataset_reader=composition.dataset_reader,
            executor=composition.executor,
            verifier=composition.verifier,
            spec_registry=composition.spec_registry,
            operation_ledger=ledger,
        )
    )
    spec = build_golden_spec(
        experiment_kind="research_planning",
        workspace_id="workspace-ne04-response-loss",
        code_version="b" * 40,
        isolation_profile_digest=(
            composition.executor.sandbox_profile_digest
        ),
        verifier_digest=composition.verifier.verifier_digest,
    )
    first = runner.run_variant(
        experiment_kind="research_planning",
        spec=spec,
        variant_id="candidate",
        owner_id="response-loss-owner",
    )
    second = runner.run_variant(
        experiment_kind="research_planning",
        spec=spec,
        variant_id="candidate",
        owner_id="different-owner",
    )

    assert ledger.raised is True
    assert second == first
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM evolution_lab_trusted_run_receipt"
        ).fetchone()[0] == 1
        assert conn.execute(
            """
            SELECT COUNT(*) FROM evolution_lab_trusted_operation_event
            """
        ).fetchone()[0] == 2
    assert not runtime_root.exists() or not any(runtime_root.iterdir())
