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
from host_brain.codex_runtime.skill_ids import is_codex_runtime_skill
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
_AUDIO_INPUT_SOURCES = {"asr", "audio", "voice", "speech", "mic", "microphone", "voice_chat"}
_DETERMINISTIC_RESULT_AGENT_IDS = {
    "system_info",
    "system_resource_snapshot",
    "process_resource_top",
    "resource_pressure_analysis",
    "resource_manager_status",
    "resource_release",
    "process_list",
    "network_status",
    "open_app",
    "plan_execution",
    "recent_turn_direct",
    "task_progress_direct",
}
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
_ACCOUNT_LOGIN_TASK_RE = re.compile(
    r"(登录|账号|账户|积分|轻币|购买|买|epub|电子书|lightnovel|轻小说)",
    re.IGNORECASE,
)
_ACCOUNT_CREDENTIAL_REQUEST_RE = re.compile(
    r"((账号信息|账户信息|账号密码|账户密码|密码|验证码|cookie|cookies|credentials?|password)"
    r".{0,16}(丢|发|给|贴|提供|传|告诉|send|paste|provide)"
    r"|(丢|发|给|贴|提供|传|告诉|send|paste|provide).{0,16}"
    r"(账号信息|账户信息|账号密码|账户密码|密码|验证码|cookie|cookies|credentials?|password))",
    re.IGNORECASE,
)
_SURFACE_FASTPATH_CONTINUE_RE = re.compile(r"^(继续|接着|往下|继续说|接着说)[。.!！?？]*$", re.IGNORECASE)
_SURFACE_FASTPATH_PRESENCE_RE = re.compile(
    r"^(在吗|在嘛|在不在|你在吗|还在吗|喂|哈喽|hello|hi|嗯|嗯嗯|哦|好)[。.!！?？]*$",
    re.IGNORECASE,
)
_SURFACE_FASTPATH_AUDIO_CHECK_RE = re.compile(
    r"^(听得到吗|听得到不|能听到吗|能听见吗|听见吗|听得到嘛)[。.!！?？]*$",
    re.IGNORECASE,
)
_MAIN_CHAIN_META_CHAT_RE = re.compile(
    r"(主链|链路|耗时|延迟|fallback|Codex|模型|路由|surface|thalamus|后台任务)",
    re.IGNORECASE,
)
_BAD_TEASE_PREFIX_RE = re.compile(
    r"^\s*(?:哈？|啊？|嘛，?)?[^。！？!?]{0,40}(?:乱七八糟|又在琢磨|又在研究)[^。！？!?]*[。！？!?]\s*",
    re.IGNORECASE,
)
_PERCEPTION_EVIDENCE_REQUIRED_RE = re.compile(
    r"(这几本|这几张|这几份|这张|哪张|图里|图上|图片|照片|截图|画面|看起来|"
    r"image|photo|screenshot|cover)",
    re.IGNORECASE,
)
_PERCEPTION_PRODUCTION_TASK_RE = re.compile(
    r"(调用|推荐|抓|获取|找|搜索|采集|每日榜单|最新资源|轻小说|epub|封装|购买)",
    re.IGNORECASE,
)
_CURRENT_COVER_CLAIM_RE = re.compile(r"(这|那|哪).{0,8}封面|封面(上|里|中|有|是|看起来)", re.IGNORECASE)


def surface_main_chain_enabled(agent_input: Any, *, sub_agent_ctx: Any | None = None) -> bool:
    if not _surface_main_chain_default_enabled():
        return False
    return surface_main_chain_required(agent_input, sub_agent_ctx=sub_agent_ctx)


def surface_main_chain_required(agent_input: Any, *, sub_agent_ctx: Any | None = None) -> bool:
    if _sub_agent_result_should_bypass_surface(sub_agent_ctx):
        return False
    if _has_multimodal_payload(agent_input):
        return False
    response_form = _response_form(agent_input)
    return response_form in _TEXT_RESPONSE_FORMS


