"""Application composition for the lifecycle runtime dependency graph."""

from __future__ import annotations

from host_brain.lifecycle_runtime.ports import LifecycleRuntimeApplicationPorts


def build_lifecycle_runtime_application_ports() -> LifecycleRuntimeApplicationPorts:
    """Bind application-owned services to immutable, named lifecycle ports."""

    from host_brain.app import emotion_context
    from host_brain.composition import process_services

    return LifecycleRuntimeApplicationPorts(
        get_activity_monitor=process_services.get_activity_monitor,
        get_endocrine_state=process_services.get_endocrine_state,
        get_episodic_store=process_services.get_episodic_store,
        get_hard_state=process_services.get_hard_state,
        get_ledger=process_services.get_ledger,
        get_life_memory_store=process_services.get_life_memory_store,
        get_model_attempt_service=process_services.get_model_attempt_service,
        emotion_timeline=emotion_context._global_emotion_timeline,
        get_user_emotion_profile=emotion_context.get_user_emotion_profile,
    )


__all__ = ["build_lifecycle_runtime_application_ports"]
