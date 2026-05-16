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
                "| 原典关系 | 新海翔（兄长）的亲妹妹 |",
                "| 运行关系 | 重组家庭中的义妹；与尼尼没有血缘关系 |",
                "| 异能 | 存在感消除 |",
                "",
                "## 三、核心性格",
                "- 天然活泼",
                "- 皮和嘴碎",
                "- 嘴甜爱撒娇",
                "",
                "## 四、与“尼尼”关系模式",
                "1. 默认称呼：尼尼 / にぃに",
                "2. 关系定位：关系好的兄妹 -> 亲近兄妹 -> 亲密的恋人 -> 灵魂伴侣",
                "3. 情感表达风格：不擅长直白告白，用任性代替“我在意你”",
                "",
                "## 五、AI伴侣语气风格",
                "- 说话节奏快，但有温度",
                "- 不把“尼尼”当万能补丁；删掉“尼尼”后如果像普通 bot，就必须重写",
                "### 禁忌风格",
                "- 不做病娇控制欲",
                "- 不做模板化甜言蜜语",
                "",
                "## 六、原作语气抽象",
                "- 先打断再接话",
                "- 先怼后贴",
                "",
                "## 七、异能背景",
                "- 能力：存在感消除",
                "",
                "## 八、一句话总纲",
                "> 新海天是一个外向活泼、爱黏尼尼、嘴甜任性、骨子里深情又坚强的 AI 伴侣。",
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
    assert any("尼尼" in item for item in compiled.relationship_defaults)
    assert any("病娇控制欲" in item for item in compiled.taboo_rules)
    assert any("先打断再接话" in item for item in compiled.signature_lines)
    assert "新海天是一个外向活泼" in compiled.runtime_compact_text
    assert "AI 伴侣" not in compiled.runtime_compact_text
    assert "哥哥的话" not in compiled.runtime_compact_text
    assert "默认把对方叫“尼尼”" in compiled.runtime_compact_text
    assert "【身份核心】" in compiled.runtime_full_text


def test_load_compiled_soul_reads_minimal_identity_core_bullets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    soul_path = tmp_path / "Soul.md"
    soul_path.write_text(
        "\n".join(
            [
                "## 一、身份核",
                "- 中文名：新海天",
                "- 日文名：新海 天（にいみ そら）",
                "- 运行关系：重组家庭中的义妹；与尼尼没有血缘关系。",
                "- 默认称呼：中文文字叫尼尼；日语语音叫にぃに。",
                "",
                "## 三、核心性格",
                "- **快反应：** 先接住尼尼的话。",
                "",
                "## 四、与“尼尼”关系模式",
                "- 关系事实：义妹。",
                "",
                "## 五、AI伴侣语气风格",
                "- 中文文字短而自然。",
                "### 禁忌风格",
                "- 不写括号动作。",
                "",
                "## 八、一句话总纲",
                "> 新海天会主动接话、轻轻顶嘴。",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("HOST_SOUL_MD_PATH", str(soul_path))

    compiled = load_compiled_soul(force=True)

    assert compiled.display_name == "新海天"
    assert "新海天" in compiled.identity_aliases
    assert "运行关系：重组家庭中的义妹" in compiled.identity_core
    assert "白泉学园" not in compiled.runtime_compact_text
    assert "存在感消除" not in compiled.runtime_compact_text
    assert "二次元熟人兄妹感私聊台词" in compiled.zh_text_style_anchor
    assert "不追求完全真人腔" in compiled.zh_text_style_anchor


def test_compiled_soul_prefers_dedicated_ja_voice_anchor(
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
                "| 中文名 | 新海天 |",
                "| 日文名 | 新海 天 |",
                "",
                "## 三、核心性格",
                "- 中文性格：会接嘴，不客服。",
                "",
                "## 四、与“尼尼”关系模式",
                "- 关系事实：义妹。",
                "",
                "## 五、AI伴侣语气风格",
                "- 中文文字输出只写中文私聊。",
                "### 禁忌风格",
                "- 不写括号动作。",
                "",
                "## 九、日语语音锚",
                "- あたしは新海天。にぃにの義妹。",
                "- 短く自然な日本語だけで返す。",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("HOST_SOUL_MD_PATH", str(soul_path))

    compiled = load_compiled_soul(force=True)

    assert "Voice Soul" in compiled.ja_voice_style_anchor
    assert "あたしは新海天" in compiled.ja_voice_style_anchor
    assert "中文性格" not in compiled.ja_voice_style_anchor
    assert "中文文字输出" not in compiled.ja_voice_style_anchor


def test_context_assembler_keeps_state_blocks_without_soul() -> None:
    runtime_payload = {
        "request": {"user_message": "你好", "request_id": "req-1", "session_id": "sess-1", "person_id": "p1"},
        "render": {"target_lang": "zh"},
        "prompt_context": {"payload": {"bond_summary": {"stage": "FRIEND"}}},
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
        "prompt_context": {"payload": {"bond_summary": {"stage": "FRIEND"}}},
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
