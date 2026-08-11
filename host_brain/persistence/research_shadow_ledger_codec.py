"""Canonical JSON codecs and validation helpers for the Research shadow ledger."""

from __future__ import annotations

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
from host_brain.persistence.research_shadow_ledger_models import (
    ResearchArmExecutionSnapshot,
    ResearchPairedRunSpecSnapshot,
    ResearchShadowLedgerError,
)

_OWNER_RANDOMIZED_RUN_SPEC_SCHEMA = "natsume.research-run-spec.owner-randomized.v1"
_OWNER_RANDOMIZATION_ALGORITHM = "PYTHON_SECRETS_RANDBELOW_V1"


def digest(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def owner_randomized_run_identity(
    *,
    registration_id: str,
    baseline: ResearchArmPlanV1,
    selected: ResearchArmPlanV1,
    cohort_run_key_digest: str,
) -> dict[str, Any]:
    return {
        "schema": _OWNER_RANDOMIZED_RUN_SPEC_SCHEMA,
        "registration_id": registration_id,
        "baseline": baseline.to_dict(),
        "selected": selected.to_dict(),
        "cohort_run_key_digest": operation_digest(
            cohort_run_key_digest,
            field="cohort_run_key_digest",
        ),
        "production_influence_enabled": False,
    }


def owner_randomized_arm_order(randomization_draw: Any) -> tuple[str, str]:
    if (
        isinstance(randomization_draw, bool)
        or not isinstance(randomization_draw, int)
        or randomization_draw not in {0, 1}
    ):
        raise ResearchShadowLedgerError("Research randomization draw must be 0 or 1")
    return (
        ("BASELINE", "SELECTED")
        if randomization_draw == 0
        else ("SELECTED", "BASELINE")
    )


def owner_randomized_run_payload(
    *,
    identity: dict[str, Any],
    arm_order: tuple[str, str],
    randomization_draw: int,
) -> dict[str, Any]:
    expected_arm_order = owner_randomized_arm_order(randomization_draw)
    if paired_arm_order(arm_order) != expected_arm_order:
        raise ResearchShadowLedgerError("Research randomization draw and arm order drift")
    return {
        **identity,
        "randomization_algorithm": _OWNER_RANDOMIZATION_ALGORITHM,
        "randomization_draw": randomization_draw,
        "arm_order": list(expected_arm_order),
        "randomized_arm_order": True,
        "owner_randomized": True,
    }


def assert_owner_randomized_run_replay(
    *,
    raw: dict[str, Any],
    identity: dict[str, Any],
) -> dict[str, Any]:
    randomization_draw = raw.get("randomization_draw")
    arm_order = paired_arm_order(raw.get("arm_order"))
    expected = owner_randomized_run_payload(
        identity=identity,
        arm_order=arm_order,
        randomization_draw=randomization_draw,
    )
    if canonical(raw) != canonical(expected):
        raise ResearchShadowLedgerError("Research RunSpec replay drift")
    return expected


def paired_run_spec_from_row(row: sqlite3.Row) -> ResearchPairedRunSpecSnapshot:
    registration_id = operation_text(
        row["registration_id"],
        field="registration_id",
        limit=255,
    )
    run_id = operation_text(row["run_id"], field="run_id", limit=255)
    registration = canonical_object(
        row["registration_json"],
        label="Research registration",
    )
    run_spec = canonical_object(row["spec_json"], label="Research RunSpec")
    binding = research_binding_from_dict(registration.get("binding"))
    profile = research_profile_from_dict(registration.get("profile"))
    budget = research_budget_from_dict(registration.get("budget"))
    expected_registration = {
        "schema": "natsume.research-benchmark.shadow.v1",
        "registration_id": registration_id,
        "binding": binding.to_dict(),
        "profile": profile.to_dict(),
        "budget": budget.to_dict(),
        "production_influence_enabled": False,
    }
    if (
        canonical(registration) != canonical(expected_registration)
        or str(row["binding_digest"]) != binding.binding_digest
        or str(row["profile_digest"]) != profile.profile_digest
        or str(row["budget_digest"]) != budget.budget_digest
        or int(row["registration_production_influence_enabled"]) != 0
    ):
        raise ResearchShadowLedgerError("Research registration payload drift")
    baseline = research_arm_plan_from_dict(run_spec.get("baseline"))
    selected = research_arm_plan_from_dict(run_spec.get("selected"))
    arm_order = paired_arm_order(run_spec.get("arm_order"))
    owner_randomized = False
    randomization_algorithm: str | None = None
    randomization_draw: int | None = None
    cohort_run_key_digest: str | None = None
    if "schema" not in run_spec:
        expected_run_spec = {
            "registration_id": registration_id,
            "baseline": baseline.to_dict(),
            "selected": selected.to_dict(),
            "arm_order": list(arm_order),
            "randomized_arm_order": True,
            "production_influence_enabled": False,
        }
        expected_run_id = f"researchrun_{digest(expected_run_spec)[:32]}"
    else:
        cohort_run_key_digest = operation_digest(
            run_spec.get("cohort_run_key_digest"),
            field="cohort_run_key_digest",
        )
        randomization_algorithm = operation_text(
            run_spec.get("randomization_algorithm"),
            field="randomization_algorithm",
            limit=128,
        )
        if randomization_algorithm != _OWNER_RANDOMIZATION_ALGORITHM:
            raise ResearchShadowLedgerError("Research randomization algorithm drift")
        randomization_draw = run_spec.get("randomization_draw")
        identity = owner_randomized_run_identity(
            registration_id=registration_id,
            baseline=baseline,
            selected=selected,
            cohort_run_key_digest=cohort_run_key_digest,
        )
        expected_run_spec = owner_randomized_run_payload(
            identity=identity,
            arm_order=arm_order,
            randomization_draw=randomization_draw,
        )
        expected_run_id = f"researchrun_{digest(identity)[:32]}"
        owner_randomized = True
    if (
        canonical(run_spec) != canonical(expected_run_spec)
        or str(row["spec_digest"]) != digest(expected_run_spec)
        or run_id != expected_run_id
        or str(row["baseline_arm_id"]) != baseline.arm_id
        or str(row["selected_arm_id"]) != selected.arm_id
        or int(row["run_randomized_arm_order"]) != 1
        or int(row["run_production_influence_enabled"]) != 0
    ):
        raise ResearchShadowLedgerError("Research RunSpec payload drift")
    return ResearchPairedRunSpecSnapshot(
        run_id=run_id,
        registration_id=registration_id,
        binding=binding,
        profile=profile,
        budget=budget,
        baseline=baseline,
        selected=selected,
        arm_order=arm_order,
        owner_randomized=owner_randomized,
        randomization_algorithm=randomization_algorithm,
        randomization_draw=randomization_draw,
        cohort_run_key_digest=cohort_run_key_digest,
    )


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
        canonical_object(row["verifier_receipt_json"], label="Research verifier receipt")
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
        or digest(expected_evidence) != evidence_digest
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
        raise ResearchShadowLedgerError("Research corpus binding payload is invalid") from exc
    if canonical(binding.to_dict()) != canonical(raw):
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
        raise ResearchShadowLedgerError("Research execution profile payload is invalid") from exc
    if canonical(profile.to_dict()) != canonical(raw):
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
                as_sequence(raw.get("declared_only_fields"), field="declared_only_fields")
            ),
        )
    except (TypeError, ValueError) as exc:
        raise ResearchShadowLedgerError("Research execution budget payload is invalid") from exc
    if canonical(budget.to_dict()) != canonical(raw):
        raise ResearchShadowLedgerError("Research execution budget payload drift")
    return budget


