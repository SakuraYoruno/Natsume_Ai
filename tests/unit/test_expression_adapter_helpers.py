from __future__ import annotations

import json

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.app.prompt_template_store import PromptTemplateStore
from host_brain.persona.emotion_reactivity import ReactivityModifiers
from host_brain.persona.expression_adapter import (
    PACE_DEEP,
    PACE_NORMAL,
    PACE_RAPID,
    PersonaExpressionAdapter,
)
from host_brain.persona.personality_traits import ExpressionHint, PersonalityTraits


def test_adapt_reduces_follow_up_on_deep_conversation() -> None:
    adapter = PersonaExpressionAdapter()
    traits = PersonalityTraits(curiosity=0.9, sensitivity=0.8)

    hint = adapter.adapt(
        traits,
        None,
        "FRIEND",
        user_emotion_tag="EXCITED",
        user_message="这是一段" * 30,
        recent_user_msg_lengths=[90, 110, 120],
    )

    assert hint.pace_hint == PACE_DEEP
    assert hint.追问倾向 < 0.5


def test_infer_conversation_pace_covers_boundary_and_trend_cases() -> None:
    adapter = PersonaExpressionAdapter()

    assert adapter._infer_conversation_pace("hi") == PACE_RAPID
    assert adapter._infer_conversation_pace("x" * 40) == PACE_NORMAL
    assert adapter._infer_conversation_pace("x" * 90) == PACE_DEEP
    assert adapter._infer_conversation_pace("x" * 25, [10, 12, 14]) == PACE_RAPID
    assert adapter._infer_conversation_pace("x" * 60, [55, 60, 70]) == PACE_DEEP
    assert adapter._infer_conversation_pace("x" * 45, [5, 25, 55]) == PACE_DEEP


def test_build_style_hint_covers_empathy_distance_and_rapid_pass_branch() -> None:
    adapter = PersonaExpressionAdapter()

    hint = adapter._build_style_hint(
        PersonalityTraits(playfulness=0.7),
        "NEUTRAL",
        "FRIEND",
        ReactivityModifiers(empathy_boost=0.25, curiosity_follow_up=0.3),
        openness=0.5,
        pace=PACE_RAPID,
    )

    assert "温柔共情" in hint
    assert "保持好奇心" in hint
    assert "回复简洁利落" in hint

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
    assert "自然温和" in distant
    assert "注意保持适当距离" in distant


def test_build_style_hint_covers_trait_and_deep_branches() -> None:
    adapter = PersonaExpressionAdapter()

    bright = adapter._build_style_hint(
        PersonalityTraits(warmth=0.8, playfulness=0.7, assertiveness=0.7),
        "HAPPY",
        "SOULMATE",
        ReactivityModifiers(),
        openness=0.85,
        pace=PACE_NORMAL,
    )
    assert "温柔真诚" in bright
    assert "可以适当俏皮" in bright
    assert "语气自信" in bright
    assert "可以更自在地表达喜悦" in bright
    assert "偶尔撒个小娇也没关系" in bright

    deep = adapter._build_style_hint(
        PersonalityTraits(sensitivity=0.8),
        "NEUTRAL",
        "FRIEND",
        ReactivityModifiers(),
        openness=0.5,
        pace=PACE_DEEP,
    )
    assert "认真倾听" in deep
    assert "细微情感" in deep


def test_determine_tone_covers_remaining_priority_branches() -> None:
    adapter = PersonaExpressionAdapter()

    assert adapter._determine_tone(
        PersonalityTraits(),
        "BORED",
        ReactivityModifiers(humor_deflection=0.4),
        0.5,
    ) == "playful"
    assert adapter._determine_tone(
        PersonalityTraits(playfulness=0.7),
        "HAPPY",
        ReactivityModifiers(),
        0.7,
        pace=PACE_DEEP,
    ) == "gentle"
    assert adapter._determine_tone(
        PersonalityTraits(warmth=0.8),
        "SAD",
        ReactivityModifiers(),
        0.5,
    ) == "caring"
    assert adapter._determine_tone(
        PersonalityTraits(sensitivity=0.8),
        "NEUTRAL",
        ReactivityModifiers(),
        0.5,
        pace=PACE_DEEP,
    ) == "caring"
    assert adapter._determine_tone(
        PersonalityTraits(playfulness=0.8),
        "NEUTRAL",
        ReactivityModifiers(),
        0.7,
        pace=PACE_RAPID,
    ) == "playful"
    assert adapter._determine_tone(
        PersonalityTraits(assertiveness=0.8),
        "NEUTRAL",
        ReactivityModifiers(),
        0.5,
    ) == "serious"


def test_build_prompt_section_describes_mid_negative_and_pace_variants() -> None:
    adapter = PersonaExpressionAdapter()

    rapid = adapter.build_prompt_section(
        ExpressionHint(
            style_hint="自然温和",
            tone="gentle",
            echo_weight_modifier=0.06,
            追问倾向=0.3,
            pace_hint=PACE_RAPID,
        )
    )
    assert "适当跟随他的情绪节奏" in rapid
    assert "如果话题有趣，可以顺势追问" in rapid
    assert "对方在快节奏聊天" in rapid

    deep = adapter.build_prompt_section(
        ExpressionHint(
            style_hint="认真倾听",
            tone="serious",
            echo_weight_modifier=-0.1,
            追问倾向=0.6,
            pace_hint=PACE_DEEP,
        )
    )
    assert "不过度卷入他的情绪波动" in deep
    assert "可以自然地多问一句" in deep
    assert "对方在认真倾诉或深度讨论" in deep


def test_build_prompt_section_uses_prompt_template_override(monkeypatch) -> None:
    ledger = SQLiteLedger(":memory:", check_same_thread=False)
    store = PromptTemplateStore(ledger=ledger, cache_ttl_ms=0)
    store.upsert_template(
        "expression_adapter.prompt_section.templates",
        content=json.dumps(
            {
                "title": "【表达卡】\n",
                "style_label": "- 策略：{value}\n",
                "tone_label": "- 口吻：{value}\n",
                "echo_label": "- 共鸣：{value}\n",
                "follow_up_label": "- 追问：{value}\n",
                "pace_label": "- 节奏：{value}\n\n",
                "tone_guidance": {
                    "gentle": "轻一点，别太硬。",
                },
                "echo_mid": "顺着他的情绪一点点走",
                "follow_up_mid": "有空就顺手多问一句",
                "pace_rapid": "现在节奏快，短一点",
            },
            ensure_ascii=False,
        ),
        enabled=True,
    )

    import host_brain.app.dependencies as deps

    monkeypatch.setattr(deps, "_ledger", ledger, raising=False)
    monkeypatch.setattr(deps, "_prompt_template_store", store, raising=False)

    adapter = PersonaExpressionAdapter()
    section = adapter.build_prompt_section(
        ExpressionHint(
            style_hint="自然温和",
            tone="gentle",
            echo_weight_modifier=0.06,
            追问倾向=0.3,
            pace_hint=PACE_RAPID,
        )
    )

    assert "【表达卡】" in section
    assert "- 策略：自然温和" in section
    assert "- 口吻：轻一点，别太硬。" in section
    assert "- 共鸣：顺着他的情绪一点点走" in section
    assert "- 追问：有空就顺手多问一句" in section
    assert "- 节奏：现在节奏快，短一点" in section
