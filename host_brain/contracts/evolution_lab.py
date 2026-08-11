"""Versioned, append-only and proposal-only Evolution Lab contracts."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
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
class RedactionReceiptV1:
    """Evidence binding a replay dataset to its redaction operation."""

    receipt_id: str
    source_digest: str
    rules_version: str
    output_dataset_digest: str
    redacted_fields: tuple[str, ...]
    source_kind: str
    production_side_effects: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "receipt_id", _text(self.receipt_id, "receipt_id", 160))
        object.__setattr__(self, "source_digest", _digest(self.source_digest, "source_digest"))
        object.__setattr__(self, "rules_version", _text(self.rules_version, "rules_version", 120))
        object.__setattr__(self, "output_dataset_digest", _digest(self.output_dataset_digest, "output_dataset_digest"))
        fields = tuple(sorted({_text(item, "redacted_fields[]", 160) for item in self.redacted_fields}))
        object.__setattr__(self, "redacted_fields", fields)
        object.__setattr__(self, "source_kind", _text(self.source_kind, "source_kind", 64).lower())
        if self.production_side_effects:
            raise EvolutionLabContractError("redaction cannot produce production side effects")
        object.__setattr__(self, "production_side_effects", False)

    @property
    def receipt_digest(self) -> str:
        return _json_digest(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "natsume.evolution-lab.redaction-receipt.v1",
            "receipt_id": self.receipt_id,
            "source_digest": self.source_digest,
            "rules_version": self.rules_version,
            "output_dataset_digest": self.output_dataset_digest,
            "redacted_fields": list(self.redacted_fields),
            "source_kind": self.source_kind,
            "production_side_effects": False,
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


# V1 is intentionally retained as an immutable historical/shadow contract.  V2
# is a parallel evidence boundary: it never upgrades V1 receipts into promotion
# evidence and keeps execution, sandbox and verifier identities explicit.


def canonical_evolution_lab_digest(value: Any) -> str:
    """Return the canonical JSON digest used by V2 dataset and output bindings."""

    return _json_digest(value)


def _nonnegative_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise EvolutionLabContractError(f"{field} must be a non-negative integer")
    return value


def _positive_int(value: Any, field: str) -> int:
    result = _nonnegative_int(value, field)
    if result <= 0:
        raise EvolutionLabContractError(f"{field} must be a positive integer")
    return result


def _integer_mapping(value: Any, field: str, *, allow_empty: bool = True) -> dict[str, int]:
    if not isinstance(value, Mapping) or (not value and not allow_empty):
        raise EvolutionLabContractError(f"{field} must be a mapping")
    normalized: dict[str, int] = {}
    for key, item in value.items():
        normalized[_text(key, f"{field}.key", 120)] = _nonnegative_int(item, f"{field}[{key!r}]")
    return normalized


def _delta_mapping(value: Any, field: str) -> dict[str, float]:
    if not isinstance(value, Mapping):
        raise EvolutionLabContractError(f"{field} must be a mapping")
    normalized: dict[str, float] = {}
    for key, item in value.items():
        if isinstance(item, bool) or not isinstance(item, (int, float)) or not math.isfinite(float(item)):
            raise EvolutionLabContractError(f"{field}[{key!r}] must be a finite number")
        normalized[_text(key, f"{field}.key", 120)] = float(item)
    return normalized


@dataclass(frozen=True, slots=True)
class LabDatasetV1:
    """JSON-safe Lab input.

    Synthetic records stay code-backed and ephemeral.  A trusted intake may
    persist only its independently redacted, content-addressed projection.
    """

    dataset_id: str
    records: tuple[Mapping[str, Any], ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "dataset_id", _text(self.dataset_id, "dataset_id", 160))
        records: list[Mapping[str, Any]] = []
        for index, record in enumerate(self.records):
            if not isinstance(record, Mapping):
                raise EvolutionLabContractError(f"records[{index}] must be a mapping")
            copied = _json_copy(record)
            if not isinstance(copied, Mapping):  # defensive: JSON round-trip keeps mappings.
                raise EvolutionLabContractError(f"records[{index}] must remain a mapping")
            records.append(copied)
        if not records:
            raise EvolutionLabContractError("Lab dataset must contain at least one record")
        object.__setattr__(self, "records", tuple(records))

    @property
    def dataset_digest(self) -> str:
        return canonical_evolution_lab_digest(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "natsume.evolution-lab.dataset.v1",
            "dataset_id": self.dataset_id,
            "records": [_json_copy(record) for record in self.records],
        }


@dataclass(frozen=True, slots=True)
class DatasetManifestV1:
    """Frozen identity of an isolated Lab dataset, not a claim made by a reader."""

    workspace_id: str
    dataset_id: str
    dataset_digest: str
    schema_digest: str
    record_count: int
    source_kind: str
    redaction_receipt_digest: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace_id", _text(self.workspace_id, "workspace_id", 160))
        object.__setattr__(self, "dataset_id", _text(self.dataset_id, "dataset_id", 160))
        object.__setattr__(self, "dataset_digest", _digest(self.dataset_digest, "dataset_digest"))
        object.__setattr__(self, "schema_digest", _digest(self.schema_digest, "schema_digest"))
        object.__setattr__(self, "record_count", _positive_int(self.record_count, "record_count"))
        object.__setattr__(self, "source_kind", _text(self.source_kind, "source_kind", 80).lower())
        redaction = self.redaction_receipt_digest
        object.__setattr__(
            self,
            "redaction_receipt_digest",
            _digest(redaction, "redaction_receipt_digest") if redaction is not None else None,
        )

    @property
    def manifest_digest(self) -> str:
        return canonical_evolution_lab_digest(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "natsume.evolution-lab.dataset-manifest.v1",
            "workspace_id": self.workspace_id,
            "dataset_id": self.dataset_id,
            "dataset_digest": self.dataset_digest,
            "schema_digest": self.schema_digest,
            "record_count": self.record_count,
            "source_kind": self.source_kind,
            "redaction_receipt_digest": self.redaction_receipt_digest,
        }


@dataclass(frozen=True, slots=True)
class PolicyVariantV1:
    """One immutable baseline or candidate policy/code/config binding."""

    variant_id: str
    policy_version: str
    code_version: str
    config_digest: str
    is_baseline: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "variant_id", _text(self.variant_id, "variant_id", 160))
        object.__setattr__(self, "policy_version", _text(self.policy_version, "policy_version", 160))
        object.__setattr__(self, "code_version", _text(self.code_version, "code_version", 160))
        object.__setattr__(self, "config_digest", _digest(self.config_digest, "config_digest"))
        if not isinstance(self.is_baseline, bool):
            raise EvolutionLabContractError("is_baseline must be boolean")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "natsume.evolution-lab.policy-variant.v1",
            "variant_id": self.variant_id,
            "policy_version": self.policy_version,
            "code_version": self.code_version,
            "config_digest": self.config_digest,
            "is_baseline": self.is_baseline,
        }


@dataclass(frozen=True, slots=True)
class EffectAuditReceiptV1:
    """An isolated executor's no-effect audit result, bound to one execution."""

    execution_id: str
    audit_id: str
    auditor_identity: str
    audit_evidence_digest: str
    effect_count: int
    denied_effect_count: int = 0
    production_side_effects: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "execution_id", _text(self.execution_id, "execution_id", 160))
        object.__setattr__(self, "audit_id", _text(self.audit_id, "audit_id", 160))
        object.__setattr__(self, "auditor_identity", _text(self.auditor_identity, "auditor_identity", 240))
        object.__setattr__(self, "audit_evidence_digest", _digest(self.audit_evidence_digest, "audit_evidence_digest"))
        if _nonnegative_int(self.effect_count, "effect_count") != 0:
            raise EvolutionLabContractError("Lab effect audit must report zero effects")
        object.__setattr__(self, "effect_count", 0)
        object.__setattr__(self, "denied_effect_count", _nonnegative_int(self.denied_effect_count, "denied_effect_count"))
        if self.production_side_effects:
            raise EvolutionLabContractError("Lab effect audit cannot report production side effects")
        object.__setattr__(self, "production_side_effects", False)

    @property
    def receipt_digest(self) -> str:
        return canonical_evolution_lab_digest(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "natsume.evolution-lab.effect-audit-receipt.v1",
            "execution_id": self.execution_id,
            "audit_id": self.audit_id,
            "auditor_identity": self.auditor_identity,
            "audit_evidence_digest": self.audit_evidence_digest,
            "effect_count": 0,
            "denied_effect_count": self.denied_effect_count,
            "production_side_effects": False,
        }


