from __future__ import annotations

import sqlite3

import pytest

from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.persistence.research_shadow_ledger_repository import (
    ResearchArmOperationState,
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


def test_d20c_arm_operation_fences_external_calls_and_preserves_unknown_effects(tmp_path) -> None:
    db_path = tmp_path / "research-arm-operation.sqlite3"
    result = SQLiteMigrationRunner(db_path=db_path).apply_migrations(request_id="d20c-operation")
    assert result.success is True

    ledger = SQLiteResearchShadowLedger(db_path, clock=lambda: 10)
    binding = ResearchCorpusBindingV1(
        snapshot_digest="1" * 64,
        corpus_ref="artifact://research/corpus/d20c-operation",
        manifest_digest="2" * 64,
        source_ids=("primary",),
        source_refs=("artifact://research/primary",),
        source_digests=("3" * 64,),
        authority_tiers=("primary",),
    )
    profile = ResearchExecutionProfileV1("4" * 64, "5" * 64, "6" * 64)
    budget = ResearchExecutionBudgetV1(1000, 2, 10000, 2000, 20000)
    registration_id = ledger.register(binding, profile, budget)
    baseline = _plan("baseline", "research.query.scope")
    selected = _plan("selected", "research.primary.primary")
    run_id = ledger.preregister_run(
        registration_id=registration_id,
        baseline=baseline,
        selected=selected,
        randomized_arm_order=True,
    )

    reserved = ledger.reserve_arm_operation(
        run_id=run_id,
        role="BASELINE",
        plan=baseline,
        execution_id="baseline-execution",
    )
    assert reserved.state is ResearchArmOperationState.RESERVED
    assert ledger.reserve_arm_operation(
        run_id=run_id,
        role="BASELINE",
        plan=baseline,
        execution_id="baseline-execution",
    ) == reserved

    claimed = ledger.claim_arm_operation(
        operation_id=reserved.operation_id,
        owner_id="worker-a",
        lease_duration_ms=10,
        at_ms=100,
    )
    assert claimed.state is ResearchArmOperationState.CALL_CLAIMED
    assert claimed.claim_fencing_token == 1
    with pytest.raises(ResearchShadowLedgerError, match="another owner"):
        ledger.claim_arm_operation(
            operation_id=reserved.operation_id,
            owner_id="worker-b",
            lease_duration_ms=10,
            at_ms=101,
        )

    started = ledger.begin_external_call(
        operation_id=reserved.operation_id,
        claim_id=claimed.claim_id or "",
        at_ms=102,
    )
    assert started.state is ResearchArmOperationState.EXTERNAL_CALL_STARTED

    reopened = SQLiteResearchShadowLedger(db_path, clock=lambda: 20)
    assert reopened.get_arm_operation(run_id=run_id, role="BASELINE") == started
    blocked = reopened.mark_reconciliation_required(
        operation_id=started.operation_id,
        reason_code="CANONICAL_AUDIT_PRESENT_WITHOUT_ARM_EVIDENCE",
        at_ms=103,
    )
    assert blocked.state is ResearchArmOperationState.RECONCILIATION_REQUIRED
    assert blocked.reconciliation_reason == "CANONICAL_AUDIT_PRESENT_WITHOUT_ARM_EVIDENCE"
    with pytest.raises(ResearchShadowLedgerError, match="not claimable"):
        reopened.claim_arm_operation(
            operation_id=started.operation_id,
            owner_id="worker-b",
            lease_duration_ms=10,
            at_ms=104,
        )

    selected_reserved = reopened.reserve_arm_operation(
        run_id=run_id,
        role="SELECTED",
        plan=selected,
        execution_id="selected-execution",
    )
    selected_claim = reopened.claim_arm_operation(
        operation_id=selected_reserved.operation_id,
        owner_id="worker-a",
        lease_duration_ms=10,
        at_ms=200,
    )
    selected_started = reopened.begin_external_call(
        operation_id=selected_reserved.operation_id,
        claim_id=selected_claim.claim_id or "",
        at_ms=201,
    )
    retry = reopened.authorize_retry_without_audit(
        operation_id=selected_started.operation_id,
        at_ms=202,
    )
    assert retry.state is ResearchArmOperationState.RETRY_AUTHORIZED_NO_AUDIT
    retry_claim = reopened.claim_arm_operation(
        operation_id=selected_reserved.operation_id,
        owner_id="worker-b",
        lease_duration_ms=10,
        at_ms=203,
    )
    assert retry_claim.claim_fencing_token == 2
    selected_started = reopened.begin_external_call(
        operation_id=selected_reserved.operation_id,
        claim_id=retry_claim.claim_id or "",
        at_ms=204,
    )
    with sqlite3.connect(db_path) as conn:
        with pytest.raises(sqlite3.IntegrityError, match="lacks evidence row"):
            conn.execute(
                """
                INSERT INTO planning_search_research_arm_operation_event
                (event_id, operation_id, event_kind, event_digest, detail_json, created_at_ms)
                VALUES (?, ?, 'EVIDENCE_PERSISTED', ?, '{}', ?)
                """,
                (
                    f"{selected_started.operation_id}:forged-evidence",
                    selected_started.operation_id,
                    "0" * 64,
                    204,
                ),
            )
    reopened.append_arm_execution(
        run_id=run_id,
        role="SELECTED",
        plan=selected,
        model=_model("selected-execution", selected.arm_id),
        verifier=_verifier("selected-execution"),
    )
    evidence = reopened.mark_arm_evidence_persisted(
        operation_id=selected_started.operation_id,
        at_ms=205,
    )
    assert evidence.state is ResearchArmOperationState.EVIDENCE_PERSISTED
    assert SQLiteResearchShadowLedger(db_path).get_arm_operation(
        run_id=run_id,
        role="SELECTED",
    ) == evidence
    with sqlite3.connect(db_path) as conn:
        with pytest.raises(sqlite3.IntegrityError, match="operation is immutable"):
            conn.execute(
                """
                UPDATE planning_search_research_arm_operation
                SET execution_id = 'forged-execution'
                WHERE operation_id = ?
                """,
                (evidence.operation_id,),
            )
