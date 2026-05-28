from __future__ import annotations

import json

import pytest

from host_brain.app.chat_agent import chat_agent_sse_generator
from host_brain.app.chat_agent_preprocessor import ChatAgentInput
from host_brain.persona.reply_plan import coerce_reply_plan
from host_brain.persona.reply_validator import validate_reply


def _parse_sse(chunk: str) -> dict:
    text = chunk.decode("utf-8") if isinstance(chunk, (bytes, bytearray)) else str(chunk)
    for line in text.splitlines():
        if line.startswith("data: "):
            return json.loads(line[6:])
    return {}


def test_social_reaction_context_only_rejects_fake_visual_ack() -> None:
    plan = coerce_reply_plan({
        "primary_topic": "emoji_social_reply",
        "tactic_family": "soft_emotion_ack",
        "tactic_variant": "image_social",
        "trajectory_stage": "first_turn",
        "response_form": "text",
        "response_form_reason": "social_reaction_context_only",
        "multimodal": {
            "summary_for_chat": "表情包社交输入",
        },
    })

    result = validate_reply(
        text="我看到了，刚才那句我先收一下。",
        plan=plan,
        user_message="[表情消息]",
        previous_assistant_text="刚才那句我说偏了，重来。",
    )

    assert "image_social_fake_visual_ack" in result.reason_codes
    assert result.safe_to_emit is False


@pytest.mark.asyncio
async def test_chat_agent_pure_social_reaction_without_asset_falls_back_to_text(monkeypatch) -> None:
    stream_calls: list[dict] = []

    async def _fake_stream_llm(**kwargs):  # noqa: ANN003
        stream_calls.append(kwargs)
        yield "你这股委屈劲儿晃过来，我先不逗你了。"

    async def _blocked_surface_llm(**kwargs):  # noqa: ANN003
        raise AssertionError("pure social reaction must not call CHAT_SURFACE")

    class _EmptyCatalog:
        def select(self, **kwargs):  # noqa: ANN003
            return None

    multimodal = {
        "primary_modality": "emoji",
        "observed_types": ["emoji"],
        "social_signal": "委屈",
        "emotion_signals": ["委屈"],
        "multimodal_act": {
            "input_kind": "emoji",
            "user_intent": "social_reaction",
            "emotion": "awkward",
            "intensity": 0.6,
            "requires_text_answer": False,
        },
    }
    monkeypatch.setattr("host_brain.integrations.llm_helpers.astream_llm_chunks_with_pool", _fake_stream_llm)
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

    assert stream_calls
    assert "输出降级" in stream_calls[-1]["messages"][-1]["content"]
    assert commit_event["decision"] == "APPROVE"
    assert commit_event["reason_code"] is None
    assert commit_event["narrative"] == "你这股委屈劲儿晃过来，我先不逗你了。"
    assert commit_event["reply_envelope"]["kind"] == "final_reply"
    assert commit_event["reply_envelope"]["sendable"] is True
    assert commit_event["reply_envelope"]["response_form"] == "text"
    trace = dict(commit_event["audit_refs"].get("trace_stage_meta") or {})
    assert dict(trace.get("social_reaction_decider") or {}).get("invoked") is True
    assert dict(trace.get("social_reaction_decider") or {}).get("fallback_to_text") is True
    assert dict(trace.get("image_social_full_chat_lane") or {}).get("status") == "FALLBACK_TO_TEXT"


@pytest.mark.asyncio
async def test_chat_agent_social_reaction_text_fallback_rejects_ungrounded_scene(monkeypatch) -> None:
    async def _bad_stream_llm(**kwargs):  # noqa: ANN003
        yield "又在房间里一个人装深沉是吧。"

    async def _blocked_surface_llm(**kwargs):  # noqa: ANN003
        raise AssertionError("social reaction text fallback must not call CHAT_SURFACE")

    class _EmptyCatalog:
        def select(self, **kwargs):  # noqa: ANN003
            return None

    multimodal = {
        "primary_modality": "emoji",
        "observed_types": ["emoji"],
        "social_signal": "委屈",
        "emotion_signals": ["委屈", "失落"],
        "multimodal_act": {
            "input_kind": "emoji",
            "user_intent": "social_reaction",
            "emotion": "sad",
            "intensity": 0.7,
            "requires_text_answer": False,
        },
    }
    monkeypatch.setattr("host_brain.integrations.llm_helpers.astream_llm_chunks_with_pool", _bad_stream_llm)
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
                    "tactic_variant": "distress_image",
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
        request_id="req-social-reaction-bad-scene",
        session_id="sess-social-reaction-bad-scene",
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
    assert commit_event["reason_code"] == "IMAGE_SOCIAL_NO_SAFE_REPLY"
    assert commit_event["narrative"] == ""
    assert commit_event["reply_envelope"]["degraded"] is True
    trace = dict(commit_event["audit_refs"].get("trace_stage_meta") or {})
    initial = dict(dict(trace.get("reply_validation") or {}).get("initial") or {})
    assert "image_social_ungrounded_inference" in initial.get("reason_codes", [])
    fallback = dict(trace.get("reply_validation") or {}).get("fallback", {})
    assert fallback.get("used") is False
    assert fallback.get("suppressed") is True


