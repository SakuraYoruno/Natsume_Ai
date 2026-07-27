from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import sqlite3
import subprocess

import pytest

from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.persistence.code_benchmark_ledger_repository import (
    CODE_BENCHMARK_DURABLE_DATA_INVALID,
    CodeBenchmarkLedgerRepositoryError,
    SQLiteCodeHistoricalBenchmarkLedger,
)
from host_brain.planning_search import (
    BenchmarkArm,
    DomainBenchmarkReport,
    DomainBenchmarkWorkload,
    SearchBudget,
)
from host_brain.planning_search.code_benchmark_experiment import (
    ControlledCodeBenchmarkExperimentService,
)
from host_brain.planning_search.code_benchmark_ledger import (
    CodeBenchmarkExecutionBudget,
    CodeBenchmarkLedgerContractError,
    CodeBenchmarkRunReceipt,
    CodeBenchmarkRunSpec,
    CodeBenchmarkWorkloadRegistration,
)
from host_brain.planning_search.code_benchmark_models import (
    CODE_HISTORICAL_BENCHMARK_EXECUTOR_TRUST,
    CODE_HISTORICAL_BENCHMARK_MODE,
    CODE_HISTORICAL_BENCHMARK_NETWORK_ISOLATION,
    CodeBenchmarkArmEvidence,
    CodeBenchmarkPathPolicy,
    CodeHistoricalBenchmarkRun,
    CodeVerifierResult,
    benchmark_outcome,
)
from host_brain.planning_search.code_benchmark_sandbox import (
    CodeHistoricalBenchmarkSandbox,
    PrivateBareBenchmarkSource,
)
from host_brain.planning_search.code_benchmark_verification_contracts import (
    CodeBenchmarkVerificationCommand,
    CodeBenchmarkVerificationPlan,
)
from host_brain.planning_search.code_benchmark_verifier import (
    ControlledCodeBenchmarkVerifier,
)


NOW = 2_007_000_000_000


def _git(path: Path, *args: str) -> str:
    completed = subprocess.run(
        ("git", "-C", str(path), *args),
        capture_output=True,
        check=True,
        text=True,
        encoding="utf-8",
    )
    return completed.stdout.strip()


def _db(tmp_path: Path) -> Path:
    path = tmp_path / "code-benchmark.sqlite3"
    result = SQLiteMigrationRunner(db_path=path).apply_migrations(
        request_id="d19-controlled-code-benchmark",
        ts_ms=NOW,
    )
    assert result.success is True, result.as_dict()
    return path


def _source_repo(tmp_path: Path, *, require_baseline: bool = False) -> tuple[Path, str, Path, Path]:
    source = tmp_path / "protected-source"
    source.mkdir()
    _git(source, "init")
    _git(source, "config", "user.email", "benchmark@example.invalid")
    _git(source, "config", "user.name", "benchmark")
    _git(source, "config", "core.autocrlf", "false")
    (source / "src").mkdir()
    (source / "tests").mkdir()
    (source / "src" / "app.py").write_text("value = 'base'\n", encoding="utf-8")
    assertion = "'baseline' in value" if require_baseline else "\"value = '\" in value"
    (source / "tests" / "test_app.py").write_text(
        "from pathlib import Path\n\n"
        "def test_value():\n"
        "    value = Path('src/app.py').read_text(encoding='utf-8')\n"
        f"    assert {assertion}\n",
        encoding="utf-8",
    )
    _git(source, "add", "src/app.py", "tests/test_app.py")
    _git(source, "commit", "-m", "seed")
    private_root = tmp_path / "private-bares"
    worktree_root = tmp_path / "benchmark-worktrees"
    private_root.mkdir()
    worktree_root.mkdir()
    return source, _git(source, "rev-parse", "HEAD"), private_root, worktree_root


