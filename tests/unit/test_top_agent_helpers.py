from __future__ import annotations

import json
import re
import sys
from types import SimpleNamespace
from unittest.mock import patch

from host_brain.app import top_agent
from host_brain.app.top_agent import (
    _SubAgentGatewayAdapter,
    _airlock_sanitize_input,
    _airlock_sanitize_output,
    _build_reply_preview,
    _build_subagent_gateway_from_runtime,
    _detect_recent_turn_query_role,
    _extract_delegate_from_llm_output,
    _extract_delegate_visible_text,
    _extract_visible_text,
    _infer_emotion_tag_for_tts,
    _load_continuity_turns,
    _looks_like_active_task_context,
    _maybe_build_recent_turn_answer,
    _maybe_build_task_progress_fastpath,
    _normalize_retrieval_summary_for_audit,
    _parse_bool_env,
    _pick_non_repeating_variant,
    _render_delegated_result_naturally,
    _resolve_retrieval_timeout_seconds,
    _should_buffer_stream_output,
    _sse,
)


def test_parse_bool_env_handles_default_and_true_value(monkeypatch) -> None:
    monkeypatch.delenv("TOP_AGENT_BOOL", raising=False)
    assert _parse_bool_env("TOP_AGENT_BOOL", default=True) is True

    monkeypatch.setenv("TOP_AGENT_BOOL", "On")
    assert _parse_bool_env("TOP_AGENT_BOOL") is True


def test_sse_serializes_payload() -> None:
    payload = {"phase": "COMMIT", "done": True}

    rendered = _sse(payload)

    assert rendered.startswith("data: ")
    assert rendered.endswith("\n\n")
    assert json.loads(rendered.removeprefix("data: ").strip()) == payload


def test_airlock_sanitize_input_strips_null_and_detects_blank() -> None:
    assert _airlock_sanitize_input("\x00 hi \x00") == ("hi", True)
    assert _airlock_sanitize_input(" \x00 ") == ("", False)


def test_airlock_sanitize_output_truncates_at_sentence_boundary(monkeypatch) -> None:
    monkeypatch.setenv("HOST_QA_MAX_RESPONSE_CHARS", "18")

    rendered = _airlock_sanitize_output("\x00abcdefghijklmno! trailing text")

    assert rendered.endswith("!")
    assert "\x00" not in rendered
    assert len(rendered) <= 18


def test_build_reply_preview_normalizes_newlines_and_truncates() -> None:
    assert _build_reply_preview("line1\r\nline2", limit=20) == "line1  line2"
    assert _build_reply_preview("0123456789", limit=6) == "012..."


def test_load_continuity_turns_returns_converted_rows() -> None:
    rows = [{"payload": {"input_preview": "hello"}}]
    turns = [{"role": "user", "content": "hello"}]

    with patch("host_brain.app.dependencies.get_ledger", return_value="LEDGER"), patch(
        "host_brain.app.top_agent_prompt._fetch_continuity_rows",
        return_value=rows,
    ) as fetch_rows, patch(
        "host_brain.app.top_agent_prompt._voice_rows_to_turns",
        return_value=turns,
    ) as rows_to_turns:
        result = _load_continuity_turns(
            request_id="req-1",
            session_id="session-1",
            person_id="person-1",
            render_target_lang="zh",
        )

    assert result == turns
    assert fetch_rows.call_args.kwargs["ledger"] == "LEDGER"
    assert rows_to_turns.call_args.kwargs == {"rows": rows, "render_target_lang": "zh"}


def test_load_continuity_turns_swallow_lookup_error() -> None:
    with patch(
        "host_brain.app.top_agent_prompt._fetch_continuity_rows",
        side_effect=RuntimeError("boom"),
    ):
        result = _load_continuity_turns(
            request_id="req-2",
            session_id="session-2",
            person_id="person-2",
            render_target_lang="ja",
        )

    assert result == []


def test_looks_like_active_task_context_detects_keyword_and_false_case() -> None:
    assert _looks_like_active_task_context([
        {"role": "assistant", "content": "继续这个模块设计和实现细节"},
    ]) is True
    assert _looks_like_active_task_context([
        {"role": "assistant", "content": "short reply"},
    ]) is False


def test_pick_non_repeating_variant_avoids_last_reply_and_handles_empty() -> None:
    assert _pick_non_repeating_variant(
        variants=["same", "same"],
        last_reply="same",
        request_id="req",
    ) == "same"
    assert _pick_non_repeating_variant(
        variants=[],
        last_reply="same",
        request_id="req",
    )


