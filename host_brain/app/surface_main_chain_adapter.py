from __future__ import annotations

import asyncio
import json
import os
import time
from collections.abc import AsyncGenerator, Mapping, Sequence
from typing import Any

from host_brain.app.context.soul_runtime import get_compiled_soul
from host_brain.app.reply_envelope import ReplyEnvelope
from host_brain.app.response_form_contract import normalize_response_form
from host_brain.persona.surface_affect_control import build_surface_affect_control_packet
from host_brain.persona.surface_main_chain import SurfaceMainChain, SurfaceMainChainInput, SurfaceMainChainResult
from host_brain.persona.surface_memory_recall import MemoryRecallPacket, memory_recall_packet_from_mapping


_MEMORY_PACKET_KEYS = (
    "surface_memory_recall_packet",
    "memory_recall_packet",
    "memory_packet",
)
_TEXT_RESPONSE_FORMS = {"", "text", "plain_text", "chat_text", "zh_text"}


def surface_main_chain_enabled(agent_input: Any, *, sub_agent_ctx: Any | None = None) -> bool:
    if not _surface_main_chain_default_enabled():
        return False
    if sub_agent_ctx is not None:
        return False
    if _is_non_surface_intent(agent_input):
        return False
    if _has_multimodal_payload(agent_input):
        return False
    response_form = _response_form(agent_input)
    return response_form in _TEXT_RESPONSE_FORMS


def build_surface_main_chain_input_from_chat_input(agent_input: Any) -> SurfaceMainChainInput:
    soul = get_compiled_soul()
    reply_plan = _reply_plan(agent_input)
    task_mode = _first_nonempty(
        reply_plan.get("primary_topic"),
        reply_plan.get("response_form_reason"),
        reply_plan.get("response_goal"),
        _safe_mapping(getattr(agent_input, "intent_draft", None)).get("route"),
        "daily_chat",
    )
    response_move = _first_nonempty(
        reply_plan.get("interaction_move"),
        reply_plan.get("response_goal"),
        reply_plan.get("tactic_variant"),
        task_mode,
    )
    task_control = {
        "response_move": response_move,
        "task_mode": task_mode,
        "information_budget": _first_nonempty(reply_plan.get("information_budget"), "short"),
        "question_budget": _coerce_int(reply_plan.get("max_questions"), default=0),
        "must_include": _clean_list(_coerce_sequence(reply_plan.get("must_include"))),
        "must_not_do": _clean_list(_coerce_sequence(reply_plan.get("must_not_do"))),
        "facts": _task_facts(agent_input=agent_input, reply_plan=reply_plan),
        "style_anchor": _clean_list(
            [
                "app_surface_main_chain",
                _first_nonempty(reply_plan.get("style_anchor"), ""),
            ]
        ),
    }
    affect_packet = build_surface_affect_control_packet(
        emotion_state=_safe_mapping(getattr(agent_input, "emotion_context", None))
        or _safe_mapping(getattr(agent_input, "emotion_snapshot", None)),
        bond_summary=_safe_mapping(getattr(agent_input, "bond_summary", None)),
        relationship_clues=_relationship_clues(agent_input),
        task_mode=task_mode,
    )
    return SurfaceMainChainInput(
        soul_md=str(getattr(soul, "source_text", "") or getattr(soul, "runtime_full_text", "")).strip(),
        user_message=str(getattr(agent_input, "user_message", "") or "").strip(),
        case_id=str(getattr(agent_input, "request_id", "") or "").strip(),
        task_control=task_control,
        memory_packet=_memory_packet(agent_input),
        affect_packet=affect_packet,
        necessary_facts=_necessary_facts(agent_input),
        must_not_do=(),
        style_anchor=("app 层只传压缩控制包，不传原始历史。",),
    )


