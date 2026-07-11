"""Restrict-only Governor adapter for capability maturity scorecards."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import os
from typing import Any


CAPABILITY_POLICY_DECISION_SCHEMA_VERSION = "Natsume.capability.policy_decision.v1"
CAPABILITY_MATURITY_POLICY_ENV = "HOST_CAPABILITY_MATURITY_POLICY_ENABLED"


def _env_bool(name: str, *, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return str(raw).strip().lower() not in {"0", "false", "no", "off", ""}


def _value(source: Any, name: str, default: Any = None) -> Any:
    if isinstance(source, Mapping):
        return source.get(name, default)
    return getattr(source, name, default)


def _risk(value: Any) -> int:
    try:
        resolved = int(value)
    except (TypeError, ValueError):
        return 5
    return resolved if 1 <= resolved <= 5 else 5


def _maturity(value: Any) -> int:
    if isinstance(value, str) and value.strip().upper().startswith("L"):
        value = value.strip()[1:]
    try:
        return max(0, min(5, int(value)))
    except (TypeError, ValueError):
        # Unknown/malformed maturity fails closed when the policy is enabled.
        return 0


@dataclass(frozen=True, slots=True)
class CapabilityPolicyDecision:
    capability_id: str
    enabled: bool
    static_risk: int
    maturity_level: int | None
    maturity_risk_floor: int
    effective_risk: int
    tightened: bool
    reasons: tuple[str, ...]
    schema_version: str = CAPABILITY_POLICY_DECISION_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "capability_id": self.capability_id,
            "enabled": self.enabled,
            "static_risk": self.static_risk,
            "maturity_level": None if self.maturity_level is None else f"L{self.maturity_level}",
            "maturity_risk_floor": self.maturity_risk_floor,
            "effective_risk": self.effective_risk,
            "tightened": self.tightened,
            "reasons": list(self.reasons),
            "can_relax_static_policy": False,
            "read_only": True,
        }


class CapabilityGovernorPolicyAdapter:
    """Translate maturity into an additional risk floor, never a discount."""

    # L0 is unavailable, L1 is lab-only, and L2 is human-supervised.  They
    # therefore enter AIRLOCK.  L3+ may use the existing static policy, never
    # anything less restrictive than it.
    _MATURITY_RISK_FLOOR = {0: 5, 1: 5, 2: 5, 3: 1, 4: 1, 5: 1}

    def __init__(
        self,
        *,
        enabled: bool | None = None,
        scorecard_provider: Callable[[str], Any | None] | None = None,
    ) -> None:
        self.enabled = _env_bool(CAPABILITY_MATURITY_POLICY_ENV, default=False) if enabled is None else bool(enabled)
        self.scorecard_provider = scorecard_provider

    def get_scorecard(self, capability_id: str) -> Any | None:
        if self.scorecard_provider is None:
            return None
        try:
            return self.scorecard_provider(str(capability_id or "").strip())
        except Exception:
            # Provider failure must never restore the less restrictive static
            # route while the maturity policy is explicitly enabled.
            return None

    def evaluate(self, meta: Any, *, scorecard: Any | None = None) -> CapabilityPolicyDecision:
        capability_id = str(_value(meta, "skill_name", _value(meta, "capability_id", "")) or "").strip()
        static_risk = _risk(_value(meta, "risk_level", 5))
        if not self.enabled:
            return CapabilityPolicyDecision(
                capability_id=capability_id,
                enabled=False,
                static_risk=static_risk,
                maturity_level=None,
                maturity_risk_floor=static_risk,
                effective_risk=static_risk,
                tightened=False,
                reasons=("FEATURE_DISABLED_STATIC_POLICY_PRESERVED",),
            )

        resolved = scorecard if scorecard is not None else self.get_scorecard(capability_id)
        if resolved is None:
            return CapabilityPolicyDecision(
                capability_id=capability_id,
                enabled=True,
                static_risk=static_risk,
                maturity_level=0,
                maturity_risk_floor=5,
                effective_risk=5,
                tightened=static_risk < 5,
                reasons=("NO_SCORECARD_FAIL_CLOSED",),
            )

        scorecard_capability = str(_value(resolved, "capability_id", "") or "").strip()
        schema_version = str(_value(resolved, "schema_version", "") or "").strip()
        policy_effect = str(_value(resolved, "policy_effect", "") or "").strip().upper()
        read_only = bool(_value(resolved, "read_only", False))
        if (
            not capability_id
            or scorecard_capability != capability_id
            or schema_version != "Natsume.capability.scorecard.v1"
            or policy_effect != "RESTRICT_ONLY"
            or not read_only
        ):
            # A mismatched scorecard is not authority.  Fail closed by using
            # maximum risk rather than trusting cross-capability evidence.
            return CapabilityPolicyDecision(
                capability_id=capability_id,
                enabled=True,
                static_risk=static_risk,
                maturity_level=0,
                maturity_risk_floor=5,
                effective_risk=5,
                tightened=static_risk < 5,
                reasons=("SCORECARD_BINDING_INVALID_FAIL_CLOSED",),
            )

        maturity = _maturity(
            _value(resolved, "maturity_level_value", _value(resolved, "maturity_level", 0))
        )
        scorecard_static_risk = _risk(
            _value(resolved, "canonical_risk", _value(resolved, "static_risk_floor", static_risk))
        )
        declared_ceiling = _maturity(_value(resolved, "static_maturity_ceiling", 0))
        expected_ceiling = {1: 5, 2: 5, 3: 4, 4: 2, 5: 1}[scorecard_static_risk]
        if declared_ceiling > expected_ceiling or maturity > declared_ceiling:
            return CapabilityPolicyDecision(
                capability_id=capability_id,
                enabled=True,
                static_risk=static_risk,
                maturity_level=0,
                maturity_risk_floor=5,
                effective_risk=5,
                tightened=static_risk < 5,
                reasons=("SCORECARD_STATIC_CEILING_INVALID_FAIL_CLOSED",),
            )
        maturity_floor = self._MATURITY_RISK_FLOOR[maturity]
        effective = max(static_risk, scorecard_static_risk, maturity_floor)
        reasons = [f"MATURITY_L{maturity}_RISK_FLOOR_{maturity_floor}"]
        if scorecard_static_risk > static_risk:
            reasons.append("FEDERATED_STATIC_RISK_TIGHTENED")
        if effective == static_risk:
            reasons.append("STATIC_POLICY_PRESERVED")
        return CapabilityPolicyDecision(
            capability_id=capability_id,
            enabled=True,
            static_risk=static_risk,
            maturity_level=maturity,
            maturity_risk_floor=maturity_floor,
            effective_risk=effective,
            tightened=effective > static_risk,
            reasons=tuple(reasons),
        )

    def effective_risk(self, meta: Any, *, scorecard: Any | None = None) -> int:
        return self.evaluate(meta, scorecard=scorecard).effective_risk


__all__ = [
    "CAPABILITY_MATURITY_POLICY_ENV",
    "CAPABILITY_POLICY_DECISION_SCHEMA_VERSION",
    "CapabilityGovernorPolicyAdapter",
    "CapabilityPolicyDecision",
]
