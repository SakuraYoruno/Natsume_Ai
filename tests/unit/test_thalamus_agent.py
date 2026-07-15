from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from host_brain.composition import process_services as dependencies
from host_brain.model_runtime import (
    CallableProviderAdapter,
    InMemoryAttemptAuditRepository,
    ProviderTarget,
    RouteDecision,
    StaticRoutePolicy,
    build_model_runtime_composition,
    build_production_prompt_registry,
)
from host_brain.tribe.application_ports import TribeApplicationPorts
from host_brain.tribe.thalamus_agent import (
    ThalamusAgent,
    _resolve_intent_classification_prompt_templates,
    classify_task_intent,
    classify_task_intent_by_llm,
    classify_task_intent_fastpath,
    classify_task_intent_by_rules,
)


def _run(coro):
    return asyncio.run(coro)


def test_compile_intent_draft_preserves_extension_fields_and_canonical_targets() -> None:
    agent = ThalamusAgent()

    compiled = agent.compile_intent_draft(
        request_id="req-thalamus-skill",
        session_id="session-thalamus-skill",
        user_message="remember that Friday evening is usually free",
        person_id="person-001",
        raw_route_decision={
            "route": "skill",
            "target_id": "memory_store",
            "task": "remember this preference",
            "reason_code": "RULE_FORCE_MEMORY_STORE",
            "confidence": 0.95,
            "route_source": "rule",
            "args": {
                "query": "Friday evening is usually free",
                "text": "remember that Friday evening is usually free",
            },
            "planned_steps": [{"target_id": "memory_store", "route": "skill"}],
        },
        source="input_routes",
        input_source="GATEWAY",
        ts_ms=1776349000000,
    )

    assert compiled["schema_version"] == "Natsume.intent.v1"
    assert compiled["route"] == "skill"
    assert compiled["target_id"] == "memory_store"
    assert compiled["action"] == "memory_store"
    assert "skill_id" not in compiled
    assert "skill_name" not in compiled
    assert compiled["route_reason"] == "RULE_FORCE_MEMORY_STORE"
    assert compiled["route_confidence"] == 0.95
    assert compiled["args"]["query"] == "Friday evening is usually free"
    assert compiled["args"]["content"] == "Friday evening is usually free"
    assert compiled["planned_steps"] == [{"target_id": "memory_store", "route": "skill"}]
    assert "planned_steps" in compiled["_thalamus_meta"]["extension_keys"]


def test_compile_intent_draft_fills_missing_required_fields_without_dropping_existing_args() -> None:
    agent = ThalamusAgent()

    compiled = agent.compile_intent_draft(
        request_id="req-thalamus-plan",
        session_id="session-thalamus-plan",
        user_message="first summarize then read emotion snapshot",
        existing_intent_draft={
            "route": "plan",
            "target_id": "skill_dag",
            "task": "first summarize then read emotion snapshot",
            "args": {
                "text": "first summarize then read emotion snapshot",
                "query": "first summarize then read emotion snapshot",
            },
            "planned_steps": [
                {"route": "skill", "target_id": "session_summary"},
                {"route": "skill", "target_id": "emotion_query"},
            ],
        },
        source="chat_handler",
        ts_ms=1776349001000,
    )

    assert compiled["id"] == "intent::req-thalamus-plan"
    assert compiled["session_id"] == "session-thalamus-plan"
    assert compiled["nonce"] == "nonce::req-thalamus-plan"
    assert compiled["action"] == "exec_intent"
    assert compiled["args"]["text"] == "first summarize then read emotion snapshot"
    assert "query" not in compiled["args"]
    assert len(compiled["planned_steps"]) == 2
    assert compiled["planned_steps"][0]["target_id"] == "session_summary"
    assert compiled["planned_steps"][1]["target_id"] == "emotion_query"


def test_compile_intent_draft_infers_skill_and_plan_routes_from_explicit_extensions() -> None:
    agent = ThalamusAgent()

    skill_compiled = agent.compile_intent_draft(
        request_id="req-thalamus-infer-skill",
        session_id="session-thalamus-infer-skill",
        user_message="please summarize the current session",
        existing_intent_draft={
            "action": "session_summary",
            "args": {"user_message": "please summarize the current session"},
        },
        source="chat_handler",
        ts_ms=1776349001500,
    )
    plan_compiled = agent.compile_intent_draft(
        request_id="req-thalamus-infer-plan",
        session_id="session-thalamus-infer-plan",
        user_message="first summarize then read emotion snapshot",
        existing_intent_draft={
            "action": "exec_intent",
            "planned_steps": [
                {"route": "skill", "target_id": "session_summary"},
                {"route": "skill", "target_id": "emotion_query"},
            ],
        },
        source="chat_handler",
        ts_ms=1776349001600,
    )

    assert skill_compiled["route"] == "skill"
    assert skill_compiled["target_id"] == "session_summary"
    assert plan_compiled["route"] == "plan"
    assert len(plan_compiled["planned_steps"]) == 2


def test_compile_intent_draft_maps_progress_like_routes_to_exec_intent_for_governor_compat() -> None:
    agent = ThalamusAgent()

    compiled = agent.compile_intent_draft(
        request_id="req-thalamus-progress",
        session_id="session-thalamus-progress",
        user_message="what is the current progress",
        raw_route_decision={
            "route": "task_progress",
            "target_id": "",
            "task": "check current progress",
            "reason_code": "RULE_TASK_PROGRESS",
            "confidence": 0.98,
            "route_source": "rule",
        },
        source="chat_handler",
        ts_ms=1776349002000,
    )

    assert compiled["route"] == "task_progress"
    assert compiled["action"] == "exec_intent"
    assert compiled["args"]["text"] == "check current progress"
    assert "user_message" not in compiled["args"]


def test_classify_and_compile_intent_draft_routes_proactive_reply_continuation() -> None:
    agent = ThalamusAgent()

    compiled = _run(
        agent.classify_and_compile_intent_draft(
            request_id="req-thalamus-proactive-reply",
            session_id="session-thalamus-proactive-reply",
            user_message="在啊，怎么啦？",
            source="proactive_interaction_dry_run",
            input_source="PROACTIVE_DRY_RUN",
            tribe_context={
                "proactive_interaction": {
                    "in_reply_to_proactive": True,
                    "outbound_message_id": "evt_proactive_1",
                    "delivery_receipt_id": "receipt_1",
                    "delivery_status": "DELIVERED_DRY_RUN",
                    "channel": "safe_outbox",
                }
            },
            ts_ms=1776349003050,
        )
    )

    assert compiled["route"] == "chat"
    assert compiled["target_id"] == ""
    assert compiled["route_reason"] == "THALAMUS_RULE_PROACTIVE_REPLY_CONTINUATION"
    assert compiled["route_source"] == "thalamus.rule"
    assert compiled["input_source"] == "PROACTIVE_DRY_RUN"
    assert compiled["args"]["proactive_reply"] is True
    assert compiled["proactive_context"]["outbound_message_id"] == "evt_proactive_1"
    assert "proactive_context" in compiled["_thalamus_meta"]["extension_keys"]


def test_classify_and_compile_intent_draft_routes_open_app_from_imperative_phrase() -> None:
    agent = ThalamusAgent()

    compiled = _run(
        agent.classify_and_compile_intent_draft(
            request_id="req-thalamus-open-app-short",
            session_id="session-thalamus-open-app-short",
            user_message="打开酷狗音乐",
            source="chat_handler",
            ts_ms=1776349003105,
        )
    )

    assert compiled["route"] == "skill"
    assert compiled["target_id"] == "open_app"
    assert compiled["route_reason"] == "THALAMUS_RULE_OPEN_APP"
    assert compiled["args"]["query"] == "打开酷狗音乐"


def test_classify_and_compile_intent_draft_routes_open_app_from_polite_phrase() -> None:
    agent = ThalamusAgent()

    compiled = _run(
        agent.classify_and_compile_intent_draft(
            request_id="req-thalamus-open-app-polite",
            session_id="session-thalamus-open-app-polite",
            user_message="可以帮我打开本地的酷狗音乐吗",
            source="chat_handler",
            ts_ms=1776349003106,
        )
    )

    assert compiled["route"] == "skill"
    assert compiled["target_id"] == "open_app"
    assert compiled["route_reason"] == "THALAMUS_RULE_OPEN_APP"
    assert compiled["args"]["query"] == "可以帮我打开本地的酷狗音乐吗"


