from __future__ import annotations

import json

import pytest

from host_brain.app.chat_agent import ChatAgentInput, SubAgentContext, chat_agent_sse_generator


def _parse_sse(raw: str) -> dict:
    assert raw.startswith("data: ")
    return json.loads(raw.removeprefix("data: ").strip())


@pytest.mark.asyncio
async def test_chat_agent_uses_surface_main_chain_by_default(monkeypatch) -> None:
    calls: list[dict] = []

    async def _fake_surface_generator(agent_input, *, trace_stage_meta=None):  # noqa: ANN001
        calls.append({"agent_input": agent_input, "trace_stage_meta": dict(trace_stage_meta or {})})
        yield "data: " + json.dumps(
            {
                "phase": "COMMIT",
                "done": True,
                "decision": "APPROVE",
                "narrative": "先别整段改，输入输出那块先看清。",
                "request_id": agent_input.request_id,
                "audit_refs": {
                    "path": "chat_agent:surface_main_chain",
                    "trace_stage_meta": {"surface_main_chain_used": True},
                },
            },
            ensure_ascii=False,
        ) + "\n\n"

    monkeypatch.delenv("HOST_SURFACE_MAIN_CHAIN_ENABLED", raising=False)
    monkeypatch.setattr(
        "host_brain.app.surface_main_chain_adapter.surface_main_chain_sse_generator",
        _fake_surface_generator,
    )

    agent_input = ChatAgentInput(
        request_id="req-chat-agent-surface-main",
        session_id="sess-chat-agent-surface-main",
        user_message="这段我不会改",
        person_id="user-1",
        emotion_context={"emotion_tag": "NEUTRAL"},
        bond_summary={"relationship_distance": "familiar"},
        render_policy={"response_form": "text", "primary_topic": "technical_help"},
        llm_runtime={
            "available": True,
            "provider_name": "mock",
            "model_id": "mock-chat",
            "timeout_ms": 5000,
            "llm_api_key": "",
            "base_url_override": "",
        },
    )

    events = [_parse_sse(chunk) async for chunk in chat_agent_sse_generator(agent_input)]

    assert calls
    assert events[-1]["phase"] == "COMMIT"
    assert events[-1]["audit_refs"]["path"] == "chat_agent:surface_main_chain"


@pytest.mark.asyncio
async def test_chat_agent_keeps_surface_main_chain_after_sub_agent_route(monkeypatch) -> None:
    calls: list[dict] = []

    async def _fake_surface_generator(agent_input, *, trace_stage_meta=None):  # noqa: ANN001
        calls.append({"agent_input": agent_input, "trace_stage_meta": dict(trace_stage_meta or {})})
        yield "data: " + json.dumps(
            {
                "phase": "COMMIT",
                "done": True,
                "decision": "APPROVE",
                "narrative": "先看端口占用，再处理进程。",
                "request_id": agent_input.request_id,
                "audit_refs": {
                    "path": "chat_agent:surface_main_chain",
                    "trace_stage_meta": {
                        "surface_main_chain_used": True,
                        "chat_sub_agent_id": "codex_worker",
                    },
                },
            },
            ensure_ascii=False,
        ) + "\n\n"

    monkeypatch.delenv("HOST_SURFACE_MAIN_CHAIN_ENABLED", raising=False)
    monkeypatch.setattr(
        "host_brain.app.surface_main_chain_adapter.surface_main_chain_sse_generator",
        _fake_surface_generator,
    )

    agent_input = ChatAgentInput(
        request_id="req-chat-agent-surface-sub-agent",
        session_id="sess-chat-agent-surface-sub-agent",
        user_message="端口被占用了怎么查",
        person_id="user-1",
        sub_agent_context=SubAgentContext(
            agent_id="codex_worker",
            task="triage port conflict",
            result_text="运行 netstat -ano 查 PID。",
            success=True,
        ),
        response_form="text",
        llm_runtime={
            "available": True,
            "provider_name": "mock",
            "model_id": "mock-chat",
            "timeout_ms": 5000,
            "llm_api_key": "",
            "base_url_override": "",
        },
    )

    events = [_parse_sse(chunk) async for chunk in chat_agent_sse_generator(agent_input)]

    assert calls
    assert calls[-1]["trace_stage_meta"]["chat_sub_agent_id"] == "codex_worker"
    assert events[-1]["phase"] == "COMMIT"
    assert events[-1]["audit_refs"]["path"] == "chat_agent:surface_main_chain"


@pytest.mark.asyncio
async def test_chat_agent_bypasses_surface_for_system_tool_result(monkeypatch) -> None:
    async def _unexpected_surface_generator(agent_input, *, trace_stage_meta=None):  # noqa: ANN001
        raise AssertionError("system tool readout should bypass surface main chain")

    async def _fake_post_process(**_kwargs):  # noqa: ANN003
        return None

    monkeypatch.delenv("HOST_SURFACE_MAIN_CHAIN_ENABLED", raising=False)
    monkeypatch.setattr(
        "host_brain.app.surface_main_chain_adapter.surface_main_chain_sse_generator",
        _unexpected_surface_generator,
    )
    monkeypatch.setattr("host_brain.app.agent_runtime_shared._async_post_process", _fake_post_process)

    agent_input = ChatAgentInput(
        request_id="req-chat-agent-system-tool",
        session_id="sess-chat-agent-system-tool",
        user_message="查一下系统状态",
        person_id="user-1",
        sub_agent_context=SubAgentContext(
            agent_id="system_info",
            task="查一下系统状态",
            result_text="{'cpu': {'percent': 22.5}}",
            success=True,
            payload={
                "result": {
                    "status": "OK",
                    "action": "info",
                    "detail": {
                        "cpu": {"count_logical": 16, "count_physical": 8, "percent": 22.5},
                        "memory": {"total_gb": 32.0, "available_gb": 18.0, "percent": 43.8},
                    },
                },
            },
        ),
        response_form="text",
        llm_runtime={
            "available": False,
            "provider_name": "mock",
            "model_id": "mock-chat",
            "timeout_ms": 5000,
            "llm_api_key": "",
            "base_url_override": "",
        },
    )

    events = [_parse_sse(chunk) async for chunk in chat_agent_sse_generator(agent_input)]
    commit = next(event for event in events if event.get("phase") == "COMMIT")

    assert commit["decision"] == "APPROVE"
    assert "系统状态查到了" in commit["narrative"]
    assert "CPU 22.5%" in commit["narrative"]
    assert commit["audit_refs"]["path"] == "chat_agent:sub=system_info"
