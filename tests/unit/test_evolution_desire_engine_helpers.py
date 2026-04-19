from __future__ import annotations

import datetime
import json

import pytest

import host_brain.persona.evolution_desire_engine as ede
from host_brain.app.prompt_template_store import resolve_prompt_template_json_merged
from host_brain.app.prompt_template_store import PromptTemplateStore
from host_brain.audit.sqlite_ledger import SQLiteLedger
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
    templates = resolve_prompt_template_json_merged("evolution_desire_engine.desire_templates")

    assert engine._try_fulfilled_text(None, 10) == ""
    assert engine._try_fulfilled_text({"unknown": 0.1}, 10) == ""

    first_text = templates["fulfilled_texts"]["warmth"][0]
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
    templates = resolve_prompt_template_json_merged("evolution_desire_engine.desire_templates")

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
    warmth_low = {
        item["text"]
        for item in templates["trait_templates"]["warmth"]
        if item["level"] == "low"
    }
    curiosity_medium = {
        item["text"]
        for item in templates["trait_templates"]["curiosity"]
        if item["level"] == "medium"
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
    warmth_high = {
        item["text"]
        for item in templates["trait_templates"]["warmth"]
        if item["level"] == "high"
    }
    humor_low = {
        item["text"]
        for item in templates["trait_templates"]["humor"]
        if item["level"] == "low"
    }
    assert warmth_high & set(high)
    assert humor_low & set(high)

    ledger = SQLiteLedger(":memory:", check_same_thread=False)
    store = PromptTemplateStore(ledger=ledger, cache_ttl_ms=0)
    store.upsert_template(
        "evolution_desire_engine.desire_templates",
        content=json.dumps(
            {
                "trait_templates": {
                    "warmth": [],
                },
            },
            ensure_ascii=False,
        ),
        enabled=True,
    )

    import host_brain.app.dependencies as deps

    monkeypatch.setattr(deps, "_ledger", ledger, raising=False)
    monkeypatch.setattr(deps, "_prompt_template_store", store, raising=False)

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
    templates = resolve_prompt_template_json_merged("evolution_desire_engine.desire_templates")

    assert engine._generate_from_time_context(ts=8 * one_day_ms, last_deep_talk_ts_ms=0, is_weekend=True) == list(
        templates["time_templates"]["周末"]
    )
    assert engine._generate_from_time_context(ts=8 * one_day_ms, last_deep_talk_ts_ms=0, is_weekend=False) == list(
        templates["time_templates"]["白天"]
    )

    class _HourDateTime(datetime.datetime):
        @classmethod
        def fromtimestamp(cls, ts, tz=None):  # noqa: ANN001
            return cls(2024, 1, 1, int(ts), 0, 0)

    monkeypatch.setattr(datetime, "datetime", _HourDateTime)
    assert engine._generate_from_time_context(ts=20_000, last_deep_talk_ts_ms=0, is_weekend=False) == list(
        templates["time_templates"]["居家夜晚"]
    )
    assert engine._generate_from_time_context(ts=23_000, last_deep_talk_ts_ms=0, is_weekend=False) == list(
        templates["time_templates"]["深夜"]
    )
    assert engine._generate_from_time_context(ts=2_000, last_deep_talk_ts_ms=0, is_weekend=False) == list(
        templates["time_templates"]["凌晨"]
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
    ) == list(templates["time_templates"]["久别重逢"])

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
    templates = resolve_prompt_template_json_merged("evolution_desire_engine.desire_templates")

    assert engine._generate_from_event_count(0) == []
    assert engine._generate_from_event_count(15) == list(templates["event_count_templates"][1]["texts"])

    assert engine._try_user_preference_desire(None, 1) == ""
    assert engine._try_user_preference_desire({"warmth": 0.5}, 1) == ""

    pref_text = templates["user_preference_templates"]["warmth"][0]
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


def test_desire_templates_override_runtime_sections(monkeypatch: pytest.MonkeyPatch) -> None:
    ledger = SQLiteLedger(":memory:", check_same_thread=False)
    store = PromptTemplateStore(ledger=ledger, cache_ttl_ms=0)
    store.upsert_template(
        "evolution_desire_engine.desire_templates",
        content=json.dumps(
            {
                "trait_templates": {
                    "warmth": [{"level": "low", "text": "自定义低温柔"}],
                    "curiosity": [{"level": "medium", "text": "自定义中好奇"}],
                },
                "time_templates": {
                    "周末": ["自定义周末意愿"],
                },
                "fulfilled_texts": {
                    "warmth": ["自定义已更温柔"],
                },
                "user_preference_templates": {
                    "warmth": ["自定义偏好温柔"],
                },
                "event_count_templates": [
                    {"min": 10, "max": 29, "texts": ["自定义互动次数意愿"]},
                ],
            },
            ensure_ascii=False,
        ),
        enabled=True,
    )

    import host_brain.app.dependencies as deps

    monkeypatch.setattr(deps, "_ledger", ledger, raising=False)
    monkeypatch.setattr(deps, "_prompt_template_store", store, raising=False)

    engine = EvolutionDesireEngine(seed=0)
    monkeypatch.setattr(engine._rng, "choice", lambda seq: seq[0])
    monkeypatch.setattr(engine._rng, "random", lambda: 0.0)

    trait_candidates = engine._generate_from_traits(
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
    assert "自定义低温柔" in trait_candidates
    assert "自定义中好奇" in trait_candidates

    one_day_ms = 24 * 3600 * 1000
    assert engine._generate_from_time_context(
        ts=8 * one_day_ms,
        last_deep_talk_ts_ms=0,
        is_weekend=True,
    ) == ["自定义周末意愿"]
    assert engine._generate_from_event_count(15) == ["自定义互动次数意愿"]
    assert engine._try_fulfilled_text({"warmth": 0.03}, ede._DESIRE_COOLDOWN_MS + 1) == "自定义已更温柔"
    assert engine._try_user_preference_desire({"warmth": 1.0}, ede._DESIRE_COOLDOWN_MS + 2) == "自定义偏好温柔"


def test_try_llm_desire_text_uses_prompt_template_override(monkeypatch: pytest.MonkeyPatch) -> None:
    ledger = SQLiteLedger(":memory:", check_same_thread=False)
    store = PromptTemplateStore(ledger=ledger, cache_ttl_ms=0)
    store.upsert_template(
        "persona.self_narrative_prompts",
        content=json.dumps(
            {
                "desire_prompt": "自定义成长意愿：{stage_cn}{trait_hint_str}{event_hint}{emotion_hint}",
            },
            ensure_ascii=False,
        ),
        enabled=True,
    )

    import host_brain.app.dependencies as deps

    monkeypatch.setattr(deps, "_ledger", ledger, raising=False)
    monkeypatch.setattr(deps, "_prompt_template_store", store, raising=False)

    engine = EvolutionDesireEngine(seed=0)
    captured: dict[str, str] = {}

    def _fake_llm(prompt: str) -> str:
        captured["prompt"] = prompt
        return "想更会接住你的情绪"

    text = engine._try_llm_desire(
        traits=PersonalityTraits.from_baseline(),
        emotion_trend="平稳",
        bond_stage="FRIEND",
        event_count=12,
        ts=ede._DESIRE_COOLDOWN_MS + 1,
        llm_fn=_fake_llm,
    )

    assert text == "想更会接住你的情绪"
    assert captured["prompt"].startswith("自定义成长意愿：")
