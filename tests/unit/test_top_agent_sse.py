from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from host_brain.app.top_agent import (
    _maybe_build_task_progress_fastpath,
    _normalize_retrieval_summary_for_audit,
    _resolve_retrieval_timeout_seconds,
    top_agent_sse_generator,
)


def _run(coro):
    return asyncio.run(coro)


async def _collect_events(async_iterable) -> list[str]:
    events: list[str] = []
    async for item in async_iterable:
        events.append(item)
    return events


async def _empty_chunks(**_kwargs):  # noqa: ANN001
    if False:
        yield ""


async def _fake_retrieval(**_kwargs):  # noqa: ANN001
    return {"status": "OK"}, []


async def _fake_state_arbiter(**_kwargs):  # noqa: ANN001
    return {}, None


async def _fake_runtime(**_kwargs):  # noqa: ANN001
    return {
        "available": True,
        "provider_name": "mock",
        "model_id": "mock-model",
        "timeout_ms": 1000,
        "llm_api_key": "",
        "base_url_override": "",
    }


class _DelegateDispatcher:
    def __init__(self) -> None:
        self.requests = []

    async def dispatch(self, req):  # noqa: ANN001
        self.requests.append(req)
        from types import SimpleNamespace

        return SimpleNamespace(success=True, agent_id=req.agent_id, result="delegated answer", latency_ms=3)


def _fake_apply_response_intelligence(**_kwargs):  # noqa: ANN001
    return "final answer", {"reflection": {"enabled": False}}


async def _noop_async(*_args, **_kwargs):  # noqa: ANN001
    return None


