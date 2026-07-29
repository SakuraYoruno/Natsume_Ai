"""Application composition for the lifecycle runtime dependency graph."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from host_brain.app.host_lifecycle_composition import HostLifecycleApplicationPorts
from host_brain.lifecycle_runtime.ports import LifecycleRuntimeApplicationPorts


def _missing_host_hard_state_owner(*_args: Any, **_kwargs: Any) -> Any:
    """Keep legacy test composition importable while failing closed on use."""

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
            activity_monitor_provider or _missing_app_local_provider
        ),
        get_endocrine_state=(
            endocrine_state_provider or _missing_host_hard_state_owner
        ),
        get_episodic_store=(
            episodic_store_provider
            or _host_provider(host_services, "episodic_store")
        ),
        get_hard_state=hard_state_provider or _missing_host_hard_state_owner,
        get_ledger=_host_provider(host_services, "ledger"),
        get_life_memory_store=_host_provider(host_services, "life_memory_store"),
        get_model_attempt_service=(
            model_attempt_service_provider
            or _host_provider(host_services, "model_attempt_service")
        ),
        emotion_timeline=_resolve_emotion_timeline(host_services),
        get_user_emotion_profile=_optional_host_provider(
            host_services,
            "user_emotion_profile",
        ),
        get_life_snapshot_reader=_optional_host_provider(
            host_services,
            "life_snapshot_reader",
        ),
    )


def _host_provider(
    host_services: HostLifecycleApplicationPorts | None,
    field_name: str,
) -> Callable[..., Any]:
    if host_services is None:
        return _missing_app_local_provider
    return host_services.require_provider(field_name)


def _optional_host_provider(
    host_services: HostLifecycleApplicationPorts | None,
    field_name: str,
) -> Callable[..., Any] | None:
    if host_services is None:
        return None
    provider = getattr(host_services, field_name, None)
    return provider if callable(provider) else None


def _resolve_emotion_timeline(
    host_services: HostLifecycleApplicationPorts | None,
) -> Any:
    if host_services is None:
        return _MissingEmotionTimeline()
    return _LazyEmotionTimeline(
        _host_provider(host_services, "emotion_timeline")
    )


def _missing_app_local_provider(*_args: Any, **_kwargs: Any) -> Any:
    raise RuntimeError("HOST_APP_LOCAL_SERVICE_PROVIDER_NOT_INJECTED")


class _MissingEmotionTimeline:
    def rebuild_summary(self, *, window_ms: int) -> dict[str, Any]:
        del window_ms
        return _missing_app_local_provider()

    def build_daily_diary(self, *, window_ms: int) -> dict[str, Any]:
        del window_ms
        return _missing_app_local_provider()


class _LazyEmotionTimeline:
    """Resolve the app-local timeline only when runtime projections use it."""

    def __init__(self, provider: Callable[[], Any]) -> None:
        self._provider = provider

    def rebuild_summary(self, *, window_ms: int) -> dict[str, Any]:
        return dict(
            self._provider().rebuild_summary(window_ms=window_ms) or {}
        )

    def build_daily_diary(self, *, window_ms: int) -> dict[str, Any]:
        return dict(
            self._provider().build_daily_diary(window_ms=window_ms) or {}
        )


__all__ = ["build_lifecycle_runtime_application_ports"]
