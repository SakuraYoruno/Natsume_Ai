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


class _SafeTemplateDict(dict[str, Any]):
    def __missing__(self, key: object) -> str:
        return "{" + str(key) + "}"



def _format_template_text(template_text: Any, **kwargs: Any) -> str:
    text = str(template_text or "")
    if not kwargs:
        return text
    payload = _SafeTemplateDict(
        {
            str(key): "" if value is None else str(value)
            for key, value in kwargs.items()
        }
    )
    try:
        return text.format_map(payload)
    except Exception:
        return text



def _load_template_mapping(template_id: str) -> dict[str, Any]:
    raw = resolve_prompt_template_json_merged(template_id)
    return dict(raw) if isinstance(raw, Mapping) else {}



def _load_runtime_texts() -> dict[str, Any]:
    return _load_template_mapping("top_agent.runtime_texts")



def _mapping_section(mapping: Mapping[str, Any], key: str) -> dict[str, Any]:
    raw = mapping.get(key)
    return dict(raw) if isinstance(raw, Mapping) else {}



def _mapping_text(
    mapping: Mapping[str, Any],
    key: str,
    *,
    default: str = "",
    **kwargs: Any,
) -> str:
    return _format_template_text(mapping.get(key, default), **kwargs)


_RECENT_TURN_USER_QUERY_RE = re.compile(r"(我刚才|我上一句|我上句|我前一句|我前面).*?(说了什么|发了什么|是什么)")
_RECENT_TURN_ASSISTANT_QUERY_RE = re.compile(r"(你刚才|你上一句|你上句|你前一句|你前面).*?(说了什么|回了什么|回复了什么|是什么)")
_RECENT_TURN_GENERIC_QUERY_RE = re.compile(r"(上一句|上句|前一句|刚才说的什么|刚刚说的什么).*?(是什么|是什么来着|说了什么)?")

_EMOTION_INFER_KEYWORDS: list[tuple[str, str]] = [
    ("HAPPY", "哈哈|开心|高兴|嘿嘿|笑|太好了|耶|うれしい|楽しい|よかった"),
    ("SAD", "难过|伤心|哭|呜|抱歉|对不起|悲しい|つらい|ごめん"),
    ("ANGRY", "生气|气死|烦|腹立|むかつく"),
    ("LOVING", "谢谢|感谢|感动|喜欢|爱你|好き|大好き|愛してる|会いたかった"),
    ("SURPRISED", "哇|真的吗|竟然|びっくり|えっ|本当"),
]


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



def _detect_recent_turn_query_role(user_message: str) -> str | None:
    normalized = str(user_message or "").strip()
    if not normalized:
        return None
    if _RECENT_TURN_USER_QUERY_RE.search(normalized):
        return "user"
    if _RECENT_TURN_ASSISTANT_QUERY_RE.search(normalized):
        return "assistant"
    if _RECENT_TURN_GENERIC_QUERY_RE.search(normalized):
        return "both"
    return None



def _load_continuity_turns(
    *,
    request_id: str,
    session_id: str,
    person_id: str,
    render_target_lang: str,
    fetch_limit: int = 24,
) -> list[dict[str, str]]:
    try:
        from host_brain.app.dependencies import get_ledger  # noqa: PLC0415
        from host_brain.app.top_agent_prompt import _fetch_continuity_rows, _voice_rows_to_turns  # noqa: PLC0415

        rows = _fetch_continuity_rows(
            ledger=get_ledger(),
            session_id=session_id,
            person_id=person_id,
            request_id=request_id,
            fetch_limit=fetch_limit,
        )
        return _voice_rows_to_turns(rows=rows, render_target_lang=render_target_lang)
    except Exception as exc:
        _logger.debug("[%s] continuity lookup failed: %s", request_id, exc)
        return []



def _maybe_build_recent_turn_answer(
    *,
    request_id: str,
    session_id: str,
    person_id: str,
    user_message: str,
    render_target_lang: str,
) -> str | None:
    query_role = _detect_recent_turn_query_role(user_message)
    if query_role is None:
        return None

    turns = _load_continuity_turns(
        request_id=request_id,
        session_id=session_id,
        person_id=person_id,
        render_target_lang=render_target_lang,
        fetch_limit=24,
    )
    if not turns:
        return None

    def _finalize_recent_turn_text(candidate: str, *, fallback: str) -> str:
        normalized = str(candidate or "").strip()
        if not normalized:
            return fallback
        if any(
            token in normalized
            for token in ("已确认事实", "请直接告诉用户", "请直接回答", "不要再说看不到", "不要再说没看到")
        ):
            return fallback
        return normalized

    latest_user = next(
        (
            str(turn.get("content") or "").strip()
            for turn in reversed(turns)
            if str(turn.get("role") or "").strip().lower() == "user"
            and str(turn.get("content") or "").strip()
        ),
        "",
    )
    latest_assistant = next(
        (
            str(turn.get("content") or "").strip()
            for turn in reversed(turns)
            if str(turn.get("role") or "").strip().lower() == "assistant"
            and str(turn.get("content") or "").strip()
        ),
        "",
    )

    templates = _mapping_section(_load_runtime_texts(), "recent_turn_answer")
    if query_role == "user" and latest_user:
        fallback = f"你上一句是「{latest_user}」。"
        return _finalize_recent_turn_text(
            _mapping_text(
                templates,
                "user_template",
                latest_user=latest_user,
                default=fallback,
            ),
            fallback=fallback,
        )
    if query_role == "assistant" and latest_assistant:
        fallback = f"我上一句说的是「{latest_assistant}」。"
        return _finalize_recent_turn_text(
            _mapping_text(
                templates,
                "assistant_template",
                latest_assistant=latest_assistant,
                default=fallback,
            ),
            fallback=fallback,
        )
    if query_role == "both":
        parts: list[str] = []
        if latest_user:
            parts.append(
                _mapping_text(
                    templates,
                    "both_user_item_template",
                    latest_user=latest_user,
                    default="你上一句是「{latest_user}」",
                )
            )
        if latest_assistant:
            parts.append(
                _mapping_text(
                    templates,
                    "both_assistant_item_template",
                    latest_assistant=latest_assistant,
                    default="我上一句说的是「{latest_assistant}」",
                )
            )
        if parts:
            fallback = "；".join(parts) + "。"
            return _finalize_recent_turn_text(
                _mapping_text(
                    templates,
                    "both_wrapper_template",
                    facts="；".join(parts),
                    default=fallback,
                ),
                fallback=fallback,
            )
    return None



