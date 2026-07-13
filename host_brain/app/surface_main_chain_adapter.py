"""Canonical runtime responsibility extracted from the former app hotspot."""

from __future__ import annotations

from typing import Any
from collections.abc import AsyncGenerator, Mapping, Sequence
from host_brain.app.reply_envelope import ReplyEnvelope
from host_brain.persona.surface_main_chain import (
    SurfaceMainChain,
    SurfaceMainChainInput,
    SurfaceMainChainResult,
    compile_surface_main_chain_input,
)
import asyncio
from host_brain.persona.surface_runtime import evaluate_surface_model_response_for_retry
import json
import os
import time

from host_brain.surface_runtime.delivery_audit import (
    _delegated_agents_from_sub_agent_context,
    _merge_delegated_agent_trace,
    _public_result_trace,
    _submit_surface_foreground_completed,
    _surface_model_audit_fields,
    _surface_timings_ms,
)
from host_brain.surface_runtime.fact_compiler import (
    _coerce_int,
    _safe_mapping,
    _sanitize_account_login_reply,
)
from host_brain.surface_runtime.input_compiler import (
    build_surface_main_chain_input_from_surface_reply_input,
)
from host_brain.surface_runtime.memory_grounding import (
    _apply_surface_sub_agent_result_guard,
    _sanitize_main_chain_meta_reply,
    _surface_memory_packet_for_output_guard,
    _surface_perception_evidence_gate_text,
)
from host_brain.surface_runtime.quality_guards import (
    _apply_surface_text_quality_guard,
    _memory_packet_payload_for_guard,
    _raw_memory_packet_payload_from_agent_input,
)
from host_brain.surface_runtime.subagent_context import (
    _surface_sub_agent_id,
    _surface_sub_agent_result_text,
)
from host_brain.surface_runtime.ports import (
    SurfaceRuntimePorts,
)
from host_brain.app.context.conversation_belief_tracker import (
    build_conversation_understanding_pack,
)
from host_brain.app.context.conversation_experience_store import (
    get_conversation_experience_store,
)
from host_brain.app.context.memory_context_orchestrator import build_memory_context_debug
from host_brain.composition.process_services import (
    get_conversation_history,
    get_ledger,
    get_life_command_service,
    get_life_snapshot_reader,
)
from host_brain.app.settings import get_persistence_settings
from host_brain.app.surface_context_packet import build_surface_context_packet
from host_brain.app.user_preference_store import build_user_preference_pack


def _build_surface_context_packet_with_life_ports(**kwargs):  # noqa: ANN003
    return build_surface_context_packet(
        life_snapshot_reader=get_life_snapshot_reader(),
        **kwargs,
    )


def _compose_surface_runtime_ports() -> SurfaceRuntimePorts:
    """Compose one immutable Surface dependency set for a request."""

    return SurfaceRuntimePorts(
        get_ledger=get_ledger,
        get_life_command_service=get_life_command_service,
        get_life_snapshot_reader=get_life_snapshot_reader,
        get_persistence_settings=get_persistence_settings,
        build_surface_context_packet=_build_surface_context_packet_with_life_ports,
        build_user_preference_pack=build_user_preference_pack,
        get_conversation_history=get_conversation_history,
        get_conversation_experience_store=get_conversation_experience_store,
        build_conversation_understanding_pack=build_conversation_understanding_pack,
        build_memory_context_debug=build_memory_context_debug,
    )


def _surface_main_chain_response_format() -> dict[str, str] | None:
    mode = str(os.environ.get("HOST_SURFACE_MAIN_CHAIN_OUTPUT_CONTRACT") or "text").strip().lower()
    if mode in {"json", "json_object", "structured"}:
        return {"type": "json_object"}
    return None


