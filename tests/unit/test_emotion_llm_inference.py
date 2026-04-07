from __future__ import annotations

import json

import pytest

from host_brain.app.emotion_llm_inference import (
    _build_inference_prompt,
    infer_emotion_via_llm,
)
from host_brain.app.prompt_template_store import (
    PromptTemplateStore,
    resolve_prompt_template_json_merged,
)
from host_brain.audit.sqlite_ledger import SQLiteLedger


def test_build_inference_prompt_uses_template_override(monkeypatch) -> None:
    ledger = SQLiteLedger(":memory:", check_same_thread=False)
    store = PromptTemplateStore(ledger=ledger, cache_ttl_ms=0)
    store.upsert_template(
        "emotion_llm_inference.prompt_templates",
        content=json.dumps(
            {
                "mood_state_line": "mood={mood_state}",
                "emotion_tag_line": "tag={current_emotion_tag}",
                "recent_conversation_header": "history:",
                "recent_conversation_item": "* {line}",
                "latest_user_message_line": 'latest="{user_message}"',
            },
            ensure_ascii=False,
        ),
        enabled=True,
    )

    import host_brain.app.dependencies as deps

    monkeypatch.setattr(deps, "_ledger", ledger, raising=False)
    monkeypatch.setattr(deps, "_prompt_template_store", store, raising=False)

    prompt = _build_inference_prompt(
        user_message="hello",
        conversation_history=["u1", "a1", "u2", "a2", "u3", "a3", "u4"],
        current_emotion_tag="CALM",
        mood_state="soft",
    )

    assert "mood=soft" in prompt
    assert "tag=CALM" in prompt
    assert "\nhistory:" in prompt
    assert "* u1" not in prompt
    assert "* a2" in prompt
    assert "* u4" in prompt
    assert 'latest="hello"' in prompt


@pytest.mark.asyncio
async def test_infer_emotion_via_llm_uses_system_prompt_template(monkeypatch) -> None:
    ledger = SQLiteLedger(":memory:", check_same_thread=False)
    store = PromptTemplateStore(ledger=ledger, cache_ttl_ms=0)
    store.upsert_template(
        "emotion_llm_inference.prompt_templates",
        content=json.dumps(
            {
                "system_prompt": "custom emotion system",
                "mood_state_line": "state={mood_state}",
                "emotion_tag_line": "emotion={current_emotion_tag}",
                "latest_user_message_line": "user={user_message}",
            },
            ensure_ascii=False,
        ),
        enabled=True,
    )

    import host_brain.app.dependencies as deps

    monkeypatch.setattr(deps, "_ledger", ledger, raising=False)
    monkeypatch.setattr(deps, "_prompt_template_store", store, raising=False)

    captured: dict[str, object] = {}

    def _fake_sync_llm_emotion_call(**kwargs):
        captured.update(kwargs)
        return json.dumps(
            {
                "user_emotion": "CALM",
                "natsume_emotion": "CALM",
                "reason": "ok",
                "confidence": 0.88,
            },
            ensure_ascii=False,
        )

    monkeypatch.setattr(
        "host_brain.app.emotion_llm_inference._sync_llm_emotion_call",
        _fake_sync_llm_emotion_call,
    )

    result = await infer_emotion_via_llm(
        user_message="test",
        conversation_history=["u", "a"],
        current_emotion_tag="NEUTRAL",
        mood_state="steady",
        timeout_ms=200,
    )

    assert result.is_valid is True
    assert captured["system_prompt"] == "custom emotion system"
    assert "state=steady" in str(captured["user_prompt"])
    assert "emotion=NEUTRAL" in str(captured["user_prompt"])
    assert "user=test" in str(captured["user_prompt"])


def test_builtin_emotion_prompt_templates_are_clean() -> None:
    templates = resolve_prompt_template_json_merged("emotion_llm_inference.prompt_templates")

    assert isinstance(templates, dict)
    assert str(templates.get("system_prompt") or "").startswith("你是情感分析专家")
    assert templates.get("mood_state_line") == "当前心情状态: {mood_state}"
    assert templates.get("latest_user_message_line") == '用户最新发言: "{user_message}"'
