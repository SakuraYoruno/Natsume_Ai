from __future__ import annotations

import datetime

import pytest

import host_brain.persona.evolution_desire_engine as ede
from host_brain.persona.evolution_desire_engine import EvolutionDesireEngine
from host_brain.persona.personality_traits import PersonalityTraits


def test_generate_desire_returns_empty_when_all_candidate_sources_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = EvolutionDesireEngine(seed=0)
    monkeypatch.setattr(engine, "_generate_from_time_context", lambda **kwargs: [])
    monkeypatch.setattr(engine, "_generate_from_event_count", lambda event_count: [])

    desire = engine.generate_desire(
        traits=None,
        emotion_trend="UNKNOWN",
        bond_stage="UNKNOWN",
        event_count=0,
        current_ts_ms=1,
    )

    assert desire == ""


def test_generate_desire_uses_preference_branch_and_cooldown_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = EvolutionDesireEngine(seed=0)

    monkeypatch.setattr(engine, "_try_fulfilled_text", lambda recent_trait_deltas, ts: "")
    monkeypatch.setattr(engine, "_try_user_preference_desire", lambda hints, ts: "pref")
    assert engine.generate_desire(current_ts_ms=1, user_preference_hints={"warmth": 0.9}) == "pref"

    engine = EvolutionDesireEngine(seed=0)
    monkeypatch.setattr(engine, "_try_fulfilled_text", lambda recent_trait_deltas, ts: "")
    monkeypatch.setattr(engine, "_try_user_preference_desire", lambda hints, ts: "")
    monkeypatch.setattr(engine, "_generate_from_time_context", lambda **kwargs: [])
    monkeypatch.setattr(engine, "_generate_from_event_count", lambda event_count: [])
    monkeypatch.setattr(engine, "_filter_cooled_down", lambda candidates, ts: [])
    monkeypatch.setattr(engine._rng, "choice", lambda seq: seq[-1])

    desire = engine.generate_desire(
        traits=PersonalityTraits(warmth=0.2, curiosity=0.9, humor=0.3, sensitivity=0.7),
        emotion_trend="POSITIVE",
        bond_stage="FRIEND",
        current_ts_ms=2,
    )

    assert desire in engine._used_ts
    assert engine._used_ts[desire] == 2


