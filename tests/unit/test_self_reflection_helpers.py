from __future__ import annotations

import json
from types import SimpleNamespace

import host_brain.persona.self_reflection as sr_mod
from host_brain.app.prompt_template_store import PromptTemplateStore, resolve_prompt_template_json_merged
from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.persona.self_reflection import SelfReflection, build_self_reflection_pulse


def test_should_trigger_by_emotion_respects_thresholds_and_dedup() -> None:
    reflection = SelfReflection(min_interval=10, max_interval=10, seed=0)
    reflection._next_trigger_turn = 10

    assert reflection.should_trigger_by_emotion(7, "") is False
    assert reflection.should_trigger_by_emotion(7, "neutral") is False

    reflection._last_event_emotion = "SAD"
    assert reflection.should_trigger_by_emotion(7, "sad") is False

    reflection._last_event_emotion = ""
    assert reflection.should_trigger_by_emotion(7, "sad") is True
    assert reflection.should_trigger_by_emotion(10, "sad") is False


def test_maybe_generate_returns_none_when_snapshot_build_fails(monkeypatch) -> None:
    reflection = SelfReflection(min_interval=1, max_interval=1, seed=0)
    monkeypatch.setattr(
        reflection._model,
        "build_snapshot",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    assert reflection.maybe_generate(turn_count=1) is None


def test_build_self_reflection_pulse_injects_optional_fields(monkeypatch) -> None:
    monkeypatch.setattr(
        sr_mod,
        "build_self_awareness_text",
        lambda snapshot, time_context="", llm_fn=None, style_evolution=None, strict_llm=False: "aware",
    )
    monkeypatch.setattr(
        sr_mod,
        "build_mood_awareness_text",
        lambda snapshot, llm_fn=None, strict_llm=False: "mood",
    )
    monkeypatch.setattr(
        sr_mod,
        "build_growth_note_text",
        lambda snapshot, llm_fn=None, style_evolution=None, strict_llm=False: "grow",
    )
    monkeypatch.setattr(
        sr_mod,
        "detect_contradictions",
        lambda snapshot: [{"type": "push_pull"}, {"type": "fear"}],
    )
    monkeypatch.setattr(sr_mod, "build_contradiction_awareness_text", lambda contradictions: "contradiction")

    pulse = build_self_reflection_pulse(
        {"bond_growth_perception": "稳定"},
        growth_awareness={
            "recent_change": "更坦率了",
            "trait_felt": "",
            "milestone_felt": "被珍惜",
            "desire": None,
        },
        growth_summary={
            "daily_summary": "今天更会把关系接稳。",
            "weekly_summary": "这周更敢表达在意，也更会接住情绪。",
            "active_themes": ["recent_change", "desire"],
            "recent_cycle_count": 3,
            "last_cycle_date": "2026-04-04",
        },
        trigger_reason="因为刚刚很想你",
        time_context="久别重逢",
    )

    assert pulse["contradiction_awareness"] == "contradiction"
    assert pulse["contradiction_types"] == ["push_pull", "fear"]
    assert pulse["trigger_reason"] == "因为刚刚很想你"
    assert pulse["time_context"] == "久别重逢"
    assert pulse["growth_awareness"] == {
        "recent_change": "更坦率了",
        "milestone_felt": "被珍惜",
    }
    assert pulse["growth_summary"] == {
        "daily_summary": "今天更会把关系接稳。",
        "weekly_summary": "这周更敢表达在意，也更会接住情绪。",
        "active_themes": ["recent_change", "desire"],
        "recent_cycle_count": 3,
        "last_cycle_date": "2026-04-04",
    }



def test_build_self_reflection_pulse_strict_llm_disables_template_fallback(monkeypatch) -> None:
    monkeypatch.setattr(
        sr_mod,
        "build_self_awareness_text",
        lambda snapshot, **kwargs: "",
    )
    monkeypatch.setattr(
        sr_mod,
        "build_mood_awareness_text",
        lambda snapshot, **kwargs: "",
    )
    monkeypatch.setattr(
        sr_mod,
        "build_growth_note_text",
        lambda snapshot, **kwargs: "",
    )
    monkeypatch.setattr(sr_mod, "detect_contradictions", lambda snapshot: [])
    monkeypatch.setattr(sr_mod, "build_contradiction_awareness_text", lambda contradictions: "")

    pulse = sr_mod.build_self_reflection_pulse(
        {"bond_growth_perception": "稳定"},
        llm_fn=lambda prompt: (_ for _ in ()).throw(RuntimeError("boom")),
        strict_llm=True,
    )

    assert pulse["self_awareness"] == ""
    assert pulse["mood_awareness"] == ""
    assert pulse["growth_note"] == ""


def test_build_style_trend_note_strict_llm_returns_empty_on_failure() -> None:
    text = sr_mod._build_style_trend_note(
        {"warmth": "rising"},
        llm_fn=lambda prompt: (_ for _ in ()).throw(RuntimeError("boom")),
        strict_llm=True,
    )

    assert text == ""
