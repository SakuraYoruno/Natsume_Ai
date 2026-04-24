from __future__ import annotations

from pathlib import Path

import pytest

from host_brain.app.context.prompt_context_plane import assemble_prompt_context_plane
from host_brain.app.context.soul_runtime import CompiledSoul, load_compiled_soul


def test_load_compiled_soul_reads_markdown_and_derives_runtime_texts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    soul_path = tmp_path / "Soul.md"
    soul_path.write_text(
        "\n".join(
            [
                "## 一、基本角色档案",
                "| 属性 | 信息 |",
                "|------|------|",
                "| 中文名 | 新海 天 |",
                "| 学校 | 白泉学园 一年级生 |",
                "| 关系 | 新海翔（哥哥）的亲妹妹 |",
                "| 异能 | 存在感消除 |",
                "",
                "## 三、核心性格",
                "- 天然活泼",
                "- 嘴甜爱撒娇",
                "",
                "## 四、与“哥哥”关系模式",
                "1. 默认称呼：哥哥 / お兄ちゃん",
                "2. 情感表达风格：不擅长直白告白，用任性代替“我在意你”",
                "",
                "## 五、AI伴侣语气风格",
                "- 说话节奏快，但有温度",
                "- 用“哥哥～”拉长语气撒娇",
                "### 禁忌风格",
                "- 不做病娇控制欲",
                "- 不做模板化甜言蜜语",
                "",
                "## 六、标志性语料",
                "- 哥哥！我来了——",
                "- 你搞不定就说一声，我来帮你。",
                "",
                "## 七、异能背景",
                "- 能力：存在感消除",
                "",
                "## 八、一句话总纲",
                "> 新海天是一个外向活泼、爱黏哥哥、嘴甜任性、骨子里深情又坚强的 AI 伴侣。",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("HOST_SOUL_MD_PATH", str(soul_path))

    compiled = load_compiled_soul(force=True)

    assert isinstance(compiled, CompiledSoul)
    assert compiled.source_path == str(soul_path.resolve())
    assert "一、基本角色档案" in compiled.section_map
    assert "新海 天" in compiled.identity_core
    assert "天然活泼" in compiled.temperament_core
    assert any("哥哥" in item for item in compiled.relationship_defaults)
    assert any("病娇控制欲" in item for item in compiled.taboo_rules)
    assert any("哥哥！我来了" in item for item in compiled.signature_lines)
    assert "新海天是一个外向活泼" in compiled.runtime_compact_text
    assert "AI 伴侣" not in compiled.runtime_compact_text
    assert "【身份核心】" in compiled.runtime_full_text


def test_context_assembler_keeps_state_blocks_without_soul() -> None:
    runtime_payload = {
        "request": {"user_message": "你好", "request_id": "req-1", "session_id": "sess-1", "person_id": "p1"},
        "render": {"target_lang": "zh"},
        "prompt_context": {"payload": {"bond_summary": {"stage": "STRANGER"}}},
        "emotion_context": {"emotion_tag": "CALM"},
        "retrieval": {"status": "OK", "evidence_items": [{"id": "e1"}]},
    }

    plane = assemble_prompt_context_plane(
        node_id="cortex_orchestrator",
        runtime_payload=runtime_payload,
        turn_context_sections=["【当前轮上下文】\n子 Agent 结果"],
    )

    assert hasattr(plane, "continuity")
    assert hasattr(plane, "conversation_interpretation")
    assert hasattr(plane, "reply_plan")
    assert not hasattr(plane, "soul")
    assert plane.intent.turn_context_sections == ("【当前轮上下文】\n子 Agent 结果",)


def test_context_assembler_preserves_multimodal_observations() -> None:
    runtime_payload = {
        "request": {
            "user_message": "这是什么意思",
            "request_id": "req-mm",
            "session_id": "sess-mm",
            "person_id": "p1",
            "attachment_summary": "用户发来一张截图，更像是在问界面上这段提示是什么意思。",
        },
        "render": {"target_lang": "zh"},
        "prompt_context": {"payload": {"bond_summary": {"stage": "STRANGER"}}},
        "emotion_context": {"emotion_tag": "CALM"},
        "retrieval": {"status": "OK", "evidence_items": []},
        "multimodal_input": {
            "observations": {
                "observed_types": ["image"],
                "summary_for_chat": "截图里能看到一段界面提示文字。",
                "core_observation": "界面提示：权限不足，请重试。",
                "visible_text": "权限不足，请重试",
                "artifact_type": "screenshot",
                "primary_modality": "image",
                "followup_required": False,
            }
        },
    }

    plane = assemble_prompt_context_plane(
        node_id="cortex_orchestrator",
        runtime_payload=runtime_payload,
    )

    assert plane.multimodal.state.summary_for_chat.startswith("截图里能看到")
    assert plane.multimodal.state.artifact_type == "screenshot"
    assert plane.reply_plan.state["response_form"] == "text"
