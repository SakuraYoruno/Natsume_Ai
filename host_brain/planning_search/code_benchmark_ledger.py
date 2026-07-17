"""Immutable contracts for cleanup-gated historical Code benchmark evidence.

The D17 worktree adapter supplies a trusted-process experiment result.  This
module makes that result pre-registerable and durable-friendly without turning
it into a production planning, model, tool, test, or promotion authority.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any, Mapping, Protocol

from host_brain.planning_search.benchmarking import (
    BenchmarkArm,
    BenchmarkArmOutcome,
    DomainBenchmarkWorkload,
)
from host_brain.planning_search.code_benchmark_models import (
    CODE_BENCHMARK_CLEANUP_STATUS,
    CODE_HISTORICAL_BENCHMARK_EXECUTOR_TRUST,
    CODE_HISTORICAL_BENCHMARK_MODE,
    CODE_HISTORICAL_BENCHMARK_NETWORK_ISOLATION,
    CodeBenchmarkArmEvidence,
    CodeHistoricalBenchmarkRun,
)


CODE_BENCHMARK_LEDGER_SCHEMA = "natsume.code-historical-benchmark-ledger.v1"
CODE_BENCHMARK_RECEIPT_STATUS = "COMPLETED_CLEAN"
CODE_BENCHMARK_EXPERIMENT_SCOPE = "EXPERIMENT_EVIDENCE_ONLY"

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,119}$")
_COMMIT_RE = re.compile(r"^[0-9a-f]{40,64}$")
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_GOVERNED_REF_RE = re.compile(
    r"^[a-z][a-z0-9+.-]{0,63}://[a-z0-9][a-z0-9._/-]{0,1018}$"
)
_ISOLATION_KEYS = frozenset(
    {
        "sandbox_mode",
        "source_ref",
        "source_commit",
        "path_policy_digest",
        "executor_trust",
        "network_isolation",
    }
)


class CodeBenchmarkLedgerContractError(ValueError):
    """Raised when a Code benchmark cannot become durable experiment evidence."""


@dataclass(frozen=True, slots=True)
class CodeBenchmarkExecutionBudget:
    """Frozen declared budget for a future controlled benchmark executor.

    D18 stores this boundary with the workload so a later executor cannot
    silently change the experiment contract.  It is deliberately *not* a
    model/tool/test runtime gate: D17 only has trusted in-process adapters and
    self-reported counters.  Non-zero fields therefore never imply that those
    actions are authorized or canonically charged in this release.
    """

    max_model_calls: int = 0
    max_provider_attempts: int = 0
    max_tool_calls: int = 0
    max_test_commands: int = 0
    max_wall_time_ms: int = 0
    max_token_count: int = 0
    max_model_cost_micros: int = 0

    def __post_init__(self) -> None:
        for field_name in (
            "max_model_calls",
            "max_provider_attempts",
            "max_tool_calls",
            "max_test_commands",
            "max_wall_time_ms",
            "max_token_count",
            "max_model_cost_micros",
        ):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise CodeBenchmarkLedgerContractError(
                    f"{field_name} must be a non-negative integer"
                )
            object.__setattr__(self, field_name, int(value))

    @property
    def budget_digest(self) -> str:
        return _digest(self.to_dict())

    def to_dict(self) -> dict[str, int]:
        return {
            "max_model_calls": self.max_model_calls,
            "max_provider_attempts": self.max_provider_attempts,
            "max_tool_calls": self.max_tool_calls,
            "max_test_commands": self.max_test_commands,
            "max_wall_time_ms": self.max_wall_time_ms,
            "max_token_count": self.max_token_count,
            "max_model_cost_micros": self.max_model_cost_micros,
        }


@dataclass(frozen=True, slots=True)
class CodeBenchmarkWorkloadRegistration:
    """Pre-register one exact Code workload before either arm executes."""

    workload: DomainBenchmarkWorkload
    execution_budget: CodeBenchmarkExecutionBudget = CodeBenchmarkExecutionBudget()
    registration_id: str = ""
    mode: str = CODE_HISTORICAL_BENCHMARK_MODE
    production_influence_enabled: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.workload, DomainBenchmarkWorkload):
            raise CodeBenchmarkLedgerContractError("workload must be DomainBenchmarkWorkload")
        if self.workload.domain != "code":
            raise CodeBenchmarkLedgerContractError("registration requires the code domain")
        if not isinstance(self.execution_budget, CodeBenchmarkExecutionBudget):
            raise CodeBenchmarkLedgerContractError(
                "execution_budget must be CodeBenchmarkExecutionBudget"
            )
        isolation = _validated_code_isolation(self.workload)
        if str(self.mode or "").strip().upper() != CODE_HISTORICAL_BENCHMARK_MODE:
            raise CodeBenchmarkLedgerContractError(
                "registration must remain trusted-process-only"
            )
        if self.production_influence_enabled:
            raise CodeBenchmarkLedgerContractError(
                "benchmark registration cannot enable production influence"
            )
        identity = {
            "schema": CODE_BENCHMARK_LEDGER_SCHEMA,
            "workload": self.workload.to_dict(),
            "execution_budget": self.execution_budget.to_dict(),
            "isolation": isolation,
            "mode": CODE_HISTORICAL_BENCHMARK_MODE,
            "production_influence_enabled": False,
        }
        digest = _digest(identity)
        expected_id = f"codebenchwork_{digest[:32]}"
        supplied_id = str(self.registration_id or "").strip().lower()
        if supplied_id and supplied_id != expected_id:
            raise CodeBenchmarkLedgerContractError(
                "registration_id does not bind workload identity"
            )
        object.__setattr__(self, "registration_id", expected_id)
        object.__setattr__(self, "mode", CODE_HISTORICAL_BENCHMARK_MODE)
        object.__setattr__(self, "production_influence_enabled", False)

    @property
    def registration_digest(self) -> str:
        return _digest(
            {
                "schema": CODE_BENCHMARK_LEDGER_SCHEMA,
                "workload": self.workload.to_dict(),
                "execution_budget": self.execution_budget.to_dict(),
                "isolation": self.isolation,
                "mode": CODE_HISTORICAL_BENCHMARK_MODE,
                "production_influence_enabled": False,
            }
        )

    @property
    def isolation(self) -> dict[str, str]:
        return _validated_code_isolation(self.workload)

    @property
    def source_ref(self) -> str:
        return self.isolation["source_ref"]

    @property
    def source_commit(self) -> str:
        return self.isolation["source_commit"]

    @property
    def path_policy_digest(self) -> str:
        return self.isolation["path_policy_digest"]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": CODE_BENCHMARK_LEDGER_SCHEMA,
            "registration_id": self.registration_id,
            "registration_digest": self.registration_digest,
            "workload": self.workload.to_dict(),
            "execution_budget": self.execution_budget.to_dict(),
            "isolation": self.isolation,
            "mode": CODE_HISTORICAL_BENCHMARK_MODE,
            "production_influence_enabled": False,
        }


@dataclass(frozen=True, slots=True)
class CodeBenchmarkRunSpec:
    """Pre-register exactly two immutable candidate paths for one workload."""

    run_id: str
    registration: CodeBenchmarkWorkloadRegistration
    baseline: BenchmarkArm
    selected: BenchmarkArm
    mode: str = CODE_HISTORICAL_BENCHMARK_MODE
    production_influence_enabled: bool = False

    def __post_init__(self) -> None:
        run_id = _identifier(self.run_id, "run_id")
        if not isinstance(self.registration, CodeBenchmarkWorkloadRegistration):
            raise CodeBenchmarkLedgerContractError("registration is invalid")
        if not isinstance(self.baseline, BenchmarkArm) or not isinstance(self.selected, BenchmarkArm):
            raise CodeBenchmarkLedgerContractError("baseline and selected must be BenchmarkArm")
        if self.baseline.arm_id == self.selected.arm_id:
            raise CodeBenchmarkLedgerContractError("paired arms must have different arm ids")
        if (
            self.baseline.virtual_state_schema != self.selected.virtual_state_schema
            or self.baseline.virtual_state_digest != self.selected.virtual_state_digest
        ):
            raise CodeBenchmarkLedgerContractError(
                "paired arms must bind the same virtual-state provenance"
            )
        if str(self.mode or "").strip().upper() != CODE_HISTORICAL_BENCHMARK_MODE:
            raise CodeBenchmarkLedgerContractError(
                "run spec must remain trusted-process-only"
            )
        if self.production_influence_enabled:
            raise CodeBenchmarkLedgerContractError(
                "run spec cannot enable production influence"
            )
        object.__setattr__(self, "run_id", run_id)
        object.__setattr__(self, "mode", CODE_HISTORICAL_BENCHMARK_MODE)
        object.__setattr__(self, "production_influence_enabled", False)

    @property
    def baseline_arm_digest(self) -> str:
        return _digest(self.baseline.to_dict())

    @property
    def selected_arm_digest(self) -> str:
        return _digest(self.selected.to_dict())

    @property
    def spec_digest(self) -> str:
        return _digest(
            {
                "schema": CODE_BENCHMARK_LEDGER_SCHEMA,
                "run_id": self.run_id,
                "registration_digest": self.registration.registration_digest,
                "baseline": self.baseline.to_dict(),
                "selected": self.selected.to_dict(),
                "mode": CODE_HISTORICAL_BENCHMARK_MODE,
                "production_influence_enabled": False,
            }
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": CODE_BENCHMARK_LEDGER_SCHEMA,
            "run_id": self.run_id,
            "registration_id": self.registration.registration_id,
            "registration_digest": self.registration.registration_digest,
            "workload_digest": self.registration.workload.workload_digest,
            "baseline": self.baseline.to_dict(),
            "selected": self.selected.to_dict(),
            "baseline_arm_digest": self.baseline_arm_digest,
            "selected_arm_digest": self.selected_arm_digest,
            "spec_digest": self.spec_digest,
            "mode": CODE_HISTORICAL_BENCHMARK_MODE,
            "production_influence_enabled": False,
        }


@dataclass(frozen=True, slots=True)
class CodeBenchmarkRunReceipt:
    """One cleanup-gated completed pair, strictly limited to experiment evidence."""

    spec: CodeBenchmarkRunSpec
    run: CodeHistoricalBenchmarkRun
    status: str = CODE_BENCHMARK_RECEIPT_STATUS
    claim_scope: str = CODE_BENCHMARK_EXPERIMENT_SCOPE
    production_influence_enabled: bool = False
    receipt_ref: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.spec, CodeBenchmarkRunSpec):
            raise CodeBenchmarkLedgerContractError("spec is invalid")
        if not isinstance(self.run, CodeHistoricalBenchmarkRun):
            raise CodeBenchmarkLedgerContractError("run is invalid")
        _validate_run_against_spec(self.run, self.spec)
        if str(self.status or "").strip().upper() != CODE_BENCHMARK_RECEIPT_STATUS:
            raise CodeBenchmarkLedgerContractError(
                "run receipt status must be COMPLETED_CLEAN"
            )
        if str(self.claim_scope or "").strip().upper() != CODE_BENCHMARK_EXPERIMENT_SCOPE:
            raise CodeBenchmarkLedgerContractError(
                "run receipt must remain experiment-evidence-only"
            )
        if self.production_influence_enabled:
            raise CodeBenchmarkLedgerContractError(
                "run receipt cannot enable production influence"
            )
        identity = {
            "schema": CODE_BENCHMARK_LEDGER_SCHEMA,
            "spec_digest": self.spec.spec_digest,
            "run_digest": self.run.run_digest,
            "cleanup_receipt_digest": self.run.cleanup_receipt.receipt_digest,
            "baseline_arm_evidence_digest": self.run.baseline_evidence.evidence_digest,
            "selected_arm_evidence_digest": self.run.selected_evidence.evidence_digest,
            "status": CODE_BENCHMARK_RECEIPT_STATUS,
            "claim_scope": CODE_BENCHMARK_EXPERIMENT_SCOPE,
            "production_influence_enabled": False,
        }
        digest = _digest(identity)
        expected_ref = f"code-benchmark-receipt://sha256/{digest}"
        supplied = str(self.receipt_ref or "").strip()
        if supplied and supplied != expected_ref:
            raise CodeBenchmarkLedgerContractError("receipt_ref does not bind run receipt")
        object.__setattr__(self, "status", CODE_BENCHMARK_RECEIPT_STATUS)
        object.__setattr__(self, "claim_scope", CODE_BENCHMARK_EXPERIMENT_SCOPE)
        object.__setattr__(self, "production_influence_enabled", False)
        object.__setattr__(self, "receipt_ref", expected_ref)

    @property
    def receipt_digest(self) -> str:
        return self.receipt_ref.removeprefix("code-benchmark-receipt://sha256/")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": CODE_BENCHMARK_LEDGER_SCHEMA,
            "spec": self.spec.to_dict(),
            "run": self.run.to_dict(),
            "status": CODE_BENCHMARK_RECEIPT_STATUS,
            "claim_scope": CODE_BENCHMARK_EXPERIMENT_SCOPE,
            "production_influence_enabled": False,
            "receipt_ref": self.receipt_ref,
        }


class CodeHistoricalBenchmarkLedger(Protocol):
    """Persistence boundary; no method is an execution or promotion route."""

    def register_workload(
        self, registration: CodeBenchmarkWorkloadRegistration
    ) -> CodeBenchmarkWorkloadRegistration: ...

    def preregister_run(self, spec: CodeBenchmarkRunSpec) -> CodeBenchmarkRunSpec: ...

    def record_completed_run(self, receipt: CodeBenchmarkRunReceipt) -> CodeBenchmarkRunReceipt: ...

    def get_completed(self, run_id: str) -> CodeBenchmarkRunReceipt | None: ...


def _validate_run_against_spec(
    run: CodeHistoricalBenchmarkRun,
    spec: CodeBenchmarkRunSpec,
) -> None:
    if run.run_id != spec.run_id:
        raise CodeBenchmarkLedgerContractError("completed run does not bind the preregistered run")
    report = run.report
    if report.workload.to_dict() != spec.registration.workload.to_dict():
        raise CodeBenchmarkLedgerContractError("completed run workload differs from registration")
    if (
        report.baseline.to_dict() != spec.baseline.to_dict()
        or report.selected.to_dict() != spec.selected.to_dict()
    ):
        raise CodeBenchmarkLedgerContractError("completed run arms differ from preregistration")
    if report.baseline_outcome is None or report.selected_outcome is None:
        raise CodeBenchmarkLedgerContractError("completed run requires both arm outcomes")
    if run.cleanup_receipt is None:
        raise CodeBenchmarkLedgerContractError("completed run requires a cleanup receipt")
    if run.cleanup_receipt.status != CODE_BENCHMARK_CLEANUP_STATUS:
        raise CodeBenchmarkLedgerContractError("completed run cleanup is not verified clean")
    if run.cleanup_receipt.run_id != run.run_id:
        raise CodeBenchmarkLedgerContractError("cleanup receipt does not bind completed run")
    _validate_arm_execution(
        arm=report.baseline,
        outcome=report.baseline_outcome,
        evidence=run.baseline_evidence,
        registration=spec.registration,
    )
    _validate_arm_execution(
        arm=report.selected,
        outcome=report.selected_outcome,
        evidence=run.selected_evidence,
        registration=spec.registration,
    )


def _validate_arm_execution(
    *,
    arm: BenchmarkArm,
    outcome: BenchmarkArmOutcome,
    evidence: CodeBenchmarkArmEvidence,
    registration: CodeBenchmarkWorkloadRegistration,
) -> None:
    if outcome.arm_id != arm.arm_id or evidence.arm_id != arm.arm_id:
        raise CodeBenchmarkLedgerContractError("arm outcome/evidence does not bind preregistered arm")
    if not outcome.executed or not isinstance(outcome.success, bool):
        raise CodeBenchmarkLedgerContractError("completed arm requires an executed boolean outcome")
    if not outcome.verifier_receipt_refs or evidence.verifier.receipt_ref not in outcome.verifier_receipt_refs:
        raise CodeBenchmarkLedgerContractError("arm outcome lacks bound verifier receipt evidence")
    if outcome.workspace_diff_digest != evidence.workspace_diff_digest:
        raise CodeBenchmarkLedgerContractError("arm outcome diff does not bind evidence diff")
    if dict(outcome.cost) != dict(evidence.verifier.cost):
        raise CodeBenchmarkLedgerContractError("arm outcome cost does not bind verifier evidence")
    if (
        evidence.source_ref != registration.source_ref
        or evidence.source_commit != registration.source_commit
        or evidence.path_policy_digest != registration.path_policy_digest
    ):
        raise CodeBenchmarkLedgerContractError(
            "arm evidence differs from registered source or path policy"
        )


def _validated_code_isolation(workload: DomainBenchmarkWorkload) -> dict[str, str]:
    isolation = dict(workload.isolation)
    if set(isolation) != _ISOLATION_KEYS:
        raise CodeBenchmarkLedgerContractError(
            "workload isolation must contain exactly the trusted D17 boundary"
        )
    source_ref = _opaque_ref(isolation.get("source_ref"), "isolation.source_ref")
    source_commit = _commit(isolation.get("source_commit"), "isolation.source_commit")
    path_policy_digest = _digest_text(
        isolation.get("path_policy_digest"), "isolation.path_policy_digest"
    )
    expected = {
        "sandbox_mode": CODE_HISTORICAL_BENCHMARK_MODE,
        "source_ref": source_ref,
        "source_commit": source_commit,
        "path_policy_digest": path_policy_digest,
        "executor_trust": CODE_HISTORICAL_BENCHMARK_EXECUTOR_TRUST,
        "network_isolation": CODE_HISTORICAL_BENCHMARK_NETWORK_ISOLATION,
    }
    if isolation != expected:
        raise CodeBenchmarkLedgerContractError(
            "workload isolation cannot claim unsupported benchmark isolation"
        )
    return expected


def _identifier(value: Any, field_name: str) -> str:
    if not isinstance(value, str):
        raise CodeBenchmarkLedgerContractError(f"{field_name} must be text")
    text = value.strip().lower()
    if not _ID_RE.fullmatch(text):
        raise CodeBenchmarkLedgerContractError(
            f"{field_name} must be a lowercase opaque identifier"
        )
    return text


def _opaque_ref(value: Any, field_name: str) -> str:
    if not isinstance(value, str):
        raise CodeBenchmarkLedgerContractError(f"{field_name} must be text")
    text = value.strip()
    if (
        not _GOVERNED_REF_RE.fullmatch(text)
        or text.lower().startswith("file://")
        or "/../" in text
        or text.endswith("/..")
    ):
        raise CodeBenchmarkLedgerContractError(
            f"{field_name} must be an opaque governed ref, not a path"
        )
    return text


def _commit(value: Any, field_name: str) -> str:
    if not isinstance(value, str):
        raise CodeBenchmarkLedgerContractError(f"{field_name} must be text")
    text = value.strip().lower()
    if not _COMMIT_RE.fullmatch(text):
        raise CodeBenchmarkLedgerContractError(f"{field_name} must be a full Git commit id")
    return text


def _digest_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str):
        raise CodeBenchmarkLedgerContractError(f"{field_name} must be text")
    text = value.strip().lower()
    if not _DIGEST_RE.fullmatch(text):
        raise CodeBenchmarkLedgerContractError(f"{field_name} must be a lowercase SHA-256")
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


__all__ = [
    "CODE_BENCHMARK_EXPERIMENT_SCOPE",
    "CODE_BENCHMARK_LEDGER_SCHEMA",
    "CODE_BENCHMARK_RECEIPT_STATUS",
    "CodeBenchmarkExecutionBudget",
    "CodeBenchmarkLedgerContractError",
    "CodeBenchmarkRunReceipt",
    "CodeBenchmarkRunSpec",
    "CodeBenchmarkWorkloadRegistration",
    "CodeHistoricalBenchmarkLedger",
]
