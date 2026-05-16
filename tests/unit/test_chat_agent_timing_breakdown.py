from __future__ import annotations

import asyncio
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
async def test_chat_agent_surface_disabled_does_not_fall_back_to_legacy_prompt(monkeypatch) -> None:
    async def _fake_astream_llm_chunks(**kwargs):  # noqa: ANN003
        yield "你好呀。"

    def _fake_response_intelligence(**kwargs):  # noqa: ANN003
        return kwargs["narrative"], {}

    captured_post_process: dict[str, object] = {}

    async def _fake_post_process(**kwargs):  # noqa: ANN003
        captured_post_process.update(kwargs)
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
        "host_brain.app.cortex_prompt_builder.build_cortex_prompt",
        lambda **kwargs: (
            [{"role": "system", "content": "你是夏目蓝。"}, {"role": "user", "content": kwargs["user_message"]}],
            {},
            {
                "conversation_history": {
                    "turns_injected": 2,
                    "fetched_turns": 2,
                    "older_turn_count": 4,
                    "summary_present": True,
                    "oldest_ts_ms": 1000,
                    "newest_ts_ms": 2000,
                    "skipped_corrupted_count": 0,
                    "skipped_empty_count": 0,
                    "deduped_same_role_count": 0,
                }
            },
        ),
    )
    monkeypatch.setattr(
        "host_brain.app.agent_runtime_shared._async_post_process",
        _fake_post_process,
    )
    monkeypatch.setenv("HOST_STREAM_TTS_ENABLED", "false")
    monkeypatch.setenv("HOST_TTS_SENTENCE_STREAM_ENABLED", "false")
    monkeypatch.setenv("HOST_SURFACE_MAIN_CHAIN_ENABLED", "false")

    agent_input = ChatAgentInput(
        request_id="req-timing-breakdown",
        session_id="sess-timing-breakdown",
        user_message="请你认真回答这一句，不要走快捷回复。",
        person_id="user_1",
        emotion_context={"emotion_tag": "NEUTRAL"},
        bond_summary={"stage": "FRIEND"},
        intent_draft={},
        suppress_edge_tts=True,
        llm_runtime={
            "available": True,
            "provider_name": "mock",
            "model_id": "mock-chat",
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

    assert commit_event["decision"] == "NO_REPLY"
    assert commit_event["reason_code"] == "SURFACE_MAIN_CHAIN_DISABLED"
    assert commit_event["audit_refs"]["surface_main_chain_required"] is True
    assert commit_event["audit_refs"]["surface_main_chain_used"] is False
    await asyncio.sleep(0)
    assert captured_post_process == {}
