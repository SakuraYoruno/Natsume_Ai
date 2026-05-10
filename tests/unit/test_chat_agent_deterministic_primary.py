from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from host_brain.app.chat_agent import chat_agent_sse_generator
from host_brain.app.chat_agent_preprocessor import ChatAgentInput
from host_brain.persona.reply_plan import ReplyContract, ReplyPlan, ResponseTrajectory
from host_brain.persona.reply_validator import validate_reply
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

    assert text == ""


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
async def test_chat_agent_text_reply_uses_full_chat_not_surface_renderer(monkeypatch) -> None:
    kernel_calls = 0

    async def _fake_kernel_complete(**kwargs):  # noqa: ANN003
        nonlocal kernel_calls
        kernel_calls += 1
        assert kwargs["scene_type"] == "CHAT_KERNEL_V2"
        return SimpleNamespace(
            content=json.dumps(
                {"text_zh": "来啦，今天怎么这么正经。", "emotion": "playful_tease", "action": "head_tilt", "intensity": 0.5},
                ensure_ascii=False,
            ),
            final_provider="mock",
            final_model="mock-chat",
        )

    async def _blocked_surface_llm(**kwargs):  # noqa: ANN003
        raise AssertionError("normal text final reply must not call CHAT_SURFACE")

    captured_post_process: dict[str, object] = {}

    async def _fake_post_process(**kwargs):  # noqa: ANN003
        captured_post_process.update(kwargs)
        return None

    monkeypatch.setattr("host_brain.app.chat_kernel_v2.acomplete_llm_with_pool", _fake_kernel_complete)
    monkeypatch.setattr("host_brain.integrations.llm_helpers.acomplete_llm_with_pool", _blocked_surface_llm)
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
                    "reply_contract": "自然回应这句招呼。",
                    "contract": {"text": "自然回应这句招呼。"},
                    "requires_validation": False,
                    "force_buffering": False,
                },
            },
        ),
    )
    monkeypatch.setattr("host_brain.app.agent_runtime_shared._async_post_process", _fake_post_process)
    monkeypatch.setenv("HOST_CHAT_KERNEL_V2_STREAMING", "false")
    monkeypatch.setenv("HOST_CHAT_KERNEL_V2_LIGHT_DAILY_CANDIDATES", "1")
    monkeypatch.setenv("HOST_STREAM_TTS_ENABLED", "false")
    monkeypatch.setenv("HOST_TTS_SENTENCE_STREAM_ENABLED", "false")

    agent_input = ChatAgentInput(
        request_id="req-text-full-chat-no-surface",
        session_id="sess-text-full-chat-no-surface",
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

    assert kernel_calls == 1
    assert commit_event["decision"] == "APPROVE"
    assert str(commit_event.get("narrative") or "") == "来啦，今天怎么这么正经。"
    assert commit_event["audit_refs"]["model_id"] == "mock-chat"
    assert commit_event["audit_refs"]["performance"]["emotion_tag"] == "HAPPY"
    await asyncio.sleep(0)
    trace_stage_meta = dict(captured_post_process.get("trace_stage_meta") or {})
    assert trace_stage_meta.get("chat_kernel_v2_used") is True
    assert trace_stage_meta["performance"]["emotion_tag"] == "HAPPY"


def test_reply_validator_rejects_process_terms_and_stock_text() -> None:
    plan = ReplyPlan(
        primary_topic="greeting",
        trajectory=ResponseTrajectory(
            response_goal="phatic_response",
            tactic_family="phatic_wave",
            tactic_variant="greeting",
            trajectory_stage="first_turn",
        ),
        contract=ReplyContract(text="自然回应这句招呼。"),
    )

    validation = validate_reply(text="接住哥哥，别憋着嘛，跟我说说。", plan=plan)

    assert "process_term_echo" in validation.reason_codes
    assert "low_quality_stock_phrase" in validation.reason_codes
    assert validation.safe_to_emit is False


@pytest.mark.asyncio
@pytest.mark.skip(reason="SurfaceRenderer retired from normal text final reply path")
async def test_chat_agent_uses_deterministic_primary_phatic_reply_without_llm(monkeypatch) -> None:
    async def _forbidden_llm(**kwargs):  # noqa: ANN003
        raise AssertionError("full chat LLM should not be called for short social surface reply")
        yield ""  # pragma: no cover

    async def _fake_surface_llm(**kwargs):  # noqa: ANN003
        assert kwargs["scene_type"] == "CHAT_SURFACE"
        return SimpleNamespace(
            content=json.dumps({"candidates": ["哥哥，来啦。"]}, ensure_ascii=False),
            final_provider="gemini",
            final_model="gemini-2.5-flash-lite",
            total_latency_ms=111,
        )

    captured_post_process: dict[str, object] = {}

    async def _fake_post_process(**kwargs):  # noqa: ANN003
        captured_post_process.update(kwargs)
        return None

    monkeypatch.setattr(
        "host_brain.integrations.llm_helpers.astream_llm_chunks_with_pool",
        _forbidden_llm,
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
    assert commit_event["audit_refs"]["model_id"] == "gemini-2.5-flash-lite"
    await asyncio.sleep(0)
    trace_stage_meta = dict(captured_post_process.get("trace_stage_meta") or {})
    assert trace_stage_meta.get("deterministic_primary_used") is True
    assert trace_stage_meta.get("deterministic_primary_reason") == "surface:renderer"
    assert trace_stage_meta.get("reply_used_fallback") is False


@pytest.mark.asyncio
@pytest.mark.skip(reason="SurfaceRenderer retired from normal text final reply path")
async def test_chat_agent_uses_deterministic_primary_identity_reply_without_llm(monkeypatch) -> None:
    async def _forbidden_llm(**kwargs):  # noqa: ANN003
        raise AssertionError("full chat LLM should not be called for identity surface reply")
        yield ""  # pragma: no cover

    async def _fake_surface_llm(**kwargs):  # noqa: ANN003
        assert kwargs["scene_type"] == "CHAT_SURFACE"
        return SimpleNamespace(
            content=json.dumps({"candidates": ["哥哥，我是新海天呀。"]}, ensure_ascii=False),
            final_provider="gemini",
            final_model="gemini-2.5-flash-lite",
            total_latency_ms=112,
        )

    captured_post_process: dict[str, object] = {}

    async def _fake_post_process(**kwargs):  # noqa: ANN003
        captured_post_process.update(kwargs)
        return None

    monkeypatch.setattr(
        "host_brain.integrations.llm_helpers.astream_llm_chunks_with_pool",
        _forbidden_llm,
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
    assert commit_event["audit_refs"]["model_id"] == "gemini-2.5-flash-lite"
    await asyncio.sleep(0)
    trace_stage_meta = dict(captured_post_process.get("trace_stage_meta") or {})
    assert trace_stage_meta.get("deterministic_primary_used") is True
    assert trace_stage_meta.get("deterministic_primary_reason") == "surface:renderer"
    assert trace_stage_meta.get("reply_used_fallback") is False


@pytest.mark.asyncio
@pytest.mark.skip(reason="SurfaceRenderer retired from normal text final reply path")
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
    assert trace_stage_meta.get("deterministic_primary_reason") == "surface:renderer"
    assert trace_stage_meta.get("reply_used_fallback") is False


@pytest.mark.asyncio
@pytest.mark.skip(reason="SurfaceRenderer retired from normal text final reply path")
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
@pytest.mark.skip(reason="SurfaceRenderer retired from normal text final reply path")
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
@pytest.mark.skip(reason="SurfaceRenderer retired from normal text final reply path")
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
@pytest.mark.skip(reason="SurfaceRenderer retired from normal text final reply path")
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
    assert surface_meta.get("rejected_attempts")[0]["reason_code"] == "SURFACE_CONTEXT_ECHO"


@pytest.mark.asyncio
@pytest.mark.skip(reason="SurfaceRenderer retired from normal text final reply path")
async def test_chat_agent_image_social_prefers_surface_renderer(monkeypatch) -> None:
    stream_called = False

    async def _full_chat_stream_llm(**kwargs):  # noqa: ANN003
        nonlocal stream_called
        stream_called = True
        raise AssertionError("full chat LLM should not be called when image social surface succeeds")

    async def _fake_surface_llm(**kwargs):  # noqa: ANN003
        assert kwargs["scene_type"] == "CHAT_SURFACE"
        return SimpleNamespace(
            content=json.dumps({"candidates": ["哥哥，怎么了呀？", "哥哥，先不闹你了。"]}, ensure_ascii=False),
            final_provider="gemini",
            final_model="gemini-2.5-flash-lite",
            total_latency_ms=123,
        )

    captured_post_process: dict[str, object] = {}

    async def _fake_post_process(**kwargs):  # noqa: ANN003
        captured_post_process.update(kwargs)
        return None

    multimodal = {
        "primary_modality": "image",
        "summary_for_chat": "委屈；一张动漫风格的猫耳女孩特写图。",
        "social_signal": "委屈",
        "emotion_signals": ["委屈"],
    }
    monkeypatch.setattr("host_brain.integrations.llm_helpers.astream_llm_chunks_with_pool", _full_chat_stream_llm)
    monkeypatch.setattr("host_brain.integrations.llm_helpers.acomplete_llm_with_pool", _fake_surface_llm)
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
                    "response_form": "text",
                    "response_form_reason": "image_social_reply",
                    "reply_contract": "把表情包当成情绪输入，不要点评图片。",
                    "contract": {
                        "text": "把表情包当成情绪输入，不要点评图片。",
                        "required_actions": [],
                    },
                    "multimodal": multimodal,
                    "requires_validation": True,
                    "force_buffering": True,
                },
            },
        ),
    )
    monkeypatch.setattr("host_brain.app.agent_runtime_shared._async_post_process", _fake_post_process)
    monkeypatch.setattr("host_brain.app.dependencies.get_reply_surface_memory_store", lambda: _MemoryStoreStub(""))
    monkeypatch.setenv("HOST_STREAM_TTS_ENABLED", "false")
    monkeypatch.setenv("HOST_TTS_SENTENCE_STREAM_ENABLED", "false")

    agent_input = ChatAgentInput(
        request_id="req-image-social-full-chat",
        session_id="sess-image-social-full-chat",
        user_message="[图片消息]",
        person_id="user_1",
        emotion_context={"emotion_tag": "NEUTRAL"},
        bond_summary={"stage": "FRIEND"},
        multimodal_observations=multimodal,
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

    assert str(commit_event.get("narrative") or "") == "哥哥，先不闹你了。"
    assert stream_called is False
    await asyncio.sleep(0)
    trace_stage_meta = dict(captured_post_process.get("trace_stage_meta") or {})
    assert trace_stage_meta.get("deterministic_primary_used") is True
    surface_meta = dict(trace_stage_meta.get("image_social_surface_render") or {})
    assert surface_meta.get("status") == "OK"
    assert trace_stage_meta.get("reply_source") == "surface"


@pytest.mark.asyncio
@pytest.mark.skip(reason="SurfaceRenderer retired from normal text final reply path")
async def test_chat_agent_image_social_validation_failure_suppresses_fallback_text(monkeypatch) -> None:
    stream_calls = 0

    async def _bad_full_chat_stream_llm(**kwargs):  # noqa: ANN003
        nonlocal stream_calls
        stream_calls += 1
        raise AssertionError("image social surface rejection must not fall through to full chat")
        yield "哥哥，你是不是觉得这张图里的猫猫很可爱呀？"

    surface_calls = 0

    async def _bad_surface_llm(**kwargs):  # noqa: ANN003
        nonlocal surface_calls
        surface_calls += 1
        return SimpleNamespace(
            content=json.dumps({"candidates": ["哥哥，你是不是觉得这张图里的猫猫很可爱呀？"]}, ensure_ascii=False),
            final_provider="gemini",
            final_model="gemini-2.5-flash-lite",
            total_latency_ms=123,
        )

    captured_post_process: dict[str, object] = {}

    async def _fake_post_process(**kwargs):  # noqa: ANN003
        captured_post_process.update(kwargs)
        return None

    multimodal = {
        "primary_modality": "image",
        "summary_for_chat": "可爱、卖萌；一张动漫风格的猫耳女孩特写图。",
        "social_signal": "可爱",
        "emotion_signals": ["可爱", "卖萌"],
    }
    monkeypatch.setattr("host_brain.integrations.llm_helpers.astream_llm_chunks_with_pool", _bad_full_chat_stream_llm)
    monkeypatch.setattr("host_brain.integrations.llm_helpers.acomplete_llm_with_pool", _bad_surface_llm)
    monkeypatch.setattr(
        "host_brain.app.cortex_prompt_builder.build_cortex_prompt",
        lambda **kwargs: (
            [{"role": "system", "content": "【人格核心】你是新海天。"}, {"role": "user", "content": kwargs["user_message"]}],
            {},
            {
                "reply_plan": {
                    "primary_topic": "emoji_social_reply",
                    "interaction_move": "playful_ping",
                    "response_goal": "cute_image_reply",
                    "tactic_family": "cute_image_ack",
                    "tactic_variant": "cute_ping",
                    "trajectory_stage": "first_turn",
                    "response_form": "text",
                    "response_form_reason": "image_social_reply",
                    "reply_contract": "把表情包当成情绪输入，不要点评图片。",
                    "contract": {
                        "text": "把表情包当成情绪输入，不要点评图片。",
                        "required_actions": [],
                    },
                    "multimodal": multimodal,
                    "requires_validation": True,
                    "force_buffering": True,
                },
            },
        ),
    )
    monkeypatch.setattr("host_brain.app.agent_runtime_shared._async_post_process", _fake_post_process)
    monkeypatch.setattr("host_brain.app.dependencies.get_reply_surface_memory_store", lambda: _MemoryStoreStub(""))
    monkeypatch.setenv("HOST_STREAM_TTS_ENABLED", "false")
    monkeypatch.setenv("HOST_TTS_SENTENCE_STREAM_ENABLED", "false")

    agent_input = ChatAgentInput(
        request_id="req-image-social-invalid-suppressed",
        session_id="sess-image-social-invalid-suppressed",
        user_message="[图片消息]",
        person_id="user_1",
        emotion_context={"emotion_tag": "NEUTRAL"},
        bond_summary={"stage": "FRIEND"},
        multimodal_observations=multimodal,
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

    assert surface_calls >= 1
    assert stream_calls == 0
    assert commit_event["decision"] == "NO_REPLY"
    assert commit_event["reason_code"] == "IMAGE_SOCIAL_NO_SAFE_REPLY"
    assert str(commit_event.get("narrative") or "") == ""
    assert commit_event["reply_envelope"]["kind"] == "no_reply"
    assert commit_event["reply_envelope"]["sendable"] is False
    assert "没接稳" not in json.dumps(commit_event, ensure_ascii=False)
    await asyncio.sleep(0)
    trace_stage_meta = dict(captured_post_process.get("trace_stage_meta") or {})
    fallback_meta = dict(trace_stage_meta.get("fallback_suppressed") or {})
    assert fallback_meta.get("reason_code") == "IMAGE_SOCIAL_NO_SAFE_REPLY"
    assert fallback_meta.get("source") in {
        "image_social_surface_no_safe_candidate",
        "image_social_surface_candidate_invalid",
        "image_social_deterministic_pre_render_invalid",
        "image_social_initial_invalid",
    }
    assert trace_stage_meta.get("fallback_used") is False
    assert trace_stage_meta.get("suppress_final_reply") is True
    assert dict(trace_stage_meta.get("image_social_surface_suppressed_full_chat") or {}).get("status") == "SUPPRESSED"


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
