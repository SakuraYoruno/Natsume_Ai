"""HTTP entry for the canonical Cortex runtime."""

from __future__ import annotations

from typing import Any, AsyncGenerator

from host_brain.agent_runtime.application_ports import AgentRuntimeApplicationPorts
from host_brain.agent_runtime.cortex_runtime import (
    _cortex_orchestrator_sse_generator_impl,
)


async def cortex_orchestrator_sse_generator(
    *,
    application_ports: AgentRuntimeApplicationPorts | None = None,
    **kwargs: Any,
) -> AsyncGenerator[str, None]:
    if not isinstance(application_ports, AgentRuntimeApplicationPorts):
        raise RuntimeError("HOST_AGENT_RUNTIME_PORTS_NOT_INJECTED")
    async for event in _cortex_orchestrator_sse_generator_impl(
        application_ports=application_ports,
        **kwargs,
    ):
        yield event

__all__ = ["cortex_orchestrator_sse_generator"]