def _arm(name: str) -> BenchmarkArm:
    return BenchmarkArm(
        arm_id=name,
        selected_candidate_id=f"{name}-candidate",
        selected_path_candidate_ids=(f"{name}-candidate", f"{name}-patch"),
        trace_digest="a" * 64,
        counters={"candidates": 2, "expansions": 1},
        virtual_state_schema="natsume.planning-search.virtual-state.v1",
        virtual_state_digest="b" * 64,
    )


def _plan() -> CodeBenchmarkVerificationPlan:
    return CodeBenchmarkVerificationPlan(
        commands=(
            CodeBenchmarkVerificationCommand(
                command_id="unit",
                argv=("python", "-m", "pytest", "-q", "tests/test_app.py"),
                timeout_seconds=20,
            ),
        )
    )


class _Writer:
    def execute(self, *, workspace, arm):  # noqa: ANN001, ANN201
        workspace.write_text("src/app.py", f"value = {arm.arm_id!r}\n")
        return {}


class _ExplodingExecutor:
    def execute(self, *, workspace, arm):  # noqa: ANN001, ANN201
        del workspace, arm
        raise AssertionError("existing terminal receipt must prevent re-execution")


def _evidence_with_controlled_receipt(
    evidence: CodeBenchmarkArmEvidence,
    *,
    controlled,
) -> CodeBenchmarkArmEvidence:
    verifier = CodeVerifierResult(
        success=controlled.success,
        receipt_ref=controlled.receipt_ref,
        cost=controlled.cost,
        evidence_digest=controlled.evidence_digest,
        controlled_receipt=controlled,
    )
    return replace(evidence, verifier=verifier, receipt_ref="")


def _run_with_evidence(
    receipt: CodeBenchmarkRunReceipt,
    *,
    baseline: CodeBenchmarkArmEvidence,
    selected: CodeBenchmarkArmEvidence,
) -> CodeHistoricalBenchmarkRun:
    report = DomainBenchmarkReport(
        workload=receipt.spec.registration.workload,
        baseline=receipt.spec.baseline,
        selected=receipt.spec.selected,
        baseline_outcome=benchmark_outcome(
            arm=receipt.spec.baseline,
            verifier=baseline.verifier,
            latency_ms=1,
            workspace_diff_digest=baseline.workspace_diff_digest,
        ),
        selected_outcome=benchmark_outcome(
            arm=receipt.spec.selected,
            verifier=selected.verifier,
            latency_ms=1,
            workspace_diff_digest=selected.workspace_diff_digest,
        ),
    )
    return CodeHistoricalBenchmarkRun(
        run_id=receipt.run.run_id,
        report=report,
        baseline_evidence=baseline,
        selected_evidence=selected,
        cleanup_receipt=receipt.run.cleanup_receipt,
    )


def _experiment_parts(tmp_path: Path, *, require_baseline: bool = False):  # noqa: ANN201
    source, commit, private_root, worktree_root = _source_repo(
        tmp_path,
        require_baseline=require_baseline,
    )
    source_ref = "snapshot://code/d19/integration"
    policy = CodeBenchmarkPathPolicy(allowed_paths=("src",), max_changed_files=1)
    plan = _plan()
    budget = CodeBenchmarkExecutionBudget(
        max_test_commands=2,
        max_wall_time_ms=60_000,
    )
    workload = DomainBenchmarkWorkload(
        workload_id="code-d19-integration",
        domain="code",
        workload_ref="benchmark://code/d19/integration",
        baseline_ref="benchmark://code/d19/baseline",
        verifier_refs=(plan.plan_ref,),
        isolation={
            "sandbox_mode": CODE_HISTORICAL_BENCHMARK_MODE,
            "source_ref": source_ref,
            "source_commit": commit,
            "path_policy_digest": policy.digest,
            "executor_trust": CODE_HISTORICAL_BENCHMARK_EXECUTOR_TRUST,
            "network_isolation": CODE_HISTORICAL_BENCHMARK_NETWORK_ISOLATION,
        },
        budget=SearchBudget(max_candidates=4, max_expansions=2, max_rollouts=1),
        cohort_key="code-d19-integration",
    )
    registration = CodeBenchmarkWorkloadRegistration(
        workload=workload,
        execution_budget=budget,
        controlled_verification_plan=plan,
    )
    spec = CodeBenchmarkRunSpec(
        run_id="run-d19-integration",
        registration=registration,
        baseline=_arm("baseline"),
        selected=_arm("selected"),
    )
    sandbox = CodeHistoricalBenchmarkSandbox(
        source=PrivateBareBenchmarkSource(
            source_repository=source,
            private_bare_root=private_root,
            worktree_root=worktree_root,
            protected_roots=(source,),
            source_ref=source_ref,
            source_commit=commit,
        )
    )
    return (
        source,
        private_root,
        worktree_root,
        policy,
        plan,
        budget,
        registration,
        spec,
        sandbox,
    )


