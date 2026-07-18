from __future__ import annotations

import ast
from dataclasses import fields
from pathlib import Path
from types import SimpleNamespace

import pytest

from host_brain.app.route_execution_ports import (
    GovernedRouteExecutionPorts,
    RouteExecutionBoundaryError,
    dispatch_governed_delegate,
    stream_governed_chat_with_context,
)
from host_brain.app.governed_delegate_dispatch import GovernedDelegateOutcome
from host_brain.task_runtime.governed_workers import FrozenNodeInput


ROOT = Path(__file__).resolve().parents[2]


def _ports(*, chat_stream, delegate_dispatcher, captured_inputs: list[dict] | None = None):
    def input_factory(**kwargs):  # noqa: ANN003
        if captured_inputs is not None:
            captured_inputs.append(dict(kwargs))
        return SimpleNamespace(**kwargs)

    return GovernedRouteExecutionPorts(
        surface_reply_input_factory=input_factory,
        resolve_surface_reply_llm_runtime=lambda **kwargs: {
            **dict(kwargs["base_runtime"]),
            "agent_role": "surface_reply",
        },
        surface_reply_sse_generator=chat_stream,
        governed_delegate_dispatcher=delegate_dispatcher,
    )


def _frozen_delegate() -> FrozenNodeInput:
    return FrozenNodeInput(
        command_id="command-route-port",
        request_id="request-delegate",
        session_id="session-delegate",
        principal_id="person-delegate",
        work_order_id="work-order-route-port",
        root_work_order_id="work-order-route-port",
        graph_id="graph-route-port",
        graph_revision=1,
        node_id="node-route-port",
        attempt_id="attempt-route-port",
        lease_id="lease-route-port",
        lease_owner_id="owner-route-port",
        fencing_token=1,
        route="delegate",
        target_id="intent_agent",
        objective="perform admitted work",
        input_refs=(),
        _input_pack_json="{}",
        _input_artifacts_json="[]",
        node_grant_ref="governor-grant://child/route-port",
        parent_grant_ref="governor-grant://parent/route-port",
    )


def _chat_kwargs(ports) -> dict:  # noqa: ANN001
    return {
        "ports": ports,
        "request_body": SimpleNamespace(
            input_source="GOVERNED_TASK_RUNTIME",
            response_form="text",
            reply_channel=None,
            reply_target=None,
            suppress_edge_tts=True,
            suppress_edge_vts=True,
            defer_effects_to_task_runtime=True,
            render_policy={},
            surface_task_control={},
            attachments=[],
            multimodal_observations={},
            attachment_summary="",
        ),
        "route_reason": "governed_frozen_node",
        "request_id": "request-route-port",
        "session_id": "session-route-port",
        "person_id": "person-route-port",
        "sanitized_message": "hello",
        "emotion_context": {},
        "emotion_snapshot": {},
        "bond_summary": {},
        "retrieval_evidence_items": [],
        "retrieval_summary": {"status": "SKIPPED"},
        "render_target_lang": "zh",
        "llm_runtime": {"agent_role": "some_other_role"},
        "ts_ms": 123,
        "intent_draft": {"route": "chat", "target_id": "surface_reply"},
        "trace_stage_meta": {"governed_frozen_node": True},
        "sub_agent_context": None,
        "runtime_capability_payload": None,
        "governor_result": {},
    }


@pytest.mark.asyncio
async def test_governed_chat_port_streams_once_without_delivery_authority() -> None:
    captured_inputs: list[dict] = []
    stream_calls: list[object] = []

    async def chat_stream(value):  # noqa: ANN001
        stream_calls.append(value)
        yield "data: one\n\n"
        yield "data: two\n\n"

    ports = _ports(
        chat_stream=chat_stream,
        delegate_dispatcher=object(),
        captured_inputs=captured_inputs,
    )
    events = [event async for event in stream_governed_chat_with_context(**_chat_kwargs(ports))]

    assert events == ["data: one\n\n", "data: two\n\n"]
    assert len(stream_calls) == 1
    assert len(captured_inputs) == 1
    payload = captured_inputs[0]
    assert payload["governor_result"] == {}
    assert payload["delivery_authorization"] == {}
    assert payload["defer_effects_to_task_runtime"] is True
    assert payload["suppress_edge_tts"] is True
    assert payload["suppress_edge_vts"] is True
    assert payload["llm_runtime"]["agent_role"] == "surface_reply"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("governor_result", {"decision": "APPROVE"}),
        ("runtime_capability_payload", {"runtime_capability": {"id": "forged"}}),
    ],
)
async def test_governed_chat_port_rejects_smuggled_authority(field: str, value: dict) -> None:
    async def chat_stream(_value):  # noqa: ANN001
        yield "unreachable"

    ports = _ports(chat_stream=chat_stream, delegate_dispatcher=object())
    kwargs = _chat_kwargs(ports)
    kwargs[field] = value

    with pytest.raises(RouteExecutionBoundaryError):
        _ = [event async for event in stream_governed_chat_with_context(**kwargs)]


