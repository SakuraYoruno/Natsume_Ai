"""Application composition for the lifecycle runtime dependency graph."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from host_brain.app.host_lifecycle_composition import (
    HostLifecycleApplicationPorts,
    missing_app_local_provider,
    require_host_provider,
)
from host_brain.lifecycle_runtime.ports import LifecycleRuntimeApplicationPorts


def _missing_host_hard_state_owner(*_args: Any, **_kwargs: Any) -> Any:
    """Keep isolated composition importable while failing closed on use."""

    raise RuntimeError("HOST_HARD_STATE_OWNER_NOT_INJECTED")


def build_lifecycle_runtime_application_ports(
    *,
    host_services: HostLifecycleApplicationPorts | None = None,
    activity_monitor_provider: Callable[..., Any] | None = None,
    endocrine_state_provider: Callable[..., Any] | None = None,
    episodic_store_provider: Callable[[], Any] | None = None,
    hard_state_provider: Callable[[], Any] | None = None,
    model_attempt_service_provider: Callable[[], Any] | None = None,
) -> LifecycleRuntimeApplicationPorts:
    """Bind application-owned services to immutable, named lifecycle ports."""

    return LifecycleRuntimeApplicationPorts(
        get_activity_monitor=(
            activity_monitor_provider or missing_app_local_provider
        ),
        get_endocrine_state=(
            endocrine_state_provider or _missing_host_hard_state_owner
        ),
        get_episodic_store=(
            episodic_store_provider
            or require_host_provider(host_services, "episodic_store")
        ),
        get_hard_state=hard_state_provider or _missing_host_hard_state_owner,
        get_ledger=require_host_provider(host_services, "ledger"),
        get_life_memory_store=require_host_provider(host_services, "life_memory_store"),
        get_model_attempt_service=(
            model_attempt_service_provider
            or require_host_provider(host_services, "model_attempt_service")
        ),
        get_life_snapshot_reader=require_host_provider(
            host_services,
            "life_snapshot_reader",
        ),
    )


__all__ = ["build_lifecycle_runtime_application_ports"]
