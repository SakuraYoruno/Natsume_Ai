"""Frozen chat and delegate executors for the governed Task Runtime.

The adapters in this module are deliberately narrower than the legacy
Executive runtime.  They receive one already-routed :class:`FrozenNodeInput`,
invoke an injected low-level execution port, and emit one terminal COMMIT
mapping.  They neither select a route nor invoke a planner.
"""

from __future__ import annotations

from collections.abc import AsyncIterable, AsyncIterator, Mapping, Sequence
from dataclasses import dataclass
import inspect
import json
import re
from typing import Any, Protocol

from host_brain.model_runtime.contracts import ModelBudgetGuard
from host_brain.task_runtime.governed_workers import FrozenNodeInput
from host_brain.contracts.agent_runtime.worker_budget_usage import (
    build_worker_budget_usage,
    stable_worker_usage_id,
)


GOVERNED_ROUTE_INPUT_INVALID = "GOVERNED_ROUTE_INPUT_INVALID"
GOVERNED_ROUTE_OUTPUT_INVALID = "GOVERNED_ROUTE_OUTPUT_INVALID"
GOVERNED_ROUTE_ROUTE_DRIFT = "GOVERNED_ROUTE_ROUTE_DRIFT"
GOVERNED_ROUTE_BUDGET_MEASUREMENT_REQUIRED = "GOVERNED_ROUTE_BUDGET_MEASUREMENT_REQUIRED"


class GovernedRouteExecutorError(RuntimeError):
    """Fail-closed error raised before a frozen executor can commit output."""

    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = str(reason_code or GOVERNED_ROUTE_OUTPUT_INVALID)
        self.detail = str(detail or "governed route execution failed")
        super().__init__(f"{self.reason_code}: {self.detail}")


class DelegateDispatchPort(Protocol):
    def __call__(self, **kwargs: Any) -> Any: ...


class ChatStreamPort(Protocol):
    def __call__(self, **kwargs: Any) -> AsyncIterable[Any] | Any: ...


@dataclass(frozen=True, slots=True)
class _FrozenChatRequestBody:
    """Authority-free request projection consumed by ``_build_chat_input``."""

    input_source: str
    response_form: str
    reply_channel: None
    reply_target: None
    suppress_edge_tts: bool
    suppress_edge_vts: bool
    defer_effects_to_task_runtime: bool
    render_policy: Mapping[str, Any]
    surface_task_control: Mapping[str, Any]
    attachments: tuple[Any, ...]
    multimodal_observations: Mapping[str, Any]
    attachment_summary: str


class FrozenDelegateRouteExecutor:
    """Invoke the injected ``_dispatch_to_sub_agent``-compatible port once."""

    def __init__(self, *, ports: Any, dispatch_to_sub_agent: DelegateDispatchPort) -> None:
        if ports is None:
            raise ValueError("ports is required")
        if not callable(dispatch_to_sub_agent):
            raise ValueError("dispatch_to_sub_agent must be callable")
        self._ports = ports
        self._dispatch = dispatch_to_sub_agent

    async def execute_stream(self, frozen: FrozenNodeInput) -> AsyncIterator[Mapping[str, Any]]:
        _require_frozen_route(frozen, expected="delegate")
        input_pack = _input_pack(frozen)
        user_message = _required_narrative(
            input_pack.get("sanitized_message")
            or input_pack.get("user_message")
            or frozen.objective,
            field_name="delegate user_message",
        )
        context = _delegate_context(input_pack, frozen=frozen)

        dispatched = self._dispatch(
            ports=self._ports,
            frozen=frozen,
            request_id=frozen.request_id,
            session_id=frozen.session_id,
            person_id=frozen.principal_id,
            target_id=frozen.target_id,
            task_text=frozen.objective,
            user_message=user_message,
            context=context,
        )
        outcome = await dispatched if inspect.isawaitable(dispatched) else dispatched
        projected = _project_outcome(outcome)
        _assert_no_route_drift(projected, frozen=frozen)

        decision = _decision_from_outcome(projected)
        narrative = _outcome_narrative(projected, decision=decision)
        reason_code = _outcome_reason_code(projected, decision=decision)
        audit_refs = _outcome_audit_refs(projected)
        yield _terminal_commit(
            frozen=frozen,
            decision=decision,
            narrative=narrative,
            reason_code=reason_code,
            audit_refs=audit_refs,
            budget_usage=_worker_budget_payload(
                frozen,
                observations=_delegate_budget_observations(audit_refs),
                include_agent_tool_calls=True,
            ),
        )