def _sub_agent_result_should_bypass_surface(sub_agent_ctx: Any | None) -> bool:
    if sub_agent_ctx is None:
        return False
    agent_id = str(getattr(sub_agent_ctx, "agent_id", "") or "").strip()
    if not agent_id:
        return False
    if is_codex_runtime_skill(agent_id) or agent_id in {"translate_agent", "intent_agent"}:
        return True
    return agent_id in _DETERMINISTIC_RESULT_AGENT_IDS or agent_id.startswith("skill:hub_skill_")


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
    task_control = _surface_task_control_from_reply_plan(
        agent_input=agent_input,
        reply_plan=reply_plan,
        trace_stage_meta=trace_stage_meta,
    )
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
        necessary_facts=_necessary_facts(agent_input, task_control=task_control, trace_stage_meta=trace_stage_meta),
        must_not_do=_surface_boundary_must_not_do(
            agent_input=agent_input,
            task_control=task_control,
            trace_stage_meta=trace_stage_meta,
        ),
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
    evidence_gate_text = _surface_perception_evidence_gate_text(agent_input)
    if evidence_gate_text:
        total_latency_ms = max(0, int(time.time() * 1000) - started_ms)
        envelope = ReplyEnvelope(
            kind="final_reply",
            text=evidence_gate_text,
            response_form="text",
            source="surface_evidence_gate",
            sendable=True,
            persistable=True,
            cacheable=False,
            degraded=False,
            reason_code="SURFACE_PERCEPTION_EVIDENCE_REQUIRED",
            audit={"request_id": request_id},
            raw_extra={"evidence_gate": True},
        )
        yield _sse({"phase": "RENDER", "chunk": evidence_gate_text})
        yield _sse({"phase": "SENTENCE", "sentence": evidence_gate_text, "sentence_index": 0})
        yield _sse(
            {
                "phase": "COMMIT",
                "done": True,
                "decision": "APPROVE",
                "reason_code": envelope.reason_code,
                "narrative": evidence_gate_text,
                "request_id": request_id,
                "reply_envelope": envelope.as_dict(),
                "audit_refs": {
                    "request_id": request_id,
                    "path": "chat_agent:surface_main_chain",
                    "latency_ms": total_latency_ms,
                    "first_token_ms": 0,
                    "llm_latency_ms": 0,
                    "model_id": "",
                    "provider_name": "rule",
                    "response_form": "text",
                    "timings_ms": _surface_timings_ms(
                        total_latency_ms=total_latency_ms,
                        llm_latency_ms=0,
                    ),
                    "reply_envelope": envelope.as_dict(),
                    "trace_stage_meta": {
                        **trace,
                        "surface_main_chain_used": True,
                        "reply_source": "surface_evidence_gate",
                        "surface_evidence_gate": {
                            "status": "OK",
                            "reason_code": "SURFACE_PERCEPTION_EVIDENCE_REQUIRED",
                            "has_multimodal_payload": False,
                        },
                    },
                },
            }
        )
        return
    fastpath_text = _surface_main_chain_fastpath_text(agent_input)
    if fastpath_text:
        total_latency_ms = max(0, int(time.time() * 1000) - started_ms)
        envelope = ReplyEnvelope(
            kind="final_reply",
            text=fastpath_text,
            response_form="text",
            source="surface_main_chain_fastpath",
            sendable=True,
            persistable=True,
            cacheable=True,
            degraded=False,
            reason_code="SURFACE_MAIN_CHAIN_FASTPATH",
            audit={"request_id": request_id},
            raw_extra={"fastpath": True, "reason_code": "LOW_INFO_RULE"},
        )
        yield _sse({"phase": "RENDER", "chunk": fastpath_text})
        yield _sse({"phase": "SENTENCE", "sentence": fastpath_text, "sentence_index": 0})
        yield _sse(
            {
                "phase": "COMMIT",
                "done": True,
                "decision": "APPROVE",
                "reason_code": envelope.reason_code,
                "narrative": fastpath_text,
                "request_id": request_id,
                "reply_envelope": envelope.as_dict(),
                "audit_refs": {
                    "request_id": request_id,
                    "path": "chat_agent:surface_main_chain",
                    "latency_ms": total_latency_ms,
                    "first_token_ms": 0,
                    "llm_latency_ms": 0,
                    "model_id": "",
                    "provider_name": "rule",
                    "response_form": "text",
                    "timings_ms": _surface_timings_ms(
                        total_latency_ms=total_latency_ms,
                        llm_latency_ms=0,
                    ),
                    "reply_envelope": envelope.as_dict(),
                    "trace_stage_meta": {
                        **trace,
                        "surface_main_chain_used": True,
                        "reply_source": "surface_main_chain_fastpath",
                        "surface_renderer": {
                            "status": "OK",
                            "invoked": False,
                            "affected_output": True,
                            "source": "surface_main_chain_fastpath",
                            "reason_code": "LOW_INFO_RULE",
                        },
                    },
                },
            }
        )
        return
    try:
        result = await run_surface_main_chain_for_chat_input(agent_input, trace_stage_meta=trace)
    except Exception as exc:
        total_latency_ms = max(0, int(time.time() * 1000) - started_ms)
        fallback_text = _surface_failure_fallback_text(agent_input, exc=exc)
        if fallback_text:
            envelope = ReplyEnvelope(
                kind="final_reply",
                text=fallback_text,
                response_form="text",
                source="surface_main_chain_fallback",
                sendable=True,
                persistable=False,
                cacheable=False,
                degraded=False,
                reason_code="SURFACE_MAIN_CHAIN_FALLBACK",
                audit={"request_id": request_id, "error": type(exc).__name__},
                raw_extra={
                    "fallback": True,
                    "degraded": True,
                    "reason_code": "SURFACE_MAIN_CHAIN_FAILED",
                },
            )
            yield _sse({"phase": "RENDER", "chunk": fallback_text})
            yield _sse({"phase": "SENTENCE", "sentence": fallback_text, "sentence_index": 0})
            yield _sse(
                {
                    "phase": "COMMIT",
                    "done": True,
                    "decision": "APPROVE",
                    "reason_code": envelope.reason_code,
                    "narrative": fallback_text,
                    "request_id": request_id,
                    "reply_envelope": envelope.as_dict(),
                    "audit_refs": {
                        "request_id": request_id,
                        "path": "chat_agent:surface_main_chain",
                        "latency_ms": total_latency_ms,
                        "reason_code": "SURFACE_MAIN_CHAIN_FAILED",
                        "timings_ms": _surface_timings_ms(
                            total_latency_ms=total_latency_ms,
                            llm_latency_ms=0,
                        ),
                        "trace_stage_meta": {
                            **trace,
                            "surface_main_chain_used": True,
                            "reply_source": "surface_main_chain_fallback",
                            "surface_renderer": {
                                "status": "DEGRADED",
                                "invoked": True,
                                "affected_output": True,
                                "source": "surface_main_chain_fallback",
                                "reason_code": "SURFACE_MAIN_CHAIN_FAILED",
                            },
                        },
                    },
                }
            )
            return
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
    narrative, account_guard_applied = _sanitize_account_login_reply(narrative, agent_input=agent_input)
    narrative = _sanitize_main_chain_meta_reply(narrative, agent_input=agent_input)
    if account_guard_applied:
        trace["surface_account_login_guard"] = {
            "applied": True,
            "reason_code": "ACCOUNT_CREDENTIAL_REQUEST_REWRITTEN",
        }
    total_latency_ms = max(0, int(time.time() * 1000) - started_ms)
    llm_latency_ms = int(result.runtime.latency_ms or 0)
    timings_ms = _surface_timings_ms(
        total_latency_ms=total_latency_ms,
        llm_latency_ms=llm_latency_ms,
    )
    model_audit = _surface_model_audit_fields(agent_input=agent_input, trace_stage_meta=trace)
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
                "model_id": model_audit["model_id"],
                "provider_name": model_audit["provider_name"],
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


