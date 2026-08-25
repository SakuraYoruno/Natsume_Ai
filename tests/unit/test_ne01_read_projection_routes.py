from __future__ import annotations

from dataclasses import fields
from types import SimpleNamespace

import pytest

from host_brain.agent_runtime.application_ports import AgentRuntimeApplicationPorts
from host_brain.agent_runtime.cortex_runtime import (
    _cortex_orchestrator_sse_generator_impl,
)
from host_brain.agent_runtime.read_routes import build_read_projection


class _TaskProjection:
    def summarize_for_session(self, **_kwargs):  # noqa: ANN003
        return {
            "status": "RUNNING",
            "target_id": "code_agent",
            "progress_message": "正在验证",
            "elapsed_ms": 65_000,
            "graph_id": "graph-ne01",
            "graph_revision": 2,
        }


class _History:
    def fetch_window_for_messages(self, **_kwargs):  # noqa: ANN003
        return SimpleNamespace(
            recent_turns=[
                SimpleNamespace(role="user", content="上一个问题"),
                SimpleNamespace(role="assistant", content="上一个回答"),
            ]
        )


def test_task_progress_is_rendered_from_durable_projection() -> None:
    result = build_read_projection(
        route="task_progress",
        request_id="request-read",
        session_id="session-read",
        person_id="person-read",
        user_message="进度如何",
        task_status_projection=_TaskProjection(),
        conversation_history=None,
    )

    assert result.available is True
    assert result.reason_code == "TASK_PROGRESS_PROJECTED"
    assert "code_agent" in result.narrative
    assert "1m05s" in result.narrative
    assert result.evidence["graph_id"] == "graph-ne01"


def test_recent_turn_reads_last_assistant_without_task_execution() -> None:
    result = build_read_projection(
        route="recent_turn",
        request_id="request-read",
        session_id="session-read",
        person_id="person-read",
        user_message="你刚刚说了什么",
        task_status_projection=None,
        conversation_history=_History(),
    )

    assert result.available is True
    assert result.reason_code == "RECENT_TURN_PROJECTED"
    assert result.narrative == "我：上一个回答"
    assert result.evidence["roles"] == ["assistant"]


def _request(message: str) -> SimpleNamespace:
    return SimpleNamespace(
        message=message,
        thread_id=None,
        person_id="person-read",
        reply_channel="host",
        reply_target="conversation-read",
        conversation_id="conversation-read",
        render_target_lang="zh",
        input_source="HOST_INPUT",
        from_id="person-read",
        msg_id="message-read",
        reply_to_msg_id="",
        attachments=[],
        multimodal_observations={},
        attachment_summary="",
    )


def _ports() -> SimpleNamespace:
    def forbidden_writer(**_kwargs):  # noqa: ANN003
        raise AssertionError("read projections must not write conversation state")

    return SimpleNamespace(
        record_user_turn=forbidden_writer,
        record_assistant_turn=forbidden_writer,
        task_status_projection=lambda: _TaskProjection(),
        conversation_history=lambda: _History(),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("route", "message", "reason_code"),
    [
        ("task_progress", "任务进度如何", "TASK_PROGRESS_PROJECTED"),
        ("recent_turn", "你刚刚说了什么", "RECENT_TURN_PROJECTED"),
    ],
)
async def test_cortex_read_routes_project_without_task_or_persistent_turns(
    route: str,
    message: str,
    reason_code: str,
) -> None:
    events = [
        event
        async for event in _cortex_orchestrator_sse_generator_impl(
            application_ports=_ports(),
            request_body=_request(message),
            request_id=f"request-{route}",
            session_id="session-read",
            ts_ms=1,
            intent_draft={"route": route},
            emotion_context={},
            emotion_snapshot={},
            governor_result={"decision": "APPROVE"},
            sse_fn=lambda value: dict(value),
            airlock_sanitize_input_fn=lambda value: (value, True),
            resolve_person_id_fn=lambda *_args, **_kwargs: "person-read",
            governed_command_application=None,
        )
    ]

    assert events[-1]["decision"] == "APPROVE"
    assert events[-1]["reason_code"] == reason_code


@pytest.mark.asyncio
async def test_unknown_legacy_route_fails_closed_without_conversation_write() -> None:
    events = [
        event
        async for event in _cortex_orchestrator_sse_generator_impl(
            application_ports=_ports(),
            request_body=_request("执行未知路线"),
            request_id="request-unknown-route",
            session_id="session-read",
            ts_ms=1,
            intent_draft={"route": "legacy_magic"},
            emotion_context={},
            emotion_snapshot={},
            governor_result={"decision": "APPROVE"},
            sse_fn=lambda value: dict(value),
            airlock_sanitize_input_fn=lambda value: (value, True),
            resolve_person_id_fn=lambda *_args, **_kwargs: "person-read",
            governed_command_application=None,
        )
    ]

    assert events[-1]["reason_code"] == "RUNTIME_ROUTE_UNSUPPORTED"


def test_agent_runtime_ports_have_no_executive_fallback() -> None:
    assert "executive_router_sse_generator" not in {
        field.name for field in fields(AgentRuntimeApplicationPorts)
    }