def test_thalamus_routes_conversational_opinion_about_main_chain_to_chat() -> None:
    cases = [
        "你觉得把后台任务迁到本机 Codex 以后，最需要继续观察的风险是什么？用两句说。",
        "别讲测试指标，直接说你现在对这个主链体感的判断，像正常聊天那样。",
        "如果我要继续压整条聊天链路的耗时，你会先怀疑哪一段？不要列清单。",
    ]

    for text in cases:
        rules = classify_task_intent_by_rules(text)
        fastpath = classify_task_intent_fastpath(text)
        assert rules is not None
        assert rules["route"] == "chat"
        assert rules["target_id"] == ""
        assert rules["reason_code"] == "THALAMUS_RULE_CONVERSATIONAL_OPINION_CHAT"
        assert fastpath is not None
        assert fastpath["route"] == "chat"
        assert fastpath["reason_code"] == "THALAMUS_RULE_CONVERSATIONAL_OPINION_CHAT"


def test_thalamus_keeps_actionable_workspace_requests_on_codex_skill() -> None:
    inspect_decision = classify_task_intent_by_rules("检查主链代码")
    write_decision = classify_task_intent_by_rules("修复主链路由")

    assert inspect_decision is not None
    assert inspect_decision["route"] == "skill"
    assert inspect_decision["target_id"] == "agent_inspect"
    assert write_decision is not None
    assert write_decision["route"] == "skill"
    assert write_decision["target_id"] == "agent_worker"


def test_thalamus_routes_strict_recent_assistant_query_to_recent_turn() -> None:
    decision = classify_task_intent_by_rules("你刚刚说了什么")

    assert decision is not None
    assert decision["route"] == "recent_turn"
    assert decision["target_id"] == ""
    assert decision["reason_code"] == "RULE_RECENT_TURN"


def test_thalamus_routes_plugin_build_request_to_agent_worker() -> None:
    decision = classify_task_intent_by_rules("做个统计 token 消耗的插件")

    assert decision is not None
    assert decision["route"] == "skill"
    assert decision["target_id"] == "agent_worker"
    assert decision["reason_code"] == "RULE_FORCE_agent_worker"


def test_classify_and_compile_intent_draft_routes_system_info_tool() -> None:
    agent = ThalamusAgent()

    compiled = _run(
        agent.classify_and_compile_intent_draft(
            request_id="req-thalamus-system-info",
            session_id="session-thalamus-system-info",
            user_message="查看本机系统状态",
            source="chat_handler",
            ts_ms=1776349003110,
        )
    )

    assert compiled["route"] == "skill"
    assert compiled["target_id"] == "system_info"
    assert compiled["route_reason"] == "THALAMUS_RULE_SYSTEM_INFO"
    assert compiled["args"]["include_memory"] is True
    assert compiled["route_source"] == "thalamus.rule"


def test_classify_and_compile_intent_draft_routes_system_resource_snapshot_tool() -> None:
    agent = ThalamusAgent()

    compiled = _run(
        agent.classify_and_compile_intent_draft(
            request_id="req-thalamus-resource-snapshot",
            session_id="session-thalamus-resource-snapshot",
            user_message="系统现在有点卡，查下CPU内存和显存占用",
            source="chat_handler",
            ts_ms=1776349003113,
        )
    )

    assert compiled["route"] == "skill"
    assert compiled["target_id"] == "system_resource_snapshot"
    assert compiled["route_reason"] == "THALAMUS_RULE_SYSTEM_RESOURCE_SNAPSHOT"
    assert compiled["args"]["include_gpu"] is True


def test_classify_and_compile_intent_draft_routes_resource_manager_status_tool() -> None:
    agent = ThalamusAgent()

    compiled = _run(
        agent.classify_and_compile_intent_draft(
            request_id="req-thalamus-resource-manager",
            session_id="session-thalamus-resource-manager",
            user_message="查看自动资源管家状态",
            source="chat_handler",
            ts_ms=1776349003114,
        )
    )

    assert compiled["route"] == "skill"
    assert compiled["target_id"] == "resource_manager_status"
    assert compiled["route_reason"] == "THALAMUS_RULE_RESOURCE_MANAGER_STATUS"


def test_classify_and_compile_intent_draft_routes_resource_pressure_analysis_tool() -> None:
    agent = ThalamusAgent()

    compiled = _run(
        agent.classify_and_compile_intent_draft(
            request_id="req-thalamus-resource-pressure-analysis",
            session_id="session-thalamus-resource-pressure-analysis",
            user_message="分析一下为什么有时候显存和内存会双双拉爆",
            source="chat_handler",
            ts_ms=17763490031145,
        )
    )

    assert compiled["route"] == "skill"
    assert compiled["target_id"] == "resource_pressure_analysis"
    assert compiled["route_reason"] == "THALAMUS_RULE_RESOURCE_PRESSURE_ANALYSIS"
    assert compiled["args"]["persist"] is True


def test_classify_and_compile_intent_draft_routes_process_resource_top_tool() -> None:
    agent = ThalamusAgent()

    compiled = _run(
        agent.classify_and_compile_intent_draft(
            request_id="req-thalamus-process-resource-top",
            session_id="session-thalamus-process-resource-top",
            user_message="哪些进程占显存和内存最多",
            source="chat_handler",
            ts_ms=1776349003115,
        )
    )

    assert compiled["route"] == "skill"
    assert compiled["target_id"] == "process_resource_top"
    assert compiled["route_reason"] == "THALAMUS_RULE_PROCESS_RESOURCE_TOP"


def test_classify_and_compile_intent_draft_routes_resource_release_dry_run_tool() -> None:
    agent = ThalamusAgent()

    compiled = _run(
        agent.classify_and_compile_intent_draft(
            request_id="req-thalamus-resource-release",
            session_id="session-thalamus-resource-release",
            user_message="帮我释放一下资源",
            source="chat_handler",
            ts_ms=1776349003116,
        )
    )

    assert compiled["route"] == "skill"
    assert compiled["target_id"] == "resource_release"
    assert compiled["route_reason"] == "THALAMUS_RULE_RESOURCE_RELEASE_DRY_RUN"
    assert compiled["args"]["dry_run"] is True


def test_classify_and_compile_intent_draft_routes_process_list_tool() -> None:
    agent = ThalamusAgent()

    compiled = _run(
        agent.classify_and_compile_intent_draft(
            request_id="req-thalamus-process-list",
            session_id="session-thalamus-process-list",
            user_message="列出当前进程",
            source="chat_handler",
            ts_ms=1776349003111,
        )
    )

    assert compiled["route"] == "skill"
    assert compiled["target_id"] == "process_list"
    assert compiled["route_reason"] == "THALAMUS_RULE_PROCESS_LIST"
    assert compiled["args"]["limit"] == 20


def test_classify_and_compile_intent_draft_routes_network_status_tool() -> None:
    agent = ThalamusAgent()

    compiled = _run(
        agent.classify_and_compile_intent_draft(
            request_id="req-thalamus-network-status",
            session_id="session-thalamus-network-status",
            user_message="检查网络连通性",
            source="chat_handler",
            ts_ms=1776349003112,
        )
    )

    assert compiled["route"] == "skill"
    assert compiled["target_id"] == "network_status"
    assert compiled["route_reason"] == "THALAMUS_RULE_NETWORK_STATUS"
    assert compiled["args"]["timeout_ms"] == 1500


def test_classify_and_compile_intent_draft_routes_url_navigation_to_browser_tool() -> None:
    agent = ThalamusAgent()

    compiled = _run(
        agent.classify_and_compile_intent_draft(
            request_id="req-thalamus-browser-navigate",
            session_id="session-thalamus-browser-navigate",
            user_message="打开 https://example.com 看一下",
            source="chat_handler",
            ts_ms=1776349003113,
        )
    )

    assert compiled["route"] == "skill"
    assert compiled["target_id"] == "browser_navigate"
    assert compiled["route_reason"] == "THALAMUS_RULE_BROWSER_NAVIGATE"
    assert compiled["args"]["url"] == "https://example.com"


