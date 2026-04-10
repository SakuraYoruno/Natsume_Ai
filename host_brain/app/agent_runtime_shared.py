"""agent_runtime_shared.py — app 外壳之间的中性共享运行时 helper。

这一层先承接旧 `top_agent.py` 中已经被多个入口直接复用的兼容能力，
把 `dispatch_agent.py` / `chat_agent.py` 对 `top_agent.py` 的硬耦合先切断，
后续再继续把真实实现逐步下沉到 tribe / observability / airlock 等稳定归属。
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from typing import Any

_logger = logging.getLogger("Natsume.agent_runtime_shared")


def _airlock_sanitize_output(text: str) -> str:
    """出口净化：null byte + 长度截断。"""
    text = text.replace("\x00", "").strip()
    max_chars = int(os.environ.get("HOST_QA_MAX_RESPONSE_CHARS") or "4000")
    if len(text) > max_chars:
        truncated = text[:max_chars]
        for ch in ("。", "！", "？", ".", "!", "?"):
            last = truncated.rfind(ch)
            if last > max_chars * 0.7:
                truncated = truncated[: last + 1]
                break
        text = truncated
    return text



def _build_reply_preview(text: str, *, limit: int = 120) -> str:
    normalized = str(text or "").strip().replace("\r", " ").replace("\n", " ")
    if len(normalized) <= int(limit):
        return normalized
    return normalized[: max(1, int(limit) - 3)].rstrip() + "..."


class _SubAgentGatewayAdapter:
    def __init__(
        self,
        *,
        provider_name: str,
        model_id: str,
        api_key: str,
        timeout_ms: int,
        base_url_override: str | None,
    ) -> None:
        from langchain_core.messages import AIMessage, HumanMessage, SystemMessage  # noqa: PLC0415
        from langchain_openai import ChatOpenAI  # noqa: PLC0415
        from host_brain.app.prompt_builder import extract_langchain_content_text  # noqa: PLC0415

        self._human_message = HumanMessage
        self._system_message = SystemMessage
        self._ai_message = AIMessage
        self._extract_text = extract_langchain_content_text
        self._llm = ChatOpenAI(
            model=str(model_id or "").strip() or "default",
            api_key=api_key,
            base_url=(str(base_url_override or "").strip() or None),
            timeout=max(1.0, float(timeout_ms) / 1000.0),
            max_retries=1,
            temperature=0.3,
        )
        self.provider_name = str(provider_name or "").strip().lower()

    def complete(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        lc_messages: list[Any] = []
        for row in list(messages or []):
            if not isinstance(row, Mapping):
                continue
            role = str(row.get("role") or "user").strip().lower()
            content = str(row.get("content") or "").strip()
            if not content:
                continue
            if role == "system":
                lc_messages.append(self._system_message(content=content))
            elif role == "assistant":
                lc_messages.append(self._ai_message(content=content))
            else:
                lc_messages.append(self._human_message(content=content))
        response = self._llm.invoke(lc_messages)
        return {"content": self._extract_text(getattr(response, "content", ""))}



def _build_subagent_gateway_from_runtime(llm_runtime: Mapping[str, Any]) -> Any | None:
    try:
        if not bool(llm_runtime.get("available")):
            return None
        provider_name = str(llm_runtime.get("provider_name") or "").strip()
        model_id = str(llm_runtime.get("model_id") or "").strip()
        api_key = str(llm_runtime.get("llm_api_key") or "").strip()
        timeout_ms = int(llm_runtime.get("timeout_ms") or 30_000)
        base_url_override = str(llm_runtime.get("base_url_override") or "").strip() or None
        if not provider_name or not model_id:
            return None
        return _SubAgentGatewayAdapter(
            provider_name=provider_name,
            model_id=model_id,
            api_key=api_key,
            timeout_ms=timeout_ms,
            base_url_override=base_url_override,
        )
    except Exception as exc:
        _logger.debug("sub-agent gateway adapter unavailable: %s", exc)
        return None



def _maybe_build_recent_turn_answer(
    *,
    request_id: str,
    session_id: str,
    person_id: str,
    user_message: str,
    render_target_lang: str,
) -> str | None:
    from host_brain.app.top_agent import _maybe_build_recent_turn_answer as _legacy_recent_turn_answer  # noqa: PLC0415

    return _legacy_recent_turn_answer(
        request_id=request_id,
        session_id=session_id,
        person_id=person_id,
        user_message=user_message,
        render_target_lang=render_target_lang,
    )


async def _async_post_process(
    *,
    request_id: str,
    person_id: str,
    session_id: str,
    user_message: str,
    narrative: str,
    emotion_context: dict[str, Any],
    bond_summary: dict[str, Any] | None,
    ts_ms: int,
    render_target_lang: str,
    total_latency_ms: int = 0,
    llm_latency_ms: int = 0,
    first_token_ms: int | None = None,
    provider_name: str = "",
    model_id: str = "",
    buffer_stream: bool = False,
    delegated_agents: list[dict[str, Any]] | None = None,
    retrieval_summary: dict[str, Any] | None = None,
    gov_decision: dict[str, Any] | None = None,
    pii_ingress_stats: dict[str, Any] | None = None,
    pii_egress_stats: dict[str, Any] | None = None,
    llm_input_digest: str | None = None,
    retrieval_evidence_items: list[dict[str, Any]] | None = None,
    motive_state: Mapping[str, Any] | None = None,
    response_policy: Mapping[str, Any] | None = None,
    trace_stage_meta: Mapping[str, Any] | None = None,
) -> None:
    from host_brain.app.top_agent import _async_post_process as _legacy_post_process  # noqa: PLC0415

    await _legacy_post_process(
        request_id=request_id,
        person_id=person_id,
        session_id=session_id,
        user_message=user_message,
        narrative=narrative,
        emotion_context=emotion_context,
        bond_summary=bond_summary,
        ts_ms=ts_ms,
        render_target_lang=render_target_lang,
        total_latency_ms=total_latency_ms,
        llm_latency_ms=llm_latency_ms,
        first_token_ms=first_token_ms,
        provider_name=provider_name,
        model_id=model_id,
        buffer_stream=buffer_stream,
        delegated_agents=delegated_agents,
        retrieval_summary=retrieval_summary,
        gov_decision=gov_decision,
        pii_ingress_stats=pii_ingress_stats,
        pii_egress_stats=pii_egress_stats,
        llm_input_digest=llm_input_digest,
        retrieval_evidence_items=retrieval_evidence_items,
        motive_state=motive_state,
        response_policy=response_policy,
        trace_stage_meta=trace_stage_meta,
    )


__all__ = [
    "_airlock_sanitize_output",
    "_build_reply_preview",
    "_build_subagent_gateway_from_runtime",
    "_maybe_build_recent_turn_answer",
    "_async_post_process",
]
