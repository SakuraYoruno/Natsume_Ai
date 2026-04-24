from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from host_brain.app.chat_agent import chat_agent_sse_generator
from host_brain.app.chat_agent_preprocessor import ChatAgentInput
from host_brain.persona.reply_plan import ReplyContract, ReplyPlan, ResponseTrajectory
from host_brain.persona.soul_reply_surface import build_deterministic_primary_reply_text


class _MemoryStoreStub:
    def __init__(self, text: str = "") -> None:
        self._text = text

    def sample(self, *, key: str, exclude=None) -> str:  # noqa: ANN001
        _ = key, exclude
        return self._text


class _TurnStoreStub:
    def query_recent_assistant(self, **kwargs):  # noqa: ANN003
        _ = kwargs
        return []


def _parse_sse(chunk: str) -> dict:
    text = chunk.decode("utf-8") if isinstance(chunk, (bytes, bytearray)) else str(chunk)
    for line in text.splitlines():
        if line.startswith("data: "):
            return json.loads(line[6:])
    return {}


def test_build_deterministic_primary_reply_text_skips_image_social_followup() -> None:
    plan = ReplyPlan(
        primary_topic="emoji_social_followup",
        trajectory=ResponseTrajectory(
            response_goal="gentle_followup_reply",
            tactic_family="soft_emotion_ack",
            tactic_variant="backoff_hold",
            trajectory_stage="followup",
        ),
        response_form_reason="image_social_followup",
    )

    from host_brain.app import dependencies

    original_getter = dependencies.get_reply_surface_memory_store
    dependencies.get_reply_surface_memory_store = lambda: _MemoryStoreStub("哥哥，好嘛，那我就当自己刚才多想了一点。")
    try:
        text = build_deterministic_primary_reply_text(plan=plan, render_target_lang="zh")
    finally:
        dependencies.get_reply_surface_memory_store = original_getter

    assert text == ""


def test_build_deterministic_primary_reply_text_for_greeting() -> None:
    plan = ReplyPlan(
        primary_topic="greeting",
        trajectory=ResponseTrajectory(
            response_goal="phatic_response",
            tactic_family="phatic_wave",
            tactic_variant="greeting",
            trajectory_stage="first_turn",
        ),
    )

    from host_brain.app import dependencies

    original_getter = dependencies.get_reply_surface_memory_store
    dependencies.get_reply_surface_memory_store = lambda: _MemoryStoreStub("哦！你好呀。")
    try:
        text = build_deterministic_primary_reply_text(plan=plan, render_target_lang="zh")
    finally:
        dependencies.get_reply_surface_memory_store = original_getter

    assert text == "哦！你好呀。"


def test_build_deterministic_primary_reply_text_for_identity() -> None:
    plan = ReplyPlan(
        primary_topic="identity",
        trajectory=ResponseTrajectory(
            response_goal="identify",
            tactic_family="ack_repeat_then_light_probe",
            tactic_variant="confirm_real_question",
            trajectory_stage="repeat",
        ),
    )

    from host_brain.app import dependencies

    original_getter = dependencies.get_reply_surface_memory_store
    dependencies.get_reply_surface_memory_store = lambda: _MemoryStoreStub("哥哥，怎么又问起这个啦？我是新海天呀。")
    try:
        text = build_deterministic_primary_reply_text(plan=plan, render_target_lang="zh")
    finally:
        dependencies.get_reply_surface_memory_store = original_getter

    assert "新海天" in text
    assert "又问起这个啦" in text


def test_build_deterministic_primary_reply_text_for_short_assistant_followup_uses_cache(monkeypatch) -> None:
    plan = ReplyPlan(
        primary_topic="assistant_reply_followup",
        trajectory=ResponseTrajectory(
            response_goal="clarify_previous_reply",
            tactic_family="explain_previous_reply",
            tactic_variant="clarify_object",
            trajectory_stage="followup",
        ),
        response_form_reason="default_text",
        contract=ReplyContract(
            text="上一条助手回复前接的用户话是“你知道吗”。上一条助手回复是“知道。”。本轮必须围绕这段上下文补清楚。",
        ),
    )
    monkeypatch.setattr(
        "host_brain.app.dependencies.get_reply_surface_memory_store",
        lambda: _MemoryStoreStub("哥哥，刚才那个“知道”我回得太短啦，是接你前面那句“你知道吗”。"),
    )
    monkeypatch.setattr(
        "host_brain.app.dependencies.get_conversation_turn_store",
        lambda: _TurnStoreStub(),
    )

    text = build_deterministic_primary_reply_text(plan=plan, render_target_lang="zh")

    assert "你知道吗" in text
    assert "知道" in text
    assert text != "我知道了。"