class FrozenChatRouteExecutor:
    """Buffer an injected chat stream into one authority-free terminal result."""

    def __init__(self, *, ports: Any, stream_chat_with_context: ChatStreamPort) -> None:
        if ports is None:
            raise ValueError("ports is required")
        if not callable(stream_chat_with_context):
            raise ValueError("stream_chat_with_context must be callable")
        self._ports = ports
        self._stream_chat = stream_chat_with_context

    async def execute_stream(self, frozen: FrozenNodeInput) -> AsyncIterator[Mapping[str, Any]]:
        _require_frozen_route(frozen, expected="chat")
        input_pack = _input_pack(frozen)
        stream = self._stream_chat(**_chat_call_kwargs(self._ports, frozen=frozen, input_pack=input_pack))
        if inspect.isawaitable(stream):
            stream = await stream
        if not isinstance(stream, AsyncIterable):
            raise GovernedRouteExecutorError(
                GOVERNED_ROUTE_OUTPUT_INVALID,
                "chat port must return an async iterable",
            )

        terminal: dict[str, Any] | None = None
        terminal_count = 0
        event_count = 0
        render_fragments: list[str] = []
        sentence_fragments: list[str] = []
        pending_sse = ""

        async for raw in stream:
            if isinstance(raw, Mapping):
                if pending_sse.strip():
                    raise GovernedRouteExecutorError(
                        GOVERNED_ROUTE_OUTPUT_INVALID,
                        "chat stream mixed an incomplete SSE frame with a mapping",
                    )
                events = (dict(raw),)
            elif isinstance(raw, (str, bytes, bytearray)):
                chunk = (
                    bytes(raw).decode("utf-8", errors="strict")
                    if isinstance(raw, (bytes, bytearray))
                    else raw
                )
                pending_sse += chunk.replace("\r\n", "\n").replace("\r", "\n")
                frames, pending_sse = _split_complete_sse_frames(pending_sse)
                events = tuple(
                    event
                    for frame in frames
                    for event in _decode_event_frame(frame)
                )
            else:
                raise GovernedRouteExecutorError(
                    GOVERNED_ROUTE_OUTPUT_INVALID,
                    "chat stream emitted neither SSE text nor a mapping",
                )

            for event in events:
                event_count += 1
                _assert_no_route_drift(event, frozen=frozen)
                is_terminal = _is_terminal(event)
                if terminal is not None and not is_terminal:
                    raise GovernedRouteExecutorError(
                        GOVERNED_ROUTE_OUTPUT_INVALID,
                        "chat stream emitted data after its terminal event",
                    )
                if is_terminal:
                    terminal_count += 1
                    terminal = event
                    continue
                _buffer_narrative(event, render_fragments, sentence_fragments)

        if pending_sse.strip():
            for event in _decode_event_frame(pending_sse):
                event_count += 1
                _assert_no_route_drift(event, frozen=frozen)
                if not _is_terminal(event) or terminal is not None:
                    raise GovernedRouteExecutorError(
                        GOVERNED_ROUTE_OUTPUT_INVALID,
                        "chat stream ended with an invalid or duplicate terminal frame",
                    )
                terminal_count += 1
                terminal = event

        if terminal_count != 1 or terminal is None:
            raise GovernedRouteExecutorError(
                GOVERNED_ROUTE_OUTPUT_INVALID,
                f"chat stream must emit exactly one terminal event, got {terminal_count}",
            )

        decision = _normalize_terminal_decision(terminal)
        narrative = str(terminal.get("narrative") or "").strip()
        if not narrative:
            narrative = "".join(render_fragments).strip()
        if not narrative:
            narrative = "\n".join(sentence_fragments).strip()
        narrative = _required_narrative(narrative, field_name="chat terminal narrative")
        reason_code = _terminal_reason_code(terminal, decision=decision)
        audit_refs = _safe_mapping(terminal.get("audit_refs"))
        audit_refs.update(
            {
                "governed_frozen_route": True,
                "source_event_count": event_count,
                "edge_tts_suppressed": True,
                "edge_vts_suppressed": True,
                "delivery_authorized": False,
            }
        )
        _assert_guarded_chat_terminal(
            frozen=frozen,
            decision=decision,
            audit_refs=audit_refs,
        )
        yield _terminal_commit(
            frozen=frozen,
            decision=decision,
            narrative=narrative,
            reason_code=reason_code,
            audit_refs=audit_refs,
            acceptance_effect_intents=_acceptance_effect_intents(terminal),
            budget_usage=_worker_budget_payload(
                frozen,
                observations=_chat_budget_observations(terminal),
                include_agent_tool_calls=False,
            ),
        )


