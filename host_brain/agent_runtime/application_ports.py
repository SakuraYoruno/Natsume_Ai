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
    build_conversation_experience_read_pack: Callable[..., dict[str, Any]]
    conversation_history: Callable[[], Any]
    life_memory_service: Callable[[], Any]
    audit_ledger: Callable[[], Any]
    prompt_registry: Callable[[], Any]
    model_attempt_service: Callable[[], Any]
    context_read_ports: ConversationContextReadPorts
    task_status_projection: Callable[[], Any]
    persist_post_process_audit: Callable[..., Awaitable[None]]
    # Ordinary companion chat stays in the app-local Surface runtime instead
    # of paying the durable TaskGraph/Attempt/Lease/Budget lifecycle.  The
    # callable is optional so isolated read/worker fixtures remain small;
    # production composition always binds it.
    stream_realtime_chat: Callable[..., Any] | None = None
    # One bounded active-projection read.  Expression preferences are
    # self/persona scoped, not user-authored render policy, and therefore the
    # app adapter resolves their canonical workspace/subject before Surface
    # sees them.
    resolve_expression_preferences: Callable[..., dict[str, Any] | None] | None = None
    # Active Knowledge is consulted only after the Surface recall planner has
    # decided that this turn needs memory.  Ordinary no-recall chat pays no
    # Knowledge OS query.
    search_active_knowledge: Callable[..., list[dict[str, Any]]] | None = None
    # A release Identity may contribute a structured style base, but cannot
    # replace Soul or the current canonical affect state.
    resolve_active_identity: Callable[..., dict[str, Any] | None] | None = None


__all__ = [
    "AgentRuntimeApplicationPorts",
]