def test_classify_and_compile_intent_draft_routes_url_scrape_to_browser_plan() -> None:
    agent = ThalamusAgent()

    compiled = _run(
        agent.classify_and_compile_intent_draft(
            request_id="req-thalamus-browser-scrape",
            session_id="session-thalamus-browser-scrape",
            user_message="抓取 https://example.com 页面文本",
            source="chat_handler",
            ts_ms=1776349003114,
        )
    )

    assert compiled["route"] == "plan"
    assert compiled["target_id"] == "skill_dag"
    assert compiled["route_reason"] == "THALAMUS_RULE_BROWSER_SCRAPE_PLAN"
    assert [step["target_id"] for step in compiled["planned_steps"]] == [
        "browser_navigate",
        "browser_scrape",
    ]
    assert compiled["planned_steps"][0]["args"]["url"] == "https://example.com"
    assert compiled["planned_steps"][1]["args"]["selector"] == "body"


def test_classify_and_compile_intent_draft_prefers_lightnovel_recommender_over_generic_scrape() -> None:
    agent = ThalamusAgent()

    compiled = _run(
        agent.classify_and_compile_intent_draft(
            request_id="req-thalamus-lightnovel-url-recommender",
            session_id="session-thalamus-lightnovel-url-recommender",
            user_message="从每日榜单或者最新资源里面抓取封面和简介给我推荐轻小说 https://www.lightnovel.fun/",
            source="chat_handler",
            ts_ms=1776349003115,
        )
    )

    assert compiled["route"] == "skill"
    assert compiled["target_id"] == "agent_lightnovel_recommender"
    assert compiled["route_reason"] == "THALAMUS_RULE_CODEX_PRODUCTION"
    assert compiled["args"]["site_adapter"] == "lightnovel.fun"


def test_classify_and_compile_intent_draft_does_not_treat_detail_url_as_eta_progress() -> None:
    agent = ThalamusAgent()

    compiled = _run(
        agent.classify_and_compile_intent_draft(
            request_id="req-thalamus-lightnovel-epub-purchase-url",
            session_id="session-thalamus-lightnovel-epub-purchase-url",
            user_message="登录我的账号并使用积分购买这本轻小说后封装成 epub，最多花 30 点 https://www.lightnovel.fun/cn/detail/1144366",
            source="chat_handler",
            ts_ms=1776349003116,
        )
    )

    assert compiled["route"] == "skill"
    assert compiled["target_id"] == "agent_epub_builder"
    assert compiled["route_reason"] == "THALAMUS_RULE_CODEX_PRODUCTION"
    assert compiled["args"]["run_mode"] == "worker"
    assert compiled["args"]["workspace_strategy"] == "temp_workspace"
    assert compiled["args"]["site_adapter"] == "lightnovel.fun"


def test_classify_and_compile_intent_draft_prefers_multimodal_chat_when_attachments_exist() -> None:
    agent = ThalamusAgent()

    compiled = _run(
        agent.classify_and_compile_intent_draft(
            request_id="req-thalamus-multimodal-file",
            session_id="session-thalamus-multimodal-file",
            user_message="帮我看看这个文件",
            source="input_routes",
            input_source="GATEWAY",
            ts_ms=1776669900001,
            attachments=[
                {
                    "attachment_id": "att_file_1",
                    "type": "file",
                    "file_name": "note.txt",
                }
            ],
            multimodal_observations={"observed_types": ["file"]},
        )
    )

    assert compiled["route"] == "chat"
    assert compiled["target_id"] == ""
    assert compiled["route_reason"] == "THALAMUS_RULE_MULTIMODAL_CHAT"
    assert compiled["route_source"] == "thalamus.rule"


def test_classify_and_compile_intent_draft_prefers_multimodal_chat_when_observations_exist() -> None:
    agent = ThalamusAgent()

    compiled = _run(
        agent.classify_and_compile_intent_draft(
            request_id="req-thalamus-multimodal-observation",
            session_id="session-thalamus-multimodal-observation",
            user_message="这是什么",
            source="chat_handler",
            input_source="WEB",
            ts_ms=1776669900002,
            multimodal_observations={
                "observed_types": ["image"],
                "summary_for_chat": "用户发来一张截图，更像是在问界面上的提示是什么意思。",
                "artifact_type": "screenshot",
            },
        )
    )

    assert compiled["route"] == "chat"
    assert compiled["target_id"] == ""
    assert compiled["route_reason"] == "THALAMUS_RULE_MULTIMODAL_CHAT"
    assert compiled["route_source"] == "thalamus.rule"


def test_classify_and_compile_intent_draft_backfills_memory_store_content() -> None:
    agent = ThalamusAgent()

    compiled = _run(
        agent.classify_and_compile_intent_draft(
            request_id="req-thalamus-memory-store",
            session_id="session-thalamus-memory-store",
            user_message="请记住：我明天上午9点开会。",
            source="chat_handler",
            ts_ms=1776349003150,
        )
    )

    assert compiled["route"] == "skill"
    assert compiled["target_id"] == "memory_store"
    assert compiled["args"]["content"] == "请记住：我明天上午9点开会。"
    assert compiled["args"]["query"] == "请记住：我明天上午9点开会。"


def test_classify_and_compile_intent_draft_routes_preference_extract_tool() -> None:
    agent = ThalamusAgent()

    compiled = _run(
        agent.classify_and_compile_intent_draft(
            request_id="req-thalamus-pref-extract",
            session_id="session-thalamus-pref-extract",
            user_message="提取这段话里的偏好：我喜欢短结论，不喜欢夸张动效。",
            source="chat_handler",
            ts_ms=1776349003155,
        )
    )

    assert compiled["route"] == "skill"
    assert compiled["target_id"] == "memory_preference_extract"
    assert compiled["route_reason"] == "THALAMUS_RULE_MEMORY_PREFERENCE_EXTRACT"
    assert compiled["args"]["max_candidates"] == 8


def test_classify_and_compile_intent_draft_routes_memory_consolidate_tool() -> None:
    agent = ThalamusAgent()

    compiled = _run(
        agent.classify_and_compile_intent_draft(
            request_id="req-thalamus-memory-consolidate",
            session_id="session-thalamus-memory-consolidate",
            user_message="记忆管家整理一下：今天确认主链工具闭环通过，下一步扩充 Host 侧技能。",
            source="chat_handler",
            ts_ms=1776349003156,
        )
    )

    assert compiled["route"] == "skill"
    assert compiled["target_id"] == "memory_consolidate"
    assert compiled["route_reason"] == "THALAMUS_RULE_MEMORY_CONSOLIDATE"
    assert compiled["args"]["include_preferences"] is True


def test_classify_and_compile_intent_draft_routes_repo_health_scan_tool() -> None:
    agent = ThalamusAgent()

    compiled = _run(
        agent.classify_and_compile_intent_draft(
            request_id="req-thalamus-repo-health",
            session_id="session-thalamus-repo-health",
            user_message="仓库医生做一次健康扫描",
            source="chat_handler",
            ts_ms=1776349003157,
        )
    )

    assert compiled["route"] == "skill"
    assert compiled["target_id"] == "repo_health_scan"
    assert compiled["route_reason"] == "THALAMUS_RULE_REPO_HEALTH_SCAN"
    assert compiled["args"]["run_tests"] is False


def test_classify_and_compile_intent_draft_routes_test_failure_triage_tool() -> None:
    agent = ThalamusAgent()

    compiled = _run(
        agent.classify_and_compile_intent_draft(
            request_id="req-thalamus-test-triage",
            session_id="session-thalamus-test-triage",
            user_message="分析这段测试失败日志：FAILED tests/unit/test_demo.py::test_contract - AssertionError",
            source="chat_handler",
            ts_ms=1776349003158,
        )
    )

    assert compiled["route"] == "skill"
    assert compiled["target_id"] == "test_failure_triage"
    assert compiled["route_reason"] == "THALAMUS_RULE_TEST_FAILURE_TRIAGE"
    assert compiled["args"]["max_failures"] == 12


