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
    get_task_status_projection: Callable[[], Any]
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
    trigger_reply_performance: Callable[..., Any]
    render_quick_gateway_progress_text: Callable[..., str]
    render_quick_gateway_realign_text: Callable[..., str]
    get_embodiment_runtime: Callable[[], Any | None] | None = None
    get_thalamus_agent: Callable[[], Any] | None = None
    get_ans_agent: Callable[[], Any] | None = None
    get_immune_agent: Callable[[], Any] | None = None

    def require_thalamus_agent(self) -> Any:
        return _require_runtime_agent(
            self.get_thalamus_agent,
            "HOST_INPUT_THALAMUS_AGENT_NOT_INJECTED",
        )

    def require_ans_agent(self) -> Any:
        return _require_runtime_agent(
            self.get_ans_agent,
            "HOST_INPUT_ANS_AGENT_NOT_INJECTED",
        )

    def require_immune_agent(self) -> Any:
        """Return the Host-composed input immune authority or fail closed."""

        provider = self.get_immune_agent
        if not callable(provider):
            raise RuntimeError("HOST_INPUT_IMMUNE_AGENT_NOT_INJECTED")
        try:
            agent = provider()
        except RuntimeError as exc:
            # The composition helper uses this generic code when the entire
            # Tribe bundle is absent. At this security boundary it is still
            # specifically an unavailable Immune authority.
            if str(exc) == "HOST_INPUT_TRIBE_RUNTIME_PORTS_NOT_INJECTED":
                raise RuntimeError("HOST_INPUT_IMMUNE_AGENT_NOT_INJECTED") from exc
            raise
        if agent is None:
            raise RuntimeError("HOST_INPUT_IMMUNE_AGENT_NOT_INJECTED")
        return agent


def _require_runtime_agent(
    provider: Callable[[], Any] | None,
    missing_code: str,
) -> Any:
    if not callable(provider):
        raise RuntimeError(missing_code)
    agent = provider()
    if agent is None:
        raise RuntimeError(missing_code)
    return agent


__all__ = ["InputRuntimeApplicationPorts"]
