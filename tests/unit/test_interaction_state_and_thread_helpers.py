from __future__ import annotations

from host_brain.app.context.interaction_state import coerce_interaction_state, build_interaction_state
from host_brain.app.context.conversation_interpretation import classify_literal_topic, interpret_conversation
from host_brain.app.context.interaction_thread import (
    InteractionThreadState,
    _classify_assistant_topic,
    _classify_user_topic,
    coerce_interaction_thread_state,
    detect_image_social_followup_kind,
    resolve_interaction_thread,
)
from host_brain.app.context.topic_ledger import TopicLedgerBlock


def test_detect_image_social_followup_kind_covers_followup_variants() -> None:
    assert detect_image_social_followup_kind("我不委屈") == "deny"
    assert detect_image_social_followup_kind("怎么了啊") == "why"
    assert detect_image_social_followup_kind("没什么是怎么了") == "nothing"
    assert detect_image_social_followup_kind("哪有闹别扭") == "deny"


def test_classify_user_and_assistant_topic_cover_major_regex_branches() -> None:
    assert _classify_user_topic("你是") == "identity"
    assert _classify_user_topic("你为什么要帮我") == "motivation"
    assert _classify_user_topic("我们很熟吗") == "relationship_boundary"
    assert _classify_user_topic("那你还会帮我吗") == "help_commitment"
    assert _classify_user_topic("hello") == "greeting"
    assert _classify_user_topic("在吗") == "presence_ping"
    assert _classify_user_topic("听得到吗") == "auditory_check"
    assert _classify_user_topic("嗯嗯") == "light_ack"
    assert _classify_user_topic("戳戳") == "playful_ping"
    assert _classify_assistant_topic("哥哥，怎么委屈成这样呀？") == "image_social"
    assert _classify_assistant_topic("我是新海天。") == "identity"
    assert _classify_assistant_topic("虽然不算熟，但是顺路嘛。") == ""
    assert _classify_assistant_topic("别装外人啦，我会管。") == "relationship_boundary"
    assert _classify_assistant_topic("你真开口的话，我不会装作没听见。") == "help_commitment"


def test_connectivity_check_is_presence_ping_not_plain_hold() -> None:
    assert classify_literal_topic("测试一下现在能回吗") == "presence_ping"

    interpretation = interpret_conversation(
        recent_turns=[],
        user_message="测试一下现在能回吗",
        topic_ledger=TopicLedgerBlock(),
    )

    assert interpretation.literal_topic == "presence_ping"
    assert interpretation.ongoing_thread == "phatic"


def test_auditory_text_check_is_not_plain_presence_ping() -> None:
    assert classify_literal_topic("听得到吗") == "auditory_check"
    assert classify_literal_topic("你能听见我说话吗？") == "auditory_check"

    interpretation = interpret_conversation(
        recent_turns=[],
        user_message="听得到吗",
        topic_ledger=TopicLedgerBlock(),
    )

    assert interpretation.literal_topic == "auditory_check"
    assert interpretation.interaction_move == "auditory_probe"
    assert "literal_topic:auditory_check" in interpretation.reason_tags


def test_coerce_interaction_thread_state_and_default_empty() -> None:
    state = coerce_interaction_thread_state(
        {
            "thread_kind": "image_social",
            "source_topic": "emoji_social_reply",
            "source_modality": "image",
            "source_excerpt": "哥哥，怎么委屈成这样呀？",
            "source_context_excerpt": "[图片消息]",
            "emotion_anchor": "distress",
            "open": True,
            "source_turn_index": 2,
            "last_assistant_move": "image_social_ack",
            "last_user_reaction": "deny",
            "turn_count": 3,
            "reason_tags": ["recent_image_social_assistant"],
        }
    )
    empty = coerce_interaction_thread_state(None)

    assert state.as_dict()["thread_kind"] == "image_social"
    assert state.as_dict()["source_excerpt"] == "哥哥，怎么委屈成这样呀？"
    assert state.as_dict()["source_context_excerpt"] == "[图片消息]"
    assert state.reason_tags == ("recent_image_social_assistant",)
    assert empty == InteractionThreadState()