def test_classify_and_compile_intent_draft_routes_frontend_route_audit_tool() -> None:
    agent = ThalamusAgent()

    compiled = _run(
        agent.classify_and_compile_intent_draft(
            request_id="req-thalamus-frontend-route",
            session_id="session-thalamus-frontend-route",
            user_message="前端路由和导航断链巡检一下 monitor",
            source="chat_handler",
            ts_ms=1776349003159,
        )
    )

    assert compiled["route"] == "skill"
    assert compiled["target_id"] == "frontend_route_audit"
    assert compiled["route_reason"] == "THALAMUS_RULE_FRONTEND_ROUTE_AUDIT"
    assert compiled["args"]["include_static"] is False


def test_classify_and_compile_intent_draft_routes_frontend_component_audit_tool() -> None:
    agent = ThalamusAgent()

    compiled = _run(
        agent.classify_and_compile_intent_draft(
            request_id="req-thalamus-frontend-component",
            session_id="session-thalamus-frontend-component",
            user_message="分析前端低价值卡片和重复组件，看看哪些可以整合",
            source="chat_handler",
            ts_ms=1776349003160,
        )
    )

    assert compiled["route"] == "skill"
    assert compiled["target_id"] == "frontend_component_value_audit"
    assert compiled["route_reason"] == "THALAMUS_RULE_FRONTEND_COMPONENT_VALUE_AUDIT"
    assert compiled["args"]["min_repeated_label_count"] == 5


def test_classify_and_compile_intent_draft_builds_explicit_supported_plan() -> None:
    agent = ThalamusAgent()

    compiled = _run(
        agent.classify_and_compile_intent_draft(
            request_id="req-thalamus-supported-plan",
            session_id="session-thalamus-supported-plan",
            user_message="first summarize the current session then read emotion snapshot",
            source="chat_handler",
            ts_ms=1776349003200,
        )
    )

    assert compiled["route"] == "plan"
    assert compiled["target_id"] == "skill_dag"
    assert compiled["route_reason"] == "THALAMUS_RULE_EXPLICIT_PLAN"
    assert [step["target_id"] for step in compiled["planned_steps"]] == [
        "session_summary",
        "emotion_query",
    ]


def test_classify_and_compile_intent_draft_builds_chinese_supported_plan() -> None:
    agent = ThalamusAgent()

    compiled = _run(
        agent.classify_and_compile_intent_draft(
            request_id="req-thalamus-supported-plan-zh",
            session_id="session-thalamus-supported-plan-zh",
            user_message="先帮我总结一下这段会话，再查一下当前情绪状态。",
            source="chat_handler",
            ts_ms=1776349003210,
        )
    )

    assert compiled["route"] == "plan"
    assert compiled["target_id"] == "skill_dag"
    assert [step["target_id"] for step in compiled["planned_steps"]] == [
        "session_summary",
        "emotion_query",
    ]


def test_classify_and_compile_intent_draft_keeps_canonical_route_seed_without_reclassifying() -> None:
    agent = ThalamusAgent()

    with patch("host_brain.tribe.thalamus_agent.classify_task_intent", new=AsyncMock()) as classify_mock:
        compiled = _run(
            agent.classify_and_compile_intent_draft(
                request_id="req-thalamus-canonical-seed",
                session_id="session-thalamus-canonical-seed",
                user_message="please summarize the session",
                existing_intent_draft={
                    "route": "skill",
                    "target_id": "session_summary",
                    "reason_code": "CANONICAL_ROUTE_REASON",
                    "args": {"query": "session"},
                },
                source="chat_handler",
                ts_ms=1776349003250,
            )
        )

    classify_mock.assert_not_called()
    assert compiled["route"] == "skill"
    assert compiled["target_id"] == "session_summary"
    assert compiled["route_reason"] == "CANONICAL_ROUTE_REASON"
    assert compiled["args"]["query"] == "session"


def test_intent_classification_prompt_templates_resolve_canonical_defaults() -> None:
    merged = _resolve_intent_classification_prompt_templates()

    assert isinstance(merged, dict)
    assert "system_prompt" in merged
    assert "user_prompt_template" in merged


def test_classify_task_intent_by_llm_uses_canonical_attempt_service(monkeypatch) -> None:
    captured: dict = {}

    async def _fake_complete(attempt):  # noqa: ANN001
        captured["attempt"] = attempt
        return {
            "content": '{"route":"chat","target_id":"","task":"normal chat","reason_code":"LLM_CHAT","confidence":0.91}'
        }

    monkeypatch.setenv("HOST_CORTEX_INTENT_LLM_ENABLED", "true")
    monkeypatch.setenv("HOST_THALAMUS_INTENT_LLM_SCENE", "THALAMUS_INTENT")
    monkeypatch.setenv("HOST_THALAMUS_INTENT_LLM_TIMEOUT_MS", "1200")

    route = RouteDecision(
        decision_id="test-thalamus-route",
        targets=(
            ProviderTarget(
                adapter_id="test.thalamus",
                provider="mock",
                model_id="mock-intent",
                timeout_ms=1_200,
            ),
        ),
        reason="TEST_THALAMUS_ROUTE",
        max_attempts=2,
    )
    composition = build_model_runtime_composition(
        prompt_registry=build_production_prompt_registry(),
        route_policy=StaticRoutePolicy(route),
        provider_adapters=[
            CallableProviderAdapter("test.thalamus", complete_fn=_fake_complete)
        ],
        audit_repository=InMemoryAttemptAuditRepository(),
    )
    dependencies.reset_model_runtime_composition(composition)
    try:
        decision = _run(
            classify_task_intent_by_llm(
                request_id="req-thalamus-canonical-attempt",
                session_id="session-thalamus-canonical-attempt",
                text="普通聊天，不要走 Codex",
                application_ports=TribeApplicationPorts(
                    model_attempt_service=dependencies.get_model_attempt_service,
                ),
            )
        )
    finally:
        dependencies.reset_model_runtime_composition()

    assert decision is not None
    assert decision["route"] == "chat"
    attempt = captured["attempt"]
    assert attempt.scene_type == "THALAMUS_INTENT"
    assert 1_000 <= attempt.timeout_ms <= 1_200
    assert attempt.prompt.prompt_id == "thalamus_agent.intent_classification_prompts"
    assert "普通聊天，不要走 Codex" in attempt.prompt.messages[1]["content"]
    assert "api_key" not in repr(attempt)
    trace = decision["model_attempt_trace"]
    assert trace["route_decision_id"] == "test-thalamus-route"
    assert trace["attempt_ids"]


def test_classify_and_compile_intent_draft_falls_back_to_cortex_classifier_when_rules_miss() -> None:
    agent = ThalamusAgent()

    with patch(
        "host_brain.tribe.thalamus_agent.classify_task_intent_by_llm",
        new=AsyncMock(
            return_value={
                "route": "skill",
                "target_id": "agent_worker",
                "task": "please fix the failing test",
                "reason_code": "LLM_ROUTE_agent_worker",
                "confidence": 0.93,
                "route_source": "llm",
            }
        ),
    ) as classify_mock:
        compiled = _run(
            agent.classify_and_compile_intent_draft(
                request_id="req-thalamus-fallback",
                session_id="session-thalamus-fallback",
                user_message="please fix the failing test",
                source="chat_handler",
                ts_ms=1776349003300,
            )
        )

    classify_mock.assert_awaited_once()
    assert compiled["route"] == "skill"
    assert compiled["target_id"] == "agent_worker"
    assert compiled["route_source"] == "llm"


def test_classify_task_intent_rejects_low_confidence_llm_route_when_rules_miss() -> None:
    with (
        patch("host_brain.tribe.thalamus_agent.classify_task_intent_by_rules", return_value=None),
        patch(
            "host_brain.tribe.thalamus_agent.classify_task_intent_by_llm",
            new=AsyncMock(
                return_value={
                    "route": "skill",
                    "target_id": "agent_worker",
                    "task": "ambiguous fallback request",
                    "reason_code": "LLM_ROUTE_agent_worker",
                    "confidence": 0.41,
                    "route_source": "llm",
                }
            ),
        ),
    ):
        decision = _run(
            classify_task_intent(
                request_id="req-thalamus-low-llm",
                session_id="session-thalamus-low-llm",
                text="ambiguous fallback request",
            )
        )

    assert decision["route"] == "chat"
    assert decision["target_id"] == ""
    assert decision["reason_code"] == "DEFAULT_CHAT"


