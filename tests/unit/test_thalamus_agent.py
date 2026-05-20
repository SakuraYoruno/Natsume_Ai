from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from host_brain.tribe.thalamus_agent import (
    ThalamusAgent,
    _resolve_intent_classification_prompt_templates,
    classify_task_intent,
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


def test_classify_and_compile_intent_draft_routes_hub_skill_list_from_natural_language() -> None:
    agent = ThalamusAgent()

    compiled = _run(
        agent.classify_and_compile_intent_draft(
            request_id="req-thalamus-hub-list",
            session_id="session-thalamus-hub-list",
            user_message="Blue, list skills for me",
            source="chat_handler",
            ts_ms=1776349003000,
        )
    )

    assert compiled["route"] == "skill"
    assert compiled["target_id"] == "hub_skill_list"
    assert compiled["route_reason"] == "THALAMUS_RULE_HUB_SKILL_LIST"
    assert compiled["route_source"] == "thalamus.rule"


def test_classify_and_compile_intent_draft_routes_hub_skill_search_with_query() -> None:
    agent = ThalamusAgent()

    compiled = _run(
        agent.classify_and_compile_intent_draft(
            request_id="req-thalamus-hub-search",
            session_id="session-thalamus-hub-search",
            user_message="search skill weather",
            source="chat_handler",
            ts_ms=1776349003100,
        )
    )

    assert compiled["route"] == "skill"
    assert compiled["target_id"] == "hub_skill_search"
    assert compiled["args"]["query"] == "weather"
    assert compiled["route_reason"] == "THALAMUS_RULE_HUB_SKILL_SEARCH"


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


def test_classify_and_compile_intent_draft_falls_back_to_cortex_classifier_when_rules_miss() -> None:
    agent = ThalamusAgent()

    with patch(
        "host_brain.tribe.thalamus_agent.classify_task_intent_by_llm",
        new=AsyncMock(
            return_value={
                "route": "skill",
                "target_id": "codex_worker",
                "task": "please fix the failing test",
                "reason_code": "LLM_ROUTE_codex_worker",
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
    assert compiled["target_id"] == "codex_worker"
    assert compiled["route_source"] == "llm"


def test_classify_task_intent_rejects_low_confidence_llm_route_when_rules_miss() -> None:
    with (
        patch("host_brain.tribe.thalamus_agent.classify_task_intent_by_rules", return_value=None),
        patch(
            "host_brain.tribe.thalamus_agent.classify_task_intent_by_llm",
            new=AsyncMock(
                return_value={
                    "route": "skill",
                    "target_id": "codex_worker",
                    "task": "ambiguous fallback request",
                    "reason_code": "LLM_ROUTE_codex_worker",
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
                    "target_id": "codex_worker",
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


def test_classify_task_intent_by_rules_routes_workspace_inspection_to_codex_inspect() -> None:
    decision = classify_task_intent_by_rules("查看完整主链")

    assert decision is not None
    assert decision["route"] == "skill"
    assert decision["target_id"] == "codex_inspect"
    assert decision["reason_code"] == "THALAMUS_RULE_CODEX_WORKSPACE_INSPECT"
    assert decision["args"]["workspace_strategy"] == "runtime_repo"


def test_classify_task_intent_by_rules_routes_workspace_work_to_codex_worker() -> None:
    decision = classify_task_intent_by_rules("开始优化意图识别和路由")

    assert decision is not None
    assert decision["route"] == "skill"
    assert decision["target_id"] == "codex_worker"
    assert decision["reason_code"] == "THALAMUS_RULE_CODEX_WORKSPACE_WORKER"
    assert decision["args"]["run_mode"] == "worker"


def test_classify_task_intent_by_rules_routes_frontend_browser_verification_to_codex_worker() -> None:
    decision = classify_task_intent_by_rules("使用无头浏览器验证前端效果")

    assert decision is not None
    assert decision["route"] == "skill"
    assert decision["target_id"] == "codex_worker"
    assert decision["reason_code"] == "THALAMUS_RULE_CODEX_WORKSPACE_WORKER"


def test_classify_task_intent_by_rules_routes_repo_commit_phrase_to_codex_worker() -> None:
    decision = classify_task_intent_by_rules("继续整理提交")

    assert decision is not None
    assert decision["route"] == "skill"
    assert decision["target_id"] == "codex_worker"
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


def test_classify_task_intent_by_rules_readonly_code_plan_not_codex_worker() -> None:
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
    assert decision["target_id"] == "codex_worker"


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
    assert compiled["target_id"] == "codex_self_heal"
    assert compiled["route_reason"] == "THALAMUS_RULE_CODEX_SELF_HEAL"
    assert compiled["args"]["run_mode"] == "self_heal"
    assert compiled["args"]["workspace_strategy"] == "runtime_repo"
    assert compiled["args"]["include_dependency_probes"] is True


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
    assert compiled["target_id"] == "codex_worker"
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
    assert compiled["target_id"] == "codex_inspect"
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