async def run_surface_main_chain_for_surface_reply_input(
    agent_input: Any,
    *,
    ports: SurfaceRuntimePorts,
    trace_stage_meta: dict[str, Any] | None = None,
) -> SurfaceMainChainResult:
    request_id = str(getattr(agent_input, "request_id", "") or "").strip()
    surface_input = build_surface_main_chain_input_from_surface_reply_input(
        agent_input,
        ports=ports,
        trace_stage_meta=trace_stage_meta,
    )
    compiled_request = compile_surface_main_chain_input(surface_input).request
    if isinstance(trace_stage_meta, dict):
        guard_packet = _raw_memory_packet_payload_from_agent_input(agent_input) or _surface_memory_packet_for_output_guard(
            agent_input=agent_input,
            trace_stage_meta=trace_stage_meta,
        ) or _memory_packet_payload_for_guard(surface_input.memory_packet)
        if guard_packet and not _safe_mapping(trace_stage_meta.get("surface_memory_recall_packet")):
            trace_stage_meta["surface_memory_recall_packet"] = guard_packet

    async def _generate(messages: list[dict[str, str]]) -> str:
        from host_brain.composition.process_services import get_model_attempt_service  # noqa: PLC0415
        from host_brain.model_runtime.contracts import (  # noqa: PLC0415
            ModelAttemptRequest,
            PromptRef,
            RetryDirective,
        )
        from host_brain.model_runtime.live_prompts import (  # noqa: PLC0415
            SURFACE_MAIN_JSON_PROMPT_ID,
            SURFACE_MAIN_TEXT_PROMPT_ID,
        )
        from host_brain.model_runtime.trace import model_attempt_result_trace  # noqa: PLC0415

        prompt_id = (
            SURFACE_MAIN_JSON_PROMPT_ID
            if _surface_main_chain_response_format() is not None
            else SURFACE_MAIN_TEXT_PROMPT_ID
        )

        def _retry_evaluator(response: Any, attempt: Any) -> Any:
            evaluation = evaluate_surface_model_response_for_retry(
                compiled_request,
                raw_output=str(response.content or ""),
                current_messages=attempt.prompt.messages,
            )
            if not evaluation.retry:
                return RetryDirective.accept()
            return RetryDirective(
                retry=True,
                reason=evaluation.reason,
                rendered_prompt=attempt.prompt.with_messages(evaluation.messages),
                reuse_target=True,
                metadata={"violation_codes": list(evaluation.violation_codes)},
            )

        generate_started = time.perf_counter()
        result = await get_model_attempt_service().complete(
            ModelAttemptRequest(
                request_id=request_id or "surface-main-chain",
                scene_type="CHAT_SURFACE",
                cognitive_tier="A",
                prompt=PromptRef(prompt_id, {"messages": messages}),
                deadline_ms=max(
                    1_000,
                    min(
                        120_000,
                        _coerce_int(
                            os.environ.get("HOST_SURFACE_MODEL_ATTEMPT_DEADLINE_MS"),
                            default=60_000,
                        ),
                    ),
                ),
                foreground=True,
                retry_evaluator=_retry_evaluator,
                metadata={"chain": "surface_main"},
            )
        )
        if isinstance(trace_stage_meta, dict):
            attempt_trace = model_attempt_result_trace(result)
            attempt_trace["generate_elapsed_ms"] = int(
                (time.perf_counter() - generate_started) * 1000
            )
            attempt_trace["message_count"] = len(messages)
            trace_stage_meta["surface_model_attempt"] = attempt_trace
        if not result.success:
            raise RuntimeError(
                f"{result.error_code or 'SURFACE_MODEL_ATTEMPT_FAILED'}: "
                f"{result.error_message or 'model attempt failed'}"
            )
        content = str(result.content or "").strip()
        if not content:
            raise RuntimeError("SURFACE_LLM_EMPTY_RESPONSE")
        return content

    return await SurfaceMainChain(
        _generate,
        model_retries_owned_externally=True,
    ).run(surface_input)


