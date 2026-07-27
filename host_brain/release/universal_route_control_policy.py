"""Privacy-safe control-route policy evaluation for release acceptance."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from host_brain.contracts.agent_runtime.control_route_execution_policy import (
    CONTROL_ROUTE_BUDGET_MODE,
    CONTROL_ROUTE_EFFECT_MODE,
    CONTROL_ROUTE_EXECUTION_MODE,
    CONTROL_ROUTE_VERIFICATION_MODE,
    validate_control_route_execution_policy,
)


CONTROL_ROUTE_POLICY_EVIDENCE_FIELDS = (
    "schema_version",
    "surface",
    "control_command_id",
    "transport_route",
    "target_id",
    "execution_owner",
    "execution_mode",
    "review_mode",
    "budget_mode",
    "verification_mode",
    "effect_mode",
    "policy_ref",
    "admission_digests_valid",
    "binding_valid",
)

_POLICY_FIELDS = frozenset(CONTROL_ROUTE_POLICY_EVIDENCE_FIELDS[:-2])
_SURFACE_ROUTES = frozenset({"scheduler", "internal", "operator"})


def evaluate_control_route_policy(
    *,
    route: str,
    raw: Mapping[str, Any],
    reasons: set[str],
) -> None:
    """Require exact frozen authority for control transports only."""

    selected = raw.get("control_route_policies", [])
    if not isinstance(selected, list) or not all(
        isinstance(item, Mapping) for item in selected
    ):
        reasons.add("CONTROL_ROUTE_POLICY_EVIDENCE_INVALID")
        return
    policies = list(selected)
    if route not in _SURFACE_ROUTES:
        if policies:
            reasons.add("CONTROL_ROUTE_POLICY_UNEXPECTED")
        return
    if len(policies) != 1:
        reasons.add("CONTROL_ROUTE_POLICY_MISSING_OR_DUPLICATE")
        return
    record = policies[0]
    if (
        set(record) != set(CONTROL_ROUTE_POLICY_EVIDENCE_FIELDS)
        or record.get("admission_digests_valid") is not True
        or record.get("binding_valid") is not True
    ):
        reasons.add("CONTROL_ROUTE_POLICY_BINDING_INVALID")
        return
    try:
        policy = validate_control_route_execution_policy(
            {key: record.get(key) for key in _POLICY_FIELDS}
        )
    except Exception:
        reasons.add("CONTROL_ROUTE_POLICY_BINDING_INVALID")
        return
    if (
        policy["surface"] != route
        or policy["execution_mode"] != CONTROL_ROUTE_EXECUTION_MODE
        or policy["budget_mode"] != CONTROL_ROUTE_BUDGET_MODE
        or policy["verification_mode"] != CONTROL_ROUTE_VERIFICATION_MODE
        or policy["effect_mode"] != CONTROL_ROUTE_EFFECT_MODE
    ):
        reasons.add("CONTROL_ROUTE_POLICY_BINDING_INVALID")


__all__ = [
    "CONTROL_ROUTE_POLICY_EVIDENCE_FIELDS",
    "evaluate_control_route_policy",
]