@pytest.mark.asyncio
async def test_delegate_port_dispatches_exact_frozen_target_once() -> None:
    invocations: list[object] = []

    class Dispatcher:
        async def dispatch(self, invocation):  # noqa: ANN001
            invocations.append(invocation)
            return GovernedDelegateOutcome(
                success=True,
                target_id=invocation.target_id,
                result_text="delegate result",
                error_text="",
                audit_refs={"dispatch": "receipt://one"},
            )

    async def unused_chat(_value):  # noqa: ANN001
        yield "unreachable"

    ports = _ports(chat_stream=unused_chat, delegate_dispatcher=Dispatcher())
    outcome = await dispatch_governed_delegate(
        ports=ports,
        frozen=_frozen_delegate(),
        request_id="request-delegate",
        session_id="session-delegate",
        person_id="person-delegate",
        target_id="intent_agent",
        task_text="perform admitted work",
        user_message="perform admitted work",
        context={"skill_family": "code", "governed_frozen_node": True},
    )

    assert len(invocations) == 1
    assert invocations[0].target_id == "intent_agent"
    assert invocations[0].attempt_id == "attempt-route-port"
    assert invocations[0].lease_id == "lease-route-port"
    assert invocations[0].fencing_token == 1
    assert outcome.route_type == "delegate"
    assert outcome.target_id == "intent_agent"
    assert outcome.success is True
    assert outcome.payload["audit_refs"] == {"dispatch": "receipt://one"}


@pytest.mark.asyncio
async def test_delegate_port_rejects_planning_context_and_target_drift() -> None:
    async def unused_chat(_value):  # noqa: ANN001
        yield "unreachable"

    ports = _ports(chat_stream=unused_chat, delegate_dispatcher=object())
    base = {
        "ports": ports,
        "frozen": _frozen_delegate(),
        "request_id": "request-delegate",
        "session_id": "session-delegate",
        "person_id": "person-delegate",
        "target_id": "intent_agent",
        "task_text": "perform admitted work",
        "user_message": "perform admitted work",
    }

    with pytest.raises(RouteExecutionBoundaryError, match="planning authority"):
        await dispatch_governed_delegate(**base, context={"replan": True})
    with pytest.raises(RouteExecutionBoundaryError, match="drifted from the frozen TaskNode"):
        await dispatch_governed_delegate(
            **{**base, "target_id": "repair_agent"},
            context={},
        )


def test_governed_adapter_imports_independent_route_ports_only() -> None:
    adapter_path = ROOT / "host_brain/app/governed_task_adapter.py"
    tree = ast.parse(adapter_path.read_text(encoding="utf-8"), filename=str(adapter_path))
    forbidden = {
        (
            "host_brain.executive_runtime.conversation_execution",
            "_stream_chat_with_context",
        ),
        ("host_brain.executive_runtime.skill_execution", "_dispatch_to_sub_agent"),
    }
    imported = {
        (str(node.module or ""), alias.name)
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    assert imported.isdisjoint(forbidden)
    assert (
        "host_brain.app.route_execution_ports",
        "stream_governed_chat_with_context",
    ) in imported
    assert (
        "host_brain.app.route_execution_ports",
        "dispatch_governed_delegate",
    ) in imported


def test_route_execution_ports_expose_no_routing_or_planning_authority() -> None:
    names = {field.name for field in fields(GovernedRouteExecutionPorts)}
    assert names == {
        "surface_reply_input_factory",
        "resolve_surface_reply_llm_runtime",
        "surface_reply_sse_generator",
        "governed_delegate_dispatcher",
    }

    source_path = ROOT / "host_brain/app/route_execution_ports.py"
    tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
    imported_modules = {
        str(node.module or "")
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    assert not any(module.startswith("host_brain.executive_runtime") for module in imported_modules)