def test_resolve_interaction_thread_prefers_current_multimodal_signal_and_phatic() -> None:
    image_thread = resolve_interaction_thread(
        recent_turns=[],
        current_literal_topic="emoji_social_reply",
        multimodal_observations={
            "primary_modality": "image",
            "core_observation": "一张哭哭的表情图",
            "emotion_signals": ["委屈"],
        },
    )
    phatic_thread = resolve_interaction_thread(
        recent_turns=[],
        current_literal_topic="greeting",
    )

    assert image_thread.thread_kind == "image_social"
    assert image_thread.emotion_anchor == "distress"
    assert image_thread.reason_tags == ("current_multimodal_image_social",)
    assert phatic_thread.thread_kind == "phatic"


def test_resolve_interaction_thread_handles_default_empty_and_nonmatching_image_turns() -> None:
    empty = resolve_interaction_thread(recent_turns=[], current_literal_topic="")
    no_match = resolve_interaction_thread(
        recent_turns=[
            {"role": "user", "content": "[图片消息]"},
            {"role": "assistant", "content": "收到。"},
        ],
        current_literal_topic="",
    )

    assert empty == InteractionThreadState()
    assert no_match == InteractionThreadState()


def test_build_and_coerce_interaction_state_cover_thread_fallback_target_and_reason_tags() -> None:
    built = build_interaction_state(
        interpretation={
            "literal_topic": "emoji_social_followup",
            "interaction_move": "repair_needed",
            "user_stance": "challenging",
            "ongoing_thread": "",
            "reason_tags": ["image_social_followup:deny"],
        },
        interaction_thread={
            "thread_kind": "image_social",
            "source_topic": "emoji_social_reply",
        },
        affect_state={"emotion_tag": "CALM"},
        relationship_state={"bond_stage": "FRIEND"},
        drive_state={"continuity_drive": 0.4},
        topic_ledger={"active_topics": ["emoji_social_reply"]},
        tactic_ledger={"recent_topics": ["emoji_social_reply"]},
        perception_summary="哭哭的小表情图。",
    )
    coerced = coerce_interaction_state(built.as_dict())

    assert built.followup_target == "emoji_social_reply"
    assert built.ongoing_thread == "image_social"
    assert built.reason_tags == ("image_social_followup:deny",)
    assert coerced == built


def test_interpret_conversation_promotes_image_social_followup_from_gateway_like_turns() -> None:
    recent_turns = [
        {"role": "user", "content": "你看一下这张图。"},
        {"role": "assistant", "content": "哥哥，怎么啦，突然丢这个给我看呀？"},
    ]

    preliminary = interpret_conversation(
        recent_turns=recent_turns,
        user_message="没有闹别扭啊",
        topic_ledger=TopicLedgerBlock(),
    )
    thread = resolve_interaction_thread(
        recent_turns=recent_turns,
        current_literal_topic=preliminary.literal_topic,
    )
    final = interpret_conversation(
        recent_turns=recent_turns,
        user_message="没有闹别扭啊",
        topic_ledger=TopicLedgerBlock(),
        interaction_thread=thread,
    )

    assert thread.thread_kind == "image_social"
    assert final.literal_topic == "emoji_social_followup"
    assert final.interaction_move == "repair_needed"
    assert final.ongoing_thread == "image_social"
    assert "image_social_followup:deny" in final.reason_tags


def test_interpret_conversation_routes_generic_image_placeholders_as_visual_share() -> None:
    generic_image_observation = {
        "primary_modality": "image",
        "observed_types": ["image"],
        "summary_for_chat": "收到一张图片。",
        "core_observation": "收到一张图片。",
        "confidence": 0.2,
    }

    interpretation = interpret_conversation(
        recent_turns=[],
        user_message="[????]",
        topic_ledger=TopicLedgerBlock(),
        multimodal_observations=generic_image_observation,
    )

    assert interpretation.literal_topic == "attachment_only_no_question"
    assert interpretation.interaction_move == "light_ack"
    assert interpretation.ongoing_thread == ""
    assert "literal_topic:attachment_only_no_question" in interpretation.reason_tags


