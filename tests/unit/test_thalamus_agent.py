from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from host_brain.tribe.thalamus_agent import (
    ThalamusAgent,
    _resolve_intent_classification_prompt_templates,
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
                "target_id": "codex_codegen",
                "task": "please fix the failing test",
                "reason_code": "LLM_ROUTE_CODEX_CODEGEN",
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
    assert compiled["target_id"] == "codex_codegen"
    assert compiled["route_source"] == "llm"


def test_classify_task_intent_by_rules_keeps_legacy_translate_rule() -> None:
    decision = classify_task_intent_by_rules("please translate this into English")

    assert decision is not None
    assert decision["route"] == "delegate"
    assert decision["target_id"] == "translate_agent"
    assert decision["route_source"] == "rule"
    assert decision["reason_code"] == "RULE_FORCE_TRANSLATE_AGENT"


def test_classify_and_compile_intent_draft_routes_self_heal_to_codex(monkeypatch) -> None:
    agent = ThalamusAgent()
    monkeypatch.setattr(
        "host_brain.skills.codex_task_builder._safe_build_runtime_health_snapshot",
        lambda request_id, include_dependency_probes: {
            "runtime_repo_root": "D:/Natsume_Ai",
            "health_status": "DEGRADED",
            "health_reason_code": "EDGE_RPC_DOWN",
            "health_report": {"status": "DEGRADED", "reason_code": "EDGE_RPC_DOWN"},
            "dependency_probe_error": None,
        },
    )

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
    assert compiled["target_id"] == "codex_codegen"
    assert compiled["route_reason"] == "THALAMUS_RULE_CODEX_SELF_HEAL"
    assert compiled["args"]["run_mode"] == "self_heal"
    assert compiled["args"]["workspace_strategy"] == "runtime_repo"
    assert "runtime:service_health" in compiled["args"]["evidence_refs"]
    assert "EDGE_RPC_DOWN" in compiled["args"]["context"]


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
                user_message="just chatting a bit",
                source="chat_handler",
                ts_ms=1776349003400,
            )
        )

    assert compiled["route"] == "chat"
    assert compiled["route_reason"] == "DEFAULT_CHAT"
    assert compiled["route_source"] == "default"