@dataclass(frozen=True, slots=True)
class SandboxReceiptV1:
    """Immutable facts that bind an output to a no-network, read-only Lab run."""

    execution_id: str
    sandbox_id: str
    sandbox_profile_digest: str
    dataset_digest: str
    output_digest: str
    network_disabled: bool
    readonly_dataset: bool
    temporary_output: bool
    effect_audit: EffectAuditReceiptV1

    def __post_init__(self) -> None:
        object.__setattr__(self, "execution_id", _text(self.execution_id, "execution_id", 160))
        object.__setattr__(self, "sandbox_id", _text(self.sandbox_id, "sandbox_id", 160))
        object.__setattr__(self, "sandbox_profile_digest", _digest(self.sandbox_profile_digest, "sandbox_profile_digest"))
        object.__setattr__(self, "dataset_digest", _digest(self.dataset_digest, "dataset_digest"))
        object.__setattr__(self, "output_digest", _digest(self.output_digest, "output_digest"))
        if self.network_disabled is not True:
            raise EvolutionLabContractError("Lab sandbox must have network disabled")
        if self.readonly_dataset is not True:
            raise EvolutionLabContractError("Lab sandbox must mount its dataset read-only")
        if self.temporary_output is not True:
            raise EvolutionLabContractError("Lab sandbox output must be temporary")
        if not isinstance(self.effect_audit, EffectAuditReceiptV1):
            raise EvolutionLabContractError("Lab sandbox requires an EffectAuditReceiptV1")
        if self.effect_audit.execution_id != self.execution_id:
            raise EvolutionLabContractError("Lab sandbox effect audit execution identity drift")

    @property
    def receipt_digest(self) -> str:
        return canonical_evolution_lab_digest(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "natsume.evolution-lab.sandbox-receipt.v1",
            "execution_id": self.execution_id,
            "sandbox_id": self.sandbox_id,
            "sandbox_profile_digest": self.sandbox_profile_digest,
            "dataset_digest": self.dataset_digest,
            "output_digest": self.output_digest,
            "network_disabled": True,
            "readonly_dataset": True,
            "temporary_output": True,
            "effect_audit": self.effect_audit.to_dict(),
            "effect_audit_receipt_digest": self.effect_audit.receipt_digest,
        }


