from __future__ import annotations

import asyncio

from types import SimpleNamespace

from host_brain.app.dispatch_agent import (
    _build_failure_recovery_plan,
    _execute_multi_step_plan,
    _maybe_build_multi_step_plan,
    _retry_after_repair,
    _build_skill_args,
    _execute_skill,
    _extract_memory_store_content,
    _hard_rule_route,
)


def _run(coro):
    return asyncio.run(coro)


def test_hard_rule_route_maps_memory_trigger_to_memory_store() -> None:
    route, target_id, reason = _hard_rule_route("蓝蓝，记住：我最喜欢的水果是芒果。")

    assert route == "skill"
    assert target_id == "memory_store"
    assert reason == "keyword:记住"


def test_build_skill_args_extracts_memory_content() -> None:
    content = _extract_memory_store_content("蓝蓝，记住：我最喜欢的水果是芒果。")
    args = _build_skill_args("memory_store", "蓝蓝，记住：我最喜欢的水果是芒果。")

    assert content == "我最喜欢的水果是芒果"
    assert args["content"] == "我最喜欢的水果是芒果"
    assert args["memory_type"] == "episodic"


def test_build_skill_args_keeps_search_query() -> None:
    args = _build_skill_args("web_search", "帮我搜索一下 OpenAI 官网")

    assert args["query"] == "帮我搜索一下 OpenAI 官网"


class _FakeEpisodicStore:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def write_memory(self, **kwargs):
        self.calls.append(dict(kwargs))
        return SimpleNamespace(success=True, reason_code=None, detail='ok', memory_id='mem-001')


def test_make_skill_episodic_writer_writes_with_evidence_refs() -> None:
    from host_brain.app.dispatch_agent import _make_skill_episodic_writer

    store = _FakeEpisodicStore()
    writer = _make_skill_episodic_writer(
        episodic_store=store,
        request_id='req-001',
        intent_id='intent-001',
        skill_id='memory_store',
    )

    memory_id = writer(
        content='我最喜欢的水果是芒果',
        tags=['preference'],
        importance=0.8,
        source='chat_api',
        emotion_tag='neutral',
        session_id='session-001',
    )

    assert memory_id == 'mem-001'
    assert store.calls[0]['text'] == '我最喜欢的水果是芒果'
    assert store.calls[0]['request_id'] == 'req-001'
    assert store.calls[0]['intent_id'] == 'intent-001'
    assert store.calls[0]['evidence_refs'] == ['skill:memory_store', 'request:req-001']
    assert store.calls[0]['emotion_tag'] == 'NEUTRAL'



def test_build_skill_args_extracts_file_read_path_and_lines() -> None:
    args = _build_skill_args("file_read", "蓝蓝，打开文件 D:/Natsume_Ai/AGENTS.md 的前20行")

    assert args["path"] == "D:/Natsume_Ai/AGENTS.md"
    assert args["offset_line"] == 1
    assert args["limit_lines"] == 20



def test_build_skill_args_extracts_browser_url() -> None:
    args = _build_skill_args("browser_navigate", "蓝蓝，打开网页 https://openai.com")

    assert args["url"] == "https://openai.com"
    assert args["timeout_ms"] == 30000



def test_build_skill_args_extracts_file_write_content() -> None:
    args = _build_skill_args("file_write", "蓝蓝，写文件 D:/Natsume_Ai/runtime/skill_test.txt 内容：你好，世界")

    assert args["path"] == "D:/Natsume_Ai/runtime/skill_test.txt"
    assert args["content"] == "你好，世界"



def test_build_skill_args_defaults_browser_screenshot_full_page() -> None:
    args = _build_skill_args("browser_screenshot", "蓝蓝，帮我截图")

    assert args["save_path"] is None
    assert args["full_page"] is True



def test_hard_rule_route_maps_system_info_keyword() -> None:
    route, target_id, reason = _hard_rule_route("蓝蓝，给我看看系统信息")

    assert route == "skill"
    assert target_id == "system_info"
    assert reason == "keyword:系统信息"



def test_build_skill_args_for_system_info_and_process_list() -> None:
    system_args = _build_skill_args("system_info", "蓝蓝，给我看看系统信息")
    process_args = _build_skill_args("process_list", "蓝蓝，给我看看进程列表")

    assert system_args["include_cpu"] is True
    assert system_args["include_disk"] is True
    assert process_args["limit"] == 20
    assert process_args["sort_by"] == "memory"



def test_build_skill_args_for_file_list_and_network_status() -> None:
    file_list_args = _build_skill_args("file_list", "蓝蓝，列出目录 D:/Natsume_Ai/runtime 的所有文件")
    network_args = _build_skill_args("network_status", "蓝蓝，看看网络状态")

    assert file_list_args["path"] == "D:/Natsume_Ai/runtime"
    assert file_list_args["recursive"] is True
    assert network_args["host"] == "8.8.8.8"



