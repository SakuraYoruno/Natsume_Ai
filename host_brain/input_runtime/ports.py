"""Typed application boundary for the canonical Host input runtime."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class InputRuntimeApplicationPorts:
    """Immutable collaborators supplied by the application composition root."""

    get_intent_engine: Callable[[], Any]
    get_ledger: Callable[[], Any]
    get_life_snapshot_reader: Callable[[], Any]
    get_multimodal_observer: Callable[[], Any]
    get_multimodal_processor: Callable[[], Any]
    get_runtime_task_registry: Callable[[], Any]
    notify_user_interaction: Callable[[], None]
    evaluate_governor_with_timing: Callable[..., Any]
    build_chat_emotion_snapshot: Callable[..., Any]
    build_chat_governance_snapshot: Callable[..., Any]
    resolve_chat_emotion_context: Callable[..., Any]
    chat_request_factory: Callable[..., Any]
    cortex_orchestrator_sse_generator: Callable[..., Any]
    create_multimodal_job: Callable[..., Any]
    update_multimodal_job: Callable[..., Any]
    reply_form_decision_type: type[Any]
    get_reply_form_decider: Callable[[], Any]
    message_type: type[Any]
    render_quick_gateway_progress_text: Callable[..., str]
    render_quick_gateway_realign_text: Callable[..., str]


__all__ = ["InputRuntimeApplicationPorts"]