def test_classify_task_intent_rejects_unknown_llm_skill_target_when_rules_miss() -> None:
    with (
        patch("host_brain.tribe.thalamus_agent.classify_task_intent_by_rules", return_value=None),
        patch(
            "host_brain.tribe.thalamus_agent.classify_task_intent_by_llm",
            new=AsyncMock(
                return_value={
                    "route": "skill",
                    "target_id": "invented_runtime_skill",
                    "task": "ambiguous fallback request",
                    "reason_code": "LLM_ROUTE_INVENTED",
                    "confidence": 0.96,
                    "route_source": "llm",
                }
            ),
        ),
    ):
        decision = _run(
            classify_task_intent(
                request_id="req-thalamus-unknown-llm-target",
                session_id="session-thalamus-unknown-llm-target",
                text="ambiguous fallback request",
            )
        )

    assert decision["route"] == "chat"
    assert decision["target_id"] == ""
    assert decision["reason_code"] == "DEFAULT_CHAT"


def test_classify_task_intent_normalizes_llm_plan_route_when_rules_miss() -> None:
    with (
        patch("host_brain.tribe.thalamus_agent.classify_task_intent_by_rules", return_value=None),
        patch(
            "host_brain.tribe.thalamus_agent.classify_task_intent_by_llm",
            new=AsyncMock(
                return_value={
                    "route": "plan",
                    "target_id": "",
                    "task": "ambiguous fallback request",
                    "reason_code": "LLM_ROUTE_PLAN",
                    "confidence": 0.93,
                    "route_source": "llm",
                }
            ),
        ),
    ):
        decision = _run(
            classify_task_intent(
                request_id="req-thalamus-llm-plan",
                session_id="session-thalamus-llm-plan",
                text="ambiguous fallback request",
            )
        )

    assert decision["route"] == "plan"
    assert decision["target_id"] == "skill_dag"
    assert decision["reason_code"] == "LLM_ROUTE_PLAN"
    assert decision["route_source"] == "llm"


def test_classify_task_intent_strips_llm_chat_target_when_rules_miss() -> None:
    with (
        patch("host_brain.tribe.thalamus_agent.classify_task_intent_by_rules", return_value=None),
        patch(
            "host_brain.tribe.thalamus_agent.classify_task_intent_by_llm",
            new=AsyncMock(
                return_value={
                    "route": "chat",
                    "target_id": "agent_worker",
                    "task": "ambiguous fallback request",
                    "reason_code": "LLM_ROUTE_CHAT",
                    "confidence": 0.95,
                    "route_source": "llm",
                }
            ),
        ),
    ):
        decision = _run(
            classify_task_intent(
                request_id="req-thalamus-llm-chat",
                session_id="session-thalamus-llm-chat",
                text="ambiguous fallback request",
            )
        )

    assert decision["route"] == "chat"
    assert decision["target_id"] == ""
    assert decision["reason_code"] == "LLM_ROUTE_CHAT"


def test_classify_task_intent_by_rules_keeps_legacy_translate_rule() -> None:
    decision = classify_task_intent_by_rules("please translate this into English")

    assert decision is not None
    assert decision["route"] == "delegate"
    assert decision["target_id"] == "translate_agent"
    assert decision["route_source"] == "rule"
    assert decision["reason_code"] == "RULE_FORCE_TRANSLATE_AGENT"


def test_classify_task_intent_by_rules_routes_planning_advice_to_intent_agent() -> None:
    decision = classify_task_intent_by_rules("帮我整理一下我现在应该先做什么，再给我一个三步建议。")

    assert decision is not None
    assert decision["route"] == "delegate"
    assert decision["target_id"] == "intent_agent"


def test_classify_task_intent_by_rules_routes_generic_execution_without_llm() -> None:
    decision = classify_task_intent_by_rules("继续执行上一个任务")

    assert decision is not None
    assert decision["route"] == "delegate"
    assert decision["target_id"] == "intent_agent"
    assert decision["reason_code"] == "RULE_ACTION_INTENT"


def test_classify_task_intent_by_rules_routes_short_continuation_to_intent_agent() -> None:
    decision = classify_task_intent_by_rules("开始吧")

    assert decision is not None
    assert decision["route"] == "delegate"
    assert decision["target_id"] == "intent_agent"
    assert decision["reason_code"] == "RULE_CONTEXT_CONTINUATION"


def test_classify_task_intent_by_rules_routes_open_settings_to_open_app() -> None:
    decision = classify_task_intent_by_rules("打开设置")

    assert decision is not None
    assert decision["route"] == "skill"
    assert decision["target_id"] == "open_app"
    assert decision["reason_code"] == "THALAMUS_RULE_OPEN_APP"


def test_classify_task_intent_by_rules_routes_workspace_inspection_to_agent_inspect() -> None:
    decision = classify_task_intent_by_rules("查看完整主链")

    assert decision is not None
    assert decision["route"] == "skill"
    assert decision["target_id"] == "agent_inspect"
    assert decision["reason_code"] == "THALAMUS_RULE_CODEX_WORKSPACE_INSPECT"
    assert decision["args"]["workspace_strategy"] == "runtime_repo"


def test_classify_task_intent_by_rules_routes_workspace_work_to_agent_worker() -> None:
    decision = classify_task_intent_by_rules("开始优化意图识别和路由")

    assert decision is not None
    assert decision["route"] == "skill"
    assert decision["target_id"] == "agent_worker"
    assert decision["reason_code"] == "THALAMUS_RULE_CODEX_WORKSPACE_WORKER"
    assert decision["args"]["run_mode"] == "worker"


def test_classify_task_intent_by_rules_routes_frontend_browser_verification_to_codex_acceptance() -> None:
    decision = classify_task_intent_by_rules("使用无头浏览器验证前端效果")

    assert decision is not None
    assert decision["route"] == "skill"
    assert decision["target_id"] == "agent_frontend_acceptance"
    assert decision["reason_code"] == "THALAMUS_RULE_CODEX_PRODUCTION"
    assert decision["args"]["start_background"] is True


def test_classify_task_intent_by_rules_routes_browser_research_to_production_skill() -> None:
    decision = classify_task_intent_by_rules("用浏览器做网购商品比价调研")

    assert decision is not None
    assert decision["route"] == "skill"
    assert decision["target_id"] == "browser_research"
    assert decision["reason_code"] == "THALAMUS_RULE_CODEX_PRODUCTION"


def test_classify_task_intent_by_rules_routes_web_corpus_collection_to_production_skill() -> None:
    decision = classify_task_intent_by_rules("收集公开授权网页训练语料并清洗成数据集")

    assert decision is not None
    assert decision["route"] == "skill"
    assert decision["target_id"] == "agent_web_corpus_collector"
    assert decision["reason_code"] == "THALAMUS_RULE_CODEX_PRODUCTION"


def test_classify_task_intent_by_rules_routes_epub_packaging_to_production_skill() -> None:
    decision = classify_task_intent_by_rules("从网页上获取我有授权的轻小说内容和图片然后封装成 epub")

    assert decision is not None
    assert decision["route"] == "skill"
    assert decision["target_id"] == "agent_epub_builder"
    assert decision["reason_code"] == "THALAMUS_RULE_CODEX_PRODUCTION"


def test_classify_task_intent_by_rules_routes_lightnovel_recommendation_to_production_skill() -> None:
    decision = classify_task_intent_by_rules("从每日榜单或者最新资源里抓封面和简介推荐轻小说")

    assert decision is not None
    assert decision["route"] == "skill"
    assert decision["target_id"] == "agent_lightnovel_recommender"
    assert decision["reason_code"] == "THALAMUS_RULE_CODEX_PRODUCTION"


def test_classify_task_intent_by_rules_routes_lightnovel_recommendation_selection_to_epub_builder() -> None:
    decision = classify_task_intent_by_rules("推荐的第一本")

    assert decision is not None
    assert decision["route"] == "skill"
    assert decision["target_id"] == "agent_epub_builder"
    assert decision["reason_code"] == "THALAMUS_RULE_CODEX_PRODUCTION"
    assert decision["args"]["recommendation_index"] == 1
    assert decision["args"]["site_adapter"] == "lightnovel.fun"
    assert decision["args"]["start_background"] is True


