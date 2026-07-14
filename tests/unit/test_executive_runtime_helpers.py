from __future__ import annotations

import asyncio
from types import SimpleNamespace

from host_brain.app.executive_runtime import (
    build_runtime_capability_handoff_extra,
    record_executive_handoff,
    record_runtime_capability_execution,
    stream_chat_route,
)
from host_brain.executive_runtime.conversation_execution import _build_chat_input


def test_build_runtime_capability_handoff_extra_returns_expected_fields() -> None:
    assert build_runtime_capability_handoff_extra(None) == {}

    payload = {
        "runtime_capability": {"runtime_capability_id": "rc-001"},
        "capability_forge": {"proposal_id": "forge-001", "stage": "CANARY"},
        "capability_gap": {"reason_code": "MISSING_CAPABILITY"},
    }
    extra = build_runtime_capability_handoff_extra(payload)
    assert extra["runtime_capability_id"] == "rc-001"
    assert extra["capability_forge_proposal_id"] == "forge-001"
    assert extra["capability_forge_stage"] == "CANARY"
    assert extra["capability_gap_reason"] == "MISSING_CAPABILITY"


def test_record_runtime_capability_execution_writes_forge_and_gap(monkeypatch) -> None:
    forge_calls: list[dict] = []
    gap_calls: list[dict] = []

    class _ForgeService:
        def record_runtime_result(self, **kwargs):  # noqa: ANN003
            forge_calls.append(dict(kwargs))

    class _GapTelemetry:
        def record_execution(self, **kwargs):  # noqa: ANN003
            gap_calls.append(dict(kwargs))

    monkeypatch.setattr(
        "host_brain.composition.process_services.get_capability_forge_service",
        lambda: _ForgeService(),
    )
    monkeypatch.setattr(
        "host_brain.composition.process_services.get_capability_gap_telemetry",
        lambda: _GapTelemetry(),
    )

    record_runtime_capability_execution(
        request_id="req-rt-001",
        runtime_capability_payload={
            "runtime_capability": {"runtime_capability_id": "rc-001"},
            "capability_forge": {"proposal_id": "forge-001"},
            "capability_gap": {"reason_code": "MISSING_CAPABILITY"},
        },
        success=False,
        latency_ms=123,
        error_text="NETWORK_UNAVAILABLE: timeout",
        executor_route="delegate",
        executor_target="intent_agent",
        actor="executive_runtime",
    )

    assert len(forge_calls) == 1
    assert forge_calls[0]["proposal_id"] == "forge-001"
    assert forge_calls[0]["error_code"] == "NETWORK_UNAVAILABLE"
    assert forge_calls[0]["actor"] == "executive_runtime"

    assert len(gap_calls) == 1
    assert gap_calls[0]["runtime_capability_id"] == "rc-001"
    assert gap_calls[0]["request_id"] == "req-rt-001"
    assert gap_calls[0]["executor_route"] == "delegate"
    assert gap_calls[0]["executor_target"] == "intent_agent"
    assert gap_calls[0]["error_code"] == "NETWORK_UNAVAILABLE"


def test_record_executive_handoff_records_and_emits(monkeypatch) -> None:
    handoff_records: list[dict] = []
    emitted_events: list[dict] = []

    class _Coordinator:
        def record_dispatch_handoff(self, **kwargs):  # noqa: ANN003
            handoff_records.append(dict(kwargs))
            return kwargs

    monkeypatch.setattr(
        "host_brain.composition.process_services.get_tribe_handoff_coordinator",
        lambda: _Coordinator(),
    )
    monkeypatch.setattr(
        "host_brain.tribe.events.emit_tribe_event",
        lambda **kwargs: emitted_events.append(dict(kwargs)),
    )

    record_executive_handoff(
        request_id="req-handoff-001",
        session_id="sess-handoff-001",
        handoff_route="skill",
        handoff_target="web_search",
        handoff_reason="keyword:search",
        extra={"skill_family": "knowledge"},
    )

    assert len(handoff_records) == 1
    assert handoff_records[0]["request_id"] == "req-handoff-001"
    assert handoff_records[0]["session_id"] == "sess-handoff-001"
    assert handoff_records[0]["route"] == "skill"
    assert handoff_records[0]["target_id"] == "web_search"
    assert handoff_records[0]["reason"] == "keyword:search"

    assert len(emitted_events) == 1
    assert emitted_events[0]["event_type"] == "INTERACTION_RECORDED"
    assert emitted_events[0]["source_agent"] == "executive_router"
    assert emitted_events[0]["payload"]["request_id"] == "req-handoff-001"
    assert emitted_events[0]["payload"]["route"] == "skill"


def _run(coro):
    return asyncio.run(coro)


def test_stream_chat_route_records_handoff_and_streams_events() -> None:
    handoffs: list[dict] = []

    async def _fake_surface_reply(_chat_input):  # noqa: ANN001
        yield 'data: {"phase":"CHAT","route":"chat"}\n\n'

    async def _collect():
        events = []
        async for event in stream_chat_route(
            route="chat",
            request_id="req-chat-route-001",
            route_reason="default_chat",
            skill_family="social",
            runtime_capability_handoff_extra={"runtime_capability_id": "rc-001"},
            record_dispatch_handoff_fn=lambda **kwargs: handoffs.append(dict(kwargs)),
            surface_reply_sse_generator_fn=_fake_surface_reply,
            chat_input=SimpleNamespace(),
        ):
            events.append(event)
        return events

    events = _run(_collect())
    assert len(events) == 1
    assert '"phase":"CHAT"' in events[0]
    assert handoffs[0]["handoff_route"] == "chat"
    assert handoffs[0]["extra"]["skill_family"] == "social"
    assert handoffs[0]["extra"]["runtime_capability_id"] == "rc-001"
