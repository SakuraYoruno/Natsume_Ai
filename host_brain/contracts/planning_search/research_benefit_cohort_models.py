"""Value contracts for the fixed Research benefit cohort.

The types in this module deliberately separate one hidden-verifier judgement
from the paired execution receipt.  They do not grant production influence:
even a successful cohort may only become input to an evolution proposal.
Durable ownership is supplied by the Research cohort repository, not by these
pure value objects.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import re
from typing import Any

from .research_benchmark_contracts import (
    ExternalProviderRunAttestationV1,
    PairedResearchRunReceiptV1,
)

RESEARCH_BENEFIT_COHORT_SCHEMA = "natsume.research-benefit-cohort.v1"
RESEARCH_BENEFIT_COHORT_SIZE = 24
RESEARCH_BENEFIT_MIN_TOPIC_CATEGORIES = 3
RESEARCH_BENEFIT_BOOTSTRAP_ITERATIONS = 10_000
RESEARCH_BENEFIT_MIN_QUALITY_UPLIFT = 0.05
RESEARCH_BENEFIT_MIN_CITATION_VALIDITY = 0.95
RESEARCH_BENEFIT_MAX_CITATION_DECLINE = 0.02
RESEARCH_BENEFIT_MAX_COST_RATIO = 1.5
RESEARCH_BENEFIT_MAX_P95_LATENCY_RATIO = 1.75
RESEARCH_SOURCE_KINDS = frozenset({"LOCAL_FILE", "WEB"})
RESEARCH_QUALITY_EVIDENCE_CLASSES = frozenset(
    {"SCRIPTED_REPLAY", "CANONICAL_ATTEMPT_UNATTESTED", "ATTESTED_EXTERNAL"}
)

_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_ARM_ROLES = frozenset({"BASELINE", "SELECTED"})


class ResearchBenefitCohortError(ValueError):
    """The fixed Research benefit protocol is malformed or identity-drifted."""


def _text(value: Any, field: str, limit: int = 512) -> str:
    normalized = str(value or "").strip()
    if not normalized or len(normalized) > limit:
        raise ResearchBenefitCohortError(f"{field} must be non-empty and bounded")
    return normalized


def _digest(value: Any, field: str) -> str:
    normalized = _text(value, field, 64).lower()
    if _DIGEST_RE.fullmatch(normalized) is None:
        raise ResearchBenefitCohortError(f"{field} must be a SHA-256 digest")
    return normalized


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _json_digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _bounded_score(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise ResearchBenefitCohortError(f"{field} must be numeric")
    normalized = float(value)
    if not math.isfinite(normalized) or not 0.0 <= normalized <= 1.0:
        raise ResearchBenefitCohortError(f"{field} must be within [0, 1]")
    return normalized


def _non_negative_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ResearchBenefitCohortError(f"{field} must be a non-negative integer")
    return value


@dataclass(frozen=True, slots=True)
class ResearchQualityVerifierReceiptV1:
    """One arm's score from an independent hidden verifier request.

    Non-attested evidence remains representable so failed experiments can be
    retained.  It is never benefit eligible.
    """

    run_id: str
    arm_role: str
    arm_execution_id: str
    evaluated_response_digest: str
    verifier_execution_id: str
    verifier_attempt_ids: tuple[str, ...]
    verifier_profile_digest: str
    rubric_digest: str
    verifier_result_digest: str
    quality_score: float
    severe_policy_errors: int
    severe_conflict_errors: int
    evidence_class: str
    hidden_verifier: bool
    attestation_ref: str | None = None
    attestation: ExternalProviderRunAttestationV1 | None = None
    production_influence_enabled: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "run_id", _text(self.run_id, "run_id", 255))
        role = _text(self.arm_role, "arm_role", 16).upper()
        if role not in _ARM_ROLES:
            raise ResearchBenefitCohortError("arm_role must be BASELINE or SELECTED")
        object.__setattr__(self, "arm_role", role)
        arm_execution_id = _text(
            self.arm_execution_id,
            "arm_execution_id",
            160,
        )
        verifier_execution_id = _text(
            self.verifier_execution_id,
            "verifier_execution_id",
            160,
        )
        if arm_execution_id == verifier_execution_id:
            raise ResearchBenefitCohortError(
                "hidden verifier requires an execution independent from the arm"
            )
        object.__setattr__(self, "arm_execution_id", arm_execution_id)
        object.__setattr__(self, "verifier_execution_id", verifier_execution_id)
        for field in (
            "evaluated_response_digest",
            "verifier_profile_digest",
            "rubric_digest",
            "verifier_result_digest",
        ):
            object.__setattr__(self, field, _digest(getattr(self, field), field))
        if isinstance(self.verifier_attempt_ids, (str, bytes, bytearray)):
            raise ResearchBenefitCohortError(
                "verifier_attempt_ids must be an explicit sequence"
            )
        attempt_ids = tuple(
            _text(item, "verifier_attempt_ids[]", 160)
            for item in self.verifier_attempt_ids
        )
        if not attempt_ids or len(set(attempt_ids)) != len(attempt_ids):
            raise ResearchBenefitCohortError(
                "verifier_attempt_ids must contain unique values"
            )
        object.__setattr__(self, "verifier_attempt_ids", attempt_ids)
        object.__setattr__(
            self,
            "quality_score",
            _bounded_score(self.quality_score, "quality_score"),
        )
        object.__setattr__(
            self,
            "severe_policy_errors",
            _non_negative_int(self.severe_policy_errors, "severe_policy_errors"),
        )
        object.__setattr__(
            self,
            "severe_conflict_errors",
            _non_negative_int(self.severe_conflict_errors, "severe_conflict_errors"),
        )
        evidence_class = _text(
            self.evidence_class,
            "evidence_class",
            64,
        ).upper()
        if evidence_class not in RESEARCH_QUALITY_EVIDENCE_CLASSES:
            raise ResearchBenefitCohortError("unsupported quality evidence_class")
        object.__setattr__(self, "evidence_class", evidence_class)
        if not isinstance(self.hidden_verifier, bool):
            raise ResearchBenefitCohortError("hidden_verifier must be a boolean")
        attestation_ref = (
            None
            if self.attestation_ref is None
            else _text(self.attestation_ref, "attestation_ref", 1_024)
        )
        attestation = self.attestation
        if attestation is not None and not isinstance(
            attestation,
            ExternalProviderRunAttestationV1,
        ):
            raise ResearchBenefitCohortError(
                "attestation must be ExternalProviderRunAttestationV1"
            )
        if evidence_class == "ATTESTED_EXTERNAL":
            if attestation_ref is None or attestation is None:
                raise ResearchBenefitCohortError(
                    "attested quality evidence requires the exact provider attestation"
                )
            if (
                attestation.execution_id != verifier_execution_id
                or attestation.attestation_ref != attestation_ref
                or attestation.response_digest != self.verifier_result_digest
            ):
                raise ResearchBenefitCohortError(
                    "quality provider attestation does not bind the verifier receipt"
                )
        elif attestation_ref is not None or attestation is not None:
            raise ResearchBenefitCohortError(
                "non-attested quality evidence cannot carry provider attestation"
            )
        if self.production_influence_enabled:
            raise ResearchBenefitCohortError(
                "Research quality evidence cannot enable production influence"
            )
        object.__setattr__(self, "attestation_ref", attestation_ref)
        object.__setattr__(self, "attestation", attestation)
        object.__setattr__(self, "production_influence_enabled", False)

    @property
    def benefit_eligible(self) -> bool:
        return (
            self.evidence_class == "ATTESTED_EXTERNAL"
            and self.hidden_verifier
            and self.attestation is not None
            and self.attestation.transport_identity_digest is not None
            and self.attestation.transport_external is True
        )

    @property
    def receipt_digest(self) -> str:
        return _json_digest(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "schema": "natsume.research-quality-verifier-receipt.v1",
            "run_id": self.run_id,
            "arm_role": self.arm_role,
            "arm_execution_id": self.arm_execution_id,
            "evaluated_response_digest": self.evaluated_response_digest,
            "verifier_execution_id": self.verifier_execution_id,
            "verifier_attempt_ids": list(self.verifier_attempt_ids),
            "verifier_profile_digest": self.verifier_profile_digest,
            "rubric_digest": self.rubric_digest,
            "verifier_result_digest": self.verifier_result_digest,
            "quality_score": self.quality_score,
            "severe_policy_errors": self.severe_policy_errors,
            "severe_conflict_errors": self.severe_conflict_errors,
            "evidence_class": self.evidence_class,
            "hidden_verifier": self.hidden_verifier,
            "attestation_ref": self.attestation_ref,
            "benefit_eligible": self.benefit_eligible,
            "production_influence_enabled": False,
        }
        if self.attestation is not None:
            result["attestation"] = self.attestation.to_dict()
        return result


@dataclass(frozen=True, slots=True)
class ResearchCohortWorkloadV1:
    workload_id: str
    run_id: str
    terminal_receipt_digest: str
    topic_category: str
    source_kinds: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "workload_id",
            _text(self.workload_id, "workload_id", 160),
        )
        object.__setattr__(self, "run_id", _text(self.run_id, "run_id", 255))
        object.__setattr__(
            self,
            "terminal_receipt_digest",
            _digest(self.terminal_receipt_digest, "terminal_receipt_digest"),
        )
        object.__setattr__(
            self,
            "topic_category",
            _text(self.topic_category, "topic_category", 120),
        )
        if isinstance(self.source_kinds, (str, bytes, bytearray)):
            raise ResearchBenefitCohortError("source_kinds must be a sequence")
        source_kinds = tuple(
            sorted(
                set(
                    _text(value, "source_kinds[]", 32).upper()
                    for value in self.source_kinds
                )
            )
        )
        if not source_kinds or any(
            value not in RESEARCH_SOURCE_KINDS for value in source_kinds
        ):
            raise ResearchBenefitCohortError(
                "source_kinds may contain only LOCAL_FILE and WEB"
            )
        object.__setattr__(self, "source_kinds", source_kinds)

    def to_dict(self) -> dict[str, Any]:
        return {
            "workload_id": self.workload_id,
            "run_id": self.run_id,
            "terminal_receipt_digest": self.terminal_receipt_digest,
            "topic_category": self.topic_category,
            "source_kinds": list(self.source_kinds),
        }


@dataclass(frozen=True, slots=True)
class ResearchBenefitCohortSpecV1:
    cohort_id: str
    cohort_version: str
    planning_algorithm_version: str
    execution_provider: str
    execution_model: str
    verifier_provider: str
    verifier_model: str
    verifier_profile_digest: str
    rubric_digest: str
    randomization_seed_digest: str
    workloads: tuple[ResearchCohortWorkloadV1, ...]
    production_influence_enabled: bool = False

    def __post_init__(self) -> None:
        for field, limit in (
            ("cohort_id", 160),
            ("cohort_version", 80),
            ("planning_algorithm_version", 120),
            ("execution_provider", 160),
            ("execution_model", 240),
            ("verifier_provider", 160),
            ("verifier_model", 240),
        ):
            object.__setattr__(
                self,
                field,
                _text(getattr(self, field), field, limit),
            )
        for field in (
            "verifier_profile_digest",
            "rubric_digest",
            "randomization_seed_digest",
        ):
            object.__setattr__(self, field, _digest(getattr(self, field), field))
        if isinstance(self.workloads, (str, bytes, bytearray)):
            raise ResearchBenefitCohortError("workloads must be a sequence")
        workloads = tuple(self.workloads)
        if len(workloads) != RESEARCH_BENEFIT_COHORT_SIZE or any(
            not isinstance(item, ResearchCohortWorkloadV1) for item in workloads
        ):
            raise ResearchBenefitCohortError(
                f"Research benefit cohort requires exactly {RESEARCH_BENEFIT_COHORT_SIZE} workloads"
            )
        workload_ids = tuple(item.workload_id for item in workloads)
        run_ids = tuple(item.run_id for item in workloads)
        receipt_digests = tuple(item.terminal_receipt_digest for item in workloads)
        if workload_ids != tuple(sorted(workload_ids)):
            raise ResearchBenefitCohortError("cohort workloads must be sorted by workload_id")
        if (
            len(set(workload_ids)) != len(workload_ids)
            or len(set(run_ids)) != len(run_ids)
            or len(set(receipt_digests)) != len(receipt_digests)
        ):
            raise ResearchBenefitCohortError(
                "cohort workload, run and terminal receipt identities must be unique"
            )
        if (
            len({item.topic_category for item in workloads})
            < RESEARCH_BENEFIT_MIN_TOPIC_CATEGORIES
        ):
            raise ResearchBenefitCohortError(
                "Research benefit cohort requires at least three topic categories"
            )
        all_source_kinds = {
            source_kind
            for workload in workloads
            for source_kind in workload.source_kinds
        }
        if all_source_kinds != RESEARCH_SOURCE_KINDS:
            raise ResearchBenefitCohortError(
                "Research benefit cohort must contain local-file and Web sources"
            )
        if self.production_influence_enabled:
            raise ResearchBenefitCohortError(
                "Research cohort spec cannot enable production influence"
            )
        object.__setattr__(self, "workloads", workloads)
        object.__setattr__(self, "production_influence_enabled", False)

    @property
    def spec_digest(self) -> str:
        return _json_digest(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": RESEARCH_BENEFIT_COHORT_SCHEMA,
            "cohort_id": self.cohort_id,
            "cohort_version": self.cohort_version,
            "planning_algorithm_version": self.planning_algorithm_version,
            "execution_provider": self.execution_provider,
            "execution_model": self.execution_model,
            "verifier_provider": self.verifier_provider,
            "verifier_model": self.verifier_model,
            "verifier_profile_digest": self.verifier_profile_digest,
            "rubric_digest": self.rubric_digest,
            "randomization_seed_digest": self.randomization_seed_digest,
            "bootstrap_algorithm": "PAIRED_BOOTSTRAP_MEAN_V1",
            "bootstrap_iterations": RESEARCH_BENEFIT_BOOTSTRAP_ITERATIONS,
            "thresholds": _thresholds(),
            "workloads": [item.to_dict() for item in self.workloads],
            "production_influence_enabled": False,
        }


def _model_response_digest(
    receipt: PairedResearchRunReceiptV1,
    role: str,
) -> str:
    model = receipt.baseline_model if role == "BASELINE" else receipt.selected_model
    if model.attestation is None:
        return _json_digest(model.to_dict())
    return model.attestation.response_digest


@dataclass(frozen=True, slots=True)
class ResearchCohortPairedEvidenceV1:
    workload_id: str
    run_id: str
    terminal_receipt_digest: str
    terminal_receipt: PairedResearchRunReceiptV1
    baseline_quality: ResearchQualityVerifierReceiptV1
    selected_quality: ResearchQualityVerifierReceiptV1
    owner_randomized_run: bool

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "workload_id",
            _text(self.workload_id, "workload_id", 160),
        )
        run_id = _text(self.run_id, "run_id", 255)
        object.__setattr__(self, "run_id", run_id)
        terminal_digest = _digest(
            self.terminal_receipt_digest,
            "terminal_receipt_digest",
        )
        object.__setattr__(self, "terminal_receipt_digest", terminal_digest)
        if not isinstance(self.terminal_receipt, PairedResearchRunReceiptV1):
            raise ResearchBenefitCohortError(
                "terminal_receipt must be PairedResearchRunReceiptV1"
            )
        actual_terminal_digest = _json_digest(self.terminal_receipt.to_dict())
        if actual_terminal_digest != terminal_digest:
            raise ResearchBenefitCohortError("terminal receipt digest drift")
        for role, quality, execution_id, response_digest in (
            (
                "BASELINE",
                self.baseline_quality,
                self.terminal_receipt.baseline_execution_id,
                _model_response_digest(self.terminal_receipt, "BASELINE"),
            ),
            (
                "SELECTED",
                self.selected_quality,
                self.terminal_receipt.selected_execution_id,
                _model_response_digest(self.terminal_receipt, "SELECTED"),
            ),
        ):
            if not isinstance(quality, ResearchQualityVerifierReceiptV1):
                raise ResearchBenefitCohortError(
                    "cohort quality evidence has an invalid type"
                )
            if (
                quality.run_id != run_id
                or quality.arm_role != role
                or quality.arm_execution_id != execution_id
                or quality.evaluated_response_digest != response_digest
            ):
                raise ResearchBenefitCohortError(
                    f"{role} quality receipt does not bind the paired execution"
                )
        if not isinstance(self.owner_randomized_run, bool):
            raise ResearchBenefitCohortError(
                "owner_randomized_run must be a boolean"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "workload_id": self.workload_id,
            "run_id": self.run_id,
            "terminal_receipt_digest": self.terminal_receipt_digest,
            "terminal_receipt": self.terminal_receipt.to_dict(),
            "baseline_quality": self.baseline_quality.to_dict(),
            "selected_quality": self.selected_quality.to_dict(),
            "owner_randomized_run": self.owner_randomized_run,
        }


@dataclass(frozen=True, slots=True)
class ResearchBenefitCohortReportV1:
    cohort_id: str
    cohort_version: str
    spec_digest: str
    evaluated_workload_count: int
    mean_quality_uplift: float
    bootstrap_95_lower_bound: float
    baseline_mean_citation_validity: float
    selected_mean_citation_validity: float
    citation_validity_delta: float
    total_cost_ratio: float
    p95_latency_ratio: float
    selected_severe_policy_errors: int
    selected_severe_conflict_errors: int
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
        if self.evaluated_workload_count != RESEARCH_BENEFIT_COHORT_SIZE:
            raise ResearchBenefitCohortError("cohort report workload count drift")
        for field in (
            "mean_quality_uplift",
            "bootstrap_95_lower_bound",
            "baseline_mean_citation_validity",
            "selected_mean_citation_validity",
            "citation_validity_delta",
            "total_cost_ratio",
            "p95_latency_ratio",
        ):
            value = float(getattr(self, field))
            if math.isnan(value):
                raise ResearchBenefitCohortError(f"{field} cannot be NaN")
            object.__setattr__(self, field, value)
        object.__setattr__(
            self,
            "selected_severe_policy_errors",
            _non_negative_int(
                self.selected_severe_policy_errors,
                "selected_severe_policy_errors",
            ),
        )
        object.__setattr__(
            self,
            "selected_severe_conflict_errors",
            _non_negative_int(
                self.selected_severe_conflict_errors,
                "selected_severe_conflict_errors",
            ),
        )
        reason_codes = tuple(
            sorted(set(_text(value, "reason_codes[]", 120) for value in self.reason_codes))
        )
        object.__setattr__(self, "reason_codes", reason_codes)
        object.__setattr__(
            self,
            "evidence_digest",
            _digest(self.evidence_digest, "evidence_digest"),
        )
        expected = not reason_codes
        if self.benefit_eligible is not expected or self.proposal_eligible is not expected:
            raise ResearchBenefitCohortError(
                "cohort eligibility must be derived from reason_codes"
            )
        if self.production_influence_enabled:
            raise ResearchBenefitCohortError(
                "Research cohort report cannot enable production influence"
            )
        object.__setattr__(self, "production_influence_enabled", False)

    @property
    def report_digest(self) -> str:
        return _json_digest(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "natsume.research-benefit-cohort-report.v1",
            "cohort_id": self.cohort_id,
            "cohort_version": self.cohort_version,
            "spec_digest": self.spec_digest,
            "evaluated_workload_count": self.evaluated_workload_count,
            "metrics": {
                "mean_quality_uplift": self.mean_quality_uplift,
                "bootstrap_95_lower_bound": self.bootstrap_95_lower_bound,
                "baseline_mean_citation_validity": self.baseline_mean_citation_validity,
                "selected_mean_citation_validity": self.selected_mean_citation_validity,
                "citation_validity_delta": self.citation_validity_delta,
                "total_cost_ratio": self.total_cost_ratio,
                "p95_latency_ratio": self.p95_latency_ratio,
                "selected_severe_policy_errors": self.selected_severe_policy_errors,
                "selected_severe_conflict_errors": self.selected_severe_conflict_errors,
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
        "cohort_size": RESEARCH_BENEFIT_COHORT_SIZE,
        "min_topic_categories": RESEARCH_BENEFIT_MIN_TOPIC_CATEGORIES,
        "min_mean_quality_uplift": RESEARCH_BENEFIT_MIN_QUALITY_UPLIFT,
        "bootstrap_95_lower_bound_strictly_greater_than": 0.0,
        "min_selected_citation_validity": RESEARCH_BENEFIT_MIN_CITATION_VALIDITY,
        "max_mean_citation_validity_decline": RESEARCH_BENEFIT_MAX_CITATION_DECLINE,
        "max_total_cost_ratio": RESEARCH_BENEFIT_MAX_COST_RATIO,
        "max_p95_latency_ratio": RESEARCH_BENEFIT_MAX_P95_LATENCY_RATIO,
        "max_selected_severe_policy_errors": 0,
        "max_selected_severe_conflict_errors": 0,
    }


__all__ = [
    "RESEARCH_BENEFIT_BOOTSTRAP_ITERATIONS",
    "RESEARCH_BENEFIT_COHORT_SCHEMA",
    "RESEARCH_BENEFIT_COHORT_SIZE",
    "RESEARCH_BENEFIT_MAX_COST_RATIO",
    "RESEARCH_BENEFIT_MAX_P95_LATENCY_RATIO",
    "RESEARCH_BENEFIT_MIN_QUALITY_UPLIFT",
    "ResearchBenefitCohortError",
    "ResearchBenefitCohortReportV1",
    "ResearchBenefitCohortSpecV1",
    "ResearchCohortPairedEvidenceV1",
    "ResearchCohortWorkloadV1",
    "ResearchQualityVerifierReceiptV1",
]
