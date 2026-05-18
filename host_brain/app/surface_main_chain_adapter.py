from __future__ import annotations

import asyncio
import json
import os
import re
import time
from collections.abc import AsyncGenerator, Mapping, Sequence
from typing import Any

from host_brain.app.context.soul_runtime import get_compiled_soul
from host_brain.app.reply_envelope import ReplyEnvelope
from host_brain.app.response_form_contract import normalize_response_form
from host_brain.persona.surface_affect_control import (
    SurfaceAffectControlPacket,
    affect_control_packet_from_mapping,
    build_surface_affect_control_packet,
)
from host_brain.persona.surface_main_chain import SurfaceMainChain, SurfaceMainChainInput, SurfaceMainChainResult
from host_brain.persona.surface_memory_recall import MemoryRecallPacket, memory_recall_packet_from_mapping


_MEMORY_PACKET_KEYS = (
    "surface_memory_recall_packet",
    "memory_recall_packet",
    "memory_packet",
)
_TRACE_RUNTIME_PAYLOAD_KEYS = (
    "conversation_history",
    "conversation_interpretation",
    "interaction_thread",
    "interaction_state",
    "topic_ledger",
    "tactic_ledger",
    "affect_state",
    "relationship_state",
    "drive_state",
    "reply_plan",
    "surface_affect_packet",
    "organ_influence_packet",
    "surface_influence_packet",
    "surface_organ_context_meta",
    "surface_organ_influence",
    "surface_memory_recall_packet",
)
_TEXT_RESPONSE_FORMS = {"", "text", "plain_text", "chat_text", "zh_text"}
_TECHNICAL_TASK_MODES = {
    "advice_continue",
    "complaint",
    "help_takeover",
    "next_step",
    "small_failure",
    "technical_help",
    "triage_first_step",
    "write_takeover",
}
_PORT_OCCUPIED_RE = re.compile(
    r"(?:端口|port)?\s*(?P<port>\d{2,5}).{0,24}(?:被占用|占用|被占|占了|in use|already in use|address already in use|bind)",
    re.IGNORECASE,
)
_PORT_OCCUPIED_REVERSED = re.compile(
    r"(?:被占用|占用|被占|占了|in use|already in use|address already in use|bind).{0,24}(?:端口|port)?\s*(?P<port>\d{2,5})",
    re.IGNORECASE,
)
_TASK_SPLIT_QUICK_RE = re.compile(r"(?:下一步|接下来|后面|任务).{0,12}(?:拆成|分成|列成).{0,8}(?:三|3)件", re.IGNORECASE)
_PYTHON_MISSING_MODULE_RE = re.compile(
    r"(?:找不到模块|没有名为).{0,12}[\"'“‘]?(?P<module>[A-Za-z0-9_.-]+)[\"'”’]?",
    re.IGNORECASE,
)
_PYTHON_NO_MODULE_RE = re.compile(r"No\s+module\s+named\s+[\"'“‘]?(?P<module>[A-Za-z0-9_.-]+)[\"'”’]?", re.IGNORECASE)
_ENV_VAR_RE = re.compile(r"\b(?P<var>[A-Z][A-Z0-9_]{2,})\b")
_TECHNICAL_TEXT_RE = re.compile(
    r"(报错|错误|异常|失败|缺包|导入|import|ModuleNotFoundError|No module named|端口|port|日志|启动|连接不上|连不上"
    r"|没响应|无响应|timeout|timed out|http://|https://|bolt://|neo4j|chroma|chromadb|OPENAI_API_KEY|环境变量)",
    re.IGNORECASE,
)
_TECHNICAL_QUESTION_RE = re.compile(r"(怎么|如何|怎么办|该|排查|查|先看|看什么|先试|试什么|\?|？)", re.IGNORECASE)
_PYTHON_MODULE_PACKAGE_MAP = {
    "cv2": "opencv-python",
    "pil": "Pillow",
    "sklearn": "scikit-learn",
    "yaml": "PyYAML",
}


def surface_main_chain_enabled(agent_input: Any, *, sub_agent_ctx: Any | None = None) -> bool:
    if not _surface_main_chain_default_enabled():
        return False
    return surface_main_chain_required(agent_input, sub_agent_ctx=sub_agent_ctx)


def surface_main_chain_required(agent_input: Any, *, sub_agent_ctx: Any | None = None) -> bool:
    _ = sub_agent_ctx
    if _has_multimodal_payload(agent_input):
        return False
    response_form = _response_form(agent_input)
    return response_form in _TEXT_RESPONSE_FORMS


