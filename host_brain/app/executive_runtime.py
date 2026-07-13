"""Application composition adapter for the canonical Executive Runtime."""

from __future__ import annotations

from typing import Any, AsyncGenerator

from host_brain.executive_runtime.conversation_execution import (
    build_runtime_capability_handoff_extra,
    record_executive_handoff as _record_executive_handoff,
    record_runtime_capability_execution as _record_runtime_capability_execution,
)
from host_brain.executive_runtime.runtime import (
    stream_chat_route,
)

from host_brain.executive_runtime.runtime import (
    _executive_runtime_core_impl,
)
from host_brain.executive_runtime.ports import ExecutiveRuntimeApplicationPorts


def _build_executive_ports() -> ExecutiveRuntimeApplicationPorts:
    from host_brain.app.runtime_port_wiring import (  # noqa: PLC0415
        build_executive_runtime_application_ports,
    )

    return build_executive_runtime_application_ports()


def record_executive_handoff(**kwargs: Any) -> None:
    _record_executive_handoff(ports=_build_executive_ports(), **kwargs)


def record_runtime_capability_execution(**kwargs: Any) -> None:
    _record_runtime_capability_execution(ports=_build_executive_ports(), **kwargs)


async def executive_runtime_sse_generator(**kwargs: Any) -> AsyncGenerator[str, None]:
    """Application-composed entry for the framework-independent runtime."""

    async for event in _executive_runtime_core_impl(
        ports=_build_executive_ports(),
        **kwargs,
    ):
        yield event


__all__ = [
    "executive_runtime_sse_generator",
    "record_executive_handoff",
    "build_runtime_capability_handoff_extra",
    "record_runtime_capability_execution",
    "stream_chat_route",
]
