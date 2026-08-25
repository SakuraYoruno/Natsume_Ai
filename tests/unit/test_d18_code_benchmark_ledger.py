from __future__ import annotations

import pytest

from host_brain.contracts.planning_search.benchmarking import (
    BenchmarkArm,
    BenchmarkArmOutcome,
    DomainBenchmarkReport,
    DomainBenchmarkWorkload,
)
from host_brain.contracts.planning_search.contracts import SearchBudget
from host_brain.contracts.planning_search.code_benchmark_ledger import (
    CodeBenchmarkExecutionBudget,
    CodeBenchmarkLedgerContractError,
    CodeBenchmarkRunReceipt,
    CodeBenchmarkRunSpec,
    CodeBenchmarkWorkloadRegistration,
)
from host_brain.contracts.planning_search.code_benchmark_models import (
    CODE_HISTORICAL_BENCHMARK_EXECUTOR_TRUST,
    CODE_HISTORICAL_BENCHMARK_MODE,
    CODE_HISTORICAL_BENCHMARK_NETWORK_ISOLATION,
    CodeBenchmarkArmEvidence,
    CodeBenchmarkContractError,
    CodeBenchmarkCleanupReceipt,
    CodeHistoricalBenchmarkRun,
    CodeVerifierResult,
)


COMMIT = "a" * 40
POLICY_DIGEST = "b" * 64


def _arm(name: str) -> BenchmarkArm:
    return BenchmarkArm(
        arm_id=name,
        selected_candidate_id=f"{name}-candidate",
        selected_path_candidate_ids=(f"{name}-candidate", f"{name}-followup"),
        trace_digest="c" * 64,
        counters={"candidates": 2, "rollouts": 1},
        virtual_state_schema="natsume.planning-search.virtual-state.v1",
        virtual_state_digest="d" * 64,
    )


def _workload(**overrides) -> DomainBenchmarkWorkload:
    values = {
        "workload_id": "code-d18-unit",
        "domain": "code",
        "workload_ref": "benchmark://code/d18/unit",
        "baseline_ref": "benchmark://code/d18/baseline",
        "verifier_refs": ("verifier://code/d18/fixture",),
        "isolation": {
            "sandbox_mode": CODE_HISTORICAL_BENCHMARK_MODE,
            "source_ref": "snapshot://code/d18/unit",
            "source_commit": COMMIT,
            "path_policy_digest": POLICY_DIGEST,
            "executor_trust": CODE_HISTORICAL_BENCHMARK_EXECUTOR_TRUST,
            "network_isolation": CODE_HISTORICAL_BENCHMARK_NETWORK_ISOLATION,
        },
        "budget": SearchBudget(max_candidates=4, max_expansions=2, max_rollouts=1),
        "cohort_key": "code-d18-unit",
    }
    values.update(overrides)
    return DomainBenchmarkWorkload(**values)


def _registration(**overrides) -> CodeBenchmarkWorkloadRegistration:
    values = {
        "workload": _workload(),
        "execution_budget": CodeBenchmarkExecutionBudget(
            max_tool_calls=2,
            max_wall_time_ms=10_000,
        ),
    }
    values.update(overrides)
    return CodeBenchmarkWorkloadRegistration(**values)


def _outcome(arm: BenchmarkArm, *, diff: str, cost: dict[str, int]) -> BenchmarkArmOutcome:
    return BenchmarkArmOutcome(
        arm_id=arm.arm_id,
        verifier_receipt_refs=(f"receipt://code/d18/{arm.arm_id}",),
        success=True,
        latency_ms=5,
        cost=cost,
        workspace_diff_digest=diff,
        executed=True,
    )


def _evidence(
    arm: BenchmarkArm,
    *,
    diff: str,
    cost: dict[str, int],
    source_ref: str = "snapshot://code/d18/unit",
) -> CodeBenchmarkArmEvidence:
    return CodeBenchmarkArmEvidence(
        arm_id=arm.arm_id,
        source_ref=source_ref,
        source_commit=COMMIT,
        path_policy_digest=POLICY_DIGEST,
        changed_file_count=1,
        changed_path_digest=("e" if arm.arm_id == "baseline" else "f") * 64,
        workspace_diff_digest=diff,
        verifier=CodeVerifierResult(
            success=True,
            receipt_ref=f"receipt://code/d18/{arm.arm_id}",
            cost=cost,
            evidence_digest=("1" if arm.arm_id == "baseline" else "2") * 64,
        ),
    )