def test_build_deterministic_primary_reply_text_for_assistant_followup_skips_polluted_cache(monkeypatch) -> None:
    plan = ReplyPlan(
        primary_topic="assistant_reply_followup",
        trajectory=ResponseTrajectory(
            response_goal="clarify_previous_reply",
            tactic_family="explain_previous_reply",
            tactic_variant="clarify_meaning",
            trajectory_stage="followup",
        ),
        response_form_reason="default_text",
        contract=ReplyContract(
            text="上一条助手回复前接的用户话是“知道什么”。上一条助手回复是“唔。”。本轮必须围绕这段上下文补清楚。",
        ),
    )
    monkeypatch.setattr(
        "host_brain.app.dependencies.get_reply_surface_memory_store",
        lambda: _MemoryStoreStub("哥哥，刚才那句“唔”是我没接住，不是在敷衍你。你是在问前面“知道什么”那截吧？"),
    )
    monkeypatch.setattr(
        "host_brain.app.dependencies.get_conversation_turn_store",
        lambda: _TurnStoreStub(),
    )

    text = build_deterministic_primary_reply_text(
        plan=plan,
        render_target_lang="zh",
        allow_semantic_fallback=False,
    )

    assert text == ""


def test_build_deterministic_primary_reply_text_for_filler_assistant_followup_uses_semantic_fallback() -> None:
    plan = ReplyPlan(
        primary_topic="assistant_reply_followup",
        trajectory=ResponseTrajectory(
            response_goal="clarify_previous_reply",
            tactic_family="explain_previous_reply",
            tactic_variant="clarify_meaning",
            trajectory_stage="followup",
        ),
        response_form_reason="default_text",
        contract=ReplyContract(
            text="上一条助手回复前接的用户话是“知道什么”。上一条助手回复是“唔。”。本轮必须围绕这段上下文补清楚。",
        ),
    )

    text = build_deterministic_primary_reply_text(plan=plan, render_target_lang="zh")

    assert "唔" in text
    assert "知道什么" in text
    assert text not in {"唔。", "嗯。", "哼。", "咦。"}


def test_build_deterministic_primary_reply_text_for_filler_assistant_followup_can_disable_semantic_fallback(monkeypatch) -> None:
    plan = ReplyPlan(
        primary_topic="assistant_reply_followup",
        trajectory=ResponseTrajectory(
            response_goal="clarify_previous_reply",
            tactic_family="explain_previous_reply",
            tactic_variant="clarify_meaning",
            trajectory_stage="followup",
        ),
        response_form_reason="default_text",
        contract=ReplyContract(
            text="上一条助手回复前接的用户话是“知道什么”。上一条助手回复是“唔。”。本轮必须围绕这段上下文补清楚。",
        ),
    )
    monkeypatch.setattr(
        "host_brain.app.dependencies.get_reply_surface_memory_store",
        lambda: _MemoryStoreStub(""),
    )
    monkeypatch.setattr(
        "host_brain.app.dependencies.get_conversation_turn_store",
        lambda: _TurnStoreStub(),
    )

    text = build_deterministic_primary_reply_text(
        plan=plan,
        render_target_lang="zh",
        allow_semantic_fallback=False,
    )

    assert text == ""


