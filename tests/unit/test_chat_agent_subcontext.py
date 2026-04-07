from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.app.chat_agent import (
    ChatAgentInput,
    SubAgentContext,
    _airlock_sanitize_output,
    _build_chat_agent_fallback_reply,
    _build_chat_agent_quality_fallback_reply,
    _build_deterministic_sub_agent_reply,
    _build_installed_capabilities_reply,
    _build_plan_execution_reply_cn,
    _build_sub_agent_context_prompt,
    _extract_visible_text,
    _extract_tts_text,
    _format_discovery_source_names_cn,
    _format_hub_skill_name_list_cn,
    _infer_emotion_tag_for_tts,
    _post_speak_sentence,
    _render_plan_step_failure_cn,
    _render_plan_step_success_cn,
    _should_buffer_stream_output,
    _build_reply_preview,
    _strip_internal_delegate_markers,
    chat_agent_sse_generator,
)
from host_brain.app.prompt_template_store import (
    PromptTemplateStore,
    resolve_prompt_template_json_merged,
)


def _run(coro):
    return asyncio.run(coro)


async def _collect_events(async_iterable) -> list[str]:
    items = []
    async for item in async_iterable:
        items.append(item)
    return items


def test_chat_agent_passes_sub_agent_context_prompt_to_top_agent_prompt(monkeypatch) -> None:
    captured = {}

    def _fake_build_top_agent_prompt(**kwargs):  # noqa: ANN001
        captured.update(kwargs)
        return "PROMPT_TEXT", "SYSTEM_PROMPT", {"temperature": 0.2}

    async def _empty_chunks(**_kwargs):  # noqa: ANN001
        if False:
            yield ""

    def _fake_apply_response_intelligence(**_kwargs):  # noqa: ANN001
        return "final answer", {"reflection": {"enabled": False}}

    monkeypatch.setenv("HOST_STREAM_REFLECTION_BUFFER_ENABLED", "false")
    monkeypatch.setenv("HOST_STREAM_TTS_ENABLED", "false")

    monkeypatch.setattr("host_brain.app.top_agent_prompt.build_top_agent_prompt", _fake_build_top_agent_prompt)
    monkeypatch.setattr("host_brain.integrations.llm_helpers.astream_llm_chunks", _empty_chunks)
    monkeypatch.setattr("host_brain.app.intelligent_runtime.apply_response_intelligence", _fake_apply_response_intelligence)

    agent_input = ChatAgentInput(
        request_id="req-chat-agent-subctx",
        session_id="session-001",
        user_message="那应该怎么做呢",
        person_id="default",
        render_target_lang="zh",
        sub_agent_context=SubAgentContext(
            agent_id="code_agent",
            task="给出贪吃蛇实现思路",
            result_text="先定义地图和蛇身数据结构，再写移动、吃食物、碰撞检测。",
            success=True,
        ),
        llm_runtime={
            "available": True,
            "provider_name": "mock",
            "model_id": "mock-model",
            "timeout_ms": 1000,
            "llm_api_key": "",
            "base_url_override": "",
            "agent_gen_params": {"temperature": 0.2},
        },
    )

    _run(_collect_events(chat_agent_sse_generator(agent_input)))

    extra_context = str(captured.get("extra_context_prompt") or "")
    assert extra_context
    assert "不要提 Agent、工具、草稿、JSON、ReAct、网关" in extra_context
    assert "先给用户一个简洁结论，再自然展开" in extra_context


def test_chat_agent_phatic_fastpath_context_is_semantic_not_literal(monkeypatch) -> None:
    captured = {}

    def _fake_build_top_agent_prompt(**kwargs):  # noqa: ANN001
        captured.update(kwargs)
        return "PROMPT_TEXT", "SYSTEM_PROMPT", {"temperature": 0.2}

    async def _empty_chunks(**_kwargs):  # noqa: ANN001
        if False:
            yield ""

    def _fake_apply_response_intelligence(**_kwargs):  # noqa: ANN001
        return "final answer", {"reflection": {"enabled": False}}

    monkeypatch.setenv("HOST_STREAM_REFLECTION_BUFFER_ENABLED", "false")
    monkeypatch.setenv("HOST_STREAM_TTS_ENABLED", "false")

    monkeypatch.setattr("host_brain.app.top_agent_prompt.build_top_agent_prompt", _fake_build_top_agent_prompt)
    monkeypatch.setattr("host_brain.integrations.llm_helpers.astream_llm_chunks", _empty_chunks)
    monkeypatch.setattr("host_brain.app.intelligent_runtime.apply_response_intelligence", _fake_apply_response_intelligence)

    agent_input = ChatAgentInput(
        request_id="req-chat-agent-phatic",
        session_id="session-001",
        user_message="你好啊",
        person_id="default",
        render_target_lang="zh",
        sub_agent_context=SubAgentContext(
            agent_id="phatic_fastpath",
            task="根据当前寒暄语境生成简短自然回复",
            result_text="场景：用户在打招呼。目标：自然回应问候，并轻轻把话题接住。上一条你的回复是：嗯，你好呀……我在呢。不要重复这个句子。",
            success=True,
        ),
        llm_runtime={
            "available": True,
            "provider_name": "mock",
            "model_id": "mock-model",
            "timeout_ms": 1000,
            "llm_api_key": "",
            "base_url_override": "",
            "agent_gen_params": {"temperature": 0.2},
        },
    )

    _run(_collect_events(chat_agent_sse_generator(agent_input)))

    extra_context = str(captured.get("extra_context_prompt") or "")
    assert "这是寒暄场景的语义目标，不是最终台词" in extra_context
    assert "只保留核心意图，用夏目蓝自己的说话方式重新组织" in extra_context


def test_chat_agent_greeting_fallback_is_not_generic_wait(monkeypatch) -> None:
    events = []

    def _fake_build_top_agent_prompt(**_kwargs):  # noqa: ANN001
        return "PROMPT_TEXT", "SYSTEM_PROMPT", {"temperature": 0.2}

    def _identity_response_intelligence(**kwargs):  # noqa: ANN001
        return kwargs.get("narrative") or "", {"reflection": {"enabled": False}}

    async def _raising_chunks(**_kwargs):  # noqa: ANN001
        raise RuntimeError("llm unavailable")
        yield  # pragma: no cover

    monkeypatch.setenv("HOST_STREAM_REFLECTION_BUFFER_ENABLED", "false")
    monkeypatch.setenv("HOST_STREAM_TTS_ENABLED", "false")
    monkeypatch.setattr("host_brain.app.top_agent_prompt.build_top_agent_prompt", _fake_build_top_agent_prompt)
    monkeypatch.setattr("host_brain.integrations.llm_helpers.astream_llm_chunks", _raising_chunks)
    monkeypatch.setattr("host_brain.app.intelligent_runtime.apply_response_intelligence", _identity_response_intelligence)

    agent_input = ChatAgentInput(
        request_id="req-chat-fallback-greet",
        session_id="session-001",
        user_message="你好啊",
        person_id="default",
        render_target_lang="zh",
        llm_runtime={
            "available": True,
            "provider_name": "mock",
            "model_id": "mock-model",
            "timeout_ms": 1000,
            "llm_api_key": "",
            "base_url_override": "",
            "agent_gen_params": {"temperature": 0.2},
        },
    )

    events = _run(_collect_events(chat_agent_sse_generator(agent_input)))
    commit = [e for e in events if '"phase": "COMMIT"' in e][-1]
    assert "等一下呢" not in commit
    assert '"narrative": "你好啊。"' in commit


def test_chat_agent_strength_fallback_answers_question(monkeypatch) -> None:
    def _fake_build_top_agent_prompt(**_kwargs):  # noqa: ANN001
        return "PROMPT_TEXT", "SYSTEM_PROMPT", {"temperature": 0.2}

    async def _raising_chunks(**_kwargs):  # noqa: ANN001
        raise RuntimeError("llm unavailable")
        yield  # pragma: no cover

    monkeypatch.setenv("HOST_STREAM_REFLECTION_BUFFER_ENABLED", "false")
    monkeypatch.setenv("HOST_STREAM_TTS_ENABLED", "false")
    monkeypatch.setattr("host_brain.app.top_agent_prompt.build_top_agent_prompt", _fake_build_top_agent_prompt)
    monkeypatch.setattr("host_brain.integrations.llm_helpers.astream_llm_chunks", _raising_chunks)
    monkeypatch.setattr("host_brain.app.dependencies.get_skill_registry", lambda: type("R", (), {"list_skill_names": lambda self: ["session_summary", "memory_recall", "read_only_status"]})())
    monkeypatch.setattr("host_brain.app.agents.tool_registry.get_tools_for_agent", lambda agent_id, allow_side_effects=False: [type("T", (), {"tool_id": f"{agent_id}_tool_a"})(), type("T", (), {"tool_id": f"{agent_id}_tool_b"})()])

    agent_input = ChatAgentInput(
        request_id="req-chat-fallback-strength",
        session_id="session-001",
        user_message="你有什么特长啊",
        person_id="default",
        render_target_lang="zh",
        llm_runtime={
            "available": True,
            "provider_name": "mock",
            "model_id": "mock-model",
            "timeout_ms": 1000,
            "llm_api_key": "",
            "base_url_override": "",
            "agent_gen_params": {"temperature": 0.2},
        },
    )

    events = _run(_collect_events(chat_agent_sse_generator(agent_input)))
    commit = [e for e in events if '"phase": "COMMIT"' in e][-1]
    assert "等一下呢" not in commit
    assert "我现在能调动的能力，大致是这些" in commit
    assert "session_summary" in commit
    assert "code_agent_tool_a" in commit


