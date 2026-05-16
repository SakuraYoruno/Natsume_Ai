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

    assert "温和地共情" in hint
    assert "自然的追问" in hint
    assert "句子短一点" in hint

    distant = adapter._build_style_hint(
        PersonalityTraits(
            warmth=0.2,
            playfulness=0.2,
            assertiveness=0.2,
        ),
        "NEUTRAL",
        "FRIEND",
        ReactivityModifiers(),
        openness=0.3,
        pace=PACE_NORMAL,
    )
    assert "自然、克制" in distant
    assert "保持距离感" in distant


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
    assert "语气温和、可靠" in bright
    assert "轻一点、活一点" in bright
    assert "态度稳" in bright
    assert "把愉快和偏心稍微放出来一点" in bright
    assert "别变成撒娇腔" in bright

    deep = adapter._build_style_hint(
        PersonalityTraits(sensitivity=0.8),
        "NEUTRAL",
        "FRIEND",
        ReactivityModifiers(),
        openness=0.5,
        pace=PACE_DEEP,
    )
    assert "放慢一些" in deep
    assert "认真听" in deep


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
    assert "适度贴着对方的情绪节奏" in rapid
    assert "有需要就顺着追问" in rapid
    assert "对方说得快" in rapid

    deep = adapter.build_prompt_section(
        ExpressionHint(
            style_hint="认真倾听",
            tone="serious",
            echo_weight_modifier=-0.1,
            追问倾向=0.6,
            pace_hint=PACE_DEEP,
        )
    )
    assert "保持稳，不被情绪一下子卷走" in deep
    assert "可以再问一句" in deep
    assert "对方在认真说" in deep


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


def test_build_prompt_section_uses_japanese_templates() -> None:
    adapter = PersonaExpressionAdapter()
    section = adapter.build_prompt_section(
        ExpressionHint(
            style_hint="静かに受け止める",
            tone="gentle",
            echo_weight_modifier=0.06,
            追问倾向=0.3,
            pace_hint=PACE_RAPID,
        ),
        render_target_lang="ja",
    )

    assert "【" in section and "方針" in section
    assert "静かに受け止める" in section
    assert "情緒" in section or "受け止め" in section
    assert "你" not in section
    assert "我" not in section
