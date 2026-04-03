from __future__ import annotations

from dataclasses import dataclass

import host_brain.persona.self_model as sm


@dataclass
class _Record:
    delta: dict[str, float]


@dataclass
class _BondState:
    current_stage: str = "FRIEND"
    event_count: int = 0


def test_compute_emotional_stability_accounts_for_flip_count_and_negative_duration() -> None:
    model = sm.SelfModel()

    stability = model._compute_emotional_stability(
        {"dominant_emotion": "SAD"},
        {
            "emotion_arc": {"start": "HAPPY", "end": "SAD"},
            "emotion_sequence": ["HAPPY", "SAD", "HAPPY", "ANXIOUS"],
            "dominant_emotion": "SAD",
            "dominant_duration_ratio": 0.9,
        },
    )

    assert 0.0 <= stability < 0.5


def test_extract_personality_changes_and_growth_milestones_cover_negative_and_large_counts() -> None:
    model = sm.SelfModel()
    changes = [
        _Record({"warmth": 0.03}),
        _Record({"humor": -0.02}),
        _Record({"resilience": 0.01}),
    ]

    extracted = model._extract_personality_changes(changes)
    milestones = model._extract_growth_milestones(
        _BondState(current_stage="SOULMATE", event_count=120),
        changes,
    )

    assert any("收敛" in text for text in extracted)
    assert any("120 次互动" in text for text in milestones)
    assert any("人格在慢慢成长中" in text for text in milestones)


def test_detect_contradictions_covers_low_mood_and_growth_paths(monkeypatch) -> None:
    monkeypatch.setattr(sm.random, "choice", lambda pool: pool[0])

    contradictions = sm.detect_contradictions(
        {
            "recent_mood_trend": "偏低",
            "bond_growth_perception": "变亲近",
            "emotional_stability": 0.6,
            "personality_changes": ["变得更温柔了"],
        }
    )

    assert [item["type"] for item in contradictions] == [
        "mood_low_bond_up",
        "growing_but_sad",
    ]


def test_detect_contradictions_covers_bond_down_paths(monkeypatch) -> None:
    monkeypatch.setattr(sm.random, "choice", lambda pool: pool[0])

    contradictions = sm.detect_contradictions(
        {
            "recent_mood_trend": "偏好",
            "bond_growth_perception": "有些疏远",
            "emotional_stability": 0.9,
            "personality_changes": ["变得更温柔了"],
        }
    )

    assert [item["type"] for item in contradictions] == [
        "mood_up_bond_down",
        "stable_but_bond_down",
    ]


def test_detect_contradictions_covers_volatile_but_bond_stable(monkeypatch) -> None:
    monkeypatch.setattr(sm.random, "choice", lambda pool: pool[0])

    contradictions = sm.detect_contradictions(
        {
            "recent_mood_trend": "平稳",
            "bond_growth_perception": "稳定",
            "emotional_stability": 0.2,
            "personality_changes": [],
        }
    )

    assert contradictions[0]["type"] == "volatile_but_bond_stable"


def test_contradiction_text_and_pool_helpers_cover_empty_paths() -> None:
    assert sm.build_contradiction_awareness_text([{}]) == ""
    assert sm.build_contradiction_awareness_text(
        [{"text": "a"}, {"text": "b"}]
    ) == "a。b"
    assert sm._pick_from_pool([]) == ""


def test_build_self_awareness_text_prefers_time_context(monkeypatch) -> None:
    monkeypatch.setattr(sm.random, "random", lambda: 0.1)
    monkeypatch.setattr(sm.random, "choice", lambda pool: pool[0])

    text = sm.build_self_awareness_text(
        {
            "recent_mood_trend": "偏好",
            "bond_growth_perception": "变亲近",
            "event_count": 99,
            "growth_milestones": ["不应走到这里"],
        },
        time_context="久别重逢",
    )

    assert "好久没见了" in text


def test_build_mood_awareness_text_uses_normal_pool(monkeypatch) -> None:
    monkeypatch.setattr(sm.random, "choice", lambda pool: pool[0])

    text = sm.build_mood_awareness_text(
        {
            "dominant_recent_emotion": "THINKING",
            "emotional_stability": 0.5,
        }
    )

    assert "思考" in text

