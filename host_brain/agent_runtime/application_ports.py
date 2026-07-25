"""App-bound operations required by the canonical agent runtime."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from host_brain.agent_runtime.context_read_ports import ConversationContextReadPorts


@dataclass(frozen=True)
class AgentRuntimeApplicationPorts:
    record_user_turn: Callable[..., Any]
    record_assistant_turn: Callable[..., Any]
    run_retrieval: Callable[..., Awaitable[Any]]
    filter_retrieval_evidence_items_for_prompt: Callable[..., Any]
    build_user_preference_pack: Callable[..., Any]
    resolve_host_llm_runtime_async: Callable[..., Awaitable[Any]]
    resolve_surface_reply_llm_runtime: Callable[..., Any]
    resolve_state_arbiter_bond_fact: Callable[..., Awaitable[Any]]
    apply_surface_emotion_bond_flow: Callable[..., Any]
    build_conversation_experience_read_pack: Callable[..., dict[str, Any]]
    drive_stream_vts_emotion: Callable[..., Awaitable[Any]]
    resolve_tts_route: Callable[..., Any]
    conversation_history: Callable[[], Any]
    life_memory_service: Callable[[], Any]
    audit_ledger: Callable[[], Any]
    prompt_registry: Callable[[], Any]
    model_attempt_service: Callable[[], Any]
    context_read_ports: ConversationContextReadPorts
    task_status_projection: Callable[[], Any]
    persist_post_process_audit: Callable[..., Awaitable[None]]


__all__ = [
    "AgentRuntimeApplicationPorts",
]
