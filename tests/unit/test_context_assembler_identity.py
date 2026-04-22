from __future__ import annotations

import json
from pathlib import Path

import pytest

from host_brain.app.context.identity_source import (
    _CHARACTER_PROFILE_CACHE,
    _STYLE_V5_CACHE,
    build_character_profile_disambiguation,
    build_style_usage_disambiguation,
    load_character_profile,
    load_v5_style_instruction,
)
from host_brain.app.context.prompt_context_plane import assemble_prompt_context_plane


def test_identity_source_loaders_read_canonical_assets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile_path = tmp_path / "character_profile.json"
    profile_path.write_text(
        json.dumps({"instruction_cn": "蓝" * 120}, ensure_ascii=False),
        encoding="utf-8",
    )
    style_path = tmp_path / "style_profile_v5.json"
    style_path.write_text(
        json.dumps(
            {
                "instruction_cn": "中" * 120,
                "instruction_ja": "日" * 120,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("NATSUME_CHARACTER_PROFILE_PATH", str(profile_path))
    monkeypatch.setenv("NATSUME_STYLE_V5_PATH", str(style_path))
    _CHARACTER_PROFILE_CACHE.clear()
    _STYLE_V5_CACHE.clear()

    assert load_character_profile("ja") == "蓝" * 120
    assert load_v5_style_instruction("zh") == "中" * 120
    assert load_v5_style_instruction("ja") == "日" * 120
    assert "不是 AI" in build_character_profile_disambiguation("zh")
    assert "AI" in build_style_usage_disambiguation("zh")


def test_context_assembler_builds_identity_block_from_runtime_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "host_brain.app.context.prompt_context_plane.load_character_profile",
        lambda render_lang: "角色卡",
    )
    monkeypatch.setattr(
        "host_brain.app.context.prompt_context_plane.resolve_prompt_context_style_row",
        lambda prompt_context, node_id: {
            "style_instruction": "风格手册",
            "citation": "style://canon",
        },
    )
    monkeypatch.setattr(
        "host_brain.app.prompt_builder_utils._build_character_dynamic_patch",
        lambda **kwargs: "\n\n【今日状态补丁】\n- calm",
    )

    runtime_payload = {
        "request": {"user_message": "你好", "request_id": "req-1", "session_id": "sess-1", "person_id": "p1"},
        "render": {"target_lang": "zh"},
        "prompt_context": {
            "payload": {
                "bond_summary": {"stage": "STRANGER"},
            }
        },
        "emotion_context": {"emotion_tag": "CALM"},
        "retrieval": {"status": "OK", "evidence_items": [{"id": "e1"}]},
    }

    class _FakeHistory:
        def fetch_window_for_messages(self, **kwargs):  # noqa: ANN003
            from host_brain.conversation.history import ConversationWindow
            from host_brain.conversation.schema import Turn

            assert kwargs["session_id"] == "sess-1"
            return ConversationWindow(
                recent_turns=[
                    Turn(
                        turn_id="t1",
                        session_id="sess-1",
                        person_id="p1",
                        request_id="req-0",
                        role="user",
                        content="前一轮",
                        ts_ms=1,
                        turn_index=0,
                    )
                ],
                older_summary="更早些时候，对方提到过天气。",
                fetched_count=1,
                injected_count=1,
                older_turn_count=2,
                oldest_ts_ms=1,
                newest_ts_ms=1,
            )

    monkeypatch.setattr(
        "host_brain.app.dependencies._conversation_history",
        _FakeHistory(),
        raising=False,
    )

    plane = assemble_prompt_context_plane(
        node_id="cortex_orchestrator",
        runtime_payload=runtime_payload,
        turn_context_sections=["【当前轮上下文】\n子 Agent 结果"],
    )

    assert plane.identity.render_target_lang == "zh"
    assert plane.identity.character_profile == "角色卡"
    assert "不是 AI" in plane.identity.profile_disambiguation
    assert plane.identity.style_instruction == "风格手册"
    assert "说话方式资料" in plane.identity.style_disambiguation
    assert plane.identity.style_citation == "style://canon"
    assert "今日状态补丁" in plane.identity.character_dynamic_patch
    assert plane.continuity.summary == "更早些时候，对方提到过天气。"
    assert plane.continuity.turns_injected == 1
    assert plane.continuity.recent_turns[0]["content"] == "前一轮"
    assert plane.dialogue.state.current_user_act == "neutral_short_query"
    assert plane.intent.turn_context_sections == ("【当前轮上下文】\n子 Agent 结果",)
