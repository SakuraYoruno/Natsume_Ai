"""Retired compatibility facade for nested Graph/ReAct tool execution.

Tool selection performed inside an already executing Agent node is a proposal,
not executable authority.  A future planner may compile that proposal into a
new immutable TaskGraph revision.  This compatibility object intentionally
does not retain a Broker, register descriptors, or invoke callbacks.
"""

from __future__ import annotations

from typing import Any


REACT_TOOL_GOVERNED_ADMISSION_REQUIRED = (
    "REACT_TOOL_GOVERNED_ADMISSION_REQUIRED"
)


class GovernedToolNode:
    """Fail closed for every legacy nested Graph tool invocation."""

    def __init__(
        self,
        *,
        tools: list[Any] | None = None,
        governor: Any = None,
        evidence_plane: Any = None,
        governor_rules_cache: Any = None,
        capability_broker: Any = None,
    ) -> None:
        # Compatibility inputs are deliberately discarded.  In particular,
        # retaining a callback or Broker here would recreate the retired
        # execution authority.
        del tools, governor, evidence_plane, governor_rules_cache, capability_broker

    def __call__(self, state: dict[str, Any]) -> dict[str, Any]:
        tool_calls = list(state.get("tool_calls") or [])
        results: list[dict[str, Any]] = []
        for raw in tool_calls:
            call = dict(raw) if isinstance(raw, dict) else {}
            tool_name = str(call.get("tool") or "").strip()
            results.append(
                {
                    "tool": tool_name,
                    "result": {
                        "error": REACT_TOOL_GOVERNED_ADMISSION_REQUIRED,
                        "reason_code": REACT_TOOL_GOVERNED_ADMISSION_REQUIRED,
                        "result": None,
                        "side_effects_executed": False,
                        "required_execution_surface": (
                            "new immutable TaskGraph revision"
                        ),
                    },
                }
            )
        return {
            "committed_route_token": REACT_TOOL_GOVERNED_ADMISSION_REQUIRED,
            "tool_results": results,
        }


__all__ = ["GovernedToolNode", "REACT_TOOL_GOVERNED_ADMISSION_REQUIRED"]
