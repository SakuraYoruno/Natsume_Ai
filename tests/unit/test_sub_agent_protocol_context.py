from __future__ import annotations

import asyncio

import host_brain.app.sub_agent_protocol as sub_agent_protocol

from host_brain.app.sub_agent_protocol import (
    LEGACY_SUB_AGENT_DISPATCH_RETIRED,
    SubAgentDispatcher,
    SubAgentRequest,
)


def test_direct_sub_agent_dispatch_is_rejected_without_runtime_state() -> None:
    dispatcher = SubAgentDispatcher()

    response = asyncio.run(
        dispatcher.dispatch(
            SubAgentRequest(
                agent_id="translate_agent",
                task="translate this",
                request_id="req-retired-dispatch-001",
                context={"gateway": "must-not-be-consumed"},
            )
        )
    )

    assert response.success is False
    assert response.error == LEGACY_SUB_AGENT_DISPATCH_RETIRED
    assert response.latency_ms == 0
    assert response.audit_refs == {
        "status": "REJECTED",
        "reason_code": LEGACY_SUB_AGENT_DISPATCH_RETIRED,
        "required_path": "HostCommand -> immutable TaskGraph -> GovernedDelegateDispatcher",
        "side_effects_executed": False,
    }
    assert not hasattr(dispatcher, "_handler_map")


def test_retired_dispatcher_has_no_service_accessor_or_private_reactivation() -> None:
    dispatcher = SubAgentDispatcher()

    assert not hasattr(sub_agent_protocol, "get_sub_agent_dispatcher")
    assert not hasattr(dispatcher, "_ensure_handlers")
    assert not hasattr(dispatcher, "_ensure_runtime_gateway")
    assert not hasattr(dispatcher, "_log_dispatch")
