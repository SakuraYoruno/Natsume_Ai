"""Canonical runtime responsibility extracted from the former app hotspot."""

from __future__ import annotations

from typing import Any
from collections.abc import AsyncGenerator, Mapping, Sequence
from host_brain.input_runtime.reply_envelope import ReplyEnvelope
from host_brain.persona.surface_main_chain import (
    SurfaceMainChain,
    SurfaceMainChainInput,
    SurfaceMainChainResult,
    compile_surface_main_chain_input,
)
import asyncio
import hashlib
from host_brain.persona.surface_runtime import evaluate_surface_model_response_for_retry
import json
import os
import time

from host_brain.surface_runtime.delivery_audit import (
    _delegated_agents_from_sub_agent_context,
    _merge_delegated_agent_trace,
    _public_result_trace,
    _submit_surface_affective_turn_planned,
    _submit_surface_sentence_delivery_intent,
    _submit_surface_stream_cancelled,
    _submit_surface_foreground_completed,
    _surface_model_audit_fields,
    _surface_timings_ms,
)
from host_brain.app.sentence_splitter import SentenceSplitter
from host_brain.persona.isolated_surface_harness import (
    build_isolated_surface_messages,
    build_native_stream_surface_messages,
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
from host_brain.surface_runtime.affective_streaming import (
    affective_turn_plan_from_trace,
    gate_stream_sentence,
    should_use_native_sentence_stream,
)
from host_brain.surface_runtime.subagent_context import (
    _surface_sub_agent_id,
    _surface_sub_agent_result_text,
)
from host_brain.surface_runtime.ports import (
    SurfaceRuntimePorts,
)


def _surface_main_chain_response_format() -> dict[str, str] | None:
    mode = str(os.environ.get("HOST_SURFACE_MAIN_CHAIN_OUTPUT_CONTRACT") or "text").strip().lower()
    if mode in {"json", "json_object", "structured"}:
        return {"type": "json_object"}
    return None


def _surface_model_deadline_ms(governed_model_budget: Any | None) -> int:
    configured = max(
        1_000,
        min(
            120_000,
            _coerce_int(
                os.environ.get("HOST_SURFACE_MODEL_ATTEMPT_DEADLINE_MS"),
                default=60_000,
            ),
        ),
    )
    if governed_model_budget is None:
        return configured
    return min(int(governed_model_budget.deadline_ms), configured)


def _final_stream_usage_is_attested(result: Any) -> bool:
    """Return whether a final stream result carries all canonical budget facts.

    A final provider response can be useful audit evidence even when the
    provider omits token or cost fields.  It must not be relabeled as usage
    attestation in that case.  Governed calls enforce these fields inside
    ``ModelAttemptService``; this small read-side classifier only keeps the
    direct native Surface trace honest.
    """

    if result is None or bool(getattr(result, "cached", False)):
        return False
    attempts = tuple(getattr(result, "attempts", ()) or ())
    if not attempts:
        return False
    measurements = _safe_mapping(
        _safe_mapping(getattr(attempts[-1], "metadata", None)).get(
            "provider_budget_measurements"
        )
    )
    required = ("token_count", "completion_tokens", "model_cost_micros")
    return all(
        isinstance(measurements.get(key), int)
        and not isinstance(measurements.get(key), bool)
        and int(measurements[key]) >= 0
        for key in required
    )


def _record_surface_memory_guard_packet(
    *,
    agent_input: Any,
    surface_input: SurfaceMainChainInput,
    trace_stage_meta: dict[str, Any] | None,
) -> None:
    if not isinstance(trace_stage_meta, dict):
        return
    guard_packet = (
        _raw_memory_packet_payload_from_agent_input(agent_input)
        or _surface_memory_packet_for_output_guard(
            agent_input=agent_input,
            trace_stage_meta=trace_stage_meta,
        )
        or _memory_packet_payload_for_guard(surface_input.memory_packet)
    )
    if guard_packet and not _safe_mapping(trace_stage_meta.get("surface_memory_recall_packet")):
        trace_stage_meta["surface_memory_recall_packet"] = guard_packet


def _governed_task_delivery_authority(agent_input: Any) -> Any | None:
    """Return only the app-local Task visibility authority for frozen Chat.

    This rejects an arbitrary object smuggled through an ordinary Surface
    input.  The authority is constructed by ``FrozenChatRouteExecutor`` from
    the live node attempt/lease/grant tuple and never comes from HTTP input.
    """

    authority = getattr(agent_input, "governed_delivery_authority", None)
    if authority is None:
        return None
    from host_brain.task_runtime.chat_delivery_authority import (  # noqa: PLC0415
        TaskBoundChatDeliveryAuthority,
    )

    if (
        not isinstance(authority, TaskBoundChatDeliveryAuthority)
        or not bool(getattr(agent_input, "defer_effects_to_task_runtime", False))
    ):
        raise RuntimeError("TASK_CHAT_DELIVERY_AUTHORITY_INVALID")
    return authority


def _native_affective_stream_preparation(
    agent_input: Any,
    *,
    ports: SurfaceRuntimePorts,
    trace_stage_meta: dict[str, Any],
) -> tuple[SurfaceMainChainInput, Any, dict[str, Any]] | None:
    """Compile once and select the native stream only for a frozen safe plan."""

    governed_model_budget = _governed_model_budget_guard(trace_stage_meta)
    governed_delivery_authority = _governed_task_delivery_authority(agent_input)
    surface_input = build_surface_main_chain_input_from_surface_reply_input(
        agent_input,
        ports=ports,
        trace_stage_meta=trace_stage_meta,
    )
    _record_surface_memory_guard_packet(
        agent_input=agent_input,
        surface_input=surface_input,
        trace_stage_meta=trace_stage_meta,
    )
    if not should_use_native_sentence_stream(
        trace_stage_meta,
        has_governed_model_budget=governed_model_budget is not None,
        has_governed_delivery_authority=governed_delivery_authority is not None,
    ):
        return None
    plan = affective_turn_plan_from_trace(trace_stage_meta)
    if not plan:
        return None
    return surface_input, compile_surface_main_chain_input(surface_input).request, plan


def _record_sentence_guard_trace(
    trace: dict[str, Any], *, name: str, detail: Mapping[str, Any]) -> None:
    prior = list(trace.get("surface_native_sentence_guards") or [])
    prior.append({"name": name, **dict(detail)})
    trace["surface_native_sentence_guards"] = prior[-16:]


def _expression_delivery_sse(
    receipt: Mapping[str, Any] | None,
    *,
    sentence_index: int | None = None,
) -> dict[str, Any] | None:
    """Expose only Host-staged delivery identity, never utterance payload.

    The remote Edge deliberately is not contacted in this Host stream.  This
    event tells a local consumer that the paired Voice/Avatar protocol was
    made durable before the associated text becomes visible.
    """

    delivery = _safe_mapping(_safe_mapping(receipt).get("expression_delivery"))
    trace = _safe_mapping(delivery.get("trace"))
    if not trace:
        traces = list(delivery.get("traces") or [])
        trace = _safe_mapping(traces[0]) if traces else {}
    if not trace:
        return None
    event: dict[str, Any] = {
        "phase": "DELIVERY",
        "delivery_state": str(delivery.get("status") or "HOST_PROTOCOL_STAGED"),
        "synchronization_id": str(trace.get("synchronization_id") or ""),
        "self_affect_revision": max(
            0,
            _coerce_int(trace.get("self_affect_revision"), default=0),
        ),
        "channels": [
            str(item).upper()
            for item in list(trace.get("channels") or [])
            if str(item).strip()
        ],
        "delivery_ids": [
            str(item) for item in list(trace.get("delivery_ids") or []) if str(item).strip()
        ],
    }
    if sentence_index is not None:
        event["sentence_index"] = max(0, int(sentence_index))
    return event


def _sanitize_native_stream_sentence(
    sentence: str,
    *,
    agent_input: Any,
    trace: dict[str, Any],
) -> str:
    candidate, account_guard_applied = _sanitize_account_login_reply(
        sentence,
        agent_input=agent_input,
    )
    candidate = _sanitize_main_chain_meta_reply(candidate, agent_input=agent_input)
    candidate, quality_guard = _apply_surface_text_quality_guard(
        candidate,
        agent_input=agent_input,
    )
    candidate, sub_agent_result_guard = _apply_surface_sub_agent_result_guard(
        candidate,
        agent_input=agent_input,
    )
    if account_guard_applied:
        _record_sentence_guard_trace(
            trace,
            name="account_login",
            detail={"applied": True},
        )
    if quality_guard.get("applied"):
        _record_sentence_guard_trace(
            trace,
            name="text_quality",
            detail=dict(quality_guard),
        )
    if sub_agent_result_guard.get("applied"):
        _record_sentence_guard_trace(
            trace,
            name="sub_agent_result",
            detail=dict(sub_agent_result_guard),
        )
    return str(candidate or "").strip()


def _buffered_affective_candidate_count(
    trace_stage_meta: Mapping[str, Any] | None,
) -> int | None:
    plan = affective_turn_plan_from_trace(trace_stage_meta)
    if str(plan.get("generation_mode") or "").upper() != "BUFFERED_CANDIDATE_SELECTION":
        return None
    return 2 if int(plan.get("candidate_count") or 0) == 2 else None


def _buffered_affective_candidate_messages(
    messages: Sequence[Mapping[str, Any]],
    *,
    candidate_count: int | None,
) -> list[dict[str, Any]]:
    copied = [dict(message) for message in messages]
    if candidate_count != 2:
        return copied
    override = "DL-04 高歧义回合：只输出恰好 2 个候选，JSON 形状必须是 {\"candidates\":[\"候选1\",\"候选2\"]}。不要输出第三个候选、字段解释或情绪标签。"
    if any(str(message.get("content") or "") == override for message in copied):
        return copied
    copied.append({"role": "system", "content": override})
    return copied


def _select_buffered_affective_candidate(
    *,
    raw_output: str,
    compiled_request: Any,
    expected_candidate_count: int,
    trace_stage_meta: dict[str, Any] | None = None,
) -> str:
    """Select one safe local winner from a frozen two-candidate affective turn.

    Candidate generation is deliberately not an authority to choose a
    relationship repair or emotional expression.  The ModelAttempt retry gate
    first enforces exact cardinality; this helper then applies the existing
    deterministic Surface scorer and the DL-04 non-manipulation gate before
    returning one plain reply to the legacy buffered renderer.
    """

    from host_brain.persona.surface.candidate_scoring import (  # noqa: PLC0415
        parse_surface_candidates,
        score_isolated_surface_candidates,
        select_best_isolated_candidate,
    )

    candidates = parse_surface_candidates(raw_output)
    if len(candidates) != expected_candidate_count:
        raise RuntimeError("AFFECTIVE_TURN_CANDIDATE_COUNT_INVALID")
    scores = score_isolated_surface_candidates(compiled_request, candidates)
    selected = select_best_isolated_candidate(scores)
    if (
        selected is None
        or bool(selected.hard_fail)
        or tuple(selected.control_violations)
    ):
        raise RuntimeError("AFFECTIVE_TURN_CANDIDATE_SELECTION_REJECTED")
    gate = gate_stream_sentence(selected.text)
    if not gate.accepted:
        raise RuntimeError(gate.reason_code)
    if isinstance(trace_stage_meta, dict):
        selected_index = next(
            (
                index
                for index, row in enumerate(scores)
                if row is selected
            ),
            -1,
        )
        trace_stage_meta["affective_candidate_selection"] = {
            "schema_version": "affective_candidate_selection.v1",
            "candidate_count": len(scores),
            "selected_index": selected_index,
            "selected_score": round(float(selected.score), 4),
            "selected_passed": bool(selected.passed),
            "selected_reason_codes": list(selected.reason_codes)[:16],
            "candidate_digests": [
                hashlib.sha256(str(row.text).encode("utf-8")).hexdigest()
                for row in scores
            ],
        }
    return gate.sentence


async def _native_affective_sentence_stream(
    agent_input: Any,
    *,
    ports: SurfaceRuntimePorts,
    trace: dict[str, Any],
    surface_input: SurfaceMainChainInput,
    compiled_request: Any,
    plan: Mapping[str, Any],
    started_ms: int,
) -> AsyncGenerator[str, None]:
    """Generate plaintext once, stage every sentence, then expose it over SSE.

    The plan and sentence intents are persisted before visibility.  If a model
    stops after any sentence, the caller receives an explicit partial-stream
    rejection rather than a second provider attempt that could duplicate what
    the user already saw.
    """

    from host_brain.model_runtime.contracts import (  # noqa: PLC0415
        ModelAttemptRequest,
        ModelAttemptStreamEvent,
        ModelAttemptStreamEventKind,
        PromptRef,
    )
    from host_brain.model_runtime.model_attempt_service import (  # noqa: PLC0415
        ModelStreamProtocolError,
    )
    from host_brain.model_runtime.live_prompts import (  # noqa: PLC0415
        SURFACE_MAIN_TEXT_PROMPT_ID,
    )
    from host_brain.model_runtime.trace import (  # noqa: PLC0415
        model_attempt_result_trace,
    )

    request_id = str(getattr(agent_input, "request_id", "") or "").strip()
    plan_id = str(plan.get("plan_id") or "").strip()
    expression = _safe_mapping(plan.get("expression_plan"))
    synchronization_id = str(expression.get("synchronization_id") or "").strip()
    governed_delivery_authority = _governed_task_delivery_authority(agent_input)
    governed_model_budget = _governed_model_budget_guard(trace)
    try:
        if governed_delivery_authority is not None:
            # A governed worker is still pre-Acceptance.  Its immutable plan
            # belongs in the Task-owned visibility ledger, never in Life.
            planned_receipt = await asyncio.to_thread(
                governed_delivery_authority.stage_plan,
                plan,
            )
        else:
            planned_receipt = await asyncio.to_thread(
                _submit_surface_affective_turn_planned,
                life_command_service=ports.get_life_command_service(),
                life_snapshot_reader=ports.get_life_snapshot_reader(),
                agent_input=agent_input,
                trace_stage_meta=trace,
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
                    "path": "surface_reply:native_affective_stream",
                    "durability": "fail_closed_before_stream",
                    "trace_stage_meta": dict(trace),
                },
            }
        )
        return
    trace["affective_turn_plan_receipt"] = {
        "command_id": planned_receipt.get("command_id"),
        "event_id": planned_receipt.get("event_id") or planned_receipt.get("delivery_id"),
        "state_version": planned_receipt.get("state_version"),
        "replayed": bool(planned_receipt.get("replayed")),
        "authority": (
            "TASK_BOUND_VISIBILITY" if governed_delivery_authority is not None else "LIFE"
        ),
    }

    messages = build_native_stream_surface_messages(compiled_request)
    stream_started = time.perf_counter()
    first_token_ms: int | None = None
    splitter = SentenceSplitter()
    narrative_parts: list[str] = []
    sent_sentence_count = 0
    sentence_receipts: list[dict[str, Any]] = []
    model_request = ModelAttemptRequest(
        request_id=request_id or "surface-main-chain",
        scene_type="CHAT_SURFACE",
        cognitive_tier="A",
        prompt=PromptRef(SURFACE_MAIN_TEXT_PROMPT_ID, {"messages": messages}),
        deadline_ms=_surface_model_deadline_ms(governed_model_budget),
        foreground=True,
        model_budget_guard=governed_model_budget,
        metadata={
            "chain": "surface_main_native_sentence_stream",
            "affective_turn_plan_id": plan_id,
            **(
                # The durable attempt journal must be tied to the immutable
                # TaskNode that owns this stream.  The buffered Surface path
                # already carries this field; without it a native stream can
                # finish delivery but is correctly rejected at acceptance as
                # an unbound model attempt.
                {"model_node_id": governed_model_budget.node_id}
                if governed_model_budget is not None
                else {}
            ),
        },
    )
    final_model_attempt: Any | None = None
    final_result_stream = False
    legacy_stream_fallback_reason = ""

    async def model_chunks() -> AsyncGenerator[str, None]:
        """Prefer a terminally-attested stream without weakening legacy Host chat.

        A direct native stream has its own per-sentence durable visibility
        authority, so it can expose a delta before the provider's terminal
        usage record arrives.  It may fall back to the old unmetered stream
        only when the provider proves *before emitting anything* that it does
        not implement the explicit final-result protocol.  Any malformed or
        interrupted attested stream stays fail-closed: replaying it through a
        second provider could duplicate words the user already saw.
        """

        nonlocal final_model_attempt, final_result_stream, legacy_stream_fallback_reason
        model_service = ports.model_attempt_service()
        stream_with_result = getattr(model_service, "stream_with_result", None)
        if callable(stream_with_result):
            emitted_attested_delta = False
            final_seen = False
            try:
                async for event in stream_with_result(model_request):
                    if not isinstance(event, ModelAttemptStreamEvent):
                        raise ModelStreamProtocolError(
                            "surface model stream emitted an untyped result event"
                        )
                    if event.kind is ModelAttemptStreamEventKind.DELTA:
                        if final_seen:
                            raise ModelStreamProtocolError(
                                "surface model stream emitted a delta after its final result"
                            )
                        emitted_attested_delta = True
                        yield event.text
                        continue
                    if event.kind is not ModelAttemptStreamEventKind.FINAL or final_seen:
                        raise ModelStreamProtocolError(
                            "surface model stream emitted an invalid final result sequence"
                        )
                    final_seen = True
                    final_model_attempt = event.result
                if not final_seen or final_model_attempt is None:
                    raise ModelStreamProtocolError(
                        "surface model stream ended without a final audited result"
                    )
                final_result_stream = True
                return
            except Exception as exc:
                # ModelAttemptService classifies this path before starting a
                # provider attempt.  No visible delta and this exact code are
                # the only safe conditions for the legacy direct-Host stream.
                if (
                    emitted_attested_delta
                    or str(getattr(exc, "code", "")).strip().upper()
                    != "MODEL_STREAM_RESULT_UNSUPPORTED"
                ):
                    raise
                legacy_stream_fallback_reason = "MODEL_STREAM_RESULT_UNSUPPORTED"

        async for raw_chunk in model_service.stream(model_request):
            chunk = str(raw_chunk or "")
            if chunk:
                yield chunk

    async def stage_sentence_receipt(
        sentence: str,
        *,
        sentence_index: int,
    ) -> Mapping[str, Any]:
        if governed_delivery_authority is not None:
            return await asyncio.to_thread(
                governed_delivery_authority.stage_sentence,
                plan=plan,
                sentence=sentence,
                sentence_index=sentence_index,
            )
        return await asyncio.to_thread(
            _submit_surface_sentence_delivery_intent,
            life_command_service=ports.get_life_command_service(),
            life_snapshot_reader=ports.get_life_snapshot_reader(),
            agent_input=agent_input,
            trace_stage_meta=trace,
            sentence=sentence,
            sentence_index=sentence_index,
        )

    async def record_stream_control(
        *,
        event_kind: str,
        sent_count: int,
        reason_code: str,
    ) -> Mapping[str, Any] | None:
        if governed_delivery_authority is not None:
            return await asyncio.to_thread(
                governed_delivery_authority.record_stream_control,
                plan=plan,
                event_kind=event_kind,
                sent_sentence_count=sent_count,
                reason_code=reason_code,
            )
        return None

    async def emit_staged_sentence(
        sentence: str,
        *,
        sentence_index: int,
    ) -> AsyncGenerator[str, None]:
        """Persist one final sentence before any user-visible stream frame."""

        receipt = await stage_sentence_receipt(sentence, sentence_index=sentence_index)
        delivery_id = str(receipt.get("delivery_id") or receipt.get("event_id") or "").strip()
        if governed_delivery_authority is not None and not delivery_id:
            raise RuntimeError("TASK_CHAT_DELIVERY_RECEIPT_INVALID")
        sentence_receipts.append(
            {
                "command_id": receipt.get("command_id"),
                "event_id": receipt.get("event_id") or delivery_id,
                "delivery_id": delivery_id or None,
                "sentence_index": sentence_index,
                "replayed": bool(receipt.get("replayed")),
                "expression_delivery": _safe_mapping(receipt.get("expression_delivery")),
            }
        )
        narrative_parts.append(sentence)
        # Direct Host streaming retains its pre-staged expression protocol.
        # Governed nodes defer Voice/Avatar intents to Task Acceptance, so no
        # external or protocol effect is exposed before verification.
        if governed_delivery_authority is None:
            delivery_event = _expression_delivery_sse(
                receipt,
                sentence_index=sentence_index,
            )
            if delivery_event is not None:
                yield _sse(delivery_event)
        yield _sse({"phase": "RENDER", "chunk": sentence})
        sentence_event: dict[str, Any] = {
            "phase": "SENTENCE",
            "sentence": sentence,
            "sentence_index": sentence_index,
            "affective_turn_plan_id": plan_id,
            "synchronization_id": synchronization_id,
        }
        if governed_delivery_authority is not None:
            sentence_event.update(
                {
                    "delivery_id": delivery_id,
                    "delivery_status": str(
                        receipt.get("delivery_status") or "TASK_NODE_SENTENCE_STAGED"
                    ),
                    "replayed": bool(receipt.get("replayed")),
                    "request_id": receipt.get("request_id"),
                    "graph_id": receipt.get("graph_id"),
                    "graph_revision": receipt.get("graph_revision"),
                    "node_id": receipt.get("node_id"),
                    "attempt_id": receipt.get("attempt_id"),
                    "lease_id": receipt.get("lease_id"),
                    "lease_owner_id": receipt.get("lease_owner_id"),
                    "fencing_token": receipt.get("fencing_token"),
                    "node_grant_ref": receipt.get("node_grant_ref"),
                    "self_id": receipt.get("self_id"),
                    "relationship_subject_id": receipt.get("relationship_subject_id"),
                    "workspace_id": receipt.get("workspace_id"),
                    "self_affect_revision": receipt.get("self_affect_revision"),
                }
            )
        yield _sse(sentence_event)

    try:
        async for raw_chunk in model_chunks():
            if first_token_ms is None:
                first_token_ms = max(
                    0,
                    int((time.perf_counter() - stream_started) * 1000),
                )
            for raw_sentence in splitter.feed(str(raw_chunk or "")):
                candidate = _sanitize_native_stream_sentence(
                    raw_sentence,
                    agent_input=agent_input,
                    trace=trace,
                )
                gate = gate_stream_sentence(candidate)
                if not gate.accepted:
                    raise RuntimeError(gate.reason_code)
                async for event in emit_staged_sentence(
                    gate.sentence,
                    sentence_index=sent_sentence_count,
                ):
                    yield event
                sent_sentence_count += 1
            if len(splitter.buffer) > 2_000:
                raise RuntimeError("SURFACE_STREAM_SENTENCE_BOUNDARY_REQUIRED")

        remainder = splitter.flush()
        if remainder:
            candidate = _sanitize_native_stream_sentence(
                remainder,
                agent_input=agent_input,
                trace=trace,
            )
            gate = gate_stream_sentence(candidate)
            if not gate.accepted:
                raise RuntimeError(gate.reason_code)
            async for event in emit_staged_sentence(
                gate.sentence,
                sentence_index=sent_sentence_count,
            ):
                yield event
            sent_sentence_count += 1
    except asyncio.CancelledError:
        try:
            if governed_delivery_authority is not None:
                await asyncio.shield(
                    record_stream_control(
                        event_kind="STREAM_CANCELLED",
                        sent_count=sent_sentence_count,
                        reason_code="SURFACE_STREAM_CANCELLED",
                    )
                )
            else:
                await asyncio.shield(
                    asyncio.to_thread(
                        _submit_surface_stream_cancelled,
                        life_command_service=ports.get_life_command_service(),
                        life_snapshot_reader=ports.get_life_snapshot_reader(),
                        agent_input=agent_input,
                        trace_stage_meta=trace,
                        sent_sentence_count=sent_sentence_count,
                        reason_code="SURFACE_STREAM_CANCELLED",
                    )
                )
        except Exception:
            # Cancellation must still propagate.  The next connection can
            # inspect the staged sentence receipts and reconcile the stream.
            pass
        raise
    except Exception as exc:
        interruption_reason = _native_stream_failure_reason(exc)
        trace["native_sentence_stream"] = {
            "status": "INTERRUPTED",
            "reason_code": interruption_reason,
            "sent_sentence_count": sent_sentence_count,
        }
        if governed_delivery_authority is not None:
            try:
                await asyncio.shield(
                    record_stream_control(
                        event_kind="STREAM_INTERRUPTED",
                        sent_count=sent_sentence_count,
                        reason_code=interruption_reason,
                    )
                )
            except Exception:
                # The Task Runtime will classify this as a recovery-required
                # boundary either way.  Never manufacture a terminal reject
                # after a sentence has become visible.
                pass
            if sent_sentence_count == 0:
                # No text, Voice or Avatar action has crossed the visibility
                # boundary.  This is a normal fail-closed terminal outcome,
                # not an ``unknown effect`` recovery case.  Preserve the
                # stable model/runtime cause in the immutable Task delivery
                # journal so the worker can release its lease and budget
                # without re-running an unseen provider attempt.
                yield _sse(
                    {
                        "phase": "COMMIT",
                        "done": True,
                        "decision": "REJECT",
                        "reason_code": interruption_reason,
                        "narrative": "",
                        "request_id": request_id,
                        "sent_sentence_count": 0,
                        "audit_refs": {
                            "request_id": request_id,
                            "path": "surface_reply:native_affective_stream",
                            "durability": "fail_closed_before_visibility",
                            "trace_stage_meta": dict(trace),
                        },
                    }
                )
                return
            raise RuntimeError("TASK_CHAT_STREAM_INTERRUPTED") from exc
        yield _sse(
            {
                "phase": "COMMIT",
                "done": True,
                "decision": "REJECT" if sent_sentence_count else "NO_REPLY",
                "reason_code": "SURFACE_STREAM_INTERRUPTED",
                "narrative": "".join(narrative_parts),
                "request_id": request_id,
                "sent_sentence_count": sent_sentence_count,
                "audit_refs": {
                    "request_id": request_id,
                    "path": "surface_reply:native_affective_stream",
                    "trace_stage_meta": dict(trace),
                },
            }
        )
        return

    narrative = "".join(narrative_parts).strip()
    if not narrative:
        if governed_delivery_authority is not None:
            await record_stream_control(
                event_kind="STREAM_COMPLETED",
                sent_count=0,
                reason_code="SURFACE_STREAM_EMPTY_RESPONSE",
            )
        yield _sse(
            {
                "phase": "COMMIT",
                "done": True,
                "decision": "NO_REPLY",
                "reason_code": "SURFACE_STREAM_EMPTY_RESPONSE",
                "narrative": "",
                "request_id": request_id,
                "audit_refs": {
                    "request_id": request_id,
                    "path": "surface_reply:native_affective_stream",
                    "trace_stage_meta": dict(trace),
                },
            }
        )
        return

    stream_control_receipt: Mapping[str, Any] | None = None
    if governed_delivery_authority is not None:
        try:
            stream_control_receipt = await record_stream_control(
                event_kind="STREAM_COMPLETED",
                sent_count=sent_sentence_count,
                reason_code="",
            )
        except Exception as exc:
            trace["native_sentence_stream"] = {
                "status": "INTERRUPTED",
                "reason_code": "TASK_CHAT_STREAM_CONTROL_FAILED",
                "sent_sentence_count": sent_sentence_count,
            }
            raise RuntimeError("TASK_CHAT_STREAM_CONTROL_FAILED") from exc

    total_latency_ms = max(0, int(time.time() * 1000) - started_ms)
    llm_latency_ms = max(0, int((time.perf_counter() - stream_started) * 1000))
    final_usage_attested = _final_stream_usage_is_attested(final_model_attempt)
    stream_summary = {
        "mode": (
            "governed_node_sentence_stream"
            if governed_delivery_authority is not None
            else "native_sentence_stream"
        ),
        "sentence_count": sent_sentence_count,
        # This field never becomes a public stream trace.  It is the exact
        # locally-gated sentence sequence needed to compile the deferred
        # Acceptance-side Voice/Avatar protocol for a governed node.  Keeping
        # it here prevents foreground completion from coalescing visible
        # sentences into a different utterance boundary.
        "stable_sentences": tuple(narrative_parts),
        "plan_id": plan_id,
        "synchronization_id": synchronization_id,
        "self_affect_revision": plan.get("self_affect_revision"),
        "first_token_ms": first_token_ms,
        "provider_usage_status": (
            "STREAM_FINAL_USAGE_ATTESTED"
            if final_usage_attested
            else "STREAM_FINAL_RESULT_AUDITED_UNMEASURED"
            if final_result_stream
            else "STREAM_ATTEMPT_AUDITED_NO_FINAL_USAGE"
        ),
    }
    if final_model_attempt is not None:
        attempt_trace = model_attempt_result_trace(final_model_attempt)
    else:
        attempt_trace = {}
    attempt_trace.update(
        {
            "streaming": True,
            "prompt_id": SURFACE_MAIN_TEXT_PROMPT_ID,
            "message_count": len(messages),
            "generate_elapsed_ms": llm_latency_ms,
            "first_token_ms": first_token_ms,
            "provider_usage_status": stream_summary["provider_usage_status"],
        }
    )
    trace["surface_model_attempt"] = attempt_trace
    trace["native_sentence_stream"] = {
        "status": "COMPLETED",
        "plan_id": plan_id,
        "synchronization_id": synchronization_id,
        "sent_sentence_count": sent_sentence_count,
        "sentence_receipts": sentence_receipts,
        "stream_control_receipt": (
            dict(stream_control_receipt) if stream_control_receipt is not None else None
        ),
        "model_stream_protocol": (
            "FINAL_RESULT_WITH_USAGE"
            if final_usage_attested
            else "FINAL_RESULT_UNMEASURED"
            if final_result_stream
            else "LEGACY_NO_FINAL_USAGE"
        ),
        "legacy_stream_fallback_reason": legacy_stream_fallback_reason or None,
    }
    timings_ms = _surface_timings_ms(
        total_latency_ms=total_latency_ms,
        llm_latency_ms=llm_latency_ms,
    )
    envelope = ReplyEnvelope.final_reply(
        text=narrative,
        response_form="text",
        source="surface_main_chain_native_stream",
        audit={
            "request_id": request_id,
            "affective_turn_plan_id": plan_id,
            "synchronization_id": synchronization_id,
            "self_emotion_tag": str(plan.get("self_emotion_tag") or ""),
            "companion_act": str(_safe_mapping(plan.get("companion_act")).get("kind") or ""),
        },
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
            llm_latency_ms=llm_latency_ms,
            stream_summary=stream_summary,
            first_token_ms=first_token_ms,
            buffer_stream=False,
        )
    except Exception as exc:
        trace["life_causality"] = {
            "status": "FAILED",
            "reason_code": "LIFE_FOREGROUND_COMMIT_FAILED_AFTER_STREAM",
            "error_type": type(exc).__name__,
            "sent_sentence_count": sent_sentence_count,
        }
        if governed_delivery_authority is not None:
            try:
                await asyncio.shield(
                    record_stream_control(
                        event_kind="STREAM_INTERRUPTED",
                        sent_count=sent_sentence_count,
                        reason_code="TASK_ACCEPTANCE_EFFECT_INTENT_BUILD_FAILED",
                    )
                )
            except Exception:
                pass
            raise RuntimeError("TASK_CHAT_ACCEPTANCE_EFFECT_INTENT_BUILD_FAILED") from exc
        yield _sse(
            {
                "phase": "COMMIT",
                "done": True,
                "decision": "REJECT",
                "reason_code": "LIFE_FOREGROUND_COMMIT_FAILED_AFTER_STREAM",
                "narrative": narrative,
                "request_id": request_id,
                "sent_sentence_count": sent_sentence_count,
                "audit_refs": {
                    "request_id": request_id,
                    "path": "surface_reply:native_affective_stream",
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
                "path": "surface_reply:native_affective_stream",
                "latency_ms": total_latency_ms,
                "first_token_ms": first_token_ms,
                "llm_latency_ms": llm_latency_ms,
                "model_id": model_audit["model_id"],
                "provider_name": model_audit["provider_name"],
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
                    "reply_source": "surface_main_chain_native_stream",
                    "surface_renderer": {
                        "status": "OK",
                        "invoked": True,
                        "affected_output": True,
                        "source": "surface_main_chain_native_stream",
                        "reason_code": "",
                    },
                },
            },
        }
    )


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


