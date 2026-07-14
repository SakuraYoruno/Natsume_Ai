from __future__ import annotations

from host_brain.persona.context.conversation_interpretation import interpret_conversation
from host_brain.persona.context.soul_runtime import get_compiled_soul
from host_brain.persona.context.topic_ledger import TopicLedgerBlock, TopicLedgerEntry


def test_conversation_interpretation_first_identity_probe_is_new() -> None:
    interpretation = interpret_conversation(
        recent_turns=[],
        user_message="你是",
        topic_ledger=TopicLedgerBlock(),
    )

    assert interpretation.literal_topic == "identity"
    assert interpretation.interaction_move == "first_ask"
    assert interpretation.freshness == "new"
    assert interpretation.user_stance == "probing"
    assert interpretation.repeat_question_count == 0


def test_conversation_interpretation_repeated_identity_probe_is_recent_reconfirm() -> None:
    name = get_compiled_soul().display_name
    interpretation = interpret_conversation(
        recent_turns=[
            {"role": "user", "content": "你是"},
            {"role": "assistant", "content": f"我是{name}。"},
        ],
        user_message="你谁",
        topic_ledger=TopicLedgerBlock(),
    )

    assert interpretation.literal_topic == "identity"
    assert interpretation.interaction_move == "reconfirm"
    assert interpretation.freshness == "recent_repeat"
    assert interpretation.last_assistant_act == "self_identify"
    assert interpretation.ongoing_thread == "identity_loop"
    assert interpretation.followup_target == "identity"
    assert interpretation.repeat_question_count == 1
    assert interpretation.repeat_surface_group == "identity_short"
    assert interpretation.recent_identity_answered is True
    assert "identity" in interpretation.answered_topics_recent


def test_conversation_interpretation_repeated_boundary_probe_becomes_probe() -> None:
    interpretation = interpret_conversation(
        recent_turns=[
            {"role": "user", "content": "你为什么要帮我，我们很熟吗"},
            {"role": "assistant", "content": "别装外人啦，我会管。"},
        ],
        user_message="你为什么要帮我，我们很熟吗",
        topic_ledger=TopicLedgerBlock(),
    )

    assert interpretation.literal_topic == "motivation"
    assert interpretation.freshness == "recent_repeat"
    assert interpretation.user_stance == "challenging"
    assert interpretation.interaction_move == "probe"
    assert interpretation.recent_boundary_answered is True


def test_conversation_interpretation_does_not_count_distance_boundary_as_answered() -> None:
    interpretation = interpret_conversation(
        recent_turns=[
            {"role": "user", "content": "你为什么要帮我，我们很熟吗"},
            {"role": "assistant", "content": "不熟。只是看见了，也没打算装作没看见。"},
        ],
        user_message="你为什么要帮我，我们很熟吗",
        topic_ledger=TopicLedgerBlock(),
    )

    assert interpretation.last_assistant_act == "boundary_distance_answer"
    assert interpretation.recent_boundary_answered is False


def test_conversation_interpretation_repeated_help_probe_is_anxiety_check() -> None:
    interpretation = interpret_conversation(
        recent_turns=[
            {"role": "user", "content": "那你还会帮我吗"},
            {"role": "assistant", "content": "看情况。你真开口的话，我不会装作没听见。"},
        ],
        user_message="那你还会帮我吗",
        topic_ledger=TopicLedgerBlock(),
    )

    assert interpretation.literal_topic == "help_commitment"
    assert interpretation.interaction_move == "anxiety_check"
    assert interpretation.freshness == "recent_repeat"


def test_conversation_interpretation_uses_topic_ledger_for_long_gap_reopen() -> None:
    interpretation = interpret_conversation(
        recent_turns=[
            {"role": "user", "content": "别的话题"},
            {"role": "assistant", "content": "嗯，我在听。"},
        ],
        user_message="你谁",
        topic_ledger=TopicLedgerBlock(
            entries=(
                TopicLedgerEntry(
                    topic_key="identity",
                    last_answer_turn_index=2,
                    last_answer_request_id="req-old",
                    answer_style="identify",
                    confidence=0.9,
                    stale_after_turn_index=50,
                ),
            ),
            active_topics=("identity",),
            recently_answered_topics=("identity",),
        ),
    )

    assert interpretation.literal_topic == "identity"
    assert interpretation.freshness == "long_gap_reopen"
    assert interpretation.interaction_move == "reconfirm"


def test_conversation_interpretation_marks_long_repeat_after_three_identity_reasks() -> None:
    name = get_compiled_soul().display_name
    interpretation = interpret_conversation(
        recent_turns=[
            {"role": "user", "content": "你是"},
            {"role": "assistant", "content": f"我是{name}。"},
            {"role": "user", "content": "你谁"},
            {"role": "assistant", "content": f"{name}。"},
            {"role": "user", "content": "你是"},
            {"role": "assistant", "content": f"{name}。"},
        ],
        user_message="你是",
        topic_ledger=TopicLedgerBlock(),
    )

    assert interpretation.literal_topic == "identity"
    assert interpretation.repeat_question_count == 3
    assert interpretation.interaction_move == "long_repeat"


