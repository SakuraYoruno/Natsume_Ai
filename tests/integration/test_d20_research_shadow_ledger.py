from __future__ import annotations

import hashlib
import json
import sqlite3

import pytest

from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.persistence.research_shadow_ledger_codec import (
    research_model_receipt_from_dict,
)
from host_brain.persistence.research_shadow_ledger_repository import (
    ResearchArmOperationState,
    ResearchShadowLedgerError,
    SQLiteResearchShadowLedger,
)
from host_brain.contracts.planning_search.research_benchmark_contracts import (
    ExternalProviderRunAttestationV1,
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


def test_d20c_legacy_identity_free_model_receipt_reopens_as_ineligible() -> None:
    raw = {
        "execution_id": "legacy-execution",
        "arm_id": "legacy-arm",
        "evidence_class": "ATTESTED_EXTERNAL",
        "attempt_ids": ["attempt::legacy-execution"],
        "prompt_digest": "d" * 64,
        "tool_call_count": 0,
        "total_tokens": 10,
        "cost_micros": 2,
        "attestation_ref": "",
        "benefit_eligible": True,
        "attestation": {
            "schema": "natsume.research.external-provider-attestation.v1",
            "execution_id": "legacy-execution",
            "provider": "openai",
            "model": "legacy-model",
            "route_digest": "a" * 64,
            "response_digest": "b" * 64,
            "total_tokens": 10,
            "cost_micros": 2,
            "provider_latency_ms": 4,
            "total_latency_ms": 5,
            "fresh_request": True,
        },
    }
    historical = ExternalProviderRunAttestationV1(
        execution_id="legacy-execution",
        provider="openai",
        model="legacy-model",
        route_digest="a" * 64,
        response_digest="b" * 64,
        total_tokens=10,
        cost_micros=2,
        provider_latency_ms=4,
        total_latency_ms=5,
    )
    raw["attestation_ref"] = historical.attestation_ref

    receipt = research_model_receipt_from_dict(raw)

    assert receipt.benefit_eligible is False


def test_d20b_research_ledger_replays_registration_and_persists_paired_receipt(
    tmp_path,
    monkeypatch,
) -> None:
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
    monkeypatch.setattr(
        "host_brain.persistence.research_shadow_ledger_repository.secrets.randbelow",
        lambda upper: 0,
    )
    run_id = ledger.preregister_randomized_run(
        registration_id=registration_id,
        baseline=baseline,
        selected=selected,
        cohort_run_key="d20b-ledger-replay",
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
        arm_order=("BASELINE", "SELECTED"),
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
        arm_order=("BASELINE", "SELECTED"),
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
        arm_order=("BASELINE", "SELECTED"),
        randomized_arm_order=True,
    )
    with pytest.raises(ResearchShadowLedgerError, match="does not match persisted arm evidence"):
        ledger.complete(run_id=run_id, receipt=drifted_receipt)


def test_d20c_owner_randomizes_arm_order_once_and_never_persists_cohort_key(
    tmp_path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "research-owner-randomization.sqlite3"
    assert SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="d20c-owner-randomization"
    ).success is True
    ledger = SQLiteResearchShadowLedger(db_path)
    registration_id = ledger.register(
        ResearchCorpusBindingV1(
            snapshot_digest="1" * 64,
            corpus_ref="artifact://research/corpus/d20c-randomization",
            manifest_digest="2" * 64,
            source_ids=("primary",),
            source_refs=("artifact://research/primary",),
            source_digests=("3" * 64,),
            authority_tiers=("primary",),
        ),
        ResearchExecutionProfileV1("4" * 64, "5" * 64, "6" * 64),
        ResearchExecutionBudgetV1(1000, 2, 10000, 2000, 20000),
    )
    baseline = _plan("baseline", "research.query.scope")
    selected = _plan("selected", "research.primary.primary")
    draws: list[int] = []

    def _draw(upper: int) -> int:
        draws.append(upper)
        return 1

    monkeypatch.setattr(
        "host_brain.persistence.research_shadow_ledger_repository.secrets.randbelow",
        _draw,
    )
    run_id = ledger.preregister_randomized_run(
        registration_id=registration_id,
        baseline=baseline,
        selected=selected,
        cohort_run_key="private-cohort-run-key",
    )
    spec = ledger.get_paired_run_spec(run_id=run_id)
    assert spec.owner_randomized is True
    assert spec.arm_order == ("SELECTED", "BASELINE")
    assert spec.randomization_algorithm == "PYTHON_SECRETS_RANDBELOW_V1"
    assert spec.randomization_draw == 1
    assert spec.cohort_run_key_digest is not None
    with ledger._connection() as conn:  # noqa: SLF001 - verifies public-safe persistence.
        stored = str(
            conn.execute(
                "SELECT spec_json FROM planning_search_research_run_spec WHERE run_id = ?",
                (run_id,),
            ).fetchone()[0]
        )
    assert "private-cohort-run-key" not in stored
    assert (
        ledger.preregister_randomized_run(
            registration_id=registration_id,
            baseline=baseline,
            selected=selected,
            cohort_run_key="private-cohort-run-key",
        )
        == run_id
    )
    assert draws == [2]
    assert (
        ledger.preregister_randomized_run(
            registration_id=registration_id,
            baseline=baseline,
            selected=selected,
            cohort_run_key="private-cohort-run-key-next",
        )
        != run_id
    )
    assert draws == [2, 2]
    assert not hasattr(ledger, "preregister_run")


def test_d20c_legacy_run_specs_are_read_only_but_historic_receipts_reopen(tmp_path) -> None:
    db_path = tmp_path / "research-legacy-run-spec.sqlite3"
    assert SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="d20c-legacy-run-spec"
    ).success is True
    ledger = SQLiteResearchShadowLedger(db_path)
    binding = ResearchCorpusBindingV1(
        snapshot_digest="1" * 64,
        corpus_ref="artifact://research/corpus/d20c-legacy",
        manifest_digest="2" * 64,
        source_ids=("primary",),
        source_refs=("artifact://research/primary",),
        source_digests=("3" * 64,),
        authority_tiers=("primary",),
    )
    profile = ResearchExecutionProfileV1("4" * 64, "5" * 64, "6" * 64)
    budget = ResearchExecutionBudgetV1(1000, 2, 10000, 2000, 20000)
    registration_id = ledger.register(binding, profile, budget)
    baseline = _plan("legacy-baseline", "research.query.scope")
    selected = _plan("legacy-selected", "research.primary.primary")
    legacy_spec = {
        "registration_id": registration_id,
        "baseline": baseline.to_dict(),
        "selected": selected.to_dict(),
        "arm_order": ["BASELINE", "SELECTED"],
        "randomized_arm_order": True,
        "production_influence_enabled": False,
    }
    encoded_spec = json.dumps(legacy_spec, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    spec_digest = hashlib.sha256(encoded_spec.encode("utf-8")).hexdigest()
    run_id = f"researchrun_{spec_digest[:32]}"
    with ledger._connection() as conn:  # noqa: SLF001 - creates an immutable historical fixture.
        conn.execute(
            """
            INSERT INTO planning_search_research_run_spec
            (run_id, spec_digest, registration_id, baseline_arm_id, selected_arm_id,
             spec_json, randomized_arm_order, production_influence_enabled, created_at_ms)
            VALUES (?, ?, ?, ?, ?, ?, 1, 0, 0)
            """,
            (run_id, spec_digest, registration_id, baseline.arm_id, selected.arm_id, encoded_spec),
        )
        conn.commit()
    assert ledger.get_paired_run_spec(run_id=run_id).owner_randomized is False

    baseline_model = _model("legacy-baseline-execution", baseline.arm_id)
    selected_model = _model("legacy-selected-execution", selected.arm_id)
    baseline_verifier = _verifier("legacy-baseline-execution")
    selected_verifier = _verifier("legacy-selected-execution")
    ledger.append_arm_execution(
        run_id=run_id,
        role="BASELINE",
        plan=baseline,
        model=baseline_model,
        verifier=baseline_verifier,
    )
    ledger.append_arm_execution(
        run_id=run_id,
        role="SELECTED",
        plan=selected,
        model=selected_model,
        verifier=selected_verifier,
    )
    receipt = PairedResearchRunReceiptV1(
        corpus_binding_digest=binding.binding_digest,
        profile_digest=profile.profile_digest,
        budget_digest=budget.budget_digest,
        baseline_execution_id=baseline_model.execution_id,
        selected_execution_id=selected_model.execution_id,
        baseline_model=baseline_model,
        selected_model=selected_model,
        baseline_verifier=baseline_verifier,
        selected_verifier=selected_verifier,
        arm_order=("BASELINE", "SELECTED"),
        randomized_arm_order=True,
    )
    with pytest.raises(ResearchShadowLedgerError, match="owner-generated arm randomization"):
        ledger.complete(run_id=run_id, receipt=receipt)
    assert ledger.get_terminal_receipt(run_id=run_id) is None

    encoded_receipt = json.dumps(
        receipt.to_dict(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    receipt_digest = hashlib.sha256(encoded_receipt.encode("utf-8")).hexdigest()
    with ledger._connection() as conn:  # noqa: SLF001 - emulates an immutable pre-upgrade receipt.
        conn.execute(
            """
            INSERT INTO planning_search_research_terminal_receipt
            (run_id, receipt_digest, receipt_json, production_influence_enabled, created_at_ms)
            VALUES (?, ?, ?, 0, 0)
            """,
            (run_id, receipt_digest, encoded_receipt),
        )
        conn.commit()
    reopened = ledger.get_terminal_receipt(run_id=run_id)
    assert reopened is not None
    assert reopened.receipt == receipt


def test_d20c_arm_operation_fences_external_calls_and_preserves_unknown_effects(
    tmp_path,
    monkeypatch,
) -> None:
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
    monkeypatch.setattr(
        "host_brain.persistence.research_shadow_ledger_repository.secrets.randbelow",
        lambda upper: 1,
    )
    run_id = ledger.preregister_randomized_run(
        registration_id=registration_id,
        baseline=baseline,
        selected=selected,
        cohort_run_key="d20c-arm-operation",
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