def test_hard_rule_route_maps_task_progress_query() -> None:
    route, target_id, reason = _hard_rule_route("蓝蓝，上个任务进度怎么样了？")

    assert route == "task_progress"
    assert target_id == ""
    assert reason == "task_progress_query"


def test_build_failure_recovery_plan_for_browser_unavailable() -> None:
    plan = _build_failure_recovery_plan(
        target_id="browser_navigate",
        user_message="蓝蓝，打开网页 https://example.com",
        error_text="BROWSER_UNAVAILABLE: FAILED",
    )

    assert plan is not None
    assert plan["agent_id"] == "repair_agent"
    assert plan["reason_code"] == "BROWSER_UNAVAILABLE"
    assert "安装" in plan["task"] or "补齐" in plan["task"] or "修复" in plan["task"]


def test_build_skill_args_generic_fallback_contains_common_fields() -> None:
    args = _build_skill_args("example-echo", "蓝蓝，帮我处理 https://example.com 和 D:/Natsume_Ai/README.md")

    assert args["query"].startswith("蓝蓝")
    assert args["url"] == "https://example.com"
    assert args["path"] == "D:/Natsume_Ai/README.md"
    assert args["content"].startswith("蓝蓝")



def test_maybe_build_multi_step_plan_resolves_two_steps() -> None:
    plan = _run(
        _maybe_build_multi_step_plan(
            user_message="蓝蓝，先帮我搜索一下 OpenAI 官网，然后记住我最喜欢的颜色是蓝色。",
            request_id="req-plan-001",
            llm_runtime=None,
        )
    )

    assert plan is not None
    assert len(plan["steps"]) >= 2
    assert plan["steps"][0]["target_id"] == "web_search"
    assert any(step["target_id"] == "memory_store" for step in plan["steps"])
    assert plan["parallel_enabled"] is False


def test_execute_multi_step_plan_aggregates_step_results(monkeypatch) -> None:
    from host_brain.app.runtime_task_registry import RuntimeTaskRegistry
    from host_brain.app.chat_agent import SubAgentContext

    registry = RuntimeTaskRegistry()
    monkeypatch.setattr("host_brain.app.dependencies.get_runtime_task_registry", lambda: registry)

    async def _fake_execute_skill(*, skill_id: str, user_message: str, request_id: str, session_id: str):  # noqa: ANN001
        return SubAgentContext(agent_id=f"skill:{skill_id}", task=user_message, result_text=f"done:{skill_id}", success=True, latency_ms=1)

    monkeypatch.setattr("host_brain.app.dispatch_agent._execute_skill", _fake_execute_skill)

    plan = {
        "plan_id": "plan::req-plan-002",
        "parallel_enabled": False,
        "steps": [
            {"step_index": 1, "description": "搜索官网", "route": "skill", "target_id": "web_search", "task_description": "搜索 OpenAI 官网", "route_reason": "keyword", "allow_parallel": True},
            {"step_index": 2, "description": "记住颜色", "route": "skill", "target_id": "memory_store", "task_description": "记住我最喜欢的颜色是蓝色", "route_reason": "keyword", "allow_parallel": False},
        ],
    }

    result = _run(
        _execute_multi_step_plan(
            plan=plan,
            request_id="req-plan-002",
            session_id="session-plan-002",
            person_id="person-plan-002",
            user_message="先搜索，再记住",
            llm_runtime=None,
            emotion_context={},
            bond_summary={},
            render_target_lang="zh",
        )
    )

    assert result is not None
    assert result.agent_id == "plan_execution"
    assert result.success is True
    assert "第1步（web_search）已完成" in result.result_text
    assert "第2步（memory_store）已完成" in result.result_text



def test_maybe_build_multi_step_plan_marks_parallel_for_safe_steps() -> None:
    plan = _run(
        _maybe_build_multi_step_plan(
            user_message="蓝蓝，先给我看看系统信息，然后再给我看看进程列表。",
            request_id="req-plan-003",
            llm_runtime=None,
        )
    )

    assert plan is not None
    assert len(plan["steps"]) >= 2
    assert plan["parallel_enabled"] is True



def test_build_failure_recovery_plan_uses_repair_agent_for_browser_unavailable() -> None:
    plan = _build_failure_recovery_plan(
        target_id="browser_navigate",
        user_message="打开网页 https://example.com",
        error_text="BROWSER_UNAVAILABLE: FAILED",
    )

    assert plan is not None
    assert plan["agent_id"] == "repair_agent"
    assert plan["retry_original"] is True