def test_strip_internal_delegate_markers_removes_delegate_placeholder() -> None:
    cleaned = _strip_internal_delegate_markers("我来帮你看一下。 [DELEGATE:intent_agent:打开文件] {agent_result}")

    assert cleaned == "我来帮你看一下。"


def test_build_sub_agent_context_prompt_keeps_failure_context_without_result_text() -> None:
    prompt = _build_sub_agent_context_prompt(
        SubAgentContext(
            agent_id="skill:browser_navigate",
            task="打开网页 https://example.com",
            result_text="",
            success=False,
            error="BROWSER_UNAVAILABLE: playwright not installed",
        )
    )

    assert "执行状态：失败" in prompt
    assert "playwright not installed" in prompt



def test_build_sub_agent_context_prompt_for_task_progress() -> None:
    prompt = _build_sub_agent_context_prompt(
        SubAgentContext(
            agent_id="task_progress_direct",
            task="task_progress_query",
            result_text="当前有个任务还在进行，是 web_search。已经跑了 3.2s。",
            success=True,
        )
    )

    assert "当前任务进度" in prompt
    assert "web_search" in prompt


def test_build_sub_agent_context_prompt_failure_includes_recovery_hint() -> None:
    prompt = _build_sub_agent_context_prompt(
        SubAgentContext(
            agent_id="skill_failure_recovery",
            task="打开网页",
            result_text="建议先安装浏览器后端，再重试。",
            success=False,
            error="BROWSER_UNAVAILABLE: FAILED",
        )
    )

    assert "执行状态：失败" in prompt
    assert "补充信息：建议先安装浏览器后端，再重试。" in prompt



def test_extract_visible_text_unwraps_fenced_json() -> None:
    raw = "```json\n{\"narrative\": {\"text_zh\": \"进度还在继续。\"}}\n```"

    assert _extract_visible_text(raw) == "进度还在继续。"



def test_build_sub_agent_context_prompt_for_plan_execution() -> None:
    prompt = _build_sub_agent_context_prompt(
        SubAgentContext(
            agent_id="plan_execution",
            task="先搜索，再记住",
            result_text="第1步（web_search）已完成：找到官网。\n第2步（memory_store）已完成：已经记住偏好。",
            success=True,
        )
    )

    assert "多步骤计划执行结果" in prompt
    assert "web_search" in prompt
    assert "memory_store" in prompt



def test_build_sub_agent_context_prompt_uses_prompt_template_override(monkeypatch) -> None:
    ledger = SQLiteLedger(":memory:", check_same_thread=False)
    store = PromptTemplateStore(ledger=ledger, cache_ttl_ms=0)
    store.upsert_template(
        "chat_agent.sub_agent_context.prompt_templates",
        content=json.dumps(
            {
                "recent_turn_direct": (
                    "[已核实事实]\n"
                    "  内容：{result_text}\n"
                    "  说明：直接用夏目蓝自己的话说出来。\n"
                )
            },
            ensure_ascii=False,
        ),
        enabled=True,
    )

    import host_brain.app.dependencies as deps

    monkeypatch.setattr(deps, "_ledger", ledger, raising=False)
    monkeypatch.setattr(deps, "_prompt_template_store", store, raising=False)

    prompt = _build_sub_agent_context_prompt(
        SubAgentContext(
            agent_id="recent_turn_direct",
            task="recent_turn_direct",
            result_text="刚才确认过了，今天会下雨。",
            success=True,
        )
    )

    assert "[已核实事实]" in prompt
    assert "今天会下雨" in prompt
    assert "我看不到" not in prompt


def test_chat_agent_runtime_texts_override_helper_defaults(monkeypatch) -> None:
    ledger = SQLiteLedger(":memory:", check_same_thread=False)
    store = PromptTemplateStore(ledger=ledger, cache_ttl_ms=0)
    store.upsert_template(
        "chat_agent.runtime_texts",
        content=json.dumps(
            {
                "list_more_suffix": " 以及更多",
                "sub_agent_recovery_hint_line": "  备注：{result_text}\n",
                "unknown_error": "暂时还说不清",
                "unnamed_skill_name": "匿名技能",
                "author_suffix": "（来自 {author}）",
            },
            ensure_ascii=False,
        ),
        enabled=True,
    )

    import host_brain.app.dependencies as deps

    monkeypatch.setattr(deps, "_ledger", ledger, raising=False)
    monkeypatch.setattr(deps, "_prompt_template_store", store, raising=False)

    prompt = _build_sub_agent_context_prompt(
        SubAgentContext(
            agent_id="tool_agent",
            task="处理一下",
            result_text="建议先重试一次。",
            error="",
            success=False,
        )
    )
    reply = _build_deterministic_sub_agent_reply(
        SubAgentContext(
            agent_id="skill:hub_skill_search",
            task="搜索技能 天气",
            result_text="",
            success=True,
            payload={
                "detail": {
                    "total": 1,
                    "skills": [
                        {"name": "", "description": "查询实时天气", "author": "tester"},
                    ],
                },
            },
        ),
        render_target_lang="zh",
    )

    assert "备注：建议先重试一次。" in prompt
    assert "暂时还说不清" in prompt
    assert _format_hub_skill_name_list_cn(["a", "b", "c"], limit=2) == "a、b 以及更多"
    assert reply is not None
    assert "匿名技能" in reply
    assert "来自 tester" in reply


def test_build_deterministic_sub_agent_reply_for_plan_execution() -> None:
    reply = _build_deterministic_sub_agent_reply(
        SubAgentContext(
            agent_id="plan_execution",
            task="先搜索，再记住",
            result_text="",
            success=True,
            payload={
                "parallel_enabled": False,
                "total_steps": 2,
                "success_count": 2,
                "steps": [
                    {"step_index": 1, "target_id": "web_search", "description": "搜索 OpenAI 官网", "task_description": "搜索 OpenAI 官网", "success": True, "result_text": "done:web_search", "error": "", "recovery_result_text": ""},
                    {"step_index": 2, "target_id": "memory_store", "description": "记住我最喜欢的颜色是蓝色", "task_description": "记住我最喜欢的颜色是蓝色", "success": True, "result_text": "done:memory_store", "error": "", "recovery_result_text": ""},
                ],
            },
        ),
        render_target_lang="zh",
    )

    assert reply is not None
    assert "我把这件事分成了2步" in reply
    assert "第1步" in reply
    assert "第2步" in reply
    assert "OpenAI 官网" in reply
    assert "记住你最喜欢的颜色是蓝色了" in reply


def test_build_deterministic_sub_agent_reply_uses_prompt_template_override(monkeypatch) -> None:
    ledger = SQLiteLedger(":memory:", check_same_thread=False)
    store = PromptTemplateStore(ledger=ledger, cache_ttl_ms=0)
    store.upsert_template(
        "chat_agent.plan_execution.reply_templates",
        content=json.dumps(
            {
                "step_success": {
                    "web_search": "第{step_index}步，我先查了 {topic}。",
                },
                "summary": {
                    "intro_sequential": "这件事我先按 {total_steps} 步替你顺了一遍。",
                },
            },
            ensure_ascii=False,
        ),
        enabled=True,
    )

    import host_brain.app.dependencies as deps

    monkeypatch.setattr(deps, "_ledger", ledger, raising=False)
    monkeypatch.setattr(deps, "_prompt_template_store", store, raising=False)

    reply = _build_deterministic_sub_agent_reply(
        SubAgentContext(
            agent_id="plan_execution",
            task="先搜索",
            result_text="",
            success=True,
            payload={
                "parallel_enabled": False,
                "total_steps": 1,
                "success_count": 1,
                "steps": [
                    {
                        "step_index": 1,
                        "target_id": "web_search",
                        "description": "搜索 OpenAI 官网",
                        "task_description": "搜索 OpenAI 官网",
                        "success": True,
                        "result_text": "done:web_search",
                        "error": "",
                        "recovery_result_text": "",
                    }
                ],
            },
        ),
        render_target_lang="zh",
    )

    assert reply is not None
    assert "这件事我先按 1 步替你顺了一遍。" in reply
    assert "第1步，我先查了 OpenAI 官网。" in reply
    assert "现在这些都已经处理好了。" in reply


