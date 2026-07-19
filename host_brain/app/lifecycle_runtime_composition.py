"""Application composition for the lifecycle runtime dependency graph."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from host_brain.lifecycle_runtime.ports import LifecycleRuntimeApplicationPorts


def _missing_host_hard_state_owner(*_args: Any, **_kwargs: Any) -> Any:
    """Keep legacy test composition importable while failing closed on use."""

    raise RuntimeError("HOST_HARD_STATE_OWNER_NOT_INJECTED")


def build_lifecycle_runtime_application_ports(
    *,
    activity_monitor_provider: Callable[..., Any] | None = None,
    endocrine_state_provider: Callable[..., Any] | None = None,
    episodic_store_provider: Callable[[], Any] | None = None,
    hard_state_provider: Callable[[], Any] | None = None,
    model_attempt_service_provider: Callable[[], Any] | None = None,
) -> LifecycleRuntimeApplicationPorts:
    """Bind application-owned services to immutable, named lifecycle ports."""

    from host_brain.app import emotion_context
    from host_brain.composition import process_services

    return LifecycleRuntimeApplicationPorts(
        get_activity_monitor=(
            activity_monitor_provider or process_services.get_activity_monitor
        ),
        get_endocrine_state=(
            endocrine_state_provider or _missing_host_hard_state_owner
        ),
        get_episodic_store=(
            episodic_store_provider or process_services.get_episodic_store
        ),
        get_hard_state=hard_state_provider or _missing_host_hard_state_owner,
        get_ledger=process_services.get_ledger,
        get_life_memory_store=process_services.get_life_memory_store,
        get_model_attempt_service=(
            model_attempt_service_provider
            or process_services.get_model_attempt_service
        ),
        emotion_timeline=emotion_context._global_emotion_timeline,
        get_user_emotion_profile=emotion_context.get_user_emotion_profile,
    )


__all__ = ["build_lifecycle_runtime_application_ports"]