def test_retry_after_repair_retries_skill(monkeypatch) -> None:
    from host_brain.app.chat_agent import SubAgentContext

    async def _fake_execute_skill(*, skill_id: str, user_message: str, request_id: str, session_id: str):  # noqa: ANN001
        return SubAgentContext(agent_id=f"skill:{skill_id}", task=user_message, result_text="retried-ok", success=True, latency_ms=1)

    monkeypatch.setattr("host_brain.app.dispatch_agent._execute_skill", _fake_execute_skill)

    ctx = _run(_retry_after_repair(
        route_kind="skill",
        target_id="browser_navigate",
        task_description="打开网页 https://example.com",
        user_message="打开网页 https://example.com",
        request_id="req-retry",
        session_id="session-retry",
        llm_runtime=None,
        emotion_context={},
        bond_summary={},
        render_target_lang="zh",
    ))

    assert ctx.success is True
    assert ctx.result_text == "retried-ok"



def test_hard_rule_route_maps_hub_skill_install() -> None:
    route, target_id, reason = _hard_rule_route("蓝蓝，安装技能 github:owner/repo/demo_skill")

    assert route == "skill"
    assert target_id == "hub_skill_install"
    assert reason == "keyword:安装技能"


def test_build_skill_args_extracts_hub_skill_install_source() -> None:
    args = _build_skill_args("hub_skill_install", "蓝蓝，安装技能 github:owner/repo/demo_skill")

    assert args["source"] == "github:owner/repo/demo_skill"
    assert args["activate_now"] is True


def test_build_skill_args_extracts_hub_skill_search_query() -> None:
    args = _build_skill_args("hub_skill_search", "蓝蓝，搜索技能 天气")

    assert args["query"] == "天气"



def test_execute_skill_preserves_runtime_payload_for_hub_skill(monkeypatch) -> None:
    class _FakeRegistry:
        def get(self, skill_id):  # noqa: ANN001
            return SimpleNamespace(skill_name=skill_id)

    class _FakeTokenService:
        def issue_approval_token(self, **_kwargs):  # noqa: ANN001
            return "approval-token-001"

    class _FakeLoader:
        def __init__(self, **_kwargs):  # noqa: ANN001
            pass

        def execute(self, **_kwargs):  # noqa: ANN001
            return SimpleNamespace(
                status="SUCCESS",
                result_code="SKILL_EXECUTED",
                detail={},
                runtime_payload={
                    "action": "list",
                    "detail": {
                        "total": 1,
                        "skills": [{"name": "demo_skill"}],
                    },
                },
            )

    monkeypatch.setattr("host_brain.app.dependencies.get_skill_registry", lambda: _FakeRegistry())
    monkeypatch.setattr("host_brain.app.dependencies.get_ledger", lambda: object())
    monkeypatch.setattr("host_brain.skills.loader.SkillLoader", _FakeLoader)
    monkeypatch.setattr("host_brain.governor.tokens.TokenService", _FakeTokenService)
    monkeypatch.setattr("host_brain.core.ids.new_event_id", lambda: "intent-001")

    ctx = _run(
        _execute_skill(
            skill_id="hub_skill_list",
            user_message="列出技能",
            request_id="req-hub-payload-001",
            session_id="session-hub-payload-001",
        )
    )

    assert ctx.success is True
    assert ctx.agent_id == "skill:hub_skill_list"
    assert ctx.payload["detail"]["total"] == 1
    assert ctx.payload["detail"]["skills"][0]["name"] == "demo_skill"



def test_hard_rule_route_prefers_hub_skill_search_over_web_search() -> None:
    route, target_id, reason = _hard_rule_route("蓝蓝，搜索技能 天气")

    assert route == "skill"
    assert target_id == "hub_skill_search"
    assert reason == "keyword:搜索技能"



def test_build_skill_args_extracts_hub_skill_install_url_source() -> None:
    args = _build_skill_args("hub_skill_install", "蓝蓝，安装技能 https://github.com/anthropics/skills/tree/main/frontend-design")

    assert args["source"] == "https://github.com/anthropics/skills/tree/main/frontend-design"
    assert args["activate_now"] is True



def test_build_skill_args_extracts_hub_skill_install_github_source_with_force() -> None:
    args = _build_skill_args("hub_skill_install", "蓝蓝，安装技能 skillshub:anthropics/skills/frontend-design 强制覆盖")

    assert args["source"] == "skillshub:anthropics/skills/frontend-design"
    assert args["force"] is True



def test_build_skill_args_extracts_clawhub_source() -> None:
    args = _build_skill_args("hub_skill_install", "蓝蓝，安装技能 clawhub:sonoscli")

    assert args["source"] == "clawhub:sonoscli"
