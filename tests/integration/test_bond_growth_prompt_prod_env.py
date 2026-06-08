from __future__ import annotations

import pytest

from host_brain.bond.bond_stage_engine import BondStageEngine
from host_brain.persona.persona_engine import PersonaEngine
from host_brain.persona.persona_growth import PersonaGrowthTracker


@pytest.fixture(autouse=True)
def _prod_env(monkeypatch):
    monkeypatch.setenv("Natsume_ENV", "prod")
    PersonaEngine.reset_instance()
    yield
    PersonaEngine.reset_instance()



def test_bond_stage_engine_prod_promotes_and_summarizes() -> None:
    engine = BondStageEngine()
    user_id = 'prod-bond-user-001'

    last_result = None
    for index in range(3):
        last_result = engine.apply_event(
            user_id=user_id,
            update_kind='EMOTIONAL_SUPPORT',
            evidence_refs=[f'evd://bond/{index}'],
            ts_ms=1_700_000_000_000 + index * 1000,
            emotion_intensity=0.95,
        )

    assert last_result is not None
    assert last_result.to_stage == 'FRIEND'
    summary = engine.get_summary(user_id)
    assert summary['stage'] == '关系好的兄妹'
    assert summary['capability_hint'] == '关系好的兄妹，可轻松拌嘴'
    assert summary['trend'] in {'上升', '快速上升'}

    state = engine._get_or_create_state(user_id)
    state.inject_narrative(milestone_hint='我们已经一起经历了一些重要时刻', shared_memory_count=3)
    assert getattr(state, '_milestone_hint', '') == '我们已经一起经历了一些重要时刻'
    assert getattr(state, '_shared_memory_count', 0) == 3
    summary = state.to_summary()
    assert 'milestone_hint' in summary and 'shared_memory_count' in summary



def test_persona_growth_tracker_prod_records_stage_milestone_and_interaction_growth() -> None:
    engine = PersonaEngine.get_instance()
    tracker = PersonaGrowthTracker(engine=engine)
    user_id = 'prod-growth-user-001'

    promoted = tracker.on_stage_promoted(
        user_id=user_id,
        from_stage='FRIEND',
        to_stage='CLOSE_FRIEND',
        ts_ms=1_700_000_000_000,
    )
    milestone = tracker.on_milestone(
        user_id=user_id,
        milestone_key='deep_talk_50',
        ts_ms=1_700_000_001_000,
    )
    interaction = tracker.on_interaction_cycle(
        user_id=user_id,
        interaction_stats={'deep_talk': 3, 'emotional_support': 3},
        ts_ms=1_700_000_002_000,
    )

    assert promoted.applied is True
    assert milestone.applied is True
    assert interaction.applied is True
    triggered = tracker.get_triggered_rules(user_id)
    assert 'FRIEND→CLOSE_FRIEND' in triggered
    assert 'deep_talk_50' in triggered
    weekly = tracker.get_weekly_cumulative(user_id)
    assert weekly
    history = tracker.get_change_history(user_id)
    assert len(history) >= 3
    traits = engine.get_traits(user_id)
    assert traits.playfulness >= 0.5
    assert traits.curiosity >= 0.7
