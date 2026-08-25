from __future__ import annotations

import json
from pathlib import Path
import subprocess

import pytest

import host_brain.planning_search.code_benchmark_verifier as controlled_verifier_module
from host_brain.contracts.planning_search.benchmarking import BenchmarkArm, DomainBenchmarkWorkload
from host_brain.contracts.planning_search.contracts import SearchBudget
from host_brain.contracts.planning_search.code_benchmark_ledger import (
    CodeBenchmarkExecutionBudget,
    CodeBenchmarkLedgerContractError,
    CodeBenchmarkWorkloadRegistration,
)
from host_brain.contracts.planning_search.code_benchmark_models import (
    CODE_HISTORICAL_BENCHMARK_EXECUTOR_TRUST,
    CODE_HISTORICAL_BENCHMARK_MODE,
    CODE_HISTORICAL_BENCHMARK_NETWORK_ISOLATION,
    CodeBenchmarkPathPolicy,
    CodeVerifierResult,
)
from host_brain.planning_search.code_benchmark_sandbox import (
    CodeHistoricalBenchmarkSandbox,
    PrivateBareBenchmarkSource,
)
from host_brain.contracts.planning_search.code_benchmark_verification_contracts import (
    CodeBenchmarkVerificationCommand,
    CodeBenchmarkVerificationCommandReceipt,
    CodeBenchmarkVerificationContractError,
    CodeBenchmarkVerificationPlan,
)
from host_brain.planning_search.code_benchmark_verifier import (
    CodeBenchmarkControlledVerificationError,
    ControlledCodeBenchmarkVerifier,
)