@dataclass(frozen=True, slots=True)
class VerificationReceiptV1:
    """Verifier evidence must be supplied by an independently identified verifier."""

    execution_id: str
    verifier_identity: str
    verifier_version: str
    verifier_digest: str
    status: str
    evidence_digest: str
    outcome: Mapping[str, Any]
    risk: Mapping[str, Any]
    metrics: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "execution_id", _text(self.execution_id, "execution_id", 160))
        object.__setattr__(self, "verifier_identity", _text(self.verifier_identity, "verifier_identity", 240))
        object.__setattr__(self, "verifier_version", _text(self.verifier_version, "verifier_version", 160))
        object.__setattr__(self, "verifier_digest", _digest(self.verifier_digest, "verifier_digest"))
        status = _text(self.status, "status", 48).upper()
        if status not in {"PASS", "FAIL", "INCOMPLETE"}:
            raise EvolutionLabContractError("unsupported V2 verification status")
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "evidence_digest", _digest(self.evidence_digest, "evidence_digest"))
        object.__setattr__(self, "outcome", _json_copy(self.outcome))
        object.__setattr__(self, "risk", _json_copy(self.risk))
        object.__setattr__(self, "metrics", _json_copy(self.metrics))

    @property
    def receipt_digest(self) -> str:
        return canonical_evolution_lab_digest(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "natsume.evolution-lab.verification-receipt.v1",
            "execution_id": self.execution_id,
            "verifier_identity": self.verifier_identity,
            "verifier_version": self.verifier_version,
            "verifier_digest": self.verifier_digest,
            "status": self.status,
            "evidence_digest": self.evidence_digest,
            "outcome": _json_copy(self.outcome),
            "risk": _json_copy(self.risk),
            "metrics": _json_copy(self.metrics),
        }