async def run_surface_main_chain_for_surface_reply_input(
    agent_input: Any,
    *,
    ports: SurfaceRuntimePorts,
    trace_stage_meta: dict[str, Any] | None = None,
) -> SurfaceMainChainResult:
    request_id = str(getattr(agent_input, "request_id", "") or "").strip()
    governed_model_budget = _governed_model_budget_guard(trace_stage_meta)
    surface_input = build_surface_main_chain_input_from_surface_reply_input(
        agent_input,
        ports=ports,
        trace_stage_meta=trace_stage_meta,
    )
    compiled_request = compile_surface_main_chain_input(surface_input).request
    _record_surface_memory_guard_packet(
        agent_input=agent_input,
        surface_input=surface_input,
        trace_stage_meta=trace_stage_meta,
    )
    affective_candidate_count = _buffered_affective_candidate_count(trace_stage_meta)

    async def _generate(messages: list[dict[str, str]]) -> str:
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
            if affective_candidate_count is not None:
                try:
                    _select_buffered_affective_candidate(
                        raw_output=str(response.content or ""),
                        compiled_request=compiled_request,
                        expected_candidate_count=affective_candidate_count,
                    )
                except RuntimeError as exc:
                    repaired_messages = _buffered_affective_candidate_messages(
                        attempt.prompt.messages,
                        candidate_count=affective_candidate_count,
                    )
                    return RetryDirective(
                        retry=True,
                        reason=str(exc).split(":", 1)[0],
                        rendered_prompt=attempt.prompt.with_messages(repaired_messages),
                        reuse_target=True,
                        metadata={
                            "expected_candidate_count": affective_candidate_count,
                        },
                    )
                return RetryDirective.accept()
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
                rendered_prompt=attempt.prompt.with_messages(
                    _buffered_affective_candidate_messages(
                        evaluation.messages,
                        candidate_count=affective_candidate_count,
                    )
                ),
                reuse_target=True,
                metadata={"violation_codes": list(evaluation.violation_codes)},
            )

        generate_started = time.perf_counter()
        model_deadline_ms = _surface_model_deadline_ms(governed_model_budget)
        if governed_model_budget is not None and model_deadline_ms <= 0:
            raise RuntimeError("MODEL_BUDGET_DEADLINE_EXHAUSTED")
        result = await ports.model_attempt_service().complete(
            ModelAttemptRequest(
                request_id=request_id or "surface-main-chain",
                scene_type="CHAT_SURFACE",
                cognitive_tier="A",
                prompt=PromptRef(
                    prompt_id,
                    {
                        "messages": _buffered_affective_candidate_messages(
                            messages,
                            candidate_count=affective_candidate_count,
                        )
                    },
                ),
                deadline_ms=model_deadline_ms,
                foreground=True,
                retry_evaluator=_retry_evaluator,
                metadata={
                    "chain": "surface_main",
                    **(
                        {"model_node_id": governed_model_budget.node_id}
                        if governed_model_budget is not None
                        else {}
                    ),
                },
                model_budget_guard=governed_model_budget,
            )
        )
        if isinstance(trace_stage_meta, dict):
            attempt_trace = model_attempt_result_trace(result)
            attempt_trace["generate_elapsed_ms"] = int(
                (time.perf_counter() - generate_started) * 1000
            )
            attempt_trace["message_count"] = len(messages)
            if affective_candidate_count is not None:
                attempt_trace["affective_candidate_count"] = affective_candidate_count
            trace_stage_meta["surface_model_attempt"] = attempt_trace
        if not result.success:
            raise RuntimeError(
                f"{result.error_code or 'SURFACE_MODEL_ATTEMPT_FAILED'}: "
                f"{result.error_message or 'model attempt failed'}"
            )
        content = str(result.content or "").strip()
        if not content:
            raise RuntimeError("SURFACE_LLM_EMPTY_RESPONSE")
        if affective_candidate_count is not None:
            return _select_buffered_affective_candidate(
                raw_output=content,
                compiled_request=compiled_request,
                expected_candidate_count=affective_candidate_count,
                trace_stage_meta=trace_stage_meta,
            )
        return content

    return await SurfaceMainChain(
        _generate,
        model_retries_owned_externally=True,
    ).run(surface_input)