def _git(path: Path, *args: str) -> str:
    completed = subprocess.run(
        ("git", "-C", str(path), *args),
        capture_output=True,
        check=True,
        text=True,
        encoding="utf-8",
    )
    return completed.stdout.strip()


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
    expected = "baseline" if require_baseline else ""
    (source / "tests" / "test_app.py").write_text(
        "from pathlib import Path\n\n"
        "def test_value():\n"
        "    value = Path('src/app.py').read_text(encoding='utf-8')\n"
        f"    assert {expected!r} in value\n" if require_baseline else
        "from pathlib import Path\n\n"
        "def test_value():\n"
        "    value = Path('src/app.py').read_text(encoding='utf-8')\n"
        "    assert \"value = '\" in value\n",
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


def _workload(
    *, source_ref: str, commit: str, policy: CodeBenchmarkPathPolicy, plan: CodeBenchmarkVerificationPlan
) -> DomainBenchmarkWorkload:
    return DomainBenchmarkWorkload(
        workload_id="code-d19-unit",
        domain="code",
        workload_ref="benchmark://code/d19/unit",
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
        cohort_key="code-d19-unit",
    )


class _Writer:
    def execute(self, *, workspace, arm):  # noqa: ANN001, ANN201
        workspace.write_text("src/app.py", f"value = {arm.arm_id!r}\n")
        return {}


def _sandbox(tmp_path: Path, *, require_baseline: bool = False):  # noqa: ANN201
    source, commit, private_root, worktree_root = _source_repo(
        tmp_path,
        require_baseline=require_baseline,
    )
    source_ref = "snapshot://code/d19/unit"
    policy = CodeBenchmarkPathPolicy(allowed_paths=("src",), max_changed_files=1)
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
    return sandbox, source_ref, commit, policy, source, private_root, worktree_root


def test_controlled_plan_rejects_shells_raw_strings_and_workspace_escapes() -> None:
    invalid_argv = (
        "python -m pytest -q tests/test_app.py",
        ("cmd", "/c", "pytest", "-q", "tests/test_app.py"),
        ("python", "-c", "import os", "-q", "tests/test_app.py"),
        ("python", "-m", "pytest", "-q", "../tests/test_app.py"),
        ("python", "-m", "pytest", "-q", "@response.txt"),
        ("python", "-m", "pytest", "-q", r"C:\unsafe\test_app.py"),
    )
    for index, argv in enumerate(invalid_argv):
        with pytest.raises(CodeBenchmarkVerificationContractError):
            CodeBenchmarkVerificationCommand(
                command_id=f"invalid-{index}",
                argv=argv,  # type: ignore[arg-type]
            )


def test_legacy_verifier_result_serialization_stays_four_fields() -> None:
    result = CodeVerifierResult(
        success=True,
        receipt_ref="receipt://code/d19/legacy",
        cost={"tool_call_count": 1},
        evidence_digest="a" * 64,
    )
    assert result.to_dict() == {
        "success": True,
        "receipt_ref": "receipt://code/d19/legacy",
        "cost": {"tool_call_count": 1},
        "evidence_digest": "a" * 64,
    }


def test_controlled_command_receipt_rejects_status_exit_code_mismatch() -> None:
    with pytest.raises(CodeBenchmarkVerificationContractError, match="status must match"):
        CodeBenchmarkVerificationCommandReceipt(
            command_id="unit",
            command_digest="a" * 64,
            executed_argv_digest="b" * 64,
            status="PASS",
            exit_code=1,
            stdout_digest="c" * 64,
            stderr_digest="d" * 64,
            stdout_length=0,
            stderr_length=0,
            duration_ms=0,
        )


def test_registration_rejects_plan_that_exceeds_paired_declared_limits(tmp_path: Path) -> None:
    sandbox, source_ref, commit, policy, _source, _private, _worktrees = _sandbox(tmp_path)
    del sandbox
    plan = _plan()
    workload = _workload(
        source_ref=source_ref,
        commit=commit,
        policy=policy,
        plan=plan,
    )
    with pytest.raises(CodeBenchmarkLedgerContractError, match="max_test_commands"):
        CodeBenchmarkWorkloadRegistration(
            workload=workload,
            execution_budget=CodeBenchmarkExecutionBudget(
                max_test_commands=1,
                max_wall_time_ms=60_000,
            ),
            controlled_verification_plan=plan,
        )


def test_controlled_verifier_rejects_an_arm_outside_its_preregistered_pair(
    tmp_path: Path,
) -> None:
    sandbox, source_ref, commit, policy, _source, _private, _worktrees = _sandbox(tmp_path)
    del sandbox
    plan = _plan()
    workload = _workload(
        source_ref=source_ref,
        commit=commit,
        policy=policy,
        plan=plan,
    )
    verifier = ControlledCodeBenchmarkVerifier(
        plan=plan,
        execution_budget=CodeBenchmarkExecutionBudget(
            max_test_commands=2,
            max_wall_time_ms=60_000,
        ),
    )
    baseline = _arm("baseline")
    selected = _arm("selected")
    verifier.prepare_run(
        run_id="run-d19-pair-bound",
        workload=workload,
        baseline=baseline,
        selected=selected,
    )
    try:
        with pytest.raises(CodeBenchmarkControlledVerificationError, match="preregistered pair"):
            verifier.verify(workspace=object(), arm=_arm("outside"))
    finally:
        verifier.finish_run(run_id="run-d19-pair-bound")


def test_timed_out_child_consumes_its_arm_and_frozen_command_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sandbox, source_ref, commit, policy, _source, _private, _worktrees = _sandbox(tmp_path)
    del sandbox
    test_file = tmp_path / "tests" / "test_app.py"
    test_file.parent.mkdir()
    test_file.write_text("def test_placeholder():\n    assert True\n", encoding="utf-8")
    plan = _plan()
    workload = _workload(
        source_ref=source_ref,
        commit=commit,
        policy=policy,
        plan=plan,
    )
    verifier = ControlledCodeBenchmarkVerifier(
        plan=plan,
        execution_budget=CodeBenchmarkExecutionBudget(
            max_test_commands=2,
            max_wall_time_ms=60_000,
        ),
    )
    baseline = _arm("baseline")
    selected = _arm("selected")
    verifier.prepare_run(
        run_id="run-d19-timeout-reservation",
        workload=workload,
        baseline=baseline,
        selected=selected,
    )
    calls = 0

    def _timeout(args, **kwargs):  # noqa: ANN001, ANN202
        nonlocal calls
        del kwargs
        calls += 1
        raise subprocess.TimeoutExpired(args, timeout=0.001)

    monkeypatch.setattr(controlled_verifier_module, "_subprocess_run", _timeout)

    class _TrustedWorkspace:
        def _trusted_verification_root(self) -> Path:
            return tmp_path

    try:
        with pytest.raises(
            CodeBenchmarkControlledVerificationError,
            match="non-terminal policy error",
        ):
            verifier.verify(workspace=_TrustedWorkspace(), arm=baseline)
        with pytest.raises(
            CodeBenchmarkControlledVerificationError,
            match="cannot verify one arm twice",
        ):
            verifier.verify(workspace=_TrustedWorkspace(), arm=baseline)
    finally:
        verifier.finish_run(run_id="run-d19-timeout-reservation")

    assert calls == 1


def test_real_controlled_pytest_receipt_is_bound_and_cleanup_stays_clean(
    tmp_path: Path,
) -> None:
    sandbox, source_ref, commit, policy, source, private_root, worktree_root = _sandbox(tmp_path)
    plan = _plan()
    workload = _workload(
        source_ref=source_ref,
        commit=commit,
        policy=policy,
        plan=plan,
    )
    budget = CodeBenchmarkExecutionBudget(
        max_test_commands=2,
        max_wall_time_ms=60_000,
    )
    verifier = ControlledCodeBenchmarkVerifier(plan=plan, execution_budget=budget)

    run = sandbox.run_paired(
        run_id="run-d19-controlled",
        workload=workload,
        baseline=_arm("baseline"),
        selected=_arm("selected"),
        path_policy=policy,
        baseline_executor=_Writer(),
        selected_executor=_Writer(),
        verifier=verifier,
    )

    for evidence in (run.baseline_evidence, run.selected_evidence):
        controlled = evidence.verifier.controlled_receipt
        assert controlled is not None
        assert controlled.plan.plan_ref == plan.plan_ref
        assert controlled.success is True
        assert dict(evidence.verifier.cost)["test_command_count"] == 1
        assert "verification_wall_time_ms" in evidence.verifier.cost
        assert evidence.verifier.receipt_ref == controlled.receipt_ref
    serialized = json.dumps(run.to_dict(), ensure_ascii=False, sort_keys=True)
    assert str(source) not in serialized
    assert str(private_root) not in serialized
    assert str(worktree_root) not in serialized
    assert list(private_root.iterdir()) == []
    assert list(worktree_root.iterdir()) == []
    assert _git(source, "status", "--porcelain=v1", "--untracked-files=no") == ""
