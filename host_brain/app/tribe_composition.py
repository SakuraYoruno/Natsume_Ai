"""向 Tribe 组合提供显式应用端口的适配层。

每个 Host 实例拥有独立的 Tribe scope 与代理目录；此处将它们同生命周期和 canonical
Life 端口绑定，避免请求期导入兼容 getter 或意外复活遗留的可写情感运行时。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from host_brain.app.host_lifecycle_composition import (
    HostLifecycleApplicationPorts,
    missing_app_local_provider,
    optional_host_provider,
    require_host_provider,
)
from host_brain.app.tribe_runtime_scope import (
    TribeAgentProviders,
    TribeRuntimeScope,
)
from host_brain.tribe.application_ports import (
    CANONICAL_AFFECT_LIFE_JOURNAL,
    LEGACY_AFFECT_COMPATIBILITY,
    TribeApplicationPorts,
)


@dataclass(frozen=True, slots=True)
class TribeRuntimeAgentPorts:
    """一个 Host 所用应用局部 Tribe 代理的显式访问包。

    provider 保留历史 getter 签名，但活动 Host 经 owner-bound ``TribeAgentCatalog``
    解析它们。应用和输入层接收此不可变包，而非在请求期导入兼容 getter。
    """

    get_thalamus_agent: Callable[[], Any]
    get_immune_agent: Callable[[], Any] | None = None

    def __post_init__(self) -> None:
        if not callable(self.get_thalamus_agent):
            raise TypeError("get_thalamus_agent must be callable")
        if self.get_immune_agent is not None and not callable(self.get_immune_agent):
            raise TypeError("get_immune_agent must be callable when provided")

    def require_thalamus_agent(self) -> Any:
        return _require_runtime_agent(
            self.get_thalamus_agent,
            "HOST_TRIBE_THALAMUS_AGENT_NOT_INJECTED",
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
    """将应用端口绑定到一个精确的 Host 自有代理目录，拒绝跨应用共享。"""

    if not isinstance(tribe_runtime, TribeRuntimeScope):
        raise TypeError("tribe_runtime must be TribeRuntimeScope")

    return TribeRuntimeAgentPorts(
        get_thalamus_agent=lambda: tribe_runtime.require_agent(
            "thalamus_agent"
        ),
        get_immune_agent=lambda: tribe_runtime.require_agent("immune_agent"),
    )


def build_tribe_runtime_scope(
    *,
    state_registry: Any,
    tribe_registry: Any,
    event_bus: Any,
    agent_providers: TribeAgentProviders,
) -> TribeRuntimeScope:
    """从显式依赖构建 ASGI 所有的 Tribe scope，使生命周期所有权可追踪。"""

    return TribeRuntimeScope(
        state_registry=state_registry,
        tribe_registry=tribe_registry,
        event_bus=event_bus,
        agent_providers=agent_providers,
    )


def build_production_tribe_runtime_scope() -> TribeRuntimeScope:
    """创建新的 Host 自有共享状态与兼容代理构造器；实例间不得复用事件和状态。"""

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
    """返回构造一个全新 Host 自有代理目录的工厂列表，而非实例缓存。"""

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
    """装配 Tribe 所需的 Host 端口，并在 canonical Life 存在时封闭遗留情感写入。

    组合阶段的 provider 可延迟到生命周期内解析，但它们始终指向同一应用实例；无法
    注入的关键权威会明确失败，不能退回到进程全局对象。
    """
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
    from host_brain.digital_life.affect_fusion import AffectFusionService

    resolved_activity_monitor_provider = (
        activity_monitor_provider or missing_app_local_provider
    )
    resolved_resource_manager_provider = (
        resource_manager_provider or missing_app_local_provider
    )
    ledger_provider = require_host_provider(host_services, "ledger")
    intent_sequence_allocator = IntentSequenceAllocator(
        ledger_provider=ledger_provider
    )
    canonical_affect_fusion = None
    if callable(life_command_service_provider):
        # 能抵达 canonical Life 命令权威的生产调用方必须使用它；不能把遗留运行时作为
        # “备份”，否则会复活第二个可变自我情感写入者。
        canonical_affect_fusion = AffectFusionService(
            command_service_provider=life_command_service_provider,
            snapshot_reader_provider=require_host_provider(
                host_services,
                "life_snapshot_reader",
            ),
            relationship_lifecycle_service_provider=(
                relationship_lifecycle_service_provider
            ),
        )

    def resolve_legacy_affect_port(field_name: str) -> Callable[..., Any]:
        """canonical Life 组成后围栏化已退役的可变情感端口。

        遗留/离线组合为明确回滚窗口保留历史 provider；生产 Host 不能依赖每个消费者
        都记得检查权威标志，意外解析必须在打开 Bond 队列、可变 silence engine 或
        emotion timeline 前失败。
        """

        if canonical_affect_fusion is not None:
            return _retired_legacy_affect_port(field_name)
        return require_host_provider(host_services, field_name)

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
        raise RuntimeError("CANONICAL_AFFECT_FUSION_NOT_INJECTED")

    return TribeApplicationPorts(
        activity_monitor=resolved_activity_monitor_provider,
        mode_transition_service=resolve_process_mode_transition_service,
        ledger=ledger_provider,
        life_snapshot_reader=require_host_provider(host_services, "life_snapshot_reader"),
        silence_engine=resolve_legacy_affect_port("silence_engine"),
        state_write_arbiter=require_host_provider(host_services, "state_write_arbiter"),
        emotion_timeline=resolve_legacy_affect_port("emotion_timeline"),
        hard_state=hard_state_provider or _missing_host_hard_state_owner,
        resource_manager=resolved_resource_manager_provider,
        shared_memory_index=require_host_provider(host_services, "shared_memory_index"),
        milestone_tracker=require_host_provider(host_services, "milestone_tracker"),
        model_attempt_service=require_host_provider(host_services, "model_attempt_service"),
        prompt_registry=require_host_provider(host_services, "prompt_registry"),
        resolve_chat_emotion_context=resolve_chat_emotion_context_with_runtime,
        canonical_affect_fusion=(
            (lambda: canonical_affect_fusion)
            if canonical_affect_fusion is not None
            else None
        ),
        affective_life_authority=(
            CANONICAL_AFFECT_LIFE_JOURNAL
            if canonical_affect_fusion is not None
            else LEGACY_AFFECT_COMPATIBILITY
        ),
        build_chat_emotion_snapshot=build_chat_emotion_snapshot,
        build_user_preference_pack=build_user_preference_pack_with_ledger,
        build_conversation_experience_read_pack=(
            build_conversation_experience_read_pack_with_ledger
        ),
        build_conversation_understanding_pack=build_conversation_understanding_pack,
        allocate_intent_seq=intent_sequence_allocator.allocate,
        evolution_activation_service=optional_host_provider(
            host_services,
            "evolution_activation_service",
        ),
    )

def _retired_legacy_affect_port(
    field_name: str,
) -> Callable[..., Any]:
    """为退役可变端口返回显式闭合失败的 provider，防止静默写入分叉。"""

    normalized_name = str(field_name or "unknown").strip() or "unknown"

    def reject_legacy_access(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError(f"LEGACY_AFFECT_PORT_RETIRED:{normalized_name}")

    return reject_legacy_access


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
