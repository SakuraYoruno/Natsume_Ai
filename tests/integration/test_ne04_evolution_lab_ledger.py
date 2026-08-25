from __future__ import annotations

import pytest

from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.contracts.evolution_lab import (
    CounterfactualPairV1,
    EvolutionProposalV1,
    ExperimentReportV1,
    ExperimentRunReceiptV1,
    ExperimentSpecV1,
    RedactionReceiptV1,
)
from host_brain.persistence.evolution_lab_ledger_repository import (
    EvolutionLabLedgerError,
    SQLiteEvolutionLabLedger,
)


def _run(run_id: str, spec: ExperimentSpecV1, variant: str) -> ExperimentRunReceiptV1:
    return ExperimentRunReceiptV1(
        run_id=run_id,
        spec_digest=spec.spec_digest,
        variant=variant,
        status="PASS",
        evidence_digest=("a" if variant == "baseline" else "b") * 64,
        outcome={"quality": 0.8 if variant == "alternative" else 0.7},
        cost={"tokens": 10},
        risk={"policy_errors": 0},
    )


def test_ne04_evolution_lab_ledger_replays_and_blocks_unbound_proposal(tmp_path) -> None:
    db_path = tmp_path / "evolution-lab.sqlite3"
    result = SQLiteMigrationRunner(db_path=db_path).apply_migrations(request_id="ne04-ledger")
    assert result.success is True
    ledger = SQLiteEvolutionLabLedger(db_path)
    spec = ExperimentSpecV1(
        experiment_id="exp-research-planning",
        dataset_digest="1" * 64,
        seed=7,
        budget={"runs": 2, "tokens": 100},
        variants=("baseline", "alternative"),
        verifier_ref="verifier://evolution/ne04",
        policy_version="policy.v1",
    )
    assert ledger.register_spec(spec) == spec.spec_digest
    assert ledger.register_spec(spec) == spec.spec_digest
    redaction = RedactionReceiptV1(
        receipt_id="redaction-ne04",
        source_digest="2" * 64,
        rules_version="redaction.v1",
        output_dataset_digest="3" * 64,
        redacted_fields=("private_body", "raw_thought"),
        source_kind="real_trace",
    )
    assert ledger.append_redaction(redaction) == redaction.receipt_digest
    assert ledger.append_redaction(redaction) == redaction.receipt_digest

    baseline = _run("run-baseline", spec, "baseline")
    alternative = _run("run-alternative", spec, "alternative")
    assert ledger.append_run(baseline) == baseline.run_id
    assert ledger.append_run(alternative) == alternative.run_id
    assert ledger.append_run(baseline) == baseline.run_id

    pair = CounterfactualPairV1(
        baseline_run_id=baseline.run_id,
        alternative_run_id=alternative.run_id,
        outcome_delta={"quality": 0.1},
        evidence_delta={"coverage": 0.1},
        cost_delta={"tokens": 0.0},
        risk_delta={"policy_errors": 0.0},
    )
    report = ExperimentReportV1(
        report_id="report-ne04",
        spec=spec,
        runs=(baseline, alternative),
        comparisons=(pair,),
    )
    assert ledger.append_report(report) == report.report_digest
    proposal = EvolutionProposalV1(
        proposal_id="proposal-ne04",
        report_digest=report.report_digest,
        target_kind="planning_policy",
        target_version="candidate.v2",
        rationale="alternative improves verified quality",
    )
    assert ledger.append_proposal(proposal) == proposal.proposal_id

    with pytest.raises(EvolutionLabLedgerError):
        ledger.append_proposal(
            EvolutionProposalV1(
                proposal_id="proposal-unbound",
                report_digest="f" * 64,
                target_kind="planning_policy",
                target_version="candidate.v3",
                rationale="unbound",
            )
        )
