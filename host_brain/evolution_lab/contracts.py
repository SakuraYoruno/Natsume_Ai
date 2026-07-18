"""Versioned, append-only and proposal-only Evolution Lab contracts."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any, Mapping

_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")


class EvolutionLabContractError(ValueError):
    """Evolution experiment evidence is invalid or unsafe to promote."""


def _text(value: Any, field: str, limit: int = 240) -> str:
    result = str(value or "").strip()
    if not result or len(result) > limit:
        raise EvolutionLabContractError(f"{field} must be non-empty and bounded")
    return result


def _digest(value: Any, field: str) -> str:
    result = _text(value, field, 64).lower()
    if not _DIGEST_RE.fullmatch(result):
        raise EvolutionLabContractError(f"{field} must be a SHA-256 digest")
    return result


def _json_copy(value: Any) -> Any:
    try:
        return json.loads(json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise EvolutionLabContractError("Evolution Lab payload must be JSON-safe") from exc


def _json_digest(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class ExperimentSpecV1:
    experiment_id: str
    dataset_digest: str
    seed: int
    budget: Mapping[str, int]
    variants: tuple[str, ...]
    verifier_ref: str
    policy_version: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "experiment_id", _text(self.experiment_id, "experiment_id", 160))
        object.__setattr__(self, "dataset_digest", _digest(self.dataset_digest, "dataset_digest"))
        if isinstance(self.seed, bool) or not isinstance(self.seed, int) or self.seed < 0:
            raise EvolutionLabContractError("seed must be a non-negative integer")
        if not isinstance(self.budget, Mapping) or not self.budget:
            raise EvolutionLabContractError("budget must be a non-empty mapping")
        budget = {str(key): value for key, value in self.budget.items()}
        if any(isinstance(value, bool) or not isinstance(value, int) or value <= 0 for value in budget.values()):
            raise EvolutionLabContractError("experiment budget values must be positive integers")
        variants = tuple(sorted({_text(item, "variants[]", 160) for item in self.variants}))
        if len(variants) < 2:
            raise EvolutionLabContractError("experiment requires at least baseline and alternative variants")
        object.__setattr__(self, "budget", budget)
        object.__setattr__(self, "variants", variants)
        object.__setattr__(self, "verifier_ref", _text(self.verifier_ref, "verifier_ref", 1_024))
        object.__setattr__(self, "policy_version", _text(self.policy_version, "policy_version", 160))

    @property
    def spec_digest(self) -> str:
        return _json_digest(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "natsume.evolution-lab.experiment-spec.v1",
            "experiment_id": self.experiment_id,
            "dataset_digest": self.dataset_digest,
            "seed": self.seed,
            "budget": _json_copy(self.budget),
            "variants": list(self.variants),
            "verifier_ref": self.verifier_ref,
            "policy_version": self.policy_version,
        }


@dataclass(frozen=True, slots=True)
class ExperimentRunReceiptV1:
    run_id: str
    spec_digest: str
    variant: str
    status: str
    evidence_digest: str
    outcome: Mapping[str, Any]
    cost: Mapping[str, int]
    risk: Mapping[str, Any]
    production_side_effects: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "run_id", _text(self.run_id, "run_id", 160))
        object.__setattr__(self, "spec_digest", _digest(self.spec_digest, "spec_digest"))
        object.__setattr__(self, "variant", _text(self.variant, "variant", 160))
        status = _text(self.status, "status", 48).upper()
        if status not in {"PASS", "FAIL", "INCOMPLETE"}:
            raise EvolutionLabContractError("unsupported experiment run status")
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "evidence_digest", _digest(self.evidence_digest, "evidence_digest"))
        object.__setattr__(self, "outcome", _json_copy(self.outcome))
        object.__setattr__(self, "risk", _json_copy(self.risk))
        if not isinstance(self.cost, Mapping) or any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in self.cost.values()):
            raise EvolutionLabContractError("experiment cost must contain non-negative integers")
        object.__setattr__(self, "cost", dict(self.cost))
        if self.production_side_effects:
            raise EvolutionLabContractError("Evolution Lab runs cannot produce production side effects")
        object.__setattr__(self, "production_side_effects", False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "natsume.evolution-lab.experiment-run-receipt.v1",
            "run_id": self.run_id,
            "spec_digest": self.spec_digest,
            "variant": self.variant,
            "status": self.status,
            "evidence_digest": self.evidence_digest,
            "outcome": _json_copy(self.outcome),
            "cost": _json_copy(self.cost),
            "risk": _json_copy(self.risk),
            "production_side_effects": False,
        }


@dataclass(frozen=True, slots=True)
class CounterfactualPairV1:
    baseline_run_id: str
    alternative_run_id: str
    outcome_delta: Mapping[str, float]
    evidence_delta: Mapping[str, float]
    cost_delta: Mapping[str, float]
    risk_delta: Mapping[str, float]

    def __post_init__(self) -> None:
        object.__setattr__(self, "baseline_run_id", _text(self.baseline_run_id, "baseline_run_id", 160))
        object.__setattr__(self, "alternative_run_id", _text(self.alternative_run_id, "alternative_run_id", 160))
        if self.baseline_run_id == self.alternative_run_id:
            raise EvolutionLabContractError("counterfactual arms must be independent")
        for name in ("outcome_delta", "evidence_delta", "cost_delta", "risk_delta"):
            value = getattr(self, name)
            if not isinstance(value, Mapping):
                raise EvolutionLabContractError(f"{name} must be a mapping")
            normalized = {str(key): float(item) for key, item in value.items()}
            object.__setattr__(self, name, normalized)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "natsume.evolution-lab.counterfactual-pair.v1",
            "baseline_run_id": self.baseline_run_id,
            "alternative_run_id": self.alternative_run_id,
            "outcome_delta": _json_copy(self.outcome_delta),
            "evidence_delta": _json_copy(self.evidence_delta),
            "cost_delta": _json_copy(self.cost_delta),
            "risk_delta": _json_copy(self.risk_delta),
        }


@dataclass(frozen=True, slots=True)
class ExperimentReportV1:
    report_id: str
    spec: ExperimentSpecV1
    runs: tuple[ExperimentRunReceiptV1, ...]
    comparisons: tuple[CounterfactualPairV1, ...]
    report_digest: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "report_id", _text(self.report_id, "report_id", 160))
        if not isinstance(self.spec, ExperimentSpecV1) or len(self.runs) < 2:
            raise EvolutionLabContractError("report requires a spec and at least two runs")
        if not all(isinstance(item, ExperimentRunReceiptV1) for item in self.runs):
            raise EvolutionLabContractError("report runs are invalid")
        if not all(item.spec_digest == self.spec.spec_digest for item in self.runs):
            raise EvolutionLabContractError("report run spec digest drift")
        if not all(isinstance(item, CounterfactualPairV1) for item in self.comparisons):
            raise EvolutionLabContractError("report comparisons are invalid")
        computed = _json_digest({"report_id": self.report_id, "spec": self.spec.to_dict(), "runs": [item.to_dict() for item in self.runs], "comparisons": [item.to_dict() for item in self.comparisons]})
        supplied = str(self.report_digest or "").strip().lower()
        if supplied and supplied != computed:
            raise EvolutionLabContractError("report_digest drift")
        object.__setattr__(self, "report_digest", computed)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "natsume.evolution-lab.experiment-report.v1",
            "report_id": self.report_id,
            "spec": self.spec.to_dict(),
            "runs": [item.to_dict() for item in self.runs],
            "comparisons": [item.to_dict() for item in self.comparisons],
            "report_digest": self.report_digest,
        }


@dataclass(frozen=True, slots=True)
class EvolutionProposalV1:
    proposal_id: str
    report_digest: str
    target_kind: str
    target_version: str
    rationale: str
    review_required: bool = True
    production_influence_enabled: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "proposal_id", _text(self.proposal_id, "proposal_id", 160))
        object.__setattr__(self, "report_digest", _digest(self.report_digest, "report_digest"))
        object.__setattr__(self, "target_kind", _text(self.target_kind, "target_kind", 120))
        object.__setattr__(self, "target_version", _text(self.target_version, "target_version", 120))
        object.__setattr__(self, "rationale", _text(self.rationale, "rationale", 4_000))
        if not self.review_required or self.production_influence_enabled:
            raise EvolutionLabContractError("Evolution proposals require Review and cannot mutate production")
        object.__setattr__(self, "review_required", True)
        object.__setattr__(self, "production_influence_enabled", False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "natsume.evolution-lab.proposal.v1",
            "proposal_id": self.proposal_id,
            "report_digest": self.report_digest,
            "target_kind": self.target_kind,
            "target_version": self.target_version,
            "rationale": self.rationale,
            "review_required": True,
            "production_influence_enabled": False,
        }


__all__ = [
    "CounterfactualPairV1",
    "EvolutionLabContractError",
    "EvolutionProposalV1",
    "ExperimentReportV1",
    "ExperimentRunReceiptV1",
    "ExperimentSpecV1",
]
