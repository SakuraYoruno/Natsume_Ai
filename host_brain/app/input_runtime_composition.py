"""Application composition for the canonical Host input runtime."""

from __future__ import annotations

from host_brain.app.chat_handler import _evaluate_governor_with_timing
from host_brain.app.cortex_orchestrator import cortex_orchestrator_sse_generator
from host_brain.app.durable_task_status import build_durable_task_status_projection
from host_brain.app.emotion_context import (
    build_chat_emotion_snapshot,
    build_chat_governance_snapshot,
    resolve_chat_emotion_context,
)
from host_brain.app.multimodal_jobs import create_multimodal_job, update_multimodal_job
from host_brain.app.reply_form_decider import MsgType, ReplyFormDecision, get_reply_form_decider
from host_brain.app.schemas import ChatRequest
from host_brain.app.surface_reply_preprocessor import (
    render_quick_gateway_progress_text,
    render_quick_gateway_realign_text,
)
from host_brain.composition.process_services import (
    get_intent_engine,
    get_ledger,
    get_life_snapshot_reader,
    get_multimodal_observer,
    get_multimodal_processor,
    notify_user_interaction,
)
from host_brain.input_runtime.ports import InputRuntimeApplicationPorts
from host_brain.input_runtime.outbound_delivery import trigger_reply_performance


def build_input_runtime_application_ports() -> InputRuntimeApplicationPorts:
    """Build one immutable dependency graph for one Host application."""

    def get_task_status_projection():
        return build_durable_task_status_projection(ledger=get_ledger())

    return InputRuntimeApplicationPorts(
        get_intent_engine=get_intent_engine,
        get_ledger=get_ledger,
        get_life_snapshot_reader=get_life_snapshot_reader,
        get_multimodal_observer=get_multimodal_observer,
        get_multimodal_processor=get_multimodal_processor,
        get_task_status_projection=get_task_status_projection,
        notify_user_interaction=notify_user_interaction,
        evaluate_governor_with_timing=_evaluate_governor_with_timing,
        build_chat_emotion_snapshot=build_chat_emotion_snapshot,
        build_chat_governance_snapshot=build_chat_governance_snapshot,
        resolve_chat_emotion_context=resolve_chat_emotion_context,
        chat_request_factory=ChatRequest,
        cortex_orchestrator_sse_generator=cortex_orchestrator_sse_generator,
        create_multimodal_job=create_multimodal_job,
        update_multimodal_job=update_multimodal_job,
        reply_form_decision_type=ReplyFormDecision,
        get_reply_form_decider=get_reply_form_decider,
        message_type=MsgType,
        trigger_reply_performance=trigger_reply_performance,
        render_quick_gateway_progress_text=render_quick_gateway_progress_text,
        render_quick_gateway_realign_text=render_quick_gateway_realign_text,
    )


__all__ = ["build_input_runtime_application_ports"]