def test_chat_agent_gateway_reply_depth_hint_uses_template_override(monkeypatch) -> None:
    ledger = SQLiteLedger(":memory:", check_same_thread=False)
    store = PromptTemplateStore(ledger=ledger, cache_ttl_ms=0)
    store.upsert_template(
        "chat_agent.runtime_texts",
        content=json.dumps(
            {
                "gateway_reply_depth_hint": "（请短一点，适合消息框）",
            },
            ensure_ascii=False,
        ),
        enabled=True,
    )

    import host_brain.app.dependencies as deps

    monkeypatch.setattr(deps, "_ledger", ledger, raising=False)
    monkeypatch.setattr(deps, "_prompt_template_store", store, raising=False)

    captured = {}

    def _fake_build_top_agent_prompt(**kwargs):  # noqa: ANN001
        captured.update(kwargs)
        return "PROMPT_TEXT", "SYSTEM_PROMPT", {"temperature": 0.2}

    async def _empty_chunks(**_kwargs):  # noqa: ANN001
        if False:
            yield ""

    def _fake_apply_response_intelligence(**_kwargs):  # noqa: ANN001
        return "final answer", {"reflection": {"enabled": False}}

    monkeypatch.setenv("HOST_STREAM_REFLECTION_BUFFER_ENABLED", "false")
    monkeypatch.setenv("HOST_STREAM_TTS_ENABLED", "false")
    monkeypatch.setattr("host_brain.app.top_agent_prompt.build_top_agent_prompt", _fake_build_top_agent_prompt)
    monkeypatch.setattr("host_brain.integrations.llm_helpers.astream_llm_chunks", _empty_chunks)
    monkeypatch.setattr("host_brain.app.intelligent_runtime.apply_response_intelligence", _fake_apply_response_intelligence)

    agent_input = ChatAgentInput(
        request_id="req-chat-agent-gateway-hint",
        session_id="session-gateway-hint",
        user_message="帮我总结一下",
        person_id="default",
        render_target_lang="zh",
        reply_depth_policy="gateway",
        llm_runtime={
            "available": True,
            "provider_name": "mock",
            "model_id": "mock-model",
            "timeout_ms": 1000,
            "llm_api_key": "",
            "base_url_override": "",
            "agent_gen_params": {"temperature": 0.2},
        },
    )

    _run(_collect_events(chat_agent_sse_generator(agent_input)))

    assert str(captured.get("user_message") or "").endswith("（请短一点，适合消息框）")


def test_chat_agent_plan_execution_uses_deterministic_renderer(monkeypatch) -> None:
    async def _unexpected_chunks(**_kwargs):  # noqa: ANN001
        raise AssertionError("llm stream should not be called for deterministic plan rendering")
        if False:
            yield ""

    monkeypatch.setenv("HOST_STREAM_REFLECTION_BUFFER_ENABLED", "false")
    monkeypatch.setenv("HOST_STREAM_TTS_ENABLED", "false")
    monkeypatch.setattr("host_brain.integrations.llm_helpers.astream_llm_chunks", _unexpected_chunks)

    def _fake_apply_response_intelligence(**kwargs):  # noqa: ANN001
        return kwargs["narrative"], {"reflection": {"enabled": False}}

    monkeypatch.setattr("host_brain.app.intelligent_runtime.apply_response_intelligence", _fake_apply_response_intelligence)

    async def _fake_runtime_async(**_kwargs):  # noqa: ANN001
        return {"available": True, "provider_name": "mock", "model_id": "mock-model", "timeout_ms": 1000, "llm_api_key": "", "base_url_override": ""}

    monkeypatch.setattr("host_brain.app.graph_config_builder.resolve_host_llm_runtime_async", _fake_runtime_async)

    agent_input = ChatAgentInput(
        request_id="req-plan-render-001",
        session_id="session-plan-render-001",
        user_message="先搜索，再记住",
        person_id="person-001",
        emotion_context={},
        emotion_snapshot={},
        bond_summary={},
        sub_agent_context=SubAgentContext(
            agent_id="plan_execution",
            task="先搜索，再记住",
            result_text="",
            success=True,
            payload={
                "parallel_enabled": False,
                "total_steps": 2,
                "success_count": 2,
                "steps": [
                    {"step_index": 1, "target_id": "web_search", "description": "搜索 OpenAI 官网", "task_description": "搜索 OpenAI 官网", "success": True, "result_text": "done:web_search", "error": "", "recovery_result_text": ""},
                    {"step_index": 2, "target_id": "memory_store", "description": "记住我最喜欢的颜色是蓝色", "task_description": "记住我最喜欢的颜色是蓝色", "success": True, "result_text": "done:memory_store", "error": "", "recovery_result_text": ""},
                ],
            },
        ),
        render_target_lang="zh",
        ts_ms=1000,
    )

    events = _run(_collect_events(chat_agent_sse_generator(agent_input)))
    commit = next(item for item in events if '"phase": "COMMIT"' in item)

    assert "OpenAI 官网" in commit
    assert "蓝色" in commit
    assert "deterministic-plan-renderer" in commit



def test_build_deterministic_sub_agent_reply_for_hub_skill_install() -> None:
    reply = _build_deterministic_sub_agent_reply(
        SubAgentContext(
            agent_id="skill:hub_skill_install",
            task="安装技能 github:owner/repo/demo_skill",
            result_text="skill installed, auto-repaired, and activated",
            success=True,
            payload={
                "action": "install",
                "detail": {
                    "success": True,
                    "name": "demo_skill",
                    "activated": True,
                    "repair_attempted": True,
                    "repair_succeeded": True,
                    "repair_actions": 2,
                    "active_skill_names": ["demo_skill", "web_search"],
                    "message": "skill installed, auto-repaired, and activated",
                },
            },
        ),
        render_target_lang="zh",
    )

    assert reply is not None
    assert "demo_skill" in reply
    assert "接进我的能力里了" in reply
    assert "依赖问题" in reply



def test_build_deterministic_sub_agent_reply_for_hub_skill_search() -> None:
    reply = _build_deterministic_sub_agent_reply(
        SubAgentContext(
            agent_id="skill:hub_skill_search",
            task="搜索技能 天气",
            result_text="已搜索到 2 个相关技能",
            success=True,
            payload={
                "action": "search",
                "detail": {
                    "total": 2,
                    "skills": [
                        {"name": "weather_now", "description": "查询实时天气", "author": "tester"},
                        {"name": "weather_forecast", "description": "查询天气预报", "author": "tester2"},
                    ],
                },
            },
        ),
        render_target_lang="zh",
    )

    assert reply is not None
    assert "2 个相关技能" in reply
    assert "weather_now" in reply
    assert "weather_forecast" in reply



def test_chat_agent_hub_skill_install_uses_deterministic_renderer(monkeypatch) -> None:
    async def _unexpected_chunks(**_kwargs):  # noqa: ANN001
        raise AssertionError("llm stream should not be called for deterministic hub skill rendering")
        if False:
            yield ""

    monkeypatch.setenv("HOST_STREAM_REFLECTION_BUFFER_ENABLED", "false")
    monkeypatch.setenv("HOST_STREAM_TTS_ENABLED", "false")
    monkeypatch.setattr("host_brain.integrations.llm_helpers.astream_llm_chunks", _unexpected_chunks)

    def _fake_apply_response_intelligence(**kwargs):  # noqa: ANN001
        return kwargs["narrative"], {"reflection": {"enabled": False}}

    monkeypatch.setattr("host_brain.app.intelligent_runtime.apply_response_intelligence", _fake_apply_response_intelligence)

    async def _fake_runtime_async(**_kwargs):  # noqa: ANN001
        return {"available": True, "provider_name": "mock", "model_id": "mock-model", "timeout_ms": 1000, "llm_api_key": "", "base_url_override": ""}

    monkeypatch.setattr("host_brain.app.graph_config_builder.resolve_host_llm_runtime_async", _fake_runtime_async)

    agent_input = ChatAgentInput(
        request_id="req-hub-render-001",
        session_id="session-hub-render-001",
        user_message="安装技能 github:owner/repo/demo_skill",
        person_id="person-001",
        emotion_context={},
        emotion_snapshot={},
        bond_summary={},
        sub_agent_context=SubAgentContext(
            agent_id="skill:hub_skill_install",
            task="安装技能 github:owner/repo/demo_skill",
            result_text="skill installed, auto-repaired, and activated",
            success=True,
            payload={
                "action": "install",
                "detail": {
                    "success": True,
                    "name": "demo_skill",
                    "activated": True,
                    "repair_attempted": True,
                    "repair_succeeded": True,
                    "repair_actions": 2,
                    "active_skill_names": ["demo_skill", "web_search"],
                    "message": "skill installed, auto-repaired, and activated",
                },
            },
        ),
        render_target_lang="zh",
        ts_ms=1000,
    )

    events = _run(_collect_events(chat_agent_sse_generator(agent_input)))
    commit = next(item for item in events if '"phase": "COMMIT"' in item)

    assert "demo_skill" in commit
    assert "deterministic-plan-renderer" in commit