def test_extract_delegate_visible_text_uses_normalizer() -> None:
    with patch(
        "host_brain.app.intelligent_runtime._normalize_narrative_payload",
        return_value=" cleaned ",
    ):
        assert _extract_delegate_visible_text("raw") == "cleaned"


def test_extract_delegate_visible_text_swallow_normalizer_error() -> None:
    with patch(
        "host_brain.app.intelligent_runtime._normalize_narrative_payload",
        side_effect=RuntimeError("boom"),
    ):
        assert _extract_delegate_visible_text(" raw ") == "raw"


def test_render_delegated_result_naturally_uses_template_and_visible_text() -> None:
    with patch("host_brain.app.top_agent._extract_delegate_visible_text", return_value="final result"):
        rendered = _render_delegated_result_naturally(
            agent_id="planner",
            result_text="ignored",
            reply_template="整理后：{agent_result}",
        )

    assert rendered == "整理后：final result"


def test_render_delegated_result_naturally_falls_back_for_code_agent() -> None:
    with patch("host_brain.app.top_agent._extract_delegate_visible_text", return_value=""):
        rendered = _render_delegated_result_naturally(
            agent_id="code_agent",
            result_text="ignored",
            reply_template=None,
        )

    assert "代码" in rendered or "思路" in rendered


def test_render_delegated_result_naturally_falls_back_for_non_code_agent() -> None:
    with patch("host_brain.app.top_agent._extract_delegate_visible_text", return_value=""):
        rendered = _render_delegated_result_naturally(
            agent_id="planner",
            result_text="ignored",
            reply_template=None,
        )

    assert "整理成正常说话" in rendered


def test_render_delegated_result_naturally_returns_visible_without_template() -> None:
    with patch("host_brain.app.top_agent._extract_delegate_visible_text", return_value="plain result"):
        code_agent_rendered = _render_delegated_result_naturally(
            agent_id="code_agent",
            result_text="ignored",
            reply_template=None,
        )
        planner_rendered = _render_delegated_result_naturally(
            agent_id="planner",
            result_text="ignored",
            reply_template=None,
        )

    assert "plain result" in code_agent_rendered
    assert planner_rendered == "plain result"


def test_subagent_gateway_adapter_complete_maps_messages_and_extracts_content() -> None:
    class _FakeMessage:
        def __init__(self, *, content: str) -> None:
            self.content = content

    class _FakeSystemMessage(_FakeMessage):
        pass

    class _FakeHumanMessage(_FakeMessage):
        pass

    class _FakeAIMessage(_FakeMessage):
        pass

    class _FakeChatOpenAI:
        init_kwargs: dict[str, object] | None = None
        last_messages: list[object] | None = None

        def __init__(self, **kwargs) -> None:
            type(self).init_kwargs = dict(kwargs)

        def invoke(self, messages: list[object]) -> SimpleNamespace:
            type(self).last_messages = list(messages)
            return SimpleNamespace(content="RAW-CONTENT")

    with patch.dict(
        sys.modules,
        {
            "langchain_core.messages": SimpleNamespace(
                AIMessage=_FakeAIMessage,
                HumanMessage=_FakeHumanMessage,
                SystemMessage=_FakeSystemMessage,
            ),
            "langchain_openai": SimpleNamespace(ChatOpenAI=_FakeChatOpenAI),
        },
    ), patch(
        "host_brain.app.prompt_builder.extract_langchain_content_text",
        return_value="clean-content",
    ):
        adapter = _SubAgentGatewayAdapter(
            provider_name="OpenAI",
            model_id="model-1",
            api_key="secret",
            timeout_ms=2500,
            base_url_override="https://example.com",
        )
        response = adapter.complete([
            {"role": "system", "content": "sys"},
            {"role": "assistant", "content": "assistant"},
            {"role": "user", "content": "user"},
            {"role": "user", "content": ""},
            "ignored",
        ])

    assert adapter.provider_name == "openai"
    assert response == {"content": "clean-content"}
    assert _FakeChatOpenAI.init_kwargs == {
        "model": "model-1",
        "api_key": "secret",
        "base_url": "https://example.com",
        "timeout": 2.5,
        "max_retries": 1,
        "temperature": 0.3,
    }
    assert [type(item).__name__ for item in _FakeChatOpenAI.last_messages] == [
        "_FakeSystemMessage",
        "_FakeAIMessage",
        "_FakeHumanMessage",
    ]