async def run_surface_main_chain_for_chat_input(agent_input: Any) -> SurfaceMainChainResult:
    llm_runtime = _safe_mapping(getattr(agent_input, "llm_runtime", None))
    request_id = str(getattr(agent_input, "request_id", "") or "").strip()

    async def _generate(messages: list[dict[str, str]]) -> str:
        from host_brain.integrations.llm_helpers import acomplete_llm_with_pool  # noqa: PLC0415

        if not bool(llm_runtime.get("available")):
            raise RuntimeError("surface main chain llm runtime unavailable")
        result = await acomplete_llm_with_pool(
            request_id=request_id,
            scene_type="CHAT_SURFACE",
            cognitive_tier="A",
            provider_name=str(llm_runtime.get("provider_name") or "").strip(),
            model_id=str(llm_runtime.get("model_id") or "").strip(),
            api_key=str(llm_runtime.get("llm_api_key") or "").strip(),
            timeout_ms=int(llm_runtime.get("timeout_ms") or 30_000),
            messages=messages,
            gen_params=_surface_gen_params(llm_runtime),
            base_url_override=str(llm_runtime.get("base_url_override") or "").strip() or None,
        )
        return str(getattr(result, "content", "") or "").strip()

    return await SurfaceMainChain(_generate).run(build_surface_main_chain_input_from_chat_input(agent_input))


async def surface_main_chain_sse_generator(
    agent_input: Any,
    *,
    trace_stage_meta: Mapping[str, Any] | None = None,
) -> AsyncGenerator[str, None]:
    started_ms = int(time.time() * 1000)
    trace = dict(trace_stage_meta or {})
    request_id = str(getattr(agent_input, "request_id", "") or "").strip()
    try:
        result = await run_surface_main_chain_for_chat_input(agent_input)
    except Exception as exc:
        total_latency_ms = max(0, int(time.time() * 1000) - started_ms)
        envelope = ReplyEnvelope.no_reply(
            reason_code="SURFACE_MAIN_CHAIN_FAILED",
            source="surface_main_chain",
            audit={"request_id": request_id, "error": type(exc).__name__},
        )
        yield _sse(
            {
                "phase": "COMMIT",
                "done": True,
                "decision": "NO_REPLY",
                "reason_code": envelope.reason_code,
                "narrative": "",
                "request_id": request_id,
                "reply_envelope": envelope.as_dict(),
                "audit_refs": {
                    "request_id": request_id,
                    "path": "chat_agent:surface_main_chain",
                    "latency_ms": total_latency_ms,
                    "reason_code": envelope.reason_code,
                    "timings_ms": _surface_timings_ms(
                        total_latency_ms=total_latency_ms,
                        llm_latency_ms=0,
                    ),
                    "trace_stage_meta": {**trace, "surface_main_chain_used": True},
                },
            }
        )
        return

    narrative = str(result.selected_text or "").strip()
    total_latency_ms = max(0, int(time.time() * 1000) - started_ms)
    llm_latency_ms = int(result.runtime.latency_ms or 0)
    timings_ms = _surface_timings_ms(
        total_latency_ms=total_latency_ms,
        llm_latency_ms=llm_latency_ms,
    )
    envelope = ReplyEnvelope.final_reply(
        text=narrative,
        response_form="text",
        source="surface_main_chain",
        audit={"request_id": request_id, "observation_codes": list(result.observation_codes)},
    )
    if narrative:
        yield _sse({"phase": "RENDER", "chunk": narrative})
        yield _sse({"phase": "SENTENCE", "sentence": narrative, "sentence_index": 0})
    yield _sse(
        {
            "phase": "COMMIT",
            "done": True,
            "decision": "APPROVE" if envelope.sendable else "NO_REPLY",
            "reason_code": envelope.reason_code or None,
            "narrative": narrative if envelope.sendable else "",
            "request_id": request_id,
            "reply_envelope": envelope.as_dict(),
            "audit_refs": {
                "request_id": request_id,
                "path": "chat_agent:surface_main_chain",
                "latency_ms": total_latency_ms,
                "first_token_ms": llm_latency_ms if narrative else None,
                "llm_latency_ms": llm_latency_ms,
                "model_id": _safe_mapping(getattr(agent_input, "llm_runtime", None)).get("model_id"),
                "provider_name": _safe_mapping(getattr(agent_input, "llm_runtime", None)).get("provider_name"),
                "response_form": "text",
                "timings_ms": timings_ms,
                "reply_envelope": envelope.as_dict(),
                "surface_main_chain": _public_result_trace(result),
                "trace_stage_meta": {
                    **trace,
                    "surface_main_chain_used": True,
                    "chat_kernel_v2_used": False,
                    "reply_source": "surface_main_chain",
                    "surface_renderer": {
                        "status": "OK",
                        "invoked": True,
                        "affected_output": bool(narrative),
                        "source": "surface_main_chain",
                        "reason_code": "",
                    },
                },
            },
        }
    )
    _schedule_surface_post_process(
        agent_input=agent_input,
        result=result,
        envelope=envelope,
        narrative=narrative,
        trace_stage_meta=trace,
        timings_ms=timings_ms,
        total_latency_ms=total_latency_ms,
        llm_latency_ms=llm_latency_ms,
    )


