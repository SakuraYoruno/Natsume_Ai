from __future__ import annotations

import asyncio
import json
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

from host_brain.app.chat_agent import SubAgentContext
from host_brain.app.dispatch_agent import (
    _build_skill_args,
    _build_dispatch_system_prompt,
    _build_failure_recovery_plan,
    _build_task_progress_sub_context,
    _coerce_structured_skill_result,
    _dispatch_to_sub_agent,
    _execute_skill,
    _extract_file_read_window,
    _extract_hub_skill_source,
    _extract_memory_store_content,
    _hard_rule_route,
    _extract_result_code,
    _format_elapsed_ms,
    _get_or_create_runtime_task,
    _llm_route_dispatch,
    _make_skill_episodic_writer,
    _maybe_build_multi_step_plan,
    _parse_bool_env,
    _retry_after_repair,
    _route_target_allows_parallel,
    _summarize_skill_result_text,
    _execute_multi_step_plan,
    dispatch_agent_sse_generator,
)
from host_brain.app.runtime_task_registry import RuntimeTaskRegistry


def _run(coro):
    return asyncio.run(coro)


async def _collect(agen):
    items = []
    async for item in agen:
        items.append(item)
    return items


def _parse_sse_event(raw: str) -> dict:
    assert raw.startswith("data: ")
    return json.loads(raw[len("data: ") :].strip())


def test_extract_result_code_and_format_elapsed_ms() -> None:
    assert _extract_result_code(None) is None
    assert _extract_result_code("") is None
    assert _extract_result_code("FILE_NOT_FOUND: missing path") == "FILE_NOT_FOUND"
    assert _extract_result_code("NETWORK_UNAVAILABLE") == "NETWORK_UNAVAILABLE"
    assert _extract_result_code("mixed case error") is None

    assert _format_elapsed_ms(-5) == "0ms"
    assert _format_elapsed_ms(950) == "950ms"
    assert _format_elapsed_ms(1_500) == "1.5s"
    assert _format_elapsed_ms(61_000) == "1m01s"


def test_hard_rule_route_defaults_to_chat_and_detects_delegate() -> None:
    route, target_id, reason = _hard_rule_route("just chatting here")
    assert route == "chat"
    assert target_id == ""
    assert reason == "default_chat"

    route, target_id, reason = _hard_rule_route("please debug this traceback")
    assert route == "delegate"
    assert target_id == "code_agent"
    assert reason.startswith("keyword:")


def test_build_task_progress_sub_context_variants(monkeypatch) -> None:
    class _Registry:
        def __init__(self) -> None:
            self.summary = None

        def summarize_for_chat(self, **_kwargs):
            return self.summary

    registry = _Registry()
    monkeypatch.setattr("host_brain.app.dependencies.get_runtime_task_registry", lambda: registry)

    empty_ctx = _build_task_progress_sub_context(
        request_id="req-progress-empty",
        session_id="sess-progress",
        person_id="person-progress",
    )
    assert empty_ctx.agent_id == "task_progress_direct"
    assert empty_ctx.success is True

    registry.summary = {
        "status": "RUNNING",
        "target_id": "browser_navigate",
        "elapsed_ms": 1_500,
        "progress_message": "opening target page",
        "recovery_status": "RUNNING",
    }
    running_ctx = _build_task_progress_sub_context(
        request_id="req-progress-running",
        session_id="sess-progress",
        person_id="person-progress",
    )
    assert "browser_navigate" in running_ctx.result_text
    assert "1.5s" in running_ctx.result_text
    assert "opening target page" in running_ctx.result_text

    registry.summary = {
        "status": "FAILED",
        "target_id": "file_read",
        "elapsed_ms": 61_000,
        "detail": "read failed",
        "result_code": "FILE_NOT_FOUND",
        "recovery_error": "repair failed",
    }
    failed_ctx = _build_task_progress_sub_context(
        request_id="req-progress-failed",
        session_id="sess-progress",
        person_id="person-progress",
    )
    assert "file_read" in failed_ctx.result_text
    assert "FILE_NOT_FOUND" in failed_ctx.result_text
    assert "repair failed" in failed_ctx.result_text


def test_route_target_allows_parallel_for_delegate_and_skill(monkeypatch) -> None:
    monkeypatch.setattr(
        "host_brain.app.agents.agent_registry.get_agent_meta",
        lambda agent_id: {"allow_parallel": agent_id == "intent_agent", "has_side_effects": False},
    )
    assert _route_target_allows_parallel("delegate", "intent_agent") is True

    monkeypatch.setattr(
        "host_brain.app.agents.agent_registry.get_agent_meta",
        lambda _agent_id: {"allow_parallel": True, "has_side_effects": True},
    )
    assert _route_target_allows_parallel("delegate", "code_agent") is False

    assert _route_target_allows_parallel("skill", "web_search") is True

    class _Registry:
        def __init__(self, meta) -> None:
            self.meta = meta

        def get(self, _skill_id):
            return self.meta

    monkeypatch.setattr(
        "host_brain.app.dependencies.get_skill_registry",
        lambda: _Registry(SimpleNamespace(risk_level=1, require_confirm=False)),
    )
    assert _route_target_allows_parallel("skill", "custom_safe") is True

    monkeypatch.setattr(
        "host_brain.app.dependencies.get_skill_registry",
        lambda: _Registry(SimpleNamespace(risk_level=2, require_confirm=False)),
    )
    assert _route_target_allows_parallel("skill", "custom_risky") is False

    monkeypatch.setattr(
        "host_brain.app.dependencies.get_skill_registry",
        lambda: (_ for _ in ()).throw(RuntimeError("registry unavailable")),
    )
    assert _route_target_allows_parallel("skill", "custom_error") is False
    assert _route_target_allows_parallel("chat", "") is False


def test_maybe_build_multi_step_plan_returns_none_for_empty_or_plan_failure(monkeypatch) -> None:
    assert _run(_maybe_build_multi_step_plan(user_message="", request_id="req-empty", llm_runtime=None)) is None

    planning_agent = SimpleNamespace(
        _decompose_request=lambda _message: [{"description": "a"}, {"description": "b"}],
        needs_planning=lambda _message: True,
        create_plan=lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("plan failed")),
    )
    monkeypatch.setattr("host_brain.app.dependencies.get_planning_agent", lambda: planning_agent)
    monkeypatch.setattr(
        "host_brain.app.dependencies.get_skill_registry",
        lambda: SimpleNamespace(list_skill_names=lambda: ["web_search"]),
    )

    assert _run(
        _maybe_build_multi_step_plan(
            user_message="do two things",
            request_id="req-plan-failed",
            llm_runtime=None,
        )
    ) is None