def test_chat_agent_deterministic_renderer_bypasses_unavailable_llm_runtime(monkeypatch) -> None:
    async def _unexpected_chunks(**_kwargs):  # noqa: ANN001
        raise AssertionError("llm stream should not be called for deterministic rendering")
        if False:
            yield ""

    monkeypatch.setenv("HOST_STREAM_REFLECTION_BUFFER_ENABLED", "false")
    monkeypatch.setenv("HOST_STREAM_TTS_ENABLED", "false")
    monkeypatch.setattr("host_brain.integrations.llm_helpers.astream_llm_chunks", _unexpected_chunks)

    def _fake_apply_response_intelligence(**kwargs):  # noqa: ANN001
        return kwargs["narrative"], {"reflection": {"enabled": False}}

    monkeypatch.setattr("host_brain.app.intelligent_runtime.apply_response_intelligence", _fake_apply_response_intelligence)

    agent_input = ChatAgentInput(
        request_id="req-hub-render-002",
        session_id="session-hub-render-002",
        user_message="蓝蓝，列出技能",
        person_id="person-001",
        emotion_context={},
        emotion_snapshot={},
        bond_summary={},
        sub_agent_context=SubAgentContext(
            agent_id="skill:hub_skill_list",
            task="列出技能",
            result_text="当前已安装 2 个 Hub 技能",
            success=True,
            payload={
                "action": "list",
                "detail": {
                    "total": 2,
                    "skills": [
                        {"name": "weather"},
                        {"name": "calendar"},
                    ],
                },
            },
        ),
        render_target_lang="zh",
        ts_ms=1000,
        llm_runtime={},
    )

    events = _run(_collect_events(chat_agent_sse_generator(agent_input)))
    commit = next(item for item in events if '"phase": "COMMIT"' in item)

    assert "Hub 技能" in commit
    assert "deterministic-plan-renderer" in commit
    assert "LLM_UNAVAILABLE" not in commit



def test_build_deterministic_sub_agent_reply_for_hub_skill_remove_failure_without_payload_detail() -> None:
    reply = _build_deterministic_sub_agent_reply(
        SubAgentContext(
            agent_id="skill:hub_skill_remove",
            task="卸载技能 smoke-local-skill",
            result_text="",
            success=False,
            error="SKILL_EXECUTION_FAILED: FAILED",
            payload={},
        ),
        render_target_lang="zh",
    )

    assert reply is not None
    assert "smoke-local-skill" in reply
    assert "还没有卸载成功" in reply



def test_build_deterministic_sub_agent_reply_for_hub_skill_search_registry_error() -> None:
    reply = _build_deterministic_sub_agent_reply(
        SubAgentContext(
            agent_id="skill:hub_skill_search",
            task="搜索技能 天气",
            result_text="",
            success=True,
            payload={
                "action": "search",
                "detail": {
                    "query": "天气",
                    "total": 0,
                    "registry_error": "HTTP Error 404: Not Found",
                    "skills": [],
                },
            },
        ),
        render_target_lang="zh",
    )

    assert reply is not None
    assert "仓库暂时不可用" in reply
    assert "404" in reply


def test_chat_agent_helper_sanitize_preview_emotion_and_tts_extract() -> None:
    with patch.dict("os.environ", {"HOST_QA_MAX_RESPONSE_CHARS": "12"}, clear=False):
        assert _airlock_sanitize_output("  hello\x00world! more text  ") == "helloworld!"

    assert _build_reply_preview("line1\r\nline2", limit=20) == "line1  line2"
    assert _build_reply_preview("abcdefghijklmnopqrstuvwxyz", limit=8) == "abcde..."

    assert _infer_emotion_tag_for_tts("哈哈，太好了", "NEUTRAL") == "HAPPY"
    assert _infer_emotion_tag_for_tts("抱歉，刚才失败了", "NEUTRAL") == "SAD"
    assert _infer_emotion_tag_for_tts("会いたかったし、大好きだよ。", "NEUTRAL") == "LOVING"
    assert _infer_emotion_tag_for_tts("whatever", "happy") == "HAPPY"
    assert _infer_emotion_tag_for_tts("", "") == "NEUTRAL"

    assert _extract_tts_text("") == ""
    assert _extract_tts_text('{"narrative":{"text_ja":"こんにちは","text_zh":"你好"}}') == "こんにちは"
    assert _extract_tts_text("{bad json") == ""


def test_chat_agent_helper_extract_visible_text_and_plan_renderers() -> None:
    assert _extract_visible_text("") == ""
    assert _extract_visible_text('{"narrative":{"text":"plain narrative"}}') == "plain narrative"
    assert _extract_visible_text("{bad json") == "{bad json"

    assert _render_plan_step_success_cn(
        {"step_index": 1, "target_id": "hub_skill_install", "description": "安装技能 weather"}
    ) == "第1步，我已经把这个 Hub 技能装好了。"
    assert _render_plan_step_success_cn(
        {"step_index": 2, "target_id": "intent_agent", "description": "确认方案"}
    ) == "第2步，我已经把执行方案确认好了。"
    assert "处理好了" in _render_plan_step_success_cn(
        {"step_index": 3, "target_id": "custom_tool", "description": "做点事情", "result_text": "done"}
    )

    failure = _render_plan_step_failure_cn(
        {
            "step_index": 4,
            "target_id": "web_search",
            "description": "搜索天气",
            "error": "NETWORK_UNAVAILABLE",
            "recovery_result_text": "稍后再试一次",
        }
    )
    assert "没有顺利完成" in failure
    assert "NETWORK_UNAVAILABLE" in failure
    assert "补救建议" in failure


def test_chat_agent_helper_hub_skill_builders_cover_remaining_variants() -> None:
    install_fail = _build_deterministic_sub_agent_reply(
        SubAgentContext(
            agent_id="skill:hub_skill_install",
            task="安装技能 demo_skill",
            result_text="install failed",
            success=False,
            error="install failed",
            payload={"detail": {"success": False, "message": "network down"}},
        ),
        render_target_lang="zh",
    )
    assert install_fail is not None
    assert "还没有装好" in install_fail
    assert "network down" in install_fail

    search_empty = _build_deterministic_sub_agent_reply(
        SubAgentContext(
            agent_id="skill:hub_skill_search",
            task="搜索技能 记账",
            result_text="",
            success=True,
            payload={"detail": {"total": 0, "skills": []}},
        ),
        render_target_lang="zh",
    )
    assert search_empty is not None
    assert "还没找到" in search_empty

    list_empty = _build_deterministic_sub_agent_reply(
        SubAgentContext(
            agent_id="skill:hub_skill_list",
            task="列出技能",
            result_text="",
            success=True,
            payload={"detail": {"total": 0, "skills": []}},
        ),
        render_target_lang="zh",
    )
    assert list_empty == "我这里现在还没有装新的 Hub 技能。"

    remove_success = _build_deterministic_sub_agent_reply(
        SubAgentContext(
            agent_id="skill:hub_skill_remove",
            task="删除技能 demo_skill",
            result_text="extra cleanup done",
            success=True,
            payload={"detail": {"success": True, "name": "demo_skill", "message": "extra cleanup done"}},
        ),
        render_target_lang="zh",
    )
    assert remove_success == "我已经把 demo_skill 卸载掉了。extra cleanup done。"


def test_chat_agent_helper_fallback_and_capability_replies(monkeypatch) -> None:
    assert (
        _build_chat_agent_fallback_reply(
            user_message="",
            sub_agent_ctx=SubAgentContext(agent_id="code_agent"),
            bond_summary=None,
            render_target_lang="zh",
        )
        == "我先把这件事整理成更清楚一点的说法给你……刚才那一步没有顺好，但我不会把内部草稿直接丢给你。"
    )
    assert "换个更清楚的说法" in _build_chat_agent_fallback_reply(
        user_message="",
        sub_agent_ctx=SubAgentContext(agent_id="recent_turn_direct"),
        bond_summary=None,
        render_target_lang="zh",
    )
    assert _build_chat_agent_fallback_reply(
        user_message="",
        sub_agent_ctx=SubAgentContext(agent_id="phatic_fastpath"),
        bond_summary=None,
        render_target_lang="zh",
    ) == "我在。"
    assert _build_chat_agent_fallback_reply(
        user_message="想你了",
        sub_agent_ctx=None,
        bond_summary=None,
        render_target_lang="zh",
    ) == "等我一下，我刚才这句没接稳。"
    assert _build_chat_agent_fallback_reply(
        user_message="在吗",
        sub_agent_ctx=None,
        bond_summary=None,
        render_target_lang="zh",
    ) == "我在。"
    assert _build_chat_agent_fallback_reply(
        user_message="你好",
        sub_agent_ctx=None,
        bond_summary=None,
        render_target_lang="zh",
    ) == "你好啊。"
    assert _build_chat_agent_fallback_reply(
        user_message="随便说点什么",
        sub_agent_ctx=None,
        bond_summary=None,
        render_target_lang="ja",
    ) == "ちょっと待ってね……今の一言を整え直す。"

    monkeypatch.setattr(
        "host_brain.app.dependencies.get_skill_registry",
        lambda: SimpleNamespace(list_skill_names=lambda: ["skill_a", "skill_b"]),
    )
    monkeypatch.setattr(
        "host_brain.app.agents.tool_registry.get_tools_for_agent",
        lambda agent_id, allow_side_effects=False: [
            SimpleNamespace(tool_id=f"{agent_id}_tool1"),
            SimpleNamespace(tool_id=f"{agent_id}_tool2"),
        ],
    )
    capabilities = _build_installed_capabilities_reply(render_target_lang="zh")
    assert "我现在能调动的能力" in capabilities
    assert "skill_a" in capabilities
    assert "code_agent_tool1" in capabilities

    monkeypatch.setattr(
        "host_brain.app.dependencies.get_skill_registry",
        lambda: (_ for _ in ()).throw(RuntimeError("registry broken")),
    )
    assert "我现在能做的事不少" in _build_installed_capabilities_reply(render_target_lang="zh")
    assert _build_installed_capabilities_reply(render_target_lang="ja") == "今の私が使える能力を整理して伝えるね。少し待って。"


