"""Explicit, frozen Host application ports for one ASGI composition root.

This module is the transitional production adapter around the remaining
``process_services`` composition facade.  Host application code receives the
immutable :class:`HostApplicationPorts` bundle instead of reaching back into
that facade.  The process facade is still a legacy construction dependency and
is deliberately not described as an app-local-instance or full locator
retirement proof.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from host_brain.app.host_lifecycle_composition import HostLifecycleApplicationPorts


ServiceProvider = Callable[[], Any]
MemoryContainerSetter = Callable[[Any | None], None]


@dataclass(frozen=True, slots=True)
class HostApplicationPorts:
    """Dependencies captured by one Host ASGI application factory invocation."""

    lifecycle: HostLifecycleApplicationPorts
    memory_worth_evaluator: ServiceProvider
    security_immune_agent: ServiceProvider
    token_service: ServiceProvider
    bond_dynamics_writer: ServiceProvider
    capability_forge_service: ServiceProvider
    capability_gap_telemetry: ServiceProvider
    capability_scorecard_provider: ServiceProvider
    memory_service_container_setter: MemoryContainerSetter

    def __post_init__(self) -> None:
        if not isinstance(self.lifecycle, HostLifecycleApplicationPorts):
            raise TypeError("lifecycle must be HostLifecycleApplicationPorts")
        for field_name in (
            "memory_worth_evaluator",
            "security_immune_agent",
            "token_service",
            "bond_dynamics_writer",
            "capability_forge_service",
            "capability_gap_telemetry",
            "capability_scorecard_provider",
            "memory_service_container_setter",
        ):
            if not callable(getattr(self, field_name)):
                raise TypeError(f"{field_name} must be callable")


def build_production_host_application_ports() -> HostApplicationPorts:
    """Map the current production composition facade into frozen Host ports.

    This is intentionally the last place in the Host application construction
    path that knows the legacy ``process_services`` facade.  It must be called
    only after ``configure_process_service_ports()`` has installed its typed
    process-level construction bindings.
    """

    from host_brain.app import emotion_context  # noqa: PLC0415
    from host_brain.core.activity_monitor import ActivityMonitor  # noqa: PLC0415
    from host_brain.core.mode_transition import ModeTransitionService  # noqa: PLC0415
    from host_brain.core.resource_manager import SystemResourceManager  # noqa: PLC0415
    from host_brain.composition import process_services  # noqa: PLC0415

    return HostApplicationPorts(
        lifecycle=HostLifecycleApplicationPorts(
            memory_guard=process_services.get_memory_guard,
            state_arbiter=process_services.get_state_arbiter,
            distillation_engine=process_services.get_distillation_engine,
            semantic_recaller=process_services.get_semantic_recaller,
            episodic_store=process_services.get_episodic_store,
            skill_registry=process_services.get_skill_registry,
            tool_catalog=process_services.get_tool_catalog,
            capability_broker=process_services.get_capability_broker,
            model_attempt_service=process_services.get_model_attempt_service,
            mode_transition_service_factory=ModeTransitionService,
            activity_monitor_factory=ActivityMonitor,
            resource_manager_factory=SystemResourceManager,
            ledger=process_services.get_ledger,
            life_memory_store=process_services.get_life_memory_store,
            bond_update_queue=process_services.get_bond_update_queue,
            life_snapshot_reader=process_services.get_life_snapshot_reader,
            silence_engine=process_services.get_silence_engine,
            state_write_arbiter=process_services.get_state_write_arbiter,
            emotion_timeline=process_services.get_emotion_timeline,
            bond_stage_engine=process_services.get_bond_stage_engine,
            shared_memory_index=process_services.get_shared_memory_index,
            milestone_tracker=process_services.get_milestone_tracker,
            prompt_registry=process_services.get_prompt_registry,
            user_emotion_profile=emotion_context.get_user_emotion_profile,
            intent_engine=process_services.get_intent_engine,
            multimodal_observer=process_services.get_multimodal_observer,
            multimodal_processor=process_services.get_multimodal_processor,
            conversation_history=process_services.get_conversation_history,
            life_memory_service=process_services.get_life_memory_service,
            model_route_catalog=process_services.get_model_route_catalog,
            session_topic_ledger_store=(
                process_services.get_session_topic_ledger_store
            ),
            session_tactic_ledger_store=(
                process_services.get_session_tactic_ledger_store
            ),
            tts_context_router=process_services.get_tts_context_router,
            meta_learning_loop=process_services.get_meta_learning_loop,
        ),
        memory_worth_evaluator=process_services.get_memory_worth_evaluator,
        security_immune_agent=process_services.get_security_immune_agent,
        token_service=process_services.get_token_service,
        bond_dynamics_writer=process_services.get_bond_dynamics_writer,
        capability_forge_service=process_services.get_capability_forge_service,
        capability_gap_telemetry=process_services.get_capability_gap_telemetry,
        capability_scorecard_provider=(
            process_services.get_capability_scorecard_provider
        ),
        memory_service_container_setter=(
            process_services.set_memory_service_container
        ),
    )


__all__ = [
    "HostApplicationPorts",
    "MemoryContainerSetter",
    "ServiceProvider",
    "build_production_host_application_ports",
]