def test_interpret_conversation_routes_emotive_image_placeholder_as_social_image() -> None:
    emotive_image_observation = {
        "primary_modality": "image",
        "observed_types": ["image"],
        "summary_for_chat": "无语；一张动漫反应图。",
        "core_observation": "无语；一张动漫反应图。",
        "social_signal": "无语",
        "emotion_signals": ["无语"],
        "confidence": 0.72,
    }

    interpretation = interpret_conversation(
        recent_turns=[],
        user_message="[????]",
        topic_ledger=TopicLedgerBlock(),
        multimodal_observations=emotive_image_observation,
    )

    assert interpretation.literal_topic == "emoji_social_reply"
    assert interpretation.interaction_move == "playful_ping"
    assert interpretation.ongoing_thread == "image_social"
    assert "literal_topic:emoji_social_reply" in interpretation.reason_tags


def test_interpret_conversation_keeps_explicit_image_question_as_description() -> None:
    generic_image_observation = {
        "primary_modality": "image",
        "observed_types": ["image"],
        "summary_for_chat": "收到一张图片。",
        "core_observation": "收到一张图片。",
        "confidence": 0.2,
    }

    interpretation = interpret_conversation(
        recent_turns=[],
        user_message="这是什么",
        topic_ledger=TopicLedgerBlock(),
        multimodal_observations=generic_image_observation,
    )

    assert interpretation.literal_topic == "literal_description"
    assert interpretation.interaction_move == "probe"
    assert interpretation.ongoing_thread == ""


def test_interpret_conversation_treats_look_request_as_image_content() -> None:
    generic_image_observation = {
        "primary_modality": "image",
        "observed_types": ["image"],
        "summary_for_chat": "一只放在桌上的白色马克杯",
        "core_observation": "一只放在桌上的白色马克杯",
        "confidence": 0.76,
    }

    interpretation = interpret_conversation(
        recent_turns=[],
        user_message="你看这张图",
        topic_ledger=TopicLedgerBlock(),
        multimodal_observations=generic_image_observation,
    )

    assert interpretation.literal_topic == "literal_description"
    assert interpretation.interaction_move == "probe"


def test_interpret_conversation_keeps_look_request_social_when_image_is_reaction_meme() -> None:
    interpretation = interpret_conversation(
        recent_turns=[],
        user_message="你看这个",
        topic_ledger=TopicLedgerBlock(),
        multimodal_observations={
            "primary_modality": "image",
            "observed_types": ["image"],
            "summary_for_chat": "像是在无语、尴尬，想让人顺着接一句",
            "core_observation": "像是在无语、尴尬，想让人顺着接一句",
            "social_signal": "无语",
            "emotion_signals": ["无语"],
        },
    )

    assert interpretation.literal_topic == "emoji_social_reply"
    assert interpretation.interaction_move == "playful_ping"


def test_interpret_conversation_promotes_assistant_reply_followup() -> None:
    interpretation = interpret_conversation(
        recent_turns=[
            {"role": "user", "content": "你知道吗"},
            {"role": "assistant", "content": "知道。"},
        ],
        user_message="知道什么",
        topic_ledger=TopicLedgerBlock(),
    )

    assert interpretation.literal_topic == "assistant_reply_followup"
    assert interpretation.interaction_move == "clarify_reference"
    assert interpretation.ongoing_thread == "assistant_reply_followup"
    assert interpretation.followup_target == "assistant_reply"
    assert "assistant_reply_followup:ask_object" in interpretation.reason_tags


def test_interpret_conversation_promotes_punct_after_assistant_filler_reply() -> None:
    interpretation = interpret_conversation(
        recent_turns=[
            {"role": "user", "content": "知道什么"},
            {"role": "assistant", "content": "唔。"},
        ],
        user_message="？",
        topic_ledger=TopicLedgerBlock(),
    )

    assert interpretation.literal_topic == "assistant_reply_followup"
    assert interpretation.interaction_move == "clarify_previous_reply"
    assert interpretation.ongoing_thread == "assistant_reply_followup"
    assert interpretation.followup_target == "assistant_reply"
    assert "assistant_reply_followup:clarify_meaning" in interpretation.reason_tags


def test_interpret_conversation_routes_lonely_punctuation_as_phatic_without_history() -> None:
    interpretation = interpret_conversation(
        recent_turns=[],
        user_message="？",
        topic_ledger=TopicLedgerBlock(),
    )

    assert interpretation.literal_topic == "phatic_punct"
    assert interpretation.interaction_move == "light_ack"
