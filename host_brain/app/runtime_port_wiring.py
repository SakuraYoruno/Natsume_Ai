"""Static application composition for extracted runtime ports."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from host_brain.executive_runtime.ports import ExecutiveRuntimeApplicationPorts


def build_executive_runtime_application_ports() -> "ExecutiveRuntimeApplicationPorts":
    """Compose one immutable Executive Runtime dependency graph."""

    from host_brain.app.durable_task_status import build_durable_task_status_projection  # noqa: PLC0415
    from host_brain.app.graph_config_builder import resolve_surface_reply_llm_runtime  # noqa: PLC0415
    from host_brain.app.settings import get_core_settings  # noqa: PLC0415
    from host_brain.app.sub_agent_protocol import (  # noqa: PLC0415
        SubAgentRequest,
        get_sub_agent_dispatcher,
    )
    from host_brain.app.surface_reply_preprocessor import (  # noqa: PLC0415
        SurfaceReplyInput,
        SurfaceSubAgentContext,
    )
    from host_brain.app.surface_reply_runtime import (  # noqa: PLC0415
        _sse,
        surface_reply_sse_generator,
    )
    from host_brain.app.tool_routes import _attempt_activate_hub_skill  # noqa: PLC0415
    from host_brain.composition import process_services  # noqa: PLC0415
    from host_brain.executive_runtime.ports import (  # noqa: PLC0415
        ExecutiveRuntimeApplicationPorts,
    )
    from host_brain.tribe.events import emit_tribe_event  # noqa: PLC0415

    return ExecutiveRuntimeApplicationPorts(
        encode_sse=_sse,
        surface_reply_input_factory=SurfaceReplyInput,
        surface_sub_agent_context_factory=SurfaceSubAgentContext,
        resolve_surface_reply_llm_runtime=resolve_surface_reply_llm_runtime,
        surface_reply_sse_generator=surface_reply_sse_generator,
        sub_agent_request_factory=SubAgentRequest,
        get_sub_agent_dispatcher=get_sub_agent_dispatcher,
        get_core_settings=get_core_settings,
        get_legacy_runtime_task_registry=process_services.get_legacy_runtime_task_registry,
        get_task_status_projection=lambda: build_durable_task_status_projection(
            ledger=process_services.get_ledger()
        ),
        get_skill_registry=process_services.get_skill_registry,
        get_planning_agent=process_services.get_planning_agent,
        get_ledger=process_services.get_ledger,
        get_token_service=process_services.get_token_service,
        get_tribe_handoff_coordinator=process_services.get_tribe_handoff_coordinator,
        get_capability_forge_service=process_services.get_capability_forge_service,
        get_capability_gap_telemetry=process_services.get_capability_gap_telemetry,
        get_runtime_capability_registry=process_services.get_runtime_capability_registry,
        get_capability_broker=process_services.get_capability_broker,
        get_resource_manager=process_services.get_resource_manager,
        get_episodic_store=process_services.get_episodic_store,
        get_semantic_recaller=process_services.get_semantic_recaller,
        refresh_skill_registry=process_services.refresh_skill_registry,
        activate_hub_skill=_attempt_activate_hub_skill,
        emit_tribe_event=emit_tribe_event,
    )

__all__ = [
    "build_executive_runtime_application_ports",
]