def completed_receipt(**overrides) -> CodeBenchmarkRunReceipt:
    registration = _registration()
    baseline = _arm("baseline")
    selected = _arm("selected")
    spec = CodeBenchmarkRunSpec(
        run_id="run-d18-unit",
        registration=registration,
        baseline=baseline,
        selected=selected,
    )
    baseline_cost = {"tool_call_count": 1}
    selected_cost = {"tool_call_count": 1}
    baseline_diff = "3" * 64
    selected_diff = "4" * 64
    report = DomainBenchmarkReport(
        workload=registration.workload,
        baseline=baseline,
        selected=selected,
        baseline_outcome=_outcome(baseline, diff=baseline_diff, cost=baseline_cost),
        selected_outcome=_outcome(selected, diff=selected_diff, cost=selected_cost),
    )
    run = CodeHistoricalBenchmarkRun(
        run_id=spec.run_id,
        report=report,
        baseline_evidence=_evidence(baseline, diff=baseline_diff, cost=baseline_cost),
        selected_evidence=_evidence(selected, diff=selected_diff, cost=selected_cost),
        cleanup_receipt=CodeBenchmarkCleanupReceipt(
            run_id=spec.run_id,
            source_before_digest="5" * 64,
            source_after_digest="5" * 64,
            private_bare_removed=True,
            worktrees_removed=True,
            run_root_removed=True,
        ),
    )
    values = {"spec": spec, "run": run}
    values.update(overrides)
    return CodeBenchmarkRunReceipt(**values)


def test_registration_and_run_spec_are_identity_bound_and_path_free() -> None:
    receipt = completed_receipt()
    registration = receipt.spec.registration

    assert registration.registration_id.startswith("codebenchwork_")
    assert registration.source_commit == COMMIT
    assert receipt.spec.spec_digest != receipt.spec.baseline_arm_digest
    serialized = receipt.to_dict()
    assert serialized["production_influence_enabled"] is False
    assert serialized["claim_scope"] == "EXPERIMENT_EVIDENCE_ONLY"
    assert "C:\\" not in str(serialized)

    with pytest.raises(CodeBenchmarkLedgerContractError, match="registration_id"):
        CodeBenchmarkWorkloadRegistration(
            workload=registration.workload,
            execution_budget=registration.execution_budget,
            registration_id="codebenchwork_wrong",
        )


def test_receipt_requires_two_preregistered_bound_outcomes_and_verified_cleanup() -> None:
    receipt = completed_receipt()
    run = receipt.run
    no_cleanup = CodeHistoricalBenchmarkRun(
        run_id=run.run_id,
        report=run.report,
        baseline_evidence=run.baseline_evidence,
        selected_evidence=run.selected_evidence,
    )
    with pytest.raises(CodeBenchmarkLedgerContractError, match="cleanup receipt"):
        CodeBenchmarkRunReceipt(spec=receipt.spec, run=no_cleanup)

    bad_baseline = _evidence(
        receipt.spec.baseline,
        diff=run.baseline_evidence.workspace_diff_digest,
        cost={"tool_call_count": 2},
    )
    bad_run = CodeHistoricalBenchmarkRun(
        run_id=run.run_id,
        report=run.report,
        baseline_evidence=bad_baseline,
        selected_evidence=run.selected_evidence,
        cleanup_receipt=run.cleanup_receipt,
    )
    with pytest.raises(CodeBenchmarkLedgerContractError, match="cost"):
        CodeBenchmarkRunReceipt(spec=receipt.spec, run=bad_run)

    with pytest.raises(CodeBenchmarkContractError, match="unchanged protected"):
        CodeBenchmarkCleanupReceipt(
            run_id=run.run_id,
            source_before_digest="5" * 64,
            source_after_digest="6" * 64,
            private_bare_removed=True,
            worktrees_removed=True,
            run_root_removed=True,
        )


def test_registration_rejects_extra_or_stronger_isolation_claims() -> None:
    workload = _workload(
        isolation={
            **dict(_workload().isolation),
            "network_enabled": False,
        }
    )
    with pytest.raises(CodeBenchmarkLedgerContractError, match="exactly"):
        CodeBenchmarkWorkloadRegistration(workload=workload)


def test_declared_execution_budget_is_frozen_but_not_misreported_as_runtime_authority() -> None:
    registration = _registration()
    assert registration.execution_budget.max_model_calls == 0
    assert registration.execution_budget.max_tool_calls == 2
    assert registration.execution_budget.budget_digest == registration.execution_budget.budget_digest
    with pytest.raises(CodeBenchmarkLedgerContractError, match="non-negative"):
        CodeBenchmarkExecutionBudget(max_tool_calls=-1)
