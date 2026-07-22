"""Retired global Agent Runtime port module.

Production code must use :mod:`host_brain.app.agent_runtime_composition` and
receive one explicit app-local port bundle from the Host ASGI composition.
"""

from host_brain.app.agent_runtime_composition import (
    AgentRuntimeCompositionError,
    build_agent_runtime_application_ports,
)

__all__ = [
    "AgentRuntimeCompositionError",
    "build_agent_runtime_application_ports",
]