@dataclass(frozen=True, slots=True)
class ExperimentSpecV2:
    """A workspace-bound, reproducible V2 experiment registration."""

    experiment_id: str
    workspace_id: str
    dataset_manifest: DatasetManifestV1
    seed: int
    budget: Mapping[str, int]
    variants: tuple[PolicyVariantV1, ...]
    verifier_identity: str
    verifier_version: str
    verifier_digest: str
    isolation_profile_digest: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "experiment_id", _text(self.experiment_id, "experiment_id", 160))
        object.__setattr__(self, "workspace_id", _text(self.workspace_id, "workspace_id", 160))
        if not isinstance(self.dataset_manifest, DatasetManifestV1):
            raise EvolutionLabContractError("V2 experiment requires a DatasetManifestV1")
        if self.dataset_manifest.workspace_id != self.workspace_id:
            raise EvolutionLabContractError("V2 experiment workspace and dataset manifest drift")
        if isinstance(self.seed, bool) or not isinstance(self.seed, int) or self.seed < 0:
            raise EvolutionLabContractError("seed must be a non-negative integer")
        if not isinstance(self.budget, Mapping) or not self.budget:
            raise EvolutionLabContractError("experiment budget must be a non-empty mapping")
        budget = _integer_mapping(self.budget, "budget", allow_empty=False)
        if any(value <= 0 for value in budget.values()):
            raise EvolutionLabContractError("experiment budget values must be positive integers")
        raw_variants = tuple(self.variants)
        if len(raw_variants) < 2 or not all(isinstance(item, PolicyVariantV1) for item in raw_variants):
            raise EvolutionLabContractError("V2 experiment requires at least two PolicyVariantV1 arms")
        variants = tuple(sorted(raw_variants, key=lambda item: item.variant_id))
        if len({item.variant_id for item in variants}) != len(variants):
            raise EvolutionLabContractError("V2 experiment variant IDs must be unique")
        if sum(1 for item in variants if item.is_baseline) != 1:
            raise EvolutionLabContractError("V2 experiment requires exactly one baseline arm")
        object.__setattr__(self, "budget", budget)
        object.__setattr__(self, "variants", variants)
        object.__setattr__(self, "verifier_identity", _text(self.verifier_identity, "verifier_identity", 240))
        object.__setattr__(self, "verifier_version", _text(self.verifier_version, "verifier_version", 160))
        object.__setattr__(self, "verifier_digest", _digest(self.verifier_digest, "verifier_digest"))
        object.__setattr__(self, "isolation_profile_digest", _digest(self.isolation_profile_digest, "isolation_profile_digest"))

    @property
    def spec_digest(self) -> str:
        return canonical_evolution_lab_digest(self.to_dict())

    def variant(self, variant_id: str) -> PolicyVariantV1:
        normalized = _text(variant_id, "variant_id", 160)
        for item in self.variants:
            if item.variant_id == normalized:
                return item
        raise EvolutionLabContractError("variant is not registered in ExperimentSpecV2")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "natsume.evolution-lab.experiment-spec.v2",
            "experiment_id": self.experiment_id,
            "workspace_id": self.workspace_id,
            "dataset_manifest": self.dataset_manifest.to_dict(),
            "dataset_manifest_digest": self.dataset_manifest.manifest_digest,
            "seed": self.seed,
            "budget": _json_copy(self.budget),
            "variants": [item.to_dict() for item in self.variants],
            "verifier_identity": self.verifier_identity,
            "verifier_version": self.verifier_version,
            "verifier_digest": self.verifier_digest,
            "isolation_profile_digest": self.isolation_profile_digest,
        }


