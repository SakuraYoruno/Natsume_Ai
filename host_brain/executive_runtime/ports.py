"""Typed application boundary for the Executive Runtime.

The runtime receives this immutable object from the application composition
root.  It deliberately contains named callables instead of a service locator:
missing collaborators are therefore visible at construction time and can be
followed through normal Python references.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Any, Protocol


class ExecutiveRuntimeSettings(Protocol):
    """Settings projection consumed by the Executive Runtime."""

    prefrontal_plan_enabled: bool
    skill_dag_parallel_window: int


ServiceProvider = Callable[[], Any]
KeywordFactory = Callable[..., Any]
SurfaceReplyStream = Callable[[Any], AsyncIterator[str]]


@dataclass(frozen=True, slots=True)
class ExecutiveRuntimeApplicationPorts:
    """All application-owned collaborators required by Executive Runtime."""

    encode_sse: Callable[[dict[str, Any]], str]
    surface_reply_input_factory: KeywordFactory
    surface_sub_agent_context_factory: KeywordFactory
    resolve_surface_reply_llm_runtime: KeywordFactory
    surface_reply_sse_generator: SurfaceReplyStream
    sub_agent_request_factory: KeywordFactory
    get_sub_agent_dispatcher: ServiceProvider
    get_core_settings: Callable[[], ExecutiveRuntimeSettings]

    get_task_status_projection: ServiceProvider
    get_skill_registry: ServiceProvider
    get_planning_agent: ServiceProvider
    get_ledger: ServiceProvider
    get_token_service: ServiceProvider
    get_tribe_handoff_coordinator: ServiceProvider
    get_capability_forge_service: ServiceProvider
    get_capability_gap_telemetry: ServiceProvider
    get_runtime_capability_registry: ServiceProvider
    get_capability_broker: ServiceProvider
    get_resource_manager: Callable[..., Any]
    get_episodic_store: ServiceProvider
    get_semantic_recaller: ServiceProvider
    emit_tribe_event: KeywordFactory


__all__ = [
    "ExecutiveRuntimeApplicationPorts",
    "ExecutiveRuntimeSettings",
]
