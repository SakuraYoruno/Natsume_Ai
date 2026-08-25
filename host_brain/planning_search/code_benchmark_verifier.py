"""Allowlisted subprocess verifier for D19 Code benchmark experiments.

This adapter deliberately reuses the Host verification policy's argv and
workspace validation, but supplies it only a server-owned frozen pytest plan.
The child process is separated from the trusted benchmark adapter process; it
is not an independent trust domain or an OS/container/network sandbox.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from threading import RLock
import time
from typing import Any, Mapping

from host_brain.agent_runtime.verification_policy import (
    VerificationCommandRunner,
    VerificationPolicy,
)
from host_brain.contracts.agent_runtime.verification_contract import VerificationResult, VerificationStatus
from host_brain.core.subprocess_utils import run as _subprocess_run
from host_brain.contracts.planning_search.benchmarking import BenchmarkArm, DomainBenchmarkWorkload
from host_brain.contracts.planning_search.code_benchmark_ledger import CodeBenchmarkExecutionBudget
from host_brain.contracts.planning_search.code_benchmark_models import CodeVerifierResult
from host_brain.contracts.planning_search.code_benchmark_verification_contracts import (
    CodeBenchmarkControlledVerificationBudget,
    CodeBenchmarkControlledVerificationReceipt,
    CodeBenchmarkVerificationCommand,
    CodeBenchmarkVerificationCommandReceipt,
    CodeBenchmarkVerificationContractError,
    CodeBenchmarkVerificationPlan,
)


CODE_BENCHMARK_CONTROLLED_VERIFICATION_ERROR = (
    "CODE_BENCHMARK_CONTROLLED_VERIFICATION_ERROR"
)


class CodeBenchmarkControlledVerificationError(RuntimeError):
    """Raised when a D19 verification boundary fails closed."""

    def __init__(self, detail: str) -> None:
        self.reason_code = CODE_BENCHMARK_CONTROLLED_VERIFICATION_ERROR
        self.detail = str(detail or "controlled Code verification failed")
        super().__init__(f"{self.reason_code}: {self.detail}")


@dataclass(frozen=True, slots=True)
class _RunnerObservation:
    args: tuple[str, ...]
    returncode: int
    duration_ms: int


class _BudgetedPytestRunner(VerificationCommandRunner):
    """Policy-compatible runner with a shared paired-run wall deadline."""

    def __init__(self, *, deadline_ns: int, clock_ns: Any) -> None:
        self._deadline_ns = deadline_ns
        self._clock_ns = clock_ns
        self.observations: list[_RunnerObservation] = []

    def run(
        self,
        args: list[str],
        *,
        cwd: Path,
        timeout_seconds: int,
        env: dict[str, str],
    ) -> subprocess.CompletedProcess[str]:
        before = int(self._clock_ns())
        remaining_ns = self._deadline_ns - before
        if remaining_ns <= 0:
            raise subprocess.TimeoutExpired(args, timeout=0)
        # ``subprocess.run`` accepts a float timeout.  Keeping the remaining
        # duration as a float avoids accidentally granting another whole
        # second after the shared paired-run budget has expired.
        effective_timeout = min(timeout_seconds, remaining_ns / 1_000_000_000)
        if effective_timeout <= 0:
            raise subprocess.TimeoutExpired(args, timeout=0)
        child_env = dict(env)
        child_env.pop("PYTHONPATH", None)
        # Windows' user-site discovery needs APPDATA.  It is not evidence of
        # a hermetic dependency environment; this adapter remains a trusted
        # workspace command boundary and records no host path in its receipt.
        if os.environ.get("APPDATA"):
            child_env["APPDATA"] = os.environ["APPDATA"]
        child_env.update(
            {
                "PYTEST_ADDOPTS": "-p no:cacheprovider",
                "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
                "PYTHONDONTWRITEBYTECODE": "1",
            }
        )
        try:
            completed = _subprocess_run(
                args,
                cwd=str(cwd),
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=effective_timeout,
                env=child_env,
                check=False,
                shell=False,
            )
        except subprocess.TimeoutExpired:
            # The generic policy records the timeout as an ERROR result.  D19
            # subsequently turns that result into a hard experiment failure.
            raise
        after = int(self._clock_ns())
        duration_ms = max(0, (after - before) // 1_000_000)
        self.observations.append(
            _RunnerObservation(
                args=tuple(str(part) for part in args),
                returncode=int(completed.returncode),
                duration_ms=int(duration_ms),
            )
        )
        if after > self._deadline_ns:
            raise subprocess.TimeoutExpired(
                args,
                timeout=effective_timeout,
                output=completed.stdout,
                stderr=completed.stderr,
            )
        return completed


@dataclass(slots=True)
class _ActiveRun:
    run_id: str
    allowed_arm_ids: frozenset[str]
    deadline_ns: int | None = None
    used_test_commands: int = 0
    observed_arms: set[str] = field(default_factory=set)


class ControlledCodeBenchmarkVerifier:
    """D17 verifier implementation with a D19 frozen pytest command plan.

    The adapter is reusable sequentially, but exactly one paired run may be
    active at a time.  It is intentionally not wired into Host composition,
    TaskGraph, AgentExecutor, ModelAttempt or the general durable Budget
    Ledger.
    """

    def __init__(
        self,
        *,
        plan: CodeBenchmarkVerificationPlan,
        execution_budget: CodeBenchmarkExecutionBudget,
        clock_ns: Any = time.monotonic_ns,
    ) -> None:
        if not isinstance(plan, CodeBenchmarkVerificationPlan):
            raise TypeError("plan must be CodeBenchmarkVerificationPlan")
        if not isinstance(execution_budget, CodeBenchmarkExecutionBudget):
            raise TypeError("execution_budget must be CodeBenchmarkExecutionBudget")
        requirements = plan.paired_budget_requirements()
        if (
            execution_budget.max_test_commands < requirements["max_test_commands"]
            or execution_budget.max_wall_time_ms < requirements["max_wall_time_ms"]
        ):
            raise CodeBenchmarkControlledVerificationError(
                "declared paired test-command/wall budget cannot cover the frozen plan"
            )
        self._plan = plan
        self._execution_budget = execution_budget
        self._budget = CodeBenchmarkControlledVerificationBudget(
            max_test_commands=execution_budget.max_test_commands,
            max_wall_time_ms=execution_budget.max_wall_time_ms,
        )
        self._clock_ns = clock_ns
        self._active: _ActiveRun | None = None
        self._lock = RLock()

    @property
    def plan(self) -> CodeBenchmarkVerificationPlan:
        return self._plan

    @property
    def execution_budget(self) -> CodeBenchmarkExecutionBudget:
        return self._execution_budget

    def prepare_run(
        self,
        *,
        run_id: str,
        workload: DomainBenchmarkWorkload,
        baseline: BenchmarkArm,
        selected: BenchmarkArm,
    ) -> None:
        with self._lock:
            if self._active is not None:
                raise CodeBenchmarkControlledVerificationError(
                    "controlled verifier already has an active paired run"
                )
            if not isinstance(workload, DomainBenchmarkWorkload) or workload.domain != "code":
                raise CodeBenchmarkControlledVerificationError(
                    "controlled verifier requires a Code workload"
                )
            if not isinstance(baseline, BenchmarkArm) or not isinstance(selected, BenchmarkArm):
                raise CodeBenchmarkControlledVerificationError(
                    "controlled verifier requires two BenchmarkArm values"
                )
            if baseline.arm_id == selected.arm_id:
                raise CodeBenchmarkControlledVerificationError(
                    "controlled verifier requires distinct paired arms"
                )
            normalized_run_id = _identifier(run_id, "run_id")
            if self._plan.plan_ref not in workload.verifier_refs:
                raise CodeBenchmarkControlledVerificationError(
                    "workload does not bind this controlled verification plan"
                )
            self._active = _ActiveRun(
                run_id=normalized_run_id,
                allowed_arm_ids=frozenset((baseline.arm_id, selected.arm_id)),
            )

    def finish_run(self, *, run_id: str) -> None:
        with self._lock:
            active = self._active
            if active is None:
                return
            if active.run_id != _identifier(run_id, "run_id"):
                raise CodeBenchmarkControlledVerificationError(
                    "controlled verifier finish run id does not match the active run"
                )
            self._active = None

    def verify(self, *, workspace: Any, arm: BenchmarkArm) -> CodeVerifierResult:
        with self._lock:
            return self._verify_locked(workspace=workspace, arm=arm)

    def _verify_locked(self, *, workspace: Any, arm: BenchmarkArm) -> CodeVerifierResult:
        active = self._active
        if active is None:
            raise CodeBenchmarkControlledVerificationError(
                "controlled verifier requires prepare_run before verify"
            )
        if not isinstance(arm, BenchmarkArm):
            raise CodeBenchmarkControlledVerificationError("arm must be BenchmarkArm")
        if arm.arm_id not in active.allowed_arm_ids:
            raise CodeBenchmarkControlledVerificationError(
                "controlled verifier arm is not part of the preregistered pair"
            )
        if arm.arm_id in active.observed_arms:
            raise CodeBenchmarkControlledVerificationError(
                "controlled verifier cannot verify one arm twice"
            )
        now_ns = int(self._clock_ns())
        if active.deadline_ns is None:
            # D19 owns subprocess verification only.  Source cloning and the
            # trusted executor are intentionally outside this budget rather
            # than being misreported as a general Code execution timeout.
            active.deadline_ns = now_ns + self._budget.max_wall_time_ms * 1_000_000
        if now_ns >= active.deadline_ns:
            raise CodeBenchmarkControlledVerificationError(
                "controlled paired verification wall budget is exhausted"
            )
        planned_count = len(self._plan.commands)
        if active.used_test_commands + planned_count > self._budget.max_test_commands:
            raise CodeBenchmarkControlledVerificationError(
                "controlled paired verification test-command budget is exhausted"
            )
        workspace_root = _trusted_workspace_root(workspace)
        # Reserve the frozen command set before the policy can start its first
        # child.  A timeout/error may leave no valid receipt, but it must never
        # make the same arm or test-command budget retryable within this pair.
        active.used_test_commands += planned_count
        active.observed_arms.add(arm.arm_id)
        runner = _BudgetedPytestRunner(
            deadline_ns=active.deadline_ns,
            clock_ns=self._clock_ns,
        )
        policy = VerificationPolicy(runner=runner)
        verification_started_ns = int(self._clock_ns())
        raw_result = policy.run(
            {
                "required": True,
                "commands": [
                    {
                        "argv": list(command.argv),
                        "cwd": "",
                        "timeout_seconds": command.timeout_seconds,
                    }
                    for command in self._plan.commands
                ],
            },
            workspace=workspace_root,
            request_id=f"codebench-{active.run_id}-{arm.arm_id}",
            intent_id=f"codebench.{active.run_id}.{arm.arm_id}",
            work_order_id=f"codebench-{active.run_id}-{arm.arm_id}",
        )
        verification_finished_ns = int(self._clock_ns())
        verification = VerificationResult.from_mapping(raw_result)
        if verification.status not in {VerificationStatus.PASS, VerificationStatus.FAIL}:
            raise CodeBenchmarkControlledVerificationError(
                "controlled command verifier returned a non-terminal policy error"
            )
        checks = tuple(verification.checks)
        if len(checks) != len(self._plan.commands) or len(runner.observations) != len(
            self._plan.commands
        ):
            raise CodeBenchmarkControlledVerificationError(
                "controlled command verifier did not execute the frozen command set"
            )
        if int(self._clock_ns()) > active.deadline_ns:
            raise CodeBenchmarkControlledVerificationError(
                "controlled paired verification wall budget is exhausted"
            )
        receipts = tuple(
            _command_receipt(command=command, check=check, observed=observed)
            for command, check, observed in zip(
                self._plan.commands,
                checks,
                runner.observations,
                strict=True,
            )
        )
        wall_time_ms = max(
            0,
            (verification_finished_ns - verification_started_ns) // 1_000_000,
        )
        try:
            receipt = CodeBenchmarkControlledVerificationReceipt(
                run_id=active.run_id,
                arm_id=arm.arm_id,
                plan=self._plan,
                budget=self._budget,
                commands=receipts,
                success=verification.status is VerificationStatus.PASS,
                verification_wall_time_ms=wall_time_ms,
            )
        except CodeBenchmarkVerificationContractError as exc:
            raise CodeBenchmarkControlledVerificationError(
                "controlled verification could not produce a bounded receipt"
            ) from exc
        return CodeVerifierResult(
            success=receipt.success,
            receipt_ref=receipt.receipt_ref,
            cost=receipt.cost,
            evidence_digest=receipt.evidence_digest,
            controlled_receipt=receipt,
        )


def _trusted_workspace_root(workspace: Any) -> Path:
    root_getter = getattr(workspace, "_trusted_verification_root", None)
    if not callable(root_getter):
        raise CodeBenchmarkControlledVerificationError(
            "controlled verifier requires the trusted benchmark workspace bridge"
        )
    root = root_getter()
    if not isinstance(root, Path) or not root.is_dir():
        raise CodeBenchmarkControlledVerificationError(
            "controlled verifier workspace root is unavailable"
        )
    return root


def _command_receipt(
    *,
    command: CodeBenchmarkVerificationCommand,
    check: Mapping[str, Any],
    observed: _RunnerObservation,
) -> CodeBenchmarkVerificationCommandReceipt:
    # The type is intentionally checked here rather than trusting an arbitrary
    # policy row: the receipt must stay aligned with the immutable plan.
    status = str(check.get("status") or "").strip().upper()
    if status not in {"PASS", "FAIL"}:
        raise CodeBenchmarkControlledVerificationError(
            "controlled verification command did not produce PASS or FAIL"
        )
    raw_argv = check.get("command")
    if not isinstance(raw_argv, list) or not all(isinstance(item, str) for item in raw_argv):
        raise CodeBenchmarkControlledVerificationError(
            "controlled verification policy did not return normalized argv"
        )
    if tuple(raw_argv) != observed.args:
        raise CodeBenchmarkControlledVerificationError(
            "controlled verification policy argv drifted from the subprocess argv"
        )
    canonical_argv = _canonical_executed_pytest_argv(raw_argv)
    if canonical_argv != list(command.argv):
        raise CodeBenchmarkControlledVerificationError(
            "controlled verification subprocess argv differs from the frozen plan"
        )
    exit_code = check.get("exit_code")
    if isinstance(exit_code, bool) or not isinstance(exit_code, int):
        raise CodeBenchmarkControlledVerificationError(
            "controlled verification policy did not return an exit code"
        )
    if int(exit_code) != observed.returncode:
        raise CodeBenchmarkControlledVerificationError(
            "controlled verification command return code drifted"
        )
    stdout = str(check.get("stdout") or "")
    stderr = str(check.get("stderr") or "")
    return CodeBenchmarkVerificationCommandReceipt(
        command_id=command.command_id,
        command_digest=command.command_digest,
        # The policy resolves ``python`` to the host interpreter path.  Verify
        # that concrete executable first, then persist the frozen portable
        # spelling so durable replay is not coupled to one host path.
        executed_argv_digest=_digest(canonical_argv),
        status=status,
        exit_code=int(exit_code),
        stdout_digest=_digest(stdout),
        stderr_digest=_digest(stderr),
        stdout_length=len(stdout.encode("utf-8")),
        stderr_length=len(stderr.encode("utf-8")),
        duration_ms=observed.duration_ms,
    )


def _canonical_executed_pytest_argv(raw_argv: list[str]) -> list[str]:
    """Validate the policy-normalized interpreter and restore frozen argv form."""

    if len(raw_argv) < 4 or raw_argv[1:3] != ["-m", "pytest"]:
        raise CodeBenchmarkControlledVerificationError(
            "controlled verification policy normalized an unexpected command"
        )
    try:
        executable_matches = Path(raw_argv[0]).resolve(strict=True) == Path(
            sys.executable
        ).resolve(strict=True)
    except OSError as exc:
        raise CodeBenchmarkControlledVerificationError(
            "controlled verification interpreter path is unavailable"
        ) from exc
    if not executable_matches:
        raise CodeBenchmarkControlledVerificationError(
            "controlled verification interpreter differs from the current Python runtime"
        )
    return ["python", *raw_argv[1:]]


def _identifier(value: Any, field_name: str) -> str:
    if not isinstance(value, str):
        raise CodeBenchmarkControlledVerificationError(f"{field_name} must be text")
    text = value.strip().lower()
    if not text or len(text) > 120 or any(
        char not in "abcdefghijklmnopqrstuvwxyz0123456789_.-" for char in text
    ) or text[0] not in "abcdefghijklmnopqrstuvwxyz0123456789":
        raise CodeBenchmarkControlledVerificationError(
            f"{field_name} must be a lowercase opaque identifier"
        )
    return text


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


__all__ = (
    "CODE_BENCHMARK_CONTROLLED_VERIFICATION_ERROR",
    "CodeBenchmarkControlledVerificationError",
    "ControlledCodeBenchmarkVerifier",
)