async def surface_main_chain_sse_generator(
    agent_input: Any,
    *,
    ports: SurfaceRuntimePorts,
    trace_stage_meta: Mapping[str, Any] | None = None,
) -> AsyncGenerator[str, None]:
    started_ms = int(time.time() * 1000)
    trace = {
        **_safe_mapping(getattr(agent_input, "trace_stage_meta", None)),
        **dict(trace_stage_meta or {}),
    }
    delegated_agents = _delegated_agents_from_sub_agent_context(agent_input)
    _merge_delegated_agent_trace(trace, delegated_agents)
    request_id = str(getattr(agent_input, "request_id", "") or "").strip()
    canonical_affective_context = bool(
        _safe_mapping(getattr(agent_input, "emotion_context", None)).get(
            "_affective_life_internal"
        )
    )
    evidence_gate_text = _surface_perception_evidence_gate_text(agent_input)
    if evidence_gate_text:
        # A rule-based perception boundary is still Natsume speaking.  It
        # therefore always receives the canonical plan.  Direct Host delivery
        # stages it locally; a governed worker instead returns one deferred
        # foreground effect, which becomes the sole writer after Acceptance.
        if canonical_affective_context:
            try:
                surface_input = build_surface_main_chain_input_from_surface_reply_input(
                    agent_input,
                    ports=ports,
                    trace_stage_meta=trace,
                )
                _record_surface_memory_guard_packet(
                    agent_input=agent_input,
                    surface_input=surface_input,
                    trace_stage_meta=trace,
                )
                plan = affective_turn_plan_from_trace(trace)
                if not plan:
                    raise RuntimeError("AFFECTIVE_TURN_PLAN_REQUIRED_FOR_RULE_REPLY")
            except Exception as exc:
                yield _sse(
                    {
                        "phase": "COMMIT",
                        "done": True,
                        "decision": "NO_REPLY",
                        "reason_code": str(exc).split(":", 1)[0][:160]
                        or "AFFECTIVE_TURN_PLAN_REQUIRED_FOR_RULE_REPLY",
                        "narrative": "",
                        "request_id": request_id,
                        "audit_refs": {
                            "request_id": request_id,
                            "path": "surface_reply:canonical_affective_deterministic",
                            "durability": "fail_closed_before_stream",
                            "trace_stage_meta": {
                                **trace,
                                "surface_main_chain_used": True,
                                "reply_source": "surface_evidence_gate_prepare_reject",
                            },
                        },
                    }
                )
                return
            trace["surface_evidence_gate"] = {
                "status": "OK",
                "reason_code": "SURFACE_PERCEPTION_EVIDENCE_REQUIRED",
                "has_multimodal_payload": False,
            }
            if bool(getattr(agent_input, "defer_effects_to_task_runtime", False)):
                async for event in _canonical_affective_deferred_delivery(
                    agent_input,
                    ports=ports,
                    trace=trace,
                    plan=plan,
                    text=evidence_gate_text,
                    started_ms=started_ms,
                    reply_source="surface_evidence_gate",
                    reason_code="SURFACE_PERCEPTION_EVIDENCE_REQUIRED",
                    degraded=False,
                    audit={"evidence_gate": True},
                    raw_extra={"evidence_gate": True},
                ):
                    yield event
                return
            if _has_governed_model_budget(trace):
                yield _sse(
                    {
                        "phase": "COMMIT",
                        "done": True,
                        "decision": "NO_REPLY",
                        "reason_code": "GOVERNED_AFFECTIVE_DEFER_REQUIRED",
                        "narrative": "",
                        "request_id": request_id,
                        "audit_refs": {
                            "request_id": request_id,
                            "path": "surface_reply:governed_affective_deterministic_buffered",
                            "durability": "fail_closed_before_stream",
                            "trace_stage_meta": {
                                **trace,
                                "surface_main_chain_used": True,
                                "reply_source": "surface_evidence_gate_defer_reject",
                            },
                        },
                    }
                )
                return
            async for event in _canonical_affective_deterministic_delivery(
                agent_input,
                ports=ports,
                trace=trace,
                plan=plan,
                text=evidence_gate_text,
                started_ms=started_ms,
                reply_source="surface_evidence_gate",
                reason_code="SURFACE_PERCEPTION_EVIDENCE_REQUIRED",
                degraded=False,
                audit={"evidence_gate": True},
                raw_extra={"evidence_gate": True},
            ):
                yield event
            return
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

    # DL-04 only changes calls that already carry the authenticated Affect
    # Fusion hand-off.  Legacy inputs deliberately stay on the compatibility
    # path until DL-06 removes their remaining parallel emotion writers.
    if canonical_affective_context:
        try:
            prepared_stream = _native_affective_stream_preparation(
                agent_input,
                ports=ports,
                trace_stage_meta=trace,
            )
        except Exception as exc:
            yield _sse(
                {
                    "phase": "COMMIT",
                    "done": True,
                    "decision": "REJECT",
                    "reason_code": str(exc).split(":", 1)[0][:160] or "AFFECTIVE_TURN_PREPARE_FAILED",
                    "narrative": "",
                    "request_id": request_id,
                    "audit_refs": {
                        "request_id": request_id,
                        "path": "surface_reply:native_affective_stream",
                        "trace_stage_meta": {
                            **trace,
                            "surface_main_chain_used": True,
                            "reply_source": "affective_turn_prepare_reject",
                        },
                    },
                }
            )
            return
        if prepared_stream is not None:
            surface_input, compiled_request, plan = prepared_stream
            async for event in _native_affective_sentence_stream(
                agent_input,
                ports=ports,
                trace=trace,
                surface_input=surface_input,
                compiled_request=compiled_request,
                plan=plan,
                started_ms=started_ms,
            ):
                yield event
            return
    try:
        result = await run_surface_main_chain_for_surface_reply_input(
            agent_input,
            ports=ports,
            trace_stage_meta=trace,
        )
    except Exception as exc:
        total_latency_ms = max(0, int(time.time() * 1000) - started_ms)
        if _has_governed_model_budget(trace):
            reason_code = _governed_model_budget_failure_reason(exc)
            yield _sse(
                {
                    "phase": "COMMIT",
                    "done": True,
                    "decision": "REJECT",
                    "reason_code": reason_code,
                    "narrative": "受治理的模型预算未满足，当前节点已停止。",
                    "request_id": request_id,
                    "audit_refs": {
                        "request_id": request_id,
                        "path": "surface_reply:surface_main_chain",
                        "latency_ms": total_latency_ms,
                        "reason_code": reason_code,
                        "timings_ms": _surface_timings_ms(
                            total_latency_ms=total_latency_ms,
                            llm_latency_ms=0,
                        ),
                        "trace_stage_meta": {
                            **trace,
                            "surface_main_chain_used": True,
                            "reply_source": "surface_main_chain_budget_reject",
                        },
                    },
                }
            )
            return
        fallback_text = _surface_failure_fallback_text(agent_input, exc=exc)
        if fallback_text:
            if canonical_affective_context:
                plan = affective_turn_plan_from_trace(trace)
                if not plan:
                    yield _sse(
                        {
                            "phase": "COMMIT",
                            "done": True,
                            "decision": "NO_REPLY",
                            "reason_code": "AFFECTIVE_TURN_PLAN_REQUIRED_FOR_FALLBACK",
                            "narrative": "",
                            "request_id": request_id,
                            "audit_refs": {
                                "request_id": request_id,
                                "path": "surface_reply:canonical_affective_deterministic",
                                "durability": "fail_closed_before_stream",
                                "trace_stage_meta": {
                                    **trace,
                                    "surface_main_chain_used": True,
                                    "reply_source": "surface_main_chain_fallback_reject",
                                },
                            },
                        }
                    )
                    return
                async for event in _canonical_affective_deterministic_delivery(
                    agent_input,
                    ports=ports,
                    trace=trace,
                    plan=plan,
                    text=fallback_text,
                    started_ms=started_ms,
                    reply_source="surface_main_chain_affective_fallback",
                    reason_code="SURFACE_MAIN_CHAIN_FALLBACK",
                    degraded=True,
                    audit={"request_id": request_id, "error": type(exc).__name__},
                    raw_extra={
                        "fallback": True,
                        "degraded": True,
                        "reason_code": "SURFACE_MAIN_CHAIN_FAILED",
                    },
                ):
                    yield event
                return
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
    # A reply node owns exactly the primary Surface model attempt frozen into
    # its TaskGraph budget.  The former optional Spark "judge" made a second,
    # unbound model call after the candidate had already been produced.  It is
    # retired instead of being skipped only for governed inputs: otherwise a
    # future non-governed caller could silently recreate a second model
    # authority outside Attempt/Budget/Acceptance.  Retire the route globally
    # rather than merely skipping it for governed inputs.
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
    acceptance_effect_intents: list[dict[str, Any]] = []
    if envelope.persistable:
        try:
            foreground_receipt = await asyncio.to_thread(
                _submit_surface_foreground_completed,
                life_command_service=ports.get_life_command_service(),
                life_snapshot_reader=ports.get_life_snapshot_reader(),
                relationship_lifecycle_service=(
                    ports.get_relationship_lifecycle_service()
                ),
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
        raw_effect_intents = foreground_receipt.get("effect_intents")
        if isinstance(raw_effect_intents, list):
            acceptance_effect_intents = [
                dict(item)
                for item in raw_effect_intents
                if isinstance(item, Mapping)
            ]
    else:
        trace["life_causality"] = {
            "status": "SKIPPED",
            "reason_code": "REPLY_NOT_PERSISTABLE",
        }
    if narrative:
        delivery_event = _expression_delivery_sse(foreground_receipt)
        if delivery_event is not None:
            yield _sse(delivery_event)
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
            "acceptance_effect_intents": acceptance_effect_intents,
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


def _governed_model_budget_guard(
    trace_stage_meta: Mapping[str, Any] | None,
):
    """Decode only the immutable guard inserted by the governed route worker."""

    raw = _safe_mapping(trace_stage_meta).get("governed_model_budget")
    if not raw:
        return None
    if not isinstance(raw, Mapping):
        raise RuntimeError("MODEL_BUDGET_GUARD_INVALID")
    from host_brain.model_runtime.contracts import ModelBudgetGuard  # noqa: PLC0415

    try:
        return ModelBudgetGuard.from_mapping(raw)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("MODEL_BUDGET_GUARD_INVALID") from exc


def _has_governed_model_budget(trace_stage_meta: Mapping[str, Any] | None) -> bool:
    return bool(_safe_mapping(trace_stage_meta).get("governed_model_budget"))


def _governed_model_budget_failure_reason(exc: BaseException) -> str:
    candidate = str(exc).split(":", 1)[0].strip().upper()
    if candidate.startswith("MODEL_"):
        return candidate
    return "MODEL_BUDGET_GUARD_REJECTED"


def _native_stream_failure_reason(exc: BaseException) -> str:
    """Return a public-safe, machine-stable cause for an unseen stream failure.

    The native sentence path is allowed to surface the cause only before a
    sentence becomes visible.  Do not forward provider prose, URLs or Python
    trace text into the client or immutable task receipt.
    """

    candidate = str(getattr(exc, "code", "") or "").strip().upper()
    if not candidate:
        candidate = str(exc).split(":", 1)[0].strip().upper()
    compact = candidate.replace("_", "")
    if (
        3 <= len(candidate) <= 160
        and compact.isalnum()
        and candidate[:1] in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        and all(char in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_" for char in candidate)
    ):
        return candidate
    return "SURFACE_STREAM_INTERRUPTED"


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