def test_builtin_chat_agent_templates_are_clean() -> None:
    capabilities = resolve_prompt_template_json_merged("chat_agent.capabilities.reply_templates")
    runtime_texts = resolve_prompt_template_json_merged("chat_agent.runtime_texts")
    sub_agent_context = resolve_prompt_template_json_merged("chat_agent.sub_agent_context.prompt_templates")

    assert isinstance(capabilities, dict)
    assert capabilities.get("zh_intro") == "我现在能调动的能力，大致是这些："
    assert capabilities.get("ja_intro") == "今の私が使える能力を整理して伝えるね。少し待って。"

    assert isinstance(runtime_texts, dict)
    assert runtime_texts.get("default_step_target")
    assert runtime_texts.get("default_topic")
    assert runtime_texts.get("default_skill_name")
    assert runtime_texts.get("default_skill_query")
    assert runtime_texts.get("list_more_suffix") == " 等"
    assert runtime_texts.get("author_suffix")
    assert runtime_texts.get("unknown_error") == "未知错误"
    assert runtime_texts.get("gateway_reply_depth_hint")

    assert isinstance(sub_agent_context, dict)
    assert "[子 Agent 执行结果]" in str(sub_agent_context.get("failure") or "")
    assert "不要提内部模块" in str(sub_agent_context.get("failure") or "")
    assert "[当前已核实的信息]" in str(sub_agent_context.get("recent_turn_direct") or "")


def test_chat_agent_quality_fallback_reply_is_minimal_and_avoids_meta_copy() -> None:
    comfort = _build_chat_agent_quality_fallback_reply(
        user_message="今天有点难受，感觉什么都做不好。",
        sub_agent_ctx=None,
        bond_summary={"stage": "相识"},
        emotion_context={"emotion_tag": "SAD"},
        render_target_lang="zh",
    )
    assert comfort == "等我一下，我刚才这句没接稳。"
    assert "草稿" not in comfort

    repair = _build_chat_agent_quality_fallback_reply(
        user_message="我还是有点在意之前那次争执。",
        sub_agent_ctx=None,
        bond_summary={"stage": "朋友"},
        emotion_context={"emotion_tag": "ANXIOUS"},
        render_target_lang="zh",
    )
    assert repair == "等我一下，我刚才这句没接稳。"

    playful = _build_chat_agent_quality_fallback_reply(
        user_message="你最近是不是更会逗我开心了？",
        sub_agent_ctx=None,
        bond_summary={"stage": "朋友"},
        emotion_context={"emotion_tag": "HAPPY"},
        render_target_lang="zh",
    )
    assert playful == "等我一下，我刚才这句没接稳。"
    assert "草稿" not in playful

    close_default = _build_chat_agent_quality_fallback_reply(
        user_message="那我们接着聊吧",
        sub_agent_ctx=None,
        bond_summary={"stage": "挚友"},
        emotion_context={},
        render_target_lang="zh",
    )
    assert close_default == "等我一下，我刚才这句没接稳。"


def test_chat_agent_quality_fallback_reply_does_not_fake_semantic_affection_answers() -> None:
    confirmation = _build_chat_agent_quality_fallback_reply(
        user_message="你喜欢我吗",
        sub_agent_ctx=None,
        bond_summary={"stage": "相识"},
        emotion_context={},
        render_target_lang="zh",
        response_policy={"confession_guard": True, "intimacy_ceiling": 0.15},
    )
    assert confirmation == "等我一下，我刚才这句没接稳。"

    disclosure = _build_chat_agent_quality_fallback_reply(
        user_message="我喜欢你",
        sub_agent_ctx=None,
        bond_summary={"stage": "相识"},
        emotion_context={},
        render_target_lang="zh",
        response_policy={"confession_guard": True, "intimacy_ceiling": 0.15},
    )
    assert disclosure == "等我一下，我刚才这句没接稳。"


def test_chat_agent_quality_fallback_reply_does_not_fabricate_memory_continuity() -> None:
    reply = _build_chat_agent_quality_fallback_reply(
        user_message="你还记得我们之前说的那个展览吗",
        sub_agent_ctx=None,
        bond_summary={"stage": "挚友", "shared_memory_count": 2},
        emotion_context={},
        render_target_lang="zh",
        response_policy={"allow_followup": False},
        prompt_runtime_payload={
            "offline_state": {"anticipation_topics": ["那场展览"]},
            "motive_state": {"dominant_motive": "continuity", "continuity_drive": 0.91},
            "bond_narrative": {
                "shared_memories": ["那场展览和我们之前熬夜聊出来的计划"],
            },
        },
    )
    assert reply == "等我一下，我刚才这句没接稳。"
    assert "接" in reply
    assert "想先从哪一段继续" not in reply


def test_chat_agent_helper_reflection_buffer_and_sub_agent_prompt_variants(monkeypatch) -> None:
    monkeypatch.setenv("HOST_STREAM_REFLECTION_BUFFER_ENABLED", "off")
    assert _should_buffer_stream_output(
        emotion_context={},
        bond_summary={},
        response_policy={"allow_followup": False},
    ) is True
    assert _should_buffer_stream_output(
        emotion_context={},
        bond_summary={},
        response_policy={"max_sentences": 1},
    ) is False
    assert _should_buffer_stream_output(emotion_context={}, bond_summary={}) is False

    monkeypatch.delenv("HOST_STREAM_REFLECTION_BUFFER_ENABLED", raising=False)
    monkeypatch.setattr(
        "host_brain.app.dependencies.get_reflection_chain",
        lambda: SimpleNamespace(
            should_reflect=lambda **kwargs: kwargs["bond_stage"] == "CLOSE" and kwargs["turn_count"] == 3
        ),
    )
    assert _should_buffer_stream_output(
        emotion_context={"emotion_tag": "sad", "turn_count": 3},
        bond_summary={"stage": "CLOSE", "intent": "support"},
    ) is True

    monkeypatch.setattr(
        "host_brain.app.dependencies.get_reflection_chain",
        lambda: (_ for _ in ()).throw(RuntimeError("reflection unavailable")),
    )
    assert _should_buffer_stream_output(emotion_context={}, bond_summary={}) is False

    assert _build_sub_agent_context_prompt(None) == ""
    assert _build_sub_agent_context_prompt(
        SubAgentContext(agent_id="intent_agent", task="do work", result_text="", success=True)
    ) == ""
    generic_prompt = _build_sub_agent_context_prompt(
        SubAgentContext(agent_id="vision_agent", task="look at image", result_text="found the button", success=True)
    )
    assert "vision_agent" in generic_prompt
    assert "found the button" in generic_prompt


def test_chat_agent_helper_post_speak_sentence_invokes_edge_and_swallows_error(monkeypatch) -> None:
    post_speak = AsyncMock(return_value=None)
    monkeypatch.setattr("host_brain.channels.edge_rpc_client.post_speak", post_speak)
    _run(
        _post_speak_sentence(
            text="こんにちは",
            utterance_id="utt-001",
            emotion_tag="HAPPY",
            request_id="req-001",
            session_id="sess-001",
        )
    )
    post_speak.assert_awaited_once()
    assert post_speak.await_args.kwargs["lang_hint"] == "ja"

    post_speak_fail = AsyncMock(side_effect=RuntimeError("edge down"))
    monkeypatch.setattr("host_brain.channels.edge_rpc_client.post_speak", post_speak_fail)
    _run(
        _post_speak_sentence(
            text="こんにちは",
            utterance_id="utt-002",
            emotion_tag="HAPPY",
            request_id="req-002",
            session_id="sess-002",
        )
    )


