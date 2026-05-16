from __future__ import annotations

import asyncio
from types import SimpleNamespace

from host_brain.app.executive_runtime import (
    build_runtime_capability_handoff_extra,
    record_executive_handoff,
    record_runtime_capability_execution,
    stream_chat_route,
    try_handle_recent_turn_direct_route,
    try_handle_task_progress_direct_route,
)
from host_brain.app.executive_runtime_core import _build_chat_input


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
        "host_brain.app.dependencies.get_capability_forge_service",
        lambda: _ForgeService(),
    )
    monkeypatch.setattr(
        "host_brain.app.dependencies.get_capability_gap_telemetry",
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
        "host_brain.tribe.handoff.get_tribe_handoff_coordinator",
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


def test_try_handle_recent_turn_direct_route_returns_commit_event(monkeypatch) -> None:
    created_tasks = []
    handoffs: list[dict] = []
    monkeypatch.setenv("HOST_LEGACY_DIRECT_TEXT_ROUTES_ENABLED", "true")

    async def _fake_post_process(**_kwargs):  # noqa: ANN001
        return None

    monkeypatch.setattr(
        "host_brain.app.agent_runtime_shared._maybe_build_recent_turn_answer",
        lambda **_kwargs: "recent answer",
    )
    monkeypatch.setattr(
        "host_brain.app.agent_runtime_shared._airlock_sanitize_output",
        lambda text: f"safe::{text}",
    )
    monkeypatch.setattr(
        "host_brain.app.agent_runtime_shared._async_post_process",
        _fake_post_process,
    )
    monkeypatch.setattr(
        "host_brain.app.executive_runtime.asyncio.create_task",
        lambda coro: (created_tasks.append(coro), coro.close(), None)[-1],
    )

    events = _run(
        try_handle_recent_turn_direct_route(
            route="recent_turn",
            request_id="req-recent-001",
            session_id="sess-recent-001",
            person_id="person-recent-001",
            sanitized_message="what did we say",
            render_target_lang="zh",
            route_reason="recent_turn_query",
            ts_ms=1,
            emotion_context={"emotion_tag": "NEUTRAL"},
            bond_summary={},
            governor_result={},
            pii_ingress_stats={},
            sse_fn=lambda payload: payload,
            record_dispatch_handoff_fn=lambda **kwargs: handoffs.append(dict(kwargs)),
        )
    )

    assert events is not None
    assert len(events) == 1
    assert events[0]["phase"] == "COMMIT"
    assert events[0]["audit_refs"]["path"] == "executive_runtime:recent_turn"
    assert events[0]["narrative"] == "safe::recent answer"
    assert handoffs[0]["handoff_route"] == "recent_turn"
    assert len(created_tasks) == 1


def test_try_handle_task_progress_direct_route_returns_commit_event(monkeypatch) -> None:
    created_tasks = []
    handoffs: list[dict] = []
    monkeypatch.setenv("HOST_LEGACY_DIRECT_TEXT_ROUTES_ENABLED", "true")

    async def _fake_post_process(**_kwargs):  # noqa: ANN001
        return None

    monkeypatch.setattr(
        "host_brain.app.agent_runtime_shared._airlock_sanitize_output",
        lambda text: f"safe::{text}",
    )
    monkeypatch.setattr(
        "host_brain.app.agent_runtime_shared._async_post_process",
        _fake_post_process,
    )
    monkeypatch.setattr(
        "host_brain.app.executive_runtime.asyncio.create_task",
        lambda coro: (created_tasks.append(coro), coro.close(), None)[-1],
    )

    events = _run(
        try_handle_task_progress_direct_route(
            route="task_progress",
            request_id="req-progress-001",
            session_id="sess-progress-001",
            person_id="person-progress-001",
            sanitized_message="progress?",
            route_reason="task_progress_query",
            ts_ms=1,
            emotion_context={"emotion_tag": "NEUTRAL"},
            bond_summary={},
            governor_result={},
            pii_ingress_stats={},
            render_target_lang="zh",
            fallback_no_summary_text="no-progress",
            build_task_progress_sub_context_fn=lambda **_kwargs: SimpleNamespace(result_text="step 2 running"),
            sse_fn=lambda payload: payload,
            record_dispatch_handoff_fn=lambda **kwargs: handoffs.append(dict(kwargs)),
        )
    )

    assert events is not None
    assert len(events) == 1
    assert events[0]["phase"] == "COMMIT"
    assert events[0]["audit_refs"]["path"] == "executive_runtime:task_progress"
    assert events[0]["narrative"] == "safe::step 2 running"
    assert handoffs[0]["handoff_route"] == "task_progress"
    assert len(created_tasks) == 1


def test_direct_text_routes_are_disabled_by_default(monkeypatch) -> None:
    monkeypatch.delenv("HOST_LEGACY_DIRECT_TEXT_ROUTES_ENABLED", raising=False)

    recent = _run(
        try_handle_recent_turn_direct_route(
            route="recent_turn",
            request_id="req-recent-disabled",
            session_id="sess-recent-disabled",
            person_id="person-recent-disabled",
            sanitized_message="刚才说到哪了",
            render_target_lang="zh",
            route_reason="recent_turn_query",
            ts_ms=1,
            emotion_context={"emotion_tag": "NEUTRAL"},
            bond_summary={},
            governor_result={},
            pii_ingress_stats={},
            sse_fn=lambda payload: payload,
            record_dispatch_handoff_fn=lambda **_kwargs: None,
        )
    )
    progress = _run(
        try_handle_task_progress_direct_route(
            route="task_progress",
            request_id="req-progress-disabled",
            session_id="sess-progress-disabled",
            person_id="person-progress-disabled",
            sanitized_message="进度呢",
            route_reason="task_progress_query",
            ts_ms=1,
            emotion_context={"emotion_tag": "NEUTRAL"},
            bond_summary={},
            governor_result={},
            pii_ingress_stats={},
            render_target_lang="zh",
            fallback_no_summary_text="no-progress",
            build_task_progress_sub_context_fn=lambda **_kwargs: SimpleNamespace(result_text="running"),
            sse_fn=lambda payload: payload,
            record_dispatch_handoff_fn=lambda **_kwargs: None,
        )
    )

    assert recent is None
    assert progress is None


def test_stream_chat_route_records_handoff_and_streams_events() -> None:
    handoffs: list[dict] = []

    async def _fake_chat_agent(_chat_input):  # noqa: ANN001
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
            chat_agent_sse_generator_fn=_fake_chat_agent,
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