def test_maybe_build_multi_step_plan_routes_chat_step_with_llm(monkeypatch) -> None:
    planning_agent = SimpleNamespace(
        _decompose_request=lambda _message: [
            {"description": "analyze this failure"},
            {"description": "搜索天气"},
            {"description": ""},
        ],
        needs_planning=lambda _message: True,
        create_plan=lambda **_kwargs: SimpleNamespace(as_dict=lambda: {"steps": 2}),
    )
    monkeypatch.setattr("host_brain.app.dependencies.get_planning_agent", lambda: planning_agent)
    monkeypatch.setattr(
        "host_brain.app.dependencies.get_skill_registry",
        lambda: SimpleNamespace(list_skill_names=lambda: ["web_search", "network_status"]),
    )
    monkeypatch.setattr(
        "host_brain.app.graph_config_builder.resolve_dispatch_agent_llm_runtime",
        lambda base_runtime=None: {"provider_name": "mock", "model_id": "mock"},
    )
    monkeypatch.setattr(
        "host_brain.app.dispatch_agent._llm_route_dispatch",
        AsyncMock(return_value=("delegate", "intent_agent", "clarified failure analysis")),
    )
    monkeypatch.setattr(
        "host_brain.app.dispatch_agent._route_target_allows_parallel",
        lambda route, target_id: (route, target_id) in {
            ("delegate", "intent_agent"),
            ("skill", "web_search"),
        },
    )

    plan = _run(
        _maybe_build_multi_step_plan(
            user_message="first analyze, then search",
            request_id="req-plan-llm",
            llm_runtime={"provider_name": "base", "model_id": "base"},
        )
    )

    assert plan is not None
    assert plan["parallel_enabled"] is True
    assert len(plan["steps"]) == 2
    assert plan["steps"][0]["route"] == "delegate"
    assert plan["steps"][0]["target_id"] == "intent_agent"
    assert plan["steps"][0]["task_description"] == "clarified failure analysis"
    assert plan["steps"][0]["route_reason"] == "llm_plan:delegate"
    assert plan["steps"][1]["target_id"] == "web_search"


def test_get_or_create_runtime_task_creates_and_refreshes_existing(monkeypatch) -> None:
    registry = RuntimeTaskRegistry()
    monkeypatch.setattr("host_brain.app.dependencies.get_runtime_task_registry", lambda: registry)

    _, created = _get_or_create_runtime_task(
        request_id="req-runtime-task",
        task_kind="skill",
        target_id="web_search",
        session_id="sess-runtime",
        person_id="person-runtime",
        user_message="search docs",
        detail="running search",
        progress_message="searching",
        meta={"phase": 1},
    )
    registry.mark_failed(created.task_id, detail="failed", error="NETWORK_UNAVAILABLE", result_code="FAIL")

    _, refreshed = _get_or_create_runtime_task(
        request_id="req-runtime-task",
        task_kind="delegate",
        target_id="intent_agent",
        session_id="sess-runtime-2",
        person_id="person-runtime-2",
        user_message="recover search",
        detail="retrying",
        progress_message="retrying now",
        meta={"phase": 2},
    )

    assert refreshed.task_id == created.task_id
    assert refreshed.task_kind == "delegate"
    assert refreshed.target_id == "intent_agent"
    assert refreshed.detail == "retrying"
    assert refreshed.progress_message == "retrying now"
    assert refreshed.error is None
    assert refreshed.result_code is None
    assert refreshed.result_text == ""
    assert refreshed.meta == {"phase": 2}


def test_build_failure_recovery_plan_and_retry_paths(monkeypatch) -> None:
    file_plan = _build_failure_recovery_plan(
        target_id="file_read",
        user_message="read missing file",
        error_text="FILE_NOT_FOUND: missing",
    )
    assert file_plan is not None
    assert file_plan["agent_id"] == "intent_agent"
    assert file_plan["reason_code"] == "FILE_NOT_FOUND"

    network_plan = _build_failure_recovery_plan(
        target_id="web_search",
        user_message="search online",
        error_text="NETWORK_UNAVAILABLE: offline",
    )
    assert network_plan is not None
    assert network_plan["agent_id"] == "repair_agent"

    unknown_plan = _build_failure_recovery_plan(
        target_id="noop",
        user_message="do nothing",
        error_text="UNKNOWN_REASON: skip",
    )
    assert unknown_plan is not None
    assert unknown_plan["agent_id"] == "repair_agent"
    assert unknown_plan["reason_code"] == "UNKNOWN_REASON"

    async def _fake_dispatch_to_sub_agent(**_kwargs):
        return SubAgentContext(
            agent_id="delegate:intent_agent",
            task="repair",
            result_text="delegate retry ok",
            success=True,
            latency_ms=3,
        )

    monkeypatch.setattr("host_brain.app.dispatch_agent._dispatch_to_sub_agent", _fake_dispatch_to_sub_agent)
    delegate_ctx = _run(
        _retry_after_repair(
            route_kind="delegate",
            target_id="intent_agent",
            task_description="repair browser",
            user_message="repair browser",
            request_id="req-retry-delegate",
            session_id="sess-retry",
            llm_runtime={"provider_name": "mock"},
            emotion_context={"emotion_tag": "happy"},
            bond_summary={"stage": "friend"},
            render_target_lang="zh",
        )
    )
    assert delegate_ctx.success is True
    assert delegate_ctx.result_text == "delegate retry ok"

    unsupported_ctx = _run(
        _retry_after_repair(
            route_kind="chat",
            target_id="chat",
            task_description="noop",
            user_message="noop",
            request_id="req-retry-unsupported",
            session_id="sess-retry",
            llm_runtime=None,
            emotion_context={},
            bond_summary={},
            render_target_lang="zh",
        )
    )
    assert unsupported_ctx.success is False
    assert unsupported_ctx.error == "UNSUPPORTED_RETRY_ROUTE"


def test_build_dispatch_system_prompt_and_parse_bool_env(monkeypatch) -> None:
    class _Registry:
        def list_skill_names(self):
            return ["alpha", "beta"]

        def get(self, skill_name):
            if skill_name == "alpha":
                return SimpleNamespace(description="first skill")
            return SimpleNamespace(description="")

    monkeypatch.setattr("host_brain.app.dependencies.get_skill_registry", lambda: _Registry())
    prompt = _build_dispatch_system_prompt()
    assert "- alpha: first skill" in prompt
    assert "- beta" in prompt

    monkeypatch.setattr(
        "host_brain.app.dependencies.get_skill_registry",
        lambda: (_ for _ in ()).throw(RuntimeError("registry down")),
    )
    fallback_prompt = _build_dispatch_system_prompt()
    assert "- web_search:" in fallback_prompt
    assert "- browser_navigate:" in fallback_prompt

    monkeypatch.setenv("DISPATCH_BOOL_TRUE", "yes")
    monkeypatch.setenv("DISPATCH_BOOL_FALSE", "0")
    monkeypatch.delenv("DISPATCH_BOOL_MISSING", raising=False)
    assert _parse_bool_env("DISPATCH_BOOL_TRUE") is True
    assert _parse_bool_env("DISPATCH_BOOL_FALSE") is False
    assert _parse_bool_env("DISPATCH_BOOL_MISSING", default=True) is True


