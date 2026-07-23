"""Application adapter that supplies explicit ports to Tribe composition."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from host_brain.app.host_lifecycle_composition import HostLifecycleApplicationPorts
from host_brain.tribe.application_ports import TribeApplicationPorts


@dataclass(frozen=True, slots=True)
class TribeRuntimeAgentPorts:
    """Explicit access to the process-bound Tribe agents used by one Host.

    Tribe agent instances are still process-bound during the NE-01 transition.
    The transition boundary is deliberately concentrated here: application and
    input code receive this immutable bundle instead of importing a Tribe
    singleton getter at request time.
    """

    get_thalamus_agent: Callable[[], Any]
    get_ans_agent: Callable[[], Any]
    get_immune_agent: Callable[[], Any] | None = None

    def __post_init__(self) -> None:
        for field_name in ("get_thalamus_agent", "get_ans_agent"):
            if not callable(getattr(self, field_name)):
                raise TypeError(f"{field_name} must be callable")
        if self.get_immune_agent is not None and not callable(self.get_immune_agent):
            raise TypeError("get_immune_agent must be callable when provided")

    def require_thalamus_agent(self) -> Any:
        return _require_runtime_agent(
            self.get_thalamus_agent,
            "HOST_TRIBE_THALAMUS_AGENT_NOT_INJECTED",
        )

    def require_ans_agent(self) -> Any:
        return _require_runtime_agent(
            self.get_ans_agent,
            "HOST_TRIBE_ANS_AGENT_NOT_INJECTED",
        )

    def require_immune_agent(self) -> Any:
        if self.get_immune_agent is None:
            raise RuntimeError("HOST_TRIBE_IMMUNE_AGENT_NOT_INJECTED")
        return _require_runtime_agent(
            self.get_immune_agent,
            "HOST_TRIBE_IMMUNE_AGENT_NOT_INJECTED",
        )


def build_production_tribe_runtime_agent_ports() -> TribeRuntimeAgentPorts:
    """Expose the remaining Tribe singleton bridge only at composition time."""

    from host_brain.tribe.ans_agent import get_ans_agent  # noqa: PLC0415
    from host_brain.tribe.immune_agent import get_immune_agent  # noqa: PLC0415
    from host_brain.tribe.thalamus_agent import get_thalamus_agent  # noqa: PLC0415

    return TribeRuntimeAgentPorts(
        get_thalamus_agent=get_thalamus_agent,
        get_ans_agent=get_ans_agent,
        get_immune_agent=get_immune_agent,
    )


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
        build_conversation_experience_read_pack,
    )
    from host_brain.app.emotion_context import (
        build_chat_emotion_snapshot,
        resolve_chat_emotion_context,
    )
    from host_brain.app.intent_seq import IntentSequenceAllocator
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
    ledger_provider = _host_provider(host_services, "ledger")
    intent_sequence_allocator = IntentSequenceAllocator(
        ledger_provider=ledger_provider
    )

    def resolve_process_mode_transition_service():
        if mode_transition_service_provider is not None:
            return mode_transition_service_provider()
        monitor = resolved_activity_monitor_provider()
        return getattr(monitor, "_mode_service", None) if monitor is not None else None

    def build_user_preference_pack_with_ledger(**kwargs: Any) -> dict[str, Any]:
        return build_user_preference_pack(ledger=ledger_provider(), **kwargs)

    def build_conversation_experience_read_pack_with_ledger(
        **kwargs: Any,
    ) -> dict[str, Any]:
        return build_conversation_experience_read_pack(
            ledger=ledger_provider(),
            **kwargs,
        )

    return TribeApplicationPorts(
        activity_monitor=resolved_activity_monitor_provider,
        mode_transition_service=resolve_process_mode_transition_service,
        bond_update_queue=_host_provider(host_services, "bond_update_queue"),
        ledger=ledger_provider,
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
        build_user_preference_pack=build_user_preference_pack_with_ledger,
        build_conversation_experience_read_pack=(
            build_conversation_experience_read_pack_with_ledger
        ),
        build_conversation_understanding_pack=build_conversation_understanding_pack,
        allocate_intent_seq=intent_sequence_allocator.allocate,
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


def _require_runtime_agent(
    provider: Callable[[], Any],
    missing_code: str,
) -> Any:
    agent = provider()
    if agent is None:
        raise RuntimeError(missing_code)
    return agent


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
    "TribeRuntimeAgentPorts",
    "bind_tribe_application_ports",
    "build_tribe_application_ports",
    "build_production_tribe_runtime_agent_ports",
    "unbind_tribe_application_ports",
]
