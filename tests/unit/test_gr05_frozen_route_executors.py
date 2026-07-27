from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from host_brain.app.governed_route_executors import (
    GOVERNED_ROUTE_BUDGET_MEASUREMENT_REQUIRED,
    GOVERNED_ROUTE_OUTPUT_INVALID,
    GOVERNED_ROUTE_ROUTE_DRIFT,
    FrozenChatRouteExecutor,
    FrozenDelegateRouteExecutor,
    GovernedRouteExecutorError,
)
from host_brain.app.governed_delegate_dispatch import GovernedDelegateTarget
from host_brain.contracts.agent_runtime.delegate_usage_attestation import (
    READONLY_DELEGATE_EXECUTION_MODE,
    build_readonly_delegate_usage_attestation,
)
from host_brain.contracts.agent_runtime.node_artifact import artifact_digest
from host_brain.contracts.agent_runtime.node_input_attestation import (
    build_node_input_artifact_attestation,
)
from host_brain.task_runtime.governed_workers import FrozenNodeInput


def _active_budget() -> dict[str, Any]:
    return {
        "budget_id": "budget://frozen-route/chat",
        "status": "ACTIVE",
        "status_version": 5,
        "remaining": {
            "token_count": 120,
            "time_ms": 4_000,
            "retry_count": 1,
            "model_cost_micros": 8_000,
        },
    }


def _complete_model_measurement() -> dict[str, Any]:
    return {
        "schema_version": "Natsume.model_runtime.budget_measurement.v1",
        "measurement_ref": "model-budget://frozen-route/surface/1",
        "attempt_count": 1,
        "retry_count": 0,
        "token_count": 18,
        "model_cost_micros": 27,
    }


def _approved_trace(trace_stage_meta: dict[str, Any]) -> dict[str, Any]:
    return {
        **dict(trace_stage_meta),
        "surface_model_attempt": {
            "success": True,
            "attempt_ids": ["model-attempt://frozen-route/surface/1"],
            "budget_measurement": _complete_model_measurement(),
        },
    }


def _frozen(
    *,
    route: str,
    target_id: str,
    input_pack: dict[str, Any] | None = None,
    input_artifacts: list[dict[str, Any]] | None = None,
    budget: dict[str, Any] | None = None,
) -> FrozenNodeInput:
    artifacts = list(input_artifacts or [])
    frozen_pack = dict(input_pack or {"user_message": "hello"})
    if route == "delegate":
        frozen_pack.setdefault(
            "delegate_target_policy",
            GovernedDelegateTarget(
                target_id=target_id,
                handler=lambda _payload, _context: {},
                adapter_id="test-port-free-readonly.v1",
                execution_mode=READONLY_DELEGATE_EXECUTION_MODE,
            ).policy_projection(),
        )
    artifact_refs = tuple(
        str(item.get("artifact_ref") or "").strip()
        for item in artifacts
    )
    return FrozenNodeInput(
        command_id="command-frozen-route",
        request_id="request-frozen-route",
        session_id="session-frozen-route",
        principal_id="principal-frozen-route",
        work_order_id="work-order-frozen-route",
        root_work_order_id="work-order-frozen-route",
        graph_id="graph-frozen-route",
        graph_revision=1,
        node_id="node-frozen-route",
        attempt_id="attempt-frozen-route",
        lease_id="lease-frozen-route",
        lease_owner_id="owner-frozen-route",
        fencing_token=1,
        node_risk_level=3,
        route=route,
        target_id=target_id,
        objective="answer only this frozen objective",
        input_refs=artifact_refs or ("artifact://frozen/input",),
        _input_pack_json=json.dumps(frozen_pack),
        _input_artifacts_json=json.dumps(artifacts),
        node_grant_ref="governor-grant://child/frozen-route",
        parent_grant_ref="governor-grant://parent/frozen-route",
        _budget_json=json.dumps(budget or {}, sort_keys=True),
    )


