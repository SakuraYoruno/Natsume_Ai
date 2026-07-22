"""Application adapter that supplies explicit ports to Tribe composition."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from host_brain.app.host_lifecycle_composition import HostLifecycleApplicationPorts
from host_brain.tribe.application_ports import TribeApplicationPorts


def build_tribe_application_ports(
    *,
    host_services: HostLifecycleApplicationPorts | None = None,
    activity_monitor_provider: Callable[..., Any] | None = None,
    hard_state_provider: Callable[[], Any] | None = None,
    mode_transition_service_provider: Callable[[], Any | None] | None = None,
    resource_manager_provider: Callable[..., Any] | None = None,
) -> TribeApplicationPorts:
    from host_brain.app.context.conversation_belief_tracker import (
        build_conversation_understanding_pack,
    )
    from host_brain.app.context.conversation_experience_store import (
        get_conversation_experience_store,
    )
    from host_brain.app.emotion_context import (
        build_chat_emotion_snapshot,
        resolve_chat_emotion_context,
    )
    from host_brain.app.intent_seq import allocate_intent_seq
    from host_brain.app.user_preference_store import build_user_preference_pack
    from host_brain.composition.tribe_runtime import compose_process_tribe_runtime
    from host_brain.tribe.registry import get_host_tribe_registry
    from host_brain.tribe.tribe_state import get_global_registry

    compose_process_tribe_runtime(
        state_registry=get_global_registry(),
        tribe_registry=get_host_tribe_registry(),
    )

    resolved_activity_monitor_provider = (
        activity_monitor_provider or _missing_app_local_provider
    )
    resolved_resource_manager_provider = (
        resource_manager_provider or _missing_app_local_provider
    )

    def resolve_process_mode_transition_service():
        if mode_transition_service_provider is not None:
            return mode_transition_service_provider()
        monitor = resolved_activity_monitor_provider()
        return getattr(monitor, "_mode_service", None) if monitor is not None else None

    return TribeApplicationPorts(
        activity_monitor=resolved_activity_monitor_provider,
        mode_transition_service=resolve_process_mode_transition_service,
        bond_update_queue=_host_provider(host_services, "bond_update_queue"),
        ledger=_host_provider(host_services, "ledger"),
        life_snapshot_reader=_host_provider(host_services, "life_snapshot_reader"),
        silence_engine=_host_provider(host_services, "silence_engine"),
        state_write_arbiter=_host_provider(host_services, "state_write_arbiter"),
        emotion_timeline=_host_provider(host_services, "emotion_timeline"),
        hard_state=hard_state_provider or _missing_host_hard_state_owner,
        resource_manager=resolved_resource_manager_provider,
        bond_stage_engine=_host_provider(host_services, "bond_stage_engine"),
        shared_memory_index=_host_provider(host_services, "shared_memory_index"),
        milestone_tracker=_host_provider(host_services, "milestone_tracker"),
        model_attempt_service=_host_provider(host_services, "model_attempt_service"),
        prompt_registry=_host_provider(host_services, "prompt_registry"),
        resolve_chat_emotion_context=resolve_chat_emotion_context,
        build_chat_emotion_snapshot=build_chat_emotion_snapshot,
        build_user_preference_pack=build_user_preference_pack,
        conversation_experience_store=get_conversation_experience_store,
        build_conversation_understanding_pack=build_conversation_understanding_pack,
        allocate_intent_seq=allocate_intent_seq,
    )


def _host_provider(
    host_services: HostLifecycleApplicationPorts | None,
    field_name: str,
) -> Callable[..., Any]:
    if host_services is None:
        return _missing_app_local_provider
    return host_services.require_provider(field_name)


def _missing_app_local_provider(*_args: Any, **_kwargs: Any) -> Any:
    raise RuntimeError("HOST_APP_LOCAL_SERVICE_PROVIDER_NOT_INJECTED")


def _missing_host_hard_state_owner(*_args: Any, **_kwargs: Any) -> Any:
    """Reject Tribe HardState access when an app owner was not injected."""

    raise RuntimeError("HOST_HARD_STATE_OWNER_NOT_INJECTED")


def bind_tribe_application_ports(*, owner: Any, ports: TribeApplicationPorts) -> None:
    """Acquire the process Tribe port lease for one live Host lifespan."""

    from host_brain.composition.tribe_runtime import (
        bind_process_tribe_application_ports,
        compose_process_tribe_runtime,
    )
    from host_brain.tribe.registry import get_host_tribe_registry
    from host_brain.tribe.tribe_state import get_global_registry

    compose_process_tribe_runtime(
        state_registry=get_global_registry(),
        tribe_registry=get_host_tribe_registry(),
    )
    bind_process_tribe_application_ports(owner=owner, ports=ports)


def unbind_tribe_application_ports(*, owner: Any) -> bool:
    """Release the process Tribe port lease held by one Host lifespan."""

    from host_brain.composition.tribe_runtime import (
        unbind_process_tribe_application_ports,
    )

    return unbind_process_tribe_application_ports(owner=owner)


__all__ = [
    "bind_tribe_application_ports",
    "build_tribe_application_ports",
    "unbind_tribe_application_ports",
]
