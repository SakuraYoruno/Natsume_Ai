"""top_agent.py — 顶级 Agent 兼容外壳。

真实运行时实现已经下沉到 `host_brain.app.agent_runtime_shared`。
本文件只保留：
1. 稳定导出的入口函数；
2. 兼容旧测试 / 猴补丁的薄包装层；
3. 对历史 helper 名字的再导出。
"""

from __future__ import annotations

import re
from typing import Any, AsyncGenerator, Mapping

from host_brain.app.agent_runtime_shared import (
    _SubAgentGatewayAdapter as _shared_SubAgentGatewayAdapter,
    _airlock_sanitize_input,
    _airlock_sanitize_output,
    _async_post_process as _shared_async_post_process,
    _build_reply_preview,
    _build_retrieval_bond_summary,
    _build_subagent_gateway_from_runtime as _shared_build_subagent_gateway_from_runtime,
    _build_trace_agent_envelope,
    _build_trace_output_digest,
    _build_trace_stage_node,
    _coerce_trace_bool,
    _coerce_trace_mapping,
    _coerce_trace_score,
    _detect_recent_turn_query_role as _shared_detect_recent_turn_query_role,
    _detect_trace_fallback_used,
    _extract_delegate_from_llm_output,
    _extract_delegate_visible_text as _shared_extract_delegate_visible_text,
    _extract_visible_text,
    _format_template_text,
    _infer_emotion_tag_for_tts as _shared_infer_emotion_tag_for_tts,
    _load_continuity_turns,
    _load_runtime_texts,
    _load_template_mapping,
    _looks_like_active_task_context,
    _mapping_section,
    _mapping_text,
    _maybe_build_phatic_fastpath_reply as _shared_maybe_build_phatic_fastpath_reply,
    _maybe_build_previous_meaning_answer,
    _maybe_build_recent_turn_answer,
    _maybe_build_task_progress_fastpath as _shared_maybe_build_task_progress_fastpath,
    _normalize_retrieval_summary_for_audit,
    _normalize_trace_stage_meta,
    _parse_bool_env,
    _persist_post_process_audit,
    _pick_non_repeating_variant,
    _prefetch_retrieval_persona_inputs,
    _render_delegated_result_naturally as _shared_render_delegated_result_naturally,
    _resolve_envelope_confidence,
    _resolve_persona_session_emotion,
    _resolve_retrieval_timeout_seconds,
    _run_persona_session_end_hooks,
    _should_buffer_stream_output,
    _sse,
    _summarize_emotion_context_for_trace,
    _summarize_motive_state_for_trace,
    _summarize_persona_resident_memory_for_trace,
    _summarize_response_policy_for_trace,
    _top_agent_sse_generator_impl,
)

_RECENT_TURN_USER_QUERY_RE = re.compile(r"(我刚才|我上一句|我上句|我前一句|我前面).*?(说了什么|发了什么|是什么)")
_RECENT_TURN_ASSISTANT_QUERY_RE = re.compile(r"(你刚才|你上一句|你上句|你前一句|你前面).*?(说了什么|回了什么|回复了什么|是什么)")
_RECENT_TURN_GENERIC_QUERY_RE = re.compile(r"(上一句|上句|前一句|刚才说的什么|刚刚说的什么).*?(是什么|是什么来着|说了什么)?")
_CONTINUATION_QUERY_RE = re.compile(r"^(然后|然后呢|继续|接着|接着呢|后面呢|再然后呢|展开说说|具体一点|继续说|接着来|那后面呢)[!！。…~]*$")
_GREETING_FASTPATH_RE = re.compile(r"^(你好|你好啊|你好呀|您好|嗨|哈喽|早上好|中午好|晚上好)[!！。…~]*$")
_PRESENCE_FASTPATH_RE = re.compile(r"^(在吗|在不在|还在吗|忙吗|在嘛)[?？!！。…~]*$")
_ACK_FASTPATH_RE = re.compile(r"^(嗯|嗯嗯|哦|噢|好的|好呀|好啊|可以|可以啊|可以呀|收到|对|对啊|是的|行)[!！。…~]*$")
_TASK_PROGRESS_QUERY_RE = re.compile(r"(进度|做到哪|到哪了|还在.*(执行|跑|做)|上个任务.*(怎么样|进度)|刚才.*任务.*(怎么样|进度)|修好了吗|处理到哪)")
_TASK_FAILURE_QUERY_RE = re.compile(r"(哪一步.*失败|哪个步骤.*失败|为什么失败|失败原因|哪一步没成功|哪个步骤没成功)")
_TASK_STEP_INDEX_RE = re.compile(r"第\s*(\d+)\s*步")
_EMOTION_INFER_KEYWORDS: list[tuple[str, str]] = [
    ("HAPPY", "哈哈|开心|高兴|嘿嘿|笑|太好了|耶|うれしい|楽しい|よかった"),
    ("SAD", "难过|伤心|哭|呜|抱歉|对不起|悲しい|つらい|ごめん"),
    ("ANGRY", "生气|气死|烦|腹立|むかつく"),
    ("LOVING", "谢谢|感谢|感动|喜欢|爱你|好き|大好き|愛してる|会いたかった"),
    ("SURPRISED", "哇|真的吗|竟然|びっくり|えっ|本当"),
]