def _chat_call_kwargs(ports: Any, *, frozen: FrozenNodeInput, input_pack: Mapping[str, Any]) -> dict[str, Any]:
    raw_body = _safe_mapping(input_pack.get("request_body"))
    request_body = _FrozenChatRequestBody(
        input_source="GOVERNED_TASK_RUNTIME",
        response_form=str(raw_body.get("response_form") or input_pack.get("response_form") or "text").strip(),
        reply_channel=None,
        reply_target=None,
        suppress_edge_tts=True,
        suppress_edge_vts=True,
        defer_effects_to_task_runtime=True,
        render_policy=_safe_mapping(raw_body.get("render_policy") or input_pack.get("render_policy")),
        surface_task_control={},
        attachments=tuple(_safe_sequence(raw_body.get("attachments") or input_pack.get("attachments"))),
        multimodal_observations=_safe_mapping(
            raw_body.get("multimodal_observations") or input_pack.get("multimodal_observations")
        ),
        attachment_summary=str(
            raw_body.get("attachment_summary") or input_pack.get("attachment_summary") or ""
        ).strip(),
    )
    message = _required_narrative(
        input_pack.get("sanitized_message") or input_pack.get("user_message") or frozen.objective,
        field_name="chat user_message",
    )
    trace_stage_meta = {
        "governed_frozen_node": True,
        "skill_route_type": "chat",
        "skill_route_reason": "governed_frozen_node",
        "graph_id": frozen.graph_id,
        "graph_revision": frozen.graph_revision,
        "node_id": frozen.node_id,
        "attempt_id": frozen.attempt_id,
        "pii_ingress_stats": _safe_mapping(input_pack.get("pii_ingress_stats")),
        "motive_state": _safe_mapping(input_pack.get("motive_state")),
    }
    guarded_budget = _governed_model_budget(frozen)
    if guarded_budget is not None:
        trace_stage_meta["governed_model_budget"] = guarded_budget
    frozen_intent = _safe_mapping(input_pack.get("intent_draft"))
    frozen_intent.update(
        {
            "scene_decision_v2": input_pack.get("scene_decision_v2"),
            "scene_identity_v2": input_pack.get("scene_identity_v2"),
        }
    )
    return {
        "ports": ports,
        "request_body": request_body,
        "route_reason": "governed_frozen_node",
        "request_id": frozen.request_id,
        "session_id": frozen.session_id,
        "person_id": frozen.principal_id,
        "sanitized_message": message,
        "emotion_context": _safe_mapping(
            input_pack.get("emotion_context")
            or input_pack.get("prepared_emotion_context")
        ),
        "emotion_snapshot": _safe_mapping(input_pack.get("emotion_snapshot")),
        "bond_summary": _safe_mapping(input_pack.get("bond_summary")),
        "retrieval_evidence_items": [
            _safe_mapping(item)
            for item in _safe_sequence(input_pack.get("retrieval_evidence_items"))
            if isinstance(item, Mapping)
        ],
        "retrieval_summary": _safe_mapping(input_pack.get("retrieval_summary")),
        "render_target_lang": str(
            input_pack.get("render_target_lang")
            or input_pack.get("prepared_render_target_lang")
            or "zh"
        ).strip() or "zh",
        "llm_runtime": _safe_mapping(input_pack.get("llm_runtime")) or None,
        "ts_ms": _non_negative_int(input_pack.get("ts_ms")),
        "intent_draft": frozen_intent,
        "trace_stage_meta": trace_stage_meta,
        "sub_agent_context": None,
        "runtime_capability_payload": None,
        # Passing an empty governor result is intentional: _build_chat_input only
        # manufactures delivery authority from an APPROVE decision.
        "governor_result": {},
    }


