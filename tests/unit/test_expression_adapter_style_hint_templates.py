from __future__ import annotations

import json

from host_brain.app.prompt_template_store import PromptTemplateStore, resolve_prompt_template_json_merged
from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.persona.emotion_reactivity import ReactivityModifiers
from host_brain.persona.expression_adapter import (
    PACE_DEEP,
    PACE_NORMAL,
    PACE_RAPID,
    PersonaExpressionAdapter,
)
from host_brain.persona.personality_traits import PersonalityTraits


def test_build_style_hint_uses_template_override(monkeypatch) -> None:
    ledger = SQLiteLedger(":memory:", check_same_thread=False)
    store = PromptTemplateStore(ledger=ledger, cache_ttl_ms=0)
    store.upsert_template(
        "expression_adapter.style_hint.templates",
        content=json.dumps(
            {
                "core": {
                    "empathy_high_reserved": "custom-empathy",
                    "curiosity_follow_up": "custom-curiosity",
                },
                "traits": {
                    "default": "custom-base",
                },
                "bond": {
                    "distance": "custom-distance",
                },
                "pace": {
                    "rapid": "custom-rapid",
                    "deep": "custom-deep",
                    "deep_sensitive": "custom-deep-sensitive",
                },
            },
            ensure_ascii=False,
        ),
        enabled=True,
    )

    import host_brain.app.dependencies as deps

    monkeypatch.setattr(deps, "_ledger", ledger, raising=False)
    monkeypatch.setattr(deps, "_prompt_template_store", store, raising=False)

    adapter = PersonaExpressionAdapter()

    rapid = adapter._build_style_hint(
        PersonalityTraits(playfulness=0.7),
        "NEUTRAL",
        "FRIEND",
        ReactivityModifiers(empathy_boost=0.25, curiosity_follow_up=0.3),
        openness=0.5,
        pace=PACE_RAPID,
    )
    assert "custom-empathy" in rapid
    assert "custom-curiosity" in rapid
    assert "custom-rapid" in rapid

    distant = adapter._build_style_hint(
        PersonalityTraits(
            warmth=0.2,
            playfulness=0.2,
            assertiveness=0.2,
        ),
        "NEUTRAL",
        "STRANGER",
        ReactivityModifiers(),
        openness=0.3,
        pace=PACE_NORMAL,
    )
    assert "custom-base" in distant
    assert "custom-distance" in distant

    deep = adapter._build_style_hint(
        PersonalityTraits(sensitivity=0.8),
        "NEUTRAL",
        "FRIEND",
        ReactivityModifiers(),
        openness=0.5,
        pace=PACE_DEEP,
    )
    assert "custom-deep" in deep
    assert "custom-deep-sensitive" in deep


def test_builtin_expression_adapter_style_templates_are_clean() -> None:
    templates = resolve_prompt_template_json_merged("expression_adapter.style_hint.templates")
    assert templates["core"]["empathy_high_reserved"] == "温柔共情，表达关心但保持分寸"
    assert templates["traits"]["default"] == "自然温和"
    assert templates["pace"]["rapid"] == "回复简洁利落，跟上对方的节奏"