def research_arm_plan_from_dict(raw: Any) -> ResearchArmPlanV1:
    if not isinstance(raw, dict):
        raise ResearchShadowLedgerError("Research arm plan payload is invalid")
    try:
        plan = ResearchArmPlanV1(
            arm_id=raw.get("arm_id"),
            candidate_ids=tuple(as_sequence(raw.get("candidate_ids"), field="candidate_ids")),
            source_ids=tuple(as_sequence(raw.get("source_ids"), field="source_ids")),
            graph_digest=raw.get("graph_digest"),
            path_digest=raw.get("path_digest"),
            prompt_digest=raw.get("prompt_digest"),
            generated_by=raw.get("generated_by", "RESEARCH_SUCCESSOR_CATALOG"),
        )
    except (TypeError, ValueError) as exc:
        raise ResearchShadowLedgerError("Research arm plan payload is invalid") from exc
    if canonical(plan.to_dict()) != canonical(raw):
        raise ResearchShadowLedgerError("Research arm plan payload drift")
    return plan


def research_provider_attestation_from_dict(raw: Any) -> ExternalProviderRunAttestationV1:
    if not isinstance(raw, dict):
        raise ResearchShadowLedgerError("Research provider attestation payload is invalid")
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
            transport_identity_digest=raw.get("transport_identity_digest"),
            transport_external=raw.get("transport_external"),
            fresh_request=raw.get("fresh_request", True),
        )
    except (TypeError, ValueError) as exc:
        raise ResearchShadowLedgerError(
            "Research provider attestation payload is invalid"
        ) from exc
    if canonical(attestation.to_dict()) != canonical(raw):
        raise ResearchShadowLedgerError("Research provider attestation payload drift")
    return attestation