def _sse(data: Mapping[str, Any]) -> str:
    return f"data: {json.dumps(dict(data), ensure_ascii=False)}\n\n"


def _public_result_trace(result: SurfaceMainChainResult) -> dict[str, Any]:
    return {
        "selected_text": result.selected_text,
        "observation_codes": list(result.observation_codes),
        "packet_trace": dict(result.compiled.trace),
        "score_trace": dict(result.runtime.score_trace),
        "latency_ms": int(result.runtime.latency_ms or 0),
    }


def _surface_timings_ms(*, total_latency_ms: int, llm_latency_ms: int) -> dict[str, int]:
    return {
        "prompt_build_ms": 0,
        "llm_runtime_resolve_ms": 0,
        "stream_after_first_token_ms": 0,
        "response_intelligence_ms": 0,
        "sanitize_egress_ms": 0,
        "precommit_log_ms": 0,
        "surface_main_chain_ms": max(0, int(total_latency_ms or 0)),
        "llm_latency_ms": max(0, int(llm_latency_ms or 0)),
    }


def _schedule_surface_post_process(
    *,
    agent_input: Any,
    result: SurfaceMainChainResult,
    envelope: ReplyEnvelope,
    narrative: str,
    trace_stage_meta: Mapping[str, Any],
    timings_ms: Mapping[str, int],
    total_latency_ms: int,
    llm_latency_ms: int,
) -> None:
    ab_eval_mode = str(os.environ.get("AB_EVAL_MODE") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    } or bool(trace_stage_meta.get("ab_eval"))
    if ab_eval_mode or not envelope.persistable:
        return
    try:
        from host_brain.app.agent_runtime_shared import _async_post_process  # noqa: PLC0415

        llm_runtime = _safe_mapping(getattr(agent_input, "llm_runtime", None))
        trace = {
            **dict(trace_stage_meta or {}),
            "surface_main_chain_used": True,
            "chat_kernel_v2_used": False,
            "reply_source": "surface_main_chain",
            "reply_envelope": envelope.as_dict(),
            "surface_main_chain": _public_result_trace(result),
            "timings_ms": dict(timings_ms),
            "skip_heavy_persona_postprocess": True,
        }
        asyncio.create_task(
            _async_post_process(
                request_id=str(getattr(agent_input, "request_id", "") or ""),
                person_id=str(getattr(agent_input, "person_id", "") or ""),
                session_id=str(getattr(agent_input, "session_id", "") or ""),
                user_message=str(getattr(agent_input, "user_message", "") or ""),
                narrative=narrative,
                emotion_context=_surface_emotion_context(agent_input),
                bond_summary=_safe_mapping(getattr(agent_input, "bond_summary", None)),
                ts_ms=_coerce_int(getattr(agent_input, "ts_ms", 0), default=0) or int(time.time() * 1000),
                render_target_lang="zh",
                total_latency_ms=max(0, int(total_latency_ms or 0)),
                llm_latency_ms=max(0, int(llm_latency_ms or 0)),
                first_token_ms=max(0, int(llm_latency_ms or 0)) if narrative else None,
                provider_name=str(llm_runtime.get("provider_name") or ""),
                model_id=str(llm_runtime.get("model_id") or ""),
                buffer_stream=True,
                delegated_agents=[],
                retrieval_summary=_safe_mapping(getattr(agent_input, "retrieval_summary", None)),
                gov_decision={"decision": "APPROVE" if envelope.sendable else "NO_REPLY"},
                pii_ingress_stats={},
                pii_egress_stats={},
                llm_input_digest=json.dumps(_public_result_trace(result), ensure_ascii=False)[:500],
                retrieval_evidence_items=list(getattr(agent_input, "retrieval_evidence_items", None) or []),
                motive_state=None,
                reply_plan=_reply_plan(agent_input),
                trace_stage_meta=trace,
            )
        )
    except Exception:
        return


def _surface_emotion_context(agent_input: Any) -> dict[str, Any]:
    emotion = _safe_mapping(getattr(agent_input, "emotion_context", None)) or _safe_mapping(
        getattr(agent_input, "emotion_snapshot", None)
    )
    if "emotion_tag" not in emotion:
        emotion["emotion_tag"] = "NEUTRAL"
    return emotion


