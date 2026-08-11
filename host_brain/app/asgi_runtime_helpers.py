"""Small, dependency-injected helpers owned by the Host ASGI composition root."""

from __future__ import annotations

from collections.abc import Callable
from functools import partial
from typing import Any

from host_brain.app.edge_event_routes import handle_edge_events_route
from host_brain.app.schemas import EdgeEventsRequest, EdgeEventsResponse
from host_brain.app.host_life_scheduler_wiring import scheduler_seed_configs
from host_brain.input_runtime.ports import InputRuntimeApplicationPorts
from host_brain.input_runtime.worker_dispatch import _dispatch_to_graph


async def dispatch_edge_events(
    request_body: EdgeEventsRequest,
    *,
    input_runtime_ports: InputRuntimeApplicationPorts,
    ledger_provider: Callable[[], Any],
    logger: Any,
) -> EdgeEventsResponse:
    """Dispatch Edge ingress through the app-local ledger and input ports."""

    return await handle_edge_events_route(
        request_body=request_body,
        get_ledger=ledger_provider,
        logger=logger,
        input_dispatcher=partial(_dispatch_to_graph, ports=input_runtime_ports),
    )


def build_scheduler_seed_configs(
    *,
    settings: Any,
    security_immune_agent_provider: Callable[[], Any],
    logger: Any,
) -> Any:
    """Build scheduler seed definitions without recovering a global service."""

    return scheduler_seed_configs(
        settings=settings,
        security_immune_agent_provider=security_immune_agent_provider,
        logger=logger,
    )


__all__ = ["build_scheduler_seed_configs", "dispatch_edge_events"]
