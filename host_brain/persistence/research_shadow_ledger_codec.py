"""Typed projections and strict canonical codecs for the Research shadow ledger."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
import sqlite3
from typing import Any

from host_brain.contracts.planning_search.research_benchmark_contracts import (
    ExternalProviderRunAttestationV1,
    PairedResearchRunReceiptV1,
    ResearchArmPlanV1,
    ResearchCorpusBindingV1,
    ResearchExecutionBudgetV1,
    ResearchExecutionProfileV1,
    ResearchModelExecutionReceiptV1,
    ResearchVerifierReceiptV1,
)


class ResearchShadowLedgerError(RuntimeError):
    """Durable Research evidence failed closed."""


class ResearchArmOperationState(str, Enum):
    """Durable recovery state for one external Research arm identity."""

    RESERVED = "RESERVED"
    CALL_CLAIMED = "CALL_CLAIMED"
    EXTERNAL_CALL_STARTED = "EXTERNAL_CALL_STARTED"
    RETRY_AUTHORIZED_NO_AUDIT = "RETRY_AUTHORIZED_NO_AUDIT"
    EVIDENCE_PERSISTED = "EVIDENCE_PERSISTED"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"


@dataclass(frozen=True, slots=True)
class ResearchArmOperationSnapshot:
    """Immutable operation projection reconstructed from its event chain."""

    operation_id: str
    operation_digest: str
    run_id: str
    arm_role: str
    arm_id: str
    execution_id: str
    state: ResearchArmOperationState
    event_sequence: int
    claim_id: str | None = None
    claim_owner_id: str | None = None
    claim_fencing_token: int | None = None
    claim_expires_at_ms: int | None = None
    reconciliation_reason: str | None = None


@dataclass(frozen=True, slots=True)
class ResearchPairedRunSpecSnapshot:
    """Typed, immutable projection of one preregistered Research run."""

    run_id: str
    registration_id: str
    binding: ResearchCorpusBindingV1
    profile: ResearchExecutionProfileV1
    budget: ResearchExecutionBudgetV1
    baseline: ResearchArmPlanV1
    selected: ResearchArmPlanV1
    arm_order: tuple[str, str]
    owner_randomized: bool
    randomization_algorithm: str | None
    randomization_draw: int | None
    cohort_run_key_digest: str | None


@dataclass(frozen=True, slots=True)
class ResearchArmExecutionSnapshot:
    """One reopened arm receipt reconstructed from canonical ledger JSON."""

    run_id: str
    role: str
    plan: ResearchArmPlanV1
    model: ResearchModelExecutionReceiptV1
    verifier: ResearchVerifierReceiptV1
    evidence_digest: str


@dataclass(frozen=True, slots=True)
class ResearchTerminalReceiptSnapshot:
    """One reopened immutable paired terminal receipt."""

    run_id: str
    receipt: PairedResearchRunReceiptV1
    receipt_digest: str

    @property
    def receipt_ref(self) -> str:
        return f"research-receipt://sha256/{self.receipt_digest}"


def arm_execution_snapshot_from_row(
    *,
    run_id: str,
    role: str,
    expected_plan: ResearchArmPlanV1,
    row: sqlite3.Row,
) -> ResearchArmExecutionSnapshot:
    model = research_model_receipt_from_dict(
        canonical_object(row["model_receipt_json"], label="Research model receipt")
    )
    verifier = research_verifier_receipt_from_dict(
        canonical_object(
            row["verifier_receipt_json"],
            label="Research verifier receipt",
        )
    )
    evidence_digest = operation_digest(row["evidence_digest"], field="evidence_digest")
    expected_evidence = {
        "plan": expected_plan.to_dict(),
        "model": model.to_dict(),
        "verifier": verifier.to_dict(),
    }
    if (
        str(row["arm_id"]) != expected_plan.arm_id
        or model.arm_id != expected_plan.arm_id
        or verifier.execution_id != model.execution_id
        or _digest(expected_evidence) != evidence_digest
    ):
        raise ResearchShadowLedgerError("Research arm evidence payload drift")
    return ResearchArmExecutionSnapshot(
        run_id=run_id,
        role=role,
        plan=expected_plan,
        model=model,
        verifier=verifier,
        evidence_digest=evidence_digest,
    )


def research_binding_from_dict(raw: Any) -> ResearchCorpusBindingV1:
    if not isinstance(raw, dict):
        raise ResearchShadowLedgerError("Research corpus binding payload is invalid")
    try:
        binding = ResearchCorpusBindingV1(
            snapshot_digest=raw.get("snapshot_digest"),
            corpus_ref=raw.get("corpus_ref"),
            manifest_digest=raw.get("manifest_digest"),
            source_ids=tuple(as_sequence(raw.get("source_ids"), field="source_ids")),
            source_refs=tuple(as_sequence(raw.get("source_refs"), field="source_refs")),
            source_digests=tuple(
                as_sequence(raw.get("source_digests"), field="source_digests")
            ),
            authority_tiers=tuple(
                as_sequence(raw.get("authority_tiers"), field="authority_tiers")
            ),
        )
    except (TypeError, ValueError) as exc:
        raise ResearchShadowLedgerError(
            "Research corpus binding payload is invalid"
        ) from exc
    if _canonical(binding.to_dict()) != _canonical(raw):
        raise ResearchShadowLedgerError("Research corpus binding payload drift")
    return binding


def research_profile_from_dict(raw: Any) -> ResearchExecutionProfileV1:
    if not isinstance(raw, dict):
        raise ResearchShadowLedgerError("Research execution profile payload is invalid")
    try:
        profile = ResearchExecutionProfileV1(
            prompt_digest=raw.get("prompt_digest"),
            profile_digest=raw.get("profile_digest"),
            output_schema_digest=raw.get("output_schema_digest"),
            cache_policy=raw.get("cache_policy", "NONE"),
        )
    except (TypeError, ValueError) as exc:
        raise ResearchShadowLedgerError(
            "Research execution profile payload is invalid"
        ) from exc
    if _canonical(profile.to_dict()) != _canonical(raw):
        raise ResearchShadowLedgerError("Research execution profile payload drift")
    return profile


def research_budget_from_dict(raw: Any) -> ResearchExecutionBudgetV1:
    if not isinstance(raw, dict):
        raise ResearchShadowLedgerError("Research execution budget payload is invalid")
    try:
        budget = ResearchExecutionBudgetV1(
            arm_max_tokens=raw.get("arm_max_tokens"),
            arm_max_tool_calls=raw.get("arm_max_tool_calls"),
            arm_max_wall_time_ms=raw.get("arm_max_wall_time_ms"),
            pair_max_tokens=raw.get("pair_max_tokens"),
            pair_max_wall_time_ms=raw.get("pair_max_wall_time_ms"),
            declared_only_fields=tuple(
                as_sequence(
                    raw.get("declared_only_fields"),
                    field="declared_only_fields",
                )
            ),
        )
    except (TypeError, ValueError) as exc:
        raise ResearchShadowLedgerError(
            "Research execution budget payload is invalid"
        ) from exc
    if _canonical(budget.to_dict()) != _canonical(raw):
        raise ResearchShadowLedgerError("Research execution budget payload drift")
    return budget


def research_arm_plan_from_dict(raw: Any) -> ResearchArmPlanV1:
    if not isinstance(raw, dict):
        raise ResearchShadowLedgerError("Research arm plan payload is invalid")
    try:
        plan = ResearchArmPlanV1(
            arm_id=raw.get("arm_id"),
            candidate_ids=tuple(
                as_sequence(raw.get("candidate_ids"), field="candidate_ids")
            ),
            source_ids=tuple(as_sequence(raw.get("source_ids"), field="source_ids")),
            graph_digest=raw.get("graph_digest"),
            path_digest=raw.get("path_digest"),
            generated_by=raw.get(
                "generated_by",
                "RESEARCH_SUCCESSOR_CATALOG",
            ),
        )
    except (TypeError, ValueError) as exc:
        raise ResearchShadowLedgerError("Research arm plan payload is invalid") from exc
    if _canonical(plan.to_dict()) != _canonical(raw):
        raise ResearchShadowLedgerError("Research arm plan payload drift")
    return plan


def research_provider_attestation_from_dict(
    raw: Any,
) -> ExternalProviderRunAttestationV1:
    if not isinstance(raw, dict):
        raise ResearchShadowLedgerError(
            "Research provider attestation payload is invalid"
        )
    try:
        attestation = ExternalProviderRunAttestationV1(
            execution_id=raw.get("execution_id"),
            provider=raw.get("provider"),
            model=raw.get("model"),
            route_digest=raw.get("route_digest"),
            response_digest=raw.get("response_digest"),
            total_tokens=raw.get("total_tokens"),
            cost_micros=raw.get("cost_micros"),
            provider_latency_ms=raw.get("provider_latency_ms"),
            total_latency_ms=raw.get("total_latency_ms"),
            fresh_request=raw.get("fresh_request", True),
        )
    except (TypeError, ValueError) as exc:
        raise ResearchShadowLedgerError(
            "Research provider attestation payload is invalid"
        ) from exc
    if _canonical(attestation.to_dict()) != _canonical(raw):
        raise ResearchShadowLedgerError("Research provider attestation payload drift")
    return attestation


def research_model_receipt_from_dict(
    raw: Any,
) -> ResearchModelExecutionReceiptV1:
    if not isinstance(raw, dict):
        raise ResearchShadowLedgerError("Research model receipt payload is invalid")
    raw_attestation = raw.get("attestation")
    try:
        attestation = (
            None
            if raw_attestation is None
            else research_provider_attestation_from_dict(raw_attestation)
        )
        receipt = ResearchModelExecutionReceiptV1(
            execution_id=raw.get("execution_id"),
            arm_id=raw.get("arm_id"),
            evidence_class=raw.get("evidence_class"),
            attempt_ids=tuple(
                as_sequence(raw.get("attempt_ids"), field="attempt_ids")
            ),
            prompt_digest=raw.get("prompt_digest"),
            tool_call_count=raw.get("tool_call_count"),
            total_tokens=raw.get("total_tokens"),
            cost_micros=raw.get("cost_micros"),
            attestation_ref=raw.get("attestation_ref"),
            attestation=attestation,
        )
    except (TypeError, ValueError) as exc:
        raise ResearchShadowLedgerError(
            "Research model receipt payload is invalid"
        ) from exc
    if _canonical(receipt.to_dict()) != _canonical(raw):
        raise ResearchShadowLedgerError("Research model receipt payload drift")
    return receipt


def research_verifier_receipt_from_dict(raw: Any) -> ResearchVerifierReceiptV1:
    if not isinstance(raw, dict):
        raise ResearchShadowLedgerError("Research verifier receipt payload is invalid")
    try:
        receipt = ResearchVerifierReceiptV1(
            execution_id=raw.get("execution_id"),
            citation_count=raw.get("citation_count"),
            cited_span_count=raw.get("cited_span_count"),
            citation_coverage=raw.get("citation_coverage"),
            citation_validity=raw.get("citation_validity"),
            evidence_digest=raw.get("evidence_digest"),
            fact_truth_attested=raw.get("fact_truth_attested", False),
        )
    except (TypeError, ValueError) as exc:
        raise ResearchShadowLedgerError(
            "Research verifier receipt payload is invalid"
        ) from exc
    if _canonical(receipt.to_dict()) != _canonical(raw):
        raise ResearchShadowLedgerError("Research verifier receipt payload drift")
    return receipt


def paired_receipt_from_dict(raw: Any) -> PairedResearchRunReceiptV1:
    if not isinstance(raw, dict):
        raise ResearchShadowLedgerError("Research terminal receipt payload is invalid")
    try:
        receipt = PairedResearchRunReceiptV1(
            corpus_binding_digest=raw.get("corpus_binding_digest"),
            profile_digest=raw.get("profile_digest"),
            budget_digest=raw.get("budget_digest"),
            baseline_execution_id=raw.get("baseline_execution_id"),
            selected_execution_id=raw.get("selected_execution_id"),
            baseline_model=research_model_receipt_from_dict(raw.get("baseline_model")),
            selected_model=research_model_receipt_from_dict(raw.get("selected_model")),
            baseline_verifier=research_verifier_receipt_from_dict(
                raw.get("baseline_verifier")
            ),
            selected_verifier=research_verifier_receipt_from_dict(
                raw.get("selected_verifier")
            ),
            arm_order=tuple(as_sequence(raw.get("arm_order"), field="arm_order")),
            randomized_arm_order=raw.get("randomized_arm_order"),
            production_influence_enabled=raw.get(
                "production_influence_enabled",
                False,
            ),
        )
    except (TypeError, ValueError) as exc:
        raise ResearchShadowLedgerError(
            "Research terminal receipt payload is invalid"
        ) from exc
    if _canonical(receipt.to_dict()) != _canonical(raw):
        raise ResearchShadowLedgerError("Research terminal receipt payload drift")
    return receipt


def as_sequence(value: Any, *, field: str) -> tuple[Any, ...]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(
        value,
        (list, tuple),
    ):
        raise ResearchShadowLedgerError(f"{field} must be an explicit sequence")
    return tuple(value)


def paired_arm_order(value: Any) -> tuple[str, str]:
    raw_order = as_sequence(value, field="arm_order")
    arm_order = tuple(str(item or "").strip().upper() for item in raw_order)
    if len(arm_order) != 2 or set(arm_order) != {"BASELINE", "SELECTED"}:
        raise ResearchShadowLedgerError(
            "Research paired arm_order must contain BASELINE and SELECTED exactly once"
        )
    return arm_order  # type: ignore[return-value]


def canonical_object(raw: Any, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(str(raw))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ResearchShadowLedgerError(f"{label} JSON is unreadable") from exc
    if not isinstance(value, dict) or _canonical(value) != str(raw):
        raise ResearchShadowLedgerError(f"{label} JSON is not canonical")
    return value


def run_spec_plan(run_spec: dict[str, Any], *, key: str) -> dict[str, Any]:
    value = run_spec.get(key)
    if not isinstance(value, dict):
        raise ResearchShadowLedgerError(
            "Research RunSpec is missing a paired arm plan"
        )
    return value


def operation_text(value: Any, *, field: str, limit: int) -> str:
    result = str(value or "").strip()
    if not result or len(result) > limit:
        raise ResearchShadowLedgerError(f"{field} must be non-empty and bounded")
    return result


def operation_role(value: Any) -> str:
    result = operation_text(value, field="arm_role", limit=32).upper()
    if result not in {"BASELINE", "SELECTED"}:
        raise ResearchShadowLedgerError("Research arm role is invalid")
    return result


def bounded_positive_int(value: Any, *, field: str, maximum: int) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 0 < value <= maximum
    ):
        raise ResearchShadowLedgerError(
            f"{field} must be a positive bounded integer"
        )
    return value


def non_negative_operation_int(value: Any, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ResearchShadowLedgerError(f"{field} must be a non-negative integer")
    return value


def operation_digest(value: Any, *, field: str) -> str:
    result = operation_text(value, field=field, limit=64).lower()
    if len(result) != 64 or any(char not in "0123456789abcdef" for char in result):
        raise ResearchShadowLedgerError(f"{field} must be a SHA-256 digest")
    return result


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


__all__ = [
    "ResearchArmExecutionSnapshot",
    "ResearchArmOperationSnapshot",
    "ResearchArmOperationState",
    "ResearchPairedRunSpecSnapshot",
    "ResearchShadowLedgerError",
    "ResearchTerminalReceiptSnapshot",
    "arm_execution_snapshot_from_row",
    "as_sequence",
    "bounded_positive_int",
    "canonical_object",
    "non_negative_operation_int",
    "operation_digest",
    "operation_role",
    "operation_text",
    "paired_arm_order",
    "paired_receipt_from_dict",
    "research_arm_plan_from_dict",
    "research_binding_from_dict",
    "research_budget_from_dict",
    "research_model_receipt_from_dict",
    "research_profile_from_dict",
    "research_verifier_receipt_from_dict",
    "run_spec_plan",
]
