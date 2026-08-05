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
import time
from typing import Any, Protocol

from host_brain.model_runtime.governed_budget import (
    GovernedModelBudgetError,
    build_governed_model_budget_guard,
)
from host_brain.task_runtime.governed_workers import FrozenNodeInput
from host_brain.contracts.agent_runtime.worker_budget_usage import (
    WorkerBudgetUsage,
    build_worker_budget_usage,
    stable_worker_usage_id,
)
from host_brain.contracts.agent_runtime.delegate_usage_attestation import (
    ReadonlyDelegateUsageAttestationError,
    validate_readonly_delegate_usage_attestation,
)
from host_brain.contracts.agent_runtime.delegate_target_policy import (
    GOVERNED_DELEGATE_BUDGET_MODE,
    GOVERNED_MODEL_DELEGATE_BUDGET_MODE,
    GovernedDelegateTargetPolicyError,
    validate_governed_delegate_target_policy,
)
from host_brain.contracts.agent_runtime.node_input_attestation import (
    NodeInputArtifactAttestationError,
    build_node_input_artifact_attestation,
)
from host_brain.persistence.task_chat_delivery_store import TaskChatDeliveryStore
from host_brain.task_runtime.chat_delivery_authority import (
    TaskBoundChatDeliveryAuthority,
)
from host_brain.digital_life.governed_companion_presentation import (
    GovernedCompanionPresentationError,
    build_governed_companion_expression_intents,
)


GOVERNED_ROUTE_INPUT_INVALID = "GOVERNED_ROUTE_INPUT_INVALID"
GOVERNED_ROUTE_OUTPUT_INVALID = "GOVERNED_ROUTE_OUTPUT_INVALID"
GOVERNED_ROUTE_ROUTE_DRIFT = "GOVERNED_ROUTE_ROUTE_DRIFT"
GOVERNED_ROUTE_BUDGET_MEASUREMENT_REQUIRED = "GOVERNED_ROUTE_BUDGET_MEASUREMENT_REQUIRED"


from host_brain.app.governed_route_support import (
    GOVERNED_ROUTE_INPUT_INVALID,
    GOVERNED_ROUTE_OUTPUT_INVALID,
    GOVERNED_ROUTE_ROUTE_DRIFT,
    GovernedRouteExecutorError,
    _assert_no_route_drift,
    _buffer_narrative,
    _decode_event_frame,
    _input_pack,
    _json_copy,
    _non_negative_int,
    _require_frozen_route,
    _required_narrative,
    _safe_mapping,
    _safe_sequence,
    _scrub_authority,
    _split_complete_sse_frames,
    _strict_non_negative_int,
    _strict_positive_int,
)
from host_brain.app.governed_expression_delivery import (
    _acceptance_effect_intents,
    _assert_expression_delivery_receipt,
    _assert_expression_delivery_scope,
    _assert_next_staged_sentence,
    _assert_task_bound_expression_delivery,
    _expression_delivery_items,
    _expression_delivery_key,
    _frozen_relationship_subject_id,
    _is_terminal,
    _mapping_child,
    _normalize_terminal_decision,
    _task_bound_staged_sentence_event,
    _terminal_reason_code,
)


class DelegateDispatchPort(Protocol):
    def __call__(self, **kwargs: Any) -> Any: ...


class ChatStreamPort(Protocol):
    def __call__(self, **kwargs: Any) -> AsyncIterable[Any] | Any: ...


@dataclass(frozen=True, slots=True)
class _FrozenChatRequestBody:
    """Authority-free request projection consumed by ``_build_chat_input``."""

    input_source: str
    workspace_id: str
    relationship_subject_id: str
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
    governed_delivery_authority: Any | None


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
        _assert_delegate_effect_free_outcome(projected)

        decision = _decision_from_outcome(projected)
        narrative = _outcome_narrative(projected, decision=decision)
        reason_code = _outcome_reason_code(projected, decision=decision)
        audit_refs = _outcome_audit_refs(projected)
        target_policy = _assert_delegate_target_policy(
            frozen=frozen,
            input_pack=input_pack,
            audit_refs=audit_refs,
        )
        acceptance_effect_intents = _delegate_companion_expression_intents(
            frozen=frozen,
            input_pack=input_pack,
            narrative=narrative,
            decision=decision,
        )
        yield _terminal_commit(
            frozen=frozen,
            decision=decision,
            narrative=narrative,
            reason_code=reason_code,
            audit_refs=audit_refs,
            acceptance_effect_intents=acceptance_effect_intents,
            budget_usage=_delegate_budget_payload(
                frozen,
                target_policy=target_policy,
                audit_refs=audit_refs,
            ),
        )


