from __future__ import annotations

from host_brain.persona.growth_feedback_collector import (
    FeedbackAdjustmentResult,
    GrowthFeedback,
    GrowthFeedbackCollector,
    EngagementMetrics,
    SOURCE_EXPLICIT_USER,
    SOURCE_IMPLICIT_BEHAVIOR,
    _detect_followup,
)
from host_brain.persona.personality_traits import TraitsDelta


def test_followup_detection_and_helper_as_dicts() -> None:
    assert _detect_followup("") is False
    assert _detect_followup("然后呢，你继续说") is True

    metrics = EngagementMetrics(
        reply_speed_ms=1234,
        user_msg_len=88,
        has_followup=True,
        is_user_initiated=True,
        user_emotion_tag="HAPPY",
        natsume_msg_len=66,
    )
    assert metrics.as_dict()["natsume_msg_len"] == 66

    feedback = GrowthFeedback(
        trait_name="warmth",
        direction=1.0,
        confidence=0.4567,
        source=SOURCE_EXPLICIT_USER,
        ts_ms=123,
        text_hint="x" * 80,
    )
    assert feedback.as_dict()["confidence"] == 0.457
    assert len(feedback.as_dict()["text_hint"]) == 50

    result = FeedbackAdjustmentResult(
        delta=TraitsDelta(warmth=0.01234),
        total_signals=2,
        trait_signal_counts={"warmth": 2},
        trait_avg_signals={"warmth": 0.123456},
        ts_ms=456,
    )
    assert result.as_dict()["trait_avg_signals"]["warmth"] == 0.1235


def test_from_canon_params_uses_feedback_overrides() -> None:
    collector = GrowthFeedbackCollector.from_canon_params(
        {
            "feedback_explicit_weight": 1.2,
            "feedback_implicit_weight": 0.4,
            "feedback_min_signals": 5,
            "feedback_signal_threshold": 0.33,
            "base_delta": 0.02,
        }
    )

    assert collector._explicit_weight == 1.2
    assert collector._implicit_weight == 0.4
    assert collector._min_signals == 5
    assert collector._signal_threshold == 0.33
    assert collector._feedback_delta == 0.02


def test_extract_engagement_signals_covers_positive_paths() -> None:
    signals = GrowthFeedbackCollector.extract_engagement_signals(
        "这次我真的想认真和你聊很久",
        metrics=EngagementMetrics(
            reply_speed_ms=5_000,
            user_msg_len=250,
            has_followup=True,
            is_user_initiated=True,
            user_emotion_tag="HAPPY",
        ),
        ts_ms=100,
    )

    tags = [(signal.trait_name, signal.direction, signal.text_hint) for signal in signals]

    assert ("warmth", 1.0, "fast_reply:5000ms") in tags
    assert ("sensitivity", 1.0, "very_long_msg:250chars") in tags
    assert ("curiosity", 1.0, "followup_question") in tags
    assert ("humor", 1.0, "followup_positive_emotion") in tags
    assert ("playfulness", 1.0, "user_initiated_chat") in tags
    assert ("warmth", 1.0, "positive_emotion:HAPPY") in tags


def test_extract_engagement_signals_covers_slow_short_and_bored_paths() -> None:
    signals = GrowthFeedbackCollector.extract_engagement_signals(
        "ok",
        metrics=EngagementMetrics(
            reply_speed_ms=180_000,
            user_msg_len=2,
            user_emotion_tag="BORED",
        ),
        ts_ms=200,
    )

    tags = [(signal.trait_name, signal.direction, signal.text_hint) for signal in signals]

    assert ("warmth", -1.0, "slow_reply:180000ms") in tags
    assert ("playfulness", -1.0, "short_slow_msg:2chars_180000ms") in tags
    assert ("playfulness", 1.0, "negative_engagement:BORED") in tags


def test_extract_engagement_signals_covers_long_message_and_followup_detection() -> None:
    signals = GrowthFeedbackCollector.extract_engagement_signals(
        "为什么会这样，我还想再多听一点",
        metrics=EngagementMetrics(
            user_msg_len=100,
            user_emotion_tag="SURPRISED",
        ),
        ts_ms=300,
    )

    tags = [(signal.trait_name, signal.direction, signal.text_hint) for signal in signals]

    assert ("sensitivity", 1.0, "long_msg:100chars") in tags
    assert ("curiosity", 1.0, "followup_question") in tags
    assert ("humor", 1.0, "followup_positive_emotion") in tags


