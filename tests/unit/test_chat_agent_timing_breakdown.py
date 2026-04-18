from __future__ import annotations

import json

import pytest

from host_brain.app.chat_agent import chat_agent_sse_generator
from host_brain.app.chat_agent_preprocessor import ChatAgentInput


def _parse_sse(chunk: str) -> dict:
    text = chunk.decode("utf-8") if isinstance(chunk, (bytes, bytearray)) else str(chunk)
    for line in text.splitlines():
        if line.startswith("data: "):
            return json.loads(line[6:])
    return {}


@pytest.mark.asyncio
async def test_chat_agent_commit_includes_timing_breakdown(monkeypatch) -> None:
    async def _fake_astream_llm_chunks(**kwargs):  # noqa: ANN003
        yield "你好呀。"

    def _fake_response_intelligence(**kwargs):  # noqa: ANN003
        return kwargs["narrative"], {}

    async def _fake_post_process(**kwargs):  # noqa: ANN003
        return None

    monkeypatch.setattr(
        "host_brain.integrations.llm_helpers.astream_llm_chunks",
        _fake_astream_llm_chunks,
    )
    monkeypatch.setattr(
        "host_brain.app.intelligent_runtime.apply_response_intelligence",
        _fake_response_intelligence,
    )
    monkeypatch.setattr(
        "host_brain.app.agent_runtime_shared._async_post_process",
        _fake_post_process,
    )
    monkeypatch.setenv("HOST_STREAM_TTS_ENABLED", "false")
    monkeypatch.setenv("HOST_TTS_SENTENCE_STREAM_ENABLED", "false")

    agent_input = ChatAgentInput(
        request_id="req-timing-breakdown",
        session_id="sess-timing-breakdown",
        user_message="今天有点累，抱抱我吧。",
        person_id="user_1",
        emotion_context={"emotion_tag": "NEUTRAL"},
        bond_summary={"stage": "FRIEND"},
        intent_draft={"chat_speed_profile": "direct_fast"},
        suppress_edge_tts=True,
        llm_runtime={
            "available": True,
            "provider_name": "mock",
            "model_id": "mock-direct-fast",
            "timeout_ms": 5000,
            "llm_api_key": "",
            "base_url_override": "",
        },
    )

    commit_event = {}
    async for chunk in chat_agent_sse_generator(agent_input):
        event = _parse_sse(chunk)
        if event.get("phase") == "COMMIT":
            commit_event = event
            break

    assert commit_event["decision"] == "APPROVE"
    timings = dict(commit_event["audit_refs"]["timings_ms"])
    assert {
        "prompt_build_ms",
        "llm_runtime_resolve_ms",
        "stream_after_first_token_ms",
        "response_intelligence_ms",
        "sanitize_egress_ms",
        "precommit_log_ms",
    }.issubset(timings)
    assert all(int(value) >= 0 for value in timings.values())


@pytest.mark.asyncio
async def test_chat_agent_skips_response_intelligence_for_fast_chat(monkeypatch) -> None:
    async def _fake_astream_llm_chunks(**kwargs):  # noqa: ANN003
        yield "我在，抱一下。"

    def _forbidden_response_intelligence(**kwargs):  # noqa: ANN003
        raise AssertionError("response intelligence should be bypassed for fast chat")

    async def _fake_post_process(**kwargs):  # noqa: ANN003
        return None

    monkeypatch.setattr(
        "host_brain.integrations.llm_helpers.astream_llm_chunks",
        _fake_astream_llm_chunks,
    )
    monkeypatch.setattr(
        "host_brain.app.intelligent_runtime.apply_response_intelligence",
        _forbidden_response_intelligence,
    )
    monkeypatch.setattr(
        "host_brain.app.agent_runtime_shared._async_post_process",
        _fake_post_process,
    )
    monkeypatch.setenv("HOST_STREAM_TTS_ENABLED", "false")
    monkeypatch.setenv("HOST_TTS_SENTENCE_STREAM_ENABLED", "false")

    agent_input = ChatAgentInput(
        request_id="req-fast-ri-bypass",
        session_id="sess-fast-ri-bypass",
        user_message="抱抱我。",
        person_id="user_1",
        emotion_context={"emotion_tag": "NEUTRAL"},
        bond_summary={"stage": "FRIEND"},
        intent_draft={"chat_speed_profile": "direct_fast"},
        suppress_edge_tts=True,
        llm_runtime={
            "available": True,
            "provider_name": "mock",
            "model_id": "mock-direct-fast",
            "timeout_ms": 5000,
            "llm_api_key": "",
            "base_url_override": "",
        },
    )

    commit_event = {}
    async for chunk in chat_agent_sse_generator(agent_input):
        event = _parse_sse(chunk)
        if event.get("phase") == "COMMIT":
            commit_event = event
            break

    assert commit_event["decision"] == "APPROVE"
    meta = dict(commit_event.get("audit_refs", {}).get("response_intelligence") or {})
    assert dict(meta.get("reflection") or {}).get("reason_code") == "FAST_CHAT_RESPONSE_INTELLIGENCE_BYPASS"


@pytest.mark.asyncio
async def test_chat_agent_context_fast_cuts_stream_after_policy_cap(monkeypatch) -> None:
    emitted_chunks: list[str] = []

    async def _fake_astream_llm_chunks(**kwargs):  # noqa: ANN003
        for chunk in [
            "我在。",
            "今天辛苦了。",
            "先靠过来一点。",
            "如果你愿意，我还想继续多说很多很多。",
            "这一段如果被消费，就说明没有及时截流。",
        ]:
            emitted_chunks.append(chunk)
            yield chunk

    async def _fake_post_process(**kwargs):  # noqa: ANN003
        return None

    monkeypatch.setattr(
        "host_brain.integrations.llm_helpers.astream_llm_chunks",
        _fake_astream_llm_chunks,
    )
    monkeypatch.setattr(
        "host_brain.app.agent_runtime_shared._async_post_process",
        _fake_post_process,
    )
    monkeypatch.setenv("HOST_STREAM_TTS_ENABLED", "false")
    monkeypatch.setenv("HOST_TTS_SENTENCE_STREAM_ENABLED", "false")

    agent_input = ChatAgentInput(
        request_id="req-context-fast-cut",
        session_id="sess-context-fast-cut",
        user_message="我今天有点累，想和你认真聊聊。",
        person_id="user_1",
        emotion_context={"emotion_tag": "TIRED"},
        bond_summary={"stage": "FRIEND"},
        intent_draft={"chat_speed_profile": "context_fast"},
        suppress_edge_tts=True,
        llm_runtime={
            "available": True,
            "provider_name": "mock",
            "model_id": "mock-context-fast",
            "timeout_ms": 5000,
            "llm_api_key": "",
            "base_url_override": "",
            "agent_gen_params": {"max_tokens": 96},
        },
    )

    commit_event = {}
    async for chunk in chat_agent_sse_generator(agent_input):
        event = _parse_sse(chunk)
        if event.get("phase") == "COMMIT":
            commit_event = event
            break

    assert commit_event["decision"] == "APPROVE"
    assert commit_event["narrative"] == "我在。今天辛苦了。先靠过来一点。"
    assert len(emitted_chunks) == 4
    assert commit_event["audit_refs"]["timings_ms"]["stream_after_first_token_ms"] >= 0