async def surface_main_chain_sse_generator(
    agent_input: Any,
    *,
    trace_stage_meta: Mapping[str, Any] | None = None,
) -> AsyncGenerator[str, None]:
    ports = _compose_surface_runtime_ports()
    started_ms = int(time.time() * 1000)
    trace = {
        **_safe_mapping(getattr(agent_input, "trace_stage_meta", None)),
        **dict(trace_stage_meta or {}),
    }
    delegated_agents = _delegated_agents_from_sub_agent_context(agent_input)
    _merge_delegated_agent_trace(trace, delegated_agents)
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
                    "path": "surface_reply:surface_main_chain",
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
    try:
        result = await run_surface_main_chain_for_surface_reply_input(
            agent_input,
            ports=ports,
            trace_stage_meta=trace,
        )
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
                        "path": "surface_reply:surface_main_chain",
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
                    "path": "surface_reply:surface_main_chain",
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

    raw_narrative = str(result.selected_text or "").strip()
    narrative = raw_narrative
    narrative, account_guard_applied = _sanitize_account_login_reply(narrative, agent_input=agent_input)
    narrative = _sanitize_main_chain_meta_reply(narrative, agent_input=agent_input)
    narrative, quality_guard = _apply_surface_text_quality_guard(
        narrative,
        agent_input=agent_input,
    )
    try:
        from host_brain.app.codex_spark_reply_judge import judge_surface_reply_with_codex_spark  # noqa: PLC0415

        narrative, spark_judge = await judge_surface_reply_with_codex_spark(
            agent_input=agent_input,
            candidate_text=narrative,
            trace_stage_meta=trace,
        )
        if spark_judge:
            trace["codex_spark_reply_judge"] = spark_judge
    except Exception as spark_exc:
        trace["codex_spark_reply_judge"] = {
            "status": "DEGRADED",
            "error": type(spark_exc).__name__,
        }
    narrative, sub_agent_result_guard = _apply_surface_sub_agent_result_guard(
        narrative,
        agent_input=agent_input,
    )
    if account_guard_applied:
        trace["surface_account_login_guard"] = {
            "applied": True,
            "reason_code": "ACCOUNT_CREDENTIAL_REQUEST_REWRITTEN",
        }
    if quality_guard.get("applied"):
        trace["surface_text_quality_guard"] = quality_guard
    if sub_agent_result_guard.get("applied"):
        trace["surface_sub_agent_result_guard"] = sub_agent_result_guard
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
    if envelope.persistable:
        try:
            foreground_receipt = await asyncio.to_thread(
                _submit_surface_foreground_completed,
                life_command_service=ports.get_life_command_service(),
                life_snapshot_reader=ports.get_life_snapshot_reader(),
                agent_input=agent_input,
                result=result,
                envelope=envelope,
                narrative=narrative,
                trace_stage_meta=trace,
                timings_ms=timings_ms,
                total_latency_ms=total_latency_ms,
                llm_latency_ms=llm_latency_ms,
            )
        except Exception as exc:
            trace["life_causality"] = {
                "status": "FAILED",
                "reason_code": "LIFE_FOREGROUND_COMMIT_FAILED",
                "error_type": type(exc).__name__,
            }
            failure_envelope = ReplyEnvelope.no_reply(
                reason_code="LIFE_FOREGROUND_COMMIT_FAILED",
                source="surface_main_chain",
                audit={
                    "request_id": request_id,
                    "error_type": type(exc).__name__,
                    "durability": "fail_closed_before_render",
                },
            )
            yield _sse(
                {
                    "phase": "COMMIT",
                    "done": True,
                    "decision": "NO_REPLY",
                    "reason_code": failure_envelope.reason_code,
                    "narrative": "",
                    "request_id": request_id,
                    "reply_envelope": failure_envelope.as_dict(),
                    "audit_refs": {
                        "request_id": request_id,
                        "path": "surface_reply:surface_main_chain",
                        "durability": "fail_closed_before_render",
                        "trace_stage_meta": {
                            **trace,
                            "surface_main_chain_used": True,
                            "reply_source": "surface_main_chain",
                        },
                    },
                }
            )
            return
        trace["life_causality"] = {
            "status": str(foreground_receipt.get("status") or "UNKNOWN"),
            "reason_code": foreground_receipt.get("reason_code"),
            "command_id": foreground_receipt.get("command_id"),
            "event_id": foreground_receipt.get("event_id"),
            "state_version": foreground_receipt.get("state_version"),
            "outbox_count": foreground_receipt.get("outbox_count"),
            "replayed": bool(foreground_receipt.get("replayed")),
        }
    else:
        trace["life_causality"] = {
            "status": "SKIPPED",
            "reason_code": "REPLY_NOT_PERSISTABLE",
        }
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
                "path": "surface_reply:surface_main_chain",
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


def _sse(data: Mapping[str, Any]) -> str:
    return f"data: {json.dumps(dict(data), ensure_ascii=False)}\n\n"


def _surface_failure_fallback_text(agent_input: Any, *, exc: BaseException) -> str:
    _ = exc
    try:
        from host_brain.app.surface_reply_preprocessor import _build_deterministic_sub_agent_reply  # noqa: PLC0415

        text = _build_deterministic_sub_agent_reply(
            getattr(agent_input, "sub_agent_context", None),
            render_target_lang=str(getattr(agent_input, "render_target_lang", "zh") or "zh"),
            user_message=str(getattr(agent_input, "user_message", "") or ""),
        )
        if text:
            return str(text).strip()
    except Exception:
        pass
    result_text = _surface_sub_agent_result_text(agent_input)
    if _surface_sub_agent_id(agent_input) and result_text:
        return result_text[:4000].strip()
    return ""