@pytest.mark.asyncio
async def test_chat_agent_social_reaction_low_confidence_observation_suppresses_reply(monkeypatch) -> None:
    async def _blocked_stream_llm(**kwargs):  # noqa: ANN003
        raise AssertionError("low confidence social reaction should not free-generate")
        yield ""

    class _EmptyCatalog:
        def select(self, **kwargs):  # noqa: ANN003
            return None

    multimodal = {
        "primary_modality": "emoji",
        "observed_types": ["emoji"],
        "summary_for_chat": "表情包社交输入",
        "multimodal_act": {
            "input_kind": "emoji",
            "user_intent": "social_reaction",
            "emotion": "unknown",
            "intensity": 0.2,
            "requires_text_answer": False,
        },
    }
    monkeypatch.setattr("host_brain.integrations.llm_helpers.astream_llm_chunks_with_pool", _blocked_stream_llm)
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
                    "tactic_variant": "image_social",
                    "trajectory_stage": "first_turn",
                    "response_form": "sticker",
                    "response_form_reason": "social_reaction",
                    "allow_form_only_reply": True,
                    "multimodal": multimodal,
                    "requires_validation": False,
                    "force_buffering": False,
                },
            },
        ),
    )

    agent_input = ChatAgentInput(
        request_id="req-social-reaction-low-confidence",
        session_id="sess-social-reaction-low-confidence",
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
    assert commit_event["reason_code"] == "SOCIAL_REACTION_OBSERVATION_LOW_CONFIDENCE"
    assert commit_event["narrative"] == ""
    assert commit_event["reply_envelope"]["kind"] == "no_reply"
    assert commit_event["reply_envelope"]["sendable"] is False
    trace = dict(commit_event["audit_refs"].get("trace_stage_meta") or {})
    assert dict(trace.get("social_reaction_decider") or {}).get("fallback_suppressed") is True
    assert dict(trace.get("image_social_full_chat_lane") or {}).get("status") == "SKIPPED_LOW_CONFIDENCE"


@pytest.mark.asyncio
async def test_chat_agent_social_reaction_low_confidence_after_previous_reply_uses_context_only_model(monkeypatch) -> None:
    stream_calls: list[dict] = []

    async def _fake_stream_llm(**kwargs):  # noqa: ANN003
        stream_calls.append(kwargs)
        yield "行，我收回刚才那句。"

    class _EmptyCatalog:
        def select(self, **kwargs):  # noqa: ANN003
            return None

    multimodal = {
        "primary_modality": "emoji",
        "observed_types": ["emoji"],
        "summary_for_chat": "表情包社交输入",
        "multimodal_act": {
            "input_kind": "emoji",
            "user_intent": "social_reaction",
            "emotion": "unknown",
            "intensity": 0.2,
            "requires_text_answer": False,
        },
    }
    monkeypatch.setattr("host_brain.integrations.llm_helpers.astream_llm_chunks_with_pool", _fake_stream_llm)
    monkeypatch.setattr("host_brain.app.reaction_catalog.get_reaction_catalog", lambda: _EmptyCatalog())
    monkeypatch.setattr(
        "host_brain.app.cortex_prompt_builder.build_cortex_prompt",
        lambda **kwargs: (
            [
                {"role": "system", "content": "【人格核心】你是新海天。"},
                {"role": "assistant", "content": "刚才那句我说偏了，重来。"},
                {"role": "user", "content": kwargs["user_message"]},
            ],
            {},
            {
                "reply_plan": {
                    "primary_topic": "emoji_social_reply",
                    "interaction_move": "playful_ping",
                    "response_goal": "gentle_image_reply",
                    "tactic_family": "soft_emotion_ack",
                    "tactic_variant": "image_social",
                    "trajectory_stage": "first_turn",
                    "response_form": "sticker",
                    "response_form_reason": "social_reaction",
                    "allow_form_only_reply": True,
                    "multimodal": multimodal,
                    "requires_validation": False,
                    "force_buffering": False,
                },
            },
        ),
    )

    agent_input = ChatAgentInput(
        request_id="req-social-reaction-low-confidence-previous",
        session_id="sess-social-reaction-low-confidence-previous",
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

    assert stream_calls
    assert "表情包上下文回应" in stream_calls[-1]["messages"][-1]["content"]
    assert commit_event["decision"] == "APPROVE"
    assert commit_event["reason_code"] is None
    assert commit_event["narrative"] == "行，我收回刚才那句。"
    assert "不硬猜" not in commit_event["narrative"]
    assert "我看到了" not in commit_event["narrative"]
    trace = dict(commit_event["audit_refs"].get("trace_stage_meta") or {})
    assert dict(trace.get("social_reaction_decider") or {}).get("context_only") is True
    assert dict(trace.get("image_social_full_chat_lane") or {}).get("status") == "CONTEXT_ONLY_LOW_CONFIDENCE"
    assert commit_event["audit_refs"]["response_form_reason"] == "social_reaction_context_only"


@pytest.mark.asyncio
async def test_chat_agent_social_reaction_to_previous_reply_repairs_instead_of_labeling(monkeypatch) -> None:
    stream_calls: list[dict] = []

    async def _fake_stream_llm(**kwargs):  # noqa: ANN003
        stream_calls.append(kwargs)
        yield "刚才那句我说偏了，重来。"

    class _EmptyCatalog:
        def select(self, **kwargs):  # noqa: ANN003
            return None

    multimodal = {
        "primary_modality": "emoji",
        "observed_types": ["emoji"],
        "social_signal": "无语",
        "emotion_signals": ["无语"],
        "summary_for_chat": "无语",
        "multimodal_act": {
            "input_kind": "emoji",
            "user_intent": "social_reaction",
            "emotion": "awkward",
            "intensity": 0.7,
            "requires_text_answer": False,
        },
    }
    monkeypatch.setattr("host_brain.integrations.llm_helpers.astream_llm_chunks_with_pool", _fake_stream_llm)
    monkeypatch.setattr("host_brain.app.reaction_catalog.get_reaction_catalog", lambda: _EmptyCatalog())
    monkeypatch.setattr(
        "host_brain.app.cortex_prompt_builder.build_cortex_prompt",
        lambda **kwargs: (
            [
                {"role": "system", "content": "【人格核心】你是新海天。"},
                {"role": "assistant", "content": "刚才那句我说偏了，重来。"},
                {"role": "user", "content": kwargs["user_message"]},
            ],
            {},
            {
                "reply_plan": {
                    "primary_topic": "emoji_social_reply",
                    "interaction_move": "playful_ping",
                    "response_goal": "gentle_image_reply",
                    "tactic_family": "soft_emotion_ack",
                    "tactic_variant": "distress_image",
                    "trajectory_stage": "first_turn",
                    "response_form": "sticker",
                    "response_form_reason": "social_reaction",
                    "allow_form_only_reply": True,
                    "multimodal": multimodal,
                    "requires_validation": False,
                    "force_buffering": False,
                },
            },
        ),
    )

    agent_input = ChatAgentInput(
        request_id="req-social-reaction-repair-previous",
        session_id="sess-social-reaction-repair-previous",
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

    assert commit_event["decision"] == "APPROVE"
    assert stream_calls
    assert commit_event["narrative"] == "刚才那句我说偏了，重来。"
    assert "这一下是有点无语" not in commit_event["narrative"]
    trace = dict(commit_event["audit_refs"].get("trace_stage_meta") or {})
    assert dict(trace.get("social_reaction_decider") or {}).get("fallback_to_text") is True
    assert dict(trace.get("image_social_full_chat_lane") or {}).get("status") == "FALLBACK_TO_TEXT"


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