def _readonly_delegate_audit(
    frozen: FrozenNodeInput,
    **extra: Any,
) -> dict[str, Any]:
    invocation_ref = "governed-delegate://sha256/" + "d" * 64
    policy = dict(frozen.input_pack["delegate_target_policy"])
    return {
        **extra,
        "delegate_invocation_ref": invocation_ref,
        "delegate_adapter": "test-port-free-readonly.v1",
        "delegate_execution_mode": "PORT_FREE_READ_ONLY",
        "delegate_target_policy_ref": policy["policy_ref"],
        "delegate_review_mode": policy["review_mode"],
        "delegate_budget_mode": policy["budget_mode"],
        "delegate_effect_mode": policy["effect_mode"],
        "side_effects_executed": False,
        "delegate_runtime_usage_attestation": (
            build_readonly_delegate_usage_attestation(
                command_id=frozen.command_id,
                graph_id=frozen.graph_id,
                graph_revision=frozen.graph_revision,
                node_id=frozen.node_id,
                attempt_id=frozen.attempt_id,
                node_grant_ref=frozen.node_grant_ref,
                target_id=frozen.target_id,
                adapter_id="test-port-free-readonly.v1",
                invocation_ref=invocation_ref,
            )
        ),
    }


@pytest.mark.asyncio
async def test_delegate_executor_calls_only_frozen_target_and_emits_one_commit() -> None:
    calls: list[dict[str, Any]] = []

    async def dispatch(**kwargs):  # noqa: ANN003
        calls.append(dict(kwargs))
        return SimpleNamespace(
            route_type="delegate",
            target_id="research_agent",
            success=True,
            result_text="frozen research result",
            error_text="",
            payload={
                "audit_refs": _readonly_delegate_audit(
                    kwargs["frozen"],
                    dispatch_receipt="receipt://delegate/1",
                )
            },
        )

    executor = FrozenDelegateRouteExecutor(ports=object(), dispatch_to_sub_agent=dispatch)
    events = [
        event
        async for event in executor.execute_stream(
            _frozen(
                route="delegate",
                target_id="research_agent",
                input_pack={
                    "user_message": "research this",
                    "context": {
                        "route_type": "chat",
                        "target_id": "different_agent",
                        "delivery_authorization": {"token": "must-not-pass"},
                        "skill_family": "research",
                    },
                },
            )
        )
    ]

    assert len(events) == 1
    assert {
        key: events[0][key]
        for key in (
            "phase",
            "done",
            "decision",
            "reason_code",
            "narrative",
            "route_type",
            "target_id",
        )
    } == {
        "phase": "COMMIT",
        "done": True,
        "decision": "APPROVE",
        "reason_code": None,
        "narrative": "frozen research result",
        "route_type": "delegate",
        "target_id": "research_agent",
    }
    assert events[0]["audit_refs"]["dispatch_receipt"] == "receipt://delegate/1"
    assert events[0]["audit_refs"]["governed_frozen_route"] is True
    assert events[0]["audit_refs"]["delivery_authorized"] is False
    assert events[0]["budget_usage"]["measurements"] == {
        "model_cost_micros": 0,
        "retry_count": 0,
        "token_count": 0,
        "tool_call_count": 0,
    }
    assert calls[0]["target_id"] == "research_agent"
    assert calls[0]["task_text"] == "answer only this frozen objective"
    assert "route_type" not in calls[0]["context"]
    assert "target_id" not in calls[0]["context"]
    assert "delivery_authorization" not in calls[0]["context"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "expected_reason_code"),
    (
        ("missing", GOVERNED_ROUTE_BUDGET_MEASUREMENT_REQUIRED),
        ("nonzero", GOVERNED_ROUTE_BUDGET_MEASUREMENT_REQUIRED),
        ("attempt_drift", GOVERNED_ROUTE_BUDGET_MEASUREMENT_REQUIRED),
        ("adapter_drift", GOVERNED_ROUTE_ROUTE_DRIFT),
        ("execution_mode_drift", GOVERNED_ROUTE_ROUTE_DRIFT),
    ),
)
async def test_delegate_executor_rejects_missing_or_drifted_usage_attestation(
    mode: str,
    expected_reason_code: str,
) -> None:
    async def dispatch(**kwargs):  # noqa: ANN003
        audit = _readonly_delegate_audit(kwargs["frozen"])
        if mode == "missing":
            audit.pop("delegate_runtime_usage_attestation")
        elif mode == "nonzero":
            audit["delegate_runtime_usage_attestation"]["measurements"][
                "token_count"
            ] = 1
        elif mode == "attempt_drift":
            audit["delegate_runtime_usage_attestation"]["attempt_id"] = (
                "attempt-drifted"
            )
        elif mode == "adapter_drift":
            audit["delegate_adapter"] = "other-port-free-readonly.v1"
        else:
            audit["delegate_execution_mode"] = "READ_ONLY_HANDOFF"
        return {
            "route_type": "delegate",
            "target_id": "research_agent",
            "success": True,
            "result_text": "must not commit",
            "audit_refs": audit,
        }

    executor = FrozenDelegateRouteExecutor(
        ports=object(),
        dispatch_to_sub_agent=dispatch,
    )
    with pytest.raises(GovernedRouteExecutorError) as raised:
        _ = [
            event
            async for event in executor.execute_stream(
                _frozen(route="delegate", target_id="research_agent")
            )
        ]
    assert raised.value.reason_code == expected_reason_code