@dataclass(frozen=True, slots=True)
class ExperimentRunReceiptV2:
    """Durable V2 evidence for one isolated baseline/candidate execution."""

    run_id: str
    execution_id: str
    spec_digest: str
    workspace_id: str
    dataset_manifest_digest: str
    variant_id: str
    status: str
    model_identity: str
    usage: Mapping[str, int]
    cost: Mapping[str, int]
    latency_ms: int
    sandbox_receipt: SandboxReceiptV1
    verification_receipt: VerificationReceiptV1
    production_side_effects: bool = False
    benefit_eligible: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "run_id", _text(self.run_id, "run_id", 160))
        object.__setattr__(self, "execution_id", _text(self.execution_id, "execution_id", 160))
        object.__setattr__(self, "spec_digest", _digest(self.spec_digest, "spec_digest"))
        object.__setattr__(self, "workspace_id", _text(self.workspace_id, "workspace_id", 160))
        object.__setattr__(self, "dataset_manifest_digest", _digest(self.dataset_manifest_digest, "dataset_manifest_digest"))
        object.__setattr__(self, "variant_id", _text(self.variant_id, "variant_id", 160))
        status = _text(self.status, "status", 48).upper()
        if status not in {"PASS", "FAIL", "INCOMPLETE"}:
            raise EvolutionLabContractError("unsupported V2 experiment run status")
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "model_identity", _text(self.model_identity, "model_identity", 240))
        object.__setattr__(self, "usage", _integer_mapping(self.usage, "usage"))
        object.__setattr__(self, "cost", _integer_mapping(self.cost, "cost"))
        object.__setattr__(self, "latency_ms", _nonnegative_int(self.latency_ms, "latency_ms"))
        if not isinstance(self.sandbox_receipt, SandboxReceiptV1):
            raise EvolutionLabContractError("V2 run requires a SandboxReceiptV1")
        if not isinstance(self.verification_receipt, VerificationReceiptV1):
            raise EvolutionLabContractError("V2 run requires a VerificationReceiptV1")
        if self.sandbox_receipt.execution_id != self.execution_id:
            raise EvolutionLabContractError("V2 sandbox execution identity drift")
        if self.verification_receipt.execution_id != self.execution_id:
            raise EvolutionLabContractError("V2 verifier execution identity drift")
        if self.status != self.verification_receipt.status:
            raise EvolutionLabContractError("V2 run status must match verifier status")
        if self.production_side_effects or self.benefit_eligible:
            raise EvolutionLabContractError("V2 Lab receipts cannot directly enable production influence")
        object.__setattr__(self, "production_side_effects", False)
        object.__setattr__(self, "benefit_eligible", False)

    @property
    def evidence_digest(self) -> str:
        return canonical_evolution_lab_digest(
            {
                "sandbox_receipt_digest": self.sandbox_receipt.receipt_digest,
                "verification_receipt_digest": self.verification_receipt.receipt_digest,
            }
        )

    @property
    def receipt_digest(self) -> str:
        return canonical_evolution_lab_digest(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "natsume.evolution-lab.experiment-run-receipt.v2",
            "run_id": self.run_id,
            "execution_id": self.execution_id,
            "spec_digest": self.spec_digest,
            "workspace_id": self.workspace_id,
            "dataset_manifest_digest": self.dataset_manifest_digest,
            "variant_id": self.variant_id,
            "status": self.status,
            "model_identity": self.model_identity,
            "usage": _json_copy(self.usage),
            "cost": _json_copy(self.cost),
            "latency_ms": self.latency_ms,
            "sandbox_receipt": self.sandbox_receipt.to_dict(),
            "sandbox_receipt_digest": self.sandbox_receipt.receipt_digest,
            "verification_receipt": self.verification_receipt.to_dict(),
            "verification_receipt_digest": self.verification_receipt.receipt_digest,
            "evidence_digest": self.evidence_digest,
            "production_side_effects": False,
            "benefit_eligible": False,
        }


@dataclass(frozen=True, slots=True)
class CounterfactualPairV2:
    baseline_run_id: str
    alternative_run_id: str
    outcome_delta: Mapping[str, float]
    evidence_delta: Mapping[str, float]
    cost_delta: Mapping[str, float]
    latency_delta: Mapping[str, float]
    risk_delta: Mapping[str, float]
    verification_delta: Mapping[str, float]

    def __post_init__(self) -> None:
        object.__setattr__(self, "baseline_run_id", _text(self.baseline_run_id, "baseline_run_id", 160))
        object.__setattr__(self, "alternative_run_id", _text(self.alternative_run_id, "alternative_run_id", 160))
        if self.baseline_run_id == self.alternative_run_id:
            raise EvolutionLabContractError("V2 counterfactual arms must be independent")
        for field in (
            "outcome_delta",
            "evidence_delta",
            "cost_delta",
            "latency_delta",
            "risk_delta",
            "verification_delta",
        ):
            object.__setattr__(self, field, _delta_mapping(getattr(self, field), field))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "natsume.evolution-lab.counterfactual-pair.v2",
            "baseline_run_id": self.baseline_run_id,
            "alternative_run_id": self.alternative_run_id,
            "outcome_delta": _json_copy(self.outcome_delta),
            "evidence_delta": _json_copy(self.evidence_delta),
            "cost_delta": _json_copy(self.cost_delta),
            "latency_delta": _json_copy(self.latency_delta),
            "risk_delta": _json_copy(self.risk_delta),
            "verification_delta": _json_copy(self.verification_delta),
        }