def _governed_model_budget(frozen: FrozenNodeInput) -> dict[str, Any] | None:
    """Derive one non-expandable model guard from frozen durable remaining."""

    budget = frozen.budget
    if not budget:
        # Direct construction is retained for narrow executor tests. Production
        # frozen workers reject an absent budget before reaching this adapter.
        return None
    remaining = _mapping_child(budget, "remaining")
    try:
        deadline_ms = int(remaining.get("time_ms"))
        retry_count = int(remaining.get("retry_count"))
        token_count = int(remaining.get("token_count"))
        model_cost_micros = int(remaining.get("model_cost_micros"))
    except (TypeError, ValueError) as exc:
        raise GovernedRouteExecutorError(
            GOVERNED_ROUTE_INPUT_INVALID,
            "frozen durable budget has invalid model limits",
        ) from exc
    max_provider_attempts = retry_count + 1
    max_output_tokens = token_count // max_provider_attempts
    if (
        deadline_ms <= 0
        or retry_count < 0
        or token_count <= 0
        or max_output_tokens <= 0
        or model_cost_micros <= 0
    ):
        raise GovernedRouteExecutorError(
            GOVERNED_ROUTE_INPUT_INVALID,
            "frozen durable budget cannot admit another model call",
        )
    try:
        guard = ModelBudgetGuard(
            budget_id=str(budget.get("budget_id") or ""),
            graph_id=frozen.graph_id,
            graph_revision=frozen.graph_revision,
            node_id=frozen.node_id,
            attempt_id=frozen.attempt_id,
            deadline_ms=deadline_ms,
            max_provider_attempts=max_provider_attempts,
            max_token_count=token_count,
            max_output_tokens=max_output_tokens,
            max_model_cost_micros=model_cost_micros,
        )
    except (TypeError, ValueError) as exc:
        raise GovernedRouteExecutorError(
            GOVERNED_ROUTE_INPUT_INVALID,
            "frozen durable budget cannot form a model guard",
        ) from exc
    return guard.as_dict()


def _assert_guarded_chat_terminal(
    *,
    frozen: FrozenNodeInput,
    decision: str,
    audit_refs: Mapping[str, Any],
) -> None:
    """Prevent fallback or synthetic approvals from bypassing model accounting."""

    expected_guard = _governed_model_budget(frozen)
    if expected_guard is None or decision != "APPROVE":
        return
    trace = _mapping_child(audit_refs, "trace_stage_meta")
    if _mapping_child(trace, "governed_model_budget") != expected_guard:
        raise GovernedRouteExecutorError(
            GOVERNED_ROUTE_BUDGET_MEASUREMENT_REQUIRED,
            "guarded chat approval did not preserve the frozen model budget guard",
        )
    evidence_gate = _mapping_child(trace, "surface_evidence_gate")
    if evidence_gate.get("status") == "OK":
        # This documented path never invokes a model and therefore has no
        # provider measurement to settle.
        return
    attempt = _mapping_child(trace, "surface_model_attempt")
    measurement = _mapping_child(attempt, "budget_measurement")
    attempt_ids = attempt.get("attempt_ids")
    if (
        attempt.get("success") is not True
        or not isinstance(attempt_ids, Sequence)
        or isinstance(attempt_ids, (str, bytes, bytearray))
        or not attempt_ids
        or not {"token_count", "model_cost_micros"}.issubset(measurement)
    ):
        raise GovernedRouteExecutorError(
            GOVERNED_ROUTE_BUDGET_MEASUREMENT_REQUIRED,
            "guarded chat approval lacks a complete surface model measurement",
        )