def _response_form(agent_input: Any) -> str:
    render_policy = _reply_plan(agent_input)
    runtime = _safe_mapping(getattr(agent_input, "llm_runtime", None))
    return normalize_response_form(
        str(
            getattr(agent_input, "response_form", "")
            or runtime.get("response_form")
            or render_policy.get("response_form")
            or "text"
        )
    )


def _reply_plan(agent_input: Any) -> dict[str, Any]:
    for raw in (getattr(agent_input, "reply_plan", None), getattr(agent_input, "render_policy", None)):
        if isinstance(raw, Mapping):
            return dict(raw)
    return {}


def _memory_packet(agent_input: Any) -> MemoryRecallPacket | None:
    for container in (
        _safe_mapping(getattr(agent_input, "trace_stage_meta", None)),
        _safe_mapping(getattr(agent_input, "retrieval_summary", None)),
    ):
        for key in _MEMORY_PACKET_KEYS:
            raw = container.get(key)
            if isinstance(raw, (MemoryRecallPacket, Mapping)):
                return memory_recall_packet_from_mapping(raw)
    return None


def _relationship_clues(agent_input: Any) -> tuple[str, ...]:
    bond = _safe_mapping(getattr(agent_input, "bond_summary", None))
    clues = _clean_list(
        [
            bond.get("relationship_distance"),
            bond.get("stage"),
            bond.get("stage_label"),
        ]
    )
    return tuple(clues)


def _task_facts(*, agent_input: Any, reply_plan: Mapping[str, Any]) -> tuple[str, ...]:
    facts = [
        f"当前任务：{_first_nonempty(reply_plan.get('reply_contract'), reply_plan.get('response_goal'), '')}",
        f"当前意图：{_first_nonempty(reply_plan.get('primary_topic'), reply_plan.get('interaction_move'), '')}",
    ]
    return tuple(_clean_list(facts))


def _necessary_facts(agent_input: Any) -> tuple[str, ...]:
    retrieval_summary = _safe_mapping(getattr(agent_input, "retrieval_summary", None))
    facts = []
    status = _first_nonempty(retrieval_summary.get("status"), "")
    if status:
        facts.append(f"召回状态：{status}")
    return tuple(_clean_list(facts))


def _surface_gen_params(llm_runtime: Mapping[str, Any]) -> dict[str, Any]:
    raw = llm_runtime.get("surface_gen_params") or llm_runtime.get("agent_gen_params") or {}
    if not isinstance(raw, Mapping):
        return {"temperature": 0.78, "top_p": 0.9, "max_tokens": 220}
    params = {key: value for key, value in dict(raw).items() if value is not None}
    params.setdefault("temperature", 0.78)
    params.setdefault("top_p", 0.9)
    params.setdefault("max_tokens", 220)
    return params


def _has_multimodal_payload(agent_input: Any) -> bool:
    if getattr(agent_input, "attachments", None):
        return True
    observations = _safe_mapping(getattr(agent_input, "multimodal_observations", None))
    return bool(observations)


def _is_non_surface_intent(agent_input: Any) -> bool:
    intent = _safe_mapping(getattr(agent_input, "intent_draft", None))
    route = str(intent.get("route") or "").strip().lower()
    target = str(intent.get("target_id") or intent.get("action") or "").strip().lower()
    return route in {"skill", "delegate", "plan"} or target.endswith("_agent")


def _surface_main_chain_default_enabled() -> bool:
    raw = str(os.environ.get("HOST_SURFACE_MAIN_CHAIN_ENABLED") or "").strip().lower()
    if raw in {"0", "false", "no", "off", "legacy", "chat_kernel_v2"}:
        return False
    return True


def _safe_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    return {}


def _clean_list(values: Sequence[Any] | None) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for value in list(values or []):
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        cleaned.append(text)
    return cleaned


def _coerce_sequence(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (str, bytes, bytearray)):
        text = str(value or "").strip()
        return [text] if text else []
    if isinstance(value, Sequence):
        return list(value)
    return [value]


def _first_nonempty(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _coerce_int(value: Any, *, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


__all__ = [
    "build_surface_main_chain_input_from_chat_input",
    "run_surface_main_chain_for_chat_input",
    "surface_main_chain_enabled",
    "surface_main_chain_sse_generator",
]