def test_chat_agent_helper_remaining_hub_and_plan_variants() -> None:
    assert _format_discovery_source_names_cn([]) == ""
    assert _format_discovery_source_names_cn([{"name": "hub-a"}]) == "hub-a"
    assert _format_discovery_source_names_cn([{"name": "hub-a"}, "hub-b", "", None]) == "hub-a、hub-b"

    assert _format_hub_skill_name_list_cn([]) == ""
    assert _format_hub_skill_name_list_cn(["a", "b", "c", "d"], limit=2) == "a、b 等"

    install_unactivated = _build_deterministic_sub_agent_reply(
        SubAgentContext(
            agent_id="skill:hub_skill_install",
            task="安装技能 demo_skill",
            result_text="custom note",
            success=True,
            payload={
                "detail": {
                    "success": True,
                    "name": "demo_skill",
                    "activated": False,
                    "repair_attempted": True,
                    "repair_succeeded": False,
                }
            },
        ),
        render_target_lang="zh",
    )
    assert install_unactivated is not None
    assert "还没完全激活" in install_unactivated
    assert "没完全修好" in install_unactivated

    install_message = _build_deterministic_sub_agent_reply(
        SubAgentContext(
            agent_id="skill:hub_skill_install",
            task="安装技能 demo_skill",
            result_text="custom summary",
            success=True,
            payload={
                "detail": {
                    "success": True,
                    "name": "demo_skill",
                    "activated": True,
                    "message": "custom summary",
                    "active_skill_names": ["demo_skill"],
                }
            },
        ),
        render_target_lang="zh",
    )
    assert install_message is not None
    assert "补充一下，custom summary" in install_message

    failure_reply = _build_plan_execution_reply_cn(
        SubAgentContext(
            agent_id="plan_execution",
            task="先做 A 再做 B",
            result_text="",
            success=False,
            payload={
                "parallel_enabled": True,
                "total_steps": 2,
                "success_count": 1,
                "steps": [
                    {"step_index": 1, "target_id": "system_info", "description": "看系统", "success": True},
                    {"step_index": 2, "target_id": "file_read", "description": "读文件", "success": False, "error": "FILE_NOT_FOUND"},
                ],
            },
        )
    )
    assert failure_reply is not None
    assert "并行处理过了" in failure_reply
    assert "FILE_NOT_FOUND" in failure_reply
    assert "补救建议" in failure_reply

    assert _build_plan_execution_reply_cn(SubAgentContext(agent_id="plan_execution", payload={"steps": []})) is None
    assert _build_deterministic_sub_agent_reply(None, render_target_lang="zh") is None
    assert (
        _build_deterministic_sub_agent_reply(
            SubAgentContext(agent_id="skill:hub_skill_list", success=True, payload={"detail": {"total": 1, "skills": [{"name": "x"}]}}),
            render_target_lang="ja",
        )
        is None
    )
    assert _render_plan_step_success_cn({"step_index": 5, "target_id": "process_list"}) == "第5步，我也把进程列表看过了。"
    assert _render_plan_step_success_cn({"step_index": 6, "target_id": "network_status"}) == "第6步，我已经检查过网络状态了。"
    assert _render_plan_step_success_cn({"step_index": 7, "target_id": "file_read"}) == "第7步，我已经把文件内容看过了。"
    assert _render_plan_step_success_cn({"step_index": 8, "target_id": "code_agent"}) == "第8步，我已经把代码相关的处理整理好了。"


def test_chat_agent_prompt_builder_failure_returns_reject_commit(monkeypatch) -> None:
    monkeypatch.setenv("HOST_STREAM_REFLECTION_BUFFER_ENABLED", "false")
    monkeypatch.setenv("HOST_STREAM_TTS_ENABLED", "false")
    monkeypatch.setattr(
        "host_brain.app.top_agent_prompt.build_top_agent_prompt",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("prompt builder broken")),
    )

    agent_input = ChatAgentInput(
        request_id="req-chat-prompt-fail",
        session_id="sess-chat-prompt-fail",
        user_message="hello",
        person_id="person-001",
        render_target_lang="zh",
        llm_runtime={"available": True, "provider_name": "mock", "model_id": "mock", "timeout_ms": 1000},
    )

    events = _run(_collect_events(chat_agent_sse_generator(agent_input)))
    commit = next(item for item in events if '"phase": "COMMIT"' in item)
    assert "PROMPT_BUILD_FAILED" in commit


def test_chat_agent_runtime_resolve_failure_and_unavailable_runtime(monkeypatch) -> None:
    monkeypatch.setenv("HOST_STREAM_REFLECTION_BUFFER_ENABLED", "false")
    monkeypatch.setenv("HOST_STREAM_TTS_ENABLED", "false")
    monkeypatch.setattr(
        "host_brain.app.top_agent_prompt.build_top_agent_prompt",
        lambda **_kwargs: (
            "prompt",
            "system",
            {},
            {
                "offline_state": {"anticipation_topics": ["那场展览"]},
                "motive_state": {"dominant_motive": "continuity", "continuity_drive": 0.91},
                "bond_narrative": {"shared_memories": ["那场展览和我们之前熬夜聊出来的计划"]},
                "response_policy": {"allow_followup": False, "max_sentences": 2},
            },
        ),
    )
    captured: list[dict[str, object]] = []

    def _fake_post_process(**kwargs):  # noqa: ANN001
        captured.append(dict(kwargs))

        async def _done():
            return None

        return _done()

    async def _bad_runtime(**_kwargs):
        raise RuntimeError("runtime unavailable")

    monkeypatch.setattr("host_brain.app.graph_config_builder.resolve_host_llm_runtime_async", _bad_runtime)
    monkeypatch.setattr("host_brain.app.top_agent._async_post_process", _fake_post_process)

    agent_input = ChatAgentInput(
        request_id="req-chat-runtime-fail",
        session_id="sess-chat-runtime-fail",
        user_message="你还记得我们之前说的那个展览吗",
        person_id="person-001",
        render_target_lang="zh",
        bond_summary={"stage": "挚友", "shared_memory_count": 2},
        llm_runtime=None,
    )
    events = _run(_collect_events(chat_agent_sse_generator(agent_input)))
    commit = next(item for item in events if '"phase": "COMMIT"' in item)
    commit_payload = json.loads(commit.removeprefix("data: ").strip())
    assert "LLM_UNAVAILABLE" in commit
    assert captured[-1]["request_id"] == "req-chat-runtime-fail"
    assert captured[-1]["user_message"] == "你还记得我们之前说的那个展览吗"
    assert captured[-1]["model_id"] == "llm-unavailable-fallback"
    assert str(commit_payload["narrative"]) == "等我一下，我刚才这句没接稳。"
    assert "脑子有点转不动" not in str(commit_payload["narrative"])
    assert str(captured[-1]["narrative"]) == str(commit_payload["narrative"])
    assert dict(captured[-1]["motive_state"] or {})["dominant_motive"] == "continuity"
    assert dict(captured[-1]["response_policy"] or {})["allow_followup"] is False

    unavailable_input = ChatAgentInput(
        request_id="req-chat-runtime-down",
        session_id="sess-chat-runtime-down",
        user_message="你还记得我们之前说的那个展览吗",
        person_id="person-001",
        render_target_lang="zh",
        bond_summary={"stage": "挚友", "shared_memory_count": 2},
        llm_runtime={"available": False, "provider_name": "mock", "model_id": "mock", "timeout_ms": 1000},
    )
    events = _run(_collect_events(chat_agent_sse_generator(unavailable_input)))
    commit = next(item for item in events if '"phase": "COMMIT"' in item)
    commit_payload = json.loads(commit.removeprefix("data: ").strip())
    assert "LLM_UNAVAILABLE" in commit
    assert captured[-1]["request_id"] == "req-chat-runtime-down"
    assert captured[-1]["model_id"] == "mock"
    assert str(commit_payload["narrative"]) == "等我一下，我刚才这句没接稳。"
    assert str(captured[-1]["narrative"]) == str(commit_payload["narrative"])


def test_chat_agent_llm_exception_quality_fallback_uses_runtime_payload(monkeypatch) -> None:
    def _fake_build_top_agent_prompt(**_kwargs):  # noqa: ANN001
        return (
            "PROMPT_TEXT",
            "SYSTEM_PROMPT",
            {"temperature": 0.2},
            {
                "offline_state": {"anticipation_topics": ["那场展览"]},
                "motive_state": {"dominant_motive": "continuity", "continuity_drive": 0.88},
                "bond_narrative": {"shared_memories": ["那场展览和我们之前熬夜聊出来的计划"]},
                "response_policy": {"allow_followup": False, "max_sentences": 2},
            },
        )

    def _identity_response_intelligence(**kwargs):  # noqa: ANN001
        return kwargs.get("narrative") or "", {"reflection": {"enabled": False}}

    async def _raising_chunks(**_kwargs):  # noqa: ANN001
        raise RuntimeError("llm unavailable")
        yield  # pragma: no cover

    monkeypatch.setenv("HOST_STREAM_REFLECTION_BUFFER_ENABLED", "false")
    monkeypatch.setenv("HOST_STREAM_TTS_ENABLED", "false")
    monkeypatch.setattr("host_brain.app.top_agent_prompt.build_top_agent_prompt", _fake_build_top_agent_prompt)
    monkeypatch.setattr("host_brain.integrations.llm_helpers.astream_llm_chunks", _raising_chunks)
    monkeypatch.setattr("host_brain.app.intelligent_runtime.apply_response_intelligence", _identity_response_intelligence)

    agent_input = ChatAgentInput(
        request_id="req-chat-fallback-memory",
        session_id="session-001",
        user_message="你还记得我们之前说的那个展览吗",
        person_id="default",
        render_target_lang="zh",
        bond_summary={"stage": "挚友", "shared_memory_count": 2},
        llm_runtime={
            "available": True,
            "provider_name": "mock",
            "model_id": "mock-model",
            "timeout_ms": 1000,
            "llm_api_key": "",
            "base_url_override": "",
            "agent_gen_params": {"temperature": 0.2},
        },
    )

    events = _run(_collect_events(chat_agent_sse_generator(agent_input)))
    commit = [event for event in events if '"phase": "COMMIT"' in event][-1]
    assert '"narrative": "等我一下，我刚才这句没接稳。"' in commit
    assert "脑子有点转不动" not in commit
    assert "想先从哪一段继续" not in commit