_SubAgentGatewayAdapter = _shared_SubAgentGatewayAdapter


def _build_subagent_gateway_from_runtime(llm_runtime: Mapping[str, Any]) -> Any | None:
    return _shared_build_subagent_gateway_from_runtime(
        llm_runtime,
        adapter_class=_SubAgentGatewayAdapter,
    )


def _detect_recent_turn_query_role(user_message: str) -> str | None:
    return _shared_detect_recent_turn_query_role(
        user_message,
        user_query_re=_RECENT_TURN_USER_QUERY_RE,
        assistant_query_re=_RECENT_TURN_ASSISTANT_QUERY_RE,
        generic_query_re=_RECENT_TURN_GENERIC_QUERY_RE,
    )


def _extract_delegate_visible_text(text: str) -> str:
    return _shared_extract_delegate_visible_text(text)


def _render_delegated_result_naturally(*, agent_id: str, result_text: str, reply_template: str | None) -> str:
    return _shared_render_delegated_result_naturally(
        agent_id=agent_id,
        result_text=result_text,
        reply_template=reply_template,
        extract_delegate_visible_text=_extract_delegate_visible_text,
    )


def _maybe_build_task_progress_fastpath(
    *,
    request_id: str,
    session_id: str,
    person_id: str,
    user_message: str,
) -> str | None:
    return _shared_maybe_build_task_progress_fastpath(
        request_id=request_id,
        session_id=session_id,
        person_id=person_id,
        user_message=user_message,
        task_progress_query_re=_TASK_PROGRESS_QUERY_RE,
        task_failure_query_re=_TASK_FAILURE_QUERY_RE,
        task_step_index_re=_TASK_STEP_INDEX_RE,
    )


def _maybe_build_phatic_fastpath_reply(
    *,
    request_id: str,
    session_id: str,
    person_id: str,
    user_message: str,
    render_target_lang: str,
) -> str | None:
    return _shared_maybe_build_phatic_fastpath_reply(
        request_id=request_id,
        session_id=session_id,
        person_id=person_id,
        user_message=user_message,
        render_target_lang=render_target_lang,
        load_continuity_turns=_load_continuity_turns,
        looks_like_active_task_context=_looks_like_active_task_context,
        continuation_query_re=_CONTINUATION_QUERY_RE,
        greeting_fastpath_re=_GREETING_FASTPATH_RE,
        presence_fastpath_re=_PRESENCE_FASTPATH_RE,
        ack_fastpath_re=_ACK_FASTPATH_RE,
    )