@pytest.mark.asyncio
async def test_delegate_executor_normalizes_failure_to_reject_without_recovery_route() -> None:
    calls = 0

    async def dispatch(**kwargs):  # noqa: ANN003
        nonlocal calls
        calls += 1
        return {
            "route_type": "delegate",
            "target_id": "intent_agent",
            "success": False,
            "result_text": "",
            "error_text": "SUB_AGENT_TIMEOUT: frozen dispatch expired",
            "audit_refs": _readonly_delegate_audit(kwargs["frozen"]),
        }

    executor = FrozenDelegateRouteExecutor(ports=object(), dispatch_to_sub_agent=dispatch)
    events = [
        event
        async for event in executor.execute_stream(
            _frozen(route="delegate", target_id="intent_agent")
        )
    ]

    assert calls == 1
    assert events[0]["decision"] == "REJECT"
    assert events[0]["reason_code"] == "SUB_AGENT_TIMEOUT"
    assert events[0]["narrative"] == "SUB_AGENT_TIMEOUT: frozen dispatch expired"


@pytest.mark.asyncio
async def test_delegate_executor_removes_context_authority_from_public_audit() -> None:
    async def dispatch(**kwargs):  # noqa: ANN003
        return {
            "route_type": "delegate",
            "target_id": "intent_agent",
            "success": True,
            "result_text": "safe delegate result",
            "payload": {
                "audit_refs": {
                    **_readonly_delegate_audit(kwargs["frozen"]),
                    "agent_context": {
                        "context_authorization": {"authorized_ref_count": 5},
                        "runtime_event_summary": {
                            "context_authorization": {"authorized_ref_count": 5},
                            "counts": {"evidence_items": 1},
                        },
                        "token_budget": {"actual_tokens": 12},
                    }
                }
            },
        }

    executor = FrozenDelegateRouteExecutor(ports=object(), dispatch_to_sub_agent=dispatch)
    events = [
        event
        async for event in executor.execute_stream(
            _frozen(route="delegate", target_id="intent_agent")
        )
    ]

    agent_context = events[0]["audit_refs"]["agent_context"]
    assert "context_authorization" not in agent_context
    assert "context_authorization" not in agent_context["runtime_event_summary"]
    assert agent_context["runtime_event_summary"]["counts"]["evidence_items"] == 1
    assert agent_context["token_budget"]["actual_tokens"] == 12


