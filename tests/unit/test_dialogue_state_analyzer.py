from __future__ import annotations

from host_brain.app.context.conversation_interpretation import interpret_conversation
from host_brain.app.context.topic_ledger import TopicLedgerBlock, TopicLedgerEntry


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
    interpretation = interpret_conversation(
        recent_turns=[
            {"role": "user", "content": "你是"},
            {"role": "assistant", "content": "我是夏目蓝。"},
        ],
        user_message="你谁",
        topic_ledger=TopicLedgerBlock(),
    )

    assert interpretation.literal_topic == "identity"
    assert interpretation.interaction_move == "reconfirm"
    assert interpretation.freshness == "recent_repeat"
    assert interpretation.last_assistant_act == "self_identify"
    assert interpretation.repeat_question_count == 1
    assert interpretation.repeat_surface_group == "identity_short"
    assert interpretation.recent_identity_answered is True
    assert "identity" in interpretation.answered_topics_recent


def test_conversation_interpretation_repeated_boundary_probe_becomes_probe() -> None:
    interpretation = interpret_conversation(
        recent_turns=[
            {"role": "user", "content": "你为什么要帮我，我们很熟吗"},
            {"role": "assistant", "content": "不熟。只是看见了，也没打算装作没看见。"},
        ],
        user_message="你为什么要帮我，我们很熟吗",
        topic_ledger=TopicLedgerBlock(),
    )

    assert interpretation.literal_topic == "motivation"
    assert interpretation.freshness == "recent_repeat"
    assert interpretation.user_stance == "challenging"
    assert interpretation.interaction_move == "probe"
    assert interpretation.recent_boundary_answered is True


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
    interpretation = interpret_conversation(
        recent_turns=[
            {"role": "user", "content": "你是"},
            {"role": "assistant", "content": "我是夏目蓝。"},
            {"role": "user", "content": "你谁"},
            {"role": "assistant", "content": "夏目蓝。"},
            {"role": "user", "content": "你是"},
            {"role": "assistant", "content": "夏目蓝。"},
        ],
        user_message="你是",
        topic_ledger=TopicLedgerBlock(),
    )

    assert interpretation.literal_topic == "identity"
    assert interpretation.repeat_question_count == 3
    assert interpretation.interaction_move == "long_repeat"


def test_conversation_interpretation_bare_name_counts_as_self_identify() -> None:
    interpretation = interpret_conversation(
        recent_turns=[
            {"role": "user", "content": "你是"},
            {"role": "assistant", "content": "夏目蓝。"},
        ],
        user_message="你谁",
        topic_ledger=TopicLedgerBlock(
            active_topics=("identity",),
            recently_answered_topics=("identity",),
        ),
    )

    assert interpretation.last_assistant_act == "self_identify"
    assert interpretation.recent_identity_answered is True