class FrozenChatRouteExecutor:
    """Execute a frozen Chat route and forward only staged sentence receipts."""

    def __init__(
        self,
        *,
        ports: Any,
        stream_chat_with_context: ChatStreamPort,
        chat_delivery_store: TaskChatDeliveryStore | None = None,
    ) -> None:
        if ports is None:
            raise ValueError("ports is required")
        if not callable(stream_chat_with_context):
            raise ValueError("stream_chat_with_context must be callable")
        self._ports = ports
        self._stream_chat = stream_chat_with_context
        self._chat_delivery_store = chat_delivery_store

    async def execute_stream(self, frozen: FrozenNodeInput) -> AsyncIterator[Mapping[str, Any]]:
        _require_frozen_route(frozen, expected="chat")
        input_pack = _input_pack(frozen)
        stream = self._stream_chat(
            **_chat_call_kwargs(
                self._ports,
                frozen=frozen,
                input_pack=input_pack,
                chat_delivery_store=self._chat_delivery_store,
            )
        )
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
        visible_sentences: list[dict[str, Any]] = []
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
                staged_sentence = _task_bound_staged_sentence_event(event, frozen=frozen)
                if staged_sentence is not None:
                    _assert_next_staged_sentence(
                        staged_sentence,
                        prior=visible_sentences,
                    )
                    visible_sentences.append(staged_sentence)
                    # This is the sole pre-Acceptance event that may leave a
                    # governed worker: it was committed to the immutable Task
                    # delivery ledger before Surface emitted the SSE frame.
                    yield staged_sentence
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
        visible_narrative = "".join(
            str(item["sentence"]) for item in visible_sentences
        ).strip()
        if visible_sentences and decision != "APPROVE":
            raise GovernedRouteExecutorError(
                GOVERNED_ROUTE_OUTPUT_INVALID,
                "a visible task-bound sentence stream requires an approved terminal result",
            )
        if visible_narrative:
            if narrative and narrative != visible_narrative:
                raise GovernedRouteExecutorError(
                    GOVERNED_ROUTE_OUTPUT_INVALID,
                    "terminal narrative differs from already-visible staged sentences",
                )
            narrative = visible_narrative
        if not narrative:
            narrative = "".join(render_fragments).strip()
        if not narrative:
            narrative = "\n".join(sentence_fragments).strip()

        # A Surface runtime can fail before it exposes any text at all.  That
        # is a rejected task result, not an unknown visible side effect: no
        # sentence receipt exists for a client to reconcile and forcing a
        # placeholder narrative would falsely turn a transport/runtime fault
        # into companion speech.  Keep approvals strict; an approved Chat
        # node must still have either a staged sentence or a terminal
        # narrative.
        no_visible_reply = not narrative and decision == "REJECT" and not visible_sentences
        if not no_visible_reply:
            narrative = _required_narrative(
                narrative,
                field_name="chat terminal narrative",
            )
        reason_code = _terminal_reason_code(terminal, decision=decision)
        audit_refs = _safe_mapping(terminal.get("audit_refs"))
        audit_refs.update(
            {
                "governed_frozen_route": True,
                "source_event_count": event_count,
                # DL-05 removed the unconditional channel suppression.  The
                # worker is still speculative (effects remain deferred until
                # Task Acceptance), but the accepted foreground command now
                # carries the same frozen expression protocol as native Chat.
                "edge_tts_suppressed": False,
                "edge_vts_suppressed": False,
                "affective_delivery_mode": "deferred_to_task_acceptance",
                "delivery_authorized": False,
                "task_bound_sentence_delivery": {
                    "status": (
                        "VISIBLE_BEFORE_ACCEPTANCE"
                        if visible_sentences
                        else "NO_VISIBLE_REPLY"
                        if no_visible_reply
                        else "NOT_USED"
                    ),
                    "visible_sentence_count": len(visible_sentences),
                    "delivery_ids": [
                        str(item["delivery_id"]) for item in visible_sentences
                    ],
                },
            }
        )
        _assert_guarded_chat_terminal(
            frozen=frozen,
            decision=decision,
            audit_refs=audit_refs,
        )
        acceptance_effect_intents = _acceptance_effect_intents(terminal)
        _assert_task_bound_expression_delivery(
            acceptance_effect_intents,
            visible_sentences=visible_sentences,
            frozen=frozen,
        )
        yield _terminal_commit(
            frozen=frozen,
            decision=decision,
            narrative=narrative,
            reason_code=reason_code,
            audit_refs=audit_refs,
            acceptance_effect_intents=acceptance_effect_intents,
            budget_usage=_worker_budget_payload(
                frozen,
                observations=_chat_budget_observations(terminal),
                include_agent_tool_calls=False,
            ),
        )