def research_model_receipt_from_dict(raw: Any) -> ResearchModelExecutionReceiptV1:
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
            attempt_ids=tuple(as_sequence(raw.get("attempt_ids"), field="attempt_ids")),
            prompt_digest=raw.get("prompt_digest"),
            tool_call_count=raw.get("tool_call_count"),
            total_tokens=raw.get("total_tokens"),
            cost_micros=raw.get("cost_micros"),
            attestation_ref=raw.get("attestation_ref"),
            attestation=attestation,
        )
    except (TypeError, ValueError) as exc:
        raise ResearchShadowLedgerError("Research model receipt payload is invalid") from exc
    normalized_raw = _demote_legacy_transport_benefit(raw)
    if canonical(receipt.to_dict()) != canonical(normalized_raw):
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
        raise ResearchShadowLedgerError("Research verifier receipt payload is invalid") from exc
    if canonical(receipt.to_dict()) != canonical(raw):
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
            production_influence_enabled=raw.get("production_influence_enabled", False),
        )
    except (TypeError, ValueError) as exc:
        raise ResearchShadowLedgerError("Research terminal receipt payload is invalid") from exc
    normalized_raw = _demote_legacy_transport_benefit(raw)
    if canonical(receipt.to_dict()) != canonical(normalized_raw):
        raise ResearchShadowLedgerError("Research terminal receipt payload drift")
    return receipt


def _demote_legacy_transport_benefit(raw: dict[str, Any]) -> dict[str, Any]:
    """Read historical V1 receipts without preserving their old eligibility.

    Before transport identity existed, ``benefit_eligible`` could be true
    based only on a provider name and usage.  Those append-only rows remain
    readable, but the derived boolean is downgraded during decoding.  No
    evidence field or digest is fabricated.
    """

    normalized = json.loads(json.dumps(raw, ensure_ascii=False))

    def _demote_model(value: Any) -> bool:
        if not isinstance(value, dict):
            return False
        attestation = value.get("attestation")
        if (
            isinstance(attestation, dict)
            and value.get("benefit_eligible") is True
            and "transport_identity_digest" not in attestation
            and "transport_external" not in attestation
        ):
            value["benefit_eligible"] = False
            return True
        return False

    if _demote_model(normalized):
        return normalized
    demoted = False
    for key in ("baseline_model", "selected_model"):
        demoted = _demote_model(normalized.get(key)) or demoted
    if demoted:
        normalized["benefit_eligible"] = False
    return normalized


def as_sequence(value: Any, *, field: str) -> tuple[Any, ...]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, (list, tuple)):
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
    if not isinstance(value, dict) or canonical(value) != str(raw):
        raise ResearchShadowLedgerError(f"{label} JSON is not canonical")
    return value


def run_spec_plan(run_spec: dict[str, Any], *, key: str) -> dict[str, Any]:
    value = run_spec.get(key)
    if not isinstance(value, dict):
        raise ResearchShadowLedgerError("Research RunSpec is missing a paired arm plan")
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
    if isinstance(value, bool) or not isinstance(value, int) or not 0 < value <= maximum:
        raise ResearchShadowLedgerError(f"{field} must be a positive bounded integer")
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


__all__ = [
    "arm_execution_snapshot_from_row",
    "assert_owner_randomized_run_replay",
    "bounded_positive_int",
    "canonical",
    "canonical_object",
    "digest",
    "non_negative_operation_int",
    "operation_digest",
    "operation_role",
    "operation_text",
    "owner_randomized_arm_order",
    "owner_randomized_run_identity",
    "owner_randomized_run_payload",
    "paired_receipt_from_dict",
    "paired_run_spec_from_row",
    "run_spec_plan",
]
