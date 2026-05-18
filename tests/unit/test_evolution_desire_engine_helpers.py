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