def test_controlled_experiment_persists_path_free_receipt_and_replays_after_restart(
    tmp_path: Path,
) -> None:
    (
        source,
        private_root,
        worktree_root,
        policy,
        plan,
        budget,
        registration,
        spec,
        sandbox,
    ) = _experiment_parts(tmp_path)
    path = _db(tmp_path)
    ledger = SQLiteCodeHistoricalBenchmarkLedger(path, clock=lambda: NOW + 1)
    service = ControlledCodeBenchmarkExperimentService(sandbox=sandbox, ledger=ledger)
    receipt = service.run_and_record(
        registration=registration,
        spec=spec,
        path_policy=policy,
        baseline_executor=_Writer(),
        selected_executor=_Writer(),
        verifier=ControlledCodeBenchmarkVerifier(plan=plan, execution_budget=budget),
    )

    serialized = json.dumps(receipt.to_dict(), ensure_ascii=False, sort_keys=True)
    assert str(source) not in serialized
    assert str(private_root) not in serialized
    assert str(worktree_root) not in serialized
    assert receipt.run.baseline_evidence.verifier.controlled_receipt is not None
    assert receipt.run.selected_evidence.verifier.controlled_receipt is not None
    assert list(private_root.iterdir()) == []
    assert list(worktree_root.iterdir()) == []

    restarted = SQLiteCodeHistoricalBenchmarkLedger(path, clock=lambda: NOW + 2)
    replayed = restarted.get_completed(spec.run_id)
    assert replayed is not None
    assert replayed.to_dict() == receipt.to_dict()

    # Exact terminal evidence is idempotent: a retry does not reopen worktrees
    # or execute either arm again.
    returned = ControlledCodeBenchmarkExperimentService(
        sandbox=sandbox,
        ledger=restarted,
    ).run_and_record(
        registration=registration,
        spec=spec,
        path_policy=policy,
        baseline_executor=_ExplodingExecutor(),
        selected_executor=_ExplodingExecutor(),
        verifier=ControlledCodeBenchmarkVerifier(plan=plan, execution_budget=budget),
    )
    assert returned.to_dict() == receipt.to_dict()

    baseline_controlled = receipt.run.baseline_evidence.verifier.controlled_receipt
    selected_controlled = receipt.run.selected_evidence.verifier.controlled_receipt
    assert baseline_controlled is not None
    assert selected_controlled is not None

    wrong_arm = _evidence_with_controlled_receipt(
        receipt.run.baseline_evidence,
        controlled=replace(baseline_controlled, arm_id="wrong-arm", receipt_ref=""),
    )
    with pytest.raises(CodeBenchmarkLedgerContractError, match="run, arm, plan or budget"):
        CodeBenchmarkRunReceipt(
            spec=receipt.spec,
            run=_run_with_evidence(
                receipt,
                baseline=wrong_arm,
                selected=receipt.run.selected_evidence,
            ),
        )

    failed_command = replace(
        baseline_controlled.commands[0],
        status="FAIL",
        exit_code=1,
    )
    failed_baseline = _evidence_with_controlled_receipt(
        receipt.run.baseline_evidence,
        controlled=replace(
            baseline_controlled,
            commands=(failed_command,),
            success=False,
            receipt_ref="",
        ),
    )
    failed_terminal = CodeBenchmarkRunReceipt(
        spec=receipt.spec,
        run=_run_with_evidence(
            receipt,
            baseline=failed_baseline,
            selected=receipt.run.selected_evidence,
        ),
    )
    assert failed_terminal.run.report.baseline_outcome.success is False
    assert failed_terminal.status == "COMPLETED_CLEAN"
    assert failed_terminal.production_influence_enabled is False

    over_budget_baseline = _evidence_with_controlled_receipt(
        receipt.run.baseline_evidence,
        controlled=replace(
            baseline_controlled,
            verification_wall_time_ms=40_000,
            receipt_ref="",
        ),
    )
    over_budget_selected = _evidence_with_controlled_receipt(
        receipt.run.selected_evidence,
        controlled=replace(
            selected_controlled,
            verification_wall_time_ms=40_000,
            receipt_ref="",
        ),
    )
    with pytest.raises(CodeBenchmarkLedgerContractError, match="paired budget"):
        CodeBenchmarkRunReceipt(
            spec=receipt.spec,
            run=_run_with_evidence(
                receipt,
                baseline=over_budget_baseline,
                selected=over_budget_selected,
            ),
        )

    # Simulate storage tampering only after bypassing the append-only trigger.
    # The restart reader must still reject a PASS receipt whose command exited
    # nonzero before it can become a returned terminal experiment record.
    with sqlite3.connect(path) as conn:
        conn.execute("DROP TRIGGER code_benchmark_receipt_no_update")
        row = conn.execute(
            "SELECT receipt_json FROM planning_search_code_benchmark_receipt "
            "WHERE run_id = ?",
            (spec.run_id,),
        ).fetchone()
        assert row is not None
        payload = json.loads(str(row[0]))
        payload["run"]["baseline_evidence"]["verifier"]["controlled_receipt"]["commands"][0][
            "exit_code"
        ] = 7
        conn.execute(
            "UPDATE planning_search_code_benchmark_receipt SET receipt_json = ? WHERE run_id = ?",
            (json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")), spec.run_id),
        )

    tampered_restart = SQLiteCodeHistoricalBenchmarkLedger(
        path,
        clock=lambda: NOW + 3,
        verify_schema=False,
    )
    with pytest.raises(CodeBenchmarkLedgerRepositoryError) as tampered:
        tampered_restart.get_completed(spec.run_id)
    assert tampered.value.reason_code == CODE_BENCHMARK_DURABLE_DATA_INVALID


def test_failed_controlled_verification_records_cleanup_gated_terminal_observation(
    tmp_path: Path,
) -> None:
    (
        _source,
        private_root,
        worktree_root,
        policy,
        plan,
        budget,
        registration,
        spec,
        sandbox,
    ) = _experiment_parts(tmp_path, require_baseline=True)
    path = _db(tmp_path)
    ledger = SQLiteCodeHistoricalBenchmarkLedger(path, clock=lambda: NOW + 1)
    service = ControlledCodeBenchmarkExperimentService(sandbox=sandbox, ledger=ledger)

    receipt = service.run_and_record(
        registration=registration,
        spec=spec,
        path_policy=policy,
        baseline_executor=_Writer(),
        selected_executor=_Writer(),
        verifier=ControlledCodeBenchmarkVerifier(plan=plan, execution_budget=budget),
    )

    assert receipt.run.report.baseline_outcome.success is True
    assert receipt.run.report.selected_outcome.success is False
    assert ledger.get_completed(spec.run_id).receipt_ref == receipt.receipt_ref
    assert list(private_root.iterdir()) == []
    assert list(worktree_root.iterdir()) == []