def test_try_fulfilled_text_covers_skip_and_success(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = EvolutionDesireEngine(seed=0)
    monkeypatch.setattr(engine._rng, "choice", lambda seq: seq[0])

    assert engine._try_fulfilled_text(None, 10) == ""
    assert engine._try_fulfilled_text({"unknown": 0.1}, 10) == ""

    first_text = ede._DESIRE_FULFILLED_TEXTS["warmth"][0]
    engine._used_ts[first_text] = 100
    assert engine._try_fulfilled_text({"warmth": 0.03}, 100) == ""

    monkeypatch.setattr(engine._rng, "random", lambda: 0.9)
    assert engine._try_fulfilled_text({"warmth": 0.03}, ede._DESIRE_COOLDOWN_MS + 100) == ""

    monkeypatch.setattr(engine._rng, "random", lambda: 0.0)
    text = engine._try_fulfilled_text({"warmth": 0.03}, ede._DESIRE_COOLDOWN_MS + 200)
    assert text == first_text
    assert engine._used_ts[text] == ede._DESIRE_COOLDOWN_MS + 200


def test_generate_from_traits_covers_all_levels_and_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = EvolutionDesireEngine(seed=0)

    low_medium = engine._generate_from_traits(
        PersonalityTraits(
            warmth=0.2,
            curiosity=0.5,
            humor=0.9,
            sensitivity=0.9,
            assertiveness=0.9,
            playfulness=0.9,
            resilience=0.9,
        )
    )
    warmth_low = {text for level, text in ede._DESIRE_TEMPLATES["warmth"] if level == "low"}
    curiosity_medium = {
        text for level, text in ede._DESIRE_TEMPLATES["curiosity"] if level == "medium"
    }
    assert warmth_low & set(low_medium)
    assert curiosity_medium & set(low_medium)

    high = engine._generate_from_traits(
        PersonalityTraits(
            warmth=0.9,
            curiosity=0.9,
            humor=0.2,
            sensitivity=0.9,
            assertiveness=0.9,
            playfulness=0.9,
            resilience=0.9,
        )
    )
    warmth_high = {text for level, text in ede._DESIRE_TEMPLATES["warmth"] if level == "high"}
    humor_low = {text for level, text in ede._DESIRE_TEMPLATES["humor"] if level == "low"}
    assert warmth_high & set(high)
    assert humor_low & set(high)

    monkeypatch.setitem(ede._DESIRE_TEMPLATES, "warmth", [])
    fallback = engine._generate_from_traits(
        PersonalityTraits(
            warmth=0.2,
            curiosity=0.2,
            humor=0.9,
            sensitivity=0.9,
            assertiveness=0.9,
            playfulness=0.9,
            resilience=0.9,
        )
    )
    assert fallback


def test_generate_from_time_context_covers_reunion_weekend_hours_and_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = EvolutionDesireEngine(seed=0)
    one_day_ms = 24 * 3600 * 1000

    assert engine._generate_from_time_context(ts=8 * one_day_ms, last_deep_talk_ts_ms=0, is_weekend=True) == list(
        ede._TIME_DESIRE_TEMPLATES["周末"]
    )
    assert engine._generate_from_time_context(ts=8 * one_day_ms, last_deep_talk_ts_ms=0, is_weekend=False) == list(
        ede._TIME_DESIRE_TEMPLATES["白天"]
    )

    class _HourDateTime(datetime.datetime):
        @classmethod
        def fromtimestamp(cls, ts, tz=None):  # noqa: ANN001
            return cls(2024, 1, 1, int(ts), 0, 0)

    monkeypatch.setattr(datetime, "datetime", _HourDateTime)
    assert engine._generate_from_time_context(ts=20_000, last_deep_talk_ts_ms=0, is_weekend=False) == list(
        ede._TIME_DESIRE_TEMPLATES["居家夜晚"]
    )
    assert engine._generate_from_time_context(ts=23_000, last_deep_talk_ts_ms=0, is_weekend=False) == list(
        ede._TIME_DESIRE_TEMPLATES["深夜"]
    )
    assert engine._generate_from_time_context(ts=2_000, last_deep_talk_ts_ms=0, is_weekend=False) == list(
        ede._TIME_DESIRE_TEMPLATES["凌晨"]
    )

    assert engine._generate_from_time_context(
        ts=6 * one_day_ms,
        last_deep_talk_ts_ms=2 * one_day_ms,
        is_weekend=False,
    ) == []
    assert engine._generate_from_time_context(
        ts=10 * one_day_ms,
        last_deep_talk_ts_ms=one_day_ms,
        is_weekend=False,
    ) == list(ede._TIME_DESIRE_TEMPLATES["久别重逢"])

    class _BrokenDateTime(datetime.datetime):
        @classmethod
        def fromtimestamp(cls, ts, tz=None):  # noqa: ANN001
            raise ValueError("bad clock")

    monkeypatch.setattr(datetime, "datetime", _BrokenDateTime)
    assert engine._generate_from_time_context(ts=123, last_deep_talk_ts_ms=0, is_weekend=False) == []


def test_generate_from_event_count_preference_and_cooldown_helpers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = EvolutionDesireEngine(seed=0)
    monkeypatch.setattr(engine._rng, "choice", lambda seq: seq[0])

    assert engine._generate_from_event_count(0) == []
    assert engine._generate_from_event_count(15) == list(ede._EVENT_COUNT_DESIRE_TEMPLATES[1][2])

    assert engine._try_user_preference_desire(None, 1) == ""
    assert engine._try_user_preference_desire({"warmth": 0.5}, 1) == ""

    pref_text = ede._USER_PREFERENCE_DESIRE_TEMPLATES["warmth"][0]
    engine._used_ts[pref_text] = 10
    assert engine._try_user_preference_desire({"warmth": 0.9}, 10) == ""

    monkeypatch.setattr(engine._rng, "random", lambda: 0.95)
    assert engine._try_user_preference_desire({"warmth": 1.0}, ede._DESIRE_COOLDOWN_MS + 10) == ""

    monkeypatch.setattr(engine._rng, "random", lambda: 0.0)
    text = engine._try_user_preference_desire({"warmth": 1.0}, ede._DESIRE_COOLDOWN_MS + 20)
    assert text == pref_text
    assert engine._used_ts[text] == ede._DESIRE_COOLDOWN_MS + 20

    assert engine._filter_cooled_down([pref_text, "fresh"], ede._DESIRE_COOLDOWN_MS + 20) == ["fresh"]
    stats = engine.get_cooldown_stats()
    assert stats["total_tracked"] >= 1
    engine.clear_cooldowns()
    assert engine.get_cooldown_stats()["total_tracked"] == 0