@pytest.mark.asyncio
async def test_chat_executor_parses_split_sse_and_buffers_one_terminal_narrative() -> None:
    captured: list[dict[str, Any]] = []

    async def stream(**kwargs):  # noqa: ANN003
        captured.append(dict(kwargs))
        yield 'data: {"phase":"RENDER","chunk":"hel'
        yield 'lo"}\n\n'
        yield 'data: {"phase":"SENTENCE","sentence":"hello"}\n\n'
        terminal = {
            "phase": "COMMIT",
            "done": True,
            "decision": "APPROVE",
            "narrative": "hello",
            "route_type": "chat",
            "target_id": "surface_reply",
            "audit_refs": {
                "trace_stage_meta": _approved_trace(kwargs["trace_stage_meta"]),
            },
        }
        encoded = json.dumps(terminal, ensure_ascii=False, separators=(",", ":"))
        split_at = len(encoded) // 2
        yield f"data: {encoded[:split_at]}"
        yield f"{encoded[split_at:]}\n\n"

    executor = FrozenChatRouteExecutor(ports=object(), stream_chat_with_context=stream)
    frozen = _frozen(
        route="chat",
        target_id="surface_reply",
        budget=_active_budget(),
    )
    events = [
        event
        async for event in executor.execute_stream(
            frozen
        )
    ]

    assert len(events) == 1
    assert events[0]["phase"] == "COMMIT"
    assert events[0]["decision"] == "APPROVE"
    assert events[0]["narrative"] == "hello"
    assert events[0]["audit_refs"]["source_event_count"] == 3
    assert captured[0]["route_reason"] == "governed_frozen_node"
    guard = captured[0]["trace_stage_meta"]["governed_model_budget"]
    assert guard["budget_id"] == "budget://frozen-route/chat"
    assert guard["graph_id"] == frozen.graph_id
    assert guard["node_id"] == frozen.node_id
    assert guard["attempt_id"] == frozen.attempt_id
    assert guard["deadline_ms"] == 4_000
    assert guard["max_provider_attempts"] == 2
    assert guard["max_output_tokens"] == 60
    assert events[0]["budget_usage"]["measurements"]["token_count"] == 18
    assert events[0]["budget_usage"]["measurements"]["model_cost_micros"] == 27


@pytest.mark.asyncio
async def test_plan_chat_consumes_only_digest_bound_dependency_artifacts() -> None:
    captured: dict[str, Any] = {}
    artifact_ref = (
        "artifact://task-result/graph-frozen-route/revisions/1/"
        "nodes/node-predecessor"
    )
    receipt_ref = "worker-projection://predecessor/" + "a" * 64
    payload = {
        "receipt_ref": receipt_ref,
        "narrative": "accepted predecessor result",
    }
    artifact = {
        "artifact_ref": artifact_ref,
        "receipt_ref": receipt_ref,
        "payload": payload,
        "payload_sha256": artifact_digest(payload),
    }

    async def stream(**kwargs):  # noqa: ANN003
        captured.update(kwargs)
        yield {
            "phase": "COMMIT",
            "done": True,
            "decision": "APPROVE",
            "narrative": "synthesized result",
            "audit_refs": {
                "trace_stage_meta": _approved_trace(
                    kwargs["trace_stage_meta"]
                ),
            },
        }

    executor = FrozenChatRouteExecutor(
        ports=object(),
        stream_chat_with_context=stream,
    )
    events = [
        event
        async for event in executor.execute_stream(
            _frozen(
                route="chat",
                target_id="surface_reply",
                input_pack={
                    "user_message": "synthesize accepted results",
                    "retrieval_evidence_items": [
                        {
                            "source_ref": "memory://unrelated",
                            "snippet": "must not enter Plan synthesis",
                        }
                    ],
                },
                input_artifacts=[artifact],
                budget=_active_budget(),
            )
        )
    ]

    assert captured["retrieval_evidence_items"] == [
        {
            "source_ref": artifact_ref,
            "evidence_ref": receipt_ref,
            "snippet": "accepted predecessor result",
            "score": 1.0,
            "confidence": 1.0,
            "store_id": "task_result_artifact",
            "metadata": {
                "store_id": "task_result_artifact",
                "payload_sha256": artifact["payload_sha256"],
            },
        }
    ]
    expected = build_node_input_artifact_attestation([artifact])
    assert (
        captured["trace_stage_meta"]["node_input_artifact_attestation"]
        == expected
    )
    assert (
        events[0]["audit_refs"]["trace_stage_meta"][
            "node_input_artifact_attestation"
        ]
        == expected
    )


