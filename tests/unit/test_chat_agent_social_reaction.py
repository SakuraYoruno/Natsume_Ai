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
async def test_chat_agent_pure_social_reaction_without_asset_does_not_call_text_surface_or_chat(monkeypatch) -> None:
    async def _blocked_stream_llm(**kwargs):  # noqa: ANN003
        raise AssertionError("pure social reaction must not call full chat")
        yield ""

    async def _blocked_surface_llm(**kwargs):  # noqa: ANN003
        raise AssertionError("pure social reaction must not call CHAT_SURFACE")

    class _EmptyCatalog:
        def select(self, **kwargs):  # noqa: ANN003
            return None

    multimodal = {
        "primary_modality": "emoji",
        "observed_types": ["emoji"],
        "multimodal_act": {
            "input_kind": "emoji",
            "user_intent": "social_reaction",
            "emotion": "awkward",
            "intensity": 0.6,
            "requires_text_answer": False,
        },
    }
    monkeypatch.setattr("host_brain.integrations.llm_helpers.astream_llm_chunks_with_pool", _blocked_stream_llm)
    monkeypatch.setattr("host_brain.integrations.llm_helpers.acomplete_llm_with_pool", _blocked_surface_llm)
    monkeypatch.setattr("host_brain.app.reaction_catalog.get_reaction_catalog", lambda: _EmptyCatalog())
    monkeypatch.setattr(
        "host_brain.app.cortex_prompt_builder.build_cortex_prompt",
        lambda **kwargs: (
            [{"role": "system", "content": "【人格核心】你是新海天。"}, {"role": "user", "content": kwargs["user_message"]}],
            {},
            {
                "reply_plan": {
                    "primary_topic": "emoji_social_reply",
                    "interaction_move": "playful_ping",
                    "response_goal": "gentle_image_reply",
                    "tactic_family": "soft_emotion_ack",
                    "tactic_variant": "steady_hold",
                    "trajectory_stage": "first_turn",
                    "response_form": "sticker",
                    "response_form_reason": "social_reaction",
                    "allow_form_only_reply": True,
                    "reply_contract": "纯表情包按社交反应处理。",
                    "contract": {"text": "纯表情包按社交反应处理。"},
                    "multimodal": multimodal,
                    "requires_validation": False,
                    "force_buffering": False,
                },
            },
        ),
    )

    agent_input = ChatAgentInput(
        request_id="req-social-reaction-no-asset",
        session_id="sess-social-reaction-no-asset",
        user_message="[表情消息]",
        person_id="user_1",
        emotion_context={"emotion_tag": "NEUTRAL"},
        bond_summary={"stage": "FRIEND"},
        multimodal_observations=multimodal,
        reply_channel="qq",
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
    assert commit_event["reason_code"] == "SOCIAL_REACTION_ASSET_MISSING"
    assert commit_event["reply_envelope"]["kind"] == "no_reply"
    assert commit_event["reply_envelope"]["sendable"] is False
    trace = dict(commit_event["audit_refs"].get("trace_stage_meta") or {})
    assert dict(trace.get("social_reaction_decider") or {}).get("invoked") is True
    assert dict(trace.get("surface_renderer") or {}).get("status") == "SKIPPED"


@pytest.mark.asyncio
async def test_chat_agent_pure_social_reaction_with_asset_returns_media_envelope(monkeypatch, tmp_path) -> None:
    from host_brain.app.reaction_catalog import ReactionAsset

    async def _blocked_stream_llm(**kwargs):  # noqa: ANN003
        raise AssertionError("pure social reaction must not call full chat")
        yield ""

    async def _blocked_surface_llm(**kwargs):  # noqa: ANN003
        raise AssertionError("pure social reaction must not call CHAT_SURFACE")

    media_path = tmp_path / "awkward.webp"
    media_path.write_bytes(b"fake")

    class _Catalog:
        def select(self, **kwargs):  # noqa: ANN003
            return ReactionAsset(
                reaction_id="awkward-1",
                emotion="awkward",
                response_form="sticker",
                media_ref=str(media_path),
            )

    multimodal = {
        "primary_modality": "emoji",
        "observed_types": ["emoji"],
        "multimodal_act": {
            "input_kind": "emoji",
            "user_intent": "social_reaction",
            "emotion": "awkward",
            "intensity": 0.6,
            "requires_text_answer": False,
        },
    }
    monkeypatch.setattr("host_brain.integrations.llm_helpers.astream_llm_chunks_with_pool", _blocked_stream_llm)
    monkeypatch.setattr("host_brain.integrations.llm_helpers.acomplete_llm_with_pool", _blocked_surface_llm)
    monkeypatch.setattr("host_brain.app.reaction_catalog.get_reaction_catalog", lambda: _Catalog())
    monkeypatch.setattr(
        "host_brain.app.cortex_prompt_builder.build_cortex_prompt",
        lambda **kwargs: (
            [{"role": "system", "content": "【人格核心】你是新海天。"}, {"role": "user", "content": kwargs["user_message"]}],
            {},
            {
                "reply_plan": {
                    "primary_topic": "emoji_social_reply",
                    "interaction_move": "playful_ping",
                    "response_goal": "gentle_image_reply",
                    "tactic_family": "soft_emotion_ack",
                    "tactic_variant": "steady_hold",
                    "trajectory_stage": "first_turn",
                    "response_form": "sticker",
                    "response_form_reason": "social_reaction",
                    "allow_form_only_reply": True,
                    "reply_contract": "纯表情包按社交反应处理。",
                    "contract": {"text": "纯表情包按社交反应处理。"},
                    "multimodal": multimodal,
                    "requires_validation": False,
                    "force_buffering": False,
                },
            },
        ),
    )

    agent_input = ChatAgentInput(
        request_id="req-social-reaction-asset",
        session_id="sess-social-reaction-asset",
        user_message="[表情消息]",
        person_id="user_1",
        emotion_context={"emotion_tag": "NEUTRAL"},
        bond_summary={"stage": "FRIEND"},
        multimodal_observations=multimodal,
        reply_channel="qq",
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

    envelope = dict(commit_event["reply_envelope"])
    assert commit_event["decision"] == "APPROVE"
    assert str(commit_event.get("narrative") or "") == ""
    assert envelope["kind"] == "final_reply"
    assert envelope["sendable"] is True
    assert envelope["cacheable"] is False
    assert envelope["response_form"] == "sticker"
    assert envelope["media_ref"] == str(media_path)
    assert envelope["reaction_id"] == "awkward-1"