def _chat_call_kwargs(
    ports: Any,
    *,
    frozen: FrozenNodeInput,
    input_pack: Mapping[str, Any],
    chat_delivery_store: TaskChatDeliveryStore | None = None,
) -> dict[str, Any]:
    raw_body = _safe_mapping(input_pack.get("request_body"))
    (
        workspace_id,
        relationship_subject_id,
        scope_authority,
    ) = _frozen_chat_delivery_scope(input_pack=input_pack, raw_body=raw_body)
    governed_delivery_authority = (
        TaskBoundChatDeliveryAuthority(
            store=chat_delivery_store,
            frozen=frozen,
            workspace_id=workspace_id,
            relationship_subject_id=relationship_subject_id,
        )
        if chat_delivery_store is not None and workspace_id and relationship_subject_id
        else None
    )
    request_body = _FrozenChatRequestBody(
        input_source="GOVERNED_TASK_RUNTIME",
        workspace_id=workspace_id,
        relationship_subject_id=relationship_subject_id,
        response_form=str(raw_body.get("response_form") or input_pack.get("response_form") or "text").strip(),
        reply_channel=None,
        reply_target=None,
        suppress_edge_tts=False,
        suppress_edge_vts=False,
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
        governed_delivery_authority=governed_delivery_authority,
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
        "governed_task_chat_delivery": {
            "status": (
                "TASK_BOUND_VISIBILITY_READY"
                if governed_delivery_authority is not None
                else "BUFFERED_NO_TASK_DELIVERY_SCOPE"
            ),
            "workspace_id": workspace_id or None,
            "relationship_subject_id": relationship_subject_id or None,
            "scope_authority": scope_authority,
        },
        "pii_ingress_stats": _safe_mapping(input_pack.get("pii_ingress_stats")),
        "motive_state": _safe_mapping(input_pack.get("motive_state")),
    }
    accepted_artifact_items: list[dict[str, Any]] = []
    input_artifacts = frozen.input_artifacts
    if input_artifacts:
        try:
            artifact_attestation = build_node_input_artifact_attestation(
                input_artifacts
            )
        except NodeInputArtifactAttestationError as exc:
            raise GovernedRouteExecutorError(
                GOVERNED_ROUTE_INPUT_INVALID,
                str(exc),
            ) from exc
        trace_stage_meta["node_input_artifact_attestation"] = (
            artifact_attestation
        )
        accepted_artifact_items = _accepted_artifact_evidence_items(
            input_artifacts
        )
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
        # The Task node principal is the authenticated caller of the command;
        # it is not automatically the relationship subject of a companion
        # turn.  Surface, AffectiveTurnPlan and delivery receipts must all use
        # the one subject frozen by Runtime Context instead.
        "person_id": relationship_subject_id or frozen.principal_id,
        "sanitized_message": message,
        "emotion_context": _safe_mapping(
            input_pack.get("emotion_context")
            or input_pack.get("prepared_emotion_context")
        ),
        "emotion_snapshot": _safe_mapping(input_pack.get("emotion_snapshot")),
        "bond_summary": _safe_mapping(input_pack.get("bond_summary")),
        # A data-dependent Plan synthesis node receives only its accepted
        # predecessor artifacts as retrieval evidence.  Ordinary memory
        # retrieval is intentionally excluded so the declared dependency
        # contract cannot be diluted by unrelated context.
        "retrieval_evidence_items": (
            accepted_artifact_items
            if accepted_artifact_items
            else [
                _safe_mapping(item)
                for item in _safe_sequence(
                    input_pack.get("retrieval_evidence_items")
                )
                if isinstance(item, Mapping)
            ]
        ),
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


