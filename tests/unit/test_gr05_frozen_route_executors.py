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
    budget: dict[str, Any] | None = None,
) -> FrozenNodeInput:
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
        input_refs=("artifact://frozen/input",),
        _input_pack_json=json.dumps(input_pack or {"user_message": "hello"}),
        _input_artifacts_json=json.dumps([]),
        node_grant_ref="governor-grant://child/frozen-route",
        parent_grant_ref="governor-grant://parent/frozen-route",
        _budget_json=json.dumps(budget or {}, sort_keys=True),
    )


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
            payload={"audit_refs": {"dispatch_receipt": "receipt://delegate/1"}},
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
    assert events[0] == {
        "phase": "COMMIT",
        "done": True,
        "decision": "APPROVE",
        "reason_code": None,
        "narrative": "frozen research result",
        "route_type": "delegate",
        "target_id": "research_agent",
        "audit_refs": {
            "dispatch_receipt": "receipt://delegate/1",
            "governed_frozen_route": True,
            "delivery_authorized": False,
        },
    }
    assert calls[0]["target_id"] == "research_agent"
    assert calls[0]["task_text"] == "answer only this frozen objective"
    assert "route_type" not in calls[0]["context"]
    assert "target_id" not in calls[0]["context"]
    assert "delivery_authorization" not in calls[0]["context"]


@pytest.mark.asyncio
async def test_delegate_executor_normalizes_failure_to_reject_without_recovery_route() -> None:
    calls = 0

    async def dispatch(**_kwargs):  # noqa: ANN003
        nonlocal calls
        calls += 1
        return {
            "route_type": "delegate",
            "target_id": "intent_agent",
            "success": False,
            "result_text": "",
            "error_text": "SUB_AGENT_TIMEOUT: frozen dispatch expired",
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
    async def dispatch(**_kwargs):  # noqa: ANN003
        return {
            "route_type": "delegate",
            "target_id": "intent_agent",
            "success": True,
            "result_text": "safe delegate result",
            "payload": {
                "audit_refs": {
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
