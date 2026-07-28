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
    """Explicit access to the app-local Tribe agents used by one Host.

    The provider functions retain the historical getter signatures, but an
    active Host resolves them through its owner-bound ``TribeAgentCatalog``.
    Application and input code receive this immutable bundle instead of
    importing those compatibility getters at request time.
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


def build_production_tribe_runtime_agent_ports(
    *,
    tribe_runtime: TribeRuntimeScope,
) -> TribeRuntimeAgentPorts:
    """Bind application ports to one exact Host-owned Agent catalog."""

    if not isinstance(tribe_runtime, TribeRuntimeScope):
        raise TypeError("tribe_runtime must be TribeRuntimeScope")

    return TribeRuntimeAgentPorts(
        get_thalamus_agent=lambda: tribe_runtime.require_agent(
            "thalamus_agent"
        ),
        get_ans_agent=lambda: tribe_runtime.require_agent("ans_agent"),
        get_immune_agent=lambda: tribe_runtime.require_agent("immune_agent"),
    )


def build_tribe_runtime_scope(
    *,
    state_registry: Any,
    tribe_registry: Any,
    event_bus: Any,
    agent_providers: TribeAgentProviders,
) -> TribeRuntimeScope:
    """Build an ASGI-owned Tribe scope from explicitly supplied dependencies."""

    return TribeRuntimeScope(
        state_registry=state_registry,
        tribe_registry=tribe_registry,
        event_bus=event_bus,
        agent_providers=agent_providers,
    )


def build_production_tribe_runtime_scope() -> TribeRuntimeScope:
    """Build fresh Host-owned shared state around compatibility Agent providers."""

    from host_brain.tribe.event_bus import InMemoryTribeEventBus  # noqa: PLC0415
    from host_brain.tribe.registry import HostTribeRegistry  # noqa: PLC0415
    from host_brain.tribe.tribe_state import TribeStateRegistry  # noqa: PLC0415

    return build_tribe_runtime_scope(
        state_registry=TribeStateRegistry(),
        tribe_registry=HostTribeRegistry(),
        event_bus=InMemoryTribeEventBus(max_events=2048),
        agent_providers=build_production_tribe_agent_providers(),
    )


def build_production_tribe_agent_providers() -> TribeAgentProviders:
    """Return constructors for one fresh Host-owned Agent catalog."""

    from host_brain.tribe.ans_agent import ANSAgent  # noqa: PLC0415
    from host_brain.tribe.cerebellum_agent import CerebellumAgent  # noqa: PLC0415
    from host_brain.tribe.circadian_agent import CircadianAgent  # noqa: PLC0415
    from host_brain.tribe.cortex_agent import CortexAgent  # noqa: PLC0415
    from host_brain.tribe.default_mode_agent import DefaultModeAgent  # noqa: PLC0415
    from host_brain.tribe.endocrine_agent import EndocrineAgent  # noqa: PLC0415
    from host_brain.tribe.glymphatic_agent import GlymphaticAgent  # noqa: PLC0415
    from host_brain.tribe.hippocampus_agent import HippocampusAgent  # noqa: PLC0415
    from host_brain.tribe.immune_agent import ImmuneAgent  # noqa: PLC0415
    from host_brain.tribe.initiative_governor_agent import (  # noqa: PLC0415
        InitiativeGovernorAgent,
    )
    from host_brain.tribe.interoception_agent import InteroceptionAgent  # noqa: PLC0415
    from host_brain.tribe.language_system_agent import LanguageSystemAgent  # noqa: PLC0415
    from host_brain.tribe.limbic_agent import LimbicAgent  # noqa: PLC0415
    from host_brain.tribe.mirror_neuron_agent import MirrorNeuronAgent  # noqa: PLC0415
    from host_brain.tribe.motor_cortex_agent import MotorCortexAgent  # noqa: PLC0415
    from host_brain.tribe.pain_reward_agent import PainRewardAgent  # noqa: PLC0415
    from host_brain.tribe.peripheral_agent import PeripheralAgent  # noqa: PLC0415
    from host_brain.tribe.prefrontal_agent import PrefrontalAgent  # noqa: PLC0415
    from host_brain.tribe.proprioception_agent import ProprioceptionAgent  # noqa: PLC0415
    from host_brain.tribe.social_brain_agent import SocialBrainAgent  # noqa: PLC0415
    from host_brain.tribe.somatic_agent import SomaticAgent  # noqa: PLC0415
    from host_brain.tribe.thalamus_agent import ThalamusAgent  # noqa: PLC0415
    from host_brain.tribe.vestibular_agent import VestibularAgent  # noqa: PLC0415

    return (
        ("limbic_agent", LimbicAgent),
        ("prefrontal_agent", PrefrontalAgent),
        ("social_brain_agent", SocialBrainAgent),
        ("cortex_agent", CortexAgent),
        ("pain_reward_agent", PainRewardAgent),
        ("endocrine_agent", EndocrineAgent),
        ("immune_agent", ImmuneAgent),
        ("hippocampus_agent", HippocampusAgent),
        ("ans_agent", ANSAgent),
        ("language_system_agent", LanguageSystemAgent),
        ("somatic_agent", SomaticAgent),
        ("peripheral_agent", PeripheralAgent),
        ("circadian_agent", CircadianAgent),
        ("proprioception_agent", ProprioceptionAgent),
        ("initiative_governor_agent", InitiativeGovernorAgent),
        ("default_mode_agent", DefaultModeAgent),
        ("mirror_neuron_agent", MirrorNeuronAgent),
        ("interoception_agent", InteroceptionAgent),
        ("motor_cortex_agent", MotorCortexAgent),
        ("cerebellum_agent", CerebellumAgent),
        ("glymphatic_agent", GlymphaticAgent),
        ("vestibular_agent", VestibularAgent),
        ("thalamus_agent", ThalamusAgent),
    )


def build_tribe_application_ports(
    *,
    host_services: HostLifecycleApplicationPorts | None = None,
    activity_monitor_provider: Callable[..., Any] | None = None,
    hard_state_provider: Callable[[], Any] | None = None,
    mode_transition_service_provider: Callable[[], Any | None] | None = None,
    resource_manager_provider: Callable[..., Any] | None = None,
    life_command_service_provider: Callable[[], Any] | None = None,
    relationship_lifecycle_service_provider: Callable[[], Any] | None = None,
) -> TribeApplicationPorts:
    from host_brain.app.context.conversation_belief_tracker import (
        build_conversation_understanding_pack,
    )
    from host_brain.app.context.conversation_experience_store import (
        build_conversation_experience_read_pack,
    )
    from host_brain.app.emotion_context import (
        HostEmotionRuntime,
        build_chat_emotion_snapshot,
        resolve_chat_emotion_context,
    )
    from host_brain.app.intent_seq import IntentSequenceAllocator
    from host_brain.app.user_preference_store import build_user_preference_pack
    from host_brain.digital_life.affect_fusion import AffectFusionService

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
    canonical_affect_fusion = None
    if callable(life_command_service_provider):
        # All production callers that can reach the canonical Life command
        # authority must use it.  Do not construct the legacy runtime as a
        # "backup": that would revive a second mutable self-affect writer.
        canonical_affect_fusion = AffectFusionService(
            command_service_provider=life_command_service_provider,
            snapshot_reader_provider=_host_provider(
                host_services,
                "life_snapshot_reader",
            ),
            relationship_lifecycle_service_provider=(
                relationship_lifecycle_service_provider
            ),
        )
        emotion_runtime_provider = None
    elif host_services is None:
        local_emotion_runtime = HostEmotionRuntime()
        emotion_runtime_provider = lambda: local_emotion_runtime
    else:
        emotion_runtime_provider = _host_provider(
            host_services,
            "emotion_context_runtime",
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

    def resolve_chat_emotion_context_with_runtime(**kwargs: Any) -> dict[str, Any]:
        if canonical_affect_fusion is not None:
            return resolve_chat_emotion_context(
                affect_fusion_service=canonical_affect_fusion,
                **kwargs,
            )
        if emotion_runtime_provider is None:
            raise RuntimeError("CANONICAL_AFFECT_FUSION_NOT_INJECTED")
        runtime = emotion_runtime_provider()
        if not isinstance(runtime, HostEmotionRuntime):
            raise RuntimeError("HOST_EMOTION_RUNTIME_NOT_INJECTED")
        return resolve_chat_emotion_context(runtime=runtime, **kwargs)

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
        resolve_chat_emotion_context=resolve_chat_emotion_context_with_runtime,
        canonical_affect_fusion=(
            (lambda: canonical_affect_fusion)
            if canonical_affect_fusion is not None
            else None
        ),
        build_chat_emotion_snapshot=build_chat_emotion_snapshot,
        build_user_preference_pack=build_user_preference_pack_with_ledger,
        build_conversation_experience_read_pack=(
            build_conversation_experience_read_pack_with_ledger
        ),
        build_conversation_understanding_pack=build_conversation_understanding_pack,
        allocate_intent_seq=intent_sequence_allocator.allocate,
        evolution_activation_service=_optional_host_provider(
            host_services,
            "evolution_activation_service",
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
