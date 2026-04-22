from __future__ import annotations

import json
import pytest

from host_brain.app.prompt_builder import (
    _build_character_dynamic_patch,
    _build_node_role_instruction,
    get_last_injection_meta,
    _set_injection_meta,
    extract_node_narrative_text,
    extract_node_runtime_payload,
)
from host_brain.app.context.identity_source import _CHARACTER_PROFILE_CACHE, load_character_profile


@pytest.fixture(autouse=True)
def _prod_env(monkeypatch):
    monkeypatch.setenv('Natsume_ENV', 'prod')
    yield



def test_prompt_builder_helpers_prod_load_character_profile_from_env(tmp_path, monkeypatch) -> None:
    profile_path = tmp_path / 'character_profile.json'
    long_text = '你是测试版夏目蓝。请保持稳定语气。' * 10
    profile_path.write_text(json.dumps({'instruction_cn': long_text}, ensure_ascii=False), encoding='utf-8')
    monkeypatch.setenv('NATSUME_CHARACTER_PROFILE_PATH', str(profile_path))
    _CHARACTER_PROFILE_CACHE.clear()

    text = load_character_profile('ja')

    assert '测试版夏目蓝' in text
    assert '稳定语气' in text



def test_prompt_builder_helpers_prod_character_dynamic_patch_contains_holiday_and_emotion() -> None:
    patch = _build_character_dynamic_patch(
        emotion_tag='HAPPY',
        is_holiday=True,
        holiday_name='樱花祭',
    )

    assert patch
    assert '樱花祭' in patch
    assert any(token in patch for token in ('开心', '轻盈', '明亮', '节日'))



def test_prompt_builder_helpers_prod_injection_meta_roundtrip() -> None:
    _set_injection_meta({'mode': 'ACTIVE', 'detected': True, 'risk_level': 'HIGH'})
    meta = get_last_injection_meta()

    assert meta['mode'] == 'ACTIVE'
    assert meta['detected'] is True
    assert meta['risk_level'] == 'HIGH'



def test_prompt_builder_helpers_prod_build_node_role_instruction_variants() -> None:
    zh = _build_node_role_instruction('chat_direct_ja', render_target_lang='zh')
    ja = _build_node_role_instruction('chat_direct_ja', render_target_lang='ja')
    other = _build_node_role_instruction('persona_renderer', render_target_lang='zh')

    assert '最终回复' in zh or '先回应' in zh
    assert '日本語' in ja or '最終応答' in ja
    assert other



def test_prompt_builder_helpers_prod_extract_node_payload_and_narrative() -> None:
    runtime_payload = {
        'core_reasoner_primary': {
            'payload': {
                'narrative': {'text': '核心结论'},
                'intent_draft': {'route': 'chat', 'target_id': '', 'action': 'narrative_only', 'args': {}},
            }
        }
    }

    payload = extract_node_runtime_payload(runtime_payload, node_id='core_reasoner_primary')
    narrative = extract_node_narrative_text(runtime_payload, node_id='core_reasoner_primary')

    assert payload['intent_draft']['action'] == 'narrative_only'
    assert narrative == '核心结论'