def test_conversation_interpretation_bare_name_counts_as_self_identify() -> None:
    name = get_compiled_soul().display_name
    interpretation = interpret_conversation(
        recent_turns=[
            {"role": "user", "content": "你是"},
            {"role": "assistant", "content": f"{name}。"},
        ],
        user_message="你谁",
        topic_ledger=TopicLedgerBlock(
            active_topics=("identity",),
            recently_answered_topics=("identity",),
        ),
    )

    assert interpretation.last_assistant_act == "self_identify"
    assert interpretation.recent_identity_answered is True


def test_conversation_interpretation_image_only_without_question_is_social_reply() -> None:
    interpretation = interpret_conversation(
        recent_turns=[],
        user_message="[图片消息]",
        topic_ledger=TopicLedgerBlock(),
        multimodal_observations={
            "primary_modality": "image",
            "observed_types": ["image"],
            "core_observation": "一张动漫人物表情图。",
        },
    )

    assert interpretation.literal_topic == "emoji_social_reply"
    assert interpretation.interaction_move == "playful_ping"
    assert interpretation.user_stance == "playful"


def test_conversation_interpretation_plain_photo_without_question_is_visual_share() -> None:
    interpretation = interpret_conversation(
        recent_turns=[],
        user_message="[图片消息]",
        topic_ledger=TopicLedgerBlock(),
        multimodal_observations={
            "primary_modality": "image",
            "observed_types": ["image"],
            "core_observation": "一只白色马克杯放在桌上。",
        },
    )

    assert interpretation.literal_topic == "attachment_only_no_question"
    assert interpretation.interaction_move == "attachment_ack"
    assert interpretation.user_stance == "probing"


def test_conversation_interpretation_look_at_photo_without_question_uses_content_path() -> None:
    interpretation = interpret_conversation(
        recent_turns=[],
        user_message="你看这张照片",
        topic_ledger=TopicLedgerBlock(),
        multimodal_observations={
            "primary_modality": "image",
            "observed_types": ["image"],
            "core_observation": "一只白色马克杯放在桌上。",
        },
    )

    assert interpretation.literal_topic == "literal_description"
    assert interpretation.interaction_move == "probe"


def test_conversation_interpretation_screenshot_only_stays_attachment_ack() -> None:
    interpretation = interpret_conversation(
        recent_turns=[],
        user_message="[图片消息]",
        topic_ledger=TopicLedgerBlock(),
        multimodal_observations={
            "primary_modality": "image",
            "artifact_type": "screenshot",
            "observed_types": ["screenshot"],
            "visible_text": "设置失败",
        },
    )

    assert interpretation.literal_topic == "attachment_only_no_question"


def test_conversation_interpretation_image_social_followup_deny_is_repair_needed() -> None:
    interpretation = interpret_conversation(
        recent_turns=[
            {"role": "user", "content": "[图片消息]"},
            {"role": "assistant", "content": "哥哥，怎么委屈成这样呀？"},
        ],
        user_message="我不委屈",
        topic_ledger=TopicLedgerBlock(),
    )

    assert interpretation.literal_topic == "emoji_social_followup"
    assert interpretation.interaction_move == "repair_needed"
    assert interpretation.user_stance == "challenging"
    assert interpretation.ongoing_thread == "image_social"
    assert interpretation.followup_target == "emoji_social_reply"
    assert "image_social_followup:deny" in interpretation.reason_tags


def test_conversation_interpretation_image_social_followup_generic_negation_is_repair_needed() -> None:
    interpretation = interpret_conversation(
        recent_turns=[
            {"role": "user", "content": "[图片消息]"},
            {"role": "assistant", "content": "哥哥，怎么，又拿这个小表情跟我闹别扭呀？"},
        ],
        user_message="没有闹别扭啊",
        topic_ledger=TopicLedgerBlock(),
    )

    assert interpretation.literal_topic == "emoji_social_followup"
    assert interpretation.interaction_move == "repair_needed"
    assert interpretation.user_stance == "challenging"
    assert interpretation.ongoing_thread == "image_social"
    assert "image_social_followup:deny" in interpretation.reason_tags


def test_conversation_interpretation_image_social_followup_why_is_reconfirm() -> None:
    interpretation = interpret_conversation(
        recent_turns=[
            {"role": "user", "content": "[图片消息]"},
            {"role": "assistant", "content": "哥哥，怎么委屈成这样呀？"},
        ],
        user_message="怎么了啊",
        topic_ledger=TopicLedgerBlock(),
    )

    assert interpretation.literal_topic == "emoji_social_followup"
    assert interpretation.interaction_move == "reconfirm"
    assert interpretation.user_stance == "probing"
    assert interpretation.ongoing_thread == "image_social"
    assert "image_social_followup:why" in interpretation.reason_tags


def test_conversation_interpretation_image_social_followup_nothing_is_light_tease() -> None:
    interpretation = interpret_conversation(
        recent_turns=[
            {"role": "user", "content": "[图片消息]"},
            {"role": "assistant", "content": "哥哥，突然这么可爱，是想让我哄你吗？"},
        ],
        user_message="没什么是怎么了",
        topic_ledger=TopicLedgerBlock(),
    )

    assert interpretation.literal_topic == "emoji_social_followup"
    assert interpretation.interaction_move == "light_tease"
    assert interpretation.user_stance == "playful"
    assert interpretation.ongoing_thread == "image_social"
    assert "image_social_followup:nothing" in interpretation.reason_tags