def build_surface_main_chain_input_from_chat_input(
    agent_input: Any,
    *,
    trace_stage_meta: dict[str, Any] | None = None,
) -> SurfaceMainChainInput:
    soul = get_compiled_soul()
    runtime_payload = _prepared_surface_runtime_payload(trace_stage_meta)
    if not runtime_payload:
        runtime_payload = _build_surface_runtime_payload(agent_input, trace_stage_meta=trace_stage_meta, merge_trace=False)
    reply_plan = _resolve_surface_reply_plan(
        agent_input,
        trace_stage_meta=trace_stage_meta,
        runtime_payload=runtime_payload,
    )
    _merge_surface_trace(trace_stage_meta, runtime_payload)
    if isinstance(trace_stage_meta, dict) and reply_plan:
        trace_stage_meta["surface_selected_reply_plan"] = dict(reply_plan)
    task_control = _surface_task_control_from_reply_plan(agent_input=agent_input, reply_plan=reply_plan)
    affect_packet = _affect_packet(
        agent_input,
        task_control=task_control,
        trace_stage_meta=trace_stage_meta,
        runtime_payload=runtime_payload,
    )
    return SurfaceMainChainInput(
        soul_md=str(getattr(soul, "source_text", "") or getattr(soul, "runtime_full_text", "")).strip(),
        user_message=str(getattr(agent_input, "user_message", "") or "").strip(),
        case_id=str(getattr(agent_input, "request_id", "") or "").strip(),
        task_control=task_control,
        memory_packet=_memory_packet(
            agent_input,
            trace_stage_meta=trace_stage_meta,
            runtime_payload=runtime_payload,
        ),
        affect_packet=affect_packet,
        influence_packet=_influence_packet(
            agent_input,
            trace_stage_meta=trace_stage_meta,
            runtime_payload=runtime_payload,
        ),
        necessary_facts=_necessary_facts(agent_input, task_control=task_control),
        must_not_do=_surface_boundary_must_not_do(task_control=task_control),
        style_anchor=("app 层只传压缩控制包，不传原始历史。",),
    )


async def run_surface_main_chain_for_chat_input(
    agent_input: Any,
    *,
    trace_stage_meta: dict[str, Any] | None = None,
) -> SurfaceMainChainResult:
    llm_runtime = _safe_mapping(getattr(agent_input, "llm_runtime", None))
    request_id = str(getattr(agent_input, "request_id", "") or "").strip()
    surface_input = build_surface_main_chain_input_from_chat_input(
        agent_input,
        trace_stage_meta=trace_stage_meta,
    )
    quick_reply_payload = _surface_quick_reply_payload(
        agent_input,
        task_control=_safe_mapping(surface_input.task_control),
    )
    if quick_reply_payload and isinstance(trace_stage_meta, dict):
        trace_stage_meta["surface_quick_reply"] = {
            "applied": True,
            "kind": str(quick_reply_payload.get("_quick_reply_kind") or ""),
        }

    async def _generate(messages: list[dict[str, str]]) -> str:
        from host_brain.integrations.llm_helpers import acomplete_llm_with_pool  # noqa: PLC0415

        if quick_reply_payload:
            visible_payload = {
                key: value
                for key, value in quick_reply_payload.items()
                if not str(key).startswith("_")
            }
            return json.dumps(visible_payload, ensure_ascii=False)
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
            response_format={"type": "json_object"},
            base_url_override=str(llm_runtime.get("base_url_override") or "").strip() or None,
        )
        if isinstance(trace_stage_meta, dict):
            trace_stage_meta["surface_llm_pool"] = _llm_pool_result_trace(result)
        if bool(getattr(result, "pool_exhausted", False)):
            raise RuntimeError("SURFACE_LLM_POOL_EXHAUSTED")
        content = str(getattr(result, "content", "") or "").strip()
        if not content:
            raise RuntimeError("SURFACE_LLM_EMPTY_RESPONSE")
        return content

    return await SurfaceMainChain(_generate).run(surface_input)