def _surface_failure_fallback_text(agent_input: Any, *, exc: BaseException) -> str:
    if str(os.environ.get("HOST_SURFACE_MAIN_CHAIN_FAILURE_FALLBACK_ENABLED") or "true").strip().lower() in {
        "0",
        "false",
        "no",
        "off",
    }:
        return ""
    _ = exc
    message = str(getattr(agent_input, "user_message", "") or "").strip()
    lowered = message.lower()
    compact_continue = re.sub(r"\s+", "", message)
    if (
        re.fullmatch(r"(继续|接着|往下|继续说|接着说)(?:[，,、]?(?:短一句|短句|一句话))?[。.!！?？]*", compact_continue)
        or lowered in {"continue", "go on"}
    ):
        return "接着来。"
    if len(message) <= 6 or any(marker in message for marker in ("在吗", "嗯")):
        return "在，刚才卡了一下。"
    if any(marker in message for marker in ("风险", "继续观察", "迁到本机 Codex", "迁移到本机 Codex")):
        return "我会先盯连接稳定性和失败回退质量。前者决定耗时会不会突然拉长，后者决定卡住时会不会把一句正常问题糊成废话。"
    if any(marker in message for marker in ("耗时", "延迟", "慢", "哪一段", "哪段", "先怀疑")):
        return "我会先怀疑入口路由和 surface 生成这两段。一个决定会不会误进后台任务，另一个决定真正等模型多久。"
    if any(marker in message for marker in ("体感", "判断", "主链效果", "主链表现", "像正常聊天")):
        return "体感上问题不在台词腔本身，而在链路不稳。路由和 fallback 一旦抖，正常聊天就会被挡在外面。"
    return "我这边刚卡了一下，先接住你这句。"


