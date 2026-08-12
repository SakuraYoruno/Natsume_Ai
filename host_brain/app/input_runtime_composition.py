"""规范 Host 输入运行时的应用层组合。

该工厂将聊天、Cortex、Tribe 与 canonical Life 的端口收敛为单一不可变依赖图；它不
创建第二个可写情感状态，也不在此层执行具身副作用。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from host_brain.agent_runtime.application_ports import AgentRuntimeApplicationPorts
from host_brain.app.chat_handler import _evaluate_governor_with_timing
from host_brain.app.cortex_orchestrator import cortex_orchestrator_sse_generator
from host_brain.app.durable_task_status import build_durable_task_status_projection
from host_brain.app.emotion_context import (
    build_chat_emotion_snapshot,
    build_chat_governance_snapshot,
    resolve_chat_emotion_context,
)
from host_brain.app.multimodal_jobs import create_multimodal_job, update_multimodal_job
from host_brain.app.reply_form_decider import (
    MsgType,
    ReplyFormDecision,
    ResponseFormDecider,
)
from host_brain.app.schemas import ChatRequest
from host_brain.app.surface_reply_preprocessor import (
    render_quick_gateway_progress_text,
    render_quick_gateway_realign_text,
)
from host_brain.app.host_lifecycle_composition import HostLifecycleApplicationPorts
from host_brain.app.tribe_composition import TribeRuntimeAgentPorts
from host_brain.digital_life.affect_fusion import AffectFusionService
from host_brain.digital_life.companion_profile_lifecycle import (
    CompanionProfileLifecycleService,
)
from host_brain.digital_life.relationship_lifecycle import RelationshipLifecycleService
from host_brain.input_runtime.ports import InputRuntimeApplicationPorts
from host_brain.input_runtime.outbound_delivery import trigger_reply_performance


def build_input_runtime_application_ports(
    *,
    host_services: HostLifecycleApplicationPorts | None = None,
    embodiment_runtime_provider: Callable[[], Any | None] | None = None,
    notify_user_interaction_callback: Callable[[], None] | None = None,
    agent_runtime_ports_provider: (
        Callable[[], AgentRuntimeApplicationPorts | None] | None
    ) = None,
    tribe_runtime_agent_ports: TribeRuntimeAgentPorts | None = None,
    life_command_service_provider: Callable[[], Any] | None = None,
) -> InputRuntimeApplicationPorts:
    """为一个 Host 应用构建不可变输入依赖图。

    Life 服务存在时，关系、陪伴配置与情感融合共享同一命令/快照权威；未注入时保留
    明确失败的 provider，而不以旧运行时替代，避免请求链出现第二个状态写入者。
    """

    get_intent_engine = _host_provider(host_services, "intent_engine")
    get_ledger = _host_provider(host_services, "ledger")
    get_life_snapshot_reader = _host_provider(host_services, "life_snapshot_reader")
    get_multimodal_observer = _host_provider(host_services, "multimodal_observer")
    get_multimodal_processor = _host_provider(host_services, "multimodal_processor")
    get_evolution_activation_service = _optional_host_provider(
        host_services,
        "evolution_activation_service",
    )
    get_evolution_scope = _optional_host_provider(
        host_services,
        "evolution_scope",
    )
    companion_profile_lifecycle_service = (
        CompanionProfileLifecycleService(
            command_service_provider=life_command_service_provider,
            snapshot_reader_provider=get_life_snapshot_reader,
        )
        if callable(life_command_service_provider)
        else None
    )
    relationship_lifecycle_service = (
        RelationshipLifecycleService(
            command_service_provider=life_command_service_provider,
            snapshot_reader_provider=get_life_snapshot_reader,
            companion_profile_lifecycle_service_provider=(
                (lambda: companion_profile_lifecycle_service)
                if companion_profile_lifecycle_service is not None
                else None
            ),
        )
        if callable(life_command_service_provider)
        else None
    )
    affect_fusion_service = (
        AffectFusionService(
            command_service_provider=life_command_service_provider,
            snapshot_reader_provider=get_life_snapshot_reader,
            relationship_lifecycle_service_provider=(
                (lambda: relationship_lifecycle_service)
                if relationship_lifecycle_service is not None
                else None
            ),
        )
        if callable(life_command_service_provider)
        else None
    )
    # A Host request may only resolve affect through the canonical Life
    # authority.  Do not construct HostEmotionRuntime as a composition
    # fallback: even an app-local fallback would restore a second mutable
    # self-affect writer when a caller accidentally omitted Life wiring.
    reply_form_decider = ResponseFormDecider()
    get_thalamus_agent = _tribe_agent_provider(
        tribe_runtime_agent_ports,
        "get_thalamus_agent",
    )
    get_immune_agent = _tribe_agent_provider(
        tribe_runtime_agent_ports,
        "get_immune_agent",
    )

    def get_task_status_projection():
        return build_durable_task_status_projection(ledger=get_ledger())

    async def trigger_reply_performance_with_runtime(**kwargs: Any) -> Any:
        # 兼容适配器保持无副作用；已接受的 ExpressionPlan 之后才经 LifeDeliveryTransport
        # 获取具身运行时，避免聊天处理阶段提前触发设备效果。
        return await trigger_reply_performance(**kwargs)

    async def cortex_orchestrator_with_app_ports(**kwargs: Any):  # noqa: ANN202
        """在请求期解析 Agent Runtime 端口并原样转发 Cortex 的异步 SSE 事件流。"""
        application_ports = (
            agent_runtime_ports_provider()
            if agent_runtime_ports_provider is not None
            else None
        )
        if not isinstance(application_ports, AgentRuntimeApplicationPorts):
            # 组合未完成时不得启动半配置 Cortex；调用方据此返回可诊断的入口失败。
            raise RuntimeError("HOST_AGENT_RUNTIME_PORTS_NOT_INJECTED")
        async for event in cortex_orchestrator_sse_generator(
            application_ports=application_ports,
            **kwargs,
        ):
            yield event

    def resolve_chat_emotion_context_with_runtime(**kwargs: Any) -> dict[str, Any]:
        """仅通过 canonical Life 情感融合解析聊天上下文，拒绝旧情感运行时回退。"""
        if affect_fusion_service is not None:
            return resolve_chat_emotion_context(
                affect_fusion_service=affect_fusion_service,
                **kwargs,
            )
        raise RuntimeError("CANONICAL_AFFECT_FUSION_NOT_INJECTED")

    return InputRuntimeApplicationPorts(
        get_intent_engine=get_intent_engine,
        get_ledger=get_ledger,
        get_life_snapshot_reader=get_life_snapshot_reader,
        get_multimodal_observer=get_multimodal_observer,
        get_multimodal_processor=get_multimodal_processor,
        get_task_status_projection=get_task_status_projection,
        notify_user_interaction=(
            notify_user_interaction_callback or _missing_app_local_provider
        ),
        evaluate_governor_with_timing=_evaluate_governor_with_timing,
        build_chat_emotion_snapshot=build_chat_emotion_snapshot,
        build_chat_governance_snapshot=build_chat_governance_snapshot,
        resolve_chat_emotion_context=resolve_chat_emotion_context_with_runtime,
        chat_request_factory=ChatRequest,
        cortex_orchestrator_sse_generator=cortex_orchestrator_with_app_ports,
        create_multimodal_job=create_multimodal_job,
        update_multimodal_job=update_multimodal_job,
        reply_form_decision_type=ReplyFormDecision,
        get_reply_form_decider=lambda: reply_form_decider,
        message_type=MsgType,
        trigger_reply_performance=trigger_reply_performance_with_runtime,
        render_quick_gateway_progress_text=render_quick_gateway_progress_text,
        render_quick_gateway_realign_text=render_quick_gateway_realign_text,
        get_embodiment_runtime=embodiment_runtime_provider,
        get_thalamus_agent=get_thalamus_agent,
        get_immune_agent=get_immune_agent,
        get_evolution_activation_service=(
            get_evolution_activation_service
        ),
        get_evolution_scope=get_evolution_scope,
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


def _tribe_agent_provider(
    ports: TribeRuntimeAgentPorts | None,
    field_name: str,
) -> Callable[[], Any]:
    if isinstance(ports, TribeRuntimeAgentPorts):
        return getattr(ports, field_name)
    return _missing_tribe_agent_provider


def _missing_tribe_agent_provider(*_args: Any, **_kwargs: Any) -> Any:
    raise RuntimeError("HOST_INPUT_TRIBE_RUNTIME_PORTS_NOT_INJECTED")


__all__ = ["build_input_runtime_application_ports"]