def test_chat_agent_commit_enforces_response_policy_max_sentences_from_runtime_payload(monkeypatch) -> None:
    monkeypatch.setenv("HOST_STREAM_REFLECTION_BUFFER_ENABLED", "false")
    monkeypatch.setenv("HOST_STREAM_TTS_ENABLED", "false")
    monkeypatch.setattr(
        "host_brain.app.top_agent_prompt.build_top_agent_prompt",
        lambda **_kwargs: ("prompt", "system", {}, {"response_policy": {"max_sentences": 1}}),
    )

    async def _chunks(**_kwargs):  # noqa: ANN001
        yield "第一句。第二句。"

    def _identity_response_intelligence(**kwargs):  # noqa: ANN001
        return kwargs["narrative"], {"reflection": {"enabled": False}}

    monkeypatch.setattr("host_brain.integrations.llm_helpers.astream_llm_chunks", _chunks)
    monkeypatch.setattr("host_brain.app.intelligent_runtime.apply_response_intelligence", _identity_response_intelligence)

    agent_input = ChatAgentInput(
        request_id="req-chat-policy-sentences",
        session_id="sess-chat-policy-sentences",
        user_message="继续说",
        person_id="person-001",
        render_target_lang="zh",
        llm_runtime={"available": True, "provider_name": "mock", "model_id": "mock", "timeout_ms": 1000},
    )

    events = _run(_collect_events(chat_agent_sse_generator(agent_input)))
    joined = "\n".join(events)
    commit = next(item for item in events if '"phase": "COMMIT"' in item)
    assert '"narrative": "第一句。"' in commit
    assert "第二句" not in joined


def test_chat_agent_post_process_receives_prompt_runtime_trace_payload(monkeypatch) -> None:
    monkeypatch.setenv("HOST_STREAM_REFLECTION_BUFFER_ENABLED", "false")
    monkeypatch.setenv("HOST_STREAM_TTS_ENABLED", "false")
    monkeypatch.setattr(
        "host_brain.app.top_agent_prompt.build_top_agent_prompt",
        lambda **_kwargs: (
            "prompt",
            "system prompt",
            {},
            {
                "motive_state": {
                    "dominant_motive": "continuity",
                    "continuity_drive": 0.91,
                },
                "response_policy": {
                    "allow_followup": False,
                    "allow_proactive_after_reply": False,
                    "max_sentences": 1,
                },
            },
        ),
    )

    async def _chunks(**_kwargs):  # noqa: ANN001
        yield "ok. keep going."

    def _identity_response_intelligence(**kwargs):  # noqa: ANN001
        return kwargs["narrative"], {"reflection": {"enabled": False}}

    captured: dict[str, object] = {}

    def _fake_async_post_process(**kwargs):  # noqa: ANN001
        captured.update(kwargs)

        async def _done() -> None:
            return None

        return _done()

    monkeypatch.setattr("host_brain.integrations.llm_helpers.astream_llm_chunks", _chunks)
    monkeypatch.setattr("host_brain.app.intelligent_runtime.apply_response_intelligence", _identity_response_intelligence)
    monkeypatch.setattr("host_brain.app.top_agent._async_post_process", _fake_async_post_process)

    agent_input = ChatAgentInput(
        request_id="req-chat-trace-runtime-payload",
        session_id="sess-chat-trace-runtime-payload",
        user_message="continue",
        person_id="person-001",
        render_target_lang="zh",
        llm_runtime={"available": True, "provider_name": "mock", "model_id": "mock", "timeout_ms": 1000},
    )

    events = _run(_collect_events(chat_agent_sse_generator(agent_input)))
    assert any('"phase": "COMMIT"' in item for item in events)
    assert dict(captured.get("motive_state") or {})["dominant_motive"] == "continuity"
    assert dict(captured.get("motive_state") or {})["continuity_drive"] == 0.91
    assert dict(captured.get("response_policy") or {})["allow_followup"] is False
    assert dict(captured.get("response_policy") or {})["allow_proactive_after_reply"] is False
    assert dict(captured.get("response_policy") or {})["max_sentences"] == 1


def test_chat_agent_commit_enforces_response_policy_max_chars_from_agent_input(monkeypatch) -> None:
    monkeypatch.setenv("HOST_STREAM_REFLECTION_BUFFER_ENABLED", "false")
    monkeypatch.setenv("HOST_STREAM_TTS_ENABLED", "false")
    monkeypatch.setattr(
        "host_brain.app.top_agent_prompt.build_top_agent_prompt",
        lambda **_kwargs: ("prompt", "system", {}),
    )

    async def _chunks(**_kwargs):  # noqa: ANN001
        yield "你好呀。后面不该出现"

    def _identity_response_intelligence(**kwargs):  # noqa: ANN001
        return kwargs["narrative"], {"reflection": {"enabled": False}}

    monkeypatch.setattr("host_brain.integrations.llm_helpers.astream_llm_chunks", _chunks)
    monkeypatch.setattr("host_brain.app.intelligent_runtime.apply_response_intelligence", _identity_response_intelligence)

    agent_input = ChatAgentInput(
        request_id="req-chat-policy-chars",
        session_id="sess-chat-policy-chars",
        user_message="说一句",
        person_id="person-001",
        render_target_lang="zh",
        response_policy={"max_chars": 4},
        llm_runtime={"available": True, "provider_name": "mock", "model_id": "mock", "timeout_ms": 1000},
    )

    events = _run(_collect_events(chat_agent_sse_generator(agent_input)))
    joined = "\n".join(events)
    commit = next(item for item in events if '"phase": "COMMIT"' in item)
    assert '"narrative": "你好呀。"' in commit
    assert "后面不该出现" not in joined

def test_chat_agent_commit_enforces_response_policy_followup_guard(monkeypatch) -> None:
    monkeypatch.setenv("HOST_STREAM_REFLECTION_BUFFER_ENABLED", "false")
    monkeypatch.setenv("HOST_STREAM_TTS_ENABLED", "false")
    monkeypatch.setattr(
        "host_brain.app.top_agent_prompt.build_top_agent_prompt",
        lambda **_kwargs: ("prompt", "system", {}, {"response_policy": {"allow_followup": False}}),
    )

    async def _chunks(**_kwargs):  # noqa: ANN001
        yield "先这样吧。你呢？"

    def _identity_response_intelligence(**kwargs):  # noqa: ANN001
        return kwargs["narrative"], {"reflection": {"enabled": False}}

    monkeypatch.setattr("host_brain.integrations.llm_helpers.astream_llm_chunks", _chunks)
    monkeypatch.setattr("host_brain.app.intelligent_runtime.apply_response_intelligence", _identity_response_intelligence)

    agent_input = ChatAgentInput(
        request_id="req-chat-policy-followup",
        session_id="sess-chat-policy-followup",
        user_message="继续",
        person_id="person-001",
        render_target_lang="zh",
        llm_runtime={"available": True, "provider_name": "mock", "model_id": "mock", "timeout_ms": 1000},
    )

    events = _run(_collect_events(chat_agent_sse_generator(agent_input)))
    joined = "\n".join(events)
    commit = next(item for item in events if '"phase": "COMMIT"' in item)
    assert '"narrative": "先这样吧。"' in commit
    assert "你呢" not in joined


def test_chat_agent_commit_enforces_response_policy_confession_guard(monkeypatch) -> None:
    monkeypatch.setenv("HOST_STREAM_REFLECTION_BUFFER_ENABLED", "false")
    monkeypatch.setenv("HOST_STREAM_TTS_ENABLED", "false")
    monkeypatch.setattr(
        "host_brain.app.top_agent_prompt.build_top_agent_prompt",
        lambda **_kwargs: (
            "prompt",
            "system",
            {},
            {"response_policy": {"confession_guard": True, "intimacy_ceiling": 0.15}},
        ),
    )

    async def _chunks(**_kwargs):  # noqa: ANN001
        yield "我爱你。我好想你。"

    def _identity_response_intelligence(**kwargs):  # noqa: ANN001
        return kwargs["narrative"], {"reflection": {"enabled": False}}

    monkeypatch.setattr("host_brain.integrations.llm_helpers.astream_llm_chunks", _chunks)
    monkeypatch.setattr("host_brain.app.intelligent_runtime.apply_response_intelligence", _identity_response_intelligence)

    agent_input = ChatAgentInput(
        request_id="req-chat-policy-confession",
        session_id="sess-chat-policy-confession",
        user_message="继续",
        person_id="person-001",
        render_target_lang="zh",
        llm_runtime={"available": True, "provider_name": "mock", "model_id": "mock", "timeout_ms": 1000},
    )

    events = _run(_collect_events(chat_agent_sse_generator(agent_input)))
    joined = "\n".join(events)
    commit = next(item for item in events if '"phase": "COMMIT"' in item)
    assert "我爱你" not in joined
    assert "好想你" not in joined
    assert "很在意你" in commit or "惦记你" in commit