async def surface_main_chain_sse_generator(
    agent_input: Any,
    *,
    trace_stage_meta: Mapping[str, Any] | None = None,
) -> AsyncGenerator[str, None]:
    started_ms = int(time.time() * 1000)
    trace = dict(trace_stage_meta or {})
    request_id = str(getattr(agent_input, "request_id", "") or "").strip()
    try:
        result = await run_surface_main_chain_for_chat_input(agent_input, trace_stage_meta=trace)
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
    quick_reply_used = bool(_safe_mapping(trace.get("surface_quick_reply")).get("applied"))
    llm_latency_ms = 0 if quick_reply_used else int(result.runtime.latency_ms or 0)
    timings_ms = _surface_timings_ms(
        total_latency_ms=total_latency_ms,
        llm_latency_ms=llm_latency_ms,
    )
    envelope = ReplyEnvelope.final_reply(
        text=narrative,
        response_form="text",
        source="surface_main_chain",
        audit={
            "request_id": request_id,
            "observation_codes": list(result.observation_codes),
            "natsume_emotion_tag": result.natsume_emotion_tag,
            "user_emotion_tag": result.user_emotion_tag,
            "vts_expression_id": result.vts_expression_id,
            "emotion_confidence": float(result.runtime.emotion_confidence or 0.0),
        },
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
                "model_id": None if quick_reply_used else _safe_mapping(getattr(agent_input, "llm_runtime", None)).get("model_id"),
                "provider_name": None if quick_reply_used else _safe_mapping(getattr(agent_input, "llm_runtime", None)).get("provider_name"),
                "response_form": "text",
                "timings_ms": timings_ms,
                "reply_envelope": envelope.as_dict(),
                "surface_main_chain": _public_result_trace(result),
                "trace_stage_meta": {
                    **trace,
                    "surface_main_chain_used": True,
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
        "natsume_emotion_tag": result.natsume_emotion_tag,
        "user_emotion_tag": result.user_emotion_tag,
        "vts_expression_id": result.vts_expression_id,
        "emotion_confidence": float(result.runtime.emotion_confidence or 0.0),
        "observation_codes": list(result.observation_codes),
        "packet_trace": dict(result.compiled.trace),
        "generation_trace": dict(result.runtime.generation_trace),
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


def _llm_pool_result_trace(result: Any) -> dict[str, Any]:
    attempts = []
    for item in list(getattr(result, "attempts", None) or []):
        attempts.append(
            {
                "model": str(getattr(item, "model", "") or ""),
                "provider": str(getattr(item, "provider", "") or ""),
                "status": str(getattr(item, "status", "") or ""),
                "reason_code": str(getattr(item, "reason_code", "") or ""),
                "latency_ms": _coerce_int(getattr(item, "latency_ms", 0), default=0),
                "detail": str(getattr(item, "detail", "") or ""),
            }
        )
    return {
        "pool_exhausted": bool(getattr(result, "pool_exhausted", False)),
        "final_provider": str(getattr(result, "final_provider", "") or ""),
        "final_model": str(getattr(result, "final_model", "") or ""),
        "cached": bool(getattr(result, "cached", False)),
        "total_latency_ms": _coerce_int(getattr(result, "total_latency_ms", 0), default=0),
        "attempts": attempts,
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
                emotion_context=_surface_emotion_context(agent_input, result=result),
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
                reply_plan=_selected_reply_plan(agent_input, trace_stage_meta),
                trace_stage_meta=trace,
            )
        )
    except Exception:
        return


def _surface_emotion_context(agent_input: Any, *, result: SurfaceMainChainResult | None = None) -> dict[str, Any]:
    emotion = _safe_mapping(getattr(agent_input, "emotion_context", None)) or _safe_mapping(
        getattr(agent_input, "emotion_snapshot", None)
    )
    if "emotion_tag" not in emotion:
        emotion["emotion_tag"] = "NEUTRAL"
    if result is not None:
        natsume_tag = str(result.natsume_emotion_tag or "NEUTRAL").strip().upper() or "NEUTRAL"
        user_tag = str(result.user_emotion_tag or "NEUTRAL").strip().upper() or "NEUTRAL"
        emotion["emotion_tag"] = natsume_tag
        emotion["emotion_source"] = "surface_generation_model"
        emotion["emotion_inference_source"] = "surface_generation_model"
        emotion["emotion_inference_confidence"] = float(result.runtime.emotion_confidence or 0.0)
        emotion["surface_user_emotion_tag"] = user_tag
        emotion["surface_natsume_emotion_tag"] = natsume_tag
        emotion["surface_vts_expression_id"] = str(result.vts_expression_id or "")
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


def _resolve_surface_reply_plan(
    agent_input: Any,
    *,
    trace_stage_meta: dict[str, Any] | None = None,
    runtime_payload: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    direct = _reply_plan(agent_input, trace_stage_meta)
    if direct:
        return direct
    payload = _safe_mapping(runtime_payload)
    if not payload:
        payload = _build_surface_runtime_payload(agent_input, trace_stage_meta=trace_stage_meta)
    reply_plan = _safe_mapping(payload.get("reply_plan"))
    if reply_plan:
        _set_agent_input_mapping(agent_input, "render_policy", reply_plan)
        return reply_plan
    return {}


def _selected_reply_plan(agent_input: Any, trace_stage_meta: Mapping[str, Any] | None = None) -> dict[str, Any]:
    selected = _safe_mapping(trace_stage_meta).get("surface_selected_reply_plan")
    if isinstance(selected, Mapping):
        return dict(selected)
    return _reply_plan(agent_input, trace_stage_meta)


def _reply_plan(agent_input: Any, trace_stage_meta: Mapping[str, Any] | None = None) -> dict[str, Any]:
    trace = _safe_mapping(trace_stage_meta)
    for raw in (
        getattr(agent_input, "surface_task_control", None),
        trace.get("surface_task_control"),
        trace.get("reply_plan"),
        getattr(agent_input, "reply_plan", None),
        getattr(agent_input, "render_policy", None),
    ):
        if isinstance(raw, Mapping) and raw:
            return dict(raw)
    return {}


def _build_surface_runtime_payload(
    agent_input: Any,
    *,
    trace_stage_meta: dict[str, Any] | None = None,
    merge_trace: bool = True,
) -> dict[str, Any]:
    try:
        from host_brain.app.surface_context_packet import build_surface_context_packet  # noqa: PLC0415
        direct_reply_plan = _reply_plan(agent_input, trace_stage_meta)

        runtime_payload = build_surface_context_packet(
            user_message=str(getattr(agent_input, "user_message", "") or ""),
            render_target_lang=str(getattr(agent_input, "render_target_lang", "") or "zh"),
            emotion_context=_safe_mapping(getattr(agent_input, "emotion_context", None)),
            emotion_snapshot=_safe_mapping(getattr(agent_input, "emotion_snapshot", None)),
            retrieval_summary=_safe_mapping(getattr(agent_input, "retrieval_summary", None)) or {"status": "SKIPPED"},
            retrieval_evidence_items=list(getattr(agent_input, "retrieval_evidence_items", None) or []),
            person_id=str(getattr(agent_input, "person_id", "") or ""),
            session_id=str(getattr(agent_input, "session_id", "") or ""),
            request_id=str(getattr(agent_input, "request_id", "") or ""),
            thread_id=getattr(agent_input, "thread_id", None),
            bond_summary=_safe_mapping(getattr(agent_input, "bond_summary", None)),
            intent_draft=_safe_mapping(getattr(agent_input, "intent_draft", None)),
            organ_context=_surface_organ_context(agent_input, trace_stage_meta=trace_stage_meta),
            surface_task_control=direct_reply_plan,
            eval_history_turns=list(getattr(agent_input, "eval_history_turns", None) or []),
        )
        if merge_trace:
            _merge_surface_trace(trace_stage_meta, runtime_payload)
        return runtime_payload
    except Exception as exc:
        if isinstance(trace_stage_meta, dict):
            trace_stage_meta["surface_context_prepare_failed"] = type(exc).__name__
    return {}


def _surface_organ_context(agent_input: Any, *, trace_stage_meta: Mapping[str, Any] | None = None) -> dict[str, Any]:
    trace = _safe_mapping(trace_stage_meta)
    agent_trace = _safe_mapping(getattr(agent_input, "trace_stage_meta", None))
    for container in (trace, agent_trace):
        for key in ("surface_organ_context", "organ_context", "tribe_context"):
            raw = container.get(key)
            if isinstance(raw, Mapping):
                return dict(raw)

    organ_route_meta = _safe_mapping(trace.get("organ_route_meta") or agent_trace.get("organ_route_meta"))
    motive_state = _safe_mapping(trace.get("motive_state") or agent_trace.get("motive_state"))
    if organ_route_meta and not motive_state:
        motive_state = {
            "withdraw_drive": organ_route_meta.get("motive_withdraw"),
            "care_drive": organ_route_meta.get("motive_care"),
            "continuity_drive": organ_route_meta.get("motive_continuity"),
        }

    vital_signs = _safe_mapping(
        trace.get("tribe_vital_signs")
        or agent_trace.get("tribe_vital_signs")
        or trace.get("vital_signs")
        or agent_trace.get("vital_signs")
    )
    immune_state = _safe_mapping(trace.get("immune_state") or agent_trace.get("immune_state"))
    proprioception_state = _safe_mapping(trace.get("proprioception_state") or agent_trace.get("proprioception_state"))
    if organ_route_meta:
        if not immune_state:
            immune_state = {
                "risk_level": organ_route_meta.get("immune_risk_level"),
                "last_decision": organ_route_meta.get("immune_last_decision"),
            }
        if not proprioception_state:
            proprioception_state = {
                "cognitive_load_index": organ_route_meta.get("cognitive_load_index"),
                "interrupt_frequency": organ_route_meta.get("interrupt_frequency"),
            }

    context = {
        "vital_signs": vital_signs,
        "voice_emotion": _safe_mapping(trace.get("voice_emotion") or agent_trace.get("voice_emotion")),
        "motive_state": motive_state,
        "immune_state": immune_state,
        "proprioception_state": proprioception_state,
        "social_brain_state": _safe_mapping(trace.get("social_brain_state") or agent_trace.get("social_brain_state")),
        "endocrine_gate": _safe_mapping(trace.get("endocrine_gate") or agent_trace.get("endocrine_gate")),
        "hedonic_state": _safe_mapping(trace.get("hedonic_state") or agent_trace.get("hedonic_state")),
        "somatic_state": _safe_mapping(trace.get("somatic_state") or agent_trace.get("somatic_state")),
        "interoception_state": _safe_mapping(trace.get("interoception_state") or agent_trace.get("interoception_state")),
        "circadian_state": _safe_mapping(trace.get("circadian_state") or agent_trace.get("circadian_state")),
        "default_mode_state": _safe_mapping(trace.get("default_mode_state") or agent_trace.get("default_mode_state")),
        "mirror_neuron_state": _safe_mapping(trace.get("mirror_neuron_state") or agent_trace.get("mirror_neuron_state")),
        "language_dual_system": _safe_mapping(trace.get("language_dual_system") or agent_trace.get("language_dual_system")),
        "motor_cortex_state": _safe_mapping(trace.get("motor_cortex_state") or agent_trace.get("motor_cortex_state")),
        "cerebellum_state": _safe_mapping(trace.get("cerebellum_state") or agent_trace.get("cerebellum_state")),
        "vestibular_state": _safe_mapping(trace.get("vestibular_state") or agent_trace.get("vestibular_state")),
        "peripheral_state": _safe_mapping(trace.get("peripheral_state") or agent_trace.get("peripheral_state")),
        "initiative_decision": _safe_mapping(trace.get("initiative_decision") or agent_trace.get("initiative_decision")),
    }
    return {
        key: value
        for key, value in context.items()
        if isinstance(value, Mapping) and value
    }


def _prepared_surface_runtime_payload(trace_stage_meta: Mapping[str, Any] | None = None) -> dict[str, Any]:
    trace = _safe_mapping(trace_stage_meta)
    if not bool(trace.get("surface_context_packet_prepared")):
        return {}
    return {
        key: dict(value)
        for key in _TRACE_RUNTIME_PAYLOAD_KEYS
        if isinstance((value := trace.get(key)), Mapping) and value
    }


def _merge_surface_trace(trace_stage_meta: dict[str, Any] | None, runtime_payload: Mapping[str, Any]) -> None:
    if not isinstance(trace_stage_meta, dict):
        return
    if runtime_payload:
        trace_stage_meta["surface_context_packet_prepared"] = True
    for key in _TRACE_RUNTIME_PAYLOAD_KEYS:
        value = runtime_payload.get(key)
        existing = trace_stage_meta.get(key)
        if isinstance(value, Mapping) and value and not (isinstance(existing, Mapping) and existing):
            trace_stage_meta[key] = dict(value)


def _surface_task_control_from_reply_plan(*, agent_input: Any, reply_plan: Mapping[str, Any]) -> dict[str, Any]:
    task_mode = _first_nonempty(
        reply_plan.get("task_mode"),
        reply_plan.get("primary_topic"),
        reply_plan.get("response_form_reason"),
        reply_plan.get("response_goal"),
        _safe_mapping(getattr(agent_input, "intent_draft", None)).get("route"),
        "daily_chat",
    )
    response_move = _first_nonempty(
        reply_plan.get("response_move"),
        reply_plan.get("interaction_move"),
        reply_plan.get("response_goal"),
        reply_plan.get("tactic_variant"),
        task_mode,
    )
    return {
        "response_move": response_move,
        "task_mode": task_mode,
        "information_budget": _first_nonempty(reply_plan.get("information_budget"), "short"),
        "question_budget": _coerce_int(
            _first_nonempty(reply_plan.get("question_budget"), reply_plan.get("max_questions")),
            default=0,
        ),
        "must_include": _clean_list(_coerce_sequence(reply_plan.get("must_include"))),
        "must_not_do": _clean_list(_coerce_sequence(reply_plan.get("must_not_do"))),
        "facts": _task_facts(reply_plan=reply_plan),
        "style_anchor": _clean_list(
            [
                "app_surface_main_chain",
                _first_nonempty(reply_plan.get("style_anchor"), ""),
            ]
        ),
    }


def _affect_packet(
    agent_input: Any,
    *,
    task_control: Mapping[str, Any],
    trace_stage_meta: Mapping[str, Any] | None = None,
    runtime_payload: Mapping[str, Any] | None = None,
) -> SurfaceAffectControlPacket:
    if _is_technical_task(task_control):
        return _derived_affect_packet(agent_input, task_control=task_control, runtime_payload=runtime_payload)
    for container in (
        _safe_mapping(trace_stage_meta),
        _safe_mapping(runtime_payload),
        _safe_mapping(getattr(agent_input, "trace_stage_meta", None)),
    ):
        for key in ("surface_affect_packet", "affect_control_packet", "affect_packet"):
            raw = container.get(key)
            if isinstance(raw, (SurfaceAffectControlPacket, Mapping)):
                return affect_control_packet_from_mapping(raw)
    return build_surface_affect_control_packet(
        emotion_state=_safe_mapping(getattr(agent_input, "emotion_context", None))
        or _safe_mapping(getattr(agent_input, "emotion_snapshot", None)),
        bond_summary=_safe_mapping(getattr(agent_input, "bond_summary", None)),
        relationship_clues=_relationship_clues(agent_input),
        task_mode=str(task_control.get("task_mode") or ""),
    )


def _is_technical_task(task_control: Mapping[str, Any]) -> bool:
    task_mode = str(task_control.get("task_mode") or "").strip().lower()
    response_move = str(task_control.get("response_move") or "").strip().lower()
    primary_topic = str(task_control.get("primary_topic") or "").strip().lower()
    interaction_move = str(task_control.get("interaction_move") or "").strip().lower()
    return (
        task_mode in _TECHNICAL_TASK_MODES
        or response_move in _TECHNICAL_TASK_MODES
        or primary_topic in _TECHNICAL_TASK_MODES
        or interaction_move in _TECHNICAL_TASK_MODES
    )


def _derived_affect_packet(
    agent_input: Any,
    *,
    task_control: Mapping[str, Any],
    runtime_payload: Mapping[str, Any] | None = None,
) -> SurfaceAffectControlPacket:
    payload = _safe_mapping(runtime_payload)
    return build_surface_affect_control_packet(
        emotion_state=_safe_mapping(payload.get("affect_state"))
        or _safe_mapping(getattr(agent_input, "emotion_context", None))
        or _safe_mapping(getattr(agent_input, "emotion_snapshot", None)),
        bond_summary={
            **_safe_mapping(getattr(agent_input, "bond_summary", None)),
            **_safe_mapping(payload.get("relationship_state")),
        },
        relationship_clues=_relationship_clues(agent_input),
        task_mode=str(task_control.get("task_mode") or ""),
    )


def _memory_packet(
    agent_input: Any,
    *,
    trace_stage_meta: Mapping[str, Any] | None = None,
    runtime_payload: Mapping[str, Any] | None = None,
) -> MemoryRecallPacket | None:
    for container in (
        _safe_mapping(trace_stage_meta),
        _safe_mapping(runtime_payload),
        _safe_mapping(getattr(agent_input, "trace_stage_meta", None)),
        _safe_mapping(getattr(agent_input, "retrieval_summary", None)),
    ):
        for key in _MEMORY_PACKET_KEYS:
            raw = container.get(key)
            if isinstance(raw, (MemoryRecallPacket, Mapping)):
                return memory_recall_packet_from_mapping(raw)
    return None


def _influence_packet(
    agent_input: Any,
    *,
    trace_stage_meta: Mapping[str, Any] | None = None,
    runtime_payload: Mapping[str, Any] | None = None,
) -> Mapping[str, Any] | None:
    for container in (
        _safe_mapping(trace_stage_meta),
        _safe_mapping(runtime_payload),
        _safe_mapping(getattr(agent_input, "trace_stage_meta", None)),
    ):
        raw = container.get("surface_influence_packet") or container.get("influence_packet")
        if isinstance(raw, Mapping):
            return dict(raw)
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


def _task_facts(*, reply_plan: Mapping[str, Any]) -> tuple[str, ...]:
    facts = [
        *_surface_input_boundary_facts(),
        f"当前任务：{_first_nonempty(reply_plan.get('reply_contract'), reply_plan.get('response_goal'), '')}",
        f"当前意图：{_first_nonempty(reply_plan.get('task_mode'), reply_plan.get('primary_topic'), reply_plan.get('interaction_move'), '')}",
        *_technical_task_facts(reply_plan),
    ]
    return tuple(_clean_list(facts))


def _necessary_facts(agent_input: Any, *, task_control: Mapping[str, Any] | None = None) -> tuple[str, ...]:
    retrieval_summary = _safe_mapping(getattr(agent_input, "retrieval_summary", None))
    facts = [*_surface_input_boundary_facts(), *_technical_task_facts(task_control or {})]
    status = _first_nonempty(retrieval_summary.get("status"), "")
    if status:
        facts.append(f"召回状态：{status}")
    return tuple(_clean_list(facts))


def _surface_input_boundary_facts() -> tuple[str, ...]:
    return (
        "当前输入渠道：中文文字私聊。",
        "当前没有音频、摄像头、屏幕画面或现实传感器事实。",
        "只能回应用户当前文字和压缩事实，不能把文字输入写成听见声音或看见画面。",
    )


def _surface_quick_reply_payload(
    agent_input: Any,
    *,
    task_control: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    task_split = _task_split_quick_reply_payload(agent_input, task_control=task_control)
    if task_split:
        return task_split
    return _technical_quick_reply_payload(agent_input, task_control=task_control)


def _technical_quick_reply_payload(
    agent_input: Any,
    *,
    task_control: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    user_message = str(getattr(agent_input, "user_message", "") or "").strip()
    if not (_is_technical_task(task_control or {}) or _looks_like_technical_advice_text(user_message)):
        return None
    missing_module = _extract_missing_python_module(user_message)
    if missing_module:
        package = _python_package_for_module(missing_module)
        return {
            "reply_text": (
                f"老哥，先确认是不是当前解释器没装包：python -m pip show {package}。"
                f"没有就跑 python -m pip install {package}；"
                '装了还报错，就用 python -c "import sys; print(sys.executable)" 看是不是跑错虚拟环境。'
            ),
            "natsume_emotion_tag": "THINKING",
            "user_emotion_tag": "NEUTRAL",
            "emotion_confidence": 0.0,
            "_quick_reply_kind": "python_missing_module_windows",
        }
    env_var = _extract_env_var_check(user_message)
    if env_var:
        return {
            "reply_text": (
                f"老哥，PowerShell 先跑 $env:{env_var} 看是不是空的；cmd 里用 echo %{env_var}%。"
                f"如果是空，就在当前终端设 $env:{env_var}=\"...\" 后重启服务；别把值贴出来。"
            ),
            "natsume_emotion_tag": "THINKING",
            "user_emotion_tag": "NEUTRAL",
            "emotion_confidence": 0.0,
            "_quick_reply_kind": "windows_env_var_check",
        }
    if _looks_like_neo4j_connectivity(user_message):
        port = _extract_first_port(user_message) or "17687"
        return {
            "reply_text": (
                f"老哥，先别急着改代码。PowerShell 跑 Test-NetConnection 127.0.0.1 -Port {port}。"
                "不通就看 Docker 里的 Neo4j 容器；通了再核对 BOND_NEO4J_URI 和账号。"
            ),
            "natsume_emotion_tag": "THINKING",
            "user_emotion_tag": "NEUTRAL",
            "emotion_confidence": 0.0,
            "_quick_reply_kind": "neo4j_connectivity_windows",
        }
    if _looks_like_chroma_connectivity(user_message):
        endpoint = _extract_http_endpoint(user_message) or "http://127.0.0.1:18000"
        return {
            "reply_text": (
                f"老哥，先确认 Chroma 活着：PowerShell 跑 Invoke-WebRequest {endpoint.rstrip('/')}/api/v2/heartbeat。"
                "连不上就看 Docker 容器有没有起来；能通再查 collection 名和维度配置。"
            ),
            "natsume_emotion_tag": "THINKING",
            "user_emotion_tag": "NEUTRAL",
            "emotion_confidence": 0.0,
            "_quick_reply_kind": "chroma_heartbeat_windows",
        }
    port = _extract_occupied_port(user_message)
    if not port:
        return None
    return {
        "reply_text": (
            f"老哥，先查占用 PID：netstat -ano | findstr :{port}。"
            "看到最后一列 PID 后，用 taskkill /PID <PID> /F 结束它；"
            "不想关进程就改服务端口再重启。"
        ),
        "natsume_emotion_tag": "NEUTRAL",
        "user_emotion_tag": "NEUTRAL",
        "emotion_confidence": 0.0,
        "_quick_reply_kind": "port_occupied_windows",
    }


def _task_split_quick_reply_payload(
    agent_input: Any,
    *,
    task_control: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    control = task_control or {}
    task_mode = str(control.get("task_mode") or "").strip().lower()
    response_move = str(control.get("response_move") or "").strip().lower()
    if task_mode not in {"task_help", "next_step", "daily_chat", "plain_answer"} and response_move not in {
        "task_help",
        "next_step",
    }:
        return None
    user_message = str(getattr(agent_input, "user_message", "") or "").strip()
    if not _TASK_SPLIT_QUICK_RE.search(user_message):
        return None
    return {
        "reply_text": "老哥，先拆三件：把卡点写清楚；只做一个最小可验证动作；跑完看结果，再决定下一步。",
        "natsume_emotion_tag": "NEUTRAL",
        "user_emotion_tag": "NEUTRAL",
        "emotion_confidence": 0.0,
        "_quick_reply_kind": "task_split_three_steps",
    }


def _extract_occupied_port(text: str) -> str:
    value = str(text or "").strip()
    if not value:
        return ""
    for pattern in (_PORT_OCCUPIED_RE, _PORT_OCCUPIED_REVERSED):
        match = pattern.search(value)
        if not match:
            continue
        port = str(match.group("port") or "").strip()
        if port and 0 < int(port) <= 65535:
            return port
    return ""


def _looks_like_technical_advice_text(text: str) -> bool:
    value = str(text or "").strip()
    if not value:
        return False
    return bool(_TECHNICAL_TEXT_RE.search(value) and _TECHNICAL_QUESTION_RE.search(value))


def _extract_missing_python_module(text: str) -> str:
    value = str(text or "")
    match = _PYTHON_NO_MODULE_RE.search(value) or _PYTHON_MISSING_MODULE_RE.search(value)
    if not match:
        return ""
    module = str(match.group("module") or "").strip().strip(".")
    if not module:
        return ""
    return module.split(".", 1)[0]


def _python_package_for_module(module: str) -> str:
    normalized = str(module or "").strip().lower()
    return _PYTHON_MODULE_PACKAGE_MAP.get(normalized, str(module or "").strip() or "包名")


def _extract_env_var_check(text: str) -> str:
    value = str(text or "").strip()
    if not value:
        return ""
    lowered = value.lower()
    if "环境变量" not in value and "env" not in lowered and "_key" not in lowered and "api_key" not in lowered:
        return ""
    if not re.search(r"(没生效|读不到|读取不到|为空|空的|查|看|确认|怎么|\?|？|not set|missing)", value, re.IGNORECASE):
        return ""
    match = _ENV_VAR_RE.search(value)
    return str(match.group("var") or "").strip() if match else ""


def _looks_like_neo4j_connectivity(text: str) -> bool:
    value = str(text or "")
    lowered = value.lower()
    return ("neo4j" in lowered or "bolt://" in lowered) and bool(
        re.search(r"(连接不上|连不上|没响应|无响应|timeout|timed out|先看|看什么|排查|查|怎么|\?|？)", value, re.IGNORECASE)
    )


def _looks_like_chroma_connectivity(text: str) -> bool:
    value = str(text or "")
    lowered = value.lower()
    return ("chroma" in lowered or "chromadb" in lowered or ":18000" in lowered) and bool(
        re.search(r"(心跳|heartbeat|连接不上|连不上|没响应|无响应|timeout|timed out|先看|看什么|排查|查|怎么|\?|？)", value, re.IGNORECASE)
    )


def _extract_first_port(text: str) -> str:
    for match in re.finditer(r":(?P<port>\d{2,5})(?:\b|/)", str(text or "")):
        port = str(match.group("port") or "").strip()
        if port and 0 < int(port) <= 65535:
            return port
    for match in re.finditer(r"\b(?P<port>\d{2,5})\b", str(text or "")):
        port = str(match.group("port") or "").strip()
        if port and 0 < int(port) <= 65535:
            return port
    return ""


def _extract_http_endpoint(text: str) -> str:
    match = re.search(r"https?://[^\s，。；;、]+", str(text or ""), re.IGNORECASE)
    if not match:
        return ""
    return match.group(0).rstrip("。；;，,")


def _surface_boundary_must_not_do(*, task_control: Mapping[str, Any] | None = None) -> tuple[str, ...]:
    return tuple(
        _clean_list(
            [
                "不要说“你这声音”“我听出来”“我看到你”或把文字输入写成音频/视觉感知。",
                "不要编造用户现实动作、地点、屏幕内容、身体状态或睡眠事实，除非 facts 明确给出。",
                *_technical_task_must_not_do(task_control or {}),
            ]
        )
    )


def _technical_task_facts(task_control: Mapping[str, Any] | None) -> tuple[str, ...]:
    if not _is_technical_task(task_control or {}):
        return ()
    return (
        "技术场景：先给一条能执行的排查步骤，命令和符号要准确。",
        "技术场景：最终回复至少包含查因和下一步处理，不要只给一条裸命令。",
        "技术场景：当前宿主环境按 Windows/PowerShell 优先给命令，除非用户明确指定 Linux 或 macOS。",
        "技术场景：有用性优先，角色口吻只做轻量包裹。",
    )


def _technical_task_must_not_do(task_control: Mapping[str, Any] | None) -> tuple[str, ...]:
    if not _is_technical_task(task_control or {}):
        return ()
    return (
        "技术回复不要用“干掉”“完事”等粗略说法替代清楚步骤。",
        "不要省略关键命令符号，例如端口筛选要保留冒号。",
        "不要默认给 lsof、grep 等 Linux/macOS 命令，除非用户明确指定该环境。",
        "不要只输出单条命令；命令后要说明下一步如何处理结果。",
    )


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


def _surface_main_chain_default_enabled() -> bool:
    raw = str(os.environ.get("HOST_SURFACE_MAIN_CHAIN_ENABLED") or "").strip().lower()
    if raw in {"0", "false", "no", "off", "legacy"}:
        return False
    return True


def _safe_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    return {}


def _set_agent_input_mapping(agent_input: Any, name: str, value: Mapping[str, Any]) -> None:
    try:
        setattr(agent_input, name, dict(value))
    except Exception:
        return


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
    "surface_main_chain_required",
    "surface_main_chain_sse_generator",
]
