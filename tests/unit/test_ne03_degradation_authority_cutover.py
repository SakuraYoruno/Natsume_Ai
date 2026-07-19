from __future__ import annotations

from typing import Any

import pytest

from host_brain.capability_plane.broker import CapabilityBroker
from host_brain.capability_plane.policies import DescriptorCapabilityPolicy
from host_brain.core.errors import MCP_TASK_AUTHORITY_REQUIRED
from host_brain.mcp.tool_broker import MCPToolBroker
from host_brain.mcp.tool_registry import MCPToolRegistry, ToolMeta
from host_brain.runtime.degradation_authority import (
    evaluate_degradation_capability,
)


class _GuardianPort:
    def __init__(self, *, allowed: bool = True) -> None:
        self.allowed = allowed

    def evaluate_capability(self, **kwargs: Any) -> dict[str, Any]:
        return {
            "schema_version": "Natsume.degradation.capability_decision.v1",
            "capability_id": kwargs["capability_id"],
            "decision": "UNCHANGED" if self.allowed else "BLOCK",
            "allowed": self.allowed,
            "allowed_by_degradation": self.allowed,
            "reason_code": (
                "NO_ADDITIONAL_DEGRADATION_RESTRICTION"
                if self.allowed
                else "DEPENDENCY_DEGRADED_HIGH_RISK_BLOCKED"
            ),
            "durability_status": "READY",
            "guardian_authority": {
                "authority": "RUNTIME_GUARDIAN",
                "authority_status": "ACTIVE",
                "durable": True,
                "aggregate_ref": "runtime-guardian://sha256/" + "a" * 64,
                "guardian_revision": 1,
                "fencing_token": 1,
            },
        }


def _broker() -> MCPToolBroker:
    return MCPToolBroker(
        registry=MCPToolRegistry(db_path=":memory:"),
        capability_broker=CapabilityBroker(
            policy=DescriptorCapabilityPolicy()
        ),
    )


def test_missing_or_forged_degradation_authority_fails_closed() -> None:
    missing = evaluate_degradation_capability(
        None,
        capability_id="high_risk_execution",
        static_risk_level=5,
    )

    class _ForgedAllow:
        @staticmethod
        def evaluate_capability(**_kwargs: Any) -> dict[str, Any]:
            return {"allowed": True, "allowed_by_degradation": True}

    forged = evaluate_degradation_capability(
        _ForgedAllow(),
        capability_id="high_risk_execution",
        static_risk_level=5,
    )
    assert missing["allowed"] is False
    assert forged["allowed"] is False
    assert forged["reason_code"] == "DEGRADATION_STATE_UNAVAILABLE"


def test_retired_mcp_facade_stays_inert_across_exact_guardian_lifespans() -> None:
    broker = _broker()
    first_lifespan_guardian = _GuardianPort()
    second_lifespan_guardian = _GuardianPort()
    calls: list[dict[str, Any]] = []
    broker.registry.register(
        ToolMeta(
            provider_id="provider",
            tool_id="write",
            name="Write",
            description="legacy provider write",
            access_level="RESTRICTED",
            risk_level="HIGH",
            reversible=False,
        )
    )
    with pytest.raises(PermissionError, match=MCP_TASK_AUTHORITY_REQUIRED):
        broker.register_handler(
            "provider",
            "write",
            lambda args: calls.append(dict(args)) or {"executed": True},
        )

    broker.bind_degradation_capability_port(first_lifespan_guardian)
    first = broker.call(
        provider_id="provider",
        tool_id="write",
        args={"value": 1},
    )
    with pytest.raises(RuntimeError, match="ALREADY_BOUND"):
        broker.bind_degradation_capability_port(second_lifespan_guardian)
    with pytest.raises(RuntimeError, match="OWNER_MISMATCH"):
        broker.unbind_degradation_capability_port(second_lifespan_guardian)

    broker.unbind_degradation_capability_port(first_lifespan_guardian)
    broker.bind_degradation_capability_port(second_lifespan_guardian)
    second = broker.call(
        provider_id="provider",
        tool_id="write",
        args={"value": 2},
    )
    broker.unbind_degradation_capability_port(second_lifespan_guardian)

    assert first.reason_code == MCP_TASK_AUTHORITY_REQUIRED
    assert second.reason_code == MCP_TASK_AUTHORITY_REQUIRED
    assert calls == []
