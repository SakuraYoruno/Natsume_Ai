"""App-local composition for the governed Surface reply runtime.

The Surface adapter owns reply construction, but it must not rediscover Host
services through the mutable process service locator.  This module binds the
already-published Agent Runtime and Life authorities once during the Host
lifespan, then exposes only the small immutable ``SurfaceRuntimePorts`` bundle
to frozen governed Chat nodes.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from host_brain.agent_runtime.application_ports import AgentRuntimeApplicationPorts
from host_brain.app.context.conversation_belief_tracker import (
    build_conversation_understanding_pack,
)
from host_brain.app.context.memory_context_orchestrator import (
    build_memory_context_debug,
)
from host_brain.app.settings import get_persistence_settings
from host_brain.app.surface_context_packet import build_surface_context_packet
from host_brain.app.user_preference_store import build_user_preference_pack
from host_brain.surface_runtime.ports import SurfaceRuntimePorts


LifeAuthorityProvider = Callable[[], tuple[Any, Any]]


class SurfaceRuntimeCompositionError(RuntimeError):
    """Raised when a governed Chat route has no lifespan-owned authority."""


def build_surface_runtime_ports(
    *,
    agent_runtime_ports: AgentRuntimeApplicationPorts,
    life_authority_provider: LifeAuthorityProvider,
) -> SurfaceRuntimePorts:
    """Bind explicit Surface collaborators for one Host lifespan.

    ``life_authority_provider`` deliberately resolves at request execution
    time.  The Host can withdraw it during shutdown, while every request still
    receives the same app-local authority pair rather than a process-global
    lookup.
    """

    if not isinstance(agent_runtime_ports, AgentRuntimeApplicationPorts):
        raise SurfaceRuntimeCompositionError("HOST_AGENT_RUNTIME_PORTS_NOT_INJECTED")
    if not callable(life_authority_provider):
        raise SurfaceRuntimeCompositionError("HOST_SURFACE_LIFE_AUTHORITY_MISSING")

    def resolve_life_authority() -> tuple[Any, Any]:
        try:
            command_service, snapshot_reader = life_authority_provider()
        except Exception as exc:
            raise SurfaceRuntimeCompositionError(
                "HOST_SURFACE_LIFE_AUTHORITY_UNAVAILABLE"
            ) from exc
        if command_service is None or snapshot_reader is None:
            raise SurfaceRuntimeCompositionError(
                "HOST_SURFACE_LIFE_AUTHORITY_UNAVAILABLE"
            )
        return command_service, snapshot_reader

    def get_life_command_service() -> Any:
        command_service, _ = resolve_life_authority()
        return command_service

    def get_life_snapshot_reader() -> Any:
        _, snapshot_reader = resolve_life_authority()
        return snapshot_reader

    def build_surface_context_packet_with_life_authority(
        **kwargs: Any,
    ) -> dict[str, Any]:
        return build_surface_context_packet(
            life_snapshot_reader=get_life_snapshot_reader(),
            **kwargs,
        )

    return SurfaceRuntimePorts(
        get_ledger=agent_runtime_ports.audit_ledger,
        get_life_command_service=get_life_command_service,
        get_life_snapshot_reader=get_life_snapshot_reader,
        get_persistence_settings=get_persistence_settings,
        build_surface_context_packet=(
            build_surface_context_packet_with_life_authority
        ),
        build_user_preference_pack=build_user_preference_pack,
        get_conversation_history=agent_runtime_ports.conversation_history,
        get_conversation_experience_store=(
            agent_runtime_ports.get_conversation_experience_store
        ),
        build_conversation_understanding_pack=(
            build_conversation_understanding_pack
        ),
        build_memory_context_debug=build_memory_context_debug,
        model_attempt_service=agent_runtime_ports.model_attempt_service,
    )


__all__ = [
    "LifeAuthorityProvider",
    "SurfaceRuntimeCompositionError",
    "build_surface_runtime_ports",
]