def _sanitize_main_chain_meta_reply(text: str, *, agent_input: Any) -> str:
    message = str(getattr(agent_input, "user_message", "") or "").strip()
    if not _MAIN_CHAIN_META_CHAT_RE.search(message):
        return text
    cleaned = _BAD_TEASE_PREFIX_RE.sub("", str(text or "").strip(), count=1).strip()
    if not cleaned:
        cleaned = str(text or "").strip()
    if any(marker in message for marker in ("耗时", "延迟", "哪一段", "哪段", "先怀疑")) and not re.search(
        r"(路由|surface|入口|thalamus|fallback|模型|生成|检索|回退)",
        cleaned,
        re.IGNORECASE,
    ):
        return _surface_failure_fallback_text(agent_input, exc=RuntimeError("SURFACE_META_REPLY_TOO_GENERIC"))
    if any(marker in message for marker in ("体感", "判断", "主链效果", "主链表现")) and len(cleaned) < 18:
        return _surface_failure_fallback_text(agent_input, exc=RuntimeError("SURFACE_META_REPLY_TOO_THIN"))
    return cleaned


def _surface_main_chain_fastpath_text(agent_input: Any) -> str:
    if str(os.environ.get("HOST_SURFACE_MAIN_CHAIN_LOW_INFO_FASTPATH_ENABLED") or "true").strip().lower() in {
        "0",
        "false",
        "no",
        "off",
    }:
        return ""
    message = str(getattr(agent_input, "user_message", "") or "").strip()
    if not message or len(message) > 16:
        return ""
    compact = re.sub(r"\s+", "", message)
    if _SURFACE_FASTPATH_CONTINUE_RE.fullmatch(compact):
        return "接着来。"
    if _SURFACE_FASTPATH_AUDIO_CHECK_RE.fullmatch(compact):
        return "我这里只看到文字。"
    if _SURFACE_FASTPATH_PRESENCE_RE.fullmatch(compact):
        return "在。"
    return ""


def _surface_perception_evidence_gate_text(agent_input: Any) -> str:
    if _has_multimodal_payload(agent_input):
        return ""
    message = str(getattr(agent_input, "user_message", "") or "").strip()
    if not message:
        return ""
    if _PERCEPTION_PRODUCTION_TASK_RE.search(message) and not _CURRENT_COVER_CLAIM_RE.search(message):
        return ""
    if "《" in message and "》" in message and not re.search(r"(图|图片|照片|截图|封面|画面|看起来)", message):
        return ""
    if not (_PERCEPTION_EVIDENCE_REQUIRED_RE.search(message) or _CURRENT_COVER_CLAIM_RE.search(message)):
        return ""
    return "我这边没拿到图、附件或明确书名证据，不能装作看见了。你把图片或书名发过来，我再认真看。"


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


