"""Test-only degradation port backed by the isolated global state machine."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

class GlobalDegradationTestPort:
    """Opt-in fake for contracts that exercise the legacy state-machine seam."""

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


__all__ = ["GlobalDegradationTestPort"]
