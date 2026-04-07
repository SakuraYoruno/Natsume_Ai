from __future__ import annotations

import json

from host_brain.app.handlers.emotion_agent_handler import _build_emotion_agent_system_prompt
from host_brain.app.prompt_template_store import PromptTemplateStore, resolve_prompt_template_json_merged
from host_brain.audit.sqlite_ledger import SQLiteLedger


def test_build_emotion_agent_system_prompt_uses_template_override(monkeypatch) -> None:
    ledger = SQLiteLedger(":memory:", check_same_thread=False)
    store = PromptTemplateStore(ledger=ledger, cache_ttl_ms=0)
    store.upsert_template(
        "emotion_agent_handler.prompt_templates",
        content=json.dumps(
            {
                "system_role_line": "custom role",
                "current_emotion_tag_line": "tag={current_emotion_tag}",
                "mood_state_line": "mood={mood_state}",
                "emotion_echo_line": "echo={echo_style}",
                "user_profile_line": "profile={profile_hint}",
                "tools_intro": "tools:",
                "tool_emotion_history_search": "- history tool",
                "tool_emotion_timeline_query": "- timeline tool",
                "tool_search_knowledge": "- knowledge tool",
                "analysis_intro": "deliver:",
                "analysis_item_1": "A",
                "analysis_item_2": "B",
                "analysis_item_3": "C",
            },
            ensure_ascii=False,
        ),
        enabled=True,
    )

    import host_brain.app.dependencies as deps

    monkeypatch.setattr(deps, "_ledger", ledger, raising=False)
    monkeypatch.setattr(deps, "_prompt_template_store", store, raising=False)

    prompt = _build_emotion_agent_system_prompt(
        current_emotion_tag="CALM",
        mood_state="steady",
        emotion_echo={"echo_style": "warm"},
        user_profile={"profile_hint": "sensitive"},
    )

    assert "custom role" in prompt
    assert "tag=CALM" in prompt
    assert "mood=steady" in prompt
    assert "echo=warm" in prompt
    assert "profile=sensitive" in prompt
    assert "tools:" in prompt
    assert "- history tool" in prompt
    assert "- timeline tool" in prompt
    assert "- knowledge tool" in prompt
    assert "deliver:" in prompt
    assert "\nA" in prompt
    assert "\nB" in prompt
    assert "\nC" in prompt


def test_builtin_emotion_agent_prompt_templates_are_clean() -> None:
    templates = resolve_prompt_template_json_merged("emotion_agent_handler.prompt_templates")
    assert templates["system_role_line"] == "你是情感分析专家，负责深度分析用户的情感状态。"
    assert templates["tool_emotion_history_search"] == "- emotion_history_search: 检索用户的历史情感记忆"
    assert templates["analysis_item_3"] == "3. 分析推理过程"
