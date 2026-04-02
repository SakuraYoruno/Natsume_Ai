from __future__ import annotations

import os

import pytest

from host_brain.bond.bond_stage_engine import BondScoreState
from host_brain.persona.evolution_desire_engine import EvolutionDesireEngine
from host_brain.persona.evolution_orchestrator import EvolutionOrchestrator
from host_brain.persona.persona_engine import PersonaEngine
from host_brain.persona.personality_traits import PersonalityTraits


@pytest.fixture(autouse=True)
def _prod_env(monkeypatch):
    monkeypatch.setenv("Natsume_ENV", "prod")
    PersonaEngine.reset_instance()
    yield
    PersonaEngine.reset_instance()



def _timeline_summary(*, dominant: str = "HAPPY", trend: str = "improving") -> dict[str, object]:
    return {
        "dominant_emotion": dominant,
        "trend": trend,
        "event_count": 12,
        "avg_valence": 0.45 if dominant == "HAPPY" else -0.35,
    }



def _timeline_diary(*, dominant: str = "HAPPY") -> dict[str, object]:
    return {
        "dominant_emotion": dominant,
        "avg_valence": 0.4 if dominant == "HAPPY" else -0.3,
        "arc_start": "NEUTRAL",
        "arc_end": dominant,
    }



def test_evolution_desire_engine_prod_generates_from_traits() -> None:
    assert str(os.environ.get("Natsume_ENV") or "").lower() == "prod"
    engine = EvolutionDesireEngine(seed=7)
    traits = PersonalityTraits(
        warmth=0.15,
        curiosity=0.2,
        humor=0.6,
        sensitivity=0.75,
        assertiveness=0.5,
        playfulness=0.45,
        resilience=0.55,
    )

    desire = engine.generate_desire(
        traits=traits,
        emotion_trend="平稳",
        bond_stage="FRIEND",
        event_count=8,
        current_ts_ms=10 * 24 * 3600 * 1000,
    )

    assert desire
    stats = engine.get_cooldown_stats()
    assert stats["total_tracked"] == 1
    assert stats["active_cooldowns"] in {0, 1}



def test_evolution_desire_engine_prod_fulfilled_branch_and_cooldown() -> None:
    assert str(os.environ.get("Natsume_ENV") or "").lower() == "prod"
    engine = EvolutionDesireEngine(seed=1)
    ts_ms = 10 * 24 * 3600 * 1000

    fulfilled = engine._try_fulfilled_text({"warmth": 0.03}, ts_ms)

    assert fulfilled
    assert any(token in fulfilled for token in ("做到", "一点", "最近"))
    assert engine._filter_cooled_down([fulfilled], ts_ms) == []
    engine.clear_cooldowns()
    assert engine.get_cooldown_stats()["total_tracked"] == 0



def test_evolution_desire_engine_prod_time_context_and_event_anchor() -> None:
    assert str(os.environ.get("Natsume_ENV") or "").lower() == "prod"
    engine = EvolutionDesireEngine(seed=3)

    reunion = engine._generate_from_time_context(
        ts=10 * 24 * 3600 * 1000,
        last_deep_talk_ts_ms=0,
        is_weekend=True,
    )
    anchored = engine._generate_from_event_count(6)

    assert reunion
    assert anchored



def test_evolution_orchestrator_prod_cycle_generates_growth_awareness() -> None:
    assert str(os.environ.get("Natsume_ENV") or "").lower() == "prod"
    orchestrator = EvolutionOrchestrator()
    bond_state = BondScoreState(
        user_id="prod-evo-user-001",
        current_stage="FRIEND",
        event_count=55,
    )
    bond_summary = bond_state.to_summary()

    result = orchestrator.run_evolution_cycle(
        user_id="prod-evo-user-001",
        date="2026-04-02",
        timeline_summary=_timeline_summary(dominant="HAPPY", trend="improving"),
        timeline_diary=_timeline_diary(dominant="HAPPY"),
        bond_state=bond_state,
        bond_summary=bond_summary,
        interaction_stats={"deep_talk": 3, "emotional_support": 3},
        ts_ms=1775100000000,
    )

    assert result.cycle_id.startswith("evo_")
    assert result.traits_changed is True
    assert result.growth_results
    assert result.desire_text
    assert result.growth_awareness
    assert orchestrator.get_last_growth_awareness() == result.growth_awareness
    assert any(key in result.growth_awareness for key in ("desire", "recent_change", "milestone_felt", "trait_felt"))



def test_evolution_orchestrator_prod_stagnation_after_three_cycles() -> None:
    assert str(os.environ.get("Natsume_ENV") or "").lower() == "prod"
    orchestrator = EvolutionOrchestrator()
    bond_state = BondScoreState(
        user_id="prod-evo-user-002",
        current_stage="STRANGER",
        event_count=0,
    )
    last_result = None

    for index in range(3):
        last_result = orchestrator.run_evolution_cycle(
            user_id="prod-evo-user-002",
            date=f"2026-04-0{index + 2}",
            timeline_summary=_timeline_summary(dominant="NEUTRAL", trend="平稳"),
            timeline_diary=_timeline_diary(dominant="NEUTRAL"),
            bond_state=bond_state,
            bond_summary=bond_state.to_summary(),
            interaction_stats={},
            ts_ms=1775100000000 + index * 86400000,
        )
        assert last_result is not None
        assert last_result.traits_changed is False

    assert last_result is not None
    assert "stagnation_felt" in last_result.growth_awareness
    assert last_result.growth_awareness["stagnation_felt"]
