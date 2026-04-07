from __future__ import annotations

import json

from host_brain.app.emotion_context import UserEmotionProfile, _vector_to_inner_monologue
from host_brain.app.prompt_template_store import PromptTemplateStore, resolve_prompt_template_json_merged
from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.metabolism import EmotionVector


def _install_store(monkeypatch, template_id: str, content: dict) -> PromptTemplateStore:
    ledger = SQLiteLedger(":memory:", check_same_thread=False)
    store = PromptTemplateStore(ledger=ledger, cache_ttl_ms=0)
    store.upsert_template(
        template_id,
        content=json.dumps(content, ensure_ascii=False),
        enabled=True,
    )

    import host_brain.app.dependencies as deps

    monkeypatch.setattr(deps, "_ledger", ledger, raising=False)
    monkeypatch.setattr(deps, "_prompt_template_store", store, raising=False)
    return store


def test_user_emotion_profile_hint_uses_template_override(monkeypatch) -> None:
    _install_store(
        monkeypatch,
        "emotion_context.user_profile_hints",
        {
            "tone": {
                "positive": "custom-positive",
            },
            "emotion_frequency": {
                "dominant": "dominant-{emotion}",
            },
            "volatility": {
                "low": "custom-stable",
            },
            "output": {
                "joiner": " | ",
                "suffix": "!",
            },
        },
    )

    profile = UserEmotionProfile()
    profile.update(emotion_tag="HAPPY", valence=0.8)
    profile.update(emotion_tag="HAPPY", valence=0.9)
    profile.update(emotion_tag="HAPPY", valence=0.85)

    summary = profile.to_summary()

    assert summary["profile_hint"] == "custom-positive | dominant-开心 | custom-stable!"


def test_vector_to_inner_monologue_uses_template_override(monkeypatch) -> None:
    _install_store(
        monkeypatch,
        "emotion_context.inner_monologue_texts",
        {
            "cases": {
                "high_positive_high_arousal": "custom-excited",
                "low_dominance": "custom-soft",
            },
            "default": "custom-default",
        },
    )

    assert _vector_to_inner_monologue(EmotionVector(valence=0.8, arousal=0.8, dominance=0.0)) == "custom-excited"
    assert _vector_to_inner_monologue(EmotionVector(valence=0.0, arousal=0.0, dominance=-0.5)) == "custom-soft"
    assert _vector_to_inner_monologue(EmotionVector(valence=0.1, arousal=0.1, dominance=0.0)) == "custom-default"


def test_builtin_emotion_context_inner_monologue_templates_are_clean() -> None:
    templates = resolve_prompt_template_json_merged("emotion_context.inner_monologue_texts")
    assert templates["cases"]["high_positive_high_arousal"] == "现在心里特别开心，有种想要分享一切的冲动"
    assert templates["cases"]["low_dominance"] == "心态比较柔软，愿意倾听和配合对方"
    assert templates["default"] == "状态还不错，心里很平稳，可以自然地聊下去"