def test_classify_task_intent_by_rules_routes_repo_commit_phrase_to_agent_worker() -> None:
    decision = classify_task_intent_by_rules("继续整理提交")

    assert decision is not None
    assert decision["route"] == "skill"
    assert decision["target_id"] == "agent_worker"
    assert decision["reason_code"] == "THALAMUS_RULE_CODEX_WORKSPACE_WORKER"


def test_classify_task_intent_by_rules_routes_deploy_without_llm() -> None:
    decision = classify_task_intent_by_rules("帮我部署这个服务")

    assert decision is not None
    assert decision["route"] == "delegate"
    assert decision["target_id"] == "intent_agent"
    assert decision["reason_code"] == "RULE_ACTION_INTENT"


def test_classify_task_intent_by_rules_keeps_ui_preference_as_memory_store() -> None:
    decision = classify_task_intent_by_rules("记住我喜欢偏冷色系UI")

    assert decision is not None
    assert decision["route"] == "skill"
    assert decision["target_id"] == "memory_store"
    assert decision["reason_code"] == "RULE_FORCE_MEMORY_STORE"


def test_classify_task_intent_by_rules_routes_memory_followup_to_chat() -> None:
    decision = classify_task_intent_by_rules("我刚才让你记住的临时口令是什么？请只回答口令本身。")

    assert decision is not None
    assert decision["route"] == "chat"
    assert decision["target_id"] == ""
    assert decision["reason_code"] == "RULE_CONTEXTUAL_CHAT_FOLLOWUP"


def test_classify_task_intent_by_rules_routes_anchor_followup_to_chat() -> None:
    decision = classify_task_intent_by_rules("那个临时锚点还记得吗？只回答锚点。")

    assert decision is not None
    assert decision["route"] == "chat"
    assert decision["target_id"] == ""
    assert decision["reason_code"] == "RULE_CONTEXTUAL_CHAT_FOLLOWUP"


def test_classify_task_intent_by_rules_routes_continuity_probe_to_chat() -> None:
    decision = classify_task_intent_by_rules("连续对话测试第三轮：把这个口令变成一句 12 字以内的短句，仍然要包含它。")

    assert decision is not None
    assert decision["route"] == "chat"
    assert decision["target_id"] == ""
    assert decision["reason_code"] == "RULE_CONTEXTUAL_CHAT_FOLLOWUP"


def test_classify_task_intent_by_rules_routes_long_dialogue_probe_to_chat() -> None:
    decision = classify_task_intent_by_rules(
        "长对话连续性测试01：建立表达偏好。之后除非我改口，偏冷色、少废话、保留一点温度、别太客服腔。简短确认。"
    )

    assert decision is not None
    assert decision["route"] == "chat"
    assert decision["target_id"] == ""
    assert decision["reason_code"] == "RULE_CONTEXTUAL_CHAT_FOLLOWUP"


def test_classify_task_intent_by_rules_routes_report_preference_followup_to_chat() -> None:
    decision = classify_task_intent_by_rules("现在我对测试报告的要求是什么？不要漏掉刚才修正。")

    assert decision is not None
    assert decision["route"] == "chat"
    assert decision["target_id"] == ""
    assert decision["reason_code"] == "RULE_CONTEXTUAL_CHAT_FOLLOWUP"


def test_classify_task_intent_by_rules_routes_report_update_to_chat() -> None:
    decision = classify_task_intent_by_rules("再修正：报告要可复现，但也要保留一点主观体验备注，不然太干。")

    assert decision is not None
    assert decision["route"] == "chat"
    assert decision["target_id"] == ""
    assert decision["reason_code"] == "RULE_CONTEXTUAL_CHAT_FOLLOWUP"


def test_classify_task_intent_by_rules_routes_anchor_alias_to_chat() -> None:
    decision = classify_task_intent_by_rules("给锚点加个别名：以后「小橙灯」也指橙灯码。")

    assert decision is not None
    assert decision["route"] == "chat"
    assert decision["target_id"] == ""
    assert decision["reason_code"] == "RULE_CONTEXTUAL_CHAT_FOLLOWUP"


def test_classify_task_intent_by_rules_routes_long_dialogue_metrics_to_chat() -> None:
    decision = classify_task_intent_by_rules("如果长对话要测连续性，你觉得最该看哪三类指标？简短说。")

    assert decision is not None
    assert decision["route"] == "chat"
    assert decision["target_id"] == ""
    assert decision["reason_code"] == "RULE_CONTEXTUAL_CHAT_FOLLOWUP"


def test_classify_task_intent_by_rules_routes_style_name_to_chat() -> None:
    decision = classify_task_intent_by_rules("给这个表达风格起一个临时名字，别超过8个字。")

    assert decision is not None
    assert decision["route"] == "chat"
    assert decision["target_id"] == ""
    assert decision["reason_code"] == "RULE_CONTEXTUAL_CHAT_FOLLOWUP"


def test_classify_task_intent_by_rules_readonly_code_plan_not_agent_worker() -> None:
    decision = classify_task_intent_by_rules("给我一个完整方案，不要改代码")

    assert decision is not None
    assert decision["route"] == "delegate"
    assert decision["target_id"] == "intent_agent"
    assert decision["reason_code"] == "RULE_ACTION_INTENT"


def test_classify_task_intent_by_rules_routes_relational_followup_to_chat() -> None:
    decision = classify_task_intent_by_rules("那你还会帮我吗")

    assert decision is not None
    assert decision["route"] == "chat"
    assert decision["target_id"] == ""
    assert str(decision["reason_code"]).startswith("RELATIONAL_CHAT:")


def test_classify_task_intent_routes_connectivity_check_to_chat() -> None:
    decision = classify_task_intent_by_rules("测试一下现在能回吗")

    assert decision is not None
    assert decision["route"] == "chat"
    assert decision["target_id"] == ""
    assert decision["route_source"] == "thalamus.rule"
    assert decision["reason_code"] == "THALAMUS_RULE_CONNECTIVITY_CHECK"


def test_classify_task_intent_routes_gateway_status_reply_check_to_chat() -> None:
    decision = classify_task_intent_by_rules(
        "Codex QQ gateway闭环验收：请用一句中文短句回复当前意图识别和场景路由是否在线。"
    )

    assert decision is not None
    assert decision["route"] == "chat"
    assert decision["target_id"] == ""
    assert decision["route_source"] == "thalamus.rule"
    assert decision["reason_code"] == "THALAMUS_RULE_STATUS_REPLY_CHECK"


def test_classify_task_intent_routes_authorized_pentest_to_pentest_agent() -> None:
    decision = classify_task_intent_by_rules("帮我对 target=example.com 做授权安全扫描，只做侦察和配置审计")

    assert decision is not None
    assert decision["route"] == "delegate"
    assert decision["target_id"] == "pentest_agent"
    assert decision["route_source"] == "thalamus.rule"
    assert decision["reason_code"] == "THALAMUS_RULE_PENTEST_AGENT"
    assert decision["args"]["requested_execution"] is True
    assert decision["args"]["secure_run_required"] is True
    assert decision["args"]["secure_run_surface"] == "pentest.secure-run"
    assert decision["args"]["scope_hint_present"] is True


def test_classify_task_intent_routes_structured_scope_pentest_hint_to_agent() -> None:
    decision = classify_task_intent_by_rules(
        "target=http://127.0.0.1:8888 scope=127.0.0.1 readonly scan, no exploit"
    )

    assert decision is not None
    assert decision["route"] == "delegate"
    assert decision["target_id"] == "pentest_agent"
    assert decision["reason_code"] == "THALAMUS_RULE_PENTEST_AGENT"
    assert decision["args"]["requested_execution"] is True
    assert decision["args"]["secure_run_required"] is True
    assert decision["args"]["scope_hint_present"] is True


def test_classify_task_intent_keeps_security_advice_out_of_pentest_agent() -> None:
    decision = classify_task_intent_by_rules("SQL 注入是什么，应该怎么防？")

    assert decision is None or decision.get("target_id") != "pentest_agent"


