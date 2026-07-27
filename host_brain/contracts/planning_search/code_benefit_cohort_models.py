"""Immutable contracts for the fixed 20-workload Code benefit cohort.

These objects bind the existing cleanup-gated Code benchmark receipt to fresh
external model evidence and independent hidden/static verification.  They are
experiment evidence only: even a passing report may only feed an evolution
proposal and can never enable production influence.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import re
from typing import Any

from .code_benchmark_ledger import CodeBenchmarkRunReceipt
from .research_benchmark_contracts import ExternalProviderRunAttestationV1


CODE_BENEFIT_COHORT_SCHEMA = "natsume.code-benefit-cohort.v1"
CODE_BENEFIT_COHORT_SIZE = 20
CODE_BENEFIT_MIN_PASS_RATE_GAIN = 0.10
CODE_BENEFIT_NON_INFERIOR_MARGIN = 0.05
CODE_BENEFIT_MIN_COST_REDUCTION = 0.15
CODE_BENEFIT_EVIDENCE_CLASSES = frozenset(
    {"SCRIPTED_REPLAY", "CANONICAL_ATTEMPT_UNATTESTED", "ATTESTED_EXTERNAL"}
)

_ARM_ROLES = frozenset({"BASELINE", "SELECTED"})
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")


class CodeBenefitCohortError(ValueError):
    """The fixed Code benefit protocol is malformed or identity-drifted."""


def _text(value: Any, field: str, limit: int = 512) -> str:
    normalized = str(value or "").strip()
    if not normalized or len(normalized) > limit:
        raise CodeBenefitCohortError(f"{field} must be non-empty and bounded")
    return normalized


def _digest(value: Any, field: str) -> str:
    normalized = _text(value, field, 64).lower()
    if _DIGEST_RE.fullmatch(normalized) is None:
        raise CodeBenefitCohortError(f"{field} must be a SHA-256 digest")
    return normalized


def _json_digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _non_negative_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise CodeBenefitCohortError(f"{field} must be a non-negative integer")
    return value


def _bounded_score(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise CodeBenefitCohortError(f"{field} must be numeric")
    normalized = float(value)
    if not math.isfinite(normalized) or not 0.0 <= normalized <= 1.0:
        raise CodeBenefitCohortError(f"{field} must be within [0, 1]")
    return normalized


@dataclass(frozen=True, slots=True)
class CodeArmBenefitEvidenceV1:
    """Fresh model and independent verifier evidence for one Code arm."""

    run_id: str
    arm_role: str
    arm_id: str
    model_execution_id: str
    model_attempt_ids: tuple[str, ...]
    model_output_digest: str
    execution_profile_digest: str
    hidden_verifier_execution_id: str
    hidden_verifier_profile_digest: str
    hidden_test_receipt_ref: str
    hidden_test_receipt_digest: str
    hidden_test_passed: bool
    static_verifier_execution_id: str
    static_verifier_profile_digest: str
    static_check_receipt_ref: str
    static_check_receipt_digest: str
    static_check_passed: bool
    diff_risk_score: float
    verification_cost_micros: int
    evidence_class: str
    attestation: ExternalProviderRunAttestationV1 | None = None
    production_influence_enabled: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "run_id", _text(self.run_id, "run_id", 160))
        role = _text(self.arm_role, "arm_role", 16).upper()
        if role not in _ARM_ROLES:
            raise CodeBenefitCohortError("arm_role must be BASELINE or SELECTED")
        object.__setattr__(self, "arm_role", role)
        object.__setattr__(self, "arm_id", _text(self.arm_id, "arm_id", 160))
        model_execution_id = _text(
            self.model_execution_id,
            "model_execution_id",
            160,
        )
        object.__setattr__(self, "model_execution_id", model_execution_id)
        if isinstance(self.model_attempt_ids, (str, bytes, bytearray)):
            raise CodeBenefitCohortError(
                "model_attempt_ids must be an explicit sequence"
            )
        attempt_ids = tuple(
            _text(value, "model_attempt_ids[]", 160)
            for value in self.model_attempt_ids
        )
        if not attempt_ids or len(set(attempt_ids)) != len(attempt_ids):
            raise CodeBenefitCohortError(
                "model_attempt_ids must contain unique values"
            )
        object.__setattr__(self, "model_attempt_ids", attempt_ids)
        for field in (
            "model_output_digest",
            "execution_profile_digest",
            "hidden_verifier_profile_digest",
            "hidden_test_receipt_digest",
            "static_verifier_profile_digest",
            "static_check_receipt_digest",
        ):
            object.__setattr__(self, field, _digest(getattr(self, field), field))
        hidden_execution_id = _text(
            self.hidden_verifier_execution_id,
            "hidden_verifier_execution_id",
            160,
        )
        static_execution_id = _text(
            self.static_verifier_execution_id,
            "static_verifier_execution_id",
            160,
        )
        if len({model_execution_id, hidden_execution_id, static_execution_id}) != 3:
            raise CodeBenefitCohortError(
                "model, hidden-test and static verifier executions must be independent"
            )
        object.__setattr__(
            self,
            "hidden_verifier_execution_id",
            hidden_execution_id,
        )
        object.__setattr__(
            self,
            "static_verifier_execution_id",
            static_execution_id,
        )
        for field in ("hidden_test_receipt_ref", "static_check_receipt_ref"):
            value = _text(getattr(self, field), field, 1_024)
            if "://" not in value:
                raise CodeBenefitCohortError(f"{field} must be a governed ref")
            object.__setattr__(self, field, value)
        expected_hidden_ref = (
            f"code-hidden-test://sha256/{self.hidden_test_receipt_digest}"
        )
        expected_static_ref = (
            f"code-static-check://sha256/{self.static_check_receipt_digest}"
        )
        if self.hidden_test_receipt_ref != expected_hidden_ref:
            raise CodeBenefitCohortError(
                "hidden_test_receipt_ref does not bind its digest"
            )
        if self.static_check_receipt_ref != expected_static_ref:
            raise CodeBenefitCohortError(
                "static_check_receipt_ref does not bind its digest"
            )
        if not isinstance(self.hidden_test_passed, bool):
            raise CodeBenefitCohortError("hidden_test_passed must be boolean")
        if not isinstance(self.static_check_passed, bool):
            raise CodeBenefitCohortError("static_check_passed must be boolean")
        object.__setattr__(
            self,
            "diff_risk_score",
            _bounded_score(self.diff_risk_score, "diff_risk_score"),
        )
        object.__setattr__(
            self,
            "verification_cost_micros",
            _non_negative_int(
                self.verification_cost_micros,
                "verification_cost_micros",
            ),
        )
        evidence_class = _text(
            self.evidence_class,
            "evidence_class",
            64,
        ).upper()
        if evidence_class not in CODE_BENEFIT_EVIDENCE_CLASSES:
            raise CodeBenefitCohortError("unsupported evidence_class")
        object.__setattr__(self, "evidence_class", evidence_class)
        attestation = self.attestation
        if attestation is not None and not isinstance(
            attestation,
            ExternalProviderRunAttestationV1,
        ):
            raise CodeBenefitCohortError(
                "attestation must be ExternalProviderRunAttestationV1"
            )
        if evidence_class == "ATTESTED_EXTERNAL":
            if attestation is None:
                raise CodeBenefitCohortError(
                    "attested external evidence requires provider attestation"
                )
            if (
                attestation.execution_id != model_execution_id
                or attestation.response_digest != self.model_output_digest
            ):
                raise CodeBenefitCohortError(
                    "provider attestation does not bind the Code arm"
                )
        elif attestation is not None:
            raise CodeBenefitCohortError(
                "non-attested evidence cannot carry provider attestation"
            )
        if self.production_influence_enabled:
            raise CodeBenefitCohortError(
                "Code benefit evidence cannot enable production influence"
            )
        object.__setattr__(self, "attestation", attestation)
        object.__setattr__(self, "production_influence_enabled", False)

    @property
    def benefit_eligible(self) -> bool:
        return (
            self.evidence_class == "ATTESTED_EXTERNAL"
            and self.attestation is not None
            and self.attestation.transport_identity_digest is not None
            and self.attestation.transport_external is True
        )

    @property
    def total_cost_micros(self) -> int:
        model_cost = 0 if self.attestation is None else self.attestation.cost_micros
        return model_cost + self.verification_cost_micros

    @property
    def receipt_digest(self) -> str:
        return _json_digest(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "natsume.code-arm-benefit-evidence.v1",
            "run_id": self.run_id,
            "arm_role": self.arm_role,
            "arm_id": self.arm_id,
            "model_execution_id": self.model_execution_id,
            "model_attempt_ids": list(self.model_attempt_ids),
            "model_output_digest": self.model_output_digest,
            "execution_profile_digest": self.execution_profile_digest,
            "hidden_verifier_execution_id": self.hidden_verifier_execution_id,
            "hidden_verifier_profile_digest": self.hidden_verifier_profile_digest,
            "hidden_test_receipt_ref": self.hidden_test_receipt_ref,
            "hidden_test_receipt_digest": self.hidden_test_receipt_digest,
            "hidden_test_passed": self.hidden_test_passed,
            "static_verifier_execution_id": self.static_verifier_execution_id,
            "static_verifier_profile_digest": self.static_verifier_profile_digest,
            "static_check_receipt_ref": self.static_check_receipt_ref,
            "static_check_receipt_digest": self.static_check_receipt_digest,
            "static_check_passed": self.static_check_passed,
            "diff_risk_score": self.diff_risk_score,
            "verification_cost_micros": self.verification_cost_micros,
            "evidence_class": self.evidence_class,
            "attestation": self.attestation.to_dict() if self.attestation else None,
            "benefit_eligible": self.benefit_eligible,
            "production_influence_enabled": False,
        }


@dataclass(frozen=True, slots=True)
class CodeCohortWorkloadV1:
    workload_id: str
    run_id: str
    terminal_receipt_digest: str
    source_commit: str
    path_policy_digest: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "workload_id",
            _text(self.workload_id, "workload_id", 160),
        )
        object.__setattr__(self, "run_id", _text(self.run_id, "run_id", 160))
        for field in (
            "terminal_receipt_digest",
            "path_policy_digest",
        ):
            object.__setattr__(self, field, _digest(getattr(self, field), field))
        source_commit = _text(self.source_commit, "source_commit", 64).lower()
        if re.fullmatch(r"[0-9a-f]{40,64}", source_commit) is None:
            raise CodeBenefitCohortError(
                "source_commit must be a Git object digest"
            )
        object.__setattr__(self, "source_commit", source_commit)

    def to_dict(self) -> dict[str, str]:
        return {
            "workload_id": self.workload_id,
            "run_id": self.run_id,
            "terminal_receipt_digest": self.terminal_receipt_digest,
            "source_commit": self.source_commit,
            "path_policy_digest": self.path_policy_digest,
        }


@dataclass(frozen=True, slots=True)
class CodeBenefitCohortSpecV1:
    cohort_id: str
    cohort_version: str
    planning_algorithm_version: str
    execution_provider: str
    execution_model: str
    execution_profile_digest: str
    execution_budget_digest: str
    hidden_verifier_profile_digest: str
    static_verifier_profile_digest: str
    randomization_seed_digest: str
    workloads: tuple[CodeCohortWorkloadV1, ...]
    production_influence_enabled: bool = False

    def __post_init__(self) -> None:
        for field, limit in (
            ("cohort_id", 160),
            ("cohort_version", 80),
            ("planning_algorithm_version", 120),
            ("execution_provider", 160),
            ("execution_model", 240),
        ):
            object.__setattr__(
                self,
                field,
                _text(getattr(self, field), field, limit),
            )
        object.__setattr__(
            self,
            "execution_provider",
            self.execution_provider.lower(),
        )
        for field in (
            "execution_profile_digest",
            "execution_budget_digest",
            "hidden_verifier_profile_digest",
            "static_verifier_profile_digest",
            "randomization_seed_digest",
        ):
            object.__setattr__(self, field, _digest(getattr(self, field), field))
        if isinstance(self.workloads, (str, bytes, bytearray)):
            raise CodeBenefitCohortError("workloads must be a sequence")
        workloads = tuple(self.workloads)
        if len(workloads) != CODE_BENEFIT_COHORT_SIZE or any(
            not isinstance(item, CodeCohortWorkloadV1) for item in workloads
        ):
            raise CodeBenefitCohortError(
                f"Code benefit cohort requires exactly {CODE_BENEFIT_COHORT_SIZE} workloads"
            )
        workload_ids = tuple(item.workload_id for item in workloads)
        run_ids = tuple(item.run_id for item in workloads)
        receipt_digests = tuple(item.terminal_receipt_digest for item in workloads)
        if workload_ids != tuple(sorted(workload_ids)):
            raise CodeBenefitCohortError(
                "Code cohort workloads must be sorted by workload_id"
            )
        if (
            len(set(workload_ids)) != len(workloads)
            or len(set(run_ids)) != len(workloads)
            or len(set(receipt_digests)) != len(workloads)
        ):
            raise CodeBenefitCohortError(
                "Code workload, run and terminal receipt identities must be unique"
            )
        if self.production_influence_enabled:
            raise CodeBenefitCohortError(
                "Code cohort spec cannot enable production influence"
            )
        object.__setattr__(self, "workloads", workloads)
        object.__setattr__(self, "production_influence_enabled", False)

    @property
    def spec_digest(self) -> str:
        return _json_digest(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": CODE_BENEFIT_COHORT_SCHEMA,
            "cohort_id": self.cohort_id,
            "cohort_version": self.cohort_version,
            "planning_algorithm_version": self.planning_algorithm_version,
            "execution_provider": self.execution_provider,
            "execution_model": self.execution_model,
            "execution_profile_digest": self.execution_profile_digest,
            "execution_budget_digest": self.execution_budget_digest,
            "hidden_verifier_profile_digest": self.hidden_verifier_profile_digest,
            "static_verifier_profile_digest": self.static_verifier_profile_digest,
            "randomization_seed_digest": self.randomization_seed_digest,
            "thresholds": _thresholds(),
            "workloads": [item.to_dict() for item in self.workloads],
            "production_influence_enabled": False,
        }


@dataclass(frozen=True, slots=True)
class CodeCohortPairedEvidenceV1:
    workload_id: str
    run_id: str
    terminal_receipt_digest: str
    terminal_receipt: CodeBenchmarkRunReceipt
    baseline: CodeArmBenefitEvidenceV1
    selected: CodeArmBenefitEvidenceV1
    owner_randomized_run: bool
    independent_fresh_worktrees: bool

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "workload_id",
            _text(self.workload_id, "workload_id", 160),
        )
        run_id = _text(self.run_id, "run_id", 160)
        object.__setattr__(self, "run_id", run_id)
        terminal_digest = _digest(
            self.terminal_receipt_digest,
            "terminal_receipt_digest",
        )
        object.__setattr__(self, "terminal_receipt_digest", terminal_digest)
        receipt = self.terminal_receipt
        if not isinstance(receipt, CodeBenchmarkRunReceipt):
            raise CodeBenefitCohortError(
                "terminal_receipt must be CodeBenchmarkRunReceipt"
            )
        if _json_digest(receipt.to_dict()) != terminal_digest:
            raise CodeBenefitCohortError("terminal receipt digest drift")
        if (
            receipt.spec.run_id != run_id
            or receipt.spec.registration.workload.workload_id != self.workload_id
        ):
            raise CodeBenefitCohortError(
                "terminal receipt does not bind workload and run"
            )
        for role, evidence, arm_id in (
            ("BASELINE", self.baseline, receipt.spec.baseline.arm_id),
            ("SELECTED", self.selected, receipt.spec.selected.arm_id),
        ):
            if not isinstance(evidence, CodeArmBenefitEvidenceV1):
                raise CodeBenefitCohortError("arm benefit evidence is invalid")
            if (
                evidence.run_id != run_id
                or evidence.arm_role != role
                or evidence.arm_id != arm_id
            ):
                raise CodeBenefitCohortError(
                    f"{role} benefit evidence does not bind terminal arm"
                )
        if (
            self.baseline.model_execution_id
            == self.selected.model_execution_id
        ):
            raise CodeBenefitCohortError(
                "paired Code arms require independent model executions"
            )
        execution_ids = {
            self.baseline.model_execution_id,
            self.baseline.hidden_verifier_execution_id,
            self.baseline.static_verifier_execution_id,
            self.selected.model_execution_id,
            self.selected.hidden_verifier_execution_id,
            self.selected.static_verifier_execution_id,
        }
        if len(execution_ids) != 6:
            raise CodeBenefitCohortError(
                "paired Code model and verifier executions must all be independent"
            )
        if set(self.baseline.model_attempt_ids).intersection(
            self.selected.model_attempt_ids
        ):
            raise CodeBenefitCohortError(
                "paired Code arms cannot share model attempts"
            )
        verifier_refs = {
            self.baseline.hidden_test_receipt_ref,
            self.baseline.static_check_receipt_ref,
            self.selected.hidden_test_receipt_ref,
            self.selected.static_check_receipt_ref,
        }
        if len(verifier_refs) != 4:
            raise CodeBenefitCohortError(
                "paired Code verifier receipts must be independent"
            )
        if not isinstance(self.owner_randomized_run, bool):
            raise CodeBenefitCohortError(
                "owner_randomized_run must be boolean"
            )
        if not isinstance(self.independent_fresh_worktrees, bool):
            raise CodeBenefitCohortError(
                "independent_fresh_worktrees must be boolean"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "workload_id": self.workload_id,
            "run_id": self.run_id,
            "terminal_receipt_digest": self.terminal_receipt_digest,
            "terminal_receipt": self.terminal_receipt.to_dict(),
            "baseline": self.baseline.to_dict(),
            "selected": self.selected.to_dict(),
            "owner_randomized_run": self.owner_randomized_run,
            "independent_fresh_worktrees": self.independent_fresh_worktrees,
        }


@dataclass(frozen=True, slots=True)
class CodeBenefitCohortReportV1:
    cohort_id: str
    cohort_version: str
    spec_digest: str
    evaluated_workload_count: int
    baseline_pass_rate: float
    selected_pass_rate: float
    pass_rate_gain: float
    baseline_hidden_test_rate: float
    selected_hidden_test_rate: float
    baseline_static_check_rate: float
    selected_static_check_rate: float
    baseline_mean_diff_risk: float
    selected_mean_diff_risk: float
    baseline_total_cost_micros: int
    selected_total_cost_micros: int
    cost_reduction_rate: float | None
    reason_codes: tuple[str, ...]
    evidence_digest: str
    benefit_eligible: bool
    proposal_eligible: bool
    production_influence_enabled: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "cohort_id", _text(self.cohort_id, "cohort_id", 160))
        object.__setattr__(
            self,
            "cohort_version",
            _text(self.cohort_version, "cohort_version", 80),
        )
        object.__setattr__(self, "spec_digest", _digest(self.spec_digest, "spec_digest"))
        if self.evaluated_workload_count != CODE_BENEFIT_COHORT_SIZE:
            raise CodeBenefitCohortError("Code report workload count drift")
        for field in (
            "baseline_pass_rate",
            "selected_pass_rate",
            "baseline_hidden_test_rate",
            "selected_hidden_test_rate",
            "baseline_static_check_rate",
            "selected_static_check_rate",
            "baseline_mean_diff_risk",
            "selected_mean_diff_risk",
        ):
            object.__setattr__(
                self,
                field,
                _bounded_score(getattr(self, field), field),
            )
        pass_gain = float(self.pass_rate_gain)
        if not math.isfinite(pass_gain) or not -1.0 <= pass_gain <= 1.0:
            raise CodeBenefitCohortError("pass_rate_gain is invalid")
        object.__setattr__(self, "pass_rate_gain", pass_gain)
        for field in (
            "baseline_total_cost_micros",
            "selected_total_cost_micros",
        ):
            object.__setattr__(
                self,
                field,
                _non_negative_int(getattr(self, field), field),
            )
        reduction = self.cost_reduction_rate
        if reduction is not None:
            reduction = float(reduction)
            if not math.isfinite(reduction) or reduction > 1.0:
                raise CodeBenefitCohortError("cost_reduction_rate is invalid")
        object.__setattr__(self, "cost_reduction_rate", reduction)
        reasons = tuple(
            sorted(set(_text(value, "reason_codes[]", 120) for value in self.reason_codes))
        )
        object.__setattr__(self, "reason_codes", reasons)
        object.__setattr__(
            self,
            "evidence_digest",
            _digest(self.evidence_digest, "evidence_digest"),
        )
        expected = not reasons
        if self.benefit_eligible is not expected or self.proposal_eligible is not expected:
            raise CodeBenefitCohortError(
                "Code report eligibility must be derived from reason_codes"
            )
        if self.production_influence_enabled:
            raise CodeBenefitCohortError(
                "Code cohort report cannot enable production influence"
            )
        object.__setattr__(self, "production_influence_enabled", False)

    @property
    def report_digest(self) -> str:
        return _json_digest(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "natsume.code-benefit-cohort-report.v1",
            "cohort_id": self.cohort_id,
            "cohort_version": self.cohort_version,
            "spec_digest": self.spec_digest,
            "evaluated_workload_count": self.evaluated_workload_count,
            "metrics": {
                "baseline_pass_rate": self.baseline_pass_rate,
                "selected_pass_rate": self.selected_pass_rate,
                "pass_rate_gain": self.pass_rate_gain,
                "baseline_hidden_test_rate": self.baseline_hidden_test_rate,
                "selected_hidden_test_rate": self.selected_hidden_test_rate,
                "baseline_static_check_rate": self.baseline_static_check_rate,
                "selected_static_check_rate": self.selected_static_check_rate,
                "baseline_mean_diff_risk": self.baseline_mean_diff_risk,
                "selected_mean_diff_risk": self.selected_mean_diff_risk,
                "baseline_total_cost_micros": self.baseline_total_cost_micros,
                "selected_total_cost_micros": self.selected_total_cost_micros,
                "cost_reduction_rate": self.cost_reduction_rate,
            },
            "thresholds": _thresholds(),
            "reason_codes": list(self.reason_codes),
            "evidence_digest": self.evidence_digest,
            "benefit_eligible": self.benefit_eligible,
            "proposal_eligible": self.proposal_eligible,
            "production_influence_enabled": False,
        }


def _thresholds() -> dict[str, Any]:
    return {
        "cohort_size": CODE_BENEFIT_COHORT_SIZE,
        "min_pass_rate_gain": CODE_BENEFIT_MIN_PASS_RATE_GAIN,
        "non_inferior_margin": CODE_BENEFIT_NON_INFERIOR_MARGIN,
        "min_cost_reduction": CODE_BENEFIT_MIN_COST_REDUCTION,
        "hidden_test_rate_regression_allowed": 0.0,
        "static_check_rate_regression_allowed": 0.0,
        "diff_risk_regression_allowed": 0.0,
    }


__all__ = [
    "CODE_BENEFIT_COHORT_SCHEMA",
    "CODE_BENEFIT_COHORT_SIZE",
    "CODE_BENEFIT_MIN_COST_REDUCTION",
    "CODE_BENEFIT_MIN_PASS_RATE_GAIN",
    "CODE_BENEFIT_NON_INFERIOR_MARGIN",
    "CodeArmBenefitEvidenceV1",
    "CodeBenefitCohortError",
    "CodeBenefitCohortReportV1",
    "CodeBenefitCohortSpecV1",
    "CodeCohortPairedEvidenceV1",
    "CodeCohortWorkloadV1",
]
