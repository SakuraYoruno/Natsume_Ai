"""HTTP entry for the canonical Cortex runtime."""

from __future__ import annotations

from typing import Any, AsyncGenerator

from host_brain.app.agent_runtime_shared import AGENT_RUNTIME_PORTS
from host_brain.agent_runtime.cortex_runtime import (
    _cortex_orchestrator_sse_generator_impl,
)


async def cortex_orchestrator_sse_generator(**kwargs: Any) -> AsyncGenerator[str, None]:
    async for event in _cortex_orchestrator_sse_generator_impl(
        application_ports=AGENT_RUNTIME_PORTS,
        **kwargs,
    ):
        yield event

__all__ = ["cortex_orchestrator_sse_generator"]
