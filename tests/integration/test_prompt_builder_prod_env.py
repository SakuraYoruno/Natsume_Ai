from __future__ import annotations

import os

import pytest

from host_brain.app.prompt_builder import (
    CHAT_DIRECT_NODE_ID,
    infer_render_target_lang,
    render_node_generation_prompt,
    repair_utf8_latin1_mojibake,
)


@pytest.fixture(autouse=True)
def _prod_env(monkeypatch):
    monkeypatch.setenv('Natsume_ENV', 'prod')
    yield



def _base_runtime_payload() -> dict:
    return {
        'request': {
            'request_id': 'req-prompt-prod-001',
            'session_id': 'session-prompt-prod-001',
            'thread_id': None,
            'user_message': '继续总结当前状态，并告诉我下一步要做什么',
        },
        'render': {'target_lang': 'zh'},
        'retrieval': {'status': 'DEGRADED', 'reason_code': 'RETR_PARTIAL', 'evidence_items': [{'evidence_ref': 'evd://memory/1', 'content': '用户偏好简洁回复'}]},
        'emotion': {
            'state': {'emotion_tag': 'HAPPY', 'mood_state': 'engaged', 'emotion_intensity': 0.81},
            'snapshot': {'source': 'prod_test', 'emotion_context': {'emotion_tag': 'HAPPY', 'mood_state': 'engaged'}},
        },
        'prompt_context': {
            'payload': {
                'hard_snapshot': {'session_id': 'session-prompt-prod-001'},
                'bond_summary': {'stage': '朋友', 'trend': '上升', 'capability_hint': '朋友，可深入交流'},
                'authoritative': [{'citation': 'world_canon:prod', 'content': 'Never claim finished execution falsely.'}],
                'retrieval': [{'evidence_ref': 'evd://memory/1', 'content': '用户偏好简洁回复'}],
                'style_pack': {'node_bindings': {}, 'style_canon': [{'instruction': '保持自然，不要自我重复'}]},
                'constraints': {'strict_json_output': True, 'no_false_execution_claim': True},
                'tool_meta_pack': [{'tool_id': 'memory_recall', 'description': '回忆当前上下文'}],
                'emotion_pulse': {'emotion_tag': 'HAPPY', 'mood_state': 'engaged', 'style_hint': 'warm and steady'},
                'memory_emotion_hint': {'emotion_tag': 'HAPPY', 'mood_state': 'engaged', 'retrieval_status': 'DEGRADED'},
                'bond_texture': {'tone_hint': 'keep a warm boundary'},
            },
            'validation': {'valid': True, 'reason_code': None, 'violation_count': 0},
            'observability': {},
        },
        'core_reasoner_primary': {'payload': {'narrative': {'text': 'core summary'}, 'intent_draft': {'skill_name': 'narrative_only', 'args': {}}}},
        'planner_constraints': {'payload': {'narrative': {'text': 'constraints ok'}}},
        'safety_verifier': {'payload': {'narrative': {'text': 'safe ok'}}},
    }