def test_chat_agent_commit_keeps_affection_confirmation_question_on_topic(monkeypatch) -> None:
    monkeypatch.setenv("HOST_STREAM_REFLECTION_BUFFER_ENABLED", "false")
    monkeypatch.setenv("HOST_STREAM_TTS_ENABLED", "false")
    monkeypatch.setattr(
        "host_brain.app.top_agent_prompt.build_top_agent_prompt",
        lambda **_kwargs: (
            "prompt",
            "system",
            {},
            {"response_policy": {"confession_guard": True, "intimacy_ceiling": 0.15}},
        ),
    )

    async def _chunks(**_kwargs):  # noqa: ANN001
        yield "我喜欢你。"

    def _identity_response_intelligence(**kwargs):  # noqa: ANN001
        return kwargs["narrative"], {"reflection": {"enabled": False}}

    monkeypatch.setattr("host_brain.integrations.llm_helpers.astream_llm_chunks", _chunks)
    monkeypatch.setattr("host_brain.app.intelligent_runtime.apply_response_intelligence", _identity_response_intelligence)

    agent_input = ChatAgentInput(
        request_id="req-chat-policy-affection-question",
        session_id="sess-chat-policy-affection-question",
        user_message="你喜欢我吗",
        person_id="person-001",
        render_target_lang="zh",
        llm_runtime={"available": True, "provider_name": "mock", "model_id": "mock", "timeout_ms": 1000},
    )

    events = _run(_collect_events(chat_agent_sse_generator(agent_input)))
    joined = "\n".join(events)
    commit = next(item for item in events if '"phase": "COMMIT"' in item)
    assert "我在。你慢慢说" not in joined
    assert "在意你" in commit or "无动于衷" in commit or "心动" in commit


def test_chat_agent_commit_enforces_response_policy_topic_shift_guard(monkeypatch) -> None:
    monkeypatch.setenv("HOST_STREAM_REFLECTION_BUFFER_ENABLED", "false")
    monkeypatch.setenv("HOST_STREAM_TTS_ENABLED", "false")
    monkeypatch.setattr(
        "host_brain.app.top_agent_prompt.build_top_agent_prompt",
        lambda **_kwargs: ("prompt", "system", {}, {"response_policy": {"allow_topic_shift": False}}),
    )

    async def _chunks(**_kwargs):  # noqa: ANN001
        yield "先把这件事说完。对了，今天天气也不错。"

    def _identity_response_intelligence(**kwargs):  # noqa: ANN001
        return kwargs["narrative"], {"reflection": {"enabled": False}}

    monkeypatch.setattr("host_brain.integrations.llm_helpers.astream_llm_chunks", _chunks)
    monkeypatch.setattr("host_brain.app.intelligent_runtime.apply_response_intelligence", _identity_response_intelligence)

    agent_input = ChatAgentInput(
        request_id="req-chat-policy-topic-shift",
        session_id="sess-chat-policy-topic-shift",
        user_message="继续",
        person_id="person-001",
        render_target_lang="zh",
        llm_runtime={"available": True, "provider_name": "mock", "model_id": "mock", "timeout_ms": 1000},
    )

    events = _run(_collect_events(chat_agent_sse_generator(agent_input)))
    joined = "\n".join(events)
    commit = next(item for item in events if '"phase": "COMMIT"' in item)
    assert '"narrative": "先把这件事说完。"' in commit
    assert "今天天气也不错" not in joined


def test_chat_agent_commit_enforces_response_policy_withdrawal_guard(monkeypatch) -> None:
    monkeypatch.setenv("HOST_STREAM_REFLECTION_BUFFER_ENABLED", "false")
    monkeypatch.setenv("HOST_STREAM_TTS_ENABLED", "false")
    monkeypatch.setattr(
        "host_brain.app.top_agent_prompt.build_top_agent_prompt",
        lambda **_kwargs: ("prompt", "system", {}, {"response_policy": {"withdrawal_guard": True}}),
    )

    async def _chunks(**_kwargs):  # noqa: ANN001
        yield "我会一直都在！！抱抱你~"

    def _identity_response_intelligence(**kwargs):  # noqa: ANN001
        return kwargs["narrative"], {"reflection": {"enabled": False}}

    monkeypatch.setattr("host_brain.integrations.llm_helpers.astream_llm_chunks", _chunks)
    monkeypatch.setattr("host_brain.app.intelligent_runtime.apply_response_intelligence", _identity_response_intelligence)

    agent_input = ChatAgentInput(
        request_id="req-chat-policy-withdrawn",
        session_id="sess-chat-policy-withdrawn",
        user_message="继续",
        person_id="person-001",
        render_target_lang="zh",
        llm_runtime={"available": True, "provider_name": "mock", "model_id": "mock", "timeout_ms": 1000},
    )

    events = _run(_collect_events(chat_agent_sse_generator(agent_input)))
    joined = "\n".join(events)
    commit = next(item for item in events if '"phase": "COMMIT"' in item)
    assert "抱抱你" not in joined
    assert "！！" not in joined
    assert "我在" in commit or "缓一缓" in commit

def test_chat_agent_commit_enforces_response_policy_intimacy_ceiling_lexical_guard(monkeypatch) -> None:
    monkeypatch.setenv("HOST_STREAM_REFLECTION_BUFFER_ENABLED", "false")
    monkeypatch.setenv("HOST_STREAM_TTS_ENABLED", "false")
    monkeypatch.setattr(
        "host_brain.app.top_agent_prompt.build_top_agent_prompt",
        lambda **_kwargs: ("prompt", "system", {}, {"response_policy": {"intimacy_ceiling": 0.2}}),
    )

    async def _chunks(**_kwargs):  # noqa: ANN001
        yield "亲爱的，抱抱你。好喜欢你。"

    def _identity_response_intelligence(**kwargs):  # noqa: ANN001
        return kwargs["narrative"], {"reflection": {"enabled": False}}

    monkeypatch.setattr("host_brain.integrations.llm_helpers.astream_llm_chunks", _chunks)
    monkeypatch.setattr("host_brain.app.intelligent_runtime.apply_response_intelligence", _identity_response_intelligence)

    agent_input = ChatAgentInput(
        request_id="req-chat-policy-intimacy-lexical",
        session_id="sess-chat-policy-intimacy-lexical",
        user_message="继续",
        person_id="person-001",
        render_target_lang="zh",
        llm_runtime={"available": True, "provider_name": "mock", "model_id": "mock", "timeout_ms": 1000},
    )

    events = _run(_collect_events(chat_agent_sse_generator(agent_input)))
    joined = "\n".join(events)
    commit = next(item for item in events if '"phase": "COMMIT"' in item)
    assert "亲爱的" not in joined
    assert "抱抱你" not in joined
    assert "好喜欢你" not in joined
    assert "缓一缓" in commit or "在意你" in commit


def test_chat_agent_commit_enforces_response_policy_humor_ceiling_lexical_guard(monkeypatch) -> None:
    monkeypatch.setenv("HOST_STREAM_REFLECTION_BUFFER_ENABLED", "false")
    monkeypatch.setenv("HOST_STREAM_TTS_ENABLED", "false")
    monkeypatch.setattr(
        "host_brain.app.top_agent_prompt.build_top_agent_prompt",
        lambda **_kwargs: ("prompt", "system", {}, {"response_policy": {"humor_ceiling": 0.1}}),
    )

    async def _chunks(**_kwargs):  # noqa: ANN001
        yield "哈哈，那就先这样吧～"

    def _identity_response_intelligence(**kwargs):  # noqa: ANN001
        return kwargs["narrative"], {"reflection": {"enabled": False}}

    monkeypatch.setattr("host_brain.integrations.llm_helpers.astream_llm_chunks", _chunks)
    monkeypatch.setattr("host_brain.app.intelligent_runtime.apply_response_intelligence", _identity_response_intelligence)

    agent_input = ChatAgentInput(
        request_id="req-chat-policy-humor-lexical",
        session_id="sess-chat-policy-humor-lexical",
        user_message="继续",
        person_id="person-001",
        render_target_lang="zh",
        llm_runtime={"available": True, "provider_name": "mock", "model_id": "mock", "timeout_ms": 1000},
    )

    events = _run(_collect_events(chat_agent_sse_generator(agent_input)))
    joined = "\n".join(events)
    commit = next(item for item in events if '"phase": "COMMIT"' in item)
    assert "哈哈" not in joined
    assert "～" not in joined
    assert '"narrative": "那就先这样吧。"' in commit

