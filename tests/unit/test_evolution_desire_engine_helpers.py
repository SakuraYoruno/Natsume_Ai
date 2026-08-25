from __future__ import annotations

import datetime
import json

import pytest

import host_brain.persona.evolution_desire_engine as ede
from host_brain.model_runtime.prompt_contract import resolve_prompt_contract_json
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


def test_generate_desire_strict_llm_disables_template_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = EvolutionDesireEngine(seed=0)
    monkeypatch.setattr(engine, "_try_fulfilled_text", lambda recent_trait_deltas, ts: "")
    monkeypatch.setattr(engine, "_try_user_preference_desire", lambda hints, ts: "")

    desire = engine.generate_desire(
        traits=PersonalityTraits.from_baseline(),
        emotion_trend="平稳",
        bond_stage="FRIEND",
        current_ts_ms=1,
        llm_fn=lambda prompt: (_ for _ in ()).throw(RuntimeError("boom")),
        strict_llm=True,
    )

    assert desire == ""


def test_self_narrative_prompt_spec_has_desire_prompt() -> None:
    templates = resolve_prompt_contract_json("persona.self_narrative_prompts")

    assert isinstance(templates, dict)
    assert str(templates.get("desire_prompt") or "").strip()


def test_try_llm_desire_skips_empty_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = EvolutionDesireEngine(seed=0)
    calls: list[str] = []

    monkeypatch.setattr(ede, "_load_prompt_templates", lambda: {})

    desire = engine._try_llm_desire(
        llm_fn=lambda prompt: calls.append(str(prompt)) or "我想更自然一点。",
        traits=PersonalityTraits.from_baseline(),
        emotion_trend="平稳",
        bond_stage="FRIEND",
        event_count=0,
        ts=1,
    )

    assert desire == ""
    assert calls == []
