from __future__ import annotations

import os

import pytest

from host_brain.bond.bond_stage_engine import BondStageEngine
from host_brain.persona.persona_engine import PersonaEngine
from host_brain.persona.persona_growth import PersonaGrowthTracker
from host_brain.app.prompt_builder import (
    infer_render_target_lang,
    repair_utf8_latin1_mojibake,
    render_node_generation_prompt,
)


@pytest.fixture(autouse=True)
def _prod_env(monkeypatch):
    monkeypatch.setenv("Natsume_ENV", "prod")
    PersonaEngine.reset_instance()
    yield
    PersonaEngine.reset_instance()



def test_bond_stage_engine_prod_promotes_and_summarizes() -> None:
    assert str(os.environ.get("Natsume_ENV") or "").lower() == "prod"
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
    assert summary['stage'] == '朋友'
    assert summary['capability_hint'] == '朋友，可深入交流'
    assert summary['trend'] in {'上升', '快速上升'}

    state = engine._get_or_create_state(user_id)
    state.inject_narrative(milestone_hint='我们已经一起经历了一些重要时刻', shared_memory_count=3)
    assert getattr(state, '_milestone_hint', '') == '我们已经一起经历了一些重要时刻'
    assert getattr(state, '_shared_memory_count', 0) == 3
    summary = state.to_summary()
    assert 'milestone_hint' in summary and 'shared_memory_count' in summary



def test_persona_growth_tracker_prod_records_stage_milestone_and_interaction_growth() -> None:
    assert str(os.environ.get("Natsume_ENV") or "").lower() == "prod"
    engine = PersonaEngine.get_instance()
    tracker = PersonaGrowthTracker(engine=engine)
    user_id = 'prod-growth-user-001'

    promoted = tracker.on_stage_promoted(
        user_id=user_id,
        from_stage='ACQUAINTANCE',
        to_stage='FRIEND',
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
    assert 'ACQUAINTANCE→FRIEND' in triggered
    assert 'deep_talk_50' in triggered
    weekly = tracker.get_weekly_cumulative(user_id)
    assert weekly
    history = tracker.get_change_history(user_id)
    assert len(history) >= 3
    traits = engine.get_traits(user_id)
    assert traits.playfulness >= 0.5
    assert traits.curiosity >= 0.7



def test_prompt_builder_prod_renders_growth_awareness_and_bond_narrative() -> None:
    assert str(os.environ.get("Natsume_ENV") or "").lower() == "prod"
    runtime_payload = {
        'request': {
            'request_id': 'req-prod-prompt-001',
            'session_id': 'session-prod-prompt-001',
            'thread_id': None,
            'user_message': '帮我总结一下最近的变化',
        },
        'render': {'target_lang': 'zh'},
        'retrieval': {'status': 'OK', 'reason_code': 'NONE', 'evidence_items': []},
        'emotion': {
            'state': {'emotion_tag': 'HAPPY', 'mood_state': 'engaged', 'emotion_intensity': 0.7},
            'snapshot': {'emotion_context': {'emotion_tag': 'HAPPY', 'mood_state': 'engaged'}},
        },
        'prompt_context': {
            'payload': {
                'hard_snapshot': {'session_id': 'session-prod-prompt-001'},
                'bond_summary': {'stage': '朋友', 'trend': '上升', 'capability_hint': '朋友，可深入交流'},
                'authoritative': [],
                'retrieval': [],
                'style_pack': {'node_bindings': {}},
                'constraints': {'strict_json_output': True},
                'tool_meta_pack': [{'tool_id': 'memory_recall', 'description': '回忆信息'}],
                'emotion_pulse': {'emotion_tag': 'HAPPY', 'mood_state': 'engaged', 'style_hint': 'warm and steady'},
                'memory_emotion_hint': {'emotion_tag': 'HAPPY', 'mood_state': 'engaged'},
                'bond_texture': {'tone_hint': 'keep a warm boundary'},
            },
            'validation': {'valid': True, 'reason_code': None, 'violation_count': 0},
            'observability': {},
        },
        'self_reflection_pulse': {
            'self_awareness': '最近感觉和 master 的关系更自然了',
            'mood_awareness': '整体来说今天感觉开心，挺平稳的',
            'growth_note': '和 master 已经成为好友了',
            'growth_awareness': {
                'recent_change': '最近和 master 的互动更自然了',
                'trait_felt': '好像比以前更会照顾对方的情绪了',
                'milestone_felt': '我们已经聊过很多次深入的话题',
                'desire': '想继续变得更温柔一点',
                'stagnation_felt': '偶尔也会担心自己是不是成长得太慢了',
            },
        },
        'bond_narrative': {
            'shared_memories': ['那天一起聊到很晚的时候，感觉很安心', '第一次被认真安慰的时候，记到了现在'],
            'milestone_count': 2,
            'narrative_style': '自然、克制，但带一点关系温度',
        },
        'planning_outline': {'total_steps': 2, 'step_descriptions': ['回顾最近关系变化', '整理成长感知']},
        'planning_execution': {'success': True, 'executed_skills': ['memory_recall'], 'step_results': [{'description': '回顾最近关系变化', 'status': 'completed'}]},
        'skill_dag': {'total_nodes': 1, 'execution_order': [['n1_memory_recall']], 'node_ids': ['n1_memory_recall']},
        'skill_execution': {'success': True, 'completed_nodes': 1, 'total_nodes': 1, 'output_preview': {'n1_memory_recall': 'memory ok'}},
        'core_reasoner_primary': {'payload': {'narrative': {'text': 'core summary'}, 'intent_draft': {'route': 'chat', 'target_id': '', 'action': 'narrative_only', 'args': {}}}},
        'planner_constraints': {'payload': {'narrative': {'text': 'constraints ok'}}},
        'safety_verifier': {'payload': {'narrative': {'text': 'safe ok'}}},
    }

    prompt = render_node_generation_prompt(node_id='chat_direct_zh', runtime_payload=runtime_payload)

    assert 'SELF_REFLECTION_PULSE' in prompt
    assert 'growth_awareness' in prompt
    assert 'desire:' in prompt
    assert 'BOND_NARRATIVE' in prompt
    assert 'shared_memory_1' in prompt
    assert 'PLANNING_EXECUTION' in prompt
    assert 'SKILL_EXECUTION_RESULTS' in prompt
    assert 'memory ok' in prompt



def test_prompt_builder_prod_helpers_cover_language_and_mojibake(monkeypatch) -> None:
    assert str(os.environ.get("Natsume_ENV") or "").lower() == "prod"
    monkeypatch.setenv('HOST_RENDER_TARGET_LANG', 'zh')
    assert infer_render_target_lang(user_message='hello world') == 'zh'
    monkeypatch.delenv('HOST_RENDER_TARGET_LANG', raising=False)
    raw = 'こんにちは'.encode('utf-8').decode('latin-1')
    repaired = repair_utf8_latin1_mojibake(raw)
    assert repaired == 'こんにちは'
