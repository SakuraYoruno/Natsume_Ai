from __future__ import annotations

import pytest

from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.persistence.research_shadow_ledger_repository import (
    ResearchShadowLedgerError,
    SQLiteResearchShadowLedger,
)
from host_brain.planning_search.research_benchmark_contracts import (
    PairedResearchRunReceiptV1,
    ResearchArmPlanV1,
    ResearchCorpusBindingV1,
    ResearchExecutionBudgetV1,
    ResearchExecutionProfileV1,
    ResearchModelExecutionReceiptV1,
    ResearchVerifierReceiptV1,
)


def _plan(arm_id: str, candidate: str) -> ResearchArmPlanV1:
    return ResearchArmPlanV1(
        arm_id=arm_id,
        candidate_ids=(candidate,),
        source_ids=("primary",),
        graph_digest="a" * 64,
        path_digest="b" * 64,
    )


def _model(execution_id: str, arm_id: str) -> ResearchModelExecutionReceiptV1:
    return ResearchModelExecutionReceiptV1(
        execution_id=execution_id,
        arm_id=arm_id,
        evidence_class="SCRIPTED_REPLAY",
        attempt_ids=(f"attempt::{execution_id}",),
    )


def _verifier(execution_id: str) -> ResearchVerifierReceiptV1:
    return ResearchVerifierReceiptV1(
        execution_id=execution_id,
        citation_count=1,
        cited_span_count=1,
        citation_coverage=1.0,
        citation_validity=1.0,
        evidence_digest="c" * 64,
    )


def test_d20b_research_ledger_replays_registration_and_persists_paired_receipt(tmp_path) -> None:
    db_path = tmp_path / "research-shadow.sqlite3"
    result = SQLiteMigrationRunner(db_path=db_path).apply_migrations(request_id="d20b-ledger")
    assert result.success is True

    ledger = SQLiteResearchShadowLedger(db_path)
    binding = ResearchCorpusBindingV1(
        snapshot_digest="1" * 64,
        corpus_ref="artifact://research/corpus/d20b",
        manifest_digest="2" * 64,
        source_ids=("primary",),
        source_refs=("artifact://research/primary",),
        source_digests=("3" * 64,),
        authority_tiers=("primary",),
    )
    profile = ResearchExecutionProfileV1("4" * 64, "5" * 64, "6" * 64)
    budget = ResearchExecutionBudgetV1(1000, 2, 10000, 2000, 20000)
    registration_id = ledger.register(binding, profile, budget)
    assert ledger.register(binding, profile, budget) == registration_id

    baseline = _plan("baseline", "research.query.scope")
    selected = _plan("selected", "research.primary.primary")
    run_id = ledger.preregister_run(
        registration_id=registration_id,
        baseline=baseline,
        selected=selected,
        randomized_arm_order=True,
    )
    ledger.append_arm_execution(
        run_id=run_id,
        role="BASELINE",
        plan=baseline,
        model=_model("baseline-execution", baseline.arm_id),
        verifier=_verifier("baseline-execution"),
    )
    ledger.append_arm_execution(
        run_id=run_id,
        role="BASELINE",
        plan=baseline,
        model=_model("baseline-execution", baseline.arm_id),
        verifier=_verifier("baseline-execution"),
    )
    with pytest.raises(ResearchShadowLedgerError, match="arm execution replay drift"):
        ledger.append_arm_execution(
            run_id=run_id,
            role="BASELINE",
            plan=baseline,
            model=_model("baseline-execution-drift", baseline.arm_id),
            verifier=_verifier("baseline-execution-drift"),
        )
    ledger.append_arm_execution(
        run_id=run_id,
        role="SELECTED",
        plan=selected,
        model=_model("selected-execution", selected.arm_id),
        verifier=_verifier("selected-execution"),
    )
    receipt = PairedResearchRunReceiptV1(
        corpus_binding_digest=binding.binding_digest,
        profile_digest=profile.profile_digest,
        budget_digest=budget.budget_digest,
        baseline_execution_id="baseline-execution",
        selected_execution_id="selected-execution",
        baseline_model=_model("baseline-execution", baseline.arm_id),
        selected_model=_model("selected-execution", selected.arm_id),
        baseline_verifier=_verifier("baseline-execution"),
        selected_verifier=_verifier("selected-execution"),
        randomized_arm_order=True,
    )
    receipt_ref = ledger.complete(run_id=run_id, receipt=receipt)

    assert receipt_ref.startswith("research-receipt://sha256/")
    assert ledger.complete(run_id=run_id, receipt=receipt) == receipt_ref

    wrong_budget_receipt = PairedResearchRunReceiptV1(
        corpus_binding_digest=binding.binding_digest,
        profile_digest=profile.profile_digest,
        budget_digest="f" * 64,
        baseline_execution_id="baseline-execution",
        selected_execution_id="selected-execution",
        baseline_model=_model("baseline-execution", baseline.arm_id),
        selected_model=_model("selected-execution", selected.arm_id),
        baseline_verifier=_verifier("baseline-execution"),
        selected_verifier=_verifier("selected-execution"),
        randomized_arm_order=True,
    )
    with pytest.raises(ResearchShadowLedgerError, match="does not bind its registration"):
        ledger.complete(run_id=run_id, receipt=wrong_budget_receipt)

    drifted_receipt = PairedResearchRunReceiptV1(
        corpus_binding_digest=binding.binding_digest,
        profile_digest=profile.profile_digest,
        budget_digest=budget.budget_digest,
        baseline_execution_id="baseline-execution-drift",
        selected_execution_id="selected-execution",
        baseline_model=_model("baseline-execution-drift", baseline.arm_id),
        selected_model=_model("selected-execution", selected.arm_id),
        baseline_verifier=_verifier("baseline-execution-drift"),
        selected_verifier=_verifier("selected-execution"),
        randomized_arm_order=True,
    )
    with pytest.raises(ResearchShadowLedgerError, match="does not match persisted arm evidence"):
        ledger.complete(run_id=run_id, receipt=drifted_receipt)