class _FakeRecallPackBuilder:
    def build(self, *_args, **_kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("recall pack should not be built for empty retrieval")


class _FakeBondQueue:
    def enqueue(self, _payload):  # noqa: ANN001
        return {"status": "QUEUED"}


class _FakeBondWriter:
    @staticmethod
    def classify_interaction(*_args, **_kwargs):  # noqa: ANN001, ANN003
        return "NOOP"

    def __init__(self, *_args, **_kwargs):  # noqa: ANN002, ANN003
        pass

    def apply_update(self, **_kwargs):  # noqa: ANN003
        return None


class TestTopAgentSseGenerator:
    def test_rejects_blank_message_at_airlock(self) -> None:
        request_body = SimpleNamespace(message="   ", session_id="session-001", thread_id=None)
        metrics = MagicMock()

        events = _run(
            _collect_events(
                top_agent_sse_generator(
                    request_body=request_body,
                    request_id="req-airlock",
                    session_id="session-001",
                    ts_ms=1000,
                    intent_draft={"id": "intent-001"},
                    emotion_context={},
                    emotion_snapshot={},
                    governor_result={"decision": "APPROVE"},
                    intent_id="intent-001",
                    metrics=metrics,
                ),
            ),
        )

        assert len(events) == 1
        body = json.loads(events[0].removeprefix("data: ").strip())
        assert body["decision"] == "REJECT"
        assert body["reason_code"] == "AIRLOCK_EMPTY_MESSAGE"

    def test_rejects_when_governor_blocks(self) -> None:
        request_body = SimpleNamespace(message="hello", session_id="session-001", thread_id=None)
        metrics = MagicMock()

        with patch(
            "host_brain.app.top_agent.chat_lite_governor",
            return_value={"decision": "REJECT", "reason_code": "RATE_LIMIT"},
        ):
            events = _run(
                _collect_events(
                    top_agent_sse_generator(
                        request_body=request_body,
                        request_id="req-governor",
                        session_id="session-001",
                        ts_ms=1000,
                        intent_draft={"id": "intent-001"},
                        emotion_context={},
                        emotion_snapshot={},
                        governor_result={"decision": "APPROVE"},
                        intent_id="intent-001",
                        metrics=metrics,
                    ),
                ),
            )

        assert [json.loads(event.removeprefix("data: ").strip())["phase"] for event in events] == [
            "PRESENCE",
            "COMMIT",
        ]
        commit = json.loads(events[-1].removeprefix("data: ").strip())
        assert commit["decision"] == "REJECT"
        assert commit["reason_code"] == "RATE_LIMIT"

    def test_recent_turn_query_is_answered_from_continuity_without_llm(self) -> None:
        request_body = SimpleNamespace(message="上一句是什么", session_id="session-001", thread_id=None)
        metrics = MagicMock()
        captured = {}

        class _FakeLedger:
            def fetch_by_time_range(self, table: str, *, limit: int = 100, **_: object) -> list[dict[str, object]]:
                assert table == "voice_event_log"
                assert limit == 2000
                return [
                    {
                        "request_id": "req-old-1",
                        "session_id": "session-001",
                        "event_type": "INPUT_COMPLETED",
                        "payload": {
                            "person_id": "default",
                            "input_preview": "你还记得我吗",
                            "reply_preview": "我当然记得你。",
                        },
                    }
                ]

            def log_voice_event(self, _row):  # noqa: ANN001
                return None

        async def _fake_chat_agent(chat_input):  # noqa: ANN001
            captured["chat_input"] = chat_input
            yield "data: " + json.dumps({
                "phase": "COMMIT",
                "done": True,
                "decision": "APPROVE",
                "narrative": f"【chat_agent】{chat_input.sub_agent_context.result_text}",
                "request_id": chat_input.request_id,
                "audit_refs": {"path": "chat_agent:direct"},
            }, ensure_ascii=False) + "\n\n"

        with patch(
            "host_brain.app.top_agent.chat_lite_governor",
            return_value={"decision": "APPROVE", "reason_code": None, "intent_id": "intent-001"},
        ), patch(
            "host_brain.app.dependencies.get_ledger",
            return_value=_FakeLedger(),
        ), patch(
            "host_brain.app.chat_agent.chat_agent_sse_generator",
            new=_fake_chat_agent,
        ), patch(
            "host_brain.app.top_agent._async_post_process",
            new=_noop_async,
        ):
            events = _run(
                _collect_events(
                    top_agent_sse_generator(
                        request_body=request_body,
                        request_id="req-recent-turn",
                        session_id="session-001",
                        ts_ms=1000,
                        intent_draft={"id": "intent-001"},
                        emotion_context={"emotion_tag": "NEUTRAL", "emotion_intensity": 0.1},
                        emotion_snapshot={"emotion_tag": "NEUTRAL"},
                        governor_result={"decision": "APPROVE"},
                        intent_id="intent-001",
                        metrics=metrics,
                    ),
                ),
            )

        commit = json.loads(events[-1].removeprefix("data: ").strip())
        assert commit["decision"] == "APPROVE"
        assert commit["audit_refs"]["path"] == "chat_agent:direct"
        assert captured["chat_input"].sub_agent_context is not None
        assert captured["chat_input"].sub_agent_context.agent_id == "recent_turn_direct"
        assert "已确认事实：" in commit["narrative"]
        assert "不要再说看不到或没看到" in commit["narrative"]

    def test_greeting_fastpath_avoids_repeating_same_reply(self) -> None:
        from host_brain.app.top_agent import _maybe_build_phatic_fastpath_reply

        class _FakeLedger:
            def fetch_by_time_range(self, table: str, *, limit: int = 100, **_: object) -> list[dict[str, object]]:
                assert table == "voice_event_log"
                assert limit == 2000
                return [
                    {
                        "request_id": "req-old-1",
                        "session_id": "session-001",
                        "event_type": "INPUT_COMPLETED",
                        "payload": {
                            "person_id": "default",
                            "input_preview": "你好啊",
                            "reply_preview": "嗯，你好呀……我在呢。",
                        },
                    }
                ]

        with patch("host_brain.app.dependencies.get_ledger", return_value=_FakeLedger()):
            reply = _maybe_build_phatic_fastpath_reply(
                request_id="req-greeting-fastpath",
                session_id="session-001",
                person_id="default",
                user_message="你好啊",
                render_target_lang="zh",
            )

        assert reply is not None
        assert reply != "嗯，你好呀……我在呢。"

    def test_then_with_active_task_context_does_not_use_phatic_fastpath(self) -> None:
        from host_brain.app.top_agent import _maybe_build_phatic_fastpath_reply

        class _FakeLedger:
            def fetch_by_time_range(self, table: str, *, limit: int = 100, **_: object) -> list[dict[str, object]]:
                assert table == "voice_event_log"
                assert limit == 2000
                return [
                    {
                        "request_id": "req-old-1",
                        "session_id": "session-001",
                        "event_type": "INPUT_COMPLETED",
                        "payload": {
                            "person_id": "default",
                            "input_preview": "对啊",
                            "reply_preview": "嗯，对啊……那，有什么具体的想法吗？比如，想从哪里开始着手呢？",
                        },
                    }
                ]

        with patch("host_brain.app.dependencies.get_ledger", return_value=_FakeLedger()):
            reply = _maybe_build_phatic_fastpath_reply(
                request_id="req-then-continuation",
                session_id="session-001",
                person_id="default",
                user_message="然后",
                render_target_lang="zh",
            )

        assert reply is None

    def test_success_path_hands_off_to_dispatch_agent(self, monkeypatch) -> None:
        monkeypatch.setenv("HOST_STREAM_REFLECTION_BUFFER_ENABLED", "false")
        monkeypatch.setenv("HOST_STREAM_TTS_ENABLED", "false")

        request_body = SimpleNamespace(message="hello", session_id="session-001", thread_id="thread-001")
        metrics = MagicMock()
        captured: dict[str, object] = {}

        async def _fake_dispatch_agent(**kwargs):  # noqa: ANN001
            captured.update(kwargs)
            yield "data: " + json.dumps({
                "phase": "COMMIT",
                "done": True,
                "decision": "APPROVE",
                "narrative": "final answer",
                "request_id": kwargs.get("request_id"),
                "audit_refs": {"path": "chat_agent:direct"},
            }, ensure_ascii=False) + "\n\n"

        with patch(
            "host_brain.app.top_agent.chat_lite_governor",
            return_value={"decision": "APPROVE", "reason_code": None, "intent_id": "intent-001"},
        ), patch(
            "host_brain.app.chat_retrieval_helpers._run_retrieval",
            new=_fake_retrieval,
        ), patch(
            "host_brain.app.prompt_builder.infer_render_target_lang",
            return_value="ja",
        ), patch(
            "host_brain.app.state_arbiter_queries._resolve_state_arbiter_bond_fact",
            new=_fake_state_arbiter,
        ), patch(
            "host_brain.app.dependencies.get_recall_pack_builder",
            return_value=_FakeRecallPackBuilder(),
        ), patch(
            "host_brain.app.graph_config_builder.resolve_host_llm_runtime_async",
            new=_fake_runtime,
        ), patch(
            "host_brain.app.dispatch_agent.dispatch_agent_sse_generator",
            new=_fake_dispatch_agent,
        ), patch(
            "host_brain.app.top_agent._maybe_build_phatic_fastpath_reply",
            return_value=None,
        ):
            events = _run(
                _collect_events(
                    top_agent_sse_generator(
                        request_body=request_body,
                        request_id="req-success",
                        session_id="session-001",
                        ts_ms=1000,
                        intent_draft={"id": "intent-001"},
                        emotion_context={"emotion_tag": "NEUTRAL", "emotion_intensity": 0.5},
                        emotion_snapshot={"emotion_tag": "NEUTRAL"},
                        governor_result={"decision": "APPROVE"},
                        intent_id="intent-001",
                        metrics=metrics,
                    ),
                ),
            )

        phases = [json.loads(event.removeprefix("data: ").strip())["phase"] for event in events]
        assert phases[0] == "PRESENCE"
        assert phases[-1] == "COMMIT"
        commit = json.loads(events[-1].removeprefix("data: ").strip())
        assert commit["decision"] == "APPROVE"
        assert commit["narrative"] == "final answer"
        assert commit["audit_refs"]["path"] == "chat_agent:direct"
        assert captured["person_id"] == "default"
        assert captured["render_target_lang"] == "ja"

    def test_top_agent_hands_off_to_dispatch_agent(self, monkeypatch) -> None:
        monkeypatch.setenv("HOST_STREAM_REFLECTION_BUFFER_ENABLED", "false")
        monkeypatch.setenv("HOST_STREAM_TTS_ENABLED", "false")

        request_body = SimpleNamespace(message="帮我想个贪吃蛇思路", session_id="session-001", thread_id=None)
        metrics = MagicMock()
        captured = {}

        async def _fake_dispatch_agent(**kwargs):  # noqa: ANN001
            captured.update(kwargs)
            yield "data: " + json.dumps({
                "phase": "COMMIT",
                "done": True,
                "decision": "APPROVE",
                "narrative": "delegated answer",
                "request_id": kwargs.get("request_id"),
                "audit_refs": {"path": "chat_agent:delegate"},
            }, ensure_ascii=False) + "\n\n"

        with patch(
            "host_brain.app.top_agent.chat_lite_governor",
            return_value={"decision": "APPROVE", "reason_code": None, "intent_id": "intent-001"},
        ), patch(
            "host_brain.app.chat_retrieval_helpers._run_retrieval",
            new=_fake_retrieval,
        ), patch(
            "host_brain.app.prompt_builder.infer_render_target_lang",
            return_value="zh",
        ), patch(
            "host_brain.app.state_arbiter_queries._resolve_state_arbiter_bond_fact",
            new=_fake_state_arbiter,
        ), patch(
            "host_brain.app.dependencies.get_recall_pack_builder",
            return_value=_FakeRecallPackBuilder(),
        ), patch(
            "host_brain.app.top_agent_prompt.build_top_agent_prompt",
            return_value=("PROMPT_TEXT", "SYSTEM_PROMPT", {"temperature": 0.2}),
        ), patch(
            "host_brain.app.graph_config_builder.resolve_host_llm_runtime_async",
            new=_fake_runtime,
        ), patch(
            "host_brain.app.dispatch_agent.dispatch_agent_sse_generator",
            new=_fake_dispatch_agent,
        ), patch(
            "host_brain.app.intelligent_runtime.apply_response_intelligence",
            new=_fake_apply_response_intelligence,
        ), patch(
            "host_brain.app.top_agent._maybe_build_phatic_fastpath_reply",
            return_value=None,
        ), patch(
            "host_brain.app.top_agent._async_post_process",
            new=_noop_async,
        ), patch(
            "host_brain.app.dependencies.get_episodic_store",
            return_value=None,
        ), patch(
            "host_brain.app.dependencies.get_bond_update_queue",
            return_value=_FakeBondQueue(),
        ), patch(
            "host_brain.app.vts_emotion_driver._drive_stream_vts_emotion",
            new=_noop_async,
        ), patch("host_brain.bond.bond_dynamics.BondDynamicsWriter", _FakeBondWriter), patch(
            "host_brain.bond.user_identity.resolve_bond_user_id",
            return_value="user-001",
        ):
            events = _run(
                _collect_events(
                    top_agent_sse_generator(
                        request_body=request_body,
                        request_id="req-delegate",
                        session_id="session-001",
                        ts_ms=1000,
                        intent_draft={"id": "intent-001"},
                        emotion_context={"emotion_tag": "NEUTRAL", "emotion_intensity": 0.5},
                        emotion_snapshot={"emotion_tag": "NEUTRAL"},
                        governor_result={"decision": "APPROVE"},
                        intent_id="intent-001",
                        metrics=metrics,
                    ),
                ),
            )

        commit = json.loads(events[-1].removeprefix("data: ").strip())
        assert commit["decision"] == "APPROVE"
        assert captured["person_id"] == "user-001"
        assert captured["sanitized_message"] == "帮我想个贪吃蛇思路"

    def test_delegate_result_is_rendered_without_internal_react_marker(self, monkeypatch) -> None:
        monkeypatch.setenv("HOST_STREAM_REFLECTION_BUFFER_ENABLED", "false")
        monkeypatch.setenv("HOST_STREAM_TTS_ENABLED", "false")

        request_body = SimpleNamespace(message="给我个贪吃蛇大致思路", session_id="session-001", thread_id=None)
        metrics = MagicMock()

        class _RawDelegateDispatcher:
            async def dispatch(self, req):  # noqa: ANN001
                from types import SimpleNamespace
                return SimpleNamespace(
                    success=True,
                    agent_id=req.agent_id,
                    result='```json\n{"narrative":{"text":"[ReAct 降级] agent_id=code_agent, 无 LLM gateway"},"intent_draft":{},"meta":{"draft_only":true}}\n```',
                    latency_ms=3,
                )

        async def _delegate_chunks(**_kwargs):  # noqa: ANN001
            yield '{"delegate":{"agent":"code_agent","task":"给出贪吃蛇实现思路"},"reply_template":"嗯……贪吃蛇游戏的思路啊……{agent_result}"}'

        with patch(
            "host_brain.app.top_agent.chat_lite_governor",
            return_value={"decision": "APPROVE", "reason_code": None, "intent_id": "intent-001"},
        ), patch(
            "host_brain.app.chat_retrieval_helpers._run_retrieval",
            new=_fake_retrieval,
        ), patch(
            "host_brain.app.prompt_builder.infer_render_target_lang",
            return_value="zh",
        ), patch(
            "host_brain.app.state_arbiter_queries._resolve_state_arbiter_bond_fact",
            new=_fake_state_arbiter,
        ), patch(
            "host_brain.app.dependencies.get_recall_pack_builder",
            return_value=_FakeRecallPackBuilder(),
        ), patch(
            "host_brain.app.top_agent_prompt.build_top_agent_prompt",
            return_value=("PROMPT_TEXT", "SYSTEM_PROMPT", {"temperature": 0.2}),
        ), patch(
            "host_brain.app.graph_config_builder.resolve_host_llm_runtime_async",
            new=_fake_runtime,
        ), patch(
            "host_brain.integrations.llm_helpers.astream_llm_chunks",
            new=_delegate_chunks,
        ), patch(
            "host_brain.app.top_agent.get_sub_agent_dispatcher",
            return_value=_RawDelegateDispatcher(),
        ), patch(
            "host_brain.app.top_agent._build_subagent_gateway_from_runtime",
            return_value="GW",
        ), patch(
            "host_brain.app.intelligent_runtime.apply_response_intelligence",
            new=_fake_apply_response_intelligence,
        ), patch(
            "host_brain.app.top_agent._maybe_build_phatic_fastpath_reply",
            return_value=None,
        ), patch(
            "host_brain.app.top_agent._async_post_process",
            new=_noop_async,
        ), patch(
            "host_brain.app.dependencies.get_episodic_store",
            return_value=None,
        ), patch(
            "host_brain.app.dependencies.get_bond_update_queue",
            return_value=_FakeBondQueue(),
        ), patch(
            "host_brain.app.vts_emotion_driver._drive_stream_vts_emotion",
            new=_noop_async,
        ), patch("host_brain.bond.bond_dynamics.BondDynamicsWriter", _FakeBondWriter), patch(
            "host_brain.bond.user_identity.resolve_bond_user_id",
            return_value="user-001",
        ):
            events = _run(
                _collect_events(
                    top_agent_sse_generator(
                        request_body=request_body,
                        request_id="req-delegate-clean",
                        session_id="session-001",
                        ts_ms=1000,
                        intent_draft={"id": "intent-001"},
                        emotion_context={"emotion_tag": "NEUTRAL", "emotion_intensity": 0.5},
                        emotion_snapshot={"emotion_tag": "NEUTRAL"},
                        governor_result={"decision": "APPROVE"},
                        intent_id="intent-001",
                        metrics=metrics,
                    ),
                ),
            )

        commit = json.loads(events[-1].removeprefix("data: ").strip())
        assert commit["decision"] == "APPROVE"
        assert "ReAct 降级" not in commit["narrative"]
        assert "draft_only" not in commit["narrative"]


def test_normalize_retrieval_summary_preserves_skipped_status() -> None:
    normalized = _normalize_retrieval_summary_for_audit({"status": "SKIPPED"})

    assert normalized["status"] == "SKIPPED"
    assert normalized["candidate_count"] == 0
    assert normalized["route_count"] == 0


def test_normalize_retrieval_summary_defaults_empty_summary_to_skipped() -> None:
    normalized = _normalize_retrieval_summary_for_audit({})

    assert normalized["status"] == "SKIPPED"
    assert normalized["candidate_count"] == 0
    assert normalized["route_count"] == 0


def test_resolve_retrieval_timeout_seconds_defaults_to_20s(monkeypatch) -> None:
    monkeypatch.delenv("HOST_TOP_AGENT_RETRIEVAL_TIMEOUT_MS", raising=False)

    assert _resolve_retrieval_timeout_seconds() == 20.0


def test_resolve_retrieval_timeout_seconds_reads_env(monkeypatch) -> None:
    monkeypatch.setenv("HOST_TOP_AGENT_RETRIEVAL_TIMEOUT_MS", "12000")

    assert _resolve_retrieval_timeout_seconds() == 12.0



def test_task_progress_fastpath_bypasses_retrieval(monkeypatch) -> None:
    request_body = SimpleNamespace(message="上个任务进度怎么样了？", session_id="session-001", thread_id=None, reply_channel=None, suppress_edge_tts=None, suppress_edge_vts=None)
    metrics = MagicMock()

    class _Registry:
        def summarize_for_chat(self, **_kwargs):
            return {
                "status": "RUNNING",
                "target_id": "web_search",
                "detail": "我正在执行 web_search。",
                "progress_message": "我正在执行 web_search。",
                "recovery_result_text": "",
            }

    async def _empty_chunks(**_kwargs):  # noqa: ANN001
        if False:
            yield ""

    def _fake_apply_response_intelligence(**_kwargs):  # noqa: ANN001
        return "当前有个任务还在进行，是 web_search。", {"reflection": {"enabled": False}}

    monkeypatch.setenv("HOST_STREAM_REFLECTION_BUFFER_ENABLED", "false")
    monkeypatch.setenv("HOST_STREAM_TTS_ENABLED", "false")
    monkeypatch.setattr("host_brain.app.dependencies.get_runtime_task_registry", lambda: _Registry())
    monkeypatch.setattr("host_brain.integrations.llm_helpers.astream_llm_chunks", _empty_chunks)
    monkeypatch.setattr("host_brain.app.intelligent_runtime.apply_response_intelligence", _fake_apply_response_intelligence)
    monkeypatch.setattr("host_brain.app.graph_config_builder.resolve_host_llm_runtime_async", AsyncMock(return_value={"available": True, "provider_name": "mock", "model_id": "mock-model", "timeout_ms": 1000, "llm_api_key": "", "base_url_override": ""}))

    with patch("host_brain.app.top_agent.chat_lite_governor", return_value={"decision": "APPROVE", "reason_code": None}), \
         patch("host_brain.app.chat_retrieval_helpers._run_retrieval", side_effect=AssertionError("retrieval should be bypassed")):
        events = _run(
            _collect_events(
                top_agent_sse_generator(
                    request_body=request_body,
                    request_id="req-progress-fastpath",
                    session_id="session-001",
                    ts_ms=1000,
                    intent_draft={"id": "intent-001"},
                    emotion_context={},
                    emotion_snapshot={},
                    governor_result={"decision": "APPROVE"},
                    intent_id="intent-001",
                    metrics=metrics,
                )
            )
        )

    commit = json.loads(events[-1].removeprefix("data: ").strip())
    assert commit["decision"] == "APPROVE"
    assert commit["audit_refs"]["path"] == "top_agent:task_progress_direct"



def test_task_progress_fastpath_can_answer_failed_step_detail(monkeypatch) -> None:
    class _Registry:
        def summarize_for_chat(self, **_kwargs):
            return {
                "status": "FAILED",
                "target_id": "plan_execution",
                "child_tasks": [
                    {
                        "target_id": "browser_navigate",
                        "status": "FAILED",
                        "error": "BROWSER_UNAVAILABLE",
                        "recovery_result_text": "先安装浏览器后端，再重试。",
                        "meta": {"step_index": 2, "description": "打开网页 https://example.com"},
                    }
                ],
            }

    monkeypatch.setattr("host_brain.app.dependencies.get_runtime_task_registry", lambda: _Registry())

    answer = _maybe_build_task_progress_fastpath(
        request_id="req-test",
        session_id="session-test",
        person_id="person-test",
        user_message="蓝蓝，第2步为什么失败了？",
    )

    assert answer is not None
    assert "第2步是 browser_navigate" in answer or "刚才失败的是 browser_navigate" in answer
    assert "BROWSER_UNAVAILABLE" in answer


def test_phatic_fastpath_routes_through_chat_agent(monkeypatch) -> None:
    monkeypatch.setenv("HOST_STREAM_REFLECTION_BUFFER_ENABLED", "false")
    monkeypatch.setenv("HOST_STREAM_TTS_ENABLED", "false")

    request_body = SimpleNamespace(
        message="鍦ㄥ悧",
        session_id="session-001",
        thread_id="thread-001",
        reply_channel="GATEWAY",
    )
    metrics = MagicMock()
    captured: dict[str, object] = {}

    async def _fake_chat_agent(chat_input):  # noqa: ANN001
        captured["chat_input"] = chat_input
        yield "data: " + json.dumps({
            "phase": "COMMIT",
            "done": True,
            "decision": "APPROVE",
            "narrative": "phatic answer",
            "request_id": chat_input.request_id,
            "audit_refs": {"path": "chat_agent:phatic"},
        }, ensure_ascii=False) + "\n\n"

    with patch(
        "host_brain.app.top_agent.chat_lite_governor",
        return_value={"decision": "APPROVE", "reason_code": None, "intent_id": "intent-001"},
    ), patch(
        "host_brain.app.top_agent._maybe_build_task_progress_fastpath",
        return_value=None,
    ), patch(
        "host_brain.app.top_agent._maybe_build_recent_turn_answer",
        return_value=None,
    ), patch(
        "host_brain.app.top_agent._maybe_build_phatic_fastpath_reply",
        return_value="暖场回复",
    ), patch(
        "host_brain.app.chat_agent.chat_agent_sse_generator",
        new=_fake_chat_agent,
    ):
        events = _run(
            _collect_events(
                top_agent_sse_generator(
                    request_body=request_body,
                    request_id="req-phatic",
                    session_id="session-001",
                    ts_ms=1000,
                    intent_draft={"id": "intent-001"},
                    emotion_context={},
                    emotion_snapshot={},
                    governor_result={"decision": "APPROVE"},
                    intent_id="intent-001",
                    metrics=metrics,
                )
            )
        )

    commit = json.loads(events[-1].removeprefix("data: ").strip())
    assert commit["audit_refs"]["path"] == "chat_agent:phatic"
    assert captured["chat_input"].sub_agent_context.agent_id == "phatic_fastpath"
    assert captured["chat_input"].retrieval_summary["reason_code"] == "PHATIC_FASTPATH"
    assert captured["chat_input"].reply_depth_policy == "gateway"


def test_runtime_unavailable_rejects_after_preprocessing(monkeypatch) -> None:
    monkeypatch.setenv("HOST_STREAM_REFLECTION_BUFFER_ENABLED", "false")
    monkeypatch.setenv("HOST_STREAM_TTS_ENABLED", "false")

    request_body = SimpleNamespace(message="hello", session_id="session-001", thread_id=None)
    metrics = MagicMock()

    async def _fake_unavailable_runtime(**_kwargs):  # noqa: ANN001
        return {"available": False}

    pii_result = SimpleNamespace(
        scrubbed_text="hello",
        has_pii=True,
        total_detections=2,
        detections=[{"rule_id": "phone"}, {"rule_id": "email"}],
    )

    with patch(
        "host_brain.app.top_agent.chat_lite_governor",
        return_value={"decision": "APPROVE", "reason_code": None, "intent_id": "intent-001"},
    ), patch(
        "host_brain.airlock.pii_filter._check_channel",
        return_value=True,
    ), patch(
        "host_brain.airlock.pii_filter.pii_scrub_text",
        return_value=pii_result,
    ), patch(
        "host_brain.app.top_agent._maybe_build_task_progress_fastpath",
        return_value=None,
    ), patch(
        "host_brain.app.top_agent._maybe_build_recent_turn_answer",
        return_value=None,
    ), patch(
        "host_brain.app.top_agent._maybe_build_phatic_fastpath_reply",
        return_value=None,
    ), patch(
        "host_brain.app.chat_retrieval_helpers._run_retrieval",
        new=_fake_retrieval,
    ), patch(
        "host_brain.app.prompt_builder.infer_render_target_lang",
        return_value="zh",
    ), patch(
        "host_brain.app.state_arbiter_queries._resolve_state_arbiter_bond_fact",
        new=_fake_state_arbiter,
    ), patch(
        "host_brain.app.dependencies.get_recall_pack_builder",
        return_value=_FakeRecallPackBuilder(),
    ), patch(
        "host_brain.app.graph_config_builder.resolve_host_llm_runtime_async",
        new=_fake_unavailable_runtime,
    ):
        events = _run(
            _collect_events(
                top_agent_sse_generator(
                    request_body=request_body,
                    request_id="req-runtime-down",
                    session_id="session-001",
                    ts_ms=1000,
                    intent_draft={"id": "intent-001"},
                    emotion_context={},
                    emotion_snapshot={},
                    governor_result={"decision": "APPROVE"},
                    intent_id="intent-001",
                    metrics=metrics,
                )
            )
        )

    commit = json.loads(events[-1].removeprefix("data: ").strip())
    assert commit["decision"] == "REJECT"
    assert commit["reason_code"] == "LLM_UNAVAILABLE"


def test_retrieval_init_failure_and_recall_pack_still_dispatch(monkeypatch) -> None:
    monkeypatch.setenv("HOST_STREAM_REFLECTION_BUFFER_ENABLED", "false")
    monkeypatch.setenv("HOST_STREAM_TTS_ENABLED", "false")

    request_body = SimpleNamespace(message="hello", session_id="session-001", thread_id=None)
    metrics = MagicMock()
    captured: dict[str, object] = {}

    def _bad_retrieval(**_kwargs):  # noqa: ANN001
        return {"status": "OK"}, []

    class _Registry:
        def get_by_request_id(self, _request_id):  # noqa: ANN001
            return None

        def create_task(self, **_kwargs):  # noqa: ANN003
            raise RuntimeError("task init boom")

    class _FakePack:
        def get_slot(self, name: str):
            if name == "bond":
                return SimpleNamespace(content="bond recall slot")
            return None

    class _RecallBuilder:
        def build(self, recall_results, task_type):  # noqa: ANN001
            assert task_type == "chat"
            assert recall_results == {"bond": "bond memory"}
            return _FakePack()

    async def _fake_state_arbiter_with_bond(**_kwargs):  # noqa: ANN001
        return {
            "bond_stage": "FRIEND",
            "trend": "up",
            "capability_hint": "warm",
        }, {"kind": "bond", "content": "bond memory"}

    async def _fake_dispatch_agent(**kwargs):  # noqa: ANN001
        captured.update(kwargs)
        yield "data: " + json.dumps({
            "phase": "COMMIT",
            "done": True,
            "decision": "APPROVE",
            "narrative": "final answer",
            "request_id": kwargs.get("request_id"),
            "audit_refs": {"path": "dispatch_agent:direct"},
        }, ensure_ascii=False) + "\n\n"

    with patch(
        "host_brain.app.top_agent.chat_lite_governor",
        return_value={"decision": "APPROVE", "reason_code": None, "intent_id": "intent-001"},
    ), patch(
        "host_brain.app.top_agent._maybe_build_task_progress_fastpath",
        return_value=None,
    ), patch(
        "host_brain.app.top_agent._maybe_build_recent_turn_answer",
        return_value=None,
    ), patch(
        "host_brain.app.top_agent._maybe_build_phatic_fastpath_reply",
        return_value=None,
    ), patch(
        "host_brain.app.dependencies.get_runtime_task_registry",
        return_value=_Registry(),
    ), patch(
        "host_brain.app.chat_retrieval_helpers._run_retrieval",
        new=_bad_retrieval,
    ), patch(
        "host_brain.app.prompt_builder.infer_render_target_lang",
        return_value="zh",
    ), patch(
        "host_brain.app.state_arbiter_queries._resolve_state_arbiter_bond_fact",
        new=_fake_state_arbiter_with_bond,
    ), patch(
        "host_brain.app.dependencies.get_recall_pack_builder",
        return_value=_RecallBuilder(),
    ), patch(
        "host_brain.app.schemas.recall_pack.RECALL_SLOT_PRIORITY",
        ["bond"],
    ), patch(
        "host_brain.app.graph_config_builder.resolve_host_llm_runtime_async",
        new=_fake_runtime,
    ), patch(
        "host_brain.app.dispatch_agent.dispatch_agent_sse_generator",
        new=_fake_dispatch_agent,
    ):
        events = _run(
            _collect_events(
                top_agent_sse_generator(
                    request_body=request_body,
                    request_id="req-retr-init-fail",
                    session_id="session-001",
                    ts_ms=1000,
                    intent_draft={"id": "intent-001"},
                    emotion_context={},
                    emotion_snapshot={},
                    governor_result={"decision": "APPROVE"},
                    intent_id="intent-001",
                    metrics=metrics,
                )
            )
        )

    commit = json.loads(events[-1].removeprefix("data: ").strip())
    assert commit["decision"] == "APPROVE"
    assert captured["retrieval_summary"]["status"] == "SKIPPED"
    assert captured["bond_summary"]["stage"] == "FRIEND"
    assert captured["recall_slot_text"]["bond"] == "bond recall slot"


def test_retrieval_failure_degrades_summary_before_dispatch(monkeypatch) -> None:
    monkeypatch.setenv("HOST_STREAM_REFLECTION_BUFFER_ENABLED", "false")
    monkeypatch.setenv("HOST_STREAM_TTS_ENABLED", "false")

    request_body = SimpleNamespace(message="hello", session_id="session-001", thread_id=None)
    metrics = MagicMock()
    captured: dict[str, object] = {}

    class _Registry:
        def get_by_request_id(self, _request_id):  # noqa: ANN001
            return None

        def create_task(self, **_kwargs):  # noqa: ANN003
            return None

    async def _failing_retrieval(**_kwargs):  # noqa: ANN001
        raise RuntimeError("retrieval boom")

    async def _fake_dispatch_agent(**kwargs):  # noqa: ANN001
        captured.update(kwargs)
        yield "data: " + json.dumps({
            "phase": "COMMIT",
            "done": True,
            "decision": "APPROVE",
            "narrative": "final answer",
            "request_id": kwargs.get("request_id"),
            "audit_refs": {"path": "dispatch_agent:degraded"},
        }, ensure_ascii=False) + "\n\n"

    with patch(
        "host_brain.app.top_agent.chat_lite_governor",
        return_value={"decision": "APPROVE", "reason_code": None, "intent_id": "intent-001"},
    ), patch(
        "host_brain.app.top_agent._maybe_build_task_progress_fastpath",
        return_value=None,
    ), patch(
        "host_brain.app.top_agent._maybe_build_recent_turn_answer",
        return_value=None,
    ), patch(
        "host_brain.app.top_agent._maybe_build_phatic_fastpath_reply",
        return_value=None,
    ), patch(
        "host_brain.app.dependencies.get_runtime_task_registry",
        return_value=_Registry(),
    ), patch(
        "host_brain.app.chat_retrieval_helpers._run_retrieval",
        new=_failing_retrieval,
    ), patch(
        "host_brain.app.prompt_builder.infer_render_target_lang",
        return_value="zh",
    ), patch(
        "host_brain.app.state_arbiter_queries._resolve_state_arbiter_bond_fact",
        new=_fake_state_arbiter,
    ), patch(
        "host_brain.app.dependencies.get_recall_pack_builder",
        return_value=_FakeRecallPackBuilder(),
    ), patch(
        "host_brain.app.graph_config_builder.resolve_host_llm_runtime_async",
        new=_fake_runtime,
    ), patch(
        "host_brain.app.dispatch_agent.dispatch_agent_sse_generator",
        new=_fake_dispatch_agent,
    ):
        events = _run(
            _collect_events(
                top_agent_sse_generator(
                    request_body=request_body,
                    request_id="req-retr-fail",
                    session_id="session-001",
                    ts_ms=1000,
                    intent_draft={"id": "intent-001"},
                    emotion_context={},
                    emotion_snapshot={},
                    governor_result={"decision": "APPROVE"},
                    intent_id="intent-001",
                    metrics=metrics,
                )
            )
        )

    commit = json.loads(events[-1].removeprefix("data: ").strip())
    assert commit["decision"] == "APPROVE"
    assert captured["retrieval_summary"]["status"] == "DEGRADED"
    assert "retrieval boom" in captured["retrieval_summary"]["error"]