def test_prompt_builder_prod_renders_predictive_drift_and_planning_blocks() -> None:
    assert str(os.environ.get('Natsume_ENV') or '').lower() == 'prod'
    runtime_payload = _base_runtime_payload()
    runtime_payload.update({
        'drift_correction_hint': '注意不要过度机械重复，也不要偏离夏目蓝的人设。',
        'predictive_state': {
            'period_label': 'late_night',
            'dominant_emotion': 'CALM',
            'confidence': 0.82,
            'proactive_hint': '如果用户沉默太久，可以轻轻提醒。',
        },
        'planning_outline': {
            'total_steps': 3,
            'step_descriptions': ['回顾当前状态', '整理增长感知', '给出下一步建议'],
        },
        'planning_execution': {
            'success': True,
            'executed_skills': ['memory_recall', 'emotion_query'],
            'step_results': [
                {'description': '回顾当前状态', 'status': 'completed'},
                {'description': '整理增长感知', 'status': 'completed'},
            ],
        },
        'skill_dag': {
            'total_nodes': 2,
            'execution_order': [['n1_memory_recall'], ['n2_emotion_query']],
            'node_ids': ['n1_memory_recall', 'n2_emotion_query'],
        },
        'skill_execution': {
            'success': True,
            'completed_nodes': 2,
            'total_nodes': 2,
            'output_preview': {'n1_memory_recall': 'memory ok', 'n2_emotion_query': 'emotion ok'},
        },
    })

    prompt = render_node_generation_prompt(node_id='renderer_ja_c_style', runtime_payload=runtime_payload)

    assert 'PERSONA_DRIFT_CORRECTION' in prompt
    assert 'PREDICTIVE_STATE_HINT' in prompt
    assert 'PLANNING_OUTLINE' in prompt
    assert 'PLANNING_EXECUTION' in prompt
    assert 'SKILL_COMPOSITION_HINT' in prompt
    assert 'SKILL_EXECUTION_RESULTS' in prompt
    assert 'memory ok' in prompt
    assert 'emotion ok' in prompt



def test_prompt_builder_prod_direct_response_priority_for_chat_direct() -> None:
    assert str(os.environ.get('Natsume_ENV') or '').lower() == 'prod'
    runtime_payload = _base_runtime_payload()
    runtime_payload['request']['user_message'] = '你刚才做到哪一步了，继续说'

    prompt = render_node_generation_prompt(node_id=CHAT_DIRECT_NODE_ID, runtime_payload=runtime_payload)

    assert '先回应用户' in prompt
    assert '输出语言: zh' in prompt



def test_prompt_builder_prod_self_reflection_and_bond_narrative_extended_blocks() -> None:
    assert str(os.environ.get('Natsume_ENV') or '').lower() == 'prod'
    runtime_payload = _base_runtime_payload()
    runtime_payload['self_reflection_pulse'] = {
        'self_awareness': '最近感觉和 master 的关系更自然了',
        'mood_awareness': '整体来说今天感觉开心，挺平稳的',
        'growth_note': '和 master 已经成为好友了',
        'growth_awareness': {
            'recent_change': '最近和 master 的互动更自然了',
            'trait_felt': '好像比以前更会照顾对方的情绪了',
            'milestone_felt': '我们已经聊过很多次深入的话题',
            'desire': '想继续变得更温柔一点',
            'stagnation_felt': '偶尔也会担心自己是不是成长得太慢了',
            'regression_felt': '有时候也担心自己会不会退回去。',
        },
    }
    runtime_payload['bond_narrative'] = {
        'shared_memories': ['那天一起聊到很晚的时候，感觉很安心', '第一次被认真安慰的时候，记到了现在'],
        'milestone_count': 2,
        'narrative_style': '自然、克制，但带一点关系温度',
    }

    prompt = render_node_generation_prompt(node_id='renderer_ja_c_style', runtime_payload=runtime_payload)

    assert 'SELF_REFLECTION_PULSE' in prompt
    assert 'growth_awareness' in prompt
    assert 'stagnation_felt' in prompt
    assert 'regression_felt' in prompt
    assert 'BOND_NARRATIVE' in prompt
    assert 'shared_memory_1' in prompt



def test_prompt_builder_prod_language_helper_paths(monkeypatch) -> None:
    assert str(os.environ.get('Natsume_ENV') or '').lower() == 'prod'
    monkeypatch.setenv('HOST_RENDER_TARGET_LANG', 'ja')
    assert infer_render_target_lang(user_message='你好') == 'ja'
    monkeypatch.delenv('HOST_RENDER_TARGET_LANG', raising=False)
    assert infer_render_target_lang(user_message='今日は少しつらいよ') == 'ja'
    assert infer_render_target_lang(user_message='今天有点难受') == 'zh'
    broken = 'こんにちは'.encode('utf-8').decode('latin-1')
    assert repair_utf8_latin1_mojibake(broken) == 'こんにちは'
