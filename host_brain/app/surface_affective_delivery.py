"""Deterministic and deferred affective delivery paths."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncGenerator, Mapping
from typing import Any

from host_brain.app.sentence_splitter import SentenceSplitter
from host_brain.app.surface_affective_native_stream import (
    _expression_delivery_sse,
    _sanitize_native_stream_sentence,
)
from host_brain.app.surface_main_chain_support import _sse
from host_brain.input_runtime.reply_envelope import ReplyEnvelope
from host_brain.surface_runtime.affective_streaming import gate_stream_sentence
from host_brain.surface_runtime.delivery_audit import (
    _public_result_trace,
    _submit_surface_affective_turn_planned,
    _submit_surface_foreground_completed,
    _submit_surface_sentence_delivery_intent,
    _surface_model_audit_fields,
    _surface_timings_ms,
)
from host_brain.surface_runtime.fact_compiler import _safe_mapping
from host_brain.surface_runtime.ports import SurfaceRuntimePorts


def _gated_deterministic_affective_sentences(
    text: str,
    *,
    agent_input: Any,
    trace: dict[str, Any],
) -> tuple[str, ...]:
    """Split a rule/fallback reply before it enters the visible protocol.

    Evidence requirements and deterministic sub-agent fallbacks do not need a
    model call, but they are still a reply from Natsume.  They therefore use
    the same sentence gate as native streaming instead of bypassing the
    frozen affective plan with one un-audited text blob.
    """

    splitter = SentenceSplitter()
    candidates = list(splitter.feed(str(text or "")))
    remainder = splitter.flush()
    if remainder:
        candidates.append(remainder)
    if not candidates and str(text or "").strip():
        candidates.append(str(text or "").strip())

    accepted: list[str] = []
    for raw_sentence in candidates:
        candidate = _sanitize_native_stream_sentence(
            raw_sentence,
            agent_input=agent_input,
            trace=trace,
        )
        gate = gate_stream_sentence(candidate)
        if not gate.accepted:
            raise RuntimeError(gate.reason_code)
        accepted.append(gate.sentence)
    if not accepted:
        raise RuntimeError("SURFACE_STREAM_EMPTY_SENTENCE")
    return tuple(accepted)


async def _canonical_affective_deterministic_delivery(
    agent_input: Any,
    *,
    ports: SurfaceRuntimePorts,
    trace: dict[str, Any],
    plan: Mapping[str, Any],
    text: str,
    started_ms: int,
    reply_source: str,
    reason_code: str,
    degraded: bool,
    audit: Mapping[str, Any] | None = None,
    raw_extra: Mapping[str, Any] | None = None,
) -> AsyncGenerator[str, None]:
    """Deliver a non-model reply through the canonical affective protocol.

    This deliberately covers only direct Host delivery.  A governed worker is
    still speculative until Task Acceptance and keeps its buffered route;
    letting this helper write Life directly from that worker would recreate a
    second acceptance authority merely to make a fallback look responsive.
    """

    request_id = str(getattr(agent_input, "request_id", "") or "").strip()
    plan_id = str(plan.get("plan_id") or "").strip()
    expression = _safe_mapping(plan.get("expression_plan"))
    synchronization_id = str(expression.get("synchronization_id") or "").strip()
    try:
        sentences = _gated_deterministic_affective_sentences(
            text,
            agent_input=agent_input,
            trace=trace,
        )
    except Exception as exc:
        trace["affective_deterministic_delivery"] = {
            "status": "REJECTED",
            "reason_code": str(exc).split(":", 1)[0][:160],
            "reply_source": reply_source,
        }
        yield _sse(
            {
                "phase": "COMMIT",
                "done": True,
                "decision": "NO_REPLY",
                "reason_code": str(exc).split(":", 1)[0][:160]
                or "SURFACE_STREAM_EMPTY_SENTENCE",
                "narrative": "",
                "request_id": request_id,
                "audit_refs": {
                    "request_id": request_id,
                    "path": "surface_reply:canonical_affective_deterministic",
                    "trace_stage_meta": dict(trace),
                },
            }
        )
        return

    narrative = "".join(sentences).strip()
    if not narrative:
        yield _sse(
            {
                "phase": "COMMIT",
                "done": True,
                "decision": "NO_REPLY",
                "reason_code": "SURFACE_STREAM_EMPTY_SENTENCE",
                "narrative": "",
                "request_id": request_id,
                "audit_refs": {
                    "request_id": request_id,
                    "path": "surface_reply:canonical_affective_deterministic",
                    "trace_stage_meta": dict(trace),
                },
            }
        )
        return

    try:
        planned_receipt = await asyncio.to_thread(
            _submit_surface_affective_turn_planned,
            life_command_service=ports.get_life_command_service(),
            life_snapshot_reader=ports.get_life_snapshot_reader(),
            agent_input=agent_input,
            trace_stage_meta=trace,
            source="surface_affective_deterministic_delivery",
        )
    except Exception as exc:
        trace["life_causality"] = {
            "status": "FAILED",
            "reason_code": "AFFECTIVE_TURN_PLAN_COMMIT_FAILED",
            "error_type": type(exc).__name__,
        }
        yield _sse(
            {
                "phase": "COMMIT",
                "done": True,
                "decision": "NO_REPLY",
                "reason_code": "AFFECTIVE_TURN_PLAN_COMMIT_FAILED",
                "narrative": "",
                "request_id": request_id,
                "audit_refs": {
                    "request_id": request_id,
                    "path": "surface_reply:canonical_affective_deterministic",
                    "durability": "fail_closed_before_stream",
                    "trace_stage_meta": dict(trace),
                },
            }
        )
        return

    trace["affective_turn_plan_receipt"] = {
        "command_id": planned_receipt.get("command_id"),
        "event_id": planned_receipt.get("event_id"),
        "state_version": planned_receipt.get("state_version"),
        "replayed": bool(planned_receipt.get("replayed")),
    }
    sentence_receipts: list[dict[str, Any]] = []
    try:
        for sentence_index, sentence in enumerate(sentences):
            sentence_receipt = await asyncio.to_thread(
                _submit_surface_sentence_delivery_intent,
                life_command_service=ports.get_life_command_service(),
                life_snapshot_reader=ports.get_life_snapshot_reader(),
                agent_input=agent_input,
                trace_stage_meta=trace,
                sentence=sentence,
                sentence_index=sentence_index,
                source="surface_affective_deterministic_delivery",
            )
            sentence_receipts.append(
                {
                    "command_id": sentence_receipt.get("command_id"),
                    "event_id": sentence_receipt.get("event_id"),
                    "sentence_index": sentence_index,
                    "replayed": bool(sentence_receipt.get("replayed")),
                    "expression_delivery": _safe_mapping(
                        sentence_receipt.get("expression_delivery")
                    ),
                }
            )
    except Exception as exc:
        trace["affective_deterministic_delivery"] = {
            "status": "FAILED",
            "reason_code": "SURFACE_SENTENCE_DELIVERY_INTENT_FAILED",
            "error_type": type(exc).__name__,
            "reply_source": reply_source,
            "sent_sentence_count": len(sentence_receipts),
        }
        yield _sse(
            {
                "phase": "COMMIT",
                "done": True,
                "decision": "NO_REPLY",
                "reason_code": "SURFACE_SENTENCE_DELIVERY_INTENT_FAILED",
                "narrative": "",
                "request_id": request_id,
                "audit_refs": {
                    "request_id": request_id,
                    "path": "surface_reply:canonical_affective_deterministic",
                    "durability": "fail_closed_before_stream",
                    "trace_stage_meta": dict(trace),
                },
            }
        )
        return

    stream_summary = {
        "mode": "deterministic_affective_delivery",
        "streaming": False,
        "sentence_delivery_staged": True,
        "sentence_count": len(sentences),
        "plan_id": plan_id,
        "synchronization_id": synchronization_id,
        "self_affect_revision": plan.get("self_affect_revision"),
        "first_token_ms": 0,
        "provider_usage_status": "NO_MODEL_RULE_FALLBACK",
        "reply_source": reply_source,
    }
    trace["affective_deterministic_delivery"] = {
        "status": "SENTENCES_STAGED",
        "reply_source": reply_source,
        "plan_id": plan_id,
        "synchronization_id": synchronization_id,
        "sentence_receipts": sentence_receipts,
    }
    envelope = ReplyEnvelope(
        kind="final_reply",
        text=narrative,
        response_form="text",
        source=reply_source,
        sendable=True,
        persistable=True,
        cacheable=False,
        # ``ReplyEnvelope.degraded`` means an unsendable degraded failure, not
        # a safe deterministic wording fallback.  Keep this accepted response
        # deliverable and record renderer degradation separately below.
        degraded=False,
        reason_code=str(reason_code or "").strip() or None,
        audit={"request_id": request_id, **dict(audit or {})},
        raw_extra=dict(raw_extra or {}),
    )

    for sentence_index, sentence in enumerate(sentences):
        receipt = _safe_mapping(sentence_receipts[sentence_index].get("expression_delivery"))
        delivery_event = _expression_delivery_sse(
            {"expression_delivery": receipt},
            sentence_index=sentence_index,
        )
        if delivery_event is not None:
            yield _sse(delivery_event)
        yield _sse({"phase": "RENDER", "chunk": sentence})
        yield _sse(
            {
                "phase": "SENTENCE",
                "sentence": sentence,
                "sentence_index": sentence_index,
                "affective_turn_plan_id": plan_id,
                "synchronization_id": synchronization_id,
            }
        )

    total_latency_ms = max(0, int(time.time() * 1000) - started_ms)
    timings_ms = _surface_timings_ms(
        total_latency_ms=total_latency_ms,
        llm_latency_ms=0,
    )
    try:
        foreground_receipt = await asyncio.to_thread(
            _submit_surface_foreground_completed,
            life_command_service=ports.get_life_command_service(),
            life_snapshot_reader=ports.get_life_snapshot_reader(),
            relationship_lifecycle_service=ports.get_relationship_lifecycle_service(),
            agent_input=agent_input,
            result=None,
            envelope=envelope,
            narrative=narrative,
            trace_stage_meta=trace,
            timings_ms=timings_ms,
            total_latency_ms=total_latency_ms,
            llm_latency_ms=0,
            stream_summary=stream_summary,
            first_token_ms=0,
            buffer_stream=False,
        )
    except Exception as exc:
        trace["life_causality"] = {
            "status": "FAILED",
            "reason_code": "LIFE_FOREGROUND_COMMIT_FAILED_AFTER_STREAM",
            "error_type": type(exc).__name__,
            "sent_sentence_count": len(sentences),
        }
        yield _sse(
            {
                "phase": "COMMIT",
                "done": True,
                "decision": "REJECT",
                "reason_code": "LIFE_FOREGROUND_COMMIT_FAILED_AFTER_STREAM",
                "narrative": narrative,
                "request_id": request_id,
                "sent_sentence_count": len(sentences),
                "audit_refs": {
                    "request_id": request_id,
                    "path": "surface_reply:canonical_affective_deterministic",
                    "trace_stage_meta": dict(trace),
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
    release_delivery_event = _expression_delivery_sse(foreground_receipt)
    if release_delivery_event is not None:
        yield _sse(release_delivery_event)
    effect_intents = [
        dict(item)
        for item in list(foreground_receipt.get("effect_intents") or [])
        if isinstance(item, Mapping)
    ]
    model_audit = _surface_model_audit_fields(agent_input=agent_input, trace_stage_meta=trace)
    yield _sse(
        {
            "phase": "COMMIT",
            "done": True,
            "decision": "APPROVE",
            "reason_code": envelope.reason_code or None,
            "narrative": narrative,
            "request_id": request_id,
            "reply_envelope": envelope.as_dict(),
            "acceptance_effect_intents": effect_intents,
            "audit_refs": {
                "request_id": request_id,
                "path": "surface_reply:canonical_affective_deterministic",
                "latency_ms": total_latency_ms,
                "first_token_ms": 0,
                "llm_latency_ms": 0,
                "model_id": model_audit["model_id"],
                "provider_name": model_audit["provider_name"] or "rule",
                "response_form": "text",
                "timings_ms": timings_ms,
                "reply_envelope": envelope.as_dict(),
                "surface_main_chain": _public_result_trace(
                    None,
                    stream_summary=stream_summary,
                ),
                "trace_stage_meta": {
                    **trace,
                    "surface_main_chain_used": True,
                    "reply_source": reply_source,
                    "surface_renderer": {
                        "status": "DEGRADED" if degraded else "OK",
                        "invoked": True,
                        "affected_output": True,
                        "source": reply_source,
                        "reason_code": envelope.reason_code or "",
                    },
                },
            },
        }
    )


async def _canonical_affective_deferred_delivery(
    agent_input: Any,
    *,
    ports: SurfaceRuntimePorts,
    trace: dict[str, Any],
    plan: Mapping[str, Any],
    text: str,
    started_ms: int,
    reply_source: str,
    reason_code: str,
    degraded: bool,
    audit: Mapping[str, Any] | None = None,
    raw_extra: Mapping[str, Any] | None = None,
) -> AsyncGenerator[str, None]:
    """Return a canonical rule reply as a Task-Acceptance-owned effect.

    The governed worker buffers this generator's nonterminal frames.  It must
    therefore not call the direct plan/sentence writers used by the Host
    stream: the one deferred ``life.foreground_completed`` effect carries the
    frozen plan and becomes the sole writer after Task Acceptance.
    """

    request_id = str(getattr(agent_input, "request_id", "") or "").strip()
    plan_id = str(plan.get("plan_id") or "").strip()
    expression = _safe_mapping(plan.get("expression_plan"))
    synchronization_id = str(expression.get("synchronization_id") or "").strip()
    try:
        sentences = _gated_deterministic_affective_sentences(
            text,
            agent_input=agent_input,
            trace=trace,
        )
    except Exception as exc:
        trace["affective_deterministic_delivery"] = {
            "status": "REJECTED",
            "reason_code": str(exc).split(":", 1)[0][:160],
            "reply_source": reply_source,
            "delivery_authority": "TASK_ACCEPTANCE",
        }
        yield _sse(
            {
                "phase": "COMMIT",
                "done": True,
                "decision": "NO_REPLY",
                "reason_code": str(exc).split(":", 1)[0][:160]
                or "SURFACE_STREAM_EMPTY_SENTENCE",
                "narrative": "",
                "request_id": request_id,
                "audit_refs": {
                    "request_id": request_id,
                    "path": "surface_reply:governed_affective_deterministic_buffered",
                    "trace_stage_meta": dict(trace),
                },
            }
        )
        return

    narrative = "".join(sentences).strip()
    if not narrative:
        yield _sse(
            {
                "phase": "COMMIT",
                "done": True,
                "decision": "NO_REPLY",
                "reason_code": "SURFACE_STREAM_EMPTY_SENTENCE",
                "narrative": "",
                "request_id": request_id,
                "audit_refs": {
                    "request_id": request_id,
                    "path": "surface_reply:governed_affective_deterministic_buffered",
                    "trace_stage_meta": dict(trace),
                },
            }
        )
        return

    stream_summary = {
        "mode": "governed_deterministic_affective_buffered",
        "streaming": False,
        "sentence_delivery_staged": False,
        "sentence_count": len(sentences),
        "plan_id": plan_id,
        "synchronization_id": synchronization_id,
        "self_affect_revision": plan.get("self_affect_revision"),
        "first_token_ms": None,
        "provider_usage_status": "NO_MODEL_RULE_FALLBACK",
        "reply_source": reply_source,
    }
    trace["affective_deterministic_delivery"] = {
        "status": "DEFERRED_TO_TASK_ACCEPTANCE",
        "reply_source": reply_source,
        "plan_id": plan_id,
        "synchronization_id": synchronization_id,
        "sentence_count": len(sentences),
    }
    envelope = ReplyEnvelope(
        kind="final_reply",
        text=narrative,
        response_form="text",
        source=reply_source,
        sendable=True,
        persistable=True,
        cacheable=False,
        degraded=False,
        reason_code=str(reason_code or "").strip() or None,
        audit={"request_id": request_id, **dict(audit or {})},
        raw_extra=dict(raw_extra or {}),
    )
    total_latency_ms = max(0, int(time.time() * 1000) - started_ms)
    timings_ms = _surface_timings_ms(
        total_latency_ms=total_latency_ms,
        llm_latency_ms=0,
    )
    try:
        foreground_receipt = await asyncio.to_thread(
            _submit_surface_foreground_completed,
            life_command_service=ports.get_life_command_service(),
            life_snapshot_reader=ports.get_life_snapshot_reader(),
            relationship_lifecycle_service=ports.get_relationship_lifecycle_service(),
            agent_input=agent_input,
            result=None,
            envelope=envelope,
            narrative=narrative,
            trace_stage_meta=trace,
            timings_ms=timings_ms,
            total_latency_ms=total_latency_ms,
            llm_latency_ms=0,
            stream_summary=stream_summary,
            first_token_ms=None,
            buffer_stream=True,
        )
    except Exception as exc:
        trace["life_causality"] = {
            "status": "FAILED",
            "reason_code": "LIFE_FOREGROUND_DEFER_FAILED",
            "error_type": type(exc).__name__,
        }
        yield _sse(
            {
                "phase": "COMMIT",
                "done": True,
                "decision": "NO_REPLY",
                "reason_code": "LIFE_FOREGROUND_DEFER_FAILED",
                "narrative": "",
                "request_id": request_id,
                "audit_refs": {
                    "request_id": request_id,
                    "path": "surface_reply:governed_affective_deterministic_buffered",
                    "trace_stage_meta": dict(trace),
                },
            }
        )
        return

    if str(foreground_receipt.get("status") or "") != "DEFERRED_TO_TASK_ACCEPTANCE":
        trace["life_causality"] = {
            "status": "FAILED",
            "reason_code": "LIFE_FOREGROUND_DEFER_AUTHORITY_DRIFT",
        }
        yield _sse(
            {
                "phase": "COMMIT",
                "done": True,
                "decision": "NO_REPLY",
                "reason_code": "LIFE_FOREGROUND_DEFER_AUTHORITY_DRIFT",
                "narrative": "",
                "request_id": request_id,
                "audit_refs": {
                    "request_id": request_id,
                    "path": "surface_reply:governed_affective_deterministic_buffered",
                    "trace_stage_meta": dict(trace),
                },
            }
        )
        return

    trace["life_causality"] = {
        "status": "DEFERRED_TO_TASK_ACCEPTANCE",
        "reason_code": foreground_receipt.get("reason_code"),
        "effect_count": len(list(foreground_receipt.get("effect_intents") or [])),
    }
    effect_intents = [
        dict(item)
        for item in list(foreground_receipt.get("effect_intents") or [])
        if isinstance(item, Mapping)
    ]
    model_audit = _surface_model_audit_fields(agent_input=agent_input, trace_stage_meta=trace)
    for sentence_index, sentence in enumerate(sentences):
        yield _sse({"phase": "RENDER", "chunk": sentence})
        yield _sse(
            {
                "phase": "SENTENCE",
                "sentence": sentence,
                "sentence_index": sentence_index,
                "affective_turn_plan_id": plan_id,
                "synchronization_id": synchronization_id,
            }
        )
    yield _sse(
        {
            "phase": "COMMIT",
            "done": True,
            "decision": "APPROVE",
            "reason_code": envelope.reason_code or None,
            "narrative": narrative,
            "request_id": request_id,
            "reply_envelope": envelope.as_dict(),
            "acceptance_effect_intents": effect_intents,
            "audit_refs": {
                "request_id": request_id,
                "path": "surface_reply:governed_affective_deterministic_buffered",
                "latency_ms": total_latency_ms,
                "first_token_ms": None,
                "llm_latency_ms": 0,
                "model_id": model_audit["model_id"],
                "provider_name": model_audit["provider_name"] or "rule",
                "response_form": "text",
                "timings_ms": timings_ms,
                "reply_envelope": envelope.as_dict(),
                "surface_main_chain": _public_result_trace(
                    None,
                    stream_summary=stream_summary,
                ),
                "trace_stage_meta": {
                    **trace,
                    "surface_main_chain_used": True,
                    "reply_source": reply_source,
                    "surface_renderer": {
                        "status": "DEGRADED" if degraded else "OK",
                        "invoked": True,
                        "affected_output": True,
                        "source": reply_source,
                        "reason_code": envelope.reason_code or "",
                    },
                },
            },
        }
    )