def _infer_emotion_tag_for_tts(narrative: str, current_tag: str) -> str:
    return _shared_infer_emotion_tag_for_tts(
        narrative,
        current_tag,
        emotion_infer_keywords=_EMOTION_INFER_KEYWORDS,
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
    await _shared_async_post_process(
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


async def top_agent_sse_generator(
    *,
    request_body: Any,
    request_id: str,
    session_id: str,
    ts_ms: int,
    intent_draft: dict[str, Any],
    emotion_context: dict[str, Any],
    emotion_snapshot: dict[str, Any],
    governor_result: dict[str, Any],
    intent_id: str | None,
    metrics: Any,
) -> AsyncGenerator[str, None]:
    async for event in _top_agent_sse_generator_impl(
        request_body=request_body,
        request_id=request_id,
        session_id=session_id,
        ts_ms=ts_ms,
        intent_draft=intent_draft,
        emotion_context=emotion_context,
        emotion_snapshot=emotion_snapshot,
        governor_result=governor_result,
        intent_id=intent_id,
        metrics=metrics,
        sse_fn=_sse,
        airlock_sanitize_input_fn=_airlock_sanitize_input,
        airlock_sanitize_output_fn=_airlock_sanitize_output,
        maybe_build_task_progress_fastpath_fn=_maybe_build_task_progress_fastpath,
        maybe_build_recent_turn_answer_fn=_maybe_build_recent_turn_answer,
        maybe_build_previous_meaning_answer_fn=_maybe_build_previous_meaning_answer,
        maybe_build_phatic_fastpath_reply_fn=_maybe_build_phatic_fastpath_reply,
        async_post_process_fn=_async_post_process,
        resolve_retrieval_timeout_seconds_fn=_resolve_retrieval_timeout_seconds,
        prefetch_retrieval_persona_inputs_fn=_prefetch_retrieval_persona_inputs,
    ):
        yield event


__all__ = [
    "_SubAgentGatewayAdapter",
    "_airlock_sanitize_input",
    "_airlock_sanitize_output",
    "_async_post_process",
    "_build_reply_preview",
    "_build_retrieval_bond_summary",
    "_build_subagent_gateway_from_runtime",
    "_build_trace_agent_envelope",
    "_build_trace_output_digest",
    "_build_trace_stage_node",
    "_coerce_trace_bool",
    "_coerce_trace_mapping",
    "_coerce_trace_score",
    "_CONTINUATION_QUERY_RE",
    "_GREETING_FASTPATH_RE",
    "_PRESENCE_FASTPATH_RE",
    "_ACK_FASTPATH_RE",
    "_TASK_PROGRESS_QUERY_RE",
    "_TASK_FAILURE_QUERY_RE",
    "_TASK_STEP_INDEX_RE",
    "_RECENT_TURN_USER_QUERY_RE",
    "_RECENT_TURN_ASSISTANT_QUERY_RE",
    "_RECENT_TURN_GENERIC_QUERY_RE",
    "_EMOTION_INFER_KEYWORDS",
    "_detect_recent_turn_query_role",
    "_detect_trace_fallback_used",
    "_extract_delegate_from_llm_output",
    "_extract_delegate_visible_text",
    "_extract_visible_text",
    "_format_template_text",
    "_infer_emotion_tag_for_tts",
    "_load_continuity_turns",
    "_load_runtime_texts",
    "_load_template_mapping",
    "_looks_like_active_task_context",
    "_mapping_section",
    "_mapping_text",
    "_maybe_build_phatic_fastpath_reply",
    "_maybe_build_previous_meaning_answer",
    "_maybe_build_recent_turn_answer",
    "_maybe_build_task_progress_fastpath",
    "_normalize_retrieval_summary_for_audit",
    "_normalize_trace_stage_meta",
    "_parse_bool_env",
    "_persist_post_process_audit",
    "_pick_non_repeating_variant",
    "_prefetch_retrieval_persona_inputs",
    "_render_delegated_result_naturally",
    "_resolve_envelope_confidence",
    "_resolve_persona_session_emotion",
    "_resolve_retrieval_timeout_seconds",
    "_run_persona_session_end_hooks",
    "_should_buffer_stream_output",
    "_sse",
    "_summarize_emotion_context_for_trace",
    "_summarize_motive_state_for_trace",
    "_summarize_persona_resident_memory_for_trace",
    "_summarize_response_policy_for_trace",
    "top_agent_sse_generator",
]