def _surface_model_audit_fields(*, agent_input: Any, trace_stage_meta: Mapping[str, Any]) -> dict[str, Any]:
    runtime = _safe_mapping(getattr(agent_input, "llm_runtime", None))
    pool_trace = _safe_mapping(trace_stage_meta.get("surface_llm_pool"))
    return {
        "model_id": str(pool_trace.get("final_model") or runtime.get("model_id") or "").strip(),
        "provider_name": str(pool_trace.get("final_provider") or runtime.get("provider_name") or "").strip(),
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
        model_audit = _surface_model_audit_fields(agent_input=agent_input, trace_stage_meta=trace_stage_meta)
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
                provider_name=str(model_audit.get("provider_name") or llm_runtime.get("provider_name") or ""),
                model_id=str(model_audit.get("model_id") or llm_runtime.get("model_id") or ""),
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
                attachments=list(getattr(agent_input, "attachments", None) or []),
                multimodal_observations=_safe_mapping(getattr(agent_input, "multimodal_observations", None)),
                attachment_summary=str(getattr(agent_input, "attachment_summary", "") or "").strip(),
                reply_channel=str(getattr(agent_input, "reply_channel", "") or "").strip() or None,
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


def build_surface_context_payload_from_adapter(
    *,
    user_message: str,
    request_id: str = "",
    session_id: str = "",
    person_id: str = "",
    render_target_lang: str = "zh",
    emotion_context: Mapping[str, Any] | None = None,
    emotion_snapshot: Mapping[str, Any] | None = None,
    retrieval_summary: Mapping[str, Any] | None = None,
    retrieval_evidence_items: list[dict[str, Any]] | None = None,
    bond_summary: Mapping[str, Any] | None = None,
    intent_draft: Mapping[str, Any] | None = None,
    organ_context: Mapping[str, Any] | None = None,
    surface_task_control: Mapping[str, Any] | None = None,
    eval_history_turns: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    from host_brain.app.surface_context_packet import build_surface_context_packet  # noqa: PLC0415

    return build_surface_context_packet(
        user_message=user_message,
        render_target_lang=render_target_lang,
        emotion_context=emotion_context,
        emotion_snapshot=emotion_snapshot,
        retrieval_summary=retrieval_summary or {"status": "SKIPPED"},
        retrieval_evidence_items=list(retrieval_evidence_items or []),
        person_id=person_id,
        session_id=session_id,
        request_id=request_id,
        bond_summary=bond_summary,
        intent_draft=intent_draft,
        organ_context=organ_context,
        surface_task_control=surface_task_control,
        eval_history_turns=list(eval_history_turns or []),
    )


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


def _surface_task_control_from_reply_plan(
    *,
    agent_input: Any,
    reply_plan: Mapping[str, Any],
    trace_stage_meta: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    memory_plan_reason = _surface_memory_plan_reason(trace_stage_meta)
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
    if memory_plan_reason == "anaphoric_selection_recall":
        task_mode = "memory_followup"
        response_move = "memory_followup"
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
        "facts": _task_facts(reply_plan=reply_plan, agent_input=agent_input, trace_stage_meta=trace_stage_meta),
        "style_anchor": _clean_list(
            [
                "app_surface_main_chain",
                "指代追问要承接已召回的最近经历对象。" if memory_plan_reason == "anaphoric_selection_recall" else "",
                _first_nonempty(reply_plan.get("style_anchor"), ""),
            ]
        ),
    }


def _surface_memory_plan_reason(trace_stage_meta: Mapping[str, Any] | None = None) -> str:
    packet = _safe_mapping(_safe_mapping(trace_stage_meta).get("surface_memory_recall_packet"))
    trace = _safe_mapping(packet.get("trace"))
    plan = _safe_mapping(trace.get("plan"))
    return str(plan.get("reason") or "").strip()


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


def _task_facts(
    *,
    reply_plan: Mapping[str, Any],
    agent_input: Any | None = None,
    trace_stage_meta: Mapping[str, Any] | None = None,
) -> tuple[str, ...]:
    facts = [
        *_surface_input_boundary_facts(agent_input=agent_input, trace_stage_meta=trace_stage_meta),
        f"当前任务：{_first_nonempty(reply_plan.get('reply_contract'), reply_plan.get('response_goal'), '')}",
        f"当前意图：{_first_nonempty(reply_plan.get('task_mode'), reply_plan.get('primary_topic'), reply_plan.get('interaction_move'), '')}",
        *_technical_task_facts(reply_plan),
    ]
    return tuple(_clean_list(facts))


def _necessary_facts(
    agent_input: Any,
    *,
    task_control: Mapping[str, Any] | None = None,
    trace_stage_meta: Mapping[str, Any] | None = None,
) -> tuple[str, ...]:
    retrieval_summary = _safe_mapping(getattr(agent_input, "retrieval_summary", None))
    facts = [
        *_surface_input_boundary_facts(agent_input=agent_input, trace_stage_meta=trace_stage_meta),
        *_technical_task_facts(task_control or {}),
        *_account_login_handoff_facts(agent_input, task_control=task_control),
    ]
    status = _first_nonempty(retrieval_summary.get("status"), "")
    if status:
        facts.append(f"召回状态：{status}")
    return tuple(_clean_list(facts))


def _surface_input_boundary_facts(
    *,
    agent_input: Any | None = None,
    trace_stage_meta: Mapping[str, Any] | None = None,
) -> tuple[str, ...]:
    if _input_has_audio_evidence(agent_input=agent_input, trace_stage_meta=trace_stage_meta):
        return (
            "当前输入渠道：语音/ASR。",
            "音频输入：有；可以确认收到了语音或语音转写。",
            "不要编造未提供的音色、语气、环境声音、摄像头画面、屏幕画面或现实传感器事实。",
        )
    return (
        "当前输入渠道：中文文字私聊。",
        "当前没有音频、摄像头、屏幕画面或现实传感器事实。",
        "只能回应用户当前文字和压缩事实，不能把文字输入写成听见声音或看见画面。",
    )


def _surface_boundary_must_not_do(
    *,
    agent_input: Any | None = None,
    task_control: Mapping[str, Any] | None = None,
    trace_stage_meta: Mapping[str, Any] | None = None,
) -> tuple[str, ...]:
    if _input_has_audio_evidence(agent_input=agent_input, trace_stage_meta=trace_stage_meta):
        sensory_boundary = "不要编造音色、语气、环境声音、摄像头画面、屏幕画面或现实传感器事实。"
    else:
        sensory_boundary = "不要说“你这声音”“我听出来”“我看到你”或把文字输入写成音频/视觉感知。"
    return tuple(
        _clean_list(
            [
                sensory_boundary,
                "不要编造用户现实动作、地点、屏幕内容、身体状态或睡眠事实，除非 facts 明确给出。",
                *_technical_task_must_not_do(task_control or {}),
                *_account_login_must_not_do(agent_input, task_control=task_control),
            ]
        )
    )


def _input_has_audio_evidence(
    *,
    agent_input: Any | None = None,
    trace_stage_meta: Mapping[str, Any] | None = None,
) -> bool:
    sources: list[Any] = []
    containers = (
        _safe_mapping(trace_stage_meta),
        _safe_mapping(getattr(agent_input, "trace_stage_meta", None)) if agent_input is not None else {},
        _safe_mapping(getattr(agent_input, "runtime_payload", None)) if agent_input is not None else {},
    )
    for container in containers:
        sources.extend(
            [
                container.get("input_source"),
                container.get("input_modality"),
                container.get("source_modality"),
                container.get("source"),
            ]
        )
    if agent_input is not None:
        sources.extend(
            [
                getattr(agent_input, "input_source", None),
                getattr(agent_input, "input_modality", None),
                getattr(agent_input, "source_modality", None),
                getattr(agent_input, "source", None),
            ]
        )
        observations = _safe_mapping(getattr(agent_input, "multimodal_observations", None))
        if observations.get("audio") or observations.get("asr") or observations.get("speech_text"):
            return True
        for attachment in _coerce_sequence(getattr(agent_input, "attachments", None)):
            attachment_map = _safe_mapping(attachment)
            mime_type = str(attachment_map.get("mime_type") or attachment_map.get("content_type") or "").lower()
            media_type = str(attachment_map.get("type") or attachment_map.get("kind") or "").lower()
            if mime_type.startswith("audio/") or media_type in {"audio", "voice", "speech"}:
                return True
    for raw in sources:
        normalized = str(raw or "").strip().lower().replace("-", "_")
        if not normalized:
            continue
        if normalized in _AUDIO_INPUT_SOURCES or "asr" in normalized:
            return True
    return False


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


def _account_login_sensitive(agent_input: Any | None, *, task_control: Mapping[str, Any] | None = None) -> bool:
    if agent_input is None:
        return False
    sub_agent_ctx = getattr(agent_input, "sub_agent_context", None)
    sub_agent_id = str(getattr(sub_agent_ctx, "agent_id", "") or "").strip()
    if sub_agent_id == "codex_epub_builder":
        return True
    intent = _safe_mapping(getattr(agent_input, "intent_draft", None))
    target_id = str(intent.get("target_id") or intent.get("action") or "").strip()
    if target_id == "codex_epub_builder":
        return True
    text = " ".join(
        str(part or "")
        for part in (
            getattr(agent_input, "user_message", ""),
            _safe_mapping(task_control).get("reply_contract"),
            _safe_mapping(task_control).get("response_goal"),
            _safe_mapping(task_control).get("primary_topic"),
        )
    )
    return bool(_ACCOUNT_LOGIN_TASK_RE.search(text)) and bool(
        re.search(r"(登录|账号|账户|购买|积分|轻币|epub|lightnovel|轻小说)", text, re.IGNORECASE)
    )


def _account_login_handoff_facts(
    agent_input: Any | None,
    *,
    task_control: Mapping[str, Any] | None = None,
) -> tuple[str, ...]:
    if not _account_login_sensitive(agent_input, task_control=task_control):
        return ()
    return (
        "账号类任务：需要用户在本地浏览器完成登录或授权；系统只复用会话状态，不接收明文账号、密码、验证码或 Cookie。",
        "涉及积分/购买：未明确确认书名、链接和最高消耗前，只能生成购买计划，不执行购买或解锁。",
    )


def _account_login_must_not_do(
    agent_input: Any | None,
    *,
    task_control: Mapping[str, Any] | None = None,
) -> tuple[str, ...]:
    if not _account_login_sensitive(agent_input, task_control=task_control):
        return ()
    return (
        "不要要求用户把账号、密码、验证码或 Cookie 发到聊天里。",
        "不要说“账号信息丢过来”“把密码发给我”“贴 Cookie 给我”这类话。",
        "不要暗示可以代填密码、绕过登录、绕过付费限制或未确认就消耗积分。",
    )


def _sanitize_account_login_reply(text: str, *, agent_input: Any | None = None) -> tuple[str, bool]:
    narrative = str(text or "").strip()
    if not narrative:
        return narrative, False
    if not _account_login_sensitive(agent_input):
        return narrative, False
    if not _ACCOUNT_CREDENTIAL_REQUEST_RE.search(narrative):
        return narrative, False
    return (
        "需要登录没错，但别把账号、密码、验证码或 Cookie 发给我。"
        "你在本地浏览器完成登录/授权，我只复用会话状态；涉及积分购买时，"
        "还要你明确确认书名、链接和最高积分后才会继续。",
        True,
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
    "build_surface_context_payload_from_adapter",
    "build_surface_main_chain_input_from_chat_input",
    "run_surface_main_chain_for_chat_input",
    "surface_main_chain_enabled",
    "surface_main_chain_required",
    "surface_main_chain_sse_generator",
]
