from __future__ import annotations

import pytest

from host_brain.contracts.planning_search.benchmarking import (
    BenchmarkArm,
    BenchmarkArmOutcome,
    DomainBenchmarkContractError,
    DomainBenchmarkReport,
    DomainBenchmarkWorkload,
)
from host_brain.contracts.planning_search.contracts import SearchBudget


def _workload() -> DomainBenchmarkWorkload:
    return DomainBenchmarkWorkload(
        workload_id="historic-fix-001",
        domain="code",
        workload_ref="artifact://benchmarks/workloads/historic-fix-001",
        baseline_ref="artifact://benchmarks/baselines/greedy-l1",
        verifier_refs=("artifact://benchmarks/verifiers/unit-and-integration",),
        isolation={"kind": "git_worktree", "main_workspace_access": "forbidden"},
        budget=SearchBudget(max_candidates=8, max_expansions=4, max_rollouts=2),
        cohort_key="code-history-v1",
    )


def _arm(name: str) -> BenchmarkArm:
    return BenchmarkArm(
        arm_id=name,
        selected_candidate_id=f"{name}-inspect",
        selected_path_candidate_ids=(f"{name}-inspect", f"{name}-verify"),
        trace_digest="a" * 64,
        counters={"candidates": 2, "expansions": 1, "transitions": 1, "rollouts": 0},
        virtual_state_schema="natsume.planning-search.virtual-state.v1",
        virtual_state_digest="b" * 64,
    )


def test_benchmark_contract_binds_real_workload_baseline_verifier_budget_and_isolation() -> None:
    workload = _workload()
    baseline = _arm("baseline")
    selected = _arm("selected")
    report = DomainBenchmarkReport(
        workload=workload,
        baseline=baseline,
        selected=selected,
        baseline_outcome=BenchmarkArmOutcome(
            arm_id="baseline",
            verifier_receipt_refs=("receipt://benchmark/baseline",),
            success=False,
            latency_ms=123,
            cost={"token_count": 100},
            workspace_diff_digest="c" * 64,
            executed=True,
        ),
        selected_outcome=BenchmarkArmOutcome(
            arm_id="selected",
            verifier_receipt_refs=("receipt://benchmark/selected",),
            success=True,
            latency_ms=145,
            cost={"token_count": 120},
            workspace_diff_digest="d" * 64,
            executed=True,
        ),
    )

    assert workload.workload_digest
    assert report.production_influence_enabled is False
    assert report.to_dict()["mode"] == "SHADOW_ONLY"
    assert report.to_dict()["workload"]["isolation"]["main_workspace_access"] == "forbidden"


def test_benchmark_workload_and_arm_records_are_detached_from_mutable_inputs() -> None:
    isolation = {"kind": "git_worktree", "nested": {"paths": ["src"]}}
    counters = {"candidates": 2}
    workload = DomainBenchmarkWorkload(
        workload_id="historic-fix-immutable",
        domain="code",
        workload_ref="artifact://benchmarks/workloads/immutable",
        baseline_ref="artifact://benchmarks/baselines/greedy-l1",
        verifier_refs=("artifact://benchmarks/verifiers/unit",),
        isolation=isolation,
        budget=SearchBudget(max_candidates=2),
        cohort_key="code-history-v1",
    )
    arm = BenchmarkArm(
        arm_id="baseline",
        selected_candidate_id="inspect",
        selected_path_candidate_ids=("inspect",),
        trace_digest="a" * 64,
        counters=counters,
    )
    isolation["nested"]["paths"].append("outside")
    counters["candidates"] = 99

    assert workload.to_dict()["isolation"]["nested"]["paths"] == ["src"]
    assert arm.to_dict()["counters"] == {"candidates": 2}
    with pytest.raises(TypeError):
        workload.isolation["kind"] = "tampered"  # type: ignore[index]


def test_benchmark_contract_rejects_production_influence_and_unverified_execution() -> None:
    workload = _workload()
    baseline = _arm("baseline")
    selected = _arm("selected")

    with pytest.raises(DomainBenchmarkContractError):
        DomainBenchmarkReport(
            workload=workload,
            baseline=baseline,
            selected=selected,
            production_influence_enabled=True,
        )
    with pytest.raises(DomainBenchmarkContractError):
        BenchmarkArmOutcome(
            arm_id="baseline",
            verifier_receipt_refs=(),
            success=True,
            latency_ms=1,
            cost={},
            executed=True,
        )
