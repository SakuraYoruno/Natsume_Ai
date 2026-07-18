"""Explicit application-local authority for dependency degradation gates.

Production front doors must receive :class:`RuntimeGuardian` (or another
structurally compatible durable authority) from their application
composition.  The historical module-global reducer is deliberately absent
from the default path.  A narrowly named adapter remains available for old
tests and migration-only compatibility code; callers have to opt into it.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol, runtime_checkable


DEGRADATION_AUTHORITY_UNAVAILABLE = "DEGRADATION_STATE_UNAVAILABLE"


@runtime_checkable
class DegradationCapabilityPort(Protocol):
    """Read-only, fail-closed capability decision authority."""

    def evaluate_capability(
        self,
        *,
        capability_id: str,
        static_risk_level: int,
        capability_policy_decision: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]: ...


def unavailable_degradation_decision(
    *,
    capability_id: str,
    detail: str = "app-local RuntimeGuardian capability authority is unavailable",
) -> dict[str, Any]:
    """Return the canonical denial used when no valid authority is injected."""

    return {
        "schema_version": "Natsume.degradation.capability_decision.v1",
        "capability_id": str(capability_id or "").strip(),
        "decision": "BLOCK",
        "allowed": False,
        "allowed_by_degradation": False,
        "reason_code": DEGRADATION_AUTHORITY_UNAVAILABLE,
        "detail": str(detail),
        "durability_required": True,
        "durability_status": "UNAVAILABLE",
        "guardian_authority": None,
    }


def evaluate_degradation_capability(
    port: DegradationCapabilityPort | None,
    *,
    capability_id: str,
    static_risk_level: int,
    capability_policy_decision: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate through one injected durable authority, failing closed.

    A result that claims to allow execution is accepted only when it carries
    the RuntimeGuardian's durable, active authority identity.  This prevents a
    permissive duck-typed callback from silently replacing the canonical
    degradation writer.
    """

    capability = str(capability_id or "").strip()
    evaluator = getattr(port, "evaluate_capability", None)
    if not capability or not callable(evaluator):
        return unavailable_degradation_decision(capability_id=capability)
    try:
        raw = evaluator(
            capability_id=capability,
            static_risk_level=int(static_risk_level),
            capability_policy_decision=(
                dict(capability_policy_decision)
                if isinstance(capability_policy_decision, Mapping)
                else None
            ),
        )
    except Exception as exc:
        return unavailable_degradation_decision(
            capability_id=capability,
            detail=f"app-local degradation authority failed: {type(exc).__name__}",
        )
    if not isinstance(raw, Mapping):
        return unavailable_degradation_decision(
            capability_id=capability,
            detail="app-local degradation authority returned an invalid decision",
        )
    decision = dict(raw)
    allowed_raw = decision.get("allowed", decision.get("allowed_by_degradation"))
    if type(allowed_raw) is not bool:
        return unavailable_degradation_decision(
            capability_id=capability,
            detail="app-local degradation authority omitted a boolean decision",
        )
    decision["allowed"] = allowed_raw
    decision["allowed_by_degradation"] = allowed_raw
    if not allowed_raw:
        decision.setdefault("decision", "BLOCK")
        decision.setdefault("reason_code", DEGRADATION_AUTHORITY_UNAVAILABLE)
        return decision

    if bool(getattr(port, "legacy_test_only", False)):
        decision.setdefault("durability_required", False)
        decision.setdefault("durability_status", "LEGACY_TEST_ONLY")
        return decision

    authority = decision.get("guardian_authority")
    if not isinstance(authority, Mapping):
        return unavailable_degradation_decision(
            capability_id=capability,
            detail="allow decision lacks RuntimeGuardian authority",
        )
    authority = dict(authority)
    try:
        guardian_revision = int(authority.get("guardian_revision") or 0)
        fencing_token = int(authority.get("fencing_token") or 0)
    except (TypeError, ValueError):
        return unavailable_degradation_decision(
            capability_id=capability,
            detail="allow decision carries malformed RuntimeGuardian coordinates",
        )
    if (
        str(authority.get("authority") or "") != "RUNTIME_GUARDIAN"
        or str(authority.get("authority_status") or "") != "ACTIVE"
        or authority.get("durable") is not True
        or str(decision.get("durability_status") or "") != "READY"
        or not str(authority.get("aggregate_ref") or "").startswith(
            "runtime-guardian://sha256/"
        )
        or guardian_revision < 0
        or fencing_token < 1
    ):
        return unavailable_degradation_decision(
            capability_id=capability,
            detail="allow decision carries invalid RuntimeGuardian authority",
        )
    return decision


class LegacyModuleGlobalDegradationAdapter:
    """Opt-in compatibility adapter for legacy tests and migration probes.

    This type must never be installed by production composition.  Its explicit
    name keeps any remaining global-reducer dependency searchable.
    """

    legacy_test_only = True

    def evaluate_capability(
        self,
        *,
        capability_id: str,
        static_risk_level: int,
        capability_policy_decision: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]:
        from host_brain.observability.degradation_state import (  # noqa: PLC0415
            evaluate_degradation_front_door,
        )

        return evaluate_degradation_front_door(
            capability_id=capability_id,
            static_risk_level=static_risk_level,
            capability_policy_decision=capability_policy_decision,
        )


__all__ = [
    "DEGRADATION_AUTHORITY_UNAVAILABLE",
    "DegradationCapabilityPort",
    "LegacyModuleGlobalDegradationAdapter",
    "evaluate_degradation_capability",
    "unavailable_degradation_decision",
]
