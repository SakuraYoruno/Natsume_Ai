"""Narrow orchestration for a preregistered D19 Code benchmark experiment.

This is an experiment-only convenience boundary.  It performs the required
registration -> frozen paired run -> clean terminal evidence order, but it has
no Host route, TaskGraph authority, lease/retry worker, promotion authority or
production influence.
"""

from __future__ import annotations

from collections.abc import Sequence

from host_brain.contracts.planning_search.code_benchmark_ledger import (
    CodeBenchmarkRunReceipt,
    CodeBenchmarkRunSpec,
    CodeBenchmarkWorkloadRegistration,
    CodeHistoricalBenchmarkLedger,
)
from host_brain.contracts.planning_search.code_benchmark_models import (
    CodeBenchmarkPathPolicy,
    CodeHistoricalBenchmarkRun,
)
from host_brain.planning_search.code_benchmark_sandbox import (
    CodeBenchmarkArmExecutor,
    CodeHistoricalBenchmarkSandbox,
)
from host_brain.planning_search.code_benchmark_verifier import (
    ControlledCodeBenchmarkVerifier,
)


CODE_BENCHMARK_EXPERIMENT_ERROR = "CODE_BENCHMARK_EXPERIMENT_ERROR"


class CodeBenchmarkExperimentError(RuntimeError):
    """Raised when a D19 experiment cannot publish terminal evidence."""

    def __init__(self, detail: str) -> None:
        self.reason_code = CODE_BENCHMARK_EXPERIMENT_ERROR
        self.detail = str(detail or "Code benchmark experiment failed")
        super().__init__(f"{self.reason_code}: {self.detail}")


class ControlledCodeBenchmarkExperimentService:
    """Run only a fully preregistered, cleanup-gated controlled verification.

    A failed pytest command is a valid terminal experiment observation.  It is
    persisted with the same cleanup and identity guarantees as a passing arm
    so a fixed cohort can measure pass rate without dropping failed tasks.
    """

    def __init__(
        self,
        *,
        sandbox: CodeHistoricalBenchmarkSandbox,
        ledger: CodeHistoricalBenchmarkLedger,
    ) -> None:
        if not isinstance(sandbox, CodeHistoricalBenchmarkSandbox):
            raise TypeError("sandbox must be CodeHistoricalBenchmarkSandbox")
        for method in (
            "register_workload",
            "preregister_run",
            "record_completed_run",
            "get_completed",
        ):
            if not callable(getattr(ledger, method, None)):
                raise TypeError("ledger must implement CodeHistoricalBenchmarkLedger")
        self._sandbox = sandbox
        self._ledger = ledger

    def run_and_record(
        self,
        *,
        registration: CodeBenchmarkWorkloadRegistration,
        spec: CodeBenchmarkRunSpec,
        path_policy: CodeBenchmarkPathPolicy,
        baseline_executor: CodeBenchmarkArmExecutor,
        selected_executor: CodeBenchmarkArmExecutor,
        verifier: ControlledCodeBenchmarkVerifier,
        arm_order: Sequence[str] = ("BASELINE", "SELECTED"),
    ) -> CodeBenchmarkRunReceipt:
        if not isinstance(registration, CodeBenchmarkWorkloadRegistration):
            raise TypeError("registration must be CodeBenchmarkWorkloadRegistration")
        if not isinstance(spec, CodeBenchmarkRunSpec):
            raise TypeError("spec must be CodeBenchmarkRunSpec")
        if not isinstance(path_policy, CodeBenchmarkPathPolicy):
            raise TypeError("path_policy must be CodeBenchmarkPathPolicy")
        if not isinstance(verifier, ControlledCodeBenchmarkVerifier):
            raise TypeError("verifier must be ControlledCodeBenchmarkVerifier")
        plan = registration.controlled_verification_plan
        if plan is None:
            raise CodeBenchmarkExperimentError(
                "D19 experiment requires a preregistered controlled verification plan"
            )
        if (
            spec.registration.registration_digest != registration.registration_digest
            or spec.registration.to_dict() != registration.to_dict()
        ):
            raise CodeBenchmarkExperimentError(
                "run spec must bind the supplied workload registration exactly"
            )
        if (
            verifier.plan.to_dict() != plan.to_dict()
            or verifier.execution_budget.to_dict() != registration.execution_budget.to_dict()
        ):
            raise CodeBenchmarkExperimentError(
                "controlled verifier plan/budget differs from preregistration"
            )
        stored_registration = self._ledger.register_workload(registration)
        bound_spec = CodeBenchmarkRunSpec(
            run_id=spec.run_id,
            registration=stored_registration,
            baseline=spec.baseline,
            selected=spec.selected,
        )
        stored_spec = self._ledger.preregister_run(bound_spec)
        completed = self._ledger.get_completed(stored_spec.run_id)
        if completed is not None:
            if completed.spec.spec_digest != stored_spec.spec_digest:
                raise CodeBenchmarkExperimentError(
                    "existing terminal receipt has a different preregistered spec"
                )
            self._require_controlled_receipts(
                completed.run,
                "existing terminal receipt",
            )
            return completed
        run = self._sandbox.run_paired(
            run_id=stored_spec.run_id,
            workload=stored_registration.workload,
            baseline=stored_spec.baseline,
            selected=stored_spec.selected,
            path_policy=path_policy,
            baseline_executor=baseline_executor,
            selected_executor=selected_executor,
            verifier=verifier,
            arm_order=arm_order,
        )
        self._require_controlled_receipts(run, "paired verification")
        receipt = CodeBenchmarkRunReceipt(spec=stored_spec, run=run)
        return self._ledger.record_completed_run(receipt)

    @staticmethod
    def _require_controlled_receipts(
        run: CodeHistoricalBenchmarkRun,
        context: str,
    ) -> None:
        for role, evidence in (
            ("baseline", run.baseline_evidence),
            ("selected", run.selected_evidence),
        ):
            controlled = evidence.verifier.controlled_receipt
            if controlled is None:
                raise CodeBenchmarkExperimentError(
                    f"{context} lacks a controlled {role} verifier receipt"
                )


__all__ = (
    "CODE_BENCHMARK_EXPERIMENT_ERROR",
    "CodeBenchmarkExperimentError",
    "ControlledCodeBenchmarkExperimentService",
)