@pytest.mark.asyncio
async def test_chat_executor_strips_delivery_authority_and_forces_edge_suppression() -> None:
    captured: dict[str, Any] = {}

    async def stream(**kwargs):  # noqa: ANN003
        captured.update(kwargs)
        yield {
            "phase": "COMMIT",
            "done": True,
            "decision": "REJECT",
            "reason_code": "MODEL_UNAVAILABLE",
            "narrative": "model unavailable",
        }

    frozen = _frozen(
        route="chat",
        target_id="surface_reply",
        input_pack={
            "user_message": "hello",
            "governor_result": {"decision": "APPROVE"},
            "delivery_authorization": {"token": "forbidden"},
            "request_body": {
                "reply_channel": "gateway",
                "reply_target": "external-device",
                "suppress_edge_tts": False,
                "suppress_edge_vts": False,
                "delivery_authorization": {"token": "forbidden"},
            },
            "intent_draft": {
                "args": {"query": "hello"},
                "delivery_authorization": {"token": "forbidden"},
            },
        },
    )
    executor = FrozenChatRouteExecutor(ports=object(), stream_chat_with_context=stream)
    events = [event async for event in executor.execute_stream(frozen)]

    request_body = captured["request_body"]
    assert captured["governor_result"] == {}
    assert captured["runtime_capability_payload"] is None
    assert "delivery_authorization" not in captured["intent_draft"]
    assert "delivery_authorization" not in captured["trace_stage_meta"]
    assert not hasattr(request_body, "delivery_authorization")
    assert request_body.reply_channel is None
    assert request_body.reply_target is None
    assert request_body.suppress_edge_tts is True
    assert request_body.suppress_edge_vts is True
    assert request_body.defer_effects_to_task_runtime is True
    assert events[0]["decision"] == "REJECT"
    assert events[0]["audit_refs"]["delivery_authorized"] is False


@pytest.mark.asyncio
async def test_chat_executor_preserves_only_supported_deferred_acceptance_effect() -> None:
    async def stream(**kwargs):  # noqa: ANN003
        yield {
            "phase": "COMMIT",
            "done": True,
            "decision": "APPROVE",
            "narrative": "accepted later",
            "acceptance_effect_intents": [
                {
                    "effect_type": "life.foreground_completed",
                    "commit_scope": "NODE",
                    "payload": {
                        "command_type": "foreground_completed",
                        "person_id": "person-frozen",
                    },
                }
            ],
            "audit_refs": {
                "trace_stage_meta": _approved_trace(kwargs["trace_stage_meta"]),
            },
        }

    executor = FrozenChatRouteExecutor(ports=object(), stream_chat_with_context=stream)
    events = [
        event
        async for event in executor.execute_stream(
            _frozen(
                route="chat",
                target_id="surface_reply",
                budget=_active_budget(),
            )
        )
    ]
    assert events[0]["acceptance_effect_intents"] == [
        {
            "effect_type": "life.foreground_completed",
            "commit_scope": "NODE",
            "payload": {
                "command_type": "foreground_completed",
                "person_id": "person-frozen",
            },
        }
    ]


