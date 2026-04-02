from __future__ import annotations

import os

import pytest

from host_brain.persona.growth_feedback_collector import GrowthFeedbackCollector
from host_brain.persona.persona_engine import PersonaEngine
from host_brain.persona.persona_growth import GrowthRule, PersonaGrowthTracker


@pytest.fixture(autouse=True)
def _prod_env(monkeypatch):
    monkeypatch.setenv('Natsume_ENV', 'prod')
    PersonaEngine.reset_instance()
    yield
    PersonaEngine.reset_instance()


class _CanonDoc:
    def __init__(self, body: dict):
        self.body = body


class _CanonStore:
    def __init__(self, body: dict):
        self._body = body

    def get(self, doc_id: str):  # noqa: ANN001
        if doc_id == 'wc-004-persona-growth-rules':
            return _CanonDoc(self._body)
        return None



def test_persona_growth_tracker_prod_loads_rules_from_canon_store() -> None:
    body = {
        'stage_promotion_rules': {
            'FRIEND→CLOSE_FRIEND': {
                'delta': {'warmth': 0.02, 'sensitivity': 0.01},
                'description': 'Canon rule for close friend',
                'one_time': True,
            }
        },
        'milestone_rules': {
            'custom_milestone': {
                'delta': {'curiosity': 0.02},
                'description': 'Canon milestone rule',
                'one_time': True,
            }
        },
    }
    tracker = PersonaGrowthTracker(engine=PersonaEngine.get_instance(), canon_store=_CanonStore(body))

    stage_result = tracker.on_stage_promoted('prod-growth-canon-user', 'FRIEND', 'CLOSE_FRIEND', ts_ms=1775123000000)
    milestone_result = tracker.on_milestone('prod-growth-canon-user', 'custom_milestone', ts_ms=1775123001000)

    assert stage_result.applied is True
    assert stage_result.reason == 'Canon rule for close friend'
    assert milestone_result.applied is True
    assert milestone_result.reason == 'Canon milestone rule'



def test_persona_growth_tracker_prod_one_time_rule_is_not_reapplied() -> None:
    tracker = PersonaGrowthTracker(engine=PersonaEngine.get_instance())
    user_id = 'prod-growth-onetime-user'

    first = tracker.on_stage_promoted(user_id, 'ACQUAINTANCE', 'FRIEND', ts_ms=1775123010000)
    second = tracker.on_stage_promoted(user_id, 'ACQUAINTANCE', 'FRIEND', ts_ms=1775123011000)

    assert first.applied is True
    assert second.applied is False
    assert 'one_time' in second.reason



def test_persona_growth_tracker_prod_interaction_cycle_with_feedback_merge_and_reset() -> None:
    engine = PersonaEngine.get_instance()
    tracker = PersonaGrowthTracker(engine=engine)
    feedback = GrowthFeedbackCollector(min_signals=2, signal_threshold=0.15, feedback_delta=0.02)
    user_id = 'prod-growth-feedback-merge-user'

    signals = feedback.extract_from_text('你好温柔，也很体贴', ts_ms=1775123020000)
    for signal in signals:
        feedback.add_feedback(user_id, signal)
    feedback.add_webui_vote(user_id, trait_name='warmth', is_positive=True, ts_ms=1775123021000)

    result = tracker.on_interaction_cycle(
        user_id,
        interaction_stats={'deep_talk': 3, 'emotional_support': 3},
        lookback_days=7,
        ts_ms=1775123022000,
        feedback_collector=feedback,
    )

    assert result.applied is True
    assert result.delta_applied is not None
    assert result.delta_applied['warmth'] > 0
    assert feedback.get_buffer_size(user_id) == 0
    assert tracker.get_weekly_cumulative(user_id)['warmth'] > 0
    assert tracker.get_change_history(user_id)
    tracker.reset_weekly_cumulative(user_id)
    assert tracker.get_weekly_cumulative(user_id)['warmth'] == 0.0



def test_persona_growth_tracker_prod_custom_rule_rejected_by_guard() -> None:
    engine = PersonaEngine.get_instance()
    tracker = PersonaGrowthTracker(engine=engine)
    user_id = 'prod-growth-guard-user'
    aggressive_rule = GrowthRule(
        trigger='custom:too_much_assertiveness',
        delta={'assertiveness': 0.9},
        description='unsafe jump',
        one_time=False,
    )

    result = tracker._apply_rule(
        user_id=user_id,
        rule=aggressive_rule,
        rule_key='unsafe_rule',
        ts_ms=1775123030000,
    )

    assert result.applied is False
    assert result.guard_result is not None
    assert '安全校验失败' in result.reason
