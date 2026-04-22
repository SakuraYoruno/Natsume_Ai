from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from host_brain.app.cortex_prompt_builder import build_cortex_prompt


def _plane() -> SimpleNamespace:
    return SimpleNamespace(
        identity=SimpleNamespace(
            profile_disambiguation="不是 AI 助手。",
            character_profile="角色卡",
            character_dynamic_patch="",
            style_disambiguation="不是客服腔。",
            style_instruction="风格手册",
            style_citation="",
        ),
        continuity=SimpleNamespace(
            recent_turns=[
                {"role": "user", "content": "前一轮"},
                {"role": "assistant", "content": "前一轮回复"},
            ],
            summary="更早些时候，对方提到过昨天的事。",
            turns_injected=2,
            fetched_turns=2,
            older_turn_count=3,
            oldest_ts_ms=111,
            newest_ts_ms=222,
            skipped_corrupted_count=0,
            skipped_empty_count=0,
            deduped_same_role_count=0,
        ),
        dialogue=SimpleNamespace(
            state=SimpleNamespace(
                as_dict=lambda: {
                    "current_user_act": "identity_probe",
                    "repeat_question_count": 1,
                }
            )
        ),
        topic=SimpleNamespace(
            state=SimpleNamespace(
                as_dict=lambda: {
                    "active_topics": ["identity"],
                    "recently_answered_topics": ["identity"],
                    "entries": [],
                }
            )
        ),
        affect=SimpleNamespace(
            state=SimpleNamespace(
                as_dict=lambda: {
                    "emotion_tag": "CALM",
                    "social_energy": 0.62,
                    "withdraw_bias": 0.18,
                }
            )
        ),
        relationship=SimpleNamespace(
            state=SimpleNamespace(
                as_dict=lambda: {
                    "bond_stage": "STRANGER",
                    "trust": 0.12,
                    "openness": 0.14,
                    "boundary_softness": 0.10,
                }
            )
        ),
        drive=SimpleNamespace(
            state=SimpleNamespace(
                as_dict=lambda: {
                    "continuity_drive": 0.42,
                    "self_protection_drive": 0.22,
                }
            )
        ),
        intent=SimpleNamespace(turn_context_sections=()),
        render_policy=SimpleNamespace(state={}),
    )


class _FakeUtteranceIndex:
    is_built = True

    def retrieve_for_prompt(self, *args, **kwargs):  # noqa: ANN002, ANN003
        return {"samples": ["dummy"]}

    def render_for_prompt(self, result, *, lang: str = "cn") -> str:  # noqa: ANN001
        _ = (result, lang)
        return "【台词风格参考】\n- 示例台词"


def test_build_cortex_prompt_returns_messages() -> None:
    with patch(
        "host_brain.app.chat_runtime_input.build_chat_runtime_input",
        return_value={"runtime": "input"},
    ), patch(
        "host_brain.app.chat_payload_builder.run_build_chat_prompt_runtime_payload",
        return_value={"gen_params": {"temperature": 0.2}},
    ), patch(
        "host_brain.app.prompt_builder._build_node_role_instruction",
        return_value="节点指令",
    ), patch(
        "host_brain.integrations.llm_helpers.adjust_gen_params_for_emotion",
        side_effect=lambda payload, emotion_context: payload,
    ), patch(
        "host_brain.app.cortex_prompt_builder.assemble_prompt_context_plane",
        return_value=_plane(),
    ), patch(
        "host_brain.retrieval.emotion_utterance_index.get_utterance_index",
        return_value=_FakeUtteranceIndex(),
    ):
        messages, gen_params, _runtime_payload = build_cortex_prompt(
            user_message="你是谁",
            render_target_lang="zh",
            person_id="p1",
            session_id="sess",
            request_id="req",
            include_runtime_payload=True,
        )

    assert gen_params == {"temperature": 0.2}
    assert messages[0]["role"] == "system"
    assert "不是 AI 助手。" in messages[0]["content"]
    assert "角色卡" in messages[0]["content"]
    assert "不是客服腔。" in messages[0]["content"]
    assert "【台词风格参考】" in messages[0]["content"]
    assert "【更早对话摘要】" in messages[0]["content"]
    assert "更早些时候，对方提到过昨天的事。" in messages[0]["content"]
    assert messages[1:3] == [
        {"role": "user", "content": "前一轮"},
        {"role": "assistant", "content": "前一轮回复"},
    ]
    assert messages[3] == {"role": "user", "content": "你是谁"}