@pytest.mark.asyncio
async def test_chat_executor_blocks_guarded_approval_without_complete_model_measurement() -> None:
    async def stream(**kwargs):  # noqa: ANN003
        trace = dict(kwargs["trace_stage_meta"])
        trace["surface_model_attempt"] = {
            "success": True,
            "attempt_ids": ["model-attempt://frozen-route/surface/partial"],
            # A provider result with no canonical cost must not become an
            # accepted chat response merely because it rendered text.
            "budget_measurement": {"token_count": 18},
        }
        yield {
            "phase": "COMMIT",
            "done": True,
            "decision": "APPROVE",
            "narrative": "unmetered answer",
            "audit_refs": {"trace_stage_meta": trace},
        }

    executor = FrozenChatRouteExecutor(ports=object(), stream_chat_with_context=stream)
    with pytest.raises(GovernedRouteExecutorError) as raised:
        _ = [
            event
            async for event in executor.execute_stream(
                _frozen(
                    route="chat",
                    target_id="surface_reply",
                    budget=_active_budget(),
                )
            )
        ]

    assert raised.value.reason_code == GOVERNED_ROUTE_BUDGET_MEASUREMENT_REQUIRED


@pytest.mark.asyncio
async def test_chat_executor_blocks_approval_when_surface_replaces_frozen_guard() -> None:
    async def stream(**kwargs):  # noqa: ANN003
        trace = _approved_trace(kwargs["trace_stage_meta"])
        trace["governed_model_budget"] = {
            **trace["governed_model_budget"],
            "budget_id": "budget://unrelated/forged",
        }
        yield {
            "phase": "COMMIT",
            "done": True,
            "decision": "APPROVE",
            "narrative": "wrong budget lineage",
            "audit_refs": {"trace_stage_meta": trace},
        }

    executor = FrozenChatRouteExecutor(ports=object(), stream_chat_with_context=stream)
    with pytest.raises(GovernedRouteExecutorError) as raised:
        _ = [
            event
            async for event in executor.execute_stream(
                _frozen(
                    route="chat",
                    target_id="surface_reply",
                    budget=_active_budget(),
                )
            )
        ]

    assert raised.value.reason_code == GOVERNED_ROUTE_BUDGET_MEASUREMENT_REQUIRED


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["missing", "duplicate"])
async def test_chat_executor_fails_closed_without_exactly_one_terminal(mode: str) -> None:
    async def stream(**_kwargs):  # noqa: ANN003
        yield {"phase": "RENDER", "chunk": "partial"}
        if mode == "duplicate":
            terminal = {
                "phase": "COMMIT",
                "done": True,
                "decision": "APPROVE",
                "narrative": "done",
            }
            yield terminal
            yield terminal

    executor = FrozenChatRouteExecutor(ports=object(), stream_chat_with_context=stream)
    with pytest.raises(GovernedRouteExecutorError) as raised:
        _ = [
            event
            async for event in executor.execute_stream(
                _frozen(route="chat", target_id="surface_reply")
            )
        ]

    assert raised.value.reason_code == GOVERNED_ROUTE_OUTPUT_INVALID


@pytest.mark.asyncio
async def test_route_drift_is_rejected_before_commit() -> None:
    async def dispatch(**_kwargs):  # noqa: ANN003
        return {
            "route_type": "delegate",
            "target_id": "rerouted_agent",
            "success": True,
            "result_text": "must not commit",
        }

    executor = FrozenDelegateRouteExecutor(ports=object(), dispatch_to_sub_agent=dispatch)
    with pytest.raises(GovernedRouteExecutorError) as raised:
        _ = [
            event
            async for event in executor.execute_stream(
                _frozen(route="delegate", target_id="frozen_agent")
            )
        ]

    assert raised.value.reason_code == GOVERNED_ROUTE_ROUTE_DRIFT