def test_llm_route_dispatch_parses_json_and_falls_back(monkeypatch) -> None:
    async def _good_stream(**kwargs):
        assert kwargs["provider_name"] == "mock-provider"
        yield "```json\n"
        yield '{"route":"delegate","target_id":"code_agent","task":"fix bug"}'
        yield "\n```"

    monkeypatch.setattr("host_brain.integrations.llm_helpers.astream_llm_chunks", _good_stream)
    monkeypatch.setattr("host_brain.app.dispatch_agent._build_dispatch_system_prompt", lambda: "SYS")
    route, target_id, task = _run(
        _llm_route_dispatch(
            user_message="please fix bug",
            llm_runtime={
                "provider_name": "mock-provider",
                "model_id": "mock-model",
                "llm_api_key": "key",
                "timeout_ms": 1234,
            },
            request_id="req-llm-good",
        )
    )
    assert route == "delegate"
    assert target_id == "code_agent"
    assert task == "fix bug"

    async def _invalid_route_stream(**_kwargs):
        yield '{"route":"weird","target_id":"ignored","task":"noop"}'

    monkeypatch.setattr("host_brain.integrations.llm_helpers.astream_llm_chunks", _invalid_route_stream)
    route, target_id, task = _run(
        _llm_route_dispatch(
            user_message="unknown",
            llm_runtime={
                "provider_name": "mock-provider",
                "model_id": "mock-model",
            },
            request_id="req-llm-invalid-route",
        )
    )
    assert route == "chat"
    assert target_id == "ignored"
    assert task == "noop"

    async def _bad_json_stream(**_kwargs):
        yield "{bad json"

    monkeypatch.setattr("host_brain.integrations.llm_helpers.astream_llm_chunks", _bad_json_stream)
    assert _run(
        _llm_route_dispatch(
            user_message="broken",
            llm_runtime={
                "provider_name": "mock-provider",
                "model_id": "mock-model",
            },
            request_id="req-llm-bad-json",
        )
    ) == ("chat", "", "")


def test_execute_multi_step_plan_parallel_partial_failure_with_recovery(monkeypatch) -> None:
    registry = RuntimeTaskRegistry()
    monkeypatch.setattr("host_brain.app.dependencies.get_runtime_task_registry", lambda: registry)

    async def _fake_execute_skill(*, skill_id: str, user_message: str, request_id: str, session_id: str):
        return SubAgentContext(
            agent_id=f"skill:{skill_id}",
            task=user_message,
            result_text=f"done:{skill_id}",
            success=True,
            latency_ms=1,
        )

    dispatch_calls: list[str] = []

    async def _fake_dispatch_to_sub_agent(*, agent_id: str, **_kwargs):
        dispatch_calls.append(agent_id)
        if agent_id == "code_agent":
            return SubAgentContext(
                agent_id="delegate:code_agent",
                task="write code",
                result_text="",
                success=False,
                latency_ms=2,
                error="NETWORK_UNAVAILABLE: backend offline",
            )
        return SubAgentContext(
            agent_id=f"delegate:{agent_id}",
            task="repair code",
            result_text="repair done",
            success=True,
            latency_ms=3,
        )

    monkeypatch.setattr("host_brain.app.dispatch_agent._execute_skill", _fake_execute_skill)
    monkeypatch.setattr("host_brain.app.dispatch_agent._dispatch_to_sub_agent", _fake_dispatch_to_sub_agent)
    monkeypatch.setattr(
        "host_brain.app.dispatch_agent._build_failure_recovery_plan",
        lambda **_kwargs: {
            "agent_id": "repair_agent",
            "task": "repair the delegate failure",
            "summary": "auto repair running",
        },
    )

    plan = {
        "plan_id": "plan::req-parallel-failure",
        "parallel_enabled": True,
        "steps": [
            {
                "step_index": 1,
                "description": "search docs",
                "route": "skill",
                "target_id": "web_search",
                "task_description": "search docs",
                "route_reason": "keyword",
                "allow_parallel": True,
            },
            {
                "step_index": 2,
                "description": "write code",
                "route": "delegate",
                "target_id": "code_agent",
                "task_description": "write code",
                "route_reason": "keyword",
                "allow_parallel": True,
            },
        ],
    }

    result = _run(
        _execute_multi_step_plan(
            plan=plan,
            request_id="req-parallel-failure",
            session_id="sess-parallel",
            person_id="person-parallel",
            user_message="search then write",
            llm_runtime=None,
            emotion_context={"emotion_tag": "neutral"},
            bond_summary={"stage": "friend"},
            render_target_lang="zh",
        )
    )

    assert result is not None
    assert result.agent_id == "plan_execution"
    assert result.success is False
    assert result.error == "PLAN_PARTIAL_FAILURE"
    assert result.payload["parallel_enabled"] is True
    assert result.payload["success_count"] == 1
    assert result.payload["steps"][1]["recovery_result_text"] == "repair done"
    assert "repair done" in result.result_text
    assert dispatch_calls == ["code_agent", "repair_agent"]


def test_dispatch_agent_sse_generator_task_progress_direct(monkeypatch) -> None:
    async def _noop_post_process(**_kwargs):
        return None

    monkeypatch.setattr(
        "host_brain.app.dispatch_agent._hard_rule_route",
        lambda _message: ("task_progress", "", "task_progress_query"),
    )
    monkeypatch.setattr(
        "host_brain.app.dispatch_agent._build_task_progress_sub_context",
        lambda **_kwargs: SubAgentContext(
            agent_id="task_progress_direct",
            task="task_progress_query",
            result_text="current task progress summary",
            success=True,
        ),
    )
    monkeypatch.setattr("host_brain.app.top_agent._airlock_sanitize_output", lambda text: f"SAN:{text}")
    monkeypatch.setattr("host_brain.app.top_agent._async_post_process", _noop_post_process)
    monkeypatch.setattr("host_brain.app.dispatch_agent.asyncio.create_task", lambda coro: coro.close())
    monkeypatch.setattr("host_brain.app.dependencies.get_runtime_task_registry", lambda: RuntimeTaskRegistry())

    events = _run(
        _collect(
            dispatch_agent_sse_generator(
                request_body=SimpleNamespace(
                    reply_channel="GATEWAY",
                    suppress_edge_tts=False,
                    suppress_edge_vts=False,
                    thread_id="thread-progress",
                ),
                request_id="req-dispatch-progress",
                session_id="sess-dispatch-progress",
                ts_ms=1_000,
                intent_draft={"scene": "chat"},
                emotion_context={"emotion_tag": "NEUTRAL"},
                emotion_snapshot={},
                governor_result={"decision": "APPROVE"},
                intent_id="intent-progress",
                metrics=SimpleNamespace(),
                person_id="person-progress",
                sanitized_message="progress?",
                recall_slot_text={},
                bond_summary={},
                retrieval_evidence_items=[],
                retrieval_summary={},
                render_target_lang="zh",
                llm_runtime={"provider_name": "base", "model_id": "base"},
                pii_ingress_stats={},
            )
        )
    )

    assert len(events) == 1
    payload = _parse_sse_event(events[0])
    assert payload["phase"] == "COMMIT"
    assert payload["audit_refs"]["path"] == "dispatch_agent:task_progress"
    assert payload["narrative"] == "SAN:current task progress summary"