def _frozen_chat_delivery_scope(
    *,
    input_pack: Mapping[str, Any],
    raw_body: Mapping[str, Any],
) -> tuple[str, str, str]:
    """Resolve Chat delivery scope from the immutable frozen authority.

    ``RuntimeContextApplicationService`` deliberately derives workspace and
    subject from Secure Ingress rather than a caller supplied ``ChatRequest``.
    Once that context is present, accepting the request-body values here would
    let ordinary Chat fall back to an unscoped buffer (and could confuse a
    transport principal with the relationship subject).  Historic graphs that
    predate Runtime Context remain readable during the rollback window, but
    cannot claim canonical task-bound scope.
    """

    context_schema = str(input_pack.get("runtime_context_schema") or "").strip()
    if context_schema:
        if context_schema != "natsume.runtime-context.request.v1":
            raise GovernedRouteExecutorError(
                GOVERNED_ROUTE_INPUT_INVALID,
                "frozen Chat runtime context schema is invalid",
            )
        workspace_id = str(input_pack.get("workspace_id") or "").strip()
        relationship_subject_id = str(input_pack.get("subject_id") or "").strip()
        if not workspace_id or not relationship_subject_id:
            raise GovernedRouteExecutorError(
                GOVERNED_ROUTE_INPUT_INVALID,
                "frozen Chat runtime context has no canonical workspace or subject",
            )
        return (
            workspace_id,
            relationship_subject_id,
            "CANONICAL_RUNTIME_CONTEXT",
        )

    # Compatibility only: old accepted graphs have no typed Runtime Context.
    # They may finish/replay through their original request projection, but new
    # Host Input always takes the canonical branch above.
    workspace_id = str(
        raw_body.get("workspace_id") or input_pack.get("workspace_id") or ""
    ).strip()
    relationship_subject_id = str(
        raw_body.get("relationship_subject_id")
        or input_pack.get("relationship_subject_id")
        or ""
    ).strip()
    return workspace_id, relationship_subject_id, "LEGACY_FROZEN_REQUEST"


