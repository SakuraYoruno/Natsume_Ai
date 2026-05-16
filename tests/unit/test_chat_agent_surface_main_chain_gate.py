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