@dataclass(frozen=True, slots=True)
class ExperimentReportV2:
    report_id: str
    spec: ExperimentSpecV2
    runs: tuple[ExperimentRunReceiptV2, ...]
    comparisons: tuple[CounterfactualPairV2, ...]
    report_digest: str = ""
    promotion_eligible: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "report_id", _text(self.report_id, "report_id", 160))
        if not isinstance(self.spec, ExperimentSpecV2):
            raise EvolutionLabContractError("V2 report requires an ExperimentSpecV2")
        if not all(isinstance(item, ExperimentRunReceiptV2) for item in self.runs):
            raise EvolutionLabContractError("V2 report runs are invalid")
        expected_variants = {item.variant_id for item in self.spec.variants}
        if len(self.runs) != len(expected_variants):
            raise EvolutionLabContractError("V2 report requires one durable run for each registered arm")
        if {item.variant_id for item in self.runs} != expected_variants:
            raise EvolutionLabContractError("V2 report variant coverage drift")
        if len({item.run_id for item in self.runs}) != len(self.runs):
            raise EvolutionLabContractError("V2 report run IDs must be unique")
        for run in self.runs:
            if (
                run.spec_digest != self.spec.spec_digest
                or run.workspace_id != self.spec.workspace_id
                or run.dataset_manifest_digest != self.spec.dataset_manifest.manifest_digest
            ):
                raise EvolutionLabContractError("V2 report run binding drift")
        if not self.comparisons or not all(isinstance(item, CounterfactualPairV2) for item in self.comparisons):
            raise EvolutionLabContractError("V2 report comparisons are invalid")
        run_ids = {item.run_id for item in self.runs}
        if any(
            item.baseline_run_id not in run_ids or item.alternative_run_id not in run_ids
            for item in self.comparisons
        ):
            raise EvolutionLabContractError("V2 report comparison references an unknown run")
        baseline_variant_id = next(item.variant_id for item in self.spec.variants if item.is_baseline)
        baseline_run_id = next(item.run_id for item in self.runs if item.variant_id == baseline_variant_id)
        if not any(
            item.baseline_run_id == baseline_run_id
            and item.alternative_run_id != baseline_run_id
            for item in self.comparisons
        ):
            raise EvolutionLabContractError("V2 report requires a baseline-to-alternative comparison")
        if self.promotion_eligible:
            raise EvolutionLabContractError("V2 reports cannot directly become promotion-eligible")
        object.__setattr__(self, "promotion_eligible", False)
        computed = canonical_evolution_lab_digest(
            {
                "report_id": self.report_id,
                "spec": self.spec.to_dict(),
                "runs": [item.to_dict() for item in self.runs],
                "comparisons": [item.to_dict() for item in self.comparisons],
                "promotion_eligible": False,
            }
        )
        supplied = str(self.report_digest or "").strip().lower()
        if supplied and supplied != computed:
            raise EvolutionLabContractError("V2 report_digest drift")
        object.__setattr__(self, "report_digest", computed)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "natsume.evolution-lab.experiment-report.v2",
            "report_id": self.report_id,
            "spec": self.spec.to_dict(),
            "runs": [item.to_dict() for item in self.runs],
            "comparisons": [item.to_dict() for item in self.comparisons],
            "report_digest": self.report_digest,
            "promotion_eligible": False,
        }


@dataclass(frozen=True, slots=True)
class EvolutionProposalV2:
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
            raise EvolutionLabContractError("V2 proposals require Review and cannot mutate production")
        object.__setattr__(self, "review_required", True)
        object.__setattr__(self, "production_influence_enabled", False)

    @property
    def proposal_digest(self) -> str:
        return canonical_evolution_lab_digest(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "natsume.evolution-lab.proposal.v2",
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
    "CounterfactualPairV2",
    "DatasetManifestV1",
    "EffectAuditReceiptV1",
    "EvolutionLabContractError",
    "EvolutionProposalV1",
    "EvolutionProposalV2",
    "ExperimentReportV1",
    "ExperimentReportV2",
    "ExperimentRunReceiptV1",
    "ExperimentRunReceiptV2",
    "ExperimentSpecV1",
    "ExperimentSpecV2",
    "LabDatasetV1",
    "PolicyVariantV1",
    "RedactionReceiptV1",
    "SandboxReceiptV1",
    "VerificationReceiptV1",
    "canonical_evolution_lab_digest",
]