def test_build_subagent_gateway_from_runtime_handles_basic_branches(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class _FakeAdapter:
        def __init__(self, **kwargs) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(top_agent, "_SubAgentGatewayAdapter", _FakeAdapter)

    assert _build_subagent_gateway_from_runtime({"available": False}) is None
    assert _build_subagent_gateway_from_runtime({"available": True, "provider_name": "", "model_id": "m"}) is None

    gateway = _build_subagent_gateway_from_runtime({
        "available": True,
        "provider_name": "mock",
        "model_id": "model-1",
        "llm_api_key": "secret",
        "timeout_ms": 1200,
        "base_url_override": "https://example.com",
    })

    assert isinstance(gateway, _FakeAdapter)
    assert captured["provider_name"] == "mock"
    assert captured["timeout_ms"] == 1200


def test_build_subagent_gateway_from_runtime_swallow_adapter_error(monkeypatch) -> None:
    class _BoomAdapter:
        def __init__(self, **_kwargs) -> None:
            raise RuntimeError("boom")

    monkeypatch.setattr(top_agent, "_SubAgentGatewayAdapter", _BoomAdapter)

    gateway = _build_subagent_gateway_from_runtime({
        "available": True,
        "provider_name": "mock",
        "model_id": "model-1",
    })

    assert gateway is None


def test_detect_recent_turn_query_role_uses_regex_order(monkeypatch) -> None:
    monkeypatch.setattr(top_agent, "_RECENT_TURN_USER_QUERY_RE", re.compile("user"))
    monkeypatch.setattr(top_agent, "_RECENT_TURN_ASSISTANT_QUERY_RE", re.compile("assistant"))
    monkeypatch.setattr(top_agent, "_RECENT_TURN_GENERIC_QUERY_RE", re.compile("generic"))

    assert _detect_recent_turn_query_role("user question") == "user"
    assert _detect_recent_turn_query_role("assistant question") == "assistant"
    assert _detect_recent_turn_query_role("generic question") == "both"
    assert _detect_recent_turn_query_role("  ") is None


def test_maybe_build_phatic_fastpath_reply_covers_blank_presence_ack_and_fallback(monkeypatch) -> None:
    monkeypatch.setattr(top_agent, "_CONTINUATION_QUERY_RE", re.compile(r"^continue$"))
    monkeypatch.setattr(top_agent, "_GREETING_FASTPATH_RE", re.compile(r"^hello$"))
    monkeypatch.setattr(top_agent, "_PRESENCE_FASTPATH_RE", re.compile(r"^presence$"))
    monkeypatch.setattr(top_agent, "_ACK_FASTPATH_RE", re.compile(r"^ack$"))

    with patch("host_brain.app.top_agent._load_continuity_turns", return_value=[]), patch(
        "host_brain.app.top_agent._looks_like_active_task_context",
        return_value=False,
    ):
        assert top_agent._maybe_build_phatic_fastpath_reply(
            request_id="req",
            session_id="session",
            person_id="person",
            user_message=" ",
            render_target_lang="zh",
        ) is None
        presence = top_agent._maybe_build_phatic_fastpath_reply(
            request_id="req",
            session_id="session",
            person_id="person",
            user_message="presence",
            render_target_lang="zh",
        )
        ack = top_agent._maybe_build_phatic_fastpath_reply(
            request_id="req",
            session_id="session",
            person_id="person",
            user_message="ack",
            render_target_lang="zh",
        )
        unknown = top_agent._maybe_build_phatic_fastpath_reply(
            request_id="req",
            session_id="session",
            person_id="person",
            user_message="other",
            render_target_lang="zh",
        )

    assert "确认你在不在" in str(presence)
    assert "轻量确认" in str(ack)
    assert unknown is None


def test_maybe_build_phatic_fastpath_reply_covers_followup_and_active_ack(monkeypatch) -> None:
    monkeypatch.setattr(top_agent, "_CONTINUATION_QUERY_RE", re.compile(r"^continue$"))
    monkeypatch.setattr(top_agent, "_GREETING_FASTPATH_RE", re.compile(r"^hello$"))
    monkeypatch.setattr(top_agent, "_PRESENCE_FASTPATH_RE", re.compile(r"^presence$"))
    monkeypatch.setattr(top_agent, "_ACK_FASTPATH_RE", re.compile(r"^ack$"))

    turns = [{"role": "assistant", "content": "last reply"}]
    with patch("host_brain.app.top_agent._load_continuity_turns", return_value=turns), patch(
        "host_brain.app.top_agent._looks_like_active_task_context",
        return_value=False,
    ):
        followup = top_agent._maybe_build_phatic_fastpath_reply(
            request_id="req",
            session_id="session",
            person_id="person",
            user_message="continue",
            render_target_lang="zh",
        )

    with patch("host_brain.app.top_agent._load_continuity_turns", return_value=turns), patch(
        "host_brain.app.top_agent._looks_like_active_task_context",
        return_value=True,
    ):
        blocked_ack = top_agent._maybe_build_phatic_fastpath_reply(
            request_id="req",
            session_id="session",
            person_id="person",
            user_message="ack",
            render_target_lang="zh",
        )

    assert "普通延续接话" in str(followup)
    assert "last reply" in str(followup)
    assert blocked_ack is None


def test_maybe_build_task_progress_fastpath_returns_none_without_keyword(monkeypatch) -> None:
    monkeypatch.setattr(top_agent, "_TASK_PROGRESS_QUERY_RE", re.compile("progress"))
    monkeypatch.setattr(top_agent, "_TASK_FAILURE_QUERY_RE", re.compile("fail"))
    monkeypatch.setattr(top_agent, "_TASK_STEP_INDEX_RE", re.compile(r"step(\d+)"))

    assert _maybe_build_task_progress_fastpath(
        request_id="req",
        session_id="session",
        person_id="person",
        user_message="hello",
    ) is None


def test_maybe_build_task_progress_fastpath_handles_missing_summary(monkeypatch) -> None:
    monkeypatch.setattr(top_agent, "_TASK_PROGRESS_QUERY_RE", re.compile("progress"))
    monkeypatch.setattr(top_agent, "_TASK_FAILURE_QUERY_RE", re.compile("fail"))
    monkeypatch.setattr(top_agent, "_TASK_STEP_INDEX_RE", re.compile(r"step(\d+)"))

    class _Registry:
        def summarize_for_chat(self, **_kwargs):
            return None

    with patch("host_brain.app.dependencies.get_runtime_task_registry", return_value=_Registry()):
        result = _maybe_build_task_progress_fastpath(
            request_id="req",
            session_id="session",
            person_id="person",
            user_message="progress",
        )

    assert result is not None


def test_maybe_build_task_progress_fastpath_covers_success_and_global_failure(monkeypatch) -> None:
    monkeypatch.setattr(top_agent, "_TASK_PROGRESS_QUERY_RE", re.compile("progress"))
    monkeypatch.setattr(top_agent, "_TASK_FAILURE_QUERY_RE", re.compile("fail"))
    monkeypatch.setattr(top_agent, "_TASK_STEP_INDEX_RE", re.compile(r"step(\d+)"))

    class _SuccessRegistry:
        def summarize_for_chat(self, **_kwargs):
            return {
                "status": "COMPLETED",
                "target_id": "plan_execution",
                "child_tasks": [
                    {
                        "target_id": "browser_navigate",
                        "status": "COMPLETED",
                        "detail": "open page",
                        "meta": {"step_index": 2, "description": "open page"},
                    }
                ],
            }

    class _FailureRegistry:
        def summarize_for_chat(self, **_kwargs):
            return {
                "status": "FAILED",
                "target_id": "deploy",
                "error": "BOOM",
                "recovery_result_text": "retry later",
                "child_tasks": [],
            }

    with patch("host_brain.app.dependencies.get_runtime_task_registry", return_value=_SuccessRegistry()):
        success = _maybe_build_task_progress_fastpath(
            request_id="req",
            session_id="session",
            person_id="person",
            user_message="progress step2",
        )

    with patch("host_brain.app.dependencies.get_runtime_task_registry", return_value=_FailureRegistry()):
        failure = _maybe_build_task_progress_fastpath(
            request_id="req",
            session_id="session",
            person_id="person",
            user_message="fail",
        )

    assert "browser_navigate" in str(success)
    assert "deploy" in str(failure)
    assert "BOOM" in str(failure)


def test_maybe_build_recent_turn_answer_handles_lookup_branches() -> None:
    rows = [{"payload": {"input_preview": "u", "reply_preview": "a"}}]
    turns = [
        {"role": "user", "content": "latest user"},
        {"role": "assistant", "content": "latest assistant"},
    ]

    with patch("host_brain.app.top_agent._detect_recent_turn_query_role", return_value=None):
        assert _maybe_build_recent_turn_answer(
            request_id="req",
            session_id="session",
            person_id="person",
            user_message="ignored",
            render_target_lang="zh",
        ) is None

    with patch("host_brain.app.top_agent._detect_recent_turn_query_role", return_value="user"), patch(
        "host_brain.app.top_agent_prompt._fetch_continuity_rows",
        side_effect=RuntimeError("boom"),
    ):
        assert _maybe_build_recent_turn_answer(
            request_id="req",
            session_id="session",
            person_id="person",
            user_message="ignored",
            render_target_lang="zh",
        ) is None

    with patch("host_brain.app.top_agent_prompt._fetch_continuity_rows", return_value=rows), patch(
        "host_brain.app.top_agent_prompt._voice_rows_to_turns",
        return_value=turns,
    ), patch("host_brain.app.dependencies.get_ledger", return_value="LEDGER"), patch(
        "host_brain.app.top_agent._detect_recent_turn_query_role",
        side_effect=["user", "assistant", "both"],
    ):
        user_answer = _maybe_build_recent_turn_answer(
            request_id="req",
            session_id="session",
            person_id="person",
            user_message="ignored",
            render_target_lang="zh",
        )
        assistant_answer = _maybe_build_recent_turn_answer(
            request_id="req",
            session_id="session",
            person_id="person",
            user_message="ignored",
            render_target_lang="zh",
        )
        both_answer = _maybe_build_recent_turn_answer(
            request_id="req",
            session_id="session",
            person_id="person",
            user_message="ignored",
            render_target_lang="zh",
        )

    assert "latest user" in str(user_answer)
    assert "latest assistant" in str(assistant_answer)
    assert "latest user" in str(both_answer) and "latest assistant" in str(both_answer)


def test_infer_emotion_tag_for_tts_uses_keyword_map(monkeypatch) -> None:
    monkeypatch.setattr(top_agent, "_EMOTION_INFER_KEYWORDS", [("HAPPY", "happy"), ("SAD", "sad")])

    assert _infer_emotion_tag_for_tts("so happy today", "NEUTRAL") == "HAPPY"
    assert _infer_emotion_tag_for_tts("sad", "ANGRY") == "ANGRY"


def test_infer_emotion_tag_for_tts_supports_japanese_affection_keywords() -> None:
    assert _infer_emotion_tag_for_tts("会いたかったし、大好きだよ。", "NEUTRAL") == "LOVING"


def test_should_buffer_stream_output_honors_flag_and_reflection(monkeypatch) -> None:
    monkeypatch.setenv("HOST_STREAM_REFLECTION_BUFFER_ENABLED", "false")
    assert _should_buffer_stream_output(emotion_context={}, bond_summary={}) is False

    monkeypatch.setenv("HOST_STREAM_REFLECTION_BUFFER_ENABLED", "true")

    class _Reflection:
        def should_reflect(self, **_kwargs) -> bool:
            return True

    with patch("host_brain.app.dependencies.get_reflection_chain", return_value=_Reflection()):
        assert _should_buffer_stream_output(
            emotion_context={"emotion_tag": "HAPPY", "turn_count": 3},
            bond_summary={"intent": "chat", "stage": "FRIEND"},
        ) is True

    with patch("host_brain.app.dependencies.get_reflection_chain", side_effect=RuntimeError("boom")):
        assert _should_buffer_stream_output(emotion_context={}, bond_summary={}) is False


def test_extract_delegate_from_llm_output_parses_marker_and_json() -> None:
    agent_id, task, template = _extract_delegate_from_llm_output(
        "before [DELEGATE:code_agent:do work] after",
    )
    assert (agent_id, task) == ("code_agent", "do work")
    assert "{agent_result}" in str(template)

    agent_id, task, template = _extract_delegate_from_llm_output(
        '```json\n{"delegate":{"agent":"code_agent","task":"ship it"},"reply_template":"答复：{agent_result}"}\n```',
    )
    assert (agent_id, task, template) == ("code_agent", "ship it", "答复：{agent_result}")

    assert _extract_delegate_from_llm_output('{"delegate":{"agent":"unknown","task":"x"}}') == (None, None, None)


def test_extract_visible_text_reads_text_zh_and_falls_back() -> None:
    assert _extract_visible_text('{"narrative":{"text_zh":"你好"}}') == "你好"
    assert _extract_visible_text("raw output") == "raw output"
    assert _extract_visible_text("") == ""


def test_normalize_retrieval_summary_marks_degraded_from_error() -> None:
    normalized = _normalize_retrieval_summary_for_audit({
        "error": "timeout",
        "route_rows": [{"store": "episodic"}],
    })

    assert normalized["status"] == "DEGRADED"
    assert normalized["degraded"] is True
    assert normalized["route_count"] == 1


def test_resolve_retrieval_timeout_seconds_clamps_invalid_value(monkeypatch) -> None:
    monkeypatch.setenv("HOST_TOP_AGENT_RETRIEVAL_TIMEOUT_MS", "abc")
    assert _resolve_retrieval_timeout_seconds() == 20.0

    monkeypatch.setenv("HOST_TOP_AGENT_RETRIEVAL_TIMEOUT_MS", "100")
    assert _resolve_retrieval_timeout_seconds() == 0.5