def _accepted_artifact_evidence_items(
    artifacts: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    for artifact in artifacts:
        payload = artifact.get("payload")
        if not isinstance(payload, Mapping):
            raise GovernedRouteExecutorError(
                GOVERNED_ROUTE_INPUT_INVALID,
                "accepted input artifact has no public payload",
            )
        snippet = str(
            payload.get("narrative")
            or payload.get("result_text")
            or payload.get("summary")
            or ""
        ).strip()
        if not snippet:
            try:
                snippet = json.dumps(
                    dict(payload),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                )
            except (TypeError, ValueError) as exc:
                raise GovernedRouteExecutorError(
                    GOVERNED_ROUTE_INPUT_INVALID,
                    "accepted input artifact payload is not finite JSON",
                ) from exc
        values.append(
            {
                "source_ref": str(artifact.get("artifact_ref") or "").strip(),
                "evidence_ref": str(
                    artifact.get("receipt_ref") or ""
                ).strip(),
                "snippet": snippet[:4_000],
                "score": 1.0,
                "confidence": 1.0,
                "store_id": "task_result_artifact",
                "metadata": {
                    "store_id": "task_result_artifact",
                    "payload_sha256": str(
                        artifact.get("payload_sha256") or ""
                    ).strip(),
                },
            }
        )
    return values


def _governed_model_budget(frozen: FrozenNodeInput) -> dict[str, Any] | None:
    """Derive one non-expandable model guard from frozen durable remaining."""

    budget = frozen.budget
    if not budget:
        # Direct construction is retained for narrow executor tests. Production
        # frozen workers reject an absent budget before reaching this adapter.
        return None
    try:
        guard = build_governed_model_budget_guard(
            budget=budget,
            graph_id=frozen.graph_id,
            graph_revision=frozen.graph_revision,
            node_id=frozen.node_id,
            attempt_id=frozen.attempt_id,
            # Ordinary personal conversation providers commonly attest exact
            # token usage but do not return a per-request currency charge.
            # Keep token/output/time enforcement on the critical path without
            # fabricating a zero-cost provider fact.  Agent/Skill builders keep
            # the strict default and still require model_cost_micros.
            require_model_cost=False,
        )
    except GovernedModelBudgetError as exc:
        raise GovernedRouteExecutorError(
            GOVERNED_ROUTE_INPUT_INVALID,
            exc.detail,
        ) from exc
    return guard.as_dict()


def _assert_guarded_chat_terminal(
    *,
    frozen: FrozenNodeInput,
    decision: str,
    audit_refs: Mapping[str, Any],
) -> None:
    """Prevent fallback or synthetic approvals from bypassing model accounting."""

    trace = _mapping_child(audit_refs, "trace_stage_meta")
    if "codex_spark_reply_judge" in trace:
        raise GovernedRouteExecutorError(
            GOVERNED_ROUTE_BUDGET_MEASUREMENT_REQUIRED,
            "chat terminal contains a retired secondary model trace",
        )
    expected_guard = _governed_model_budget(frozen)
    if expected_guard is None or decision != "APPROVE":
        return
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
        or "token_count" not in measurement
    ):
        raise GovernedRouteExecutorError(
            GOVERNED_ROUTE_BUDGET_MEASUREMENT_REQUIRED,
            "guarded chat approval lacks a provider token measurement",
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


def _delegate_companion_expression_intents(
    *,
    frozen: FrozenNodeInput,
    input_pack: Mapping[str, Any],
    narrative: str,
    decision: str,
) -> tuple[dict[str, Any], ...]:
    """Freeze the companion presentation for an accepted user-facing Delegate.

    Delegate targets remain read-only and never receive delivery authority.
    Their accepted text still needs the same Digital Life presentation boundary
    as native Chat, otherwise a governed translation or research answer silently
    drops TTS and Avatar.  The plan is derived only from the immutable canonical
    AffectFusion hand-off already stored in the WorkOrder; caller emotion fields
    and the delegate result cannot rewrite Natsume's self-affect.
    """

    if decision != "APPROVE" or not narrative:
        return ()
    request_body = _safe_mapping(input_pack.get("request_body"))
    source = str(request_body.get("input_source") or "").strip().upper()
    if source not in {"CHAT", "GATEWAY", "HOST_INPUT", "WEB"}:
        return ()
    if bool(request_body.get("suppress_edge_tts")) or bool(
        request_body.get("suppress_edge_vts")
    ):
        return ()
    context_schema = str(input_pack.get("runtime_context_schema") or "").strip()
    if context_schema != "natsume.runtime-context.request.v1":
        return ()
    workspace_id = str(input_pack.get("workspace_id") or "").strip()
    relationship_subject_id = _frozen_relationship_subject_id(frozen)
    try:
        return build_governed_companion_expression_intents(
            request_id=frozen.request_id,
            session_id=frozen.session_id,
            workspace_id=workspace_id,
            relationship_subject_id=relationship_subject_id,
            runtime_context_schema=context_schema,
            input_source=str(request_body.get("input_source") or ""),
            suppress_edge_tts=bool(request_body.get("suppress_edge_tts")),
            suppress_edge_vts=bool(request_body.get("suppress_edge_vts")),
            emotion_context=_safe_mapping(
                input_pack.get("prepared_emotion_context")
                or input_pack.get("emotion_context")
            ),
            narrative=narrative,
            decision=decision,
            task_mode="technical_help",
            created_at_ms=max(0, int(time.time() * 1000)),
            metadata_source="governed_delegate_companion_presentation",
        )
    except GovernedCompanionPresentationError as exc:
        raise GovernedRouteExecutorError(
            GOVERNED_ROUTE_INPUT_INVALID,
            str(exc),
        ) from exc


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


def _assert_delegate_effect_free_outcome(outcome: Mapping[str, Any]) -> None:
    nested = outcome.get("payload")
    direct_audit = outcome.get("audit_refs")
    nested_audit = (
        nested.get("audit_refs")
        if isinstance(nested, Mapping)
        else None
    )
    for value in (
        dict(outcome),
        dict(nested) if isinstance(nested, Mapping) else {},
        dict(direct_audit) if isinstance(direct_audit, Mapping) else {},
        dict(nested_audit) if isinstance(nested_audit, Mapping) else {},
    ):
        side_effects = value.get("side_effects_executed")
        delivery = value.get("delivery_authorized")
        if side_effects is not None and side_effects is not False:
            raise GovernedRouteExecutorError(
                GOVERNED_ROUTE_OUTPUT_INVALID,
                "frozen Delegate claimed an executed side effect",
            )
        if delivery is not None and delivery is not False:
            raise GovernedRouteExecutorError(
                GOVERNED_ROUTE_OUTPUT_INVALID,
                "frozen Delegate claimed delivery authority",
            )
        if any(
            value.get(key) not in (None, (), [])
            for key in (
                "acceptance_effect_intents",
                "effect_intents",
                "outbox",
                "outbox_messages",
            )
        ):
            raise GovernedRouteExecutorError(
                GOVERNED_ROUTE_OUTPUT_INVALID,
                "frozen Delegate returned an effect or Outbox descriptor",
            )


def _assert_delegate_target_policy(
    *,
    frozen: FrozenNodeInput,
    input_pack: Mapping[str, Any],
    audit_refs: Mapping[str, Any],
) -> dict[str, Any]:
    try:
        policy = validate_governed_delegate_target_policy(
            _safe_mapping(input_pack.get("delegate_target_policy"))
        )
    except GovernedDelegateTargetPolicyError as exc:
        raise GovernedRouteExecutorError(
            GOVERNED_ROUTE_ROUTE_DRIFT,
            exc.detail,
        ) from exc
    if policy["target_id"] != frozen.target_id:
        raise GovernedRouteExecutorError(
            GOVERNED_ROUTE_ROUTE_DRIFT,
            "frozen Delegate target policy differs from its worker route",
        )
    audit_expected = {
        "delegate_target_policy_ref": policy["policy_ref"],
        "delegate_adapter": policy["adapter_id"],
        "delegate_execution_mode": policy["execution_mode"],
        "delegate_review_mode": policy["review_mode"],
        "delegate_budget_mode": policy["budget_mode"],
        "delegate_effect_mode": policy["effect_mode"],
        "side_effects_executed": False,
    }
    if {key: audit_refs.get(key) for key in audit_expected} != audit_expected:
        raise GovernedRouteExecutorError(
            GOVERNED_ROUTE_ROUTE_DRIFT,
            "Delegate execution evidence differs from its admitted target policy",
        )
    return policy


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
    canonical budget measurement.  The Surface primary model attempt is the
    only model authority admitted for a frozen Chat node.
    """

    audit_refs = _mapping_child(terminal, "audit_refs")
    trace_stage_meta = _mapping_child(audit_refs, "trace_stage_meta")
    measurements: list[dict[str, Any]] = []
    _append_model_budget_measurement(
        measurements,
        _mapping_child(trace_stage_meta, "surface_model_attempt"),
    )
    return {"model_budget_measurements": measurements}


def _delegate_budget_payload(
    frozen: FrozenNodeInput,
    *,
    target_policy: Mapping[str, Any],
    audit_refs: Mapping[str, Any],
) -> dict[str, Any]:
    budget_mode = str(target_policy.get("budget_mode") or "").strip()
    if budget_mode == GOVERNED_DELEGATE_BUDGET_MODE:
        return _readonly_delegate_budget_payload(
            frozen,
            audit_refs=audit_refs,
        )
    if budget_mode == GOVERNED_MODEL_DELEGATE_BUDGET_MODE:
        return _model_backed_delegate_budget_payload(
            frozen,
            audit_refs=audit_refs,
        )
    raise GovernedRouteExecutorError(
        GOVERNED_ROUTE_BUDGET_MEASUREMENT_REQUIRED,
        "Delegate target policy has no supported budget measurement mode",
    )


def _readonly_delegate_budget_payload(
    frozen: FrozenNodeInput,
    *,
    audit_refs: Mapping[str, Any],
) -> dict[str, Any]:
    """Require a task-bound exact-zero proof from the registered Delegate.

    The production Delegate does not receive a model, tool, nested-dispatch or
    effect port.  Missing observations therefore cannot silently mean zero:
    the composition-owned dispatcher must attest that exact port-free boundary
    and bind it to this command/graph/node/attempt/grant tuple.
    """

    raw = audit_refs.get("delegate_runtime_usage_attestation")
    try:
        attestation = validate_readonly_delegate_usage_attestation(
            raw if isinstance(raw, Mapping) else {},
        )
    except ReadonlyDelegateUsageAttestationError as exc:
        raise GovernedRouteExecutorError(
            GOVERNED_ROUTE_BUDGET_MEASUREMENT_REQUIRED,
            f"delegate runtime usage attestation is invalid: {exc}",
        ) from exc
    expected = {
        "command_id": frozen.command_id,
        "graph_id": frozen.graph_id,
        "graph_revision": frozen.graph_revision,
        "node_id": frozen.node_id,
        "attempt_id": frozen.attempt_id,
        "node_grant_ref": frozen.node_grant_ref,
        "target_id": frozen.target_id,
        "adapter_id": str(audit_refs.get("delegate_adapter") or "").strip(),
        "invocation_ref": str(
            audit_refs.get("delegate_invocation_ref") or ""
        ).strip(),
        "execution_mode": str(
            audit_refs.get("delegate_execution_mode") or ""
        ).strip(),
    }
    if {key: attestation.get(key) for key in expected} != expected:
        raise GovernedRouteExecutorError(
            GOVERNED_ROUTE_BUDGET_MEASUREMENT_REQUIRED,
            "delegate runtime usage attestation drifted from frozen authority",
        )
    return WorkerBudgetUsage(
        usage_id=stable_worker_usage_id(
            graph_id=frozen.graph_id,
            graph_revision=frozen.graph_revision,
            node_id=frozen.node_id,
            attempt_id=frozen.attempt_id,
            route=frozen.route,
        ),
        measurements={
            str(key): int(value)
            for key, value in dict(attestation["measurements"]).items()
        },
        evidence_refs=(str(attestation["attestation_ref"]),),
    ).to_payload()


def _model_backed_delegate_budget_payload(
    frozen: FrozenNodeInput,
    *,
    audit_refs: Mapping[str, Any],
) -> dict[str, Any]:
    expected_guard = _governed_model_budget(frozen)
    actual_guard = _mapping_child(audit_refs, "delegate_model_budget_guard")
    if expected_guard is None or dict(actual_guard) != expected_guard:
        raise GovernedRouteExecutorError(
            GOVERNED_ROUTE_BUDGET_MEASUREMENT_REQUIRED,
            "model-backed Delegate did not preserve its frozen Task budget guard",
        )
    model_attempt = _mapping_child(audit_refs, "delegate_model_attempt")
    measurement = _mapping_child(model_attempt, "budget_measurement")
    if (
        model_attempt.get("success") is not True
        or not measurement
        or "token_count" not in measurement
    ):
        raise GovernedRouteExecutorError(
            GOVERNED_ROUTE_BUDGET_MEASUREMENT_REQUIRED,
            "model-backed Delegate lacks a successful provider token measurement",
        )
    usage = _worker_budget_payload(
        frozen,
        observations={"model_budget_measurements": [dict(measurement)]},
        include_agent_tool_calls=False,
    )
    if usage is None:
        raise GovernedRouteExecutorError(
            GOVERNED_ROUTE_BUDGET_MEASUREMENT_REQUIRED,
            "model-backed Delegate produced no billable budget dimensions",
        )
    return usage


def _append_model_budget_measurement(
    target: list[dict[str, Any]],
    model_attempt: Mapping[str, Any],
) -> None:
    measurement = model_attempt.get("budget_measurement")
    if isinstance(measurement, Mapping):
        # Shallow-copy only the typed measurement.  The budget contract below
        # remains responsible for strict fields, references and replay drift.
        target.append(dict(measurement))


__all__ = [
    "FrozenChatRouteExecutor",
    "FrozenDelegateRouteExecutor",
    "GOVERNED_ROUTE_INPUT_INVALID",
    "GOVERNED_ROUTE_OUTPUT_INVALID",
    "GOVERNED_ROUTE_ROUTE_DRIFT",
    "GovernedRouteExecutorError",
]