def test_build_cortex_prompt_merges_turn_context_into_single_system_prompt() -> None:
    plane = _plane()
    plane.intent = SimpleNamespace(turn_context_sections=("【当前轮上下文】\n子 Agent 提示", "【计划】\n先接住对方。"))
    with patch(
        "host_brain.app.chat_runtime_input.build_chat_runtime_input",
        return_value={"runtime": "input"},
    ), patch(
        "host_brain.app.chat_payload_builder.run_build_chat_prompt_runtime_payload",
        return_value={"gen_params": {"temperature": 0.2}},
    ), patch(
        "host_brain.app.prompt_builder._build_node_role_instruction",
        return_value="节点指令",
    ), patch(
        "host_brain.integrations.llm_helpers.adjust_gen_params_for_emotion",
        side_effect=lambda payload, emotion_context: payload,
    ), patch(
        "host_brain.app.cortex_prompt_builder.assemble_prompt_context_plane",
        return_value=plane,
    ), patch(
        "host_brain.retrieval.emotion_utterance_index.get_utterance_index",
        return_value=_FakeUtteranceIndex(),
    ):
        messages, _gen_params, _runtime_payload = build_cortex_prompt(
            user_message="你是谁",
            render_target_lang="zh",
            person_id="p1",
            session_id="sess",
            request_id="req",
            include_runtime_payload=True,
        )

    assert messages[0]["role"] == "system"
    assert "【当前轮上下文】" in messages[0]["content"]
    assert "子 Agent 提示" in messages[0]["content"]
    assert "先接住对方。" in messages[0]["content"]
    assert [message["role"] for message in messages] == ["system", "user", "assistant", "user"]


def test_build_cortex_prompt_exposes_conversation_history_meta() -> None:
    with patch(
        "host_brain.app.chat_runtime_input.build_chat_runtime_input",
        return_value={"runtime": "input"},
    ), patch(
        "host_brain.app.chat_payload_builder.run_build_chat_prompt_runtime_payload",
        return_value={"gen_params": {"temperature": 0.2}},
    ), patch(
        "host_brain.app.prompt_builder._build_node_role_instruction",
        return_value="节点指令",
    ), patch(
        "host_brain.integrations.llm_helpers.adjust_gen_params_for_emotion",
        side_effect=lambda payload, emotion_context: payload,
    ), patch(
        "host_brain.app.cortex_prompt_builder.assemble_prompt_context_plane",
        return_value=_plane(),
    ), patch(
        "host_brain.retrieval.emotion_utterance_index.get_utterance_index",
        return_value=_FakeUtteranceIndex(),
    ):
        _messages, _gen_params, runtime_payload = build_cortex_prompt(
            user_message="你是谁",
            render_target_lang="zh",
            person_id="p1",
            session_id="sess",
            request_id="req",
            include_runtime_payload=True,
        )

    assert runtime_payload["conversation_history"] == {
        "turns_injected": 2,
        "fetched_turns": 2,
        "older_turn_count": 3,
        "summary_present": True,
        "oldest_ts_ms": 111,
        "newest_ts_ms": 222,
        "skipped_corrupted_count": 0,
        "skipped_empty_count": 0,
        "deduped_same_role_count": 0,
    }
    assert runtime_payload["dialogue_state"] == {
        "current_user_act": "identity_probe",
        "repeat_question_count": 1,
    }
    assert runtime_payload["topic_state"]["active_topics"] == ["identity"]
    assert runtime_payload["affect_state"]["emotion_tag"] == "CALM"
    assert runtime_payload["relationship_state"]["bond_stage"] == "STRANGER"
    assert runtime_payload["drive_state"]["continuity_drive"] == 0.42


def test_build_cortex_prompt_appends_control_line_once() -> None:
    plane = _plane()
    plane.render_policy = SimpleNamespace(
        state={
            "affect_summary": "当前内在状态平稳。",
            "relationship_summary": "当前关系仍偏陌生。",
            "dialogue_summary": "旧的抽象说明",
            "topic_summary": "旧的topic说明",
            "render_policy_summary": "旧的策略说明",
            "control_line": "这句不是第一次问身份。先接住对方的重复确认，不要重说名字。"
        }
    )
    with patch(
        "host_brain.app.chat_runtime_input.build_chat_runtime_input",
        return_value={"runtime": "input"},
    ), patch(
        "host_brain.app.chat_payload_builder.run_build_chat_prompt_runtime_payload",
        return_value={"gen_params": {"temperature": 0.2}},
    ), patch(
        "host_brain.app.prompt_builder._build_node_role_instruction",
        return_value="节点指令",
    ), patch(
        "host_brain.integrations.llm_helpers.adjust_gen_params_for_emotion",
        side_effect=lambda payload, emotion_context: payload,
    ), patch(
        "host_brain.app.cortex_prompt_builder.assemble_prompt_context_plane",
        return_value=plane,
    ), patch(
        "host_brain.retrieval.emotion_utterance_index.get_utterance_index",
        return_value=_FakeUtteranceIndex(),
    ):
        messages, _gen_params, _runtime_payload = build_cortex_prompt(
            user_message="你谁",
            render_target_lang="zh",
            person_id="p1",
            session_id="sess",
            request_id="req",
            include_runtime_payload=True,
        )

    assert messages[0]["role"] == "system"
    assert messages[0]["content"].count("这句不是第一次问身份。先接住对方的重复确认，不要重说名字。") == 1
    assert "当前内在状态平稳。" not in messages[0]["content"]
    assert "当前关系仍偏陌生。" not in messages[0]["content"]
    assert "旧的抽象说明" not in messages[0]["content"]
    assert "旧的topic说明" not in messages[0]["content"]
    assert "旧的策略说明" not in messages[0]["content"]
    assert [message["role"] for message in messages] == ["system", "user", "assistant", "user"]