def test_extract_from_text_handles_empty_and_keyword_matches() -> None:
    assert GrowthFeedbackCollector.extract_from_text("", ts_ms=1) == []

    signals = GrowthFeedbackCollector.extract_from_text(
        "你好温柔，但刚才又太冷淡了",
        ts_ms=2,
    )

    matches = [(signal.trait_name, signal.direction) for signal in signals]
    assert ("warmth", 1.0) in matches
    assert ("warmth", -1.0) in matches


def test_add_feedback_trims_buffer_and_recent_feedbacks_are_serialized() -> None:
    collector = GrowthFeedbackCollector(max_buffer_size=2)
    for idx in range(3):
        collector.add_feedback(
            "u1",
            GrowthFeedback(
                trait_name="warmth",
                direction=1.0,
                confidence=0.5,
                source=SOURCE_EXPLICIT_USER,
                ts_ms=idx,
                text_hint=f"note-{idx}",
            ),
        )

    assert collector.get_buffer_size("u1") == 2
    recent = collector.get_recent_feedbacks("u1", limit=1)
    assert recent == [
        {
            "trait_name": "warmth",
            "direction": 1.0,
            "confidence": 0.5,
            "source": SOURCE_EXPLICIT_USER,
            "ts_ms": 2,
            "text_hint": "note-2",
        }
    ]


def test_add_webui_vote_and_implicit_signal_ignore_invalid_traits() -> None:
    collector = GrowthFeedbackCollector()

    collector.add_webui_vote("u1", trait_name="unknown", is_positive=True, ts_ms=1)
    collector.add_implicit_signal("u1", trait_name="unknown", direction=2.0, confidence=2.0, ts_ms=2)
    assert collector.get_buffer_size("u1") == 0

    collector.add_implicit_signal(
        "u1",
        trait_name="warmth",
        direction=2.0,
        confidence=2.0,
        text_hint="x" * 80,
        ts_ms=3,
    )
    recent = collector.get_recent_feedbacks("u1")

    assert recent[0]["direction"] == 1.0
    assert recent[0]["confidence"] == 1.0
    assert len(recent[0]["text_hint"]) == 50


def test_compute_adjustment_handles_empty_zero_weight_threshold_and_invalid_trait() -> None:
    empty = GrowthFeedbackCollector()
    empty_result = empty.compute_adjustment("u-empty", ts_ms=10)
    assert empty_result.total_signals == 0
    assert empty_result.delta.as_dict()["warmth"] == 0.0

    zero_weight = GrowthFeedbackCollector(min_signals=1)
    zero_weight.add_feedback(
        "u-zero",
        GrowthFeedback(
            trait_name="warmth",
            direction=1.0,
            confidence=0.0,
            source=SOURCE_EXPLICIT_USER,
            ts_ms=11,
        ),
    )
    zero_result = zero_weight.compute_adjustment("u-zero", ts_ms=12)
    assert zero_result.trait_avg_signals["warmth"] == 0.0
    assert zero_result.delta.as_dict()["warmth"] == 0.0

    threshold = GrowthFeedbackCollector(min_signals=2, signal_threshold=0.3)
    threshold.add_feedback(
        "u-threshold",
        GrowthFeedback(
            trait_name="not_a_trait",
            direction=1.0,
            confidence=1.0,
            source=SOURCE_EXPLICIT_USER,
            ts_ms=13,
        ),
    )
    threshold.add_feedback(
        "u-threshold",
        GrowthFeedback(
            trait_name="warmth",
            direction=1.0,
            confidence=1.0,
            source=SOURCE_IMPLICIT_BEHAVIOR,
            ts_ms=14,
        ),
    )
    threshold.add_feedback(
        "u-threshold",
        GrowthFeedback(
            trait_name="warmth",
            direction=-1.0,
            confidence=1.0,
            source=SOURCE_IMPLICIT_BEHAVIOR,
            ts_ms=15,
        ),
    )
    threshold_result = threshold.compute_adjustment("u-threshold", ts_ms=16)

    assert threshold_result.total_signals == 3
    assert threshold_result.trait_signal_counts["warmth"] == 2
    assert threshold_result.trait_avg_signals["warmth"] == 0.0
    assert threshold_result.delta.as_dict()["warmth"] == 0.0
