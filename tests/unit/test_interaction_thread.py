from __future__ import annotations

from host_brain.persona.context.interaction_thread import (
    detect_assistant_reply_followup_kind,
    resolve_interaction_thread,
)


def test_resolve_interaction_thread_detects_image_social_thread() -> None:
    thread = resolve_interaction_thread(
        recent_turns=[
            {"role": "user", "content": "[图片消息]"},
            {"role": "assistant", "content": "哥哥，怎么委屈成这样呀？"},
        ],
    )

    assert thread.open is True
    assert thread.thread_kind == "image_social"
    assert thread.source_topic == "emoji_social_reply"
    assert thread.source_modality == "image"
    assert thread.emotion_anchor == "distress"


def test_resolve_interaction_thread_detects_image_social_thread_from_gateway_placeholder_turn() -> None:
    thread = resolve_interaction_thread(
        recent_turns=[
            {"role": "user", "content": "你看一下这张图。"},
            {"role": "assistant", "content": "哥哥，怎么啦，突然丢这个给我看呀？"},
        ],
    )

    assert thread.open is True
    assert thread.thread_kind == "image_social"
    assert thread.source_topic == "emoji_social_reply"
    assert thread.source_modality == "image"
    assert "recent_image_social_assistant" in thread.reason_tags


def test_resolve_interaction_thread_tracks_image_social_deny_reaction_from_generic_negation() -> None:
    thread = resolve_interaction_thread(
        recent_turns=[
            {"role": "user", "content": "[图片消息]"},
            {"role": "assistant", "content": "哥哥，怎么，又拿这个小表情跟我闹别扭呀？"},
            {"role": "user", "content": "没有闹别扭啊"},
        ],
    )

    assert thread.open is True
    assert thread.thread_kind == "image_social"
    assert thread.last_user_reaction == "deny"


def test_resolve_interaction_thread_tracks_image_social_followup_without_bracket_placeholder() -> None:
    thread = resolve_interaction_thread(
        recent_turns=[
            {"role": "user", "content": "你看一下这张图。"},
            {"role": "assistant", "content": "哥哥，怎么啦，突然丢这个给我看呀？"},
            {"role": "user", "content": "没有闹别扭啊"},
        ],
    )

    assert thread.open is True
    assert thread.thread_kind == "image_social"
    assert thread.last_user_reaction == "deny"


def test_resolve_interaction_thread_detects_identity_loop() -> None:
    thread = resolve_interaction_thread(
        recent_turns=[
            {"role": "user", "content": "你是"},
            {"role": "assistant", "content": "我是新海天。"},
            {"role": "user", "content": "你谁"},
        ],
        current_literal_topic="identity",
    )

    assert thread.open is True
    assert thread.thread_kind == "identity_loop"
    assert thread.source_topic == "identity"


def test_resolve_interaction_thread_detects_help_reassurance() -> None:
    thread = resolve_interaction_thread(
        recent_turns=[
            {"role": "user", "content": "那你还会帮我吗"},
            {"role": "assistant", "content": "看情况。你真开口的话，我不会装作没听见。"},
            {"role": "user", "content": "那你还会帮我吗"},
        ],
        current_literal_topic="help_commitment",
    )

    assert thread.open is True
    assert thread.thread_kind == "help_reassurance"
    assert thread.source_topic == "help_commitment"


def test_resolve_interaction_thread_detects_assistant_reply_followup() -> None:
    thread = resolve_interaction_thread(
        recent_turns=[
            {"role": "user", "content": "你知道吗"},
            {"role": "assistant", "content": "知道。"},
        ],
        current_user_message="知道什么",
    )

    assert thread.open is True
    assert thread.thread_kind == "assistant_reply_followup"
    assert thread.source_topic == "assistant_reply"
    assert thread.source_excerpt == "知道。"
    assert thread.source_context_excerpt == "你知道吗"
    assert thread.last_user_reaction == "ask_object"


def test_resolve_interaction_thread_detects_punct_followup_after_filler_reply() -> None:
    thread = resolve_interaction_thread(
        recent_turns=[
            {"role": "user", "content": "知道什么"},
            {"role": "assistant", "content": "唔。"},
        ],
        current_user_message="？",
    )

    assert thread.open is True
    assert thread.thread_kind == "assistant_reply_followup"
    assert thread.source_topic == "assistant_reply"
    assert thread.source_excerpt == "唔。"
    assert thread.source_context_excerpt == "知道什么"
    assert thread.last_user_reaction == "clarify_meaning"


def test_detect_assistant_reply_followup_kind_catches_short_object_question() -> None:
    assert detect_assistant_reply_followup_kind("知道什么", "知道。") == "ask_object"
    assert detect_assistant_reply_followup_kind("什么意思") == "clarify_meaning"
    assert detect_assistant_reply_followup_kind("？", "唔。") == "clarify_meaning"