def test_classify_task_intent_routes_takeover_as_untrusted_secure_run_request() -> None:
    """Takeover language is planning material, never an autonomous strategy."""
    decision = classify_task_intent_by_rules(
        "授权渗透测试 scope=10.10.10.0/24，把这个域端了，拿下域控"
    )

    assert decision is not None
    assert decision["route"] == "delegate"
    assert decision["target_id"] == "pentest_agent"
    assert decision["args"]["requested_goal"] == {"goal_type": "domain_takeover"}
    assert decision["args"]["requested_execution"] is True
    assert decision["args"]["secure_run_required"] is True
    assert "strategy" not in decision["args"]
    assert "allow_exploit" not in decision["args"]
    assert decision["args"]["scope_hint_present"] is True


def test_classify_task_intent_takeover_with_getshell() -> None:
    """getshell + 全量拿下 → domain_takeover。"""
    decision = classify_task_intent_by_rules(
        "授权渗透测试，scope=192.168.1.0/24，全部 getshell，一网打尽"
    )

    assert decision is not None
    assert decision["args"]["requested_goal"] == {"goal_type": "domain_takeover"}
    assert decision["args"]["secure_run_required"] is True


def test_classify_task_intent_takeover_with_lateral() -> None:
    """横向清场语义 → domain_takeover。"""
    decision = classify_task_intent_by_rules(
        "授权渗透测试 scope=172.16.0.0/16，横向移动拿下所有内网主机"
    )

    assert decision is not None
    assert decision["args"]["requested_goal"] == {"goal_type": "domain_takeover"}
    assert decision["args"]["secure_run_required"] is True


def test_classify_task_intent_plain_scan_requires_secure_run_without_goal() -> None:
    """A read-only network scan is still an effect and requires SecureRun."""
    decision = classify_task_intent_by_rules(
        "帮我对 target=example.com 做授权安全扫描，只做侦察和配置审计"
    )

    assert decision is not None
    assert decision["target_id"] == "pentest_agent"
    assert decision["args"].get("strategy") is None
    assert decision["args"].get("requested_goal") is None
    assert decision["args"]["requested_execution"] is True
    assert decision["args"]["secure_run_required"] is True


def test_classify_and_compile_intent_draft_routes_pentest_message_only() -> None:
    agent = ThalamusAgent()

    compiled = _run(
        agent.classify_and_compile_intent_draft(
            request_id="req-thalamus-pentest",
            session_id="session-thalamus-pentest",
            user_message="对目标：https://example.com 做授权漏洞评估，只允许只读检查",
            person_id="person-001",
        )
    )

    assert compiled["route"] == "delegate"
    assert compiled["target_id"] == "pentest_agent"
    assert compiled["action"] == "exec_intent"
    assert compiled["args"]["text"] == "对目标：https://example.com 做授权漏洞评估，只允许只读检查"


def test_classify_task_intent_does_not_treat_codex_name_as_code_request() -> None:
    decision = classify_task_intent_by_rules("Codex 现在在线吗？")

    assert decision is not None
    assert decision["route"] == "chat"
    assert decision["target_id"] == ""
    assert decision["reason_code"] == "THALAMUS_RULE_CONNECTIVITY_CHECK"


def test_classify_task_intent_fastpath_routes_connectivity_check_to_chat() -> None:
    decision = classify_task_intent_fastpath("测试下能不能回复")

    assert decision is not None
    assert decision["route"] == "chat"
    assert decision["target_id"] == ""
    assert decision["route_source"] == "rule.fastpath"
    assert decision["reason_code"] == "THALAMUS_RULE_CONNECTIVITY_CHECK"


def test_classify_task_intent_fastpath_routes_simple_project_technical_advice_to_chat() -> None:
    decision = classify_task_intent_fastpath("Neo4j 连接不上 bolt://127.0.0.1:17687，先看什么？")

    assert decision is not None
    assert decision["route"] == "chat"
    assert decision["target_id"] == ""
    assert decision["route_source"] == "rule.fastpath"
    assert decision["reason_code"] == "THALAMUS_RULE_SIMPLE_TECH_CHAT"


def test_compile_simple_project_technical_advice_uses_narrative_action() -> None:
    agent = ThalamusAgent()

    compiled = agent.compile_intent_draft(
        request_id="req-thalamus-neo4j",
        session_id="session-thalamus-neo4j",
        user_message="Neo4j 连接不上 bolt://127.0.0.1:17687，先看什么？",
        raw_route_decision=classify_task_intent_fastpath("Neo4j 连接不上 bolt://127.0.0.1:17687，先看什么？"),
    )

    assert compiled["route"] == "chat"
    assert compiled["action"] == "narrative_only"
    assert compiled["args"]["text"] == "Neo4j 连接不上 bolt://127.0.0.1:17687，先看什么？"


def test_classify_task_intent_keeps_real_test_work_on_codex() -> None:
    decision = classify_task_intent_by_rules("帮我跑一下测试，看看失败的用例")

    assert decision is not None
    assert decision["route"] == "skill"
    assert decision["target_id"] == "agent_worker"


def test_classify_task_intent_fastpath_routes_english_greeting_to_chat() -> None:
    decision = classify_task_intent_fastpath("hello")

    assert decision is not None
    assert decision["route"] == "chat"
    assert decision["route_source"] == "rule.fastpath"
    assert decision["reason_code"] == "THALAMUS_RULE_PHATIC_GREETING"


def test_classify_task_intent_fastpath_routes_image_social_followup_to_chat() -> None:
    decision = classify_task_intent_fastpath("我不委屈")

    assert decision is not None
    assert decision["route"] == "chat"
    assert decision["route_source"] == "rule.fastpath"
    assert decision["reason_code"] == "THALAMUS_RULE_PHATIC_SOCIAL_FOLLOWUP"


def test_classify_task_intent_fastpath_routes_generic_image_social_negation_to_chat() -> None:
    decision = classify_task_intent_fastpath("没有闹别扭啊")

    assert decision is not None
    assert decision["route"] == "chat"
    assert decision["route_source"] == "rule.fastpath"
    assert decision["reason_code"] == "THALAMUS_RULE_PHATIC_SOCIAL_FOLLOWUP"


def test_classify_task_intent_fastpath_routes_assistant_reply_followup_to_chat() -> None:
    decision = classify_task_intent_fastpath("知道什么")

    assert decision is not None
    assert decision["route"] == "chat"
    assert decision["route_source"] == "rule.fastpath"
    assert decision["reason_code"] == "THALAMUS_RULE_ASSISTANT_REPLY_FOLLOWUP"


def test_classify_task_intent_fastpath_routes_punctuation_to_chat_without_llm() -> None:
    decision = classify_task_intent_fastpath("？")

    assert decision is not None
    assert decision["route"] == "chat"
    assert decision["target_id"] == ""
    assert decision["route_source"] == "rule.fastpath"
    assert decision["reason_code"] == "THALAMUS_RULE_PHATIC_PUNCT"


def test_classify_and_compile_intent_draft_does_not_misroute_task_like_message_as_phatic_chat() -> None:
    agent = ThalamusAgent()

    compiled = _run(
        agent.classify_and_compile_intent_draft(
            request_id="req-thalamus-not-phatic-task",
            session_id="session-thalamus-not-phatic-task",
            user_message="今天有点累，但还是帮我把这段翻译一下。",
            source="chat_handler",
            ts_ms=1776349003310,
        )
    )

    assert compiled["route"] != "chat"
    assert compiled["target_id"] == "translate_agent"
    assert compiled["route_source"] == "rule"
    assert compiled["route_reason"] == "RULE_FORCE_TRANSLATE_AGENT"


def test_classify_and_compile_intent_draft_keeps_long_support_chat_out_of_emotion_query() -> None:
    agent = ThalamusAgent()

    compiled = _run(
        agent.classify_and_compile_intent_draft(
            request_id="req-thalamus-long-support-chat",
            session_id="session-thalamus-long-support-chat",
            user_message=(
                "我想和你认真聊一会儿。不是为了完成什么任务，也不是为了让你替我列清单，"
                "而是我最近脑子里堆了很多事情：工作上的疲惫、和朋友之间说不清的别扭，"
                "还有我自己都分不太清现在到底是焦虑、委屈，还是只是太累了。"
                "我想让你先听我说，再慢慢陪我理顺。"
            ),
            source="chat_handler",
            ts_ms=1776349003330,
        )
    )

    assert compiled["route"] == "chat"
    assert compiled["target_id"] == ""
    assert compiled["route_reason"] == "THALAMUS_RULE_CONTEXT_CHAT"