def _delegate_context(input_pack: Mapping[str, Any], *, frozen: FrozenNodeInput) -> dict[str, Any]:
    raw_context = _safe_mapping(input_pack.get("context"))
    for key in tuple(raw_context):
        normalized = str(key).strip().lower()
        if normalized in {"route", "route_type", "target_id", "planner", "planning", "replan"}:
            raw_context.pop(key, None)
    raw_context.update(
        {
            "governed_frozen_node": True,
            "graph_id": frozen.graph_id,
            "graph_revision": frozen.graph_revision,
            "node_id": frozen.node_id,
            "attempt_id": frozen.attempt_id,
            "input_refs": list(frozen.input_refs),
        }
    )
    return raw_context


def _project_outcome(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    keys = (
        "route_type",
        "target_id",
        "success",
        "decision",
        "result_text",
        "error_text",
        "reason_code",
        "payload",
        "audit_refs",
    )
    projected = {key: getattr(value, key) for key in keys if hasattr(value, key)}
    if not projected:
        raise GovernedRouteExecutorError(
            GOVERNED_ROUTE_OUTPUT_INVALID,
            "delegate port returned no execution outcome",
        )
    return projected


def _decision_from_outcome(outcome: Mapping[str, Any]) -> str:
    raw = str(outcome.get("decision") or "").strip().upper()
    if raw in {"APPROVE", "SUCCESS"}:
        return "APPROVE"
    if raw in {"REJECT", "FAILED", "FAILURE", "NO_REPLY"}:
        return "REJECT"
    if "success" not in outcome:
        raise GovernedRouteExecutorError(
            GOVERNED_ROUTE_OUTPUT_INVALID,
            "delegate outcome requires success or an explicit decision",
        )
    return "APPROVE" if outcome.get("success") is True else "REJECT"


def _outcome_narrative(outcome: Mapping[str, Any], *, decision: str) -> str:
    if decision == "APPROVE":
        value = outcome.get("result_text") or outcome.get("narrative")
    else:
        value = outcome.get("error_text") or outcome.get("result_text") or outcome.get("narrative")
    return _required_narrative(value, field_name="delegate terminal narrative")


def _outcome_reason_code(outcome: Mapping[str, Any], *, decision: str) -> str | None:
    explicit = str(outcome.get("reason_code") or "").strip()
    if explicit:
        return explicit
    if decision == "APPROVE":
        return None
    error = str(outcome.get("error_text") or "").strip()
    match = re.match(r"^([A-Z][A-Z0-9_]{2,127})(?::|\s|$)", error)
    return match.group(1) if match else "DELEGATE_EXECUTION_FAILED"


def _outcome_audit_refs(outcome: Mapping[str, Any]) -> dict[str, Any]:
    direct = _safe_mapping(outcome.get("audit_refs"))
    payload = _safe_mapping(outcome.get("payload"))
    direct.update(_safe_mapping(payload.get("audit_refs")))
    direct["governed_frozen_route"] = True
    direct["delivery_authorized"] = False
    return direct


def _terminal_commit(
    *,
    frozen: FrozenNodeInput,
    decision: str,
    narrative: str,
    reason_code: str | None,
    audit_refs: Mapping[str, Any],
    acceptance_effect_intents: Sequence[Mapping[str, Any]] = (),
    budget_usage: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    safe_audit_refs = _safe_mapping(audit_refs)
    safe_audit_refs["delivery_authorized"] = False
    event = {
        "phase": "COMMIT",
        "done": True,
        "decision": decision,
        "reason_code": reason_code,
        "narrative": narrative,
        "route_type": frozen.route,
        "target_id": frozen.target_id,
        "audit_refs": safe_audit_refs,
    }
    if acceptance_effect_intents:
        event["acceptance_effect_intents"] = [
            _json_copy(item) for item in acceptance_effect_intents
        ]
    if budget_usage is not None:
        event["budget_usage"] = _json_copy(budget_usage)
    return event


def _worker_budget_payload(
    frozen: FrozenNodeInput,
    *,
    observations: Any,
    include_agent_tool_calls: bool,
) -> dict[str, Any] | None:
    usage = build_worker_budget_usage(
        usage_id=stable_worker_usage_id(
            graph_id=frozen.graph_id,
            graph_revision=frozen.graph_revision,
            node_id=frozen.node_id,
            attempt_id=frozen.attempt_id,
            route=frozen.route,
        ),
        observations=observations,
        include_agent_tool_calls=include_agent_tool_calls,
    )
    return usage.to_payload() if usage is not None else None


def _chat_budget_observations(terminal: Mapping[str, Any]) -> dict[str, Any]:
    """Project only server-owned model measurements from a chat terminal.

    The terminal also carries memory, evidence and user-authored content.  A
    recursive scan of that payload is both unbounded and lets content imitate a
    canonical budget measurement.  The surface runtime has two explicit model
    trace slots; only measurements from those slots cross this boundary.
    """

    audit_refs = _mapping_child(terminal, "audit_refs")
    trace_stage_meta = _mapping_child(audit_refs, "trace_stage_meta")
    measurements: list[dict[str, Any]] = []
    _append_model_budget_measurement(
        measurements,
        _mapping_child(trace_stage_meta, "surface_model_attempt"),
    )
    spark_judge = _mapping_child(trace_stage_meta, "codex_spark_reply_judge")
    _append_model_budget_measurement(
        measurements,
        _mapping_child(spark_judge, "model_attempt"),
    )
    return {"model_budget_measurements": measurements}


def _delegate_budget_observations(audit_refs: Mapping[str, Any]) -> dict[str, Any]:
    """Project explicit sub-agent measurements without scanning agent context."""

    measurements: list[dict[str, Any]] = []
    for trace_key in ("intent_agent_trace", "repair_agent_trace"):
        trace = _mapping_child(audit_refs, trace_key)
        _append_model_budget_measurement(
            measurements,
            _mapping_child(trace, "model_attempt"),
        )

    projected: dict[str, Any] = {"model_budget_measurements": measurements}
    agent_trace = audit_refs.get("agent_execution_trace")
    if (
        isinstance(agent_trace, Mapping)
        and agent_trace.get("schema_version") == "nle.trace.redacted.v1"
    ):
        # This is an internal, redacted execution trace.  Do not accept a trace
        # from agent_context or subgraph payloads, which can include user data.
        projected["agent_execution_trace"] = dict(agent_trace)
    return projected


def _append_model_budget_measurement(
    target: list[dict[str, Any]],
    model_attempt: Mapping[str, Any],
) -> None:
    measurement = model_attempt.get("budget_measurement")
    if isinstance(measurement, Mapping):
        # Shallow-copy only the typed measurement.  The budget contract below
        # remains responsible for strict fields, references and replay drift.
        target.append(dict(measurement))


def _mapping_child(value: Any, key: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    child = value.get(key)
    return child if isinstance(child, Mapping) else {}


def _acceptance_effect_intents(event: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    raw = event.get("acceptance_effect_intents")
    if raw in (None, (), []):
        return ()
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes, bytearray)):
        raise GovernedRouteExecutorError(
            GOVERNED_ROUTE_OUTPUT_INVALID,
            "chat acceptance effects must be a sequence",
        )
    normalized: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, Mapping):
            raise GovernedRouteExecutorError(
                GOVERNED_ROUTE_OUTPUT_INVALID,
                "chat acceptance effect must be a mapping",
            )
        effect_type = str(item.get("effect_type") or "").strip()
        scope = str(item.get("commit_scope") or "").strip().upper()
        payload = item.get("payload")
        if (
            effect_type != "life.foreground_completed"
            or scope != "NODE"
            or not isinstance(payload, Mapping)
        ):
            raise GovernedRouteExecutorError(
                GOVERNED_ROUTE_OUTPUT_INVALID,
                "frozen chat may defer only NODE-scoped life.foreground_completed",
            )
        normalized.append(
            {
                "effect_type": effect_type,
                "commit_scope": scope,
                "payload": _json_copy(payload),
            }
        )
    return tuple(normalized)


def _normalize_terminal_decision(event: Mapping[str, Any]) -> str:
    decision = str(event.get("decision") or "").strip().upper()
    if decision in {"APPROVE", "SUCCESS"}:
        return "APPROVE"
    if decision in {"REJECT", "FAILED", "FAILURE", "NO_REPLY"}:
        return "REJECT"
    if event.get("success") is True:
        return "APPROVE"
    if event.get("success") is False:
        return "REJECT"
    raise GovernedRouteExecutorError(
        GOVERNED_ROUTE_OUTPUT_INVALID,
        "chat terminal requires APPROVE or REJECT decision",
    )


def _terminal_reason_code(event: Mapping[str, Any], *, decision: str) -> str | None:
    value = str(event.get("reason_code") or "").strip()
    if value:
        return value
    return None if decision == "APPROVE" else "CHAT_EXECUTION_FAILED"


def _is_terminal(event: Mapping[str, Any]) -> bool:
    return event.get("done") is True or str(event.get("phase") or "").strip().upper() == "COMMIT"


def _buffer_narrative(
    event: Mapping[str, Any],
    render_fragments: list[str],
    sentence_fragments: list[str],
) -> None:
    phase = str(event.get("phase") or "").strip().upper()
    chunk = str(event.get("chunk") or event.get("delta") or "")
    sentence = str(event.get("sentence") or "")
    if chunk and phase in {"", "DELTA", "RENDER", "TOKEN"}:
        render_fragments.append(chunk)
    if sentence and phase == "SENTENCE":
        sentence_fragments.append(sentence.strip())


def _split_complete_sse_frames(value: str) -> tuple[tuple[str, ...], str]:
    frames: list[str] = []
    remainder = value
    while "\n\n" in remainder:
        frame, remainder = remainder.split("\n\n", 1)
        if frame.strip():
            frames.append(frame)
    return tuple(frames), remainder


def _decode_event_frame(frame: str) -> tuple[dict[str, Any], ...]:
    text = str(frame or "").strip()
    if not text:
        return ()
    if text.startswith("{"):
        data = text
    else:
        data_lines = [
            line[5:].lstrip()
            for line in text.split("\n")
            if line.startswith("data:")
        ]
        if not data_lines:
            return ()
        data = "\n".join(data_lines).strip()
    if not data or data == "[DONE]":
        return ()
    try:
        loaded = json.loads(data)
    except (TypeError, ValueError) as exc:
        raise GovernedRouteExecutorError(
            GOVERNED_ROUTE_OUTPUT_INVALID,
            "chat stream emitted malformed SSE JSON",
        ) from exc
    if not isinstance(loaded, Mapping):
        raise GovernedRouteExecutorError(
            GOVERNED_ROUTE_OUTPUT_INVALID,
            "chat SSE data must be a mapping",
        )
    return (dict(loaded),)


def _assert_no_route_drift(event: Mapping[str, Any], *, frozen: FrozenNodeInput) -> None:
    supplied_route = str(event.get("route_type") or event.get("route") or "").strip().lower()
    supplied_target = str(event.get("target_id") or "").strip()
    if supplied_route and supplied_route != frozen.route:
        raise GovernedRouteExecutorError(
            GOVERNED_ROUTE_ROUTE_DRIFT,
            f"frozen {frozen.route} executor returned route {supplied_route}",
        )
    if supplied_target and supplied_target != frozen.target_id:
        raise GovernedRouteExecutorError(
            GOVERNED_ROUTE_ROUTE_DRIFT,
            f"frozen executor returned target {supplied_target}",
        )


def _require_frozen_route(frozen: FrozenNodeInput, *, expected: str) -> None:
    if not isinstance(frozen, FrozenNodeInput):
        raise GovernedRouteExecutorError(
            GOVERNED_ROUTE_INPUT_INVALID,
            "FrozenNodeInput is required",
        )
    if frozen.route != expected or not frozen.target_id:
        raise GovernedRouteExecutorError(
            GOVERNED_ROUTE_INPUT_INVALID,
            f"executor requires frozen {expected}:target input",
        )


def _input_pack(frozen: FrozenNodeInput) -> dict[str, Any]:
    pack = frozen.input_pack
    if not isinstance(pack, Mapping):
        raise GovernedRouteExecutorError(
            GOVERNED_ROUTE_INPUT_INVALID,
            "frozen input_pack must be a mapping",
        )
    return _safe_mapping(pack)


def _required_narrative(value: Any, *, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise GovernedRouteExecutorError(
            GOVERNED_ROUTE_OUTPUT_INVALID,
            f"{field_name} is required",
        )
    return text


def _safe_mapping(value: Any) -> dict[str, Any]:
    raw = dict(value) if isinstance(value, Mapping) else {}
    sanitized = _scrub_authority(raw)
    return dict(sanitized) if isinstance(sanitized, Mapping) else {}


def _safe_sequence(value: Any) -> tuple[Any, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return ()
    sanitized = _scrub_authority(list(value))
    return tuple(sanitized) if isinstance(sanitized, list) else ()


def _json_copy(value: Any) -> Any:
    try:
        return json.loads(
            json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
        )
    except (TypeError, ValueError) as exc:
        raise GovernedRouteExecutorError(
            GOVERNED_ROUTE_OUTPUT_INVALID,
            "chat acceptance effect payload must be canonical JSON",
        ) from exc


def _scrub_authority(value: Any) -> Any:
    if isinstance(value, Mapping):
        cleaned: dict[str, Any] = {}
        for raw_key, child in value.items():
            key = str(raw_key)
            normalized = key.strip().lower()
            if (
                "delivery_author" in normalized
                or normalized in {
                    "governor_result",
                    "governor_grant_ref",
                    # Sub-agent context exposes a safe authorization summary,
                    # but authority metadata still does not belong in the
                    # public worker projection.  Counts remain available in
                    # the surrounding runtime-event summary.
                    "context_authorization",
                    "delivery_claim",
                    "delivery_token",
                    "approval_token",
                }
            ):
                continue
            cleaned[key] = _scrub_authority(child)
        return cleaned
    if isinstance(value, list):
        return [_scrub_authority(child) for child in value]
    if isinstance(value, tuple):
        return [_scrub_authority(child) for child in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise GovernedRouteExecutorError(
        GOVERNED_ROUTE_INPUT_INVALID,
        "frozen route payload must remain JSON-safe",
    )


def _non_negative_int(value: Any) -> int:
    try:
        parsed = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return max(0, parsed)


__all__ = [
    "FrozenChatRouteExecutor",
    "FrozenDelegateRouteExecutor",
    "GOVERNED_ROUTE_INPUT_INVALID",
    "GOVERNED_ROUTE_OUTPUT_INVALID",
    "GOVERNED_ROUTE_ROUTE_DRIFT",
    "GovernedRouteExecutorError",
]