def _resolve_persona_session_emotion(emotion_context: Mapping[str, Any]) -> tuple[float, float]:
    """为人格 session_end 提取可用的 valence / arousal。"""
    payload = dict(emotion_context or {})
    vad_payload = payload.get("vad") if isinstance(payload.get("vad"), Mapping) else {}

    raw_valence = payload.get("valence")
    if raw_valence is None:
        raw_valence = vad_payload.get("valence")

    raw_arousal = payload.get("arousal")
    if raw_arousal is None:
        raw_arousal = vad_payload.get("arousal")

    def _coerce(value: Any, default: float) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return float(default)

    if raw_valence is not None or raw_arousal is not None:
        valence = max(-1.0, min(1.0, _coerce(raw_valence, 0.0)))
        arousal = max(0.0, min(1.0, _coerce(raw_arousal, 0.3)))
        return valence, arousal

    emotion_tag = str(payload.get("emotion_tag") or "NEUTRAL").strip().upper()
    intensity = max(0.0, min(1.0, _coerce(payload.get("emotion_intensity"), 0.5)))
    fallback_vad = {
        "HAPPY": (0.7, 0.65),
        "LOVING": (0.8, 0.45),
        "EXCITED": (0.75, 0.85),
        "SURPRISED": (0.2, 0.8),
        "SAD": (-0.6, 0.25),
        "ANGRY": (-0.7, 0.8),
        "NEUTRAL": (0.0, 0.3),
    }
    base_valence, base_arousal = fallback_vad.get(emotion_tag, (0.0, 0.3))
    valence = max(-1.0, min(1.0, float(base_valence) * max(0.4, intensity)))
    arousal = max(0.0, min(1.0, max(float(base_arousal), intensity)))
    return valence, arousal


async def _run_persona_session_end_hooks(
    *,
    request_id: str,
    person_id: str,
    session_id: str,
    user_message: str,
    emotion_context: Mapping[str, Any],
) -> None:
    """统一触发人格 session_end/conversation_end 收口。"""
    try:
        from host_brain.bond.user_identity import get_canonical_bond_user_id  # noqa: PLC0415
        from host_brain.tribe.limbic_agent import get_limbic_agent  # noqa: PLC0415

        persona_user_id = get_canonical_bond_user_id()
        valence, arousal = _resolve_persona_session_emotion(emotion_context)
        pending_topics = []
        topic_preview = _build_reply_preview(user_message, limit=64)
        if topic_preview:
            pending_topics = [topic_preview]
        await asyncio.to_thread(
            get_limbic_agent().on_session_end,
            person_id=persona_user_id,
            valence=valence,
            arousal=arousal,
            pending_topics=pending_topics,
        )
    except Exception as exc:
        _logger.debug("[%s] persona inner_world session_end failed: %s", request_id, exc)

    try:
        from host_brain.tribe.limbic_agent import get_limbic_agent  # noqa: PLC0415

        await asyncio.to_thread(
            get_limbic_agent().on_conversation_ended,
            request_id=request_id,
            session_id=session_id,
            person_id=person_id,
        )
    except Exception as exc:
        _logger.debug("[%s] persona body_sense conversation_end failed: %s", request_id, exc)



def _infer_emotion_tag_for_tts(narrative: str, current_tag: str) -> str:
    """当 current_tag 为 NEUTRAL 时，根据回复文案推断 emotion_tag。"""
    if not narrative or str(current_tag or "").strip().upper() != "NEUTRAL":
        return str(current_tag or "NEUTRAL").strip().upper() or "NEUTRAL"
    text = (narrative or "")[:500]
    for tag, pattern in _EMOTION_INFER_KEYWORDS:
        if re.search(pattern, text, re.IGNORECASE):
            return tag
    return "NEUTRAL"


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
    "_resolve_persona_session_emotion",
    "_run_persona_session_end_hooks",
    "_infer_emotion_tag_for_tts",
    "_async_post_process",
]