def test_classify_and_compile_intent_draft_routes_self_heal_to_codex() -> None:
    agent = ThalamusAgent()

    compiled = _run(
        agent.classify_and_compile_intent_draft(
            request_id="req-thalamus-self-heal",
            session_id="session-thalamus-self-heal",
            user_message="please self heal the host health check failure",
            source="chat_handler",
            ts_ms=1776349003500,
        )
    )

    assert compiled["route"] == "skill"
    assert compiled["target_id"] == "agent_self_heal"
    assert compiled["route_reason"] == "THALAMUS_RULE_CODEX_SELF_HEAL"
    assert compiled["args"]["run_mode"] == "self_heal"
    assert compiled["args"]["workspace_strategy"] == "runtime_repo"
    assert compiled["args"]["include_dependency_probes"] is True


def test_classify_and_compile_intent_draft_routes_production_brief_to_codex_background() -> None:
    agent = ThalamusAgent()

    with patch("host_brain.app.user_preference_store.build_user_preference_pack", return_value={}):
        compiled = _run(
            agent.classify_and_compile_intent_draft(
                request_id="req-thalamus-production-brief",
                session_id="session-thalamus-production-brief",
                user_message="生成一个今日工作简报，后台异步跑就行",
                source="chat_handler",
                ts_ms=1776349003520,
            )
        )

    assert compiled["route"] == "skill"
    assert compiled["target_id"] == "agent_workbench_brief"
    assert compiled["route_reason"] == "THALAMUS_RULE_CODEX_PRODUCTION"
    assert compiled["args"]["run_mode"] == "research"
    assert compiled["args"]["start_background"] is True
    assert compiled["args"]["workspace_strategy"] == "temp_workspace"


def test_classify_and_compile_intent_draft_routes_frontend_acceptance_to_runtime_repo() -> None:
    agent = ThalamusAgent()

    compiled = _run(
        agent.classify_and_compile_intent_draft(
            request_id="req-thalamus-frontend-acceptance",
            session_id="session-thalamus-frontend-acceptance",
            user_message="使用无头浏览器验证前端效果",
            source="chat_handler",
            ts_ms=1776349003530,
        )
    )

    assert compiled["route"] == "skill"
    assert compiled["target_id"] == "agent_frontend_acceptance"
    assert compiled["route_reason"] == "THALAMUS_RULE_CODEX_PRODUCTION"
    assert compiled["args"]["run_mode"] == "inspect"
    assert compiled["args"]["workspace_strategy"] == "runtime_repo"
    assert compiled["args"]["start_background"] is True


def test_classify_and_compile_intent_draft_routes_epub_builder_to_temp_workspace_worker() -> None:
    agent = ThalamusAgent()

    compiled = _run(
        agent.classify_and_compile_intent_draft(
            request_id="req-thalamus-epub-builder",
            session_id="session-thalamus-epub-builder",
            user_message="把我有授权的轻小说内容和图片封装成 epub",
            source="chat_handler",
            ts_ms=1776349003540,
        )
    )

    assert compiled["route"] == "skill"
    assert compiled["target_id"] == "agent_epub_builder"
    assert compiled["args"]["run_mode"] == "worker"
    assert compiled["args"]["workspace_strategy"] == "temp_workspace"


def test_classify_and_compile_intent_draft_routes_recommendation_selection_to_epub_builder() -> None:
    agent = ThalamusAgent()

    compiled = _run(
        agent.classify_and_compile_intent_draft(
            request_id="req-thalamus-epub-selection",
            session_id="session-thalamus-epub-selection",
            user_message="推荐的第一本",
            source="chat_handler",
            ts_ms=1776349003542,
        )
    )

    assert compiled["route"] == "skill"
    assert compiled["target_id"] == "agent_epub_builder"
    assert compiled["args"]["run_mode"] == "worker"
    assert compiled["args"]["workspace_strategy"] == "temp_workspace"
    assert compiled["args"]["recommendation_index"] == 1
    assert compiled["args"]["site_adapter"] == "lightnovel.fun"


def test_classify_and_compile_intent_draft_routes_lightnovel_recommender_to_temp_workspace() -> None:
    agent = ThalamusAgent()

    compiled = _run(
        agent.classify_and_compile_intent_draft(
            request_id="req-thalamus-lightnovel-recommender",
            session_id="session-thalamus-lightnovel-recommender",
            user_message="从每日榜单或者最新资源里面抓取封面和简介给我推荐轻小说",
            source="chat_handler",
            ts_ms=1776349003545,
        )
    )

    assert compiled["route"] == "skill"
    assert compiled["target_id"] == "agent_lightnovel_recommender"
    assert compiled["args"]["run_mode"] == "worker"
    assert compiled["args"]["workspace_strategy"] == "temp_workspace"
    assert compiled["args"]["site_adapter"] == "lightnovel.fun"


def test_classify_and_compile_intent_draft_defaults_runtime_repo_for_codex_code_requests() -> None:
    agent = ThalamusAgent()

    compiled = _run(
        agent.classify_and_compile_intent_draft(
            request_id="req-thalamus-codex-runtime-repo",
            session_id="session-thalamus-codex-runtime-repo",
            user_message="please fix the failing test and show me the code",
            source="chat_handler",
            ts_ms=1776349003550,
        )
    )

    assert compiled["route"] == "skill"
    assert compiled["target_id"] == "agent_worker"
    assert compiled["args"]["workspace_strategy"] == "runtime_repo"
    assert compiled["args"]["run_mode"] == "worker"
    assert compiled["args"]["task"] == "please fix the failing test and show me the code"


def test_classify_and_compile_intent_draft_uses_inspect_temp_workspace_for_lightweight_code_snippet() -> None:
    agent = ThalamusAgent()

    compiled = _run(
        agent.classify_and_compile_intent_draft(
            request_id="req-thalamus-codex-light-snippet",
            session_id="session-thalamus-codex-light-snippet",
            user_message="写一个 Python 函数，输入数组后去重并按出现频次降序返回。",
            source="chat_handler",
            ts_ms=1776349003560,
        )
    )

    assert compiled["route"] == "skill"
    assert compiled["target_id"] == "agent_inspect"
    assert compiled["args"]["run_mode"] == "inspect"
    assert compiled["args"]["allow_temp_workspace"] is True
    assert "workspace_strategy" not in compiled["args"]


def test_classify_and_compile_intent_draft_defaults_to_chat_when_no_rule_or_llm() -> None:
    agent = ThalamusAgent()

    with patch(
        "host_brain.tribe.thalamus_agent.classify_task_intent_by_llm",
        new=AsyncMock(return_value=None),
    ):
        compiled = _run(
            agent.classify_and_compile_intent_draft(
                request_id="req-thalamus-default-chat",
                session_id="session-thalamus-default-chat",
                user_message=(
                    "我想和你聊聊最近这段时间的很多事情，包括工作上的疲惫、和朋友之间的距离感、"
                    "还有我对未来节奏的犹豫。我不一定马上要结论，只是想把这些感觉慢慢说给你听，"
                    "也想看看你会怎么接住我。"
                    "\n\n"
                    "这段话我会继续补充很多细节，比如过去几周发生的事情、我对自己状态的反思、"
                    "还有我为什么会在这种时候更想慢慢聊，而不是马上要一个短回答。"
                    "我也想把每一个让我犹豫、拧巴、停不下来的瞬间都描述得更完整一点，"
                    "包括那些我没来得及和别人说、但其实一直压在心里的部分。"
                ),
                source="chat_handler",
                ts_ms=1776349003400,
            )
        )

    assert compiled["route"] == "chat"
    assert compiled["route_reason"] == "THALAMUS_RULE_CONTEXT_CHAT"
    assert compiled["route_source"] == "thalamus.rule"