def test_dispatch_agent_sse_generator_uses_llm_route_before_chat(monkeypatch) -> None:
    async def _fake_chat_agent(chat_input):
        assert chat_input.reply_channel == "GATEWAY"
        assert chat_input.reply_depth_policy == "gateway"
        assert chat_input.thread_id == "thread-chat"
        assert chat_input.ts_ms == 2_000
        assert chat_input.intent_draft == {"scene": "chat"}
        assert chat_input.llm_runtime == {"provider_name": "base", "model_id": "base"}
        yield 'data: {"phase":"CHAT","ok":true}\n\n'

    monkeypatch.setenv("HOST_DISPATCH_LLM_ROUTE_ENABLED", "true")
    monkeypatch.setattr(
        "host_brain.app.dispatch_agent._hard_rule_route",
        lambda _message: ("chat", "", "default_chat"),
    )
    monkeypatch.setattr(
        "host_brain.app.graph_config_builder.resolve_dispatch_agent_llm_runtime",
        lambda base_runtime=None: {"provider_name": "dispatch", "model_id": "dispatch"},
    )
    monkeypatch.setattr(
        "host_brain.app.dispatch_agent._llm_route_dispatch",
        AsyncMock(return_value=("chat", "", "")),
    )
    monkeypatch.setattr(
        "host_brain.app.dispatch_agent._maybe_build_multi_step_plan",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr("host_brain.app.dependencies.get_runtime_task_registry", lambda: RuntimeTaskRegistry())
    monkeypatch.setattr("host_brain.app.chat_agent.chat_agent_sse_generator", _fake_chat_agent)

    events = _run(
        _collect(
            dispatch_agent_sse_generator(
                request_body=SimpleNamespace(
                    reply_channel="GATEWAY",
                    suppress_edge_tts=True,
                    suppress_edge_vts=False,
                    thread_id="thread-chat",
                ),
                request_id="req-dispatch-llm-chat",
                session_id="sess-dispatch-llm-chat",
                ts_ms=2_000,
                intent_draft={"scene": "chat"},
                emotion_context={"emotion_tag": "NEUTRAL"},
                emotion_snapshot={"mood": "steady"},
                governor_result={"decision": "APPROVE"},
                intent_id="intent-llm-chat",
                metrics=SimpleNamespace(),
                person_id="person-llm-chat",
                sanitized_message="this is a long enough message to trigger llm route",
                recall_slot_text={"memory": "slot"},
                bond_summary={"stage": "friend"},
                retrieval_evidence_items=[{"memory_id": "m1"}],
                retrieval_summary={"status": "ok"},
                render_target_lang="zh",
                llm_runtime={"provider_name": "base", "model_id": "base"},
                pii_ingress_stats={"masked": 0},
            )
        )
    )

    assert len(events) == 2
    first = _parse_sse_event(events[0])
    assert first["phase"] == "PRESENCE"
    assert first["event"] == "THINKING"
    second = _parse_sse_event(events[1])
    assert second["phase"] == "CHAT"


def test_dispatch_to_sub_agent_wraps_dispatcher_response_and_context(monkeypatch) -> None:
    captured = {}

    class _Dispatcher:
        async def dispatch(self, req):
            captured["request"] = req
            return SimpleNamespace(
                agent_id=req.agent_id,
                result="partial repair",
                success=False,
                error="FILE_NOT_FOUND: missing target",
            )

    monkeypatch.setattr(
        "host_brain.app.graph_config_builder.resolve_subagent_llm_runtime",
        lambda base_runtime=None: {"provider_name": "dispatch", "model_id": "dispatch"},
    )
    monkeypatch.setattr(
        "host_brain.app.top_agent._build_subagent_gateway_from_runtime",
        lambda _runtime: {"provider_name": "dispatch"},
    )
    monkeypatch.setattr("host_brain.app.sub_agent_protocol.get_sub_agent_dispatcher", lambda: _Dispatcher())

    ctx = _run(
        _dispatch_to_sub_agent(
            agent_id="intent_agent",
            task="repair missing file",
            user_message="repair missing file",
            request_id="req-sub-dispatch",
            session_id="sess-sub-dispatch",
            llm_runtime={"provider_name": "base", "model_id": "base"},
            emotion_tag="sad",
            bond_stage="friend",
            render_target_lang="zh",
            recent_turns=["first", "second", "third", "fourth"],
            previous_agent_result="previous failure",
        )
    )

    req = captured["request"]
    assert req.agent_id == "intent_agent"
    assert req.task == "repair missing file"
    assert req.context["gateway"] == {"provider_name": "dispatch"}
    assert req.context["recent_turns"] == ["second", "third", "fourth"]
    assert req.context["previous_agent_result"] == "previous failure"
    assert req.context["emotion_tag"] == "sad"
    assert req.context["bond_stage"] == "friend"
    assert ctx.agent_id == "intent_agent"
    assert ctx.success is False
    assert ctx.result_text == "partial repair"
    assert ctx.error == "FILE_NOT_FOUND: missing target"


def test_dispatch_to_sub_agent_returns_failure_when_dispatcher_raises(monkeypatch) -> None:
    class _Dispatcher:
        async def dispatch(self, _req):
            raise RuntimeError("dispatcher offline")

    monkeypatch.setattr(
        "host_brain.app.graph_config_builder.resolve_subagent_llm_runtime",
        lambda base_runtime=None: base_runtime,
    )
    monkeypatch.setattr("host_brain.app.top_agent._build_subagent_gateway_from_runtime", lambda _runtime: None)
    monkeypatch.setattr("host_brain.app.sub_agent_protocol.get_sub_agent_dispatcher", lambda: _Dispatcher())

    ctx = _run(
        _dispatch_to_sub_agent(
            agent_id="code_agent",
            task="debug the crash",
            user_message="debug the crash",
            request_id="req-sub-error",
            session_id="sess-sub-error",
            llm_runtime={"provider_name": "base", "model_id": "base"},
        )
    )

    assert ctx.agent_id == "code_agent"
    assert ctx.success is False
    assert ctx.error == "dispatcher offline"


def test_extract_helpers_cover_hub_source_and_file_windows() -> None:
    assert (
        _extract_hub_skill_source("安装技能 https://github.com/example/demo-skill")
        == "https://github.com/example/demo-skill"
    )
    assert (
        _extract_hub_skill_source("安装技能 github:owner/repo/demo_skill")
        == "github:owner/repo/demo_skill"
    )
    assert _extract_hub_skill_source("安装 hub 技能 C:/skills/demo-skill 覆盖安装") == "C:/skills/demo-skill"
    assert _extract_hub_skill_source("安装技能 demo_skill 立即激活") == "demo_skill"

    assert _extract_file_read_window("打开文件 D:/Natsume_Ai/AGENTS.md 的第 12 行") == (12, 1)
    assert _extract_file_read_window("打开文件 D:/Natsume_Ai/AGENTS.md 的前 20 行") == (1, 20)
    assert _extract_file_read_window("打开文件 D:/Natsume_Ai/AGENTS.md") == (1, None)


def test_extract_memory_and_skill_args_cover_empty_and_remove_paths() -> None:
    assert _extract_memory_store_content("") == ""
    assert _build_skill_args("file_write", "写文件 D:/Natsume_Ai/runtime/demo.txt")["content"] == ""
    assert _build_skill_args("hub_skill_remove", "删除技能 demo_skill")["skill_name"] == "demo_skill"


def test_make_skill_episodic_writer_raises_when_store_write_fails() -> None:
    class _FailStore:
        def write_memory(self, **_kwargs):
            return SimpleNamespace(success=False, reason_code="STORE_BLOCKED", detail="blocked")

    writer = _make_skill_episodic_writer(
        episodic_store=_FailStore(),
        request_id="req-writer-fail",
        intent_id="intent-writer-fail",
        skill_id="memory_store",
    )

    try:
        writer(content="remember this")
        assert False, "expected RuntimeError"
    except RuntimeError as exc:
        assert str(exc) == "STORE_BLOCKED"


def test_coerce_and_summarize_structured_skill_result_helpers() -> None:
    assert _coerce_structured_skill_result({"detail": {"message": "ok"}}) == {"detail": {"message": "ok"}}
    assert _coerce_structured_skill_result('{"detail": {"message": "json ok"}}') == {
        "detail": {"message": "json ok"}
    }
    assert _coerce_structured_skill_result("{'detail': {'message': 'py ok'}}") == {
        "detail": {"message": "py ok"}
    }
    assert _coerce_structured_skill_result("plain text") is None

    assert _summarize_skill_result_text(
        "hub_skill_install",
        {"detail": {"name": "demo", "activated": True}},
        "",
    ) == "技能 demo 已安装并激活"
    assert _summarize_skill_result_text(
        "hub_skill_search",
        {"detail": {"total": 3}, "query": "天气"},
        "",
    ) == "已搜索到 3 个与天气相关的技能"
    assert _summarize_skill_result_text(
        "hub_skill_remove",
        {"detail": {"message": "已移除 demo"}},
        "",
    ) == "已移除 demo"
    assert _summarize_skill_result_text("custom_skill", None, "") == "Skill custom_skill 执行成功"


def test_execute_skill_returns_missing_skill_error(monkeypatch) -> None:
    class _FakeRegistry:
        def get(self, _skill_id):
            return None

    monkeypatch.setattr("host_brain.app.dependencies.get_skill_registry", lambda: _FakeRegistry())
    monkeypatch.setattr("host_brain.app.dependencies.get_ledger", lambda: object())

    ctx = _run(
        _execute_skill(
            skill_id="missing_skill",
            user_message="run missing skill",
            request_id="req-skill-missing",
            session_id="sess-skill-missing",
        )
    )

    assert ctx.success is False
    assert ctx.error == "Skill 'missing_skill' not found in registry"


def test_execute_skill_parses_structured_result_digest(monkeypatch) -> None:
    class _FakeRegistry:
        def get(self, skill_id):
            return SimpleNamespace(skill_name=skill_id)

    class _FakeTokenService:
        def issue_approval_token(self, **_kwargs):
            return "approval-token-structured"

    class _FakeLoader:
        def __init__(self, **_kwargs):
            pass

        def execute(self, **_kwargs):
            return SimpleNamespace(
                status="SUCCESS",
                result_code="SKILL_EXECUTED",
                detail={
                    "result_digest": '{"detail":{"total":2,"message":"structured ok"}}',
                },
                runtime_payload={},
            )

    monkeypatch.setattr("host_brain.app.dependencies.get_skill_registry", lambda: _FakeRegistry())
    monkeypatch.setattr("host_brain.app.dependencies.get_ledger", lambda: object())
    monkeypatch.setattr("host_brain.skills.loader.SkillLoader", _FakeLoader)
    monkeypatch.setattr("host_brain.governor.tokens.TokenService", _FakeTokenService)
    monkeypatch.setattr("host_brain.core.ids.new_event_id", lambda: "intent-structured")

    ctx = _run(
        _execute_skill(
            skill_id="hub_skill_list",
            user_message="列出技能",
            request_id="req-skill-structured",
            session_id="sess-skill-structured",
        )
    )

    assert ctx.success is True
    assert ctx.result_text == "当前已安装 2 个 Hub 技能"
    assert ctx.payload["detail"]["message"] == "structured ok"
    assert ctx.payload["detail"]["total"] == 2


def test_execute_skill_memory_backend_warning_and_idempotent_replay(monkeypatch) -> None:
    class _FakeRegistry:
        def get(self, skill_id):
            return SimpleNamespace(skill_name=skill_id)

    class _FakeTokenService:
        def issue_approval_token(self, **_kwargs):
            return "approval-token-memory"

    class _FakeLoader:
        def __init__(self, **_kwargs):
            pass

        def execute(self, **_kwargs):
            return SimpleNamespace(
                status="IDEMPOTENT_REPLAY",
                result_code="SKILL_SKIPPED",
                detail={},
                runtime_payload={},
            )

    monkeypatch.setattr("host_brain.app.dependencies.get_skill_registry", lambda: _FakeRegistry())
    monkeypatch.setattr("host_brain.app.dependencies.get_ledger", lambda: object())
    monkeypatch.setattr(
        "host_brain.app.dependencies.get_episodic_store",
        lambda: (_ for _ in ()).throw(RuntimeError("episodic backend offline")),
    )
    monkeypatch.setattr("host_brain.skills.loader.SkillLoader", _FakeLoader)
    monkeypatch.setattr("host_brain.governor.tokens.TokenService", _FakeTokenService)
    monkeypatch.setattr("host_brain.core.ids.new_event_id", lambda: "intent-memory")

    ctx = _run(
        _execute_skill(
            skill_id="memory_store",
            user_message="记住我喜欢红茶",
            request_id="req-memory-replay",
            session_id="sess-memory-replay",
        )
    )

    assert ctx.success is True
    assert "memory_store" in ctx.result_text
    assert ctx.payload == {}


def test_execute_skill_returns_failed_status_payload(monkeypatch) -> None:
    class _FakeRegistry:
        def get(self, skill_id):
            return SimpleNamespace(skill_name=skill_id)

    class _FakeTokenService:
        def issue_approval_token(self, **_kwargs):
            return "approval-token-failed"

    class _FakeLoader:
        def __init__(self, **_kwargs):
            pass

        def execute(self, **_kwargs):
            return SimpleNamespace(
                status="FAILED",
                result_code="SKILL_FAILED",
                detail={"result_digest": '{"detail":{"message":"broken tool"}}'},
                runtime_payload={},
            )

    monkeypatch.setattr("host_brain.app.dependencies.get_skill_registry", lambda: _FakeRegistry())
    monkeypatch.setattr("host_brain.app.dependencies.get_ledger", lambda: object())
    monkeypatch.setattr("host_brain.skills.loader.SkillLoader", _FakeLoader)
    monkeypatch.setattr("host_brain.governor.tokens.TokenService", _FakeTokenService)
    monkeypatch.setattr("host_brain.core.ids.new_event_id", lambda: "intent-failed")

    ctx = _run(
        _execute_skill(
            skill_id="hub_skill_remove",
            user_message="删除技能 demo_skill",
            request_id="req-skill-failed",
            session_id="sess-skill-failed",
        )
    )

    assert ctx.success is False
    assert ctx.error == "SKILL_FAILED: FAILED"
    assert ctx.payload["detail"]["message"] == "broken tool"


def test_execute_skill_returns_exception_context_when_to_thread_raises(monkeypatch) -> None:
    async def _boom(_func):
        raise RuntimeError("thread crashed")

    monkeypatch.setattr("host_brain.app.dispatch_agent.asyncio.to_thread", _boom)

    ctx = _run(
        _execute_skill(
            skill_id="web_search",
            user_message="search this",
            request_id="req-skill-exc",
            session_id="sess-skill-exc",
        )
    )

    assert ctx.success is False
    assert ctx.agent_id == "skill:web_search"
    assert ctx.error == "thread crashed"


def test_dispatch_agent_sse_generator_plan_build_failure_falls_back_to_chat(monkeypatch) -> None:
    async def _fake_chat_agent(chat_input):
        assert chat_input.user_message == "chat after planner failure"
        yield 'data: {"phase":"CHAT","fallback":"plan_error"}\n\n'

    monkeypatch.setattr(
        "host_brain.app.dispatch_agent._hard_rule_route",
        lambda _message: ("chat", "", "default_chat"),
    )
    monkeypatch.setattr(
        "host_brain.app.dispatch_agent._maybe_build_multi_step_plan",
        AsyncMock(side_effect=RuntimeError("planner unavailable")),
    )
    monkeypatch.setattr("host_brain.app.dependencies.get_runtime_task_registry", lambda: RuntimeTaskRegistry())
    monkeypatch.setattr("host_brain.app.chat_agent.chat_agent_sse_generator", _fake_chat_agent)

    events = _run(
        _collect(
            dispatch_agent_sse_generator(
                request_body=SimpleNamespace(
                    reply_channel="WEB",
                    suppress_edge_tts=False,
                    suppress_edge_vts=False,
                    thread_id="thread-plan-fallback",
                ),
                request_id="req-plan-fallback",
                session_id="sess-plan-fallback",
                ts_ms=3_000,
                intent_draft={"scene": "chat"},
                emotion_context={"emotion_tag": "NEUTRAL"},
                emotion_snapshot={},
                governor_result={"decision": "APPROVE"},
                intent_id="intent-plan-fallback",
                metrics=SimpleNamespace(),
                person_id="person-plan-fallback",
                sanitized_message="chat after planner failure",
                recall_slot_text={},
                bond_summary={},
                retrieval_evidence_items=[],
                retrieval_summary={},
                render_target_lang="zh",
                llm_runtime={"provider_name": "base", "model_id": "base"},
                pii_ingress_stats={},
            )
        )
    )

    assert len(events) == 1
    assert _parse_sse_event(events[0])["fallback"] == "plan_error"


def test_dispatch_agent_sse_generator_recent_turn_short_circuits(monkeypatch) -> None:
    async def _noop_post_process(**_kwargs):
        return None

    monkeypatch.setattr(
        "host_brain.app.dispatch_agent._hard_rule_route",
        lambda _message: ("recent_turn", "", "recent_turn_query"),
    )
    monkeypatch.setattr(
        "host_brain.app.top_agent._maybe_build_recent_turn_answer",
        lambda **_kwargs: "recent turn answer",
    )
    monkeypatch.setattr("host_brain.app.top_agent._airlock_sanitize_output", lambda text: f"SAN:{text}")
    monkeypatch.setattr("host_brain.app.top_agent._async_post_process", _noop_post_process)
    monkeypatch.setattr("host_brain.app.dispatch_agent.asyncio.create_task", lambda coro: coro.close())

    events = _run(
        _collect(
            dispatch_agent_sse_generator(
                request_body=SimpleNamespace(reply_channel="WEB", thread_id="thread-recent"),
                request_id="req-recent",
                session_id="sess-recent",
                ts_ms=5_000,
                intent_draft={},
                emotion_context={"emotion_tag": "NEUTRAL"},
                emotion_snapshot={},
                governor_result={"decision": "APPROVE"},
                intent_id="intent-recent",
                metrics=SimpleNamespace(),
                person_id="person-recent",
                sanitized_message="what did I just say?",
                recall_slot_text={},
                bond_summary={},
                retrieval_evidence_items=[],
                retrieval_summary={},
                render_target_lang="zh",
                llm_runtime={"provider_name": "base", "model_id": "base"},
                pii_ingress_stats={},
            )
        )
    )

    assert len(events) == 1
    payload = _parse_sse_event(events[0])
    assert payload["phase"] == "COMMIT"
    assert payload["audit_refs"]["path"] == "dispatch_agent:recent_turn"
    assert payload["narrative"] == "SAN:recent turn answer"


def test_dispatch_agent_sse_generator_llm_route_exception_falls_back_to_chat(monkeypatch) -> None:
    async def _fake_chat_agent(chat_input):
        assert chat_input.bond_summary == {"stage": "friend"}
        assert chat_input.reply_channel == "GATEWAY"
        yield 'data: {"phase":"CHAT","fallback":"llm_route_error"}\n\n'

    monkeypatch.setenv("HOST_DISPATCH_LLM_ROUTE_ENABLED", "true")
    monkeypatch.setattr(
        "host_brain.app.dispatch_agent._hard_rule_route",
        lambda _message: ("chat", "", "default_chat"),
    )
    monkeypatch.setattr(
        "host_brain.app.graph_config_builder.resolve_dispatch_agent_llm_runtime",
        lambda base_runtime=None: {"provider_name": "dispatch", "model_id": "dispatch"},
    )
    monkeypatch.setattr(
        "host_brain.app.dispatch_agent._llm_route_dispatch",
        AsyncMock(side_effect=RuntimeError("llm route failed")),
    )
    monkeypatch.setattr(
        "host_brain.app.dispatch_agent._maybe_build_multi_step_plan",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr("host_brain.app.dependencies.get_runtime_task_registry", lambda: RuntimeTaskRegistry())
    monkeypatch.setattr("host_brain.app.chat_agent.chat_agent_sse_generator", _fake_chat_agent)

    events = _run(
        _collect(
            dispatch_agent_sse_generator(
                request_body=SimpleNamespace(
                    reply_channel="GATEWAY",
                    suppress_edge_tts=False,
                    suppress_edge_vts=False,
                    thread_id="thread-llm-error",
                ),
                request_id="req-llm-error",
                session_id="sess-llm-error",
                ts_ms=6_000,
                intent_draft={"scene": "chat"},
                emotion_context={"emotion_tag": "NEUTRAL"},
                emotion_snapshot={},
                governor_result={"decision": "APPROVE"},
                intent_id="intent-llm-error",
                metrics=SimpleNamespace(),
                person_id="person-llm-error",
                sanitized_message="this is definitely long enough to trigger the llm route path",
                recall_slot_text={},
                bond_summary={"stage": "friend"},
                retrieval_evidence_items=[],
                retrieval_summary={},
                render_target_lang="zh",
                llm_runtime={"provider_name": "base", "model_id": "base"},
                pii_ingress_stats={},
            )
        )
    )

    assert len(events) == 2
    assert _parse_sse_event(events[0])["event"] == "THINKING"
    assert _parse_sse_event(events[1])["fallback"] == "llm_route_error"


def test_dispatch_agent_sse_generator_delegate_self_heals_after_failure(monkeypatch) -> None:
    registry = RuntimeTaskRegistry()
    dispatch_calls = []

    async def _fake_dispatch_to_sub_agent(*, agent_id: str, previous_agent_result=None, **kwargs):
        dispatch_calls.append(
            {
                "agent_id": agent_id,
                "task": kwargs["task"],
                "previous_agent_result": previous_agent_result,
            }
        )
        if agent_id == "code_agent":
            return SubAgentContext(
                agent_id="delegate:code_agent",
                task=kwargs["task"],
                result_text="partial delegate output",
                success=False,
                latency_ms=5,
                error="FILE_NOT_FOUND: missing module",
            )
        return SubAgentContext(
            agent_id=f"delegate:{agent_id}",
            task=kwargs["task"],
            result_text="repair analysis",
            success=True,
            latency_ms=7,
        )

    async def _fake_chat_agent(chat_input):
        assert chat_input.sub_agent_context is not None
        assert chat_input.sub_agent_context.agent_id == "delegate_failure_recovery"
        assert chat_input.sub_agent_context.success is False
        assert chat_input.sub_agent_context.result_text == "repair analysis"
        assert chat_input.sub_agent_context.error == "FILE_NOT_FOUND: missing module"
        yield 'data: {"phase":"CHAT","route":"delegate"}\n\n'

    monkeypatch.setattr(
        "host_brain.app.dispatch_agent._hard_rule_route",
        lambda _message: ("delegate", "code_agent", "keyword:debug"),
    )
    monkeypatch.setattr(
        "host_brain.app.dispatch_agent._maybe_build_multi_step_plan",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr("host_brain.app.dispatch_agent._dispatch_to_sub_agent", _fake_dispatch_to_sub_agent)
    monkeypatch.setattr(
        "host_brain.app.dispatch_agent._build_failure_recovery_plan",
        lambda **_kwargs: {
            "agent_id": "repair_agent",
            "task": "repair the broken delegate environment",
            "summary": "auto repair running",
        },
    )
    monkeypatch.setattr("host_brain.app.dependencies.get_runtime_task_registry", lambda: registry)
    monkeypatch.setattr("host_brain.app.chat_agent.chat_agent_sse_generator", _fake_chat_agent)

    events = _run(
        _collect(
            dispatch_agent_sse_generator(
                request_body=SimpleNamespace(
                    reply_channel="GATEWAY",
                    suppress_edge_tts=False,
                    suppress_edge_vts=False,
                    thread_id="thread-delegate-heal",
                ),
                request_id="req-delegate-heal",
                session_id="sess-delegate-heal",
                ts_ms=4_000,
                intent_draft={"scene": "tool"},
                emotion_context={"emotion_tag": "NEUTRAL"},
                emotion_snapshot={},
                governor_result={"decision": "APPROVE"},
                intent_id="intent-delegate-heal",
                metrics=SimpleNamespace(),
                person_id="person-delegate-heal",
                sanitized_message="debug the missing module failure",
                recall_slot_text={},
                bond_summary={"stage": "friend"},
                retrieval_evidence_items=[],
                retrieval_summary={"status": "SKIPPED"},
                render_target_lang="zh",
                llm_runtime={"provider_name": "base", "model_id": "base"},
                pii_ingress_stats={},
            )
        )
    )

    assert len(events) == 3
    first = _parse_sse_event(events[0])
    assert first["event"] == "DELEGATING"
    assert first["agent"] == "code_agent"
    second = _parse_sse_event(events[1])
    assert second["event"] == "SELF_HEALING"
    assert second["agent"] == "repair_agent"
    third = _parse_sse_event(events[2])
    assert third["phase"] == "CHAT"
    assert third["route"] == "delegate"

    task = registry.get_by_request_id("req-delegate-heal")
    assert task is not None
    assert task.result_code == "FILE_NOT_FOUND"
    assert task.result_text == "partial delegate output"
    assert task.recovery_status == "DONE"
    assert task.recovery_result_text == "repair analysis"
    assert task.recovery_error is None
    assert dispatch_calls == [
        {
            "agent_id": "code_agent",
            "task": "debug the missing module failure",
            "previous_agent_result": None,
        },
        {
            "agent_id": "repair_agent",
            "task": "repair the broken delegate environment",
            "previous_agent_result": "FILE_NOT_FOUND: missing module",
        },
    ]


def test_dispatch_agent_sse_generator_skill_self_heals_without_retry(monkeypatch) -> None:
    registry = RuntimeTaskRegistry()

    async def _fake_execute_skill(**_kwargs):
        return SubAgentContext(
            agent_id="skill:browser_navigate",
            task="open browser",
            result_text="",
            success=False,
            latency_ms=4,
            error="BROWSER_UNAVAILABLE: missing browser runtime",
        )

    async def _fake_dispatch_to_sub_agent(**_kwargs):
        return SubAgentContext(
            agent_id="delegate:repair_agent",
            task="repair browser",
            result_text="browser repaired",
            success=True,
            latency_ms=5,
        )

    async def _fake_chat_agent(chat_input):
        assert chat_input.sub_agent_context is not None
        assert chat_input.sub_agent_context.agent_id == "skill_failure_recovery"
        assert chat_input.sub_agent_context.success is False
        assert chat_input.sub_agent_context.result_text == "browser repaired"
        assert chat_input.sub_agent_context.error == "BROWSER_UNAVAILABLE: missing browser runtime"
        yield 'data: {"phase":"CHAT","route":"skill-recovery"}\n\n'

    monkeypatch.setattr(
        "host_brain.app.dispatch_agent._hard_rule_route",
        lambda _message: ("skill", "browser_navigate", "keyword:open"),
    )
    monkeypatch.setattr(
        "host_brain.app.dispatch_agent._maybe_build_multi_step_plan",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr("host_brain.app.dispatch_agent._execute_skill", _fake_execute_skill)
    monkeypatch.setattr("host_brain.app.dispatch_agent._dispatch_to_sub_agent", _fake_dispatch_to_sub_agent)
    monkeypatch.setattr(
        "host_brain.app.dispatch_agent._build_failure_recovery_plan",
        lambda **_kwargs: {
            "agent_id": "repair_agent",
            "task": "repair browser runtime",
            "summary": "repair running",
            "retry_original": False,
        },
    )
    monkeypatch.setattr("host_brain.app.dependencies.get_runtime_task_registry", lambda: registry)
    monkeypatch.setattr("host_brain.app.chat_agent.chat_agent_sse_generator", _fake_chat_agent)

    events = _run(
        _collect(
            dispatch_agent_sse_generator(
                request_body=SimpleNamespace(reply_channel="WEB", thread_id="thread-skill-heal"),
                request_id="req-skill-heal",
                session_id="sess-skill-heal",
                ts_ms=7_000,
                intent_draft={},
                emotion_context={"emotion_tag": "NEUTRAL"},
                emotion_snapshot={},
                governor_result={"decision": "APPROVE"},
                intent_id="intent-skill-heal",
                metrics=SimpleNamespace(),
                person_id="person-skill-heal",
                sanitized_message="open the browser",
                recall_slot_text={},
                bond_summary={"stage": "friend"},
                retrieval_evidence_items=[],
                retrieval_summary={},
                render_target_lang="zh",
                llm_runtime={"provider_name": "base", "model_id": "base"},
                pii_ingress_stats={},
            )
        )
    )

    assert len(events) == 3
    assert _parse_sse_event(events[0])["event"] == "SKILL_RUNNING"
    assert _parse_sse_event(events[1])["event"] == "SELF_HEALING"
    assert _parse_sse_event(events[2])["route"] == "skill-recovery"
    task = registry.get_by_request_id("req-skill-heal")
    assert task is not None
    assert task.result_code == "BROWSER_UNAVAILABLE"
    assert task.recovery_status == "DONE"
    assert task.recovery_result_text == "browser repaired"


def test_dispatch_agent_sse_generator_skill_retries_after_repair(monkeypatch) -> None:
    registry = RuntimeTaskRegistry()

    async def _fake_execute_skill(**_kwargs):
        return SubAgentContext(
            agent_id="skill:browser_navigate",
            task="open browser",
            result_text="",
            success=False,
            latency_ms=3,
            error="BROWSER_UNAVAILABLE: missing browser runtime",
        )

    async def _fake_dispatch_to_sub_agent(**_kwargs):
        return SubAgentContext(
            agent_id="delegate:repair_agent",
            task="repair browser",
            result_text="browser repaired",
            success=True,
            latency_ms=4,
        )

    async def _fake_retry_after_repair(**_kwargs):
        return SubAgentContext(
            agent_id="skill:browser_navigate",
            task="open browser",
            result_text="browser opened after repair",
            success=True,
            latency_ms=5,
        )

    async def _fake_chat_agent(chat_input):
        assert chat_input.sub_agent_context is not None
        assert chat_input.sub_agent_context.agent_id == "skill:browser_navigate"
        assert chat_input.sub_agent_context.success is True
        assert chat_input.sub_agent_context.result_text == "browser opened after repair"
        yield 'data: {"phase":"CHAT","route":"skill-retry"}\n\n'

    monkeypatch.setattr(
        "host_brain.app.dispatch_agent._hard_rule_route",
        lambda _message: ("skill", "browser_navigate", "keyword:open"),
    )
    monkeypatch.setattr(
        "host_brain.app.dispatch_agent._maybe_build_multi_step_plan",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr("host_brain.app.dispatch_agent._execute_skill", _fake_execute_skill)
    monkeypatch.setattr("host_brain.app.dispatch_agent._dispatch_to_sub_agent", _fake_dispatch_to_sub_agent)
    monkeypatch.setattr("host_brain.app.dispatch_agent._retry_after_repair", _fake_retry_after_repair)
    monkeypatch.setattr(
        "host_brain.app.dispatch_agent._build_failure_recovery_plan",
        lambda **_kwargs: {
            "agent_id": "repair_agent",
            "task": "repair browser runtime",
            "summary": "repair running",
            "retry_original": True,
        },
    )
    monkeypatch.setattr("host_brain.app.dependencies.get_runtime_task_registry", lambda: registry)
    monkeypatch.setattr("host_brain.app.chat_agent.chat_agent_sse_generator", _fake_chat_agent)

    events = _run(
        _collect(
            dispatch_agent_sse_generator(
                request_body=SimpleNamespace(reply_channel="WEB", thread_id="thread-skill-retry"),
                request_id="req-skill-retry",
                session_id="sess-skill-retry",
                ts_ms=8_000,
                intent_draft={},
                emotion_context={"emotion_tag": "NEUTRAL"},
                emotion_snapshot={},
                governor_result={"decision": "APPROVE"},
                intent_id="intent-skill-retry",
                metrics=SimpleNamespace(),
                person_id="person-skill-retry",
                sanitized_message="open the browser again",
                recall_slot_text={},
                bond_summary={"stage": "friend"},
                retrieval_evidence_items=[],
                retrieval_summary={},
                render_target_lang="zh",
                llm_runtime={"provider_name": "base", "model_id": "base"},
                pii_ingress_stats={},
            )
        )
    )

    assert len(events) == 4
    assert _parse_sse_event(events[0])["event"] == "SKILL_RUNNING"
    assert _parse_sse_event(events[1])["event"] == "SELF_HEALING"
    assert _parse_sse_event(events[2])["event"] == "RETRYING"
    assert _parse_sse_event(events[3])["route"] == "skill-retry"
    task = registry.get_by_request_id("req-skill-retry")
    assert task is not None
    assert task.result_code is None
    assert task.error is None
    assert task.result_text == "browser opened after repair"


def test_dispatch_agent_sse_generator_unknown_route_falls_back_to_chat(monkeypatch) -> None:
    async def _fake_chat_agent(_chat_input):
        yield 'data: {"phase":"CHAT","route":"unknown-fallback"}\n\n'

    monkeypatch.setattr(
        "host_brain.app.dispatch_agent._hard_rule_route",
        lambda _message: ("weird_route", "", "bad"),
    )
    monkeypatch.setattr("host_brain.app.dependencies.get_runtime_task_registry", lambda: RuntimeTaskRegistry())
    monkeypatch.setattr("host_brain.app.chat_agent.chat_agent_sse_generator", _fake_chat_agent)

    events = _run(
        _collect(
            dispatch_agent_sse_generator(
                request_body=SimpleNamespace(reply_channel="WEB", thread_id="thread-unknown"),
                request_id="req-unknown-route",
                session_id="sess-unknown-route",
                ts_ms=9_000,
                intent_draft={},
                emotion_context={"emotion_tag": "NEUTRAL"},
                emotion_snapshot={},
                governor_result={"decision": "APPROVE"},
                intent_id="intent-unknown-route",
                metrics=SimpleNamespace(),
                person_id="person-unknown-route",
                sanitized_message="do something odd",
                recall_slot_text={},
                bond_summary={},
                retrieval_evidence_items=[],
                retrieval_summary={},
                render_target_lang="zh",
                llm_runtime={"provider_name": "base", "model_id": "base"},
                pii_ingress_stats={},
            )
        )
    )

    assert len(events) == 1
    assert _parse_sse_event(events[0])["route"] == "unknown-fallback"