@pytest.mark.asyncio
async def test_chat_agent_uses_deterministic_primary_phatic_reply_without_llm(monkeypatch) -> None:
    async def _forbidden_llm(**kwargs):  # noqa: ANN003
        raise AssertionError("LLM should not be called for deterministic primary reply")
        yield ""  # pragma: no cover

    captured_post_process: dict[str, object] = {}

    async def _fake_post_process(**kwargs):  # noqa: ANN003
        captured_post_process.update(kwargs)
        return None

    monkeypatch.setattr(
        "host_brain.integrations.llm_helpers.astream_llm_chunks_with_pool",
        _forbidden_llm,
    )
    monkeypatch.setattr(
        "host_brain.app.cortex_prompt_builder.build_cortex_prompt",
        lambda **kwargs: (
            [
                {"role": "system", "content": "【人格核心】你是新海天。"},
                {"role": "user", "content": kwargs["user_message"]},
            ],
            {},
            {
                "reply_plan": {
                    "primary_topic": "greeting",
                    "interaction_move": "greeting",
                    "response_goal": "phatic_response",
                    "tactic_family": "phatic_wave",
                    "tactic_variant": "greeting",
                    "trajectory_stage": "first_turn",
                    "response_form": "text",
                    "response_form_reason": "phatic_greeting",
                    "reply_contract": "短短接住寒暄。",
                    "contract": {
                        "text": "短短接住寒暄。",
                        "required_actions": ["phatic_ack"],
                        "forbidden_actions": [],
                        "realization_steps": ["接住寒暄"],
                    },
                    "requires_validation": True,
                    "force_buffering": False,
                },
            },
        ),
    )
    monkeypatch.setattr(
        "host_brain.app.agent_runtime_shared._async_post_process",
        _fake_post_process,
    )
    monkeypatch.setattr(
        "host_brain.app.dependencies.get_reply_surface_memory_store",
        lambda: _MemoryStoreStub("哥哥？你来啦！"),
    )
    monkeypatch.setenv("HOST_STREAM_TTS_ENABLED", "false")
    monkeypatch.setenv("HOST_TTS_SENTENCE_STREAM_ENABLED", "false")

    agent_input = ChatAgentInput(
        request_id="req-deterministic-primary-phatic",
        session_id="sess-deterministic-primary-phatic",
        user_message="hello",
        person_id="user_1",
        emotion_context={"emotion_tag": "NEUTRAL"},
        bond_summary={"stage": "FRIEND"},
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
    assert "来啦" in str(commit_event.get("narrative") or "")
    assert commit_event["audit_refs"]["model_id"] == "deterministic-plan-renderer"
    await asyncio.sleep(0)
    trace_stage_meta = dict(captured_post_process.get("trace_stage_meta") or {})
    assert trace_stage_meta.get("deterministic_primary_used") is True
    assert trace_stage_meta.get("reply_used_fallback") is False


@pytest.mark.asyncio
async def test_chat_agent_uses_deterministic_primary_identity_reply_without_llm(monkeypatch) -> None:
    async def _forbidden_llm(**kwargs):  # noqa: ANN003
        raise AssertionError("LLM should not be called for deterministic primary reply")
        yield ""  # pragma: no cover

    captured_post_process: dict[str, object] = {}

    async def _fake_post_process(**kwargs):  # noqa: ANN003
        captured_post_process.update(kwargs)
        return None

    monkeypatch.setattr(
        "host_brain.integrations.llm_helpers.astream_llm_chunks_with_pool",
        _forbidden_llm,
    )
    monkeypatch.setattr(
        "host_brain.app.cortex_prompt_builder.build_cortex_prompt",
        lambda **kwargs: (
            [
                {"role": "system", "content": "【人格核心】你是新海天。"},
                {"role": "user", "content": kwargs["user_message"]},
            ],
            {},
            {
                "reply_plan": {
                    "primary_topic": "identity",
                    "interaction_move": "reconfirm",
                    "response_goal": "identify",
                    "tactic_family": "ack_repeat_then_light_probe",
                    "tactic_variant": "confirm_real_question",
                    "trajectory_stage": "repeat",
                    "response_form": "text",
                    "response_form_reason": "default_text",
                    "reply_contract": "先认下身份，再顺手接一句。",
                    "contract": {
                        "text": "先认下身份，再顺手接一句。",
                        "required_actions": ["identity_claim"],
                        "forbidden_actions": [],
                        "realization_steps": ["先认下身份", "再顺手接一句"],
                    },
                    "requires_validation": True,
                    "force_buffering": True,
                },
            },
        ),
    )
    monkeypatch.setattr(
        "host_brain.app.agent_runtime_shared._async_post_process",
        _fake_post_process,
    )
    monkeypatch.setattr(
        "host_brain.app.dependencies.get_reply_surface_memory_store",
        lambda: _MemoryStoreStub("哥哥，怎么又问起这个啦？我是新海天呀。"),
    )
    monkeypatch.setenv("HOST_STREAM_TTS_ENABLED", "false")
    monkeypatch.setenv("HOST_TTS_SENTENCE_STREAM_ENABLED", "false")

    agent_input = ChatAgentInput(
        request_id="req-deterministic-primary-identity",
        session_id="sess-deterministic-primary-identity",
        user_message="你谁",
        person_id="user_1",
        emotion_context={"emotion_tag": "NEUTRAL"},
        bond_summary={"stage": "FRIEND"},
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
    assert "新海天" in str(commit_event.get("narrative") or "")
    assert commit_event["audit_refs"]["model_id"] == "deterministic-plan-renderer"
    await asyncio.sleep(0)
    trace_stage_meta = dict(captured_post_process.get("trace_stage_meta") or {})
    assert trace_stage_meta.get("deterministic_primary_used") is True
    assert trace_stage_meta.get("reply_used_fallback") is False


@pytest.mark.asyncio
async def test_chat_agent_uses_surface_render_for_cold_assistant_followup(monkeypatch) -> None:
    async def _forbidden_stream_llm(**kwargs):  # noqa: ANN003
        raise AssertionError("stream LLM should not be called after surface render")
        yield ""  # pragma: no cover

    async def _fake_surface_llm(**kwargs):  # noqa: ANN003
        assert kwargs["scene_type"] == "CHAT_SURFACE"
        assert "不要照抄语义种子" in kwargs["messages"][0]["content"]
        return SimpleNamespace(
            content=json.dumps(
                {
                    "reply": "刚才唔了一下啦，哥哥，你这个“什么”指哪件事呀？"
                },
                ensure_ascii=False,
            ),
            final_provider="gemini",
            final_model="gemini-2.5-flash-lite",
            total_latency_ms=1234,
        )

    captured_post_process: dict[str, object] = {}

    async def _fake_post_process(**kwargs):  # noqa: ANN003
        captured_post_process.update(kwargs)
        return None

    monkeypatch.setattr(
        "host_brain.integrations.llm_helpers.astream_llm_chunks_with_pool",
        _forbidden_stream_llm,
    )
    monkeypatch.setattr(
        "host_brain.integrations.llm_helpers.acomplete_llm_with_pool",
        _fake_surface_llm,
    )
    monkeypatch.setattr(
        "host_brain.app.cortex_prompt_builder.build_cortex_prompt",
        lambda **kwargs: (
            [
                {"role": "system", "content": "【人格核心】你是新海天。"},
                {"role": "user", "content": "知道什么"},
                {"role": "assistant", "content": "唔。"},
                {"role": "user", "content": kwargs["user_message"]},
            ],
            {},
            {
                "reply_plan": {
                    "primary_topic": "assistant_reply_followup",
                    "interaction_move": "followup",
                    "response_goal": "clarify_previous_reply",
                    "tactic_family": "explain_previous_reply",
                    "tactic_variant": "clarify_meaning",
                    "trajectory_stage": "followup",
                    "response_form": "text",
                    "response_form_reason": "default_text",
                    "reply_contract": "上一条助手回复前接的用户话是“知道什么”。上一条助手回复是“唔。”。本轮必须围绕这段上下文补清楚。",
                    "contract": {
                        "text": "上一条助手回复前接的用户话是“知道什么”。上一条助手回复是“唔。”。本轮必须围绕这段上下文补清楚。",
                        "required_actions": ["previous_reference"],
                        "forbidden_actions": [],
                        "realization_steps": ["接住追问", "补清上一句"],
                    },
                    "requires_validation": True,
                    "force_buffering": False,
                },
            },
        ),
    )
    monkeypatch.setattr(
        "host_brain.app.agent_runtime_shared._async_post_process",
        _fake_post_process,
    )
    monkeypatch.setattr(
        "host_brain.app.dependencies.get_reply_surface_memory_store",
        lambda: _MemoryStoreStub(""),
    )
    monkeypatch.setattr(
        "host_brain.app.dependencies.get_conversation_turn_store",
        lambda: _TurnStoreStub(),
    )
    monkeypatch.setenv("HOST_STREAM_TTS_ENABLED", "false")
    monkeypatch.setenv("HOST_TTS_SENTENCE_STREAM_ENABLED", "false")

    agent_input = ChatAgentInput(
        request_id="req-surface-render-assistant-followup",
        session_id="sess-surface-render-assistant-followup",
        user_message="？",
        person_id="user_1",
        emotion_context={"emotion_tag": "NEUTRAL"},
        bond_summary={"stage": "FRIEND"},
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

    narrative = str(commit_event.get("narrative") or "")
    assert commit_event["decision"] == "APPROVE"
    assert "唔" in narrative
    assert "什么" in narrative
    assert "指哪件事" in narrative
    assert "没接住" not in narrative
    assert commit_event["audit_refs"]["model_id"] == "gemini-2.5-flash-lite"
    await asyncio.sleep(0)
    trace_stage_meta = dict(captured_post_process.get("trace_stage_meta") or {})
    assert trace_stage_meta.get("deterministic_primary_reason") == "assistant_reply_followup:surface_render"
    assert trace_stage_meta.get("reply_used_fallback") is False


@pytest.mark.asyncio
async def test_chat_agent_selects_valid_surface_candidate_before_fallback(monkeypatch) -> None:
    async def _forbidden_stream_llm(**kwargs):  # noqa: ANN003
        raise AssertionError("stream LLM should not be called when candidate pool has a valid surface reply")
        yield ""  # pragma: no cover

    surface_call_count = 0

    async def _fake_surface_llm(**kwargs):  # noqa: ANN003
        nonlocal surface_call_count
        surface_call_count += 1
        return SimpleNamespace(
            content=json.dumps(
                {
                        "candidates": [
                            "哥哥，刚才那句“唔”是我没接住，不是在敷衍你。你是在问前面“知道什么”那截吧？",
                            "刚才唔了一下啦，哥哥，你这个“什么”具体指哪件事呀？",
                        ]
                    },
                ensure_ascii=False,
            ),
            final_provider="gemini",
            final_model="gemini-2.5-flash-lite",
            total_latency_ms=456,
        )

    captured_post_process: dict[str, object] = {}

    async def _fake_post_process(**kwargs):  # noqa: ANN003
        captured_post_process.update(kwargs)
        return None

    monkeypatch.setattr(
        "host_brain.integrations.llm_helpers.astream_llm_chunks_with_pool",
        _forbidden_stream_llm,
    )
    monkeypatch.setattr(
        "host_brain.integrations.llm_helpers.acomplete_llm_with_pool",
        _fake_surface_llm,
    )
    monkeypatch.setattr(
        "host_brain.app.cortex_prompt_builder.build_cortex_prompt",
        lambda **kwargs: (
            [
                {"role": "system", "content": "【人格核心】你是新海天。"},
                {"role": "user", "content": "知道什么"},
                {"role": "assistant", "content": "唔。"},
                {"role": "user", "content": kwargs["user_message"]},
            ],
            {},
            {
                "reply_plan": {
                    "primary_topic": "assistant_reply_followup",
                    "interaction_move": "followup",
                    "response_goal": "clarify_previous_reply",
                    "tactic_family": "explain_previous_reply",
                    "tactic_variant": "clarify_meaning",
                    "trajectory_stage": "followup",
                    "response_form": "text",
                    "response_form_reason": "default_text",
                    "reply_contract": "上一条助手回复前接的用户话是“知道什么”。上一条助手回复是“唔。”。本轮必须围绕这段上下文补清楚。",
                    "contract": {
                        "text": "上一条助手回复前接的用户话是“知道什么”。上一条助手回复是“唔。”。本轮必须围绕这段上下文补清楚。",
                        "required_actions": ["previous_reference"],
                        "forbidden_actions": [],
                        "realization_steps": ["接住追问", "补清上一句"],
                    },
                    "requires_validation": True,
                    "force_buffering": False,
                },
            },
        ),
    )
    monkeypatch.setattr(
        "host_brain.app.agent_runtime_shared._async_post_process",
        _fake_post_process,
    )
    monkeypatch.setattr(
        "host_brain.app.dependencies.get_reply_surface_memory_store",
        lambda: _MemoryStoreStub(""),
    )
    monkeypatch.setattr(
        "host_brain.app.dependencies.get_conversation_turn_store",
        lambda: _TurnStoreStub(),
    )
    monkeypatch.setenv("HOST_STREAM_TTS_ENABLED", "false")
    monkeypatch.setenv("HOST_TTS_SENTENCE_STREAM_ENABLED", "false")

    agent_input = ChatAgentInput(
        request_id="req-surface-candidate-pool",
        session_id="sess-surface-candidate-pool",
        user_message="？",
        person_id="user_1",
        emotion_context={"emotion_tag": "NEUTRAL"},
        bond_summary={"stage": "FRIEND"},
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

    narrative = str(commit_event.get("narrative") or "")
    assert "具体指哪件事" in narrative
    assert "没接住" not in narrative
    assert commit_event["audit_refs"]["model_id"] == "gemini-2.5-flash-lite"
    assert surface_call_count == 1
    await asyncio.sleep(0)
    trace_stage_meta = dict(captured_post_process.get("trace_stage_meta") or {})
    surface_meta = dict(trace_stage_meta.get("assistant_followup_surface_render") or {})
    assert surface_meta.get("candidate_index") == 2
    assert surface_meta.get("candidate_count") == 2
    assert trace_stage_meta.get("reply_used_fallback") is False


@pytest.mark.asyncio
async def test_chat_agent_accepts_natural_surface_candidate_without_forced_previous_reference(monkeypatch) -> None:
    async def _forbidden_stream_llm(**kwargs):  # noqa: ANN003
        raise AssertionError("stream LLM should not be called when natural surface candidate is acceptable")
        yield ""  # pragma: no cover

    async def _fake_surface_llm(**kwargs):  # noqa: ANN003
        return SimpleNamespace(
            content=json.dumps(
                {
                    "candidates": [
                        "哥哥，你之前说的哪个小秘密呀？",
                        "哥哥，刚才那个“唔”太省了，是在接你那句“知道哪个小秘密”。",
                    ]
                },
                ensure_ascii=False,
            ),
            final_provider="gemini",
            final_model="gemini-2.5-flash-lite",
            total_latency_ms=321,
        )

    captured_post_process: dict[str, object] = {}

    async def _fake_post_process(**kwargs):  # noqa: ANN003
        captured_post_process.update(kwargs)
        return None

    monkeypatch.setattr(
        "host_brain.integrations.llm_helpers.astream_llm_chunks_with_pool",
        _forbidden_stream_llm,
    )
    monkeypatch.setattr(
        "host_brain.integrations.llm_helpers.acomplete_llm_with_pool",
        _fake_surface_llm,
    )
    monkeypatch.setattr(
        "host_brain.app.cortex_prompt_builder.build_cortex_prompt",
        lambda **kwargs: (
            [
                {"role": "system", "content": "【人格核心】你是新海天。"},
                {"role": "user", "content": "知道哪个小秘密"},
                {"role": "assistant", "content": "唔。"},
                {"role": "user", "content": kwargs["user_message"]},
            ],
            {},
            {
                "reply_plan": {
                    "primary_topic": "assistant_reply_followup",
                    "interaction_move": "followup",
                    "response_goal": "clarify_previous_reply",
                    "tactic_family": "explain_previous_reply",
                    "tactic_variant": "clarify_meaning",
                    "trajectory_stage": "followup",
                    "response_form": "text",
                    "response_form_reason": "default_text",
                    "reply_contract": "上一条助手回复前接的用户话是“知道哪个小秘密”。上一条助手回复是“唔。”。本轮必须围绕这段上下文补清楚。",
                    "contract": {
                        "text": "上一条助手回复前接的用户话是“知道哪个小秘密”。上一条助手回复是“唔。”。本轮必须围绕这段上下文补清楚。",
                        "required_actions": ["previous_reference"],
                        "forbidden_actions": [],
                        "realization_steps": ["接住追问", "补清上一句"],
                    },
                    "requires_validation": True,
                    "force_buffering": False,
                },
            },
        ),
    )
    monkeypatch.setattr(
        "host_brain.app.agent_runtime_shared._async_post_process",
        _fake_post_process,
    )
    monkeypatch.setattr(
        "host_brain.app.dependencies.get_reply_surface_memory_store",
        lambda: _MemoryStoreStub(""),
    )
    monkeypatch.setattr(
        "host_brain.app.dependencies.get_conversation_turn_store",
        lambda: _TurnStoreStub(),
    )
    monkeypatch.setenv("HOST_STREAM_TTS_ENABLED", "false")
    monkeypatch.setenv("HOST_TTS_SENTENCE_STREAM_ENABLED", "false")

    agent_input = ChatAgentInput(
        request_id="req-surface-natural-candidate",
        session_id="sess-surface-natural-candidate",
        user_message="？",
        person_id="user_1",
        emotion_context={"emotion_tag": "NEUTRAL"},
        bond_summary={"stage": "FRIEND"},
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

    narrative = str(commit_event.get("narrative") or "")
    assert narrative == "哥哥，你之前说的哪个小秘密呀？"
    assert commit_event["audit_refs"]["model_id"] == "gemini-2.5-flash-lite"
    await asyncio.sleep(0)
    trace_stage_meta = dict(captured_post_process.get("trace_stage_meta") or {})
    surface_meta = dict(trace_stage_meta.get("assistant_followup_surface_render") or {})
    assert surface_meta.get("candidate_index") == 1
    assert trace_stage_meta.get("reply_used_fallback") is False


@pytest.mark.asyncio
async def test_chat_agent_rejects_vague_surface_candidate_when_context_has_anchor(monkeypatch) -> None:
    async def _forbidden_stream_llm(**kwargs):  # noqa: ANN003
        raise AssertionError("stream LLM should not be called when anchored surface candidate is available")
        yield ""  # pragma: no cover

    async def _fake_surface_llm(**kwargs):  # noqa: ANN003
        return SimpleNamespace(
            content=json.dumps(
                {
                    "candidates": [
                        "哥哥你问哪个呀？",
                        "哥哥，哪个新秘密呀？你这主语藏得真好。",
                    ]
                },
                ensure_ascii=False,
            ),
            final_provider="gemini",
            final_model="gemini-2.5-flash-lite",
            total_latency_ms=321,
        )

    captured_post_process: dict[str, object] = {}

    async def _fake_post_process(**kwargs):  # noqa: ANN003
        captured_post_process.update(kwargs)
        return None

    monkeypatch.setattr(
        "host_brain.integrations.llm_helpers.astream_llm_chunks_with_pool",
        _forbidden_stream_llm,
    )
    monkeypatch.setattr(
        "host_brain.integrations.llm_helpers.acomplete_llm_with_pool",
        _fake_surface_llm,
    )
    monkeypatch.setattr(
        "host_brain.app.cortex_prompt_builder.build_cortex_prompt",
        lambda **kwargs: (
            [
                {"role": "system", "content": "【人格核心】你是新海天。"},
                {"role": "user", "content": "知道哪个新秘密"},
                {"role": "assistant", "content": "唔。"},
                {"role": "user", "content": kwargs["user_message"]},
            ],
            {},
            {
                "reply_plan": {
                    "primary_topic": "assistant_reply_followup",
                    "interaction_move": "followup",
                    "response_goal": "clarify_previous_reply",
                    "tactic_family": "explain_previous_reply",
                    "tactic_variant": "clarify_meaning",
                    "trajectory_stage": "followup",
                    "response_form": "text",
                    "response_form_reason": "default_text",
                    "reply_contract": "上一条助手回复前接的用户话是“知道哪个新秘密”。上一条助手回复是“唔。”。本轮必须围绕这段上下文补清楚。",
                    "contract": {
                        "text": "上一条助手回复前接的用户话是“知道哪个新秘密”。上一条助手回复是“唔。”。本轮必须围绕这段上下文补清楚。",
                        "required_actions": ["previous_reference"],
                        "forbidden_actions": [],
                        "realization_steps": ["接住追问", "补清上一句"],
                    },
                    "requires_validation": True,
                    "force_buffering": False,
                },
            },
        ),
    )
    monkeypatch.setattr(
        "host_brain.app.agent_runtime_shared._async_post_process",
        _fake_post_process,
    )
    monkeypatch.setattr(
        "host_brain.app.dependencies.get_reply_surface_memory_store",
        lambda: _MemoryStoreStub(""),
    )
    monkeypatch.setattr(
        "host_brain.app.dependencies.get_conversation_turn_store",
        lambda: _TurnStoreStub(),
    )
    monkeypatch.setenv("HOST_STREAM_TTS_ENABLED", "false")
    monkeypatch.setenv("HOST_TTS_SENTENCE_STREAM_ENABLED", "false")

    agent_input = ChatAgentInput(
        request_id="req-surface-reject-vague-anchor",
        session_id="sess-surface-reject-vague-anchor",
        user_message="？",
        person_id="user_1",
        emotion_context={"emotion_tag": "NEUTRAL"},
        bond_summary={"stage": "FRIEND"},
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

    assert str(commit_event.get("narrative") or "") == "哥哥，哪个新秘密呀？你这主语藏得真好。"
    await asyncio.sleep(0)
    trace_stage_meta = dict(captured_post_process.get("trace_stage_meta") or {})
    surface_meta = dict(trace_stage_meta.get("assistant_followup_surface_render") or {})
    assert surface_meta.get("candidate_index") == 2
    assert surface_meta.get("rejected_attempts")[0]["reason_code"] in {
        "SURFACE_TEMPLATE_SHAPE",
        "SURFACE_UNDERSPECIFIED_CONTEXT_NOT_RESOLVED",
    }


@pytest.mark.asyncio
async def test_chat_agent_rejects_circular_meaning_surface_candidate(monkeypatch) -> None:
    async def _forbidden_stream_llm(**kwargs):  # noqa: ANN003
        raise AssertionError("stream LLM should not be called when non-circular surface candidate is available")
        yield ""  # pragma: no cover

    async def _fake_surface_llm(**kwargs):  # noqa: ANN003
        return SimpleNamespace(
            content=json.dumps(
                {
                    "candidates": [
                        "哥哥，你问这个是什么意思呀？",
                        "刚才只唔了一下啦，哥哥，你说的是哪句？",
                    ]
                },
                ensure_ascii=False,
            ),
            final_provider="gemini",
            final_model="gemini-2.5-flash-lite",
            total_latency_ms=321,
        )

    captured_post_process: dict[str, object] = {}

    async def _fake_post_process(**kwargs):  # noqa: ANN003
        captured_post_process.update(kwargs)
        return None

    monkeypatch.setattr(
        "host_brain.integrations.llm_helpers.astream_llm_chunks_with_pool",
        _forbidden_stream_llm,
    )
    monkeypatch.setattr(
        "host_brain.integrations.llm_helpers.acomplete_llm_with_pool",
        _fake_surface_llm,
    )
    monkeypatch.setattr(
        "host_brain.app.cortex_prompt_builder.build_cortex_prompt",
        lambda **kwargs: (
            [
                {"role": "system", "content": "【人格核心】你是新海天。"},
                {"role": "user", "content": "这个是什么意思"},
                {"role": "assistant", "content": "唔。"},
                {"role": "user", "content": kwargs["user_message"]},
            ],
            {},
            {
                "reply_plan": {
                    "primary_topic": "assistant_reply_followup",
                    "interaction_move": "followup",
                    "response_goal": "clarify_previous_reply",
                    "tactic_family": "explain_previous_reply",
                    "tactic_variant": "clarify_meaning",
                    "trajectory_stage": "followup",
                    "response_form": "text",
                    "response_form_reason": "default_text",
                    "reply_contract": "上一条助手回复前接的用户话是“这个是什么意思”。上一条助手回复是“唔。”。本轮必须围绕这段上下文补清楚。",
                    "contract": {
                        "text": "上一条助手回复前接的用户话是“这个是什么意思”。上一条助手回复是“唔。”。本轮必须围绕这段上下文补清楚。",
                        "required_actions": ["previous_reference"],
                        "forbidden_actions": [],
                        "realization_steps": ["接住追问", "补清上一句"],
                    },
                    "requires_validation": True,
                    "force_buffering": False,
                },
            },
        ),
    )
    monkeypatch.setattr(
        "host_brain.app.agent_runtime_shared._async_post_process",
        _fake_post_process,
    )
    monkeypatch.setattr(
        "host_brain.app.dependencies.get_reply_surface_memory_store",
        lambda: _MemoryStoreStub(""),
    )
    monkeypatch.setattr(
        "host_brain.app.dependencies.get_conversation_turn_store",
        lambda: _TurnStoreStub(),
    )
    monkeypatch.setenv("HOST_STREAM_TTS_ENABLED", "false")
    monkeypatch.setenv("HOST_TTS_SENTENCE_STREAM_ENABLED", "false")

    agent_input = ChatAgentInput(
        request_id="req-surface-reject-circular-meaning",
        session_id="sess-surface-reject-circular-meaning",
        user_message="？",
        person_id="user_1",
        emotion_context={"emotion_tag": "NEUTRAL"},
        bond_summary={"stage": "FRIEND"},
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

    assert str(commit_event.get("narrative") or "") == "刚才只唔了一下啦，哥哥，你说的是哪句？"
    await asyncio.sleep(0)
    trace_stage_meta = dict(captured_post_process.get("trace_stage_meta") or {})
    surface_meta = dict(trace_stage_meta.get("assistant_followup_surface_render") or {})
    assert surface_meta.get("candidate_index") == 2
    assert surface_meta.get("rejected_attempts")[0]["reason_code"] == "SURFACE_TEMPLATE_SHAPE"
