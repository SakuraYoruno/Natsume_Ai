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
