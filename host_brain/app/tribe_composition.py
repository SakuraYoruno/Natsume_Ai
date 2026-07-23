"""Application adapter that supplies explicit ports to Tribe composition."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from host_brain.app.host_lifecycle_composition import HostLifecycleApplicationPorts
from host_brain.app.tribe_runtime_scope import (
    TribeAgentProviders,
    TribeRuntimeScope,
)
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


def build_tribe_runtime_scope(
    *,
    state_registry: Any,
    tribe_registry: Any,
    agent_providers: TribeAgentProviders,
) -> TribeRuntimeScope:
    """Build an ASGI-owned Tribe scope from explicitly supplied dependencies."""

    return TribeRuntimeScope(
        state_registry=state_registry,
        tribe_registry=tribe_registry,
        agent_providers=agent_providers,
    )


def build_production_tribe_runtime_scope() -> TribeRuntimeScope:
    """Map legacy compatibility singletons only at the Host composition root."""

    from host_brain.tribe.registry import get_host_tribe_registry  # noqa: PLC0415
    from host_brain.tribe.tribe_state import get_global_registry  # noqa: PLC0415

    return build_tribe_runtime_scope(
        state_registry=get_global_registry(),
        tribe_registry=get_host_tribe_registry(),
        agent_providers=build_production_tribe_agent_providers(),
    )


def build_production_tribe_agent_providers() -> TribeAgentProviders:
    """Return the fixed compatibility Agent catalog without runtime discovery."""

    from host_brain.tribe.ans_agent import get_ans_agent  # noqa: PLC0415
    from host_brain.tribe.cerebellum_agent import get_cerebellum_agent  # noqa: PLC0415
    from host_brain.tribe.circadian_agent import get_circadian_agent  # noqa: PLC0415
    from host_brain.tribe.cortex_agent import get_cortex_agent  # noqa: PLC0415
    from host_brain.tribe.default_mode_agent import get_default_mode_agent  # noqa: PLC0415
    from host_brain.tribe.endocrine_agent import get_endocrine_agent  # noqa: PLC0415
    from host_brain.tribe.glymphatic_agent import get_glymphatic_agent  # noqa: PLC0415
    from host_brain.tribe.hippocampus_agent import get_hippocampus_agent  # noqa: PLC0415
    from host_brain.tribe.immune_agent import get_immune_agent  # noqa: PLC0415
    from host_brain.tribe.initiative_governor_agent import (  # noqa: PLC0415
        get_initiative_governor_agent,
    )
    from host_brain.tribe.interoception_agent import get_interoception_agent  # noqa: PLC0415
    from host_brain.tribe.language_system_agent import get_language_system_agent  # noqa: PLC0415
    from host_brain.tribe.limbic_agent import get_limbic_agent  # noqa: PLC0415
    from host_brain.tribe.mirror_neuron_agent import get_mirror_neuron_agent  # noqa: PLC0415
    from host_brain.tribe.motor_cortex_agent import get_motor_cortex_agent  # noqa: PLC0415
    from host_brain.tribe.pain_reward_agent import get_pain_reward_agent  # noqa: PLC0415
    from host_brain.tribe.peripheral_agent import get_peripheral_agent  # noqa: PLC0415
    from host_brain.tribe.prefrontal_agent import get_prefrontal_agent  # noqa: PLC0415
    from host_brain.tribe.proprioception_agent import get_proprioception_agent  # noqa: PLC0415
    from host_brain.tribe.social_brain_agent import get_social_brain_agent  # noqa: PLC0415
    from host_brain.tribe.somatic_agent import get_somatic_agent  # noqa: PLC0415
    from host_brain.tribe.thalamus_agent import get_thalamus_agent  # noqa: PLC0415
    from host_brain.tribe.vestibular_agent import get_vestibular_agent  # noqa: PLC0415

    return (
        ("limbic_agent", get_limbic_agent),
        ("prefrontal_agent", get_prefrontal_agent),
        ("social_brain_agent", get_social_brain_agent),
        ("cortex_agent", get_cortex_agent),
        ("pain_reward_agent", get_pain_reward_agent),
        ("endocrine_agent", get_endocrine_agent),
        ("immune_agent", get_immune_agent),
        ("hippocampus_agent", get_hippocampus_agent),
        ("ans_agent", get_ans_agent),
        ("language_system_agent", get_language_system_agent),
        ("somatic_agent", get_somatic_agent),
        ("peripheral_agent", get_peripheral_agent),
        ("circadian_agent", get_circadian_agent),
        ("proprioception_agent", get_proprioception_agent),
        ("initiative_governor_agent", get_initiative_governor_agent),
        ("default_mode_agent", get_default_mode_agent),
        ("mirror_neuron_agent", get_mirror_neuron_agent),
        ("interoception_agent", get_interoception_agent),
        ("motor_cortex_agent", get_motor_cortex_agent),
        ("cerebellum_agent", get_cerebellum_agent),
        ("glymphatic_agent", get_glymphatic_agent),
        ("vestibular_agent", get_vestibular_agent),
        ("thalamus_agent", get_thalamus_agent),
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


__all__ = [
    "TribeRuntimeAgentPorts",
    "build_tribe_application_ports",
    "build_production_tribe_agent_providers",
    "build_production_tribe_runtime_agent_ports",
    "build_production_tribe_runtime_scope",
    "build_tribe_runtime_scope",
]
