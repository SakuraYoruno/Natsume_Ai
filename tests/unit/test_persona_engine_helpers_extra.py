from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from host_brain.persona import self_model as self_model_mod
from host_brain.persona.persona_engine import (
    PersonaEngine,
    SelfModelSnapshot,
    TraitChangeRecord,
)
from host_brain.persona.personality_traits import DEFAULT_BASELINE, PersonalityTraits, TraitsDelta


@pytest.fixture(autouse=True)
def _reset_engine() -> None:
    yield
    PersonaEngine.reset_instance()


def test_trait_change_record_as_dict_preserves_audit_payload() -> None:
    record = TraitChangeRecord(
        user_id="u1",
        delta={"warmth": 0.02},
        before={"warmth": 0.8},
        after={"warmth": 0.82},
        reason="growth:test",
        ts_ms=123,
    )

    assert record.as_dict() == {
        "user_id": "u1",
        "delta": {"warmth": 0.02},
        "before": {"warmth": 0.8},
        "after": {"warmth": 0.82},
        "reason": "growth:test",
        "ts_ms": 123,
    }


def test_engine_loads_default_baseline_when_canon_missing_field(tmp_path: Path) -> None:
    canon_path = tmp_path / "persona.json"
    canon_path.write_text('{"version": 1}', encoding="utf-8")

    engine = PersonaEngine(canon_path=canon_path)

    assert engine.baseline.as_dict() == DEFAULT_BASELINE


def test_engine_loads_default_baseline_when_canon_is_invalid_json(tmp_path: Path) -> None:
    canon_path = tmp_path / "persona.json"
    canon_path.write_text("{broken", encoding="utf-8")

    engine = PersonaEngine(canon_path=canon_path)

    assert engine.baseline.as_dict() == DEFAULT_BASELINE


def test_get_expression_adapter_handles_object_negative_emotion_branch() -> None:
    engine = PersonaEngine()
    traits = PersonalityTraits(
        warmth=0.8,
        curiosity=0.7,
        humor=0.1,
        sensitivity=0.8,
        assertiveness=0.2,
        playfulness=0.1,
        resilience=0.2,
    )

    hint = engine.get_expression_adapter(
        traits,
        SimpleNamespace(label="sad", valence=-0.6, arousal=0.2),
        "CONFIDANT",
    )

    assert hint.style_hint == "深度共情（情绪偏低）"
    assert hint.tone == "deeply_caring_subdued"
    assert hint.echo_weight_modifier == pytest.approx(0.18, abs=1e-6)
    assert hint.追问倾向 == pytest.approx(0.095, abs=1e-6)


def test_get_expression_adapter_handles_tender_and_bright_dict_branches() -> None:
    engine = PersonaEngine()

    tender_traits = PersonalityTraits(
        warmth=0.7,
        curiosity=0.1,
        humor=0.1,
        sensitivity=0.2,
        assertiveness=0.1,
        playfulness=0.7,
        resilience=0.1,
    )
    tender_hint = engine.get_expression_adapter(
        tender_traits,
        {"emotion": "LOVING", "valence": 0.2, "arousal": 0.4},
        "CLOSE_FRIEND",
    )
    assert tender_hint.style_hint == "温柔俏皮（温柔质感）"
    assert tender_hint.tone == "warm_playful_tender"

    bright_traits = PersonalityTraits(
        warmth=0.2,
        curiosity=0.7,
        humor=0.1,
        sensitivity=0.6,
        assertiveness=0.7,
        playfulness=0.1,
        resilience=0.1,
    )
    bright_hint = engine.get_expression_adapter(
        bright_traits,
        {"label": "EXCITED", "valence": 0.8, "arousal": 0.9},
        "SOULMATE",
    )
    assert bright_hint.style_hint == "主动探索（活泼状态）"
    assert bright_hint.tone == "inquisitive_bright"
    assert bright_hint.echo_weight_modifier == pytest.approx(0.13, abs=1e-6)
    assert bright_hint.追问倾向 == pytest.approx(0.1, abs=1e-6)


def test_get_expression_adapter_handles_mid_openness_bright_and_subdued() -> None:
    engine = PersonaEngine()
    traits = PersonalityTraits(
        warmth=0.2,
        curiosity=0.5,
        humor=0.1,
        sensitivity=0.5,
        assertiveness=0.65,
        playfulness=0.2,
        resilience=0.1,
    )

    bright = engine.get_expression_adapter(
        traits,
        {"label": "HAPPY", "valence": 0.7, "arousal": 0.85},
        "CLOSE_FRIEND",
    )
    subdued = engine.get_expression_adapter(
        traits,
        {"label": "SAD", "valence": -0.6, "arousal": 0.2},
        "CLOSE_FRIEND",
    )

    assert bright.style_hint == "自信坚定"
    assert bright.tone == "serious_bright"
    assert bright.echo_weight_modifier == pytest.approx(0.05, abs=1e-6)
    assert bright.追问倾向 == pytest.approx(0.025, abs=1e-6)
    assert subdued.tone == "serious_subdued"


def test_get_self_model_snapshot_uses_self_model_and_filters_unknown_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = PersonaEngine()

    def _build_snapshot(self, **kwargs):  # noqa: ANN001
        return {
            "recent_mood_trend": "偏好",
            "emotional_stability": 0.3333,
            "dominant_recent_emotion": "HAPPY",
            "bond_growth_perception": "稳定",
            "shared_memory_count": 2,
            "last_deep_talk_days_ago": 1,
            "personality_changes": ["更柔和了"],
            "growth_milestones": ["已经陪伴很久了"],
            "ignored_field": "should_not_leak",
        }

    monkeypatch.setattr(self_model_mod.SelfModel, "build_snapshot", _build_snapshot)

    snap = engine.get_self_model_snapshot("snap_user")

    assert isinstance(snap, SelfModelSnapshot)
    assert snap.recent_mood_trend == "偏好"
    assert snap.growth_milestones == ["已经陪伴很久了"]
    assert "ignored_field" not in snap.as_dict()


def test_get_self_model_snapshot_falls_back_to_phase_one_when_self_model_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = PersonaEngine()
    engine.apply_growth_delta(
        "fallback_user",
        TraitsDelta(warmth=-0.02, humor=0.03),
        reason="fallback",
    )

    def _raise(self, **kwargs):  # noqa: ANN001
        raise RuntimeError("self model unavailable")

    monkeypatch.setattr(self_model_mod.SelfModel, "build_snapshot", _raise)

    snap = engine.get_self_model_snapshot("fallback_user")

    assert isinstance(snap, SelfModelSnapshot)
    assert "温柔度有所降低" in snap.personality_changes
    assert "幽默感有所提升" in snap.personality_changes
