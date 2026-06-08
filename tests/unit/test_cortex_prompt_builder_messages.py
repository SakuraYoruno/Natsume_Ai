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
        interaction_thread=SimpleNamespace(
            state=SimpleNamespace(
                as_dict=lambda: {
                    "thread_kind": "identity_loop",
                    "source_topic": "identity",
                    "source_modality": "",
                    "emotion_anchor": "",
                    "open": True,
                    "source_turn_index": 0,
                    "last_assistant_move": "",
                    "last_user_reaction": "",
                    "turn_count": 2,
                    "reason_tags": ["recent_identity_loop"],
                }
            )
        ),
        interaction_state=SimpleNamespace(
            state=SimpleNamespace(
                as_dict=lambda: {
                    "current_topic": "identity",
                    "interaction_move": "reconfirm",
                    "user_stance": "probing",
                    "followup_target": "identity",
                    "ongoing_thread": "identity_loop",
                    "affect": {"emotion_tag": "CALM"},
                    "relationship": {"bond_stage": "FRIEND"},
                    "drive": {"continuity_drive": 0.42},
                    "topic_ledger": {"active_topics": ["identity"]},
                    "tactic_ledger": {"recent_topics": ["identity"]},
                    "perception_summary": "",
                    "reason_tags": ["recent_identity_loop"],
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
                    "bond_stage": "FRIEND",
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
        multimodal=SimpleNamespace(
            state=SimpleNamespace(
                summary_for_chat="",
                as_dict=lambda: {
                    "observed_types": [],
                    "summary_for_chat": "",
                    "visible_text": "",
                    "artifact_type": "",
                    "primary_modality": "",
                    "emotion_signals": [],
                    "confidence": 0.0,
                    "followup_required": False,
                },
            )
        ),
        intent=SimpleNamespace(turn_context_sections=()),
        reply_plan=SimpleNamespace(
            state={
                "affect_summary": "当前情绪平稳，语气自然收着。",
                "relationship_summary": "义妹关系已成立，但还在初次相处阶段；语气收着，不要装陌生也不要过度亲密。",
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

    assert gen_params == {}
    assert messages[0]["role"] == "system"
    assert "【人格核心】" in messages[0]["content"]
    assert "你是新海天。" in messages[0]["content"]
    assert "【普通聊天写法】" not in messages[0]["content"]
    assert "这轮是在接连续身份追问" in messages[0]["content"]
    assert "当前情绪平稳，语气自然收着。" in messages[0]["content"]
    assert "义妹关系已成立，但还在初次相处阶段" in messages[0]["content"]
    assert "这轮不是第一次问身份，用短确认回应，不要完整自报。" not in messages[0]["content"]
    assert "【本轮状态标签】" in messages[0]["content"]
    assert "topic=identity" in messages[0]["content"]
    assert "thread=identity_loop" in messages[0]["content"]
    assert "【更早对话摘要】" not in messages[0]["content"]
    assert "角色卡" not in messages[0]["content"]
    assert "风格手册" not in messages[0]["content"]
    assert messages[1:3] == [
        {"role": "user", "content": "前一轮"},
        {"role": "assistant", "content": "前一轮回复"},
    ]
    assert messages[3] == {"role": "user", "content": "你是谁"}


def test_build_cortex_prompt_can_skip_reply_contract_prompt(monkeypatch) -> None:
    monkeypatch.setenv("HOST_CHAT_DISABLE_REPLY_CONTRACT_PROMPT", "true")
    with patch(
        "host_brain.app.cortex_prompt_builder.assemble_prompt_context_plane",
        return_value=_plane(),
    ), patch(
        "host_brain.app.cortex_prompt_builder.get_compiled_soul",
        return_value=_compiled_soul(),
    ):
        messages, _gen_params, runtime_payload = build_cortex_prompt(
            user_message="你是谁",
            render_target_lang="zh",
            person_id="p1",
            session_id="sess",
            request_id="req",
            include_runtime_payload=True,
        )

    assert "【本轮目标】" not in messages[0]["content"]
    assert "【本轮状态标签】" not in messages[0]["content"]
    assert "这轮不是第一次问身份，用短确认回应，不要完整自报。" not in messages[0]["content"]
    assert runtime_payload["reply_plan"]["reply_contract"] == "这轮不是第一次问身份，用短确认回应，不要完整自报。"


def test_build_cortex_prompt_can_use_legacy_reply_contract_prompt(monkeypatch) -> None:
    monkeypatch.setenv("HOST_CHAT_LEGACY_REPLY_CONTRACT_PROMPT", "true")
    with patch(
        "host_brain.app.cortex_prompt_builder.assemble_prompt_context_plane",
        return_value=_plane(),
    ), patch(
        "host_brain.app.cortex_prompt_builder.get_compiled_soul",
        return_value=_compiled_soul(),
    ):
        messages, _gen_params, _runtime_payload = build_cortex_prompt(
            user_message="你是谁",
            render_target_lang="zh",
            person_id="p1",
            session_id="sess",
            request_id="req",
            include_runtime_payload=True,
        )

    assert "【本轮目标】" in messages[0]["content"]
    assert "这轮不是第一次问身份，用短确认回应，不要完整自报。" in messages[0]["content"]
    assert "【本轮状态标签】" not in messages[0]["content"]


def test_build_cortex_prompt_uses_full_soul_for_short_text_chat() -> None:
    plane = _plane()
    plane.conversation_interpretation = SimpleNamespace(
        state=SimpleNamespace(
            as_dict=lambda: {
                "literal_topic": "mood_reflection",
                "interaction_move": "share_state",
                "freshness": "new",
                "user_stance": "open",
                "repeat_question_count": 0,
            }
        )
    )
    plane.interaction_thread = SimpleNamespace(
        state=SimpleNamespace(
            as_dict=lambda: {
                "thread_kind": "",
                "source_topic": "",
                "source_modality": "",
                "emotion_anchor": "",
                "open": False,
                "source_turn_index": 0,
                "last_assistant_move": "",
                "last_user_reaction": "",
                "turn_count": 0,
                "reason_tags": [],
            }
        )
    )
    plane.interaction_state = SimpleNamespace(
        state=SimpleNamespace(
            as_dict=lambda: {
                "current_topic": "mood_reflection",
                "interaction_move": "share_state",
                "user_stance": "open",
                "followup_target": "",
                "ongoing_thread": "",
                "affect": {"emotion_tag": "CALM"},
                "relationship": {"bond_stage": "FRIEND"},
                "drive": {"continuity_drive": 0.42},
                "topic_ledger": {"active_topics": ["mood_reflection"]},
                "tactic_ledger": {"recent_topics": ["mood_reflection"]},
                "perception_summary": "",
                "reason_tags": [],
            }
        )
    )
    plane.reply_plan = SimpleNamespace(
        state={
            "affect_summary": "当前情绪平稳，语气自然收着。",
            "relationship_summary": "义妹关系已成立，但还在初次相处阶段；语气收着，不要装陌生也不要过度亲密。",
            "reply_contract": "先接住对方在说自己有点累，再自然回应。",
            "response_goal": "mood_reflection",
            "tactic_family": "plain_hold",
            "primary_topic": "mood_reflection",
        }
    )
    with patch(
        "host_brain.app.cortex_prompt_builder.assemble_prompt_context_plane",
        return_value=plane,
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

    assert "【人格核心】" in messages[0]["content"]
    assert "【身份核心】" in messages[0]["content"]
    assert "【语言风格】" in messages[0]["content"]
    assert "先接住对方在说自己有点累" not in messages[0]["content"]
    assert "topic=mood_reflection" in messages[0]["content"]
    assert "【更早对话摘要】" not in messages[0]["content"]


def test_build_cortex_prompt_exposes_conversation_history_meta() -> None:
    with patch(
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
    assert runtime_payload["interaction_thread"]["thread_kind"] == "identity_loop"
    assert runtime_payload["interaction_state"]["ongoing_thread"] == "identity_loop"
    assert runtime_payload["topic_ledger"]["active_topics"] == ["identity"]
    assert "tactic_ledger" in runtime_payload
    assert runtime_payload["affect_state"]["emotion_tag"] == "CALM"
    assert runtime_payload["relationship_state"]["bond_stage"] == "FRIEND"
    assert runtime_payload["drive_state"]["continuity_drive"] == 0.42


def test_build_cortex_prompt_injects_image_social_thread_summary() -> None:
    plane = _plane()
    plane.conversation_interpretation = SimpleNamespace(
        state=SimpleNamespace(
            as_dict=lambda: {
                "literal_topic": "emoji_social_followup",
                "interaction_move": "repair_needed",
                "freshness": "new",
                "user_stance": "challenging",
                "repeat_question_count": 0,
            }
        )
    )
    plane.interaction_thread = SimpleNamespace(
        state=SimpleNamespace(
            emotion_anchor="distress",
            as_dict=lambda: {
                "thread_kind": "image_social",
                "source_topic": "emoji_social_reply",
                "source_modality": "image",
                "emotion_anchor": "distress",
                "open": True,
                "source_turn_index": 0,
                "last_assistant_move": "image_social_ack",
                "last_user_reaction": "deny",
                "turn_count": 3,
                "reason_tags": ["recent_image_social_assistant"],
            },
        )
    )
    plane.interaction_state = SimpleNamespace(
        state=SimpleNamespace(
            current_topic="emoji_social_followup",
            interaction_move="repair_needed",
            user_stance="challenging",
            followup_target="emoji_social_reply",
            ongoing_thread="image_social",
            perception_summary="用户发来的是一张委屈风格的社交图片。",
            as_dict=lambda: {
                "current_topic": "emoji_social_followup",
                "interaction_move": "repair_needed",
                "user_stance": "challenging",
                "followup_target": "emoji_social_reply",
                "ongoing_thread": "image_social",
                "affect": {"emotion_tag": "CALM"},
                "relationship": {"bond_stage": "FRIEND"},
                "drive": {"continuity_drive": 0.42},
                "topic_ledger": {"active_topics": ["emoji_social_reply"]},
                "tactic_ledger": {"recent_topics": ["emoji_social_reply"]},
                "perception_summary": "用户发来的是一张委屈风格的社交图片。",
                "reason_tags": ["ongoing_thread:image_social"],
            },
        )
    )
    plane.reply_plan = SimpleNamespace(
        state={
            "affect_summary": "当前情绪平稳，语气自然收着。",
            "relationship_summary": "义妹关系已成立，但还在初次相处阶段；语气收着，不要装陌生也不要过度亲密。",
            "reply_contract": "先接住对方是在回刚才那张图，再顺着这层情绪接一句。",
            "response_goal": "gentle_followup_reply",
            "tactic_family": "soft_emotion_ack",
            "primary_topic": "emoji_social_followup",
        }
    )

    with patch(
        "host_brain.app.cortex_prompt_builder.assemble_prompt_context_plane",
        return_value=plane,
    ), patch(
        "host_brain.app.cortex_prompt_builder.get_compiled_soul",
        return_value=_compiled_soul(),
    ):
        messages, _gen_params, _runtime_payload = build_cortex_prompt(
            user_message="没有闹别扭啊",
            render_target_lang="zh",
            person_id="p1",
            session_id="sess",
            request_id="req",
            include_runtime_payload=True,
        )

    assert "这句话是在接上一张社交图片" in messages[0]["content"]
    assert "不要装作第一次听见" in messages[0]["content"]


def test_build_cortex_prompt_uses_full_soul_for_image_social_followup() -> None:
    plane = _plane()
    plane.conversation_interpretation = SimpleNamespace(
        state=SimpleNamespace(
            as_dict=lambda: {
                "literal_topic": "emoji_social_followup",
                "interaction_move": "repair_needed",
                "freshness": "recent_repeat",
                "user_stance": "challenging",
                "repeat_question_count": 0,
            }
        )
    )
    plane.interaction_thread = SimpleNamespace(
        state=SimpleNamespace(
            as_dict=lambda: {
                "thread_kind": "image_social",
                "source_topic": "emoji_social_reply",
                "source_modality": "image",
                "emotion_anchor": "distress",
                "open": True,
                "source_turn_index": 0,
                "last_assistant_move": "image_social_ack",
                "last_user_reaction": "deny",
                "turn_count": 3,
                "reason_tags": ["recent_image_social_assistant"],
            },
        )
    )
    plane.interaction_state = SimpleNamespace(
        state=SimpleNamespace(
            current_topic="emoji_social_followup",
            interaction_move="repair_needed",
            user_stance="challenging",
            followup_target="emoji_social_reply",
            ongoing_thread="image_social",
            perception_summary="用户发来的是一张委屈风格的社交图片。",
            as_dict=lambda: {
                "current_topic": "emoji_social_followup",
                "interaction_move": "repair_needed",
                "user_stance": "challenging",
                "followup_target": "emoji_social_reply",
                "ongoing_thread": "image_social",
                "affect": {"emotion_tag": "CALM"},
                "relationship": {"bond_stage": "FRIEND"},
                "drive": {"continuity_drive": 0.42},
                "topic_ledger": {"active_topics": ["emoji_social_reply"]},
                "tactic_ledger": {"recent_topics": ["emoji_social_reply"]},
                "perception_summary": "用户发来的是一张委屈风格的社交图片。",
                "reason_tags": ["ongoing_thread:image_social"],
            },
        )
    )
    plane.reply_plan = SimpleNamespace(
        state={
            "affect_summary": "当前情绪平稳，语气自然收着。",
            "relationship_summary": "义妹关系已成立，只是刚开始相处；不用刻意拉开，轻轻接住眼前情绪。",
            "reply_contract": "先接住对方是在回刚才那张图，再顺着这层情绪接一句。",
            "response_goal": "gentle_followup_reply",
            "tactic_family": "soft_emotion_ack",
            "primary_topic": "emoji_social_followup",
        }
    )
    assemble_calls: list[dict[str, object]] = []

    def _assemble(*args, **kwargs):
        assemble_calls.append(dict(kwargs))
        return plane

    with patch(
        "host_brain.app.cortex_prompt_builder.assemble_prompt_context_plane",
        side_effect=_assemble,
    ), patch(
        "host_brain.app.cortex_prompt_builder.get_compiled_soul",
        return_value=_compiled_soul(
            compact="你是新海天。说话活泼自然，会顺着眼前的小情绪接住人。",
            full="【身份核心】\n你是新海天。\n\n【语言风格】\n说话活泼、爱撒娇。",
        ),
    ):
        messages, _gen_params, _runtime_payload = build_cortex_prompt(
            user_message="没有闹别扭啊",
            render_target_lang="zh",
            person_id="p1",
            session_id="sess",
            request_id="req",
            include_runtime_payload=True,
        )

    assert len(assemble_calls) == 1
    assert assemble_calls[0]["history_max_turns"] is None
    assert assemble_calls[0]["allow_older_summary"] is True
    assert "【身份核心】" in messages[0]["content"]
    assert "【语言风格】" in messages[0]["content"]
    assert "你是新海天。说话活泼自然，会顺着眼前的小情绪接住人。" not in messages[0]["content"]


def test_build_cortex_prompt_uses_lightweight_payload_for_relational_chat() -> None:
    with patch(
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

    assemble_plane.assert_called_once()
    _, kwargs = assemble_plane.call_args
    assert messages[-1] == {"role": "user", "content": "你是"}
    assert runtime_payload["request"]["user_message"] == "你是"
    assert "【更早对话摘要】" not in messages[0]["content"]
    assert "你是新海天。" in messages[0]["content"]
    assert "【普通聊天写法】" not in messages[0]["content"]
    assert kwargs["history_max_turns"] == 6
    assert kwargs["allow_older_summary"] is False


def test_build_cortex_prompt_uses_structured_reply_state_by_default() -> None:
    plane = _plane()
    plane.reply_plan = SimpleNamespace(
        state={
            "affect_summary": "当前情绪平稳，语气自然收着。",
            "relationship_summary": "义妹关系已成立，但还在初次相处阶段；语气收着，不要装陌生也不要过度亲密。",
            "reply_contract": "这轮不是第一次问身份，用短确认回应，不要完整自报。",
        }
    )
    with patch(
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
    assert "这轮不是第一次问身份，用短确认回应，不要完整自报。" not in messages[0]["content"]
    assert "【本轮目标】" not in messages[0]["content"]
    assert "【本轮状态标签】" in messages[0]["content"]
    assert "topic=identity" in messages[0]["content"]
    assert "thread=identity_loop" in messages[0]["content"]
    assert "当前情绪平稳，语气自然收着。" in messages[0]["content"]
    assert "义妹关系已成立，但还在初次相处阶段；语气收着，不要装陌生也不要过度亲密。" in messages[0]["content"]
    assert "这轮是在接连续身份追问" in messages[0]["content"]
    assert [message["role"] for message in messages] == ["system", "user", "assistant", "user"]


def test_build_cortex_prompt_structured_state_carries_boundary_action_tags() -> None:
    plane = _plane()
    plane.interaction_state = SimpleNamespace(
        state=SimpleNamespace(
            as_dict=lambda: {
                "current_topic": "relationship_boundary",
                "interaction_move": "probe_boundary",
                "user_stance": "challenging",
                "followup_target": "",
                "ongoing_thread": "boundary_probe",
                "affect": {"emotion_tag": "CALM"},
                "relationship": {"bond_stage": "FRIEND"},
                "drive": {"continuity_drive": 0.42},
                "topic_ledger": {"active_topics": ["relationship_boundary"]},
                "tactic_ledger": {"recent_topics": ["relationship_boundary"]},
                "perception_summary": "",
                "reason_tags": ["boundary_probe"],
            }
        )
    )
    plane.reply_plan = SimpleNamespace(
        state={
            "affect_summary": "",
            "relationship_summary": "义妹关系已成立，但还在初次相处阶段；语气收着，不要装陌生也不要过度亲密。",
            "primary_topic": "relationship_boundary",
            "interaction_move": "probe_boundary",
            "response_form": "text",
            "contract": {
                "required_actions": ["boundary_anchor", "reason_clause"],
                "forbidden_actions": ["generic_companioning"],
            },
            "reply_contract": "先放稳义妹关系事实再给短理由。",
        }
    )
    with patch(
        "host_brain.app.cortex_prompt_builder.assemble_prompt_context_plane",
        return_value=plane,
    ), patch(
        "host_brain.app.cortex_prompt_builder.get_compiled_soul",
        return_value=_compiled_soul(),
    ):
        messages, _gen_params, _runtime_payload = build_cortex_prompt(
            user_message="你为什么要帮我，我们很熟吗",
            render_target_lang="zh",
            person_id="p1",
            session_id="sess",
            request_id="req",
            include_runtime_payload=True,
        )

    assert "先放稳义妹关系事实再给短理由。" not in messages[0]["content"]
    assert "topic=relationship_boundary" in messages[0]["content"]
    assert "thread=boundary_probe" in messages[0]["content"]
    assert "required=boundary_anchor,reason_clause" in messages[0]["content"]
    assert "forbidden=generic_companioning" in messages[0]["content"]


def test_build_cortex_prompt_injects_multimodal_summary_when_present() -> None:
    plane = _plane()
    plane.multimodal = SimpleNamespace(
        state=SimpleNamespace(
            summary_for_chat="截图里能看到一段界面提示文字。",
            as_dict=lambda: {
                "observed_types": ["image"],
                "summary_for_chat": "截图里能看到一段界面提示文字。",
                "visible_text": "权限不足，请重试",
                "artifact_type": "screenshot",
                "primary_modality": "image",
                "emotion_signals": [],
                "confidence": 0.8,
                "followup_required": False,
            },
        )
    )
    with patch(
        "host_brain.app.cortex_prompt_builder.assemble_prompt_context_plane",
        return_value=plane,
    ), patch(
        "host_brain.app.cortex_prompt_builder.get_compiled_soul",
        return_value=_compiled_soul(),
    ):
        messages, _gen_params, runtime_payload = build_cortex_prompt(
            user_message="这是什么意思",
            render_target_lang="zh",
            person_id="p1",
            session_id="sess",
            request_id="req",
            include_runtime_payload=True,
        )

    assert "【附件观察】" in messages[0]["content"]
    assert "截图里能看到一段界面提示文字。" in messages[0]["content"]
    assert runtime_payload["multimodal"]["artifact_type"] == "screenshot"


def test_build_cortex_prompt_projects_emoji_social_user_message() -> None:
    plane = _plane()
    plane.conversation_interpretation = SimpleNamespace(
        state=SimpleNamespace(
            literal_topic="emoji_social_reply",
            interaction_move="share_emotion",
            freshness="new",
            user_stance="playful",
            repeat_question_count=0,
            as_dict=lambda: {
                "literal_topic": "emoji_social_reply",
                "interaction_move": "share_emotion",
                "freshness": "new",
                "user_stance": "playful",
                "repeat_question_count": 0,
            }
        )
    )
    plane.interaction_thread = SimpleNamespace(
        state=SimpleNamespace(
            thread_kind="image_social",
            source_topic="emoji_social_reply",
            source_modality="emoji",
            emotion_anchor="委屈",
            open=True,
            source_turn_index=0,
            last_assistant_move="",
            last_user_reaction="",
            turn_count=1,
            reason_tags=["image_social"],
            as_dict=lambda: {
                "thread_kind": "image_social",
                "source_topic": "emoji_social_reply",
                "source_modality": "emoji",
                "emotion_anchor": "委屈",
                "open": True,
                "source_turn_index": 0,
                "last_assistant_move": "",
                "last_user_reaction": "",
                "turn_count": 1,
                "reason_tags": ["image_social"],
            }
        )
    )
    plane.multimodal = SimpleNamespace(
        state=SimpleNamespace(
            summary_for_chat="委屈",
            social_signal="委屈",
            as_dict=lambda: {
                "observed_types": ["emoji"],
                "summary_for_chat": "委屈",
                "social_signal": "委屈",
                "visible_text": "",
                "artifact_type": "",
                "primary_modality": "emoji",
                "emotion_signals": ["委屈"],
                "confidence": 1.0,
                "followup_required": False,
            },
        )
    )
    plane.reply_plan = SimpleNamespace(
        state={
            "affect_summary": "",
            "relationship_summary": "",
            "reply_contract": "这不是图片鉴赏，是表情包社交承接。",
            "response_goal": "image_social_ack",
            "tactic_family": "soft_emotion_ack",
            "primary_topic": "emoji_social_reply",
        }
    )
    with patch(
        "host_brain.app.cortex_prompt_builder.assemble_prompt_context_plane",
        return_value=plane,
    ), patch(
        "host_brain.app.cortex_prompt_builder.get_compiled_soul",
        return_value=_compiled_soul(),
    ):
        messages, _gen_params, runtime_payload = build_cortex_prompt(
            user_message="[表情消息]",
            render_target_lang="zh",
            person_id="p1",
            session_id="sess",
            request_id="req",
            include_runtime_payload=True,
        )

    assert "【表情包输入】" in messages[0]["content"]
    assert "这张图/表情传来的意思：委屈" in messages[0]["content"]
    assert "不要描述图片" in messages[0]["content"]
    assert messages[-1] == {"role": "user", "content": "（委屈）"}
    assert runtime_payload["effective_user_message_for_prompt"] == "（委屈）"
