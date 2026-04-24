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
from host_brain.app.context.soul_runtime import load_compiled_soul


@pytest.fixture(autouse=True)
def _prod_env(monkeypatch):
    monkeypatch.setenv('Natsume_ENV', 'prod')
    yield



def test_prompt_builder_helpers_prod_load_compiled_soul_from_env(tmp_path, monkeypatch) -> None:
    soul_path = tmp_path / 'Soul.md'
    soul_path.write_text(
        "\n".join(
            [
                "## 一、基本角色档案",
                "| 属性 | 信息 |",
                "|------|------|",
                "| 中文名 | 新海 天 |",
                "| 学校 | 白泉学园 一年级生 |",
                "| 关系 | 新海翔（哥哥）的亲妹妹 |",
                "",
                "## 三、核心性格",
                "- 活泼",
                "- 爱撒娇",
                "",
                "## 四、与“哥哥”关系模式",
                "- 默认称呼：哥哥",
                "- 会自己跑过来黏着你",
                "",
                "## 五、AI伴侣语气风格",
                "- 说话节奏快，但有温度",
                "### 禁忌风格",
                "- 不做病娇控制欲",
                "",
                "## 八、一句话总纲",
                "> 新海天是一个外向活泼、爱黏哥哥的 AI 伴侣。",
            ]
        ),
        encoding='utf-8',
    )
    monkeypatch.setenv('HOST_SOUL_MD_PATH', str(soul_path))

    compiled = load_compiled_soul(force=True)

    assert '新海 天' in compiled.identity_core
    assert '新海天是一个外向活泼' in compiled.runtime_compact_text
    assert any('病娇控制欲' in item for item in compiled.taboo_rules)



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
