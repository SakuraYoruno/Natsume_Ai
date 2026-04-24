from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from host_brain.app.cortex_prompt_builder import build_cortex_prompt


def _plane() -> SimpleNamespace:
    return SimpleNamespace(
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
        conversation_interpretation=SimpleNamespace(
            state=SimpleNamespace(
                as_dict=lambda: {
                    "literal_topic": "identity",
                    "interaction_move": "reconfirm",
                    "freshness": "recent_repeat",
                    "user_stance": "probing",
                    "repeat_question_count": 1,
                }
            )
        ),
        topic_ledger=SimpleNamespace(
            state=SimpleNamespace(
                as_dict=lambda: {
                    "active_topics": ["identity"],
                    "recently_answered_topics": ["identity"],
                    "entries": [],
                }
            )
        ),
        tactic_ledger=SimpleNamespace(
            state=SimpleNamespace(
                as_dict=lambda: {
                    "recent_topics": ["identity"],
                    "entries": [
                        {
                            "topic_key": "identity",
                            "tactic_family": "ack_name_then_wait",
                            "trajectory_stage": "early_repeat",
                        }
                    ],
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
        reply_plan=SimpleNamespace(
            state={
                "affect_summary": "当前情绪平稳，语气自然收着。",
                "relationship_summary": "你和对方还不熟，不要过度亲近。",
                "reply_contract": "这轮不是第一次问身份，用短确认回应，不要完整自报。",
                "response_goal": "identify",
                "tactic_family": "ack_repeat_then_light_probe",
                "primary_topic": "identity",
            }
        ),
    )


def _compiled_soul(*, compact: str | None = None, full: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        runtime_compact_text=compact
        or "你是新海天。白泉学园一年级生，也是哥哥身边那个活泼黏人的妹妹。说话自然、热闹、会撒娇，但不要模板化甜言蜜语，也不要系统腔。",
        runtime_full_text=full
        or "【身份核心】\n你是新海天。白泉学园一年级生，哥哥的新海翔是你最重要的人。\n\n【语言风格】\n说话活泼、爱撒娇、嘴甜，但不用模板化甜言蜜语。",
    )


def test_build_cortex_prompt_returns_messages() -> None:
    with patch(
        "host_brain.app.chat_runtime_input.build_chat_runtime_input",
        return_value={"runtime": "input"},
    ), patch(
        "host_brain.app.chat_payload_builder.run_build_chat_prompt_runtime_payload",
        return_value={"gen_params": {"temperature": 0.2}},
    ), patch(
        "host_brain.app.chat_payload_builder.run_build_chat_prompt_runtime_payload_lightweight",
        return_value={"gen_params": {"temperature": 0.2}},
    ), patch(
        "host_brain.integrations.llm_helpers.adjust_gen_params_for_emotion",
        side_effect=lambda payload, emotion_context: payload,
    ), patch(
        "host_brain.app.cortex_prompt_builder.assemble_prompt_context_plane",
        return_value=_plane(),
    ), patch(
        "host_brain.app.cortex_prompt_builder.get_compiled_soul",
        return_value=_compiled_soul(),
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
    assert "你是新海天。" in messages[0]["content"]
    assert "【人格核心】" in messages[0]["content"]
    assert "当前情绪平稳，语气自然收着。" in messages[0]["content"]
    assert "你和对方还不熟，不要过度亲近。" in messages[0]["content"]
    assert "这轮不是第一次问身份，用短确认回应，不要完整自报。" in messages[0]["content"]
    assert "【更早对话摘要】" not in messages[0]["content"]
    assert "角色卡" not in messages[0]["content"]
    assert "风格手册" not in messages[0]["content"]
    assert messages[1:3] == [
        {"role": "user", "content": "前一轮"},
        {"role": "assistant", "content": "前一轮回复"},
    ]
    assert messages[3] == {"role": "user", "content": "你是谁"}


def test_build_cortex_prompt_uses_full_soul_for_non_relational_chat() -> None:
    with patch(
        "host_brain.app.chat_runtime_input.build_chat_runtime_input",
        return_value={"runtime": "input"},
    ), patch(
        "host_brain.app.chat_payload_builder.run_build_chat_prompt_runtime_payload",
        return_value={"gen_params": {"temperature": 0.2}},
    ), patch(
        "host_brain.app.chat_payload_builder.run_build_chat_prompt_runtime_payload_lightweight",
        return_value={"gen_params": {"temperature": 0.2}},
    ), patch(
        "host_brain.integrations.llm_helpers.adjust_gen_params_for_emotion",
        side_effect=lambda payload, emotion_context: payload,
    ), patch(
        "host_brain.app.cortex_prompt_builder.assemble_prompt_context_plane",
        return_value=_plane(),
    ), patch(
        "host_brain.app.cortex_prompt_builder.get_compiled_soul",
        return_value=_compiled_soul(),
    ):
        messages, _gen_params, _runtime_payload = build_cortex_prompt(
            user_message="今天有点累",
            render_target_lang="zh",
            person_id="p1",
            session_id="sess",
            request_id="req",
            include_runtime_payload=True,
        )

    assert "【身份核心】" in messages[0]["content"]
    assert "【语言风格】" in messages[0]["content"]
    assert "【更早对话摘要】" not in messages[0]["content"]


def test_build_cortex_prompt_exposes_conversation_history_meta() -> None:
    with patch(
        "host_brain.app.chat_runtime_input.build_chat_runtime_input",
        return_value={"runtime": "input"},
    ), patch(
        "host_brain.app.chat_payload_builder.run_build_chat_prompt_runtime_payload",
        return_value={"gen_params": {"temperature": 0.2}},
    ), patch(
        "host_brain.app.chat_payload_builder.run_build_chat_prompt_runtime_payload_lightweight",
        return_value={"gen_params": {"temperature": 0.2}},
    ), patch(
        "host_brain.integrations.llm_helpers.adjust_gen_params_for_emotion",
        side_effect=lambda payload, emotion_context: payload,
    ), patch(
        "host_brain.app.cortex_prompt_builder.assemble_prompt_context_plane",
        return_value=_plane(),
    ), patch(
        "host_brain.app.cortex_prompt_builder.get_compiled_soul",
        return_value=_compiled_soul(),
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
    assert runtime_payload["conversation_interpretation"] == {
        "literal_topic": "identity",
        "interaction_move": "reconfirm",
        "freshness": "recent_repeat",
        "user_stance": "probing",
        "repeat_question_count": 1,
    }
    assert runtime_payload["topic_ledger"]["active_topics"] == ["identity"]
    assert "tactic_ledger" in runtime_payload
    assert runtime_payload["affect_state"]["emotion_tag"] == "CALM"
    assert runtime_payload["relationship_state"]["bond_stage"] == "STRANGER"
    assert runtime_payload["drive_state"]["continuity_drive"] == 0.42


def test_build_cortex_prompt_uses_lightweight_payload_for_relational_chat() -> None:
    with patch(
        "host_brain.app.chat_runtime_input.build_chat_runtime_input",
        return_value={"runtime": "input"},
    ), patch(
        "host_brain.app.chat_payload_builder.run_build_chat_prompt_runtime_payload",
    ) as full_builder, patch(
        "host_brain.app.chat_payload_builder.run_build_chat_prompt_runtime_payload_lightweight",
        return_value={"gen_params": {"temperature": 0.2}, "prompt_runtime_profile": "relational_chat:identity"},
    ) as light_builder, patch(
        "host_brain.integrations.llm_helpers.adjust_gen_params_for_emotion",
        side_effect=lambda payload, emotion_context: payload,
    ), patch(
        "host_brain.app.cortex_prompt_builder.assemble_prompt_context_plane",
        return_value=_plane(),
    ) as assemble_plane, patch(
        "host_brain.app.cortex_prompt_builder.get_compiled_soul",
        return_value=_compiled_soul(),
    ):
        messages, _gen_params, runtime_payload = build_cortex_prompt(
            user_message="你是",
            render_target_lang="zh",
            person_id="p1",
            session_id="sess",
            request_id="req",
            include_runtime_payload=True,
        )

    full_builder.assert_not_called()
    light_builder.assert_called_once()
    assemble_plane.assert_called_once()
    _, kwargs = assemble_plane.call_args
    assert messages[-1] == {"role": "user", "content": "你是"}
    assert runtime_payload["prompt_runtime_profile"] == "relational_chat:identity"
    assert "【更早对话摘要】" not in messages[0]["content"]
    assert "你是新海天。白泉学园一年级生" in messages[0]["content"]
    assert kwargs["history_max_turns"] == 6
    assert kwargs["allow_older_summary"] is False


def test_build_cortex_prompt_uses_reply_contract_once() -> None:
    plane = _plane()
    plane.reply_plan = SimpleNamespace(
        state={
            "affect_summary": "当前情绪平稳，语气自然收着。",
            "relationship_summary": "你和对方还不熟，不要过度亲近。",
            "reply_contract": "这轮不是第一次问身份，用短确认回应，不要完整自报。",
        }
    )
    with patch(
        "host_brain.app.chat_runtime_input.build_chat_runtime_input",
        return_value={"runtime": "input"},
    ), patch(
        "host_brain.app.chat_payload_builder.run_build_chat_prompt_runtime_payload",
        return_value={"gen_params": {"temperature": 0.2}},
    ), patch(
        "host_brain.app.chat_payload_builder.run_build_chat_prompt_runtime_payload_lightweight",
        return_value={"gen_params": {"temperature": 0.2}},
    ), patch(
        "host_brain.integrations.llm_helpers.adjust_gen_params_for_emotion",
        side_effect=lambda payload, emotion_context: payload,
    ), patch(
        "host_brain.app.cortex_prompt_builder.assemble_prompt_context_plane",
        return_value=plane,
    ), patch(
        "host_brain.app.cortex_prompt_builder.get_compiled_soul",
        return_value=_compiled_soul(),
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
    assert messages[0]["content"].count("这轮不是第一次问身份，用短确认回应，不要完整自报。") == 1
    assert "【本轮回应动作】" in messages[0]["content"]
    assert "当前情绪平稳，语气自然收着。" in messages[0]["content"]
    assert "你和对方还不熟，不要过度亲近。" in messages[0]["content"]
    assert [message["role"] for message in messages] == ["system", "user", "assistant", "user"]
